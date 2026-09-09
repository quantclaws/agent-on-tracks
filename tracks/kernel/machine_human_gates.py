"""Human gate reducers (SM-05): human.triage/review/retry, preview.generated,
human.approval/return/recover, approval.recorded, issues.created.

Extracted from ``machine.py`` for module-size compliance (C0302).

No circular import: imports only ``events``, ``m_test`` helpers and
``stage_registry`` at runtime; ``State`` is imported under
``TYPE_CHECKING`` only (duck-typed at runtime). ``machine.py`` imports
the reducers from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import EventEnvelope
from .m_test import _reset_doc, _reset_review
from .stage_registry import _uncommit

if TYPE_CHECKING:
    from .machine import State


def _on_human_triage(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    s.awaiting = None
    s.triage_decision = p["decision"]
    if p["decision"] == "go":
        s.substate = "DRAFT"
        _reset_doc(s)
        # Fresh retry budget: TRIAGE failures (and their evidence) must not
        # carry into DRAFT -- the two substates share `current_attempt` but
        # budget per substate (FR-11).  DRAFT starts at attempt 0 so decide()
        # emits attempt=1; last_failure=None so no stale TRIAGE evidence leaks
        # into the DRAFT dispatch prompt.
        s.current_attempt = 0
        s.last_failure = None
    else:
        s.substate = None


def _on_human_review(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    s.awaiting = None
    s.substate = "EXIT" if p["action"] == "no_comment" else "RESPOND"
    if s.substate != "RESPOND":
        return
    _reset_doc(s)
    s.review_diff_ref = p.get("diff_ref")
    # Fresh 3-attempt budget for the new RESPOND round triggered by the human
    # comment; prior DRAFT/RESPOND failures must not consume it.
    s.current_attempt = 0
    _uncommit(s)


def _on_human_retry(s: State, p: dict, ev: EventEnvelope) -> None:
    # Fix 4: human.retry clears escalation and lets decide() re-dispatch from
    # the current substate with a fresh attempt budget. last_failure is
    # preserved so the re-dispatch prompt still carries failure evidence (FR-11)
    # for the agent to act on. Failure handling has already reset the
    # appropriate dispatch flag (doc/review) before escalation; no need to
    # indiscriminately reset both here.
    #
    # --clear-evidence (payload clear_evidence=true): the operator signals the
    # underlying program/config/validator was fixed, so the old failure
    # evidence is stale. last_failure is dropped and the dispatch flag reset so
    # decide() re-dispatches from a clean slate (attempt=1, no evidence). This
    # also covers the active-state recovery path: an ordinary retry already
    # cleared escalation, a dispatch was issued and then killed by Maestro
    # (doc_dispatched stays True, no result), and the stale evidence must not
    # leak into the next dispatch. apply() already cleared s.pending (any non-
    # command.issued event does); _reset_doc lets decide() issue a fresh one.
    # Review-substate parity (run 01KZTHE7 2026-08-15): a PRISM_PLAN dispatch
    # killed together with the loop left reviewer_dispatched=True with no
    # reviewer_produced ever arriving; decide() then had nothing to issue and
    # the loop exited on every restart. --clear-evidence must reset the review
    # flags too, not just the doc flag.
    if p.get("clear_evidence"):
        s.last_failure = None
        _reset_doc(s)
        _reset_review(s)
    s.awaiting = None
    s.status = "active"
    s.current_attempt = 0
    s.infra_failure_streak = 0
    s.format_failure_streak = 0
    # M-IMPL NEEDS_ATTENTION reconcile exit (operator finding 2026-08-24,
    # run 01M0S0FQ): a baseline.frozen(status=stale) parks the stage with
    # decide() halted (flow.md §10.2 "awaiting reconcile"). The operator's
    # retry IS the reconcile confirmation: re-enter BASELINE so decide()
    # re-freezes against the reconciled reality (the residency-scoped
    # reference makes an unchanged-since-park tree freeze back to current).
    if s.stage == "M-IMPL" and s.substate == "NEEDS_ATTENTION":
        s.substate = "BASELINE"


def _on_preview_generated(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.2 (also the C-02 / stale re-preview path: back to the human gate)
    s.preview_ready = True
    s.approved = False
    s.substate = "AWAIT_HUMAN"
    s.awaiting = "approval"
    s.status = "awaiting_human"


def _on_human_approval(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.awaiting == "rollback":
        # SM-01.13 / flow.md §10.1: Human approved the ac_gap/spec_gap
        # rollback (M-TEST or M-IMPL) -- clear the gate and let decide()
        # produce rollback_stage(return_target).
        # B57 re-fix (#73): a lineage park leaves return_target None -- the
        # Human chooses the target at approval time and the choice rides the
        # approval event (`to_stage`, closed set validated by the CLI). A
        # preset gap-typed target (ac_gap->M-ACC / spec_gap->M-SPEC) stays
        # authoritative unless the Human explicitly overrides it here.
        if isinstance(p.get("to_stage"), str) and p["to_stage"]:
            s.return_target = p["to_stage"]
        if not s.return_target:
            # Defense in depth (CLI already fails closed): an approval with
            # no preset target and no chosen to_stage cannot produce a valid
            # rollback_stage command -- the park stays until a complete
            # approval arrives.
            return
        s.awaiting = None
        s.status = "active"
        s.substate = "RETURNED"
        return
    # SM-05.3 - the ONLY entry into APPROVED (FR-0180 hard human gate)
    s.approved = True
    s.awaiting = None
    s.status = "active"
    s.substate = "APPROVED"
    s.approval_digest = p["digest"]
    s.approval_actor = p["actor"]


def _on_human_return(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.4/.7
    s.returned = True
    s.awaiting = None
    s.status = "active"
    s.substate = "RETURNED"
    s.return_target = p["to_stage"]


def _on_human_recover(s: State, p: dict, ev: EventEnvelope) -> None:
    # B32 (#32): forward-recovery intent. Unlike human.return this never rolls
    # back -- it records a forward target and halts decide() until recover_stage
    # re-enters the target stage. last_failure is deliberately preserved (the
    # original failure evidence survives recovery for the fresh M-IMPL cycle).
    s.awaiting = None
    s.status = "active"
    s.substate = "RECOVER_PENDING"
    s.recover_target = p["to_stage"]
    s.recover_reason = p["reason"]


def _on_approval_recorded(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.5 (C-01): recording the approval identity IS the APPROVED->ISSUES
    # transition, materializing ISSUES as an observable substate.
    s.approval_digest = p["digest"]
    s.approval_actor = p["actor"]
    s.substate = "ISSUES"


def _on_issues_created(s: State, p: dict, ev: EventEnvelope) -> None:
    s.issues_created = True
