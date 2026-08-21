"""Runtime-owned tasks.json/tasks.md and report outlets."""

import pytest

from tests.integration.v05_contract_helpers import run_m_impl_journey


@pytest.mark.integration
# AC-FR0030-03@v0.5 TRACKS-TRACE task graph persists and reports
def test_tasks_projection_and_report_are_available(trac, event_log, host_repo):
    _, _, events = run_m_impl_journey(trac, event_log)
    task_docs = sorted((host_repo / ".tracks" / "projects").rglob("tasks.md"))
    assert task_docs
    assert "# Task Graph" in task_docs[-1].read_text(encoding="utf-8")
    report = trac("report")
    assert report.returncode == 0
    assert "Tracks-R" in report.stdout
    assert any(event["type"] == "task.completed" for event in events)
