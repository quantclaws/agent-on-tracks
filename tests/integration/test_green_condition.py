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


def _setup_design_trio(tmp_path: Path, plan_text: str, if_text: str = "") -> Path:
    """Set up acceptance + interfaces + test-plan in tmp_path."""
    acc = tmp_path / "acceptance.md"
    acc.write_text(_AM + "## FR-0010 A\n\n### AC-FR0010-01\n\n  - c\n", encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    if_text = if_text or _IM + "# Interfaces\n\n## 5. IF- Registry\n\n| IF-TRACE-001 | desc |\n"
    iface.write_text(if_text, encoding="utf-8")
    # Build a minimal but template-conformant test-plan
    tpl = templating.load_template("test-plan")
    tpl_body = templating._RAW_PLACEHOLDER.sub("", tpl)  # noqa: SLF001
    from tracks.executor.validate import _strip_comments
    from tracks.frontmatter import split_frontmatter
    _tpl_head, tpl_body = split_frontmatter(_strip_comments(tpl_body))
    # Find the end of the last required section and append our AC coverage
    plan = tmp_path / "test-plan.md"
    plan.write_text(
        _FM + tpl_body + "\n## 8. AC Coverage\n\n" + plan_text,
        encoding="utf-8",
    )
    return plan


def test_validate_test_plan_if(tmp_path, capsys):
    """AC-FR0140-03@v0.4 trac validate --file test-plan.md validates IF- attribution."""
    # Valid: integration AC has IF- attribution
    plan = _setup_design_trio(tmp_path, "- AC-FR0010-01: integration IF-TRACE-001\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_test_plan_missing_if(tmp_path, capsys):
    """AC-FR0140-03@v0.4 integration/e2e AC missing IF- -> validate fails."""
    plan = _setup_design_trio(tmp_path, "- AC-FR0010-01: integration\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 1
    err = capsys.readouterr().err
    assert "missing IF- attribution" in err


def test_validate_test_plan_bad_if(tmp_path, capsys):
    """AC-FR0140-03@v0.4 unregistered IF- identifier -> validate fails."""
    plan = _setup_design_trio(tmp_path, "- AC-FR0010-01: integration IF-FAKE-999\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 1
    err = capsys.readouterr().err
    assert "IF-FAKE-999 not defined" in err


def test_validate_test_plan_unit_no_if_required(tmp_path, capsys):
    """AC-FR0140-02@v0.4 unit AC does not require IF- attribution."""
    plan = _setup_design_trio(tmp_path, "- AC-FR0010-01: unit\n")
    assert cmd_validate(tmp_path, "--file", str(plan)) == 0
