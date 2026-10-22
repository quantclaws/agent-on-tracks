"""Read-only run activity timeline from opencode event logs (OOB face).

``GET /api/runs/{run_id}/activity`` replays the dispatched-agent session(s)
of the run's current stage: the supervisor's dispatched opencode subprocess
writes its event log to ``<repo>/.opencode/logs/*.jsonl`` (``OPENCODE_LOGGER_DIR``,
relative to the dispatch cwd = the project repo). Rows mentioning
``.tracks/projects/<version>/`` mark the session; every part row of that
session at or after the run's current ``stage.entered`` timestamp becomes a
timeline item — i.e. what the presently working agent has done since the
stage began.

Mechanism: the parse state, log-file selection (newest 3), sort order,
thinking tail quota (30) and response tail quota (200) are shared with the
intake face (``intake_activity`` — same module-level incremental cache, same
``_FileState.runs`` version→sessions association), plus a ``since_ts`` floor
on item timestamps. The run→repo/version resolution reuses the detail
projection path (``projections.project_run_detail`` + ``api_query`` run-plane
reads); the run row (stage/substate/awaiting) comes from the runtime DB
``runs`` table, ``stage_entered_at`` from the latest ``stage.entered`` event
of the current stage, and ``role`` from the latest ``command.issued`` event
whose params are a ``dispatch_agent`` for the current stage.

Polling-grade read path: repeat polls only read appended log bytes (the
shared incremental cache); a shrunk log file resets its entry. Payloads
leave through the shared ``SecretRedactor`` via ``api_query._response``,
like every other read face.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tracks.server import api_query, intake_activity, projections


def _session_states(repo_path: str | Path) -> list[tuple[Path, Any]]:
    """Ingested log-file states (shared incremental cache)."""
    return [
        (path, intake_activity._ingest(path))
        for path in intake_activity._log_files(Path(repo_path))
    ]


def _version_sessions(
    states: list[tuple[Path, Any]], version: str
) -> list[str]:
    """Sorted session ids associated with a version across the log files."""
    return sorted(
        {item for _, state in states for item in state.runs.get(version, ())}
    )


def _merged_owned(
    states: list[tuple[Path, Any]], sessions: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Part items merged across log files plus each item's owning session."""
    merged: dict[str, dict[str, Any]] = {}
    owner: dict[str, str] = {}
    for _, state in states:
        for session in sessions:
            for part_id, item in (state.items.get(session) or {}).items():
                merged[part_id] = item
                owner[part_id] = session
    return merged, owner


def _timed_items(
    merged: dict[str, dict[str, Any]], since_ts: str | None
) -> list[tuple[str, dict[str, Any]]]:
    """Merged items at/after the since floor, time-ordered."""
    floor = intake_activity._ts_key(since_ts or "")
    timed = [
        (part_id, entry)
        for part_id, entry in merged.items()
        if intake_activity._ts_key(entry["ts"]) >= floor
    ]
    return sorted(timed, key=lambda pair: intake_activity._ts_key(pair[1]["ts"]))


def _limit_thinking(
    ordered: list[tuple[str, dict[str, Any]]]
) -> list[tuple[str, dict[str, Any]]]:
    """Drop the oldest thinking items beyond the shared tail quota."""
    thinking = [pair for pair in ordered if pair[1]["kind"] == "thinking"]
    if len(thinking) <= intake_activity._MAX_THINKING:
        return ordered
    dropped = {
        id(pair)
        for pair in thinking[: len(thinking) - intake_activity._MAX_THINKING]
    }
    return [pair for pair in ordered if id(pair) not in dropped]


