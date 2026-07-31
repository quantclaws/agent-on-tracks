"""check_trace — FR-0170 AC<->FR bidirectional coverage (AC-FR0170-01..04).

Ground truth per TP-003 §3a: expectations hardcoded on the fixture side (never
computed by the implementation under test). Covers: full-coverage pass, forward
orphans (missing section / section without AC), reverse orphans (AC pointing at
a missing spec item), no false positives from discussion blocks and fenced
code, the complete non-short-circuiting orphan list with stable order, and the
always-on wiring (validate_document / trac validate on acceptance.md).
"""
from tracks.cli.main import cmd_validate
from tracks.executor.validate import check_trace, check_trace_file, validate_document

# Spec fixture line numbers (hardcoded ground truth):
#   line 1  ### FR-0010    line 9  ### NFR-0020
SPEC = """### FR-0010 功能甲

- [x] 已决定
- **来源**：BS-01
- **交付入口**：trac x

描述。

### NFR-0020 质量乙

- [x] 已决定
- **来源**：BS-02

描述。
"""

ACC_FULL = """## FR-0010 功能甲

### AC-FR0010-01 已覆盖

- [ ] 已确认

## NFR-0020 质量乙

### AC-NFR0020-01 已覆盖

- [ ] 已确认
"""


def test_full_coverage_passes():
    assert check_trace(SPEC, ACC_FULL) == []


def test_forward_orphan_missing_section():
    acc = "## FR-0010 功能甲\n\n### AC-FR0010-01 已覆盖\n"
    assert check_trace(SPEC, acc) == [
        "line:9 NFR-0020 has no '## NFR-0020' section in acceptance"
    ]


def test_forward_orphan_section_without_ac():
    acc = ACC_FULL.replace("### AC-NFR0020-01 已覆盖\n\n- [ ] 已确认\n", "待补。\n")
    assert check_trace(SPEC, acc) == [
        "line:9 NFR-0020 acceptance section has no AC item for it"
    ]


def test_forward_orphan_section_ac_for_other_item_does_not_cover():
    # a section whose only AC back-references another item covers nothing
    acc = ACC_FULL.replace("AC-NFR0020-01", "AC-FR0010-02")
    issues = check_trace(SPEC, acc)
    assert "line:9 NFR-0020 acceptance section has no AC item for it" in issues


def test_reverse_orphan_ac_refers_missing_item():
    acc = ACC_FULL + "\n## FR-0999 幽灵\n\n### AC-FR0999-01 幽灵\n"
    assert check_trace(SPEC, acc) == [
        "line:15 AC-FR0999-01 refers to missing FR-0999 in spec"
    ]


def test_discussion_blocks_and_fenced_code_ignored():
    acc = (ACC_FULL
           + "\n> ## FR-0300 假章节\n> ### AC-FR0300-01 讨论块内\n"
           + "\n```\n## FR-0400 假章节\n### AC-FR0400-01 代码块内\n```\n")
    spec = SPEC + "\n```\n### FR-0500 代码块内假条目\n```\n"
    assert check_trace(spec, acc) == []


def test_complete_orphan_list_stable_order():
    # forward orphans in spec order first, then reverse orphans in acceptance
    # order — all reported at once (no short-circuit).
    acc = "## FR-0777 无中生有\n\n### AC-FR0777-01 无中生有\n"
    assert check_trace(SPEC, acc) == [
        "line:1 FR-0010 has no '## FR-0010' section in acceptance",
        "line:9 NFR-0020 has no '## NFR-0020' section in acceptance",
        "line:3 AC-FR0777-01 refers to missing FR-0777 in spec",
    ]


# -- wiring: always-on for acceptance.md (AC-FR0170-04) ------------------------

_ACC_HEAD = "---\nacc_id: ACC-X\ncreated: 2026-07-31\nstatus: draft\nsha:\n---\n\n"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_validate_document_requires_sibling_spec(tmp_path):
    p = _write(tmp_path, "acceptance.md", _ACC_HEAD + ACC_FULL)
    assert validate_document(p, "acceptance.md") == (
        "trace", "line:1 acceptance validate requires spec.md in same dir")


def test_validate_document_trace_runs_without_checks(tmp_path):
    _write(tmp_path, "spec.md", SPEC)
    p = _write(tmp_path, "acceptance.md", _ACC_HEAD + "# 验收\n\n无覆盖。\n")
    check, reason = validate_document(p, "acceptance.md", checks=[])
    assert check == "trace"
    assert "FR-0010" in reason and "NFR-0020" in reason


def test_validate_document_trace_pass(tmp_path):
    _write(tmp_path, "spec.md", SPEC)
    p = _write(tmp_path, "acceptance.md", _ACC_HEAD + ACC_FULL)
    assert validate_document(p, "acceptance.md") is None


def test_check_trace_file_reads_sibling_spec(tmp_path):
    _write(tmp_path, "spec.md", SPEC)
    p = _write(tmp_path, "acceptance.md", ACC_FULL)
    assert check_trace_file(p) == []


def test_cli_validate_acceptance_runs_trace(tmp_path, capsys):
    _write(tmp_path, "spec.md", SPEC)
    p = _write(tmp_path, "acceptance.md",
               _ACC_HEAD + "# 验收\n\n## FR-0010 功能甲\n\n### AC-FR0010-01 覆盖\n")
    assert cmd_validate(tmp_path, "--file", str(p)) == 1
    assert "NFR-0020" in capsys.readouterr().err


def test_cli_validate_acceptance_trace_pass(tmp_path, capsys):
    _write(tmp_path, "spec.md", SPEC)
    p = _write(tmp_path, "acceptance.md", _ACC_HEAD + "# 验收\n\n" + ACC_FULL)
    assert cmd_validate(tmp_path, "--file", str(p)) == 0
    assert "valid" in capsys.readouterr().out
