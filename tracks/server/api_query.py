"""Read-only HTTP routes -> projections (interfaces §2b, §1e).

Every handler is a thin adapter: it resolves the request's path/query
parameters, reads through the §1e read model (``tracks/server/projections.py``)
or one of the read-only stores below, and returns a ``QueryResponse`` envelope
(HTTP status + JSON payload) that the app factory maps onto a Starlette
response. All reads use independent read-only SQLite connections (``mode=ro``),
never take the writer lock and emit no events (NFR-0150); every payload passes
through the ``SecretRedactor`` before it leaves the process (§1g.3).

Request seam (bound by ``create_app``, T-014):
    request.app.state.home      -> service home (holds service.db)
    request.app.state.redactor  -> SecretRedactor
    request.app.state.config    -> effective serve config (optional overlay)
    request.path_params         -> pid / run_id / command_id / doc
    request.query_params        -> timeline filters + cursor, diff pair

Material revisions (§2b #15-16): the doc revision is the approval-bound
baseline digest (``baseline.revision_digest``) — the same digest
``record_stage_approval`` binds. Revision history is read from the run's
``material.edited`` events; ``doc_diff`` resolves older contents from the
material-revision git commits, recomputing the trio digest at each commit.
``history``/``content`` are display data only (no identity is derived from
mtimes).

The project-domain docs faces (§2b #31-34, §1n/§1p) read the same revision
semantics from the version directory: tree, versioned read, diff and the
discussion threads are declared in ``EXTENSION_ROUTES`` for the composition
root (``create_app``) to merge into the route table.

Contract tokens: IF-QUERY-001, IF-SERVE-001, IF-WAIT-001, IF-PROJ-001,
IF-DOCREV-001, IF-WEBGATE-001, IF-CMDSVC-001, IF-SECRECY-001.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.baseline import revision_digest
from tracks.discuss.parser import parse_threads
from tracks.frontmatter import split_frontmatter
from tracks.kernel.events import EventEnvelope, event_envelope_from_row
from tracks.server import projections

# E-06 material set (§2b #15-16, §1n.4): the six-piece doc domain with
# `design` kept as the architecture alias. The mapping is owned by the
# projections read models (single source) and shared with the write face,
# which imports `_DOC_FILES` from here.
_DOC_FILES = projections.DOC_FILES
# baseline.revision_digest members: labelled story/spec/acc bodies.
_TRIO_FILES = ("story.md", "spec.md", "acceptance.md")
_TRIO_LABELS = ("story", "spec", "acc")
_CONFIG_KEYS = ("wait_initial_s", "wait_cap_s", "poll_interval_s", "poll_idle_cap_s", "lease_ttl_s")
_GIT_LOG_LIMIT = "200"


@dataclass(frozen=True)
class QueryResponse:
    """HTTP envelope of a read-only route: status code + JSON payload.

    Starlette-free on purpose: the server package stays importable without
    starlette (unit level); the app factory maps this onto a JSONResponse.
    """

    status_code: int
    payload: Any


# -- request seam + response/error helpers ----------------------------------


def _state(request: Any) -> Any:
    return request.app.state


def _home(request: Any) -> Path:
    return Path(_state(request).home)


def _redactor(request: Any) -> Any:
    return getattr(_state(request), "redactor", None)


def _response(status_code: int, request: Any, payload: Any) -> QueryResponse:
    redactor = _redactor(request)
    if redactor is not None:
        payload = redactor.redact_payload(payload)
    return QueryResponse(status_code, payload)


def _not_found(what: str, ident: str) -> QueryResponse:
    return QueryResponse(
        404, {"error": {"reason": "not_found", "detail": f"{what} {ident!r} is not registered"}}
    )


def _error(status_code: int, reason: str, detail: str) -> QueryResponse:
    return QueryResponse(status_code, {"error": {"reason": reason, "detail": detail}})


def _param(source: Any, name: str) -> str | None:
    value = source.get(name)
    return value if isinstance(value, str) and value else None


def _path_text(request: Any, name: str) -> str:
    """A route path parameter as text ('' when absent)."""
    return str(request.path_params.get(name, ""))


# -- read-only stores --------------------------------------------------------


def _ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _read_rows(path: Path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = _ro(path)
    if conn is None:
        return []
    with contextlib.closing(conn):
        return conn.execute(sql, args).fetchall()


def _json_dict(raw: object) -> dict:
    if raw is None:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_value(raw: object) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _service_db_path(home: Path) -> Path:
    return home / "service.db"


def _tracks_db_path(repo_path: str) -> Path:
    return Path(repo_path) / ".tracks" / "runtime" / "tracks.db"


def _project_rows(home: Path) -> list[dict]:
    rows = _read_rows(
        _service_db_path(home),
        "SELECT project_id, repo_path, version FROM projects ORDER BY project_id",
    )
    return [{"project_id": row[0], "repo_path": row[1], "version": row[2]} for row in rows]


def _project_for_run(home: Path, run_id: str) -> dict | None:
    """Registered project owning run_id (material revision read path).

    Same discovery order as the detail projections: service-plane rows
    (commands / service events) first — a run accepted through the command
    service is addressable before its run plane exists — then the project's
    run-plane ``runs`` table.
    """
    conn = projections._service_db(home)
    try:
        return projections._project_for_run(conn, run_id)
    finally:
        if conn is not None:
            conn.close()


def _service_events(home: Path, run_id: str, event_type: str) -> list[dict]:
    rows = _read_rows(
        _service_db_path(home),
        "SELECT seq, ts, payload FROM service_events WHERE run_id = ? AND type = ?"
        " ORDER BY seq",
        (run_id, event_type),
    )
    return [{"seq": row[0], "ts": row[1], "payload": _json_dict(row[2])} for row in rows]


def _command_row(home: Path, command_id: str) -> dict | None:
    rows = _read_rows(
        _service_db_path(home),
        "SELECT command_id, kind, status, result_json FROM commands WHERE command_id = ?",
        (command_id,),
    )
    if not rows:
        return None
    row = rows[0]
    return {"command_id": row[0], "kind": row[1], "status": row[2], "result_json": row[3]}


def _load_payload(raw: object, repo_path: str) -> dict:
    payload = _json_dict(raw)
    if set(payload) == {"$ref"}:
        name = paths.blob_name_from_ref(payload["$ref"])
        if name is None:
            return {}
        blob = Path(repo_path) / ".tracks" / "runtime" / "blobs" / name
        try:
            return _json_dict(blob.read_text(encoding="utf-8"))
        except OSError:
            return {}
    return payload


def _run_events(repo_path: str, run_id: str) -> list[EventEnvelope]:
    rows = _read_rows(
        _tracks_db_path(repo_path),
        "SELECT run_id, seq, ts, version, type, schema_version, command_id, task_id, payload"
        " FROM events WHERE run_id = ? ORDER BY seq",
        (run_id,),
    )
    return [event_envelope_from_row(row, _load_payload(row[8], repo_path)) for row in rows]


def _latest_readiness(home: Path) -> dict[str, dict]:
    rows = _read_rows(
        _service_db_path(home),
        "SELECT project_id, payload FROM service_events"
        " WHERE type = 'project.readiness_checked' ORDER BY seq",
    )
    latest: dict[str, dict] = {}
    for project_id, raw in rows:
        payload = _json_dict(raw)
        pid = project_id or payload.get("project_id")
        if isinstance(pid, str) and pid:
            latest[pid] = {"ok": bool(payload.get("ok")), "checks": payload.get("checks") or {}}
    return latest


# -- material revisions (docs API) -------------------------------------------


def _git(repo: Path, *args: str) -> str | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _relpath(path: Path, repo: Path) -> str | None:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return None


def _vdir(project: dict) -> Path:
    return Path(project["repo_path"]) / ".tracks" / "projects" / str(project["version"])


def _doc_file(project: dict, doc: str) -> Path | None:
    name = _DOC_FILES.get(doc)
    return _vdir(project) / name if name else None


def _doc_context(home: Path, run_id: str, doc: str) -> tuple[Path, Path, str] | None:
    """(repo, doc file, current revision digest) for a run/doc, or None."""
    project = _project_for_run(home, run_id)
    if project is None:
        return None
    doc_file = _doc_file(project, doc)
    if doc_file is None or not doc_file.exists():
        return None
    return Path(project["repo_path"]), doc_file, revision_digest(_vdir(project))


def _body_sha(text: str) -> str:
    _, body = split_frontmatter(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _trio_digest(texts: list[str]) -> str:
    """baseline.revision_digest over committed document bodies."""
    joined = "\n".join(
        f"{label}:{_body_sha(text)}" for label, text in zip(_TRIO_LABELS, texts, strict=True)
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _revision_contents(repo: Path, vdir: Path, doc_file: Path) -> dict[str, str]:
    """Revision digest -> doc content at each material-revision commit.

    The Runtime commits material revisions to the run's repo and approvals
    bind the trio digest, so each commit's digest is recomputed from the
    committed story/spec/acceptance bodies.
    """
    rel_dir = _relpath(vdir, repo)
    rel_doc = _relpath(doc_file, repo)
    if rel_dir is None or rel_doc is None:
        return {}
    log = _git(repo, "log", f"-n{_GIT_LOG_LIMIT}", "--format=%H", "--", rel_dir)
    if log is None:
        return {}
    contents: dict[str, str] = {}
    for sha in log.split():
        digest = _commit_trio_digest(repo, sha, rel_dir)
        if digest is None or digest in contents:
            continue
        content = _git(repo, "show", f"{sha}:{rel_doc}")
        if content is not None:
            contents[digest] = content
    return contents


def _commit_trio_digest(repo: Path, sha: str, rel_dir: str) -> str | None:
    """Trio digest of one commit's story/spec/acceptance bodies.

    None when the commit does not carry all three members (the revision
    predates the version dir or the docs were introduced later).
    """
    texts: list[str] = []
    for name in _TRIO_FILES:
        text = _git(repo, "show", f"{sha}:{rel_dir}/{name}")
        if text is None:
            return None
        texts.append(text)
    return _trio_digest(texts)


def _content_at(repo: Path, doc_file: Path, revision: str, current: str) -> str | None:
    if revision == current:
        return doc_file.read_text(encoding="utf-8")
    return _revision_contents(repo, doc_file.parent, doc_file).get(revision)


def _unified_doc_diff(doc: str, old: str, new: str) -> str:
    """Unified diff of one doc's two revision contents (display data only)."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{doc}",
            tofile=f"b/{doc}",
        )
    )


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _doc_history(home: Path, doc_file: Path, run_id: str, doc: str, current: str) -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    for event in _service_events(home, run_id, "material.edited"):
        payload = event["payload"]
        if payload.get("doc") != doc:
            continue
        actor = str(payload.get("actor") or "")
        for revision in (payload.get("from_revision"), payload.get("to_revision")):
            if isinstance(revision, str) and revision and revision not in seen:
                seen.add(revision)
                entries.append({"revision": revision, "ts": event["ts"], "actor": actor})
    if current not in seen:
        entries.append({"revision": current, "ts": _mtime_iso(doc_file), "actor": ""})
    return entries


