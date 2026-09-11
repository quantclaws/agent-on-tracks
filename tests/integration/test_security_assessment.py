"""Integration: M-SECURITY contract scans (FR-0272, IF-SECURITY-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_m_impl_parked``) — bare ``trac run`` bootstrap is forbidden. The
host declares its ``[host-contract.*]`` table through the walker's
``pre_seed_hook`` so it lands in the same phase0 seed commit and the real
M-SECURITY handler consumes it (declared scan execution, fail-closed
aggregation). The loopback CI stand-in (``ci_echo_standin``) keeps the
M-VERIFY readback green so the chain reaches M-SECURITY.
"""

from __future__ import annotations

import pytest

from tests._support.host_contracts import declare_host_contract, declared_contract_toml
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


def _declare_malformed_scan(repo) -> None:
    """Declared contract whose scan command is empty: the Runtime must
    normalize it fail-closed (malformed -> aggregate unknown), never run a
    guessed command or treat "no output" as a pass (AC-FR0272-02). The quality
    gate and CI binding stay declared so the chain reaches M-SECURITY."""
    declare_host_contract(repo, declared_contract_toml(scan_command=""))


# AC-FR0272-01@v0.8 TRACKS-TRACE contract scans pass with policy digest binding
def test_contract_scans_pass(host_repo, trac, event_log, ci_echo_standin):
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

    # CLI half: the real M-SECURITY producer runs the contract-declared
    # scans on the frozen candidate and lands security.assessed.
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
def test_unknown_or_malformed_blocks(host_repo, trac, event_log, ci_echo_standin):
    # Module half (fail-closed aggregation): a malformed result row and a
    # malformed status both aggregate unknown — verified nothing, pass nothing.
    assert aggregate_security_status([None]) == "unknown"
    empty_status = run_security_scans(_contract(_scan("broken", "")), host_repo, _CANDIDATE)
    assert isinstance(empty_status, list)
    assert empty_status[0].status == "malformed"

    # CLI half: the declared empty scan command must surface
    # security.assessed status unknown carrying the repair route.
    walk_to_m_impl_parked(trac, pre_seed_hook=_declare_malformed_scan)
    events = event_log()
    assessed2 = [e for e in events if e["type"] == "security.assessed"]
    assert assessed2, "security.assessed must appear even for unknown/malformed (failed|unknown)"
    assert any(a["payload"]["status"] in ("failed", "unknown") for a in assessed2)
    malformed = [a for a in assessed2 if a["payload"]["status"] == "unknown"]
    assert malformed, "an empty declared scan command must fail closed to unknown"
    assert any("repair_route" in a["payload"] for a in malformed)
    status = trac("status")
    assert "security=failed" in status.stdout or "security=unknown" in status.stdout
    assert "blocked" in status.stdout
