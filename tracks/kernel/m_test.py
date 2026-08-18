"""M-TEST state-machine logic (flow.md §9 / SM-01, FR-0010~0070).

Extracted from ``machine.py`` for module-size compliance (C0302). Contains the
M-TEST reducers, decide() control flow, criteria-pack constants, and the shared
attempt-accounting helpers used across all stages.

No circular import: this module imports only from ``events`` at runtime;
``State`` is imported under ``TYPE_CHECKING`` only (duck-typed at runtime).
``machine.py`` imports the helpers, reducers, and decide functions from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import WRITE_MANIFEST_CONTRACT
from .events import Command, EventEnvelope

if TYPE_CHECKING:
    from .machine import State


# -- shared helpers (used by reducers across all stages) --------------------


def _reset_doc(s: State) -> None:
    s.doc_dispatched = s.doc_produced = s.doc_validated = False


def _reset_review(s: State) -> None:
    s.reviewer_dispatched = s.reviewer_produced = False


def _consume_attempt(s: State) -> None:
    """Shared attempt accounting: increment; escalate to awaiting_human at >=3."""
    s.current_attempt += 1
    if s.current_attempt >= 3:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


# -- M-TEST constants -------------------------------------------------------

# D-29 criteria pack identity (architecture.md §3.4): Runtime decides the pack
# name+version; Prism loads it and echoes the identity in its verdict.
_CRITERIA_PACK = {"name": "tracks-prism-test", "version": "0.1"}
# Shield / M-TEST Prism read-only context docs (interfaces.md §3a).
_M_TEST_CONTEXT_DOCS = ("test-plan.md", "interfaces.md", "acceptance.md")


# -- M-TEST reducers (flow.md §9 / SM-01, FR-0010~0070) -----------------------


def _on_m_test_outcome_done(s: State) -> None:
    """SM-01.3: Shield outcome done -> COLLECT; Prism outcome -> produced."""
    if s.substate == "WRITE":
        s.substate = "COLLECT"
    elif s.substate == "PRISM_REVIEW":
        s.reviewer_produced = True


def _on_test_collected(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-01.5/.6: passed -> PRISM_REVIEW; failed -> WRITE (re-dispatch Shield).
    if p["status"] == "passed":
        s.test_collected = True
        s.substate = "PRISM_REVIEW"
        _reset_review(s)
    else:
        s.test_collected = False
        s.substate = "WRITE"
        _reset_doc(s)
        _consume_attempt(s)  # shared <=3 budget


def _on_red_validated(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-01.9/.10: valid -> EXIT; invalid -> DIAGNOSE.
    s.red_findings = p.get("findings")
    if p["status"] == "valid":
        s.red_validated = True
        s.substate = "EXIT"
    else:
        s.red_validated = False
        s.substate = "DIAGNOSE"


def _on_test_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-01.14: test asset frozen. The executor follows up with stage.exited +
    # run.completed (M-IMPL not registered -> boundary).
    s.active_result = None  # v0.5: pipeline publish complete
    s.test_committed = True
    # M-IMPL RGR: a runtime-committed Shield test fix re-freezes the R tree --
    # the GREEN regression gate must diff against the fix commit, not the
    # original RED checkpoint, or every sanctioned fix reads as drift (run
    # 01KZTHE7 T-017: Shield fix 7d8234b flagged "R unit tests changed").
    commit_sha = p.get("commit_sha")
    if commit_sha:
        s.r_tree_identity = commit_sha


def _on_test_written(s: State, p: dict, ev: EventEnvelope) -> None:
    # v0.5 batch 2: Shield WRITE checkpoint published. Transition to COLLECT
    # (same as _on_m_test_outcome_done for WRITE -> COLLECT).
    s.active_result = None  # v0.5: pipeline publish complete
    if s.substate == "WRITE":
        s.substate = "COLLECT"


def _on_m_test_verdict_failed(s: State, p: dict) -> None:
    """M-TEST verdict.failed routing (FR-0040/0060/0070).

    - ``criteria_pack_mismatch`` (PRISM_REVIEW): re-dispatch Prism, consume
      the shared <=3 budget (anti-self-report triple, D-29).
    - ``trace`` (EXIT, SM-01.15): re-dispatch Shield (WRITE), consume budget.
    - ``test_defect`` (DIAGNOSE, SM-01.11): re-dispatch Shield (WRITE), consume.
    - ``stub_gap`` (DIAGNOSE, SM-01.12): rollback M-DESIGN, no Human.
    - ``ac_gap``/``spec_gap`` (DIAGNOSE, SM-01.13): await Human approval, then
      rollback M-ACC / M-SPEC.
    """
    check = p.get("check")
    if check == "criteria_pack_mismatch":
        _reset_review(s)
        _consume_attempt(s)
        return
    if check == "trace":
        s.trace_passed = False
        s.substate = "WRITE"
        _reset_doc(s)
        _consume_attempt(s)
        return
    if check == "commit":
        # D-30/F-1: hook rejected test commit -> re-dispatch Shield (mirrors
        # check=="trace": reset trace_passed, WRITE, consume budget).
        s.trace_passed = False
        s.substate = "WRITE"
        _reset_doc(s)
        _consume_attempt(s)
        return
    classification = check  # test_defect | stub_gap | ac_gap | spec_gap
    s.diagnose_classification = classification
    # Same State-carried snapshot as M-IMPL: the WRITE re-dispatch reads the
    # verdict details from diagnose_report so provider-failure retries and
    # `trac retry --clear-evidence` never strip the diagnosis (FR-11).
    s.diagnose_report = {k: p.get(k) for k in ("check", "reason", "evidence", "attempt")}
    if classification == "test_defect":
        s.substate = "WRITE"
        _reset_doc(s)
        _consume_attempt(s)
    elif classification == "stub_gap":
        # rollback M-DESIGN -- decide() produces rollback_stage(M-DESIGN)
        s.diagnose_classification = "stub_gap"
    elif classification in ("ac_gap", "spec_gap"):
        # Human must approve the rollback (SM-01.13)
        s.status = "awaiting_human"
        s.awaiting = "rollback"
        s.return_target = "M-ACC" if classification == "ac_gap" else "M-SPEC"
    else:
        # Pipeline validation failures (template, discussion_diff, no_diff,
        # forbidden_diff, digest_drift, publish_error, etc.): reset the
        # appropriate dispatch flag and consume an attempt to prevent
        # infinite re-dispatch loops. no_diff_justified (v0.5 no_diff peer
        # review: reviewer rejected the no-diff explanation) is also routed
        # here — it consumes an attempt like any other author failure.
        if s.substate == "PRISM_REVIEW":
            _reset_review(s)
        else:
            s.substate = "WRITE"
            _reset_doc(s)
        _consume_attempt(s)


# -- M-TEST decide() control flow (flow.md §9 / SM-01) -----------------------


def _m_test_shield_dispatch(s: State) -> Command:
    """DISPATCH/WRITE: dispatch Shield to write integration/e2e tests."""
    objective = "write integration/e2e tests against interface stubs"
    report = s.diagnose_report or {}
    reason = report.get("reason")
    evidence = report.get("evidence")
    if reason or evidence:
        # test_defect re-dispatch: carry the DIAGNOSE verdict details so
        # Shield fixes the pinpointed defects instead of re-deriving them.
        parts = [f"reason: {reason}"] if reason else []
        if evidence:
            parts.append(f"evidence: {evidence}")
        objective += (
            f". Fix the diagnosed test defects (Prism verdict "
            f"{report.get('check') or 'test_defect'}): " + "; ".join(parts)
        )
    params = {
        "role": "shield",
        "substate": "WRITE",
        "objective": objective,
        "stage": "M-TEST",
        "attempt": s.current_attempt + 1,
        "docs": list(_M_TEST_CONTEXT_DOCS),
        "assignment": {
            "kind": "WRITE",
            "skills": ["tracks-discuz"],
            "docs": list(_M_TEST_CONTEXT_DOCS),
            # B28/#30 slim: front-load the manifest contract (schema +
            # example + pre-return self-checks) so the writer validates its
            # own output instead of burning dispatches on shape errors.
            "manifest_contract": dict(WRITE_MANIFEST_CONTRACT),
        },
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)  # FR-11
    return Command(kind="dispatch_agent", params=params)


def _m_test_prism_dispatch(s: State) -> Command:
    """PRISM_REVIEW: dispatch Prism with the criteria pack (D-29 triple ①)."""
    params = {
        "role": "prism",
        "substate": "PRISM_REVIEW",
        "objective": "review test contract against the criteria pack",
        "stage": "M-TEST",
        "attempt": s.current_attempt + 1,
        "docs": list(_M_TEST_CONTEXT_DOCS),
        "assignment": {
            "kind": "PRISM_REVIEW",
            "skills": ["tracks-discuz", "tracks-prism-test"],
            "docs": list(_M_TEST_CONTEXT_DOCS),
            "criteria_pack": dict(_CRITERIA_PACK),
        },
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


def _decide_m_test(s: State, sub: str) -> Command | None:
    """M-TEST explicit control flow (architecture.md §1.2, SM-01).

    Pure (NFR-0030): no I/O, no clock/env. Each substate maps to at most one
    Command; substates not listed produce None (halt / awaiting result).
    """
    if sub == "DISPATCH":
        return _m_test_shield_dispatch(s)  # SM-01.2: first Shield dispatch
    if sub == "WRITE":
        if s.doc_dispatched:
            return None  # awaiting Shield outcome
        return _m_test_shield_dispatch(s)  # SM-01.4/.6/.8/.11/.15 re-dispatch
    if sub == "COLLECT":
        return Command(kind="collect_tests", params={"stage": "M-TEST"})  # SM-01.5
    if sub == "PRISM_REVIEW":
        if s.reviewer_dispatched:
            return None  # awaiting Prism verdict
        return _m_test_prism_dispatch(s)  # SM-01.7
    if sub == "RED_CHECK":
        return Command(kind="run_tests", params={"stage": "M-TEST"})  # SM-01.9
    if sub == "EXIT":
        return _m_test_exit_route(s)  # SM-01.14/.15
    if sub == "DIAGNOSE":
        return _m_test_diagnose_route(s)  # SM-01.11/.12/.13
    if sub == "RETURNED":
        # SM-01.13: Human approved ac_gap/spec_gap rollback (diagnose_rollback)
        # or SM-05.7: Human return from escalation (human_return).
        reason = "human_return" if s.returned else "diagnose_rollback"
        return Command(
            kind="rollback_stage", params={"to_stage": s.return_target, "reason": reason}
        )
    return None


def _m_test_exit_route(s: State) -> Command | None:
    """EXIT: trace gate -> commit tests (pipeline) -> seal. trace_passed and
    test_committed are set by reducers; decide() only fires the next step."""
    if not s.trace_passed:
        return Command(kind="check_trace", params={"stage": "M-TEST"})  # SM-01.14
    if not s.test_committed:
        return Command(kind="commit_tests", params={"stage": "M-TEST"})  # SM-01.14
    if not s.stage_exited:
        return Command(kind="write_frontmatter", params={"stage": "M-TEST"})
    return None


def _m_test_diagnose_route(s: State) -> Command | None:
    """DIAGNOSE four-way routing (FR-0060). test_defect and ac_gap/spec_gap
    are routed by the reducer (WRITE / awaiting_human); stub_gap produces the
    rollback command here (no Human gate, SM-01.12)."""
    if s.diagnose_classification == "stub_gap":
        return Command(kind="rollback_stage", params={"to_stage": "M-DESIGN", "reason": "stub_gap"})
    return None  # test_defect -> WRITE (reducer); ac_gap/spec_gap -> awaiting
