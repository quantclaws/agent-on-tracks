"""Web release decisions (IF-WEBGATE-001, IF-RELEASE-002, IF-RELEASE-003)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support.webgate_seed import (
    accepted_commands,
    command_service,
    rejected_reasons,
    seed_release_run,
)

pytestmark = pytest.mark.integration


def _release_params(digest: str) -> dict:
    return {"run_id": "run-1", "action": "release", "preview_digest": digest}


# AC-FR0309-01@v0.9 TRACKS-TRACE release via web happy
def test_release_via_web_happy(tmp_path: Path):
    """AC-FR0309-01: release binds preview digest, remote result queryable."""
    home, _repo, digest = seed_release_run(tmp_path, "run-1")
    svc = command_service(home)
    receipt = svc.accept(
        kind="record_release_decision",
        params=_release_params(digest),
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="rel-ok-1",
    )
    assert receipt.command_id
    assert receipt.status == "accepted"
    assert [row["kind"] for row in accepted_commands(home)] == ["record_release_decision"]


# AC-FR0309-02@v0.9 TRACKS-TRACE stale preview rejected
def test_stale_preview_rejected(tmp_path: Path):
    """AC-FR0309-02: stale preview release rejected, no publish side effect."""
    from tracks.supervisor.service import Rejection

    home, repo, digest = seed_release_run(tmp_path, "run-1", stale=True)
    svc = command_service(home)
    try:
        svc.accept(
            kind="record_release_decision",
            params=_release_params(digest),
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="rel-stale-1",
        )
    except Rejection as exc:
        assert exc.reason == "stale_preview"
    else:
        raise AssertionError("stale preview must be rejected")
    assert "stale_preview" in rejected_reasons(home)
    assert accepted_commands(home) == [], "a rejected decision persists no command"


# AC-FR0309-03@v0.9 TRACKS-TRACE delay and return bound to digest
def test_delay_and_return_bound_to_digest(tmp_path: Path):
    """AC-FR0309-03: delay and return each bind the reviewed digest, auditable."""
    home, _repo, digest = seed_release_run(tmp_path, "run-1")
    svc = command_service(home)
    delay = svc.accept(
        kind="record_release_decision",
        params={"run_id": "run-1", "action": "delay", "preview_digest": digest},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="rel-delay-1",
    )
    back = svc.accept(
        kind="record_release_decision",
        params={"run_id": "run-1", "action": "return", "preview_digest": digest},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="rel-return-1",
    )
    assert delay.command_id != back.command_id

    # each decision is auditable as its own command, bound to the reviewed
    # digest it was made under (§1b #7 / §1c commands row)
    assert [row["kind"] for row in accepted_commands(home)] == [
        "record_release_decision",
        "record_release_decision",
    ]
    from tracks.supervisor.db import ServiceDB

    rows = ServiceDB(home)
    assert rows.get_command(delay.command_id)["params_json"] == json.dumps(
        {"run_id": "run-1", "action": "delay", "preview_digest": digest}, sort_keys=True
    )
    assert rows.get_command(back.command_id)["params_json"] == json.dumps(
        {"run_id": "run-1", "action": "return", "preview_digest": digest}, sort_keys=True
    )
    assert {row["kind"] for row in accepted_commands(home)} == {"record_release_decision"}

# OOB verified 2026-09-23T08:36Z: green-on-arrival, island-2 sweep (run 01M2QTJB).
