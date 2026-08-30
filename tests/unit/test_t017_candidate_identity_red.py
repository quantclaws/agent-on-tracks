"""T-017 RED: IF-AUTH-002 candidate-identity fail-closed (r12 plan_defect).

Pins the FULL-exposed slice that ``test_unknown_missing_drift_control_fail_closed``
shows on the frozen baseline: the existing-green path must fail-closed when the
``counterexample_experiment`` carries a caller-supplied explicit
``candidate_digest`` (frozen foreign sha) without a trustworthy experiment
binding, even when ``target_kill`` and ``controls`` look green.  The public
facade ``judge_authenticity`` (frozen, T-009) delegates to
``authenticity_existing``; the defect lives in ``_verify_kill`` (T-017 owns
``tracks/executor/authenticity_existing.py``, never ``authenticity.py``).

The candidate-digest target assertions fail against the pre-fix ``_verify_kill``
(T-012 baseline): it did not check ``candidate_digest`` and returned
``verified`` for a foreign digest, leaking the identity-drift Red as a green
allow.  The RED lands on the IF-AUTH-002 contract token (assertion failure),
not an assembly error.  ``test_verify_kill_trusted_experiment_still_verifies``
is a preserved-contract guard (AC-FR0261-01) and intentionally passes on the
pre-fix baseline too.
"""

from __future__ import annotations

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import judge_authenticity
from tracks.executor.authenticity_existing import _verify_kill

_AC = "AC-FR0261-01@v0.7"
_LEGAL = frozenset({"assertion_failure", "symbol_missing"})
_IF_FROZEN_FOREIGN = "sha256:foreign"
_IF_FROZEN_FOREIGN_2 = "sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
_UNTRUSTED_REASON = "candidate_digest_untrusted"


class _CallerSuppliedDigestMapping(dict):
    """A mapping whose membership predicate must not authenticate its value."""

    def __contains__(self, key: object) -> bool:
        if key == "candidate_digest":
            return False
        return super().__contains__(key)


def _existing_green(counterexample_experiment: dict | None):
    """Judge an existing-green outcome via the frozen facade (IF-AUTH-002)."""
    return judge_authenticity(
        _AC,
        "existing",
        bound_nodes=("t1",),
        outcomes={"t1": TestRunResult(node_id="t1", status="passed", detail=None)},
        legal_failure_kinds=_LEGAL,
        counterexample_experiment=counterexample_experiment,
    )


# AC-FR0261-01@v0.7 TRACKS-TRACE IF-AUTH-002 candidate-identity fail-closed

def test_verify_kill_trusted_experiment_still_verifies():
    """IF-AUTH-002 preserved contract (AC-FR0261-01): a same-AC counterexample
    kill produced by a trustworthy experiment (no caller-supplied
    candidate_digest) with target_kill=verified and controls=green still
    verifies and green-allows the existing behaviour.  Guards the identity-
    drift fail-closed fix (T-017) against over-blocking the preserved path."""
    ce = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "target_kill": "verified",
        "controls": "green",
    }
    kill, reason = _verify_kill(_AC, ce)
    assert kill == "verified", (
        "IF-AUTH-002 preserved contract: a trustworthy same-AC kill must "
        f"verify (failed with reason={reason})"
    )
    assert reason is None
    judgement = _existing_green(ce)
    assert judgement.green_allowed is True, (
        "IF-AUTH-002 preserved contract: trusted experiment green-allows the "
        "existing behaviour"
    )
    assert judgement.counterexample_kill == "verified"
    assert judgement.blocked_reason is None


def test_verify_kill_candidate_digest_foreign_fails_closed():
    """IF-AUTH-002: a same-AC counterexample whose experiment carries an
    explicit caller-supplied candidate_digest (frozen foreign) must NOT verify,
    even when target_kill=verified and controls=green (identity drift)."""
    ce = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN,
    }
    kill, reason = _verify_kill(_AC, ce)
    assert kill == "missing", (
        "IF-AUTH-002 candidate-identity fail-closed: foreign candidate_digest "
        f"{_IF_FROZEN_FOREIGN!r} was verified (reason={reason})"
    )
    assert reason == _UNTRUSTED_REASON, (
        f"untrusted candidate_digest must report {_UNTRUSTED_REASON!r}, got {reason!r}"
    )


