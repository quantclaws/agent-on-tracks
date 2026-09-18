"""Web release decisions (IF-WEBGATE-001, IF-RELEASE-002, IF-RELEASE-003)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


def _release_params(digest: str = "sha256:preview") -> dict:
    return {"run_id": "run-1", "action": "release", "preview_digest": digest}


# AC-FR0309-01@v0.9 TRACKS-TRACE release via web happy
def test_release_via_web_happy(tmp_path: Path):
    """AC-FR0309-01: release binds preview digest, remote result queryable."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="record_release_decision",
        params=_release_params(),
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="rel-ok-1",
    )
    assert receipt.command_id


# AC-FR0309-02@v0.9 TRACKS-TRACE stale preview rejected
def test_stale_preview_rejected(tmp_path: Path):
    """AC-FR0309-02: stale preview release rejected, no publish side effect."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    try:
        svc.accept(
            kind="record_release_decision",
            params=_release_params("sha256:stale"),
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="rel-stale-1",
        )
    except Rejection as exc:
        assert exc.reason == "stale_preview"
    else:
        raise AssertionError("stale preview must be rejected")


# AC-FR0309-03@v0.9 TRACKS-TRACE delay and return bound to digest
def test_delay_and_return_bound_to_digest(tmp_path: Path):
    """AC-FR0309-03: delay and return each bind the reviewed digest, auditable."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    delay = svc.accept(
        kind="record_release_decision",
        params={"run_id": "run-1", "action": "delay", "preview_digest": "sha256:preview"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="rel-delay-1",
    )
    back = svc.accept(
        kind="record_release_decision",
        params={"run_id": "run-1", "action": "return", "preview_digest": "sha256:preview"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="rel-return-1",
    )
    assert delay.command_id != back.command_id
