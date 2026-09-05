"""M-TEST reducer/decide branches (flow.md §9 / SM-01, FR-0010~0070) - pure.

Covers the explicit `_decide_m_test` control flow, SM-01 transition enforcement,
the shared <=3 attempt budget, DIAGNOSE four-way routing, and the kernel purity
boundary (NFR-0030).
"""

from tests.unit.helpers import seq
from tracks.executor.executor import _NEXT_STAGE
from tracks.kernel import decide, project
from tracks.kernel.machine import _CRITERIA_PACK, _M_TEST_CONTEXT_DOCS

ENTER_M_TEST = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-TEST"}),
]

# D-41 v3 timing (flow.md §9.1): the pre-WRITE R1 snapshot capture is the first
# M-TEST command and its passed event gates the first Shield WRITE dispatch.
CAPTURE_CMD = (
    "command.issued",
    {"command": {"kind": "capture_baseline", "params": {"stage": "M-TEST"}, "command_id": "C0"}},
)
BASELINE_CAPTURED = (
    "test.baseline_captured",
    {
        "status": "passed",
        "baseline_id": "b0",
        "baseline_tree": "t0",
        "layers": ["unit", "integration", "e2e"],
        "nodes_count": 0,
        "empty_baseline": True,
        "node_digest_blob": None,
        "errors": [],
    },
)

SHIELD_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "shield", "substate": "WRITE"},
            "command_id": "C1",
        }
    },
)
SHIELD_DONE = ("outcome.received", {"role": "shield", "status": "done"})
COLLECT_CMD = (
    "command.issued",
    {"command": {"kind": "collect_tests", "params": {"stage": "M-TEST"}, "command_id": "C2"}},
)
COLLECTED = ("test.collected", {"status": "passed", "collected_count": 1, "errors": []})
PRISM_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "prism", "substate": "PRISM_REVIEW"},
            "command_id": "C3",
        }
    },
)
PRISM_DONE = ("outcome.received", {"role": "prism", "status": "done"})
PRISM_PASS = ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_CRITERIA_PACK)})
RUN_CMD = (
    "command.issued",
    {"command": {"kind": "run_tests", "params": {"stage": "M-TEST"}, "command_id": "C4"}},
)
RED_VALID = ("red.validated", {"status": "valid", "findings": []})
TRACE_CMD = (
    "command.issued",
    {"command": {"kind": "check_trace", "params": {"stage": "M-TEST"}, "command_id": "C5"}},
)
TRACE_PASS = ("verdict.passed", {"check": "trace", "detail": "closure verified"})
COMMIT_CMD = (
    "command.issued",
    {"command": {"kind": "commit_tests", "params": {"stage": "M-TEST"}, "command_id": "C6"}},
)
TEST_COMMITTED = ("test.committed", {"commit_sha": "abc", "test_count": 1})


def state_of(*items):
    return project(seq(*ENTER_M_TEST, *items))


def _full_cycle():
    """CAPTURE -> DISPATCH -> WRITE -> COLLECT -> RED_CHECK -> PRISM_REVIEW ->
    EXIT (D-41 v3 timing)."""
    return [
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        PRISM_PASS,
        TRACE_CMD,
        TRACE_PASS,
        COMMIT_CMD,
        TEST_COMMITTED,
        ("stage.exited", {"stage": "M-TEST"}),
        ("run.completed", {"terminal_state": "boundary"}),
    ]


# -- AC-FR0010-01@v0.4: M-DESIGN EXIT -> stage.entered(M-TEST), substate=DISPATCH ----


# AC-FR0010-01@v0.4 TRACKS-TRACE enter M-TEST from design exit
def test_enter_m_test_from_design_exit():
    """AC-FR0010-01@v0.4"""
    s = state_of()
    assert s.stage == "M-TEST"
    assert s.substate == "DISPATCH"
    assert s.current_attempt == 0


# -- AC-FR0010-02@v0.4: _NEXT_STAGE + boundary ---------------------------------------


# AC-FR0010-02@v0.5 TRACKS-TRACE next stage design to M-TEST to M-IMPL
def test_next_stage_design_to_m_test():
    """AC-FR0010-02@v0.5"""
    assert _NEXT_STAGE["M-DESIGN"] == "M-TEST"
    assert _NEXT_STAGE["M-TEST"] == "M-IMPL"  # v0.5: M-TEST -> M-IMPL


# -- AC-FR0010-03@v0.4: SM-01 transition enforcement ---------------------------------


