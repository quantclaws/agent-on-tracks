"""RED/GREEN/refactor/review/diagnose contracts at public event outlets."""

import pytest

from tests.integration.v05_contract_helpers import events_of, run_m_impl_journey


@pytest.mark.integration
# AC-FR0070-02@v0.5 TRACKS-TRACE RED is unit-test-only and audited
# AC-FR0080-02@v0.5 TRACKS-TRACE illegal Red returns to Devon
# AC-FR0090-01@v0.5 TRACKS-TRACE Prism reviews B through R
# AC-FR0090-02@v0.5 TRACKS-TRACE Prism Red verdict binds attempt
# AC-FR0100-01@v0.5 TRACKS-TRACE GREEN restores immutable R tree
# AC-FR0140-01@v0.5 TRACKS-TRACE task review checks range and budget
# AC-FR0140-02@v0.5 TRACKS-TRACE final Prism routes full range
# AC-FR0140-03@v0.5 TRACKS-TRACE completion follows final pass
def test_rgr_phase_events_and_audit_evidence(trac, event_log):
    _, _, events = run_m_impl_journey(trac, event_log)
    outcomes = events_of(events, "outcome.received")
    devon = [event for event in outcomes if event["payload"].get("role") == "devon"]
    assert {event["payload"].get("phase") for event in devon} >= {
        "red", "green", "refactor"
    }
    assert all(event["payload"].get("audit_evidence") for event in devon)
    red = events_of(events, "red.checkpointed")
    green = events_of(events, "green.committed")
    done = events_of(events, "task.completed")
    assert red and green and done
    assert red[0]["seq"] < green[0]["seq"] < done[0]["seq"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("classification", "expected_target", "shield_fix"),
    [
        ("impl_defect", "M-IMPL", False),
        ("test_defect", "M-IMPL", True),
        ("stub_gap", "M-DESIGN", False),
        ("ac_gap", "M-ACC", False),
        ("spec_gap", "M-SPEC", False),
    ],
)
# AC-FR0150-01@v0.5 TRACKS-TRACE diagnose routes without Human
# AC-FR0150-02@v0.5 TRACKS-TRACE failure reason is a closed set
# AC-FR0150-03@v0.5 TRACKS-TRACE Shield fix commits frozen tests
# AC-FR0150-04@v0.5 TRACKS-TRACE rollback supersedes old evidence
# AC-FR0160-02@v0.5 TRACKS-TRACE island failure routes diagnose or planning
def test_diagnose_and_shield_fix_public_routes(
    trac, event_log, classification, expected_target, shield_fix
):
    _, _, events = run_m_impl_journey(
        trac,
        event_log,
        simulate=(
            "devon:RED=fail|ok;"
            f"diagnose:classification={classification};"
            "shield:SHIELD_FIX=ok"
        ),
    )
    failures = events_of(events, "verdict.failed")
    allowed = {
        "red_invalid", "regression", "budget", "island", "scope",
        "test_defect", "impl_defect", "stub_gap", "ac_gap", "spec_gap",
    }
    classified = [
        event for event in failures
        if event["payload"].get("check") == classification
    ]
    assert classified
    assert all(event["payload"].get("check") in allowed for event in failures)
    shield_commits = events_of(events, "test.committed")
    assert bool(shield_commits) is shield_fix
    assert all(event["payload"]["test_count"] > 0 for event in shield_commits)
    assert classified[-1]["payload"].get("target_stage") == expected_target
    assert not any(
        event["type"].startswith("human.")
        for event in events
        if event["seq"] > classified[0]["seq"]
        and classification in {"test_defect", "impl_defect", "stub_gap"}
    )
