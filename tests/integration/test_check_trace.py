"""Integration tests for trac check trace CLI (FR-0080/FR-0110/NFR-0010/NFR-0020).

AC-FR0080-01@v0.4 independent CLI, AC-FR0080-06@v0.4 short format in CLI,
AC-FR0110-01@v0.4 JSON + human readable, AC-FR0110-02@v0.4 exit code stable,
AC-FR0110-03@v0.4 engine consumer, AC-NFR0010-01@v0.4 no file changes,
AC-NFR0010-02@v0.4 no auto fix, AC-NFR0020-01@v0.4 output deterministic,
AC-NFR0020-02@v0.4 stable order.
"""
import json
from pathlib import Path

from tests.integration.helpers import setup_trace_repo
from tracks import paths
from tracks.cli.main import cmd_check


def test_independent_cli(tmp_path, capsys):
    """AC-FR0080-01@v0.4 trac check trace runs as independent CLI."""
    repo = setup_trace_repo(tmp_path, "clean")
    assert cmd_check(repo, "trace") == 0
    assert "trace ok" in capsys.readouterr().out


def test_short_format_in_cli(tmp_path, capsys):
    """AC-FR0080-06@v0.4 short-format marker detected via CLI."""
    repo = setup_trace_repo(tmp_path, "short_marker")
    assert cmd_check(repo, "trace") == 1
    err = capsys.readouterr().out
    assert "short format" in err


def test_json_output(tmp_path, capsys):
    """AC-FR0110-01@v0.4 --json output is valid JSON."""
    repo = setup_trace_repo(tmp_path, "clean")
    assert cmd_check(repo, "trace", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pass"
    assert data["hard_errors"] == []


def test_human_readable_output(tmp_path, capsys):
    """AC-FR0110-01@v0.4 default human-readable output."""
    repo = setup_trace_repo(tmp_path, "orphans")
    assert cmd_check(repo, "trace") == 1
    out = capsys.readouterr().out
    assert "has no AC item" in out or "non-existent" in out or "no test marker" in out


def test_exit_code_stable(tmp_path):
    """AC-FR0110-02@v0.4 exit code stable across multiple runs."""
    repo = setup_trace_repo(tmp_path, "clean")
    codes = [cmd_check(repo, "trace") for _ in range(3)]
    assert all(c == 0 for c in codes)
    repo_orphans = setup_trace_repo(tmp_path, "orphans")
    codes = [cmd_check(repo_orphans, "trace") for _ in range(3)]
    assert all(c == 1 for c in codes)


def test_engine_consumer(tmp_path, capsys):
    """AC-FR0110-03@v0.4 engine reads --json + exit code as verdict."""
    repo = setup_trace_repo(tmp_path, "orphans")
    rc = cmd_check(repo, "trace", "--json")
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "fail"
    assert len(data["hard_errors"]) > 0


def test_no_file_changes(tmp_path):
    """AC-NFR0010-01@v0.4 running trace does not modify any files."""
    repo = setup_trace_repo(tmp_path, "orphans")
    home = paths.tracks_home(repo)
    vdir = paths.version_dir(home, "v0.4")
    files = list(vdir.rglob("*")) + list((repo / "tests").rglob("*.py"))
    before = {str(f): f.read_text(encoding="utf-8") for f in files if f.is_file()}
    cmd_check(repo, "trace")
    for fpath, content in before.items():
        assert Path(fpath).read_text(encoding="utf-8") == content


def test_no_auto_fix(tmp_path):
    """AC-NFR0010-02@v0.4 trace reports issues without auto-fixing."""
    repo = setup_trace_repo(tmp_path, "orphans")
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.4")
    before = (vdir / "spec.md").read_text(encoding="utf-8")
    cmd_check(repo, "trace")
    assert (vdir / "spec.md").read_text(encoding="utf-8") == before


def test_output_deterministic(tmp_path, capsys):
    """AC-NFR0020-01@v0.4 same input produces byte-identical output."""
    repo = setup_trace_repo(tmp_path, "orphans")
    cmd_check(repo, "trace", "--json")
    out1 = capsys.readouterr().out
    cmd_check(repo, "trace", "--json")
    out2 = capsys.readouterr().out
    assert out1 == out2


def test_warnings_do_not_change_exit_code(tmp_path, capsys):
    """AC-FR0080-04@v0.4 BS->FR warnings do not change exit code."""
    repo = setup_trace_repo(tmp_path, "clean")
    rc = cmd_check(repo, "trace", "--json")
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "pass"
    assert len(data["warnings"]) > 0


def test_trace_version_flag(tmp_path, capsys):
    """--version flag selects specific version directory."""
    repo = setup_trace_repo(tmp_path, "clean")
    assert cmd_check(repo, "trace", "--version", "v0.4") == 0
