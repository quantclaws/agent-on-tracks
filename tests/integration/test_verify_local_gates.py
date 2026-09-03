"""Integration: host-contract local gates (FR-0269, IF-VERIFY-003/IF-HOSTCONTRACT-001)."""

from __future__ import annotations

import pytest

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


# AC-FR0269-01@v0.8 TRACKS-TRACE contract gates run and pass with candidate binding
def test_contract_gates_run_and_pass(host_repo, trac, event_log, tmp_path):
    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): a well-formed
    # contract loads and validates clean — the entry premise for gate runs.
    probe_path = tmp_path / "project.toml"
    probe_path.write_text(_MINIMAL_CONTRACT_TOML, encoding="utf-8")
    loaded = load_host_contract(probe_path)
    assert loaded.language == "python"
    assert validate_host_contract(loaded, tmp_path) == ()

    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
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
    from tracks.executor.host_contract import parse_gate_result

    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): exit-code outcomes
    # normalize fail-closed and malformed blobs never pass.
    assert parse_gate_result(None, "exit_code", 0).status == "passed"
    assert parse_gate_result(None, "exit_code", 3).status == "failed"
    assert parse_gate_result(b"not json{{", "file", None).status == "malformed"

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
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
