"""Controlled retry (IF-WEBGATE-001, IF-ESCAPE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.webgate_seed import (
    accepted_commands,
    command_service,
    rejected_reasons,
    seed_retry_run,
)

pytestmark = pytest.mark.integration


# AC-FR0311-01@v0.9 TRACKS-TRACE retry from legal position no duplicates
def test_retry_from_legal_position_no_duplicates(tmp_path: Path):
    """AC-FR0311-01: retry resumes legally without repeating side effects."""
    home, _repo = seed_retry_run(tmp_path, "run-1", legal=True)
    svc = command_service(home)
    receipt = svc.accept(
        kind="retry_run",
        params={"run_id": "run-1", "clear_evidence": False},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="retry-ok-1",
    )
    assert receipt.command_id
    assert receipt.status == "accepted"
    assert [row["kind"] for row in accepted_commands(home)] == ["retry_run"]


# AC-FR0311-02@v0.9 TRACKS-TRACE stale evidence retry rejected
def test_stale_evidence_retry_rejected(tmp_path: Path):
    """AC-FR0311-02: expired-evidence retry rejected; unrecoverable has no entry."""
    from tracks.supervisor.service import Rejection

    home, _repo = seed_retry_run(tmp_path, "run-stale", legal=False)
    svc = command_service(home)
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
        assert exc.reason == "validation_failed"
        assert exc.detail and exc.detail.strip(), "the rejection must carry the reason"
    else:
        raise AssertionError("stale evidence retry must be rejected")
    assert "validation_failed" in rejected_reasons(home)
    assert accepted_commands(home) == [], "a rejected retry persists no command"
