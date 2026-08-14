"""Parse inline-discussion threads from markdown (FR-050/FR-060/FR-120).

Line-based and pure: reads only the given text. Identity is rebuilt on every
scan (no persistence); ``T-NNN`` is a per-scan label. Fenced code blocks are
skipped (AC-1201); a root comment's anchor is the nearest non-empty,
non-blockquote line above it (AC-1203).
"""

from __future__ import annotations

import re

from tracks.discuss.model import SNIPPET_LEN, Comment, Thread, normalize

_LABELS = frozenset(
    {
        "note",
        "warning",
        "tip",
        "important",
        "definition",
        "example",
        "remark",
        "attention",
        "caution",
    }
)
_STATUSES = frozenset({"open", "resolved", "reopen"})

_FENCE = re.compile(r"^\s*(```|~~~)")
_BQ = re.compile(r"^\s*(>+)\s*(.*)$")
# A: colon inside bold  ->  **Name [STATUS]:** body   /   **@Name:** body
_TAG_IN = re.compile(
    r"^\*\*(?P<name>@?[^*\[]+?)\s*(?:\[(?P<st>[A-Za-z]+)\])?\s*:\*\*\s?(?P<body>.*)$"
)
# B: colon outside bold ->  **Name** [STATUS]: body   /   **Name**: body
_TAG_OUT = re.compile(
    r"^\*\*(?P<name>@?[^*\[]+?)\*\*\s*(?:\[(?P<st>[A-Za-z]+)\])?\s*:\s?(?P<body>.*)$"
)
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


class _CommentBuilder:
    """Mutable comment node while scanning; ``freeze`` -> immutable Comment."""

    __slots__ = ("depth", "speaker", "body", "line", "text", "mentions", "children")

    def __init__(self, depth, speaker, body, line, text, mentions):
        self.depth = depth
        self.speaker = speaker
        self.body = body
        self.line = line
        self.text = text
        self.mentions = list(mentions)
        self.children: list = []

    def freeze(self) -> Comment:
        return Comment(
            depth=self.depth,
            speaker=self.speaker,
            body=self.body,
            line=self.line,
            text=self.text,
            mentions=tuple(self.mentions),
            children=tuple(c.freeze() for c in self.children),
        )


class _Accumulator:
    """Folds document lines into threads; one full scan, no persistence.

    Builds each thread's reply tree: a depth-N comment attaches as a child of
    the nearest preceding comment with depth < N (FR-050 nesting).
    """

    def __init__(self, total: int):
        self.total = total
        self.threads: list = []
        self._anchor_text = ""
        self._anchor_line = 0
        self._root = None  # _CommentBuilder root of the current thread
        self._root_status = "open"
        self._root_anchor = (0, "")
        self._stack: list = []  # ancestor chain root..most-recent comment
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
        self._add_comment(idx, raw.rstrip(), len(m.group(1)), name, status, body)

    def _on_non_bq(self, idx: int, raw: str) -> None:
        if not raw.strip():
            return  # blank line: keeps the thread open, is not an anchor
        self.flush()  # plain content ends the current thread
        self._anchor_text = raw.strip()
        self._anchor_line = idx

    def _add_comment(self, idx, text, depth, name, status, body) -> None:
        node = _CommentBuilder(depth, name, body, idx, text, _mentions(body))
        if depth == 1:
            self.flush()
            self._root = node
            self._root_status = status or "open"
            self._root_anchor = (self._anchor_line, self._anchor_text)
            self._stack = [node]
            self._reply_count = 0
            self._last = name
            self._mentioned = list(node.mentions)
            return
        if self._root is None:  # orphan reply: promote to a root (best-effort)
            self._add_comment(idx, text, 1, name, status, body)
            return
        while len(self._stack) > 1 and self._stack[-1].depth >= depth:
            self._stack.pop()
        self._stack[-1].children.append(node)  # nearest shallower ancestor
        self._stack.append(node)
        self._reply_count += 1
        self._last = name
        self._mentioned.extend(node.mentions)

    def flush(self) -> None:
        if self._root is None:
            return
        seq = len(self.threads) + 1
        anchor_line, anchor_text = self._root_anchor
        self.threads.append(
            Thread(
                thread_id=f"T-{seq:03d}",
                initiator=self._root.speaker,
                status=self._root_status,
                last_speaker=self._last,
                reply_count=self._reply_count,
                snippet=normalize(self._root.body)[:SNIPPET_LEN],
                mentioned_agents=tuple(dict.fromkeys(self._mentioned)),
                root=self._root.freeze(),
                total_lines=self.total,
                anchor_line=anchor_line,
                anchor_text=anchor_text,
                root_line=self._root.line,
                root_text=self._root.text,
            )
        )
        self._root = None
        self._stack = []


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
