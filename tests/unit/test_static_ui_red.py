"""T-004 RED: Releases/时间线读模型与静态视图合同（IF-AWAIT-001 承接面）.

Devon-owned unit RED for the T-004 delivery slice
(``tracks/server/projections.py`` + ``api_query.py`` + the
releases/sidebar/views/timeline static view layer):

- run detail 追加字段 ``awaiting_gate``（interfaces §1k.1 字段组 + §1k.2
  type→primary_action 封闭映射；非 awaiting 为 null）——Review 承接的投影
  半边（FR-0334/FR-0343）；
- run detail 追加字段 ``return_options``（§1m.1：与 CLI ``trac return`` 同一
  合法性规则共享计算 targets/already_executed；targets 空即无回拨入口）；
- ``GET /api/projects/{pid}/releases``（§2b #36）三节聚合：summary 摘要行 +
  版本分组注册表 + backlog 事件序派生（§1d.3 去向可查）+ 归档服务面分储；
- ``GET /api/projects/{pid}/intakes``（#37）与 ``.../intakes/{id}``（#38）
  intake 读模型；
- ``GET /api/projects/{pid}/hotfix-preview``（#44）的参数闭合校验面；
- 静态视图层（无 Node 工具链，architecture §4.3——unit 层按 T-005 先例钉
  module exit contracts）：releases.js 三组视图/摘要行/backlog 转正/hotfix
  入口；timeline.js 五态 data-state 双通道、attempt 独立节点、回边、固定
  详情栏（detail-pane 非浮层）、awaiting 动作面板（§1k）、回拨指针（§1m）；
  views.js 的 releases 窗口注册（五 tab 世界，无 review 视图）。

RED discipline: the read endpoints/fields do not exist yet and releases.js is
absent, so every node guards the missing state into a real ``AssertionError``
(missing-symbol asserts, file-read guards, token/structural asserts — no
collection/import errors, no stub tokens, no assembly errors). Behavioural
verification of the browser journeys stays with the frozen ui e2e layer.

AC: FR-0333/FR-0334/FR-0335/FR-0336/FR-0337/FR-0341/FR-0343 — TRACKS-TRACE
IF-AWAIT-001, IF-GATEUI-001（承接半边）+ IF-RELEASES-001 / IF-TIMELINE-002 /
IF-RETURN-001 / IF-HOTFIXUI-001 执行面镜像（acceptance 锚点归 T-009 收口）.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import paths
from tracks.server import api_query
from tracks.server.redaction import SecretRedactor
from tracks.store.store import Store
from tracks.supervisor import db as sdb

_PID = "proj-1"
_VERSION = "v0.11"
_APP = Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "app"

# §2b read endpoints this slice lands on the api_query extension seam (#36-38,
# #44) — create_app merges EXTENSION_ROUTES into the assembled route table.
_NEW_READ_PATHS = (
    "/api/projects/{pid}/releases",
    "/api/projects/{pid}/intakes",
    "/api/projects/{pid}/intakes/{intake_id}",
    "/api/projects/{pid}/hotfix-preview",
)


# -- fixtures: real stores seeded through the documented contracts -----------
#
# Every run is driven through the real kernel fold (Store.append + the closed
# event set), so the awaiting/return projections are exercised over genuinely
# reachable machine states — one minimal event sequence per gate form.


def _write_run(
    store: Store, run_id: str, version: str, events: list[tuple[str, dict]]
) -> None:
    for event_type, payload in events:
        store.append(run_id, version, event_type, payload)


def _register_project(home: Path, repo: Path, run_ids: list[str]) -> None:
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, "alice", "2026-10-11T00:00:00+00:00"),
        )
        for index, run_id in enumerate(run_ids):
            conn.execute(
                "INSERT INTO commands (command_id, kind, params_json, params_digest,"
                " idempotency_key, actor, actor_class, surface, project_id, run_id,"
                " status, claim_generation, result_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"cmd-{index}", "create_run", "{}", f"digest-{index}",
                    f"idem-{index}", "alice", "human", "http", _PID, run_id,
                    "completed", 1, "{}",
                    "2026-10-11T00:00:00+00:00", "2026-10-11T00:00:01+00:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_intakes(home: Path, rows: list[dict]) -> None:
    store = sdb.ServiceDB(home)
    for row in rows:
        store.insert_intake(row)


def _seed_home(
    tmp_path: Path,
    runs: dict[str, tuple[str, list[tuple[str, dict]]]],
    intakes: list[dict] | None = None,
) -> tuple[Path, Path]:
    """service.db project + commands rows over a real event-folded run plane."""
    repo = tmp_path / "repo"
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    _register_project(home, repo, list(runs))
    store = Store(paths.tracks_home(repo))
    try:
        for run_id, (version, events) in runs.items():
            _write_run(store, run_id, version, events)
    finally:
        store.close()
    if intakes:
        _seed_intakes(home, intakes)
    return home, repo


# (run_id, gate type, primary action, event sequence) — the §1k.2 closed
# mapping rows, each reachable through the real kernel (verified against the
# machine reducers: triage via M-STORY/TRIAGE outcome, review and its park
# member via lex.verdict, approval via preview.generated, AWAIT_RELEASE via
# release.previewed, escalation via run.breaker_tripped, rollback via an
# acceptance_defect Prism revise, hotfix_triage via the 3rd failed Sage
# outcome).
_GATE_FORMS: tuple[tuple[str, str, str, list[tuple[str, dict]]], ...] = (
    (
        "run-gate-triage",
        "triage",
        "triage",
        [("stage.entered", {"stage": "M-STORY"}), ("outcome.received", {"status": "done"})],
    ),
    (
        "run-gate-review",
        "review",
        "review",
        [("stage.entered", {"stage": "M-SPEC"}), ("lex.verdict", {"verdict": "pass"})],
    ),
    (
        "run-gate-review-park",
        "review_pending_threads",
        "review",
        [
            ("stage.entered", {"stage": "M-SPEC"}),
            ("lex.verdict", {"verdict": "pass-pending-human-threads"}),
        ],
    ),
    (
        "run-gate-approval",
        "approval",
        "approve",
        [
            ("stage.entered", {"stage": "M-REQ-APPROVAL"}),
            ("preview.generated", {"digest": "d" * 64}),
        ],
    ),
    (
        "run-gate-release",
        "AWAIT_RELEASE",
        "release-decision",
        [
            ("stage.entered", {"stage": "M-RELEASE"}),
            ("release.previewed", {"preview_digest": "p" * 64}),
        ],
    ),
    (
        "run-gate-escalation",
        "escalation",
        "retry",
        [
            ("stage.entered", {"stage": "M-IMPL"}),
            ("run.breaker_tripped", {"condition": "stalled", "report": "loss report"}),
        ],
    ),
    (
        "run-gate-rollback",
        "rollback",
        "return",
        [
            ("stage.entered", {"stage": "M-TEST"}),
            (
                "prism.verdict",
                {"verdict": "revise", "defect_classification": "acceptance_defect"},
            ),
        ],
    ),
    (
        "run-gate-hotfix",
        "hotfix_triage",
        "guidance",
        [
            ("stage.entered", {"stage": "M-HOTFIX-TRIAGE"}),
            (
                "outcome.received",
                {
                    "status": "failed",
                    "failure_class": "agent_error",
                    "attempt": 3,
                    "self_report": "anchor not found",
                },
            ),
        ],
    ),
)

_PLAIN_RUN = ("run-plain", _VERSION, [("stage.entered", {"stage": "M-IMPL"})])


def _gate_runs() -> dict[str, tuple[str, list[tuple[str, dict]]]]:
    runs = {_PLAIN_RUN[0]: (_PLAIN_RUN[1], _PLAIN_RUN[2])}
    for run_id, _type, _action, events in _GATE_FORMS:
        runs[run_id] = (_VERSION, events)
    return runs


def _seed_gate_home(tmp_path: Path) -> Path:
    home, _repo = _seed_home(tmp_path / "gates", _gate_runs())
    return home


# -- the handler seam: request double + response extraction -----------------


def _request(
    home: Path, path_params: dict | None = None, query: dict | None = None
) -> SimpleNamespace:
    """Build a request double for the api_query handler seam.

    The handlers read ``request.app.state.home`` / ``.redactor`` plus the
    path/query param dicts; a nested SimpleNamespace satisfies that seam
    without importing the server app factory.
    """
    state = SimpleNamespace(home=home, redactor=SecretRedactor({}))
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        path_params=dict(path_params or {}),
        query_params=dict(query or {}),
    )


def _route_table() -> dict[str, tuple[Any, tuple[str, ...]]]:
    """EXTENSION_ROUTES as {path: (handler, methods)}; shape asserted."""
    routes = getattr(api_query, "EXTENSION_ROUTES", None)
    assert isinstance(routes, (list, tuple)) and routes, (
        "api_query.EXTENSION_ROUTES must carry the §2b read endpoints for the"
        " create_app route-assembly seam"
    )
    table: dict[str, tuple[Any, tuple[str, ...]]] = {}
    for entry in routes:
        assert isinstance(entry, (list, tuple)) and len(entry) == 3, (
            f"extension route entries are (path, handler, methods): {entry!r}"
        )
        table[entry[0]] = (entry[1], tuple(entry[2]))
    return table


def _handler(path: str) -> Any:
    entry = _route_table().get(path)
    assert entry is not None, (
        f"{path} must be declared on api_query.EXTENSION_ROUTES (interfaces §2b)"
    )
    handler, methods = entry
    assert callable(handler), f"{path} must bind the api_query read handler"
    assert methods == ("GET",), f"{path} is a read-only GET endpoint"
    return handler


def _call(path: str, request: SimpleNamespace) -> tuple[int, Any]:
    """Call one read handler; a stub token becomes a real assertion failure."""
    try:
        result = asyncio.run(_handler(path)(request))
    except NotImplementedError as exc:
        raise AssertionError(f"{path} is still a stub: {exc}") from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), f"{path} must return QueryResponse(status, payload)"
    return status, getattr(result, "payload", None)


def _run_detail(home: Path, run_id: str) -> dict:
    request = _request(home, path_params={"run_id": run_id})
    detail = getattr(api_query, "run_detail", None)
    assert callable(detail), "api_query.run_detail serves GET /api/runs/{run_id} (§2b #10)"
    result = asyncio.run(detail(request))
    status = getattr(result, "status_code", None)
    assert status == 200, f"run_detail must serve the snapshot for {run_id}"
    payload = getattr(result, "payload", None)
    assert isinstance(payload, dict), "run_detail returns the JSON payload envelope"
    return payload


def _error_reason(payload: Any) -> str:
    assert isinstance(payload, dict) and "error" in payload, (
        f"expected the unified error envelope, got {payload!r}"
    )
    return payload["error"]["reason"]


# -- extension routes (§2b #36-38, #44) ---------------------------------------


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-RELEASES-001 extension routes declare the read endpoints
def test_extension_routes_declare_v011_read_endpoints():
    table = _route_table()
    for path in _NEW_READ_PATHS:
        assert path in table, (
            f"EXTENSION_ROUTES must declare {path} (interfaces §2b #36-38/#44)"
        )
        handler, methods = table[path]
        assert callable(handler), f"{path} must bind the api_query read handler"
        assert methods == ("GET",), f"{path} is a read-only GET endpoint"


# -- awaiting_gate field group + closed mapping (§1k, FR-0334/FR-0343) --------


# AC-FR0334-02@v0.11 TRACKS-TRACE IF-AWAIT-001 every gate type maps to a non-empty primary action
def test_run_detail_appends_awaiting_gate_closed_mapping(tmp_path: Path):
    home = _seed_gate_home(tmp_path)

    for run_id, gate_type, primary_action, _events in _GATE_FORMS:
        gate = _run_detail(home, run_id).get("awaiting_gate")
        assert isinstance(gate, dict), (
            f"{run_id} must carry the run-detail awaiting_gate field group"
            " (interfaces §1k.1; the field is currently absent)"
        )
        assert set(gate) == {"type", "reason", "impact", "primary_action"}, (
            f"{run_id} awaiting_gate is the closed field group of §1k.1,"
            f" got {sorted(gate)}"
        )
        assert gate["type"] == gate_type, (
            f"{run_id} awaiting_gate.type must fold the machine gate as"
            f" {gate_type!r}, got {gate['type']!r}"
        )
        assert gate["primary_action"] == primary_action, (
            f"{run_id} {gate_type!r} must map to primary_action"
            f" {primary_action!r} (§1k.2 closed mapping), got"
            f" {gate['primary_action']!r}"
        )
        for field in ("reason", "impact"):
            value = gate[field]
            assert isinstance(value, str) and value.strip(), (
                f"{run_id} awaiting_gate.{field} must be a non-empty readable"
                " string derived from run-plane facts (§1k.1)"
            )


# AC-FR0343-02@v0.11 TRACKS-TRACE IF-AWAIT-001 non-awaiting nodes carry no gate entry
def test_run_detail_awaiting_gate_is_null_when_not_awaiting(tmp_path: Path):
    home = _seed_gate_home(tmp_path)

    detail = _run_detail(home, _PLAIN_RUN[0])
    assert "awaiting_gate" in detail, (
        "awaiting_gate is an appended run-detail field — the key itself rides"
        " every detail snapshot (§2b #10 追加式)"
    )
    assert detail["awaiting_gate"] is None, (
        "a run awaiting nothing projects awaiting_gate=null — the lower pane"
        " renders node detail only, never a gate action entry (§1k.1/§1k.2)"
    )


# -- return_options field group (§1m, FR-0337) --------------------------------


# AC-FR0337-01@v0.11 TRACKS-TRACE IF-RETURN-001 return_options share the CLI legality rule
def test_run_detail_return_options_share_cli_legality(tmp_path: Path):
    home = _seed_gate_home(tmp_path)

    plain = _run_detail(home, _PLAIN_RUN[0]).get("return_options")
    assert isinstance(plain, dict) and set(plain) == {"allowed", "targets"}, (
        "return_options is the §1m.1 field group {allowed, targets}"
    )
    assert plain["allowed"] is False and plain["targets"] == [], (
        "an active M-IMPL run not awaiting approval/escalation has no legal"
        " return target under the shared `trac return` rule — targets empty"
        " means no return entry at all (§1m.1)"
    )

    approval = _run_detail(home, "run-gate-approval").get("return_options")
    assert isinstance(approval, dict) and set(approval) == {"allowed", "targets"}, (
        "the approval gate run carries the same §1m.1 field group"
    )
    assert approval["allowed"] is True, "a legal return source is allowed"
    targets = approval["targets"]
    assert isinstance(targets, list) and targets, (
        "awaiting=approval at M-REQ-APPROVAL exposes its legal targets"
    )
    assert {target.get("stage") for target in targets} == {
        "M-STORY",
        "M-SPEC",
        "M-ACC",
    }, (
        "targets are the CLI's closed approval-return set (upstream requirement"
        " stages only — forward M-DESIGN never allowed)"
    )
    for target in targets:
        assert set(target) >= {"stage", "already_executed"}, (
            "each target names its stage and its already_executed list (§1m.1)"
        )
        assert target["already_executed"] == [], (
            "no irreversible publish operations ran in this fixture"
        )


# -- GET /api/projects/{pid}/releases (§2b #36, FR-0333) ----------------------


def _seed_releases_home(tmp_path: Path) -> tuple[Path, Path]:
    active_entry = "01ENTRYACTIVE0000000000000000"
    promoted_entry = "01ENTRYPROMOTED0000000000000"
    removed_entry = "01ENTRYREMOVED00000000000000"
    runs = {
        "run-new": ("v0.12", [("stage.entered", {"stage": "M-IMPL"})]),
        "run-old": (
            _VERSION,
            [
                ("stage.entered", {"stage": "M-MILESTONE"}),
                ("run.completed", {"terminal_state": "sealed"}),
            ],
        ),
        active_entry: (
            _VERSION,
            [("backlog.recorded", {"source": "text", "content": "CTL-99 fix noise"})],
        ),
        promoted_entry: (
            "v0.10",
            [("backlog.recorded", {"source": "issue", "content": "CTL-98 import"})],
        ),
        "01ENTRYDISPOSED00000000000A": (
            "v0.10",
            [
                (
                    "backlog.dispositioned",
                    {"entry": promoted_entry, "disposition": "promoted", "to": _VERSION},
                )
            ],
        ),
        removed_entry: (
            "v0.10",
            [("backlog.recorded", {"source": "backlog", "content": "CTL-97 dupe"})],
        ),
        "01ENTRYDISPOSED00000000000B": (
            "v0.10",
            [
                (
                    "backlog.dispositioned",
                    {"entry": removed_entry, "disposition": "removed", "to": None},
                )
            ],
        ),
    }
    intakes = [
        {
            "intake_id": "in-arch",
            "project_id": _PID,
            "source": "text",
            "content": "archived intake",
            "status": "decided",
            "decision": "archive",
            "decided_at": "2026-10-11T01:00:00+00:00",
            "created_at": "2026-10-11T00:00:00+00:00",
        },
        {
            "intake_id": "in-backlog",
            "project_id": _PID,
            "source": "text",
            "content": "backlogged intake",
            "status": "decided",
            "decision": "backlog",
            "decided_at": "2026-10-11T02:00:00+00:00",
            "created_at": "2026-10-11T00:00:00+00:00",
        },
    ]
    return _seed_home(tmp_path / "releases", runs, intakes)


def _releases_payload(home: Path, pid: str = _PID) -> Any:
    status, payload = _call(
        "/api/projects/{pid}/releases", _request(home, path_params={"pid": pid})
    )
    assert status == 200, "the releases aggregation serves the registered project"
    return payload


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-RELEASES-001 summary line + version-grouped registry
def test_releases_endpoint_groups_summary_and_version_registry(tmp_path: Path):
    home, repo = _seed_releases_home(tmp_path)

    payload = _releases_payload(home)

    assert isinstance(payload, dict), "§1e.2 aggregation object"
    summary = payload.get("summary")
    assert isinstance(summary, dict), "the summary row is the §1e.2 object"
    assert set(summary) == {"project_id", "registered_version", "repo_name"}, (
        "the summary line carries exactly the registered identity fields"
    )
    assert summary["project_id"] == _PID
    assert summary["registered_version"] == _VERSION
    assert summary["repo_name"] == repo.name, "repo_name names the registered repo"

    versions = payload.get("versions")
    assert isinstance(versions, list) and versions, "versions lists the registry groups"
    seeded = [
        group.get("version")
        for group in versions
        if group.get("version") in {"v0.12", _VERSION}
    ]
    assert seeded == ["v0.12", _VERSION], (
        "version groups order by numeric components descending (§1e.2)"
    )
    by_version = {group.get("version"): group for group in versions}
    runs_new = by_version["v0.12"].get("runs")
    runs_old = by_version[_VERSION].get("runs")
    assert isinstance(runs_new, list) and isinstance(runs_old, list), (
        "each version group carries its runs"
    )
    new_ids = {run.get("run_id") for run in runs_new}
    old_ids = {run.get("run_id") for run in runs_old}
    assert "run-new" in new_ids, "the active v0.12 run rides its version group"
    assert "run-old" in old_ids, "the completed v0.11 run rides its version group"
    for run in runs_new + runs_old:
        assert set(run) >= {"run_id", "stage", "status", "awaiting", "updated_ts"}, (
            "registry run rows carry the §1e.2 closed key set"
        )


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-RELEASES-001 unknown project fails closed
def test_releases_endpoint_unknown_project_is_not_found(tmp_path: Path):
    home, _repo = _seed_releases_home(tmp_path)

    status, payload = _call(
        "/api/projects/{pid}/releases",
        _request(home, path_params={"pid": "ghost"}),
    )

    assert status == 404
    assert _error_reason(payload) == "not_found"


# AC-FR0340-01@v0.11 TRACKS-TRACE IF-BACKLOG-001 backlog derives from events with queryable whereabouts
def test_releases_endpoint_derives_backlog_group_from_events(tmp_path: Path):
    home, _repo = _seed_releases_home(tmp_path)

    backlog = _releases_payload(home).get("backlog")

    assert isinstance(backlog, dict), "backlog is the §1e.2 {count, entries} group"
    entries = backlog.get("entries")
    assert isinstance(entries, list), "entries aggregates backlog.recorded by ULID"
    by_entry = {entry.get("entry_id"): entry for entry in entries}
    active = by_entry.get("01ENTRYACTIVE0000000000000000")
    promoted = by_entry.get("01ENTRYPROMOTED0000000000000")
    removed = by_entry.get("01ENTRYREMOVED00000000000000")
    assert active is not None, "an undispositioned entry stays in the group"
    assert set(active) >= {
        "entry_id",
        "version",
        "source",
        "content",
        "recorded_ts",
        "disposition",
        "to",
    }, "entries carry the §1e.2 closed key set"
    assert active["disposition"] is None, "an active entry is undispositioned"
    assert active["source"] == "text" and active["content"] == "CTL-99 fix noise"
    assert active["version"] == _VERSION
    assert promoted is not None and promoted["disposition"] == "promoted", (
        "a promoted entry keeps its whereabouts queryable (§1d.3)"
    )
    assert promoted["to"] == _VERSION, "the promoted-to version rides the entry"
    assert removed is not None and removed["disposition"] == "removed"
    assert backlog.get("count") == 1, (
        "the folded group counts only undispositioned entries (§1d.3 removal)"
    )


# AC-FR0333-02@v0.11 TRACKS-TRACE IF-RELEASES-001 archive group is distinct from backlog
def test_releases_endpoint_archive_group_reads_decided_intakes(tmp_path: Path):
    home, _repo = _seed_releases_home(tmp_path)

    archive = _releases_payload(home).get("archive")

    assert isinstance(archive, list), "archive is the §1e.2 service-plane group"
    by_id = {row.get("intake_id"): row for row in archive}
    assert "in-arch" in by_id, "the archive-decided intake is queryable"
    archived = by_id["in-arch"]
    assert set(archived) >= {"intake_id", "content", "decided_ts"}, (
        "archive rows carry the §1e.2 closed key set"
    )
    assert archived["content"] == "archived intake"
    assert "in-backlog" not in by_id, (
        "a backlog-decided intake never appears in the archive group — the two"
        " stores stay distinct (SM-01.2 vs SM-01.3)"
    )


# -- GET intakes (§2b #37/#38, FR-0338 read half) -----------------------------


def _seed_intakes_home(tmp_path: Path) -> Path:
    report = {
        "feasibility": {"verdict": "feasible", "blockers": []},
        "duplicates": {"found": False, "items": []},
        "conflicts": {"found": False, "pairs": []},
        "title": "seed story",
        "input_form": "raw",
        "summary": "fine",
    }
    rows = [
        {
            "intake_id": "in-eval",
            "project_id": _PID,
            "source": "text",
            "content": "evaluated input",
            "status": "evaluated",
            "report_json": json.dumps(report),
            "created_at": "2026-10-11T00:00:00+00:00",
        },
        {
            "intake_id": "in-sub",
            "project_id": _PID,
            "source": "issue",
            "content": "issue input",
            "issue": 7,
            "status": "submitted",
            "created_at": "2026-10-11T00:01:00+00:00",
        },
    ]
    home, _repo = _seed_home(tmp_path / "intakes", {"run-x": (_VERSION, [])}, rows)
    return home


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 intake list + status filter
def test_intakes_list_serves_rows_with_status_filter(tmp_path: Path):
    home = _seed_intakes_home(tmp_path)

    status, payload = _call(
        "/api/projects/{pid}/intakes", _request(home, path_params={"pid": _PID})
    )
    assert status == 200
    rows = payload.get("intakes") if isinstance(payload, dict) else None
    assert isinstance(rows, list) and {row.get("intake_id") for row in rows} == {
        "in-eval",
        "in-sub",
    }, "the list serves the project's intake rows"

    status, payload = _call(
        "/api/projects/{pid}/intakes",
        _request(home, path_params={"pid": _PID}, query={"status": "evaluated"}),
    )
    assert status == 200
    rows = payload.get("intakes") if isinstance(payload, dict) else None
    assert isinstance(rows, list) and [row.get("intake_id") for row in rows] == [
        "in-eval"
    ], "?status= filters the read model (§1f.3)"

    status, payload = _call(
        "/api/projects/{pid}/intakes",
        _request(home, path_params={"pid": "ghost"}),
    )
    assert status == 404
    assert _error_reason(payload) == "not_found"


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 intake detail with parsed report
def test_intake_detail_serves_report_and_fails_closed(tmp_path: Path):
    home = _seed_intakes_home(tmp_path)

    status, payload = _call(
        "/api/projects/{pid}/intakes/{intake_id}",
        _request(home, path_params={"pid": _PID, "intake_id": "in-eval"}),
    )
    assert status == 200
    assert set(payload) >= {
        "intake_id",
        "source",
        "status",
        "report",
        "decision",
        "created_ts",
    }, "the detail row carries the §1f.3 schema"
    assert payload["status"] == "evaluated"
    assert isinstance(payload["report"], dict) and payload["report"]["title"] == (
        "seed story"
    ), "report arrives as the parsed §1f.2 object"

    status, payload = _call(
        "/api/projects/{pid}/intakes/{intake_id}",
        _request(home, path_params={"pid": _PID, "intake_id": "ghost"}),
    )
    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- GET hotfix-preview (§2b #44, FR-0341 read half) --------------------------


# AC-FR0341-01@v0.11 TRACKS-TRACE IF-HOTFIXUI-001 scenario closed set + unknown project
def test_hotfix_preview_validates_scenario_and_project(tmp_path: Path):
    home = _seed_intakes_home(tmp_path)

    status, payload = _call(
        "/api/projects/{pid}/hotfix-preview",
        _request(home, path_params={"pid": "ghost"}, query={"issue": "7"}),
    )
    assert status == 404, "an unknown project fails closed"
    assert _error_reason(payload) == "not_found"

    status, _payload = _call(
        "/api/projects/{pid}/hotfix-preview",
        _request(
            home,
            path_params={"pid": _PID},
            query={"issue": "7", "scenario": "bogus"},
        ),
    )
    assert status == 422, (
        "scenario is the closed set {post-release, dev} — anything else is a"
        " validation failure (§1i.1)"
    )


# -- read-only discipline (NFR-0150 inherited; §2b read half) -----------------


def _stores_digest(repo: Path, home: Path) -> str:
    files = [
        path.relative_to(repo).as_posix() + ":" + hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((repo / ".tracks").rglob("*"))
        if path.is_file()
    ]
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        events = conn.execute("SELECT COUNT(*) FROM service_events").fetchone()[0]
        intakes = conn.execute("SELECT COUNT(*) FROM intakes").fetchone()[0]
    finally:
        conn.close()
    raw = json.dumps({"files": files, "events": events, "intakes": intakes})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# AC-NFR0158-01@v0.11 TRACKS-TRACE IF-QUERY-001 the new read models never write
def test_v011_read_endpoints_are_read_only(tmp_path: Path):
    home = _seed_intakes_home(tmp_path)
    repo = home.parent / "repo"
    before = _stores_digest(repo, home)

    _releases_payload(home)
    _call(
        "/api/projects/{pid}/intakes", _request(home, path_params={"pid": _PID})
    )
    _call(
        "/api/projects/{pid}/intakes/{intake_id}",
        _request(home, path_params={"pid": _PID, "intake_id": "in-eval"}),
    )
    _run_detail(home, "run-x")

    assert _stores_digest(repo, home) == before, (
        "the §2b read half leaves both stores untouched — no events, no row"
        " writes (IF-QUERY-001)"
    )


# -- static view layer: releases.js (IF-RELEASES-001 / IF-HOTFIXUI-001) ------


def _module(name: str) -> str:
    """Source of one native ES module; a missing module fails as an assertion."""
    path = _APP / name
    assert path.is_file(), (
        f"tracks/server/static/app/{name} must exist: the v0.11 view layer"
        " ships native ES modules with no build chain (interfaces §3 #7)"
    )
    return path.read_text(encoding="utf-8")


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-RELEASES-001 releases window: three groups + summary
def test_releases_module_renders_three_groups_and_summary():
    releases = _module("releases.js")
    assert "import " in releases and "export " in releases, (
        "releases.js is a native ES module (no build chain, §1l.2 inherited)"
    )
    assert "/api/projects/" in releases and "/releases" in releases, (
        "the releases view consumes the #36 aggregation projection — no second"
        " data source, no mock data (NFR-0158)"
    )
    assert "summary" in releases, (
        "the registered project/version identity renders as the window's"
        " summary row (E-01: 注册项目/版本信息仅为窗口内摘要行)"
    )
    assert "versions" in releases, (
        "the registry group is version-grouped (E-01 sidebar 三组之一)"
    )
    assert "backlog" in releases and "archive" in releases, (
        "the backlog folded group and the archive query group both render,"
        " visually distinct from each other (AC-FR0333-02)"
    )
    assert "data-testid" in releases, "interactive controls bind data-testid"


# AC-FR0340-02@v0.11 TRACKS-TRACE IF-RELEASES-001 backlog selection opens the promote entry
def test_releases_module_backlog_promotion_uses_intake_channel():
    releases = _module("releases.js")
    assert "/intakes" in releases and "backlog" in releases, (
        "selecting a backlog entry opens the promotion entry — it submits"
        " through the unified intake channel (source=backlog), never a"
        " second write path (SM-01.6)"
    )


# AC-FR0341-01@v0.11 TRACKS-TRACE IF-HOTFIXUI-001 hotfix entry: scenario + preview + anchoring
def test_releases_module_hotfix_entry_shows_preview_and_anchoring():
    releases = _module("releases.js")
    assert "hotfix-preview" in releases, (
        "the hotfix entry consumes the #44 read-only preview (§1i.1)"
    )
    for scenario in ("post-release", "dev"):
        assert scenario in releases, (
            f"the scenario closed set member {scenario!r} is selectable (§1i.1)"
        )
    assert "anchored" in releases and "unanchored" in releases, (
        "the preview's anchoring status renders both ways — an unanchored"
        " result explicitly exits hotfix (§1i.1)"
    )


# -- static view layer: timeline.js (IF-TIMELINE-002 / IF-AWAIT-001) ---------


# AC-FR0335-01@v0.11 TRACKS-TRACE IF-TIMELINE-002 five states + attempts + back edges
def test_timeline_module_renders_five_states_attempts_and_back_edges():
    timeline = _module("timeline.js")
    assert "data-state" in timeline, (
        "node state rides the data-state attribute — the machine-assertable"
        " channel beside the icon shape (§1l.1 双通道)"
    )
    for state in ("done", "active", "pending", "attention", "void"):
        assert re.search(rf'["\']{state}["\']', timeline), (
            f"the five-state closed set member {state!r} must be a renderable"
            " state value (§1l.1)"
        )
    assert "attempt" in timeline, (
        "every attempt renders as its own node — no folding (§1l.1)"
    )
    assert "stage.rolled_back" in timeline, (
        "back edges derive from the rollback event pair in the event order —"
        " a recognizable 来源→目标 edge (§1l.1)"
    )
    assert "stage_order" in timeline and "M-START" not in timeline, (
        "the 13-stage track still comes from the server stage_order — the"
        " client never hardcodes the stage set (§1l.1/§1l.3)"
    )


# AC-FR0336-02@v0.11 TRACKS-TRACE IF-TIMELINE-002 fixed detail pane replaces the overlay
def test_timeline_fixed_detail_pane_replaces_the_overlay():
    timeline = _module("timeline.js")
    assert "detail-pane" in timeline, (
        "the fixed lower pane carries data-testid=detail-pane (§1l.2)"
    )
    assert "timeline-node-overlay" not in timeline and "node-overlay" not in timeline, (
        "the v0.10 floating overlay is replaced by the fixed pane — no overlay"
        " remains (§0.1 #13 破坏性变更)"
    )
    assert "selected" in timeline and "active" in timeline, (
        "the pane distinguishes the selected node from the currently active"
        " node with two separate markers (§1l.2 双标记)"
    )
    assert "owning" in timeline, (
        "the pane carries the owning surface entry — the stable jump to the"
        " face that owns the node (§1l.2)"
    )
    assert "degradedMessage" in timeline or "degrade" in timeline, (
        "nodes without data degrade readably instead of fabricating (§1l.2)"
    )


# AC-FR0334-02@v0.11 TRACKS-TRACE IF-AWAIT-001 the pane is the awaiting action panel
def test_timeline_awaiting_panel_consumes_awaiting_gate():
    timeline = _module("timeline.js")
    assert "awaiting_gate" in timeline, (
        "the run detail's awaiting_gate field group drives the lower pane —"
        " awaiting nodes turn the pane into the gate action panel (§1k.1/§1l.2)"
    )
    for field in ("reason", "impact", "primary_action"):
        assert field in timeline, (
            f"the panel renders awaiting_gate.{field} (§1k.1 字段组)"
        )
    for action in (
        "triage",
        "review",
        "approve",
        "release-decision",
        "retry",
        "return",
        "guidance",
    ):
        assert re.search(rf'["\']{action}["\']', timeline), (
            f"the §1k.2 closed mapping member {action!r} has a panel face —"
            " 映射外无入口"
        )


# AC-FR0337-01@v0.11 TRACKS-TRACE IF-RETURN-001 return pointer gated by targets
def test_timeline_return_pointer_gated_by_return_options():
    timeline = _module("timeline.js")
    assert "return_options" in timeline and "targets" in timeline, (
        "the return pointer consumes the run detail's return_options —"
        " targets empty means no entry at all (§1m.1)"
    )
    assert "already_executed" in timeline, (
        "the confirm face lists the irreversible operations it would cross —"
        " never disguising them as undone (§1m.2)"
    )
    assert "confirm" in timeline and "/return" in timeline, (
        "the explicit confirmation posts to the existing #25 return endpoint"
        " with confirm (§1m.2)"
    )


# -- static view layer: views.js window registry (IF-RELEASES-001) -----------


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-WORKBENCH-001 releases window renders, review does not
def test_views_registry_maps_the_releases_window():
    views = _module("views.js")
    assert "releases" in views, (
        "the five-tab world routes the releases window through the view"
        " registry (§1e.1)"
    )
    assert "releases.js" in views, (
        "the releases window renders through the releases view module"
    )
    for gone in ("renderReview", "renderProjects", "renderRuns"):
        assert gone not in views, (
            f"{gone} belongs to the removed pre-v0.11 windows — Review is"
            " carried by the awaiting action panel and the projects/runs"
            " registries merge into Releases (§0.1 #1)"
        )
