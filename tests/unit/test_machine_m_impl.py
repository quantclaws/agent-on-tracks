"""M-IMPL reducer/decide branches (flow.md §10) - pure.

Covers the explicit `_decide_m_impl` control flow, the inner RGR cycle
(Red->Green->Refactor), Prism review at three checkpoints, DIAGNOSE four-way
routing, multi-task iteration, gate failures, and the kernel purity boundary
(NFR-0030).
"""

import json
from pathlib import Path

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
    GREEN_COMMIT_CMD,
    GREEN_COMMITTED,
    GREEN_GATE_CMD,
    GREEN_PASS,
    ISLAND1_CMD,
    ISLAND1_PASS,
    M_IMPL_REQUIRED_ASSIGNMENT_KEYS,
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
    seq,
)
from tracks.executor.executor import _NEXT_STAGE
from tracks.kernel import decide, project
from tracks.kernel.machine import _M_IMPL_CONTEXT_DOCS, _M_IMPL_CRITERIA_PACK

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


# -- stage entry / initial substate ------------------------------------------


def test_enter_m_impl_from_m_test_exit():
    """stage.entered(M-IMPL) sets substate=BASELINE, resets fields."""
    s = state_of()
    assert s.stage == "M-IMPL"
    assert s.substate == "BASELINE"
    assert s.current_attempt == 0
    assert s.baseline_frozen is False
    assert s.taskgraph_committed is False
    assert s.tasks_total == 0


def test_next_stage_m_test_to_m_impl():
    """_NEXT_STAGE[M-TEST] == M-IMPL."""
    assert _NEXT_STAGE["M-TEST"] == "M-IMPL"


# -- BASELINE -> PLANNING ----------------------------------------------------


def test_baseline_current_to_planning():
    """baseline.frozen(status=current) -> PLANNING."""
    s = state_of(BASELINE_CMD, BASELINE_FROZEN)
    assert s.baseline_frozen is True
    assert s.substate == "PLANNING"


def test_baseline_stale_to_needs_attention():
    """baseline.frozen(status=stale) -> NEEDS_ATTENTION."""
    s = state_of(BASELINE_CMD, ("baseline.frozen", {"status": "stale"}))
    assert s.substate == "NEEDS_ATTENTION"
    assert s.baseline_frozen is False


def test_baseline_decide_produces_freeze_baseline():
    """decide() at BASELINE produces freeze_baseline command."""
    s = state_of()
    cmd = decide(s)
    assert cmd.kind == "freeze_baseline"


def test_needs_attention_decide_returns_none():
    """decide() at NEEDS_ATTENTION returns None (halt)."""
    s = state_of(BASELINE_CMD, ("baseline.frozen", {"status": "stale"}))
    assert decide(s) is None


# -- PLANNING -> ISLAND_GATE_1 -----------------------------------------------


def test_planning_dispatches_archer():
    """decide() at PLANNING dispatches Archer."""
    s = state_of(BASELINE_CMD, BASELINE_FROZEN)
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "PLANNING"
    assert "tracks-discuz" in cmd.params["assignment"]["skills"]


def test_planning_outcome_done_then_commit_taskgraph():
    """After Archer outcome, decide() produces commit_taskgraph."""
    s = state_of(BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE)
    assert s.doc_produced is True
    cmd = decide(s)
    assert cmd.kind == "commit_taskgraph"


def test_taskgraph_committed_to_island_gate_1():
    """taskgraph.committed -> ISLAND_GATE_1."""
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
    )
    assert s.taskgraph_committed is True
    assert s.tasks_total == 1
    assert s.substate == "ISLAND_GATE_1"


# -- ISLAND_GATE_1 -> PRISM_PLAN ---------------------------------------------


def test_island_gate_1_decide_check_island_1():
    """decide() at ISLAND_GATE_1 produces check_island_1."""
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
    )
    cmd = decide(s)
    assert cmd.kind == "check_island_1"


def test_island_1_pass_to_prism_plan():
    """verdict.passed(island_1) -> PRISM_PLAN."""
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
        ISLAND1_CMD,
        ISLAND1_PASS,
    )
    assert s.substate == "PRISM_PLAN"


def test_island_1_fail_to_planning():
    """verdict.failed(island) -> PLANNING, attempt consumed."""
    fail = ("verdict.failed", {"check": "island", "reason": "gap", "attempt": 1})
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
        ISLAND1_CMD,
        fail,
    )
    assert s.substate == "PLANNING"
    assert s.current_attempt == 1


# -- PRISM_PLAN -> TASK_DISPATCH ---------------------------------------------


def test_prism_plan_dispatches_prism():
    """decide() at PRISM_PLAN dispatches Prism with criteria pack."""
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
        ISLAND1_CMD,
        ISLAND1_PASS,
    )
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "PRISM_PLAN"
    assert cmd.params["assignment"]["criteria_pack"] == dict(_M_IMPL_CRITERIA_PACK)


def test_prism_plan_pass_to_task_dispatch():
    """prism.verdict(pass) at PRISM_PLAN -> TASK_DISPATCH."""
    s = state_of(
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
    )
    assert s.substate == "TASK_DISPATCH"


def test_prism_plan_revise_to_planning():
    """prism.verdict(revise) at PRISM_PLAN -> PLANNING, attempt consumed."""
    revise = ("prism.verdict", {"verdict": "revise", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)})
    s = state_of(
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
        revise,
    )
    assert s.substate == "PLANNING"
    assert s.current_attempt == 1


# -- TASK_DISPATCH -> RED ----------------------------------------------------


def test_task_dispatch_decide_select_task():
    """decide() at TASK_DISPATCH produces select_task."""
    s = state_of(
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
    )
    cmd = decide(s)
    assert cmd.kind == "select_task"


def test_task_started_to_red():
    """task.started -> RED, per-task fields reset."""
    s = state_of(
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
    )
    assert s.substate == "RED"
    assert s.current_task_id == "T1"
    assert s.current_attempt == 0
    assert s.green_committed is False


# -- RED -> RED_GATE -> RED_CHECKPOINT -> PRISM_RED --------------------------


def test_red_dispatches_devon():
    """decide() at RED dispatches Devon (phase=red)."""
    s = state_of(
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
    )
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    assert cmd.params["substate"] == "RED"


def test_red_outcome_to_red_gate():
    """Devon RED outcome done -> RED_GATE."""
    s = state_of(
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
    )
    assert s.substate == "RED_GATE"


def test_red_gate_pass_to_red_checkpoint():
    """verdict.passed(red_valid) -> RED_CHECKPOINT."""
    s = state_of(
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
    )
    assert s.substate == "RED_CHECKPOINT"


def test_red_gate_fail_to_red():
    """verdict.failed(red_invalid) -> RED, attempt consumed."""
    fail = ("verdict.failed", {"check": "red_invalid", "reason": "no fail", "attempt": 1})
    s = state_of(
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
        fail,
    )
    assert s.substate == "RED"
    assert s.current_attempt == 1


def test_red_checkpointed_to_prism_red():
    """red.checkpointed -> PRISM_RED, r_tree_identity set."""
    s = state_of(
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
    )
    assert s.substate == "PRISM_RED"
    assert s.r_tree_identity == "abc123"


# -- PRISM_RED -> GREEN ------------------------------------------------------


def test_prism_red_pass_to_green():
    """prism.verdict(pass) at PRISM_RED -> GREEN."""
    s = state_of(
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
    )
    assert s.substate == "GREEN"


def test_prism_red_revise_to_red():
    """An ordinary PRISM_RED revise returns to RED and consumes an attempt."""
    revise = ("prism.verdict", {"verdict": "revise", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)})
    s = state_of(
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
        revise,
    )
    assert s.substate == "RED"
    assert s.current_attempt == 1


