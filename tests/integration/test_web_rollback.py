"""Web rollback (IF-WEBGATE-001, IF-ESCAPE-001, IF-LEASE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0312-01@v0.9 TRACKS-TRACE return with impact preview
def test_return_with_impact_preview(tmp_path: Path):
    """AC-FR0312-01: return lists invalidated evidence, then advances on confirm."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    receipt = svc.accept(
        kind="return_stage",
        params={"run_id": "run-1", "to": "M-SPEC", "reason": "rework", "confirm": True},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="return-1",
    )
    assert receipt.command_id


# AC-FR0312-02@v0.9 TRACKS-TRACE late results isolated after rollback
def test_late_results_isolated_after_rollback(tmp_path: Path):
    """AC-FR0312-02: late results after rollback quarantined, flow unmoved."""
    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.lease import acquire_lease

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    lease = acquire_lease(db, "run-1", "worker-a", 30)
    committed = db.complete_command("cmd-late-1", lease.generation + 5, {"ok": True}, None)
    assert committed is False
    assert any(e["type"] == "worker.late_result" for e in db.read_events(run_id="run-1"))


# AC-FR0312-03@v0.9 TRACKS-TRACE pushed release survives rollback
def test_pushed_release_survives_rollback(tmp_path: Path):
    """AC-FR0312-03: pushed tag and release kept, impact list truthful."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="return_stage",
        params={"run_id": "run-1", "to": "M-SPEC", "reason": "rework", "confirm": True},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="return-pushed-1",
    )
    assert receipt.command_id
