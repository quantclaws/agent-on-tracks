"""OOB RED coverage for the FULL_F producer/consumer boundary.

The fixture executes a real three-layer FULL command in a temporary host
repository.  It deliberately does not append ``full.executed`` or
``evidence.reused`` as Arrange facts; those events must come from Runtime.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

_PROJECT_TOML = """\
[unit]
framework = "pytest"
paths = ["tests/unit/"]
collect = "{python} -m pytest --collect-only -q tests/unit/"
run = "{python} -m pytest tests/unit/ -q --junitxml={{result}}"
run_selected = "{python} -m pytest {{nodes}} -q --junitxml={{result}}"
cwd = "."

[integration]
framework = "pytest"
paths = ["tests/integration/"]
collect = "{python} -m pytest --collect-only -q tests/integration/"
run = "{python} -m pytest tests/integration/ -q --junitxml={{result}}"
run_selected = "{python} -m pytest {{nodes}} -q --junitxml={{result}}"
cwd = "."

[e2e]
framework = "pytest"
paths = ["tests/e2e/"]
collect = "{python} -m pytest --collect-only -q tests/e2e/"
run = "{python} -m pytest tests/e2e/ -q --junitxml={{result}}"
run_selected = "{python} -m pytest {{nodes}} -q --junitxml={{result}}"
cwd = "."

[nightly]
schedule = "0 3 * * *"
workflow = ".github/workflows/nightly.yml"
job = "nightly-regression"
layers = ["unit", "integration", "e2e"]
purpose = "scheduled FULL-suite regression"
"""

_COUNTER_TEST = """\
from pathlib import Path


def test_full_counter():
    counter = Path(__file__).resolve().parents[2] / ".tracks/runtime/full-counter"
    counter.parent.mkdir(parents=True, exist_ok=True)
    value = int(counter.read_text() or "0") if counter.exists() else 0
    counter.write_text(str(value + 1))
"""


def _full_host(tmp_path: Path) -> tuple[Path, Store, Executor]:
    repo = git_repo(tmp_path, gitignore=True)
    projects = repo / ".tracks" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    projects.joinpath("project.toml").write_text(
        _PROJECT_TOML.format(python=sys.executable), encoding="utf-8"
    )
    for layer in ("unit", "integration", "e2e"):
        path = repo / "tests" / layer
        path.mkdir(parents=True, exist_ok=True)
        path.joinpath("test_counter.py").write_text(_COUNTER_TEST, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fullf fixture"], cwd=repo, check=True,
        capture_output=True, text=True,
    )
    store = Store(repo / ".tracks")
    store.append(
        "RUN",
        "v0.8",
        "baseline.frozen",
        {"status": "current", "digest": "baseline-fixture"},
    )
    return repo, store, Executor(store, repo, "RUN")


def _run_real_full(executor: Executor, store: Store) -> int:
    executor._execute_full_round(
        Command("check_island_2", command_id=f"C-FULL-{uuid.uuid4().hex[:8]}"),
        store.state("RUN"),
        "FULL_1",
        {},
    )
    counter = executor.repo / ".tracks/runtime/full-counter"
    return int(counter.read_text(encoding="utf-8"))


def test_real_full_persists_identity_and_execution_binding(tmp_path):
    """A passed FULL must persist its actual identity and execution WAL."""
    repo, store, executor = _full_host(tmp_path)
    count = _run_real_full(executor, store)
    events = list(store.events("RUN"))
    full = [event for event in events if event.type == "full.executed"][-1]

    assert count == 3
    assert full.payload["passed"] is True
    assert full.payload["serves_as_full_f"] is True
    assert full.payload["outcomes_ref"]
    assert full.payload["execution_commit"]
    identity = full.payload["identity"]
    assert full.payload["identity_basis"][0] == identity["tree"]
    assert full.payload["identity_basis"][1] == identity["command"]
    assert full.payload["identity_basis"][2] == identity["env"]
    assert full.payload["identity_basis"][3] == identity["selection_id"]
    store.close()


def test_test_selected_alone_cannot_authorize_fullf_reuse(tmp_path):
    """A forged historical selection cannot substitute for executed FULL."""
    repo, store, executor = _full_host(tmp_path)
    _run_real_full(executor, store)
    forged = {
        "scope": "full",
        "tree": "forged-tree",
        "command": ["forged-command"],
        "env": "forged-env",
        "selection_id": "forged-selection",
        "identity_basis": [
            "forged-tree", ["forged-command"], "forged-env", "forged-selection"
        ],
    }
    store.append("RUN", "v0.8", "test.selected", forged)
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()

    executor._emit_full_f_judgment(Command("judge_full_f_reuse", command_id="C-J"), candidate)

    assert not any(
        event.type == "evidence.reused" and event.payload.get("kind") == "full_f"
        for event in store.events("RUN")
    )
    store.close()


def test_fullf_rechecks_identity_after_stale_for_same_candidate(tmp_path):
    """A later stale mark invalidates reuse and causes a real FULL rerun."""
    repo, store, executor = _full_host(tmp_path)
    initial_count = _run_real_full(executor, store)
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    executor._emit_full_f_judgment(
        Command("judge_full_f_reuse", command_id="C-FIRST"), candidate
    )
    assert any(
        event.type == "evidence.reused" and event.payload.get("candidate_sha") == candidate
        for event in store.events("RUN")
    )
    store.append(
        "RUN",
        "v0.8",
        "evidence.staled",
        {"candidate_sha": candidate, "reason": "test_stale"},
    )
    executor._emit_full_f_judgment(
        Command("judge_full_f_reuse", command_id="C-STALE"), candidate
    )
    counter = int((repo / ".tracks/runtime/full-counter").read_text(encoding="utf-8"))
    assert counter > initial_count
    assert any(
        event.type == "full.executed"
        and event.payload.get("candidate_sha") == candidate
        and event.payload.get("passed") is True
        for event in store.events("RUN")
    )
    store.close()


def test_fullf_rerun_executes_real_layers_and_failure_does_not_progress(tmp_path, monkeypatch):
    """A reuse miss must execute FULL and a failing result must not continue."""
    repo, store, executor = _full_host(tmp_path)
    initial_count = _run_real_full(executor, store)
    (repo / "tests" / "unit" / "test_counter.py").write_text(
        _COUNTER_TEST + "\n\ndef test_forced_full_failure():\n    assert False\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(executor, "_do_run_local_gates", lambda *_args: None)

    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    executor.issue(
        Command(
            "judge_full_f_reuse",
            params={"candidate_sha": candidate},
            command_id="C-RERUN",
        )
    )

    counter = executor.repo / ".tracks/runtime/full-counter"
    assert int(counter.read_text(encoding="utf-8")) > initial_count
    assert not any(
        event.type in {"observe_ci_runs", "stage.entered"}
        and event.payload.get("candidate_sha") == candidate
        for event in store.events("RUN")
    )
    assert any(
        event.type == "full.executed" and event.payload.get("passed") is False
        for event in store.events("RUN")
    )
    (repo / "tests" / "unit" / "test_counter.py").write_text(
        _COUNTER_TEST, encoding="utf-8"
    )
    executor.issue(
        Command(
            "judge_full_f_reuse",
            params={"candidate_sha": candidate},
            command_id="C-RETRY",
        )
    )
    assert int(counter.read_text(encoding="utf-8")) > initial_count + 3
    assert any(
        event.type == "full.executed"
        and event.payload.get("candidate_sha") == candidate
        and event.payload.get("passed") is True
        for event in store.events("RUN")
    )
    store.close()
