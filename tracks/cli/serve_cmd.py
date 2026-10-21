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

v0.11 (interfaces §1j.3/§1f.1): the supervisor tick additionally claims
accepted human-gate commands for worker execution and takes submitted
intakes into the SM-01 evaluation window (crash recovery included) — the
self-drive closure's tick half; the drive channels are unchanged.

Startup failure classification (§2a): unusable store or occupied port ->
exit 1 with the concrete reason on stderr; first start without any password
supply channel -> exit 2 with provisioning guidance. ``--port 0`` binds a
random free port and the real bound port is what gets announced.
"""

from __future__ import annotations

import contextlib
import contextvars
import getpass
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import __version__, paths, project_runtime
from tracks.server import auth
from tracks.server.app import create_app
from tracks.supervisor import intake as intake_worker
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
# Boolean flags map to their ``opts`` key; lifecycle flags are POSIX-only
# except ``--status`` (interfaces §1e.5).
_BOOL_FLAGS: dict[str, str] = {
    "--password-stdin": "password_stdin",
    "--background": "background",
    "--stop": "stop",
    "--status": "status",
}
_PASSWORD_HINT = (
    "trac serve: first start needs a password before logins are accepted.\n"
    "supply it with --password-stdin, the TRAC_SERVE_PASSWORD environment\n"
    "variable, or an interactive terminal prompt, then start trac serve again."
)
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|KEY|CREDENTIAL", re.IGNORECASE)

# v0.11 (interfaces §1j.3): the human-gate command kinds the supervisor
# claims for worker execution. The synchronous short writes (§1b execution
# forms) never appear here — they execute at acceptance.
_GATE_COMMAND_KINDS = (
    "record_triage",
    "record_review",
    "record_stage_approval",
    "record_release_decision",
)
# v0.11 (interfaces §1f.1): a row stranded in ``evaluating`` older than
# this is crash wreckage — it returns to ``submitted`` for a re-dispatch
# (budget <= 3 rides the intake worker's own reclaim accounting).
_INTAKE_STALE_S = 600.0

# Human-action wake-up: while a worker is in flight the loop polls fast so a
# finished gate is driven without waiting a full interval; idle keeps the
# NFR-0152 interval (never a hot loop).
_BUSY_POLL_S = 0.25


class _Wake:
    """Counting wake-up channel: a notify between waits is never lost."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._count = 0
        self._seen = 0

    @property
    def count(self) -> int:
        with self._cond:
            return self._count

    def notify(self) -> None:
        with self._cond:
            self._count += 1
            self._cond.notify_all()

    def wait(
        self, timeout: float, stop_event: threading.Event, seen: int | None = None
    ) -> int:
        with self._cond:
            if seen is None:
                seen = self._seen
            self._cond.wait_for(
                lambda: self._count != seen or stop_event.is_set(), timeout
            )
            self._seen = self._count
            return self._count


def _human_accept_wake(wake: _Wake):
    """The serve accept hook: wake the loop on human accepts only.

    System drive/swap accepts must not self-wake (no self-excitation).
    """

    def _hook(kind: str, actor_class: str) -> None:
        del kind
        if actor_class == "human":
            wake.notify()

    return _hook


class _UsageError(ValueError):
    """A §2a grammar violation (unknown flag, missing value, missing --repo)."""


# -- flag parsing (interfaces §2a grammar) ------------------------------------


