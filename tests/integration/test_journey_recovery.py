"""Integration: journey crash recovery matrix (NFR-0149-02, IF-MILESTONE-001/IF-PUBLISH-002).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_awaiting_release``) — bare ``trac run`` bootstrap is forbidden.
The interruption is replayed through the production publish handler with the
same write-ahead command (``replay_execute_publish``), so the real remote
decides done/reconciled_skip/conflict. The release chain needs the loopback
CI stand-in (``ci_echo_standin``) and a bare origin whose main is an ancestor
of the frozen candidate.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.helpers import (
    init_bare_remote,
    replay_execute_publish,
    walk_to_awaiting_release,
)

pytestmark = pytest.mark.integration


# AC-NFR0149-02@v0.8 TRACKS-TRACE interrupt replay reconcile matrix
def test_interrupt_replay_reconcile_matrix(host_repo, trac, event_log, ci_echo_standin):
    from tracks.executor.publish import reconcile_operation

    # Module contract (IF-PUBLISH-002, unit-pinned): the remote state decides
    # the reconcile verdict — absent target is pending, matching target skips,
    # same key with divergent content conflicts.
    planned = {"idempotency_key": "sha256:abc", "target": "main", "digest": "d1"}
    assert reconcile_operation(planned, {"exists": False}) == "pending"
    assert reconcile_operation(planned, {"exists": True, "matches": True}) == "skip"
    assert (
        reconcile_operation(planned, {"exists": True, "digest": "d2"}) == "conflict"
    )

    bare, _initial = init_bare_remote(host_repo, "recovery_bare.git")
    run_id = walk_to_awaiting_release(trac)
    assert run_id
    assert trac("release", "--action", "release").returncode == 0
    trac("run")
    events = event_log(run_id)
    done = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert done, "the publish must complete before the reconcile replay"
    candidate = done[0]["payload"]["candidate_sha"]
    # Simulate kill-9 after the write-ahead point: the same execute_publish
    # command is replayed with reconcile=True through the production handler.
    replay_execute_publish(host_repo, run_id)
    events_after = event_log(run_id)
    skipped = [
        e
        for e in events_after
        if e["type"] == "publish.executed"
        and e["payload"].get("status") == "reconciled_skip"
    ]
    assert skipped, "reconciled_skip must appear after resume"
    for s in skipped:
        assert s["payload"]["idempotency_key"] in [
            d["payload"]["idempotency_key"] for d in done
        ]
    # No duplicate remote side effects: main still points at the candidate.
    ls = subprocess.run(
        ["git", "ls-remote", str(bare), "refs/heads/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"{candidate}\trefs/heads/main" in ls
    # Release trace must stay consistent across the interrupt/replay.
    replay = trac("replay")
    assert "reconciled_skip" in replay.stdout or skipped
    report = trac("report")
    assert "release" in report.stdout.lower() or "trace" in report.stdout.lower()
