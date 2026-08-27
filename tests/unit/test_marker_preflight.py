"""Marker preflight (2026-08-27): fail-fast at the Shield WRITE gate.

One canonical parser (tracks.checks.trace._MARKER_LINE + the shared
marker-format layer) serves both the WRITE preflight and the M-TEST EXIT
trace gate. Regression for run 01M0S0FQ, where a short-format marker rode
through every WRITE/red/Prism round and exploded only at EXIT.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git_repo
from tracks.checks.trace import marker_preflight_errors


def _write(repo: Path, rel: str, marker_line: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{marker_line}\ndef test_x():\n    assert True\n", encoding="utf-8"
    )
    return rel


def test_short_format_marker_flagged(tmp_path):
    repo = git_repo(tmp_path)
    rel = _write(repo, "tests/unit/test_a.py", "# AC-FR0265-01 TRACKS-TRACE closure pass")
    errs = marker_preflight_errors(repo, [rel], "v0.7")
    assert errs == [f"{rel}: test marker AC-FR0265-01 short format (missing @version)"]


def test_versioned_marker_current_and_cross_version(tmp_path):
    repo = git_repo(tmp_path)
    cur = _write(repo, "tests/unit/test_cur.py", "# AC-FR0265-01@v0.7 TRACKS-TRACE closure")
    cross = _write(repo, "tests/unit/test_cross.py", "# AC-FR0010-02@v0.5 TRACKS-TRACE legacy")
    assert marker_preflight_errors(repo, [cur, cross], "v0.7") == []
    short = _write(repo, "tests/unit/test_short.py", "// AC-FR0265-02 TRACKS-TRACE x")
    assert marker_preflight_errors(repo, [short], None)  # format layer only


def test_non_test_artifacts_and_data_dirs_ignored(tmp_path):
    repo = git_repo(tmp_path)
    (repo / "architecture.md").write_text("docs", encoding="utf-8")
    rel = _write(repo, "tests/assets/test_data.py", "# AC-FR0265-01 TRACKS-TRACE x")
    assert marker_preflight_errors(repo, ["architecture.md", rel], "v0.7") == []


def test_write_gate_emits_test_defect_on_bad_marker(tmp_path):
    """The wiring: _do_validate_result fails closed (verdict test_defect,
    reason marker_preflight, disposition rewrite) instead of validating."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    repo = git_repo(tmp_path)
    import shutil

    from tracks import paths

    contract = paths.project_toml_path(paths.tracks_home(repo))
    contract.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        Path(__file__).resolve().parents[2] / ".tracks" / "projects" / "project.toml",
        contract,
    )
    store = Store(repo / ".tracks")
    store.append("RUN", "v0.7", "stage.entered", {"stage": "M-TEST"})
    ex = Executor(store, repo, "RUN")
    rel = _write(repo, "tests/integration/test_bad.py", "# AC-FR0265-01 TRACKS-TRACE closure pass")
    cmd = __import__("tracks.kernel.events", fromlist=["Command"]).Command(
        "validate_result",
        params={"artifacts": [rel], "checks": ["write_scope"], "result_id": "R1"},
        command_id="C-V",
    )
    ex._do_validate_result(cmd, store.state("RUN"), None, False)
    failures = [e for e in store.events("RUN") if e.type == "verdict.failed"]
    assert failures and failures[-1].payload["reason"] == "marker_preflight"
    assert failures[-1].payload["check"] == "test_defect"
    assert not [e for e in store.events("RUN") if e.type == "result.validated"]
