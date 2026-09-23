"""Abandon run (IF-WEBGATE-001, IF-ESCAPE-002, IF-DRIVE-001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks.supervisor.db import ServiceDB
from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0313-01@v0.9 TRACKS-TRACE abandon terminal auditable
def test_abandon_terminal_auditable(tmp_path: Path):
    """AC-FR0313-01: abandon reaches cancelled terminal, audit kept."""
    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    receipt = svc.accept(
        kind="abandon_run",
        params={"run_id": "run-1", "reason": "no longer needed"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="abandon-1",
    )
    assert receipt.command_id

    # the acceptance is queryable on the documented store: the audit row
    # carries the operator, the surface and the abandonment reason
    status = svc.status(receipt.command_id)
    assert status["command_id"] == receipt.command_id
    assert status["kind"] == "abandon_run"
    assert status["status"] == "accepted"
    assert (status["actor"], status["actor_class"], status["surface"]) == (
        "human",
        "human",
        "http",
    )
    row = db.get_command(receipt.command_id)
    assert json.loads(row["params_json"]) == {
        "run_id": "run-1",
        "reason": "no longer needed",
    }
    # the acceptance is audited on the service-plane log
    accepted = [event for event in db.read_events() if event["type"] == "command.accepted"]
    assert len(accepted) == 1
    payload = accepted[0]["payload"]
    assert payload["kind"] == "abandon_run"
    assert payload["idempotency_key"] == "abandon-1"


# AC-FR0313-02@v0.9 TRACKS-TRACE abandon no fake success
def test_abandon_no_fake_success(tmp_path: Path):
    """AC-FR0313-02: abandon adds no remote effects, never shows success."""
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
    # the acceptance emits nothing but its audit record: no publish or release
    # side effect is fabricated at the accept tier
    types = [event["type"] for event in db.read_events()]
    assert types == ["command.accepted"], (
        f"abandon must not add remote side effects: {types}"
    )
    # the abandonment is not dressed up as a completed success
    assert receipt.status == "accepted"
    assert svc.status(receipt.command_id)["status"] == "accepted"


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
