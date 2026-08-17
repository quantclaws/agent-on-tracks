"""B1 (issue #2): writer dispatches run in isolated worktrees.

Devon RED/GREEN/REFACTOR (M-IMPL) and Shield WRITE (M-TEST) dispatches get a
per-dispatch worktree created from the main HEAD; the work is replayed onto
the main tree (unstaged) so every downstream pipeline keeps operating on the
main tree. Lifecycle is audited as worktree.opened / worktree.closed events.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import (
    git_repo,
)
from tests.unit.helpers import (
    git_strip as _git,
)
from tests.unit.helpers import (
    m_impl_started_task as _started_task_store,
)
from tracks.effects.fake import FakeBackend
from tracks.executor.executor import Executor
from tracks.kernel.events import Command


class _InstrumentedFake(FakeBackend):
    """FakeBackend that records the dispatched worktree and writes files in
    it (production agents write there naturally; the stock fake has no I/O)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_worktrees: list[Path | None] = []
        self.write_files: dict[str, str] = {}
        self.check_files: list[str] = []

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        self.seen_worktrees.append(Path(worktree) if worktree is not None else None)
        if worktree is not None:
            wt = Path(worktree)
            for rel in self.check_files:
                assert (wt / rel).exists(), f"worktree must contain committed {rel}"
            for rel, content in self.write_files.items():
                target = wt / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        return super().act(role, substate, doc, doc_path, assignment=assignment)


def _devon_assignment(task_id: str, phase: str = "red") -> dict:
    return {
        "kind": phase.upper(),
        "target_doc": None,
        "doc_set": [],
        "role": "devon",
        "substate": phase.upper(),
        "attempt": 1,
        "review_round": 0,
        "docs": [],
        "templates": [],
        "skills": [],
        "criteria_pack": None,
        "test_tasks": [],
        "task_id": task_id,
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0230-01"],
        "test_refs": [],
        "commands": [],
        "manifest": {
            "task_id": task_id,
            "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
            "forbidden_paths": [],
            "unit_commands": [],
            "red_test_paths": ["tests/unit"],
        },
        "phase": phase,
        "r_tree_identity": None,
        "pre_dirty_snapshot": {},
        "result_identity": "9" * 40,
    }


def _dispatch_devon(executor: Executor, store, assignment: dict, substate: str = "RED"):
    cmd = Command(
        "dispatch_agent",
        {"role": "devon", "substate": substate, "assignment": assignment},
        command_id="C-D",
    )
    executor._dispatch_agent_backend(
        cmd,
        store.state("RUN"),
        assignment["task_id"],
        "devon",
        substate,
        None,
        None,
        assignment,
    )


def _events(store, kind: str):
    return [ev for ev in store.events("RUN") if ev.type == kind]


