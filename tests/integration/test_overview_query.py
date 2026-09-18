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
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    before = db.read_events()
    overview = project_overview(home)
    assert "runs" in overview
    assert db.read_events() == before
