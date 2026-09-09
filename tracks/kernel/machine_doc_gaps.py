"""SM-02 doc-gap adjudication projection (IF-DOCGAP-001 / IF-QUARANTINE-001):
the per-record waiting/route/quarantine/resume state rebuilt from the
doc-gap event closed set, including the design_gap nested Archer/Prism
revision workflow (#62).

Extracted from ``machine.py`` for module-size compliance (C0302).

No circular import: imports only ``events`` at runtime; ``State`` is
imported under ``TYPE_CHECKING`` only (duck-typed at runtime).
``machine.py`` imports the reducers from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import EventEnvelope

if TYPE_CHECKING:
    from .machine import State


def _new_doc_gap_record(
    origin: dict | None = None,
    document_paths: list | None = None,
    thread_ids: list | None = None,
) -> dict:
    """Fresh waiting record shape for the doc-gap projection (SM-02).

    Shared by the ``doc_comment.detected`` and ``outcome.quarantined``
    reducers; every doc-gap record starts in the waiting DETECTED state with
    empty route/quarantine/resume fields.
    """
    return {
        "state": "DETECTED",
        "origin": dict(origin or {}),
        "document_paths": list(document_paths or []),
        "thread_ids": list(thread_ids or []),
        "quarantine_id": None,
        "quarantine_status": None,
        "manifest_ref": None,
        "route": None,
        "responsible_role": None,
        "decision_ref": None,
        "reason": None,
        "next_dispatch_id": None,
        "next_attempt": None,
        # SM-02 design_gap nested workflow (#62 finding 1): sub-progress of
        # the DESIGN_GAP state toward READY_TO_RESUME.  None = not started
        # (or agent_correction, which has no nested workflow).  The resume
        # gate requires "prism_reviewed" before thread resolution may drive
        # a new attempt, so an Archer revision that resolves threads early
        # never short-circuits past Prism's review.
        "revision": None,
        "design_attempts": 0,
        "review_attempts": 0,
        "design_dispatch_id": None,
        "revised_design_identity": None,
        "prism_verdict": None,
        # SM-02 (#62 finding 2): pre-dispatch content-identity snapshot of the
        # record's adjudicated design docs, captured in ``_dispatch_doc_gap_agent``
        # right before the nested Archer acts.  The checkpoint stages ONLY the
        # document_paths whose identity drifted from this snapshot, so Human /
        # pre-dirty / unattributed design changes are never committed as Archer
        # output (AC-FR0236-01 attribution).
        "pre_dispatch_identities": None,
    }


def _on_doc_comment_detected(s: State, p: dict, ev: EventEnvelope) -> None:
    """``doc_comment.detected``: open a per-record waiting projection
    (AC-FR0234-03).  The origin role/task/phase/dispatch/attempt and the
    discussion threads stay visible while the outcome is paused."""
    s.doc_gaps[p["record_id"]] = _new_doc_gap_record(
        origin=p.get("origin"),
        document_paths=p.get("document_paths"),
        thread_ids=p.get("thread_ids"),
    )


def _on_outcome_quarantined(s: State, p: dict, ev: EventEnvelope) -> None:
    """``outcome.quarantined``: surface the quarantine identity and status so a
    held outcome is never mistaken for a success (AC-FR0236-01/02).  The record
    is created when the quarantined event is the first trace of the lifecycle
    (blob-manifest round trip), always in the waiting DETECTED state."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        record = _new_doc_gap_record()
        s.doc_gaps[p["record_id"]] = record
    record["quarantine_id"] = p.get("quarantine_id")
    record["quarantine_status"] = p.get("status")
    # Persisted manifest reference: the resume decision rebuilds the
    # quarantine descriptor from this blob (decide_quarantine_resume).
    record["manifest_ref"] = p.get("manifest_ref")


