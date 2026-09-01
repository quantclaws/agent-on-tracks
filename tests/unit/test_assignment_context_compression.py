"""OOB b92 unit tests: assignment context compression (T1 minimal protocol).

Pure in-memory / tmp-dir fixtures: no subprocess, no ``trac``. Exercises
``MImplRuntimeMixin._anchor_surface_for_assignment`` (R1 aggregated AST-only
injector) and ``_apply_assignment_context_grading`` (R3 role grading) against
the locked contract:

- ``missing_edges`` grouped per ``(owner_task, module)`` with ``module`` a
  single string and ``anchors`` the deduped sorted trigger set (no
  ``{modules[], anchors[]}`` double array / cartesian pairing);
- ``expand(missing_edges)`` equals ``validate_anchor_satisfiability``'s AST
  violations set;
- advisory strings (``imports unowned tracks module`` / ``dynamically
  loads``) never appear in any role's assignment payload;
- ``dynamic_modules`` detail stays out of the payload by default;
- Devon and Shield test_tasks slices are shape-split (F-02).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tracks.effects.backend import valid_test_tasks
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.taskgraph import TaskNode, parse_tasks_json, validate_anchor_satisfiability

_TEST_PLAN = (
    "# Test plan\n\n## 8. AC Coverage\n\n"
    "| AC id | layer | test | IF |\n|---|---|---|---|\n"
    "| AC-FR0001-01 | integration | tests/unit/test_app.py | IF-IMPL-001 |\n"
)
_ACCEPTANCE = "# Acceptance\n\n### AC-FR0001-01\n\n- observable\n"


def _fake_mixin(vdir: Path, repo: Path):
    obj = MImplRuntimeMixin.__new__(MImplRuntimeMixin)
    obj.repo = Path(repo)
    obj.version = "v0.8"
    obj._vdir = lambda: Path(vdir)  # type: ignore[method-assign]
    return obj


def _mk_env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (repo, vdir, mixin) with acceptance/test-plan docs written."""
    repo = tmp_path
    vdir = repo / ".tracks" / "projects" / "v0.8"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(_ACCEPTANCE, encoding="utf-8")
    (vdir / "test-plan.md").write_text(_TEST_PLAN, encoding="utf-8")
    return repo, vdir, _fake_mixin(vdir, repo)


def _write_tasks(vdir: Path, tasks: list[dict]) -> None:
    (vdir / "tasks.json").write_text(json.dumps({"schema": 2, "tasks": tasks}), encoding="utf-8")


def _write_sidecar(vdir: Path, anchors: dict) -> None:
    sidecar = {
        "schema": 1,
        "anchors": anchors,
        "anchor_set_digest": "d",
        "recorded_tree": "tree-stamp",
    }
    (vdir / "anchor-surface.json").write_text(json.dumps(sidecar), encoding="utf-8")


def _task_dict(
    task_id: str,
    scope: str,
    depends_on: list[str] | None = None,
    acceptance_refs: list[str] | None = None,
    ac_refs: list[str] | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "issue_number": 1,
        "description": f"task {task_id}",
        "ac_refs": ac_refs or ["AC-FR0001-01"],
        "fr_refs": ["FR-0001"],
        "if_ids": ["IF-IMPL-001"],
        "scope_boundary": scope,
        "depends_on": depends_on or [],
        "batch": "1",
        "parallel": False,
        "unit_refs": [],
        "acceptance_refs": acceptance_refs or [],
    }


def _node(task: dict) -> TaskNode:
    return TaskNode(
        task_id=task["task_id"],
        issue_number=task["issue_number"],
        description=task["description"],
        ac_refs=tuple(task["ac_refs"]),
        fr_refs=tuple(task["fr_refs"]),
        if_ids=tuple(task["if_ids"]),
        test_refs=tuple(task["acceptance_refs"]),
        scope_boundary=task["scope_boundary"],
        depends_on=tuple(task["depends_on"]),
        batch=task["batch"],
        parallel=task["parallel"],
        budget=2,
        unit_refs=(),
        acceptance_refs=tuple(task["acceptance_refs"]),
        schema=2,
    )