# -- release preview ---------------------------------------------------------


def _preview_stale_reason(events: list[EventEnvelope], preview: EventEnvelope) -> str | None:
    """Closed stale set (§1g): drift/stale markers AFTER the active preview."""
    preview_sha = (preview.payload or {}).get("candidate_sha")
    for event in events:
        if event.seq <= preview.seq:
            continue
        payload = event.payload or {}
        if event.type == "candidate.stale":
            return "candidate_drift"
        if event.type == "evidence.staled":
            return "evidence_staled"
        if event.type == "candidate.frozen":
            fresh = payload.get("candidate_sha")
            if fresh and fresh != preview_sha:
                return "candidate_drift"
    return None


def _preview_summary(preview: dict) -> str:
    candidate = str(preview.get("candidate_sha") or "")
    digest = str(preview.get("preview_digest") or "")
    risks = preview.get("risks") or []
    issues = preview.get("known_issues") or []
    return (
        f"candidate={candidate} preview_digest={digest} "
        f"risks={len(risks)} known_issues={len(issues)}"
    )


# -- GET /healthz ------------------------------------------------------------


async def healthz(request: Any) -> Any:
    """GET /healthz (public): 200 {status, projects, version, uptime_s} | 503."""
    home = _home(request)
    try:
        from tracks.server.app import health_response

        payload = health_response(home)
    except (ImportError, NotImplementedError):
        payload = None
    if not isinstance(payload, dict):
        return _response(
            503,
            request,
            {
                "status": "unavailable",
                "reasons": [{"check": "health_probe", "reason": "health probe is not wired"}],
            },
        )
    return _response(200 if payload.get("status") == "ok" else 503, request, payload)


