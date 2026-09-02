"""Integration: failure evidence chain (FR-0280, NFR-0146, IF-FAILURE-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The failure.* event-family producers
are wired by later runtime tasks; until then the event-level assertions are
legal Red against that product gap. The module-level halves assert the
delivered IF-FAILURE-001 contract payloads (§1k).
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.failure_review import (
    acknowledge_failure,
    inject_into_assignment,
    invalidate_failure,
    record_failure,
    review_failure_chain,
    select_failure,
)

pytestmark = pytest.mark.integration

_CHAIN_TYPES = (
    "failure.emitted",
    "failure.stored",
    "failure.selected",
    "failure.injected",
    "failure.consumed",
    "failure.acked",
    "failure.invalidated",
)


# AC-FR0280-01@v0.8 TRACKS-TRACE chain events replay and rich evidence not overwritten
def test_chain_events_replay(host_repo, trac, event_log):
    # IF-FAILURE-001 module surface: append-only records with §1k id grammar,
    # deterministic selection, evidence injection, per-role ACK.
    rich = record_failure("chain", 1, "last_failure", {"msg": "rich"})
    fid = rich["failure_id"]
    assert fid == "1-last_failure-0"
    ordinary = record_failure("chain", 1, "diagnose_report", {"msg": "ordinary"})
    assert ordinary["failure_id"] == "1-diagnose_report-1"
    # Selection: the latest un-ACKed record of the round (deterministic rule).
    assert select_failure("chain", "Devon", 1)["failure_id"] == ordinary["failure_id"]
    # Rich evidence still awaiting consumption is not overwritten: after the
    # ordinary record is ACKed, the rich record is still selectable.
    assert acknowledge_failure("chain", ordinary["failure_id"], "Devon")["acked"] is True
    assert select_failure("chain", "Devon", 1)["failure_id"] == fid
    # Injection references the failure id in the assignment evidence list (§1k).
    assignment = inject_into_assignment({"kind": "devon:red"}, ordinary)
    assert assignment["evidence"] == [ordinary["failure_id"]]
    # A chain without injected references reviews consistent (tri-state, §1k).
    outcome, _reasons = review_failure_chain([])
    assert outcome == "consistent"

    walk_to_m_impl_parked(trac)
    events = event_log()
    found = [e["type"] for e in events if e["type"].startswith("failure.")]
    assert found, "failure chain must appear"
    assert found[0] in _CHAIN_TYPES
    replay = trac("replay").stdout
    assert "failure" in replay.lower()
    report = trac("report").stdout
    assert "failure" in report.lower()


# AC-FR0280-02@v0.8 TRACKS-TRACE consistent selection rules across roles
def test_consistent_selection_rules(host_repo, trac, event_log):
    # Deterministic shared rule: same (role, round) -> same record; every role
    # sees the same selection (single implementation, §1k).
    first = record_failure("select", 2, "last_failure", {"msg": "a"})
    second = record_failure("select", 2, "diagnose_report", {"msg": "b"})
    assert select_failure("select", "Devon", 2)["failure_id"] == second["failure_id"]
    assert select_failure("select", "Devon", 2)["failure_id"] == second["failure_id"]
    assert select_failure("select", "Prism", 2)["failure_id"] == second["failure_id"]
    # Un-ACKed look-back: a round without records falls back up to 3 rounds.
    assert select_failure("select", "Devon", 5)["failure_id"] == second["failure_id"]
    assert first["failure_id"] == "2-last_failure-0"

    walk_to_m_impl_parked(trac)
    events = event_log()
    selected = [e for e in events if e["type"] == "failure.selected"]
    assert selected, "failure.selected must appear"
    assert all("rule" in s["payload"] or "source" in s["payload"] for s in selected)
    by_round: dict = {}
    for s in selected:
        by_round.setdefault(s["payload"].get("round"), []).append(s["payload"].get("source"))
    for sources in by_round.values():
        assert len(set(sources)) == 1
    replay = trac("replay").stdout
    assert "failure.selected" in replay


# AC-FR0280-03@v0.8 TRACKS-TRACE lost or mismatched blocks review
def test_lost_or_mismatched_blocks(host_repo, trac, event_log):
    # Injected reference that was never stored -> evidence_lost (fail-closed).
    outcome, reasons = review_failure_chain(
        [{"type": "failure.injected", "payload": {"failure_id": "no-such-id"}}]
    )
    assert outcome == "evidence_lost"
    assert reasons, "the lost reference must be reported"
    # Stored rows out of seq order -> mismatched.
    outcome2, reasons2 = review_failure_chain(
        [
            {"type": "failure.stored", "payload": {"failure_id": "1-a-1", "seq": 2}},
            {"type": "failure.stored", "payload": {"failure_id": "1-a-0", "seq": 1}},
        ]
    )
    assert outcome2 == "mismatched"
    assert reasons2, "the ordering violation must be reported"
    # A consistent chain (stored then injected, ascending seq) passes.
    outcome3, _ = review_failure_chain(
        [
            {"type": "failure.stored", "payload": {"failure_id": "1-a-0", "seq": 1}},
            {"type": "failure.injected", "payload": {"failure_id": "1-a-0"}},
        ]
    )
    assert outcome3 == "consistent"

    walk_to_m_impl_parked(trac)
    events = event_log()
    failed = [e for e in events if e["type"] == "review.failed" and e["payload"].get("area") == "failure_evidence"]
    assert failed, "review.failed must appear for lost/mismatched"
    assert failed[0]["payload"]["outcome"] in ("evidence_lost", "mismatched")
    status = trac("status").stdout
    assert "evidence lost" in status.lower() or "mismatched" in status.lower()
    assert "blocked" in status.lower()


# AC-NFR0146-01@v0.8 TRACKS-TRACE append only replay identical
def test_append_only_replay_identical(host_repo, trac, event_log):
    # Append-only: sequential §1k ids, records never overwrite each other.
    first = record_failure("append", 3, "last_failure", {"msg": "x"})
    second = record_failure("append", 3, "last_failure", {"msg": "y"})
    assert first["failure_id"] == "3-last_failure-0"
    assert second["failure_id"] == "3-last_failure-1"
    assert first["record"] == {"msg": "x"}
    assert second["record"] == {"msg": "y"}
    # Both records stay alive: selection returns the latest un-ACKed.
    assert select_failure("append", "Devon", 3)["failure_id"] == "3-last_failure-1"
    # Replaying the same chain yields the identical review outcome (NFR-0146).
    chain = [
        {"type": "failure.stored", "payload": {"failure_id": "3-last_failure-0", "seq": 0}},
        {"type": "failure.stored", "payload": {"failure_id": "3-last_failure-1", "seq": 1}},
    ]
    out1, _ = review_failure_chain(chain)
    out2, _ = review_failure_chain(chain)
    assert out1 == out2 == "consistent"

    walk_to_m_impl_parked(trac)
    events = event_log()
    replay1 = trac("replay").stdout
    replay2 = trac("replay").stdout
    assert replay1 == replay2
    stored = [e for e in events if e["type"] == "failure.stored"]
    assert stored, "failure.stored must appear"
    selected = [e for e in events if e["type"] == "failure.selected"]
    assert selected, "failure.selected must appear"


# AC-NFR0146-02@v0.8 TRACKS-TRACE per role consumption proofs
def test_per_role_consumption_proofs(host_repo, trac, event_log):
    # Consumption proofs are role-scoped: ACK excludes the record only for the
    # acking role (§1k ACK discipline); other roles still see it.
    recorded = record_failure("proofs", 4, "last_failure", {"msg": "proof"})
    fid = recorded["failure_id"]
    assert fid == "4-last_failure-0"
    assert select_failure("proofs", "Devon", 4)["failure_id"] == fid
    assert acknowledge_failure("proofs", fid, "Devon")["acked"] is True
    assert select_failure("proofs", "Prism", 4)["failure_id"] == fid
    # Invalidation applies to the acked record (§1k happy path).
    invalidated = invalidate_failure("proofs", fid, "superseded")
    assert invalidated["invalidated"] is True
    assert invalidated["failure_id"] == fid

    walk_to_m_impl_parked(trac)
    events = event_log()
    consumed = [e for e in events if e["type"] == "failure.consumed"]
    acked = [e for e in events if e["type"] == "failure.acked"]
    invalidated_events = [e for e in events if e["type"] == "failure.invalidated"]
    assert consumed, "failure.consumed must appear"
    assert acked, "failure.acked must appear"
    assert invalidated_events, "failure.invalidated must appear"
    assert trac("replay").stdout is not None