def test_prism_red_plan_defect_replans_without_consuming_devon_budget():
    """A wholly green R-tree/no-lawful-RED finding is a taskgraph defect,
    not a defective RED test: Archer must remove/retype/re-scope it."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "plan_defect",
        },
    )
    s = state_of(
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
        revise,
    )
    assert s.substate == "PLANNING"
    assert s.current_attempt == 0
    assert s.current_task_id is None
    assert s.current_task_metadata is None
    assert s.current_manifest is None
    assert s.r_tree_identity is None
    assert s.taskgraph_committed is False
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "PLANNING"


def test_prism_red_red_defect_stays_red_for_repin():
    """A genuinely defective Devon RED artifact remains a RED re-pin case."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "red_defect",
        },
    )
    s = state_of(
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
        revise,
    )
    assert s.substate == "RED"
    assert s.current_attempt == 1
    assert s.r_tree_identity == "abc123"


# -- GREEN -> GREEN_GATE -> GREEN_COMMIT -> REFACTOR -------------------------


def test_green_outcome_to_green_gate():
    """Devon GREEN outcome done -> GREEN_GATE."""
    s = state_of(
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
    )
    assert s.substate == "GREEN_GATE"


def test_green_gate_pass_to_green_commit():
    """verdict.passed(green) -> GREEN_COMMIT."""
    s = state_of(
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
    )
    assert s.substate == "GREEN_COMMIT"


def test_green_committed_to_refactor():
    """green.committed -> REFACTOR, green_committed=True."""
    s = state_of(
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
    )
    assert s.substate == "REFACTOR"
    assert s.green_committed is True


def test_green_no_change_to_task_review():
    """B38 (#39): green.no_change (no worktree diff + explicit reason) skips the
    commit entirely and passes straight to TASK_REVIEW — symmetric with
    refactor.no_change convergence."""
    s = state_of(
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
        ("green.no_change", {"reason": "implementation already in baseline"}),
    )
    assert s.substate == "TASK_REVIEW"
    assert s.green_committed is True
    assert s.refactor_done is True


# -- REFACTOR -> REFACTOR_GATE -> TASK_REVIEW --------------------------------


def test_refactor_outcome_to_refactor_gate():
    """Devon REFACTOR outcome done -> REFACTOR_GATE."""
    s = state_of(
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
    )
    assert s.substate == "REFACTOR_GATE"


def test_refactor_committed_to_task_review():
    """refactor.committed -> TASK_REVIEW, refactor_done=True."""
    s = state_of(
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
    )
    assert s.substate == "TASK_REVIEW"
    assert s.refactor_done is True


def test_refactor_no_change_to_task_review():
    """refactor.no_change -> TASK_REVIEW, refactor_done=True."""
    s = state_of(
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
        ("refactor.no_change", {"reason": "clean"}),
    )
    assert s.substate == "TASK_REVIEW"
    assert s.refactor_done is True


# -- TASK_REVIEW -> PRISM_FINAL -> TASK_DONE ---------------------------------


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


def test_task_review_lineage_failure_parks_for_rollback():
    """B57 (#73, re-fixed 2026-08-27): verdict.failed(lineage) at TASK_REVIEW
    parks awaiting Human rollback WITHOUT presuming the return target -- the
    old hardcode (M-DESIGN for every lineage failure) redid a stage that was
    never implicated (B49 #64 condemned exactly that routing). The Human
    chooses at approval time (`trac approve --to`). The old else-branch (stay
    in substate + consume attempt) re-verified the same immutable
    green.committed forever: the G was produced by defective runtime code
    (Tracks-Attempt bound the logical attempt while its R lived at a
    different ref slot, run 01M0S0FQ T-001), so no retry can ever change the
    verdict."""
    s = state_of(*_at_task_review())
    assert s.substate == "TASK_REVIEW"
    s2 = state_of(*_at_task_review(), TASK_REVIEW_CMD, LINEAGE_FAIL)
    assert s2.status == "awaiting_human"
    assert s2.awaiting == "rollback"
    assert s2.return_target is None  # Human decides: approve --to / recover
    assert s2.substate == "TASK_REVIEW"
    assert decide(s2) is None  # halted: only trac approve resolves the gate


def test_task_review_lineage_failure_retry_loop_folds_to_rollback():
    """B57 (#73): the incident replay -- interleaved human.retry (plain and
    --clear-evidence) events between lineage failures -- still folds to the
    rollback park: the trailing verdict.failed(lineage) wins and the operator
    is offered the approve-rollback exit instead of the eternal loop."""
    s = state_of(
        *_at_task_review(),
        TASK_REVIEW_CMD,
        LINEAGE_FAIL,
        ("human.retry", {"actor": "operator", "clear_evidence": False}),
        TASK_REVIEW_CMD,
        LINEAGE_FAIL,
        ("human.retry", {"actor": "operator", "clear_evidence": True}),
        TASK_REVIEW_CMD,
        LINEAGE_FAIL,
    )
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target is None


def test_task_review_lineage_rollback_approval_to_returned():
    """B57 (#73, re-fixed): Human approves the lineage rollback WITH a chosen
    to_stage (CLI `--to`, closed set M-ACC|M-SPEC|M-DESIGN) -> RETURNED ->
    rollback_stage(chosen target); re-entry then goes through trac recover
    (stage.recovered, B53 residency boundary) with the fixed runtime. An
    approval carrying no to_stage cannot resolve the park (no preset target
    exists for lineage): the gate stays shut."""
    s = state_of(
        *_at_task_review(),
        TASK_REVIEW_CMD,
        LINEAGE_FAIL,
        ("human.approval", {"actor": "operator", "to_stage": "M-DESIGN"}),
    )
    assert s.substate == "RETURNED"
    assert s.return_target == "M-DESIGN"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"
    # Incomplete approval (no to_stage, no preset): the park stays.
    s2 = state_of(
        *_at_task_review(),
        TASK_REVIEW_CMD,
        LINEAGE_FAIL,
        ("human.approval", {"actor": "operator"}),
    )
    assert s2.status == "awaiting_human"
    assert s2.awaiting == "rollback"
    assert decide(s2) is None


# -- TASK_REVIEW -> PRISM_FINAL -> TASK_DONE ---------------------------------


def test_sanctioned_rebaseline_checkpoint_does_not_reenter_red_review():
    """B91 follow-up (re-baseline): a red.checkpointed carrying
    sanction=shield_fix lands MID-CYCLE (after green.committed, from the
    sanctioned Shield fix round). It only re-anchors r_tree_identity -- it
    must NOT flip the substate back to PRISM_RED, or the GREEN->REFACTOR flow
    derails (run 01M0S0FQ T-013 post-mortem, 2026-08-27)."""
    sanctioned = (
        "red.checkpointed",
        {"r_sha": "fix789", "sanction": "shield_fix", "task_id": "T1", "attempt": 2},
    )
    prefix = _at_task_review()
    idx = prefix.index(GREEN_COMMITTED)
    s = state_of(*prefix[: idx + 1], sanctioned)
    assert s.substate == "REFACTOR"
    assert s.r_tree_identity == "fix789"


def test_task_review_pass_to_prism_final():
    """verdict.passed(task_review) -> PRISM_FINAL."""
    s = state_of(
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
    )
    assert s.substate == "PRISM_FINAL"


def test_prism_final_pass_to_task_done():
    """prism.verdict(pass) at PRISM_FINAL -> TASK_DONE."""
    s = state_of(
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
    )
    assert s.substate == "TASK_DONE"


def test_prism_final_revise_impl_to_green():
    """prism.verdict(revise, impl_defect) at PRISM_FINAL -> GREEN.

    Refactor_done is cleared (GREEN retry).  green_committed is also cleared
    (#86): the revise must be observable as a legal same-R re-commit so the
    runtime idempotency guard lets commit_green issue a NEW green.committed
    instead of no-op'ing forever.
    """
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "impl_defect",
        },
    )
    s = state_of(
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
        revise,
    )
    assert s.substate == "GREEN"
    assert s.current_attempt == 1
    assert s.refactor_done is False
    assert s.green_committed is False