# -- GET /api/service/config -------------------------------------------------


async def service_config(request: Any) -> Any:
    """GET /api/service/config: effective wait/poll/lease config (§1e)."""
    payload = projections.project_service_config(_home(request))
    config = getattr(_state(request), "config", None)
    for key in _CONFIG_KEYS:
        value = config.get(key) if isinstance(config, dict) else getattr(config, key, None)
        if value is not None:
            payload[key] = value
    return _response(200, request, payload)


# -- GET /api/projects -------------------------------------------------------


async def list_projects(request: Any) -> Any:
    """GET /api/projects: registered projects with attribution (§2b #6)."""
    home = _home(request)
    readiness = _latest_readiness(home)
    payload = [
        {
            "project_id": row["project_id"],
            "repo_path": row["repo_path"],
            "version": row["version"],
            "readiness": readiness.get(row["project_id"], {"ok": False, "checks": {}}),
        }
        for row in _project_rows(home)
    ]
    return _response(200, request, payload)


# -- GET /api/projects/{pid}/overview ----------------------------------------


async def project_overview(request: Any) -> Any:
    """GET /api/projects/{pid}/overview: runs + human-todo count (§1e)."""
    home = _home(request)
    overview = projections.project_overview(home)
    pid = _param(request.path_params, "pid")
    if pid is not None and not any(row["project_id"] == pid for row in overview["projects"]):
        return _not_found("project", pid)
    return _response(200, request, overview)


