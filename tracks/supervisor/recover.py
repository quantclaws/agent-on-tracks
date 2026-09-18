"""Startup recovery for the service plane (IF-RECOVER-001).

On serve start: requeue claimed commands (command.requeued with
reason=service_restart), keep persisted waits (retry_at preserved), expire
stale leases so a fresh generation is granted, and reconcile unfinished
effects through the existing v0.8 WAL/idempotency path (completed external
side effects are skipped, never repeated; nothing is lost).

Contract token: IF-RECOVER-001.
"""

from __future__ import annotations

from typing import Any


def recover_on_startup(db: Any) -> dict:
    """Requeue claimed commands / preserve waits / expire leases; returns a
    recovery summary {requeued: [...], waits_kept: [...], leases_expired: [...]}"""
    raise NotImplementedError("IF-RECOVER-001")
