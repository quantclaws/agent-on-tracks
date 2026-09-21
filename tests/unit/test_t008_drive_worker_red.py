"""T-008 RED: structured drive step and claimed-worker boundary execution
(IF-DRIVE-001).

Devon-owned unit RED for the T-008 delivery slice (architecture rows 16 + 19;
interfaces §1d): ``tracks/executor/drive.py`` (drive_once five-state single
step) and ``tracks/supervisor/worker_main.py`` (claimed ``drive_run`` execution
inside the isolated worker subprocess). The failing nodes pin the IF-DRIVE-001
contracts this task must deliver:

- terminal: a run already in a terminal state is reported as
  ``DriveResult.kind == "terminal"`` with the terminal_state surfaced in
  ``detail`` — a cancelled run (abandon, SM-01.21) is never re-driven and
  never presented as success (AC-FR0313-01/02);
- await_human: a run parked at an original-contract human gate halts as
  ``kind == "await_human"`` and repeated drives keep waiting — nothing is
  issued past the gate (AC-FR0297-02);
- failed: an undrivable run fails closed as ``kind == "failed"`` with
  ``failure_class == "unrecoverable"`` and ``wait is None`` — never classified
  as an external wait or a human gate, so the supervisor cannot enter a
  wait/retry loop on it (AC-FR0313-03);
- the five-state kind closed set (interfaces §1d) holds for every
  deterministic boundary above (AC-FR0297-01);
- worker_main: a claimed ``drive_run`` command is executed against the
  command's own run through the real service.db, committing the boundary
  outcome through the fencing CAS — an await_human boundary completes the
  command without advancing the run; an unknown run fails the command
  unrecoverably with no ``wait.entered`` / ``command.requeued`` event (no fake
  success, no wait masquerade, no retry loop) (AC-FR0297-01/02,
  AC-FR0313-03).

M-IMPL RED discipline: the scaffold bodies of drive.py / worker_main.py still
raise their IF-DRIVE-001 stub token; every failing node guards that stub into a
real ``AssertionError`` (``raise ... from None``) so the records classify as
assertion_failure — no stub_token, no assembly errors, no ``pytest.fail``
(whose ``Failed:`` line classifies unclassified). Nothing here mocks the
system under test: run-plane state is seeded into the real per-project
tracks.db via ``tracks.store.Store`` and service-plane state into the real
service.db via ``tracks.supervisor.db.ServiceDB``; the worker entry runs
in-process against those real stores.

AC: FR-0297/FR-0313 — TRACKS-TRACE IF-DRIVE-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths
from tracks.executor import drive as drive_mod
from tracks.store import Store
from tracks.supervisor import worker_main
from tracks.supervisor.db import ServiceDB

_VERSION = "v0.9"
_ADVANCING_EVENTS = ("command.issued", "stage.entered")


# -- run-plane seeding (real tracks.db through the production Store) -----------


def _seed_repo(tmp_path: Path, run_id: str, events: list) -> tuple[Path, Store]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = git_repo(tmp_path)
    store = Store(paths.tracks_home(repo))
    for event_type, payload in events:
        store.append(run_id, _VERSION, event_type, payload)
    return repo, store


def _cancelled_run_events() -> list:
    """Abandon terminal (SM-01.21): run.completed(terminal_state=cancelled)."""
    return [
        ("story.requested", {"raw_chars": 1}),
        ("stage.entered", {"stage": "M-STORY"}),
        ("run.completed", {"terminal_state": "cancelled", "reason": "human abandon"}),
    ]


def _human_gate_events() -> list:
    """preview.generated parks M-REQ-APPROVAL at AWAIT_HUMAN (SM-05.2)."""
    return [
        ("story.requested", {"raw_chars": 1}),
        ("stage.entered", {"stage": "M-REQ-APPROVAL"}),
        ("preview.generated", {"digest": "d" * 64}),
    ]


def _advancing(store: Store, run_id: str) -> list:
    return [e.type for e in store.events(run_id) if e.type in _ADVANCING_EVENTS]


def _drive(repo: Path, run_id: str):
    """Call drive_once; the IF-DRIVE-001 stub becomes a behavioral assertion."""
    try:
        return drive_mod.drive_once(repo, run_id, config=drive_mod.DriveConfig())
    except NotImplementedError as exc:
        raise AssertionError(
            f"IF-DRIVE-001 drive_once is still a stub: {exc} — one structured "
            "decide->issue->execute step must report the drive boundary"
        ) from None


# -- drive_once boundary classification (interfaces §1d) ------------------------


# AC-FR0313-01@v0.9 TRACKS-TRACE IF-DRIVE-001 cancelled terminal is reported
# AC-FR0313-02@v0.9 TRACKS-TRACE IF-DRIVE-001 abandon is never re-driven
def test_drive_once_cancelled_terminal_not_advanced(tmp_path: Path):
    repo, store = _seed_repo(tmp_path, "R1", _cancelled_run_events())
    before = _advancing(store, "R1")
    result = _drive(repo, "R1")
    assert result.kind == "terminal", (
        "a run completed with terminal_state=cancelled must report kind=terminal "
        "(interfaces §1d), not continue/await/failed"
    )
    assert result.run_id == "R1"
    assert "cancelled" in result.detail.lower(), (
        "the terminal DriveResult must surface terminal_state=cancelled so the "
        "abandon is not presented as success (AC-FR0313-02)"
    )
    assert result.wait is None
    assert result.failure is None
    assert _advancing(store, "R1") == before, (
        "a terminal run must not be re-driven (no new command.issued/stage.entered)"
    )


# AC-FR0297-02@v0.9 TRACKS-TRACE IF-DRIVE-001 human gate halts as await_human
def test_drive_once_human_gate_returns_await_human(tmp_path: Path):
    repo, store = _seed_repo(tmp_path, "R1", _human_gate_events())
    result = _drive(repo, "R1")
    assert result.kind == "await_human", (
        "a run parked at an original-contract human gate must halt as await_human"
    )
    assert result.run_id == "R1"
    assert result.stage == "M-REQ-APPROVAL"
    assert result.wait is None
    assert result.failure is None
    assert _advancing(store, "R1") == ["stage.entered"], (
        "nothing may be issued past the human gate (FR-0297-02)"
    )
    again = _drive(repo, "R1")
    assert again.kind == "await_human"
    assert _advancing(store, "R1") == ["stage.entered"], (
        "repeated drives must keep waiting at the gate instead of advancing"
    )


# AC-FR0313-03@v0.9 TRACKS-TRACE IF-DRIVE-001 undrivable run fails unrecoverable
def test_drive_once_unknown_run_fails_unrecoverable(tmp_path: Path):
    repo = git_repo(tmp_path)
    result = _drive(repo, "R-missing")
    assert result.kind == "failed", (
        "an undrivable run must fail closed instead of reporting a boundary it "
        "did not reach"
    )
    assert result.run_id == "R-missing"
    assert isinstance(result.failure, dict)
    assert result.failure.get("failure_class") == "unrecoverable", (
        "the drive failure must be unrecoverable, distinct from a recoverable "
        "external wait (AC-FR0313-03)"
    )
    assert result.failure.get("reason")
    assert result.wait is None, (
        "an unrecoverable failure must not masquerade as an external wait"
    )


# AC-FR0297-01@v0.9 TRACKS-TRACE IF-DRIVE-001 drive kinds stay in the closed set
def test_drive_once_kinds_stay_in_closed_set(tmp_path: Path):
    allowed = {"continue", "await_human", "await_external", "terminal", "failed"}
    gate_repo, _ = _seed_repo(tmp_path / "gate", "R1", _human_gate_events())
    done_repo, _ = _seed_repo(tmp_path / "done", "R1", _cancelled_run_events())
    bare_repo, _ = _seed_repo(tmp_path / "bare", "R-missing", [])
    kinds = {
        _drive(gate_repo, "R1").kind,
        _drive(done_repo, "R1").kind,
        _drive(bare_repo, "R-missing").kind,
    }
    assert kinds <= allowed, (
        f"drive_once produced out-of-contract kinds: {sorted(kinds - allowed)}"
    )


# -- worker_main: claimed drive_run execution (interfaces §1i, §2a) ------------


def _service_db(home: Path) -> ServiceDB:
    home.mkdir(parents=True, exist_ok=True)
    return ServiceDB(home)


def _raw(home: Path, sql: str, params: tuple = ()) -> None:
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        conn.execute(sql, params)
        conn.commit()


def _seed_project(home: Path, repo: Path, project_id: str) -> None:
    _raw(
        home,
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, str(repo), _VERSION, "human", "2026-09-21T00:00:00+00:00"),
    )


def _seed_claimed_drive(db: ServiceDB, home: Path, run_id: str, command_id: str) -> None:
    """Seed a claimed system drive_run command bound to generation 1."""
    db.register_command(
        {
            "command_id": command_id,
            "kind": "drive_run",
            "params_json": json.dumps({"run_id": run_id}),
            "params_digest": "0" * 64,
            "idempotency_key": f"idem-{command_id}",
            "actor": "supervisor",
            "actor_class": "system",
            "surface": "internal",
            "project_id": "P1",
            "run_id": run_id,
        }
    )
    assert db.claim_command(command_id, "worker-w1", 1) is True, (
        "fixture: the drive_run command must be claimable from accepted"
    )
    _raw(
        home,
        "INSERT INTO leases (run_id, worker_id, generation, acquired_at,"
        " expires_at) VALUES (?, ?, ?, ?, ?)",
        (
            run_id,
            "worker-w1",
            1,
            "2026-09-21T00:00:00+00:00",
            "2026-09-21T00:01:00+00:00",
        ),
    )


def _run_worker(monkeypatch, home: Path, command_id: str) -> int:
    """Run the worker entry; the IF-DRIVE-001 stub becomes an assertion."""
    monkeypatch.setenv("TRAC_SERVE_HOME", str(home))
    try:
        return worker_main.main(["--command-id", command_id])
    except NotImplementedError as exc:
        raise AssertionError(
            f"IF-DRIVE-001 worker_main is still a stub: {exc} — a claimed "
            "drive_run command must be executed to its drive boundary"
        ) from None


# AC-FR0297-01@v0.9 TRACKS-TRACE IF-DRIVE-001 claimed worker commits at the gate
def test_worker_main_commits_await_human_boundary(tmp_path: Path, monkeypatch):
    repo, store = _seed_repo(tmp_path, "R1", _human_gate_events())
    home = tmp_path / "service-home"
    db = _service_db(home)
    _seed_project(home, repo, "P1")
    _seed_claimed_drive(db, home, "R1", "C1")
    code = _run_worker(monkeypatch, home, "C1")
    assert code == 0
    row = db.get_command("C1")
    assert row["status"] == "completed", (
        "the worker must commit the claimed command's boundary outcome"
    )
    result = json.loads(row["result_json"])
    assert result.get("kind") == "await_human"
    assert store.state("R1").awaiting == "approval", (
        "the worker must not cross the human gate it stopped at"
    )
    assert _advancing(store, "R1") == ["stage.entered"]


# AC-FR0313-03@v0.9 TRACKS-TRACE IF-DRIVE-001 worker failure is terminal, no wait
def test_worker_main_unknown_run_fails_without_wait_or_retry(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    home = tmp_path / "service-home"
    db = _service_db(home)
    _seed_project(home, repo, "P1")
    _seed_claimed_drive(db, home, "R-missing", "C1")
    code = _run_worker(monkeypatch, home, "C1")
    assert isinstance(code, int)
    row = db.get_command("C1")
    assert row["status"] == "failed", "an undrivable run must fail its command"
    failure = json.loads(row["result_json"])
    assert failure.get("failure_class") == "unrecoverable", (
        "the command failure must be classified unrecoverable (AC-FR0313-03)"
    )
    events = [event["type"] for event in db.read_events()]
    assert "wait.entered" not in events, (
        "an unrecoverable failure must not become an external wait"
    )
    assert "command.requeued" not in events, (
        "a failed command must not enter a retry loop"
    )
