"""Read-only intake activity timeline from opencode event logs (OOB face).

``GET /api/projects/{pid}/intakes/{intake_id}/activity`` replays the Scribe
intake-evaluation session whose dispatched opencode subprocess wrote its
event log to ``<repo>/.opencode/logs/*.jsonl`` (``OPENCODE_LOGGER_DIR``,
relative to the dispatch cwd = the project repo). Rows carrying
``intakes/<intake_id>/`` mark the session; every part row of that session
becomes a timeline item.

Polling-grade read path: a module-level cache keeps per-file parse state
(size / consumed offset / trailing partial line / items by session / intake
association), so repeat polls only read appended bytes; a shrunk file
(rotation/truncation) resets its entry. Payloads leave through the shared
``SecretRedactor`` via ``api_query._response``, like every other read face.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_MAX_FILES = 3  # newest log files scanned per request
_CACHE_FILES = 6  # cached file states (module-level incremental cache)
_MAX_ITEMS_SESSION = 300  # cached items per session per file
_MAX_ITEMS = 200  # response tail quota
_MAX_THINKING = 30  # thinking-item tail quota inside the response
_MAX_TEXT = 1200  # per-item text quota (chars)
_MAX_BYTES = 400 * 1024  # response serialization ceiling
_INTAKE_RE = re.compile(r"intakes/([A-Za-z0-9][A-Za-z0-9_-]*)")
_RUN_RE = re.compile(r"\.tracks/projects/([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass
class _FileState:
    """Incremental parse state of one log file (see module docstring)."""

    size: int = 0
    consumed: int = 0
    partial: str = ""
    items: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    intakes: dict[str, set[str]] = field(default_factory=dict)
    runs: dict[str, set[str]] = field(default_factory=dict)


_CACHE: dict[str, _FileState] = {}


def _clip(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return text[:_MAX_TEXT]


def _ts_key(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _tool_label(name: str, given: dict[str, Any]) -> str:
    target = given.get("filePath")
    if name == "read" and isinstance(target, str) and target:
        return f"读取 {target}"
    command = given.get("command")
    if name == "bash" and isinstance(command, str) and command.strip():
        return f"执行 {' '.join(command.split())[:80]}"
    pattern = given.get("pattern")
    if name in ("grep", "glob") and isinstance(pattern, str) and pattern:
        return f"搜索 {pattern}"
    blob = json.dumps(given, ensure_ascii=False, default=str)
    return f"工具 {name} {blob[:120]}"


def _tool_item(part: dict[str, Any], ts_text: str) -> dict[str, Any]:
    name = part.get("tool")
    name_text = name if isinstance(name, str) and name else "unknown"
    state = part.get("state")
    state = state if isinstance(state, dict) else {}
    given = state.get("input")
    given = given if isinstance(given, dict) else {}
    output = state.get("output")
    if isinstance(output, str) and output:
        body: Any = output
    else:
        body = json.dumps(given, ensure_ascii=False, default=str)
    return {
        "ts": ts_text,
        "kind": "tool",
        "tool": name_text,
        "label": _tool_label(name_text, given),
        "text": _clip(body),
    }


def _part_item(part: dict[str, Any], ts: Any) -> dict[str, Any] | None:
    """One timeline item from a ``message.part.updated`` part (None = skip)."""
    ts_text = ts if isinstance(ts, str) else ""
    ptype = part.get("type")
    if ptype == "text":
        return {
            "ts": ts_text,
            "kind": "text",
            "tool": None,
            "label": "",
            "text": _clip(part.get("text")),
        }
    if ptype == "reasoning":
        return {
            "ts": ts_text,
            "kind": "thinking",
            "tool": None,
            "label": "",
            "text": _clip(part.get("text")),
        }
    if ptype == "tool":
        return _tool_item(part, ts_text)
    if ptype == "step-start":
        return {
            "ts": ts_text,
            "kind": "step",
            "tool": None,
            "label": "步骤开始",
            "text": "",
        }
    if ptype == "step-finish":
        return {
            "ts": ts_text,
            "kind": "step",
            "tool": None,
            "label": "步骤结束",
            "text": "",
        }
    return None


def _envelope(line: str) -> tuple[str, dict[str, Any], Any] | None:
    """The (sessionID, props, timestamp) of one log line (None = skip)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    payload = obj.get("payload")
    props = payload.get("properties") if isinstance(payload, dict) else None
    if not isinstance(props, dict):
        return None
    session = props.get("sessionID")
    if not isinstance(session, str) or not session:
        return None
    return session, props, obj.get("timestamp")


def _mark_intakes(line: str, session: str, state: _FileState) -> None:
    """Record intake→session association from any line mentioning intakes/."""
    if "intakes/" not in line and ".tracks/projects/" not in line:
        return
    if "intakes/" in line:
        for match in _INTAKE_RE.finditer(line):
            state.intakes.setdefault(match.group(1), set()).add(session)
    if ".tracks/projects/" in line:
        for match in _RUN_RE.finditer(line):
            state.runs.setdefault(match.group(1), set()).add(session)


def _fold_part(
    props: dict[str, Any], ts: Any, session: str, state: _FileState
) -> None:
    """Fold one ``message.part.updated`` part into the session bucket."""
    part = props.get("part")
    if not isinstance(part, dict):
        return
    item = _part_item(part, ts)
    if item is None:
        return
    bucket = state.items.setdefault(session, {})
    key = part.get("id")
    if not isinstance(key, str) or not key:
        key = f"noid-{len(bucket)}"
    if len(bucket) >= _MAX_ITEMS_SESSION and key not in bucket:
        bucket.pop(next(iter(bucket)))
    bucket[key] = item


