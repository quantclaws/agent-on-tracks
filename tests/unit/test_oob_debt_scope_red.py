"""OOB #129/#133/RED-no-rice: debt ledger, scope advisory, RED routing."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.helpers import (
    ARCHER_DISPATCH,
    ARCHER_DONE,
    BASELINE_CMD,
    BASELINE_FROZEN,
    DEVON_RED_DISPATCH,
    DEVON_RED_DONE,
    ENTER_M_IMPL,
    ISLAND1_CMD,
    ISLAND1_PASS,
    PRISM_PLAN_DISPATCH,
    PRISM_PLAN_DONE,
    PRISM_PLAN_PASS,
    RED_GATE_CMD,
    SELECT_TASK_CMD,
    TASK_STARTED,
    TASKGRAPH_CMD,
    TASKGRAPH_COMMITTED,
    git_repo,
    m_impl_docs,
    m_impl_store,
    seq,
)
from tracks.executor.executor import Executor
from tracks.executor.taskgraph import (
    parse_tasks_json,
    scope_anchor_advisories,
    validate_debt_references,
)
from tracks.kernel import project

_SCHEMA2_TASK = {
    "task_id": "T-001",
    "issue_number": 1,
    "description": "slice",
    "ac_refs": ["AC-FR0001-01"],
    "fr_refs": ["FR-0001"],
    "if_ids": ["IF-IMPL-001"],
    "unit_refs": [],
    "acceptance_refs": ["tests/integration/test_app.py"],
    "scope_boundary": "tracks/app.py",
    "depends_on": [],
    "batch": "1",
    "parallel": False,
    "budget": 3,
}

_DEBT_ITEM = {
    "kind": "undelivered_scope",
    "deliverable": "tracks/leftover.py",
    "carrier_task": "T-002",
    "evidence": "plan says later",
}


def _graph_two_tasks(debt_on_first=None):
    first = {**_SCHEMA2_TASK, **{"task_id": "T-001"}}
    second = {
        **_SCHEMA2_TASK,
        **{"task_id": "T-002", "depends_on": ["T-001"]},
    }
    if debt_on_first is not None:
        first["debt"] = debt_on_first
    return json.dumps({"schema": 2, "tasks": [first, second]})


def test_debt_parse_validate_and_defaults():
    tasks, err = parse_tasks_json(_graph_two_tasks([_DEBT_ITEM]))
    assert err is None
    assert tasks[0].debt == (_DEBT_ITEM,)
    tasks2, err2 = parse_tasks_json(_graph_two_tasks())
    assert err2 is None
    assert tasks2[0].debt == ()
    assert validate_debt_references(tasks) == []
    bad_kind = [{**_DEBT_ITEM, "kind": "nope"}]
    _, err3 = parse_tasks_json(_graph_two_tasks(bad_kind))
    assert err3 is not None and "debt kind" in err3
    bad_carrier = [{**_DEBT_ITEM, "carrier_task": "T-999"}]
    tasks4, err4 = parse_tasks_json(_graph_two_tasks(bad_carrier))
    assert err4 is None
    assert any("T-999" in e for e in validate_debt_references(tasks4))
    bad_deliv = [{**_DEBT_ITEM, "deliverable": "  "}]
    _, err5 = parse_tasks_json(_graph_two_tasks(bad_deliv))
    assert err5 is not None and "deliverable" in err5


def test_debt_excluded_from_identity_but_in_payload_and_assignment(tmp_path):
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    tasks, err = parse_tasks_json(_graph_two_tasks([_DEBT_ITEM]))
    assert err is None
    node = tasks[0]
    payload = MImplRuntimeMixin._task_payload(node)
    assert payload["debt"] == [_DEBT_ITEM]
    other = MImplRuntimeMixin._task_payload(tasks[1])
    assert MImplRuntimeMixin._task_payloads_equivalent(payload, {**payload, "debt": []})
    assert not MImplRuntimeMixin._task_payloads_equivalent(payload, other)
    repo = git_repo(tmp_path, gitignore=True)
    m_impl_docs(repo)
    store = m_impl_store(repo)
    ex = Executor(store, repo, "RUN")
    state = SimpleNamespace(
        current_manifest=None, current_attempt=0, taskgraph_digest="d"
    )
    assignment: dict = {}
    task_dict = dict(payload)
    task_dict["task_id"] = "T-001"
    ex._add_assignment_runtime_fields(
        assignment, state, {"role": "devon", "substate": "RED"}, task_dict, {}
    )
    assert assignment["debt"] == [_DEBT_ITEM]


def test_scope_anchor_advisory_debt_exempt_and_nonblocking():
    tasks, err = parse_tasks_json(_graph_two_tasks())
    assert err is None
    surface = {
        "anchors": {
            "tests/integration/test_app.py": {"ast_modules": ["tracks.app"]},
        }
    }
    adv = scope_anchor_advisories(tasks, surface, repo=None)
    assert adv == []
    tasks2, _ = parse_tasks_json(
        json.dumps(
            {
                "schema": 2,
                "tasks": [
                    {**_SCHEMA2_TASK, "task_id": "T-001", "scope_boundary": "tracks/app.py, tracks/extra.py"},
                    {
                        **_SCHEMA2_TASK,
                        "task_id": "T-002",
                        "depends_on": ["T-001"],
                        "scope_boundary": "tracks/app.py",
                    },
                ],
            }
        )
    )
    adv2 = scope_anchor_advisories(tasks2, surface, repo=None)
    assert any("T-001" in a and "tracks/extra.py" in a for a in adv2)
    assert not any(a.startswith("T-002:") for a in adv2)
    tasks3, _ = parse_tasks_json(_graph_two_tasks([_DEBT_ITEM]))
    # debt on T-001 exempts it even though its scope would otherwise diff
    tasks3b, _ = parse_tasks_json(
        json.dumps(
            {
                "schema": 2,
                "tasks": [
                    {
                        **_SCHEMA2_TASK,
                        "task_id": "T-001",
                        "scope_boundary": "tracks/app.py, tracks/extra.py",
                        "debt": [_DEBT_ITEM],
                    },
                    {
                        **_SCHEMA2_TASK,
                        "task_id": "T-002",
                        "depends_on": ["T-001"],
                        "scope_boundary": "tracks/app.py",
                    },
                ],
            }
        )
    )
    assert tasks3 is not None and tasks3b is not None
    adv3 = scope_anchor_advisories(tasks3b, surface, repo=None)
    assert not any(a.startswith("T-001:") for a in adv3)


def _red_state(*verdicts):
    return project(seq(*ENTER_M_IMPL, BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE, TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS, PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS, SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE, RED_GATE_CMD, *verdicts))


def test_red_missing_only_routes_planning_without_attempt():
    evidence = json.dumps({"classifications": ["missing"], "verdict": "missing"})
    fail = (
        "verdict.failed",
        {"check": "red_invalid", "reason": "M-IMPL RED classifications are missing", "evidence": evidence, "attempt": 1},
    )
    s = _red_state(fail)
    assert s.substate == "PLANNING"
    assert s.taskgraph_committed is False
    assert s.current_attempt == 0


def test_red_collection_error_stays_red_with_attempt():
    evidence = json.dumps({"classifications": ["collection_error"], "verdict": "missing"})
    fail = (
        "verdict.failed",
        {"check": "red_invalid", "reason": "M-IMPL RED contains illegal classification: collection_error", "evidence": evidence, "attempt": 1},
    )
    s = _red_state(fail)
    assert s.substate == "RED"
    assert s.current_attempt == 1


def test_red_nonjson_evidence_stays_red_with_attempt():
    fail = (
        "verdict.failed",
        {"check": "red_invalid", "reason": "boom", "evidence": "backend Devon outcome", "attempt": 1},
    )
    s = _red_state(fail)
    assert s.substate == "RED"
    assert s.current_attempt == 1
