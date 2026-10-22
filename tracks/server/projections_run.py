"""Run/overview/task projections for the §1e read model (IF-QUERY-001).

Task progress, gates/executions, overview, run detail, the AC chain, todos,
and per-run execution state. Public names are re-exported by
``tracks.server.projections``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State

from .projections_core import (
    _ac_closed,
    _chain_rows,
    _control_state,
    _is_queued,
    _lease,
    _load_payload,
    _pause_state,
    _project_for_run,
    _project_runs,
    _projects,
    _rows,
    _run_events,
    _service_db,
    _state_of,
    _todo_for,
    _tracks_db,
    _wait_spec,
    project_state,
)
from .projections_docs import _version_story_title
from .projections_gate import (
    _awaiting_gate,
    _blocked,
    _current_failure,
    _return_options,
    _run_branch,
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


_TEST_RESULT_TYPES = frozenset(
    {"local_gate.passed", "local_gate.failed", "full.executed", "red.validated", "test.collected"})


def _latest_tests_passed(events: list[EventEnvelope]) -> int | None:
    for event in reversed(events):
        if event.type not in _TEST_RESULT_TYPES:
            continue
        found = _extract_passed(event.payload or {})
        if found is not None:
            return found
    return None


_TASK_PROGRESS_EVENTS = frozenset(
    {"taskgraph.committed", "task.started", "task.completed", "verdict.failed"}
)


def _batch_value(raw: Any) -> int | str | None:
    """Normalize a tasks.json batch to its sortable display value."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def _batch_sort_key(batch: int | str | None) -> tuple[int, int, str]:
    if isinstance(batch, int):
        return (0, batch, "")
    return (1, 0, "" if batch is None else str(batch))


def _task_display(raw: dict) -> tuple[str, bool]:
    """Display title + verification-only marker from a raw tasks.json task."""
    title = raw.get("title")
    description = raw.get("description")
    source = title if isinstance(title, str) and title.strip() else description
    verification_only = bool(raw.get("verification_only"))
    for value in (title, description):
        if isinstance(value, str) and re.match(
            r"^\s*(?:【)?verification-only\b", value
        ):
            verification_only = True
    if not isinstance(source, str):
        return "", verification_only
    first_line = source.splitlines()[0] if source.splitlines() else ""
    text = " ".join(first_line.split())
    text = re.sub(r"^verification-only\s+", "", text)
    text = re.sub(r"^【verification-only[^】]*】\s*", "", text)
    return text[:60], verification_only


def _task_completed(events: list[EventEnvelope]) -> tuple[int, set[str]]:
    graph_event = next(
        (event for event in reversed(events) if event.type == "taskgraph.committed"),
        None,
    )
    graph_seq = graph_event.seq if graph_event is not None else 0
    completed = {
        event.payload.get("task_id") or event.task_id
        for event in events
        if event.type == "task.completed" and event.seq > graph_seq
    }
    completed = {task_id for task_id in completed if isinstance(task_id, str) and task_id}
    if graph_event is not None:
        retained = (graph_event.payload or {}).get("retained_completed_task_ids")
        if isinstance(retained, list):
            completed.update(
                task_id for task_id in retained
                if isinstance(task_id, str) and task_id
            )
    return graph_seq, completed


def _task_event_state(events: list[EventEnvelope]) -> dict[str, Any]:
    """Derive the current generation's completions, starts, failures, running."""
    graph_seq, completed = _task_completed(events)
    starts: dict[str, int] = {}
    failures: dict[str, int] = {}
    for event in events:
        if event.seq <= graph_seq:
            continue
        task_id = (event.payload or {}).get("task_id") or event.task_id
        if not isinstance(task_id, str) or not task_id:
            continue
        if event.type == "task.started":
            starts[task_id] = event.seq
        elif event.type == "verdict.failed":
            failures[task_id] = event.seq

    running = None
    for task_id, _seq in sorted(starts.items(), key=lambda item: item[1], reverse=True):
        if task_id not in completed:
            running = task_id
            break

    return {
        "completed": completed,
        "starts": starts,
        "failures": failures,
        "running": running,
    }


def _task_row(raw: dict, event_state: dict[str, Any], state: State) -> dict:
    task_id = raw["task_id"]
    if task_id in event_state["completed"]:
        task_state = "done"
    elif event_state["failures"].get(task_id, 0) > event_state["starts"].get(task_id, 0):
        task_state = "failed"
    elif task_id == event_state["running"]:
        task_state = "running"
    else:
        task_state = "pending"
    phase = (
        state.substate
        if task_state == "running" and state.stage == "M-IMPL"
        else None
    )
    title, verification_only = _task_display(raw)
    raw_depends = raw.get("depends_on")
    depends_on = (
        [dep for dep in raw_depends if isinstance(dep, str)]
        if isinstance(raw_depends, list)
        else []
    )
    row = {
        "task_id": task_id, "title": title, "state": task_state,
        "phase": phase, "depends_on": depends_on,
    }
    if verification_only:
        row["verification_only"] = True
    return row


