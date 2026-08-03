"""Template structure check (FR-150 'template', NFR-010 line:N).

check_template validates a document's required frontmatter fields and level-2
sections against its kind template; returns line:N issues ([] = valid). Isolated
here: wiring into validate_document(checks=[...]) / outcome / exit-gate is a
later increment.
"""
from pathlib import Path

from tracks import templating
from tracks.cli.main import cmd_validate
from tracks.discuss.parser import parse_threads
from tracks.effects.fake import FakeBackend
from tracks.executor.validate import (
    _design_guidance_markers,
    check_design_trace,
    check_spec_items,
    check_template,
    validate_document,
)
from tracks.kernel.machine import DESIGN_DOCS


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


# -- FR-150 spec item lint (metadata format) ----------------------------------

def test_check_spec_items_valid():
    text = ("### FR-0010 标题\n\n- **来源**：BS-01\n"
            "- **交付入口**：trac init\n\n描述\n")
    assert check_spec_items(text) == []


def test_check_spec_items_fr_requires_delivery_entry():
    text = "### FR-0010 标题\n\n- **来源**：BS-01\n\n描述\n"
    assert any("交付入口" in i for i in check_spec_items(text))


def test_check_spec_items_nfr_no_delivery_entry():
    text = "### NFR-0010 标题\n\n- **来源**：BS-01\n\n描述\n"
    assert check_spec_items(text) == []


def test_check_spec_items_missing_source_rejected():
    text = "### NFR-0010 错误信息含行号\n\n描述\n"
    assert any("来源" in i for i in check_spec_items(text))


# -- FR-150 template check: acceptance skip + HTML comments -------------------

def test_check_template_acceptance_skips_section_names(tmp_path):
    # acceptance level-2 sections vary per FR/NFR -> no false 'missing section'
    p = tmp_path / "acceptance.md"
    p.write_text(
        "---\nacc_id: ACC-001\ncreated: 2026-01-01\nstatus: draft\nsha:\n---\n\n"
        "# 验收\n\n## FR-0010 agent 抽象\n\n### AC-FR0010-01\n\n- 条件\n",
        encoding="utf-8",
    )
    assert check_template(p) == []


def test_check_template_spec_conditional_sections_optional(tmp_path):
    # the spec template's conditional sections (界面与入口 …) are commented out,
    # so a spec without them is valid (HTML comments ignored).
    p = tmp_path / "spec.md"
    p.write_text(
        "---\nspec_id: SPEC-001\ncreated: 2026-01-01\nstatus: draft\nsha:\n---\n\n"
        "# 规格\n\n## 功能需求\n\n### FR-0010 标题\n\n"
        "- **来源**：BS-01\n- **交付入口**：x\n\n描述\n\n"
        "## 非功能需求\n\n### NFR-0010 标题\n\n- **来源**：BS-02\n\n描述\n",
        encoding="utf-8",
    )
    assert check_template(p) == []


# -- v0.3 design trio: template kinds + BS-06 AC→layer trace ------------------

def _design_doc(tmp_path: Path, kind: str, name: str) -> Path:
    # template text with guidance comments stripped is structurally conformant
    import re
    p = tmp_path / name
    p.write_text(re.sub(r"<!--.*?-->", "", templating.load_template(kind),
                        flags=re.S), encoding="utf-8")
    return p


def test_design_kinds_template_conformant(tmp_path):
    # architecture/interfaces are registered kinds validated from their templates
    assert check_template(_design_doc(tmp_path, "architecture",
                                      "architecture.md")) == []
    assert check_template(_design_doc(tmp_path, "interfaces",
                                      "interfaces.md")) == []


def test_design_template_missing_section(tmp_path):
    p = _design_doc(tmp_path, "architecture", "architecture.md")
    text = p.read_text(encoding="utf-8")
    idx = text.rfind("## 4.")  # drop the last required section
    p.write_text(text[:idx], encoding="utf-8")
    issues = check_template(p)
    assert any("missing section" in i for i in issues)


def test_check_design_trace_covered_and_orphan():
    acc = ("---\nstatus: draft\nsha:\n---\n\n# acc\n\n## FR-0010 t\n\n"
           "### AC-FR0010-01 x\n\n### AC-FR0010-02 y\n")
    covered = "# plan\n\n- AC-FR0010-01: unit\n- AC-FR0010-02: e2e\n"
    assert check_design_trace(acc, covered) == []
    orphan = "# plan\n\n- AC-FR0010-01: unit\n"  # AC-FR0010-02 unattributed
    issues = check_design_trace(acc, orphan)
    assert len(issues) == 1 and "AC-FR0010-02" in issues[0]


