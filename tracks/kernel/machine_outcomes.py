"""Dispatch/outcome accounting and failure digestion: command.issued
progress tracking, outcome.received routing, the infra and output-format
failure classes with their bounded re-dispatch/escalation policies, and
the failed-outcome stage routing (hotfix / M-TEST / M-IMPL).

Extracted from ``machine.py`` for module-size compliance (C0302).

No circular import: imports only ``events``, ``m_impl``/``m_test``
helpers and ``stage_registry`` at runtime; ``State`` is imported under
``TYPE_CHECKING`` only (duck-typed at runtime). ``machine.py`` imports
the reducers from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import EventEnvelope
from .m_impl import (
    _M_IMPL_REVIEW_SUBSTATES,
    _on_m_impl_outcome_done,
    _set_m_impl_dispatch_flags,
)
from .m_test import (
    _consume_attempt,
    _on_m_test_outcome_done,
    _reset_doc,
    _reset_review,
)
from .stage_registry import _REVIEW_SUBSTATE

if TYPE_CHECKING:
    from .machine import State


def _on_command_issued(s: State, p: dict, ev: EventEnvelope) -> None:
    cmd = p.get("command", {})
    s.pending = cmd
    if cmd.get("kind") != "dispatch_agent":
        _track_non_dispatch_milestone(s, cmd.get("kind"))
        return
    # v0.5 no_diff peer review (checked before M-TEST so the right flag is set).
    if s.substate == "NO_DIFF_REVIEW":
        s.no_diff_reviewer_dispatched = True
        return
    if s.substate == "NO_DIFF_EXPLAIN":
        s.doc_dispatched = True
        return
    if s.stage == "M-TEST":
        # SM-01.2: DISPATCH -> WRITE on the first Shield dispatch. Subsequent
        # Shield re-dispatches (WRITE/SM-01.4/.6/.8/.11/.15) stay in WRITE.
        # PRISM_REVIEW dispatches set reviewer_dispatched so _decide_m_test
        # can guard against re-dispatch while awaiting the verdict.
        if s.substate == "DISPATCH":
            s.substate = "WRITE"
        sub = cmd.get("params", {}).get("substate")
        if sub == "PRISM_REVIEW":
            s.reviewer_dispatched = True
        else:
            s.doc_dispatched = True
        return
    if s.stage == "M-IMPL":
        _set_m_impl_dispatch_flags(s, cmd)
        return
    if s.substate in _REVIEW_SUBSTATE:
        s.reviewer_dispatched = True
    else:
        s.doc_dispatched = True


def _track_non_dispatch_milestone(s: State, kind: str) -> None:
    """Track non-dispatch command progress without added nesting/dispatch
    branching: M-TEST COLLECT substate on ``collect_tests`` and v0.6 hotfix
    (IF-HOTFIX-002) precheck/validate progress markers."""
    if s.stage == "M-TEST" and kind == "collect_tests":
        s.substate = "COLLECT"  # SM-01.3: WRITE -> COLLECT
    elif s.stage == "M-HOTFIX-TRIAGE":
        _track_hotfix_command_progress(s, kind)


def _track_hotfix_command_progress(s: State, kind: str) -> None:
    """v0.6 hotfix (IF-HOTFIX-002): track non-dispatch command progress so
    decide() correctly sequences the sequential flow through PRECHECK,
    SAGE_TRIAGE, and ANCHORED terminal transitions."""
    if kind == "precheck_hotfix":
        s.doc_dispatched = True
    elif kind == "validate_anchor":
        s.doc_validated = True
    # complete_hotfix_entry's result events (baseline.inherited +
    # stage.entered M-DESIGN) land synchronously within the same issue() call,
    # so no tracking flag is needed here.


def _on_outcome_received(s: State, p: dict, ev: EventEnvelope) -> None:
    if p.get("scope") == "security":
        # Out-of-band security review outcome: not the current task's
        # outcome -- its verdict is published through the security face
        # (_publish_security_review_verdict); the task's RGR/outcome
        # pipeline never consumes it.
        return
    status = p.get("status")
    if s.substate == "ISSUES":
        if status != "done":
            _consume_attempt(s)
        return
    if status != "done":
        _handle_failed_outcome(s, p)
        return
    # v0.5: when the outcome carries a result_checkpoint payload, atomically
    # project active_result in the same event - no crash window between
    # outcome.received and a separate result.submitted event.
    checkpoint = p.get("result_checkpoint")
    if checkpoint is not None:
        s.active_result = dict(checkpoint)
    if s.stage == "M-TEST":
        if checkpoint is None:
            _on_m_test_outcome_done(s)
        return
    if s.stage == "M-IMPL":
        if checkpoint is None:
            _on_m_impl_outcome_done(s)
        return
    if s.substate == "TRIAGE":
        s.awaiting = "triage"
    elif s.substate in _REVIEW_SUBSTATE:
        s.reviewer_produced = True
    else:
        s.doc_produced = True


def _reset_m_test_dispatch_flag(s: State) -> None:
    """Reset the flag guarding the current M-TEST substate's dispatcher.

    A write-flags-only reset left reviewer_dispatched stuck True after a
    failed PRISM_REVIEW dispatch, so decide() halted forever even after
    human.retry cleared the escalation gate (run 01KZTHE7RMZE6110PK9C54K1E2
    stalled post prism non_zero_exit)."""
    if s.substate == "PRISM_REVIEW":
        _reset_review(s)
    elif s.substate == "NO_DIFF_REVIEW":
        s.no_diff_reviewer_dispatched = False
    else:
        _reset_doc(s)


# Infra failure classes the runtime digests itself (bounded re-dispatch with
# executor backoff): the opencode process was killed / timed out, the binary
# or provider is unavailable. These describe the machine, not the agent, so
# they never consume the attempt budget or overwrite last_failure (run
# 01KZTHE7 T-017: "killed by signal 9" evidence kept overwriting Prism's
# diagnosis and 3 infra failures exhausted the agent budget).
_INFRA_FAILURE_CLASSES = frozenset(
    {
        "signal",
        "opencode_missing",
        "provider_unavailable",
        "timeout",
        "abnormal_step_finish",
        # OOB 2026-09-05 (run 01M19FJVES7G113RD8QXXY3PQZ): opencode exiting 1
        # is a process-level failure (CLI crash, provider 400/5xx rejected the
        # request, gateway misconfig) -- machine-side, never agent semantics.
        # Classified as semantic it burned a Devon attempt + a DIAGNOSE round
        # on a deterministic APIError (which the DIAGNOSE vocabulary cannot
        # even name). Infra: bounded backoff re-dispatch, no attempt, no
        # DIAGNOSE detour.
        "non_zero_exit",
    }
)
_INFRA_RETRY_LIMIT = 3


# Output-contract format failures (operator 2026-08-24): the agent ran and
# produced artifacts, but its final reply failed a mechanical manifest check
# (shape/length/required fields). Distinct from infra (machine) and from
# semantic failures (assignment quality): re-dispatch immediately without
# burning the attempt budget -- the same agent session can fix its own
# reply shape -- but escalate after _FORMAT_RETRY_LIMIT consecutive ones so
# a loop of malformed replies parks for a human instead of spinning.
_FORMAT_FAILURE_CLASSES = frozenset({"manifest_malformed", "evidence_malformed"})
_FORMAT_RETRY_LIMIT = 3


def _is_format_verdict(p: dict) -> bool:
    return (p.get("failure_class") or p.get("check") or "") in _FORMAT_FAILURE_CLASSES


_FORMAT_VERDICT_ROUTE = {
    "RED_GATE": "RED",
    "RED_CHECKPOINT": "RED",
    "GREEN_GATE": "GREEN",
    "GREEN_COMMIT": "GREEN",
    "REFACTOR_GATE": "REFACTOR",
}


def _handle_verdict_format_failure(s: State, p: dict) -> None:
    fmt = p.get("failure_class") or p.get("check") or "evidence_malformed"
    s.format_failure_streak += 1
    s.last_failure = {
        "check": fmt,
        "reason": p.get("reason"),
        "evidence": p.get("evidence"),
    }
    target = _FORMAT_VERDICT_ROUTE.get(s.substate or "")
    if target is not None:
        s.substate = target
        _reset_doc(s)
    elif s.substate in _M_IMPL_REVIEW_SUBSTATES or s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    if s.format_failure_streak >= 5:
        # Terminal escape (M1-S3): even a simplified contract did not
        # stop the shape violations -- the writer model cannot comply.
        # Human stays the terminal escape, never the first.
        s.status = "awaiting_human"
        s.awaiting = "escalation"
        return
    if s.stage == "M-IMPL" and s.format_failure_streak >= 2:
        # M1-S3 (convergence plan 2026-09-05): two consecutive shape
        # violations mean the envelope contract itself overstrains the
        # writer -- route the contract authority (Archer RULING) for a
        # simplification delta instead of a third agent retry.
        s.substate = "RULING"
        _reset_review(s)
        _reset_doc(s)
        return
    if s.format_failure_streak >= _FORMAT_RETRY_LIMIT:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _is_infra_failure(p: dict) -> bool:
    return (p.get("failure_class") or "agent_error") in _INFRA_FAILURE_CLASSES


def _handle_infra_failure(s: State) -> None:
    """Digest an infrastructure failure without touching agent semantics:
    no attempt consumed, last_failure untouched, substate kept. Reset the
    dispatch flag so decide() re-issues the same assignment; cap consecutive
    infra failures to avoid retry-storming a degraded gateway (backlog:
    provider-failure retry storm, 2026-08-16)."""
    s.infra_failure_streak += 1
    if s.substate in _M_IMPL_REVIEW_SUBSTATES or s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    if s.infra_failure_streak >= _INFRA_RETRY_LIMIT:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _handle_format_failure(s: State, p: dict) -> None:
    """Digest an output-contract format failure (manifest_malformed) without
    burning the semantic attempt budget: the reply failed a mechanical shape
    check, so the assignment's semantics were never evaluated. Unlike infra,
    the evidence IS carried to the re-dispatch (last_failure: the agent must
    fix exactly this shape defect, FR-11) and infra_failure_streak is left
    alone (backoff is for degraded gateways; the session-reused agent
    retries immediately). Bounded: >=_FORMAT_RETRY_LIMIT in a row escalates."""
    s.format_failure_streak += 1
    s.last_failure = {
        "check": p.get("failure_class") or "manifest_malformed",
        "reason": p.get("self_report"),
        "evidence": p.get("audit_evidence") or p.get("artifact_ref"),
    }
    if s.substate in _M_IMPL_REVIEW_SUBSTATES or s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    if s.format_failure_streak >= 5:
        # Terminal escape (M1-S3): even a simplified contract did not
        # stop the shape violations -- the writer model cannot comply.
        # Human stays the terminal escape, never the first.
        s.status = "awaiting_human"
        s.awaiting = "escalation"
        return
    if s.stage == "M-IMPL" and s.format_failure_streak >= 2:
        # M1-S3 (convergence plan 2026-09-05): two consecutive shape
        # violations mean the envelope contract itself overstrains the
        # writer -- route the contract authority (Archer RULING) for a
        # simplification delta instead of a third agent retry.
        s.substate = "RULING"
        _reset_review(s)
        _reset_doc(s)
        return
    if s.format_failure_streak >= _FORMAT_RETRY_LIMIT:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _handle_failed_outcome(s: State, p: dict) -> None:
    """FR-0210: a failed dispatch_agent outcome consumes an attempt from the
    same accounting as verdict.failed, carries failure evidence into the
    re-dispatch prompt (FR-11), and escalates to awaiting_human at the 3rd
    attempt. run048: None/absent/unknown status is treated as ``failed`` with
    ``agent_error`` so the 3-attempt escalation evidence stays meaningful."""
    if (p.get("failure_class") or "agent_error") in _FORMAT_FAILURE_CLASSES:
        _handle_format_failure(s, p)
        return
    if _is_infra_failure(p):
        _handle_infra_failure(s)
        return
    s.infra_failure_streak = 0
    s.format_failure_streak = 0
    s.last_failure = {
        "check": p.get("failure_class") or "agent_error",
        "reason": p.get("self_report"),
        "evidence": p.get("audit_evidence") or p.get("artifact_ref"),
    }
    if _route_failed_outcome_by_stage(s, p):
        return
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    _consume_attempt(s)


def _route_failed_outcome_by_stage(s: State, p: dict) -> bool:
    """Route a failed outcome by stage. Returns True if handled."""
    if s.stage == "M-HOTFIX-TRIAGE":
        _handle_hotfix_failed_outcome(s, p)
        return True
    if s.stage == "M-TEST":
        # D-32/SM-01.12: an invalid M-DESIGN test-task contract surfaces as a
        # stub_gap failed outcome (emitted by the executor instead of calling
        # the backend). It routes straight to DIAGNOSE/stub_gap — no attempt
        # consumed, no Human, no three wasted Shield attempts — and decide()
        # rolls back to M-DESIGN.
        if p.get("failure_class") == "stub_gap":
            _reset_doc(s)
            s.diagnose_classification = "stub_gap"
            s.substate = "DIAGNOSE"
            return True
        _reset_m_test_dispatch_flag(s)
        _consume_attempt(s)
        return True
    if s.stage == "M-IMPL":
        if (
            p.get("role") == "devon"
            and p.get("status") == "failed"
            and s.substate in ("RED", "GREEN")
        ):
            # A backend execution failure has unknown attribution. Preserve its
            # evidence, spend the same bounded attempt, and let Prism diagnose
            # it instead of blindly repeating the failed phase.
            _reset_doc(s)
            _reset_review(s)
            s.diagnose_classification = None
            s.substate = "DIAGNOSE"
            _consume_attempt(s)
            return True
        if s.substate in _M_IMPL_REVIEW_SUBSTATES:
            _reset_review(s)
        else:
            _reset_doc(s)
        _consume_attempt(s)
        return True
    return False


def _handle_hotfix_failed_outcome(s: State, p: dict) -> None:
    """SM-01.5-.7: a failed Sage dispatch outcome or anchor_invalid verdict in
    SAGE_TRIAGE consumes the shared <=3 attempt budget, resets the dispatcher
    flag so decide() re-dispatches Sage, and parks at AWAIT_HUMAN on the 3rd
    failure (never auto-feature-routes, NFR-0100-03)."""
    _reset_doc(s)
    s.current_attempt = int(p.get("attempt", s.current_attempt + 1))
    if s.current_attempt >= 3:
        s.substate = "AWAIT_HUMAN"
        s.status = "awaiting_human"
        s.awaiting = "hotfix_triage"
    else:
        # Redispatch Sage with fresh doc flags — _reset_doc already cleared the
        # dispatcher flags so decide() issues a fresh dispatch (SM-01.6).
        s.substate = "SAGE_TRIAGE"
