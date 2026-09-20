"""Readiness probes (IF-PROJ-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.db import ServiceDB
from tracks.supervisor.readiness import PROBE_KINDS, run_readiness
from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0290-01@v0.9 TRACKS-TRACE readiness items displayed
def test_readiness_items_displayed(tmp_path: Path):
    """AC-FR0290-01: four probes each report ok plus reason."""
    results = run_readiness(tmp_path)
    by_check = {r.check: r for r in results}
    assert set(by_check) == set(PROBE_KINDS)
    for kind in PROBE_KINDS:
        assert by_check[kind].ok in (True, False)
        if not by_check[kind].ok:
            assert by_check[kind].reason


def _create_run_params() -> dict:
    """create_run params used by the blocked and the repaired attempt."""
    return {
        "project_id": "p1",
        "journey": "feature",
        "version": "v0.9",
        "story": "readiness fixture",
        "issue": None,
        "target": None,
        "preempt": False,
    }


def _seed_readiness(db: ServiceDB, *, tools_ok: bool) -> None:
    """Persist one project.readiness_checked event (interfaces §1a #5)."""
    checks = {
        "contract": {"ok": True, "reason": None},
        "harness_model": {"ok": True, "reason": None},
        "credentials_ref": {"ok": True, "reason": None},
        "tools": {"ok": tools_ok, "reason": None if tools_ok else "git lfs not installed"},
    }
    db.append_event(
        "project.readiness_checked",
        {"project_id": "p1", "ok": tools_ok, "checks": checks, "actor": "human"},
        project_id="p1",
    )


# AC-FR0290-02@v0.9 TRACKS-TRACE not ready blocks run creation
def test_not_ready_blocks_run_creation(tmp_path: Path):
    """AC-FR0290-02: explicit-negative readiness blocks create_run with a
    concrete reason and creates no run; a repaired recheck unlocks creation."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    _seed_readiness(db, tools_ok=False)
    svc = CommandService(home, db, object())

    try:
        svc.accept(
            kind="create_run",
            params=_create_run_params(),
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="ready-block-1",
        )
    except Rejection as exc:
        assert exc.reason == "validation_failed"
        assert exc.detail and exc.detail.strip(), "the block must carry a concrete reason"
    else:
        raise AssertionError("create_run must be rejected while a readiness check is failing")

    events = db.read_events()
    assert any(
        event["type"] == "command.rejected"
        and (event["payload"] or {}).get("reason") == "validation_failed"
        for event in events
    ), "the rejected attempt must be audited"
    assert not any(
        event["type"] == "command.accepted" for event in events
    ), "a rejected create_run must not be accepted (no run)"
    assert db.find_by_idempotency("ready-block-1") is None, "no command/run row for the block"

    _seed_readiness(db, tools_ok=True)
    receipt = svc.accept(
        kind="create_run",
        params=_create_run_params(),
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="ready-block-2",
    )
    assert receipt.command_id
    stored = db.find_by_idempotency("ready-block-2")
    assert stored is not None and stored["command_id"] == receipt.command_id
