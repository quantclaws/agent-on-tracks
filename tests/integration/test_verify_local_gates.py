"""Integration: host-contract local gates (FR-0269, IF-VERIFY-003/IF-HOSTCONTRACT-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_awaiting_release``) — bare ``trac run`` bootstrap is forbidden
(v0.8 suite-wide defect, no init/start -> rc=1). The host declares its
``[host-contract.*]`` table through the walker's ``pre_seed_hook`` so it lands
in the same phase0 seed commit and the real M-VERIFY handler consumes it
(never a fixture-fabricated event). The release chain needs the loopback CI
stand-in (``ci_echo_standin``); the fail-closed half drives the production
``run_local_gates`` handler directly over the real Store because a
contract-level refusal opens a repair round whose re-arm owns the next move.
"""

from __future__ import annotations

import subprocess

import pytest

from tests._support.host_contracts import declare_host_contract, declared_contract_toml
from tests.e2e.helpers import walk_to_awaiting_release
from tracks.executor.host_contract import load_host_contract, validate_host_contract

pytestmark = pytest.mark.integration

# Minimal well-formed contract (neutral vocabulary, IF-HOSTCONTRACT-001
# closed-set shape) for loader/validator boundary probes.
_MINIMAL_CONTRACT_TOML = """\
[host-contract]
version = 1
language = "python"
toolchain = "cpython>=3.10"
install = "tool-pkg install -e ."

[[host-contract.local_gate]]
kind = "quality"
source = "guard_registry"
categories = ["lint_format"]
result_channel = "exit_code"
timeout_seconds = 60
"""

# Declared-but-unknown gate vocabulary: the contract load fails closed before
# any command is guessed (AC-FR0269-02 / IF-HOSTCONTRACT-001 closed set).
_GHOST_GATE_TOML = (
    declared_contract_toml()
    + '\n[[host-contract.local_gate]]\nkind = "unknown_ghost"\n'
)


def _declared_contract(repo) -> None:
    body = declared_contract_toml(extra_gates=(("trace", "true"),))
    declare_host_contract(repo, body)


# AC-FR0269-01@v0.8 TRACKS-TRACE contract gates run and pass with candidate binding
def test_contract_gates_run_and_pass(host_repo, trac, event_log, tmp_path, ci_echo_standin):
    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): a well-formed
    # contract loads and validates clean — the entry premise for gate runs.
    probe_path = tmp_path / "project.toml"
    probe_path.write_text(_MINIMAL_CONTRACT_TOML, encoding="utf-8")
    loaded = load_host_contract(probe_path)
    assert loaded.language == "python"
    assert validate_host_contract(loaded, tmp_path) == ()

    walk_to_awaiting_release(trac, pre_seed_hook=_declared_contract)
    events = event_log()
    passed = [e for e in events if e["type"] == "local_gate.passed"]
    assert passed, "local_gate.passed events must appear per declared gate kind"
    for ev in passed:
        p = ev["payload"]
        assert "kind" in p
        assert p["kind"] in ("quality", "trace", "reach", "anti_slop", "version", "build", "smoke")
        assert "candidate_sha" in p
        assert "contract_digest" in p
        assert "normalized_result" in p
        nr = p["normalized_result"]
        assert nr.get("schema") == "tracks-gate-result"
        assert nr.get("version") == 1
    # Every declared executable gate kind produced its real passed event.
    assert {"quality", "trace"} <= {e["payload"]["kind"] for e in passed}
    # The materialized event binds the declared contract path + schema
    # version. (The M-DESIGN completion first materializes the runtime
    # default for an undeclared host; the declared bytes record source=host.)
    materialized = [e for e in events if e["type"] == "host_contract.materialized"]
    assert materialized, "host_contract.materialized must appear"
    payload = next(
        e["payload"] for e in materialized if e["payload"].get("source") == "host"
    )
    assert payload["version"] == 1
    assert str(host_repo) in payload["contract_path"]
    status = trac("status")
    assert "passed" in status.stdout
    # validate outlet must pass for the well-formed declared contract
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    v = trac("validate", "--file", str(contract_path))
    assert v.returncode == 0, v.stdout + v.stderr


# AC-FR0269-02@v0.8 TRACKS-TRACE missing or malformed gate fails closed with blocked
def test_missing_or_malformed_gate_fails_closed(host_repo, trac, event_log):
    from tracks import paths
    from tracks.executor.executor import Executor
    from tracks.executor.host_contract import parse_gate_result
    from tracks.kernel.events import Command
    from tracks.store import Store

    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): exit-code outcomes
    # normalize fail-closed and malformed blobs never pass.
    assert parse_gate_result(None, "exit_code", 0).status == "passed"
    assert parse_gate_result(None, "exit_code", 3).status == "failed"
    assert parse_gate_result(b"not json{{", "file", None).status == "malformed"

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    # The declared contract carries an unknown gate kind; the real M-VERIFY
    # handler must refuse it fail-closed (never guess a command).
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(_GHOST_GATE_TOML, encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", str(contract_path.relative_to(host_repo))],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "declare ghost-gate host contract"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=host_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-VERIFY"})
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": "main",
                "frozen_at_seq": 0,
            },
        )
        executor = Executor(store, host_repo, run_id)
        command = Command(
            kind="run_local_gates", params={"candidate_sha": candidate}
        )
        executor._execute(command, store.state(run_id), None, False)
        events = list(store.events(run_id))
        # Resume (the production reconcile replay) must re-run the affected
        # verification rather than skip it.
        executor._execute(
            Command(
                kind="run_local_gates",
                params={"candidate_sha": candidate},
                command_id=command.command_id,
            ),
            store.state(run_id),
            None,
            True,
        )
        events2 = list(store.events(run_id))
    finally:
        store.close()

    def _rows(rows):
        return [{"type": e.type, "payload": dict(e.payload or {})} for e in rows]

    failed = [e for e in _rows(events) if e["type"] == "local_gate.failed"]
    assert failed, "local_gate.failed must appear for unknown/malformed gate"
    assert any(
        f["payload"].get("reason") in ("unknown", "malformed", "missing_contract", "timeout")
        for f in failed
    )
    invalid = [e for e in _rows(events) if e["type"] == "host_contract.invalid"]
    assert invalid, "host_contract.invalid must refuse the unknown gate kind"
    assert invalid[-1]["payload"]["reason"] == "missing_contract"
    # Resume re-ran the affected verification (a new refusal on the stream).
    failed2 = [e for e in _rows(events2) if e["type"] == "local_gate.failed"]
    assert len(failed2) > len(failed), "resume must re-run the failed verification, not skip"
    assert not [e for e in events2 if e.type == "local_gate.passed"]
    status = trac("status")
    assert "blocked" in status.stdout
    # repair_route must be present per §1.0.14
    assert any("repair_route" in f["payload"] for f in failed) or "repair" in status.stdout
