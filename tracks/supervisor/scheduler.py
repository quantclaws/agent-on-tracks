"""Single-active-run scheduling and hotfix priority swap (IF-SCHED-001).

Scheduling semantics locked by SPEC-009 (Human ruling): many registered runs,
at most one active run driven at a time, and hotfix urgency served by an
explicit auditable swap sequence — pause the active feature run, drive the
hotfix to release, resume the feature — every step accepted through the
command service with actor + command_id on the timeline. True concurrency
(worktree isolation / multi-worker) is out of scope for this version.

The schedule singleton row (interfaces §1c #8) carries ``active_run`` plus the
ordered ``queue`` of accepted runs, and this module is its only writer. Every
active-slot switch appends an auditable ``schedule.changed`` (interfaces §1a
#18) carrying ``{active_run, queued, reason}`` with the closed reason
vocabulary: a created run taking the slot (``run_created``), the hotfix swap
(``hotfix_preemption``), the preempted run resumed (``run_resumed``), and a
terminal run leaving the schedule with the queue head promoted
(``run_terminal``). ``next_runnable`` hands the supervisor only the active run
— and nothing while a persisted pause request/effect
(``run.pause_requested`` / ``run.paused``) stands, so no wake-up can drive
past a pause (SM-02.5 priority).

Contract token: IF-SCHED-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

# Pause-relevant service events in delivery order (interfaces §1a #19-21); the
# latest one decides whether the active run may be driven.
_PAUSE_EVENTS = ("run.pause_requested", "run.paused", "run.resumed")
_PAUSE_BLOCKING = ("run.pause_requested", "run.paused")

_SCHEDULE_UPSERT = (
    "INSERT INTO schedule (id, active_run, queue_json) VALUES (1, ?, ?)"
    " ON CONFLICT(id) DO UPDATE SET active_run = excluded.active_run,"
    " queue_json = excluded.queue_json"
)


def _service_conn(db: Any) -> sqlite3.Connection:
    """Open the service.db connection that owns the schedule singleton row.

    Prefers the ServiceDB connection seam; a location-only double exposing
    ``db.home`` (interfaces §3 #1) is accepted through the same fallback.
    """
    open_seam = getattr(db, "_connect", None)
    if open_seam is None:
        home = getattr(db, "home", None)
        if home is None:
            raise TypeError("scheduler store must expose a ServiceDB connection")
        conn = sqlite3.connect(str(Path(home) / "service.db"))
        conn.row_factory = sqlite3.Row
        return conn
    return open_seam()


class Scheduler:
    """Owns the schedule singleton row (active_run + ordered queue, §1c #8)."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def next_runnable(self) -> str | None:
        """The run_id the supervisor may drive now (active, not paused)."""
        active = self._state()["active_run"]
        if not active:
            return None
        latest = None
        for event in self._db.read_events(run_id=active):
            if event.get("type") in _PAUSE_EVENTS:
                latest = event
        if latest is not None and latest.get("type") in _PAUSE_BLOCKING:
            return None
        return active

    def on_run_created(self, run_id: str, *, journey: str, preempt: bool, actor: str) -> None:
        """Enqueue; with preempt=true and an active run, start the auditable
        hotfix swap sequence (pause active -> activate hotfix); otherwise
        reject with active_run_exists (handled by the command service)."""
        state = self._state()
        active = state["active_run"]
        if not active:
            self._write(run_id, state["queued"])
            self._changed(run_id, state["queued"], "run_created")
            return
        if preempt:
            # Hotfix urgency: the submitted run takes the single active slot
            # and the preempted run is parked at the queue front so the next
            # switch resumes it (§1i). The pause/resume commands with their
            # actor + command_id ride the command service (AC-FR0296-04).
            queue = [active, *state["queued"]]
            self._write(run_id, queue)
            self._changed(run_id, queue, "hotfix_preemption")
            return
        # A queued run leaves the active slot untouched, so there is no
        # switch to record; the queue itself is the observable state (§1c #8).
        self._write(active, [*state["queued"], run_id])

    def on_run_terminal(self, run_id: str) -> None:
        """Deactivate; resume the preempted feature run when applicable."""
        state = self._state()
        active = state["active_run"]
        queue = state["queued"]
        if run_id == active:
            promoted = queue[0] if queue else None
            remaining = queue[1:]
            reason = "run_resumed" if promoted and self._preempted(run_id) else "run_terminal"
            self._write(promoted, remaining)
            self._changed(promoted, remaining, reason)
        elif run_id in queue:
            # A queued run never held the active slot: drop it from the queue
            # without fabricating a switch event.
            remaining = [queued for queued in queue if queued != run_id]
            self._write(active, remaining)

    def queue_snapshot(self) -> dict:
        """{active_run, queued: [...]} for projections (queue is visible)."""
        return self._state()

    def _state(self) -> dict:
        """The persisted schedule state; an unset row reads as empty."""
        stored = self._db.get_schedule()
        if stored is None:
            return {"active_run": None, "queued": []}
        return {"active_run": stored["active_run"], "queued": list(stored["queue"])}

    def _write(self, active_run: str | None, queue: list) -> None:
        """Persist the schedule singleton row (id=1) in one statement."""
        with contextlib.closing(_service_conn(self._db)) as conn:
            conn.execute(_SCHEDULE_UPSERT, (active_run, json.dumps(list(queue))))
            conn.commit()

    def _changed(self, active_run: str | None, queue: list, reason: str) -> None:
        """Append the auditable schedule.changed (interfaces §1a #18)."""
        self._db.append_event(
            "schedule.changed",
            {"active_run": active_run, "queued": list(queue), "reason": reason},
        )

    def _preempted(self, run_id: str) -> bool:
        """True when run_id took the active slot via a hotfix preemption."""
        for event in self._db.read_events():
            payload = event.get("payload") or {}
            if (
                event.get("type") == "schedule.changed"
                and payload.get("reason") == "hotfix_preemption"
                and payload.get("active_run") == run_id
            ):
                return True
        return False
