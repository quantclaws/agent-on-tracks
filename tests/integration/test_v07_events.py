"""Integration: v0.7 events append-only + identity + crash recovery (IF-AUTH-001, IF-MUTATION-001/002).

AC-NFR0140-01@v0.7 append-only and projection rebuild,
AC-NFR0140-02@v0.7 identity five-part binding,
AC-NFR0140-03@v0.7 WAL replay reruns missing, never pass,
AC-NFR0140-04@v0.7 unknown/missing/drift control fail-closed.

Assertions land on the append-only event outlet (interfaces §1a) observed via
`trac replay`/`trac report` and the authenticity/mutation public functions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import judge_authenticity
from tracks.executor.mutation import (
    MutationExperimentResult,
    MutationManifest,
    run_mutation_experiment,
)

pytestmark = pytest.mark.integration



# AC-NFR0140-01@v0.7 TRACKS-TRACE append-only and projection rebuild
def test_append_only_and_projection_replay(trac, host_repo, event_log):
    """AC-NFR0140-01: events are append-only; dropping the projection and
    rebuilding yields an invariant state (not a re-read echo of the log).

    The outlet (§1a/NFR-04): ``drop`` the derived ``runs``/``backlog``
    projection tables, then ``trac status`` re-derives state from the
    append-only events; the rebuilt status must match the pre-drop status.

    NOTE (SHIELD_FIX T-015 / issue 101): the v0.7 run must be ACTIVATED via
    the real journey (init -> start -> triage -> reviews) BEFORE any
    `trac run`, otherwise `trac run` exits rc=1 'no active run' and NO
    phase0.* event is ever emitted regardless of the implementation (a
    perpetual Red fixture). A bare `seed_v05_approved_baseline` created the
    project docs but not a run, so it could never reach the append-only
    phase0 outlet.
    """
    import sqlite3

    from tests.e2e.helpers import walk_to_await_human
    from tracks import paths

    walk_to_await_human(trac, version="v0.7")
    trac("run")
    pre_status = trac("status").stdout
    # v0.7 phase0 events must be present in the append-only stream.
    events_before = [e["type"] for e in event_log("latest")]
    assert any(t.startswith("phase0.") for t in events_before), (
        "phase0.* events missing from append-only stream"
    )
    # Drop the derived projection tables (NOT the events); trac status must
    # rebuild from events alone (NFR-04 / AC-NFR0140-01).
    db = paths.tracks_home(host_repo) / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM backlog")
        conn.commit()
        pre_event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert pre_event_count > 0, "events must persist (append-only, not dropped)"
    # Rebuild: trac status re-derives state from the append-only events.
    post_status = trac("status").stdout
    assert post_status == pre_status, (
        "projection rebuild must be invariant: status changed after drop+rebuild"
    )
    # The events table is append-only: no rows were mutated/deleted.
    events_after = [e["type"] for e in event_log("latest")]
    assert events_after == events_before, (
        "event stream must be append-only (no mutation across rebuild)"
    )


# AC-NFR0140-02@v0.7 TRACKS-TRACE identity five-part binding
def test_identity_five_part_binding():
    """AC-NFR0140-02: authenticity/mutation evidence binds five-part identity."""
    outcome = TestRunResult("node-id", "failed", "token")
    judgement = judge_authenticity(
        ac_ref="AC-NFR0140-02@v0.7",
        category="new",
        bound_nodes=("node-id",),
        outcomes={"node-id": outcome},
        legal_failure_kinds={"token"},
        counterexample_experiment=None,
    )
    # Five-part identity: AC/IF + candidate/patch digest + selection/evidence id
    # + attempt + actor. The judgement carries the AC ref; the mutation manifest
    # carries candidate/patch digest + runner_identity (actor).
    assert judgement.ac == "AC-NFR0140-02@v0.7"
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-NFR0140-02@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:candidate",
        patch_digest="sha256:patch",
        target_nodes=("node-id",),
        control_nodes=("node-c",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/...",),
        expected_result={"target": "killed", "controls": "green"},
    )
    assert manifest.candidate_digest.startswith("sha256:")
    assert manifest.runner_identity  # actor present


# AC-NFR0140-03@v0.7 TRACKS-TRACE WAL replay reruns missing, never pass
def test_wal_replay_reruns_missing_never_pass():
    """AC-NFR0140-03: a missing persisted result never reports status=passed.

    The contract outlet (§1g/§1a): WAL replay must re-run a missing experiment
    and surface a non-passed result. The stub raising NotImplementedError is the
    legal Red anchor (the re-run path is not implemented); a phantom pass is
    forbidden. We assert the returned result is a non-passed
    MutationExperimentResult — the stub never returns, so this fails legally."""
    def _raise(_repo, _nodes):
        raise RuntimeError("WAL: no persisted result")

    def _open(_):
        return Path("/tmp/wt")

    def _apply(_repo, _patch):
        return "identity_match"

    def _close(_):
        return None

    result = run_mutation_experiment(
        manifest=MutationManifest(
            protocol_version=1,
            ac="AC-NFR0140-03@v0.7",
            if_ref="IF-MUTATION-002",
            candidate_digest="sha256:candidate",
            patch_digest="sha256:patch",
            target_nodes=("node-t",),
            control_nodes=("node-c",),
            runner_identity="runtime:mutation-v1",
            allowed_change_scope=("tracks/...",),
            expected_result={"target": "killed", "controls": "green"},
        ),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply,
        run_nodes=_raise,
        close_worktree=_close,
    )
    assert isinstance(result, MutationExperimentResult)
    assert result.status != "passed", "missing result must never be passed"


# AC-NFR0140-04@v0.7 TRACKS-TRACE unknown/missing/drift control fail-closed
def test_unknown_missing_drift_control_fail_closed():
    """AC-NFR0140-04: identity drift / missing evidence fails closed."""
    outcome = TestRunResult("node-d", "passed", None)
    # An existing-green AC with a drifted (foreign candidate) counterexample
    # must fail closed (not verified).
    drifted_experiment = {
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": "sha256:foreign",  # drift
        "ac": "AC-NFR0140-04@v0.7",
    }
    judgement = judge_authenticity(
        ac_ref="AC-NFR0140-04@v0.7",
        category="existing",
        bound_nodes=("node-d",),
        outcomes={"node-d": outcome},
        legal_failure_kinds=set(),
        counterexample_experiment=drifted_experiment,
    )
    assert judgement.counterexample_kill != "verified", (
        "identity-drifted counterexample must fail closed"
    )
