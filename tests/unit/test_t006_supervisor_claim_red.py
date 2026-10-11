"""T-006 RED: supervisor claiming & the self-drive tick (task-claimed
IF-INTAKE-001 evaluation half; served contracts IF-GATEUI-001 §1j.3 gate
claiming and IF-DRIVE-001 drive preservation).

Devon-owned unit RED for the T-006 delivery slice — ``tracks/cli/serve_cmd.py``
``_supervisor_tick`` grows exactly two claim steps (task T-006; interfaces
§1j.3 worker gate-claim face and §1f.1 SM-01 evaluation window) while the
existing drive channels stay untouched:

- gate command claiming: accepted ``record_triage`` / ``record_review`` /
  ``record_stage_approval`` / ``record_release_decision`` commands are
  claimed under the run lease (claim CAS + generation) and handed to a
  worker — the tick half of the self-drive loop (interfaces §1j.3: after a
  gate command is accepted no manual dispatch is ever needed);
- the claim set is closed: the synchronous short-write kinds
  (``submit_intake`` and siblings) are never spawned by the tick — they
  execute at acceptance (§1b execution-form boundary);
- a missed claim only delays: a gate command owned by another worker is
  never double-spawned, and once the crash-recovery requeue returns it to
  accepted the next tick claims it — nothing is lost (requeue inherits);
- gate claiming and the fresh-drive acceptance share one tick: the
  runnable run's regular drive keeps being accepted and spawned in the
  same pass that claims the gate command (interfaces §1j.3 self-drive
  closure — after the gate event lands, the next tick's ordinary drive
  advances the run, no manual dispatch in between);
- intake evaluation claiming (the task-claimed IF-INTAKE-001 half):
  ``intakes.status=submitted`` rows are taken into the evaluation window
  by the tick (SM-01 submitted -> evaluating), and rows stranded in
  ``evaluating`` past the timeout are re-dispatched through the delivered
  crash-recovery path (budget <= 3 rides ``tracks/supervisor/intake.py``);
  a fresh ``evaluating`` row is not re-driven immediately (timeout-gated,
  never a hot loop);
- the pre-existing ``_drive_accepted`` / ``_accept_fresh_drive`` channels
  keep driving accepted ``drive_run`` commands from the same tick.

RED discipline: every failing node is a plain AssertionError on the
contract above (no stub tokens to guard — the tick exists and simply lacks
the two steps; no assembly errors, no pytest.fail). The worker subprocess
boundary is observed through a recording Popen double while the claim CAS,
the lease upsert and every service.db row stay real; the Scribe evaluation
channel is observed through delegating spies over the delivered
``tracks/supervisor/intake.py`` entry points (the agent channel stays on
the unit conftest's fake backend) — nothing mocks the system under test.
The browser write-control face (intake.js / docs.js / discussions.js /
editor.js) is carried by the ui e2e channel and is not a unit surface.

AC: FR-0338 — TRACKS-TRACE IF-INTAKE-001 (the task's claimed IF for this
slice); gate wiring serves IF-GATEUI-001 (§1j.3) and drive preservation
serves IF-DRIVE-001.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tests.unit.helpers import git_repo
from tracks.cli import serve_cmd
from tracks.supervisor import intake as intake_mod
from tracks.supervisor import worker as worker_mod
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.scheduler import Scheduler
from tracks.supervisor.service import CommandService
from tracks.supervisor.worker import WorkerManager

_PID = "proj-1"
_ACTOR = "local-user"
_TS = "2026-10-11T00:00:00+00:00"
_WORKER_ID = "supervisor-test"
_EVALUATION_WINDOW = {"evaluating", "evaluated", "failed"}
_GATE_KIND_PARAMS = {
    "record_triage": {"run_id": "R-tri", "decision": "go"},
    "record_review": {"run_id": "R-rev", "action": "comment"},
    "record_stage_approval": {
        "run_id": "R-apr",
        "object": "spec",
        "expected_revision": "0" * 64,
        "decision": "approve",
    },
    "record_release_decision": {
        "run_id": "R-rel",
        "action": "proceed",
        "preview_digest": "0" * 64,
    },
}


# -- fixtures: real service plane stores, seeded through the contract --------


def _service_home(tmp_path: Path) -> Path:
    home = tmp_path / "service-home"
    ServiceDB(home)
    return home


def _q(home: Path, sql: str, params: tuple = ()) -> list:
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        return conn.execute(sql, params).fetchall()


def _project_row(home: Path, repo: Path) -> None:
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?,?,?,?,?)",
        (_PID, str(repo), "v0.11", _ACTOR, _TS),
    )
    conn.commit()
    conn.close()


def _schedule_row(home: Path, active_run: str) -> None:
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO schedule (id, active_run, queue_json) VALUES (1, ?, ?)",
        (active_run, json.dumps([])),
    )
    conn.commit()
    conn.close()


def _seed_command(
    home: Path, command_id: str, kind: str, params: dict, run_id: str | None
) -> None:
    ServiceDB(home).register_command(
        {
            "command_id": command_id,
            "kind": kind,
            "params_json": json.dumps(params),
            "params_digest": "0" * 64,
            "idempotency_key": f"idem-{command_id}",
            "actor": _ACTOR,
            "actor_class": "human",
            "surface": "web",
            "project_id": _PID,
            "run_id": run_id,
        }
    )


def _seed_intake(
    home: Path, intake_id: str, *, status: str, updated_age_s: float = 0.0
) -> None:
    stamp = datetime.fromtimestamp(
        time.time() - updated_age_s, tz=timezone.utc
    ).isoformat()
    ServiceDB(home).insert_intake(
        {
            "intake_id": intake_id,
            "project_id": _PID,
            "source": "text",
            "content": "统一入口的一段输入",
            "issue": None,
            "origin_entry": None,
            "status": status,
            "report_json": None,
            "decision": None,
            "decided_at": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
    )


def _intake(home: Path, intake_id: str) -> dict:
    return ServiceDB(home).get_intake(intake_id) or {}


# -- the worker subprocess boundary: a recording Popen double ------------------


class _FakeProc:
    """A never-exiting stand-in for one spawned worker subprocess."""

    def __init__(self, argv: list[str], pid: int = 4242) -> None:
        self.argv = list(argv)
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


class _SpawnRecorder:
    """Records every worker spawn; claim CAS and leases stay real."""

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str], env: dict | None = None) -> _FakeProc:
        self.argvs.append(list(argv))
        return _FakeProc(argv)

    def command_ids(self) -> set[str]:
        ids: set[str] = set()
        for argv in self.argvs:
            if "--command-id" in argv:
                index = argv.index("--command-id")
                if index + 1 < len(argv):
                    ids.add(argv[index + 1])
        return ids


def _tick(home: Path, monkeypatch, recorder: _SpawnRecorder) -> None:
    """One real supervisor tick with the process boundary recorded."""
    db = ServiceDB(home)
    config = SimpleNamespace(poll_interval_s=5, lease_ttl_s=30)
    scheduler = Scheduler(db)
    worker = WorkerManager(db, scheduler, config)
    service = CommandService(home, db, config)
    monkeypatch.setattr(worker_mod.subprocess, "Popen", recorder)
    serve_cmd._supervisor_tick(
        db, service, scheduler, worker, _WORKER_ID, 30, 5, serve_cmd._DriveState()
    )


# -- the Scribe channel boundary: delegating spies over delivered entry points --


def _spy_evaluation(monkeypatch) -> list[str]:
    """Record run_intake_evaluation calls; the real attempt still runs."""
    calls: list[str] = []
    real = intake_mod.run_intake_evaluation

    def _spy(home: Path, intake_id: str, *, repo: Path | None = None) -> dict:
        calls.append(str(intake_id))
        return real(home, intake_id, repo=repo)

    monkeypatch.setattr(intake_mod, "run_intake_evaluation", _spy)
    return calls


def _spy_reclaim(monkeypatch) -> list[float]:
    """Record reclaim_stale_evaluating calls; the real recovery still runs."""
    calls: list[float] = []
    real = intake_mod.reclaim_stale_evaluating

    def _spy(home: Path, *, older_than_s: float, now_ts: float | None = None) -> list:
        calls.append(float(older_than_s))
        return real(home, older_than_s=older_than_s, now_ts=now_ts)

    monkeypatch.setattr(intake_mod, "reclaim_stale_evaluating", _spy)
    return calls


def _brief(home: Path, intake_id: str) -> Path:
    return home / "intakes" / intake_id / "assignment.md"


def _eventually(check, timeout_s: float = 2.0) -> bool:
    """Poll a bounded window — sync claims pass at once, handoffs settle."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return check()