def collect_run_activity(
    repo_path: str | Path, version: str, since_ts: str | None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Sessions + time-ordered tail items of one run version since a ts.

    Sessions are the ``.tracks/projects/<version>/``-marked sessions across
    the newest log files; items are their merged part rows with
    ``_ts_key(item.ts) >= _ts_key(since_ts)`` (an empty/unparseable
    ``since_ts`` floors at 0.0, i.e. no filtering). Thinking tail quota,
    response tail quota and the serialization budget match the intake face.
    Only sessions still owning a surviving item are returned.
    """
    states = _session_states(repo_path)
    sessions = _version_sessions(states, version)
    if not sessions:
        return [], []
    merged, owner = _merged_owned(states, sessions)
    ordered = _limit_thinking(_timed_items(merged, since_ts))
    tail = ordered[-intake_activity._MAX_ITEMS:]
    items = [entry for _, entry in tail]
    kept = sorted({owner[part_id] for part_id, _ in tail})
    return kept, intake_activity._fit_budget(version, kept, items)


def _dispatch_params(payload: Any) -> dict[str, Any] | None:
    """The ``dispatch_agent`` params dict of a ``command.issued`` payload.

    The real shape nests the kind on the command envelope
    (``payload.command.kind == "dispatch_agent"``, params carry
    ``role``/``stage``); a flat ``payload.params`` carrying ``role`` +
    ``stage`` keys is accepted as the fallback.
    """
    if not isinstance(payload, dict):
        return None
    command = payload.get("command")
    if isinstance(command, dict) and command.get("kind") == "dispatch_agent":
        params = command.get("params")
        if isinstance(params, dict):
            return params
    params = payload.get("params")
    if (
        isinstance(params, dict)
        and isinstance(params.get("role"), str)
        and isinstance(params.get("stage"), str)
    ):
        return params
    return None


def _run_row(repo_path: str, run_id: str) -> Any | None:
    """Latest run-plane row (version/status/stage/substate/awaiting/updated)."""
    rows = api_query._read_rows(
        api_query._tracks_db_path(repo_path),
        "SELECT version, status, stage, substate, awaiting, updated_ts"
        " FROM runs WHERE run_id = ? LIMIT 1",
        (run_id,),
    )
    return rows[0] if rows else None


def _run_context(
    detail: dict[str, Any], row: Any | None
) -> tuple[str, str, Any, Any]:
    """(version, stage, substate, awaiting) with detail fallbacks."""
    version = (
        str(row[0])
        if row is not None and row[0]
        else str(detail.get("version") or "")
    )
    stage = (
        str(row[2])
        if row is not None and row[2]
        else str(detail.get("stage") or "")
    )
    substate = row[3] if row is not None else detail.get("substate")
    awaiting = row[4] if row is not None else None
    return version, stage, substate, awaiting


def _stage_trail(
    repo_path: str, run_id: str, stage: str
) -> tuple[str, str]:
    """(entered ts, role) from the current stage's entered/issued events."""
    entered_at = ""
    role = ""
    for event in api_query._run_events(repo_path, run_id):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "stage.entered" and payload.get("stage") == stage:
            entered_at = event.ts
        if event.type == "command.issued":
            params = _dispatch_params(payload)
            if (
                params is not None
                and params.get("stage") == stage
                and isinstance(params.get("role"), str)
            ):
                role = str(params["role"])
    return entered_at, role


async def run_activity(request: Any) -> Any:
    """GET /api/runs/{run_id}/activity (OOB read face)."""
    run_id = str(request.path_params.get("run_id", ""))
    home = Path(request.app.state.home)
    detail = projections.project_run_detail(home, run_id)
    if detail is None:
        return api_query._not_found("run", run_id)
    project = api_query._project_for_run(home, run_id)
    if project is None:
        return api_query._not_found("run", run_id)
    repo_path = str(project["repo_path"])
    row = _run_row(repo_path, run_id)
    version, stage, substate, awaiting = _run_context(detail, row)
    entered_at, role = _stage_trail(repo_path, run_id, stage)
    sessions, items = collect_run_activity(repo_path, version, entered_at)
    return api_query._response(
        200,
        request,
        {
            "run_id": run_id,
            "version": version,
            "stage": stage,
            "substate": substate,
            "awaiting": awaiting,
            "stage_entered_at": entered_at,
            "role": role,
            "sessions": sessions,
            "items": items,
        },
    )
