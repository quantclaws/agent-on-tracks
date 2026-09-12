"""Behavior coverage for the b89 anchor-surface sidecar (anchor_surface).

Covers the deterministic pieces the CLI/journey layers exercise only in
aggregate: git dirty-stamp shaping, AST/dynamic split, fail-closed sidecar
validation, subprocess run folding (timeout/infra/malformed), parallel
collection, tasks.json anchor loading, blob caching, and the CLI entry.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks.executor import anchor_surface as surf
from tracks.executor.anchor_surface import (
    AnchorSurfaceError,
    _PluginRun,
    collect_anchor_surface,
    load_anchor_surface,
)


def _contract(framework="pytest", run=".venv/bin/python -m pytest tests/ -q"):
    integration = SimpleNamespace(run=run, cwd=".", framework=framework)
    return SimpleNamespace(framework=framework, integration=integration)


# ---------------------------------------------------------------------------
# git stamps
# ---------------------------------------------------------------------------


def test_git_head_success_failure_and_oserror(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    assert surf._git_head(repo) == surf._git_head(repo)
    assert len(surf._git_head(repo)) == 40
    assert surf._git_head(tmp_path / "not-a-repo") == "no-head"

    def boom(*_args, **_kwargs):
        raise OSError("no git binary")

    monkeypatch.setattr(surf.subprocess, "run", boom)
    assert surf._git_head(repo) == "no-head"
    assert surf._git_status_porcelain(repo) == ""


def test_git_status_porcelain_returns_output_only_on_success(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    assert surf._git_status_porcelain(repo) == ""
    (repo / "new.txt").write_text("x", encoding="utf-8")
    assert "?? new.txt" in surf._git_status_porcelain(repo)

    def failed(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(surf.subprocess, "run", failed)
    assert surf._git_status_porcelain(repo) == ""


@pytest.mark.parametrize(
    ("path", "skipped"),
    [
        ("", True),
        (".git/config", True),
        (".tracks/runtime/x", True),
        ("build/lib/x.py", True),
        (".venv/lib/python.py", True),
        (".my-env/bin/python", True),
        ("pkg/__pycache__/x.pyc", True),
        (".coverage", True),
        (".coverage.hostname", True),
        ("src/app.py", False),
        (".github/workflow.yml", False),
    ],
)
def test_is_skipped_stamp_path(path, skipped):
    assert surf._is_skipped_stamp_path(path) is skipped


def test_dirty_tree_stamp_clean_equals_head(tmp_path: Path):
    repo = git_repo(tmp_path)
    assert surf._dirty_tree_stamp(repo) == surf._git_head(repo)


def test_dirty_tree_stamp_hashes_dirty_content_deterministically(tmp_path: Path):
    repo = git_repo(tmp_path)
    (repo / "dirty.txt").write_text("content", encoding="utf-8")
    first = surf._dirty_tree_stamp(repo)
    second = surf._dirty_tree_stamp(repo)
    assert first == second
    assert first != surf._git_head(repo)
    expected = hashlib.sha256(
        json.dumps(
            {
                "head": surf._git_head(repo),
                "dirty": {"dirty.txt": hashlib.sha256(b"content").hexdigest()},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert first == expected


def test_dirty_tree_stamp_skips_structural_paths(tmp_path: Path):
    repo = git_repo(tmp_path)
    for rel in (".venv/lib/x.py", "build/out.txt", ".tracks/state.json"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    assert surf._dirty_tree_stamp(repo) == surf._git_head(repo)


def test_dirty_tree_stamp_unreadable_path_is_marked(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    monkeypatch.setattr(surf, "_git_status_porcelain", lambda _repo: "?? ghost.txt\n")
    stamp = surf._dirty_tree_stamp(repo)
    expected = hashlib.sha256(
        json.dumps(
            {"head": surf._git_head(repo), "dirty": {"ghost.txt": "unreadable"}},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert stamp == expected


def test_anchor_set_digest_is_order_insensitive():
    first = surf._anchor_set_digest(["tests/b.py::t", "tests/a.py::t"])
    second = surf._anchor_set_digest(["tests/a.py::t", "tests/b.py::t"])
    assert first == second
    joined = "\n".join(sorted(["tests/b.py::t", "tests/a.py::t"]))
    assert first == hashlib.sha256(joined.encode("utf-8")).hexdigest()
    assert surf._anchor_set_digest([]) != first


# ---------------------------------------------------------------------------
# AST surface
# ---------------------------------------------------------------------------


def test_import_helpers_filter_and_map_names():
    out: set[str] = set()
    tree = __import__("ast").parse(
        "import tracks, tracks.a, other\nfrom tracks import *\n"
        "from tracks.sub import y\nfrom . import z\n"
    )
    for node in __import__("ast").walk(tree):
        if isinstance(node, __import__("ast").Import):
            surf._add_import_names(node, out)
        elif isinstance(node, __import__("ast").ImportFrom):
            surf._add_importfrom_names(node, out)
    assert out == {"tracks", "tracks.a", "tracks.sub"}


def test_importfrom_none_module_is_ignored():
    import ast

    out: set[str] = set()
    surf._add_importfrom_names(
        ast.ImportFrom(module=None, names=[ast.alias(name="x")], level=0), out
    )
    assert out == set()


def test_find_tracks_dir_walks_up_or_falls_back(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "tracks").mkdir(parents=True)
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    found_repo, found_tracks = surf._find_tracks_dir(nested)
    assert found_repo == repo.resolve()
    assert found_tracks == (repo / "tracks").resolve()
    bare = tmp_path / "bare"
    bare.mkdir()
    with monkeypatch.context() as m:
        m.setattr(Path, "is_dir", lambda _self: False)
        fallback_repo, fallback_tracks = surf._find_tracks_dir(bare)
    assert fallback_repo == bare.resolve()
    assert fallback_tracks == (bare / "tracks").resolve()


def test_is_module_in_tracks_shape_guards(monkeypatch):
    def raising(_name):
        raise ImportError("boom")

    monkeypatch.setattr(surf.importlib.util, "find_spec", raising)
    assert surf._is_module_in_tracks("tracks.x", Path("tracks")) is False
    monkeypatch.setattr(surf.importlib.util, "find_spec", lambda _name: None)
    assert surf._is_module_in_tracks("tracks.x", Path("tracks")) is False
    monkeypatch.setattr(
        surf.importlib.util, "find_spec", lambda _name: SimpleNamespace(origin=None)
    )
    assert surf._is_module_in_tracks("tracks.x", Path("tracks")) is False


def test_pytest_sessionfinish_writes_dynamic_snapshot(tmp_path: Path, monkeypatch):
    out = tmp_path / "surface" / "out.json"
    monkeypatch.setenv("TRACKS_SURFACE_OUT", str(out))
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    surf.pytest_sessionfinish(SimpleNamespace(), exitstatus=0)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["outcome"] == "pass"
    assert "tracks.executor.anchor_surface" in data["modules"]
    assert data["modules"] == sorted(set(data["modules"]))

    surf.pytest_sessionfinish(SimpleNamespace(), exitstatus=1)
    assert json.loads(out.read_text(encoding="utf-8"))["outcome"] == "fail"


def test_pytest_sessionfinish_noop_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TRACKS_SURFACE_OUT", raising=False)
    monkeypatch.chdir(tmp_path)
    surf.pytest_sessionfinish(SimpleNamespace(), exitstatus=0)
    assert list(tmp_path.iterdir()) == []


def test_pytest_sessionfinish_swallows_write_errors(tmp_path: Path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    monkeypatch.setenv("TRACKS_SURFACE_OUT", str(blocker))
    monkeypatch.chdir(tmp_path)
    surf.pytest_sessionfinish(SimpleNamespace(), exitstatus=0)
    assert blocker.is_dir()


# ---------------------------------------------------------------------------
# contract / loader
# ---------------------------------------------------------------------------


def test_is_pytest_contract_matrix():
    assert surf._is_pytest_contract(SimpleNamespace(framework="pytest", integration=None))
    assert surf._is_pytest_contract(
        SimpleNamespace(framework=None, integration=SimpleNamespace(framework="pytest"))
    )
    assert not surf._is_pytest_contract(SimpleNamespace(framework="go", integration=None))
    assert not surf._is_pytest_contract(
        SimpleNamespace(framework=None, integration=SimpleNamespace(framework="go"))
    )
    assert not surf._is_pytest_contract(SimpleNamespace())
    assert not surf._is_pytest_contract(
        SimpleNamespace(framework=None, integration=SimpleNamespace())
    )


def test_validate_sidecar_dict_closed_shape(tmp_path: Path):
    good = {
        "schema": 1,
        "anchors": {},
        "anchor_set_digest": "digest",
        "recorded_tree": "tree",
    }
    surf._validate_sidecar_dict(good, tmp_path / "x.json")
    for bad, message in (
        ([], "top level must be object"),
        ({**good, "schema": 2}, "schema must be 1"),
        ({**good, "anchors": []}, "anchors must be object"),
        ({**good, "anchor_set_digest": ""}, "anchor_set_digest"),
        ({**good, "recorded_tree": 7}, "recorded_tree"),
    ):
        with pytest.raises(AnchorSurfaceError, match=message):
            surf._validate_sidecar_dict(bad, tmp_path / "x.json")


def test_validate_anchor_entry_closed_shape():
    surf._validate_anchor_entry(
        "anchor",
        {
            "modules": ["tracks.a"],
            "ast_modules": ["tracks.a"],
            "dynamic_modules": [],
            "outcome": "pass",
        },
    )
    surf._validate_anchor_entry("anchor", {"modules": [], "outcome": "error"})
    for key, entry, message in (
        ("", {"modules": [], "outcome": "pass"}, "anchor key must be non-empty"),
        ("a", [], "entry must be object"),
        ("a", {"modules": "x", "outcome": "pass"}, "modules must be list of strings"),
        ("a", {"modules": [1], "outcome": "pass"}, "modules must be list of strings"),
        (
            "a",
            {"modules": [], "ast_modules": "x", "outcome": "pass"},
            "ast_modules must be list of strings",
        ),
        (
            "a",
            {"modules": [], "dynamic_modules": [1], "outcome": "pass"},
            "dynamic_modules must be list of strings",
        ),
        ("a", {"modules": [], "outcome": "maybe"}, "outcome must be pass|fail|error"),
    ):
        with pytest.raises(AnchorSurfaceError, match=message):
            surf._validate_anchor_entry(key, entry)


def test_load_anchor_surface_rejects_missing_and_malformed(tmp_path: Path):
    with pytest.raises(AnchorSurfaceError, match="file missing"):
        load_anchor_surface(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(AnchorSurfaceError, match="json malformed"):
        load_anchor_surface(bad)


# ---------------------------------------------------------------------------
# subprocess surface
# ---------------------------------------------------------------------------


def test_python_bin_for_repo_prefers_local_env(tmp_path: Path):
    repo = tmp_path / "repo"
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    assert surf._python_bin_for_repo(repo) == str(python)
    assert surf._python_bin_for_repo(tmp_path / "nope") == surf.sys.executable


def test_make_skipped_sidecar_reason_names_both_frameworks():
    contract = SimpleNamespace(framework="go", integration=SimpleNamespace(framework=None))
    sidecar = surf._make_skipped_sidecar("d", "t", contract)
    assert sidecar["skipped"] is True
    assert "framework='go'" in sidecar["skipped_reason"]
    assert "integration.framework=None" in sidecar["skipped_reason"]
    sidecar2 = surf._make_skipped_sidecar("d", "t", SimpleNamespace())
    assert "integration.framework=None" in sidecar2["skipped_reason"]


def test_read_plugin_output_shapes(tmp_path: Path):
    assert surf._read_plugin_output(tmp_path / "missing.json", 1) == (
        [],
        "error",
        "surface file missing",
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    modules, outcome, error = surf._read_plugin_output(bad, 1)
    assert modules == [] and outcome == "error" and "json malformed" in error
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"modules": ["tracks.a"], "outcome": "pass"}), encoding="utf-8")
    assert surf._read_plugin_output(good, 1) == (["tracks.a"], "pass", "")
    weird = tmp_path / "weird.json"
    weird.write_text(json.dumps({"modules": [], "outcome": "weird"}), encoding="utf-8")
    assert surf._read_plugin_output(weird, 1)[1] == "fail"


def test_split_surface_scans_static_imports(tmp_path: Path):
    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "import tracks.executor.taskgraph\n"
        "import tracks.does_not.exist\n"
        "import json\n",
        encoding="utf-8",
    )
    ast_mods, dyn_mods = surf._split_surface(
        "tests/test_x.py::test_x", repo, ["tracks.a", "tracks.a"]
    )
    assert ast_mods == ["tracks.does_not.exist", "tracks.executor.taskgraph"]
    assert dyn_mods == ["tracks.a"]
    assert surf._split_surface("tests/missing.py::t", repo, []) == ([], [])


def test_split_surface_read_failure_yields_no_ast(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("import tracks.executor.taskgraph\n", encoding="utf-8")

    def denied(_self, **_kwargs):
        raise OSError("denied")

    monkeypatch.setattr(Path, "read_text", denied)
    assert surf._split_surface("tests/test_x.py::t", repo, ["tracks.a"]) == (
        [],
        ["tracks.a"],
    )


def test_split_surface_drops_unmappable_modules(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "import tracks.executor.taskgraph\nimport tracks.keep\n", encoding="utf-8"
    )
    from tracks.executor import taskgraph

    real = taskgraph._module_to_path

    def flaky(module, repo_path=None):
        if module == "tracks.keep":
            raise RuntimeError("unmappable")
        return real(module, repo_path)

    monkeypatch.setattr(taskgraph, "_module_to_path", flaky)
    ast_mods, _ = surf._split_surface("tests/test_x.py::t", repo, [])
    assert ast_mods == ["tracks.executor.taskgraph"]


def test_split_surface_survives_taskgraph_import_failure(tmp_path: Path, monkeypatch):
    import sys

    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("import tracks.executor.taskgraph\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "tracks.executor.taskgraph", None)
    ast_mods, _ = surf._split_surface("tests/test_x.py::t", repo, [])
    assert ast_mods == ["tracks.executor.taskgraph"]


def test_anchor_run_env_and_argv(tmp_path: Path):
    repo = tmp_path / "repo"
    plugin = tmp_path / "plugin"
    env = surf._anchor_run_env(repo, str(plugin), tmp_path / "out.json")
    assert env["TRACKS_SURFACE_OUT"] == str(tmp_path / "out.json")
    assert env["PYTHONPATH"] == str(repo) + surf.os.pathsep + str(plugin)
    same = surf._anchor_run_env(repo, str(repo), tmp_path / "out.json")
    assert same["PYTHONPATH"] == str(repo)
    argv = surf._anchor_run_argv("python-bin", "tests/test_x.py::test_x")
    assert argv == [
        "python-bin",
        "-m",
        "pytest",
        "tests/test_x.py::test_x",
        "-p",
        "tracks.executor.anchor_surface",
        "--no-header",
        "-q",
    ]


def _stub_run(returncode: int = 0, stdout: str = ""):
    def fake(*_args, **_kwargs):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    return fake


def test_run_anchor_plugin_reads_surface_file(tmp_path: Path, monkeypatch):
    surface = tmp_path / "surface.json"
    surface.write_text(json.dumps({"modules": ["tracks.a"], "outcome": "pass"}), encoding="utf-8")
    monkeypatch.setattr(surf.subprocess, "run", _stub_run())
    run = surf._run_anchor_plugin(tmp_path, ["pytest"], {}, 5, surface)
    assert run == _PluginRun(["tracks.a"], "pass", "")


def test_run_anchor_plugin_missing_and_malformed_surface(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(surf.subprocess, "run", _stub_run(stdout="child out"))
    run = surf._run_anchor_plugin(tmp_path, ["pytest"], {}, 5, tmp_path / "absent.json")
    assert run.outcome == "error"
    assert "surface file missing (rc=0)" in run.error_msg
    assert "child out" in run.error_msg
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    run = surf._run_anchor_plugin(tmp_path, ["pytest"], {}, 5, bad)
    assert "surface json malformed" in run.error_msg


def test_run_anchor_plugin_timeout_and_oserror(tmp_path: Path, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=5)

    monkeypatch.setattr(surf.subprocess, "run", timeout)
    run = surf._run_anchor_plugin(tmp_path, ["pytest"], {}, 5, tmp_path / "x.json")
    assert run.outcome == "error" and run.error_msg.startswith("timeout after 5s")

    def os_error(*_args, **_kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(surf.subprocess, "run", os_error)
    run = surf._run_anchor_plugin(tmp_path, ["pytest"], {}, 5, tmp_path / "x.json")
    assert run.error_msg == "infra failure: exec format error"


def test_run_anchor_plugin_marks_error_when_reader_reports_error(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(
        surf, "_read_plugin_output", lambda _tmp, _timeout: (["tracks.a"], "pass", "oops")
    )
    monkeypatch.setattr(surf.subprocess, "run", _stub_run())
    run = surf._run_anchor_plugin(tmp_path, ["pytest"], {}, 5, tmp_path)
    assert run == _PluginRun([], "error", "oops")


def test_anchor_surface_entry_splits_only_on_ok_runs(tmp_path: Path):
    repo = tmp_path / "repo"
    test_file = repo / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("import tracks.executor.taskgraph\n", encoding="utf-8")
    entry = surf._anchor_surface_entry(
        "tests/test_x.py::test_x", repo, _PluginRun(["tracks.dyn"], "pass", "")
    )
    assert entry["outcome"] == "pass"
    assert entry["ast_modules"] == ["tracks.executor.taskgraph"]
    assert entry["dynamic_modules"] == ["tracks.dyn"]
    assert entry["modules"] == ["tracks.dyn", "tracks.executor.taskgraph"]
    error_entry = surf._anchor_surface_entry(
        "tests/test_x.py::test_x", repo, _PluginRun(["tracks.dyn"], "error", "boom")
    )
    assert error_entry["modules"] == [] and error_entry["ast_modules"] == []
    assert error_entry["error"] == "boom"
    silent = surf._anchor_surface_entry(
        "tests/test_x.py::test_x", repo, _PluginRun([], "error", "")
    )
    assert "error" not in silent


def test_run_one_anchor_cleans_temp_file(tmp_path: Path, monkeypatch):
    seen: dict = {}

    def fake_run(repo, argv, env, timeout, tmp_path):
        seen["tmp"] = tmp_path
        tmp_path.write_text("{}", encoding="utf-8")
        return _PluginRun(["tracks.a"], "pass", "")

    monkeypatch.setattr(surf, "_run_anchor_plugin", fake_run)
    anchor, entry = surf._run_one_anchor(
        "tests/test_x.py::test_x", tmp_path, "python", str(tmp_path), 5
    )
    assert anchor == "tests/test_x.py::test_x"
    assert entry["outcome"] == "pass"
    assert not seen["tmp"].exists()


def test_sidecar_payload_optional_skip_reason():
    plain = surf._sidecar_payload("d", "t", {})
    assert "skipped" not in plain and "skipped_reason" not in plain
    skipped = surf._sidecar_payload("d", "t", {}, "why")
    assert skipped["skipped"] is True and skipped["skipped_reason"] == "why"


def test_contract_run_error_matrix():
    assert surf._contract_run_error(SimpleNamespace(integration=None)) is None
    assert surf._contract_run_error(
        SimpleNamespace(integration=SimpleNamespace(run=123))
    ) is None
    assert surf._contract_run_error(
        SimpleNamespace(integration=SimpleNamespace(run="   "))
    ) is None
    assert surf._contract_run_error(
        SimpleNamespace(integration=SimpleNamespace(run="pytest tests/"))
    ) is None
    reason = surf._contract_run_error(
        SimpleNamespace(integration=SimpleNamespace(run="pytest 'unclosed"))
    )
    assert reason is not None and "unparsable" in reason


# ---------------------------------------------------------------------------
# collection orchestration
# ---------------------------------------------------------------------------


def test_future_result_folds_exceptions_to_error():
    good: Future = Future()
    good.set_result(("anchor-a", {"outcome": "pass"}))
    assert surf._future_result({good: "anchor-a"}, good) == (
        "anchor-a",
        {"outcome": "pass"},
    )
    bad: Future = Future()
    bad.set_exception(RuntimeError("worker died"))
    anchor, entry = surf._future_result({bad: "anchor-b"}, bad)
    assert anchor == "anchor-b"
    assert entry == {"modules": [], "outcome": "error", "error": "worker died"}


def test_collect_parallel_results_maps_anchors(monkeypatch, tmp_path: Path):
    def fake_one(anchor, repo, python_bin, plugin_root_str, timeout):
        return anchor, {"modules": [anchor], "outcome": "pass"}

    monkeypatch.setattr(surf, "_run_one_anchor", fake_one)
    results = surf._collect_parallel_results(
        ["a", "b", "c"], tmp_path, "python", str(tmp_path), 5, 2
    )
    assert sorted(results) == ["a", "b", "c"]
    assert results["b"]["modules"] == ["b"]


def test_collect_anchor_results_serial_and_parallel_dispatch(monkeypatch, tmp_path: Path):
    calls: list[str] = []

    def fake_one(anchor, repo, python_bin, plugin_root_str, timeout):
        calls.append(anchor)
        return anchor, {"modules": [], "outcome": "pass"}

    monkeypatch.setattr(surf, "_run_one_anchor", fake_one)
    serial = surf._collect_anchor_results(["a"], tmp_path, "python", str(tmp_path), None, 5)
    assert sorted(serial) == ["a"]
    surf._collect_anchor_results(["a", "b"], tmp_path, "python", str(tmp_path), 0, 5)
    assert calls == ["a", "a", "b"]

    parallel_calls: list[tuple] = []

    def fake_parallel(anchors, repo, python_bin, plugin_root_str, timeout, jobs):
        parallel_calls.append((list(anchors), jobs))
        return {anchor: {"modules": [], "outcome": "pass"} for anchor in anchors}

    monkeypatch.setattr(surf, "_collect_parallel_results", fake_parallel)
    results = surf._collect_anchor_results(
        ["a", "b"], tmp_path, "python", str(tmp_path), 4, 5
    )
    assert sorted(results) == ["a", "b"]
    assert parallel_calls == [(["a", "b"], 4)]
    one = surf._collect_anchor_results(["a"], tmp_path, "python", str(tmp_path), 4, 5)
    assert sorted(one) == ["a"]


def test_collect_anchor_surface_skips_unparsable_contract(tmp_path: Path):
    repo = git_repo(tmp_path)
    contract = _contract(run="pytest 'unclosed")
    sidecar = collect_anchor_surface(repo, ["tests/a.py::t"], contract, jobs=1)
    assert sidecar["skipped"] is True
    assert "unparsable" in sidecar["skipped_reason"]
    assert sidecar["anchors"] == {}


def test_collect_anchor_surface_empty_anchors_short_circuits(tmp_path: Path):
    repo = git_repo(tmp_path)
    sidecar = collect_anchor_surface(repo, [], _contract(), jobs=1)
    assert sidecar["anchors"] == {}
    assert "skipped" not in sidecar
    assert sidecar["anchor_set_digest"] == surf._anchor_set_digest([])


# ---------------------------------------------------------------------------
# tasks.json / blob cache / CLI
# ---------------------------------------------------------------------------


def test_load_anchors_from_tasks_uses_acceptance_then_test_refs(tmp_path: Path):
    version_dir = tmp_path / "v0.8"
    version_dir.mkdir()
    (version_dir / "tasks.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"acceptance_refs": ["tests/b.py::t", "tests/a.py", "docs/x.md"]},
                    {"test_refs": ["tests/c.py"]},
                    {"test_refs": ["tests/a.py", 7]},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert surf._load_anchors_from_tasks(version_dir) == [
        "tests/a.py",
        "tests/b.py::t",
        "tests/c.py",
    ]


def test_cache_sidecar_blob_writes_content_addressed_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sidecar = {"schema": 1, "anchors": {}}
    surf._cache_sidecar_blob(repo, sidecar)
    expected = hashlib.sha256(
        json.dumps(sidecar, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached = repo / ".tracks" / "runtime" / "blobs" / expected
    assert cached.is_file()
    assert json.loads(cached.read_text(encoding="utf-8")) == sidecar


def test_cache_sidecar_blob_is_best_effort(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".tracks").write_text("blocker", encoding="utf-8")
    surf._cache_sidecar_blob(repo, {"schema": 1})


def test_write_sidecar_default_relative_and_absolute_paths(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    version_dir = repo / "v0.8"
    version_dir.mkdir()
    assert surf._write_sidecar(repo, version_dir, None, {"schema": 1}, ["a"]) == 0
    default = version_dir / "anchor-surface.json"
    assert json.loads(default.read_text(encoding="utf-8")) == {"schema": 1}
    assert "anchor-surface written to" in capsys.readouterr().out

    assert surf._write_sidecar(repo, version_dir, "custom/out.json", {"schema": 1}, []) == 0
    assert (repo / "custom" / "out.json").is_file()
    absolute = tmp_path / "absolute.json"
    assert surf._write_sidecar(repo, version_dir, absolute, {"schema": 1}, []) == 0
    assert absolute.is_file()


def _collect_args(tmp_path: Path, **overrides):
    values = {
        "projects_dir": str(tmp_path / "projects"),
        "version": "v0.8",
        "out": None,
        "jobs": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_main_collect_missing_tasks_returns_2(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert surf._main_collect(_collect_args(tmp_path)) == 2
    assert "tasks.json not found" in capsys.readouterr().err


def test_main_collect_unparseable_tasks_returns_2(tmp_path: Path, monkeypatch, capsys):
    projects = tmp_path / "projects" / "v0.8"
    projects.mkdir(parents=True)
    (projects / "tasks.json").write_text("{bad", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    args = _collect_args(tmp_path, projects_dir="projects")
    assert surf._main_collect(args) == 2
    assert "failed to load anchors" in capsys.readouterr().err


def test_main_collect_no_contract_returns_2(tmp_path: Path, monkeypatch, capsys):
    version_dir = tmp_path / "projects" / "v0.8"
    version_dir.mkdir(parents=True)
    (version_dir / "tasks.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    import tracks.project

    def no_contract(_repo):
        raise RuntimeError("no contract")

    monkeypatch.setattr(tracks.project, "load_contract", no_contract)
    args = _collect_args(tmp_path, projects_dir="projects")
    assert surf._main_collect(args) == 2
    assert "no contract" in capsys.readouterr().err


def test_main_collect_writes_sidecar(tmp_path: Path, monkeypatch, capsys):
    version_dir = tmp_path / "projects" / "v0.8"
    version_dir.mkdir(parents=True)
    (version_dir / "tasks.json").write_text(
        json.dumps({"tasks": [{"test_refs": ["tests/a.py::t"]}]}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    import tracks.project

    monkeypatch.setattr(tracks.project, "load_contract", lambda _repo: _contract())
    monkeypatch.setattr(
        surf, "collect_anchor_surface", lambda *a, **kw: {"schema": 1, "anchors": {}}
    )
    args = _collect_args(tmp_path, projects_dir="projects")
    assert surf._main_collect(args) == 0
    assert (version_dir / "anchor-surface.json").is_file()


def test_main_cli_parses_collect_arguments(tmp_path: Path, monkeypatch):
    captured: dict = {}

    def fake_collect(args):
        captured.update(vars(args))
        return 7

    monkeypatch.setattr(surf, "_main_collect", fake_collect)
    assert surf.main(["collect", "--version", "v0.8", "--jobs", "3"]) == 7
    assert captured["version"] == "v0.8"
    assert captured["jobs"] == 3
    assert captured["projects_dir"] == ".tracks/projects"
    assert captured["out"] is None
    assert surf.main(["collect", "--version", "v0.8", "--out", "x.json"]) == 7
    assert captured["out"] == "x.json"


def test_main_cli_requires_subcommand():
    with pytest.raises(SystemExit):
        surf.main([])


def test_module_main_guard_exits_without_args(monkeypatch):
    import runpy
    import sys
    import warnings

    monkeypatch.setattr(sys, "argv", ["tracks.executor.anchor_surface"])
    with warnings.catch_warnings(), pytest.raises(SystemExit) as excinfo:
        warnings.simplefilter("ignore")
        runpy.run_module("tracks.executor.anchor_surface", run_name="__main__")
    assert excinfo.value.code == 2
