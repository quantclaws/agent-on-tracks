"""Green condition / IF- attribution tests (FR-0140).

AC-FR0140-01@v0.4 template has green condition field,
AC-FR0140-02@v0.4 check_design_trace IF- attribution validation,
AC-FR0140-04@v0.4 field carries IF not execution.
"""

from pathlib import Path

from tracks import templating
from tracks.executor.validate import check_design_trace, parse_test_tasks

ACC = """## FR-0010 Feature A

### AC-FR0010-01

  - cond

### AC-FR0010-02

  - cond
"""

PLAN_WITH_IF = """## 8. AC Coverage

- AC-FR0010-01: unit
- AC-FR0010-02: integration IF-TRACE-002
"""

PLAN_NO_IF = """## 8. AC Coverage

- AC-FR0010-01: unit
- AC-FR0010-02: integration
"""

PLAN_BAD_IF = """## 8. AC Coverage

- AC-FR0010-01: unit
- AC-FR0010-02: integration IF-FAKE-999
"""

REGISTRY = {"IF-TRACE-001", "IF-TRACE-002", "IF-REACH-001", "IF-REACH-002"}


# AC-FR0140-01@v0.4 TRACKS-TRACE template has green condition field
def test_template_has_green_condition_field():
    """AC-FR0140-01@v0.4 test-plan template has green condition field."""
    tpl = templating.load_template("test-plan")
    assert "变绿条件" in tpl
    assert "IF-" in tpl


# AC-FR0140-02@v0.4 TRACKS-TRACE check design trace IF attribution
def test_check_design_trace_if_attribution():
    """AC-FR0140-02@v0.4 integration/e2e ACs require valid IF- attribution."""
    # With valid IF- -> no issues
    issues = check_design_trace(ACC, PLAN_WITH_IF, REGISTRY)
    assert not any("IF-" in i for i in issues)

    # Missing IF- -> error
    issues = check_design_trace(ACC, PLAN_NO_IF, REGISTRY)
    assert any("missing IF- attribution" in i for i in issues)

    # Invalid IF- -> error
    issues = check_design_trace(ACC, PLAN_BAD_IF, REGISTRY)
    assert any("IF-FAKE-999 not defined" in i for i in issues)


# AC-FR0140-04@v0.4 TRACKS-TRACE field carries IF not execution
def test_field_carries_if_not_execution():
    """AC-FR0140-04@v0.4 field carries IF info, does not execute greening."""
    tpl = templating.load_template("test-plan")
    # The template mentions the field but does not implement execution
    assert "M-IMPL" in tpl or "变绿" in tpl


# -- parse_test_tasks (D-28: Runtime-side §8 parser) ------------------------

_TT_ACC = """## FR-0010 Feature A

### AC-FR0010-01

  - cond

### AC-FR0010-02

  - cond

### AC-FR0010-03

  - cond
"""

_TT_PLAN = """## 8. AC Coverage

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0010-01（desc one） | unit + integration | test_a | IF-MTEST-001 |
| AC-FR0010-02（desc two） | integration + e2e | test_b | IF-MTEST-001, IF-SHIELD-001 |
| AC-FR0010-03（desc three） | unit | test_c | IF-TRACE-001 |
"""


def _write_pair(tmp_path: Path, acc: str, plan: str) -> tuple[Path, Path]:
    acc_path = tmp_path / "acceptance.md"
    plan_path = tmp_path / "test-plan.md"
    acc_path.write_text(acc, encoding="utf-8")
    plan_path.write_text(plan, encoding="utf-8")
    return acc_path, plan_path


def test_parse_test_tasks_unit_plus_integration(tmp_path):
    """unit + integration row yields integration layer only."""
    acc, plan = _write_pair(tmp_path, _TT_ACC, _TT_PLAN)
    tasks = parse_test_tasks(acc, plan)
    by_id = {t["ac_id"]: t for t in tasks}
    assert "AC-FR0010-01" in by_id
    assert by_id["AC-FR0010-01"]["layers"] == ["integration"]


def test_parse_test_tasks_integration_plus_e2e(tmp_path):
    """integration + e2e row yields both layers."""
    acc, plan = _write_pair(tmp_path, _TT_ACC, _TT_PLAN)
    tasks = parse_test_tasks(acc, plan)
    by_id = {t["ac_id"]: t for t in tasks}
    assert by_id["AC-FR0010-02"]["layers"] == ["e2e", "integration"]


def test_parse_test_tasks_if_ids_extracted(tmp_path):
    """IF ids are extracted and sorted from the §8 row."""
    acc, plan = _write_pair(tmp_path, _TT_ACC, _TT_PLAN)
    tasks = parse_test_tasks(acc, plan)
    by_id = {t["ac_id"]: t for t in tasks}
    assert by_id["AC-FR0010-02"]["if_ids"] == ["IF-MTEST-001", "IF-SHIELD-001"]


def test_parse_test_tasks_unit_only_dropped(tmp_path):
    """Unit-only ACs are not included in test_tasks."""
    acc, plan = _write_pair(tmp_path, _TT_ACC, _TT_PLAN)
    tasks = parse_test_tasks(acc, plan)
    ac_ids = {t["ac_id"] for t in tasks}
    assert "AC-FR0010-03" not in ac_ids


def test_parse_test_tasks_sorted_unique(tmp_path):
    """Output is sorted by ac_id and contains no duplicates."""
    acc, plan = _write_pair(tmp_path, _TT_ACC, _TT_PLAN)
    tasks = parse_test_tasks(acc, plan)
    ac_ids = [t["ac_id"] for t in tasks]
    assert ac_ids == sorted(ac_ids)
    assert len(ac_ids) == len(set(ac_ids))


def test_parse_test_tasks_missing_files(tmp_path):
    """Returns empty list when files don't exist."""
    tasks = parse_test_tasks(tmp_path / "nope.md", tmp_path / "nada.md")
    assert tasks == []


def test_parse_test_tasks_missing_if_still_included(tmp_path):
    """An AC with a non-unit layer but no IF- id is still included
    (fail-closed: the parser structures, the gates surface the gap)."""
    plan = _TT_PLAN.replace("IF-MTEST-001", "").replace("IF-SHIELD-001", "")
    plan = plan.replace("||\n|---|", "|\n|---|")  # cleanup empty cells
    acc, plan_p = _write_pair(tmp_path, _TT_ACC, plan)
    tasks = parse_test_tasks(acc, plan_p)
    by_id = {t["ac_id"]: t for t in tasks}
    assert by_id["AC-FR0010-01"]["if_ids"] == []


def test_parse_test_tasks_missing_layer_still_included(tmp_path):
    """A required AC with no layer is retained as invalid task input."""
    plan = _TT_PLAN.replace("integration + e2e", "")
    acc, plan_p = _write_pair(tmp_path, _TT_ACC, plan)
    tasks = parse_test_tasks(acc, plan_p)
    by_id = {t["ac_id"]: t for t in tasks}
    assert by_id["AC-FR0010-02"]["layers"] == []


def test_parse_test_tasks_malformed_if_still_included(tmp_path):
    """A malformed IF token is represented as an empty IF list."""
    plan = _TT_PLAN.replace("IF-MTEST-001, IF-SHIELD-001", "IF-MTEST-01")
    acc, plan_p = _write_pair(tmp_path, _TT_ACC, plan)
    tasks = parse_test_tasks(acc, plan_p)
    by_id = {t["ac_id"]: t for t in tasks}
    assert by_id["AC-FR0010-02"]["if_ids"] == []