def _parse_serve_args(args: list[str]) -> dict:
    opts: dict[str, Any] = {"repo": [], "home": None}
    for name, default in _FLAG_SPECS.values():
        opts[name] = default
    for option in _BOOL_FLAGS.values():
        opts[option] = False
    index = 0
    while index < len(args):
        flag = args[index]
        boolean_option = _BOOL_FLAGS.get(flag)
        if boolean_option is not None:
            opts[boolean_option] = True
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
        wake: _Wake | None = None,
    ):
        import uvicorn

        self._db = db
        self._opts = opts
        self._home = home
        self._stop = stop
        self._thread = thread
        self._wake = wake
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
        if self._wake is not None:
            self._wake.notify()
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
    wake: _Wake | None = None,
) -> None:
    """Background drive task of the serve process (architecture §1.1).

    Re-drives accepted ``drive_run`` commands (the recovery path requeued
    them) and accepts a fresh drive for the runnable run, one attempt per
    poll interval per run — never a hot loop (NFR-0152).

    ``wake`` wakes the loop early on human accepts (and on stop, paired
    with every ``stop.set``); a reaped worker re-ticks immediately to
    drive the just-freed run, and a busy loop polls at ``_BUSY_POLL_S``.
    """
    interval = max(0.5, float(getattr(config, "poll_interval_s", 5) or 5))
    ttl = int(getattr(config, "lease_ttl_s", 30) or 30)
    worker_id = f"supervisor-{os.getpid()}"
    state = _DriveState()
    seen = wake.count if wake is not None else 0
    while not stop.is_set():
        try:
            reaped = _supervisor_tick(db, service, scheduler, worker, worker_id, ttl, interval,
                state)
        except Exception as exc:  # resilience: one bad tick must not kill the service
            _log("error", f"supervisor tick failed: {exc}")
            reaped = False
        if reaped:
            continue  # drive the just-freed run at once; poll() fires once so no hot loop
        wait_s = (
            min(interval, _BUSY_POLL_S)
            if state.in_flight or state.gate_in_flight or state.intake_in_flight
            else interval
        )
        if wake is None:
            stop.wait(wait_s)
        else:
            seen = wake.wait(wait_s, stop, seen)


@dataclass
class _DriveState:
    """In-memory bookkeeping of the serve-process drive loop."""

    in_flight: set[str] = field(default_factory=set)
    cooldown: dict[str, float] = field(default_factory=dict)
    # v0.11 claim bookkeeping: gate commands by command_id, intake
    # evaluations by intake_id — separate namespaces so a gate claim never
    # throttles the same run's ordinary drive (and vice versa).
    gate_in_flight: set[str] = field(default_factory=set)
    gate_cooldown: dict[str, float] = field(default_factory=dict)
    intake_in_flight: set[str] = field(default_factory=set)
    intake_cooldown: dict[str, float] = field(default_factory=dict)


def _supervisor_tick(
    db: ServiceDB, service: CommandService, scheduler: Scheduler,
    worker: WorkerManager, worker_id: str, ttl: int, interval: float,
    state: _DriveState,
) -> bool:
    finished_list = worker.poll()
    reaped = bool(finished_list)
    for finished in finished_list:
        state.in_flight.discard(str(finished.get("run_id") or ""))
        state.gate_in_flight.discard(str(finished.get("command_id") or ""))
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
    _claim_accepted_gates(db, worker, worker_id, ttl, interval, state, now)
    _claim_submitted_intakes(db, interval, state, now)
    return reaped


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


