"""T-008 RED addendum: ac-chain read face must not run the full run-detail precheck.

The interface §1g.1 contract puts ``GET /api/runs/{id}/ac-chain`` on the
``_chain_rows`` short-circuit path: 0-AC runs answer <5ms and never pay for
the full run-detail fold (``_executions`` replays every ``task.started`` and
is quadratic on a large stream). The handler may still distinguish a real
run from an unknown one, but only through a cheap existence check — never
through ``project_run_detail`` (the full replay precheck).

These are handler-level RED nodes: the current handler still gates on
``projections.project_run_detail``, so each node fails as a real
``AssertionError`` rather than a collection/import error. Fixtures write the
real service/tracks stores; no product module is mocked except the forbidden
full-detail seam, whose call *is* the failure.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import paths
from tracks.server import api_query, projections, projections_core
from tracks.server.redaction import SecretRedactor
from tracks.store.store import Store
from tracks.supervisor import db as sdb

_VERSION = "v0.12"
_PROJECT_ID = "proj-1"


# AC-NFR0150-02@v0.9 TRACKS-TRACE a warm cache cannot hide a forbidden event replay
def test_empty_registry_never_reads_event_stream(tmp_path, monkeypatch):
    def forbidden_read(*args):
        raise AssertionError("an empty AC registry must not read or replay run events")

    monkeypatch.setattr(projections_core, "_run_events", forbidden_read)
    assert projections_core._chain_rows(str(tmp_path), "v0.8", "RUN") == []


def _write_acceptance(repo: Path, ac_id: str) -> None:
    target = repo / ".tracks" / "projects" / _VERSION / "acceptance.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"## NFR\n\n### {ac_id}\n\n- criterion {ac_id}\n", encoding="utf-8")


def _write_tasks(repo: Path, ac_id: str) -> None:
    target = repo / ".tracks" / "projects" / _VERSION / "tasks.json"
    target.write_text(
        json.dumps(
            {
                "schema": 2,
                "tasks": [
                    {
                        "task_id": "T-1",
                        "test_tasks": [
                            {
                                "ac_id": ac_id,
                                "anchors": [
                                    "tests/integration/test_hotfix262_ui_perf.py::"
                                    "test_ac_chain_short_circuits_empty_registry"
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _seed(tmp_path: Path, run_id: str, ac_id: str | None = None) -> Path:
    repo = tmp_path / "repo"
    if ac_id is not None:
        _write_acceptance(repo, ac_id)
        _write_tasks(repo, ac_id)
    store = Store(paths.tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-IMPL"})
    finally:
        store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES (?,?,?,?,?)",
            (_PROJECT_ID, str(repo), _VERSION, "alice", "2026-10-30T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO commands (command_id, kind, params_json, params_digest,"
            " idempotency_key, actor, actor_class, surface, project_id, run_id,"
            " status, claim_generation, result_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "cmd-1", "create_run", "{}", "digest-1", "idem-1", "alice", "human",
                "http", _PROJECT_ID, run_id, "completed", 1, "{}",
                "2026-10-30T00:00:00+00:00", "2026-10-30T00:00:01+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return home


def _request(home: Path, run_id: str) -> SimpleNamespace:
    state = SimpleNamespace(home=home, redactor=SecretRedactor({}))
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        path_params={"run_id": run_id},
        query_params={},
    )


def _call(home: Path, run_id: str) -> Any:
    return asyncio.run(api_query.ac_chain(_request(home, run_id)))


def _forbidden_full_detail(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError(
        "ac-chain must not call project_run_detail (full replay precheck);"
        " a cheap existence check must gate the 404 instead"
    )


# AC-NFR0150-02@v0.9 TRACKS-TRACE IF-UIPERF-001 0-AC ac-chain skips full run detail
def test_ac_chain_zero_ac_skips_full_detail_precheck(tmp_path: Path, monkeypatch: Any):
    home = _seed(tmp_path, "run-zero-ac")
    monkeypatch.setattr(projections, "project_run_detail", _forbidden_full_detail)

    result = _call(home, "run-zero-ac")

    assert getattr(result, "status_code", None) == 200
    assert getattr(result, "payload", None) == [], (
        "a 0-AC run answers the empty chain without the full run-detail fold"
    )


# AC-NFR0150-02@v0.9 TRACKS-TRACE IF-UIPERF-001 unknown run still 404 without full detail
def test_ac_chain_unknown_run_404_without_full_detail(tmp_path: Path, monkeypatch: Any):
    home = _seed(tmp_path, "run-zero-ac")
    monkeypatch.setattr(projections, "project_run_detail", _forbidden_full_detail)

    result = _call(home, "run-ghost")

    assert getattr(result, "status_code", None) == 404
    payload = getattr(result, "payload", None)
    assert isinstance(payload, dict) and payload.get("error", {}).get("reason") == "not_found"


# AC-NFR0150-02@v0.9 TRACKS-TRACE IF-UIPERF-001 populated chain still serves after cheap precheck
def test_ac_chain_populated_run_still_serves_rows(tmp_path: Path, monkeypatch: Any):
    home = _seed(tmp_path, "run-one-ac", ac_id="AC-NFR0150-02")
    monkeypatch.setattr(projections, "project_run_detail", _forbidden_full_detail)

    result = _call(home, "run-one-ac")

    assert getattr(result, "status_code", None) == 200
    payload = getattr(result, "payload", None)
    assert isinstance(payload, list) and [row.get("ac_id") for row in payload] == [
        "AC-NFR0150-02"
    ], "the cheap existence gate must not replace the real chain projection"
