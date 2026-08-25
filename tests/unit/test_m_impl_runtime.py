"""Focused Runtime contracts for the first half of M-IMPL.

Includes the T-03 "Runtime-authoritative gates" RED contract: the Runtime —
not the Agent — re-executes assigned commands in a Runtime-selected worktree,
derives candidate changed paths from observed git/filesystem state, and its
observed verdicts (with argv/cwd/exit_code/classification/output hashes) are
what route the kernel. Agent-reported commands/results remain audit-only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.unit.helpers import (
    RGR_GREEN_DIFF,
    RGR_RED_DIFF,
    git_repo,
)
from tests.unit.helpers import (
    git_strip as _git,
)
from tests.unit.helpers import (
    m_impl_docs as _docs,
)
from tests.unit.helpers import (
    m_impl_graph as _graph,
)
from tests.unit.helpers import (
    m_impl_started_task as _started_task_store,
)
from tests.unit.helpers import (
    m_impl_store as _store,
)
from tests.unit.helpers import (
    m_impl_task as _task,
)
from tracks import paths
from tracks.baseline import (
    m_impl_baseline_digest,
    m_impl_baseline_missing,
)
from tracks.executor.executor import Executor
from tracks.executor.m_impl_runtime import (
    _m_impl_red_classification_error,
    _m_impl_red_classifications,
)
from tracks.executor.rgr import (
    create_green_commit,
    create_red_ref,
    red_base_sha,
    verify_lineage,
)
from tracks.executor.worktree import (
    cleanup_worktree,
    create_gate_worktree,
)
from tracks.kernel import decide
from tracks.kernel.events import Command
from tracks.store import Store


def _repo(tmp_path: Path) -> Path:
    """Repo fixture with a committed .tracks/ gitignore (M-IMPL docs repo)."""
    return git_repo(tmp_path, gitignore=True)


def _contract(repo: Path) -> None:
    """Current atomic project contract (D-41): ``-q`` collect output is the
    nodeid-per-line inventory SELECT_TASK parses, and run_selected expands
    {nodes}/{result} for the per-task selected executions."""
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        "run_selected='.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit', 'integration', 'e2e']\n"
        "purpose='scheduled FULL-suite regression'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "integration").mkdir(parents=True)
    (repo / "tests" / "e2e").mkdir(parents=True)
    (repo / "tests" / "assets").mkdir(parents=True)


def _executor(repo: Path, store: Store) -> Executor:
    return Executor(store, repo, "RUN")


def test_baseline_payload_is_deterministic_and_stale_inputs_are_visible(tmp_path):
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    store = _store(repo)
    store.append("RUN", "v0.5", "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", "v0.5", "issues.created", {"mapping": {"FR-0001": 1}})
    executor = _executor(repo, store)
    command = Command("freeze_baseline", command_id="C-BASE")
    executor._do_freeze_baseline(command, store.state("RUN"), None, False)
    payload = list(store.events("RUN"))[-1].payload
    assert {"status", "digest", "summary", "frozen_test_paths"} <= payload.keys()
    assert payload["status"] == "current"
    digest = m_impl_baseline_digest(
        vdir,
        repo,
        approval_digest="approval-1",
        issue_evidence='{"FR-0001":1}',
        frozen_test_paths=payload["frozen_test_paths"],
        branch=payload["branch"],
        tip=payload["tip"],
        design_checkpoint=payload["design_checkpoint"],
    )
    assert payload["digest"] == digest

    (repo / "tests" / "e2e").rmdir()
    executor._do_freeze_baseline(command, store.state("RUN"), None, False)
    assert list(store.events("RUN"))[-1].payload["status"] == "stale"
    assert "tests/e2e/" in list(store.events("RUN"))[-1].payload["missing"]


def test_m_impl_baseline_digest_is_bound_to_branch_tip_and_design_checkpoint(tmp_path):
    """The M-IMPL baseline digest must change when the branch name, branch tip,
    or the M-DESIGN checkpoint commit identity changes (identity binding)."""
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    common = {
        "vdir": vdir,
        "repo": repo,
        "approval_digest": "approval-1",
        "issue_evidence": '{"FR-0001":1}',
        "frozen_test_paths": ["tests/integration/", "tests/e2e/"],
    }
    branch_a = m_impl_baseline_digest(
        **common, branch="main", tip="0" * 40, design_checkpoint="1" * 40
    )
    branch_b = m_impl_baseline_digest(
        **common, branch="release/v0.5", tip="0" * 40, design_checkpoint="1" * 40
    )
    tip_b = m_impl_baseline_digest(
        **common, branch="main", tip="2" * 40, design_checkpoint="1" * 40
    )
    checkpoint_b = m_impl_baseline_digest(
        **common, branch="main", tip="0" * 40, design_checkpoint="3" * 40
    )
    assert branch_a != branch_b
    assert branch_a != tip_b
    assert branch_a != checkpoint_b


def test_m_impl_baseline_missing_reports_absent_identity(tmp_path):
    """A baseline missing its branch name, branch tip, or M-DESIGN checkpoint
    commit identity reports the identity as missing (fail closed)."""
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    missing = m_impl_baseline_missing(
        vdir,
        repo,
        approval_digest="approval-1",
        issue_evidence='{"FR-0001":1}',
        frozen_test_paths=["tests/integration/"],
        contract_valid=True,
        branch="",
        tip="",
        design_checkpoint="",
    )
    assert "branch.name" in missing
    assert "branch.tip" in missing
    assert "design.checkpointed" in missing


def test_freeze_baseline_payload_binds_branch_tip_and_design_checkpoint(tmp_path):
    """baseline.frozen payload carries the branch name, branch tip, and the
    M-DESIGN checkpoint commit identity the digest is bound to."""
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    store = _store(repo)
    store.append("RUN", "v0.5", "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", "v0.5", "issues.created", {"mapping": {"FR-0001": 1}})
    tip = _git(repo, "rev-parse", "HEAD")
    executor = _executor(repo, store)
    executor._do_freeze_baseline(
        Command("freeze_baseline", command_id="C-BASE-ID"),
        store.state("RUN"),
        None,
        False,
    )
    payload = list(store.events("RUN"))[-1].payload
    assert payload["status"] == "current"
    assert payload.get("branch") == _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert payload.get("tip") == tip
    assert payload.get("design_checkpoint") == tip
    digest = m_impl_baseline_digest(
        vdir,
        repo,
        approval_digest="approval-1",
        issue_evidence='{"FR-0001":1}',
        frozen_test_paths=payload["frozen_test_paths"],
        branch=payload["branch"],
        tip=payload["tip"],
        design_checkpoint=payload["design_checkpoint"],
    )
    assert payload["digest"] == digest


def test_freeze_baseline_missing_design_checkpoint_is_stale(tmp_path):
    """A baseline with no M-DESIGN checkpoint identity fails closed as stale."""
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo, design_checkpoint=False)
    store.append("RUN", "v0.5", "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", "v0.5", "issues.created", {"mapping": {"FR-0001": 1}})
    executor = _executor(repo, store)
    executor._do_freeze_baseline(
        Command("freeze_baseline", command_id="C-BASE-STALE"),
        store.state("RUN"),
        None,
        False,
    )
    payload = list(store.events("RUN"))[-1].payload
    assert payload["status"] == "stale"
    assert "design.checkpointed" in payload["missing"]


def test_taskgraph_missing_invalid_valid_and_no_tasks_json_overwrite(tmp_path):
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TASK")
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    assert not (vdir / "tasks.json").exists()
    assert list(store.events("RUN"))[-1].payload["check"] == "taskgraph"

    invalid = vdir / "tasks.json"
    invalid.write_text('{"tasks": [{"task_id": "T-001"}]}', encoding="utf-8")
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    assert list(store.events("RUN"))[-1].type == "verdict.failed"

    task = _task()
    source = json.dumps({"tasks": [task]}, indent=2)
    invalid.write_text(source, encoding="utf-8")
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    assert invalid.read_text(encoding="utf-8") == source
    assert (vdir / "tasks.md").exists()
    committed = [ev for ev in store.events("RUN") if ev.type == "taskgraph.committed"][-1]
    assert committed.payload["task_ids"] == ["T-001"]


def test_island_gate_rechecks_six_tuple_and_passes_only_when_closed(tmp_path):
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [_task()]}, sort_keys=True), encoding="utf-8"
    )
    store = _store(repo)
    raw = (vdir / "tasks.json").read_text(encoding="utf-8")
    _graph(store, _task(), raw)
    executor = _executor(repo, store)
    command = Command("check_island_1", command_id="C-ISLAND")
    executor._do_check_island_1(command, store.state("RUN"), None, False)
    assert list(store.events("RUN"))[-1].payload == {"check": "island_1"}

    (vdir / "architecture.md").write_text(
        "# Architecture\n\n- **FR-0001**: owner=app, surface=cli\n",
        encoding="utf-8",
    )
    executor._do_check_island_1(command, store.state("RUN"), None, False)
    failed = list(store.events("RUN"))[-1]
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "island"


def test_manifest_persists_replays_and_ready_selection_is_serial(tmp_path):
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo)
    first = _task()
    second = {
        **_task("tests/unit/test_second.py::test_second"),
        "task_id": "T-002",
        "batch": "2",
        "depends_on": ["T-001"],
    }
    raw = json.dumps({"tasks": [second, first]}, sort_keys=True)
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {
            "task_count": 2,
            "task_ids": ["T-001", "T-002"],
            "tasks": [second, first],
            "path": "tasks.json",
            "digest": hashlib.sha256(raw.encode()).hexdigest(),
            "validate_status": "pass",
        },
    )
    executor = _executor(repo, store)
    command = Command("select_task", command_id="C-SELECT")
    executor._do_select_task(command, store.state("RUN"), None, False)
    events = list(store.events("RUN"))
    assert len([ev for ev in events if ev.type == "writelock.granted"]) == 1
    assert len([ev for ev in events if ev.type == "task.started"]) == 1
    state = store.state("RUN")
    assert state.current_task_id == "T-001"
    assert state.current_manifest["task_id"] == "T-001"
    replay = store.state("RUN")
    assert replay.current_manifest == state.current_manifest
    executor._do_select_task(command, replay, None, True)
    events = list(store.events("RUN"))
    assert len([ev for ev in events if ev.type == "task.started"]) == 1

    store.append("RUN", "v0.5", "task.completed", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.released", {"task_id": "T-001"})
    executor._do_select_task(command, store.state("RUN"), None, False)
    started = [ev for ev in store.events("RUN") if ev.type == "task.started"]
    assert [ev.payload["task_id"] for ev in started] == ["T-001", "T-002"]
    assert store.state("RUN").current_task_metadata["task_id"] == "T-002"


def test_select_task_after_recovered_reentry_ignores_abandoned_started(tmp_path):
    """B53 (#69): stage.recovered(M-IMPL) is a residency boundary for the
    in-flight-lease guard. A task.started from the abandoned cycle (never
    completed, rolled back through M-DESIGN, re-entered via B32 recovery)
    must not strand TASK_DISPATCH -- the pre-fix boundary scan only counted
    stage.entered, so the stale start looked in-flight and _do_select_task
    returned silently on every command (run 01M0S0FQ hot loop, ~120ms per
    select_task). FR-0150: stale starts remain selectable."""
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo)
    task = _task()
    _graph(store, task)
    # abandoned cycle: T-001 started, never completed
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
                "forbidden_paths": [".tracks/projects/**"],
            },
        },
    )
    store.append(
        "RUN",
        "v0.5",
        "stage.rolled_back",
        {"from_stage": "M-IMPL", "to_stage": "M-DESIGN", "reason": "stub_gap"},
    )
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-DESIGN"})
    # B32 forward recovery re-enters M-IMPL (the CURRENT residency boundary)
    store.append(
        "RUN",
        "v0.5",
        "stage.recovered",
        {"stage": "M-IMPL", "from_stage": "M-DESIGN", "reason": "mis-typed stub_gap"},
    )
    # fresh residency re-commits its taskgraph (stage.recovered reuses the
    # fresh-cycle reset, clearing task_refs)
    _graph(store, task)
    executor = _executor(repo, store)
    state = store.state("RUN")
    assert state.current_task_id is None  # recovery reset the stage cycle
    executor._do_select_task(
        Command("select_task", command_id="C-SELECT"), state, None, False
    )
    started = [ev for ev in store.events("RUN") if ev.type == "task.started"]
    # T-001 re-selected in the fresh residency (clean budget re-run)
    assert [ev.payload["task_id"] for ev in started] == ["T-001", "T-001"]
    assert store.state("RUN").current_task_id == "T-001"


def test_red_ref_free_attempt_skips_live_slot_of_same_residency(tmp_path):
    """B54 (#70): a same-residency re-checkpoint (Prism red_defect retry,
    human.retry round) must allocate the NEXT free R slot, not crash. The
    old walk raised on a slot live this residency -- run 01M0S0FQ T-001:
    slots 1-3 orphaned by the rollback, slot 4 checkpointed in this
    residency, the red_defect retry's checkpoint (attempt 2) walked into
    live slot 4 and killed the trac run process."""
    repo = _repo(tmp_path)
    store = _store(repo)
    executor = _executor(repo, store)
    # physical refs: slots 1-4 all taken (1-3 orphaned, 4 live this residency)
    sha = _git(repo, "rev-parse", "HEAD")
    for slot in (1, 2, 3, 4):
        _git(repo, "update-ref", f"refs/trac/rgr/RUN/T-001/{slot}/red", sha)
    # a live checkpoint event for slot 4 in THIS residency
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": "T-001", "attempt": 4, "r_sha": sha, "ref": "refs/trac/rgr/RUN/T-001/4/red"},
    )
    # retry's checkpoint requests attempt 2 -> walks orphans 2, 3 AND live 4 -> 5
    assert executor._red_ref_free_attempt("T-001", 2) == 5
    # fresh task with no refs at all keeps its requested slot
    assert executor._red_ref_free_attempt("T-002", 1) == 1


def test_refactor_diff_reconstructable_from_working_tree(tmp_path):
    """B55 (#71): the OpenCode backend never captures diff_ref for RGR
    refactor phases (GREEN outcomes carry diff_ref=None too); the gate must
    accept a working-tree reconstruction -- RED/GREEN's _validated_diff
    fallback -- instead of parking every real refactor with a contract_error
    (run 01M0S0FQ T-001: refactor changed tracks/adapters/base.py with
    diff_ref=None -> escalation park)."""
    repo = _repo(tmp_path)
    store = _store(repo)
    executor = _executor(repo, store)
    target = repo / "tracks" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "tracks/app.py")
    _git(repo, "commit", "-m", "app")
    # committed and unmodified: no diff to reconstruct
    assert not executor._refactor_diff_reconstructable(
        {"changed_paths": ["tracks/app.py"]}, []
    )
    # refactor change sitting uncommitted in the main tree -> reconstructable
    target.write_text("x = 2\n", encoding="utf-8")
    assert executor._refactor_diff_reconstructable(
        {"changed_paths": ["tracks/app.py"]}, []
    )
    # union with observed_changed covers outcomes whose changed_paths are
    # empty while the tree drifted
    assert executor._refactor_diff_reconstructable(
        {"changed_paths": []}, ["tracks/app.py"]
    )
    # paths with no on-disk change are not reconstructable (fail-closed kept)
    assert not executor._refactor_diff_reconstructable(
        {"changed_paths": ["tracks/absent.py"]}, []
    )


class _RecordingBackend:
    def __init__(self, result):
        self.result = result
        self.assignments = []

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        self.assignments.append((role, substate, assignment))
        return dict(self.result)


def test_every_m_impl_assignment_materializes_role_contracts(tmp_path):
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo)
    task = _task()
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {
            "task_count": 1,
            "tasks": [task],
            "path": "tasks.json",
            "digest": "graph",
            "validate_status": "pass",
        },
    )
    executor = _executor(repo, store)
    backend = _RecordingBackend(
        {"status": "failed", "artifact_ref": None, "self_report": "bounded"}
    )
    executor.backend = backend
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "archer",
                "substate": "PLANNING",
                "assignment": {"skills": ["tracks-discuz"]},
            },
        )
    )
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "prism",
                "substate": "PRISM_PLAN",
                "assignment": {"skills": ["tracks-prism-impl"]},
            },
        )
    )
    executor._do_select_task(Command("select_task"), store.state("RUN"), None, False)
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "devon",
                "substate": "RED",
                "assignment": {"phase": "red", "skills": ["tracks-devon-rgr"]},
            },
        )
    )
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "shield",
                "substate": "WRITE",
                "assignment": {"skills": ["tracks-discuz"]},
            },
        )
    )
    assignments = {role: assignment for role, _sub, assignment in backend.assignments}
    assert assignments["devon"]["task_id"] == "T-001"
    assert assignments["devon"]["manifest"]["allowed_paths"]
    assert assignments["devon"]["pre_dirty_snapshot"] == {}
    assert assignments["devon"]["result_identity"]
    assert assignments["devon"]["phase"] == "red"
    assert assignments["prism"]["criteria_pack"] == {
        "name": "tracks-prism-impl",
        "version": "0.1",
    }
    assert assignments["shield"]["test_tasks"] == [
        {
            "ac_id": "AC-FR0001-01",
            "layers": ["integration"],
            "if_ids": ["IF-IMPL-001"],
        }
    ]
    dispatches = [ev for ev in store.events("RUN") if ev.type == "command.issued"]
    assert all("assignment" in ev.payload["command"]["params"] for ev in dispatches)


def _structured_outcome(
    phase: str,
    changed: list[str],
    r_identity=None,
    classification: str | None = None,
    verdict: str | None = None,
    diff_ref: str | None = None,
):
    outcome = {
        "role": "devon",
        "status": "done",
        "phase": phase,
        "changed_paths": changed,
        "commands": [],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": [],
        **({"r_identity": r_identity} if r_identity else {}),
        **({"diff_ref": diff_ref} if diff_ref is not None else {}),
    }
    if classification is not None:
        outcome["results"] = [{"classification": classification}]
    if verdict is not None:
        outcome["verdict"] = verdict
    return outcome


def test_empty_red_and_green_fail_closed_without_commits(tmp_path):
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append(
        "RUN", "v0.5", "outcome.received", _structured_outcome("red", ["tests/unit/test_app.py"])
    )
    executor = _executor(repo, store)
    base = _git(repo, "rev-parse", "HEAD")
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False
    )
    assert _git(repo, "rev-parse", "HEAD") == base
    assert not any(ev.type == "red.checkpointed" for ev in store.events("RUN"))

    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN", "v0.5", "outcome.received", _structured_outcome("green", ["tracks/app.py"], "R")
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )
    assert _git(repo, "rev-parse", "HEAD") == base
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_commit_green_empty_diff_with_no_change_reason_emits_green_no_change(tmp_path):
    """B38 (#39): a GREEN resubmit with no worktree diff and an explicit
    no_change_reason (implementation already in the baseline) must emit
    green.no_change instead of cycling through impl_defect -> DIAGNOSE ->
    GREEN. No commit is performed."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome("green", [], "R")
    outcome["no_change_reason"] = "implementation already on disk from baseline"
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    base = _git(repo, "rev-parse", "HEAD")

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )

    assert _git(repo, "rev-parse", "HEAD") == base
    emitted = [ev for ev in store.events("RUN") if ev.type == "green.no_change"]
    assert len(emitted) == 1
    assert emitted[0].payload["task_id"] == "T-001"
    assert emitted[0].payload["reason"] == "implementation already on disk from baseline"
    assert not any(ev.type == "verdict.failed" for ev in store.events("RUN"))
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_commit_green_empty_diff_without_no_change_reason_stays_fail_closed(tmp_path):
    """B38 (#39): without an explicit no_change_reason, an empty captured diff
    must keep the fail-closed verdict.failed(impl_defect) — the reason is a
    programming contract, not an accident to paper over."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _structured_outcome("green", [], "R"))
    executor = _executor(repo, store)
    base = _git(repo, "rev-parse", "HEAD")

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )

    assert _git(repo, "rev-parse", "HEAD") == base
    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert len(fails) == 1
    assert fails[0].payload["check"] == "impl_defect"
    assert not any(ev.type == "green.no_change" for ev in store.events("RUN"))
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_commit_green_no_change_reconcile_idempotent(tmp_path):
    """B38 (#39): green.no_change already persisted for the command_id must
    NOT be re-emitted when reconcile=True (crash between event commit and
    return). Mirrors _do_recover_stage idempotency."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome("green", [], "R")
    outcome["no_change_reason"] = "already in baseline"
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    command = Command("commit_green", command_id="C-G-REPLAY")
    stale_state = store.state("RUN")

    executor._do_commit_green(command, stale_state, None, False)
    executor._do_commit_green(command, stale_state, None, True)

    emitted = [ev for ev in store.events("RUN") if ev.type == "green.no_change"]
    assert len(emitted) == 1


_RGR_GREEN_DIFF = (
    "diff --git a/tracks/app.py b/tracks/app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tracks/app.py\n"
    "@@ -0,0 +1 @@\n"
    "+IMPLEMENTED = True\n"
)


def _run_red_gate(executor: Executor, store: Store, outcome: dict):
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "RED_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )
    return list(store.events("RUN"))[-1]


def _impl_only_started_store(repo: Path, task: dict) -> Store:
    """task.started manifest whose allowed_paths grants impl files only.

    Mirrors T-014 of run 01KZTHE7: test_refs are frozen integration/e2e
    suites, so no test path enters allowed_paths - yet Devon's RED evidence
    is a failing test that must live in tests/unit/ (run 01KZTHE7 seq 993
    rejected it as "outside manifest").
    """
    _docs(repo)
    # project.toml [layout.devon] is the writable set the RED evidence check
    # consults to recognise Devon test dirs (tests/unit/).
    paths.project_toml_path(paths.tracks_home(repo)).parent.mkdir(
        parents=True, exist_ok=True
    )
    paths.project_toml_path(paths.tracks_home(repo)).write_text(
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='pytest --collect-only tests/unit'\n"
        "run='pytest tests/unit --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='pytest --collect-only tests/integration'\n"
        "run='pytest tests/integration --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='pytest --collect-only tests/e2e'\n"
        "run='pytest tests/e2e --junitxml={result}'\n"
        "run_selected='pytest {nodes} --junitxml={result}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit', 'integration', 'e2e']\n"
        "purpose='scheduled FULL-suite regression'\n\n"
        "[layout]\n\n"
        "[layout.devon]\nwritable=['tracks/', 'tests/unit/']\n",
        encoding="utf-8",
    )
    store = _store(repo)
    _graph(store, task)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": ["tracks/app.py"],
                "forbidden_paths": ["tests/integration/**", "tests/e2e/**"],
            },
        },
    )
    return store


