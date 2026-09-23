"""Timeline diagnosis (IF-QUERY-001)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tracks.paths import tracks_home
from tracks.server.projections import project_timeline
from tracks.store import Store
from tracks.supervisor import db as sdb

pytestmark = pytest.mark.integration

_SEED_TS = "2026-09-23T00:00:00+00:00"


def _seed_diagnosis_run(tmp_path: Path, run_id: str) -> Path:
    """A registered run whose events carry the run/command/task/AC dimensions.

    The seeded history is a real M-IMPL slice: a task starts, its verdict
    fails with an AC reference and an attempt count, and a human retry
    follows under a different command — the failure/retry trail the timeline
    must render (interfaces §1e).
    """
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-IMPL"})
        store.append(
            run_id,
            "v0.9",
            "task.started",
            {"task_id": "T-1"},
            task_id="T-1",
            command_id="cmd-1",
        )
        store.append(
            run_id,
            "v0.9",
            "verdict.failed",
            {
                "check": "red_classifier",
                "reason": "boom",
                "attempt": 2,
                "ac_refs": ["AC-FR0305-01"],
            },
            task_id="T-1",
            command_id="cmd-1",
        )
        store.append(
            run_id,
            "v0.9",
            "human.retry",
            {"task_id": "T-1", "attempt": 3},
            task_id="T-1",
            command_id="cmd-2",
        )
    finally:
        store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", "local-user", _SEED_TS),
        )
        conn.commit()
    finally:
        conn.close()
    return home


def _ordering_key(row: dict) -> tuple[str, int, int]:
    return (row["ts"], 0 if row["source"] == "tracks" else 1, row["seq"])


# AC-FR0305-01@v0.9 TRACKS-TRACE timeline shows failures retries next
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_timeline_shows_failures_retries_next(tmp_path: Path):
    """AC-FR0305-01: the timeline shows the failure/retry trail that happened."""
    home = _seed_diagnosis_run(tmp_path, "run-1")

    timeline = project_timeline(home, "run-1")

    assert "events" in timeline and "cursor" in timeline
    rows = timeline["events"]
    types = [row["type"] for row in rows]
    assert types == ["stage.entered", "task.started", "verdict.failed", "human.retry"], (
        f"the timeline must show what actually happened: {types}"
    )
    for row in rows:
        assert row["run_id"] == "run-1"
        assert row["source"] in ("tracks", "service")
        assert row["summary"] == row["type"]
        assert isinstance(row["ts"], str) and row["ts"]
    keys = [_ordering_key(row) for row in rows]
    assert keys == sorted(keys), "the merged timeline follows (ts, source_rank, seq)"
    assert timeline["cursor"], "a non-empty timeline carries a resumable cursor"


# AC-FR0305-02@v0.9 TRACKS-TRACE logs locatable by dimensions
def test_logs_locatable_by_dimensions(tmp_path: Path):
    """AC-FR0305-02: an error is locatable by run, command, task and AC."""
    home = _seed_diagnosis_run(tmp_path, "run-1")

    by_command = project_timeline(home, "run-1", command_id="cmd-1")["events"]
    assert [row["type"] for row in by_command] == ["task.started", "verdict.failed"]
    assert all(row["command_id"] == "cmd-1" for row in by_command)

    by_task = project_timeline(home, "run-1", task_id="T-1")["events"]
    assert [row["type"] for row in by_task] == [
        "task.started",
        "verdict.failed",
        "human.retry",
    ]
    assert all(row["task_id"] == "T-1" for row in by_task)

    by_ac = project_timeline(home, "run-1", ac_id="AC-FR0305-01")["events"]
    assert [row["type"] for row in by_ac] == ["verdict.failed"]
    assert "AC-FR0305-01" in by_ac[0]["ac_refs"], (
        "the failure row must carry the AC dimension it references"
    )
