"""Unit coverage for the M-IMPL batch/task progress projection.

The M-IMPL stage card keeps the phase pipeline in its state diagram and
renders the orthogonal work-unit axis from ``tasks.json`` plus generation
events: batch grouping, done/running/pending/failed states, the running
task's phase, and the generation cutoff for completions.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State
from tracks.server import projections

_VERSION = "v0.9"


def _event(seq: int, type_: str, payload: dict | None = None) -> EventEnvelope:
    payload = payload or {}
    return EventEnvelope(
        seq=seq,
        ts=f"2026-10-21T00:00:{seq:02d}+00:00",
        run_id="run-1",
        version=_VERSION,
        type=type_,
        schema_version=1,
        command_id=None,
        task_id=payload.get("task_id"),
        payload=payload,
    )


def _write_tasks(repo: Path, tasks: list[dict]) -> Path:
    target = repo / ".tracks" / "projects" / _VERSION / "tasks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema": 2, "tasks": tasks}, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def _progress(
    repo: Path, events: list[EventEnvelope], *, stage: str = "M-IMPL", substate: str = "RED"
) -> dict | None:
    return projections._task_progress(
        str(repo), _VERSION, State(stage=stage, substate=substate), events
    )


def test_task_progress_groups_batches_and_marks_done_running_pending(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_tasks(
        repo,
        [
            {"task_id": "T-001", "description": "first task", "batch": "1", "depends_on": []},
            {
                "task_id": "T-003", "title": "Third task", "description": "third",
                "batch": "1", "depends_on": [],
            },
            {
                "task_id": "T-002", "description": "second task", "batch": "2",
                "depends_on": ["T-001"],
            },
        ],
    )
    events = [
        _event(1, "stage.entered", {"stage": "M-IMPL"}),
        _event(2, "taskgraph.committed", {"task_count": 3}),
        _event(3, "task.started", {"task_id": "T-001"}),
        _event(4, "task.completed", {"task_id": "T-001"}),
        _event(5, "task.started", {"task_id": "T-003"}),
    ]

    progress = _progress(repo, events, substate="PRISM_RED")

    assert progress is not None
    assert progress["done"] == 1
    assert progress["total"] == 3
    assert progress["batch"] == 1
    assert progress["batches"] == 2
    assert progress["current_task_id"] == "T-003"
    assert progress["current_phase"] == "PRISM_RED"
    assert [group["batch"] for group in progress["batches_detail"]] == [1, 2]
    first, second = progress["batches_detail"]
    assert first["tasks"] == [
        {
            "task_id": "T-001", "title": "first task", "state": "done",
            "phase": None, "depends_on": [],
        },
        {
            "task_id": "T-003", "title": "Third task", "state": "running",
            "phase": "PRISM_RED", "depends_on": [],
        },
    ]
    assert second["tasks"] == [
        {
            "task_id": "T-002", "title": "second task", "state": "pending",
            "phase": None, "depends_on": ["T-001"],
        },
    ]


def test_batches_sort_numerically_with_non_numeric_last(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_tasks(
        repo,
        [
            {"task_id": "T-010", "description": "ten", "batch": "10", "depends_on": []},
            {"task_id": "T-002", "description": "two", "batch": "2", "depends_on": []},
            {"task_id": "T-001", "description": "one", "batch": "1", "depends_on": []},
            {"task_id": "T-003", "description": "beta", "batch": "beta", "depends_on": []},
        ],
    )
    progress = _progress(repo, [_event(1, "taskgraph.committed", {"task_count": 4})])

    assert progress is not None
    assert [group["batch"] for group in progress["batches_detail"]] == [1, 2, 10, "beta"]


def test_current_phase_is_only_reported_in_m_impl(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_tasks(
        repo,
        [{"task_id": "T-001", "description": "first", "batch": "1", "depends_on": []}],
    )
    events = [
        _event(1, "taskgraph.committed", {"task_count": 1}),
        _event(2, "task.started", {"task_id": "T-001"}),
    ]

    progress = _progress(repo, events, stage="M-TEST", substate="RED")

    assert progress is not None
    assert progress["current_phase"] is None
    assert progress["current_task_id"] == "T-001"
    assert progress["batches_detail"][0]["tasks"][0]["phase"] is None


def test_generation_cutoff_ignores_previous_completion(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_tasks(
        repo,
        [
            {"task_id": "T-001", "description": "first", "batch": "1", "depends_on": []},
            {"task_id": "T-002", "description": "second", "batch": "1", "depends_on": []},
        ],
    )
    events = [
        _event(1, "stage.entered", {"stage": "M-IMPL"}),
        _event(2, "task.started", {"task_id": "T-001"}),
        _event(3, "task.completed", {"task_id": "T-001"}),
        _event(4, "taskgraph.committed", {"task_count": 2}),
        _event(5, "task.started", {"task_id": "T-002"}),
    ]

    progress = _progress(repo, events)

    assert progress is not None
    assert progress["done"] == 0
    states = {
        task["task_id"]: task["state"]
        for group in progress["batches_detail"] for task in group["tasks"]
    }
    assert states == {"T-001": "pending", "T-002": "running"}


def test_failed_task_is_superseded_by_a_newer_start(tmp_path: Path):
    repo = tmp_path / "repo"
    _write_tasks(
        repo,
        [{"task_id": "T-001", "description": "first", "batch": "1", "depends_on": []}],
    )
    failed = [
        _event(1, "taskgraph.committed", {"task_count": 1}),
        _event(2, "task.started", {"task_id": "T-001"}),
        _event(3, "verdict.failed", {"task_id": "T-001", "check": "red", "reason": "boom"}),
    ]
    progress = _progress(repo, failed)
    assert progress is not None
    assert progress["batches_detail"][0]["tasks"][0]["state"] == "failed"

    retried = [*failed, _event(4, "task.started", {"task_id": "T-001"})]
    progress = _progress(repo, retried)
    assert progress is not None
    assert progress["batches_detail"][0]["tasks"][0]["state"] == "running"


def test_task_progress_degrades_on_missing_or_malformed_graph(tmp_path: Path):
    repo = tmp_path / "repo"
    events = [_event(1, "taskgraph.committed", {"task_count": 1})]
    assert _progress(repo, events) is None

    target = _write_tasks(repo, [])
    assert _progress(repo, events) is None

    target.write_text("{not json", encoding="utf-8")
    assert _progress(repo, events) is None

    target.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    assert _progress(repo, events) is None


def test_title_falls_back_to_description_and_marks_verification_only(tmp_path: Path):
    repo = tmp_path / "repo"
    long_description = "A" * 80 + "\nsecond line"
    _write_tasks(
        repo,
        [
            {
                "task_id": "T-001", "description": "verification-only 验收闭口\nsecond line",
                "batch": "1", "depends_on": [],
            },
            {"task_id": "T-002", "description": long_description, "batch": "1", "depends_on": []},
        ],
    )
    progress = _progress(repo, [_event(1, "taskgraph.committed", {"task_count": 2})])

    assert progress is not None
    first, second = progress["batches_detail"][0]["tasks"]
    assert first["title"] == "验收闭口"
    assert first["verification_only"] is True
    assert second["title"] == "A" * 60
    assert "verification_only" not in second
