"""IF-AUTH-002 existing-green counterexample kill (T-012 RED unit).

Pins the existing-green authenticity contract from interfaces.md §1f and
AC-FR0261-01/02 *before* T-012 lands ``authenticity_existing.py``:

- ``judge_authenticity(category="existing")`` delegates to the
  ``authenticity_existing`` module behind the frozen facade; it must NOT
  inline existing-branch logic in ``authenticity.py`` (T-009 freeze).
- Existing behaviour is green-allowed only with a verified AC-specific
  counterexample kill (same-AC ``mutation.experiment(passed)``) — AC-FR0261-01.
- A missing counterexample kill, a cross-AC/foreign counterexample, or a
  broad mutation blocks the existing-green verdict (fail-closed, no
  exemption path) — AC-FR0261-01/02, AC-FR0261-03 role separation.

Each assertion fails today: ``authenticity_existing`` is not implemented, so
the facade raises ``NotImplementedError("IF-AUTH-002")`` — the M-IMPL RED on
the IF-AUTH-002 contract token.
"""

from __future__ import annotations

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import (
    AuthenticityJudgement,
    judge_authenticity,
)

_GREEN = {
    "t1": TestRunResult(node_id="t1", status="passed", detail=None),
}
_LEGAL = frozenset({"assertion_failure", "symbol_missing"})
_AC = "AC-FR0261-01@v0.7"


def _judge_existing(
    outcomes: dict,
    counterexample_experiment: dict | None,
) -> AuthenticityJudgement:
    """Judge an existing-behaviour outcome via the frozen facade."""
    return judge_authenticity(
        _AC,
        "existing",
        bound_nodes=("t1",),
        outcomes=outcomes,
        legal_failure_kinds=_LEGAL,
        counterexample_experiment=counterexample_experiment,
    )


# AC-FR0261-01@v0.7 TRACKS-TRACE existing green + same-AC kill verified

def test_existing_green_with_same_ac_kill_verified():
    """Existing green + same-AC counterexample passed -> kill verified."""
    counterexample = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "status": "passed",
        "target_kill": "verified",
        "controls": "green",
    }
    judgement = _judge_existing(_GREEN, counterexample)
    assert judgement.ac == _AC
    assert judgement.category == "existing"
    assert judgement.green_allowed is True
    assert judgement.counterexample_kill == "verified"
    assert judgement.blocked_reason is None


# AC-FR0261-01@v0.7 TRACKS-TRACE missing kill -> blocked

def test_existing_green_missing_kill_blocked():
    """Existing green with NO counterexample kill must be blocked."""
    judgement = _judge_existing(_GREEN, None)
    assert judgement.green_allowed is False
    assert judgement.counterexample_kill in ("missing", "none")
    assert judgement.blocked_reason is not None


# AC-FR0261-01/02@v0.7 TRACKS-TRACE foreign/cross-AC kill fail-closed

def test_existing_green_cross_ac_kill_blocked():
    """A counterexample kill for a different AC must NOT green an existing
    behaviour (cross-AC borrowing is fail-closed)."""
    counterexample = {
        "ac": "AC-OTHER@v0.7",
        "if_ref": "IF-MUTATION-002",
        "status": "passed",
        "target_kill": "verified",
        "controls": "green",
    }
    judgement = _judge_existing(_GREEN, counterexample)
    assert judgement.green_allowed is False
    assert judgement.counterexample_kill != "verified"
    assert judgement.blocked_reason is not None


def test_existing_green_not_passed_experiment_blocked():
    """A same-AC counterexample experiment that did NOT pass must block."""
    counterexample = {
        "ac": _AC,
        "if_ref": "IF-MUTATION-002",
        "status": "blocked",
        "target_kill": "survived",
        "controls": "green",
    }
    judgement = _judge_existing(_GREEN, counterexample)
    assert judgement.green_allowed is False
    assert judgement.counterexample_kill != "verified"
    assert judgement.blocked_reason is not None


# AC-FR0261-02@v0.7 TRACKS-TRACE AC-specific binding

def test_existing_green_broad_mutation_blocked():
    """A broad counterexample (no single AC binding) must be blocked."""
    counterexample = {
        "broad": True,
        "ac": None,
        "if_ref": "IF-MUTATION-002",
        "status": "passed",
    }
    judgement = _judge_existing(_GREEN, counterexample)
    assert judgement.green_allowed is False
    assert judgement.counterexample_kill != "verified"
    assert judgement.blocked_reason is not None
