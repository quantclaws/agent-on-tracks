"""Read-model projections over tracks.db + service.db (IF-QUERY-001).

Every projection is computed on demand from the event stores and project
documents (read-only connections); nothing here keeps in-memory "running"
facts. Progress reports test counts and AC-closure counts separately with
identifiable sources; no fabricated percentages (FR-0302). The human todo
projection lists only legitimate human decisions — quota/CI/network waits
never appear (FR-0307). Execution state is run/task level and never exposes
Agent session content (FR-0303).

Contract token: IF-QUERY-001.
"""

from __future__ import annotations

import base64
import json
import sqlite3
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


# -- read-only connection helpers -------------------------------------------


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _service_db(home: Path) -> sqlite3.Connection | None:
    return _ro(Path(home) / "service.db")


def _tracks_db(repo_path: str) -> sqlite3.Connection | None:
    return _ro(Path(repo_path) / ".tracks" / "runtime" / "tracks.db")


def _blobs_home(repo_path: str) -> Path:
    return Path(repo_path) / ".tracks" / "runtime" / "blobs"


def _rows(conn: sqlite3.Connection | None, sql: str, args: tuple = ()) -> list[tuple]:
    if conn is None:
        return []
    return conn.execute(sql, args).fetchall()


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


def _run_events(repo_path: str, run_id: str) -> list[EventEnvelope]:
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
    return [
        event_envelope_from_row(row, _load_payload(row[8], repo_path))
        for row in rows
    ]


def _state_of(repo_path: str, run_id: str) -> State:
    return project_state(_run_events(repo_path, run_id))


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
            "SELECT run_id, version, status, stage, substate, awaiting FROM runs "
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


# -- public projections -----------------------------------------------------


def project_overview(home: Path) -> dict:
    """Projects + runs + per-run human-todo counts (§1e)."""
    conn = _service_db(home)
    try:
        projects = _projects(conn)
        runs = []
        todo_count = 0
        for project in projects:
            for run in _project_runs(project["repo_path"]):
                state = _state_of(project["repo_path"], run["run_id"])
                todo = _todo_for(run["run_id"], state, project)
                if todo is not None:
                    todo_count += 1
                runs.append(
                    {
                        "run_id": run["run_id"],
                        "title": run["version"],
                        "version": run["version"],
                        "stage": run["stage"],
                        "control_state": _run_control_state(
                            conn, state, run["run_id"], []
                        ),
                        "human_todos": 1 if todo is not None else 0,
                    }
                )
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
            "stage": state.stage,
            "substate": state.substate,
            "control_state": _control_state(
                state, events, pause_label, wait, _is_queued(conn, run_id)
            ),
            "pause": pause,
            "wait": wait,
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
    """Merged tracks.db + service_events timeline with cursor (§1e)."""
    conn = _service_db(home)
    try:
        project = _project_for_run(conn, run_id)
        if project is None:
            return {"events": [], "cursor": ""}
        repo_path = project["repo_path"]
        rows = _timeline_rows(conn, repo_path, run_id)
        rows = _filter_timeline(rows, command_id, task_id, ac_id)
        rows = _after_cursor(rows, after_seq)
        return {"events": rows, "cursor": _cursor_of(rows)}
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
