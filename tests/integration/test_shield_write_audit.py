"""Shield M-TEST WRITE audit regression tests (FR-0120).

Focused file: three Prism blockers for the discussion-audit bootstrap fix:
- Blocker 1: validate post-agent delta against pre-dispatch byte snapshot, not HEAD
- Blocker 2: atomic whole-run rollback covering all changed paths, no early return
- Blocker 3: lexical no-follow type-aware path/symlink handling

The base ``opencode`` stand-in script (generic + Shield behaviors:
``shield_reply``, ``shield_test``, ``shield_body_edit``,
``shield_pre_dirty_reply``, ``shield_replace_body_plus_reply``,
``shield_asset_edit_plus_doc_edit``, ``shield_doc_to_symlink``,
``shield_doc_to_dir_symlink``, ``shield_doc_to_dangling_symlink``) lives in
``test_opencode_backend.STANDIN``; this file composes it with the Devon
behaviors from ``opencode_audit_standin.AUDIT_STANDIN`` so the audit fixture
drives both Shield and Devon write scenarios.
"""

import os
import shutil
import stat
import subprocess

import pytest

from tracks.effects.audit import Auditor
from tracks.effects.fake import FakeBackend
from tracks.effects.opencode import OpencodeBackend

M_TEST_DOCS = ("test-plan.md", "interfaces.md", "acceptance.md")


