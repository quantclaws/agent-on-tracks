"""Canonical discussion-delta detection (FR-0120 / inline-discussion protocol).

Pure function: given the base text and the current text of a document, decide
whether the delta is *only* canonical discussion changes (blockquote threads
parseable by the discuss parser) plus the blank-line framing separators that
the writer inserts around them. Used by:

- ``tracks.executor.validate.is_discussion_diff`` (loads base from git HEAD)
- ``tracks.effects.opencode._shield_write_audit`` (loads base from the
  pre-dispatch byte snapshot so a pre-dirty body + canonical reply passes)

Structural validation:

1. Decode both versions as strict UTF-8; invalid UTF-8 -> reject.
2. ``base_bytes == current_bytes`` -> reject (no-op is not a discussion delta).
   CRLF/LF line-ending style is then normalized to LF in both versions (only
   ``\\r\\n`` -> ``\\n``; bare CR is untouched) so a pure newline-style change
   does not produce a discussion finding.
3. ``parse_threads(current)`` must be non-empty (at least one thread).
4. Remove only parser-recognized discussion comment lines from each version.
   Align the two body line-lists with ``difflib.SequenceMatcher``.  Every
   insert / delete / replace region must contain only truly empty framing
   lines (line content is empty after stripping the line ending; space/tab
   whitespace-only lines are NOT framing) that were part of a blank-line run
   immediately adjacent to a discussion comment line in their original
   version.  This allows the writer's canonical framing separators (the truly
   empty ``""`` lines ``_splice`` inserts) while rejecting any mutation of
   body text, body whitespace, trailing spaces, or a blank line turning into
   a space/tab whitespace-only line.
"""
from __future__ import annotations

import difflib
import re

from tracks.discuss.model import iter_comments
from tracks.discuss.parser import parse_threads

