"""Bare filenames in a Prism DIAGNOSE verdict must resolve to repo test paths.

Run 01M0S0FQ v0.7 boundary (2026-08-29): the diagnosis narrated
``hotfix_support.py`` without its tests/ prefix; the literal-path grant
matched nothing and Shield's legal fix was rolled back as over-reach.
"""

from pathlib import Path
from types import SimpleNamespace

from tracks.executor.m_impl_runtime import MImplRuntimeMixin

_REPO = Path(__file__).resolve().parents[2]


def _runtime(tmp_path):
    rt = object.__new__(MImplRuntimeMixin)
    rt.repo = tmp_path
    return rt


def test_unique_bare_name_resolves(tmp_path):
    (tmp_path / "tests" / "_support").mkdir(parents=True)
    (tmp_path / "tests" / "_support" / "hotfix_support.py").write_text("x=1")
    rt = _runtime(tmp_path)
    assert rt._resolve_bare_test_path("hotfix_support.py") == [
        "tests/_support/hotfix_support.py"
    ]


def test_ambiguous_bare_name_grants_nothing(tmp_path):
    for sub in ("integration", "e2e"):
        (tmp_path / "tests" / sub).mkdir(parents=True)
        (tmp_path / "tests" / sub / "helpers.py").write_text("x=1")
    rt = _runtime(tmp_path)
    assert rt._resolve_bare_test_path("helpers.py") == []


def test_missing_bare_name_grants_nothing(tmp_path):
    (tmp_path / "tests").mkdir()
    rt = _runtime(tmp_path)
    assert rt._resolve_bare_test_path("nope.py") == []


def test_pycache_hits_ignored(tmp_path):
    sup = tmp_path / "tests" / "_support"
    sup.mkdir(parents=True)
    (sup / "hotfix_support.py").write_text("x=1")
    pc = sup / "__pycache__"
    pc.mkdir()
    (pc / "hotfix_support.py").write_text("cached")
    rt = _runtime(tmp_path)
    assert rt._resolve_bare_test_path("hotfix_support.py") == [
        "tests/_support/hotfix_support.py"
    ]


def test_absolute_path_resolves_by_basename_without_crash(tmp_path):
    # OOB 2026-09-03 (run 01M19FJVES7G113RD8QXXY3PQZ): evidence embeds
    # absolute paths like PosixPath('/.../demo_host.py'); rglob must not die.
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (tmp_path / "tests" / "integration" / "demo_host.py").write_text("x=1")
    rt = _runtime(tmp_path)
    assert rt._resolve_bare_test_path(
        "/Users/openclaw/workspace/tracks/tracks/executor/demo_host.py"
    ) == ["tests/integration/demo_host.py"]


def test_dir_prefixed_path_resolves_by_basename_without_crash(tmp_path):
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (tmp_path / "tests" / "integration" / "test_a.py").write_text("x=1")
    rt = _runtime(tmp_path)
    assert rt._resolve_bare_test_path("tests/integration/test_a.py") == [
        "tests/integration/test_a.py"
    ]


def test_grant_merges_bare_and_literal_into_manifest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "hotfix_support.py").write_text("x=1")
    rt = _runtime(tmp_path)
    assignment = {"manifest": {"allowed_paths": ["tracks/x.py"]}}
    state = SimpleNamespace(
        diagnose_report={
            "evidence": "e2e helpers.py与hotfix_support.py [adapter]计数=0; see tests/e2e/test_a.py"
        }
    )
    rt._grant_diagnosed_test_paths(assignment, state)
    allowed = assignment["manifest"]["allowed_paths"]
    assert "tests/hotfix_support.py" in allowed
    assert "tests/e2e/test_a.py" in allowed
    assert allowed[0] == "tracks/x.py"  # product entries survive
