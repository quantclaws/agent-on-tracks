from dataclasses import replace

from tracks.executor.host_contract import (
    HostContract,
    SecurityScanDecl,
    VersionDecl,
)
from tracks.executor.security import (
    security_policy_digest,
    security_scan_evidence_complete,
)


def _contract(*scans: SecurityScanDecl) -> HostContract:
    return HostContract(
        contract_version=1,
        language="python",
        toolchain="3.12",
        install="uv sync",
        local_gates=(),
        version=VersionDecl("feature", "patch", "rc"),
        build_command="python -m build",
        build_artifact="dist/pkg.whl",
        smoke=(),
        security_scans=scans,
        ci={},
        tracker={},
    )


def _scan(scan_id="sast") -> SecurityScanDecl:
    return SecurityScanDecl(
        scan_id=scan_id,
        tool="bandit",
        tool_version="1.8.0",
        install="uv pip install bandit==1.8.0",
        command="bandit -r .",
        result_channel="exit_code",
        threshold="high",
        timeout_seconds=60,
    )


def _entry(scan_id="sast", **overrides):
    entry = {
        "id": scan_id,
        "status": "passed",
        "result_version": 1,
        "summary": {},
        "exit_code": 0,
    }
    entry.update(overrides)
    return entry


def test_digest_covers_each_scan_field_and_preserves_order():
    contract = _contract(_scan("first"), _scan("second"))
    original = security_policy_digest(contract)
    for field in (
        "scan_id",
        "tool",
        "tool_version",
        "install",
        "command",
        "result_channel",
        "threshold",
        "timeout_seconds",
    ):
        changed = replace(contract.security_scans[0], **{field: 61 if field == "timeout_seconds" else "changed"})
        assert security_policy_digest(replace(contract, security_scans=(changed, contract.security_scans[1]))) != original
    assert security_policy_digest(replace(contract, security_scans=tuple(reversed(contract.security_scans)))) != original


def test_digest_ignores_unrelated_host_fields():
    contract = _contract(_scan())
    changed = replace(contract, language="ruby", ci={"required": True}, tracker={"x": 1})
    assert security_policy_digest(changed) == security_policy_digest(contract)


def test_complete_evidence_accepts_id_and_scan_id_event_shapes():
    contract = _contract(_scan("sast"), _scan("deps"))
    assert security_scan_evidence_complete(contract, [_entry("sast"), _entry("deps")])
    assert security_scan_evidence_complete(
        contract, [{"scan_id": name, **{k: v for k, v in _entry(name).items() if k != "id"}}
         for name in ("sast", "deps")]
    )


def test_incomplete_duplicate_unknown_and_malformed_evidence_fails_closed():
    contract = _contract(_scan("sast"), _scan("deps"))
    valid = [_entry("sast"), _entry("deps")]
    cases = [
        valid[:1],
        [_entry("sast"), _entry("sast")],
        [_entry("sast"), _entry("unknown")],
        [_entry("sast", status="failed"), _entry("deps")],
        [_entry("sast", result_version=True), _entry("deps")],
        [_entry("sast", summary=[]), _entry("deps")],
        [_entry("sast", exit_code=True), _entry("deps")],
        [_entry("sast"), None],
        {"sast": _entry("sast")},
    ]
    for entries in cases:
        assert not security_scan_evidence_complete(contract, entries)


def test_malformed_declarations_fail_closed():
    assert not security_scan_evidence_complete(_contract(), [])
    assert not security_scan_evidence_complete(_contract(_scan(""), _scan("")), [])
