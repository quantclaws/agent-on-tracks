"""Loopback GitHub CI stand-in that echoes the requested candidate head.

Journey anchors freeze their candidate only after the walk, so the CI
readback target cannot be pre-configured with a fixed sha (the
``test_verify_ci_readback._CiStandIn`` pattern). This stand-in answers any
``/actions/runs?head_sha=<sha>`` request with one completed successful run
for that exact head and the declared workflow identity, so the full release
chain (M-VERIFY readback -> M-RELEASE preview) passes deterministically
without network. Required checks, when a host contract declares any, are
served from the jobs endpoint.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_REPO = "acme/host"
DEFAULT_WORKFLOW_PATH = ".github/workflows/ci.yml"
DEFAULT_WORKFLOW_ID = 123
DEFAULT_RUN_ID = 17


class CiEchoStandIn:
    """Minimal ``actions/runs`` + ``actions/runs/{id}/jobs`` echo subset.

    Also serves the M-MILESTONE close chain (IF-ISSUE-002, additive): issue
    comment POST / issue PATCH+GET and milestone PATCH+GET over the same
    stateful loopback channel, so authoritative close readbacks verify.
    """

    def __init__(
        self,
        *,
        repo: str = DEFAULT_REPO,
        workflow_path: str = DEFAULT_WORKFLOW_PATH,
        workflow_id: int = DEFAULT_WORKFLOW_ID,
        run_id: int = DEFAULT_RUN_ID,
        checks: dict[str, str] | None = None,
        fake_issue_number: str | None = None,
    ):
        self.repo = repo
        self.workflow_path = workflow_path
        self.workflow_id = workflow_id
        self.run_id = run_id
        self.checks = dict(checks or {})
        self.fake_issue_number = fake_issue_number
        self.requests: list[str] = []
        self.issue_states: dict[int, str] = {}
        self.issues: dict[int, dict] = {}
        self.next_issue_number = 1
        self.last_comment_id = 0
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _reply(self, status: int, body: dict):
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

            def _issue_api(self, method: str) -> bool:
                collection = f"/repos/{owner.repo}/issues"
                if self.path == collection:
                    if method == "POST":
                        body = self._json_body()
                        if owner.fake_issue_number is not None:
                            entry = {
                                "number": owner.fake_issue_number,
                                "title": body.get("title", ""),
                                "state": "open",
                            }
                            self._reply(201, dict(entry))
                            return True
                        number = owner.next_issue_number
                        owner.next_issue_number += 1
                        entry = {
                            "number": number,
                            "title": body.get("title", ""),
                            "state": "open",
                        }
                        owner.issues[number] = entry
                        self._reply(201, dict(entry))
                        return True
                    self._reply(404, {"message": "not found"})
                    return True
                prefix = f"{collection}/"
                if not self.path.startswith(prefix):
                    return False
                tail = self.path[len(prefix) :].split("/")
                try:
                    number = int(tail[0])
                except (ValueError, IndexError):
                    self._reply(404, {"message": "not found"})
                    return True
                if method == "POST" and len(tail) > 1 and tail[1] == "comments":
                    self._json_body()
                    owner.last_comment_id += 1
                    self._reply(201, {"id": owner.last_comment_id})
                    return True
                if method == "PATCH":
                    state = str(self._json_body().get("state") or "closed")
                    owner.issue_states[number] = state
                    if number in owner.issues:
                        owner.issues[number]["state"] = state
                    self._reply(200, {"number": number, "state": state})
                    return True
                if method == "GET":
                    entry = owner.issues.get(number)
                    state = owner.issue_states.get(
                        number, (entry or {}).get("state", "open")
                    )
                    self._reply(
                        200,
                        {
                            "number": number,
                            "title": (entry or {}).get("title", "issue"),
                            "state": state,
                        },
                    )
                    return True
                self._reply(404, {"message": "not found"})
                return True

            def _milestone_api(self, method: str) -> bool:
                prefix = f"/repos/{owner.repo}/milestones/"
                if not self.path.startswith(prefix):
                    return False
                if method == "PATCH":
                    self._json_body()
                    self._reply(200, {"title": "milestone", "state": "closed"})
                    return True
                if method == "GET":
                    self._reply(200, {"title": "milestone", "state": "closed"})
                    return True
                self._reply(404, {"message": "not found"})
                return True

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                parsed = urlparse(self.path)
                with owner._lock:
                    owner.requests.append(self.path)
                if self._issue_api("GET") or self._milestone_api("GET"):
                    return
                prefix = f"/repos/{owner.repo}/actions/"
                if parsed.path == f"{prefix}runs":
                    head_sha = (parse_qs(parsed.query).get("head_sha") or [""])[0]
                    owner._reply_runs(self, head_sha)
                    return
                if parsed.path == f"{prefix}runs/{owner.run_id}/jobs":
                    self._reply(
                        200,
                        {
                            "total_count": len(owner.checks),
                            "jobs": [
                                {
                                    "id": index,
                                    "run_id": owner.run_id,
                                    "name": name,
                                    "status": "completed",
                                    "conclusion": conclusion,
                                }
                                for index, (name, conclusion) in enumerate(
                                    owner.checks.items(), 1
                                )
                            ],
                        },
                    )
                    return
                self._reply(404, {"message": "not found"})

            def do_POST(self):  # noqa: N802
                with owner._lock:
                    owner.requests.append(self.path)
                if self._issue_api("POST"):
                    return
                self._reply(404, {"message": "not found"})

            def do_PATCH(self):  # noqa: N802
                with owner._lock:
                    owner.requests.append(self.path)
                if self._issue_api("PATCH") or self._milestone_api("PATCH"):
                    return
                self._reply(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _reply_runs(self, handler: BaseHTTPRequestHandler, head_sha: str) -> None:
        handler._reply(
            200,
            {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": self.run_id,
                        "name": "CI",
                        "workflow_id": self.workflow_id,
                        "path": self.workflow_path,
                        "head_sha": head_sha,
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            },
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> CiEchoStandIn:
        self._thread.start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
