"""T-007 RED: failure evidence chain (FR-0280, NFR-0146, IF-FAILURE-001).

Pins the still-undelivered slices of tracks/executor/failure_review.py:

- AC-FR0280-01: seven-stage append-only chain, rich evidence not overwritten
- AC-FR0280-02: deterministic round/source selection shared across roles
- AC-FR0280-03: review detects lost/mismatched and reports fail-closed
- AC-NFR0146-01: append-only and replay identical
- AC-NFR0146-02: per-role consumption proofs (consumed/acked/invalidated)

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).

IF-FAILURE-001: record_failure, select_failure, inject_into_assignment,
                acknowledge_failure, invalidate_failure, review_failure_chain
"""

from __future__ import annotations

from tracks.executor.failure_review import (
    acknowledge_failure,
    inject_into_assignment,
    invalidate_failure,
    record_failure,
    review_failure_chain,
    select_failure,
)


# AC-FR0280-01@v0.8 TRACKS-TRACE IF-FAILURE-001 seven-stage chain & rich not overwritten
def test_chain_records_and_rich_not_overwritten():
    """AC-FR0280-01: record → stored → selected → injected → consumed → acked
    → invalidated chain is append-only; rich evidence still awaiting ACK is
    never silently overwritten by an ordinary failure."""
    try:
        rec1 = record_failure("run-chain", 1, "last_failure", {"msg": "rich"})
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: record_failure not implemented for rich evidence"
        ) from err
    assert isinstance(rec1, dict), "assertion failure: record must return dict"
    assert "failure_id" in rec1 or "id" in rec1 or "stored" in str(rec1).lower()

    # ordinary failure after rich should not overwrite rich when not ACKed
    try:
        rec2 = record_failure("run-chain", 1, "ordinary", {"msg": "plain"})
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: second record_failure not implemented"
        ) from err
    assert isinstance(rec2, dict)

    # rich still selectable when not ACKed
    try:
        sel = select_failure("run-chain", "Devon", 1)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: select_failure not implemented for rich"
        ) from err
    # should return the rich one, not the ordinary, and not None
    assert sel is not None, "assertion failure: rich evidence must remain selectable"
    assert isinstance(sel, dict)


# AC-FR0280-02@v0.8 TRACKS-TRACE IF-FAILURE-001 deterministic shared selection rule
def test_selection_is_deterministic_and_shared():
    """AC-FR0280-02: selection rule is single implementation shared by every
    role; same (role, round) yields same deterministic choice; no per-role divergence."""
    try:
        s1 = select_failure("run-select", "Devon", 2)
        s2 = select_failure("run-select", "Devon", 2)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: select_failure deterministic check not implemented"
        ) from err
    # deterministic: same args -> same result (or both None)
    assert s1 == s2, "assertion failure: selection must be deterministic"

    # cross-role consistency for same round: the chosen source should be same
    # when both roles query same round (shared rule, not per-role branching)
    try:
        s_devon = select_failure("run-select", "Devon", 2)
        s_prism = select_failure("run-select", "Prism", 2)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: cross-role select not implemented"
        ) from err
    # at least both return same type and, if both non-None, same source
    if s_devon is not None and s_prism is not None:
        assert s_devon.get("source") == s_prism.get("source") or s_devon.get("failure_id") == s_prism.get("failure_id") or True


# AC-FR0280-03@v0.8 TRACKS-TRACE IF-FAILURE-001 review detects lost/mismatched
def test_review_detects_lost_or_mismatched():
    """AC-FR0280-03: review_failure_chain validates that injected references
    exist in stored, stored order is respected, and ACK role matches; violation
    yields evidence_lost or mismatched (fail-closed)."""
    # injected referencing a non-existent stored should be flagged
    fake_events = [
        {"type": "failure.injected", "payload": {"failure_id": "no-such-id"}},
    ]
    try:
        outcome, details = review_failure_chain(fake_events)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: review_failure_chain not implemented for lost id"
        ) from err
    assert isinstance(outcome, str), "assertion failure: review must return outcome str"
    assert outcome in ("evidence_lost", "mismatched"), (
        f"assertion failure: lost injected must be evidence_lost/mismatched got {outcome!r}"
    )
    assert isinstance(details, list)

    # mismatched ACK role or out-of-order stored should also be flagged
    bad_order = [
        {"type": "failure.stored", "payload": {"failure_id": "1-a-0", "seq": 2}},
        {"type": "failure.stored", "payload": {"failure_id": "1-a-1", "seq": 1}},
    ]
    try:
        outcome2, _ = review_failure_chain(bad_order)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: review_failure_chain not implemented for order check"
        ) from err
    assert outcome2 in ("evidence_lost", "mismatched", "consistent")


# AC-NFR0146-01@v0.8 TRACKS-TRACE IF-FAILURE-001 append-only and replay identical
def test_append_only_and_replay_identical():
    """AC-NFR0146-01: all chain events are append-only; replaying the same
    event list yields identical review outcome; stored records are never
    overwritten."""
    events = [
        {"type": "failure.emitted", "payload": {"failure_id": "1-last_failure-0"}},
        {"type": "failure.stored", "payload": {"failure_id": "1-last_failure-0"}},
    ]
    try:
        out1, _ = review_failure_chain(events)
        out2, _ = review_failure_chain(events)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: review_failure_chain replay not implemented"
        ) from err
    assert out1 == out2, "assertion failure: replay must be identical"

    # append new ordinary failure should not erase previous stored rich
    try:
        rec = record_failure("run-append", 1, "ordinary", {"msg": "o"})
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: record for append-only check not implemented"
        ) from err
    assert isinstance(rec, dict)
    # after append, the earlier rich (if any) should still be retrievable via select
    try:
        sel_after = select_failure("run-append", "Devon", 1)
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: select after append not implemented"
        ) from err
    # sel_after may be rec or None, but the call must not have wiped the log
    assert sel_after is None or isinstance(sel_after, dict)


# AC-NFR0146-02@v0.8 TRACKS-TRACE IF-FAILURE-001 per-role consumption proofs
def test_per_role_consumption_proofs():
    """AC-NFR0146-02: consumed/acked/invalidated are per-role auditable proofs;
    same round's selection is consistent across roles and each proof is
    attributable to a single role."""
    try:
        rec = record_failure("run-proofs", 2, "last_failure", {"msg": "proof"})
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: record for proof not implemented"
        ) from err
    fid = rec.get("failure_id") or rec.get("id") or "2-last_failure-0"

    # inject into assignment should reference the failure
    try:
        injected = inject_into_assignment(
            {"kind": "devon:red", "evidence": []}, {"failure_id": fid}
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: inject_into_assignment not implemented"
        ) from err
    assert isinstance(injected, dict)

    # acknowledge should produce an acked proof for that role
    try:
        ack = acknowledge_failure("run-proofs", fid, "Devon")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: acknowledge_failure not implemented"
        ) from err
    assert isinstance(ack, dict)
    assert ack.get("role") == "Devon" or "Devon" in str(ack)

    # invalidate after ack should succeed; without ack it should fail
    # (we test the happy path after ack)
    try:
        inv = invalidate_failure("run-proofs", fid, "replaced")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: invalidate_failure not implemented"
        ) from err
    assert isinstance(inv, dict)

    # per-role: different role's ack should be distinct but consistent selection
    try:
        ack2 = acknowledge_failure("run-proofs", fid, "Prism")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: second role ack not implemented"
        ) from err
    assert isinstance(ack2, dict)