def m_test_assignment(substate="WRITE"):
    return {"kind": substate, "skills": ["tracks-discuz"], "docs": list(M_TEST_DOCS)}


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
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "m-test docs"], cwd=host_repo, check=True, capture_output=True
    )


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    """Put a fake `opencode` first on PATH (L2 stand-in).

    Re-declares the stand-in so this file is self-contained; the AUDIT_STANDIN
    constant (base Shield + Devon behaviors) is imported from
    ``opencode_audit_standin`` to avoid drift."""
    from tests.integration.opencode_audit_standin import AUDIT_STANDIN

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode"
    exe.write_text(AUDIT_STANDIN, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def _set_env(monkeypatch, behavior, docs, extra=None):
    monkeypatch.setenv("FAKE_OPENCODE_BEHAVIOR", behavior)
    monkeypatch.setenv("FAKE_OPENCODE_DOCS", ",".join(map(str, docs)))
    if extra is not None:
        monkeypatch.setenv("FAKE_OPENCODE_EXTRA", str(extra))


def write_project_layout(
    host_repo, devon=("tracks/", "tests/unit/"), shield=("tests/integration/",)
):
    """Write the Archer-produced ``.tracks/projects/project.toml`` so the
    dynamic layout authorization (FR-0120) allows Shield/Devon writes."""
    path = host_repo / ".tracks" / "projects" / "project.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[unit]\n"
        'framework = "pytest"\n'
        'paths = ["tests/unit/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"\n'
        'run = ".venv/bin/python -m pytest tests/unit/ --junitxml={result}"\n'
        'run_selected = ".venv/bin/python -m pytest {nodes} --junitxml={result}"\n'
        'cwd = "."\n\n'
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q --junitxml={result}"\n'
        'run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"\n'
        'cwd = "."\n\n'
        "[e2e]\n"
        'framework = "pytest"\n'
        'paths = ["tests/e2e/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
        'run = ".venv/bin/python -m pytest tests/e2e/ --junitxml={result}"\n'
        'run_selected = ".venv/bin/python -m pytest {nodes} --junitxml={result}"\n'
        'cwd = "."\n\n'
        "[nightly]\n"
        'schedule = "0 3 * * *"\n'
        'workflow = ".github/workflows/nightly.yml"\n'
        'job = "nightly-regression"\n'
        'layers = ["unit", "integration", "e2e"]\n'
        'purpose = "current FULL suite; not a local gate"\n\n'
        "[layout]\n\n"
        "[layout.devon]\n"
        f"writable = {list(devon)!r}\n\n"
        "[layout.shield]\n"
        f"writable = {list(shield)!r}\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def project_layout(host_repo):
    """Every audit test in this file dispatches into a layout-authorized repo
    (FR-0120): Shield may write test assets under ``tests/integration/`` and
    Devon under ``tracks/``/``tests/unit/``, mirroring the real Archer-written
    project contract the machine produces in M-DESIGN."""
    write_project_layout(host_repo)


DEVON_DOCS = ("architecture.md", "interfaces.md")


def devon_assignment(substate="GREEN", allowed=None):
    return {
        "kind": substate,
        "phase": substate.lower(),
        "manifest": {
            "allowed_paths": allowed or ["tracks/impl/demo.py", "tests/unit/test_demo.py"]
        },
        "skills": ["tracks-devon-rgr"],
    }


def commit_devon_docs(host_repo, docs):
    for path in docs:
        path.write_text("---\nsha:\n---\n\n# design\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "devon docs"], cwd=host_repo, check=True, capture_output=True
    )


def devon_backend(host_repo):
    return OpencodeBackend(host_repo, "v0.5")


# -- Baseline scenarios (ported from test_opencode_backend.py) ----------------


def test_shield_write_canonical_discussion_reply_accepted(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Shield M-TEST WRITE: a canonical discussion reply appended to
    test-plan.md passes the audit (not over-reach; discussion-only diff)."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    test_file = host_repo / "tests" / "integration" / "test_foo.py"
    _set_env(monkeypatch, "shield_reply", docs, test_file)
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert test_file.exists()
    assert "Shield" in (m_test_vdir / "test-plan.md").read_text(encoding="utf-8")


def test_shield_write_non_discussion_doc_edit_rolls_back_entire_run(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """A body/prose edit to test-plan.md (not a canonical discussion reply)
    fails as over_reach and rolls back the ENTIRE run, including the allowed
    test-asset write (partially-valid runs never persist)."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    test_file = host_repo / "tests" / "integration" / "test_foo.py"
    _set_env(monkeypatch, "edit_docs", docs, test_file)
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    assert not test_file.exists()
    original = "---\nsha:\n---\n\n# doc\n"
    assert (m_test_vdir / "test-plan.md").read_text(encoding="utf-8") == original


def test_shield_write_test_only_passes(fake_opencode, m_test_vdir, host_repo, monkeypatch):
    """Shield M-TEST WRITE that only writes test assets (no doc edits) passes
    — the discussion-only guard only fires when an assignment doc is touched."""
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    test_file = host_repo / "tests" / "integration" / "test_bar.py"
    _set_env(monkeypatch, "shield_test", docs, test_file)
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert test_file.exists()


def test_shield_write_declared_e2e_writable_not_over_reach(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Regression: the Fake's generated project contract declares
    ``tests/e2e/`` (and the rest of the host Shield scope) in
    ``[layout.shield].writable``, so a Shield M-TEST WRITE that creates a
    test file under ``tests/e2e/`` is within scope — the write-scope audit
    must NOT report over_reach.

    Direct Fake/Opencode audit regression (not just a TOML parser test): the
    contract comes from ``FakeBackend._write_project_contract()`` and the
    dispatch runs the real OpencodeBackend audit via the fake ``opencode``
    stand-in, so the declared e2e path is proven allowed end-to-end."""
    FakeBackend(host_repo, "v0.5")._write_project_contract()
    docs = [m_test_vdir / name for name in M_TEST_DOCS]
    commit_m_test_docs(host_repo, docs)
    e2e_file = host_repo / "tests" / "e2e" / "test_e2e_scope.py"
    _set_env(monkeypatch, "shield_test", docs, e2e_file)
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert "over_reach" not in out.get("audit_evidence", "")
    assert e2e_file.exists()


def test_shield_write_pre_dirty_doc_preserved_on_force_rollback(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
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
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    assert not test_file.exists()
    assert plan.read_bytes() == pre_dirty


# -- Blocker 1: validate post-agent delta against pre-dispatch snapshot ------


def test_shield_write_pre_dirty_reply_appended_passes(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
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
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert test_file.exists()
    # Pre-dirty body preserved + discussion reply appended.
    new_content = plan.read_bytes()
    assert new_content.startswith(pre_dirty)
    assert b"Shield" in new_content


def test_shield_write_pre_dirty_replace_body_plus_reply_rolls_back(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
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
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    assert not test_file.exists()
    # Pre-dirty content restored byte-identical (NOT erased to HEAD).
    assert plan.read_bytes() == pre_dirty


# -- Blocker 2: atomic whole-run rollback, no early return --------------------


def test_shield_write_asset_edit_plus_doc_edit_atomic_rollback(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
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
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    # Test asset ALSO rolled back atomically (no early return).
    assert test_file.read_bytes() == pre_dirty_asset
    # Doc body restored.
    assert plan.read_text(encoding="utf-8") == "---\nsha:\n---\n\n# doc\n"


# -- Blocker 3: lexical no-follow type-aware path/symlink handling -----------


@pytest.mark.parametrize(
    "behavior",
    [
        "shield_doc_to_symlink",
        "shield_doc_to_dir_symlink",
        "shield_doc_to_dangling_symlink",
    ],
)
def test_shield_write_doc_replaced_by_symlink_rolls_back(
    fake_opencode, m_test_vdir, host_repo, monkeypatch, behavior
):
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
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "test-plan.md" in out["audit_evidence"]
    # Symlink removed, original regular file restored.
    assert not plan.is_symlink()
    assert plan.is_file()
    assert plan.read_bytes() == original


def test_shield_write_doc_replaced_by_symlink_does_not_follow(
    fake_opencode, m_test_vdir, host_repo, monkeypatch, tmp_path
):
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
    out = m_test_backend(host_repo).act(
        "shield", "WRITE", None, None, assignment=m_test_assignment()
    )
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    # External target NOT written through (still its original content).
    assert external.read_text(encoding="utf-8") == "external content\n"
    # Symlink removed, original restored.
    assert not plan.is_symlink()
    assert plan.read_bytes() == original


# -- BOOT-ROLLBACK-001: ancestor symlink escape protection -------------------


def test_shield_force_rollback_ancestor_symlink_preserves_external(
    host_repo, m_test_vdir, tmp_path
):
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
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "tests"], cwd=host_repo, check=True, capture_output=True)

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


# -- Devon commentable-doc audit (Blocker 1 + 2, Shield parity) ---------------


def test_devon_canonical_discussion_reply_and_allowed_write_pass(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Requirement 3+4: Devon appends a canonical discussion reply to a
    commentable doc AND writes a legit file in its allowed layout dir; both
    pass the audit unchanged (no doc edit violation, no over-reach)."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    impl = host_repo / "tracks" / "impl" / "demo.py"
    _set_env(monkeypatch, "devon_reply", [docs[0]], impl)
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    assert impl.exists()
    assert "Devon" in docs[0].read_text(encoding="utf-8")


def test_devon_deletes_commentable_doc_rolls_back(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Blocker 1: Devon DELETES a commentable doc. The post-state
    ``exists()`` filter must not hide the deletion - the doc is still audited,
    the run fails as over_reach, and the doc is restored from HEAD."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    original = docs[0].read_bytes()
    _set_env(monkeypatch, "devon_delete_doc", [docs[0]])
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "architecture.md" in out["audit_evidence"]
    assert docs[0].read_bytes() == original


@pytest.mark.parametrize(
    "behavior",
    [
        "devon_doc_to_dangling_symlink",
        "devon_doc_to_dir_symlink",
    ],
)
def test_devon_commentable_doc_replaced_by_exception_type_rolls_back(
    fake_opencode, m_test_vdir, host_repo, monkeypatch, behavior
):
    """Blocker 1: Devon swaps a commentable doc for an exception type
    (dangling/directory symlink). The type-aware no-follow check flags it and
    rollback restores the original regular file."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    original = docs[0].read_bytes()
    _set_env(monkeypatch, behavior, [docs[0]])
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "architecture.md" in out["audit_evidence"]
    assert not docs[0].is_symlink()
    assert docs[0].read_bytes() == original


def test_devon_body_edit_plus_allowed_write_atomic_rollback(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Blocker 2 (Shield parity): Devon body-edits a commentable doc AND
    writes a legit allowed file. The whole run is rolled back in one force
    pass - the allowed write must NOT survive the invalid doc edit."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    original = docs[0].read_bytes()
    impl = host_repo / "tracks" / "impl" / "demo.py"
    _set_env(monkeypatch, "devon_body_edit", [docs[0]], impl)
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "architecture.md" in out["audit_evidence"]
    assert not impl.exists()
    assert docs[0].read_bytes() == original


def test_devon_overreach_plus_doc_edit_atomic_rollback(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Blocker 2: Devon over-reaches (out-of-scope write) AND body-edits a
    commentable doc. The audit is ONE atomic decision: the doc edit, the
    over-reach write, and any allowed write are all rolled back together - no
    early-return leaves the allowed-but-illegal doc edit behind."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    original = docs[0].read_bytes()
    impl = host_repo / "tracks" / "impl" / "demo.py"
    evil = host_repo / "evil.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(impl))
    _set_env(monkeypatch, "devon_overreach_plus_doc_edit", [docs[0]], evil)
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "architecture.md" in out["audit_evidence"]
    assert "over-reach" in out["audit_evidence"]
    assert "evil.tmp" in out["audit_evidence"]
    assert not evil.exists()
    assert not impl.exists()
    assert docs[0].read_bytes() == original


def test_devon_pre_dirty_doc_reply_appended_passes_and_preserves_body(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Pre-dirty preservation (pass path): a canonical discussion reply
    appended to Human's pre-dirty commentable content passes and the pre-dirty
    body is preserved byte-identical (delta validated against the snapshot,
    not HEAD)."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    arch = docs[0]
    pre_dirty = arch.read_bytes() + b"human note\n"
    arch.write_bytes(pre_dirty)
    impl = host_repo / "tracks" / "impl" / "demo.py"
    _set_env(monkeypatch, "devon_pre_dirty_reply", [arch], impl)
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "done"
    assert out.get("failure_class") is None
    new_content = arch.read_bytes()
    assert new_content.startswith(pre_dirty)
    assert b"Devon" in new_content
    assert impl.exists()


def test_devon_violation_preserves_predirty_human_work(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Pre-dirty preservation (fail path): on a force rollback, Human's
    pre-dirty tracked file is restored byte-identical from the pre-dispatch
    snapshot - never erased to HEAD - while the invalid doc edit is reverted."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    arch = docs[0]
    original = arch.read_bytes()
    predirty = host_repo / "tests" / "unit" / "test_predirty.py"
    predirty.parent.mkdir(parents=True, exist_ok=True)
    predirty.write_text("# original\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "predirty file"], cwd=host_repo, check=True, capture_output=True
    )
    human_dirty = b"# original\nhuman note\n"
    predirty.write_bytes(human_dirty)
    _set_env(monkeypatch, "devon_edit_predirty_plus_doc_edit", [arch], predirty)
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "architecture.md" in out["audit_evidence"]
    # Human's pre-existing dirty content is preserved, not erased to HEAD.
    assert predirty.read_bytes() == human_dirty
    assert arch.read_bytes() == original


def test_devon_doc_replaced_by_nonempty_dir_atomic_rollback(
    fake_opencode, m_test_vdir, host_repo, monkeypatch
):
    """Review blocker: Devon replaces a commentable doc with a plain
    NON-EMPTY directory (payload + deep child) and in the same run makes an
    allowed write and an over-reach write.  ``agent_changed`` carries both the
    parent and its children; force rollback must be type-aware - remove the
    deeper children / anomalous tree first, then restore the parent regular
    file - so it never walks a child through a restored regular file
    (NotADirectoryError).  The outcome stays ``over_reach`` (never
    ``filesystem``); the original doc is restored; the child, allowed write
    and over-reach write all disappear."""
    docs = [m_test_vdir / name for name in DEVON_DOCS]
    commit_devon_docs(host_repo, docs)
    arch = docs[0]
    original = arch.read_bytes()
    impl = host_repo / "tracks" / "impl" / "demo.py"
    evil = host_repo / "evil.tmp"
    monkeypatch.setenv("FAKE_OPENCODE_TARGET", str(impl))
    _set_env(monkeypatch, "devon_doc_to_nonempty_dir", [arch], evil)
    out = devon_backend(host_repo).act("devon", "GREEN", None, None, assignment=devon_assignment())
    assert out["status"] == "failed"
    assert out["failure_class"] == "over_reach"
    assert "architecture.md" in out["audit_evidence"]
    assert "over-reach" in out["audit_evidence"]
    assert "evil.tmp" in out["audit_evidence"]
    # Original regular doc restored (no NotADirectoryError -> no filesystem).
    assert arch.is_file()
    assert arch.read_bytes() == original
    # The child files under the swapped doc are gone.
    assert not (arch / "payload.md").exists()
    assert not (arch / "deep" / "child.md").exists()
    # Allowed write and over-reach write both rolled back atomically.
    assert not impl.exists()
    assert not evil.exists()
