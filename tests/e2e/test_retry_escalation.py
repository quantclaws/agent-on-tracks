"""Validation failure → evidence-carrying re-dispatch → escalation
(FR-11, FR-12, NFR-03): AC-11a, AC-12a, AC-N03a.
"""
from tests.e2e.helpers import dispatches


def start_to_draft(trac):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需要重派的需求").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0


def test_failed_validation_redispatches_with_evidence(trac, event_log):
    start_to_draft(trac)
    # first validation fails, second passes (AC-11a)
    r = trac("run", simulate="validator:story.md=schema_fail|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    fails = [e for e in evs if e["type"] == "verdict.failed"]
    assert len(fails) == 1
    assert fails[0]["payload"]["check"] == "schema"
    fail_seq = fails[0]["seq"]

    # story.md was not committed before the failure
    commits = [e for e in evs if e["type"] == "story.committed"]
    assert all(c["seq"] > fail_seq for c in commits)

    # the re-dispatch (after the failure) carries the failure evidence
    redispatch = [
        d for d in dispatches(evs, "DRAFT") if d["seq"] > fail_seq
    ]
    assert redispatch, "no re-dispatch after verdict.failed"
    evidence = redispatch[0]["payload"]["command"]["params"]["evidence"]
    assert evidence["check"] == "schema" and evidence["reason"]

    # NFR-03: the agent self-reported "done" yet the runtime still failed it
    outcome_before_fail = [
        e for e in evs
        if e["type"] == "outcome.received" and e["seq"] < fail_seq
    ]
    assert outcome_before_fail[-1]["payload"]["status"] == "done"


def test_three_failures_escalate(trac, event_log):
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    fails = [e for e in evs if e["type"] == "verdict.failed"]
    assert len(fails) == 3  # AC-12a: exactly 3 attempts, then stop
    assert len(dispatches(evs, "DRAFT")) == 3
    # no dispatch after the third failure
    last_fail_seq = fails[-1]["seq"]
    assert not [d for d in dispatches(evs) if d["seq"] > last_fail_seq]
    assert "awaiting=escalation" in r.stdout

    r = trac("status")
    assert r.returncode == 0 and "escalation" in r.stdout
