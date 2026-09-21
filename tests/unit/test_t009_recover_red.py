"""T-009 RED: crash/restart recovery for the supervisor service plane
(IF-RECOVER-001).

Devon-owned unit RED for the T-009 delivery slice:
``tracks/supervisor/recover.py`` (architecture row 17: 启动恢复——重认领/等待
保留/租约过期/既有 WAL reconcile 衔接). The failing nodes pin the
``recover_on_startup`` contract the composition root (cmd_serve startup order,
interfaces §1.1) consumes, matching the acceptance anchors of this task:

- claimed commands are requeued back to accepted and audited with
  ``command.requeued(reason=service_restart)`` carrying the same command_id
  (the idempotency path replays the SAME command, so completed external side
  effects are skipped — no duplicate effects, nothing is lost) —
  AC-FR0300-01 / AC-FR0300-02;
- persisted waits survive the restart untouched: known-reset ``retry_at`` is
  preserved exactly and unknown-reset probe plans are preserved, with no
  ``wait.resolved`` invented — AC-FR0298-01;
- stale (expired) leases are expired so a fresh lease grants a strictly higher
  generation (the fencing floor is preserved: the dead worker's generation is
  fenced and can never commit duplicate effects), while a live lease is left
  untouched — AC-FR0300-01 / AC-FR0300-02;
- the recovery summary is exactly the documented shape
  {requeued, waits_kept, leases_expired} and recovery is idempotent (a second
  run emits no duplicate requeue events) — AC-FR0300-02.

M-IMPL RED discipline: failures must classify as assertion_failure (no
stub_token, no assembly errors). The ``_recover`` guard converts the IF-RECOVER-001
NotImplementedError stub into an explicit behavioral AssertionError — "the
stub must be replaced by behavior" — which doubles as the regression pin against
falling back to the stub. Nothing here mocks the system under test: commands,
waits and leases are seeded through the production db/lease/waiting modules
into a real sqlite service.db and read back from the same store; only the
wall-clock backdating of one lease row simulates a lease that expired while the
process was down.

AC: FR-0298/FR-0300 — TRACKS-TRACE IF-RECOVER-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from tracks.supervisor import recover as recover_mod
from tracks.supervisor import waiting as waiting_mod
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.lease import acquire_lease, current_generation, is_fenced

_KNOWN_RESET_AT = "2030-01-01T00:05:00+00:00"
_BACKOFF = {
    "interval_s": 60,
    "cap_s": 900,
    "next_probe_at": "2030-01-01T00:01:00+00:00",
}
_PAST = "2000-01-01T00:00:00+00:00"


def _service_db(tmp_path: Path) -> ServiceDB:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    return ServiceDB(home)


def _recover(db: ServiceDB) -> dict:
    """Call recover_on_startup; the IF-RECOVER-001 stub becomes an assertion."""
    try:
        return recover_mod.recover_on_startup(db)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-RECOVER-001 recover_on_startup is still a stub: "
            f"{exc} — startup recovery must requeue claimed commands, keep "
            "persisted waits, and expire stale leases"
        ) from None


def _events(db: ServiceDB, event_type: str) -> list:
    return [event for event in db.read_events() if event["type"] == event_type]


def _rows(home: Path, sql: str, params: tuple = ()) -> list:
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        return conn.execute(sql, params).fetchall()


def _register(db: ServiceDB, command_id: str, run_id: str) -> None:
    db.register_command(
        {
            "command_id": command_id,
            "kind": "drive_run",
            "params_json": json.dumps({"run_id": run_id}),
            "params_digest": "p",
            "idempotency_key": f"k-{command_id}",
            "actor": "system",
            "actor_class": "system",
            "surface": "internal",
            "project_id": None,
            "run_id": run_id,
        }
    )


def _claim(db: ServiceDB, command_id: str, generation: int = 1, worker: str = "worker-a") -> None:
    assert db.claim_command(command_id, worker, generation)


def _complete(db: ServiceDB, command_id: str, generation: int = 1) -> None:
    assert db.complete_command(command_id, generation, {"ok": True}, None)


def _backdate_lease(db: ServiceDB, run_id: str) -> None:
    """Simulate a lease row whose expires_at lapsed while the process was down."""
    with contextlib.closing(sqlite3.connect(db.home / "service.db")) as conn:
        conn.execute(
            "UPDATE leases SET expires_at = ? WHERE run_id = ?", (_PAST, run_id)
        )
        conn.commit()


def _enter_wait(db: ServiceDB, run_id: str, spec: dict) -> None:
    waiting_mod.enter_wait(db, run_id, spec)


# -- summary shape: the documented recovery contract ---------------------------


# AC-FR0300-02@v0.9 TRACKS-TRACE IF-RECOVER-001 recovery summary carries all three lists
def test_recover_summary_shape(tmp_path: Path):
    db = _service_db(tmp_path)
    summary = _recover(db)
    assert set(summary) == {"requeued", "waits_kept", "leases_expired"}
    assert summary["requeued"] == []
    assert summary["waits_kept"] == []
    assert summary["leases_expired"] == []


# -- claimed commands: requeue, same command_id, no loss/no duplication ---------


# AC-FR0300-01@v0.9 TRACKS-TRACE IF-RECOVER-001 claimed command requeued on restart
def test_recover_requeues_claimed_command(tmp_path: Path):
    db = _service_db(tmp_path)
    _register(db, "CMD-1", "R1")
    _claim(db, "CMD-1", generation=1, worker="worker-a")
    summary = _recover(db)
    assert "CMD-1" in summary["requeued"]
    row = db.get_command("CMD-1")
    assert row is not None
    assert row["status"] == "accepted"
    assert row["claim_generation"] is None
    requeued = _events(db, "command.requeued")
    assert len(requeued) == 1
    assert requeued[0]["command_id"] == "CMD-1"
    assert requeued[0]["payload"]["command_id"] == "CMD-1"
    assert requeued[0]["payload"]["reason"] == "service_restart"


# AC-FR0300-01@v0.9 TRACKS-TRACE IF-RECOVER-001 terminal commands are not resurrected
def test_recover_does_not_resurrect_completed_command(tmp_path: Path):
    db = _service_db(tmp_path)
    _register(db, "CMD-1", "R1")
    _claim(db, "CMD-1", generation=1)
    _complete(db, "CMD-1", generation=1)
    _register(db, "CMD-2", "R2")
    _claim(db, "CMD-2", generation=1)
    _complete(db, "CMD-2", generation=1)
    summary = _recover(db)
    assert summary["requeued"] == []
    assert db.get_command("CMD-1")["status"] == "completed"
    assert db.get_command("CMD-2")["status"] == "completed"
    assert _events(db, "command.requeued") == []


# AC-FR0300-02@v0.9 TRACKS-TRACE IF-RECOVER-001 recovery is idempotent
def test_recover_is_idempotent_on_second_call(tmp_path: Path):
    db = _service_db(tmp_path)
    _register(db, "CMD-1", "R1")
    _claim(db, "CMD-1", generation=1)
    first = _recover(db)
    assert "CMD-1" in first["requeued"]
    second = _recover(db)
    assert second["requeued"] == []
    assert len(_events(db, "command.requeued")) == 1


# -- waits: durable, retry_at preserved across the restart ----------------------


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-RECOVER-001 known-reset retry_at survives restart
def test_recover_keeps_known_reset_wait_retry_at(tmp_path: Path):
    db = _service_db(tmp_path)
    _enter_wait(
        db,
        "R1",
        {
            "wait_class": "quota",
            "reason": "429 quota exceeded",
            "retry_at": _KNOWN_RESET_AT,
            "known_reset": True,
            "backoff": None,
        },
    )
    _recover(db)
    loaded = waiting_mod.load_wait(db, "R1")
    assert loaded is not None
    assert loaded.wait_class == "quota"
    assert loaded.known_reset is True
    assert loaded.retry_at == _KNOWN_RESET_AT
    assert loaded.backoff is None
    assert _events(db, "wait.resolved") == []


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-RECOVER-001 unknown-reset probe plan survives restart
def test_recover_keeps_unknown_reset_backoff_plan(tmp_path: Path):
    db = _service_db(tmp_path)
    _enter_wait(
        db,
        "R1",
        {
            "wait_class": "quota",
            "reason": "429 rate limited",
            "retry_at": None,
            "known_reset": False,
            "backoff": dict(_BACKOFF),
        },
    )
    _recover(db)
    loaded = waiting_mod.load_wait(db, "R1")
    assert loaded is not None
    assert loaded.retry_at is None
    assert loaded.known_reset is False
    assert loaded.backoff == _BACKOFF


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-RECOVER-001 waits_kept enumerates preserved waits
def test_recover_reports_kept_waits(tmp_path: Path):
    db = _service_db(tmp_path)
    _enter_wait(
        db,
        "R1",
        {
            "wait_class": "ci",
            "reason": "ci-running",
            "retry_at": _KNOWN_RESET_AT,
            "known_reset": True,
            "backoff": None,
        },
    )
    _enter_wait(
        db,
        "R2",
        {
            "wait_class": "network",
            "reason": "connection reset by peer",
            "retry_at": None,
            "known_reset": False,
            "backoff": dict(_BACKOFF),
        },
    )
    summary = _recover(db)
    assert set(summary["waits_kept"]) == {"R1", "R2"}
    rows = _rows(db.home, "SELECT COUNT(*) FROM waits")
    assert rows[0][0] == 2


# -- leases: stale leases expired, live leases untouched (fence preserved) ------


# AC-FR0300-01@v0.9 TRACKS-TRACE IF-RECOVER-001 stale lease expired, fresh generation fenced
def test_recover_expires_stale_lease_and_preserves_fence(tmp_path: Path):
    db = _service_db(tmp_path)
    old = acquire_lease(db, "R1", "worker-a", 30)
    _backdate_lease(db, "R1")
    summary = _recover(db)
    assert "R1" in summary["leases_expired"]
    released = [
        event
        for event in _events(db, "lease.released")
        if (event["payload"] or {}).get("run_id") == "R1"
    ]
    assert released, "IF-RECOVER-001: an expired lease must be released with an audit event"
    payload = released[-1]["payload"]
    assert payload["reason"] == "expired"
    fresh = acquire_lease(db, "R1", "worker-b", 30)
    assert fresh.generation == old.generation + 1
    assert current_generation(db, "R1") == old.generation + 1
    assert is_fenced(db, "R1", old.generation) is True


# AC-FR0300-02@v0.9 TRACKS-TRACE IF-RECOVER-001 live lease untouched by recovery
def test_recover_does_not_expire_live_lease(tmp_path: Path):
    db = _service_db(tmp_path)
    live = acquire_lease(db, "R1", "worker-a", 30)
    summary = _recover(db)
    assert "R1" not in summary["leases_expired"]
    assert _events(db, "lease.released") == []
    assert current_generation(db, "R1") == live.generation
    assert is_fenced(db, "R1", live.generation) is False
