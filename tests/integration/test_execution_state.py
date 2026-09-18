"""Execution state (IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.projections import project_executions, project_run_detail

pytestmark = pytest.mark.integration


# AC-FR0303-01@v0.9 TRACKS-TRACE role task states with log refs
def test_role_task_states_with_log_refs(tmp_path: Path):
    """AC-FR0303-01: executions carry role task state plus log_ref."""
    rows = project_executions(tmp_path, "run-1")
    assert isinstance(rows, list)
    for row in rows:
        assert row["state"] in ("executing", "waiting", "failed")
        assert "log_ref" in row


# AC-FR0303-02@v0.9 TRACKS-TRACE no agent session content anywhere
def test_no_agent_session_content_anywhere(tmp_path: Path):
    """AC-FR0303-02: detail and executions never embed session content."""
    detail = project_run_detail(tmp_path, "run-1")
    blob = repr(detail) + repr(project_executions(tmp_path, "run-1"))
    assert "session_content" not in blob
    assert "transcript" not in blob
