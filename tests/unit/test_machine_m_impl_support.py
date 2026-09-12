import json as json
from pathlib import Path as Path

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
from tracks.executor.executor import _NEXT_STAGE as _NEXT_STAGE
from tracks.kernel import decide as decide
from tracks.kernel import project as project
from tracks.kernel.machine import _M_IMPL_CONTEXT_DOCS as _M_IMPL_CONTEXT_DOCS
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK as _M_IMPL_CRITERIA_PACK

DEVON_REFACTOR_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "devon", "substate": "REFACTOR"},
            "command_id": "C14",
        }
    },
)

DEVON_REFACTOR_DONE = ("outcome.received", {"role": "devon", "status": "done"})

REFACTOR_GATE_CMD = (
    "command.issued",
    {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C15"}},
)

REFACTOR_COMMITTED = ("refactor.committed", {"commit_sha": "ghi789"})

TASK_REVIEW_CMD = (
    "command.issued",
    {
        "command": {
            "kind": "run_task_gates",
            "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"},
            "command_id": "C16",
        }
    },
)

TASK_REVIEW_PASS = ("verdict.passed", {"check": "task_review"})

PRISM_FINAL_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "prism", "substate": "PRISM_FINAL"},
            "command_id": "C17",
        }
    },
)

PRISM_FINAL_DONE = ("outcome.received", {"role": "prism", "status": "done"})

PRISM_FINAL_PASS = (
    "prism.verdict",
    {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)},
)

COMPLETE_TASK_CMD = (
    "command.issued",
    {
        "command": {
            "kind": "complete_task",
            "params": {"stage": "M-IMPL", "task_id": "T1"},
            "command_id": "C18",
        }
    },
)

TASK_COMPLETED = ("task.completed", {"task_id": "T1"})

ISLAND2_CMD = (
    "command.issued",
    {"command": {"kind": "check_island_2", "params": {"stage": "M-IMPL"}, "command_id": "C19"}},
)

FULL1_CLEAN = (
    "full.executed",
    {"round": "FULL_1", "passed": True, "serves_as_full_f": True},
)

ISLAND2_PASS = ("verdict.passed", {"check": "island_2"})

EXIT_CMD = (
    "command.issued",
    {"command": {"kind": "write_frontmatter", "params": {"stage": "M-IMPL"}, "command_id": "C20"}},
)

STAGE_EXITED = ("stage.exited", {"stage": "M-IMPL"})

def state_of(*items):
    return project(seq(*ENTER_M_IMPL, *items))

def _full_single_task_cycle():
    """BASELINE -> PLANNING -> ISLAND_GATE_1 -> PRISM_PLAN -> TASK_DISPATCH ->
    RED -> RED_GATE -> RED_CHECKPOINT -> PRISM_RED -> GREEN -> GREEN_GATE ->
    GREEN_COMMIT -> REFACTOR -> REFACTOR_GATE -> TASK_REVIEW -> PRISM_FINAL ->
    TASK_DONE -> ISLAND_GATE_2 -> EXIT."""
    return [
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
        RED_VALID_PASS,
        RED_CHECKPOINT_CMD,
        RED_CHECKPOINTED,
        PRISM_RED_DISPATCH,
        PRISM_RED_DONE,
        PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH,
        DEVON_GREEN_DONE,
        GREEN_GATE_CMD,
        GREEN_PASS,
        GREEN_COMMIT_CMD,
        GREEN_COMMITTED,
        DEVON_REFACTOR_DISPATCH,
        DEVON_REFACTOR_DONE,
        REFACTOR_GATE_CMD,
        REFACTOR_COMMITTED,
        TASK_REVIEW_CMD,
        TASK_REVIEW_PASS,
        PRISM_FINAL_DISPATCH,
        PRISM_FINAL_DONE,
        PRISM_FINAL_PASS,
        COMPLETE_TASK_CMD,
        TASK_COMPLETED,
        ISLAND2_CMD,
        FULL1_CLEAN,
        ISLAND2_PASS,
        EXIT_CMD,
        STAGE_EXITED,
    ]

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
    return [
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
        RED_VALID_PASS,
        RED_CHECKPOINT_CMD,
        RED_CHECKPOINTED,
        PRISM_RED_DISPATCH,
        PRISM_RED_DONE,
        PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH,
        DEVON_GREEN_DONE,
        GREEN_GATE_CMD,
        GREEN_PASS,
        GREEN_COMMIT_CMD,
        GREEN_COMMITTED,
        DEVON_REFACTOR_DISPATCH,
        DEVON_REFACTOR_DONE,
        REFACTOR_GATE_CMD,
        REFACTOR_COMMITTED,
    ]

def _diagnose_state(classification):
    """Enter DIAGNOSE via GREEN_GATE unknown_attribution, then verdict.failed
    with the given classification."""
    base = [
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
        RED_VALID_PASS,
        RED_CHECKPOINT_CMD,
        RED_CHECKPOINTED,
        PRISM_RED_DISPATCH,
        PRISM_RED_DONE,
        PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH,
        DEVON_GREEN_DONE,
        GREEN_GATE_CMD,
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
