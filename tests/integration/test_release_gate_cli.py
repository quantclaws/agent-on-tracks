"""Integration: independent Human three-way gate (FR-0274, IF-RELEASE-003).

T-001 GREEN carries no M-RELEASE producer yet (T-039 routing is deferred), so
the decision basis — a §1a#6 ``release.previewed`` plus gate evidence — is
seeded through the public ``Store.append`` API on the scaffolded run (same
precedent as ``tests/integration/escape_gate_seed.py``). All assertions land
on contract outlets: ``validate_release_decision`` fail-closed verdicts,
``release.decided`` / ``release.rejected`` events, ``trac status`` lines and
exit codes. Bare ``trac run`` bootstrap is forbidden (no init/start → rc=1).
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.e2e.helpers import walk_to_await_human
from tracks import paths
from tracks.executor.release_gate import validate_release_decision
from tracks.store.store import Store

pytestmark = pytest.mark.integration


def _seed_release_preview(host_repo, *, gate="passed", stale=False, tag="r1"):
    """Append a §1a#6 release.previewed decision basis (+ gate evidence, plus
    an optional post-preview stale marker) via public Store.append. Returns
    the seeded digests for binding assertions."""
    home = paths.tracks_home(host_repo)
    store = Store(home)
    try:
        run_id = store.active_run()
        assert run_id, "release seeds require an active run (init/start first)"
        version = store.state(run_id).version
        sha = "sha256:" + hashlib.sha256(b"release-seed-candidate").hexdigest()
        seed = json.dumps(["release-seed-preview", tag], sort_keys=True).encode()
        preview = "sha256:" + hashlib.sha256(seed).hexdigest()
        store.append(
            run_id,
            version,
            "release.previewed",
            {
                "candidate_sha": sha,
                "preview_digest": preview,
                "artifact_digest": sha,
                "evidence_digests": {},
                "operation_plan_digest": sha,
                "contract_policy_digest": sha,
                "risks": [],
                "blob_ref": "seed-preview-blob",
            },
        )
        if gate == "passed":
            store.append(
                run_id,
                version,
                "security.assessed",
                {
                    "status": "passed",
                    "policy_digest": sha,
                    "candidate_sha": sha,
                    "scans": [],
                    "prism_scope": "security",
                },
            )
        elif gate == "failed":
            store.append(
                run_id,
                version,
                "security.assessed",
                {
                    "status": "failed",
                    "policy_digest": sha,
                    "candidate_sha": sha,
                    "scans": [],
                    "prism_scope": "security",
                },
            )
        if stale:
            store.append(
                run_id,
                version,
                "candidate.stale",
                {
                    "candidate_sha": sha,
                    "reason": "candidate_drift",
                    "detail": "seeded drift",
                },
            )
    finally:
        store.close()
    return {"candidate_sha": sha, "preview_digest": preview}


# AC-FR0274-01@v0.8 TRACKS-TRACE release action allowed when gates pass and preview fresh
def test_release_action_allowed(host_repo, trac, event_log):
    allowed, reason = validate_release_decision(
        "release", {"preview_digest": "sha256:abc"}, {"gate_failed": False}
    )
    assert (allowed, reason) == (True, None)

    run_id = walk_to_await_human(trac, version="v0.8")
    seeded = _seed_release_preview(host_repo, gate="passed")
    result = trac("release", "--action", "release")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "decision: release" in result.stdout
    events = event_log(run_id)
    decided = [
        e
        for e in events
        if e["type"] == "release.decided" and e["payload"].get("action") == "release"
    ]
    assert decided, "a passing gate must record release.decided"
    assert decided[0]["payload"]["candidate_sha"] == seeded["candidate_sha"]
    assert decided[0]["payload"]["preview_digest"] == seeded["preview_digest"]
    assert decided[0]["payload"]["actor"] == "human"
    assert "decision=release" in trac("status").stdout
    # Approve surface must not produce release.decided (delivery exclusivity).
    assert trac("approve", "--actor", "Aaron").returncode == 0
    decided_after = [e for e in event_log(run_id) if e["type"] == "release.decided"]
    assert all(d["payload"].get("actor") == "human" for d in decided_after)
    assert len(decided_after) == len(decided), "approve must not append release.decided"


# AC-FR0274-02@v0.8 TRACKS-TRACE rejected gate or stale blocks release decision
def test_rejected_gate_or_stale(host_repo, trac, event_log):
    ok, reason = validate_release_decision(
        "release", {"preview_digest": "sha256:abc"}, {"gate_failed": True}
    )
    assert (ok, reason) == (False, "gate failed")
    ok, reason = validate_release_decision(
        "release", {"preview_digest": "sha256:abc"}, {"preview_stale": True}
    )
    assert (ok, reason) == (False, "preview stale")

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    _seed_release_preview(host_repo, gate="failed", tag="r1")
    result = trac("release", "--action", "release")
    assert result.returncode != 0
    assert "release rejected" in result.stdout + result.stderr
    events = event_log()
    rejected = [e for e in events if e["type"] == "release.rejected"]
    assert rejected and rejected[-1]["payload"]["reason"] == "gate_failed"
    assert not [e for e in events if e["type"] == "release.decided"]
    # A stale preview rejects the same way with no stage transfer.
    _seed_release_preview(host_repo, gate="passed", stale=True, tag="r2")
    result2 = trac("release", "--action", "release")
    assert result2.returncode != 0
    events2 = event_log()
    rejected2 = [e for e in events2 if e["type"] == "release.rejected"]
    assert rejected2 and rejected2[-1]["payload"]["reason"] == "preview_stale"
    assert not [e for e in events2 if e["type"] == "release.decided"]
    assert not any(e["type"] == "publish.planned" for e in events2)
    assert "rejected: gate failed or preview stale" in trac("status").stdout


# AC-FR0274-03@v0.8 TRACKS-TRACE delay and return bind preview and move or park
def test_delay_and_return(host_repo, trac, event_log):
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

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    seeded = _seed_release_preview(host_repo, gate="passed", tag="r1")
    delay = trac("release", "--action", "delay", "--reason", "need more review")
    assert delay.returncode == 0, delay.stdout + delay.stderr
    assert "decision: delay" in delay.stdout
    events = event_log()
    delayed = [
        e
        for e in events
        if e["type"] == "release.decided" and e["payload"].get("action") == "delay"
    ]
    assert delayed
    assert delayed[0]["payload"]["candidate_sha"] == seeded["candidate_sha"]
    assert delayed[0]["payload"]["preview_digest"] == seeded["preview_digest"]
    assert "decision=delay" in trac("status").stdout
    # The gate closes after a decision until a newer preview is generated
    # (SM-01.11): regenerate, then return binds the fresh preview.
    seeded2 = _seed_release_preview(host_repo, gate="passed", tag="r2")
    ret = trac("release", "--action", "return", "--to", "M-DESIGN", "--reason", "revisit")
    assert ret.returncode == 0, ret.stdout + ret.stderr
    assert "decision: return" in ret.stdout
    events2 = event_log()
    returned = [
        e
        for e in events2
        if e["type"] == "release.decided" and e["payload"].get("action") == "return"
    ]
    assert returned
    assert returned[0]["payload"]["target"] == "M-DESIGN"
    assert returned[0]["payload"]["candidate_sha"] == seeded2["candidate_sha"]
    assert returned[0]["payload"]["preview_digest"] == seeded2["preview_digest"]


# AC-FR0274-04@v0.8 TRACKS-TRACE surface exclusivity and stale checks for delay/return
def test_surface_exclusivity(host_repo, trac, event_log):
    assert validate_release_decision(
        "delay", {"preview_digest": "sha256:abc"}, {"preview_stale": True}
    ) == (False, "preview stale")
    assert validate_release_decision(
        "return", {"preview_digest": "sha256:abc"}, {"preview_stale": True}
    ) == (False, "preview stale")

    run_id = walk_to_await_human(trac, version="v0.8")
    _seed_release_preview(host_repo, gate="passed", stale=True)
    # Even delay/return must be rejected when preview stale — no decision.
    delay_stale = trac("release", "--action", "delay", "--reason", "x")
    assert delay_stale.returncode != 0
    assert "preview stale" in (delay_stale.stdout + delay_stale.stderr).lower()
    ret_stale = trac("release", "--action", "return", "--to", "M-DESIGN", "--reason", "x")
    assert ret_stale.returncode != 0
    assert "preview stale" in (ret_stale.stdout + ret_stale.stderr).lower()
    assert not [e for e in event_log(run_id) if e["type"] == "release.decided"]
    # Delivery exclusivity: a successful approve produces no release.decided.
    assert trac("approve", "--actor", "Aaron").returncode == 0
    assert not [e for e in event_log(run_id) if e["type"] == "release.decided"]
