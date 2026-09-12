"""Behavior coverage for the task-graph validators (``taskgraph_validate``).

Pure-function branches the commit path reaches only on malformed or
integration-heavy graphs: duplicate/unknown dependencies, scope boundary
rejection and overlap, structural field errors per schema, six-tuple closure
gaps, §8 acceptance coverage and issue-number validity.
"""

from __future__ import annotations

from dataclasses import replace

from tracks.executor import taskgraph_validate as tv
from tracks.executor.taskgraph_parse import TaskNode


def _task(
    task_id: str = "T-001",
    *,
    description: str = "slice",
    ac_refs=("AC-FR0001-01",),
    fr_refs=("FR-0001",),
    if_ids=("IF-IMPL-001",),
    test_refs=(),
    scope_boundary: str = "tracks/a.py",
    depends_on=(),
    batch: str = "1",
    budget: int = 1,
    acceptance_refs=(),
    schema: int = 1,
    deferred_refs=(),
    integration: bool = False,
) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        issue_number=1,
        description=description,
        ac_refs=tuple(ac_refs),
        fr_refs=tuple(fr_refs),
        if_ids=tuple(if_ids),
        test_refs=tuple(test_refs),
        scope_boundary=scope_boundary,
        depends_on=tuple(depends_on),
        batch=batch,
        parallel=False,
        budget=budget,
        acceptance_refs=tuple(acceptance_refs),
        schema=schema,
        deferred_refs=tuple(deferred_refs),
        integration=integration,
    )


def test_validate_dag_duplicate_and_dash_dependency():
    ok, error = tv.validate_dag([_task("T-001"), _task("T-001", description="dup")])
    assert ok is False
    assert error == "duplicate task_id: T-001"

    ok, error = tv.validate_dag([_task(depends_on=("-",))])
    assert ok is True
    assert error is None


def test_scope_single_errors_integration_invalid_and_empty():
    assert tv._scope_single_errors(_task(integration=True, scope_boundary="/abs")) == []
    invalid = tv._scope_single_errors(_task(scope_boundary="/abs/x.py"))
    assert invalid == [
        "T-001: invalid scope path '/abs/x.py'",
        "T-001: scope_boundary must not be empty",
    ]
    empty = tv._scope_single_errors(_task(scope_boundary=""))
    assert empty == ["T-001: scope_boundary must not be empty"]


def test_scope_overlap_skips_integration_tasks():
    overlapping = [_task("T-001", scope_boundary="tracks/a.py"), _task("T-002", scope_boundary="tracks/a.py")]
    ok, errors = tv.validate_scope(overlapping)
    assert ok is False
    assert errors == ["scope overlap: T-001 and T-002 both target tracks/a.py"]

    skipped = [
        _task("T-001", scope_boundary="tracks/a.py", integration=True),
        _task("T-002", scope_boundary="tracks/a.py", integration=True),
    ]
    assert tv._scope_overlap_errors(skipped) == []

    mixed = [
        _task("T-001", scope_boundary="tracks/a.py"),
        _task("T-002", scope_boundary="tracks/a.py", integration=True),
    ]
    assert tv._scope_overlap_errors(mixed) == []


def test_validate_task_structure_empty_graph():
    assert tv.validate_task_structure([]) == [
        "tasks.json: task graph must contain at least one task"
    ]


def test_common_structure_errors_fields():
    errors = tv._common_structure_errors(_task(description=" ", batch="", budget=0))
    assert errors == [
        "T-001: description must not be empty",
        "T-001: batch must not be empty",
        "T-001: budget must be a positive integer",
    ]


def test_integration_structure_errors_schema_variants():
    assert tv._integration_structure_errors(_task(integration=True, schema=2)) == []
    assert tv._integration_structure_errors(_task(integration=True)) == [
        "T-001: test_refs must not be empty"
    ]
    deferred = _task(integration=True, deferred_refs=("tests/integration/x.py",))
    assert tv._integration_structure_errors(deferred) == []


def test_standard_structure_errors_schema_variants():
    assert tv._standard_acfr_errors(_task(ac_refs=(), fr_refs=(), if_ids=())) == [
        "T-001: ac_refs must not be empty",
        "T-001: fr_refs must not be empty",
        "T-001: if_ids must not be empty",
    ]
    empty_v2 = _task(schema=2, acceptance_refs=())
    assert tv._standard_ref_errors(empty_v2) == [
        "T-001: acceptance_refs must not be empty"
    ]
    assert tv._standard_ref_errors(_task(schema=2, acceptance_refs=("x",))) == []
    assert tv._standard_ref_errors(_task(schema=2, deferred_refs=("x",))) == []
    assert tv._standard_ref_errors(_task()) == ["T-001: test_refs must not be empty"]


