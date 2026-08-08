"""Task-plan contracts through public file, event, and CLI outlets."""

from pathlib import Path

import pytest

from tests.integration.helpers import walk_to_m_test
from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey

ASSETS = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"


@pytest.mark.integration
# AC-FR0030-01@v0.5 TRACKS-TRACE task graph is committed from content truth
# AC-FR0180-01@v0.5 TRACKS-TRACE runtime consumes Task List and dependencies
def test_taskgraph_happy_path_reaches_public_file_and_event(trac, event_log, host_repo):
    _, _, events = run_m_impl_journey(trac, event_log)
    committed = events_of(events, "taskgraph.committed")
    assert committed
    payload = committed[-1]["payload"]
    assert payload["validate_status"] == "pass"
    assert payload["task_count"] == len(payload["task_ids"]) > 0
    taskplans = sorted((host_repo / ".tracks" / "projects").rglob("task-plan.md"))
    assert taskplans
    text = taskplans[-1].read_text(encoding="utf-8")
    assert "Task List" in text and "Dependency Graph" in text


@pytest.mark.integration
# AC-FR0030-02@v0.5 TRACKS-TRACE cycle scope and AC gaps fail closed
# AC-FR0180-02@v0.5 TRACKS-TRACE task-plan validation errors are observable
def test_taskgraph_key_error_paths_fail_at_validate_cli(trac, host_repo):
    walk_to_m_test(trac)
    assert trac("run").returncode == 0
    taskplan = host_repo / ".tracks" / "projects" / "v0.5" / "task-plan.md"
    taskplan.parent.mkdir(parents=True, exist_ok=True)
    taskplan.write_text(
        (ASSETS / "cycle_overlap.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = trac("validate", "--file", str(taskplan))
    output = f"{result.stdout}\n{result.stderr}".lower()
    assert result.returncode != 0
    assert "cycle" in output
    assert "overlap" in output
    assert "not covered" in output or "coverage" in output
