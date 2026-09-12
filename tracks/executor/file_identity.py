"""Shared file content-identity helpers.

A content identity is sha256(content) for readable regular files,
``symlink:{target}`` for symlinks, ``missing`` for deleted entries, and
``unreadable`` for non-regular/permission-denied files. Identities carry no
mtime, so they are deterministic and JSON-serializable: they can be persisted
in events (``command.issued`` pre-dirty snapshots) and compared across
dispatches or after crash recovery.

The Fake Shield, Executor, and ResultCheckpoint all observe the same identity
semantics, so the helpers live in one production module instead of being
duplicated at each boundary.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path

from tracks.executor.host_contract import DEFAULT_INSTALL_INTERPRETER

# Paths excluded from the dirty-aware tree stamp: runtime state and build
# artifacts are not source/test/config content and must never destabilize a
# selection identity across WAL/replay of the same command. The declared env
# directory component is derived from the contract boundary's default
# interpreter spelling (IF-HOSTCONTRACT-001) — never spelled here.
_ENV_DIR_PREFIX = (
    posixpath.normpath(DEFAULT_INSTALL_INTERPRETER).split(posixpath.sep)[0] + "/"
)

TREE_STAMP_SKIP_PREFIXES = (
    ".git/",
    ".opencode/",
    ".test_cache/",
    ".ruff_cache/",
    ".tracks/",
    _ENV_DIR_PREFIX,
    "build/",
    "dist/",
    "logs/",
)


def porcelain_path(line: str) -> str | None:
    """Path of one ``git status --porcelain`` line (rename → destination).

    Returns None for lines too short to carry a path. Shared by the dirty
    tree stamp implementations so the parsing can never drift."""
    if len(line) < 4:
        return None
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def path_identity(path: Path) -> str:
    """Content identity of one filesystem entry (see module docstring)."""
    if path.is_symlink():
        return f"symlink:{os.readlink(path)}"
    if not path.exists():
        return "missing"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def is_regular_file_identity(identity: str) -> bool:
    """True iff an identity represents a readable regular file (sha256 hash).

    False for symlinks (``symlink:*``), deleted (``missing``), or unreadable
    entries.
    """
    return not identity.startswith("symlink:") and identity not in ("missing", "unreadable")
