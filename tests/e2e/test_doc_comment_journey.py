"""E-04 operator happy path for design discussion adjudication and resume.

This e2e test covers the user-facing path where a design discussion is added,
the run pauses on detection, Prism adjudicates out-of-band on the document
surface (nested SM-02-ADJUDICATION marker, flow.md §10.4), the thread is
legally resolved, and the outcome resumes with a new attempt to completion.
All assertions land on public CLI/event/file observable outlets
(interfaces.md §2e/§4e).

Scenario construction (PRISM-V05-R2-02 revision): the design discussion is
applied by the dispatched Shield agent INSIDE the dispatch window
(``tests/doc_gap_injection.arm_doc_delta`` wraps the fake agent seam) — a
pre-dispatch write would only move the document baseline and could never
trigger detection.
"""

import pytest

from tests._support.doc_gap_injection import (
    TRACKS_DOC_PLAN,
    arm_doc_delta,
    assert_doc_delta_landed,
    disarm_doc_delta,
    prism_adjudicates,
)
from tests.e2e.helpers import walk_to_await_human


@pytest.mark.e2e
# AC-FR0234-03@v0.5 TRACKS-TRACE status/discuss/replay/report shows waiting/origin/quarantine
# AC-FR0235-01@v0.5 TRACKS-TRACE design gap routes Archer without human gate
# AC-FR0235-03@v0.5 TRACKS-TRACE closed thread creates new attempt not old outcome success
def test_design_discussion_adjudication_resume_happy_path(trac, event_log, host_repo, monkeypatch):
    """Verify the E-04 operator happy path over the public CLI surface.

    1. The dispatched Shield appends a legal design discussion in-window;
       the run pauses with doc_comment.detected + outcome.quarantined.
    2. Prism adjudicates (design_gap -> archer) and closes the thread on the
       public document surface.
    3. The next ``trac run`` ingests the adjudication, resumes with a NEW
       attempt and completes the journey.
    4. ``trac replay`` surfaces the full lifecycle chain.
    """
    root = "> **Shield:** Design discussion for the E-04 happy path."
    discussion = f"\n\n{root}\n"
    arm_doc_delta(monkeypatch, path=TRACKS_DOC_PLAN, text=discussion)

    run_id = walk_to_await_human(trac, version="v0.5")
    assert trac("approve", "--actor", "Aaron").returncode == 0

    parked = trac("run")
    assert parked.returncode == 0, parked.stderr
    assert "status=active" in parked.stdout, (
        "the SM-02 pause must park the run as active (outcome paused)"
    )

    events = event_log(run_id)
    detected = [e for e in events if e["type"] == "doc_comment.detected"]
    quarantined = [e for e in events if e["type"] == "outcome.quarantined"]
    assert len(detected) == 1, "expected doc_comment.detected for the discussion"
    assert len(quarantined) == 1, "expected outcome.quarantined for the pause"
    assert_doc_delta_landed(host_repo, TRACKS_DOC_PLAN, discussion)

    prism_adjudicates(
        host_repo,
        route="design_gap",
        quarantine_id=quarantined[0]["payload"]["quarantine_id"],
        threads=list(detected[0]["payload"]["thread_ids"]),
        resolve_root=root,
    )
    disarm_doc_delta(monkeypatch)

    resumed_run = trac("run")
    assert resumed_run.returncode == 0, resumed_run.stderr
    assert "status=completed" in resumed_run.stdout, (
        "after adjudication + thread close the resumed journey must complete"
    )

    events = event_log(run_id)
    adjudicated = [
        e for e in events
        if e["type"] == "doc_comment.adjudicated"
        and e["payload"].get("route") == "design_gap"
    ]
    restored = [e for e in events if e["type"] == "outcome.restored"]
    discarded = [e for e in events if e["type"] == "outcome.discarded"]
    resumed = [e for e in events if e["type"] == "outcome.resumed"]
    assert len(adjudicated) == 1, "expected doc_comment.adjudicated(design_gap)"
    assert adjudicated[0]["payload"].get("responsible_role") == "archer"
    assert (restored or discarded) and resumed, (
        "expected outcome.restored or outcome.discarded plus outcome.resumed after thread close"
    )
    assert resumed[0]["payload"].get("next_attempt", 0) >= 1, (
        "the resume must be a NEW attempt, never the old outcome"
    )

    # Lifecycle order on the public replay surface: detected -> quarantined
    # -> adjudicated -> restored -> resumed.
    replay = trac("replay", run_id)
    assert replay.returncode == 0, replay.stderr
    terminal_marker = (
        "outcome.restored" if restored else "outcome.discarded"
    )
    order = [replay.stdout.find(marker) for marker in (
        "doc_comment.detected",
        "outcome.quarantined",
        "doc_comment.adjudicated",
        terminal_marker,
        "outcome.resumed",
    )]
    assert all(pos >= 0 for pos in order), (
        f"replay must surface the full SM-02 lifecycle: {order}"
    )
    assert order == sorted(order), (
        f"replay lifecycle must appear in order: {order}"
    )
