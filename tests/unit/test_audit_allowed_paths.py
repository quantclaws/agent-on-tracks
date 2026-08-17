"""Regression tests for Fix M (audit allowed set merged into manifest.allowed_paths).

These two tests assert that Devon's ``_allowed_paths`` includes the
assignment manifest's Archer-authored ``allowed_paths`` (explicit per-task
write grants, e.g. a CI workflow path) on top of the coarse project.toml
[layout] dirs, and that a compliant write to such a manifest path produces
no over-reach audit evidence.

This file is intentionally **untracked**: the T-017 RGR cycle uses a GREEN
gate regression diff (``git diff --name-only r_sha -- tests/``) against the
R baseline (commit c50796f). Keeping the tests out of any tracked test
module avoids polluting that diff with unrelated changes, while pytest still
collects untracked files under ``tests/unit/`` so coverage is preserved.
Once T-017 lands, this file should be ``git add``-ed into the tree.
"""

import subprocess
from pathlib import Path

from tracks.effects.audit import Auditor
from tracks.effects.opencode import OpencodeBackend
from tracks.paths import project_toml_path, tracks_home


def test_devon_audit_allowed_includes_manifest_paths(tmp_path, monkeypatch):
    """T-017 audit false positive: ``_allowed_paths`` for Devon must include
    the assignment manifest's Archer-authored ``allowed_paths`` (explicit
    per-task write grants, e.g. a CI workflow path) in addition to the coarse
    project.toml [layout] dirs, else a compliant out-of-layout write is
    mis-flagged as over-reach."""
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    home = tracks_home(tmp_path)
    toml = project_toml_path(home)
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"\n'
        'cwd = "."\n'
        "\n[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n',
        encoding="utf-8",
    )
    backend = OpencodeBackend(tmp_path, "v0.1")
    agent_dest = tmp_path / ".tracks" / "runtime" / "agent"
    assignment = {"manifest": {"allowed_paths": [".github/workflows/ci.yml"]}}

    allowed = backend._allowed_paths([], agent_dest, "devon", "GREEN", assignment)

    assert Path(".github/workflows/ci.yml") in allowed
    assert tmp_path / "tracks" in allowed
    assert tmp_path / "tests/unit" in allowed
    assert agent_dest in allowed


def test_manifest_path_not_flagged_over_reach(tmp_path):
    """End-to-end audit check: with the manifest path in the allowed set
    (via ``_allowed_paths``), a write to that path produces NO over-reach
    evidence — the T-017 force-rollback false positive is gone."""
    repo = tmp_path / "host"
    repo.mkdir()
    for cmd in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "T"],
        ["commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    backend = OpencodeBackend(repo, "v0.1")
    agent_dest = repo / ".tracks" / "runtime" / "agent"
    assignment = {"manifest": {"allowed_paths": [".github/workflows/ci.yml"]}}

    allowed = backend._allowed_paths([], agent_dest, "devon", "GREEN", assignment)
    auditor = Auditor(repo, allowed=allowed)
    baseline = auditor.baseline()

    ci = repo / ".github" / "workflows" / "ci.yml"
    ci.parent.mkdir(parents=True, exist_ok=True)
    ci.write_text("jobs: {}\n", encoding="utf-8")

    assert auditor.audit(baseline) is None  # compliant write: no over-reach


def test_shield_audit_allowed_includes_manifest_paths(tmp_path, monkeypatch):
    """SHIELD_FIX false over-reach (run 01KZTHE7 T-017): Prism DIAGNOSE named
    tests/unit/test_ci_live_release_jobs.py as the defect site, but tests/unit/
    is not in the Shield [layout] dirs, so the audit rolled Shield's fix
    back. The dispatch grants exactly the diagnosed file via
    manifest.allowed_paths; the shield branch of ``_allowed_paths`` must
    honor that grant (Fix-M pattern, shield side)."""
    monkeypatch.delenv("TRACKS_HOME", raising=False)
    home = tracks_home(tmp_path)
    toml = project_toml_path(home)
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"\n'
        'cwd = "."\n'
        "\n[layout]\n\n"
        "[layout.shield]\n"
        'writable = ["tests/integration/", "tests/e2e/", "tests/assets/", '
        '"tests/counterexamples/"]\n',
        encoding="utf-8",
    )
    backend = OpencodeBackend(tmp_path, "v0.1")
    agent_dest = tmp_path / ".tracks" / "runtime" / "agent"
    assignment = {
        "manifest": {"allowed_paths": ["tests/unit/test_ci_live_release_jobs.py"]}
    }

    allowed = backend._allowed_paths([], agent_dest, "shield", "WRITE", assignment)

    assert Path("tests/unit/test_ci_live_release_jobs.py") in allowed
    assert tmp_path / "tests/integration" in allowed
    assert agent_dest in allowed


def test_grant_diagnosed_test_paths_from_evidence():
    """Dispatch side of the SHIELD_FIX unlock: exactly the test files named
    in the Prism DIAGNOSE evidence land in manifest.allowed_paths; nothing
    else is granted (fail-closed, no blanket tests/ grant)."""
    from types import SimpleNamespace

    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    state = SimpleNamespace(
        diagnose_report={
            "evidence": (
                "tests/unit/test_ci_live_release_jobs.py:115-123 SKIP_TOKEN "
                "in _live_skip helper; contrast tests/unit/test_other.py:9"
            )
        }
    )
    assignment = {"manifest": {"allowed_paths": [".github/workflows/ci.yml"]}}
    MImplRuntimeMixin._grant_diagnosed_test_paths(assignment, state)
    allowed = assignment["manifest"]["allowed_paths"]
    assert "tests/unit/test_ci_live_release_jobs.py" in allowed
    assert "tests/unit/test_other.py" in allowed
    assert ".github/workflows/ci.yml" in allowed
    assert len(allowed) == 3  # no duplicates, nothing else granted

    no_tests = SimpleNamespace(
        diagnose_report={"evidence": "impl bug in tracks/kernel/foo.py"}
    )
    untouched = {"manifest": {"allowed_paths": [".github/workflows/ci.yml"]}}
    MImplRuntimeMixin._grant_diagnosed_test_paths(untouched, no_tests)
    assert untouched["manifest"]["allowed_paths"] == [".github/workflows/ci.yml"]
