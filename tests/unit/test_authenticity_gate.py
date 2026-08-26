"""IF-AUTH-001 new-behaviour + IF-AUTH-002 existing-behaviour authenticity
gate (T-009/T-012 RED unit).

Pins the authenticity facade declared in interfaces.md §1f:

- ``classify_behaviour`` separates "new" (not in baseline) from "existing"
  (in baseline) behaviours using the frozen baseline AC/IF sets
  (AC-FR0260-01: new behaviour requires legal Red on frozen baseline;
  AC-FR0260-04: coexists with D-41 selection semantics).
- ``judge_authenticity`` evaluates the Red outcome: legal Red (assertion/
  symbol-missing stub tokens) for new behaviours, or illegal Red (collection/
  unexpected-pass/irrelevant) for unrelated downstream failures
  (AC-FR0260-02: unrelated failures isolated, AC-FR0260-05: broad mutation
  irrelevant).
- ``judge_authenticity`` for ``category="existing"`` delegates to the
  ``authenticity_existing`` module (IF-AUTH-002, T-012) which returns an
  ``AuthenticityJudgement``.

The ``classify_behaviour`` and new-branch ``judge_authenticity`` tests pass;
the existing-branch test asserts delegation produces a judgement (it fails
until T-012 lands ``authenticity_existing``, which the facade imports lazily).
"""

from __future__ import annotations

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import (
    AuthenticityJudgement,
    classify_behaviour,
    judge_authenticity,
)

# AC-FR0260-01@v0.7 TRACKS-TRACE new behaviour on frozen baseline

_BASELINE_ACS = frozenset({"AC-FR0250-01@v0.6", "AC-FR0250-03@v0.6"})
_BASELINE_IFS = frozenset({"IF-MTEST-001", "IF-SELECT-001"})


def test_classify_behaviour_new_when_ac_not_in_baseline():
    category = classify_behaviour(
        "AC-FR0260-01@v0.7", ("IF-AUTH-001",), _BASELINE_ACS, _BASELINE_IFS
    )
    assert category == "new"


def test_classify_behaviour_existing_when_acs_in_baseline():
    category = classify_behaviour(
        "AC-FR0250-01@v0.6", ("IF-MTEST-001",), _BASELINE_ACS, _BASELINE_IFS
    )
    assert category == "existing"


def test_classify_behaviour_new_when_if_not_in_baseline():
    category = classify_behaviour(
        "AC-FR0260-01@v0.7", ("IF-AUTH-001",), _BASELINE_ACS, _BASELINE_IFS
    )
    assert category == "new"


# AC-FR0260-01@v0.7 TRACKS-TRACE new behaviour legal Red (assertion/symbol)
def test_judge_authenticity_new_legal_red_on_assertion_failure():
    judgement = judge_authenticity(
        "AC-FR0260-01@v0.7",
        "new",
        bound_nodes=("t1",),
        outcomes={"t1": TestRunResult(node_id="t1", status="failed", detail="assert 1 == 2")},
        legal_failure_kinds={"assertion_failure", "symbol_missing"},
        counterexample_experiment=None,
    )
    assert judgement.ac == "AC-FR0260-01@v0.7"
    assert judgement.category == "new"
    assert judgement.red == "legal"
    assert judgement.green_allowed is False
    assert judgement.unrelated_nodes == ()
    assert judgement.blocked_reason is None


# AC-FR0260-02@v0.7 TRACKS-TRACE unrelated downstream failure isolated
def test_judge_authenticity_unrelated_downstream_failure_isolated():
    judgement = judge_authenticity(
        "AC-FR0260-01@v0.7",
        "new",
        bound_nodes=("t1",),
        outcomes={
            "t1": TestRunResult(node_id="t1", status="passed", detail=None),
            "t2": TestRunResult(node_id="t2", status="failed", detail="assert 0 == 1"),
        },
        legal_failure_kinds={"assertion_failure", "symbol_missing"},
        counterexample_experiment=None,
    )
    # The failing node t2 is not bound to this AC -> unrelated
    assert judgement.red == "none"
    assert judgement.unrelated_nodes == ("t2",)
    assert judgement.blocked_reason is None


# AC-FR0260-05@v0.7 TRACKS-TRACE broad mutation irrelevant Red rejected
def test_judge_authenticity_broad_mutation_irrelevant_red_is_illegal():
    judgement = judge_authenticity(
        "AC-FR0260-01@v0.7",
        "new",
        bound_nodes=("t1",),
        outcomes={"t1": TestRunResult(node_id="t1", status="failed", detail="assert 1 == 2")},
        legal_failure_kinds={"assertion_failure", "symbol_missing"},
        counterexample_experiment={"broad": True, "ac": "different-ac"},
    )
    assert judgement.red == "illegal"
    assert judgement.blocked_reason is not None


# AC-FR0260-04@v0.7 TRACKS-TRACE coexists with D-41: existing behaviour
# delegates to authenticity_existing (IF-AUTH-002, T-012); the facade must
# NOT inline the existing branch (T-009 contract freezes judge_authenticity).
def test_judge_authenticity_existing_delegates_to_authenticity_existing():
    judgement = judge_authenticity(
        "AC-FR0250-01@v0.6",
        "existing",
        bound_nodes=("t1",),
        outcomes={"t1": TestRunResult(node_id="t1", status="passed", detail=None)},
        legal_failure_kinds={"assertion_failure", "symbol_missing"},
        counterexample_experiment=None,
    )
    assert isinstance(judgement, AuthenticityJudgement)
    assert judgement.category == "existing"
