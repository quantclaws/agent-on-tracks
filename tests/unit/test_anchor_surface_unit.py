"""OB-1 unit tests for anchor_surface (real subprocess, loader, AST).

Covers: dynamic/AST union, isolation, timeout/infra, loader fail-closed,
AST captures unexecuted imports.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from tracks.executor.anchor_surface import (
    AnchorSurfaceError,
    collect_anchor_surface,
    load_anchor_surface,
    static_imports_of,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_contract(framework: str = "pytest"):
    class C:
        pass

    class Sec:
        pass

    c = C()
    c.framework = framework
    sec = Sec()
    sec.run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q -n 4 --junitxml={result}"
    sec.cwd = "."
    sec.framework = framework
    c.integration = sec
    return c


def _init_mini_repo(base: Path) -> Path:
    """Create a minimal repo with a tracks mini package and git."""
    repo = base / "mini"
    repo.mkdir(parents=True, exist_ok=True)
    # tracks mini_pkg
    pkg = repo / "tracks" / "mini_pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "foo.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (pkg / "bar.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    (pkg / "baz.py").write_text("def baz():\n    return 3\n", encoding="utf-8")
    # git init minimal
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=False)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(repo), check=False)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=False)
    subprocess.run(["git", "add", "."], cwd=str(repo), check=False)
    subprocess.run(["git", "commit", "-m", "init", "-q"], cwd=str(repo), check=False)
    # tests dir
    (repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    return repo


def _write_test(repo: Path, rel: str, content: str) -> str:
    """Write test file under *repo* at *rel* and return nodeid."""
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    # ensure committed for dirty stamp clean? not needed but commit for status
    subprocess.run(["git", "add", str(rel)], cwd=str(repo), check=False)
    subprocess.run(["git", "commit", "-m", f"add {rel}", "-q"], cwd=str(repo), check=False)
    # nodeid from first test func
    # assume single test func named test_*
    return f"{rel}::test_example"


# ---------------------------------------------------------------------------
# static AST
# ---------------------------------------------------------------------------


def test_static_imports_of_captures_unexecuted_import():
    """AST must capture imports inside a function after early assert failure."""
    text = textwrap.dedent(
        """
        def test_example():
            assert False, "early fail"
            import tracks.mini_pkg.foo
            from tracks.mini_pkg.bar import bar
            import tracks.mini_pkg.baz as bz
        """
    )
    mods = static_imports_of(text)
    assert "tracks.mini_pkg.foo" in mods
    assert "tracks.mini_pkg.bar" in mods
    # aliased import still reports full module
    assert "tracks.mini_pkg.baz" in mods


def test_static_imports_of_handles_top_level_and_from_tracks():
    text = textwrap.dedent(
        """
        import tracks.mini_pkg.foo
        from tracks.mini_pkg.bar import something
        from tracks import mini_pkg
        """
    )
    mods = static_imports_of(text)
    assert "tracks.mini_pkg.foo" in mods
    assert "tracks.mini_pkg.bar" in mods
    # from tracks import mini_pkg -> tracks.mini_pkg
    assert "tracks.mini_pkg" in mods


def test_static_imports_of_syntax_error_returns_empty():
    assert static_imports_of("def bad(:") == set()


# ---------------------------------------------------------------------------
# plugin dynamic capture + union
# ---------------------------------------------------------------------------


def test_collect_dynamic_and_static_union(tmp_path: Path):
    repo = _init_mini_repo(tmp_path)
    # anchor file top-level imports foo; also has unexecuted bar inside function
    content = textwrap.dedent(
        """
        import tracks.mini_pkg.foo
        def test_example():
            assert True
            # unexecuted after pass is still static-visible
            from tracks.mini_pkg.bar import bar
        """
    )
    node = _write_test(repo, "tests/integration/test_union.py", content)
    sidecar = collect_anchor_surface(repo, [node], _fake_contract(), jobs=1)
    assert sidecar["schema"] == 1
    entry = sidecar["anchors"][node]
    # outcome should be pass (test passes)
    assert entry["outcome"] in ("pass", "fail")
    mods = set(entry["modules"])
    # both foo (dynamic) and bar (static) should be present (union)
    assert "tracks.mini_pkg.foo" in mods
    assert "tracks.mini_pkg.bar" in mods
    # split fields present and consistent (b89 rev4)
    ast_mods = entry["ast_modules"]
    dyn_mods = entry["dynamic_modules"]
    assert isinstance(ast_mods, list) and isinstance(dyn_mods, list)
    assert "tracks.mini_pkg.foo" in ast_mods  # top-level import in test file
    assert "tracks.mini_pkg.bar" in ast_mods  # function-body import captured by AST
    assert "tracks.mini_pkg.foo" in dyn_mods  # actually loaded in subprocess
    assert entry["modules"] == sorted(set(ast_mods) | set(dyn_mods))
    # anchor_set_digest and recorded_tree present
    assert "anchor_set_digest" in sidecar and sidecar["anchor_set_digest"]
    assert "recorded_tree" in sidecar and sidecar["recorded_tree"]


def test_dynamic_modules_filter_origin(tmp_path: Path, monkeypatch):
    """Dynamic snapshot keeps only modules whose origin lies inside tracks/."""
    import importlib.util
    import types

    from tracks.executor import anchor_surface as surf

    repo = _init_mini_repo(tmp_path)
    inside = (repo / "tracks" / "mini_pkg" / "foo.py").resolve()
    outside = tmp_path / "site-packages" / "evil.py"

    def fake_find_spec(name, package=None):
        if name == "tracks.inside":
            return importlib.util.spec_from_file_location(name, inside)
        if name == "tracks.outside":
            return importlib.util.spec_from_file_location(name, outside)
        if name == "tracks.builtin_thing":
            spec = importlib.util.spec_from_file_location(name, inside)
            spec.origin = "built-in"
            return spec
        return None

    monkeypatch.setattr(surf.importlib.util, "find_spec", fake_find_spec)
    assert surf._is_module_in_tracks("tracks.inside", repo / "tracks") is True
    assert surf._is_module_in_tracks("tracks.outside", repo / "tracks") is False
    assert surf._is_module_in_tracks("tracks.builtin_thing", repo / "tracks") is False
    assert surf._is_module_in_tracks("tracks.missing", repo / "tracks") is False
    assert types  # import kept meaningful


def test_collect_isolation_between_anchors(tmp_path: Path):
    """Per-anchor subprocess must not leak modules between anchors."""
    repo = _init_mini_repo(tmp_path)
    c1 = textwrap.dedent(
        """
        import tracks.mini_pkg.foo
        def test_example():
            assert True
        """
    )
    c2 = textwrap.dedent(
        """
        import tracks.mini_pkg.bar
        def test_example():
            assert True
        """
    )
    n1 = _write_test(repo, "tests/integration/test_iso_a.py", c1)
    # second file maps to different nodeid but same func name
    p2 = repo / "tests/integration/test_iso_b.py"
    p2.parent.mkdir(parents=True, exist_ok=True)
    p2.write_text(c2, encoding="utf-8")
    subprocess.run(["git", "add", "tests/integration/test_iso_b.py"], cwd=str(repo), check=False)
    subprocess.run(["git", "commit", "-m", "add b", "-q"], cwd=str(repo), check=False)
    n2 = "tests/integration/test_iso_b.py::test_example"

    sidecar = collect_anchor_surface(repo, [n1, n2], _fake_contract(), jobs=2)
    e1 = sidecar["anchors"][n1]
    e2 = sidecar["anchors"][n2]
    m1 = set(e1["modules"])
    m2 = set(e2["modules"])
    # each should contain its own only, not the other's (isolation)
    assert "tracks.mini_pkg.foo" in m1
    assert "tracks.mini_pkg.bar" not in m1
    assert "tracks.mini_pkg.bar" in m2
    assert "tracks.mini_pkg.foo" not in m2
    # split fields consistent per anchor
    assert e1["modules"] == sorted(set(e1["ast_modules"]) | set(e1["dynamic_modules"]))
    assert e2["modules"] == sorted(set(e2["ast_modules"]) | set(e2["dynamic_modules"]))
    assert "tracks.mini_pkg.foo" not in e2["dynamic_modules"]
    assert "tracks.mini_pkg.bar" not in e1["dynamic_modules"]


def test_collect_infra_failure_records_error(tmp_path: Path, monkeypatch):
    repo = _init_mini_repo(tmp_path)
    content = textwrap.dedent(
        """
        import tracks.mini_pkg.foo
        def test_example():
            assert True
        """
    )
    node = _write_test(repo, "tests/integration/test_infra.py", content)
    # monkeypatch subprocess.run to simulate timeout for this anchor
    import tracks.executor.anchor_surface as mod

    orig_run = subprocess.run

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "pytest", timeout=120)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    # but _dirty_tree_stamp also uses subprocess.run — need to allow head/status
    # So patch only when argv contains "pytest"
    def selective_fake(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        if isinstance(argv, list) and any("pytest" in str(a) for a in argv):
            raise subprocess.TimeoutExpired(cmd="pytest", timeout=120)
        return orig_run(*args, **kwargs)

    monkeypatch.setattr(mod.subprocess, "run", selective_fake)
    sidecar = mod.collect_anchor_surface(repo, [node], _fake_contract(), jobs=1, timeout=1)
    entry = sidecar["anchors"][node]
    assert entry["outcome"] == "error"
    assert entry["modules"] == []
    assert entry["ast_modules"] == []
    assert entry["dynamic_modules"] == []
    assert "error" in entry


def test_collect_timeout_via_sleep_file(tmp_path: Path):
    """Real subprocess timeout with a sleeping test (short timeout)."""
    repo = _init_mini_repo(tmp_path)
    content = textwrap.dedent(
        """
        import time, tracks.mini_pkg.foo
        def test_example():
            time.sleep(3)
            assert True
        """
    )
    node = _write_test(repo, "tests/integration/test_sleep.py", content)
    # collect with 1s timeout to force error
    sidecar = collect_anchor_surface(repo, [node], _fake_contract(), jobs=1, timeout=1)
    entry = sidecar["anchors"][node]
    assert entry["outcome"] == "error"
    assert entry["modules"] == []


def test_collect_non_pytest_skipped(tmp_path: Path):
    repo = _init_mini_repo(tmp_path)
    sidecar = collect_anchor_surface(repo, ["tests/integration/test_x.py::test_example"], _fake_contract("go"), jobs=1)
    assert sidecar.get("skipped") is True
    assert "skipped_reason" in sidecar
    assert sidecar["anchors"] == {}


# ---------------------------------------------------------------------------
# loader fail-closed matrix
# ---------------------------------------------------------------------------


def test_loader_fail_closed_missing(tmp_path: Path):
    with pytest.raises(AnchorSurfaceError):
        load_anchor_surface(tmp_path / "nope.json")


def test_loader_fail_closed_malformed(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(AnchorSurfaceError):
        load_anchor_surface(p)


def test_loader_fail_closed_schema(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"schema": 2, "anchors": {}, "anchor_set_digest": "a", "recorded_tree": "r"}), encoding="utf-8")
    with pytest.raises(AnchorSurfaceError):
        load_anchor_surface(p)


def test_loader_fail_closed_anchors_illegal(tmp_path: Path):
    for bad in [
        {"schema": 1, "anchors": [], "anchor_set_digest": "a", "recorded_tree": "r"},
        {"schema": 1, "anchors": {"a": {"modules": "notalist", "outcome": "pass"}}, "anchor_set_digest": "a", "recorded_tree": "r"},
        {"schema": 1, "anchors": {"a": {"modules": [], "outcome": "bad"}}, "anchor_set_digest": "a", "recorded_tree": "r"},
        {"schema": 1, "anchors": {"a": {"modules": [], "ast_modules": "bad", "dynamic_modules": [], "outcome": "pass"}}, "anchor_set_digest": "a", "recorded_tree": "r"},
        {"schema": 1, "anchors": {"a": {"modules": [], "ast_modules": [], "dynamic_modules": "bad", "outcome": "pass"}}, "anchor_set_digest": "a", "recorded_tree": "r"},
    ]:
        p = tmp_path / "bad2.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(AnchorSurfaceError):
            load_anchor_surface(p)


def test_loader_ok(tmp_path: Path):
    p = tmp_path / "ok.json"
    data = {"schema": 1, "anchors": {"a": {"modules": ["tracks.foo"], "outcome": "pass"}}, "anchor_set_digest": "abc", "recorded_tree": "def"}
    p.write_text(json.dumps(data), encoding="utf-8")
    out = load_anchor_surface(p)
    assert out["schema"] == 1


def test_loader_ok_split_sidecar(tmp_path: Path):
    """New split sidecar loads; legacy sidecar (no split fields) also loads."""
    p = tmp_path / "split.json"
    data = {
        "schema": 1,
        "anchors": {
            "a": {
                "modules": ["tracks.foo", "tracks.bar"],
                "ast_modules": ["tracks.foo"],
                "dynamic_modules": ["tracks.bar"],
                "outcome": "pass",
            }
        },
        "anchor_set_digest": "abc",
        "recorded_tree": "def",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    out = load_anchor_surface(p)
    assert out["anchors"]["a"]["ast_modules"] == ["tracks.foo"]
    assert out["anchors"]["a"]["dynamic_modules"] == ["tracks.bar"]
    # legacy without split fields still valid
    p2 = tmp_path / "legacy.json"
    p2.write_text(
        json.dumps({"schema": 1, "anchors": {"a": {"modules": ["tracks.foo"], "outcome": "fail"}}, "anchor_set_digest": "x", "recorded_tree": "y"}),
        encoding="utf-8",
    )
    out2 = load_anchor_surface(p2)
    assert out2["anchors"]["a"]["outcome"] == "fail"
