"""Loopback GitHub REST stand-in for the v0.10 release-hygiene contracts.

L2 contract-sim infrastructure (test-plan §6.4): serves the issue and
milestone API subset the v0.10 features drive (``IF-HOTFIX-011`` precheck
classification, ``IF-TLS-001`` channel, ``IF-TRACKER-001`` milestone
lifecycle) over plain HTTP or TLS with the repo's generated test CA
(``tests/assets/v0.10/tls``). It implements only the documented request
surface — no tracks business — and records every request so idempotency
(no duplicate milestone creates) is observable.

Not pytest-collected: module name does not match ``test_*``.
"""

from __future__ import annotations

import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ASSETS = Path(__file__).resolve().parents[1] / "assets" / "v0.10" / "tls"

DEFAULT_REPO = "acme/host"


class GithubApiStandIn:
    """Stateful issues + milestones stand-in with injectable failures.

    ``issue_status`` / ``milestones`` / ``create_status`` shape the served
    responses; ``requests`` records every path+method so tests observe
    duplicate-creation and readback behavior. ``tls=True`` serves HTTPS with
    the test-CA-signed localhost certificate (the TLS control experiment).
    """

    def __init__(
        self,
        *,
        repo: str = DEFAULT_REPO,
        issues: dict[int, dict] | None = None,
        issue_status: int | None = None,
        milestones: list[dict] | None = None,
        create_status: int = 201,
        tls: bool = False,
    ):
        self.repo = repo
        self.issues = dict(issues or {})
        self.issue_status = issue_status
        self.milestones = list(milestones or [])
        self.create_status = create_status
        self.tls = tls
        self.requests: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _reply(self, status: int, body):
                raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _json_body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    return json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    return {}

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                with owner._lock:
                    owner.requests.append(("GET", urlparse(self.path).path))
                parsed = urlparse(self.path)
                collection = f"/repos/{owner.repo}/milestones"
                if parsed.path == collection:
                    query = parse_qs(parsed.query)
                    state = (query.get("state") or ["open"])[0]
                    rows = [
                        m
                        for m in owner.milestones
                        if state == "all" or m.get("state", "open") == state
                    ]
                    self._reply(200, rows)
                    return
                prefix = f"{collection}/"
                if parsed.path.startswith(prefix):
                    number = parsed.path[len(prefix) :]
                    for m in owner.milestones:
                        if str(m.get("number")) == number:
                            self._reply(200, m)
                            return
                    self._reply(404, {"message": "not found"})
                    return
                issue_prefix = f"/repos/{owner.repo}/issues/"
                if parsed.path.startswith(issue_prefix):
                    if owner.issue_status is not None:
                        self._reply(owner.issue_status, {"message": "injected"})
                        return
                    number = parsed.path[len(issue_prefix) :]
                    entry = owner.issues.get(int(number)) if number.isdigit() else None
                    if entry is None:
                        self._reply(404, {"message": "not found"})
                        return
                    self._reply(200, entry)
                    return
                self._reply(404, {"message": "not found"})

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                with owner._lock:
                    owner.requests.append(("POST", urlparse(self.path).path))
                if urlparse(self.path).path == f"/repos/{owner.repo}/milestones":
                    body = self._json_body()
                    if owner.create_status != 201:
                        self._reply(owner.create_status, {"message": "injected"})
                        return
                    number = len(owner.milestones) + 1
                    entry = {
                        "number": number,
                        "title": body.get("title", ""),
                        "state": "open",
                        "html_url": (
                            f"https://github.com/{owner.repo}/milestone/{number}"
                        ),
                    }
                    owner.milestones.append(entry)
                    owner.created.append(entry)
                    self._reply(201, entry)
                    return
                self._reply(404, {"message": "not found"})

            def do_PATCH(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                with owner._lock:
                    owner.requests.append(("PATCH", urlparse(self.path).path))
                prefix = f"/repos/{owner.repo}/milestones/"
                parsed = urlparse(self.path)
                if not parsed.path.startswith(prefix):
                    self._reply(404, {"message": "not found"})
                    return
                number = parsed.path[len(prefix) :]
                for milestone in owner.milestones:
                    if str(milestone.get("number")) == number:
                        milestone["state"] = "closed"
                        self._reply(200, milestone)
                        return
                self._reply(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(
                certfile=str(_ASSETS / "localhost.pem"),
                keyfile=str(_ASSETS / "localhost.key"),
            )
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        scheme = "https" if self.tls else "http"
        return f"{scheme}://127.0.0.1:{self.server.server_port}"

    @property
    def ca_bundle(self) -> str:
        """The test CA bundle path (``TRAC_GITHUB_CA_BUNDLE`` override)."""
        return str(_ASSETS / "test_ca.pem")

    def start(self) -> GithubApiStandIn:
        self._thread.start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