# -- GET /api/runs/{run_id} --------------------------------------------------


async def run_detail(request: Any) -> Any:
    """GET /api/runs/{run_id}: detail snapshot incl. event_cursor (§2b #10)."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    detail = projections.project_run_detail(home, run_id)
    if detail is None:
        return _not_found("run", run_id)
    # Snapshot/live-stream consistency (IF-STREAM-001): the snapshot carries
    # the merged-stream cursor at its end, so SSE/poll consumers resume from
    # exactly the events the snapshot already reflects.
    detail["event_cursor"] = projections.project_timeline(home, run_id)["cursor"]
    return _response(200, request, detail)


# -- GET /api/runs/{run_id}/timeline -----------------------------------------


async def timeline(request: Any) -> Any:
    """GET /api/runs/{run_id}/timeline: filtered, cursor-paginated events."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    if projections.project_run_detail(home, run_id) is None:
        return _not_found("run", run_id)
    query = request.query_params
    payload = projections.project_timeline(
        home,
        run_id,
        command_id=_param(query, "command_id"),
        task_id=_param(query, "task_id"),
        ac_id=_param(query, "ac_id"),
        after_seq=_param(query, "after"),
    )
    return _response(200, request, payload)


# -- GET /api/runs/{run_id}/ac-chain -----------------------------------------


