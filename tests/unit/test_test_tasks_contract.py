"""D-32/D-28 test-task contract: test-plan §8 template, parser, and the
M-DESIGN EXIT structured contract validator (fail-closed M-DESIGN→M-TEST).

The template carries the machine-readable ``## 8. AC Coverage`` table; the
M-DESIGN EXIT gate (checks=["test_tasks"]) demands every acceptance AC has a
row and every integration/e2e AC carries registered IF- ids, so an invalid
contract never reaches Shield.
"""

from tracks import templating
from tracks.effects.backend import valid_test_tasks
from tracks.executor.validate import (
    check_test_tasks,
    check_test_tasks_contract_file,
)

ACC_OK = """## FR-0010 Feature

### AC-FR0010-01

  - cond

### AC-FR0010-02

  - cond

### AC-FR0010-03

  - cond
"""

PLAN_VALID = """## 8. AC Coverage

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0010-01（一） | unit + integration | test_a | IF-MTEST-001 |
| AC-FR0010-02（二） | integration + e2e | test_b | IF-MTEST-001, IF-SHIELD-001 |
| AC-FR0010-03（三） | unit | test_c | IF-TRACE-001 |
"""

REGISTRY = {"IF-MTEST-001", "IF-SHIELD-001", "IF-TRACE-001"}


# -- template: canonical §8 present with the required columns ---------------


def test_template_carries_machine_readable_coverage_section():
    tpl = templating.load_template("test-plan")
    assert "## 8. AC Coverage" in tpl
    assert "| AC id | layer | test | IF |" in tpl
    for header in ("AC id", "layer", "test", "IF"):
        assert header in tpl


def test_template_coverage_semantics_pinned():
    # the template documents the contract semantics (item 1)
    tpl = templating.load_template("test-plan")
    assert "unit" in tpl and "integration" in tpl and "e2e" in tpl
    assert "IF-" in tpl
    assert "interfaces.md" in tpl
    # no leftover template *guidance blockquote* markers in the new §8
    assert "**When needed**" not in tpl.split("## 8. AC Coverage")[1]


# -- check_test_tasks contract validator -------------------------------------


def test_contract_valid_plan_passes():
    assert check_test_tasks(ACC_OK, PLAN_VALID, REGISTRY) == []


def test_contract_missing_section_fails():
    plan = "## 7. CI Gate\n\n- check\n"
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("## 8. AC Coverage" in i for i in issues)


def test_contract_missing_ac_row_fails():
    plan = PLAN_VALID.replace("| AC-FR0010-03（三） | unit | test_c | IF-TRACE-001 |\n", "")
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("missing row" in i and "AC-FR0010-03" in i for i in issues)


def test_contract_integration_ac_without_if_fails():
    plan = PLAN_VALID.replace("IF-MTEST-001, IF-SHIELD-001", "")
    plan = plan.replace("||", "| |")  # keep the empty IF cell parseable
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("missing IF-" in i and "AC-FR0010-02" in i for i in issues)


def test_contract_unknown_ac_fails():
    plan = PLAN_VALID.replace("AC-FR0010-03（三）", "AC-FR9999-01（幽灵）")
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("unknown AC" in i and "AC-FR9999-01" in i for i in issues)


def test_contract_unregistered_if_fails():
    plan = PLAN_VALID.replace("IF-SHIELD-001", "IF-FAKE-999")
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("IF-FAKE-999 not registered" in i for i in issues)


def test_contract_unit_only_rejected_at_m_design_exit():
    # Item 2 (BLOCKER): an all-unit plan has no Shield test task. M-DESIGN
    # EXIT must reject it so Shield is never dispatched with nothing to do
    # (no rollback loop).
    plan = """## 8. AC Coverage

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0010-01（一） | unit | a1 | - |
| AC-FR0010-02（二） | unit | a2 | - |
| AC-FR0010-03（三） | unit | a3 | - |
"""
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("no valid Shield test task" in i for i in issues), (
        "all-unit plan must be rejected at M-DESIGN EXIT: " + str(issues)
    )


def test_contract_no_valid_shield_task_when_required_fails():
    # M-TEST needs Shield tasks (integration/e2e exists) but none is valid
    plan = """## 8. AC Coverage

| AC id | layer | unit | IF |
| --- | --- | --- | --- |
| AC-FR0010-01（一） | integration | a1 | |
"""
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("missing IF-" in i for i in issues)
    assert any("no valid Shield test task" in i for i in issues)


def test_contract_file_variant(tmp_path):
    # file-level contract (as called by the M-DESIGN EXIT validate_document)
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text(
        "## 5. IF Registry\n\n### IF-MTEST-001\n\n### IF-SHIELD-001\n", encoding="utf-8"
    )
    from tracks.executor.validate import check_test_tasks_contract_file

    assert check_test_tasks_contract_file(plan) == []


# -- shared validator: valid_test_tasks ---------------------------------------


