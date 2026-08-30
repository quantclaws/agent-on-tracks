"""Quarantine and recovery integration tests (FR-0236/NFR-0090).

All assertions land on public observable outlets (interfaces.md §4e):
events table outcome.quarantined, outcome.restored, outcome.discarded,
outcome.resumed, and the CLI (replay) text surface.

Scenario construction (PRISM-V05-R2-02 revision): every test arms
``tests/doc_gap_injection.arm_doc_delta`` so the quarantine-eligible delta
lands INSIDE the dispatch window (applied by the dispatched Shield agent at
the ``_act_shield`` seam, after the pre-dispatch document-identity
snapshot).  Human-side pre-dirty worktree state is written at the fake
design seam via the ``baseline.touch`` config — before the dispatch, never
attributable to the agent.

Adjudication closure (#62): resume decisions require Prism's out-of-band
SM-02-ADJUDICATION marker first (flow.md §10.4); ``pause_then_adjudicate``
drives that public journey.  The resume decision comes from
``decide_quarantine_resume()``: an empty quarantine restores trivially with
reason ``empty``; stale/conflicting identities discard fail-closed.
"""

import pytest

from tests._support.doc_gap_injection import TRACKS_DOC_PLAN, arm_doc_delta, assert_doc_delta_landed
from tests.integration.helpers import walk_to_m_test
from tests.integration.v05_contract_helpers import (
    events_of,
    pause_then_adjudicate,
    run_m_impl_journey,
)

_HUMAN_PRE_DIRTY_REL = "notes/human-draft.md"
_HUMAN_PRE_DIRTY_TEXT = "human work-in-progress, never staged\n"

_COMMIT_EVENT_TYPES = ("red.checkpointed", "green.committed", "refactor.committed", "test.committed")


@pytest.mark.integration
# AC-FR0236-01@v0.5 TRACKS-TRACE quarantine excludes human pre-dirty and shared index
def test_quarantine_excludes_human_predirty_and_shared_index(trac, event_log, host_repo, monkeypatch):
    """Verify that outcome.quarantined isolates only agent changes (legal Red).

    Constructs the scenario: a HUMAN pre-dirty worktree file is written at
    the design seam (part of the baseline, never staged — FR-0236-01's
    "human pre-dirty"), and the dispatched Shield agent applies a legal
    discussion delta inside the dispatch window (the agent-attributable
    change).  The quarantine flow is not wired; the test asserts that
    outcome.quarantined appears carrying the manifest reference and that the
    human pre-dirty file survives untouched — but the runtime never emits
    the event — legal Red.
    """
    discussion = "\n\n> **Shield:** Discussion for quarantine isolation test.\n"
    arm_doc_delta(
        monkeypatch,
        path=TRACKS_DOC_PLAN,
        text=discussion,
        baseline={"touch": {_HUMAN_PRE_DIRTY_REL: _HUMAN_PRE_DIRTY_TEXT}},
    )

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    # Human pre-dirty file is baseline state: it must exist and be unchanged.
    human_file = host_repo / _HUMAN_PRE_DIRTY_REL
    assert human_file.is_file(), (
        f"human pre-dirty file {_HUMAN_PRE_DIRTY_REL} must exist (baseline "
        "worktree state)"
    )
    assert human_file.read_text(encoding="utf-8") == _HUMAN_PRE_DIRTY_TEXT, (
        "quarantine must never roll back human pre-dirty worktree state"
    )

    quarantined = events_of(events, "outcome.quarantined")
    assert len(quarantined) > 0, (
        "expected outcome.quarantined event excluding human/pre-dirty"
    )
    for q in quarantined:
        payload = q["payload"]
        assert payload.get("quarantine_id"), "§1m: quarantined must carry quarantine_id"
        assert payload.get("manifest_ref"), "§1m: quarantined must carry manifest_ref"
        assert payload.get("manifest_sha256"), "§1m: quarantined must carry manifest_sha256"
        assert payload.get("status") in ("empty", "held"), (
            f"§1m: quarantined status must be empty|held, got {payload.get('status')}"
        )


