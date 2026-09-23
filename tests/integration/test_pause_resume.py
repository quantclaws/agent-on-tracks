"""Pause and resume (IF-PAUSE-001)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tracks.paths import tracks_home
from tracks.server.projections import project_run_detail
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration

_SEED_TS = "2026-09-23T00:00:00+00:00"


def _seed_paused_run(tmp_path: Path, run_id: str) -> Path:
    """A registered run at M-IMPL with one completed task.

    The completed work is what a resume must preserve (AC-FR0310-02).
    """
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-IMPL"})
        store.append(
            run_id,
            "v0.9",
            "taskgraph.committed",
            {"task_count": 2, "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}]},
        )
        store.append(run_id, "v0.9", "task.started", {"task_id": "T-1"}, task_id="T-1")
        store.append(run_id, "v0.9", "task.completed", {"task_id": "T-1"})
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


def _run_history(repo: Path, run_id: str) -> list[tuple[int, str]]:
    store = Store(tracks_home(repo))
    try:
        return [(event.seq, event.type) for event in store.events(run_id)]
    finally:
        store.close()


# AC-FR0310-01@v0.9 TRACKS-TRACE pause two phase and priority
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_pause_two_phase_and_priority(tmp_path: Path):
    """AC-FR0310-01: pause shows requested then paused; retry_at cannot cross."""
    home = _seed_paused_run(tmp_path, "run-1")
    db = sdb.ServiceDB(home)
    svc = CommandService(home, db, object())
    svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="pause-1",
    )
    kinds = [event["type"] for event in db.read_events(run_id="run-1")]
    assert "run.pause_requested" in kinds, "the pause request must be persisted"

    # phase one — 已接收: the request is durably visible but not yet effective
    requested = project_run_detail(home, "run-1")
    assert requested["pause"] == {"requested": True, "effective": False}
    assert requested["control_state"] == "暂停请求已接收"

    # phase two — 已暂停: the worker reached its boundary (§1i) and the two
    # phases are now distinguishable in the projection
    db.append_event(
        "run.paused",
        {"run_id": "run-1", "actor": "human", "command_id": "cmd-1", "at_boundary": "M-IMPL"},
        run_id="run-1",
    )
    paused = project_run_detail(home, "run-1")
    assert paused["pause"] == {"requested": False, "effective": True}
    assert paused["control_state"] == "已暂停"

    # pause priority: an already-lapsed retry_at must not cross the pause
    db.upsert_wait(
        "run-1", "quota", "rate limited", "2020-01-01T00:00:00+00:00", False, None, _SEED_TS
    )
    waited = project_run_detail(home, "run-1")
    assert waited["wait"] is not None and waited["wait"]["wait_class"] == "quota"
    assert waited["control_state"] == "已暂停", (
        "a lapsed retry_at must never release an effective pause"
    )


# AC-FR0310-02@v0.9 TRACKS-TRACE resume from legal position
def test_resume_from_legal_position(tmp_path: Path):
    """AC-FR0310-02: resume continues from paused position, work kept."""
    home = _seed_paused_run(tmp_path, "run-1")
    repo = tmp_path / "repo"
    db = sdb.ServiceDB(home)
    svc = CommandService(home, db, object())
    svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="pause-2",
    )
    before = _run_history(repo, "run-1")

    resumed = svc.accept(
        kind="resume_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="resume-1",
    )
    assert resumed.command_id
    asserted = [event for event in db.read_events(run_id="run-1") if event["type"] == "run.resumed"]
    assert asserted, "the resume must be persisted"
    # the resume names the legal position it continues from
    payload = asserted[-1]["payload"]
    assert payload["from_stage"] == "M-IMPL"
    assert payload["run_id"] == "run-1"
    assert payload["actor"] == "human"
    # the completed work is preserved: the accept tier never rewrites history
    assert _run_history(repo, "run-1") == before
