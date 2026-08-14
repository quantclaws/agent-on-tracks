"""trac discuss CLI (FR-080, AC-FR0080-01..04, AC-FR0060-09, AC-FR0090-03, AC-FR0110-04)."""

import base64
import json
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from tracks.discuss.cli import run_discuss
from tracks.discuss.parser import parse_threads


def _doc(tmp_path, body="# S-001: 标题\n\n说明文字。\n"):
    (tmp_path / "story.md").write_text(body, encoding="utf-8")
    return tmp_path


def _start(tmp_path, speaker="Aaron", msg="问题"):
    return run_discuss(
        tmp_path, ["start", "--file", "story.md", "--anchor-line", "1", "--speaker", speaker, msg]
    )


def _query(tmp_path, capsys, *extra):
    assert run_discuss(tmp_path, ["query", "--file", "story.md", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def _encode(token):
    """Mirror of CLI _encode_token_str (z1 compressed) for round-trip verification."""
    raw = json.dumps(token, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    compressed = zlib.compress(raw.encode("utf-8"), level=9)
    return "z1." + base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


def _encode_legacy(token):
    """Old uncompressed base64url encoding (Round 1) for backward-compat tests."""
    raw = json.dumps(token, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode(token_str):
    """Mirror of CLI _decode_token z1 path for round-trip verification."""
    assert token_str.startswith("z1.")
    payload = token_str[3:]
    padded = payload + "=" * (-len(payload) % 4)
    compressed = base64.urlsafe_b64decode(padded.encode("ascii"))
    return json.loads(zlib.decompress(compressed).decode("utf-8"))


# -- AC-FR0080-01: five subcommands callable ---------------------------------------


def test_five_subcommands_callable(tmp_path, capsys):
    _doc(tmp_path)
    assert _start(tmp_path) == 0
    tid = capsys.readouterr().out.strip()
    q = _query(tmp_path, capsys)
    tok = json.dumps(q["threads"][0]["token"])
    assert (
        run_discuss(
            tmp_path,
            [
                "reply",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tok,
                "--speaker",
                "Sage",
                "回复",
            ],
        )
        == 0
    )
    capsys.readouterr()
    q = _query(tmp_path, capsys)
    tok = json.dumps(q["threads"][0]["token"])
    assert (
        run_discuss(
            tmp_path,
            [
                "edit",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tok,
                "--depth",
                "1",
                "--speaker",
                "Aaron",
                "改后",
            ],
        )
        == 0
    )
    capsys.readouterr()
    q = _query(tmp_path, capsys)
    tok = json.dumps(q["threads"][0]["token"])
    assert (
        run_discuss(
            tmp_path,
            [
                "set-status",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tok,
                "--status",
                "resolved",
                "--operator",
                "Aaron",
            ],
        )
        == 0
    )


# -- AC-FR0080-02/03/04: query output ------------------------------------------


def test_query_json_has_five_tuple_and_token(tmp_path, capsys):
    # AC-FR0080-02
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    t = _query(tmp_path, capsys)["threads"][0]
    for field in ("total_lines", "anchor_line", "anchor_text", "root_line", "root_text", "token"):
        assert field in t


def test_query_blocker_categories(tmp_path, capsys):
    # AC-FR0080-03
    _doc(tmp_path)
    _start(tmp_path)  # Aaron initiates an open, unreplied thread
    capsys.readouterr()
    q = _query(tmp_path, capsys, "--blocker", "Aaron")
    assert q["unanswered"] == ["T-001"]
    assert q["unresolved"] == ["T-001"]
    assert "awaiting_my_reply" in q


def test_query_check_ready(tmp_path, capsys):
    # AC-FR0080-04
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    q = _query(tmp_path, capsys, "--check-ready")
    assert q["is_ready"] is False and q["ready_blockers"] == ["T-001"]


# -- AC-FR0090-03: scope gate ------------------------------------------------------


def test_scope_gate_rejects_traversal(tmp_path, capsys):
    _doc(tmp_path)
    assert run_discuss(tmp_path, ["query", "--file", "../outside.md"]) == 1
    assert "scope" in capsys.readouterr().err


def test_scope_gate_rejects_external_absolute(tmp_path, capsys):
    _doc(tmp_path)
    assert run_discuss(tmp_path, ["query", "--file", "/etc/passwd"]) == 1


# -- AC-FR0060-09: missing token refused (fail closed) -----------------------------


def test_write_without_token_refused(tmp_path, capsys):
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    rc = run_discuss(
        tmp_path, ["reply", "--file", "story.md", "--thread-id", "T-001", "--speaker", "Sage", "x"]
    )  # no --token
    assert rc == 1


# -- AC-FR0060-08: stale token does not write --------------------------------------


def test_stale_token_leaves_file_unchanged(tmp_path, capsys):
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    tok = json.dumps(_query(tmp_path, capsys)["threads"][0]["token"])
    # reorder: insert a thread before Aaron so Aaron drifts T-001 -> T-002
    _start(tmp_path, speaker="Zed", msg="插队")
    capsys.readouterr()
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            "T-001",
            "--token",
            tok,
            "--speaker",
            "Sage",
            "x",
        ],
    )
    assert rc == 1
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before


def test_reply_to_comment_via_cli(tmp_path, capsys):
    # FR-050 nesting via CLI: reply to Sage -> depth-3 child under Sage
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n>> **Aaron:** thanks\n")
    t = _query(tmp_path, capsys)["threads"][0]
    thread_tok = json.dumps(t["token"])
    sage = next(c for c in t["root"]["children"] if c["speaker"] == "Sage")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            t["thread_id"],
            "--token",
            thread_tok,
            "--reply-to-token",
            json.dumps(sage["token"]),
            "--speaker",
            "Scribe",
            "done",
        ],
    )
    assert rc == 0
    out = (tmp_path / "story.md").read_text(encoding="utf-8")
    assert ">> **Sage:** please revise\n>>> **Scribe:** done" in out


