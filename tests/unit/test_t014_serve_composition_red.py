"""T-014 RED: service composition & entry (IF-SERVE-001).

Devon-owned unit RED for the T-014 delivery slice — ``tracks/cli/main.py``
(serve registration + USAGE sync), ``tracks/cli/serve_cmd.py`` (cmd_serve)
and ``tracks/server/app.py`` (create_app / health_response), per the locked
design: interfaces §0/§2a (serve grammar, stdout/exit-code classification),
§1f.1 (first-start password provisioning is fail-closed), §2b #3 (healthz
payload) and architecture §1.1 (composition root wiring).

Pinned here, at the only layer that must stay importable without starlette:

- ``serve`` is registered in ``cli.main._COMMANDS`` bound to
  ``serve_cmd.cmd_serve`` and the USAGE line is synced (§2a) — the
  registration the frozen serve-lifecycle anchor drives through ``trac serve``;
- first start with no password supply channel exits 2 with stderr guidance
  naming a supply channel (§1f.1/§2a) — the fail-closed gate that precedes
  any server start, so the recovery startup sequence (AC-FR0288-02, frozen
  anchor ``test_restart_recovers_registry_commands_waits``) can never run
  unprovisioned;
- ``--repo`` is mandatory grammar (§2a);
- ``health_response`` returns the 200 shape
  ``{status, projects, version, uptime_s}`` over a real service.db and the
  503 shape ``{status: unavailable, reasons: [{check, reason}, ...]}`` when
  the store cannot be opened (§2b #3, AC-FR0288-03 handler contract — no
  fake "available");
- ``create_app`` returns the ASGI application uvicorn runs (§1.1), carries
  the documented request seam (``request.app.state.home/.service/
  .permitted_repos`` — api_query/api_command docstrings), dispatches the
  public ``/healthz`` route through ``health_response``, and assembles the
  auth boundary in front of ``/api/*`` (§1f.3).

The process loop itself (uvicorn serve, graceful stop, recover-on-startup
wiring, started/stopped events) is pinned at integration by the frozen
serve-lifecycle suite; unit level does not spawn servers. Guards convert the
IF-SERVE-001 stubs into behavioral AssertionError (no stub_token, no
assembly errors); the ASGI drives below speak the real protocol uvicorn
speaks (scope/receive/send) — nothing mocks the system under test.

AC: FR-0288 — TRACKS-TRACE IF-SERVE-001.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tracks.cli import main as cli_main
from tracks.cli import serve_cmd
from tracks.server.app import create_app, health_response
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.scheduler import Scheduler
from tracks.supervisor.service import CommandService
from tracks.supervisor.worker import WorkerManager

_IF = "IF-SERVE-001"
_ACTOR = "local-user"


# -- stub guards: the IF-SERVE-001 stubs must be replaced by behavior --------


def _guarded(action: str, fn, *args, **kwargs):
    """Call one IF-SERVE-001 face; a stub token becomes a real assertion."""
    try:
        return fn(*args, **kwargs)
    except NotImplementedError as exc:
        raise AssertionError(
            f"{_IF} {action} is still a stub: {exc} — the serve composition "
            "root must be replaced by real behavior (interfaces §1.1/§2a)"
        ) from None


# -- fixtures: real service plane stores, seeded through the contract --------


def _service_home(tmp_path: Path) -> Path:
    home = tmp_path / "service-home"
    ServiceDB(home)
    return home


def _make_service(home: Path) -> CommandService:
    return CommandService(home, ServiceDB(home), SimpleNamespace())


def _serve_config(repo: Path) -> SimpleNamespace:
    """Effective serve config carried by cmd_serve into the root (§1j)."""
    return SimpleNamespace(
        permitted_repos=[repo],
        host="127.0.0.1",
        port=0,
        wait_initial_s=60,
        wait_cap_s=900,
        poll_interval_s=5,
        poll_idle_cap_s=60,
        lease_ttl_s=30,
    )


def _supervisor(home: Path, config: SimpleNamespace) -> SimpleNamespace:
    db = ServiceDB(home)
    scheduler = Scheduler(db)
    worker = WorkerManager(db, scheduler, config)
    return SimpleNamespace(db=db, scheduler=scheduler, worker=worker)


def _register_project(home: Path, repo: Path) -> None:
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", _ACTOR, "2026-09-21T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _plain_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".tracks").mkdir(parents=True)
    return repo


def _clear_serve_env(monkeypatch) -> None:
    monkeypatch.delenv("TRAC_SERVE_PASSWORD", raising=False)
    monkeypatch.delenv("TRAC_SERVE_HOME", raising=False)


# -- the ASGI seam: scope/receive/send doubles, the protocol uvicorn speaks --


def _asgi_drive(
    app, method: str, path: str, *, headers: dict | None = None
) -> tuple[int, dict]:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"accept", b"application/json"),
            *[(key.encode(), value.encode()) for key, value in (headers or {}).items()],
        ],
        "client": ("127.0.0.1", 111),
        "server": ("127.0.0.1", 80),
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return start["status"], json.loads(body or b"{}")


# -- serve registration (cli/main.py, interfaces §0/§2a) ---------------------


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 serve registered with USAGE sync
def test_serve_registered_in_commands_and_usage():
    entry = cli_main._COMMANDS.get("serve")
    assert entry is not None, (
        "trac serve must be registered in cli.main._COMMANDS (interfaces §2a: "
        "the serve subcommand grammar is the service lifecycle entry)"
    )
    assert entry[0] is serve_cmd.cmd_serve, (
        "_COMMANDS['serve'] must dispatch to serve_cmd.cmd_serve"
    )
    assert re.search(r"\bserve\b", cli_main.USAGE), (
        "USAGE must be synced with the serve registration (§2a)"
    )


# -- cmd_serve: fail-closed startup gates (interfaces §1f.1/§2a) --------------


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 no-password first start exits 2
def test_first_start_without_password_exits_2_with_guidance(
    tmp_path: Path, monkeypatch, capsys
):
    _clear_serve_env(monkeypatch)
    repo = _plain_repo(tmp_path)

    code = _guarded(
        "cmd_serve", serve_cmd.cmd_serve, tmp_path, "--repo", str(repo)
    )

    assert code == 2, "first start without any password supply channel exits 2 (§2a)"
    err = capsys.readouterr().err
    assert "password" in err.lower(), "stderr must carry provisioning guidance"
    assert ("--password-stdin" in err) or ("TRAC_SERVE_PASSWORD" in err), (
        "guidance must name a supply channel: --password-stdin or "
        "TRAC_SERVE_PASSWORD (§1f.1)"
    )


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 --repo is mandatory grammar
def test_serve_requires_repo_flag(tmp_path: Path, monkeypatch, capsys):
    _clear_serve_env(monkeypatch)

    code = _guarded("cmd_serve", serve_cmd.cmd_serve, tmp_path)

    assert code != 0, "serve without --repo must not start (§2a grammar)"
    assert capsys.readouterr().err.strip(), "the grammar rejection must be explained"


# -- health_response (server/app.py, interfaces §2b #3) -----------------------


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 healthz ok shape over real store
def test_health_response_ok_shape_with_registered_project(tmp_path: Path):
    repo = _plain_repo(tmp_path)
    home = _service_home(tmp_path)
    _register_project(home, repo)

    payload = _guarded("health_response", health_response, home)

    assert payload.get("status") == "ok"
    assert payload.get("projects") == 1, "projects counts the registered rows"
    assert isinstance(payload.get("version"), str) and payload["version"], (
        "version is a non-empty string (§2b #3)"
    )
    assert isinstance(payload.get("uptime_s"), float) and payload["uptime_s"] >= 0


# AC-FR0288-03@v0.9 TRACKS-TRACE IF-SERVE-001 broken store -> structured 503 shape
def test_health_response_unavailable_reports_structured_reasons(tmp_path: Path):
    home = tmp_path / "service-home"
    home.mkdir(parents=True)
    (home / "service.db").mkdir()  # the store cannot be opened

    payload = _guarded("health_response", health_response, home)

    assert payload.get("status") == "unavailable", (
        "an unopenable store must not fake availability (AC-FR0288-03)"
    )
    reasons = payload.get("reasons")
    assert isinstance(reasons, list) and reasons, "reasons is a non-empty list"
    for item in reasons:
        assert isinstance(item.get("check"), str) and item["check"], (
            "each reason names its check"
        )
        assert isinstance(item.get("reason"), str) and item["reason"], (
            "each reason carries the concrete cause"
        )


# -- create_app: the composition root (architecture §1.1) --------------------


def _composition(tmp_path: Path):
    repo = _plain_repo(tmp_path)
    home = _service_home(tmp_path)
    config = _serve_config(repo)
    service = _make_service(home)
    supervisor = _supervisor(home, config)
    app = _guarded(
        "create_app", create_app, home, service, supervisor, config
    )
    return repo, app


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 root exposes the request seam
def test_create_app_exposes_documented_state_seam(tmp_path: Path):
    repo, app = _composition(tmp_path)

    assert callable(app), "create_app returns the ASGI application uvicorn runs"
    state = app.state
    assert Path(state.home).name == "service-home", (
        "request.app.state.home is the service home (api_query/api_command seam)"
    )
    assert state.service is not None, "the command service is injected"
    assert [Path(p) for p in state.permitted_repos] == [repo], (
        "request.app.state.permitted_repos is the serve --repo scope (§1g.2)"
    )


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 public healthz route assembled
def test_create_app_serves_public_healthz(tmp_path: Path):
    _, app = _composition(tmp_path)

    status, body = _asgi_drive(app, "GET", "/healthz")

    assert status == 200
    assert body.get("status") == "ok"
    assert isinstance(body.get("projects"), int), (
        "/healthz dispatches through health_response over the wired home"
    )


# AC-FR0288-01@v0.9 TRACKS-TRACE IF-SERVE-001 auth boundary assembled on /api
def test_create_app_rejects_unauthenticated_api(tmp_path: Path):
    _, app = _composition(tmp_path)

    status, body = _asgi_drive(app, "GET", "/api/projects")

    assert status == 401, "unauthenticated /api access is rejected (§1f.3)"
    assert "unauthenticated" in json.dumps(body).lower(), (
        "the 401 body carries the unauthenticated reason token"
    )