def test_check_design_trace_requires_layer_token():
    acc = "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n"
    mentioned_no_layer = "# plan\n\nAC-FR0010-01 is planned.\n"
    assert check_design_trace(acc, mentioned_no_layer)  # id present, no layer


def test_validate_document_test_plan_trace_gated(tmp_path):
    # the design trace runs for test-plan.md only when 'trace' is requested
    (tmp_path / "acceptance.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n", encoding="utf-8")
    plan = _design_doc(tmp_path, "test-plan", "test-plan.md")
    template_only = validate_document(plan, "test-plan.md", ["template"])
    assert template_only is None  # no trace requested -> passes
    with_trace = validate_document(plan, "test-plan.md", ["template", "trace"])
    assert with_trace[0] == "trace"  # orphan AC reported


def test_cli_validate_test_plan_runs_design_trace(tmp_path, capsys, monkeypatch):
    (tmp_path / "acceptance.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n", encoding="utf-8")
    _design_doc(tmp_path, "test-plan", "test-plan.md")
    monkeypatch.chdir(tmp_path)
    assert cmd_validate(Path("."), "--file", "test-plan.md") == 1  # orphan AC
    err = capsys.readouterr().err
    assert "AC-FR0010-01" in err and "layer attribution" in err


# -- run044: design docs reserve blockquotes for discussion threads -----------
# Leftover template-guidance blockquotes (> **When needed**: ...) were misread
# as open inline-discussion threads, forcing a bogus revise -> escalation.

def test_design_guidance_markers_derived_from_templates():
    # single source: the '**Marker**:' lines in the design templates' guidance
    assert _design_guidance_markers() == {"When needed", "Lifecycle"}


def test_design_templates_carry_no_blockquotes():
    # blockquotes in delivered design docs are discussion threads, so the
    # templates must model that: no blockquote line, no parseable thread
    for kind in ("architecture", "interfaces", "test-plan"):
        text = templating.load_template(kind)
        assert not [ln for ln in text.splitlines()
                    if ln.lstrip().startswith(">")], kind
        assert parse_threads(text) == [], kind


def test_check_template_leftover_guidance_blockquote_fails(tmp_path):
    p = _design_doc(tmp_path, "test-plan", "test-plan.md")
    text = p.read_text(encoding="utf-8")
    idx = text.index("## 3. Ground Truth Method")
    p.write_text(text[:idx] + "> **When needed**: Required when ...\n\n" + text[idx:],
                 encoding="utf-8")
    issues = check_template(p)
    assert any(i.startswith("line:") and "template guidance blockquote left in doc"
               in i and "'When needed'" in i for i in issues)


def test_validate_document_leftover_guidance_failed(tmp_path):
    # the marker set is trio-wide: an architecture doc fails on Lifecycle too
    p = _design_doc(tmp_path, "architecture", "architecture.md")
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n> **Lifecycle**: This section is inherited.\n", encoding="utf-8")
    failure = validate_document(p, "architecture.md", ["template"])
    assert failure is not None and failure[0] == "template"
    assert "template guidance blockquote left in doc" in failure[1]
    assert "'Lifecycle'" in failure[1]


def test_check_template_guidance_comment_and_fence_pass(tmp_path):
    # guidance as HTML comment is the sanctioned vehicle; fenced examples too
    p = _design_doc(tmp_path, "test-plan", "test-plan.md")
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n<!-- Template guidance (delete before delivery):\n"
                   "  **When needed**: Required when ...\n-->\n\n"
                   "```text\n> **Lifecycle**: example\n```\n", encoding="utf-8")
    assert check_template(p) == []


def test_check_template_discussion_thread_is_not_guidance(tmp_path):
    p = _design_doc(tmp_path, "architecture", "architecture.md")
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n> **Prism [open]:** 模块边界能否再细化？\n", encoding="utf-8")
    assert check_template(p) == []  # thread blockquotes pass the template check


def test_fake_design_trio_has_no_blockquotes(tmp_path):
    # fake-backend parity: the deterministic trio stays guard-clean
    vdir = tmp_path / ".tracks" / "projects" / "v0.1"
    vdir.mkdir(parents=True)
    (vdir / "acceptance.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n", encoding="utf-8")
    FakeBackend(tmp_path, "v0.1")._write_design("ok")
    for name in DESIGN_DOCS:
        text = (vdir / name).read_text(encoding="utf-8")
        assert not [ln for ln in text.splitlines()
                    if ln.lstrip().startswith(">")], name
        assert check_template(vdir / name) == [], name
