"""Live GithubBackend channel tests (FR-0200, D-05) — stand-in the external
urllib.request.urlopen (NOT tracks' own code) to exercise the HTTP classification
and payload logic of the live GitHub Issues path.
"""

import json
import urllib.error
from pathlib import Path

import pytest

from tracks.effects.github import (
    FakeIssueBackend,
    GithubBackend,
    GithubIssuesError,
    select_issue_backend,
)

_REPO = Path("/tmp/host-repo")  # pure backend; repo path is never touched


class _FakeResp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return json.dumps(self.data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _make_urlopen(resp=None, raise_http=None, network_error=False):
    def urlopen(req, timeout):
        if raise_http is not None:
            code, reason = raise_http
            raise urllib.error.HTTPError(req.full_url, code, reason, None, None)
        if network_error:
            raise urllib.error.URLError("boom")
        return _FakeResp(resp if resp is not None else {})

    return urlopen


def _backend(monkeypatch, repo="o/r", project=""):
    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    monkeypatch.setenv("TRAC_GITHUB_REPO", repo)
    if project:
        monkeypatch.setenv("TRAC_GITHUB_PROJECT", project)
    return GithubBackend(_REPO, "v0.2")


# --- constructor ------------------------------------------------------------


def test_init_requires_repo_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    with pytest.raises(GithubIssuesError) as ei:
        GithubBackend(_REPO, "v0.2")
    assert ei.value.classification == "not_found"


def test_init_reads_repo_and_project(monkeypatch):
    b = _backend(monkeypatch, repo="owner/repo", project="col123")
    assert b.gh_repo == "owner/repo" and b.project == "col123"


# --- _request + HTTP classification ----------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (401, "auth"),
        (403, "rate_limit"),
        (404, "not_found"),
        (429, "rate_limit"),
        (500, "network"),
    ],
)
def test_http_errors_classified(monkeypatch, code, expected):
    b = _backend(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", _make_urlopen(raise_http=(code, "reason")))
    with pytest.raises(GithubIssuesError) as ei:
        b._request("https://api.github.com/x", {})
    assert ei.value.classification == expected


def test_urlerror_classified_network(monkeypatch):
    b = _backend(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", _make_urlopen(network_error=True))
    with pytest.raises(GithubIssuesError) as ei:
        b._request("https://api.github.com/x", {})
    assert ei.value.classification == "network"


def test_request_returns_json(monkeypatch):
    b = _backend(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", _make_urlopen(resp={"n": 1}))
    assert b._request("https://api.github.com/x", {}) == {"n": 1}


# --- create_issue / add_to_project -----------------------------------------


def test_create_issue_returns_number(monkeypatch):
    b = _backend(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", _make_urlopen(resp={"number": 7}))
    assert b.create_issue("[FR-1] t", "body", ["v0.2"]) == "7"


def test_add_to_project_posts_card(monkeypatch):
    b = _backend(monkeypatch)
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout: calls.append(req.full_url) or _FakeResp({})
    )
    b.add_to_project("7", "col123")
    assert calls and "cards" in calls[0]


def test_add_to_project_noop_without_project(monkeypatch):
    b = _backend(monkeypatch, project="")  # empty project
    calls = []
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout: calls.append(req.full_url) or _FakeResp({})
    )
    b.add_to_project("7", "")
    assert calls == []  # no request issued


# --- select backend ---------------------------------------------------------


def test_select_live_when_token_and_opencode(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_GITHUB_REPO", "o/r")
    b = select_issue_backend(_REPO, "v0.2")
    assert isinstance(b, GithubBackend)


def test_select_fake_when_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    b = select_issue_backend(_REPO, "v0.2")
    assert isinstance(b, FakeIssueBackend)


def test_select_fake_when_fake_backend(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    b = select_issue_backend(_REPO, "v0.2")
    assert isinstance(b, FakeIssueBackend)
