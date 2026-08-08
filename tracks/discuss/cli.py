"""`trac discuss` CLI (FR-080, IF-003 §7a) — doc-level bypass, not in the event loop.

Five subcommands (query/start/reply/edit/set-status). ``--file`` is canonicalized
and scope-gated to the repo (AC-0903); write commands require ``--token`` and
proceed only on a unique relocation (fail closed). File writes are flock-
serialized via tmp+rename (FR-110, AC-1104).

Tokens: query returns an opaque ``token_str`` (``z1.<base64url(zlib(json))>``)
for each thread and comment; ``--compact`` omits the legacy ``token`` dict and
location fields. Write commands accept inline JSON, z1 tokens, and legacy
base64 tokens via ``_decode_token`` (backward compatible).
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import sys
import zlib
from pathlib import Path

from tracks.discuss import writer
from tracks.discuss.gate import check_ready
from tracks.discuss.locate import comment_token, token_for
from tracks.discuss.model import iter_comments, speaker_key
from tracks.discuss.parser import parse_threads


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401 - argparse hook
        raise writer.WriteError(f"usage: {message}")


def _encode_token_str(token: dict) -> str:
    """Opaque, stable ASCII encoding of a token dict.

    Compact JSON (sorted keys) -> UTF-8 -> zlib compress -> URL-safe base64,
    padding stripped, prefixed ``z1.``. The same dict always yields the same
    string; decode restores the dict.
    """
    raw = json.dumps(token, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    compressed = zlib.compress(raw.encode("utf-8"), level=9)
    return "z1." + base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


_TOKEN_SHAPES = {
    "thread": {"total_lines": int, "anchor_line": int, "anchor_text": str,
               "root_line": int, "root_text": str},
    "comment": {"text": str, "depth": int, "speaker": str},
}


def _validate_token_shape(tok: dict, kind: str) -> None:
    """Validate required fields/types for a thread or comment token.

    Cross-checked against locate.py's token contract (token_for / comment_token).
    Raises WriteError on missing field or wrong type (no traceback, fail closed).
    ``parent`` is optional for comment tokens (locate_comment uses .get default).
    """
    for field, typ in _TOKEN_SHAPES[kind].items():
        if field not in tok:
            raise writer.WriteError(f"malformed {kind} token: missing {field!r}")
        val = tok[field]
        if isinstance(val, bool) or not isinstance(val, typ):
            raise writer.WriteError(
                f"malformed {kind} token: {field!r} must be {typ.__name__}")
    if kind == "comment" and "parent" in tok and not isinstance(tok["parent"], str):
        raise writer.WriteError("malformed comment token: 'parent' must be str")


def _decode_token(raw: str, kind: str) -> dict:
    """Decode and validate ``--token`` / ``--reply-to-token``.

    Accepts three transport forms: (1) inline JSON (starts with ``{``),
    (2) ``z1.<base64url(zlib(json))>`` from query, (3) legacy uncompressed
    base64url (no prefix, backward compat). Validates the decoded dict has
    the required fields/types for ``kind`` ("thread" or "comment"). Raises
    WriteError on malformed input so the CLI returns a clean error (no
    traceback, no file write, fail closed).
    """
    try:
        if raw.startswith("{"):
            tok = json.loads(raw)
        elif raw.startswith("z1."):
            payload = raw[3:]
            padded = payload + "=" * (-len(payload) % 4)
            compressed = base64.urlsafe_b64decode(padded.encode("ascii"))
            tok = json.loads(zlib.decompress(compressed).decode("utf-8"))
        else:
            padded = raw + "=" * (-len(raw) % 4)
            tok = json.loads(
                base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, zlib.error) as e:
        raise writer.WriteError(f"malformed token: {e}") from None
    if not isinstance(tok, dict):
        raise writer.WriteError("malformed token: expected a JSON object")
    _validate_token_shape(tok, kind)
    return tok


def _scope_check(repo: Path, file_arg: str) -> Path:
    """Canonicalize ``file_arg`` and require it inside ``repo`` (AC-0903)."""
    raw = Path(file_arg)
    target = raw.resolve() if raw.is_absolute() else (repo / raw).resolve()
    try:
        target.relative_to(repo.resolve())
    except ValueError:
        raise writer.WriteError(f"--file outside repo scope: {file_arg}") from None
    return target


def _atomic_write(path: Path, transform) -> None:
    """flock-serialized read-modify-write: tmp + rename (FR-110, AC-1104).

    If ``transform`` raises (LocateFailure/WriteError) nothing is written, so the
    file is left byte-for-byte unchanged (fail closed).
    """
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "w", encoding="utf-8") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(transform(text), encoding="utf-8")
            tmp.replace(path)
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def _comment_json(c, parent_text: str = "", *, compact: bool = False) -> dict:
    tok = comment_token(c, parent_text)
    d = {
        "depth": c.depth, "speaker": c.speaker, "body": c.body, "line": c.line,
        "mentions": list(c.mentions), "token_str": _encode_token_str(tok),
        "children": [_comment_json(ch, c.text, compact=compact) for ch in c.children],
    }
    if not compact:
        d["token"] = tok
    return d


def _thread_json(t, *, compact: bool = False) -> dict:
    tok = token_for(t)
    d = {
        "thread_id": t.thread_id, "initiator": t.initiator, "status": t.status,
        "last_speaker": t.last_speaker, "reply_count": t.reply_count,
        "snippet": t.snippet, "mentioned_agents": list(t.mentioned_agents),
        "root": _comment_json(t.root, compact=compact),
        "token_str": _encode_token_str(tok),
    }
    if not compact:
        d.update(
            total_lines=t.total_lines, anchor_line=t.anchor_line,
            anchor_text=t.anchor_text, root_line=t.root_line,
            root_text=t.root_text, token=tok)
    return d


def _blocker_categories(threads, agent: str) -> dict:
    key = speaker_key(agent)
    mine = [t for t in threads if speaker_key(t.initiator) == key]
    # awaiting_my_reply (FR-050 orthogonal @mention semantic): a comment that
    # @mentions me (requests my answer) AND has no child reply yet.
    awaiting = []
    for t in threads:
        for c in iter_comments(t.root):
            if not c.children and any(speaker_key(m) == key for m in c.mentions):
                awaiting.append(t.thread_id)
                break
    return {
        "unanswered": [t.thread_id for t in mine if t.reply_count == 0],
        "unresolved": [t.thread_id for t in mine if t.status != "resolved"],
        "awaiting_my_reply": awaiting,
    }


def _validate_query_flags(ns) -> None:
    """Validate mutual exclusivity and required combinations for query."""
    if ns.inbox and ns.blocker:
        raise writer.WriteError("--inbox and --blocker are mutually exclusive")
    if ns.summary_only and not ns.check_ready:
        raise writer.WriteError("--summary-only requires --check-ready")
    active = sum(1 for a in (ns.thread_id, ns.inbox, ns.summary_only) if a)
    if active > 1:
        raise writer.WriteError(
            "--thread-id/--inbox/--summary-only are mutually exclusive")


def _ready_fields(text: str) -> dict:
    is_ready, blockers = check_ready(text)
    return {"is_ready": is_ready, "ready_blockers": list(blockers)}


def _thread_id_output(threads, ns, text) -> dict:
    matched = [t for t in threads if t.thread_id == ns.thread_id]
    if not matched:
        raise writer.WriteError(f"thread not found: {ns.thread_id}")
    out = {"threads": [_thread_json(matched[0], compact=True)]}
    if ns.check_ready:
        out.update(_ready_fields(text))
    return out


def _inbox_output(threads, ns, text) -> dict:
    cats = _blocker_categories(threads, ns.inbox)
    inbox_ids = set()
    for key in ("unanswered", "unresolved", "awaiting_my_reply"):
        inbox_ids.update(cats[key])
    inbox_threads = [t for t in threads if t.thread_id in inbox_ids]
    out = {"threads": [_thread_json(t, compact=True) for t in inbox_threads]}
    out.update(cats)
    if ns.check_ready:
        out.update(_ready_fields(text))
    return out


def _full_output(threads, ns, text) -> dict:
    if ns.initiator:
        threads = [t for t in threads
                   if speaker_key(t.initiator) == speaker_key(ns.initiator)]
    if ns.status:
        threads = [t for t in threads if t.status == ns.status]
    out = {"threads": [_thread_json(t, compact=ns.compact) for t in threads]}
    if ns.check_ready:
        out.update(_ready_fields(text))
    if ns.blocker:
        out.update(_blocker_categories(parse_threads(text), ns.blocker))
    return out


def _query(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    threads = parse_threads(text)
    _validate_query_flags(ns)
    if ns.summary_only:
        out = _ready_fields(text)
    elif ns.thread_id:
        out = _thread_id_output(threads, ns, text)
    elif ns.inbox:
        out = _inbox_output(threads, ns, text)
    else:
        out = _full_output(threads, ns, text)
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _start(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    _atomic_write(target, lambda t: writer.start(t, ns.anchor_line, ns.speaker, ns.message))
    new = parse_threads(target.read_text(encoding="utf-8"))
    first = writer.format_root(ns.speaker, "open", ns.message)[0]
    tid = next((t.thread_id for t in new if t.root_text == first), None)
    print(tid or "")
    return 0


def _reply(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    token = _decode_token(ns.token, "thread")
    reply_to = _decode_token(ns.reply_to_token, "comment") if ns.reply_to_token else None
    _atomic_write(target, lambda t: writer.reply(
        t, ns.thread_id, token, ns.speaker, ns.message, reply_to))
    print("ok")
    return 0


def _edit(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    token = _decode_token(ns.token, "thread")
    _atomic_write(
        target, lambda t: writer.edit(t, ns.thread_id, token, ns.depth, ns.speaker, ns.new_body))
    print("ok")
    return 0


def _set_status(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    token = _decode_token(ns.token, "thread")
    _atomic_write(
        target, lambda t: writer.set_status(t, ns.thread_id, token, ns.status, ns.operator))
    print("ok")
    return 0


def _build_parser() -> _Parser:
    p = _Parser(prog="trac discuss")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query")
    q.add_argument("--file", required=True)
    q.add_argument("--initiator")
    q.add_argument("--blocker")
    q.add_argument("--status")
    q.add_argument("--check-ready", action="store_true")
    q.add_argument("--compact", action="store_true",
                   help="omit legacy token dict and location fields (for automation)")
    q.add_argument("--inbox", metavar="Speaker",
                   help="union of unanswered/unresolved/awaiting_my_reply (compact, auto)")
    q.add_argument("--thread-id", metavar="T-NNN",
                   help="return only this thread (compact, auto)")
    q.add_argument("--summary-only", action="store_true",
                   help="with --check-ready: output only gate summary (no threads)")

    s = sub.add_parser("start")
    s.add_argument("--file", required=True)
    s.add_argument("--anchor-line", type=int, required=True)
    s.add_argument("--speaker", required=True)
    s.add_argument("message")

    for name, extra in (
        ("reply", [("message", {})]),
        ("edit", [("new_body", {})]),
        ("set-status", []),
    ):
        sp = sub.add_parser(name)
        sp.add_argument("--file", required=True)
        sp.add_argument("--thread-id", required=True)
        sp.add_argument("--token", required=True,
                        help="thread locate token: query's token_str (opaque) or inline JSON")
        if name == "reply":
            sp.add_argument("--speaker", required=True)
            sp.add_argument("--reply-to-token",
                            help="comment token_str (opaque) or inline JSON to reply to "
                                 "(omit = reply to root)")
        if name == "edit":
            sp.add_argument("--depth", type=int, required=True)
            sp.add_argument("--speaker", required=True)
        if name == "set-status":
            sp.add_argument("--status", required=True, choices=["resolved", "reopen"])
            sp.add_argument("--operator", required=True)
        for arg, kw in extra:
            sp.add_argument(arg, **kw)
    return p


_HANDLERS = {
    "query": _query, "start": _start, "reply": _reply,
    "edit": _edit, "set-status": _set_status,
}


def run_discuss(repo: Path, argv: list) -> int:
    """Entry for `trac discuss ...`; returns the process exit code."""
    try:
        ns = _build_parser().parse_args(argv)
        return _HANDLERS[ns.cmd](repo, ns)
    except writer.DiscussError as e:
        print(str(e), file=sys.stderr)
        return 1
