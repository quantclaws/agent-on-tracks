"""Read-only per-round agent I/O faces (OOB read faces).

``GET /api/runs/{run_id}/stages/{stage}/rounds`` lists one run stage's
agent dispatches in order (one item per ``dispatch_agent``
``command.issued``), and ``GET /api/runs/{run_id}/rounds/{command_id}``
opens a single round: the dispatch input, the agent output text and the
surviving thinking tail.

Mechanism: the round window is the stage's last ``stage.entered`` up to
its next ``stage.exited`` (open-ended when the stage never exited); each
dispatch pairs with the nearest later ``outcome.received`` carrying the
same command id before the next dispatch (no outcome yet → ``running``).
Input/output bodies live in content-addressed blobs under
``<repo>/.tracks/runtime/blobs/<sha256>`` referenced by the outcome's
``agent_io`` (``input_ref``/``output_ref``) — blobs are permanent, so a
round whose blob is gone only degrades that face (``input`` null /
``output`` ``{"unavailable": true}``). The thinking tail is parsed from
the newest opencode logs (``<repo>/.opencode/logs/*.jsonl``) through the
intake face's shared incremental cache (``intake_activity._ingest`` /
``_log_files``, ``_FileState.runs`` version→sessions association) — logs
rotate, so a round whose session already scrolled out of the cache
degrades to ``thinking_available: false`` instead of failing.

Polling-grade read path: repeat polls only read appended log bytes (the
shared incremental cache); payloads leave through the shared
``SecretRedactor`` via ``api_query._response``, like every other read
face.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from pathlib import Path
from typing import Any

from tracks.server import api_query, intake_activity
from tracks.server.run_activity import _dispatch_params

_PROMPT_HEAD = 8000  # input prompt display quota (chars, head)
_TEXT_TAIL = 12000  # output text display quota (chars, tail kept)
_STDERR_HEAD = 2000  # stderr display quota (chars, head)
_SUMMARY_HEAD = 300  # round-list summary quota (chars)
_THINKING_TAIL = 30  # thinking-item tail quota (matches intake face)
_THINKING_HEAD = 1200  # per-thinking-item quota (matches intake face)
_LIVE_TAIL = 40  # live-item tail quota (text/tool/step/reasoning, time-ordered)
_LIVE_TEXT = 1200  # per-live-item text quota (matches intake face)
_SESSION_SLOP_S = 120.0  # session min-ts tolerance before dispatch start


def _stage_window(events: list[Any], stage: str) -> tuple[int, int | None] | None:
    """(entered seq, exited seq|None) of a stage's last entry window.

    The window opens at the stage's last ``stage.entered`` and closes at
    the first ``stage.exited`` of the same stage after it (None = the
    stage never exited, i.e. open-ended to the last event).
    """
    start: int | None = None
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "stage.entered" and payload.get("stage") == stage:
            start = event.seq
    if start is None:
        return None
    for event in events:
        if event.seq <= start or event.type != "stage.exited":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("stage") == stage:
            return (start, event.seq)
    return (start, None)


def _dispatches(events: list[Any]) -> list[dict[str, Any]]:
    """Ordered ``dispatch_agent`` dispatches of a run's event stream."""
    found: list[dict[str, Any]] = []
    for event in events:
        if event.type != "command.issued":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        params = _dispatch_params(payload)
        if params is None:
            continue
        command = payload.get("command")
        command_id = event.command_id
        if isinstance(command, dict) and isinstance(command.get("command_id"), str):
            command_id = command["command_id"]
        found.append(
            {"seq": event.seq, "ts": event.ts, "command_id": command_id,
             "params": params}
        )
    return found


def _window_dispatches(
    events: list[Any], window: tuple[int, int | None]
) -> list[dict[str, Any]]:
    """Dispatches inside a stage window (entered seq <= seq < exited seq)."""
    start_seq, end_seq = window
    return [
        disp for disp in _dispatches(events)
        if disp["seq"] >= start_seq and (end_seq is None or disp["seq"] < end_seq)
    ]


def _outcome_index(events: list[Any]) -> tuple[list[int], list[Any]]:
    """(seqs, outcomes) of ``outcome.received`` in event order."""
    seqs: list[int] = []
    outs: list[Any] = []
    for event in events:
        if event.type == "outcome.received":
            seqs.append(event.seq)
            outs.append(event)
    return seqs, outs


