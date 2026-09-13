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


# AC-FR0284-01@v0.8 TRACKS-TRACE title-declared milestones resolve to their
# number (GitHub REST milestones address by number; a title path 404s)
def _milestone_standin(requests, *, listing):
    """Self-contained milestone stand-in: listing + number-addressed close."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # pragma: no cover - server noise
            return

        def _reply(self, status: int, body):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
            requests.append(("GET", self.path))
            path = self.path.split("?", 1)[0]
            if path == f"/repos/{REPO}/milestones":
                self._reply(200, listing)
            elif path == f"/repos/{REPO}/milestones/7":
                self._reply(200, {"number": 7, "title": "release v0.8", "state": "closed"})
            else:
                self._reply(404, {"message": "not found"})

        def do_PATCH(self):  # noqa: N802
            requests.append(("PATCH", self.path))
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if self.path == f"/repos/{REPO}/milestones/7":
                self._reply(200, {"state": "closed"})
            else:
                self._reply(404, {"message": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_close_project_milestone_resolves_title_to_number(monkeypatch):
    # GitHub REST addresses milestones by NUMBER: a title-declared milestone
    # must resolve via the listing before PATCHing (live evidence: a title in
    # the path 404s on the real API).
    requests = []
    listing = [{"number": 7, "title": "release v0.8", "state": "open"}]
    server, thread = _milestone_standin(requests, listing=listing)
    try:
        _env(monkeypatch, type("S", (), {"base_url": f"http://127.0.0.1:{server.server_port}"}))
        result = close_project_milestone(REPO, "release v0.8", "release v0.8")
        assert result["state"] == "closed"
        assert result["api_verified"] is True
        paths = [path for _method, path in requests]
        assert paths[0] == f"/repos/{REPO}/milestones?state=all"
        assert f"/repos/{REPO}/milestones/7" in paths[1], paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_close_project_milestone_unknown_title_is_fail_closed(monkeypatch):
    requests = []
    server, thread = _milestone_standin(requests, listing=[])
    try:
        _env(monkeypatch, type("S", (), {"base_url": f"http://127.0.0.1:{server.server_port}"}))
        result = close_project_milestone(REPO, "release v0.8", "release v9.9")
        assert result["api_verified"] is False
        assert result["error"] == "milestone_not_found"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# FR-0283-01/04: global issue identity + crash-idempotent dedup search
# ---------------------------------------------------------------------------


class _IssueStandIn:
    """Loopback issues subset: POST create, GET listing, GET single.

    ``fail_search`` makes the listing answer HTTP 500; the store is shared so
    a created issue is immediately visible to the listing (like the real
    remote after a crash before persist).
    """

    def __init__(self, *, fail_search: bool = False):
        owner = self
        self.fail_search = fail_search
        self.requests: list[tuple[str, str]] = []
        self.issues: dict[int, dict] = {}
        self.next_number = 1

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _reply(self, status: int, body):
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _collection(self, path: str) -> bool:
                return path == f"/repos/{REPO}/issues"

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                owner.requests.append(("POST", self.path))
                if not self._collection(self.path.split("?", 1)[0]):
                    self._reply(404, {"message": "not found"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                number = owner.next_number
                owner.next_number += 1
                entry = {
                    "number": number,
                    "node_id": f"I_{number}",
                    "id": 1000 + number,
                    "title": payload.get("title", ""),
                    "body": payload.get("body", ""),
                    "state": "open",
                    "html_url": f"https://github.com/{REPO}/issues/{number}",
                }
                owner.issues[number] = entry
                self._reply(201, dict(entry))

            def do_GET(self):  # noqa: N802
                owner.requests.append(("GET", self.path))
                path = self.path.split("?", 1)[0]
                if self._collection(path):
                    if owner.fail_search:
                        self._reply(500, {"message": "boom"})
                        return
                    self._reply(200, list(owner.issues.values()))
                    return
                prefix = f"/repos/{REPO}/issues/"
                if path.startswith(prefix):
                    try:
                        number = int(path[len(prefix) :])
                    except ValueError:
                        self._reply(404, {"message": "not found"})
                        return
                    entry = owner.issues.get(number)
                    if entry is None:
                        self._reply(404, {"message": "not found"})
                    else:
                        self._reply(200, dict(entry))
                    return
                self._reply(404, {"message": "not found"})

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


def _issue_env(monkeypatch, server):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", server.base_url)
    monkeypatch.setenv("TRAC_GITHUB_REPO", REPO)
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)


_BASELINE = "sha256:baseline-digest"
_TITLE = "[FR-0270] issue dedup"


def _seed_matching_issue(server) -> dict:
    entry = {
        "number": 7,
        "node_id": "I_7",
        "id": 1007,
        "title": _TITLE,
        "body": f"item\n\nbaseline digest: {_BASELINE}\n",
        "state": "open",
        "html_url": f"https://github.com/{REPO}/issues/7",
    }
    server.issues[7] = entry
    server.next_number = 8
    return entry


# AC-FR0283-01@v0.8 TRACKS-TRACE readback carries the global issue identity
def test_readback_issue_carries_global_identity(monkeypatch):
    from tracks.effects.github import readback_issue

    server = _IssueStandIn().start()
    try:
        _issue_env(monkeypatch, server)
        _seed_matching_issue(server)
        data = readback_issue(REPO, 7)
        assert data["api_verified"] is True
        assert data["issue_number"] == 7
        assert data["node_id"] == "I_7"
        assert data["global_id"] == 1007
        assert data["url"].endswith("/issues/7")
    finally:
        server.close()


# AC-FR0283-01@v0.8 TRACKS-TRACE creation mapping includes node_id
def test_create_issue_verified_mapping_carries_global_identity(monkeypatch, tmp_path):
    from tracks.effects.github import GithubBackend, create_issue_verified

    server = _IssueStandIn().start()
    try:
        _issue_env(monkeypatch, server)
        mapping = create_issue_verified(
            GithubBackend(tmp_path, "v0.8"), _TITLE, "body", ["v0.8"]
        )
        assert mapping["api_verified"] is True
        assert mapping["node_id"] == "I_1"
        assert mapping["global_id"] == 1001
        assert mapping["url"].endswith("/issues/1")
        assert [method for method, _path in server.requests] == ["POST", "GET"]
    finally:
        server.close()


# AC-FR0283-04@v0.8 TRACKS-TRACE authoritative map reuse (persist survived)
def test_create_issue_verified_reuses_authoritative_mapping(monkeypatch, tmp_path):
    from tracks.effects.github import (
        GithubBackend,
        create_issue_verified,
        find_authoritative_mapping,
        persist_issue_mapping,
    )

    server = _IssueStandIn().start()
    try:
        _issue_env(monkeypatch, server)
        persist_issue_mapping(
            tmp_path,
            "FR-0270",
            {
                "issue_number": 7,
                "node_id": "I_7",
                "title": _TITLE,
                "url": f"https://github.com/{REPO}/issues/7",
                "repo": REPO,
                "baseline_digest": _BASELINE,
                "api_verified": True,
                "authoritative": True,
                "source": "issue_create",
            },
        )
        assert find_authoritative_mapping(tmp_path, REPO, _TITLE, _BASELINE) is not None
        mapping = create_issue_verified(
            GithubBackend(tmp_path, "v0.8"),
            _TITLE,
            "body",
            ["v0.8"],
            repo_dir=tmp_path,
            repo_id=REPO,
            baseline_digest=_BASELINE,
        )
        assert mapping["api_verified"] is True
        assert mapping["reused"] is True
        assert mapping["recovered"] is False
        assert mapping["issue_number"] == 7
        assert mapping["node_id"] == "I_7"
        # No remote call at all: the authoritative map is the dedup key.
        assert server.requests == []
    finally:
        server.close()


# AC-FR0283-04@v0.8 TRACKS-TRACE remote recovery before persist (crash window)
def test_create_issue_verified_recovers_remote_issue_before_persist(monkeypatch, tmp_path):
    from tracks.effects.github import GithubBackend, create_issue_verified

    server = _IssueStandIn().start()
    try:
        _issue_env(monkeypatch, server)
        _seed_matching_issue(server)
        mapping = create_issue_verified(
            GithubBackend(tmp_path, "v0.8"),
            _TITLE,
            "body",
            ["v0.8"],
            repo_dir=tmp_path,
            repo_id=REPO,
            baseline_digest=_BASELINE,
        )
        assert mapping["api_verified"] is True
        assert mapping["reused"] is True
        assert mapping["recovered"] is True
        assert mapping["issue_number"] == 7
        assert mapping["node_id"] == "I_7"
        methods = [method for method, _path in server.requests]
        assert methods == ["GET", "GET"]  # listing search + verification readback
        assert not any(path.endswith("/issues") and method == "POST" for method, path in server.requests)
    finally:
        server.close()


# AC-FR0283-04@v0.8 TRACKS-TRACE same title other baseline is never reused
def test_create_issue_verified_ignores_same_title_other_baseline(monkeypatch, tmp_path):
    from tracks.effects.github import GithubBackend, create_issue_verified

    server = _IssueStandIn().start()
    try:
        _issue_env(monkeypatch, server)
        entry = _seed_matching_issue(server)
        entry["body"] = "item\n\nbaseline digest: sha256:other\n"
        mapping = create_issue_verified(
            GithubBackend(tmp_path, "v0.8"),
            _TITLE,
            "new body",
            ["v0.8"],
            repo_dir=tmp_path,
            repo_id=REPO,
            baseline_digest=_BASELINE,
        )
        assert mapping["recovered"] is False
        assert str(mapping["issue_number"]) == "8"  # a fresh create, not issue 7
        methods = [method for method, _path in server.requests]
        assert methods == ["GET", "POST", "GET"]
    finally:
        server.close()


# AC-FR0283-04@v0.8 TRACKS-TRACE dedup search failure fails closed (no create)
def test_create_issue_verified_search_failure_fails_closed(monkeypatch, tmp_path):
    from tracks.effects.github import (
        GithubBackend,
        GithubIssuesError,
        create_issue_verified,
    )

    server = _IssueStandIn(fail_search=True).start()
    try:
        _issue_env(monkeypatch, server)
        with pytest.raises(GithubIssuesError) as excinfo:
            create_issue_verified(
                GithubBackend(tmp_path, "v0.8"),
                _TITLE,
                "body",
                ["v0.8"],
                repo_dir=tmp_path,
                repo_id=REPO,
                baseline_digest=_BASELINE,
            )
        assert excinfo.value.classification == "network"
        assert [method for method, _path in server.requests] == ["GET"]
    finally:
        server.close()


# AC-FR0283-04@v0.8 TRACKS-TRACE unverified map entries never satisfy the key
def test_find_authoritative_mapping_requires_api_verified(tmp_path):
    from tracks.effects.github import find_authoritative_mapping, persist_issue_mapping

    persist_issue_mapping(
        tmp_path,
        "FR-0270",
        {
            "issue_number": "FAKE-1",
            "title": _TITLE,
            "repo": REPO,
            "baseline_digest": _BASELINE,
            "api_verified": False,
        },
    )
    assert find_authoritative_mapping(tmp_path, REPO, _TITLE, _BASELINE) is None
