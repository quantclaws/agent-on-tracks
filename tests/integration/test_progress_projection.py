"""Progress projection (IF-QUERY-001).

Integration over the documented storage contract: service.db rows per
interfaces §1c plus the project's tracks.db written through the real
Store, read back through the public projection surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_server_projections_unit import _seed
from tracks.server.projections import project_run_detail

pytestmark = pytest.mark.integration


# AC-FR0302-01@v0.9 TRACKS-TRACE dual counts presented
def test_dual_counts_presented(tmp_path: Path):
    """AC-FR0302-01: stage/task/quality shown; test vs AC-closure counts split."""
    run = "run-dual-int"
    staged = [{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}]
    graph = {
        "type": "taskgraph.committed",
        "payload": {
            "task_count": 3,
            "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}, {"task_id": "T-3"}],
        },
    }
    started = {
        "type": "task.started",
        "payload": {"task_id": "T-1", "task": {"task_id": "T-1"}},
        "task_id": "T-1",
    }
    done = {"type": "task.completed", "payload": {"task_id": "T-1"}}
    home, _repo = _seed(
        tmp_path,
        run_id=run,
        events=[*staged, graph, started, done],
        ac_ids=["AC-FR0302-01", "AC-FR0302-02"],
    )

    snapshot = project_run_detail(home, run)

    assert snapshot["run_id"] == run
    assert snapshot["stage"] == "M-IMPL"
    assert snapshot["control_state"] == "推进中"
    assert snapshot["wait"] is None
    assert snapshot["pause"] == {"requested": False, "effective": False}
    counts = snapshot["progress"]
    assert counts["tasks_total"] == 3
    assert counts["tasks_done"] == 1
    assert counts["ac_total"] == 2
    assert 0 <= counts["ac_closed"] <= counts["ac_total"]
    assert isinstance(counts["tests_passed"], (int, type(None)))
    assert "tests_passed" in counts and "ac_closed" in counts
    assert "percent" not in json.dumps(snapshot).lower()
    assert snapshot["lease"] == {"generation": 1, "worker_id": "worker-1"}


# AC-FR0302-02@v0.9 TRACKS-TRACE progress matches real counts
def test_progress_matches_real_counts(tmp_path: Path):
    """AC-FR0302-02: task totals track the committed graph; no pct invented."""
    run = "run-counts-int"
    opening = [{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}]
    committed = {
        "type": "taskgraph.committed",
        "payload": {
            "task_count": 5,
            "tasks": [{"task_id": f"T-{n}"} for n in range(1, 6)],
        },
    }
    first = {"type": "task.started", "payload": {"task_id": "T-1"}, "task_id": "T-1"}
    first_done = {"type": "task.completed", "payload": {"task_id": "T-1"}}
    second = {"type": "task.started", "payload": {"task_id": "T-2"}, "task_id": "T-2"}
    second_done = {"type": "task.completed", "payload": {"task_id": "T-2"}}
    third = {"type": "task.started", "payload": {"task_id": "T-3"}, "task_id": "T-3"}
    home, _repo = _seed(
        tmp_path,
        run_id=run,
        events=[*opening, committed, first, first_done, second, second_done, third],
        ac_ids=["AC-FR0302-01", "AC-FR0302-02", "AC-FR0304-01"],
    )

    snapshot = project_run_detail(home, run)
    counts = snapshot["progress"]

    assert counts["tasks_total"] == 5
    assert counts["tasks_done"] == 2
    assert counts["tasks_done"] <= counts["tasks_total"]
    assert counts["ac_total"] == 3
    assert snapshot["substate"] == "RED"
    assert "percent" not in json.dumps(counts).lower()
