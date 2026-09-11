"""Integration: independent Human three-way gate (FR-0274, IF-RELEASE-003).

The decision basis is always produced by the real M-RELEASE preview command
on a complete same-candidate chain (candidate.frozen + local gates + CI +
prism verify_final + security); it is never seeded. Tests mutate only the
upstream premises (a failed foreign gate, a stale marker, a fresh candidate)
through the public ``Store.append`` API, then observe the real CLI decision
surface: ``validate_release_decision`` fail-closed verdicts,
``release.decided``/``release.rejected`` events, ``trac status`` lines and
exit codes. Bare ``trac run`` bootstrap is forbidden (no init/start → rc=1).
"""

from __future__ import annotations

import pytest

from tests.integration.test_release_authorization_direct import (
    _append_complete_recovery,
    _new_candidate,
)
from tests.integration.test_release_preview_direct import (
    _events,
    _issue_preview,
    _prepare,
    _previews,
)
from tracks import paths
from tracks.executor.release_gate import validate_release_decision
from tracks.store import Store

pytestmark = pytest.mark.integration


def _append_foreign_gate_failure(host_repo, run_id, candidate, contract_digest):
    """Append a failed gate on an identity the preview never declared.

    ``has_complete_passed_gates`` treats a bound identity outside the
    declared set as a later barrier, so the candidate's gate status fails
    while the preview's recomputed evidence inputs stay byte-identical
    (the failed identity was never part of the preview digest)."""
    store = Store(paths.tracks_home(host_repo))
    try:
        store.append(
            run_id,
            store.state(run_id).version or "v0.8",
            "local_gate.failed",
            {
                "kind": "quality",
                "gate_identity": "quality[99]",
                "candidate_sha": candidate,
                "contract_digest": contract_digest,
                "command_echo": ["false"],
                "normalized_result": {
                    "schema": "tracks-gate-result",
                    "version": 1,
                    "status": "failed",
                    "exit_code": 7,
                    "summary": {"source": "failed gate premise"},
                    "gate_id": "quality",
                },
                "status": "failed",
                "exit_code": 7,
                "reason": "failed",
            },
        )
    finally:
        store.close()


def _append_candidate_stale(host_repo, run_id, candidate):
    store = Store(paths.tracks_home(host_repo))
    try:
        store.append(
            run_id,
            store.state(run_id).version or "v0.8",
            "candidate.stale",
            {
                "candidate_sha": candidate,
                "reason": "candidate_drift",
                "detail": "seeded drift",
            },
        )
    finally:
        store.close()


def _decided(events: list) -> list:
    return [e for e in events if e.type == "release.decided"]


# AC-FR0274-01@v0.8 TRACKS-TRACE release action allowed when gates pass and preview fresh
def test_release_action_allowed(host_repo, trac, tmp_path, event_log):
    allowed, reason = validate_release_decision(
        "release", {"preview_digest": "sha256:abc"}, {"gate_failed": False}
    )
    assert (allowed, reason) == (True, None)

    run_id, candidate, _contract, _remote, _artifact, _contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    preview = _previews(_events(host_repo, run_id))[0].payload
    result = trac("release", "--action", "release")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "decision: release" in result.stdout
    decided = [
        e for e in _decided(_events(host_repo, run_id)) if e.payload.get("action") == "release"
    ]
    assert decided, "a passing gate must record release.decided"
    assert decided[0].payload["candidate_sha"] == preview["candidate_sha"]
    assert decided[0].payload["preview_digest"] == preview["preview_digest"]
    assert decided[0].payload["actor"] == "human"
    assert "decision=release" in trac("status").stdout
    # Approve surface must not produce release.decided (delivery exclusivity).
    # At M-RELEASE the approve gate is not applicable (rc=1), which is exactly
    # why an approval can never stand in for the release decision.
    trac("approve", "--actor", "Aaron")
    decided_after = _decided(_events(host_repo, run_id))
    assert all(d.payload.get("actor") == "human" for d in decided_after)
    assert len(decided_after) == len(decided), "approve must not append release.decided"


