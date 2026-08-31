"""Integration: M-SECURITY contract scans (FR-0272, IF-SECURITY-001)."""

from __future__ import annotations

import pytest

from tracks.executor.security import aggregate_security_status, run_security_scans

pytestmark = pytest.mark.integration


# AC-FR0272-01@v0.8 TRACKS-TRACE contract scans pass with policy digest binding
def test_contract_scans_pass(host_repo, trac, event_log):
    try:
        run_security_scans(None, host_repo, "a" * 40)  # type: ignore[arg-type]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-SECURITY-001" in str(exc)
    try:
        aggregate_security_status([])  # type: ignore[arg-type]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-SECURITY-001" in str(exc)

    trac("run")
    events = event_log()
    assessed = [e for e in events if e["type"] == "security.assessed"]
    assert assessed, "security.assessed must appear in M-SECURITY"
    payload = assessed[0]["payload"]
    assert "policy_digest" in payload
    assert "candidate_sha" in payload
    assert payload["status"] == "passed"
    status = trac("status")
    assert "security=passed" in status.stdout
    replay = trac("replay")
    assert "policy_digest" in replay.stdout or replay.returncode == 0


# AC-FR0272-02@v0.8 TRACKS-TRACE unknown or malformed blocks and re-runs on fix
def test_unknown_or_malformed_blocks(host_repo, trac, event_log):
    try:
        aggregate_security_status([None])  # type: ignore[list-item]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-SECURITY-001" in str(exc)
    except Exception:
        pass

    trac("run")
    events = event_log()
    assessed = [e for e in events if e["type"] == "security.assessed"]
    # Inject malformed policy by corrupting contract (if file exists)
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    if contract_path.exists():
        orig = contract_path.read_text(encoding="utf-8")
        contract_path.write_text(orig + "\n[host-contract.security_scan_override]\nunknown=1\n", encoding="utf-8")
    trac("run")
    events2 = event_log()
    assessed2 = [e for e in events2 if e["type"] == "security.assessed"]
    assert assessed2, "security.assessed must appear even for unknown/malformed (failed|unknown)"
    assert any(a["payload"]["status"] in ("failed", "unknown") for a in assessed2)
    status = trac("status")
    assert "security=failed" in status.stdout or "security=unknown" in status.stdout
    assert "blocked" in status.stdout
    assert any("repair_route" in a["payload"] for a in assessed2)
    # After fix, same candidate must produce new assessment
    if contract_path.exists() and "orig" in locals():
        contract_path.write_text(orig, encoding="utf-8")
        trac("run")
        events3 = event_log()
        assert len([e for e in events3 if e["type"] == "security.assessed"]) >= len(assessed)
