from tests.unit.test_machine_m_impl_support import (
    _M_IMPL_CRITERIA_PACK,
    _NEXT_STAGE,
    ARCHER_DISPATCH,
    ARCHER_DONE,
    BASELINE_CMD,
    BASELINE_FROZEN,
    DEVON_GREEN_DISPATCH,
    DEVON_GREEN_DONE,
    DEVON_RED_DISPATCH,
    DEVON_RED_DONE,
    DEVON_REFACTOR_DISPATCH,
    DEVON_REFACTOR_DONE,
    GREEN_COMMIT_CMD,
    GREEN_COMMITTED,
    GREEN_GATE_CMD,
    GREEN_PASS,
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
    REFACTOR_COMMITTED,
    REFACTOR_GATE_CMD,
    SELECT_TASK_CMD,
    TASK_STARTED,
    TASKGRAPH_CMD,
    TASKGRAPH_COMMITTED,
    decide,
    state_of,
)


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

