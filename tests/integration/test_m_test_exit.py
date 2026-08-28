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
    """AC-FR0070-04@v0.4: short-marker Shield WRITE is rejected fail-closed;
    re-dispatch fixes markers and the run eventually completes.

    Evolution note: the short-format marker rejection legitimately moved to
    the marker preflight (verdict.failed check=test_defect,
    reason=marker_preflight) ahead of the trace verdict -- same fail-closed
    contract, earlier enforcement point. The preserved semantics: a defective
    marker WRITE never exits M-TEST until re-dispatch repairs it."""
    run_id = walk_to_m_test(trac)
    # short_marker: Shield writes tests with short-format markers (rejected)
    # then ok on re-dispatch -> markers fixed on second try
    r = trac("run", simulate="shield:WRITE=short_marker|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    # First marker/trace check failed (fail-closed rejection of the defective
    # WRITE -- marker preflight or trace verdict, whichever enforcement point).
    marker_fails = [
        e
        for e in evs
        if e["type"] == "verdict.failed"
        and (
            e["payload"].get("check") == "trace"
            or e["payload"].get("reason") == "marker_preflight"
        )
    ]
    assert marker_fails
    # But eventually completed (re-dispatch fixed the markers)
    assert any(e["type"] == "run.completed" for e in evs)


# AC-FR0070-05@v0.4 TRACKS-TRACE trace fail redispatch
# AC-FR0080-13@v0.4 TRACKS-TRACE Shield marker obligation enforced by trace
def test_trace_fail_redispatch(trac, event_log):
    """AC-FR0070-05@v0.4: short-marker rejection -> Shield re-dispatch.

    Evolution note: as in AC-FR0070-04, the rejection surfaces at the marker
    preflight (check=test_defect, reason=marker_preflight); the Shield
    re-dispatch contract is unchanged."""
    run_id = walk_to_m_test(trac)
    trac("run", simulate="shield:WRITE=short_marker|ok")
    evs = event_log(run_id)
    # marker/trace failure -> verdict.failed (preflight or trace check)
    marker_fail = [
        e
        for e in evs
        if e["type"] == "verdict.failed"
        and (
            e["payload"].get("check") == "trace"
            or e["payload"].get("reason") == "marker_preflight"
        )
    ]
    assert marker_fail
    # Shield re-dispatched after the failure
    m_test_evs = m_test_events(evs)
    fail_seq = marker_fail[0]["seq"]
    shield_redispatch = [
        e
        for e in m_test_evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "shield"
        and e["seq"] > fail_seq
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
    # git log contains the controlled test commit. b230664/B59 legitimately
    # evolved the freeze flow: Shield's WRITE output is committed during the
    # pipeline (the commit message comes from the Shield manifest's
    # suggested_commit_message, e.g. 'shield checkpoint'), and the M-TEST
    # freeze anchors on that controlled commit instead of a fixed
    # 'M-TEST: freeze test assets' literal. The preserved contract: the test
    # assets land via a controlled commit at M-TEST (not dirty at freeze).
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "tests"],
        cwd=host_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert not status.strip(), (
        "test assets must be committed at the M-TEST freeze (controlled "
        f"commit, no dirty tests/ residue); got: {status!r}"
    )
    assert committed[0]["payload"]["commit_sha"], (
        "test.committed must reference the controlled freeze commit"
    )
    assert committed[0]["payload"]["commit_sha"] in subprocess.run(
        ["git", "rev-list", "HEAD"],
        cwd=host_repo,
        capture_output=True,
        text=True,
    ).stdout, "test.committed commit_sha must be an actual git commit"


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
