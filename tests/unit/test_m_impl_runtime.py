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
from tracks.executor.rgr import (
    create_green_commit,
    red_base_sha,
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
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='pytest --collect-only tests/integration'\n"
        "run='pytest tests/integration'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='pytest --collect-only tests/e2e'\n"
        "run='pytest tests/e2e'\ncwd='.'\n",
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


class _RecordingBackend:
    def __init__(self, result):
        self.result = result
        self.assignments = []

    def act(self, role, substate, doc, doc_path, assignment=None):
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
    }
    message = _git(repo, "log", "--format=%B", "-1", green_payload["g_sha"])
    assert "Tracks-Attempt: 1" in message
    assert f"Tracks-R: {red_payload['r_sha']}" in message
    assert "Tracks-AC: AC-FR0001-01,FR-0001" in message
    assert "Tracks-FR:" not in message
    assert "Tracks-NFR:" not in message


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

        def act(self, role, substate, doc, doc_path, assignment=None):
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
# commands/results/changed_paths remain audit-only. Each test below is RED:
# the production Runtime gate does not yet execute/observe anything, so the
# assertions land on the missing runtime-authority contract token.
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

_RUNTIME_EVIDENCE_TOKENS = (
    "argv",
    "cwd",
    "exit_code",
    "classification",
    "stdout_sha",
    "stderr_sha",
)


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
    commands/results/manifest cannot cause a GREEN pass when the Runtime
    re-execution of the assigned unit command exits 1. The Runtime verdict
    must carry observed argv/cwd/exit_code/classification/output hashes and
    no G is created."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"r_sha": "1" * 40, "task_id": task["task_id"], "attempt": 1},
    )
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, "1" * 40, agent_cwd="/agent/claimed/cwd"),
    )
    _write_failing_unit_test(repo)
    head_before = _git(repo, "rev-parse", "HEAD")

    executor = _executor(repo, store)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "Runtime unit command exits 1 -> GREEN must fail closed"
    assert fails[-1].payload["check"] == "impl_defect"
    evidence = fails[-1].payload.get("evidence") or ""
    assert isinstance(evidence, str) and evidence
    for token in _RUNTIME_EVIDENCE_TOKENS:
        assert token in evidence, f"runtime evidence missing observed {token}"
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
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"r_sha": "1" * 40, "task_id": task["task_id"], "attempt": 1},
    )
    no_change_outcome = _structured_outcome(
        "green", [], r_identity="1" * 40, diff_ref=RGR_GREEN_DIFF
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
    assert "exited" in last.get("reason", ""), (
        "gate must proceed to Runtime unit re-execution and fail there"
    )


def test_green_gate_regression_when_r_unit_test_mutation_hidden(tmp_path):
    """Contract 2: the Runtime derives candidate changed paths from the
    observed git/filesystem state — never from Devon's changed_paths. A hidden
    mutation of the frozen R unit test must fail GREEN closed with
    `regression`."""
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
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    # Candidate state the Runtime observes: the green diff plus a hidden
    # mutation of the immutable R unit test. Devon's changed_paths reports
    # only tracks/app.py.
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
    assert fails, "hidden R unit-test mutation must fail GREEN closed"
    assert fails[-1].payload["check"] == "regression"


def test_refactor_no_change_still_reruns_authoritative_green_gate(tmp_path):
    """Contract 3: a REFACTOR `no_change` outcome must still rerun the same
    authoritative Green gate plan; a real failing rerun can never emit
    refactor.no_change / pass or advance."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    base = _git(repo, "rev-parse", "HEAD")
    gate = create_gate_worktree(str(repo), base, "", "", "RUN", task["task_id"])
    try:
        _write_failing_unit_test(Path(gate.path))
        outcome = _structured_outcome("refactor", [], r_identity="1" * 40)
        outcome["no_change_reason"] = "no improvements found"
        store.append("RUN", "v0.5", "outcome.received", outcome)
        executor = _executor(repo, store)
        before = len(list(store.events("RUN")))
        executor._do_run_refactor_gate(
            Command("run_refactor_gate", {"stage": "M-IMPL"}, command_id="C-RF"),
            store.state("RUN"),
            None,
            False,
        )
        new_events = list(store.events("RUN"))[before:]
        assert not any(ev.type == "refactor.no_change" for ev in new_events), (
            "no_change is emitted only after the authoritative Green gate rerun passes"
        )
        fails = [ev for ev in new_events if ev.type == "verdict.failed"]
        assert fails, "a real failing rerun must fail closed"
        assert store.state("RUN").substate == "REFACTOR", (
            "a failing rerun must not advance to TASK_REVIEW"
        )
    finally:
        cleanup_worktree(gate)


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
    results stay audit-only."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"r_sha": "1" * 40, "task_id": task["task_id"], "attempt": 1},
    )
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, "1" * 40, agent_cwd="/agent/claimed/cwd"),
    )
    base = _git(repo, "rev-parse", "HEAD")
    gate = create_gate_worktree(str(repo), base, "", "", "RUN", task["task_id"])
    try:
        _write_failing_unit_test(Path(gate.path))
        executor = _executor(repo, store)
        executor._do_run_task_gates(
            Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
            store.state("RUN"),
            None,
            False,
        )
        fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
        assert fails, "the Runtime gate must execute and fail closed"
        assert fails[-1].payload["check"] == "impl_defect"
        evidence = fails[-1].payload.get("evidence") or ""
        gate_cwd = str(Path(gate.path).resolve())
        assert gate_cwd in evidence, "gate commands must run in the Runtime-selected worktree cwd"
        assert "/agent/claimed/cwd" not in evidence, (
            "agent-reported cwd must never become the execution cwd"
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