def test_blocker_awaiting_mention_without_child(tmp_path, capsys):
    # FR-050 orthogonal @mention: requests an answer; awaiting iff no child reply
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** @Scribe please revise\n")
    q = _query(tmp_path, capsys, "--blocker", "Scribe")
    assert q["awaiting_my_reply"] == ["T-001"]  # Sage @mentioned Scribe, unanswered


def test_blocker_awaiting_cleared_by_child_reply(tmp_path, capsys):
    _doc(
        tmp_path,
        "# H\n\n> **Aaron:** root\n>> **Sage:** @Scribe please revise\n>>> **Scribe:** done\n",
    )
    q = _query(tmp_path, capsys, "--blocker", "Scribe")
    assert q["awaiting_my_reply"] == []  # Scribe replied (child) -> no longer awaiting


# -- AC-FR0110-04: flock serializes concurrent writes ------------------------------


def test_concurrent_starts_no_lost_writes(tmp_path):
    _doc(tmp_path)

    def do_start(i):
        return run_discuss(
            tmp_path,
            ["start", "--file", "story.md", "--anchor-line", "1", "--speaker", f"U{i}", f"q{i}"],
        )

    with ThreadPoolExecutor(max_workers=5) as ex:
        assert list(ex.map(do_start, range(5))) == [0] * 5
    text = (tmp_path / "story.md").read_text(encoding="utf-8")
    assert len(parse_threads(text)) == 5


# -- opaque token_str (backward-compatible transport) ------------------------------


def test_query_emits_token_str_for_thread_and_comments(tmp_path, capsys):
    # Contract 1: query returns both token dict and opaque token_str (thread + comment)
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** reply\n")
    t = _query(tmp_path, capsys)["threads"][0]
    assert "token_str" in t and isinstance(t["token_str"], str)
    assert _decode(t["token_str"]) == t["token"]
    sage = t["root"]["children"][0]
    assert "token_str" in sage and isinstance(sage["token_str"], str)
    assert _decode(sage["token_str"]) == sage["token"]


