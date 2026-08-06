"""Deterministic coverage for the Shield no-commit scope check's
runtime-owned path exemption (``.tracks/``, ``.opencode/``)."""

from __future__ import annotations

import pytest

from tests.e2e_live.m_test_helpers import (
    _git,
    assert_shield_no_commit_scope,
    snapshot_git_state,
)
from tests.e2e_live.test_m_test_helpers import _git_repo


def test_scope_contract_ignores_runtime_owned_but_fails_other_outside(tmp_path):
    """Runtime-owned prefixes (.tracks/, .opencode/) are ignored while any
    other outside-tests/ path still fails and allowed tests/** paths pass."""
    repo = _git_repo(tmp_path)
    before = snapshot_git_state(repo)
    remote_refs = _git(repo, "ls-remote", "origin")

    # Runtime-owned dirty paths: document lock, db-ish files, opencode config.
    for rel in (
        ".tracks/projects/story.md.lock",
        ".tracks/runtime/tracks.db-wal",
        ".tracks/runtime/tracks.db-shm",
        ".opencode/config.json",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")

    # Allowed tests/** paths pass alongside runtime-owned dirt.
    (repo / "tests" / "integration" / "test_ok.py").write_text(
        "def test_ok():\n    pass\n", encoding="utf-8"
    )
    assert_shield_no_commit_scope(before, repo, remote_refs)

    # Any other outside-tests/ path still fails the assertion.
    (repo / "src").mkdir()
    (repo / "src" / "product.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="outside tests"):
        assert_shield_no_commit_scope(before, repo, remote_refs)


def test_scope_contract_expands_collapsed_tests_dir_then_catches_rogue_leaf(tmp_path):
    """A porcelain-collapsed ``?? tests/`` (every leaf untracked) is expanded
    to its leaf files: with only allowed leaves the scope check passes, and a
    rogue ``tests/rogue.py`` leaf is caught after expansion (run010 harness
    twin of the run001 product bug)."""
    repo = _git_repo(tmp_path, with_tests=False)
    before = snapshot_git_state(repo)
    remote_refs = _git(repo, "ls-remote", "origin")

    # Write only allowed leaves under a previously-absent tests/ tree so git
    # porcelain collapses the whole dir to a single ``?? tests/`` entry.
    for rel in (
        "tests/integration/test_ok.py",
        "tests/e2e/test_ok.py",
        "tests/assets/fixture.json",
        "tests/counterexamples/case.py",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x\n", encoding="utf-8")

    # Sanity: porcelain really did collapse to ``?? tests/``.
    assert _git(repo, "status", "--porcelain").strip() == "?? tests/"

    # Expanded to leaves, every path is under tests/<allowed>/ -> passes.
    assert_shield_no_commit_scope(before, repo, remote_refs)

    # A rogue leaf (not under any allowed tests/<dir>/) is caught after
    # expansion; porcelain still collapses to ``?? tests/``.
    (repo / "tests" / "rogue.py").write_text("x = 1\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain").strip() == "?? tests/"
    with pytest.raises(AssertionError, match="Shield wrote to tests/"):
        assert_shield_no_commit_scope(before, repo, remote_refs)
