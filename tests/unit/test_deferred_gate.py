"""B94 deferred anchor gate unit tests (CCR001-clean).

Covers:
 1 deferred-only failure -> verdict passed with deferred_failures
 2 deferred failures not counted in breaker / no DIAGNOSE
 3 integration scope union / hard gate / skip RED
 4 drift breaker unexpected vs legal
"""

from __future__ import annotations

import json

from tests.unit.helpers import git_repo, m_impl_docs, m_impl_store
from tracks.executor import deferred_gate
from tracks.executor.breaker import RunBreaker
from tracks.executor.taskgraph import parse_tasks_json
from tracks.kernel import project
from tracks.store import Store


def _valid_task(**overrides):
    base = {
        "task_id": "T-001",
        "issue_number": 1,
        "description": "slice",
        "ac_refs": ["AC-FR0001-01"],
        "fr_refs": ["FR-0001"],
        "if_ids": ["IF-IMPL-001"],
        "scope_boundary": "tracks/app.py",
        "depends_on": [],
        "batch": "1",
        "parallel": False,
        "budget": 2,
        "unit_refs": [],
        "acceptance_refs": ["tests/integration/test_a.py::test_one"],
        "deferred_refs": [],
        "integration": False,
        "schema": 2,
    }
    base.update(overrides)
    return base


def test_taskgraph_deferred_accepts_and_validates():
    # valid deferred
    raw = json.dumps({"schema": 2, "tasks": [_valid_task(deferred_refs=["tests/integration/test_b.py::t"])]})
    tasks, err = parse_tasks_json(raw)
    assert err is None
    assert tasks[0].deferred_refs == ("tests/integration/test_b.py::t",)
    # also e2e allowed
    raw = json.dumps({"schema": 2, "tasks": [_valid_task(deferred_refs=["tests/e2e/test_e2e.py::t"])]})
    tasks, err = parse_tasks_json(raw)
    assert err is None
    # invalid prefix
    raw = json.dumps({"schema": 2, "tasks": [_valid_task(deferred_refs=["tests/unit/test_x.py::t"])]})
    _, err = parse_tasks_json(raw)
    assert err is not None and "deferred_refs" in err
    # intersect
    raw = json.dumps(
        {"schema": 2, "tasks": [_valid_task(acceptance_refs=["tests/integration/test_a.py::test_one"], deferred_refs=["tests/integration/test_a.py::test_one"])]}
    )
    _, err = parse_tasks_json(raw)
    assert err is not None and "intersect" in err
    # integration must be bool
    raw = json.dumps({"schema": 2, "tasks": [_valid_task(integration="yes")]})
    _, err = parse_tasks_json(raw)
    assert err is not None and "integration" in err


def test_taskgraph_defaults_and_backward_compat():
    # legacy schema 1 without deferred still parses
    raw = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "T-001",
                    "issue_number": 1,
                    "description": "d",
                    "ac_refs": ["AC-FR0001-01"],
                    "fr_refs": ["FR-0001"],
                    "if_ids": ["IF-IMPL-001"],
                    "test_refs": ["tests/integration/test_a.py::test_one"],
                    "scope_boundary": "tracks/app.py",
                    "depends_on": [],
                    "batch": "1",
                    "parallel": False,
                    "budget": 2,
                }
            ]
        }
    )
    tasks, err = parse_tasks_json(raw)
    assert err is None
    assert tasks[0].deferred_refs == ()
    assert tasks[0].integration is False
    # schema2 default deferred/integration
    raw = json.dumps({"schema": 2, "tasks": [_valid_task()]})
    tasks, _ = parse_tasks_json(raw)
    assert tasks[0].deferred_refs == ()
    assert tasks[0].integration is False


