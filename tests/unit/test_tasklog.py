"""Focused contracts for the deterministic Runtime task-log projection."""

from __future__ import annotations

from tracks.cli.main import cmd_report
from tracks.executor.executor import Executor
from tracks.executor.taskgraph import TaskNode
from tracks.kernel.events import Command, EventEnvelope
from tracks.store import Store
from tracks.tasklog import build_task_log, rebuild_task_log, task_log_path


def _event(
    seq: int,
    event_type: str,
    payload: dict | None = None,
    *,
    command_id: str | None = None,
    task_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        seq=seq,
        ts="1970-01-01T00:00:00+00:00",
        run_id="run-1",
        version="v0.5",
        type=event_type,
        schema_version=1,
        command_id=command_id,
        task_id=task_id,
        payload=payload or {},
    )


def _task_events() -> list[EventEnvelope]:
    return [
        _event(1, "story.requested", {"raw_chars": 1}),
        _event(
            2,
            "taskgraph.committed",
            {"task_ids": ["T-002", "T-001"]},
        ),
        _event(3, "task.started", {"task_id": "T-002"}),
        _event(
            4,
            "outcome.received",
            {"role": "devon", "self_report": "forged agent self report"},
            task_id="T-002",
        ),
        _event(5, "verdict.passed", {"check": "red_valid"}),
        _event(
            6,
            "red.checkpointed",
            {
                "task_id": "T-002",
                "attempt": 2,
                "ref": "refs/trac/rgr/run-1/T-002/2/red",
                "r_sha": "r-sha",
            },
            task_id="T-002",
        ),
        _event(
            7,
            "green.committed",
            {
                "task_id": "T-002",
                "attempt": 2,
                "g_sha": "g-sha",
                "base_sha": "b-sha",
                "r_sha": "r-sha",
            },
            task_id="T-002",
        ),
        _event(
            8,
            "refactor.no_change",
            {"task_id": "T-002", "reason": "agent no-change reason"},
            task_id="T-002",
        ),
        _event(9, "verdict.passed", {"check": "task_review"}),
        _event(10, "task.completed", {"task_id": "T-002"}),
    ]


def test_task_log_is_event_only_deduplicated_and_task_sorted():
    events = _task_events()
    duplicate_checkpoint = _event(
        11,
        "red.checkpointed",
        {
            "task_id": "T-002",
            "attempt": 2,
            "ref": "refs/trac/rgr/run-1/T-002/2/red",
            "r_sha": "r-sha",
        },
        task_id="T-002",
    )
    text = build_task_log([*reversed(events), duplicate_checkpoint], run_id="run-1", version="v0.5")

    assert text.index("## Task `T-001`") < text.index("## Task `T-002`")
    assert "### Phase 1 Red" in text
    assert "### Runtime Quality Gate" in text
    assert text.count("Tracks-R: ref=`refs/trac/rgr/run-1/T-002/2/red` sha=`r-sha`") == 1
    assert "Public attempt: `2`" in text
    assert "Green identity: g_sha=`g-sha`" in text
    assert "Refactor result: `no-change`" in text
    assert "Task Review: `passed` (check=`task_review`)" in text
    assert "task.completed: `recorded`" in text
    assert "forged agent self report" not in text
    assert "agent no-change reason" not in text


def test_task_log_partial_run_does_not_claim_unrecorded_phases():
    text = build_task_log(
        [
            _event(1, "taskgraph.committed", {"task_ids": ["T-001"]}),
            _event(2, "task.started", {"task_id": "T-001"}),
        ],
        run_id="run-1",
        version="v0.5",
    )

    assert "No Runtime Red checkpoint recorded." in text
    assert "No Runtime Green commit recorded." in text
    assert "No Runtime refactor result recorded." in text
    assert "No task.completed event recorded." in text
    assert "task.completed: `recorded`" not in text


def test_task_log_handles_a_lease_before_task_start():
    text = build_task_log(
        [_event(1, "writelock.granted", {"task_id": "T-001"})],
        run_id="run-1",
        version="v0.5",
    )

    assert "## Task `T-001`" in text
    assert "writelock.granted: `recorded` (task start pending)" in text
    assert "No task.completed event recorded." in text


def test_rebuild_task_log_recovers_deleted_projection_byte_for_byte(tmp_path):
    home = tmp_path / ".tracks"
    events = _task_events()

    target = rebuild_task_log(home, events)
    assert target == task_log_path(home, "v0.5")
    first = target.read_bytes()
    target.unlink()

    assert rebuild_task_log(home, reversed(events)) == target
    assert target.read_bytes() == first


def test_runtime_completion_rebuilds_task_log_without_duplicate_events(host_repo):
    home = host_repo / ".tracks"
    store = Store(home)
    try:
        store.append("run-1", "v0.5", "story.requested", {"raw_chars": 1})
        store.append("run-1", "v0.5", "stage.entered", {"stage": "M-IMPL"})
        executor = Executor(store, host_repo, "run-1")
        task = TaskNode(
            task_id="T-001",
            issue_number=1,
            description="slice",
            ac_refs=(),
            fr_refs=(),
            if_ids=(),
            test_refs=(),
            scope_boundary="tracks/",
            depends_on=(),
            batch="1",
            parallel=False,
            budget=1,
        )
        executor._start_task(
            Command("select_task", command_id="C-START"), task, {"forbidden_paths": []}
        )
        target = task_log_path(home, "v0.5")
        assert target.exists()
        assert b"No task.completed event recorded." in target.read_bytes()
        command = Command("complete_task", {"task_id": "T-001"}, command_id="C-DONE")

        executor._do_complete_task(command, store.state("run-1"), None, False)
        first = target.read_bytes()
        before = [(event.seq, event.type) for event in store.events("run-1")]

        executor._do_complete_task(command, store.state("run-1"), None, True)

        assert target.read_bytes() == first
        assert [(event.seq, event.type) for event in store.events("run-1")] == before
        assert first.count(b"task.completed: `recorded`") == 1
    finally:
        store.close()


def test_report_rebuilds_a_deleted_task_log_without_mutating_events(host_repo, tmp_path):
    home = host_repo / ".tracks"
    store = Store(home)
    try:
        store.append("run-1", "v0.5", "story.requested", {"raw_chars": 1})
        store.append("run-1", "v0.5", "task.started", {"task_id": "T-001"})
        store.append(
            "run-1",
            "v0.5",
            "red.checkpointed",
            {
                "task_id": "T-001",
                "attempt": 1,
                "ref": "refs/trac/rgr/run-1/T-001/1/red",
                "r_sha": "r-sha",
            },
        )
        before = [(event.seq, event.type, event.payload) for event in store.events("run-1")]
    finally:
        store.close()

    assert cmd_report(host_repo) == 0
    target = task_log_path(home, "v0.5")
    first = target.read_bytes()
    target.unlink()

    assert (
        cmd_report(
            host_repo,
            "--run-id",
            "run-1",
            "--output",
            str(tmp_path / "report"),
        )
        == 0
    )

    check = Store(home)
    try:
        after = [(event.seq, event.type, event.payload) for event in check.events("run-1")]
    finally:
        check.close()
    assert target.read_bytes() == first
    assert after == before
