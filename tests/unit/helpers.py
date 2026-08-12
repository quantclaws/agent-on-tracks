"""Unit helpers: build EventEnvelope lists for pure-function tests (NFR-02)."""
import hashlib
import json
import subprocess
from pathlib import Path

from tracks import paths
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK
from tracks.store import Store


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    )


def git_strip(repo, *args):
    """Run a git command in ``repo`` and return stripped stdout."""
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def git_repo(tmp_path, *, gitignore=False, email="test@example.com",
             name="Test"):
    """Create an initialized git repo with a README and an initial commit.

    README content is ``readme\\n`` and the commit message is ``initial``,
    matching the rollback/version/M-IMPL materialization test fixtures. With
    ``gitignore=True`` an ``.tracks/`` .gitignore is also committed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git_strip(repo, "init", "-b", "main")
    git_strip(repo, "config", "user.email", email)
    git_strip(repo, "config", "user.name", name)
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    git_strip(repo, "add", "README.md")
    if gitignore:
        (repo / ".gitignore").write_text(".tracks/\n", encoding="utf-8")
        git_strip(repo, "add", ".gitignore")
    git_strip(repo, "commit", "-m", "initial")
    return repo


def init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@test.test")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, base


def ev(seq, type, payload=None, run_id="RUN", version="v0.1",
       command_id=None, task_id=None):
    return EventEnvelope(
        seq=seq, ts="2026-07-30T00:00:00+00:00", run_id=run_id,
        version=version, type=type, schema_version=1,
        command_id=command_id, task_id=task_id, payload=payload or {},
    )


def seq(*items):
    """items: (type, payload) tuples -> enumerated envelopes."""
    return [ev(i + 1, t, p) for i, (t, p) in enumerate(items)]


def capture_popen_cmd(monkeypatch,
                      target="tracks.effects.opencode.subprocess.Popen"):
    """Patch ``subprocess.Popen`` with a stub that records the argv of the next
    call and returns a minimal exit-0 process (empty JSON stdout). Returns the
    dict whose ``cmd`` key holds the captured argv list. Shared by the unit
    tests that assert on the opencode ``run`` command shape."""
    captured = {}

    class _StubProc:
        pid = 123
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return "{}", ""

    def _fake_popen(*args, **kwargs):
        captured["cmd"] = args[0]
        return _StubProc()

    monkeypatch.setattr(target, _fake_popen)
    return captured


# -- shared M-IMPL fixtures (de-duplicate R0801 across unit test modules) -----

M_IMPL_REQUIRED_ASSIGNMENT_KEYS = frozenset({
    "target_doc", "doc_set", "role", "substate", "attempt", "review_round",
    "docs", "templates", "skills", "criteria_pack", "test_tasks", "manifest",
    "phase", "r_tree_identity", "pre_dirty_snapshot", "result_identity",
})

RGR_RED_DIFF = (
    "diff --git a/tests/unit/test_app.py b/tests/unit/test_app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tests/unit/test_app.py\n"
    "@@ -0,0 +1 @@\n"
    "+def test_app(): assert False\n"
)

RGR_GREEN_DIFF = (
    "diff --git a/tracks/app.py b/tracks/app.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/tracks/app.py\n"
    "@@ -0,0 +1 @@\n"
    '+IMPLEMENTED_IF = "IF-IMPL-001"\n'
)

# Public event constants for the first half of the M-IMPL lifecycle (pure
# reducer/decide fixtures). Shared by test_machine_m_impl and test_devon_rgr.
ENTER_M_IMPL = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-IMPL"}),
]
BASELINE_CMD = ("command.issued", {"command": {"kind": "freeze_baseline",
                                               "params": {"stage": "M-IMPL"},
                                               "command_id": "C1"}})
BASELINE_FROZEN = ("baseline.frozen", {"status": "current"})
ARCHER_DISPATCH = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                  "params": {"role": "archer",
                                                             "substate": "PLANNING"},
                                                  "command_id": "C2"}})
ARCHER_DONE = ("outcome.received", {"role": "archer", "status": "done"})
TASKGRAPH_CMD = ("command.issued", {"command": {"kind": "commit_taskgraph",
                                                "params": {"stage": "M-IMPL"},
                                                "command_id": "C3"}})
TASKGRAPH_COMMITTED = ("taskgraph.committed", {"task_count": 1})
ISLAND1_CMD = ("command.issued", {"command": {"kind": "check_island_1",
                                              "params": {"stage": "M-IMPL"},
                                              "command_id": "C4"}})
ISLAND1_PASS = ("verdict.passed", {"check": "island_1"})
PRISM_PLAN_DISPATCH = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                      "params": {"role": "prism",
                                                                 "substate": "PRISM_PLAN"},
                                                      "command_id": "C5"}})
PRISM_PLAN_DONE = ("outcome.received", {"role": "prism", "status": "done"})
PRISM_PLAN_PASS = ("prism.verdict", {"verdict": "pass",
                                     "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)})
SELECT_TASK_CMD = ("command.issued", {"command": {"kind": "select_task",
                                                  "params": {"stage": "M-IMPL"},
                                                  "command_id": "C6"}})
TASK_STARTED = ("task.started", {"task_id": "T1"})
DEVON_RED_DISPATCH = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                     "params": {"role": "devon",
                                                                "substate": "RED"},
                                                     "command_id": "C7"}})
DEVON_RED_DONE = ("outcome.received", {"role": "devon", "status": "done"})
RED_GATE_CMD = ("command.issued", {"command": {"kind": "run_task_gates",
                                               "params": {"stage": "M-IMPL",
                                                          "gate": "RED_GATE"},
                                               "command_id": "C8"}})
RED_VALID_PASS = ("verdict.passed", {"check": "red_valid"})
RED_CHECKPOINT_CMD = ("command.issued", {"command": {"kind": "checkpoint_red",
                                                     "params": {"stage": "M-IMPL",
                                                                "task_id": "T1"},
                                                     "command_id": "C9"}})
RED_CHECKPOINTED = ("red.checkpointed", {"r_sha": "abc123"})
PRISM_RED_DISPATCH = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                     "params": {"role": "prism",
                                                                "substate": "PRISM_RED"},
                                                     "command_id": "C10"}})
PRISM_RED_DONE = ("outcome.received", {"role": "prism", "status": "done"})
PRISM_RED_PASS = ("prism.verdict", {"verdict": "pass",
                                    "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)})
DEVON_GREEN_DISPATCH = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                       "params": {"role": "devon",
                                                                  "substate": "GREEN"},
                                                       "command_id": "C11"}})
DEVON_GREEN_DONE = ("outcome.received", {"role": "devon", "status": "done"})
GREEN_GATE_CMD = ("command.issued", {"command": {"kind": "run_task_gates",
                                                 "params": {"stage": "M-IMPL",
                                                            "gate": "GREEN_GATE"},
                                                 "command_id": "C12"}})
GREEN_PASS = ("verdict.passed", {"check": "green"})
GREEN_COMMIT_CMD = ("command.issued", {"command": {"kind": "commit_green",
                                                   "params": {"stage": "M-IMPL",
                                                              "task_id": "T1"},
                                                   "command_id": "C13"}})
GREEN_COMMITTED = ("green.committed", {"commit_sha": "def456"})


def m_impl_docs(repo: Path, *, closure: bool = True) -> Path:
    """Write the v0.5 design trio + context docs under .tracks/projects/v0.5/.

    With ``closure=True`` architecture.md carries the FR-0001 closure line the
    island gates require; ``closure=False`` leaves it as a plain stub.
    """
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.5")
    vdir.mkdir(parents=True, exist_ok=True)
    for name in ("story.md", "spec.md", "acceptance.md", "interfaces.md",
                 "test-plan.md", "architecture.md"):
        (vdir / name).write_text(f"# {name}\n", encoding="utf-8")
    (vdir / "acceptance.md").write_text(
        "# Acceptance\n\n### AC-FR0001-01\n\n- observable\n",
        encoding="utf-8",
    )
    (vdir / "interfaces.md").write_text(
        "# Interfaces\n\n## 5. IF Registry\n\n- IF-IMPL-001\n",
        encoding="utf-8",
    )
    (vdir / "test-plan.md").write_text(
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/unit/test_app.py | "
        "IF-IMPL-001 |\n",
        encoding="utf-8",
    )
    if closure:
        (vdir / "architecture.md").write_text(
            "# Architecture\n\n"
            "- **FR-0001**: owner=app, surface=cli, composition=root, "
            "wiring=entry->app, test=unit, evidence=pytest, "
            "IF-=IF-IMPL-001\n",
            encoding="utf-8",
        )
    return vdir


def m_impl_store(repo: Path, *, design_checkpoint: bool = True) -> Store:
    """Store seeded with the realistic M-DESIGN history before M-IMPL.

    Default history: story.requested; stage.entered(M-DESIGN);
    result.checkpointed with the actual repo HEAD as commit_sha (minimal
    canonical payload); stage.exited(M-DESIGN); stage.entered(M-IMPL).
    With ``design_checkpoint=False`` the M-DESIGN result.checkpointed is
    omitted while the stage sequence is retained.
    """
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-DESIGN"})
    if design_checkpoint:
        sha = git_strip(repo, "rev-parse", "HEAD")
        store.append("RUN", "v0.5", "result.checkpointed",
                     {"created_commit": True, "commit_sha": sha,
                      "base_sha": sha, "result_id": "D1"})
    store.append("RUN", "v0.5", "stage.exited", {"stage": "M-DESIGN"})
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-IMPL"})
    return store


def m_impl_task(test_ref: str = "tests/unit/test_app.py::test_app") -> dict:
    """Canonical single-task payload used by the M-IMPL fixtures."""
    return {
        "task_id": "T-001", "issue_number": 1, "description": "slice",
        "ac_refs": ["AC-FR0001-01"], "fr_refs": ["FR-0001"],
        "if_ids": ["IF-IMPL-001"], "test_refs": [test_ref],
        "scope_boundary": "tracks/app.py", "depends_on": [],
        "batch": "1", "parallel": False, "budget": 3,
    }


def m_impl_graph(store: Store, task: dict, raw: str | None = None) -> None:
    """Append a committed single-task taskgraph event (digest included)."""
    raw = raw or json.dumps({"tasks": [task]}, sort_keys=True)
    store.append(
        "RUN", "v0.5", "taskgraph.committed",
        {"task_count": 1, "task_ids": [task["task_id"]],
         "tasks": [task], "path": "tasks.json",
         "digest": hashlib.sha256(raw.encode()).hexdigest(),
         "validate_status": "pass"},
    )


def m_impl_started_task(repo: Path, *, closure: bool = True) -> tuple[Store, dict]:
    """Store with a committed single-task graph and task.started manifest."""
    m_impl_docs(repo, closure=closure)
    store = m_impl_store(repo)
    task = m_impl_task()
    m_impl_graph(store, task)
    store.append("RUN", "v0.5", "task.started",
                 {"task_id": task["task_id"], "task": task,
                  "manifest": {"task_id": task["task_id"],
                               "allowed_paths": [
                                   "tracks/app.py", "tests/unit/test_app.py",
                               ],
                               "forbidden_paths": [".tracks/projects/**"]}})
    return store, task


def assert_cleanup_cycle(backend, info, source):
    """Assert the deployed file matches ``source`` byte-identically, clean up,
    then assert the deployed file was removed."""
    try:
        assert info["dest"].read_bytes() == source.read_bytes()
    finally:
        backend._cleanup(info)
    assert not info["dest"].exists()