def test_opaque_thread_token_works_for_reply_edit_set_status(tmp_path, capsys):
    # Contract 2: opaque thread token_str accepted by reply / edit / set-status
    _doc(tmp_path)
    _start(tmp_path)
    tid = capsys.readouterr().out.strip()
    tstr = _query(tmp_path, capsys)["threads"][0]["token_str"]
    assert (
        run_discuss(
            tmp_path,
            [
                "reply",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tstr,
                "--speaker",
                "Sage",
                "回复",
            ],
        )
        == 0
    )
    capsys.readouterr()
    tstr = _query(tmp_path, capsys)["threads"][0]["token_str"]
    assert (
        run_discuss(
            tmp_path,
            [
                "edit",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tstr,
                "--depth",
                "1",
                "--speaker",
                "Aaron",
                "改后",
            ],
        )
        == 0
    )
    capsys.readouterr()
    tstr = _query(tmp_path, capsys)["threads"][0]["token_str"]
    assert (
        run_discuss(
            tmp_path,
            [
                "set-status",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tstr,
                "--status",
                "resolved",
                "--operator",
                "Aaron",
            ],
        )
        == 0
    )


def test_opaque_comment_token_replies_at_depth_3(tmp_path, capsys):
    # Contract 3: opaque comment token_str makes reply write at depth=3
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n>> **Aaron:** thanks\n")
    t = _query(tmp_path, capsys)["threads"][0]
    sage = next(c for c in t["root"]["children"] if c["speaker"] == "Sage")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            t["thread_id"],
            "--token",
            t["token_str"],
            "--reply-to-token",
            sage["token_str"],
            "--speaker",
            "Scribe",
            "done",
        ],
    )
    assert rc == 0
    out = (tmp_path / "story.md").read_text(encoding="utf-8")
    assert ">> **Sage:** please revise\n>>> **Scribe:** done" in out


def test_inline_json_token_still_accepted(tmp_path, capsys):
    # Contract 4: old inline JSON token form continues to work
    _doc(tmp_path)
    _start(tmp_path)
    tid = capsys.readouterr().out.strip()
    tok = json.dumps(_query(tmp_path, capsys)["threads"][0]["token"])
    assert (
        run_discuss(
            tmp_path,
            [
                "reply",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                tok,
                "--speaker",
                "Sage",
                "回复",
            ],
        )
        == 0
    )


def test_stale_opaque_token_rejected_file_unchanged(tmp_path, capsys):
    # Contract 5: stale opaque token rejected, file unchanged
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    tstr = _query(tmp_path, capsys)["threads"][0]["token_str"]
    _start(tmp_path, speaker="Zed", msg="插队")  # drift Aaron T-001 -> T-002
    capsys.readouterr()
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            "T-001",
            "--token",
            tstr,
            "--speaker",
            "Sage",
            "x",
        ],
    )
    assert rc == 1
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before


def test_opaque_comment_token_ambiguous_rejected(tmp_path, capsys):
    # Contract 5 (bonus): opaque comment token ambiguous -> fail closed, no write
    body = "# H\n\n> **Aaron:** root\n>> **Sage:** same\n>> **Sage:** same\n"
    _doc(tmp_path, body)
    t = _query(tmp_path, capsys)["threads"][0]
    sage = next(c for c in t["root"]["children"] if c["speaker"] == "Sage")
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            t["thread_id"],
            "--token",
            t["token_str"],
            "--reply-to-token",
            sage["token_str"],
            "--speaker",
            "Scribe",
            "done",
        ],
    )
    assert rc == 1  # ambiguous -> fail closed
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before


def test_malformed_opaque_token_no_traceback_no_write(tmp_path, capsys):
    # Contract 6: malformed opaque token -> usage error (no traceback, no file write)
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            "T-001",
            "--token",
            "garbage",
            "--speaker",
            "Sage",
            "x",
        ],
    )
    assert rc == 1  # existing usage-error semantics (no traceback, no file write)
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before
    assert "malformed" in capsys.readouterr().err


# -- token shape validation: wrong-shape / cross-kind / type-error -----------------


def _render(token_dict, form):
    if form == "opaque":
        return _encode(token_dict)
    if form == "legacy":
        return _encode_legacy(token_dict)
    return json.dumps(token_dict)


