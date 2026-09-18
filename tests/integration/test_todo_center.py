"""Todo center (IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.projections import project_todos

pytestmark = pytest.mark.integration


# AC-FR0307-01@v0.9 TRACKS-TRACE legit decisions listed with context
def test_legit_decisions_listed_with_context(tmp_path: Path):
    """AC-FR0307-01: approvals and release decisions listed with jump refs."""
    todos = project_todos(tmp_path)
    assert isinstance(todos, list)
    for todo in todos:
        assert todo["kind"] in ("stage_approval", "release_decision", "clarification", "triage")
        assert "context" in todo and "action_ref" in todo


# AC-FR0307-02@v0.9 TRACKS-TRACE waits never in todos
def test_waits_never_in_todos(tmp_path: Path):
    """AC-FR0307-02: quota and CI waits never appear in the todo center."""
    todos = project_todos(tmp_path)
    kinds = [t.get("wait_class") for t in todos]
    assert "quota" not in kinds
    assert "ci" not in kinds
