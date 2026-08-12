"""Regression-safe full fake journey through M-IMPL."""

import pytest

from tests.e2e.helpers import walk_to_m_test_complete


@pytest.mark.e2e
# AC-FR0010-04@v0.5 TRACKS-TRACE pre-M-TEST event prefix remains stable
def test_full_journey_to_boundary_includes_m_impl(trac, event_log):
    run_id = walk_to_m_test_complete(trac, version="v0.5")
    events = event_log(run_id)
    stages = [
        event["payload"]["stage"]
        for event in events
        if event["type"] == "stage.entered"
    ]
    assert stages[:8] == [
        "M-START", "M-STORY", "M-SPEC", "M-ACC", "M-REQ-APPROVAL",
        "M-DESIGN", "M-TEST", "M-IMPL",
    ]
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["payload"]["terminal_state"] == "boundary"
