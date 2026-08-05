"""Green condition / IF- attribution tests (FR-0140).

AC-FR0140-01@v0.4 template has green condition field,
AC-FR0140-02@v0.4 check_design_trace IF- attribution validation,
AC-FR0140-04@v0.4 field carries IF not execution.
"""
from tracks import templating
from tracks.executor.validate import check_design_trace

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