def _on_doc_comment_adjudicated(s: State, p: dict, ev: EventEnvelope) -> None:
    """``doc_comment.adjudicated``: Prism's route moves the record to
    DESIGN_GAP or AGENT_CORRECTION (AC-FR0235-01/02).

    The accepted decision identity (decision_ref, responsible_role) is
    projected for replay audit.  The FIRST decision stands: an identical
    replay is an idempotent no-op and a conflicting re-adjudication (same
    record, different decision_ref) is rejected fail-closed — the record
    never flips routes after adjudication."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        return
    if record.get("decision_ref") is not None:
        return
    route = p.get("route")
    record["route"] = route
    record["responsible_role"] = p.get("responsible_role")
    record["decision_ref"] = p.get("decision_ref")
    if route == "design_gap":
        record["state"] = "DESIGN_GAP"
    elif route == "agent_correction":
        record["state"] = "AGENT_CORRECTION"


def _on_outcome_resolve(s: State, p: dict, ev: EventEnvelope) -> None:
    """``outcome.restored|discarded``: project the resume decision with its
    reason and the new dispatch/attempt (AC-FR0236-04)."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        return
    record["state"] = "RESTORED" if ev.type == "outcome.restored" else "DISCARDED"
    record["reason"] = p.get("reason")
    record["next_dispatch_id"] = p.get("next_dispatch_id")
    record["next_attempt"] = p.get("next_attempt")


def _on_outcome_resumed(s: State, p: dict, ev: EventEnvelope) -> None:
    """``outcome.resumed``: the old outcome is superseded by a NEW dispatch/
    attempt, never re-marked as a success (AC-FR0235-03, NFR-0090-03)."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        return
    record["state"] = "RESUMED"
    record["next_dispatch_id"] = p.get("next_dispatch_id")
    record["next_attempt"] = p.get("next_attempt")


def _on_doc_gap_design_dispatched(s: State, p: dict, ev: EventEnvelope) -> None:
    """``doc_gap.design_dispatched``: the nested Archer revision or Prism
    review dispatch was issued for a DESIGN_GAP record (#62 finding 1).

    The dispatch_id binds the WAL command.issued to the record's revision
    sub-progress so replay can verify the nested workflow actually ran."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        return
    phase = p.get("phase")
    record["design_dispatch_id"] = p.get("dispatch_id")
    # SM-02 (#62 finding 2): persist the nested Archer pre-dispatch identity
    # snapshot so the checkpoint can stage only identity-changed document_paths
    # (design_revision phase only; design_review carries no design write).
    if phase == "design_revision":
        record["revision"] = "archer_dispatched"
        record["design_attempts"] = int(record.get("design_attempts") or 0) + 1
        if p.get("pre_dispatch_identities") is not None:
            record["pre_dispatch_identities"] = p.get("pre_dispatch_identities")
    elif phase == "design_review":
        record["revision"] = "prism_dispatched"


def _on_doc_gap_design_revised(s: State, p: dict, ev: EventEnvelope) -> None:
    """``doc_gap.design_revised``: Archer's revision was checkpointed (#62
    finding 1).  The revised combined design identity is projected for the
    resume decision's design-stale check: the live identity will differ from
    the quarantine's pause-time anchor, so held成果 is discarded."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        return
    record["revision"] = "design_revised"
    record["revised_design_identity"] = p.get("revised_design_identity")


def _on_doc_gap_design_failed(s: State, p: dict, ev: EventEnvelope) -> None:
    """Retry the failed nested phase at most three times, then park."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is not None:
        record["reason"] = p.get("reason")
        if p.get("phase") == "design_review":
            record["review_attempts"] = int(record.get("review_attempts") or 0) + 1
            record["revision"] = (
                "design_revised" if record["review_attempts"] < 3 else "design_failed"
            )
        else:
            record["revision"] = (
                None if int(record.get("design_attempts") or 0) < 3 else "design_failed"
            )


def _on_doc_gap_design_reviewed(s: State, p: dict, ev: EventEnvelope) -> None:
    """``doc_gap.design_reviewed``: Prism reviewed the revised design (#62
    finding 1).  On pass the record reaches prism_reviewed and the resume
    gate may proceed once threads close.  On revise the revision resets to
    None so the run loop re-dispatches Archer for another revision round —
    both are deterministic under replay."""
    record = s.doc_gaps.get(p.get("record_id"))
    if record is None:
        return
    verdict = p.get("verdict")
    record["prism_verdict"] = verdict
    if verdict == "pass":
        record["revision"] = "prism_reviewed"
    else:
        record["revision"] = (
            None if int(record.get("design_attempts") or 0) < 3 else "design_failed"
        )
        if record["revision"] == "design_failed":
            record["reason"] = "Prism design review budget exhausted"
