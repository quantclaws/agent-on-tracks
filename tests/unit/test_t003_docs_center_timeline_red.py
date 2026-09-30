"""T-003 RED: project-domain docs read models + timeline stage_order (IF-QUERY-001).

Devon-owned unit RED for the T-003 delivery slice
(``tracks/server/api_query.py`` + ``tracks/server/projections.py``; the
IF-DOCCENTER-001 / IF-DISCUSS-001 / IF-TIMELINE-001 server faces):

- project-domain docs tree (#31): numeric version-descending order with each
  ``v<M>.<m>-hotfix-<n>`` directory right after its baseline, the six-piece
  closed doc set (missing members absent, non-doc files ignored), per-version
  ``baseline.revision_digest`` recomputation, and ``editable_run_id`` = the
  newest non-terminal run of the version;
- versioned read (#32) and diff (#33) over the same revision semantics;
- discussions read model (#34): the server-side ``tracks/discuss`` parse
  projected onto ``thread_id/status/initiator/anchor_line/summary/entry_line/
  awaiting`` (the client never parses the protocol);
- timeline response (#11) gains the 13-stage ``stage_order`` (M-START first,
  M-REQ-APPROVAL between M-ACC and M-DESIGN), composed server-side from the
  kernel stage registry rather than hardcoded client-side;
- ``_DOC_FILES`` spans the six-piece set with ``design`` kept as the
  architecture alias, and ``EXTENSION_ROUTES`` declares #31-34 on the
  api_query side for the T-001 route-assembly seam.

The four read endpoints do not exist yet; every failing node guards that
missing-symbol state into a real ``AssertionError`` (no stub tokens, no
assembly errors).

AC: FR-0322/FR-0325/FR-0326 — TRACKS-TRACE IF-QUERY-001.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import paths
from tracks.baseline import revision_digest
from tracks.server import api_query
from tracks.server.redaction import SecretRedactor
from tracks.store import Store
from tracks.supervisor import db as sdb

_PID = "proj-1"
_VERSION = "v0.10"
_HOTFIX_VERSION = "v0.10-hotfix-1"
_PREVIOUS_VERSION = "v0.9"
_STAGE_ORDER = [
    "M-START",
    "M-STORY",
    "M-SPEC",
    "M-ACC",
    "M-REQ-APPROVAL",
    "M-DESIGN",
    "M-TEST",
    "M-IMPL",
    "M-VERIFY",
    "M-SECURITY",
    "M-RELEASE",
    "M-PUBLISH",
    "M-MILESTONE",
]
_DOC_FILES = {
    "story": "story.md",
    "spec": "spec.md",
    "acceptance": "acceptance.md",
    "architecture": "architecture.md",
    "interfaces": "interfaces.md",
    "test-plan": "test-plan.md",
    "design": "architecture.md",
}
_SPEC_TEXT = "# spec v0.10\n\nSPEC-BODY-MARKER\n"
_ARCHITECTURE_TEXT = "# architecture v0.10\n\nARCHITECTURE-BODY-MARKER\n"
# Inline discussions in the tracks/discuss canonical format: a resolved thread
# with a nested reply, an open root that asks @Aaron, and a reopen thread
# whose latest reply asks @Sage. Line numbers are load-bearing (locate hints).
_DISCUSS_SPEC_TEXT = "\n".join(
    [
        "# spec v0.10 discussions",
        "",
        "ANCHOR-RESOLVED",
        "",
        "> **Aaron [RESOLVED]:** RESOLVED-BODY",
        ">> **Sage:** ack",
        "",
        "ANCHOR-OPEN",
        "",
        "> **Sage [open]:** OPEN-BODY please answer @Aaron",
        "",
        "ANCHOR-REOPEN",
        "",
        "> **Prism [reopen]:** REOPEN-BODY",
        ">> **Archer:** REOPEN-REPLY @Sage please re-check",
    ]
)


# -- fixtures: real stores seeded through the documented contracts -----------


def _write_doc(repo: Path, version: str, name: str, text: str) -> Path:
    target = repo / ".tracks" / "projects" / version / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _write_version(repo: Path, version: str, docs: dict[str, str]) -> None:
    for name, text in docs.items():
        _write_doc(repo, version, name, text)


def _seed_runs(repo: Path, rows: list[tuple]) -> None:
    store = Store(paths.tracks_home(repo))
    try:
        store.conn.executemany(
            "INSERT OR REPLACE INTO runs (run_id, version, status, stage, substate,"
            " awaiting, updated_ts) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        store.conn.commit()
    finally:
        store.close()


def _seed_home(tmp_path: Path, runs: list[tuple] | None = None) -> tuple[Path, Path]:
    """service.db with the registered project + its repo (optional runs rows)."""
    repo = tmp_path / "repo"
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, "alice", "2026-09-30T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    if runs is not None:
        _seed_runs(repo, runs)
    return home, repo


def _seed_docs_tree(tmp_path: Path) -> tuple[Path, Path]:
    home, repo = _seed_home(
        tmp_path,
        runs=[
            ("run-old", _VERSION, "active", "M-IMPL", "DRAFT", None, "2026-09-29T08:00:00+00:00"),
            (
                "run-new",
                _VERSION,
                "awaiting_human",
                "M-IMPL",
                "RULING",
                "review",
                "2026-09-30T08:00:00+00:00",
            ),
            (
                "run-done",
                _VERSION,
                "completed",
                "M-MILESTONE",
                "SEALED",
                None,
                "2026-09-30T12:00:00+00:00",
            ),
            (
                "run-prev",
                _PREVIOUS_VERSION,
                "active",
                "M-IMPL",
                "DRAFT",
                None,
                "2026-09-30T09:00:00+00:00",
            ),
        ],
    )
    _write_version(
        repo,
        _VERSION,
        {
            "story.md": "# story\n",
            "spec.md": _SPEC_TEXT,
            "acceptance.md": "# acceptance\n",
            "architecture.md": _ARCHITECTURE_TEXT,
            "flow.md": "# flow (not a six-piece member)\n",
            "tasks.json": "{}\n",
        },
    )
    _write_version(
        repo,
        _HOTFIX_VERSION,
        {"story.md": "# story\n", "spec.md": "# spec\n", "acceptance.md": "# acceptance\n"},
    )
    _write_version(
        repo,
        _PREVIOUS_VERSION,
        {
            "story.md": "# story v0.9\n",
            "spec.md": "# spec v0.9\n",
            "acceptance.md": "# acceptance v0.9\n",
            "interfaces.md": "# interfaces v0.9\n",
        },
    )
    return home, repo


def _seed_read_fixture(tmp_path: Path) -> tuple[Path, Path]:
    home, repo = _seed_home(tmp_path)
    _write_version(
        repo,
        _VERSION,
        {
            "story.md": "# story\n",
            "spec.md": _SPEC_TEXT,
            "acceptance.md": "# acceptance\n",
            "architecture.md": _ARCHITECTURE_TEXT,
        },
    )
    return home, repo


def _seed_discussions_fixture(tmp_path: Path) -> tuple[Path, Path]:
    home, repo = _seed_home(tmp_path)
    _write_version(
        repo,
        _VERSION,
        {
            "story.md": "# story\n",
            "spec.md": _DISCUSS_SPEC_TEXT,
            "acceptance.md": "# acceptance\n",
        },
    )
    return home, repo


def _seed_timeline_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    home = tmp_path / "service"
    db = sdb.ServiceDB(home)
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, "alice", "2026-09-30T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    db.append_event(
        "command.accepted", {"kind": "create_run"}, project_id=_PID, run_id="run-timeline"
    )
    return home


# -- the handler seam: request double + response extraction ------------------


class _Request:
    """Duck-typed request exposing exactly the api_query handler seam."""

    def __init__(
        self,
        home: Path,
        *,
        path_params: dict | None = None,
        query: dict | None = None,
    ) -> None:
        state = SimpleNamespace(home=home, redactor=SecretRedactor({}))
        self.app = SimpleNamespace(state=state)
        self.path_params = dict(path_params or {})
        self.query_params = dict(query or {})


def _handler(name: str):
    handler = getattr(api_query, name, None)
    assert callable(handler), (
        f"api_query.{name} must be declared for the T-003 project-domain read"
        " models (§2b #31-34)"
    )
    return handler


def _call(name: str, request: _Request) -> tuple[int, Any]:
    """Call one read handler; a stub token becomes a real assertion failure."""
    try:
        result = asyncio.run(_handler(name)(request))
    except NotImplementedError as exc:
        raise AssertionError(f"{name} is still a stub: {exc}") from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), (
        f"{name} must return api_query.QueryResponse(status_code, payload)"
    )
    return status, getattr(result, "payload", None)


def _error_reason(payload: Any) -> str:
    assert isinstance(payload, dict) and "error" in payload, (
        f"expected the unified error envelope, got {payload!r}"
    )
    return payload["error"]["reason"]


def _iso_or_fail(value: Any, label: str) -> None:
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise AssertionError(f"{label} must be an ISO-8601 timestamp: {value!r}") from None


def _worktree_digest(repo: Path, home: Path) -> str:
    """Content digest of the project docs + the service event count."""
    files = [
        (path.relative_to(repo).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted((repo / ".tracks" / "projects").rglob("*"))
        if path.is_file()
    ]
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        events = conn.execute("SELECT COUNT(*) FROM service_events").fetchone()[0]
    finally:
        conn.close()
    raw = json.dumps({"files": files, "service_events": events}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# -- extension routes + doc domain (§2b #31-34, §1n.4) -----------------------


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 extension routes declare #31-34
def test_extension_routes_declare_project_docs_endpoints():
    routes = getattr(api_query, "EXTENSION_ROUTES", None)
    assert isinstance(routes, (list, tuple)) and routes, (
        "api_query.EXTENSION_ROUTES must declare the §2b #31-34 read endpoints"
        " for the create_app route-assembly seam (T-001 CORE-02)"
    )
    for item in routes:
        assert isinstance(item, (list, tuple)) and len(item) == 3, (
            f"extension route entries are (path, handler, methods): {item!r}"
        )
    methods_by_path = {path: tuple(methods) for path, _bound, methods in routes}
    assert set(methods_by_path) == {
        "/api/projects/{pid}/docs/tree",
        "/api/projects/{pid}/docs/{version}/{doc}",
        "/api/projects/{pid}/docs/{version}/{doc}/diff",
        "/api/projects/{pid}/docs/{version}/{doc}/discussions",
    }, "EXTENSION_ROUTES must carry exactly the §2b #31-34 docs endpoints"
    for path, handler, _methods in routes:
        assert callable(handler), f"{path} must bind the api_query read handler"
        assert methods_by_path[path] == ("GET",), f"{path} is a read-only GET endpoint"


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 six-piece closed set + design alias
def test_doc_files_cover_six_piece_with_design_alias():
    assert api_query._DOC_FILES == _DOC_FILES, (
        "the doc parameter domain is the six-piece set with `design` kept as"
        " the architecture alias (§1n.4)"
    )


# -- docs tree (GET /api/projects/{pid}/docs/tree) ---------------------------


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 tree: version desc, six-piece, editable run
def test_docs_tree_versions_desc_six_piece_and_editable_run(tmp_path: Path):
    home, repo = _seed_docs_tree(tmp_path)

    status, payload = _call("docs_tree", _Request(home, path_params={"pid": _PID}))

    assert status == 200
    versions = payload.get("versions") if isinstance(payload, dict) else None
    assert isinstance(versions, list) and versions, "the tree lists version entries"
    order = [entry["version"] for entry in versions]
    assert order == [_VERSION, _HOTFIX_VERSION, _PREVIOUS_VERSION], (
        "versions are ordered by numeric components descending, with each"
        " `v<M>.<m>-hotfix-<n>` directory right after its baseline (§1n.1)"
    )
    by_version = {entry["version"]: entry for entry in versions}
    expected_docs = {
        _VERSION: {"story", "spec", "acceptance", "architecture"},
        _HOTFIX_VERSION: {"story", "spec", "acceptance"},
        _PREVIOUS_VERSION: {"story", "spec", "acceptance", "interfaces"},
    }
    for version, entry in by_version.items():
        assert set(entry) >= {"version", "docs", "editable_run_id"}
        docs = entry["docs"]
        assert {doc["doc"] for doc in docs} == expected_docs[version], (
            f"{version} docs must be exactly the existing six-piece members"
        )
        revision = revision_digest(repo / ".tracks" / "projects" / version)
        for doc in docs:
            assert set(doc) >= {"doc", "revision", "updated_at"}
            assert doc["revision"] == revision, (
                f"{version}/{doc['doc']} carries the version directory revision"
            )
            _iso_or_fail(doc["updated_at"], f"{version}/{doc['doc']} updated_at")
    assert by_version[_VERSION]["editable_run_id"] == "run-new", (
        "editable_run_id is the newest non-terminal run of the version"
    )
    assert by_version[_HOTFIX_VERSION]["editable_run_id"] is None
    assert by_version[_PREVIOUS_VERSION]["editable_run_id"] == "run-prev"


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 unknown project -> 404
def test_docs_tree_unknown_project_is_not_found(tmp_path: Path):
    home, _repo = _seed_docs_tree(tmp_path)

    status, payload = _call("docs_tree", _Request(home, path_params={"pid": "ghost"}))

    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- versioned read + diff (GET .../docs/{version}/{doc}[/diff]) -------------


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 versioned read: content/revision/history
def test_project_doc_read_returns_content_revision_and_history(tmp_path: Path):
    home, repo = _seed_read_fixture(tmp_path)
    vdir = repo / ".tracks" / "projects" / _VERSION

    status, payload = _call(
        "read_project_doc",
        _Request(home, path_params={"pid": _PID, "version": _VERSION, "doc": "spec"}),
    )

    assert status == 200
    assert set(payload) >= {"revision", "content", "history"}
    assert payload["revision"] == revision_digest(vdir), (
        "the versioned read carries the version directory revision (§1n.2)"
    )
    assert payload["content"] == _SPEC_TEXT
    history = payload["history"]
    assert isinstance(history, list) and history
    for entry in history:
        assert set(entry) >= {"revision", "ts", "actor"}
    assert payload["revision"] in {entry["revision"] for entry in history}


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 design alias + 404 targets
def test_project_doc_read_design_alias_and_unknown_targets(tmp_path: Path):
    home, _repo = _seed_read_fixture(tmp_path)
    params = {"pid": _PID, "version": _VERSION, "doc": "design"}

    status, payload = _call("read_project_doc", _Request(home, path_params=params))
    assert status == 200
    assert payload["content"] == _ARCHITECTURE_TEXT, "design aliases architecture.md"

    status, payload = _call(
        "read_project_doc",
        _Request(home, path_params={"pid": _PID, "version": _VERSION, "doc": "notes"}),
    )
    assert status == 404
    assert _error_reason(payload) == "not_found"

    status, payload = _call(
        "read_project_doc",
        _Request(home, path_params={"pid": _PID, "version": "v1.0", "doc": "spec"}),
    )
    assert status == 404
    assert _error_reason(payload) == "not_found"


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 versioned diff semantics
def test_project_doc_diff_same_revision_empty_and_validation(tmp_path: Path):
    home, repo = _seed_read_fixture(tmp_path)
    revision = revision_digest(repo / ".tracks" / "projects" / _VERSION)
    params = {"pid": _PID, "version": _VERSION, "doc": "spec"}

    status, payload = _call(
        "project_doc_diff",
        _Request(home, path_params=params, query={"from": revision, "to": revision}),
    )
    assert status == 200
    assert (payload.get("from"), payload.get("to")) == (revision, revision)
    assert payload.get("unified_diff") == "", "identical revisions diff to nothing"

    status, payload = _call(
        "project_doc_diff", _Request(home, path_params=params, query={"from": revision})
    )
    assert status == 422, "diff requires both from and to"
    assert _error_reason(payload) == "validation_failed"

    status, payload = _call(
        "project_doc_diff",
        _Request(home, path_params=params, query={"from": "0" * 64, "to": revision}),
    )
    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- discussions read model (GET .../docs/{version}/{doc}/discussions) ------


# AC-FR0325-01@v0.10 TRACKS-TRACE IF-QUERY-001 discussions server-side projection
def test_project_doc_discussions_thread_projection(tmp_path: Path):
    home, _repo = _seed_discussions_fixture(tmp_path)

    status, payload = _call(
        "project_doc_discussions",
        _Request(home, path_params={"pid": _PID, "version": _VERSION, "doc": "spec"}),
    )

    assert status == 200
    threads = payload.get("threads") if isinstance(payload, dict) else None
    assert isinstance(threads, list) and len(threads) == 3, (
        "the server-side tracks/discuss parse yields one entry per thread (§1p.1)"
    )
    for thread in threads:
        assert set(thread) >= {
            "thread_id",
            "status",
            "initiator",
            "anchor_line",
            "summary",
            "entry_line",
            "awaiting",
        }
    resolved, open_thread, reopen = threads
    assert resolved["thread_id"] == "T-001"
    assert (resolved["status"], resolved["initiator"]) == ("resolved", "Aaron")
    assert (resolved["anchor_line"], resolved["entry_line"]) == (3, 5)
    assert resolved["summary"] == "RESOLVED-BODY"
    assert resolved["awaiting"] is None, "no pending @request in the resolved thread"
    assert open_thread["thread_id"] == "T-002"
    assert (open_thread["status"], open_thread["initiator"]) == ("open", "Sage")
    assert (open_thread["anchor_line"], open_thread["entry_line"]) == (8, 10)
    assert open_thread["summary"] == "OPEN-BODY please answer @Aaron"
    assert open_thread["awaiting"] == "Aaron", "the root @request asks Aaron to answer"
    assert reopen["thread_id"] == "T-003"
    assert (reopen["status"], reopen["initiator"]) == ("reopen", "Prism")
    assert (reopen["anchor_line"], reopen["entry_line"]) == (12, 14)
    assert reopen["summary"] == "REOPEN-BODY"
    assert reopen["awaiting"] == "Sage", "the latest reply @request asks Sage to answer"


# AC-FR0325-01@v0.10 TRACKS-TRACE IF-QUERY-001 unknown discussions target -> 404
def test_project_doc_discussions_unknown_target_is_not_found(tmp_path: Path):
    home, _repo = _seed_discussions_fixture(tmp_path)

    status, payload = _call(
        "project_doc_discussions",
        _Request(home, path_params={"pid": _PID, "version": _VERSION, "doc": "notes"}),
    )

    assert status == 404
    assert _error_reason(payload) == "not_found"


# -- timeline stage_order (GET /api/runs/{run_id}/timeline) ------------------


# AC-FR0326-01@v0.10 TRACKS-TRACE IF-QUERY-001 timeline gains the 13-stage order
def test_timeline_extends_stage_order_13_stages(tmp_path: Path):
    home = _seed_timeline_fixture(tmp_path)

    status, payload = _call("timeline", _Request(home, path_params={"run_id": "run-timeline"}))

    assert status == 200
    assert payload.get("cursor"), "the existing timeline schema is unchanged"
    assert payload.get("stage_order") == _STAGE_ORDER, (
        "the timeline response carries the server-composed 13-stage display"
        " order: M-START first, M-REQ-APPROVAL between M-ACC and M-DESIGN"
        " (§1q.1) — the client never hardcodes the stage set"
    )


# -- read-only discipline ----------------------------------------------------


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 project docs reads leave stores untouched
def test_project_docs_read_models_are_read_only(tmp_path: Path):
    home, repo = _seed_discussions_fixture(tmp_path)
    revision = revision_digest(repo / ".tracks" / "projects" / _VERSION)
    before = _worktree_digest(repo, home)
    calls = (
        ("docs_tree", {"pid": _PID}, None),
        ("read_project_doc", {"pid": _PID, "version": _VERSION, "doc": "spec"}, None),
        (
            "project_doc_diff",
            {"pid": _PID, "version": _VERSION, "doc": "spec"},
            {"from": revision, "to": revision},
        ),
        ("project_doc_discussions", {"pid": _PID, "version": _VERSION, "doc": "spec"}, None),
    )
    for name, path_params, query in calls:
        status, _payload = _call(name, _Request(home, path_params=path_params, query=query))
        assert status == 200, f"{name} must serve the read-only projection"

    assert _worktree_digest(repo, home) == before, "the read models never write"
