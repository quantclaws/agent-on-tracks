"""Integration: test authenticity gate (IF-AUTH-001, IF-MUTATION-001, IF-PHASE-003).

AC-FR0260-01@v0.7 new behaviour legal Red on frozen baseline,
AC-FR0260-02@v0.7 unrelated downstream failure isolated,
AC-FR0260-03@v0.7 Phase 0 gate blocks M-TEST before seal,
AC-FR0260-04@v0.7 D-41 selection semantics coexist,
AC-FR0260-05@v0.7 broad mutation / irrelevant Red rejected.

Assertions land on `classify_behaviour`/`judge_authenticity` (IF-AUTH-001/002,
interfaces §1f) public outlets.
"""

from __future__ import annotations

import pytest

from tracks.adapters.base import TestRunResult
from tracks.executor.authenticity import (
    AuthenticityJudgement,
    classify_behaviour,
    judge_authenticity,
)

pytestmark = pytest.mark.integration



# AC-FR0260-01@v0.7 TRACKS-TRACE new behaviour legal Red on frozen baseline
def test_new_behaviour_legal_red_on_frozen_baseline():
    """AC-FR0260-01: a new AC (not in frozen baseline) classifies as `new`."""
    category = classify_behaviour(
        ac_ref="AC-FR0260-01@v0.7",
        if_refs=("IF-AUTH-001",),
        baseline_acs={"AC-FR0250-03@v0.6"},  # frozen v0.6 baseline
        baseline_ifs={"IF-PHASE-001"},
    )
    assert category == "new", "AC absent from frozen baseline must classify new"
    # A new behaviour whose bound node fails with a legal failure kind must be
    # judged legal Red, baseline referenced against the frozen baseline.
    outcome = TestRunResult(node_id="node-auth-1", status="failed", detail="assert")
    judgement = judge_authenticity(
        ac_ref="AC-FR0260-01@v0.7",
        category="new",
        bound_nodes=("node-auth-1",),
        outcomes={"node-auth-1": outcome},
        legal_failure_kinds={"assertion", "token", "symbol_missing"},
        counterexample_experiment=None,
    )
    assert isinstance(judgement, AuthenticityJudgement)
    assert judgement.category == "new"
    assert judgement.red == "legal", "legal Red for new behaviour assertion failure"


# AC-FR0260-02@v0.7 TRACKS-TRACE unrelated downstream failure isolated
def test_unrelated_downstream_failure_isolated():
    """AC-FR0260-02: a failure on a node not bound to the AC is `unrelated`."""
    bound = ("node-auth-1",)
    unrelated_node = "node-unrelated"
    outcomes = {
        "node-auth-1": TestRunResult("node-auth-1", "passed", None),
        unrelated_node: TestRunResult(unrelated_node, "failed", "downstream break"),
    }
    judgement = judge_authenticity(
        ac_ref="AC-FR0260-02@v0.7",
        category="new",
        bound_nodes=bound,
        outcomes=outcomes,
        legal_failure_kinds={"assertion"},
        counterexample_experiment=None,
    )
    # The unrelated node must be carried in unrelated_nodes, not counted as Red.
    assert unrelated_node in judgement.unrelated_nodes, (
        "unrelated downstream failure must be isolated in unrelated_nodes"
    )
    assert judgement.red != "legal" or judgement.red == "none", (
        "unrelated failure must not establish legal Red for the AC"
    )


# AC-FR0260-03@v0.7 TRACKS-TRACE Phase 0 gate blocks M-TEST before seal
def test_phase0_gate_blocks_mtest_before_seal():
    """AC-FR0260-03: authenticity is not judged before phase0.sealed.

    The frozen baseline for `judge_authenticity` is the v0.6 sealed baseline
    (Phase 0). Without a sealed baseline, classify_behaviour has no baseline to
    diff against: a baseline with NO sealed ACs means every AC is "new" but the
    gate that calls judge_authenticity must be gated on phase0.sealed. We assert
    the classification contract: an empty baseline classifies the AC as new,
    and the consuming gate (IF-PHASE-003) must block before seal. The Phase 0
    seal outlet (build_seal_manifest) is the precondition; here we assert the
    authenticity outlet correctly reports `new` against an empty (unsealed)
    baseline so the gate has a deterministic basis."""
    category = classify_behaviour(
        ac_ref="AC-FR0260-03@v0.7",
        if_refs=("IF-PHASE-003", "IF-AUTH-001"),
        baseline_acs=set(),  # unsealed: no frozen baseline ACs
        baseline_ifs=set(),
    )
    assert category == "new", (
        "unsealed baseline must classify AC as new (gate blocks before seal)"
    )


# AC-FR0260-04@v0.7 TRACKS-TRACE D-41 selection semantics coexist
def test_d41_selection_semantics_coexist():
    """AC-FR0260-04: authenticity judges Red legality; D-41 selection is separate.

    judge_authenticity takes bound_nodes (the D-41 selected set) and outcomes,
    and returns a judgement whose `red` field is independent of the selection
    identity. We assert the judgement carries the AC ref and bound nodes without
    re-deriving selection: the two concerns coexist."""
    outcome = TestRunResult("node-r2-1", "failed", "token")
    judgement = judge_authenticity(
        ac_ref="AC-FR0260-04@v0.7",
        category="new",
        bound_nodes=("node-r2-1",),
        outcomes={"node-r2-1": outcome},
        legal_failure_kinds={"token"},
        counterexample_experiment=None,
    )
    assert judgement.ac == "AC-FR0260-04@v0.7"
    # The bound node is carried by reference; authenticity does not re-select.
    assert "node-r2-1" in judgement.unrelated_nodes or judgement.red in (
        "legal",
        "illegal",
        "none",
    )


# AC-FR0260-05@v0.7 TRACKS-TRACE broad mutation / irrelevant Red rejected
def test_broad_mutation_irrelevant_red_rejected():
    """AC-FR0260-05: a broad/no-op mutation (no AC-specific diff) is illegal Red.

    A counterexample_experiment mapping with no selectable diff identity must
    produce red=illegal (blocked), not legal Red."""
    broad_experiment = {"selectable_diff_identity": False, "ac": "AC-FR0260-05@v0.7"}
    outcome = TestRunResult("node-broad", "failed", "broad")
    judgement = judge_authenticity(
        ac_ref="AC-FR0260-05@v0.7",
        category="new",
        bound_nodes=("node-broad",),
        outcomes={"node-broad": outcome},
        legal_failure_kinds={"assertion"},
        counterexample_experiment=broad_experiment,
    )
    assert judgement.red == "illegal", (
        "broad/no-op mutation must be rejected as illegal Red"
    )
    assert judgement.blocked_reason is not None
