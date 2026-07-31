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
