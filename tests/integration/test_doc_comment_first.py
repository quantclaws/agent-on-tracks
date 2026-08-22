"""Doc-comment-first outcome integration tests (FR-0234/FR-0235/FR-0237).

All assertions land on public observable outlets (interfaces.md §4e):
events table doc_comment.detected, doc_comment.adjudicated,
outcome.quarantined, outcome.rejected, outcome.restored/discarded/resumed,
and the CLI (replay) text surface.

Scenario construction (PRISM-V05-R2-02 revision): every positive test arms
``tests/doc_gap_injection.arm_doc_delta`` so the delta (legal discussion
blockquote, illegal body edit, or resolved thread) is applied by the
dispatched Shield agent INSIDE the dispatch window — after the pre-dispatch
document-identity snapshot, before outcome validation.  A pre-dispatch write
would only move the baseline and could never trigger detection.

Adjudication closure (#62): after the SM-02 pause, Prism adjudicates
out-of-band on the public document surface (nested ``SM-02-ADJUDICATION``
marker reply, flow.md §10.4); ``pause_then_adjudicate`` drives that public
journey and a second ``trac run`` ingests it.  Illegal-body-edit scenarios
assert the FR-0237 atomic rollback instead of delta persistence: the
injected edit is legitimately ABSENT after the journey because the runtime
restores the document byte-for-byte.
"""

import pytest

from tests.doc_gap_injection import (
    TRACKS_DOC_PLAN,
    arm_doc_delta,
    assert_doc_delta_landed,
)
from tests.integration.helpers import walk_to_m_test
from tests.integration.v05_contract_helpers import (
    command_dispatches,
    events_of,
    pause_then_adjudicate,
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
    """Verify that a design-gap adjudication emits doc_comment.adjudicated.

    Constructs the scenario: the dispatched Shield agent appends a legal
    design-gap discussion to test-plan.md inside the dispatch window; the
    first ``trac run`` pauses on detection.  Prism then adjudicates on the
    public document surface (nested SM-02-ADJUDICATION marker, flow.md
    §10.4) and a second run ingests it.  The test asserts that
    doc_comment.adjudicated(design_gap, responsible_role=archer) appears in
    the event stream and that no human gate event occurs after detection
    (FR-0235-01: no Human technical gate).
    """
    discussion = "\n\n> **Shield:** Design gap discussion requiring Archer routing.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="design_gap"
    )

    detected = events_of(events, "doc_comment.detected")
    assert len(detected) == 1, (
        "expected exactly one doc_comment.detected for the injected discussion"
    )
    design_gaps = [
        e for e in events_of(events, "doc_comment.adjudicated")
        if e["payload"].get("route") == "design_gap"
    ]
    assert len(design_gaps) == 1, (
        "expected exactly one doc_comment.adjudicated(design_gap) event"
    )
    assert design_gaps[0]["payload"].get("responsible_role") == "archer", (
        "§1m: doc_comment.adjudicated(design_gap) must route archer"
    )
    # FR-0235-01: routing to Archer happens WITHOUT a Human technical gate —
    # no human.* event may follow the first detection.
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
    """Verify agent-correction adjudication keeps the outcome paused.

    Constructs the scenario: the dispatched Shield agent appends a legal
    discussion that Prism adjudicates to the agent-correction route.  The
    discussion thread is never closed in this test, so the outcome must stay
    paused: no outcome.restored/discarded/resumed may appear even after the
    adjudication is ingested (FR-0235-02).
    """
    discussion = "\n\n> **Shield:** Agent-correction guidance for the original agent.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="agent_correction"
    )

    agent_corrections = [
        e for e in events_of(events, "doc_comment.adjudicated")
        if e["payload"].get("route") == "agent_correction"
    ]
    assert len(agent_corrections) == 1, (
        "expected exactly one doc_comment.adjudicated(agent_correction) event"
    )
    assert agent_corrections[0]["payload"].get("responsible_role") == "shield", (
        "agent_correction must route the originating shield role"
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
    """Verify that a closed thread yields a new dispatch/attempt.

    Constructs the scenario: the dispatched Shield agent appends a discussion
    thread that is born resolved (status on the root line, §1k).  After
    Prism's adjudication is ingested, the closed thread reaches the resume
    decision: the runtime emits outcome.restored/discarded/resumed carrying
    the new dispatch identity (next_dispatch_id, next_attempt — §1m) and the
    resumed journey completes — never by promoting the old outcome.
    """
    resolved_root = (
        "> **Shield [RESOLVED]:** Resolved thread injected inside the dispatch window."
    )
    arm_doc_delta(
        monkeypatch,
        path=TRACKS_DOC_PLAN,
        text=f"\n\n{resolved_root}\n",
    )

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="agent_correction"
    )

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
    completed = [
        e for e in events if e["type"] == "run.completed"
    ]
    assert completed, (
        "the resumed journey must reach its terminal boundary — the old "
        "paused outcome is never promoted to success in place"
    )


