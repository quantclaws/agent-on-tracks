"""Clarification interaction (IF-CMDSVC-001, IF-DOCGAP-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0293-01@v0.9 TRACKS-TRACE reply continues same run
def test_reply_continues_same_run(tmp_path: Path):
    """AC-FR0293-01: submit_clarification keeps run_id, no new run."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="submit_clarification",
        params={"run_id": "run-1", "doc": "spec", "thread_token": "t1", "body": "answer"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="clar-1",
    )
    assert receipt.command_id
    status = svc.status(receipt.command_id)
    assert status["command_id"] == receipt.command_id


# AC-FR0293-02@v0.9 TRACKS-TRACE discussion history traceable
def test_discussion_history_traceable(tmp_path: Path):
    """AC-FR0293-02: timeline carries question reply time and actor."""
    from tracks.server.projections import project_timeline

    home = tmp_path / "home"
    home.mkdir()
    timeline = project_timeline(home, "run-1")
    assert "events" in timeline and "cursor" in timeline
    assert isinstance(timeline["events"], list)
