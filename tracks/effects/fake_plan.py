"""FakeBackend M-IMPL PLANNING task-graph derivation (flow.md §10).

Extracted from ``fake.py`` for module-size compliance (C0302). Runtime
taskgraph validators stay lazily imported: importing ``tracks.executor`` at
module scope would run ``executor/__init__.py``, which imports
``tracks.effects`` back during import.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracks.effects.fake_shield import _FAILED_TOKENS, _ac_slug, simulated_agent_failure


class FakePlanMixin:
    """Deterministic tasks.json derivation and self-validation."""

    @staticmethod
    def _is_m_impl_planning(role: str, substate: str) -> bool:
        """Archer PLANNING is the only M-IMPL substate the fake simulates."""
        return role == "archer" and substate == "PLANNING"

    def _act_m_impl_planning(self, assignment: dict | None = None) -> dict:
        """M-IMPL PLANNING: derive the implementation task graph from the
        frozen trio + design docs and write ``.tracks/projects/{version}/
        tasks.json`` as the Agent artifact (the Runtime never creates it).

        Required AC ids come from acceptance.md, valid IF ids from
        interfaces.md §5 IF Registry; the graph is one deterministic vertical
        slice per AC. ``archer:PLANNING`` failure tokens (``_FAILED_TOKENS``)
        return ``failed`` without writing a valid artifact. Output is a pure
        function of the doc contents (no clock/random), so the same docs
        yield byte-identical tasks.json.
        """
        token = self.token("archer", "PLANNING", "ok")
        if token in _FAILED_TOKENS:
            return simulated_agent_failure(token, "archer")
        vdir = self._design_vdir()
        tasks_json, error = self._derive_task_graph(vdir, assignment)
        if error is not None:
            return {
                "status": "failed",
                "artifact_ref": None,
                "failure_class": "invalid_taskgraph",
                "audit_evidence": error,
                "self_report": f"archer planning failed: {error}",
            }
        raw = json.dumps(tasks_json, indent=2) + "\n"
        (vdir / "tasks.json").write_text(raw, encoding="utf-8")
        return {
            "status": "done",
            "artifact_ref": str(vdir / "tasks.json"),
            "self_report": f"wrote {len(tasks_json['tasks'])} task(s) to tasks.json",
            "audit_evidence": (
                "task graph derived from acceptance.md ACs and interfaces.md IF registry"
            ),
            # Declared archer:planning replies carry the task graph itself; the
            # Runtime's commit_taskgraph consumer still parses the file, so
            # this is the reply's own machine-readable outcome.
            "tasks": tasks_json["tasks"],
        }

    def _derive_task_graph(
        self, vdir: Path, assignment: dict | None = None
    ) -> tuple[dict | None, str | None]:
        """Build and self-validate the deterministic task graph.

        The Runtime taskgraph validators are imported lazily: importing
        ``tracks.executor`` at module scope would run ``executor/__init__.py``,
        which imports ``tracks.effects`` back into this package during import
        (effects <-> executor cycle). Function-level imports keep the effects
        boundary acyclic.
        """
        validators = self._taskgraph_validators()
        acs, registry, error = self._taskgraph_sources(vdir, assignment, validators)
        if error:
            return None, error
        if not acs:
            return None, "acceptance.md contains no AC ids"
        if not registry:
            return None, "interfaces.md §5 IF Registry is empty"
        data = self._taskgraph_data(acs, registry, validators["requirement_ref"])
        if assignment and assignment.get("hotfix_issue") is not None:
            for task in data.get("tasks", []):
                task["issue_number"] = int(assignment["hotfix_issue"])
        error = self._validate_taskgraph(data, acs, registry, validators)
        return (None, error) if error else (data, None)

    @staticmethod
    def _taskgraph_sources(
        vdir: Path, assignment: dict | None, validators: dict
    ) -> tuple[list[str], list[str], str | None]:
        from tracks.executor.test_tasks import resolve_inherited_baseline_docs

        acc, ifc = resolve_inherited_baseline_docs(vdir / "test-plan.md")
        anchors = {
            str(ref).split("@", 1)[0]
            for ref in (assignment or {}).get("hotfix_anchor_acs", [])
            if ref
        }
        if not anchors and not acc.is_file():
            return [], [], "acceptance.md missing; cannot derive required ACs"
        if not anchors and not ifc.is_file():
            return [], [], "interfaces.md missing; cannot derive IF registry"
        acs, registry = FakePlanMixin._taskgraph_doc_sources(acc, ifc, assignment, validators)
        if not anchors:
            return acs, registry, None
        hotfix_acs, hotfix_registry = FakePlanMixin._hotfix_taskgraph_sources(
            acs, registry, assignment, anchors, acc, vdir / "test-plan.md"
        )
        return hotfix_acs, hotfix_registry, None

    @staticmethod
    def _taskgraph_doc_sources(
        acc: Path, ifc: Path, assignment: dict | None, validators: dict
    ) -> tuple[list[str], list[str]]:
        if acc.is_file() and ifc.is_file():
            acs = sorted(validators["known_ac_ids"](acc.read_text(encoding="utf-8")))
            registry = sorted(
                validators["extract_if_registry"](ifc.read_text(encoding="utf-8")) or []
            )
            return acs, registry
        rows = (assignment or {}).get("test_tasks") or []
        acs = sorted({str(row.get("ac_id") or "") for row in rows if row.get("ac_id")})
        registry = sorted(
            {
                str(if_id)
                for row in rows
                for if_id in (row.get("if_ids") or [])
                if if_id
            }
        )
        return acs, registry

    @staticmethod
    def _hotfix_taskgraph_sources(
        acs: list[str],
        registry: list[str],
        assignment: dict | None,
        anchors: set[str],
        acc: Path,
        plan: Path,
    ) -> tuple[list[str], list[str]]:
        """Anchored hotfix slice of the inherited taskgraph sources.

        Archer PLANNING assignments carry no ``test_tasks`` slice (b92 R3
        card diet: ``_apply_assignment_context_grading`` blanks it), so the
        per-task IF registry cannot be derived from the assignment. Fall
        back to the delta test-plan's own anchored rows (the same
        ``parse_test_tasks`` the Shield WRITE injection uses); the raw
        inherited registry stays the last resort so the M-IMPL planner is
        never failed closed with ``IF Registry is empty``."""
        from tracks.executor.test_tasks import parse_test_tasks

        rows = (assignment or {}).get("test_tasks") or []
        assigned_ifs = {
            str(if_id)
            for row in rows
            for if_id in (row.get("if_ids") or [])
            if if_id
        }
        if not assigned_ifs and plan.is_file():
            assigned_ifs = {
                str(if_id)
                for row in parse_test_tasks(acc, plan)
                for if_id in (row.get("if_ids") or [])
                if if_id
            }
        if not assigned_ifs:
            assigned_ifs = {str(if_id) for if_id in registry if if_id}
        return sorted(set(acs) & anchors), sorted(assigned_ifs)

    @staticmethod
    def _taskgraph_validators() -> dict:
        from tracks.executor.taskgraph import (  # noqa: PLC0415
            _requirement_ref,
            parse_tasks_json,
            validate_ac_coverage,
            validate_dag,
            validate_issue_numbers,
            validate_scope,
            validate_task_structure,
        )
        from tracks.executor.test_tasks import (  # noqa: PLC0415
            _extract_if_registry,
            _known_ac_ids,
        )

        return {
            "requirement_ref": _requirement_ref,
            "parse_tasks_json": parse_tasks_json,
            "validate_ac_coverage": validate_ac_coverage,
            "validate_dag": validate_dag,
            "validate_issue_numbers": validate_issue_numbers,
            "validate_scope": validate_scope,
            "validate_task_structure": validate_task_structure,
            "extract_if_registry": _extract_if_registry,
            "known_ac_ids": _known_ac_ids,
        }

    @staticmethod
    def _taskgraph_data(acs: list[str], registry: list[str], requirement_ref) -> dict:
        tasks = []
        for index, ac_id in enumerate(acs):
            if_id = registry[index % len(registry)]
            slug = _ac_slug(ac_id)
            tasks.append(
                {
                    "task_id": f"T-{index + 1:03d}",
                    "issue_number": index + 1,
                    "description": f"Implement {ac_id} as a vertical slice ({if_id})",
                    "ac_refs": [ac_id],
                    "fr_refs": [requirement_ref(ac_id)],
                    "if_ids": [if_id],
                    "test_refs": [
                        f"tests/unit/test_{slug}.py::test_devon_behavioral_contract"
                    ],
                    "scope_boundary": f"tracks/impl/{slug}.py, tests/unit/test_{slug}.py",
                    "depends_on": [],
                    "batch": "1",
                    "parallel": False,
                    "budget": 3,
                }
            )
        return {"tasks": tasks}

    @staticmethod
    def _validate_taskgraph(
        data: dict, acs: list[str], registry: list[str], validators: dict
    ) -> str | None:
        raw = json.dumps(data, indent=2) + "\n"
        nodes, error = validators["parse_tasks_json"](raw)
        if error is not None:
            return error
        errors = validators["validate_task_structure"](nodes)
        valid, detail = validators["validate_dag"](nodes)
        if not valid:
            errors.append(detail)
        errors.extend(validators["validate_scope"](nodes)[1])
        errors.extend(validators["validate_ac_coverage"](nodes, acs, set(registry))[1])
        errors.extend(validators["validate_issue_numbers"](nodes)[1])
        return "; ".join(errors) if errors else None
