"""Dispatch activity and operator retry public CLI contracts."""

import pytest

from tests.integration.helpers import walk_to_m_test


@pytest.mark.integration
# AC-NFR0030-01@v0.5 TRACKS-TRACE dispatch activity is concise and flushed
# AC-NFR0030-02@v0.5 TRACKS-TRACE attempts and failure class are visible
# AC-NFR0030-03@v0.5 TRACKS-TRACE retry appends event and resets budget
def test_retry_activity_and_failure_evidence(trac, event_log):
    run_id = walk_to_m_test(trac)
    failed = trac("run", simulate="shield:WRITE=fail|fail|fail")
    assert "attempts=3" in failed.stdout
    status = trac("status")
    assert "awaiting=escalation" in status.stdout
    assert "[agent_failed]" in status.stdout
    retried = trac("retry")
    assert retried.returncode == 0
    expected = "human.retry event appended; escalation gate cleared; attempt budget reset"
    assert expected in retried.stdout
    retry_events = [event for event in event_log(run_id) if event["type"] == "human.retry"]
    assert len(retry_events) == 1
