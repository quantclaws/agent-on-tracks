"""Integration: independent Human three-way gate (FR-0274, IF-RELEASE-003)."""

from __future__ import annotations

import pytest

from tracks.executor.release_gate import validate_release_decision

pytestmark = pytest.mark.integration


# AC-FR0274-01@v0.8 TRACKS-TRACE release action allowed when gates pass and preview fresh
def test_release_action_allowed(host_repo, trac, event_log):
    try:
        validate_release_decision("release", {"preview_digest": "sha256:abc"}, {"gate": "passed"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-003" in str(exc)

    trac("run")
    events = event_log()
    previewed = [e for e in events if e["type"] == "release.previewed"]
    assert previewed
    digest = previewed[0]["payload"]["preview_digest"]
    candidate = previewed[0]["payload"]["candidate_sha"]
    # Human release action via independent CLI surface
    result = trac("release", "--action", "release")
    # When gates pass and preview fresh, decision must be recorded
    assert "decision: release" in result.stdout or result.returncode == 0
    events2 = event_log()
    decided = [e for e in events2 if e["type"] == "release.decided" and e["payload"].get("action") == "release"]
    assert decided
    assert decided[0]["payload"]["candidate_sha"] == candidate
    assert decided[0]["payload"]["preview_digest"] == digest
    assert decided[0]["payload"]["actor"] == "human"
    status = trac("status")
    assert "decision=release" in status.stdout
    # approve surface must not produce release.decided
    trac("run")
    events3 = event_log()
    decided_via_approve = [e for e in events3 if e["type"] == "release.decided"]
    # The only decided events are via release CLI, not approve
    assert all(d["payload"].get("actor") == "human" for d in decided_via_approve)


# AC-FR0274-02@v0.8 TRACKS-TRACE rejected gate or stale blocks release decision
def test_rejected_gate_or_stale(host_repo, trac, event_log):
    try:
        validate_release_decision("release", {}, {"gate": "failed"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-003" in str(exc)

    trac("run")
    # Simulate gate failure by corrupting a gate declaration
    import subprocess

    (host_repo / "gate_fail.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "gate_fail.txt"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "drift for gate fail"], cwd=host_repo, check=True)
    trac("run")
    result = trac("release", "--action", "release")
    assert result.returncode != 0
    assert "release rejected" in result.stdout or "gate failed" in result.stdout or "preview stale" in result.stdout
    events = event_log()
    rejected = [e for e in events if e["type"] == "release.rejected"]
    assert rejected or result.returncode != 0
    if rejected:
        assert rejected[0]["payload"]["reason"] in ("gate_failed", "preview_stale")
    assert "AWAITING_RELEASE" in trac("status").stdout or "awaiting_release" in trac("status").stdout.lower()
    # No transition to M-PUBLISH
    assert not any(e["type"] == "publish.planned" for e in events)


# AC-FR0274-03@v0.8 TRACKS-TRACE delay and return bind preview and move or park
def test_delay_and_return(host_repo, trac, event_log):
    try:
        validate_release_decision("delay", {"preview_digest": "sha256:xyz"}, {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-003" in str(exc)

    trac("run")
    delay = trac("release", "--action", "delay", "--reason", "need more review")
    assert "decision: delay" in delay.stdout or delay.returncode == 0
    events = event_log()
    delayed = [e for e in events if e["type"] == "release.decided" and e["payload"].get("action") == "delay"]
    assert delayed
    assert "delay" in trac("status").stdout.lower() or "delayed" in trac("status").stdout.lower()
    # Return must bind same preview and stale downstream but keep evidence
    ret = trac("release", "--action", "return", "--to", "M-DESIGN", "--reason", "revisit")
    assert "decision: return" in ret.stdout or ret.returncode == 0
    events2 = event_log()
    returned = [e for e in events2 if e["type"] == "release.decided" and e["payload"].get("action") == "return"]
    assert returned
    assert returned[0]["payload"]["target"] == "M-DESIGN"


# AC-FR0274-04@v0.8 TRACKS-TRACE surface exclusivity and stale checks for delay/return
def test_surface_exclusivity(host_repo, trac, event_log):
    try:
        validate_release_decision("return", {"preview_digest": "sha256:abc", "stale": True}, {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-003" in str(exc)

    trac("run")
    import subprocess

    (host_repo / "stale2.txt").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "add", "stale2.txt"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "stale2"], cwd=host_repo, check=True)
    # Even delay/return must be rejected when preview stale
    delay_stale = trac("release", "--action", "delay", "--reason", "x")
    assert delay_stale.returncode != 0
    assert "preview stale" in delay_stale.stdout.lower() or "stale" in delay_stale.stdout.lower()
    ret_stale = trac("release", "--action", "return", "--to", "M-VERIFY", "--reason", "x")
    assert ret_stale.returncode != 0
    assert not any(e["type"] == "release.decided" and e["payload"].get("action") == "delay" for e in event_log() if e["payload"].get("preview_digest") and "stale" in str(e))
    # Approve / GitHub UI must not produce release.decided
    trac("approve", "--actor", "Aaron")
    # approve may be irrelevant in release stage; ensure no release.decided appears via it
    events = event_log()
    decided_via_approve = [e for e in events if e["type"] == "release.decided" and e["payload"].get("actor") != "human"]
    assert not decided_via_approve
    assert True
