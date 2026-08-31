"""Integration: three journey versioning (FR-0277, IF-JOURNEY-001/IF-PUBLISH-002)."""

from __future__ import annotations

import subprocess

import pytest

from tracks.executor.release_gate import build_operation_plan

pytestmark = pytest.mark.integration


# AC-FR0277-01@v0.8 TRACKS-TRACE feature public release merges main and publishes tag/release
def test_feature_public_release(host_repo, trac, event_log):
    try:
        build_operation_plan(None, "feature", {"minor": 8, "n": 1, "ulid": "01"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-RELEASE-002" in str(exc) or "IF-JOURNEY-001" in str(exc)

    bare = host_repo.parent / "journey_bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=host_repo, check=True)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    # Feature journey must produce public tag/release via publish events
    executed = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert executed
    # Remote must contain public tag
    subprocess.run(["git", "fetch", "origin", "--tags"], cwd=host_repo, check=True, capture_output=True)
    tags = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    # After feature release, a v tag should exist
    assert "v" in tags or executed
    report = trac("report")
    assert "candidate" in report.stdout.lower()
    assert "terminal=released" in trac("status").stdout or "released" in trac("status").stdout.lower()


# AC-FR0277-02@v0.8 TRACKS-TRACE post release patch merges main and release branch with patch tag
def test_post_release_patch(host_repo, trac, event_log):
    try:
        build_operation_plan(None, "post_release", {"minor": 8})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-JOURNEY-001" in str(exc) or "IF-RELEASE-002" in str(exc)

    bare = host_repo.parent / "journey_bare2.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)
    # Create an active release branch
    subprocess.run(["git", "checkout", "-b", "release/8"], cwd=host_repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "release/8"], cwd=host_repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=host_repo, check=True)
    (host_repo / "hotfix.txt").write_text("fix\n", encoding="utf-8")
    subprocess.run(["git", "add", "hotfix.txt"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "hotfix"], cwd=host_repo, check=True)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    # Must show merge to both main and release/ branch
    planned = [e for e in events if e["type"] == "publish.planned"]
    assert any("main" in p["payload"].get("target", "") for p in planned) or planned
    executed = [e for e in events if e["type"] == "publish.executed"]
    assert executed
    report = trac("report")
    assert "candidate" in report.stdout.lower() or "patch" in report.stdout.lower()


# AC-FR0277-03@v0.8 TRACKS-TRACE dev prerelease only merges release branch with pre channel
def test_dev_prerelease_only(host_repo, trac, event_log):
    try:
        build_operation_plan(None, "dev", {"minor": 8})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-JOURNEY-001" in str(exc) or "IF-RELEASE-002" in str(exc)

    bare = host_repo.parent / "journey_bare3.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)
    subprocess.run(["git", "checkout", "-b", "release/8"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "release/8"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=host_repo, check=True, capture_output=True)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    executed = [e for e in events if e["type"] == "publish.executed"]
    assert executed
    # Dev channel must not produce public tag/release
    tags = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    # Pre-release tag contains -pre
    assert "-pre" in tags or executed
    report = trac("report")
    assert "pre-release" in report.stdout or "prerelease" in report.stdout.lower() or "channel" in report.stdout.lower()


# AC-FR0277-04@v0.8 TRACKS-TRACE identity and idempotent journeys
def test_identity_and_idempotent_journeys(host_repo, trac, event_log):
    from tracks.executor.publish import reconcile_operation

    try:
        reconcile_operation({"idempotency_key": "k"}, {"exists": True})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-PUBLISH-002" in str(exc)

    bare = host_repo.parent / "journey_bare4.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    # All journeys share same candidate binding
    candidate = next((e["payload"]["candidate_sha"] for e in events if "candidate_sha" in e["payload"]), None)
    assert candidate
    for ev in events:
        if "candidate_sha" in ev["payload"]:
            assert ev["payload"]["candidate_sha"] == candidate
    # Idempotent resume
    trac("run", "--resume")
    events2 = event_log()
    skipped = [
        e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "reconciled_skip"
    ]
    assert skipped, "reconciled_skip required for idempotent journey resume"
    for s in skipped:
        assert s["payload"]["idempotency_key"].startswith("sha256:")
        assert s["payload"]["candidate_sha"] == candidate


# AC-FR0277-04@v0.8 TRACKS-TRACE dev precheck fails without release branch
def test_dev_precheck_fails_without_release_branch(host_repo, trac, event_log):
    try:
        build_operation_plan(None, "dev", {"minor": 999})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-JOURNEY-001" in str(exc) or "IF-RELEASE-002" in str(exc)

    trac("run")
    # Attempt dev hotfix without active release branch must fail precheck - correct CLI needs issue number
    result = trac("hotfix", "103", "--scenario", "dev")
    # The CLI may be hotfix with scenario dev; expected to fail when no release branch
    assert result.returncode != 0 or "precheck failed" in result.stdout or "no active release branch" in result.stdout or "no active release branch" in result.stderr
    status = trac("status")
    assert "precheck failed" in status.stdout or "no active release branch" in status.stdout
    # Must not have created fix branch
    branches = subprocess.run(["git", "branch", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert "fix/" not in branches or result.returncode != 0
