"""Single-active-run scheduling and hotfix priority swap (IF-SCHED-001).

Scheduling semantics locked by SPEC-009 (Human ruling): many registered runs,
at most one active run driven at a time, and hotfix urgency served by an
explicit auditable swap sequence — pause the active feature run, drive the
hotfix to release, resume the feature — every step accepted through the
command service with actor + command_id on the timeline. True concurrency
(worktree isolation / multi-worker) is out of scope for this version.

The schedule singleton row (interfaces §1c #8) carries ``active_run`` plus the
ordered ``queue`` of accepted runs, and this module is its only writer. Every
active-slot switch is persisted together with an auditable ``schedule.changed``
(interfaces §1a #18) carrying ``{active_run, queued, reason}`` with the closed
reason vocabulary: a created run taking the slot (``run_created``), the hotfix
swap (``hotfix_preemption``), the preempted run resumed (``run_resumed``), and
a terminal run leaving the schedule with the queue head promoted
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
    """Owns the schedule singleton row (active_run + ordered queue, §1c #8).

    The swap steps are not written here directly: they are accepted through
    the command service (``self._service``) so the full §1b.1 acceptance tiers
    — guard, schema, idempotency, actor_class — apply to every step, exactly
    as they do for operator-submitted commands (interfaces §1i).
    """

    def __init__(self, db: Any, service: Any = None) -> None:
        self._db = db
        self._service = service

    def next_runnable(self) -> str | None:
        """The run_id the supervisor may drive now (active, not paused)."""
        active = self._state()["active_run"]
        if active and not self._pause_blocks(active):
            return active
        return None

    def on_run_created(self, run_id: str, *, journey: str, preempt: bool, actor: str) -> None:
        """Enqueue; with preempt=true and an active run, start the auditable
        hotfix swap sequence (pause active -> activate hotfix); otherwise
        reject with active_run_exists (handled by the command service)."""
        del journey  # journey-aware prechecks ride the command service (FR-0292)
        state = self._state()
        active = state["active_run"]
        if run_id == active:
            # Re-acceptance of the already-active run (same canonical run
            # identity): nothing to schedule — never a duplicate queue entry.
            return
        if not active:
            self._switch(run_id, state["queued"], "run_created")
            return
        if preempt:
            # AC-FR0296-04: the preempted active run is paused by accepting a
            # ``pause_run`` command through the command service (actor=提交人,
            # command_id on the audit trail, SM-01.1) before the hotfix takes
            # the single active slot (§1i).
            self._accept_swap_step("pause_run", active, actor=actor, actor_class="human")
            self._switch(run_id, [active, *state["queued"]], "hotfix_preemption")
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
            resumed = promoted is not None and self._preempted(run_id)
            if resumed:
                self._resume_preempted(promoted)
            reason = "run_resumed" if resumed else "run_terminal"
            self._switch(promoted, remaining, reason)
        elif run_id in queue:
            # A queued run never held the active slot: drop it from the queue
            # without fabricating a switch event.
            self._write(active, [queued for queued in queue if queued != run_id])

    def queue_snapshot(self) -> dict:
        """{active_run, queued: [...]} for projections (queue is visible)."""
        return self._state()

    def _state(self) -> dict:
        """The persisted schedule state; an unset row reads as empty."""
        stored = self._db.get_schedule()
        if stored is None:
            return {"active_run": None, "queued": []}
        return {"active_run": stored["active_run"], "queued": list(stored["queue"])}

    def _pause_blocks(self, run_id: str) -> bool:
        """True when the latest persisted pause state blocks driving run_id.

        A ``run.pause_requested`` blocks at once — SM-02.5: no new drive may
        start before the worker reaches its boundary — and ``run.paused``
        keeps it blocked; only a later ``run.resumed`` lifts the block
        (interfaces §1a #19-21).
        """
        latest = None
        for event in self._db.read_events(run_id=run_id):
            if event.get("type") in _PAUSE_EVENTS:
                latest = event
        return latest is not None and latest.get("type") in _PAUSE_BLOCKING

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

    def _commands(self) -> Any:
        """The command-service seam for the swap steps (interfaces §1b/§1i).

        The composition root injects the shared ``CommandService``; a
        scheduler built standalone (``Scheduler(db)``) lazily builds one over
        the same store so every swap step still rides the acceptance tiers.
        """
        if self._service is None:
            from tracks.supervisor.service import CommandService  # cycle-safe import

            self._service = CommandService(Path(self._db.home), self._db, {})
        return self._service

    def _accept_swap_step(
        self, kind: str, run_id: str, *, actor: str, actor_class: str
    ) -> str:
        """Accept one hotfix-swap step (pause_run / resume_run) as a command.

        The step rides the full §1b.1 acceptance tiers (guard, schema,
        idempotency, actor_class) exactly like an operator-submitted command:
        the command row reaches ``accepted`` and ``command.accepted`` plus the
        kind event (``run.pause_requested`` / ``run.resumed``) land on the
        timeline with the operator actor and the assigned command_id
        (interfaces §1i — AC-FR0296-04). Returns the command_id.
        """
        receipt = self._commands().accept(
            kind,
            {"run_id": run_id},
            actor=actor,
            actor_class=actor_class,
            surface="internal",
            idempotency_key=None,
        )
        return str(receipt.command_id)

    def _resume_preempted(self, run_id: str) -> None:
        """AC-FR0296-04: resume the preempted feature through the command
        service (resume_run accepted under the swap submitter's identity —
        a human-only kind, interfaces §1b#9) before it retakes the slot."""
        submitter = self._preemption_submitter(run_id)
        if submitter is None:
            return
        self._accept_swap_step("resume_run", run_id, actor=submitter, actor_class="human")

    def _preemption_submitter(self, run_id: str) -> str | None:
        """Actor recorded on the run's pause request — the operator whose
        hotfix create_run opened the swap (§1i actor=提交人). None when the
        run was never paused on the service plane."""
        submitter = None
        for event in self._db.read_events(run_id=run_id):
            if event.get("type") == "run.pause_requested":
                actor = (event.get("payload") or {}).get("actor")
                if actor:
                    submitter = str(actor)
        return submitter

    def _write(self, active_run: str | None, queue: list[str]) -> None:
        """Persist the schedule singleton row (id=1) in one statement."""
        with contextlib.closing(_service_conn(self._db)) as conn:
            conn.execute(_SCHEDULE_UPSERT, (active_run, json.dumps(list(queue))))
            conn.commit()

    def _switch(self, active_run: str | None, queue: list[str], reason: str) -> None:
        """Persist an active-slot switch together with its audit event.

        Keeping the row write and the ``schedule.changed`` append in one place
        holds every switch and its audit record in lockstep (interfaces §1a
        #18); queue edits that do not switch the active run use ``_write``.
        """
        self._write(active_run, queue)
        self._db.append_event(
            "schedule.changed",
            {"active_run": active_run, "queued": list(queue), "reason": reason},
        )
