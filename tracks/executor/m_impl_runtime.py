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
import shlex
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
from tracks.executor.quality_gate import (
    execute_gate_command,
    failed_summary_lines,
    observation_evidence,
)
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
from tracks.executor.worktree import (
    WorktreeHandle,
    cleanup_worktree,
    create_gate_worktree,
    ensure_runtime_assets,
)
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
    # Fix F (run 01KZTHE7 T-013, 2026-08-16): FR-11 `trac retry` resets the
    # attempt budget, so only verdict.failed events recorded after the last
    # human.retry count against the task budget. Pre-retry failures are
    # superseded - this run's history (56 retries, 31 verdict.failed) would
    # otherwise permanently fail-closed every TASK_REVIEW.
    cutoff = max(
        (ev["seq"] for ev in events if ev["type"] == "human.retry"),
        default=0,
    )
    if sum(
        ev["type"] == "verdict.failed" and ev["seq"] > cutoff for ev in events
    ) > task.budget:
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


def _devon_path_scope_error(
    changed_paths: list[str],
    allowed: set[str],
    forbidden: set[str],
    devon_test_dirs: list[str],
) -> str | None:
    """Validate one changed path against the manifest scope rules.

    Devon test-dir paths (RED failing tests) are exempt from the
    allowed_paths gate: impl-only manifests grant no test path, frozen
    suites stay blocked by forbidden_paths.
    """
    for path in changed_paths:
        if path.startswith("/") or ".." in path.split("/"):
            return f"Devon evidence path is not repo-relative: {path}"
        if any(_manifest_path_matches(path, item) for item in forbidden):
            return f"Devon evidence path is forbidden: {path}"
        if not any(_manifest_path_matches(path, item) for item in allowed) and not (
            devon_test_dirs and any(path.startswith(d) for d in devon_test_dirs)
        ):
            return f"Devon evidence path is outside manifest: {path}"
    return None


_M_IMPL_RED_CLASSIFICATIONS = frozenset({"assertion_failure", "symbol_missing"})


def _red_classification_label(value: object) -> str:
    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "unknown"
    value = value.strip()
    return value or "missing"


