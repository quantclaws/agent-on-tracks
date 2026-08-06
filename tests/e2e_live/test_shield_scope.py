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
