"""Overview query (IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.projections import project_overview

pytestmark = pytest.mark.integration


# AC-FR0301-01@v0.9 TRACKS-TRACE overview consistent with state
def test_overview_consistent_with_state(tmp_path: Path):
    """AC-FR0301-01: overview versions and todo counts match run state."""
    overview = project_overview(tmp_path)
    assert "projects" in overview and "runs" in overview
    assert "human_todo_count" in overview
    assert overview["human_todo_count"] == sum(
        len(r.get("human_todos", [])) for r in overview["runs"]
    )


# AC-FR0301-02@v0.9 TRACKS-TRACE query during long task nonblocking
def test_query_during_long_task_nonblocking(tmp_path: Path):
    """AC-FR0301-02: queries return instantly and append no events."""
    import time

    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    # a claimed command is the long task in flight while the query runs
    db.register_command(
        {
            "command_id": "cmd-long-1",
            "kind": "drive_run",
            "params_json": '{"run_id": "run-1"}',
            "params_digest": "d",
            "idempotency_key": "long-1",
            "actor": "system",
            "actor_class": "system",
            "surface": "internal",
            "project_id": "p1",
            "run_id": "run-1",
        }
    )
    assert db.claim_command("cmd-long-1", "worker-1", 1)

    before = db.read_events()
    start = time.monotonic()
    overview = project_overview(home)
    elapsed = time.monotonic() - start

    assert "runs" in overview
    assert db.read_events() == before, "the read path must not write"
    assert elapsed < 1.0, "an overview query must return under the NFR-0150 budget"
    # the query never drives the in-flight task: the claim, its generation
    # and its (still empty) result are untouched
    inflight = db.get_command("cmd-long-1")
    assert inflight["status"] == "claimed"
    assert inflight["claim_generation"] == 1
    assert inflight["result_json"] is None
