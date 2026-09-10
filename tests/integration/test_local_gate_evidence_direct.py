"""Direct LOCAL_GATES acceptance at the real Store/Executor boundary.

The host contract and gate commands are real temporary-repository artifacts.
The test does not replace the loader, validator, or gate executor.  CI after a
fully passing local-gate chain uses the existing loopback stand-in from the CI
readback slice; no external network or real Agent is used.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_verify_ci_readback import _REPO, _CiStandIn
from tracks import paths
from tracks.executor.executor import Executor
from tracks.executor.host_contract import (
    GATE_RESULT_PROTOCOL,
    load_host_contract,
    validate_host_contract,
)
from tracks.executor.m_verify import freeze_candidate
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_RUNNER = """\
import json
import sys
from pathlib import Path

mode, counter_name, counter_path = sys.argv[1:4]
counter = Path(counter_path)
counts = json.loads(counter.read_text(encoding="utf-8")) if counter.exists() else {}
counts[counter_name] = counts.get(counter_name, 0) + 1
counter.parent.mkdir(parents=True, exist_ok=True)
counter.write_text(json.dumps(counts, sort_keys=True), encoding="utf-8")

if mode == "malformed_file":
    Path(sys.argv[4]).write_text("not-json", encoding="utf-8")
    raise SystemExit(0)
if mode == "pass_file":
    Path(sys.argv[4]).write_text(
        json.dumps({
            "schema": "tracks-gate-result",
            "version": 1,
            "status": "passed",
            "exit_code": 0,
            "summary": {"observed": True},
        }),
        encoding="utf-8",
    )
    raise SystemExit(0)
if mode == "fail_exit":
    raise SystemExit(7)
if mode == "pass_exit":
    raise SystemExit(0)
