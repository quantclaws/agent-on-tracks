"""Deliverable consistency gate (FR-040/FR-130, AC-FR0130-03)."""
from tracks.cli.main import cmd_check
from tracks.deliverables import check_deliverables


# AC-FR0120-02@v0.4 TRACKS-TRACE real deliverables consistent
def test_real_deliverables_consistent():
    """AC-FR0120-02@v0.4: Shield.md in deliverables set, checked for
    existence + version + IQ alongside the other five agent prompts."""
    # Scribe/Sage agents + tracks-discuz skill ship with version 0.2
    assert check_deliverables() == []


def test_missing_deliverable(tmp_path):
    missing = tmp_path / "nope.md"
    assert check_deliverables(paths=[missing]) == [f"missing deliverable: {missing}"]


def test_missing_version(tmp_path):
    p = tmp_path / "Scribe.md"
    p.write_text("---\ndescription: x\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == [f"missing or malformed version in {p}"]


def test_malformed_version(tmp_path):
    p = tmp_path / "Sage.md"
    p.write_text("---\nversion: abc\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == [f"missing or malformed version in {p}"]


def test_wellformed_version(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nversion: 0.2\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == []


def test_missing_iq_in_agent(tmp_path, monkeypatch):
    import tracks.deliverables as d
    p = tmp_path / "Lex.md"
    p.write_text("---\nversion: 0.2\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(d, "AGENT_DELIVERABLES", (p,))
    assert check_deliverables(paths=[p]) == [f"missing or malformed IQ in {p}"]


def test_malformed_iq_in_agent(tmp_path, monkeypatch):
    import tracks.deliverables as d
    p = tmp_path / "Sage.md"
    p.write_text("---\nversion: 0.2\nIQ: Z\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(d, "AGENT_DELIVERABLES", (p,))
    assert check_deliverables(paths=[p]) == [f"missing or malformed IQ in {p}"]


def test_wellformed_iq_in_agent(tmp_path, monkeypatch):
    import tracks.deliverables as d
    p = tmp_path / "Scribe.md"
    p.write_text("---\nversion: 0.2\nIQ: A\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(d, "AGENT_DELIVERABLES", (p,))
    assert check_deliverables(paths=[p]) == []


def test_skill_exempt_from_iq(tmp_path):
    # SKILL.md is not an agent prompt: version only, no IQ required
    p = tmp_path / "SKILL.md"
    p.write_text("---\nversion: 0.2\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == []


def test_cli_check_deliverables(tmp_path, capsys):
    assert cmd_check(tmp_path, "deliverables") == 0
    assert "deliverables ok" in capsys.readouterr().out


def test_cli_check_usage(tmp_path, capsys):
    assert cmd_check(tmp_path, "bogus") == 1
    assert "usage:" in capsys.readouterr().err


# -- Scribe author-discussion contract (Task C) -------------------------------
# As author, Scribe must use --compact (all speakers) as its complete inbox,
# reply to every open/reopen thread by Human/Aaron/Sage (regardless of
# @Scribe), never resolve others' threads, and treat --inbox Scribe as a
# quick personal filter only.

def test_scribe_contract_author_uses_compact_not_inbox_as_complete_inbox():
    """DRAFT and RESPOND must instruct --compact (all speakers) as the first
    query command, not --inbox Scribe as the primary/complete inbox."""
    from tracks.deliverables import AGENT_DELIVERABLES
    scribe = next(p for p in AGENT_DELIVERABLES if p.name == "Scribe.md")
    text = scribe.read_text(encoding="utf-8")
    # --compact must appear in DRAFT and RESPOND as the complete-inbox query
    assert text.count("--compact") >= 3  # TRIAGE + DRAFT + RESPOND
    # --inbox Scribe must be qualified as quick/personal, not primary
    assert "仅可" in text or "仅" in text


def test_scribe_contract_must_reply_to_all_open_reopen_threads():
    """Scribe must reply to every open/reopen thread by Human/Aaron/Sage,
    regardless of @Scribe mention."""
    from tracks.deliverables import AGENT_DELIVERABLES
    scribe = next(p for p in AGENT_DELIVERABLES if p.name == "Scribe.md")
    text = scribe.read_text(encoding="utf-8")
    assert "open/reopen" in text
    assert "无论是否 @Scribe" in text


def test_scribe_contract_must_not_resolve_others_threads():
    """Scribe must not set-status resolved on threads initiated by others."""
    from tracks.deliverables import AGENT_DELIVERABLES
    scribe = next(p for p in AGENT_DELIVERABLES if p.name == "Scribe.md")
    text = scribe.read_text(encoding="utf-8")
    assert "不得代发起人 set-status resolved" in text


def test_scribe_contract_exit_via_check_ready_summary_only():
    """Exit check must use --check-ready --summary-only."""
    from tracks.deliverables import AGENT_DELIVERABLES
    scribe = next(p for p in AGENT_DELIVERABLES if p.name == "Scribe.md")
    text = scribe.read_text(encoding="utf-8")
    assert "--check-ready --summary-only" in text


def test_scribe_contract_editing_discipline_one_pass_no_mechanical_renumber():
    """DRAFT and RESPOND must enforce one-pass block replace and forbid
    mechanical renumber churn across multiple LLM steps."""
    from tracks.deliverables import AGENT_DELIVERABLES
    scribe = next(p for p in AGENT_DELIVERABLES if p.name == "Scribe.md")
    text = scribe.read_text(encoding="utf-8")
    # Editing discipline must appear in both DRAFT and RESPOND
    assert text.count("编辑纪律") >= 2
    assert "一次区块替换" in text
    assert "禁止" in text and "机械" in text
    assert "不得把机械编辑拆成多轮 LLM step" in text


def test_scribe_contract_open_reviewer_ruling_must_land():
    """Complete-story path must land open/reopen reviewer threads that carry a
    clear ruling/request (not only resolved threads), and must not modify body
    for still-unclarified open/reopen questions."""
    from tracks.deliverables import AGENT_DELIVERABLES
    scribe = next(p for p in AGENT_DELIVERABLES if p.name == "Scribe.md")
    text = scribe.read_text(encoding="utf-8")
    # Open reviewer ruling/request must be landed + replied
    assert "明确 ruling/request" in text
    assert "也必须落地" in text
    # Unclarified open/reopen: reply only, no body change
    assert "未澄清" in text
    assert "不擅自改正文" in text
