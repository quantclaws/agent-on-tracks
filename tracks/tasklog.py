"""Deterministic, event-backed M-IMPL task-log projection."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from tracks import paths
from tracks.kernel.events import EventEnvelope

_PHASE_EVENTS = frozenset(
    {
        "red.checkpointed",
        "green.committed",
        "green.no_change",
        "refactor.committed",
        "refactor.no_change",
    }
)
_TASK_GATE_CHECKS = frozenset(
    {
        "red_valid",
        "red_invalid",
        "green",
        "impl_defect",
        "unknown_attribution",
        "regression",
        "task_review",
        "budget",
        "scope",
        "public_interface",
        "test_defect",
        "criteria_pack_mismatch",
        "full_suite",
        "commit",
    }
)
_GATE_LABELS = {
    "red_valid": "Red Gate",
    "red_invalid": "Red Gate",
    "green": "Green Gate",
    "impl_defect": "Green Gate",
    "unknown_attribution": "Green Gate",
    "regression": "Refactor Gate",
    "task_review": "Task Review",
    "budget": "Task Review",
    "scope": "Task Review",
    "public_interface": "Task Review",
}
_TASK_PRISM_SUBSTATES = frozenset({"PRISM_RED", "PRISM_FINAL", "DIAGNOSE"})


def task_log_path(home: Path, version: str) -> Path:
    """Return the version-owned task-log projection path."""
    return paths.version_dir(home, version) / "task-log.md"


def build_task_log(
    events: Iterable[EventEnvelope],
    *,
    run_id: str,
    version: str,
) -> str:
    """Render one run/version task log from persisted Runtime events only."""
    scoped = _scoped_events(events, run_id, version)
    task_streams = _task_streams(scoped)
    lines = [
        "# M-IMPL Task Log",
        "",
        f"- Run: {_code(run_id)}",
        f"- Version: {_code(version)}",
    ]
    if not task_streams:
        lines.extend(["", "- No M-IMPL task events recorded."])
        return "\n".join(lines) + "\n"
    for task_id in sorted(task_streams):
        lines.extend(["", f"## Task {_code(task_id)}", ""])
        lines.extend(_task_lines(task_streams[task_id], scoped))
    return "\n".join(lines) + "\n"


def rebuild_task_log(home: Path, events: Iterable[EventEnvelope]) -> Path | None:
    """Atomically rebuild a task log from one persisted run event stream."""
    persisted = tuple(events)
    scope = _event_scope(persisted)
    if scope is None:
        return None
    run_id, version = scope
    target = task_log_path(home, version)
    _atomic_write(target, build_task_log(persisted, run_id=run_id, version=version))
    return target


def _event_scope(events: tuple[EventEnvelope, ...]) -> tuple[str, str] | None:
    if not events:
        return None
    run_id = _clean(getattr(events[0], "run_id", None))
    version = _clean(getattr(events[0], "version", None))
    return (run_id, version) if run_id and version else None


def _scoped_events(
    events: Iterable[EventEnvelope],
    run_id: str,
    version: str,
) -> list[EventEnvelope]:
    return sorted(
        (event for event in events if event.run_id == run_id and event.version == version),
        key=lambda event: event.seq,
    )


def _task_streams(events: list[EventEnvelope]) -> dict[str, list[EventEnvelope]]:
    contexts = _command_contexts(events)
    streams = {task_id: [] for task_id in _declared_task_ids(events)}
    active_task_id = None
    for event in events:
        event_type = event.type
        task_id = _event_task_id(event)
        if event_type == "task.started":
            active_task_id = task_id or active_task_id
            _append_task_event(streams, active_task_id, event)
            continue
        if event_type == "writelock.granted":
            _append_task_event(streams, task_id, event)
            continue
        if event_type == "task.completed":
            task_id = task_id or active_task_id
            _append_task_event(streams, task_id, event)
            if task_id == active_task_id:
                active_task_id = None
            continue
        task_id = _associated_task_id(event, active_task_id, contexts)
        _append_task_event(streams, task_id, event)
    return streams


def _command_contexts(events: list[EventEnvelope]) -> dict[str, tuple[str | None, str | None]]:
    contexts = {}
    for event in events:
        if event.type != "command.issued":
            continue
        command = _payload(event).get("command")
        if not isinstance(command, dict):
            continue
        params = command.get("params")
        if command.get("kind") != "dispatch_agent" or not isinstance(params, dict):
            continue
        command_id = _clean(event.command_id) or _clean(command.get("command_id"))
        if command_id:
            contexts[command_id] = (_clean(params.get("role")), _clean(params.get("substate")))
    return contexts


def _declared_task_ids(events: list[EventEnvelope]) -> set[str]:
    taskgraphs = [event for event in events if event.type == "taskgraph.committed"]
    if not taskgraphs:
        return set()
    payload = _payload(taskgraphs[-1])
    task_ids = _string_set(payload.get("task_ids"))
    tasks = payload.get("tasks")
    if isinstance(tasks, list):
        task_ids.update(
            task_id
            for task in tasks
            if isinstance(task, dict)
            for task_id in (_clean(task.get("task_id")),)
            if task_id
        )
    return task_ids


def _associated_task_id(
    event: EventEnvelope,
    active_task_id: str | None,
    contexts: dict[str, tuple[str | None, str | None]],
) -> str | None:
    if event.type in _PHASE_EVENTS or event.type == "test.committed":
        return _event_task_id(event) or active_task_id
    if event.type in ("verdict.passed", "verdict.failed"):
        check = _clean(_payload(event).get("check"))
        if check in _TASK_GATE_CHECKS:
            return _event_task_id(event) or active_task_id
        return None
    if event.type != "prism.verdict":
        return None
    role, substate = contexts.get(event.command_id or "", (None, None))
    if role == "prism" and substate not in _TASK_PRISM_SUBSTATES:
        return None
    if substate in _TASK_PRISM_SUBSTATES or active_task_id:
        return _event_task_id(event) or active_task_id
    return None


def _append_task_event(
    streams: dict[str, list[EventEnvelope]],
    task_id: str | None,
    event: EventEnvelope,
) -> None:
    if task_id:
        streams.setdefault(task_id, []).append(event)


def _task_lines(events: list[EventEnvelope], scoped: list[EventEnvelope]) -> list[str]:
    lines = ["### Task Lifecycle"]
    lines.extend(_lifecycle_lines(events))
    lines.extend(["", "### Phase 1 Red"])
    lines.extend(_red_lines(events))
    lines.extend(["", "### Phase 2 Green"])
    lines.extend(_green_lines(events))
    lines.extend(["", "### Phase 3 Refactor"])
    lines.extend(_refactor_lines(events))
    lines.extend(["", "### Runtime Quality Gate"])
    lines.extend(_gate_lines(events, scoped))
    lines.extend(["", "### Task Completion"])
    lines.extend(_completion_lines(events))
    return lines


def _lifecycle_lines(events: list[EventEnvelope]) -> list[str]:
    if any(event.type == "task.started" for event in events):
        return ["- task.started: `recorded`"]
    if any(event.type == "writelock.granted" for event in events):
        return ["- writelock.granted: `recorded` (task start pending)"]
    return ["- No task.started event recorded."]


def _red_lines(events: list[EventEnvelope]) -> list[str]:
    lines = []
    for event in events:
        if event.type != "red.checkpointed":
            continue
        payload = _payload(event)
        _append_once(
            lines,
            "- Public attempt: "
            + _code(_attempt(payload))
            + "; Tracks-R: ref="
            + _code(_clean(payload.get("ref")))
            + " sha="
            + _code(_clean(payload.get("r_sha"))),
        )
    return lines or ["- No Runtime Red checkpoint recorded."]


def _green_lines(events: list[EventEnvelope]) -> list[str]:
    lines = []
    for event in events:
        if event.type not in ("green.committed", "green.no_change"):
            continue
        payload = _payload(event)
        if event.type == "green.committed":
            _append_once(
                lines,
                "- Public attempt: "
                + _code(_attempt(payload))
                + "; Green identity: g_sha="
                + _code(_clean(payload.get("g_sha")) or _clean(payload.get("commit_sha")))
                + "; base_sha="
                + _code(_clean(payload.get("base_sha")))
                + "; Tracks-R="
                + _code(_clean(payload.get("r_sha"))),
            )
        else:
            _append_once(
                lines,
                "- Public attempt: "
                + _code(_attempt(payload) or "")
                + "; Green result: no-change ("
                + _code(_clean(payload.get("reason")))
                + ")",
            )
    return lines or ["- No Runtime Green commit recorded."]


def _refactor_lines(events: list[EventEnvelope]) -> list[str]:
    lines = []
    for event in events:
        if event.type not in ("refactor.committed", "refactor.no_change"):
            continue
        payload = _payload(event)
        result = "committed" if event.type == "refactor.committed" else "no-change"
        line = "- Refactor result: " + _code(result)
        commit_sha = _clean(payload.get("commit_sha"))
        if commit_sha:
            line += "; commit_sha=" + _code(commit_sha)
        _append_once(lines, line)
    return lines or ["- No Runtime refactor result recorded."]


def _gate_lines(events: list[EventEnvelope], scoped: list[EventEnvelope]) -> list[str]:
    contexts = _command_contexts(scoped)
    lines = []
    for event in events:
        if event.type in ("verdict.passed", "verdict.failed"):
            line = _gate_verdict_line(event)
        elif event.type == "prism.verdict":
            line = _prism_verdict_line(event, contexts)
        elif event.type == "test.committed":
            line = _test_commit_line(event)
        else:
            line = None
        if line:
            _append_once(lines, line)
    return lines or ["- No Runtime task gate verdict recorded."]


def _gate_verdict_line(event: EventEnvelope) -> str | None:
    payload = _payload(event)
    check = _clean(payload.get("check"))
    if check not in _TASK_GATE_CHECKS:
        return None
    status = "passed" if event.type == "verdict.passed" else "failed"
    label = _GATE_LABELS.get(check, "Runtime Gate")
    line = f"- {label}: {_code(status)} (check={_code(check)})"
    attempt = _attempt(payload)
    return line + f"; public attempt={_code(attempt)}" if attempt else line


def _prism_verdict_line(
    event: EventEnvelope,
    contexts: dict[str, tuple[str | None, str | None]],
) -> str:
    _role, substate = contexts.get(event.command_id or "", (None, None))
    label = {
        "PRISM_RED": "Prism Red Review",
        "PRISM_FINAL": "Prism Final Review",
        "DIAGNOSE": "Prism Diagnosis",
    }.get(substate, "Prism Review")
    return f"- {label}: {_code(_clean(_payload(event).get('verdict')))}"


def _test_commit_line(event: EventEnvelope) -> str:
    commit_sha = _clean(_payload(event).get("commit_sha"))
    return "- Shield Test Commit: " + _code(commit_sha or "recorded")


def _completion_lines(events: list[EventEnvelope]) -> list[str]:
    return (
        ["- task.completed: `recorded`"]
        if any(event.type == "task.completed" for event in events)
        else ["- No task.completed event recorded."]
    )


def _event_task_id(event: EventEnvelope) -> str | None:
    payload = _payload(event)
    return _clean(payload.get("task_id")) or _clean(event.task_id)


def _payload(event: EventEnvelope) -> dict:
    return event.payload if isinstance(event.payload, dict) else {}


def _string_set(value: object) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {item for raw in value for item in (_clean(raw),) if item}


def _attempt(payload: dict) -> str | None:
    value = payload.get("attempt")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return _clean(value)


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.replace("`", "'").split())
    return value or None


def _code(value: str | None) -> str:
    return f"`{value or '-'}`"


def _append_once(lines: list[str], line: str) -> None:
    if line not in lines:
        lines.append(line)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
