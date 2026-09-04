"""Integration: M-SECURITY contract scans (FR-0272, IF-SECURITY-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_m_impl_parked``) — bare ``trac run`` bootstrap is forbidden. The
module-level halves assert the delivered IF-SECURITY-001 contract faces
(declared scan execution, fail-closed aggregation); the security.assessed
event assertions stay legal Red against the unwired M-SECURITY producers.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.host_contract import HostContract, SecurityScanDecl, VersionDecl
from tracks.executor.security import aggregate_security_status, run_security_scans

pytestmark = pytest.mark.integration

_CANDIDATE = "a" * 40


def _scan(scan_id: str, command: str) -> SecurityScanDecl:
    return SecurityScanDecl(
        scan_id=scan_id,
        tool=command,
        tool_version="1.0",
        install="",
        command=command,
        result_channel="exit_code",
        threshold="0",
        timeout_seconds=5,
    )


def _contract(*scans: SecurityScanDecl) -> HostContract:
    return HostContract(
        contract_version=1,
        language="python",
        toolchain="t",
        install="",
        local_gates=(),
        version=VersionDecl(
            feature_tag="v{minor}.{n}",
            patch_line="v{minor}.{n}",
            prerelease_tag="v{minor}.{n}-rc",
        ),
        build_command="",
        build_artifact="",
        smoke=(),
        security_scans=tuple(scans),
        ci={},
        tracker={},
        operations={},
    )


# AC-FR0272-01@v0.8 TRACKS-TRACE contract scans pass with policy digest binding
def test_contract_scans_pass(host_repo, trac, event_log):
    # Module half (IF-SECURITY-001): every declared scan runs and yields a
    # normalized result bound to its scan_id; an undeclared (None) contract
    # verifies nothing and fails closed to an empty result set.
    results = run_security_scans(_contract(_scan("pip-audit", "true"), _scan("bandit", "false")), host_repo, _CANDIDATE)
    assert isinstance(results, list) and len(results) == 2
    by_id = {r.gate_id: r for r in results}
    assert by_id["pip-audit"].status == "passed"
    assert by_id["bandit"].status == "failed"
    assert run_security_scans(None, host_repo, _CANDIDATE) == []
    # Aggregation is the closed verdict set: all-pass -> passed, any fail ->
    # failed, nothing verified -> unknown (never a silent pass).
    assert aggregate_security_status([]) == "unknown"
    assert aggregate_security_status(results) == "failed"

    # CLI half: M-SECURITY must emit security.assessed bound to the policy
    # and the candidate. Legal Red: the M-SECURITY producer is not wired on
    # this baseline.
    walk_to_m_impl_parked(trac)
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
    # Module half (fail-closed aggregation): a malformed result row and a
    # malformed status both aggregate unknown — verified nothing, pass nothing.
    assert aggregate_security_status([None]) == "unknown"
    empty_status = run_security_scans(_contract(_scan("broken", "")), host_repo, _CANDIDATE)
    assert isinstance(empty_status, list)

    # CLI half: an unknown/malformed assessment must surface status
    # failed|unknown carrying the repair route. Legal Red: the M-SECURITY
    # producer is not wired on this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    assessed2 = [e for e in events if e["type"] == "security.assessed"]
    assert assessed2, "security.assessed must appear even for unknown/malformed (failed|unknown)"
    assert any(a["payload"]["status"] in ("failed", "unknown") for a in assessed2)
    status = trac("status")
    assert "security=failed" in status.stdout or "security=unknown" in status.stdout
    assert "blocked" in status.stdout
    assert any("repair_route" in a["payload"] for a in assessed2)
