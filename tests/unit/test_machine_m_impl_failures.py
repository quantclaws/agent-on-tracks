from tests.unit import m_impl_machine_sequences as m_impl
from tests.unit.test_machine_m_impl_support import (
    _M_IMPL_CONTEXT_DOCS,
    _M_IMPL_CRITERIA_PACK,
    DEVON_GREEN_DISPATCH,
    DEVON_GREEN_DONE,
    DEVON_RED_DISPATCH,
    DEVON_REFACTOR_DISPATCH,
    DEVON_REFACTOR_DONE,
    ENTER_M_IMPL,
    GREEN_GATE_CMD,
    M_IMPL_REQUIRED_ASSIGNMENT_KEYS,
    TASK_REVIEW_CMD,
    _at_task_review,
    _devon_failed_outcome,
    _full_single_task_cycle,
    decide,
    project,
    seq,
    state_of,
)


def test_refactor_gate_public_interface_to_diagnose_stub_gap():
    """verdict.failed(public_interface) at REFACTOR_GATE -> DIAGNOSE/stub_gap."""
    fail = ("verdict.failed", {"check": "public_interface", "reason": "broke API", "attempt": 1})
    s = state_of(*m_impl.refactor_gate(), fail)
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


def test_green_gate_contract_error_parks_for_operator():
    """B49 (run 01M0S0FQ, 2026-08-24): a gate-contract mismatch must park the
    run for the operator (awaiting_human/escalation) -- NOT route into
    DIAGNOSE/stub_gap whose decide() emits rollback_stage(M-DESIGN). The
    auto-rollback loop sent runtime-side defects to be "fixed" in a design
    that was not defective."""
    fail = (
        "verdict.failed",
        {"check": "contract_error", "reason": "gate-contract mismatch", "attempt": 1},
    )
    s = state_of(*m_impl.green_gate(), fail)
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
    fail = (
        "verdict.failed",
        {"check": "contract_error", "reason": "gate-contract mismatch", "attempt": 1},
    )
    s = state_of(*m_impl.green_gate(), fail)
    assert s.current_attempt == 0, "parking must not consume the attempt budget"


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


def test_shield_fix_outcome_to_green_gate():
    """Shield SHIELD_FIX outcome done -> GREEN_GATE."""
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
    s = state_of(
        *m_impl.green_gate(),
        ("verdict.failed", {"check": "unknown_attribution", "reason": "unclear", "attempt": 1}),
        ("verdict.failed", {"check": "test_defect", "attempt": 1}),
        shield_dispatch,
        shield_done,
    )
    assert s.substate == "GREEN_GATE"


def test_island_gate_2_full_suite_fail_to_diagnose():
    """verdict.failed(full_suite) at ISLAND_GATE_2 -> DIAGNOSE."""
    base = _full_single_task_cycle()[:40]  # up to ISLAND2_CMD
    fail = ("verdict.failed", {"check": "full_suite", "reason": "e2e fail", "attempt": 1})
    s = state_of(*base, fail)
    assert s.substate == "DIAGNOSE"


def test_red_failure_escalation():
    """Three Devon RED failures escalate to awaiting_human."""
    fail = (
        "outcome.received",
        {"role": "devon", "status": "failed", "failure_class": "agent_failed"},
    )
    evs = []
    for _ in range(3):
        evs.append(DEVON_RED_DISPATCH)
        evs.append(fail)
    s = state_of(*m_impl.task_started(), *evs)
    assert s.current_attempt == 3
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert decide(s) is None


def test_failed_devon_red_routes_to_diagnose_with_evidence():
    s = state_of(
        *m_impl.task_started(),
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
        *m_impl.prism_red(),
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
        *m_impl.prism_red(),
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
        *m_impl.green_commit(),
        DEVON_REFACTOR_DISPATCH,
        _devon_failed_outcome(),
    )
    assert s.substate == "REFACTOR"
    assert s.current_attempt == 1
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    assert cmd.params["substate"] == "REFACTOR"


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


def test_archer_dispatch_assignment_has_required_keys():
    """Archer dispatch assignment carries all required keys (D-29)."""
    s = state_of(*m_impl.planning())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


def test_devon_dispatch_assignment_has_required_keys():
    """Devon dispatch assignment carries all required keys."""
    s = state_of(*m_impl.task_started())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


def test_prism_dispatch_assignment_has_required_keys():
    """Prism dispatch assignment carries all required keys + criteria_pack."""
    s = state_of(*m_impl.island1())
    cmd = decide(s)
    assignment = cmd.params["assignment"]
    assert assignment.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS
    assert assignment["criteria_pack"] == dict(_M_IMPL_CRITERIA_PACK)


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


def test_kernel_purity_no_io():
    """decide()/project() perform no I/O -- pure folds over events."""
    s = state_of(*_full_single_task_cycle())
    cmd = decide(s)
    assert cmd is None  # stage_exited -> halt


def test_red_gate_lint_fail_returns_to_red_without_consuming_attempt():
    """B4 (issue #5, user ruling): mechanical lint findings at RED_GATE route
    back to RED but never consume the attempt budget (attempts are reserved
    for semantic, agent-caused failures)."""
    fail = ("verdict.failed", {"check": "lint", "reason": "F401", "attempt": 1})
    s = state_of(*m_impl.red_gate(), fail)
    assert s.substate == "RED"
    assert s.current_attempt == 0


def test_green_gate_lint_fail_returns_to_green_without_consuming_attempt():
    """B4 (issue #5): GREEN lint findings route back to GREEN, no attempt."""
    fail = ("verdict.failed", {"check": "lint", "reason": "E501", "attempt": 1})
    s = state_of(*m_impl.green_gate(), fail)
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
        *m_impl.prism_red(),
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
    s = state_of(
        *m_impl.green_gate(),
        ("verdict.failed", {"check": "impl_defect", "attempt": 1}),
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
        *m_impl.green_gate(),
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
        *m_impl.green_commit(),
        DEVON_REFACTOR_DISPATCH,
        DEVON_REFACTOR_DONE,
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
        *m_impl.green_gate(),
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
