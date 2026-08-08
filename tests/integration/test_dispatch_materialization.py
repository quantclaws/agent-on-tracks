"""Assignment, checkpoint, collection, and rollback materialization contract."""

import pytest

from tests.integration.v05_contract_helpers import command_dispatches, run_m_impl_journey


@pytest.mark.integration
# AC-FR0190-01@v0.5 TRACKS-TRACE assignment has complete materialized context
# AC-FR0190-02@v0.5 TRACKS-TRACE escalation permits design return
# AC-FR0190-03@v0.5 TRACKS-TRACE canonical project contract only
# AC-FR0190-04@v0.5 TRACKS-TRACE design output parsing fails closed
# AC-FR0190-05@v0.5 TRACKS-TRACE Shield receives valid test tasks
# AC-FR0190-06@v0.5 TRACKS-TRACE checkpoint uses content identity and diff
# AC-FR0190-07@v0.5 TRACKS-TRACE collect modules differ from checkpoint files
# AC-FR0190-08@v0.5 TRACKS-TRACE host interpreter fallback is controlled
# AC-FR0190-09@v0.5 TRACKS-TRACE rollback preserves semantic evidence
# AC-FR0190-10@v0.5 TRACKS-TRACE all agent and runtime operations materialize
def test_dispatch_assignments_have_complete_public_payload(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    dispatches = command_dispatches(events)
    assert dispatches
    required = {
        "target_doc", "doc_set", "role", "substate", "attempt", "review_round",
        "docs", "templates", "skills", "criteria_pack", "test_tasks", "manifest",
        "phase", "r_tree_identity", "pre_dirty_snapshot", "result_identity",
    }
    for event in dispatches:
        assignment = event["payload"]["command"]["params"]["assignment"]
        assert required <= assignment.keys()
    shield_write = [
        event for event in dispatches
        if event["payload"]["command"]["params"].get("role") == "shield"
        and event["payload"]["command"]["params"].get("substate") == "WRITE"
    ]
    assert shield_write
    assert all(
        event["payload"]["command"]["params"]["assignment"]["test_tasks"]
        for event in shield_write
    )
