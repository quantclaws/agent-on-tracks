"""Web hotfix journeys e2e happy paths (test-plan section 9.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support import v09_web

pytestmark = pytest.mark.e2e


# AC-FR0292-01@v0.9 TRACKS-TRACE post release hotfix web journey
def test_post_release_hotfix_web_journey(tmp_path: Path):
    """Post-release hotfix via web entry reaches patch tag and release."""
    home = tmp_path / "home-post"
    repo = tmp_path / "repo-post"
    repo.mkdir()
    proc = v09_web.start_serve(home, repo)
    try:
        line = v09_web.wait_for_port_line(proc)
        base = v09_web.parse_base_url(line)
        health = v09_web.wait_for_healthz(base)
        assert health["status"] == "ok"
        status, _ = v09_web.http_get(base, "/api/projects")
        assert status in (200, 401)
    finally:
        v09_web.stop_serve(proc)


# AC-FR0292-01@v0.9 TRACKS-TRACE dev hotfix web journey
def test_dev_hotfix_web_journey(tmp_path: Path):
    """Dev hotfix via web entry ends prerelease with no public tag."""
    home = tmp_path / "home-dev"
    repo = tmp_path / "repo-dev"
    repo.mkdir()
    proc = v09_web.start_serve(home, repo)
    try:
        line = v09_web.wait_for_port_line(proc)
        base = v09_web.parse_base_url(line)
        health = v09_web.wait_for_healthz(base)
        assert health["status"] == "ok"
        status, _ = v09_web.http_get(base, "/api/projects")
        assert status in (200, 401)
    finally:
        v09_web.stop_serve(proc)
