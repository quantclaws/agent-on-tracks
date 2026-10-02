"""Mutating HTTP routes -> CommandService (interfaces §2b, §1b).

Every handler resolves the authenticated session (``auth.resolve_session``,
§1f.2-3), compares the ``X-Trac-CSRF`` credential constant-time (§1f.3),
validates the structured payload through the command guard (§1g.1), applies
the endpoint-level run scope and delegates to ``CommandService.accept`` with
``surface="http"`` (§1b.1). The web-gate business binding — approval revision
(FR-0308), release preview digest (FR-0309), controlled-retry position
(FR-0311) — lives in the command service's accept path so the CLI face, the
HTTP face and direct callers share one binding (IF-WEBGATE-001); rejections
there are audited (``command.rejected``) and mapped onto the §2b status codes
through the unified error envelope, and produce no execution effects (SM-01.5).
Handlers never run long tasks (§1b); the two §2b short tasks (register #5,
readiness #7) complete synchronously as the endpoint table specifies.

Request seam (bound by ``create_app``, T-014; mirrors the T-011 query seam):
    request.app.state.home            -> service home (service.db)
    request.app.state.service         -> CommandService
    request.app.state.permitted_repos -> realpath serve --repo scope (§1g.2)
    request.path_params               -> pid / run_id / doc
    request.cookies                   -> {"trac_session": <token>}
    request.headers                   -> case-insensitive .get
    await request.json()              -> structured JSON body

Response envelope: ``CommandResponse(status_code, payload)`` — starlette-free
so the package stays importable without starlette (unit level); the app
factory maps it onto a JSONResponse.

The docs-centre edit face (``edit_project_doc``, §2b #35) resolves the
version's editable run and submits the same ``edit_material`` command with
``revision_kind="docs"`` (docs_revision token); the run-domain face stays
trio-only. Both edit faces append the face's live token as the top-level 409
``current_revision`` (§1o.2).

Contract tokens: IF-WEBGATE-001, IF-CMDSVC-001, IF-CMDGUARD-001,
IF-WEBAUTH-001, IF-SECRECY-001, IF-DOCREV-001, IF-DOCSAVE-001.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

from tracks import paths
from tracks.baseline import _TRIO, revision_digest
from tracks.frontmatter import split_frontmatter
from tracks.paths import tracks_home
from tracks.server import auth, guard, projections
from tracks.server.api_query import _DOC_FILES
from tracks.supervisor import db as sdb
from tracks.supervisor import readiness
from tracks.supervisor.service import Rejection, docs_revision

_SESSION_COOKIE = "trac_session"
_LOCAL_HOME_SENTINEL = "unknown"

# Run-domain edit face doc set (interfaces §1o.1b): the trio only; design
# documents live on the docs-centre face #35, whose docs_revision token
# resolves the structural impossibility the v0.9 aliased face had.
_TRIO_DOCS = frozenset({"story", "spec", "acceptance"})
_DOCS_EDIT_PATH = "/api/projects/{pid}/docs/{version}/{doc}/edits"
_DOCS_CENTRE_HINT = (
    "run-domain edits are trio-only (story/spec/acceptance); design documents"
    " are edited via the docs-centre face POST"
    " /api/projects/{pid}/docs/{version}/{doc}/edits (interfaces §1o.1b)"
)

# Closed Rejection reason -> HTTP status (§1b.1, §2b failure column).
_STATUS_BY_REASON = {
    "unauthenticated": 401,
    "forbidden_actor": 403,
    "scope_violation": 403,
    "guard_blocked": 400,
    "validation_failed": 400,
    "idempotency_conflict": 409,
    "stale_revision": 409,
    "stale_preview": 409,
    "active_run_exists": 409,
    "not_found": 404,
}


def _rejection_status(kind: str, reason: str) -> int:
    """§2b status for a service rejection: the closed reason map, with the
    retry endpoint's fixed 409 override (§2b #26 — the retry position is
    conveyed through the readable cause, not the reason token)."""
    if kind == "retry_run" and reason == "validation_failed":
        return 409
    return _STATUS_BY_REASON.get(reason, 400)


@dataclass(frozen=True)
class CommandResponse:
    """HTTP envelope of a mutating route: status code + JSON payload."""

    status_code: int
    payload: Any


# -- request seam + response/error helpers -----------------------------------


def _home(request: Any) -> Path:
    return Path(request.app.state.home)


def _service(request: Any) -> Any:
    return request.app.state.service


def _permitted(request: Any) -> list[Path]:
    return [Path(root) for root in getattr(request.app.state, "permitted_repos", [])]


def _error(status: int, reason: str, detail: str) -> CommandResponse:
    return CommandResponse(status, {"error": {"reason": reason, "detail": detail}})


async def _json_body(request: Any) -> dict:
    """The structured JSON body; malformed/non-object bodies fail closed."""
    try:
        body = await request.json()
    except (AttributeError, TypeError, ValueError):
        return {}
    return dict(body) if isinstance(body, dict) else {}


def _audit(
    home: Path,
    event_type: str,
    payload: dict,
    *,
    project_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """Append one service-plane audit event (closed §1a set)."""
    sdb.ServiceDB(home).append_event(
        event_type, payload, project_id=project_id, run_id=run_id
    )


def _authenticated(request: Any):
    """Resolve the session from the cookie; None -> 401 at the call site."""
    conn = sqlite3.connect(f"file:{_home(request) / 'service.db'}?mode=ro", uri=True)
    try:
        return auth.resolve_session(conn, request.cookies.get(_SESSION_COOKIE))
    finally:
        conn.close()


def _csrf_rejection(request: Any, session: Any, kind: str, run_id: str | None) -> CommandResponse:
    """403 + access.denied for a missing/mismatching CSRF credential (§1f.3)."""
    _audit(
        _home(request),
        "access.denied",
        {
            "surface": kind,
            "reason": "unauthenticated",
            "actor": session.actor,
            "actor_class": session.actor_class,
        },
        run_id=run_id,
    )
    return _error(403, "unauthenticated", "missing or mismatching X-Trac-CSRF credential")


def _rejection_audit(
    request: Any,
    session: Any,
    kind: str,
    params: dict,
    reason: str,
    detail: str,
    *,
    status: int | None = None,
) -> CommandResponse:
    """Persist the command.rejected audit and map the reason to §2b status.

    The per-endpoint failure column (§2b) overrides the generic mapping —
    e.g. retry #26 rejects with 409 regardless of the closed reason token.
    """
    _audit(
        _home(request),
        "command.rejected",
        {
            "kind": kind,
            "reason": reason,
            "detail": detail,
            "actor": session.actor,
            "actor_class": session.actor_class,
        },
        project_id=params.get("project_id"),
        run_id=params.get("run_id"),
    )
    return _error(status or _STATUS_BY_REASON.get(reason, 400), reason, detail)


# -- run context (project resolution + run state, all read-only) --------------


def _run_context(home: Path, run_id: str | None):
    """(project, repo, state, events) for a run; None when unregistered."""
    if not run_id:
        return None
    conn = projections._service_db(home)
    try:
        project = projections._project_for_run(conn, run_id)
    finally:
        if conn is not None:
            conn.close()
    if project is None:
        return None
    repo = Path(project["repo_path"])
    events = projections._run_events(project["repo_path"], run_id)
    return project, repo, projections.project_state(events), events


def _unknown_run(request: Any, session: Any, kind: str, params: dict) -> CommandResponse:
    return _rejection_audit(
        request,
        session,
        kind,
        params,
        "not_found",
        f"run {params.get('run_id')!r} is not registered on this service",
    )


# -- accept pipeline (session -> csrf -> guard -> preflight -> service) -------


async def _mutate(
    request: Any,
    kind: str,
    params: dict,
    *,
    guard_params: dict | None = None,
    preflight: Any = None,
    respond: Any = None,
) -> CommandResponse:
    """Shared mutation pipeline; ``params`` is what the service receives."""
    session = _authenticated(request)
    if session is None:
        return _error(401, "unauthenticated", "an authenticated session is required")
    if not auth.check_csrf(session, request.headers.get("X-Trac-CSRF")):
        return _csrf_rejection(request, session, kind, params.get("run_id"))
    return _validated_mutate(
        request,
        session,
        kind,
        params,
        guard_params=guard_params,
        preflight=preflight,
        respond=respond,
    )


def _validated_mutate(
    request: Any,
    session: Any,
    kind: str,
    params: dict,
    *,
    guard_params: dict | None = None,
    preflight: Any = None,
    respond: Any = None,
) -> CommandResponse:
    """Guard -> preflight -> accept tail for an already-resolved session.

    The docs-centre face (#35) resolves its project version and editable run
    before entering this shared tail, so the accept/binding path stays one.
    """
    try:
        guard.validate_command_payload(kind, guard_params if guard_params is not None else params)
    except guard.GuardRejection as rejection:
        return _rejection_audit(request, session, kind, params, rejection.reason, rejection.detail)
    if preflight is not None:
        failure = preflight(request, session, params)
        if failure is not None:
            return failure
    return _accept(request, kind, params, session, respond=respond)


def _accept(
    request: Any,
    kind: str,
    params: dict,
    session: Any,
    respond: Any = None,
) -> CommandResponse:
    """Delegate to CommandService.accept(surface=http) and map the outcome."""
    try:
        receipt = _service(request).accept(
            kind,
            params,
            actor=session.actor,
            actor_class=session.actor_class,
            surface="http",
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except Rejection as rejection:
        return _error(
            _rejection_status(kind, rejection.reason), rejection.reason, rejection.detail
        )
    payload: dict = {"command_id": receipt.command_id}
    if respond is not None:
        payload.update(respond(receipt))
    return CommandResponse(200 if receipt.deduplicated else 202, payload)


def _run_scoped(kind: str):
    """Preflight that only resolves the run (404 for unknown runs)."""

    def preflight(request: Any, session: Any, params: dict) -> CommandResponse | None:
        if _run_context(_home(request), params.get("run_id")) is None:
            return _unknown_run(request, session, kind, params)
        return None

    return preflight


# -- material revisions (§2b #17 new_revision / §1o.2) -------------------------


def _projected_revision(repo: Path, version: str | None, doc: str | None, content: str) -> str:
    """The trio revision digest the accepted edit will produce once applied."""
    edited = _DOC_FILES.get(doc or "")
    pieces = []
    for label, name in _TRIO:
        if name == edited:
            body = split_frontmatter(content)[1]
        else:
            text = (paths.version_dir(tracks_home(repo), version or "") / name).read_text(
                encoding="utf-8"
            )
            body = split_frontmatter(text)[1]
        pieces.append(f"{label}:{hashlib.sha256(body.encode('utf-8')).hexdigest()}")
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()


def _material_context(request: Any, run_id: Any) -> tuple[Path | None, str | None]:
    """(repo, version) of a run's material; repo None when unresolvable.

    One resolution site for the edit response and the stale-409 enrichment:
    the projected run version, then the registered project's version.
    """
    ctx = _run_context(_home(request), run_id)
    if ctx is None:
        return None, None
    project, repo, state, _events = ctx
    version = state.version or project.get("version")
    return repo, str(version) if version else None


def _current_material_revision(request: Any, run_id: Any) -> str | None:
    """The live trio digest of a run's material (the #17 face token, §1o.2)."""
    repo, version = _material_context(request, run_id)
    if repo is None or version is None:
        return None
    try:
        return revision_digest(paths.version_dir(tracks_home(repo), version))
    except (OSError, UnicodeDecodeError):
        return None


def _docs_revision_or_none(version_dir: Path | None) -> str | None:
    """The live docs_revision of a version dir (the #35 face token, §1o.2)."""
    if version_dir is None:
        return None
    try:
        return docs_revision(version_dir)
    except (OSError, UnicodeDecodeError):
        return None


def _with_current_revision(response: CommandResponse, current: str | None) -> CommandResponse:
    """§1o.2: a 409 ``stale_revision`` carries the face's live token.

    The token is appended **top-level** next to the existing error envelope
    (never nested inside ``error`` — the sole authoritative form); the editor
    offers reload-discard / force-overwrite from it instead of silently
    overwriting the server content. Every other rejection envelope is
    returned untouched.
    """
    payload = response.payload
    if response.status_code != 409 or not isinstance(payload, dict) or current is None:
        return response
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("reason") != "stale_revision":
        return response
    return CommandResponse(response.status_code, {**payload, "current_revision": current})


# -- short tasks: registration (§2b #5) and readiness (§2b #7) -----------------


def _registration_rejected(
    request: Any, session: Any, repo_path: str, reason: str, detail: str, status: int
) -> CommandResponse:
    _audit(
        _home(request),
        "project.registration_rejected",
        {"repo_path": repo_path, "reason": reason, "actor": session.actor},
    )
    return _error(status, reason, detail)


def _repo_version(repo: Path) -> str:
    """The host repo's declared version (pyproject project.version)."""
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        version = str(data.get("project", {}).get("version", "")).strip()
    except (OSError, tomllib.TOMLDecodeError):
        version = ""
    return version or _LOCAL_HOME_SENTINEL


def _registered_project(home: Path, repo_path: str, version: str, actor: str) -> dict:
    """Insert (or return) the projects row; the registration is idempotent."""
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        row = conn.execute(
            "SELECT project_id, repo_path, version FROM projects WHERE repo_path = ?",
            (repo_path,),
        ).fetchone()
        if row is not None:
            return {"project_id": row[0], "repo_path": row[1], "version": row[2], "fresh": False}
        project_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (
                project_id,
                repo_path,
                version,
                actor,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return {"project_id": project_id, "repo_path": repo_path, "version": version, "fresh": True}
    finally:
        conn.close()


# -- the route table (interfaces §2b mutation column) --------------------------


async def register_project(request: Any) -> CommandResponse:
    """POST /api/projects -> register_project (§1b #1, §2b #5)."""
    session = _authenticated(request)
    if session is None:
        return _error(401, "unauthenticated", "an authenticated session is required")
    if not auth.check_csrf(session, request.headers.get("X-Trac-CSRF")):
        return _csrf_rejection(request, session, "register_project", None)
    params = await _json_body(request)
    repo_path = str(params.get("repo_path", ""))
    try:
        guard.check_path_scope(Path(repo_path), _permitted(request))
    except guard.GuardRejection:
        return _registration_rejected(
            request,
            session,
            repo_path,
            "outside_permitted_scope",
            "repo path is outside the permitted serve scope",
            403,
        )
    if not Path(repo_path).is_dir():
        return _registration_rejected(
            request, session, repo_path, "not_a_tracks_repo", "repo path does not exist", 422
        )
    try:
        guard.validate_command_payload("register_project", params)
    except guard.GuardRejection as rejection:
        return _rejection_audit(
            request, session, "register_project", params, rejection.reason, rejection.detail
        )
    accepted = _accept(request, "register_project", params, session)
    if accepted.status_code >= 400:
        return accepted
    repo = Path(repo_path).resolve()
    row = _registered_project(
        _home(request), str(repo), _repo_version(repo), session.actor
    )
    if row.pop("fresh"):
        _audit(
            _home(request),
            "project.registered",
            {
                "project_id": row["project_id"],
                "repo_path": row["repo_path"],
                "version": row["version"],
                "actor": session.actor,
            },
            project_id=row["project_id"],
        )
    return CommandResponse(201, row)


async def run_readiness(request: Any) -> CommandResponse:
    """POST /api/projects/{pid}/readiness -> check_readiness (§1b #2, §2b #7)."""
    session = _authenticated(request)
    if session is None:
        return _error(401, "unauthenticated", "an authenticated session is required")
    if not auth.check_csrf(session, request.headers.get("X-Trac-CSRF")):
        return _csrf_rejection(request, session, "check_readiness", None)
    project_id = request.path_params.get("pid")
    conn = projections._service_db(_home(request))
    try:
        project = projections._project_by_id(conn, project_id)
    finally:
        if conn is not None:
            conn.close()
    if project is None:
        return _error(404, "not_found", f"project {project_id!r} is not registered")
    summary = readiness.summarize_readiness(readiness.run_readiness(Path(project["repo_path"])))
    _audit(
        _home(request),
        "project.readiness_checked",
        {
            "project_id": project["project_id"],
            "ok": summary["ok"],
            "checks": summary["checks"],
            "actor": session.actor,
        },
        project_id=project["project_id"],
    )
    return CommandResponse(200, {"ok": summary["ok"], "checks": summary["checks"]})


async def create_run(request: Any) -> CommandResponse:
    """POST /api/projects/{pid}/runs -> create_run (§1b #3, §2b #8)."""
    params = await _json_body(request)
    params["project_id"] = request.path_params.get("pid") or params.get("project_id")

    def respond(_receipt: Any) -> dict:
        canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return {"run_id": uuid.uuid5(uuid.NAMESPACE_OID, canonical).hex}

    return await _mutate(request, "create_run", params, respond=respond)


async def submit_clarification(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/clarifications -> submit_clarification (§1b #4)."""
    params = {**await _json_body(request), "run_id": request.path_params.get("run_id")}
    return await _mutate(
        request,
        "submit_clarification",
        params,
        preflight=_run_scoped("submit_clarification"),
    )


async def edit_material(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/docs/{doc}/edits -> edit_material (§1b #5).

    The run-domain face is trio-only (interfaces §1o.1b): ``design`` and the
    three design document names answer 422 pointing at the docs-centre face,
    whose docs_revision token can actually move for design saves.
    """
    run_id = request.path_params.get("run_id")
    doc = request.path_params.get("doc")
    params = {**await _json_body(request), "run_id": run_id, "doc": doc}

    def respond(_receipt: Any) -> dict:
        repo, version = _material_context(request, run_id)
        if repo is None:
            return {"new_revision": ""}
        return {"new_revision": _projected_revision(repo, version, doc, params.get("content", ""))}

    def trio_only(rq: Any, session: Any, payload: dict) -> CommandResponse | None:
        if str(payload.get("doc") or "") not in _TRIO_DOCS:
            return _rejection_audit(
                rq,
                session,
                "edit_material",
                payload,
                "validation_failed",
                _DOCS_CENTRE_HINT,
                status=422,
            )
        return None

    def preflight(rq: Any, session: Any, payload: dict) -> CommandResponse | None:
        failure = _run_scoped("edit_material")(rq, session, payload)
        if failure is not None:
            return failure
        return trio_only(rq, session, payload)

    response = await _mutate(
        request,
        "edit_material",
        params,
        preflight=preflight,
        respond=respond,
    )
    return _with_current_revision(response, _current_material_revision(request, run_id))


def _project_row(request: Any, project_id: str) -> dict | None:
    """The registered projects row for pid, or None."""
    conn = projections._service_db(_home(request))
    try:
        return projections._project_by_id(conn, project_id)
    finally:
        if conn is not None:
            conn.close()


def _project_version_entry(request: Any, pid: str, version: str) -> dict | None:
    """The docs-tree version entry for (pid, version), or None."""
    payload = projections.project_docs_tree(_home(request), pid)
    if payload is None:
        return None
    for entry in payload.get("versions") or ():
        if entry.get("version") == version:
            return entry
    return None


async def edit_project_doc(request: Any) -> CommandResponse:
    """POST /api/projects/{pid}/docs/{version}/{doc}/edits (§2b #35).

    The docs-centre edit face: base/new revision are the six-piece
    docs_revision token. It resolves the version's ``editable_run_id``
    (none -> 422 ``no_editable_run``) and submits the same ``edit_material``
    command with ``revision_kind="docs"`` through the shared accept path
    (§1o.1b).
    """
    session = _authenticated(request)
    if session is None:
        return _error(401, "unauthenticated", "an authenticated session is required")
    pid = str(request.path_params.get("pid") or "")
    version = str(request.path_params.get("version") or "")
    doc = str(request.path_params.get("doc") or "")
    body = await _json_body(request)
    if not auth.check_csrf(session, request.headers.get("X-Trac-CSRF")):
        return _csrf_rejection(request, session, "edit_material", None)
    if doc not in _DOC_FILES:
        return _error(404, "not_found", f"document {doc!r} is not a six-piece member")
    entry = _project_version_entry(request, pid, version)
    if entry is None:
        return _error(404, "not_found", f"project {pid!r} version {version!r} is not registered")
    params = {
        "run_id": entry.get("editable_run_id"),
        "doc": doc,
        "base_revision": body.get("base_revision"),
        "content": body.get("content"),
        "revision_kind": "docs",
    }
    if not params["run_id"]:
        return _rejection_audit(
            request,
            session,
            "edit_material",
            params,
            "no_editable_run",
            f"version {version!r} has no editable run; a non-terminal run of the"
            " version is required (interfaces §1o.1b)",
            status=422,
        )
    project = _project_row(request, pid)
    version_dir = (
        paths.version_dir(tracks_home(Path(project["repo_path"])), version)
        if project is not None
        else None
    )
    guard_params = {key: params[key] for key in ("run_id", "doc", "base_revision", "content")}

    def respond(_receipt: Any) -> dict:
        return {"new_revision": _docs_revision_or_none(version_dir) or ""}

    response = _validated_mutate(
        request,
        session,
        "edit_material",
        params,
        guard_params=guard_params,
        preflight=_run_scoped("edit_material"),
        respond=respond,
    )
    return _with_current_revision(response, _docs_revision_or_none(version_dir))


async def record_approval(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/approvals -> record_stage_approval (§1b #6).

    The revision binding (FR-0308) is enforced by the service accept path, so
    a stale approval is rejected there with ``command.rejected(stale_revision)``
    and lands no command row.
    """
    params = {**await _json_body(request), "run_id": request.path_params.get("run_id")}
    return await _mutate(request, "record_stage_approval", params)


async def record_release_decision(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/release-decision -> record_release_decision."""
    params = {**await _json_body(request), "run_id": request.path_params.get("run_id")}
    return await _mutate(request, "record_release_decision", params)


async def pause_run(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/pause -> pause_run (§1b #8, §2b #22)."""
    params = {"run_id": request.path_params.get("run_id")}

    def respond(_receipt: Any) -> dict:
        return {"state": "pause_requested"}

    return await _mutate(
        request, "pause_run", params, preflight=_run_scoped("pause_run"), respond=respond
    )


async def resume_run(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/resume -> resume_run (§1b #9, §2b #23)."""
    params = {"run_id": request.path_params.get("run_id")}
    return await _mutate(request, "resume_run", params, preflight=_run_scoped("resume_run"))


async def abandon_run(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/abandon -> abandon_run (§1b #10, §2b #24)."""
    params = {**await _json_body(request), "run_id": request.path_params.get("run_id")}
    return await _mutate(request, "abandon_run", params, preflight=_run_scoped("abandon_run"))


async def return_stage(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/return -> return_stage (§1b #11, §2b #25)."""
    params = {**await _json_body(request), "run_id": request.path_params.get("run_id")}
    return await _mutate(request, "return_stage", params, preflight=_run_scoped("return_stage"))


async def retry_run(request: Any) -> CommandResponse:
    """POST /api/runs/{run_id}/retry -> retry_run (§1b #12, §2b #26)."""
    body = await _json_body(request)
    params = {
        "run_id": request.path_params.get("run_id"),
        "clear_evidence": bool(body.get("clear_evidence", False)),
    }
    # guard.py's schema registry carries the required core param; the
    # contract's optional boolean flag is closed-set checked here (§1b #12).
    clear_evidence = body.get("clear_evidence", False)
    if not set(body) <= {"clear_evidence"} or not isinstance(clear_evidence, bool):
        session = _authenticated(request)
        if session is None:
            return _error(401, "unauthenticated", "an authenticated session is required")
        return _rejection_audit(
            request,
            session,
            "retry_run",
            params,
            "validation_failed",
            "retry accepts only the optional boolean clear_evidence flag",
        )
    return await _mutate(
        request,
        "retry_run",
        params,
        guard_params={"run_id": params["run_id"]},
    )


# CORE-02-style extension seam: the composition root merges the declared
# routes so the §2b #35 docs-centre edit face lands without re-touching app.py.
EXTENSION_ROUTES = (
    (_DOCS_EDIT_PATH, edit_project_doc, ("POST",)),
)