def _run_awaiting(db: ServiceDB, run_id: str | None) -> str | None:
    """Run-plane ``runs.awaiting`` for run_id, read-only (fail-open → None)."""
    if not run_id:
        return None
    try:
        project_id = db.find_run_project_id(str(run_id))
        project = db.get_project(project_id)
        repo_path = (project or {}).get("repo_path") or ""
        if not repo_path:
            return None
        runtime_db = paths.db_path(paths.tracks_home(Path(repo_path)))
        if not runtime_db.exists():
            return None
        conn = sqlite3.connect(f"file:{runtime_db}?mode=ro", uri=True)
    except (sqlite3.Error, OSError, ValueError):
        return None
    try:
        try:
            row = conn.execute(
                "SELECT awaiting FROM runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None or row[0] is None or str(row[0]) == "":
            return None
        return str(row[0])
    finally:
        conn.close()


def _accept_fresh_drive(
    db: ServiceDB, service: CommandService, worker: WorkerManager,
    worker_id: str, ttl: int, runnable: str, state: _DriveState, now: float,
) -> None:
    """Accept a new drive_run for the runnable run and hand it to a worker."""
    # awaiting 期间 drive 是 no-op (drive_once 直达 await_human 边界退出):
    # 跳过 fresh accept 以止住每 tick 的空转写/spawn;人工命令走
    # _claim_accepted_gates 通道,gate 完成后 awaiting 清除、下个 tick 自动恢复.
    if _run_awaiting(db, runnable):
        return
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


# -- v0.11 claim steps: human gates and intake evaluation (§1j.3/§1f.1) -------


def _claim_accepted_gates(
    db: ServiceDB, worker: WorkerManager, worker_id: str, ttl: int,
    interval: float, state: _DriveState, now: float,
) -> None:
    """Claim accepted human-gate commands under the run lease (§1j.3).

    A missed claim (another generation already owns the command) only
    delays: the command stays claimable and the crash-recovery requeue
    path returns it to accepted for a later tick — nothing is lost.
    """
    for row in _accepted_gate_commands(db):
        command_id = str(row.get("command_id") or "")
        run_id = str(row.get("run_id") or "")
        throttled = (
            not command_id
            or not run_id
            or command_id in state.gate_in_flight
            or now - state.gate_cooldown.get(command_id, 0.0) < interval
        )
        if throttled:
            continue
        if worker.spawn(row, acquire_lease(db, run_id, worker_id, ttl)):
            state.gate_in_flight.add(command_id)
        else:
            state.gate_cooldown[command_id] = now


def _accepted_gate_commands(db: ServiceDB) -> list[dict]:
    """Accepted rows of the closed human-gate kind set (§1j.3)."""
    marks = ", ".join("?" for _ in _GATE_COMMAND_KINDS)
    with contextlib.closing(_connect_store(db.home, readonly=True)) as conn:
        rows = conn.execute(
            f"SELECT command_id, kind, run_id, params_json FROM commands"
            f" WHERE status = 'accepted' AND kind IN ({marks})",
            _GATE_COMMAND_KINDS,
        ).fetchall()
    return [dict(row) for row in rows]


def _claim_submitted_intakes(
    db: ServiceDB, interval: float, state: _DriveState, now: float,
) -> None:
    """Take submitted intakes into the SM-01 evaluation window (§1f.1).

    Crash recovery runs first — a stranded ``evaluating`` row past the
    timeout returns to ``submitted`` for a re-dispatch — then each still
    submitted row is handed to the evaluation worker off the tick path:
    the supervisor never blocks on a Scribe session.
    """
    home = Path(db.home)
    _reclaim_stale_intakes(home)
    for intake_id in _submitted_intake_ids(db):
        if (
            intake_id in state.intake_in_flight
            or now - state.intake_cooldown.get(intake_id, 0.0) < interval
        ):
            continue
        state.intake_in_flight.add(intake_id)
        state.intake_cooldown[intake_id] = now
        threading.Thread(
            target=_evaluate_intake,
            args=(home, intake_id, state),
            name=f"trac-intake-{intake_id}",
            daemon=True,
        ).start()


def _submitted_intake_ids(db: ServiceDB) -> list[str]:
    with contextlib.closing(_connect_store(db.home, readonly=True)) as conn:
        rows = conn.execute(
            "SELECT intake_id FROM intakes WHERE status = 'submitted'"
        ).fetchall()
    return [str(row["intake_id"]) for row in rows]


def _evaluate_intake(home: Path, intake_id: str, state: _DriveState) -> None:
    """One intake evaluation attempt in its background thread."""
    try:
        intake_worker.run_intake_evaluation(Path(home), intake_id)
    except Exception as exc:  # one bad evaluation must not kill the service
        _log("error", f"intake evaluation {intake_id} failed: {exc}")
    finally:
        state.intake_in_flight.discard(intake_id)


def _reclaim_stale_intakes(home: Path) -> list[str]:
    """Recover crash-stranded evaluations (evaluating -> submitted)."""
    try:
        return intake_worker.reclaim_stale_evaluating(
            Path(home), older_than_s=_INTAKE_STALE_S
        )
    except Exception as exc:  # recovery is best-effort; the tick goes on
        _log("error", f"intake reclaim failed: {exc}")
        return []


def _log(level: str, msg: str) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "msg": msg}
    print(json.dumps(row), file=sys.stderr, flush=True)


# -- the entry face -----------------------------------------------------------


@dataclass(frozen=True)
class _ServiceProject:
    """The installed project a lifecycle call operates on (interfaces §1e)."""

    root: Path
    receipt: dict


# The CLI-entry scope: the environment variable is what delegated/background
# children observe, while this in-process depth lets a direct ``cmd_serve``
# call (tests/management backdoor) stay distinguishable from the user face
# even when the ambient environment already carries TRAC_CLI_ENTRY=1
# (interfaces §1d.4; PRISM-RED-T002-04).
_CLI_ENTRY_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "trac_cli_entry_depth", default=0
)


@contextlib.contextmanager
def cli_entry_scope():
    """Scope ``TRAC_CLI_ENTRY=1`` to one ``trac.cli.main.main`` call."""
    previous = os.environ.get("TRAC_CLI_ENTRY")
    token = _CLI_ENTRY_DEPTH.set(_CLI_ENTRY_DEPTH.get() + 1)
    os.environ["TRAC_CLI_ENTRY"] = "1"
    try:
        yield
    finally:
        _CLI_ENTRY_DEPTH.reset(token)
        if previous is None:
            os.environ.pop("TRAC_CLI_ENTRY", None)
        else:
            os.environ["TRAC_CLI_ENTRY"] = previous


