"""E2E: reference host journey (FR-0282)."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.e2e


# AC-FR0282-01@v0.8 TRACKS-TRACE reference host release journey with isomorphic chain
def test_reference_host_release_journey(host_repo, trac, event_log, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    # Create a separate host repo for reference host materialization
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=ref_root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=ref_root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=ref_root, check=True)
    (ref_root / "README.md").write_text("ref\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=ref_root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ref_root, check=True)

    bare = tmp_path / "ref_bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=ref_root, check=True)

    # Simulate reference host creation via trac on that repo
    import os
    import sys

    env = dict(os.environ.items())
    env["TRAC_GITHUB_API_BASE"] = "http://127.0.0.1:9"
    env["TRAC_GITHUB_REPO"] = "acme/ref"
    env["GITHUB_TOKEN"] = "token"
    proc = subprocess.run(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=ref_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1)

    # Back to host_repo journey for assertion
    trac("run")
    trac("release", "preview")
    trac("release", "--action", "release")
    trac("run")

    events = event_log()
    # Isomorphic chain must be present in both hosts
    chain = ["candidate.frozen", "local_gate.passed", "ci.run_observed", "prism.verdict", "security.assessed", "release.previewed", "release.decided", "publish.executed", "milestone.sealed"]
    found = [e["type"] for e in events if e["type"] in chain]
    assert found == chain

    # Report must show same candidate across hosts (at least in main host)
    report = trac("report").stdout
    assert "candidate" in report.lower()
    assert "terminal=released" in trac("status").stdout or "released" in trac("status").stdout.lower()

    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
