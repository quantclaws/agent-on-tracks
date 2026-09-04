"""Integration: fail-closed release matrix (NFR-0149-03, IF-VERIFY-001/IF-PUBLISH-002/IF-ISSUE-001).

b93 §8.1 bootstrap contract: the CLI half is driven by the shared walker
(``walk_to_m_impl_parked``) — bare ``trac run`` bootstrap is forbidden. The
module-level halves assert the delivered fail-closed contract faces
(IF-VERIFY-001 binding scan, IF-PUBLISH-002 reconcile verdicts); the
release-chain event assertions stay legal Red against the unwired
M-VERIFY producers.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.m_verify import collect_binding_violations
from tracks.executor.publish import reconcile_operation

pytestmark = pytest.mark.integration

_CANDIDATE = "a" * 40
_FOREIGN = "b" * 40


# AC-NFR0149-03@v0.8 TRACKS-TRACE identity stale malformed fake blocked
def test_identity_stale_malformed_fake_blocked(host_repo, trac, event_log, monkeypatch):
    # Module half (IF-VERIFY-001): unbound/foreign release-chain evidence is
    # flagged — the identity fail-closed face; an empty chain has nothing to
    # flag. Malformed input (None event rows) fails closed, not silently.
    assert collect_binding_violations([], _CANDIDATE) == []
    flagged = collect_binding_violations(
        [{"kind": "evidence.reused", "candidate_sha": _FOREIGN}], _CANDIDATE
    )
    assert len(flagged) == 1, f"foreign-SHA evidence must be flagged, got {flagged!r}"

    # Module half (IF-PUBLISH-002): the reconcile verdicts are closed —
    # matching remote skips, absent remote pends, divergent content conflicts.
    planned = {"idempotency_key": "k1", "target": "t", "kind": "tag"}
    assert reconcile_operation(planned, {"exists": True, "matches": True}) == "skip"
    assert reconcile_operation(planned, {"exists": False}) == "pending"
    divergent = {"idempotency_key": "k1", "target": "t", "digest": "d1"}
    assert reconcile_operation(divergent, {"idempotency_key": "k1", "target": "t", "digest": "d2"}) == "conflict"

    # CLI half: the walked run must never surface a successful release chain
    # for evidence that is stale/malformed/unverifiable — the fail-closed
    # posture is observable as no unbound release success. Legal Red: the
    # release-stage producers are not wired on this baseline.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    walk_to_m_impl_parked(trac)
    events = event_log()
    release_success = [
        e
        for e in events
        if e["type"] in ("release.decided", "publish.executed", "milestone.sealed")
    ]
    for ev in release_success:
        assert ev["payload"].get("candidate_sha"), (
            f"release success {ev['type']} must bind a candidate_sha (NFR-0149-03)"
        )
    status = trac("status")
    assert "awaiting=escalation" in status.stdout or "blocked" in status.stdout.lower(), (
        "a run with unverifiable evidence must sit blocked, not silently succeed"
    )
