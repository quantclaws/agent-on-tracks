"""Read-model projections over tracks.db + service.db (IF-QUERY-001).

Every projection is computed on demand from the event stores and project
documents (read-only connections); nothing here keeps in-memory "running"
facts. Progress reports test counts and AC-closure counts separately with
identifiable sources; no fabricated percentages (FR-0302). The human todo
projection lists only legitimate human decisions — quota/CI/network waits
never appear (FR-0307). Execution state is run/task level and never exposes
Agent session content (FR-0303). The project-domain docs read models (§1n)
serve the version tree / docs and the discussion threads; the timeline
carries the 13-stage display order (§1q).

Contract token: IF-QUERY-001.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.baseline import revision_digest
from tracks.discuss.model import iter_comments
from tracks.discuss.parser import parse_threads
from tracks.executor.validation_shared import _acc_scan
from tracks.kernel.events import EventEnvelope, event_envelope_from_row
from tracks.kernel.machine import State
from tracks.kernel.machine import project as project_state
from tracks.kernel.stage_registry import canonical_stage_order

_PAUSE_REQUESTED = "run.pause_requested"
_PAUSED = "run.paused"
_TERMINAL_STATUSES = frozenset({"completed", "backlog"})
_GATE_TYPES = {"local_gate.passed": "passed", "local_gate.failed": "failed"}
_TEST_RESULT_TYPES = frozenset(
    {"local_gate.passed", "local_gate.failed", "full.executed", "red.validated", "test.collected"}
)
_LATEST_AC_TYPES = ("verdict.passed", "verdict.failed", "red.validated", "test.written")

_CONFIG_DEFAULTS: dict[str, Any] = {
    "wait_initial_s": 60,
    "wait_cap_s": 900,
    "poll_interval_s": 5,
    "poll_idle_cap_s": 60,
    "lease_ttl_s": 30,
    "version": "v0.9",
}

# Project-domain doc read model (§1n.4): the six-piece closed set with
# `design` kept as the architecture alias; shared with the write face.
DOC_FILES = {
    "story": "story.md",
    "spec": "spec.md",
    "acceptance": "acceptance.md",
    "architecture": "architecture.md",
    "interfaces": "interfaces.md",
    "test-plan": "test-plan.md",
    "design": "architecture.md",
}
_DOC_ORDER = ("story", "spec", "acceptance", "architecture", "interfaces", "test-plan")
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)$")
_HOTFIX_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)-hotfix-(\d+)$")


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
            conn = sqlite3.connect(
                f"file:{path}?mode=ro&immutable=1", uri=True, factory=_ROConn
            )
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
        rows = _rows(
            conn, "SELECT MAX(seq) FROM events WHERE run_id = ?", (run_id,)
        )
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
        rows = _rows(
            conn,
            "SELECT run_id, seq, ts, version, type, schema_version, command_id, task_id, payload "
            "FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        )
    finally:
        if conn is not None:
            conn.close()
    events = [
        event_envelope_from_row(row, _load_payload(row[8], repo_path))
        for row in rows
    ]
    _cache_put(
        _EVENTS_CACHE, (repo_path, run_id), (rows[-1][1] if rows else 0, events)
    )
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
        {"project_id": row[0], "repo_path": row[1], "version": row[2]}
        for row in _rows(conn, "SELECT project_id, repo_path, version FROM projects")
    ]


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
    rows = _rows(
        conn, "SELECT project_id FROM service_events WHERE run_id = ? LIMIT 1", (run_id,)
    )
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
        rows = _rows(
            conn,
            "SELECT run_id, version, status, stage, substate, awaiting, updated_ts FROM runs "
            "ORDER BY updated_ts",
        )
    finally:
        if conn is not None:
            conn.close()
    return [
        {
            "run_id": row[0],
            "version": row[1],
            "status": row[2],
            "stage": row[3],
            "substate": row[4],
            "awaiting": row[5],
            "updated_ts": row[6],
        }
        for row in rows
    ]


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
                "ac_id": ac_id,
                "layer": _layer_of(nodes),
                "test_nodes": nodes,
                "latest_result": _latest_result(evidence),
                "candidate_sha": state.candidate_sha,
                "evidence": evidence,
            }
        )
    return chain


def _ac_closed(chain: list[dict]) -> int:
    return sum(
        1
        for row in chain
        if any(item["status"] == "ok" for item in row["evidence"])
    )


# -- progress / execution helpers ------------------------------------------


def _extract_passed(payload: dict) -> int | None:
    value = payload.get("passed")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    summary = (payload.get("normalized_result") or {}).get("summary")
    if isinstance(summary, dict):
        inner = summary.get("passed")
        if isinstance(inner, int) and not isinstance(inner, bool):
            return inner
    return None


def _latest_tests_passed(events: list[EventEnvelope]) -> int | None:
    for event in reversed(events):
        if event.type not in _TEST_RESULT_TYPES:
            continue
        found = _extract_passed(event.payload or {})
        if found is not None:
            return found
    return None


def _gates(events: list[EventEnvelope]) -> list[dict]:
    gates = []
    for event in events:
        status = _GATE_TYPES.get(event.type)
        if status is None:
            continue
        payload = event.payload or {}
        name = payload.get("gate") or payload.get("gate_id") or event.type
        gates.append({"name": str(name), "status": status})
    return gates


def _executions(events: list[EventEnvelope]) -> list[dict]:
    started: list[str] = []
    for event in events:
        if event.type == "task.started":
            task_id = event.payload.get("task_id") or event.task_id
            if isinstance(task_id, str) and task_id and task_id not in started:
                started.append(task_id)
    completed = {
        event.payload.get("task_id")
        for event in events
        if event.type == "task.completed"
    }
    executions = []
    for task_id in started:
        if task_id in completed:
            continue
        executions.append(
            {
                "role": _task_role(events, task_id),
                "task_id": task_id,
                "state": "failed" if _task_failed(events, task_id) else "executing",
                "log_ref": _task_log_ref(events, task_id),
            }
        )
    return executions


def _task_events(events: list[EventEnvelope], task_id: str) -> list[EventEnvelope]:
    return [
        event
        for event in events
        if (event.payload or {}).get("task_id") == task_id or event.task_id == task_id
    ]


def _task_role(events: list[EventEnvelope], task_id: str) -> str:
    for event in reversed(_task_events(events, task_id)):
        role = (event.payload or {}).get("role")
        if event.type == "outcome.received" and isinstance(role, str):
            return role
    return ""


def _task_failed(events: list[EventEnvelope], task_id: str) -> bool:
    return any(event.type == "verdict.failed" for event in _task_events(events, task_id))


def _task_log_ref(events: list[EventEnvelope], task_id: str) -> str:
    for event in reversed(_task_events(events, task_id)):
        log_ref = (event.payload or {}).get("log_ref")
        if isinstance(log_ref, str) and log_ref:
            return log_ref
    return ""


# -- service-plane state ----------------------------------------------------


def _pause_state(
    conn: sqlite3.Connection | None, run_id: str
) -> tuple[dict, str | None]:
    rows = _rows(
        conn,
        "SELECT type FROM service_events WHERE run_id = ? AND type IN (?, ?) "
        "ORDER BY seq DESC LIMIT 1",
        (run_id, _PAUSE_REQUESTED, _PAUSED),
    )
    if not rows:
        return {"requested": False, "effective": False}, None
    if rows[0][0] == _PAUSED:
        return {"requested": False, "effective": True}, "已暂停"
    return {"requested": True, "effective": False}, "暂停请求已接收"


def _wait_spec(conn: sqlite3.Connection | None, run_id: str) -> dict | None:
    rows = _rows(
        conn,
        "SELECT wait_class, reason, retry_at, known_reset, backoff_json FROM waits "
        "WHERE run_id = ?",
        (run_id,),
    )
    if not rows:
        return None
    wait_class, reason, retry_at, known_reset, backoff_json = rows[0]
    return {
        "wait_class": wait_class,
        "reason": reason,
        "retry_at": retry_at,
        "known_reset": bool(known_reset),
        "backoff": _json(backoff_json) if backoff_json else None,
    }


def _lease(conn: sqlite3.Connection | None, run_id: str) -> dict:
    rows = _rows(
        conn, "SELECT generation, worker_id FROM leases WHERE run_id = ?", (run_id,)
    )
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
    state: State,
    events: list[EventEnvelope],
    pause_label: str | None,
    wait: dict | None,
    queued: bool,
) -> str:
    if pause_label is not None:
        return pause_label
    if wait is not None:
        return "等待外部"
    if state.awaiting or state.substate == "AWAITING_RELEASE":
        return "等待人工"
    if queued:
        return "排队中"
    if _is_terminal(state, events):
        return "终态"
    return "推进中"


# -- v0.11 awaiting gate + return options (§1k/§1m) ---------------------------

# §1k.2 type -> primary_action closed mapping (映射外无入口). The
# AWAIT_RELEASE member is the M-RELEASE substate-shaped gate.
_AWAIT_RELEASE = "AWAIT_RELEASE"
_AWAITING_PRIMARY_ACTIONS = {
    "triage": "triage",
    "review": "review",
    "review_pending_threads": "review",
    "approval": "approve",
    "escalation": "retry",
    "rollback": "return",
    "hotfix_triage": "guidance",
}
_AWAITING_REASON_LABELS = {
    _AWAIT_RELEASE: "发布三择一决策（release / delay / return）",
    "triage": "story triage 裁决（go / no-go / park）",
    "review": "文档阶段评审（comment / no-comment）",
    "review_pending_threads": "挂起线程恢复重评（comment / no-comment）",
    "approval": "批准基线（approve / revise）",
    "escalation": "失败升级处置（retry / return）",
    "rollback": "回滚目标确认（approve 选定目标）",
    "hotfix_triage": "hotfix 锚定人工处置（owning surface 指引）",
}


def _awaiting_type(state: State) -> str | None:
    """The §1k.1 gate type: the M-RELEASE gate substate maps onto the
    AWAIT_RELEASE type, else a closed awaiting member; None when the run
    awaits nothing."""
    if state.substate == "AWAITING_RELEASE":
        return _AWAIT_RELEASE
    return state.awaiting if state.awaiting in _AWAITING_PRIMARY_ACTIONS else None


def _awaiting_gate(state: State) -> dict | None:
    """The run-detail ``awaiting_gate`` field group (§1k.1) or None."""
    gate_type = _awaiting_type(state)
    if gate_type is None:
        return None
    stage = state.stage or "run"
    reason = f"{stage} 到达人工门：{_AWAITING_REASON_LABELS[gate_type]}"
    failure = state.last_failure if isinstance(state.last_failure, dict) else {}
    if failure.get("reason"):
        reason = f"{reason}（{failure['reason']}）"
    primary = (
        "release-decision"
        if gate_type == _AWAIT_RELEASE
        else _AWAITING_PRIMARY_ACTIONS[gate_type]
    )
    return {
        "type": gate_type,
        "reason": reason,
        "impact": (
            f"run 停在 {stage}（{state.substate or gate_type}），该门完成前不再推进；"
            "在时间线下栏动作面板处理后自动推进到下一边界"
        ),
        "primary_action": primary,
    }


class _RunStoreView:
    """Store-shaped read-only view (§1m.1 与 CLI 同一合法性规则共享计算):
    the shared ``trac return`` gate rules and already-executed scan run over
    the same code the CLI face uses — no writer lock, no second copy."""

    def __init__(self, repo_path: str) -> None:
        self._repo_path = repo_path

    def state(self, run_id: str) -> State:
        return _state_of(self._repo_path, run_id)

    def events(self, run_id: str) -> list[EventEnvelope]:
        return _run_events(self._repo_path, run_id)


def _return_options(repo_path: str, run_id: str) -> dict:
    """The run-detail ``return_options`` field group (§1m.1): the CLI's legal
    targets with each target's already-executed irreversible operations."""
    from tracks.cli.gate_cmd import _return_gate
    from tracks.cli.release_cmd import _already_executed_ops

    view = _RunStoreView(repo_path)
    _state, allowed, _error = _return_gate(view, run_id)
    if not allowed:
        return {"allowed": False, "targets": []}
    executed = _already_executed_ops(view, run_id)
    return {
        "allowed": True,
        "targets": [
            {"stage": stage, "already_executed": executed} for stage in allowed
        ],
    }


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
        return {
            "run_id": run_id,
            "kind": "stage_approval",
            "object": state.stage or "approval",
            "revision": _material_revision(project) or state.preview_digest,
            "context": f"{state.stage or 'stage'} awaiting Human approval",
            "action_ref": f"/runs/{run_id}/review",
        }
    if state.awaiting == "triage":
        return {
            "run_id": run_id,
            "kind": "triage",
            "object": state.stage or "triage",
            "revision": None,
            "context": "triage decision required",
            "action_ref": f"/runs/{run_id}/review",
        }
    if state.substate == "AWAITING_RELEASE":
        return {
            "run_id": run_id,
            "kind": "release_decision",
            "object": state.stage or "release",
            "revision": state.preview_digest,
            "context": "release candidate awaiting decision",
            "action_ref": f"/runs/{run_id}/release",
        }
    return None


