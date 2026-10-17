"""Discussion gate (FR-100, AC-1001..1003).

``check_ready`` answers whether a document's inline discussion is settled: ready
iff every thread is resolved (open and reopen both block). Pure read; backs
``trac discuss query --check-ready`` and the ``discussion_ready`` validate check.

``adjudication_owner`` implements the FR-0314.4 ownership determination: a
thread whose root comment explicitly asks a specific party (@Human or a named
reviewer) to adjudicate is resolved only by that party. Both the discuss CLI
(``set-status resolved``) and the service-side web entry reuse it (lazy import).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from tracks.discuss.model import speaker_key
from tracks.discuss.parser import parse_threads

_HUMAN_FALLBACK = "Human"

# Human-identity cache: resolved start-dir string -> display name. The auth
# table is read-only and the binding rarely changes; a stale entry only
# matters across a rename, which restarts the process.
_HUMAN_IDENTITY_CACHE: dict[str, str] = {}


def check_ready(text: str) -> tuple:
    """Return ``(is_ready, ready_blockers)``; blockers are non-resolved thread_ids."""
    blockers = tuple(t.thread_id for t in parse_threads(text) if t.status != "resolved")
    return not blockers, blockers


def adjudication_owner(text: str, thread_id: str) -> str | None:
    """The party the thread's root explicitly asks to adjudicate, or None.

    Only the root comment's @mentions create an adjudication request
    (interfaces §1f.4); reply-level mentions never do. With zero or several
    root mentions there is no single bounded party, so the existing operator
    rules apply unchanged.
    """
    thread = next((t for t in parse_threads(text) if t.thread_id == thread_id), None)
    if thread is None:
        return None
    mentions = tuple(dict.fromkeys(thread.root.mentions))
    if len(mentions) == 1:
        return mentions[0]
    return None


def thread_initiator(text: str, thread_id: str) -> str | None:
    """The thread's root speaker (#210: the resolved-rule disjunction needs
    both the initiator and the adjudication owner)."""
    thread = next((t for t in parse_threads(text) if t.thread_id == thread_id), None)
    return thread.initiator if thread is not None else None


def _service_db_candidates(repo_or_cwd) -> list:
    """Candidate service.db paths holding the auth table (read-only).

    The serve home is ``<repo>/.tracks/service`` by default (``--home`` /
    ``TRAC_SERVE_HOME`` override it); ``TRACKS_HOME`` overrides the
    ``.tracks`` root instead. From a repo path (or any file/dir inside it)
    the walk-up covers subdirectories; a bare ``service.db`` candidate
    covers callers already rooted at the serve home.
    """
    candidates: list = []
    env_home = os.environ.get("TRAC_SERVE_HOME")
    if env_home:
        candidates.append(Path(env_home).expanduser() / "service.db")
    tracks_home_env = os.environ.get("TRACKS_HOME")
    if tracks_home_env:
        base = Path(tracks_home_env).expanduser()
        candidates.append(base / "service" / "service.db")
        candidates.append(base / "service.db")
    start = Path(repo_or_cwd).expanduser() if repo_or_cwd is not None else Path.cwd()
    try:
        cur = start.resolve()
    except OSError:
        cur = start
    if cur.is_file():
        cur = cur.parent
    for _ in range(8):
        candidates.append(cur / ".tracks" / "service" / "service.db")
        candidates.append(cur / "service.db")
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    seen: set = set()
    out: list = []
    for cand in candidates:
        key = str(cand)
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _read_auth_display_name(db_path: Path) -> str | None:
    """The auth row's display_name, or None (missing/unreadable/blank).

    Read-only open; a pre-§1c-evolution table without the display_name
    column reads as "not collected" (the startup migration owns the column).
    """
    try:
        if not db_path.exists():
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        try:
            row = conn.execute("SELECT display_name FROM auth LIMIT 1").fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        name = row[0]
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None
    finally:
        conn.close()


def _git_user_name(cwd) -> str | None:
    """``git config user.name`` in ``cwd`` (global config applies outside a
    repo), or None when git is unavailable or the key is unset."""
    try:
        if cwd is not None and not Path(cwd).exists():
            return None
        proc = subprocess.run(
            ["git", "config", "user.name"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def human_identity(repo_or_cwd=None) -> str:
    """The effective Human display name for ``repo_or_cwd``.

    Priority: auth-table display_name (service.db, read-only) -> ``git
    config user.name`` -> ``"Human"``. Lightly cached per resolved start
    path (the binding rarely changes; a rename restarts the process).
    """
    if repo_or_cwd is not None:
        try:
            cache_key = str(Path(repo_or_cwd).expanduser().resolve())
        except OSError:
            cache_key = str(repo_or_cwd)
    else:
        try:
            cache_key = str(Path.cwd().resolve())
        except OSError:
            cache_key = "cwd"
    cached = _HUMAN_IDENTITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    for db_path in _service_db_candidates(repo_or_cwd):
        name = _read_auth_display_name(db_path)
        if name:
            _HUMAN_IDENTITY_CACHE[cache_key] = name
            return name
    if repo_or_cwd is not None:
        git_cwd = Path(repo_or_cwd).expanduser()
        if git_cwd.is_file():
            git_cwd = git_cwd.parent
    else:
        git_cwd = Path.cwd()
    result = _git_user_name(git_cwd) or _HUMAN_FALLBACK
    _HUMAN_IDENTITY_CACHE[cache_key] = result
    return result


def is_human_owner(owner: str | None, repo_or_cwd=None) -> bool:
    """Whether ``owner`` names the Human (display name or legacy "Human").

    Compared through ``speaker_key`` (the same normalized identity the
    discuss writer uses). The literal ``"human"`` always counts (backward
    compatibility for @Human mentions predating name collection).
    """
    if not isinstance(owner, str) or not owner.strip():
        return False
    if speaker_key(owner) == speaker_key(_HUMAN_FALLBACK):
        return True
    try:
        return speaker_key(owner) == speaker_key(human_identity(repo_or_cwd))
    except Exception:
        return False


def human_keys(repo_or_cwd=None) -> set:
    """The ``speaker_key`` set that counts as the Human at ``repo_or_cwd``.

    The collected display name plus the legacy ``"human"`` alias, so a
    renamed binding never deadlocks a Human-adjudicated thread.
    """
    try:
        name = human_identity(repo_or_cwd)
    except Exception:
        name = _HUMAN_FALLBACK
    return {speaker_key(name), speaker_key(_HUMAN_FALLBACK)}