@pytest.mark.integration
# AC-FR0237-01@v0.5 TRACKS-TRACE illegal body edit atomically rolls back entire outcome
def test_illegal_body_edit_atomically_rolls_back_entire_outcome(trac, event_log, host_repo, monkeypatch):
    """Verify that an illegal body edit produces outcome.rejected + rollback.

    Constructs the scenario: the dispatched Shield agent appends
    NON-discussion body text to test-plan.md as part of its work — an
    illegal body edit (§1k DocDeltaClass=illegal_body_edit).  The runtime
    rejects the outcome atomically BEFORE ordinary validation: the test
    asserts outcome.rejected(over_reach, atomic) appears AND the injected
    edit is gone — the document is restored to its pre-dispatch bytes, so
    asserting delta persistence here would contradict the FR-0237 contract.
    """
    body_edit = "\n\nThis is an illegal body edit injected inside the dispatch window — not a discussion.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=body_edit)

    run_id = walk_to_m_test(trac, version="v0.5")
    result = trac("run")
    assert result.returncode == 0, result.stderr

    doc = host_repo / TRACKS_DOC_PLAN
    assert doc.is_file(), "the edited document must exist after the journey"
    content = doc.read_text(encoding="utf-8")
    assert body_edit not in content, (
        "FR-0237 atomic rollback must restore the document — the illegal "
        "edit must NOT survive the rejected outcome"
    )

    events = event_log(run_id)
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
    """Verify that illegal edit events contain rejected_paths and redispatch.

    Constructs the scenario: in-window illegal body edit (as above).  The
    test asserts that outcome.rejected carries the rejected document path
    and that a NEW shield dispatch follows the atomic rollback — observable
    as a later shield WRITE dispatch in the public event stream.
    """
    body_edit = "\n\nIllegal body edit for path reporting and redispatch, injected in-window.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=body_edit)

    run_id = walk_to_m_test(trac, version="v0.5")
    result = trac("run")
    assert result.returncode == 0, result.stderr

    doc = host_repo / TRACKS_DOC_PLAN
    assert doc.is_file(), "the edited document must exist after the journey"
    assert body_edit not in doc.read_text(encoding="utf-8"), (
        "FR-0237 atomic rollback must restore the document"
    )

    events = event_log(run_id)
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
    """Verify that illegal edit failure evidence survives replay.

    Constructs the scenario: in-window illegal body edit (as above).  The
    test asserts that the rejection evidence is present in the append-only
    event store AND visible in the public ``trac replay`` text surface.
    """
    body_edit = "\n\nIllegal body edit for replay evidence, injected in-window.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=body_edit)

    run_id = walk_to_m_test(trac, version="v0.5")
    result = trac("run")
    assert result.returncode == 0, result.stderr

    doc = host_repo / TRACKS_DOC_PLAN
    assert doc.is_file(), "the edited document must exist after the journey"
    assert body_edit not in doc.read_text(encoding="utf-8"), (
        "FR-0237 atomic rollback must restore the document"
    )

    events = event_log(run_id)
    rejected = events_of(events, "outcome.rejected")
    assert len(rejected) > 0, (
        "expected outcome.rejected events that survive replay"
    )
    replay = trac("replay", run_id)
    assert replay.returncode == 0, replay.stderr
    assert "outcome.rejected" in replay.stdout, (
        "rejection evidence must survive replay on the public text surface"
    )


