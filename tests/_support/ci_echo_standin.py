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
    """Minimal ``actions/runs`` + ``actions/runs/{id}/jobs`` echo subset."""

    def __init__(
        self,
        *,
        repo: str = DEFAULT_REPO,
        workflow_path: str = DEFAULT_WORKFLOW_PATH,
        workflow_id: int = DEFAULT_WORKFLOW_ID,
        run_id: int = DEFAULT_RUN_ID,
        checks: dict[str, str] | None = None,
    ):
        self.repo = repo
        self.workflow_path = workflow_path
        self.workflow_id = workflow_id
        self.run_id = run_id
        self.checks = dict(checks or {})
        self.requests: list[str] = []
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

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                parsed = urlparse(self.path)
                with owner._lock:
                    owner.requests.append(self.path)
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
