"""T-002 RED: issue-mapping behavior body (FR-0270, IF-VERIFY-004).

Second RED slice of T-002: pins the still-undelivered issue-mapping half of
the github effects contract (the CI-readback half — readback_ci_run /
judge_ci_binding — is already delivered and green):

- AC-FR0270-01: real-mode issue creation is verified by an immediate API
  readback (api_verified=true) and lands in the authoritative map
  (.tracks/runtime/issue-map.json, vocabulary shared with the milestone
  closer consumer: issue_number + api_verified)
- AC-FR0270-02: FAKE artifacts are rejected in real mode (fake_rejected);
  fake-channel creation can never claim api_verified=true
- AC-FR0270-03: missing credentials at backend selection fail closed with a
  classified missing_token error (module precedent: readback_ci_run) — the
  silent Fake stand-in fallback on a missing token is stale and removed

All target tests fail on the pre-fix baseline with assertion_failure (the
contract functions do not exist yet / selection still returns the Fake
stand-in; no stub_token, no assembly errors).
Only unit tests are added (RED discipline, manifest red_test_paths =
tests/unit); no existing test pins the missing-token fallback outside the
explicit fake channel, so no test is modified or removed.

IF-VERIFY-004: select_issue_backend, create_issue_verified,
persist_issue_mapping, readback_issue, reject_fake_artifact
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from tracks.effects.github import (
    FakeIssueBackend,
    GithubBackend,
    GithubIssuesError,
    select_issue_backend,
)

_REPO = Path("/tmp/host-repo")  # pure backend paths never touch the repo dir


class _StubResp:
    """Minimal urlopen stand-in returning canned JSON bytes."""

    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install_stub(monkeypatch, captured, create_payload, readback_payload):
    """Stand in urllib.request.urlopen; route POST->create, GET->readback."""

    def _fake_urlopen(req, timeout=30):
        captured.append(
            {
                "method": req.get_method(),
                "url": req.full_url if hasattr(req, "full_url") else str(req),
                "auth": req.headers.get("Authorization"),
            }
        )
        if req.get_method() == "POST":
            return _StubResp(create_payload)
        return _StubResp(readback_payload)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


def _real_mode_env(monkeypatch):
    """Real-channel env: token + stand-in API base + live repo selector."""
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "o/r")
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)


# AC-FR0270-03@v0.8 TRACKS-TRACE IF-VERIFY-004 selection missing token -> attention
def test_select_missing_token_fails_closed(monkeypatch):
    """AC-FR0270-03: with a live agent channel and no GITHUB_TOKEN, backend
    selection must raise the classified missing_token error (readback_ci_run
    precedent) — never silently return the Fake stand-in."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "o/r")
    try:
        backend = select_issue_backend(_REPO, "v0.8")
    except GithubIssuesError as exc:
        assert exc.classification == "missing_token", (
            f"assertion failure: expected missing_token classification, "
            f"got {exc.classification!r}"
        )
        return
    raise AssertionError(
        f"assertion failure: missing token must fail closed, got backend "
        f"{type(backend).__name__} (stale silent Fake fallback)"
    )


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 create + API readback verified
def test_create_issue_verified_real_mode_api_verified(monkeypatch, tmp_path):
    """AC-FR0270-01: real-mode creation must be followed by an immediate API
    readback against the stand-in base and carry api_verified=true."""
    captured: list = []
    _install_stub(
        monkeypatch,
        captured,
        create_payload={"number": 7},
        readback_payload={"number": 7, "title": "t", "state": "open"},
    )
    _real_mode_env(monkeypatch)
    try:
        from tracks.effects.github import create_issue_verified
    except ImportError as err:
        raise AssertionError(
            "assertion failure: create_issue_verified not implemented in github effects"
        ) from err
    backend = GithubBackend(tmp_path, "v0.8")
    mapping = create_issue_verified(backend, "[FR-0270] t", "body", ["v0.8"])
    assert isinstance(mapping, dict), (
        f"assertion failure: create_issue_verified must return a mapping, got {mapping!r}"
    )
    assert mapping.get("api_verified") is True, (
        f"assertion failure: verified creation must carry api_verified=true, got {mapping!r}"
    )
    assert str(mapping.get("issue_number")) == "7", (
        f"assertion failure: mapping must record the issue number, got {mapping!r}"
    )
    methods = [call["method"] for call in captured]
    assert methods[:2] == ["POST", "GET"], (
        f"assertion failure: create must be followed by an API readback, got {methods!r}"
    )
    assert captured[0]["url"].endswith("/repos/o/r/issues"), (
        f"assertion failure: creation must POST the repo issues endpoint, got {captured[0]['url']!r}"
    )
    assert "/repos/o/r/issues/7" in captured[1]["url"], (
        f"assertion failure: readback must GET the created issue, got {captured[1]['url']!r}"
    )
    assert captured[1]["url"].startswith("http://127.0.0.1:9"), (
        f"assertion failure: readback must honor the stand-in API base, got {captured[1]['url']!r}"
    )


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-VERIFY-004 fake channel never verified
def test_create_issue_verified_fake_channel_never_verified(tmp_path):
    """AC-FR0270-02: creation through the fake stand-in channel must never
    claim api_verified=true — fake artifacts cannot become closable."""
    try:
        from tracks.effects.github import create_issue_verified
    except ImportError as err:
        raise AssertionError(
            "assertion failure: create_issue_verified not implemented in github effects"
        ) from err
    backend = FakeIssueBackend(tmp_path, "v0.8")
    mapping = create_issue_verified(backend, "[FR-0270] t", "body", ["v0.8"])
    assert isinstance(mapping, dict), (
        f"assertion failure: fake-channel creation must return a mapping, got {mapping!r}"
    )
    assert mapping.get("api_verified") is not True, (
        f"assertion failure: fake-channel creation must not claim api_verified, got {mapping!r}"
    )


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 issue readback via stand-in base
def test_readback_issue_verified_payload(monkeypatch):
    """AC-FR0270-01: readback_issue must GET the issue from the stand-in base
    with bearer auth and return an api_verified=true payload."""
    captured: list = []
    _install_stub(
        monkeypatch,
        captured,
        create_payload={},
        readback_payload={"number": 7, "title": "t", "state": "open"},
    )
    _real_mode_env(monkeypatch)
    try:
        from tracks.effects.github import readback_issue
    except ImportError as err:
        raise AssertionError(
            "assertion failure: readback_issue not implemented in github effects"
        ) from err
    data = readback_issue("acme/host", 7)
    assert isinstance(data, dict), (
        f"assertion failure: readback_issue must return a payload, got {data!r}"
    )
    assert data.get("api_verified") is True, (
        f"assertion failure: API readback must be api_verified=true, got {data!r}"
    )
    assert captured, "assertion failure: readback_issue must issue an API request"
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"].startswith("http://127.0.0.1:9"), (
        f"assertion failure: readback must honor the stand-in API base, got {captured[0]['url']!r}"
    )
    assert captured[0]["url"].endswith("/repos/acme/host/issues/7"), (
        f"assertion failure: readback must GET the requested issue, got {captured[0]['url']!r}"
    )
    assert captured[0]["auth"] == "Bearer token", (
        f"assertion failure: readback must send bearer auth, got {captured[0]['auth']!r}"
    )