@pytest.mark.parametrize("form", ["opaque", "json", "legacy"])
@pytest.mark.parametrize(
    "cmd,extra,bad_token",
    [
        # wrong-shape (no required fields) on all three write commands
        ("reply", ["--speaker", "Sage", "x"], {"x": 1}),
        ("edit", ["--depth", "1", "--speaker", "Aaron", "new"], {"x": 1}),
        ("set-status", ["--status", "resolved", "--operator", "Aaron"], {"x": 1}),
        # cross-kind: comment token used as --token (thread)
        (
            "reply",
            ["--speaker", "Sage", "x"],
            {"text": "r", "depth": 1, "speaker": "A", "parent": ""},
        ),
        # type-error: total_lines is str instead of int
        (
            "reply",
            ["--speaker", "Sage", "x"],
            {
                "total_lines": "x",
                "anchor_line": 1,
                "anchor_text": "a",
                "root_line": 1,
                "root_text": "r",
            },
        ),
    ],
)
def test_bad_thread_token_rejected(tmp_path, capsys, form, cmd, extra, bad_token):
    """Wrong-shape / cross-kind / type-error --token -> rc=1, no traceback, no write."""
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            cmd,
            "--file",
            "story.md",
            "--thread-id",
            "T-001",
            "--token",
            _render(bad_token, form),
            *extra,
        ],
    )
    assert rc == 1
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before
    assert "malformed" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("form", ["opaque", "json", "legacy"])
@pytest.mark.parametrize(
    "bad_token",
    [
        {"x": 1},  # wrong-shape
        # cross-kind: thread token used as --reply-to-token (comment)
        {"total_lines": 5, "anchor_line": 1, "anchor_text": "a", "root_line": 3, "root_text": "r"},
        # type-error: depth is str instead of int
        {"text": "r", "depth": "x", "speaker": "A"},
    ],
)
def test_bad_reply_to_token_rejected(tmp_path, capsys, form, bad_token):
    """Wrong-shape / cross-kind / type-error --reply-to-token -> rc=1, no write."""
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** reply\n")
    capsys.readouterr()
    t = _query(tmp_path, capsys)["threads"][0]
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            "T-001",
            "--token",
            t["token_str"],
            "--reply-to-token",
            _render(bad_token, form),
            "--speaker",
            "Scribe",
            "x",
        ],
    )
    assert rc == 1
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before
    assert "malformed" in capsys.readouterr().err.lower()


# -- compact query + z1 compression ------------------------------------------------


def test_query_compact_omits_token_and_location(tmp_path, capsys):
    """--compact omits legacy token dict and thread location fields."""
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** reply\n")
    t = _query(tmp_path, capsys, "--compact")["threads"][0]
    assert "token_str" in t
    assert "token" not in t
    for f in ("total_lines", "anchor_line", "anchor_text", "root_line", "root_text"):
        assert f not in t
    sage = t["root"]["children"][0]
    assert "token_str" in sage
    assert "token" not in sage


def test_query_default_includes_token_and_location(tmp_path, capsys):
    """Default (non-compact) query still includes token dict and location fields."""
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** reply\n")
    t = _query(tmp_path, capsys)["threads"][0]
    assert "token" in t and "token_str" in t
    for f in ("total_lines", "anchor_line", "anchor_text", "root_line", "root_text"):
        assert f in t
    sage = t["root"]["children"][0]
    assert "token" in sage and "token_str" in sage


def test_z1_token_str_is_compressed(tmp_path, capsys):
    """z1 token_str is shorter than legacy uncompressed for Chinese text."""
    _doc(tmp_path, "# H\n\n> **Aaron:** 这是一段中文根评论\n>> **Sage:** 这也是中文回复\n")
    t = _query(tmp_path, capsys)["threads"][0]
    z1 = t["token_str"]
    legacy = _encode_legacy(t["token"])
    assert z1.startswith("z1.")
    assert len(z1) < len(legacy)


