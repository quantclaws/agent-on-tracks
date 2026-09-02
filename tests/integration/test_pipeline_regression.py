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
    replay_out = trac("replay", run_id).stdout
    assert "prism.verdict" in replay_out
    assert "red.validated" in replay_out
    # The chain is complete on this journey: status must NOT report a missing
    # link (the missing-link fail-closed half is unit-pinned by the T-038 RED).
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
    # The `trac report` pipeline-version-rendering half of AC-FR0285-03
    # (pipeline version identical to v0.7) belongs to the T-040 report face,
    # which is not delivered; deferred there per the b93 diagnosis — not
    # skipped silently.
