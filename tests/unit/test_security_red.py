"""T-034 RED: M-SECURITY contract scans (FR-0272, IF-SECURITY-001).

Pins the still-unimplemented slices of tracks/executor/security.py:

- AC-FR0272-01: contract-declared scans install (if declared) -> run ->
  normalized result; the deep-audit review envelope carries scan_results +
  policy_digest + candidate_sha with scope=security.
- AC-FR0272-02: aggregate fail-closed — passed iff EVERY declared scan
  passed; any failed -> failed; malformed/missing/unknown -> unknown
  (nothing verified can never authorize a release).

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import replace

import pytest

from tracks.executor.host_contract import (
    HostContract,
    NormalizedGateResult,
    SecurityScanDecl,
    VersionDecl,
)
from tracks.executor.security import (
    aggregate_security_status,
    build_security_review_assignment,
    run_security_scans,
    security_assessed_payload,
)


def _calls(fn, *args, label: str):
    try:
        return fn(*args)
    except NotImplementedError as err:
        raise AssertionError(f"assertion failure: {label} not implemented") from err


def _scan(scan_id: str = "pip-audit", command: str = "true") -> SecurityScanDecl:
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


def _result(status: str, gate_id: str = "pip-audit") -> NormalizedGateResult:
    return NormalizedGateResult(
        gate_id=gate_id, result_version=1, status=status, exit_code=0, summary={}
    )


# AC-FR0272-01@v0.8 TRACKS-TRACE IF-SECURITY-001 aggregate passed
def test_aggregate_security_status_passed():
    """AC-FR0272-01: passed iff EVERY declared scan passed."""
    results = [_result("passed", "pip-audit"), _result("passed", "bandit")]
    outcome = _calls(aggregate_security_status, results, label="aggregate_security_status")
    assert outcome == "passed", (
        f"assertion failure: all scans passed must aggregate passed, got {outcome!r}"
    )


# AC-FR0272-02@v0.8 TRACKS-TRACE IF-SECURITY-001 aggregate fail-closed
def test_aggregate_security_status_fail_closed():
    """AC-FR0272-02: any failed -> failed; malformed/missing -> unknown;
    an empty scan set verified nothing and can never pass."""
    one_failed = _calls(
        aggregate_security_status,
        [_result("passed"), _result("failed", "bandit")],
        label="aggregate_security_status",
    )
    assert one_failed == "failed", (
        f"assertion failure: any failed scan must aggregate failed, got {one_failed!r}"
    )
    malformed = _calls(
        aggregate_security_status,
        [_result("passed"), None],
        label="aggregate_security_status",
    )
    assert malformed == "unknown", (
        f"assertion failure: malformed result must aggregate unknown, got {malformed!r}"
    )
    malformed_status = _calls(
        aggregate_security_status,
        [_result("malformed")],
        label="aggregate_security_status",
    )
    assert malformed_status == "unknown", (
        f"assertion failure: malformed status must aggregate unknown, got {malformed_status!r}"
    )
    empty = _calls(aggregate_security_status, [], label="aggregate_security_status")
    assert empty == "unknown", (
        f"assertion failure: an empty scan set verifies nothing and must "
        f"aggregate unknown, got {empty!r}"
    )


# AC-FR0272-01@v0.8 TRACKS-TRACE IF-SECURITY-001 scan execution
def test_run_security_scans_executes_declared(tmp_path):
    """AC-FR0272-01: every contract-declared scan runs (exit_code channel
    synthesized by the Runtime) and yields a normalized result bound to its
    scan_id; a failing command surfaces failed, not a crash."""
    contract = _contract(_scan("pip-audit", "true"), _scan("bandit", "false"))
    results = _calls(
        run_security_scans, contract, tmp_path, "a" * 40, label="run_security_scans"
    )
    assert isinstance(results, list) and len(results) == 2, (
        f"assertion failure: one normalized result per declared scan, got {results!r}"
    )
    by_id = {r.gate_id: r for r in results}
    assert by_id["pip-audit"].status == "passed", (
        f"assertion failure: successful command must pass, got {by_id!r}"
    )
    assert by_id["bandit"].status == "failed", (
        f"assertion failure: failing command must surface failed, got {by_id!r}"
    )
    for r in results:
        assert r.result_version == 1


@pytest.mark.parametrize("install", ["false", "missing-scan-installer", "'unterminated"])
def test_scan_install_failure_cannot_be_overridden_by_successful_scan(tmp_path, install):
    """AC-FR0272-02: a failed policy prerequisite blocks execution."""
    marker = tmp_path / "scan-ran"
    scan = replace(_scan(command=f"touch {shlex.quote(str(marker))}"), install=install)
    results = run_security_scans(_contract(scan), tmp_path, "a" * 40)
    assert aggregate_security_status(results) != "passed"
    assert not marker.exists()
    assert results[0].summary["phase"] == "install"


@pytest.mark.parametrize("payload, expected", [
    (None, "malformed"),
    ({"schema": "tracks-gate-result", "version": 99, "status": "passed"}, "malformed"),
    ({"schema": "tracks-gate-result", "version": 1, "status": "failed", "summary": {}}, "failed"),
    ({"schema": "tracks-gate-result", "version": 1, "status": "passed", "summary": {}}, "passed"),
])
def test_scan_file_channel_requires_valid_normalized_result(tmp_path, payload, expected):
    """AC-FR0272-01/02: exit zero cannot stand in for the declared file result."""
    writer = tmp_path / "scanner.py"
    writer.write_text(
        "import pathlib, sys\n"
        + (f"pathlib.Path(sys.argv[1]).write_text({json.dumps(payload)!r})\n" if payload else "")
    )
    scan = replace(
        _scan(command=f"{shlex.quote(sys.executable)} {shlex.quote(str(writer))} {{result}}"),
        result_channel="file",
    )
    result = run_security_scans(_contract(scan), tmp_path, "a" * 40)[0]
    assert result.status == expected
    assert result.gate_id == scan.scan_id


def test_security_event_preserves_normalized_result_evidence():
    """AC-FR0272-01: replay must retain the result version and scanner evidence."""
    result = replace(_result("failed"), summary={"finding": "dependency advisory"})
    payload = security_assessed_payload("a" * 40, "policy", [result])
    scan = payload["scans"][0]
    assert scan["result_version"] == result.result_version
    assert scan["summary"] == result.summary


# AC-FR0272-01@v0.8 TRACKS-TRACE IF-SECURITY-001 prism review envelope
def test_build_security_review_assignment_envelope():
    """AC-FR0272-01: the deep-audit review envelope carries scan_results +
    policy_digest + candidate_sha and is scoped to security (prism.verdict
    scope=security per §1f)."""
    results = [_result("passed")]
    envelope = _calls(
        build_security_review_assignment,
        results,
        "sha256:" + "p" * 64,
        "a" * 40,
        label="build_security_review_assignment",
    )
    assert isinstance(envelope, dict), "assertion failure: envelope must be dict"
    assert envelope.get("scope") == "security" or envelope.get("prism_scope") == "security", (
        f"assertion failure: review envelope must be scoped to security, got {envelope!r}"
    )
    assert envelope.get("candidate_sha") == "a" * 40
    assert envelope.get("policy_digest") == "sha256:" + "p" * 64
    assert envelope.get("scan_results") == results


# AC-FR0272-02@v0.8 TRACKS-TRACE IF-SECURITY-001 undeclared policy fail-closed
def test_run_security_scans_undeclared_policy_fails_closed(tmp_path):
    """FR-0272-02 / AC-FR0272-02: a None or malformed contract (undeclared
    security policy) must FAIL CLOSED — run_security_scans returns an empty
    result set (aggregating to unknown → blocked) instead of crashing with an
    AttributeError, and never infers or skips a policy item."""
    for broken_contract in (None, object()):
        try:
            results = run_security_scans(broken_contract, tmp_path, "a" * 40)
        except NotImplementedError as err:
            raise AssertionError(
                "assertion failure: run_security_scans not implemented for "
                "undeclared policy"
            ) from err
        except Exception as err:  # noqa: BLE001
            raise AssertionError(
                "assertion failure: an undeclared/malformed contract must fail "
                f"closed (empty scan set → unknown → blocked), got "
                f"{type(err).__name__}: {err}"
            ) from err
        assert results == [], (
            f"assertion failure: an undeclared policy has zero scans to run — "
            f"must return an empty set (aggregating unknown → blocked), got {results!r}"
        )
    # the fail-closed chain: empty set aggregates to unknown (blocked), never pass
    outcome = _calls(aggregate_security_status, [], label="aggregate_security_status")
    assert outcome == "unknown"
