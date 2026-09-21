"""T-011 RED: read-only query routes -> projections (IF-QUERY-001).

Devon-owned unit RED for the T-011 delivery slice
(``tracks/server/api_query.py``): the read-only GET surfaces of interfaces
§2b #9/#10/#11/#13/#14/#27 branch onto the §1e projections, every payload
passes through ``SecretRedactor`` (IF-SECRECY-001) and no query takes the
writer path (NFR-0150).

The unit seam is the handler contract the app factory (T-014) binds to: each
handler receives a request exposing

    request.app.state.home      -> Path of the service home (service.db)
    request.app.state.redactor  -> SecretRedactor applied to the payload
    request.path_params         -> path parameters (pid / run_id / command_id)
    request.query_params        -> query parameters (timeline filters / after)

and returns ``api_query.QueryResponse(status_code, payload)`` — the
starlette-free response envelope (the runtime venv does not carry starlette
yet; the composition root maps the envelope onto JSONResponse).

Fixtures seed the documented storage contract for real (interfaces §1c
service.db through ``ServiceDB``, each run's tracks.db through ``Store``,
the version's acceptance.md registry) — nothing here mocks the system under
test. The current handler bodies raise their IF-QUERY-001 stub token; every
failing node guards that stub state into a real ``AssertionError`` (no
stub_token, no assembly errors).

AC: FR-0295/0301/0302/0305/0307/0315 — TRACKS-TRACE IF-QUERY-001.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import paths
from tracks.server import api_query
from tracks.server.redaction import SecretRedactor
from tracks.store import Store
from tracks.supervisor import db as sdb

_VERSION = "v0.9"
_CANARY_NAME = "TRAC_CANARY"
_CANARY_VALUE = "canary-t011-9f3c1b7e"
_AC_IDS = [
    "AC-FR0295-01",
    "AC-FR0295-04",
    "AC-FR0301-01",
    "AC-FR0301-02",
    "AC-FR0302-01",
    "AC-FR0305-01",
    "AC-FR0305-02",
    "AC-FR0307-01",
    "AC-FR0307-02",
    "AC-FR0315-01",
]
_RUNNING_EVENTS = [
    {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
    {
        "type": "taskgraph.committed",
        "payload": {"task_count": 2, "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}]},
    },
    {
        "type": "task.started",
        "payload": {"task_id": "T-1"},
        "task_id": "T-1",
        "command_id": "cmd-a",
    },
    {"type": "task.completed", "payload": {"task_id": "T-1"}},
]


# -- fixtures: real stores seeded through the documented contract ------------


def _write_acceptance(repo: Path) -> None:
    blocks = []
    for ac_id in _AC_IDS:
        parts = ac_id.split("-")
        blocks.append(f"## {parts[1]}\n\n### {ac_id}\n\n- criterion {ac_id}\n")
    target = repo / ".tracks" / "projects" / _VERSION / "acceptance.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(blocks), encoding="utf-8")


def _write_events(repo: Path, run_id: str, events: list[dict]) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        for event in events:
            store.append(
                run_id,
                _VERSION,
                event["type"],
                event.get("payload", {}),
                command_id=event.get("command_id"),
                task_id=event.get("task_id"),
            )
    finally:
        store.close()


def _command(run_id: str, command_id: str, *, status: str, result=None, failure=None) -> dict:
    return {
        "command_id": command_id,
        "kind": "create_run",
        "params_json": "{}",
        "params_digest": f"digest-{command_id}",
        "idempotency_key": f"idem-{command_id}",
        "actor": "alice",
        "actor_class": "human",
        "surface": "http",
        "project_id": "proj-1",
        "run_id": run_id,
        "status": status,
        "result": result,
        "failure": failure,
    }


def _persist_command(db: sdb.ServiceDB, command: dict) -> None:
    db.register_command(command)
    if command.get("status") not in ("completed", "failed"):
        return
    assert db.claim_command(command["command_id"], "worker-1", 1)
    failure = command.get("failure") if command["status"] == "failed" else None
    result = command.get("result") if command["status"] == "completed" else None
    assert db.complete_command(command["command_id"], 1, result, failure)


def _bind_run(conn: sqlite3.Connection, repo: Path, run_id: str) -> None:
    conn.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?)",
        ("proj-1", str(repo), _VERSION, "alice", "2026-09-20T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO leases VALUES (?,?,?,?,?)",
        (run_id, "worker-1", 1, "2026-09-20T00:00:00+00:00", "2026-09-20T00:01:00+00:00"),
    )
    conn.execute("INSERT INTO schedule VALUES (1, ?, '[]')", (run_id,))


def _insert_wait(conn: sqlite3.Connection, run_id: str, wait: dict) -> None:
    conn.execute(
        "INSERT INTO waits VALUES (?,?,?,?,?,?,?)",
        (
            run_id,
            wait["wait_class"],
            wait["reason"],
            wait.get("retry_at"),
            int(bool(wait.get("known_reset", False))),
            wait.get("backoff_json"),
            "2026-09-20T00:00:00+00:00",
        ),
    )


def _insert_service_event(conn: sqlite3.Connection, run_id: str, event: tuple) -> None:
    event_type, command_id, payload = event
    conn.execute(
        "INSERT INTO service_events (ts, type, project_id, run_id, command_id, payload)"
        " VALUES (?,?,?,?,?,?)",
        (
            "2026-09-20T00:00:01+00:00",
            event_type,
            "proj-1",
            run_id,
            command_id,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ),
    )


def _seed_repo(
    tmp_path: Path,
    run_id: str,
    events: list[dict] | None,
    extra_runs: list[tuple[str, list[dict]]] | None,
) -> Path:
    repo = tmp_path / "repo"
    _write_acceptance(repo)
    _write_events(repo, run_id, events or [])
    for other_run, other_events in extra_runs or ():
        _write_events(repo, other_run, other_events)
    return repo


def _seed(
    tmp_path: Path,
    *,
    run_id: str,
    events: list[dict] | None = None,
    extra_runs: list[tuple[str, list[dict]]] | None = None,
    service_events: list[tuple[str, str, dict]] | None = None,
    waits: list[tuple[str, dict]] | None = None,
    commands: list[dict] | None = None,
) -> Path:
    """Seed service.db + one tracks.db exactly as the contracts document."""
    repo = _seed_repo(tmp_path, run_id, events, extra_runs)

    home = tmp_path / "service"
    db = sdb.ServiceDB(home)
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        _bind_run(conn, repo, run_id)
        for wait in waits or ():
            _insert_wait(conn, wait[0], wait[1])
        for event in service_events or ():
            _insert_service_event(conn, run_id, event)
        conn.commit()
    finally:
        conn.close()
    for command in commands or ():
        _persist_command(db, command)
    return home


# -- the handler seam: request double + response extraction ------------------


class _Request:
    """Duck-typed request exposing exactly the handler seam (see docstring)."""

    def __init__(
        self,
        home: Path,
        redactor: SecretRedactor,
        *,
        path_params: dict | None = None,
        query: dict | None = None,
    ) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(home=home, redactor=redactor))
        self.path_params = dict(path_params or {})
        self.query_params = dict(query or {})


def _request(
    home: Path,
    *,
    path_params: dict | None = None,
    query: dict | None = None,
    redactor: SecretRedactor | None = None,
) -> _Request:
    return _Request(
        home,
        redactor if redactor is not None else SecretRedactor({}),
        path_params=path_params,
        query=query,
    )


def _call(handler, request: _Request) -> tuple[int, Any]:
    """Call one query handler; a stub token becomes a real assertion failure."""
    try:
        result = asyncio.run(handler(request))
    except NotImplementedError as exc:
        raise AssertionError(f"{handler.__name__} is still a stub: {exc}") from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), (
        f"{handler.__name__} must return api_query.QueryResponse(status_code, payload)"
    )
    return status, getattr(result, "payload", None)


def _error_reason(payload: Any) -> str:
    assert isinstance(payload, dict) and "error" in payload, (
        f"expected the unified error envelope, got {payload!r}"
    )
    return payload["error"]["reason"]


def _counts(home: Path, repo: Path) -> dict[str, int]:
    tracks = sqlite3.connect(f"file:{repo / '.tracks' / 'runtime' / 'tracks.db'}?mode=ro", uri=True)
    service = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        return {
            "tracks_events": tracks.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "service_events": service.execute("SELECT COUNT(*) FROM service_events").fetchone()[0],
            "commands": service.execute("SELECT COUNT(*) FROM commands").fetchone()[0],
            "waits": service.execute("SELECT COUNT(*) FROM waits").fetchone()[0],
        }
    finally:
        tracks.close()
        service.close()


def _wait_reason(home: Path, run_id: str) -> str:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT reason FROM waits WHERE run_id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, "fixture must have seeded the wait row"
    return row[0]


# -- overview (GET /api/projects/{pid}/overview) -----------------------------


# AC-FR0301-01@v0.9 TRACKS-TRACE IF-QUERY-001 overview mirrors seeded run state
def test_overview_payload_matches_seeded_state(tmp_path: Path):
    run_id = "run-overview"
    home = _seed(tmp_path, run_id=run_id, events=_RUNNING_EVENTS)

    status, payload = _call(
        api_query.project_overview, _request(home, path_params={"pid": "proj-1"})
    )

    assert status == 200
    assert set(payload) >= {"projects", "runs", "human_todo_count"}
    assert payload["projects"] == [
        {"project_id": "proj-1", "repo_path": str(tmp_path / "repo"), "version": _VERSION}
    ]
    rows = {row["run_id"]: row for row in payload["runs"]}
    assert set(rows) == {run_id}
    row = rows[run_id]
    assert set(row) >= {"run_id", "title", "version", "stage", "control_state", "human_todos"}
    assert row["version"] == _VERSION
    assert row["stage"] == "M-IMPL"
    assert row["control_state"] == "推进中"
    assert row["human_todos"] == 0
    assert payload["human_todo_count"] == 0


# AC-FR0301-01@v0.9 TRACKS-TRACE IF-QUERY-001 unknown project -> 404 not_found
def test_overview_unknown_project_is_not_found(tmp_path: Path):
    home = _seed(tmp_path, run_id="run-overview", events=_RUNNING_EVENTS)

    status, payload = _call(
        api_query.project_overview, _request(home, path_params={"pid": "ghost"})
    )

    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- run detail (GET /api/runs/{run_id}) -------------------------------------


# AC-FR0302-01@v0.9 TRACKS-TRACE IF-QUERY-001 run snapshot carries stage/progress/lease
def test_run_detail_exposes_snapshot_schema(tmp_path: Path):
    run_id = "run-detail"
    home = _seed(tmp_path, run_id=run_id, events=_RUNNING_EVENTS)

    status, payload = _call(api_query.run_detail, _request(home, path_params={"run_id": run_id}))

    assert status == 200
    assert set(payload) >= {
        "run_id",
        "version",
        "stage",
        "substate",
        "control_state",
        "pause",
        "wait",
        "progress",
        "quality",
        "executions",
        "lease",
    }
    assert payload["run_id"] == run_id
    assert payload["version"] == _VERSION
    assert payload["stage"] == "M-IMPL"
    assert payload["control_state"] == "推进中"
    assert payload["wait"] is None
    assert payload["pause"] == {"requested": False, "effective": False}
    assert set(payload["progress"]) >= {
        "tasks_done",
        "tasks_total",
        "tests_passed",
        "ac_closed",
        "ac_total",
    }
    assert payload["progress"]["tasks_done"] == 1
    assert payload["progress"]["tasks_total"] == 2
    assert set(payload["quality"]) == {"gates"}
    assert isinstance(payload["executions"], list)
    assert payload["lease"]["generation"] == 1


# AC-FR0302-01@v0.9 TRACKS-TRACE IF-QUERY-001 unknown run snapshot -> 404
def test_run_detail_unknown_run_is_not_found(tmp_path: Path):
    home = _seed(tmp_path, run_id="run-detail", events=_RUNNING_EVENTS)

    status, payload = _call(api_query.run_detail, _request(home, path_params={"run_id": "ghost"}))

    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- timeline (GET /api/runs/{run_id}/timeline) ------------------------------


def _timeline_seed(tmp_path: Path, run_id: str) -> Path:
    return _seed(
        tmp_path,
        run_id=run_id,
        events=[
            {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
            {
                "type": "task.started",
                "payload": {"task_id": "T-1"},
                "task_id": "T-1",
                "command_id": "cmd-a",
            },
            {
                "type": "verdict.failed",
                "payload": {"ac_refs": ["AC-FR0305-01"], "check": "red_classifier"},
                "command_id": "cmd-b",
            },
        ],
        service_events=[
            ("command.accepted", "cmd-a", {"kind": "create_run"}),
            ("run.pause_requested", "cmd-c", {"actor": "alice"}),
        ],
    )


# AC-FR0305-01@v0.9 TRACKS-TRACE IF-QUERY-001 timeline merges both event sources
# AC-FR0305-02@v0.9 TRACKS-TRACE IF-QUERY-001 timeline filters by command/task/AC
def test_timeline_merges_sources_and_filters(tmp_path: Path):
    run_id = "run-timeline"
    home = _timeline_seed(tmp_path, run_id)

    status, payload = _call(api_query.timeline, _request(home, path_params={"run_id": run_id}))

    assert status == 200
    assert set(payload) >= {"events", "cursor"}
    events = payload["events"]
    assert {event["source"] for event in events} == {"tracks", "service"}
    for event in events:
        assert set(event) >= {
            "source",
            "seq",
            "ts",
            "type",
            "run_id",
            "command_id",
            "task_id",
            "ac_refs",
            "summary",
            "payload_ref",
        }
        assert event["run_id"] == run_id
    assert payload["cursor"]

    status, payload = _call(
        api_query.timeline,
        _request(home, path_params={"run_id": run_id}, query={"command_id": "cmd-a"}),
    )
    assert status == 200
    assert {event["source"] for event in payload["events"]} == {"tracks", "service"}
    assert all(event["command_id"] == "cmd-a" for event in payload["events"])

    status, payload = _call(
        api_query.timeline,
        _request(home, path_params={"run_id": run_id}, query={"task_id": "T-1"}),
    )
    assert status == 200
    assert [event["type"] for event in payload["events"]] == ["task.started"]
    assert payload["events"][0]["task_id"] == "T-1"

    status, payload = _call(
        api_query.timeline,
        _request(home, path_params={"run_id": run_id}, query={"ac_id": "AC-FR0305-01"}),
    )
    assert status == 200
    assert [event["type"] for event in payload["events"]] == ["verdict.failed"]
    assert "AC-FR0305-01" in payload["events"][0]["ac_refs"]


# AC-FR0305-01@v0.9 TRACKS-TRACE IF-QUERY-001 cursor resumes strictly after the page
def test_timeline_cursor_round_trip(tmp_path: Path):
    run_id = "run-cursor"
    home = _timeline_seed(tmp_path, run_id)

    status, first = _call(api_query.timeline, _request(home, path_params={"run_id": run_id}))
    assert status == 200
    assert first["events"] and first["cursor"]

    status, second = _call(
        api_query.timeline,
        _request(home, path_params={"run_id": run_id}, query={"after": first["cursor"]}),
    )
    assert status == 200
    assert second["events"] == []


# AC-FR0305-01@v0.9 TRACKS-TRACE IF-QUERY-001 unknown run timeline -> 404
def test_timeline_unknown_run_is_not_found(tmp_path: Path):
    home = _timeline_seed(tmp_path, "run-timeline")

    status, payload = _call(api_query.timeline, _request(home, path_params={"run_id": "ghost"}))

    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- command read-back (GET /api/commands/{command_id}) ----------------------


# AC-FR0295-01@v0.9 TRACKS-TRACE IF-QUERY-001 persisted command read-back by id
def test_command_status_reads_result_and_failure(tmp_path: Path):
    run_id = "run-command"
    home = _seed(
        tmp_path,
        run_id=run_id,
        events=_RUNNING_EVENTS,
        commands=[
            _command(run_id, "cmd-done", status="completed", result={"run_id": run_id}),
            _command(
                run_id,
                "cmd-failed",
                status="failed",
                failure={"failure_class": "unrecoverable", "reason": "boom"},
            ),
        ],
    )

    status, payload = _call(
        api_query.command_status, _request(home, path_params={"command_id": "cmd-done"})
    )
    assert status == 200
    assert payload["command_id"] == "cmd-done"
    assert payload["kind"] == "create_run"
    assert payload["status"] == "completed"
    assert payload["result"] == {"run_id": run_id}

    status, payload = _call(
        api_query.command_status, _request(home, path_params={"command_id": "cmd-failed"})
    )
    assert status == 200
    assert payload["status"] == "failed"
    assert payload["failure"] == {"failure_class": "unrecoverable", "reason": "boom"}
    assert "result" not in payload


# AC-FR0295-01@v0.9 TRACKS-TRACE IF-QUERY-001 unknown command -> 404
def test_command_status_unknown_command_is_not_found(tmp_path: Path):
    home = _seed(tmp_path, run_id="run-command", events=_RUNNING_EVENTS)

    status, payload = _call(
        api_query.command_status, _request(home, path_params={"command_id": "ghost"})
    )

    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- todos (GET /api/todos, GET /api/runs/{run_id}/todos) --------------------


# AC-FR0307-01@v0.9 TRACKS-TRACE IF-QUERY-001 todo center lists decisions; run view scopes
# AC-FR0307-02@v0.9 TRACKS-TRACE IF-QUERY-001 quota waits never list as human todos
def test_todo_center_and_run_scoping(tmp_path: Path):
    approval_run = "run-approval"
    quiet_run = "run-quiet"
    home = _seed(
        tmp_path,
        run_id=approval_run,
        events=[
            {"type": "stage.entered", "payload": {"stage": "M-REQ-APPROVAL"}},
            {"type": "preview.generated", "payload": {"preview_digest": "d" * 64}},
        ],
        extra_runs=[(quiet_run, [{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}])],
        waits=[(quiet_run, {"wait_class": "quota", "reason": "rate limited"})],
    )

    status, payload = _call(api_query.global_todos, _request(home))
    assert status == 200
    assert [row["run_id"] for row in payload] == [approval_run]
    assert payload[0]["kind"] == "stage_approval"
    assert payload[0]["context"]
    assert payload[0]["action_ref"]
    assert all(row["kind"] != "quota" for row in payload)

    status, payload = _call(
        api_query.run_todos, _request(home, path_params={"run_id": approval_run})
    )
    assert status == 200
    assert [row["kind"] for row in payload] == ["stage_approval"]

    status, payload = _call(api_query.run_todos, _request(home, path_params={"run_id": quiet_run}))
    assert status == 200
    assert payload == []

    status, payload = _call(api_query.run_todos, _request(home, path_params={"run_id": "ghost"}))
    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- read-only + redaction ---------------------------------------------------


# AC-FR0295-04@v0.9 TRACKS-TRACE IF-QUERY-001 query sweep leaves both stores unchanged
# AC-FR0301-02@v0.9 TRACKS-TRACE IF-QUERY-001 queries return without touching the write path
def test_query_surface_is_read_only(tmp_path: Path):
    run_id = "run-readonly"
    home = _seed(
        tmp_path,
        run_id=run_id,
        events=_RUNNING_EVENTS,
        service_events=[("command.accepted", "cmd-a", {"kind": "create_run"})],
        commands=[_command(run_id, "cmd-done", status="completed", result={"run_id": run_id})],
    )
    before = _counts(home, tmp_path / "repo")

    sweep = [
        (api_query.project_overview, {"pid": "proj-1"}),
        (api_query.run_detail, {"run_id": run_id}),
        (api_query.timeline, {"run_id": run_id}),
        (api_query.ac_chain, {"run_id": run_id}),
        (api_query.run_todos, {"run_id": run_id}),
        (api_query.global_todos, {}),
        (api_query.command_status, {"command_id": "cmd-done"}),
    ]
    for handler, path_params in sweep:
        status, _payload = _call(handler, _request(home, path_params=path_params))
        assert status == 200, f"{handler.__name__} must serve the read-only query"

    assert _counts(home, tmp_path / "repo") == before


# AC-FR0315-01@v0.9 TRACKS-TRACE IF-QUERY-001 query payloads pass through the redactor
def test_query_payloads_pass_through_redactor(tmp_path: Path):
    run_id = "run-secret"
    home = _seed(
        tmp_path,
        run_id=run_id,
        events=_RUNNING_EVENTS,
        waits=[
            (
                run_id,
                {
                    "wait_class": "quota",
                    "reason": f"exhausted for {_CANARY_VALUE}",
                    "known_reset": False,
                },
            )
        ],
        commands=[
            _command(
                run_id,
                "cmd-secret",
                status="completed",
                result={"detail": f"token={_CANARY_VALUE}"},
            )
        ],
    )
    redactor = SecretRedactor({_CANARY_NAME: _CANARY_VALUE})
    assert _CANARY_VALUE in _wait_reason(home, run_id)

    status, detail = _call(
        api_query.run_detail, _request(home, path_params={"run_id": run_id}, redactor=redactor)
    )
    assert status == 200
    assert detail["wait"]["reason"] == f"exhausted for ${{{_CANARY_NAME}}}"

    status, command = _call(
        api_query.command_status,
        _request(home, path_params={"command_id": "cmd-secret"}, redactor=redactor),
    )
    assert status == 200
    assert command["result"]["detail"] == f"token=${{{_CANARY_NAME}}}"

    rendered = json.dumps([detail, command])
    assert _CANARY_VALUE not in rendered
    assert _CANARY_NAME in rendered
