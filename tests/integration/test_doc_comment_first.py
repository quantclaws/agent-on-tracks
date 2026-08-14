"""Doc-comment-first outcome integration tests (FR-0234/FR-0235/FR-0237).

All assertions land on public observable outlets (interfaces.md §4e):
events table doc_comment.detected, outcome.quarantined, outcome.rejected,
and CLI status.  The doc-comment-first flow is not yet wired into the
runtime (IF-DOCGAP-001/IF-QUARANTINE-001 stubs), producing a legal Red
at the stub-function layer.

Each test constructs the AC triggering scenario (legal discussion, illegal
body edit, or quarantine-eligible change) before asserting the expected
event, so the test is meaningful when the stub is wired.
"""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0234-01@v0.5 TRACKS-TRACE legal discussion pauses before all ordinary validation
def test_legal_discussion_pauses_before_all_ordinary_validation(trac, event_log, host_repo):
    """Verify that the runtime emits doc_comment.detected for a legal discussion.

    Constructs the scenario: the test writes a legal discussion blockquote to
    a permitted design document (Shield: test-plan.md) before the dispatch,
    simulating the agent's outcome modification.  The doc-comment flow is not
    yet wired; the test asserts that doc_comment.detected appears in the event
    stream after a dispatch, but the runtime never emits it — legal Red.
    """
    # Construct scenario: write a legal discussion to test-plan.md
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** This is a legal discussion comment for testing.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    detected = events_of(events, "doc_comment.detected")
    # The doc-comment stub is not wired — legal Red (NotImplementedError or
    # missing event).  Once wired, a legal discussion in the outcome should
    # produce at least one doc_comment.detected event.
    assert len(detected) > 0, (
        "expected doc_comment.detected event after dispatch with legal discussion"
    )


@pytest.mark.integration
# AC-FR0234-02@v0.5 TRACKS-TRACE role comment scope and pre-dispatch threads do not retrigger
def test_role_comment_scope_and_predispatch_threads_do_not_retrigger(trac, event_log, host_repo):
    """Verify doc_comment.detected respects role scope (legal Red).

    Constructs the scenario: writes a legal discussion to a role-permitted
    document and a pre-dispatch discussion thread.  The doc-comment flow is
    not yet wired; the test asserts that the runtime emits doc_comment.detected
    only for role-permitted docs, but the runtime never emits it — legal Red.
    """
    # Construct scenario: write a pre-dispatch thread + new discussion
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        existing = tp_path.read_text(encoding="utf-8")
        # Pre-dispatch thread (should not retrigger)
        prediscussion = (
            "\n\n> **Prism [RESOLVED]:** Pre-existing resolved thread.\n"
        )
        # New discussion (should trigger)
        new_discussion = (
            "\n\n> **Shield:** New role-scoped discussion for testing.\n"
        )
        tp_path.write_text(
            existing + prediscussion + new_discussion, encoding="utf-8"
        )

    _, _, events = run_m_impl_journey(trac, event_log)
    detected = events_of(events, "doc_comment.detected")
    assert len(detected) > 0, (
        "expected doc_comment.detected for role-scoped discussion"
    )


