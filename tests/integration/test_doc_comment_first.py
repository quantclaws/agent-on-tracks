"""Doc-comment-first outcome integration tests (FR-0234/FR-0235/FR-0237).

All assertions land on public observable outlets (interfaces.md §4e):
events table doc_comment.detected, doc_comment.adjudicated,
outcome.quarantined, outcome.rejected, and the CLI (replay) text surface.
The doc-comment-first flow is not yet wired into the runtime
(IF-DOCGAP-001/IF-QUARANTINE-001 stubs), producing a legal Red at the
event-observation layer.

Scenario construction (PRISM-V05-R2-02 revision): every positive test arms
``tests/doc_gap_injection.arm_doc_delta`` so the delta (legal discussion
blockquote, illegal body edit, or resolved thread) is applied by the
dispatched Shield agent INSIDE the dispatch window — after the pre-dispatch
document-identity snapshot, before outcome validation.  A pre-dispatch write
would only move the baseline and could never trigger detection.  After the
journey each test asserts the document exists AND carries the delta
(``assert_doc_delta_landed``), replacing the earlier silent ``exists()``
guards with a real scenario-faithfulness check.
"""

import pytest

from tests.doc_gap_injection import TRACKS_DOC_PLAN, arm_doc_delta, assert_doc_delta_landed
from tests.integration.v05_contract_helpers import (
    command_dispatches,
    events_of,
    run_m_impl_journey,
)


@pytest.mark.integration
# AC-FR0234-01@v0.5 TRACKS-TRACE legal discussion pauses before all ordinary validation
def test_legal_discussion_pauses_before_all_ordinary_validation(trac, event_log, host_repo, monkeypatch):
    """Verify that the runtime emits doc_comment.detected for a legal discussion.

    Constructs the scenario: the dispatched Shield agent appends a legal
    discussion blockquote to test-plan.md as part of its own work (in-window
    delta).  The doc-comment flow is not yet wired; the test asserts that
    doc_comment.detected appears in the event stream, but the runtime never
    emits it — legal Red.
    """
    discussion = "\n\n> **Shield:** Legal discussion injected inside the dispatch window.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    detected = events_of(events, "doc_comment.detected")
    assert len(detected) > 0, (
        "expected doc_comment.detected event after dispatch with legal discussion"
    )
    # §1m: doc_comment.detected carries document_paths for the touched docs.
    with_paths = [e for e in detected if e["payload"].get("document_paths")]
    assert with_paths, (
        "expected doc_comment.detected payload to carry non-empty document_paths"
    )


@pytest.mark.integration
# AC-FR0234-02@v0.5 TRACKS-TRACE role comment scope and pre-dispatch threads do not retrigger
def test_role_comment_scope_and_predispatch_threads_do_not_retrigger(trac, event_log, host_repo, monkeypatch):
    """Verify doc_comment.detected respects role scope and baseline threads.

    Constructs the scenario: a PRE-DISPATCH thread lands at the design seam
    (part of the document baseline the outcome is diffed against) plus an
    in-window new discussion by the dispatched Shield agent on a
    role-permitted document.  The pre-dispatch thread must NOT retrigger
    detection (interfaces.md §1k: baseline discussions do not re-trigger the
    pause); only the in-window discussion does — so exactly one
    doc_comment.detected event may appear.  The flow is not wired, so no
    detection occurs — legal Red.
    """
    prediscussion = "\n\n> **Prism [RESOLVED]:** Pre-existing resolved thread.\n"
    new_discussion = "\n\n> **Shield:** New in-window discussion on a role-permitted document.\n"
    arm_doc_delta(
        monkeypatch,
        path=TRACKS_DOC_PLAN,
        text=new_discussion,
        baseline={"path": TRACKS_DOC_PLAN, "text": prediscussion},
    )

    _, _, events = run_m_impl_journey(trac, event_log)
    doc = assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, new_discussion)
    content = doc.read_text(encoding="utf-8")
    assert prediscussion in content, (
        "pre-dispatch baseline thread must be present in the document "
        "(baseline applied at the design seam)"
    )

    detected = events_of(events, "doc_comment.detected")
    assert len(detected) == 1, (
        "expected exactly one doc_comment.detected: the in-window discussion; "
        "pre-dispatch baseline threads must not retrigger detection"
    )


