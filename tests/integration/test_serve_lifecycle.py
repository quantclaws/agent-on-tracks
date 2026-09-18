"""Serve lifecycle (IF-SERVE-001, IF-RECOVER-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# AC-FR0288-01@v0.9 TRACKS-TRACE health available and browserless progress
def test_health_and_browserless_progress(tmp_path: Path):
    """AC-FR0288-01: serve prints serving line; /healthz ok with projects count."""
    from tracks.cli.serve_cmd import cmd_serve
    from tracks.server.app import health_response

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        cmd_serve(
            repo,
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--port",
            "0",
            "--password-stdin",
        )
    except NotImplementedError as exc:
        assert str(exc) == "IF-SERVE-001"
    else:
        raise AssertionError("serve stub must raise IF-SERVE-001 before Devon implements it")
    try:
        health_response(home)
    except NotImplementedError as exc:
        assert str(exc) == "IF-SERVE-001"
    else:
        raise AssertionError("health stub must raise IF-SERVE-001 before Devon implements it")


# AC-FR0288-02@v0.9 TRACKS-TRACE restart recovers registry commands waits
def test_restart_recovers_registry_commands_waits(tmp_path: Path):
    """AC-FR0288-02: restart keeps projects; waits keep retry_at."""
    from tracks.cli.serve_cmd import cmd_serve
    from tracks.supervisor.recover import recover_on_startup

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        cmd_serve(repo, "--repo", str(repo), "--home", str(home), "--port", "0")
    except NotImplementedError as exc:
        assert str(exc) == "IF-SERVE-001"
    else:
        raise AssertionError("serve stub must raise IF-SERVE-001 before Devon implements it")
    try:
        recover_on_startup(object())
    except NotImplementedError as exc:
        assert str(exc) == "IF-RECOVER-001"
    else:
        raise AssertionError("recover stub must raise IF-RECOVER-001 before Devon implements it")


# AC-FR0288-03@v0.9 TRACKS-TRACE unhealthy shows reason no fake available
def test_unhealthy_no_fake_available(tmp_path: Path):
    """AC-FR0288-03: unwritable home fails closed with reason, no serving line."""
    from tracks.cli.serve_cmd import cmd_serve

    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        cmd_serve(repo, "--repo", str(repo), "--home", str(tmp_path / "home"), "--port", "0")
    except NotImplementedError as exc:
        assert str(exc) == "IF-SERVE-001"
    else:
        raise AssertionError("serve stub must raise IF-SERVE-001 before Devon implements it")
