"""IF-MUTATION-002 mutation experiment facade (T-011 RED unit).

Pins the mutation experiment contract from interfaces.md §1g *before* the
GREEN implementation lands in ``tracks/executor/mutation_experiment.py``:

- ``run_mutation_experiment`` executes an isolated mutation experiment:
  creates a worktree, applies the patch, runs target/control nodes, and
  rolls back cleanly (AC-FR0263-01: target kill + controls green).
- The function returns a ``MutationExperimentResult`` with status, baseline,
  apply, target_kill, controls, rollback, and blocked_reason.

Each assertion fails today because ``run_mutation_experiment`` is a
``NotImplementedError("IF-MUTATION-002")`` stub in
``tracks/executor/mutation.py`` -- the M-IMPL RED on the contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tracks.adapters.base import TestRunResult
from tracks.executor.mutation import (
    MutationExperimentResult,
    MutationManifest,
    run_mutation_experiment,
)

# AC-FR0263-01@v0.7 TRACKS-TRACE experiment chain: target kill + controls green

def test_run_mutation_experiment_returns_result():
    """run_mutation_experiment returns a MutationExperimentResult."""
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-FR0263-01@v0.7",
        if_ref="IF-MUTATION-002",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=("t1",),
        control_nodes=("c1",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/",),
        expected_result={"target": "killed", "controls": "green"},
    )

    def _open_worktree(name: str) -> Path:
        return Path("/tmp/test_worktree")

    def _apply_patch(worktree: Path, patch: str) -> str:
        return "applied"

    def _run_nodes(worktree: Path, nodes: Sequence[str]) -> Mapping[str, TestRunResult]:
        return {}

    def _close_worktree(worktree: Path) -> None:
        pass

    result = run_mutation_experiment(
        manifest=manifest,
        repo=Path("/tmp/test_repo"),
        open_worktree=_open_worktree,
        apply_patch=_apply_patch,
        run_nodes=_run_nodes,
        close_worktree=_close_worktree,
    )
    assert isinstance(result, MutationExperimentResult)


def test_run_mutation_experiment_status_is_passed_or_blocked():
    """run_mutation_experiment returns status passed or blocked."""
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-FR0263-01@v0.7",
        if_ref="IF-MUTATION-002",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=("t1",),
        control_nodes=("c1",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/",),
        expected_result={"target": "killed", "controls": "green"},
    )

    def _open_worktree(name: str) -> Path:
        return Path("/tmp/test_worktree")

    def _apply_patch(worktree: Path, patch: str) -> str:
        return "applied"

    def _run_nodes(worktree: Path, nodes: Sequence[str]) -> Mapping[str, TestRunResult]:
        return {}

    def _close_worktree(worktree: Path) -> None:
        pass

    result = run_mutation_experiment(
        manifest=manifest,
        repo=Path("/tmp/test_repo"),
        open_worktree=_open_worktree,
        apply_patch=_apply_patch,
        run_nodes=_run_nodes,
        close_worktree=_close_worktree,
    )
    assert result.status in ("passed", "blocked")


# AC-FR0263-04@v0.7 TRACKS-TRACE failure matrix fail-closed

def test_run_mutation_experiment_blocks_on_unclean_rollback():
    """run_mutation_experiment blocks when rollback is not clean."""
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-FR0263-01@v0.7",
        if_ref="IF-MUTATION-002",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=("t1",),
        control_nodes=("c1",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/",),
        expected_result={"target": "killed", "controls": "green"},
    )

    def _open_worktree(name: str) -> Path:
        return Path("/tmp/test_worktree")

    def _apply_patch(worktree: Path, patch: str) -> str:
        return "applied"

    def _run_nodes(worktree: Path, nodes: Sequence[str]) -> Mapping[str, TestRunResult]:
        return {}

    def _close_worktree(worktree: Path) -> None:
        raise RuntimeError("rollback failed")

    with pytest.raises(ValueError, match="rollback"):
        run_mutation_experiment(
            manifest=manifest,
            repo=Path("/tmp/test_repo"),
            open_worktree=_open_worktree,
            apply_patch=_apply_patch,
            run_nodes=_run_nodes,
            close_worktree=_close_worktree,
        )


# AC-FR0261-03 / AC-FR0263-03 (IF-MUTATION-002) TRACKS-TRACE
# tests-in-scope is rejected at the mutation experiment entry.

def test_run_mutation_experiment_blocks_tests_in_scope():
    """run_mutation_experiment must fail-closed when allowed_change_scope
    contains a tests/ path.

    A mutation against test assets is forbidden (AC-FR0262-03/AC-FR0261-03):
    even when target/control nodes would otherwise all pass, the experiment
    entry must BLOCK with ``blocked_reason==\"tests_in_scope\"`` instead of
    producing a passed/green verdict.  This mirrors the MutationBlockReason
    ``tests_in_scope`` (interfaces.md §1a).
    """
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-FR0261-01@v0.7",
        if_ref="IF-MUTATION-002",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=("t1",),
        control_nodes=("c1",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tests/unit/test_mutation_experiment.py",),
        expected_result={"target": "killed", "controls": "green"},
    )

    def _open_worktree(name: str) -> Path:
        return Path("/tmp/test_worktree")

    def _apply_patch(worktree: Path, patch: str) -> str:
        return "applied"

    def _run_nodes(
        worktree: Path, nodes: Sequence[str]
    ) -> Mapping[str, TestRunResult]:
        # If the guard is missing, the experiment would otherwise pass:
        # target is killed and control is green.  That is exactly the leak
        # the tests-scope block must prevent.
        return {
            "t1": TestRunResult(node_id="t1", status="failed", detail=None),
            "c1": TestRunResult(node_id="c1", status="passed", detail=None),
        }

    def _close_worktree(worktree: Path) -> None:
        pass

    result = run_mutation_experiment(
        manifest=manifest,
        repo=Path("/tmp/test_repo"),
        open_worktree=_open_worktree,
        apply_patch=_apply_patch,
        run_nodes=_run_nodes,
        close_worktree=_close_worktree,
    )
    assert result.status == "blocked"
    assert result.blocked_reason == "tests_in_scope"

