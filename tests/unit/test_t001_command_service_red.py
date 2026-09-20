"""T-001 RED: persistent command-service core (IF-CMDSVC-001).

Devon-owned unit RED for the command-service slice the task declares: ``db.py``
(service-plane store, interfaces §1c) and ``service.py`` (CommandService,
interfaces §1b/§1h). The failing nodes pin the IF-CMDSVC-001 contracts this
task must deliver:

- persist-before-execute (SM-01.1): an HTTP acceptance returns after the write
  and never waits for execution (NFR-0150-01);
- submission surface (cli/http) recorded on the command row and the
  ``command.accepted`` payload (§1a#6);
- idempotency dedup / same-key-different-params conflict / missing-key
  rejection (§1b.2);
- human-only actor_class enforcement (§1f.5);
- create_run business preflight: ``active_run_exists`` without preempt
  (FR-0291); the create_run journey schema tier (FR-0292-01; the v0.8 hotfix
  business precheck behind AC-FR0292-02 is deferred to T-INT);
- clarification accepted on the same run, no new run (FR-0293);
- pause acceptance persists ``run.pause_requested`` (two-phase, FR-0310).

Both ServiceDB and CommandService are exercised as real objects. Their current
scaffold bodies raise ``NotImplementedError("IF-...")``; those contract-token
failures are the legitimate Red. Where a preflight needs persisted facts, the
documented service.db tables (§1c) and the registered project's run plane are
seeded directly — nothing here mocks the system under test.

AC: FR-0291/FR-0292/FR-0293/FR-0310/NFR-0150 — TRACKS-TRACE IF-CMDSVC-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

import pytest

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor import service as svc

_TS = "2026-09-19T00:00:00+00:00"

# Service-plane schema for the tables this unit slice seeds/reads (interfaces
# §1c). Single-line statements; ServiceDB owns the full schema.
_SERVICE_SCHEMA = (
    "CREATE TABLE service_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
    " type TEXT, project_id TEXT, run_id TEXT, command_id TEXT, payload TEXT);"
    "CREATE TABLE projects (project_id TEXT PRIMARY KEY, repo_path TEXT UNIQUE,"
    " version TEXT, registered_by TEXT, registered_at TEXT);"
    "CREATE TABLE commands (command_id TEXT PRIMARY KEY, kind TEXT, params_json TEXT,"
    " params_digest TEXT, idempotency_key TEXT UNIQUE, actor TEXT, actor_class TEXT,"
    " surface TEXT, project_id TEXT, run_id TEXT, status TEXT,"
    " claim_generation INTEGER, result_json TEXT, created_at TEXT, updated_at TEXT);"
    "CREATE TABLE schedule (id INTEGER PRIMARY KEY CHECK (id = 1), active_run TEXT,"
    " queue_json TEXT);"
)

_LEGAL_RUN_PARAMS = {
    "project_id": "P1",
    "journey": "feature",
    "version": "v0.9",
    "story": "as a user I want a feature",
    "issue": None,
    "target": None,
    "preempt": False,
}


def _connect(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "service.db")


def _exec(home: Path, sql: str, params: tuple = ()) -> None:
    with contextlib.closing(_connect(home)) as conn:
        conn.execute(sql, params)
        conn.commit()


def _rows(home: Path, sql: str, params: tuple = ()) -> list:
    with contextlib.closing(_connect(home)) as conn:
        return conn.execute(sql, params).fetchall()


def _seed_project(home: Path, repo: Path) -> None:
    _exec(
        home,
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?, ?, ?, ?, ?)",
        ("P1", str(repo), "v0.9", "local-user", _TS),
    )


def _seed_schedule(home: Path, *, active_run: str | None = None) -> None:
    _exec(
        home,
        "INSERT INTO schedule (id, active_run, queue_json) VALUES (1, ?, ?)",
        (active_run, json.dumps([])),
    )


def _seed_readiness_ok(home: Path) -> None:
    payload = {
        "project_id": "P1",
        "ok": True,
        "checks": {"contract": {"ok": True, "reason": None}},
        "actor": "local-user",
    }
    _exec(
        home,
        "INSERT INTO service_events (ts, type, project_id, run_id, command_id, payload)"
        " VALUES (?, ?, ?, NULL, NULL, ?)",
        (_TS, "project.readiness_checked", "P1", json.dumps(payload)),
    )


def _seed_legal_create_run(home: Path, repo: Path) -> None:
    _seed_project(home, repo)
    _seed_schedule(home)
    _seed_readiness_ok(home)


def _seed_active_run(repo: Path, run_id: str) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "story.requested", {"raw_chars": 1})
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-IMPL"})
    finally:
        store.close()


def _seed_pause_state(home: Path, run_id: str) -> None:
    for event_type in ("run.pause_requested", "run.paused"):
        payload = {"run_id": run_id, "actor": "local-user", "command_id": "C-pause"}
        _exec(
            home,
            "INSERT INTO service_events (ts, type, project_id, run_id, command_id, payload)"
            " VALUES (?, ?, ?, ?, NULL, ?)",
            (_TS, event_type, "P1", run_id, json.dumps(payload)),
        )


def _accept(service, kind, params, *, key="key-1", actor_class="human", surface="http"):
    return service.accept(
        kind,
        params,
        actor="local-user",
        actor_class=actor_class,
        surface=surface,
        idempotency_key=key,
    )


def _rejection(
    service, kind, params, *, key="key-1", actor_class="human", surface="http"
) -> svc.Rejection:
    with pytest.raises(svc.Rejection) as exc:
        _accept(service, kind, params, key=key, actor_class=actor_class, surface=surface)
    return exc.value


@pytest.fixture
def service_home(tmp_path: Path) -> Path:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_connect(home)) as conn:
        conn.executescript(_SERVICE_SCHEMA)
        conn.commit()
    return home


@pytest.fixture
def service(service_home: Path) -> svc.CommandService:
    return svc.CommandService(service_home, sdb.ServiceDB(service_home), {})


# -- ServiceDB: service-plane persistence (interfaces §1c) --------------------


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 service event append
def test_service_db_append_event_persists_monotonic_seq(service_home: Path):
    db = sdb.ServiceDB(service_home)
    first = db.append_event("command.accepted", {"command_id": "C1"})
    second = db.append_event("command.deduplicated", {"idempotency_key": "k1"})
    assert second > first >= 1
    rows = _rows(service_home, "SELECT seq, type, payload FROM service_events ORDER BY seq")
    assert [row[1] for row in rows] == ["command.accepted", "command.deduplicated"]
    assert [row[0] for row in rows] == [first, second]
    assert json.loads(rows[0][2])["command_id"] == "C1"


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 command row persistence
def test_service_db_register_command_persists_accepted_row(service_home: Path):
    db = sdb.ServiceDB(service_home)
    command = {
        "command_id": "C-1",
        "kind": "pause_run",
        "params_json": "{}",
        "params_digest": "d" * 64,
        "idempotency_key": "key-1",
        "actor": "local-user",
        "actor_class": "human",
        "surface": "http",
        "project_id": None,
        "run_id": "R1",
    }
    command_id = db.register_command(command)
    row = db.get_command(command_id)
    assert command_id == "C-1"
    assert row["status"] == "accepted"
    assert row["kind"] == "pause_run"
    assert row["idempotency_key"] == "key-1"


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 idempotency lookup
def test_service_db_find_by_idempotency_returns_original(service_home: Path):
    db = sdb.ServiceDB(service_home)
    db.register_command(
        {
            "command_id": "C-2",
            "kind": "resume_run",
            "params_json": "{}",
            "params_digest": "e" * 64,
            "idempotency_key": "key-2",
            "actor": "local-user",
            "actor_class": "human",
            "surface": "http",
            "project_id": None,
            "run_id": "R1",
        }
    )
    found = db.find_by_idempotency("key-2")
    assert found["command_id"] == "C-2"
    assert db.find_by_idempotency("key-missing") is None


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 unknown command lookup
def test_service_db_get_command_unknown_returns_none(service_home: Path):
    assert sdb.ServiceDB(service_home).get_command("no-such-command") is None


# -- closed sets (interfaces §1a/#1b) ----------------------------------------


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 service event closed set
def test_service_events_closed_set_matches_interfaces():
    assert set(sdb.SERVICE_EVENT_TYPES) == {
        "service.started",
        "service.stopped",
        "project.registered",
        "project.registration_rejected",
        "project.readiness_checked",
        "command.accepted",
        "command.deduplicated",
        "command.rejected",
        "command.claimed",
        "command.completed",
        "command.failed",
        "command.requeued",
        "wait.entered",
        "wait.resolved",
        "lease.acquired",
        "lease.released",
        "worker.late_result",
        "schedule.changed",
        "run.pause_requested",
        "run.paused",
        "run.resumed",
        "material.edited",
        "access.denied",
        "auth.login",
    }
    assert len(sdb.SERVICE_EVENT_TYPES) == len(set(sdb.SERVICE_EVENT_TYPES))


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 command status closed set
def test_command_statuses_closed_set():
    assert set(sdb.COMMAND_STATUSES) == {
        "accepted",
        "claimed",
        "completed",
        "failed",
        "rejected",
    }


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 service command kind partition
def test_service_command_kinds_and_actor_partition():
    expected = {
        "register_project",
        "check_readiness",
        "create_run",
        "submit_clarification",
        "edit_material",
        "record_stage_approval",
        "record_release_decision",
        "pause_run",
        "resume_run",
        "abandon_run",
        "return_stage",
        "retry_run",
        "drive_run",
    }
    assert set(svc.SERVICE_COMMAND_KINDS) == expected
    assert expected - {"drive_run"} == svc.HUMAN_ONLY_KINDS
    assert {"drive_run"} == svc.SYSTEM_ONLY_KINDS


# -- CommandService.accept: SM-01 accept path (interfaces §1b/§1h) -----------


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 accept persists then returns
# AC-NFR0150-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 acceptance excludes execution time
def test_accept_persists_before_returning_without_executing(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    receipt = _accept(service, "create_run", dict(_LEGAL_RUN_PARAMS))
    assert isinstance(receipt, svc.CommandReceipt)
    assert receipt.status == "accepted"
    assert receipt.deduplicated is False
    row = sdb.ServiceDB(service_home).get_command(receipt.command_id)
    assert row["status"] == "accepted"
    assert row["result_json"] is None
    events = _rows(
        service_home,
        "SELECT type FROM service_events WHERE command_id = ?",
        (receipt.command_id,),
    )
    assert [event[0] for event in events] == ["command.accepted"]


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 duplicate submit deduplicated
def test_accept_dedup_same_key_same_params_returns_original(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    first = _accept(service, "create_run", dict(_LEGAL_RUN_PARAMS), key="dedup-key")
    second = _accept(service, "create_run", dict(_LEGAL_RUN_PARAMS), key="dedup-key")
    assert second.command_id == first.command_id
    assert second.deduplicated is True
    count = _rows(
        service_home,
        "SELECT COUNT(*) FROM commands WHERE idempotency_key = ?",
        ("dedup-key",),
    )
    assert count[0][0] == 1
    dedup = _rows(
        service_home,
        "SELECT payload FROM service_events WHERE type = 'command.deduplicated'",
    )
    assert json.loads(dedup[0][0])["original_command_id"] == first.command_id


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 idempotency conflict
def test_accept_same_key_different_params_conflicts(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    first = _accept(service, "create_run", dict(_LEGAL_RUN_PARAMS), key="conflict-key")
    changed = dict(_LEGAL_RUN_PARAMS, version="v0.9.1")
    rejection = _rejection(service, "create_run", changed, key="conflict-key")
    assert rejection.reason == "idempotency_conflict"
    accepted = _rows(
        service_home,
        "SELECT COUNT(*) FROM service_events WHERE type = 'command.accepted'",
    )
    assert accepted[0][0] == 1
    assert first.status == "accepted"


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 missing idempotency key rejected
def test_accept_http_change_without_key_rejected(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    rejection = _rejection(service, "create_run", dict(_LEGAL_RUN_PARAMS), key=None)
    assert rejection.reason == "validation_failed"
    assert _rows(service_home, "SELECT COUNT(*) FROM commands")[0][0] == 0


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 closed kind set rejects shell
def test_accept_unknown_kind_rejected(service, service_home):
    rejection = _rejection(service, "run_shell", {"cmd": "rm -rf /"}, key="shell-key")
    assert rejection.reason in {"guard_blocked", "validation_failed"}
    assert _rows(service_home, "SELECT COUNT(*) FROM commands")[0][0] == 0


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 human-only actor class enforced
def test_accept_enforces_human_only_actor_class(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    system_attempt = _rejection(
        service, "create_run", dict(_LEGAL_RUN_PARAMS), actor_class="system"
    )
    assert system_attempt.reason == "forbidden_actor"
    human_attempt = _rejection(service, "drive_run", {"run_id": "R1"}, actor_class="human")
    assert human_attempt.reason == "forbidden_actor"
    assert _rows(service_home, "SELECT COUNT(*) FROM commands")[0][0] == 0


# AC-FR0291-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 active run blocks new run
def test_accept_create_run_active_run_exists_without_preempt_rejected(
    service, service_home, tmp_path
):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    _exec(service_home, "UPDATE schedule SET active_run = ? WHERE id = 1", ("R-active",))
    rejection = _rejection(service, "create_run", dict(_LEGAL_RUN_PARAMS))
    assert rejection.reason == "active_run_exists"
    assert _rows(service_home, "SELECT COUNT(*) FROM commands")[0][0] == 0


# AC-FR0292-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 create_run journey schema
def test_accept_create_run_unknown_journey_rejected_at_schema(service, service_home):
    # The hotfix business precheck itself (AC-FR0292-02) is deferred to T-INT;
    # this unit tier pins only the create_run params-schema tier (§1b.1), which
    # rejects an unknown journey before any deferred business preflight runs.
    params = dict(_LEGAL_RUN_PARAMS, journey="turbo_fix")
    rejection = _rejection(service, "create_run", params, key="journey-key")
    assert rejection.reason == "validation_failed"
    assert _rows(service_home, "SELECT COUNT(*) FROM commands")[0][0] == 0


# AC-FR0293-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 clarification keeps run id
def test_accept_submit_clarification_continues_same_run(service, service_home, tmp_path):
    repo = git_repo(tmp_path)
    _seed_project(service_home, repo)
    _seed_active_run(repo, "R1")
    params = {"run_id": "R1", "doc": "spec", "thread_token": "token-1", "body": "answer"}
    receipt = _accept(service, "submit_clarification", params, key="clar-key")
    assert receipt.status == "accepted"
    row = sdb.ServiceDB(service_home).get_command(receipt.command_id)
    assert row["kind"] == "submit_clarification"
    assert row["run_id"] == "R1"
    events = _rows(
        service_home,
        "SELECT type, run_id FROM service_events WHERE command_id = ?",
        (receipt.command_id,),
    )
    assert events == [("command.accepted", "R1")]


# AC-FR0310-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 pause acceptance two-phase
def test_accept_pause_persists_requested_before_paused(service, service_home, tmp_path):
    repo = git_repo(tmp_path)
    _seed_project(service_home, repo)
    _seed_active_run(repo, "R1")
    receipt = _accept(service, "pause_run", {"run_id": "R1"}, key="pause-key")
    assert receipt.status == "accepted"
    events = _rows(
        service_home,
        "SELECT type, command_id, run_id FROM service_events ORDER BY seq",
    )
    assert [event[0] for event in events] == ["command.accepted", "run.pause_requested"]
    assert all(event[1] == receipt.command_id for event in events)
    assert events[1][2] == "R1"


# AC-FR0310-02@v0.9 TRACKS-TRACE IF-CMDSVC-001 resume from paused position
def test_accept_resume_emits_run_resumed(service, service_home, tmp_path):
    repo = git_repo(tmp_path)
    _seed_project(service_home, repo)
    _seed_active_run(repo, "R1")
    _seed_pause_state(service_home, "R1")
    receipt = _accept(service, "resume_run", {"run_id": "R1"}, key="resume-key")
    assert receipt.status == "accepted"
    types = [
        row[0]
        for row in _rows(
            service_home,
            "SELECT type FROM service_events WHERE command_id = ? ORDER BY seq",
            (receipt.command_id,),
        )
    ]
    assert types == ["command.accepted", "run.resumed"]


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 submission surface recorded
def test_accept_records_submission_surface(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    cli = _accept(service, "create_run", dict(_LEGAL_RUN_PARAMS), key="cli-key", surface="cli")
    http = _accept(
        service, "create_run", dict(_LEGAL_RUN_PARAMS), key="http-key", surface="http"
    )
    rows = {
        row[0]: row[1]
        for row in _rows(service_home, "SELECT command_id, surface FROM commands")
    }
    assert rows[cli.command_id] == "cli"
    assert rows[http.command_id] == "http"
    payloads = {
        row[0]: json.loads(row[1])["surface"]
        for row in _rows(
            service_home,
            "SELECT command_id, payload FROM service_events WHERE type = 'command.accepted'",
        )
    }
    assert payloads[cli.command_id] == "cli"
    assert payloads[http.command_id] == "http"


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-CMDSVC-001 persisted status query
def test_status_returns_persisted_command(service, service_home, tmp_path):
    _seed_legal_create_run(service_home, git_repo(tmp_path))
    receipt = _accept(service, "create_run", dict(_LEGAL_RUN_PARAMS), key="status-key")
    status = service.status(receipt.command_id)
    assert status["command_id"] == receipt.command_id
    assert status["status"] == "accepted"
    assert status["kind"] == "create_run"
