"""Quality-gate layering through command and event outlets."""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0110-01@v0.5 TRACKS-TRACE green gate runs granular checks without e2e
# AC-FR0110-03@v0.5 TRACKS-TRACE integration feedback is desensitized
# AC-FR0110-04@v0.5 TRACKS-TRACE unknown attribution enters diagnose
# AC-FR0130-01@v0.5 TRACKS-TRACE no-change refactor is gateable
# AC-FR0130-02@v0.5 TRACKS-TRACE production and test checks are layered
# AC-FR0130-03@v0.5 TRACKS-TRACE public interface changes roll back
def test_quality_gate_layers_reach_public_commands_and_events(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    issued = [
        event
        for event in events_of(events, "command.issued")
        if event["payload"].get("command", {}).get("kind")
        in {"run_task_gates", "run_refactor_gate", "check_island_2"}
    ]
    kinds = [event["payload"]["command"]["kind"] for event in issued]
    assert "run_task_gates" in kinds
    assert "run_refactor_gate" in kinds
    green = events_of(events, "green.committed")
    refactor = events_of(events, "refactor.committed") + events_of(events, "refactor.no_change")
    assert green and refactor
    assert kinds.index("run_task_gates") < kinds.index("check_island_2")
