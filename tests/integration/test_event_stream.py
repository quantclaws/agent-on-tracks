"""Event stream (IF-STREAM-001)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.paths import tracks_home
from tracks.server import api_events
from tracks.server.redaction import SecretRedactor
from tracks.store import Store
from tracks.supervisor import db as sdb

pytestmark = pytest.mark.integration


def _seed_stream_run(tmp_path: Path, run_id: str) -> Path:
    """A registered run with tracks.db + service-plane events: the merged
    event source the subscription replays (interfaces §1e)."""
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-IMPL"})
        store.append(run_id, "v0.9", "taskgraph.committed", {"task_count": 2})
    finally:
        store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", "local-user", "2026-09-23T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    db = sdb.ServiceDB(home)
    db.append_event(
        "command.accepted", {"command_id": "cmd-1", "kind": "pause_run"}, run_id=run_id
    )
    return home


def _request(home: Path, run_id: str, **query: str) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(home=home, redactor=SecretRedactor({}))),
        path_params={"run_id": run_id},
        query_params=dict(query),
    )


def _row_key(row: dict) -> tuple[str, int, int]:
    return (row["ts"], 0 if row["source"] == "tracks" else 1, row["seq"])


def _row_cursor(row: dict) -> str:
    return api_events.encode_cursor(*_row_key(row))


# AC-FR0306-01@v0.9 TRACKS-TRACE live push without refresh
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_live_push_without_refresh(tmp_path: Path):
    """AC-FR0306-01: a pushed frame carries the id/event/data segments and a
    cursor that matches the event it delivers."""
    cursor = api_events.encode_cursor("2030-01-01T00:00:00+00:00", 1, 7)
    assert isinstance(cursor, str)
    assert api_events.decode_cursor(cursor) == ("2030-01-01T00:00:00+00:00", 1, 7)

    home = _seed_stream_run(tmp_path, "run-1")
    response = asyncio.run(api_events.stream_events(_request(home, "run-1")))
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"

    frame = asyncio.run(response.body_iterator.__anext__())
    assert frame.startswith("id: "), f"a frame must open with its cursor: {frame!r}"
    lines = frame.split("\n")
    assert lines[1].startswith("event: "), f"frame missing the event segment: {frame!r}"
    assert lines[2].startswith("data: "), f"frame missing the data segment: {frame!r}"
    frame_cursor = lines[0][len("id: ") :]
    event_type = lines[1][len("event: ") :]
    delivered = json.loads(lines[2][len("data: ") :])
    assert api_events.decode_cursor(frame_cursor) == _row_key(delivered), (
        "the frame cursor must match the event it carries (§2b #28)"
    )
    assert event_type == delivered["type"] == "stage.entered"


# AC-FR0306-02@v0.9 TRACKS-TRACE reconnect backfill no regression
def test_reconnect_backfill_no_regression(tmp_path: Path):
    """AC-FR0306-02: a reconnect backfills exactly the missing events — no
    duplicates, no regression, and an idle poll keeps the client's position."""
    home = _seed_stream_run(tmp_path, "run-1")

    snapshot = asyncio.run(api_events.stream_events(_request(home, "run-1", wait="0")))
    assert snapshot.status_code == 200
    events = snapshot.payload["events"]
    assert [row["type"] for row in events] == [
        "stage.entered",
        "taskgraph.committed",
        "command.accepted",
    ]
    keys = [_row_key(row) for row in events]
    assert keys == sorted(keys), "the merged stream must follow (ts, source_rank, seq)"

    # reconnect from the first event's cursor: the remaining events, no repeat
    resumed = asyncio.run(
        api_events.stream_events(_request(home, "run-1", after=_row_cursor(events[0]), wait="0"))
    )
    backfilled = resumed.payload["events"]
    assert [row["type"] for row in backfilled] == ["taskgraph.committed", "command.accepted"]
    assert all(_row_key(row) > _row_key(events[0]) for row in backfilled)
    assert resumed.payload["cursor"] == _row_cursor(backfilled[-1])

    # an idle poll after the newest cursor stays empty and keeps the position
    idle = asyncio.run(
        api_events.stream_events(
            _request(home, "run-1", after=snapshot.payload["cursor"], wait="0")
        )
    )
    assert idle.payload["events"] == []
    assert idle.payload["cursor"] == snapshot.payload["cursor"]

    # a malformed cursor is ignored leniently, never a regression to empty
    malformed = asyncio.run(
        api_events.stream_events(_request(home, "run-1", after="not-a-cursor", wait="0"))
    )
    assert malformed.status_code == 200
    assert [row["type"] for row in malformed.payload["events"]] == [
        row["type"] for row in events
    ]

# OOB verified 2026-09-23T08:36Z: green-on-arrival, island-2 sweep (run 01M2QTJB).
