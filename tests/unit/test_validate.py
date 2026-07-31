"""Template structure check (FR-150 'template', NFR-010 line:N).

check_template validates a document's required frontmatter fields and level-2
sections against its kind template; returns line:N issues ([] = valid). Isolated
here: wiring into validate_document(checks=[...]) / outcome / exit-gate is a
later increment.
"""
from pathlib import Path

from tracks import templating
from tracks.cli.main import cmd_validate
from tracks.executor.validate import check_spec_items, check_template


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
        p.read_text(encoding="utf-8").replace("## 6. 开放产品决定", "开放产品决定"),
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
    p.write_text(
        p.read_text(encoding="utf-8").replace("## 7. 必要性与风险", "必要性与风险"),
        encoding="utf-8",
    )
    assert cmd_validate(tmp_path, "--file", str(p)) == 1
    assert "line:1 missing section" in capsys.readouterr().err


def test_cli_validate_usage(tmp_path, capsys):
    assert cmd_validate(tmp_path, "story.md") == 1
    assert "usage:" in capsys.readouterr().err


# -- FR-150 spec item lint (checkbox format) ----------------------------------

def test_check_spec_items_valid():
    text = ("### FR-0010 标题\n\n- [x] 已决定 — 依据\n- **来源**：BS-01\n"
            "- **交付入口**：trac init\n\n描述\n")
    assert check_spec_items(text) == []


def test_check_spec_items_missing_checkbox():
    text = "### FR-0010 标题\n\n- **来源**：BS-01\n- **交付入口**：x\n\n描述\n"
    assert any("已决定 checkbox" in i for i in check_spec_items(text))


def test_check_spec_items_fr_requires_delivery_entry():
    text = "### FR-0010 标题\n\n- [x] 已决定\n- **来源**：BS-01\n\n描述\n"
    assert any("交付入口" in i for i in check_spec_items(text))


def test_check_spec_items_nfr_no_delivery_entry():
    text = "### NFR-0010 标题\n\n- [x] 已决定\n- **来源**：BS-01\n\n描述\n"
    assert check_spec_items(text) == []


def test_check_spec_items_nfr_missing_checkbox_rejected():
    """AC-FR0150-03: a missing decided-checkbox line is a format error -> reject."""
    text = "### NFR-0010 错误信息含行号\n\n- **来源**：story §3.3\n\n描述\n"
    assert any("已决定 checkbox" in i for i in check_spec_items(text))


def test_check_spec_items_undecided_checkbox_rejected():
    """AC-FR0150-03: only YES means YES — '- [ ] 已决定' (undecided) is rejected."""
    text = "### NFR-0010 标题\n\n- [ ] 已决定\n- **来源**：BS-01\n\n描述\n"
    assert any("已决定 checkbox" in i for i in check_spec_items(text))


def test_check_spec_items_missing_source_rejected():
    """AC-FR0150-03: no '- **来源**：' field is a format error -> reject (Aaron)."""
    text = "### NFR-0010 错误信息含行号\n\n- [x] 已决定\n\n描述\n"
    assert any("来源" in i for i in check_spec_items(text))


def test_check_spec_items_old_status_format_rejected():
    """AC-FR0150-03: legacy '- **状态**：…已决定…' is NOT accepted (no compat)."""
    text = "### NFR-0010 标题\n\n- **状态**：有效·可测·已决定\n- **来源**：BS-01\n\n描述\n"
    issues = check_spec_items(text)
    assert any("已决定 checkbox" in i for i in issues)  # rejected, not defaulted


# -- FR-150 template check: acceptance skip + HTML comments -------------------

def test_check_template_acceptance_skips_section_names(tmp_path):
    # acceptance level-2 sections vary per FR/NFR -> no false 'missing section'
    p = tmp_path / "acceptance.md"
    p.write_text(
        "---\nacc_id: ACC-001\ncreated: 2026-01-01\nstatus: draft\nsha:\n---\n\n"
        "# 验收\n\n## FR-0010 agent 抽象\n\n### AC-FR0010-01\n\n- [ ] 已确认\n  - 条件\n",
        encoding="utf-8",
    )
    assert check_template(p) == []


def test_check_template_spec_conditional_sections_optional(tmp_path):
    # the spec template's conditional sections (界面与入口 …) are commented out,
    # so a spec without them is valid (HTML comments ignored).
    p = tmp_path / "spec.md"
    p.write_text(
        "---\nspec_id: SPEC-001\ncreated: 2026-01-01\nstatus: draft\nsha:\n---\n\n"
        "# 规格\n\n## 功能需求\n\n### FR-0010 标题\n\n- [x] 已决定\n"
        "- **来源**：BS-01\n- **交付入口**：x\n\n描述\n\n"
        "## 非功能需求\n\n### NFR-0010 标题\n\n- [x] 已决定\n- **来源**：BS-02\n\n描述\n",
        encoding="utf-8",
    )
    assert check_template(p) == []