@pytest.mark.integration
# AC-FR0236-02@v0.5 TRACKS-TRACE empty/held quarantine never checkpoint before resume
def test_empty_and_held_quarantine_never_checkpoint_before_resume(trac, event_log, host_repo, monkeypatch):
    """Verify that empty/held quarantine never checkpoints (legal Red).

    Constructs the scenario: in-window legal discussion delta (agent changes
    exist, so a wired runtime would hold the quarantine).  The quarantine
    flow is not wired; the test asserts that outcome.quarantined(status
    empty|held) appears and that NO checkpoint/commit event occurs between
    the quarantine and the resume decision — legal Red.
    """
    discussion = "\n\n> **Shield:** Discussion for empty/held quarantine test.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    quarantined = events_of(events, "outcome.quarantined")
    assert len(quarantined) > 0, (
        "expected outcome.quarantined(status=held) before any checkpoint"
    )
    first_q_seq = quarantined[0]["seq"]
    assert quarantined[0]["payload"].get("status") in ("empty", "held"), (
        "§1m: quarantined status must be empty|held"
    )
    resume_seqs = [
        e["seq"]
        for e in events
        if e["type"] in ("outcome.restored", "outcome.discarded", "outcome.resumed")
    ]
    resume_boundary = min(resume_seqs) if resume_seqs else float("inf")
    checkpoints_before_resume = [
        e
        for e in events
        if e["type"] in _COMMIT_EVENT_TYPES
        and first_q_seq < e["seq"] < resume_boundary
    ]
    assert not checkpoints_before_resume, (
        "empty/held quarantine must never checkpoint before the resume "
        f"decision: {checkpoints_before_resume}"
    )


@pytest.mark.integration
# AC-FR0236-03@v0.5 TRACKS-TRACE restart rebuilds doc gap and quarantine state
# AC-NFR0090-01@v0.5 TRACKS-TRACE all doc-gap/quarantine/resume facts append-only and rebuildable
def test_restart_rebuilds_doc_gap_and_quarantine_state(trac, event_log, host_repo, monkeypatch):
    """Verify that restart rebuilds quarantine state from events (legal Red).

    Constructs the scenario: in-window legal discussion delta.  The
    quarantine flow is not wired; the test asserts that quarantine/resume
    events exist in the append-only store and that a FRESH process
    (``trac replay`` reads the store from scratch, i.e. rebuilds state)
    surfaces them — legal Red.
    """
    discussion = "\n\n> **Shield:** Discussion for restart rebuild test.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    quarantined = events_of(events, "outcome.quarantined")
    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    resumed = events_of(events, "outcome.resumed")
    all_quarantine_events = quarantined + restored + discarded + resumed
    assert len(all_quarantine_events) > 0, (
        "expected quarantine/resume events for state rebuild"
    )
    replay = trac("replay", run_id)
    assert replay.returncode == 0, replay.stderr
    assert "outcome.quarantined" in replay.stdout, (
        "a fresh process must rebuild quarantine state from the append-only "
        "event store (replay surfaces the doc-gap facts)"
    )


@pytest.mark.integration
# AC-FR0236-04@v0.5 TRACKS-TRACE restore current/discard stale without pre-dirty
def test_resume_restores_current_and_discards_stale(trac, event_log, host_repo, monkeypatch):
    """Verify the resume decision after Prism adjudication + thread close.

    Constructs the scenario: in-window RESOLVED discussion delta — the
    thread is born closed.  After Prism's adjudication is ingested, the
    resume decision fires through ``decide_quarantine_resume()``: the
    Shield WRITE's test-file changes ARE agent-attributable non-design
    changes inside the project's ``[layout.shield]`` writable dirs, so the
    quarantine is HELD; under ``agent_correction`` no design revision
    drifts the anchor, so the held content is still current and the record
    restores with reason ``identity_current`` (stale/conflict discards are
    covered by the decide_quarantine_resume unit matrix; the design_stale
    discard of a HELD quarantine after a nested Archer revision is covered
    by test_design_gap_held_artifact_discarded_design_stale_not_committed).
    """
    discussion = "\n\n> **Shield [RESOLVED]:** Resolved discussion for restore test.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="agent_correction"
    )

    restored = events_of(events, "outcome.restored")
    discarded = events_of(events, "outcome.discarded")
    assert len(restored) + len(discarded) > 0, (
        "expected a resume decision after adjudication + thread closure"
    )
    for e in restored + discarded:
        assert e["payload"].get("reason") in (
            "empty",
            "identity_current",
            "design_stale",
            "run_stale",
            "content_conflict",
        ), f"resume reason must come from the closed set, got {e['payload']}"
    # Deterministic for this scenario: the held test-file content is still
    # current under agent_correction (no design revision), so the held
    # quarantine restores — never a stale discard (AC-FR0236-04).
    assert any(
        e["payload"].get("reason") in ("empty", "identity_current") for e in restored
    ), (
        "a current held (or empty) quarantine must restore, never a stale "
        "discard (AC-FR0236-04)"
    )