def _archer_assignment(mixin, vdir: Path) -> dict:
    assignment: dict = {"role": "archer", "substate": "PLANNING"}
    mixin._apply_assignment_context_grading(
        assignment, {"role": "archer", "substate": "PLANNING"}, None, vdir, vdir / "acceptance.md"
    )
    return assignment


def _gate_violation_set(tasks: list[TaskNode], surface: dict) -> set[tuple[str, str, str]]:
    """Parse the gate's violation messages into {(task_id, module, anchor)}.

    Message forms (taskgraph.validate_anchor_satisfiability):
    - ``{tid} anchor {a} exercises {path} owned by {owner} without depends_on edge``
    - ``anchor {a} missing from anchor-surface sidecar``
    """
    out: set[tuple[str, str, str]] = set()
    _ok, violations, _adv = validate_anchor_satisfiability(tasks, surface)
    pat = re.compile(r"^(?:(?P<tid>\S+) )?anchor (?P<anchor>\S+) exercises (?P<path>\S+) owned by")
    for msg in violations:
        m = pat.match(msg)
        if m:
            out.add((m.group("tid") or "", m.group("path"), m.group("anchor")))
        else:
            m2 = re.match(r"^anchor (?P<anchor>\S+) missing from anchor-surface sidecar$", msg)
            assert m2, f"unparsed gate violation message: {msg}"
            out.add(("", "", m2.group("anchor")))
    return out


def _expand(violations: dict) -> set[tuple[str, str, str]]:
    """Locked expand definition: {(task_id, module, anchor)}."""
    out: set[tuple[str, str, str]] = set()
    for task_id, payload in violations.items():
        for edge in payload["missing_edges"]:
            for anchor in edge["anchors"]:
                out.add((task_id, edge["module"], anchor))
    return out


# -- T1 test_anchor_surface_shape_is_aggregated -------------------------------


def test_anchor_surface_shape_is_aggregated(tmp_path: Path):
    repo, vdir, mixin = _mk_env(tmp_path)
    _write_tasks(
        vdir,
        [
            _task_dict(
                "T-001", "tracks/xfoo.py", acceptance_refs=["tests/integration/test_a1.py::test_a1"]
            ),
            _task_dict(
                "T-002", "tracks/xbar.py", acceptance_refs=["tests/integration/test_b1.py::test_b1"]
            ),
        ],
    )
    _write_sidecar(
        vdir,
        {
            "tests/integration/test_a1.py::test_a1": {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": ["tracks.zdyn_a", "tracks.zdyn_b"],
                "modules": ["tracks.xbar", "tracks.zdyn_a", "tracks.zdyn_b"],
                "outcome": "fail",
            },
            "tests/integration/test_b1.py::test_b1": {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": [],
                "modules": ["tracks.xbar"],
                "outcome": "pass",
            },
        },
    )
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" not in result
    edges = result["violations"]["T-001"]["missing_edges"]
    assert edges == [
        {
            "owner_task": "T-002",
            "module": "tracks/xbar.py",
            "anchors": ["tests/integration/test_a1.py::test_a1"],
        }
    ]
    for edge in edges:
        assert set(edge.keys()) == {"owner_task", "module", "anchors"}
        assert isinstance(edge["module"], str)
        assert edge["anchors"] == sorted(set(edge["anchors"]))
        assert "modules" not in edge  # no double-array form
    # locked top-level fields
    for key in (
        "advisories_ref",
        "advisories_count",
        "advisories_digest",
        "sidecar_digest",
        "recorded_tree",
        "stats",
    ):
        assert key in result
    assert "advisories" not in result  # advisory strings stay externalized
    assert result["recorded_tree"] == "tree-stamp"
    assert result["stats"] == {
        "ast_modules_total": 2,
        "dynamic_modules_total": 2,
        "anchors_total": 2,
    }
    assert result["advisories_digest"].startswith("sha256:")
    assert result["sidecar_digest"]