def test_red_gate_accepts_devon_test_dir_evidence_for_impl_only_manifest(tmp_path):
    """Fix G: RED evidence in a Devon test dir (tests/unit/) must not be
    rejected by the allowed_paths gate when the manifest grants only impl
    paths - frozen suites stay blocked by forbidden_paths."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    last = _run_red_gate(
        executor,
        store,
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"


def test_red_gate_still_rejects_frozen_suite_evidence(tmp_path):
    """The devon-test-dir exemption must not open frozen suites: an evidence
    path under tests/integration/ is forbidden for Devon writes."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    last = _run_red_gate(
        executor,
        store,
        _structured_outcome(
            "red",
            ["tests/integration/test_frozen.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    assert last.type == "verdict.failed"
    assert last.payload["check"] == "red_invalid"
    assert "forbidden" in last.payload["reason"]


def test_rgr_public_attempt_one_and_identity_payloads(tmp_path):
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(red) == 1
    red_payload = red[0].payload
    assert red_payload["attempt"] == 1
    assert red_payload["task_id"] == task["task_id"]
    assert red_payload["ref"] == ("refs/trac/rgr/RUN/T-001/1/red")
    assert _git(repo, "rev-parse", red_payload["ref"]) == red_payload["r_sha"]

    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "green", ["tracks/app.py"], red_payload["r_sha"], diff_ref=_RGR_GREEN_DIFF
        ),
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"]
    assert len(green) == 1
    green_payload = green[0].payload
    # D-41: green.committed carries the GREEN_GATE selection evidence binding.
    # This flow never ran a passing GREEN_GATE, so both bind empty.
    assert green_payload == {
        "g_sha": green_payload["g_sha"],
        "task_id": task["task_id"],
        "attempt": 1,
        "r_sha": red_payload["r_sha"],
        "base_sha": green_payload["base_sha"],
        "trailers": {
            "Tracks-Task": task["task_id"],
            "Tracks-Attempt": "1",
            "Tracks-R": red_payload["r_sha"],
            "Tracks-Issue": "1",
            "Tracks-AC": "AC-FR0001-01,FR-0001",
        },
        "evidence_ids": [],
        "identity_basis": {},
    }
    message = _git(repo, "log", "--format=%B", "-1", green_payload["g_sha"])
    assert "Tracks-Attempt: 1" in message
    assert f"Tracks-R: {red_payload['r_sha']}" in message
    assert "Tracks-AC: AC-FR0001-01,FR-0001" in message
    assert "Tracks-FR:" not in message
    assert "Tracks-NFR:" not in message


def test_green_commit_binds_r_ref_slot_not_logical_attempt(tmp_path):
    """B56 (#72): B54's first-free-slot R allocation can place the immutable
    R ref at a slot above the logical attempt counter; G's Tracks-Attempt and
    green.committed.attempt must record that slot so TASK_REVIEW's
    verify_lineage resolves the ref the G trailer actually binds (run
    01M0S0FQ T-001: logical attempt 3, R checkpointed into slot 5, G trailer
    attempt 3 resolved the abandoned cycle's slot-3 R -> lineage park)."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    # Pre-occupy slots 1-2 with orphan refs (abandoned-cycle semantics): the
    # fresh residency's first checkpoint (logical attempt 1) must allocate
    # slot 3.
    base = _git(repo, "rev-parse", "HEAD")
    for slot in (1, 2):
        _git(repo, "update-ref", f"refs/trac/rgr/RUN/T-001/{slot}/red", base)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    assert red.payload["attempt"] == 3  # first free slot, not logical 1

    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "green", ["tracks/app.py"], red.payload["r_sha"], diff_ref=RGR_GREEN_DIFF
        ),
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"][-1]
    # G binds the R ref slot (3), not the logical attempt (1)
    assert green.payload["attempt"] == 3
    assert green.payload["trailers"]["Tracks-Attempt"] == "3"
    assert green.payload["trailers"]["Tracks-R"] == red.payload["r_sha"]
    message = _git(repo, "log", "--format=%B", "-1", green.payload["g_sha"])
    assert "Tracks-Attempt: 3" in message
    # TASK_REVIEW lineage resolves the ref the G trailer actually binds
    events = [
        {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
        for ev in store.events("RUN")
    ]
    proof = verify_lineage(
        str(repo),
        "RUN",
        task["task_id"],
        green.payload["attempt"],
        green.payload["g_sha"],
        events,
        issue_number=1,
        ac_refs=["AC-FR0001-01", "FR-0001"],
    )
    assert proof.g_trailers_valid
    assert proof.r_before_g


def test_r_lineage_attempt_falls_back_to_logical_attempt(tmp_path):
    """B56 (#72): without a matching red.checkpointed (r_sha unknown or no
    R identity at all) the lineage attempt falls back to the logical attempt
    -- pre-B54 behavior, fail-closed rather than guessing a slot."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task["task_id"], "attempt": 4, "r_sha": "R-live"},
    )
    assert executor._r_lineage_attempt(task["task_id"], "R-live", 1) == 4
    assert executor._r_lineage_attempt(task["task_id"], "R-unknown", 1) == 1
    assert executor._r_lineage_attempt(task["task_id"], None, 2) == 2
    assert executor._r_lineage_attempt(task["task_id"], "", 2) == 2