def _pair_outcome(
    seqs: list[int], outs: list[Any], dseq: int,
    bound: int | None, command_id: Any,
) -> Any | None:
    """Nearest outcome after a dispatch before the bound with same id."""
    pos = bisect_right(seqs, dseq)
    while pos < len(seqs):
        if bound is not None and seqs[pos] >= bound:
            return None
        if outs[pos].command_id == command_id:
            return outs[pos]
        pos += 1
    return None


def _find_dispatch(
    dispatches: list[dict[str, Any]], command_id: str
) -> tuple[dict[str, Any] | None, int | None]:
    """(dispatch, next-dispatch seq|None) for one command id."""
    for pos, disp in enumerate(dispatches):
        if disp["command_id"] == command_id:
            bound = dispatches[pos + 1]["seq"] if pos + 1 < len(dispatches) else None
            return disp, bound
    return None, None


def _run_version(repo_path: str, run_id: str, events: list[Any]) -> str:
    """Run version from the run plane, falling back to event versions."""
    rows = api_query._read_rows(
        api_query._tracks_db_path(repo_path),
        "SELECT version FROM runs WHERE run_id = ? LIMIT 1",
        (run_id,),
    )
    if rows and isinstance(rows[0][0], str) and rows[0][0]:
        return rows[0][0]
    for event in events:
        if event.version:
            return event.version
    return ""


def _summary_str(payload: Any) -> str:
    """Short outcome summary for the round list (truncated to 300)."""
    if not isinstance(payload, dict):
        return ""
    direct = payload.get("output_summary")
    if isinstance(direct, str):
        return direct[:_SUMMARY_HEAD]
    agent = payload.get("agent_io")
    if isinstance(agent, dict):
        nested = agent.get("output_summary")
        if isinstance(nested, str):
            return nested[:_SUMMARY_HEAD]
        if isinstance(nested, dict):
            return json.dumps(nested, ensure_ascii=False, sort_keys=True)[
                :_SUMMARY_HEAD
            ]
    return ""


def _read_blob(repo_path: str, ref: Any) -> dict[str, Any] | None:
    """One JSON blob dict by ref (None = missing/unreadable/invalid)."""
    if not isinstance(ref, str) or not ref or "/" in ref or "\\" in ref:
        return None
    try:
        raw = (
            Path(repo_path) / ".tracks" / "runtime" / "blobs" / ref
        ).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _sessions_view(repo_path: str) -> list[tuple[Path, Any]]:
    """Ingested log-file states (shared incremental cache, intake face)."""
    return [
        (path, intake_activity._ingest(path))
        for path in intake_activity._log_files(Path(repo_path))
    ]


def _merged_items(
    states: list[tuple[Path, Any]], session: str
) -> dict[str, dict[str, Any]]:
    """One session's part items merged across the scanned log files."""
    merged: dict[str, dict[str, Any]] = {}
    for _, state in states:
        for part_id, item in (state.items.get(session) or {}).items():
            merged[part_id] = item
    return merged


def _session_keys(items: dict[str, dict[str, Any]]) -> list[float]:
    """Timestamp sort keys of one session's items."""
    return [
        intake_activity._ts_key(item.get("ts", "")) for item in items.values()
    ]


def _session_rank(
    keys: list[float], start: float, floor: float, ceiling: float
) -> tuple[int, float] | None:
    """(0=at/after start, 1=pre-start, gap) of a session inside the window."""
    if not any(floor <= key <= ceiling for key in keys):
        return None
    post = [key for key in keys if start <= key <= ceiling]
    if post:
        return 0, min(post) - start
    pre = [key for key in keys if key >= floor]
    if pre:
        return 1, start - max(pre)
    return None


def _candidate_sessions(
    states: list[tuple[Path, Any]], sessions: list[str],
    start: float, floor: float, ceiling: float,
) -> list[tuple[int, float, str, dict[str, dict[str, Any]]]]:
    """Sessions overlapping the dispatch window with their rank/gap."""
    found: list[tuple[int, float, str, dict[str, dict[str, Any]]]] = []
    for session in sessions:
        items = _merged_items(states, session)
        if not items:
            continue
        ranked = _session_rank(_session_keys(items), start, floor, ceiling)
        if ranked is not None:
            found.append((ranked[0], ranked[1], session, items))
    return found


def _best_candidate(
    candidates: list[tuple[int, float, str, dict[str, dict[str, Any]]]],
    rank: int,
) -> tuple[int, float, str, dict[str, dict[str, Any]]] | None:
    """Lowest-gap candidate of one rank (first wins ties)."""
    best = None
    for candidate in candidates:
        if candidate[0] != rank:
            continue
        if best is None or candidate[1] < best[1]:
            best = candidate
    return best


