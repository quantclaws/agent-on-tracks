"""v0.12 run/round/gate seeds (IF-ROUNDS-001, IF-REVIEWDECISION-001, IF-STAGEPATH).

Seeds the documented storage contracts only: the service-plane ``projects``
row (interfaces §1c table 2), the run's tracks.db through the real
``Store``, content-addressed blobs and ``.opencode/logs`` JSONL samples.
Each seed arranges a real observable state the HTTP/UI layer resolves; it
never fabricates the response under test.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tracks.paths import tracks_home
from tracks.store import Store
from tracks.supervisor import db as service_db

_TS = "2026-10-19T00:00:00+00:00"
_TRIO = ("story.md", "spec.md", "acceptance.md")


def prepare_home(home: Path, repo: Path, *, project_id: str, version: str) -> None:
    """Create the service store and register the project row."""
    home.mkdir(parents=True, exist_ok=True)
    service_db.ServiceDB(home)
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), version, "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()


def write_docs(repo: Path, version: str, body: str = "review body\n") -> Path:
    vdir = repo / ".tracks" / "projects" / version
    vdir.mkdir(parents=True, exist_ok=True)
    from tracks import templating

    for name in _TRIO:
        if name == "story.md":
            text = templating.render_story_skeleton("评审轮故事", "2026-10-19")
        else:
            text = (
                "---\n"
                "envelope: tracks-envelope:v2\n"
                "---\n\n"
                f"# {version} {name}\n\n{body}"
            )
        (vdir / name).write_text(text, encoding="utf-8")
    if (repo / ".git").exists():
        subprocess.run(
            ["git", "add", ".tracks/projects"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"seed {version} docs"],
            cwd=repo,
            check=False,
            capture_output=True,
        )
    return vdir


def _store(repo: Path) -> Store:
    return Store(tracks_home(repo))


def seed_awaiting_review(
    repo: Path,
    run_id: str,
    *,
    version: str = "v1.0",
    stage: str = "M-STORY",
) -> Store:
    """A run parked at the human review gate (§1h.1) through real events."""
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": stage})
    store.append(run_id, version, "sage.verdict", {"verdict": "pass"})
    return store


def seed_exception_gate(
    repo: Path,
    run_id: str,
    *,
    version: str = "v1.0",
    stage: str = "M-IMPL",
) -> Store:
    """A run parked at an exception gate (escalation) through real events."""
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": stage})
    store.append(
        run_id,
        version,
        "run.breaker_tripped",
        {"condition": "budget exhausted", "report": "evidence tail"},
    )
    return store


def seed_no_stage_events(repo: Path, run_id: str, *, version: str = "v1.0") -> Store:
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    return store


def seed_stage_path_run(repo: Path, run_id: str, *, version: str = "v1.0") -> Store:
    """Stages covering done / void / attention / pending in one run."""
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-START"})
    store.append(run_id, version, "stage.exited", {"stage": "M-START"})
    store.append(run_id, version, "stage.entered", {"stage": "M-STORY"})
    store.append(run_id, version, "stage.rolled_back", {"to_stage": "M-ACC"})
    store.append(run_id, version, "stage.entered", {"stage": "M-ACC"})
    store.append(run_id, version, "stage.exited", {"stage": "M-ACC"})
    store.append(run_id, version, "stage.entered", {"stage": "M-SPEC"})
    store.append(run_id, version, "sage.verdict", {"verdict": "pass"})
    return store


def seed_workflow_run(repo: Path, run_id: str, *, version: str = "v1.0") -> Store:
    """A stage window with repeated dispatches, outcomes and one human act."""
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
    for index, substate in enumerate(("RED", "RED", "GREEN", "RED"), start=1):
        command_id = f"cmd-workflow-{index}"
        store.append(
            run_id,
            version,
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "command_id": command_id,
                    "params": {
                        "role": "devon",
                        "substate": substate,
                        "stage": "M-IMPL",
                        "attempt": index,
                        "review_round": 1,
                    },
                }
            },
            command_id=command_id,
        )
        store.append(
            run_id,
            version,
            "outcome.received",
            {"status": "done", "agent_io": {"stdout": substate}},
            command_id=command_id,
        )
    store.append(run_id, version, "human.retry", {"actor": "local-user"})
    return store


def seed_heartbeat_wait(repo: Path, run_id: str, *, version: str = "v1.0") -> Store:
    """An awaiting gate whose last events are heartbeats (FR-0358 heartbeat rule)."""
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-STORY"})
    store.append(run_id, version, "sage.verdict", {"verdict": "pass"})
    now = datetime.now(timezone.utc)
    for seq in (1, 2):
        _set_ts(store, run_id, seq, (now - timedelta(seconds=120)).isoformat())
    _set_ts(store, run_id, 3, (now - timedelta(seconds=95)).isoformat())
    for offset, event_type in ((30, "lease.acquired"), (5, "lease.released")):
        event = store.append(run_id, version, event_type, {})
        _set_ts(store, run_id, event.seq, (now - timedelta(seconds=offset)).isoformat())
    return store


def seed_rounds(
    repo: Path,
    run_id: str,
    *,
    version: str = "v1.0",
    stage: str = "M-TEST",
    count: int = 2,
    with_logs: bool = False,
) -> list[str]:
    """Seed ``count`` dispatch/outcome rounds with input/output blobs.

    Returns the command ids in round order. With ``with_logs`` a JSONL
    thinking sample is written for the matched session (the rotation
    degradation case simply omits it).
    """
    store = _store(repo)
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": stage})
    command_ids: list[str] = []
    dispatch_ts: list[str] = []
    for index in range(1, count + 1):
        command_id = f"cmd-round-{index}"
        command_ids.append(command_id)
        event = store.append(
            run_id,
            version,
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "command_id": command_id,
                    "params": {
                        "role": "devon",
                        "substate": "RED",
                        "stage": stage,
                        "attempt": index,
                        "review_round": index,
                        "doc": "tests/integration/test_sample.py",
                        "objective": f"round {index}",
                        "assignment": {"kind": "RGR", "phase": "red"},
                    },
                }
            },
            command_id=command_id,
        )
        dispatch_ts.append(event.ts)
        input_ref = store.write_audit_blob(
            {
                "prompt": f"round {index} prompt .tracks/projects/{version}/test-plan.md",
                "assignment": {"kind": "RGR", "phase": "red"},
                "role": "devon",
                "substate": "RED",
                "attempt": index,
                "review_round": index,
                "doc": "tests/integration/test_sample.py",
                "objective": f"round {index}",
            }
        )
        stdout = "\n".join(
            [
                json.dumps({"type": "text", "part": {"text": f"round {index} output"}}),
                json.dumps({"type": "tool_use", "part": {"tool": "read"}}),
            ]
        )
        output_ref = store.write_audit_blob({"stdout": stdout, "stderr": ""})
        store.append(
            run_id,
            version,
            "outcome.received",
            {
                "status": "done",
                "output_summary": f"round {index} summary",
                "agent_io": {
                    "input_ref": input_ref,
                    "output_ref": output_ref,
                    "audit_completeness": "complete",
                },
            },
            command_id=command_id,
        )
    if with_logs:
        write_thinking_log(repo, version, dispatch_ts)
    return command_ids


def write_thinking_log(repo: Path, version: str, dispatch_ts: list[str]) -> Path:
    logs = repo / ".opencode" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "session-1.jsonl"
    template = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "assets"
        / "v0.12"
        / "rounds_session.jsonl"
    ).read_text(encoding="utf-8").strip()
    lines = [
        template.replace("__TS__", ts)
        .replace("__INDEX__", str(index))
        .replace("__VERSION__", version)
        for index, ts in enumerate(dispatch_ts, start=1)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _set_ts(store: Store, run_id: str, seq: int, ts: str) -> None:
    with store.conn:
        store.conn.execute(
            "UPDATE events SET ts = ? WHERE run_id = ? AND seq = ?",
            (ts, run_id, seq),
        )


def events(repo: Path, run_id: str) -> list[dict]:
    conn = sqlite3.connect(str(tracks_home(repo) / "runtime" / "tracks.db"))
    try:
        rows = conn.execute(
            "SELECT seq, ts, type, command_id, payload FROM events"
            " WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "seq": row[0],
            "ts": row[1],
            "type": row[2],
            "command_id": row[3],
            "payload": json.loads(row[4]),
        }
        for row in rows
    ]


def wait_event(
    repo: Path,
    run_id: str,
    event_type: str,
    *,
    timeout: float = 30.0,
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = [event for event in events(repo, run_id) if event["type"] == event_type]
        if found:
            return found[-1]
        time.sleep(0.2)
    raise AssertionError(f"{event_type} never landed for {run_id}")
