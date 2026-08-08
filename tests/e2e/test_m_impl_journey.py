"""User-visible M-IMPL happy journey to the M-VERIFY boundary."""

import pytest

from tests.e2e.helpers import walk_to_m_test_complete


@pytest.mark.e2e
# AC-FR0010-02@v0.5 TRACKS-TRACE status and next-stage boundary
# AC-FR0160-03@v0.5 TRACKS-TRACE successful M-IMPL exits at boundary
# AC-FR0160-04@v0.5 TRACKS-TRACE M-IMPL exit has no Human gate
def test_boundary_after_m_impl(trac, event_log):
    run_id = walk_to_m_test_complete(trac)
    events = event_log(run_id)
    entered = [
        event for event in events
        if event["type"] == "stage.entered" and event["payload"].get("stage") == "M-IMPL"
    ]
    exited = [
        event for event in events
        if event["type"] == "stage.exited" and event["payload"].get("stage") == "M-IMPL"
    ]
    completed = [event for event in events if event["type"] == "run.completed"]
    assert entered and exited and completed
    assert entered[0]["seq"] < exited[0]["seq"] < completed[-1]["seq"]
    assert completed[-1]["payload"]["terminal_state"] == "boundary"
    assert not any(
        event["type"] == "stage.entered"
        and event["payload"].get("stage") == "M-VERIFY"
        for event in events
    )
    impl_events = [
        event for event in events if entered[0]["seq"] <= event["seq"] <= exited[0]["seq"]
    ]
    assert not any(event["type"] in {"human.review", "human.approval"} for event in impl_events)
    status = trac("status")
    assert "terminal=boundary" in status.stdout