def test_coverage_union_includes_deferred():
    from tracks.executor.taskgraph import validate_acceptance_coverage

    plan = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_b.py::t | IF-IMPL-001 |\n"
    )
    # only acceptance does not cover -> gap
    t1 = _valid_task(acceptance_refs=["tests/integration/test_a.py::test_one"], deferred_refs=[])
    tasks, _ = parse_tasks_json(json.dumps({"schema": 2, "tasks": [t1]}))
    ok, errs = validate_acceptance_coverage(tasks, plan)
    assert not ok
    # deferred covers -> ok
    t2 = _valid_task(acceptance_refs=["tests/integration/test_a.py::test_one"], deferred_refs=["tests/integration/test_b.py::t"])
    tasks, _ = parse_tasks_json(json.dumps({"schema": 2, "tasks": [t2]}))
    ok, _ = validate_acceptance_coverage(tasks, plan)
    assert ok


def test_partition_deferred_only_vs_mixed():
    failed = [{"node": "tests/integration/test_b.py::t", "status": "failed"}]
    deferred = ["tests/integration/test_b.py::t"]
    hard, deferred_out = deferred_gate.partition_failures(failed, deferred)
    assert hard == []
    assert len(deferred_out) == 1
    # mixed
    failed2 = [
        {"node": "tests/integration/test_b.py::t", "status": "failed"},
        {"node": "tests/integration/test_a.py::test_one", "status": "failed"},
    ]
    hard, deferred_out = deferred_gate.partition_failures(failed2, deferred)
    assert len(hard) == 1 and hard[0]["node"] == "tests/integration/test_a.py::test_one"
    assert len(deferred_out) == 1
    # file-level deferred matches node under file
    failed3 = [{"node": "tests/integration/test_b.py::test_x", "status": "failed"}]
    hard, deferred_out = deferred_gate.partition_failures(failed3, ["tests/integration/test_b.py"])
    assert hard == [] and len(deferred_out) == 1


def test_breaker_does_not_count_deferred_only():
    b = RunBreaker()
    # deferred-only failure is emitted as verdict.passed with deferred_failures -> breaker sees no failed
    b.note("verdict.passed", {"check": "green", "task_id": "T-001", "deferred_failures": ["tests/integration/test_b.py::t"]})
    assert b.task_failures == {}
    # explicit deferred_only marker on failed is filtered
    b2 = RunBreaker()
    b2.note("verdict.failed", {"check": "impl_defect", "task_id": "T-001", "deferred_only": True})
    assert b2.task_failures == {}
    # real hard failure counts
    b3 = RunBreaker()
    b3.note("verdict.failed", {"check": "impl_defect", "task_id": "T-001"})
    assert b3.task_failures["T-001"] == 1
    # mixed hard+deferred still counts (has hard_failures not empty)
    b4 = RunBreaker()
    b4.note(
        "verdict.failed",
        {"check": "impl_defect", "task_id": "T-001", "hard_failures": ["tests/integration/test_a.py::t"], "deferred_failures": ["tests/integration/test_b.py::t"]},
    )
    # our helper treats hard non-empty as countable (defaults to true)
    assert b4.task_failures["T-001"] == 1


def test_diagnose_not_triggered_for_deferred_only():
    # diagnose routing is via verdict.failed -> DIAGNOSE; a deferred-only
    # verdict is passed, so no inspect for DIAGNOSE is needed.
    # Here we verify the partition leads to passed logic, not failed.
    failed = [{"node": "tests/integration/test_b.py::t"}]
    deferred = ["tests/integration/test_b.py::t"]
    hard, _ = deferred_gate.partition_failures(failed, deferred)
    # hard empty means the runtime would emit verdict.passed, not failed -> no DIAGNOSE
    assert hard == []
    # hard non-empty would go to DIAGNOSE path (impl_defect)
    failed2 = [{"node": "tests/integration/test_a.py::test_one"}]
    hard2, _ = deferred_gate.partition_failures(failed2, deferred)
    assert hard2 != []


