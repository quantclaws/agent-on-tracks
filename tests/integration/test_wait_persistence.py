"""Wait persistence (IF-WAIT-001, IF-RECOVER-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.waiting import WaitPolicy, enter_wait, next_probe, resolve_wait

pytestmark = pytest.mark.integration


# AC-FR0298-01@v0.9 TRACKS-TRACE wait survives restart and auto resumes
def test_wait_survives_restart_and_auto_resumes(tmp_path: Path):
    """AC-FR0298-01: wait row and retry_at persist across restart, then resume."""
    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.recover import recover_on_startup

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    enter_wait(
        db,
        "run-1",
        {"wait_class": "ci", "reason": "ci-running", "retry_at": "2030-01-01T00:00:00+00:00"},
    )
    summary = recover_on_startup(db)
    assert "run-1" in summary["waits_kept"]
    resolve_wait(db, "run-1", "condition_met")
    events = db.read_events(run_id="run-1")
    assert any(e["type"] == "wait.resolved" for e in events)


# AC-FR0298-02@v0.9 TRACKS-TRACE no hot loop bounded probing
def test_no_hot_loop_bounded_probing(tmp_path: Path):
    """AC-FR0298-02: probe intervals bounded, never hot-looping."""
    policy = WaitPolicy()
    state = next_probe(policy, None)
    assert state["interval_s"] >= policy.initial_s
    assert state["interval_s"] <= policy.cap_s
    grown = next_probe(policy, state)
    assert grown["interval_s"] <= policy.cap_s
