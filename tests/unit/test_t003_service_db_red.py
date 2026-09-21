"""T-003 RED re-pin (attempt 3): ServiceDB-backed wait durability through the
waiting-module read-back contract (IF-WAIT-001).

Re-pin directive (DIAGNOSE failure 9-prism-35, round 9): the batch-2 RED
pinned raw ServiceDB faces (get_wait / list_waits / lease / schedule) with no
production caller outside the RED file and no acceptance anchor, so it was
judged red_defect and must be re-pinned by its author. This re-pin keeps the
same delivery obligation — the service.db wait read faces enter through this
task (db.py closes alongside waiting.py) — but pins it where the production
consumer and the acceptance anchor live:

- ``waiting.load_wait(db, run_id)``: the production read-back the supervisor
  consumes to learn the persisted wake-up — the "持久保存 / 到达 reset 时间后
  无人工干预自动续跑" half of AC-FR0299-01, and the bounded probe-plan
  read-back of AC-FR0299-02 / AC-NFR0152-01 (no fabricated countdown).
- ``waiting.active_wait_runs(db)``: the enumeration the startup recovery
  consumes to keep waits across a restart — the "服务重启后 retry_at 保留" half
  of AC-FR0298-01.

Lease/schedule storage faces are deliberately NOT pinned here: they have no
production consumer until T-007/T-015 and no acceptance anchor in this task.

M-IMPL RED discipline: every failing node carries a real assertion signal —
the ``_read_back`` / ``_sweep`` guards raise ``AssertionError`` (plain
``assert``) when the read-back is missing, so the records classify as
assertion_failure rather than unclassified (round 11 F-T003-RED-01). No
stub_token, no assembly errors. Nothing here mocks the system under test:
waits are persisted into the real ServiceDB-backed service.db and read back
through the production module.

AC: FR-0298/FR-0299/NFR-0152 — TRACKS-TRACE IF-WAIT-001.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor import waiting as waiting_mod
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.waiting import WaitSpec

_KNOWN_RESET_AT = "2030-01-01T00:05:00+00:00"
_UNKNOWN_BACKOFF = {
    "interval_s": 60,
    "cap_s": 900,
    "next_probe_at": "2030-01-01T00:01:00+00:00",
}


def _service_db(tmp_path: Path) -> ServiceDB:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    return ServiceDB(home)


def _read_back(db: ServiceDB, run_id: str):
    """Call waiting.load_wait; a missing read-back raises AssertionError."""
    reader = getattr(waiting_mod, "load_wait", None)
    assert reader is not None, (
        "waiting.load_wait missing — the persisted retry_at must be readable "
        "for the reset-time auto-resume (FR-0299)"
    )
    return reader(db, run_id)


def _sweep(db: ServiceDB):
    """Call waiting.active_wait_runs; a missing sweep raises AssertionError."""
    sweep = getattr(waiting_mod, "active_wait_runs", None)
    assert sweep is not None, (
        "waiting.active_wait_runs missing — the restart recovery must "
        "enumerate persisted waits (FR-0298)"
    )
    return sweep(db)


def _enter(db: ServiceDB, run_id: str, spec: dict) -> None:
    waiting_mod.enter_wait(db, run_id, spec)


# AC-FR0299-01@v0.9 TRACKS-TRACE IF-WAIT-001 known-reset read-back keeps retry_at
def test_load_wait_returns_known_reset_spec(tmp_path: Path):
    """AC-FR0299-01: a persisted known-reset quota wait reads back with its
    exact retry_at so the supervisor can wake without a human signal."""
    db = _service_db(tmp_path)
    _enter(
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
    loaded = _read_back(db, "R1")
    assert isinstance(loaded, WaitSpec)
    assert loaded.wait_class == "quota"
    assert loaded.known_reset is True
    assert loaded.retry_at == _KNOWN_RESET_AT
    assert loaded.backoff is None


# AC-FR0299-02@v0.9 TRACKS-TRACE IF-WAIT-001 unknown-reset read-back probe plan
# AC-NFR0152-01@v0.9 TRACKS-TRACE IF-WAIT-001 backoff口径 readable
def test_load_wait_returns_unknown_reset_probe_plan(tmp_path: Path):
    """AC-FR0299-02 / AC-NFR0152-01: an unknown-reset wait reads back as the
    bounded probe plan (60s -> 900s) with no retry_at and no countdown."""
    db = _service_db(tmp_path)
    _enter(
        db,
        "R1",
        {
            "wait_class": "quota",
            "reason": "429 rate limited",
            "retry_at": None,
            "known_reset": False,
            "backoff": dict(_UNKNOWN_BACKOFF),
        },
    )
    loaded = _read_back(db, "R1")
    assert isinstance(loaded, WaitSpec)
    assert loaded.known_reset is False
    assert loaded.retry_at is None
    assert loaded.backoff is not None
    assert loaded.backoff["interval_s"] == 60
    assert loaded.backoff["cap_s"] == 900
    assert "next_probe_at" in loaded.backoff
    assert "countdown" not in str(loaded.backoff).lower()


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 absent wait reads back none
def test_load_wait_absent_returns_none(tmp_path: Path):
    """AC-FR0298-01: a run with no active wait reads back None (no invented
    wait state)."""
    db = _service_db(tmp_path)
    assert _read_back(db, "R-absent") is None


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 resolve clears the read-back
def test_load_wait_none_after_resolve(tmp_path: Path):
    """AC-FR0298-01: after resolve_wait the read-back is None (wait cleared)."""
    db = _service_db(tmp_path)
    _enter(
        db,
        "R1",
        {
            "wait_class": "ci",
            "reason": "ci-running",
            "retry_at": None,
            "known_reset": False,
            "backoff": None,
        },
    )
    waiting_mod.resolve_wait(db, "R1", "condition_met")
    assert _read_back(db, "R1") is None


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 restart sweep enumerates waits
def test_active_wait_runs_lists_persisted_waits(tmp_path: Path):
    """AC-FR0298-01: the restart sweep lists every run with an active wait so
    retry_at survives the restart."""
    db = _service_db(tmp_path)
    _enter(
        db,
        "R1",
        {
            "wait_class": "quota",
            "reason": "429 rate limited",
            "retry_at": None,
            "known_reset": False,
            "backoff": dict(_UNKNOWN_BACKOFF),
        },
    )
    _enter(
        db,
        "R2",
        {
            "wait_class": "ci",
            "reason": "ci-running",
            "retry_at": _KNOWN_RESET_AT,
            "known_reset": True,
            "backoff": None,
        },
    )
    assert set(_sweep(db)) == {"R1", "R2"}


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 sweep empty without waits
def test_active_wait_runs_empty_without_waits(tmp_path: Path):
    """AC-FR0298-01: with no persisted waits the restart sweep is empty."""
    db = _service_db(tmp_path)
    assert list(_sweep(db)) == []


# guard: pytest import is the collection anchor for fixture-based probes
_ = pytest
