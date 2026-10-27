"""Integration: UI readability + read-face performance (IF-UIPERF-001).

The contract under test is interfaces §1g:

* the ac-chain read face SHORT-CIRCUITS on an empty AC registry (the
  ``if not acs: return []`` guard precedes the event replay) and answers in
  under 5 ms for a 0-AC run;
* the slim timeline payload stays under 50 KB with a p95 under 500 ms and
  carries no full evidence;
* the unified Todos inbox covers the three sources (awaiting runs, pending
  attention blocks, pending evaluated intakes) and every item carries the
  context fields plus a navigable ``action_ref``;
* the backlog title is READABLE and NEVER a ULID.

TEST-2: every assertion lands on a PUBLIC outlet (interfaces §4c) -- the
HTTP read face of a real serve subprocess:

* ``GET /api/runs/{id}/ac-chain``;
* ``GET /api/runs/{id}/timeline?slim=1``;
* ``GET /api/todos``;
* ``GET /api/projects/{pid}/releases`` (the backlog group).

No private helper is imported and no internal return value is asserted: the
tests drive the real serve subprocess over HTTP and read its responses.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_get,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tracks.paths import tracks_home
from tracks.store import Store

pytestmark = pytest.mark.integration

_AC_CHAIN_BUDGET_MS = 5.0
_SLIM_BUDGET_BYTES = 50 * 1024
_SLIM_P95_BUDGET_MS = 500.0
_TS = "2026-10-27T00:00:00+00:00"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _start_serve(tmp_path: Path):
    """Real serve subprocess over an isolated home + host repo."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("hotfix-262 ui host\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".tracks/\n")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "hf262@example.com"),
        ("config", "user.name", "HF262 Human"),
        ("add", "README.md", ".gitignore"),
        ("commit", "-m", "initial"),
    ):
        _git(repo, *args)
    proc = start_serve(home, repo, port=0)
    base = parse_base_url(wait_for_port_line(proc))
    wait_for_healthz(base)
    cookie, _csrf = login_session(base)
    return proc, base, cookie, home, repo


def _register(home: Path, repo: Path, project_id: str, version: str) -> None:
    """The service-plane projects row the projections resolve."""
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects "
            "(project_id TEXT, repo_path TEXT, version TEXT, owner TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), version, "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _append(repo: Path, run_id: str, version: str, kind: str, payload: dict) -> None:
    """Append one run-plane event through the real store (the event outlet)."""
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, version, kind, payload)
    finally:
        store.close()


def _get(base: str, cookie: str, path: str) -> dict:
    status, body = http_get(base, path, cookies=cookie)
    assert status == 200, f"{path} must resolve: {status} {body[:200]}"
    return json.loads(body)


# AC-FR0335-02@v0.11 TRACKS-TRACE slim timeline payload bounded
def test_slim_timeline_payload_bounded(tmp_path: Path):
    """AC-FR0335-02: the slim timeline payload is bounded -- under 50 KB,
    with a p95 under 500 ms -- and carries no full evidence blob.

    Contract: interfaces §1g item 2 (the slim tightening), §4c outlet 2.

    Outlet: ``GET /api/runs/{id}/timeline?slim=1`` over a real serve.
    """
    proc, base, cookie, home, repo = _start_serve(tmp_path)
    try:
        _register(home, repo, "proj-hf262-slim", "v0.8")
        run_id = "run-hf262-slim"
        # A run carrying a LARGE evidence payload per event: the slim face
        # must not carry it through.
        for index in range(50):
            _append(
                repo,
                run_id,
                "v0.8",
                "verdict.failed",
                {
                    "check": "collection",
                    "reason": "fixture failure",
                    "attempt": index + 1,
                    "evidence": "x" * 20_000,
                },
            )
        _append(repo, run_id, "v0.8", "stage.entered", {"stage": "M-TEST"})

        samples = []
        for _ in range(20):
            started = time.perf_counter()
            timeline = _get(base, cookie, f"/api/runs/{run_id}/timeline?slim=1")
            samples.append((time.perf_counter() - started) * 1000)
        rows = timeline.get("events") or []
        assert rows, "the slim timeline must not be empty"

        payload_bytes = len(
            json.dumps(timeline, ensure_ascii=False, default=str).encode("utf-8")
        )
        assert payload_bytes < _SLIM_BUDGET_BYTES, (
            f"the slim timeline payload must stay under {_SLIM_BUDGET_BYTES} bytes "
            f"(got {payload_bytes})"
        )

        # The slim rows carry no full evidence blob.
        for row in rows:
            payload = row.get("payload") or {}
            assert "evidence" not in payload or len(str(payload["evidence"])) < 1024, (
                "the slim timeline rows must not carry the full evidence payload"
            )

        samples.sort()
        p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
        assert p95 < _SLIM_P95_BUDGET_MS, (
            f"the slim timeline p95 must stay under {_SLIM_P95_BUDGET_MS} ms "
            f"(got {p95})"
        )
    finally:
        stop_serve(proc)


