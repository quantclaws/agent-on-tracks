"""Focused unit tests: contract-correct default Fake Shield M-TEST behavior.

Behavior A (Fake Shield behavioral tests): the default Shield test derives the
same deterministic production path Fake Archer PLANNING uses
(``tracks/impl/{ac_slug}.py``) and the applicable IF id from
``assignment.test_tasks``, so it fails as a legitimate Red (assertion_failure,
collection succeeds) before the implementation and passes once the
implementation exists with ``IMPLEMENTED_IF == expected IF`` — without ever
editing product code. Special tokens (illegit_red / mixed_red / pass_red /
short_marker) keep their semantics. ``_append_revision_marker`` sanitizes the
evidence reason to a single comment-safe line.

Behavior C (reach declarations for the Fake design): the Fake M-DESIGN
declares and materializes ``.tracks/reach-entries.txt`` as an architecture
Scaffold config artifact and the design ResultCheckpoint stages it as a
declared scaffold; check_reach passes once Green files exist.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tracks import paths
from tracks.checks.reach import check_reach_file
from tracks.effects.fake import FakeBackend
from tracks.executor import Executor
from tracks.scaffold import _scaffold_declared_paths
from tracks.store import Store


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / ".gitignore").write_text("", encoding="utf-8")
    _git(repo, "add", "README.md", ".gitignore")
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True, text=True
    )
    return repo


def _vdir(tmp_path: Path) -> Path:
    return paths.version_dir(paths.tracks_home(tmp_path), "v0.5")


def _acceptance(tmp_path: Path) -> Path:
    vdir = _vdir(tmp_path)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(
        "# Acceptance\n\n"
        "## FR-0010 Deterministic output\n\n### AC-FR0010-01\n\n- x\n\n"
        "## NFR-0020 Stable interface\n\n### AC-NFR0020-01\n\n- y\n",
        encoding="utf-8",
    )
    return vdir


_TASKS = [
    {"ac_id": "AC-FR0010-01", "layers": ["integration"], "if_ids": ["IF-MTEST-001"]},
    {"ac_id": "AC-NFR0020-01", "layers": ["e2e"], "if_ids": ["IF-MTEST-002"]},
]


def _act(tmp_path: Path, tasks=_TASKS, token: str | None = None) -> dict:
    assignment = {"test_tasks": tasks}
    if token is not None:
        os.environ["TRAC_FAKE_SIMULATE"] = f"shield:WRITE={token}"
        try:
            return FakeBackend(tmp_path, "v0.5").act("shield", "WRITE", None, None, assignment)
        finally:
            del os.environ["TRAC_FAKE_SIMULATE"]
    return FakeBackend(tmp_path, "v0.5").act("shield", "WRITE", None, None, assignment)


def _test_file(tmp_path: Path, layer: str, slug: str) -> Path:
    return tmp_path / "tests" / layer / f"test_{slug}.py"


def _run_pytest(repo: Path, target: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", target],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _write_impl(repo: Path, slug: str, if_id: str) -> Path:
    impl = repo / "tracks" / "impl" / f"{slug}.py"
    impl.parent.mkdir(parents=True, exist_ok=True)
    impl.write_text(
        f'"""Deterministic implementation for {if_id}."""\nIMPLEMENTED_IF = "{if_id}"\n',
        encoding="utf-8",
    )
    return impl


# -- Behavior A: default Shield tests are deterministic behavioral contracts ---


def test_default_test_fails_legitimately_before_implementation(tmp_path):
    _act(tmp_path)
    test_file = _test_file(tmp_path, "integration", "ac_fr0010_01")

    assert test_file.is_file()
    text = test_file.read_text(encoding="utf-8")
    assert "runpy" in text
    assert "tracks/impl/ac_fr0010_01.py" in text
    assert "'IF-MTEST-001'" in text
    assert "IMPLEMENTED_IF" in text
    assert "NotImplementedError" not in text
    assert "nonexistent_module" not in text

    proc = _run_pytest(tmp_path, str(test_file))
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "AssertionError" in proc.stdout + proc.stderr, (
        "pre-implementation failure must be a legit assertion_failure"
    )
    assert "error" not in proc.stderr.lower() or "AssertionError" in proc.stderr


def test_default_test_passes_after_matching_implementation_exists(tmp_path):
    _act(tmp_path)
    test_file = _test_file(tmp_path, "integration", "ac_fr0010_01")
    _write_impl(tmp_path, "ac_fr0010_01", "IF-MTEST-001")

    proc = _run_pytest(tmp_path, str(test_file))

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_default_test_does_not_pass_for_wrong_if_id(tmp_path):
    _act(tmp_path)
    test_file = _test_file(tmp_path, "integration", "ac_fr0010_01")
    _write_impl(tmp_path, "ac_fr0010_01", "IF-MTEST-999")

    proc = _run_pytest(tmp_path, str(test_file))

    assert proc.returncode != 0, "wrong IF id must keep the test Red"


def test_default_test_never_edits_product_code(tmp_path):
    _act(tmp_path)
    _test_file(tmp_path, "integration", "ac_fr0010_01")

    assert not (tmp_path / "tracks" / "impl").exists(), (
        "writing the Shield test must not create product code"
    )


def test_e2e_default_test_uses_its_own_if_id_and_path(tmp_path):
    _act(tmp_path)
    e2e = _test_file(tmp_path, "e2e", "ac_nfr0020_01")

    text = e2e.read_text(encoding="utf-8")
    assert "tracks/impl/ac_nfr0020_01.py" in text
    assert "'IF-MTEST-002'" in text


def test_special_tokens_keep_their_semantics(tmp_path, monkeypatch):
    cases = {
        "illegit_red": ("import nonexistent_module", "integration"),
        "mixed_red": ("assert False", "integration"),
        "pass_red": ("    pass", "integration"),
    }
    for token, (needle, layer) in cases.items():
        repo = tmp_path / token
        repo.mkdir()
        monkeypatch.setenv("TRAC_FAKE_SIMULATE", f"shield:WRITE={token}")
        FakeBackend(repo, "v0.5").act(
            "shield",
            "WRITE",
            None,
            None,
            {"test_tasks": [_TASKS[0]]},
        )
        body = _test_file(repo, layer, "ac_fr0010_01").read_text(encoding="utf-8")
        assert needle in body, f"{token}: expected {needle!r} in {body!r}"


def test_short_marker_token_writes_short_format_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "shield:WRITE=short_marker")
    FakeBackend(tmp_path, "v0.5").act(
        "shield",
        "WRITE",
        None,
        None,
        {"test_tasks": [_TASKS[0]]},
    )
    body = _test_file(tmp_path, "integration", "ac_fr0010_01").read_text(encoding="utf-8")
    assert "TRACKS-TRACE short format (no @version)" in body


def test_older_flow_versions_keep_legacy_m_test_stub(tmp_path):
    """v0.1/v0.4 M-TEST journeys (no M-IMPL) keep the legacy legit stub so the
    M-TEST red classification (stub_token_failure) is unchanged there."""
    repo = tmp_path / "legacy"
    repo.mkdir()
    FakeBackend(repo, "v0.4").act(
        "shield",
        "WRITE",
        None,
        None,
        {"test_tasks": [_TASKS[0]]},
    )
    body = _test_file(repo, "integration", "ac_fr0010_01").read_text(encoding="utf-8")
    assert 'raise NotImplementedError("IF-MTEST-001")' in body
    assert "IMPLEMENTED_IF" not in body


# -- Behavior A: revision marker sanitization ---------------------------------


def test_revision_marker_collapses_multiline_reason_to_one_comment(tmp_path):
    tests_dir = tmp_path / "tests" / "integration"
    tests_dir.mkdir(parents=True)
    test_file = tests_dir / "test_ac_fr0010_01.py"
    test_file.write_text("def test_ac_fr0010_01():\n    assert False\n", encoding="utf-8")

    assignment = {
        "test_tasks": _TASKS,
        "evidence": {
            "check": "no_diff",
            "reason": "line1\nline2\r\n<script>alert(1)</script>",
        },
    }
    _act_result = FakeBackend(tmp_path, "v0.5").act("shield", "WRITE", None, None, assignment)
    assert _act_result["status"] == "done"

    text = test_file.read_text(encoding="utf-8")
    marker_lines = [line for line in text.splitlines() if line.startswith("# shield revision:")]
    assert len(marker_lines) == 1
    assert "\n" not in marker_lines[0]
    assert "line1 line2 <script>alert(1)</script>" in marker_lines[0]
    # the file must stay syntactically valid (no newline injection / SyntaxError)
    compile(text, "<test>", "exec")


def test_revision_marker_defaults_when_reason_missing(tmp_path):
    tests_dir = tmp_path / "tests" / "integration"
    tests_dir.mkdir(parents=True)
    test_file = tests_dir / "test_ac_fr0010_01.py"
    test_file.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    FakeBackend(tmp_path, "v0.5").act(
        "shield",
        "WRITE",
        None,
        None,
        {"test_tasks": _TASKS, "evidence": {"check": "full_suite"}},
    )
    text = test_file.read_text(encoding="utf-8")
    assert "# shield revision: full_suite\n" in text
    compile(text, "<test>", "exec")


# -- Behavior C: reach declarations for the Fake design -----------------------


def test_fake_design_declares_and_materializes_reach_entries(tmp_path):
    _acceptance(tmp_path)
    result = FakeBackend(tmp_path, "v0.5").act(
        "archer",
        "DRAFT",
        None,
        None,
        {"kind": "DRAFT"},
    )
    assert result["status"] == "done"

    reach_path = tmp_path / ".tracks" / "reach-entries.txt"
    assert reach_path.is_file(), "Fake M-DESIGN must materialize reach entries"
    assert reach_path.read_text(encoding="utf-8").splitlines() == [
        "tracks.impl.ac_fr0010_01",
        "tracks.impl.ac_nfr0020_01",
    ]

    architecture = (_vdir(tmp_path) / "architecture.md").read_text(encoding="utf-8")
    assert ".tracks/reach-entries.txt" in _scaffold_declared_paths(architecture)


def test_fake_design_reach_entries_recognized_as_declared_scaffold(tmp_path):
    repo = _git_repo(tmp_path)
    _acceptance(repo)
    FakeBackend(repo, "v0.5").act(
        "archer",
        "DRAFT",
        None,
        None,
        {"kind": "DRAFT"},
    )
    store = Store(paths.tracks_home(repo))
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    executor = Executor(store, repo, "RUN")

    path, issue = executor._stageable_scaffold_path(".tracks/reach-entries.txt")
    assert issue is None, f"reach entries must stage as declared scaffold: {issue}"
    assert path is not None
    assert path.name == "reach-entries.txt"
    # an arbitrary other .tracks/** path stays rejected
    _, rejected = executor._stageable_scaffold_path(".tracks/other.txt")
    assert rejected is not None


def test_fake_design_older_flow_versions_declare_no_reach_entries(tmp_path):
    vdir = paths.version_dir(paths.tracks_home(tmp_path), "v0.4")
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text("# A\n\n### AC-FR0010-01\n\n- x\n", encoding="utf-8")
    FakeBackend(tmp_path, "v0.4").act(
        "archer",
        "DRAFT",
        None,
        None,
        {"kind": "DRAFT"},
    )
    assert not (tmp_path / ".tracks" / "reach-entries.txt").exists()
    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    assert _scaffold_declared_paths(architecture) <= {"{path}"}


def test_check_reach_passes_after_green_files_exist(tmp_path):
    _acceptance(tmp_path)
    FakeBackend(tmp_path, "v0.5").act(
        "archer",
        "DRAFT",
        None,
        None,
        {"kind": "DRAFT"},
    )
    # Green files exist: deterministic Fake Devon implementations.
    _write_impl(tmp_path, "ac_fr0010_01", "IF-MTEST-001")
    _write_impl(tmp_path, "ac_nfr0020_01", "IF-MTEST-002")

    report = check_reach_file(tmp_path)

    assert report.status == "pass", report
    assert report.islands == ()
    assert "tracks.impl.ac_fr0010_01" in report.entrypoints
    assert "tracks.impl.ac_nfr0020_01" in report.entrypoints


def test_check_reach_fails_for_undeclared_production_module(tmp_path):
    """Reach flags an existing production module that is not declared as a
    reach entrypoint (the reverse of the green-pass case)."""
    _acceptance(tmp_path)
    FakeBackend(tmp_path, "v0.5").act(
        "archer",
        "DRAFT",
        None,
        None,
        {"kind": "DRAFT"},
    )
    _write_impl(tmp_path, "ac_fr0010_01", "IF-MTEST-001")
    _write_impl(tmp_path, "ac_nfr0020_01", "IF-MTEST-002")
    undeclared = tmp_path / "tracks" / "impl" / "ac_undeclared_01.py"
    undeclared.write_text('IMPLEMENTED_IF = "IF-MTEST-003"\n', encoding="utf-8")

    report = check_reach_file(tmp_path)

    assert report.status == "fail"
    assert "tracks.impl.ac_undeclared_01" in report.islands