async def ac_chain(request: Any) -> Any:
    """GET /api/runs/{run_id}/ac-chain: AC -> test node -> result -> evidence."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    if projections.project_run_detail(home, run_id) is None:
        return _not_found("run", run_id)
    return _response(200, request, projections.project_ac_chain(home, run_id))


# -- GET /api/runs/{run_id}/todos and GET /api/todos -------------------------


async def run_todos(request: Any) -> Any:
    """GET /api/runs/{run_id}/todos: human decisions for this run only."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    if projections.project_run_detail(home, run_id) is None:
        return _not_found("run", run_id)
    rows = [todo for todo in projections.project_todos(home) if todo["run_id"] == run_id]
    return _response(200, request, rows)


async def global_todos(request: Any) -> Any:
    """GET /api/todos: the human todo center (quota/CI waits never listed)."""
    return _response(200, request, projections.project_todos(_home(request)))


# -- GET /api/runs/{run_id}/docs/{doc} + .../diff ----------------------------


async def read_doc(request: Any) -> Any:
    """GET /api/runs/{run_id}/docs/{doc}: current content + revision history."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    doc = str(request.path_params.get("doc", ""))
    context = _doc_context(home, run_id, doc)
    if context is None:
        return _not_found("document", f"{run_id}/{doc}")
    _, doc_file, revision = context
    payload = {
        "revision": revision,
        "content": doc_file.read_text(encoding="utf-8"),
        "history": _doc_history(home, doc_file, run_id, doc, revision),
    }
    return _response(200, request, payload)


async def doc_diff(request: Any) -> Any:
    """GET /api/runs/{run_id}/docs/{doc}/diff?from=&to=: unified diff."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    doc = str(request.path_params.get("doc", ""))
    context = _doc_context(home, run_id, doc)
    if context is None:
        return _not_found("document", f"{run_id}/{doc}")
    repo, doc_file, current = context
    from_revision = _param(request.query_params, "from")
    to_revision = _param(request.query_params, "to")
    if from_revision is None or to_revision is None:
        return _error(422, "validation_failed", "diff requires both 'from' and 'to' revisions")
    old = _content_at(repo, doc_file, from_revision, current)
    new = _content_at(repo, doc_file, to_revision, current)
    if old is None or new is None:
        return _not_found("revision", f"{from_revision}..{to_revision}")
    return _response(
        200,
        request,
        {
            "from": from_revision,
            "to": to_revision,
            "unified_diff": _unified_doc_diff(doc, old, new),
        },
    )


# -- project-domain docs read faces (§2b #31-34, §1n/§1p) --------------------


def _doc_ident(request: Any) -> str:
    return f"{_path_text(request, 'version')}/{_path_text(request, 'doc')}"


def _implicit_host_project(request: Any) -> dict | None:
    """The serve's own permitted repo as an implicit host project (RP-01).

    The v0.10 workbench is the single-user local face of ONE host repo: when
    nothing is registered yet (a fresh serve before the first registration),
    the docs-centre read faces still resolve against the serve's ``--repo``
    scope so the browser can browse the host repo's version tree. The moment
    any project is registered the fallback disappears — a registered registry
    is authoritative and unknown ids keep failing closed (§2b #31-34).
    """
    conn = projections._service_db(_home(request))
    try:
        if projections._projects(conn):
            return None
    finally:
        if conn is not None:
            conn.close()
    roots = getattr(_state(request), "permitted_repos", None) or []
    for root in roots:
        path = Path(root)
        if path.is_dir():
            return {"project_id": "", "repo_path": str(path), "version": ""}
    return None


async def docs_tree(request: Any) -> Any:
    """GET /api/projects/{pid}/docs/tree: version tree + six-piece docs (#31)."""
    home = _home(request)
    pid = _param(request.path_params, "pid")
    payload = projections.project_docs_tree(home, pid or "")
    if payload is None:
        host = _implicit_host_project(request)
        if host is not None:
            payload = projections.docs_tree_for_project(host)
    if payload is None:
        return _not_found("project", pid or "")
    return _response(200, request, payload)