# AC-FR0274-02@v0.8 TRACKS-TRACE rejected gate or stale blocks release decision
def test_rejected_gate_or_stale(host_repo, trac, tmp_path, event_log):
    ok, reason = validate_release_decision(
        "release", {"preview_digest": "sha256:abc"}, {"gate_failed": True}
    )
    assert (ok, reason) == (False, "gate failed")
    ok, reason = validate_release_decision(
        "release", {"preview_digest": "sha256:abc"}, {"preview_stale": True}
    )
    assert (ok, reason) == (False, "preview stale")

    run_id, candidate, _contract, _remote, artifact_digest, contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    _append_foreign_gate_failure(host_repo, run_id, candidate, contract_digest)
    result = trac("release", "--action", "release")
    assert result.returncode != 0
    assert "release rejected" in result.stdout + result.stderr
    events = _events(host_repo, run_id)
    rejected = [e for e in events if e.type == "release.rejected"]
    assert rejected and rejected[-1].payload["reason"] == "gate_failed"
    assert not _decided(events)
    # A stale preview rejects the same way with no stage transfer.
    store = Store(paths.tracks_home(host_repo))
    try:
        _append_complete_recovery(store, run_id, candidate, contract_digest, artifact_digest)
    finally:
        store.close()
    _append_candidate_stale(host_repo, run_id, candidate)
    result2 = trac("release", "--action", "release")
    assert result2.returncode != 0
    events2 = _events(host_repo, run_id)
    rejected2 = [e for e in events2 if e.type == "release.rejected"]
    assert rejected2 and rejected2[-1].payload["reason"] == "preview_stale"
    assert not _decided(events2)
    assert not any(e.type == "publish.planned" for e in events2)
    assert "rejected: gate failed or preview stale" in trac("status").stdout


# AC-FR0274-03@v0.8 TRACKS-TRACE delay and return bind preview and move or park
def test_delay_and_return(host_repo, trac, tmp_path, event_log):
    assert validate_release_decision("delay", {"preview_digest": "sha256:xyz"}, {}) == (
        True,
        None,
    )
    assert validate_release_decision("return", {"preview_digest": "sha256:xyz"}, {}) == (
        True,
        None,
    )
    # Failing gates block only release — delay/return need just a fresh preview.
    assert validate_release_decision(
        "delay", {"preview_digest": "sha256:xyz"}, {"gate_failed": True}
    ) == (True, None)

    run_id, candidate, contract, remote, artifact_digest, _contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    first = _previews(_events(host_repo, run_id))[0].payload
    delay = trac("release", "--action", "delay", "--reason", "need more review")
    assert delay.returncode == 0, delay.stdout + delay.stderr
    assert "decision: delay" in delay.stdout
    events = _events(host_repo, run_id)
    delayed = [
        e for e in events if e.type == "release.decided" and e.payload.get("action") == "delay"
    ]
    assert delayed
    assert delayed[0].payload["candidate_sha"] == first["candidate_sha"]
    assert delayed[0].payload["preview_digest"] == first["preview_digest"]
    assert "decision=delay" in trac("status").stdout
    # The gate closes after a decision until a newer preview is generated
    # (SM-01.11): a fresh candidate mints a new preview, then return binds it.
    fresh_candidate, _contract_digest2 = _new_candidate(
        host_repo, run_id, contract, artifact_digest, remote=remote
    )
    _issue_preview(host_repo, run_id, fresh_candidate)
    fresh = _previews(_events(host_repo, run_id))[-1].payload
    assert fresh["preview_digest"] != first["preview_digest"]
    ret = trac("release", "--action", "return", "--to", "M-DESIGN", "--reason", "revisit")
    assert ret.returncode == 0, ret.stdout + ret.stderr
    assert "decision: return" in ret.stdout
    events2 = _events(host_repo, run_id)
    returned = [
        e for e in events2 if e.type == "release.decided" and e.payload.get("action") == "return"
    ]
    assert returned
    assert returned[0].payload["target"] == "M-DESIGN"
    assert returned[0].payload["candidate_sha"] == fresh["candidate_sha"]
    assert returned[0].payload["preview_digest"] == fresh["preview_digest"]


# AC-FR0274-04@v0.8 TRACKS-TRACE surface exclusivity and stale checks for delay/return
def test_surface_exclusivity(host_repo, trac, tmp_path, event_log):
    assert validate_release_decision(
        "delay", {"preview_digest": "sha256:abc"}, {"preview_stale": True}
    ) == (False, "preview stale")
    assert validate_release_decision(
        "return", {"preview_digest": "sha256:abc"}, {"preview_stale": True}
    ) == (False, "preview stale")

    run_id, candidate, _contract, _remote, _artifact, _contract_digest = _prepare(
        host_repo, trac, tmp_path
    )
    _issue_preview(host_repo, run_id, candidate)
    _append_candidate_stale(host_repo, run_id, candidate)
    # Even delay/return must be rejected when preview stale — no decision.
    delay_stale = trac("release", "--action", "delay", "--reason", "x")
    assert delay_stale.returncode != 0
    assert "preview stale" in (delay_stale.stdout + delay_stale.stderr).lower()
    ret_stale = trac("release", "--action", "return", "--to", "M-DESIGN", "--reason", "x")
    assert ret_stale.returncode != 0
    assert "preview stale" in (ret_stale.stdout + ret_stale.stderr).lower()
    assert not _decided(_events(host_repo, run_id))
    # Delivery exclusivity: the approve surface produces no release.decided.
    trac("approve", "--actor", "Aaron")
    assert not _decided(_events(host_repo, run_id))
