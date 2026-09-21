"""T-007 RED: lease/generation fencing for the single effective executor
(IF-LEASE-001).

Devon-owned unit RED for the T-007 delivery slice: ``tracks/supervisor/lease.py``
(architecture row 13: 租约/代次、完成 CAS、迟到结果隔离). The failing nodes pin
the IF-LEASE-001 contracts this task must deliver:

- acquire/renew grants a monotonic per-run generation computed inside a SQLite
  transaction and persists the single ``leases`` row (interfaces §1c #5, §1i:
  generation = previous + 1; one effective executor per run) — AC-FR0296-01;
- ``current_generation`` / ``is_fenced`` expose the fencing floor (0 = no lease
  ever) so a superseded generation is quarantined instead of driving the run —
  AC-FR0296-02;
- ``release_lease`` is itself generation-fenced and audited: a late release from
  a superseded worker cannot clear the current lease, and the generation
  counter never rolls back across release/re-acquire — AC-FR0296-02;
- a stale-generation outcome is quarantined with the auditable
  ``worker.late_result(disposition=quarantined)`` event carrying the current
  generation, leaving lease state untouched (interfaces §1a #17) —
  AC-FR0296-02 / AC-FR0312-02.

The scaffold bodies of lease.py still raise their IF-LEASE-001 stub token and
``quarantine_late_result`` is not scaffolded yet; every failing node deliberately
guards that stub/symbol state into a real ``AssertionError`` (``raise ... from
None`` / plain ``assert``) so the records classify as assertion_failure — no
stub_token, no assembly errors, no ``pytest.fail`` (whose ``Failed:`` line
classifies unclassified). Nothing here mocks the system under test: leases are
persisted into the real ServiceDB-backed service.db through the production
module and read back from the same store.

AC: FR-0296/FR-0312 — TRACKS-TRACE IF-LEASE-001.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from tracks.supervisor import lease as lease_mod
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.lease import Lease


def _service_db(tmp_path: Path) -> ServiceDB:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    return ServiceDB(home)


def _rows(home: Path, sql: str, params: tuple = ()) -> list:
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        return conn.execute(sql, params).fetchall()


def _events(db: ServiceDB, event_type: str, run_id: str) -> list:
    return [
        event
        for event in db.read_events(run_id=run_id)
        if event["type"] == event_type
    ]


def _acquire(db: ServiceDB, run_id: str, worker_id: str, ttl_s: int) -> Lease:
    """Call acquire_lease; the IF-LEASE-001 stub becomes a behavioral assertion."""
    try:
        return lease_mod.acquire_lease(db, run_id, worker_id, ttl_s)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-LEASE-001 acquire_lease is still a stub: "
            f"{exc} — a lease must be granted with a monotonic generation"
        ) from None


def _release(db: ServiceDB, lease: Lease, reason: str) -> None:
    """Call release_lease; the IF-LEASE-001 stub becomes an assertion."""
    try:
        lease_mod.release_lease(db, lease, reason)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-LEASE-001 release_lease is still a stub: "
            f"{exc} — a release must be generation-fenced and audited"
        ) from None


def _current(db: ServiceDB, run_id: str) -> int:
    """Call current_generation; the IF-LEASE-001 stub becomes an assertion."""
    try:
        return lease_mod.current_generation(db, run_id)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-LEASE-001 current_generation is still a stub: "
            f"{exc} — the fencing floor must be readable"
        ) from None


def _fenced(db: ServiceDB, run_id: str, generation: int) -> bool:
    """Call is_fenced; the IF-LEASE-001 stub becomes an assertion."""
    try:
        return lease_mod.is_fenced(db, run_id, generation)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-LEASE-001 is_fenced is still a stub: "
            f"{exc} — stale generations must be detectable"
        ) from None


def _quarantine(db: ServiceDB, run_id: str, command_id: str, generation: int) -> bool:
    """Call quarantine_late_result; missing seam or stub asserts behaviorally."""
    quarantine = getattr(lease_mod, "quarantine_late_result", None)
    assert callable(quarantine), (
        "IF-LEASE-001 quarantine_late_result missing — a stale-generation "
        "outcome must be quarantined with an auditable worker.late_result "
        "(FR-0296/FR-0312)"
    )
    try:
        return quarantine(db, run_id, command_id, generation)
    except NotImplementedError as exc:
        raise AssertionError(
            "IF-LEASE-001 quarantine_late_result is still a stub: "
            f"{exc} — late results must be quarantined and audited"
        ) from None


# -- acquire_lease: monotonic generation, single row per run (§1i, §1c #5) ------


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 first acquire is generation one
def test_acquire_lease_first_generation_is_one_and_persists(tmp_path: Path):
    db = _service_db(tmp_path)
    lease = _acquire(db, "R1", "worker-a", 30)
    assert isinstance(lease, Lease)
    assert lease.run_id == "R1"
    assert lease.worker_id == "worker-a"
    assert lease.generation == 1
    assert lease.ttl_s == 30
    rows = _rows(
        tmp_path / "service-home",
        "SELECT worker_id, generation FROM leases WHERE run_id = ?",
        ("R1",),
    )
    assert len(rows) == 1
    assert rows[0][0] == "worker-a"
    assert rows[0][1] == 1
    events = _events(db, "lease.acquired", "R1")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["run_id"] == "R1"
    assert payload["worker_id"] == "worker-a"
    assert payload["generation"] == 1
    assert payload["ttl_s"] == 30


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 generation = previous + 1
def test_acquire_lease_generations_are_monotonic(tmp_path: Path):
    db = _service_db(tmp_path)
    generations = [
        _acquire(db, "R1", f"worker-{index}", 30).generation for index in (1, 2, 3)
    ]
    assert generations == [1, 2, 3]
    events = _events(db, "lease.acquired", "R1")
    assert [event["payload"]["generation"] for event in events] == [1, 2, 3]


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 at most one effective lease per run
def test_acquire_lease_keeps_single_row_per_run(tmp_path: Path):
    db = _service_db(tmp_path)
    _acquire(db, "R1", "worker-a", 30)
    second = _acquire(db, "R1", "worker-b", 30)
    assert second.generation == 2
    rows = _rows(
        tmp_path / "service-home",
        "SELECT worker_id, generation FROM leases WHERE run_id = ?",
        ("R1",),
    )
    assert len(rows) == 1
    assert (rows[0][0], rows[0][1]) == ("worker-b", 2)
    total = _rows(tmp_path / "service-home", "SELECT COUNT(*) FROM leases")
    assert total[0][0] == 1


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 generation counters are per run
def test_acquire_lease_generations_are_scoped_per_run(tmp_path: Path):
    db = _service_db(tmp_path)
    assert _acquire(db, "R1", "worker-a", 30).generation == 1
    assert _acquire(db, "R2", "worker-x", 30).generation == 1
    assert _acquire(db, "R1", "worker-a", 30).generation == 2
    assert _acquire(db, "R2", "worker-x", 30).generation == 2


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 expiry window follows ttl_s
def test_acquire_lease_expiry_window_follows_ttl(tmp_path: Path):
    db = _service_db(tmp_path)
    lease = _acquire(db, "R1", "worker-a", 45)
    rows = _rows(
        tmp_path / "service-home",
        "SELECT acquired_at, expires_at FROM leases WHERE run_id = ?",
        ("R1",),
    )
    acquired_at, expires_at = rows[0]
    delta = (
        datetime.fromisoformat(expires_at) - datetime.fromisoformat(acquired_at)
    ).total_seconds()
    assert 0 < delta <= lease.ttl_s


# -- current_generation / is_fenced: the fencing floor (§1i 防旧) ----------------


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 no lease ever reads zero
def test_current_generation_is_zero_without_lease(tmp_path: Path):
    db = _service_db(tmp_path)
    assert _current(db, "R-never") == 0


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 current generation tracks latest
def test_current_generation_tracks_latest(tmp_path: Path):
    db = _service_db(tmp_path)
    assert _current(db, "R1") == 0
    _acquire(db, "R1", "worker-a", 30)
    assert _current(db, "R1") == 1
    _acquire(db, "R1", "worker-b", 30)
    assert _current(db, "R1") == 2


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 superseded generation is fenced
def test_is_fenced_true_only_for_superseded_generation(tmp_path: Path):
    db = _service_db(tmp_path)
    first = _acquire(db, "R1", "worker-a", 30)
    assert _fenced(db, "R1", first.generation) is False
    second = _acquire(db, "R1", "worker-b", 30)
    assert _fenced(db, "R1", second.generation) is False
    assert _fenced(db, "R1", first.generation) is True


# -- release_lease: audited and generation-fenced (§1a #16, §1i) -----------------


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 release is audited with its reason
def test_release_lease_emits_released_event_with_reason(tmp_path: Path):
    db = _service_db(tmp_path)
    lease = _acquire(db, "R1", "worker-a", 30)
    _release(db, lease, "completed")
    events = _events(db, "lease.released", "R1")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["run_id"] == "R1"
    assert payload["worker_id"] == "worker-a"
    assert payload["generation"] == 1
    assert payload["reason"] == "completed"


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 release never rolls back the counter
def test_release_then_reacquire_never_reuses_generation(tmp_path: Path):
    db = _service_db(tmp_path)
    first = _acquire(db, "R1", "worker-a", 30)
    _release(db, first, "completed")
    second = _acquire(db, "R1", "worker-b", 30)
    assert second.generation == 2
    assert _current(db, "R1") == 2
    assert _fenced(db, "R1", first.generation) is True


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 a late release cannot clear the lease
def test_stale_release_does_not_clear_current_lease(tmp_path: Path):
    db = _service_db(tmp_path)
    first = _acquire(db, "R1", "worker-a", 30)
    _acquire(db, "R1", "worker-b", 30)
    _release(db, first, "expired")
    assert _current(db, "R1") == 2
    assert _fenced(db, "R1", first.generation) is True
    assert _acquire(db, "R1", "worker-c", 30).generation == 3


# AC-FR0296-01@v0.9 TRACKS-TRACE IF-LEASE-001 concurrent acquires stay monotonic
def test_concurrent_acquires_yield_distinct_monotonic_generations(tmp_path: Path):
    db = _service_db(tmp_path)
    barrier = threading.Barrier(2)
    captured: list = []

    def _drive(worker_id: str) -> None:
        barrier.wait(timeout=5)
        try:
            captured.append(_acquire(db, "R1", worker_id, 30).generation)
        except Exception as exc:  # surfaced by the assertions below, never swallowed
            captured.append(exc)

    threads = [
        threading.Thread(target=_drive, args=(f"worker-{index}",)) for index in (1, 2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(captured) == 2
    assert all(isinstance(value, int) for value in captured), (
        f"assertion failure: concurrent acquire did not grant a generation "
        f"({captured!r})"
    )
    assert sorted(captured) == [1, 2]
    assert _current(db, "R1") == 2


# -- late-result quarantine: stale generation audited, state untouched -----------


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 stale outcome quarantined + audited
# AC-FR0312-02@v0.9 TRACKS-TRACE IF-LEASE-001 late result cannot drive the run
def test_late_result_from_stale_generation_is_quarantined_audited(tmp_path: Path):
    db = _service_db(tmp_path)
    first = _acquire(db, "R1", "worker-a", 30)
    _acquire(db, "R1", "worker-b", 30)
    assert _quarantine(db, "R1", "CMD-7", first.generation) is True
    events = _events(db, "worker.late_result", "R1")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["run_id"] == "R1"
    assert payload["command_id"] == "CMD-7"
    assert payload["generation"] == first.generation
    assert payload["current_generation"] == 2
    assert payload["disposition"] == "quarantined"
    assert _current(db, "R1") == 2
    assert _fenced(db, "R1", first.generation) is True


# AC-FR0296-02@v0.9 TRACKS-TRACE IF-LEASE-001 current outcome is not quarantined
def test_late_result_from_current_generation_is_not_quarantined(tmp_path: Path):
    db = _service_db(tmp_path)
    lease = _acquire(db, "R1", "worker-a", 30)
    assert _quarantine(db, "R1", "CMD-8", lease.generation) is False
    assert _events(db, "worker.late_result", "R1") == []
