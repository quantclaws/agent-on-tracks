"""Focused unit coverage for report argument resolution and progress output."""

from __future__ import annotations

import pytest

from tracks.cli.main import _parse_report_args, cmd_report
from tracks.store import Store


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), (None, None, "md")),
        (("--run-id", "run-1"), ("run-1", None, "md")),
        (("--output", "report-out"), (None, "report-out", "md")),
        (("--run-id", "latest", "--format", "html"), ("latest", None, "html")),
        (
            ("--output", "report-out", "--run-id", "run-1", "--format", "md"),
            ("run-1", "report-out", "md"),
        ),
    ],
)
def test_parse_report_args_allows_default_and_partial_options(args, expected):
    assert _parse_report_args(args) == expected


@pytest.mark.parametrize(
    "args",
    [
        ("--run-id",),
        ("--unknown", "value"),
        ("--format", "json"),
        ("--run-id", "one", "--run-id", "two"),
    ],
)
def test_parse_report_args_rejects_malformed_options(args):
    assert _parse_report_args(args) is None


def _append_run(home, run_id, events=()):
    store = Store(home)
    try:
        store.append(run_id, "v0.5", "story.requested", {"raw_chars": 1})
        for event_type, payload in events:
            store.append(run_id, "v0.5", event_type, payload)
    finally:
        store.close()


def _event_history(home, run_id):
    store = Store(home)
    try:
        return [(event.seq, event.type, event.payload) for event in store.events(run_id)]
    finally:
        store.close()


def test_report_no_args_uses_latest_run_and_default_output(host_repo, capsys):
    run_id = "run-default"
    home = host_repo / ".tracks"
    _append_run(home, run_id)

    assert cmd_report(host_repo) == 0

    output = capsys.readouterr().out.splitlines()
    default_dir = home / "report"
    assert output == [
        f"report: {default_dir / 'report.md'}",
        f"html: {default_dir / 'index.html'}",
    ]
    assert f"`{run_id}`" in (default_dir / "report.md").read_text(encoding="utf-8")


def test_report_run_id_without_output_uses_default_output(host_repo, capsys):
    run_id = "run-explicit-default"
    home = host_repo / ".tracks"
    _append_run(home, run_id)

    assert cmd_report(host_repo, "--run-id", run_id) == 0

    output = capsys.readouterr().out.splitlines()
    default_dir = home / "report"
    assert output == [
        f"report: {default_dir / 'report.md'}",
        f"html: {default_dir / 'index.html'}",
    ]


def test_report_latest_alias_and_output_only_resolve_persisted_run(host_repo, capsys, tmp_path):
    run_id = "run-persisted"
    _append_run(host_repo / ".tracks", run_id)

    latest_output = tmp_path / "latest"
    assert cmd_report(host_repo, "--run-id", "latest", "--output", str(latest_output)) == 0
    capsys.readouterr()
    assert f"`{run_id}`" in (latest_output / "report.md").read_text(encoding="utf-8")

    output_only = tmp_path / "output-only"
    assert cmd_report(host_repo, "--output", str(output_only)) == 0
    capsys.readouterr()
    assert f"`{run_id}`" in (output_only / "report.md").read_text(encoding="utf-8")


def test_report_no_run_fails_without_creating_default_output(host_repo, capsys):
    assert cmd_report(host_repo) == 1

    assert capsys.readouterr().err == "no runs available for report\n"
    assert not (host_repo / ".tracks").exists()


def test_report_unknown_explicit_run_fails_cleanly(host_repo, capsys, tmp_path):
    _append_run(host_repo / ".tracks", "known-run")

    assert cmd_report(host_repo, "--run-id", "missing-run", "--output", str(tmp_path)) == 1

    assert capsys.readouterr().err == "unknown run: missing-run\n"
    assert not (tmp_path / "report.md").exists()


@pytest.mark.parametrize(
    ("report_format", "selected_name"),
    [("md", "report.md"), ("html", "index.html")],
)
def test_explicit_report_output_stays_compatible(
    host_repo,
    capsys,
    tmp_path,
    report_format,
    selected_name,
):
    run_id = "explicit-run"
    _append_run(host_repo / ".tracks", run_id)
    output_dir = tmp_path / "report"

    assert (
        cmd_report(
            host_repo,
            "--run-id",
            run_id,
            "--output",
            str(output_dir),
            "--format",
            report_format,
        )
        == 0
    )

    assert capsys.readouterr().out.splitlines() == [
        f"report: {output_dir / selected_name}",
        f"html: {output_dir / 'index.html'}",
    ]


def test_report_prints_task_and_tracks_r_progress(host_repo, capsys, tmp_path):
    run_id = "progress-run"
    _append_run(
        host_repo / ".tracks",
        run_id,
        [
            ("taskgraph.committed", {"task_count": 2, "task_ids": ["T-002", "T-001"]}),
            ("task.started", {"task_id": "T-002"}),
            ("task.started", {"task_id": "T-001"}),
            ("red.checkpointed", {"task_id": "T-002", "r_sha": "abc123"}),
            ("task.completed", {"task_id": "T-001"}),
        ],
    )

    assert cmd_report(host_repo, "--run-id", run_id, "--output", str(tmp_path)) == 0

    assert capsys.readouterr().out.splitlines()[-1] == (
        "progress: tasks: 1/2 completed, 2 started (T-001, T-002); Tracks-R: 1 checkpointed (T-002)"
    )


def test_report_stdout_is_deterministic_and_does_not_append_events(host_repo, capsys, tmp_path):
    run_id = "deterministic-run"
    home = host_repo / ".tracks"
    _append_run(
        home,
        run_id,
        [
            ("taskgraph.committed", {"task_count": 1, "task_ids": ["T-001"]}),
            ("red.checkpointed", {"task_id": "T-001", "r_sha": "abc123"}),
        ],
    )
    before = _event_history(home, run_id)

    assert cmd_report(host_repo, "--run-id", run_id, "--output", str(tmp_path)) == 0
    first = capsys.readouterr().out
    assert cmd_report(host_repo, "--run-id", run_id, "--output", str(tmp_path)) == 0
    second = capsys.readouterr().out

    assert first == second
    assert _event_history(home, run_id) == before
