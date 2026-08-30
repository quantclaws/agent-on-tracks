"""Integration: mutation experiment isolated worktree (IF-MUTATION-002).

AC-FR0263-01@v0.7 experiment chain target kill controls green,
AC-FR0263-02@v0.7 events append-only replayable,
AC-FR0263-03@v0.7 crash recovery reruns missing, no phantom pass,
AC-FR0263-04@v0.7 failure matrix fail-closed.

Assertions land on `run_mutation_experiment` (IF-MUTATION-002, §1g) public outlets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.adapters.base import TestRunResult
from tracks.executor.mutation import (
    MutationExperimentResult,
    MutationManifest,
    run_mutation_experiment,
)

pytestmark = pytest.mark.integration



def _manifest(ac="AC-FR0263-01@v0.7", scope=("tracks/...",)) -> MutationManifest:
    return MutationManifest(
        protocol_version=1,
        ac=ac,
        if_ref="IF-MUTATION-002",
        candidate_digest="sha256:candidate",
        patch_digest="sha256:patch",
        target_nodes=("node-target",),
        control_nodes=("node-control",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=scope,
        expected_result={"target": "killed", "controls": "green"},
    )


# AC-FR0263-01@v0.7 TRACKS-TRACE experiment chain target kill controls green
def test_experiment_chain_target_kill_controls_green():
    """AC-FR0263-01: baseline/apply/target/control/rollback all satisfied."""

    def _open(_):
        return Path("/tmp/wt")

    def _apply(_repo, _patch):
        return "identity_match"

    def _run(_repo, nodes):
        # Target killed, controls green.
        return {
            "node-target": TestRunResult("node-target", "failed", "killed"),
            "node-control": TestRunResult("node-control", "passed", None),
        }

    def _close(_):
        return None

    result = run_mutation_experiment(
        manifest=_manifest(),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply,
        run_nodes=_run,
        close_worktree=_close,
    )
    assert isinstance(result, MutationExperimentResult)
    assert result.status == "passed"
    assert result.baseline == "verified"
    assert result.apply == "identity_match"
    assert result.target_kill == "verified"
    assert result.controls == "green"
    assert result.rollback == "clean"


# AC-FR0263-02@v0.7 TRACKS-TRACE events append-only replayable
def test_events_append_only_replayable():
    """AC-FR0263-02: experiment result carries command_echo/digests for replay."""
    calls = []

    def _open(_):
        return Path("/tmp/wt")

    def _apply(_repo, _patch):
        return "identity_match"

    def _run(_repo, nodes):
        calls.append(list(nodes))
        return {n: TestRunResult(n, "failed", "k") for n in nodes}

    def _close(_):
        return None

    result = run_mutation_experiment(
        manifest=_manifest(ac="AC-FR0263-02@v0.7"),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply,
        run_nodes=_run,
        close_worktree=_close,
    )
    # The result must expose replayable evidence fields (node_results_ref).
    assert hasattr(result, "node_results_ref")
    # Append-only: re-running with identical inputs must not mutate prior result.
    calls.clear()
    result2 = run_mutation_experiment(
        manifest=_manifest(ac="AC-FR0263-02@v0.7"),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply,
        run_nodes=_run,
        close_worktree=_close,
    )
    assert result.status == result2.status


# AC-FR0263-03@v0.7 TRACKS-TRACE crash recovery reruns missing, no phantom pass
def test_crash_recovery_reruns_no_phantom_pass():
    """AC-FR0263-03: a missing persisted result must never be a passed status."""
    # Model a crash: run_nodes raises (no persisted result). The experiment
    # must NOT report status=passed when results are missing.
    def _raise(_repo, _nodes):
        raise RuntimeError("crash: no persisted result")

    def _open(_):
        return Path("/tmp/wt")

    def _apply(_repo, _patch):
        return "identity_match"

    def _close(_):
        return None

    try:
        result = run_mutation_experiment(
            manifest=_manifest(ac="AC-FR0263-03@v0.7"),
            repo=Path("/tmp/repo"),
            open_worktree=_open,
            apply_patch=_apply,
            run_nodes=_raise,
            close_worktree=_close,
        )
    except NotImplementedError:
        # Legal Red anchor: the WAL replay path is not implemented (stub token).
        # A phantom pass is forbidden; the missing-result re-run must surface.
        raise AssertionError(
            "WAL replay must re-run missing experiments (IF-MUTATION-002 not implemented)"
        ) from None
    assert isinstance(result, MutationExperimentResult)
    assert result.status != "passed", (
        "missing persisted result must never be reported as passed (no phantom)"
    )


# AC-FR0263-04@v0.7 TRACKS-TRACE failure matrix fail-closed
def test_failure_matrix_fail_closed():
    """AC-FR0263-04: target_survived / control_hit / stale_patch all blocked."""

    def _open(_):
        return Path("/tmp/wt")

    def _close(_):
        return None

    # (a) target survived: target still green -> blocked target_survived.
    def _run_target_survived(_repo, nodes):
        return {n: TestRunResult(n, "passed", None) for n in nodes}

    def _apply_ok(_repo, _patch):
        return "identity_match"

    r1 = run_mutation_experiment(
        manifest=_manifest(ac="AC-FR0263-04a@v0.7"),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply_ok,
        run_nodes=_run_target_survived,
        close_worktree=_close,
    )
    assert r1.status == "blocked"
    assert r1.blocked_reason == "target_survived"

    # (b) control hit: control node failed -> blocked control_hit.
    def _run_control_hit(_repo, nodes):
        return {n: TestRunResult(n, "failed", "hit") for n in nodes}

    r2 = run_mutation_experiment(
        manifest=_manifest(ac="AC-FR0263-04b@v0.7"),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply_ok,
        run_nodes=_run_control_hit,
        close_worktree=_close,
    )
    assert r2.status == "blocked"
    assert r2.blocked_reason == "control_hit"

    # (c) stale patch: apply identity mismatch -> blocked stale_patch.
    def _apply_stale(_repo, _patch):
        return "mismatch"

    def _run_ok(_repo, nodes):
        return {
            "node-target": TestRunResult("node-target", "failed", "k"),
            "node-control": TestRunResult("node-control", "passed", None),
        }

    r3 = run_mutation_experiment(
        manifest=_manifest(ac="AC-FR0263-04c@v0.7"),
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply_stale,
        run_nodes=_run_ok,
        close_worktree=_close,
    )
    assert r3.status == "blocked"
    assert r3.blocked_reason == "stale_patch"
