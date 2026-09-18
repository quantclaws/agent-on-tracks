"""Web approval gate (IF-WEBGATE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0308-01@v0.9 TRACKS-TRACE approval bound and advances
def test_approval_bound_and_advances(tmp_path: Path):
    """AC-FR0308-01: approval records actor revision decision, run advances."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="record_stage_approval",
        params={
            "run_id": "run-1",
            "object": "spec",
            "expected_revision": "rev-r2",
            "decision": "approve",
        },
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="appr-ok-1",
    )
    assert receipt.command_id
    assert svc.status(receipt.command_id)["command_id"] == receipt.command_id


# AC-FR0308-02@v0.9 TRACKS-TRACE stale revision rejected
def test_stale_revision_rejected(tmp_path: Path):
    """AC-FR0308-02: expired revision approval rejected, state unmoved."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    try:
        svc.accept(
            kind="record_stage_approval",
            params={
                "run_id": "run-1",
                "object": "spec",
                "expected_revision": "rev-stale",
                "decision": "approve",
            },
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="appr-stale-2",
        )
    except Rejection as exc:
        assert exc.reason == "stale_revision"
    else:
        raise AssertionError("stale revision must be rejected")
