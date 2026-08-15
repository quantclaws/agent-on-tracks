"""M-IMPL Runtime handlers extracted from :mod:`executor`.

The mixin keeps the public ``Executor`` method surface while isolating the
task-graph, manifest, and RGR implementation from the other stage handlers.
All filesystem and event-store access still goes through the host executor.
"""

# pylint: disable=too-many-lines
# The M-IMPL Runtime handlers all live in a single mixin on the public Executor
# (matching machine.py): splitting would spread the RGR gate/task-graph logic
# across modules without reducing cognitive load.
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path

from tracks.baseline import (
    m_impl_baseline_digest,
    m_impl_baseline_missing,
    m_impl_baseline_summary,
)
from tracks.effects.backend import valid_test_tasks
from tracks.executor.helpers import _commit_if_staged, _short_detail, git
from tracks.executor.quality_gate import execute_gate_command, observation_evidence
from tracks.executor.rgr import (
    create_green_commit,
    create_red_ref,
    red_base_sha,
    verify_lineage,
)
from tracks.executor.taskgraph import (
    TaskNode,
    parse_tasks_json,
    validate_ac_coverage,
    validate_dag,
    validate_island_closure,
    validate_issue_numbers,
    validate_scope,
    validate_task_structure,
)
from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids
from tracks.executor.validate import parse_test_tasks
from tracks.executor.worktree import WorktreeHandle, cleanup_worktree, create_gate_worktree
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK, State
from tracks.project import ContractError, layout_paths, load_contract
from tracks.tasklog import rebuild_task_log

_SECRET_PATTERN = re.compile(r"(sk-|ghp_|gho_|AKIA)[A-Za-z0-9]{16,}")


def _task_review_failure(
    repo, run_id, events, task, g_sha, base_sha, trailers, allowed, task_id, attempt
) -> dict | None:
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
    if sum(ev["type"] == "verdict.failed" for ev in events) > task.budget:
        return {
            "check": "budget",
            "reason": f"verdict.failed count exceeds task budget {task.budget}",
        }
    return None


def _batch_key(batch: str) -> tuple[int, str]:
    try:
        return (0, f"{int(batch):020d}")
    except (TypeError, ValueError):
        return (1, str(batch))


def _manifest_path_matches(path: str, rule: str) -> bool:
    if rule.endswith("/**"):
        root = rule[:-3].rstrip("/")
        return path == root or path.startswith(root + "/")
    return path == rule or path.startswith(rule.rstrip("/") + "/")


_M_IMPL_RED_CLASSIFICATIONS = frozenset({"assertion_failure", "symbol_missing"})


def _red_classification_label(value: object) -> str:
    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "unknown"
    value = value.strip()
    return value or "missing"


def _m_impl_red_classifications(outcome: dict) -> tuple[list[str], bool]:
    results = outcome.get("results")
    if not isinstance(results, list) or not results:
        return ["missing"], False
    classifications = [
        _red_classification_label(
            result.get("classification") if isinstance(result, dict) else None
        )
        for result in results
    ]
    return classifications, True


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


def _combined_provenance(task) -> list[str]:
    """Combine current task ac_refs + fr_refs into the single Tracks-AC
    provenance list: de-duplicated with stable deterministic ordering."""
    return list(dict.fromkeys((*task.ac_refs, *task.fr_refs)))


