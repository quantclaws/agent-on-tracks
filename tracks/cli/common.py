"""Shared ``trac`` CLI shell primitives.

Extracted from :mod:`tracks.cli.main` for module-size compliance (C0302):
the single-writer lock, stderr error exits, the shared state-line format,
the host gitignore scaffold helpers, actor resolution, and the canonical
stage-order single source. ``tracks.cli.main`` re-exports every name
below, so the pre-split import surface is unchanged.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

from tracks import paths
from tracks.executor import git


class LockHeld(Exception):
    def __init__(self, pid: str):
        super().__init__(pid)
        self.pid = pid


def _err(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def _err2(msg: str) -> int:
    """Error exit with code 2 (release preview surface: no release run)."""
    print(msg, file=sys.stderr)
    return 2


def _format_state(state) -> str:
    """Shared one-line state format for run/status/replay (Fix 3: escalation
    reason visibility). When awaiting escalation, append the attempt count,
    failure check, and one-line reason so the operator sees why the run halted."""
    line = (
        f"stage={state.stage} substate={state.substate} "
        f"status={state.status} awaiting={state.awaiting or '-'}"
    )
    if state.awaiting == "escalation":
        line += f" attempts={state.current_attempt}"
        if state.last_failure:
            check = state.last_failure.get("check") or "?"
            reason = state.last_failure.get("reason") or ""
            reason = reason.strip().splitlines()[0] if reason else ""
            snippet = f"[{check}] {reason}" if reason else f"[{check}]"
            line += f" reason={snippet}"
    return line


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def writer_lock(home: Path):
    lock = paths.lock_path(home)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    for retry in (True, False):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                holder = lock.read_text(encoding="utf-8").strip() or "unknown"
            except FileNotFoundError:
                continue  # holder just released; retry acquisition
            # Crash recovery (AC-29b): a dead holder's lock is stale.
            if retry and holder.isdigit() and not _pid_alive(int(holder)):
                lock.unlink(missing_ok=True)
                continue
            raise LockHeld(holder) from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


RUNTIME_GITIGNORE = "tracks.db*\nblobs/\nlock\nlog/\nprompts/\n"
PROJECTS_GITIGNORE = "*.lock\n*.tmp\n"
# .tracks/-level transient artifacts (report output, discuss locks).
TRACKS_GITIGNORE = "report/\n*.lock\n"
# Host-tree byproducts of the runtime's own contract commands: the
# collect/run test executions materialize __pycache__/ and .pytest_cache/ in
# the host repo (under tests/ and at the root). Same doctrine as the .tracks
# gitignores above -- runtime-owned transients must be ignored in every
# stage or they pollute host git status; the B59 freeze gate has
# false-positived on exactly these since it landed (944c0c0, 2026-08-25:
# every e2e fake journey died at test_freeze_contamination). Managed as a
# marked block in the HOST ROOT .gitignore: appended only, never rewritten,
# idempotent by marker, committed with the scaffold so `trac start`'s
# clean-tree gate sees a committed host.
HOST_BYPRODUCTS_MARKER = "# BEGIN tracks-managed (runtime test-command byproducts)"
HOST_BYPRODUCTS_GITIGNORE = (
    HOST_BYPRODUCTS_MARKER
    + "\n__pycache__/\n*.py[cod]\n.pytest_cache/\n# END tracks-managed\n"
)


def _ensure_host_byproducts_gitignore(repo: Path) -> bool:
    """Idempotently append the managed byproduct block to the host root
    .gitignore. Returns True when the file changed (caller commits it)."""
    gitignore = repo / ".gitignore"
    current = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if HOST_BYPRODUCTS_MARKER in current:
        return False
    sep = "" if not current or current.endswith("\n") else "\n"
    gitignore.write_text(current + sep + HOST_BYPRODUCTS_GITIGNORE, encoding="utf-8")
    return True


def _human_actor(repo: Path) -> str:
    return git(repo, "config", "user.name", check=False).stdout.strip() or "Human"


def _resolve_actor(repo: Path, actor: str | None) -> str:
    """The approving/anchoring actor name: the CLI --actor value when given,
    else the git user.name when configured, else the generic 'human'."""
    return actor or git(repo, "config", "user.name", check=False).stdout.strip() or "human"


def _canonical_stage_order() -> tuple[str, ...]:
    """IF-RELEASE-003 / FR-0274/FR-0287: the canonical stage order single
    source — tuple(machine._STAGES) minus M-REQ-APPROVAL plus
    release.RELEASE_STAGES. Upstream = a smaller ordinal."""
    from tracks.kernel import machine as _kernel_machine
    from tracks.kernel import release as _kernel_release

    return (
        tuple(s for s in _kernel_machine._STAGES if s != "M-REQ-APPROVAL")
        + tuple(_kernel_release.RELEASE_STAGES)
    )
