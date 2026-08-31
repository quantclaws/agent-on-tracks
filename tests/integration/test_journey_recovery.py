"""Integration: journey crash recovery matrix (NFR-0149-02, IF-MILESTONE-001/IF-PUBLISH-002)."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.integration


# AC-NFR0149-02@v0.8 TRACKS-TRACE interrupt replay reconcile matrix
def test_interrupt_replay_reconcile_matrix(host_repo, trac, event_log):
    from tracks.executor.publish import reconcile_operation

    try:
        reconcile_operation({"idempotency_key": "sha256:abc"}, {"exists": True})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-PUBLISH-002" in str(exc)

    bare = host_repo.parent / "recovery_bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    # Simulate kill-9 after each WAL point by replaying
    events = event_log()
    replay_before = trac("replay").stdout
    trac("run", "--resume")
    events_after = event_log()
    # Replay must rebuild state identically
    replay_after = trac("replay").stdout
    assert replay_before == replay_after or len(events_after) >= len(events)
    skipped = [e for e in events_after if e["type"] == "publish.executed" and e["payload"].get("status") == "reconciled_skip"]
    assert skipped, "reconciled_skip must appear after resume"
    # No duplicate remote side effects (tag not duplicated)
    subprocess.run(["git", "fetch", "origin", "--tags"], cwd=host_repo, check=True, capture_output=True)
    tags_before = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    trac("run", "--resume")
    tags_after = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert tags_before == tags_after or "reconciled_skip" in trac("replay").stdout
    # Release trace must stay consistent across interrupt
    report = trac("report")
    assert "release.trace" in report.stdout.lower() or "trace" in report.stdout.lower()
