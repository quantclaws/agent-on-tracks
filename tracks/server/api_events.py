"""Server-sent events stream with cursor catch-up (IF-STREAM-001).

``GET /api/runs/{run_id}/events?after=<cursor>`` streams merged
tracks.db + service_events frames (``id``/``event``/``data``); on reconnect
the server replays events strictly after the cursor, then keeps pushing
live. Snapshots and the stream share one event source (the §1e merged
timeline projection), so a reconnecting client never regresses (FR-0306).
With ``wait=0`` the endpoint returns one JSON batch instead of a stream
(polling fallback; the NFR-0152 5s/60s cadence caps are consumer-side
against this one-shot surface, and idle polls return empty stable batches).

Unit seam (bound by the T-014 app factory, T-011 convention): the handler
reads ``request.app.state.home`` / ``request.app.state.redactor``,
``request.path_params["run_id"]`` and ``request.query_params`` and returns
one starlette-free :class:`EventResponse`:

- ``wait=0`` -> ``status_code`` + JSON ``payload`` ``{events, cursor}``
  (mirrors ``api_query.QueryResponse``; errors use the unified envelope);
- SSE -> ``media_type="text/event-stream"`` + ``body_iterator`` of frames
  (mirrors ``StreamingResponse``; each frame carries the ``id:``/``event:``/
  ``data:`` segments of §2b #28).

Cursors are the opaque urlsafe encoding of the §1e ordering key
``(ts, source_rank, seq)`` (tracks=0, service=1). The snapshot spelling of
the key (``source`` string) decodes to the same position, so the
``event_cursor`` of ``GET /api/runs/{run_id}`` and stream/poll cursors are
interchangeable — reconnect resumes without gap or overlap. Live push
re-polls the same merged source per tick: real-time and backfill share one
data path, with no in-memory stream state (§1e).

Contract token: IF-STREAM-001.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracks.server import projections

_LIVE_TICK_S = 5.0  # merged-source re-poll cadence; poll_interval_s default (NFR-0152)


@dataclass(frozen=True)
class EventResponse:
    """Starlette-free envelope of the subscription route (see module doc)."""

    status_code: int
    payload: Any = None
    media_type: str | None = None
    body_iterator: Any = None


def encode_cursor(ts: str, source_rank: int, seq: int) -> str:
    """Opaque cursor over the merged (ts, source_rank, seq) ordering (§1e)."""
    raw = json.dumps({"ts": ts, "source_rank": int(source_rank), "seq": int(seq)}, sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, int, int]:
    """Parse a cursor produced by :func:`encode_cursor`; fail closed.

    Also accepts the snapshot spelling (``source`` string) of the same
    ordering key so snapshot and stream cursors are interchangeable.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        if "source_rank" in data:
            rank = int(data["source_rank"])
        else:
            rank = _rank(str(data["source"]))
        return str(data["ts"]), rank, int(data["seq"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"malformed event cursor: {cursor!r}") from exc


async def stream_events(request: Any) -> EventResponse:
    """GET /api/runs/{run_id}/events (SSE, or one-shot JSON with wait=0)."""
    home = Path(request.app.state.home)
    run_id = str(request.path_params.get("run_id", ""))
    if projections.project_run_detail(home, run_id) is None:
        return EventResponse(
            404,
            {"error": {"reason": "not_found", "detail": f"run {run_id!r} is not registered"}},
        )
    after = _param(request.query_params, "after")
    redactor = getattr(request.app.state, "redactor", None)
    if _param(request.query_params, "wait") == "0":
        return _poll_batch(home, run_id, redactor, after)
    return EventResponse(
        200,
        media_type="text/event-stream",
        body_iterator=_stream_frames(home, run_id, redactor, after),
    )


def _param(source: Any, name: str) -> str | None:
    value = source.get(name)
    return value if isinstance(value, str) and value else None


def _rank(source: str) -> int:
    return 0 if source == "tracks" else 1


def _row_key(row: dict) -> tuple[str, int, int]:
    return (str(row["ts"]), _rank(str(row["source"])), int(row["seq"]))


def _start_key(cursor: str | None) -> tuple[str, int, int] | None:
    if not cursor:
        return None
    try:
        return decode_cursor(cursor)
    except ValueError:
        return None  # lenient like the timeline projection: ignore malformed cursors


def _rows_after(home: Path, run_id: str, start: tuple[str, int, int] | None) -> list[dict]:
    rows = projections.project_timeline(home, run_id)["events"]
    if start is not None:
        rows = [row for row in rows if _row_key(row) > start]
    return rows


def _redacted(row: dict, redactor: Any) -> dict:
    if redactor is None:
        return row
    return redactor.redact_payload(row)


def _frame(row: dict, redactor: Any) -> str:
    token = encode_cursor(*_row_key(row))
    data = json.dumps(_redacted(row, redactor), ensure_ascii=False, sort_keys=True)
    return f"id: {token}\nevent: {row['type']}\ndata: {data}\n\n"


def _poll_batch(home: Path, run_id: str, redactor: Any, after: str | None) -> EventResponse:
    """One-shot polling batch (§2b #28 wait=0); an idle poll keeps the
    consumer's position resumable by echoing the effective cursor."""
    rows = _rows_after(home, run_id, _start_key(after))
    if rows:
        cursor = encode_cursor(*_row_key(rows[-1]))
    else:
        cursor = after or ""
    return EventResponse(
        200,
        {"events": [_redacted(row, redactor) for row in rows], "cursor": cursor},
    )


async def _stream_frames(
    home: Path, run_id: str, redactor: Any, after: str | None
) -> AsyncIterator[str]:
    """Backfill strictly after the cursor, then hold the merged-source tick."""
    start = _start_key(after)
    while True:
        for row in _rows_after(home, run_id, start):
            start = _row_key(row)
            yield _frame(row, redactor)
        await asyncio.sleep(_LIVE_TICK_S)
