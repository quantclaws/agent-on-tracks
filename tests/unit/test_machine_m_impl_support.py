import json as json
from pathlib import Path as Path

from tests.unit import m_impl_machine_sequences as _mseq
from tests.unit.helpers import ARCHER_DISPATCH as ARCHER_DISPATCH
from tests.unit.helpers import ARCHER_DONE as ARCHER_DONE
from tests.unit.helpers import BASELINE_CMD as BASELINE_CMD
from tests.unit.helpers import BASELINE_FROZEN as BASELINE_FROZEN
from tests.unit.helpers import DEVON_GREEN_DISPATCH as DEVON_GREEN_DISPATCH
from tests.unit.helpers import DEVON_GREEN_DONE as DEVON_GREEN_DONE
from tests.unit.helpers import DEVON_RED_DISPATCH as DEVON_RED_DISPATCH
from tests.unit.helpers import DEVON_RED_DONE as DEVON_RED_DONE
from tests.unit.helpers import ENTER_M_IMPL as ENTER_M_IMPL
from tests.unit.helpers import GREEN_COMMIT_CMD as GREEN_COMMIT_CMD
from tests.unit.helpers import GREEN_COMMITTED as GREEN_COMMITTED
from tests.unit.helpers import GREEN_GATE_CMD as GREEN_GATE_CMD
from tests.unit.helpers import GREEN_PASS as GREEN_PASS
from tests.unit.helpers import ISLAND1_CMD as ISLAND1_CMD
from tests.unit.helpers import ISLAND1_PASS as ISLAND1_PASS
from tests.unit.helpers import M_IMPL_REQUIRED_ASSIGNMENT_KEYS as M_IMPL_REQUIRED_ASSIGNMENT_KEYS
from tests.unit.helpers import PRISM_PLAN_DISPATCH as PRISM_PLAN_DISPATCH
from tests.unit.helpers import PRISM_PLAN_DONE as PRISM_PLAN_DONE
from tests.unit.helpers import PRISM_PLAN_PASS as PRISM_PLAN_PASS
from tests.unit.helpers import PRISM_RED_DISPATCH as PRISM_RED_DISPATCH
from tests.unit.helpers import PRISM_RED_DONE as PRISM_RED_DONE
from tests.unit.helpers import PRISM_RED_PASS as PRISM_RED_PASS
from tests.unit.helpers import RED_CHECKPOINT_CMD as RED_CHECKPOINT_CMD
from tests.unit.helpers import RED_CHECKPOINTED as RED_CHECKPOINTED
from tests.unit.helpers import RED_GATE_CMD as RED_GATE_CMD
from tests.unit.helpers import RED_VALID_PASS as RED_VALID_PASS
from tests.unit.helpers import SELECT_TASK_CMD as SELECT_TASK_CMD
from tests.unit.helpers import TASK_STARTED as TASK_STARTED
from tests.unit.helpers import TASKGRAPH_CMD as TASKGRAPH_CMD
from tests.unit.helpers import TASKGRAPH_COMMITTED as TASKGRAPH_COMMITTED
from tests.unit.helpers import seq as seq
from tests.unit.m_impl_machine_sequences import COMPLETE_TASK_CMD as COMPLETE_TASK_CMD
from tests.unit.m_impl_machine_sequences import DEVON_REFACTOR_DISPATCH as DEVON_REFACTOR_DISPATCH
from tests.unit.m_impl_machine_sequences import DEVON_REFACTOR_DONE as DEVON_REFACTOR_DONE
from tests.unit.m_impl_machine_sequences import EXIT_CMD as EXIT_CMD
from tests.unit.m_impl_machine_sequences import FULL1_CLEAN as FULL1_CLEAN
from tests.unit.m_impl_machine_sequences import ISLAND2_CMD as ISLAND2_CMD
from tests.unit.m_impl_machine_sequences import ISLAND2_PASS as ISLAND2_PASS
from tests.unit.m_impl_machine_sequences import PRISM_FINAL_DISPATCH as PRISM_FINAL_DISPATCH
from tests.unit.m_impl_machine_sequences import PRISM_FINAL_DONE as PRISM_FINAL_DONE
from tests.unit.m_impl_machine_sequences import PRISM_FINAL_PASS as PRISM_FINAL_PASS
from tests.unit.m_impl_machine_sequences import REFACTOR_COMMITTED as REFACTOR_COMMITTED
from tests.unit.m_impl_machine_sequences import REFACTOR_GATE_CMD as REFACTOR_GATE_CMD
from tests.unit.m_impl_machine_sequences import STAGE_EXITED as STAGE_EXITED
from tests.unit.m_impl_machine_sequences import TASK_COMPLETED as TASK_COMPLETED
from tests.unit.m_impl_machine_sequences import TASK_REVIEW_CMD as TASK_REVIEW_CMD
from tests.unit.m_impl_machine_sequences import TASK_REVIEW_PASS as TASK_REVIEW_PASS
from tracks.executor.executor import _NEXT_STAGE as _NEXT_STAGE
from tracks.kernel import decide as decide
from tracks.kernel import project as project
from tracks.kernel.machine import _M_IMPL_CONTEXT_DOCS as _M_IMPL_CONTEXT_DOCS
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK as _M_IMPL_CRITERIA_PACK


