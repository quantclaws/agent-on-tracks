"""Single-active-run scheduling and hotfix priority swap (IF-SCHED-001).

Scheduling semantics locked by SPEC-009 (Human ruling): many registered runs,
at most one active run driven at a time, and hotfix urgency served by an
explicit auditable swap sequence — pause the active feature run, drive the
hotfix to release, resume the feature — every step accepted through the
command service with actor + command_id on the timeline. True concurrency
(worktree isolation / multi-worker) is out of scope for this version.

Contract token: IF-SCHED-001.
"""

from __future__ import annotations

from typing import Any


class Scheduler:
    """Owns the schedule singleton row (active_run + ordered queue, §1c #8)."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def next_runnable(self) -> str | None:
        """The run_id the supervisor may drive now (active, not paused)."""
        raise NotImplementedError("IF-SCHED-001")

    def on_run_created(self, run_id: str, *, journey: str, preempt: bool, actor: str) -> None:
        """Enqueue; with preempt=true and an active run, start the auditable
        hotfix swap sequence (pause active -> activate hotfix); otherwise
        reject with active_run_exists (handled by the command service)."""
        raise NotImplementedError("IF-SCHED-001")

    def on_run_terminal(self, run_id: str) -> None:
        """Deactivate; resume the preempted feature run when applicable."""
        raise NotImplementedError("IF-SCHED-001")

    def queue_snapshot(self) -> dict:
        """{active_run, queued: [...]} for projections (queue is visible)."""
        raise NotImplementedError("IF-SCHED-001")