# -- gate command claiming (interfaces §1j.3, IF-GATEUI-001) -------------------


# T-006 TRACKS-TRACE IF-GATEUI-001 accepted gate commands claimed under lease
# (self-drive tick half; the drive channel keeps driving from the same tick)
def test_tick_claims_gate_commands_and_keeps_drive_channel(tmp_path, monkeypatch):
    home = _service_home(tmp_path)
    _seed_command(
        home, "C-gate", "record_triage", {"run_id": "R1", "decision": "go"}, "R1"
    )
    _seed_command(home, "C-drive", "drive_run", {"run_id": "R9"}, "R9")
    recorder = _SpawnRecorder()

    _tick(home, monkeypatch, recorder)

    spawned = recorder.command_ids()
    assert "C-gate" in spawned, (
        "the supervisor tick must hand accepted record_* gate commands to a "
        "worker (interfaces §1j.3: gate commands are claimed and executed, "
        "never left to manual dispatch)"
    )
    gate = ServiceDB(home).get_command("C-gate")
    assert gate is not None and gate["status"] == "claimed", (
        "the gate command must move accepted -> claimed through the fencing "
        "CAS before any worker runs it"
    )
    leases = _q(home, "SELECT worker_id FROM leases WHERE run_id = 'R1'")
    assert leases and leases[0][0] == _WORKER_ID, (
        "the gate claim rides the run lease (claim CAS/generation fencing), "
        "like every worker execution"
    )
    assert "C-drive" in spawned, (
        "the existing _drive_accepted channel must keep driving accepted "
        "drive_run commands from the same tick (IF-DRIVE-001 preserved)"
    )
    drive = ServiceDB(home).get_command("C-drive")
    assert drive is not None and drive["status"] == "claimed"


