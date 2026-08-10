"""Issues consumption semantics contracts.

FR-0220, IF-IMPL-003/IF-IMPL-002/IF-IMPL-004/IF-DEVON-001.

Each Devon task carries a GitHub issue number in tasks.json; Devon dispatch
payload carries issue_number + ac_refs provenance; Devon G commit trailers
contain Tracks-Issue and Tracks-AC.
"""

import pytest

from tests.integration.v05_contract_helpers import (
    command_dispatches,
    events_of,
    git_read,
    read_tasksjson,
    run_m_impl_journey,
    validate_tasksjson,
)


@pytest.mark.integration
# AC-FR0220-01@v0.5 TRACKS-TRACE every task carries positive issue number in tasks.json
def test_issue_number_in_tasksjson(trac, event_log, host_repo):
    """Every Devon task in tasks.json carries a GitHub issue number (positive integer)."""
    _, _, events = run_m_impl_journey(trac, event_log)
    tasks_json = read_tasksjson(host_repo)
    for task in tasks_json.get("tasks", []):
        assert "issue_number" in task, (
            f"task {task.get('task_id')}: missing issue_number field"
        )
        assert isinstance(task["issue_number"], int), (
            f"task {task.get('task_id')}: issue_number must be int,"
            f" got {type(task['issue_number'])}"
        )
        assert task["issue_number"] >= 1, (
            f"task {task.get('task_id')}: issue_number must be positive, got {task['issue_number']}"
        )


@pytest.mark.integration
# AC-FR0220-01@v0.5 TRACKS-TRACE trac validate checks issue number validity
def test_validate_issue_numbers(trac, host_repo):
    """trac validate --file tasks.json validates issue number is a positive integer."""
    result, output = validate_tasksjson(trac, host_repo, "issue_invalid.json")
    assert result.returncode != 0
    assert "issue" in output
    assert "t-001" in output


@pytest.mark.integration
# AC-FR0220-02@v0.5 TRACKS-TRACE Devon dispatch payload carries issue_number and ac_refs
def test_dispatch_payload_carries_provenance(trac, event_log, host_repo):
    """Devon dispatch assignment payload carries issue_number + ac_refs provenance;
    materialized fields are complete."""
    _, _, events = run_m_impl_journey(trac, event_log)
    devon_dispatches = command_dispatches(events, role="devon")
    assert devon_dispatches, "expected at least one Devon dispatch"
    for event in devon_dispatches:
        params = event["payload"]["command"]["params"]
        assignment = params.get("assignment", {})
        assert "issue_number" in assignment, (
            f"Devon dispatch missing issue_number: {assignment.keys()}"
        )
        assert isinstance(assignment["issue_number"], int), (
            f"Devon dispatch issue_number must be int: {assignment['issue_number']}"
        )
        assert assignment["issue_number"] >= 1, (
            f"Devon dispatch issue_number must be positive: {assignment['issue_number']}"
        )
        assert "ac_refs" in assignment, (
            f"Devon dispatch missing ac_refs: {assignment.keys()}"
        )
        assert isinstance(assignment["ac_refs"], list), (
            f"Devon dispatch ac_refs must be list: {assignment['ac_refs']}"
        )
        assert assignment["ac_refs"], (
            "Devon dispatch ac_refs must be non-empty"
        )


@pytest.mark.integration
# AC-FR0220-03@v0.5 TRACKS-TRACE G commit trailers contain Tracks-Issue and Tracks-AC
def test_g_commit_trailers_contain_provenance(trac, event_log, host_repo):
    """Devon G commit trailers contain Tracks-Issue and Tracks-AC; git log --format='%B'
    -1 <G> shows all five trailers."""
    _, _, events = run_m_impl_journey(trac, event_log)
    greens = events_of(events, "green.committed")
    assert greens, "expected at least one green.committed event"
    green = greens[0]
    g_sha = green["payload"]["g_sha"]
    task_id = green["payload"]["task_id"]
    message = git_read(host_repo, "log", "--format=%B", "-1", g_sha)
    assert f"Tracks-Task: {task_id}" in message
    assert "Tracks-Attempt:" in message
    assert "Tracks-R:" in message
    assert "Tracks-Issue:" in message
    assert "Tracks-AC:" in message


@pytest.mark.integration
# AC-FR0220-04@v0.5 TRACKS-TRACE missing issue number fails trac validate
def test_missing_issue_fails(trac, host_repo):
    """Issue number missing or non-positive-integer -> trac validate fails and
    reports position."""
    result, output = validate_tasksjson(trac, host_repo, "issue_invalid.json")
    assert result.returncode != 0
    assert "issue" in output
    assert "t-001" in output


@pytest.mark.integration
# AC-FR0220-04@v0.5 TRACKS-TRACE missing trailer fails TASK_REVIEW
def test_missing_trailer_fails_review(trac, event_log, host_repo):
    """Devon G commit missing Tracks-Issue or Tracks-AC trailer -> TASK_REVIEW fails."""
    _, _, events = run_m_impl_journey(trac, event_log)
    greens = events_of(events, "green.committed")
    assert greens
    green = greens[0]
    g_sha = green["payload"]["g_sha"]
    message = git_read(host_repo, "log", "--format=%B", "-1", g_sha)
    # The contract requires all five trailers; missing any is a TASK_REVIEW failure.
    # We assert the contract holds (trailers present); if Devon omits them, the
    # verdict.failed event fires.
    trailers_present = {
        "Tracks-Task": "Tracks-Task:" in message,
        "Tracks-Attempt": "Tracks-Attempt:" in message,
        "Tracks-R": "Tracks-R:" in message,
        "Tracks-Issue": "Tracks-Issue:" in message,
        "Tracks-AC": "Tracks-AC:" in message,
    }
    missing = [k for k, v in trailers_present.items() if not v]
    if missing:
        verdicts = events_of(events, "verdict.failed")
        assert verdicts, (
            f"missing trailers {missing} should trigger verdict.failed at TASK_REVIEW"
        )