def _match_session(
    states: list[tuple[Path, Any]], version: str,
    start_ts: str, end_ts: str | None,
) -> tuple[str | None, dict[str, dict[str, Any]]]:
    """Session whose items overlap a dispatch window, anchored at start.

    Candidates are the ``runs[version]`` sessions with at least one item
    in ``[start_ts-120s, end_ts+120s]`` (``+∞`` when the round is still
    running). Preference goes to sessions active at or after the dispatch
    start — the winner minimizes the gap of its first such item to the
    start — so a round reusing a long-lived session still matches its own
    live tail instead of being excluded by the session's old first item.
    When no session reaches the start (clock skew), it falls back to the
    closest pre-start floor within the 120s tolerance.
    """
    sessions = sorted(
        {item for _, state in states for item in state.runs.get(version, ())}
    )
    start = intake_activity._ts_key(start_ts)
    ceiling = (
        intake_activity._ts_key(end_ts) + _SESSION_SLOP_S
        if end_ts
        else float("inf")
    )
    candidates = _candidate_sessions(
        states, sessions, start, start - _SESSION_SLOP_S, ceiling
    )
    best = _best_candidate(candidates, 0)
    if best is None:
        best = _best_candidate(candidates, 1)
    if best is None:
        return None, {}
    return best[2], best[3]


