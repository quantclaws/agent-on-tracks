"""Supervisor lease fencing (IF-LEASE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.lease import acquire_lease, current_generation, is_fenced

pytestmark = pytest.mark.integration


# AC-FR0296-01@v0.9 TRACKS-TRACE concurrent drive single dispatch
def test_concurrent_drive_single_dispatch(tmp_path: Path):
    """AC-FR0296-01: two racers, one generation wins, no double dispatch."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    first = acquire_lease(db, "run-1", "worker-a", 30)
    second = acquire_lease(db, "run-1", "worker-b", 30)
    assert second.generation == first.generation + 1
    assert current_generation(db, "run-1") == second.generation
    assert is_fenced(db, "run-1", first.generation) is True
    assert is_fenced(db, "run-1", second.generation) is False


# AC-FR0296-02@v0.9 TRACKS-TRACE stale generation late result quarantined
def test_stale_generation_late_result_quarantined(tmp_path: Path):
    """AC-FR0296-02: stale completion quarantined, run state unchanged."""
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    lease = acquire_lease(db, "run-1", "worker-a", 30)
    committed = db.complete_command("cmd-1", lease.generation + 99, {"ok": True}, None)
    assert committed is False
    events = db.read_events(run_id="run-1")
    assert any(e["type"] == "worker.late_result" for e in events)
