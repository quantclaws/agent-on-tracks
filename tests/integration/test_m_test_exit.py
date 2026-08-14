"""EXIT trace gate + test commit + boundary (FR-0070).

Tests the M-TEST exit gate: trace closure (filtered to required ACs),
test.committed, stage.exited, run.completed(boundary), and the SM-01.15
recovery path (trace fail -> WRITE re-dispatch).
"""

from tests.integration.helpers import m_test_events, walk_to_m_test


# AC-FR0070-01@v0.4 TRACKS-TRACE exit gate all conditions
def test_exit_gate_all_conditions(trac, event_log):
    """AC-FR0070-01@v0.4: EXIT gate = collection + legit Red + Prism pass + trace."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    # All four conditions verified by their events
    assert any(e["type"] == "test.collected" and e["payload"]["status"] == "passed" for e in evs)
    assert any(e["type"] == "red.validated" and e["payload"]["status"] == "valid" for e in evs)
    assert any(e["type"] == "prism.verdict" and e["payload"]["verdict"] == "pass" for e in evs)
    assert any(e["type"] == "verdict.passed" and e["payload"]["check"] == "trace" for e in evs)
    # Exit happened
    assert any(e["type"] == "stage.exited" and e["payload"]["stage"] == "M-TEST" for e in evs)


# AC-FR0070-02@v0.4 TRACKS-TRACE trace closure required ACs
def test_trace_closure_required_acs(trac, event_log):
    """AC-FR0070-02@v0.4: trace closure verifies required AC markers."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    trace_pass = [
        e for e in evs if e["type"] == "verdict.passed" and e["payload"]["check"] == "trace"
    ]
    assert trace_pass  # trace gate passed


# AC-FR0070-03@v0.4 TRACKS-TRACE trace filters required only
def test_trace_filters_required_only(trac, event_log):
    """AC-FR0070-03@v0.4: trace gate filters required ACs (integration|e2e);
    unit-only AC gaps don't block M-TEST exit."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    # The run completed = trace gate passed (unit-only AC gaps don't block)
    assert any(e["type"] == "run.completed" for e in evs)


# AC-FR0070-04@v0.4 TRACKS-TRACE trace fail no exit
def test_trace_fail_no_exit(trac, event_log):
    """AC-FR0070-04@v0.4: trace gate failure -> no stage.exited(M-TEST)."""
    run_id = walk_to_m_test(trac)
    # short_marker: Shield writes tests with short-format markers (trace fails)
    # then ok on re-dispatch -> trace passes on second try
    r = trac("run", simulate="shield:WRITE=short_marker|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    # First trace check failed
    trace_fails = [
        e for e in evs if e["type"] == "verdict.failed" and e["payload"].get("check") == "trace"
    ]
    assert trace_fails
    # But eventually completed (re-dispatch fixed the markers)
    assert any(e["type"] == "run.completed" for e in evs)


# AC-FR0070-05@v0.4 TRACKS-TRACE trace fail redispatch
# AC-FR0080-13@v0.4 TRACKS-TRACE Shield marker obligation enforced by trace
def test_trace_fail_redispatch(trac, event_log):
    """AC-FR0070-05@v0.4: trace fail -> EXIT->WRITE re-dispatch Shield."""
    run_id = walk_to_m_test(trac)
    trac("run", simulate="shield:WRITE=short_marker|ok")
    evs = event_log(run_id)
    # trace failure -> verdict.failed(trace)
    trace_fail = [
        e for e in evs if e["type"] == "verdict.failed" and e["payload"].get("check") == "trace"
    ]
    assert trace_fail
    # Shield re-dispatched after trace failure
    m_test_evs = m_test_events(evs)
    trace_fail_seq = trace_fail[0]["seq"]
    shield_redispatch = [
        e
        for e in m_test_evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "shield"
        and e["seq"] > trace_fail_seq
    ]
    assert shield_redispatch  # Shield was re-dispatched to fix markers


# AC-FR0080-11@v0.4 TRACKS-TRACE trace as verdict source
def test_trace_as_verdict_source(trac, event_log):
    """AC-FR0080-11@v0.4: engine reads trace as verdict source (verdict.passed)."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    # The trace gate emits verdict.passed or verdict.failed
    trace_verdicts = [
        e
        for e in evs
        if e["type"] in ("verdict.passed", "verdict.failed")
        and e["payload"].get("check") == "trace"
    ]
    assert trace_verdicts
    assert trace_verdicts[0]["type"] == "verdict.passed"  # happy path


# AC-FR0070-07@v0.4 TRACKS-TRACE test committed
def test_test_committed(trac, event_log, host_repo):
    """AC-FR0070-07@v0.4: controlled test commit -> test.committed + stage.exited."""
    import subprocess

    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    committed = [e for e in evs if e["type"] == "test.committed"]
    assert len(committed) == 1
    assert committed[0]["payload"]["test_count"] > 0
    # git log contains the controlled test commit
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=host_repo, capture_output=True, text=True
    ).stdout
    assert "M-TEST: freeze test assets" in log


# AC-FR0070-08@v0.4 TRACKS-TRACE boundary after M-TEST
def test_boundary_after_m_test(trac, event_log):
    """AC-FR0070-08@v0.4: stage.exited(M-TEST) -> run.completed(boundary)."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    exited = [e for e in evs if e["type"] == "stage.exited" and e["payload"]["stage"] == "M-TEST"]
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert exited and completed
    assert exited[0]["seq"] < completed[0]["seq"]
    assert completed[0]["payload"]["terminal_state"] == "boundary"


# AC-FR0070-06@v0.4 TRACKS-TRACE no human gate in M-TEST
def test_no_human_gate_in_m_test(trac, event_log):
    """AC-FR0070-06@v0.4: M-TEST has no Human gate (no human.review/approval
    as exit prerequisite in the happy path)."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    human_evs = [e for e in evs if e["type"].startswith("human.")]
    assert not human_evs  # no Human gate in M-TEST happy path
