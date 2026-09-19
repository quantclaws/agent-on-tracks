"""Execution state (IF-QUERY-001).

Role/task snapshots come from tracks.db run-plane events; the view must
carry a jump-to-log reference per task and must never embed Agent
session content (story Out-of-Scope, FR-0303).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_server_projections_unit import _seed
from tracks.server.projections import project_executions, project_run_detail

pytestmark = pytest.mark.integration


# AC-FR0303-01@v0.9 TRACKS-TRACE role task states with log refs
def test_role_task_states_with_log_refs(tmp_path: Path):
    """AC-FR0303-01: each live task reports role/state plus its log ref."""
    run = "run-exec-int"
    blob_ref = ".tracks/runtime/blobs/" + "c" * 64
    history = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        {
            "type": "taskgraph.committed",
            "payload": {
                "task_count": 3,
                "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}, {"task_id": "T-3"}],
            },
        },
        {
            "type": "task.started",
            "payload": {"task_id": "T-1", "task": {"task_id": "T-1"}},
            "task_id": "T-1",
        },
        {
            "type": "outcome.received",
            "payload": {"role": "devon", "status": "failed", "self_report": "red"},
            "task_id": "T-1",
        },
        {
            "type": "verdict.failed",
            "payload": {
                "task_id": "T-1",
                "check": "red_classifier",
                "reason": "boom",
                "log_ref": blob_ref,
            },
            "task_id": "T-1",
        },
        {"type": "task.started", "payload": {"task_id": "T-2"}, "task_id": "T-2"},
    ]
    home, _repo = _seed(tmp_path, run_id=run, events=history, ac_ids=["AC-FR0303-01"])

    rows = project_executions(home, run)
    indexed = {entry["task_id"]: entry for entry in rows}

    assert indexed["T-1"]["role"] == "devon"
    assert indexed["T-1"]["state"] == "failed"
    assert indexed["T-1"]["log_ref"] == blob_ref
    assert indexed["T-2"]["state"] == "executing"
    assert "T-3" not in indexed
    for entry in rows:
        assert set(entry) == {"role", "task_id", "state", "log_ref"}
        assert entry["state"] in ("executing", "waiting", "failed")
        assert isinstance(entry["log_ref"], str)


# AC-FR0303-02@v0.9 TRACKS-TRACE no agent session content anywhere
def test_no_agent_session_content_anywhere(tmp_path: Path):
    """AC-FR0303-02: executions/detail expose refs only, never session text."""
    run = "run-session-int"
    transcript = "VERBATIM-SESSION-TRANSCRIPT-INT"
    history = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        {
            "type": "task.started",
            "payload": {"task_id": "T-1"},
            "task_id": "T-1",
        },
        {
            "type": "outcome.received",
            "payload": {
                "role": "devon",
                "status": "done",
                "self_report": "ok",
                "agent_io": {
                    "output_ref": ".tracks/runtime/blobs/" + "d" * 64,
                    "transcript": transcript,
                    "messages": [{"role": "assistant", "content": "hidden"}],
                },
            },
            "task_id": "T-1",
        },
    ]
    home, _repo = _seed(tmp_path, run_id=run, events=history, ac_ids=["AC-FR0303-02"])

    rows = project_executions(home, run)
    snapshot = project_run_detail(home, run)
    rendered = json.dumps(rows) + json.dumps(snapshot)

    assert transcript not in rendered
    assert "agent_io" not in rendered
    assert "transcript" not in rendered
    assert "messages" not in rendered
    assert "session_content" not in rendered
    for entry in rows:
        assert set(entry) == {"role", "task_id", "state", "log_ref"}
