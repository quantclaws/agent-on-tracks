"""Integration tests for ID grammar validation via trac validate (FR-0130).

AC-FR0130-01@v0.4 BS-XX grammar via CLI, AC-FR0130-05@v0.4 validate rejects bad grammar.
"""

from tracks import templating
from tracks.cli.main import cmd_validate

_BS_TEMPLATE = (
    "### BS-01 {行为种子标题}\n\n"
    "- EARS: `WHEN/IF/WHILE/WHERE {条件}, THE 系统 SHALL {用户可观察行为}`\n"
    "- 来源: [{路径编号} / 约束 / 非常规要求 / 重要推导]\n"
    "- 说明: {这项行为保护什么用户结果}"
)


# AC-FR0130-01@v0.4 TRACKS-TRACE validate story BS
# AC-FR0130-05@v0.4 TRACKS-TRACE validate story BS
def test_validate_story_bs(tmp_path, capsys):
    """AC-FR0130-01@v0.4 AC-FR0130-05@v0.4 trac validate --file story.md validates BS-XX."""
    p = tmp_path / "story.md"
    text = templating.render_story_skeleton("test req", "2026-08-05")
    text = text.replace(
        _BS_TEMPLATE,
        "### BS-01 Good behavior\n\n- 来源: [3.1]",
    )
    p.write_text(text, encoding="utf-8")
    assert cmd_validate(tmp_path, "--file", str(p)) == 0
    assert "valid" in capsys.readouterr().out


# AC-FR0130-05@v0.4 TRACKS-TRACE validate rejects bad grammar
def test_validate_rejects_bad_grammar(tmp_path, capsys):
    """AC-FR0130-05@v0.4 trac validate rejects bad BS-XX grammar."""
    p = tmp_path / "story.md"
    text = templating.render_story_skeleton("test req", "2026-08-05")
    text = text.replace(
        "### BS-01 {行为种子标题}",
        "### BS-1 Bad one-digit",
    )
    p.write_text(text, encoding="utf-8")
    assert cmd_validate(tmp_path, "--file", str(p)) == 1
    err = capsys.readouterr().err
    assert "bad item heading" in err


# AC-FR0130-02@v0.4 TRACKS-TRACE validate rejects duplicate BS
def test_validate_rejects_duplicate_bs(tmp_path, capsys):
    """AC-FR0130-02@v0.4 trac validate rejects duplicate BS IDs."""
    p = tmp_path / "story.md"
    text = templating.render_story_skeleton("test req", "2026-08-05")
    text = text.replace(
        _BS_TEMPLATE,
        "### BS-01 First\n\n- 来源: [3.1]\n\n### BS-01 Dup\n\n- 来源: [3.1]",
    )
    p.write_text(text, encoding="utf-8")
    assert cmd_validate(tmp_path, "--file", str(p)) == 1
    err = capsys.readouterr().err
    assert "duplicate id BS-01" in err
