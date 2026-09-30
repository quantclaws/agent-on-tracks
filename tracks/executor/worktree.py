"""Three-worktree scheme (FR-0070, IF-IMPL-006).

Devon candidate worktree (from C_design, naturally excludes Shield tests),
test-authority worktree (Shield freezes tests as frozen bundle), and gate
worktree (combines C_design + frozen bundle + Devon candidate diff). Frozen
bundle is never merged into Devon candidate (BS-04 temporal isolation).
"""

from __future__ import annotations

import contextlib
import os
import posixpath
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tracks.executor.host_contract import declared_install_interpreter


@dataclass(frozen=True)
class WorktreeHandle:
    path: str
    base_sha: str
    kind: Literal["devon_candidate", "gate", "test_authority"]


def _writer_worktree_path(repo: str, run_id: str, task_id: str | None, kind: str) -> str:
    """B1 (issue #2): the deterministic path a writer worktree occupies
    (mirrors ``_worktree_path`` for pre-creation stale-path reclamation)."""
    return _worktree_path(repo, run_id, task_id, kind)


def _iter_scope_paths(repo: str, scope_paths: list[str]):
    """Yield in-scope repo-relative paths: tracked-changed vs HEAD plus
    untracked. ``scope_paths`` may name files or directories (git pathspec)."""
    if not scope_paths:
        return
    changed = _git(repo, "diff", "HEAD", "--name-only", "--", *scope_paths)
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "--", *scope_paths)
    seen: set[str] = set()
    for rel in (changed + "\n" + untracked).splitlines():
        rel = rel.strip()
        if rel and rel not in seen:
            seen.add(rel)
            yield rel


def seed_worktree_with_cycle_wip(repo: str, worktree_path: str, scope_paths: list[str]) -> int:
    """Seed a fresh writer worktree with the cycle's accumulated WIP.

    OOB 2026-09-06 (run 01M19FJVES7G113RD8QXXY3PQZ): GREEN work that fails its
    gate stays UNCOMMITTED in the main tree (no G commit), so a re-dispatch's
    worktree -- freshly checked out from HEAD -- cannot see it. On a large
    task Devon then re-derives the same wiring every round while the anchors
    never shrink (one 2h20m round produced zero net change). Seeding copies
    the in-scope current main-tree content into the worktree so the writer
    resumes from the accumulated cycle state, never from clean HEAD. Scope
    restriction keeps operator edits / runtime state out of the writer's
    view. Returns the number of seeded paths (0 = nothing to carry).
    """
    from pathlib import Path

    main = Path(repo)
    wt = Path(worktree_path)
    seeded = 0
    for rel in _iter_scope_paths(repo, scope_paths):
        src = main / rel
        dst = wt / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        elif dst.exists():
            dst.unlink()
        seeded += 1
    return seeded


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


def runtime_asset_paths(repo: str) -> tuple[str, ...]:
    """Resolve project-relative runtime assets from the host declaration."""
    interpreter = declared_install_interpreter(Path(repo))
    env_dir = posixpath.dirname(posixpath.dirname(interpreter))
    # Bare/absolute executables do not declare a project-relative environment.
    if (env_dir and not posixpath.isabs(env_dir)
            and not {".", ".."}.intersection(env_dir.split("/"))):
        return (".opencode", env_dir)
    return (".opencode",)


