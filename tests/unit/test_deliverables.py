"""Deliverable consistency gate (FR-040/FR-130, AC-FR0130-03)."""
from tracks.cli.main import cmd_check
from tracks.deliverables import check_deliverables


def test_real_deliverables_consistent():
    # Scribe/Sage agents + tracks-discuz skill ship with version 0.2
    assert check_deliverables() == []


def test_missing_deliverable(tmp_path):
    missing = tmp_path / "nope.md"
    assert check_deliverables(paths=[missing]) == [f"missing deliverable: {missing}"]


def test_missing_version(tmp_path):
    p = tmp_path / "Scribe.md"
    p.write_text("---\ndescription: x\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == [f"missing or malformed version in {p}"]


def test_malformed_version(tmp_path):
    p = tmp_path / "Sage.md"
    p.write_text("---\nversion: abc\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == [f"missing or malformed version in {p}"]


def test_wellformed_version(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nversion: 0.2\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == []


def test_cli_check_deliverables(tmp_path, capsys):
    assert cmd_check(tmp_path, "deliverables") == 0
    assert "deliverables ok" in capsys.readouterr().out


def test_cli_check_usage(tmp_path, capsys):
    assert cmd_check(tmp_path, "bogus") == 1
    assert "usage:" in capsys.readouterr().err
