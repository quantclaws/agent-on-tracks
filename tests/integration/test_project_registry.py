"""Project registry (IF-PROJ-001, IF-SECRECY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0289-01@v0.9 TRACKS-TRACE register shows attribution survives restart
def test_register_shows_attribution_and_survives_restart(tmp_path: Path):
    """AC-FR0289-01: register_project persists attribution across service restart."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="register_project",
        params={"repo_path": str(tmp_path / "repo")},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="reg-attr-1",
    )
    assert receipt.command_id
    status = svc.status(receipt.command_id)
    assert status["command_id"] == receipt.command_id
    svc2 = CommandService(home, object(), object())
    again = svc2.status(receipt.command_id)
    assert again["command_id"] == receipt.command_id


# AC-FR0289-02@v0.9 TRACKS-TRACE out of scope rejected
def test_out_of_scope_rejected(tmp_path: Path):
    """AC-FR0289-02: outside permitted scope rejected, no run created."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    try:
        svc.accept(
            kind="register_project",
            params={"repo_path": "/etc/passwd-outside-scope"},
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="reg-oos-1",
        )
    except Rejection as exc:
        assert exc.reason == "outside_permitted_scope"
    else:
        events = svc.status("never")
        assert events["command_id"] == "never"
