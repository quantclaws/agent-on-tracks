"""M-IMPL RGR phase dispatch (mixin ``MImplDispatchMixin``): task gates,
task review, selected-layer execution and the REFACTOR gate.

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tracks import paths
from tracks.executor.helpers import git
from tracks.executor.m_impl_anchor import _is_evidence_shape_error, _manifest_path_matches
from tracks.executor.m_impl_diagnose import _m_impl_red_classification_error
from tracks.executor.m_impl_green import _task_review_failure
from tracks.executor.oscillation import OSCILLATION_CHECK as _OSCILLATION_CHECK
from tracks.executor.oscillation import detect_oscillation as _detect_oscillation
from tracks.executor.oscillation import parse_failed_nodes as _parse_failed_nodes
from tracks.executor.taskgraph import TaskNode
from tracks.executor.test_select import (
    EvidenceIdentity,
    TestResultError,
    TestSelectError,
    emit_stale_propagation,
    evidence_identity,
    make_selection_id,
)
from tracks.executor.worktree import cleanup_worktree
from tracks.kernel.machine import State
from tracks.project import ContractError


class TaskSelectionFailure(Exception):
    """Selected task tests executed correctly but did not all pass."""

    def __init__(self, message: str, evidence: str):
        super().__init__(message)
        self.evidence = evidence


class MImplDispatchMixin:
    """M-IMPL RGR dispatch mixin."""

    def _do_run_task_gates(self, cmd, state, task_id, reconcile):
        gate = cmd.params.get("gate")
        if gate == "RED_GATE":
            self._emit_task_gate(cmd, state, "red", "red_invalid", "red_valid")
        elif gate == "GREEN_GATE":
            self._run_green_gate(cmd, state)
        elif gate == "TASK_REVIEW":
            self._run_task_review_gate(cmd, state)
        if gate in ("RED_GATE", "GREEN_GATE", "TASK_REVIEW"):
            self._rebuild_task_log_projection()

    def _run_green_gate(self, cmd, state: State) -> None:  # pylint: disable=too-many-locals
        task_id = state.current_task_id
        attempt = state.current_attempt + 1
        reason = self._devon_evidence_error("green", state)
        if reason is not None:
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason=reason,
                evidence="backend Devon outcome",
                task_id=task_id,
                attempt=attempt,
            )
            return
        cwd, gate_handle = self._ensure_gate_worktree(state)
        try:
            # B39 (run 01M0AMKV T-006 seq 728): regression is judged ONLY on
            # this task's Runtime-captured diff plus its claimed changed_paths
            # touching an R-frozen tests/ file — not on the whole worktree-vs-R
            # tests/ diff. The old full diff misjudged unrelated baseline test
            # additions (ops fixes, earlier batch siblings) as task cheating,
            # false-failing every later task in a batch that lands tests after
            # R. The captured diff still surfaces hidden R-test mutations that
            # Devon omits from changed_paths; the union closes the self-report.
            r_sha = state.r_tree_identity
            if r_sha and r_sha.strip() and r_sha != "0" * 40:
                bad = self._green_regression_tests(r_sha, state)
                if bad:
                    self._emit_gate_failure(
                        cmd,
                        check="regression",
                        reason="Runtime observed R unit tests changed",
                        evidence=json.dumps(
                            {"r_sha": r_sha, "changed_tests": bad},
                            sort_keys=True,
                        ),
                        task_id=task_id,
                        attempt=attempt,
                    )
                    return
            task = self._lookup_task(task_id or "")
            if task is None:
                raise TestSelectError(f"GREEN_GATE task not found: {task_id}")
            selection_evidence = self._execute_task_selection(cmd, state, cwd, task)
            lint_findings, lint_note = self._run_declared_lint(self._lint_targets("green"))
            if lint_findings is not None:
                self._emit_gate_failure(
                    cmd,
                    check="lint",
                    reason="lint findings in GREEN deliverables ([lint].check)",
                    evidence=lint_findings,
                    task_id=task_id,
                    attempt=attempt,
                )
                return
            payload = {"check": "green", "task_id": task_id, **selection_evidence}
            if lint_note:
                payload["lint"] = lint_note
            self._emit("verdict.passed", payload, command_id=cmd.command_id)
        except TaskSelectionFailure as exc:
            # M1-S1 (convergence plan 2026-09-05): mechanical oscillation
            # signature -- consecutive GREEN_GATE failures of this task
            # swapped anchor outcomes. Mutually exclusive anchors admit no
            # writer retry; route the contract authority instead of
            # re-burning the writer budget. The CURRENT failure's anchors
            # (exc.evidence, not yet stored) are compared against the last
            # recorded failure for this task so the signature trips at the
            # SECOND failure, not one attempt later.
            events = [
                {"type": ev.type, "payload": dict(ev.payload or {})}
                for ev in self.store.events(self.run_id)
            ]
            # OOB 2026-09-06 (S1 reversibility): resolve the PREVIOUS S1
            # firing's swap sets (blob-externalized) so the pure detector
            # can require a repeat swap -- fresh churn stays the writer's
            # domain instead of escalating every partial-progress round
            # into an authority war (run 01M19FJVES7G113RD8QXXY3PQZ:
            # 05:09-07:03, two hours of authority churn, zero writer
            # dispatches).
            osc = _detect_oscillation(
                events,
                task_id,
                exc.evidence,
                prior_oscillation=self._prior_oscillation_sets(task_id),
            )
            if osc is not None:
                self._emit(
                    "oscillation.detected",
                    {
                        "task_id": task_id,
                        "signature": "S1",
                        **osc,
                        "task_selection": exc.evidence,
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
                self._emit_gate_failure(
                    cmd,
                    check=_OSCILLATION_CHECK,
                    reason=(
                        "anchor oscillation (S1): healed∧newly_red∧common "
                        "across consecutive attempts — mutually exclusive "
                        "anchors, no writer retry can satisfy both; Archer "
                        "RULING carries the paired-delta decision"
                    ),
                    evidence=json.dumps(
                        {
                            "oscillation": osc,
                            "task_selection": exc.evidence,
                            # OOB 2026-09-05 (baseline-advance fix): carry the
                            # CURRENT failing set at the evidence root so the
                            # next detection compares against THIS round, not
                            # the pre-oscillation baseline. Without it the
                            # contract_conflict verdict is skipped by
                            # last_failed_nodes and the replan->Devon->gate
                            # loop re-fires the identical S1 forever (run
                            # 01M19FJVES7G113RD8QXXY3PQZ seq 3016==3054).
                            "failed_nodes": _parse_failed_nodes(exc.evidence),
                        },
                        sort_keys=True,
                    ),
                    task_id=task_id,
                    attempt=attempt,
                    failure_class="contract_conflict",
                )
                return
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason=str(exc),
                evidence=exc.evidence,
                task_id=task_id,
                attempt=attempt,
            )
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit_gate_failure(
                cmd,
                check="contract_error",
                reason=f"SELECT_TASK failed closed: {type(exc).__name__}: {exc}",
                evidence="task_if selection/contract result",
                task_id=task_id,
                attempt=attempt,
            )
        finally:
            if gate_handle is not None:
                cleanup_worktree(gate_handle)

    def _prior_oscillation_sets(self, task_id: str) -> dict | None:
        """The latest recorded S1 firing's {healed, newly_red} for this task
        (the detection currently being evaluated has NOT been emitted yet,
        so the latest in the store IS the prior one). The 8KB store
        discipline externalizes payloads to blobs -- resolved through the
        store; unreadable/absent -> None (legacy first-firing semantics,
        fail-closed)."""
        prior = next(
            (
                dict(ev.payload or {})
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "oscillation.detected"
                and (ev.payload or {}).get("task_id") == task_id
            ),
            None,
        )
        if not isinstance(prior, dict) or not prior:
            return None
        if "$ref" in prior:
            try:
                prior = self.store.load_payload(prior) or {}
            except (OSError, ValueError):
                return None
        if not prior.get("newly_red"):
            return None
        return {
            "healed": list(prior.get("healed") or []),
            "newly_red": list(prior.get("newly_red") or []),
        }

    def _run_task_review_gate(self, cmd, state: State) -> None:
        task_id = state.current_task_id
        attempt = state.current_attempt + 1
        events = [
            {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
            for ev in self.store.events(self.run_id)
        ]
        # B84 (#84): scan reversed events for the most recent green event
        # (green.committed or green.no_change) whose task_id matches the
        # current task — never use a stale green.committed from another task.
        green_for_task = next(
            (
                ev
                for ev in reversed(events)
                if ev["type"] in ("green.committed", "green.no_change")
                and ev["payload"].get("task_id") == task_id
            ),
            None,
        )
        if green_for_task is not None and green_for_task["type"] == "green.no_change":
            self._run_task_review_no_change(cmd, task_id, attempt, events)
            return
        if green_for_task is not None:
            self._run_task_review_committed(cmd, task_id, attempt, events, green_for_task)
            return
        any_green_committed = any(ev["type"] == "green.committed" for ev in events)
        if any_green_committed:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="green.committed lacks task identity",
                task_id=task_id,
                attempt=attempt,
            )
            return
        self._emit_gate_failure(
            cmd,
            check="lineage",
            reason="no green.committed event found",
            task_id=task_id,
            attempt=attempt,
        )

    def _run_task_review_no_change(self, cmd, task_id, attempt, events):
        """No-change review: no new G exists, scope/lineage/secret/provenance
        checks are vacuous — GREEN_GATE already programmatically verified
        the behavior. Budget check still runs. Source: #84 event 9074-9078."""
        task = self._lookup_task(task_id)
        if task is None:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="task lookup failed for no-change review",
                task_id=task_id,
                attempt=attempt,
            )
            return
        manifest = self._current_manifest() or {}
        failure = _task_review_failure(
            str(self.repo),
            self.run_id,
            events,
            task,
            None,
            None,
            {},
            manifest.get("allowed_paths") or [],
            task_id,
            attempt,
        )
        if failure is not None:
            self._emit_gate_failure(
                cmd,
                check=failure["check"],
                reason=failure["reason"],
                evidence=failure.get("evidence", ""),
                task_id=task_id,
                attempt=attempt,
            )
            return
        self._emit("verdict.passed", {"check": "task_review"}, command_id=cmd.command_id)

    def _run_task_review_committed(self, cmd, task_id, attempt, events, green_ev):
        """Normal green.committed review: verify scope, lineage, secrets,
        provenance, and budget. Source: #84 event 9074-9078."""
        payload = green_ev["payload"]
        task = self._lookup_task(task_id)
        g_sha = payload.get("g_sha")
        base_sha = payload.get("base_sha")
        if task is None or not g_sha or not base_sha:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="green.committed lacks task or base identity",
                task_id=task_id,
                attempt=attempt,
            )
            return
        manifest = self._current_manifest() or {}
        failure = _task_review_failure(
            str(self.repo),
            self.run_id,
            events,
            task,
            g_sha,
            base_sha,
            payload.get("trailers") or {},
            manifest.get("allowed_paths") or [],
            payload.get("task_id") or task_id,
            payload.get("attempt") or attempt,
        )
        if failure is not None:
            self._emit_gate_failure(
                cmd,
                check=failure["check"],
                reason=failure["reason"],
                evidence=failure.get("evidence", ""),
                task_id=task_id,
                attempt=attempt,
            )
            return
        self._emit("verdict.passed", {"check": "task_review"}, command_id=cmd.command_id)

    def _emit_task_gate(self, cmd, state, phase, failure_check, pass_check):
        reason = self._devon_evidence_error(phase, state)
        if reason is None and phase == "red" and state.stage == "M-IMPL":
            outcome = self._last_devon_outcome() or {}
            classification_error = _m_impl_red_classification_error(outcome)
            if classification_error is not None:
                reason, evidence = classification_error
                self._emit(
                    "verdict.failed",
                    {
                        "check": failure_check,
                        "reason": reason,
                        "evidence": evidence,
                        "task_id": state.current_task_id,
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                    task_id=state.current_task_id,
                )
                return
        if reason is not None:
            self._emit_gate_failure(
                cmd,
                check=failure_check,
                reason=reason,
                evidence="backend Devon outcome",
                task_id=state.current_task_id,
                attempt=state.current_attempt + 1,
            )
            return
        lint_findings, lint_note = None, ""
        if phase == "red":
            # B4 (issue #5): post-agent validation runs before the verdict —
            # Archer-declared lint over the delivered RED test files
            # (mechanical errors surface in the phase that introduced
            # them, not at commit time; T-018 burned 3 attempts on this).
            lint_findings, lint_note = self._run_declared_lint(self._lint_targets("red"))
        if lint_findings is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "lint",
                    "reason": "lint findings in RED deliverables ([lint].check)",
                    "evidence": lint_findings,
                    "task_id": state.current_task_id,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=state.current_task_id,
            )
            return
        payload = {"check": pass_check}
        if lint_note:
            payload["lint"] = lint_note
        self._emit("verdict.passed", payload, command_id=cmd.command_id)

    def _execute_task_selection(self, cmd, state: State, cwd: str, task: TaskNode) -> dict:
        """Emit and execute one task_if selection through contract run_selected.
        B94: selection includes deferred refs (and integration hard gate)."""
        contract, nodes = self._collect_task_gate_nodes(cwd, task)
        by_layer = self._by_layer(nodes)
        baseline = str(state.r_tree_identity or "")
        if not baseline:
            raise TestSelectError("SELECT_TASK lacks immutable R baseline identity")
        commit = git(Path(cwd), "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp(Path(cwd))
        basis = f"task:{task.task_id}:ifs:{','.join(sorted(task.if_ids))}"
        selection_id = make_selection_id(
            nodes=nodes,
            scope="task_if",
            basis=basis,
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        self._stale_prior_task_selection(cmd, task, nodes, baseline, selection_id)
        node_ref = self.store.write_audit_blob(
            [
                {
                    "node": node,
                    "layer": "unit" if node.startswith("tests/unit/") else "integration",
                }
                for node in nodes
            ]
        )
        if node_ref is None:
            raise TestSelectError("SELECT_TASK nodes blob write failed")
        nodes_blob = f".tracks/runtime/blobs/{node_ref}"
        self._emit(
            "test.selected",
            {
                "scope": "task_if",
                "basis": basis,
                "nodes_count": len(nodes),
                "nodes": nodes,
                "nodes_blob": nodes_blob,
                "baseline": baseline,
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": task.task_id,
                "task_ifs": list(task.if_ids),
            },
            command_id=cmd.command_id,
            task_id=task.task_id,
        )
        scope_rules = list((state.current_manifest or {}).get("allowed_paths", []))
        scope_rules.extend(node.partition("::")[0] for node in nodes)
        scope_rules = sorted(set(scope_rules))
        snapshot = self._task_content_snapshot(Path(cwd), scope_rules)
        content_digest = self._snapshot_digest(snapshot)
        env_identity = self._gate_environment_identity()
        outcomes, failed_nodes, commands, templates, forensics_ref = (
            self._run_task_selected_layers(cmd, contract, cwd, by_layer)
        )
        command_identity = self._selection_command_identity(commands)
        identity = EvidenceIdentity(
            tree=content_digest,
            command=command_identity,
            env=env_identity,
            selection_id=selection_id,
        )
        evidence_ids = [
            evidence_identity(
                identity,
                outcome["node"],
                outcome["status"],
                state.current_attempt + 1,
                "runtime",
            )
            for outcome in outcomes
        ]
        outcomes_ref = self.store.write_audit_blob(outcomes)
        if outcomes_ref is None:
            raise TestSelectError("GREEN outcomes blob write failed")
        outcomes_ref_path = f".tracks/runtime/blobs/{outcomes_ref}"
        self._check_drift_breaker(cmd, task, failed_nodes)
        from tracks.executor.deferred_gate import partition_failures

        hard, deferred = partition_failures(
            failed_nodes, getattr(task, "deferred_refs", ()) or ()
        )
        if self._is_deferred_only(failed_nodes, hard, deferred):
            return self._deferred_success_payload(
                selection_id,
                nodes_blob,
                outcomes_ref_path,
                evidence_ids,
                content_digest,
                command_identity,
                env_identity,
                scope_rules,
                snapshot,
                commands,
                templates,
                deferred,
            )
        if hard:
            self._raise_hard_failure(
                selection_id, outcomes_ref_path, hard, deferred,
                forensics_ref=forensics_ref,
            )
        return self._success_payload(
            selection_id,
            nodes_blob,
            outcomes_ref_path,
            evidence_ids,
            content_digest,
            command_identity,
            env_identity,
            scope_rules,
            snapshot,
            commands,
            templates,
        )

    def _forensics_root(self, command_id: str) -> Path:
        """M2: per-selection forensic capture root under runtime state.

        ``.tracks/runtime/forensics/<command_id>/<layer>/`` is untracked
        runtime state (never dirties the frozen tree); sibling dirs older
        than 24h are pruned so the diagnostic window stays bounded."""
        import time as _time

        root = paths.runtime_dir(paths.tracks_home(Path(self.repo))) / "forensics"
        root.mkdir(parents=True, exist_ok=True)
        cutoff = _time.time() - 24 * 3600
        for stale in root.iterdir():
            try:
                if stale.is_dir() and stale.stat().st_mtime < cutoff:
                    shutil.rmtree(stale, ignore_errors=True)
            except OSError:
                continue
        return root / command_id

    def _collect_forensics_ref(self, forensics_root: Path, failed_nodes) -> str | None:
        """M2: persist the captured forensic package to an audit blob.

        Returns the blob path (referenced from the failure evidence) when
        failures occurred and the conftest hook captured forensics for
        them; None otherwise (nothing failed / nothing captured -- the
        evidence chain stays exactly as before)."""
        if not failed_nodes:
            return None
        records: list[dict] = []
        for path in sorted(forensics_root.glob("*/failures/*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        if not records:
            return None
        ref = self.store.write_audit_blob(
            {
                "forensics_dir": str(forensics_root),
                "records": records,
            }
        )
        if ref is None:
            return None
        return f".tracks/runtime/blobs/{ref}"

    @staticmethod
    def _record_task_node_outcomes(outcomes, failed_nodes, selected, mapping) -> None:
        for node in selected:
            case = mapping[node]
            detail = case.detail or ""
            outcomes.append({"node": node, "status": case.status, "detail": detail})
            if case.status != "passed":
                failed_nodes.append({"node": node, "status": case.status, "detail": detail})

    def _by_layer(self, nodes: list[str]) -> dict[str, list[str]]:
        return {
            "unit": [n for n in nodes if n.startswith("tests/unit/")],
            "integration": [n for n in nodes if n.startswith("tests/integration/")],
            "e2e": [n for n in nodes if n.startswith("tests/e2e/")],
        }

    def _is_deferred_only(self, failed_nodes, hard, deferred) -> bool:
        return bool(failed_nodes and not hard and deferred)

    def _snapshot_ref_or_raise(self, scope_rules, snapshot, commands, templates) -> str:
        ref = self.store.write_audit_blob(
            {
                "rules": scope_rules,
                "entries": snapshot,
                "commands": {layer: list(argv) for layer, argv in commands.items()},
                "templates": templates,
            }
        )
        if ref is None:
            raise TestSelectError("GREEN content identity blob write failed")
        return f".tracks/runtime/blobs/{ref}"

    def _raise_hard_failure(
        self, selection_id: str, outcomes_ref_path: str, hard, deferred,
        forensics_ref: str | None = None,
    ) -> None:
        evidence = {
            "selection_id": selection_id,
            "outcomes_ref": outcomes_ref_path,
            "failed_nodes": hard,
            "deferred_failures": (
                [str(x.get("node") or "") for x in deferred]
                if deferred
                else []
            ),
        }
        if forensics_ref:
            # M2: live forensic package (git state at failure + preserved
            # repos) -- DIAGNOSE must rule on this, never on static
            # post-teardown inference.
            evidence["forensics_ref"] = forensics_ref
        raise TaskSelectionFailure(
            "selected task tests did not all pass",
            json.dumps(evidence, sort_keys=True),
        )

    def _success_payload(
        self,
        selection_id,
        nodes_blob,
        outcomes_ref_path,
        evidence_ids,
        content_digest,
        command_identity,
        env_identity,
        scope_rules,
        snapshot,
        commands,
        templates,
    ) -> dict:
        snapshot_ref = self._snapshot_ref_or_raise(
            scope_rules, snapshot, commands, templates
        )
        return {
            "selection_id": selection_id,
            "nodes_blob": nodes_blob,
            "outcomes_ref": outcomes_ref_path,
            "evidence_ids": sorted(evidence_ids),
            "snapshot_ref": snapshot_ref,
            "identity_basis": {
                "tree": content_digest,
                "command": list(command_identity),
                "env": env_identity,
                "selection_id": selection_id,
            },
        }

    def _deferred_success_payload(
        self,
        selection_id,
        nodes_blob,
        outcomes_ref_path,
        evidence_ids,
        content_digest,
        command_identity,
        env_identity,
        scope_rules,
        snapshot,
        commands,
        templates,
        deferred,
    ) -> dict:
        payload = self._success_payload(
            selection_id,
            nodes_blob,
            outcomes_ref_path,
            evidence_ids,
            content_digest,
            command_identity,
            env_identity,
            scope_rules,
            snapshot,
            commands,
            templates,
        )
        payload["deferred_failures"] = sorted(
            str(x.get("node") or "") for x in deferred
        )
        return payload

    def _emit_stale_refs(
        self,
        cmd,
        task_id: str,
        selection_refs,
        evidence_refs,
        reason: str,
    ) -> None:
        targets = emit_stale_propagation(
            {
                "selection_refs": selection_refs,
                "evidence_refs": evidence_refs,
            }
        )
        already_stale = {
            (str(target.get("kind")), str(target.get("ref")))
            for ev in self.store.events(self.run_id)
            if ev.type == "evidence.staled"
            for target in (ev.payload.get("targets") or [])
            if isinstance(target, dict)
        }
        fresh_targets = [
            target for target in targets if (target["kind"], target["ref"]) not in already_stale
        ]
        if fresh_targets:
            self._emit(
                "evidence.staled",
                {"targets": fresh_targets, "reason": reason},
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _stale_prior_task_selection(
        self,
        cmd,
        task: TaskNode,
        nodes: list[str],
        baseline: str,
        selection_id: str,
    ) -> None:
        previous = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "test.selected"
                and ev.payload.get("scope") == "task_if"
                and ev.payload.get("task_id") == task.task_id
            ),
            None,
        )
        if previous is None or previous.payload.get("selection_id") == selection_id:
            return
        changes = []
        if sorted(previous.payload.get("task_ifs") or []) != sorted(task.if_ids):
            changes.append("task_if_changed")
        if previous.payload.get("baseline") != baseline:
            changes.append("baseline_changed")
        if sorted(previous.payload.get("nodes") or []) != sorted(nodes):
            changes.append("selected_nodes_changed")
        if not changes:
            changes.append("selection_identity_changed")
        previous_id = str(previous.payload.get("selection_id") or "")
        evidence_refs = {
            str(ref)
            for ev in self.store.events(self.run_id)
            if ev.payload.get("selection_id") == previous_id
            or (ev.payload.get("identity_basis") or {}).get("selection_id") == previous_id
            for ref in (ev.payload.get("evidence_ids") or [])
            if ref
        }
        self._emit_stale_refs(
            cmd,
            task.task_id,
            [previous_id],
            evidence_refs,
            ",".join(changes),
        )

    def _do_run_refactor_gate(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        gate_handle = None
        try:
            reason = self._devon_evidence_error("refactor", state)
            if reason is not None:
                self._emit_refactor_evidence_error(cmd, reason, state)
                return
            cwd, gate_handle = self._ensure_gate_worktree(state, phase="refactor")
            outcome = self._last_devon_outcome() or {}
            task_id = state.current_task_id or task_id or ""
            reusable, observed_changed, green = self._green_reuse_state(task_id, str(self.repo))
            if reusable:
                self._reuse_green_evidence(cmd, task_id, outcome, green)
                return
            self._flag_stale_green_evidence(cmd, task_id, green, observed_changed)
            task = self._lookup_task(task_id)
            if task is None:
                raise TestSelectError(f"REFACTOR_GATE task not found: {task_id}")
            if self._refactor_diff_missing(
                outcome, observed_changed
            ) and not self._refactor_diff_reconstructable(outcome, observed_changed):
                raise TestSelectError("refactor content identity changed without a captured diff")
            selection_evidence = self._execute_task_selection(cmd, state, cwd, task)
            changed = sorted(set(outcome.get("changed_paths", [])) | set(observed_changed))
            allowed = set((state.current_manifest or {}).get("allowed_paths", []))
            outside = self._refactor_outside_paths(changed, allowed)
            if outside:
                self._emit(
                    "verdict.failed",
                    {
                        "check": "scope",
                        "reason": "refactor changed forbidden paths: " + ", ".join(outside),
                        "evidence": str(outside),
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                )
                return
            if not changed:
                self._emit(
                    "refactor.no_change",
                    {
                        "task_id": task_id,
                        "reason": outcome.get("no_change_reason"),
                        **selection_evidence,
                    },
                    command_id=cmd.command_id,
                )
                return
            committed = self._commit_refactor_changes(
                cmd, state, task_id, changed, selection_evidence
            )
            if not committed:
                return
        except TaskSelectionFailure as exc:
            self._emit_gate_failure(
                cmd,
                check="regression",
                reason=str(exc),
                evidence=exc.evidence,
                task_id=state.current_task_id,
                attempt=state.current_attempt + 1,
            )
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit_gate_failure(
                cmd,
                check="contract_error",
                reason=f"REFACTOR SELECT_TASK failed closed: {type(exc).__name__}: {exc}",
                evidence="task_if refactor selection/identity",
                task_id=state.current_task_id,
                attempt=state.current_attempt + 1,
            )
        finally:
            if gate_handle is not None:
                cleanup_worktree(gate_handle)
            self._rebuild_task_log_projection()

    @staticmethod
    def _refactor_diff_missing(outcome: dict, observed_changed) -> bool:
        diff_ref = outcome.get("diff_ref")
        return bool(observed_changed) and (
            not isinstance(diff_ref, str) or not diff_ref.strip() or diff_ref.strip() == "no-change"
        )

    def _refactor_diff_reconstructable(self, outcome: dict, observed_changed) -> bool:
        """B55 (#71): the OpenCode backend never captures diff_ref for Devon
        RGR worktree phases (the artifact is changed_paths + pre/post
        identities; GREEN outcomes carry diff_ref=None too). RED/GREEN
        already reconstruct the diff from the working tree
        (_validated_diff's fallback); REFACTOR must accept the same
        reconstruction -- the refactor changes sit uncommitted in the main
        tree at gate time -- instead of parking every real refactor with a
        contract_error."""
        union = {
            "changed_paths": sorted(
                set(outcome.get("changed_paths") or []) | set(observed_changed or [])
            )
        }
        return self._generate_diff_from_changed_paths(union) is not None

    @staticmethod
    def _refactor_outside_paths(changed, allowed) -> list[str]:
        return sorted(
            path
            for path in changed
            if not any(_manifest_path_matches(path, rule) for rule in allowed)
        )

    def _commit_refactor_changes(
        self, cmd, state, task_id: str, changed, selection_evidence
    ) -> bool:
        git(self.repo, "add", "--", *changed)
        # Lazy import: executor.py imports this module's mixin at module
        # scope, so the scoped commit helper resolves at call time.
        from tracks.executor.executor import _scoped_commit_if_staged

        proc = _scoped_commit_if_staged(
            self.repo,
            f"M-IMPL: refactor\n\ncommand_id: {cmd.command_id}",
            paths=sorted(changed),
        )
        if proc is None:
            self._emit(
                "verdict.failed",
                {
                    "check": "regression",
                    "reason": "refactor evidence has no captured diff",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return False
        if proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return False
        self._emit(
            "refactor.committed",
            {"task_id": task_id, **selection_evidence},
            command_id=cmd.command_id,
        )
        return True

    def _emit_refactor_evidence_error(self, cmd, reason: str, state) -> None:
        if _is_evidence_shape_error(reason):
            self._emit(
                "verdict.failed",
                {
                    "check": "evidence_malformed",
                    "failure_class": "evidence_malformed",
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
        else:
            self._emit(
                "verdict.failed",
                {
                    "check": "regression",
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
