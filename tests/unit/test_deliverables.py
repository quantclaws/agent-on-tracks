"""Deliverable consistency gate (FR-040/FR-130, AC-FR0130-03)."""
from tracks.cli.main import cmd_check
from tracks.deliverables import check_deliverables


# AC-FR0120-02@v0.4 TRACKS-TRACE real deliverables consistent
def test_real_deliverables_consistent():
    """AC-FR0120-02@v0.4: Shield.md in deliverables set, checked for
    existence + version + IQ alongside the other five agent prompts."""
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


def test_missing_iq_in_agent(tmp_path, monkeypatch):
    import tracks.deliverables as d
    p = tmp_path / "Lex.md"
    p.write_text("---\nversion: 0.2\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(d, "AGENT_DELIVERABLES", (p,))
    assert check_deliverables(paths=[p]) == [f"missing or malformed IQ in {p}"]


def test_malformed_iq_in_agent(tmp_path, monkeypatch):
    import tracks.deliverables as d
    p = tmp_path / "Sage.md"
    p.write_text("---\nversion: 0.2\nIQ: Z\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(d, "AGENT_DELIVERABLES", (p,))
    assert check_deliverables(paths=[p]) == [f"missing or malformed IQ in {p}"]


def test_wellformed_iq_in_agent(tmp_path, monkeypatch):
    import tracks.deliverables as d
    p = tmp_path / "Scribe.md"
    p.write_text("---\nversion: 0.2\nIQ: A\n---\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(d, "AGENT_DELIVERABLES", (p,))
    assert check_deliverables(paths=[p]) == []


def test_skill_exempt_from_iq(tmp_path):
    # SKILL.md is not an agent prompt: version only, no IQ required
    p = tmp_path / "SKILL.md"
    p.write_text("---\nversion: 0.2\n---\n\nbody\n", encoding="utf-8")
    assert check_deliverables(paths=[p]) == []


def test_cli_check_deliverables(tmp_path, capsys):
    assert cmd_check(tmp_path, "deliverables") == 0
    assert "deliverables ok" in capsys.readouterr().out


def test_cli_check_usage(tmp_path, capsys):
    assert cmd_check(tmp_path, "bogus") == 1
    assert "usage:" in capsys.readouterr().err
