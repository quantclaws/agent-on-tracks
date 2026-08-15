"""E-04 operator happy path for design discussion adjudication and resume.

This e2e test covers the user-facing path where a design discussion is added,
Prism adjudicates, quarantine is held, and the outcome resumes after the
thread closes.  All assertions land on public CLI/event/file observable
outlets (interfaces.md §2e/§4e).

Scenario construction (PRISM-V05-R2-02 revision): the design discussion is
applied by the dispatched Shield agent INSIDE the dispatch window
(``tests/doc_gap_injection.arm_doc_delta`` wraps the fake agent seam) — a
pre-dispatch write would only move the document baseline and could never
trigger detection.  After the journey the test asserts the document exists
AND carries the delta.  Since the IF-DOCGAP-001/IF-QUARANTINE-001 stubs are
not yet wired into the Runtime, the expected doc-comment events do not
appear in the event stream — legal Red.
"""

import pytest

from tests.doc_gap_injection import TRACKS_DOC_PLAN, arm_doc_delta, assert_doc_delta_landed
from tests.e2e.helpers import walk_to_m_test_complete


@pytest.mark.e2e
# AC-FR0234-03@v0.5 TRACKS-TRACE status/discuss/replay/report shows waiting/origin/quarantine
# AC-FR0235-01@v0.5 TRACKS-TRACE design gap routes Archer without human gate
# AC-FR0235-03@v0.5 TRACKS-TRACE closed thread creates new attempt not old outcome success
def test_design_discussion_adjudication_resume_happy_path(trac, event_log, host_repo, monkeypatch):
    """Verify the E-04 operator happy path (legal Red).

    The dispatched Shield agent appends a legal design discussion to
    test-plan.md (its permitted document) inside the dispatch window; the
    test then walks the public journey.  The doc-comment flow is not yet
    wired; the test asserts that the expected doc-comment events appear in
    the public event stream, but the runtime never emits them — legal Red.
    Once wired, this test will:
    1. Observe the outcome paused with 'outcome paused: design discussion'
    2. Verify trac status shows doc-gap=awaiting-adjudication
    3. Verify trac discuss query shows the new thread
    4. Verify trac replay shows detected->adjudicated->quarantined->restored
    5. Verify the resumed dispatch/attempt uses a new attempt number
    """
    discussion = "\n\n> **Shield:** Design discussion for the E-04 happy path.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test_complete(trac, version="v0.5")
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)
    events = event_log(run_id)

    # Assert that the doc-comment events are present in the event stream.
    # The runtime never emits them — legal Red.
    detected = [
        e for e in events
        if e["type"] == "doc_comment.detected"
    ]
    adjudicated = [
        e for e in events
        if e["type"] == "doc_comment.adjudicated"
    ]
    quarantined = [
        e for e in events
        if e["type"] == "outcome.quarantined"
    ]
    restored = [
        e for e in events
        if e["type"] == "outcome.restored"
    ]
    resumed = [
        e for e in events
        if e["type"] == "outcome.resumed"
    ]
    assert len(detected) > 0, (
        "expected doc_comment.detected for design discussion"
    )
    assert len(adjudicated) > 0, (
        "expected doc_comment.adjudicated after Prism review"
    )
    assert len(quarantined) > 0, (
        "expected outcome.quarantined for design gap"
    )
    assert len(restored) + len(resumed) > 0, (
        "expected outcome.restored/resumed after thread close"
    )