def _user_face() -> bool:
    """True for the global ``trac`` entry, false inside the project launcher."""
    return (
        _CLI_ENTRY_DEPTH.get() > 0
        and os.environ.get("TRAC_PROJECT_ACTIVE") != "1"
    )


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _find_service_project(repo: Path) -> tuple[_ServiceProject | None, str | None]:
    """Resolve ``--repo`` into an installed project, fail-closed (§1d.4)."""
    try:
        found = project_runtime.find_project(repo)
        if found is None:
            return (
                None,
                f"not_installed: {repo} is not an installed tracks project; "
                f"run `trac install {repo}` first",
            )
        receipt = project_runtime.read_receipt(found)
    except project_runtime.ProjectCorrupt as exc:
        return None, f"install_corrupt: {exc}"
    return _ServiceProject(root=found, receipt=receipt), None


def cmd_serve(repo: Path, *args: str) -> int:
    """Parse serve flags and dispatch the lifecycle face (interfaces §2a)."""
    del repo  # the command context; the serve scope comes from --repo flags
    raw_args = list(args)
    try:
        opts = _parse_serve_args(raw_args)
    except _UsageError as exc:
        print(f"trac serve: {exc}", file=sys.stderr)
        return 1
    project, error = _dispatch_project(opts)
    if error is not None:
        print(f"trac serve: {error}", file=sys.stderr)
        return 1
    if _is_windows() and (opts["background"] or opts["stop"]):
        print(
            "trac serve: unsupported_platform: --background/--stop need POSIX",
            file=sys.stderr,
        )
        return 1
    if opts["stop"]:
        return _cmd_stop(project, opts)
    if opts["status"]:
        return _cmd_status(project, opts)
    if opts["background"]:
        return _cmd_background(project, opts)
    if _user_face():
        return _delegate_foreground(project, raw_args)
    return _run_foreground(opts)


def _dispatch_project(opts: dict) -> tuple[_ServiceProject | None, str | None]:
    """The project a user-face or lifecycle call must be bound to."""
    if _user_face() or opts["stop"] or opts["status"] or opts["background"]:
        return _find_service_project(opts["repo"][0])
    return None, None


def _run_foreground(opts: dict) -> int:
    """Legacy in-process foreground serve (direct ``cmd_serve`` face)."""
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


def _delegate_foreground(project: _ServiceProject, raw_args: list[str]) -> int:
    """Run the project launcher's foreground serve with TRAC_PROJECT_ACTIVE=1."""
    environment = dict(os.environ)
    environment["TRAC_PROJECT_ACTIVE"] = "1"
    command = [
        project.receipt["python"],
        str(project.root / project.receipt["launcher"]),
        "serve",
        *raw_args,
    ]
    try:
        completed = subprocess.run(command, cwd=str(project.root), env=environment)
    except OSError as exc:
        print(
            f"trac serve: install_corrupt: cannot run project launcher: {exc}",
            file=sys.stderr,
        )
        return 1
    return completed.returncode


def _cmd_stop(project: _ServiceProject, opts: dict) -> int:
    result = project_runtime.stop_service(
        _resolve_home(opts), project.receipt, project.root
    )
    state = result["state"]
    if state == "stopped":
        print(f"stopped pid={result['pid']}")
        return 0
    if state == "stale_removed":
        print("not running (stale pidfile removed)")
        return 0
    if state == "not_running":
        print("not running")
        return 0
    print(
        f"stop_refused: pid {result['pid']} does not belong to {project.root}",
        file=sys.stderr,
    )
    return 1


def _cmd_status(project: _ServiceProject, opts: dict) -> int:
    result = project_runtime.pidfile_status(
        _resolve_home(opts), project.receipt, project.root
    )
    state = result["state"]
    if state == "stopped":
        print("stopped")
        return 0
    if state == "stale":
        print("stopped (stale pidfile)")
        return 0
    if state == "running":
        print(
            f"running pid={result['pid']} version={result['version']} "
            f"url={result['url']}"
        )
        return 0
    print(f"running (unhealthy: {result.get('detail') or 'unhealthy'})")
    return 1