def _live_tail(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Time-ordered tail (40) live session items for a running round.

    All kinds (text/tool/step/thinking) are kept; each row is
    ``{ts, kind, tool, label, text}`` with text clipped to 1200 chars.
    The intake cache already stores this shape (text pre-clipped), so
    this only re-clips defensively and sorts by timestamp.
    """
    ordered = sorted(
        items.values(), key=lambda item: intake_activity._ts_key(item.get("ts", ""))
    )
    tail = ordered[-_LIVE_TAIL:]
    rows: list[dict[str, Any]] = []
    for item in tail:
        kind = item.get("kind")
        text = item.get("text")
        text = text[:_LIVE_TEXT] if isinstance(text, str) else ""
        tool = item.get("tool")
        tool = tool if isinstance(tool, str) and tool else None
        label = item.get("label")
        label = label if isinstance(label, str) else ""
        rows.append(
            {
                "ts": item.get("ts", ""),
                "kind": kind if isinstance(kind, str) else "",
                "tool": tool,
                "label": label,
                "text": text,
            }
        )
    return rows


def _assemble_round(
    states: list[tuple[Path, Any]], version: str,
    disp: dict[str, Any], outcome: Any | None, n: int,
) -> dict[str, Any]:
    """One round-list item from a dispatch + its paired outcome."""
    end_ts = outcome.ts if outcome is not None else None
    session, items = _match_session(states, version, disp["ts"], end_ts)
    payload = outcome.payload if outcome is not None else {}
    if not isinstance(payload, dict):
        payload = {}
    agent = payload.get("agent_io")
    agent = agent if isinstance(agent, dict) else {}
    params = disp["params"]
    input_ref = agent.get("input_ref")
    output_ref = agent.get("output_ref")
    assignment = params.get("assignment")
    return {
        "n": n,
        "command_id": disp["command_id"],
        "role": params.get("role"),
        "substate": params.get("substate"),
        "review_round": params.get("review_round"),
        "attempt": params.get("attempt"),
        "doc": params.get("doc"),
        "objective": params.get("objective"),
        "assignment": assignment if isinstance(assignment, dict) else None,
        "start_ts": disp["ts"],
        "end_ts": end_ts,
        "status": "ok" if outcome is not None else "running",
        "output_summary": _summary_str(payload),
        "input_ref": input_ref if isinstance(input_ref, str) else None,
        "output_ref": output_ref if isinstance(output_ref, str) else None,
        "session_id": session,
        "has_thinking": any(
            item.get("kind") == "thinking" for item in items.values()
        ),
    }


def _dispatch_bound(
    dispatches: list[dict[str, Any]], pos: int, window: tuple[int, int | None]
) -> int | None:
    """Seq of the next dispatch, else the stage window's exit."""
    if pos + 1 < len(dispatches):
        return dispatches[pos + 1]["seq"]
    return window[1]


def _build_rounds(
    states: list[tuple[Path, Any]], version: str,
    dispatches: list[dict[str, Any]], seqs: list[int], outs: list[Any],
    window: tuple[int, int | None],
) -> list[dict[str, Any]]:
    """One assembled round per dispatch inside the stage window."""
    rounds: list[dict[str, Any]] = []
    for pos, disp in enumerate(dispatches):
        bound = _dispatch_bound(dispatches, pos, window)
        outcome = _pair_outcome(seqs, outs, disp["seq"], bound, disp["command_id"])
        rounds.append(_assemble_round(states, version, disp, outcome, pos + 1))
    return rounds


def rounds_for_stage(
    repo_path: str | Path, run_id: str, stage: str
) -> list[dict[str, Any]]:
    """Ordered round-list items of one run stage (no HTTP needed)."""
    repo = str(repo_path)
    events = api_query._run_events(repo, run_id)
    window = _stage_window(events, stage)
    if window is None:
        return []
    dispatches = _window_dispatches(events, window)
    seqs, outs = _outcome_index(events)
    version = _run_version(repo, run_id, events)
    states = _sessions_view(repo)
    return _build_rounds(states, version, dispatches, seqs, outs, window)


def _stdout_row(line: str) -> tuple[str | None, str | None]:
    """(text, tool) of one JSONL stream row ((None, None) = skip)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    part = obj.get("part")
    if not isinstance(part, dict):
        return None, None
    if obj.get("type") == "text":
        text = part.get("text")
        return (text if isinstance(text, str) else None), None
    if obj.get("type") == "tool_use":
        tool = part.get("tool")
        return None, (tool if isinstance(tool, str) and tool else "unknown")
    return None, None


def _parse_stdout(stdout: Any) -> tuple[str, list[dict[str, Any]]]:
    """(joined text, per-tool counts) of an opencode JSONL event stream."""
    texts: list[str] = []
    counts: dict[str, int] = {}
    if not isinstance(stdout, str):
        return "", []
    for line in stdout.split("\n"):
        text, tool = _stdout_row(line)
        if text is not None:
            texts.append(text)
        if tool is not None:
            counts[tool] = counts.get(tool, 0) + 1
    joined = "".join(texts)
    if len(joined) > _TEXT_TAIL:
        joined = joined[-_TEXT_TAIL:]
    return joined, [{"tool": name, "n": counts[name]} for name in counts]


def _detail_input(repo_path: str, agent: dict[str, Any]) -> dict[str, Any] | None:
    """Dispatch input doc from the input blob (None = blob missing)."""
    blob = _read_blob(repo_path, agent.get("input_ref"))
    if blob is None:
        return None
    prompt = blob.get("prompt")
    assignment = blob.get("assignment")
    return {
        "prompt": prompt[:_PROMPT_HEAD] if isinstance(prompt, str) else "",
        "assignment": assignment if isinstance(assignment, dict) else {},
        "role": blob.get("role"),
        "substate": blob.get("substate"),
        "attempt": blob.get("attempt"),
        "review_round": blob.get("review_round"),
        "doc": blob.get("doc"),
        "objective": blob.get("objective"),
    }


def _detail_output(
    repo_path: str, agent: dict[str, Any], produced: bool
) -> dict[str, Any] | None:
    """Agent output doc from the output blob.

    A produced-but-unreadable blob degrades to ``{"unavailable": true}``;
    an unproduced (still running) round degrades to None.
    """
    blob = _read_blob(repo_path, agent.get("output_ref"))
    if blob is None:
        return {"unavailable": True} if produced else None
    text, calls = _parse_stdout(blob.get("stdout"))
    stderr = blob.get("stderr")
    return {
        "text": text,
        "tool_calls": calls,
        "stderr": stderr[:_STDERR_HEAD] if isinstance(stderr, str) else "",
    }


def _thinking_entry(item: dict[str, Any]) -> dict[str, Any]:
    """One thinking row (text clipped to the per-item quota)."""
    text = item.get("text")
    return {
        "ts": item.get("ts", ""),
        "text": text[:_THINKING_HEAD] if isinstance(text, str) else "",
    }


def _detail_thinking(
    repo_path: str, version: str, start_ts: str, end_ts: str | None
) -> tuple[list[dict[str, Any]] | None, bool]:
    """(thinking tail rows, available) of a round's matched session.

    A session already rotated out of the log cache degrades to
    ``(None, False)`` — the input/output blobs stay readable regardless.
    """
    states = _sessions_view(repo_path)
    session, items = _match_session(states, version, start_ts, end_ts)
    if session is None:
        return None, False
    thinking = [item for item in items.values() if item.get("kind") == "thinking"]
    thinking.sort(key=lambda item: intake_activity._ts_key(item.get("ts", "")))
    return [_thinking_entry(item) for item in thinking[-_THINKING_TAIL:]], True


def _locate_round(
    events: list[Any], command_id: str
) -> tuple[dict[str, Any] | None, Any | None]:
    """(dispatch, paired outcome|None) of one command (absent → Nones)."""
    dispatches = _dispatches(events)
    hit, bound = _find_dispatch(dispatches, command_id)
    if hit is None:
        return None, None
    seqs, outs = _outcome_index(events)
    return hit, _pair_outcome(seqs, outs, hit["seq"], bound, command_id)


def _outcome_agent(outcome: Any | None) -> dict[str, Any]:
    """The outcome's ``agent_io`` dict (empty when absent/invalid)."""
    payload = outcome.payload if outcome is not None else {}
    if not isinstance(payload, dict):
        return {}
    agent = payload.get("agent_io")
    return agent if isinstance(agent, dict) else {}


def _round_live(
    repo_path: str, version: str, start_ts: str, end_ts: str | None
) -> list[dict[str, Any]]:
    """Live tail rows of the round's matched session ([] when rotated out)."""
    states = _sessions_view(repo_path)
    session, items = _match_session(states, version, start_ts, end_ts)
    return _live_tail(items) if session is not None else []


def _round_detail_body(
    repo: str, command_id: str, hit: dict[str, Any], outcome: Any | None,
    agent: dict[str, Any], end_ts: str | None, live: list[dict[str, Any]],
    thinking: list[dict[str, Any]] | None, available: bool,
) -> dict[str, Any]:
    """The round-detail payload (identity, params, live, input/output)."""
    params = hit["params"]
    assignment = params.get("assignment")
    assignment = assignment if isinstance(assignment, dict) else None
    return {
        "command_id": command_id,
        "role": params.get("role"),
        "substate": params.get("substate"),
        "review_round": params.get("review_round"),
        "attempt": params.get("attempt"),
        "start_ts": hit["ts"],
        "end_ts": end_ts,
        "status": "ok" if outcome is not None else "running",
        "running": outcome is None,
        "params": {
            "role": params.get("role"),
            "substate": params.get("substate"),
            "review_round": params.get("review_round"),
            "attempt": params.get("attempt"),
            "doc": params.get("doc"),
            "objective": params.get("objective"),
            "assignment": assignment,
        },
        "live": live,
        "input": _detail_input(repo, agent),
        "output": _detail_output(repo, agent, outcome is not None),
        "thinking": thinking,
        "thinking_available": available,
    }


def round_detail(
    repo_path: str | Path, run_id: str, command_id: str
) -> dict[str, Any] | None:
    """One round's input/output/thinking (None = command not dispatched)."""
    repo = str(repo_path)
    events = api_query._run_events(repo, run_id)
    hit, outcome = _locate_round(events, command_id)
    if hit is None:
        return None
    end_ts = outcome.ts if outcome is not None else None
    version = _run_version(repo, run_id, events)
    thinking, available = _detail_thinking(repo, version, hit["ts"], end_ts)
    live = _round_live(repo, version, hit["ts"], end_ts)
    return _round_detail_body(
        repo, command_id, hit, outcome, _outcome_agent(outcome),
        end_ts, live, thinking, available,
    )


async def stage_rounds(request: Any) -> Any:
    """GET /api/runs/{run_id}/stages/{stage}/rounds (OOB read face).

    Lists the stage window's dispatches in order (see module docstring
    for the window/pairing rules); an unknown run 404s, a stage without
    a window answers an empty round list.
    """
    run_id = str(request.path_params.get("run_id", ""))
    stage = str(request.path_params.get("stage", ""))
    home = Path(request.app.state.home)
    project = api_query._project_for_run(home, run_id)
    if project is None:
        return api_query._not_found("run", run_id)
    result = {
        "run_id": run_id,
        "stage": stage,
        "rounds": rounds_for_stage(str(project["repo_path"]), run_id, stage),
    }
    return api_query._response(200, request, result)


async def round_detail_view(request: Any) -> Any:
    """GET /api/runs/{run_id}/rounds/{command_id} (OOB read face).

    Opens one round's input/output/thinking (see module docstring for
    the blob-permanent vs log-rotation degradation); an unknown run or
    an undispatched command id 404s.
    """
    run_id = str(request.path_params.get("run_id", ""))
    command_id = str(request.path_params.get("command_id", ""))
    home = Path(request.app.state.home)
    project = api_query._project_for_run(home, run_id)
    if project is None:
        return api_query._not_found("run", run_id)
    detail = round_detail(str(project["repo_path"]), run_id, command_id)
    if detail is None:
        return api_query._not_found("command", command_id)
    return api_query._response(200, request, detail)
