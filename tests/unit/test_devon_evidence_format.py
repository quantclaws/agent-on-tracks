"""Devon evidence shape vs semantic routing (OOB: format channel)."""

from __future__ import annotations

from tests.unit.helpers import (
    ARCHER_DISPATCH,
    ARCHER_DONE,
    BASELINE_CMD,
    BASELINE_FROZEN,
    DEVON_GREEN_DISPATCH,
    DEVON_GREEN_DONE,
    DEVON_RED_DISPATCH,
    DEVON_RED_DONE,
    ENTER_M_IMPL,
    GREEN_GATE_CMD,
    ISLAND1_CMD,
    ISLAND1_PASS,
    PRISM_PLAN_DISPATCH,
    PRISM_PLAN_DONE,
    PRISM_PLAN_PASS,
    PRISM_RED_DISPATCH,
    PRISM_RED_DONE,
    PRISM_RED_PASS,
    RED_CHECKPOINT_CMD,
    RED_CHECKPOINTED,
    RED_GATE_CMD,
    RED_VALID_PASS,
    SELECT_TASK_CMD,
    TASK_STARTED,
    TASKGRAPH_CMD,
    TASKGRAPH_COMMITTED,
    git_repo,
    m_impl_docs,
    m_impl_store,
    m_impl_task,
    seq,
)
from tracks.executor.executor import Executor
from tracks.executor.m_impl_runtime import _is_evidence_shape_error
from tracks.kernel import project
from tracks.kernel.events import Command

PRE_RED_GATE = [
    *ENTER_M_IMPL,
    BASELINE_CMD,
    BASELINE_FROZEN,
    ARCHER_DISPATCH,
    ARCHER_DONE,
    TASKGRAPH_CMD,
    TASKGRAPH_COMMITTED,
    ISLAND1_CMD,
    ISLAND1_PASS,
    PRISM_PLAN_DISPATCH,
    PRISM_PLAN_DONE,
    PRISM_PLAN_PASS,
    SELECT_TASK_CMD,
    TASK_STARTED,
    DEVON_RED_DISPATCH,
    DEVON_RED_DONE,
    RED_GATE_CMD,
]

PRE_GREEN_GATE = [
    *PRE_RED_GATE,
    RED_VALID_PASS,
    RED_CHECKPOINT_CMD,
    RED_CHECKPOINTED,
    PRISM_RED_DISPATCH,
    PRISM_RED_DONE,
    PRISM_RED_PASS,
    DEVON_GREEN_DISPATCH,
    DEVON_GREEN_DONE,
    GREEN_GATE_CMD,
]


def _shape_outcome():
    return {
        "role": "devon",
        "status": "done",
        "phase": "red",
        "changed_paths": ["tests/unit/test_app.py"],
        "commands": [],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
    }


def _semantic_outcome():
    base = _shape_outcome()
    base["implemented_if_ids"] = []
    base["manifest_compliance"] = False
    return base


def test_shape_classifier_splits_buckets():
    assert _is_evidence_shape_error("Devon evidence missing: implemented_if_ids")
    assert _is_evidence_shape_error("Devon evidence phase mismatch: expected green")
    assert not _is_evidence_shape_error("Devon manifest_compliance is not true")
    assert not _is_evidence_shape_error("Devon RED evidence has no changed paths")


def test_shape_red_gate_emits_evidence_malformed_without_attempt(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    m_impl_docs(repo)
    store = m_impl_store(repo)
    task = m_impl_task()
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {"task_count": 1, "tasks": [task], "digest": "g"},
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {"task_id": task["task_id"]},
        },
    )
    store.append("RUN", "v0.5", "outcome.received", _shape_outcome())
    ex = Executor(store, repo, "RUN")
    state = store.state("RUN")
    ex._emit_task_gate(
        Command("run_task_gates", {"gate": "RED_GATE"}, command_id="C1"),
        state,
        "red",
        "red_invalid",
        "red_valid",
    )
    verdict = [e for e in store.events("RUN") if e.type == "verdict.failed"][-1]
    assert verdict.payload.get("failure_class") == "evidence_malformed"
    assert verdict.payload.get("check") == "evidence_malformed"
    st = store.state("RUN")
    assert st.current_attempt == 0
    assert st.format_failure_streak == 1


def test_semantic_red_gate_stays_burning(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    m_impl_docs(repo)
    store = m_impl_store(repo)
    task = m_impl_task()
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {"task_count": 1, "tasks": [task], "digest": "g"},
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {"task_id": task["task_id"]},
        },
    )
    store.append("RUN", "v0.5", "outcome.received", _semantic_outcome())
    ex = Executor(store, repo, "RUN")
    state = store.state("RUN")
    ex._emit_task_gate(
        Command("run_task_gates", {"gate": "RED_GATE"}, command_id="C1"),
        state,
        "red",
        "red_invalid",
        "red_valid",
    )
    verdict = [e for e in store.events("RUN") if e.type == "verdict.failed"][-1]
    assert verdict.payload.get("failure_class") is None
    assert verdict.payload.get("check") == "red_invalid"
    st = store.state("RUN")
    assert st.current_attempt == 1
    assert st.format_failure_streak == 0


def test_semantic_green_stays_impl_defect():
    vf = (
        "verdict.failed",
        {
            "check": "impl_defect",
            "reason": "Devon manifest_compliance is not true",
            "evidence": "backend Devon outcome",
            "task_id": "T1",
            "attempt": 1,
        },
    )
    s = project(seq(*PRE_GREEN_GATE, vf))
    assert s.format_failure_streak == 0
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "impl_defect"


def test_format_streak_three_escalates():
    def _vf():
        return (
            "verdict.failed",
            {
                "check": "evidence_malformed",
                "failure_class": "evidence_malformed",
                "reason": "Devon evidence missing: implemented_if_ids",
                "evidence": "backend Devon outcome",
                "task_id": "T1",
                "attempt": 1,
            },
        )

    s1 = project(seq(*PRE_RED_GATE, _vf()))
    assert s1.current_attempt == 0
    assert s1.format_failure_streak == 1
    assert s1.status == "active"
    s3 = project(seq(*PRE_RED_GATE, _vf(), _vf(), _vf()))
    assert s3.format_failure_streak == 3
    assert s3.status == "awaiting_human"
    assert s3.awaiting == "escalation"
    assert s3.current_attempt == 0
