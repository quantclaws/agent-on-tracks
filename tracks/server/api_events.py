"""Server-sent events stream with cursor catch-up (IF-STREAM-001).

``GET /api/runs/{run_id}/events?after=<cursor>`` streams merged
tracks.db + service_events frames (``id``/``event``/``data``); on reconnect
the server replays events strictly after the cursor, then pushes live.
Snapshots and the stream share the same event source, so a reconnecting
client never regresses (FR-0306). With ``wait=0`` the endpoint returns one
JSON batch instead of a stream (polling fallback, NFR-0152 caps).

Contract token: IF-STREAM-001.
"""

from __future__ import annotations

from typing import Any


def encode_cursor(ts: str, source_rank: int, seq: int) -> str:
    """Opaque cursor over the merged (ts, source_rank, seq) ordering (§1e)."""
    raise NotImplementedError("IF-STREAM-001")


def decode_cursor(cursor: str) -> tuple[str, int, int]:
    """Parse a cursor produced by :func:`encode_cursor`; fail closed."""
    raise NotImplementedError("IF-STREAM-001")


async def stream_events(request: Any) -> Any:
    """GET /api/runs/{run_id}/events (SSE or one-shot JSON with wait=0)."""
    raise NotImplementedError("IF-STREAM-001")
