"""T-005 RED: human-gate command worker execution (IF-GATEUI-001 exec face).

Devon-owned unit RED for the T-005 delivery slice (architecture §1.0.9 /
interfaces §1j): ``tracks/supervisor/gate_exec.py`` (new worker execution
face for the four human gate commands) plus the ``worker_main`` kind
dispatch extension that routes ``record_triage`` / ``record_review`` /
``record_stage_approval`` / ``record_release_decision`` through it, sharing
the CLI human pipeline (writer_lock + human.* domain event + checkpoint)
with zero CLI semantic drift (§1j.3, IF-DRIVE-001 reuse).

The failing nodes pin the contracts this task must deliver:

- a claimed ``record_triage`` command executes against the real service.db
  and run plane: the command completes and exactly one ``human.triage``
  event lands with the payload decision/actor, clearing the awaiting gate
  (§1j.3, AC-FR0342-01);
- a claimed ``record_review`` command on a review gate does the same for
  ``human.review`` (§1j.3, AC-FR0342-01);
- the four gate kinds are dispatched to the gate execution face instead of
  the legacy "worker does not handle command kind" refusal, and a gate
  command on a run with no awaiting gate still fails closed — never fake
  success (§1j.3/§1j.4, AC-FR0342-02);
- the worker-side pipeline stays CLI-equivalent ("与 CLI 等价", §1j.3): the
  same twin fixture driven through ``trac triage`` and through the claimed
  worker command appends the same event sequence and the same
  ``human.triage`` payload (AC-FR0342-01).

M-IMPL RED discipline: ``gate_exec`` is still a missing symbol, so the
dispatch node guards the import into a real ``AssertionError``
(``raise ... from None``) and the execution nodes fail as plain assertion
failures on today's unhandled-kind refusal — every record classifies as
assertion_failure, no stub tokens, no assembly errors, no ``pytest.fail``.
Nothing here mocks the system under test: run-plane state is seeded into
the real per-project tracks.db via ``tracks.store.Store`` and service-plane
state into the real service.db via ``tracks.supervisor.db.ServiceDB``; the
worker entry runs in-process against those real stores.

AC: FR-0342 — TRACKS-TRACE IF-GATEUI-001 (worker execution face).
"""

from __future__ import annotations

import contextlib
import datetime
import json
import sqlite3
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths, templating
from tracks.cli.run_cmd import cmd_triage
from tracks.executor.helpers import git
from tracks.store import Store
from tracks.supervisor import worker_main
from tracks.supervisor.db import ServiceDB

_VERSION = "v0.2"
_RUN = "R1"
_ACTOR = "web-human"
_GATE_KINDS = (
    "record_triage",
    "record_review",
    "record_stage_approval",
    "record_release_decision",
)


# -- fixtures: real run plane + real service plane ------------------------------


def _seed_run(repo: Path, events: list) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        for event_type, payload in events:
            store.append(_RUN, _VERSION, event_type, payload)
    finally:
        store.close()


def _triage_gate_events() -> list:
    """A TRIAGE outcome parks M-STORY at awaiting=triage."""
    return [
        ("story.requested", {"raw_chars": 4}),
        ("stage.entered", {"stage": "M-STORY"}),
        ("outcome.received", {"status": "done", "role": "scribe"}),
    ]


def _idle_run_events() -> list:
    """A mid-flight M-IMPL run with no awaiting gate."""
    return [
        ("story.requested", {"raw_chars": 4}),
        ("stage.entered", {"stage": "M-IMPL"}),
    ]


def _capture_story_doc(repo: Path) -> None:
    """Commit the story capture the human pipeline's template check needs."""
    vdir = paths.version_dir(paths.tracks_home(repo), _VERSION)
    vdir.mkdir(parents=True, exist_ok=True)
    story = vdir / "story.md"
    story.write_text(
        templating.render_story_skeleton(
            "给工作台加一个导出按钮", datetime.date.today().isoformat()
        ),
        encoding="utf-8",
    )
    git(repo, "add", str(story))
    git(
        repo,
        "commit",
        "-m",
        f"M-START: capture raw requirement for {_VERSION}",
        "--only",
        "--",
        str(story),
    )


