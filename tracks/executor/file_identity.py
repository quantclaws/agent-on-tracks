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
from pathlib import Path


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
    return (not identity.startswith("symlink:")
            and identity not in ("missing", "unreadable"))
