"""Template structure check (FR-150 'template', NFR-010 line:N).

check_template validates a document's required frontmatter fields and level-2
sections against its kind template; returns line:N issues ([] = valid). Isolated
here: wiring into validate_document(checks=[...]) / outcome / exit-gate is a
later increment.
"""
from pathlib import Path

from tracks import templating
from tracks.cli.main import cmd_validate
from tracks.executor.validate import check_template


def _story(tmp_path: Path) -> Path:
    p = tmp_path / "story.md"
    p.write_text(
        templating.render_story_skeleton("做一个X", "2026-07-31"), encoding="utf-8"
    )
    return p


def test_check_template_valid_story(tmp_path):
    assert check_template(_story(tmp_path)) == []


def test_check_template_extra_section_allowed(tmp_path):
    p = _story(tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + "\n## 目标\n\n额外章节。\n",
                 encoding="utf-8")
    assert check_template(p) == []


def test_check_template_missing_section(tmp_path):
    p = _story(tmp_path)
    p.write_text(
        p.read_text(encoding="utf-8").replace("## 5. 开放产品决定", "开放产品决定"),
        encoding="utf-8",
    )
    assert "line:1 missing section '开放产品决定'" in check_template(p)


def test_check_template_missing_frontmatter_field(tmp_path):
    p = _story(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("sha:\n", ""), encoding="utf-8")
    assert "line:1 missing frontmatter field 'sha'" in check_template(p)


def test_check_template_unknown_file(tmp_path):
    p = tmp_path / "foo.md"
    p.write_text("---\nx: y\n---\n\n# hi\n", encoding="utf-8")
    assert check_template(p) == ["line:1 no template mapping for 'foo.md'"]


def test_check_template_missing_file(tmp_path):
    assert check_template(tmp_path / "story.md") == ["line:1 missing file"]


def test_cli_validate_valid(tmp_path, capsys):
    p = _story(tmp_path)
    assert cmd_validate(tmp_path, "--file", str(p)) == 0
    assert "valid" in capsys.readouterr().out


def test_cli_validate_invalid_reports_line(tmp_path, capsys):
    p = _story(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("## 6. 必要性", "必要性"),
                 encoding="utf-8")
    assert cmd_validate(tmp_path, "--file", str(p)) == 1
    assert "line:1 missing section" in capsys.readouterr().err


def test_cli_validate_usage(tmp_path, capsys):
    assert cmd_validate(tmp_path, "story.md") == 1
    assert "usage:" in capsys.readouterr().err
