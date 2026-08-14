"""Three-worktree scheme (FR-0070, IF-IMPL-006).

Devon candidate worktree (from C_design, naturally excludes Shield tests),
test-authority worktree (Shield freezes tests as frozen bundle), and gate
worktree (combines C_design + frozen bundle + Devon candidate diff). Frozen
bundle is never merged into Devon candidate (BS-04 temporal isolation).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class WorktreeHandle:
    path: str
    base_sha: str
    kind: Literal["devon_candidate", "gate", "test_authority"]


def create_devon_worktree(
    repo: str,
    c_design_sha: str,
    run_id: str,
    task_id: str,
) -> WorktreeHandle:
    """FR-0070 create Devon candidate worktree (from C_design).

    Devon runs in this worktree, naturally excluding Shield WRITE-generated
    tests (BS-04 temporal isolation).
    git worktree add <path> <c_design_sha>.
    """
    path = _worktree_path(repo, run_id, task_id, "devon")
    _git(repo, "worktree", "add", "--detach", path, c_design_sha)
    return WorktreeHandle(path=path, base_sha=c_design_sha, kind="devon_candidate")


def create_test_authority_worktree(
    repo: str,
    c_design_sha: str,
    run_id: str,
) -> WorktreeHandle:
    """FR-0070 create test-authority worktree.

    Shield freezes tests (frozen bundle) in this worktree, independent commit.
    """
    path = _worktree_path(repo, run_id, None, "test_authority")
    _git(repo, "worktree", "add", "--detach", path, c_design_sha)
    return WorktreeHandle(path=path, base_sha=c_design_sha, kind="test_authority")


def create_gate_worktree(
    repo: str,
    c_design_sha: str,
    frozen_bundle_sha: str,
    devon_diff: str,
    run_id: str,
    task_id: str,
) -> WorktreeHandle:
    """FR-0070/FR-0110 create gate worktree (combine C_design + frozen bundle +
    Devon candidate diff).

    First git worktree add from C_design, then checkout Devon candidate product
    code diff, finally cherry-pick or merge frozen test commit.
    Frozen bundle is never merged into Devon candidate (BS-04).
    """
    path = _worktree_path(repo, run_id, task_id, "gate")
    _git(repo, "worktree", "add", "--detach", path, c_design_sha)
    if devon_diff.strip():
        _apply_and_commit(path, devon_diff, "Devon candidate diff")
    if frozen_bundle_sha.strip():
        _git(path, "cherry-pick", "--allow-empty", frozen_bundle_sha)
    return WorktreeHandle(path=path, base_sha=c_design_sha, kind="gate")


def cleanup_worktree(
    handle: WorktreeHandle,
) -> bool:
    """FR-0070 cleanup worktree after phase (git worktree remove).

    Returns True=cleaned successfully, False=cleanup failed (emit error event,
    does not corrupt existing worktree).
    """
    path = handle.path
    if not os.path.exists(path):
        return True
    main = _main_repo(path)
    if main is not None and _same_path(path, main):
        return True
    removed = _remove_worktree(main, path)
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)
    if main is not None:
        subprocess.run(
            ["git", "-C", main, "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
        )
    _cleanup_empty_parents(path)
    return removed or not os.path.exists(path)


# -- helpers -----------------------------------------------------------------


def _worktree_path(repo: str, run_id: str, task_id: str | None, kind: str) -> str:
    parts = [repo, ".tracks", "worktrees", run_id]
    if task_id is not None:
        parts.append(task_id)
    parts.append(kind)
    parent = os.path.dirname(os.path.join(*parts))
    os.makedirs(parent, exist_ok=True)
    return os.path.join(*parts)


def _apply_and_commit(wt_path: str, diff_text: str, message: str) -> None:
    fd, diff_path = tempfile.mkstemp(suffix=".diff")
    with os.fdopen(fd, "w") as fh:
        fh.write(diff_text)
    try:
        subprocess.run(
            ["git", "-C", wt_path, "apply", "--whitespace=nowarn", diff_path],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", wt_path, "add", "-A"],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", wt_path, "commit", "-m", message, "--allow-empty"],
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(diff_path)


def _main_repo(path: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", path, "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    common = proc.stdout.strip()
    if not os.path.isabs(common):
        common = os.path.normpath(os.path.join(path, common))
    return os.path.dirname(common)


def _remove_worktree(main: str | None, path: str) -> bool:
    if main is None or not os.path.isdir(main):
        return False
    proc = subprocess.run(
        ["git", "-C", main, "worktree", "remove", "--force", path],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def _same_path(a: str, b: str) -> bool:
    return os.path.abspath(a) == os.path.abspath(b)


def _cleanup_empty_parents(path: str) -> None:
    parent = os.path.dirname(path)
    seen = {os.path.abspath(path)}
    while parent and parent not in seen:
        seen.add(os.path.abspath(parent))
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)


def _git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()
