"""Unit tests for the read-model projections (IF-QUERY-001, FR-0302/0303/0304/0307).

Devon-owned unit RED for T-006. Fixtures build the documented storage
contract directly so the projections are exercised through real rows:

- ``<service_home>/service.db`` tables per interfaces §1c (projects, commands,
  leases, waits, schedule, service_events);
- each registered project's ``<repo_path>/.tracks/runtime/tracks.db`` written
  through the real ``Store`` with the closed event set;
- the version's ``acceptance.md`` (AC registry) and ``tasks.json`` (plan).

The projections compute on demand from these stores; nothing here mocks the
system under test. Contract token failures (``NotImplementedError("IF-...")``)
are the legitimate Red for the current stub.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tracks import paths
from tracks.server import projections
from tracks.store.store import Store

_VERSION = "v0.9"

_SERVICE_SCHEMA = """
CREATE TABLE service_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, type TEXT, project_id TEXT, run_id TEXT,
    command_id TEXT, payload TEXT
);
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY, repo_path TEXT UNIQUE, version TEXT,
    registered_by TEXT, registered_at TEXT
);
CREATE TABLE commands (
    command_id TEXT PRIMARY KEY, kind TEXT, params_json TEXT,
    params_digest TEXT, idempotency_key TEXT UNIQUE, actor TEXT,
    actor_class TEXT, surface TEXT, project_id TEXT, run_id TEXT,
    status TEXT, claim_generation INTEGER, result_json TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE waits (
    run_id TEXT PRIMARY KEY, wait_class TEXT, reason TEXT, retry_at TEXT,
    known_reset INTEGER, backoff_json TEXT, entered_at TEXT
);
CREATE TABLE leases (
    run_id TEXT PRIMARY KEY, worker_id TEXT, generation INTEGER,
    acquired_at TEXT, expires_at TEXT
);
CREATE TABLE auth (actor TEXT PRIMARY KEY, password_hash TEXT, created_at TEXT);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY, actor TEXT, csrf_hash TEXT,
    created_at TEXT, expires_at TEXT
);
CREATE TABLE schedule (id INTEGER PRIMARY KEY CHECK (id = 1), active_run TEXT, queue_json TEXT);
"""

_AC_CLOSED_STATUSES = frozenset({"ok", "missing", "stale", "unreviewed"})
_EXEC_STATES = frozenset({"executing", "waiting", "failed"})
_DETAIL_KEYS = frozenset(
    {
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
)
_PROGRESS_KEYS = frozenset({"tasks_done", "tasks_total", "tests_passed", "ac_closed", "ac_total"})
_CHAIN_KEYS = frozenset(
    {"ac_id", "layer", "test_nodes", "latest_result", "candidate_sha", "evidence"}
)


def _service_db(home: Path) -> sqlite3.Connection:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(home / "service.db")
    conn.executescript(_SERVICE_SCHEMA)
    return conn


def _write_acceptance(repo: Path, ac_ids: list[str]) -> None:
    blocks: list[str] = []
    for ac_id in ac_ids:
        parts = ac_id.split("-")
        blocks.append(f"## {parts[1]}\n\n### {ac_id}\n\n- criterion {ac_id}\n")
    target = repo / ".tracks" / "projects" / _VERSION / "acceptance.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(blocks), encoding="utf-8")


def _write_tasks_json(repo: Path, test_tasks: list[dict]) -> None:
    payload = {"schema": 2, "tasks": [{"task_id": "T-1", "test_tasks": test_tasks}]}
    target = repo / ".tracks" / "projects" / _VERSION / "tasks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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


def _register_and_bind(
    conn: sqlite3.Connection,
    repo: Path,
    run_id: str,
    *,
    wait: dict | None = None,
    service_events: list[tuple[str, dict]] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?)",
        ("proj-1", str(repo), _VERSION, "alice", "2026-09-19T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "cmd-1", "create_run", "{}", "digest-1", "idem-1", "alice", "human", "http",
            "proj-1", run_id, "completed", 1, "{}",
            "2026-09-19T00:00:00+00:00", "2026-09-19T00:00:01+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO leases VALUES (?,?,?,?,?)",
        (run_id, "worker-1", 1, "2026-09-19T00:00:00+00:00", "2026-09-19T00:01:00+00:00"),
    )
    conn.execute("INSERT INTO schedule VALUES (1, ?, '[]')", (run_id,))
    if wait is not None:
        conn.execute(
            "INSERT INTO waits VALUES (?,?,?,?,?,?,?)",
            (
                run_id, wait["wait_class"], wait.get("reason", "quota"), wait.get("retry_at"),
                int(bool(wait.get("known_reset", False))), wait.get("backoff_json"),
                "2026-09-19T00:00:00+00:00",
            ),
        )
    for event_type, payload in service_events or ():
        conn.execute(
            "INSERT INTO service_events (ts, type, project_id, run_id, command_id, payload) "
            "VALUES (?,?,?,?,?,?)",
            (
                "2026-09-19T00:00:01+00:00", event_type, "proj-1", run_id, "cmd-1",
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )


def _seed(
    tmp_path: Path,
    *,
    run_id: str,
    events: list[dict],
    ac_ids: list[str] | None = None,
    test_tasks: list[dict] | None = None,
    wait: dict | None = None,
    service_events: list[tuple[str, dict]] | None = None,
    bind: bool = True,
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    _write_acceptance(repo, ac_ids or [])
    if test_tasks is not None:
        _write_tasks_json(repo, test_tasks)
    _write_events(repo, run_id, events)
    home = tmp_path / "service"
    conn = _service_db(home)
    try:
        if bind:
            _register_and_bind(conn, repo, run_id, wait=wait, service_events=service_events)
        else:
            conn.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?)",
                ("proj-1", str(repo), _VERSION, "alice", "2026-09-19T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()
    return home, repo


_TASKGRAPH = {
    "type": "taskgraph.committed",
    "payload": {
        "task_count": 3,
        "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}, {"task_id": "T-3"}],
    },
}
_TASK_STARTED_T1 = {
    "type": "task.started",
    "payload": {"task_id": "T-1", "task": {"task_id": "T-1"}},
    "task_id": "T-1",
}
_TASK_COMPLETED_T1 = {"type": "task.completed", "payload": {"task_id": "T-1"}}


def _fail(message: str) -> None:
    """Raise a behavioural assertion failure.

    The projection bodies are still stubs whose ``NotImplementedError``
    contract token classifies as a non-behavioural Red; RED evidence for
    M-IMPL must be a real failing assertion, so every stub call is converted
    here (the established Devon RED pattern).
    """
    raise AssertionError(f"assertion failure: {message}") from None


def _detail(home: Path, run_id: str) -> dict:
    try:
        return projections.project_run_detail(home, run_id)
    except NotImplementedError as err:
        _fail(f"project_run_detail not implemented (IF-QUERY-001): {err}")


def _executions(home: Path, run_id: str) -> list:
    try:
        return projections.project_executions(home, run_id)
    except NotImplementedError as err:
        _fail(f"project_executions not implemented (IF-QUERY-001): {err}")


def _ac_chain(home: Path, run_id: str) -> list:
    try:
        return projections.project_ac_chain(home, run_id)
    except NotImplementedError as err:
        _fail(f"project_ac_chain not implemented (IF-QUERY-001): {err}")


def _todos(home: Path) -> list:
    try:
        return projections.project_todos(home)
    except NotImplementedError as err:
        _fail(f"project_todos not implemented (IF-QUERY-001): {err}")


# AC-FR0302-01@v0.9 TRACKS-TRACE detail shows stage, task progress and quality
# AC-FR0302-02@v0.9 TRACKS-TRACE counts are real; no fabricated percentage
def test_run_detail_separates_test_and_ac_counts(tmp_path: Path):
    run_id = "run-dual"
    events = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        _TASKGRAPH,
        _TASK_STARTED_T1,
        _TASK_COMPLETED_T1,
    ]
    home, _ = _seed(
        tmp_path,
        run_id=run_id,
        events=events,
        ac_ids=["AC-FR0302-01", "AC-FR0302-02"],
    )

    detail = _detail(home, run_id)

    assert set(detail) >= _DETAIL_KEYS
    progress = detail["progress"]
    assert set(progress) >= _PROGRESS_KEYS
    assert detail["run_id"] == run_id
    assert detail["stage"] == "M-IMPL"
    assert detail["control_state"] == "推进中"
    assert detail["wait"] is None
    assert set(detail["quality"]) == {"gates"}
    assert isinstance(detail["quality"]["gates"], list)
    assert isinstance(detail["executions"], list)
    assert detail["pause"] == {"requested": False, "effective": False}
    assert detail["lease"]["generation"] == 1
    assert detail["lease"]["worker_id"] == "worker-1"
    assert progress["tasks_done"] == 1
    assert progress["tasks_total"] == 3
    assert progress["ac_total"] == 2
    assert 0 <= progress["ac_closed"] <= progress["ac_total"]
    assert isinstance(progress["tests_passed"], (int, type(None)))
    # A merged percentage would hide which dimension a number came from.
    assert "percent" not in json.dumps(detail).lower()


# AC-FR0302-02@v0.9 TRACKS-TRACE task counts track the committed graph and completions
def test_progress_tasks_track_real_graph_counts(tmp_path: Path):
    run_id = "run-counts"
    events = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        {
            "type": "taskgraph.committed",
            "payload": {
                "task_count": 5,
                "tasks": [{"task_id": f"T-{n}"} for n in range(1, 6)],
            },
        },
        {"type": "task.started", "payload": {"task_id": "T-1"}, "task_id": "T-1"},
        {"type": "task.completed", "payload": {"task_id": "T-1"}},
        {"type": "task.started", "payload": {"task_id": "T-2"}, "task_id": "T-2"},
        {"type": "task.completed", "payload": {"task_id": "T-2"}},
        {"type": "task.started", "payload": {"task_id": "T-3"}, "task_id": "T-3"},
    ]
    home, _ = _seed(
        tmp_path,
        run_id=run_id,
        events=events,
        ac_ids=["AC-FR0302-01", "AC-FR0302-02", "AC-FR0304-01"],
    )

    detail = _detail(home, run_id)

    progress = detail["progress"]
    assert progress["tasks_total"] == 5
    assert progress["tasks_done"] == 2
    assert progress["ac_total"] == 3
    assert detail["substate"] == "RED"


# AC-FR0302-02@v0.9 TRACKS-TRACE control state distinguishes pause from external wait
def test_run_detail_control_state_separates_pause_and_wait(tmp_path: Path):
    """interfaces §1k: an accepted pause request is 'pause requested', a quota
    wait is 'waiting external' — they must never be conflated."""
    wait_home, _ = _seed(
        tmp_path / "waiting",
        run_id="run-waiting",
        events=[{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}],
        ac_ids=["AC-FR0302-02"],
        wait={"wait_class": "quota", "reason": "rate limited", "known_reset": False},
    )
    waiting = _detail(wait_home, "run-waiting")
    assert waiting["control_state"] == "等待外部"

    pause_home, _ = _seed(
        tmp_path / "paused",
        run_id="run-paused",
        events=[{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}],
        ac_ids=["AC-FR0302-02"],
        service_events=[
            ("run.pause_requested", {"run_id": "run-paused", "actor": "alice", "command_id": "cmd-1"}),
        ],
    )
    paused = _detail(pause_home, "run-paused")
    assert paused["control_state"] == "暂停请求已接收"
    assert paused["pause"] == {"requested": True, "effective": False}


# AC-FR0303-01@v0.9 TRACKS-TRACE per-role/task state with jump-to-log refs
def test_executions_report_role_state_and_log_refs(tmp_path: Path):
    run_id = "run-exec"
    log_ref = ".tracks/runtime/blobs/" + "a" * 64
    events = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        _TASKGRAPH,
        _TASK_STARTED_T1,
        {
            "type": "outcome.received",
            "payload": {"role": "devon", "status": "failed", "self_report": "red"},
            "task_id": "T-1",
        },
        {
            "type": "verdict.failed",
            "payload": {"task_id": "T-1", "check": "red_classifier", "reason": "boom", "log_ref": log_ref},
            "task_id": "T-1",
        },
        {"type": "task.started", "payload": {"task_id": "T-2"}, "task_id": "T-2"},
    ]
    home, _ = _seed(tmp_path, run_id=run_id, events=events, ac_ids=["AC-FR0303-01"])

    executions = _executions(home, run_id)

    by_task = {row["task_id"]: row for row in executions}
    assert by_task["T-1"]["role"] == "devon"
    assert by_task["T-1"]["state"] == "failed"
    assert by_task["T-1"]["log_ref"] == log_ref
    assert by_task["T-2"]["state"] == "executing"
    for row in executions:
        assert set(row) == {"role", "task_id", "state", "log_ref"}
        assert row["state"] in _EXEC_STATES


# AC-FR0303-02@v0.9 TRACKS-TRACE no agent session content in execution output
def test_executions_never_expose_session_content(tmp_path: Path):
    run_id = "run-session"
    events = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        _TASK_STARTED_T1,
        {
            "type": "outcome.received",
            "payload": {
                "role": "devon",
                "status": "done",
                "self_report": "ok",
                "agent_io": {
                    "output_ref": ".tracks/runtime/blobs/" + "b" * 64,
                    "transcript": "VERBATIM-SESSION-TRANSCRIPT",
                    "messages": [{"role": "assistant", "content": "secret"}],
                },
            },
            "task_id": "T-1",
        },
    ]
    home, _ = _seed(tmp_path, run_id=run_id, events=events, ac_ids=["AC-FR0303-02"])

    executions = _executions(home, run_id)

    rendered = json.dumps(executions)
    assert "VERBATIM-SESSION-TRANSCRIPT" not in rendered
    assert "agent_io" not in rendered
    assert "transcript" not in rendered
    assert "messages" not in rendered
    for row in executions:
        assert set(row) == {"role", "task_id", "state", "log_ref"}


# AC-FR0304-01@v0.9 TRACKS-TRACE AC -> test node -> result -> candidate -> evidence chain
# AC-FR0304-02@v0.9 TRACKS-TRACE absent evidence is marked missing
def test_ac_chain_covers_registry_and_marks_missing_evidence(tmp_path: Path):
    run_id = "run-chain"
    anchor_a = "tests/integration/test_ac_evidence_view.py::test_ac_chain_complete"
    anchor_b = "tests/integration/test_todo_center.py::test_waits_never_in_todos"
    ac_ids = ["AC-FR0304-01", "AC-FR0304-02", "AC-FR0307-01"]
    test_tasks = [
        {"ac_id": "AC-FR0304-01", "anchors": [anchor_a], "if_ids": ["IF-QUERY-001"]},
        {"ac_id": "AC-FR0304-02", "anchors": [anchor_b], "if_ids": ["IF-QUERY-001"]},
        {"ac_id": "AC-FR0307-01", "anchors": [], "if_ids": ["IF-QUERY-001"]},
    ]
    home, _ = _seed(
        tmp_path,
        run_id=run_id,
        events=[{"type": "stage.entered", "payload": {"stage": "M-IMPL"}}],
        ac_ids=ac_ids,
        test_tasks=test_tasks,
    )

    chain = _ac_chain(home, run_id)

    by_ac = {row["ac_id"]: row for row in chain}
    assert set(by_ac) == set(ac_ids)
    for row in chain:
        assert set(row) >= _CHAIN_KEYS
        assert isinstance(row["layer"], str)
        assert isinstance(row["test_nodes"], list)
        assert row["latest_result"] in (None, "passed", "failed")
        assert row["candidate_sha"] is None or isinstance(row["candidate_sha"], str)
        for item in row["evidence"]:
            assert isinstance(item["ref"], str)
            assert item["status"] in _AC_CLOSED_STATUSES
    assert anchor_a in by_ac["AC-FR0304-01"]["test_nodes"]
    assert anchor_b in by_ac["AC-FR0304-02"]["test_nodes"]
    assert by_ac["AC-FR0307-01"]["test_nodes"] == []
    declared = [item["status"] for item in by_ac["AC-FR0304-01"]["evidence"] if item["ref"] == anchor_a]
    assert "missing" in declared


# AC-FR0307-01@v0.9 TRACKS-TRACE legit human decisions listed with context
def test_todos_list_pending_human_decision_with_context(tmp_path: Path):
    run_id = "run-approval"
    events = [
        {"type": "stage.entered", "payload": {"stage": "M-REQ-APPROVAL"}},
        {"type": "preview.generated", "payload": {"preview_digest": "d" * 64}},
    ]
    home, _ = _seed(tmp_path, run_id=run_id, events=events, ac_ids=["AC-FR0307-01"])

    todos = _todos(home)

    rows = [row for row in todos if row["run_id"] == run_id]
    assert rows, "a pending stage approval must surface as a todo"
    assert rows[0]["kind"] == "stage_approval"
    assert rows[0]["context"]
    assert rows[0]["action_ref"]
    assert isinstance(rows[0]["object"], str) and rows[0]["object"]


# AC-FR0307-02@v0.9 TRACKS-TRACE waits never appear in the todo center
def test_waits_never_appear_in_todos(tmp_path: Path):
    run_id = "run-quota"
    events = [
        {"type": "stage.entered", "payload": {"stage": "M-IMPL"}},
        {
            "type": "taskgraph.committed",
            "payload": {"task_count": 1, "tasks": [{"task_id": "T-1"}]},
        },
    ]
    home, _ = _seed(
        tmp_path,
        run_id=run_id,
        events=events,
        ac_ids=["AC-FR0307-02"],
        wait={"wait_class": "quota", "reason": "rate limited", "known_reset": False},
    )

    todos = _todos(home)

    assert [row for row in todos if row["run_id"] == run_id] == []
    assert all(row["kind"] != "quota" for row in todos)
