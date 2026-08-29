"""T-017 RED: IF-AUTH-002 candidate-identity fail-closed (r12 plan_defect).

Pins the FULL-exposed slice where test_unknown_missing_drift_control_fail_closed
was outside T-012's RGR lineage.  The existing-green path must fail-closed when
the counterexample_experiment carries a caller-supplied explicit
candidate_digest (frozen foreign sha) without a trustworthy experiment binding,
even when target_kill and controls look green.  The public facade
judge_authenticity (frozen) delegates to authenticity_existing — the defect
lives in _verify_kill.

Each assertion fails today because _verify_kill does not check
candidate_digest and returns verified for a foreign digest — the RED must land
on the IF-AUTH-002 contract token (assertion failure), not an assembly error.
"""

from __future__ import annotations

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import judge_authenticity
from tracks.executor.authenticity_existing import _verify_kill

_AC = "AC-FR0261-01@v0.7"
_LEGAL = frozenset({"assertion_failure", "symbol_missing"})
_IF_FROZEN_FOREIGN = "sha256:foreign"
_IF_FROZEN_FOREIGN_2 = "sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _existing_green(counterexample_experiment: dict | None):
    return judge_authenticity(
        _AC,
        "existing",
        bound_nodes=("t1",),
        outcomes={"t1": TestRunResult(node_id="t1", status="passed", detail=None)},
        legal_failure_kinds=_LEGAL,
        counterexample_experiment=counterexample_experiment,
    )


def test_verify_kill_candidate_digest_foreign_fails_closed():
    """IF-AUTH-002: a same-AC counterexample whose experiment carries an
    explicit caller-supplied candidate_digest (frozen foreign) must NOT verify,
    even when target_kill=verified and controls=green."""
    ce = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN,
    }
    kill, reason = _verify_kill(_AC, ce)
    assert kill != "verified", (
        f"IF-AUTH-002 candidate-identity fail-closed: foreign candidate_digest "
        f"{_IF_FROZEN_FOREIGN!r} was verified (reason={reason})"
    )
    assert reason is not None


def test_verify_kill_explicit_candidate_digest_blocks_existing_green():
    """Through the frozen facade: existing behaviour with a foreign
    candidate_digest must remain blocked, not green-allowed."""
    ce = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN,
    }
    judgement = _existing_green(ce)
    assert judgement.green_allowed is False, (
        "IF-AUTH-002: existing green must be blocked when counterexample "
        f"carries explicit foreign candidate_digest {ce['candidate_digest']!r}"
    )
    assert judgement.counterexample_kill != "verified"
    assert judgement.blocked_reason is not None


def test_verify_kill_arbitrary_candidate_digest_is_fail_closed():
    """Any explicit candidate_digest not bound to a trustworthy experiment
    must fail-closed — not only the frozen literal."""
    ce = {
        "ac": _AC,
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN_2,
    }
    kill, _ = _verify_kill(_AC, ce)
    assert kill != "verified"


def test_existing_green_real_red_identity_drift_remains_blocked():
    """The existing-green kill stays blocked when controls and target look
    green but the digest is caller-supplied foreign — exercises the same
    FULL-exposed identity-drift Red that test_unknown_missing_drift_control_
    fail_closed shows on the frozen baseline."""
    ce = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN,
        "status": "passed",
    }
    j = _existing_green(ce)
    assert j.green_allowed is False
    assert j.blocked_reason is not None
