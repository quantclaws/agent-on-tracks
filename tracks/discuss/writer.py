"""inline-discussion write operations (FR-090/FR-110, AC-0505).

Pure text transforms: each takes the document text and returns the rewritten
text (the CLI layer adds flock + tmp/rename, FR-110). reply / edit / set-status
first relocate the thread by content token and proceed ONLY on a unique match
(fail closed -> LocateFailure). Output is canonical: ``> **Speaker [STATUS]:**
body`` (status marker shown only for resolved/reopen; open is the unmarked
default, matching the corpus convention).
"""

from __future__ import annotations

import re

from tracks.discuss.locate import locate, locate_comment
from tracks.discuss.model import LocateResult, speaker_key
from tracks.discuss.parser import parse_tag, parse_threads

_BQ = re.compile(r"^\s*(>+)\s*(.*)$")


class DiscussError(Exception):
    """Base for discuss write failures (CLI -> stderr + exit 1)."""


class LocateFailure(DiscussError):
    def __init__(self, result):
        super().__init__(result.status)
        self.result = result


class WriteError(DiscussError):
    """A write was refused for a non-locate reason (author / status rule)."""


def format_root(speaker: str, status: str, body: str) -> list:
    marker = f" [{status.upper()}]" if status in ("resolved", "reopen") else ""
    parts = body.splitlines() or [""]
    return [f"> **{speaker}{marker}:** {parts[0]}"] + [f"> {ln}" for ln in parts[1:]]


def _format_reply(speaker: str, body: str, depth: int = 2) -> list:
    prefix = ">" * depth
    parts = body.splitlines() or [""]
    return [f"{prefix} **{speaker}:** {parts[0]}"] + [f"{prefix} {ln}" for ln in parts[1:]]


def _nl(text: str) -> str:
    return "\n" if text.endswith("\n") else ""


def _anchor_insert_index(lines: list, anchor_line: int) -> int:
    """0-indexed insert position: just after the first blank line that ends the
    anchor paragraph (FR-110 start position)."""
    i = anchor_line - 1
    n = len(lines)
    while i < n and lines[i].strip():
        i += 1
    return i + 1 if i < n else n


def _splice(lines: list, idx: int, block: list) -> list:
    """Insert block at idx with blank-line separation on both sides."""
    out = list(lines)
    if idx > 0 and out[idx - 1].strip():
        out.insert(idx, "")
        idx += 1
    out[idx:idx] = block
    end = idx + len(block)
    if end < len(out) and out[end].strip():
        out.insert(end, "")
    return out


def start(text: str, anchor_line: int, speaker: str, message: str) -> str:
    """Insert a new open root thread after the anchor paragraph (FR-110)."""
    lines = text.splitlines()
    idx = _anchor_insert_index(lines, anchor_line)
    block = format_root(speaker, "open", message)
    return "\n".join(_splice(lines, idx, block)) + _nl(text)


def _thread(text: str, thread_id: str, token: dict):
    """Locate (fail closed) and return the matching Thread object."""
    result = locate(text, token, thread_id)
    if result.status != "unique":
        raise LocateFailure(result)
    return next(t for t in parse_threads(text) if t.thread_id == thread_id)


def _subtree_end_index(lines: list, comment) -> int:
    """0-indexed insert position just after ``comment``'s subtree block.

    The subtree spans the comment + its descendants (depth > comment.depth) plus
    body-continuation lines; it ends before a sibling/uncle comment (a speaker-
    tagged blockquote at depth <= comment.depth) or non-blockquote content.
    """
    i = comment.line  # 0-indexed line after the 1-indexed comment
    n = len(lines)
    last = comment.line - 1
    while i < n:
        m = _BQ.match(lines[i])
        if m is None:
            if lines[i].strip():
                break
            i += 1
            continue
        if parse_tag(m.group(2)) is not None and len(m.group(1)) <= comment.depth:
            break  # a sibling / uncle comment ends this subtree
        last = i  # descendant comment or body-continuation line
        i += 1
    return last + 1


def reply(
    text: str, thread_id: str, token: dict, speaker: str, message: str, reply_to: dict | None = None
) -> str:
    """Append a reply (FR-050 nesting, FR-110).

    ``reply_to=None`` replies to the root (depth 2, at the thread end);
    ``reply_to=<comment token>`` replies to that comment (depth+1, inserted
    after its subtree). Fail closed: a non-unique thread OR comment locate
    raises LocateFailure and nothing is written.
    """
    thread = _thread(text, thread_id, token)
    lines = text.splitlines()
    if reply_to is None:
        target = thread.root
    else:
        status, payload = locate_comment(thread, reply_to)
        if status != "unique":
            raise LocateFailure(
                LocateResult(status, candidates=payload if status == "ambiguous" else None)
            )
        target = payload
    at = _subtree_end_index(lines, target)
    block = _format_reply(speaker, message, target.depth + 1)
    out = list(lines)
    out[at:at] = block
    nxt = at + len(block)
    if nxt < len(out) and _BQ.match(out[nxt]):  # blank line before next blockquote
        out.insert(nxt, "")
    return "\n".join(out) + _nl(text)


def _find_comment(lines: list, thread, depth: int, speaker: str):
    """0-indexed line of the depth/speaker comment in the thread, or None."""
    if depth == 1:
        if speaker_key(thread.initiator) == speaker_key(speaker):
            return thread.root_line - 1
        return None
    i = thread.root_line
    n = len(lines)
    while i < n:
        m = _BQ.match(lines[i])
        if not m:
            if lines[i].strip():
                break
            i += 1
            continue
        if len(m.group(1)) == depth:
            tag = parse_tag(m.group(2))
            if tag and speaker_key(tag[0]) == speaker_key(speaker):
                return i
        i += 1
    return None


def edit(text: str, thread_id: str, token: dict, depth: int, speaker: str, new_body: str) -> str:
    """Replace the depth/speaker comment's body (FR-110); author only (AC-1103).

    The ``--speaker`` must be the comment's original author (format-consistency
    rule, FR-090); a non-author edit is refused.
    """
    thread = _thread(text, thread_id, token)
    lines = text.splitlines()
    idx = _find_comment(lines, thread, depth, speaker)
    if idx is None:
        raise WriteError(f"no comment at depth {depth} by {speaker!r} (or not author)")
    if depth == 1:
        lines[idx : idx + 1] = format_root(thread.initiator, thread.status, new_body)
    else:
        lines[idx : idx + 1] = _format_reply(speaker, new_body, depth)
    return "\n".join(lines) + _nl(text)


def set_status(text: str, thread_id: str, token: dict, status: str, operator: str) -> str:
    """Change a root thread's status (FR-090 format-consistency rules).

    resolved -> operator must equal the initiator; reopen -> anyone.
    """
    if status not in ("resolved", "reopen"):
        raise WriteError(f"invalid status {status!r} (use resolved|reopen)")
    thread = _thread(text, thread_id, token)
    if status == "resolved" and speaker_key(operator) != speaker_key(thread.initiator):
        raise WriteError("resolved requires operator == initiator (FR-090)")
    lines = text.splitlines()
    idx = thread.root_line - 1
    tag = parse_tag(_BQ.match(lines[idx]).group(2))
    lines[idx] = format_root(thread.initiator, status, tag[2])[0]
    return "\n".join(lines) + _nl(text)