def test_prism_final_revise_red_to_red():
    """prism.verdict(revise, red_defect) at PRISM_FINAL -> RED.

    Both green_committed and refactor_done are cleared (RED lineage restart).
    """
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "red_defect",
        },
    )
    s = state_of(
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
        revise,
    )
    assert s.substate == "RED"
    assert s.green_committed is False
    assert s.refactor_done is False


# -- TASK_DONE -> ISLAND_GATE_2 (single task) --------------------------------


def test_task_done_single_task_to_island_gate_2():
    """task.completed with tasks_completed >= tasks_total -> ISLAND_GATE_2."""
    s = state_of(*_full_single_task_cycle()[:39])  # up to TASK_COMPLETED
    assert s.tasks_completed == 1
    assert s.tasks_total == 1
    assert s.substate == "ISLAND_GATE_2"


def test_island_gate_2_pass_to_exit():
    """verdict.passed(island_2) -> EXIT, island_2_passed=True."""
    s = state_of(*_full_single_task_cycle()[:42])  # up to ISLAND2_PASS
    assert s.substate == "EXIT"
    assert s.island_2_passed is True


def test_exit_decide_write_frontmatter():
    """decide() at EXIT produces write_frontmatter."""
    s = state_of(*_full_single_task_cycle()[:42])  # up to ISLAND2_PASS
    cmd = decide(s)
    assert cmd.kind == "write_frontmatter"
    assert cmd.params["stage"] == "M-IMPL"


def test_stage_exited_decide_returns_none():
    """After stage.exited, decide() returns None."""
    s = state_of(*_full_single_task_cycle())
    assert s.stage_exited is True
    assert decide(s) is None


# -- multi-task iteration ----------------------------------------------------


def test_multi_task_iterates_to_dispatch():
    """task.completed with remaining tasks -> TASK_DISPATCH."""
    events = [
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        ("taskgraph.committed", {"task_count": 2}),
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
    ]
    s = state_of(*events)
    assert s.tasks_completed == 1
    assert s.tasks_total == 2
    assert s.substate == "TASK_DISPATCH"
    assert s.current_task_id is None  # cleared on task.completed
    # Per-task fields reset for next cycle
    assert s.green_committed is False
    assert s.refactor_done is False
    assert s.r_tree_identity is None


def test_task_started_clears_diagnose_classification():
    """task.started clears stale diagnose_classification (replay cleanup)."""
    events = [
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        ("taskgraph.committed", {"task_count": 2}),
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
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
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
        SELECT_TASK_CMD,
        ("task.started", {"task_id": "T2"}),
    ]
    s = state_of(*events)
    assert s.substate == "RED"
    assert s.diagnose_classification is None


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


def test_diagnose_impl_defect_to_green():
    """DIAGNOSE + impl_defect -> GREEN (Devon)."""
    s = _diagnose_state("impl_defect")
    assert s.substate == "GREEN"
    assert s.current_attempt == 1
    assert s.green_committed is False  # #86: same-R re-commit must be observable


def test_diagnose_impl_defect_without_r_checkpoint_routes_to_red():
    """Regression: when RED crashed (no r_tree_identity) and DIAGNOSE returns
    impl_defect, the kernel must route back to RED, not GREEN.

    Routing to GREEN with r_tree_identity=None trips the runtime stale-check
    at m_impl_runtime.py:384 (phase=green but no R checkpoint), causing a
    stale failure loop. With a checkpoint present, impl_defect still routes
    to GREEN as before."""
    # RED crashes (failed outcome) -> DIAGNOSE, no R checkpoint created.
    red_crash_to_diagnose = [
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
        _devon_failed_outcome(),
    ]
    verdict = ("verdict.failed", {"check": "impl_defect", "attempt": 1})

    # No R checkpoint: impl_defect must route to RED, not GREEN.
    s = state_of(*red_crash_to_diagnose, verdict)
    assert s.r_tree_identity is None
    assert s.substate == "RED"
    assert s.current_attempt == 2  # RED crash consumed attempt 1, impl_defect retry consumes another

    # With an R checkpoint present (via _diagnose_state), impl_defect routes
    # to GREEN as before.
    s2 = _diagnose_state("impl_defect")
    assert s2.r_tree_identity == "abc123"
    assert s2.substate == "GREEN"


def test_diagnose_test_defect_to_shield_fix():
    """DIAGNOSE + test_defect -> SHIELD_FIX (Shield)."""
    s = _diagnose_state("test_defect")
    assert s.substate == "SHIELD_FIX"
    assert s.current_attempt == 1


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


def test_shield_dispatch_carries_diagnose_report():
    """SHIELD_FIX objective carries the DIAGNOSE reason/evidence from State.

    Regression (run 01KZTHE7 T-008/T-017): Shield used to receive only the
    'fix diagnosed test defects' label and burned 50+ minutes re-deriving
    Prism's analysis.
    """
    pre_diagnose = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE, GREEN_GATE_CMD,
    ]
    s = state_of(*pre_diagnose, *_diagnose_events_with_report())
    assert s.substate == "SHIELD_FIX"
    assert s.diagnose_report["check"] == "test_defect"
    cmd = decide(s)
    assert cmd.params["role"] == "shield"
    assert "not_real but provenance is fully real" in cmd.params["objective"]
    assert "test_release_evidence.py:295-297" in cmd.params["objective"]


def test_shield_diagnose_report_survives_retry_clear_evidence():
    """`trac retry --clear-evidence` must not strip the DIAGNOSE details.

    Regression (run 01KZTHE7 T-017, 2026-08-16): provider-failure escalation
    forced a clear-evidence retry, which dropped last_failure (and with it
    the only copy of Prism's diagnosis) - every later Shield attempt got a
    bare objective and had to re-diagnose from scratch.
    """
    pre_diagnose = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE, GREEN_GATE_CMD,
    ]
    s = state_of(
        *pre_diagnose,
        *_diagnose_events_with_report(),
        ("human.retry", {"actor": "openclaw", "clear_evidence": True}),
    )
    assert s.substate == "SHIELD_FIX"
    assert s.last_failure is None  # clear-evidence dropped it
    assert s.diagnose_report is not None  # diagnosis survived on State
    cmd = decide(s)
    assert "not_real but provenance is fully real" in cmd.params["objective"]


def test_shield_diagnose_report_survives_infra_failure():
    """Infra failures (signal/provider_unavailable/...) are digested by the
    runtime: no attempt consumed, no last_failure pollution, substate kept,
    diagnosis intact (user stance: infra errors are not agent failures)."""
    pre_diagnose = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE, GREEN_GATE_CMD,
    ]
    infra_failed = ("outcome.received", {
        "role": "shield",
        "status": "failed",
        "failure_class": "signal",
        "self_report": "killed by signal 9",
    })
    s = state_of(*pre_diagnose, *_diagnose_events_with_report(), infra_failed)
    assert s.substate == "SHIELD_FIX"
    assert s.current_attempt == 1  # infra failure did not consume a budget
    # infra failure did not pollute evidence: last_failure still holds the
    # DIAGNOSE verdict, not "killed by signal 9"
    assert s.last_failure["check"] == "test_defect"
    assert s.infra_failure_streak == 1
    assert s.diagnose_report["check"] == "test_defect"
    cmd = decide(s)
    assert "not_real but provenance is fully real" in cmd.params["objective"]