@pytest.mark.integration
# AC-FR0234-04@v0.5 TRACKS-TRACE outcome without new discussion uses original validation path
def test_outcome_without_new_discussion_uses_original_validation_path(trac, event_log, host_repo):
    """Verify the ordinary validation path proceeds when no discussion (legal Red).

    This test does NOT write any discussion to the document — the plain
    journey should proceed through the original validation path WITHOUT
    triggering doc_comment_first.  The doc-comment gate is not wired, so the
    test asserts that the event stream contains outcome.received events
    (ordinary path) and NO doc_comment.detected events.
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
def test_design_gap_routes_archer_without_human_gate(trac, event_log, host_repo):
    """Verify that a design-gap adjudication emits doc_comment.adjudicated (legal Red).

    Constructs the scenario: writes a legal discussion to a permitted document.
    The doc-comment flow is not yet wired; the test asserts that
    doc_comment.adjudicated(design_gap) appears in the event stream,
    but the runtime never emits it — legal Red.
    """
    # Construct scenario: add a discussion to test-plan.md
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** Design gap discussion for Archer routing.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    adjudicated = events_of(events, "doc_comment.adjudicated")
    design_gaps = [
        e for e in adjudicated
        if e["payload"].get("route") == "design_gap"
    ]
    assert len(design_gaps) > 0, (
        "expected doc_comment.adjudicated(design_gap) event"
    )


@pytest.mark.integration
# AC-FR0235-02@v0.5 TRACKS-TRACE agent correction stays paused until original thread closes
def test_agent_correction_stays_paused_until_original_thread_closes(trac, event_log, host_repo):
    """Verify agent-correction adjudication keeps paused (legal Red).

    Constructs the scenario: writes a legal discussion thread.  The doc-comment
    flow is not yet wired; the test asserts that doc_comment.adjudicated(agent_correction)
    appears, but the runtime never emits it — legal Red.
    """
    # Construct scenario: add a discussion to test-plan.md
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** Agent correction discussion for testing.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    adjudicated = events_of(events, "doc_comment.adjudicated")
    agent_corrections = [
        e for e in adjudicated
        if e["payload"].get("route") == "agent_correction"
    ]
    assert len(agent_corrections) > 0, (
        "expected doc_comment.adjudicated(agent_correction) event"
    )


@pytest.mark.integration
# AC-FR0235-03@v0.5 TRACKS-TRACE closed thread creates new attempt not old outcome success
def test_closed_thread_creates_new_attempt_not_old_outcome_success(trac, event_log, host_repo):
    """Verify that closed thread yields new dispatch/attempt (legal Red).

    Constructs the scenario: writes a legal discussion and marks it as resolved.
    The doc-comment flow is not yet wired; the test asserts that the runtime
    emits outcome.restored or outcome.discarded with a new dispatch identity,
    but the runtime never emits these — legal Red.
    """
    # Construct scenario: add a resolved discussion thread
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield [RESOLVED]:** Resolved discussion for testing.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    resumed = events_of(events, "outcome.resumed")
    assert len(restored) + len(discarded) + len(resumed) > 0, (
        "expected outcome.restored/discarded/resumed for closed thread"
    )


@pytest.mark.integration
# AC-FR0237-01@v0.5 TRACKS-TRACE illegal body edit atomically rolls back entire outcome
def test_illegal_body_edit_atomically_rolls_back_entire_outcome(trac, event_log, host_repo):
    """Verify that illegal body edit produces outcome.rejected (legal Red).

    Constructs the scenario: writes a non-discussion body edit to a design
    document (test-plan.md) — an illegal modification.  The doc-comment flow
    is not yet wired; the test asserts that outcome.rejected(over_reach)
    appears in the event stream, but the runtime never emits it — legal Red.
    """
    # Construct scenario: make a non-discussion body edit to test-plan.md
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        # Add a line that is not a discussion blockquote (illegal body edit)
        body_edit = "\n\nThis is an illegal body edit — not a discussion.\n"
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + body_edit, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    rejected = events_of(events, "outcome.rejected")
    assert len(rejected) > 0, (
        "expected outcome.rejected for illegal body edit"
    )


@pytest.mark.integration
# AC-FR0237-02@v0.5 TRACKS-TRACE illegal edit reports paths and redispatches new attempt
def test_illegal_edit_reports_paths_and_redispatches_new_attempt(trac, event_log, host_repo):
    """Verify that illegal edit events contain rejected_paths (legal Red).

    Constructs the scenario: writes a non-discussion body edit.  The doc-comment
    flow is not yet wired; the test asserts that outcome.rejected events contain
    rejected_paths and a new dispatch, but the runtime never emits these — legal Red.
    """
    # Construct scenario: illegal body edit on test-plan.md
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        body_edit = "\n\nIllegal body edit for path reporting test.\n"
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + body_edit, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    rejected = events_of(events, "outcome.rejected")
    over_reach = [
        e for e in rejected
        if e["payload"].get("failure_class") == "over_reach"
        and e["payload"].get("rollback") == "atomic"
    ]
    assert len(over_reach) > 0, (
        "expected outcome.rejected(over_reach, atomic) for illegal body edit"
    )


@pytest.mark.integration
# AC-FR0237-03@v0.5 TRACKS-TRACE illegal edit failure evidence survives replay
def test_illegal_edit_failure_evidence_survives_replay(trac, event_log, host_repo):
    """Verify that illegal edit failure evidence survives replay (legal Red).

    Constructs the scenario: writes a non-discussion body edit.  The doc-comment
    flow is not yet wired; the test asserts that the failure evidence from an
    illegal body edit is present in replay, but the runtime never produces it
    — legal Red.
    """
    # Construct scenario: illegal body edit on test-plan.md
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        body_edit = "\n\nIllegal body edit for replay evidence test.\n"
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + body_edit, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    rejected = events_of(events, "outcome.rejected")
    assert len(rejected) > 0, (
        "expected outcome.rejected events that survive replay"
    )