def _task_batches(
    tasks: list[dict], event_state: dict[str, Any], state: State
) -> dict[str, Any]:
    groups: dict[int | str | None, list[dict]] = {}
    task_batches: dict[str, int | str | None] = {}
    order: list[int | str | None] = []
    for raw in tasks:
        task_id = raw["task_id"]
        batch = _batch_value(raw.get("batch"))
        task_batches[task_id] = batch
        if batch not in groups:
            groups[batch] = []
            order.append(batch)
        groups[batch].append(_task_row(raw, event_state, state))

    batches_detail = [
        {"batch": batch, "tasks": groups[batch]}
        for batch in sorted(order, key=_batch_sort_key)
    ]
    running = event_state["running"]
    current_batch = task_batches.get(running) if running else None
    if running is None:
        upcoming = next(
            (row["task_id"] for group in batches_detail for row in group["tasks"]
             if row["state"] != "done"),
            None,
        )
        current_batch = task_batches.get(upcoming) if upcoming else (
            order[-1] if order else None
        )
    return {
        "groups": groups,
        "batches_detail": batches_detail,
        "current_batch": current_batch,
    }


def _task_progress(
    repo_path: str, version: str | None, state: State, events: list[EventEnvelope]
) -> dict | None:
    """Orthogonal work-unit progress for the M-IMPL stage card.

    The phase pipeline stays the state diagram; this block reports the
    batches/tasks axis read from ``tasks.json`` plus the current generation's
    completion/start/failure events. Missing or malformed inputs degrade to
    ``None`` rather than inventing a task graph.
    """
    if not version:
        return None
    target = Path(repo_path) / ".tracks" / "projects" / version / "tasks.json"
    if not target.exists():
        return None
    has_task_events = any(event.type in _TASK_PROGRESS_EVENTS for event in events)
    if state.stage != "M-IMPL" and not has_task_events:
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw_tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw_tasks, list):
        return None
    tasks = [
        task for task in raw_tasks
        if isinstance(task, dict) and isinstance(task.get("task_id"), str) and task["task_id"]
    ]
    if not tasks:
        return None

    event_state = _task_event_state(events)
    batches = _task_batches(tasks, event_state, state)
    return {
        "done": sum(
            1 for task in tasks if task["task_id"] in event_state["completed"]
        ),
        "total": len(tasks),
        "batch": batches["current_batch"],
        "batches": len(batches["groups"]),
        "current_task_id": event_state["running"],
        "current_phase": state.substate if state.stage == "M-IMPL" else None,
        "batches_detail": batches["batches_detail"],
    }


_GATE_TYPES = {"local_gate.passed": "passed", "local_gate.failed": "failed"}


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
    completed = {event.payload.get("task_id") for event in events if event.type == "task.completed"}
    executions = []
    for task_id in started:
        if task_id in completed:
            continue
        executions.append(
            {
                "role": _task_role(events, task_id), "task_id": task_id,
                "state": "failed" if _task_failed(events, task_id) else "executing",
                "log_ref": _task_log_ref(events, task_id),
            })
    return executions


def _task_events(events: list[EventEnvelope], task_id: str) -> list[EventEnvelope]:
    return [
        event for event in events if (
            event.payload or {}
        ).get("task_id") == task_id or event.task_id == task_id]


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


# -- public projections -----------------------------------------------------


def _backlog_group(repo_path: str) -> dict:
    """§1e.2/§1d.3 backlog: entries by ULID, dispositions folded in; count excludes."""
    entries: dict[str, dict] = {}
    dispositions: dict[str, dict] = {}
    rows = _rows(
        _tracks_db(repo_path),
        "SELECT run_id, ts, version, type, payload FROM events"
        " WHERE type IN ('backlog.recorded', 'backlog.dispositioned') ORDER BY seq",
    )
    for run_id, ts, version, event_type, raw in rows:
        payload = _load_payload(raw, repo_path)
        if event_type == "backlog.recorded":
            entries[run_id] = {
                "entry_id": run_id,
                "version": version,
                "source": payload.get("source"),
                "content": payload.get("content"),
                "recorded_ts": ts,
                "disposition": None,
                "to": None,
            }
        elif isinstance(payload.get("entry"), str):
            dispositions[payload["entry"]] = {
                "disposition": payload.get("disposition"),
                "to": payload.get("to"),
            }
    for entry_id, marker in dispositions.items():
        if entry_id in entries:
            entries[entry_id].update(marker)
    backlog_rows = sorted(entries.values(), key=lambda row: str(row["recorded_ts"] or ""))
    return {
        "count": sum(1 for row in backlog_rows if row["disposition"] is None),
        "entries": backlog_rows,
    }


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
    return {"run_id": run["run_id"], "title": titles[run["version"]] or run["run_id"],
        "version": run["version"], "status": run["status"], "stage": run["stage"],
        "updated_ts": run["updated_ts"],
        "control_state": _run_control_state(conn, state, run["run_id"], []),
        "human_todos": 1 if todo is not None else 0,}


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
                    "project_id": project["project_id"], "repo_path": project["repo_path"],
                    "version": project["version"],
                } for project in projects
            ],
            "runs": runs, "human_todo_count": todo_count,}
    finally:
        if conn is not None:
            conn.close()