# AC-FR0010-03@v0.4 TRACKS-TRACE SM-01 transitions enforced
def test_sm01_transitions_enforced():
    """AC-FR0010-03@v0.4: CAPTURE -> DISPATCH -> WRITE -> COLLECT ->
    RED_CHECK -> PRISM_REVIEW -> EXIT follows SM-01 (D-41 v3 timing)."""
    s = state_of()
    assert s.substate == "DISPATCH"
    s1 = project(seq(*ENTER_M_TEST, CAPTURE_CMD, BASELINE_CAPTURED))
    assert s1.substate == "DISPATCH"
    assert s1.baseline_captured is True
    s2 = project(seq(*ENTER_M_TEST, CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH))
    assert s2.substate == "WRITE"
    s3 = project(seq(*ENTER_M_TEST, CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE))
    assert s3.substate == "COLLECT"
    s4 = project(
        seq(
            *ENTER_M_TEST,
            CAPTURE_CMD,
            BASELINE_CAPTURED,
            SHIELD_DISPATCH,
            SHIELD_DONE,
            COLLECT_CMD,
            COLLECTED,
        )
    )
    assert s4.substate == "RED_CHECK"
    s5 = project(seq(*ENTER_M_TEST, *_full_cycle()[:12]))
    assert s5.substate == "EXIT"


# -- AC-FR0010-05@v0.4: explicit control flow + kernel purity ------------------------


# AC-FR0010-05@v0.4 TRACKS-TRACE explicit control flow
def test_explicit_control_flow():
    """AC-FR0010-05@v0.4: decide() produces M-TEST-specific commands that the
    DRAFT/REVIEW/EXIT pattern cannot yield."""
    s = state_of()
    cmd = decide(s)
    # D-41: the FIRST M-TEST command captures the pre-WRITE R1 snapshot.
    assert cmd.kind == "capture_baseline"
    s0 = state_of(CAPTURE_CMD, BASELINE_CAPTURED)
    cmd0 = decide(s0)
    assert cmd0.kind == "dispatch_agent"
    assert cmd0.params["role"] == "shield"
    assert cmd0.params["substate"] == "WRITE"
    # COLLECT: decide produces collect_tests (not dispatch_agent)
    s2 = project(seq(*ENTER_M_TEST, CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE))
    assert decide(s2).kind == "collect_tests"
    # RED_CHECK: decide produces run_tests (D-41 v3: before PRISM_REVIEW)
    s3 = project(
        seq(
            *ENTER_M_TEST,
            CAPTURE_CMD,
            BASELINE_CAPTURED,
            SHIELD_DISPATCH,
            SHIELD_DONE,
            COLLECT_CMD,
            COLLECTED,
        )
    )
    assert decide(s3).kind == "run_tests"


# AC-NFR0030-01@v0.4 TRACKS-TRACE kernel purity no IO
def test_kernel_purity_no_io():
    """AC-NFR0030-01@v0.4: decide()/project() perform no I/O -- they are pure
    folds over events. Verified by construction: no filesystem/clock/env access
    in the decide/project path; all non-determinism enters as events."""
    s = state_of(*_full_cycle())
    cmd = decide(s)
    assert cmd is None  # test_committed -> executor emits stage.exited + run.completed


# -- AC-FR0010-05@v0.4 / SM-01.1: stage.entered resets M-TEST fields -----------------


# AC-FR0010-05@v0.4 TRACKS-TRACE enter M-TEST resets fields
def test_enter_m_test_resets_fields():
    """AC-FR0010-05@v0.4: entering M-TEST resets all per-cycle fields."""
    s = state_of(CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED)
    assert s.test_collected is True
    # Re-enter M-TEST (e.g. after stub_gap rollback -> M-DESIGN -> M-TEST again)
    s2 = project(
        seq(
            *ENTER_M_TEST,
            CAPTURE_CMD,
            BASELINE_CAPTURED,
            SHIELD_DISPATCH,
            SHIELD_DONE,
            COLLECT_CMD,
            COLLECTED,
            ("stage.entered", {"stage": "M-TEST"}),
        )
    )
    assert s2.test_collected is False
    assert s2.current_attempt == 0
    assert s2.substate == "DISPATCH"
    assert s2.baseline_captured is False  # fresh cycle re-captures the snapshot


# -- AC-FR0020-01@v0.4: DISPATCH creates Shield tasks -> WRITE -----------------------


# AC-FR0020-01@v0.4 TRACKS-TRACE dispatch creates Shield tasks
def test_dispatch_creates_shield_tasks():
    """AC-FR0020-01@v0.4"""
    s = state_of(CAPTURE_CMD, BASELINE_CAPTURED)
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "shield"
    assert cmd.params["substate"] == "WRITE"
    assert cmd.params["assignment"]["skills"] == ["tracks-discuz", "tracks-shield"]


# -- AC-FR0020-04@v0.4: WRITE validate failure <=3 -> escalation ---------------------


# AC-FR0020-04@v0.4 TRACKS-TRACE write retry escalation
def test_write_retry_escalation():
    """AC-FR0020-04@v0.4: three Shield failures escalate to awaiting_human."""
    fail = (
        "outcome.received",
        {"role": "shield", "status": "failed", "failure_class": "agent_failed"},
    )
    evs = [SHIELD_DISPATCH, fail] * 3
    s = state_of(*evs)
    assert s.current_attempt == 3
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert decide(s) is None  # halted