def state_of(*items):
    return project(seq(*ENTER_M_IMPL, *items))

def _full_single_task_cycle():
    """BASELINE -> PLANNING -> ISLAND_GATE_1 -> PRISM_PLAN -> TASK_DISPATCH ->
    RED -> RED_GATE -> RED_CHECKPOINT -> PRISM_RED -> GREEN -> GREEN_GATE ->
    GREEN_COMMIT -> REFACTOR -> REFACTOR_GATE -> TASK_REVIEW -> PRISM_FINAL ->
    TASK_DONE -> ISLAND_GATE_2 -> EXIT."""
    return list(_mseq.cycle())

LINEAGE_FAIL = (
    "verdict.failed",
    {
        "check": "lineage",
        "reason": "G trailers do not match the immutable R lineage",
        "task_id": "T1",
        "attempt": 1,
    },
)

def _at_task_review():
    """Full prefix through REFACTOR_GATE -> TASK_REVIEW (refactor committed)."""
    return list(_mseq.task_review())

def _diagnose_state(classification):
    """Enter DIAGNOSE via GREEN_GATE unknown_attribution, then verdict.failed
    with the given classification."""
    base = [
        *_mseq.green_gate(),
        ("verdict.failed", {"check": "unknown_attribution", "reason": "unclear", "attempt": 1}),
    ]
    verdict = ("verdict.failed", {"check": classification, "attempt": 1})
    return state_of(*base, verdict)

def _diagnose_events_with_report():
    """DIAGNOSE verdict.failed carrying the full reason/evidence payload."""
    return [
        ("verdict.failed", {
            "check": "unknown_attribution", "reason": "unclear", "attempt": 1,
        }),
        ("verdict.failed", {
            "check": "test_defect",
            "reason": "tests assert not_real but provenance is fully real",
            "evidence": "tests/integration/test_release_evidence.py:295-297,344-347,363-365",
            "attempt": 2,
        }),
    ]

def _devon_failed_outcome():
    return (
        "outcome.received",
        {
            "role": "devon",
            "status": "failed",
            "failure_class": "agent_failed",
            "self_report": "simulated execution failure",
            "audit_evidence": "backend exit evidence",
        },
    )

