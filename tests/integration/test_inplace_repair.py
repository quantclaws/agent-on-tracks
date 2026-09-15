"""Integration: in-place repair and new candidate re-walk (FR-0286, IF-REPAIR-001/002).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_m_impl_parked``, parks at M-IMPL/DIAGNOSE/awaiting=escalation —
the budget-exhausted repair context) — bare ``trac run`` bootstrap is
forbidden. The module-level halves assert the delivered IF-REPAIR-001/002
contract faces (classification, rounds, frozen tests, irreparability,
evidence staling). ``classify_defect_route`` (kernel/release.py) is the
T-039 deliverable and still raises its contract token: the token probes
are legal anchors. Event-level assertions (repair.round_started,
evidence.staled wiring) stay legal Red against the runtime wiring.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.repair import (
    _ROUNDS,
    assert_frozen_tests_untouched,
    classify_defect,
    judge_irreparable,
    mark_fix_new_candidate,
    open_repair_round,
)
from tracks.kernel.release import classify_defect_route


@pytest.fixture(autouse=True)
def _isolate_repair_round_counter():
    """Module-level round counter isolation: other suites in the same xdist
    worker may open rounds for the same synthetic run id first."""
    _ROUNDS.clear()
    yield
    _ROUNDS.clear()

pytestmark = pytest.mark.integration

_CLASSIFICATION = {"defect_class": "behavior", "owner": "Devon", "discipline": "red_first"}


# AC-FR0286-01@v0.8 TRACKS-TRACE no auto rollback in place rounds
def test_no_auto_rollback_in_place_rounds(host_repo, trac, event_log):
    # Module half (IF-REPAIR-001): open_repair_round emits round_started
    # {round, budget, classification} and NEVER rolls back; classification is
    # the closed-set mapping; classify_defect_route still raises its
    # IF-REPAIR-001 token (T-039 deliverable) — the legal anchor.
    opened = open_repair_round("RUN", dict(_CLASSIFICATION), 3)
    assert isinstance(opened, dict)
    assert opened.get("round") == 1
    assert opened.get("budget") == 3
    assert opened.get("classification", {}).get("defect_class") == "behavior"
    assert "rolled_back" not in str(opened), "in-place repair must never roll back"
    classified = classify_defect({"kind": "behavior"}, {})
    assert classified.get("owner") == "Devon"
    assert classified.get("discipline") == "red_first"
    try:
        classify_defect_route("local_gate_failed", {})
        raise AssertionError("classify_defect_route must raise NotImplementedError(IF-REPAIR-001)")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)

    # CLI half: the walked run parks in the repair context; no automatic
    # stage.rolled_back to M-DESIGN may exist anywhere in the stream.
    walk_to_m_impl_parked(trac)
    events = event_log()
    rolled = [e for e in events if e["type"] == "stage.rolled_back" and "M-DESIGN" in str(e["payload"])]
    assert not rolled, "in-place repair must not produce automatic stage.rolled_back to M-DESIGN"
    # The round_started producer must surface the round on the event stream.
    repair = [e for e in events if e["type"] == "repair.round_started"]
    assert repair, "repair.round_started must appear for blocking defect"
    assert repair[0]["payload"]["round"] <= 3
    assert "classification" in repair[0]["payload"]
    assert "budget" in repair[0]["payload"]


# AC-FR0286-02@v0.8 TRACKS-TRACE repair disciplines and frozen tests untouched
def test_repair_disciplines_and_frozen_tests(host_repo, trac, event_log):
    # Module half (IF-REPAIR-001 §2): the discipline/owner pairs are exact;
    # a frozen test registered with a digest that is missing from the repo
    # fails closed as a contract breach; an empty digest map passes.
    mapping = {
        "behavior": ("Devon", "red_first"),
        "gate": (None, "verification_only"),
        "cve": ("Archer", "cve_advisory"),
        "contract": (None, "contract_delta"),
    }
    for kind, (owner, discipline) in mapping.items():
        result = classify_defect({"kind": kind}, {})
        assert result.get("discipline") == discipline, (
            f"{kind} discipline must be {discipline}, got {result!r}"
        )
        if owner is not None:
            assert result.get("owner") == owner
    assert assert_frozen_tests_untouched(host_repo, {}) is None
    try:
        assert_frozen_tests_untouched(
            host_repo, {"tests/integration/test_vanished_frozen.py": "deadbeef"}
        )
    except AssertionError:
        pass
    else:
        raise AssertionError("a missing frozen test must fail closed as a contract breach")

    # CLI half: every repair round on the stream carries a closed-set
    # discipline classification.
    walk_to_m_impl_parked(trac)
    events = event_log()
    repairs = [e for e in events if e["type"] == "repair.round_started"]
    assert repairs, "repair.round_started must appear for discipline check"
    for r in repairs:
        disc = r["payload"].get("classification", {}).get("discipline")
        assert disc in ("red_first", "verification_only", "cve_advisory", "contract_delta", None)


# AC-FR0286-03@v0.8 TRACKS-TRACE fix new candidate rewalks verify
def test_fix_new_candidate_rewalks_verify(host_repo, trac, event_log):
    # Module half (IF-REPAIR-002): marking a fix as a new candidate stales
    # the old evidence with reason=fix_new_candidate.
    staled = mark_fix_new_candidate("run", "a" * 40)
    assert isinstance(staled, dict)
    assert staled.get("event") == "evidence.staled"
    assert staled.get("reason") == "fix_new_candidate"

    # CLI half: after a fix commit the old candidate's evidence is staled and
    # a new candidate is frozen (never reusing the old preview). The fix
    # commit carries the Tracks-Repair-Round provenance trailer (SM-01.20:
    # the repair channel is machine-identifiable -- a bare commit is drift,
    # never a new candidate).
    walk_to_m_impl_parked(trac)
    candidate_before = next(
        (e["payload"]["candidate_sha"] for e in event_log() if "candidate_sha" in e["payload"]),
        None,
    )
    round_events = [e for e in event_log() if e["type"] == "repair.round_started"]
    assert round_events, "park chain must have opened a repair round"
    open_round = round_events[-1]["payload"]["round"]
    run_id = round_events[-1]["run_id"]
    (host_repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "fix.txt"], cwd=host_repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"fix: repair\n\nTracks-Repair-Round: {run_id}/{open_round}"],
        cwd=host_repo,
        check=True,
    )
    for _ in range(3):
        trac("run")
    events2 = event_log()
    staled_events = [
        e for e in events2 if e["type"] == "evidence.staled" and e["payload"].get("reason") == "fix_new_candidate"
    ]
    assert staled_events, "evidence.staled reason=fix_new_candidate must appear after fix commit"
    frozen = [e for e in events2 if e["type"] == "candidate.frozen"]
    assert frozen, "a fix commit must freeze a new candidate"
    if candidate_before is not None:
        assert frozen[-1]["payload"]["candidate_sha"] != candidate_before


# AC-FR0286-04@v0.8 TRACKS-TRACE irreparable blocked routes to known issue or escape
def test_irreparable_blocked_routes_to_known_issue_or_escape(host_repo, trac, event_log):
    # Module half (IF-REPAIR-002): irreparable only when the budget is
    # exhausted AND Prism confirms attribution unchanged (or a C-class
    # alternate trigger holds).
    assert judge_irreparable(3, 3, {"attribution_unchanged": True}) is True
    assert judge_irreparable(1, 3, {"attribution_unchanged": True}) is False
    assert judge_irreparable(3, 3, {"attribution_unchanged": False}) is False
    assert judge_irreparable(1, 3, {"attribution_unchanged": True, "no_fix_available": True}) is True

    # CLI half: the walked park IS the budget-exhausted observable
    # (attempts=3 at escalation); the irreparable routing event stays legal
    # Red against the runtime wiring.
    walk_to_m_impl_parked(trac)
    events = event_log()
    irreparable = [
        e for e in events if "irreparable" in str(e.get("payload", "")).lower() or "blocked" in str(e).lower()
    ]
    assert irreparable, "irreparable or blocked event must appear"
    status = trac("status").stdout.lower()
    assert "escalation" in status or "blocked" in status or "irreparable" in status