@pytest.mark.integration
# SM-02 design_gap nested workflow (#62 finding 1)
# AC-FR0235-01@v0.5 TRACKS-TRACE design gap drives nested Archer + Prism review
def test_design_gap_drives_nested_archer_revision_and_prism_review(
    trac, event_log, host_repo, monkeypatch
):
    """Verify a design_gap adjudication drives the actual nested workflow.

    After Prism adjudicates design_gap on the document surface, the next
    ``trac run`` must:
    1. Dispatch Archer to revise the design (doc_gap.design_dispatched +
       command.issued role=archer).
    2. Checkpoint the revised design (doc_gap.design_revised with a changed
       design identity).
    3. Dispatch Prism to review the revised design
       (doc_gap.design_dispatched phase=design_review).
    4. Emit Prism's review verdict (doc_gap.design_reviewed pass).
    5. Resume the paused origin with a NEW attempt (outcome.resumed) - the
       design revision makes the quarantine stale, so held成果 is discarded
       (outcome.discarded reason=design_stale) or restored (empty quarantine).
    6. Emit NO human.* events (no Human technical gate).
    """
    discussion = "\n\n> **Shield:** Design gap requiring nested Archer revision.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="design_gap"
    )

    # 1. Archer design-revision dispatch was issued (WAL + SM-02 audit).
    archer_dispatched = [
        e for e in events
        if e["type"] == "doc_gap.design_dispatched"
        and e["payload"].get("phase") == "design_revision"
    ]
    assert len(archer_dispatched) == 1, (
        "expected exactly one doc_gap.design_dispatched(design_revision)"
    )
    archer_cmd = [
        e for e in command_dispatches(events, role="archer")
        if e["seq"] > archer_dispatched[0]["seq"]
    ]
    assert archer_cmd, "expected a command.issued for role=archer after dispatch"

    # 2. Design revision checkpointed with a changed identity.
    revised = events_of(events, "doc_gap.design_revised")
    assert len(revised) == 1, "expected doc_gap.design_revised checkpoint"
    assert revised[0]["payload"].get("revised_design_identity"), (
        "doc_gap.design_revised must carry the revised design identity"
    )
    assert set(revised[0]["payload"].get("changed_paths", [])) <= {
        "architecture.md", "interfaces.md", "test-plan.md"
    }, "nested Archer checkpoint must not include the held Shield test artifact"

    # 3. Prism design-review dispatch was issued.
    prism_dispatched = [
        e for e in events
        if e["type"] == "doc_gap.design_dispatched"
        and e["payload"].get("phase") == "design_review"
    ]
    assert len(prism_dispatched) == 1, (
        "expected exactly one doc_gap.design_dispatched(design_review)"
    )

    # 4. Prism reviewed the revised design (pass).
    reviewed = events_of(events, "doc_gap.design_reviewed")
    assert len(reviewed) == 1, "expected doc_gap.design_reviewed"
    assert reviewed[0]["payload"].get("verdict") == "pass", (
        "expected Prism design review verdict=pass"
    )

    # 5. Resume with a NEW attempt; held成果 discarded as design_stale (or
    # restored when the quarantine is empty - either is a legal resume).
    resumed = events_of(events, "outcome.resumed")
    assert len(resumed) >= 1, (
        "expected outcome.resumed with a new dispatch/attempt after the "
        "nested workflow"
    )
    assert resumed[0]["payload"].get("next_attempt", 0) >= 1, (
        "the resume must be a NEW attempt"
    )
    discarded = events_of(events, "outcome.discarded")
    restored = events_of(events, "outcome.restored")
    terminal = discarded + restored
    assert len(terminal) >= 1, (
        "expected outcome.restored or outcome.discarded before resumed"
    )
    if discarded:
        assert discarded[0]["payload"].get("reason") == "design_stale", (
            "after a design revision, held quarantine成果 must be discarded "
            "as design_stale"
        )

    # 6. No Human technical gate after the detection.
    detected = events_of(events, "doc_comment.detected")
    first_seq = detected[0]["seq"] if detected else 0
    human_after = [
        e for e in events
        if e["type"].startswith("human.") and e["seq"] > first_seq
    ]
    assert not human_after, (
        f"design_gap nested workflow must not require a human gate: {human_after}"
    )


@pytest.mark.integration
# SM-02 design_gap nested workflow (#62 finding 1)
# AC-FR0235-02@v0.5 TRACKS-TRACE no design.committed/prism.verdict clobber origin
def test_design_gap_nested_workflow_does_not_clobber_origin_stage(
    trac, event_log, host_repo, monkeypatch
):
    """Verify the nested workflow emits no design.committed/prism.verdict.

    The SM-02-scoped events (doc_gap.*) project into the doc-gap record
    without touching the origin stage's M-DESIGN reducers.  No
    design.committed or prism.verdict event may appear during the nested
    workflow - those would clobber the paused M-TEST/M-IMPL origin state.
    """
    discussion = "\n\n> **Shield:** Design gap for origin-stage preservation.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="design_gap"
    )

    adjudicated = [
        e for e in events
        if e["type"] == "doc_comment.adjudicated"
        and e["payload"].get("route") == "design_gap"
    ]
    resumed = events_of(events, "outcome.resumed")
    assert adjudicated and resumed, "expected adjudication + resume"
    # Only check the nested-workflow window: between the adjudication and
    # the resume.  After resume, the normal M-TEST pipeline legitimately
    # emits prism.verdict (the M-TEST review, not M-DESIGN).
    adj_seq = adjudicated[0]["seq"]
    resume_seq = resumed[0]["seq"]
    design_committed = [
        e for e in events
        if e["type"] == "design.committed" and adj_seq < e["seq"] < resume_seq
    ]
    prism_verdict = [
        e for e in events
        if e["type"] == "prism.verdict" and adj_seq < e["seq"] < resume_seq
    ]
    assert not design_committed, (
        "doc_gap nested workflow must NOT emit design.committed (clobbers "
        f"origin stage): {design_committed}"
    )
    assert not prism_verdict, (
        "doc_gap nested workflow must NOT emit prism.verdict (clobbers "
        f"origin stage): {prism_verdict}"
    )


