"""M-IMPL runtime aggregator.

The public ``MImplRuntimeMixin`` composes the M-IMPL domain mixins
extracted under :mod:`tracks.executor` (assignment/evidence, taskgraph
ledger, island gates, FULL chain, test ops, GREEN chain, RED diagnosis,
RGR dispatch) while keeping the public ``Executor`` method surface
unchanged.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from tracks import paths
from tracks.executor.helpers import git
from tracks.executor.m_impl_anchor import (
    MImplAnchorMixin,
    _combined_provenance,
    _devon_path_scope_error,  # noqa: F401
    _is_evidence_shape_error,
    _manifest_path_matches,
)
from tracks.executor.m_impl_full_chain import MImplFullChainMixin
from tracks.executor.m_impl_islands import (
    MImplIslandsMixin,
    _resolve_island_gate_2,  # noqa: F401
)
from tracks.executor.m_impl_ledger import MImplLedgerMixin
from tracks.executor.m_impl_testops import MImplTestOpsMixin
from tracks.executor.oscillation import OSCILLATION_CHECK as _OSCILLATION_CHECK
from tracks.executor.oscillation import detect_oscillation as _detect_oscillation
from tracks.executor.oscillation import parse_failed_nodes as _parse_failed_nodes
from tracks.executor.quality_gate import failed_summary_lines
from tracks.executor.rgr import (
    adopt_red_ref,
    create_green_commit,
    create_red_ref,
    red_base_sha,
    verify_lineage,
)
from tracks.executor.taskgraph import TaskNode, parse_tasks_json
from tracks.executor.test_select import (
    EvidenceIdentity,
    TestResultError,
    TestSelectError,
    emit_stale_propagation,
    evidence_identity,
    make_selection_id,
    resolve_selected_command,
    reuse_allowed,
)
from tracks.executor.worktree import cleanup_worktree
from tracks.kernel.machine import State
from tracks.project import ContractError, load_contract

_SECRET_PATTERN = re.compile(r"(sk-|ghp_|gho_|AKIA)[A-Za-z0-9]{16,}")

class TaskSelectionFailure(Exception):
    """Selected task tests executed correctly but did not all pass."""

    def __init__(self, message: str, evidence: str):
        super().__init__(message)
        self.evidence = evidence

def _green_diff_checks(
    repo, run_id, task_id, attempt, g_sha, base_sha, events, allowed, trailers, task
) -> dict | None:
    if g_sha is None or base_sha is None:
        return None
    diff_proc = git(Path(repo), "diff", "--name-only", base_sha, g_sha, check=False)
    if diff_proc.returncode == 0:
        outside = sorted(
            path
            for path in diff_proc.stdout.splitlines()
            if path.strip()
            and not any(_manifest_path_matches(path.strip(), rule) for rule in allowed)
        )
        if outside:
            return {
                "check": "scope",
                "reason": "G changes outside manifest allowed paths: " + ", ".join(outside),
                "evidence": json.dumps(outside),
            }
    proof = verify_lineage(
        repo,
        run_id,
        task_id,
        attempt,
        g_sha,
        events,
        issue_number=task.issue_number,
        ac_refs=_combined_provenance(task),
    )
    if not proof.g_trailers_valid:
        return {"check": "lineage", "reason": "G trailers do not match the immutable R lineage"}
    if diff_proc.returncode == 0:
        full = git(Path(repo), "diff", base_sha, g_sha, check=False)
        if full.returncode == 0 and any(
            ln.startswith("+") and not ln.startswith("+++") and _SECRET_PATTERN.search(ln)
            for ln in full.stdout.splitlines()
        ):
            return {"check": "secret", "reason": "G adds a secret-shaped added line"}
    if not (trailers.get("Tracks-AC") or "").strip():
        return {"check": "provenance", "reason": "G has no Tracks-AC provenance trailer"}
    return None

def _task_review_failure(
    repo, run_id, events, task, g_sha, base_sha, trailers, allowed, task_id, attempt
) -> dict | None:
    # B84 (#84): g_sha=None means no-change review (green.no_change) — no new G
    # exists, so scope/lineage/secret/provenance checks are vacuous. Only the
    # budget check below runs.
    failure = _green_diff_checks(
        repo, run_id, task_id, attempt, g_sha, base_sha, events, allowed, trailers, task
    )
    if failure is not None:
        return failure
    # Fix F (run 01KZTHE7 T-013, 2026-08-16): FR-11 `trac retry` resets the
    # attempt budget, so only verdict.failed events recorded after the last
    # human.retry count against the task budget. Pre-retry failures are
    # superseded - this run's history (56 retries, 31 verdict.failed) would
    # otherwise permanently fail-closed every TASK_REVIEW.
    cutoff = max(
        (ev["seq"] for ev in events if ev["type"] == "human.retry"),
        default=0,
    )
    if sum(ev["type"] == "verdict.failed" and ev["seq"] > cutoff for ev in events) > task.budget:
        return {
            "check": "budget",
            "reason": f"verdict.failed count exceeds task budget {task.budget}",
        }
    return None

_M_IMPL_RED_CLASSIFICATIONS = frozenset({"assertion_failure", "symbol_missing"})

def _red_classification_label(value: object) -> str:
    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "unknown"
    value = value.strip()
    return value or "missing"

_RED_CLASSIFY_PATTERN = re.compile(r"classify_red\s*->\s*(assertion_failure|symbol_missing)")

# Natural-language RED summaries (T-016 attempt 2, 2026-08-16): Devon
# describes failing assertions without the classify_red token. Infer the
# two legal classifications from failure keywords; only `result: "fail"`
# commands are considered (passing guards are not RED evidence). Keep it
# conservative: assembly errors (collection/import/fixture) never infer.
_RED_ASSERTION_PATTERN = re.compile(
    r"\bassert\b|AssertionError|assertion failure|assertion_failure", re.IGNORECASE
)

_RED_SYMBOL_PATTERN = re.compile(
    r"ModuleNotFoundError|ImportError|NameError|AttributeError|"
    r"symbol_missing|cannot import|has no attribute",
)

_RED_ASSEMBLY_ERROR_PATTERN = re.compile(
    r"collection error|ERROR collecting|FixtureLookupError|"
    r"SyntaxError|ModuleNotFoundError|ImportError",
    re.IGNORECASE,
)

def _red_inference_from_command(command: object) -> list[str]:
    """Classifications inferable from one command entry (token first,
    then keyword inference on fail-only natural-language summaries)."""
    if not isinstance(command, dict):
        return []
    summary = command.get("output_summary")
    if not isinstance(summary, str):
        return []
    tokens = _RED_CLASSIFY_PATTERN.findall(summary)
    if tokens:
        return tokens
    if command.get("result") != "fail":
        return []
    if _RED_ASSEMBLY_ERROR_PATTERN.search(summary):
        return []
    if _RED_SYMBOL_PATTERN.search(summary):
        return ["symbol_missing"]
    if _RED_ASSERTION_PATTERN.search(summary):
        return ["assertion_failure"]
    return []

def _m_impl_red_classifications(outcome: dict) -> tuple[list[str], bool]:
    results = outcome.get("results")
    if isinstance(results, list) and results:
        classifications = [
            _red_classification_label(
                result.get("classification") if isinstance(result, dict) else None
            )
            for result in results
        ]
        return classifications, True
    # Fail-closed fallback: Devon RED outcomes sometimes omit `results`.
    # Infer classifications from `commands[*].output_summary`, which always
    # carries a `classify_red -> <legal|illegal>` token. Only the two legal
    # classifications are matched - stub/illegal tokens never infer.
    commands = outcome.get("commands")
    if not isinstance(commands, list):
        return ["missing"], False
    inferred: list[str] = []
    for command in commands:
        inferred.extend(_red_inference_from_command(command))
    if not inferred:
        return ["missing"], False
    return inferred, True

def _m_impl_red_classification_error(outcome: dict) -> tuple[str, str] | None:
    """Return deterministic failure evidence for an invalid M-IMPL Red."""
    classifications, has_results = _m_impl_red_classifications(outcome)
    verdict_present = "verdict" in outcome
    verdict = _red_classification_label(outcome.get("verdict")) if verdict_present else "missing"
    evidence = json.dumps(
        {"classifications": sorted(classifications), "verdict": verdict},
        sort_keys=True,
        separators=(",", ":"),
    )
    if not has_results:
        return "M-IMPL RED classifications are missing", evidence
    illegal = sorted(set(classifications) - _M_IMPL_RED_CLASSIFICATIONS)
    if illegal:
        return "M-IMPL RED contains illegal classification: " + ",".join(illegal), evidence
    if len(set(classifications)) != 1:
        return "M-IMPL RED classifications are mixed", evidence
    if verdict_present and verdict not in _M_IMPL_RED_CLASSIFICATIONS:
        return "M-IMPL RED contains an illegal verdict: " + verdict, evidence
    if verdict_present and verdict != classifications[0]:
        return "M-IMPL RED verdict does not match classification", evidence
    return None


class MImplRuntimeMixin(
    MImplAnchorMixin,
    MImplLedgerMixin,
    MImplIslandsMixin,
    MImplFullChainMixin,
    MImplTestOpsMixin,
):
    """M-IMPL assignment, graph, island, and RGR command handlers."""

    def _rebuild_task_log_projection(self) -> None:
        """Compatibility seam; tasks.md is generated with tasks.json."""

    def _lookup_task(self, task_id: str):
        state = self.store.state(self.run_id)
        for raw in state.task_refs:
            if raw.get("task_id") == task_id:
                return self._task_node(raw)
        if state.current_task_metadata and state.current_task_metadata.get("task_id") == task_id:
            return self._task_node(state.current_task_metadata)
        tasks_path = self._vdir() / "tasks.json"
        if not tasks_path.exists():
            return None
        tasks, err = parse_tasks_json(tasks_path.read_text(encoding="utf-8"))
        if err is not None:
            return None
        return next((t for t in tasks if t.task_id == task_id), None)

    @staticmethod
    def _task_node(raw: dict) -> TaskNode:
        # B50 (#65): raw dicts arrive from two eras -- schema-2 payloads carry
        # the explicit unit_refs/acceptance_refs split, legacy event payloads
        # only the monolithic test_refs (layer-routed by path prefix, the
        # verified 196cbc9 convention: tests/unit/ -> RED obligation,
        # integration -> acceptance anchor). A raw with both split fields
        # empty but a non-empty test_refs is a legacy direct construction.
        test_refs = tuple(str(ref) for ref in (raw.get("test_refs") or ()))
        unit_refs = tuple(raw.get("unit_refs") or ())
        acceptance_refs = tuple(raw.get("acceptance_refs") or ())
        if not unit_refs and not acceptance_refs and test_refs:
            unit_refs = tuple(r for r in test_refs if r.startswith("tests/unit/"))
            acceptance_refs = tuple(r for r in test_refs if not r.startswith("tests/unit/"))
        # B94 deferred anchors (optional, default empty; integration flag)
        deferred_raw = raw.get("deferred_refs") or ()
        deferred_refs = tuple(
            str(r) for r in deferred_raw if isinstance(r, str)
        )
        integration = bool(raw.get("integration", False))
        # #129 debt ledger (optional, default empty; pure annotation)
        debt_raw = raw.get("debt") or ()
        debt = tuple(dict(item) for item in debt_raw if isinstance(item, dict))
        return TaskNode(
            task_id=raw["task_id"],
            issue_number=raw["issue_number"],
            description=raw["description"],
            ac_refs=tuple(raw["ac_refs"]),
            fr_refs=tuple(raw["fr_refs"]),
            if_ids=tuple(raw["if_ids"]),
            test_refs=tuple(unit_refs) + tuple(acceptance_refs) or test_refs,
            scope_boundary=raw["scope_boundary"],
            depends_on=tuple(raw["depends_on"]),
            batch=raw["batch"],
            parallel=raw["parallel"],
            budget=raw["budget"],
            unit_refs=unit_refs,
            acceptance_refs=acceptance_refs,
            schema=int(raw.get("schema") or 1),
            deferred_refs=deferred_refs,
            integration=integration,
            debt=debt,
        )

    def _current_manifest(self) -> dict | None:
        state = self.store.state(self.run_id)
        if state.current_manifest is not None:
            return dict(state.current_manifest)
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "task.started":
                manifest = ev.payload.get("manifest")
                return dict(manifest) if isinstance(manifest, dict) else None
        return None

    def _last_devon_verdict(self) -> str | None:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "outcome.received" and ev.payload.get("role") == "devon":
                return ev.payload.get("verdict")
        return None

    def _last_devon_outcome(self) -> dict | None:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "outcome.received" and ev.payload.get("role") == "devon":
                return ev.payload
        return None

    def _frozen_test_paths(self) -> list[str]:
        try:
            contract = load_contract(self.repo)
        except ContractError:
            return []
        paths: list[str] = []
        for section in (contract.integration, contract.e2e):
            if section is None:
                continue
            paths.extend(section.paths)
        return sorted(set(paths))

    def _approval_digest(self) -> str:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "approval.recorded":
                return ev.payload.get("digest", "")
            if ev.type == "baseline.inherited":
                # Hotfixes consume the approved target-version baseline rather
                # than recreating an approval in their delta directory.
                return str(ev.payload.get("baseline_digest") or "")
        return ""

    def _issue_evidence(self) -> str:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "issues.created":
                mapping = ev.payload.get("mapping", {})
                return json.dumps(
                    mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            if ev.type == "hotfix.requested":
                issue = ev.payload.get("issue")
                if issue is not None:
                    return json.dumps({"hotfix_issue": issue}, separators=(",", ":"))
        return ""

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

    def _retry_cutoff_seq(self) -> int:
        # FR-11: `trac retry` resets the attempt budget, so a fresh attempt
        # number N after a retry is a *different* attempt from an attempt N
        # recorded before it. Guards that scan prior verdict.failed /
        # committed events must only consider events strictly after the last
        # human.retry, otherwise the stale pre-retry verdict (same task,
        # same attempt number) false-positives the fresh attempt forever
        # (run 01KZTHE7 T-013: seq 937/942 blocked post-retry attempt 1).
        cutoff = 0
        for ev in self.store.events(self.run_id):
            if ev.type == "human.retry":
                cutoff = ev.seq
        return cutoff

    def _m_impl_event_recorded(
        self,
        event_type: str,
        task_id: str,
        attempt: int,
        min_seq: int | None = None,
    ) -> bool:
        cutoff = self._retry_cutoff_seq()
        if min_seq is not None:
            # B90 (#91): an additional task-scoped staleness floor on top of
            # the retry cutoff (see _last_task_outcome_seq).
            cutoff = max(cutoff, min_seq)
        return any(
            ev.type == event_type
            and ev.seq > cutoff
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("attempt") == attempt
            for ev in self.store.events(self.run_id)
        )

    def _last_task_outcome_seq(self, task_id: str) -> int:
        """B90 (#91): seq of the task's latest dispatch outcome (Devon or
        Prism), 0 when none. A red.checkpointed recorded BEFORE the current
        round's outcome belongs to an earlier review round -- it cannot
        satisfy the live RED_CHECKPOINT wait, so it must not feed the
        idempotency guard. Operator finding (2026-08-27, run 01M0S0FQ
        T-013): the guard keyed on (task_id, attempt) alone livelocked
        after a Prism revise round because _red_ref_free_attempt (B54/#70)
        had inflated the earlier checkpoint's recorded attempt past the
        machine's counter (orphan pre-replan ref held slot 2, so the event
        logged attempt=3 while current_attempt+1 later equalled 3 again);
        the guard silently returned, decide() re-issued checkpoint_red 20x,
        and only the B86/B88 stall breaker stopped the loop. Mirrors the
        #86 state-aware discriminator already present in commit_green's
        guard (recorded + green_committed)."""
        seq = 0
        for ev in self.store.events(self.run_id):
            if ev.type == "outcome.received" and ev.task_id == task_id:
                seq = max(seq, ev.seq)
        return seq

    def _do_checkpoint_red(self, cmd, state, task_id, reconcile):
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        if self._m_impl_event_recorded(
            "red.checkpointed",
            task_id,
            attempt,
            # B90 (#91): only an event recorded after this round's dispatch
            # outcome is a true duplicate (crash-replay double-fire); an
            # earlier-round checkpoint is stale and must be re-issued on a
            # fresh ref slot.
            min_seq=self._last_task_outcome_seq(task_id),
        ):
            self._rebuild_task_log_projection()
            return
        reason, diff = self._validated_diff("red", state)
        if reason is not None:
            self._emit_gate_failure(
                cmd,
                check="red_invalid",
                reason=reason,
                evidence="backend Devon outcome",
                task_id=task_id,
                attempt=attempt,
            )
            self._rebuild_task_log_projection()
            return
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        attempt = self._red_ref_free_attempt(task_id, attempt)
        r = create_red_ref(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=attempt,
            test_diff=diff,
            base_sha=base_sha,
        )
        self._emit(
            "red.checkpointed",
            self._red_checkpoint_payload(task_id, attempt, r),
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _red_ref_free_attempt(self, task_id: str, attempt: int) -> int:
        """Allocate the first free immutable-ref attempt slot >= attempt.

        Operator finding (2026-08-24, run 01M0S0FQ): a rollback through
        M-DESIGN re-approval abandons an M-IMPL cycle whose RGR refs
        (refs/trac/rgr/{run}/{task}/{N}/red) outlive the cycle -- the fresh
        residency restarts task attempts at 1 and would collide with the
        orphaned ref (create_red_ref raises; the checkpoint crashed).

        B54 (#70): ANY taken slot is skipped -- orphan (abandoned residency)
        or live (checkpointed earlier in THIS residency). The old walk raised
        on a live slot, crashing the framework's own same-residency
        re-checkpoint flows: a Prism red_defect retry re-runs Devon and
        re-checkpoints the same task (run 01M0S0FQ T-001: slot 4 live ->
        second checkpoint crashed trac run), and a human.retry round moves
        the idempotency guard's cutoff past the prior checkpoint the same
        way. R immutability demands a fresh slot for a fresh checkpoint;
        the exact-duplicate double-fire (no retry in between) is already
        caught by the guard in _do_checkpoint_red, so the raise protected
        no real scenario. The event records the allocated attempt, keeping
        history and refs consistent."""
        slot = attempt
        for _ in range(50):
            ref = f"refs/trac/rgr/{self.run_id}/{task_id}/{slot}/red"
            if git(self.repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
                return slot
            slot += 1
        raise TestSelectError(
            f"no free R ref attempt slot for task {task_id} within 50 of {attempt}"
        )

    def _red_checkpoint_payload(self, task_id: str, attempt: int, r) -> dict:
        """``red.checkpointed`` payload (interfaces §4a, AC-FR0245-01): the
        RGR trailer block rides the R checkpoint alongside the green commit
        trailers, so the hotfix issue provenance is auditable on fix/{issue}.
        The checkpoint itself stays valid when the task identity cannot be
        resolved; only the trailer block is then omitted (audit enrichment,
        never a new fail path for R creation)."""
        payload = {"ref": r.ref, "r_sha": r.sha, "task_id": task_id, "attempt": attempt}
        task = self._lookup_task(task_id)
        issue_number = self.store.state(self.run_id).hotfix_issue if task is not None else None
        issue_number = issue_number or (task.issue_number if task is not None else None)
        if task is not None and issue_number:
            payload["trailers"] = {
                "Tracks-Task": task_id,
                "Tracks-Attempt": str(attempt),
                "Tracks-R": r.sha,
                "Tracks-Issue": str(issue_number),
                "Tracks-AC": ",".join(_combined_provenance(task)),
            }
        return payload

    def _rebaseline_red_family(self, task_id: str, commit_sha: str, command_id: str) -> None:
        """B91 follow-up (re-baseline): freeze a sanctioned mid-M-IMPL Shield
        test-fix commit as a NEW immutable R slot in the task's red.checkpointed
        family (2026-08-27, run 01M0S0FQ T-013 post-mortem).

        ``test_defect`` rounds are the system's designed channel for catching
        M-TEST-stage test defects during Devon's cycle (four-way DIAGNOSE ->
        Shield SHIELD_FIX -> ``test.committed``, SM-01.14 re-points the
        regression baseline). Before this, the fix commit never entered the R
        family, so the G lineage anchor and the regression baseline diverged:
        pre-B91 the G bound the fix commit (never provable); post-B91 the G
        bound the ORIGINAL slot -- honest only while the fix leaves the frozen
        test bodies untouched, an over-certification the moment it edits one.
        Adopting the fix commit as a fresh slot (``red.checkpointed`` with
        ``sanction: shield_fix``) reunifies both semantics: the trailer names
        exactly the frozen tree that gated the G, and B91's exact-match
        resolution picks the new slot up automatically. The kernel projection
        treats a sanctioned checkpoint as a pure re-anchor: it must NOT
        re-enter the RED review substate mid-cycle.

        No-op (fail-closed by omission) when the task has no checkpointed RED
        at all: a family that never opened is not ours to open from a fix
        commit; B91's no-checkpoint lineage guard still governs.
        """
        has_family = any(
            ev.type == "red.checkpointed" and ev.payload.get("task_id") == task_id
            for ev in self.store.events(self.run_id)
        )
        if not has_family:
            return
        # Idempotent replay: the fix commit already sits in the family (a
        # crashed/retried shield round must not fork duplicate slots).
        already_adopted = any(
            ev.type == "red.checkpointed"
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("r_sha") == commit_sha
            for ev in self.store.events(self.run_id)
        )
        if already_adopted:
            return
        attempt = self._red_ref_free_attempt(task_id, 1)
        r = adopt_red_ref(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=attempt,
            sha=commit_sha,
        )
        payload = self._red_checkpoint_payload(task_id, attempt, r)
        payload["sanction"] = "shield_fix"
        self._emit(
            "red.checkpointed",
            payload,
            command_id=command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _validated_diff(self, phase: str, state: State) -> tuple[str | None, str | None]:
        reason = self._devon_evidence_error(phase, state)
        outcome = self._last_devon_outcome()
        diff = outcome.get("diff_ref") if outcome else None
        if reason is None and not isinstance(diff, str):
            # Fail-closed fallback: Devon outcomes sometimes omit `diff_ref`.
            # Reconstruct it from the working tree using `changed_paths`.
            # B58 (#74): GREEN must reconstruct from the union of every green
            # outcome of the current RGR cycle -- impl_defect re-dispatches
            # report only their own delta, so the last outcome alone
            # under-captures the cycle's impl diff (run 01M0S0FQ T-001:
            # dispatch 1 changed tracks/project.py, dispatch 3 changed
            # tracks/adapters/base.py, G captured only base.py and the loader
            # impl never reached any commit).
            if phase == "green":
                union = self._green_cycle_changed_paths(state.current_task_id)
                if union:
                    outcome = {**(outcome or {}), "changed_paths": union}
            generated = self._generate_diff_from_changed_paths(outcome)
            if generated is None:
                return f"Devon {phase.upper()} outcome has no captured diff_ref", diff
            diff = generated
        if reason is None and not diff.strip():
            return f"Devon {phase.upper()} captured diff is empty", diff
        return reason, diff

    def _green_cycle_changed_paths(self, task_id: str | None = None) -> list[str] | None:
        """B58 (#74): changed_paths union across every devon GREEN outcome of
        the current RGR cycle (events after the last red.checkpointed; that
        ref bounds the lineage the eventual G binds -- B56). The union only
        widens the ``git diff -- <paths>`` filter of the working-tree
        reconstruction: content still comes from the real tree, so a path a
        later dispatch reverted contributes no diff lines. A sanctioned
        Shield-fix re-baseline (sanction=shield_fix, B91 follow-up) does NOT
        bound the union -- the impl cycle continues through it; only a fresh
        task-starting checkpoint does.

        Prism OOB A02: task_id filtering makes the task boundary explicit
        (task.started/red.checkpointed already bound it implicitly); outcomes
        without a task_id are legacy-shape and stay included."""
        paths: set[str] = set()
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "red.checkpointed" and ev.payload.get("sanction") != "shield_fix":
                break
            if ev.type == "task.started":
                break
            if (
                ev.type == "outcome.received"
                and ev.payload.get("role") == "devon"
                and ev.payload.get("phase") in (None, "green")
                and ev.payload.get("task_id") in (None, task_id)
            ):
                changed = ev.payload.get("changed_paths")
                if isinstance(changed, list):
                    paths.update(p for p in changed if isinstance(p, str) and p)
        return sorted(paths) or None

    def _generate_diff_from_changed_paths(self, outcome: dict | None) -> str | None:
        if not outcome:
            return None
        changed = outcome.get("changed_paths")
        if not isinstance(changed, list) or not changed:
            return None
        existing = [
            str(self.repo / path)
            for path in changed
            if isinstance(path, str) and path and (self.repo / path).exists()
        ]
        if not existing:
            return None
        # `git add -N` registers intent-to-add without staging content, so
        # untracked new files surface in `git diff` as new-file diffs (which
        # is exactly what `diff_ref` should be). Partial failure is fine: the
        # command still succeeds for tracked modified files.
        git(self.repo, "add", "-N", "--", *existing, check=False)
        diff = git(self.repo, "diff", "--", *existing).stdout
        return diff or None

    def _emit_green_no_change(self, cmd, state, task_id, diff, reconcile) -> bool:
        """B38 (#39): if the GREEN evidence is structurally valid but the captured
        worktree diff is empty and Devon declared an explicit no_change_reason,
        emit green.no_change (idempotent on reconcile). Returns True when the
        no-change path was taken (skipping the commit)."""
        outcome = self._last_devon_outcome() or {}
        if (
            diff
            or not outcome.get("no_change_reason")
            or self._devon_evidence_error("green", state) is not None
        ):
            return False
        if reconcile and any(
            e.type == "green.no_change" and e.command_id == cmd.command_id
            for e in self.store.events(self.run_id)
        ):
            self._rebuild_task_log_projection()
            return True
        self._emit(
            "green.no_change",
            {"task_id": task_id, "reason": outcome["no_change_reason"]},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()
        return True

    def _attempt_impl_defect_recorded(self, task_id: str, attempt: int, cutoff: int) -> bool:
        return any(
            ev.type == "verdict.failed"
            and ev.payload.get("check") == "impl_defect"
            # Exact task match only: the old `in (None, task_id)` amnesty
            # let task-less legacy events (DIAGNOSE verdicts before 2026-08-15
            # carried no task_id) poison every task's commit after an attempt
            # reset - run 01KZTHE7 T-013's green commit was blocked by T-008's
            # stale diagnosis verdict (seq 890) hours later.
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("attempt") == attempt
            # Retry cutoff: pre-retry verdicts for the same attempt number
            # describe a superseded attempt (FR-11 budget reset) and must
            # not block the post-retry fresh attempt.
            and ev.seq > cutoff
            for ev in self.store.events(self.run_id)
        )

    def _green_commit_lineage(self, state, task_id: str, attempt: int) -> tuple:
        """Resolve the G-commit lineage inputs for this attempt.

        Returns ``(failure_payload, task, r_sha, b_sha, green_base)``; when
        ``failure_payload`` is not None the commit is blocked and only the
        payload fields up to the failure are meaningful.
        """
        task = self._lookup_task(task_id)
        if task is None or not state.r_tree_identity:
            return (
                {
                    "check": "impl_defect",
                    "reason": (
                        "green commit lacks task_id or R lineage identity "
                        "(check Devon pre/post identity trailers)"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                },
                None,
                "",
                None,
                None,
            )
        r_sha = self._r_lineage_r_sha(task_id, state.r_tree_identity)
        if not r_sha:
            # B91 (#92): no checkpoint family exists at all -- the original
            # fail-closed block below still fires via the empty-identity
            # path only when r_tree_identity is also empty; a non-empty
            # identity with NO recorded checkpoints is an inconsistent
            # lineage, fail closed the same way.
            return (
                {
                    "check": "impl_defect",
                    "reason": (
                        "green commit R lineage unresolvable: r_tree_identity "
                        "matches no red.checkpointed for the task"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                },
                None,
                "",
                None,
                None,
            )
        # FR-0120 replay-safe base: B is derived from the immutable R commit's
        # parent (never blindly the current HEAD), so a crash after the branch
        # update but before green.committed reconciles to the same G.
        b_sha = red_base_sha(str(self.repo), r_sha)
        if b_sha is None:
            return (
                {
                    "check": "impl_defect",
                    "reason": "green lineage base B unresolvable from R",
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": f"r_sha={r_sha}",
                },
                task,
                r_sha,
                None,
                None,
            )
        green_base = self._green_commit_base(b_sha)
        if green_base is None:
            return (
                {
                    "check": "impl_defect",
                    "reason": (
                        "green lineage violation: branch HEAD diverged from "
                        "base B (not a descendant; unknown work on HEAD)"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": (
                        f"b_sha={b_sha} head={git(self.repo, 'rev-parse', 'HEAD').stdout.strip()}"
                    ),
                },
                task,
                r_sha,
                b_sha,
                None,
            )
        return None, task, r_sha, b_sha, green_base

    def _green_gate_evidence(self, task_id: str) -> dict:
        return next(
            (
                ev.payload
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "verdict.passed"
                and ev.payload.get("check") == "green"
                and ev.payload.get("task_id") == task_id
            ),
            {},
        )

    def _r_lineage_r_sha(self, task_id: str, identity: str | None) -> str | None:
        """B91 (#92): resolve the TRUE R ref sha for the G lineage.

        ``state.r_tree_identity`` carries two semantics: the GREEN regression
        gate's diff baseline and the G commit's R lineage trailer source.
        SM-01.14 (_on_test_committed) deliberately re-points it at the
        runtime-committed Shield fix so sanctioned test fixes do not read as
        drift -- after a test_defect round the identity is the fix COMMIT,
        not an R ref. Binding Tracks-R to it produces a G that no
        verify_lineage can ever validate (run 01M0S0FQ T-013, 2026-08-27:
        Tracks-R=5b72700 shield commit vs slot-4 ref d826d34 -> TASK_REVIEW
        lineage rollback). When the identity matches no recorded checkpoint
        for the task, fall back to the LATEST red.checkpointed r_sha; the
        immutable R family is the only valid lineage anchor."""
        if not identity:
            return None
        latest = None
        for ev in self.store.events(self.run_id):
            if (
                ev.type == "red.checkpointed"
                and ev.payload.get("task_id") == task_id
                and isinstance(ev.payload.get("r_sha"), str)
            ):
                if ev.payload.get("r_sha") == identity:
                    return identity
                latest = ev.payload["r_sha"]
        return latest

    def _r_lineage_attempt(self, task_id: str, r_sha: str | None, attempt: int) -> int:
        """B56 (#72): the G lineage attempt is the R ref slot allocated at
        checkpoint time, not the logical attempt counter.

        B54's first-free-slot walk lets the immutable R ref live at a slot
        HIGHER than the logical attempt (run 01M0S0FQ T-001: logical attempt
        3 checkpointed into slot 5 after the abandoned cycle and the Prism
        red_defect retry occupied 3-4). verify_lineage resolves
        refs/trac/rgr/{run}/{task}/{attempt}/red by the attempt recorded in
        green.committed, so G's Tracks-Attempt must be that slot; a G built
        on the logical attempt binds a slot it does not live in and parks
        TASK_REVIEW lineage. The latest red.checkpointed matching
        (task, r_sha) is authoritative; the logical attempt is only the
        fallback when no checkpoint matches."""
        if not r_sha:
            return attempt
        for ev in reversed(list(self.store.events(self.run_id))):
            if (
                ev.type == "red.checkpointed"
                and ev.payload.get("task_id") == task_id
                and ev.payload.get("r_sha") == r_sha
            ):
                recorded = ev.payload.get("attempt")
                if isinstance(recorded, int):
                    return recorded
        return attempt

    def _do_commit_green(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        cutoff = self._retry_cutoff_seq()
        if self._attempt_impl_defect_recorded(task_id, attempt, cutoff):
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason="Runtime gate already failed for this attempt",
                evidence="prior verdict.failed(impl_defect)",
                task_id=task_id,
                attempt=attempt,
            )
            self._rebuild_task_log_projection()
            return
        # B56 (#72): the green.committed idempotency guard keys on the
        # lineage attempt (the R ref slot) -- the same identity the payload
        # and G trailers record -- so a crash-replay reconciles.
        # #86: legal same-R re-commit (PRISM_FINAL revise or DIAGNOSE routed
        # back to GREEN) clears state.green_committed so the guard below
        # distinguishes "already done, reconcile" (recorded + True) from
        # "revised, re-issue" (recorded + False).  A recorded event with
        # green_committed=False lets the same-R-slot green.committed be
        # re-emitted; TASK_REVIEW reads by task_id (most recent seq) per #84.
        lineage_attempt = self._r_lineage_attempt(
            task_id,
            # B91 (#92): resolve through the checkpoint family so a Shield
            # fix commit re-pointed r_tree_identity (SM-01.14) cannot leak
            # the fix sha into the dedup key; matches the r_sha the G will
            # actually carry.
            self._r_lineage_r_sha(task_id, state.r_tree_identity),
            attempt,
        )
        if (
            self._m_impl_event_recorded("green.committed", task_id, lineage_attempt)
            and state.green_committed
        ):
            self._rebuild_task_log_projection()
            return
        reason, diff = self._validated_diff("green", state)
        if reason is not None:
            if self._emit_green_no_change(cmd, state, task_id, diff, reconcile):
                return
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason=reason,
                evidence="backend Devon outcome",
                task_id=task_id,
                attempt=attempt,
            )
            self._rebuild_task_log_projection()
            return
        blocker, task, r_sha, b_sha, green_base = self._green_commit_lineage(
            state, task_id, attempt
        )
        if blocker is not None:
            self._emit(
                "verdict.failed",
                blocker,
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        g = create_green_commit(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=lineage_attempt,
            impl_diff=diff,
            base_sha=green_base,
            r_sha=r_sha,
            issue_number=state.hotfix_issue or task.issue_number,
            ac_refs=_combined_provenance(task),
        )
        materialized, materialize_reason = self._materialize_green_commit(
            g.sha,
            green_base,
        )
        if not materialized:
            self._emit(
                "verdict.failed",
                {
                    "check": "impl_defect",
                    "reason": materialize_reason,
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": f"g_sha={g.sha} b_sha={b_sha}",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        # green.committed is emitted only after the checked-out release branch
        # and worktree actually contain G (idempotent replay emits no duplicate).
        green_evidence = self._green_gate_evidence(task_id)
        green_payload = {
            "g_sha": g.sha,
            "task_id": task_id,
            "attempt": lineage_attempt,
            "r_sha": r_sha,
            "base_sha": g.parent,
            "trailers": g.trailers,
            "evidence_ids": list(green_evidence.get("evidence_ids") or []),
            "identity_basis": dict(green_evidence.get("identity_basis") or {}),
        }
        if green_evidence.get("snapshot_ref"):
            green_payload["snapshot_ref"] = str(green_evidence["snapshot_ref"])
        self._emit(
            "green.committed",
            green_payload,
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _green_commit_base(self, b_sha: str) -> str | None:
        """Resolve the base for the formal G commit (run 01KZTHE7 T-017).

        Returns the sha G should be parented on:
        - HEAD == B: the fast-forward case, G.parent = B.
        - HEAD is a DESCENDANT of B: legitimate post-B commits landed on the
          branch (the runtime's own SHIELD_FIX result_checkpoint commit, or
          operator runtime-fix commits). G is then re-based onto HEAD — the
          impl diff replays on top; RGR semantics stay intact (R is recorded
          as a trailer, B lineage is preserved through HEAD).
        - otherwise (diverged / unrelated work): None -> fail closed.
        """
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if head == b_sha:
            return b_sha
        # --is-ancestor signals its verdict via exit code (0=yes, 1=no), so
        # check must be off; any other failure also lands on the fail-closed
        # path below.
        anc = git(self.repo, "merge-base", "--is-ancestor", b_sha, "HEAD", check=False)
        if anc.returncode == 0:
            return head
        return None

    def _materialize_green_commit(
        self,
        g_sha: str,
        b_sha: str,
    ) -> tuple[bool, str | None]:
        """Materialize the formal G commit onto the checked-out release branch.

        Safe compare-and-set / fast-forward semantics only — unrelated work is
        never reset or overwritten:

        - ``HEAD == G``: already materialized -> idempotent success (replay
          after a crash between branch update and green.committed).
        - ``HEAD == B``: fast-forward the branch and working tree to G (G's
          parent is exactly B). Uncommitted dirty paths outside G's diff
          survive the fast-forward byte-for-byte (B47).
        - any other ``HEAD``: fail closed with a lineage reason.

        Returns ``(ok, reason)``; ``ok=False`` carries the fail-closed reason.
        """
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if head == g_sha:
            return True, None
        if head != b_sha:
            return False, (
                "green lineage violation: branch HEAD is neither B nor G "
                f"(HEAD={head[:12]}, B={b_sha[:12]}, G={g_sha[:12]})"
            )
        # B47 (run 01M0AMKV r2): the fast-forward reset must not destroy
        # uncommitted runtime-owned artifacts. tasks.json/tasks.md are
        # written by PLANNING and are never part of G, so a bare
        # ``reset --hard`` reverted them to the stale version committed by
        # a previous cycle (the r1 taskgraph resurrected over the in-flight
        # r2 graph). Preserve every dirty path G does not touch.
        changed_by_g = set(git(self.repo, "diff", "--name-only", b_sha, g_sha).stdout.splitlines())
        dirty: set[str] = set()
        for line in git(self.repo, "status", "--porcelain").stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:]
            if " -> " in path:  # rename entry: keep both ends
                dirty.update(p for p in path.split(" -> ") if p)
            else:
                dirty.add(path)
        preserved: dict[str, bytes] = {}
        for rel in sorted(dirty - changed_by_g):
            p = self.repo / rel
            if p.is_file():
                preserved[rel] = p.read_bytes()
        git(self.repo, "reset", "--hard", g_sha)
        for rel, data in preserved.items():
            (self.repo / rel).write_bytes(data)
        return True, None

    def _validated_green_snapshot(self, green):
        """Load and shape-check the GREEN content identity snapshot blob."""
        snapshot = self._read_runtime_blob(green.payload.get("snapshot_ref"))
        if not isinstance(snapshot, dict):
            raise TestSelectError("GREEN content identity snapshot is malformed")
        rules = snapshot.get("rules")
        previous = snapshot.get("entries")
        templates = snapshot.get("templates")
        if (
            not isinstance(rules, list)
            or not isinstance(previous, dict)
            or not isinstance(templates, dict)
        ):
            raise TestSelectError("GREEN content identity snapshot lacks rules/entries/templates")
        return rules, previous, templates

    def _restore_r_owned_unit_tests(self, current, previous, r_sha: str) -> None:
        for path, identity in previous.items():
            if (
                path.startswith("tests/unit/")
                and current.get(path) == "missing"
                and r_sha
                and self._path_in_tree(r_sha, path)
            ):
                # Formal G intentionally excludes the frozen R commit. The
                # REFACTOR gate injects R separately, so an R-owned unit node
                # absent from main is unchanged, not candidate drift.
                current[path] = identity

    def _green_command_identities(self, basis: dict, templates) -> tuple:
        """Validate the recorded command identity and rebuild it for now.

        Returns (green_command, current_command): the former as recorded on
        the green evidence, the latter extended when contract templates
        drifted since GREEN.
        """
        command = basis.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise TestSelectError("GREEN evidence command identity is malformed")
        contract = load_contract(self.repo)
        sections = {"unit": contract.unit, "integration": contract.integration}
        current_templates = {
            layer: {
                "run_selected": sections[layer].run_selected,
                "cwd": sections[layer].cwd,
            }
            for layer in templates
            if layer in sections
        }
        current_command = tuple(command)
        if current_templates != templates:
            current_command += (
                json.dumps(current_templates, sort_keys=True, separators=(",", ":")),
            )
        return tuple(command), current_command

    def _stale_target_refs(self) -> set[str]:
        return {
            str(target.get("ref"))
            for ev in self.store.events(self.run_id)
            if ev.type == "evidence.staled"
            for target in (ev.payload.get("targets") or [])
            if isinstance(target, dict) and target.get("ref")
        }

    def _green_reuse_state(self, task_id: str, cwd: str):  # pylint: disable=too-many-locals
        """Return (allowed, changed_paths, green_event) from Runtime snapshots."""
        green = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "green.committed" and ev.payload.get("task_id") == task_id
            ),
            None,
        )
        if green is None:
            raise TestSelectError("REFACTOR_GATE lacks green.committed evidence")
        basis = dict(green.payload.get("identity_basis") or {})
        rules, previous, templates = self._validated_green_snapshot(green)
        current = self._task_content_snapshot(Path(cwd), rules)
        r_sha = self.store.state(self.run_id).r_tree_identity or ""
        self._restore_r_owned_unit_tests(current, previous, r_sha)
        current_digest = self._snapshot_digest(current)
        green_command, current_command = self._green_command_identities(basis, templates)
        green_identity = EvidenceIdentity(
            tree=str(basis.get("tree") or ""),
            command=green_command,
            env=str(basis.get("env") or ""),
            selection_id=str(basis.get("selection_id") or ""),
        )
        current_identity = EvidenceIdentity(
            tree=current_digest,
            command=current_command,
            env=self._gate_environment_identity(),
            selection_id=green_identity.selection_id,
        )
        stale_refs = self._stale_target_refs()
        related_refs = {
            green_identity.selection_id,
            *(str(ref) for ref in (green.payload.get("evidence_ids") or []) if ref),
        }
        changed = sorted(
            path for path in set(previous) | set(current) if previous.get(path) != current.get(path)
        )
        return (
            reuse_allowed(green_identity, current_identity, stale_refs & related_refs),
            changed,
            green,
        )

    def _reuse_green_evidence(self, cmd, task_id: str, outcome: dict, green) -> None:
        self._emit(
            "evidence.reused",
            {
                "kind": "green",
                "task_id": task_id,
                "reused_evidence_ids": list(green.payload.get("evidence_ids") or []),
                "identity_basis": dict(green.payload.get("identity_basis") or {}),
                "consumer_gate": "REFACTOR_GATE",
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._emit(
            "refactor.no_change",
            {"task_id": task_id, "reason": outcome.get("no_change_reason")},
            command_id=cmd.command_id,
        )

    def _flag_stale_green_evidence(self, cmd, task_id: str, green, observed_changed) -> None:
        green_basis = dict(green.payload.get("identity_basis") or {})
        self._emit_stale_refs(
            cmd,
            task_id,
            [str(green_basis.get("selection_id") or "")],
            list(green.payload.get("evidence_ids") or []),
            "green_content_identity_changed" if observed_changed else "green_evidence_stale",
        )

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

    def _selected_test_argv(self, test_refs: list, command_id: str, layer: str):
        """Contract ``run_selected`` argv for engine-executed test refs (T-015).

        The executed argv is the verbatim host-contract expansion (the
        anchor_probe/test_select ``load_contract`` pattern): the engine
        injects neither a runner module nor flags. Returns
        ``(argv, result_path, cwd)``; raises ``ContractError`` /
        ``TestSelectError`` which callers route to their fail-closed
        channels. The staged result file is consumed evidence -- callers
        unlink it after the run (FA-4)."""
        contract = load_contract(Path(self.repo))
        section = getattr(contract, layer)
        cwd = Path(self.repo) if section.cwd == "." else Path(self.repo) / section.cwd
        result_path = self._result_staging_path(command_id, f"{layer}_selected")
        argv = list(
            resolve_selected_command(section.run_selected, list(test_refs), str(result_path), cwd)
        )
        return argv, result_path, cwd

    def _run_anchor_suite(self, cmd_argv, run_cwd, result_path) -> tuple[int, str, str]:
        """Run one engine-selected anchor suite; always consume the staging result."""
        try:
            proc = subprocess.run(
                cmd_argv, cwd=run_cwd, capture_output=True, text=True, timeout=1800
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            return 124, str(exc), "anchor run timed out after 1800s"
        finally:
            result_path.unlink(missing_ok=True)

    def _pin_r_ref(self, tid: str, attempt: int) -> tuple[str, str]:
        """Pin the R ref straight to HEAD; return (ref, base_sha)."""
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        ref = f"refs/trac/rgr/{self.run_id}/{tid}/{attempt}/red"
        git(self.repo, "update-ref", ref, base_sha)
        return ref, base_sha

    def _fail_anchor_contract(self, cmd, tid: str, attempt: int, layer: str, exc) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "contract_error",
                "reason": (
                    f"anchor RED run needs the host contract [{layer}].run_selected: {exc}"
                ),
                "task_id": tid,
                "attempt": attempt,
            },
            command_id=cmd.command_id,
            task_id=tid,
        )

    def _do_anchor_red(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        """Runtime-executed RED anchor confirmation for preset-anchor tasks.

        The frozen failing tests ARE the red anchor (no new unit test to
        write): run them expecting red, then pin the R ref straight to the
        base sha - the base tree already contains the anchors - and emit
        red.checkpointed so the normal PRISM_RED -> GREEN flow continues
        (run 01KZTHE7 T-013, 2026-08-15: a Devon RED dispatch here can only
        produce an empty changed_paths evidence the gate rejects).
        """
        tid = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        meta = state.current_task_metadata or {}
        if meta.get("integration"):
            self._do_walk_red(cmd, state, tid, meta)
            return
        test_refs = meta.get("test_refs") or []
        attempt = state.current_attempt + 1
        if not isinstance(test_refs, list) or not test_refs:
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": "preset-anchor task declares no test_refs",
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        try:
            cmd_argv, result_path, run_cwd = self._selected_test_argv(
                test_refs, cmd.command_id or "", "integration"
            )
        except (ContractError, TestSelectError) as exc:
            self._fail_anchor_contract(cmd, tid, attempt, "integration", exc)
            return
        rc, out, err = self._run_anchor_suite(cmd_argv, run_cwd, result_path)
        summary = out.strip().splitlines()[-1] if out.strip() else ""
        if rc == 0:
            # Anchors already green: either the implementation already
            # exists (task-graph drift) or an anchor broke - a human must
            # decide which.
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": (
                        "preset RED anchors unexpectedly pass - anchors must "
                        f"be red before GREEN. {summary}"
                    ),
                    "evidence": out[-2000:],
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            self._rebuild_task_log_projection()
            return
        ref, base_sha = self._pin_r_ref(tid, attempt)
        ref_payload = {
            "failed": failed_summary_lines(out),
            "tail": out[-400:],
        }
        blob = self.store.write_audit_blob(
            {"cmd": cmd_argv, "rc": rc, "stdout": out, "stderr": err}
        )
        if blob:
            ref_payload["log_ref"] = f".tracks/runtime/blobs/{blob}"
        self._emit(
            "red.checkpointed",
            {
                "ref": ref,
                "r_sha": base_sha,
                "task_id": tid,
                "attempt": attempt,
                "anchor": True,
                "evidence": json.dumps(ref_payload, ensure_ascii=False),
            },
            command_id=cmd.command_id,
            task_id=tid,
        )
        self._rebuild_task_log_projection()

    def _do_walk_red(self, cmd, state, tid: str, meta: dict) -> None:
        """Runtime-executed RED for integration tasks (walk the R-tree).

        An integration task carries no new RED unit test to write; its
        unit pins are already delivered. Seal the unit R-tree on site:
        run the unit_refs expecting green, then pin the R ref straight
        to the base sha and emit red.checkpointed so the normal
        PRISM_RED -> GREEN flow continues with a resolvable
        r_tree_identity (run 01M19FJVES7G113RD8QXXY3PQZ T-042:
        GREEN start with no RED checkpoint left GREEN structurally
        unpassable - Devon assignment without r_tree_identity plus
        green commit lineage without R ref).
        """
        attempt = state.current_attempt + 1
        unit_refs = [r for r in (meta.get("unit_refs") or []) if isinstance(r, str)]
        if not unit_refs:
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": "walk_red integration task declares no unit_refs",
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        try:
            cmd_argv, result_path, run_cwd = self._selected_test_argv(
                unit_refs, cmd.command_id or "", "unit"
            )
        except (ContractError, TestSelectError) as exc:
            self._fail_anchor_contract(cmd, tid, attempt, "unit", exc)
            return
        rc, out, err = self._run_anchor_suite(cmd_argv, run_cwd, result_path)
        if rc != 0:
            summary = out.strip().splitlines()[-1] if out.strip() else ""
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": (
                        "walk_red unit pins are not green - regression "
                        f"baseline cannot be sealed. {summary}"
                    ),
                    "evidence": out[-2000:],
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            self._rebuild_task_log_projection()
            return
        ref, base_sha = self._pin_r_ref(tid, attempt)
        ref_payload: dict = {"passed": True, "tail": out[-400:], "walk_red": True}
        blob = self.store.write_audit_blob(
            {"cmd": cmd_argv, "rc": rc, "stdout": out, "stderr": err}
        )
        if blob:
            ref_payload["log_ref"] = f".tracks/runtime/blobs/{blob}"
        self._emit(
            "red.checkpointed",
            {
                "ref": ref,
                "r_sha": base_sha,
                "task_id": tid,
                "attempt": attempt,
                "anchor": True,
                "walk_red": True,
                "evidence": json.dumps(ref_payload, ensure_ascii=False),
            },
            command_id=cmd.command_id,
            task_id=tid,
        )
        self._rebuild_task_log_projection()

    def _do_verify_task(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        """Runtime-executed acceptance for verification-only tasks (§1.0.3).

        User ruling 2026-08-15: acceptance is Runtime work, not agent work. A
        verification-only task has frozen test assets and no
        RED-implementation, so the RGR chain (evidence diff -> red ref ->
        green) structurally cannot apply - run 01KZTHE7 T-001 burned three
        Devon attempts at "RED evidence has no changed paths" before this
        path. The Runtime runs the task's declared test_refs directly; a full
        pass completes the task, a failure re-enters the gate-failure budget
        (3 attempts -> escalation for a human, matching flaky-retry then
        stop semantics).
        """
        tid = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        meta = state.current_task_metadata or {}
        test_refs = meta.get("test_refs") or []
        if not isinstance(test_refs, list) or not test_refs:
            self._emit(
                "verdict.failed",
                {
                    "check": "verification_failed",
                    "reason": "verification-only task declares no test_refs",
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        try:
            cmd_argv, result_path, run_cwd = self._selected_test_argv(
                test_refs, cmd.command_id or "", "integration"
            )
        except (ContractError, TestSelectError) as exc:
            self._emit(
                "verdict.failed",
                {
                    "check": "contract_error",
                    "reason": (
                        "verification run needs the host contract "
                        f"[integration].run_selected: {exc}"
                    ),
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        err = ""
        try:
            proc = subprocess.run(
                cmd_argv,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            rc, out, err = 124, str(exc), "verification timed out after 1800s"
        finally:
            result_path.unlink(missing_ok=True)
        summary = out.strip().splitlines()[-1] if out.strip() else ""
        if rc == 0:
            self._emit(
                "task.completed",
                {
                    "task_id": tid,
                    "verification": True,
                    "commands": [
                        {
                            "cmd": " ".join(cmd_argv),
                            "result": "pass",
                            "output_summary": summary,
                        }
                    ],
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
        else:
            # Failure evidence for the fixer relay (user directive
            # 2026-08-15): fixers cannot re-run these suites, so hand them
            # the whole picture - FAILED summary lines, the full-output blob
            # path (--tb=long stacks included), and a short tail inline.
            ref = self.store.write_audit_blob(
                {"cmd": cmd_argv, "rc": rc, "stdout": out, "stderr": err}
            )
            evidence_payload = {
                "failed": failed_summary_lines(out),
                "tail": out[-400:],
            }
            if ref:
                evidence_payload["log_ref"] = f".tracks/runtime/blobs/{ref}"
            self._emit(
                "verdict.failed",
                {
                    "check": "verification_failed",
                    "reason": f"verification test_refs failed rc={rc}: {summary}",
                    "evidence": json.dumps(evidence_payload, ensure_ascii=False),
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
        self._rebuild_task_log_projection()
