"""`trac discuss` CLI (FR-080, IF-003 §7a) — doc-level bypass, not in the event loop.

Five subcommands (query/start/reply/edit/set-status). ``--file`` is canonicalized
and scope-gated to the repo (AC-0903); write commands require ``--token`` and
proceed only on a unique relocation (fail closed). File writes are flock-
serialized via tmp+rename (FR-110, AC-1104).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from pathlib import Path

from tracks.discuss import writer
from tracks.discuss.gate import check_ready
from tracks.discuss.locate import comment_token, token_for
from tracks.discuss.model import speaker_key
from tracks.discuss.parser import parse_threads


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401 - argparse hook
        raise writer.WriteError(f"usage: {message}")


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


def _comment_json(c, parent_text: str = "") -> dict:
    return {
        "depth": c.depth, "speaker": c.speaker, "body": c.body, "line": c.line,
        "mentions": list(c.mentions), "token": comment_token(c, parent_text),
        "children": [_comment_json(ch, c.text) for ch in c.children],
    }


def _thread_json(t) -> dict:
    return {
        "thread_id": t.thread_id, "initiator": t.initiator, "status": t.status,
        "last_speaker": t.last_speaker, "reply_count": t.reply_count,
        "snippet": t.snippet, "mentioned_agents": list(t.mentioned_agents),
        "root": _comment_json(t.root),
        "total_lines": t.total_lines, "anchor_line": t.anchor_line,
        "anchor_text": t.anchor_text, "root_line": t.root_line,
        "root_text": t.root_text, "token": token_for(t),
    }


def _blocker_categories(threads, agent: str) -> dict:
    key = speaker_key(agent)
    mine = [t for t in threads if speaker_key(t.initiator) == key]
    return {
        "unanswered": [t.thread_id for t in mine if t.reply_count == 0],
        "unresolved": [t.thread_id for t in mine if t.status != "resolved"],
        "awaiting_my_reply": [
            t.thread_id for t in threads
            if agent in t.mentioned_agents or speaker_key(t.last_speaker) != key
        ],
    }


def _query(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    threads = parse_threads(text)
    if ns.initiator:
        threads = [t for t in threads
                   if speaker_key(t.initiator) == speaker_key(ns.initiator)]
    if ns.status:
        threads = [t for t in threads if t.status == ns.status]
    out = {"threads": [_thread_json(t) for t in threads]}
    if ns.check_ready:
        out["is_ready"], blockers = check_ready(text)
        out["ready_blockers"] = list(blockers)
    if ns.blocker:
        out.update(_blocker_categories(parse_threads(text), ns.blocker))
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
    token = json.loads(ns.token)
    reply_to = json.loads(ns.reply_to_token) if ns.reply_to_token else None
    _atomic_write(target, lambda t: writer.reply(
        t, ns.thread_id, token, ns.speaker, ns.message, reply_to))
    print("ok")
    return 0


def _edit(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    token = json.loads(ns.token)
    _atomic_write(
        target, lambda t: writer.edit(t, ns.thread_id, token, ns.depth, ns.speaker, ns.new_body))
    print("ok")
    return 0


def _set_status(repo: Path, ns) -> int:
    target = _scope_check(repo, ns.file)
    token = json.loads(ns.token)
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
        sp.add_argument("--token", required=True)
        if name == "reply":
            sp.add_argument("--speaker", required=True)
            sp.add_argument("--reply-to-token",
                            help="comment token to reply to (omit = reply to root)")
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