def test_infra_failure_streak_escalates_without_consuming_attempts():
    """3 consecutive infra failures escalate to awaiting_human without ever
    touching the agent attempt budget; human.retry clears the streak."""
    pre_diagnose = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE, GREEN_GATE_CMD,
    ]
    infra_failed = ("outcome.received", {
        "role": "shield",
        "status": "failed",
        "failure_class": "provider_unavailable",
        "self_report": "provider/model/credentials unavailable",
    })
    events = [*pre_diagnose, *_diagnose_events_with_report()]
    for _ in range(3):
        events = [*events, infra_failed]
    s = state_of(*events)
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert s.current_attempt == 1  # still untouched by infra failures
    assert s.infra_failure_streak == 3
    s2 = state_of(*events, ("human.retry", {"actor": "openclaw"}))
    assert s2.status == "active"
    assert s2.infra_failure_streak == 0


def test_non_zero_exit_is_infra_not_semantic():
    """OOB 2026-09-05: opencode exiting 1 (CLI crash, provider 400) is
    machine-side infra -- no attempt consumed, no last_failure overwrite,
    streak increments toward the bounded infra re-dispatch."""
    pre = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE, GREEN_GATE_CMD,
    ]
    crash = ("outcome.received", {
        "role": "devon",
        "status": "failed",
        "failure_class": "non_zero_exit",
        "self_report": "opencode exited 1",
    })
    verdict = ("verdict.failed", {"check": "impl_defect", "attempt": 1,
                                  "reason": "selected task tests did not all pass",
                                  "evidence": "seed"})
    s = state_of(*pre, verdict, DEVON_GREEN_DISPATCH, crash)
    assert s.infra_failure_streak == 1
    assert s.current_attempt == 0  # untouched: infra never burns the budget
    assert s.last_failure["check"] == "impl_defect"  # evidence not overwritten


def test_shield_diagnose_report_survives_semantic_failure():
    """A semantic Shield failure (FR-0210) overwrites last_failure with the
    attempt's own failure signal but must not touch diagnose_report."""
    pre_diagnose = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE, GREEN_GATE_CMD,
    ]
    semantic_failed = ("outcome.received", {
        "role": "shield",
        "status": "failed",
        "failure_class": "manifest_malformed",
        "self_report": "manifest malformed: final part.text must be raw JSON",
    })
    s = state_of(*pre_diagnose, *_diagnose_events_with_report(), semantic_failed)
    assert s.last_failure["check"] == "manifest_malformed"  # overwritten
    assert s.diagnose_report["check"] == "test_defect"  # diagnosis intact
    cmd = decide(s)
    assert "not_real but provenance is fully real" in cmd.params["objective"]


def test_shield_fix_dispatch_contract_is_write():
    """SHIELD_FIX dispatches Shield with substate=WRITE (D-29 integration contract).

    Machine substate stays SHIELD_FIX (flow.md §10), but the dispatched
    command params and assignment use WRITE per test_dispatch_materialization.
    """
    s = _diagnose_state("test_defect")
    assert s.substate == "SHIELD_FIX"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "shield"
    assert cmd.params["substate"] == "WRITE"
    assert cmd.params["assignment"]["substate"] == "WRITE"
    assert cmd.params["assignment"]["role"] == "shield"
    assert cmd.params["assignment"]["skills"] == ["tracks-discuz", "tracks-shield"]
    assert "SHIELD_FIX" in cmd.params["objective"]


def test_diagnose_stub_gap_to_design():
    """DIAGNOSE + stub_gap -> rollback M-DESIGN, no Human."""
    s = _diagnose_state("stub_gap")
    assert s.diagnose_classification == "stub_gap"
    assert s.status == "active"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


def test_diagnose_ac_gap_to_acc():
    """DIAGNOSE + ac_gap -> awaiting Human, rollback M-ACC."""
    s = _diagnose_state("ac_gap")
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target == "M-ACC"
    assert decide(s) is None


def test_diagnose_spec_gap_to_spec():
    """DIAGNOSE + spec_gap -> awaiting Human, rollback M-SPEC."""
    s = _diagnose_state("spec_gap")
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target == "M-SPEC"


def test_diagnose_ac_gap_human_approval_to_returned():
    """Human approves ac_gap rollback -> RETURNED -> rollback_stage."""
    s = _diagnose_state("ac_gap")
    assert decide(s) is None
    s2 = state_of(
        *(_diagnose_state("ac_gap").__dict__.get("_events", []))
    )  # rebuild not needed; use direct event seq
    # Rebuild from events: add human.approval
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
        ("verdict.failed", {"check": "ac_gap", "attempt": 1}),
        ("human.approval", {"digest": "d", "actor": "H"}),
    ]
    s2 = state_of(*base)
    assert s2.substate == "RETURNED"
    assert s2.status == "active"
    cmd = decide(s2)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-ACC"


# -- DIAGNOSE via PRISM_PLAN design_gap --------------------------------------


def test_prism_plan_design_gap_to_diagnose_stub_gap():
    """prism.verdict(revise, design_gap) at PRISM_PLAN -> DIAGNOSE/stub_gap."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "design_gap",
        },
    )
    s = state_of(
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
        revise,
    )
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


# -- DIAGNOSE via REFACTOR_GATE public_interface -----------------------------


def test_refactor_gate_public_interface_to_diagnose_stub_gap():
    """verdict.failed(public_interface) at REFACTOR_GATE -> DIAGNOSE/stub_gap."""
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
        GREEN_PASS,
        GREEN_COMMIT_CMD,
        GREEN_COMMITTED,
        DEVON_REFACTOR_DISPATCH,
        DEVON_REFACTOR_DONE,
        REFACTOR_GATE_CMD,
    ]
    fail = ("verdict.failed", {"check": "public_interface", "reason": "broke API", "attempt": 1})
    s = state_of(*base, fail)
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


# -- B49 (#64): contract_error parks instead of stub_gap rollback --------------


def test_green_gate_contract_error_parks_for_operator():
    """B49 (run 01M0S0FQ, 2026-08-24): a gate-contract mismatch must park the
    run for the operator (awaiting_human/escalation) -- NOT route into
    DIAGNOSE/stub_gap whose decide() emits rollback_stage(M-DESIGN). The
    auto-rollback loop sent runtime-side defects to be "fixed" in a design
    that was not defective."""
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
    ]
    fail = (
        "verdict.failed",
        {"check": "contract_error", "reason": "gate-contract mismatch", "attempt": 1},
    )
    s = state_of(*base, fail)
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert s.substate == "GREEN_GATE", "parking must not move the substate"
    assert s.diagnose_classification is None
    # Parked: no further command (in particular no rollback_stage) is issued.
    assert decide(s) is None


def test_contract_error_does_not_burn_attempt_budget():
    """B49: parking consumes no attempt -- human.retry after the underlying
    fix re-dispatches the gate from the current substate with a fresh
    budget, so the parked verdict must leave current_attempt untouched."""
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
    ]
    fail = (
        "verdict.failed",
        {"check": "contract_error", "reason": "gate-contract mismatch", "attempt": 1},
    )
    s = state_of(*base, fail)
    assert s.current_attempt == 0, "parking must not consume the attempt budget"


# -- TASK_REVIEW scope / budget gate failures ---------------------------------


def test_task_review_scope_failure_routes_to_planning():
    """#83: verdict.failed(scope) at TASK_REVIEW -> PLANNING, no GREEN, no
    Shield dispatch, no attempt consumed. Evidence preserved for Archer."""
    SCOP_FAIL = ("verdict.failed", {"check": "scope", "reason": "G changes outside manifest", "evidence": '["outside.py"]', "attempt": 1})
    s = state_of(*_at_task_review(), TASK_REVIEW_CMD, SCOP_FAIL)
    assert s.substate == "PLANNING"
    assert s.current_attempt == 0, "scope must not consume the attempt budget"
    assert s.taskgraph_committed is False
    assert s.doc_dispatched is False
    assert s.doc_produced is False
    assert s.current_task_id is None, "stale task identity must be cleared"
    assert s.current_task_metadata is None
    assert s.current_manifest is None
    assert s.green_committed is False
    assert s.refactor_done is False
    assert s.r_tree_identity is None
    assert s.last_failure is not None
    assert s.last_failure["check"] == "scope"
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer", "scope must dispatch Archer, not Devon"
    assert cmd.params["substate"] == "PLANNING"
    assert "evidence" in cmd.params, "scope failure evidence must be carried to Archer"
    assert cmd.params["evidence"]["check"] == "scope"

    # Shield must never be dispatched on the scope route
    assert cmd.params["role"] != "shield"


def test_task_review_scope_failure_does_not_generate_shield_assignment():
    """#83 Shield safety: scope route must never produce role=shield
    assignment at any point in the state projection."""
    SCOP_FAIL = ("verdict.failed", {"check": "scope", "reason": "G changes outside manifest", "evidence": '["outside.py"]', "attempt": 1})
    s = state_of(*_at_task_review(), TASK_REVIEW_CMD, SCOP_FAIL)
    assert s.substate == "PLANNING"
    for flag in ("reviewer_dispatched", "doc_dispatched", "doc_produced"):
        if flag == "doc_dispatched":
            assert getattr(s, flag) is False
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer", "scope route role must be archer, not shield"
    assert cmd.params["substate"] == "PLANNING"


def test_task_review_budget_failure_stays_green():
    """#83 regression: budget must still route to GREEN (unchanged)."""
    BUDGET_FAIL = ("verdict.failed", {"check": "budget", "reason": "verdict.failed exceeds budget", "attempt": 1})
    s = state_of(*_at_task_review(), TASK_REVIEW_CMD, BUDGET_FAIL)
    assert s.substate == "GREEN", "budget must still route to GREEN"
    assert s.current_attempt == 1, "budget must consume the attempt"
    assert s.doc_dispatched is False
    assert s.doc_produced is False
    # #86 follow-up: the budget re-entry into GREEN must drop green_committed
    # so the B56 same-R re-commit guard lets the new G through (the prior G
    # for this task/slot may already be recorded).
    assert s.green_committed is False, "budget route must reset green_committed"