def test_integration_scope_union_and_hard_gate(tmp_path):
    # two tasks with different scopes, integration should union
    t1 = _valid_task(task_id="T-001", scope_boundary="tracks/a.py", deferred_refs=["tests/integration/test_b.py::t"], integration=False)
    t2 = _valid_task(
        task_id="T-INT",
        scope_boundary="tracks/b.py",
        acceptance_refs=["tests/integration/test_a.py::test_one"],
        deferred_refs=[],
        integration=True,
    )
    # use helper directly
    from tracks.executor.deferred_gate import integration_allowed_paths, integration_hard_refs

    # build minimal TaskNode-like dicts for union test
    all_tasks = [t1, t2]
    allowed = integration_allowed_paths(all_tasks)
    assert "tracks/a.py" in allowed and "tracks/b.py" in allowed
    hard = integration_hard_refs(t2, all_tasks)
    assert "tests/integration/test_b.py::t" in hard
    assert "tests/integration/test_a.py::test_one" in hard
    # non-integration hard gate does not include others' deferred
    from tracks.executor.deferred_gate import selection_union

    assert selection_union(t1) == ["tests/integration/test_a.py::test_one", "tests/integration/test_b.py::t"]


def test_integration_starts_red_for_walk_red(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    _ = m_impl_docs(repo)
    store = m_impl_store(repo)
    # task with integration flag
    task = {
        "task_id": "T-INT",
        "issue_number": 1,
        "description": "integration task",
        "ac_refs": ["AC-FR0001-01"],
        "fr_refs": ["FR-0001"],
        "if_ids": ["IF-IMPL-001"],
        "test_refs": ["tests/integration/test_a.py::test_one"],
        "scope_boundary": "tracks/app.py",
        "depends_on": [],
        "batch": "1",
        "parallel": False,
        "budget": 2,
        "unit_refs": [],
        "acceptance_refs": ["tests/integration/test_a.py::test_one"],
        "deferred_refs": [],
        "integration": True,
        "schema": 2,
    }
    # simulate task.started with integration true
    # use kernel reducer directly
    from tracks.kernel.m_impl import _on_task_started

    # create a dummy state via project
    state = project(store.events("RUN"))
    # emit task.started manually via reducer
    payload = {"task_id": "T-INT", "task": task, "manifest": {"task_id": "T-INT"}}
    # construct minimal envelope
    from tracks.kernel.events import EventEnvelope

    ev = EventEnvelope(seq=99, ts="2026-01-01T00:00:00+00:00", run_id="RUN", version="v0.5", type="task.started", schema_version=1, command_id=None, task_id=None, payload=payload)
    _on_task_started(state, payload, ev)
    assert state.substate == "RED"
    assert state.current_task_id == "T-INT"
    # non-integration stays RED
    task2 = dict(task)
    task2["integration"] = False
    task2["task_id"] = "T-001"
    state2 = project(store.events("RUN"))
    payload2 = {"task_id": "T-001", "task": task2, "manifest": {"task_id": "T-001"}}
    ev2 = EventEnvelope(seq=100, ts="2026-01-01T00:00:00+00:00", run_id="RUN", version="v0.5", type="task.started", schema_version=1, command_id=None, task_id=None, payload=payload2)
    _on_task_started(state2, payload2, ev2)
    assert state2.substate == "RED"


def test_drift_breaker_legal_vs_unexpected():
    # two tasks: T-001 deferred B, T-INT integration deferred also B
    t1 = _valid_task(task_id="T-001", acceptance_refs=["tests/integration/test_a.py::test_one"], deferred_refs=["tests/integration/test_b.py::t"])
    t_int = _valid_task(task_id="T-INT", acceptance_refs=["tests/integration/test_c.py::t"], deferred_refs=[], integration=True)
    tasks = [t1, t_int]
    # completed set empty -> legal includes all deferred + unfinished acceptance
    legal = deferred_gate.legal_red_refs(tasks, set())
    # deferred B is legal, acceptance of unfinished also legal
    assert "tests/integration/test_b.py::t" in legal
    assert "tests/integration/test_a.py::test_one" in legal
    # drift check: failed subset of legal -> no drift
    failed_ok = ["tests/integration/test_b.py::t"]
    assert deferred_gate.unexpected_reds(failed_ok, legal) == []
    # file-level deferred covers any node under file
    failed_ok2 = ["tests/integration/test_b.py::test_other"]
    # legal has file-level not, but node-level deferred exact only covers that node; test file-level case
    t_file = _valid_task(task_id="T-001", acceptance_refs=[], deferred_refs=["tests/integration/test_b.py"])
    legal2 = deferred_gate.legal_red_refs([t_file], set())
    assert deferred_gate.unexpected_reds(failed_ok2, legal2) == []
    # unexpected red
    failed_bad = ["tests/integration/test_unknown.py::t"]
    assert deferred_gate.unexpected_reds(failed_bad, legal) == ["tests/integration/test_unknown.py::t"]
    # also after T-001 completes, its acceptance no longer legal
    legal_after = deferred_gate.legal_red_refs(tasks, {"T-001"})
    assert "tests/integration/test_a.py::test_one" not in legal_after
    assert "tests/integration/test_b.py::t" in legal_after  # deferred stays legal
    assert deferred_gate.unexpected_reds(["tests/integration/test_a.py::test_one"], legal_after) == ["tests/integration/test_a.py::test_one"]


def test_runtime_drift_emits_event(tmp_path):
    # exercise the executor's drift check path via direct call
    from tracks.executor.taskgraph import TaskNode

    repo = git_repo(tmp_path, gitignore=True)
    _ = m_impl_docs(repo)
    store = Store(repo / ".tracks")
    # minimal taskgraph committed
    task = TaskNode(
        task_id="T-001",
        issue_number=1,
        description="slice",
        ac_refs=("AC-FR0001-01",),
        fr_refs=("FR-0001",),
        if_ids=("IF-IMPL-001",),
        test_refs=("tests/integration/test_a.py::test_one",),
        scope_boundary="tracks/app.py",
        depends_on=(),
        batch="1",
        parallel=False,
        budget=2,
        unit_refs=(),
        acceptance_refs=("tests/integration/test_a.py::test_one",),
        schema=2,
        deferred_refs=("tests/integration/test_b.py::t",),
        integration=False,
    )
    # write tasks.json so _task_node can resolve but we will inject state manually
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {
            "task_count": 1,
            "tasks": [
                {
                    "task_id": "T-001",
                    "issue_number": 1,
                    "description": "slice",
                    "ac_refs": ["AC-FR0001-01"],
                    "fr_refs": ["FR-0001"],
                    "if_ids": ["IF-IMPL-001"],
                    "test_refs": ["tests/integration/test_a.py::test_one"],
                    "scope_boundary": "tracks/app.py",
                    "depends_on": [],
                    "batch": "1",
                    "parallel": False,
                    "budget": 2,
                    "unit_refs": [],
                    "acceptance_refs": ["tests/integration/test_a.py::test_one"],
                    "deferred_refs": ["tests/integration/test_b.py::t"],
                    "integration": False,
                    "schema": 2,
                }
            ],
            "digest": "d",
        },
    )
    # create executor with run_id RUN
    from tracks.executor.executor import Executor

    ex = Executor(store, repo, "RUN")
    # fabricate a failed nodes list containing legal and illegal
    # call drift check directly
    cmd = type("Cmd", (), {"command_id": "C1"})()
    # legal deferred failure should not emit drift_breaker
    before = len([e for e in store.events("RUN") if e.type == "drift_breaker"])
    ex._check_drift_breaker(cmd, task, [{"node": "tests/integration/test_b.py::t"}])
    after = len([e for e in store.events("RUN") if e.type == "drift_breaker"])
    assert after == before
    # illegal failure should emit
    ex._check_drift_breaker(cmd, task, [{"node": "tests/integration/test_unknown.py::t"}])
    after2 = len([e for e in store.events("RUN") if e.type == "drift_breaker"])
    assert after2 == before + 1
    payload = [e for e in store.events("RUN") if e.type == "drift_breaker"][-1].payload
    assert "tests/integration/test_unknown.py::t" in payload["unexpected"]
