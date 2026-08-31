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


# AC-FR0080-01@v0.4 TRACKS-TRACE independent CLI
def test_independent_cli(tmp_path, capsys):
    """AC-FR0080-01@v0.4 trac check trace runs as independent CLI."""
    repo = setup_trace_repo(tmp_path, "clean")
    assert cmd_check(repo, "trace") == 0
    assert "trace ok" in capsys.readouterr().out


# AC-FR0080-06@v0.4 TRACKS-TRACE short format in CLI
def test_short_format_in_cli(tmp_path, capsys):
    """AC-FR0080-06@v0.4 short-format marker detected via CLI."""
    repo = setup_trace_repo(tmp_path, "short_marker")
    assert cmd_check(repo, "trace") == 1
    err = capsys.readouterr().out
    assert "short format" in err


# AC-FR0110-01@v0.4 TRACKS-TRACE JSON output
def test_json_output(tmp_path, capsys):
    """AC-FR0110-01@v0.4 --json output is valid JSON."""
    repo = setup_trace_repo(tmp_path, "clean")
    assert cmd_check(repo, "trace", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "pass"
    assert data["hard_errors"] == []


# AC-FR0110-01@v0.4 TRACKS-TRACE human readable output
def test_human_readable_output(tmp_path, capsys):
    """AC-FR0110-01@v0.4 default human-readable output."""
    repo = setup_trace_repo(tmp_path, "orphans")
    assert cmd_check(repo, "trace") == 1
    out = capsys.readouterr().out
    assert "has no AC item" in out or "non-existent" in out or "no test marker" in out


# AC-FR0110-02@v0.4 TRACKS-TRACE exit code stable
def test_exit_code_stable(tmp_path):
    """AC-FR0110-02@v0.4 exit code stable across multiple runs."""
    repo = setup_trace_repo(tmp_path, "clean")
    codes = [cmd_check(repo, "trace") for _ in range(3)]
    assert all(c == 0 for c in codes)
    repo_orphans = setup_trace_repo(tmp_path, "orphans")
    codes = [cmd_check(repo_orphans, "trace") for _ in range(3)]
    assert all(c == 1 for c in codes)


# AC-FR0110-03@v0.4 TRACKS-TRACE engine consumer
def test_engine_consumer(tmp_path, capsys):
    """AC-FR0110-03@v0.4 engine reads --json + exit code as verdict."""
    repo = setup_trace_repo(tmp_path, "orphans")
    rc = cmd_check(repo, "trace", "--json")
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["status"] == "fail"
    assert len(data["hard_errors"]) > 0


# AC-NFR0010-01@v0.4 TRACKS-TRACE no file changes
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


# AC-NFR0010-02@v0.4 TRACKS-TRACE no auto fix
def test_no_auto_fix(tmp_path):
    """AC-NFR0010-02@v0.4 trace reports issues without auto-fixing."""
    repo = setup_trace_repo(tmp_path, "orphans")
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.4")
    before = (vdir / "spec.md").read_text(encoding="utf-8")
    cmd_check(repo, "trace")
    assert (vdir / "spec.md").read_text(encoding="utf-8") == before


# AC-NFR0020-01@v0.4 TRACKS-TRACE output deterministic
def test_output_deterministic(tmp_path, capsys):
    """AC-NFR0020-01@v0.4 same input produces byte-identical output."""
    repo = setup_trace_repo(tmp_path, "orphans")
    cmd_check(repo, "trace", "--json")
    out1 = capsys.readouterr().out
    cmd_check(repo, "trace", "--json")
    out2 = capsys.readouterr().out
    assert out1 == out2


# AC-FR0080-04@v0.4 TRACKS-TRACE warnings do not change exit code
def test_warnings_do_not_change_exit_code(tmp_path, capsys):
    """AC-FR0080-04@v0.4 BS->FR warnings do not change exit code."""
    repo = setup_trace_repo(tmp_path, "clean")
    rc = cmd_check(repo, "trace", "--json")
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["status"] == "pass"
    assert len(data["warnings"]) > 0


# AC-FR0080-10@v0.4 TRACKS-TRACE trace version flag
def test_trace_version_flag(tmp_path, capsys):
    """AC-FR0080-10@v0.4: trac check trace checks all ACs uniformly via
    --version, not stage-aware; M-TEST gate filters required ACs separately."""
    repo = setup_trace_repo(tmp_path, "clean")
    assert cmd_check(repo, "trace", "--version", "v0.4") == 0


# AC-FR0264-02@v0.7 TRACKS-TRACE §10.2 version isolation: early trace output unchanged
def test_v06_trace_output_version_isolated_no_closure_leak(tmp_path, capsys):
    """§10.2 regression (test-plan §10 item 2): early-version (v0.4/v0.6) trace
    output semantics are unchanged and version-isolated — the JSON carries only
    the classic {status, hard_errors, warnings} field set with NO v0.7
    candidate-bound closure field. The v0.7 path must instead go through the
    candidate-bound closure (IF-CLOSURE-001); that outlet is not wired, so the
    v0.7 clause is the legal-Red anchor (the regression clause stays green)."""
    repo = setup_trace_repo(tmp_path, "clean")
    rc = cmd_check(repo, "trace", "--version", "v0.4", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "pass"
    # Version isolation: early-version output keeps its classic field set.
    assert set(payload) <= {"status", "hard_errors", "warnings"}, (
        f"early-version trace JSON must keep its classic field set; got {sorted(payload)}"
    )
    assert "closure" not in payload, (
        "early-version trace output must not carry the v0.7 candidate-bound "
        "closure field (capability backward compatibility)"
    )
    # v0.7 goes the candidate-bound closure path; the closure record must be
    # emitted with closure=candidate-bound. Not wired -> legal Red anchor.
    cmd_check(repo, "trace", "--version", "v0.7", "--json")
    v07_raw = capsys.readouterr().out
    try:
        v07 = json.loads(v07_raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "v0.7 trace must emit a JSON candidate-bound closure record "
            f"(stdout={v07_raw!r})"
        ) from exc
    assert v07.get("closure") == "candidate-bound", (
        f"v0.7 trace must report closure=candidate-bound (version isolation); "
        f"payload={v07}"
    )


# AC-NFR0143-02@v0.8 TRACKS-TRACE release trace version isolation and export
def test_v08_release_trace_version_isolated(tmp_path, capsys):
    repo = setup_trace_repo(tmp_path, "clean")
    rc = cmd_check(repo, "trace", "--version", "v0.4", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "release" not in payload
    # v0.8 must expose release segment - legal Red anchor: not yet wired must fail
    cmd_check(repo, "trace", "--version", "v0.8", "--json")
    v08_raw = capsys.readouterr().out
    # Early version must be isolated (no release/closure leak)
    assert rc == 0
    # v0.8 trace must emit JSON with release segment; pre-implementation this fails legally
    try:
        v08 = json.loads(v08_raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"v0.8 trace must emit JSON with release segment, got {v08_raw!r}") from exc
    assert "release" in v08, f"v0.8 trace JSON must contain release, got {sorted(v08.keys())}"
    assert v08.get("release", {}).get("status") in ("closed", "pending", None)