# T-006 TRACKS-TRACE IF-GATEUI-001 gate claim set is closed (§1b execution forms)
def test_tick_gate_claim_closed_set_excludes_short_writes(tmp_path, monkeypatch):
    home = _service_home(tmp_path)
    for kind, params in _GATE_KIND_PARAMS.items():
        _seed_command(home, f"C-{kind}", kind, params, params["run_id"])
    _seed_command(
        home,
        "C-short",
        "submit_intake",
        {"project_id": _PID, "source": "text", "content": "x"},
        None,
    )
    recorder = _SpawnRecorder()

    _tick(home, monkeypatch, recorder)

    spawned = recorder.command_ids()
    missing = [kind for kind in _GATE_KIND_PARAMS if f"C-{kind}" not in spawned]
    assert not missing, (
        f"every human-gate kind must be claimable by the tick, missing: "
        f"{missing} (record_triage/record_review/record_stage_approval/"
        "record_release_decision — interfaces §1j.3)"
    )
    assert "C-short" not in spawned, (
        "submit_intake is a synchronous short write executed at acceptance "
        "(§1b execution-form boundary) — the tick must never spawn it"
    )
    short = ServiceDB(home).get_command("C-short")
    assert short is not None and short["status"] == "accepted", (
        "an unexecuted short write stays accepted for its acceptance-time "
        "executor, untouched by the tick's claim set"
    )


# T-006 TRACKS-TRACE IF-GATEUI-001 missed claim only delays; requeue inherits
def test_tick_gate_claim_miss_is_delayed_not_lost(tmp_path, monkeypatch):
    home = _service_home(tmp_path)
    _seed_command(
        home, "C-g", "record_review", {"run_id": "R1", "action": "comment"}, "R1"
    )
    db = ServiceDB(home)
    assert db.claim_command("C-g", "worker-other", 7), "fixture: pre-claim the gate"
    recorder = _SpawnRecorder()

    _tick(home, monkeypatch, recorder)

    assert "C-g" not in recorder.command_ids(), (
        "a gate command owned by another worker must not be double-spawned"
    )
    owned = ServiceDB(home).get_command("C-g")
    assert owned is not None and owned["status"] == "claimed", (
        "the tick must not fabricate an outcome for a claim it missed"
    )

    requeued = db.requeue_claimed("crash-recovery-test")
    assert "C-g" in requeued, "fixture: the recovery requeue returns it to accepted"

    _tick(home, monkeypatch, recorder)

    assert "C-g" in recorder.command_ids(), (
        "a requeued gate command must be claimed by the next tick — a missed "
        "claim only delays the gate, it never loses the command (requeue "
        "inherits, interfaces §1j.4)"
    )
    recovered = ServiceDB(home).get_command("C-g")
    assert recovered is not None and recovered["status"] == "claimed"
    leases = _q(home, "SELECT worker_id FROM leases WHERE run_id = 'R1'")
    assert leases and leases[0][0] == _WORKER_ID


