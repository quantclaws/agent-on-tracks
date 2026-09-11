"""Unit: GitHub issue/project close API seam (FR-0284, AC-FR0284-01).

The M-MILESTONE closer's irreversible effects go through the same
``TRAC_GITHUB_API_BASE`` stand-in channel as the CI/issue readbacks: comment +
PATCH close + readback verification for issues, PATCH + readback for
milestones. Missing credentials stay a classified ``missing_token`` error and
transport/HTTP failures are normalized into ``api_verified=False`` — a close
is never claimed without a remote confirming readback.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tracks.effects.github import (
    GithubIssuesError,
    close_issue,
    close_project_milestone,
)

REPO = "acme/host"


class _CloseStandIn:
    """Loopback close subset: comment POST, issue PATCH/GET, milestone
    PATCH/GET. ``fail_issue`` makes the issue endpoints answer HTTP 500."""

    def __init__(self, *, fail_issue: bool = False):
        owner = self
        self.fail_issue = fail_issue
        self.requests: list[tuple[str, str]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _reply(self, status: int, body: dict | None = None):
                raw = b"" if body is None else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                owner.requests.append(("POST", self.path))
                if owner.fail_issue:
                    self._reply(500, {"message": "boom"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self._reply(201, {"id": 99, "body": "trace"})

            def do_PATCH(self):  # noqa: N802
                owner.requests.append(("PATCH", self.path))
                if owner.fail_issue and "/issues/" in self.path:
                    self._reply(500, {"message": "boom"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self._reply(200, {"state": "closed"})

            def do_GET(self):  # noqa: N802
                owner.requests.append(("GET", self.path))
                if owner.fail_issue and "/issues/" in self.path:
                    self._reply(500, {"message": "boom"})
                    return
                if "/milestones/" in self.path:
                    self._reply(200, {"title": "v0.8", "state": "closed"})
                    return
                self._reply(200, {"number": 12, "title": "t", "state": "closed"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


def _env(monkeypatch, server, *, token="loopback-token"):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", server.base_url)
    monkeypatch.setenv("TRAC_GITHUB_REPO", REPO)
    if token is None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GITHUB_TOKEN", token)


# AC-FR0284-01@v0.8 TRACKS-TRACE issue comment close and readback verified
def test_close_issue_comments_closes_and_reads_back(monkeypatch):
    server = _CloseStandIn().start()
    try:
        _env(monkeypatch, server)
        result = close_issue(REPO, 12, "release trace: candidate_sha=abc")
        assert result["state"] == "closed"
        assert result["comment_id"] == 99
        assert result["api_verified"] is True
        assert result["error"] is None
        calls = [method for method, _path in server.requests]
        assert calls == ["POST", "PATCH", "GET"]
    finally:
        server.close()


# AC-FR0284-01@v0.8 TRACKS-TRACE close failure never claims api_verified
def test_close_issue_failure_is_fail_closed(monkeypatch):
    server = _CloseStandIn(fail_issue=True).start()
    try:
        _env(monkeypatch, server)
        result = close_issue(REPO, 12, "release trace")
        assert result["api_verified"] is False
        assert result["error"], "a failed close must carry its classified error"
        assert result["state"] != "closed"
    finally:
        server.close()


# AC-FR0284-01@v0.8 TRACKS-TRACE close requires credentials
def test_close_issue_missing_token(monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_REPO", REPO)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError) as excinfo:
        close_issue(REPO, 12, "release trace")
    assert excinfo.value.classification == "missing_token"


# AC-FR0284-01@v0.8 TRACKS-TRACE project milestone close readback verified
def test_close_project_milestone_verified(monkeypatch):
    server = _CloseStandIn().start()
    try:
        _env(monkeypatch, server)
        result = close_project_milestone(REPO, "release v0.8", 55)
        assert result["state"] == "closed"
        assert result["api_verified"] is True
        assert result["project"] == "release v0.8"
        assert [method for method, _path in server.requests] == ["PATCH", "GET"]
    finally:
        server.close()
    # An undeclared milestone is malformed, not a claimed close.
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    result = close_project_milestone(REPO, "release v0.8", "")
    assert result["api_verified"] is False
    assert result["error"] == "milestone_not_declared"
