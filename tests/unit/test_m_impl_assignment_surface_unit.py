"""OB-5 unit tests: assignment anchor_surface injection shape."""

from __future__ import annotations

import json
from pathlib import Path

from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.taskgraph import TaskNode


def make_task(task_id: str, scope: str, depends_on=None, acceptance_refs=None, schema=2) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        issue_number=1,
        description=f"task {task_id}",
        ac_refs=("AC-FR0001-01",),
        fr_refs=("FR-0001",),
        if_ids=("IF-IMPL-001",),
        test_refs=tuple(acceptance_refs or []),
        scope_boundary=scope,
        depends_on=tuple(depends_on or []),
        batch="1",
        parallel=False,
        budget=2,
        unit_refs=(),
        acceptance_refs=tuple(acceptance_refs or []),
        schema=schema,
    )


class _FakeStoreHome:
    def __init__(self, home: Path):
        self.home = Path(home)


class _FakeState:
    pass


def _fake_mixin(vdir: Path, repo: Path):
    """Return a MImplRuntimeMixin-shaped object with _vdir and repo."""
    obj = MImplRuntimeMixin.__new__(MImplRuntimeMixin)
    obj.repo = Path(repo)
    obj.store = _FakeStoreHome(Path(repo) / ".tracks")
    obj.version = "v0.8"
    # Bind _vdir to return vdir
    obj._vdir = lambda: Path(vdir)  # type: ignore[method-assign]
    return obj


def test_assignment_surface_schema1_returns_unavailable(tmp_path: Path):
    vdir = tmp_path / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    repo = tmp_path
    # schema-1 tasks.json
    data = {
        "schema": 1,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "t",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": False,
                "test_refs": ["tests/integration/test_a.py::test_x"],
            }
        ],
    }
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    mixin = _fake_mixin(vdir, repo)
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" in result
    assert isinstance(result["unavailable"], str)
    assert "schema-1" in result["unavailable"]


def test_assignment_surface_missing_tasks_json_returns_unavailable(tmp_path: Path):
    vdir = tmp_path / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    repo = tmp_path
    mixin = _fake_mixin(vdir, repo)
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" in result
    assert "tasks.json missing" in result["unavailable"]


def test_assignment_surface_missing_sidecar_returns_unavailable(tmp_path: Path):
    vdir = tmp_path / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    repo = tmp_path
    data = {
        "schema": 2,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "t",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": False,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_a.py::test_x"],
            }
        ],
    }
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    mixin = _fake_mixin(vdir, repo)
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" in result
    assert "sidecar" in result["unavailable"].lower()


def test_assignment_surface_valid_shape(tmp_path: Path):
    vdir = tmp_path / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    repo = tmp_path
    # Create tracks files so scope existence not needed here
    (repo / "tracks").mkdir()
    (repo / "tracks" / "foo.py").write_text("", encoding="utf-8")
    (repo / "tracks" / "bar.py").write_text("", encoding="utf-8")
    # tasks: T-001 depends on nothing but anchor exercises bar owned by T-002
    data = {
        "schema": 2,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "t1",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": False,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_a.py::test_x"],
            },
            {
                "task_id": "T-002",
                "issue_number": 2,
                "description": "t2",
                "ac_refs": ["AC-FR0002-01"],
                "fr_refs": ["FR-0002"],
                "if_ids": ["IF-IMPL-002"],
                "scope_boundary": "tracks/bar.py",
                "depends_on": [],
                "batch": "1",
                "parallel": False,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_b.py::test_y"],
            },
        ],
    }
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    # sidecar that makes T-001 missing edge for bar
    sidecar = {
        "schema": 1,
        "anchors": {
            "tests/integration/test_a.py::test_x": {"modules": ["tracks.bar"], "outcome": "fail"},
            "tests/integration/test_b.py::test_y": {"modules": ["tracks.bar"], "outcome": "pass"},
        },
        "anchor_set_digest": "d",
        "recorded_tree": "r",
    }
    (vdir / "anchor-surface.json").write_text(json.dumps(sidecar), encoding="utf-8")
    mixin = _fake_mixin(vdir, repo)
    result = mixin._anchor_surface_for_assignment()
    # Valid shape: task_id keys + advisories, no unavailable
    assert "unavailable" not in result
    assert "T-001" in result
    assert "T-002" in result
    assert "advisories" in result
    assert isinstance(result["advisories"], list)
    # T-001 should have one missing edge entry
    t1_list = result["T-001"]
    assert isinstance(t1_list, list)
    assert len(t1_list) == 1
    entry = t1_list[0]
    assert set(entry.keys()) >= {"anchor", "module", "owner_task", "depends_missing"}
    assert entry["anchor"] == "tests/integration/test_a.py::test_x"
    assert entry["module"] == "tracks/bar.py"
    assert entry["owner_task"] == "T-002"
    assert entry["depends_missing"] is True
    # T-002 owns bar itself, so no violation
    assert result["T-002"] == []


def test_assignment_surface_advisories_shape(tmp_path: Path):
    vdir = tmp_path / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    repo = tmp_path
    (repo / "tracks").mkdir()
    (repo / "tracks" / "foo.py").write_text("", encoding="utf-8")
    data = {
        "schema": 2,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "t",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": False,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_a.py::test_x"],
            }
        ],
    }
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    sidecar = {
        "schema": 1,
        "anchors": {
            "tests/integration/test_a.py::test_x": {"modules": ["tracks.unowned"], "outcome": "pass"},
        },
        "anchor_set_digest": "d",
        "recorded_tree": "r",
    }
    (vdir / "anchor-surface.json").write_text(json.dumps(sidecar), encoding="utf-8")
    mixin = _fake_mixin(vdir, repo)
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" not in result
    assert "advisories" in result
    assert any("unowned" in adv for adv in result["advisories"])
    # No task violations, empty list
    assert result["T-001"] == []


def test_assignment_surface_unavailable_shape_is_compatible(tmp_path: Path):
    """schema-1 compatible: unavailable marker is a dict with string reason, no extra keys required."""
    vdir = tmp_path / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    repo = tmp_path
    data = {
        "schema": 1,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "t",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": False,
                "test_refs": ["tests/integration/test_a.py::test_x"],
            }
        ],
    }
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    mixin = _fake_mixin(vdir, repo)
    result = mixin._anchor_surface_for_assignment()
    # Shape check: must be dict with unavailable string, json-serializable
    assert isinstance(result, dict)
    assert "unavailable" in result
    assert isinstance(result["unavailable"], str)
    # json round-trip
    assert json.loads(json.dumps(result)) == result
