"""Shared read-only infrastructure for the IF-QUERY-001 projections.

Read-only SQLite opens/retries, per-run event/State caches (#225), project/run
discovery, registry/plan document reads, the AC evidence chain, service-plane
state, and the human-todo helpers. Behaviour is unchanged from the original
single-module projections read model; the public projections live in
``tracks.server.projections`` and its sibling projection modules.

Contract token: IF-QUERY-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.baseline import revision_digest
from tracks.executor.validation_shared import _acc_scan
from tracks.kernel.events import EventEnvelope, event_envelope_from_row
from tracks.kernel.machine import State
from tracks.kernel.machine import project as project_state

_PAUSE_REQUESTED = "run.pause_requested"
_PAUSED = "run.paused"
_TERMINAL_STATUSES = frozenset({"completed", "backlog"})
_LATEST_AC_TYPES = ("verdict.passed", "verdict.failed", "red.validated", "test.written")


# -- read-only connection helpers -------------------------------------------


class _ROConn(sqlite3.Connection):
    """Read-only connection remembering its database path (race retry)."""

    trac_path: str


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    # A quiescent WAL database (neither -wal nor -shm beside it — clean
    # close, no live writer) opens immutable: the main file already holds
    # every committed frame, so the snapshot is exact and the read leaves
    # zero filesystem artifacts under the repo (a plain mode=ro connection
    # to a WAL database materializes -shm/-wal sidecars it can never clean
    # up). Live databases — the serve with its writer connected — always
    # carry the sidecars and keep the ordinary read-only open.
    wal = path.parent / (path.name + "-wal")
    shm = path.parent / (path.name + "-shm")
    conn: _ROConn
    if not wal.exists() and not shm.exists():
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True,factory=_ROConn)
        except sqlite3.Error:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, factory=_ROConn)
    else:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, factory=_ROConn)
    conn.trac_path = str(path)
    return conn


def _service_db(home: Path) -> sqlite3.Connection | None:
    return _ro(Path(home) / "service.db")


def _tracks_db(repo_path: str) -> sqlite3.Connection | None:
    return _ro(Path(repo_path) / ".tracks" / "runtime" / "tracks.db")


def _blobs_home(repo_path: str) -> Path:
    return Path(repo_path) / ".tracks" / "runtime" / "blobs"


def _rows(conn: sqlite3.Connection | None, sql: str, args: tuple = ()) -> list[tuple]:
    if conn is None:
        return []
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error as exc:
        return _rows_retry(conn, exc, sql, args)


def _rows_retry(
    conn: sqlite3.Connection, exc: sqlite3.Error, sql: str, args: tuple
) -> list[tuple]:
    """T-008 read-hardening: a quiescent ``immutable=1`` snapshot can race a
    live writer (torn read), and a run plane read inside its creation window
    (file created, schema commit in flight) answers ``no such table`` even
    over the plain open. Retry once over a fresh plain connection (which
    sees the live WAL); a still-missing table is the no-rows case — the same
    semantic a not-yet-existing database file already has. Else re-raise."""
    if not isinstance(exc, sqlite3.OperationalError) or "no such table" not in str(exc):
        path = getattr(conn, "trac_path", None)
        if not path:
            raise
        with contextlib.closing(
            sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ) as fresh:
            try:
                return fresh.execute(sql, args).fetchall()
            except sqlite3.OperationalError as retry_exc:
                if "no such table" not in str(retry_exc):
                    raise
    return []


def _json(raw: object) -> Any:
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _load_payload(raw: object, repo_path: str) -> dict:
    payload = _json(raw)
    if isinstance(payload, dict) and set(payload) == {"$ref"}:
        blob = _blobs_home(repo_path) / str(payload["$ref"])
        try:
            return json.loads(blob.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return payload if isinstance(payload, dict) else {}


# -- per-run event/state caches (#225) ----------------------------------------
#
# Events are append-only with a monotonic per-run ``seq``, so ``MAX(seq)`` is
# a sound watermark: unchanged watermark means an already-built envelope list
# / folded State is still current. One cheap indexed scalar query per run per
# request replaces the multi-hundred-ms full replay (blob reads + json.loads)
# the overview/todo/detail projections used to pay on every request. The
# capacity cap bounds memory; entries beyond it age out oldest-first.

_CACHE_CAP = 64
_CACHE_LOCK = threading.Lock()
_EVENTS_CACHE: dict[tuple[str, str], tuple[Any, list[EventEnvelope]]] = {}
_STATE_CACHE: dict[tuple[str, str], tuple[Any, State]] = {}


def _cache_put(cache: dict, key: tuple[str, str], value: tuple) -> None:
    with _CACHE_LOCK:
        if key in cache:
            del cache[key]
        cache[key] = value
        while len(cache) > _CACHE_CAP:
            del cache[next(iter(cache))]


def _cache_get(cache: dict, key: tuple[str, str]) -> tuple | None:
    with _CACHE_LOCK:
        return cache.get(key)


def _run_watermark(repo_path: str, run_id: str) -> Any:
    """The run's current event watermark (``MAX(seq)``); None = no events."""
    conn = _tracks_db(repo_path)
    try:
        rows = _rows(conn, "SELECT MAX(seq) FROM events WHERE run_id = ?", (run_id,))
    finally:
        if conn is not None:
            conn.close()
    return rows[0][0] if rows else None