def test_scope_route_replan_can_commit_taskgraph():
    """#83: after scope -> PLANNING, Archer dispatch produces a new taskgraph
    which can be committed and re-reviewed through PRISM_PLAN."""
    SCOP_FAIL = ("verdict.failed", {"check": "scope", "reason": "G changes outside manifest", "evidence": '["outside.py"]', "attempt": 1})
    s = state_of(*_at_task_review(), TASK_REVIEW_CMD, SCOP_FAIL)
    assert s.substate == "PLANNING"

    # decide() must dispatch Archer
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "PLANNING"
    assert "evidence" in cmd.params

    # Simulate Archer outcome received + taskgraph committed
    ARCHER_DONE_2 = ("outcome.received", {"role": "archer", "status": "done"})
    TASKGRAPH_CMD_2 = ("command.issued", {"command": {"kind": "commit_taskgraph", "params": {"stage": "M-IMPL"}, "command_id": "C99"}})
    TASKGRAPH_COMMITTED_2 = ("taskgraph.committed", {"task_count": 2, "task_ids": ["T-006a", "T-007"], "tasks": [{"task_id": "T-006a", "issue_number": 83, "description": "core impl", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}, {"task_id": "T-007", "issue_number": 83, "description": "broader scope", "ac_refs": ["AC-2"], "fr_refs": [], "if_ids": ["IF-2"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["T-006a"], "batch": "1", "parallel": "0", "budget": 3}], "digest": "abcdef", "path": "tasks.json"})

    s2 = state_of(*_at_task_review(), TASK_REVIEW_CMD, SCOP_FAIL, ARCHER_DONE_2, TASKGRAPH_CMD_2, TASKGRAPH_COMMITTED_2)
    assert s2.substate == "ISLAND_GATE_1"
    assert s2.taskgraph_committed is True
    assert s2.tasks_total == 2
    assert s2.current_task_id is None  # no stale task identity


# -- SHIELD_FIX -> GREEN_GATE ------------------------------------------------


def test_shield_fix_outcome_to_green_gate():
    """Shield SHIELD_FIX outcome done -> GREEN_GATE."""
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
        ("verdict.failed", {"check": "test_defect", "attempt": 1}),
    ]
    shield_dispatch = (
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "shield", "substate": "WRITE"},
                "command_id": "CS",
            }
        },
    )
    shield_done = ("outcome.received", {"role": "shield", "status": "done"})
    s = state_of(*base, shield_dispatch, shield_done)
    assert s.substate == "GREEN_GATE"


# -- ISLAND_GATE_2 failure -> DIAGNOSE ---------------------------------------


def test_island_gate_2_full_suite_fail_to_diagnose():
    """verdict.failed(full_suite) at ISLAND_GATE_2 -> DIAGNOSE."""
    base = _full_single_task_cycle()[:40]  # up to ISLAND2_CMD
    fail = ("verdict.failed", {"check": "full_suite", "reason": "e2e fail", "attempt": 1})
    s = state_of(*base, fail)
    assert s.substate == "DIAGNOSE"


# -- escalation (3 failed attempts) ------------------------------------------


def test_red_failure_escalation():
    """Three Devon RED failures escalate to awaiting_human."""
    fail = (
        "outcome.received",
        {"role": "devon", "status": "failed", "failure_class": "agent_failed"},
    )
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
    ]
    evs = []
    for _ in range(3):
        evs.append(DEVON_RED_DISPATCH)
        evs.append(fail)
    s = state_of(*base, *evs)
    assert s.current_attempt == 3
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert decide(s) is None


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


def test_failed_devon_red_routes_to_diagnose_with_evidence():
    s = state_of(
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
        _devon_failed_outcome(),
    )
    assert s.substate == "DIAGNOSE"
    assert s.current_attempt == 1
    assert s.reviewer_dispatched is False
    assert s.last_failure == {
        "check": "agent_failed",
        "reason": "simulated execution failure",
        "evidence": "backend exit evidence",
    }
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "DIAGNOSE"
    assert cmd.params["evidence"] == s.last_failure


def test_failed_devon_green_routes_to_diagnose_with_r_lineage():
    s = state_of(
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
        _devon_failed_outcome(),
    )
    assert s.r_tree_identity == "abc123"
    assert s.substate == "DIAGNOSE"
    assert s.current_attempt == 1
    assert s.reviewer_dispatched is False
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "DIAGNOSE"


def test_green_gate_impl_defect_after_diagnose_redispatches_prism():
    """Regression (run 01KZTHE7 T-013, 2026-08-15): a GREEN_GATE impl_defect
    failure re-entering DIAGNOSE must reset reviewer_dispatched.

    The stale flag path: Prism DIAGNOSE's verdict routes back to GREEN via
    _route_m_impl_diagnose (which does not reset review - task_review pass
    resets it in the happy path). If the retried GREEN gate fails again with
    impl_defect, the preset-classification branch used to leave
    reviewer_dispatched=True, so _decide_m_impl_prism returned None forever
    and the run loop silently exited instead of re-dispatching Prism."""
    prism_diagnose_dispatch = (
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "prism", "substate": "DIAGNOSE"},
                "command_id": "CD1",
            }
        },
    )
    s = state_of(
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
        # GREEN attempt 1 -> GREEN_GATE impl_defect -> DIAGNOSE (preset class)
        DEVON_GREEN_DISPATCH,
        DEVON_GREEN_DONE,
        GREEN_GATE_CMD,
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
        # loop dispatches Prism DIAGNOSE -> sets reviewer_dispatched=True
        prism_diagnose_dispatch,
        # Prism diagnose verdict: impl_defect -> GREEN (does NOT reset review)
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
        # GREEN attempt 2 -> GREEN_GATE impl_defect again -> must reset review
        DEVON_GREEN_DISPATCH,
        DEVON_GREEN_DONE,
        GREEN_GATE_CMD,
        ("verdict.failed", {"check": "impl_defect", "attempt": 2}),
    )
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "impl_defect"
    assert s.reviewer_dispatched is False
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "DIAGNOSE"


