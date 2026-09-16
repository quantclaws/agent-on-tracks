"""evidence_digests barrier semantics (post re-walk full evidence)."""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor.release_preview import evidence_digests

IDENTITY = {"selection_id": "sel-1", "tree": "t", "command": ["c"], "env": "e"}


def _ev(seq, t, **p):
    return SimpleNamespace(seq=seq, type=t, payload=p)


def test_full_rewalk_after_barrier_stands_without_prebarrier_set():
    """A stale barrier with an EMPTY pre-barrier set means the candidate's
    evidence all landed after the barrier (a complete re-walk) -- the
    current set must stand, not fail as evidence-less."""
    events = [
        _ev(1, "full.executed", candidate_sha="S", passed=True, full_f_eligible=True, execution_commit="S", selection_id="sel-1", identity=IDENTITY, evidence_ids=["e1"]),
        _ev(2, "evidence.staled", reason="human_return"),  # barrier
        # the full re-walk: every evidence event lands AFTER the barrier
        _ev(3, "ci.run_observed", candidate_sha="S", status="passed", api_verified=True),
        _ev(4, "full.executed", candidate_sha="S", passed=True, full_f_eligible=True, execution_commit="S", selection_id="sel-1", identity=IDENTITY, evidence_ids=["e1"]),
    ]
    out = evidence_digests(events, "S")
    assert out, "the post-barrier evidence set must satisfy the digest join"


def test_barrier_with_missing_success_still_fails_closed():
    """A non-empty pre-barrier set that is NOT re-established after the
    barrier still fails closed (never a partial pass)."""
    events = [
        _ev(1, "full.executed", candidate_sha="S", passed=True, full_f_eligible=True, execution_commit="S", selection_id="sel-1", identity=IDENTITY, evidence_ids=["e1"]),
        _ev(2, "evidence.staled", reason="human_return"),
        # only a CI evidence re-established after the barrier: the full_f
        # requirement from before is NOT satisfied -> fail closed
        _ev(3, "ci.run_observed", candidate_sha="S", status="passed", api_verified=True),
    ]
    # NOTE: the pre-barrier set ({full_f}) is re-established only if the
    # post-barrier full_f ALSO succeeds; assert the fail-closed contract:
    out = evidence_digests(events, "S")
    assert out is None
