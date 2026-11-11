"""Shared release-evidence fixtures for the hotfix-262 release unit tests.

The payloads and contract snippets below are enumerated from interfaces §1b /
§1f and the v0.8 host-contract test harness; they are shared so the release
test modules do not duplicate the same fixture blocks (R0801).
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.unit.full_f_authority_support import authoritative_payload
from tracks.executor.host_contract import GATE_RESULT_PROTOCOL, GATE_RESULT_VERSION

CONTRACT_HEADER = (
    "[host-contract]\n"
    "version = 1\n"
    "language = 'python'\n"
    "toolchain = 'cpython'\n"
    "install = ''\n"
)
VERSION_SCHEME = (
    "\n[host-contract.version_scheme]\n"
    "feature_tag = 'v{minor}.0'\n"
    "patch_line = 'v{minor}.{n}'\n"
    "prerelease_tag = 'v{minor}.{n}-pre.{ulid}'\n"
)
LOCAL_GATE_SECTION = (
    "\n[[host-contract.local_gate]]\n"
    "kind = 'quality'\n"
    "source = 'command'\n"
    "command = 'quality'\n"
    "result_channel = 'exit_code'\n"
)
SECURITY_SCAN_SECTION = (
    "\n[[host-contract.security_scan]]\n"
    "id = 'security'\ntool = 'fixture'\ntool_version = '1'\n"
    "install = ''\ncommand = 'true'\nresult_channel = 'exit_code'\n"
    "threshold = '0'\ntimeout_seconds = 5\n"
)
CI_SECTION = (
    "\n[host-contract.ci]\n"
    "repo_env = 'CI_REPO'\n"
    "workflow = 'ci.yml'\n"
    "required_checks = ['quality']\n"
)
OPERATIONS_SECTION = (
    "\n[host-contract.operations.feature]\n"
    "steps = ['tag:{feature_tag}']\n"
)

MINIMAL_HOST_CONTRACT_TOML = CONTRACT_HEADER + VERSION_SCHEME + OPERATIONS_SECTION
FULL_HOST_CONTRACT_TOML = (
    CONTRACT_HEADER
    + VERSION_SCHEME
    + LOCAL_GATE_SECTION
    + SECURITY_SCAN_SECTION
    + CI_SECTION
    + OPERATIONS_SECTION
)

RELEASE_FACTS = {
    "version": "v0.8",
    "major": "0",
    "minor": "0.8",
    "feature_tag": "v0.8.0",
    "patch_line": "v0.8.{n}",
    "prerelease_tag": "v0.8.{n}-pre.{ulid}",
}

IDENTITY = {
    "tree": "tree-1",
    "command": ["pytest"],
    "env": "env-1",
    "selection_id": "selection-1",
}


def quality_gate_payload(
    candidate: str,
    contract_digest: str,
    *,
    status: str = "passed",
    summary: dict | None = None,
) -> dict:
    passed = status == "passed"
    return {
        "kind": "quality",
        "gate_identity": "quality[0]",
        "candidate_sha": candidate,
        "contract_digest": contract_digest,
        "command_echo": ["quality"],
        "normalized_result": {
            "schema": GATE_RESULT_PROTOCOL,
            "version": GATE_RESULT_VERSION,
            "status": status,
            "exit_code": 0 if passed else 7,
            "summary": dict(summary) if summary is not None else {"status": status},
            "gate_id": "quality",
        },
        "reason": None if passed else "gate_failed",
    }


def ev(seq: int, event_type: str, payload: dict, command_id=None):
    return SimpleNamespace(
        seq=seq, type=event_type, payload=payload, command_id=command_id
    )


def full_payload(candidate: str, *, eligible: bool = True, **overrides) -> dict:
    """A bound authoritative FULL_F payload (7362 shape)."""
    values = {
        "execution_commit": candidate,
        "passed": eligible,
        "full_f_eligible": eligible,
    }
    values.update(overrides)
    payload = authoritative_payload(**values)
    payload["candidate_sha"] = candidate
    return payload


def ci_payload(candidate: str) -> dict:
    return {
        "status": "passed",
        "repo": "acme/host",
        "workflow": "ci.yml",
        "run_id": 17,
        "head_sha": candidate,
        "candidate_sha": candidate,
        "conclusion": "success",
        "required_checks": ["quality"],
        "api_verified": True,
    }


def prism_payload(
    scope: str, candidate: str, findings: list | None = None
) -> dict:
    payload = {"candidate_sha": candidate, "scope": scope, "verdict": "pass"}
    if findings is not None:
        payload["findings"] = findings
    return payload


def security_payload(candidate: str) -> dict:
    return {"candidate_sha": candidate, "status": "passed"}


def reused_full_payload(candidate: str) -> dict:
    return {
        "kind": "full_f",
        "candidate_sha": candidate,
        "source_event_seq": 99,
        "identity_basis": ["tree-1", ["pytest"], "env-1", "selection-1"],
    }


def release_evidence(candidate: str) -> list[tuple[str, dict]]:
    """One successful event per IF-RELEV-001 required key."""
    return [
        ("full.executed", full_payload(candidate)),
        ("ci.run_observed", ci_payload(candidate)),
        ("prism.verdict", prism_payload("verify_final", candidate)),
        ("prism.verdict", prism_payload("security", candidate)),
        ("security.assessed", security_payload(candidate)),
    ]
