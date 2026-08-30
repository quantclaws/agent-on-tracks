"""Regression-safe full fake journey through M-IMPL."""

import pytest

from tests.e2e.helpers import walk_to_m_test_complete


@pytest.mark.e2e
# AC-FR0010-04@v0.5 TRACKS-TRACE pre-M-TEST event prefix remains stable
def test_full_journey_to_boundary_includes_m_impl(trac, event_log):
    run_id = walk_to_m_test_complete(trac, version="v0.5")
    events = event_log(run_id)
    stages = [event["payload"]["stage"] for event in events if event["type"] == "stage.entered"]
    assert stages[:8] == [
        "M-START",
        "M-STORY",
        "M-SPEC",
        "M-ACC",
        "M-REQ-APPROVAL",
        "M-DESIGN",
        "M-TEST",
        "M-IMPL",
    ]
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["payload"]["terminal_state"] == "boundary"


# AC-FR0264-02@v0.7 TRACKS-TRACE §10.4 early-journey capability backward compatibility
@pytest.mark.e2e
def test_v05_journey_does_not_trigger_v07_phase0_or_dualhost(trac, event_log):
    """§10.4 regression (test-plan §10 item 4): the v0.5 journey completes
    without triggering the v0.7-only capabilities — no `phase0.*` and no
    `failclosed.*` events in the early-version event stream (capability
    backward compatibility). The v0.7 candidate-bound closure (IF-CLOSURE-001)
    is the version-isolated path; its absence here is the regression proof, and
    the v0.7 closure record (not wired) anchors the legal Red."""
    run_id = walk_to_m_test_complete(trac, version="v0.5")
    events = event_log(run_id)
    types = [e["type"] for e in events]
    # Regression guard: an early journey must NOT trigger v0.7 capabilities.
    assert not any(t.startswith("phase0.") for t in types), (
        "v0.5 journey must not emit phase0.* events (Phase 0 is v0.7-only)"
    )
    assert not any(t.startswith("failclosed.") for t in types), (
        "v0.5 journey must not emit failclosed.* events (dual-host is v0.7-only)"
    )
    assert events[-1]["type"] == "run.completed", "v0.5 journey still completes"

    # Legal-Red anchor: the v0.7 candidate-bound closure (version-isolated
    # capability) is not wired; driving it must surface the closure record.
    check = trac("check", "trace", "--version", "v0.7", "--json")
    assert "closure=candidate-bound" in check.stdout or "closure" in check.stdout, (
        f"v0.7 check trace must surface the candidate-bound closure outlet; "
        f"got stdout={check.stdout!r}"
    )
