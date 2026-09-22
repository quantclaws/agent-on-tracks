"""Lease / generation fencing for the single effective executor (IF-LEASE-001).

One run has at most one effective executor at any moment: leases carry a
monotonic generation, command claim/complete is fenced by CAS on that
generation, and outcomes arriving from a stale generation are quarantined
(``worker.late_result``) without touching run state. On lease expiry the
supervisor terminates the old worker process before granting a new
generation (interfaces §1i).

The single ``leases`` row per run (interfaces §1c #5) doubles as the fencing
floor: acquire/renew computes ``previous + 1`` inside a ``BEGIN IMMEDIATE``
transaction so two supervisors can never reuse a generation; release is
itself generation-fenced (a late release from a superseded worker cannot
clear the current lease, and the counter never rolls back); a stale outcome
is quarantined with an auditable ``worker.late_result`` that leaves lease
state untouched (interfaces §1a #17).

Contract token: IF-LEASE-001.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Lease:
    run_id: str
    worker_id: str
    generation: int
    ttl_s: int


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db: Any) -> sqlite3.Connection:
    """A connection to the service.db holding the leases table.

    The ServiceDB store exposes its connection seam; duck-typed doubles that
    only know the store location (``db.home``, interfaces §3 #1) still work.
    """
    connect = getattr(db, "_connect", None)
    if callable(connect):
        return connect()
    home = getattr(db, "home", None)
    if home is None:
        raise TypeError("lease store must expose a connection (ServiceDB-shaped)")
    conn = sqlite3.connect(str(Path(home) / "service.db"))
    conn.row_factory = sqlite3.Row
    return conn


_LEASE_UPSERT = (
    "INSERT INTO leases (run_id, worker_id, generation, acquired_at, expires_at)"
    " VALUES (?, ?, ?, ?, ?)"
    " ON CONFLICT(run_id) DO UPDATE SET worker_id = excluded.worker_id,"
    " generation = excluded.generation, acquired_at = excluded.acquired_at,"
    " expires_at = excluded.expires_at"
)


def acquire_lease(db: Any, run_id: str, worker_id: str, ttl_s: int) -> Lease:
    """Acquire/renew the run lease with generation = previous + 1 (txn)."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(seconds=ttl_s)).isoformat()
    with contextlib.closing(_connect(db)) as conn:
        conn.isolation_level = None  # drive the write transaction explicitly
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT generation FROM leases WHERE run_id = ?", (run_id,)
        ).fetchone()
        generation = int(row["generation"]) + 1 if row is not None else 1
        conn.execute(_LEASE_UPSERT, (run_id, worker_id, generation, now, expires_at))
        conn.commit()
    db.append_event(
        "lease.acquired",
        {
            "run_id": run_id,
            "worker_id": worker_id,
            "generation": generation,
            "ttl_s": ttl_s,
        },
        run_id=run_id,
    )
    return Lease(run_id=run_id, worker_id=worker_id, generation=generation, ttl_s=ttl_s)


def release_lease(db: Any, lease: Lease, reason: str) -> None:
    """Release the lease (completed|paused|preempted|expired).

    Generation-fenced: a release from a superseded worker is a no-op — it
    must never clear the current lease or roll the fencing floor back.
    """
    with contextlib.closing(_connect(db)) as conn:
        row = conn.execute(
            "SELECT generation FROM leases WHERE run_id = ?", (lease.run_id,)
        ).fetchone()
        if row is None or int(row["generation"]) != lease.generation:
            return
    db.append_event(
        "lease.released",
        {
            "run_id": lease.run_id,
            "worker_id": lease.worker_id,
            "generation": lease.generation,
            "reason": reason,
        },
        run_id=lease.run_id,
    )


def current_generation(db: Any, run_id: str) -> int:
    """Current fencing generation for the run (0 = no lease ever)."""
    with contextlib.closing(_connect(db)) as conn:
        row = conn.execute(
            "SELECT generation FROM leases WHERE run_id = ?", (run_id,)
        ).fetchone()
    return int(row["generation"]) if row is not None else 0


def is_fenced(db: Any, run_id: str, generation: int) -> bool:
    """True when the generation is stale (caller must quarantine, not act)."""
    return generation < current_generation(db, run_id)


def quarantine_late_result(
    db: Any, run_id: str, command_id: str, generation: int
) -> bool:
    """Quarantine an outcome from a stale generation; True when quarantined.

    Emits the auditable ``worker.late_result(disposition=quarantined)`` event
    (interfaces §1a #17) carrying the current generation and never touches
    lease or run state; an outcome from the current generation is not
    quarantined and returns False with no event written.
    """
    current = current_generation(db, run_id)
    if generation >= current:
        return False
    db.append_event(
        "worker.late_result",
        {
            "run_id": run_id,
            "command_id": command_id,
            "generation": generation,
            "current_generation": current,
            "disposition": "quarantined",
        },
        run_id=run_id,
    )
    return True
