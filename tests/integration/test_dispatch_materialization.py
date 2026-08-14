"""Assignment, checkpoint, collection, and rollback materialization contract."""

import pytest

from tests.integration.v05_contract_helpers import (
    command_dispatches,
    first_m_impl_attempt_segment,
    run_m_impl_journey,
)
from tests.unit.helpers import M_IMPL_REQUIRED_ASSIGNMENT_KEYS
from tracks.effects.backend import valid_test_tasks


@pytest.mark.integration
# AC-FR0190-01@v0.5 TRACKS-TRACE assignment has complete materialized context
# AC-FR0190-02@v0.5 TRACKS-TRACE escalation permits design return
# AC-FR0190-03@v0.5 TRACKS-TRACE canonical project contract only
# AC-FR0190-04@v0.5 TRACKS-TRACE design output parsing fails closed
# AC-FR0190-06@v0.5 TRACKS-TRACE checkpoint uses content identity and diff
# AC-FR0190-07@v0.5 TRACKS-TRACE collect modules differ from checkpoint files
# AC-FR0190-08@v0.5 TRACKS-TRACE host interpreter fallback is controlled
# AC-FR0190-09@v0.5 TRACKS-TRACE rollback preserves semantic evidence
# AC-FR0190-10@v0.5 TRACKS-TRACE all agent and runtime operations materialize
def test_dispatch_assignments_have_complete_public_payload(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    dispatches = command_dispatches(first_m_impl_attempt_segment(events))
    assert dispatches
    for event in dispatches:
        assignment = event["payload"]["command"]["params"]["assignment"]
        assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


@pytest.mark.integration
# AC-FR0190-05@v0.5 TRACKS-TRACE Shield receives valid test tasks
def test_shield_fix_dispatch_assignments_have_valid_test_tasks(trac, event_log):
    _, _, events = run_m_impl_journey(
        trac,
        event_log,
        simulate=("devon:RED=fail|ok;diagnose:classification=test_defect;shield:SHIELD_FIX=ok"),
    )
    shield_writes = command_dispatches(
        first_m_impl_attempt_segment(events), role="shield", substate="WRITE"
    )
    assert shield_writes
    for event in shield_writes:
        test_tasks = event["payload"]["command"]["params"]["assignment"]["test_tasks"]
        assert test_tasks
        assert valid_test_tasks(test_tasks)
