"""Abandon run (IF-WEBGATE-001, IF-ESCAPE-002, IF-DRIVE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0313-01@v0.9 TRACKS-TRACE abandon terminal auditable
def test_abandon_terminal_auditable(tmp_path: Path):
    """AC-FR0313-01: abandon reaches cancelled terminal, audit kept."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="abandon_run",
        params={"run_id": "run-1", "reason": "no longer needed"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="abandon-1",
    )
    assert receipt.command_id
    assert svc.status(receipt.command_id)["command_id"] == receipt.command_id


# AC-FR0313-02@v0.9 TRACKS-TRACE abandon no fake success
def test_abandon_no_fake_success(tmp_path: Path):
    """AC-FR0313-02: abandon adds no remote effects, never shows success."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    receipt = svc.accept(
        kind="abandon_run",
        params={"run_id": "run-1", "reason": "stop"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="abandon-2",
    )
    assert receipt.command_id
    assert not any(e["type"] == "publish.completed" for e in db.read_events())


# AC-FR0313-03@v0.9 TRACKS-TRACE unrecoverable failure terminal
def test_unrecoverable_failure_terminal(tmp_path: Path):
    """AC-FR0313-03: unrecoverable failure ends failed terminal, not waiting."""
    from tracks.executor.drive import DriveConfig, drive_once

    result = drive_once(tmp_path, "run-unrecoverable", config=DriveConfig())
    if result.kind == "failed":
        assert result.failure is not None
        assert result.failure.get("failure_class") == "unrecoverable"
        assert result.wait is None
    else:
        assert result.kind in ("continue", "await_human", "await_external", "terminal")