@pytest.mark.integration
# AC-FR0234-04@v0.5 TRACKS-TRACE outcome without new discussion uses original validation path
def test_outcome_without_new_discussion_uses_original_validation_path(trac, event_log):
    """Verify the ordinary validation path proceeds when no discussion (legal Red).

    This test does NOT arm any doc delta — the plain journey should proceed
    through the original validation path WITHOUT triggering doc_comment_first.
    The doc-comment gate is not wired, so the test asserts that the event
    stream contains outcome.received events (ordinary path) and NO
    doc_comment.detected events.
    """
    _, _, events = run_m_impl_journey(trac, event_log)
    received = events_of(events, "outcome.received")
    detected = events_of(events, "doc_comment.detected")
    assert len(received) > 0, "expected outcome.received for original path"
    # Without a new discussion, doc_comment.detected must NOT appear.
    # The doc-comment gate is not wired, so no detection occurs — legal Red
    # if the assertion is inverted (detected > 0 would be wrong).
    assert len(detected) == 0, (
        "expected NO doc_comment.detected for outcome without new discussion; "
        "AC-FR0234-04 requires the original validation path"
    )


@pytest.mark.integration
# AC-FR0235-01@v0.5 TRACKS-TRACE design gap routes Archer without human gate
def test_design_gap_routes_archer_without_human_gate(trac, event_log, host_repo, monkeypatch):
    """Verify that a design-gap adjudication emits doc_comment.adjudicated (legal Red).

    Constructs the scenario: the dispatched Shield agent appends a legal
    design-gap discussion to test-plan.md inside the dispatch window.  The
    doc-comment flow is not yet wired; the test asserts that
    doc_comment.adjudicated(design_gap) appears in the event stream and that
    no human gate event occurs after detection (FR-0235-01: no Human
    technical gate), but the runtime never emits it — legal Red.
    """
    discussion = "\n\n> **Shield:** Design gap discussion requiring Archer routing.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    adjudicated = events_of(events, "doc_comment.adjudicated")
    design_gaps = [
        e for e in adjudicated
        if e["payload"].get("route") == "design_gap"
    ]
    assert len(design_gaps) > 0, (
        "expected doc_comment.adjudicated(design_gap) event"
    )
    for e in design_gaps:
        assert e["payload"].get("responsible_role"), (
            "§1m: doc_comment.adjudicated(design_gap) must carry responsible_role"
        )
    # FR-0235-01: routing to Archer happens WITHOUT a Human technical gate —
    # no human.* event may follow the first detection.
    detected = events_of(events, "doc_comment.detected")
    if detected:
        first_seq = detected[0]["seq"]
        human_after = [
            e for e in events
            if e["type"].startswith("human.") and e["seq"] > first_seq
        ]
        assert not human_after, (
            f"design-gap routing must not require a human gate: {human_after}"
        )


@pytest.mark.integration
# AC-FR0235-02@v0.5 TRACKS-TRACE agent correction stays paused until original thread closes
def test_agent_correction_stays_paused_until_original_thread_closes(trac, event_log, host_repo, monkeypatch):
    """Verify agent-correction adjudication keeps the outcome paused (legal Red).

    Constructs the scenario: the dispatched Shield agent appends a legal
    discussion that belongs to the agent-correction route.  The discussion
    thread is never closed in this test, so the outcome must stay paused:
    no outcome.restored/discarded/resumed may appear.  The flow is not wired;
    the test asserts doc_comment.adjudicated(agent_correction) appears, but
    the runtime never emits it — legal Red.
    """
    discussion = "\n\n> **Shield:** Agent-correction guidance for the original agent.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    adjudicated = events_of(events, "doc_comment.adjudicated")
    agent_corrections = [
        e for e in adjudicated
        if e["payload"].get("route") == "agent_correction"
    ]
    assert len(agent_corrections) > 0, (
        "expected doc_comment.adjudicated(agent_correction) event"
    )
    # The thread is never closed: the outcome must stay paused (no
    # restore/discard/resume may fire before thread close).
    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    resumed = events_of(events, "outcome.resumed")
    assert len(restored) + len(discarded) + len(resumed) == 0, (
        "outcome must stay paused until the original thread closes "
        "(FR-0235-02)"
    )


@pytest.mark.integration
# AC-FR0235-03@v0.5 TRACKS-TRACE closed thread creates new attempt not old outcome success
def test_closed_thread_creates_new_attempt_not_old_outcome_success(trac, event_log, host_repo, monkeypatch):
    """Verify that a closed thread yields a new dispatch/attempt (legal Red).

    Constructs the scenario: the dispatched Shield agent appends a discussion
    thread that is born resolved (status on the root line, §1k).  The
    doc-comment flow is not yet wired; the test asserts that the runtime
    emits outcome.restored/discarded/resumed carrying the new dispatch
    identity (next_dispatch_id, next_attempt — §1m), but the runtime never
    emits these — legal Red.
    """
    discussion = "\n\n> **Shield [RESOLVED]:** Resolved thread injected inside the dispatch window.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    resumed = events_of(events, "outcome.resumed")
    terminal = restored + discarded + resumed
    assert len(terminal) > 0, (
        "expected outcome.restored/discarded/resumed for closed thread"
    )
    for e in terminal:
        payload = e["payload"]
        assert payload.get("next_dispatch_id"), (
            "§1m: restored/discarded/resumed must carry next_dispatch_id"
        )
        assert payload.get("next_attempt", 0) >= 1, (
            "§1m: restored/discarded/resumed must carry next_attempt >= 1 — "
            "a closed thread creates a NEW attempt, not old-outcome success"
        )


