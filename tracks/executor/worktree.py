"""Three-worktree scheme (FR-0070, IF-IMPL-006).

Devon candidate worktree (from C_design, naturally excludes Shield tests),
test-authority worktree (Shield freezes tests as frozen bundle), and gate
worktree (combines C_design + frozen bundle + Devon candidate diff). Frozen
bundle is never merged into Devon candidate (BS-04 temporal isolation).

This module is a contract stub: signatures are frozen, bodies raise
NotImplementedError with the IF- token. Devon fills the implementation during
M-IMPL; the declaration contract must not change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class WorktreeHandle:
    path: str           # worktree mount path
    base_sha: str       # worktree base commit
    kind: Literal["devon_candidate", "gate", "test_authority"]


def create_devon_worktree(
    repo: str,
    c_design_sha: str,      # M-DESIGN pass common baseline
    run_id: str,
    task_id: str,
) -> WorktreeHandle:
    """FR-0070 create Devon candidate worktree (from C_design).

    Devon runs in this worktree, naturally excluding Shield WRITE-generated
    tests (BS-04 temporal isolation).
    git worktree add <path> <c_design_sha>.
    """
    raise NotImplementedError("IF-IMPL-006")


def create_test_authority_worktree(
    repo: str,
    c_design_sha: str,
    run_id: str,
) -> WorktreeHandle:
    """FR-0070 create test-authority worktree.

    Shield freezes tests (frozen bundle) in this worktree, independent commit.
    """
    raise NotImplementedError("IF-IMPL-006")


def create_gate_worktree(
    repo: str,
    c_design_sha: str,
    frozen_bundle_sha: str,  # test-authority worktree frozen test commit
    devon_diff: str,         # Devon candidate product code diff
    run_id: str,
    task_id: str,
) -> WorktreeHandle:
    """FR-0070/FR-0110 create gate worktree (combine C_design + frozen bundle +
    Devon candidate diff).

    First git worktree add from C_design, then checkout Devon candidate product
    code diff, finally cherry-pick or merge frozen test commit.
    Frozen bundle is never merged into Devon candidate (BS-04).
    """
    raise NotImplementedError("IF-IMPL-006")


def cleanup_worktree(
    handle: WorktreeHandle,
) -> bool:
    """FR-0070 cleanup worktree after phase (git worktree remove).

    Returns True=cleaned successfully, False=cleanup failed (emit error event,
    does not corrupt existing worktree).
    """
    raise NotImplementedError("IF-IMPL-006")