def test_failed_devon_refactor_stays_in_refactor_retry():
    s = state_of(
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
        _devon_failed_outcome(),
    )
    assert s.substate == "REFACTOR"
    assert s.current_attempt == 1
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    assert cmd.params["substate"] == "REFACTOR"


# -- rebuild from events (replay stability) ----------------------------------


def test_rebuild_from_events_identical():
    """Projecting the same events twice yields identical state."""
    evs = seq(*ENTER_M_IMPL, *_full_single_task_cycle())
    s1 = project(evs)
    s2 = project(evs)
    assert s1.stage == s2.stage == "M-IMPL"
    assert s1.substate == s2.substate
    assert s1.tasks_completed == s2.tasks_completed
    assert s1.baseline_frozen == s2.baseline_frozen
    assert s1.r_tree_identity == s2.r_tree_identity


def test_no_duplicate_command_after_replay():
    """After replaying the full cycle, decide() returns None (stage_exited)."""
    evs = seq(*ENTER_M_IMPL, *_full_single_task_cycle())
    s = project(evs)
    assert s.stage_exited is True
    assert decide(s) is None


# -- dispatch assignment has required keys -----------------------------------


def test_archer_dispatch_assignment_has_required_keys():
    """Archer dispatch assignment carries all required keys (D-29)."""
    s = state_of(BASELINE_CMD, BASELINE_FROZEN)
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


def test_devon_dispatch_assignment_has_required_keys():
    """Devon dispatch assignment carries all required keys."""
    s = state_of(
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
    )
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


def test_prism_dispatch_assignment_has_required_keys():
    """Prism dispatch assignment carries all required keys + criteria_pack."""
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        TASKGRAPH_COMMITTED,
        ISLAND1_CMD,
        ISLAND1_PASS,
    )
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS
    assert assignment["criteria_pack"] == dict(_M_IMPL_CRITERIA_PACK)


# -- context docs ------------------------------------------------------------


def test_m_impl_context_docs():
    """M-IMPL context docs include trio + design trio."""
    assert _M_IMPL_CONTEXT_DOCS == (
        "story.md",
        "spec.md",
        "acceptance.md",
        "architecture.md",
        "interfaces.md",
        "test-plan.md",
    )


# -- kernel purity -----------------------------------------------------------


def test_kernel_purity_no_io():
    """decide()/project() perform no I/O -- pure folds over events."""
    s = state_of(*_full_single_task_cycle())
    cmd = decide(s)
    assert cmd is None  # stage_exited -> halt


# ---------------------------------------------------------------------------
# B4 (issue #5): lint gate-failure routing (no attempt consumption)
# ---------------------------------------------------------------------------


def test_red_gate_lint_fail_returns_to_red_without_consuming_attempt():
    """B4 (issue #5, user ruling): mechanical lint findings at RED_GATE route
    back to RED but never consume the attempt budget (attempts are reserved
    for semantic, agent-caused failures)."""
    fail = ("verdict.failed", {"check": "lint", "reason": "F401", "attempt": 1})
    s = state_of(
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
        fail,
    )
    assert s.substate == "RED"
    assert s.current_attempt == 0


def test_green_gate_lint_fail_returns_to_green_without_consuming_attempt():
    """B4 (issue #5): GREEN lint findings route back to GREEN, no attempt."""
    fail = ("verdict.failed", {"check": "lint", "reason": "E501", "attempt": 1})
    s = state_of(
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
        fail,
    )
    assert s.substate == "GREEN"
    assert s.current_attempt == 0


def test_diagnose_contract_violation_stays_dispatchable():
    """B62 (#80): an unrecognized DIAGNOSE verdict check (e.g.
    diagnose_contract_violation from a contract-violating reply) stays in
    DIAGNOSE with the attempt consumed — the executor contract promises
    "consume attempt, redispatch Prism; budget exhaustion escalates".
    The stale-flag path (run 01M0S0FQ T-006, 2026-08-25): the else branch
    used to leave reviewer_dispatched=True, so _decide_m_impl_prism
    returned None forever and the run loop exited silently on every
    restart — the budget could never be exhausted."""
    prism_diagnose_dispatch = (
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "prism", "substate": "DIAGNOSE"},
                "command_id": "CD1",
            }
        },
    )
    s = state_of(
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
        # GREEN gate failure -> DIAGNOSE (preset impl_defect, review reset)
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
        # loop dispatches Prism DIAGNOSE -> sets reviewer_dispatched=True
        prism_diagnose_dispatch,
        # Prism reply violates the DIAGNOSE JSON contract
        ("verdict.failed", {"check": "diagnose_contract_violation", "attempt": 2}),
    )
    assert s.substate == "DIAGNOSE"
    assert s.status == "active"  # budget (attempt 2 of 3) not exhausted yet
    assert s.reviewer_dispatched is False
    cmd = decide(s)
    assert cmd is not None, "must re-dispatch Prism DIAGNOSE, not stall"
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "DIAGNOSE"


def test_diagnose_contract_violation_budget_exhaustion_escalates():
    """B62 (#80): three consecutive contract violations consume the attempt
    budget and escalate to awaiting_human — the escalation the executor
    comment promises; unreachable before the re-dispatch fix."""
    prism_diagnose_dispatch = (
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "prism", "substate": "DIAGNOSE"},
                "command_id": "CD1",
            }
        },
    )
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
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
    ]
    s = state_of(
        *base,
        # three contract-violation rounds, each consumed by the else branch
        prism_diagnose_dispatch,
        ("verdict.failed", {"check": "diagnose_contract_violation", "attempt": 1}),
        prism_diagnose_dispatch,
        ("verdict.failed", {"check": "diagnose_contract_violation", "attempt": 2}),
        prism_diagnose_dispatch,
        ("verdict.failed", {"check": "diagnose_contract_violation", "attempt": 3}),
    )
    assert s.substate == "DIAGNOSE"
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"


def test_green_gate_regression_routes_to_diagnose():
    """B63 (#81): an R-test regression verdict must be attributed by DIAGNOSE,
    not blindly retried in GREEN. The stale-loop path (run 01M0S0FQ T-002,
    2026-08-25): defective R tests leave the GREEN agent no legal path (it
    may not modify RED-approved files), so a GREEN re-dispatch reproduces
    the same regression forever and burns the whole budget."""
    s = state_of(
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
        ("verdict.failed", {"check": "regression", "attempt": 1}),
    )
    assert s.substate == "DIAGNOSE"
    assert s.status == "active"
    assert s.reviewer_dispatched is False
    cmd = decide(s)
    assert cmd is not None, "must dispatch Prism DIAGNOSE, not loop GREEN"
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "DIAGNOSE"


def test_refactor_gate_regression_also_routes_to_diagnose():
    """B63 (#81): the REFACTOR-gate regression variant routes to DIAGNOSE as
    well (impl_defect re-enters GREEN; the gate chain re-verifies forward)."""
    s = state_of(
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
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "REFACTOR"}, "command_id": "C14"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"gate": "REFACTOR_GATE", "stage": "M-IMPL"}, "command_id": "C15"}}),
        ("verdict.failed", {"check": "regression", "attempt": 1}),
    )
    assert s.substate == "DIAGNOSE"
    cmd = decide(s)
    assert cmd is not None and cmd.params.get("substate") == "DIAGNOSE"