# AC-NFR0150-02@v0.9 TRACKS-TRACE ac-chain short circuits empty registry
def test_ac_chain_short_circuits_empty_registry(tmp_path: Path):
    """AC-NFR0150-02: the ac-chain read face short-circuits on an empty AC
    registry -- it returns an empty chain and answers in under 5 ms, without
    replaying the run's events.

    Contract: interfaces §1g item 1 (the ``if not acs: return []`` guard
    precedes ``_run_events`` / ``project_state``), §4c outlet 1.

    Outlet: ``GET /api/runs/{id}/ac-chain`` over a real serve.
    """
    proc, base, cookie, home, repo = _start_serve(tmp_path)
    try:
        _register(home, repo, "proj-hf262-acchain", "v0.8")
        run_id = "run-hf262-acchain"
        # The registry is empty: the version has no acceptance.md, so the
        # ac-chain has no AC to report.
        _append(repo, run_id, "v0.8", "story.requested", {"raw_chars": 1})
        _append(repo, run_id, "v0.8", "stage.entered", {"stage": "M-IMPL"})
        # A bounded event volume: a NON-short-circuiting read face is
        # measurably over the contract's 5 ms budget, while a
        # short-circuiting one stays orders of magnitude below it.
        for index in range(5000):
            _append(
                repo,
                run_id,
                "v0.8",
                "task.started",
                {"task_id": f"T-{index:05d}", "task": {"task_id": f"T-{index:05d}"}},
            )

        samples = []
        for _ in range(5):
            started = time.perf_counter()
            chain = _get(base, cookie, f"/api/runs/{run_id}/ac-chain")
            samples.append((time.perf_counter() - started) * 1000)
        assert chain == [], f"the 0-AC run's ac-chain must be empty (got {chain!r})"
        best = min(samples)
        assert best < _AC_CHAIN_BUDGET_MS, (
            f"the 0-AC ac-chain must answer under {_AC_CHAIN_BUDGET_MS} ms "
            f"(best {best}) -- the empty-registry short-circuit must precede "
            "the event replay"
        )
    finally:
        stop_serve(proc)