_RED_CLASSIFY_PATTERN = re.compile(
    r"classify_red\s*->\s*(assertion_failure|symbol_missing)"
)

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

    def _add_assignment_role_fields(
        self,
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
            if assignment["phase"] == "red":
                assignment["red_test_paths"] = self._devon_red_test_dirs()
                # 同时刷新 manifest 里的 phase_rules.red 文本，让存量任务 retry 也看到新合同
                if isinstance(assignment.get("manifest"), dict):
                    manifest = assignment["manifest"]
                    manifest["red_test_paths"] = self._devon_red_test_dirs()
                    pr = manifest.get("phase_rules")
                    if isinstance(pr, dict):
                        pr["red"] = (
                            "write failing unit tests only under red_test_paths; "
                            "allowed_paths lists the green-phase impl scope "
                            "and is not writable in RED"
                        )
        elif role == "prism":
            assignment["criteria_pack"] = dict(_M_IMPL_CRITERIA_PACK)
            if substate in ("PRISM_RED", "PRISM_FINAL", "DIAGNOSE"):
                assignment["r_tree_identity"] = state.r_tree_identity
        elif role == "shield" and substate == "WRITE":
            assignment["phase"] = "shield_fix"
            self._strip_test_forbidden_paths(assignment)
            self._grant_diagnosed_test_paths(assignment, state)

    @staticmethod
    def _strip_test_forbidden_paths(assignment: dict) -> None:
        """Shield needs to write to test paths (tests/integration/, etc.) to
        fix diagnosed test defects. The manifest from the task graph carries
        Devon's RGR forbidden_paths which include these test dirs. Strip
        test-path entries so Shield agent doesn't self-restrict (the audit
        already uses layout_paths("shield") as the whitelist)."""
        manifest = assignment.get("manifest")
        if isinstance(manifest, dict):
            forbidden = manifest.get("forbidden_paths")
            if isinstance(forbidden, list):
                manifest["forbidden_paths"] = [
                    p for p in forbidden if not p.startswith("tests/")
                ]

    _DIAGNOSED_TEST_PATH_RE = re.compile(r"tests/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+")

    @staticmethod
    def _grant_diagnosed_test_paths(assignment: dict, state) -> None:
        """SHIELD_FIX audit unlock (run 01KZTHE7 T-017): the over-reach audit
        whitelists Shield by [layout] dirs (tests/integration|e2e|...), but a
        diagnosed defect may live anywhere under tests/ (e.g. a unit-level
        RED contract test). Grant exactly the test files the Prism DIAGNOSE
        verdict names in its evidence by adding them to
        manifest.allowed_paths. Fail-closed: only paths the diagnosis
        literally mentions are granted — no blanket tests/ grant."""
        report = getattr(state, "diagnose_report", None) or {}
        evidence = report.get("evidence") or ""
        if not isinstance(evidence, str):
            evidence = str(evidence)
        named = sorted(set(MImplRuntimeMixin._DIAGNOSED_TEST_PATH_RE.findall(evidence)))
        if not named:
            return
        manifest = assignment.get("manifest")
        if not isinstance(manifest, dict):
            return
        allowed = list(manifest.get("allowed_paths") or [])
        manifest["allowed_paths"] = allowed + [p for p in named if p not in allowed]

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
        if (
            phase == "green"
            and not outcome.get("changed_paths")
            and not outcome.get("no_change_reason")
        ):
            # Symmetric with refactor: a GREEN resubmit may legitimately carry
            # no new paths when the implementation is already on disk from a
            # prior attempt that passed GREEN_GATE but was killed by a later
            # guard (run 01KZTHE7 T-013 attempt 2, 2026-08-15). Require an
            # explicit no_change_reason so the gate does not short-circuit a
            # genuinely missing implementation; the unit commands still run.
            return "Devon GREEN evidence needs changed paths or no_change_reason"
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
        # RED evidence is a failing test that must live in a Devon test dir
        # (tests/unit/). Such paths are exempt from the allowed_paths gate
        # because impl-only manifests (no unit test_refs) grant no test path;
        # frozen Shield suites remain blocked by forbidden_paths, and the
        # post-loop RED check below still requires a test-dir location.
        devon_test_dirs = self._devon_red_test_dirs() if phase == "red" else []
        path_error = _devon_path_scope_error(
            outcome["changed_paths"], allowed, forbidden, devon_test_dirs
        )
        if path_error is not None:
            return path_error
        if phase == "red" and devon_test_dirs and any(
            not any(path.startswith(d + "/") or path == d for d in devon_test_dirs)
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
        # FR-0150 rollback re-entry: a task started in a PREVIOUS M-IMPL cycle
        # but never completed is stale work, not an in-flight lease -- the old
        # blanket guard deadlocked the fresh cycle on it (T-017 stranded,
        # run 01KZTHE7, 2026-08-17). Completions count across cycles (the work
        # is committed in git); only task.started events after the latest
        # M-IMPL stage.entered gate the single-in-flight rule, and stale
        # starts remain selectable so the task re-runs on a clean budget.
        impl_entry_seq = max(
            (
                e.seq
                for e in events
                if e.type == "stage.entered" and e.payload.get("stage") == "M-IMPL"
            ),
            default=0,
        )
        started_this_cycle = {
            e.payload.get("task_id")
            for e in events
            if e.type == "task.started" and e.seq > impl_entry_seq and e.payload.get("task_id")
        }
        if started_this_cycle - completed:
            return
        if state.writelock_held:
            self._recover_task_lease(cmd, events, completed)
            return
        ready = [
            task
            for task in tasks
            if task.task_id not in completed
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
        test_dirs = self._devon_red_test_dirs()
        if not test_dirs:
            return []
        return [f".venv/bin/python -m pytest -n 4 {' '.join(test_dirs)}"]

    def _devon_red_test_dirs(self) -> list[str]:
        """RED-phase test write grant dirs: devon layout dirs containing 'test'.

        与 _devon_evidence_error 和 _unit_commands 同源派生，避免漂移。
        RED phase 允许 Devon 在这些目录写 failing unit tests，即使
        manifest.allowed_paths (impl scope) 不含测试路径。
        """
        return [d.rstrip("/") for d in layout_paths(self.repo, "devon") if "test" in d]

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
            "red_test_paths": self._devon_red_test_dirs(),
            "frozen_test_paths": self._frozen_test_paths(),
            "phase_rules": {
                "red": "write failing unit tests only under red_test_paths; "
                       "allowed_paths lists the green-phase impl scope and is not writable in RED",
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
        via cleanup_worktree(handle). If handle is None, the cwd is the repo
        itself (no cleanup needed).
        """
        task_id = state.current_task_id or ""
        gate_path = os.path.join(
            str(self.repo), ".tracks", "worktrees", self.run_id, task_id, "gate"
        )
        if task_id and os.path.isdir(gate_path):
            # Pre-existing worktree (e.g., test-created, or a leftover from a
            # cleanup failure): re-link runtime assets (.opencode) so gate unit
            # commands see the deployment meta-tests compare against (T-013).
            ensure_runtime_assets(str(self.repo), gate_path)
            # B2 (run 01KZTHE7, user ruling: no cross-gate sharing): return a
            # real handle so the caller's finally-cleanup removes the worktree.
            # The old ``return gate_path, None`` made every pre-existing gate
            # worktree a permanent leak (five accumulated across T-013..T-018;
            # ISLAND_GATE_2 reach then reported 1208 phantom islands).
            return gate_path, WorktreeHandle(path=gate_path, base_sha="", kind="gate")

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

    def _gate_evidence(self, obs) -> str:
        """Failed-gate evidence for the fixer relay: FAILED summary lines
        plus the blob path of the full captured output. Fixers are forbidden
        from re-running integration/e2e suites, so the evidence itself must
        carry everything they cannot obtain (user directive 2026-08-15)."""
        ref = self.store.write_audit_blob(
            {
                "argv": list(obs.argv),
                "cwd": obs.cwd,
                "exit_code": obs.exit_code,
                "stdout": obs.stdout,
                "stderr": obs.stderr,
            }
        )
        extra = {"failed": failed_summary_lines(obs.stdout)}
        if ref:
            extra["log_ref"] = f".tracks/runtime/blobs/{ref}"
        return observation_evidence(obs, extra)

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

    _LINT_TIMEOUT_SECONDS = 120

    def _lint_targets(self, phase: str) -> list[str]:
        """B4 (issue #5): Devon's delivered files this phase, lint-scoped.

        RED lints delivered test files (under tests/); GREEN lints
        delivered non-test sources. Only .py files existing on disk are
        handed over — the runtime stays language-neutral.
        """
        outcome = self._last_devon_outcome() or {}
        changed = outcome.get("changed_paths") or []

        def _under_tests(path: str) -> bool:
            return path.startswith("tests/")

        want_tests = phase == "red"
        return [
            path
            for path in changed
            if path.endswith(".py")
            and _under_tests(path) == want_tests
            and (self.repo / path).is_file()
        ]

    def _run_declared_lint(self, paths: list[str]) -> tuple[str | None, str]:
        """Run the Archer-declared lint command ([lint].check) over paths.

        Returns (findings, note): findings is None when clean or skipped,
        otherwise the linter output that fails the gate as check=lint.
        Fail-open on tool-environment problems (missing binary, timeout):
        hygiene tooling must not block the pipeline; actual findings
        fail closed (B4, issue #5).
        """
        from tracks.project import lint_check_command

        command = lint_check_command(self.repo)
        if command is None or not paths:
            return None, ""
        argv = shlex.split(command) + list(paths)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                timeout=self._LINT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"lint skipped: {exc}"
        if proc.returncode != 0:
            return (proc.stdout + "\n" + proc.stderr).strip() or "lint failed", ""
        return None, ""

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
                        evidence=self._gate_evidence(obs),
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
            lint_findings, lint_note = self._run_declared_lint(
                self._lint_targets("green")
            )
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
            payload = {"check": "green"}
            if lint_note:
                payload["lint"] = lint_note
            self._emit("verdict.passed", payload, command_id=cmd.command_id)
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
    ) -> bool:
        cutoff = self._retry_cutoff_seq()
        return any(
            ev.type == event_type
            and ev.seq > cutoff
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
            # Fail-closed fallback: Devon outcomes sometimes omit `diff_ref`.
            # Reconstruct it from the working tree using `changed_paths`.
            generated = self._generate_diff_from_changed_paths(outcome)
            if generated is None:
                return f"Devon {phase.upper()} outcome has no captured diff_ref", diff
            diff = generated
        if reason is None and not diff.strip():
            return f"Devon {phase.upper()} captured diff is empty", diff
        return reason, diff

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

    def _do_commit_green(self, cmd, state, task_id, reconcile):
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        cutoff = self._retry_cutoff_seq()
        if any(
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
            if self._emit_green_no_change(cmd, state, task_id, diff, reconcile):
                return
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
        green_base = self._green_commit_base(b_sha)
        if green_base is None:
            self._emit(
                "verdict.failed",
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
            base_sha=green_base,
            r_sha=r_sha,
            issue_number=task.issue_number,
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
        self._emit(
            "green.committed",
            {
                "g_sha": g.sha,
                "task_id": task_id,
                "attempt": attempt,
                "r_sha": r_sha,
                "base_sha": g.parent,
                "trailers": g.trailers,
            },
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
        anc = git(
            self.repo, "merge-base", "--is-ancestor", b_sha, "HEAD", check=False
        )
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
                        evidence=self._gate_evidence(obs),
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

    def _do_anchor_red(self, cmd, state, task_id, reconcile):
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
        cmd_argv = [".venv/bin/python", "-m", "pytest", "-q", "--tb=long", *test_refs]
        err = ""
        try:
            proc = subprocess.run(
                cmd_argv, cwd=self.repo, capture_output=True, text=True, timeout=1800
            )
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            rc, out, err = 124, str(exc), "anchor run timed out after 1800s"
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
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        ref = f"refs/trac/rgr/{self.run_id}/{tid}/{attempt}/red"
        git(self.repo, "update-ref", ref, base_sha)
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
        cmd_argv = [".venv/bin/python", "-m", "pytest", "-q", "--tb=long", *test_refs]
        err = ""
        try:
            proc = subprocess.run(
                cmd_argv,
                cwd=self.repo,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            rc, out, err = 124, str(exc), "verification timed out after 1800s"
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