# -- T1 test_no_advisory_string_in_payload ------------------------------------


def test_no_advisory_string_in_payload(tmp_path: Path):
    repo, vdir, mixin = _mk_env(tmp_path)
    _write_tasks(
        vdir,
        [
            _task_dict(
                "T-001", "tracks/xfoo.py", acceptance_refs=["tests/integration/test_a1.py::test_a1"]
            ),
            _task_dict(
                "T-002", "tracks/xbar.py", acceptance_refs=["tests/integration/test_b1.py::test_b1"]
            ),
        ],
    )
    _write_sidecar(
        vdir,
        {
            "tests/integration/test_a1.py::test_a1": {
                "ast_modules": ["tracks.xbar", "tracks.zunowned"],
                "dynamic_modules": ["tracks.zdyn_only", "tracks.zunowned"],
                "modules": ["tracks.xbar", "tracks.zunowned", "tracks.zdyn_only"],
                "outcome": "fail",
            },
            "tests/integration/test_b1.py::test_b1": {
                "ast_modules": ["tracks.zunowned"],
                "dynamic_modules": ["tracks.zdyn_only"],
                "modules": ["tracks.zunowned", "tracks.zdyn_only"],
                "outcome": "pass",
            },
        },
    )
    task = _task_dict(
        "T-001",
        "tracks/xfoo.py",
        acceptance_refs=["tests/integration/test_a1.py::test_a1"],
    )
    role_params = [
        {"role": "archer", "substate": "PLANNING"},
        {"role": "prism", "substate": "PRISM_PLAN"},
        {"role": "devon", "substate": "RED"},
        {"role": "devon", "substate": "GREEN"},
        {"role": "devon", "substate": "REFACTOR"},
        {"role": "shield", "substate": "WRITE"},
        {"role": "prism", "substate": "DIAGNOSE"},
        {"role": "unknown", "substate": "WHATEVER"},
    ]
    for params in role_params:
        assignment: dict = {"role": params["role"], "substate": params["substate"]}
        mixin._apply_assignment_context_grading(
            assignment, params, task, vdir, vdir / "acceptance.md"
        )
        payload = json.dumps(assignment, ensure_ascii=False, sort_keys=True)
        assert "imports unowned tracks module" not in payload, params
        assert "dynamically loads" not in payload, params


# -- T1 test_dynamic_modules_not_in_prompt_by_default -------------------------


def test_dynamic_modules_not_in_prompt_by_default(tmp_path: Path):
    repo, vdir, mixin = _mk_env(tmp_path)
    _write_tasks(
        vdir,
        [
            _task_dict(
                "T-001", "tracks/xfoo.py", acceptance_refs=["tests/integration/test_a1.py::test_a1"]
            ),
            _task_dict(
                "T-002", "tracks/xbar.py", acceptance_refs=["tests/integration/test_b1.py::test_b1"]
            ),
        ],
    )
    _write_sidecar(
        vdir,
        {
            "tests/integration/test_a1.py::test_a1": {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": ["tracks.zdyn_a", "tracks.zdyn_b", "tracks.zdyn_c"],
                "modules": ["tracks.xbar", "tracks.zdyn_a", "tracks.zdyn_b", "tracks.zdyn_c"],
                "outcome": "fail",
            },
            "tests/integration/test_b1.py::test_b1": {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": ["tracks.zdyn_a"],
                "modules": ["tracks.xbar", "tracks.zdyn_a"],
                "outcome": "pass",
            },
        },
    )
    assignment = _archer_assignment(mixin, vdir)
    payload = json.dumps(assignment, ensure_ascii=False, sort_keys=True)
    # no dynamic_modules *detail*: the only allowed occurrence is the
    # count-only stats key "dynamic_modules_total"
    assert "tracks.zdyn_a" not in payload  # no per-module dynamic detail
    assert "dynamic_modules_total" in payload
    assert payload.replace("dynamic_modules_total", "").count("dynamic_modules") == 0
    # count-only visibility for audit
    assert assignment["anchor_surface"]["stats"]["dynamic_modules_total"] == 4


