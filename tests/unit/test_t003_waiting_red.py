"""T-003 RED: waiting/quota/backoff contract (IF-WAIT-001).

Devon-owned unit RED for the waiting slice the task declares:
``tracks/supervisor/waiting.py`` (classify_wait five-class closed set,
enter/resolve_wait persistence, next_probe bounded backoff per NFR-0152,
interfaces §1j). The failing nodes pin the IF-WAIT-001 contracts this task
must deliver:

- classify_wait closed set: ci/quota/network/agent/external, known-reset
  quota carries retry_at exact wake, unknown-reset quota carries a backoff
  probe plan (never a fabricated countdown), unrecoverable returns None
  (AC-FR0299-01/AC-FR0299-02);
- enter/resolve_wait persist and clear the active waits row and emit
  wait.entered/wait.resolved (§1a #13/#14; AC-FR0298-01 persistence tier);
- next_probe doubles from 60s to the 900s cap and never exceeds it; the
  WaitPolicy defaults are the NFR-0152 locked values
  (AC-NFR0152-01 backoff tier).

M-IMPL RED discipline: failures must classify as assertion_failure (no
stub_token, no assembly errors). The call guards below convert a
NotImplementedError("IF-WAIT-001") stub into an explicit behavioral
assertion failure — "the stub must be replaced by behavior" — which doubles
as the regression pin against falling back to the stub. Nothing here mocks
the system under test; waits are seeded in a real sqlite service.db.

AC: FR-0298/FR-0299/NFR-0152 — TRACKS-TRACE IF-WAIT-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

import pytest

from tracks.supervisor import waiting as waiting_mod
from tracks.supervisor.waiting import WaitPolicy

_TS = "2026-09-20T00:00:00+00:00"

_WAIT_SCHEMA = (
    "CREATE TABLE service_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,"
    " type TEXT, project_id TEXT, run_id TEXT, command_id TEXT, payload TEXT);"
    "CREATE TABLE waits (run_id TEXT PRIMARY KEY, wait_class TEXT, reason TEXT,"
    " retry_at TEXT, known_reset INTEGER, backoff_json TEXT, entered_at TEXT);"
)


def _connect(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "service.db")


def _rows(home: Path, sql: str, params: tuple = ()) -> list:
    with contextlib.closing(_connect(home)) as conn:
        return conn.execute(sql, params).fetchall()


def _classify(failure: dict):
    try:
        return waiting_mod.classify_wait(failure)
    except NotImplementedError as exc:
        pytest.fail(f"IF-WAIT-001 still stub: {exc} — classify must return behavior")


def _enter(db, run_id: str, spec) -> None:
    try:
        waiting_mod.enter_wait(db, run_id, spec)
    except NotImplementedError as exc:
        pytest.fail(f"IF-WAIT-001 still stub: {exc} — enter must persist behavior")


def _resolve(db, run_id: str, resolved_by: str) -> None:
    try:
        waiting_mod.resolve_wait(db, run_id, resolved_by)
    except NotImplementedError as exc:
        pytest.fail(f"IF-WAIT-001 still stub: {exc} — resolve must clear behavior")


def _probe(policy: WaitPolicy, backoff: dict | None) -> dict:
    try:
        return waiting_mod.next_probe(policy, backoff)
    except NotImplementedError as exc:
        pytest.fail(f"IF-WAIT-001 still stub: {exc} — backoff must compute behavior")


class _Db:
    """Minimal ServiceDB-shaped stand-in over the seeded sqlite file.

    Only exposes what waiting needs: execute/commit for the waits table plus
    append_event for the §1a wait.entered/wait.resolved audit events. Uses the
    real sqlite file so persistence crosses connections.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self._conn = sqlite3.connect(home / "service.db")
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params: tuple = ()):
        cursor = self._conn.execute(sql, params)
        self._conn.commit()
        return cursor

    def append_event(
        self,
        type: str,
        payload: dict,
        *,
        project_id=None,
        run_id=None,
        command_id=None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO service_events (ts, type, project_id, run_id,"
            " command_id, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (_TS, type, project_id, run_id, command_id, json.dumps(payload or {})),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def close(self) -> None:
        self._conn.close()


@pytest.fixture
def wait_home(tmp_path: Path) -> Path:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(_connect(home)) as conn:
        conn.executescript(_WAIT_SCHEMA)
        conn.commit()
    return home


@pytest.fixture
def wait_db(wait_home: Path):
    db = _Db(wait_home)
    yield db
    db.close()


# -- classify_wait: closed wait_class set (§1j) ---------------------------------


# AC-FR0299-01@v0.9 TRACKS-TRACE IF-WAIT-001 known-reset quota exact wake
def test_classify_known_reset_quota_carries_retry_at():
    reset_at = "2026-09-20T01:00:00+00:00"
    spec = _classify(
        {
            "kind": "provider_quota",
            "retry_after": reset_at,
            "stderr": "provider returned 429 quota exceeded, reset at 2026-09-20T01:00:00Z",
        }
    )
    assert spec is not None
    assert spec.wait_class == "quota"
    assert spec.known_reset is True
    assert spec.retry_at == reset_at
    assert spec.backoff is None
    assert "countdown" not in (spec.reason or "").lower() or spec.retry_at is not None


# AC-FR0299-02@v0.9 TRACKS-TRACE IF-WAIT-001 unknown-reset quota probe plan
def test_classify_unknown_reset_quota_carries_backoff_no_countdown():
    spec = _classify(
        {
            "kind": "provider_quota",
            "stderr": "provider returned 429 rate limited, retry unknown",
        }
    )
    assert spec is not None
    assert spec.wait_class == "quota"
    assert spec.known_reset is False
    assert spec.retry_at is None
    assert spec.backoff is not None
    assert spec.backoff.get("interval_s") == 60
    assert spec.backoff.get("cap_s") == 900
    assert "next_probe_at" in spec.backoff


# AC-FR0299-02@v0.9 TRACKS-TRACE IF-WAIT-001 no fabricated countdown field
def test_classify_unknown_reset_has_no_countdown_token():
    spec = _classify({"kind": "provider_quota", "stderr": "429 rate_limit exceeded"})
    assert spec is not None
    assert spec.known_reset is False
    rendered = json.dumps(
        {
            "retry_at": spec.retry_at,
            "backoff": spec.backoff,
            "reason": spec.reason,
        }
    )
    assert "countdown" not in rendered.lower()
    assert "retry_at" not in rendered or spec.retry_at is None


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 five-class closed set
def test_classify_wait_closed_set_covers_five_classes():
    cases = {
        "ci": {"kind": "ci_timeout", "stderr": "CI job still running"},
        "quota": {"kind": "provider_quota", "stderr": "429 quota exceeded"},
        "network": {"kind": "network_error", "stderr": "connection reset by peer"},
        "agent": {"kind": "agent_busy", "stderr": "agent still producing output"},
        "external": {"kind": "external_blocked", "stderr": "waiting on external signal"},
    }
    seen = set()
    for failure in cases.values():
        spec = _classify(dict(failure))
        assert spec is not None
        seen.add(spec.wait_class)
    assert seen == {"ci", "quota", "network", "agent", "external"}


# AC-FR0299-01@v0.9 TRACKS-TRACE IF-WAIT-001 unrecoverable is not a wait
def test_classify_unrecoverable_returns_none():
    assert _classify({"kind": "unrecoverable", "stderr": "auth revoked"}) is None
    assert (
        _classify({"kind": "config_error", "stderr": "invalid command params"}) is None
    )


# -- enter/resolve_wait: persistence (§1a #13/#14, §1c waits) --------------------


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 enter persists row and event
def test_enter_wait_persists_row_and_entered_event(wait_db, wait_home: Path):
    spec = _classify(
        {
            "kind": "provider_quota",
            "retry_after": "2026-09-20T01:00:00+00:00",
            "stderr": "429 quota exceeded",
        }
    )
    assert spec is not None
    _enter(wait_db, "R1", spec)
    rows = _rows(
        wait_home,
        "SELECT wait_class, reason, retry_at, known_reset FROM waits WHERE run_id = ?",
        ("R1",),
    )
    assert len(rows) == 1
    assert rows[0][0] == "quota"
    assert isinstance(rows[0][1], str) and rows[0][1].strip() != ""
    assert rows[0][2] == "2026-09-20T01:00:00+00:00"
    assert rows[0][3] == 1
    events = _rows(
        wait_home,
        "SELECT type, run_id FROM service_events ORDER BY seq",
    )
    assert ("wait.entered", "R1") in events


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 one active wait per run
def test_enter_wait_second_enter_replaces_active_row(wait_db, wait_home: Path):
    first = _classify({"kind": "network_error", "stderr": "connection reset"})
    second = _classify({"kind": "provider_quota", "stderr": "429 quota exceeded"})
    assert first is not None and second is not None
    _enter(wait_db, "R1", first)
    _enter(wait_db, "R1", second)
    rows = _rows(wait_home, "SELECT COUNT(*) FROM waits WHERE run_id = ?", ("R1",))
    assert rows[0][0] == 1
    current = _rows(wait_home, "SELECT wait_class FROM waits WHERE run_id = ?", ("R1",))
    assert current[0][0] == "quota"


# AC-FR0298-01@v0.9 TRACKS-TRACE IF-WAIT-001 resolve clears row and emits event
def test_resolve_wait_clears_row_and_resolved_event(wait_db, wait_home: Path):
    spec = _classify({"kind": "network_error", "stderr": "connection reset"})
    assert spec is not None
    _enter(wait_db, "R1", spec)
    _resolve(wait_db, "R1", "retry_at_reached")
    rows = _rows(wait_home, "SELECT COUNT(*) FROM waits WHERE run_id = ?", ("R1",))
    assert rows[0][0] == 0
    events = _rows(
        wait_home,
        "SELECT type, payload FROM service_events WHERE type = 'wait.resolved'",
    )
    assert len(events) == 1
    payload = json.loads(events[0][1])
    assert payload.get("resolved_by") == "retry_at_reached"
    assert payload.get("run_id") == "R1"


# -- next_probe: bounded exponential backoff (NFR-0152) --------------------------


# AC-NFR0152-01@v0.9 TRACKS-TRACE IF-WAIT-001 backoff starts at initial
def test_next_probe_first_interval_is_initial():
    state = _probe(WaitPolicy(60, 900, 5, 60), None)
    assert state["interval_s"] == 60
    assert state["cap_s"] == 900
    assert isinstance(state.get("next_probe_at"), str)


# AC-NFR0152-01@v0.9 TRACKS-TRACE IF-WAIT-001 backoff doubles then caps
def test_next_probe_doubles_until_cap():
    policy = WaitPolicy(60, 900, 5, 60)
    state = _probe(policy, None)
    intervals = [state["interval_s"]]
    for _ in range(6):
        state = _probe(policy, state)
        intervals.append(state["interval_s"])
    assert intervals[:5] == [60, 120, 240, 480, 900]
    assert all(value <= 900 for value in intervals)
    assert intervals[5] == 900


# AC-NFR0152-01@v0.9 TRACKS-TRACE IF-WAIT-001 backoff never exceeds cap
def test_next_probe_never_exceeds_cap():
    policy = WaitPolicy(60, 900, 5, 60)
    state = {"interval_s": 900, "cap_s": 900, "next_probe_at": _TS}
    for _ in range(3):
        state = _probe(policy, state)
        assert state["interval_s"] <= 900
        assert state["cap_s"] == 900
    assert state["interval_s"] == 900


# guard: pytest import is the collection anchor for fixture-based probes
_ = pytest
