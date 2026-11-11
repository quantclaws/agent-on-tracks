"""evidence_digests barrier semantics (post re-walk full evidence)."""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor.release_preview import evidence_digests

CANDIDATE = "S"
IDENTITY = {
    "selection_id": "sel-1",
    "tree": "t",
    "command": ["c"],
    "env": "e",
}


def _ev(seq, event_type, **payload):
    return SimpleNamespace(seq=seq, type=event_type, payload=payload)


def _full(seq):
    return _ev(
        seq,
        "full.executed",
        candidate_sha=CANDIDATE,
        round="FULL_F",
        passed=True,
        full_f_eligible=True,
        execution_commit=CANDIDATE,
        selection_id="sel-1",
        identity=IDENTITY,
        evidence_ids=["e1"],
    )


def _complete_evidence(start):
    """The five required successful evidence keys."""
    return [
        _full(start),
        _ev(start + 1, "ci.run_observed", candidate_sha=CANDIDATE, status="passed", api_verified=True),
        _ev(start + 2, "prism.verdict", candidate_sha=CANDIDATE, scope="verify_final", verdict="pass"),
        _ev(start + 3, "prism.verdict", candidate_sha=CANDIDATE, scope="security", verdict="pass"),
        _ev(start + 4, "security.assessed", candidate_sha=CANDIDATE, status="passed"),
    ]


def test_full_rewalk_after_barrier_stands_without_prebarrier_set():
    """A stale barrier with an EMPTY pre-barrier set means the candidate's
    evidence all landed after the barrier (a complete re-walk) -- the
    current set must stand, not fail as evidence-less."""
    events = [
        _ev(2, "evidence.staled", reason="human_return"),  # barrier
        *_complete_evidence(3),
    ]

    out = evidence_digests(events, CANDIDATE)

    assert out is not None
    assert {
        "full_f",
        "ci",
        "prism.verify_final",
        "prism.security",
        "security",
    } <= set(out)


def test_barrier_with_missing_success_still_fails_closed():
    """A non-empty pre-barrier set that is NOT re-established after the
    barrier still fails closed (never a partial pass)."""
    events = [
        _full(1),
        _ev(2, "evidence.staled", reason="human_return"),
        # only a CI evidence re-established after the barrier: the full_f
        # requirement from before is NOT satisfied -> fail closed
        _ev(3, "ci.run_observed", candidate_sha=CANDIDATE, status="passed", api_verified=True),
    ]

    out = evidence_digests(events, CANDIDATE)

    assert out is None
