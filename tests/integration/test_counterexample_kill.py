"""Integration: AC-specific counterexample kill (IF-AUTH-002, IF-MUTATION-002, IF-PHASE-003).

AC-FR0261-01@v0.7 existing green requires kill verified,
AC-FR0261-02@v0.7 AC-specific binding and minimality reviewed,
AC-FR0261-03@v0.7 frozen tests untouchable, role separation.

Assertions land on `judge_authenticity` (IF-AUTH-002, interfaces §1f) and
`run_mutation_experiment` (IF-MUTATION-002, §1g) public outlets.
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


# AC-FR0261-01@v0.7 TRACKS-TRACE existing green requires kill verified
def test_existing_green_requires_kill_verified():
    """AC-FR0261-01: existing behaviour green requires counterexample_kill=verified."""
    # Existing AC: passes (green) but the counterexample experiment must kill it.
    outcome = TestRunResult("node-existing", "passed", None)
    # A missing counterexample experiment for an existing-green AC must block.
    missing = judge_authenticity(
        ac_ref="AC-FR0261-01@v0.7",
        category="existing",
        bound_nodes=("node-existing",),
        outcomes={"node-existing": outcome},
        legal_failure_kinds=set(),
        counterexample_experiment=None,
    )
    assert missing.counterexample_kill == "missing", (
        "existing green without counterexample must report kill=missing (blocked)"
    )
    # A verified counterexample experiment (target killed, controls green)
    # must yield counterexample_kill=verified.
    verified_experiment = {
        "target_kill": "verified",
        "controls": "green",
        "ac": "AC-FR0261-01@v0.7",
    }
    verified = judge_authenticity(
        ac_ref="AC-FR0261-01@v0.7",
        category="existing",
        bound_nodes=("node-existing",),
        outcomes={"node-existing": outcome},
        legal_failure_kinds=set(),
        counterexample_experiment=verified_experiment,
    )
    assert verified.counterexample_kill == "verified"
    assert verified.green_allowed is True


# AC-FR0261-02@v0.7 TRACKS-TRACE AC-specific binding and minimality reviewed
def test_ac_specific_binding_and_minimality_reviewed():
    """AC-FR0261-02: counterexample must bind a specific AC/IF (not broad)."""
    # An existing-green AC with a counterexample bound to a DIFFERENT AC must
    # be rejected (not verified): the kill must be AC-specific.
    cross_ac_experiment = {
        "target_kill": "verified",
        "controls": "green",
        "ac": "AC-FR0261-99@v0.7",  # bound to a different AC
    }
    outcome = TestRunResult("node-x", "passed", None)
    judgement = judge_authenticity(
        ac_ref="AC-FR0261-02@v0.7",
        category="existing",
        bound_nodes=("node-x",),
        outcomes={"node-x": outcome},
        legal_failure_kinds=set(),
        counterexample_experiment=cross_ac_experiment,
    )
    assert judgement.counterexample_kill != "verified", (
        "cross-AC counterexample must not be accepted as verified (minimality)"
    )


# AC-FR0261-03@v0.7 TRACKS-TRACE frozen tests untouchable, role separation
def test_frozen_tests_untouchable_role_separation():
    """AC-FR0261-03: a mutation whose scope touches tests/ is blocked.

    run_mutation_experiment must reject any manifest whose allowed_change_scope
    contains a tests/ path (role separation: Devon cannot mutate frozen tests).
    """
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-FR0261-03@v0.7",
        if_ref="IF-AUTH-002",
        candidate_digest="sha256:candidate",
        patch_digest="sha256:patch",
        target_nodes=("node-t",),
        control_nodes=("node-c",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/...", "tests/integration/frozen.py"),
        expected_result={"target": "killed", "controls": "green"},
    )

    def _open(_):
        return Path("/tmp/worktree")

    def _apply(_repo, _patch):
        return "identity_match"

    def _run(_repo, _nodes):
        return {n: TestRunResult(n, "passed", None) for n in _nodes}

    def _close(_):
        return None

    result = run_mutation_experiment(
        manifest=manifest,
        repo=Path("/tmp/repo"),
        open_worktree=_open,
        apply_patch=_apply,
        run_nodes=_run,
        close_worktree=_close,
    )
    assert isinstance(result, MutationExperimentResult)
    assert result.status == "blocked", (
        "mutation touching tests/ must be blocked (frozen tests untouchable)"
    )
    assert result.blocked_reason == "tests_in_scope"