def _old_plan_events():
    """Events: old plan commits T-001, T-007; T-001 completes; T-007 scope failure
    triggers Archer re-plan that produces new plan with retained T-001 + merged
    T-006-007."""
    task_t1 = {"task_id": "T-001", "issue_number": 83, "description": "stable core", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}
    task_t7 = {"task_id": "T-007", "issue_number": 83, "description": "original scope", "ac_refs": ["AC-2"], "fr_refs": [], "if_ids": ["IF-2"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["T-001"], "batch": "1", "parallel": "0", "budget": 3}
    return [
        BASELINE_CMD,
        BASELINE_FROZEN,
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "archer", "substate": "PLANNING"}, "command_id": "C2"}}),
        ("outcome.received", {"role": "archer", "status": "done"}),
        ("command.issued", {"command": {"kind": "commit_taskgraph", "params": {"stage": "M-IMPL"}, "command_id": "C3"}}),
        ("taskgraph.committed", {"task_count": 2, "task_ids": ["T-001", "T-007"], "tasks": [task_t1, task_t7], "digest": "aaa", "path": "tasks.json"}),
        ("command.issued", {"command": {"kind": "check_island_1", "params": {"stage": "M-IMPL"}, "command_id": "C4"}}),
        ("verdict.passed", {"check": "island_1"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_PLAN"}, "command_id": "C5"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "select_task", "params": {"stage": "M-IMPL"}, "command_id": "C6"}}),
        ("task.started", {"task_id": "T-001"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "RED"}, "command_id": "C7"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "RED_GATE"}, "command_id": "C8"}}),
        ("verdict.passed", {"check": "red_valid"}),
        ("command.issued", {"command": {"kind": "checkpoint_red", "params": {"stage": "M-IMPL", "task_id": "T-001"}, "command_id": "C9"}}),
        ("red.checkpointed", {"r_sha": "r001"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_RED"}, "command_id": "C10"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "GREEN"}, "command_id": "C11"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "GREEN_GATE"}, "command_id": "C12"}}),
        ("verdict.passed", {"check": "green"}),
        ("command.issued", {"command": {"kind": "commit_green", "params": {"stage": "M-IMPL", "task_id": "T-001"}, "command_id": "C13"}}),
        ("green.committed", {"commit_sha": "g001"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "REFACTOR"}, "command_id": "C14"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C15"}}),
        ("refactor.committed", {"commit_sha": "r001"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"}, "command_id": "C16"}}),
        ("verdict.passed", {"check": "task_review"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_FINAL"}, "command_id": "C17"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "complete_task", "params": {"stage": "M-IMPL", "task_id": "T-001"}, "command_id": "C18"}}),
        ("task.completed", {"task_id": "T-001"}),
    ]

SCOP_FAIL = ("verdict.failed", {"check": "scope", "reason": "G changes outside manifest", "evidence": '["outside.py"]', "attempt": 1})

def _new_plan_events():
    """Events after scope failure: Archer replans with T-001 (same payload) + new
    merged T-006-007 (different ID, different payload)."""
    task_t1 = {"task_id": "T-001", "issue_number": 83, "description": "stable core", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}
    task_merged = {"task_id": "T-006-007", "issue_number": 83, "description": "merged scope", "ac_refs": ["AC-2", "AC-3"], "fr_refs": [], "if_ids": ["IF-2"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["T-001"], "batch": "1", "parallel": "0", "budget": 3}
    # T-007 scope failure at TASK_REVIEW -> PLANNING -> Archer re-dispatch
    return [
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"}, "command_id": "C16b"}}),
        SCOP_FAIL,
        # Archer dispatches (from PLANNING)
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "archer", "substate": "PLANNING"}, "command_id": "C99"}}),
        ("outcome.received", {"role": "archer", "status": "done"}),
        ("command.issued", {"command": {"kind": "commit_taskgraph", "params": {"stage": "M-IMPL"}, "command_id": "C100"}}),
        ("taskgraph.committed", {
            "task_count": 2,
            "task_ids": ["T-001", "T-006-007"],
            "tasks": [task_t1, task_merged],
            "digest": "bbb",
            "path": "tasks.json",
            "retained_completed_task_ids": ["T-001"],
        }),
        ("command.issued", {"command": {"kind": "check_island_1", "params": {"stage": "M-IMPL"}, "command_id": "C101"}}),
        ("verdict.passed", {"check": "island_1"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_PLAN"}, "command_id": "C102"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
    ]


__all__ = [
    name
    for name in globals()
    if name not in {"__name__", "__file__", "__package__", "__loader__", "__spec__", "__cached__", "__builtins__", "__all__"}
]
