"""Hotfix swap scheduling (IF-SCHED-001, IF-PAUSE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.scheduler import Scheduler

pytestmark = pytest.mark.integration


# AC-FR0296-03@v0.9 TRACKS-TRACE second run queued
def test_second_run_queued(tmp_path: Path):
    """AC-FR0296-03: second accepted run queues while first is active."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    sched = Scheduler(db)
    sched.on_run_created("run-a", journey="feature", preempt=False, actor="human")
    sched.on_run_created("run-b", journey="feature", preempt=False, actor="human")
    snap = sched.queue_snapshot()
    assert snap["active_run"] == "run-a"
    assert "run-b" in snap["queued"]


# AC-FR0296-04@v0.9 TRACKS-TRACE hotfix preemption audited
def test_hotfix_preemption_audited(tmp_path: Path):
    """AC-FR0296-04: preempt pauses feature, activates hotfix, audits each step."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    sched = Scheduler(db)
    sched.on_run_created("run-feat", journey="feature", preempt=False, actor="human")
    sched.on_run_created("run-hot", journey="hotfix_post", preempt=True, actor="human")
    snap = sched.queue_snapshot()
    assert snap["active_run"] == "run-hot"
    events = db.read_events()
    kinds = [e["type"] for e in events]
    assert "run.pause_requested" in kinds
    assert "schedule.changed" in kinds
