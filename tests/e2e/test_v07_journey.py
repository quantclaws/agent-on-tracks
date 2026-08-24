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

from tests.e2e.helpers import walk_to_await_human

pytestmark = pytest.mark.e2e


def _start_v07_run(trac, host_repo):
    """Drive the REAL journey start for v0.7 (init -> start -> triage ->
    reviews -> approve), so the run is ACTIVE and M-TEST RED_CHECK is
    reachable. A run that is never started is a perpetual-Red fixture
    (PRISM-V07-R4-01): `trac run` exits rc=1 'no active run' and no M-TEST
    outlet can ever appear even with a conforming implementation."""
    run_id = walk_to_await_human(trac, version="v0.7")
    assert trac("approve", "--actor", "Aaron").returncode == 0
    return run_id


# AC-FR0256-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0257-04@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0260-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0261-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0263-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
# AC-FR0265-01@v0.7 TRACKS-TRACE v0.7-A journey Phase 0 to closure
def test_v07a_journey_phase0_to_closure(trac, host_repo, event_log):
    """v0.7-A main journey: Phase 0 seal -> M-TEST legal Red -> M-IMPL
    mutation -> candidate-bound closure.

    The run is started through the real journey (init -> start -> triage ->
    reviews -> approval) so the Phase 0 / M-TEST / closure outlets are
    REACHABLE. Legal Red anchor: the v0.7 Phase 0 path (IF-PHASE-001/002/003)
    is not wired, so the run does not emit `phase0.sealed` and the candidate-
    bound closure (IF-CLOSURE-001) is not produced; every assertion fails on
    the absent contract outlet.
    """
    _start_v07_run(trac, host_repo)
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
    observable outlet is the run's adapter execution audit (IF-ADAPTER-002,
    interfaces §4a row 6): selection events carry the adapter identity.

    The run is started through the real journey (R4-01 fixture fix): the
    outlet is REACHABLE. Legal Red anchor: the v0.7 adapter audit is not
    wired, so the selection/adapter outlet is absent — the assertions fail
    legally on the absent contract outlet, never on a fixture defect.
    """
    _start_v07_run(trac, host_repo)
    trac("run")
    events = event_log("latest")
    selected = [e for e in events if e["type"] == "test.selected"]
    # Adapter execution audit: a conforming v0.7 run leaves test.selected
    # records carrying the reference-pytest adapter identity. The assertion
    # is discriminating: it rejects an empty stream AND a record without the
    # adapter identity (PRISM-V07-R4-02 counterexample shape).
    assert selected, (
        "v0.7 RED_CHECK must record test.selected events via the host adapter "
        "(adapter execution audit outlet)"
    )
    for ev in selected:
        payload = ev.get("payload") or {}
        adapter_id = payload.get("adapter")
        assert adapter_id == "reference-pytest", (
            f"v0.7 test execution must record the reference-pytest adapter; "
            f"got {adapter_id!r}"
        )
