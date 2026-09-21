"""T-015 RED: single-active-run scheduling and the auditable hotfix swap
(IF-SCHED-001).

Devon-owned unit RED for the T-015 delivery slice —
``tracks/supervisor/scheduler.py`` (architecture §1.0.3 单活动 + 换队, §1c #8
schedule 单行表). The failing nodes pin the IF-SCHED-001 contracts this task
must deliver:

- the schedule singleton row (``id=1``) carries the one ``active_run`` plus an
  ordered ``queue`` of accepted runs; the second accepted run waits in the
  queue and is never driven while the first is active (interfaces §1c #8,
  §1i — AC-FR0296-03);
- ``next_runnable`` names only the active run, and nothing at all while an
  effective pause (``run.pause_requested`` / ``run.paused``) blocks new
  drives — no wake-up may bypass a pause (interfaces §1i);
- every active-slot change is auditable through ``schedule.changed`` with the
  closed reason vocabulary and the ``{active_run, queued, reason}`` payload
  keys (interfaces §1a #18);
- terminal runs leave the schedule (active -> queue head promoted; queued ->
  removed), so only accepted runs are ever queued (AC-FR0296-03);
- hotfix urgency is an explicit auditable swap: ``preempt=true`` moves the
  hotfix into the single active slot with ``schedule.changed``
  ``reason=hotfix_preemption`` while the preempted feature run is parked at
  the queue front; when the hotfix reaches terminal the preempted feature is
  resumed as the active run (interfaces §1i — AC-FR0296-04). The full
  pause -> swap -> resume command chain with actor/command_id audit rides
  T-INT (deferred_refs).

The scaffold bodies of scheduler.py still raise the IF-SCHED-001 stub token,
so every failing node guards that stub state into a real ``AssertionError``
(``raise ... from None`` / plain ``assert``): the records classify as
assertion_failure — no stub_token, no assembly errors, no ``pytest.fail``
(whose ``Failed:`` line classifies unclassified). Nothing here mocks the
system under test: schedule state is written through the production
``Scheduler`` and read back from the real ServiceDB-backed service.db.

AC: FR-0296 — TRACKS-TRACE IF-SCHED-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from tracks.supervisor.db import ServiceDB
from tracks.supervisor.scheduler import Scheduler

_SCHEDULE_CHANGED_REASONS = frozenset(
    {"run_created", "hotfix_preemption", "run_terminal", "run_paused", "run_resumed"}
)


def _service_db(tmp_path: Path) -> ServiceDB:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    return ServiceDB(home)


def _schedule_rows(home: Path) -> list:
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        return conn.execute("SELECT id, active_run, queue_json FROM schedule").fetchall()


def _schedule_changed(db: ServiceDB) -> list:
    return [event for event in db.read_events() if event["type"] == "schedule.changed"]


def _create(
    engine: Scheduler,
    run_id: str,
    *,
    journey: str = "feature",
    preempt: bool = False,
    actor: str = "human",
) -> None:
    """Call on_run_created; the IF-SCHED-001 stub becomes an assertion."""
    try:
        engine.on_run_created(run_id, journey=journey, preempt=preempt, actor=actor)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-SCHED-001 on_run_created is still a stub: "
            f"{exc} — an accepted run must enter the schedule (active or queued)"
        ) from None


def _terminal(engine: Scheduler, run_id: str) -> None:
    """Call on_run_terminal; the IF-SCHED-001 stub becomes an assertion."""
    try:
        engine.on_run_terminal(run_id)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-SCHED-001 on_run_terminal is still a stub: "
            f"{exc} — a terminal run must leave the active slot / queue"
        ) from None


def _snapshot(engine: Scheduler) -> dict:
    """Call queue_snapshot; the IF-SCHED-001 stub becomes an assertion."""
    try:
        return engine.queue_snapshot()
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-SCHED-001 queue_snapshot is still a stub: "
            f"{exc} — the single active run and ordered queue must be visible"
        ) from None


def _next_runnable(engine: Scheduler) -> str | None:
    """Call next_runnable; the IF-SCHED-001 stub becomes an assertion."""
    try:
        return engine.next_runnable()
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-SCHED-001 next_runnable is still a stub: "
            f"{exc} — only the one active run may be driven"
        ) from None


def _preempted_scenario(tmp_path: Path) -> tuple[ServiceDB, Scheduler]:
    """Feature active + a second accepted feature queued + hotfix preemption."""
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    _create(engine, "R-FEAT")
    _create(engine, "R-NEXT")
    _create(engine, "R-HOT", journey="hotfix_post", preempt=True)
    return db, engine


# -- single active run + ordered queue (AC-FR0296-03) ---------------------------


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 first run takes the single active slot
def test_first_created_run_becomes_active_singleton(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    _create(engine, "R1")
    snapshot = _snapshot(engine)
    assert set(snapshot) == {"active_run", "queued"}
    assert snapshot["active_run"] == "R1"
    assert snapshot["queued"] == []
    assert _next_runnable(engine) == "R1"
    stored = db.get_schedule()
    assert stored is not None
    assert stored["active_run"] == "R1"
    assert stored["queue"] == []
    rows = _schedule_rows(tmp_path / "service-home")
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] == "R1"
    assert json.loads(rows[0][2]) == []
    changed = _schedule_changed(db)
    assert len(changed) == 1
    payload = changed[0]["payload"]
    assert payload["reason"] == "run_created"
    assert payload["active_run"] == "R1"
    assert payload["queued"] == []


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 second accepted run queues behind active
def test_second_accepted_run_is_queued_not_driven(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    _create(engine, "R1")
    _create(engine, "R2")
    snapshot = _snapshot(engine)
    assert snapshot["active_run"] == "R1"
    assert snapshot["queued"] == ["R2"]
    stored = db.get_schedule()
    assert stored["active_run"] == "R1"
    assert stored["queue"] == ["R2"]
    assert [_next_runnable(engine) for _ in range(3)] == ["R1", "R1", "R1"]


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 queue keeps arrival order
def test_queue_preserves_arrival_order(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    for run_id in ("R1", "R2", "R3"):
        _create(engine, run_id)
    assert _snapshot(engine) == {"active_run": "R1", "queued": ["R2", "R3"]}
    assert db.get_schedule()["queue"] == ["R2", "R3"]


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 schedule row is the durable state
def test_schedule_row_is_the_durable_state(tmp_path: Path):
    db = _service_db(tmp_path)
    first = Scheduler(db)
    _create(first, "R1")
    _create(first, "R2")
    restarted = Scheduler(db)
    assert _snapshot(restarted) == {"active_run": "R1", "queued": ["R2"]}
    assert _next_runnable(restarted) == "R1"


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 no active run -> nothing to drive
def test_next_runnable_none_without_active_run(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    assert _next_runnable(engine) is None
    assert _snapshot(engine) == {"active_run": None, "queued": []}


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 terminal active promotes queue head
def test_active_terminal_promotes_queue_head(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    for run_id in ("R1", "R2", "R3"):
        _create(engine, run_id)
    _terminal(engine, "R1")
    assert _snapshot(engine) == {"active_run": "R2", "queued": ["R3"]}
    assert db.get_schedule()["active_run"] == "R2"
    assert db.get_schedule()["queue"] == ["R3"]
    assert _next_runnable(engine) == "R2"
    terminal = [
        event
        for event in _schedule_changed(db)
        if event["payload"]["reason"] == "run_terminal"
    ]
    assert len(terminal) == 1
    assert terminal[0]["payload"]["active_run"] == "R2"


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 terminal queued run leaves the queue
def test_queued_terminal_leaves_active_untouched(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    for run_id in ("R1", "R2", "R3"):
        _create(engine, run_id)
    _terminal(engine, "R2")
    assert _snapshot(engine) == {"active_run": "R1", "queued": ["R3"]}
    assert _next_runnable(engine) == "R1"


# -- hotfix priority swap (AC-FR0296-04) ----------------------------------------


# AC-FR0296-04@v0.9 TRACKS-TRACE IF-SCHED-001 hotfix preempt activates + audits swap
def test_hotfix_preemption_activates_hotfix_and_queues_feature(tmp_path: Path):
    db, engine = _preempted_scenario(tmp_path)
    snapshot = _snapshot(engine)
    assert snapshot["active_run"] == "R-HOT"
    assert "R-FEAT" in snapshot["queued"]
    assert "R-NEXT" in snapshot["queued"]
    assert _next_runnable(engine) == "R-HOT"
    preemptions = [
        event
        for event in _schedule_changed(db)
        if event["payload"]["reason"] == "hotfix_preemption"
    ]
    assert len(preemptions) == 1
    payload = preemptions[0]["payload"]
    assert payload["active_run"] == "R-HOT"
    assert "R-FEAT" in payload["queued"]


# AC-FR0296-04@v0.9 TRACKS-TRACE IF-SCHED-001 hotfix terminal resumes preempted feature
def test_hotfix_terminal_resumes_preempted_feature(tmp_path: Path):
    db, engine = _preempted_scenario(tmp_path)
    assert _next_runnable(engine) == "R-HOT"
    _terminal(engine, "R-HOT")
    assert _snapshot(engine) == {"active_run": "R-FEAT", "queued": ["R-NEXT"]}
    assert _next_runnable(engine) == "R-FEAT"
    resumed = [
        event
        for event in _schedule_changed(db)
        if event["payload"]["reason"] == "run_resumed"
    ]
    assert len(resumed) == 1
    assert resumed[0]["payload"]["active_run"] == "R-FEAT"


# AC-FR0296-04@v0.9 TRACKS-TRACE IF-SCHED-001 preempt without active run is no swap
def test_hotfix_preempt_without_active_run_is_plain_activation(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    _create(engine, "R-HOT", journey="hotfix_dev", preempt=True)
    assert _snapshot(engine) == {"active_run": "R-HOT", "queued": []}
    assert _next_runnable(engine) == "R-HOT"
    reasons = [event["payload"]["reason"] for event in _schedule_changed(db)]
    assert "hotfix_preemption" not in reasons


# -- pause priority: no drive while an effective pause stands (§1i) -------------


# AC-FR0296-04@v0.9 TRACKS-TRACE IF-SCHED-001 pause request blocks new drives
def test_pause_request_blocks_drive_until_resumed(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    for run_id in ("R1", "R2"):
        _create(engine, run_id)
    db.append_event(
        "run.pause_requested",
        {"run_id": "R1", "actor": "human", "command_id": "CMD-PAUSE"},
        run_id="R1",
        command_id="CMD-PAUSE",
    )
    assert _next_runnable(engine) is None
    db.append_event(
        "run.paused",
        {
            "run_id": "R1",
            "actor": "human",
            "command_id": "CMD-PAUSE",
            "at_boundary": "drive_boundary",
        },
        run_id="R1",
        command_id="CMD-PAUSE",
    )
    assert _next_runnable(engine) is None
    assert _snapshot(engine)["active_run"] == "R1"
    db.append_event(
        "run.resumed",
        {"run_id": "R1", "actor": "human", "command_id": "CMD-RESUME", "from_stage": None},
        run_id="R1",
        command_id="CMD-RESUME",
    )
    assert _next_runnable(engine) == "R1"


# -- audit vocabulary of schedule.changed (interfaces §1a #18) ------------------


# AC-FR0296-03@v0.9 TRACKS-TRACE IF-SCHED-001 schedule.changed payloads stay closed
# AC-FR0296-04@v0.9 TRACKS-TRACE IF-SCHED-001 swap audit reasons stay closed
def test_schedule_changed_events_use_closed_vocabulary(tmp_path: Path):
    db = _service_db(tmp_path)
    engine = Scheduler(db)
    _create(engine, "R1")
    _create(engine, "R2")
    _create(engine, "R-HOT", journey="hotfix_post", preempt=True)
    _terminal(engine, "R-HOT")
    _terminal(engine, "R1")
    changed = _schedule_changed(db)
    assert changed, "no schedule.changed events were emitted"
    for event in changed:
        payload = event["payload"]
        assert payload["reason"] in _SCHEDULE_CHANGED_REASONS
        assert payload["active_run"] is None or isinstance(payload["active_run"], str)
        assert isinstance(payload["queued"], list)
        assert all(isinstance(run_id, str) for run_id in payload["queued"])