def _gate_repo(base: Path, events: list) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    repo = git_repo(base)
    _seed_run(repo, events)
    _capture_story_doc(repo)
    return repo


def _service_plane(home: Path, repo: Path) -> ServiceDB:
    home.mkdir(parents=True, exist_ok=True)
    db = ServiceDB(home)
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES ('P1', ?, ?, 'human',"
            " '2026-10-12T00:00:00+00:00')",
            (str(repo), _VERSION),
        )
        conn.commit()
    return db


def _claim_gate_command(db: ServiceDB, home: Path, kind: str, params: dict, cid: str):
    """Seed + claim an accepted gate command bound to generation 1."""
    db.register_command(
        {
            "command_id": cid,
            "kind": kind,
            "params_json": json.dumps(params),
            "params_digest": "0" * 64,
            "idempotency_key": f"idem-{cid}",
            "actor": _ACTOR,
            "actor_class": "human",
            "surface": "http",
            "project_id": "P1",
            "run_id": _RUN,
        }
    )
    assert db.claim_command(cid, "worker-w1", 1) is not None, (
        "fixture: an accepted gate command must be claimable"
    )
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        # leases.run_id is the primary key: the 4-kind dispatch loop re-seeds
        # the same run's lease, so the insert is idempotent (ISLAND_GATE_2/M2).
        conn.execute(
            "INSERT OR IGNORE INTO leases (run_id, worker_id, generation,"
            " acquired_at, expires_at) VALUES (?, 'worker-w1', 1,"
            " '2026-10-12T00:00:00+00:00', '2026-10-12T00:01:00+00:00')",
            (_RUN,),
        )
        conn.commit()
    return db.get_command(cid)


def _run_worker(monkeypatch, home: Path, command_id: str) -> int:
    monkeypatch.setenv("TRAC_SERVE_HOME", str(home))
    return worker_main.main(["--command-id", command_id])


# -- claimed gate commands execute through the worker ---------------------------


# AC-FR0342-01@v0.11 TRACKS-TRACE IF-GATEUI-001 claimed record_triage executes
def test_claimed_record_triage_executes_human_gate_event(tmp_path, monkeypatch):
    repo = _gate_repo(tmp_path, _triage_gate_events())
    home = tmp_path / "service-home"
    db = _service_plane(home, repo)
    _claim_gate_command(
        db, home, "record_triage", {"run_id": _RUN, "decision": "go"}, "C-TRI"
    )
    code = _run_worker(monkeypatch, home, "C-TRI")
    assert code == 0, (
        "the worker must execute a claimed record_triage to completion instead of"
        " refusing the gate kind (§1j.3)"
    )
    row = db.get_command("C-TRI")
    assert row["status"] == "completed", (
        "a gate command whose human pipeline succeeded must be marked completed"
        f" (§1j.3); got {row['status']} with {row['result_json']}"
    )
    store = Store(paths.tracks_home(repo))
    try:
        triage = [e for e in store.events(_RUN) if e.type == "human.triage"]
        assert len(triage) == 1, (
            "exactly one human.triage event per gate command (interfaces §4b.3)"
        )
        assert triage[0].payload.get("decision") == "go"
        assert triage[0].payload.get("actor") == _ACTOR, (
            "the command row's actor rides the human.triage payload (§1j.3)"
        )
        assert store.state(_RUN).awaiting is None, (
            "the executed gate must clear the awaiting boundary (§1j.3)"
        )
    finally:
        store.close()


# AC-FR0342-01@v0.11 TRACKS-TRACE IF-GATEUI-001 claimed record_review executes
def test_claimed_record_review_executes_human_gate_event(tmp_path, monkeypatch):
    repo = _gate_repo(tmp_path, _triage_gate_events() + [("sage.verdict", {"verdict": "pass"})])
    home = tmp_path / "service-home"
    db = _service_plane(home, repo)
    _claim_gate_command(
        db, home, "record_review", {"run_id": _RUN, "action": "no_comment"}, "C-REV"
    )
    code = _run_worker(monkeypatch, home, "C-REV")
    assert code == 0, (
        "the worker must execute a claimed record_review to completion (§1j.3)"
    )
    row = db.get_command("C-REV")
    assert row["status"] == "completed", (
        f"a succeeded review gate command must be completed; got {row['result_json']}"
    )
    store = Store(paths.tracks_home(repo))
    try:
        reviews = [e for e in store.events(_RUN) if e.type == "human.review"]
        assert len(reviews) == 1, "exactly one human.review event (interfaces §4b.3)"
        assert reviews[0].payload.get("action") == "no_comment"
        assert reviews[0].payload.get("actor") == _ACTOR
        assert store.state(_RUN).awaiting is None
    finally:
        store.close()


