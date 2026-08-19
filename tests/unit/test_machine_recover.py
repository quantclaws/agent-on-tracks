"""B32 (#32): forward-recovery reducer chain (human.recover -> stage.recovered).

Covers the pure kernel half of the recovery channel:
- human.recover -> substate=RECOVER_PENDING + recover_target/reason on State.
- decide(RECOVER_PENDING) -> recover_stage command (never rollback_stage).
- stage.recovered reuses _on_stage_entered -> M-IMPL/BASELINE with all
  per-stage fields (baseline_frozen/taskgraph_committed/current_task_id) reset.
- replay is deterministic; recovery preserves a pre-set last_failure sentinel.
"""

from tests.unit.helpers import (
    ARCHER_DISPATCH,
    ARCHER_DONE,
    BASELINE_CMD,
    BASELINE_FROZEN,
    ENTER_M_IMPL,
    ISLAND1_CMD,
    ISLAND1_PASS,
    PRISM_PLAN_DISPATCH,
    PRISM_PLAN_DONE,
    TASKGRAPH_CMD,
    TASKGRAPH_COMMITTED,
    ev,
    seq,
)
from tracks.kernel import decide, project
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK, State

# Prism at PRISM_PLAN mis-typifies a task-graph defect as stub_gap -> the
# stub_gap rollback to M-DESIGN that strands the run (the bug being fixed).
REVISE_STUB_GAP = (
    "prism.verdict",
    {
        "verdict": "revise",
        "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
        "defect_classification": "stub_gap",
    },
)

ROLLED_BACK = ("stage.rolled_back", {"from_stage": "M-IMPL", "to_stage": "M-DESIGN", "reason": "stub_gap"})

HUMAN_RECOVER = (
    "human.recover",
    {"reason": "mis-typed stub_gap; task graph must re-decompose", "to_stage": "M-IMPL"},
)

# Full M-IMPL stub_gap chain (mirrors test_machine_m_impl.py ::test_prism_plan_design_gap_to_diagnose_stub_gap)
_STUB_GAP_CHAIN = [
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
    REVISE_STUB_GAP,
]


def test_human_recover_sets_recover_pending():
    """human.recover -> RECOVER_PENDING + recover_target/reason, status active
    awaiting None, and (critically) does NOT clear a pre-set last_failure."""
    from tracks.kernel.machine import _on_human_recover

    sentinel = {"check": "SENTINEL", "reason": "preserved"}
    s = State()
    s.stage = "M-DESIGN"
    s.substate = "DRAFT"
    s.last_failure = sentinel
    envelope = ev(1, "human.recover", {"reason": "r", "to_stage": "M-IMPL"})
    _on_human_recover(s, envelope.payload, envelope)

    assert s.substate == "RECOVER_PENDING"
    assert s.recover_target == "M-IMPL"
    assert s.recover_reason == "r"
    assert s.status == "active"
    assert s.awaiting is None
    assert s.last_failure == sentinel


def test_decide_recover_pending_issues_recover_stage():
    """decide(RECOVER_PENDING) -> recover_stage(M-IMPL), never rollback."""
    s = project(seq(*ENTER_M_IMPL, *_STUB_GAP_CHAIN, ROLLED_BACK, HUMAN_RECOVER))
    assert s.stage == "M-DESIGN"
    assert s.substate == "RECOVER_PENDING"
    assert s.recover_target == "M-IMPL"

    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "recover_stage"
    assert cmd.params["to_stage"] == "M-IMPL"
    assert cmd.params["reason"].startswith("mis-typed")


def test_stage_recovered_reenters_m_impl_fresh():
    """stage.recovered -> M-IMPL/BASELINE with per-stage fields reset."""
    s = project(
        seq(*ENTER_M_IMPL, *_STUB_GAP_CHAIN, ROLLED_BACK, HUMAN_RECOVER)
    )
    # Pre-recovery fields must be set (non-default) so the reset is observable.
    assert s.baseline_frozen is True
    assert s.taskgraph_committed is True

    s2 = project(
        seq(
            *ENTER_M_IMPL,
            *_STUB_GAP_CHAIN,
            ROLLED_BACK,
            HUMAN_RECOVER,
            ("stage.recovered", {"stage": "M-IMPL", "from_stage": "M-DESIGN", "reason": "x"}),
        )
    )
    assert s2.stage == "M-IMPL"
    assert s2.substate == "BASELINE"
    assert s2.status == "active"
    assert s2.baseline_frozen is False
    assert s2.taskgraph_committed is False
    assert s2.current_task_id is None
    assert decide(s2) is not None  # machine continues (freeze_baseline)


def test_recovery_replay_is_deterministic():
    """Projecting the same recovery event log twice gives identical state."""
    items = (
        *ENTER_M_IMPL,
        *_STUB_GAP_CHAIN,
        ROLLED_BACK,
        HUMAN_RECOVER,
        ("stage.recovered", {"stage": "M-IMPL", "from_stage": "M-DESIGN", "reason": "x"}),
    )
    s1 = project(seq(*items))
    s2 = project(seq(*items))
    assert s1.stage == s2.stage == "M-IMPL"
    assert s1.substate == s2.substate == "BASELINE"
    assert s1.taskgraph_committed == s2.taskgraph_committed is False
    assert decide(s1) == decide(s2)
