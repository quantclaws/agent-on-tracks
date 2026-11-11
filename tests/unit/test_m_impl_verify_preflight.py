"""Verification-only preflight: Shield test assets must be committed.

hotfix #262 T-004 PRISM-PLAN-011: the four acceptance anchors can pass on a
worktree that still carries uncommitted Shield-domain test assets, so the
planned SHIELD_FIX trigger never fires and freeze_candidate blocks on the
dirty tree. The preflight reports ``test_defect`` so the assets route to
Shield.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git as _git
from tests.unit.helpers import init_repo as _init_repo
from tracks.executor import m_impl_diagnose as _diagnose


def test_dirty_shield_paths_flags_tracked_untracked_and_deleted(tmp_path, monkeypatch):
    repo, _base = _init_repo(tmp_path)
    tracked = repo / "tests" / "integration" / "test_x.py"
    support = repo / "tests" / "_support" / "release_premises.py"
    tracked.parent.mkdir(parents=True)
    support.parent.mkdir(parents=True)
    tracked.write_text("x\n")
    support.write_text("support\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    monkeypatch.setattr(
        _diagnose,
        "layout_paths",
        lambda _repo, _role: [
            "tests/integration",
            "tests/_support",
            "tests/counterexamples",
        ],
    )

    tracked.write_text("dirty\n")
    untracked = repo / "tests" / "counterexamples" / "new.patch"
    untracked.parent.mkdir(parents=True)
    untracked.write_text("new\n")
    (repo / "README.md").write_text("operator only\n")

    assert _diagnose._dirty_shield_paths(Path(repo)) == [
        "tests/counterexamples/new.patch",
        "tests/integration/test_x.py",
    ]

    tracked.unlink()
    assert _diagnose._dirty_shield_paths(Path(repo)) == [
        "tests/counterexamples/new.patch",
        "tests/integration/test_x.py",
    ]
