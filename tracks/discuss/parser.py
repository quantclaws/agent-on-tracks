"""Parse inline-discussion threads from markdown (FR-050/FR-060/FR-120).

Line-based and pure: reads only the given text. Identity is rebuilt on every
scan (no persistence); ``T-NNN`` is a per-scan label. Fenced code blocks are
skipped (AC-1201); a root comment's anchor is the nearest non-empty,
non-blockquote line above it (AC-1203).
"""
from __future__ import annotations

import re

from tracks.discuss.model import SNIPPET_LEN, Thread, normalize

_LABELS = frozenset({
    "note", "warning", "tip", "important", "definition", "example",
    "remark", "attention", "caution",
})
_STATUSES = frozenset({"open", "resolved", "reopen"})

_FENCE = re.compile(r"^\s*(```|~~~)")
_BQ = re.compile(r"^\s*(>+)\s*(.*)$")
# A: colon inside bold  ->  **Name [STATUS]:** body   /   **@Name:** body
_TAG_IN = re.compile(
    r"^\*\*(?P<name>@?[^*\[]+?)\s*(?:\[(?P<st>[A-Za-z]+)\])?\s*:\*\*\s?(?P<body>.*)$")
# B: colon outside bold ->  **Name** [STATUS]: body   /   **Name**: body
_TAG_OUT = re.compile(
    r"^\*\*(?P<name>@?[^*\[]+?)\*\*\s*(?:\[(?P<st>[A-Za-z]+)\])?\s*:\s?(?P<body>.*)$")
# C: no-bold ASCII id   ->  Name: body
_TAG_PLAIN = re.compile(r"^(?P<name>@?[A-Za-z_][A-Za-z0-9_-]*)\s*:\s?(?P<body>.*)$")
_MENTION = re.compile(r"@([A-Za-z_][A-Za-z0-9_-]*)")


def parse_tag(content: str):
    """Parse a speaker tag at the start of blockquote content (FR-050).

    Returns ``(speaker, status, body)`` — speaker keeps display case with any
    leading ``@`` stripped; status is ``open``/``resolved``/``reopen`` or None
    (meaningful only on roots); body stripped. Returns None for label tags,
    bold-without-colon, and plain blockquotes (not a comment, AC-0504).
    """
    for rx in (_TAG_IN, _TAG_OUT, _TAG_PLAIN):
        m = rx.match(content)
        if not m:
            continue
        name = m.group("name").strip()
        if name.lstrip("@").lower() in _LABELS:
            return None
        st = m.groupdict().get("st")
        status = st.lower() if st and st.lower() in _STATUSES else None
        return name.lstrip("@"), status, m.group("body").strip()
    return None


def _mentions(text: str) -> list:
    return _MENTION.findall(text)


class _Accumulator:
    """Folds document lines into threads; one full scan, no persistence."""

    def __init__(self, total: int):
        self.total = total
        self.threads: list = []
        self._anchor_text = ""
        self._anchor_line = 0
        self._root = None
        self._reply_count = 0
        self._last = ""
        self._mentioned: list = []

    def feed(self, idx: int, raw: str) -> None:
        m = _BQ.match(raw)
        if m is None:
            self._on_non_bq(idx, raw)
            return
        tag = parse_tag(m.group(2))
        if tag is None:
            return  # blockquote but not a comment (label / plain); ignore
        name, status, body = tag
        if len(m.group(1)) == 1:
            self.flush()
            self._start_root(idx, raw.rstrip(), name, status, body)
        else:
            self._add_reply(idx, raw.rstrip(), name, body)

    def _on_non_bq(self, idx: int, raw: str) -> None:
        if not raw.strip():
            return  # blank line: keeps the thread open, is not an anchor
        self.flush()  # plain content ends the current thread
        self._anchor_text = raw.strip()
        self._anchor_line = idx

    def _start_root(self, idx, root_text, name, status, body) -> None:
        self._root = {
            "name": name, "status": status or "open", "body": body,
            "root_line": idx, "root_text": root_text,
            "anchor_line": self._anchor_line, "anchor_text": self._anchor_text,
        }
        self._reply_count = 0
        self._last = name
        self._mentioned = list(_mentions(body))

    def _add_reply(self, idx, root_text, name, body) -> None:
        if self._root is None:  # orphan reply: promote to a root (best-effort)
            self._start_root(idx, root_text, name, None, body)
            return
        self._reply_count += 1
        self._last = name
        self._mentioned.extend(_mentions(body))

    def flush(self) -> None:
        if self._root is None:
            return
        seq = len(self.threads) + 1
        self.threads.append(Thread(
            thread_id=f"T-{seq:03d}",
            initiator=self._root["name"],
            status=self._root["status"],
            last_speaker=self._last,
            reply_count=self._reply_count,
            snippet=normalize(self._root["body"])[:SNIPPET_LEN],
            mentioned_agents=tuple(dict.fromkeys(self._mentioned)),
            total_lines=self.total,
            anchor_line=self._root["anchor_line"],
            anchor_text=self._root["anchor_text"],
            root_line=self._root["root_line"],
            root_text=self._root["root_text"],
        ))
        self._root = None


def parse_threads(text: str) -> list:
    """Full-scan parse of all discussion threads in ``text`` (FR-060/FR-120)."""
    lines = text.splitlines()
    acc = _Accumulator(len(lines))
    in_fence = False
    for idx, raw in enumerate(lines, start=1):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if not in_fence:
            acc.feed(idx, raw)
    acc.flush()
    return acc.threads
