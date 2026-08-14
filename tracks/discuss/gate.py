"""Discussion gate (FR-100, AC-1001..1003).

``check_ready`` answers whether a document's inline discussion is settled: ready
iff every thread is resolved (open and reopen both block). Pure read; backs
``trac discuss query --check-ready`` and the ``discussion_ready`` validate check.
"""

from __future__ import annotations

from tracks.discuss.parser import parse_threads


def check_ready(text: str) -> tuple:
    """Return ``(is_ready, ready_blockers)``; blockers are non-resolved thread_ids."""
    blockers = tuple(t.thread_id for t in parse_threads(text) if t.status != "resolved")
    return not blockers, blockers