def test_green_commit_reconstructs_diff_from_cycle_outcome_union(tmp_path):
    """B58 (#74): GREEN's working-tree diff reconstruction must union the
    changed_paths of EVERY green outcome of the current RGR cycle, not just
    the last one. impl_defect re-dispatches report only their own delta (run
    01M0S0FQ T-001: dispatch 1 changed tracks/project.py, dispatch 3 changed
    tracks/adapters/base.py; the reconstructed G captured only base.py and
    the loader impl never reached any commit -- no gate caught it)."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    task["scope_boundary"] = "tracks/app.py, tracks/project.py"
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {"task_count": 1, "tasks": [task], "digest": "graph"},
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": [
                    "tracks/app.py",
                    "tracks/project.py",
                    "tests/unit/test_app.py",
                ],
                "forbidden_paths": [".tracks/projects/**"],
            },
        },
    )
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})

    # Dispatch 1's impl (no diff_ref -- OpenCode backend contract, B55) is
    # still sitting uncommitted in the main tree when dispatch 2 is re-sent
    # after a GREEN_GATE impl_defect failure.
    project = repo / "tracks" / "project.py"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text("ADAPTER = 'reference-pytest'\n", encoding="utf-8")
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/project.py"], red.payload["r_sha"]),
    )
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "impl_defect", "attempt": 1, "reason": "TypeError in gate run"},
    )
    app = repo / "tracks" / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"]),
    )

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"][-1]
    # The union reconstruction captured BOTH dispatches' files in G.
    changed = _git(repo, "show", "--name-only", "--format=", green.payload["g_sha"])
    assert "tracks/project.py" in changed
    assert "tracks/app.py" in changed


def test_green_cycle_changed_paths_stops_at_last_checkpoint(tmp_path):
    """B58 (#74): the union is bounded by the last red.checkpointed -- green
    outcomes from a superseded (red_defect-retried) cycle must not leak into
    the new cycle's diff reconstruction."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/stale.py"]),
    )
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task["task_id"], "attempt": 1, "r_sha": "R-live"},
    )
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], "R-live"),
    )
    assert executor._green_cycle_changed_paths() == ["tracks/app.py"]
    # No green outcome in the current cycle -> None (caller falls back to the
    # last outcome's own changed_paths).
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task["task_id"], "attempt": 2, "r_sha": "R-live-2"},
    )
    assert executor._green_cycle_changed_paths() is None


