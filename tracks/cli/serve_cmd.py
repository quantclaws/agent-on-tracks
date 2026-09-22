"""``trac serve`` — service lifecycle entry (IF-SERVE-001).

Single-command manual start/stop of the v0.9 web service (no daemon /
autostart / log rotation this version). Parses the grammar of interfaces
§2a, provisions the first-start password (scrypt through
``tracks/server/auth.py``), builds the composition root (§1.1:
``ServiceDB`` -> ``recover_on_startup`` -> ``CommandService`` -> scheduler +
worker manager -> ``create_app``), runs the supervisor drive loop as a
background task of the serve process and serves the Starlette application
under uvicorn until SIGINT/SIGTERM, then appends ``service.stopped`` and
exits 0.

Startup failure classification (§2a): unusable store or occupied port ->
exit 1 with the concrete reason on stderr; first start without any password
supply channel -> exit 2 with provisioning guidance. ``--port 0`` binds a
random free port and the real bound port is what gets announced.
"""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import __version__
from tracks.server import auth
from tracks.server.app import create_app
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.lease import acquire_lease
from tracks.supervisor.recover import recover_on_startup
from tracks.supervisor.scheduler import Scheduler
from tracks.supervisor.service import CommandService
from tracks.supervisor.worker import WorkerManager

# (option name, default) per §2a scalar flag; int-typed flags are the ones
# whose default is an int. One table is the single source for the serve
# grammar; ``--repo`` is the one repeatable accumulator flag.
_FLAG_SPECS: dict[str, tuple[str, Any]] = {
    "--host": ("host", "127.0.0.1"),
    "--home": ("home", None),
    "--port": ("port", 8000),
    "--wait-initial-s": ("wait_initial_s", 60),
    "--wait-cap-s": ("wait_cap_s", 900),
    "--poll-interval-s": ("poll_interval_s", 5),
    "--poll-idle-cap-s": ("poll_idle_cap_s", 60),
}
_REPEAT_FLAG = "--repo"
_BOOL_FLAGS = frozenset({"--password-stdin"})
_PASSWORD_HINT = (
    "trac serve: first start needs a password before logins are accepted.\n"
    "supply it with --password-stdin, the TRAC_SERVE_PASSWORD environment\n"
    "variable, or an interactive terminal prompt, then start trac serve again."
)
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|KEY|CREDENTIAL", re.IGNORECASE)


class _UsageError(ValueError):
    """A §2a grammar violation (unknown flag, missing value, missing --repo)."""


# -- flag parsing (interfaces §2a grammar) ------------------------------------


def _parse_serve_args(args: list[str]) -> dict:
    opts: dict[str, Any] = {"repo": [], "home": None, "password_stdin": False}
    for name, default in _FLAG_SPECS.values():
        opts[name] = default
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in _BOOL_FLAGS:
            opts["password_stdin"] = True
            index += 1
            continue
        spec = _FLAG_SPECS.get(flag)
        if spec is None and flag != _REPEAT_FLAG:
            raise _UsageError(f"unknown serve flag: {flag}")
        if index + 1 >= len(args):
            raise _UsageError(f"serve flag {flag} needs a value")
        value = args[index + 1]
        index += 2
        if flag == _REPEAT_FLAG:
            opts["repo"].append(Path(value).expanduser().resolve())
        else:
            _apply_flag(opts, flag, spec, value)
    if not opts["repo"]:
        raise _UsageError("serve requires at least one --repo <path>")
    return opts


def _apply_flag(opts: dict, flag: str, spec: tuple[str, Any], value: str) -> None:
    name, default = spec
    if isinstance(default, int):
        opts[name] = _positive_int(flag, value)
    else:
        opts[name] = value


