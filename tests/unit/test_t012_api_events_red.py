"""T-012 RED: event subscription route (IF-STREAM-001).

Devon-owned unit RED for the T-012 delivery slice
(``tracks/server/api_events.py``): the event subscription surface of
interfaces §2b #28 — SSE frames carrying the three ``id``/``event``/``data``
segments, cursor catch-up with no duplicates and no regression (§4a #5),
the ``wait=0`` one-shot JSON polling batch (NFR-0152 caps), and the opaque
merged-order cursor of §1e.

The unit seam is the handler contract the app factory (T-014) binds to,
identical to the T-011 convention: each handler receives a request exposing

    request.app.state.home      -> Path of the service home (service.db)
    request.app.state.redactor  -> SecretRedactor applied at the outlet
    request.path_params         -> path parameters (run_id)
    request.query_params        -> query parameters (after / wait)

The ``wait=0`` polling batch returns the starlette-free status/payload
envelope (``api_query.QueryResponse`` shape); the SSE subscription returns
the StreamingResponse mirror (``media_type="text/event-stream"`` plus a
``body_iterator`` of frames) so the composition root maps it 1:1. Frames
are consumed with a per-frame timeout: a live stream must deliver its
backfill promptly; live-push behaviour itself is the T-INT deferred anchor.

Fixtures seed the documented storage contract for real (interfaces §1c
service.db through ``ServiceDB``, the run's tracks.db through ``Store``) —
nothing here mocks the system under test. The current handler bodies raise
their IF-STREAM-001 stub token; every failing node guards that stub state
into a real ``AssertionError`` (no stub_token, no assembly errors).

AC: FR-0306/NFR-0152 — TRACKS-TRACE IF-STREAM-001.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import paths
from tracks.server import api_events, projections
from tracks.server.redaction import SecretRedactor
from tracks.store import Store
from tracks.supervisor import db as sdb

_VERSION = "v0.9"
_RUN = "run-stream"
_PAST_TS = "2026-09-20T00:00:01+00:00"
_FUTURE_TS = "2099-01-01T00:00:00+00:00"
_FRAME_TIMEOUT_S = 5.0
_EVENT_ROW_KEYS = {
    "source",
    "seq",
    "ts",
    "type",
    "run_id",
    "command_id",
    "task_id",
    "ac_refs",
    "summary",
    "payload_ref",
}


# -- fixtures: real stores seeded through the documented contract ------------


def _seed_home(tmp_path: Path, run_id: str) -> tuple[Path, Path]:
    """Create the service home plus one registered repo hosting ``run_id``."""
    repo = tmp_path / "repo"
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    with contextlib.closing(sqlite3.connect(str(home / "service.db"))) as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), _VERSION, "alice", _PAST_TS),
        )
        conn.commit()
    return repo, home


def _seed_tracks_events(repo: Path, run_id: str, specs: list[tuple[str, dict]]) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        for event_type, payload in specs:
            store.append(run_id, _VERSION, event_type, payload)
    finally:
        store.close()


def _seed_service_event(
    home: Path, run_id: str, ts: str, event_type: str, payload: dict
) -> None:
    with contextlib.closing(sqlite3.connect(str(home / "service.db"))) as conn:
        conn.execute(
            "INSERT INTO service_events (ts, type, project_id, run_id, command_id, payload)"
            " VALUES (?,?,?,?,?,?)",
            (
                ts,
                event_type,
                "proj-1",
                run_id,
                None,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()


# -- the handler seam: request double + guarded calls ------------------------


def _request(home: Path, *, run_id: str = _RUN, query: dict[str, str] | None = None) -> Any:
    """Duck-typed request exposing exactly the handler seam (see module doc)."""
    state = SimpleNamespace(home=home, redactor=SecretRedactor({}))
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    request.path_params = {"run_id": run_id}
    request.query_params = dict(query or {})
    return request


def _call_poll(request: Any) -> tuple[int, Any]:
    """wait=0 polling call; a stub token becomes a real assertion failure."""
    try:
        result = asyncio.run(api_events.stream_events(request))
    except NotImplementedError as exc:
        raise AssertionError(f"stream_events is still a stub: {exc}") from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), (
        "wait=0 polling must return the status/payload envelope (QueryResponse seam)"
    )
    return status, getattr(result, "payload", None)


def _call_stream(request: Any) -> Any:
    """SSE subscription call; a stub token becomes a real assertion failure."""
    try:
        return asyncio.run(api_events.stream_events(request))
    except NotImplementedError as exc:
        raise AssertionError(f"stream_events is still a stub: {exc}") from None


def _encode(ts: str, source_rank: int, seq: int) -> str:
    try:
        return api_events.encode_cursor(ts, source_rank, seq)
    except NotImplementedError as exc:
        raise AssertionError(f"encode_cursor is still a stub: {exc}") from None


def _decode(cursor: str) -> tuple[str, int, int]:
    try:
        return api_events.decode_cursor(cursor)
    except NotImplementedError as exc:
        raise AssertionError(f"decode_cursor is still a stub: {exc}") from None


async def _next_frame(iterator: Any, what: str) -> str:
    try:
        frame = await asyncio.wait_for(iterator.__anext__(), _FRAME_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise AssertionError(f"SSE frame did not arrive in time: {what}") from None
    except StopAsyncIteration:
        raise AssertionError(f"SSE stream ended before the backfill completed: {what}") from None
    return frame.decode("utf-8") if isinstance(frame, bytes) else frame


async def _drain_backfill(stream: Any, count: int) -> list[str]:
    media_type = getattr(stream, "media_type", None)
    assert media_type == "text/event-stream", (
        f"SSE subscription must stream text/event-stream, got {media_type!r}"
    )
    iterator = getattr(stream, "body_iterator", None)
    assert iterator is not None, "SSE envelope must expose a body_iterator"
    frames = [await _next_frame(iterator, f"frame {index + 1}/{count}") for index in range(count)]
    with contextlib.suppress(Exception):
        await iterator.aclose()
    return frames


def _read_sse_backfill(request: Any, count: int) -> list[str]:
    return asyncio.run(_drain_backfill(_call_stream(request), count))


def _frame_segments(frame: str) -> dict[str, str]:
    """Parse one SSE frame into its id/event/data segments (§2b #28)."""
    segments: dict[str, str] = {}
    for line in frame.splitlines():
        for name in ("id", "event", "data"):
            if line.startswith(f"{name}:"):
                segments[name] = line[len(name) + 1 :].strip()
    return segments


def _rank(source: str) -> int:
    return 0 if source == "tracks" else 1


def _seed_one_event(repo: Path, home: Path) -> str:
    """One tracks event, snapshotted by a first poll; returns its cursor."""
    _seed_tracks_events(repo, _RUN, [("stage.entered", {"stage": "M-IMPL"})])
    status, first = _call_poll(_request(home, query={"wait": "0"}))
    assert status == 200, "the snapshot poll must succeed before the gap is injected"
    assert len(first["events"]) == 1
    return first["cursor"]


# -- cursor mechanics (§1e) ---------------------------------------------------


# AC-FR0306-02@v0.9 TRACKS-TRACE IF-STREAM-001 cursor round-trips the merged order key
def test_cursor_roundtrip_carries_merged_order_key():
    token = _encode(_PAST_TS, 0, 7)
    assert _decode(token) == (_PAST_TS, 0, 7)
    assert "+" not in token and "/" not in token, "cursor must be urlsafe (§1e)"

    tracks_first = _decode(_encode(_PAST_TS, 0, 1))
    service_next = _decode(_encode(_PAST_TS, 1, 1))
    later_seq = _decode(_encode(_PAST_TS, 1, 2))
    assert tracks_first < service_next < later_seq, (
        "cursor ordering must follow (ts, source_rank, seq) with tracks=0 before service=1"
    )


# AC-FR0306-02@v0.9 TRACKS-TRACE IF-STREAM-001 decode fails closed on malformed cursors
def test_decode_cursor_fails_closed_on_garbage():
    for garbage in ("", "not-a-cursor", _PAST_TS):
        raised: BaseException | None = None
        try:
            _decode(garbage)
        except AssertionError:
            raise
        except Exception as exc:  # the fail-closed raise itself is the contract
            raised = exc
        assert raised is not None, (
            f"decode_cursor({garbage!r}) must fail closed, not return a usable position"
        )


# -- wait=0 polling batch (§2b #28, NFR-0152) ---------------------------------


# AC-NFR0152-02@v0.9 TRACKS-TRACE IF-STREAM-001 poll batch serves the merged source
def test_poll_batch_serves_merged_events_and_consistent_cursor(tmp_path: Path):
    repo, home = _seed_home(tmp_path, _RUN)
    _seed_service_event(home, _RUN, _PAST_TS, "command.accepted", {"kind": "create_run"})
    _seed_tracks_events(
        repo,
        _RUN,
        [("stage.entered", {"stage": "M-IMPL"}), ("task.started", {"task_id": "T-1"})],
    )

    status, payload = _call_poll(_request(home, query={"wait": "0"}))

    assert status == 200
    assert set(payload) >= {"events", "cursor"}
    events = payload["events"]
    assert [event["type"] for event in events] == [
        "command.accepted",
        "stage.entered",
        "task.started",
    ], "batch order must follow the merged (ts, source_rank, seq) key"
    assert {event["source"] for event in events} == {"service", "tracks"}
    for event in events:
        assert set(event) >= _EVENT_ROW_KEYS
        assert event["run_id"] == _RUN
    last = events[-1]
    assert _decode(payload["cursor"]) == (last["ts"], _rank(last["source"]), last["seq"])


# AC-FR0306-02@v0.9 TRACKS-TRACE IF-STREAM-001 after=cursor replays exactly the gap, once
def test_poll_batch_backfills_gap_after_cursor_without_duplicates(tmp_path: Path):
    repo, home = _seed_home(tmp_path, _RUN)
    gap_cursor = _seed_one_event(repo, home)

    _seed_tracks_events(repo, _RUN, [("task.started", {"task_id": "T-9"})])
    _seed_service_event(home, _RUN, _FUTURE_TS, "command.completed", {"command_id": "cmd-9"})

    status, replay = _call_poll(_request(home, query={"wait": "0", "after": gap_cursor}))
    assert status == 200
    assert [event["type"] for event in replay["events"]] == [
        "task.started",
        "command.completed",
    ], "backfill must deliver exactly the disconnected gap, both sources, no repeats"
    assert _decode(replay["cursor"]) > _decode(gap_cursor), "cursor must advance"

    status, again = _call_poll(_request(home, query={"wait": "0", "after": replay["cursor"]}))
    assert status == 200
    assert again["events"] == [], "a second poll at the advanced cursor must not repeat"


# AC-FR0306-02@v0.9 TRACKS-TRACE IF-STREAM-001 repeated fetches never regress the cursor
def test_poll_batch_replay_does_not_duplicate_or_regress(tmp_path: Path):
    repo, home = _seed_home(tmp_path, _RUN)
    gap_cursor = _seed_one_event(repo, home)
    _seed_tracks_events(repo, _RUN, [("task.started", {"task_id": "T-9"})])

    status_one, one = _call_poll(_request(home, query={"wait": "0", "after": gap_cursor}))
    status_two, two = _call_poll(_request(home, query={"wait": "0", "after": gap_cursor}))
    assert status_one == status_two == 200
    assert [event["type"] for event in one["events"]] == ["task.started"]
    assert [event["type"] for event in two["events"]] == ["task.started"], (
        "replaying the same request must not double-deliver the gap"
    )
    assert _decode(one["cursor"]) >= _decode(gap_cursor)
    assert _decode(two["cursor"]) == _decode(one["cursor"]), "cursor must not regress"


# AC-NFR0152-02@v0.9 TRACKS-TRACE IF-STREAM-001 idle polls stay silent and capped
def test_poll_idle_returns_empty_batches_with_stable_cursor(tmp_path: Path):
    repo, home = _seed_home(tmp_path, _RUN)
    gap_cursor = _seed_one_event(repo, home)

    for round_index in range(3):
        status, payload = _call_poll(
            _request(home, query={"wait": "0", "after": gap_cursor})
        )
        assert status == 200
        assert payload["events"] == [], f"idle poll {round_index + 1} must carry no load"
        assert _decode(payload["cursor"]) == _decode(gap_cursor), (
            "an idle poll must keep the consumer's position resumable"
        )

    config = projections.project_service_config(home)
    assert config["poll_interval_s"] == 5, "poll fallback cadence ceiling is the locked 5s"
    assert config["poll_idle_cap_s"] == 60, "idle cap is the locked 60s (NFR-0152)"


# -- SSE subscription (§2b #28, §4a #5) ---------------------------------------


# AC-FR0306-01@v0.9 TRACKS-TRACE IF-STREAM-001 SSE frames carry id/event/data backfill
# AC-FR0306-02@v0.9 TRACKS-TRACE IF-STREAM-001 stream and poll batches share one source
def test_sse_backfill_frames_and_poll_batch_share_source(tmp_path: Path):
    repo, home = _seed_home(tmp_path, _RUN)
    gap_cursor = _seed_one_event(repo, home)
    _seed_tracks_events(repo, _RUN, [("task.started", {"task_id": "T-9"})])
    _seed_service_event(home, _RUN, _FUTURE_TS, "command.completed", {"command_id": "cmd-9"})

    frames = _read_sse_backfill(_request(home, query={"after": gap_cursor}), 2)

    parsed = [_frame_segments(frame) for frame in frames]
    for segments in parsed:
        assert set(segments) == {"id", "event", "data"}, (
            "every SSE frame must carry the three id/event/data segments"
        )
    assert [segments["event"] for segments in parsed] == [
        "task.started",
        "command.completed",
    ], "backfill frames follow the merged (ts, source_rank, seq) order"
    base = _decode(gap_cursor)
    positions = [_decode(segments["id"]) for segments in parsed]
    assert all(position > base for position in positions), "frame ids resume after the cursor"
    assert positions == sorted(positions), "frame ids are monotonic (no regression)"
    for segments in parsed:
        assert isinstance(json.loads(segments["data"]), dict), "data must be a JSON object"

    status, batch = _call_poll(_request(home, query={"wait": "0", "after": gap_cursor}))
    assert status == 200
    assert [event["type"] for event in batch["events"]] == [
        segments["event"] for segments in parsed
    ], "polling fallback and SSE backfill must share one event source"


# -- error surface (§2b #28) ---------------------------------------------------


# AC-FR0306-01@v0.9 TRACKS-TRACE IF-STREAM-001 unknown run is 404 in both modes
def test_events_unknown_run_is_not_found(tmp_path: Path):
    _, home = _seed_home(tmp_path, _RUN)

    status, payload = _call_poll(_request(home, run_id="ghost", query={"wait": "0"}))
    assert status == 404
    assert payload["error"]["reason"] == "not_found"

    stream = _call_stream(_request(home, run_id="ghost"))
    assert getattr(stream, "status_code", None) == 404, (
        "the SSE subscription must reject unknown runs before streaming"
    )
    assert stream.payload["error"]["reason"] == "not_found"
