"""E2E: v0.7-A main journey (Phase 0 → candidate-bound closure).

AC-FR0256-01@v0.7 gap ACs bound to real collected nodes,
AC-FR0257-04@v0.7 seal read-only,
AC-FR0260-01@v0.7 new behaviour legal Red,
AC-FR0261-01@v0.7 existing green + counterexample kill,
AC-FR0263-01@v0.7 experiment chain target kill controls green,
AC-FR0265-01@v0.7 closure candidate-bound pass.

Happy path only; error matrix lives in integration (test-plan §1.5/§11.1).
"""

from __future__ import annotations

from tests.hotfix_support import seed_v05_approved_baseline


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