async def read_project_doc(request: Any) -> Any:
    """GET /api/projects/{pid}/docs/{version}/{doc}: content + history (#32)."""
    home = _home(request)
    doc = _path_text(request, "doc")
    resolved = projections.resolve_project_doc(
        home, _path_text(request, "pid"), _path_text(request, "version"), doc
    )
    if resolved is None:
        host = _implicit_host_project(request)
        if host is not None:
            resolved = projections.resolve_doc_in_project(
                host, _path_text(request, "version"), doc
            )
    if resolved is None:
        return _not_found("document", _doc_ident(request))
    project, version_dir, doc_file, revision = resolved
    return _response(
        200,
        request,
        {
            "revision": revision,
            "content": doc_file.read_text(encoding="utf-8"),
            "history": _project_doc_history(
                home, Path(project["repo_path"]), version_dir, doc_file, doc, revision
            ),
        },
    )


async def project_doc_diff(request: Any) -> Any:
    """GET /api/projects/{pid}/docs/{version}/{doc}/diff?from=&to= (#33)."""
    home = _home(request)
    doc = _path_text(request, "doc")
    resolved = projections.resolve_project_doc(
        home, _path_text(request, "pid"), _path_text(request, "version"), doc
    )
    if resolved is None:
        host = _implicit_host_project(request)
        if host is not None:
            resolved = projections.resolve_doc_in_project(
                host, _path_text(request, "version"), doc
            )
    if resolved is None:
        return _not_found("document", _doc_ident(request))
    project, _version_dir, doc_file, current = resolved
    from_revision = _param(request.query_params, "from")
    to_revision = _param(request.query_params, "to")
    if from_revision is None or to_revision is None:
        return _error(422, "validation_failed", "diff requires both 'from' and 'to' revisions")
    repo = Path(project["repo_path"])
    old = _content_at(repo, doc_file, from_revision, current or "")
    new = _content_at(repo, doc_file, to_revision, current or "")
    if old is None or new is None:
        return _not_found("revision", f"{from_revision}..{to_revision}")
    return _response(
        200,
        request,
        {
            "from": from_revision,
            "to": to_revision,
            "unified_diff": _unified_doc_diff(doc, old, new),
        },
    )


async def project_doc_discussions(request: Any) -> Any:
    """GET /api/projects/{pid}/docs/{version}/{doc}/discussions (#34)."""
    home = _home(request)
    version = _path_text(request, "version")
    doc = _path_text(request, "doc")
    payload = projections.project_doc_discussions(
        home, _path_text(request, "pid"), version, doc
    )
    if payload is None:
        host = _implicit_host_project(request)
        if host is not None:
            resolved = projections.resolve_doc_in_project(host, version, doc)
            if resolved is not None:
                _project, _vdir, doc_file, _revision = resolved
                payload = {
                    "threads": [
                        projections._thread_json(thread)
                        for thread in parse_threads(doc_file.read_text(encoding="utf-8"))
                    ]
                }
    if payload is None:
        return _not_found("document", _doc_ident(request))
    return _response(200, request, payload)


def _project_doc_history(
    home: Path,
    repo: Path,
    version_dir: Path,
    doc_file: Path,
    doc: str,
    current: str | None,
) -> list[dict]:
    """Merged revision history for a project-domain doc (§1n.2).

    Current revision first (file mtime), then the version's material.edited
    events and the git commits touching the version directory, deduplicated
    by revision digest — the same-source merge the run domain uses, with
    older contents resolved through the repo's git history.
    """
    entries: list[dict] = []
    seen: set[str] = set()

    def add(revision: object, ts: object, actor: object) -> None:
        if isinstance(revision, str) and revision and revision not in seen:
            seen.add(revision)
            entries.append({"revision": revision, "ts": str(ts or ""), "actor": str(actor or "")})

    if current:
        add(current, _mtime_iso(doc_file), "")
    for event in _project_material_events(home, repo, version_dir.name, doc):
        payload = event["payload"]
        for revision in (payload.get("to_revision"), payload.get("from_revision")):
            add(revision, event["ts"], payload.get("actor"))
    rel_dir = _relpath(version_dir, repo)
    if rel_dir is not None:
        log = _git(repo, "log", f"-n{_GIT_LOG_LIMIT}", "--format=%H", "--", rel_dir)
        for sha in (log or "").split():
            add(
                _commit_trio_digest(repo, sha, rel_dir),
                (_git(repo, "show", "-s", "--format=%cI", sha) or "").strip(),
                (_git(repo, "show", "-s", "--format=%an", sha) or "").strip(),
            )
    return entries


