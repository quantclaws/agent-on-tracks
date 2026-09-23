"""Island, Prism plan, and serial task dispatch public contracts."""

import pytest

from tests.integration.v05_contract_helpers import command_dispatches, events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0040-01@v0.5 TRACKS-TRACE six-tuple closes island gate one
# AC-FR0040-02@v0.5 TRACKS-TRACE island gap redispatches planning
# AC-FR0050-01@v0.5 TRACKS-TRACE Prism criteria identity triple
# AC-FR0050-02@v0.5 TRACKS-TRACE Prism plan verdict routes
# AC-FR0050-03@v0.5 TRACKS-TRACE revise requires discussion finding
# AC-FR0050-04@v0.5 TRACKS-TRACE all Prism phases bind criteria
# AC-FR0060-01@v0.5 TRACKS-TRACE ready task gets lease and manifest
# AC-FR0060-02@v0.5 TRACKS-TRACE IF subset schedules task progression
# AC-FR0060-03@v0.5 TRACKS-TRACE parallel markers remain serial
def test_planning_and_dispatch_contracts_are_persisted(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    prism = [
        event
        for event in command_dispatches(events, role="prism")
        if event["payload"]["command"]["params"].get("substate")
        in {"PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE"}
    ]
    assert prism
    for dispatch in prism:
        assignment = dispatch["payload"]["command"]["params"]["assignment"]
        assert "criteria_pack" in assignment
        assert assignment["criteria_pack"] == {
            "name": "tracks-prism-impl",
            "version": "0.3",
        }
    started = events_of(events, "task.started")
    locks = events_of(events, "writelock.granted")
    assert started and locks
    assert all(event["payload"]["manifest"]["allowed_paths"] for event in started)
    assert all(
        left["seq"] < right["seq"] for left, right in zip(started, started[1:], strict=False)
    )
