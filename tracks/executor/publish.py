"""M-PUBLISH executor domain (FR-0275, NFR-0144, IF-PUBLISH-001/002).

Write-ahead intent + per-operation idempotency key + remote readback
reconcile for the contract-declared external operations. The executor plans
and reconciles; the irreversible effects themselves live in the effects
boundary and are Runtime-only (agents never execute or simulate them).
"""

from __future__ import annotations

from typing import Literal

ReconcileVerdict = Literal["done", "skip", "pending", "conflict"]


def plan_operations(operation_plan: dict, preview_digest: str) -> list[dict]:
    """Write-ahead planned records, one per declared operation step."""
    raise NotImplementedError("IF-PUBLISH-001")


def operation_idempotency_key(preview_digest: str, kind: str, target: str) -> str:
    raise NotImplementedError("IF-PUBLISH-001")


def reconcile_operation(planned: dict, remote_state: dict) -> ReconcileVerdict:
    """Remote is authoritative: done→skip, mismatch→conflict, absent→pending.

    IF-PUBLISH-002: the remote decides. An executed op whose remote state
    matches the plan skips (reconciled_skip, exact-once); an absent target is
    pending (execute); the same key with divergent remote content is a
    conflict (reconcile_conflict audit, blocked).
    """
    remote = remote_state or {}
    exists = remote.get("exists", bool(remote))
    if not exists:
        return "pending"
    if remote.get("matches") is False:
        return "conflict"
    expected_digest = planned.get("digest")
    remote_digest = remote.get("digest")
    diverged = expected_digest is not None and remote_digest is not None
    if diverged and expected_digest != remote_digest:
        return "conflict"
    return "skip"


def assert_agent_forbidden(actor: str) -> None:
    """Agents attempting irreversible operations block with agent_forbidden."""
    raise NotImplementedError("IF-PUBLISH-001")