def test_green_union_beats_no_change_when_earlier_dispatch_left_changes(tmp_path):
    """B58 (#74) / Prism OOB A01: a last outcome declaring no_change_reason
    must NOT short-circuit into green.no_change when an earlier green dispatch
    of the same cycle still has uncommitted worktree changes -- the union
    reconstruction surfaces a non-empty diff, so the real change gets
    committed instead of being silently dropped."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    # Dispatch 1 changed tracks/app.py (still uncommitted in the tree).
    app = repo / "tracks" / "app.py"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text("x = 1\n", encoding="utf-8")
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"]),
    )
    # Dispatch 2 declares no change of its own.
    no_change = _structured_outcome("green", [], red.payload["r_sha"])
    no_change["no_change_reason"] = "already implemented"
    store.append("RUN", "v0.5", "outcome.received", no_change)
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    events = list(store.events("RUN"))
    assert not any(ev.type == "green.no_change" for ev in events)
    green = [ev for ev in events if ev.type == "green.committed"][-1]
    changed = _git(repo, "show", "--name-only", "--format=", green.payload["g_sha"])
    assert "tracks/app.py" in changed


def test_red_checkpoint_retry_uses_next_public_attempt(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "red_invalid", "attempt": 1},
    )
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="symbol_missing",
            verdict="symbol_missing",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R2"),
        store.state("RUN"),
        None,
        False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert red[0].payload["attempt"] == 2
    assert red[0].payload["ref"].endswith("/2/red")


def test_stub_token_red_is_invalid_but_legal_red_classes_pass(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    failed = _run_red_gate(
        executor,
        store,
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="stub_token_failure",
            verdict="stub_token_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "stub_token_failure" in failed.payload["reason"]
    assert failed.payload["evidence"]
    assert store.state("RUN").current_attempt == 1
    assert store.state("RUN").substate == "RED"
    assert decide(store.state("RUN")).params["attempt"] == 2

    for classification in ("assertion_failure", "symbol_missing"):
        store.append(
            "RUN",
            "v0.5",
            "outcome.received",
            _structured_outcome(
                "red",
                ["tests/unit/test_app.py"],
                classification=classification,
                verdict=classification,
                diff_ref=RGR_RED_DIFF,
            ),
        )
        executor._do_run_task_gates(
            Command("run_task_gates", {"gate": "RED_GATE"}, command_id=f"C-{classification}"),
            store.state("RUN"),
            None,
            False,
        )
        assert list(store.events("RUN"))[-1].type == "verdict.passed"


def test_malformed_red_classifications_fail_closed(tmp_path):
    cases = (
        {},
        {"results": [{"classification": None}], "verdict": "assertion_failure"},
        {"results": [{"classification": "unknown"}], "verdict": "unknown"},
        {
            "results": [
                {"classification": "assertion_failure"},
                {"classification": "symbol_missing"},
            ],
            "verdict": "assertion_failure",
        },
    )
    for index, details in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        repo = _repo(case_dir)
        store, _ = _started_task_store(repo)
        executor = _executor(repo, store)
        outcome = _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            diff_ref=RGR_RED_DIFF,
        )
        outcome.update(details)
        failed = _run_red_gate(executor, store, outcome)
        assert failed.type == "verdict.failed"
        assert failed.payload["check"] == "red_invalid"
        assert failed.payload["reason"]
        assert failed.payload["evidence"]


def test_rgr_reconcile_replay_emits_no_duplicate_events(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    command = Command("checkpoint_red", command_id="C-R-REPLAY")
    stale_state = store.state("RUN")
    executor._do_checkpoint_red(command, stale_state, None, False)
    executor._do_checkpoint_red(command, stale_state, None, True)
    assert len([ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]) == 1

    red_sha = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][0].payload[
        "r_sha"
    ]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red_sha, diff_ref=_RGR_GREEN_DIFF),
    )
    green_command = Command("commit_green", command_id="C-G-REPLAY")
    stale_green_state = store.state("RUN")
    executor._do_commit_green(green_command, stale_green_state, None, False)
    executor._do_commit_green(green_command, stale_green_state, None, True)
    assert len([ev for ev in store.events("RUN") if ev.type == "green.committed"]) == 1


def test_test_defect_uses_public_shield_write_and_commits_tests(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        {
            "role": "devon",
            "status": "failed",
            "failure_class": "agent_failed",
            "self_report": "failed",
            "audit_evidence": "execution",
        },
    )
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "test_defect", "attempt": 2},
    )
    executor = _executor(repo, store)

    class _ShieldWriteBackend:
        """Stub backend that writes a tests/ file (like the real Shield)."""

        def __init__(self):
            self.assignments = []

        def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
            self.assignments.append((role, substate, assignment))
            tests_dir = repo / "tests" / "integration"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_shield_fix.py").write_text(
                "def test_shield_fix():\n    assert True\n",
                encoding="utf-8",
            )
            return {"status": "done", "artifact_ref": None, "self_report": "fixed"}

    backend = _ShieldWriteBackend()
    executor.backend = backend
    command = decide(store.state("RUN"))
    assert command.params["role"] == "shield"
    assert command.params["substate"] == "WRITE"
    executor.issue(command)
    committed = [ev for ev in store.events("RUN") if ev.type == "test.committed"]
    assert committed, "test.committed must be emitted after Shield fix"
    assert committed[0].payload["test_count"] > 0


# ---------------------------------------------------------------------------
# T-03 "Runtime-authoritative gates" RED contract
#
# The Runtime — not the Agent — executes assigned commands in a Runtime-selected
# worktree, derives candidate changed paths from observed git/filesystem state,
# and its observed verdict is what routes the kernel. Agent-reported
# commands/results/changed_paths remain audit-only. Under D-41 Slice B the
# Runtime verdict binds a per-task SELECT_TASK execution: failed selections
# fail closed with actionable JSON evidence (selection_id/outcomes_ref/
# failed_nodes), never legacy full-unit argv/hash tokens.
# ---------------------------------------------------------------------------

_OUTSIDE_MANIFEST_DIFF = (
    "diff --git a/tooling/admin.py b/tooling/admin.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tooling/admin.py\n"
    "@@ -0,0 +1 @@\n"
    "+ADMIN = True\n"
)

_SECRET_LINE_DIFF = (
    "diff --git a/tracks/app.py b/tracks/app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tracks/app.py\n"
    "@@ -0,0 +1 @@\n"
    '+SECRET = "sk-0123456789abcdef0123456789abcdef01234567"\n'
)


def _immutable_r_checkpoint(repo: Path, task: dict, *, attempt: int = 1) -> dict:
    """Create a real immutable R commit containing the task's exact unit node
    (``tests/unit/test_app.py::test_app``, via the canonical RGR_RED_DIFF) and
    return its ``red.checkpointed`` payload. D-41: SELECT_TASK expands
    test_refs against R, so a fake sha would fail closed before any gate."""
    ref = create_red_ref(
        repo=str(repo),
        run_id="RUN",
        task_id=task["task_id"],
        attempt=attempt,
        test_diff=RGR_RED_DIFF,
        base_sha=_git(repo, "rev-parse", "HEAD"),
    )
    return {
        "ref": ref.ref,
        "r_sha": ref.sha,
        "task_id": task["task_id"],
        "attempt": attempt,
    }


def _write_if_mapped_integration_test(root) -> Path:
    """Materialize the integration node the shared test-plan §8 row maps to
    IF-IMPL-001 (target ``tests/integration/test_app.py``); SELECT_TASK unions
    it into the selected set and executes it through contract run_selected."""
    target = Path(root) / "tests" / "integration" / "test_app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def test_app():\n    assert True\n", encoding="utf-8")
    return target


def _selection_failure_evidence(payload: dict) -> dict:
    """Parse the actionable TaskSelectionFailure evidence JSON: the failed
    GREEN verdict must carry selection_id/outcomes_ref/failed_nodes."""
    evidence = json.loads(payload.get("evidence") or "{}")
    assert {"selection_id", "outcomes_ref", "failed_nodes"} <= set(evidence), (
        f"selected-test failure evidence lacks actionable identity fields: {evidence}"
    )
    assert evidence["selection_id"]
    assert evidence["outcomes_ref"].startswith(".tracks/runtime/blobs/")
    assert evidence["failed_nodes"], "a failed selection must name its failed nodes"
    return evidence


def _gate_manifest(task, *, unit_commands=None):
    """Manifest with the assigned Runtime unit command (executor schema)."""
    return {
        "task_id": task["task_id"],
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
        "unit_commands": list(unit_commands or [".venv/bin/python -m pytest -n 4 tests/unit"]),
    }


def _green_outcome(task, r_sha, *, agent_cwd=None, diff=None):
    outcome = _structured_outcome(
        "green",
        ["tracks/app.py"],
        r_identity=r_sha,
        diff_ref=diff or RGR_GREEN_DIFF,
    )
    outcome["commands"] = [
        {
            "cmd": ".venv/bin/python -m pytest -n 4 tests/unit",
            "result": "pass",
            **({"cwd": agent_cwd} if agent_cwd is not None else {}),
        }
    ]
    outcome["results"] = [{"classification": "pass"}]
    return outcome


def _write_failing_unit_test(repo: Path, *, passes: bool = False) -> Path:
    unit = repo / "tests" / "unit" / "test_app.py"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(
        "def test_app():\n    assert True\n" if passes else "def test_app():\n    assert False\n",
        encoding="utf-8",
    )
    return unit


def test_green_gate_fails_closed_on_runtime_unit_command_failure(tmp_path):
    """Contract 1: a well-formed Devon GREEN outcome self-reporting passing
    commands/results/manifest cannot cause a GREEN pass when the Runtime's
    SELECT_TASK re-execution of the task-selected unit node exits 1 (D-41).
    The verdict must be impl_defect carrying the actionable JSON evidence
    (selection_id/outcomes_ref/failed_nodes) and no G is created."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    red = _immutable_r_checkpoint(repo, task)
    store.append("RUN", "v0.5", "red.checkpointed", red)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, red["r_sha"], agent_cwd="/agent/claimed/cwd"),
    )
    _write_failing_unit_test(repo)
    _write_if_mapped_integration_test(repo)
    head_before = _git(repo, "rev-parse", "HEAD")

    executor = _executor(repo, store)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "Runtime selected unit node exits 1 -> GREEN must fail closed"
    last = fails[-1].payload
    assert last["check"] == "impl_defect"
    evidence = _selection_failure_evidence(last)
    assert [node["node"] for node in evidence["failed_nodes"]] == [
        "tests/unit/test_app.py::test_app"
    ]
    assert _git(repo, "rev-parse", "HEAD") == head_before, "no G may be created"
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_green_gate_no_change_with_reason_does_not_short_circuit(tmp_path):
    """Regression (run 01KZTHE7 T-013 attempt 2, 2026-08-15): a GREEN resubmit
    may legitimately carry no new changed_paths when the implementation is
    already on disk. The evidence check must not short-circuit with the
    stale "Devon GREEN evidence has no changed paths" false-kill; the gate
    must proceed to re-execute the Runtime unit commands, where a genuine
    defect still fails closed with an observed unit-command reason."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    red = _immutable_r_checkpoint(repo, task)
    store.append("RUN", "v0.5", "red.checkpointed", red)
    no_change_outcome = _structured_outcome(
        "green", [], r_identity=red["r_sha"], diff_ref=RGR_GREEN_DIFF
    )
    no_change_outcome["no_change_reason"] = (
        "attempt 1 implementation already on disk; resubmit unchanged"
    )
    no_change_outcome["commands"] = [
        {"cmd": ".venv/bin/python -m pytest -n 4 tests/unit", "result": "pass"}
    ]
    no_change_outcome["results"] = [{"classification": "pass"}]
    store.append("RUN", "v0.5", "outcome.received", no_change_outcome)
    _write_failing_unit_test(repo)
    _write_if_mapped_integration_test(repo)

    executor = _executor(repo, store)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "failing unit test on disk must still fail GREEN closed"
    last = fails[-1].payload
    assert last["check"] == "impl_defect"
    assert "no changed paths" not in last.get("reason", ""), (
        "no-change GREEN with reason must not hit the stale false-kill"
    )
    evidence = _selection_failure_evidence(last)
    assert [node["node"] for node in evidence["failed_nodes"]] == [
        "tests/unit/test_app.py::test_app"
    ], "the gate must proceed to the SELECT_TASK re-execution and fail there"


def test_green_gate_not_regression_when_r_unit_test_mutation_not_claimed(tmp_path):
    """B39 (run 01M0AMKV T-006 seq 728): regression is judged ONLY on this
    task's GREEN evidence (Devon changed_paths) touching an R-frozen tests/
    file — never on the whole worktree-vs-R tests/ diff. A hidden mutation of
    the frozen R unit test that Devon did NOT report (changed_paths only
    tracks/app.py) is no longer misjudged as task cheating: the gate proceeds
    to the Runtime unit re-execution instead of emitting `regression`."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    # Candidate state the Runtime observes: a tests/ file differing from R,
    # but Devon's changed_paths reports only tracks/app.py.
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8")
    _write_failing_unit_test(repo, passes=True)
    _write_if_mapped_integration_test(repo)

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert not fails, "unclaimed tree-vs-R tests diff must NOT fail GREEN as regression"
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert passed and passed[-1].payload["check"] == "green"