def test_verify_kill_explicit_candidate_digest_blocks_existing_green():
    """Through the frozen facade: existing behaviour with a foreign
    candidate_digest must stay blocked (counterexample_kill=missing), never
    green-allowed."""
    ce = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN,
    }
    judgement = _existing_green(ce)
    assert judgement.green_allowed is False, (
        "IF-AUTH-002: existing green must be blocked when the counterexample "
        f"carries an explicit foreign candidate_digest {ce['candidate_digest']!r}"
    )
    assert judgement.counterexample_kill == "missing"
    assert judgement.blocked_reason == _UNTRUSTED_REASON


def test_verify_kill_arbitrary_candidate_digest_is_fail_closed():
    """Any explicit candidate_digest not bound to a trustworthy experiment
    must fail-closed — not only the frozen literal."""
    ce = {
        "ac": _AC,
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN_2,
    }
    kill, reason = _verify_kill(_AC, ce)
    assert kill == "missing"
    assert reason == _UNTRUSTED_REASON, (
        f"arbitrary foreign candidate_digest must fail closed with "
        f"{_UNTRUSTED_REASON!r}, got {reason!r}"
    )


def test_existing_green_real_red_identity_drift_remains_blocked():
    """The existing-green kill stays blocked when controls and target look
    green but the digest is caller-supplied foreign — exercises the same
    FULL-exposed identity-drift Red that ``test_unknown_missing_drift_control_``
    ``fail_closed`` shows on the frozen baseline, even when the experiment
    envelope also claims status=passed."""
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
    assert j.counterexample_kill == "missing"
    assert j.blocked_reason == _UNTRUSTED_REASON


def test_candidate_digest_precedes_scope_check():
    """Candidate-digest fail-closed takes precedence over tests-scope.

    Even when allowed_change_scope touches tests/, a foreign candidate_digest
    must still be reported as candidate_digest_untrusted, not tests_in_scope.
    Exercises the ordering contract of _verify_kill (IF-AUTH-002 + IF-MUTATION-002).
    """
    ce = {
        "ac": _AC,
        "target_kill": "verified",
        "controls": "green",
        "candidate_digest": _IF_FROZEN_FOREIGN,
        "allowed_change_scope": ["tracks/executor/authenticity_existing.py", "tests/unit/test_fake.py"],
    }
    kill, reason = _verify_kill(_AC, ce)
    assert kill == "missing"
    assert reason == _UNTRUSTED_REASON, (
        f"candidate_digest must take precedence over tests-scope, got {reason!r}"
    )


def test_verify_kill_does_not_trust_caller_digest_membership_claim():
    """An untrusted Mapping cannot hide an explicit foreign digest from the
    fail-closed check by lying about membership (IF-AUTH-002)."""
    ce = _CallerSuppliedDigestMapping(
        {
            "ac": _AC,
            "if_ref": "IF-MUTATION-002",
            "target_kill": "verified",
            "controls": "green",
            "candidate_digest": _IF_FROZEN_FOREIGN,
        }
    )
    kill, reason = _verify_kill(_AC, ce)
    assert kill == "missing", (
        "IF-AUTH-002 candidate-identity fail-closed: a caller-controlled "
        "membership claim must not make a foreign candidate_digest verified"
    )
    assert reason == _UNTRUSTED_REASON


def test_facade_does_not_trust_caller_digest_membership_claim():
    """Through the frozen facade: a membership-lying Mapping carrying a
    foreign candidate_digest must leave the existing behaviour blocked
    (IF-AUTH-002 end-to-end, identity drift)."""
    ce = _CallerSuppliedDigestMapping(
        {
            "ac": _AC,
            "if_ref": "IF-MUTATION-002",
            "target_kill": "verified",
            "controls": "green",
            "candidate_digest": _IF_FROZEN_FOREIGN,
        }
    )
    judgement = _existing_green(ce)
    assert judgement.green_allowed is False, (
        "IF-AUTH-002: the facade must not green-allow an existing behaviour "
        "whose counterexample hides a foreign candidate_digest behind a "
        "caller-controlled membership claim"
    )
    assert judgement.counterexample_kill == "missing"
    assert judgement.blocked_reason == _UNTRUSTED_REASON
