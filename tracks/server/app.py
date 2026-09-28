"""Starlette app factory and web composition root (IF-SERVE-001).

Composition root of the web surface: wires the service-level store
(``tracks/supervisor/db.py``), the command service, the supervisor handle,
auth middleware and the route tables into the Starlette application that
``cmd_serve`` runs under uvicorn. HTTP handlers never touch the executor;
mutations go through the command service only (interfaces §1b).

v0.10 adds the name-binding auth face (IF-WEBAUTH-001 / IF-AUTHNAME-001):
``POST /api/auth/name`` + ``GET /api/auth/profile`` (§2b #29-30), the login
response's ``name_required`` flag (§2b #1), the §1m.2 name gate on workbench
page entries, logout cookie clearing (§1m.4), and the CORE-02 route seam
merging api_query-side extension routes (§2b #31-34 arrive without
re-touching this root).

The module stays importable without starlette (unit layer): the Starlette
imports live inside the composing functions. The request seam is the one
documented by the api modules — ``request.app.state.home`` / ``.service`` /
``.supervisor`` / ``.config`` / ``.permitted_repos`` / ``.redactor`` — and
every handler envelope (``QueryResponse`` / ``CommandResponse`` /
``EventResponse``) is mapped onto a Starlette response here. The auth
boundary (§1f.3) guards everything except ``/healthz``, the login face and
``/static``: API misses get 401 JSON, page misses a 302 to ``/login``.

Contract tokens: IF-SERVE-001, IF-WEBAUTH-001, IF-QUERY-001, IF-STREAM-001.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any

from tracks import __version__
from tracks.server import api_command, api_events, api_query, auth, pages, redaction

SESSION_COOKIE = "trac_session"
_SESSION_MAX_AGE_S = 7 * 24 * 3600  # rolling 7-day sessions (§1f.2)
_LOCAL_ACTOR_FALLBACK = "local-user"
_PROCESS_STARTED = time.monotonic()
_STATIC_DIR = Path(__file__).parent / "static"
# Public surface (§2b #3, §1f.3): health probe, the login face and the
# vendored assets; everything else requires a session.
_PUBLIC_PATHS = frozenset({"/healthz", "/login", "/api/auth/login"})

# (path, handler, methods) — the §2b route table over the delivered handler
# modules; the composition root only binds, it never re-implements.
_ROUTES: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    ("/healthz", api_query.healthz, ("GET",)),
    ("/api/service/config", api_query.service_config, ("GET",)),
    ("/api/projects", api_query.list_projects, ("GET",)),
    ("/api/projects", api_command.register_project, ("POST",)),
    ("/api/projects/{pid}/readiness", api_command.run_readiness, ("POST",)),
    ("/api/projects/{pid}/runs", api_command.create_run, ("POST",)),
    ("/api/projects/{pid}/overview", api_query.project_overview, ("GET",)),
    ("/api/runs/{run_id}", api_query.run_detail, ("GET",)),
    ("/api/runs/{run_id}/timeline", api_query.timeline, ("GET",)),
    ("/api/runs/{run_id}/ac-chain", api_query.ac_chain, ("GET",)),
    ("/api/runs/{run_id}/todos", api_query.run_todos, ("GET",)),
    ("/api/todos", api_query.global_todos, ("GET",)),
    ("/api/runs/{run_id}/docs/{doc}", api_query.read_doc, ("GET",)),
    ("/api/runs/{run_id}/docs/{doc}/diff", api_query.doc_diff, ("GET",)),
    ("/api/runs/{run_id}/docs/{doc}/edits", api_command.edit_material, ("POST",)),
    ("/api/runs/{run_id}/release-preview", api_query.release_preview, ("GET",)),
    ("/api/runs/{run_id}/release-decision", api_command.record_release_decision, ("POST",)),
    ("/api/runs/{run_id}/clarifications", api_command.submit_clarification, ("POST",)),
    ("/api/runs/{run_id}/approvals", api_command.record_approval, ("POST",)),
    ("/api/runs/{run_id}/pause", api_command.pause_run, ("POST",)),
    ("/api/runs/{run_id}/resume", api_command.resume_run, ("POST",)),
    ("/api/runs/{run_id}/abandon", api_command.abandon_run, ("POST",)),
    ("/api/runs/{run_id}/return", api_command.return_stage, ("POST",)),
    ("/api/runs/{run_id}/retry", api_command.retry_run, ("POST",)),
    ("/api/commands/{command_id}", api_query.command_status, ("GET",)),
    ("/api/runs/{run_id}/events", api_events.stream_events, ("GET",)),
)

# Server-rendered pages (E-01..E-08, interfaces §2b page list).
_PAGE_ROUTES: tuple[tuple[str, str], ...] = (
    ("/", "overview"),
    ("/login", "login"),
    ("/projects", "projects"),
    ("/projects/{pid}/runs/new", "run_new"),
    ("/runs/{run_id}", "run_detail"),
    ("/runs/{run_id}/review", "review"),
    ("/todos", "todos"),
    ("/runs/{run_id}/release", "release"),
)


def create_app(home: Path, service: Any, supervisor: Any, config: Any) -> Any:
    """Build the Starlette application (routes/middleware/static wiring).

    The returned app serves the pages and API of interfaces §2b and mounts
    ``tracks/server/static/`` at ``/static/``; uvicorn runs it in the serve
    process while the supervisor drives runs in the same process (workers are
    child processes).
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware

    home = Path(home)
    glue = _AuthGlue(home, supervisor)
    app = Starlette(
        routes=_build_routes(glue),
        middleware=[Middleware(_AuthBoundary, home=home)],
    )
    app.state.home = home
    app.state.service = service
    app.state.supervisor = supervisor
    app.state.config = config
    app.state.permitted_repos = [Path(p) for p in getattr(config, "permitted_repos", [])]
    app.state.redactor = redaction.SecretRedactor(
        dict(getattr(config, "secret_values", None) or {})
    )
    return app