# -- AC-FR0030-02@v0.4: collection pass/fail routing --------------------------------


# AC-FR0030-02@v0.4 TRACKS-TRACE collect passed to red check
def test_collect_passed_to_red_check():
    """AC-FR0030-02@v0.4 (D-41 v3): collection success -> RED_CHECK."""
    s = state_of(CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED)
    assert s.substate == "RED_CHECK"
    assert s.test_collected is True


# AC-FR0030-02@v0.4 TRACKS-TRACE collect failed to write
def test_collect_failed_to_write():
    """AC-FR0030-02@v0.4: collection failure -> WRITE re-dispatch."""
    failed = (
        "test.collected",
        {"status": "failed", "collected_count": 0, "errors": ["import error"]},
    )
    s = state_of(
        CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, failed
    )
    assert s.substate == "WRITE"
    assert s.test_collected is False
    assert s.current_attempt == 1


# -- AC-FR0040-01@v0.4/02/03: PRISM_REVIEW + criteria pack --------------------------


# AC-FR0040-01@v0.4 TRACKS-TRACE prism dispatch
def test_prism_dispatch():
    """AC-FR0040-01@v0.4: PRISM_REVIEW dispatches Prism with criteria pack
    (D-41 v3: entered from a valid RED_CHECK)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
    )
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["assignment"]["skills"] == ["tracks-discuz", "tracks-prism-test"]
    assert cmd.params["assignment"]["criteria_pack"] == dict(_CRITERIA_PACK)


def test_prism_dispatch_failure_re_dispatches():
    """v0.5 regression (run 01KZTHE7RMZE6110PK9C54K1E2): a failed prism
    dispatch (non_zero_exit) must reset reviewer_dispatched so decide()
    re-dispatches Prism with failure evidence instead of halting forever
    after human.retry clears the escalation gate."""
    failed = (
        "outcome.received",
        {
            "role": "prism",
            "status": "failed",
            "failure_class": "non_zero_exit",
            "self_report": "opencode exited 1",
        },
    )
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        failed,
    )
    assert s.substate == "PRISM_REVIEW"
    assert s.reviewer_dispatched is False
    cmd = decide(s)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    # 4ca0207: non_zero_exit is infra -- the re-dispatch still happens
    # (reviewer flag reset) but machine noise never rides the evidence
    # channel (infra failures do not overwrite last_failure).
    assert "evidence" not in cmd.params or cmd.params["evidence"] is None


# AC-FR0040-02@v0.4 TRACKS-TRACE criteria pack mismatch
def test_criteria_pack_mismatch():
    """AC-FR0040-02@v0.4: verdict.failed(criteria_pack_mismatch) re-dispatches."""
    mismatch = (
        "verdict.failed",
        {"check": "criteria_pack_mismatch", "reason": "pack mismatch", "attempt": 1},
    )
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        mismatch,
    )
    assert s.current_attempt == 1
    assert s.substate == "PRISM_REVIEW"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"


# B59 (#75) TRACKS-TRACE freeze contamination parks for the operator
def test_freeze_contamination_parks_for_human():
    """B59 (#75): verdict.failed(test_freeze_contamination) at COMMIT 是废弃
    周期的主树残留 —— 非 Shield 缺陷、重派不可修：停车 awaiting_human/
    escalation，由操作者清理主树后 `trac retry` 重入 freeze。"""
    contam = (
        "verdict.failed",
        {
            "check": "test_freeze_contamination",
            "reason": "tracked files under tests/ are modified at freeze time",
            "evidence": "tests/unit/test_red.py",
            "attempt": 1,
        },
    )
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        PRISM_PASS,
        TRACE_CMD,
        TRACE_PASS,
        COMMIT_CMD,
        contam,
    )
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert s.test_committed is False
    assert decide(s) is None  # halted for the operator


# AC-FR0040-03@v0.4 TRACKS-TRACE prism pass to exit
def test_prism_pass_to_exit():
    """AC-FR0040-03@v0.4 (D-41 v3): RED_CHECK already ran, pass -> EXIT."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        PRISM_PASS,
    )
    assert s.substate == "EXIT"


# AC-FR0040-03@v0.4 TRACKS-TRACE prism revise to write
def test_prism_revise_to_write():
    """AC-FR0040-03@v0.4"""
    revise = ("prism.verdict", {"verdict": "revise", "criteria_pack": dict(_CRITERIA_PACK)})
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        revise,
    )
    assert s.substate == "WRITE"
    assert s.current_attempt == 1


# -- AC-FR0050-05@v0.4: RED_CHECK routing -------------------------------------------


