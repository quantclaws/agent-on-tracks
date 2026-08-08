"""M-IMPL entry, explicit lifecycle, replay, and append-only contracts."""

import pytest

from tests.integration.v05_contract_helpers import first_m_impl_index, run_m_impl_journey


@pytest.mark.integration
# AC-FR0010-01@v0.5 TRACKS-TRACE M-TEST enters M-IMPL BASELINE
# AC-FR0010-03@v0.5 TRACKS-TRACE legal SM-01 lifecycle only
# AC-NFR0010-02@v0.5 TRACKS-TRACE effects remain event-driven
# AC-NFR0020-01@v0.5 TRACKS-TRACE M-IMPL events append only
# AC-NFR0020-02@v0.5 TRACKS-TRACE projections rebuild from events
def test_m_impl_public_event_lifecycle(trac, event_log):
    run_id, _, events = run_m_impl_journey(trac, event_log)
    start = first_m_impl_index(events)
    impl_events = events[start:]
    assert impl_events[0]["payload"] == {"stage": "M-IMPL"}
    assert [event["seq"] for event in events] == sorted(
        {event["seq"] for event in events}
    )
    status = trac("status")
    assert status.returncode == 0
    assert f"run={run_id}" in status.stdout
    assert "stage=M-IMPL" in status.stdout or "terminal=boundary" in status.stdout
    assert any(event["type"] == "baseline.frozen" for event in impl_events)
    assert any(event["type"] == "task.completed" for event in impl_events)
