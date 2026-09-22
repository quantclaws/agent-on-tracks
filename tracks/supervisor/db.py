"""Service-plane SQLite store: ``<service_home>/service.db`` (interfaces §1c).

Owns the service-plane schema (``service_events`` / ``projects`` /
``commands`` / ``waits`` / ``leases`` / ``auth`` / ``sessions`` /
``schedule``) and the append-only service event log. The server process is
the single writer; read paths open independent read-only connections
(NFR-0150). Run-plane business events stay in the project's tracks.db — this
module never writes there.

Contract tokens: IF-CMDSVC-001, IF-SERVE-001, IF-RECOVER-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# Closed set of service-plane event types (interfaces §1a). tracks.db
# EVENT_TYPES is NOT extended in v0.9 — the run-plane closed set is frozen.
SERVICE_EVENT_TYPES = (
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
)

COMMAND_STATUSES = ("accepted", "claimed", "completed", "failed", "rejected")

_DB_FILENAME = "service.db"

# Service-plane schema (interfaces §1c tables 1-8). Applied with
# CREATE TABLE IF NOT EXISTS so a store file seeded with a documented
# subset keeps its rows while missing tables are added.
_SCHEMA_SCRIPT = (
    "CREATE TABLE IF NOT EXISTS service_events ("
    " seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, type TEXT,"
    " project_id TEXT, run_id TEXT, command_id TEXT, payload TEXT);"
    "CREATE TABLE IF NOT EXISTS projects ("
    " project_id TEXT PRIMARY KEY, repo_path TEXT UNIQUE, version TEXT,"
    " registered_by TEXT, registered_at TEXT);"
    "CREATE TABLE IF NOT EXISTS commands ("
    " command_id TEXT PRIMARY KEY, kind TEXT, params_json TEXT,"
    " params_digest TEXT, idempotency_key TEXT UNIQUE, actor TEXT,"
    " actor_class TEXT, surface TEXT, project_id TEXT, run_id TEXT,"
    " status TEXT, claim_generation INTEGER, result_json TEXT,"
    " created_at TEXT, updated_at TEXT);"
    "CREATE TABLE IF NOT EXISTS waits ("
    " run_id TEXT PRIMARY KEY, wait_class TEXT, reason TEXT, retry_at TEXT,"
    " known_reset INTEGER, backoff_json TEXT, entered_at TEXT);"
    "CREATE TABLE IF NOT EXISTS leases ("
    " run_id TEXT PRIMARY KEY, worker_id TEXT, generation INTEGER,"
    " acquired_at TEXT, expires_at TEXT);"
    "CREATE TABLE IF NOT EXISTS auth ("
    " actor TEXT PRIMARY KEY, password_hash TEXT, created_at TEXT);"
    "CREATE TABLE IF NOT EXISTS sessions ("
    " token_hash TEXT PRIMARY KEY, actor TEXT, csrf_hash TEXT,"
    " created_at TEXT, expires_at TEXT);"
    "CREATE TABLE IF NOT EXISTS schedule ("
    " id INTEGER PRIMARY KEY CHECK (id = 1), active_run TEXT, queue_json TEXT);"
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServiceDB:
    """Single-writer service-plane store (interfaces §1c tables 1-8)."""

    def __init__(self, home: Path) -> None:
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(_SCHEMA_SCRIPT)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.home / _DB_FILENAME))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def append_event(
        self,
        type: str,
        payload: dict,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        command_id: str | None = None,
    ) -> int:
        """Append one service event (type in SERVICE_EVENT_TYPES) -> seq."""
        if type not in SERVICE_EVENT_TYPES:
            raise ValueError(f"unknown service event type: {type!r}")
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                "INSERT INTO service_events (ts, type, project_id, run_id,"
                " command_id, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (_utcnow(), type, project_id, run_id, command_id, json.dumps(payload or {})),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def read_events(self, *, run_id: str | None = None, after_seq: int = 0) -> list:
        """Read service events in seq order (read-only consumers)."""
        sql = (
            "SELECT seq, ts, type, project_id, run_id, command_id, payload"
            " FROM service_events WHERE seq > ?"
        )
        args: list = [after_seq]
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        sql += " ORDER BY seq"
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(sql, args).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            raw = event.get("payload")
            with contextlib.suppress(ValueError, TypeError):
                event["payload"] = json.loads(raw) if raw else {}
            events.append(event)
        return events

    def register_command(self, command: dict) -> str:
        """Persist a command row (status=accepted) and return command_id."""
        now = _utcnow()
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO commands (command_id, kind, params_json, params_digest,"
                " idempotency_key, actor, actor_class, surface, project_id, run_id,"
                " status, claim_generation, result_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', NULL, NULL, ?, ?)",
                (
                    command["command_id"],
                    command.get("kind"),
                    command.get("params_json"),
                    command.get("params_digest"),
                    command.get("idempotency_key"),
                    command.get("actor"),
                    command.get("actor_class"),
                    command.get("surface"),
                    command.get("project_id"),
                    command.get("run_id"),
                    now,
                    now,
                ),
            )
            conn.commit()
        return command["command_id"]

    def find_by_idempotency(self, idempotency_key: str) -> dict | None:
        """Look up a command by its unique idempotency key (§1b.2)."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT command_id, kind, params_json, params_digest,"
                " idempotency_key, actor, actor_class, surface, project_id,"
                " run_id, status, claim_generation, result_json, created_at,"
                " updated_at FROM commands WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row is not None else None

    def claim_command(self, command_id: str, worker_id: str, generation: int) -> bool:
        """CAS accepted->claimed bound to the lease generation (§1i)."""
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE commands SET status = 'claimed', claim_generation = ?,"
                " updated_at = ? WHERE command_id = ? AND status = 'accepted'",
                (generation, _utcnow(), command_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def complete_command(
        self, command_id: str, generation: int, result: dict | None, failure: dict | None
    ) -> bool:
        """CAS claimed->completed/failed; False means a stale generation
        (the caller emits ``worker.late_result`` and discards the outcome)."""
        status = "failed" if failure is not None else "completed"
        outcome = failure if failure is not None else result
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE commands SET status = ?, result_json = ?, updated_at = ?"
                " WHERE command_id = ? AND status = 'claimed' AND claim_generation = ?",
                (status, json.dumps(outcome), _utcnow(), command_id, generation),
            )
            conn.commit()
            return cursor.rowcount == 1

    def requeue_claimed(self, reason: str) -> list:
        """Recovery: claimed -> accepted with command.requeued (§1h.7)."""
        now = _utcnow()
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT command_id FROM commands WHERE status = 'claimed'"
            ).fetchall()
            requeued = [row["command_id"] for row in rows]
            conn.execute(
                "UPDATE commands SET status = 'accepted', claim_generation = NULL,"
                " updated_at = ? WHERE status = 'claimed'",
                (now,),
            )
            for command_id in requeued:
                conn.execute(
                    "INSERT INTO service_events (ts, type, project_id, run_id,"
                    " command_id, payload) VALUES (?, 'command.requeued', NULL,"
                    " NULL, ?, ?)",
                    (now, command_id, json.dumps({"command_id": command_id, "reason": reason})),
                )
            conn.commit()
        return requeued

    def get_command(self, command_id: str) -> dict | None:
        """Command row by id (status query, restart-safe)."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT command_id, kind, params_json, params_digest,"
                " idempotency_key, actor, actor_class, surface, project_id,"
                " run_id, status, claim_generation, result_json, created_at,"
                " updated_at FROM commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def upsert_wait(
        self,
        run_id: str,
        wait_class: str,
        reason: str,
        retry_at: str | None,
        known_reset: bool,
        backoff_json: str | None,
        entered_at: str,
    ) -> None:
        """Upsert the single active waits row (interfaces §1c table 4)."""
        with contextlib.closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO waits (run_id, wait_class, reason, retry_at,"
                " known_reset, backoff_json, entered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    wait_class,
                    reason,
                    retry_at,
                    1 if known_reset else 0,
                    backoff_json,
                    entered_at,
                ),
            )
            conn.commit()

    def clear_wait(self, run_id: str) -> None:
        """Clear the active waits row (wait.resolved persistence half)."""
        with contextlib.closing(self._connect()) as conn:
            conn.execute("DELETE FROM waits WHERE run_id = ?", (run_id,))
            conn.commit()

    def get_wait(self, run_id: str) -> dict | None:
        """Active waits row by run (interfaces §1c table 4); None when absent."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT run_id, wait_class, reason, retry_at,"
                " known_reset, backoff_json, entered_at FROM waits WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_waits(self) -> list:
        """All active waits rows in deterministic run order (§1c table 4)."""
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT run_id, wait_class, reason, retry_at,"
                " known_reset, backoff_json, entered_at FROM waits ORDER BY run_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_projects(self) -> list:
        """Registered projects (interfaces §1c table 2), insertion order."""
        with contextlib.closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT project_id, repo_path, version FROM projects ORDER BY rowid"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: str | None) -> dict | None:
        """Registered project row by id (interfaces §1c table 2)."""
        if not project_id:
            return None
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT project_id, repo_path, version FROM projects"
                " WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def find_run_project_id(self, run_id: str | None) -> str | None:
        """project_id that owns run_id (commands row, then service events)."""
        if not run_id:
            return None
        queries = (
            "SELECT project_id FROM commands WHERE run_id = ?"
            " AND project_id IS NOT NULL LIMIT 1",
            "SELECT project_id FROM service_events WHERE run_id = ?"
            " AND project_id IS NOT NULL LIMIT 1",
        )
        with contextlib.closing(self._connect()) as conn:
            for query in queries:
                row = conn.execute(query, (run_id,)).fetchone()
                if row is not None and row[0]:
                    return str(row[0])
        return None

    def get_schedule(self) -> dict | None:
        """Single schedule row (interfaces §1c table 8); None when unset."""
        with contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT active_run, queue_json FROM schedule WHERE id = 1"
            ).fetchone()
        if row is None:
            return None
        try:
            queue = json.loads(row["queue_json"]) if row["queue_json"] else []
        except ValueError:
            queue = []
        return {"active_run": row["active_run"], "queue": queue if isinstance(queue, list) else []}
