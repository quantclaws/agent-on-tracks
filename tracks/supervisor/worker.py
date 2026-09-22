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

import os
import subprocess
import sys
from typing import Any

_WORKER_MODULE = "tracks.supervisor.worker_main"


class WorkerManager:
    """Owns worker subprocesses and the claim/complete fencing boundary."""

    def __init__(self, db: Any, scheduler: Any, config: Any) -> None:
        self._db = db
        self._scheduler = scheduler
        self._config = config
        self._workers: dict[str, tuple] = {}

    def spawn(self, command: dict, lease: Any) -> str:
        """Claim the command under the lease and spawn the worker; return pid ref.

        Returns an empty ref when the fenced claim CAS fails (another
        generation already owns the command) — the caller must not launch a
        second executor for it.
        """
        command_id = str(command.get("command_id") or "")
        if not command_id:
            return ""
        generation = int(getattr(lease, "generation", 0) or 0)
        worker_id = str(getattr(lease, "worker_id", "") or f"worker-{os.getpid()}")
        if not self._db.claim_command(command_id, worker_id, generation):
            return ""
        run_id = str(command.get("run_id") or "")
        proc = subprocess.Popen(
            [sys.executable, "-m", _WORKER_MODULE, "--command-id", command_id],
            env=self._worker_env(),
        )
        self._workers[command_id] = (proc, generation, run_id)
        return str(proc.pid)

    def _worker_env(self) -> dict:
        env = dict(os.environ)
        home = getattr(self._db, "home", None)
        if home is not None:
            env["TRAC_SERVE_HOME"] = str(home)
        return env

    def poll(self) -> list:
        """Reap finished workers; commit via fenced CAS; emit late_result."""
        finished: list = []
        for command_id, entry in list(self._workers.items()):
            proc, generation, run_id = entry
            if proc.poll() is None:
                continue
            del self._workers[command_id]
            finished.append(self._reap(command_id, proc, generation, run_id))
        return finished

    def _reap(self, command_id: str, proc: Any, generation: int, run_id: str) -> dict:
        """Fenced outcome commit for one finished worker subprocess."""
        row = self._db.get_command(command_id)
        status = row.get("status") if row else None
        if row is not None and status == "claimed":
            failure = {
                "failure_class": "unrecoverable",
                "reason": (
                    f"worker exited {proc.returncode} without committing its outcome"
                ),
            }
            if self._db.complete_command(command_id, generation, None, failure):
                status = "failed"
            else:
                # The fencing CAS rejected the outcome; the store quarantines
                # a stale generation with worker.late_result (§1a #17).
                status = "quarantined"
        return {
            "command_id": command_id,
            "run_id": run_id,
            "returncode": proc.returncode,
            "status": status,
        }

    def terminate_stale(self, run_id: str, grace_s: float) -> None:
        """SIGTERM then SIGKILL the worker of an expired lease (§1i)."""
        for entry in list(self._workers.values()):
            proc, _, worker_run = entry
            if worker_run != run_id or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def effectuate_pause(self, run_id: str) -> None:
        """At the drive boundary, emit run.paused (SM-02.5 second phase).

        Only a pending ``run.pause_requested`` is effectuated: the latest
        pause-relevant event must be the request itself (a later ``run.paused``
        means it already took effect and a ``run.resumed`` withdraws it) — no
        fabricated pause is ever emitted.
        """
        latest = None
        for event in self._db.read_events(run_id=run_id):
            if event.get("type") in (
                "run.pause_requested",
                "run.paused",
                "run.resumed",
            ):
                latest = event
        if latest is None or latest.get("type") != "run.pause_requested":
            return
        payload = latest.get("payload") or {}
        self._db.append_event(
            "run.paused",
            {
                "run_id": run_id,
                "actor": payload.get("actor"),
                "command_id": payload.get("command_id"),
                "at_boundary": "drive_boundary",
            },
            run_id=run_id,
            command_id=payload.get("command_id"),
        )