# -- the four gate kinds are dispatched to the gate execution face --------------


def _gate_exec_module():
    """Import the §1j.3 execution face; a missing symbol is a RED assertion."""
    try:
        from tracks.supervisor import gate_exec
    except ImportError as exc:
        raise AssertionError(
            "IF-GATEUI-001 execution face tracks/supervisor/gate_exec.py is still"
            f" a missing symbol ({exc}): the four human gate commands need a"
            " worker execution module sharing the CLI pipeline (§1j.3)"
        ) from None
    return gate_exec


# AC-FR0342-02@v0.11 TRACKS-TRACE IF-GATEUI-001 gate kinds routed via gate_exec
def test_gate_kinds_dispatched_not_refused_as_unhandled(tmp_path):
    _gate_exec_module()
    repo = _gate_repo(tmp_path, _idle_run_events())
    db = _service_plane(tmp_path / "service-home", repo)
    for index, kind in enumerate(_GATE_KINDS):
        cid = f"C-GATE-{index}"
        _claim_gate_command(db, tmp_path / "service-home", kind, {"run_id": _RUN}, cid)
        _result, failure = worker_main._execute(db, db.get_command(cid))
        assert failure is not None, (
            f"{kind} on a run with no awaiting gate must fail closed, never fake"
            " success (§1j.4)"
        )
        reason = str(failure.get("reason", ""))
        assert "does not handle command kind" not in reason, (
            f"{kind} must be dispatched to the gate execution face (worker_main"
            f" kind dispatch extension, §1j.3) instead of the legacy"
            f" unhandled-kind refusal: {reason}"
        )


# -- worker gate execution stays CLI-equivalent (§1j.3) -------------------------


# AC-FR0342-01@v0.11 TRACKS-TRACE IF-GATEUI-001 worker pipeline equals CLI twin
def test_worker_gate_pipeline_appends_cli_equivalent_events(tmp_path, monkeypatch):
    cli_repo = _gate_repo(tmp_path / "cli", _triage_gate_events())
    worker_repo = _gate_repo(tmp_path / "worker", _triage_gate_events())
    assert cmd_triage(cli_repo, "go", "--actor", _ACTOR) == 0, (
        "fixture baseline: the CLI twin of the shared pipeline must succeed"
    )
    home = tmp_path / "worker" / "service-home"
    db = _service_plane(home, worker_repo)
    _claim_gate_command(
        db, home, "record_triage", {"run_id": _RUN, "decision": "go"}, "C-EQ"
    )
    assert _run_worker(monkeypatch, home, "C-EQ") == 0, (
        "the worker half of the twin must execute the gate command (§1j.3)"
    )
    cli_store = Store(paths.tracks_home(cli_repo))
    worker_store = Store(paths.tracks_home(worker_repo))
    try:
        assert [e.type for e in worker_store.events(_RUN)] == [
            e.type for e in cli_store.events(_RUN)
        ], (
            "the worker gate path must append the same pipeline event sequence as"
            " the CLI twin (§1j.3 与 CLI 等价 / CLI 语义零变更)"
        )
        cli_event = next(e for e in cli_store.events(_RUN) if e.type == "human.triage")
        worker_event = next(
            e for e in worker_store.events(_RUN) if e.type == "human.triage"
        )
        assert worker_event.payload.get("decision") == cli_event.payload.get("decision")
        assert worker_event.payload.get("actor") == cli_event.payload.get("actor"), (
            "the payload contract (decision/actor) is identical on both faces"
        )
    finally:
        cli_store.close()
        worker_store.close()
