"""inline-discussion writer (FR-090/FR-110, AC-0505/0901/0902/1101/1102/1103)."""
import pytest

from tracks.discuss.locate import comment_token, token_for
from tracks.discuss.parser import parse_threads
from tracks.discuss.writer import (
    LocateFailure,
    WriteError,
    edit,
    reply,
    set_status,
    start,
)


def _tok(text, idx=0):
    return token_for(parse_threads(text)[idx])


# -- start (FR-110 position, AC-0505 canonical) -------------------------------

def test_start_inserts_after_anchor_blank_line():
    # AC-1101
    text = "# Heading\n\nSome content.\n"
    out = start(text, 1, "Aaron", "a comment")
    assert out == "# Heading\n\n> **Aaron:** a comment\n\nSome content.\n"


def test_start_canonical_open_unmarked():
    # AC-0505: open root is the unmarked canonical form
    out = start("# H\n\nbody\n", 1, "Aaron", "hi")
    assert "> **Aaron:** hi" in out


# -- reply (FR-110 position, AC-1102) -----------------------------------------

def test_reply_appends_to_thread():
    text = "# H\n\n> **Aaron:** root\n"
    out = reply(text, "T-001", _tok(text), "Sage", "a reply")
    assert out == "# H\n\n> **Aaron:** root\n>> **Sage:** a reply\n"


def test_reply_blank_line_before_next_blockquote():
    # AC-1102
    text = "# H\n\n> **Aaron:** root\n\n> **Bob:** other\n"
    out = reply(text, "T-001", _tok(text), "Sage", "reply")
    assert ">> **Sage:** reply\n\n> **Bob:** other" in out


# -- edit (FR-110, AC-1103 author-only) ---------------------------------------

def test_edit_author_only():
    text = "# H\n\n> **Aaron:** root\n"
    with pytest.raises(WriteError):
        edit(text, "T-001", _tok(text), 1, "Sage", "new")  # not the author


def test_edit_replaces_body():
    text = "# H\n\n> **Aaron:** root\n"
    out = edit(text, "T-001", _tok(text), 1, "Aaron", "new body")
    assert "> **Aaron:** new body" in out
    assert "root" not in out


# -- set-status (FR-090, AC-0901/0902) ----------------------------------------

def test_set_status_resolved_requires_initiator():
    # AC-0901 (format-consistency rule)
    text = "# H\n\n> **Aaron:** root\n"
    with pytest.raises(WriteError):
        set_status(text, "T-001", _tok(text), "resolved", "Sage")


def test_set_status_resolved_by_initiator():
    text = "# H\n\n> **Aaron:** root\n"
    out = set_status(text, "T-001", _tok(text), "resolved", "Aaron")
    assert "> **Aaron [RESOLVED]:** root" in out


def test_set_status_reopen_anyone():
    # AC-0902
    text = "# H\n\n> **Aaron:** root\n"
    out = set_status(text, "T-001", _tok(text), "reopen", "Whoever")
    assert "> **Aaron [REOPEN]:** root" in out


def test_set_status_invalid():
    text = "# H\n\n> **Aaron:** root\n"
    with pytest.raises(WriteError):
        set_status(text, "T-001", _tok(text), "open", "Aaron")


# -- fail closed (FR-070 freshness) -------------------------------------------

def test_reply_stale_raises_locate_failure():
    orig = "# H\n\n> **Aaron:** comment"
    reordered = "# H\n\n> **Zed:** newfirst\n\n> **Aaron:** comment"
    with pytest.raises(LocateFailure) as exc:
        reply(reordered, "T-001", _tok(orig), "Sage", "x")
    assert exc.value.result.status == "stale"


# -- nesting: reply to a specific comment (FR-050 depth = reply to whom) ------

def _sage_token(text):
    t = parse_threads(text)[0]
    sage = next(c for c in t.root.children if c.speaker == "Sage")
    return token_for(t), comment_token(sage, t.root.text)


def test_reply_to_comment_nests_under_it():
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n>> **Aaron:** thanks\n"
    thread_tok, sage_tok = _sage_token(text)
    out = reply(text, "T-001", thread_tok, "Scribe", "done", reply_to=sage_tok)
    # Scribe is depth 3, inserted right after Sage and before the next sibling
    assert ">> **Sage:** please revise\n>>> **Scribe:** done" in out
    assert out.index(">>> **Scribe:** done") < out.index(">> **Aaron:** thanks")


def test_reply_to_root_appends_depth2():
    text = "# H\n\n> **Aaron:** root\n"
    t = parse_threads(text)[0]
    out = reply(text, "T-001", token_for(t), "Sage", "reply",
                reply_to=comment_token(t.root, ""))
    assert ">> **Sage:** reply" in out  # depth 2 (root.depth + 1)


def test_reply_to_ambiguous_comment_fails_closed():
    # two identical Sage replies under the root -> comment token is ambiguous
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** same\n>> **Sage:** same\n"
    t = parse_threads(text)[0]
    sage = t.root.children[0]
    dup_tok = comment_token(sage, t.root.text)
    with pytest.raises(LocateFailure) as exc:
        reply(text, "T-001", token_for(t), "Scribe", "x", reply_to=dup_tok)
    assert exc.value.result.status == "ambiguous"