_BQ_RE = re.compile(r"^\s*(>+)\s*(.*)$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _discussion_line_numbers(text: str) -> set[int]:
    """1-indexed line numbers of all lines belonging to canonical discussion
    thread blocks: speaker-tagged comments AND their blockquote continuation
    lines.

    Only canonical ``> **Name:**`` tags start a thread; raw blockquotes,
    ``> note:`` labels, and fenced-code ``>`` are NOT thread starters.  However,
    once a thread is started, all subsequent blockquote lines in the same
    blockquote run (continuation lines produced by ``format_root`` /
    ``_format_reply``) are part of the thread block and must be treated as
    discussion lines — otherwise a canonical multi-line comment is incorrectly
    flagged as a body-text change.

    A thread block ends at a non-blockquote, non-blank line (or EOF).  Lines
    inside fenced code blocks are excluded.
    """
    # Identify speaker-tagged comment lines from the parser.
    tagged: set[int] = set()
    for thread in parse_threads(text):
        for comment in iter_comments(thread.root):
            tagged.add(comment.line)

    if not tagged:
        return set()

    # Walk through the text and mark all blockquote lines that are part of
    # a discussion thread block (after the first tagged comment in a run).
    lines = text.splitlines(keepends=True)
    result: set[int] = set()
    in_thread = False
    in_fence = False

    for idx, raw in enumerate(lines, start=1):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            in_thread = False  # fence is non-blockquote content, ends thread
            continue
        if in_fence:
            continue

        if _BQ_RE.match(raw) is not None:
            if idx in tagged:
                result.add(idx)
                in_thread = True
            elif in_thread:
                result.add(idx)
            # else: blockquote before any tagged comment in this run — skip
        else:
            if raw.rstrip("\r\n").strip():
                in_thread = False
            # blank lines don't end the thread block

    return result


def _is_framing(line: str) -> bool:
    """True when *line* (with its line ending) is a truly empty framing
    separator: its content is empty after removing the line ending.  A
    space/tab whitespace-only line is NOT framing - it is body whitespace
    that must not be hidden or manufactured as a discussion finding."""
    return line.rstrip("\r\n") == ""


def _framing_blank_numbers(
    lines: list[str], discussion_lines: set[int],
) -> set[int]:
    """1-indexed numbers of truly-empty framing lines in a run immediately
    adjacent to a discussion comment line.

    For each discussion comment, walks upward and downward through consecutive
    truly-empty lines (space/tab whitespace-only lines are NOT framing and
    stop the walk).  A run of truly-empty lines touching a discussion comment
    on either side is entirely framing – this matches the writer's ``_splice``
    behaviour of inserting separator blanks and also handles non-canonical
    agents that emit extra blank lines before or after a thread."""
    framing: set[int] = set()
    n = len(lines)
    for dl in discussion_lines:
        up = dl - 1
        while up >= 1 and _is_framing(lines[up - 1]):
            framing.add(up)
            up -= 1
        down = dl + 1
        while down <= n and _is_framing(lines[down - 1]):
            framing.add(down)
            down += 1
    return framing


def _body_lines(
    lines: list[str], discussion_lines: set[int],
) -> tuple[list[str], list[int]]:
    """Non-discussion lines and their 1-indexed original line numbers.

    Discussion comment lines are removed; every remaining line keeps its
    original position so framing-blank membership can be checked."""
    body: list[str] = []
    orig: list[int] = []
    for i, raw in enumerate(lines, start=1):
        if i in discussion_lines:
            continue
        body.append(raw)
        orig.append(i)
    return body, orig


def _region_is_framing_only(
    orig: list[int], framing: set[int], lo: int, hi: int,
) -> bool:
    """True when every body line in ``orig[lo:hi]`` was a framing blank."""
    return all(orig[k] in framing for k in range(lo, hi))


def _body_diff_is_framing_only(
    base_body: list[str], base_orig: list[int], base_framing: set[int],
    current_body: list[str], current_orig: list[int],
    current_framing: set[int],
) -> bool:
    """True when every non-equal diff region between *base_body* and
    *current_body* contains only framing-blank lines.

    Uses ``difflib.SequenceMatcher(autojunk=False)`` to align the two body
    line sequences.  ``autojunk=False`` ensures blank lines are always matched
    (never treated as junk by the heuristic)."""
    matcher = difflib.SequenceMatcher(
        autojunk=False, a=base_body, b=current_body,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in ("delete", "replace") and not _region_is_framing_only(
            base_orig, base_framing, i1, i2,
        ):
            return False
        if tag in ("insert", "replace") and not _region_is_framing_only(
            current_orig, current_framing, j1, j2,
        ):
            return False
    return True


def _prepare(text: str) -> tuple[list[str], list[int], set[int]]:
    """Split *text* into body lines (discussion comments removed), their
    1-indexed original line numbers, and the set of framing-blank numbers."""
    lines = text.splitlines(keepends=True)
    dl = _discussion_line_numbers(text)
    framing = _framing_blank_numbers(lines, dl)
    body, orig = _body_lines(lines, dl)
    return body, orig, framing


def is_discussion_delta(base_text: str | bytes, current_text: str | bytes) -> bool:
    """True when the delta from *base_text* to *current_text* is exclusively
    canonical discussion changes plus framing separators.

    See module docstring for the structural rules.  Pure: no I/O, no
    filesystem access - callers supply both texts.
    """
    try:
        base_bytes = (base_text if isinstance(base_text, bytes)
                      else base_text.encode("utf-8"))
        current_bytes = (current_text if isinstance(current_text, bytes)
                         else current_text.encode("utf-8"))
        base_decoded = base_bytes.decode("utf-8")
        current_decoded = current_bytes.decode("utf-8")
    except UnicodeError:
        return False

    if base_bytes == current_bytes:
        return False

    # Contract 2: for discussion-legality comparison, normalize CRLF/LF to a
    # single logical line ending so a pure newline-style change does not
    # produce a discussion finding. Only "\r\n" -> "\n"; bare CR is untouched.
    base_decoded = base_decoded.replace("\r\n", "\n")
    current_decoded = current_decoded.replace("\r\n", "\n")

    if not parse_threads(current_decoded):
        return False

    base_body, base_orig, base_framing = _prepare(base_decoded)
    current_body, current_orig, current_framing = _prepare(current_decoded)

    return _body_diff_is_framing_only(
        base_body, base_orig, base_framing,
        current_body, current_orig, current_framing,
    )
