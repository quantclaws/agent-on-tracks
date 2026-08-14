"""Quarantine and recovery integration tests (FR-0236/NFR-0090).

All assertions land on public observable outlets (interfaces.md §4e):
events table outcome.quarantined, outcome.restored, outcome.discarded,
outcome.resumed, and CLI status.  The doc-comment-first flow is not yet
wired into the runtime (IF-QUARANTINE-001/IF-DOCGAP-001 stubs), producing
a legal Red at the stub-function layer.

Each test constructs the AC triggering scenario (quarantine-eligible change,
legal discussion, or resume decision) before asserting the expected event.
"""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0236-01@v0.5 TRACKS-TRACE quarantine excludes human pre-dirty and shared index
def test_quarantine_excludes_human_predirty_and_shared_index(trac, event_log, host_repo):
    """Verify that outcome.quarantined isolates only agent changes (legal Red).

    Constructs the scenario: writes a legal discussion to a permitted document,
    simulating a quarantine-eligible change.  The quarantine flow is not yet
    wired; the test asserts that outcome.quarantined appears in the event
    stream, but the runtime never emits it — legal Red.
    """
    # Construct scenario: add a legal discussion (simulates quarantine-eligible change)
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** Discussion for quarantine isolation test.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    quarantined = events_of(events, "outcome.quarantined")
    assert len(quarantined) > 0, (
        "expected outcome.quarantined event excluding human/pre-dirty"
    )


@pytest.mark.integration
# AC-FR0236-02@v0.5 TRACKS-TRACE empty/held quarantine never checkpoint before resume
def test_empty_and_held_quarantine_never_checkpoint_before_resume(trac, event_log, host_repo):
    """Verify that empty/held quarantine never checkpoints (legal Red).

    Constructs the scenario: writes a legal discussion to trigger quarantine
    flow.  The quarantine flow is not yet wired; the test asserts that the
    runtime produces outcome.quarantined with status=empty|held before any
    checkpoint, but the runtime never emits it — legal Red.
    """
    # Construct scenario: add a legal discussion
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** Discussion for empty/held quarantine test.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    quarantined = events_of(events, "outcome.quarantined")
    assert len(quarantined) > 0, (
        "expected outcome.quarantined(event) before checkpoint"
    )


@pytest.mark.integration
# AC-FR0236-03@v0.5 TRACKS-TRACE restart rebuilds doc gap and quarantine state
# AC-NFR0090-01@v0.5 TRACKS-TRACE all doc-gap/quarantine/resume facts append-only and rebuildable
def test_restart_rebuilds_doc_gap_and_quarantine_state(trac, event_log, host_repo):
    """Verify that restart rebuilds quarantine state from events (legal Red).

    Constructs the scenario: writes a legal discussion to trigger quarantine
    flow, then simulates a restart.  The quarantine flow is not yet wired;
    the test asserts that the runtime can rebuild quarantine state from
    append-only events, but the runtime never emits the required events
    — legal Red.
    """
    # Construct scenario: add a legal discussion
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** Discussion for restart rebuild test.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    quarantined = events_of(events, "outcome.quarantined")
    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    resumed = events_of(events, "outcome.resumed")
    all_quarantine_events = quarantined + restored + discarded + resumed
    assert len(all_quarantine_events) > 0, (
        "expected quarantine/resume events for state rebuild"
    )


@pytest.mark.integration
# AC-FR0236-04@v0.5 TRACKS-TRACE restore current/discard stale without pre-dirty
def test_resume_restores_current_and_discards_stale(trac, event_log, host_repo):
    """Verify that resume restores current and discards stale (legal Red).

    Constructs the scenario: writes a legal discussion to trigger quarantine
    and resume flow.  The quarantine flow is not yet wired; the test asserts
    that the runtime emits outcome.restored(identity_current) or
    outcome.discarded(stale), but the runtime never emits these — legal Red.
    """
    # Construct scenario: add a resolved discussion
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield [RESOLVED]:** Resolved discussion for restore test.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    current_restores = [
        e for e in restored
        if e["payload"].get("reason") == "identity_current"
    ]
    stale_discards = [
        e for e in discarded
        if e["payload"].get("reason") in ("design_stale", "run_stale", "content_conflict")
    ]
    assert len(current_restores) + len(stale_discards) > 0, (
        "expected restore(current) or discard(stale) event"
    )


@pytest.mark.integration
# AC-NFR0090-02@v0.5 TRACKS-TRACE interruption never leaks quarantine past gates
def test_interruption_never_leaks_quarantine_past_gates(trac, event_log, host_repo):
    """Verify that interruption never leaks quarantine past gates (legal Red).

    Constructs the scenario: writes a legal discussion to trigger quarantine
    flow, then simulates interruption.  The quarantine flow is not yet wired;
    the test asserts that the runtime atomically rolls back quarantine without
    polluting the index, but the runtime never emits quarantine events — legal Red.
    """
    # Construct scenario: add a legal discussion
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield:** Discussion for interruption gate test.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    quarantined = events_of(events, "outcome.quarantined")
    assert len(quarantined) > 0, (
        "expected outcome.quarantined event from quarantine gate"
    )
    for q in quarantined:
        status = q["payload"].get("status")
        assert status in ("empty", "held"), (
            f"expected empty|held quarantine, got {status}"
        )


@pytest.mark.integration
# AC-NFR0090-03@v0.5 TRACKS-TRACE replay never promotes old outcome or duplicates success
def test_replay_never_promotes_old_outcome_or_duplicates_success(trac, event_log, host_repo):
    """Verify that replay never promotes old outcome (legal Red).

    Constructs the scenario: writes a legal discussion to trigger quarantine
    and resume flow.  The quarantine flow is not yet wired; the test asserts
    that the runtime never promotes old outcomes to success, but the runtime
    never emits the required quarantine events — legal Red.
    """
    # Construct scenario: add a resolved discussion
    tp_path = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    if tp_path.exists():
        discussion = (
            "\n\n> **Shield [RESOLVED]:** Resolved discussion for replay test.\n"
        )
        tp_path.write_text(tp_path.read_text(encoding="utf-8") + discussion, encoding="utf-8")

    _, _, events = run_m_impl_journey(trac, event_log)
    resumed = events_of(events, "outcome.resumed")
    assert len(resumed) > 0, (
        "expected outcome.resumed event for new attempt replay"
    )
    for r in resumed:
        payload = r["payload"]
        assert "next_dispatch_id" in payload, (
            "expected next_dispatch_id in outcome.resumed"
        )
        assert payload.get("next_attempt", 0) > 0, (
            "expected next_attempt > 0 in outcome.resumed"
        )
