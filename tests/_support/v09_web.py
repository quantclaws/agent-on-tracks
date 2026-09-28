"""v0.9 web test helpers (IF-SERVE-001, IF-WEBAUTH-001, IF-STREAM-001).

Real-subprocess serve lifecycle only: no TestClient, no in-process app.
Helpers build stdlib HTTP calls, parse serve stdout, and poll healthz.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY = sys.executable
_SERVE_PASSWORD = "v09-test-password"


def serve_cmd(home: Path, repo: Path, port: int = 0) -> list[str]:
    """Argv for a real serve subprocess (interfaces section 2a).

    The ``serve`` subcommand is registered in ``tracks/cli/main.py``
    ``_COMMANDS`` by the Devon foundation task; until then the CLI
    rejects it with USAGE (fail-closed, no fake service).
    """
    return [
        _PY,
        "-c",
        "import pathlib, sys; "
        "from tracks.cli.serve_cmd import cmd_serve; "
        "sys.exit(cmd_serve(pathlib.Path('.'), *sys.argv[1:]))",
        "--repo",
        str(repo),
        "--home",
        str(home),
        "--port",
        str(port),
        "--password-stdin",
    ]


def start_serve(home: Path, repo: Path, port: int = 0) -> subprocess.Popen:
    """Start serve as a real subprocess; caller must stop it."""
    home.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        serve_cmd(home, repo, port),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    assert proc.stdin is not None
    proc.stdin.write(_SERVE_PASSWORD + "\n")
    proc.stdin.flush()
    return proc


def stop_serve(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """Terminate a serve subprocess started by start_serve."""
    import contextlib

    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()


def wait_for_port_line(proc: subprocess.Popen, timeout: float = 8.0) -> str:
    """Read stdout until the 'serving on http://' line or timeout."""
    start = time.time()
    buf = ""
    while time.time() - start < timeout:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            err = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(
                f"serve exited early code={proc.returncode} stdout={out!r} stderr={err!r}"
            )
        import select

        if proc.stdout and select.select([proc.stdout], [], [], 0.2)[0]:
            line = proc.stdout.readline()
            buf += line
            if "serving on http://" in line:
                return line.strip()
    raise AssertionError(f"serve never printed serving line; got={buf!r}")


def parse_base_url(serving_line: str) -> str:
    """Extract http://host:port from the serve stdout line (section 2a)."""
    token = "serving on "
    idx = serving_line.find(token)
    assert idx >= 0, f"no serving marker in {serving_line!r}"
    rest = serving_line[idx + len(token):].strip().split()[0]
    return rest.rstrip("/")


def http_get(base_url: str, path: str, timeout: float = 5.0, cookies: str = "") -> tuple[int, bytes]:
    """GET via stdlib only; returns (status, body)."""
    req = urllib.request.Request(base_url + path, method="GET")
    if cookies:
        req.add_header("Cookie", cookies)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def http_post(
    base_url: str,
    path: str,
    payload: dict,
    cookies: str = "",
    csrf: str = "",
    idempotency_key: str = "",
    timeout: float = 5.0,
) -> tuple[int, bytes]:
    """POST JSON via stdlib; returns (status, body)."""
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if cookies:
        headers["Cookie"] = cookies
    if csrf:
        headers["X-Trac-CSRF"] = csrf
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def login_session(
    base_url: str,
    password: str = _SERVE_PASSWORD,
    timeout: float = 5.0,
    display_name: str = "Shell Human",
) -> tuple[str, str]:
    """Login via POST /api/auth/login (§2b #1); returns (cookie, csrf).

    The first-start password is the one start_serve fed the subprocess on
    stdin; the session cookie and CSRF token drive the authenticated
    journey endpoints. v0.10 name gate (§1m.2): when the login response
    declares ``name_required``, the display name is submitted through
    POST /api/auth/name (§2b #29 — idempotent, contract-validated) so the
    returned session is provisioned for the workbench data face; servers
    without the name gate are unaffected.
    """
    data = json.dumps({"password": password}).encode()
    req = urllib.request.Request(
        base_url + "/api/auth/login", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
        cookie = resp.headers.get("Set-Cookie", "")
    csrf = body["csrf_token"]
    assert cookie and csrf, f"login must issue session + csrf, got {body!r}"
    if body.get("name_required"):
        name_data = json.dumps({"name": display_name}).encode()
        name_req = urllib.request.Request(
            base_url + "/api/auth/name",
            data=name_data,
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Trac-CSRF": csrf,
            },
            method="POST",
        )
        with urllib.request.urlopen(name_req, timeout=timeout) as resp:
            assert resp.status == 200, (
                f"the name step must bind the display name: {resp.status}"
            )
    return cookie, csrf


def wait_for_healthz(base_url: str, timeout: float = 10.0) -> dict:
    """Poll GET /healthz until 200 or timeout; returns parsed JSON."""
    start = time.time()
    last: tuple[int, bytes] = (0, b"")
    while time.time() - start < timeout:
        try:
            status, body = http_get(base_url, "/healthz")
        except OSError:
            time.sleep(0.2)
            continue
        last = (status, body)
        if status == 200:
            return json.loads(body.decode())
        time.sleep(0.2)
    raise AssertionError(f"healthz never 200; last={last!r}")
