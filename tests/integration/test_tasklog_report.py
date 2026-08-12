"""Runtime-owned task-log and report projection outlets."""

import pytest

from tests.integration.v05_contract_helpers import run_m_impl_journey


@pytest.mark.integration
# AC-FR0030-03@v0.5 TRACKS-TRACE task progress persists and reports
def test_tasklog_and_report_are_rebuilt_from_events(trac, event_log, host_repo):
    _, _, events = run_m_impl_journey(trac, event_log)
    task_logs = sorted((host_repo / ".tracks" / "projects").rglob("task-log.md"))
    assert task_logs
    text = task_logs[-1].read_text(encoding="utf-8")
    assert "Phase 1 Red" in text and "Runtime Quality Gate" in text
    report = trac("report")
    assert report.returncode == 0
    assert "Tracks-R" in report.stdout
    assert any(event["type"] == "task.completed" for event in events)
