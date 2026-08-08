"""Template structure check (FR-150 'template', NFR-010 line:N).

check_template validates a document's required frontmatter fields and level-2
sections against its kind template; returns line:N issues ([] = valid). Isolated
here: wiring into validate_document(checks=[...]) / outcome / exit-gate is a
later increment.
"""
import re
from pathlib import Path

from tracks import templating
from tracks.cli.main import USAGE, cmd_validate
from tracks.discuss.parser import parse_threads
from tracks.effects.fake import FakeBackend
from tracks.executor.validate import (
    TRAC_SUBCOMMANDS,
    _design_guidance_markers,
    check_design_trace,
    check_spec_items,
    check_template,
    is_discussion_diff,
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
    idx = text.rfind("## 5.")  # drop the last required section
    p.write_text(text[:idx], encoding="utf-8")
    issues = check_template(p)
    assert any("missing section" in i for i in issues)


def test_architecture_requires_scaffold_manifest_section(tmp_path):
    # batch B: the scaffold manifest is a REQUIRED architecture section — the
    # template check derives it from the template like every other section.
    p = _design_doc(tmp_path, "architecture", "architecture.md")
    text = p.read_text(encoding="utf-8")
    assert "Scaffold 宣言" in text  # the template carries the section
    p.write_text(text.replace("## 2. Scaffold 宣言", "Scaffold 宣言"),
                 encoding="utf-8")
    issues = check_template(p)
    assert "line:1 missing section 'Scaffold 宣言'" in issues
    assert validate_document(p, "architecture.md", ["template"]) is not None
    # the other design kinds have no scaffold requirement
    for kind in ("interfaces", "test-plan"):
        doc = _design_doc(tmp_path, kind, f"{kind}.md")
        assert "Scaffold 宣言" not in doc.read_text(encoding="utf-8")
        assert check_template(doc) == []


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
    (tmp_path / "interfaces.md").write_text(
        "---\ninterfaces_id: IF-NNN\nstatus: draft\nsha:\n---\n\n"
        "## 5. IF Registry\n\n### IF-MTEST-001\n",
        encoding="utf-8")
    _design_doc(tmp_path, "test-plan", "test-plan.md")
    monkeypatch.chdir(tmp_path)
    assert cmd_validate(Path("."), "--file", "test-plan.md") == 1  # orphan AC
    err = capsys.readouterr().err
    assert "AC-FR0010-01" in err and "layer attribution" in err


def test_cli_validate_test_plan_runs_test_tasks_contract(tmp_path, capsys, monkeypatch):
    """Item 3 (BLOCKER): `trac validate --file test-plan.md` must run both the
    design trace AND check_test_tasks_contract_file (FR-0140). A test-plan
    whose §8 passes design_trace (every AC has a layer) but fails the
    test-task contract (e.g. missing IF- attribution for integration layer)
    must return 1 with a test_tasks error."""
    (tmp_path / "acceptance.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n", encoding="utf-8")
    (tmp_path / "interfaces.md").write_text(
        "---\ninterfaces_id: IF-NNN\nstatus: draft\nsha:\n---\n\n"
        "## 5. IF Registry\n\n### IF-MTEST-001\n",
        encoding="utf-8")
    (tmp_path / "test-plan.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n# Plan\n\n"
        "## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0010-01 | integration | test_a | IF-FAKE-999 |\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cmd_validate(Path("."), "--file", "test-plan.md") == 1
    err = capsys.readouterr().err
    assert "IF-FAKE-999" in err or "test_tasks" in err


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


def test_fake_shield_missing_test_tasks_fails(tmp_path):
    backend = FakeBackend(tmp_path, "v0.4")

    result = backend.act("shield", "WRITE", None, None, {})

    assert result["status"] == "failed"
    assert result["failure_class"] == "invalid_test_tasks"
    assert not (tmp_path / "tests").exists()


def test_fake_design_architecture_carries_scaffold_manifest(tmp_path):
    # fake-backend parity (batch B): the deterministic Archer output keeps the
    # required Scaffold 宣言 section, and its manifest declares no real host
    # path (the fake writes nothing beyond the doc-set — no undeclared files).
    from tracks.effects.opencode import _scaffold_declared_paths
    vdir = tmp_path / ".tracks" / "projects" / "v0.1"
    vdir.mkdir(parents=True)
    (vdir / "acceptance.md").write_text(
        "---\nstatus: draft\nsha:\n---\n\n### AC-FR0010-01 x\n", encoding="utf-8")
    FakeBackend(tmp_path, "v0.1")._write_design("ok")
    text = (vdir / "architecture.md").read_text(encoding="utf-8")
    assert "Scaffold 宣言" in text
    assert _scaffold_declared_paths(text) <= {"{path}"}  # template placeholder


# -- run045: design docs must not invoke fabricated `trac` subcommands --------
# Fabricated tooling (`trac agent archer ci-scan`) passed all gates in live
# run045; the template check now rejects any `trac <token>` outside the real
# CLI surface.

def test_trac_subcommands_pin_parity_with_cli_usage():
    # parity pin: the command names spelled in tracks/cli/main.py USAGE must
    # equal TRAC_SUBCOMMANDS exactly — a new CLI command forces a set update.
    names = {
        m.group(1)
        for part in re.sub(r"<[^>]*>", "",
                           USAGE.removeprefix("usage: trac ")).split("|")
        if (m := re.match(r"([a-z][a-z-]*)(?=\s|$)", part.strip()))
    }
    assert names == set(TRAC_SUBCOMMANDS)


def test_design_doc_unknown_trac_subcommand_fails(tmp_path):
    # the invocation hides inside a code fence; it is still line-located
    p = _design_doc(tmp_path, "test-plan", "test-plan.md")
    text = p.read_text(encoding="utf-8")
    p.write_text(text + "\n```bash\ntrac agent archer ci-scan\n```\n",
                 encoding="utf-8")
    line_no = text.count("\n") + 3
    issues = check_template(p)
    assert any(i.startswith(f"line:{line_no} ")
               and "unknown trac subcommand 'agent'" in i for i in issues)


def test_design_doc_known_trac_subcommands_pass(tmp_path):
    p = _design_doc(tmp_path, "architecture", "architecture.md")
    p.write_text(p.read_text(encoding="utf-8")
                 + "\n自检用 `trac validate --file <path>`；"
                   "讨论用 `trac discuss start`。\n", encoding="utf-8")
    assert check_template(p) == []


def test_unknown_trac_subcommand_scoped_to_design_kinds(tmp_path):
    # the guard covers architecture/interfaces/test-plan only
    p = _story(tmp_path)
    p.write_text(p.read_text(encoding="utf-8") + "\ntrac frobnicate now\n",
                 encoding="utf-8")
    assert check_template(p) == []


def test_validate_document_fails_on_unknown_trac_subcommand(tmp_path):
    p = _design_doc(tmp_path, "interfaces", "interfaces.md")
    p.write_text(p.read_text(encoding="utf-8") + "\ntrac archive push\n",
                 encoding="utf-8")
    failure = validate_document(p, "interfaces.md", ["template"])
    assert failure is not None and failure[0] == "template"
    assert "unknown trac subcommand 'archive'" in failure[1]


# -- story legacy template compatibility ---------------------------------------
# A complete legacy-structured story (strong legacy signal = >=2 legacy-only
# level-2 headings: 工作项 / 分流建议 / 需求描述…) is validated against the
# legacy required-section set, not force-migrated to the latest template.

_LEGACY_FM = "---\nstory_id: S-005\ntitle: test\ncreated: 2026-01-01\nstatus: draft\nsha:\n---\n\n"
_LEGACY_SECTIONS = (
    "## 1. 原始输入\n\nraw\n\n"
    "## 2. 用户意图\n\nintent\n\n"
    "## 3. 需求描述（规划层，待 M-STORY 展开）\n\ndesc\n\n"
    "## 4. 工作项\n\nitems\n\n"
    "## 5. 开放产品决定\n\ndecisions\n\n"
    "## 6. 范围、约束与例外\n\nscope\n\n"
    "## 7. 分流建议\n\nGo\n"
)


def _legacy_story(tmp_path: Path) -> Path:
    p = tmp_path / "story.md"
    p.write_text(_LEGACY_FM + "# S-005: test\n\n" + _LEGACY_SECTIONS,
                 encoding="utf-8")
    return p


def test_legacy_story_passes_template_check(tmp_path):
    assert check_template(_legacy_story(tmp_path)) == []


def test_legacy_story_missing_required_section_fails(tmp_path):
    p = _legacy_story(tmp_path)
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("## 7. 分流建议", "分流建议"), encoding="utf-8")
    issues = check_template(p)
    assert "line:1 missing section '分流建议'" in issues


def test_legacy_story_missing_frontmatter_fails(tmp_path):
    p = _legacy_story(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("sha:\n", ""), encoding="utf-8")
    assert "line:1 missing frontmatter field 'sha'" in check_template(p)


def test_legacy_story_missing_demand_desc_fails(tmp_path):
    p = _legacy_story(tmp_path)
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("## 3. 需求描述（规划层，待 M-STORY 展开）",
                              "需求描述（规划层，待 M-STORY 展开）"), encoding="utf-8")
    assert "line:1 missing section '需求描述'" in check_template(p)


