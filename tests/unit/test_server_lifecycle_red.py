"""T-002 RED: project server lifecycle (IF-SERVERLIFE-001 /
IF-GLOBALCLI-001 / IF-PROJECTCLI-001 / IF-VERSIONVIS-001).

Devon-owned unit RED for the serve slice (interfaces §1d.4/§1e):

- the user face (``main`` -> ``cmd_serve`` with ``TRAC_CLI_ENTRY=1``) refuses a
  ``--repo`` without a receipt with ``not_installed`` and never composes the
  service (no uvicorn, no store); a corrupt project is ``install_corrupt``;
- ``--background`` spawns the project launcher's foreground serve detached
  (``start_new_session``), appends stdout/stderr to ``serve.log``, writes the
  JSON pidfile after a bounded healthz wait and announces the project version;
- ``--stop`` validates PID ownership against the project launcher, SIGTERM
  then cleanup, is idempotent, clears stale pidfiles and refuses foreign PIDs;
- ``--status`` reports the receipt version over a live ``/healthz``, degrades
  to ``running (unhealthy: ...)`` with exit 1, and cleans stale pidfiles;
- Windows refuses ``--background``/``--stop`` with ``unsupported_platform``
  while the foreground face keeps working and never writes a pidfile.

The contract surfaces under test are the real CLI faces; the only process
boundary substituted is the blocking foreground composition (``_serve``),
which is replaced by a probe in the two install-gate nodes so a RED tree
cannot start uvicorn. Installed projects and their launchers are external
inputs (interfaces §1b), built here as disposable files.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tracks.cli import serve_cmd
from tracks.cli.main import main

from .install_red_support import seed_installed_project

_HOST = "127.0.0.1"
_VERSION = "9.9.9"

# A project launcher stand-in: answers ``/healthz`` on the requested port and
# records the argv it was started with. The real launcher is an external input
# (interfaces §1b.3), so this is a fixture, not a mock of the system.
_HEALTH_LAUNCHER = '''\
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = os.environ.get("TRAC_FAKE_HEALTH_VERSION", "9.9.9")


def _flag(name, default):
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except ValueError:
        return default


host = _flag("--host", "127.0.0.1")
port = int(_flag("--port", "0"))
marker = os.environ.get("TRAC_FAKE_LAUNCHER_MARKER")
if marker:
    Path(marker).write_text(json.dumps({"argv": sys.argv[1:]}), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {"status": "ok", "projects": [], "version": VERSION, "uptime_s": 0}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


print("fake launcher ready", flush=True)
ThreadingHTTPServer((host, port), Handler).serve_forever()
'''

# A launcher stand-in that double-forks so the observed daemon is not the
# test process's child (stop waits on real process death, not a zombie).
_STOPPABLE_LAUNCHER = '''\
import os
import time
from pathlib import Path

marker = os.environ.get("TRAC_FAKE_DAEMON_PID")
if marker and os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        Path(marker).write_text(str(os.getpid()), encoding="utf-8")
        while True:
            time.sleep(1)
    os._exit(0)
os._exit(0)
'''


@pytest.fixture(autouse=True)
def _hermetic_git_identity(monkeypatch):
    """Deterministic git identity for any installer-driven project seeding."""
    for key, value in {
        "GIT_AUTHOR_NAME": "Tracks Test",
        "GIT_AUTHOR_EMAIL": "tracks-test@example.com",
        "GIT_COMMITTER_NAME": "Tracks Test",
        "GIT_COMMITTER_EMAIL": "tracks-test@example.com",
    }.items():
        monkeypatch.setenv(key, value)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])


def _seed_project(tmp_path: Path, launcher: str) -> Path:
    root = seed_installed_project(tmp_path / "project", _VERSION)
    (root / ".tracks" / "bin" / "trac.py").write_text(launcher, encoding="utf-8")
    return root


def _write_pidfile(
    root: Path, pid: int, port: int, *, version: str = _VERSION
) -> Path:
    service = root / ".tracks" / "service"
    service.mkdir(parents=True, exist_ok=True)
    pidfile = service / "serve.pid"
    pidfile.write_text(
        json.dumps(
            {
                "pid": pid,
                "host": _HOST,
                "port": port,
                "version": version,
                "repo": str(root.resolve()),
                "started_at": "2026-10-19T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return pidfile


def _spawn_launcher(
    root: Path, port: int, *, version: str = _VERSION
) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(root / ".tracks" / "bin" / "trac.py"),
            "serve",
            "--repo",
            str(root),
            "--host",
            _HOST,
            "--port",
            str(port),
        ],
        env={**os.environ, "TRAC_FAKE_HEALTH_VERSION": version},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_health(port: int, timeout: float = 10.0) -> dict:
    url = f"http://{_HOST}:{port}/healthz"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError(f"fake health server did not answer on {url}")


def _wait_file(path: Path, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
        time.sleep(0.05)
    raise AssertionError(f"file never appeared: {path}")


def _announced_pid(line: str, port: int) -> int:
    prefix = f"serving on http://{_HOST}:{port} (pid "
    suffix = f", version {_VERSION}, background)"
    assert line.startswith(prefix), line
    assert line.endswith(suffix), line
    return int(line[len(prefix):-len(suffix)])


def _assert_pidfile(root: Path, pid: int, port: int) -> dict:
    payload = json.loads(
        (root / ".tracks" / "service" / "serve.pid").read_text(encoding="utf-8")
    )
    assert payload["pid"] == pid
    assert payload["host"] == _HOST
    assert payload["port"] == port
    assert payload["version"] == _VERSION
    assert Path(payload["repo"]).resolve() == root.resolve()
    assert isinstance(payload["started_at"], str)
    assert payload["started_at"]
    return payload


def _wait_pid_gone(pid: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise AssertionError(f"pid {pid} still alive after stop")


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# AC-FR0350-02@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 user face refuses uninstalled repo
# AC-FR0349-02@v0.12 TRACKS-TRACE IF-GLOBALCLI-001 no global server fallback
def test_ac_0350_02_serve_outside_project_not_installed(
    tmp_path, capsys, monkeypatch
):
    target = tmp_path / "plain"
    target.mkdir()
    started: list[tuple] = []
    monkeypatch.setattr(
        serve_cmd, "_serve", lambda *args, **kwargs: started.append(args) or 0
    )
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    monkeypatch.delenv("TRAC_CLI_ENTRY", raising=False)

    # PRISM-RED-T002-04 (interfaces §1d.4): the CLI-entry marker is scoped to
    # this main() call — a caller-provided value is saved and restored, so the
    # next direct cmd_serve call is distinguishable from the user face.
    monkeypatch.setenv("TRAC_CLI_ENTRY", "caller-marker")
    assert main(["--help"]) == 0
    capsys.readouterr()
    assert os.environ.get("TRAC_CLI_ENTRY") == "caller-marker"
    monkeypatch.delenv("TRAC_CLI_ENTRY")

    rc = main(["serve", "--repo", str(target)])
    out, err = capsys.readouterr()
    assert started == []
    assert os.environ.get("TRAC_CLI_ENTRY") is None
    assert rc == 1
    assert out == ""
    assert "not_installed" in err
    assert "trac install" in err
    assert not (target / ".tracks" / "service" / "service.db").exists()

    # The direct ``cmd_serve`` import face (tests/management backdoor) keeps
    # the v0.9 semantics: with the marker scoped away by main(), it bypasses
    # the user-face install gate without any manual environment clearing; the
    # password channel then reaches the foreground composition.
    monkeypatch.setenv("TRAC_SERVE_PASSWORD", "unit-test-password")
    rc = serve_cmd.cmd_serve(Path.cwd(), "--repo", str(target), "--port", "0")
    capsys.readouterr()
    assert rc == 0
    assert len(started) == 1


# AC-FR0350-02@v0.12 TRACKS-TRACE IF-PROJECTCLI-001 corrupt project fail-closed
def test_ac_0350_02_corrupt_project_serve_fails_closed(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "broken"
    package = root / ".tracks" / "env" / _VERSION / "site-packages" / "tracks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        f'__version__ = "{_VERSION}"\n', encoding="utf-8"
    )
    started: list[tuple] = []
    monkeypatch.setattr(
        serve_cmd, "_serve", lambda *args, **kwargs: started.append(args) or 0
    )
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)

    rc = main(["serve", "--repo", str(root)])
    out, err = capsys.readouterr()
    assert started == []
    assert rc == 1
    assert out == ""
    assert "install_corrupt" in err


# AC-FR0349-02@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 stop/status scope is a project
def test_ac_0349_02_stop_and_status_outside_project_refused(
    tmp_path, capsys, monkeypatch
):
    target = tmp_path / "plain"
    target.mkdir()
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)

    for flag in ("--stop", "--status"):
        rc = main(["serve", "--repo", str(target), flag])
        out, err = capsys.readouterr()
        assert rc == 1, flag
        assert out == ""
        assert "not_installed" in err
        assert "trac install" in err

    assert not (target / ".tracks" / "service" / "serve.pid").exists()


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 background spawns detached serve
# AC-FR0350-01@v0.12 TRACKS-TRACE IF-VERSIONVIS-001 announces locked version
# AC-NFR0159-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 new session + pidfile + log
def test_ac_0350_01_background_spawns_detached_launcher(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _HEALTH_LAUNCHER)
    marker = tmp_path / "launcher.json"
    monkeypatch.setenv("TRAC_FAKE_LAUNCHER_MARKER", str(marker))
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    service = root / ".tracks" / "service"
    service.mkdir(parents=True)
    (service / "serve.log").write_text("previous log line\n", encoding="utf-8")

    pid = None
    try:
        rc = serve_cmd.cmd_serve(
            root,
            "--repo",
            str(root),
            "--host",
            _HOST,
            "--port",
            str(port),
            "--background",
            "--wait-initial-s",
            "20",
        )
        out, err = capsys.readouterr()
        assert rc == 0, err
        assert err == ""
        pid = _announced_pid(out.strip(), port)
        _assert_pidfile(root, pid, port)

        assert os.getsid(pid) != os.getsid(0)
        assert _wait_health(port)["version"] == _VERSION

        argv = json.loads(marker.read_text(encoding="utf-8"))["argv"]
        assert "serve" in argv
        assert str(root) in argv
        log = (service / "serve.log").read_text(encoding="utf-8")
        assert log.startswith("previous log line\n")
        assert "fake launcher ready" in log
    finally:
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 bounded background failure
def test_ac_0350_01_background_child_exit_reports_failure(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    launcher = (
        "import sys\n"
        "print('boom: fake serve cannot start', file=sys.stderr, flush=True)\n"
        "raise SystemExit(3)\n"
    )
    root = _seed_project(tmp_path, launcher)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)

    rc = serve_cmd.cmd_serve(
        root,
        "--repo",
        str(root),
        "--host",
        _HOST,
        "--port",
        str(port),
        "--background",
        "--wait-initial-s",
        "20",
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert err.startswith("background_failed:"), err
    assert "boom: fake serve cannot start" in err
    assert not (root / ".tracks" / "service" / "serve.pid").exists()


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 already running is idempotent
def test_ac_0350_01_background_already_running(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _HEALTH_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    proc = _spawn_launcher(root, port)
    try:
        _wait_health(port)
        pidfile = _write_pidfile(root, proc.pid, port)
        rc = serve_cmd.cmd_serve(
            root,
            "--repo",
            str(root),
            "--host",
            _HOST,
            "--port",
            str(port),
            "--background",
            "--wait-initial-s",
            "20",
        )
        out, err = capsys.readouterr()
        assert rc == 0, err
        assert out.strip() == (
            f"already running pid={proc.pid} url=http://{_HOST}:{port}"
        )
        assert pidfile.exists()
    finally:
        _terminate(proc)


# AC-NFR0159-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 stop terminates owned launcher
# AC-FR0350-01@v0.12 TRACKS-TRACE IF-PROJECTCLI-001 stop targets the project pid
def test_ac_nfr0159_01_stop_terminates_owned_launcher(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _STOPPABLE_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    marker = tmp_path / "daemon.pid"
    starter = subprocess.Popen(
        [
            sys.executable,
            str(root / ".tracks" / "bin" / "trac.py"),
            "serve",
            "--repo",
            str(root),
            "--host",
            _HOST,
            "--port",
            str(port),
        ],
        env={**os.environ, "TRAC_FAKE_DAEMON_PID": str(marker)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    starter.wait(timeout=10)
    daemon_pid = int(_wait_file(marker))
    pidfile = _write_pidfile(root, daemon_pid, port)
    try:
        rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--stop")
        out, err = capsys.readouterr()
        assert rc == 0, err
        assert out.strip() == f"stopped pid={daemon_pid}"
        assert not pidfile.exists()
        _wait_pid_gone(daemon_pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(daemon_pid, signal.SIGKILL)


# AC-NFR0159-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 foreign pid is not stopped
def test_ac_nfr0159_01_stop_refuses_foreign_pid(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _STOPPABLE_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    foreign = subprocess.Popen(["sleep", "120"])
    try:
        pidfile = _write_pidfile(root, foreign.pid, port)
        rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--stop")
        out, err = capsys.readouterr()
        assert rc == 1
        assert out == ""
        assert err.startswith(
            f"stop_refused: pid {foreign.pid} does not belong to "
        )
        assert str(root.resolve()) in err
        assert foreign.poll() is None
        assert pidfile.exists()
    finally:
        _terminate(foreign)


# AC-NFR0159-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 stop is idempotent
def test_ac_nfr0159_01_stop_not_running_and_stale_pidfile(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _STOPPABLE_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)

    rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--stop")
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert out.strip() == "not running"

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=10)
    pidfile = _write_pidfile(root, dead.pid, port)
    rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--stop")
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert out.strip() == "not running (stale pidfile removed)"
    assert not pidfile.exists()


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-VERSIONVIS-001 status reports receipt version
# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 status validates healthz
def test_ac_0350_01_status_reports_running_receipt_version(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _HEALTH_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    proc = _spawn_launcher(root, port, version="0.0.1")
    try:
        _wait_health(port)
        _write_pidfile(root, proc.pid, port)
        rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--status")
        out, err = capsys.readouterr()
        assert rc == 0, err
        assert out.strip() == (
            f"running pid={proc.pid} version={_VERSION} url=http://{_HOST}:{port}"
        )
    finally:
        _terminate(proc)


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 unhealthy/stopped/stale faces
def test_ac_0350_01_status_unhealthy_stopped_and_stale(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _STOPPABLE_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)

    rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--status")
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert out.strip() == "stopped"

    sleeping = subprocess.Popen(["sleep", "120"])
    try:
        _write_pidfile(root, sleeping.pid, port)
        rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--status")
        out, err = capsys.readouterr()
        assert rc == 1
        # The contract fixes the shape and exit code only; ``<detail>`` is
        # implementation-chosen (e.g. a connection error).
        assert out.strip().startswith("running (unhealthy:")
    finally:
        _terminate(sleeping)

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=10)
    pidfile = _write_pidfile(root, dead.pid, port)
    rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--status")
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert out.strip() == "stopped (stale pidfile)"
    assert not pidfile.exists()


# AC-NFR0159-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 windows foreground honesty
# AC-FR0350-01@v0.12 TRACKS-TRACE IF-GLOBALCLI-001 windows refuses background
def test_ac_nfr0159_01_windows_no_background_no_pidfile(
    tmp_path, capsys, monkeypatch
):
    port = _free_port()
    root = _seed_project(tmp_path, _HEALTH_LAUNCHER)
    monkeypatch.setenv("TRAC_CLI_ENTRY", "1")
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    # Platform injection per test-plan §6.4: ``sys.platform``/``platform`` only
    # (``os.name`` drives pathlib's Path flavour and would break the host).
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    pidfile = root / ".tracks" / "service" / "serve.pid"

    rc = serve_cmd.cmd_serve(
        root, "--repo", str(root), "--background", "--port", str(port)
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "unsupported_platform" in err
    assert not pidfile.exists()

    rc = serve_cmd.cmd_serve(root, "--repo", str(root), "--stop")
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "unsupported_platform" in err

    started: list[tuple] = []
    monkeypatch.setattr(
        serve_cmd, "_serve", lambda *args, **kwargs: started.append(args) or 0
    )
    # Windows foreground honesty is asserted on the direct ``cmd_serve`` face
    # (the user-face install gate/delegation is covered by the frozen
    # integration node). A first start still needs the legacy password channel
    # (interfaces §2d keeps the exit-2 guidance); supply it so the probe
    # reaches the foreground composition and asserts the no-pidfile honesty.
    monkeypatch.delenv("TRAC_CLI_ENTRY", raising=False)
    monkeypatch.setenv("TRAC_SERVE_PASSWORD", "unit-test-password")
    rc = serve_cmd.cmd_serve(
        root, "--repo", str(root), "--host", _HOST, "--port", str(port)
    )
    capsys.readouterr()
    assert rc == 0
    assert len(started) == 1
    assert not pidfile.exists()