def _green_gate_with_r_checkpoint(
    repo, store, executor, *, changed_paths=None, r_sha=None, diff_ref=None
):
    """Checkpoint R from a genuine RED outcome, then stage a GREEN outcome
    (with the R-frozen test materialized as a passing test so the Runtime unit
    re-execution succeeds and the gate reaches the regression decision). The
    caller runs the gate and drives additional working-tree setup first."""
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    red = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    resolved = r_sha or (red[-1].payload["r_sha"] if red else None)
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome(
        "green",
        changed_paths or ["tracks/app.py"],
        r_identity=resolved,
        diff_ref=diff_ref if diff_ref is not None else RGR_GREEN_DIFF,
    )
    outcome["commands"] = [
        {"cmd": ".venv/bin/python -m pytest -n 4 tests/unit", "result": "pass"}
    ]
    outcome["results"] = [{"classification": "pass"}]
    store.append("RUN", "v0.5", "outcome.received", outcome)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8")
    _write_failing_unit_test(repo, passes=True)
    return resolved


def test_green_gate_regression_when_devon_reports_changed_r_frozen_test(tmp_path):
    """B39: the narrowed regression must still catch task cheating — a GREEN
    outcome whose changed_paths claims an R-frozen tests/ file that exists in
    the R tree fails GREEN closed with `regression` and names the file."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(
        repo,
        store,
        executor,
        changed_paths=["tests/unit/test_app.py", "tracks/app.py"],
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "GREEN evidence touching an R-frozen tests/ file must fail GREEN closed"
    assert fails[-1].payload["check"] == "regression"
    evidence = fails[-1].payload.get("evidence") or ""
    assert "tests/unit/test_app.py" in evidence, "evidence must name the R-frozen test"
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_green_gate_regression_when_captured_diff_touches_r_test_unclaimed(tmp_path):
    """B39 supplement: the Runtime's authoritative captured diff (outcome
    diff_ref, parsed from `diff --git` headers — not Devon's changed_paths)
    must catch a hidden mutation of an R-frozen tests/ file that Devon did
    NOT claim. The union (captured_paths ∪ claimed paths) closes the
    self-report gap instead of re-opening the seq 728 false-positive."""
    _HIDDEN_R_TEST_MUTATION_DIFF = (
        "diff --git a/tracks/app.py b/tracks/app.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tracks/app.py\n"
        "@@ -0,0 +1 @@\n"
        "+IMPLEMENTED = True\n"
        "diff --git a/tests/unit/test_app.py b/tests/unit/test_app.py\n"
        "--- a/tests/unit/test_app.py\n"
        "+++ b/tests/unit/test_app.py\n"
        "@@ -1 +1 @@\n"
        "-def test_app(): assert False\n"
        "+def test_app(): assert True\n"
    )
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(
        repo,
        store,
        executor,
        changed_paths=["tracks/app.py"],
        diff_ref=_HIDDEN_R_TEST_MUTATION_DIFF,
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, (
        "captured-diff mutation of an R-frozen test (unclaimed by Devon) "
        "must fail GREEN closed"
    )
    assert fails[-1].payload["check"] == "regression"
    evidence = fails[-1].payload.get("evidence") or ""
    assert "tests/unit/test_app.py" in evidence, "evidence must name the hidden-mutated test"
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_green_gate_not_regression_on_tests_added_after_r(tmp_path):
    """B39 (run 01M0AMKV T-006 seq 728 repro): tests/ files added to the
    baseline AFTER R (ops fixes, earlier batch siblings) must not fail GREEN —
    the tree differs from r_sha in tests/, but Devon's changed_paths contains
    no R-frozen tests/ path."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(repo, store, executor, changed_paths=["tracks/app.py"])
    _write_if_mapped_integration_test(repo)
    (repo / "tests" / "unit" / "test_ops_fix.py").write_text(
        "def test_ops_fix():\n    assert True\n", encoding="utf-8"
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    assert not any(ev.type == "verdict.failed" for ev in store.events("RUN"))
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert passed and passed[-1].payload["check"] == "green"


def test_green_gate_not_regression_when_reported_test_not_in_r(tmp_path):
    """B39: a new tests/ file that does not exist in the R tree (RED-added test
    later adjusted) is outside the frozen R set; GREEN_GATE only protects the
    R-frozen tests, later adjustments are governed by test_defect/SHIELD_FIX."""
    repo = _repo(tmp_path)
    _contract(repo)
    task = _task()
    manifest = {
        "task_id": task["task_id"],
        "allowed_paths": [
            "tracks/app.py",
            "tests/unit/test_app.py",
            "tests/unit/test_extra.py",
        ],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store = _store(repo)
    from tests.unit.helpers import m_impl_graph
    raw = json.dumps({"tasks": [task]}, sort_keys=True)
    m_impl_graph(store, task, raw=raw)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": manifest},
    )
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(
        repo,
        store,
        executor,
        changed_paths=["tracks/app.py", "tests/unit/test_extra.py"],
    )
    (repo / "tests" / "unit" / "test_extra.py").write_text(
        "def test_extra():\n    assert True\n", encoding="utf-8"
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert not fails, "reported test file not in R tree must NOT trigger regression"
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert passed and passed[-1].payload["check"] == "green"


def test_green_gate_ambiguous_r_identity_fails_closed_as_contract_error(tmp_path):
    """D-41: an absent/all-zero R identity is ambiguity, not a skip — the
    SELECT_TASK baseline cannot be trusted, so the gate fails closed with
    contract_error (routed to DIAGNOSE) instead of skipping the regression
    check and passing."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"r_sha": "0" * 40, "task_id": task["task_id"], "attempt": 1},
    )
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome(
        "green", ["tests/unit/test_app.py", "tracks/app.py"], r_identity="0" * 40
    )
    outcome["commands"] = [{"cmd": ".venv/bin/python -m pytest -n 4 tests/unit", "result": "pass"}]
    outcome["results"] = [{"classification": "pass"}]
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8")
    _write_failing_unit_test(repo, passes=True)

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "ambiguous all-zero R identity must fail closed"
    last = fails[-1].payload
    assert last["check"] == "contract_error"
    assert "SELECT_TASK failed closed" in last["reason"]
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert not passed, "an ambiguous R identity must never pass GREEN"
    # B49 (#64): contract_error parks for the operator instead of routing
    # into DIAGNOSE (the stub_gap auto-rollback loop, run 01M0S0FQ).
    state = store.state("RUN")
    assert state.substate == "GREEN_GATE"
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"


def test_refactor_no_change_fails_closed_without_persisted_green_evidence(tmp_path):
    """D-41 supersession of Contract 3: REFACTOR `no_change` no longer blind-
    reruns the Green gate — it reuses the persisted green.committed
    evidence/identity. Without that persisted evidence the reuse state is
    unresolvable, so the gate fails closed with contract_error and parks for
    the operator (B49: awaiting_human/escalation) instead of emitting
    refactor.no_change or re-executing anything."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    outcome = _structured_outcome("refactor", [], r_identity="1" * 40)
    outcome["no_change_reason"] = "no improvements found"
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    executor._do_run_refactor_gate(
        Command("run_refactor_gate", {"stage": "M-IMPL"}, command_id="C-RF"),
        store.state("RUN"),
        None,
        False,
    )
    events = list(store.events("RUN"))
    assert not any(ev.type == "refactor.no_change" for ev in events), (
        "without persisted green.committed evidence/identity no refactor.no_change "
        "may be emitted"
    )
    fails = [ev for ev in events if ev.type == "verdict.failed"]
    assert fails, "unresolvable green reuse state must fail closed"
    assert fails[-1].payload["check"] == "contract_error"
    assert "REFACTOR SELECT_TASK failed closed" in fails[-1].payload["reason"]
    assert not any(ev.type == "test.selected" for ev in events), (
        "the gate must not re-execute any selection without persisted evidence"
    )
    # B49 (#64): contract_error parks instead of DIAGNOSE routing -- the
    # substate stays wherever the gate ran from.
    state = store.state("RUN")
    assert state.substate != "DIAGNOSE"
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"


_TASK_REVIEW_FLAWS = (
    "scope_overflow",
    "lineage",
    "secret_line",
    "missing_ac",
    "budget_overflow",
)


def _task_review_scenario(repo, flaw):
    """Shared TASK_REVIEW fixture reaching the review gate with one observable
    defect in the Runtime-authoritative candidate/lineage state.

    The Green commit is real (create_green_commit on the immutable R commit's
    derived base B) and its diff/trailers carry exactly the named flaw:
    scope_overflow -> actual G diff is only an outside-manifest path;
    secret_line -> actual G diff is only a secret-shaped added line on an
    allowed path; missing_ac -> actual G has an empty combined provenance
    trailer; lineage -> actual G carries a wrong Tracks-R; budget_overflow ->
    budget history only (G otherwise valid)."""
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    base_sha = red_base_sha(str(repo), r_sha)
    assert base_sha is not None, "immutable R commit must resolve its base B"
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    if flaw == "scope_overflow":
        impl_diff = _OUTSIDE_MANIFEST_DIFF
    elif flaw == "secret_line":
        impl_diff = _SECRET_LINE_DIFF
    else:
        impl_diff = RGR_GREEN_DIFF
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha, diff=impl_diff))
    store.append("RUN", "v0.5", "verdict.passed", {"check": "green"})
    if flaw == "budget_overflow":
        # Inflate the consumed-attempt budget beyond the task budget (=3).
        for attempt in range(1, 5):
            store.append("RUN", "v0.5", "verdict.failed", {"check": "budget", "attempt": attempt})
    trailer_r = "0" * 40 if flaw == "lineage" else r_sha
    ac_refs = [] if flaw == "missing_ac" else ["AC-FR0001-01", "FR-0001"]
    g = create_green_commit(
        repo=str(repo),
        run_id="RUN",
        task_id=task["task_id"],
        attempt=1,
        impl_diff=impl_diff,
        base_sha=base_sha,
        r_sha=trailer_r,
        issue_number=task["issue_number"],
        ac_refs=ac_refs,
    )
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "g_sha": g.sha,
            "task_id": task["task_id"],
            "attempt": 1,
            "r_sha": r_sha,
            "base_sha": base_sha,
            "trailers": g.trailers,
        },
    )
    _git(repo, "reset", "--hard", g.sha)
    store.append(
        "RUN",
        "v0.5",
        "refactor.no_change",
        {"task_id": task["task_id"], "reason": "no improvements"},
    )
    return executor, store


