"""Integration tests for green condition / IF- validation (FR-0140).

AC-FR0140-03@v0.4 trac validate --file test-plan.md validates IF- attribution.
"""

from pathlib import Path

from tracks import templating
from tracks.cli.main import cmd_validate

_FM = "---\nspec_id: SPEC-X\ncreated: 2026-08-05\nstatus: draft\nsha:\n---\n\n"
_AM = "---\nacc_id: ACC-X\ncreated: 2026-08-05\nstatus: draft\nsha:\n---\n\n"
_IM = (
    "---\ninterfaces_id: IF-X\nspec_ref: SPEC-X\narch_ref: ARCH-X\n"
    "created: 2026-08-05\nstatus: draft\nsha:\n---\n\n"
)


def _setup_design_trio(
    tmp_path: Path, plan_text: str, if_text: str = "", acc_text: str = ""
) -> Path:
    """Set up acceptance + interfaces + test-plan in tmp_path."""
    acc = tmp_path / "acceptance.md"
    acc_text = acc_text or (_AM + "## FR-0010 A\n\n### AC-FR0010-01\n\n  - c\n")
    acc.write_text(acc_text, encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    if_text = if_text or (_IM + "# Interfaces\n\n## 5. IF Registry\n\n### IF-TRACE-001 desc\n")
    iface.write_text(if_text, encoding="utf-8")
    # Build a minimal but template-conformant test-plan: strip HTML comments
    # and frontmatter, then append §8 data rows to the template's table.
    tpl = templating.load_template("test-plan")
    tpl_body = templating._RAW_PLACEHOLDER.sub("", tpl)  # noqa: SLF001
    from tracks.executor.validate import _strip_comments
    from tracks.frontmatter import split_frontmatter

    _tpl_head, tpl_body = split_frontmatter(_strip_comments(tpl_body))
    plan = tmp_path / "test-plan.md"
    plan.write_text(_FM + tpl_body + plan_text, encoding="utf-8")
    return plan


# AC-FR0140-03@v0.4 TRACKS-TRACE validate test plan IF
def test_validate_test_plan_if(tmp_path, capsys):
    """AC-FR0140-03@v0.4 trac validate --file test-plan.md validates IF- attribution."""
    # Valid: integration AC has IF- attribution
    plan = _setup_design_trio(tmp_path, "| AC-FR0010-01 | integration | test_a | IF-TRACE-001 |\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 0
    assert "valid" in capsys.readouterr().out


# AC-FR0140-03@v0.4 TRACKS-TRACE validate test plan missing IF
def test_validate_test_plan_missing_if(tmp_path, capsys):
    """AC-FR0140-03@v0.4 integration/e2e AC missing IF- -> validate fails."""
    plan = _setup_design_trio(tmp_path, "| AC-FR0010-01 | integration | test_a | |\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 1
    err = capsys.readouterr().err
    assert "missing IF- attribution" in err


# AC-FR0140-03@v0.4 TRACKS-TRACE validate test plan bad IF
def test_validate_test_plan_bad_if(tmp_path, capsys):
    """AC-FR0140-03@v0.4 unregistered IF- identifier -> validate fails."""
    plan = _setup_design_trio(tmp_path, "| AC-FR0010-01 | integration | test_a | IF-FAKE-999 |\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 1
    err = capsys.readouterr().err
    assert "IF-FAKE-999 not defined" in err


# AC-FR0140-02@v0.4 TRACKS-TRACE validate test plan unit no IF required
def test_validate_test_plan_unit_no_if_required(tmp_path, capsys):
    """AC-FR0140-02@v0.4 unit AC does not require IF- attribution."""
    acc_text = _AM + "## FR-0010 A\n\n### AC-FR0010-01\n\n  - c\n\n### AC-FR0010-02\n\n  - c2\n"
    plan = _setup_design_trio(
        tmp_path,
        "| AC-FR0010-01 | unit | test_a | |\n"
        "| AC-FR0010-02 | integration | test_b | IF-TRACE-001 |\n",
        acc_text=acc_text,
    )
    assert cmd_validate(tmp_path, "--file", str(plan)) == 0
