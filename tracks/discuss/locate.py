"""Relocate a discussion thread by content token (FR-070, IF-003 §6).

Pure: reads only the given text. Every locate is a fresh full scan; the token
(the query's 5-tuple) is relocated through a 4-level degrade — L0 exact ->
L1 Levenshtein window -> L2 root-only -> L3 not found. A write proceeds ONLY on
a unique, confident match whose current thread_id equals the requested one
(fail closed: ambiguous / not_found / stale never write).
"""
from __future__ import annotations

import re

from tracks.discuss.model import LocateResult, speaker_key
from tracks.discuss.parser import parse_tag, parse_threads

_BQ = re.compile(r"^\s*(>+)\s*(.*)$")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _threshold(text: str) -> int:
    return max(5, int(len(text) * 0.2))


def _root_speaker(root_text: str):
    m = _BQ.match(root_text)
    if not m:
        return None
    tag = parse_tag(m.group(2))
    return speaker_key(tag[0]) if tag else None


def token_for(thread) -> dict:
    """The content locate token (5-tuple) a query hands back for a thread."""
    return {
        "total_lines": thread.total_lines,
        "anchor_line": thread.anchor_line,
        "anchor_text": thread.anchor_text,
        "root_line": thread.root_line,
        "root_text": thread.root_text,
    }


def _l0(threads, token, delta):
    target = token["root_line"] + delta
    return [t for t in threads
            if t.root_line == target
            and t.root_text == token["root_text"]
            and t.anchor_text == token["anchor_text"]]


def _l1(threads, token, delta):
    target = token["root_line"] + delta
    window = max(abs(delta) + 5, 10)
    root_tok, anc_tok = token["root_text"], token["anchor_text"]
    root_th, anc_th = _threshold(root_tok), _threshold(anc_tok)
    out = []
    for t in threads:
        if abs(t.root_line - target) > window:
            continue
        if (_levenshtein(t.root_text, root_tok) <= root_th
                and _levenshtein(t.anchor_text, anc_tok) <= anc_th):
            out.append(t)
    return out


def _l2(threads, token):
    want = _root_speaker(token["root_text"])
    if want is None:
        return []
    thresh = _threshold(token["root_text"])
    scored = [(t, _levenshtein(t.root_text, token["root_text"]))
              for t in threads if speaker_key(t.initiator) == want]
    within = [(t, d) for t, d in scored if d <= thresh]
    if not within:
        return []
    best = min(d for _, d in within)
    return [t for t, d in within if d == best]


def _relocate(text, token):
    threads = parse_threads(text)
    delta = len(text.splitlines()) - token["total_lines"]
    for hits in (_l0(threads, token, delta), _l1(threads, token, delta),
                 _l2(threads, token)):
        if not hits:
            continue
        if len(hits) == 1:
            return "unique", hits[0], None
        return "ambiguous", None, tuple(t.root_line for t in hits)
    return "not_found", None, None


def locate(text: str, token: dict, thread_id: str) -> LocateResult:
    """Relocate ``thread_id`` by content ``token`` (FR-070 + freshness).

    unique    -> content matched exactly one thread AND its current thread_id
                 equals ``thread_id``; a write may proceed.
    stale     -> content matched, but the current thread_id differs (reorder
                 drift); re-query required (AC-0608).
    ambiguous -> tied / low-confidence candidates; candidates (line numbers).
    not_found -> L3 failed (AC-0607 / AC-0704).
    """
    kind, thread, candidates = _relocate(text, token)
    if kind == "not_found":
        return LocateResult("not_found")
    if kind == "ambiguous":
        return LocateResult("ambiguous", candidates=candidates)
    if thread.thread_id == thread_id:
        return LocateResult("unique", thread_id=thread.thread_id)
    return LocateResult("stale")
