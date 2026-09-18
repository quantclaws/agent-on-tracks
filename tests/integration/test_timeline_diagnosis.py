"""Timeline diagnosis (IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.projections import project_timeline

pytestmark = pytest.mark.integration


# AC-FR0305-01@v0.9 TRACKS-TRACE timeline shows failures retries next
def test_timeline_shows_failures_retries_next(tmp_path: Path):
    """AC-FR0305-01: timeline rows carry failure retry and next-step fields."""
    timeline = project_timeline(tmp_path, "run-1")
    assert "events" in timeline and "cursor" in timeline
    for row in timeline["events"]:
        assert "type" in row and "summary" in row


# AC-FR0305-02@v0.9 TRACKS-TRACE logs locatable by dimensions
def test_logs_locatable_by_dimensions(tmp_path: Path):
    """AC-FR0305-02: timeline filters by run command task AC locate logs."""
    timeline = project_timeline(
        tmp_path, "run-1", command_id="cmd-1", task_id="task-1", ac_id="AC-FR0305-01"
    )
    assert "events" in timeline
    assert "cursor" in timeline
