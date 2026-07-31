"""inline-discussion writer (FR-090/FR-110, AC-FR0050-05, AC-FR0090-01/02, AC-FR0110-01/02/03)."""
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


# -- start (FR-110 position, AC-FR0050-05 canonical) -------------------------------

def test_start_inserts_after_anchor_blank_line():
    # AC-FR0110-01
    text = "# Heading\n\nSome content.\n"
    out = start(text, 1, "Aaron", "a comment")
    assert out == "# Heading\n\n> **Aaron:** a comment\n\nSome content.\n"


def test_start_canonical_open_unmarked():
    # AC-FR0050-05: open root is the unmarked canonical form
    out = start("# H\n\nbody\n", 1, "Aaron", "hi")
    assert "> **Aaron:** hi" in out


# -- reply (FR-110 position, AC-FR0110-02) -----------------------------------------

def test_reply_appends_to_thread():
    text = "# H\n\n> **Aaron:** root\n"
    out = reply(text, "T-001", _tok(text), "Sage", "a reply")
    assert out == "# H\n\n> **Aaron:** root\n>> **Sage:** a reply\n"


def test_reply_blank_line_before_next_blockquote():
    # AC-FR0110-02
    text = "# H\n\n> **Aaron:** root\n\n> **Bob:** other\n"
    out = reply(text, "T-001", _tok(text), "Sage", "reply")
    assert ">> **Sage:** reply\n\n> **Bob:** other" in out


# -- edit (FR-110, AC-FR0110-03 author-only) ---------------------------------------

def test_edit_author_only():
    text = "# H\n\n> **Aaron:** root\n"
    with pytest.raises(WriteError):
        edit(text, "T-001", _tok(text), 1, "Sage", "new")  # not the author


def test_edit_replaces_body():
    text = "# H\n\n> **Aaron:** root\n"
    out = edit(text, "T-001", _tok(text), 1, "Aaron", "new body")
    assert "> **Aaron:** new body" in out
    assert "root" not in out


# -- set-status (FR-090, AC-FR0090-01/02) ----------------------------------------

def test_set_status_resolved_requires_initiator():
    # AC-FR0090-01 (format-consistency rule)
    text = "# H\n\n> **Aaron:** root\n"
    with pytest.raises(WriteError):
        set_status(text, "T-001", _tok(text), "resolved", "Sage")


def test_set_status_resolved_by_initiator():
    text = "# H\n\n> **Aaron:** root\n"
    out = set_status(text, "T-001", _tok(text), "resolved", "Aaron")
    assert "> **Aaron [RESOLVED]:** root" in out


def test_set_status_reopen_anyone():
    # AC-FR0090-02
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


def test_reply_to_comment_with_descendant_appends_after_subtree():
    # _subtree_end_index must span the target's descendants (Scribe) so the new
    # reply lands after the whole subtree, not between Sage and Scribe.
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n>>> **Scribe:** done\n"
    thread_tok, sage_tok = _sage_token(text)
    out = reply(text, "T-001", thread_tok, "Aaron", "noted", reply_to=sage_tok)
    assert ">>> **Scribe:** done\n>>> **Aaron:** noted" in out


def test_reply_to_comment_before_prose():
    # the subtree scan stops at the following prose (non-blockquote) content
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n\nSome prose.\n"
    thread_tok, sage_tok = _sage_token(text)
    out = reply(text, "T-001", thread_tok, "Scribe", "done", reply_to=sage_tok)
    assert ">> **Sage:** please revise\n>>> **Scribe:** done" in out
    assert out.index(">>> **Scribe:** done") < out.index("Some prose.")


# -- edit a non-root comment (FR-110 depth>1, AC-FR0110-03 author-only) -------------

def test_edit_reply_replaces_body():
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n"
    out = edit(text, "T-001", _tok(text), 2, "Sage", "updated request")
    assert ">> **Sage:** updated request" in out
    assert "please revise" not in out
    assert "> **Aaron:** root" in out  # root untouched


def test_edit_reply_author_only():
    # Aaron authored the root, not the depth-2 reply -> refused
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n"
    with pytest.raises(WriteError):
        edit(text, "T-001", _tok(text), 2, "Aaron", "x")


def test_edit_reply_missing_scans_past_blanks_and_content():
    # no Bob reply exists; the scan skips the intra-thread blank and stops at the
    # following prose -> no match -> WriteError (fail closed)
    text = "# H\n\n> **Aaron:** root\n>> **Sage:** x\n\nSome content.\n"
    with pytest.raises(WriteError):
        edit(text, "T-001", _tok(text), 2, "Bob", "x")


# -- start: blank-line separation when the anchor has no trailing blank --------

def test_start_anchor_at_eof_inserts_blank_before():
    # anchor paragraph ends the doc with no trailing blank -> _splice inserts a
    # separating blank line before the new thread
    text = "# H\n\nanchor para"
    out = start(text, 3, "Aaron", "comment")
    assert "anchor para\n\n> **Aaron:** comment" in out