# AC-FR0270-01@v0.8 TRACKS-TRACE IF-VERIFY-004 authoritative map crash-idempotent
def test_persist_issue_mapping_idempotent(tmp_path):
    """AC-FR0270-01: persist_issue_mapping must write the authoritative map at
    .tracks/runtime/issue-map.json (closer vocabulary: issue_number +
    api_verified) and a crash-retry re-persist must dedup, not duplicate."""
    try:
        from tracks.effects.github import persist_issue_mapping
    except ImportError as err:
        raise AssertionError(
            "assertion failure: persist_issue_mapping not implemented in github effects"
        ) from err
    backend = FakeIssueBackend(tmp_path, "v0.8")
    issue_id = backend.create_issue("[FR-0270] t", "body", ["v0.8"])
    mapping = {"issue_number": issue_id, "api_verified": False}
    persist_issue_mapping(tmp_path, "FR-0270", mapping)
    persist_issue_mapping(tmp_path, "FR-0270", mapping)  # crash-retry re-run
    map_path = tmp_path / ".tracks" / "runtime" / "issue-map.json"
    assert map_path.exists(), (
        f"assertion failure: authoritative map must exist at {map_path}"
    )
    data = json.loads(map_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), (
        f"assertion failure: authoritative map must be an object, got {data!r}"
    )
    assert list(data.keys()) == ["FR-0270"], (
        f"assertion failure: re-persist must dedup to one entry, got {list(data.keys())!r}"
    )
    entry = data["FR-0270"]
    assert str(entry.get("issue_number")) == "FAKE-1", (
        f"assertion failure: entry must record the issue number, got {entry!r}"
    )
    assert entry.get("api_verified") is not True, (
        f"assertion failure: fake mapping must not claim api_verified, got {entry!r}"
    )


# AC-FR0270-02@v0.8 TRACKS-TRACE IF-VERIFY-004 real mode rejects FAKE artifacts
def test_reject_fake_artifact_in_real_mode(monkeypatch):
    """AC-FR0270-02: in the real channel a FAKE-prefixed artifact must be
    rejected with the fake_rejected verdict and can never be api_verified."""
    _real_mode_env(monkeypatch)
    try:
        from tracks.effects.github import reject_fake_artifact
    except ImportError as err:
        raise AssertionError(
            "assertion failure: reject_fake_artifact not implemented in github effects"
        ) from err
    verdict = reject_fake_artifact("FAKE-90")
    assert isinstance(verdict, dict), (
        f"assertion failure: rejection must return a verdict, got {verdict!r}"
    )
    assert verdict.get("reason") == "fake_rejected", (
        f"assertion failure: FAKE artifact must be fake_rejected, got {verdict!r}"
    )
    assert verdict.get("api_verified") is not True, (
        f"assertion failure: rejected artifact must not be api_verified, got {verdict!r}"
    )


# guard: pytest import is the collection anchor for fixture-based probes
_ = pytest
