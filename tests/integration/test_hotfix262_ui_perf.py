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

The observation is the HTTP / projection read face (interfaces §4c outlets
1, 2, 3, 5, 6) -- the projections the server serves.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tracks import paths
from tracks.store import Store

pytestmark = pytest.mark.integration

_AC_CHAIN_BUDGET_MS = 5.0
_SLIM_BUDGET_BYTES = 50 * 1024
_SLIM_P95_BUDGET_MS = 500.0


def _new_host(tmp_path: Path) -> tuple[Path, Store]:
    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "HF262 Human"], cwd=repo, check=True)
    (repo / "README.md").write_text("hotfix-262 host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True
    )
    return repo, Store(paths.tracks_home(repo))


def _register_project(home: Path, repo: Path, project_id: str, version: str) -> None:
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects "
            "(project_id TEXT, repo_path TEXT, version TEXT, owner TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), version, "local-user", "2026-10-27T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_0_ac_run(repo: Path, store: Store, run_id: str, count: int = 5000) -> None:
    """A 0-AC run with a bounded number of events (the ac-chain short-circuit
    must not replay them).

    The event volume is chosen so that a NON-short-circuiting read face is
    measurably over the contract's 5 ms budget, while a short-circuiting one
    stays orders of magnitude below it.
    """
    store.append(run_id, "v0.8", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-IMPL"})
    for index in range(count):
        store.append(
            run_id,
            "v0.8",
            "task.started",
            {"task_id": f"T-{index:05d}", "task": {"task_id": f"T-{index:05d}"}},
        )


# AC-FR0335-02@v0.11 TRACKS-TRACE slim timeline payload bounded
def test_slim_timeline_payload_bounded(tmp_path: Path):
    """AC-FR0335-02: the slim timeline payload is bounded -- under 50 KB,
    with a p95 under 500 ms -- and carries no full evidence blob.

    Contract: interfaces §1g item 2 (the slim tightening), §4c outlet 2.
    """
    from tracks.server.projections_timeline import project_timeline

    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-SLIM-1"
    try:
        # A run carrying a LARGE evidence payload per event: the slim face
        # must not carry it through.
        for index in range(50):
            store.append(
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
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-TEST"})
    finally:
        store.close()

    home = paths.tracks_home(repo)
    _register_project(home, repo, "proj-hf262-slim", "v0.8")

    timeline = project_timeline(home, run_id, slim=True)
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

    # The p95 of the read face is under 500 ms.
    samples = []
    for _ in range(20):
        started = time.perf_counter()
        project_timeline(home, run_id, slim=True)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
    assert p95 < _SLIM_P95_BUDGET_MS, (
        f"the slim timeline p95 must stay under {_SLIM_P95_BUDGET_MS} ms (got {p95})"
    )


# AC-NFR0150-02@v0.9 TRACKS-TRACE ac-chain short circuits empty registry
def test_ac_chain_short_circuits_empty_registry(tmp_path: Path):
    """AC-NFR0150-02: the ac-chain read face short-circuits on an empty AC
    registry -- it returns an empty chain and answers in under 5 ms, without
    replaying the run's events.

    Contract: interfaces §1g item 1 (the ``if not acs: return []`` guard
    precedes ``_run_events`` / ``project_state``), §4c outlet 1.
    """
    from tracks.server.projections_run import project_ac_chain

    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-ACCHAIN-1"
    try:
        _seed_0_ac_run(repo, store, run_id)
    finally:
        store.close()

    home = paths.tracks_home(repo)
    _register_project(home, repo, "proj-hf262-acchain", "v0.8")

    # The registry is empty: the version has no acceptance.md, so the
    # ac-chain has no AC to report.
    version_dir = paths.version_dir(home, "v0.8")
    assert not (version_dir / "acceptance.md").exists(), (
        "the 0-AC fixture must have no acceptance document"
    )

    started = time.perf_counter()
    chain = project_ac_chain(home, run_id)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert chain == [], (
        f"the 0-AC run's ac-chain must be empty (got {chain!r})"
    )
    assert elapsed_ms < _AC_CHAIN_BUDGET_MS, (
        f"the 0-AC ac-chain must answer under {_AC_CHAIN_BUDGET_MS} ms "
        f"(got {elapsed_ms}) -- the empty-registry short-circuit must precede "
        "the event replay"
    )


# AC-FR0333-01@v0.11 TRACKS-TRACE unified todos inbox coverage
def test_unified_todos_inbox_coverage(tmp_path: Path):
    """AC-FR0333-01: the Todos inbox is UNIFIED -- it covers the awaiting
    runs, the pending attention blocks AND the pending evaluated intakes.

    Contract: interfaces §1g item 5 (the three sources of the unified
    inbox), §4c outlet 6.
    """
    from tracks.server.projections_run import project_todos

    repo, store = _new_host(tmp_path)
    try:
        # Source (a): a run awaiting a human decision in AWAITING_RELEASE.
        store.append(
            "RUN-HF262-TODO-A",
            "v0.8",
            "stage.entered",
            {"stage": "M-RELEASE"},
        )
        store.append(
            "RUN-HF262-TODO-A",
            "v0.8",
            "release.previewed",
            {"preview_digest": "sha256:abc", "candidate_sha": "c" * 40},
        )
        # Source (b): a run with an unresolved attention block.
        store.append(
            "RUN-HF262-TODO-B",
            "v0.8",
            "stage.entered",
            {"stage": "M-REQ-APPROVAL"},
        )
        store.append(
            "RUN-HF262-TODO-B",
            "v0.8",
            "attention.required",
            {
                "area": "issue_creation",
                "reason": "missing_token",
                "stage": "M-REQ-APPROVAL",
            },
        )
    finally:
        store.close()

    home = paths.tracks_home(repo)
    _register_project(home, repo, "proj-hf262-todos", "v0.8")
    _seed_evaluated_intake(home, "intake-hf262-1")

    todos = project_todos(home)
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
        if str(todo.get("kind") or "") == "attention"
        or "attention" in str(todo.get("kind") or "")
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

    # Every item keeps the navigable identity the UI binds on.
    for todo in todos:
        assert todo.get("run_id") or todo.get("intake_id"), (
            f"every inbox item must carry a navigable identity (got {todo!r})"
        )


def _seed_evaluated_intake(home: Path, intake_id: str) -> None:
    """A pending ``evaluated`` intake row in the service plane."""
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS intakes ("
            "intake_id TEXT, source TEXT, status TEXT, version TEXT, "
            "report_json TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO intakes VALUES (?,?,?,?,?,?)",
            (
                intake_id,
                "issue",
                "evaluated",
                "v0.8",
                json.dumps({"title": "fixture evaluated intake"}),
                "2026-10-27T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


# AC-FR0340-01@v0.11 TRACKS-TRACE backlog title never ULID
def test_backlog_title_never_ulid(tmp_path: Path):
    """AC-FR0340-01: the backlog sidebar entry shows a READABLE title and
    NEVER a ULID -- the fallback chain ends in the localized placeholder.

    Contract: interfaces §1g item 4 (the title join, the fallback chain and
    the "never render a ULID" rule), §4c outlet 5.
    """
    from tracks.server.projections_run import _backlog_group

    repo, store = _new_host(tmp_path)
    entry_id = "01HQF262BACKLOGENTRY0000000"
    try:
        store.append(
            entry_id,
            "v0.8",
            "backlog.recorded",
            {"reason": "active_run", "source": "text", "content": "修复发布门禁", "intake_id": None},
        )
    finally:
        store.close()

    group = _backlog_group(str(repo))
    entries = group.get("entries") or []
    assert entries, "the backlog group must carry the recorded entry"
    entry = entries[-1]

    title = str(entry.get("display_title") or entry.get("title") or "")
    assert title, "the backlog entry must carry a display title"
    assert entry_id not in title, (
        f"the backlog title must never render the ULID (got {title!r})"
    )
    assert not title.startswith("01"), (
        f"the backlog title must never render a ULID (got {title!r})"
    )


# AC-FR0307-01@v0.9 TRACKS-TRACE todos items carry context and action ref
def test_todos_items_carry_context_and_action_ref(tmp_path: Path):
    """AC-FR0307-01: every legitimate human decision appears in the inbox
    with its reason, its context and a jump entry.

    Contract: interfaces §1g item 5 (the list item field set
    ``{version, title, stage, kind_label, summary/context, updated_ts,
    action_ref}``), §4c outlet 6.
    """
    from tracks.server.projections_run import project_todos

    repo, store = _new_host(tmp_path)
    try:
        store.append(
            "RUN-HF262-TODO-CTX",
            "v0.8",
            "stage.entered",
            {"stage": "M-RELEASE"},
        )
        store.append(
            "RUN-HF262-TODO-CTX",
            "v0.8",
            "release.previewed",
            {"preview_digest": "sha256:abc", "candidate_sha": "c" * 40},
        )
    finally:
        store.close()

    home = paths.tracks_home(repo)
    _register_project(home, repo, "proj-hf262-todo-ctx", "v0.8")

    todos = project_todos(home)
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
