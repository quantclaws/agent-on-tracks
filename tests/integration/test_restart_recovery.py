"""Restart recovery (IF-RECOVER-001, IF-PUBLISH-002)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.recover import recover_on_startup

pytestmark = pytest.mark.integration


# AC-FR0300-01@v0.9 TRACKS-TRACE worker kill reclaim no duplicate effects
def test_worker_kill_reclaim_no_duplicate_effects(tmp_path: Path):
    """AC-FR0300-01: killed worker command requeued once, effects not duplicated."""
    import json

    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)

    def register(command_id: str, idempotency_key: str, run_id: str) -> None:
        db.register_command(
            {
                "command_id": command_id,
                "kind": "pause_run",
                "params_json": json.dumps({"run_id": run_id}, sort_keys=True),
                "params_digest": "d",
                "idempotency_key": idempotency_key,
                "actor": "human",
                "actor_class": "human",
                "surface": "http",
                "run_id": run_id,
            }
        )

    # the command the killed worker was executing when it died
    register("cmd-kill-1", "kill-1", "run-1")
    assert db.claim_command("cmd-kill-1", "worker-old", 1)
    # a command that already finished: its external effect must not be repeated
    register("cmd-done-1", "kill-done-1", "run-2")
    assert db.claim_command("cmd-done-1", "worker-old", 1)
    assert db.complete_command("cmd-done-1", 1, {"effect": "published"}, None)

    summary = recover_on_startup(db)

    # the interrupted command is reclaimed exactly once, under the same id
    assert summary["requeued"] == ["cmd-kill-1"]
    assert sum(1 for e in db.read_events() if e["type"] == "command.requeued") == 1
    reclaimed = db.get_command("cmd-kill-1")
    assert reclaimed["status"] == "accepted"
    assert json.loads(reclaimed["params_json"]) == {"run_id": "run-1"}
    assert db.claim_command("cmd-kill-1", "worker-new", 2), (
        "the reclaimed command must be claimable by the new worker"
    )
    # the completed command keeps its single effect: never requeued, never re-run
    assert "cmd-done-1" not in summary["requeued"]
    done = db.get_command("cmd-done-1")
    assert done["status"] == "completed"
    assert json.loads(done["result_json"]) == {"effect": "published"}


# AC-FR0300-02@v0.9 TRACKS-TRACE server restart resumes without loss
def test_server_restart_resumes_without_loss(tmp_path: Path):
    """AC-FR0300-02: waits preserved, nothing lost or double-published."""
    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.waiting import enter_wait

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    enter_wait(
        db,
        "run-2",
        {
            "wait_class": "ci",
            "reason": "ci green pending",
            "retry_at": "2030-01-01T00:00:00+00:00",
            "known_reset": True,
            "backoff": {"interval_s": 60, "cap_s": 900, "next_probe_at": "2030-01-01T00:00:00+00:00"},
        },
    )
    before = db.get_wait("run-2")

    summary = recover_on_startup(db)

    assert "run-2" in summary["waits_kept"]
    assert summary["requeued"] == []
    # the persisted wait survives the restart with retry_at and the full state
    after = db.get_wait("run-2")
    assert after is not None, "a restart must not drop a persisted wait"
    assert after["retry_at"] == "2030-01-01T00:00:00+00:00"
    assert after["wait_class"] == "ci"
    assert after["reason"] == "ci green pending"
    assert after["known_reset"] == 1
    assert after["backoff_json"] == before["backoff_json"]
    # recovery neither re-enters the wait nor repeats a publish side effect:
    # the durable wait state is the only event, so nothing is lost or repeated
    types = [event["type"] for event in db.read_events()]
    assert types == ["wait.entered"], (
        f"recovery must not re-emit wait or publish side effects: {types}"
    )
