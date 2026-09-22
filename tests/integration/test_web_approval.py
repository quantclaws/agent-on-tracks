"""Web approval gate (IF-WEBGATE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.webgate_seed import (
    accepted_commands,
    command_service,
    rejected_reasons,
    seed_approval_run,
    supersede_trio,
)

pytestmark = pytest.mark.integration


# AC-FR0308-01@v0.9 TRACKS-TRACE approval bound and advances
def test_approval_bound_and_advances(tmp_path: Path):
    """AC-FR0308-01: approval records actor revision decision, run advances."""
    home, _repo, current = seed_approval_run(tmp_path, "run-1")
    svc = command_service(home)
    receipt = svc.accept(
        kind="record_stage_approval",
        params={
            "run_id": "run-1",
            "object": "spec",
            "expected_revision": current,
            "decision": "approve",
        },
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="appr-ok-1",
    )
    assert receipt.command_id
    assert receipt.status == "accepted"
    assert svc.status(receipt.command_id)["command_id"] == receipt.command_id
    assert [row["kind"] for row in accepted_commands(home)] == ["record_stage_approval"]


# AC-FR0308-02@v0.9 TRACKS-TRACE stale revision rejected
def test_stale_revision_rejected(tmp_path: Path):
    """AC-FR0308-02: expired revision approval rejected, state unmoved."""
    from tracks.supervisor.service import Rejection

    home, repo, reviewed = seed_approval_run(tmp_path, "run-1")
    supersede_trio(repo)  # the material moved past the reviewed revision
    svc = command_service(home)
    try:
        svc.accept(
            kind="record_stage_approval",
            params={
                "run_id": "run-1",
                "object": "spec",
                "expected_revision": reviewed,
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
    assert "stale_revision" in rejected_reasons(home)
    assert accepted_commands(home) == [], "a rejected approval persists no command"