class MImplRuntimeMixin:
    """M-IMPL assignment, graph, island, and RGR command handlers."""

    def _rebuild_task_log_projection(self) -> None:
        """Refresh the derived task log after a durable M-IMPL boundary."""
        rebuild_task_log(self.store.home, self.store.events(self.run_id))

    def _materialize_m_impl_assignment(
        self,
        state: State,
        params: dict,
        command_id: str,
    ) -> dict:
        self._rebuild_task_log_projection()
        assignment = deepcopy(params.get("assignment") or {})
        task = state.current_task_metadata
        if task is None:
            task = self._materialized_task(state)
        pre_dirty = self._dirty_snapshot()
        self._add_assignment_runtime_fields(
            assignment,
            state,
            params,
            task,
            pre_dirty,
        )
        assignment["test_tasks"] = parse_test_tasks(
            self._vdir() / "acceptance.md",
            self._vdir() / "test-plan.md",
        )
        self._add_assignment_role_fields(assignment, state, params)
        return assignment

    def _materialized_task(self, state: State) -> dict | None:
        if not state.current_task_id:
            return None
        task_node = next(
            (
                self._task_node(raw)
                for raw in state.task_refs
                if raw.get("task_id") == state.current_task_id
            ),
            None,
        )
        return self._task_payload(task_node) if task_node is not None else None

    def _add_assignment_runtime_fields(
        self,
        assignment: dict,
        state: State,
        params: dict,
        task: dict | None,
        pre_dirty: dict[str, str],
    ) -> None:
        role = params.get("role")
        substate = params.get("substate")
        task_id = task.get("task_id") if isinstance(task, dict) else "none"
        manifest = dict(state.current_manifest) if state.current_manifest is not None else None
        result_identity = self._result_identity(
            task_id,
            str(substate or "dispatch").lower(),
            state.current_attempt,
            pre_dirty,
            state.taskgraph_digest or "",
        )
        if manifest is not None:
            manifest.update({"pre_dirty_snapshot": pre_dirty, "result_identity": result_identity})
            if substate in ("GREEN", "REFACTOR", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE"):
                manifest["r_tree_identity"] = state.r_tree_identity
        assignment.update(
            {
                "role": role,
                "substate": substate,
                "task_id": task.get("task_id") if task else None,
                "task": dict(task) if task else None,
                "manifest": manifest,
                "pre_dirty_snapshot": pre_dirty,
                "result_identity": result_identity,
                "commands": {
                    "unit": self._unit_commands(),
                    "test": self._test_commands(),
                    "guard": self._guard_commands(),
                },
            }
        )
        if task:
            for key in ("issue_number", "ac_refs", "fr_refs", "if_ids", "test_refs"):
                value = task.get(key)
                assignment[key] = list(value) if isinstance(value, tuple) else value

    @staticmethod
    def _add_assignment_role_fields(
        assignment: dict,
        state: State,
        params: dict,
    ) -> None:
        role = params.get("role")
        substate = params.get("substate")
        if role == "devon":
            phase = assignment.get("phase")
            assignment["phase"] = phase if phase in ("red", "green", "refactor") else None
            if assignment["phase"] in ("green", "refactor"):
                assignment["r_tree_identity"] = state.r_tree_identity
        elif role == "prism":
            assignment["criteria_pack"] = dict(_M_IMPL_CRITERIA_PACK)
            if substate in ("PRISM_RED", "PRISM_FINAL", "DIAGNOSE"):
                assignment["r_tree_identity"] = state.r_tree_identity
        elif role == "shield" and substate == "WRITE":
            assignment["phase"] = "shield_fix"

    def _invalid_m_impl_assignment(
        self,
        role: str,
        substate: str,
        assignment: dict | None,
    ) -> str | None:
        if role == "devon":
            error = self._invalid_devon_assignment(assignment)
            if error is not None:
                return error
        if (
            role == "shield"
            and substate == "WRITE"
            and not valid_test_tasks((assignment or {}).get("test_tasks"))
        ):
            return (
                "Shield WRITE assignment.test_tasks is invalid: each entry needs "
                "non-empty ac_id, if_ids, and test_paths"
            )
        return None

    @staticmethod
    def _invalid_devon_assignment(assignment: dict | None) -> str | None:
        required = (
            "task_id",
            "if_ids",
            "ac_refs",
            "test_refs",
            "commands",
            "manifest",
            "phase",
            "pre_dirty_snapshot",
            "result_identity",
        )
        data = assignment or {}
        missing = [key for key in required if MImplRuntimeMixin._assignment_key_missing(data, key)]
        phase = data.get("phase")
        if phase in ("green", "refactor") and not data.get("r_tree_identity"):
            missing.append("r_tree_identity")
        if missing:
            return f"Devon assignment missing: {', '.join(missing)}"
        return None

    @staticmethod
    def _assignment_key_missing(assignment: dict, key: str) -> bool:
        value = assignment.get(key)
        required_list = key in ("if_ids", "ac_refs", "test_refs", "commands", "manifest")
        return value is None or (required_list and not value)

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
        return TaskNode(
            task_id=raw["task_id"],
            issue_number=raw["issue_number"],
            description=raw["description"],
            ac_refs=tuple(raw["ac_refs"]),
            fr_refs=tuple(raw["fr_refs"]),
            if_ids=tuple(raw["if_ids"]),
            test_refs=tuple(raw["test_refs"]),
            scope_boundary=raw["scope_boundary"],
            depends_on=tuple(raw["depends_on"]),
            batch=raw["batch"],
            parallel=raw["parallel"],
            budget=raw["budget"],
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

    def _devon_evidence_error(self, phase: str, state: State) -> str | None:
        outcome = self._last_devon_outcome()
        if not outcome or outcome.get("status") != "done":
            return "Devon did not provide a completed outcome"
        error = self._devon_evidence_fields_error(phase, outcome)
        if error is not None:
            return error
        error = self._devon_evidence_change_error(phase, outcome)
        if error is not None:
            return error
        return self._devon_evidence_scope_error(phase, state, outcome)

    @staticmethod
    def _devon_evidence_fields_error(phase: str, outcome: dict) -> str | None:
        required = (
            "phase",
            "changed_paths",
            "commands",
            "manifest_compliance",
            "pre_identity",
            "post_identity",
            "implemented_if_ids",
        )
        missing = [key for key in required if key not in outcome]
        if missing:
            return "Devon evidence missing: " + ", ".join(missing)
        if outcome.get("phase") != phase:
            return f"Devon evidence phase mismatch: expected {phase}"
        if not isinstance(outcome.get("changed_paths"), list):
            return "Devon evidence changed_paths must be a list"
        if any(not isinstance(path, str) or not path.strip() for path in outcome["changed_paths"]):
            return "Devon evidence changed_paths contains an invalid path"
        if not isinstance(outcome.get("commands"), list):
            return "Devon evidence commands must be a list"
        if outcome.get("manifest_compliance") is not True:
            return "Devon manifest_compliance is not true"
        if outcome.get("pre_identity") in (None, "") or outcome.get("post_identity") in (None, ""):
            return "Devon evidence is missing pre/post identity"
        if not isinstance(outcome.get("implemented_if_ids"), list):
            return "Devon evidence implemented_if_ids must be a list"
        if phase in ("green", "refactor") and not outcome.get("r_identity"):
            return "Devon evidence missing r_identity"
        return None

    @staticmethod
    def _devon_evidence_change_error(phase: str, outcome: dict) -> str | None:
        if phase == "red" and not outcome.get("changed_paths"):
            return "Devon RED evidence has no changed paths"
        if phase == "green" and not outcome.get("changed_paths"):
            return "Devon GREEN evidence has no changed paths"
        if (
            phase == "refactor"
            and not outcome.get("changed_paths")
            and not outcome.get("no_change_reason")
        ):
            return "Devon REFACTOR evidence needs changed paths or no_change_reason"
        return None

    def _devon_evidence_scope_error(
        self,
        phase: str,
        state: State,
        outcome: dict,
    ) -> str | None:
        task = state.current_task_metadata or {}
        expected = set(task.get("if_ids", []))
        claimed = set(outcome.get("implemented_if_ids", []))
        if not claimed <= expected:
            return "Devon evidence claims an IF id outside the current task"
        manifest = state.current_manifest or {}
        allowed = set(manifest.get("allowed_paths", []))
        forbidden = set(manifest.get("forbidden_paths", []))
        for path in outcome["changed_paths"]:
            if path.startswith("/") or ".." in path.split("/"):
                return f"Devon evidence path is not repo-relative: {path}"
            if any(_manifest_path_matches(path, item) for item in forbidden):
                return f"Devon evidence path is forbidden: {path}"
            if not any(_manifest_path_matches(path, item) for item in allowed):
                return f"Devon evidence path is outside manifest: {path}"
        if phase == "red":
            devon_test_dirs = [d for d in layout_paths(self.repo, "devon") if "test" in d]
            if devon_test_dirs and any(
                not any(path.startswith(d) for d in devon_test_dirs)
                for path in outcome["changed_paths"]
            ):
                return "Devon RED evidence includes a non-test path"
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
        return ""

    def _issue_evidence(self) -> str:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "issues.created":
                mapping = ev.payload.get("mapping", {})
                return json.dumps(
                    mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
        return ""

    def _design_checkpoint_sha(self) -> str:
        """C_design: latest ``result.checkpointed.payload.commit_sha`` inside the
        real M-DESIGN stage window (stage.entered(M-DESIGN) .. the matching
        stage.exited). A checkpoint from any other stage, or one emitted outside
        a genuine M-DESIGN window, is never consumed."""
        checkpoint = ""
        in_design = False
        for ev in self.store.events(self.run_id):
            if ev.type == "stage.entered" and ev.payload.get("stage") == "M-DESIGN":
                in_design = True
                continue
            if ev.type == "stage.exited" and ev.payload.get("stage") == "M-DESIGN":
                in_design = False
                continue
            if in_design and ev.type == "result.checkpointed":
                sha = ev.payload.get("commit_sha")
                if isinstance(sha, str) and sha.strip():
                    checkpoint = sha.strip()
        return checkpoint

    def _do_freeze_baseline(self, cmd, state, task_id, reconcile):
        if reconcile and state.baseline_frozen:
            return
        vdir = self._vdir()
        frozen = self._frozen_test_paths()
        approval_digest = self._approval_digest()
        issue_evidence = self._issue_evidence()
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        tip = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        design_checkpoint = self._design_checkpoint_sha()
        digest = m_impl_baseline_digest(
            vdir,
            self.repo,
            approval_digest=approval_digest,
            issue_evidence=issue_evidence,
            frozen_test_paths=frozen,
            branch=branch,
            tip=tip,
            design_checkpoint=design_checkpoint,
        )
        try:
            load_contract(self.repo)
            contract_valid = True
        except ContractError:
            contract_valid = False
        missing = list(
            m_impl_baseline_missing(
                vdir,
                self.repo,
                approval_digest=approval_digest,
                issue_evidence=issue_evidence,
                frozen_test_paths=frozen,
                contract_valid=contract_valid,
                branch=branch,
                tip=tip,
                design_checkpoint=design_checkpoint,
            )
        )
        self._emit(
            "baseline.frozen",
            {
                "status": "current" if not missing else "stale",
                "digest": digest,
                "summary": m_impl_baseline_summary(vdir),
                "frozen_test_paths": frozen,
                "missing": missing,
                "branch": branch,
                "tip": tip,
                "design_checkpoint": design_checkpoint,
            },
            command_id=cmd.command_id,
        )

    def _do_commit_taskgraph(self, cmd, state, task_id, reconcile):
        if reconcile and state.taskgraph_committed:
            self._rebuild_task_log_projection()
            return
        vdir = self._vdir()
        tasks_json_path = vdir / "tasks.json"
        raw, error = self._read_taskgraph(tasks_json_path)
        if error is not None:
            self._emit_taskgraph_failure(cmd, state, error, raw)
            return
        tasks, error = parse_tasks_json(raw)
        if error is not None:
            self._emit_taskgraph_failure(cmd, state, error, raw[:500])
            return
        errors = self._taskgraph_errors(vdir, tasks)
        if errors:
            self._emit_taskgraph_failure(cmd, state, "; ".join(errors), "\n".join(errors))
            return
        (vdir / "tasks.md").write_text(self._tasks_md(tasks), encoding="utf-8")
        self._emit(
            "taskgraph.committed",
            {
                "task_count": len(tasks),
                "task_ids": [t.task_id for t in tasks],
                "tasks": [self._task_payload(t) for t in tasks],
                "path": "tasks.json",
                "validate_status": "pass",
                "digest": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "tasks_md": "tasks.md",
            },
            command_id=cmd.command_id,
        )
        self._rebuild_task_log_projection()

    @staticmethod
    def _read_taskgraph(path: Path) -> tuple[str, str | None]:
        if not path.exists():
            return "", "tasks.json not found; Archer must produce it"
        try:
            return path.read_text(encoding="utf-8"), None
        except (OSError, UnicodeDecodeError) as exc:
            return "", f"tasks.json unreadable: {exc}"

    def _emit_taskgraph_failure(
        self,
        cmd,
        state,
        reason: str,
        evidence: str = "",
    ) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "taskgraph",
                "reason": reason,
                "evidence": evidence,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    @staticmethod
    def _taskgraph_errors(vdir: Path, tasks: list[TaskNode]) -> list[str]:
        errors = validate_task_structure(tasks)
        ok, cycle = validate_dag(tasks)
        if not ok:
            errors.append(cycle or "task graph DAG is invalid (validate_dag returned no reason)")
        ok, overlap = validate_scope(tasks)
        if not ok:
            errors.extend(overlap)
        ac_ids, if_registry, doc_errors = MImplRuntimeMixin._taskgraph_docs(
            vdir / "acceptance.md",
            vdir / "interfaces.md",
        )
        errors.extend(doc_errors)
        ok, coverage = validate_ac_coverage(tasks, ac_ids, if_registry)
        if not ok:
            errors.extend(coverage)
        ok, issue_errors = validate_issue_numbers(tasks)
        if not ok:
            errors.extend(issue_errors)
        return errors

    @staticmethod
    def _taskgraph_docs(
        acc_path: Path,
        if_path: Path,
    ) -> tuple[list[str], set[str], list[str]]:
        errors: list[str] = []
        if not acc_path.is_file():
            errors.append("acceptance.md missing; cannot determine required ACs")
            ac_ids = []
        else:
            ac_ids = sorted(_known_ac_ids(acc_path.read_text(encoding="utf-8")))
        if not if_path.is_file():
            errors.append("interfaces.md missing; cannot validate IF registry")
            return ac_ids, set(), errors
        if_registry = _extract_if_registry(if_path.read_text(encoding="utf-8"))
        if if_registry is None:
            errors.append("interfaces.md missing IF Registry")
            if_registry = set()
        return ac_ids, if_registry, errors

    @staticmethod
    def _task_payload(task: TaskNode) -> dict:
        return {
            "task_id": task.task_id,
            "issue_number": task.issue_number,
            "description": task.description,
            "ac_refs": list(task.ac_refs),
            "fr_refs": list(task.fr_refs),
            "if_ids": list(task.if_ids),
            "test_refs": list(task.test_refs),
            "scope_boundary": task.scope_boundary,
            "depends_on": list(task.depends_on),
            "batch": task.batch,
            "parallel": task.parallel,
            "budget": task.budget,
        }

    @staticmethod
    def _tasks_md(tasks) -> str:
        lines = ["# Task Graph", ""]
        for task in tasks:
            lines.extend(
                [
                    f"## {task.task_id}",
                    f"- Issue: #{task.issue_number}",
                    f"- Description: {task.description}",
                    f"- AC refs: {', '.join(task.ac_refs)}",
                    f"- FR refs: {', '.join(task.fr_refs)}",
                    f"- IF ids: {', '.join(task.if_ids)}",
                    f"- Test refs: {', '.join(task.test_refs)}",
                    f"- Scope: {task.scope_boundary}",
                    f"- Depends on: {', '.join(task.depends_on) if task.depends_on else '-'}",
                    f"- Batch: {task.batch}",
                    f"- Parallel: {task.parallel}",
                    "",
                ]
            )
        return "\n".join(lines)

    def _do_check_island_1(self, cmd, state, task_id, reconcile):
        vdir = self._vdir()
        errors = self._island_taskgraph_errors(vdir, vdir / "tasks.json", state)
        if errors:
            self._emit(
                "verdict.failed",
                {
                    "check": "island",
                    "reason": "; ".join(errors),
                    "evidence": "\n".join(errors),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit("verdict.passed", {"check": "island_1"}, command_id=cmd.command_id)

    def _island_taskgraph_errors(
        self,
        vdir: Path,
        tasks_path: Path,
        state: State,
    ) -> list[str]:
        raw, error = self._read_taskgraph(tasks_path)
        if error is not None:
            return [error]  # keep the full reason including remediation hint
        tasks, error = parse_tasks_json(raw)
        if error is not None:
            return [error]
        if (
            state.taskgraph_digest
            and hashlib.sha256(raw.encode("utf-8")).hexdigest() != state.taskgraph_digest
        ):
            return ["tasks.json changed after taskgraph.committed"]
        errors = self._taskgraph_errors(vdir, tasks)
        arch_path = vdir / "architecture.md"
        if not arch_path.is_file():
            errors.append("architecture.md missing")
        else:
            ok, closure = validate_island_closure(
                tasks,
                arch_path.read_text(encoding="utf-8"),
            )
            if not ok:
                errors.extend(closure)
        return errors

    def _do_check_island_2(self, cmd, state, task_id, reconcile):
        if reconcile and state.island_2_passed:
            return
        from tracks.checks.reach import check_reach_file

        reach = check_reach_file(self.repo)
        if reach.status != "pass":
            self._emit(
                "verdict.failed",
                {
                    "check": "island",
                    "reason": "reach check failed: " + "; ".join((*reach.errors, *reach.islands)),
                    "evidence": str(reach),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        results, error = self._run_contract_sections(cmd, state, "run")
        if error is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "full_suite",
                    "reason": error,
                    "evidence": error,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        failures = [
            f"{name}: " + _short_detail(stdout + "\n" + stderr)
            for name, code, stdout, stderr in results
            if code != 0
        ]
        if failures:
            self._emit(
                "verdict.failed",
                {
                    "check": "full_suite",
                    "reason": "; ".join(failures),
                    "evidence": "; ".join(failures),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit("verdict.passed", {"check": "island_2"}, command_id=cmd.command_id)

    def _do_select_task(self, cmd, state, task_id, reconcile):
        if state.current_task_id:
            return
        tasks = [self._task_node(raw) for raw in state.task_refs]
        if not tasks:
            self._emit_no_task_failure(cmd, state, "no tasks available")
            return
        events = list(self.store.events(self.run_id))
        completed, started = self._task_event_ids(events)
        if started - completed:
            return
        if state.writelock_held:
            self._recover_task_lease(cmd, events, completed)
            return
        ready = [
            task
            for task in tasks
            if task.task_id not in completed
            and task.task_id not in started
            and all(dep == "-" or dep in completed for dep in task.depends_on)
        ]
        if not ready:
            self._emit_no_task_failure(cmd, state, "no ready tasks")
            return
        chosen = min(ready, key=lambda task: (_batch_key(task.batch), task.task_id))
        self._start_task(cmd, chosen, self._task_manifest(chosen, state))

    @staticmethod
    def _task_event_ids(events) -> tuple[set[str], set[str]]:
        completed = {
            ev.payload.get("task_id")
            for ev in events
            if ev.type == "task.completed" and ev.payload.get("task_id")
        }
        started = {
            ev.payload.get("task_id")
            for ev in events
            if ev.type == "task.started" and ev.payload.get("task_id")
        }
        return completed, started

    def _recover_task_lease(self, cmd, events, completed: set[str]) -> None:
        lease = next((ev for ev in reversed(events) if ev.type == "writelock.granted"), None)
        if lease is None:
            return
        payload = lease.payload
        lease_task = payload.get("task_id")
        if lease_task in completed:
            released = any(
                ev.type == "writelock.released" and ev.payload.get("task_id") == lease_task
                for ev in events
            )
            if not released:
                self._emit("writelock.released", {"task_id": lease_task}, command_id=cmd.command_id)
            self._rebuild_task_log_projection()
            return
        if lease_task and payload.get("manifest"):
            self._emit(
                "task.started",
                {
                    "task_id": lease_task,
                    "task": payload.get("task", {}),
                    "manifest": payload["manifest"],
                },
                command_id=cmd.command_id,
            )
            self._rebuild_task_log_projection()

    def _start_task(self, cmd, task: TaskNode, manifest: dict) -> None:
        payload = {"task_id": task.task_id, "task": self._task_payload(task), "manifest": manifest}
        self._emit(
            "writelock.granted",
            {
                **payload,
                "allowed_paths": self._task_allowed_paths(task),
                "forbidden_paths": manifest["forbidden_paths"],
            },
            command_id=cmd.command_id,
        )
        self._emit("task.started", payload, command_id=cmd.command_id)
        self._rebuild_task_log_projection()

    def _emit_no_task_failure(self, cmd, state, reason: str) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "island",
                "reason": reason,
                "evidence": "no tasks available for dispatch",
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    @staticmethod
    def _parse_allowed_paths(scope_boundary: str) -> list[str]:
        parts = (p.strip().replace("\\", "/") for p in scope_boundary.replace("\n", ",").split(","))
        return sorted(
            {
                p.rstrip("/")
                for p in parts
                if p and not p.startswith("/") and ".." not in p.split("/")
            }
        )

    def _forbidden_paths(self) -> list[str]:
        forbidden = {".tracks/projects/**"}
        # Shield's writable dirs are forbidden for Devon (from project.toml).
        for path in layout_paths(self.repo, "shield"):
            clean = path.rstrip("/")
            forbidden.update({path, f"{clean}/**"})
        for path in self._frozen_test_paths():
            clean = path.rstrip("/")
            forbidden.update({path, f"{clean}/**"})
        return sorted(forbidden)

    def _test_commands(self) -> dict:
        try:
            contract = load_contract(self.repo)
        except ContractError:
            return {}
        return {
            name: section.run
            for name, section in (("integration", contract.integration), ("e2e", contract.e2e))
            if section is not None
        }

    def _guard_commands(self) -> list[str]:
        return [
            ".venv/bin/python -m ruff check",
            ".venv/bin/python -m flake8 --select=CCR001",
            ".venv/bin/python -m pylint --disable=all --enable=R0801,C0302,R0915,R0914",
        ]

    def _unit_commands(self) -> list[str]:
        test_dirs = [d.rstrip("/") for d in layout_paths(self.repo, "devon") if "test" in d]
        if not test_dirs:
            return []
        return [f".venv/bin/python -m pytest -n 4 {' '.join(test_dirs)}"]

    def _task_manifest(self, task: TaskNode, state: State) -> dict:
        pre_dirty = self._dirty_snapshot()
        baseline_digest = state.taskgraph_digest or ""
        if not baseline_digest:
            for ev in reversed(list(self.store.events(self.run_id))):
                if ev.type == "baseline.frozen":
                    baseline_digest = ev.payload.get("digest", "")
                    break
        manifest = {
            "task_id": task.task_id,
            "task_ref": self._task_payload(task),
            "issue_number": task.issue_number,
            "if_ids": list(task.if_ids),
            "ac_refs": list(task.ac_refs),
            "fr_refs": list(task.fr_refs),
            "test_refs": list(task.test_refs),
            "scope_boundary": task.scope_boundary,
            "allowed_paths": self._task_allowed_paths(task),
            "forbidden_paths": self._forbidden_paths(),
            "frozen_test_paths": self._frozen_test_paths(),
            "phase_rules": {
                "red": "write failing unit tests only",
                "green": "write implementation only; keep R tests immutable",
                "refactor": "quality-only changes; preserve green behavior",
            },
            "budget": task.budget,
            "baseline_identity": baseline_digest,
            "unit_commands": self._unit_commands(),
            "test_commands": self._test_commands(),
            "guard_commands": self._guard_commands(),
            "pre_dirty_snapshot": pre_dirty,
            "r_tree_identity": None,
            "result_identity": None,
        }
        manifest["result_identity"] = self._result_identity(
            task.task_id,
            "manifest",
            state.current_attempt,
            pre_dirty,
            baseline_digest,
        )
        return manifest

    def _task_allowed_paths(self, task: TaskNode) -> list[str]:
        allowed = set(self._parse_allowed_paths(task.scope_boundary))
        for ref in task.test_refs:
            path = ref.split("::", 1)[0].strip()
            if path.startswith("tests/unit/"):
                allowed.add(path)
        return sorted(allowed)

    def _result_identity(
        self,
        task_id: str,
        phase: str,
        attempt: int,
        pre_dirty: dict[str, str],
        baseline_digest: str,
    ) -> str:
        material = json.dumps(
            {
                "run_id": self.run_id,
                "task_id": task_id,
                "phase": phase,
                "attempt": attempt,
                "pre_dirty": pre_dirty,
                "baseline": baseline_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

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

    def _unit_commands_from_manifest(self) -> list[str]:
        commands = (self._current_manifest() or {}).get("unit_commands")
        if (
            isinstance(commands, list)
            and commands
            and all(isinstance(item, str) and item.strip() for item in commands)
        ):
            return list(commands)
        return [".venv/bin/python -m pytest -n 4 tests/unit"]

    def _ensure_gate_worktree(self, state: State) -> tuple[str, WorktreeHandle | None]:
        """Find or create a gate worktree with R tests + Green impl applied.

        Returns (cwd, handle). If handle is not None, the caller must clean it up
        via cleanup_worktree(handle). If handle is None, the cwd is either a
        pre-existing worktree or the repo itself (no cleanup needed).
        """
        task_id = state.current_task_id or ""
        gate_path = os.path.join(
            str(self.repo), ".tracks", "worktrees", self.run_id, task_id, "gate"
        )
        if task_id and os.path.isdir(gate_path):
            return gate_path, None  # Pre-existing worktree (e.g., test-created)

        # Try to create a gate worktree from base B + Green impl + R tests
        r_sha = state.r_tree_identity
        if not r_sha or not r_sha.strip() or r_sha == "0" * 40:
            return str(self.repo), None  # Bogus R, can't create worktree

        base_sha = red_base_sha(str(self.repo), r_sha)
        if base_sha is None:
            return str(self.repo), None  # Can't derive base B from R

        # When the immutable R tests are already present in the observed
        # working tree, run the gate against that state so a hidden R test
        # mutation is surfaced as `regression`. Only when they are missing
        # from the tree (they live solely inside the R commit) is a gate
        # worktree needed to restore them next to the Green impl.
        if self._r_tests_in_working_tree(r_sha):
            return str(self.repo), None

        # Get the Green impl diff from the latest green outcome
        g_diff = None
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "outcome.received" and ev.payload.get("phase") == "green":
                g_diff = ev.payload.get("diff_ref")
                break
        if not g_diff or not g_diff.strip():
            return str(self.repo), None  # No Green diff available

        try:
            handle = create_gate_worktree(
                str(self.repo),
                base_sha,
                r_sha,
                g_diff,
                self.run_id,
                task_id,
            )
            return handle.path, handle
        except Exception:
            return str(self.repo), None  # Creation failed, fall back to repo cwd

    def _r_tests_in_working_tree(self, r_sha: str) -> bool:
        """Whether every tests/ file in the immutable R commit is present as a
        file in the main working tree (the observed candidate state)."""
        proc = git(
            self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", r_sha, check=False
        )
        if proc.returncode != 0:
            return False
        rels = [
            line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("tests/")
        ]
        return bool(rels) and all(os.path.isfile(os.path.join(str(self.repo), rel)) for rel in rels)

    def _emit_gate_failure(self, cmd, *, check, reason, task_id, attempt, evidence=""):
        self._emit(
            "verdict.failed",
            {
                "check": check,
                "reason": reason,
                "evidence": evidence,
                "task_id": task_id,
                "attempt": attempt,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _run_green_gate(self, cmd, state: State) -> None:
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
            for command in self._unit_commands_from_manifest():
                obs = execute_gate_command(command, cwd)
                if obs.exit_code != 0:
                    self._emit_gate_failure(
                        cmd,
                        check="impl_defect",
                        reason=f"Runtime unit command exited {obs.exit_code}",
                        evidence=observation_evidence(obs),
                        task_id=task_id,
                        attempt=attempt,
                    )
                    return
            r_sha = state.r_tree_identity
            if r_sha and r_sha.strip() and r_sha != "0" * 40:
                proc = git(Path(cwd), "diff", "--name-only", r_sha, "--", "tests/", check=False)
                if proc.returncode == 0 and proc.stdout.strip():
                    self._emit_gate_failure(
                        cmd,
                        check="regression",
                        reason="Runtime observed R unit tests changed",
                        evidence=json.dumps(
                            {"r_sha": r_sha, "changed_tests": sorted(set(proc.stdout.splitlines()))}
                        ),
                        task_id=task_id,
                        attempt=attempt,
                    )
                    return
            self._emit("verdict.passed", {"check": "green"}, command_id=cmd.command_id)
        finally:
            if gate_handle is not None:
                cleanup_worktree(gate_handle)

    def _run_task_review_gate(self, cmd, state: State) -> None:
        task_id = state.current_task_id
        attempt = state.current_attempt + 1
        events = [
            {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
            for ev in self.store.events(self.run_id)
        ]
        green = next((ev for ev in reversed(events) if ev["type"] == "green.committed"), None)
        payload = green["payload"] if green is not None else {}
        task = self._lookup_task(task_id)
        g_sha = payload.get("g_sha")
        base_sha = payload.get("base_sha")
        if green is None or task is None or not g_sha or not base_sha:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="no green.committed event found"
                if green is None
                else "green.committed lacks task or base identity",
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
            self._emit(
                "verdict.failed",
                {
                    "check": failure_check,
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "task_id": state.current_task_id,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=state.current_task_id,
            )
            return
        self._emit("verdict.passed", {"check": pass_check}, command_id=cmd.command_id)

    def _m_impl_event_recorded(
        self,
        event_type: str,
        task_id: str,
        attempt: int,
    ) -> bool:
        return any(
            ev.type == event_type
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("attempt") == attempt
            for ev in self.store.events(self.run_id)
        )

    def _do_checkpoint_red(self, cmd, state, task_id, reconcile):
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        if self._m_impl_event_recorded("red.checkpointed", task_id, attempt):
            self._rebuild_task_log_projection()
            return
        reason, diff = self._validated_diff("red", state)
        if reason is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "task_id": task_id,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
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
            {"ref": r.ref, "r_sha": r.sha, "task_id": task_id, "attempt": attempt},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _validated_diff(self, phase: str, state: State) -> tuple[str | None, str | None]:
        reason = self._devon_evidence_error(phase, state)
        outcome = self._last_devon_outcome()
        diff = outcome.get("diff_ref") if outcome else None
        if reason is None and not isinstance(diff, str):
            return f"Devon {phase.upper()} outcome has no captured diff_ref", diff
        if reason is None and not diff.strip():
            return f"Devon {phase.upper()} captured diff is empty", diff
        return reason, diff

    def _do_commit_green(self, cmd, state, task_id, reconcile):
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        if any(
            ev.type == "verdict.failed"
            and ev.payload.get("check") == "impl_defect"
            and ev.payload.get("task_id") in (None, task_id)
            and ev.payload.get("attempt") == attempt
            for ev in self.store.events(self.run_id)
        ):
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
        if self._m_impl_event_recorded("green.committed", task_id, attempt):
            self._rebuild_task_log_projection()
            return
        reason, diff = self._validated_diff("green", state)
        if reason is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "impl_defect",
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "task_id": task_id,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        task = self._lookup_task(task_id)
        if task is None or not state.r_tree_identity:
            self._emit(
                "verdict.failed",
                {
                    "check": "impl_defect",
                    "reason": (
                        "green commit lacks task_id or R lineage identity "
                        "(check Devon pre/post identity trailers)"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        r_sha = state.r_tree_identity
        # FR-0120 replay-safe base: B is derived from the immutable R commit's
        # parent (never blindly the current HEAD), so a crash after the branch
        # update but before green.committed reconciles to the same G.
        b_sha = red_base_sha(str(self.repo), r_sha)
        if b_sha is None:
            self._emit(
                "verdict.failed",
                {
                    "check": "impl_defect",
                    "reason": "green lineage base B unresolvable from R",
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": f"r_sha={r_sha}",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        g = create_green_commit(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=attempt,
            impl_diff=diff,
            base_sha=b_sha,
            r_sha=r_sha,
            issue_number=task.issue_number,
            ac_refs=_combined_provenance(task),
        )
        materialized, materialize_reason = self._materialize_green_commit(
            g.sha,
            b_sha,
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
        self._emit(
            "green.committed",
            {
                "g_sha": g.sha,
                "task_id": task_id,
                "attempt": attempt,
                "r_sha": r_sha,
                "base_sha": b_sha,
                "trailers": g.trailers,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

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
          parent is exactly B).
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
        git(self.repo, "reset", "--hard", g_sha)
        return True, None

    def _do_run_refactor_gate(self, cmd, state, task_id, reconcile):
        gate_handle = None
        try:
            reason = self._devon_evidence_error("refactor", state)
            if reason is not None:
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
                return
            cwd, gate_handle = self._ensure_gate_worktree(state)
            for command in self._unit_commands_from_manifest():
                obs = execute_gate_command(command, cwd)
                if obs.exit_code != 0:
                    self._emit_gate_failure(
                        cmd,
                        check="regression",
                        reason=f"Runtime unit command exited {obs.exit_code}",
                        evidence=observation_evidence(obs),
                        task_id=state.current_task_id,
                        attempt=state.current_attempt + 1,
                    )
                    if state.substate != "REFACTOR_GATE":
                        # flow.md §10.1: a REFACTOR_GATE failure routes back to
                        # REFACTOR, never forward to TASK_REVIEW; reconcile a
                        # gate driven outside the formal window to that domain.
                        self._emit(
                            "green.committed",
                            {
                                "g_sha": state.r_tree_identity or "",
                                "r_sha": state.r_tree_identity,
                                "base_sha": "",
                                "trailers": {},
                                "task_id": state.current_task_id,
                                "attempt": state.current_attempt + 1,
                            },
                            command_id=cmd.command_id,
                            task_id=state.current_task_id,
                        )
                    return
            outcome = self._last_devon_outcome() or {}
            changed = sorted(set(outcome.get("changed_paths", [])))
            if not changed:
                self._emit(
                    "refactor.no_change",
                    {"task_id": state.current_task_id, "reason": outcome.get("no_change_reason")},
                    command_id=cmd.command_id,
                )
                return
            allowed = set((state.current_manifest or {}).get("allowed_paths", []))
            outside = sorted(path for path in changed if path not in allowed)
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
            git(self.repo, "add", "--", *changed)
            proc = _commit_if_staged(
                self.repo,
                f"M-IMPL: refactor\n\ncommand_id: {cmd.command_id}",
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
                return
            if proc.returncode != 0:
                self._emit_commit_failure(proc, state, cmd.command_id)
                return
            self._emit(
                "refactor.committed", {"task_id": state.current_task_id}, command_id=cmd.command_id
            )
        finally:
            if gate_handle is not None:
                cleanup_worktree(gate_handle)
            self._rebuild_task_log_projection()

    def _do_verify_task(self, cmd, state, task_id, reconcile):
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
        cmd_argv = [".venv/bin/python", "-m", "pytest", "-q", *test_refs]
        try:
            proc = subprocess.run(
                cmd_argv,
                cwd=self.repo,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            rc, out = proc.returncode, proc.stdout or ""
        except subprocess.TimeoutExpired as exc:
            rc, out = 124, str(exc)
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
            # Failure evidence for the DIAGNOSE loop: the FAILED summary
            # lines first (the whole picture - run 01KZTHE7 T-008's diagnoser
            # had to re-count failures because a tail-only excerpt swallowed
            # them), then a short tail for context.
            failed_lines = [
                line
                for line in out.splitlines()
                if line.startswith("FAILED") or line.startswith("ERROR")
            ]
            evidence = "\n".join(failed_lines[:30]) + "\n--- tail ---\n" + out[-800:]
            self._emit(
                "verdict.failed",
                {
                    "check": "verification_failed",
                    "reason": f"verification test_refs failed rc={rc}: {summary}",
                    "evidence": evidence,
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
        self._rebuild_task_log_projection()

    def _do_complete_task(self, cmd, state, task_id, reconcile):
        tid = cmd.params.get("task_id") or state.current_task_id
        events = list(self.store.events(self.run_id))
        already_completed = any(
            ev.type == "task.completed" and ev.payload.get("task_id") == tid for ev in events
        )
        if already_completed:
            released = any(
                ev.type == "writelock.released" and ev.payload.get("task_id") == tid
                for ev in events
            )
            if not released:
                self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
            self._rebuild_task_log_projection()
            return
        self._emit("task.completed", {"task_id": tid}, command_id=cmd.command_id)
        self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
        self._rebuild_task_log_projection()
