"""Integration: failure evidence chain (FR-0280, NFR-0146, IF-FAILURE-001)."""

from __future__ import annotations

import pytest

from tracks.executor.failure_review import (
    acknowledge_failure,
    inject_into_assignment,
    record_failure,
    review_failure_chain,
    select_failure,
)

pytestmark = pytest.mark.integration


# AC-FR0280-01@v0.8 TRACKS-TRACE chain events replay and rich evidence not overwritten
def test_chain_events_replay(host_repo, trac, event_log):
    try:
        record_failure("run", 1, "last_failure", {"msg": "x"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)
    try:
        select_failure("run", "Devon", 1)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)
    try:
        inject_into_assignment({"kind": "devon:red"}, {"failure_id": "1-last_failure-0"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)
    try:
        acknowledge_failure("run", "1-last_failure-0", "Devon")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)

    try:
        review_failure_chain([])
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)

    trac("run")
    events = event_log()
    chain_types = ["failure.emitted", "failure.stored", "failure.selected", "failure.injected", "failure.consumed", "failure.acked", "failure.invalidated"]
    found = [e["type"] for e in events if e["type"].startswith("failure.")]
    assert found, "failure chain must appear"
    assert found[0] in chain_types
    replay = trac("replay").stdout
    assert "failure" in replay.lower()
    report = trac("report").stdout
    assert "failure" in report.lower()


# AC-FR0280-02@v0.8 TRACKS-TRACE consistent selection rules across roles
def test_consistent_selection_rules(host_repo, trac, event_log):
    try:
        select_failure("run", "Devon", 1)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)

    trac("run")
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
    try:
        review_failure_chain([{"type": "failure.injected", "payload": {"failure_id": "bad"}}])
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)

    trac("run")
    events = event_log()
    failed = [e for e in events if e["type"] == "review.failed" and e["payload"].get("area") == "failure_evidence"]
    assert failed, "review.failed must appear for lost/mismatched"
    assert failed[0]["payload"]["outcome"] in ("evidence_lost", "mismatched")
    status = trac("status").stdout
    assert "evidence lost" in status.lower() or "mismatched" in status.lower()
    assert "blocked" in status.lower()


# AC-NFR0146-01@v0.8 TRACKS-TRACE append only replay identical
def test_append_only_replay_identical(host_repo, trac, event_log):
    try:
        record_failure("run", 1, "last_failure", {"msg": "x"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)
    except TypeError:
        try:
            record_failure("run", 1, "last_failure", {})  # type: ignore[call-arg]
            raise AssertionError("expected NotImplementedError")
        except NotImplementedError as exc2:
            assert "IF-FAILURE-001" in str(exc2)

    trac("run")
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
    try:
        acknowledge_failure("run", "fid", "Devon")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-FAILURE-001" in str(exc)

    trac("run")
    events = event_log()
    consumed = [e for e in events if e["type"] == "failure.consumed"]
    acked = [e for e in events if e["type"] == "failure.acked"]
    invalidated = [e for e in events if e["type"] == "failure.invalidated"]
    assert consumed, "failure.consumed must appear"
    assert acked, "failure.acked must appear"
    assert invalidated, "failure.invalidated must appear"
    replay_out = trac("replay").stdout
    assert replay_out is not None