def _run_control_state(
    conn: sqlite3.Connection | None, state: State, run_id: str, events: list[EventEnvelope]
) -> str:
    pause, pause_label = _pause_state(conn, run_id)
    wait = _wait_spec(conn, run_id)
    return _control_state(state, events, pause_label, wait, _is_queued(conn, run_id))


# M-VERIFY's handler chain has no per-step substates (it stays VERIFYING),
# so the operator-facing progress is derived from the chain's own events.
_VERIFY_STEPS = (
    ("freeze", "冻结候选"),
    ("local_gates", "本地门禁"),
    ("version", "版本声明"),
    ("ci", "CI 回读"),
    ("prism", "Prism 复审"),
)
_VERIFY_AREA_STEP = {
    "freeze": "freeze",
    "ci_readback": "ci",
    "version_gate": "version",
    "host_contract": "local_gates",
}


def _verify_window(events: list[EventEnvelope]) -> list[EventEnvelope]:
    """Events after the latest M-VERIFY entry (a re-walk starts fresh)."""
    start = 0
    for index, event in enumerate(events):
        if event.type not in ("stage.entered", "stage.recovered"):
            continue
        payload = event.payload or {}
        stage = str(payload.get("stage") or payload.get("to_stage") or "")
        if stage == "M-VERIFY":
            start = index + 1
    return events[start:]


_VERIFY_FIXED_STATES = {
    "candidate.frozen": ("freeze", "done"),
    "candidate.stale": ("freeze", "failed"),
}


def _verify_local_gate_state(event: EventEnvelope, payload: dict) -> tuple[str, str] | None:
    if event.type not in ("local_gate.passed", "local_gate.failed"):
        return None
    key = "version" if str(payload.get("kind") or "") == "version" else "local_gates"
    return key, "done" if event.type == "local_gate.passed" else "failed"


def _verify_ci_state(payload: dict) -> tuple[str, str]:
    passed = payload.get("api_verified") is True and payload.get("status") == "passed"
    return "ci", "done" if passed else "failed"


def _verify_prism_state(payload: dict) -> tuple[str, str] | None:
    verdict = str(payload.get("verdict") or "")
    if verdict == "pass":
        return "prism", "done"
    if verdict in ("fail", "failed", "revise"):
        return "prism", "failed"
    return None


def _verify_event_state(event: EventEnvelope) -> tuple[str, str] | None:
    payload = event.payload or {}
    fixed = _VERIFY_FIXED_STATES.get(event.type)
    if fixed is not None:
        return fixed
    local_gate = _verify_local_gate_state(event, payload)
    if local_gate is not None:
        return local_gate
    if event.type == "ci.run_observed":
        return _verify_ci_state(payload)
    if event.type == "prism.verdict":
        return _verify_prism_state(payload)
    return None


def _verify_event_states(window: list[EventEnvelope]) -> dict[str, str]:
    states = {key: "pending" for key, _label in _VERIFY_STEPS}
    for event in window:
        found = _verify_event_state(event)
        if found is not None:
            states[found[0]] = found[1]
    return states


def _verify_progress(
    state: State, events: list[EventEnvelope], blocked: dict | None
) -> dict | None:
    """M-VERIFY sub-step progress (freeze / gates / version / CI / Prism)."""
    blocked_stage = (blocked or {}).get("stage")
    if state.stage != "M-VERIFY" and blocked_stage != "M-VERIFY":
        return None
    states = _verify_event_states(_verify_window(events))
    if blocked is not None:
        key = _VERIFY_AREA_STEP.get(str(blocked.get("area") or ""))
        if key is not None:
            states[key] = "failed"
    else:
        for key, _label in _VERIFY_STEPS:
            if states[key] == "pending":
                states[key] = "active"
                break
    return {
        "stage": "M-VERIFY",
        "steps": [
            {"key": key, "label": label, "state": states[key]}
            for key, label in _VERIFY_STEPS
        ],
    }


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
        task_progress = _task_progress(
            repo_path, state.version or version, state, events
        )
        blocked = _blocked(events, state)
        return {"run_id": run_id, "version": state.version or version, "status": state.status,
            "stage": state.stage, "substate": state.substate,
            "branch": _run_branch(state),
            "control_state": _control_state(
                state, events, pause_label, wait, _is_queued(conn, run_id),
                blocked=blocked is not None,
            ),
            "pause": pause, "wait": wait,
            **({"blocked": blocked} if blocked is not None else {}),
            "last_failure": _current_failure(events),
            "verify_progress": _verify_progress(state, events, blocked),
            "awaiting_gate": _awaiting_gate(
                state, repo_path, state.version or version, events),
            "return_options": _return_options(repo_path, run_id),
            "progress": {
                "tasks_done": state.tasks_completed, "tasks_total": state.tasks_total,
                "tests_passed": _latest_tests_passed(events), "ac_closed": _ac_closed(chain),
                "ac_total": len(chain),
            },
            "task_progress": task_progress,
            "quality": {"gates": _gates(events)}, "executions": _executions(events),
            "lease": _lease(conn, run_id),}
    finally:
        if conn is not None:
            conn.close()


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
