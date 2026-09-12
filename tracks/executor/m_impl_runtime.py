"""M-IMPL runtime aggregator.

The public ``MImplRuntimeMixin`` composes the M-IMPL domain mixins
extracted under :mod:`tracks.executor` (assignment/evidence, taskgraph
ledger, island gates, FULL chain, test ops, GREEN chain, RED diagnosis,
RGR dispatch) while keeping the public ``Executor`` method surface
unchanged.
"""

from __future__ import annotations

import json
import subprocess  # noqa: F401  (test seam: subprocess.run monkeypatching)

from tracks.executor.m_impl_anchor import (
    MImplAnchorMixin,
    _devon_path_scope_error,  # noqa: F401
    _is_evidence_shape_error,  # noqa: F401
)
from tracks.executor.m_impl_diagnose import (
    MImplDiagnoseMixin,
    _m_impl_red_classification_error,  # noqa: F401
    _m_impl_red_classifications,  # noqa: F401
)
from tracks.executor.m_impl_dispatch import (
    MImplDispatchMixin,
    TaskSelectionFailure,  # noqa: F401
)
from tracks.executor.m_impl_full_chain import MImplFullChainMixin
from tracks.executor.m_impl_green import MImplGreenMixin
from tracks.executor.m_impl_islands import (
    MImplIslandsMixin,
    _resolve_island_gate_2,  # noqa: F401
)
from tracks.executor.m_impl_ledger import MImplLedgerMixin
from tracks.executor.m_impl_refactor import MImplRefactorMixin
from tracks.executor.m_impl_testops import MImplTestOpsMixin
from tracks.executor.taskgraph import TaskNode, parse_tasks_json
from tracks.executor.test_select import TestSelectError  # noqa: F401
from tracks.project import ContractError, load_contract


class MImplRuntimeMixin(
    MImplAnchorMixin,
    MImplLedgerMixin,
    MImplIslandsMixin,
    MImplFullChainMixin,
    MImplTestOpsMixin,
    MImplGreenMixin,
    MImplDiagnoseMixin,
    MImplDispatchMixin,
    MImplRefactorMixin,
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
