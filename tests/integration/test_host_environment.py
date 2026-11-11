"""A fresh host prepares tools before collection, with real restart recovery."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._support.local_tool_wheels import tool_wheelhouse
from tests.unit.test_t015_phase0_repair_evidence import _contract
from tracks import paths
from tracks.capabilities import FULL_WORKFLOW_PROFILE
from tracks.executor.executor import Executor
from tracks.executor.host_contract import load_host_contract
from tracks.host_contract_defaults import _DEFAULT_HOST_CONTRACT_TOML
from tracks.kernel.attention import unresolved_attentions
from tracks.kernel.events import Command
from tracks.kernel.machine import decide
from tracks.store import Store

pytestmark = pytest.mark.integration


def _python(script):
    return shlex.join([sys.executable, "-c", script])


def _declare(repo, bootstrap=(), install="true"):
    contract = _DEFAULT_HOST_CONTRACT_TOML.replace(
        'install = "true"',
        f"bootstrap = {json.dumps(list(bootstrap))}\ninstall = {json.dumps(install)}",
        1,
    )
    path = paths.project_toml_path(paths.tracks_home(repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_contract() + "\n" + contract, encoding="utf-8")
    return path


def _start(repo, *, modern=True):
    store = Store(paths.tracks_home(repo))
    store.append("fresh", "v0.1", "story.requested", {
        "raw_chars": 1, **({"workflow_profile": FULL_WORKFLOW_PROFILE} if modern else {}),
    })
    store.append("fresh", "v0.1", "stage.entered", {"stage": "M-TEST"})
    return store


# AC-FR0348-01@v0.12 TRACKS-TRACE installed host creates tools before baseline tests
def test_fresh_host_bootstraps_before_actual_collection(host_repo):
    (host_repo / ".venv").unlink()  # remove fixture link, never the linked environment
    wheelhouse = tool_wheelhouse(host_repo.parent / "test-tool-wheels", "pytest")
    bootstrap = shlex.join([sys.executable, "-m", "venv", ".venv"])
    install = shlex.join([
        ".venv/bin/python", "-m", "pip", "install", "--no-index", "--find-links",
        str(wheelhouse), "pytest",
    ])
    _declare(host_repo, [bootstrap], install)
    for layer in ("unit", "integration", "e2e"):
        (host_repo / "tests" / layer).mkdir(parents=True)
    (host_repo / "tests/unit/test_baseline.py").write_text("def test_baseline():\n    assert True\n")
    from tracks import __file__ as package_file

    architecture = paths.projects_dir(paths.tracks_home(host_repo)) / "v0.1/architecture.md"
    architecture.parent.mkdir(parents=True)
    architecture.write_bytes(
        (Path(package_file).parent / "assets/demo_host/architecture.md").read_bytes()
    )
    with_store = _start(host_repo)
    try:
        executor = Executor(with_store, host_repo, "fresh")
        command = decide(with_store.state("fresh"))
        assert command.kind == "prepare_host_environment"
        executor.issue(command)
        assert (host_repo / ".venv/bin/python").is_file()
        assert with_store.state("fresh").host_environment_prepared
        probe = subprocess.check_output([
            str(host_repo / ".venv/bin/python"), "-c", "import pytest; print(pytest.__file__)",
        ], text=True).strip()
        assert Path(probe).is_relative_to(host_repo / ".venv")
        command = decide(with_store.state("fresh"))
        assert command.kind == "phase0_validate"
        executor.issue(command)
        assert with_store.state("fresh").phase0_status == "SEALED"
        assert "tests/unit/test_baseline.py::test_baseline" in executor._collect_all_declared_layers()[0]
        events = list(with_store.events("fresh"))
        prepared = next(e for e in events if e.type == "environment.prepared")
        assert prepared.seq < next(e.seq for e in events if e.type == "phase0.coverage")
        assert len([e for e in events if e.type == "environment.step_completed"]) == 2
        with_store.rebuild_projections()
        assert with_store.state("fresh").host_environment_prepared
    finally:
        with_store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE setup failure never proceeds to test dispatch
@pytest.mark.parametrize("failure_at", ["bootstrap", "install"])
def test_setup_failure_is_visible_and_retryable(host_repo, failure_at):
    fail = _python("raise SystemExit('missing tool')")
    bootstrap = [fail] if failure_at == "bootstrap" else []
    _declare(host_repo, bootstrap, "true" if bootstrap else fail)
    store = _start(host_repo)
    try:
        executor = Executor(store, host_repo, "fresh")
        executor.issue(decide(store.state("fresh")))
        state = store.state("fresh")
        assert not state.host_environment_prepared
        assert state.phase0_status is None
        assert state.pending is None
        attention = unresolved_attentions(store.events("fresh"))[-1]
        assert attention.payload["reason"] == "environment_setup_failed"
        assert "missing tool" in attention.payload["detail"]
        assert attention.payload["evidence_blob"]
        assert executor._pending_operator_attention(state) is not None
        _declare(host_repo)
        store.append("fresh", "v0.1", "human.retry", {})
        executor.issue(decide(store.state("fresh")))
        assert store.state("fresh").host_environment_prepared
        assert not unresolved_attentions(store.events("fresh"))
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE explicit contracts are required for modern runs
@pytest.mark.parametrize("body", ["", "[unit]\nframework = 'pytest'\n"])
def test_missing_modern_contract_does_not_materialize_success(host_repo, body):
    path = paths.project_toml_path(paths.tracks_home(host_repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    store = _start(host_repo)
    try:
        executor = Executor(store, host_repo, "fresh")
        executor.issue(decide(store.state("fresh")))
        assert not store.state("fresh").host_environment_prepared
        assert unresolved_attentions(store.events("fresh"))[-1].payload["reason"] == "invalid_environment_contract"
        assert executor._load_or_default_contract(Command(kind="run_local_gates"), "candidate") == (None, None, None)
        executor.issue(Command(kind="materialize_host_contract"))
        assert not any(e.type == "host_contract.materialized" for e in store.events("fresh"))
        assert not (paths.runtime_dir(store.home) / "materialized-host-contract.toml").exists()
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE legacy replay keeps the historical fallback
def test_legacy_contract_fallback_remains_available(host_repo):
    store = _start(host_repo, modern=False)
    try:
        contract, _, source = Executor(store, host_repo, "fresh")._load_or_default_contract(
            Command(kind="run_local_gates"), "candidate"
        )
        assert source == "runtime_default"
        assert contract.install == "true"
        assert decide(store.state("fresh")).kind != "prepare_host_environment"
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE redesign re-arms preparation even when phase0 sealed
@pytest.mark.parametrize("transition,payload", [
    ("stage.entered", {"stage": "M-DESIGN"}),
    ("stage.rolled_back", {"to_stage": "M-DESIGN"}),
])
def test_redesign_rearms_preparation(host_repo, transition, payload):
    store = _start(host_repo)
    try:
        store.append("fresh", "v0.1", "environment.prepared", {})
        store.append("fresh", "v0.1", "phase0.sealed", {"seal_id": "previous"})
        store.append("fresh", "v0.1", transition, payload)
        assert not store.state("fresh").host_environment_prepared
        store.append("fresh", "v0.1", "stage.entered", {"stage": "M-TEST"})
        assert decide(store.state("fresh")).kind == "prepare_host_environment"
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE malformed bootstrap never becomes a runnable contract
@pytest.mark.parametrize("raw", ['"echo hello"', '[""]', '[1]'])
def test_invalid_bootstrap_rejected(host_repo, raw):
    path = _declare(host_repo)
    path.write_text(path.read_text().replace("bootstrap = []", "bootstrap = " + raw))
    with pytest.raises(ValueError, match="bootstrap"):
        load_host_contract(path)


def _wait_for(path, process):
    deadline = time.monotonic() + 15
    while not path.exists():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.05)


# AC-NFR0140-01@v0.7 TRACKS-TRACE actual process death preserves setup WAL and step receipts
@pytest.mark.parametrize("inputs_changed", [None, "contract", "head"])
def test_process_death_recovers_only_unfinished_step(host_repo, inputs_changed):
    count = "from pathlib import Path; p=Path('bootstrap-count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
    install = "from pathlib import Path; import time; Path('install-started').touch();\nwhile not Path('continue-install').exists(): time.sleep(0.05)"
    _declare(host_repo, [_python(count)], _python(install))
    _start(host_repo).close()
    script = (
        "from pathlib import Path; from tracks import paths; from tracks.store import Store; "
        "from tracks.executor.executor import Executor; from tracks.kernel.machine import decide; "
        f"repo=Path({str(host_repo)!r}); store=Store(paths.tracks_home(repo)); "
        "Executor(store, repo, 'fresh').issue(decide(store.state('fresh')))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "TRAC_AGENT_BACKEND": "fake"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        _wait_for(host_repo / "install-started", process)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
    store = Store(paths.tracks_home(host_repo))
    try:
        state = store.state("fresh")
        assert state.pending["kind"] == "prepare_host_environment"
        command_id = state.pending["command_id"]
        assert not state.host_environment_prepared
        (host_repo / "continue-install").touch()
        if inputs_changed == "contract":
            contract = paths.project_toml_path(store.home)
            contract.write_text(contract.read_text() + "\n# revised contract\n")
        elif inputs_changed == "head":
            (host_repo / "README.md").write_text("new candidate inputs\n")
            subprocess.run(["git", "add", "README.md"], cwd=host_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "new inputs"], cwd=host_repo, check=True, capture_output=True)
        assert Executor(store, host_repo, "fresh")._recover() == "prepare_host_environment"
        if inputs_changed:
            assert not store.state("fresh").host_environment_prepared
            attention = unresolved_attentions(store.events("fresh"))[-1]
            assert attention.payload["reason"] == "preparation_inputs_changed"
            assert (host_repo / "bootstrap-count").read_text() == "x"
            store.append("fresh", "v0.1", "human.retry", {})
            Executor(store, host_repo, "fresh").issue(decide(store.state("fresh")))
            assert (host_repo / "bootstrap-count").read_text() == "xx"
            assert store.state("fresh").host_environment_prepared
            assert not unresolved_attentions(store.events("fresh"))
            return
        assert (host_repo / "bootstrap-count").read_text() == "x"
        assert store.state("fresh").host_environment_prepared
        assert store.state("fresh").pending is None
        events = [e for e in store.events("fresh") if e.command_id == command_id]
        assert len([e for e in events if e.type == "environment.step_completed"]) == 2
        assert len([e for e in events if e.type == "environment.prepared"]) == 1
    finally:
        store.close()