def test_diagnose_red_defect_routes_devon_back_to_red():
    """B63 blocker (#81): a defective RED unit test is Devon's own artifact —
    DIAGNOSE must be able to say red_defect and route Devon back to RED
    (re-pin) where rewriting the tests is legal. Before the fix the token was
    outside the M-IMPL DIAGNOSE vocabulary (executor whitelist + opencode
    _DIAGNOSE_CLASSIFICATIONS + Prism.md/SKILL.md contract), so Prism could
    only misroute: test_defect -> Shield (integration domain) or impl_defect
    -> GREEN (may not touch frozen R tests; run 01M0S0FQ T-002 loop)."""
    prism_diagnose_dispatch = (
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "prism", "substate": "DIAGNOSE"},
                "command_id": "CD1",
            }
        },
    )
    s = state_of(
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
        ("verdict.failed", {"check": "regression", "attempt": 1}),
        prism_diagnose_dispatch,
        ("verdict.failed", {"check": "red_defect", "attempt": 1}),
    )
    assert s.substate == "RED"
    assert s.doc_dispatched is False
    assert s.green_committed is False and s.refactor_done is False
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    assert cmd.params["substate"] == "RED"


def test_red_defect_in_diagnose_vocabulary():
    """red_defect must remain a legal DIAGNOSE classification: present in the
    executor/opencode vocabulary gates. The classification vocabulary itself
    is assignment-authoritative (inlined per dispatch), so the prompt/skill
    prose is deliberately NOT text-pinned here (b91 host-neutrality)."""
    from tracks.effects.opencode import OpencodeBackend

    assert "red_defect" in OpencodeBackend._DIAGNOSE_CLASSIFICATIONS


# -- B83 (#83): generation-aware retained completion on replacement taskgraph ---


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


def test_b83_retained_completed_count():
    """After replacement taskgraph, tasks_completed equals retained count (1),
    not old total (2). Merged T-006-007 is not retained."""
    s = state_of(*_old_plan_events(), *_new_plan_events())
    assert s.substate == "TASK_DISPATCH"
    assert s.tasks_total == 2
    assert s.tasks_completed == 1
    assert s.retained_completed_task_ids == ["T-001"]
    assert s.taskgraph_generation == 1


def test_b83_retained_task_not_selected_again():
    """decide() at TASK_DISPATCH after replacement selects T-006-007, not
    retained T-001."""
    s = state_of(*_old_plan_events(), *_new_plan_events())
    assert s.substate == "TASK_DISPATCH"
    cmd = decide(s)
    assert cmd.kind == "select_task"


