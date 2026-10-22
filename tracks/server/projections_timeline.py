"""Timeline read model: merged tracks + service events, cursor and slim mode.

Stage display order (§1q), timeline assembly/filtering/cursor, and the slim
payload whitelist. Public names are re-exported by
``tracks.server.projections``.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from tracks.kernel.stage_registry import canonical_stage_order

from .projections_core import (
    _json,
    _load_payload,
    _project_for_run,
    _rows,
    _run_events,
    _service_db,
    _tracks_db,
)


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


def project_timeline(
    home: Path, run_id: str,
    *,
    command_id: str | None = None, task_id: str | None = None, ac_id: str | None = None,
    after_seq: str | None = None, slim: bool = False, include_internal: bool = True,
) -> dict:
    """Merged tracks.db + service_events timeline with cursor + stage order (§1e)."""
    conn = _service_db(home)
    try:
        project = _project_for_run(conn, run_id)
        if project is None:
            return {"events": [], "cursor": "", "stage_order": _stage_order()}
        repo_path = project["repo_path"]
        rows = _timeline_rows(conn, repo_path, run_id, slim=slim, include_internal=include_internal)
        rows = _filter_timeline(rows, command_id, task_id, ac_id)
        rows = _after_cursor(rows, after_seq)
        if slim and len(rows) > _SLIM_MAX_ROWS:
            rows = rows[-_SLIM_MAX_ROWS:]
        return {"events": rows, "cursor": _cursor_of(rows), "stage_order": _stage_order()}
    finally:
        if conn is not None:
            conn.close()


def _timeline_rows(
    conn: sqlite3.Connection | None, repo_path: str, run_id: str, *, slim: bool = False,
    include_internal: bool = True,
) -> list[dict]:
    rows = _track_timeline_rows(repo_path, run_id, slim=slim)
    rows.extend(
        _service_timeline_rows(conn, run_id, slim=slim, include_internal=include_internal)
    )
    rows.sort(key=_timeline_sort_key)
    return rows


def _track_timeline_rows(repo_path: str, run_id: str, *, slim: bool) -> list[dict]:
    if slim:
        return _slim_track_rows(repo_path, run_id)
    return _full_track_rows(repo_path, run_id)


def _full_track_rows(repo_path: str, run_id: str) -> list[dict]:
    rows = []
    for event in _run_events(repo_path, run_id):
        payload = dict(event.payload or {})
        rows.append(
            {
                "source": "tracks", "seq": event.seq, "ts": event.ts, "type": event.type,
                "run_id": event.run_id, "command_id": event.command_id,
                "task_id": event.task_id,
                "ac_refs": _event_ac_refs(payload), "summary": event.type,
                # §1l.1/§9.2 full timeline rendering: the client derives the
                # five-state labels / attempt grouping / rollback edges from
                # the event stream itself — the payload (stage, attempt, …)
                # is the derivation input, never a second source. The
                # response face redacts it (api_query._response), the SSE
                # face redacts it too (api_events._redacted).
                "payload": payload,
                "stage": payload.get("stage"),
                "attempt": payload.get("attempt"),
                "payload_ref": "",
            })
    return rows


def _service_timeline_rows(
    conn: sqlite3.Connection | None, run_id: str, *, slim: bool, include_internal: bool,
) -> list[dict]:
    if slim or not include_internal:
        service = _rows(conn,
            "SELECT seq, ts, type, run_id, command_id, payload FROM service_events "
                "WHERE run_id = ? AND type NOT IN (?, ?) ORDER BY seq",
            (run_id, *_SLIM_EXCLUDED_SERVICE_TYPES))
    else:
        service = _rows(conn,
            "SELECT seq, ts, type, run_id, command_id, payload FROM service_events "
                "WHERE run_id = ? ORDER BY seq",
            (run_id,))
    rows = []
    for seq, ts, type_, run, command, raw in service:
        parsed = _json(raw)
        parsed = parsed if isinstance(parsed, dict) else {}
        if slim:
            parsed = _slim_payload(type_, parsed)
        rows.append(
            {
                "source": "service", "seq": int(seq), "ts": ts, "type": type_, "run_id": run,
                "command_id": command, "task_id": None, "ac_refs": _event_ac_refs(parsed),
                "summary": type_, "payload": parsed,
                "stage": parsed.get("stage"),
                "attempt": parsed.get("attempt"),
                "payload_ref": "",
            })
    return rows


def _timeline_sort_key(row: dict) -> tuple[str, int, int]:
    return (row["ts"] or "", 0 if row["source"] == "tracks" else 1, row["seq"])


def _event_ac_refs(payload: dict) -> list[str]:
    refs = payload.get("ac_refs")
    return [ref for ref in refs if isinstance(ref, str)] if isinstance(refs, list) else []


# Timeline slim mode: the client (timeline.js foldStageFacts + the stage
# workflow diagram) needs only the stage-derivation keys plus the small
# workflow scalars (round/verdict/decision/action/check/final/commit_sha)
# and the dispatch identity (command.kind + a small params subset); the
# 30KB–2.2MB evidence blobs never contribute them (737 $ref rows in the
# reference run carry no stage, 8 carry attempt).
_SLIM_KEYS = frozenset({"stage", "attempt", "to", "to_stage", "reason",
    # Rollback edges / visit boundaries: the client must see where a
    # ``stage.rolled_back`` came FROM and whether it was an operator repair
    # (a self-rollback or a repair must not void the current visit). Dropping
    # these made the slim face lose the edge whenever ``current`` did not
    # happen to be the source stage (recovery/truncation paths).
    "from_stage", "from", "operator_repair",
    # verdict.failed evidence rides the same bounded verdict blob; the UI
    # folds it into 技术详情 (never the default headline).
    "evidence",
    "round", "verdict", "decision", "action", "check", "final", "commit_sha",
    "area", "next"})

# Slim-mode service-plane noise exclusion (read-face load shedding only; the
# writer is untouched): ``command.accepted`` + ``lease.acquired`` are the
# supervisor's internal command-lifecycle pairs emitted on every poll tick
# (~one pair per 5s while idling — 129k of 129k rows in the reference run),
# not user-timeline semantics. ``lease.released`` stays (rare, a meaningful
# lease boundary), as do ``command.rejected`` and every other service type.
_SLIM_EXCLUDED_SERVICE_TYPES = ("command.accepted", "lease.acquired")

# Slim-mode backstop cap: even after the noise exclusion, the response keeps
# only the newest N merged rows. The UI renders the stage path / current card
# from recent workflow facts, so an unbounded tail is pure serialization cost;
# the non-slim path (full-history audit face) is never capped.
_SLIM_MAX_ROWS = 20000

# command.issued params allowlist for slim mode: the dispatch identity the
# stage workflow diagram renders (role/substate/attempt/review_round/…).
# Only keys actually present are kept; everything else stays discarded.
_SLIM_COMMAND_PARAM_KEYS = frozenset({"role", "substate", "attempt", "review_round",
    "decision", "doc", "objective", "stage", "base", "branch_name"})

# The ``$ref`` row types whose blobs ARE stage-workflow facts. Run bd0c68eb
# carries 22 such refs — 8 command.issued (dispatch identity: kind +
# params.substate, 134KB), 10 outcome.received (conclusion status, 256KB),
# 2 red.validated, 2 verdict.failed (~438KB total); dropping them left the
# stage graph empty and the summary at 0 dispatches. Every OTHER ref type
# (the reference run's 737 rows) carries no stage facts, so its blob stays
# unread — slim mode remains bounded to this small workflow set.
_SLIM_REF_TYPES = frozenset({"command.issued", "outcome.received", "red.validated",
    # Visit/edge facts can carry long reasons (recovery narratives) and are
    # stored as blobs once over the inline threshold; both stay rare, so
    # reading their blobs is bounded.
    "stage.rolled_back", "stage.recovered"})


def _keep_keys(target: dict, source: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in source:
            target[key] = source[key]


def _slim_command(command: object) -> dict:
    if not isinstance(command, dict):
        return {}
    kept: dict = {}
    if "kind" in command:
        kept["kind"] = command["kind"]
    params = command.get("params")
    if isinstance(params, dict):
        small = {key: params[key] for key in _SLIM_COMMAND_PARAM_KEYS if key in params}
        if small:
            kept["params"] = small
    return kept


def _slim_payload(type_: str, parsed: dict) -> dict:
    """Whitelisted slim payload for one event (slim branch only).

    Top-level scalars keep _SLIM_KEYS; a ``command.issued`` row additionally
    keeps ``command.kind`` + the _SLIM_COMMAND_PARAM_KEYS subset of
    ``command.params``. All other shapes (incl. service-plane command rows
    with the same structure) fall through to the scalar whitelist.
    """
    slim = {key: parsed[key] for key in _SLIM_KEYS if key in parsed}
    if type_ == "outcome.received":
        # Outcome blobs name their conclusion via status/failure_class (this
        # run's 10 refs carry no ``verdict`` key); keep the small scalars so
        # the slim row is not empty and the role survives.
        _keep_keys(slim, parsed, ("role", "status", "failure_class"))
    elif type_ == "red.validated":
        _keep_keys(slim, parsed, ("status",))
    if type_ == "command.issued":
        command = _slim_command(parsed.get("command"))
        if command:
            slim["command"] = command
    return slim


def _slim_track_rows(repo_path: str, run_id: str) -> list[dict]:
    """Track-side timeline rows; blobs are read only for workflow refs.

    Reads raw payloads straight from the events table. A ``{"$ref": ...}``
    row whose type is a stage-workflow fact (``_SLIM_REF_TYPES`` or
    ``verdict.*``) reads that one blob and emits the same slim row an inline
    payload would; a missing/unreadable/non-dict blob degrades to the old
    ``payload={}`` + ``payload_ref``. Every other ref type keeps ``payload={}``
    and its blob unread, bounding the read face to the small workflow set.
    """
    conn = _tracks_db(repo_path)
    try:
        rows = _rows(conn,
            "SELECT run_id, seq, ts, type, command_id, task_id, payload "
                "FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,))
    finally:
        if conn is not None:
            conn.close()
    return [_slim_track_row(repo_path, row) for row in rows]


def _slim_track_row(repo_path: str, row: tuple) -> dict:
    run, seq, ts, type_, command_id, task_id, raw = row
    parsed = _json(raw)
    parsed = parsed if isinstance(parsed, dict) else {}
    if set(parsed) == {"$ref"}:
        return _slim_ref_row(repo_path, row, parsed)
    return _slim_inline_row(row, parsed)


def _slim_ref_row(repo_path: str, row: tuple, parsed: dict) -> dict:
    run, seq, ts, type_, command_id, task_id, raw = row
    workflow = type_ in _SLIM_REF_TYPES or type_.startswith("verdict.")
    blob = _load_payload(raw, repo_path) if workflow else {}
    slim = _slim_payload(type_, blob) if blob else {}
    return _slim_row(
        (run, seq, ts, type_, command_id, task_id), blob, slim, str(parsed["$ref"])
    )


def _slim_inline_row(row: tuple, parsed: dict) -> dict:
    run, seq, ts, type_, command_id, task_id, _raw = row
    slim = _slim_payload(type_, parsed)
    return _slim_row((run, seq, ts, type_, command_id, task_id), parsed, slim, "")


def _slim_row(
    identity: tuple, payload: dict, slim: dict, payload_ref: str,
) -> dict:
    run, seq, ts, type_, command_id, task_id = identity
    return {
        "source": "tracks", "seq": seq, "ts": ts, "type": type_,
        "run_id": run, "command_id": command_id, "task_id": task_id,
        "ac_refs": _event_ac_refs(payload), "summary": type_, "payload": slim,
        "stage": slim.get("stage"), "attempt": slim.get("attempt"),
        "payload_ref": payload_ref,
    }


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
    raw = json.dumps({"ts": last["ts"], "source": last["source"], "seq": last["seq"]},
        sort_keys=True)
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
