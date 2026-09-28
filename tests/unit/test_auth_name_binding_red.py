"""T-001 RED: auth name binding — display_name evolution, effective actor
and the name/profile HTTP face (IF-WEBAUTH-001; name-binding extension per
interfaces §1m / IF-AUTHNAME-001).

Devon-owned unit RED for the T-001 delivery slice:

- ``tracks/supervisor/db.py``: the ``auth`` table gains the nullable
  ``display_name`` column (fresh stores via CREATE TABLE, legacy stores via
  startup PRAGMA-probe + idempotent ALTER, interfaces §1c #6) and the
  service event closed set appends ``auth.name_bound`` (§1a #25);
- ``tracks/server/auth.py``: display-name validation (non-empty, trimmed,
  <=64 chars, no control characters, §1m.2) and the effective actor rule
  ``display_name ?? auth.actor ?? local-user`` at session resolution (§1m.3);
- ``tracks/server/app.py``: ``POST /api/auth/name`` (invalid names -> 400
  ``validation_failed``, CSRF enforced, binding triple: write
  ``auth.display_name``, update existing sessions rows, append the
  ``auth.name_bound`` audit event), ``GET /api/auth/profile``, the login
  response ``name_required`` flag (§2b #1), the 7 workbench entries
  redirecting to ``/login`` while the name is uncollected (§1m.2), logout
  clearing the session cookie (§1m.4), and the create_app route-assembly
  seam merging api_query-side extension routes (task T-001 CORE-02 seam,
  initially empty, so the §2b #31-34 read endpoints land without
  re-touching app.py).

RED discipline: none of the name-binding faces exist on the current tree.
Every node guards the missing symbol / contract field into a real
``AssertionError`` (getattr guards, closed-set membership asserts, status
and schema asserts), so records classify as assertion_failure — no
stub_token, no assembly errors. The ASGI drives below speak the protocol
uvicorn speaks against a real ServiceDB-backed service home with a real
CommandService; nothing mocks the system under test. The supervisor handle
carries the real ServiceDB store (the auth audit producer, §1a #25); the
scheduler/worker halves are not exercised by this slice and stay pinned in
tests/unit/test_t014_serve_composition_red.py.

AC: FR-0318/FR-0320/FR-0321/FR-0322 — TRACKS-TRACE IF-WEBAUTH-001.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tracks.server import api_query, auth
from tracks.server.app import create_app
from tracks.supervisor.db import SERVICE_EVENT_TYPES, ServiceDB
from tracks.supervisor.service import CommandService

_PASSWORD = "correct horse battery staple"
_NAME = "Ada Lovelace"
_LOCAL_ACTOR = "local-user"

# The §1c #6-evolved auth/sessions pair (display_name present, nullable) —
# the state db.py owns reaching in production; auth-level behavior tests
# seed it directly.
_AUTH_SERVICE_SCHEMA = (
    "CREATE TABLE auth (actor TEXT PRIMARY KEY, password_hash TEXT,"
    " created_at TEXT, display_name TEXT);"
    "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, actor TEXT,"
    " csrf_hash TEXT, created_at TEXT, expires_at TEXT);"
)

# A pre-evolution service store (v0.9 shape: auth without display_name) the
# startup migration must evolve in place (§1c #6).
_LEGACY_SERVICE_SCHEMA = (
    "CREATE TABLE service_events (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ts TEXT, type TEXT, project_id TEXT, run_id TEXT, command_id TEXT,"
    " payload TEXT);"
    "CREATE TABLE auth (actor TEXT PRIMARY KEY, password_hash TEXT, created_at TEXT);"
    "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, actor TEXT, csrf_hash TEXT,"
    " created_at TEXT, expires_at TEXT);"
)

# The 7 non-login workbench entries (interfaces §1m.2 name gate; the PAGES
# URL set minus /login).
_WORKBENCH_PATHS = (
    "/",
    "/projects",
    "/projects/p1/runs/new",
    "/runs/r1",
    "/runs/r1/review",
    "/todos",
    "/runs/r1/release",
)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expires_future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def _connect(home: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path(home) / "service.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _auth_columns(home: Path) -> set:
    with contextlib.closing(_connect(home)) as conn:
        rows = conn.execute("PRAGMA table_info(auth)").fetchall()
    return {row[1] for row in rows}


def _seed_auth_row(
    conn: sqlite3.Connection,
    *,
    actor: str = _LOCAL_ACTOR,
    display_name: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO auth (actor, password_hash, created_at, display_name)"
        " VALUES (?, ?, ?, ?)",
        (actor, "scrypt$00$00", _now_iso(), display_name),
    )
    conn.commit()


def _seed_session_row(
    conn: sqlite3.Connection, token: str, *, actor: str = _LOCAL_ACTOR
) -> None:
    conn.execute(
        "INSERT INTO sessions (token_hash, actor, csrf_hash, created_at,"
        " expires_at) VALUES (?, ?, ?, ?, ?)",
        (_sha256_hex(token), actor, "c" * 64, _now_iso(), _expires_future()),
    )
    conn.commit()


def _validator():
    """The §1m.2 display-name validation seam; missing symbol = assertion."""
    fn = getattr(auth, "validate_display_name", None)
    assert fn is not None, (
        "auth.validate_display_name missing — interfaces §1m.2 requires "
        "name validation (non-empty, trimmed, <=64 chars, no control "
        "characters) behind POST /api/auth/name"
    )
    return fn


def _expect_invalid(fn: Any, raw: Any) -> None:
    """A rejected name raises ValueError (the 400 validation_failed seam)."""
    try:
        accepted = fn(raw)
    except ValueError:
        return
    raise AssertionError(f"display name {raw!r} must be rejected, got {accepted!r}")


def _effective_actor_fn():
    """The §1m.3 effective-actor seam; missing symbol = assertion."""
    fn = getattr(auth, "effective_actor", None)
    assert fn is not None, (
        "auth.effective_actor missing — interfaces §1m.3 requires the "
        "effective actor rule (display_name ?? auth.actor ?? local-user)"
    )
    return fn


# -- service store: display_name evolution + audit event (§1c #6 / §1a #25) ----


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 auth.name_bound service event
def test_service_events_accept_auth_name_bound(tmp_path: Path):
    store = ServiceDB(tmp_path / "home")
    assert "auth.name_bound" in SERVICE_EVENT_TYPES, (
        "interfaces §1a #25 appends auth.name_bound to the service event "
        "closed set — the binding audit cannot be appended without it"
    )
    store.append_event("auth.name_bound", {"actor": _NAME, "surface": "http"})
    events = [e for e in store.read_events() if e["type"] == "auth.name_bound"]
    assert len(events) == 1, "the audit event appends and reads back once"
    assert events[0]["payload"] == {"actor": _NAME, "surface": "http"}


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 fresh auth table has display_name
def test_fresh_service_store_has_display_name_column(tmp_path: Path):
    home = tmp_path / "home"
    ServiceDB(home)
    assert "display_name" in _auth_columns(home), (
        "interfaces §1c #6: a freshly created auth table must carry the "
        "nullable display_name column (NULL = name not collected yet)"
    )
    with contextlib.closing(_connect(home)) as conn:
        _seed_auth_row(conn)
        stored = conn.execute("SELECT display_name FROM auth").fetchone()
    assert stored is not None and stored[0] is None, (
        "an uncollected name reads back as NULL (§1m.2 unset state)"
    )


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 legacy store evolves in place
def test_legacy_store_evolves_display_name_idempotently(tmp_path: Path):
    home = tmp_path / "legacy-home"
    home.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(str(home / "service.db"))) as conn:
        conn.executescript(_LEGACY_SERVICE_SCHEMA)
        conn.execute(
            "INSERT INTO auth (actor, password_hash, created_at)"
            " VALUES (?, ?, ?)",
            (_LOCAL_ACTOR, "scrypt$00$00", _now_iso()),
        )
        conn.commit()
        conn.row_factory = sqlite3.Row
        _seed_session_row(conn, "legacy-token")
    ServiceDB(home)  # startup over a pre-evolution store
    assert "display_name" in _auth_columns(home), (
        "interfaces §1c #6: an existing store must evolve in place at "
        "startup (PRAGMA table_info probe + ALTER TABLE ADD COLUMN)"
    )
    ServiceDB(home)  # re-opening must stay idempotent (no duplicate ALTER)
    with contextlib.closing(_connect(home)) as conn:
        auth_row = conn.execute("SELECT actor, display_name FROM auth").fetchone()
        session_row = conn.execute(
            "SELECT actor FROM sessions WHERE token_hash = ?",
            (_sha256_hex("legacy-token"),),
        ).fetchone()
    assert auth_row is not None and auth_row["actor"] == _LOCAL_ACTOR, (
        "the evolved store keeps the pre-existing auth row"
    )
    assert auth_row["display_name"] is None
    assert session_row is not None and session_row["actor"] == _LOCAL_ACTOR, (
        "the evolved store keeps the pre-existing sessions row"
    )


# -- auth: display-name validation (interfaces §1m.2) ---------------------------


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    home = tmp_path / "auth-home"
    home.mkdir(parents=True)
    conn = sqlite3.connect(str(home / "service.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_AUTH_SERVICE_SCHEMA)
    conn.commit()
    return conn


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 valid names trim to <=64 chars
def test_validate_display_name_accepts_and_trims():
    validate = _validator()
    assert validate("  Ada Lovelace  ") == _NAME
    assert validate("Grace Hopper") == "Grace Hopper"
    assert validate("A" * 64) == "A" * 64, "64 chars (post-trim) is the cap"
    assert validate("  " + "B" * 64 + "  ") == "B" * 64
    assert validate("拉芙蕾丝·艾达") == "拉芙蕾丝·艾达"


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 empty/overlong/control rejected
def test_validate_display_name_rejects_invalid():
    validate = _validator()
    for raw in (
        "",
        "   ",
        "\t \n",
        "C" * 65,
        "  " + "C" * 65 + "  ",
        "Ada\x00Lovelace",
        "Ada\nLovelace",
        "Ada\x1f",
        "Ada\x7f",
        None,
        123,
    ):
        _expect_invalid(validate, raw)


# -- auth: effective actor (interfaces §1m.3) -----------------------------------


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 effective actor precedence
def test_effective_actor_display_name_then_auth_row_then_fallback(db):
    effective = _effective_actor_fn()
    _seed_auth_row(db, display_name=_NAME)
    assert effective(db) == _NAME, "a collected display_name wins (§1m.3)"
    db.execute("UPDATE auth SET display_name = NULL")
    db.commit()
    assert effective(db) == _LOCAL_ACTOR, (
        "without display_name the auth row actor applies"
    )
    db.execute("UPDATE auth SET actor = 'alice'")
    db.commit()
    assert effective(db) == "alice", "a provisioned auth actor is second in line"
    db.execute("DELETE FROM auth")
    db.commit()
    assert effective(db) == _LOCAL_ACTOR, (
        "the local-user fallback applies when no auth row exists"
    )


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 session resolve uses effective actor
def test_resolve_session_actor_follows_effective_actor(db):
    _seed_auth_row(db, actor=_LOCAL_ACTOR, display_name=_NAME)
    _seed_session_row(db, "token-1")
    resolved = auth.resolve_session(db, "token-1")
    assert resolved is not None, "the seeded session row must resolve (§1f.2)"
    assert resolved.actor == _NAME, (
        "interfaces §1m.3: Session.actor at resolution equals the effective "
        "actor (display_name first), not the stale pre-binding row actor"
    )
    db.execute("UPDATE auth SET display_name = NULL")
    db.commit()
    unbound = auth.resolve_session(db, "token-1")
    assert unbound is not None and unbound.actor == _LOCAL_ACTOR
    db.execute("DELETE FROM auth")
    db.commit()
    fallback = auth.resolve_session(db, "token-1")
    assert fallback is not None and fallback.actor == _LOCAL_ACTOR, (
        "with no auth row the local-user fallback applies (§1m.3)"
    )


# -- the ASGI seam: real protocol drives over a real service home ---------------


@dataclass
class _Driven:
    status: int
    headers: dict
    set_cookies: list
    body: Any


def _request_scope(method: str, path: str, options: dict) -> dict:
    raw_headers = [(b"host", b"testserver")]
    if options.get("json_body") is not None:
        raw_headers.append((b"content-type", b"application/json"))
    cookie = options.get("cookie")
    if cookie is not None:
        raw_headers.append((b"cookie", f"trac_session={cookie}".encode()))
    for key, value in (options.get("headers") or {}).items():
        raw_headers.append((key.encode(), value.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("utf-8"),
        "headers": raw_headers,
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "http",
        "root_path": "",
        "client": ("127.0.0.1", 111),
        "server": ("127.0.0.1", 80),
    }
    scope["asgi"] = {"version": "3.0", "spec_version": "2.3"}
    return scope


def _request_body(options: dict) -> bytes:
    body = options.get("json_body")
    return b"" if body is None else json.dumps(body).encode("utf-8")


def _run_asgi(app: Any, scope: dict, payload: bytes) -> list:
    messages: list = []

    async def receive() -> dict:
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return messages


def _parse_start(start: dict) -> tuple:
    headers: dict = {}
    set_cookies: list = []
    for key, value in start["headers"]:
        decoded = key.decode("latin-1").lower()
        if decoded == "set-cookie":
            set_cookies.append(value.decode("latin-1"))
        else:
            headers[decoded] = value.decode("latin-1")
    return int(start["status"]), headers, set_cookies


def _parse_body(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _drive(app: Any, method: str, path: str, **options: Any) -> _Driven:
    scope = _request_scope(method, path, options)
    messages = _run_asgi(app, scope, _request_body(options))
    start = next(m for m in messages if m["type"] == "http.response.start")
    raw = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    status, headers, set_cookies = _parse_start(start)
    return _Driven(status, headers, set_cookies, _parse_body(raw))


def _composition(tmp_path: Path):
    """Real ServiceDB home + CommandService + the create_app root (§1.1)."""
    home = tmp_path / "service-home"
    store = ServiceDB(home)
    config = SimpleNamespace(permitted_repos=[], secret_values={})
    supervisor = SimpleNamespace(db=store)
    service = CommandService(home, store, config)
    with contextlib.closing(_connect(home)) as conn:
        auth.provision_password(conn, _PASSWORD)
    app = create_app(home, service, supervisor, config)
    return home, store, app


def _login(app: Any, password: str = _PASSWORD) -> _Driven:
    return _drive(app, "POST", "/api/auth/login", json_body={"password": password})


def _session_token(driven: _Driven) -> str:
    for line in driven.set_cookies:
        if line.startswith("trac_session="):
            return line.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError("login must set the trac_session cookie (§1f.2)")


def _bind_name(app: Any, token: str, csrf: str, name: Any) -> _Driven:
    return _drive(
        app,
        "POST",
        "/api/auth/name",
        json_body={"name": name},
        headers={"x-trac-csrf": csrf},
        cookie=token,
    )


# -- app: login name_required (interfaces §2b #1 / §1m.2) -----------------------


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 login flags name_required=true
def test_login_response_carries_name_required_true_when_unset(tmp_path: Path):
    _, _, app = _composition(tmp_path)
    driven = _login(app)
    assert driven.status == 200, "the provisioned password logs in (§2b #1)"
    assert driven.body.get("name_required") is True, (
        "a successful login over an uncollected display_name must answer "
        "name_required=true so the login page routes to the name step (§1m.2)"
    )


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 name bound then re-login
def test_login_name_required_false_after_binding(tmp_path: Path):
    _, _, app = _composition(tmp_path)
    first = _login(app)
    assert first.status == 200
    token = _session_token(first)
    bound = _bind_name(app, token, first.body["csrf_token"], _NAME)
    assert bound.status == 200, (
        "POST /api/auth/name must accept a valid name (§2b #29)"
    )
    assert bound.body.get("actor") == _NAME
    rebound = _bind_name(app, token, first.body["csrf_token"], _NAME)
    assert rebound.status == 200 and rebound.body.get("actor") == _NAME, (
        "POST /api/auth/name is idempotently re-submittable (§2b #29)"
    )
    second = _login(app)
    assert second.body.get("name_required") is False, (
        "once display_name is collected the login response must answer "
        "name_required=false (§2b #1)"
    )
    assert second.body.get("actor") == _NAME, (
        "issuance uses the effective actor (§1m.3): a session issued after "
        "the binding carries the display name"
    )


# -- app: POST /api/auth/name (interfaces §2b #29 / §1m.2) ----------------------


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 binding triple persists
def test_post_name_binds_display_sessions_and_audit(tmp_path: Path):
    home, store, app = _composition(tmp_path)
    with contextlib.closing(_connect(home)) as conn:
        _seed_session_row(conn, "seeded-token")
    login = _login(app)
    token = _session_token(login)
    driven = _bind_name(app, token, login.body["csrf_token"], _NAME)
    assert driven.status == 200, "POST /api/auth/name is the §2b #29 binding face"
    assert driven.body.get("actor") == _NAME, "the response carries the live name"
    with contextlib.closing(_connect(home)) as conn:
        auth_row = conn.execute("SELECT display_name FROM auth").fetchone()
        session_actors = [
            row[0] for row in conn.execute("SELECT actor FROM sessions").fetchall()
        ]
    assert auth_row is not None and auth_row[0] == _NAME, (
        "binding writes auth.display_name (§1m.2)"
    )
    assert session_actors and set(session_actors) == {_NAME}, (
        "binding updates every existing sessions row actor to the name (§1m.2)"
    )
    events = [e for e in store.read_events() if e["type"] == "auth.name_bound"]
    assert len(events) == 1, "binding appends exactly one audit event (§1a #25)"
    assert events[0]["payload"] == {"actor": _NAME, "surface": "http"}


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 invalid names rejected with 400
def test_post_name_rejects_invalid_names_with_validation_failed(tmp_path: Path):
    home, _, app = _composition(tmp_path)
    login = _login(app)
    token = _session_token(login)
    csrf = login.body["csrf_token"]
    for raw in ("", "   ", "C" * 65, "Ada\x00Lovelace", "Ada\nLovelace", None, 123):
        driven = _bind_name(app, token, csrf, raw)
        assert driven.status == 400, (
            f"name {raw!r} must fail validation with 400 (§2b #29)"
        )
        assert driven.body["error"]["reason"] == "validation_failed"
    with contextlib.closing(_connect(home)) as conn:
        row = conn.execute("SELECT display_name FROM auth").fetchone()
    assert row is not None and row[0] is None, (
        "rejected submissions must not collect a name (§1m.2)"
    )


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 name binding enforces CSRF
def test_post_name_rejects_missing_or_wrong_csrf(tmp_path: Path):
    _, store, app = _composition(tmp_path)
    login = _login(app)
    token = _session_token(login)
    missing = _drive(
        app, "POST", "/api/auth/name", json_body={"name": _NAME}, cookie=token
    )
    assert missing.status == 403, (
        "POST /api/auth/name without the X-Trac-CSRF header must be "
        "rejected with 403 (§2b #29)"
    )
    wrong = _bind_name(app, token, "0" * 64, _NAME)
    assert wrong.status == 403, "a mismatched CSRF credential is rejected (§1f.3)"
    assert not [
        e for e in store.read_events() if e["type"] == "auth.name_bound"
    ], "rejected submissions must leave no binding audit behind"


# -- app: GET /api/auth/profile (interfaces §2b #30) ----------------------------


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 profile reports actor and name_set
def test_get_profile_reports_actor_and_name_set(tmp_path: Path):
    _, _, app = _composition(tmp_path)
    login = _login(app)
    token = _session_token(login)
    before = _drive(app, "GET", "/api/auth/profile", cookie=token)
    assert before.status == 200, "GET /api/auth/profile is the §2b #30 read face"
    assert before.body.get("actor") == _LOCAL_ACTOR
    assert before.body.get("name_set") is False, (
        "profile reports the uncollected state so the login page can pick "
        "the name step (§1.0.3)"
    )
    bound = _bind_name(app, token, login.body["csrf_token"], _NAME)
    assert bound.status == 200
    after = _drive(app, "GET", "/api/auth/profile", cookie=token)
    assert after.status == 200
    assert after.body.get("actor") == _NAME
    assert after.body.get("name_set") is True


# -- app: workbench name gate (interfaces §1m.2) --------------------------------


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-WEBAUTH-001 workbench gates on collected name
def test_workbench_pages_redirect_to_login_until_name_bound(tmp_path: Path):
    _, _, app = _composition(tmp_path)
    login = _login(app)
    token = _session_token(login)
    for path in _WORKBENCH_PATHS:
        gated = _drive(app, "GET", path, cookie=token)
        assert gated.status == 302 and gated.headers.get("location") == "/login", (
            f"a session without a collected name must not reach {path} "
            "(§1m.2: workbench entries redirect to /login until the name "
            "step completes)"
        )
    login_page = _drive(app, "GET", "/login", cookie=token)
    assert login_page.status == 200, "the login page itself stays reachable"
    bound = _bind_name(app, token, login.body["csrf_token"], _NAME)
    assert bound.status == 200
    for path in _WORKBENCH_PATHS:
        served = _drive(app, "GET", path, cookie=token)
        assert served.status == 200, (
            f"after the name is bound, {path} serves the workbench again"
        )


# -- app: logout clears credentials (interfaces §1m.4) --------------------------


# AC-FR0320-02@v0.10 TRACKS-TRACE IF-WEBAUTH-001 logout clears cookie and row
# AC-FR0318-03@v0.10 TRACKS-TRACE IF-WEBAUTH-001 Account logout leaves no credentials
def test_logout_clears_cookie_and_session_row(tmp_path: Path):
    home, _, app = _composition(tmp_path)
    login = _login(app)
    token = _session_token(login)
    driven = _drive(app, "POST", "/api/auth/logout", cookie=token)
    clearing = [
        line
        for line in driven.set_cookies
        if line.startswith("trac_session=")
        and (
            "max-age=0" in line.lower()
            or "expires=thu, 01 jan 1970" in line.lower()
        )
    ]
    assert clearing, (
        "logout must clear the trac_session cookie in its response (§1m.4: "
        "the browser keeps no residual credentials)"
    )
    assert driven.status == 204
    with contextlib.closing(_connect(home)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE token_hash = ?",
            (_sha256_hex(token),),
        ).fetchone()
    assert row[0] == 0, "logout deletes the server-side session row (§1m.4)"


# -- app: create_app route-assembly extension seam (CORE-02, T-001) -------------


# AC-FR0322-01@v0.10 TRACKS-TRACE IF-QUERY-001 create_app merges extension routes
def test_create_app_merges_api_query_extension_routes(tmp_path, monkeypatch):
    async def _tree(request: Any) -> Any:
        return SimpleNamespace(status_code=200, payload={"extension_route": True})

    monkeypatch.setattr(
        api_query,
        "EXTENSION_ROUTES",
        (("/api/projects/p1/docs/tree", _tree, ("GET",)),),
        raising=False,
    )
    _, _, app = _composition(tmp_path)
    login = _login(app)
    token = _session_token(login)
    driven = _drive(app, "GET", "/api/projects/p1/docs/tree", cookie=token)
    assert driven.status == 200, (
        "the create_app route assembly must merge the extension routes "
        "declared on the api_query side (task T-001 CORE-02 seam, initially "
        "empty) so the §2b #31-34 read endpoints land without re-touching "
        "app.py"
    )
    assert driven.body.get("extension_route") is True