# -- project-domain docs read models (§1n, §1p) ------------------------------


def _version_sort_key(name: str) -> tuple[int, int, int, int, str] | None:
    """Numeric-descending order for version directories (§1n.1).

    ``v<M>.<m>`` sorts by numeric components (never lexicographically); a
    ``v<M>.<m>-hotfix-<n>`` directory ranks right after its baseline version,
    newer hotfix numbers after older ones. None for non-version names.
    """
    match = _HOTFIX_VERSION_RE.match(name)
    if match is not None:
        return (-int(match.group(1)), -int(match.group(2)), 1, -int(match.group(3)), name)
    match = _VERSION_RE.match(name)
    if match is None:
        return None
    return (-int(match.group(1)), -int(match.group(2)), 0, 0, name)


def _projects_root(project: dict) -> Path:
    return Path(project["repo_path"]) / ".tracks" / "projects"


def _project_versions(project: dict) -> list[str]:
    root = _projects_root(project)
    if not root.is_dir():
        return []
    keyed = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        key = _version_sort_key(child.name)
        if key is not None:
            keyed.append((key, child.name))
    keyed.sort()
    return [name for _key, name in keyed]


def _version_path(project: dict, version: str) -> Path:
    return _projects_root(project) / version


_STORY_TITLE_RE = re.compile(r"^title:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _version_story_title(project: dict, version: str) -> str | None:
    """The story.md frontmatter ``title:`` of one version (human label, #224).

    Searched in the file head only — the frontmatter block leads the document
    by construction; a missing/unreadable story or a missing field degrades to
    ``None`` and callers fall back to the run id.
    """
    story = _version_path(project, version) / DOC_FILES["story"]
    try:
        head = story.read_text(encoding="utf-8")[:2048]
    except (OSError, UnicodeDecodeError):
        return None
    match = _STORY_TITLE_RE.search(head)
    return match.group(1) if match else None


def _revision_or_none(vdir: Path) -> str | None:
    try:
        return revision_digest(vdir)
    except (OSError, UnicodeDecodeError):
        return None


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _editable_run_id(runs: list[dict], version: str) -> str | None:
    """The newest non-terminal run of ``version``, or None (§1n.1)."""
    active = [
        run
        for run in runs
        if run["version"] == version and run["status"] not in _TERMINAL_STATUSES
    ]
    if not active:
        return None
    return max(active, key=lambda run: run["updated_ts"] or "")["run_id"]


def project_docs_tree(home: Path, project_id: str) -> dict | None:
    """Project docs tree: version-descending dirs + six-piece docs (§1n.1)."""
    project = _project_from_home(home, project_id)
    if project is None:
        return None
    return docs_tree_for_project(project)


def docs_tree_for_project(project: dict) -> dict:
    """Docs tree read model for one resolved project row (§1n.1).

    Split out of ``project_docs_tree`` so the single-user host fallback
    (api_query) can compute the same tree for the serve's own repo without a
    registered projects row.
    """
    runs = _project_runs(project["repo_path"])
    versions = []
    for version in _project_versions(project):
        vdir = _version_path(project, version)
        revision = _revision_or_none(vdir)
        docs = []
        for doc in _DOC_ORDER:
            path = vdir / DOC_FILES[doc]
            if path.is_file():
                docs.append(
                    {"doc": doc, "revision": revision, "updated_at": _mtime_iso(path)}
                )
        versions.append(
            {
                "version": version,
                "title": _version_story_title(project, version),
                "docs": docs,
                "editable_run_id": _editable_run_id(runs, version),
            }
        )
    return {"versions": versions}


def resolve_project_doc(
    home: Path, project_id: str, version: str, doc: str
) -> tuple[dict, Path, Path, str | None] | None:
    """(project, version dir, doc file, revision) for a project-domain doc.

    None when the project, the version directory name, or the doc member is
    unknown; handlers fail closed on that state (§2b #32-34).
    """
    project = _project_from_home(home, project_id)
    if project is None:
        return None
    return resolve_doc_in_project(project, version, doc)


def resolve_doc_in_project(
    project: dict, version: str, doc: str
) -> tuple[dict, Path, Path, str | None] | None:
    """``resolve_project_doc`` over an already-resolved project row (§2b #32)."""
    name = DOC_FILES.get(doc)
    if name is None or _version_sort_key(version) is None:
        return None
    vdir = _version_path(project, version)
    doc_file = vdir / name
    if not doc_file.is_file():
        return None
    return project, vdir, doc_file, _revision_or_none(vdir)


def project_doc_discussions(
    home: Path, project_id: str, version: str, doc: str
) -> dict | None:
    """Server-side tracks/discuss parse projected to the UI read model (§1p)."""
    resolved = resolve_project_doc(home, project_id, version, doc)
    if resolved is None:
        return None
    _project, _vdir, doc_file, _revision = resolved
    threads = parse_threads(doc_file.read_text(encoding="utf-8"))
    return {"threads": [_thread_json(thread) for thread in threads]}


def _thread_json(thread: Any) -> dict:
    return {
        "thread_id": thread.thread_id,
        "status": thread.status,
        "initiator": thread.initiator,
        "anchor_line": thread.anchor_line,
        "summary": thread.snippet,
        "entry_line": thread.root_line,
        "awaiting": _thread_awaiting(thread),
    }


def _thread_awaiting(thread: Any) -> str | None:
    """The party a thread currently awaits, or None (§1p.1).

    A comment whose @mention requests an answer (the tracks/discuss
    "@request someone to answer" semantic) and that still has no child reply
    is an open request; the latest such comment names the awaited party when
    it carries exactly one distinct mention.
    """
    pending = [
        comment
        for comment in iter_comments(thread.root)
        if not comment.children and comment.mentions
    ]
    if not pending:
        return None
    latest = max(pending, key=lambda comment: comment.line)
    unique = list(dict.fromkeys(latest.mentions))
    return unique[0] if len(unique) == 1 else None


def _stage_order() -> list[str]:
    """The 13-stage timeline display order (§1q.1).

    M-START leads; M-REQ-APPROVAL is inserted between M-ACC and M-DESIGN on
    top of the canonical kernel stage order (release stages included).
    """
    order = ["M-START"]
    for stage in canonical_stage_order():
        if stage == "M-DESIGN":
            order.append("M-REQ-APPROVAL")
        order.append(stage)
    return order


# -- public projections -----------------------------------------------------


def _overview_run_row(conn, project: dict, run: dict, titles: dict) -> dict | None:
    """One overview run row, or None when the row is not a run.

    Skipped rows (DIAGNOSE seq 11461 item 3): run-less rows with an empty
    version — the same criterion the §1e.2 registry (_registry_versions)
    uses — and backlog/archive terminals, which are entries, never runs
    (the nondevelop exits keep their no-run observability)."""
    if not run["version"] or run["status"] in ("backlog", "archive"):
        return None
    state = _state_of(project["repo_path"], run["run_id"])
    todo = _todo_for(run["run_id"], state, project)
    if run["version"] not in titles:
        titles[run["version"]] = _version_story_title(project, run["version"])
    return {
        "run_id": run["run_id"],
        "title": titles[run["version"]] or run["run_id"],
        "version": run["version"],
        "status": run["status"],
        "stage": run["stage"],
        "updated_ts": run["updated_ts"],
        "control_state": _run_control_state(conn, state, run["run_id"], []),
        "human_todos": 1 if todo is not None else 0,
    }


def project_overview(home: Path) -> dict:
    """Projects + runs + per-run human-todo counts (§1e)."""
    conn = _service_db(home)
    try:
        projects = _projects(conn)
        runs = []
        todo_count = 0
        for project in projects:
            titles: dict[str, str | None] = {}
            for run in _project_runs(project["repo_path"]):
                row = _overview_run_row(conn, project, run, titles)
                if row is None:
                    continue
                todo_count += row["human_todos"]
                runs.append(row)
        return {
            "projects": [
                {
                    "project_id": project["project_id"],
                    "repo_path": project["repo_path"],
                    "version": project["version"],
                }
                for project in projects
            ],
            "runs": runs,
            "human_todo_count": todo_count,
        }
    finally:
        if conn is not None:
            conn.close()


def _run_control_state(
    conn: sqlite3.Connection | None, state: State, run_id: str, events: list[EventEnvelope]
) -> str:
    pause, pause_label = _pause_state(conn, run_id)
    wait = _wait_spec(conn, run_id)
    return _control_state(state, events, pause_label, wait, _is_queued(conn, run_id))


def project_run_detail(home: Path, run_id: str) -> dict | None:
    """Stage/substate/control state/wait/progress/executions snapshot (§1e)."""
    conn = _service_db(home)
    try:
        project = _project_for_run(conn, run_id)
        if project is None:
            return None
        repo_path, version = project["repo_path"], project["version"]
        events = _run_events(repo_path, run_id)
        state = project_state(events)
        pause, pause_label = _pause_state(conn, run_id)
        wait = _wait_spec(conn, run_id)
        chain = _chain_rows(repo_path, version, run_id)
        return {
            "run_id": run_id,
            "version": state.version or version,
            "status": state.status,
            "stage": state.stage,
            "substate": state.substate,
            "control_state": _control_state(
                state, events, pause_label, wait, _is_queued(conn, run_id)
            ),
            "pause": pause,
            "wait": wait,
            "awaiting_gate": _awaiting_gate(state),
            "return_options": _return_options(repo_path, run_id),
            "progress": {
                "tasks_done": state.tasks_completed,
                "tasks_total": state.tasks_total,
                "tests_passed": _latest_tests_passed(events),
                "ac_closed": _ac_closed(chain),
                "ac_total": len(chain),
            },
            "quality": {"gates": _gates(events)},
            "executions": _executions(events),
            "lease": _lease(conn, run_id),
        }
    finally:
        if conn is not None:
            conn.close()


def project_timeline(
    home: Path,
    run_id: str,
    *,
    command_id: str | None = None,
    task_id: str | None = None,
    ac_id: str | None = None,
    after_seq: str | None = None,
) -> dict:
    """Merged tracks.db + service_events timeline with cursor + stage order (§1e)."""
    conn = _service_db(home)
    try:
        project = _project_for_run(conn, run_id)
        if project is None:
            return {"events": [], "cursor": "", "stage_order": _stage_order()}
        repo_path = project["repo_path"]
        rows = _timeline_rows(conn, repo_path, run_id)
        rows = _filter_timeline(rows, command_id, task_id, ac_id)
        rows = _after_cursor(rows, after_seq)
        return {"events": rows, "cursor": _cursor_of(rows), "stage_order": _stage_order()}
    finally:
        if conn is not None:
            conn.close()


def _timeline_rows(
    conn: sqlite3.Connection | None, repo_path: str, run_id: str
) -> list[dict]:
    rows = []
    for event in _run_events(repo_path, run_id):
        rows.append(
            {
                "source": "tracks",
                "seq": event.seq,
                "ts": event.ts,
                "type": event.type,
                "run_id": event.run_id,
                "command_id": event.command_id,
                "task_id": event.task_id,
                "ac_refs": _event_ac_refs(event.payload or {}),
                "summary": event.type,
                "payload_ref": "",
            }
        )
    service = _rows(
        conn,
        "SELECT seq, ts, type, run_id, command_id, payload FROM service_events "
        "WHERE run_id = ? ORDER BY seq",
        (run_id,),
    )
    for seq, ts, type_, run, command, payload in service:
        rows.append(
            {
                "source": "service",
                "seq": int(seq),
                "ts": ts,
                "type": type_,
                "run_id": run,
                "command_id": command,
                "task_id": None,
                "ac_refs": _event_ac_refs(_json(payload)),
                "summary": type_,
                "payload_ref": "",
            }
        )
    rows.sort(key=lambda row: (row["ts"] or "", 0 if row["source"] == "tracks" else 1, row["seq"]))
    return rows


def _event_ac_refs(payload: dict) -> list[str]:
    refs = payload.get("ac_refs")
    return [ref for ref in refs if isinstance(ref, str)] if isinstance(refs, list) else []


def _filter_timeline(
    rows: list[dict], command_id: str | None, task_id: str | None, ac_id: str | None
) -> list[dict]:
    filtered = rows
    if command_id is not None:
        filtered = [row for row in filtered if row["command_id"] == command_id]
    if task_id is not None:
        filtered = [row for row in filtered if row["task_id"] == task_id]
    if ac_id is not None:
        filtered = [row for row in filtered if ac_id in row["ac_refs"]]
    return filtered


def _cursor_of(rows: list[dict]) -> str:
    if not rows:
        return ""
    last = rows[-1]
    raw = json.dumps(
        {"ts": last["ts"], "source": last["source"], "seq": last["seq"]}, sort_keys=True
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _after_cursor(rows: list[dict], after_seq: str | None) -> list[dict]:
    if not after_seq:
        return rows
    try:
        decoded = json.loads(base64.urlsafe_b64decode(after_seq.encode("ascii")))
        key = (decoded["ts"], 0 if decoded["source"] == "tracks" else 1, int(decoded["seq"]))
    except (ValueError, KeyError, TypeError):
        return rows
    result = []
    for row in rows:
        row_key = (row["ts"], 0 if row["source"] == "tracks" else 1, row["seq"])
        if row_key > key:
            result.append(row)
    return result


def project_ac_chain(home: Path, run_id: str) -> list:
    """AC -> test node -> result -> candidate -> evidence chain (§1e)."""
    conn = _service_db(home)
    try:
        project = _project_for_run(conn, run_id)
        if project is None:
            return []
        return _chain_rows(project["repo_path"], project["version"], run_id)
    finally:
        if conn is not None:
            conn.close()


def project_todos(home: Path) -> list:
    """Legitimate human decisions only (§1e); waits/env issues excluded."""
    conn = _service_db(home)
    try:
        todos = []
        for project in _projects(conn):
            for run in _project_runs(project["repo_path"]):
                state = _state_of(project["repo_path"], run["run_id"])
                todo = _todo_for(run["run_id"], state, project)
                if todo is not None:
                    todos.append(todo)
        return todos
    finally:
        if conn is not None:
            conn.close()


def project_service_config(home: Path) -> dict:
    """Effective wait/poll/lease configuration values (§1e, NFR-0152)."""
    return dict(_CONFIG_DEFAULTS)


def project_executions(home: Path, run_id: str) -> list:
    """Per-role/task execution state + log refs (FR-0303; no session content)."""
    conn = _service_db(home)
    try:
        project = _project_for_run(conn, run_id)
    finally:
        if conn is not None:
            conn.close()
    if project is None:
        return []
    return _executions(_run_events(project["repo_path"], run_id))
