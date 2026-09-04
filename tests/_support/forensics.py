"""M2 forensic capture (convergence plan 2026-09-05).

Integration test failure forensics: when a test fails AND the runtime
set ``TRAC_FORENSICS_DIR``, capture the host repo's live git state
(``log --graph --all``, ``worktree list``, ``status``) plus the failing
assertion context into the forensics dir BEFORE the fixture tears the
temp repo down -- and preserve the whole repo alongside.

Kills the "wrong tree" misdiagnosis class mechanically: T-042 :78
(2026-09-04) -- Prism DIAGNOSE inferred statically after the temp repo
was destroyed, blamed a nonexistent identity-source defect, and burned
~12 Devon attempts chasing it.

Inert by design: without ``TRAC_FORENSICS_DIR`` (local dev runs) the
hook captures nothing and changes no behavior.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_MAX_LONGREPR_LINES = 60
_MAX_GIT_LINES = 200


def forensics_dir() -> Path | None:
    """The runtime-provided capture dir, or None (inert) when unset."""
    raw = os.environ.get("TRAC_FORENSICS_DIR", "").strip()
    return Path(raw) if raw else None


def _sanitize(nodeid: str) -> str:
    """Filesystem-safe, bounded node id for the forensics file name."""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", nodeid)
    return clean[-160:] if len(clean) > 160 else clean


def _git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode == 0 else (proc.stderr or "")


def _clip(text: str, limit: int) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    return "\n".join(lines[:limit]) + f"\n... [{len(lines) - limit} more lines clipped]"


def _longrepr_text(rep) -> str:
    try:
        text = rep.longreprtext
    except Exception:  # pragma: no cover - longrepr shapes vary
        return ""
    return _clip(text, _MAX_LONGREPR_LINES)


def capture_failure_forensics(item, rep, host_repo: Path) -> dict | None:
    """Capture one failing test's forensic package.

    Writes ``failures/<node>.json`` (git state + assertion context) and
    preserves the full host repo under ``preserved/<node>/`` when
    ``TRAC_FORENSICS_PRESERVE`` is not "0". Returns the captured record
    (also embedded in the JSON file), or None when the dir is unset or
    the repo is missing.
    """
    root = forensics_dir()
    if root is None or not host_repo.exists():
        return None
    record = {
        "nodeid": item.nodeid,
        "when": rep.when,
        "host_repo": str(host_repo),
        "git_log_graph_all": _clip(
            _git(host_repo, "log", "--oneline", "--graph", "--all", "-n", "80"),
            _MAX_GIT_LINES,
        ),
        "git_worktree_list": _git(host_repo, "worktree", "list"),
        "git_status": _git(host_repo, "status", "--porcelain=v1", "-b"),
        "git_head": _git(host_repo, "rev-parse", "HEAD").strip(),
        "assertion_context": _longrepr_text(rep),
    }
    preserved = ""
    if os.environ.get("TRAC_FORENSICS_PRESERVE", "1") != "0":
        target = root / "preserved" / _sanitize(item.nodeid)
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(host_repo, target, symlinks=True)
            preserved = str(target)
        except OSError:
            preserved = ""
    record["preserved_repo"] = preserved
    failures_dir = root / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    path = failures_dir / f"{_sanitize(item.nodeid)}.json"
    try:
        path.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=1),
            encoding="utf-8",
        )
    except OSError:
        return None
    return record