raise SystemExit(9)
"""


@pytest.fixture
def ci_standin():
    server = _CiStandIn().start()
    try:
        yield server
    finally:
        server.close()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _set_ci_env(monkeypatch: pytest.MonkeyPatch, standin: _CiStandIn, candidate: str) -> None:
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)
    monkeypatch.setenv("TRAC_CI_REPO", _REPO)
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")
    standin.configure(head_sha=candidate, checks={"required-ci": "success"})


def _gate_command(python: str, mode: str, name: str, counter: Path, result: bool) -> str:
    argv = [
        shlex.quote(python),
        "gate_runner.py",
        mode,
        shlex.quote(name),
        shlex.quote(str(counter)),
    ]
    if result:
        argv.append("{result}")
    return " ".join(argv)


def _write_contract(repo: Path, counter: Path, *, second_mode: str = "pass_exit") -> Path:
    (repo / "gate_runner.py").write_text(_RUNNER, encoding="utf-8")
    python = sys.executable
    second_result = second_mode == "pass_file" or second_mode == "malformed_file"
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[host-contract]",
        "version = 1",
        'language = "python"',
        'toolchain = "cpython"',
        f"install = {json.dumps(python)}",
        "",
        "[[host-contract.local_gate]]",
        'kind = "quality"',
        'source = "command"',
        f"command = {json.dumps(_gate_command(python, 'pass_file', 'quality', counter, True))}",
        'categories = ["lint_format"]',
        'result_channel = "file"',
        "timeout_seconds = 30",
        "",
        "[[host-contract.local_gate]]",
        'kind = "trace"',
        'source = "command"',
        f"command = {json.dumps(_gate_command(python, second_mode, 'trace', counter, second_result))}",
        'categories = ["trace"]',
        f'result_channel = "{"file" if second_result else "exit_code"}"',
        "timeout_seconds = 30",
        "",
        "[host-contract.ci]",
        'repo_env = "TRAC_CI_REPO"',
        'workflow = "123"',
        'required_checks = ["required-ci"]',
    ]
    contract.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return contract


def _seed_candidate(host_repo: Path, trac, counter: Path, *, second_mode: str = "pass_exit") -> tuple[str, str, Path]:
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin="LOCAL_GATES acceptance boundary").returncode == 0
    contract = _write_contract(host_repo, counter, second_mode=second_mode)
    subprocess.run(
        ["git", "add", "-f", "gate_runner.py", ".tracks/projects/project.toml"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    _git(host_repo, "commit", "-qm", "materialize local gate contract")
    candidate = _git(host_repo, "rev-parse", "HEAD")
    loaded = load_host_contract(contract)
    assert loaded.contract_version == 1
    assert validate_host_contract(loaded, host_repo) == ()
    identity = freeze_candidate(host_repo)
    assert identity.candidate_sha == candidate
    assert identity.clean_tree is True and identity.branch

    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        version = store.state(run_id).version or "v0.8"
        store.append(run_id, version, "stage.entered", {"stage": "M-VERIFY"})
        store.append(
            run_id,
            version,
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": identity.branch,
                "frozen_at_seq": len(list(store.events(run_id))) + 1,
            },
        )
    finally:
        store.close()
    return run_id, candidate, contract


def _events(host_repo: Path, run_id: str) -> list:
    store = Store(paths.tracks_home(host_repo))
    try:
        return list(store.events(run_id))
    finally:
        store.close()


def _issue_gates(host_repo: Path, run_id: str, candidate: str) -> None:
    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id).issue(
            Command(kind="run_local_gates", params={"candidate_sha": candidate})
        )
    finally:
        store.close()


def _replay_gates(host_repo: Path, run_id: str, candidate: str) -> None:
    store = Store(paths.tracks_home(host_repo))
    try:
        issued = [
            event
            for event in store.events(run_id)
            if event.type == "command.issued"
            and (event.payload or {}).get("command", {}).get("kind") == "run_local_gates"
        ][-1]
        raw = issued.payload["command"]
        command = Command(
            kind=raw["kind"],
            params=raw.get("params", {}),
            command_id=raw.get("command_id"),
        )
        Executor(store, host_repo, run_id)._execute(
            command, store.state(run_id), None, reconcile=True
        )
    finally:
        store.close()


def _counts(counter: Path) -> dict[str, int]:
    return json.loads(counter.read_text(encoding="utf-8")) if counter.exists() else {}


def _gate_events(events: list) -> list:
    return [event for event in events if event.type in {"local_gate.passed", "local_gate.failed"}]


def _assert_complete_payload(events: list, candidate: str, digest: str) -> None:
    for event in _gate_events(events):
        payload = event.payload or {}
        normalized = payload.get("normalized_result")
        assert payload.get("candidate_sha") == candidate
        assert payload.get("contract_digest") == digest
        assert isinstance(payload.get("command_echo"), list)
        assert isinstance(normalized, dict)
        assert normalized.get("schema") == GATE_RESULT_PROTOCOL
        assert normalized.get("version") == 1
        assert normalized.get("status") in {"passed", "failed", "malformed"}
        assert isinstance(normalized.get("summary"), dict)


# AC-FR0269-01: every declared local gate executes and emits complete evidence.
def test_all_declared_gates_execute_with_candidate_bound_results(
    host_repo, trac, tmp_path, monkeypatch, ci_standin
):
    run_id, candidate, contract = _seed_candidate(host_repo, trac, tmp_path / "counts.json")
    _set_ci_env(monkeypatch, ci_standin, candidate)
    _issue_gates(host_repo, run_id, candidate)

    events = _events(host_repo, run_id)
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    assert [event.payload["kind"] for event in _gate_events(events)] == ["quality", "trace"]
    assert _counts(tmp_path / "counts.json") == {"quality": 1, "trace": 1}
    _assert_complete_payload(events, candidate, digest)
    assert any(event.type == "ci.run_observed" for event in events), (
        "a fully passing local-gate chain must reach the configured loopback CI boundary"
    )


# AC-FR0269-01/02: same-digest reconcile is idempotent; it does not rerun gates.
def test_same_digest_replay_does_not_rerun_local_gates(
    host_repo, trac, tmp_path, monkeypatch, ci_standin
):
    run_id, candidate, contract = _seed_candidate(host_repo, trac, tmp_path / "counts.json")
    _set_ci_env(monkeypatch, ci_standin, candidate)
    _issue_gates(host_repo, run_id, candidate)
    before = _counts(tmp_path / "counts.json")
    _replay_gates(host_repo, run_id, candidate)

    assert _counts(tmp_path / "counts.json") == before == {"quality": 1, "trace": 1}
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    _assert_complete_payload(_events(host_repo, run_id), candidate, digest)


@pytest.mark.parametrize("failure_mode,reason", [("fail_exit", "failed"), ("malformed_file", "malformed")])
# AC-FR0269-02: a partial result cannot skip a later declared gate.
def test_partial_gate_failure_runs_remaining_and_blocks(
    host_repo, trac, tmp_path, failure_mode, reason
):
    run_id, candidate, contract = _seed_candidate(
        host_repo, trac, tmp_path / "counts.json", second_mode=failure_mode
    )
    _issue_gates(host_repo, run_id, candidate)

    events = _events(host_repo, run_id)
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    gate_events = _gate_events(events)
    assert [event.payload["kind"] for event in gate_events] == ["quality", "trace"]
    assert gate_events[-1].type == "local_gate.failed"
    assert gate_events[-1].payload.get("reason") == reason
    assert _counts(tmp_path / "counts.json") == {"quality": 1, "trace": 1}
    _assert_complete_payload(events, candidate, digest)
    assert not any(
        event.type in {"ci.run_observed", "security.assessed", "publish.executed"}
        for event in events
    )
    _replay_gates(host_repo, run_id, candidate)
    assert _counts(tmp_path / "counts.json") == {"quality": 2, "trace": 2}


# AC-FR0269-02: a changed contract digest reruns the same candidate's gates.
def test_changed_contract_digest_reruns_same_candidate(
    host_repo, trac, tmp_path, monkeypatch, ci_standin
):
    counter = tmp_path / "counts.json"
    run_id, candidate, contract = _seed_candidate(host_repo, trac, counter)
    _set_ci_env(monkeypatch, ci_standin, candidate)
    _issue_gates(host_repo, run_id, candidate)
    old_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    before = _counts(counter)
    before_events = _events(host_repo, run_id)
    before_seq = max(event.seq for event in before_events)

    # This is an evidence-consumption premise only.  The frozen candidate is
    # unchanged; the dirty contract is not asserted to be release-eligible.
    _write_contract(host_repo, counter, second_mode="pass_file")
    new_digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    assert new_digest != old_digest
    _replay_gates(host_repo, run_id, candidate)

    assert _counts(counter) == {key: value + 1 for key, value in before.items()}
    events = _events(host_repo, run_id)
    fresh = [event for event in _gate_events(events) if event.seq > before_seq]
    assert fresh and all((event.payload or {}).get("contract_digest") == new_digest for event in fresh)
    _assert_complete_payload(fresh, candidate, new_digest)
