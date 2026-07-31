"""trac discuss CLI (FR-080, AC-0801..0804/0609/0903/1104)."""
import json
from concurrent.futures import ThreadPoolExecutor

from tracks.discuss.cli import run_discuss
from tracks.discuss.parser import parse_threads


def _doc(tmp_path, body="# S-001: 标题\n\n说明文字。\n"):
    (tmp_path / "story.md").write_text(body, encoding="utf-8")
    return tmp_path


def _start(tmp_path, speaker="Aaron", msg="问题"):
    return run_discuss(tmp_path, ["start", "--file", "story.md", "--anchor-line", "1",
                                  "--speaker", speaker, msg])


def _query(tmp_path, capsys, *extra):
    assert run_discuss(tmp_path, ["query", "--file", "story.md", *extra]) == 0
    return json.loads(capsys.readouterr().out)


# -- AC-0801: five subcommands callable ---------------------------------------

def test_five_subcommands_callable(tmp_path, capsys):
    _doc(tmp_path)
    assert _start(tmp_path) == 0
    tid = capsys.readouterr().out.strip()
    q = _query(tmp_path, capsys)
    tok = json.dumps(q["threads"][0]["token"])
    assert run_discuss(tmp_path, ["reply", "--file", "story.md", "--thread-id", tid,
                                  "--token", tok, "--speaker", "Sage", "回复"]) == 0
    capsys.readouterr()
    q = _query(tmp_path, capsys)
    tok = json.dumps(q["threads"][0]["token"])
    assert run_discuss(tmp_path, ["edit", "--file", "story.md", "--thread-id", tid,
                                  "--token", tok, "--depth", "1", "--speaker", "Aaron",
                                  "改后"]) == 0
    capsys.readouterr()
    q = _query(tmp_path, capsys)
    tok = json.dumps(q["threads"][0]["token"])
    assert run_discuss(tmp_path, ["set-status", "--file", "story.md", "--thread-id", tid,
                                  "--token", tok, "--status", "resolved",
                                  "--operator", "Aaron"]) == 0


# -- AC-0802/0803/0804: query output ------------------------------------------

def test_query_json_has_five_tuple_and_token(tmp_path, capsys):
    # AC-0802
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    t = _query(tmp_path, capsys)["threads"][0]
    for field in ("total_lines", "anchor_line", "anchor_text", "root_line",
                  "root_text", "token"):
        assert field in t


def test_query_blocker_categories(tmp_path, capsys):
    # AC-0803
    _doc(tmp_path)
    _start(tmp_path)  # Aaron initiates an open, unreplied thread
    capsys.readouterr()
    q = _query(tmp_path, capsys, "--blocker", "Aaron")
    assert q["unanswered"] == ["T-001"]
    assert q["unresolved"] == ["T-001"]
    assert "awaiting_my_reply" in q


def test_query_check_ready(tmp_path, capsys):
    # AC-0804
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    q = _query(tmp_path, capsys, "--check-ready")
    assert q["is_ready"] is False and q["ready_blockers"] == ["T-001"]


# -- AC-0903: scope gate ------------------------------------------------------

def test_scope_gate_rejects_traversal(tmp_path, capsys):
    _doc(tmp_path)
    assert run_discuss(tmp_path, ["query", "--file", "../outside.md"]) == 1
    assert "scope" in capsys.readouterr().err


def test_scope_gate_rejects_external_absolute(tmp_path, capsys):
    _doc(tmp_path)
    assert run_discuss(tmp_path, ["query", "--file", "/etc/passwd"]) == 1


# -- AC-0609: missing token refused (fail closed) -----------------------------

def test_write_without_token_refused(tmp_path, capsys):
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    rc = run_discuss(tmp_path, ["reply", "--file", "story.md", "--thread-id", "T-001",
                                "--speaker", "Sage", "x"])  # no --token
    assert rc == 1


# -- AC-0608: stale token does not write --------------------------------------

def test_stale_token_leaves_file_unchanged(tmp_path, capsys):
    _doc(tmp_path)
    _start(tmp_path)
    capsys.readouterr()
    tok = json.dumps(_query(tmp_path, capsys)["threads"][0]["token"])
    # reorder: insert a thread before Aaron so Aaron drifts T-001 -> T-002
    _start(tmp_path, speaker="Zed", msg="插队")
    capsys.readouterr()
    before = (tmp_path / "story.md").read_text(encoding="utf-8")
    rc = run_discuss(tmp_path, ["reply", "--file", "story.md", "--thread-id", "T-001",
                                "--token", tok, "--speaker", "Sage", "x"])
    assert rc == 1
    assert (tmp_path / "story.md").read_text(encoding="utf-8") == before


def test_reply_to_comment_via_cli(tmp_path, capsys):
    # FR-050 nesting via CLI: reply to Sage -> depth-3 child under Sage
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** please revise\n"
                   ">> **Aaron:** thanks\n")
    t = _query(tmp_path, capsys)["threads"][0]
    thread_tok = json.dumps(t["token"])
    sage = next(c for c in t["root"]["children"] if c["speaker"] == "Sage")
    rc = run_discuss(tmp_path, ["reply", "--file", "story.md", "--thread-id",
                                t["thread_id"], "--token", thread_tok,
                                "--reply-to-token", json.dumps(sage["token"]),
                                "--speaker", "Scribe", "done"])
    assert rc == 0
    out = (tmp_path / "story.md").read_text(encoding="utf-8")
    assert ">> **Sage:** please revise\n>>> **Scribe:** done" in out


def test_blocker_awaiting_mention_without_child(tmp_path, capsys):
    # FR-050 orthogonal @mention: requests an answer; awaiting iff no child reply
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** @Scribe please revise\n")
    q = _query(tmp_path, capsys, "--blocker", "Scribe")
    assert q["awaiting_my_reply"] == ["T-001"]  # Sage @mentioned Scribe, unanswered


def test_blocker_awaiting_cleared_by_child_reply(tmp_path, capsys):
    _doc(tmp_path, "# H\n\n> **Aaron:** root\n>> **Sage:** @Scribe please revise\n"
                   ">>> **Scribe:** done\n")
    q = _query(tmp_path, capsys, "--blocker", "Scribe")
    assert q["awaiting_my_reply"] == []  # Scribe replied (child) -> no longer awaiting


# -- AC-1104: flock serializes concurrent writes ------------------------------

def test_concurrent_starts_no_lost_writes(tmp_path):
    _doc(tmp_path)

    def do_start(i):
        return run_discuss(tmp_path, ["start", "--file", "story.md", "--anchor-line", "1",
                                      "--speaker", f"U{i}", f"q{i}"])

    with ThreadPoolExecutor(max_workers=5) as ex:
        assert list(ex.map(do_start, range(5))) == [0] * 5
    text = (tmp_path / "story.md").read_text(encoding="utf-8")
    assert len(parse_threads(text)) == 5
