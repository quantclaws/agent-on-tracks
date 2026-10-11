"""T-005 round-2 RED: repair pins for the three diagnosed defects.

Devon-owned unit RED for the T-005 repair batch (DIAGNOSE seq 11461 /
GREEN_GATE seq 11454; the failing integration anchors
test_mstart_gate.py::test_develop_creates_branch_run_title,
test_nondevelop_actions_no_run_branch and the gate-endpoint set routed
three product defects back). The failing nodes pin the contracts the next
GREEN must deliver — one node per diagnosed defect:

- version derivation (interfaces §1g.1.d, IF-MSTART-001 /
  AC-FR0339-01): ``_next_release_version`` must stay monotone across
  majors — a project whose greatest existing release is ``v1.0`` derives
  ``v2.0`` (``group(1)`` max → ``v{max+1}.0``), never today's backwards
  ``v0.1``; the v0.x shape keeps the greatest-minor+1 semantics already
  pinned green by the T-002 lineage (``v0.2`` → ``v0.3``), and a project
  without any release keeps ``v0.1``;

- the decide_intake 200 payload (interfaces §1g.1a / §2b #40,
  IF-MSTART-001 / AC-FR0339-01): the develop-over-active-run terminal
  answers 200 with ``{outcome: backlog_recorded, active_run}`` — the
  response must carry the ``active_run`` key the service outcome already
  provides, not just the outcome token;

- the overview projection (IF-MSTART-001 / AC-FR0339-03 observability
  half): ``project_overview`` lists only real runs — run-less rows with
  an empty version (the same ``_registry_versions`` phantom criterion)
  and backlog-terminal rows never present as runs, so the nondevelop
  exits (no run, no branch) stay invisible as runs.

M-IMPL RED discipline: every defect is present in the product tree, so
each node fails as a plain AssertionError against the documented
contract — no stubs, no assembly errors, no ``pytest.fail``. Nothing here
mocks the system under test: the fixtures seed the real service.db
(projects/schedule/intakes/sessions) and the real per-project tracks.db
through ``tracks.store.Store`` / ``ServiceDB``, the handler runs through
its documented request seam with a real issued session (CSRF +
Idempotency-Key), and the projection reads the seeded stores. Existing
test files are untouched (round constraint: 不得改 tests/ — this round
only adds this file).

AC: FR-0339 / NFR-0157 — TRACKS-TRACE IF-MSTART-001, IF-CMDSVC-001.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.executor.helpers import git
from tracks.server import api_command, auth
from tracks.server.projections import project_overview
from tracks.store import Store
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.service import CommandService, _next_release_version

_PID = "proj-1"
_ACTOR = "local-user"
_TS = "2026-10-12T00:00:00+00:00"


# -- helpers: real storage seeded through the documented contract ---------------


def _service_rows(home: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=rw", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _seed_project(home: Path, repo: Path, version: str = "v0.2") -> None:
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?,?,?,?,?)",
        (_PID, str(repo), version, _ACTOR, _TS),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schedule (id, active_run, queue_json)"
        " VALUES (1, ?, ?)",
        ("run-live", json.dumps([])),
    )
    conn.commit()
    conn.close()


def _seed_intake(home: Path, intake_id: str) -> None:
    report = {
        "feasibility": {"verdict": "feasible", "blockers": []},
        "duplicates": {"found": False, "items": []},
        "conflicts": {"found": False, "pairs": []},
        "title": "Scribe 建议标题",
        "input_form": "raw",
        "summary": "ok",
    }
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO intakes (intake_id, project_id, source, content, issue,"
        " origin_entry, status, report_json, decision, decided_at, created_at,"
        " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            intake_id, _PID, "text", "活跃期的一段输入", None, None,
            "evaluated", json.dumps(report, ensure_ascii=False), None, None,
            _TS, _TS,
        ),
    )
    conn.commit()
    conn.close()


def _seed_run(repo: Path, run_id: str, events: list, version: str) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        for event_type, payload in events:
            store.append(run_id, version, event_type, payload)
    finally:
        store.close()


class _Headers:
    """Case-insensitive header mapping (the handler-seam .get contract)."""

    def __init__(self, raw: dict) -> None:
        self._raw = {str(key).lower(): value for key, value in (raw or {}).items()}

    def get(self, name: str, default: Any = None) -> Any:
        return self._raw.get(str(name).lower(), default)


class _Request:
    """Duck-typed request exposing exactly the api_command handler seam."""

    def __init__(self, home: Path, service: Any, repo: Path, *, body: Any,
                 path_params: dict | None = None) -> None:
        conn = sqlite3.connect(home / "service.db")
        try:
            issued = auth.issue_session(conn, _ACTOR)
        finally:
            conn.close()
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                home=home, service=service, permitted_repos=[repo]
            )
        )
        self.path_params = dict(path_params or {})
        self.cookies = {"trac_session": issued.token}
        self.headers = _Headers(
            {"X-Trac-CSRF": issued.csrf_token, "Idempotency-Key": "idem-repair-1"}
        )
        self._body = body

    async def json(self) -> Any:
        return self._body


# -- defect 1: version derivation stays monotone across majors ------------------


# AC-FR0339-01@v0.11 TRACKS-TRACE IF-MSTART-001 v1.0 derives v2.0, v0.x keeps minor+1
def test_next_release_version_stays_monotone_across_majors(tmp_path: Path):
    majors = tmp_path / "majors"
    majors.mkdir(parents=True, exist_ok=True)
    repo = git_repo(majors)
    git(repo, "branch", "releases/v1.0")
    assert _next_release_version(repo) == "v2.0", (
        "the next release derives from the greatest existing release major"
        " (group(1) max, v{max+1}.0): a project at v1.0 must never derive the"
        " backwards v0.1 (§1g.1.d; DIAGNOSE seq 11461 item 1)"
    )
    from_dirs = tmp_path / "dirs"
    from_dirs.mkdir(parents=True, exist_ok=True)
    repo_dirs = git_repo(from_dirs)
    (paths.projects_dir(paths.tracks_home(repo_dirs)) / "v1.0").mkdir(parents=True)
    assert _next_release_version(repo_dirs) == "v2.0", (
        "versioned project directories feed the same derivation as release"
        " branches (§1g.1.d) — the v1.0 directory must also derive v2.0"
    )
    minors = tmp_path / "minors"
    minors.mkdir(parents=True, exist_ok=True)
    repo_x = git_repo(minors)
    git(repo_x, "branch", "releases/v0.2")
    assert _next_release_version(repo_x) == "v0.3", (
        "the v0.x shape keeps the greatest-minor+1 semantics pinned green by"
        " the T-002 lineage (v0.2 → v0.3); the major fix must not regress it"
    )
    bare = tmp_path / "bare"
    bare.mkdir(parents=True, exist_ok=True)
    assert _next_release_version(git_repo(bare)) == "v0.1", (
        "a project without any release keeps the v0.1 first-release default"
    )


# -- defect 2: the 200 backlog_recorded payload carries active_run --------------


# AC-FR0339-01@v0.11 TRACKS-TRACE IF-MSTART-001 backlog_recorded 200 carries active_run
def test_decide_intake_backlog_response_carries_active_run(tmp_path: Path):
    repo = git_repo(tmp_path)
    _seed_run(
        repo,
        "run-live",
        [
            ("story.requested", {"raw_chars": 4}),
            ("stage.entered", {"stage": "M-IMPL"}),
        ],
        "v0.2",
    )
    home = tmp_path / "home"
    ServiceDB(home)
    _seed_project(home, repo)
    _seed_intake(home, "I-active")
    service = CommandService(home, ServiceDB(home), SimpleNamespace())
    request = _Request(
        home,
        service,
        repo,
        body={"action": "develop"},
        path_params={"intake_id": "I-active"},
    )
    response = asyncio.run(api_command.decide_intake(request))
    assert response.status_code == 200, (
        "the develop-over-active-run terminal answers 200 (§2b #40); got"
        f" {response.status_code} with {response.payload!r}"
    )
    payload = response.payload
    assert isinstance(payload, dict) and payload.get("outcome") == "backlog_recorded"
    assert payload.get("active_run") == "run-live", (
        "the 200 payload must carry the active_run key (§1g.1a: {outcome:"
        " backlog_recorded, active_run}) so the UI can name the run holding"
        f" the lock; got {payload!r} (DIAGNOSE seq 11461 item 2)"
    )


# -- defect 3: project_overview lists only real runs ----------------------------


# AC-FR0339-03@v0.11 TRACKS-TRACE IF-MSTART-001 phantom rows never present as runs
def test_project_overview_skips_phantom_and_backlog_rows(tmp_path: Path):
    repo = git_repo(tmp_path)
    _seed_run(
        repo,
        "run-live",
        [
            ("story.requested", {"raw_chars": 4}),
            ("stage.entered", {"stage": "M-STORY"}),
        ],
        "v0.2",
    )
    _seed_run(
        repo,
        "01BACKLOGBACKLOGBACKLOGBACKLOG00",
        [("backlog.recorded", {"reason": "active_run"})],
        "v0.1",
    )
    _seed_run(
        repo,
        "01PHANTOMPHANTOMPHANTOMPHANTOM00",
        [("backlog.recorded", {"reason": "active_run"})],
        "",
    )
    home = tmp_path / "home"
    ServiceDB(home)
    _seed_project(home, repo)
    overview = project_overview(home)
    listed = sorted(str(run["run_id"]) for run in overview["runs"])
    assert listed == ["run-live"], (
        "the overview lists only real runs: the run-less backlog-terminal row"
        " (status=backlog) and the empty-version phantom row (the same"
        " criterion _registry_versions uses) never present as runs — the"
        " nondevelop exits keep their no-run observability (§1e, DIAGNOSE"
        f" seq 11461 item 3); got {listed}"
    )