# T-006 TRACKS-TRACE IF-GATEUI-001 gate claim and fresh drive share one tick
def test_tick_gate_claim_coexists_with_fresh_drive(tmp_path, monkeypatch):
    home = _service_home(tmp_path)
    _schedule_row(home, "R1")
    _seed_command(
        home, "C-gate", "record_triage", {"run_id": "R1", "decision": "go"}, "R1"
    )
    recorder = _SpawnRecorder()

    _tick(home, monkeypatch, recorder)

    spawned = recorder.command_ids()
    assert "C-gate" in spawned, (
        "the tick must claim the runnable run's gate command (interfaces "
        "§1j.3) in the same pass that drives the run"
    )
    gate = ServiceDB(home).get_command("C-gate")
    assert gate is not None and gate["status"] == "claimed"
    drives = _q(
        home,
        "SELECT command_id FROM commands WHERE kind = 'drive_run' AND run_id = 'R1'",
    )
    assert drives, (
        "the tick must still accept a fresh drive_run for the runnable run "
        "(self-drive closure: after the gate event lands, the next ordinary "
        "drive advances the run — no manual dispatch in between)"
    )
    assert {row[0] for row in drives} <= spawned, (
        "the freshly accepted drive command must be handed to a worker in "
        "the same tick, never stranded accepted"
    )
    for row in drives:
        command = ServiceDB(home).get_command(row[0])
        assert command is not None and command["status"] == "claimed"


# -- intake evaluation claiming (interfaces §1f.1 SM-01, IF-INTAKE-001) --------


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 tick takes submitted intakes
def test_tick_takes_submitted_intake_into_evaluation(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    home = _service_home(tmp_path)
    _project_row(home, repo)
    _seed_intake(home, "I1", status="submitted")
    calls = _spy_evaluation(monkeypatch)
    recorder = _SpawnRecorder()

    _tick(home, monkeypatch, recorder)

    def _claimed() -> bool:
        row = _intake(home, "I1")
        return (
            "I1" in calls
            or _brief(home, "I1").exists()
            or row.get("status") in _EVALUATION_WINDOW
        )

    assert _eventually(_claimed), (
        "the supervisor tick must take intakes.status=submitted rows into the "
        "evaluation window (SM-01 submitted -> evaluating via the intake "
        "worker entry point) — a submitted intake is never left waiting for a "
        "manual dispatch (AC-FR0338-01)"
    )


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 stale evaluating redispatch
def test_tick_reclaims_stale_evaluating_with_timeout_gate(tmp_path, monkeypatch):
    repo = git_repo(tmp_path)
    home = _service_home(tmp_path)
    _project_row(home, repo)
    _seed_intake(home, "I-stale", status="evaluating", updated_age_s=7200.0)
    _seed_intake(home, "I-fresh", status="evaluating", updated_age_s=0.0)
    calls = _spy_reclaim(monkeypatch)
    recorder = _SpawnRecorder()

    _tick(home, monkeypatch, recorder)

    def _recovered() -> bool:
        row = _intake(home, "I-stale")
        return bool(calls) or row.get("status") != "evaluating"

    assert _eventually(_recovered), (
        "an intake stranded in evaluating past the timeout must be recovered "
        "by the tick (crash recovery: redispatch through the intake reclaim "
        "path, budget <= 3) — a crashed evaluator never strands the intake "
        "forever (AC-FR0338-01)"
    )
    assert not _brief(home, "I-fresh").exists(), (
        "a fresh evaluating row must not be re-driven immediately — the "
        "crash recovery is timeout-gated, never a hot loop"
    )
    assert _intake(home, "I-fresh").get("status") == "evaluating"
