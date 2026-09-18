"""Supervisor-side worker manager (interfaces §1i).

Claims accepted commands under the run lease, spawns the isolated worker
subprocess (``python -m tracks.supervisor.worker_main``), watches liveness,
and commits completions through the fencing CAS. Pause takes effect at the
current drive boundary (run.pause_requested -> run.paused); on lease expiry
the manager SIGTERMs then SIGKILLs the stale worker before any new
generation is granted. HTTP handlers never reach this module's execution
path.

Contract tokens: IF-LEASE-001, IF-PAUSE-001, IF-DRIVE-001, IF-RECOVER-001.
"""

from __future__ import annotations

from typing import Any


class WorkerManager:
    """Owns worker subprocesses and the claim/complete fencing boundary."""

    def __init__(self, db: Any, scheduler: Any, config: Any) -> None:
        self._db = db

    def spawn(self, command: dict, lease: Any) -> str:
        """Claim the command under the lease and spawn the worker; return pid ref."""
        raise NotImplementedError("IF-DRIVE-001")

    def poll(self) -> list:
        """Reap finished workers; commit via fenced CAS; emit late_result."""
        raise NotImplementedError("IF-LEASE-001")

    def terminate_stale(self, run_id: str, grace_s: float) -> None:
        """SIGTERM then SIGKILL the worker of an expired lease (§1i)."""
        raise NotImplementedError("IF-LEASE-001")

    def effectuate_pause(self, run_id: str) -> None:
        """At the drive boundary, emit run.paused (SM-02.5 second phase)."""
        raise NotImplementedError("IF-PAUSE-001")
