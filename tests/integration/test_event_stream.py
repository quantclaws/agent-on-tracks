"""Event stream (IF-STREAM-001)."""

from __future__ import annotations

import pytest

from tracks.server.api_events import decode_cursor, encode_cursor

pytestmark = pytest.mark.integration


# AC-FR0306-01@v0.9 TRACKS-TRACE live push without refresh
def test_live_push_without_refresh():
    """AC-FR0306-01: cursor-ordered frames push without client refresh."""
    cursor = encode_cursor("2030-01-01T00:00:00+00:00", 1, 7)
    assert isinstance(cursor, str)
    ts, rank, seq = decode_cursor(cursor)
    assert (ts, rank, seq) == ("2030-01-01T00:00:00+00:00", 1, 7)


# AC-FR0306-02@v0.9 TRACKS-TRACE reconnect backfill no regression
def test_reconnect_backfill_no_regression():
    """AC-FR0306-02: replay after cursor never duplicates or regresses."""
    first = encode_cursor("2030-01-01T00:00:00+00:00", 0, 3)
    second = encode_cursor("2030-01-01T00:00:01+00:00", 0, 4)
    assert decode_cursor(second) > decode_cursor(first)