# -- T1 test_role_grading ------------------------------------------------------


def test_role_grading(tmp_path: Path):
    repo, vdir, mixin = _mk_env(tmp_path)
    _write_tasks(
        vdir,
        [
            _task_dict(
                "T-001", "tracks/xfoo.py", acceptance_refs=["tests/integration/test_a1.py::test_a1"]
            ),
            _task_dict(
                "T-002", "tracks/xbar.py", acceptance_refs=["tests/integration/test_b1.py::test_b1"]
            ),
        ],
    )
    _write_sidecar(
        vdir,
        {
            "tests/integration/test_a1.py::test_a1": {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": [],
                "modules": ["tracks.xbar"],
                "outcome": "fail",
            },
        },
    )
    task = _task_dict(
        "T-001",
        "tracks/xfoo.py",
        depends_on=["T-002"],
        acceptance_refs=["tests/integration/test_a1.py::test_a1"],
    )

    # Archer PLANNING: aggregated anchor_surface, no full test_tasks
    assignment: dict = {"role": "archer", "substate": "PLANNING"}
    mixin._apply_assignment_context_grading(
        assignment, {"role": "archer", "substate": "PLANNING"}, task, vdir, vdir / "acceptance.md"
    )
    assert "unavailable" not in assignment["anchor_surface"]
    assert "violations" in assignment["anchor_surface"]
    assert assignment["test_tasks"] is None
    assert assignment["test_tasks_count"] == 1
    assert assignment["test_tasks_ref"].endswith("test-plan.md#8")

    # Prism PRISM_PLAN: same form as Archer
    assignment = {"role": "prism", "substate": "PRISM_PLAN"}
    mixin._apply_assignment_context_grading(
        assignment, {"role": "prism", "substate": "PRISM_PLAN"}, task, vdir, vdir / "acceptance.md"
    )
    assert "violations" in assignment["anchor_surface"]
    assert assignment["test_tasks"] is None

    # Devon: anchors-form slice, anchor_surface None
    for substate in ("RED", "GREEN", "REFACTOR"):
        assignment = {"role": "devon", "substate": substate}
        mixin._apply_assignment_context_grading(
            assignment, {"role": "devon", "substate": substate}, task, vdir, vdir / "acceptance.md"
        )
        assert assignment["anchor_surface"] is None
        assert assignment["test_tasks"] == [
            {
                "ac_id": "AC-FR0001-01",
                "anchors": ["tests/integration/test_a1.py::test_a1"],
                "if_ids": ["IF-IMPL-001"],
            }
        ]
        assert len(assignment["test_tasks"]) <= 5

    # Shield WRITE: layers-form slice, anchor_surface None
    assignment = {"role": "shield", "substate": "WRITE"}
    mixin._apply_assignment_context_grading(
        assignment, {"role": "shield", "substate": "WRITE"}, task, vdir, vdir / "acceptance.md"
    )
    assert assignment["anchor_surface"] is None
    assert assignment["test_tasks"] == [
        {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-IMPL-001"]}
    ]

    # Prism DIAGNOSE / unknown roles: nothing injected
    for params in (
        {"role": "prism", "substate": "DIAGNOSE"},
        {"role": "writer", "substate": "DRAFT"},
    ):
        assignment = {"role": params["role"], "substate": params["substate"]}
        mixin._apply_assignment_context_grading(
            assignment, params, task, vdir, vdir / "acceptance.md"
        )
        assert assignment["anchor_surface"] is None
        assert assignment["test_tasks"] is None


# -- T1 test_expand_equals_gate ------------------------------------------------


def test_expand_equals_gate(tmp_path: Path):
    """Multi-module / multi-anchor broken graph: expand(violations) == gate set,
    with no cartesian pseudo-combinations (F-01 regression)."""
    repo, vdir, mixin = _mk_env(tmp_path)
    a1 = "tests/integration/test_a1.py::test_a1"
    a2 = "tests/integration/test_a2.py::test_a2"
    a3 = "tests/integration/test_a3.py::test_a3"
    a4 = "tests/integration/test_a4.py::test_a4"
    tasks = [
        _task_dict("T-001", "tracks/xfoo.py", depends_on=[], acceptance_refs=[a1, a2, a3, a4]),
        _task_dict("T-002", "tracks/xbar.py", acceptance_refs=[]),
        _task_dict("T-003", "tracks/xbaz.py", acceptance_refs=[]),
    ]
    _write_tasks(vdir, tasks)
    _write_sidecar(
        vdir,
        {
            # two modules owned by different tasks + two anchors on the same module
            a1: {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": [],
                "modules": ["tracks.xbar"],
                "outcome": "fail",
            },
            a2: {
                "ast_modules": ["tracks.xbaz"],
                "dynamic_modules": [],
                "modules": ["tracks.xbaz"],
                "outcome": "fail",
            },
            a3: {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": [],
                "modules": ["tracks.xbar"],
                "outcome": "fail",
            },
            a4: {
                "ast_modules": ["tracks.xbar"],
                "dynamic_modules": [],
                "modules": ["tracks.xbar"],
                "outcome": "fail",
            },
        },
    )
    raw = (vdir / "tasks.json").read_text(encoding="utf-8")
    parsed, err = parse_tasks_json(raw)
    assert err is None
    nodes = [_node(t) for t in tasks]
    # sanity: the gate itself reports the broken edges
    gate_set = _gate_violation_set(nodes, json.loads((vdir / "anchor-surface.json").read_text()))
    assert gate_set == {
        ("T-001", "tracks/xbar.py", a1),
        ("T-001", "tracks/xbar.py", a3),
        ("T-001", "tracks/xbar.py", a4),
        ("T-001", "tracks/xbaz.py", a2),
    }
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" not in result
    edges = result["violations"]["T-001"]["missing_edges"]
    # module-grouped: xbar carries its three real anchors, xbaz its one
    assert edges == [
        {
            "owner_task": "T-002",
            "module": "tracks/xbar.py",
            "anchors": [a1, a3, a4],
        },
        {
            "owner_task": "T-003",
            "module": "tracks/xbaz.py",
            "anchors": [a2],
        },
    ]
    expanded = _expand(result["violations"])
    assert expanded == gate_set
    # no cartesian blow-up: 4 real pairs, not 3x1+... cross products
    assert len(expanded) == 4
    assert len(edges) == 2


# -- T1 test_shield_test_tasks_shape_valid -------------------------------------


def test_shield_test_tasks_shape_valid(tmp_path: Path):
    """F-02 regression: Shield WRITE keeps the valid_test_tasks layers form and
    the dispatch validation does not reject the assignment."""
    repo, vdir, mixin = _mk_env(tmp_path)
    _write_tasks(
        vdir,
        [
            _task_dict(
                "T-001", "tracks/xfoo.py", acceptance_refs=["tests/integration/test_a1.py::test_a1"]
            )
        ],
    )
    _write_sidecar(vdir, {})
    task = _task_dict("T-001", "tracks/xfoo.py")
    assignment: dict = {"role": "shield", "substate": "WRITE"}
    mixin._apply_assignment_context_grading(
        assignment,
        {"role": "shield", "substate": "WRITE"},
        task,
        vdir,
        vdir / "acceptance.md",
    )
    assert assignment["anchor_surface"] is None
    assert valid_test_tasks(assignment["test_tasks"])
    assert mixin._invalid_m_impl_assignment("shield", "WRITE", assignment) is None
    # shape preserved: never rewritten to the Devon anchors form
    for entry in assignment["test_tasks"]:
        assert set(entry.keys()) == {"ac_id", "layers", "if_ids"}
        assert "anchors" not in entry


# -- T1 test_legacy_sidecar_fallback_is_bounded --------------------------------


def test_legacy_sidecar_fallback_is_bounded(tmp_path: Path):
    repo, vdir, mixin = _mk_env(tmp_path)
    _write_tasks(
        vdir,
        [
            _task_dict(
                "T-001", "tracks/xfoo.py", acceptance_refs=["tests/integration/test_a1.py::test_a1"]
            ),
            _task_dict(
                "T-002", "tracks/xbar.py", acceptance_refs=["tests/integration/test_b1.py::test_b1"]
            ),
        ],
    )
    # legacy entry: no ast_modules/dynamic_modules split; 60 "dynamic" noise
    # modules (unowned) plus one genuinely owned hard edge
    legacy_mods = ["tracks.xbar"] + [f"tracks.zfake{i:02d}" for i in range(60)]
    _write_sidecar(
        vdir,
        {
            "tests/integration/test_a1.py::test_a1": {"modules": legacy_mods, "outcome": "fail"},
            "tests/integration/test_b1.py::test_b1": {"modules": legacy_mods, "outcome": "pass"},
        },
    )
    result = mixin._anchor_surface_for_assignment()
    assert "unavailable" not in result
    edges = result["violations"]["T-001"]["missing_edges"]
    # aggregated per module, not a per-(anchor, module) depends_missing expansion
    assert edges == [
        {
            "owner_task": "T-002",
            "module": "tracks/xbar.py",
            "anchors": ["tests/integration/test_a1.py::test_a1"],
        }
    ]
    assert "modules" not in edges[0]
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
    # advisory strings stay zero-tolerance on the legacy fallback path too (F-05)
    assert "imports unowned" not in payload
    assert "dynamically loads" not in payload
    assert "zfake00" not in payload  # unowned noise never enters the payload
    # bounded: legacy noise does not blow the 15KB anchor_surface budget
    assert len(payload.encode("utf-8")) <= 15 * 1024
    # advisory count still visible via the gate-consistent counter
    assert result["advisories_count"] >= 60


# -- T1 test_unavailable_markers ------------------------------------------------


def test_unavailable_markers(tmp_path: Path):
    repo, vdir, mixin = _mk_env(tmp_path)
    # sidecar missing
    _write_tasks(vdir, [_task_dict("T-001", "tracks/xfoo.py")])
    result = mixin._anchor_surface_for_assignment()
    assert result == {"unavailable": mixin._anchor_surface_for_assignment()["unavailable"]}
    assert "sidecar" in result["unavailable"].lower()

    # schema-1 tasks.json
    (vdir / "tasks.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "tasks": [
                    {
                        "task_id": "T-001",
                        "issue_number": 1,
                        "description": "t",
                        "ac_refs": ["AC-FR0001-01"],
                        "fr_refs": ["FR-0001"],
                        "if_ids": ["IF-IMPL-001"],
                        "scope_boundary": "tracks/xfoo.py",
                        "depends_on": [],
                        "batch": "1",
                        "parallel": False,
                        "test_refs": ["tests/integration/test_a1.py::test_a1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_sidecar(vdir, {})
    result = mixin._anchor_surface_for_assignment()
    assert "schema-1" in result["unavailable"]

    # skipped sidecar (non-pytest contract)
    _write_tasks(vdir, [_task_dict("T-001", "tracks/xfoo.py")])
    (vdir / "anchor-surface.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "anchors": {},
                "anchor_set_digest": "d",
                "recorded_tree": "r",
                "skipped": True,
                "skipped_reason": "anchor surface skipped: non-pytest contract",
            }
        ),
        encoding="utf-8",
    )
    result = mixin._anchor_surface_for_assignment()
    assert "skipped" in result["unavailable"]
