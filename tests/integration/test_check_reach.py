"""Integration tests for trac check reach CLI (FR-0090/FR-0110/NFR-0010/NFR-0020).

AC-FR0090-01@v0.4 independent CLI, AC-FR0090-04@v0.4 no entries fail,
AC-FR0090-06@v0.4 verdict source, AC-FR0110-01@v0.4 JSON + human,
AC-FR0110-02@v0.4 exit code stable, AC-FR0110-03@v0.4 engine consumer,
AC-NFR0010-01@v0.4 no file changes, AC-NFR0010-02@v0.4 no auto fix,
AC-NFR0020-01@v0.4 output deterministic, AC-NFR0020-02@v0.4 stable order.
"""
import json
import shutil
from pathlib import Path

from tracks.cli.main import cmd_check


def _setup_reach_repo(tmp_path: Path, scenario: str = "clean") -> Path:
    """Copy a reach fixture into tmp_path."""
    fixtures = Path(__file__).resolve().parent.parent / "assets" / "reach_fixtures" / scenario
    for item in fixtures.iterdir():
        if item.is_dir():
            shutil.copytree(item, tmp_path / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, tmp_path / item.name)
    return tmp_path


def test_independent_cli(tmp_path, capsys):
    """AC-FR0090-01@v0.4 trac check reach runs as independent CLI."""
    repo = _setup_reach_repo(tmp_path, "clean")
    assert cmd_check(repo, "reach") == 0
    assert "reach ok" in capsys.readouterr().out


def test_islands_reported(tmp_path, capsys):
    """AC-FR0090-02@v0.4 islands reported in human-readable output."""
    repo = _setup_reach_repo(tmp_path, "island")
    assert cmd_check(repo, "reach") == 1
    out = capsys.readouterr().out
    assert "island module" in out
    assert "orphan" in out


def test_no_entries_fail(tmp_path, capsys):
    """AC-FR0090-04@v0.4 no entrypoint declaration -> error, non-zero exit."""
    repo = _setup_reach_repo(tmp_path, "no_entries")
    assert cmd_check(repo, "reach") == 1
    err = capsys.readouterr().err
    assert "no entrypoints" in err


def test_json_output(tmp_path, capsys):
    """AC-FR0110-01@v0.4 --json output is valid JSON."""
    repo = _setup_reach_repo(tmp_path, "clean")
    assert cmd_check(repo, "reach", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pass"
    assert data["islands"] == []


def test_human_readable_output(tmp_path, capsys):
    """AC-FR0110-01@v0.4 default human-readable output."""
    repo = _setup_reach_repo(tmp_path, "island")
    assert cmd_check(repo, "reach") == 1
    out = capsys.readouterr().out
    assert "island module" in out


def test_exit_code_stable(tmp_path):
    """AC-FR0110-02@v0.4 exit code stable across multiple runs."""
    repo = _setup_reach_repo(tmp_path, "clean")
    codes = [cmd_check(repo, "reach") for _ in range(3)]
    assert all(c == 0 for c in codes)
    repo_island = _setup_reach_repo(tmp_path, "island")
    codes = [cmd_check(repo_island, "reach") for _ in range(3)]
    assert all(c == 1 for c in codes)


def test_engine_consumer(tmp_path, capsys):
    """AC-FR0110-03@v0.4 AC-FR0090-06@v0.4 engine reads --json + exit code."""
    repo = _setup_reach_repo(tmp_path, "island")
    rc = cmd_check(repo, "reach", "--json")
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "fail"
    assert len(data["islands"]) > 0


def test_no_file_changes(tmp_path):
    """AC-NFR0010-01@v0.4 running reach does not modify any files."""
    repo = _setup_reach_repo(tmp_path, "island")
    before = {}
    for f in repo.rglob("*.py"):
        before[str(f)] = f.read_text(encoding="utf-8")
    for f in repo.rglob("*.toml"):
        before[str(f)] = f.read_text(encoding="utf-8")
    cmd_check(repo, "reach")
    for fpath, content in before.items():
        assert Path(fpath).read_text(encoding="utf-8") == content


def test_no_auto_fix(tmp_path, capsys):
    """AC-NFR0010-02@v0.4 reach reports issues without auto-fixing."""
    repo = _setup_reach_repo(tmp_path, "island")
    orphan = repo / "pkg" / "orphan.py"
    before = orphan.read_text(encoding="utf-8")
    cmd_check(repo, "reach")
    after = orphan.read_text(encoding="utf-8")
    assert before == after


def test_output_deterministic(tmp_path, capsys):
    """AC-NFR0020-01@v0.4 same input produces byte-identical output."""
    repo = _setup_reach_repo(tmp_path, "island")
    cmd_check(repo, "reach", "--json")
    out1 = capsys.readouterr().out
    cmd_check(repo, "reach", "--json")
    out2 = capsys.readouterr().out
    assert out1 == out2


def test_entry_flag(tmp_path, capsys):
    """--entry flag adds explicit entrypoint."""
    repo = _setup_reach_repo(tmp_path, "no_entries")
    # With --entry, the no-entries fixture has an entrypoint
    assert cmd_check(repo, "reach", "--entry", "pkg.mod_a") == 0
