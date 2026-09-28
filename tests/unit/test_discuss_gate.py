"""Discussion gate (FR-100, AC-FR0100-01..03)."""

from tracks.discuss.gate import check_ready


def test_open_thread_blocks():
    # AC-FR0100-01
    ready, blockers = check_ready("# H\n\n> **Aaron:** open question")
    assert ready is False
    assert blockers == ("T-001",)


def test_reopen_thread_blocks():
    # AC-FR0100-02
    ready, blockers = check_ready("# H\n\n> **Aaron [REOPEN]:** again")
    assert ready is False and blockers == ("T-001",)


def test_all_resolved_is_ready():
    # AC-FR0100-03
    text = "# H\n\n> **Aaron [RESOLVED]:** q1\n\nmid\n\n> **Sage [RESOLVED]:** q2"
    ready, blockers = check_ready(text)
    assert ready is True and blockers == ()


def test_no_threads_is_ready():
    assert check_ready("# H\n\nplain doc") == (True, ())


def test_mixed_statuses_block():
    text = "# H\n\n> **A [RESOLVED]:** ok\n\nmid\n\n> **B:** still open"
    ready, blockers = check_ready(text)
    assert ready is False and blockers == ("T-002",)


# -- #210 OOB（2026-09-28）：resolved 的析取规则 --------------------------------


def _thread_doc():
    return (
        "anchor paragraph\n\n"
        "> **Prism:** root finding with a single adjudication request @Archer.\n"
        ">> **Archer:** done as asked.\n"
        "\n"
    )


def test_thread_initiator_helper():
    from tracks.discuss.gate import thread_initiator

    assert thread_initiator(_thread_doc(), "T-001") == "Prism"
    assert thread_initiator("no threads here", "T-001") is None


def test_resolved_accepts_adjudication_owner():
    """#210：根唯一 @mention 的裁决者可以收口（旧 FR-090 只认发起人，与
    CLI 的 FR-0314.4 合取成死锁——无人能关线程）。"""

    from tracks.discuss import writer
    from tracks.discuss.locate import token_for
    from tracks.discuss.parser import parse_threads

    text = _thread_doc()
    token = token_for(parse_threads(text)[0])
    out = writer.set_status(text, "T-001", token, "resolved", "Archer")
    assert "[RESOLVED]" in out


def test_resolved_initiator_of_requested_thread_surrenders():
    """AC-FR0314-04 test 2 语义：发起人请求裁决后让渡自决权——writer 侧同样拒绝。"""
    import pytest

    from tracks.discuss import writer
    from tracks.discuss.locate import token_for
    from tracks.discuss.parser import parse_threads

    text = _thread_doc()
    token = token_for(parse_threads(text)[0])
    with pytest.raises(writer.WriteError):
        writer.set_status(text, "T-001", token, "resolved", "Prism")


def test_resolved_unrequested_thread_accepts_initiator():
    from tracks.discuss import writer
    from tracks.discuss.locate import token_for
    from tracks.discuss.parser import parse_threads

    text = "anchor\n\n> **Prism:** root finding, no adjudication request.\n\n"
    token = token_for(parse_threads(text)[0])
    out = writer.set_status(text, "T-001", token, "resolved", "Prism")
    assert "[RESOLVED]" in out


def test_resolved_rejects_third_party():
    import pytest

    from tracks.discuss import writer
    from tracks.discuss.locate import token_for
    from tracks.discuss.parser import parse_threads

    text = _thread_doc()
    token = token_for(parse_threads(text)[0])
    with pytest.raises(writer.WriteError):
        writer.set_status(text, "T-001", token, "resolved", "Scribe")
