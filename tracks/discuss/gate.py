"""Discussion gate (FR-100, AC-1001..1003).

``check_ready`` answers whether a document's inline discussion is settled: ready
iff every thread is resolved (open and reopen both block). Pure read; backs
``trac discuss query --check-ready`` and the ``discussion_ready`` validate check.

``adjudication_owner`` implements the FR-0314.4 ownership determination: a
thread whose root comment explicitly asks a specific party (@Human or a named
reviewer) to adjudicate is resolved only by that party. Both the discuss CLI
(``set-status resolved``) and the service-side web entry reuse it (lazy import).
"""

from __future__ import annotations

from tracks.discuss.parser import parse_threads


def check_ready(text: str) -> tuple:
    """Return ``(is_ready, ready_blockers)``; blockers are non-resolved thread_ids."""
    blockers = tuple(t.thread_id for t in parse_threads(text) if t.status != "resolved")
    return not blockers, blockers


def adjudication_owner(text: str, thread_id: str) -> str | None:
    """The party the thread's root explicitly asks to adjudicate, or None.

    Only the root comment's @mentions create an adjudication request
    (interfaces §1f.4); reply-level mentions never do. With zero or several
    root mentions there is no single bounded party, so the existing operator
    rules apply unchanged.
    """
    thread = next((t for t in parse_threads(text) if t.thread_id == thread_id), None)
    if thread is None:
        return None
    mentions = tuple(dict.fromkeys(thread.root.mentions))
    if len(mentions) == 1:
        return mentions[0]
    return None
