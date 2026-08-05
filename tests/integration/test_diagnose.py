"""DIAGNOSE four-way routing (FR-0060, SM-01.11/.12/.13).

Tests the four classification routes: test_defect -> WRITE, stub_gap ->
rollback M-DESIGN, ac_gap -> awaiting Human + rollback M-ACC, spec_gap ->
awaiting Human + rollback M-SPEC.
"""
from tests.integration.helpers import assert_no_human_events_in_m_test, walk_to_m_test


# AC-FR0060-01@v0.4 TRACKS-TRACE diagnose no human
def test_diagnose_no_human(trac, event_log):
    """AC-FR0060-01@v0.4: DIAGNOSE routing is never a Human gate."""
    run_id = walk_to_m_test(trac)
    # test_defect: Shield writes bad tests -> DIAGNOSE -> test_defect -> re-dispatch
    r =     trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=test_defect")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    # No human.* events during M-TEST for test_defect routing
    assert_no_human_events_in_m_test(evs)


# AC-FR0060-02@v0.4 TRACKS-TRACE test defect redispatch
def test_test_defect_redispatch(trac, event_log):
    """AC-FR0060-02@v0.4: test_defect -> WRITE re-dispatch Shield."""
    run_id = walk_to_m_test(trac)
    trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=test_defect")
    evs = event_log(run_id)
    failed = [e for e in evs if e["type"] == "verdict.failed"
              and e["payload"].get("check") == "test_defect"]
    assert failed  # test_defect verdict was emitted
    assert failed[0]["payload"]["target_stage"] == "M-TEST"
    # Run completed (re-dispatched Shield wrote ok tests)
    assert any(e["type"] == "run.completed" for e in evs)


# AC-FR0060-03@v0.4 TRACKS-TRACE stub gap no human
def test_stub_gap_no_human(trac, event_log):
    """AC-FR0060-03@v0.4: stub_gap -> rollback M-DESIGN, no Human."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=stub_gap")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    failed = [e for e in evs if e["type"] == "verdict.failed"
              and e["payload"].get("check") == "stub_gap"]
    assert failed
    assert failed[0]["payload"]["target_stage"] == "M-DESIGN"
    # stage.rolled_back to M-DESIGN (no human.* approval for this route)
    rolled = [e for e in evs if e["type"] == "stage.rolled_back"]
    assert rolled
    assert rolled[0]["payload"]["to_stage"] == "M-DESIGN"
    # No human approval between verdict.failed and rollback
    fail_seq = failed[0]["seq"]
    roll_seq = rolled[0]["seq"]
    human_between = [e for e in evs if e["type"].startswith("human.")
                     and fail_seq < e["seq"] < roll_seq]
    assert not human_between


# AC-FR0060-04@v0.4 TRACKS-TRACE ac gap needs human
def test_ac_gap_needs_human(trac, event_log):
    """AC-FR0060-04@v0.4: ac_gap -> awaiting Human, then rollback M-ACC."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=illegit_red,diagnose:classification=ac_gap")
    assert "awaiting=rollback" in r.stdout
    evs = event_log(run_id)
    failed = [e for e in evs if e["type"] == "verdict.failed"
              and e["payload"].get("check") == "ac_gap"]
    assert failed
    assert failed[0]["payload"]["target_stage"] == "M-ACC"
    # No rollback yet (awaiting Human)
    assert not [e for e in evs if e["type"] == "stage.rolled_back"]
    # Human approves -> rollback
    assert trac("approve", "--actor", "Aaron").returncode == 0
    trac("run")
    evs2 = event_log(run_id)
    rolled = [e for e in evs2 if e["type"] == "stage.rolled_back"]
    assert rolled
    assert rolled[0]["payload"]["to_stage"] == "M-ACC"


# AC-FR0060-05@v0.4 TRACKS-TRACE spec gap needs human
def test_spec_gap_needs_human(trac, event_log):
    """AC-FR0060-05@v0.4: spec_gap -> awaiting Human, then rollback M-SPEC."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=illegit_red,diagnose:classification=spec_gap")
    assert "awaiting=rollback" in r.stdout
    evs = event_log(run_id)
    failed = [e for e in evs if e["type"] == "verdict.failed"
              and e["payload"].get("check") == "spec_gap"]
    assert failed
    assert failed[0]["payload"]["target_stage"] == "M-SPEC"
    # No rollback yet
    assert not [e for e in evs if e["type"] == "stage.rolled_back"]
    # Human approves -> rollback
    assert trac("approve", "--actor", "Aaron").returncode == 0
    trac("run")
    evs2 = event_log(run_id)
    rolled = [e for e in evs2 if e["type"] == "stage.rolled_back"]
    assert rolled
    assert rolled[0]["payload"]["to_stage"] == "M-SPEC"


# AC-FR0060-06@v0.4 TRACKS-TRACE verdict failed payload
def test_verdict_failed_payload(trac, event_log):
    """AC-FR0060-06@v0.4: verdict.failed carries classification, target_stage,
    artifact_disposition."""
    run_id = walk_to_m_test(trac)
    trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=stub_gap")
    evs = event_log(run_id)
    failed = [e for e in evs if e["type"] == "verdict.failed"
              and e["payload"].get("check") in
              ("test_defect", "stub_gap", "ac_gap", "spec_gap")]
    assert failed
    p = failed[0]["payload"]
    assert "check" in p
    assert "target_stage" in p
    assert "artifact_disposition" in p
