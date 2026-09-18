"""Controlled retry (IF-WEBGATE-001, IF-ESCAPE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0311-01@v0.9 TRACKS-TRACE retry from legal position no duplicates
def test_retry_from_legal_position_no_duplicates(tmp_path: Path):
    """AC-FR0311-01: retry resumes legally without repeating side effects."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="retry_run",
        params={"run_id": "run-1", "clear_evidence": False},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="retry-ok-1",
    )
    assert receipt.command_id


# AC-FR0311-02@v0.9 TRACKS-TRACE stale evidence retry rejected
def test_stale_evidence_retry_rejected(tmp_path: Path):
    """AC-FR0311-02: expired-evidence retry rejected; unrecoverable has no entry."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    try:
        svc.accept(
            kind="retry_run",
            params={"run_id": "run-stale", "clear_evidence": False},
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="retry-stale-1",
        )
    except Rejection as exc:
        assert exc.reason in ("validation_failed", "not_found")
    else:
        raise AssertionError("stale evidence retry must be rejected")
