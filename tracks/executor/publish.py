"""M-PUBLISH executor domain (FR-0275, NFR-0144, IF-PUBLISH-001/002).

Write-ahead intent + per-operation idempotency key + remote readback
reconcile for the contract-declared external operations. The executor plans
and reconciles; the irreversible effects themselves live in the effects
boundary and are Runtime-only (agents never execute or simulate them).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

ReconcileVerdict = Literal["done", "skip", "pending", "conflict"]


def plan_operations(operation_plan: dict, preview_digest: str) -> list[dict]:
    """Write-ahead planned records, one per declared operation step."""
    if not isinstance(operation_plan, dict) or not isinstance(preview_digest, str):
        return []
    records: list[dict] = []
    for step in operation_plan.get("steps") or ():
        if not isinstance(step, str) or ":" not in step:
            return []
        kind, target = step.split(":", 1)
        if not kind or not target:
            return []
        records.append(
            {
                "operation_kind": kind,
                "target": target,
                "preview_digest": preview_digest,
                "idempotency_key": operation_idempotency_key(
                    preview_digest, kind, target
                ),
            }
        )
    return records


def operation_idempotency_key(preview_digest: str, kind: str, target: str) -> str:
    raw = json.dumps(
        {"preview_digest": preview_digest, "kind": kind, "target": target},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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


class PublishBlocked(RuntimeError):
    """Structured refusal of a publish actor that is not Runtime/human."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_ALLOWED_PUBLISH_ACTORS = frozenset({"runtime", "human"})


def assert_agent_forbidden(actor: object) -> None:
    """Fail closed unless the actor is the Runtime (or an explicit human).

    IF-PUBLISH-001: irreversible operations are Runtime-only. An empty or
    non-string actor is ``malformed_actor``; any Agent role name blocks as
    ``agent_forbidden``. Pure function: no I/O, no state.
    """
    if not isinstance(actor, str) or not actor.strip():
        raise PublishBlocked("malformed_actor")
    if actor.strip().lower() in _ALLOWED_PUBLISH_ACTORS:
        return None
    raise PublishBlocked("agent_forbidden")