@pytest.mark.integration
# SM-02 design_gap nested workflow (#62 finding 4)
# AC-FR0236-04@v0.5 TRACKS-TRACE held non-design origin artifact discarded as design_stale
def test_design_gap_held_artifact_discarded_design_stale_not_committed(
    trac, event_log, host_repo, monkeypatch
):
    """A held non-design origin artifact is discarded (not committed) after a
    nested Archer design revision, and the origin gets a NEW attempt.

    Constructs a Shield WRITE that pauses on a legal discussion
    (``outcome.quarantined`` status=``held`` - the Shield's test files are
    agent-attributable non-design changes inside the project's
    ``[layout.shield]`` writable dirs).  Prism adjudicates ``design_gap``;
    the nested Archer revises the design docs (advancing the combined design
    identity) and Prism reviews them.  Because the held quarantine's pause-time
    design anchor now differs from the live revised identity, the resume
    decision discards the held artifact as ``design_stale`` (never committed -
    no ``test.committed`` checkpoint fires for the paused outcome) and issues
    a NEW Shield WRITE attempt.
    """
    discussion = "\n\n> **Shield:** Design gap requiring nested Archer revision.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="design_gap"
    )

    # Held non-design origin artifact: the Shield WRITE paused with a HELD
    # quarantine (its test-file changes, attributable and inside the layout
    # writable dirs, not pre-dirty).
    quarantined = events_of(events, "outcome.quarantined")
    assert quarantined, "expected outcome.quarantined at the SM-02 pause"
    assert quarantined[0]["payload"].get("status") == "held", (
        "origin artifact must be HELD (non-design agent change quarantined); "
        f"got {quarantined[0]['payload'].get('status')!r}"
    )

    # Nested Archer design revision advanced the combined design identity.
    revised = events_of(events, "doc_gap.design_revised")
    assert revised, "expected doc_gap.design_revised (Archer revision checkpoint)"
    assert revised[0]["payload"].get("revised_design_identity"), (
        "doc_gap.design_revised must carry the revised design identity"
    )

    # The held artifact is discarded as design_stale (the revised design
    # identity drifted from the quarantine's pause-time anchor).
    discarded = events_of(events, "outcome.discarded")
    assert discarded, (
        "expected outcome.discarded after a design revision of a HELD quarantine"
    )
    assert discarded[0]["payload"].get("reason") == "design_stale", (
        "held artifact must be discarded as design_stale; "
        f"got {discarded[0]['payload'].get('reason')!r}"
    )

    # The held artifact was NEVER committed: no test.committed checkpoint
    # fires for the paused outcome (the SM-02 pause precedes ordinary
    # validation/checkpoint).  Only a NEW attempt's test.committed may appear
    # AFTER the resume.
    q_seq = quarantined[0]["seq"]
    discard_seq = discarded[0]["seq"]
    test_committed_before_discard = [
        e for e in events
        if e["type"] == "test.committed" and q_seq < e["seq"] < discard_seq
    ]
    assert not test_committed_before_discard, (
        "held non-design origin artifact must NOT be committed before the "
        f"design_stale discard: {test_committed_before_discard}"
    )

    # Origin gets a NEW attempt (resume issues a fresh Shield WRITE dispatch).
    resumed = events_of(events, "outcome.resumed")
    assert resumed, "expected outcome.resumed with a NEW origin attempt"
    assert resumed[0]["payload"].get("next_attempt", 0) >= 1, (
        "the resume must be a NEW attempt (next_attempt >= 1)"
    )
    # The resume follows the discard (held artifact dropped before re-dispatch).
    assert discard_seq < resumed[0]["seq"], (
        "outcome.resumed must follow outcome.discarded"
    )
    shield_resume_dispatches = [
        event
        for event in command_dispatches(events, role="shield")
        if event["seq"] > resumed[0]["seq"]
    ]
    assert shield_resume_dispatches, (
        "expected a NEW Shield dispatch (origin new attempt) after the resume"
    )
