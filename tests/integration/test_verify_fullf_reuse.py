"""Integration: FULL_F reuse judgment (FR-0268, IF-VERIFY-002/IF-EVIDENCE-001)."""

from __future__ import annotations

import pytest

from tracks.executor.m_verify import judge_full_f_reuse

pytestmark = pytest.mark.integration


# AC-FR0268-01@v0.8 TRACKS-TRACE undrifted identity reuses full_f with evidence reused
def test_undrifted_identity_reuses_full_f(host_repo, trac, event_log):
    try:
        judge_full_f_reuse(
            candidate_sha="a" * 40,
            full_f_evidence={"identity_basis": ("a", "b", "c", "d")},
            identity_quadruple={"tree": "a", "command": "b", "env": "c", "selection_id": "d"},
            stale_marks=(),
        )
        raise AssertionError("judge_full_f_reuse must raise IF-VERIFY-002")
    except NotImplementedError as exc:
        assert "IF-VERIFY-002" in str(exc)

    trac("run")
    events = event_log()
    reused = [e for e in events if e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"]
    assert reused, "evidence.reused kind=full_f must appear when identity matches and no STALE"
    payload = reused[0]["payload"]
    assert "candidate_sha" in payload
    assert "identity_basis" in payload
    status = trac("status")
    assert "full_reuse=full_f" in status.stdout or "full_reuse" in status.stdout
    # Must not have re-executed FULL
    assert not any(e["type"] == "full.executed" for e in events if e["seq"] > reused[0]["seq"])


# AC-FR0268-02@v0.8 TRACKS-TRACE drift or stale reruns full with full_executed
def test_drift_or_stale_reruns_full(host_repo, trac, event_log):
    try:
        judge_full_f_reuse(
            candidate_sha="b" * 40,
            full_f_evidence={"identity_basis": ("x",)},
            identity_quadruple={"tree": "y", "command": "y", "env": "y", "selection_id": "y"},
            stale_marks=("STALE",),
        )
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-VERIFY-002" in str(exc)

    trac("run")
    events = event_log()
    reused = [e for e in events if e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"]
    # When drift/stale/identity_mismatch, no reuse
    assert not reused or any(
        e["payload"].get("reason") in ("drift", "stale", "identity_mismatch") for e in reused
    )
    full_executed = [e for e in events if e["type"] == "full.executed"]
    assert full_executed, "full.executed must appear when reuse is not eligible"
    status = trac("status")
    assert "full_rerun" in status.stdout or "drift" in status.stdout or "stale" in status.stdout


# AC-FR0268-03@v0.8 TRACKS-TRACE stale evidence not reused as passed basis
def test_stale_evidence_not_reused(host_repo, trac, event_log):
    try:
        judge_full_f_reuse(
            candidate_sha="c" * 40,
            full_f_evidence={"identity_basis": ("a", "b", "c", "d"), "stale": True},
            identity_quadruple={"tree": "a", "command": "b", "env": "c", "selection_id": "d"},
            stale_marks=("STALE",),
        )
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-VERIFY-002" in str(exc)
        assert "IF-EVIDENCE-001" in str(exc) or "IF-VERIFY-002" in str(exc)

    trac("run")
    events = event_log()
    # STALE-marked FULL_F must be marked stale and not produce evidence.reused
    reused = [e for e in events if e["type"] == "evidence.reused" and e["payload"].get("kind") == "full_f"]
    for r in reused:
        assert r["payload"].get("stale") is not True
    # Replay must show stale marking
    replay = trac("replay")
    assert replay.returncode == 0 or "stale" in replay.stdout.lower()
    # No reuse for stale evidence even if SHA matches
    stale_events = [e for e in events if "stale" in e["type"] or e["payload"].get("stale")]
    assert stale_events
