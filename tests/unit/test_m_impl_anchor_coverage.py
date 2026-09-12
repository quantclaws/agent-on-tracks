"""Behavior coverage for ``MImplAnchorMixin`` and its module helpers:

anchor-edge aggregation, sidecar digest resolution, role-specific assignment
context grading, diagnosed-path grants and the Devon evidence validators.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import m_impl_anchor as anchor
from tracks.executor.m_impl_anchor import MImplAnchorMixin


class _Cmd:
    command_id = "CMD-1"


def _state(**overrides) -> SimpleNamespace:
    base = {
        "current_task_id": "T-001",
        "current_attempt": 0,
        "current_task_metadata": {},
        "current_manifest": {},
        "r_tree_identity": "r" * 40,
        "task_refs": [],
        "hotfix_issue": None,
        "hotfix_anchor_acs": [],
        "diagnose_report": None,
        "taskgraph_digest": "",
        "stage": "M-IMPL",
        "substate": "RED",
        "version": "v0.8",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Host(MImplAnchorMixin):
    def __init__(self, tmp_path: Path, vdir: Path | None = None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.vdir = vdir or (tmp_path / "vdir")
        self.vdir.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.emitted: list = []
        self.store = SimpleNamespace()

    def _vdir(self):
        return self.vdir

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _devon_red_test_dirs(self):
        return ["tests/unit"]

    def _rebuild_task_log_projection(self):
        return None

    def _dirty_snapshot(self):
        return {}


# -- module helpers ---------------------------------------------------------


def test_manifest_path_matches_rules():
    assert anchor._manifest_path_matches("tracks/a.py", "tracks/**") is True
    assert anchor._manifest_path_matches("tracks", "tracks/**") is True
    assert anchor._manifest_path_matches("other/a.py", "tracks/**") is False
    assert anchor._manifest_path_matches("tracks/a.py", "tracks/a.py") is True
    assert anchor._manifest_path_matches("tracks/sub/a.py", "tracks") is True
    assert anchor._manifest_path_matches("tracksx/a.py", "tracks") is False


def test_combined_provenance_deduplicates():
    task = SimpleNamespace(ac_refs=("AC-1", "AC-2"), fr_refs=("AC-2", "FR-1"))
    assert anchor._combined_provenance(task) == ["AC-1", "AC-2", "FR-1"]


def test_devon_path_scope_error_branches():
    assert anchor._devon_path_scope_error(["/abs/x"], set(), set(), []) == (
        "Devon evidence path is not repo-relative: /abs/x"
    )
    assert anchor._devon_path_scope_error(["a/../b"], set(), set(), []) == (
        "Devon evidence path is not repo-relative: a/../b"
    )
    assert anchor._devon_path_scope_error(
        ["tests/unit/test_a.py"], set(), {"tests/**"}, ["tests/unit"]
    ) is None
    assert anchor._devon_path_scope_error(
        ["tracks/a.py"], set(), {"tracks/**"}, []
    ) == "Devon evidence path is forbidden: tracks/a.py"
    assert anchor._devon_path_scope_error(
        ["tracks/a.py"], {"tracks/b.py"}, set(), []
    ) == "Devon evidence path is outside manifest: tracks/a.py"
    assert anchor._devon_path_scope_error(
        ["tracks/a.py"], {"tracks/**"}, set(), []
    ) is None


def test_is_evidence_shape_error_branches():
    assert anchor._is_evidence_shape_error(None) is False
    assert anchor._is_evidence_shape_error("") is False
    assert anchor._is_evidence_shape_error("changed_paths contains an invalid path") is True
    assert anchor._is_evidence_shape_error("Devon evidence missing: phase") is True
    assert anchor._is_evidence_shape_error("unrelated failure") is False


def test_anchor_entry_totals_skips_non_dict_entries():
    entry_ast, ast_total, dyn_total = anchor._anchor_entry_totals(
        {
            "a": {"ast_modules": ["tracks.a"], "dynamic_modules": ["tracks.b"]},
            "b": "not-a-dict",
        }
    )
    assert entry_ast == {"a": ["tracks.a"]}
    assert (ast_total, dyn_total) == (1, 1)


def _index(task_scopes=None, all_scopes=None, owner_map=None):
    return SimpleNamespace(
        task_scopes=task_scopes or {},
        all_scopes=all_scopes or set(),
        owner_map=owner_map or {},
    )


def test_task_anchor_edges_fallback_and_missing_anchor(monkeypatch):
    monkeypatch.setattr(anchor, "_closure_scopes", lambda index, task_id: set())
    task = SimpleNamespace(
        task_id="T-001",
        acceptance_refs=None,
        test_refs=("anchor-1", None, ""),
    )
    edges: dict = {}
    index = _index(task_scopes={"T-001": set()})
    anchor._task_anchor_edges(index, task, {}, {}, edges)
    assert edges == {("T-001", "", ""): {"anchor-1"}}


def test_task_anchor_edges_cross_scope_ownership(monkeypatch):
    monkeypatch.setattr(anchor, "_closure_scopes", lambda index, task_id: set())
    task = SimpleNamespace(
        task_id="T-001",
        acceptance_refs=("anchor-1",),
        test_refs=(),
    )
    index = _index(
        task_scopes={"T-001": set()},
        all_scopes={"tracks/b.py"},
        owner_map={"tracks/b.py": "T-002"},
    )
    edges: dict = {}
    anchor._task_anchor_edges(index, task, {"anchor-1": {}}, {"anchor-1": ["tracks.b"]}, edges)
    assert edges == {("T-001", "tracks/b.py", "T-002"): {"anchor-1"}}


def test_anchor_violations_grouping_and_advisories(monkeypatch):
    tasks = [SimpleNamespace(task_id="T-001"), SimpleNamespace(task_id="T-002")]
    edges = {
        ("T-001", "tracks/b.py", "T-002"): {"anchor-2", "anchor-1"},
    }
    violations = anchor._anchor_violations(tasks, edges)
    assert violations["T-001"]["missing_edges"] == [
        {
            "owner_task": "T-002",
            "module": "tracks/b.py",
            "anchors": ["anchor-1", "anchor-2"],
        }
    ]
    assert violations["T-002"]["missing_edges"] == []

    monkeypatch.setattr(
        "tracks.executor.taskgraph.validate_anchor_satisfiability",
        lambda tasks, surface: (_ for _ in ()).throw(RuntimeError("gate down")),
    )
    assert anchor._anchor_advisories_count(tasks, {}) == 0


# -- sidecar digest ---------------------------------------------------------


def test_sidecar_digest_and_ref_blob_and_fallback(tmp_path):
    host = _Host(tmp_path)
    sidecar = host.vdir / "anchor-surface.json"
    sidecar.write_text("{}", encoding="utf-8")
    surface = {"anchors": {}}
    digest, ref = host._sidecar_digest_and_ref(surface, sidecar)
    assert ref == str(sidecar)

    import hashlib

    expected = hashlib.sha256(
        json.dumps(surface, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert digest == expected
    blob = host.repo / ".tracks" / "runtime" / "blobs" / expected
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text("x", encoding="utf-8")
    digest, ref = host._sidecar_digest_and_ref(surface, sidecar)
    assert (digest, ref) == (expected, f".tracks/runtime/blobs/{expected}")


def test_sidecar_digest_relative_inside_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    vdir = repo / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True)
    host = _Host(tmp_path, vdir)
    sidecar = vdir / "anchor-surface.json"
    sidecar.write_text("{}", encoding="utf-8")
    digest, ref = host._sidecar_digest_and_ref({"anchors": {}}, sidecar)
    assert digest
    assert ref == ".tracks/projects/v0.8/anchor-surface.json"


def test_sidecar_digest_exception_and_outside_repo(tmp_path):
    host = _Host(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    surface = {"bad": {1, 2}}
    digest, ref = host._sidecar_digest_and_ref(surface, outside)
    assert digest == ""
    assert ref == str(outside)


# -- assignment context grading --------------------------------------------


def test_context_grading_archer_infra_and_devon_slices(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    assignment: dict = {}
    monkeypatch.setattr(
        host,
        "_anchor_surface_for_assignment",
        lambda: (_ for _ in ()).throw(RuntimeError("sidecar exploded")),
    )
    host._apply_assignment_context_grading(
        assignment,
        {"role": "archer", "substate": "PLANNING"},
        None,
        host.vdir,
        host.vdir / "acceptance.md",
    )
    assert assignment["anchor_surface"] == {"unavailable": "infra: sidecar exploded"}
    assert assignment["test_tasks"] is None

    devon: dict = {}
    task = {
        "ac_refs": ["AC-FR0001-01"],
        "acceptance_refs": ["tests/integration/test_a.py::t"],
        "if_ids": ["IF-IMPL-001"],
    }
    host._apply_assignment_context_grading(
        devon,
        {"role": "devon", "substate": "RED"},
        task,
        host.vdir,
        host.vdir / "acceptance.md",
    )
    assert devon["test_tasks"] == [
        {
            "ac_id": "AC-FR0001-01",
            "anchors": ["tests/integration/test_a.py::t"],
            "if_ids": ["IF-IMPL-001"],
        }
    ]

    assert host._devon_test_tasks_slice(None) == []
    assert host._devon_test_tasks_slice({"test_refs": ["x"], "ac_refs": []}) == []


def test_context_grading_unknown_role_and_shield_slice(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    assignment = {"anchor_surface": "x", "test_tasks": "x"}
    host._apply_assignment_context_grading(
        assignment, {"role": "sage", "substate": "RULING"}, None, host.vdir, None
    )
    assert assignment["anchor_surface"] is None
    assert assignment["test_tasks"] is None

    acc = host.vdir / "acceptance.md"
    plan = host.vdir / "test-plan.md"
    acc.write_text("", encoding="utf-8")
    plan.write_text("", encoding="utf-8")
    task = {"ac_refs": ["AC-FR0001-01"]}
    valid_incoming = [
        {"ac_id": "AC-FR0002-01", "layers": ["integration"], "if_ids": ["IF-IMPL-001"]}
    ]
    monkeypatch.setattr(anchor, "parse_test_tasks", lambda *_a: [])
    assert host._shield_test_tasks_slice(task, acc, plan, valid_incoming) == valid_incoming
    assert host._shield_test_tasks_slice(task, acc, plan, None) == []

    parsed = [
        {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-IMPL-001"]},
        {"ac_id": "AC-FR0009-99", "layers": ["e2e"], "if_ids": ["IF-IMPL-002"]},
    ]
    monkeypatch.setattr(anchor, "parse_test_tasks", lambda *_a: parsed)
    assert host._shield_test_tasks_slice(task, acc, plan, valid_incoming) == [parsed[0]]


def test_set_test_tasks_ref_outside_repo(tmp_path):
    host = _Host(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    assignment: dict = {}
    host._set_test_tasks_ref(assignment, outside)
    assert assignment["test_tasks_ref"] == f"{outside}/test-plan.md#8"


# -- diagnosed path grants --------------------------------------------------


def test_grant_diagnosed_test_paths_branches(tmp_path):
    host = _Host(tmp_path)
    assignment = {"manifest": {"allowed_paths": []}}
    host._grant_diagnosed_test_paths(
        assignment, _state(diagnose_report={"evidence": {"nested": "no path"}})
    )
    assert assignment["manifest"]["allowed_paths"] == []

    host._grant_diagnosed_test_paths(
        assignment,
        _state(diagnose_report={"evidence": "defect in tests/unit/test_x.py here"}),
    )
    assert assignment["manifest"]["allowed_paths"] == ["tests/unit/test_x.py"]

    no_manifest = {"manifest": None}
    host._grant_diagnosed_test_paths(
        no_manifest,
        _state(diagnose_report={"evidence": "tests/unit/test_x.py"}),
    )
    assert no_manifest == {"manifest": None}


def test_resolve_bare_test_path_fail_closed(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    assert host._resolve_bare_test_path("hotfix_support.py") == []

    tests_dir = host.repo / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    assert host._resolve_bare_test_path("/") == []

    monkeypatch.setattr(
        Path,
        "rglob",
        lambda self, pattern: (_ for _ in ()).throw(NotImplementedError()),
    )
    assert host._resolve_bare_test_path("hotfix_support.py") == []
    monkeypatch.undo()

    (tests_dir / "hotfix_support.py").write_text("", encoding="utf-8")
    assert host._resolve_bare_test_path("hotfix_support.py") == [
        "tests/unit/hotfix_support.py"
    ]

    other = host.repo / "tests" / "integration"
    other.mkdir(parents=True)
    (other / "hotfix_support.py").write_text("", encoding="utf-8")
    assert host._resolve_bare_test_path("hotfix_support.py") == []


def test_bare_filename_grant_resolves_single_match(tmp_path):
    host = _Host(tmp_path)
    tests_dir = host.repo / "tests" / "unit"
    tests_dir.mkdir(parents=True)
    (tests_dir / "unique_helper.py").write_text("", encoding="utf-8")
    assignment = {"manifest": {"allowed_paths": []}}
    host._grant_diagnosed_test_paths(
        assignment, _state(diagnose_report={"evidence": "see unique_helper.py"})
    )
    assert "tests/unit/unique_helper.py" in assignment["manifest"]["allowed_paths"]


# -- assignment validation --------------------------------------------------


def _devon_assignment(**overrides) -> dict:
    base = {
        "task_id": "T-001",
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0001-01"],
        "test_refs": ["tests/unit/test_a.py::t"],
        "commands": {"unit": ["pytest"]},
        "manifest": {"allowed_paths": ["tracks/a.py"]},
        "phase": "red",
        "pre_dirty_snapshot": {},
        "result_identity": "r1",
    }
    base.update(overrides)
    return base


def test_invalid_m_impl_assignment_shield_and_devon(tmp_path):
    host = _Host(tmp_path)
    shield_error = host._invalid_m_impl_assignment(
        "shield", "WRITE", {"test_tasks": [{"ac_id": "bad"}]}
    )
    assert shield_error is not None and "test_tasks is invalid" in shield_error

    green = host._invalid_m_impl_assignment(
        "devon", "GREEN", _devon_assignment(phase="green")
    )
    assert green == "Devon assignment missing: r_tree_identity"

    assert host._invalid_m_impl_assignment(
        "devon", "GREEN", _devon_assignment(phase="green", r_tree_identity="r")
    ) is None
    assert host._invalid_m_impl_assignment("sage", "RULING", None) is None


# -- Devon evidence validators ----------------------------------------------


def _outcome(**overrides) -> dict:
    base = {
        "phase": "red",
        "changed_paths": ["tests/unit/test_a.py"],
        "commands": [],
        "manifest_compliance": True,
        "pre_identity": "a",
        "post_identity": "b",
        "implemented_if_ids": [],
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (_outcome(phase="green"), "Devon evidence phase mismatch: expected red"),
        (_outcome(changed_paths="x"), "Devon evidence changed_paths must be a list"),
        (
            _outcome(changed_paths=["  "]),
            "Devon evidence changed_paths contains an invalid path",
        ),
        (_outcome(commands="x"), "Devon evidence commands must be a list"),
        (
            _outcome(manifest_compliance=False),
            "Devon manifest_compliance is not true",
        ),
        (
            _outcome(pre_identity=None),
            "Devon evidence is missing pre/post identity",
        ),
        (
            _outcome(implemented_if_ids="x"),
            "Devon evidence implemented_if_ids must be a list",
        ),
    ],
)
def test_devon_evidence_fields_errors(outcome, expected):
    assert MImplAnchorMixin._devon_evidence_fields_error("red", outcome) == expected


def test_devon_evidence_missing_keys_and_r_identity():
    assert MImplAnchorMixin._devon_evidence_fields_error("red", {}) == (
        "Devon evidence missing: phase, changed_paths, commands, manifest_compliance, "
        "pre_identity, post_identity, implemented_if_ids"
    )
    green = _outcome(phase="green")
    assert MImplAnchorMixin._devon_evidence_fields_error("green", green) == (
        "Devon evidence missing r_identity"
    )
    assert MImplAnchorMixin._devon_evidence_change_error(
        "red", _outcome(changed_paths=[])
    ) == "Devon RED evidence has no changed paths"
    assert MImplAnchorMixin._devon_evidence_change_error(
        "refactor", _outcome(phase="refactor", changed_paths=[])
    ) == "Devon REFACTOR evidence needs changed paths or no_change_reason"
    assert MImplAnchorMixin._devon_evidence_change_error(
        "refactor",
        _outcome(phase="refactor", changed_paths=[], no_change_reason="none"),
    ) is None


def test_devon_evidence_scope_error_branches(tmp_path):
    host = _Host(tmp_path)
    state = _state(
        current_task_metadata={"if_ids": ["IF-IMPL-001"]},
        current_manifest={"allowed_paths": ["tracks/a.py"], "forbidden_paths": []},
    )
    claimed = _outcome(phase="green", implemented_if_ids=["IF-OTHER-999"])
    assert host._devon_evidence_scope_error("green", state, claimed) == (
        "Devon evidence claims an IF id outside the current task"
    )

    outside = _outcome(phase="green", changed_paths=["other/b.py"])
    assert host._devon_evidence_scope_error("green", state, outside) == (
        "Devon evidence path is outside manifest: other/b.py"
    )

    red = _outcome(phase="red", changed_paths=["tests/unit/test_a.py", "tracks/a.py"])
    assert host._devon_evidence_scope_error("red", state, red) == (
        "Devon RED evidence includes a non-test path"
    )


def test_anchor_surface_for_assignment_error_paths(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    tasks_path = host.vdir / "tasks.json"
    tasks_path.write_text("{not json", encoding="utf-8")
    result = host._anchor_surface_for_assignment()
    assert "parse error" in result["unavailable"]

    tasks_path.unlink()
    tasks_path.mkdir()
    result = host._anchor_surface_for_assignment()
    assert "unreadable" in result["unavailable"]

    tasks_path.rmdir()
    tasks_path.write_text(json.dumps({"schema": 2, "tasks": []}), encoding="utf-8")
    assert host._anchor_surface_for_assignment() == {"unavailable": "no tasks"}

    tasks_path.write_text(
        json.dumps(
            {
                "schema": 2,
                "tasks": [
                    {
                        "task_id": "T-001",
                        "issue_number": 1,
                        "description": "t",
                        "ac_refs": ["AC-FR0001-01"],
                        "fr_refs": ["FR-0001"],
                        "if_ids": ["IF-IMPL-001"],
                        "scope_boundary": "tracks/a.py",
                        "depends_on": [],
                        "batch": "1",
                        "parallel": False,
                        "unit_refs": [],
                        "acceptance_refs": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (host.vdir / "anchor-surface.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "anchors": {},
                "anchor_set_digest": "d",
                "recorded_tree": "r",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        host,
        "_anchor_surface_summary",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("summary blew up")),
    )
    result = host._anchor_surface_for_assignment()
    assert result == {"unavailable": "infra: summary blew up"}


def test_invalid_m_impl_assignment_no_repo_host():
    host = object.__new__(MImplAnchorMixin)
    assert host._assignment_key_missing({}, "task_id") is True
    assert host._assignment_key_missing({"ac_refs": ()}, "ac_refs") is True
    assert host._assignment_key_missing({"ac_refs": ["x"]}, "ac_refs") is False
