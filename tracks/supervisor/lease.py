"""Lease / generation fencing for the single effective executor (IF-LEASE-001).

One run has at most one effective executor at any moment: leases carry a
monotonic generation, command claim/complete is fenced by CAS on that
generation, and outcomes arriving from a stale generation are quarantined
(``worker.late_result``) without touching run state. On lease expiry the
supervisor terminates the old worker process before granting a new
generation (interfaces §1i).

Contract token: IF-LEASE-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Lease:
    run_id: str
    worker_id: str
    generation: int
    ttl_s: int


def acquire_lease(db: Any, run_id: str, worker_id: str, ttl_s: int) -> Lease:
    """Acquire/renew the run lease with generation = previous + 1 (txn)."""
    raise NotImplementedError("IF-LEASE-001")


def release_lease(db: Any, lease: Lease, reason: str) -> None:
    """Release the lease (completed|paused|preempted|expired)."""
    raise NotImplementedError("IF-LEASE-001")


def current_generation(db: Any, run_id: str) -> int:
    """Current fencing generation for the run (0 = no lease ever)."""
    raise NotImplementedError("IF-LEASE-001")


def is_fenced(db: Any, run_id: str, generation: int) -> bool:
    """True when the generation is stale (caller must quarantine, not act)."""
    raise NotImplementedError("IF-LEASE-001")