@pytest.mark.parametrize("flaw", _TASK_REVIEW_FLAWS)
def test_task_review_is_not_unconditional(tmp_path, flaw):
    """Contract 4: TASK_REVIEW is not unconditional. Observed scope overflow,
    exact-lineage failure, secret-shaped added line, missing AC/provenance, or
    budget overflow must fail closed (verdict.failed, never verdict.passed)."""
    repo = _repo(tmp_path)
    executor, store = _task_review_scenario(repo, flaw)
    assert store.state("RUN").substate == "TASK_REVIEW"
    before = len(list(store.events("RUN")))
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        store.state("RUN"),
        None,
        False,
    )
    new_events = list(store.events("RUN"))[before:]
    assert new_events, f"{flaw}: TASK_REVIEW must emit a verdict"
    assert new_events[-1].type == "verdict.failed", (
        f"{flaw}: TASK_REVIEW must fail closed, got {new_events[-1].type}"
    )
    assert new_events[-1].payload.get("reason"), f"{flaw}: TASK_REVIEW failure must carry a reason"


def test_gate_commands_run_in_runtime_selected_worktree_cwd(tmp_path):
    """Contract 5: gate commands run in the Runtime-selected gate/candidate
    worktree cwd — never the cwd an Agent reports. Agent-reported commands and
    results stay audit-only. Under D-41 the proof is behavioral: the selected
    unit node exists ONLY in the prepared Runtime worktree, so an impl_defect
    verdict whose failed_nodes name that node proves the SELECT_TASK execution
    ran there (a main-repo fallback would fail closed as contract_error with
    an empty inventory)."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    red = _immutable_r_checkpoint(repo, task)
    store.append("RUN", "v0.5", "red.checkpointed", red)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, red["r_sha"], agent_cwd="/agent/claimed/cwd"),
    )
    base = _git(repo, "rev-parse", "HEAD")
    gate = create_gate_worktree(str(repo), base, "", "", "RUN", task["task_id"])
    try:
        _write_failing_unit_test(Path(gate.path))
        _write_if_mapped_integration_test(Path(gate.path))
        executor = _executor(repo, store)
        executor._do_run_task_gates(
            Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
            store.state("RUN"),
            None,
            False,
        )
        fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
        assert fails, "the Runtime gate must execute and fail closed"
        last = fails[-1].payload
        assert last["check"] == "impl_defect"
        evidence = _selection_failure_evidence(last)
        assert [node["node"] for node in evidence["failed_nodes"]] == [
            "tests/unit/test_app.py::test_app"
        ], (
            "the selected node failed where only the prepared Runtime worktree "
            "has it - the gate ran in that worktree"
        )
        audit_outcomes = [
            ev.payload for ev in store.events("RUN") if ev.type == "outcome.received"
        ]
        assert "/agent/claimed/cwd" in json.dumps(audit_outcomes), (
            "agent-reported cwd should remain preserved as audit-only evidence"
        )
        runtime_evidence = [
            ev.payload
            for ev in store.events("RUN")
            if ev.type in ("test.selected", "verdict.failed", "verdict.passed")
        ]
        assert "/agent/claimed/cwd" not in json.dumps(runtime_evidence), (
            "agent-reported cwd must never become Runtime execution evidence"
        )
    finally:
        cleanup_worktree(gate)


def test_task_review_budget_respects_retry_cutoff(tmp_path):
    """Regression (run 01KZTHE7 T-013, 2026-08-16): verdict.failed events
    recorded BEFORE a human.retry are superseded (FR-11 budget reset) and
    must not fail TASK_REVIEW's budget check. The budget_overflow flaw still
    fails when no retry intervenes."""
    repo = _repo(tmp_path)
    executor, store = _task_review_scenario(repo, "budget_overflow")
    store.append("RUN", "v0.5", "human.retry", {})
    state = store.state("RUN")
    assert state.substate == "TASK_REVIEW", "retry must not leave the review substate"
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        state,
        None,
        False,
    )
    budget_fails = [
        ev
        for ev in store.events("RUN")
        if ev.type == "verdict.failed"
        and ev.payload.get("check") == "budget"
        and ev.seq > max(e.seq for e in store.events("RUN") if e.type == "human.retry")
    ]
    assert not budget_fails, (
        "pre-retry verdict.failed must not fail the post-retry budget check"
    )


def test_m_impl_event_recorded_respects_retry_cutoff(tmp_path):
    """Regression (run 01KZTHE7 T-013, 2026-08-16): `trac retry` (FR-11)
    resets the attempt budget, so a post-retry attempt number N is a fresh
    attempt, not the pre-retry attempt N. Idempotency scans must only
    consider events strictly after the last human.retry."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "g_sha": "a" * 40,
            "task_id": task["task_id"],
            "attempt": 1,
            "r_sha": "b" * 40,
            "base_sha": "c" * 40,
            "trailers": [],
        },
    )
    executor = _executor(repo, store)
    assert executor._m_impl_event_recorded("green.committed", task["task_id"], 1), (
        "without a retry the recorded event must be visible"
    )
    store.append("RUN", "v0.5", "human.retry", {"task_id": task["task_id"]})
    assert not executor._m_impl_event_recorded("green.committed", task["task_id"], 1), (
        "after human.retry the pre-retry event is superseded"
    )
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "g_sha": "d" * 40,
            "task_id": task["task_id"],
            "attempt": 1,
            "r_sha": "e" * 40,
            "base_sha": "f" * 40,
            "trailers": [],
        },
    )
    assert executor._m_impl_event_recorded("green.committed", task["task_id"], 1), (
        "a fresh post-retry event must be visible again"
    )


def test_commit_green_stale_prefailure_does_not_block_after_retry(tmp_path):
    """Regression (run 01KZTHE7 T-013, 2026-08-16): a verdict.failed(
    impl_defect, attempt=1) recorded BEFORE human.retry must not false-
    positive the post-retry fresh attempt=1 with "Runtime gate already
    failed for this attempt"."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {
            "check": "impl_defect",
            "reason": "stale pre-retry failure",
            "evidence": "legacy",
            "task_id": task["task_id"],
            "attempt": 1,
        },
    )
    store.append("RUN", "v0.5", "human.retry", {"task_id": task["task_id"]})
    state = store.state("RUN")
    assert state.current_attempt == 0, "retry must reset the attempt budget"

    executor = _executor(repo, store)
    before = len(list(store.events("RUN")))
    executor._do_commit_green(
        Command("commit_green", {"task_id": task["task_id"]}, command_id="C-CG"),
        state,
        task["task_id"],
        None,
    )
    new_events = list(store.events("RUN"))[before:]
    stale_blocks = [
        ev
        for ev in new_events
        if ev.type == "verdict.failed"
        and ev.payload.get("reason") == "Runtime gate already failed for this attempt"
    ]
    assert not stale_blocks, (
        "pre-retry verdict.failed must not block the post-retry fresh attempt"
    )


def test_red_classification_inferred_from_commands_when_results_missing(tmp_path):
    """Devon RED outcomes that omit `results` should still pass RED_GATE when
    `commands[*].output_summary` carries a legal `classify_red -> <token>`."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [
        {
            "cmd": "pytest",
            "output_summary": (
                "37 failed / 0 passed, exit 1; runtime classify_red -> "
                "assertion_failure (legal M-IMPL red)"
            ),
        }
    ]
    outcome["verdict"] = "assertion_failure"
    event = _run_red_gate(executor, store, outcome)
    assert event.type == "verdict.passed"
    assert event.payload["check"] == "red_valid"


