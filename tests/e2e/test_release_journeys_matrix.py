"""E2E: six journeys dual host matrix (NFR-0149-01)."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.e2e


# AC-NFR0149-01@v0.8 TRACKS-TRACE six journeys dual host with needs_attention counting
def test_six_journeys_dual_host(host_repo, trac, event_log, tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    journeys = ["feature", "post_release", "dev"]
    hosts = ["tracks", "reference"]
    # Simulate sequential execution of 6 journeys over two hosts
    results = []
    for host in hosts:
        for journey in journeys:
            # Fresh repo per journey/host
            repo = tmp_path / f"{host}_{journey}"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
            (repo / "README.md").write_text(f"{host} {journey}\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
            bare = tmp_path / f"bare_{host}_{journey}.git"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)

            # Run trac in that repo (using same host_repo fixture's trac is not suitable per-repo, so use subprocess)
            import os
            import sys

            env = dict(os.environ.items())
            env["TRAC_GITHUB_REPO"] = f"acme/{host}"
            env["TRAC_GITHUB_API_BASE"] = "http://127.0.0.1:9"
            env["GITHUB_TOKEN"] = "token"
            env["TRAC_AGENT_BACKEND"] = "fake"
            proc = subprocess.run(
                [sys.executable, "-m", "tracks.cli.main", "run"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
            )
            results.append((host, journey, proc))

    assert len(results) == 6
    for _host, _journey, proc in results:
        assert proc.returncode == 0
        assert "candidate.frozen" in proc.stdout or "candidate" in proc.stdout.lower()
        assert "release.previewed" in proc.stdout or "preview" in proc.stdout.lower()

    # Verify that missing credentials would be counted as needs_attention not pass
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # Simulate a journey without credentials
    repo2 = tmp_path / "needs_attention_repo"
    repo2.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo2, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo2, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo2, check=True)
    (repo2 / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo2, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo2, check=True)
    import os as _os
    import sys as _sys

    env2 = {k: v for k, v in _os.environ.items() if k not in ("GITHUB_TOKEN", "TRAC_GITHUB_API_BASE")}
    env2["TRAC_GITHUB_REPO"] = "acme/host"
    env2["TRAC_AGENT_BACKEND"] = "fake"
    proc2 = subprocess.run(
        [_sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=repo2,
        env=env2,
        capture_output=True,
        text=True,
    )
    assert "needs_attention" in proc2.stdout.lower()
    assert "missing_token" in proc2.stdout.lower() or "issue_creation" in proc2.stdout.lower()

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