# AC-FR0333-01@v0.11 TRACKS-TRACE unified todos inbox coverage
def test_unified_todos_inbox_coverage(tmp_path: Path):
    """AC-FR0333-01: the Todos inbox is UNIFIED -- it covers the awaiting
    runs, the pending attention blocks AND the pending evaluated intakes.

    Contract: interfaces §1g item 5 (the three sources of the unified
    inbox), §4c outlet 6.

    Outlet: ``GET /api/todos`` over a real serve.
    """
    proc, base, cookie, home, repo = _start_serve(tmp_path)
    try:
        _register(home, repo, "proj-hf262-todos", "v0.8")
        # Source (a): a run awaiting the release decision.
        _append(repo, "RUN-HF262-TODO-A", "v0.8", "stage.entered", {"stage": "M-RELEASE"})
        _append(
            repo,
            "RUN-HF262-TODO-A",
            "v0.8",
            "release.previewed",
            {"preview_digest": "sha256:abc", "candidate_sha": "c" * 40},
        )
        # Source (b): a run with an unresolved attention block.
        _append(
            repo, "RUN-HF262-TODO-B", "v0.8", "stage.entered", {"stage": "M-REQ-APPROVAL"}
        )
        _append(
            repo,
            "RUN-HF262-TODO-B",
            "v0.8",
            "attention.required",
            {
                "area": "issue_creation",
                "reason": "missing_token",
                "stage": "M-REQ-APPROVAL",
            },
        )
        # Source (c): a pending evaluated intake.
        _seed_evaluated_intake(home, "intake-hf262-1")

        todos = _get(base, cookie, "/api/todos")
        assert todos, "the unified Todos inbox must not be empty"

        kinds = {str(todo.get("kind") or "") for todo in todos}
        assert "release_decision" in kinds, (
            "the inbox must cover the awaiting-release source (kinds: "
            f"{sorted(kinds)})"
        )

        # Source (b): the pending attention block.
        blocking = [
            todo
            for todo in todos
            if "attention" in str(todo.get("kind") or "")
            or "missing_token" in json.dumps(todo, ensure_ascii=False, default=str)
        ]
        assert blocking, (
            "the inbox must cover the pending attention blocks (todos: "
            f"{json.dumps(todos, ensure_ascii=False, default=str)})"
        )

        # Source (c): the pending evaluated intake.
        intake_blocked = [
            todo
            for todo in todos
            if str(todo.get("intake_id") or "") == "intake-hf262-1"
            or "intake-hf262-1" in json.dumps(todo, ensure_ascii=False, default=str)
        ]
        assert intake_blocked, (
            "the inbox must cover the pending evaluated intakes (todos: "
            f"{json.dumps(todos, ensure_ascii=False, default=str)})"
        )
    finally:
        stop_serve(proc)


def _seed_evaluated_intake(home: Path, intake_id: str) -> None:
    """A pending ``evaluated`` intake row in the service plane.

    The row is written with the PRODUCTION schema (tracks/supervisor/db.py
    ``intakes``): the table is created by the serve process itself, so the
    fixture only inserts -- it never declares its own table shape.
    """
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(intakes)").fetchall()
        ]
        if not columns:
            conn.execute(
                "CREATE TABLE intakes ("
                " intake_id TEXT PRIMARY KEY, project_id TEXT, source TEXT, content TEXT,"
                " issue INTEGER NULL, origin_entry TEXT NULL, status TEXT,"
                " report_json TEXT NULL, decision TEXT NULL, decided_at TEXT NULL,"
                " created_at TEXT, updated_at TEXT)"
            )
            columns = [
                row[1]
                for row in conn.execute("PRAGMA table_info(intakes)").fetchall()
            ]
        values = {
            "intake_id": intake_id,
            "project_id": "proj-hf262-todos",
            "source": "issue",
            "content": "fixture evaluated intake",
            "issue": 4242,
            "origin_entry": None,
            "status": "evaluated",
            "report_json": json.dumps({"title": "fixture evaluated intake"}),
            "decision": None,
            "decided_at": None,
            "created_at": _TS,
            "updated_at": _TS,
        }
        conn.execute(
            "INSERT INTO intakes ({}) VALUES ({})".format(
                ", ".join(columns), ", ".join("?" for _ in columns)
            ),
            [values.get(column) for column in columns],
        )
        conn.commit()
    finally:
        conn.close()


