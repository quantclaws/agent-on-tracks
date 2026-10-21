"""T-002 RED: background readiness must belong to our own child (IF-SERVERLIFE-001).

PRISM-PLAN-09 / interfaces §1e.2 / test-plan §10 #17: ``trac serve
--background`` may only report success — write the pidfile and announce
``serving on ...`` — when the serving endpoint belongs to the child it
spawned. A healthz 200 on the requested port is not proof of readiness: a
foreign listener can answer it while our child fails to bind, which used to
leave a pidfile pointing at a dead process and made ``--stop`` report a stale
pidfile instead of stopping a service.

The contract surface is the ``cmd_serve --background`` face and its bounded
readiness wait. Launchers are external inputs (interfaces §1b.3): the
stand-ins below are real subprocesses and loopback listeners, not mocks of
the system under test.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tracks.cli import serve_cmd

from .install_red_support import seed_installed_project

_HOST = "127.0.0.1"
_VERSION = "9.9.9"


@pytest.fixture(autouse=True)
def _hermetic_service_env(monkeypatch):
    """Neither the delegated face nor an operator service home leaks in."""
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    monkeypatch.delenv("TRAC_SERVE_HOME", raising=False)

# A launcher stand-in that serves /healthz on the requested port and prints
# the same startup announcement the real foreground serve emits (§1e.1).
_SERVING_LAUNCHER = '''\
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status": "ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
print(f"serving on http://127.0.0.1:{port} (pid {os.getpid()})", flush=True)
server.serve_forever()
'''

# A launcher stand-in that stays alive but never binds or serves the port —
# the child a bounded readiness wait must not confuse with a foreign listener.
_SILENT_LAUNCHER = "import time\ntime.sleep(60)\n"


class _HealthHandler(BaseHTTPRequestHandler):
    """A foreign loopback listener that answers every request with healthz 200."""

    def do_GET(self) -> None:
        body = b'{"status": "ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextlib.contextmanager
def _foreign_health_listener():
    """Bind a foreign healthz-200 listener and yield its port."""
    server = ThreadingHTTPServer((_HOST, 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])


def _seed_background_project(tmp_path: Path, launcher: str) -> Path:
    return seed_installed_project(tmp_path / "project", _VERSION, launcher=launcher)


def _run_background(
    root: Path, port: int, wait_initial_s: str, capsys
) -> tuple[int, str, str]:
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
        wait_initial_s,
    )
    out, err = capsys.readouterr()
    return rc, out, err


def _cleanup_pidfile(root: Path) -> bool:
    """Stop a mis-announced child and consume its pidfile; return whether one existed."""
    pidfile = root / ".tracks" / "service" / "serve.pid"
    if not pidfile.exists():
        return False
    pid = int(json.loads(pidfile.read_text(encoding="utf-8"))["pid"])
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    with contextlib.suppress(ChildProcessError, ProcessLookupError):
        os.waitpid(pid, 0)
    pidfile.unlink()
    return True


def _wait_health(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://{_HOST}:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"launcher stand-in did not answer {url}")


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 taken port fails without pidfile
# AC-NFR0159-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 no pidfile for a non-serving child
def test_background_on_taken_port_fails_without_pidfile(
    tmp_path: Path, capsys
) -> None:
    root = _seed_background_project(tmp_path, _SILENT_LAUNCHER)
    with _foreign_health_listener() as port:
        rc, out, err = _run_background(root, port, "1", capsys)
    wrote_pidfile = _cleanup_pidfile(root)
    assert "background_failed:" in err, (out, err)
    assert rc == 1, (out, err)
    assert wrote_pidfile is False, "a failed background start wrote a pidfile"
    assert "serving on" not in out, out


# AC-FR0350-01@v0.12 TRACKS-TRACE IF-SERVERLIFE-001 own child readiness is confirmed
def test_background_with_serving_child_writes_pidfile(
    tmp_path: Path, capsys
) -> None:
    root = _seed_background_project(tmp_path, _SERVING_LAUNCHER)
    try:
        rc, out, err = _run_background(root, _free_port(), "10", capsys)
        pidfile = root / ".tracks" / "service" / "serve.pid"
        assert rc == 0, (out, err)
        assert err == ""
        assert pidfile.exists(), out
        payload = json.loads(pidfile.read_text(encoding="utf-8"))
        assert payload["version"] == _VERSION
        _wait_health(int(payload["port"]))
    finally:
        _cleanup_pidfile(root)
