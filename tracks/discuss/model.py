"""inline-discussion bypass types (IF-003 §6, FR-060).

Pure data + normalization. Threads are an immutable view of one full scan of a
document; nothing here touches the filesystem or the event store.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WS = re.compile(r"\s+")

SNIPPET_LEN = 80


def normalize(text: str) -> str:
    """FR-060 / AC-0603: strip + collapse runs of whitespace to one space +
    Unicode NFC. Does NOT change case or strip markdown formatting."""
    return _WS.sub(" ", unicodedata.normalize("NFC", text).strip())


def speaker_key(name: str) -> str:
    """Speaker comparison key: normalized + lowercased (display keeps case)."""
    return normalize(name).lower()


@dataclass(frozen=True)
class Comment:
    """One comment in a thread's reply tree (FR-050 nesting).

    ``depth`` = number of '>' (1 = root). A depth-N comment replies to the
    nearest preceding depth-(N-1) comment; ``children`` are its depth+1 replies
    (nested). ``depth`` thus encodes 'reply to whom'; ``mentions`` is the
    orthogonal '@request someone to answer' semantic (FR-050).
    """

    depth: int
    speaker: str          # display case ('@' stripped)
    body: str
    line: int             # 1-indexed first line of the comment
    text: str             # raw first line (rstripped)
    mentions: tuple       # @mentions in this comment's body
    children: tuple       # tuple[Comment] — nested replies


def iter_comments(comment: Comment):
    """Yield ``comment`` then all descendants in scan (pre) order."""
    yield comment
    for child in comment.children:
        yield from iter_comments(child)


@dataclass(frozen=True)
class Thread:
    """One discussion thread as seen in a single full scan (FR-060)."""

    thread_id: str            # "T-NNN", per-scan sequence (NOT persistent)
    initiator: str            # root comment speaker (display case preserved)
    status: str               # "open" | "resolved" | "reopen"
    last_speaker: str
    reply_count: int
    snippet: str              # root body, first SNIPPET_LEN chars
    mentioned_agents: tuple   # deduped @mentions (display case)
    root: Comment             # the reply tree (root comment + nested children)
    # 5-tuple locate hints (L0/L1); content is the authority, not these numbers.
    total_lines: int
    anchor_line: int
    anchor_text: str
    root_line: int
    root_text: str


@dataclass(frozen=True)
class DiscussQuery:
    """Result of `trac discuss query` (IF-003 §6)."""

    threads: tuple
    is_ready: bool | None = None              # only with --check-ready
    ready_blockers: tuple | None = None
    unanswered: tuple | None = None           # only with --blocker
    unresolved: tuple | None = None
    awaiting_my_reply: tuple | None = None


@dataclass(frozen=True)
class LocateResult:
    """Result of relocating a thread for a write command (FR-070, IF-003 §6).

    status:
      unique    -> exactly one confident match; thread_id set; write may proceed
      ambiguous -> tied / low-confidence candidates; candidates (line numbers)
      not_found -> L3 failed; nothing matched
      stale     -> token relocated a thread whose current thread_id differs from
                   the requested one (reorder drift); re-query required
    Writes happen ONLY when status == 'unique' (fail closed, FR-070).
    """

    status: str
    thread_id: str | None = None
    candidates: tuple | None = None
