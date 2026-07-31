"""FR-0210 exit-gate failure paths: protocol-exit failures (over-reach,
no-target-diff) consume an attempt and re-dispatch the same author, escalating
to awaiting_human at the 3rd failure (Aaron: over-reach = re-dispatch, <=3).

Covers the exit-gate branches of `_on_outcome_received` (kernel/machine.py) and
the decision-loop re-dispatch in `decide()` — a failed `dispatch_agent` outcome
is NOT a produced document, carries failure evidence into the next prompt, and
shares the same attempt accounting as `verdict.failed`.
"""
from tests.e2e.helpers import assert_escalation_after_three_failures, dispatches


def start_to_draft(trac):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需要重派的需求").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0


def failed_outcomes(evs):
    return [
        e for e in evs
        if e["type"] == "outcome.received"
        and e["payload"]["status"] == "failed"
    ]


def test_over_reach_redispatches_with_evidence(trac, event_log):
    """Over-reach → outcome failed (over_reach) → re-dispatch carries evidence."""
    start_to_draft(trac)
    r = trac("run", simulate="scribe:DRAFT=over_reach|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log()

    fails = failed_outcomes(evs)
    assert len(fails) == 1
    assert fails[0]["payload"]["failure_class"] == "over_reach"
    assert fails[0]["payload"]["audit_evidence"]

    # the re-dispatch (after the failure) carries the failure evidence (FR-11)
    fail_seq = fails[0]["seq"]
    redispatch = [d for d in dispatches(evs, "DRAFT") if d["seq"] > fail_seq]
    assert redispatch, "no re-dispatch after over-reach failure"
    evidence = redispatch[0]["payload"]["command"]["params"].get("evidence")
    assert evidence and evidence["check"] == "over_reach"

    # a failed agent outcome is NOT a produced doc — no commit before re-dispatch
    commits = [e for e in evs if e["type"] == "story.committed"]
    assert all(c["seq"] > fail_seq for c in commits)


def test_over_reach_three_failures_escalate(trac, event_log):
    """Over-reach 3x → attempt accounting consumes to the limit → awaiting_human."""
    start_to_draft(trac)
    r = trac("run", simulate="scribe:DRAFT=over_reach")
    assert r.returncode == 0, r.stderr
    evs = event_log()

    fails = failed_outcomes(evs)
    assert_escalation_after_three_failures(trac, evs, r, fails)


def test_no_target_diff_redispatches_then_succeeds(trac, event_log):
    """Exit-0-but-no-diff (no_target_diff) → failed → re-dispatch succeeds."""
    start_to_draft(trac)
    r = trac("run", simulate="scribe:DRAFT=no_target_diff|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log()

    fails = failed_outcomes(evs)
    assert len(fails) == 1
    assert fails[0]["payload"]["failure_class"] == "no_target_diff"
    fail_seq = fails[0]["seq"]

    # the failed attempt never produced a doc: no commit before the failure
    assert not [e for e in evs if e["type"] == "story.committed" and e["seq"] < fail_seq]
    assert len(dispatches(evs, "DRAFT")) == 2  # failed attempt + re-dispatch
    # the re-dispatch succeeded and committed the doc
    assert any(e["type"] == "story.committed" for e in evs if e["seq"] > fail_seq)
    assert "awaiting=review" in r.stdout
