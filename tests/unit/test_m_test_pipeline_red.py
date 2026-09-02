"""T-038 RED: M-TEST pipeline regression kernel pins (FR-0285, IF-PIPELINE-001).

Pins the kernel-side slices of the single-authority test pipeline
(WRITE → COLLECT → red.validated → prism.verdict) in tracks/kernel/m_test.py:

- AC-FR0285-01: Prism consumes the red.validated evidence — the PRISM_REVIEW
  dispatch envelope must carry the red-evidence reference, and the review
  route must fail closed (never dispatch Prism) when the current-tree red
  evidence was NOT validated (red_validated=False), so an out-of-order or
  injected event stream can never reach Prism without a valid RED.
- AC-FR0285-01 chain ordering (preserved guards): valid RED_CHECK lands in
  PRISM_REVIEW; invalid RED_CHECK lands in DIAGNOSE and never dispatches
  Prism.
- AC-FR0285-02 (preserved guard): a Shield self-report ("selfcheck passed")
  is never authority — the WRITE outcome only moves WRITE→COLLECT; authority
  stays with the Runtime RED_CHECK.

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).
"""

from __future__ import annotations

from tracks.kernel.m_test import _decide_m_test, _m_test_review_route, _on_red_validated
from tracks.kernel.machine import State


def _prism_review_state(red_validated: bool) -> State:
    s = State(run_id="RUN", stage="M-TEST", substate="PRISM_REVIEW")
    s.red_validated = red_validated
    s.red_findings = {"cases": ["t1"]} if red_validated else None
    return s


# AC-FR0285-01@v0.8 TRACKS-TRACE IF-PIPELINE-001 prism consumes red evidence
def test_prism_review_requires_red_validated():
    """AC-FR0285-01: the review route must FAIL CLOSED when the red evidence
    was not validated — an un-validated PRISM_REVIEW state (out-of-order /
    injected stream) must never dispatch Prism."""
    s = _prism_review_state(red_validated=False)
    cmd = _m_test_review_route(s)
    assert cmd is None or cmd.kind != "dispatch_agent", (
        f"assertion failure: Prism must never be dispatched without a validated "
        f"red evidence (red_validated=False), got {cmd!r}"
    )
    decided = _decide_m_test(s, "PRISM_REVIEW")
    assert decided is None or decided.kind != "dispatch_agent", (
        f"assertion failure: decide(PRISM_REVIEW) without red_validated must not "
        f"dispatch Prism, got {decided!r}"
    )


# AC-FR0285-01@v0.8 TRACKS-TRACE IF-PIPELINE-001 prism envelope carries red evidence
def test_prism_dispatch_carries_red_evidence():
    """AC-FR0285-01: with red evidence validated, the Prism dispatch envelope
    must carry the red.validated reference (findings + validated marker) so
    the verdict consumes the red evidence (isolated counterexample kill)."""
    s = _prism_review_state(red_validated=True)
    s.reviewer_dispatched = False
    cmd = _m_test_review_route(s)
    assert cmd is not None and cmd.kind == "dispatch_agent", (
        f"assertion failure: a validated red must dispatch Prism, got {cmd!r}"
    )
    assignment = cmd.params.get("assignment", {})
    red_evidence = cmd.params.get("red_evidence") or assignment.get("red_evidence")
    assert red_evidence is not None, (
        f"assertion failure: Prism dispatch must carry the red.validated evidence "
        f"reference, got params={cmd.params!r}"
    )
    assert red_evidence.get("red_validated") is True
    assert red_evidence.get("findings") == {"cases": ["t1"]}


# AC-FR0285-01@v0.8 TRACKS-TRACE IF-PIPELINE-001 chain ordering guard
def test_red_check_routes_preserved():
    """Preserved guard: valid RED_CHECK -> PRISM_REVIEW; invalid -> DIAGNOSE
    (and the DIAGNOSE state must not be Prism-dispatchable)."""
    s = State(run_id="RUN", stage="M-TEST", substate="RED_CHECK")
    _on_red_validated(s, {"status": "valid", "findings": {"cases": []}}, None)
    assert s.substate == "PRISM_REVIEW" and s.red_validated is True
    s2 = State(run_id="RUN", stage="M-TEST", substate="RED_CHECK")
    _on_red_validated(s2, {"status": "invalid", "findings": None}, None)
    assert s2.substate == "DIAGNOSE" and s2.red_validated is False
    decided = _decide_m_test(s2, "DIAGNOSE")
    assert decided is None or decided.kind != "dispatch_agent" or decided.params.get(
        "role"
    ) != "prism", (
        f"assertion failure: an invalid red must never dispatch Prism, got {decided!r}"
    )


# AC-FR0285-02@v0.8 TRACKS-TRACE IF-PIPELINE-001 selfcheck never authority
def test_shield_selfcheck_never_reaches_prism():
    """AC-FR0285-02: a Shield self-report (selfcheck) is not authority — the
    WRITE outcome only moves WRITE→COLLECT; the state can never reach
    PRISM_REVIEW without the Runtime red.validated."""
    from tracks.kernel.m_test import _on_m_test_outcome_done

    s = State(run_id="RUN", stage="M-TEST", substate="WRITE")
    _on_m_test_outcome_done(s)
    assert s.substate == "COLLECT", (
        f"assertion failure: Shield self-report only advances to COLLECT "
        f"(Runtime authority next), got {s.substate!r}"
    )
    assert s.red_validated is False
    decided = _decide_m_test(s, "COLLECT")
    assert decided is not None and decided.kind == "collect_tests", (
        f"assertion failure: COLLECT routes to the Runtime collector "
        f"(red.validated authority), got {decided!r}"
    )
