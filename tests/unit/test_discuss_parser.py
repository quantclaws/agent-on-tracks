"""inline-discussion parser (FR-050/FR-060/FR-120, AC-FR0050-01..AC-FR0120-03)."""

from tracks.discuss.parser import parse_tag, parse_threads


def _one(text):
    threads = parse_threads(text)
    assert len(threads) == 1
    return threads[0]


# -- FR-050 syntax ------------------------------------------------------------


def test_parse_tag_three_layouts_equivalent():
    # AC-FR0050-01: colon-inside / colon-outside / no-bold give the same fields
    a = parse_tag("**Speaker:** hello")
    b = parse_tag("**Speaker**: hello")
    c = parse_tag("Speaker: hello")
    assert a == b == c == ("Speaker", None, "hello")


def test_root_status_inside_and_outside_bold():
    # AC-FR0050-02
    assert parse_tag("**Name [RESOLVED]:** x")[1] == "resolved"
    assert parse_tag("**Name** [RESOLVED]: x")[1] == "resolved"
    assert parse_tag("**Name [REOPEN]:** x")[1] == "reopen"


def test_reply_bracket_is_plain_text():
    # AC-FR0050-03: a reply's [open] must not override the root's resolved status
    t = _one("> **Aaron [RESOLVED]:** root\n>> **Sage:** 已修复 [open]")
    assert t.status == "resolved"
    assert t.last_speaker == "Sage"


def test_labels_and_plain_not_recognized():
    # AC-FR0050-04
    assert parse_threads("> **Note:** x") == []
    assert parse_threads("> 格式约定: y") == []
    assert parse_threads("> [!WARNING]") == []
    assert parse_threads("> **Name** no colon") == []


def test_at_speaker_tag_equivalent():
    assert parse_tag("**@Aaron:** hi")[0] == "Aaron"


# -- FR-060 data structure ----------------------------------------------------


def test_thread_id_autoincrement():
    # AC-FR0060-01
    ts = parse_threads("> **A:** one\n\ntext\n\n> **B:** two")
    assert [t.thread_id for t in ts] == ["T-001", "T-002"]


def test_five_tuple_recorded():
    # AC-FR0060-02
    t = _one("# Heading\n\n> **Aaron:** hello")
    assert t.total_lines == 3
    assert t.anchor_line == 1 and t.anchor_text == "# Heading"
    assert t.root_line == 3 and t.root_text == "> **Aaron:** hello"


def test_reply_count_last_speaker_mentions():
    t = _one("> **A:** hi @Bob\n>> **B:** reply1\n>> **C:** @Dave ok")
    assert t.initiator == "A"
    assert t.reply_count == 2
    assert t.last_speaker == "C"
    assert t.mentioned_agents == ("Bob", "Dave")  # deduped, ordered


def test_snippet_truncated_to_80():
    t = _one("> **A:** " + "x" * 120)
    assert len(t.snippet) == 80


# -- FR-120 parse boundary ----------------------------------------------------


def test_fenced_code_skipped():
    # AC-FR0120-01
    assert parse_threads("```\n> **Aaron:** in code\n```") == []


def test_plain_markdown_around_thread():
    # AC-FR0120-02
    t = _one("说明文字\n\n> **Aaron:** comment\n\n说明文字")
    assert t.initiator == "Aaron"


def test_anchor_is_nearest_non_blockquote_line():
    # AC-FR0120-03
    t = _one("first\nsecond\n\n> **Aaron:** x")
    assert t.anchor_text == "second" and t.anchor_line == 2


# -- FR-050 nesting (depth = reply to whom) -----------------------------------


def test_reply_tree_nesting():
    text = (
        "> **Aaron:** I don't know\n"
        ">> **Sage:** please revise line 5\n"
        ">>> **Scribe:** done\n"
        ">> **Aaron:** thanks\n"
    )
    t = _one(text)
    assert t.initiator == "Aaron" and t.reply_count == 3
    kids = t.root.children
    assert [c.speaker for c in kids] == ["Sage", "Aaron"]  # both depth 2
    assert all(c.depth == 2 for c in kids)
    # Scribe (depth 3) is a child of Sage, NOT the root
    assert [c.speaker for c in kids[0].children] == ["Scribe"]
    assert kids[0].children[0].depth == 3
    assert kids[1].children == ()  # Aaron's depth-2 reply has no child


def test_depth3_with_no_depth2_attaches_to_root():
    t = _one("> **Aaron:** root\n>>> **Sage:** deep\n")
    assert t.root.children[0].speaker == "Sage"
    assert t.root.children[0].depth == 3  # nearest shallower ancestor = root


def test_iter_comments_preorder():
    from tracks.discuss.model import iter_comments

    t = _one("> **A:** r\n>> **B:** b\n>>> **C:** c\n>> **D:** d\n")
    assert [c.speaker for c in iter_comments(t.root)] == ["A", "B", "C", "D"]
