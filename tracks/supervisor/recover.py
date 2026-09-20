"""Startup recovery for the service plane (IF-RECOVER-001).

On serve start: requeue claimed commands (``command.requeued`` with
reason=service_restart), keep persisted waits (retry_at preserved), expire
stale leases so a fresh generation is granted, and reconcile unfinished
effects through the existing v0.8 WAL/idempotency path (completed external
side effects are skipped, never repeated; nothing is lost).

Contract token: IF-RECOVER-001.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from typing import Any

from tracks.supervisor.lease import _connect as _lease_connect
from tracks.supervisor.waiting import active_wait_runs


def _expired_leases(db: Any) -> list:
    """Lease rows whose ``expires_at`` has already lapsed (interfaces §1c #5).

    The row is deliberately left in place: the single leases row is the
    fencing floor, so deleting it would let a dead worker's generation look
    current again. Expired rows are only reported (and audited by the caller)
    — the next acquire computes ``previous + 1`` from the preserved row.
    """
    now = datetime.now(timezone.utc)
    with contextlib.closing(_lease_connect(db)) as conn:
        rows = conn.execute(
            "SELECT run_id, worker_id, generation, expires_at FROM leases"
        ).fetchall()
    expired = []
    for row in rows:
        try:
            expires_at = datetime.fromisoformat(str(row["expires_at"]))
        except (TypeError, ValueError):
            continue
        if expires_at <= now:
            expired.append(
                {
                    "run_id": str(row["run_id"]),
                    "worker_id": str(row["worker_id"]),
                    "generation": int(row["generation"]),
                }
            )
    return expired


def recover_on_startup(db: Any) -> dict:
    """Requeue claimed commands / preserve waits / expire stale leases; returns
    a recovery summary {requeued: [...], waits_kept: [...], leases_expired: [...]}

    Composition-root startup order (architecture §1.1): claimed commands go
    back to ``accepted`` through ``command.requeued(reason=service_restart)``
    keeping the SAME command_id, so the existing WAL/idempotency replay skips
    already-completed external effects (no loss, no duplicate side effects).
    Persisted waits are durable state and are reported, never rewritten
    (``retry_at`` stays exactly as entered). A lapsed lease is released with
    ``lease.released(reason=expired)`` so the scheduler grants a fresh
    generation while the fencing floor stays monotonic.
    """
    requeue = getattr(db, "requeue_claimed", None)
    requeued = list(requeue("service_restart")) if callable(requeue) else []
    waits_kept = list(active_wait_runs(db))
    leases_expired: list = []
    for lease in _expired_leases(db):
        db.append_event(
            "lease.released",
            {
                "run_id": lease["run_id"],
                "worker_id": lease["worker_id"],
                "generation": lease["generation"],
                "reason": "expired",
            },
            run_id=lease["run_id"],
        )
        leases_expired.append(lease["run_id"])
    return {
        "requeued": requeued,
        "waits_kept": waits_kept,
        "leases_expired": leases_expired,
    }
