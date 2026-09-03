"""walk_red: integration RED seals the unit R-tree on site (01M19FJVES7G113RD8QXXY3PQZ T-042).

B94 parked integration tasks at GREEN start with no RED checkpoint, so
GREEN assignment validation (r_tree_identity required) and green commit
lineage (R ref required) could never pass - six plan_defect DIAGNOSE
rounds. walk_red reuses the preset anchor_red command channel: RED for
an integration task runs unit_refs expecting green, pins the R ref
straight to HEAD, and emits red.checkpointed with a resolvable ref.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.kernel.events import Command
from tracks.kernel.m_impl import _decide_m_impl_agent, _is_integration_task
from tracks.store import Store

_UNIT_RUN_SELECTED = (
    ".venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
    "--dist loadscope --junitxml={result}"
)
_INTEGRATION_RUN_SELECTED = (
    ".venv/bin/python -m pytest {nodes} --tb=short -q -n 8 "
    "--dist loadscope --junitxml={result}"
)

_UNIT_PIN = "tests/unit/test_walk_pin.py::test_pin"


def _engine_repo(tmp_path: Path) -> Path:
    repo = git_repo(tmp_path, gitignore=True)
    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[unit]\nframework='pytest'\npaths=['tests/unit/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/unit/'\n"
        "run='.venv/bin/python -m pytest tests/unit/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        f"run_selected='{_UNIT_RUN_SELECTED}'\ncwd='.'\n\n"
        "[integration]\nframework='pytest'\npaths=['tests/integration/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/integration/'\n"
        "run='.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        f"run_selected='{_INTEGRATION_RUN_SELECTED}'\ncwd='.'\n\n"
        "[e2e]\nframework='pytest'\npaths=['tests/e2e/']\n"
        "collect='.venv/bin/python -m pytest --collect-only -q tests/e2e/'\n"
        "run='.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 "
        "--dist loadscope --junitxml={result}'\n"
        f"run_selected='{_INTEGRATION_RUN_SELECTED}'\ncwd='.'\n\n"
        "[nightly]\nschedule='0 3 * * *'\nworkflow='.github/workflows/nightly.yml'\n"
        "job='nightly-regression'\nlayers=['unit', 'integration', 'e2e']\n"
        "purpose='regression'\n\n"
        "[layout.devon]\nwritable=['tracks/', 'tests/unit/']\n",
        encoding="utf-8",
    )
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "integration").mkdir(parents=True)
    (repo / "tests" / "e2e").mkdir(parents=True)
    return repo


def _commit(repo: Path, rel: str, text: str, msg: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True)


def _integration_meta(**over) -> dict:
    meta: dict = {
        "task_id": "T-INT",
        "issue_number": 1,
        "description": "integration收口",
        "ac_refs": ["AC-FR0001-01"],
        "fr_refs": ["FR-0001"],
        "if_ids": ["IF-IMPL-001"],
        "test_refs": [_UNIT_PIN],
        "scope_boundary": "tracks/app.py",
        "depends_on": [],
        "batch": "1",
        "parallel": False,
        "budget": 3,
        "unit_refs": [_UNIT_PIN],
        "acceptance_refs": [],
        "deferred_refs": [],
        "integration": True,
        "schema": 2,
    }
    meta.update(over)
    return meta


def _fake_run(captured: list, returncode: int, stdout: str):
    real_run = subprocess.run

    def _run(argv, *args, **kwargs):
        if list(argv)[:1] == ["git"]:
            return real_run(argv, *args, **kwargs)
        captured.append(list(argv))
        return subprocess.CompletedProcess(list(argv), returncode, stdout=stdout, stderr="")

    return _run


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


# integration flag itself triggers walk_red, no description marker needed
def test_integration_flag_triggers_anchor_red_command():
    from tracks.kernel.machine import State

    state = State()
    state.current_task_id = "T-INT"
    state.current_task_metadata = _integration_meta()
    state.substate = "RED"
    state.doc_dispatched = False
    assert _is_integration_task(state) is True
    cmd = _decide_m_impl_agent(state, "RED")
    assert cmd is not None and cmd.kind == "anchor_red"


# non-integration RED without the preset marker still goes to Devon
def test_non_integration_red_dispatches_devon():
    from tracks.kernel.machine import State

    state = State()
    state.current_task_id = "T-001"
    state.current_task_metadata = _integration_meta(
        task_id="T-001", integration=False, description="plain slice"
    )
    state.substate = "RED"
    state.doc_dispatched = False
    assert _is_integration_task(state) is False
    cmd = _decide_m_impl_agent(state, "RED")
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"


# walk_red seals R: checkpoint emitted, ref resolves, GREEN unblocked
def test_walk_red_seals_r_and_unblocks_green(tmp_path, monkeypatch):
    import tracks.executor.m_impl_runtime as mir

    repo = _engine_repo(tmp_path)
    _commit(repo, "tracks/app.py", "x = 1\n", "app")
    _commit(repo, "tests/unit/test_walk_pin.py", "def test_pin(): assert True\n", "pin")
    head = _head(repo)
    store = Store(paths.tracks_home(repo))
    task = _integration_meta()
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {"task_count": 1, "tasks": [task], "digest": "d"},
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-INT", "task": task, "manifest": {"task_id": "T-INT"}},
    )
    executor = Executor(store, repo, "RUN")
    state = store.state("RUN")
    captured: list = []
    monkeypatch.setattr(mir.subprocess, "run", _fake_run(captured, 0, "1 passed\n"))
    executor._do_anchor_red(
        Command(kind="anchor_red", params={}, command_id="C-WR"),
        state,
        "T-INT",
        False,
    )
    checkpoints = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    assert len(checkpoints) == 1
    assert checkpoints[0].payload["r_sha"] == head
    assert checkpoints[0].payload["walk_red"] is True
    ref = checkpoints[0].payload["ref"]
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert resolved.returncode == 0
    live = store.state("RUN")
    assert live.r_tree_identity == head
    # GREEN assignment validation now passes with the sealed identity
    assignment = {
        "task_id": "T-INT",
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0001-01"],
        "test_refs": [_UNIT_PIN],
        "commands": {"unit": ["u"], "test": {"integration": "i"}, "guard": ["g"]},
        "manifest": {"allowed_paths": ["tracks/app.py"]},
        "phase": "green",
        "pre_dirty_snapshot": {},
        "result_identity": "r",
        "r_tree_identity": live.r_tree_identity,
    }
    assert MImplRuntimeMixin._invalid_devon_assignment(assignment) is None
    # green commit lineage resolves through the walk_red checkpoint family
    failure, _task, r_sha, b_sha, green_base = executor._green_commit_lineage(
        live, "T-INT", live.current_attempt + 1
    )
    assert failure is None
    assert r_sha == head
    assert b_sha is not None and green_base is not None


# walk_red fails closed when the pins are not green
def test_walk_red_red_pins_fail_closed(tmp_path, monkeypatch):
    import tracks.executor.m_impl_runtime as mir

    repo = _engine_repo(tmp_path)
    _commit(repo, "tracks/app.py", "x = 1\n", "app")
    _commit(repo, "tests/unit/test_walk_pin.py", "def test_pin(): assert True\n", "pin")
    store = Store(paths.tracks_home(repo))
    task = _integration_meta()
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {"task_count": 1, "tasks": [task], "digest": "d"},
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-INT", "task": task, "manifest": {"task_id": "T-INT"}},
    )
    executor = Executor(store, repo, "RUN")
    state = store.state("RUN")
    captured: list = []
    monkeypatch.setattr(mir.subprocess, "run", _fake_run(captured, 1, "1 failed\n"))
    executor._do_anchor_red(
        Command(kind="anchor_red", params={}, command_id="C-WR"),
        state,
        "T-INT",
        False,
    )
    failed = [e for e in store.events("RUN") if e.type == "verdict.failed"]
    assert failed and failed[-1].payload["check"] == "red_invalid"
    assert "walk_red" in failed[-1].payload["reason"]
    assert not [e for e in store.events("RUN") if e.type == "red.checkpointed"]