def _build_routes(glue: _AuthGlue) -> list:
    """Bind the §2b route table; the root binds, it never re-implements."""
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles

    # CORE-02 extension seam: routes the api_query side declares merge into
    # the assembly (empty until that face exists), so later tasks land the
    # §2b #31-34 read endpoints without re-touching this composition root.
    api_table = _ROUTES + tuple(getattr(api_query, "EXTENSION_ROUTES", ()))
    routes = [
        Route(path, _endpoint(handler), methods=list(methods))
        for path, handler, methods in api_table
    ]
    routes += [
        Route(path, _page_endpoint(name), methods=["GET"]) for path, name in _PAGE_ROUTES
    ]
    routes += [
        Route("/api/auth/login", glue.login, methods=["POST"]),
        Route("/api/auth/logout", glue.logout, methods=["POST"]),
        Route("/api/auth/name", glue.bind_name, methods=["POST"]),
        Route("/api/auth/profile", glue.profile, methods=["GET"]),
    ]
    routes.append(
        Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static")
    )
    return routes


def _endpoint(handler: Any) -> Any:
    """Map one handler envelope onto a Starlette JSON/SSE response."""

    async def endpoint(request: Any) -> Any:
        result = await handler(request)
        if getattr(result, "body_iterator", None) is not None:
            from starlette.responses import StreamingResponse

            return StreamingResponse(
                result.body_iterator,
                status_code=result.status_code,
                media_type=result.media_type,
            )
        from starlette.responses import JSONResponse

        return JSONResponse(result.payload, status_code=result.status_code)

    return endpoint


def _page_endpoint(name: str) -> Any:
    async def endpoint(request: Any) -> Any:
        from starlette.responses import HTMLResponse

        return HTMLResponse(pages.render_page(name, {}))

    return endpoint


