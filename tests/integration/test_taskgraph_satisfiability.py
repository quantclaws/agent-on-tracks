"""OB-3 integration tests: commit path with real sidecar & scope checks."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.helpers import make_repo
from tracks import paths
from tracks.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store, new_ulid


def _init_store_repo(tmp_path: Path, version="v0.8"):
    repo = make_repo(tmp_path)
    home = paths.tracks_home(repo)
    store = Store(home)
    run_id = new_ulid()
    vdir = paths.version_dir(home, version)
    vdir.mkdir(parents=True, exist_ok=True)
    # link .venv for contract execution (as host_repo fixture does)
    venv_target = Path(sys.prefix).resolve()
    venv_link = repo / ".venv"
    if not venv_link.exists():
        with contextlib.suppress(OSError):
            venv_link.symlink_to(venv_target, target_is_directory=True)
    # minimal .gitignore
    (repo / ".gitignore").write_text(".venv\n", encoding="utf-8")
    return repo, home, store, run_id, vdir


def _write_contract(home: Path):
    p = paths.project_toml_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)

    def sec(name):
        return (
            f"[{name}]\n"
            'framework = "pytest"\n'
            f'paths = ["tests/{name}/"]\n'
            f'collect = "{exe} -m pytest --collect-only -q tests/{name}/"\n'
            f'run = "{exe} -m pytest -q tests/{name}/ --junitxml={{result}}"\n'
            f'run_selected = "{exe} -m pytest {{nodes}} -q --junitxml={{result}}"\n'
            'cwd = "."\n\n'
        )

    exe = sys.executable
    p.write_text(
        sec("unit") + sec("integration") + sec("e2e")
        + '[nightly]\nschedule = "0 3 * * *"\nworkflow = ".github/workflows/nightly.yml"\njob = "nightly"\nlayers = ["unit","integration","e2e"]\npurpose = "x"\n\n'
        + '[layout.devon]\nwritable = ["tracks/"]\n[layout.shield]\nwritable = ["tests/"]\n',
        encoding="utf-8",
    )


def _write_minimal_docs(vdir: Path):
    (vdir / "acceptance.md").write_text(
        "# Acceptance\n\n## FR-0001\n\n### AC-FR0001-01\n\n - c\n"
        "## FR-0002\n\n### AC-FR0002-01\n\n - c\n",
        encoding="utf-8",
    )
    (vdir / "interfaces.md").write_text(
        "# Interfaces\n\n## 5. IF Registry\n\n| ID | Name |\n|---|---| \n| IF-IMPL-001 | x |\n| IF-IMPL-002 | y |\n",
        encoding="utf-8",
    )
    (vdir / "architecture.md").write_text(
        "# Architecture\n\n"
        "- tracks/foo.py owner=T-001 surface=foo composition=x wiring=y test=test_a evidence=e\n"
        "- tracks/bar.py owner=T-002 surface=bar composition=x wiring=y test=test_b evidence=e\n"
        "- AC-FR0001-01 FR-0001 if_ids IF-IMPL-001\n"
        "- AC-FR0002-01 FR-0002 if_ids IF-IMPL-002\n",
        encoding="utf-8",
    )
    (vdir / "test-plan.md").write_text(
        "# Test Plan\n\n## 8. AC Coverage\n\n| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_a.py::test_x | IF-IMPL-001 |\n"
        "| AC-FR0002-01 | integration | tests/integration/test_b.py::test_y | IF-IMPL-002 |\n",
        encoding="utf-8",
    )


def _write_tracks_and_tests(repo: Path):
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tracks" / "foo.py").write_text("def foo(): return 1\n", encoding="utf-8")
    (repo / "tracks" / "bar.py").write_text("def bar(): return 2\n", encoding="utf-8")
    # Ensure tests dirs
    (repo / "tests" / "unit").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "e2e").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "integration" / "__init__.py").write_text("", encoding="utf-8")
    # test_a imports foo and bar (so T-001 exercises bar owned by T-002)
    (repo / "tests" / "integration" / "test_a.py").write_text(
        "import tracks.foo\nimport tracks.bar\ndef test_x(): assert tracks.foo.foo() == 1\n",
        encoding="utf-8",
    )
    (repo / "tests" / "integration" / "test_b.py").write_text(
        "import tracks.bar\ndef test_y(): assert tracks.bar.bar() == 2\n",
        encoding="utf-8",
    )
    # e2e dummy for contract
    (repo / "tests" / "e2e" / "test_e2e.py").write_text("def test_e(): assert True\n", encoding="utf-8")
    (repo / "tests" / "e2e" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests" / "unit" / "test_u.py").write_text("def test_u(): assert True\n", encoding="utf-8")


def _tasks_data_missing_edge():
    return {
        "schema": 2,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "facade",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": True,
                "budget": 3,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_a.py::test_x"],
            },
            {
                "task_id": "T-002",
                "issue_number": 2,
                "description": "owner",
                "ac_refs": ["AC-FR0002-01"],
                "fr_refs": ["FR-0002"],
                "if_ids": ["IF-IMPL-002"],
                "scope_boundary": "tracks/bar.py",
                "depends_on": [],
                "batch": "1",
                "parallel": True,
                "budget": 3,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_b.py::test_y"],
            },
        ],
    }


def _tasks_data_with_edge():
    data = _tasks_data_missing_edge()
    data["tasks"][0]["depends_on"] = ["T-002"]
    return data


def _commit_taskgraph_via_executor(repo: Path, store, run_id: str, vdir: Path):
    # Ensure store has a run entry and M-IMPL stage
    # Minimal state: need version and baseline docs so taskgraph happy path not blocked on missing files
    # Append necessary events to get into M-IMPL with taskgraph not yet committed
    # Use Store's state machine: we need to be in a stage where commit_taskgraph is allowed.
    # Simplify: directly invoke the private method via Executor, bypassing decide.

    ex = Executor(store, repo, run_id)
    # Ensure Executor sees correct version
    ex.version = "v0.8"
    # Need to seed store with some history so state is plausible: directly call _do_commit_taskgraph
    cmd = Command(kind="commit_taskgraph", params={}, command_id=new_ulid())
    state = store.state(run_id)
    # If state.version is empty, set it by appending a stage event
    if not state.version:
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-IMPL"})
        state = store.state(run_id)
    ex._do_commit_taskgraph(cmd, state, None, False)
    return store.events(run_id)


def _events_of(events, etype):
    return [e for e in events if e.type == etype]


@pytest.mark.integration
def test_bad_graph_rejected_with_missing_edge(tmp_path: Path):
    repo, home, store, run_id, vdir = _init_store_repo(tmp_path, "v0.8")
    _write_contract(home)
    _write_minimal_docs(vdir)
    _write_tracks_and_tests(repo)
    # write bad graph
    (vdir / "tasks.json").write_text(json.dumps(_tasks_data_missing_edge()), encoding="utf-8")
    # Ensure clean git state for dirty_tree_stamp
    subprocess.run(["git", "add", "-A"], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=repo, check=False, capture_output=True)
    events = _commit_taskgraph_via_executor(repo, store, run_id, vdir)
    fails = _events_of(events, "verdict.failed")
    assert fails, "bad graph should be rejected"
    # Find the taskgraph check failure
    taskgraph_fails = [e for e in fails if e.payload.get("check") == "taskgraph"]
    assert taskgraph_fails, f"expected taskgraph verdict, got {fails}"
    payload = taskgraph_fails[-1].payload
    evidence = str(payload.get("evidence") or "") + str(payload.get("reason") or "")
    assert "without depends_on edge" in evidence
    assert "T-001" in evidence


@pytest.mark.integration
def test_good_graph_passes_and_tasks_md_generated(tmp_path: Path):
    repo, home, store, run_id, vdir = _init_store_repo(tmp_path, "v0.8")
    _write_contract(home)
    _write_minimal_docs(vdir)
    _write_tracks_and_tests(repo)
    (vdir / "tasks.json").write_text(json.dumps(_tasks_data_with_edge()), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=repo, check=False, capture_output=True)
    events = _commit_taskgraph_via_executor(repo, store, run_id, vdir)
    committed = _events_of(events, "taskgraph.committed")
    assert committed, "good graph should commit"
    payload = committed[-1].payload
    assert payload["validate_status"] == "pass"
    # tasks.md should be regenerated
    tasks_md = vdir / "tasks.md"
    assert tasks_md.exists()
    text = tasks_md.read_text(encoding="utf-8")
    assert "T-001" in text and "T-002" in text
    # payload should contain satisfiability (not skipped)
    assert "satisfiability" in payload
    assert payload["satisfiability"]["violations"] == []
    # probe_summary also
    assert "satisfiability" in payload["anchor_probe"]


@pytest.mark.integration
def test_scope_synthetic_rejected(tmp_path: Path):
    repo, home, store, run_id, vdir = _init_store_repo(tmp_path, "v0.8")
    _write_contract(home)
    _write_minimal_docs(vdir)
    _write_tracks_and_tests(repo)
    data = _tasks_data_missing_edge()
    # Make T-001 scope synthetic that neither exists nor declared
    data["tasks"][0]["scope_boundary"] = "tracks/synthetic/_verify2.py"
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=repo, check=False, capture_output=True)
    events = _commit_taskgraph_via_executor(repo, store, run_id, vdir)
    fails = _events_of(events, "verdict.failed")
    taskgraph_fails = [e for e in fails if e.payload.get("check") == "taskgraph"]
    assert taskgraph_fails
    evidence = str(taskgraph_fails[-1].payload.get("evidence") or "") + str(taskgraph_fails[-1].payload.get("reason") or "")
    assert "neither exists in repo nor is declared" in evidence
    assert "tracks/synthetic/_verify2.py" in evidence


@pytest.mark.integration
def test_sidecar_infra_failure_skipped_not_silent(tmp_path: Path, monkeypatch, capsys):
    repo, home, store, run_id, vdir = _init_store_repo(tmp_path, "v0.8")
    _write_contract(home)
    _write_minimal_docs(vdir)
    _write_tracks_and_tests(repo)
    (vdir / "tasks.json").write_text(json.dumps(_tasks_data_with_edge()), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=repo, check=False, capture_output=True)

    # Force infra failure by monkeypatching collect to raise
    import tracks.executor.anchor_surface as surf

    orig_collect = surf.collect_anchor_surface

    def failing_collect(*args, **kwargs):
        from tracks.executor.anchor_surface import AnchorSurfaceError

        raise AnchorSurfaceError("simulated infra failure")

    monkeypatch.setattr(surf, "collect_anchor_surface", failing_collect)
    # Also need to patch the import used inside m_impl_runtime's helper

    # The helper imports from anchor_surface inside method, so patching surf is enough.
    # Ensure sidecar is stale (remove any existing)
    sidecar = vdir / "anchor-surface.json"
    if sidecar.exists():
        sidecar.unlink()

    # Also monkeypatch load to force regen path? Remove sidecar ensures regen.
    events = _commit_taskgraph_via_executor(repo, store, run_id, vdir)
    # Even with infra failure, taskgraph should still pass (skipped) not silently ignored?
    # The commit should succeed with skipped marker, not fail.
    committed = _events_of(events, "taskgraph.committed")
    # Since graph is good, even with sidecar failure it should still commit but with skipped
    assert committed, "infra failure should not block commit when graph is otherwise good"
    payload = committed[-1].payload
    assert "satisfiability" in payload
    assert "skipped" in payload["satisfiability"]
    assert "infra" in payload["satisfiability"]["skipped"]
    # stderr should have printed
    captured = capsys.readouterr()
    # The helper prints to stderr; capsys may capture it
    assert "anchor-surface regen failed (infra)" in captured.err or "infra" in str(captured.err)

    # Restore
    monkeypatch.setattr(surf, "collect_anchor_surface", orig_collect)
