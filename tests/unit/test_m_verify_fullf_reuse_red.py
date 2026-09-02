"""T-013 RED: FULL_F reuse判定 (FR-0268, IF-VERIFY-002/IF-EVIDENCE-001).

Pins the still-undelivered slices of tracks/executor/m_verify.py:
- AC-FR0268-01: undrifted identity reuses full_f
- AC-FR0268-02: drift or stale reruns full
- AC-FR0268-03: stale evidence not reused even if SHA matches

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).

IF-VERIFY-002: judge_full_f_reuse
IF-EVIDENCE-001: ReuseDecision identity_basis / stale handling
"""

from __future__ import annotations

from tracks.executor.m_verify import judge_full_f_reuse


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-VERIFY-002 undrifted reuses
def test_undrifted_identity_reuses_full_f():
    """AC-FR0268-01: candidate undrifted, identity quadruple matches
    FULL_F identity_basis and no STALE -> reuse."""
    try:
        decision = judge_full_f_reuse(
            candidate_sha="a" * 40,
            full_f_evidence={"identity_basis": ("a", "b", "c", "d")},
            identity_quadruple={"tree": "a", "command": "b", "env": "c", "selection_id": "d"},
            stale_marks=(),
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_full_f_reuse not implemented for undrifted reuse"
        ) from err
    assert decision.decision == "reuse", (
        f"assertion failure: expected reuse for undrifted identity, got {decision.decision!r}"
    )
    assert decision.reason == "reuse_full_f", (
        f"assertion failure: expected reason reuse_full_f, got {decision.reason!r}"
    )
    assert decision.identity_basis == ("a", "b", "c", "d")


# AC-FR0268-02@v0.8 TRACKS-TRACE IF-VERIFY-002 drift reruns
def test_drift_or_stale_reruns_full():
    """AC-FR0268-02: drift / identity mismatch / STALE -> rerun with
    full_executed, reason in drift/stale/identity_mismatch."""
    # drift via stale_marks
    try:
        d1 = judge_full_f_reuse(
            candidate_sha="b" * 40,
            full_f_evidence={"identity_basis": ("x",)},
            identity_quadruple={"tree": "y", "command": "y", "env": "y", "selection_id": "y"},
            stale_marks=("STALE",),
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_full_f_reuse not implemented for stale drift"
        ) from err
    assert d1.decision == "rerun"
    assert d1.reason in ("drift", "stale", "identity_mismatch")

    # identity mismatch
    try:
        d2 = judge_full_f_reuse(
            candidate_sha="c" * 40,
            full_f_evidence={"identity_basis": ("a", "b", "c", "d")},
            identity_quadruple={"tree": "a", "command": "b", "env": "X", "selection_id": "d"},
            stale_marks=(),
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_full_f_reuse not implemented for identity mismatch"
        ) from err
    assert d2.decision == "rerun"
    assert d2.reason == "identity_mismatch"


# AC-FR0268-03@v0.8 TRACKS-TRACE IF-EVIDENCE-001 stale not reused
def test_stale_evidence_not_reused():
    """AC-FR0268-03: STALE-marked FULL_F must not be reused even if
    candidate SHA matches and quadruple is identical."""
    try:
        decision = judge_full_f_reuse(
            candidate_sha="d" * 40,
            full_f_evidence={"identity_basis": ("a", "b", "c", "d"), "stale": True},
            identity_quadruple={"tree": "a", "command": "b", "env": "c", "selection_id": "d"},
            stale_marks=("STALE",),
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: judge_full_f_reuse not implemented for stale evidence"
        ) from err
    assert decision.decision == "rerun", (
        f"assertion failure: stale evidence must not reuse, got {decision.decision!r}"
    )
    assert decision.reason == "stale", (
        f"assertion failure: expected reason stale for STALE evidence, got {decision.reason!r}"
    )
    # stale evidence's identity must not be returned as passed basis
    assert decision.decision != "reuse"