def _cmd_background(project: _ServiceProject, opts: dict) -> int:
    home = _resolve_home(opts)
    running = _already_running(home, project)
    if running is not None:
        print(running)
        return 0
    log_path = home / project_runtime.SERVICE_LOG_NAME
    home.mkdir(parents=True, exist_ok=True)
    process = _spawn_background(project, opts, log_path)
    port, error = _wait_for_background(
        process, opts["host"], opts["port"], log_path, opts["wait_initial_s"]
    )
    if error is not None:
        _terminate_background(process)
        _report_background_failure(error, log_path)
        return 1
    payload = {
        "pid": process.pid,
        "host": opts["host"],
        "port": port,
        "version": project.receipt["version"],
        "repo": str(project.root.resolve()),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    project_runtime.write_pidfile(home, payload)
    print(
        f"serving on http://{opts['host']}:{port} "
        f"(pid {process.pid}, version {payload['version']}, background)",
        flush=True,
    )
    return 0


def _already_running(home: Path, project: _ServiceProject) -> str | None:
    """The idempotent face when the pidfile names a healthy owned service."""
    payload = project_runtime.read_pidfile(home)
    if payload is None:
        return None
    pid = payload.get("pid")
    owned = project_runtime.launcher_owns_pid(project.root, project.receipt, pid)
    if not project_runtime.pid_alive(pid) or not owned:
        project_runtime.remove_pidfile(home)
        return None
    host = str(payload.get("host") or "127.0.0.1")
    try:
        port = int(payload.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    ok, _ = project_runtime.healthz(host, port)
    if not ok:
        project_runtime.remove_pidfile(home)
        return None
    return f"already running pid={int(pid)} url=http://{host}:{port}"


def _spawn_background(
    project: _ServiceProject, opts: dict, log_path: Path
) -> subprocess.Popen:
    """Spawn the project launcher's foreground serve detached (§1e.2)."""
    environment = dict(os.environ)
    environment["TRAC_PROJECT_ACTIVE"] = "1"
    if opts["password_stdin"]:
        password = sys.stdin.readline().strip()
        if password:
            environment["TRAC_SERVE_PASSWORD"] = password
    command = [
        project.receipt["python"],
        str(project.root / project.receipt["launcher"]),
        *_background_child_args(opts),
    ]
    with log_path.open("ab", buffering=0) as log_handle:
        return subprocess.Popen(
            command,
            cwd=str(project.root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )


def _background_child_args(opts: dict) -> list[str]:
    args = ["serve"]
    for repo_path in opts["repo"]:
        args += ["--repo", str(repo_path)]
    args += ["--host", str(opts["host"]), "--port", str(opts["port"])]
    for flag, key in (
        ("--wait-initial-s", "wait_initial_s"),
        ("--wait-cap-s", "wait_cap_s"),
        ("--poll-interval-s", "poll_interval_s"),
        ("--poll-idle-cap-s", "poll_idle_cap_s"),
    ):
        args += [flag, str(opts[key])]
    if opts.get("home"):
        args += ["--home", str(opts["home"])]
    return args


_SERVING_LINE = re.compile(r"serving on http://[^\s:]+:(\d+)")


def _wait_for_background(
    process: subprocess.Popen,
    host: str,
    requested_port: int,
    log_path: Path,
    timeout: float,
) -> tuple[int, str | None]:
    """Bounded wait for *this child's* ``/healthz`` or child exit (§1e.2).

    A healthz 200 only proves that *some* listener answers on the port; it
    cannot distinguish our spawned child from a foreign listener occupying
    the requested port. Readiness therefore also requires the child to hold
    the LISTEN socket itself — otherwise a bind-failed child would be
    announced as serving and the pidfile would name a dead process.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        code = process.poll()
        if code is not None:
            return 0, f"child exited with code {code}"
        port = requested_port or _announced_port(log_path)
        if port:
            healthy, _ = project_runtime.healthz(host, port)
            if healthy and _child_owns_port(process.pid, int(port)):
                return int(port), None
        if time.monotonic() >= deadline:
            return 0, f"health check timed out after {timeout}s"
        time.sleep(0.05)


def _child_owns_port(pid: int, port: int) -> bool:
    """True when *pid* itself holds the LISTEN socket on *port*.

    Linux resolves the socket inode through ``/proc`` (no external tool);
    other POSIX hosts use ``lsof``, the standard process inspector (macOS
    ships it). Background serving is POSIX-only (§1e.5).
    """
    if sys.platform.startswith("linux"):
        inodes = _linux_listen_inodes(port)
        if inodes is not None:
            return _pid_holds_socket(pid, inodes)
    return _lsof_owns_port(pid, port)


def _linux_listen_inodes(port: int) -> set[str] | None:
    """Listening socket inodes for *port* from /proc; None when unavailable."""
    inodes: set[str] = set()
    tables = 0
    for name in ("tcp", "tcp6"):
        try:
            lines = Path(f"/proc/net/{name}").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        tables += 1
        inodes.update(_listen_inodes(lines, port))
    return inodes if tables else None


def _listen_inodes(lines: list[str], port: int) -> set[str]:
    """Inodes of ``state 0A`` (LISTEN) rows on *port* in a /proc/net table."""
    inodes: set[str] = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != "0A":
            continue
        try:
            local_port = int(fields[1].rsplit(":", 1)[1], 16)
        except (IndexError, ValueError):
            continue
        if local_port == port:
            inodes.add(fields[9])
    return inodes


def _pid_holds_socket(pid: int, inodes: set[str]) -> bool:
    """True when *pid*'s file descriptors reference one of *inodes*."""
    if not inodes:
        return False
    targets = {f"socket:[{inode}]" for inode in inodes}
    try:
        handles = list(Path(f"/proc/{pid}/fd").iterdir())
    except OSError:
        return False
    for handle in handles:
        try:
            if os.readlink(handle) in targets:
                return True
        except OSError:
            continue
    return False


def _lsof_owns_port(pid: int, port: int) -> bool:
    """True when ``lsof`` lists *pid* among the LISTEN owners of *port*."""
    try:
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and str(pid) in completed.stdout.split()


def _announced_port(log_path: Path) -> int:
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    matches = _SERVING_LINE.findall(text)
    return int(matches[-1]) if matches else 0


def _log_tail(log_path: Path, limit: int = 2000) -> str:
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def _report_background_failure(error: str, log_path: Path) -> None:
    tail = _log_tail(log_path)
    detail = f"{error}; log tail: {tail}" if tail else error
    print(f"background_failed: {detail}", file=sys.stderr)


def _terminate_background(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _serve(opts: dict, home: Path, db: ServiceDB) -> int:
    _log("info", f"startup recovery: {json.dumps(recover_on_startup(db))}")
    config = _build_config(opts, home)
    wake = _Wake()
    service = CommandService(home, db, config, on_accept=_human_accept_wake(wake))
    scheduler = Scheduler(db)
    worker = WorkerManager(db, scheduler, config)
    supervisor = SimpleNamespace(db=db, scheduler=scheduler, worker=worker)
    app = create_app(home, service, supervisor, config)
    stop, thread = _start_supervisor(db, service, scheduler, worker, config, wake)
    _start_cache_warmup(home)
    server = _AnnouncingServer(app, opts, home, db, stop, thread, wake)
    if not server.run():
        stop.set()
        wake.notify()
        thread.join(timeout=2)
        print(
            f"trac serve: could not serve http://{opts['host']}:{opts['port']}"
            " (port occupied or bind failure)",
            file=sys.stderr,
        )
        return 1
    return 0


def _start_cache_warmup(home: Path) -> threading.Thread:
    """Fold every registered run's events once at startup (#225).

    The projection caches are watermark-invalidated, so one cold replay in a
    daemon thread moves the multi-second first-request cost off the request
    path entirely; failures are logged and never block serving.
    """
    def _warm() -> None:
        try:
            from tracks.server import projections

            started = time.monotonic()
            projections.project_overview(home)
            _log(
                "info",
                f"projection cache warmed in {time.monotonic() - started:.1f}s",
            )
        except Exception as exc:  # warmup is best-effort; serving must not fail
            _log("error", f"projection cache warmup failed: {exc}")

    thread = threading.Thread(target=_warm, name="trac-cache-warmup", daemon=True)
    thread.start()
    return thread


def _start_supervisor(
    db: ServiceDB, service: CommandService, scheduler: Scheduler,
    worker: WorkerManager, config: Any, wake: _Wake | None = None,
) -> tuple[threading.Event, threading.Thread]:
    """Run the drive loop as a background task of the serve process (§1.1)."""
    stop = threading.Event()
    thread = threading.Thread(
        target=_supervisor_loop,
        args=(db, service, scheduler, worker, config, stop, wake),
        name="trac-supervisor",
        daemon=True,
    )
    thread.start()
    return stop, thread
