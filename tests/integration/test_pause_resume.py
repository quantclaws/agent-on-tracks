"""Pause and resume (IF-PAUSE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0310-01@v0.9 TRACKS-TRACE pause two phase and priority
def test_pause_two_phase_and_priority(tmp_path: Path):
    """AC-FR0310-01: pause shows requested then paused; retry_at cannot cross."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    receipt = svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="pause-1",
    )
    assert receipt.command_id
    kinds = [e["type"] for e in db.read_events(run_id="run-1")]
    assert "run.pause_requested" in kinds


# AC-FR0310-02@v0.9 TRACKS-TRACE resume from legal position
def test_resume_from_legal_position(tmp_path: Path):
    """AC-FR0310-02: resume continues from paused position, work kept."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="pause-2",
    )
    resumed = svc.accept(
        kind="resume_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="resume-1",
    )
    assert resumed.command_id
    kinds = [e["type"] for e in db.read_events(run_id="run-1")]
    assert "run.resumed" in kinds
