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


def _writer_worktree_path(repo: str, run_id: str, task_id: str | None, kind: str) -> str:
    """B1 (issue #2): the deterministic path a writer worktree occupies
    (mirrors ``_worktree_path`` for pre-creation stale-path reclamation)."""
    return _worktree_path(repo, run_id, task_id, kind)


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


_RUNTIME_ASSETS = (".opencode",)


def ensure_runtime_assets(repo: str, wt_path: str) -> None:
    """Link gitignored runtime assets from the main repo into a worktree.

    Worktrees are clean ``git worktree add`` checkouts: gitignored deployment
    targets (notably ``.opencode/`` — the agents/skills the meta-tests
    ``test_devon_canonical_equals_deployed`` compare against) are structurally
    absent, so any gate unit command that touches them fails with
    FileNotFoundError (run 01KZTHE7 T-013). Symlink each asset back to the main
    repo's deployment so the worktree sees the same environment. Best-effort and
    idempotent: never raises, never clobbers an existing entry.
    """
    for name in _RUNTIME_ASSETS:
        src = os.path.join(repo, name)
        dst = os.path.join(wt_path, name)
        if os.path.lexists(dst):
            continue
        if not (os.path.exists(src) or os.path.islink(src)):
            continue
        with contextlib.suppress(OSError):
            os.symlink(os.path.abspath(src), dst)


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
    # Runtime assets are linked AFTER all git commits so _apply_and_commit's
    # `git add -A` cannot swallow the symlink into a commit; the entry stays
    # untracked (and ignored under the canonical .gitignore).
    ensure_runtime_assets(repo, path)
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


def sweep_worktrees(repo: str) -> list[str]:
    """B2 (run 01KZTHE7): remove every worktree/directory under .tracks/worktrees/.

    Wired into ``trac start`` ONLY (user ruling: no other call site) — opening a
    new run is the single sanctioned moment to reclaim worktrees leaked by a
    crashed loop or by the historical pre-existing-handle cleanup escape. The
    sweep is audited by the caller via a ``worktree.swept`` event; this function
    stays pure reclamation. Fail-open hygiene: never raises, never touches the
    main repo, returns the removed paths.
    """
    base = os.path.join(repo, ".tracks", "worktrees")
    if not os.path.isdir(base):
        return []
    main = _main_repo(base)
    removed = _remove_registered_worktrees(repo, base, main)
    shutil.rmtree(base, ignore_errors=True)
    if main is not None:
        subprocess.run(
            ["git", "-C", main, "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
        )
    if not os.path.isdir(base):
        removed.append(base)
    return removed


def _remove_registered_worktrees(repo: str, base: str, main: str | None) -> list[str]:
    """Force-remove every git-registered worktree under ``base`` (never the
    main repo); leftover directory shells are handled by the caller's rmtree."""
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if listing.returncode != 0:
        return []
    prefix = os.path.abspath(base) + os.sep
    removed: list[str] = []
    for line in listing.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        wt = line.removeprefix("worktree ")
        if not os.path.abspath(wt).startswith(prefix):
            continue
        if _same_path(wt, repo):
            continue  # never the main repo
        _remove_worktree(main, wt)
        if os.path.exists(wt):
            shutil.rmtree(wt, ignore_errors=True)
        removed.append(wt)
    return removed


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
