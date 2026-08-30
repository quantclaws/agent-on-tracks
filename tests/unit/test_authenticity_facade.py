"""IF-AUTH-001/002 judge_authenticity facade delegation (T-009/T-012 RED unit).

Pins the facade freeze contract from interfaces.md §1f and the T-009/T-012
task descriptions:

- The facade's ``existing`` branch must NOT inline its own logic; it must
  delegate to the ``authenticity_existing`` function (IF-AUTH-002, T-012).
- After T-012 lands, ``authenticity_existing`` implements the existing-green
  counterexample kill, so ``judge_authenticity`` with ``category="existing"``
  returns an ``AuthenticityJudgement`` (it no longer raises).
"""

from __future__ import annotations

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import AuthenticityJudgement, judge_authenticity


def _assert_existing_delegates(
    outcomes: dict,
    counterexample_experiment: dict | None = None,
) -> None:
    """Assert that judge_authenticity(category="existing") returns a real
    AuthenticityJudgement (facade delegates to authenticity_existing)."""
    judgement = judge_authenticity(
        "AC-FR0261-01@v0.7", "existing",
        bound_nodes=("t1",),
        outcomes=outcomes,
        legal_failure_kinds={"assertion_failure", "symbol_missing"},
        counterexample_experiment=counterexample_experiment,
    )
    assert isinstance(judgement, AuthenticityJudgement)
    assert judgement.category == "existing"


# AC-FR0260-04@v0.7 TRACKS-TRACE existing branch must NOT inline logic

def test_existing_branch_delegates_on_passed_outcome():
    _assert_existing_delegates(
        {"t1": TestRunResult(node_id="t1", status="passed", detail=None)},
    )


def test_existing_branch_delegates_on_failed_outcome():
    _assert_existing_delegates(
        {"t1": TestRunResult(node_id="t1", status="failed", detail="assert 1 == 2")},
    )


def test_existing_branch_delegates_with_unrelated_failures():
    _assert_existing_delegates({
        "t1": TestRunResult(node_id="t1", status="passed", detail=None),
        "t2": TestRunResult(node_id="t2", status="failed", detail="assert 0 == 1"),
    })


def test_existing_branch_delegates_with_broad_mutation():
    _assert_existing_delegates(
        {"t1": TestRunResult(node_id="t1", status="passed", detail=None)},
        counterexample_experiment={"broad": True, "ac": "different-ac"},
    )