def test_latest_story_missing_required_section_still_fails(tmp_path):
    """Latest-template stories without legacy signal are strictly checked."""
    p = _story(tmp_path)
    p.write_text(
        p.read_text(encoding="utf-8").replace("## 7. 必要性与风险", "必要性与风险"),
        encoding="utf-8",
    )
    assert "line:1 missing section '必要性与风险'" in check_template(p)


def test_weak_legacy_signal_not_enough_single_section(tmp_path):
    """A single legacy-only heading (工作项 but no 分流建议/需求描述) is not a
    strong signal -> strict latest-template check applies -> fails on missing
    latest sections."""
    p = tmp_path / "story.md"
    p.write_text(
        _LEGACY_FM + "# S-NNN: test\n\n"
        "## 1. 原始输入\n\nraw\n\n"
        "## 2. 用户意图\n\nintent\n\n"
        "## 4. 工作项\n\nitems\n\n",
        encoding="utf-8",
    )
    issues = check_template(p)
    assert any("missing section '核心操作路径'" in i for i in issues)


# -- is_discussion_diff unit tests (item 1: canonical discussion-only) -------


def _setup_disc_repo(tmp_path: Path, base_text: str) -> tuple[Path, Path]:
    """Create a git repo, write base_text to doc, commit, return (repo, doc)."""
    import subprocess
    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=repo, check=True, capture_output=True)
    doc = repo / "doc.md"
    doc.write_text(base_text, encoding="utf-8")
    subprocess.run(["git", "add", "doc.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True,
                   capture_output=True)
    return repo, doc


_BASE_DOC = (
    "# Title\n\n"
    "## Body\n\n"
    "Some content here.\n\n"
    "> Raw blockquote from human input.\n"
)


def test_discussion_diff_pure_canonical_add_passes(tmp_path):
    """Adding a canonical discussion thread passes."""
    repo, doc = _setup_disc_repo(tmp_path, _BASE_DOC)
    doc.write_text(_BASE_DOC + "\n> **Sage:** Need more detail.\n",
                   encoding="utf-8")
    assert is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_modify_existing_canonical_thread_passes(tmp_path):
    """Modifying an existing canonical thread (e.g. resolve status) passes."""
    base = _BASE_DOC + "\n> **Sage [open]:** Need more detail.\n"
    repo, doc = _setup_disc_repo(tmp_path, base)
    doc.write_text(
        _BASE_DOC + "\n> **Sage [resolved]:** Need more detail.\n",
        encoding="utf-8")
    assert is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_modify_raw_blockquote_plus_canonical_rejects(tmp_path):
    """Modifying a raw blockquote AND adding canonical discussion is rejected."""
    repo, doc = _setup_disc_repo(tmp_path, _BASE_DOC)
    current = _BASE_DOC.replace(
        "> Raw blockquote from human input.",
        "> Modified raw blockquote.") + "\n> **Sage:** comment.\n"
    doc.write_text(current, encoding="utf-8")
    assert not is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_illegal_label_plus_canonical_rejects(tmp_path):
    """Adding a > note: label (non-canonical) + canonical discussion rejects."""
    repo, doc = _setup_disc_repo(tmp_path, _BASE_DOC)
    current = _BASE_DOC + "\n> note: this is a label.\n> **Sage:** comment.\n"
    doc.write_text(current, encoding="utf-8")
    assert not is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_body_text_change_rejects(tmp_path):
    """Changing body text (non-blockquote) is rejected."""
    repo, doc = _setup_disc_repo(tmp_path, _BASE_DOC)
    current = _BASE_DOC.replace("Some content here.", "Changed content.") \
        + "\n> **Sage:** comment.\n"
    doc.write_text(current, encoding="utf-8")
    assert not is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_fenced_code_quote_change_rejects(tmp_path):
    """Changing a > line inside a fenced code block is rejected (it's body)."""
    base = _BASE_DOC + "\n```python\n> print('hello')\n```\n"
    repo, doc = _setup_disc_repo(tmp_path, base)
    current = base.replace("> print('hello')", "> print('world')") \
        + "\n> **Sage:** comment.\n"
    doc.write_text(current, encoding="utf-8")
    assert not is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_no_diff_rejects(tmp_path):
    """No diff at all returns False."""
    repo, doc = _setup_disc_repo(tmp_path, _BASE_DOC)
    assert not is_discussion_diff(repo, doc, "HEAD")


def test_discussion_diff_only_raw_blockquote_added_rejects(tmp_path):
    """Adding only a raw (non-canonical) blockquote is rejected."""
    repo, doc = _setup_disc_repo(tmp_path, _BASE_DOC)
    current = _BASE_DOC + "\n> Another raw quote.\n"
    doc.write_text(current, encoding="utf-8")
    assert not is_discussion_diff(repo, doc, "HEAD")
