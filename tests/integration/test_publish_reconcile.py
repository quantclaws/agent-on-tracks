"""Integration: publish reconcile idempotency (NFR-0144, IF-PUBLISH-002).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(M-IMPL park -> bounded drives to M-RELEASE/AWAITING_RELEASE -> ``trac
release`` -> ``trac run`` executes M-PUBLISH) — bare ``trac run`` bootstrap is
forbidden (v0.8 suite-wide defect). The release chain needs the loopback CI
stand-in (``ci_echo_standin``) and a bare ``origin`` bound as above. Resume is
the runtime's own D-13 recovery seam: the issued ``execute_publish`` command
is replayed with ``reconcile=True`` through the production handler, exactly
what ``Executor._recover`` does for a hanging command. The module-level
halves assert the delivered IF-PUBLISH-002 reconcile contract.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import (
    force_diverged_main,
    init_bare_remote,
    push_ancestor_main,
    replay_execute_publish,
    walk_to_awaiting_release,
)
from tracks.executor.publish import reconcile_operation

pytestmark = pytest.mark.integration


# AC-NFR0144-01@v0.8 TRACKS-TRACE repeat operation skips no duplicates
def test_repeat_operation_skips_no_duplicates(host_repo, trac, event_log, ci_echo_standin):
    verdict = reconcile_operation({"idempotency_key": "k1", "target": "t"}, {"exists": True, "matches": True})
    assert verdict == "skip"

    init_bare_remote(host_repo, "bare.git")
    run_id = walk_to_awaiting_release(trac)
    assert trac("release", "--action", "release").returncode == 0
    trac("run")
    events = event_log()
    done = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert done, "the first publish must complete before the reconcile replay"
    # Resume must not duplicate remote effects
    replay_execute_publish(host_repo, run_id)
    events2 = event_log()
    skipped = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "reconciled_skip"]
    assert skipped, "reconciled_skip required for idempotent repeat"
    for s in skipped:
        assert s["payload"]["idempotency_key"].startswith("sha256:")
    # No duplicate done entries
    done2 = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert len(done2) == len(done)
    # publish progress must remain consistent; the closing tail may have
    # legitimately advanced the run past M-PUBLISH to terminal=released
    status_out = trac("status").stdout
    assert "M-PUBLISH" in status_out or (
        "terminal=released" in status_out and "M-MILESTONE" in status_out
    )


# AC-NFR0144-01@v0.8 TRACKS-TRACE unfinished continues after reconcile
def test_unfinished_continues(host_repo, trac, event_log, ci_echo_standin):
    verdict = reconcile_operation({"idempotency_key": "k2"}, {"exists": False})
    assert verdict == "pending"

    # Empty bare: the first publish attempt plans then fails closed with
    # branch_missing (interrupted write-ahead), leaving the op unfinished.
    _bare, initial = init_bare_remote(host_repo, "bare.git", push_main=False)
    run_id = walk_to_awaiting_release(trac)
    assert trac("release", "--action", "release").returncode == 0
    trac("run")
    events = event_log()
    planned = [e for e in events if e["type"] == "publish.planned"]
    assert planned, "publish.planned required before resume"
    for p in planned:
        assert p["payload"]["idempotency_key"].startswith("sha256:")
        assert p["payload"]["candidate_sha"]
    failed = [e for e in events if e["type"] == "publish.failed"]
    assert failed and failed[-1]["payload"]["reason"] == "branch_missing"
    assert not [e for e in events if e["type"] == "publish.executed"]
    # Repair the remote (an ancestor main) and resume: the unfinished op
    # continues to done after reconcile.
    push_ancestor_main(host_repo, initial)
    replay_execute_publish(host_repo, run_id)
    events2 = event_log()
    done = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert done, "unfinished ops must continue to done after reconcile"
    for d in done:
        assert d["payload"]["idempotency_key"].startswith("sha256:")
        assert d["payload"]["candidate_sha"]


# AC-NFR0144-02@v0.8 TRACKS-TRACE same key remote diff conflict is blocked
def test_same_key_remote_diff_conflict(host_repo, trac, event_log, ci_echo_standin):
    verdict = reconcile_operation(
        {"idempotency_key": "sha256:abc", "target": "t", "digest": "d1"},
        {"idempotency_key": "sha256:abc", "target": "t", "digest": "d2"},
    )
    assert verdict == "conflict"

    init_bare_remote(host_repo, "bare.git")
    run_id = walk_to_awaiting_release(trac)
    assert trac("release", "--action", "release").returncode == 0
    trac("run")
    events = event_log()
    done = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert done, "the first publish must complete before the divergence"
    # Inject a remote divergence (manual overwrite with a non-ancestor sha)
    # then resume: the reconcile must surface conflict.
    divergent = force_diverged_main(host_repo)
    replay_execute_publish(host_repo, run_id)
    events2 = event_log()
    conflicts = [e for e in events2 if e["type"] == "reconcile_conflict"]
    if conflicts:
        assert conflicts[0]["payload"]["idempotency_key"].startswith("sha256:")
        assert "expected" in conflicts[0]["payload"]
        assert "remote" in conflicts[0]["payload"]
        assert conflicts[0]["payload"]["remote"]["object_id"] == divergent
        blocked = [e for e in events2 if e["type"] == "publish.failed" and e["payload"].get("reason") == "reconcile_conflict"]
        assert blocked
        assert "reconcile_conflict" in trac("replay").stdout
    else:
        raise AssertionError("reconcile_conflict must appear for same-key diff")
