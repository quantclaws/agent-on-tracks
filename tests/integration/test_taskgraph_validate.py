"""tasks.json task graph contracts through public file, event, and CLI outlets."""

import json

import pytest

from tests.integration.helpers import walk_to_m_test
from tests.integration.v05_contract_helpers import (
    events_of,
    run_m_impl_journey,
    validate_tasksjson,
)


@pytest.mark.integration
# AC-FR0030-01@v0.5 TRACKS-TRACE task graph committed from tasks.json machine truth
# AC-FR0180-01@v0.5 TRACKS-TRACE runtime parses tasks.json to drive DAG scheduling
def test_taskgraph_happy_path_reaches_public_file_and_event(trac, event_log, host_repo):
    _, _, events = run_m_impl_journey(trac, event_log)
    committed = events_of(events, "taskgraph.committed")
    assert committed
    payload = committed[-1]["payload"]
    assert payload["validate_status"] == "pass"
    assert payload["task_count"] == len(payload["task_ids"]) > 0
    tasks_json = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.json"
    assert tasks_json.exists()
    data = json.loads(tasks_json.read_text(encoding="utf-8"))
    assert "tasks" in data
    task = data["tasks"][0]
    assert "task_id" in task
    assert "issue_number" in task
    assert "if_ids" in task
    assert "scope_boundary" in task
    assert "budget" in task


@pytest.mark.integration
# AC-FR0030-02@v0.5 TRACKS-TRACE DAG cycle scope and AC gaps fail closed at validate CLI
# AC-FR0180-02@v0.5 TRACKS-TRACE tasks.json validation errors are observable via CLI
def test_taskgraph_key_error_paths_fail_at_validate_cli(trac, host_repo):
    walk_to_m_test(trac)
    assert trac("run").returncode == 0
    result, output = validate_tasksjson(trac, host_repo, "dag_cycle.json")
    assert result.returncode != 0
    assert "cycle" in output


@pytest.mark.integration
# AC-FR0030-03@v0.5 TRACKS-TRACE tasks.md projected from tasks.json by Runtime
def test_tasksmd_projected(trac, event_log, host_repo):
    _, _, events = run_m_impl_journey(trac, event_log)
    tasks_md = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.md"
    assert tasks_md.exists()
    tasks_json = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.json"
    assert tasks_json.exists()
    md_text = tasks_md.read_text(encoding="utf-8")
    json_data = json.loads(tasks_json.read_text(encoding="utf-8"))
    for task in json_data["tasks"]:
        assert task["task_id"] in md_text


@pytest.mark.integration
# AC-FR0030-03@v0.5 TRACKS-TRACE trac report rebuilds per-task progress
def test_report_progress(trac, event_log, host_repo):
    run_id, _, events = run_m_impl_journey(trac, event_log)
    committed = events_of(events, "taskgraph.committed")
    assert committed
    report = trac("report", "--run-id", run_id, "--output", str(host_repo / "report_out"))
    assert report.returncode == 0
    assert "task" in report.stdout.lower()
