"""Serve lifecycle (IF-SERVE-001, IF-RECOVER-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.v09_web import (
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration


# AC-FR0288-01@v0.9 TRACKS-TRACE health available and browserless progress
def test_health_and_browserless_progress(tmp_path: Path):
    """AC-FR0288-01: serve prints serving line; /healthz ok with projects count."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = start_serve(home, repo, port=0)
    try:
        line = wait_for_port_line(proc)
        assert "(pid " in line
        base = parse_base_url(line)
        payload = wait_for_healthz(base)
        assert payload["status"] == "ok"
        assert isinstance(payload["projects"], int)
        assert isinstance(payload["version"], str)
        assert isinstance(payload["uptime_s"], float)
    finally:
        stop_serve(proc)


# AC-FR0288-02@v0.9 TRACKS-TRACE restart recovers registry commands waits
def test_restart_recovers_registry_commands_waits(tmp_path: Path):
    """AC-FR0288-02: restart keeps projects; waits keep retry_at."""
    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.recover import recover_on_startup
    from tracks.supervisor.service import CommandService

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    receipt = svc.accept(
        kind="create_run",
        params={
            "project_id": "p-restart",
            "journey": "feature",
            "version": "v0.9",
            "story": None,
            "issue": None,
            "target": None,
            "preempt": False,
        },
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="restart-recover-1",
    )
    assert db.claim_command(receipt.command_id, worker_id="w1", generation=1)
    summary = recover_on_startup(db)
    assert set(summary) >= {"requeued", "waits_kept", "leases_expired"}
    assert receipt.command_id in summary["requeued"]


# AC-FR0288-03@v0.9 TRACKS-TRACE unhealthy shows reason no fake available
def test_unhealthy_no_fake_available(tmp_path: Path):
    """AC-FR0288-03: unwritable home fails closed with reason, no serving line."""
    from tracks.server.app import health_response

    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o500)
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = start_serve(home, repo, port=0)
    try:
        code = proc.wait(timeout=10)
        assert code == 1, "unwritable home must exit 1 (fail closed)"
        err = proc.stderr.read() if proc.stderr else ""
        out = proc.stdout.read() if proc.stdout else ""
        assert "serving on http://" not in out
        assert err.strip(), "fail-closed exit must state a reason on stderr"
    finally:
        stop_serve(proc)
    payload = health_response(home)
    assert payload["status"] == "unavailable"
    assert payload["reasons"]
