"""Unit tests for three-worktree scheme (IF-IMPL-006).

Tests path determinism, cleanup idempotency, and that the main worktree
is never removed.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git as _git
from tests.unit.helpers import init_repo as _init_repo
from tracks.executor.worktree import (
    cleanup_worktree,
    create_devon_worktree,
    create_gate_worktree,
    create_test_authority_worktree,
)


def _worktree_list(repo: Path) -> set[str]:
    out = _git(repo, "worktree", "list", "--porcelain").stdout
    return {
        line.removeprefix("worktree ") for line in out.splitlines() if line.startswith("worktree ")
    }


def test_devon_worktree_path(tmp_path):
    repo, base = _init_repo(tmp_path)
    handle = create_devon_worktree(str(repo), base, "run-1", "T-001")
    assert "run-1" in handle.path
    assert "devon" in handle.path
    assert Path(handle.path).exists()
    cleanup_worktree(handle)


def test_devon_worktree_deterministic(tmp_path):
    repo, base = _init_repo(tmp_path)
    h1 = create_devon_worktree(str(repo), base, "run-1", "T-001")
    p1 = h1.path
    cleanup_worktree(h1)
    h2 = create_devon_worktree(str(repo), base, "run-1", "T-001")
    assert h2.path == p1
    cleanup_worktree(h2)


def test_test_authority_worktree(tmp_path):
    repo, base = _init_repo(tmp_path)
    handle = create_test_authority_worktree(str(repo), base, "run-1")
    assert Path(handle.path).exists()
    cleanup_worktree(handle)


def test_gate_worktree(tmp_path):
    repo, base = _init_repo(tmp_path)
    devon = create_devon_worktree(str(repo), base, "run-1", "T-001")
    authority = create_test_authority_worktree(str(repo), base, "run-1")
    gate = create_gate_worktree(
        str(repo),
        base,
        "",
        "",
        "run-1",
        "T-001",
    )
    assert Path(gate.path).exists()
    cleanup_worktree(gate)
    cleanup_worktree(devon)
    cleanup_worktree(authority)


def test_cleanup_idempotent(tmp_path):
    repo, base = _init_repo(tmp_path)
    handle = create_devon_worktree(str(repo), base, "run-1", "T-001")
    cleanup_worktree(handle)
    cleanup_worktree(handle)
    assert not Path(handle.path).exists()


def test_cleanup_never_deletes_main(tmp_path):
    repo, base = _init_repo(tmp_path)
    original = _worktree_list(repo)
    handle = create_devon_worktree(str(repo), base, "run-1", "T-001")
    cleanup_worktree(handle)
    assert _worktree_list(repo) == original
