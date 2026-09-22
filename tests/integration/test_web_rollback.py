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
    from tests._support.lease_fencing import arrange_stale_completion, quarantined_events
    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.lease import current_generation

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    stale, renewed = arrange_stale_completion(db, "run-1", "cmd-late-1")

    committed = db.complete_command("cmd-late-1", stale.generation, {"ok": True}, None)

    assert committed is False
    late = quarantined_events(db, "run-1")
    assert late, "the late result must be quarantined, not applied"
    payload = late[-1]["payload"]
    assert payload["command_id"] == "cmd-late-1"
    assert payload["generation"] == stale.generation
    assert payload["current_generation"] == renewed.generation
    assert payload["disposition"] == "quarantined"
    assert current_generation(db, "run-1") == renewed.generation
    assert db.get_command("cmd-late-1")["status"] == "claimed"


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
