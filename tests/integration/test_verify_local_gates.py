"""Integration: host-contract local gates (FR-0269, IF-VERIFY-003/IF-HOSTCONTRACT-001)."""

from __future__ import annotations

import pytest

from tracks.executor.host_contract import load_host_contract

pytestmark = pytest.mark.integration


# AC-FR0269-01@v0.8 TRACKS-TRACE contract gates run and pass with candidate binding
def test_contract_gates_run_and_pass(host_repo, trac, event_log):
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    # Contract loading is the IF-HOSTCONTRACT-001 boundary; must raise token pre-implementation
    try:
        load_host_contract(contract_path)
        raise AssertionError("load_host_contract must raise IF-HOSTCONTRACT-001")
    except NotImplementedError as exc:
        assert "IF-HOSTCONTRACT-001" in str(exc)
    except FileNotFoundError:
        # Also acceptable if file missing, but IF token is expected
        pytest.fail("load_host_contract must raise NotImplementedError(IF-HOSTCONTRACT-001)")

    trac("run")
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
    status = trac("status")
    assert "passed" in status.stdout
    # validate outlet must pass for well-formed contract
    v = trac("validate", "--file", str(contract_path))
    assert v.returncode == 0 or "host-contract" in v.stdout.lower()


# AC-FR0269-02@v0.8 TRACKS-TRACE missing or malformed gate fails closed with blocked
def test_missing_or_malformed_gate_fails_closed(host_repo, trac, event_log):
    from tracks.executor.host_contract import execute_gate, parse_gate_result

    try:
        execute_gate(None, host_repo, {})  # type: ignore[arg-type]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-HOSTCONTRACT-001" in str(exc) or "IF-VERIFY-003" in str(exc)
    try:
        parse_gate_result(None, "exit_code", 0)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-HOSTCONTRACT-001" in str(exc) or "IF-VERIFY-003" in str(exc)

    # Corrupt the contract by injecting an unknown gate kind via file mutation
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    if contract_path.exists():
        original = contract_path.read_text(encoding="utf-8")
        contract_path.write_text(
            original + '\n[[host-contract.local_gate]]\nkind="unknown_ghost"\n',
            encoding="utf-8",
        )
    trac("run")
    events = event_log()
    failed = [e for e in events if e["type"] == "local_gate.failed"]
    assert failed, "local_gate.failed must appear for unknown/malformed gate"
    assert any(
        f["payload"].get("reason") in ("unknown", "malformed", "missing_contract", "timeout")
        for f in failed
    )
    status = trac("status")
    assert "blocked" in status.stdout
    # repair_route must be present per §1.0.14
    assert any("repair_route" in f["payload"] for f in failed) or "repair" in status.stdout
    # Resume must re-run the affected verification rather than skip
    resume = trac("run", "--resume")
    events2 = event_log()
    assert len([e for e in events2 if e["type"] == "local_gate.failed"]) >= len(failed)
    assert resume.returncode != 0 or "blocked" in resume.stdout