class _AuthGlue:
    """Auth-face endpoints + service-event audits (§2b #1-2/#29-30, §1a #24-25).

    Thin glue over the delivered ``auth`` mechanics: verify, issue the
    session (cookie HttpOnly + SameSite=Strict, Path=/), collect the display
    name, expose the profile read, and audit the outcomes without ever
    recording the password or token itself.
    """

    def __init__(self, home: Path, supervisor: Any) -> None:
        self._home = Path(home)
        self._supervisor = supervisor

    async def login(self, request: Any) -> Any:
        from starlette.responses import JSONResponse

        password = await _login_password(request)
        with contextlib.closing(_open_conn(self._home)) as conn:
            actor = _local_actor(conn)
            valid = auth.verify_password(
                conn, password if isinstance(password, str) else ""
            )
            if not valid:
                self._audit(actor, "failed")
                return JSONResponse(
                    {"error": {"reason": "unauthenticated", "detail": "password mismatch"}},
                    status_code=401,
                )
            issued = auth.issue_session(conn, auth.effective_actor(conn))
            name_required = not _name_collected(conn)
        self._audit(actor, "succeeded")
        response = JSONResponse(
            {
                "actor": issued.actor,
                "csrf_token": issued.csrf_token,
                "name_required": name_required,
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            issued.token,
            max_age=_SESSION_MAX_AGE_S,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    async def bind_name(self, request: Any) -> Any:
        """POST /api/auth/name — validate + bind the display name (§2b #29)."""
        from starlette.responses import JSONResponse

        with contextlib.closing(_open_conn(self._home, readonly=True)) as conn:
            session = auth.resolve_session(conn, request.cookies.get(SESSION_COOKIE))
        if session is None:
            return _unauthenticated_response()
        if not auth.check_csrf(session, request.headers.get("x-trac-csrf")):
            self._audit_denied("auth_name", session)
            return JSONResponse(
                {
                    "error": {
                        "reason": "unauthenticated",
                        "detail": "missing or mismatching X-Trac-CSRF credential",
                    }
                },
                status_code=403,
            )
        try:
            name = auth.validate_display_name(await _name_field(request))
        except ValueError as exc:
            return JSONResponse(
                {"error": {"reason": "validation_failed", "detail": str(exc)}},
                status_code=400,
            )
        with contextlib.closing(_open_conn(self._home)) as conn:
            auth.bind_display_name(conn, name)
        self._audit_name_bound(name)
        return JSONResponse({"actor": name})

    async def profile(self, request: Any) -> Any:
        """GET /api/auth/profile — the name-state read face (§2b #30)."""
        from starlette.responses import JSONResponse

        with contextlib.closing(_open_conn(self._home, readonly=True)) as conn:
            actor = auth.effective_actor(conn)
            name_set = _name_collected(conn)
        return JSONResponse({"actor": actor, "name_set": name_set})

    async def logout(self, request: Any) -> Any:
        from starlette.responses import Response

        token = request.cookies.get(SESSION_COOKIE)
        if isinstance(token, str) and token:
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with contextlib.closing(_open_conn(self._home)) as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (digest,))
                conn.commit()
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE, path="/", httponly=True, samesite="strict"
        )
        return response

    def _audit(self, actor: str, outcome: str) -> None:
        self._emit_service_event("auth.login", {"actor": actor, "outcome": outcome})

    def _audit_name_bound(self, name: str) -> None:
        """§1a #25: actor = the effective display name, surface = http."""
        self._emit_service_event(
            "auth.name_bound", {"actor": name, "surface": "http"}
        )

    def _audit_denied(self, surface: str, session: Any) -> None:
        """§1f.3 parity with the command face: a CSRF miss is audited as
        ``access.denied`` (closed §1a set) with the session's actor."""
        self._emit_service_event(
            "access.denied",
            {
                "surface": surface,
                "reason": "unauthenticated",
                "actor": session.actor,
                "actor_class": session.actor_class,
            },
        )

    def _emit_service_event(self, event_type: str, payload: dict) -> None:
        store = getattr(self._supervisor, "db", None)
        if store is not None:
            store.append_event(event_type, payload)


