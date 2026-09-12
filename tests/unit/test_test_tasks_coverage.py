"""Behavior coverage for the test-task contract helpers (``test_tasks``).

Malformed-input and fail-closed branches of the §8 AC Coverage parser, the
hotfix unit-row extraction, the file-level design-trace/contract checks and
the Shield test-task issue shaping.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor import test_tasks as tt


def _plan(rows: str) -> str:
    return (
        "## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n" + rows
    )


def test_table_cells_without_pipe():
    assert tt._table_cells("no pipes here") == []
    assert tt._table_cells("| a | b |") == ["a", "b"]


def test_coverage_line_blockquote_skipped():
    in_cov, columns, rows = tt._coverage_line("> quoted thread", False, None)
    assert (in_cov, columns, rows) == (False, None, [])


def test_coverage_heading_state_level3_inside_section():
    assert tt._coverage_heading_state("### 8.1 sub", True) == (True, True)
    assert tt._coverage_heading_state("### 8.1 sub", False) == (False, False)


def test_is_canonical_header_short_list():
    assert tt._is_canonical_header(["AC id", "layer"]) is False


def test_find_coverage_headers_skips_quotes_and_separators():
    plan = (
        "## 8. AC Coverage\n\n"
        "> inline discussion\n"
        "|---|---|\n"
        "| AC id | layer | test | IF |\n"
        "| AC-FR0001-01 | unit | tests/unit/test_a.py | IF-IMPL-001 |\n"
    )
    assert tt._find_coverage_headers(plan) == [["AC id", "layer", "test", "IF"]]


def test_scan_line_for_empty_check_blockquote():
    assert tt._scan_line_for_empty_check("> x", True, 2, True) == (
        True,
        True,
        2,
        True,
    )


def test_parse_layer_cell_invalid_token():
    assert tt._parse_layer_cell("unit, foo") == (set(), False)
    assert tt._parse_layer_cell(None) == (set(), False)


def test_empty_test_task_and_missing_rows():
    assert tt._empty_test_task("AC-FR0001-01") == {
        "ac_id": "AC-FR0001-01",
        "layers": [],
        "if_ids": [],
    }
    assert tt._test_task_for_ac("AC-FR0001-01", []) == {
        "ac_id": "AC-FR0001-01",
        "layers": [],
        "if_ids": [],
    }


def test_coverage_row_items_with_test_non_adjacent_columns():
    cells = ["AC-FR0001-01", "unit", "tests/unit/test_a.py", "extra", "IF-IMPL-001"]
    rows = tt._coverage_row_items_with_test(
        "| " + " | ".join(cells) + " |", cells, (1, 4)
    )
    assert rows == [("AC-FR0001-01", "unit", None, "IF-IMPL-001")]


def test_coverage_heading_or_continuation_blockquote():
    assert tt._coverage_heading_or_continuation("> x", False, None) == (
        True,
        False,
        None,
    )


def test_parse_hotfix_unit_rows_shapes():
    plan = _plan(
        "| AC-FR0001-01@v0.5 |  | tests/unit/test_a.py::test_a | IF-IMPL-001 |\n"
        "| AC-FR0001-02@v0.5 | integration | tests/integration/test_b.py | IF-IMPL-001 |\n"
        "| AC-FR0001-03@v0.5 | unit+integration | tests/unit/test_c.py | IF-IMPL-001 |\n"
        "| AC-FR0001-04@v0.5 | unit | tests/unit/test_d.py::test_d | IF-IMPL-001 |\n"
    )
    assert tt.parse_hotfix_unit_rows(plan) == [
        {
            "ac": "AC-FR0001-04@v0.5",
            "if_ids": ["IF-IMPL-001"],
            "test": "tests/unit/test_d.py::test_d",
        }
    ]


def test_layer_of_unattributed_and_required_ac_ids_missing_files(tmp_path):
    assert tt._layer_of("AC-FR0001-01", ["AC-FR0001-01 appears with no layer"]) is None
    assert tt.required_ac_ids(tmp_path / "nope.md", tmp_path / "nope2.md") == set()


def test_extract_if_registry_stops_at_next_heading():
    text = (
        "## 5. IF Registry\n\n"
        "- IF-IMPL-001\n\n"
        "## 6. Next\n\n"
        "- IF-OTHER-999\n"
    )
    assert tt._extract_if_registry(text) == {"IF-IMPL-001"}


def test_design_trace_file_missing_docs(tmp_path):
    plan = tmp_path / "test-plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    assert tt.check_design_trace_file(plan) == [
        "line:1 test-plan validate requires acceptance.md in same dir"
    ]

    (tmp_path / "acceptance.md").write_text("# acc\n", encoding="utf-8")
    assert tt.check_design_trace_file(plan) == [
        "line:1 test-plan validate requires interfaces.md in same dir"
    ]


def test_design_trace_file_empty_registry(tmp_path):
    plan = tmp_path / "test-plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    (tmp_path / "acceptance.md").write_text("# acc\n", encoding="utf-8")
    (tmp_path / "interfaces.md").write_text(
        "## 5. IF Registry\n\nno identifiers\n", encoding="utf-8"
    )
    assert tt.check_design_trace_file(plan) == [
        "interfaces.md §5 IF Registry is empty (no IF- identifiers)"
    ]


def test_row_declares_shield_empty_cell():
    assert tt._row_declares_shield(None) is False
    assert tt._row_declares_shield("  ") is False
    assert tt._row_declares_shield("integration") is True


def test_shield_task_issues_shapes():
    assert tt._shield_task_issues("X", None, None) == []
    no_layers = tt._shield_task_issues("X", {"layers": [], "if_ids": []}, None)
    assert no_layers == ["AC X invalid layer (must be unit/integration/e2e)"]

    no_ifs = tt._shield_task_issues(
        "X", {"layers": ["integration"], "if_ids": []}, None
    )
    assert no_ifs == ["AC X missing IF- attribution for integration/e2e"]

    unregistered = tt._shield_task_issues(
        "X", {"layers": ["integration"], "if_ids": ["IF-Z-999"]}, set()
    )
    assert unregistered == ["IF-Z-999 not registered"]


def test_test_tasks_contract_file_missing_acceptance(tmp_path):
    plan = tmp_path / "test-plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    assert tt.check_test_tasks_contract_file(plan) == [
        "line:1 test-plan test_tasks validate requires acceptance.md"
    ]


def test_hotfix_coverage_ref_issues_flags_unversioned():
    hotfix_dir = Path("v0.5-hotfix-42")
    plan = _plan(
        "| AC-FR0001-01 | integration | tests/integration/test_a.py | IF-IMPL-001 |\n"
        "| AC-FR0001-02@v0.5 | integration | tests/integration/test_b.py | IF-IMPL-001 |\n"
    )
    issues = tt._hotfix_coverage_ref_issues(hotfix_dir / "test-plan.md", plan)
    assert issues == ["hotfix AC row must be versioned exactly: AC-FR0001-01"]


def test_parse_test_tasks_returns_empty_without_docs(tmp_path):
    assert tt.parse_test_tasks(tmp_path / "acc.md", tmp_path / "plan.md") == []
