"""Scope overflow rollback (FR-20): AC-20a."""

import subprocess


def git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def dispatches(evs):
    return [
        e
        for e in evs
        if e["type"] == "command.issued" and e["payload"]["command"]["kind"] == "dispatch_agent"
    ]


def test_overflow_spec_rolls_back_to_story(host_repo, trac, event_log):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="一个范围过大的需求").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0
    assert trac("run").returncode == 0  # M-STORY → HUMAN_REVIEW
    assert trac("review", "no-comment").returncode == 0

    # EXIT M-STORY → M-SPEC DRAFT writes a 31-FR spec → overflow → rollback
    r = trac("run", simulate="sage:DRAFT=scope_overflow")
    assert r.returncode == 0, r.stderr
    evs = event_log()

    fails = [
        e
        for e in evs
        if e["type"] == "verdict.failed" and e["payload"]["check"] == "scope_overflow"
    ]
    assert len(fails) == 1
    assert "31" in fails[0]["payload"]["reason"]

    rollbacks = [e for e in evs if e["type"] == "stage.rolled_back"]
    assert len(rollbacks) == 1
    assert rollbacks[0]["payload"]["from_stage"] == "M-SPEC"
    assert rollbacks[0]["payload"]["to_stage"] == "M-STORY"
    assert rollbacks[0]["seq"] > fails[0]["seq"]

    # landing point is DRAFT: next dispatch is Scribe with overflow evidence,
    # and no new TRIAGE dispatch after the rollback (AC-20a)
    after = [d for d in dispatches(evs) if d["seq"] > rollbacks[0]["seq"]]
    assert after, "no dispatch after rollback"
    first = after[0]["payload"]["command"]["params"]
    assert first["role"] == "scribe" and first["substate"] == "DRAFT"
    assert first["evidence"]["check"] == "scope_overflow"
    assert all(d["payload"]["command"]["params"]["substate"] != "TRIAGE" for d in after)

    # branch survives the rollback; run parked cleanly at the human gate
    assert "releases/v0.1" in git_out(host_repo, "branch", "--list", "releases/v0.1")
    assert "awaiting=review" in r.stdout
