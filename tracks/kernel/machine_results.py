"""v0.5 ResultCheckpoint pipeline reducers (result.submitted/validated/
checkpointed) and the no_diff peer-review reducers (no_diff.detected/
explained/reviewed).

Extracted from ``machine.py`` for module-size compliance (C0302).

No circular import: imports only ``events``, ``m_impl``/``m_test``
helpers, ``machine_verdicts`` escalation and ``stage_registry`` at
runtime; ``State`` is imported under ``TYPE_CHECKING`` only (duck-typed
at runtime). ``machine.py`` imports the reducers from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import EventEnvelope
from .m_impl import _on_m_impl_verdict_failed
from .m_test import _on_m_test_verdict_failed, _reset_doc, _reset_review
from .machine_verdicts import _escalate_or_continue, _on_design_verdict_failed
from .stage_registry import _REVIEW_SUBSTATE

if TYPE_CHECKING:
    from .machine import State


# -- v0.5 ResultCheckpoint pipeline reducers (batch 1: M-STORY/M-SPEC/M-ACC) ---


def _on_result_submitted(s: State, p: dict, ev: EventEnvelope) -> None:
    """Capture a result into the pipeline. The payload carries everything the
    pipeline needs: source actor, stage/substate, artifacts to validate,
    allowed_paths to checkpoint, base_sha, checks, domain_event to publish,
    actor_kind (agent|human), commit_label for checkpoint commits.

    v0.5 review-A: result_id (stable audit identity), digests (artifact sha256
    at capture time, re-verified at checkpoint), forbid_diff (no-comment gate),
    discussion_only (reviewer diff must be canonical discussion change)."""
    s.active_result = dict(p)
    s.pending = None


def _on_result_validated(s: State, p: dict, ev: EventEnvelope) -> None:
    """Validation passed; mark the active result as validated. For author
    (DRAFT/RESPOND) results, also set doc_validated so decide() never re-issues
    validate_document after the pipeline completes."""
    s.pending = None
    if s.active_result is None:
        return
    s.active_result["validated"] = True
    if s.active_result.get("substate") in ("DRAFT", "RESPOND"):
        s.doc_validated = True


def _on_result_checkpointed(s: State, p: dict, ev: EventEnvelope) -> None:
    """Checkpoint committed (or no-change); mark the active result."""
    s.pending = None
    if s.active_result is None:
        return
    s.active_result["checkpointed"] = True
    s.active_result["commit_sha"] = p.get("commit_sha")
    s.active_result["created_commit"] = p.get("created_commit", False)


# -- v0.5 no_diff peer review reducers --------------------------------------
# requires_diff on an author result with no diff emits no_diff.detected.
# Flow: NO_DIFF_EXPLAIN -> NO_DIFF_REVIEW -> (pass: resume pipeline) |
# (revise: verdict.failed(no_diff_justified)). active_result is preserved
# across the review so the pipeline can resume after a pass.


def _on_no_diff_detected(s: State, p: dict, ev: EventEnvelope) -> None:
    s.pending = None
    if s.active_result is not None:
        s.active_result["no_diff_origin_substate"] = s.substate
    s.substate = "NO_DIFF_EXPLAIN"
    s.doc_dispatched = False  # re-dispatch author for the explanation
    s.no_diff_explanation = None
    s.no_diff_reviewer_dispatched = False
    # active_result is NOT cleared — pipeline resumes after review pass.


def _on_no_diff_explained(s: State, p: dict, ev: EventEnvelope) -> None:
    s.pending = None
    s.no_diff_explanation = p.get("explanation", "")
    s.substate = "NO_DIFF_REVIEW"
    s.no_diff_reviewer_dispatched = False  # dispatch reviewer


def _on_no_diff_reviewed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.pending = None
    verdict = p.get("verdict")
    if verdict == "pass":
        # Pipeline resumes: mark validated AND no_diff-approved so the
        # checkpoint's requires_diff gate is skipped.
        if s.active_result is not None:
            s.active_result["validated"] = True
            s.active_result["no_diff_approved"] = True
            s.substate = s.active_result.pop("no_diff_origin_substate", "DRAFT")
        s.no_diff_explanation = None
        s.no_diff_reviewer_dispatched = False
        return
    # Reviewer rejected: route through the stage's verdict.failed handler.
    origin = s.active_result.pop("no_diff_origin_substate", "DRAFT") if s.active_result else "DRAFT"
    explanation = s.no_diff_explanation
    s.active_result = None
    s.substate = origin
    s.no_diff_explanation = None
    s.no_diff_reviewer_dispatched = False
    s.last_failure = {
        "check": "no_diff_justified",
        "reason": "reviewer rejected no-diff explanation",
        "evidence": explanation,
        "attempt": p.get("attempt"),
    }
    if s.stage == "M-TEST":
        _on_m_test_verdict_failed(s, {"check": "no_diff_justified", **p})
        return
    if s.stage == "M-IMPL":
        _on_m_impl_verdict_failed(s, {"check": "no_diff_justified", **p})
        return
    if s.stage == "M-DESIGN":
        _on_design_verdict_failed(s, {"check": "no_diff_justified", **p})
        return
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    _escalate_or_continue(s, {"attempt": p.get("attempt", s.current_attempt + 1)})
