"""T-002 RED: intake/backlog/gate service faces (storage + acceptance halves).

Devon-owned unit RED for the T-002 service-plane slice the task declares —
the storage and acceptance faces of IF-INTAKE-001 / IF-BACKLOG-001 /
IF-MSTART-001 / IF-GATEUI-001 that live in this task's scope (kernel events,
service.db, CommandService acceptance, templating classification):

- closed sets grow exactly the contracted members: tracks.db EVENT_TYPES +
  ``backlog.dispositioned`` (§1d.1), service_events + the three ``intake.*``
  members (§1a #26-28), ServiceCommandKind + the five new kinds (§1b #14-18);
- the ``intakes`` table exists idempotently with the documented columns
  (§1c #9) — the SM-01 state machine's storage;
- ``submit_intake`` acceptance persists the intakes row (status=submitted)
  and the ``intake.submitted`` audit carrying content_sha (§1b #14);
- ``decide_intake`` guards: intake_not_ready unless evaluated, remove_backlog
  only for source=backlog (§1b #15); the non-develop actions are short writes
  executed at acceptance — backlog lands ``backlog.recorded`` with the new
  payload keys, archive only moves the intakes row + ``intake.decided``
  (§1f.3);
- ``decide_intake(develop)`` walks the M-START order: delegation to create_run
  carrying title/story_form with the releases/v{N+1} derivation (§1g.1.d),
  the active-run exit recording backlog(reason=active_run) and the dirty-tree
  refusal (§1g.1.a/b);
- ``dispose_backlog`` lands ``backlog.dispositioned`` with the去向 fields and
  refuses double disposition / promote without target (§1b #16, §1d.3);
- ``record_triage`` / ``record_review`` enforce the awaiting preflight and
  their closed decision/action sets (§1b #17/#18);
- ``templating.classify_story_input`` separates complete stories from raw
  seeds (§1f.4) and the Scribe evaluation worker module exists
  (``tracks/supervisor/intake.py``, §1f.2).

RED discipline: every failing node guards the absent behavior into a real
``AssertionError`` (ImportError/ValueError/OperationalError/Rejection guards
— no stub tokens, no assembly errors, no ``pytest.fail``). Fixtures seed the
documented storage for real (Store run planes, service.db rows, git repos
via the shared unit helper); nothing mocks the system under test. The full
UI/HTTP matrix stays with the frozen integration anchors.

AC: FR-0338/FR-0339/FR-0340/FR-0342 — TRACKS-TRACE IF-INTAKE-001 /
IF-BACKLOG-001 / IF-MSTART-001 / IF-GATEUI-001.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tests.unit.helpers import git, git_repo
from tracks import paths, templating
from tracks.kernel.events import EVENT_TYPES
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor import service as svc
from tracks.supervisor.service import CommandService, Rejection

_PID = "proj-1"
_ACTOR = "local-user"
_TS = "2026-10-11T00:00:00+00:00"
_ENTRY = "01ENTRYENTRYENTRYENTRYENTRY0000"
_ENTRY2 = "01ENTRYENTRYENTRYENTRYENTRY0001"


# -- seeding / audit helpers (real storage, no SUT mocks) ---------------------


def _q(home: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=rw", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _run_q(repo: Path, sql: str, params: tuple = ()) -> list:
    db_path = paths.tracks_home(repo) / "runtime" / "tracks.db"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _project_row(home: Path, repo: Path, version: str = "v0.2") -> None:
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?,?,?,?,?)",
        (_PID, str(repo), version, _ACTOR, _TS),
    )
    conn.commit()
    conn.close()


def _schedule_row(home: Path, active_run: str | None) -> None:
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT OR REPLACE INTO schedule (id, active_run, queue_json) VALUES (1, ?, ?)",
        (active_run, json.dumps([])),
    )
    conn.commit()
    conn.close()


def _intake_row(
    home: Path,
    intake_id: str,
    *,
    source: str = "text",
    content: str = "统一入口的一段输入",
    status: str = "evaluated",
    report: dict | None = None,
) -> None:
    """Seed one documented intakes row (§1c #9) for the decide/gate contracts.

    The intakes table not existing yet is the RED target itself — the missing
    storage is guarded into the same AssertionError vocabulary instead of an
    assembly error; any other SQL failure re-raises untouched.
    """
    if report is None:
        report = {
            "feasibility": {"verdict": "feasible", "blockers": []},
            "duplicates": {"found": False, "items": []},
            "conflicts": {"found": False, "pairs": []},
            "title": "Scribe 建议标题",
            "input_form": "raw",
            "summary": "ok",
        }
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO intakes (intake_id, project_id, source, content, issue,"
            " origin_entry, status, report_json, decision, decided_at, created_at,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                intake_id,
                _PID,
                source,
                content,
                None,
                None,
                status,
                json.dumps(report, ensure_ascii=False),
                None,
                None,
                _TS,
                _TS,
            ),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        if "no such table: intakes" not in str(exc):
            raise
        raise AssertionError(
            "decide_intake's documented storage is missing: the intakes table"
            " (§1c #9) must exist for the SM-01 state machine to be seedable"
        ) from None
    finally:
        conn.close()


def _service(home: Path) -> CommandService:
    return CommandService(home, sdb.ServiceDB(home), {})


def _accept(service: CommandService, kind: str, params: dict, *, key: str):
    try:
        return service.accept(
            kind, params,
            actor=_ACTOR, actor_class="human", surface="http", idempotency_key=key,
        )
    except Rejection as rejection:
        raise AssertionError(
            f"{kind} must accept {params!r} here; rejected with"
            f" {rejection.reason}: {rejection.detail}"
        ) from None


def _refused(service: CommandService, kind: str, params: dict, *, key: str) -> Rejection:
    with pytest.raises(Rejection) as caught:
        service.accept(
            kind, params,
            actor=_ACTOR, actor_class="human", surface="http", idempotency_key=key,
        )
    return caught.value


def _seed_backlog_entry(repo: Path, entry: str) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        store.append(entry, "v0.2", "backlog.recorded", {"reason": "active_run"})
    finally:
        store.close()


def _seed_run(repo: Path, run_id: str, *, awaiting_triage: bool) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        store.append(run_id, "v0.2", "story.requested", {"raw_chars": 4})
        store.append(run_id, "v0.2", "stage.entered", {"stage": "M-STORY" if awaiting_triage else "M-IMPL"})
        if awaiting_triage:
            store.append(run_id, "v0.2", "outcome.received", {"status": "done", "role": "scribe"})
    finally:
        store.close()


# -- closed sets: events, table, kinds (§1a/§1c/§1d/§1b) ----------------------


# AC-FR0340-02@v0.11 TRACKS-TRACE IF-BACKLOG-001 dispositioned closed set + marker
def test_backlog_dispositioned_event_type_and_run_less_marker(tmp_path: Path):
    assert "backlog.dispositioned" in EVENT_TYPES, (
        "tracks.db EVENT_TYPES must append backlog.dispositioned (§1d.1) — the"
        " disposition is the queryable去向 truth of a backlog entry"
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(paths.tracks_home(repo))
    try:
        store.append(_ENTRY, "v0.2", "backlog.recorded", {"reason": "active_run"})
        store.append("01DISPOSITION00000000000000000", "v0.2", "backlog.dispositioned",
                     {"entry": _ENTRY, "disposition": "removed", "to": None})
        store.append("01DISPOSITION00000000000000001", "v0.2", "backlog.dispositioned",
                     {"entry": _ENTRY, "disposition": "removed", "to": None})
        state = store.state(_ENTRY)
        assert store.active_run() is None, (
            "the run-less disposition marker never counts as the active run"
            " (the _on_backlog_recorded phantom semantics stay unchanged, §1d.1)"
        )
        assert state.status == "backlog", "the entry keeps its backlog status after disposition"
    finally:
        store.close()


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 intake service events appendable
def test_intake_service_events_append_monotonically(tmp_path: Path):
    db = sdb.ServiceDB(tmp_path / "home")
    seqs = []
    for etype in ("intake.submitted", "intake.evaluated", "intake.decided"):
        try:
            seqs.append(db.append_event(etype, {"intake_id": "I1"}, project_id=_PID))
        except ValueError:
            raise AssertionError(
                f"{etype} must be a service_events member (§1a #26-28); the closed"
                " set rejects it today"
            ) from None
    assert seqs == sorted(seqs) and all(seq >= 1 for seq in seqs), (
        "intake events append append-only with monotonic seq (§1a)"
    )


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 intakes table idempotent schema
def test_intakes_table_created_with_documented_columns(tmp_path: Path):
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    sdb.ServiceDB(home)  # idempotent reopen: CREATE TABLE IF NOT EXISTS (§1c #9)
    columns = _q(home, "PRAGMA table_info(intakes)")
    assert columns, (
        "the intakes table must exist after ServiceDB init (§1c #9) — the SM-01"
        " intake state machine has no storage without it"
    )
    names = {row[1] for row in columns}
    assert names == {
        "intake_id", "project_id", "source", "content", "issue", "origin_entry",
        "status", "report_json", "decision", "decided_at", "created_at", "updated_at",
    }, f"the documented §1c #9 column set is expected, got {sorted(names)}"
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO intakes (intake_id, project_id, source, content, issue,"
        " origin_entry, status, report_json, decision, decided_at, created_at,"
        " updated_at) VALUES ('I1', ?, 'text', 'hello', NULL, NULL, 'submitted',"
        " NULL, NULL, NULL, ?, ?)",
        (_PID, _TS, _TS),
    )
    conn.commit()
    conn.close()
    assert _q(home, "SELECT status, source FROM intakes WHERE intake_id = 'I1'") == [
        ("submitted", "text")
    ]


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 five new kinds are human-only
# AC-FR0342-01@v0.11 TRACKS-TRACE IF-GATEUI-001 gate kinds join the closed set
def test_service_command_kinds_gain_intake_and_gate_members():
    new_kinds = {
        "submit_intake",
        "decide_intake",
        "dispose_backlog",
        "record_triage",
        "record_review",
    }
    assert new_kinds <= set(svc.SERVICE_COMMAND_KINDS), (
        "the service command closed set must carry the five v0.11 members (§1b #14-18)"
    )
    assert new_kinds <= svc.HUMAN_ONLY_KINDS, "all five new kinds are human-gated (§1b)"
    assert not (new_kinds & svc.SYSTEM_ONLY_KINDS)


# -- submit_intake / decide_intake acceptance (§1b #14/#15, §1f.3) ------------


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 submit creates row + audit
def test_submit_intake_persists_row_and_service_event(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo)
    service = _service(home)
    content = "统一入口的一段话"

    receipt = _accept(
        service, "submit_intake", {"project_id": _PID, "source": "text", "content": content},
        key="sub-1",
    )

    assert receipt.status == "accepted"
    assert _q(home, "SELECT source, content, status FROM intakes") == [
        ("text", content, "submitted")
    ], "the intakes row is created at submission with status=submitted (SM-01)"
    intake_id = _q(home, "SELECT intake_id FROM intakes")[0][0]
    audits = [
        json.loads(row[0])
        for row in _q(home, "SELECT payload FROM service_events WHERE type = 'intake.submitted'")
    ]
    assert audits and audits[-1]["intake_id"] == intake_id
    assert audits[-1]["source"] == "text"
    assert audits[-1]["content_sha"] == hashlib.sha256(content.encode("utf-8")).hexdigest(), (
        "the audit carries the content digest (§1a #26), not the raw body"
    )
    assert audits[-1]["actor"] == _ACTOR

    refused = _refused(
        service, "submit_intake", {"project_id": _PID, "source": "gossip", "content": "x"},
        key="sub-2",
    )
    assert refused.reason == "validation_failed", (
        f"source is a closed three-way set; got {refused.reason}"
    )


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 decide preconditions
def test_decide_intake_readiness_and_source_guards(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo)
    service = _service(home)

    unknown = _refused(service, "decide_intake", {"intake_id": "I-nope", "action": "backlog"},
                       key="d-0")
    assert unknown.reason == "intake_not_ready", (
        f"an unresolvable intake answers intake_not_ready; got {unknown.reason}"
    )

    _intake_row(home, "I-sub", status="submitted")
    early = _refused(service, "decide_intake", {"intake_id": "I-sub", "action": "backlog"},
                     key="d-1")
    assert early.reason == "intake_not_ready", (
        f"deciding before the evaluation terminal answers intake_not_ready; got {early.reason}"
    )
    assert _q(home, "SELECT status FROM intakes WHERE intake_id = 'I-sub'") == [("submitted",)], (
        "a refused decision migrates nothing (SM-01.5)"
    )

    _intake_row(home, "I-eval", status="evaluated")
    wrong_source = _refused(
        service, "decide_intake", {"intake_id": "I-eval", "action": "remove_backlog"}, key="d-2"
    )
    assert wrong_source.reason == "validation_failed", (
        f"remove_backlog is backlog-source-only; got {wrong_source.reason}"
    )


# AC-FR0340-01@v0.11 TRACKS-TRACE IF-BACKLOG-001 backlog/archive short writes
def test_decide_intake_backlog_and_archive_short_writes(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo)
    service = _service(home)
    _intake_row(home, "I-back", content="条目内容")

    receipt = _accept(service, "decide_intake", {"intake_id": "I-back", "action": "backlog"},
                      key="b-1")

    assert receipt.status == "accepted"
    recorded = _run_q(repo, "SELECT run_id, payload FROM events WHERE type = 'backlog.recorded'")
    assert len(recorded) == 1, "action ① records exactly one backlog entry"
    entry_id, payload_raw = recorded[0]
    payload = json.loads(payload_raw)
    assert payload["source"] == "text" and payload["intake_id"] == "I-back", (
        "the new backlog.recorded payload keys name the unified-entry source (§1d.2)"
    )
    assert payload["content"] == "条目内容"
    assert _q(home, "SELECT status, decision FROM intakes WHERE intake_id = 'I-back'") == [
        ("decided", "backlog")
    ], "the intakes row reaches the decided terminal with its decision (§1c #9)"
    decided = [
        json.loads(row[0])
        for row in _q(home, "SELECT payload FROM service_events WHERE type = 'intake.decided'")
    ]
    assert decided[-1]["action"] == "backlog"
    assert decided[-1]["outcome"]["backlog_entry_id"] == entry_id, (
        "the decision audit carries the created entry identity (§1a #28)"
    )

    _intake_row(home, "I-arch", content="归档内容")
    _accept(service, "decide_intake", {"intake_id": "I-arch", "action": "archive"}, key="b-2")
    row = _q(home, "SELECT status, decision, decided_at FROM intakes WHERE intake_id = 'I-arch'")
    assert row[0][0] == "decided" and row[0][1] == "archive" and row[0][2] is not None, (
        "action ③ archives on the service plane only — the intakes row records it,"
        " physically distinct from the run-plane backlog storage (§1h.2)"
    )
    for _run_id, payload_raw in _run_q(
        repo, "SELECT run_id, payload FROM events WHERE type = 'backlog.recorded'"
    ):
        assert json.loads(payload_raw).get("intake_id") != "I-arch", (
            "archiving never writes a backlog entry"
        )


# AC-FR0340-02@v0.11 TRACKS-TRACE IF-BACKLOG-001 dispose lands dispositioned
def test_dispose_backlog_records_disposition_and_refuses_double(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo)
    service = _service(home)
    _seed_backlog_entry(repo, _ENTRY)

    receipt = _accept(
        service,
        "dispose_backlog",
        {"project_id": _PID, "entry_id": _ENTRY, "disposition": "removed"},
        key="disp-1",
    )

    assert receipt.status == "accepted"
    events = _run_q(repo, "SELECT payload FROM events WHERE type = 'backlog.dispositioned'")
    assert len(events) == 1, "the disposal lands exactly one backlog.dispositioned (§1d.3)"
    payload = json.loads(events[0][0])
    assert payload["entry"] == _ENTRY
    assert payload["disposition"] == "removed" and payload["to"] is None, (
        "the去向 fields are queryable (removed, no target)"
    )

    _refused(
        service,
        "dispose_backlog",
        {"project_id": _PID, "entry_id": _ENTRY, "disposition": "removed"},
        key="disp-2",
    )
    assert len(_run_q(repo, "SELECT payload FROM events WHERE type = 'backlog.dispositioned'")) == 1, (
        "an already-disposed entry is refused before a second event (§1b #16 precondition)"
    )

    _seed_backlog_entry(repo, _ENTRY2)
    promote = _refused(
        service,
        "dispose_backlog",
        {"project_id": _PID, "entry_id": _ENTRY2, "disposition": "promoted"},
        key="disp-3",
    )
    assert promote.reason == "validation_failed", (
        f"promoted requires a non-empty target version; got {promote.reason}"
    )


# AC-FR0342-01@v0.11 TRACKS-TRACE IF-GATEUI-001 gate kinds awaiting preflight
def test_record_triage_and_review_awaiting_preflight(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo)
    service = _service(home)
    _seed_run(repo, "run-idle", awaiting_triage=False)
    _seed_run(repo, "run-tri", awaiting_triage=True)

    idle_triage = _refused(service, "record_triage",
                           {"run_id": "run-idle", "decision": "go"}, key="g-1")
    assert idle_triage.reason == "awaiting_mismatch", (
        f"a non-awaiting run refuses record_triage; got {idle_triage.reason}"
    )
    idle_review = _refused(service, "record_review",
                           {"run_id": "run-idle", "action": "comment"}, key="g-2")
    assert idle_review.reason == "awaiting_mismatch"
    wrong_gate = _refused(service, "record_review",
                          {"run_id": "run-tri", "action": "comment"}, key="g-3")
    assert wrong_gate.reason == "awaiting_mismatch", (
        "review requires awaiting in {review, review_pending_threads}; a triage gate"
        " is a mismatch — no dangling gate events"
    )

    bad_decision = _refused(service, "record_triage",
                            {"run_id": "run-tri", "decision": "maybe"}, key="g-4")
    assert bad_decision.reason == "validation_failed", (
        f"the triage decision set is closed; got {bad_decision.reason}"
    )

    receipt = _accept(service, "record_triage", {"run_id": "run-tri", "decision": "go"},
                      key="g-5")
    assert receipt.status == "accepted"
    assert _q(
        home, "SELECT status FROM commands WHERE kind = 'record_triage' AND run_id = 'run-tri'"
    ) == [("accepted",)], "the accepted gate command is persisted for the worker claim (§1j.3)"


# AC-FR0339-01@v0.11 TRACKS-TRACE IF-MSTART-001 develop delegates create_run
def test_decide_intake_develop_delegates_create_run_with_title_and_form(tmp_path: Path):
    repo = git_repo(tmp_path)
    git(repo, "branch", "releases/v0.2")  # merged at HEAD: no unmerged gate fires
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo, version="v0.2")
    _schedule_row(home, None)
    _intake_row(home, "I-dev", content="raw seed 一段话")
    service = _service(home)

    receipt = _accept(service, "decide_intake", {"intake_id": "I-dev", "action": "develop"},
                      key="dev-1")

    assert receipt.status == "accepted"
    runs = _q(home, "SELECT params_json FROM commands WHERE kind = 'create_run'")
    assert len(runs) == 1, "develop delegates to exactly one create_run acceptance (§1g.1.d)"
    params = json.loads(runs[0][0])
    assert params["journey"] == "feature"
    assert params["version"] == "v0.3", (
        f"the version derives from the greatest existing release minor + 1; got {params.get('version')}"
    )
    assert params["title"] == "Scribe 建议标题", "the Scribe title rides the delegation (§1g.1.d)"
    assert params["story_form"] == "raw", "the classified input form rides the delegation"


# AC-FR0339-02@v0.11 TRACKS-TRACE IF-MSTART-001 active-run and dirty-tree gates
def test_decide_intake_develop_gates(tmp_path: Path):
    repo = git_repo(tmp_path)
    home = tmp_path / "home"
    sdb.ServiceDB(home)
    _project_row(home, repo, version="v0.2")
    _schedule_row(home, "run-live")
    _seed_run(repo, "run-live", awaiting_triage=False)
    _intake_row(home, "I-active", content="活跃期输入")
    service = _service(home)

    receipt = _accept(service, "decide_intake",
                      {"intake_id": "I-active", "action": "develop"}, key="dev-a")

    assert receipt.status == "accepted", "the active-run exit is a legal terminal, not a refusal"
    recorded = _run_q(repo, "SELECT payload FROM events WHERE type = 'backlog.recorded'")
    assert len(recorded) == 1, "the input is recorded into backlog instead of a new run"
    payload = json.loads(recorded[0][0])
    assert payload["reason"] == "active_run", (
        f"the backlog entry names the active-run cause; got {payload.get('reason')}"
    )
    assert payload["source"] == "text" and payload["intake_id"] == "I-active"
    assert payload["content"] == "活跃期输入"
    assert _q(home, "SELECT COUNT(*) FROM commands WHERE kind = 'create_run'") == [(0,)], (
        "no new run and no branch is created while another run is active (§1g.1.a)"
    )

    (repo / "scratch.txt").write_text("dirt\n", encoding="utf-8")
    _intake_row(home, "I-dirty", content="脏树输入")
    dirty = _refused(service, "decide_intake",
                     {"intake_id": "I-dirty", "action": "develop"}, key="dev-b")
    assert dirty.reason == "dirty_tree", (
        f"an unclean worktree refuses develop with the dirty_tree reason; got {dirty.reason}"
    )
    assert _q(home, "SELECT COUNT(*) FROM commands WHERE kind = 'create_run'") == [(0,)]


# AC-FR0338-02@v0.11 TRACKS-TRACE IF-INTAKE-001 classify raw vs complete
def test_classify_story_input_separates_raw_from_complete():
    fn = getattr(templating, "classify_story_input", None)
    if not callable(fn):
        raise AssertionError(
            "templating must define the pure classify_story_input(content) function"
            " (IF-INTAKE-001 §1f.4) — the machine classification is authoritative"
        )
    sections = "\n\n".join(
        (
            "## 1. 原始输入",
            "## 2. 用户意图",
            "## 3. 核心操作路径",
            "## 4. 行为种子",
            "## 5. 范围、约束与例外",
            "## 6. 开放产品决定",
            "## 7. 必要性与风险",
        )
    )
    complete = (
        "---\nenvelope: tracks-envelope:v2\nstory_id: S-001\ntitle: 一个标题\n"
        "created: 2026-10-11\nstatus: draft\nsha:\n---\n\n# S-001: 一个标题\n\n"
        f"{sections}\n"
    )
    assert fn(complete) == "complete", (
        "story frontmatter (story_id/title) plus the fixed section structure is a"
        " complete story — adopted verbatim, never wrapped into §1"
    )
    assert fn("v0.11 统一入口的一句话需求") == "raw", (
        "a plain input is a raw seed — rendered verbatim into the skeleton §1"
    )
    assert fn("---\nstory_id: S-009\ntitle: 只有头\n---\n\n无章节正文\n") == "raw", (
        "frontmatter without the fixed section structure is still a raw seed (both"
        " halves of the §1f.4 rule are required)"
    )


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 evaluation worker module exists
def test_intake_evaluation_worker_module_exposes_entry_points():
    try:
        from tracks.supervisor import intake as intake_mod
    except ImportError:
        raise AssertionError(
            "tracks/supervisor/intake.py must exist as the Scribe evaluation worker"
            " (IF-INTAKE-001 §1f.2: assignment materialization, report landing on"
            " the intakes row, evaluating-timeout reclaim)"
        ) from None
    public = [
        name
        for name in dir(intake_mod)
        if not name.startswith("_") and callable(getattr(intake_mod, name))
    ]
    assert public, "the worker module exposes its evaluation entry points"
