"""Focused unit tests: G materialization in the M-IMPL Runtime (FR-0120).

Behavior B: ``_do_commit_green`` still creates the formal G commit (parent=B +
trailers via create_green_commit) but now materializes G onto the checked-out
release branch/worktree with safe compare-and-set / fast-forward semantics:

- ``HEAD == B`` (B derived replay-safely from the immutable R commit's parent,
  never blindly from the current HEAD): fast-forward/update to G and update the
  working tree;
- ``HEAD == G``: idempotent success (reconcile after a crash between the branch
  update and green.committed);
- any unrelated ``HEAD``: fail closed with an impl_defect/lineage reason, never
  resetting or overwriting unrelated work.

green.committed is emitted only after the branch/worktree contains G, and
reconcile emits no duplicate event. Frozen Shield integration/e2e tests remain
byte-identical in G (G is B's tree + the product impl diff only).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from tests.unit.helpers import (
    RGR_GREEN_DIFF,
    RGR_RED_DIFF,
    git_repo,
    m_impl_started_task,
)
from tests.unit.helpers import (
    git_strip as _git,
)
from tests.unit.helpers import (
    m_impl_store as _store,
)
from tests.unit.helpers import (
    m_impl_task as _task,
)
from tracks.executor.executor import Executor
from tracks.executor.rgr import red_base_sha
from tracks.kernel.events import Command
from tracks.store import Store


def _repo(tmp_path: Path) -> Path:
    """Repo fixture with a committed .tracks/ gitignore (M-IMPL docs repo)."""
    return git_repo(tmp_path, gitignore=True, email="t@example.com", name="T")


def _freeze_tests(repo: Path) -> None:
    """Commit a frozen Shield integration test to B.

    It must survive into G byte-identically (G is B's tree + the product impl
    diff only).
    """
    frozen = repo / "tests" / "integration" / "test_frozen.py"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    frozen.write_text(
        "# AC-FR0001-01@v0.5 TRACKS-TRACE integration test\n"
        "def test_frozen():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    _git(repo, "add", "tests")
    subprocess.run(["git", "commit", "-m", "freeze tests"], cwd=repo,
                   check=True, capture_output=True, text=True)


def _started_task(repo: Path) -> tuple[Store, dict]:
    _freeze_tests(repo)
    return m_impl_started_task(repo, closure=False)


def _outcome(phase, changed, r_identity=None, diff=None) -> dict:
    outcome = {
        "role": "devon", "status": "done", "phase": phase,
        "changed_paths": changed, "commands": [],
        "manifest_compliance": True, "pre_identity": "pre",
        "post_identity": "post", "implemented_if_ids": ["IF-IMPL-001"],
        "diff_ref": diff,
    }
    if r_identity:
        outcome["r_identity"] = r_identity
    return outcome


def _run_red(executor: Executor, store: Store) -> str:
    store.append("RUN", "v0.5", "outcome.received",
                 _outcome("red", ["tests/unit/test_app.py"], diff=RGR_RED_DIFF))
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"), None, False,
    )
    red = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1]
    return red.payload["r_sha"]


def _stage_green(executor: Executor, store: Store, r_sha: str) -> None:
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received",
                 _outcome("green", ["tracks/app.py"], r_sha,
                          diff=RGR_GREEN_DIFF))


def _commit_green(executor: Executor, store: Store) -> dict:
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"), None, False,
    )
    greens = [e for e in store.events("RUN")
              if e.type == "green.committed"]
    assert greens, "green.committed must be emitted"
    return greens[-1].payload


# -- fast-forward materialization ---------------------------------------------

def test_commit_green_fast_forwards_branch_and_worktree_to_g(tmp_path):
    repo = _repo(tmp_path)
    store, task = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    b_sha = red_base_sha(str(repo), r_sha)
    assert b_sha is not None
    assert _git(repo, "rev-parse", "HEAD") == b_sha
    _stage_green(executor, store, r_sha)

    payload = _commit_green(executor, store)

    assert payload["r_sha"] == r_sha
    assert payload["base_sha"] == b_sha
    head = _git(repo, "rev-parse", "HEAD")
    assert head == payload["g_sha"], "branch/worktree must contain G"
    assert _git(repo, "rev-parse", f"{payload['g_sha']}^") == b_sha, (
        "G parent must be B (derived from R's parent, not current HEAD)")
    impl = repo / "tracks" / "app.py"
    assert impl.is_file(), "working tree must contain the implementation"
    assert 'IMPLEMENTED_IF = "IF-IMPL-001"' in impl.read_text(encoding="utf-8")
    assert payload["task_id"] == task["task_id"]
    assert payload["attempt"] == 1
    message = _git(repo, "log", "--format=%B", "-1", payload["g_sha"])
    assert f"Tracks-R: {r_sha}" in message
    assert "Tracks-Attempt: 1" in message


def test_green_materialization_leaves_frozen_tests_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    _stage_green(executor, store, r_sha)
    payload = _commit_green(executor, store)

    frozen_path = "tests/integration/test_frozen.py"
    g_frozen = _git(repo, "show", f"{payload['g_sha']}:{frozen_path}")
    b_frozen = _git(repo, "show", f"{payload['base_sha']}:{frozen_path}")
    assert g_frozen == b_frozen, "frozen Shield tests must be byte-identical in G"
    assert (repo / frozen_path).read_text(
        encoding="utf-8").rstrip("\n") == g_frozen, (
        "working tree frozen test must match G")


def test_commit_green_does_not_include_red_test_diff_in_g(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    _stage_green(executor, store, r_sha)
    payload = _commit_green(executor, store)

    tree = _git(repo, "ls-tree", "-r", "--name-only", payload["g_sha"])
    assert "tests/unit/test_app.py" not in tree, (
        "G must be B tree + product impl diff only (no Red test diff)")


# -- idempotent recovery ------------------------------------------------------

def test_commit_green_reconcile_after_crash_is_idempotent(tmp_path):
    """Crash after the branch update to G but before green.committed: replay
    recomputes the same deterministic G and succeeds without a new commit."""
    repo = _repo(tmp_path)
    store, task = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    _stage_green(executor, store, r_sha)
    payload = _commit_green(executor, store)
    head = _git(repo, "rev-parse", "HEAD")
    assert head == payload["g_sha"]

    # Recovery event stream: the crash happened AFTER the branch update to G
    # but BEFORE green.committed was persisted, so red.checkpointed IS already
    # recorded (the immutable R ref already exists) and only the green commit
    # is re-driven. The branch is still at the G the crashed invocation wrote.
    store2 = _store(repo)
    task2 = _task()
    raw = json.dumps({"tasks": [task2]}, sort_keys=True)
    store2.append("RUN", "v0.5", "taskgraph.committed",
                  {"task_count": 1, "task_ids": [task2["task_id"]],
                   "tasks": [task2], "path": "tasks.json",
                   "digest": hashlib.sha256(raw.encode()).hexdigest(),
                   "validate_status": "pass"})
    store2.append("RUN", "v0.5", "task.started",
                  {"task_id": task2["task_id"], "task": task2,
                   "manifest": {"task_id": task2["task_id"],
                                "allowed_paths": [
                                    "tracks/app.py", "tests/unit/test_app.py",
                                ],
                                "forbidden_paths": [".tracks/projects/**"]}})
    store2.append("RUN", "v0.5", "red.checkpointed",
                  {"r_sha": r_sha, "task_id": task2["task_id"], "attempt": 1})
    executor2 = Executor(store2, repo, "RUN")
    _stage_green(executor2, store2, r_sha)

    payload2 = _commit_green(executor2, store2)

    assert payload2["g_sha"] == head, (
        "reconcile must converge on the same G that is already materialized")
    assert _git(repo, "rev-parse", "HEAD") == head, (
        "reconcile must not move the branch")
    assert not any(e.type == "verdict.failed" for e in store2.events("RUN"))


def test_commit_green_reconcile_replay_emits_no_duplicate_event(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    _stage_green(executor, store, r_sha)
    command = Command("commit_green", command_id="C-G")
    stale_state = store.state("RUN")
    executor._do_commit_green(command, stale_state, None, False)
    executor._do_commit_green(command, stale_state, None, True)
    assert len([e for e in store.events("RUN")
                if e.type == "green.committed"]) == 1


# -- unrelated HEAD fails closed ----------------------------------------------

def test_commit_green_fails_closed_on_unrelated_head(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    _stage_green(executor, store, r_sha)
    # unrelated work lands on the branch after the Red checkpoint.
    (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")
    subprocess.run(["git", "commit", "-m", "unrelated"], cwd=repo,
                   check=True, capture_output=True, text=True)
    head_before = _git(repo, "rev-parse", "HEAD")

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"), None, False,
    )

    failures = [e for e in store.events("RUN")
                if e.type == "verdict.failed"]
    assert failures, "unrelated HEAD must fail closed"
    assert failures[-1].payload["check"] == "impl_defect"
    assert "lineage" in failures[-1].payload["reason"]
    assert _git(repo, "rev-parse", "HEAD") == head_before, (
        "unrelated work must never be reset or overwritten")
    assert not any(e.type == "green.committed" for e in store.events("RUN"))


def test_commit_green_fails_closed_when_r_base_unresolvable(tmp_path):
    repo = _repo(tmp_path)
    store, task = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    # A red.checkpointed event with a bogus r_sha: B cannot be derived.
    store.append("RUN", "v0.5", "red.checkpointed",
                 {"r_sha": "0" * 40, "task_id": task["task_id"],
                  "attempt": 1})
    _stage_green(executor, store, "0" * 40)
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"), None, False,
    )
    failures = [e for e in store.events("RUN")
                if e.type == "verdict.failed"]
    assert failures
    assert failures[-1].payload["check"] == "impl_defect"
    assert "unresolvable" in failures[-1].payload["reason"]
    assert not any(e.type == "green.committed" for e in store.events("RUN"))


def test_commit_green_no_g_after_runtime_gate_failure(tmp_path):
    """Contract 1 (G side): when the Runtime observed the assigned Green unit
    command exiting 1 and emitted verdict.failed(impl_defect), commit_green
    must NOT materialize G onto the branch even if Devon self-reports a pass —
    no G is created and HEAD is untouched."""
    repo = _repo(tmp_path)
    store, task = _started_task(repo)
    executor = Executor(store, repo, "RUN")
    r_sha = _run_red(executor, store)
    _stage_green(executor, store, r_sha)
    # The Runtime-authoritative gate observed the failing unit command.
    store.append("RUN", "v0.5", "verdict.failed",
                 {"check": "impl_defect", "reason": "runtime gate",
                  "evidence": '{"argv":[],"exit_code":1}', "attempt": 1})
    head_before = _git(repo, "rev-parse", "HEAD")

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"), None, False,
    )

    failures = [e for e in store.events("RUN")
                if e.type == "verdict.failed"]
    assert failures
    assert failures[-1].payload["check"] == "impl_defect"
    assert _git(repo, "rev-parse", "HEAD") == head_before, (
        "no G may be materialized after a Runtime gate failure")
    assert not any(e.type == "green.committed" for e in store.events("RUN"))