# AC-FR0050-05@v0.4 TRACKS-TRACE red valid to prism review
def test_red_valid_to_prism_review():
    """AC-FR0050-05@v0.4 (D-41 v3): valid red -> PRISM_REVIEW (Prism consumes
    the current-tree red evidence before EXIT)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
    )
    assert s.substate == "PRISM_REVIEW"
    assert s.red_validated is True


# AC-FR0050-05@v0.4 TRACKS-TRACE red invalid to diagnose
def test_red_invalid_to_diagnose():
    """AC-FR0050-05@v0.4"""
    invalid = (
        "red.validated",
        {"status": "invalid", "findings": [{"test_id": "*", "classification": "unexpected_pass"}]},
    )
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        invalid,
    )
    assert s.substate == "DIAGNOSE"
    assert s.red_validated is False


# -- AC-FR0060-01@v0.4..05: DIAGNOSE four-way routing --------------------------------


def _diagnose_state(classification):
    invalid = ("red.validated", {"status": "invalid", "findings": []})
    verdict = (
        "verdict.failed",
        {
            "check": classification,
            "target_stage": {
                "test_defect": "M-TEST",
                "stub_gap": "M-DESIGN",
                "ac_gap": "M-ACC",
                "spec_gap": "M-SPEC",
            }[classification],
            "artifact_disposition": "rewrite",
            "attempt": 1,
        },
    )
    return state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        invalid,
        verdict,
    )


# AC-FR0060-01@v0.4 TRACKS-TRACE diagnose entered
def test_diagnose_entered():
    """AC-FR0060-01@v0.4"""
    s = _diagnose_state("test_defect")
    assert s.substate == "WRITE"  # test_defect -> WRITE
    s2 = _diagnose_state("stub_gap")
    assert s2.diagnose_classification == "stub_gap"


# AC-FR0060-02@v0.5 test_defect re-dispatch carries the DIAGNOSE report
def test_test_defect_write_dispatch_carries_diagnose_report():
    """The WRITE re-dispatch objective carries the DIAGNOSE reason/evidence
    from State.diagnose_report (not last_failure), so provider-failure
    retries and `trac retry --clear-evidence` never strip the diagnosis
    (mirrors the M-IMPL SHIELD_FIX fix, run 01KZTHE7 T-008/T-017)."""
    invalid = ("red.validated", {"status": "invalid", "findings": []})
    verdict = (
        "verdict.failed",
        {
            "check": "test_defect",
            "target_stage": "M-TEST",
            "reason": "RED asserts stale contract",
            "evidence": "tests/e2e/test_trace.py:40-44",
            "attempt": 1,
        },
    )
    base = [
        CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE,
        COLLECT_CMD, COLLECTED, RUN_CMD, invalid, verdict,
    ]
    s = state_of(*base)
    assert s.diagnose_report["check"] == "test_defect"
    cmd = decide(s)
    assert cmd.params["role"] == "shield"
    assert "RED asserts stale contract" in cmd.params["objective"]
    assert "tests/e2e/test_trace.py:40-44" in cmd.params["objective"]

    # clear-evidence retry must not strip the diagnosis from the re-dispatch
    s2 = state_of(*base, ("human.retry", {"actor": "openclaw", "clear_evidence": True}))
    assert s2.last_failure is None
    cmd2 = decide(s2)
    assert "RED asserts stale contract" in cmd2.params["objective"]


# AC-FR0060-02@v0.4 TRACKS-TRACE diagnose test defect to write
def test_diagnose_test_defect_to_write():
    """AC-FR0060-02@v0.4"""
    s = _diagnose_state("test_defect")
    assert s.substate == "WRITE"
    assert s.current_attempt == 1
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "shield"


# AC-FR0060-03@v0.4 TRACKS-TRACE diagnose stub gap to design
def test_diagnose_stub_gap_to_design():
    """AC-FR0060-03@v0.4: stub_gap -> rollback M-DESIGN, no Human."""
    s = _diagnose_state("stub_gap")
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"
    assert s.status == "active"  # no awaiting_human


# AC-FR0060-04@v0.4 TRACKS-TRACE diagnose ac gap to acc
def test_diagnose_ac_gap_to_acc():
    """AC-FR0060-04@v0.4: ac_gap -> awaiting Human, then rollback M-ACC."""
    s = _diagnose_state("ac_gap")
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target == "M-ACC"
    assert decide(s) is None  # halted pending human.approval
    # Human approves -> RETURNED -> rollback
    s2 = project(
        seq(
            *ENTER_M_TEST,
            CAPTURE_CMD,
            BASELINE_CAPTURED,
            SHIELD_DISPATCH,
            SHIELD_DONE,
            COLLECT_CMD,
            COLLECTED,
            RUN_CMD,
            ("red.validated", {"status": "invalid", "findings": []}),
            (
                "verdict.failed",
                {
                    "check": "ac_gap",
                    "target_stage": "M-ACC",
                    "artifact_disposition": "rollback",
                    "attempt": 1,
                },
            ),
            ("human.approval", {"digest": "d", "actor": "H"}),
        )
    )
    assert s2.substate == "RETURNED"
    cmd = decide(s2)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-ACC"


# AC-FR0060-05@v0.4 TRACKS-TRACE diagnose spec gap to spec
def test_diagnose_spec_gap_to_spec():
    """AC-FR0060-05@v0.4: spec_gap -> awaiting Human, then rollback M-SPEC."""
    s = _diagnose_state("spec_gap")
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target == "M-SPEC"


# -- AC-FR0060-07@v0.4: no M-IMPL events --------------------------------------------


# AC-FR0060-07@v0.4 TRACKS-TRACE no M-IMPL events
def test_no_m_impl_events():
    """AC-FR0060-07@v0.4: M-TEST produces no M-IMPL events (SHIELD_FIX etc.)."""
    s = state_of(*_full_cycle())
    # No M-IMPL stage entry; test.committed is the terminal M-TEST event
    assert s.test_committed is True
    assert s.stage == "M-TEST"


# -- AC-FR0070-05@v0.4: trace fail -> WRITE redispatch ------------------------------


# AC-FR0070-05@v0.4 TRACKS-TRACE trace fail to write
def test_trace_fail_to_write():
    """AC-FR0070-05@v0.4: trace gate failure -> EXIT->WRITE, consumes budget."""
    trace_fail = (
        "verdict.failed",
        {"check": "trace", "reason": "orphan", "attempt": 1, "evidence": "trac check trace"},
    )
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        PRISM_PASS,
        TRACE_CMD,
        trace_fail,
    )
    assert s.substate == "WRITE"
    assert s.trace_passed is False
    assert s.current_attempt == 1


# -- AC-NFR0030-02@v0.4: rebuild from events ----------------------------------------


# AC-NFR0030-02@v0.4 TRACKS-TRACE rebuild from events
def test_rebuild_from_events():
    """AC-NFR0030-02@v0.4: projecting the same events yields identical state."""
    evs = seq(*ENTER_M_TEST, *_full_cycle())
    s1 = project(evs)
    s2 = project(evs)
    assert s1.stage == s2.stage == "M-TEST"
    assert s1.substate == s2.substate
    assert s1.test_committed == s2.test_committed
    assert s1.test_collected == s2.test_collected
    assert s1.red_validated == s2.red_validated


# -- SM-01 transition coverage (§9 checklist) ----------------------------------


def test_dispatch_to_write():
    """SM-01.2@v0.4"""
    s = state_of(SHIELD_DISPATCH)
    assert s.substate == "WRITE"


def test_stub_gap_failed_outcome_routes_to_diagnose_not_retry():
    """D-32 / SM-01.12: an invalid M-DESIGN test-task contract (stub_gap
    failed outcome, emitted instead of dispatching) routes straight to
    DIAGNOSE/stub_gap: decide() emits rollback_stage(M-DESIGN), no attempt
    consumed, no Human, no three wasted Shield attempts."""
    stub = (
        "outcome.received",
        {
            "role": "shield",
            "status": "failed",
            "failure_class": "stub_gap",
            "self_report": "M-DESIGN test-task contract invalid",
            "audit_evidence": "empty test_tasks",
        },
    )
    s = state_of(SHIELD_DISPATCH, stub)
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    assert s.current_attempt == 0  # no attempt consumed -> no escalation
    assert s.status == "active" and s.awaiting is None
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


def test_stub_gap_rollback_to_design_clears_stale_evidence():
    """SM-01.12 evidence scoping: ONLY the stub_gap rollback (M-TEST ->
    M-DESIGN) clears last_failure, because the M-TEST stub_gap/failed-outcome
    evidence is stale relative to the M-DESIGN Archer re-dispatch that follows
    (the contract gap is in the design, not the test run). Other rollbacks
    (scope_overflow, ac_gap, spec_gap, human_return) PRESERVE last_failure so
    the downstream drafter still receives the original failure evidence
    (FR-11) -- see test_scope_overflow_rollback_preserves_evidence.

    Here the stub_gap route clears, and the M-DESIGN re-entry state semantics
    (DRAFT, counters reset) are preserved."""
    stub = (
        "outcome.received",
        {
            "role": "shield",
            "status": "failed",
            "failure_class": "stub_gap",
            "self_report": "M-DESIGN test-task contract invalid",
            "audit_evidence": "empty test_tasks",
        },
    )
    # M-TEST stub_gap captured failure evidence, then decide() rolls back.
    pre = state_of(SHIELD_DISPATCH, stub)
    assert pre.last_failure is not None  # stub_gap evidence captured
    assert pre.diagnose_classification == "stub_gap"
    # Executor processes rollback_stage(M-DESIGN) -> stage.rolled_back event.
    s = state_of(
        SHIELD_DISPATCH,
        stub,
        (
            "stage.rolled_back",
            {"from_stage": "M-TEST", "to_stage": "M-DESIGN", "reason": "stub_gap"},
        ),
    )
    assert s.stage == "M-DESIGN"
    assert s.current_attempt == 0
    assert s.last_failure is None
    # Existing rollback state semantics preserved (SM-01.12 / M-DESIGN re-entry)
    assert s.substate == "DRAFT"
    assert s.design_validated == 0
    assert s.design_committed == 0
    assert s.prism_passed_this_round is False


ENTER_M_SPEC = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-SPEC"}),
]


def test_scope_overflow_rollback_preserves_evidence():
    """FR-20 / AC-20a: a scope_overflow rollback (M-SPEC -> M-STORY) must
    PRESERVE last_failure so the Scribe re-dispatch carries the overflow
    evidence into the next DRAFT. This is the regression flipped from the
    stub_gap clearing rule: scope_overflow evidence is NOT stale -- it is the
    reason for the rollback and Scribe needs it to re-scope the story."""
    overflow = (
        "verdict.failed",
        {"check": "scope_overflow", "reason": "31 FRs in spec", "attempt": 0},
    )
    pre = project(seq(*ENTER_M_SPEC, overflow))
    assert pre.last_failure is not None
    assert pre.last_failure["check"] == "scope_overflow"
    assert pre.scope_overflow is True
    # Executor processes rollback_stage(M-STORY) -> stage.rolled_back event,
    # carrying from_stage/to_stage/reason in the payload (not implicit state).
    s = project(
        seq(
            *ENTER_M_SPEC,
            overflow,
            (
                "stage.rolled_back",
                {"from_stage": "M-SPEC", "to_stage": "M-STORY", "reason": "scope_overflow"},
            ),
        )
    )
    assert s.stage == "M-STORY"
    assert s.substate == "DRAFT"
    assert s.scope_overflow is False  # rollback consumed the flag
    assert s.story_committed is False  # the redone story must be re-committed
    # Evidence preserved for the Scribe re-dispatch (FR-11).
    assert s.last_failure is not None
    assert s.last_failure["check"] == "scope_overflow"
    # decide() re-dispatches Scribe DRAFT carrying the overflow evidence.
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "scribe"
    assert cmd.params["substate"] == "DRAFT"
    assert cmd.params["evidence"]["check"] == "scope_overflow"


def test_write_to_collect():
    """SM-01.3@v0.4"""
    s = state_of(SHIELD_DISPATCH, SHIELD_DONE)
    assert s.substate == "COLLECT"


def test_write_retry():
    """SM-01.4@v0.4"""
    fail = (
        "outcome.received",
        {"role": "shield", "status": "failed", "failure_class": "agent_failed"},
    )
    s = state_of(SHIELD_DISPATCH, fail)
    assert s.substate == "WRITE"
    assert s.current_attempt == 1


def test_collect_to_red_check():
    """SM-01.5@v0.4 (D-41 v3): collection success enters RED_CHECK."""
    s = state_of(CAPTURE_CMD, BASELINE_CAPTURED, SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED)
    assert s.substate == "RED_CHECK"


def test_collect_fail_to_write():
    """SM-01.6@v0.4"""
    failed = ("test.collected", {"status": "failed", "collected_count": 0, "errors": ["err"]})
    s = state_of(SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, failed)
    assert s.substate == "WRITE"


def test_diagnose_test_defect():
    """SM-01.11@v0.4"""
    s = _diagnose_state("test_defect")
    assert s.substate == "WRITE"


def test_diagnose_stub_gap():
    """SM-01.12@v0.4"""
    s = _diagnose_state("stub_gap")
    assert s.diagnose_classification == "stub_gap"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


# -- v0.5 batch 2: ResultCheckpoint pipeline for M-TEST ------------------------


def _shield_checkpoint(cmd_id="C1"):
    """Minimal result_checkpoint payload for M-TEST Shield WRITE."""
    return {
        "source": "shield",
        "stage": "M-TEST",
        "substate": "WRITE",
        "actor_kind": "agent",
        "artifacts": ["tests/integration/test_ac_fr0010_01.py"],
        "allowed_paths": ["tests/integration/test_ac_fr0010_01.py"],
        "base_sha": "b",
        "checks": ["write_scope", "collection"],
        "requires_diff": False,
        "forbid_diff": False,
        "discussion_only": False,
        "commit_label": "M-TEST: shield commit",
        "result_id": cmd_id,
        "digests": {},
        "domain_event": {"type": "test.written", "payload": {}},
    }


def _prism_checkpoint(verdict="pass", cmd_id="C3"):
    """Minimal result_checkpoint payload for M-TEST Prism review."""
    return {
        "source": "prism",
        "stage": "M-TEST",
        "substate": "PRISM_REVIEW",
        "actor_kind": "agent",
        "verdict": verdict,
        "artifacts": list(_M_TEST_CONTEXT_DOCS),
        "allowed_paths": list(_M_TEST_CONTEXT_DOCS),
        "base_sha": "b",
        "checks": ["template"],
        "requires_diff": verdict != "pass",
        "forbid_diff": False,
        "discussion_only": True,
        "commit_label": f"M-TEST: prism ({verdict}) checkpoint",
        "result_id": cmd_id,
        "digests": {},
        "domain_event": {
            "type": "prism.verdict",
            "payload": {"verdict": verdict, "criteria_pack": dict(_CRITERIA_PACK)},
        },
    }


SHIELD_DONE_CP = (
    "outcome.received",
    {"role": "shield", "status": "done", "result_checkpoint": _shield_checkpoint()},
)


def test_shield_write_outcome_sets_active_result():
    """Shield WRITE outcome with result_checkpoint sets active_result;
    decide() drives validate_result (test file artifacts, write_scope+collection checks)."""
    s = state_of(SHIELD_DISPATCH, SHIELD_DONE_CP)
    assert s.active_result is not None
    assert s.active_result["domain_event"]["type"] == "test.written"
    cmd = decide(s)
    assert cmd.kind == "validate_result"
    assert "write_scope" in cmd.params["checks"]
    assert "collection" in cmd.params["checks"]


def test_test_written_transitions_write_to_collect():
    """After pipeline publishes test.written, the reducer transitions
    WRITE -> COLLECT and clears active_result."""
    s = state_of(
        SHIELD_DISPATCH,
        SHIELD_DONE_CP,
        (
            "result.validated",
            {
                "artifacts": ["tests/integration/test_ac_fr0010_01.py"],
                "base_sha": "b",
                "result_id": "C1",
            },
        ),
        (
            "result.checkpointed",
            {"created_commit": True, "commit_sha": "c", "base_sha": "b", "result_id": "C1"},
        ),
        ("test.written", {"commit_sha": "c", "result_id": "C1"}),
    )
    assert s.active_result is None
    assert s.substate == "COLLECT"


def test_prism_verdict_outcome_sets_active_result():
    """M-TEST Prism outcome with result_checkpoint sets active_result;
    decide() drives validate_result for the context docs."""
    base = [
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
    ]
    prism_done = (
        "outcome.received",
        {
            "role": "prism",
            "status": "done",
            "result_checkpoint": _prism_checkpoint("pass"),
            "criteria_pack": dict(_CRITERIA_PACK),
        },
    )
    s = state_of(*base, prism_done)
    assert s.active_result is not None
    cmd = decide(s)
    assert cmd.kind == "validate_result"
    assert set(cmd.params["artifacts"]) == set(_M_TEST_CONTEXT_DOCS)


def test_prism_pass_pipeline_publishes_verdict():
    """Full pipeline for M-TEST Prism pass (D-41 v3): validate -> checkpoint ->
    publish -> prism.verdict(pass) -> EXIT, active_result cleared."""
    base = [
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
    ]
    prism_done = (
        "outcome.received",
        {
            "role": "prism",
            "status": "done",
            "result_checkpoint": _prism_checkpoint("pass"),
            "criteria_pack": dict(_CRITERIA_PACK),
        },
    )
    s = state_of(
        *base,
        prism_done,
        (
            "result.validated",
            {"artifacts": list(_M_TEST_CONTEXT_DOCS), "base_sha": "b", "result_id": "C3"},
        ),
        (
            "result.checkpointed",
            {"created_commit": False, "commit_sha": "c", "base_sha": "b", "result_id": "C3"},
        ),
        (
            "prism.verdict",
            {"verdict": "pass", "criteria_pack": dict(_CRITERIA_PACK), "result_id": "C3"},
        ),
    )
    assert s.active_result is None
    assert s.substate == "EXIT"


def test_prism_revise_pipeline_transitions_to_write():
    """Full pipeline for M-TEST Prism revise: publish -> prism.verdict(revise)
    -> WRITE, active_result cleared, attempt consumed."""
    base = [
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
    ]
    prism_done = (
        "outcome.received",
        {
            "role": "prism",
            "status": "done",
            "result_checkpoint": _prism_checkpoint("revise"),
            "criteria_pack": dict(_CRITERIA_PACK),
        },
    )
    s = state_of(
        *base,
        prism_done,
        (
            "result.validated",
            {"artifacts": list(_M_TEST_CONTEXT_DOCS), "base_sha": "b", "result_id": "C3"},
        ),
        (
            "result.checkpointed",
            {"created_commit": True, "commit_sha": "c", "base_sha": "b", "result_id": "C3"},
        ),
        (
            "prism.verdict",
            {"verdict": "revise", "criteria_pack": dict(_CRITERIA_PACK), "result_id": "C3"},
        ),
    )
    assert s.active_result is None
    assert s.substate == "WRITE"
    assert s.current_attempt == 1


# -- PRISM_REVIEW defect_classification routing ----------------------------------


def _prism_revise_with_dc(dc):
    """prism.verdict(revise) with the given defect_classification."""
    payload = {"verdict": "revise", "criteria_pack": dict(_CRITERIA_PACK)}
    if dc is not None:
        payload["defect_classification"] = dc
    return ("prism.verdict", payload)


def test_prism_revise_test_plan_defect_to_design():
    """prism.verdict(revise, test_plan_defect) -> DIAGNOSE with stub_gap
    classification; decide() -> rollback_stage(M-DESIGN)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc("test_plan_defect"),
    )
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"


