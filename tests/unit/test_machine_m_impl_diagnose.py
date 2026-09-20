from tests.unit import m_impl_machine_sequences as m_impl
from tests.unit.test_machine_m_impl_support import (
    _M_IMPL_CRITERIA_PACK,
    COMPLETE_TASK_CMD,
    DEVON_GREEN_DISPATCH,
    DEVON_GREEN_DONE,
    DEVON_RED_DISPATCH,
    DEVON_REFACTOR_DISPATCH,
    DEVON_REFACTOR_DONE,
    GREEN_COMMIT_CMD,
    GREEN_COMMITTED,
    GREEN_GATE_CMD,
    GREEN_PASS,
    LINEAGE_FAIL,
    PRISM_FINAL_DISPATCH,
    PRISM_FINAL_DONE,
    PRISM_FINAL_PASS,
    REFACTOR_COMMITTED,
    REFACTOR_GATE_CMD,
    SELECT_TASK_CMD,
    TASK_COMPLETED,
    TASK_REVIEW_CMD,
    TASK_REVIEW_PASS,
    _at_task_review,
    _devon_failed_outcome,
    _diagnose_events_with_report,
    _diagnose_state,
    _full_single_task_cycle,
    decide,
    state_of,
)


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
    s = state_of(*_at_task_review(), TASK_REVIEW_CMD, TASK_REVIEW_PASS)
    assert s.substate == "PRISM_FINAL"


def test_prism_final_pass_to_task_done():
    """prism.verdict(pass) at PRISM_FINAL -> TASK_DONE."""
    s = state_of(
        *_at_task_review(),
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
        *_at_task_review(),
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
        *_at_task_review(),
        TASK_REVIEW_CMD,
        TASK_REVIEW_PASS,
        PRISM_FINAL_DISPATCH,
        PRISM_FINAL_DONE,
        revise,
    )
    assert s.substate == "RED"
    assert s.green_committed is False
    assert s.refactor_done is False


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


def test_multi_task_iterates_to_dispatch():
    """task.completed with remaining tasks -> TASK_DISPATCH."""
    s = state_of(*m_impl.task_done(("taskgraph.committed", {"task_count": 2})))
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
        *m_impl.green_gate(("taskgraph.committed", {"task_count": 2})),
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
        *m_impl.task_started(),
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


def test_shield_dispatch_carries_diagnose_report():
    """SHIELD_FIX objective carries the DIAGNOSE reason/evidence from State.

    Regression (run 01KZTHE7 T-008/T-017): Shield used to receive only the
    'fix diagnosed test defects' label and burned 50+ minutes re-deriving
    Prism's analysis.
    """
    s = state_of(*m_impl.green_gate(), *_diagnose_events_with_report())
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
    s = state_of(
        *m_impl.green_gate(),
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
    infra_failed = ("outcome.received", {
        "role": "shield",
        "status": "failed",
        "failure_class": "signal",
        "self_report": "killed by signal 9",
    })
    s = state_of(*m_impl.green_gate(), *_diagnose_events_with_report(), infra_failed)
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
    """Consecutive infra failures escalate to awaiting_human at the limit
    without ever touching the agent attempt budget; below the limit the run
    keeps waiting (Human decision 2026-09-19: high load -> wait); human.retry
    clears the streak. The limit is env-tunable (TRAC_INFRA_RETRY_LIMIT);
    the default spans ~106 minutes of capped backoff, so the historical
    3-streak pin would freeze the waiting semantics back to ~3.5 minutes."""
    from tracks.kernel.machine_outcomes import _infra_retry_limit
    infra_failed = ("outcome.received", {
        "role": "shield",
        "status": "failed",
        "failure_class": "provider_unavailable",
        "self_report": "provider/model/credentials unavailable",
    })
    base = [*m_impl.green_gate(), *_diagnose_events_with_report()]
    limit = _infra_retry_limit()
    events = [*base]
    for _ in range(limit - 1):
        events = [*events, infra_failed]
    s = state_of(*events)
    assert s.status == "active", "below the limit the run keeps waiting out the gateway"
    events = [*events, infra_failed]
    s = state_of(*events)
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert s.current_attempt == 1  # still untouched by infra failures
    assert s.infra_failure_streak == limit
    s2 = state_of(*events, ("human.retry", {"actor": "openclaw"}))
    assert s2.status == "active"
    assert s2.infra_failure_streak == 0


def test_infra_retry_limit_env_override(monkeypatch):
    """TRAC_INFRA_RETRY_LIMIT tunes the escalation threshold (per-call read:
    a restarted process picks the current environment); garbage falls back
    to the default."""
    from tracks.kernel import machine_outcomes

    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "2")
    assert machine_outcomes._infra_retry_limit() == 2
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "0")
    assert machine_outcomes._infra_retry_limit() == 1  # floor at 1
    monkeypatch.setenv("TRAC_INFRA_RETRY_LIMIT", "not-an-int")
    assert machine_outcomes._infra_retry_limit() == machine_outcomes._INFRA_RETRY_LIMIT_DEFAULT


def test_non_zero_exit_is_infra_not_semantic():
    """OOB 2026-09-05: opencode exiting 1 (CLI crash, provider 400) is
    machine-side infra -- no attempt consumed, no last_failure overwrite,
    streak increments toward the bounded infra re-dispatch."""
    crash = ("outcome.received", {
        "role": "devon",
        "status": "failed",
        "failure_class": "non_zero_exit",
        "self_report": "opencode exited 1",
    })
    verdict = ("verdict.failed", {"check": "impl_defect", "attempt": 1,
                                  "reason": "selected task tests did not all pass",
                                  "evidence": "seed"})
    s = state_of(*m_impl.green_gate(), verdict, DEVON_GREEN_DISPATCH, crash)
    assert s.infra_failure_streak == 1
    assert s.current_attempt == 0  # untouched: infra never burns the budget
    assert s.last_failure["check"] == "impl_defect"  # evidence not overwritten


def test_shield_diagnose_report_survives_semantic_failure():
    """A semantic Shield failure (FR-0210) overwrites last_failure with the
    attempt's own failure signal but must not touch diagnose_report."""
    semantic_failed = ("outcome.received", {
        "role": "shield",
        "status": "failed",
        "failure_class": "manifest_malformed",
        "self_report": "manifest malformed: final part.text must be raw JSON",
    })
    s = state_of(*m_impl.green_gate(), *_diagnose_events_with_report(), semantic_failed)
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
        *m_impl.green_gate(),
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
    s = state_of(*m_impl.prism_plan_done(), revise)
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"