def _parse_line(line: str, state: _FileState) -> None:
    """Fold one log line into the file state (session mark and/or item)."""
    if (
        "message.part.updated" not in line
        and "intakes/" not in line
        and ".tracks/projects/" not in line
    ):
        return
    found = _envelope(line)
    if found is None:
        return
    session, props, ts = found
    _mark_intakes(line, session, state)
    if "message.part.updated" not in line:
        return
    _fold_part(props, ts, session, state)


def _reset_state(state: _FileState) -> None:
    """Drop a file's cached parse state (rotation/truncation detected)."""
    state.size = 0
    state.consumed = 0
    state.partial = ""
    state.items.clear()
    state.intakes.clear()
    state.runs.clear()


def _read_new(path: Path, state: _FileState, size: int) -> None:
    """Consume the file's appended bytes into the cached state."""
    try:
        with open(path, "rb") as handle:
            handle.seek(state.consumed)
            raw = handle.read()
    except OSError:
        raw = b""
    text = state.partial + raw.decode("utf-8", errors="replace")
    state.partial = ""
    lines = text.split("\n")
    if raw and not raw.endswith(b"\n") and lines:
        state.partial = lines.pop()
    for line in lines:
        if line:
            _parse_line(line, state)
    if state.partial:
        state.consumed = size - len(state.partial.encode("utf-8"))
    else:
        state.consumed = size


def _evict_cache(keep: str) -> None:
    """Bound the module cache to ``_CACHE_FILES`` entries."""
    while len(_CACHE) > _CACHE_FILES:
        victim = next((name for name in _CACHE if name != keep), None)
        if victim is None:
            break
        del _CACHE[victim]


def _ingest(path: Path) -> _FileState:
    """Advance one file's cached state by reading only appended bytes."""
    key = str(path)
    try:
        size = path.stat().st_size
    except OSError:
        return _FileState()
    state = _CACHE.get(key)
    if state is None:
        state = _FileState()
        _CACHE[key] = state
    elif size < state.size or state.consumed > size:
        _reset_state(state)
    if state.consumed < size or state.partial:
        _read_new(path, state, size)
    state.size = size
    _evict_cache(key)
    return state


def _log_files(repo: Path) -> list[Path]:
    """The newest ``_MAX_FILES`` ``*.jsonl`` logs, oldest-first."""
    try:
        files = [p for p in (repo / ".opencode" / "logs").glob("*.jsonl") if p.is_file()]
    except OSError:
        return []

    def _key(path: Path) -> tuple[float, str]:
        try:
            return (path.stat().st_mtime, path.name)
        except OSError:
            return (0.0, path.name)

    return sorted(files, key=_key)[-_MAX_FILES:]


def _fit_budget(
    intake_id: str, sessions: list[str], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop head items until the serialized response fits ``_MAX_BYTES``."""
    while len(items) > 1:
        raw = json.dumps(
            {"intake_id": intake_id, "sessions": sessions, "items": items},
            ensure_ascii=False,
        ).encode("utf-8")
        if len(raw) <= _MAX_BYTES:
            break
        del items[0]
    return items


def collect_activity(
    repo_path: str | Path, intake_id: str
) -> tuple[list[str], list[dict[str, Any]]]:
    """Sessions + time-ordered tail items of one intake (no HTTP needed)."""
    states = [(path, _ingest(path)) for path in _log_files(Path(repo_path))]
    sessions = sorted(
        {item for _, state in states for item in state.intakes.get(intake_id, ())}
    )
    if not sessions:
        return [], []
    merged: dict[str, dict[str, Any]] = {}
    for _, state in states:
        for session in sessions:
            for part_id, item in (state.items.get(session) or {}).items():
                merged[part_id] = item
    ordered = sorted(merged.values(), key=lambda entry: _ts_key(entry["ts"]))
    thinking = [entry for entry in ordered if entry["kind"] == "thinking"]
    if len(thinking) > _MAX_THINKING:
        dropped = {id(entry) for entry in thinking[: len(thinking) - _MAX_THINKING]}
        ordered = [entry for entry in ordered if id(entry) not in dropped]
    return sessions, _fit_budget(intake_id, sessions, ordered[-_MAX_ITEMS:])


def _intake_exists(home: Path, project_id: str, intake_id: str) -> bool:
    try:
        conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM intakes WHERE project_id = ? AND intake_id = ? LIMIT 1",
            (project_id, intake_id),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


async def intake_activity(request: Any) -> Any:
    """GET /api/projects/{pid}/intakes/{intake_id}/activity (OOB read face)."""
    from tracks.server import api_query, projections

    pid = request.path_params.get("pid")
    pid = pid if isinstance(pid, str) and pid else ""
    home = Path(request.app.state.home)
    project = projections._project_from_home(home, pid)
    if project is None:
        return api_query._not_found("project", pid)
    intake_id = request.path_params.get("intake_id")
    intake_id = intake_id if isinstance(intake_id, str) and intake_id else ""
    if not _intake_exists(home, str(project["project_id"]), intake_id):
        return api_query._not_found("intake", intake_id)
    sessions, items = collect_activity(str(project["repo_path"]), intake_id)
    payload = {"intake_id": intake_id, "sessions": sessions, "items": items}
    return api_query._response(200, request, payload)
