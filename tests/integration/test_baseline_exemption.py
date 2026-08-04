"""Integration tests for legacy baseline exemption (FR-0100).

AC-FR0100-01@v0.4 baseline file schema, AC-FR0100-02@v0.4 exemption works,
AC-FR0100-04@v0.4 new content still reported, AC-FR0100-05@v0.4 no auto fix.
"""
import json
import shutil
from pathlib import Path

from tests.integration.helpers import setup_trace_repo, write_baseline
from tracks import paths
from tracks.cli.main import cmd_check


def test_baseline_file_schema(tmp_path, capsys):
    """AC-FR0100-01@v0.4 baseline file is read and applied."""
    repo = setup_trace_repo(tmp_path, "baseline")
    write_baseline(repo, ids=["FR-0020"])
    assert cmd_check(repo, "trace", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pass"
    assert not any("FR-0020" in e for e in data["hard_errors"])


def test_trace_baseline_exemption(tmp_path, capsys):
    """AC-FR0100-02@v0.4 baseline-exempted IDs not in trace output."""
    repo = setup_trace_repo(tmp_path, "baseline")
    assert cmd_check(repo, "trace", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert any("FR-0020" in e for e in data["hard_errors"])

    write_baseline(repo, ids=["FR-0020"])
    assert cmd_check(repo, "trace", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert not any("FR-0020" in e for e in data["hard_errors"])


def test_new_content_still_reported(tmp_path, capsys):
    """AC-FR0100-04@v0.4 exempted IDs don't suppress new content errors."""
    repo = setup_trace_repo(tmp_path, "baseline")
    write_baseline(repo, ids=["FR-0020"])
    assert cmd_check(repo, "trace", "--json") == 0


def test_no_auto_fix(tmp_path):
    """AC-FR0100-05@v0.4 baseline does not trigger file modifications."""
    repo = setup_trace_repo(tmp_path, "baseline")
    write_baseline(repo, ids=["FR-0020"])
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.4")
    before = (vdir / "spec.md").read_text(encoding="utf-8")
    cmd_check(repo, "trace")
    assert (vdir / "spec.md").read_text(encoding="utf-8") == before


def test_reach_baseline_exemption(tmp_path, capsys):
    """AC-FR0100-02@v0.4 baseline-exempted modules not in reach output."""
    fixtures = (
        Path(__file__).resolve().parent.parent
        / "assets" / "reach_fixtures" / "island"
    )
    for item in fixtures.iterdir():
        if item.is_dir():
            shutil.copytree(item, tmp_path / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, tmp_path / item.name)
    assert cmd_check(tmp_path, "reach", "--json") == 1
    data = json.loads(capsys.readouterr().out)
    assert "pkg.orphan" in data["islands"]

    write_baseline(tmp_path, modules=["pkg.orphan"])
    assert cmd_check(tmp_path, "reach", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert "pkg.orphan" not in data["islands"]