@pytest.mark.integration
# AC-NFR0090-02@v0.5 TRACKS-TRACE interruption never leaks quarantine past gates
def test_interruption_never_leaks_quarantine_past_gates(trac, event_log, host_repo, monkeypatch):
    """Verify that interruption never leaks quarantine past gates (legal Red).

    Constructs the scenario: in-window legal discussion delta plus a human
    pre-dirty file at the baseline seam; the journey ends mid-flow (the walk
    stops after the dispatch, simulating an interrupted run before any
    resume decision).  The quarantine flow is not wired; the test asserts
    that any emitted outcome.quarantined carries a terminal status
    (empty|held) — i.e. the quarantine never leaks past a gate into a
    committed state — but the runtime never emits it — legal Red.
    """
    discussion = "\n\n> **Shield:** Discussion for interruption gate test.\n"
    arm_doc_delta(
        monkeypatch,
        path=TRACKS_DOC_PLAN,
        text=discussion,
        baseline={"touch": {_HUMAN_PRE_DIRTY_REL: _HUMAN_PRE_DIRTY_TEXT}},
    )

    _, _, events = run_m_impl_journey(trac, event_log)
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

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
def test_replay_never_promotes_old_outcome_or_duplicates_success(trac, event_log, host_repo, monkeypatch):
    """Verify that resume creates a NEW attempt and replays stay idempotent.

    Constructs the scenario: in-window RESOLVED discussion delta.  After
    Prism's adjudication is ingested, outcome.resumed carries a NEW dispatch
    identity (next_dispatch_id + next_attempt > 0 — the resume is a new
    attempt, never a re-promotion of the old outcome).  Re-running ``trac
    run`` afterwards must NOT re-emit the adjudication or duplicate the
    resume: the same marker comment can never create a second attempt.
    """
    discussion = "\n\n> **Shield [RESOLVED]:** Resolved discussion for replay test.\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_m_test(trac, version="v0.5")
    parked = trac("run")
    assert parked.returncode == 0, parked.stderr

    events = pause_then_adjudicate(
        trac, event_log, host_repo, monkeypatch, run_id, route="agent_correction"
    )

    resumed = events_of(events, "outcome.resumed")
    assert len(resumed) == 1, (
        "expected exactly one outcome.resumed for the new attempt"
    )
    payload = resumed[0]["payload"]
    assert "next_dispatch_id" in payload, (
        "expected next_dispatch_id in outcome.resumed"
    )
    assert payload.get("next_attempt", 0) > 0, (
        "expected next_attempt > 0 in outcome.resumed"
    )
    assert len(events_of(events, "doc_comment.adjudicated")) == 1

    # Idempotent replay: further runs must not re-adjudicate or duplicate.
    again = trac("run")
    assert again.returncode == 0, again.stderr
    events_after = event_log(run_id)
    assert len(events_of(events_after, "doc_comment.adjudicated")) == 1, (
        "replaying the same marker comment must not re-emit the adjudication"
    )
    assert len(events_of(events_after, "outcome.resumed")) == 1, (
        "replaying must not create a second attempt from the same decision"
    )
