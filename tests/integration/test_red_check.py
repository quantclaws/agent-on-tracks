"""Red classification via events (FR-0050).

Tests the RED_CHECK substate: Runtime independently re-runs tests, classifies
failures, and routes to EXIT (valid) or DIAGNOSE (invalid).
"""
from tests.integration.helpers import walk_to_m_test


def test_red_check_independent_rerun(trac, event_log):
    """AC-FR0050-01@v0.4: Runtime independently re-runs (run_tests command)."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    run_cmds = [e for e in evs if e["type"] == "command.issued"
                and e["payload"]["command"]["kind"] == "run_tests"]
    assert len(run_cmds) == 1  # exactly one independent re-run


def test_legit_red_validated(trac, event_log):
    """AC-FR0050-02@v0.4: legit Red (stub_token_failure) -> red.validated(valid)."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = event_log(run_id)
    red = [e for e in evs if e["type"] == "red.validated"]
    assert red[0]["payload"]["status"] == "valid"
    findings = red[0]["payload"]["findings"]
    assert findings[0]["classification"] == "stub_token_failure"


def test_illegit_red_to_diagnose(trac, event_log):
    """AC-FR0050-03@v0.4: illegit Red (ImportError) -> red.validated(invalid) -> DIAGNOSE."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=test_defect")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    red = [e for e in evs if e["type"] == "red.validated"]
    assert red[0]["payload"]["status"] == "invalid"
    assert red[0]["payload"]["findings"][0]["classification"] == "collection_error"
    # DIAGNOSE -> test_defect -> WRITE re-dispatch (ok) -> eventually completes
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert completed


def test_unexpected_pass_to_diagnose(trac, event_log):
    """AC-FR0050-04@v0.4: unexpected pass -> red.validated(invalid) -> DIAGNOSE."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=pass_red|ok,diagnose:classification=test_defect")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    red = [e for e in evs if e["type"] == "red.validated"]
    assert red[0]["payload"]["status"] == "invalid"
    assert red[0]["payload"]["findings"][0]["classification"] == "unexpected_pass"


def test_all_pass_does_not_exit(trac, event_log):
    """AC-FR0050-06@v0.4: all tests pass -> does NOT exit M-TEST (enters DIAGNOSE)."""
    run_id = walk_to_m_test(trac)
    r = trac("run", simulate="shield:WRITE=pass_red,diagnose:classification=test_defect")
    evs = event_log(run_id)
    # No stage.exited(M-TEST) because DIAGNOSE -> test_defect -> escalation
    exited = [e for e in evs if e["type"] == "stage.exited"
              and e["payload"]["stage"] == "M-TEST"]
    assert not exited
    assert "awaiting=escalation" in r.stdout
