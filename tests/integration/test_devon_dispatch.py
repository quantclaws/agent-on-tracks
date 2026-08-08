"""Devon materialization and manifest audit at public outlets."""

import pytest

from tests.integration.v05_contract_helpers import command_dispatches, events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0170-01@v0.5 TRACKS-TRACE Devon materializes and cleans up like agents
# AC-FR0170-03@v0.5 TRACKS-TRACE manifest over-reach rolls back only Devon
def test_devon_dispatch_manifest_and_audit_evidence(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    dispatches = command_dispatches(events, role="devon")
    assert dispatches
    assignments = [
        event["payload"]["command"]["params"]["assignment"]
        for event in dispatches
    ]
    assert all(assignment["manifest"]["allowed_paths"] for assignment in assignments)
    outcomes = [
        event for event in events_of(events, "outcome.received")
        if event["payload"].get("role") == "devon"
    ]
    assert outcomes
    assert all("audit_evidence" in event["payload"] for event in outcomes)
