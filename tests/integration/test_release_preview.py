"""Integration: preview aggregation (FR-0273, IF-RELEASE-002)."""

from __future__ import annotations

import hashlib
import json

import pytest

from tracks.executor.release_gate import compute_preview_digest

pytestmark = pytest.mark.integration


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# AC-FR0273-01@v0.8 TRACKS-TRACE preview digest binds all components
def test_preview_digest_binds_all(host_repo, trac, event_log):
    try:
        compute_preview_digest("a" * 40, "sha256:" + "b" * 64, {}, "sha256:" + "c" * 64, "sha256:" + "d" * 64)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-002" in str(exc)

    trac("run")
    events = event_log()
    previewed = [e for e in events if e["type"] == "release.previewed"]
    assert previewed, "release.previewed must appear in M-RELEASE"
    payload = previewed[0]["payload"]
    for key in ("candidate_sha", "preview_digest", "artifact_digest", "evidence_digests", "operation_plan_digest", "contract_policy_digest"):
        assert key in payload, f"preview missing {key}"
    # Locally recompute digest and bind it (ground truth without importing helper)
    raw = {
        "candidate_sha": payload["candidate_sha"],
        "artifact_digest": payload["artifact_digest"],
        "evidence_digests": payload["evidence_digests"],
        "operation_plan_digest": payload["operation_plan_digest"],
        "contract_policy_digest": payload["contract_policy_digest"],
    }
    expected = "sha256:" + hashlib.sha256(_canonical(raw)).hexdigest()
    assert payload["preview_digest"] == expected
    assert payload["preview_digest"].startswith("sha256:")
    status = trac("status")
    assert "preview_digest" in status.stdout
    assert "awaiting_release" in status.stdout
    # Independent CLI preview outlet
    preview_cli = trac("release", "preview")
    assert "preview_digest=" in preview_cli.stdout or "candidate=" in preview_cli.stdout
    assert payload["candidate_sha"] in preview_cli.stdout


# AC-FR0273-02@v0.8 TRACKS-TRACE stale preview reported and not reusable for release
def test_stale_preview_reported(host_repo, trac, event_log):
    from tracks.executor.release_gate import judge_preview_stale

    try:
        judge_preview_stale({}, {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-002" in str(exc)

    trac("run")
    events = event_log()
    previewed = [e for e in events if e["type"] == "release.previewed"]
    assert previewed
    first_digest = previewed[0]["payload"]["preview_digest"]
    # Cause stale by creating a new commit (candidate drift)
    (host_repo / "stale.txt").write_text("x\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "add", "stale.txt"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "stale"], cwd=host_repo, check=True)
    stale_status = trac("status")
    preview_check = trac("release", "preview")
    combined = stale_status.stdout + preview_check.stdout + preview_check.stderr
    assert "stale" in combined.lower()
    assert any(r in combined for r in ("candidate drift", "candidate_drift", "evidence staled", "evidence_staled", "operation plan changed"))
    # Stale preview must not be usable for release action
    release_attempt = trac("release", "--action", "release")
    assert release_attempt.returncode != 0
    assert "stale" in (release_attempt.stdout + release_attempt.stderr).lower()
    # Regeneration must create a new previewed event, old digest retained
    trac("run")
    events2 = event_log()
    previewed2 = [e for e in events2 if e["type"] == "release.previewed"]
    assert len(previewed2) >= 2
    assert previewed2[-1]["payload"]["preview_digest"] != first_digest
    assert any(p["payload"]["preview_digest"] == first_digest for p in previewed2)