def test_prism_revise_acceptance_defect_to_acc():
    """prism.verdict(revise, acceptance_defect) -> awaiting_human, rollback
    M-ACC."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc("acceptance_defect"),
    )
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target == "M-ACC"


def test_prism_revise_spec_defect_to_spec():
    """prism.verdict(revise, spec_defect) -> awaiting_human, rollback M-SPEC."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc("spec_defect"),
    )
    assert s.status == "awaiting_human"
    assert s.awaiting == "rollback"
    assert s.return_target == "M-SPEC"


def test_prism_revise_test_defect_stays_write():
    """Backward compat: prism.verdict(revise) with no defect_classification
    -> WRITE, current_attempt incremented (current behavior)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc(None),
    )
    assert s.substate == "WRITE"
    assert s.current_attempt == 1


def test_prism_revise_test_defect_explicit():
    """prism.verdict(revise, test_defect) -> WRITE (explicit test_defect works
    same as default)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc("test_defect"),
    )
    assert s.substate == "WRITE"
    assert s.current_attempt == 1


def test_prism_revise_null_defect_classification_routes_to_write():
    """Bugfix regression: prism.verdict(revise) whose payload carries
    defect_classification=null (key present, value None) must default to
    test_defect -> WRITE, resetting the author dispatch and consuming the
    attempt. Before the fix `p.get("defect_classification", "test_defect")`
    returned None, no routing branch ran, substate stayed PRISM_REVIEW with
    reviewer_dispatched=True, and the run halted."""
    payload = {
        "verdict": "revise",
        "criteria_pack": dict(_CRITERIA_PACK),
        "defect_classification": None,
    }
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        ("prism.verdict", payload),
    )
    assert s.substate == "WRITE"
    assert s.doc_dispatched is False
    assert s.current_attempt == 1
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "shield"


