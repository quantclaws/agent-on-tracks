"""SQLite event store (D-02): `events` is the sole append-only source of truth;
`runs`/`backlog` are derived projections, drop-and-rebuildable (NFR-04).

Blob discipline (R3-07): payload JSON >8KB is externalized to content-addressed
`runtime/blobs/{sha256}` via temp file -> fsync -> atomic rename, and only then
is the event committed, so a committed event never references a missing blob.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State, project

BLOB_THRESHOLD = 8 * 1024
SCHEMA_VERSION = 1

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    ts = int(time.time() * 1000)
    out = [_ULID_ALPHABET[(ts >> s) & 31] for s in range(45, -1, -5)]
    rnd = int.from_bytes(os.urandom(10), "big")
    out += [_ULID_ALPHABET[(rnd >> s) & 31] for s in range(75, -1, -5)]
    return "".join(out)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    command_id TEXT,
    task_id TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    substate TEXT,
    awaiting TEXT,
    updated_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backlog (
    run_id TEXT NOT NULL,
    version TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""


class Store:
    """Single-writer store; concurrency is guarded by the runtime lock file
    (D-07), not by this class."""

    def __init__(self, home: Path):
        self.home = home
        paths.runtime_dir(home).mkdir(parents=True, exist_ok=True)
        paths.blobs_dir(home).mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(paths.db_path(home))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- blobs (R3-07: blob first, event second) --------------------------

    def _write_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        final = paths.blobs_dir(self.home) / sha
        if final.exists():
            return sha
        tmp = final.with_name(final.name + ".tmp." + new_ulid())
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        return sha

    def write_audit_blob(self, payload: dict | list | str) -> str | None:
        """Best-effort content-addressed evidence write.

        Audit evidence is supplementary: callers must be able to record the
        business outcome even when the evidence filesystem is unavailable.
        """
        try:
            if isinstance(payload, str):
                raw = payload.encode("utf-8")
            else:
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            return self._write_blob(raw)
        except (OSError, TypeError, ValueError):
            return None

    def load_payload(self, payload: dict) -> dict:
        if set(payload) == {"$ref"}:
            data = (paths.blobs_dir(self.home) / payload["$ref"]).read_bytes()
            return json.loads(data)
        return payload

    # -- append + projection in one ACID transaction (D-02) ---------------

    def append(
        self,
        run_id: str,
        version: str,
        type: str,
        payload: dict,
        command_id: str | None = None,
        task_id: str | None = None,
    ) -> EventEnvelope:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(raw) > BLOB_THRESHOLD:
            stored = {"$ref": self._write_blob(raw)}
        else:
            stored = payload
        with self.conn:
            cur = self.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?", (run_id,)
            )
            seq = cur.fetchone()[0] + 1
            ev = EventEnvelope(
                seq=seq,
                ts=_now_iso(),
                run_id=run_id,
                version=version,
                type=type,
                schema_version=SCHEMA_VERSION,
                command_id=command_id,
                task_id=task_id,
                payload=stored,
            )
            self.conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ev.run_id,
                    ev.seq,
                    ev.ts,
                    ev.version,
                    ev.type,
                    ev.schema_version,
                    ev.command_id,
                    ev.task_id,
                    json.dumps(ev.payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            self._project_run(run_id)
            if type == "backlog.recorded":
                self.conn.execute(
                    "INSERT INTO backlog VALUES (?,?,?,?,?)",
                    (
                        run_id,
                        payload.get("version", version),
                        payload.get("decision", ""),
                        payload.get("reason", ""),
                        ev.ts,
                    ),
                )
        return ev

    def _project_run(self, run_id: str) -> None:
        state = self.state(run_id)
        self.conn.execute(
            "INSERT INTO runs (run_id, version, status, stage, substate, awaiting, updated_ts) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
            "version=excluded.version, status=excluded.status, stage=excluded.stage, "
            "substate=excluded.substate, awaiting=excluded.awaiting, "
            "updated_ts=excluded.updated_ts",
            (
                run_id,
                state.version or "",
                state.status,
                state.stage,
                state.substate,
                state.awaiting,
                _now_iso(),
            ),
        )

    # -- reads -------------------------------------------------------------

    def events(self, run_id: str) -> Iterator[EventEnvelope]:
        cur = self.conn.execute(
            "SELECT run_id, seq, ts, version, type, schema_version, command_id, task_id, payload "
            "FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        )
        for r in cur.fetchall():
            yield EventEnvelope(
                seq=r[1],
                ts=r[2],
                run_id=r[0],
                version=r[3],
                type=r[4],
                schema_version=r[5],
                command_id=r[6],
                task_id=r[7],
                payload=self.load_payload(json.loads(r[8])),
            )

    def state(self, run_id: str) -> State:
        return project(self.events(run_id))

    def active_run(self) -> str | None:
        cur = self.conn.execute(
            "SELECT run_id FROM runs "
            "WHERE status NOT IN ('completed', 'backlog') AND stage IS NOT NULL "
            "ORDER BY updated_ts DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else None

    def latest_run(self) -> str | None:
        """Return the most recent run_id regardless of status."""
        cur = self.conn.execute(
            "SELECT run_id FROM runs ORDER BY updated_ts DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else None

    def rebuild_projections(self) -> None:
        """NFR-04: projections are derivable from events alone."""
        with self.conn:
            self.conn.execute("DELETE FROM runs")
            self.conn.execute("DELETE FROM backlog")
            run_ids = [
                r[0] for r in self.conn.execute("SELECT DISTINCT run_id FROM events")
            ]
            for rid in run_ids:
                self._project_run(rid)
                for ev in self.events(rid):
                    if ev.type == "backlog.recorded":
                        p = ev.payload
                        self.conn.execute(
                            "INSERT INTO backlog VALUES (?,?,?,?,?)",
                            (
                                rid,
                                p.get("version", ev.version),
                                p.get("decision", ""),
                                p.get("reason", ""),
                                ev.ts,
                            ),
                        )