def test_shared_validator_agrees_with_fake():
    ok = [{"ac_id": "AC-FR0010-01", "layers": ["integration"], "if_ids": ["IF-MTEST-001"]}]
    assert valid_test_tasks(ok)
    for bad in (
        None,
        {},
        [],
        [{"ac_id": "AC-FR0010-01", "layers": [], "if_ids": ["IF-MTEST-001"]}],
        [{"ac_id": "AC-FR0010-01", "layers": ["unit"], "if_ids": ["IF-MTEST-001"]}],
        [{"ac_id": "AC-FR0010-01", "layers": ["integration"], "if_ids": []}],
        [{"ac_id": "bogus", "layers": ["integration"], "if_ids": ["IF-MTEST-001"]}],
        [{"ac_id": "AC-FR0010-01", "layers": ["integration"], "if_ids": ["IF-"]}],
    ):
        assert valid_test_tasks(bad) is False


# -- §2f (issue #33): inline-discussion blockquote lines are not plan content --


def test_visible_plan_lines_excludes_discussion_blockquotes():
    """§2f：blockquote（内联讨论线程）不是 plan 内容，不参与任何扫描。"""
    from tracks.executor.test_tasks import _visible_plan_lines

    text = (
        "## 8. AC Coverage\n"
        "\n"
        "| AC id | layer | test | IF |\n"
        "|---|---|---|---|\n"
        "| AC-FR0010-01（一） | unit + integration | test_a | IF-MTEST-001 |\n"
        "\n"
        "> **Prism:** [PRISM-V06-R14-02][blocker] 讨论线程正文\n"
        ">> **Shield:** 回复行，含 IF-FAKE-999 与 layer 词 integration\n"
        "   > 缩进变体的 blockquote 行\n"
        "\n"
        "plain prose line\n"
    )
    joined = "\n".join(_visible_plan_lines(text))
    # blockquote 行（含嵌套/缩进变体）不得进入可见行
    assert "PRISM-V06-R14-02" not in joined
    assert "IF-FAKE-999" not in joined
    assert "缩进变体" not in joined
    # §8 表格行与普通正文行保留
    assert (
        "| AC-FR0010-01（一） | unit + integration | test_a | IF-MTEST-001 |"
        in joined
    )
    assert "plain prose line" in joined


def test_visible_plan_lines_still_strips_fences_and_comments():
    """既有行为不回归：HTML 注释与围栏代码块仍然剥离。"""
    from tracks.executor.test_tasks import _visible_plan_lines

    text = (
        "<!-- hidden comment IF-FAKE-001 -->\n"
        "```\n"
        "fenced code with | fake | table |\n"
        "```\n"
        "kept line\n"
    )
    joined = "\n".join(_visible_plan_lines(text))
    assert "IF-FAKE-001" not in joined
    assert "fake" not in joined
    assert "kept line" in joined


def test_contract_ignores_blockquote_rows_in_coverage_scan():
    """回归（r2 M-DESIGN EXIT 假失败）：§8 表格之后的讨论 blockquote 表格行
    不得被当作 coverage 行——unknown AC / unregistered IF 只由真实表格行触发。"""
    plan = PLAN_VALID + (
        "\n"
        "> **Prism:** [X][blocker] 讨论线程\n"
        "> | AC-FR9999-01（幽灵） | integration | ghost | IF-FAKE-999 |\n"
    )
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert not any("AC-FR9999-01" in i for i in issues), issues
    assert not any("IF-FAKE-999" in i for i in issues), issues
    assert issues == []


# -- template: interfaces.md §5 IF Registry (template, extraction, hard-fail) ----


def test_template_interfaces_has_if_registry_section():
    tpl = templating.load_template("interfaces")
    assert "## 5. IF Registry" in tpl


def test_extract_if_registry_only_from_section_5():
    from tracks.executor.validate import _extract_if_registry

    text = (
        "## 1. 跨模块合同\n\nIF-FAKE-001 should not be extracted\n\n"
        "## 5. IF Registry\n\n### IF-MTEST-001\n\n### IF-SHIELD-001\n"
    )
    assert _extract_if_registry(text) == {"IF-MTEST-001", "IF-SHIELD-001"}


def test_extract_if_registry_missing_section_5_returns_none():
    from tracks.executor.validate import _extract_if_registry

    text = "## 0. 延续性\n\nnone\n\n## 4. 可观察出口\n\nstuff\n"
    assert _extract_if_registry(text) is None


def test_check_test_tasks_contract_file_missing_interfaces_fails(tmp_path):
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    from tracks.executor.validate import check_test_tasks_contract_file

    issues = check_test_tasks_contract_file(plan)
    assert any("interfaces.md" in i for i in issues)


def test_check_test_tasks_contract_file_missing_section_5_fails(tmp_path):
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text(
        "---\ninterfaces_id: IF-NNN\nstatus: draft\nsha:\n---\n\n"
        "## 0. 延续性\n\nnone\n\n## 4. 可观察出口\n\nstuff\n",
        encoding="utf-8",
    )
    from tracks.executor.validate import check_test_tasks_contract_file

    issues = check_test_tasks_contract_file(plan)
    assert any("IF Registry" in i or "§5" in i for i in issues)


