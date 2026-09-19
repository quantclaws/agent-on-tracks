"""Todo center (IF-QUERY-001).

Only legitimate human decisions surface here; external waits and
environment repair hints stay in the run detail wait/diagnostics
presentation (FR-0307).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.test_server_projections_unit import _seed
from tracks.server.projections import project_todos

pytestmark = pytest.mark.integration


# AC-FR0307-01@v0.9 TRACKS-TRACE legit decisions listed with context
def test_legit_decisions_listed_with_context(tmp_path: Path):
    """AC-FR0307-01: pending approvals carry reason, context, and jump ref."""
    run = "run-approval-int"
    history = [
        {"type": "stage.entered", "payload": {"stage": "M-REQ-APPROVAL"}},
        {"type": "preview.generated", "payload": {"preview_digest": "e" * 64}},
    ]
    home, _repo = _seed(tmp_path, run_id=run, events=history, ac_ids=["AC-FR0307-01"])

    todos = project_todos(home)
    mine = [entry for entry in todos if entry["run_id"] == run]

    assert mine, "a pending stage approval must surface as a todo"
    first = mine[0]
    assert first["kind"] == "stage_approval"
    assert first["context"]
    assert first["action_ref"]
    assert isinstance(first["object"], str) and first["object"]
    for entry in mine:
        assert entry["kind"] in (
            "stage_approval",
            "release_decision",
            "clarification",
            "triage",
        )


# AC-FR0307-02@v0.9 TRACKS-TRACE waits never in todos
def test_waits_never_in_todos(tmp_path: Path):
    """AC-FR0307-02: quota/CI waits stay out of the human todo center."""
    run = "run-quota-int"
    history = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        {
            "type": "taskgraph.committed",
            "payload": {"task_count": 1, "tasks": [{"task_id": "T-1"}]},
        },
    ]
    home, _repo = _seed(
        tmp_path,
        run_id=run,
        events=history,
        ac_ids=["AC-FR0307-02"],
        wait={"wait_class": "quota", "reason": "rate limited", "known_reset": False},
    )

    todos = project_todos(home)

    assert [entry for entry in todos if entry["run_id"] == run] == []
    assert all(entry["kind"] != "quota" for entry in todos)
    assert all("wait_class" not in entry for entry in todos)
