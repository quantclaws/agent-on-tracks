"""inline-discussion model + normalization (FR-060, AC-FR0060-03)."""
import dataclasses

import pytest

from tracks.discuss.model import (
    Comment,
    LocateResult,
    Thread,
    normalize,
    speaker_key,
)


def test_normalize_strip_and_collapse():
    assert normalize("  a   b\n\tc ") == "a b c"


def test_normalize_nfc():
    assert normalize("e\u0301") == "\u00e9"  # decomposed -> composed


def test_normalize_preserves_case():
    assert normalize("AbC") == "AbC"  # AC-FR0060-03: no case change


def test_normalize_preserves_markdown():
    assert normalize("**bold** [RESOLVED]:") == "**bold** [RESOLVED]:"


def test_speaker_key_lowercases():
    assert speaker_key("  Aaron  ") == "aaron"
    assert speaker_key("Sage") == "sage"


def _thread(**over):
    root = Comment(
        depth=1, speaker="Aaron", body="hi", line=5, text="> **Aaron:** hi",
        mentions=(), children=(),
    )
    base = {
        "thread_id": "T-001", "initiator": "Aaron", "status": "open",
        "last_speaker": "Aaron", "reply_count": 0, "snippet": "hi",
        "mentioned_agents": (), "root": root, "total_lines": 10, "anchor_line": 3,
        "anchor_text": "anchor", "root_line": 5, "root_text": "> **Aaron:** hi",
    }
    base.update(over)
    return Thread(**base)


def test_thread_is_frozen():
    t = _thread()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.status = "resolved"


def test_locate_defaults():
    assert LocateResult(status="unique", thread_id="T-001").candidates is None
