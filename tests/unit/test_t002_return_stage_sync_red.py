"""T-002 RED: return_stage service-side conditional confirm + sync execution.

Devon-owned unit RED for the PLANNING round 1 revision (DIAGNOSE
plan_defect seq 10722 承接): the #25 return command on the service face
must mirror the v0.9 §1b#11 CLI semantics and actually execute inside the
acceptance chain — the frozen anchor ``test_return_confirm_revalidation``
leg2 reads the run-plane events immediately after the POST, and today the
service accepts with zero execution:

- **conditional confirm** (v0.9 §1b#11): ``confirm`` is only required when
  the return would cross already-executed irreversible operations — a
  return with no irreversible history is accepted without the key; an
  unconfirmed return over irreversible operations is refused
  ``validation_failed`` with the ``already_executed`` list carried in the
  detail (no events, pointer unmoved);
- **synchronous execution** (受理链内同步执行): a confirmed return lands
  the full run-plane sequence — escape barrier, ``human.return``
  (actor pinned to the literal "Human", §1a) and the bounded rollback
  advance through ``stage.rolled_back`` — before the acceptance returns;
  after it the run's state sits at the target stage.

RED discipline: every failing node guards the absent behavior into a real
``AssertionError`` (Rejection/missing-event guards — no stub tokens, no
assembly errors, no ``pytest.fail``). Fixtures seed the documented storage
for real (run plane through Store, projects row in service.db,
CommandService over ServiceDB); nothing mocks the system under test.

AC: FR-0337 — TRACKS-TRACE IF-RETURN-001 / IF-CMDSVC-001 (the #25 service
face; the CLI twin ``trac return`` keeps its v0.9 semantics untouched).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from tracks import paths
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService, Rejection

_PID = "proj-1"
_ACTOR = "local-user"
_TS = "2026-10-11T00:00:00+00:00"
_RUN = "run-ret"
_VERSION = "v0.11"


def _run_events(repo: Path, event_type: str) -> list[dict]:
    """Payloads of one run-plane event type (append-order)."""
    db_path = paths.tracks_home(repo) / "runtime" / "tracks.db"
    with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT payload FROM events WHERE run_id = ? AND type = ? ORDER BY seq",
            (_RUN, event_type),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _run_state(repo: Path):
    store = Store(paths.tracks_home(repo))
    try:
        return store.state(_RUN)
    finally:
        store.close()


def _seed_returnable(tmp_path: Path, *, irreversible: bool) -> tuple[Path, Path, CommandService]:
    """A run at the M-REQ-APPROVAL approval gate (return targets M-STORY),
    registered on a real service plane; optionally carrying one
    already-executed irreversible publish operation."""
    repo = tmp_path / "repo"
    repo.mkdir()
    vdir = repo / ".tracks" / "projects" / _VERSION
    vdir.mkdir(parents=True)
    for name, body in (
        ("story.md", "story body\n"), ("spec.md", "spec body\n"),
        ("acceptance.md", "acceptance body\n"),
    ):
        (vdir / name).write_text(body, encoding="utf-8")
    store = Store(paths.tracks_home(repo))
    try:
        store.append(_RUN, _VERSION, "story.requested", {"raw_chars": 3})
        store.append(_RUN, _VERSION, "stage.entered", {"stage": "M-REQ-APPROVAL"})
        store.append(_RUN, _VERSION, "preview.generated", {"digest": "deadbeef"})
        if irreversible:
            store.append(
                _RUN, _VERSION, "publish.executed",
                {"status": "done", "operation_kind": "merge", "target": "main"},
            )
    finally:
        store.close()
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    with sqlite3.connect(home / "service.db") as conn:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, _ACTOR, _TS),
        )
    return repo, home, CommandService(home, sdb.ServiceDB(home), {})


def _accept(service: CommandService, params: dict, *, key: str):
    try:
        return service.accept(
            "return_stage", params,
            actor=_ACTOR, actor_class="human", surface="http", idempotency_key=key,
        )
    except Rejection as rejection:
        raise AssertionError(
            f"return_stage must accept {params!r} here; rejected with"
            f" {rejection.reason}: {rejection.detail}"
        ) from None


def _refused(service: CommandService, params: dict, *, key: str) -> Rejection | None:
    try:
        service.accept(
            "return_stage", params,
            actor=_ACTOR, actor_class="human", surface="http", idempotency_key=key,
        )
    except Rejection as rejection:
        return rejection
    return None


# AC-FR0337-02@v0.11 TRACKS-TRACE IF-RETURN-001 confirm is a conditional gate
def test_return_without_irreversible_ops_needs_no_confirm(tmp_path: Path):
    _repo, _home, service = _seed_returnable(tmp_path, irreversible=False)

    receipt = _accept(
        service,
        {"run_id": _RUN, "to": "M-STORY", "reason": "rework the story"},
        key="ret-free-1",
    )

    assert receipt.status == "accepted", (
        "a return that crosses no irreversible operation is accepted without the"
        " confirm key (v0.9 §1b#11: confirm=true is only required when crossing"
        " already-executed irreversible operations)"
    )


# AC-FR0337-02@v0.11 TRACKS-TRACE IF-RETURN-001 unconfirmed crossing carries the list
def test_return_unconfirmed_over_irreversible_ops_refused_with_already_executed(
    tmp_path: Path,
):
    _repo, _home, service = _seed_returnable(tmp_path, irreversible=True)
    params = {"run_id": _RUN, "to": "M-STORY", "reason": "crossing history"}

    missing = _refused(service, dict(params), key="ret-guard-1")
    assert missing is not None and missing.reason == "validation_failed", (
        "an unconfirmed return over already-executed irreversible operations is"
        f" refused validation_failed; got {'acceptance (no refusal)' if missing is None else missing.reason}"
    )
    assert "already_executed" in missing.detail and "merge" in missing.detail, (
        "the refusal detail carries the already_executed operations (the merge"
        f" over main this return would cross); got {missing.detail!r}"
    )
    explicit_false = _refused(
        service, {**params, "confirm": False}, key="ret-guard-2"
    )
    assert explicit_false is not None and explicit_false.reason == "validation_failed", (
        "confirm=false over irreversible operations is the same refusal, not a"
        " silent go-ahead"
    )


# AC-FR0337-02@v0.11 TRACKS-TRACE IF-RETURN-001 confirmed return executes synchronously
def test_confirmed_return_executes_to_stage_rolled_back_in_acceptance_chain(
    tmp_path: Path,
):
    repo, _home, service = _seed_returnable(tmp_path, irreversible=True)

    receipt = _accept(
        service,
        {
            "run_id": _RUN, "to": "M-STORY", "reason": "crossing history",
            "confirm": True,
        },
        key="ret-exec-1",
    )

    assert receipt.status == "accepted"
    returns = _run_events(repo, "human.return")
    assert returns, (
        "the confirmed return lands human.return inside the acceptance chain —"
        " the frozen anchor reads the run-plane events immediately after the"
        " POST (test_return_confirm_revalidation leg2); today the service"
        " accepts with zero execution"
    )
    assert returns[-1]["actor"] == "Human", (
        "the human.return payload actor is pinned to the literal 'Human' (§1a,"
        " E-01 renders human_return=Human→<to>) — not the session operator name"
    )
    assert returns[-1]["to_stage"] == "M-STORY"
    assert returns[-1]["reason"] == "crossing history"
    rolled = _run_events(repo, "stage.rolled_back")
    assert rolled, (
        "the bounded rollback advance reaches stage.rolled_back synchronously —"
        " 至 stage.rolled_back 落盘 before the acceptance returns"
    )
    assert rolled[-1]["to_stage"] == "M-STORY"
    state = _run_state(repo)
    assert state.stage == "M-STORY", (
        f"the run's projected state sits at the return target; got {state.stage!r}"
    )