def test_three_token_transports_all_accepted(tmp_path, capsys):
    """Inline JSON, z1 compressed, and legacy base64 all accepted by write commands."""
    _doc(tmp_path)
    _start(tmp_path)
    tid = capsys.readouterr().out.strip()
    t = _query(tmp_path, capsys)["threads"][0]
    # z1 (current format from query)
    assert (
        run_discuss(
            tmp_path,
            [
                "reply",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                t["token_str"],
                "--speaker",
                "Sage",
                "z1回复",
            ],
        )
        == 0
    )
    capsys.readouterr()
    # legacy uncompressed base64
    t = _query(tmp_path, capsys)["threads"][0]
    assert (
        run_discuss(
            tmp_path,
            [
                "reply",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                _encode_legacy(t["token"]),
                "--speaker",
                "Sage",
                "legacy回复",
            ],
        )
        == 0
    )
    capsys.readouterr()
    # inline JSON
    t = _query(tmp_path, capsys)["threads"][0]
    assert (
        run_discuss(
            tmp_path,
            [
                "reply",
                "--file",
                "story.md",
                "--thread-id",
                tid,
                "--token",
                json.dumps(t["token"]),
                "--speaker",
                "Sage",
                "json回复",
            ],
        )
        == 0
    )


def test_malformed_z1_token_fail_closed(tmp_path, capsys):
    """Corrupted z1. prefix -> rc=1, no traceback, no file write."""
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(
        tmp_path,
        [
            "reply",
            "--file",
            "story.md",
            "--thread-id",
            "T-001",
            "--token",
            "z1.!!!garbage!!!",
            "--speaker",
            "Sage",
            "x",
        ],
    )
    assert rc == 1
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before
    assert "malformed" in capsys.readouterr().err.lower()


# -- inbox: union / dedup / doc order / compact / irrelevant exclusion -------------


def test_inbox_union_unanswered_and_awaiting(tmp_path, capsys):
    """--inbox returns union of unanswered + awaiting_my_reply, compact, doc order."""
    body = (
        "# H\n\n"
        "> **Aaron:** root-a\n"  # T-001 unanswered (Aaron)
        "> **Bob:** root-b\n"  # T-002 unanswered (Bob)
        ">> **Sage:** @Aaron please check\n"  # awaiting Aaron
        "> **Cara:** root-c (resolved by self)\n"
    )  # T-003 resolved
    _doc(tmp_path, body)
    q = _query(tmp_path, capsys, "--inbox", "Aaron")
    ids = [t["thread_id"] for t in q["threads"]]
    assert ids == ["T-001", "T-002"]  # T-001 unanswered, T-002 awaiting; T-003 excluded
    assert q["unanswered"] == ["T-001"]
    assert q["awaiting_my_reply"] == ["T-002"]
    assert q["unresolved"] == ["T-001"]
    for t in q["threads"]:
        assert "token" not in t and "token_str" in t


def test_inbox_includes_unresolved(tmp_path, capsys):
    """--inbox includes unresolved (open/reopen) threads initiated by the speaker."""
    body = (
        "# H\n\n"
        "> **Aaron:** root-a\n"  # T-001 open
        "> **Bob:** root-b (resolved)\n"
    )  # T-002 resolved
    _doc(tmp_path, body)
    q = _query(tmp_path, capsys, "--inbox", "Aaron")
    ids = [t["thread_id"] for t in q["threads"]]
    assert ids == ["T-001"]
    assert q["unresolved"] == ["T-001"]


def test_inbox_no_duplicates(tmp_path, capsys):
    """A thread matching multiple categories appears only once in threads list."""
    body = (
        "# H\n\n"
        "> **Aaron:** root\n"  # unanswered + unresolved
        ">> **Sage:** @Aaron please\n"
    )  # awaiting (matches same thread)
    _doc(tmp_path, body)
    q = _query(tmp_path, capsys, "--inbox", "Aaron")
    ids = [t["thread_id"] for t in q["threads"]]
    assert ids == ["T-001"]  # no dup despite 3 categories matching


def test_inbox_excludes_irrelevant_threads(tmp_path, capsys):
    """Threads not involving the speaker are excluded."""
    body = "# H\n\n> **Aaron:** root-a\n> **Bob:** root-b\n>> **Cara:** @Bob check\n"
    _doc(tmp_path, body)
    q = _query(tmp_path, capsys, "--inbox", "Aaron")
    ids = [t["thread_id"] for t in q["threads"]]
    assert ids == ["T-001"]  # only Aaron's; T-002 involves Bob/Cara, not Aaron