def test_check_test_tasks_contract_file_empty_registry_fails(tmp_path):
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text(
        "---\ninterfaces_id: IF-NNN\nstatus: draft\nsha:\n---\n\n"
        "## 5. IF Registry\n\n(no interfaces registered yet)\n",
        encoding="utf-8",
    )
    from tracks.executor.validate import check_test_tasks_contract_file

    issues = check_test_tasks_contract_file(plan)
    assert any("empty" in i.lower() or "no IF-" in i.lower() for i in issues)


def test_check_design_trace_file_missing_interfaces_fails(tmp_path):
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    from tracks.executor.validate import check_design_trace_file

    issues = check_design_trace_file(plan)
    assert any("interfaces.md" in i for i in issues)


def test_check_design_trace_file_missing_section_5_fails(tmp_path):
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text(
        "---\ninterfaces_id: IF-NNN\nstatus: draft\nsha:\n---\n\n"
        "## 0. 延续性\n\nnone\n\n## 4. 可观察出口\n\nstuff\n",
        encoding="utf-8",
    )
    from tracks.executor.validate import check_design_trace_file

    issues = check_design_trace_file(plan)
    assert any("IF Registry" in i or "§5" in i for i in issues)


# -- item 4: duplicate AC, canonical headers, non-empty test cell, v0.4 -------


def test_contract_duplicate_ac_row_fails():
    plan = PLAN_VALID.replace(
        "| AC-FR0010-01（一） | unit + integration | test_a | IF-MTEST-001 |\n",
        "| AC-FR0010-01（一） | unit + integration | test_a | IF-MTEST-001 |\n"
        "| AC-FR0010-01（一） | integration | test_b | IF-MTEST-001 |\n",
    )
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("duplicate" in i.lower() and "AC-FR0010-01" in i for i in issues)


def test_contract_empty_test_cell_fails():
    plan = """## 8. AC Coverage

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0010-01（一） | integration |  | IF-MTEST-001 |
| AC-FR0010-02（二） | integration | test_b | IF-MTEST-001 |
| AC-FR0010-03（三） | unit | test_c | - |
"""
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("empty" in i.lower() and "test" in i.lower() for i in issues)


def test_contract_non_canonical_header_rejected():
    plan = """## 8. AC Coverage

| AC id | layer | test_name | IF |
|---|---|---|---|
| AC-FR0010-01（一） | integration | test_a | IF-MTEST-001 |
| AC-FR0010-02（二） | integration | test_b | IF-MTEST-001 |
| AC-FR0010-03（三） | unit | test_c | - |
"""
    issues = check_test_tasks(ACC_OK, plan, REGISTRY)
    assert any("header" in i.lower() for i in issues), issues


# -- fail-closed: registry missing/empty/unregistered IF ---------------------


def test_if_registry_does_not_match_arbitrary_if_tokens():
    """IF- tokens outside §5 must NOT be treated as a registry (no fail-open)."""
    from tracks.executor.validate import _extract_if_registry

    text = (
        "## 1. 跨模块合同\n\nIF-FAKE-001 should not be extracted\n\n"
        "## 4. 可观察出口\n\nIF-OTHER-002 in prose\n"
    )
    assert _extract_if_registry(text) is None


def test_fail_closed_when_registry_missing(tmp_path):
    """Missing §5 registry must still fail-closed."""
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text("## 4. 可观察出口\n\nstuff\n", encoding="utf-8")

    issues = check_test_tasks_contract_file(plan)
    assert any("IF Registry" in i or "§5" in i for i in issues)


def test_fail_closed_when_registry_empty(tmp_path):
    """Empty §5 registry must still fail-closed."""
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(PLAN_VALID, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text("## 5. IF Registry\n\n(no interfaces yet)\n", encoding="utf-8")

    issues = check_test_tasks_contract_file(plan)
    assert any("empty" in i.lower() or "no IF-" in i.lower() for i in issues)


def test_fail_closed_when_if_unregistered(tmp_path):
    """An unregistered IF- must still fail-closed."""
    acc = tmp_path / "acceptance.md"
    acc.write_text(ACC_OK, encoding="utf-8")
    plan = tmp_path / "test-plan.md"
    plan.write_text(
        PLAN_VALID.replace("IF-MTEST-001", "IF-BOGUS-999"),
        encoding="utf-8",
    )
    iface = tmp_path / "interfaces.md"
    iface.write_text(
        "## 5. IF Registry\n\n### IF-MTEST-001\n\n### IF-SHIELD-001\n",
        encoding="utf-8",
    )

    issues = check_test_tasks_contract_file(plan)
    assert any("IF-BOGUS-999" in i for i in issues)


# -- template: example IF ids must not be counted as registry (fail-open) ----


def test_template_interfaces_example_not_counted_as_registry():
    """The default-copied interfaces template must NOT count its example
    `IF-EXAMPLE-001` as a registered IF- (no fail-open)."""
    tpl = templating.load_template("interfaces")
    from tracks.executor.validate import _extract_if_registry

    registry = _extract_if_registry(tpl)
    # A fresh template with only the example must be treated as empty or
    # missing — never as a registry containing IF-EXAMPLE-001.
    assert registry is None or "IF-EXAMPLE-001" not in registry, (
        "template example IF-EXAMPLE-001 must not be counted as registry"
    )
