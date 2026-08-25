"""Unit tests for taskgraph parsing and validation (IF-IMPL-003).

Covers edge cases beyond the integration fixtures: empty tasks, self-cycle,
multi-path scope, missing acceptance.md, negative issue numbers.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor.taskgraph import (
    parse_tasks_json,
    plan_row_targets,
    validate_ac_coverage,
    validate_acceptance_coverage,
    validate_dag,
    validate_issue_numbers,
    validate_scope,
    validate_task_structure,
)

FIXTURES = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"

_VALID = (FIXTURES / "valid.json").read_text(encoding="utf-8")
_ACC = (FIXTURES / "acceptance.md").read_text(encoding="utf-8")
_IF = (FIXTURES / "interfaces.md").read_text(encoding="utf-8")


def _load(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_valid():
    tasks, err = parse_tasks_json(_VALID)
    assert err is None
    assert len(tasks) == 2
    assert tasks[0].task_id == "T-001"
    assert tasks[1].depends_on == ("T-001",)


def test_parse_invalid_json():
    tasks, err = parse_tasks_json("{not json}")
    assert err is not None
    assert "invalid JSON" in err
    assert tasks == []


def test_parse_missing_tasks_key():
    tasks, err = parse_tasks_json('{"version": "v0.5"}')
    assert err is not None
    assert "missing 'tasks'" in err


def test_parse_empty_tasks():
    tasks, err = parse_tasks_json('{"tasks": []}')
    assert err is None
    assert tasks == []


def test_parse_missing_required_field():
    raw = '{"tasks": [{"task_id": "T-1"}]}'
    tasks, err = parse_tasks_json(raw)
    assert err is not None
    assert "missing required field" in err


def test_dag_no_cycle():
    tasks, _ = parse_tasks_json(_VALID)
    ok, cycle = validate_dag(tasks)
    assert ok
    assert cycle is None


def test_dag_cycle():
    tasks, _ = parse_tasks_json(_load("dag_cycle.json"))
    ok, cycle = validate_dag(tasks)
    assert not ok
    assert "cycle" in cycle
    assert "T-001" in cycle


def test_dag_self_cycle():
    raw = (
        '{"tasks": [{"task_id":"T-1","issue_number":1,"description":"d",'
        '"ac_refs":[],"fr_refs":[],"if_ids":[],"test_refs":[],'
        '"scope_boundary":"a.py","depends_on":["T-1"],'
        '"batch":"1","parallel":false,"budget":1}]}'
    )
    tasks, _ = parse_tasks_json(raw)
    ok, cycle = validate_dag(tasks)
    assert not ok
    assert "cycle" in cycle


def test_dag_unknown_dependency():
    raw = (
        '{"tasks": [{"task_id":"T-1","issue_number":1,"description":"d",'
        '"ac_refs":[],"fr_refs":[],"if_ids":[],"test_refs":[],'
        '"scope_boundary":"a.py","depends_on":["T-999"],'
        '"batch":"1","parallel":false,"budget":1}]}'
    )
    tasks, _ = parse_tasks_json(raw)
    ok, msg = validate_dag(tasks)
    assert not ok
    assert "T-1" in msg
    assert "T-999" in msg
    assert "unknown" in msg.lower()


def test_scope_no_overlap():
    tasks, _ = parse_tasks_json(_VALID)
    ok, errors = validate_scope(tasks)
    assert ok
    assert errors == []


def test_scope_overlap():
    tasks, _ = parse_tasks_json(_load("scope_overlap.json"))
    ok, errors = validate_scope(tasks)
    assert not ok
    assert any("overlap" in e for e in errors)
    assert any("T-001" in e for e in errors)


def test_ac_coverage_all_covered():
    from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids

    tasks, _ = parse_tasks_json(_VALID)
    acs = sorted(_known_ac_ids(_ACC))
    ifs = _extract_if_registry(_IF)
    ok, errors = validate_ac_coverage(tasks, acs, ifs)
    assert ok
    assert errors == []


def test_ac_coverage_gap():
    from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids

    tasks, _ = parse_tasks_json(_load("ac_coverage_gap.json"))
    acs = sorted(_known_ac_ids(_ACC))
    ifs = _extract_if_registry(_IF)
    ok, errors = validate_ac_coverage(tasks, acs, ifs)
    assert not ok
    assert any("not covered" in e for e in errors)


def test_ac_coverage_invalid_if():
    from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids

    tasks, _ = parse_tasks_json(_load("if_invalid.json"))
    acs = sorted(_known_ac_ids(_ACC))
    ifs = _extract_if_registry(_IF)
    ok, errors = validate_ac_coverage(tasks, acs, ifs)
    assert not ok
    assert any("IF-IMPL-999" in e for e in errors)
    assert any("T-001" in e for e in errors)


def test_issue_numbers_valid():
    tasks, _ = parse_tasks_json(_VALID)
    ok, errors = validate_issue_numbers(tasks)
    assert ok
    assert errors == []


def test_issue_numbers_zero():
    tasks, _ = parse_tasks_json(_load("issue_invalid.json"))
    ok, errors = validate_issue_numbers(tasks)
    assert not ok
    assert any("issue" in e for e in errors)
    assert any("T-001" in e for e in errors)


def test_issue_numbers_negative():
    raw = (
        '{"tasks": [{"task_id":"T-1","issue_number":-5,"description":"d",'
        '"ac_refs":[],"fr_refs":[],"if_ids":[],"test_refs":[],'
        '"scope_boundary":"a.py","depends_on":[],'
        '"batch":"1","parallel":false,"budget":1}]}'
    )
    tasks, _ = parse_tasks_json(raw)
    ok, errors = validate_issue_numbers(tasks)
    assert not ok
    assert any("not a positive integer" in e for e in errors)


# -- B50 (#65) schema v2: unit_refs / acceptance_refs split -------------------

_SCHEMA2_TASK = {
    "task_id": "T-001",
    "issue_number": 1,
    "description": "slice",
    "ac_refs": ["AC-FR0001-01"],
    "fr_refs": ["FR-0001"],
    "if_ids": ["IF-IMPL-001"],
    "unit_refs": ["tests/unit/test_app.py::test_app"],
    "acceptance_refs": ["tests/integration/test_app.py"],
    "scope_boundary": "tracks/app.py",
    "depends_on": [],
    "batch": "1",
    "parallel": False,
    "budget": 3,
}


def _schema2_graph(task_overrides: dict | None = None) -> str:
    import json

    task = {**_SCHEMA2_TASK, **(task_overrides or {})}
    return json.dumps({"schema": 2, "tasks": [task]})


def test_schema2_parse_splits_refs():
    tasks, err = parse_tasks_json(_schema2_graph())
    assert err is None
    assert tasks[0].schema == 2
    assert tasks[0].unit_refs == ("tests/unit/test_app.py::test_app",)
    assert tasks[0].acceptance_refs == ("tests/integration/test_app.py",)
    # test_refs stays the derived combined list for legacy consumers
    assert tasks[0].test_refs == (
        "tests/unit/test_app.py::test_app",
        "tests/integration/test_app.py",
    )


def test_schema2_rejects_legacy_test_refs():
    import json

    raw = json.dumps({"schema": 2, "tasks": [{**_SCHEMA2_TASK, "test_refs": ["x"]}]})
    tasks, err = parse_tasks_json(raw)
    assert err is not None
    assert "test_refs" in err and "schema 2" in err
    assert tasks == []


def test_schema2_requires_split_fields():
    raw = _schema2_graph({"unit_refs": None})
    raw = raw.replace('"unit_refs": null', '"no_unit": []')
    tasks, err = parse_tasks_json(raw)
    assert err is not None
    assert "missing required field 'unit_refs'" in err

    raw = _schema2_graph({"acceptance_refs": None})
    raw = raw.replace('"acceptance_refs": null', '"no_acc": []')
    tasks, err = parse_tasks_json(raw)
    assert err is not None
    assert "missing required field 'acceptance_refs'" in err


def test_schema2_unit_refs_must_live_under_tests_unit():
    tasks, err = parse_tasks_json(
        _schema2_graph({"unit_refs": ["tests/integration/test_app.py"]})
    )
    assert err is not None
    assert "unit_refs entry must live under tests/unit/" in err


def test_schema2_acceptance_refs_must_live_under_tests_integration():
    tasks, err = parse_tasks_json(
        _schema2_graph({"acceptance_refs": ["tests/unit/test_app.py::test_app"]})
    )
    assert err is not None
    assert "acceptance_refs entry must live under tests/integration/" in err


def test_unsupported_schema_version_rejected():
    raw = '{"schema": 3, "tasks": []}'
    tasks, err = parse_tasks_json(raw)
    assert err is not None
    assert "unsupported schema version" in err


def test_schema_marker_type_tightened():
    """PRISM-B49B50-R1-02: bool (True -> 1) and float (2.0 -> 2) root
    markers must fail closed instead of silently coercing."""
    tasks, err = parse_tasks_json('{"schema": true, "tasks": []}')
    assert err is not None
    assert "unsupported schema version" in err
    tasks, err = parse_tasks_json('{"schema": 2.0, "tasks": []}')
    assert err is not None
    assert "unsupported schema version" in err


def test_acceptance_coverage_bare_node_ref_no_false_gap():
    """PRISM-B49B50-R1-01 adversarial (1): a BARE node ref in §8
    (``test_app.py::test_flow_a``) must anchor identically to the runtime
    gate -- the commit-layer closure must not compute a phantom path from
    the whole ``file::test`` string and deadlock Archer in PLANNING."""
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | test_app.py::test_flow_a | "
        "IF-IMPL-001 |\n"
    )
    tasks, _ = parse_tasks_json(
        _schema2_graph({"acceptance_refs": ["tests/integration/test_app.py::test_flow_a"]})
    )
    ok, errors = validate_acceptance_coverage(tasks, plan)
    assert ok
    assert errors == []


def test_acceptance_coverage_file_declaration_covers_node_targets():
    """PRISM-B49B50-R1-01 adversarial (2): a FILE-level declaration covers
    every node target in that file (same whole-file expansion the runtime
    gate applies) -- must not be reported as a gap."""
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | "
        "`tests/integration/test_app.py::test_flow_a` + "
        "`tests/integration/test_app.py::test_flow_b` | IF-IMPL-001 |\n"
    )
    tasks, _ = parse_tasks_json(
        _schema2_graph({"acceptance_refs": ["tests/integration/test_app.py"]})
    )
    ok, errors = validate_acceptance_coverage(tasks, plan)
    assert ok
    assert errors == []


def test_plan_row_targets_matches_runtime_gate_parser():
    """PRISM-B49B50-R1-01: commit layer and gate layer share ONE parser."""
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    cell = "`tests/integration/test_app.py::test_flow_a` + `test_other.py` + test_b.py::t"
    assert (
        MImplRuntimeMixin._integration_row_targets(cell)
        == plan_row_targets(cell)
        == [
            ("tests/integration/test_app.py", "tests/integration/test_app.py::test_flow_a"),
            ("tests/integration/test_other.py", None),
            ("tests/integration/test_b.py", "tests/integration/test_b.py::t"),
        ]
    )


def test_legacy_schema1_layers_route_by_prefix():
    """Legacy graphs keep test_refs but are mapped: tests/unit/ -> RED
    obligation, everything else -> acceptance anchor (replay compatibility)."""
    import json

    raw = json.dumps(
        {
            "tasks": [
                {
                    **_SCHEMA2_TASK,
                    "test_refs": [
                        "tests/unit/test_app.py::test_app",
                        "tests/integration/test_app.py",
                    ],
                    "unit_refs": None,
                    "acceptance_refs": None,
                }
            ]
        }
    )
    raw = raw.replace('"unit_refs": null, ', "").replace(', "acceptance_refs": null', "")
    tasks, err = parse_tasks_json(raw)
    assert err is None
    assert tasks[0].schema == 1
    assert tasks[0].unit_refs == ("tests/unit/test_app.py::test_app",)
    assert tasks[0].acceptance_refs == ("tests/integration/test_app.py",)


def test_structure_schema2_empty_acceptance_rejected_empty_unit_ok():
    tasks, _ = parse_tasks_json(_schema2_graph({"unit_refs": []}))
    errors = validate_task_structure(tasks)
    assert errors == [], "empty unit_refs is legal (R manifest covers unit)"

    tasks, _ = parse_tasks_json(
        _schema2_graph(
            {
                "unit_refs": [],
                "acceptance_refs": [],
            }
        )
    )
    errors = validate_task_structure(tasks)
    assert any("acceptance_refs must not be empty" in e for e in errors)


def test_acceptance_coverage_schema2_declared_anchor_covers_row():
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_app.py | "
        "IF-IMPL-001 |\n"
    )
    tasks, _ = parse_tasks_json(_schema2_graph())
    ok, errors = validate_acceptance_coverage(tasks, plan)
    assert ok
    assert errors == []


def test_acceptance_coverage_schema2_node_anchor_covers_row():
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | "
        "`tests/integration/test_app.py::test_flow_a` + "
        "`tests/integration/test_app.py::test_flow_b` | IF-IMPL-001 |\n"
    )
    tasks, _ = parse_tasks_json(
        _schema2_graph(
            {"acceptance_refs": ["tests/integration/test_app.py::test_flow_a"]}
        )
    )
    ok, errors = validate_acceptance_coverage(tasks, plan)
    # A node-level declaration covers only its own node; flow_b is missing.
    assert not ok
    assert any("test_flow_b" in e for e in errors)


def test_acceptance_coverage_schema2_gap_fails_at_planning_time():
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_app.py | "
        "IF-IMPL-001 |\n"
    )
    tasks, _ = parse_tasks_json(
        _schema2_graph({"acceptance_refs": ["tests/integration/test_other.py"]})
    )
    ok, errors = validate_acceptance_coverage(tasks, plan)
    assert not ok
    assert any("§8 row AC-FR0001-01" in e for e in errors)
    assert any("not declared by any task acceptance_refs" in e for e in errors)


def test_acceptance_coverage_non_integration_rows_skipped():
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | unit | tests/unit/test_app.py | IF-IMPL-001 |\n"
    )
    tasks, _ = parse_tasks_json(_schema2_graph({"acceptance_refs": []}))
    # unit-layer rows are Devon's RED territory, not acceptance closure
    ok, errors = validate_acceptance_coverage(tasks, plan)
    assert ok
    assert errors == []


def test_acceptance_coverage_legacy_schema1_skipped():
    import json

    raw = json.dumps(
        {
            "tasks": [
                {
                    **_SCHEMA2_TASK,
                    "test_refs": ["tests/integration/test_app.py"],
                    "unit_refs": None,
                    "acceptance_refs": None,
                }
            ]
        }
    )
    raw = raw.replace('"unit_refs": null, ', "").replace(', "acceptance_refs": null', "")
    tasks, _ = parse_tasks_json(raw)
    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_unrelated.py | "
        "IF-IMPL-001 |\n"
    )
    ok, errors = validate_acceptance_coverage(tasks, plan)
    assert ok, "legacy graphs keep their historical IF-index binding; no closure check"