def test_devon_dispatch_runs_in_worktree_and_replays_to_main(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    (repo / "tracks").mkdir(exist_ok=True)
    (repo / "tracks" / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "impl base")
    store, task = _started_task_store(repo)
    head_before = _git(repo, "rev-parse", "HEAD")
    fake = _InstrumentedFake(repo, "v0.5")
    fake.write_files = {
        "tests/unit/test_app.py": "def test_app():\n    assert False\n",
        "tracks/app.py": "VALUE = 1\n",
    }
    executor = Executor(store, repo, "RUN")
    executor.backend = fake
    assignment = _devon_assignment(task["task_id"])

    _dispatch_devon(executor, store, assignment)

    assert fake.seen_worktrees == [fake.seen_worktrees[0]]
    assert fake.seen_worktrees[0] is not None, "Devon dispatch must receive a worktree"
    wt_path = fake.seen_worktrees[0]
    assert not wt_path.exists(), "worktree must be cleaned up after the dispatch"
    assert (repo / "tests" / "unit" / "test_app.py").read_text(encoding="utf-8").startswith(
        "def test_app():"
    ), "new worktree file must be replayed to the main tree"
    assert (repo / "tracks" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n", (
        "tracked edit must be replayed to the main tree"
    )
    staged = _git(repo, "diff", "--cached", "--name-only")
    assert staged == "", "replay must not stage anything on the main tree"
    opened = _events(store, "worktree.opened")
    closed = _events(store, "worktree.closed")
    assert len(opened) == 1 and opened[0].payload["kind"] == "devon_candidate"
    assert opened[0].payload["task_id"] == task["task_id"]
    assert opened[0].payload["base_sha"] == head_before
    assert len(closed) == 1 and closed[0].payload["replayed"] is True
    assert closed[0].payload["kind"] == "devon_candidate"


def test_devon_dispatch_clean_worktree_is_noop_on_main(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    store, task = _started_task_store(repo)
    head_before = _git(repo, "rev-parse", "HEAD")
    fake = _InstrumentedFake(repo, "v0.5")
    executor = Executor(store, repo, "RUN")
    executor.backend = fake

    _dispatch_devon(executor, store, _devon_assignment(task["task_id"]))

    closed = _events(store, "worktree.closed")
    assert len(closed) == 1 and closed[0].payload["replayed"] is False
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--porcelain") == ""


def test_worktree_created_from_current_main_head(tmp_path):
    """Re-dispatches (PRISM revise -> GREEN, REFACTOR) must see committed
    work: the worktree is created from the CURRENT main HEAD, not a stale
    C_design."""
    repo = git_repo(tmp_path, gitignore=True)
    (repo / "tracks").mkdir(exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED = "G"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "G")
    store, task = _started_task_store(repo)
    fake = _InstrumentedFake(repo, "v0.5")
    fake.check_files = ["tracks/app.py"]
    executor = Executor(store, repo, "RUN")
    executor.backend = fake

    _dispatch_devon(executor, store, _devon_assignment(task["task_id"], "green"), "GREEN")


def test_non_writer_dispatch_gets_no_worktree(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    store, task = _started_task_store(repo)
    fake = _InstrumentedFake(repo, "v0.5")
    executor = Executor(store, repo, "RUN")
    executor.backend = fake
    cmd = Command(
        "dispatch_agent",
        {"role": "prism", "substate": "PRISM_FINAL"},
        command_id="C-P",
    )
    executor._dispatch_agent_backend(
        cmd, store.state("RUN"), task["task_id"], "prism", "PRISM_FINAL", None, None, {}
    )

    assert fake.seen_worktrees == [None], "reviewer dispatches stay on the main tree"
    assert _events(store, "worktree.opened") == []


def test_shield_write_opens_test_authority_worktree(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    store, task = _started_task_store(repo)
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-TEST"})
    executor = Executor(store, repo, "RUN")
    cmd = Command(
        "dispatch_agent",
        {"role": "shield", "substate": "WRITE"},
        command_id="C-S",
    )
    handle, wt_task_id = executor._open_writer_worktree(
        store.state("RUN"), "shield", "WRITE", cmd
    )

    assert handle is not None and handle.kind == "test_authority"
    assert wt_task_id is None
    opened = _events(store, "worktree.opened")
    assert len(opened) == 1 and opened[0].payload["kind"] == "test_authority"
    from tracks.executor.worktree import cleanup_worktree

    cleanup_worktree(handle)


def test_allowed_paths_resolve_against_worktree_root_no_overreach(tmp_path):
    """Prism blocker regression: with a writer worktree, the audit whitelist
    must resolve under the Auditor's root (the worktree) — otherwise every
    legal write is over-reach and force-rolled-back in production while the
    Fake-based tests stay green (issue #2, B1 review)."""
    from tracks.effects.audit import Auditor
    from tracks.effects.opencode import OpencodeBackend
    from tracks.executor.worktree import create_devon_worktree, ensure_runtime_assets

    repo = git_repo(tmp_path)  # no .tracks ignore: the contract must commit
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = "pytest --collect-only tests/integration"\n'
        'run = "pytest tests/integration"\n'
        'cwd = "."\n'
        "\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n',
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "contract")
    head = _git(repo, "rev-parse", "HEAD")
    handle = create_devon_worktree(str(repo), head, "RUN", "T-001")
    ensure_runtime_assets(str(repo), handle.path)
    try:
        backend = OpencodeBackend(repo, "v0.5")
        assignment = _devon_assignment("T-001")
        agent_dest = repo / ".opencode" / "agents"
        allowed = backend._allowed_paths(
            [], agent_dest, "devon", "RED", assignment, root=Path(handle.path)
        )
        auditor = Auditor(Path(handle.path), allowed=allowed)
        baseline = auditor.baseline()
        legal = Path(handle.path) / "tests" / "unit" / "test_new.py"
        legal.parent.mkdir(parents=True, exist_ok=True)
        legal.write_text("def test_new():\n    assert False\n", encoding="utf-8")
        overreach = auditor.audit(baseline)
        assert overreach is None, f"legal worktree write flagged: {overreach}"
    finally:
        from tracks.executor.worktree import cleanup_worktree

        cleanup_worktree(handle)


def test_replay_blocked_by_dirty_file_falls_back_to_mirror(tmp_path):
    """When the fast-path git apply refuses (the main tree holds an
    uncommitted file the diff creates), the replay mirrors the worktree's
    final state per file — exactly what the agent would have left had it
    worked directly in the main tree (a direct edit also overwrites a
    pre-existing dirty file). No worktree verdict, work reclaimed."""
    repo = git_repo(tmp_path, gitignore=True)
    store, task = _started_task_store(repo)
    conflicting = repo / "tests" / "unit" / "test_app.py"
    conflicting.parent.mkdir(parents=True, exist_ok=True)
    conflicting.write_text("PRE-EXISTING DIRTY\n", encoding="utf-8")
    fake = _InstrumentedFake(repo, "v0.5")
    fake.write_files = {"tests/unit/test_app.py": "def test_app():\n    assert False\n"}
    executor = Executor(store, repo, "RUN")
    executor.backend = fake

    _dispatch_devon(executor, store, _devon_assignment(task["task_id"]))

    assert conflicting.read_text(encoding="utf-8") == "def test_app():\n    assert False\n"
    wt_fails = [
        ev
        for ev in store.events("RUN")
        if ev.type == "verdict.failed" and ev.payload.get("check") == "worktree"
    ]
    assert wt_fails == [], "mirror fallback must not fail the dispatch"
    assert not fake.seen_worktrees[0].exists(), "worktree is reclaimed after replay"
