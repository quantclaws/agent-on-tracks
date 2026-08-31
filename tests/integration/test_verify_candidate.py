"""Integration: candidate freeze and binding (FR-0267, IF-VERIFY-001)."""

from __future__ import annotations

import subprocess

import pytest

from tracks.executor.m_verify import freeze_candidate

pytestmark = pytest.mark.integration


# AC-FR0267-01@v0.8 TRACKS-TRACE candidate frozen with full SHA and clean tree binding
def test_clean_tree_freezes_candidate(host_repo, trac, event_log):
    repo = host_repo
    # Verify the stub contract is present and raises the IF token before any
    # runtime wiring exists. This is the legal-Red anchor: the interface is
    # declared but not yet implemented.
    try:
        freeze_candidate(repo)
        raise AssertionError("freeze_candidate must raise NotImplementedError(IF-VERIFY-001)")
    except NotImplementedError as exc:
        assert "IF-VERIFY-001" in str(exc)

    # Behavioural assertion on the public outlet: a clean-tree run must
    # produce candidate.frozen with full SHA and clean_tree=true and bind
    # that SHA to all later release-chain events.
    # The outlet is events + status/replay (interfaces §4a#1).
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert len(head) == 40

    # Drive the release entry if implemented; before implementation the
    # events are absent and the assertion fails legally.
    trac("run")
    events = event_log()
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    assert frozen, "candidate.frozen must appear after clean-tree freeze"
    payload = frozen[0]["payload"]
    assert payload["candidate_sha"] == head
    assert payload["clean_tree"] is True
    # All downstream release events must carry the same candidate_sha
    downstream = [e for e in events if "candidate_sha" in e["payload"]]
    for ev in downstream:
        assert ev["payload"]["candidate_sha"] == head


# AC-FR0267-02@v0.8 TRACKS-TRACE dirty tree does not freeze candidate and needs_attention
def test_dirty_tree_needs_attention(host_repo, trac, event_log):
    repo = host_repo
    # Make the tree dirty (tracked file modification)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    try:
        freeze_candidate(repo)
        raise AssertionError("freeze_candidate must raise IF-VERIFY-001 even on dirty tree")
    except NotImplementedError as exc:
        assert "IF-VERIFY-001" in str(exc)

    result = trac("run")
    events = event_log()
    frozen = [e for e in events if e["type"] == "candidate.frozen"]
    # No successful freeze on dirty tree
    assert not any(e["payload"].get("clean_tree") is True for e in frozen)
    # Must surface needs_attention with dirty_tree reason and next guidance
    status = trac("status")
    combined = result.stdout + result.stderr + status.stdout + status.stderr
    assert "needs_attention" in combined or "dirty_tree" in combined
    # Also via events if implemented
    attention = [e for e in events if e["type"] == "attention.required"]
    if attention:
        assert any(a["payload"].get("reason") == "dirty_tree" for a in attention)
        assert all(a["payload"].get("next") for a in attention)


# AC-FR0267-03@v0.8 TRACKS-TRACE drift marks stale and replay does not refreeze
def test_drift_marks_stale_no_refreeze(host_repo, trac, event_log):
    repo = host_repo
    try:
        from tracks.executor.m_verify import collect_binding_violations

        violations = collect_binding_violations([], "deadbeef" * 5)
        assert isinstance(violations, list)
        raise AssertionError("collect_binding_violations must raise IF-VERIFY-001")
    except NotImplementedError as exc:
        assert "IF-VERIFY-001" in str(exc)

    trac("run")
    events_before = event_log()
    frozen_before = [e for e in events_before if e["type"] == "candidate.frozen"]
    # Produce a drift commit after freeze attempt
    (repo / "drift.txt").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "add", "drift.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "drift"], cwd=repo, check=True)
    trac("run")
    events_after = event_log()
    # Drift must produce candidate.stale or blocked with stale reason
    stale = [e for e in events_after if e["type"] == "candidate.stale"]
    assert stale, "candidate.stale must appear after drift"
    assert any(
        s["payload"].get("reason") in ("candidate_drift", "head_moved") for s in stale
    )
    frozen_after = [e for e in events_after if e["type"] == "candidate.frozen"]
    # Idempotent: exactly one successful freeze for the original candidate
    if frozen_before and frozen_after:
        assert len(frozen_after) == len(frozen_before)
    # Replay must rebuild identity without duplicating freeze
    replay = trac("replay")
    assert replay.returncode == 0 or "candidate.frozen" in replay.stdout
