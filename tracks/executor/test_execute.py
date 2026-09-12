"""M-TEST run/red-check/trace execution extracted from the Executor
(mixin ``ExecTestRunMixin``)."""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tracks import paths
from tracks.executor.helpers import (
    _DIAGNOSE_TARGET,
    _LEGIT_RED,
    _scoped_commit_if_staged,
    _short_detail,
    classify_red_detail,
    git,
)
from tracks.executor.test_collect import _R2_BASIS, _R2_SCOPE, _contract_sections
from tracks.executor.test_select import (
    EmptyR2SelectionError,
    TestResultError,
    TestSelectError,
    make_selection_id,
    parse_test_result,
    require_exact_node_coverage,
    require_nonempty_r2_selection,
    resolve_selected_command,
)
from tracks.executor.test_select import audit as audit_selection_argv
from tracks.executor.validate import required_ac_ids
from tracks.project import ContractError, load_contract


@dataclass(frozen=True)
class _RedCheckSelection:
    """SELECT_R2 inputs of one RED_CHECK command."""

    contract: object
    sections: list
    baseline_id: object
    selected_by_layer: dict
    selected_all: list


class ExecTestRunMixin:
    """run_tests / collect / red-check / check-trace / commit-tests handlers."""

    def _do_run_tests(self, cmd, state, task_id, reconcile):
        """SM-01.9 / D-41 FR-0250-02 RED_CHECK (v3 timing + v6 result channel).

        SELECT_R2 against THIS run's persisted pre-WRITE snapshot -> emit
        ``test.selected(scope=r2_delta)`` BEFORE executing -> execute ONLY the
        selected nodes through each layer's contract ``run_selected`` template
        ({nodes}/{result} substituted; concurrency/test result flags are
        contract-owned, Runtime injects nothing) -> parse the strict test result
        result and require exact node coverage -> classify legal Red per node.
        Never executes a full ``run`` nor any historical/R1 node. Feature
        empty-R2 fails closed (check=empty_r2); the hotfix explicit unit-only
        increment bypass is preserved (FR-0244). All-legit -> red.validated(valid)
        -> PRISM_REVIEW; illegit/unexpected pass -> red.validated(invalid) ->
        DIAGNOSE. stdout/stderr are logs only -- the {result} test result record is
        the sole per-node authority."""
        if reconcile and state.red_validated:
            return
        selection = self._red_check_selection(cmd, state)
        if selection is None:
            return
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        # Review-5: dirty worktree content is stamped separately from commit
        # so two selections over the same node set + HEAD with different
        # uncommitted R2 content never share a selection identity.
        tree_stamp = self._dirty_tree_stamp()
        selection_id = make_selection_id(
            nodes=selection.selected_all,
            scope=_R2_SCOPE,
            basis=_R2_BASIS,
            baseline=str(selection.baseline_id or ""),
            commit=commit,
            tree_stamp=tree_stamp,
        )
        # FA-5 (final review pin): a crash/reconcile replay of a command whose
        # test.selected is already persisted must compare the CURRENT recomputed
        # identity against the persisted record. A mismatch means the worktree/
        # baseline moved under the old selection identity -- fail closed WITHOUT
        # executing (evidence-integrity hole); an exact match resumes normally.
        persisted_selection = next(
            (
                ev.payload
                for ev in self.store.events(self.run_id)
                if ev.type == "test.selected" and ev.command_id == cmd.command_id
            ),
            None,
        )
        stale_keys = self._stale_selection_fields(
            persisted_selection,
            selection_id,
            tree_stamp,
            selection.baseline_id,
            selection.selected_all,
        )
        if stale_keys:
            self._emit_stale_selection_failure(
                cmd,
                state,
                persisted_selection,
                stale_keys,
                selection_id,
                tree_stamp,
                selection.baseline_id,
                selection.selected_all,
            )
            return
        nodes_blob = self._persist_selection_nodes(
            cmd, state, selection.selected_by_layer, selection.selected_all
        )
        if nodes_blob is None:
            return
        # WAL/reconcile reuse the SAME stamped selection: re-issue only when
        # this command has not already emitted it (deterministic identity).
        if persisted_selection is None:
            self._emit_test_selected(
                cmd,
                selection.baseline_id,
                selection.selected_all,
                nodes_blob,
                commit,
                tree_stamp,
                selection_id,
            )

        if not selection.selected_all:
            self._emit_unit_only_increment_valid(cmd, selection_id, nodes_blob)
            return

        self._execute_red_check(
            cmd,
            state,
            selection.sections,
            selection.selected_by_layer,
            selection_id,
            nodes_blob,
        )

    def _red_check_selection(self, cmd, state):
        """SELECT_R2 inputs, or None after emitting the failure verdict."""
        try:
            contract = load_contract(self.repo)
            sections = _contract_sections(contract)
            baseline_id, selected_by_layer, selected_all = self._selection_context()
            require_nonempty_r2_selection(
                _R2_SCOPE,
                selected_all,
                allow_explicit_unit_increment=self._has_persisted_unit_only_increment(),
            )
        except EmptyR2SelectionError:
            self._emit_empty_r2_failure(cmd, state)
            return None
        except (ContractError, TestSelectError) as exc:
            reason = (
                f"contract error: {exc.reason}"
                if isinstance(exc, ContractError)
                else str(exc)
            )
            self._emit_contract_error_red(cmd, state, reason)
            return None
        return _RedCheckSelection(
            contract, sections, baseline_id, selected_by_layer, selected_all
        )

    def _emit_empty_r2_failure(self, cmd, state) -> None:
        """Feature M-TEST empty R2 -> fail closed (check=empty_r2): a vacuous
        pass is refused; hotfix must declare its unit-only increment.

        Before blaming the author, screen for collect-pipeline blindness
        (operator finding 2026-08-24, run 01M0S0FQ): committed test artifacts
        that the collector never saw indicate a runtime/contract defect
        (check=collect_defect) -- no attempt charge, operator escalation,
        preserved checkpointed work. A third screen covers the design
        re-approval re-validation cycle: when this cycle's no-diff review was
        ACCEPTED and the run already committed its test increment, the empty
        selection is discharged (check=r2_discharged) to EXIT -- the
        increment exists and was validated earlier in this run; the cycle
        structurally cannot produce a new R2 delta (operator finding
        2026-08-24, third M-TEST cycle)."""
        defect = self._collect_defect_evidence()
        if defect is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "collect_defect",
                    "reason": defect,
                    "evidence": [],
                    "target_stage": "M-TEST",
                },
                command_id=cmd.command_id,
            )
            return
        discharge = self._r2_discharge_evidence()
        if discharge is not None:
            self._emit(
                "verdict.passed",
                {
                    "check": "r2_discharged",
                    "detail": discharge,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "verdict.failed",
            {
                "check": "empty_r2",
                "reason": (
                    "feature M-TEST produced an empty R2 selection against "
                    "the pre-WRITE snapshot: a vacuous pass is refused "
                    "(hotfix must declare its unit-only increment explicitly)"
                ),
                "evidence": [],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _r2_discharge_evidence(self) -> str | None:
        """Design re-approval discharge for an empty-R2 gate hit.

        Conditions (all required, machine evidence only):
        - this cycle's no-diff explanation was REVIEWED and ACCEPTED
          (no_diff.reviewed verdict=pass after the retry cutoff);
        - the run already committed its test increment (test.committed);
        - the current full collect sees the committed assets
          (collected_count > 0).

        Under those, the vacuous-pass guard's purpose (refuse validation of a
        nonexistent increment) is already satisfied by the committed and
        earlier-validated increment; the re-validation cycle structurally
        cannot add a new R2 delta. Returns the discharge detail, else None."""
        cutoff = self._retry_cutoff_seq()
        no_diff_accepted = any(
            ev.type == "no_diff.reviewed"
            and ev.seq > cutoff
            and ev.payload.get("verdict") == "pass"
            for ev in self.store.events(self.run_id)
        )
        if not no_diff_accepted:
            return None
        committed = self._latest_event("test.committed")
        if committed is None:
            return None
        collected = self._latest_event("test.collected", status="passed")
        if collected is None or collected.payload.get("collected_count", 0) <= 0:
            return None
        return (
            "design re-approval re-validation: no-diff review accepted this "
            f"cycle; increment already committed ({committed.payload.get('commit_sha', '')[:12]}) "
            f"and collected ({collected.payload.get('collected_count')} nodes)"
        )

    def _collect_defect_evidence(self) -> str | None:
        """Infra-side collect blindness signatures for an empty-R2 gate hit.

        Signature A (total blindness): the latest persisted baseline AND the
        latest full collect both saw ZERO nodes while the contract's declared
        layer paths contain python test modules -- the collector parsed
        nothing (incident: framework 9.1 quiet collect emits per-file counts
        without ``::`` node ids; the runtime parser keyed on ``::`` lines).

        Signature B (new-file blindness): test modules added/changed by this
        run's checkpointed Shield commit (test.written -> result.checkpointed
        base..commit diff) have NO collected node carrying that file prefix
        -- the collector never saw the new file at all.

        Returns a human-readable reason when either signature holds (the
        gate is a collect/contract defect, not the author's), else None.
        """
        layer_paths = self._declared_layer_paths()
        collected = self._latest_event("test.collected")
        baseline = self._latest_event("test.baseline_captured", status="passed")
        reason = self._total_blindness_reason(collected, baseline, layer_paths)
        if reason is not None:
            return reason
        return self._new_file_blindness_reason(collected, layer_paths)

    def _declared_layer_paths(self) -> list[str]:
        """Repo-relative layer path prefixes declared by the host contract."""
        try:
            sections = _contract_sections(load_contract(self.repo))
        except ContractError:
            sections = []
        layer_paths: list[str] = []
        for _name, sec in sections:
            if sec is None:
                continue
            declared = getattr(sec, "paths", None)
            if declared is None and hasattr(sec, "get"):
                declared = sec.get("paths")
            layer_paths.extend(
                rel for rel in declared or [] if isinstance(rel, str) and rel
            )
        return layer_paths

    def _total_blindness_reason(self, collected, baseline, layer_paths) -> str | None:
        """Signature A: zero-node baseline + zero-node collect while the
        declared layer dirs demonstrably contain test modules."""
        if collected is None or baseline is None:
            return None
        if collected.payload.get("collected_count") != 0:
            return None
        if baseline.payload.get("nodes_count") != 0:
            return None
        has_modules = any(
            (Path(self.repo) / rel).is_dir()
            and any((Path(self.repo) / rel).rglob("test_*.py"))
            for rel in layer_paths
        )
        if not has_modules:
            return None
        return (
            "collect pipeline blindness: baseline and full collect both "
            "parsed 0 nodes while declared layer paths contain test "
            "modules (suspect collector output format / parser mismatch, "
            "e.g. framework 9.1 quiet collect per-file counts without "
            "node ids); Shield's committed artifacts were never seen"
        )

    def _new_file_blindness_reason(self, collected, layer_paths) -> str | None:
        """Signature B: checkpointed new/changed test modules absent from the
        collected node inventory entirely (file prefix never collected)."""
        written = self._latest_event("test.written")
        if collected is None or written is None:
            return None
        result_id = written.payload.get("result_id")
        commit = written.payload.get("commit_sha")
        base = self._checkpoint_base_sha(result_id)
        if not (base and commit and base != commit):
            return None
        test_modules = self._changed_test_modules(base, commit, layer_paths)
        if not test_modules:
            return None
        prefixes = self._collected_prefixes(collected)
        if prefixes is None:
            return None
        missing = [m for m in test_modules if m not in prefixes]
        if not missing:
            return None
        return (
            "collect pipeline blindness: checkpointed test modules have "
            f"zero collected nodes: {', '.join(missing[:5])} "
            "(collector never saw files the author committed)"
        )

    def _checkpoint_base_sha(self, result_id) -> str | None:
        """base_sha of the result.checkpointed event for ``result_id``."""
        for ev in self.store.events(self.run_id):
            if (
                ev.type == "result.checkpointed"
                and ev.payload.get("result_id") == result_id
                and ev.payload.get("base_sha")
            ):
                return ev.payload.get("base_sha")
        return None

    def _changed_test_modules(self, base, commit, layer_paths) -> list[str]:
        """Test modules changed in base..commit under declared layer paths."""
        try:
            proc = git(
                self.repo, "diff", "--name-only", f"{base}..{commit}", check=False
            )
            changed = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        except Exception:
            return []
        return sorted(
            ln
            for ln in changed
            if ln.endswith(".py")
            and any(
                ln == rel or ln.startswith(rel.rstrip("/") + "/")
                for rel in layer_paths
            )
        )

    def _collected_prefixes(self, collected) -> set[str] | None:
        """File-prefix set of the latest collect's per-node blob (None when
        the blob is unreadable -- the caller treats that as no signal)."""
        try:
            per_node = self._read_runtime_blob(collected.payload.get("per_node_blob"))
        except TestSelectError:
            return None
        return {(entry.get("node") or "").partition("::")[0] for entry in per_node}

    @staticmethod
    def _stale_selection_fields(
        persisted_selection,
        selection_id: str,
        tree_stamp: str,
        baseline_id,
        selected_all,
    ) -> list[str]:
        """FA-5: the persisted test.selected identity fields that diverge from
        the CURRENT recomputed identity (empty when no record or exact match)."""
        if persisted_selection is None:
            return []
        stale_keys = [
            key
            for key in ("selection_id", "tree_stamp")
            if persisted_selection.get(key) != {
                "selection_id": selection_id,
                "tree_stamp": tree_stamp,
            }[key]
        ]
        if persisted_selection.get("baseline") != str(baseline_id or ""):
            stale_keys.append("baseline")
        if list(persisted_selection.get("nodes") or []) != list(selected_all):
            stale_keys.append("nodes")
        return stale_keys

    def _emit_stale_selection_failure(
        self,
        cmd,
        state,
        persisted_selection,
        stale_keys: list[str],
        selection_id: str,
        tree_stamp: str,
        baseline_id,
        selected_all,
    ) -> None:
        """Refuse to execute against a moved tree under a stale selection
        identity (FA-5 evidence-integrity pin)."""
        log_ref = self._red_log_blob_ref({"stale_fields": stale_keys})
        self._emit(
            "verdict.failed",
            {
                "check": "stale",
                "target_stage": "M-TEST",
                "artifact_disposition": "rollback",
                "reason": (
                    "replayed command's persisted test.selected identity "
                    f"diverged on {', '.join(stale_keys)}: refusing to "
                    "execute against a moved tree under the stale "
                    "selection identity"
                ),
                "evidence": {
                    "stale_fields": stale_keys,
                    "persisted": {
                        key: persisted_selection.get(key)
                        for key in (
                            "selection_id",
                            "tree_stamp",
                            "baseline",
                            "nodes",
                        )
                    },
                    "recomputed": {
                        "selection_id": selection_id,
                        "tree_stamp": tree_stamp,
                        "baseline": str(baseline_id or ""),
                        "nodes": list(selected_all),
                    },
                },
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _persist_selection_nodes(
        self, cmd, state, selected_by_layer, selected_all
    ) -> str | None:
        """Write the per-node layer map blob for this selection; None (after
        emitting contract_error) when the blob write fails (fail closed)."""
        sel_blob = self.store.write_audit_blob(
            [
                {
                    "node": node,
                    "layer": next(
                        (lyr for lyr, ns in selected_by_layer.items() if node in ns), ""
                    ),
                }
                for node in selected_all
            ]
        )
        if sel_blob is None:
            self._emit_contract_error_red(
                cmd, state, "RED_CHECK selection nodes blob write failed"
            )
            return None
        return f".tracks/runtime/blobs/{sel_blob}"

    def _emit_test_selected(
        self, cmd, baseline_id, selected_all, nodes_blob, commit, tree_stamp, selection_id
    ) -> None:
        self._emit(
            "test.selected",
            {
                "scope": _R2_SCOPE,
                "basis": _R2_BASIS,
                "nodes_count": len(selected_all),
                "nodes": list(selected_all),
                "nodes_blob": nodes_blob,
                "baseline": str(baseline_id or ""),
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": None,
                "task_ifs": None,
            },
            command_id=cmd.command_id,
        )

    def _emit_unit_only_increment_valid(self, cmd, selection_id, nodes_blob) -> None:
        """Hotfix explicit unit-only increment bypass: empty selection with
        a declared increment releases without any execution (FR-0244)."""
        self._emit(
            "red.validated",
            {
                "status": "valid",
                "findings": [],
                "basis": "unit-only hotfix increment",
                "selection_id": selection_id,
                "nodes_blob": nodes_blob,
            },
            command_id=cmd.command_id,
        )

    def _execute_red_check(
        self, cmd, state, sections, selected_by_layer, selection_id, nodes_blob
    ) -> None:
        """Execute the selection, then classify legal Red per node: all-legit
        -> red.validated(valid) -> PRISM_REVIEW; illegit/unexpected pass ->
        red.validated(invalid) -> DIAGNOSE."""
        logs: dict[str, dict] = {}
        outcomes, findings, all_legit, error = self._execute_selected_layers(
            cmd, sections, selected_by_layer, logs
        )
        if error is not None:
            self._emit_contract_error_red(cmd, state, error)
            return
        outcomes_ref = self.store.write_audit_blob(outcomes)
        if outcomes_ref is None:
            # Review-8: the normalized per-node outcomes table is the evidence
            # red.validated binds to -- never emit a valid verdict carrying a
            # null outcomes reference (fail closed via contract_error).
            self._emit_contract_error_red(
                cmd, state, "RED_CHECK outcomes evidence blob write failed"
            )
            return
        outcomes_ref_path = f".tracks/runtime/blobs/{outcomes_ref}"
        if all_legit:
            self._emit(
                "red.validated",
                {
                    "status": "valid",
                    "findings": findings,
                    "selection_id": selection_id,
                    "nodes_blob": nodes_blob,
                    "outcomes_ref": outcomes_ref_path,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit_red_check_invalid(
            cmd, state, findings, logs, outcomes, selection_id, nodes_blob, outcomes_ref_path
        )

    def _execute_selected_layers(
        self, cmd, sections, selected_by_layer, logs: dict[str, dict]
    ) -> tuple[list[dict], list[dict], bool, str | None]:
        """Run ONLY the selected nodes through each layer's contract
        ``run_selected`` template ({nodes}/{result} substituted; concurrency/
        test result flags are contract-owned, Runtime injects nothing).

        Returns ``(outcomes, findings, all_legit, error_reason)``; on a
        test result/contract/OS failure ``error_reason`` is set (FRB-G) while the
        staged per-command result files are still cleaned up (FA-4)."""
        outcomes: list[dict] = []
        findings: list[dict] = []
        all_legit = True
        staged_results: list[Path] = []
        oob_files = self._oob_accepted_test_files()
        try:
            for name, section in sections:
                nodes = selected_by_layer.get(name) or []
                if not nodes:
                    # A declared layer without R2 delta never executes here:
                    # M-TEST runs neither the full run command nor R1 history.
                    continue
                mapping = self._run_layer_command(
                    cmd, name, section, nodes, staged_results, logs
                )
                if not self._record_layer_outcomes(
                    nodes, mapping, outcomes, findings, oob_files
                ):
                    all_legit = False
        except (TestResultError, TestSelectError, OSError, UnicodeError) as exc:
            # FRB-G: a missing run_selected executable (OSError) or an
            # undecodable output stream routes the contract_error channel
            # (red.validated invalid + verdict.failed), never a raw crash.
            return [], [], False, f"{type(exc).__name__}: {exc}"
        finally:
            self._cleanup_staged_results(staged_results)
        return outcomes, findings, all_legit, None

    def _run_layer_command(self, cmd, name, section, nodes, staged_results, logs):
        """Execute one layer's selected nodes; returns the node→case mapping."""
        cwd = self.repo / section.cwd if section.cwd != "." else self.repo
        result_path = self._result_staging_path(cmd.command_id, name)
        staged_results.append(result_path)
        # Review-4: pre-existing XML at this command/layer path is a
        # previous attempt's record -- poison, not evidence. Unlink it
        # so a command writing no fresh result fails closed on the
        # missing file instead of reusing stale records.
        result_path.unlink(missing_ok=True)
        argv = resolve_selected_command(
            section.run_selected, nodes, str(result_path), cwd
        )
        if not audit_selection_argv(
            section.run_selected, nodes, str(result_path), list(argv), cwd
        ):
            raise TestSelectError(
                f"[{name}] executed argv diverges from the contract's "
                "run_selected expansion (injected argument)"
            )
        proc = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True)
        logs[name] = {
            "returncode": proc.returncode,
            "command_echo": list(argv),
            "stdout": proc.stdout[-8000:],
            "stderr": proc.stderr[-8000:],
        }
        cases = parse_test_result(result_path)
        return require_exact_node_coverage(cases, nodes)

    def _cleanup_staged_results(self, staged_results: list[Path]) -> None:
        # FA-4 (final review pin): the per-command staging XML is consumed
        # evidence, not residue -- unlink it after a normal parse success
        # AND after every handled failure so one attempt's record never
        # leaks into the next; then remove the per-run staging directory
        # when it is empty ("when possible": a non-empty dir keeps other
        # commands' live results and stays).
        for staged in staged_results:
            staged.unlink(missing_ok=True)
        if staged_results:
            with contextlib.suppress(OSError):
                staged_results[0].parent.rmdir()

    @staticmethod
    def _record_layer_outcomes(
        nodes, mapping, outcomes: list[dict], findings: list[dict], oob_files: set[str]
    ) -> bool:
        """Append one normalized outcome + finding per selected node (the
        {result} test result record is the sole per-node authority; stdout/stderr
        are logs only). Returns True when every node classified as legal Red;
        an unexpected pass/skip classifies unexpected_pass -- unless the node's
        file is OOB-declared (see _oob_accepted_test_files), in which case
        green-on-arrival is legal (oob_verified)."""
        layer_legit = True
        for node in nodes:
            case = mapping[node]
            klass = (
                "unexpected_pass"
                if case.status in ("passed", "skipped")
                else classify_red_detail(case.detail or "", case.status)
            )
            if klass == "unexpected_pass" and node.split("::")[0] in oob_files:
                klass = "oob_verified"
            if klass not in _LEGIT_RED and klass != "oob_verified":
                layer_legit = False
            outcomes.append(
                {
                    "node": node,
                    "status": case.status,
                    "classification": klass,
                    "detail": case.detail or "",
                }
            )
            findings.append(
                {
                    "test_id": node,
                    "classification": klass,
                    "detail": _short_detail(case.detail or ""),
                }
            )
        return layer_legit

    def _emit_red_check_invalid(
        self,
        cmd,
        state,
        findings: list[dict],
        logs: dict[str, dict],
        outcomes: list[dict],
        selection_id: str,
        nodes_blob: str,
        outcomes_ref_path: str,
    ) -> None:
        """Invalid Red -> DIAGNOSE: classify the gap and emit verdict.failed."""
        log_ref = self._red_log_blob_ref({"sections": logs, "outcomes": outcomes})
        self._emit(
            "red.validated",
            {
                "status": "invalid",
                "findings": findings,
                "log_ref": log_ref,
                "selection_id": selection_id,
                "nodes_blob": nodes_blob,
                "outcomes_ref": outcomes_ref_path,
            },
            command_id=cmd.command_id,
        )
        classification = self._diagnose_classification()
        self._emit(
            "verdict.failed",
            {
                "check": classification,
                "target_stage": _DIAGNOSE_TARGET.get(classification, "M-TEST"),
                "artifact_disposition": "rewrite"
                if classification == "test_defect"
                else "rollback",
                "reason": next(
                    (
                        f["classification"]
                        for f in findings
                        if f["classification"] not in _LEGIT_RED
                        and f["classification"] != "oob_verified"
                    ),
                    "invalid",
                ),
                "evidence": findings,
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _do_check_trace(self, cmd, state, task_id, reconcile):
        """SM-01.14 EXIT gate: trac check trace closure, filtered to required
        ACs (integration|e2e layer, FR-0070 decision A / architecture.md §5.1).

        v0.6 hotfix empty-Shield increment (FR-0244-04, IF-HOTFIX-007): when
        the EXIT route carries ``hotfix=True``, emit the increment.declared
        release evidence (shield=empty + delta §8 unit rows) and run the
        plan-level closure (check_hotfix_plan_closure) on the anchor AC set."""
        if reconcile and state.trace_passed:
            return
        vdir = self._vdir()
        tests_dir = self.repo / "tests"
        report, blocking = self._trace_report(cmd, state, vdir, tests_dir)
        self._emit_trace_verdict(cmd, state, report, blocking)

    def _trace_report(self, cmd, state, vdir: Path, tests_dir: Path) -> tuple[object, list[str]]:
        """Build the trace closure report + its blocking hard errors."""
        from tracks.checks.trace import check_trace_full_file  # lazy: avoid circular import

        if cmd.params.get("hotfix"):
            report, unit_rows = self._hotfix_trace_report(vdir, tests_dir, state)
            if report.status == "pass":
                self._emit(
                    "increment.declared",
                    {"shield": "empty", "unit_rows": unit_rows, "trace_status": "pass"},
                    command_id=cmd.command_id,
                )
            return report, list(report.hard_errors)
        # IF-HOTFIX-007: a hotfix run WITH Shield integration tests still
        # needs the plan-level hotfix trace closure (cross-version AC
        # markers resolve via the target version dir, not the delta dir).
        # Without hotfix_ctx the delta-dir check sees anchored ACs as
        # unbound (PRISM-V06-R16-01, T-004). No increment.declared (that
        # is empty-shield only; this path has Shield tests committed).
        if getattr(state, "hotfix_anchor_acs", None):
            report = self._anchored_hotfix_trace_report(vdir, tests_dir, state)
            return report, list(report.hard_errors)
        report = check_trace_full_file(vdir, tests_dir)
        required = required_ac_ids(vdir / "acceptance.md", vdir / "test-plan.md")
        blocking = (
            [e for e in report.hard_errors if any(rid in e for rid in required)]
            if required
            else list(report.hard_errors)
        )
        return report, blocking

    def _hotfix_trace_report(
        self, vdir: Path, tests_dir: Path, state
    ) -> tuple[object, list]:
        """Empty-Shield hotfix closure over the plan's declared unit rows."""
        from tracks.checks.trace import check_trace_full_file
        from tracks.executor.test_tasks import parse_hotfix_unit_rows

        plan_path = vdir / "test-plan.md"
        plan_text = (
            plan_path.read_text(encoding="utf-8", errors="replace")
            if plan_path.exists()
            else ""
        )
        unit_rows = parse_hotfix_unit_rows(plan_text)
        report = check_trace_full_file(
            vdir,
            tests_dir,
            hotfix_ctx={
                "projects_dir": str(paths.projects_dir(self.store.home)),
                "run_id": self.run_id,
                "anchor_acs": list(state.hotfix_anchor_acs or []),
                "declared_unit_rows": unit_rows,
            },
        )
        return report, unit_rows

    def _anchored_hotfix_trace_report(self, vdir: Path, tests_dir: Path, state) -> object:
        """Plan-level hotfix closure carrying the run's anchor AC set."""
        from tracks.checks.trace import check_trace_full_file

        increment = next(
            (
                ev.payload
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "increment.declared"
            ),
            None,
        )
        unit_rows = increment.get("unit_rows", []) if increment else []
        return check_trace_full_file(
            vdir,
            tests_dir,
            hotfix_ctx={
                "projects_dir": str(paths.projects_dir(self.store.home)),
                "run_id": self.run_id,
                "anchor_acs": list(state.hotfix_anchor_acs or []),
                "declared_unit_rows": unit_rows,
            },
        )

    def _emit_trace_verdict(self, cmd, state, report, blocking: list[str]) -> None:
        if not blocking:
            self._emit(
                "verdict.passed",
                {"check": "trace", "detail": "trace closure verified"},
                command_id=cmd.command_id,
            )
        else:
            self._emit(
                "verdict.failed",
                {
                    "check": "trace",
                    "reason": "; ".join(blocking),
                    "evidence": "trac check trace",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )

    def _do_commit_tests(self, cmd, state, task_id, reconcile):
        """SM-01.14: freeze the test asset via a controlled git commit of tests/
        -> test.committed. stage.exited + run.completed are handled by the
        subsequent write_frontmatter command (v0.5 batch 2)."""
        if reconcile and state.test_committed:
            return
        marker = f"command_id: {cmd.command_id}"
        tests_dir = self.repo / "tests"
        # B59 (#75): the designed flow commits Shield's WRITE output during the
        # pipeline itself (_stage_and_commit) — at freeze time tests/ must be
        # ENTIRELY clean. Any dirty file, tracked or untracked, is residue from
        # a DISCARDED cycle (run 01M0S0FQ: three freeze commits — 1e135d7,
        # 632a84f, 02417f9 — silently absorbed prior Devon RED residue,
        # seeding the baseline with tests no current round wrote). Fail closed
        # on all of it instead of absorbing: the operator cleans the tree and
        # `trac retry` re-enters the freeze. (Runtime-owned test-runner
        # byproducts under tests/ are NOT residue: `trac init` gitignores them
        # at the host root — see HOST_BYPRODUCTS_GITIGNORE — so they never
        # reach this check.)
        if tests_dir.exists():
            status = git(self.repo, "status", "--porcelain", "--", "tests")
            residue = [
                line[3:].split(" -> ")[-1].strip()
                for line in status.stdout.splitlines()
                if line.strip()
            ]
            if residue:
                self._emit(
                    "verdict.failed",
                    {
                        "check": "test_freeze_contamination",
                        "reason": (
                            "tracked files under tests/ are modified at freeze time "
                            "and are not attributable to the current Shield WRITE "
                            "result; refusing to freeze them into the test baseline"
                        ),
                        "evidence": "; ".join(residue[:20]),
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                )
                return
            git(self.repo, "add", "tests")
        proc = _scoped_commit_if_staged(
            self.repo, f"M-TEST: freeze test assets\n\n{marker}", paths=["tests"]
        )
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        # proc is None when there's nothing new to stage — tests were already
        # committed during the WRITE pipeline. That's OK: emit test.committed
        # pointing at the current HEAD (the existing freeze commit).
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        test_count = sum(1 for _ in tests_dir.rglob("test_*.py")) if tests_dir.exists() else 0
        self._emit(
            "test.committed",
            {"commit_sha": commit_sha, "test_count": test_count},
            command_id=cmd.command_id,
        )

    def _diagnose_classification(self) -> str:
        """Read the DIAGNOSE classification from the fake backend's simulate
        token (default ``test_defect``). The real channel dispatches Prism for
        diagnostic review; the fake channel keeps it deterministic."""
        default = "test_defect"
        backend = getattr(self, "backend", None)
        token_fn = getattr(backend, "token", None)
        token = token_fn("diagnose", "classification", default) if token_fn else default
        return (
            token
            if token in (
                "test_defect", "stub_gap", "ac_gap", "spec_gap", "impl_defect",
                "red_defect", "plan_defect", "unknown",
            )
            else default
        )