# AC-FR0340-01@v0.11 TRACKS-TRACE backlog title never ULID
def test_backlog_title_never_ulid(tmp_path: Path):
    """AC-FR0340-01: the backlog sidebar entry shows a READABLE title and
    NEVER a ULID -- the fallback chain ends in the localized placeholder.

    Contract: interfaces §1g item 4, §4c outlet 5.

    Outlet: ``GET /api/projects/{pid}/releases`` over a real serve -- the
    backlog group's ``display_title``.
    """
    proc, base, cookie, home, repo = _start_serve(tmp_path)
    try:
        _register(home, repo, "proj-hf262-backlog", "v0.8")
        entry_id = "01HQF262BACKLOGENTRY0000000"
        untitled_id = "01HQF262BACKLOGUNTITLED000"
        _append(
            repo,
            entry_id,
            "v0.8",
            "backlog.recorded",
            {
                "reason": "active_run",
                "source": "text",
                "content": "修复发布门禁的凭据回退",
                "intake_id": None,
                "title": "修复发布门禁的凭据回退",
            },
        )
        _append(
            repo,
            untitled_id,
            "v0.8",
            "backlog.recorded",
            {"reason": "active_run", "source": "text", "content": "", "intake_id": None},
        )

        payload = _get(base, cookie, "/api/projects/proj-hf262-backlog/releases")
        entries = (payload.get("backlog") or {}).get("entries") or []
        assert entries, "the backlog group must carry the recorded entries"
        by_id = {row["entry_id"]: row for row in entries}

        titled = by_id[entry_id]
        title = str(titled.get("display_title") or titled.get("title") or "")
        assert title, "the backlog entry must carry a display title"
        assert entry_id not in title, (
            f"the backlog title must never render the ULID (got {title!r})"
        )

        # The untitled entry falls back to the localized placeholder --
        # never to its ULID.
        fallback = by_id[untitled_id]
        fallback_title = str(
            fallback.get("display_title") or fallback.get("title") or ""
        )
        assert fallback_title, (
            "the untitled backlog entry must still show a localized placeholder"
        )
        assert untitled_id not in fallback_title, (
            f"the fallback title must never render the ULID (got {fallback_title!r})"
        )
    finally:
        stop_serve(proc)


# AC-FR0307-01@v0.9 TRACKS-TRACE todos items carry context and action ref
def test_todos_items_carry_context_and_action_ref(tmp_path: Path):
    """AC-FR0307-01: every legitimate human decision appears in the inbox
    with its reason, its context and a jump entry.

    Contract: interfaces §1g item 5 (the list item field set
    ``{version, title, stage, kind_label, summary/context, updated_ts,
    action_ref}``), §4c outlet 6.

    Outlet: ``GET /api/todos`` over a real serve.
    """
    proc, base, cookie, home, repo = _start_serve(tmp_path)
    try:
        _register(home, repo, "proj-hf262-todo-ctx", "v0.8")
        _append(
            repo, "RUN-HF262-TODO-CTX", "v0.8", "story.requested", {"raw_chars": 1}
        )
        _append(
            repo, "RUN-HF262-TODO-CTX", "v0.8", "stage.entered", {"stage": "M-RELEASE"}
        )
        _append(
            repo,
            "RUN-HF262-TODO-CTX",
            "v0.8",
            "release.previewed",
            {"preview_digest": "sha256:abc", "candidate_sha": "c" * 40},
        )

        todos = _get(base, cookie, "/api/todos")
        mine = [todo for todo in todos if todo.get("run_id") == "RUN-HF262-TODO-CTX"]
        assert mine, "a legitimate human decision must surface as a todo"
        first = mine[0]
        for field in ("version", "stage", "kind_label", "action_ref"):
            assert first.get(field), (
                f"every inbox item must carry the {field!r} field (got {first!r})"
            )
        assert first.get("context") or first.get("summary"), (
            f"every inbox item must carry its context (got {first!r})"
        )
        assert str(first["action_ref"]).startswith("/"), (
            f"the action_ref must be a navigable path (got {first['action_ref']!r})"
        )
    finally:
        stop_serve(proc)