async def _login_password(request: Any) -> Any:
    """JSON body first (§2b #1), the login form fallback second.

    The urlencoded fallback (the ``/login`` page form) parses natively;
    Starlette's ``request.form()`` would assert an optional multipart
    dependency the contract does not require, so any other content type
    fails closed as a missing password (401 mismatch) instead of a 500.
    """
    content_type = str(request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type == "application/json":
        try:
            body = await request.json()
        except ValueError:
            return None
        return body.get("password") if isinstance(body, dict) else None
    if content_type == "application/x-www-form-urlencoded":
        raw = (await request.body()).decode("utf-8", "replace")
        values = urllib.parse.parse_qs(raw)
        return values.get("password", [None])[0]
    return None


async def _name_field(request: Any) -> Any:
    """The ``{name}`` JSON field of POST /api/auth/name (§2b #29)."""
    try:
        body = await request.json()
    except ValueError:
        return None
    return body.get("name") if isinstance(body, dict) else None


def _unauthenticated_response() -> Any:
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"error": {"reason": "unauthenticated", "detail": "authentication required"}},
        status_code=401,
    )


def _name_collected(conn: sqlite3.Connection) -> bool:
    """Whether auth.display_name is collected (interfaces §1m.2)."""
    row = conn.execute("SELECT display_name FROM auth LIMIT 1").fetchone()
    return row is not None and isinstance(row[0], str) and bool(row[0].strip())


class _AuthBoundary:
    """Session boundary around the non-public surface (interfaces §1f.3).

    API misses answer 401 JSON ``unauthenticated``; page misses redirect to
    the login page (302). Session resolution goes through the delivered
    ``tracks/server/auth.py`` mechanics against a short-lived read-only
    connection (NFR-0150 read-path discipline). v0.10 adds the §1m.2 name
    gate: an authenticated session may use the API face, but workbench page
    entries bounce back to ``/login`` until the display name is collected.
    """

    def __init__(self, app: Any, home: Path) -> None:
        self._app = app
        self._home = Path(home)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        from starlette.requests import Request
        from starlette.responses import RedirectResponse

        request = Request(scope, receive=receive)
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/static/"):
            await self._app(scope, receive, send)
            return
        with contextlib.closing(_open_conn(self._home, readonly=True)) as conn:
            session = auth.resolve_session(conn, request.cookies.get(SESSION_COOKIE))
            allowed = session is not None and (
                path.startswith("/api") or _name_collected(conn)
            )
        if allowed:
            await self._app(scope, receive, send)
            return
        if path.startswith("/api"):
            response = _unauthenticated_response()
        else:
            response = RedirectResponse("/login", status_code=302)
        await response(scope, receive, send)


def health_response(home: Path) -> Any:
    """Return the ``GET /healthz`` payload: 200 ``{status, projects, version,
    uptime_s}`` or 503 ``{status: unavailable, reasons: [...]}`` (§2b #3)."""
    db_path = Path(home) / "service.db"
    if not db_path.exists():
        return _unavailable("service_store", f"service store {db_path} is missing")
    try:
        with contextlib.closing(_open_conn(Path(home), readonly=True)) as conn:
            projects = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    except sqlite3.Error as exc:
        return _unavailable("service_store", f"service store is not readable: {exc}")
    return {
        "status": "ok",
        "projects": projects,
        "version": __version__,
        "uptime_s": round(time.monotonic() - _PROCESS_STARTED, 3),
    }


def _unavailable(check: str, reason: str) -> dict:
    return {"status": "unavailable", "reasons": [{"check": check, "reason": reason}]}


def _open_conn(home: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{Path(home) / 'service.db'}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(Path(home) / "service.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _local_actor(conn: sqlite3.Connection) -> str:
    """The provisioned local single user (§1f.1); falls back to the default."""
    row = conn.execute("SELECT actor FROM auth LIMIT 1").fetchone()
    return str(row[0]) if row is not None else _LOCAL_ACTOR_FALLBACK
