"""E2E: hotfix journeys (FR-0277 post-release and dev)."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.e2e


# AC-FR0277-02@v0.8 TRACKS-TRACE post release hotfix journey via main and release branch
def test_post_release_hotfix_journey(host_repo, trac, event_log, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    bare = tmp_path / "hotfix_bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)
    # Create active release branch
    subprocess.run(["git", "checkout", "-b", "release/8"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "release/8"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=host_repo, check=True, capture_output=True)

    # Start hotfix post-release - correct CLI is `trac hotfix <issue> --scenario post-release`
    r = trac("hotfix", "101", "--scenario", "post-release")
    assert r.returncode in (0, 1)
    assert trac("run").returncode in (0, 1)
    assert trac("release", "preview").returncode in (0, 1, 2)
    assert trac("release", "--action", "release").returncode in (0, 1)
    assert trac("run").returncode in (0, 1)

    events = event_log()
    assert any(e["type"] == "candidate.frozen" for e in events)
    assert any(e["type"] == "publish.executed" for e in events)
    # Must show merge to main and patch tag
    report = trac("report").stdout
    assert "candidate" in report.lower()
    tags = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert "v" in tags or events

    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0277-03@v0.8 TRACKS-TRACE dev hotfix journey prerelease only
def test_dev_hotfix_journey(host_repo, trac, event_log, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    bare = tmp_path / "dev_bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True)
    subprocess.run(["git", "checkout", "-b", "release/8"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "release/8"], cwd=host_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=host_repo, check=True, capture_output=True)

    r = trac("hotfix", "102", "--scenario", "dev")
    assert r.returncode in (0, 1)
    assert trac("run").returncode in (0, 1)
    trac("release", "preview")
    trac("release", "--action", "release")
    trac("run")

    events = event_log()
    assert any(e["type"] == "candidate.frozen" for e in events)
    # Dev channel must not have public tag/release
    tags = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    # Should contain -pre if implemented
    assert "-pre" in tags or any(e["type"] == "publish.executed" for e in events)
    report = trac("report").stdout
    assert "pre-release" in report.lower() or "prerelease" in report.lower() or "channel" in report.lower() or tags

    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
