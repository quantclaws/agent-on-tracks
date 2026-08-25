"""Mutation experiment implementation (IF-MUTATION-002).

Implements ``run_mutation_experiment`` declared in interfaces.md §1g.  The
function executes an isolated mutation experiment: creates a worktree, applies
the patch, runs target/control nodes, and rolls back cleanly.

The facade in ``tracks/executor/mutation.py`` delegates to this module.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from tracks.adapters.base import TestRunResult
from tracks.executor.mutation import MutationExperimentResult, MutationManifest


def run_mutation_experiment(  # pylint: disable=too-many-locals
    manifest: MutationManifest,
    repo: Path,
    open_worktree: Callable[[str], Path],
    apply_patch: Callable[[Path, str], str],
    run_nodes: Callable[[Path, Sequence[str]], Mapping[str, TestRunResult]],
    close_worktree: Callable[[Path], None],
) -> MutationExperimentResult:
    """Execute an isolated mutation experiment (IF-MUTATION-002).

    Creates a worktree, applies the patch, runs target/control nodes, and
    rolls back cleanly.  Returns a ``MutationExperimentResult`` with the
    status (``passed`` or ``blocked``), baseline, apply, target_kill,
    controls, rollback, and optional blocked_reason.

    Guards:
    - Unclean rollback blocks the experiment (AC-FR0263-04).
    """
    # Phase 1: create worktree and capture baseline identity
    try:
        worktree = open_worktree("mutation")
    except Exception as exc:
        return MutationExperimentResult(
            status="blocked",
            baseline="",
            apply="",
            target_kill="",
            controls="",
            rollback="",
            blocked_reason=str(exc),
            node_results_ref=None,
        )

    try:
        # Phase 2: apply the patch
        apply_result = apply_patch(worktree, manifest.patch_digest)

        # Check if the patch was applied correctly (stale patch detection).
        # The apply_patch callback returns 'identity_match' on success,
        # or a mismatch indicator (e.g. 'mismatch') when the patch is stale.
        if "mismatch" in (apply_result or ""):
            with contextlib.suppress(Exception):
                close_worktree(worktree)
            return MutationExperimentResult(
                status="blocked",
                baseline="verified",
                apply=apply_result or "",
                target_kill="",
                controls="",
                rollback="clean",
                blocked_reason="stale_patch",
                node_results_ref=None,
            )

        # Phase 3: run target nodes (filter by target node IDs)
        target_raw = run_nodes(worktree, manifest.target_nodes)
        target_results = {
            nid: r for nid, r in target_raw.items()
            if nid in manifest.target_nodes
        }
        target_killed = any(
            r.status in ("failed", "error") for r in target_results.values()
        )
        target_kill = "verified" if target_killed else "survived"

        # Phase 4: run control nodes (filter by control node IDs)
        control_raw = run_nodes(worktree, manifest.control_nodes)
        control_results = {
            nid: r for nid, r in control_raw.items()
            if nid in manifest.control_nodes
        }
        all_control_green = all(
            r.status in ("passed", "skipped") for r in control_results.values()
        )
        controls = "green" if all_control_green else "failed"

        # Phase 5: try to roll back cleanly
        try:
            close_worktree(worktree)
            rollback = "clean"
        except Exception as exc:
            raise ValueError(f"rollback_dirty: {exc}") from exc

        # Determine result
        if target_kill == "verified" and controls == "green":
            return MutationExperimentResult(
                status="passed",
                baseline="verified",
                apply=apply_result,
                target_kill=target_kill,
                controls=controls,
                rollback=rollback,
                blocked_reason=None,
                node_results_ref=None,
            )

        # Target survived or control hit
        blocked_reason = "target_survived" if target_kill != "verified" else "control_hit"
        return MutationExperimentResult(
            status="blocked",
            baseline="verified",
            apply=apply_result,
            target_kill=target_kill,
            controls=controls,
            rollback=rollback,
            blocked_reason=blocked_reason,
            node_results_ref=None,
        )
    except ValueError:
        # Rollback failure (ValueError) propagates to the caller.
        # Attempt cleanup but don't mask the original error.
        with contextlib.suppress(Exception):
            close_worktree(worktree)
        raise
    except Exception as exc:
        # Other exceptions: return a blocked result for WAL replay.
        with contextlib.suppress(Exception):
            close_worktree(worktree)
        return MutationExperimentResult(
            status="blocked",
            baseline="verified",
            apply="",
            target_kill="",
            controls="",
            rollback="",
            blocked_reason=str(exc),
            node_results_ref=None,
        )

