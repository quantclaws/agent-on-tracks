"""Controlled retry (IF-WEBGATE-001, IF-ESCAPE-001)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support.webgate_seed import (
    accepted_commands,
    command_rows,
    command_service,
    rejected_reasons,
    seed_retry_run,
)
from tracks.paths import tracks_home
from tracks.store import Store

pytestmark = pytest.mark.integration


# AC-FR0311-01@v0.9 TRACKS-TRACE retry from legal position no duplicates
def test_retry_from_legal_position_no_duplicates(tmp_path: Path):
    """AC-FR0311-01: retry resumes legally without repeating side effects."""
    home, repo = seed_retry_run(tmp_path, "run-1", legal=True)
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
    # v0.12 executes retry_run synchronously at acceptance (2bd50d6): the row
    # is issued and completed in the same accept, so no worker-facing
    # "accepted" row survives. The retry side effect is still exactly once.
    rows = command_rows(home)
    assert [row["kind"] for row in rows] == ["retry_run"]
    assert rows[0]["status"] == "completed"
    assert json.loads(rows[0]["result_json"]) == {"kind": "retry_recorded"}
    store = Store(tracks_home(repo))
    try:
        retries = [e for e in store.events("run-1") if e.type == "human.retry"]
    finally:
        store.close()
    assert len(retries) == 1, "the retry side effect must not repeat"
    assert retries[0].payload["clear_evidence"] is False


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
