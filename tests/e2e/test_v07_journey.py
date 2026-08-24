"""E2E: v0.7-A main journey (Phase 0 → candidate-bound closure).

AC-FR0256-01@v0.7 gap ACs bound to real collected nodes,
AC-FR0257-04@v0.7 seal read-only,
AC-FR0260-01@v0.7 new behaviour legal Red,
AC-FR0261-01@v0.7 existing green + counterexample kill,
AC-FR0263-01@v0.7 experiment chain target kill controls green,
AC-FR0264-02@v0.7 reference adapter drives the v0.7 execution path,
AC-FR0265-01@v0.7 closure candidate-bound pass.

Happy path only; error matrix lives in integration (test-plan §1.5/§11.1).
"""

from __future__ import annotations

import pytest

from tests.hotfix_support import seed_v05_approved_baseline

pytestmark = pytest.mark.e2e



# AC-FR0256-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0257-04@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0260-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0261-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0263-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0265-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
def test_v07a_journey_phase0_to_closure(trac, host_repo, event_log):
    """v0.7-A main journey: Phase 0 seal → M-TEST legal Red → M-IMPL mutation → closure.

    Legal Red anchor: the v0.7 Phase 0 path (IF-PHASE-001/002/003) is not yet
    wired into the run loop, so `trac run` does not emit `phase0.sealed` and the
    candidate-bound closure (IF-CLOSURE-001) is not produced; every assertion
    below fails on the absence of the contract outlet.
    """
    seed_v05_approved_baseline(host_repo, version="v0.7")

    trac("run")
    run_id = "latest"
    events = [e["type"] for e in event_log(run_id)]

    # Phase 0 binding + seal (AC-FR0256-01 / AC-FR0257-04).
    assert "phase0.baseline_repaired" in events, (
        "Phase 0 must emit phase0.baseline_repaired for the three gap ACs"
    )
    assert "phase0.sealed" in events, "Phase 0 must seal the v0.6 baseline"

    # M-TEST authenticity legal Red for new behaviour (AC-FR0260-01).
    assert "authenticity.judged" in events, (
        "M-TEST must emit authenticity.judged for new-behaviour legal Red"
    )

    # M-IMPL mutation experiment chain (AC-FR0263-01).
    assert "mutation.experiment" in events, (
        "M-IMPL must emit mutation.experiment (target kill, controls green)"
    )

    # Candidate-bound closure (AC-FR0265-01): trac check trace --version v0.7.
    check = trac("check", "trace", "--version", "v0.7", "--json")
    assert "closure=candidate-bound" in check.stdout, (
        f"closure must be candidate-bound; got: {check.stdout!r}"
    )


# AC-FR0264-02@v0.7 TRACKS-TRACE reference adapter drives the v0.7 execution path
def test_v07a_journey_uses_reference_adapter(trac, host_repo, event_log):
    """AC-FR0264-02 (e2e): the v0.7-A RED_CHECK execution path runs collected
    and selected nodes through the reference pytest adapter (same-in/out as
    the v0.6 pytest path, FR-0264 refactor not a behaviour change). The
    observable outlet is the run's test execution audit: `test.selected`
    events carry the adapter identity (reference-pytest) per interfaces §4a
    row 6 / §1h project contract.

    Legal Red anchor (IF-ADAPTER-002): the v0.7 run does not yet issue
    test.selected records (or records without the adapter identity), so the
    discriminating assertions fail on the absent adapter-audit outlet.
    """
    seed_v05_approved_baseline(host_repo, version="v0.7")
    trac("run")
    events = event_log("latest")
    selected = [e for e in events if e["type"] == "test.selected"]
    # Non-empty + adapter identity: a conforming v0.7 RED_CHECK records the
    # reference adapter on the selection (never a vacuous pass on emptiness).
    assert selected, (
        "v0.7 RED_CHECK must record test.selected events via the host adapter "
        "(adapter execution audit outlet)"
    )
    for ev in selected:
        adapter_id = (ev.get("payload") or {}).get("adapter")
        assert adapter_id == "reference-pytest", (
            f"v0.7 test execution must record the reference-pytest adapter; "
            f"got {adapter_id!r}"
        )
