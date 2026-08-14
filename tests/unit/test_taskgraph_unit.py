"""Unit tests for taskgraph parsing and validation (IF-IMPL-003).

Covers edge cases beyond the integration fixtures: empty tasks, self-cycle,
multi-path scope, missing acceptance.md, negative issue numbers.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor.taskgraph import (
    parse_tasks_json,
    validate_ac_coverage,
    validate_dag,
    validate_issue_numbers,
    validate_scope,
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