def ensure_runtime_assets(repo: str, wt_path: str) -> None:
    """Link gitignored runtime assets from the main repo into a worktree.

    Worktrees are clean ``git worktree add`` checkouts: gitignored deployment
    targets (notably ``.opencode/`` — the agents/skills the meta-tests
    ``test_devon_canonical_equals_deployed`` compare against) are structurally
    absent, so any gate unit command that touches them fails with
    FileNotFoundError (run 01KZTHE7 T-013). Symlink each asset back to the main
    repo's deployment so the worktree sees the same environment. Best-effort and
    idempotent: never raises, never clobbers an existing entry.

    B59 (#75): the contract's env directory joins the asset set. Agent-side
    guard/unit commands use the RELATIVE interpreter path the host contract
    declares (project.toml contract + manifest guard_commands); without the
    symlink that path only resolves in the main repo, so an agent verifying
    in its worktree falls back to running commands in the MAIN tree —
    measuring the contaminated main-repo state instead of the candidate
    worktree (run 01M0S0FQ T-001: attempt 2 reported "10 passed" from the
    main tree while 6 loader tests legitimately failed in the clean
    worktree). Import safety: running the main env's interpreter with
    cwd=worktree keeps imports correct — ``python -m`` prepends the cwd to
    sys.path, so the worktree's ``tracks`` package shadows the main repo's
    editable install; the shared environment only supplies third-party
    dependencies.
    """
    for name in runtime_asset_paths(repo):
        src = os.path.join(repo, name)
        dst = os.path.join(wt_path, name)
        if os.path.lexists(dst):
            continue
        if not (os.path.exists(src) or os.path.islink(src)):
            continue
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
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

    Gate-worktree assembly bypasses the repo's pre-commit hooks on BOTH the
    candidate commit and the frozen-bundle cherry-pick (Prism B02 fix): the
    gate is a transient evaluation surface (never enters git history), and
    the repo-global hooksPath hard-requires ROOT/.venv which a fresh gate
    worktree structurally lacks — a hooked commit fails deterministically
    and (pre-fix) the bare except in _ensure_gate_worktree silently degraded
    the evaluation to the main repo's placeholder (T-002's lost GREEN).
    """
    path = _worktree_path(repo, run_id, task_id, "gate")
    _git(repo, "worktree", "add", "--detach", path, c_design_sha)
    if devon_diff.strip():
        _apply_and_commit(path, devon_diff, "Devon candidate diff", no_verify=True)
    if frozen_bundle_sha.strip():
        # Bypass the repo-global hooksPath for the cherry-pick the same way:
        # point hooksPath at an empty dir local to the worktree.
        _git(
            path, "config", "core.hooksPath", str(Path(path) / ".tracks" / ".no-hooks"),
        )
        _ensure_empty_hooks_dir(path)
        _git(path, "cherry-pick", "--allow-empty", frozen_bundle_sha)
    # Runtime assets are linked AFTER all git commits so _apply_and_commit's
    # `git add -A` cannot swallow the symlink into a commit; the entry stays
    # untracked (and ignored under the canonical .gitignore).
    ensure_runtime_assets(repo, path)
    return WorktreeHandle(path=path, base_sha=c_design_sha, kind="gate")


def _ensure_empty_hooks_dir(worktree_path: str) -> None:
    """Create the empty hooks dir the cherry-pick hooksPath override points at."""
    d = Path(worktree_path) / ".tracks" / ".no-hooks"
    d.mkdir(parents=True, exist_ok=True)


def cleanup_worktree(
    handle: WorktreeHandle,
) -> bool:
    """FR-0070 cleanup worktree after phase (git worktree remove).

    Returns True=cleaned successfully, False=cleanup failed (emit error event,
    does not corrupt existing worktree).

    #212 OOB (2026-09-29): kill any agent processes still running in this
    worktree BEFORE removing the directory — the old cleanup leaked every
    resident opencode child as an orphan (PPID=1) that accumulated across
    task cycles and eventually blocked subsequent dispatches (live: 6 stale
    Sage processes ground T-009 PRISM_FINAL to a halt)."""
    path = handle.path
    if not os.path.exists(path):
        return True
    _kill_worktree_agents(path)
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


def _apply_and_commit(wt_path: str, diff_text: str, message: str, no_verify: bool = False) -> None:
    fd, diff_path = tempfile.mkstemp(suffix=".diff")
    with os.fdopen(fd, "w") as fh:
        fh.write(diff_text)
    commit_argv = ["git", "-C", wt_path, "commit", "-m", message, "--allow-empty"]
    if no_verify:
        commit_argv.insert(-1, "--no-verify")
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
            commit_argv,
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


def _process_cwd(pid: int) -> str | None:
    """A process's working directory via ``lsof`` (macOS/Linux portable;
    Prism F-1: the worktree is the Popen *cwd*, never the ``--dir`` argv).
    Returns None when lsof is unavailable or the process is gone."""
    try:
        proc = subprocess.run(
            ["lsof", "-p", str(pid), "-Fn", "-w"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("n/") and os.path.isdir(line[1:]):
            return os.path.realpath(line[1:])
    return None


def _kill_worktree_agents(path: str) -> list[int]:
    """#212: terminate opencode processes whose *working directory* is inside
    this worktree (SIGTERM → brief wait → SIGKILL). The worktree is the
    Popen cwd, not a cmdline argument (Prism F-1) — lsof resolves each
    candidate's cwd. Best-effort: never raises, returns the PIDs signaled."""
    marked = os.path.realpath(path)
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid,command"], capture_output=True, text=True, check=False
        )
    except OSError:
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) < 2 or "opencode run --agent" not in parts[1]:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        cwd = _process_cwd(pid)
        if cwd is not None and (cwd == marked or cwd.startswith(marked + os.sep)):
            pids.append(pid)
    _terminate_groups(pids)
    return pids


def sweep_orphan_agents() -> list[int]:
    """#212: terminate orphaned opencode agent processes (PPID=1) whose
    working directory no longer exists (Prism F-1: the worktree is the Popen
    cwd, not a cmdline arg — resolve each candidate's cwd via lsof, never
    parse --dir from the command line). Called at drive startup alongside
    the worktree sweep; production environments accumulate orphans across
    crash/restart cycles (linear growth per task cycle)."""
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid,ppid,command"], capture_output=True, text=True, check=False
        )
    except OSError:
        return []
    killed: list[int] = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or "opencode run --agent" not in parts[2]:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if ppid != 1 or pid == os.getpid():
            continue
        cwd = _process_cwd(pid)
        # An orphan whose cwd is gone (worktree deleted) or unreadable
        # (lsof permission failure on a zombie-adjacent process) — both
        # are unrecoverable residents, safe to reclaim.
        if cwd is None or not os.path.exists(cwd):
            killed.append(pid)
    _terminate_groups(killed)
    return killed


def _terminate_groups(pids: list[int]) -> None:
    """SIGTERM each PID's process group, wait briefly, then SIGKILL any
    survivors (#212)."""
    import signal as _signal
    import time as _time

    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, _signal.SIGTERM)
    if not pids:
        return
    _time.sleep(1.0)
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, _signal.SIGKILL)
