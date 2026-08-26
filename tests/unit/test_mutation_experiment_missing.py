"""IF-MUTATION-002 missing-result fail-closed (T-011 RED unit).

Pins the "missing results never pass" contract from AC-FR0263-03/04 and
AC-NFR0140-03 (never phantom pass; rerun missing, never pass):

- ``run_mutation_experiment`` must NEVER return ``status="passed"`` when a
  required node's result is missing from ``run_nodes`` output.
- A missing control node result must not be treated as vacuously ``green``
  (that is a phantom pass).
- Missing target/control results must be observable as a blocked experiment
  (or at minimum NOT ``passed``), so WAL/replay can rerun the missing node.

Each assertion fails today: the implementation silently filters out missing
node results and can return ``status="passed"`` with ``controls="green"``
when the control result is absent (vacuous truth over an empty set).
"""

from __future__ import annotations

from pathlib import Path

from tracks.adapters.base import TestRunResult
from tracks.executor.mutation import (
    MutationManifest,
    run_mutation_experiment,
)

# AC-FR0263-03@v0.7 / AC-NFR0140-03@v0.7 kill/mutation rebuild: never phantom pass

_MANIFEST = MutationManifest(
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


def _run(run_nodes):
    return run_mutation_experiment(
        manifest=_MANIFEST,
        repo=Path("/tmp/test_repo"),
        open_worktree=lambda name: Path("/tmp/test_worktree"),
        apply_patch=lambda worktree, patch: "applied",
        run_nodes=run_nodes,
        close_worktree=lambda worktree: None,
    )


def _target_killed_only() -> dict:
    return {"t1": TestRunResult(node_id="t1", status="failed", detail="")}


def _control_green_only() -> dict:
    return {"c1": TestRunResult(node_id="c1", status="passed", detail="")}


def test_missing_control_result_never_passes():
    """Target killed + control result MISSING must not report passed.

    The control node is required by the manifest; an absent result means the
    experiment did not actually run the control, so the controls verdict must
    not be vacuously green and the experiment must not pass.
    """
    result = _run(lambda worktree, nodes: _target_killed_only())
    assert result.status != "passed", (
        "phantom pass: control result missing but experiment passed "
        f"(controls={result.controls!r})"
    )
    assert result.controls != "green"


def test_missing_target_result_never_passes():
    """Target result MISSING must not report passed (blocked or rerun)."""
    result = _run(lambda worktree, nodes: _control_green_only())
    assert result.status != "passed", (
        "phantom pass: target result missing but experiment passed"
    )


def test_missing_any_result_blocks_with_observable_reason():
    """A missing node result yields blocked with a non-empty reason."""
    result = _run(lambda worktree, nodes: _target_killed_only())
    assert result.status in ("blocked",), (
        f"expected blocked for missing node result, got {result.status!r}"
    )


def test_partial_control_results_still_fail_closed():
    """Partial control results (one of several) must not pass.

    A single present green control among missing siblings must not be enough
    for a passed verdict while other required controls did not run.
    """
    manifest = MutationManifest(
        protocol_version=1,
        ac="AC-FR0263-01@v0.7",
        if_ref="IF-MUTATION-002",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=("t1",),
        control_nodes=("c1", "c2"),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/",),
        expected_result={"target": "killed", "controls": "green"},
    )

    def _run_nodes(worktree, nodes):
        return {
            "t1": TestRunResult(node_id="t1", status="failed", detail=""),
            "c1": TestRunResult(node_id="c1", status="passed", detail=""),
            # c2 missing
        }

    result = run_mutation_experiment(
        manifest=manifest,
        repo=Path("/tmp/test_repo"),
        open_worktree=lambda name: Path("/tmp/test_worktree"),
        apply_patch=lambda worktree, patch: "applied",
        run_nodes=_run_nodes,
        close_worktree=lambda worktree: None,
    )
    assert result.status != "passed", (
        "phantom pass: one control missing but experiment passed "
        f"(controls={result.controls!r})"
    )


def test_all_results_present_chain_passes_control():
    """Sanity anchor: with ALL results present the passed chain stays valid."""
    def _run_nodes(worktree, nodes):
        return {
            "t1": TestRunResult(node_id="t1", status="failed", detail=""),
            "c1": TestRunResult(node_id="c1", status="passed", detail=""),
        }

    result = _run(_run_nodes)
    assert result.status == "passed"
    assert result.target_kill == "verified"
    assert result.controls == "green"