def _run_events(repo_path: str, run_id: str) -> list[EventEnvelope]:
    cached = _cache_get(_EVENTS_CACHE, (repo_path, run_id))
    watermark = _run_watermark(repo_path, run_id)
    if cached is not None and watermark is not None and cached[0] == watermark:
        return cached[1]
    conn = _tracks_db(repo_path)
    try:
        rows = _rows(conn,
            "SELECT run_id, seq, ts, version, type, schema_version, command_id, task_id, payload "
                "FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,))
    finally:
        if conn is not None:
            conn.close()
    events = [event_envelope_from_row(row, _load_payload(row[8], repo_path)) for row in rows]
    _cache_put(_EVENTS_CACHE, (repo_path, run_id), (rows[-1][1] if rows else 0, events))
    return events


def _state_of(repo_path: str, run_id: str) -> State:
    cached = _cache_get(_STATE_CACHE, (repo_path, run_id))
    watermark = _run_watermark(repo_path, run_id)
    if cached is not None and watermark is not None and cached[0] == watermark:
        return cached[1]
    state = project_state(_run_events(repo_path, run_id))
    _cache_put(_STATE_CACHE, (repo_path, run_id), (watermark or 0, state))
    return state


# -- project / run discovery ------------------------------------------------


def _projects(conn: sqlite3.Connection | None) -> list[dict]:
    return [
        {
            "project_id": row[0], "repo_path": row[1], "version": row[2]
        } for row in _rows(conn, "SELECT project_id, repo_path, version FROM projects")]


def _project_by_id(conn: sqlite3.Connection | None, project_id: str | None) -> dict | None:
    if project_id is None:
        return None
    for project in _projects(conn):
        if project["project_id"] == project_id:
            return project
    return None


def _project_from_home(home: Path, project_id: str) -> dict | None:
    """Registered project by id over a short-lived read-only connection."""
    conn = _service_db(home)
    try:
        return _project_by_id(conn, project_id)
    finally:
        if conn is not None:
            conn.close()


def _project_for_run(conn: sqlite3.Connection | None, run_id: str) -> dict | None:
    rows = _rows(conn, "SELECT project_id FROM commands WHERE run_id = ? LIMIT 1", (run_id,))
    if rows:
        found = _project_by_id(conn, rows[0][0])
        if found is not None:
            return found
    rows = _rows(conn, "SELECT project_id FROM service_events WHERE run_id = ? LIMIT 1", (run_id,))
    if rows:
        found = _project_by_id(conn, rows[0][0])
        if found is not None:
            return found
    for project in _projects(conn):
        if _run_exists(project["repo_path"], run_id):
            return project
    return None


def _run_exists(repo_path: str, run_id: str) -> bool:
    conn = _tracks_db(repo_path)
    try:
        return bool(_rows(conn, "SELECT 1 FROM runs WHERE run_id = ? LIMIT 1", (run_id,)))
    finally:
        if conn is not None:
            conn.close()


def _project_runs(repo_path: str) -> list[dict]:
    conn = _tracks_db(repo_path)
    try:
        rows = _rows(conn,
            "SELECT run_id, version, status, stage, substate, awaiting, updated_ts FROM runs "
                "ORDER BY updated_ts")
    finally:
        if conn is not None:
            conn.close()
    return [
        {
            "run_id": row[0], "version": row[1], "status": row[2], "stage": row[3],
            "substate": row[4], "awaiting": row[5], "updated_ts": row[6],
        } for row in rows]


# -- registry / plan documents ---------------------------------------------


def _registry_acs(repo_path: str, version: str | None) -> list[str]:
    if not version:
        return []
    target = Path(repo_path) / ".tracks" / "projects" / version / "acceptance.md"
    if not target.exists():
        return []
    try:
        _, acs = _acc_scan(target.read_text(encoding="utf-8"))
    except OSError:
        return []
    return [ac_id for ac_id, *_ in acs]


def _plan_tests(repo_path: str, version: str | None) -> dict[str, list[str]]:
    if not version:
        return {}
    target = Path(repo_path) / ".tracks" / "projects" / version / "tasks.json"
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    mapping: dict[str, list[str]] = {}
    for task in data.get("tasks", []) if isinstance(data, dict) else []:
        for entry in task.get("test_tasks", []) if isinstance(task, dict) else []:
            ac_id = entry.get("ac_id")
            if not isinstance(ac_id, str):
                continue
            anchors = [ref for ref in entry.get("anchors", []) if isinstance(ref, str)]
            mapping.setdefault(ac_id, []).extend(anchors)
    return mapping


def _layer_of(nodes: list[str]) -> str:
    for node in nodes:
        for layer in ("integration", "e2e", "unit"):
            if node.startswith(f"tests/{layer}/"):
                return layer
    return ""


# -- evidence chain ---------------------------------------------------------


def _ac_evidence(events: list[EventEnvelope], nodes: list[str]) -> list[dict]:
    evidence = []
    for node in nodes:
        status = "missing"
        for event in events:
            if not _event_references(event, node):
                continue
            if event.type == "verdict.failed":
                status = "stale"
            elif event.type == "verdict.passed":
                status = "ok"
            elif event.type == "prism.verdict":
                status = "unreviewed"
        evidence.append({"ref": node, "status": status})
    return evidence


def _event_references(event: EventEnvelope, node: str) -> bool:
    payload = event.payload or {}
    for key in ("node", "test_id", "ref"):
        if payload.get(key) == node:
            return True
    for key in ("nodes", "test_nodes", "failed_nodes"):
        values = payload.get(key)
        if isinstance(values, list) and node in values:
            return True
    return False


def _latest_result(evidence: list[dict]) -> str | None:
    statuses = {item["status"] for item in evidence}
    if "ok" in statuses:
        return "passed"
    if statuses & {"stale", "unreviewed"}:
        return "failed"
    return None


def _chain_rows(repo_path: str, version: str | None, run_id: str) -> list[dict]:
    events = _run_events(repo_path, run_id)
    state = project_state(events)
    tests = _plan_tests(repo_path, version)
    chain = []
    for ac_id in _registry_acs(repo_path, version):
        nodes = list(tests.get(ac_id, []))
        evidence = _ac_evidence(events, nodes)
        chain.append(
            {
                "ac_id": ac_id, "layer": _layer_of(nodes), "test_nodes": nodes,
                "latest_result": _latest_result(evidence), "candidate_sha": state.candidate_sha,
                "evidence": evidence,
            })
    return chain


def _ac_closed(chain: list[dict]) -> int:
    return sum(1 for row in chain if any(item["status"] == "ok" for item in row["evidence"]))


# -- service-plane state ----------------------------------------------------


def _pause_state(
    conn: sqlite3.Connection | None, run_id: str
) -> tuple[dict, str | None]:
    rows = _rows(conn,
        "SELECT type FROM service_events WHERE run_id = ? AND type IN (?, ?) "
            "ORDER BY seq DESC LIMIT 1",
        (run_id, _PAUSE_REQUESTED, _PAUSED))
    if not rows:
        return {"requested": False, "effective": False}, None
    if rows[0][0] == _PAUSED:
        return {"requested": False, "effective": True}, "已暂停"
    return {"requested": True, "effective": False}, "暂停请求已接收"


def _wait_spec(conn: sqlite3.Connection | None, run_id: str) -> dict | None:
    rows = _rows(conn,
        "SELECT wait_class, reason, retry_at, known_reset, backoff_json FROM waits "
            "WHERE run_id = ?",
        (run_id,))
    if not rows:
        return None
    wait_class, reason, retry_at, known_reset, backoff_json = rows[0]
    return {"wait_class": wait_class, "reason": reason, "retry_at": retry_at,
        "known_reset": bool(known_reset), "backoff": _json(backoff_json) if backoff_json else None,}


def _lease(conn: sqlite3.Connection | None, run_id: str) -> dict:
    rows = _rows(conn, "SELECT generation, worker_id FROM leases WHERE run_id = ?", (run_id,))
    if not rows:
        return {"generation": 0, "worker_id": ""}
    return {"generation": int(rows[0][0] or 0), "worker_id": rows[0][1] or ""}


def _is_queued(conn: sqlite3.Connection | None, run_id: str) -> bool:
    rows = _rows(conn, "SELECT active_run, queue_json FROM schedule WHERE id = 1")
    if not rows:
        return False
    active_run, queue_json = rows[0]
    if active_run == run_id:
        return False
    queue = _json(queue_json) if queue_json else []
    return isinstance(queue, list) and run_id in queue


def _is_terminal(state: State, events: list[EventEnvelope]) -> bool:
    if state.status in _TERMINAL_STATUSES:
        return True
    return any(event.type in ("run.completed", "run.interrupted") for event in events)


def _control_state(
    state: State, events: list[EventEnvelope], pause_label: str | None, wait: dict | None,
    queued: bool, blocked: bool = False,
) -> str:
    if pause_label is not None:
        return pause_label
    if blocked:
        return "阻塞"
    if wait is not None:
        return "等待外部"
    if state.awaiting or state.substate == "AWAITING_RELEASE":
        return "等待人工"
    if queued:
        return "排队中"
    if _is_terminal(state, events):
        return "终态"
    return "推进中"


# -- todos ------------------------------------------------------------------


def _material_revision(project: dict) -> str | None:
    """Current material revision of the project's version dir.

    The trio digest is the same content addressing ``record_stage_approval``
    binds, so a pending stage-approval item shows the revision the reviewer
    actually signs. None when the version docs are not readable.
    """
    version = project.get("version")
    repo_path = project.get("repo_path")
    if not version or not repo_path:
        return None
    vdir = paths.version_dir(paths.tracks_home(Path(str(repo_path))), str(version))
    try:
        return revision_digest(vdir)
    except (OSError, UnicodeDecodeError):
        return None


def _todo_for(run_id: str, state: State, project: dict) -> dict | None:
    if state.awaiting == "approval":
        return {"run_id": run_id, "kind": "stage_approval", "object": state.stage or "approval",
            "revision": _material_revision(project) or state.preview_digest,
            "context": f"{state.stage or 'stage'} awaiting Human approval",
            "action_ref": f"/runs/{run_id}/review",}
    if state.awaiting == "triage":
        return {"run_id": run_id, "kind": "triage", "object": state.stage or "triage",
            "revision": None, "context": "triage decision required",
            "action_ref": f"/runs/{run_id}/review",}
    if state.substate == "AWAITING_RELEASE":
        return {"run_id": run_id, "kind": "release_decision", "object": state.stage or "release",
            "revision": state.preview_digest, "context": "release candidate awaiting decision",
            "action_ref": f"/runs/{run_id}/release",}
    return None
