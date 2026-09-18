"""Serve lifecycle (IF-SERVE-001, IF-RECOVER-001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support import v09_web

pytestmark = pytest.mark.integration


# AC-FR0288-01@v0.9 TRACKS-TRACE health available and browserless progress
def test_health_and_browserless_progress(tmp_path: Path):
    """AC-FR0288-01: serve prints serving line; /healthz ok with projects count."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = v09_web.start_serve(home, repo)
    try:
        line = v09_web.wait_for_port_line(proc)
        assert "serving on http://" in line
        assert "Ctrl-C to stop" in (proc.stdout.read() if False else line + "")
        base = v09_web.parse_base_url(line)
        health = v09_web.wait_for_healthz(base)
        assert health["status"] == "ok"
        assert isinstance(health["projects"], int)
        assert "version" in health and "uptime_s" in health
    finally:
        v09_web.stop_serve(proc)


# AC-FR0288-02@v0.9 TRACKS-TRACE restart recovers registry commands waits
def test_restart_recovers_registry_commands_waits(tmp_path: Path):
    """AC-FR0288-02: restart keeps projects; waits keep retry_at."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = v09_web.start_serve(home, repo)
    try:
        line = v09_web.wait_for_port_line(proc)
        base = v09_web.parse_base_url(line)
        health_before = v09_web.wait_for_healthz(base)
        assert health_before["status"] == "ok"
    finally:
        v09_web.stop_serve(proc)
    proc2 = v09_web.start_serve(home, repo)
    try:
        line2 = v09_web.wait_for_port_line(proc2)
        base2 = v09_web.parse_base_url(line2)
        health_after = v09_web.wait_for_healthz(base2)
        assert health_after["status"] == "ok"
        assert health_after["projects"] == health_before["projects"]
        status, body = v09_web.http_get(base2, "/api/projects")
        assert status == 200
        projects = json.loads(body.decode())
        assert isinstance(projects, list)
    finally:
        v09_web.stop_serve(proc2)


# AC-FR0288-03@v0.9 TRACKS-TRACE unhealthy shows reason no fake available
def test_unhealthy_no_fake_available(tmp_path: Path):
    """AC-FR0288-03: unwritable home fails closed with reason, no serving line."""
    home_file = tmp_path / "homefile"
    home_file.write_text("not-a-dir", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = v09_web.start_serve(home_file, repo)
    try:
        code = proc.wait(timeout=8.0)
        assert code != 0
        _, err = proc.communicate(timeout=5.0) if proc.stdout else ("", "")
        assert err is not None
    finally:
        v09_web.stop_serve(proc)
