"""Programmatic M-IMPL exit gate."""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0160-01@v0.5 TRACKS-TRACE reach and full integration e2e gate exit
def test_island_gate_two_requires_reach_and_full_suites(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    exited = [
        event
        for event in events_of(events, "stage.exited")
        if event["payload"].get("stage") == "M-IMPL"
    ]
    assert exited
    reach = trac("check", "reach", "--json")
    assert reach.returncode == 0, reach.stderr
    assert '"status": "pass"' in reach.stdout