def _project_material_events(home: Path, repo: Path, version: str, doc: str) -> list[dict]:
    """material.edited events of the version's runs for one doc, newest first."""
    run_ids = [
        row[0]
        for row in _read_rows(
            _tracks_db_path(str(repo)),
            "SELECT run_id FROM runs WHERE version = ? ORDER BY updated_ts",
            (version,),
        )
    ]
    if not run_ids:
        return []
    placeholders = ",".join("?" for _ in run_ids)
    rows = _read_rows(
        _service_db_path(home),
        "SELECT ts, payload FROM service_events WHERE type = 'material.edited'"
        f" AND run_id IN ({placeholders}) ORDER BY seq DESC",
        tuple(run_ids),
    )
    wanted = {doc, "architecture"} if doc == "design" else {doc}
    events = []
    for ts, raw in rows:
        payload = _json_dict(raw)
        if payload.get("doc") in wanted:
            events.append({"ts": ts, "payload": payload})
    return events


# -- GET /api/runs/{run_id}/release-preview ----------------------------------


async def release_preview(request: Any) -> Any:
    """GET /api/runs/{run_id}/release-preview: digest + staleness (§2b #20)."""
    home = _home(request)
    run_id = str(request.path_params.get("run_id", ""))
    project = _project_for_run(home, run_id)
    if project is None:
        return _not_found("run", run_id)
    events = _run_events(project["repo_path"], run_id)
    previews = [event for event in events if event.type == "release.previewed"]
    if not previews:
        return _error(409, "preview_unavailable", f"run {run_id!r} has no release preview yet")
    preview = previews[-1]
    stale_reason = _preview_stale_reason(events, preview)
    payload = {
        "preview_digest": (preview.payload or {}).get("preview_digest"),
        "generated_at": preview.ts,
        "stale": stale_reason is not None,
        "stale_reason": stale_reason,
        "summary": _preview_summary(preview.payload or {}),
        "candidate_sha": (preview.payload or {}).get("candidate_sha"),
    }
    return _response(200, request, payload)


# -- GET /api/commands/{command_id} ------------------------------------------


async def command_status(request: Any) -> Any:
    """GET /api/commands/{command_id}: persisted command status/result."""
    home = _home(request)
    command_id = str(request.path_params.get("command_id", ""))
    row = _command_row(home, command_id)
    if row is None:
        return _not_found("command", command_id)
    payload: dict[str, Any] = {
        "command_id": row["command_id"],
        "kind": row["kind"],
        "status": row["status"],
    }
    outcome = _json_value(row["result_json"])
    if outcome is not None and row["status"] == "failed":
        payload["failure"] = outcome
    elif outcome is not None and row["status"] == "completed":
        payload["result"] = outcome
    return _response(200, request, payload)


# CORE-02 extension seam (T-001): the project-domain docs read endpoints
# (§2b #31-34) declare their routes on this side; create_app merges the table
# into the assembled route set so later faces land without re-touching the
# composition root.
EXTENSION_ROUTES = (
    ("/api/projects/{pid}/docs/tree", docs_tree, ("GET",)),
    ("/api/projects/{pid}/docs/{version}/{doc}", read_project_doc, ("GET",)),
    ("/api/projects/{pid}/docs/{version}/{doc}/diff", project_doc_diff, ("GET",)),
    (
        "/api/projects/{pid}/docs/{version}/{doc}/discussions",
        project_doc_discussions,
        ("GET",),
    ),
)
