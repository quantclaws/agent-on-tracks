"""Shield M-TEST WRITE audit regression tests (FR-0120).

Focused file: three Prism blockers for the discussion-audit bootstrap fix:
- Blocker 1: validate post-agent delta against pre-dispatch byte snapshot, not HEAD
- Blocker 2: atomic whole-run rollback covering all changed paths, no early return
- Blocker 3: lexical no-follow type-aware path/symlink handling

The STANDIN script lives in ``test_opencode_backend.py``; this file reuses
the same fixture so the stand-in behaviors added there (``shield_reply``,
``shield_test``, ``shield_body_edit``, ``shield_pre_dirty_reply``,
``shield_replace_body_plus_reply``, ``shield_asset_edit_plus_doc_edit``,
``shield_doc_to_symlink``, ``shield_doc_to_dir_symlink``,
``shield_doc_to_dangling_symlink``) are available.
"""
import os
import shutil
import stat
import subprocess

import pytest

from tracks.effects.audit import Auditor
from tracks.effects.opencode import OpencodeBackend

M_TEST_DOCS = ("test-plan.md", "interfaces.md", "acceptance.md")


def m_test_assignment(substate="WRITE"):
    return {"kind": substate, "skills": ["tracks-discuz"],
            "docs": list(M_TEST_DOCS)}


def m_test_backend(host_repo):
    return OpencodeBackend(host_repo, "v0.5")


@pytest.fixture
def m_test_vdir(host_repo):
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    vdir.mkdir(parents=True)
    return vdir


def commit_m_test_docs(host_repo, docs):
    for path in docs:
        path.write_text("---\nsha:\n---\n\n# doc\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "m-test docs"], cwd=host_repo,
                   check=True, capture_output=True)


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    """Put a fake `opencode` first on PATH (L2 stand-in).

    Re-declares the stand-in from ``test_opencode_backend.py`` so this file
    is self-contained; the STANDIN constant is imported from there to avoid
    drift."""
    from tests.integration.test_opencode_backend import STANDIN
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode"
    exe.write_text(STANDIN, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def _set_env(monkeypatch, behavior, docs, extra=None):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", behavior)
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    if extra is not None:
        monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(extra))


# -- Baseline scenarios (ported from test_opencode_backend.py) ----------------