def test_red_classification_inference_fails_when_no_pattern_in_commands(tmp_path):
    """An empty/legal `commands` list with no `classify_red` token must NOT
    be inferred - the gate stays fail-closed with `classifications missing`."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [{"cmd": "pytest", "output_summary": "all good"}]
    outcome["verdict"] = "assertion_failure"
    failed = _run_red_gate(executor, store, outcome)
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "missing" in failed.payload["reason"]


def test_red_classification_inference_rejects_stub_token(tmp_path):
    """`classify_red -> stub_token_failure` must NOT be inferred; only
    assertion_failure/symbol_missing match. Ensures the counterexample patch
    (`red_classification.patch`) keeps failing."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [
        {"cmd": "pytest", "output_summary": "classify_red -> stub_token_failure"}
    ]
    outcome["verdict"] = "stub_token_failure"
    failed = _run_red_gate(executor, store, outcome)
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "missing" in failed.payload["reason"]


def test_red_classification_inference_mixed_fails(tmp_path):
    """When commands disagree (assertion_failure vs symbol_missing) the
    existing `mixed classifications` check must still fire."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [
        {"cmd": "pytest", "output_summary": "classify_red -> assertion_failure"},
        {"cmd": "pytest", "output_summary": "classify_red -> symbol_missing"},
    ]
    outcome["verdict"] = "assertion_failure"
    failed = _run_red_gate(executor, store, outcome)
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "mixed" in failed.payload["reason"]


def test_validated_diff_generates_from_changed_paths_when_diff_ref_missing(tmp_path):
    """When `diff_ref` is absent but `changed_paths` references a file that
    exists on disk, the gate must reconstruct the diff via `git add -N` +
    `git diff` instead of failing `no captured diff_ref`."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    test_file = repo / "tests" / "unit" / "test_app.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_app():\n    assert False\n", encoding="utf-8")
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
    )
    outcome.pop("diff_ref", None)
    event = _run_red_gate(executor, store, outcome)
    assert event.type == "verdict.passed"
    assert event.payload["check"] == "red_valid"


def test_devon_red_test_dirs_helper(tmp_path):
    """The RED write-grant helper derives Devon test dirs from
    project.toml [layout.devon] (rstripped, same source as _unit_commands)."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    assert executor._devon_red_test_dirs() == ["tests/unit"]


def test_manifest_includes_red_test_paths_for_impl_only_task(tmp_path):
    """Regression (T-016 manifest deadlock): an impl-only task (test_refs all
    integration/e2e) must still publish red_test_paths and a phase_rules.red
    that names it, so Devon knows RED unit tests live in tests/unit/ even
    though allowed_paths (green impl scope) grants no test path."""
    repo = _repo(tmp_path)
    task = _task("tests/integration/test_impl.py::test_impl")
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    state = store.state("RUN")
    task_node = executor._task_node(task)
    manifest = executor._task_manifest(task_node, state)
    assert manifest["allowed_paths"] == ["tracks/app.py"]
    assert manifest["red_test_paths"] == ["tests/unit"]
    assert "red_test_paths" in manifest["phase_rules"]["red"]


def test_assignment_red_injects_red_test_paths_for_stale_manifest(tmp_path):
    """Regression (T-016 retry): the manifest persists at task.started, so a
    retry reuses a stale manifest without red_test_paths. Devon RED dispatch
    must inject red_test_paths at the assignment level and refresh the stale
    manifest's phase_rules.red so the retry sees the new contract."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    assignment = {
        "phase": "red",
        "manifest": {
            "task_id": task["task_id"],
            "allowed_paths": ["tracks/app.py"],
            "forbidden_paths": [],
            "phase_rules": {
                "red": "write failing unit tests only",
                "green": "write implementation only; keep R tests immutable",
                "refactor": "quality-only changes; preserve green behavior",
            },
        },
    }
    executor._add_assignment_role_fields(
        assignment, store.state("RUN"), {"role": "devon", "substate": "RED"}
    )
    assert assignment["red_test_paths"] == ["tests/unit"]
    assert assignment["manifest"]["red_test_paths"] == ["tests/unit"]
    assert "red_test_paths" in assignment["manifest"]["phase_rules"]["red"]


def test_red_classification_infers_assertion_failure_from_natural_language():
    """Fix L regression (T-016 attempt 2, 2026-08-16): Devon RED outcome
    omits `results` and uses natural-language failure descriptions instead
    of the `classify_red ->` token. Keyword inference must still recognize
    the legal `assertion_failure` classification so RED_GATE does not waste
    an attempt budget on a bogus `red_invalid` verdict.

    Logic-chain check (spec item (c)):
    - cmd1 (fail, full real summary): no assembly-error substring; no symbol
      pattern; `assert` keyword present -> assertion_failure.
    - cmd2 (fail, "no collection/import errors"): the `collection error`
      pattern needs a literal "collection error" substring; the summary
      writes "collection/import errors" (slash, not space) so it does NOT
      match; `ImportError` is case-sensitive and the summary has lowercase
      "import" only, so it does NOT match -> not assembly, not symbol, and
      no `assert` keyword -> infers nothing.
    - cmd3 (pass guard): result != "fail" -> skipped even though its
      summary contains `assert`.
    """
    outcome = {
        "role": "devon",
        "status": "done",
        "phase": "red",
        "changed_paths": ["tests/unit/test_executor_doc_gap.py"],
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest -n 4 tests/unit/test_executor_doc_gap.py --tb=short -q",
                "result": "fail",
                "output_summary": (
                    "6 collected: 2 passed (AC-FR0234-04 no-delta control, "
                    "AC-NFR0090-02 open-thread guard), 4 failed on §1m contract "
                    "tokens: test_legal_discussion_pauses_outcome_before_ordinary_validation "
                    "(assert len(doc_comment.detected)==0==1), "
                    "test_illegal_body_edit_is_rejected_atomically_as_over_reach "
                    "(assert len(outcome.rejected)==0==1), "
                    "test_illegal_body_edit_takes_precedence_over_legal_discussion "
                    "(assert len(outcome.rejected)==0==1), "
                    "test_closed_thread_resumes_with_new_dispatch_and_attempt "
                    "(assert (0+0)==1 on outcome.restored|discarded). Failures are "
                    "missing-event contract gaps in executor doc-gap wiring, not "
                    "assembly errors (control test passes, fixtures sound)."
                ),
            },
            {
                "cmd": ".venv/bin/python -m pytest -n 4 tests/unit --tb=short -q",
                "result": "fail",
                "output_summary": (
                    "Full unit suite: only the 4 doc-gap tests above fail; all "
                    "other unit tests pass. New RED test file introduces no "
                    "collection/import errors or regressions elsewhere."
                ),
            },
            {
                "cmd": ".venv/bin/python -m ruff check .",
                "result": "pass",
                "output_summary": "All checks passed.",
            },
        ],
        "manifest_compliance": True,
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["assertion_failure"]
    assert has_results is True
    assert _m_impl_red_classification_error(outcome) is None


def test_red_classification_keyword_inference_skips_assembly_errors():
    """Assembly errors (collection/import/fixture) are never inferred as a
    legal RED classification - the failure is illegit and should route to
    collection_error, not assertion_failure/symbol_missing."""
    outcome = {
        "phase": "red",
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest tests/unit/test_x.py -q",
                "result": "fail",
                "output_summary": (
                    "ERROR collecting tests/unit/test_x.py collection error: "
                    'cannot import name "_fixture"'
                ),
            }
        ],
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["missing"]
    assert has_results is False


def test_red_classification_keyword_inference_symbol_missing():
    """AttributeError on a missing product-code symbol is the canonical
    `symbol_missing` RED failure: the test runs, but the product object
    lacks the expected attribute/method. Must infer `symbol_missing`, not
    `assertion_failure` and not assembly error."""
    outcome = {
        "phase": "red",
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest tests/unit/test_doc_gap.py -q",
                "result": "fail",
                "output_summary": (
                    "AttributeError: 'Executor' object has no attribute "
                    "'_route_doc_comment_first' - the doc-gap branch is "
                    "not yet wired in the executor dispatch."
                ),
            }
        ],
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["symbol_missing"]
    assert has_results is True
    assert _m_impl_red_classification_error(outcome) is None


def test_red_classification_keyword_inference_ignores_passing_guards():
    """Passing guard commands (ruff, git status) are not RED evidence even
    if their output_summary mentions `assert`. Only `result: "fail"`
    commands are inspected by the keyword-inference fallback."""
    outcome = {
        "phase": "red",
        "commands": [
            {
                "cmd": ".venv/bin/python -m ruff check .",
                "result": "pass",
                "output_summary": "All checks passed. assert count is fine.",
            }
        ],
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["missing"]
    assert has_results is False


# ---------------------------------------------------------------------------
# B4 (issue #5): Archer-declared lint validation at RED_GATE / GREEN_GATE
# ---------------------------------------------------------------------------


def _add_lint_section(repo: Path, check: str) -> None:
    """(Re)write the [lint] section -- replace, never append: a duplicate
    TOML table would make the whole contract unloadable."""
    contract = paths.project_toml_path(paths.tracks_home(repo))
    text = contract.read_text(encoding="utf-8")
    marker = text.find("\n[lint]\n")
    if marker != -1:
        text = text[:marker]
    contract.write_text(text + f"\n[lint]\ncheck = '{check}'\n", encoding="utf-8")


def _fake_linter(repo: Path, *, exit_code: int, output: str) -> str:
    script = repo / "fake_lint.sh"
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' {output!r}\nexit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return "./fake_lint.sh"


def _red_gate_lint_store(repo: Path):
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    return store, task


def test_red_gate_lint_findings_fail_with_check_lint(tmp_path):
    """B4 (issue #5): RED deliverables with declared-lint findings fail the
    gate as check=lint — mechanical errors surface in the phase that made
    them instead of detonating at commit time (T-018 burned 3 attempts)."""
    repo = _repo(tmp_path)
    _contract(repo)
    linter = _fake_linter(
        repo,
        exit_code=1,
        output="tests/unit/test_app.py:28:1: F401 'textwrap' imported but unused",
    )
    _add_lint_section(repo, linter)
    store, _task = _red_gate_lint_store(repo)
    _write_failing_unit_test(repo)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
        diff_ref=RGR_RED_DIFF,
    )
    executor = _executor(repo, store)

    last = _run_red_gate(executor, store, outcome)

    assert last.type == "verdict.failed"
    assert last.payload["check"] == "lint"
    assert "F401" in last.payload["evidence"]