def test_inbox_compact_shape_no_token_dict(tmp_path, capsys):
    """--inbox auto-compact: no token dict or location fields in threads or comments."""
    body = "# H\n\n> **Aaron:** root\n>> **Sage:** @Aaron check\n"
    _doc(tmp_path, body)
    q = _query(tmp_path, capsys, "--inbox", "Aaron")
    t = q["threads"][0]
    assert "token" not in t
    for f in ("total_lines", "anchor_line", "anchor_text", "root_line", "root_text"):
        assert f not in t
    assert "token_str" in t
    assert "token" not in t["root"]


def test_inbox_and_blocker_mutually_exclusive(tmp_path, capsys):
    _doc(tmp_path)
    rc = run_discuss(
        tmp_path, ["query", "--file", "story.md", "--inbox", "Aaron", "--blocker", "Aaron"]
    )
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().err.lower()


# -- thread-id: single / unknown / compact -----------------------------------------


def test_thread_id_returns_single_thread_compact(tmp_path, capsys):
    body = "# H\n\n> **Aaron:** root-a\n> **Bob:** root-b\n"
    _doc(tmp_path, body)
    q = _query(tmp_path, capsys, "--thread-id", "T-002")
    assert len(q["threads"]) == 1
    assert q["threads"][0]["thread_id"] == "T-002"
    assert "token" not in q["threads"][0]
    assert "token_str" in q["threads"][0]


def test_thread_id_unknown_fail_clean(tmp_path, capsys):
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n")
    rc = run_discuss(tmp_path, ["query", "--file", "story.md", "--thread-id", "T-999"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_thread_id_with_check_ready(tmp_path, capsys):
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n")
    q = _query(tmp_path, capsys, "--thread-id", "T-001", "--check-ready")
    assert q["threads"][0]["thread_id"] == "T-001"
    assert "is_ready" in q and "ready_blockers" in q


# -- summary-only: no threads/body/token -------------------------------------------


def test_summary_only_no_threads(tmp_path, capsys):
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n")
    q = _query(tmp_path, capsys, "--check-ready", "--summary-only")
    assert "threads" not in q
    assert "is_ready" in q
    assert "ready_blockers" in q


def test_summary_only_without_check_ready_fails(tmp_path, capsys):
    _doc(tmp_path)
    rc = run_discuss(tmp_path, ["query", "--file", "story.md", "--summary-only"])
    assert rc == 1
    assert "requires --check-ready" in capsys.readouterr().err.lower()


# -- mutual exclusivity ------------------------------------------------------------


def test_thread_id_and_inbox_mutually_exclusive(tmp_path, capsys):
    _doc(tmp_path)
    rc = run_discuss(
        tmp_path, ["query", "--file", "story.md", "--thread-id", "T-001", "--inbox", "Aaron"]
    )
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().err.lower()


def test_summary_only_and_thread_id_mutually_exclusive(tmp_path, capsys):
    _doc(tmp_path)
    rc = run_discuss(
        tmp_path,
        ["query", "--file", "story.md", "--summary-only", "--check-ready", "--thread-id", "T-001"],
    )
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().err.lower()


def test_summary_only_and_inbox_mutually_exclusive(tmp_path, capsys):
    _doc(tmp_path)
    rc = run_discuss(
        tmp_path,
        ["query", "--file", "story.md", "--summary-only", "--check-ready", "--inbox", "Aaron"],
    )
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().err.lower()


# -- default full compatibility ----------------------------------------------------


def test_default_full_query_unchanged(tmp_path, capsys):
    """Default query still returns full output with token dict and location fields."""
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** reply\n")
    q = _query(tmp_path, capsys)
    t = q["threads"][0]
    assert "token" in t and "token_str" in t
    for f in ("total_lines", "anchor_line", "anchor_text", "root_line", "root_text"):
        assert f in t
    sage = t["root"]["children"][0]
    assert "token" in sage
