"""Acceptance trace gate wired into the run loop (FR-0170, SM-04.3):
always-on trace fails the M-ACC draft, re-dispatches Sage with evidence,
recovers on a full-coverage redraft, and escalates after three failures.
"""

from tests.e2e.helpers import dispatches


def walk_to_acc_draft(trac):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="trace 门禁旅程").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0
    for _ in ("M-STORY", "M-SPEC"):
        r = trac("run")
        assert r.returncode == 0, r.stderr
        assert "awaiting=review" in r.stdout
        assert trac("review", "no-comment").returncode == 0


def test_trace_fail_redispatch(trac, event_log):
    walk_to_acc_draft(trac)
    # first acceptance draft leaves the last spec item uncovered, redraft is ok
    r = trac("run", simulate="sage:DRAFT=trace_orphan|ok")
    assert r.returncode == 0, r.stderr
    assert "awaiting=review" in r.stdout  # recovered into LEX_REVIEW
    evs = event_log()
    fails = [e for e in evs if e["type"] == "verdict.failed"]
    assert len(fails) == 1
    assert fails[0]["payload"]["check"] == "trace"
    assert "line:" in fails[0]["payload"]["reason"]  # orphan carries line:N
    fail_seq = fails[0]["seq"]
    redispatch = [d for d in dispatches(evs, "DRAFT") if d["seq"] > fail_seq]
    assert redispatch, "no re-dispatch after trace verdict.failed"
    evidence = redispatch[0]["payload"]["command"]["params"]["evidence"]
    assert evidence["check"] == "trace" and "line:" in evidence["reason"]


def test_trace_three_failures_escalate(trac, event_log):
    walk_to_acc_draft(trac)
    r = trac("run", simulate="sage:DRAFT=trace_orphan")
    assert r.returncode == 0, r.stderr
    assert "awaiting=escalation" in r.stdout
    evs = event_log()
    fails = [e for e in evs if e["type"] == "verdict.failed" and e["payload"]["check"] == "trace"]
    assert len(fails) == 3  # AC-12a parity: three attempts, then Human
    last_fail_seq = fails[-1]["seq"]
    assert not [d for d in dispatches(evs) if d["seq"] > last_fail_seq]
