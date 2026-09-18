"""Integration: pipeline regression (FR-0285, IF-PIPELINE-001).

b93 §8.1 bootstrap contract: the anchors are driven by the shared walker
(init -> start v0.8 -> triage -> doc trio -> approve -> M-IMPL RED failure
injection -> parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare
``trac run`` bootstrap is forbidden (v0.8 suite-wide defect, 7th unmigrated
file). The walk drives M-DESIGN -> M-TEST in a single post-approve run, so
the M-TEST segment (WRITE -> COLLECT -> red.validated -> prism.verdict) is
a real journey premise, not a seeded one.

Layering notes: the missing-link fail-closed half (pipeline incomplete) and
the prism-envelope-consumes-red-evidence half are pinned at unit level by
tests/unit/test_m_test_pipeline_red.py (4/4 GREEN on m_test.py); the
``trac report`` pipeline-version rendering half of AC-FR0285-03 belongs to
the T-040 report face (undelivered) and is deferred there per the b93
diagnosis — the integration anchor asserts the closed stage set via
``trac validate`` + walked ``stage.entered`` events.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked

pytestmark = pytest.mark.integration

_KNOWN_STAGES = {
    "M-START", "M-STORY", "M-SPEC", "M-ACC", "M-REQ-APPROVAL", "M-DESIGN",
    "M-TEST", "M-IMPL", "M-VERIFY", "M-SECURITY", "M-RELEASE", "M-PUBLISH",
    "M-MILESTONE",
}


def _m_test_segment(events: list[dict]) -> list[dict]:
    """Slice events from stage.entered(M-TEST) to the closing stage.exited."""
    start = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "stage.entered" and "M-TEST" in str(e["payload"])
    )
    for end in range(start + 1, len(events)):
        if events[end]["type"] == "stage.exited":
            return events[start : end + 1]
    return events[start:]


def _write_dispatches(segment: list[dict]) -> list[dict]:
    return [
        e
        for e in segment
        if e["type"] == "command.issued"
        and e["payload"].get("command", {}).get("kind") == "dispatch_agent"
        and e["payload"]["command"]["params"].get("substate") == "WRITE"
    ]


def _prism_dispatches(segment: list[dict]) -> list[dict]:
    return [
        e
        for e in segment
        if e["type"] == "command.issued"
        and e["payload"].get("command", {}).get("kind") == "dispatch_agent"
        and e["payload"]["command"]["params"].get("substate") == "PRISM_REVIEW"
    ]


# AC-FR0285-01@v0.8 TRACKS-TRACE write collect redcheck prism chain intact
def test_write_collect_redcheck_prism_chain_intact(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    events = event_log(run_id)
    segment = _m_test_segment(events)
    types = [e["type"] for e in segment]
    # WRITE (Shield dispatch) -> COLLECT (test.collected) -> red.validated
    # (Runtime RED_CHECK) -> prism.verdict (Prism REVIEW), in chain order.
    assert _write_dispatches(segment), "WRITE dispatch must open the M-TEST chain"
    write_idx = segment.index(_write_dispatches(segment)[0])
    collect_idx = types.index("test.collected")
    red_idx = types.index("red.validated")
    prism_idx = types.index("prism.verdict")
    assert "red.validated" in types, "red.validated must appear in M-TEST chain"
    assert "prism.verdict" in types, "prism.verdict must appear"
    assert write_idx < collect_idx < red_idx < prism_idx, (
        "M-TEST chain must hold WRITE -> COLLECT -> red.validated -> prism.verdict order"
    )
    # The PRISM_REVIEW dispatch CONSUMES the Runtime red.validated evidence:
    # the real dispatch command carries the red verdict identity (status,
    # selection, findings, nodes/outcomes refs) — the isolated counterexample
    # kill reviews the red evidence, never a producer self-check or a rerun.
    red = segment[red_idx]["payload"]
    selected = next(e for e in segment if e["type"] == "test.selected")["payload"]
    prism_dispatch = _prism_dispatches(segment)[-1]["payload"]["command"]["params"]
    assignment = prism_dispatch.get("assignment", {})
    red_evidence = assignment.get("red_evidence") or {}
    assert red_evidence.get("status") == red["status"] == "valid"
    assert red_evidence.get("selection_id") == red["selection_id"]
    assert red_evidence.get("selection_id") == selected["selection_id"]
    assert red_evidence.get("nodes_blob") == red["nodes_blob"]
    assert red_evidence.get("outcomes_ref") == red["outcomes_ref"]
    # M5 card diet: the card carries the classification summary only; the
    # per-node traceback detail stays in the red.validated event (history
    # lives in the event log, not the prompt).
    assert red_evidence.get("findings") == [
        {k: v for k, v in f.items() if k != "detail"} for f in red["findings"]
    ]
    # Prism only takes the isolated kill task (criteria pack), never a suite.
    verdict = segment[prism_idx]["payload"]
    assert verdict.get("criteria_pack"), "M-TEST verdict must carry the criteria pack"
    replay_out = trac("replay", run_id).stdout
    assert "prism.verdict" in replay_out
    assert "red.validated" in replay_out
    # The replay audit shows the consumed red evidence on the dispatch.
    assert "red_evidence" in replay_out
    assert red["selection_id"] in replay_out
    # The chain is complete on this journey: status must NOT report a missing
    # link (the injected-hole fail-closed half is pinned at the journey
    # boundary by test_missing_link_fails_closed_at_journey_boundary).
    status = trac("status").stdout
    assert "pipeline incomplete" not in status.lower()


# AC-FR0285-02@v0.8 TRACKS-TRACE no selfcheck authority
def test_no_selfcheck_authority(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    events = event_log(run_id)
    segment = _m_test_segment(events)
    types = [e["type"] for e in segment]
    # Authority is the Runtime RED_CHECK: red.validated must exist in M-TEST.
    assert "red.validated" in types, "red.validated must appear, not full.executed"
    # A FULL suite rerun never replaces the RED_CHECK chain: no full.executed
    # may appear inside the M-TEST segment masquerading as the authority.
    assert "full.executed" not in types, (
        "no full.executed may masquerade as red.validated during M-TEST"
    )
    # Shield self-report events (if any) never carry the authority gates.
    selfcheck = [e for e in events if "selfcheck" in e["type"].lower()]
    assert not [sc for sc in selfcheck if sc["type"] in ("red.validated", "prism.verdict")]


# AC-FR0285-03@v0.8 TRACKS-TRACE no new pipeline definition
def test_no_new_pipeline_definition(host_repo, trac, event_log):
    run_id = walk_to_m_impl_parked(trac)
    events = event_log(run_id)
    # Regression check only verifies the existing chain: every stage entered
    # on the walked journey is inside the frozen closed set (no new pipeline
    # definition).
    stages = [
        e["payload"].get("stage")
        for e in events
        if e["type"] == "stage.entered" and e["payload"].get("stage")
    ]
    assert stages, "the walk must enter stages"
    for s in stages:
        assert s in _KNOWN_STAGES, f"stage {s} is outside the frozen pipeline set"
    v = trac("validate", "--file", ".tracks/projects/project.toml")
    assert v.returncode in (0, 1)
    # AC-FR0285-03 report half: `trac report` renders the frozen test-pipeline
    # version — v0.8 is regression-only and adds no pipeline definition.
    from tests.e2e.helpers import generate_report_md
    from tracks.kernel.m_test import M_TEST_PIPELINE_VERSION

    assert M_TEST_PIPELINE_VERSION == "v0.7"
    report = generate_report_md(trac, host_repo)
    assert "pipeline_version" in report
    assert f"`{M_TEST_PIPELINE_VERSION}`" in report


# AC-FR0285-01@v0.8 TRACKS-TRACE missing link fails closed at the journey boundary
def test_missing_link_fails_closed_at_journey_boundary(host_repo, trac, event_log):
    """A hole in WRITE -> COLLECT -> red.validated is judged fail-closed.

    The stream reaches PRISM_REVIEW with the red verdict but no COLLECT link
    (an injected/out-of-order stream): the production drive dispatches no
    Prism, lands the auditable ``pipeline incomplete`` attention, and
    ``trac status`` reports the missing link.
    """
    from tracks import paths
    from tracks.store import Store

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        store.append(run_id, "v0.8", "phase0.sealed", {"seal_id": "test-seal"})
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-TEST"})
        store.append(
            run_id,
            "v0.8",
            "test.selected",
            {
                "selection_id": "SEL-HOLE",
                "scope": "red",
                "nodes": ["tests/unit/test_x.py::test_y"],
            },
        )
        # The COLLECT link never landed; the red verdict did (the injected
        # out-of-order hole the chain judge must catch).
        store.append(
            run_id,
            "v0.8",
            "red.validated",
            {
                "status": "valid",
                "findings": [],
                "selection_id": "SEL-HOLE",
                "nodes_blob": ".tracks/runtime/blobs/nodes",
                "outcomes_ref": ".tracks/runtime/blobs/outcomes",
            },
        )
    finally:
        store.close()

    drive = trac("run")
    assert drive.returncode == 0, drive.stderr
    events = event_log(run_id)
    attention = [
        e
        for e in events
        if e["type"] == "attention.required"
        and e["payload"].get("area") == "pipeline"
    ]
    assert attention, "the production drive must judge pipeline incomplete"
    assert attention[-1]["payload"]["reason"] == "pipeline_incomplete"
    assert "COLLECT" in attention[-1]["payload"]["detail"]
    # Prism was never dispatched on the incomplete chain, and no authority
    # verdict exists.
    assert not [e for e in events if e["type"] == "prism.verdict"]
    assert not [
        e
        for e in events
        if e["type"] == "command.issued"
        and e["payload"].get("command", {}).get("kind") == "dispatch_agent"
        and e["payload"]["command"]["params"].get("substate") == "PRISM_REVIEW"
    ]
    status = trac("status").stdout
    assert "pipeline incomplete" in status.lower()
    assert "COLLECT" in status