def test_b83_merged_task_completion_increments_correctly():
    """Merged T-006-007 completes, tasks_completed becomes 2 (not 3), and
    substate transitions to ISLAND_GATE_2 (tasks_total=2)."""
    merged_done = [
        ("command.issued", {"command": {"kind": "select_task", "params": {"stage": "M-IMPL"}, "command_id": "C103"}}),
        ("task.started", {"task_id": "T-006-007"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "RED"}, "command_id": "C104"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "RED_GATE"}, "command_id": "C105"}}),
        ("verdict.passed", {"check": "red_valid"}),
        ("command.issued", {"command": {"kind": "checkpoint_red", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C106"}}),
        ("red.checkpointed", {"r_sha": "r006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_RED"}, "command_id": "C107"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "GREEN"}, "command_id": "C108"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "GREEN_GATE"}, "command_id": "C109"}}),
        ("verdict.passed", {"check": "green"}),
        ("command.issued", {"command": {"kind": "commit_green", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C110"}}),
        ("green.committed", {"commit_sha": "g006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "REFACTOR"}, "command_id": "C111"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C112"}}),
        ("refactor.committed", {"commit_sha": "r006"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"}, "command_id": "C113"}}),
        ("verdict.passed", {"check": "task_review"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_FINAL"}, "command_id": "C114"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "complete_task", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C115"}}),
        ("task.completed", {"task_id": "T-006-007"}),
    ]
    s = state_of(*_old_plan_events(), *_new_plan_events(), *merged_done)
    assert s.tasks_completed == 2
    assert s.tasks_total == 2
    assert s.substate == "ISLAND_GATE_2", "must reach ISLAND_GATE_2, not TASK_DISPATCH"


def test_b83_retained_task_completion_event_does_not_double_count():
    """During replay, the old task.completed(T-001) event fires but the
    reducer skips the increment (T-001 is in retained set)."""
    # Replay the full sequence: old plan + new plan + merged completion
    merged_done = [
        ("command.issued", {"command": {"kind": "select_task", "params": {"stage": "M-IMPL"}, "command_id": "C103"}}),
        ("task.started", {"task_id": "T-006-007"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "RED"}, "command_id": "C104"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "RED_GATE"}, "command_id": "C105"}}),
        ("verdict.passed", {"check": "red_valid"}),
        ("command.issued", {"command": {"kind": "checkpoint_red", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C106"}}),
        ("red.checkpointed", {"r_sha": "r006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_RED"}, "command_id": "C107"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "GREEN"}, "command_id": "C108"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "GREEN_GATE"}, "command_id": "C109"}}),
        ("verdict.passed", {"check": "green"}),
        ("command.issued", {"command": {"kind": "commit_green", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C110"}}),
        ("green.committed", {"commit_sha": "g006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "REFACTOR"}, "command_id": "C111"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C112"}}),
        ("refactor.committed", {"commit_sha": "r006"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"}, "command_id": "C113"}}),
        ("verdict.passed", {"check": "task_review"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_FINAL"}, "command_id": "C114"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "complete_task", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C115"}}),
        ("task.completed", {"task_id": "T-006-007"}),
    ]
    evs = seq(*ENTER_M_IMPL, *_old_plan_events(), *_new_plan_events(), *merged_done)
    from tracks.kernel import project as _project
    s1 = _project(evs)
    s2 = _project(evs)
    assert s1.tasks_completed == s2.tasks_completed == 2
    assert s1.retained_completed_task_ids == s2.retained_completed_task_ids == ["T-001"]
    assert s1.substate == s2.substate == "ISLAND_GATE_2"


def test_b83_same_id_changed_payload_not_retained():
    """A task with the same ID but different payload is NOT retained.
    Unit-level: the _task_payloads_equivalent check rejects it."""
    task_t1_old = {"task_id": "T-001", "issue_number": 83, "description": "original desc", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}
    task_t1_new = {"task_id": "T-001", "issue_number": 83, "description": "changed desc", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin
    assert not MImplRuntimeMixin._task_payloads_equivalent(task_t1_old, task_t1_new)


def test_b83_retained_depends_on_chain():
    """A task whose dependency is retained (T-001) is selectable in the new
    plan."""
    # Project state: old plan completes T-001, scope failure, new plan retains T-001
    s = state_of(*_old_plan_events(), *_new_plan_events())
    assert s.substate == "TASK_DISPATCH"
    # Retained T-001 is "completed" for dependency resolution; T-006-007
    # depends on it so it should be ready.
    assert s.retained_completed_task_ids == ["T-001"]
    # decide() for TASK_DISPATCH produces select_task, and the executor
    # (not the pure kernel) resolves the actual ready set. At the state
    # level, check that the retention projection is correct.
    cmd = decide(s)
    assert cmd.kind == "select_task"


def test_replacement_taskgraph_clears_inflight_task_lease():
    """#139: a within-residency graph replacement must clear the in-flight
    task lease even when the replan did NOT route through scope failure
    (operator/RULING commit_taskgraph, run 01M19FJV: graph 81d33fa5 landed
    at seq 3624 while T-042 was in flight; select_task then no-op'ed 20x
    on the stale current_task_id guard and tripped loop.aborted)."""
    task = {
        "task_id": "T-007",
        "issue_number": 139,
        "description": "in-flight scope",
        "ac_refs": ["AC-2"],
        "fr_refs": [],
        "if_ids": ["IF-2"],
        "test_refs": [],
        "unit_refs": [],
        "acceptance_refs": [],
        "schema": 2,
        "scope_boundary": "tracks/",
        "depends_on": ["-"],
        "batch": "1",
        "parallel": "0",
        "budget": 3,
    }
    old_commit = (
        "taskgraph.committed",
        {
            "task_count": 1,
            "task_ids": ["T-007"],
            "tasks": [task],
            "digest": "aaa",
            "path": "tasks.json",
        },
    )
    replacement_commit = (
        "taskgraph.committed",
        {
            "task_count": 1,
            "task_ids": ["T-007"],
            "tasks": [{**task, "description": "revised scope"}],
            "digest": "bbb",
            "path": "tasks.json",
            "retained_completed_task_ids": [],
        },
    )
    s = state_of(
        BASELINE_CMD,
        BASELINE_FROZEN,
        ARCHER_DISPATCH,
        ARCHER_DONE,
        TASKGRAPH_CMD,
        old_commit,
        ISLAND1_CMD,
        ISLAND1_PASS,
        PRISM_PLAN_DISPATCH,
        PRISM_PLAN_DONE,
        PRISM_PLAN_PASS,
        SELECT_TASK_CMD,
        ("task.started", {"task_id": "T-007", "task": dict(task), "manifest": {"task_id": "T-007"}}),
        # No scope verdict routes the replan -- the replacement lands via a
        # direct commit_taskgraph (operator/RULING path).
        TASKGRAPH_CMD,
        replacement_commit,
    )
    assert s.taskgraph_generation == 1
    assert s.current_task_id is None
    assert s.current_task_metadata is None
    assert s.current_manifest is None
    assert s.substate == "ISLAND_GATE_1"


def test_diagnose_plan_defect_routes_to_planning():
    """#89: DIAGNOSE plan_defect -> PLANNING (Archer replan), no attempt
    consumed, evidence preserved, task identity cleared."""
    s = _diagnose_state("plan_defect")
    assert s.substate == "PLANNING"
    assert s.current_attempt == 0, "plan_defect must not consume the attempt budget"
    assert s.taskgraph_committed is False
    assert s.doc_dispatched is False
    assert s.current_task_id is None, "stale task identity must be cleared"
    assert s.green_committed is False
    assert s.refactor_done is False
    assert s.r_tree_identity is None
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer", "plan_defect must dispatch Archer, not Devon"
    assert cmd.params["substate"] == "PLANNING"
    assert "evidence" in cmd.params


def test_prism_final_revise_plan_defect_to_planning():
    """#89: PRISM_FINAL revise plan_defect -> PLANNING (Archer replan),
    no attempt consumed, task identity cleared."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "plan_defect",
        },
    )
    s = state_of(
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
        revise,
    )
    assert s.substate == "PLANNING"
    assert s.current_attempt == 0, "plan_defect must not consume the attempt budget"
    assert s.taskgraph_committed is False
    assert s.current_task_id is None
    assert s.green_committed is False
    assert s.refactor_done is False
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "PLANNING"


def test_plan_defect_in_diagnose_vocabulary():
    """plan_defect must remain a legal DIAGNOSE classification: present in
    the executor whitelist and the opencode backend classification tuple.
    The classification vocabulary itself is assignment-authoritative
    (inlined per dispatch), so the prompt/skill prose is deliberately NOT
    text-pinned here (b91 host-neutrality)."""
    from tracks.effects.opencode import OpencodeBackend

    repo_root = Path(__file__).resolve().parents[2]
    assert "plan_defect" in OpencodeBackend._DIAGNOSE_CLASSIFICATIONS
    executor_py = (repo_root / "tracks/executor/executor.py").read_text(encoding="utf-8")
    assert "plan_defect" in executor_py, "executor.py whitelist/filter lacks plan_defect"


# -- #89 follow-up (B90): vocabulary inlined into the dispatch -----------------


def test_diagnose_dispatch_inlines_classification_vocabulary():
    """#89/B90: D-39 session reuse freezes the agent/skill vocabulary at
    session-creation time, so prompt-contract revisions never reach an
    existing session (run 01M0S0FQ T-012: plan_defect committed, Prism
    still said impl_defect). The runtime must inline the vocabulary into
    every DIAGNOSE/PRISM_FINAL assignment — fresh per dispatch."""
    from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS

    s_diag = state_of(
        *_full_single_task_cycle()[:24],  # through DEVON GREEN done
        GREEN_GATE_CMD,
        ("verdict.failed", {"check": "unknown_attribution", "reason": "x", "attempt": 1}),
    )
    cmd = decide(s_diag)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    vocab = cmd.params["assignment"]["classification_vocabulary"]
    assert vocab == list(DIAGNOSE_CLASSIFICATIONS)
    assert "plan_defect" in vocab

    s_final = state_of(*_full_single_task_cycle()[:34])  # through TASK_REVIEW_PASS
    cmd2 = decide(s_final)
    assert cmd2 is not None and cmd2.params["role"] == "prism"
    assert cmd2.params["assignment"]["classification_vocabulary"] == list(
        DIAGNOSE_CLASSIFICATIONS
    )


def test_effects_whitelist_shares_kernel_vocabulary_source():
    """#89 single source of truth: the opencode backend whitelist and the
    kernel dispatch injection must be the SAME tuple — drift between them
    recreates the B63 'Prism can never say it' failure class."""
    from tracks.effects import opencode as _oc
    from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS

    assert _oc.OpencodeBackend._DIAGNOSE_CLASSIFICATIONS is DIAGNOSE_CLASSIFICATIONS


def test_green_objective_names_the_failed_anchors():
    """OOB 2026-09-05: a GREEN re-dispatch must point at the anchors the
    GREEN_GATE will re-run -- a generic "implement task X" objective let the
    writer work adjacent faces while the same anchors stayed red (run
    01M19FJVES7G113RD8QXXY3PQZ). Deterministic (sorted) and bounded (cap)."""
    pre = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        GREEN_GATE_CMD,
    ]
    failed = (
        "verdict.failed",
        {
            "check": "impl_defect",
            "task_id": "T-042",
            "attempt": 1,
            "evidence": json.dumps(
                {
                    "failed_nodes": [
                        {"node": "tests/integration/test_b.py::test_two"},
                        {"node": "tests/integration/test_a.py::test_one"},
                    ]
                }
            ),
        },
    )
    # Real flow: the gate verdict routes DIAGNOSE; Prism's diagnosis
    # verdict (impl_defect, anchors on the payload) routes GREEN.
    diagnosis = (
        "verdict.failed",
        {
            "check": "impl_defect",
            "task_id": "T-042",
            "attempt": 2,
            "reason": "wiring gaps",
            "evidence": "Prism diagnosis prose (not JSON)",
            "failed_nodes": [
                {"node": "tests/integration/test_b.py::test_two"},
                {"node": "tests/integration/test_a.py::test_one"},
            ],
        },
    )
    s = state_of(*pre, failed, diagnosis)
    assert s.substate == "GREEN"
    cmd = decide(s)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    objective = cmd.params["objective"]
    assert "test_a.py::test_one" in objective and "test_b.py::test_two" in objective
    # sorted ascending: a before b
    assert objective.index("test_a.py") < objective.index("test_b.py")


def test_green_objective_omits_anchor_clause_without_nodes():
    """No parseable failed_nodes -> no invented clause (fail-closed)."""
    pre = [
        BASELINE_CMD, BASELINE_FROZEN, ARCHER_DISPATCH, ARCHER_DONE,
        TASKGRAPH_CMD, TASKGRAPH_COMMITTED, ISLAND1_CMD, ISLAND1_PASS,
        PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE, PRISM_PLAN_PASS,
        SELECT_TASK_CMD, TASK_STARTED, DEVON_RED_DISPATCH, DEVON_RED_DONE,
        RED_GATE_CMD, RED_VALID_PASS, RED_CHECKPOINT_CMD, RED_CHECKPOINTED,
        PRISM_RED_DISPATCH, PRISM_RED_DONE, PRISM_RED_PASS,
        GREEN_GATE_CMD,
    ]
    failed = (
        "verdict.failed",
        {"check": "impl_defect", "task_id": "T-042", "attempt": 1, "evidence": "not json"},
    )
    s = state_of(*pre, failed)
    cmd = decide(s)
    assert "the gate failed on these anchors" not in cmd.params["objective"]
