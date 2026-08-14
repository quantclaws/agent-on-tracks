"""E2E M-TEST happy path (FR-0010~0070): M-DESIGN exit -> M-TEST -> boundary.

Fake-channel end-to-end journey: the full M-TEST cycle from M-DESIGN exit
through Shield write, collection, Prism review, Red check, trace closure,
test commit, to run.completed(terminal_state="boundary").
"""

from tests.e2e.helpers import walk_to_m_test_complete
from tests.e2e.test_happy_path import types
from tests.integration.helpers import (
    assert_no_human_events_in_m_test,
    assert_sm01_event_sequence,
)


# AC-FR0010-02@v0.4 TRACKS-TRACE boundary after M-TEST
# AC-FR0070-08@v0.4 TRACKS-TRACE boundary after M-TEST
def test_boundary_after_m_test(trac, event_log):
    """AC-FR0010-02@v0.4, AC-FR0070-08@v0.4: M-DESIGN EXIT -> M-TEST ->
    run.completed(boundary)."""
    run_id = walk_to_m_test_complete(trac)
    evs = event_log(run_id)
    ts = types(evs)
    # M-TEST was entered after M-DESIGN exited
    entered = [e for e in evs if e["type"] == "stage.entered"]
    assert entered[-1]["payload"]["stage"] == "M-TEST"
    # Run completed at the boundary (M-IMPL not registered)
    assert ts[-2:] == ["stage.exited", "run.completed"]
    assert evs[-1]["payload"]["terminal_state"] == "boundary"
    # The final stage.exited is M-TEST
    exited = [e for e in evs if e["type"] == "stage.exited"]
    assert exited[-1]["payload"]["stage"] == "M-TEST"


# AC-FR0070-06@v0.4 TRACKS-TRACE no human gate
def test_no_human_gate(trac, event_log):
    """AC-FR0070-06@v0.4: M-TEST has no Human gate (BS-05)."""
    run_id = walk_to_m_test_complete(trac)
    evs = event_log(run_id)
    assert_no_human_events_in_m_test(evs)


# AC-FR0010-05@v0.4 TRACKS-TRACE M-TEST substate sequence
def test_m_test_substate_sequence(trac, event_log):
    """AC-FR0010-05@v0.4: M-TEST substate sequence follows SM-01."""
    run_id = walk_to_m_test_complete(trac)
    evs = event_log(run_id)
    # The M-TEST events include all SM-01 milestones
    m_test_start = next(
        i
        for i, e in enumerate(evs)
        if e["type"] == "stage.entered" and e["payload"]["stage"] == "M-TEST"
    )
    m_test_evs = evs[m_test_start:]
    ts = types(m_test_evs)
    # SM-01 sequence: all milestone events present
    assert_sm01_event_sequence(ts)