def test_shield_write_canonical_discussion_reply_accepted(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Shield M-TEST WRITE: a canonical discussion reply appended to
    test-plan.md passes the audit (not over-reach; discussion-only diff)."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    test_file = host_repo / "tests" / "integration" / "test_foo.py"
    _set_env(monkeypatch, "shield_reply", docs, test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert test_file.exists()
    assert "Shield" in (m_test_vdir / "test-plan.md").read_text(
        encoding="utf-8")


def test_shield_write_non_discussion_doc_edit_rolls_back_entire_run(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """A body/prose edit to test-plan.md (not a canonical discussion reply)
    fails as over_reach and rolls back the ENTIRE run, including the allowed
    test-asset write (partially-valid runs never persist)."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    test_file = host_repo / "tests" / "integration" / "test_foo.py"
    _set_env(monkeypatch, "edit_docs", docs, test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    assert not test_file.exists()
    original = "---\nsha:\n---\n\n# doc\n"
    assert (m_test_vdir / "test-plan.md").read_text(
        encoding="utf-8") == original


def test_shield_write_test_only_passes(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Shield M-TEST WRITE that only writes test assets (no doc edits) passes
    — the discussion-only guard only fires when an assignment doc is touched."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    test_file = host_repo / "tests" / "integration" / "test_bar.py"
    _set_env(monkeypatch, "shield_test", docs, test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert test_file.exists()


def test_shield_write_pre_dirty_doc_preserved_on_force_rollback(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Regression: a pre-dirty test-plan.md (Human's uncommitted edit) is
    preserved byte-identical when a Shield WRITE body edit triggers a force
    rollback. ``git checkout HEAD`` would erase Human's pre-dirty back to the
    committed baseline; the content-snapshot restore keeps it. The agent test
    asset is removed and the run fails as over_reach."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    plan = m_test_vdir / "test-plan.md"
    pre_dirty = plan.read_bytes() + b"human note\n"
    plan.write_bytes(pre_dirty)
    test_file = host_repo / "tests" / "integration" / "test_predirty.py"
    _set_env(monkeypatch, "shield_body_edit", [plan], test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    assert not test_file.exists()
    assert plan.read_bytes() == pre_dirty


# -- Blocker 1: validate post-agent delta against pre-dispatch snapshot ------


def test_shield_write_pre_dirty_reply_appended_passes(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Blocker 1: a canonical discussion reply appended to pre-dirty body
    content passes. The pre-dispatch snapshot (Human's uncommitted body) is
    the base, NOT HEAD: removing/replacing body content would be flagged, but
    a pure discussion append is allowed and the pre-dirty body is preserved.

    Without the snapshot-based check, the diff against HEAD would include both
    Human's body edit AND the agent's reply, likely failing as
    non-discussion."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    plan = m_test_vdir / "test-plan.md"
    pre_dirty = plan.read_bytes() + b"human note\n"
    plan.write_bytes(pre_dirty)
    test_file = host_repo / "tests" / "integration" / "test_reply.py"
    _set_env(monkeypatch, "shield_pre_dirty_reply", [plan], test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert test_file.exists()
    # Pre-dirty body preserved + discussion reply appended.
    new_content = plan.read_bytes()
    assert new_content.startswith(pre_dirty)
    assert b"Shield" in new_content


def test_shield_write_pre_dirty_replace_body_plus_reply_rolls_back(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Blocker 1: agent removes/replaces Human's pre-dirty body content then
    adds a discussion reply. The delta against the pre-dispatch snapshot is
    NOT discussion-only (body content was removed/replaced), so the run fails
    and the pre-dirty bytes are restored byte-identical.

    Without the snapshot-based check, the diff against HEAD might pass because
    the final content differs from HEAD by both the replacement and the reply,
    but ``is_discussion_delta`` would still see the body swap as non-discussion.
    This test pins the snapshot-as-base behavior explicitly."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    plan = m_test_vdir / "test-plan.md"
    pre_dirty = plan.read_bytes() + b"human note\n"
    plan.write_bytes(pre_dirty)
    test_file = host_repo / "tests" / "integration" / "test_replace.py"
    _set_env(monkeypatch, "shield_replace_body_plus_reply", [plan], test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    assert not test_file.exists()
    # Pre-dirty content restored byte-identical (NOT erased to HEAD).
    assert plan.read_bytes() == pre_dirty


# -- Blocker 2: atomic whole-run rollback, no early return --------------------


def test_shield_write_asset_edit_plus_doc_edit_atomic_rollback(
        fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Blocker 2: agent edits a pre-dirty test asset (allowed write) AND
    makes an invalid doc body edit. The atomic audit must roll back BOTH the
    doc edit and the allowed test-asset edit in one force pass - no early
    return that would leave the allowed test-asset edit behind.

    Without atomicity, the generic over-reach guard would pass (test asset is
    allowed) and only the doc-delta check would fire, rolling back only the
    doc and leaving the allowed test-asset edit persisted despite the invalid
    run."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    # Human pre-dirties a test asset.
    test_file = host_repo / "tests" / "integration" / "test_atomic.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    pre_dirty_asset = b"# original test\n"
    test_file.write_bytes(pre_dirty_asset)
    plan = m_test_vdir / "test-plan.md"
    _set_env(monkeypatch, "shield_asset_edit_plus_doc_edit", [plan], test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    # Test asset ALSO rolled back atomically (no early return).
    assert test_file.read_bytes() == pre_dirty_asset
    # Doc body restored.
    assert plan.read_text(encoding="utf-8") == "---\nsha:\n---\n\n# doc\n"


# -- Blocker 3: lexical no-follow type-aware path/symlink handling -----------


@pytest.mark.parametrize("behavior", [
    "shield_doc_to_symlink",
    "shield_doc_to_dir_symlink",
    "shield_doc_to_dangling_symlink",
])
def test_shield_write_doc_replaced_by_symlink_rolls_back(
        fake_opencode, m_test_vdir, host_repo, monkeypatch, behavior):
    """Blocker 3: agent replaces a regular assignment doc with a symlink
    (file, directory, or dangling target). The type-aware touch detection
    flags it as offending; rollback unlinks the symlink WITHOUT following it
    and restores the original regular file from the pre-dispatch snapshot.

    A naive ``read_text()`` on a symlink-to-directory would raise IsADirectory;
    on a dangling symlink would raise FileNotFoundError; a naive restore via
    ``open(path, 'w')`` would write THROUGH the symlink to the external
    target. This test pins the no-follow, type-aware behavior."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    plan = m_test_vdir / "test-plan.md"
    original = plan.read_bytes()
    test_file = host_repo / "tests" / "integration" / "test_symlink.py"
    _set_env(monkeypatch, behavior, [plan], test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    # Symlink removed, original regular file restored.
    assert not plan.is_symlink()
    assert plan.is_file()
    assert plan.read_bytes() == original


def test_shield_write_doc_replaced_by_symlink_does_not_follow(
        fake_opencode, m_test_vdir, host_repo, monkeypatch, tmp_path):
    """Blocker 3 (explicit no-follow): agent replaces a doc with a symlink
    pointing to a writable external file. Rollback must unlink the symlink
    (not write through it) and restore the original. The external target must
    NOT be modified."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    plan = m_test_vdir / "test-plan.md"
    original = plan.read_bytes()
    external = tmp_path / "external.txt"
    external.write_text("external content\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_OPENCODE_SYMLINK_TARGET", str(external))
    test_file = host_repo / "tests" / "integration" / "test_no_follow.py"
    _set_env(monkeypatch, "shield_doc_to_symlink", [plan], test_file)
    out = m_test_backend(host_repo).act("shield", "WRITE", None, None,
                                        assignment=m_test_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    # External target NOT written through (still its original content).
    assert external.read_text(encoding="utf-8") == "external content\n"
    # Symlink removed, original restored.
    assert not plan.is_symlink()
    assert plan.read_bytes() == original


# -- BOOT-ROLLBACK-001: ancestor symlink escape protection -------------------


def test_shield_force_rollback_ancestor_symlink_preserves_external(
        host_repo, m_test_vdir, tmp_path):
    """BOOT-ROLLBACK-001: Shield WRITE force rollback with an ancestor
    symlink to an external directory must not follow the symlink.  External
    targets remain byte-identical; the ancestor is restored (shallowest-first
    sort) before the child is touched.

    Without the sort + safety check, unstable set ordering could process
    ``nested/victim`` before ``nested``, following the ancestor symlink to
    delete/overwrite the external victim."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    # Tracked test directory with a victim file
    test_dir = host_repo / "tests" / "integration"
    test_dir.mkdir(parents=True)
    victim = test_dir / "test_victim.py"
    victim.write_text("# original\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "tests"], cwd=host_repo,
                   check=True, capture_output=True)

    auditor = Auditor(host_repo, allowed=[str(d) for d in docs])
    baseline = auditor.baseline()

    # External directory with a victim file (same basename)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_victim = outside / "test_victim.py"
    outside_victim.write_bytes(b"# external\n")

    # Agent replaces the test directory with a symlink to the external dir
    # AND makes an invalid doc edit (triggers force rollback).
    shutil.rmtree(test_dir)
    os.symlink(outside, test_dir)
    plan = m_test_vdir / "test-plan.md"
    plan.write_text("# replaced\n", encoding="utf-8")

    changed = auditor.agent_changed_paths(baseline)
    assert "tests/integration" in changed
    assert "tests/integration/test_victim.py" in changed
    auditor.rollback_agent_changes(baseline, changed, force=True)

    # External victim must NOT be deleted or modified.
    assert outside_victim.exists()
    assert outside_victim.read_bytes() == b"# external\n"
    # Ancestor restored to a real directory; victim restored from HEAD.
    assert not test_dir.is_symlink()
    assert victim.read_text(encoding="utf-8") == "# original\n"
