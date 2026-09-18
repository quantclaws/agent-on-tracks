"""Service-plane SQLite store: ``<service_home>/service.db`` (interfaces §1c).

Owns the service-plane schema (``service_events`` / ``projects`` /
``commands`` / ``waits`` / ``leases`` / ``auth`` / ``sessions`` /
``schedule``) and the append-only service event log. The server process is
the single writer; read paths open independent read-only connections
(NFR-0150). Run-plane business events stay in the project's tracks.db — this
module never writes there.

Contract tokens: IF-CMDSVC-001, IF-SERVE-001, IF-RECOVER-001.
"""

from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = 1

# Closed set of service-plane event types (interfaces §1a). tracks.db
# EVENT_TYPES is NOT extended in v0.9 — the run-plane closed set is frozen.
SERVICE_EVENT_TYPES = (
    "service.started",
    "service.stopped",
    "project.registered",
    "project.registration_rejected",
    "project.readiness_checked",
    "command.accepted",
    "command.deduplicated",
    "command.rejected",
    "command.claimed",
    "command.completed",
    "command.failed",
    "command.requeued",
    "wait.entered",
    "wait.resolved",
    "lease.acquired",
    "lease.released",
    "worker.late_result",
    "schedule.changed",
    "run.pause_requested",
    "run.paused",
    "run.resumed",
    "material.edited",
    "access.denied",
    "auth.login",
)

COMMAND_STATUSES = ("accepted", "claimed", "completed", "failed", "rejected")


class ServiceDB:
    """Single-writer service-plane store (interfaces §1c tables 1-8)."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def append_event(
        self,
        type: str,
        payload: dict,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        command_id: str | None = None,
    ) -> int:
        """Append one service event (type in SERVICE_EVENT_TYPES) -> seq."""
        raise NotImplementedError("IF-CMDSVC-001")

    def read_events(self, *, run_id: str | None = None, after_seq: int = 0) -> list:
        """Read service events in seq order (read-only consumers)."""
        raise NotImplementedError("IF-QUERY-001")

    def register_command(self, command: dict) -> str:
        """Persist a command row (status=accepted) and return command_id."""
        raise NotImplementedError("IF-CMDSVC-001")

    def find_by_idempotency(self, idempotency_key: str) -> dict | None:
        """Look up a command by its unique idempotency key (§1b.2)."""
        raise NotImplementedError("IF-CMDSVC-001")

    def claim_command(self, command_id: str, worker_id: str, generation: int) -> bool:
        """CAS accepted->claimed bound to the lease generation (§1i)."""
        raise NotImplementedError("IF-LEASE-001")

    def complete_command(
        self, command_id: str, generation: int, result: dict | None, failure: dict | None
    ) -> bool:
        """CAS claimed->completed/failed; False means a stale generation
        (the caller emits ``worker.late_result`` and discards the outcome)."""
        raise NotImplementedError("IF-LEASE-001")

    def requeue_claimed(self, reason: str) -> list:
        """Recovery: claimed -> accepted with command.requeued (§1h.7)."""
        raise NotImplementedError("IF-RECOVER-001")

    def get_command(self, command_id: str) -> dict | None:
        """Command row by id (status query, restart-safe)."""
        raise NotImplementedError("IF-CMDSVC-001")
