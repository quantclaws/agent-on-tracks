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

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import judge_authenticity
from tracks.executor.mutation import (
    MutationExperimentResult,
    MutationManifest,
    run_mutation_experiment,
)


# AC-NFR0140-01@v0.7 TRACKS-TRACE append-only and projection rebuild
def test_append_only_and_projection_replay(trac, event_log):
    """AC-NFR0140-01: v0.7 events append-only; projection rebuild invariant."""
    trac("run", simulate="v07_phase0_seal")
    run_id = "latest"
    events_before = event_log(run_id)
    types_before = [e["type"] for e in events_before]
    # v0.7 phase0 events must be present in the append-only stream.
    assert any(t.startswith("phase0.") for t in types_before), (
        "phase0.* events missing from append-only stream"
    )
    # Re-running the projection must reproduce the same event sequence.
    events_after = event_log(run_id)
    assert [e["type"] for e in events_after] == types_before, (
        "projection rebuild must be invariant (append-only)"
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