def _positive_int(flag: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise _UsageError(f"serve flag {flag} needs an integer, got {value!r}") from exc
    if parsed < 0:
        raise _UsageError(f"serve flag {flag} needs a non-negative integer, got {value!r}")
    return parsed


# -- composition helpers ------------------------------------------------------


def _resolve_home(opts: dict) -> Path:
    """``--home`` flag first, then ``TRAC_SERVE_HOME``, else the default.

    Interfaces §2a: the default is ``<first --repo>/.tracks/service`` and
    ``TRAC_SERVE_HOME`` overrides that default; an explicit ``--home``
    flag is the operator's direct choice and wins over both.
    """
    if opts.get("home"):
        return Path(opts["home"]).expanduser().resolve()
    override = os.environ.get("TRAC_SERVE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return opts["repo"][0] / ".tracks" / "service"


def _open_store(home: Path) -> ServiceDB | None:
    try:
        return ServiceDB(home)
    except (sqlite3.Error, OSError) as exc:
        _log("error", f"service store at {home} is unusable: {exc}")
        return None


def _connect_store(home: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(home / "service.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _needs_password(home: Path) -> bool:
    """First start = an empty auth table (interfaces §1f.1)."""
    if not (home / "service.db").exists():
        return False
    with contextlib.closing(_connect_store(home, readonly=True)) as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM auth").fetchone()[0])
    return count == 0


def _supply_password(opts: dict) -> str | None:
    """The §1f.1 supply channels: stdin, env, interactive TTY — else None."""
    if opts["password_stdin"]:
        password = sys.stdin.readline().strip()
        return password or None
    from_env = os.environ.get("TRAC_SERVE_PASSWORD")
    if from_env:
        return from_env
    if sys.stdin.isatty():
        return getpass.getpass("trac serve password: ")
    return None


def _collect_secret_values() -> dict[str, str]:
    """Protected env credential values, held in memory only (§1g.3)."""
    return {
        name: value
        for name, value in os.environ.items()
        if value and _SECRET_NAME.search(name)
    }


def _build_config(opts: dict, home: Path) -> SimpleNamespace:
    return SimpleNamespace(
        permitted_repos=list(opts["repo"]),
        host=opts["host"],
        port=opts["port"],
        home=home,
        wait_initial_s=opts["wait_initial_s"],
        wait_cap_s=opts["wait_cap_s"],
        poll_interval_s=opts["poll_interval_s"],
        poll_idle_cap_s=opts["poll_idle_cap_s"],
        lease_ttl_s=30,
        secret_values=_collect_secret_values(),
    )


# -- serving ------------------------------------------------------------------


class _AnnouncingServer:
    """uvicorn server that announces the real bound port after startup.

    Signals bridge through a pre-installed handler: uvicorn 0.49 captures
    the original handlers, shuts down gracefully on SIGINT/SIGTERM, restores
    them and re-raises the signal — the bridge then records
    ``service.stopped`` and exits 0 (§2a graceful-stop contract) instead of
    dying with the default signal disposition.
    """

    def __init__(
        self,
        app: Any,
        opts: dict,
        home: Path,
        db: ServiceDB,
        stop: threading.Event,
        thread: threading.Thread,
    ):
        import uvicorn

        self._db = db
        self._opts = opts
        self._home = home
        self._stop = stop
        self._thread = thread
        self._started_at = time.monotonic()
        outer = self

        class _Server(uvicorn.Server):
            async def startup(self, sockets: Any = None) -> None:
                await super().startup(sockets)
                if self.started:
                    outer._announce(self)

        self._server = _Server(
            uvicorn.Config(
                app, host=opts["host"], port=opts["port"],
                log_level="warning", access_log=False,
            )
        )
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._bridge_signal)

    def _bridge_signal(self, sig: int, frame: Any) -> None:
        """Finish the graceful stop: bookkeeping, then exit 0 (§2a)."""
        del sig, frame
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._db.append_event(
                "service.stopped",
                {
                    "reason": "signal",
                    "uptime_s": round(time.monotonic() - self._started_at, 3),
                },
            )
        except sqlite3.Error as exc:
            _log("error", f"could not record service.stopped: {exc}")
        raise SystemExit(0)

    def _announce(self, server: Any) -> None:
        port = _bound_port(server, self._opts["port"])
        print(f"serving on http://{self._opts['host']}:{port} (pid {os.getpid()})", flush=True)
        print("Ctrl-C to stop", flush=True)
        self._db.append_event(
            "service.started",
            {
                "pid": os.getpid(),
                "host": self._opts["host"],
                "port": port,
                "home": str(self._home),
                "permitted_repos": [str(p) for p in self._opts["repo"]],
                "version": __version__,
            },
        )

    def run(self) -> bool:
        """Serve until stopped; returns only without a stop signal (bind path)."""
        self._server.run()
        return bool(self._server.started)


def _bound_port(server: Any, fallback: int) -> int:
    for listener in getattr(server, "servers", []) or []:
        for sock in getattr(listener, "sockets", []) or []:
            try:
                return int(sock.getsockname()[1])
            except (OSError, TypeError, IndexError):
                continue
    return fallback


def _supervisor_loop(
    db: ServiceDB, service: CommandService, scheduler: Scheduler,
    worker: WorkerManager, config: Any, stop: threading.Event,
) -> None:
    """Background drive task of the serve process (architecture §1.1).

    Re-drives accepted ``drive_run`` commands (the recovery path requeued
    them) and accepts a fresh drive for the runnable run, one attempt per
    poll interval per run — never a hot loop (NFR-0152).
    """
    interval = max(0.5, float(getattr(config, "poll_interval_s", 5) or 5))
    ttl = int(getattr(config, "lease_ttl_s", 30) or 30)
    worker_id = f"supervisor-{os.getpid()}"
    state = _DriveState()
    while not stop.is_set():
        try:
            _supervisor_tick(db, service, scheduler, worker, worker_id, ttl, interval, state)
        except Exception as exc:  # resilience: one bad tick must not kill the service
            _log("error", f"supervisor tick failed: {exc}")
        stop.wait(interval)


@dataclass
class _DriveState:
    """In-memory bookkeeping of the serve-process drive loop."""

    in_flight: set[str] = field(default_factory=set)
    cooldown: dict[str, float] = field(default_factory=dict)


def _supervisor_tick(
    db: ServiceDB, service: CommandService, scheduler: Scheduler,
    worker: WorkerManager, worker_id: str, ttl: int, interval: float,
    state: _DriveState,
) -> None:
    for finished in worker.poll():
        state.in_flight.discard(str(finished.get("run_id") or ""))
    now = time.monotonic()
    _drive_accepted(db, worker, worker_id, ttl, interval, state, now)
    runnable = scheduler.next_runnable()
    throttled = (
        not runnable
        or runnable in state.in_flight
        or now - state.cooldown.get(runnable, 0.0) < interval
    )
    if not throttled:
        _accept_fresh_drive(
            db, service, worker, worker_id, ttl, runnable, state, now
        )


def _drive_accepted(
    db: ServiceDB, worker: WorkerManager, worker_id: str, ttl: int,
    interval: float, state: _DriveState, now: float,
) -> None:
    """Re-drive accepted drive_run commands (the recovery requeue path)."""
    for row in _accepted_drive_runs(db):
        run_id = str(row.get("run_id") or "")
        throttled = (
            not run_id
            or run_id in state.in_flight
            or now - state.cooldown.get(run_id, 0.0) < interval
        )
        if throttled:
            continue
        if worker.spawn(row, acquire_lease(db, run_id, worker_id, ttl)):
            state.in_flight.add(run_id)
        else:
            state.cooldown[run_id] = now


def _accept_fresh_drive(
    db: ServiceDB, service: CommandService, worker: WorkerManager,
    worker_id: str, ttl: int, runnable: str, state: _DriveState, now: float,
) -> None:
    """Accept a new drive_run for the runnable run and hand it to a worker."""
    receipt = service.accept(
        "drive_run",
        {"run_id": runnable},
        actor=worker_id,
        actor_class="system",
        surface="internal",
        idempotency_key=None,
    )
    if receipt.deduplicated:
        return
    command = db.get_command(receipt.command_id)
    if command is not None and worker.spawn(
        dict(command), acquire_lease(db, runnable, worker_id, ttl)
    ):
        state.in_flight.add(runnable)
    else:
        state.cooldown[runnable] = now


def _accepted_drive_runs(db: ServiceDB) -> list[dict]:
    with contextlib.closing(_connect_store(db.home, readonly=True)) as conn:
        rows = conn.execute(
            "SELECT command_id, kind, run_id, params_json FROM commands"
            " WHERE status = 'accepted' AND kind = 'drive_run'"
        ).fetchall()
    return [dict(row) for row in rows]


def _log(level: str, msg: str) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    print(json.dumps(row), file=sys.stderr, flush=True)


# -- the entry face -----------------------------------------------------------


def cmd_serve(repo: Path, *args: str) -> int:
    """Parse serve flags and run the service until stopped (interfaces §2a)."""
    del repo  # the command context; the serve scope comes from --repo flags
    try:
        opts = _parse_serve_args(list(args))
    except _UsageError as exc:
        print(f"trac serve: {exc}", file=sys.stderr)
        return 1
    home = _resolve_home(opts)
    db = _open_store(home)
    if db is None:
        return 1
    if _needs_password(home):
        password = _supply_password(opts)
        if password is None:
            print(_PASSWORD_HINT, file=sys.stderr)
            return 2
        with contextlib.closing(_connect_store(home)) as conn:
            auth.provision_password(conn, password)
        _log("info", "first-start password provisioned")
    return _serve(opts, home, db)


def _serve(opts: dict, home: Path, db: ServiceDB) -> int:
    _log("info", f"startup recovery: {json.dumps(recover_on_startup(db))}")
    config = _build_config(opts, home)
    service = CommandService(home, db, config)
    scheduler = Scheduler(db)
    worker = WorkerManager(db, scheduler, config)
    supervisor = SimpleNamespace(db=db, scheduler=scheduler, worker=worker)
    app = create_app(home, service, supervisor, config)
    stop, thread = _start_supervisor(db, service, scheduler, worker, config)
    server = _AnnouncingServer(app, opts, home, db, stop, thread)
    if not server.run():
        stop.set()
        thread.join(timeout=2)
        print(
            f"trac serve: could not serve http://{opts['host']}:{opts['port']}"
            " (port occupied or bind failure)",
            file=sys.stderr,
        )
        return 1
    return 0


def _start_supervisor(
    db: ServiceDB, service: CommandService, scheduler: Scheduler,
    worker: WorkerManager, config: Any,
) -> tuple[threading.Event, threading.Thread]:
    """Run the drive loop as a background task of the serve process (§1.1)."""
    stop = threading.Event()
    thread = threading.Thread(
        target=_supervisor_loop,
        args=(db, service, scheduler, worker, config, stop),
        name="trac-supervisor",
        daemon=True,
    )
    thread.start()
    return stop, thread