def test_red_gate_clean_lint_and_missing_tool_fail_open(tmp_path):
    """B4 (issue #5): a clean declared linter leaves the gate untouched; a
    missing/unrunnable linter is skipped fail-open with an audit note on the
    passed verdict (hygiene tooling must not block the pipeline)."""
    repo = _repo(tmp_path)
    _contract(repo)
    clean = _fake_linter(repo, exit_code=0, output="")
    _add_lint_section(repo, clean)
    store, _task = _red_gate_lint_store(repo)
    _write_failing_unit_test(repo)
    executor = _executor(repo, store)

    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
        diff_ref=RGR_RED_DIFF,
    )
    last = _run_red_gate(executor, store, outcome)
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"
    assert "lint" not in last.payload

    _add_lint_section(repo, "./no_such_linter")
    last = _run_red_gate(executor, store, outcome)
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"
    assert last.payload["lint"].startswith("lint skipped")


def test_red_gate_without_lint_contract_skips_lint(tmp_path):
    """No [lint] section -> no lint at all (language-neutral default; existing
    projects keep their gate behavior byte-compatibly)."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, _task = _red_gate_lint_store(repo)
    _write_failing_unit_test(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
        diff_ref=RGR_RED_DIFF,
    )
    last = _run_red_gate(executor, store, outcome)
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"
    assert "lint" not in last.payload


def test_green_gate_lint_findings_fail_with_check_lint(tmp_path):
    """B4 (issue #5): GREEN lints the delivered non-test sources with the
    declared command; findings fail the gate as check=lint."""
    repo = _repo(tmp_path)
    _contract(repo)
    linter = _fake_linter(repo, exit_code=1, output="tracks/app.py:1:1: E501 line too long")
    _add_lint_section(repo, linter)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    red = _immutable_r_checkpoint(repo, task)
    store.append("RUN", "v0.5", "red.checkpointed", red)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, red["r_sha"]),
    )
    _write_failing_unit_test(repo, passes=True)
    _write_if_mapped_integration_test(repo)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text(
        'IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8"
    )
    executor = _executor(repo, store)

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "lint findings must fail the GREEN gate closed"
    assert fails[-1].payload["check"] == "lint"
    assert "E501" in fails[-1].payload["evidence"]


# -- B50 (#65): declared-binding gate layer routing -----------------------------


def _runtime(tmp_path) -> Executor:
    """Bare executor for the pure ref-resolution helpers (no event history
    needed: _expand_unit_refs/_acceptance_anchors/_integration_row_targets
    are inventory-in/selection-out functions)."""
    repo = _repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    return Executor(store, repo, "RUN")


def test_expand_unit_refs_node_exact_and_file_expansion(tmp_path):
    """NODE ref selects exactly that node; FILE ref expands to every
    collected node in the file (explicit whole-file declaration)."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/unit/test_a.py::test_one",
        "tests/unit/test_a.py::test_two",
        "tests/unit/test_b.py::test_three",
    ]
    assert executor._expand_unit_refs(
        ["tests/unit/test_a.py::test_one"], inventory
    ) == ["tests/unit/test_a.py::test_one"]
    assert executor._expand_unit_refs(["tests/unit/test_a.py"], inventory) == [
        "tests/unit/test_a.py::test_one",
        "tests/unit/test_a.py::test_two",
    ]


def test_expand_unit_refs_absent_ref_fails_closed(tmp_path):
    """B50 fail-closed: a unit_ref absent from the unit collect raises
    TestSelectError (both unknown file and unknown node variants)."""
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    inventory = ["tests/unit/test_a.py::test_one"]
    with pytest.raises(TestSelectError, match="absent from unit collect"):
        executor._expand_unit_refs(["tests/unit/test_missing.py"], inventory)
    with pytest.raises(TestSelectError, match="absent from unit collect"):
        executor._expand_unit_refs(["tests/unit/test_a.py::test_other"], inventory)


def test_acceptance_anchors_resolve_and_schema1_skips_cross_check(tmp_path):
    """Legacy (schema-1) graphs resolve their mapped acceptance refs against
    the integration inventory without the §8 cross-check."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    # No test-plan text at all: schema-1 must still resolve (skip cross-check).
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py::test_flow_a"],
        inventory,
        "",
        schema=1,
    )
    assert anchors == ["tests/integration/test_app.py::test_flow_a"]
    # FILE ref expands to the whole file.
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py"], inventory, "", schema=1
    )
    assert anchors == inventory


def test_acceptance_anchors_schema2_absent_from_inventory_fails_closed(tmp_path):
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    with pytest.raises(TestSelectError, match="absent from integration collect"):
        executor._acceptance_anchors(
            ["tests/integration/test_app.py::test_flow_a"],
            ["tests/integration/test_other.py::test_x"],
            "",
            schema=2,
        )


_PLAN_WITH_TWO_FLOWS = (
    "# Test plan\n\n## 8. AC Coverage\n\n"
    "| AC id | layer | test | IF |\n|---|---|---|---|\n"
    "| AC-FR0001-01 | integration | "
    "`tests/integration/test_app.py::test_flow_a` + "
    "`tests/integration/test_app.py::test_flow_b` | IF-IMPL-001 |\n"
)


def test_acceptance_anchors_schema2_declared_must_be_planned_row_target(tmp_path):
    """Schema-2 cross-check: a declared anchor absent from every §8
    integration row target fails closed (binding is declared AND planned)."""
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    # flow_a IS a §8 row target -> resolves.
    assert executor._acceptance_anchors(
        ["tests/integration/test_app.py::test_flow_a"],
        inventory,
        _PLAN_WITH_TWO_FLOWS,
        schema=2,
    ) == ["tests/integration/test_app.py::test_flow_a"]
    # A node that exists in the inventory but is NOT a §8 target -> fail.
    with pytest.raises(TestSelectError, match="not a test-plan §8 integration row target"):
        executor._acceptance_anchors(
            ["tests/integration/test_app.py::test_flow_b", "tests/integration/test_app.py::test_flow_a"],
            inventory,
            _PLAN_WITH_TWO_FLOWS.replace("test_flow_b", "test_flow_c"),
            schema=2,
        )


def test_acceptance_anchors_schema2_file_declaration_covers_planned_nodes(tmp_path):
    """PRISM-B49B50-R1-01 mirror: a FILE-level declaration against a §8 row
    that names individual NODE targets must pass the gate cross-check --
    same whole-file coverage the commit-time closure applies."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py"],
        inventory,
        _PLAN_WITH_TWO_FLOWS,
        schema=2,
    )
    assert anchors == inventory


def test_acceptance_anchors_schema2_node_declaration_with_file_target_row(tmp_path):
    """PRISM-B49B50-R1-01 mirror (reverse): a NODE declaration against a §8
    row whose target is the whole FILE is planned (file target subsumes its
    nodes)."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    plan_file_target = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_app.py | "
        "IF-IMPL-001 |\n"
    )
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py::test_flow_b"],
        inventory,
        plan_file_target,
        schema=2,
    )
    assert anchors == ["tests/integration/test_app.py::test_flow_b"]


def test_acceptance_anchors_schema2_undeclared_node_still_fails_closed(tmp_path):
    """The FILE/NODE coverage relaxation must not open a hole: a NODE anchor
    in a file §8 never mentions (neither the file nor any of its nodes) is
    still rejected."""
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_unrelated.py::test_x",
    ]
    with pytest.raises(TestSelectError, match="not a test-plan §8 integration row target"):
        executor._acceptance_anchors(
            ["tests/integration/test_unrelated.py::test_x"],
            inventory,
            _PLAN_WITH_TWO_FLOWS,
            schema=2,
        )


def test_integration_row_targets_parse_every_plus_separated_item(tmp_path):
    """Bug 3 (run 01M0S0FQ T-001, 2026-08-24): a multi-test §8 cell
    (``A + B + C``) must yield per-item targets, and a NODE item binds that
    node exactly -- never the whole first file."""
    executor = _runtime(tmp_path)
    targets = executor._integration_row_targets(
        "`tests/integration/test_app.py::test_flow_a` + `tests/integration/test_other.py`"
    )
    assert targets == [
        ("tests/integration/test_app.py", "tests/integration/test_app.py::test_flow_a"),
        ("tests/integration/test_other.py", None),
    ]


def test_integration_row_targets_bare_filename_normalizes_under_integration(tmp_path):
    executor = _runtime(tmp_path)
    targets = executor._integration_row_targets("test_app.py::test_flow_a + `test_app.py`")
    assert targets == [
        ("tests/integration/test_app.py", "tests/integration/test_app.py::test_flow_a"),
        ("tests/integration/test_app.py", None),
    ]


def test_task_node_maps_legacy_test_refs_by_layer_prefix():
    """_task_node: a legacy raw payload (only test_refs) is layer-routed by
    path prefix into unit_refs/acceptance_refs (196cbc9 convention)."""
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    node = MImplRuntimeMixin._task_node(
        {
            "task_id": "T-001",
            "issue_number": 1,
            "description": "slice",
            "ac_refs": ["AC-FR0001-01"],
            "fr_refs": ["FR-0001"],
            "if_ids": ["IF-IMPL-001"],
            "test_refs": [
                "tests/unit/test_app.py::test_app",
                "tests/integration/test_app.py",
            ],
            "scope_boundary": "tracks/app.py",
            "depends_on": [],
            "batch": "1",
            "parallel": False,
            "budget": 3,
        }
    )
    assert node.schema == 1
    assert node.unit_refs == ("tests/unit/test_app.py::test_app",)
    assert node.acceptance_refs == ("tests/integration/test_app.py",)


def test_task_node_schema2_payload_keeps_explicit_split():
    """A schema-2 raw payload carries its explicit split: the fields pass
    through untouched (no prefix re-routing over the declaration)."""
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    node = MImplRuntimeMixin._task_node(
        {
            "task_id": "T-001",
            "issue_number": 1,
            "description": "slice",
            "ac_refs": ["AC-FR0001-01"],
            "fr_refs": ["FR-0001"],
            "if_ids": ["IF-IMPL-001"],
            "test_refs": [],
            "unit_refs": ["tests/unit/test_app.py::test_app"],
            "acceptance_refs": ["tests/integration/test_app.py"],
            "schema": 2,
            "scope_boundary": "tracks/app.py",
            "depends_on": [],
            "batch": "1",
            "parallel": False,
            "budget": 3,
        }
    )
    assert node.schema == 2
    assert node.unit_refs == ("tests/unit/test_app.py::test_app",)
    assert node.acceptance_refs == ("tests/integration/test_app.py",)
