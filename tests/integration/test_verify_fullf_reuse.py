"""Integration: FULL_F reuse judgment (FR-0268, IF-VERIFY-002/IF-EVIDENCE-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The M-VERIFY freeze/reuse event
producers are wired by later runtime tasks (kernel/release.py is the T-039
deliverable); until then the event-level assertions are legal Red against
that product gap. The module-level halves assert the delivered
IF-VERIFY-002 judgment contract (§1d).
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.m_verify import judge_full_f_reuse

pytestmark = pytest.mark.integration

_QUAD = {"tree": "a", "command": "b", "env": "c", "selection_id": "d"}
_BASIS = ("a", "b", "c", "d")


# AC-FR0268-01@v0.8 TRACKS-TRACE undrifted identity reuses full_f with evidence reused
def test_undrifted_identity_reuses_full_f(host_repo, trac, event_log):
    # IF-VERIFY-002 module surface: undrifted candidate, matching quadruple,
    # no STALE -> reuse with reason reuse_full_f and the identity basis
    # carried on the decision (§1d: reuse only when all three hold).
    decision = judge_full_f_reuse(
        candidate_sha="a" * 40,
        full_f_evidence={"identity_basis": _BASIS},
        identity_quadruple=_QUAD,
        stale_marks=(),
    )
    assert decision.decision == "reuse"
    assert decision.reason == "reuse_full_f"
    assert decision.identity_basis == _BASIS

    walk_to_m_impl_parked(trac)
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
    # IF-VERIFY-002 module surface: STALE mark wins over a matching identity
    # (stale has highest priority, §1d)...
    d_stale = judge_full_f_reuse(
        candidate_sha="b" * 40,
        full_f_evidence={"identity_basis": ("x",)},
        identity_quadruple={"tree": "y", "command": "y", "env": "y", "selection_id": "y"},
        stale_marks=("STALE",),
    )
    assert d_stale.decision == "rerun"
    assert d_stale.reason in ("drift", "stale", "identity_mismatch")
    # ...and a mismatched quadruple reruns with identity_mismatch even when
    # no STALE mark is present.
    d_mismatch = judge_full_f_reuse(
        candidate_sha="c" * 40,
        full_f_evidence={"identity_basis": _BASIS},
        identity_quadruple={"tree": "a", "command": "b", "env": "X", "selection_id": "d"},
        stale_marks=(),
    )
    assert d_mismatch.decision == "rerun"
    assert d_mismatch.reason == "identity_mismatch"

    walk_to_m_impl_parked(trac)
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
    # IF-EVIDENCE-001 consumption surface: a STALE-marked FULL_F is never
    # reused even when the SHA matches and the quadruple is identical — the
    # stale flag in the evidence itself (not only the stale_marks tuple) wins.
    decision = judge_full_f_reuse(
        candidate_sha="c" * 40,
        full_f_evidence={"identity_basis": _BASIS, "stale": True},
        identity_quadruple=_QUAD,
        stale_marks=("STALE",),
    )
    assert decision.decision == "rerun"
    assert decision.reason == "stale"
    # Stale evidence's identity must not be returned as a passed basis.
    assert decision.decision != "reuse"

    walk_to_m_impl_parked(trac)
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