@pytest.mark.integration
# AC-FR0237-01@v0.5 TRACKS-TRACE illegal body edit atomically rolls back entire outcome
def test_illegal_body_edit_atomically_rolls_back_entire_outcome(trac, event_log, host_repo, monkeypatch):
    """Verify that an illegal body edit produces outcome.rejected (legal Red).

    Constructs the scenario: the dispatched Shield agent appends
    NON-discussion body text to test-plan.md as part of its work — an
    illegal body edit (§1k DocDeltaClass=illegal_body_edit).  The flow is not
    wired; the test asserts that outcome.rejected(over_reach, atomic) with
    the rejected path appears, but the runtime never emits it — legal Red.
    """
    body_edit = "\n\nThis is an illegal body edit injected inside the dispatch window — not a discussion.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=body_edit)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, body_edit)

    rejected = events_of(events, "outcome.rejected")
    assert len(rejected) > 0, (
        "expected outcome.rejected for illegal body edit"
    )
    for e in rejected:
        payload = e["payload"]
        assert payload.get("failure_class") == "over_reach", (
            "§1m: illegal body edit is rejected with failure_class=over_reach"
        )
        assert payload.get("rollback") == "atomic", (
            "§1m: illegal body edit rolls back the entire outcome atomically"
        )


@pytest.mark.integration
# AC-FR0237-02@v0.5 TRACKS-TRACE illegal edit reports paths and redispatches new attempt
def test_illegal_edit_reports_paths_and_redispatches_new_attempt(trac, event_log, host_repo, monkeypatch):
    """Verify that illegal edit events contain rejected_paths (legal Red).

    Constructs the scenario: in-window illegal body edit (as above).  The
    flow is not wired; the test asserts that outcome.rejected carries the
    rejected document path and that a NEW shield dispatch follows the
    rejection, but the runtime never emits these — legal Red.
    """
    body_edit = "\n\nIllegal body edit for path reporting and redispatch, injected in-window.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=body_edit)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, body_edit)

    rejected = events_of(events, "outcome.rejected")
    over_reach = [
        e for e in rejected
        if e["payload"].get("failure_class") == "over_reach"
    ]
    assert len(over_reach) > 0, (
        "expected outcome.rejected(over_reach) for illegal body edit"
    )
    assert any(
        any("test-plan.md" in str(p) for p in e["payload"].get("rejected_paths", []))
        for e in over_reach
    ), "expected the edited document path in rejected_paths"
    # FR-0237-02: after the atomic rollback the same logical role is
    # redispatched on a new attempt — observable as a later shield WRITE
    # dispatch in the public event stream.
    first_reject_seq = over_reach[0]["seq"]
    later_writes = [
        d for d in command_dispatches(events, role="shield", substate="WRITE")
        if d["seq"] > first_reject_seq
    ]
    assert later_writes, (
        "expected a new shield WRITE dispatch after the over_reach rejection"
    )


@pytest.mark.integration
# AC-FR0237-03@v0.5 TRACKS-TRACE illegal edit failure evidence survives replay
def test_illegal_edit_failure_evidence_survives_replay(trac, event_log, host_repo, monkeypatch):
    """Verify that illegal edit failure evidence survives replay (legal Red).

    Constructs the scenario: in-window illegal body edit (as above).  The
    flow is not wired; the test asserts that the rejection evidence is
    present in the append-only event store AND visible in the public
    ``trac replay`` text surface, but the runtime never produces it —
    legal Red.
    """
    body_edit = "\n\nIllegal body edit for replay evidence, injected in-window.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=body_edit)

    run_id, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, body_edit)

    rejected = events_of(events, "outcome.rejected")
    assert len(rejected) > 0, (
        "expected outcome.rejected events that survive replay"
    )
    replay = trac("replay", run_id)
    assert replay.returncode == 0, replay.stderr
    assert "outcome.rejected" in replay.stdout, (
        "rejection evidence must survive replay on the public text surface"
    )