def test_task_structure_errors_integration_branch():
    errors = tv._task_structure_errors(_task(integration=True, description=" "))
    assert errors == [
        "T-001: description must not be empty",
        "T-001: test_refs must not be empty",
    ]


def test_task_closure_errors_missing_and_partial():
    fields = ("owner=", "surface=", "composition=", "wiring=", "test=", "evidence=")
    missing_entry = tv._task_closure_errors(_task(), [], fields)
    assert missing_entry == ["T-001/AC-FR0001-01: six-tuple entry missing"]

    partial = tv._task_closure_errors(
        _task(), ["- **FR-0001** owner=x surface=y"], fields
    )
    assert partial == [
        "T-001/AC-FR0001-01: missing closure fields composition=, wiring=, test=, evidence=",
        "T-001/AC-FR0001-01: IF-IMPL-001 missing from closure",
    ]

    full = (
        "- **FR-0001** owner=x surface=y composition=z wiring=w "
        "test=t evidence=e IF-IMPL-001"
    )
    assert tv._task_closure_errors(
        _task(if_ids=("IF-IMPL-001",)), [full], fields
    ) == []
    assert tv._task_closure_errors(
        _task(if_ids=("IF-OTHER-999",)), [full], fields
    ) == ["T-001/AC-FR0001-01: IF-OTHER-999 missing from closure"]


def test_scope_paths_rejects_malformed_and_forbidden():
    paths, errors = tv._scope_paths(
        " , tracks/a.py, /bad, ~/home, .tracks/projects/x, a b, x/../y "
    )
    assert paths == ["tracks/a.py"]
    assert errors == [
        "task: invalid scope path '/bad'",
        "task: invalid scope path '~/home'",
        "task: forbidden scope path '.tracks/projects/x'",
        "task: invalid scope path 'a b'",
        "task: invalid scope path 'x/../y'",
    ]
    assert tv._parse_scope_paths("tracks\\a.py") == frozenset({"tracks/a.py"})


def test_requirement_ref_normalization_and_passthrough():
    assert tv._requirement_ref("AC-FR0010-01") == "FR-0010"
    assert tv._requirement_ref("AC-NFR0002-03") == "NFR-0002"
    assert tv._requirement_ref("not-an-ac") == "not-an-ac"


def test_validate_acceptance_coverage_schema_gate_and_gap():
    plan = (
        "## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0257-03 | integration | tests/integration/x.py::t | IF-IMPL-001 |\n"
    )
    assert tv.validate_acceptance_coverage([], plan) == (True, [])
    assert tv.validate_acceptance_coverage([_task(schema=1)], plan) == (True, [])

    undeclared = _task(schema=2, acceptance_refs=())
    ok, errors = tv.validate_acceptance_coverage([undeclared], plan)
    assert ok is False
    assert errors == [
        "§8 row AC-FR0257-03 target tests/integration/x.py::t is not declared by any "
        "task acceptance_refs"
    ]

    declared = _task(schema=2, acceptance_refs=("tests/integration/x.py::t",))
    assert tv.validate_acceptance_coverage([declared], plan) == (True, [])

    whole_file = _task(schema=2, acceptance_refs=("tests/integration/x.py",))
    assert tv.validate_acceptance_coverage([whole_file], plan) == (True, [])


def test_row_names_integration_and_plan_row_targets():
    assert tv._row_names_integration(None, "tests/x.py") is False
    assert tv._row_names_integration("unit", "tests/x.py") is False
    assert tv._row_names_integration("unit, integration", "tests/x.py") is True

    assert tv.plan_row_targets("::node") == []
    assert tv.plan_row_targets("tests/e2e/test_x.py::t") == [
        ("tests/e2e/test_x.py", "tests/e2e/test_x.py::t")
    ]
    assert tv.plan_row_targets("test_x.py") == [("tests/integration/test_x.py", None)]
    assert tv.plan_row_targets("`tests/integration/a.py`+tests/e2e/b.py") == [
        ("tests/integration/a.py", None),
        ("tests/e2e/b.py", None),
    ]


def test_validate_issue_numbers_rejects_non_positive():
    ok, errors = tv.validate_issue_numbers([replace(_task(), issue_number=0)])
    assert ok is False
    assert errors == ["T-001 issue_number=0 is not a positive integer"]
    assert tv.validate_issue_numbers([_task()]) == (True, [])
