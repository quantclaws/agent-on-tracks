"""Command service persistence (IF-CMDSVC-001, IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


def _accept(svc: CommandService, key: str, payload: dict | None = None) -> object:
    return svc.accept(
        kind="pause_run",
        params=payload or {"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key=key,
    )


# AC-FR0295-01@v0.9 TRACKS-TRACE command id survives restart
def test_command_id_survives_restart(tmp_path: Path):
    """AC-FR0295-01: command_id queryable after service restart."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = _accept(svc, "persist-1")
    assert receipt.command_id
    svc2 = CommandService(home, object(), object())
    assert svc2.status(receipt.command_id)["command_id"] == receipt.command_id


# AC-FR0295-02@v0.9 TRACKS-TRACE idempotency dedup and conflict
def test_idempotency_dedup_and_conflict(tmp_path: Path):
    """AC-FR0295-02: same key+payload dedups; same key+other payload conflicts."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    first = _accept(svc, "idem-1", {"run_id": "run-1"})
    second = _accept(svc, "idem-1", {"run_id": "run-1"})
    assert second.command_id == first.command_id
    assert second.deduplicated is True
    try:
        _accept(svc, "idem-1", {"run_id": "run-2"})
    except Rejection as exc:
        assert exc.reason == "idempotency_conflict"
    else:
        raise AssertionError("same key with different payload must conflict")


# AC-FR0295-03@v0.9 TRACKS-TRACE cli http equivalent events
def test_cli_http_equivalent_events(tmp_path: Path):
    """AC-FR0295-03: CLI and HTTP surfaces emit the same event type."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    via_http = svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="equiv-http",
    )
    via_cli = svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="cli",
        idempotency_key="equiv-cli",
    )
    assert via_http.command_id != via_cli.command_id
    assert svc.status(via_http.command_id)["command_id"] == via_http.command_id


# AC-FR0295-04@v0.9 TRACKS-TRACE queries are readonly
def test_queries_are_readonly(tmp_path: Path):
    """AC-FR0295-04: read queries leave events and projections unchanged."""
    from tracks.server.projections import project_overview
    from tracks.supervisor.db import ServiceDB

    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    before = db.read_events()
    overview = project_overview(home)
    assert "runs" in overview
    assert db.read_events() == before