def test_prism_revise_empty_defect_classification_routes_to_write():
    """prism.verdict(revise) with an empty defect_classification defaults to
    test_defect -> WRITE (same as absent/None)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc(""),
    )
    assert s.substate == "WRITE"
    assert s.current_attempt == 1


def test_prism_revise_unknown_classification_fails_closed():
    """prism.verdict(revise) with an unknown non-empty defect_classification
    is NOT silently defaulted: no routing branch runs, the substate stays
    PRISM_REVIEW (fail-closed halt, reviewer still dispatched)."""
    s = state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        _prism_revise_with_dc("mystery_defect"),
    )
    assert s.substate == "PRISM_REVIEW"
    assert s.reviewer_dispatched is True
    assert decide(s) is None


def test_prism_pass_revise_pass_completes_at_boundary():
    """Historical v0.4 pass|revise|pass: the third Prism token is consumed
    and the run completes at the M-TEST boundary. The revise token carries
    defect_classification=null (the published form before the serialization
    hardening) and must route revise -> WRITE so the Shield re-dispatch and
    the third pass both run."""

    def _prism_done():
        return PRISM_DONE

    # 1st token: pass -> RED_CHECK -> EXIT -> trace fails -> WRITE (attempt 1)
    evs = [
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        PRISM_DISPATCH,
        _prism_done(),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_CRITERIA_PACK)}),
        RUN_CMD,
        RED_VALID,
        TRACE_CMD,
        ("verdict.failed", {"check": "trace", "reason": "orphan", "attempt": 1}),
    ]
    s = state_of(*evs)
    assert s.substate == "WRITE"
    assert s.current_attempt == 1
    # 2nd token: revise (defect_classification=null) -> WRITE (attempt 2)
    evs += [
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        PRISM_DISPATCH,
        _prism_done(),
        (
            "prism.verdict",
            {
                "verdict": "revise",
                "criteria_pack": dict(_CRITERIA_PACK),
                "defect_classification": None,
            },
        ),
    ]
    s = state_of(*evs)
    assert s.substate == "WRITE"
    assert s.current_attempt == 2
    # 3rd token: pass -> RED_CHECK -> EXIT -> trace pass -> commit -> boundary
    evs += [
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        PRISM_DISPATCH,
        _prism_done(),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_CRITERIA_PACK)}),
        RUN_CMD,
        RED_VALID,
        TRACE_CMD,
        TRACE_PASS,
        COMMIT_CMD,
        TEST_COMMITTED,
        ("stage.exited", {"stage": "M-TEST"}),
        ("run.completed", {"terminal_state": "boundary"}),
    ]
    s = state_of(*evs)
    assert s.stage == "M-TEST"
    assert s.test_committed is True
    assert s.status == "completed"
    assert s.terminal_state == "boundary"
    assert decide(s) is None  # M-TEST boundary: run done
