"""Verdict, commit and review reducers: verdict.passed/failed with the
shared attempt escalation, story/spec/acceptance/design.committed,
prism.verdict routing (M-DESIGN / M-TEST / M-IMPL), sage/lex reviewer
verdicts, and the revise re-dispatch evidence shaping.

Extracted from ``machine.py`` for module-size compliance (C0302).

No circular import: imports only ``events``, ``m_impl``/``m_test``
helpers, ``machine_outcomes`` failure handlers and ``stage_registry`` at
runtime; ``State`` is imported under ``TYPE_CHECKING`` only (duck-typed
at runtime). ``machine.py`` imports the reducers from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import EventEnvelope
from .m_impl import (
    _on_m_impl_prism_verdict,
    _on_m_impl_verdict_failed,
    _on_m_impl_verdict_passed,
)
from .m_test import (
    _consume_attempt,
    _on_m_test_verdict_failed,
    _reset_doc,
    _reset_review,
)
from .machine_outcomes import (
    _handle_hotfix_failed_outcome,
    _handle_verdict_format_failure,
    _is_format_verdict,
)
from .stage_registry import (
    _COMMITTED_EVENT,
    _REVIEW_SUBSTATE,
    _STAGES,
    _VERDICT_OWNERS,
    DESIGN_DOCS,
    _uncommit,
)

if TYPE_CHECKING:
    from .machine import State


def _on_verdict_passed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.infra_failure_streak = 0  # the pipeline moved: infra is healthy again
    s.format_failure_streak = 0  # and the last reply was well-formed
    if s.stage == "M-IMPL":
        _on_m_impl_verdict_passed(s, p)
        return
    if s.stage == "M-TEST":
        if p.get("check") == "r2_discharged":
            # Design re-approval re-validation discharge (operator finding
            # 2026-08-24, run 01M0S0FQ third M-TEST cycle): the increment is
            # committed and was validated earlier in this run; this cycle's
            # no-diff review was accepted, so the empty selection is not a
            # vacuous pass. Route to EXIT (trace gate + commit_tests over the
            # committed assets); trace_passed stays False so EXIT still runs
            # check_trace.
            s.substate = "EXIT"
            return
        # EXIT trace gate passed (SM-01.14): trace closure verified; the next
        # decide() step issues commit_tests.
        s.trace_passed = True
        s.last_failure = None
        return
    if s.stage == "M-DESIGN":
        # Multi-doc sequence: each passed validate advances one design doc
        # (DRAFT/RESPOND pipeline and the EXIT gate share the counter).
        s.design_validated += 1
        if s.substate == "EXIT" and s.design_validated >= len(DESIGN_DOCS):
            s.exit_validated = True
    elif s.substate == "EXIT":
        s.exit_validated = True  # review-exit gate passed (AC-1502)
    else:
        s.doc_validated = True
    s.last_failure = None


def _on_verdict_failed(s: State, p: dict, ev: EventEnvelope) -> None:
    # failed_nodes (OOB 2026-09-05): the diagnosis verdict may carry the
    # triggering gate's failing anchors -- the GREEN re-dispatch objective
    # names them (M2 live-evidence rule). Absent on older events -> None.
    s.last_failure = {
        k: p.get(k)
        for k in ("check", "reason", "evidence", "log_ref", "attempt", "failed_nodes")
    }
    is_human = bool(s.active_result and s.active_result.get("actor_kind") == "human")
    s.active_result = None  # v0.5: pipeline failure clears the checkpoint
    if is_human:
        # Human pipeline failure: keep the awaiting gate, no agent retry,
        # no escalation, no substate change.
        return
    if _is_format_verdict(p):
        _handle_verdict_format_failure(s, p)
        return
    s.format_failure_streak = 0
    if s.stage == "M-HOTFIX-TRIAGE":
        _handle_hotfix_failed_outcome(s, p)
        return
    if s.stage == "M-TEST":
        _on_m_test_verdict_failed(s, p)
        return
    if s.stage == "M-IMPL":
        _on_m_impl_verdict_failed(s, p)
        return
    if s.stage == "M-DESIGN":
        _on_design_verdict_failed(s, p)
        return
    if p.get("check") == "commit":
        # D-30/F-1: hook rejected commit (document or EXIT seal) -> re-dispatch
        # drafter with evidence; mirror DRAFT validate-failure budget, not AC-1502.
        # v0.5: if the failure came from a reviewer checkpoint (substate is a
        # review substate), reset the reviewer instead of the author.
        if s.substate in _REVIEW_SUBSTATE:
            _reset_review(s)
        else:
            _reset_doc(s)
            _uncommit(s)
            s.substate = "DRAFT"
        s.exit_validated = False
        _escalate_or_continue(s, p)
        return
    if s.substate == "EXIT":
        # AC-1502: gate failed at review exit -> block exit, await human.
        s.status = "awaiting_human"
        s.awaiting = "review"
        return
    # v0.5: if the failure came from a reviewer pipeline (substate is a review
    # substate), reset the reviewer instead of the author doc.
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    if p.get("check") == "scope_overflow":
        s.scope_overflow = True  # FR-20: rollback, never escalation
        return
    _escalate_or_continue(s, p)


def _escalate_or_continue(s: State, p: dict) -> None:
    """Consume an attempt and escalate on the 3rd failure."""
    s.current_attempt = int(p.get("attempt", s.current_attempt + 1))
    if s.current_attempt >= 3:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _on_design_verdict_failed(s: State, p: dict) -> None:
    """BS-05: no human gate in M-DESIGN - a failed validate, even at the
    EXIT gate, falls back to Archer re-dispatch; 3rd attempt escalates.
    D-30/F-1: design_committed also resets.
    D-32: a failed reviewer ResultCheckpoint (e.g. Prism review no_diff) resets
    only the reviewer flags and retries the same actor — it is NOT a revise
    verdict, must not discard the already-committed design docs, and never a
    Human."""
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
        _escalate_or_continue(s, p)
        return
    s.design_validated = 0
    s.design_committed = 0
    if s.substate == "EXIT":
        s.exit_validated = False
        s.substate = "RESPOND"
    _reset_doc(s)
    _escalate_or_continue(s, p)


def _on_doc_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # story/spec/acceptance.committed share one shape: the committed flag,
    # then (non-final) the stage's review substate. Facts come from the stage
    # owning this committed event, not from s.stage (FR-0160).
    sd = _COMMITTED_EVENT[ev.type]
    setattr(s, sd.committed_flag, True)
    s.active_result = None  # v0.5: pipeline publish complete
    if not p.get("final"):
        s.substate = sd.review_substate
        _reset_review(s)


def _on_design_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # M-DESIGN: one design.committed event per doc (payload carries `doc`).
    # After the third doc of the cycle the stage enters PRISM_REVIEW.
    # v0.5: active_result is only cleared after the last doc — publish_result
    # emits all 3 design.committed events in one call; clearing on the first
    # would break reconcile (active_result=None → publish skipped).
    s.design_committed += 1
    if s.design_committed >= len(DESIGN_DOCS):
        s.active_result = None  # pipeline publish complete (all docs)
        s.substate = _STAGES["M-DESIGN"].review_substate
        _reset_review(s)


def _review_last_failure(p: dict) -> dict:
    """FR-11 + D-35: the revise re-dispatch evidence carries the reviewer's
    structured summary/findings and the blobs ref for the full body (SC-D35
    §2.3, AC-03). Stale infra-failure evidence must never replay instead
    (run 01KZTHE7: revise retry carried "opencode exited 1" from Prism's own
    crash)."""
    failure = {
        "check": "prism.verdict",
        "reason": p.get("review_summary")
        or f"reviewer verdict=revise ({p.get('defect_classification') or 'test_defect'}); "
        "address anchored discussion threads before re-submitting",
        "evidence": p.get("findings") or p.get("discussion_refs"),
    }
    if p.get("review_ref"):
        failure["review_ref"] = p["review_ref"]
    return failure


def _on_prism_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    if p.get("scope") == "security":
        return  # A security review cannot mutate the parked task's review state.
    s.active_result = None  # v0.5: pipeline publish complete
    if p.get("verdict") != "pass":
        s.last_failure = _review_last_failure(p)
    if s.stage == "M-IMPL":
        _on_m_impl_prism_verdict(s, p)
        return
    if s.stage == "M-TEST":
        # D-41 v3 timing (flow.md §9.1): RED_CHECK already ran before
        # PRISM_REVIEW, so a pass verdict goes straight to EXIT; revise routes
        # by defect_classification back to WRITE/upstream. The shared <=3
        # budget is consumed on revise (NOT reset -- unlike M-DESIGN, M-TEST
        # shares one budget across WRITE/COLLECT/RED_CHECK/PRISM_REVIEW/EXIT).
        s.criteria_pack_loaded = p.get("criteria_pack")
        if p["verdict"] == "pass":
            s.substate = "EXIT"
            return
        # REVISE: route by defect_classification (default test_defect).
        # `or` treats absent AND None/empty (e.g. old replay events carrying
        # defect_classification=null) as test_defect; unknown non-empty tokens
        # still fall through all branches -> fail-closed (substate unchanged).
        dc = p.get("defect_classification") or "test_defect"
        if dc == "test_defect":
            s.substate = "WRITE"
            _reset_doc(s)
            _consume_attempt(s)
            # Stale-diagnosis hygiene (operator finding, 2026-08-24 run
            # 01M0S0FQ): a Prism REVISE routing back to WRITE must not leave
            # an older DIAGNOSE verdict (e.g. empty_r2) in diagnose_report —
            # _m_test_shield_dispatch would prefix the re-dispatch objective
            # with the outdated "Fix the diagnosed test defects" text while
            # the real findings travel via last_failure/params.evidence.
            # Clear it so the prompt text matches the evidence it carries.
            s.diagnose_report = None
        elif dc == "test_plan_defect":
            # Rollback to M-DESIGN (like stub_gap in DIAGNOSE)
            s.substate = "DIAGNOSE"
            s.diagnose_classification = "stub_gap"
        elif dc == "acceptance_defect":
            # awaiting Human, then rollback M-ACC
            s.status = "awaiting_human"
            s.awaiting = "rollback"
            s.return_target = "M-ACC"
        elif dc == "spec_defect":
            # awaiting Human, then rollback M-SPEC
            s.status = "awaiting_human"
            s.awaiting = "rollback"
            s.return_target = "M-SPEC"
        return
    # M-DESIGN has no human review gate (BS-05 / flow.md §8.3): pass goes
    # straight to EXIT; revise re-dispatches Archer via RESPOND.
    if s.stage == "M-DESIGN" and p.get("anchor_verdict") == "overturned":
        # FR-0243-03: M-DESIGN Prism overturned the hotfix anchor -> roll back
        # to M-HOTFIX-TRIAGE/SAGE_TRIAGE for Sage re-anchoring. The M-DESIGN
        # redispatch budget is NOT consumed (upstream product defect).
        s.substate = "DIAGNOSE"
        s.diagnose_classification = "anchor_overturned"
        return
    s.prism_passed_this_round = p["verdict"] == "pass"
    if p["verdict"] == "pass":
        s.substate = "EXIT"
        s.design_validated = 0  # the exit gate re-validates all three docs
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    s.design_validated = 0
    s.design_committed = 0
    # Fresh 3-attempt budget for RESPOND (flow.md §8.3: 重派 Archer <=3 per
    # RESPOND round); DRAFT/EXIT failures must not consume the RESPOND budget.
    s.current_attempt = 0


def _on_reviewer_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    owners = _VERDICT_OWNERS[ev.type]
    if p["verdict"] == "pass":
        setattr(s, owners[0].reviewer_passed_flag, True)
        s.substate = "HUMAN_REVIEW"
        s.awaiting = "review"
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    # Fresh 3-attempt budget for RESPOND (flow.md: 重派 <=3 per RESPOND round);
    # DRAFT failures must not consume the RESPOND budget.
    s.current_attempt = 0
    if len(owners) == 1:
        # RESPOND must re-commit to re-enter review; a uniquely owned verdict
        # event (sage.verdict -> story) names the doc itself.
        setattr(s, owners[0].committed_flag, False)
    else:
        _uncommit(s)  # shared verdict event: the current stage picks the doc
