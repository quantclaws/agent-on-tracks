"""Candidate-bound release authorization (IF-RELEASE-002/003).

This module is the single read-time gate for the release CLI.  It rechecks the
preview inputs and aggregates only evidence bound to the active candidate;
the CLI remains responsible for locking and appending the resulting event.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tracks.executor.helpers import git
from tracks.executor.host_contract import (
    CANONICAL_CONTRACT_RELPATH,
    load_host_contract,
    validate_host_contract,
)
from tracks.executor.local_gate_evidence import has_complete_passed_gates
from tracks.executor.release_gate import validate_release_decision
from tracks.executor.release_preview import (
    assemble_preview,
    preview_blob_matches,
    preview_inputs_match,
)

STALE_REASONS = frozenset(
    {"candidate_drift", "evidence_staled", "operation_plan_changed"}
)


@dataclass(frozen=True)
class ReleaseAuthorization:
    """Read-only authorization result consumed by CLI and status rendering."""

    stale_reason: str | None
    gate_status: dict
    detail: str

    @property
    def preview_stale(self) -> bool:
        return self.stale_reason is not None


def _raw_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _version_facts(version: str) -> dict:
    value = str(version or "")
    parts = value.lstrip("v").split(".")
    return {
        "version": value,
        "major": parts[0] if parts and parts[0].isdigit() else "0",
        "minor": parts[1] if len(parts) > 1 and parts[1].isdigit() else "0",
    }


def _payload(event) -> dict:
    value = event.payload if event is not None else {}
    return value if isinstance(value, dict) else {}


def _latest_bound(events: list, event_type: str, candidate: str):
    return next(
        (
            event
            for event in reversed(events)
            if event.type == event_type
            and _payload(event).get("candidate_sha") == candidate
        ),
        None,
    )


def _stale_marker(events: list, preview_event, candidate: str) -> str | None:
    for event in events:
        if event.seq <= preview_event.seq:
            continue
        reason = _stale_event_reason(event, candidate)
        if reason:
            return reason
    return None


def _stale_event_reason(event, candidate: str) -> str | None:
    payload = _payload(event)
    if event.type in {"candidate.stale", "evidence.staled"}:
        marked = payload.get("candidate_sha")
        if marked in (None, candidate):
            return "candidate_drift" if event.type == "candidate.stale" else "evidence_staled"
    if event.type == "candidate.frozen":
        fresh = payload.get("candidate_sha")
        if fresh and fresh != candidate:
            return "candidate_drift"
    return None


def _current_candidate(repo: Path, events: list, candidate: str) -> str | None:
    frozen = next(
        (event for event in reversed(events) if event.type == "candidate.frozen"),
        None,
    )
    frozen_payload = _payload(frozen)
    if (
        frozen_payload.get("candidate_sha") != candidate
        or frozen_payload.get("clean_tree") is not True
    ):
        return "candidate_drift"
    head = git(repo, "rev-parse", "HEAD", check=False)
    if head.returncode != 0 or head.stdout.strip() != candidate:
        return "candidate_drift"
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no", check=False)
    if dirty.returncode != 0 or dirty.stdout.strip():
        return "candidate_drift"
    return None


def _load_contract(repo: Path):
    path = repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
    try:
        raw = path.read_bytes()
        contract = load_host_contract(path)
    except (OSError, UnicodeError, ValueError):
        return None, None
    if validate_host_contract(contract, repo):
        return None, None
    return contract, _raw_digest(raw)


def _ci_status(events: list, candidate: str, contract) -> tuple[str, bool]:
    event = _latest_bound(events, "ci.run_observed", candidate)
    if event is None:
        return "unknown", False
    payload = _payload(event)
    declared = contract.ci or {}
    required = declared.get("required_checks") or []
    checks = payload.get("required_checks") or []
    valid = (
        payload.get("status") == "passed"
        and payload.get("api_verified") is True
        and payload.get("head_sha") == candidate
        and payload.get("workflow") == declared.get("workflow")
        and set(checks) == set(required)
        and payload.get("conclusion") == declared.get("conclusion", "success")
    )
    return ("passed" if valid else "failed"), valid


def _prism_status(events: list, candidate: str) -> tuple[str, bool]:
    event = next(
        (
            event
            for event in reversed(events)
            if event.type == "prism.verdict"
            and _payload(event).get("candidate_sha") == candidate
            and _payload(event).get("scope") == "verify_final"
        ),
        None,
    )
    if event is None:
        return "unknown", False
    valid = _payload(event).get("verdict") == "pass"
    return ("pass" if valid else "failed"), valid


def _security_status(events: list, candidate: str, contract_digest: str) -> tuple[str, bool]:
    event = _latest_bound(events, "security.assessed", candidate)
    if event is None:
        return "unknown", False
    payload = _payload(event)
    valid = payload.get("status") == "passed" and payload.get("policy_digest") == contract_digest
    return ("passed" if valid else "failed"), valid


def _stale_authorization(reason: str, detail: str) -> ReleaseAuthorization:
    return ReleaseAuthorization(
        reason,
        {"preview_stale": True, "gate_failed": True},
        detail,
    )


def _recomputed_preview(
    repo: Path, contract, digest: str, preview: dict, events: list, version: str
):
    plan = preview.get("operation_plan") or {}
    journey = plan.get("journey")
    if not isinstance(journey, str) or not journey:
        return None
    return assemble_preview(
        repo,
        contract,
        preview.get("candidate_sha"),
        "sha256:" + digest,
        _version_facts(version),
        events,
        journey=journey,
        known_issues=preview.get("known_issues"),
    )


def _gate_status(events: list, candidate: str, contract, digest: str) -> dict:
    local_ok = has_complete_passed_gates(events, candidate, digest, contract)
    ci_status, ci_ok = _ci_status(events, candidate, contract)
    prism_status, prism_ok = _prism_status(events, candidate)
    security_status, security_ok = _security_status(events, candidate, digest)
    return {
        "preview_stale": False,
        "gate_failed": not (local_ok and ci_ok and prism_ok and security_ok),
        "security_status": security_status,
        "prism_status": prism_status,
        "ci_status": ci_status,
    }


def _validate_preview_state(
    repo: Path, home: Path, events: list, preview_event, candidate: object
):
    if not isinstance(candidate, str) or not candidate:
        return ("operation_plan_changed", "preview missing candidate"), None, None
    marker = _stale_marker(events, preview_event, candidate)
    if marker:
        return (marker, marker), None, None
    drift = _current_candidate(repo, events, candidate)
    if drift:
        return (drift, drift), None, None
    preview = _payload(preview_event)
    if not preview_blob_matches(home, preview):
        return ("operation_plan_changed", "preview blob mismatch"), None, None
    contract, digest = _load_contract(repo)
    if contract is None:
        return ("operation_plan_changed", "host contract invalid"), None, None
    return None, contract, digest


def assess_release(
    repo: Path,
    home: Path,
    events: list,
    preview_event,
    version: str,
) -> ReleaseAuthorization:
    """Recompute current release authority, fail-closed on malformed input."""
    preview = _payload(preview_event)
    candidate = preview.get("candidate_sha")
    failure, contract, contract_digest = _validate_preview_state(
        repo, home, events, preview_event, candidate
    )
    if failure is not None:
        return _stale_authorization(*failure)
    current = _recomputed_preview(
        repo, contract, contract_digest, preview, events, version
    )
    if current is None or not preview_inputs_match(preview, current):
        return _stale_authorization("operation_plan_changed", "preview inputs changed")
    gate_status = _gate_status(events, candidate, contract, contract_digest)
    detail = "gate failed" if gate_status["gate_failed"] else "bound"
    return ReleaseAuthorization(None, gate_status, detail)


def validate_authorization(
    decision: str, authorization: ReleaseAuthorization, preview: dict
) -> tuple[bool, str | None]:
    """Use the existing release gate validator for all three actions."""
    return validate_release_decision(decision, preview, authorization.gate_status)
