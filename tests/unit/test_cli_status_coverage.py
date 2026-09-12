"""Behavior coverage for ``trac status`` / ``replay`` / ``report``.

Drives the status-line renderers, run-row prioritization, suspended lines,
replay parsing/printing and the report generator's error windows
(interfaces §2b, NFR-04 projection rebuild, §1.0.1 keep-alive).
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks import paths
from tracks.cli import status_cmd
from tracks.cli.status_cmd import (
    _parse_report_args,
    _print_suspended_statuses,
    _prioritize_status_rows,
    _resolve_report_events,
    _status_branch,
    _status_line,
    cmd_replay,
    cmd_report,
    cmd_status,
)
from tracks.store import Store


def _state(**kw):
    base = {
        "status": "active",
        "terminal_state": None,
        "stage": "M-STORY",
        "substate": "DRAFT",
        "awaiting": None,
        "hotfix_issue": None,
        "hotfix_scenario": None,
        "version": "v0.1",
        "current_attempt": 0,
        "last_failure": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo, Store(repo / ".tracks")


def _seed(store, run_id, version="v0.1", stage="M-STORY"):
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": stage})


# ---------------------------------------------------------------------------
# pure line helpers
# ---------------------------------------------------------------------------


def test_status_line_variants():
    completed = _status_line("RUN", _state(status="completed", terminal_state="done"))
    assert completed == "run=RUN: completed terminal=done stage=M-STORY"

    hotfix_completed = _status_line(
        "RUN",
        _state(status="completed", terminal_state="done", hotfix_issue=7, hotfix_scenario="b"),
    )
    assert hotfix_completed.endswith("branch=fix/7 scenario=b")

    waiting = _status_line(
        "RUN", _state(status="awaiting_human", awaiting="hotfix_triage", hotfix_issue=7)
    )
    assert waiting == "run=RUN: awaiting=awaiting_human origin=hotfix-triage issue=7"

    active_hotfix = _status_line("RUN", _state(hotfix_issue=9, hotfix_scenario="c"))
    assert active_hotfix.startswith("run=RUN: stage=M-STORY")
    assert "branch=fix/9 scenario=c issue=9" in active_hotfix


def test_status_branch_prefers_hotfix_then_release():
    assert _status_branch(_state(hotfix_issue=3)) == "fix/3"
    assert _status_branch(_state(version="v0.5")) == "releases/v0.5"
    assert _status_branch(_state(version="main")) == "-"


def test_prioritize_status_rows_puts_active_hotfix_first():
    class _FakeStore:
        def active_hotfix_run(self):
            return "HF"

    rows = [("A",), ("HF",), ("B",)]
    assert _prioritize_status_rows(_FakeStore(), rows) == [("HF",), ("A",), ("B",)]

    class _NoHotfix:
        def active_hotfix_run(self):
            return None

    assert _prioritize_status_rows(_NoHotfix(), rows) == rows


def test_print_suspended_statuses_skips_primary_and_completed(capsys):
    class _FakeStore:
        def state(self, run_id):
            return SimpleNamespace(
                status="completed" if run_id == "DONE" else "active",
                stage="M-IMPL",
                substate="RED",
                hotfix_issue=None,
                version="v0.1",
            )

    _print_suspended_statuses(_FakeStore(), [("P",), ("DONE",), ("S1",)], "P")
    out = capsys.readouterr().out
    assert out.count("suspended:") == 1
    assert "run=S1" in out
    assert "branch=releases/v0.1" in out


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


def test_cmd_status_no_runs(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    rc = cmd_status(repo)
    store.close()
    assert rc == 0
    assert "no runs yet" in capsys.readouterr().out


def test_cmd_status_primary_completed_prints_other_active_and_suspended(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN-C", stage="M-STORY")
    store.append("RUN-C", "v0.1", "run.completed", {"terminal_state": "done"})
    _seed(store, "RUN-A", stage="M-IMPL")
    _seed(store, "RUN-B", stage="M-IMPL")
    rc = cmd_status(repo)
    out = capsys.readouterr().out
    store.close()
    assert rc == 0
    assert "run=RUN-C: completed terminal=done stage=M-STORY" in out
    assert "run=RUN-B: stage=M-IMPL" in out
    assert "suspended: run=RUN-A stage=M-IMPL substate=BASELINE" in out


def test_cmd_status_prints_release_lines(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    monkeypatch.setattr(status_cmd, "_release_status_lines", lambda *a, **k: ["publish=planned"])
    monkeypatch.setattr(status_cmd, "_escape_status_fragments", lambda *a, **k: [])
    assert cmd_status(repo) == 0
    assert "publish=planned" in capsys.readouterr().out
    store.close()


def test_cmd_report_unknown_run_with_existing_db(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_report(repo, "--run-id", "NOPE") == 1
    assert "unknown run: NOPE" in capsys.readouterr().err
    store.close()


def test_cmd_status_swallows_status_line_failures(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    monkeypatch.setattr(
        status_cmd, "_release_status_lines", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(
        status_cmd,
        "_escape_status_fragments",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    rc = cmd_status(repo)
    store.close()
    assert rc == 0
    assert "run=RUN:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_replay
# ---------------------------------------------------------------------------


def test_cmd_replay_usage_error_and_no_runs(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_replay(repo, "A", "B") == 1
    assert "usage: trac replay" in capsys.readouterr().err
    assert cmd_replay(repo) == 1
    assert "no runs available for replay" in capsys.readouterr().err
    store.close()


def test_cmd_replay_unknown_and_success(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_replay(repo, "NOPE") == 1
    assert "unknown run: NOPE" in capsys.readouterr().err
    _seed(store, "RUN")
    rc = cmd_replay(repo, "RUN")
    out = capsys.readouterr().out
    store.close()
    assert rc == 0
    assert "story.requested" in out
    assert "final: stage=M-STORY substate=TRIAGE status=active awaiting=-" in out


# ---------------------------------------------------------------------------
# report helpers
# ---------------------------------------------------------------------------


def test_parse_report_args_valid_and_invalid():
    assert _parse_report_args(()) == (None, None, "md")
    assert _parse_report_args(("--run-id", "R", "--format", "html")) == ("R", None, "html")
    assert _parse_report_args(("--output", "o")) == (None, "o", "md")
    assert _parse_report_args(("--bogus", "x")) is None
    assert _parse_report_args(("--run-id", "R", "--run-id", "S")) is None
    assert _parse_report_args(("--run-id",)) is None
    assert _parse_report_args(("--run-id", "")) is None
    assert _parse_report_args(("--format", "pdf")) is None


def test_resolve_report_events_no_runs_and_unknown(tmp_path):
    repo, store = _setup(tmp_path)
    home = paths.tracks_home(repo)
    assert _resolve_report_events(home, None) == (None, None, "no runs available for report")
    assert _resolve_report_events(home, "NOPE") == (None, None, "unknown run: NOPE")
    _seed(store, "RUN")
    run_id, events, err = _resolve_report_events(home, "latest")
    store.close()
    assert (run_id, err) == ("RUN", None)
    assert [e.type for e in events] == ["story.requested", "stage.entered"]


def test_cmd_report_usage_error(tmp_path, capsys):
    repo, store = _setup(tmp_path)
    assert cmd_report(repo, "--bogus") == 1
    assert "usage: trac report" in capsys.readouterr().err
    store.close()


def test_cmd_report_missing_db_paths(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert cmd_report(repo, "--run-id", "RUN") == 1
    assert "unknown run: RUN" in capsys.readouterr().err
    assert cmd_report(repo) == 1
    assert "no runs available for report" in capsys.readouterr().err


def test_cmd_report_generate_failure_without_release_snippet(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    monkeypatch.setattr(status_cmd, "generate_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(status_cmd, "_release_report_snippet", lambda events: "")
    assert cmd_report(repo, "--run-id", "RUN") == 1
    assert "boom" in capsys.readouterr().err
    store.close()


def test_cmd_report_generate_failure_keeps_release_binding(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    monkeypatch.setattr(status_cmd, "generate_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(status_cmd, "_release_report_snippet", lambda events: "release preview_digest=d")
    assert cmd_report(repo, "--run-id", "RUN") == 0
    out = capsys.readouterr().out
    assert "release preview_digest=d" in out
    store.close()


def test_cmd_report_success_prints_summary_and_snippet(tmp_path, capsys, monkeypatch):
    repo, store = _setup(tmp_path)
    _seed(store, "RUN")
    monkeypatch.setattr(
        status_cmd, "generate_report", lambda repo, run_id, output: ("/r/report.md", "/r/index.html")
    )
    monkeypatch.setattr(status_cmd, "progress_summary", lambda events: "progress: 1/2")
    monkeypatch.setattr(status_cmd, "_release_report_snippet", lambda events: "release x")
    assert cmd_report(repo, "--run-id", "RUN", "--format", "html") == 0
    out = capsys.readouterr().out
    store.close()
    assert "report: /r/index.html" in out
    assert "html: /r/index.html" in out
    assert "progress: 1/2" in out
    assert "release x" in out
