"""inline-discussion writer (FR-090/FR-110, AC-0505/0901/0902/1101/1102/1103)."""
import pytest

from tracks.discuss.locate import token_for
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
