"""T-006 RED: live GitHub channel TLS + the milestone ensure primitive
(IF-TLS-001 main contract, interfaces 1r.4; IF-TRACKER-001's github.py
delivery face, interfaces 1r.5, issue #181).

Devon-owned unit RED for the T-006 delivery slice (scope:
``tracks/effects/github.py``):

- every HTTPS call point (the ``_urlopen`` / ``_get_any`` / ``_api_json``
  chain) must drive urllib with an explicit verifying SSL context built
  from ``certifi.where()``, with ``TRAC_GITHUB_CA_BUNDLE`` as the
  explicit override path (interfaces 1r.4.1);
- classified ``missing_token`` failures must point operators at the ops
  prerequisite section in ``docs/getting-started/installation.md``
  (interfaces 1r.4.2, AC-FR0330-02);
- the new ``ensure_project_milestone(repo_id, project, title)``
  primitive: list by exact title match -> reuse (``created=false``);
  miss with credentials -> create + API readback (``created=true``);
  any failure returns a classified error and never claims
  ``api_verified`` (interfaces 1r.5.1).

RED discipline: the explicit ssl context kwarg and the ops-doc pointer
do not exist on the current tree and ``ensure_project_milestone`` is
absent, so every node below fails as a real ``AssertionError`` (explicit
context asserts, CA-bundle equality against a freshly built reference
context, and getattr guards) — no stub tokens, no assembly errors. Only
the external boundary (GitHub REST over urllib) is replaced by a
scripted stand-in; the SSL context construction itself stays the system
under test and is never mocked (test-plan 6.2).

AC: FR-0330/FR-0331 — TRACKS-TRACE IF-TLS-001.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import certifi
import pytest

from tracks.effects import github

_TITLE = "v0.10.0 release"
_REPO = "owner/repo"
_OPS_PREREQ_DOC = "docs/getting-started/installation.md"

_ISSUE_PAYLOAD = {
    "number": 5,
    "id": 901,
    "node_id": "NODE-5",
    "title": "[FR-0330] live channel TLS",
    "body": "body",
    "state": "open",
    "html_url": "https://example.invalid/issues/5",
    "labels": [{"name": "release"}],
}

_MILESTONE_CLOSED = {"number": 7, "title": _TITLE, "state": "closed"}


@dataclass
class _TransportCall:
    """One observed urlopen() call: method, url and the ssl context sent."""

    method: str
    url: str
    context: object


class _JsonResponse:
    """Minimal urlopen() result stand-in: context manager + one read()."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None


class _ScriptedOpener:
    """urlopen stand-in routing every call through a per-test handler.

    The handler receives (method, url, body) and returns the JSON payload
    (or raises). Each transport call records the ssl context it was
    handed — that context is the IF-TLS-001 observable under test.
    """

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[_TransportCall] = []

    def __call__(self, req, timeout=None, context=None, **kwargs):
        method = req.get_method()
        url = req.full_url
        body = json.loads(req.data.decode("utf-8")) if req.data else None
        self.calls.append(_TransportCall(method, url, context))
        return _JsonResponse(self._handler(method, url, body))


def _http_error(url: str, code: int, reason: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, reason, None, None)


def _install(monkeypatch, handler) -> _ScriptedOpener:
    """Patch the transport boundary and arm a clean live-channel env."""
    monkeypatch.delenv("TRAC_GITHUB_CA_BUNDLE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "t-tls-unit")
    opener = _ScriptedOpener(handler)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    return opener


def _assert_explicit_tls_context(call: _TransportCall, cafile: Path) -> None:
    """IF-TLS-001 (interfaces 1r.4.1) observable: the transport call carries
    an explicit verifying SSL context whose trust bundle is exactly
    ``cafile``'s (certifi default, or the TRAC_GITHUB_CA_BUNDLE override)."""
    assert call.context is not None, (
        f"{call.method} {call.url} was sent without an explicit ssl context — "
        "interfaces 1r.4.1 requires every HTTPS call point "
        "(_urlopen/_get_any/_api_json chain) to drive urllib with "
        "ssl.create_default_context(cafile=...)"
    )
    context = call.context
    assert isinstance(context, ssl.SSLContext), (
        "the explicit context must be a real ssl.SSLContext (interfaces 1r.4.1)"
    )
    assert context.verify_mode == ssl.CERT_REQUIRED, (
        "the explicit context must keep certificate verification required "
        "(interfaces 1r.4.1: explicit TLS validation, not a disabled check)"
    )
    reference = ssl.create_default_context(cafile=str(cafile))
    assert context.get_ca_certs() == reference.get_ca_certs(), (
        f"the explicit context must trust exactly the {cafile} CA bundle "
        "(interfaces 1r.4.1: certifi.where() by default, "
        "TRAC_GITHUB_CA_BUNDLE when set)"
    )


def _single_cert_bundle(tmp_path: Path) -> Path:
    """A one-certificate override bundle distinct from certifi's default.

    Slicing the first PEM block out of the certifi bundle yields a valid
    CA file whose trust set (exactly one certificate) is observably
    different from the full default, so override precedence is provable
    without generating new key material.
    """
    bundle = Path(certifi.where()).read_text(encoding="utf-8")
    begin = "-----BEGIN CERTIFICATE-----"
    end = "-----END CERTIFICATE-----"
    start = bundle.index(begin)
    finish = bundle.index(end, start) + len(end)
    target = tmp_path / "override-ca.pem"
    target.write_text(bundle[start:finish] + "\n", encoding="utf-8")
    return target


def _ensure_fn():
    """The interfaces 1r.5.1 ensure seam; missing symbol = AssertionError."""
    fn = getattr(github, "ensure_project_milestone", None)
    assert fn is not None, (
        "tracks.effects.github.ensure_project_milestone missing — interfaces "
        "1r.5.1 (IF-TRACKER-001 github.py delivery face, task T-006) requires "
        "the list->create->readback milestone ensure primitive"
    )
    return fn


class _MilestonesApi:
    """Stateful stand-in for the GitHub milestones REST face (1r.5.1).

    GET  .../milestones       -> the current list;
    POST .../milestones       -> create (observed, numbered);
    GET  .../milestones/<n>   -> read one back (404 when unknown).
    """

    def __init__(self, existing: list[dict]):
        self.milestones = [dict(item) for item in existing]
        self.created: list[dict] = []
        self._next_number = 41

    def handle(self, method: str, url: str, body: dict | None):
        path = urllib.parse.urlparse(url).path
        match = re.search(r"/milestones/(\d+)$", path)
        if match:
            return self._read_one(int(match.group(1)))
        if not path.endswith("/milestones"):
            raise AssertionError(f"unexpected GitHub API call: {method} {url}")
        if method == "GET":
            return self.milestones
        if method == "POST":
            created = {
                "number": self._next_number,
                "state": "open",
                "title": str((body or {}).get("title", "")),
            }
            self._next_number += 1
            self.milestones.append(created)
            self.created.append(created)
            return created
        raise AssertionError(f"unexpected GitHub API call: {method} {url}")

    def _read_one(self, number: int) -> dict:
        for item in self.milestones:
            if item["number"] == number:
                return item
        raise _http_error(f"milestone {number}", 404, "Not Found")


# -- TLS: every HTTPS call point carries the explicit certifi context (1r.4.1) --


# AC-FR0330-01@v0.10 TRACKS-TRACE IF-TLS-001 backend GET (_urlopen funnel)
def test_backend_get_sends_explicit_certifi_ssl_context(monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_REPO", _REPO)
    opener = _install(
        monkeypatch,
        lambda method, url, body: _ISSUE_PAYLOAD,
    )
    backend = github.GithubBackend(Path("."), "v0.10")
    host_issue = backend.fetch_issue(5)
    assert host_issue.number == 5, "the scripted read must decode (behavior)"
    assert opener.calls, "fetch_issue must perform a transport call"
    for call in opener.calls:
        _assert_explicit_tls_context(call, Path(certifi.where()))


# AC-FR0330-01@v0.10 TRACKS-TRACE IF-TLS-001 module readback (_get_any funnel)
def test_module_readback_sends_explicit_certifi_ssl_context(monkeypatch):
    opener = _install(
        monkeypatch,
        lambda method, url, body: _ISSUE_PAYLOAD,
    )
    mapping = github.readback_issue(_REPO, 5)
    assert mapping["issue_number"] == 5, "the scripted readback must decode"
    assert mapping["api_verified"] is True
    assert opener.calls, "readback_issue must perform a transport call"
    for call in opener.calls:
        _assert_explicit_tls_context(call, Path(certifi.where()))


# AC-FR0330-01@v0.10 TRACKS-TRACE IF-TLS-001 milestone close (_api_json funnel)
def test_milestone_close_sends_explicit_certifi_ssl_context(monkeypatch):
    opener = _install(
        monkeypatch,
        lambda method, url, body: _MILESTONE_CLOSED,
    )
    result = github.close_project_milestone(_REPO, "proj", "7")
    assert result["state"] == "closed", "the scripted close must decode"
    assert result["api_verified"] is True
    assert opener.calls, "close_project_milestone must perform transport calls"
    for call in opener.calls:
        _assert_explicit_tls_context(call, Path(certifi.where()))


# AC-FR0330-01@v0.10 TRACKS-TRACE IF-TLS-001 TRAC_GITHUB_CA_BUNDLE overrides
def test_ca_bundle_override_replaces_certifi_default(monkeypatch, tmp_path):
    override = _single_cert_bundle(tmp_path)
    monkeypatch.setenv("TRAC_GITHUB_CA_BUNDLE", str(override))
    opener = _install(
        monkeypatch,
        lambda method, url, body: _ISSUE_PAYLOAD,
    )
    mapping = github.readback_issue(_REPO, 5)
    assert mapping["issue_number"] == 5
    assert opener.calls, "readback_issue must perform a transport call"
    for call in opener.calls:
        _assert_explicit_tls_context(call, override)


# -- classified missing_token guidance points at the ops prereq (1r.4.2) ------


# AC-FR0330-02@v0.10 TRACKS-TRACE IF-TLS-001 missing_token points to ops section
def test_missing_token_errors_point_to_ops_prereq_section(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    attempts = (
        ("select_issue_backend", lambda: github.select_issue_backend(Path("."), "v0.10")),
        ("readback_issue", lambda: github.readback_issue(_REPO, 5)),
    )
    for face, attempt in attempts:
        with pytest.raises(github.GithubIssuesError) as raised:
            attempt()
        assert raised.value.classification == "missing_token", (
            f"{face} must keep the classified missing_token error (1r.4.2)"
        )
        assert _OPS_PREREQ_DOC in str(raised.value), (
            f"{face}'s missing_token message must point operators at the ops "
            f"prerequisite section ({_OPS_PREREQ_DOC}, interfaces 1r.4.2 / "
            "AC-FR0330-02) so the live-journey environment is fixable"
        )


# -- ensure_project_milestone (interfaces 1r.5.1, IF-TRACKER-001 github face) --


# AC-FR0331-01@v0.10 TRACKS-TRACE IF-TLS-001 ensure reuses an exact title match
def test_ensure_milestone_reuses_exact_title_match(monkeypatch):
    stand_in = _MilestonesApi(
        [
            {"number": 7, "title": _TITLE, "state": "open"},
            {"number": 8, "title": f"other {_TITLE} suffix", "state": "open"},
        ]
    )
    opener = _install(monkeypatch, stand_in.handle)
    result = _ensure_fn()(_REPO, "proj", _TITLE)
    assert result.get("created") is False, (
        "a remote milestone with the exact declared title must be reused, "
        "not re-created (interfaces 1r.5.1)"
    )
    assert result.get("api_verified") is True, (
        "reuse of an API-observed milestone is verified (interfaces 1r.5.1)"
    )
    assert result.get("number") == 7, "the matched milestone number is returned"
    assert result.get("milestone") == _TITLE, "the matched title is returned"
    assert result.get("state") == "open", "the observed state is returned"
    assert not stand_in.created, (
        "the substring decoy must not match: only an exact title match "
        "reuses, and reuse never POSTs (interfaces 1r.5.1)"
    )
    assert all(call.method == "GET" for call in opener.calls), (
        "the reuse path is read-only (interfaces 1r.5.1)"
    )


# AC-FR0331-01@v0.10 TRACKS-TRACE IF-TLS-001 ensure creates then reads back
def test_ensure_milestone_creates_then_reads_back(monkeypatch):
    stand_in = _MilestonesApi([])
    opener = _install(monkeypatch, stand_in.handle)
    result = _ensure_fn()(_REPO, "proj", _TITLE)
    assert result.get("created") is True, (
        "a missing milestone with credentials present must be created "
        "(interfaces 1r.5.1)"
    )
    assert result.get("api_verified") is True, (
        "create is only reported verified after the API readback "
        "(interfaces 1r.5.1)"
    )
    assert result.get("number") == 41, "the created milestone number is returned"
    assert result.get("milestone") == _TITLE
    assert result.get("state") == "open"
    posts = [call for call in opener.calls if call.method == "POST"]
    assert len(posts) == 1, "exactly one create is issued (no duplicate build)"
    post_index = opener.calls.index(posts[0])
    gets_after = [call for call in opener.calls[post_index + 1 :] if call.method == "GET"]
    assert gets_after, (
        "the created milestone must be read back over the API before "
        "api_verified is claimed (interfaces 1r.5.1)"
    )


# AC-FR0331-01@v0.10 TRACKS-TRACE IF-TLS-001 ensure failure stays classified
def test_ensure_milestone_create_failure_returns_classified_error(monkeypatch):
    posts: list[str] = []

    def rejecting(method: str, url: str, body: dict | None):
        path = urllib.parse.urlparse(url).path
        if method == "POST" and path.endswith("/milestones"):
            posts.append(url)
            raise _http_error(url, 401, "Unauthorized")
        return []

    opener = _install(monkeypatch, rejecting)
    result = _ensure_fn()(_REPO, "proj", _TITLE)
    assert isinstance(result, dict), "failures return a classified error result"
    assert not result.get("api_verified"), (
        "any failure must never claim api_verified (interfaces 1r.5.1)"
    )
    assert "auth" in str(result.get("error") or ""), (
        "the returned error carries the transport classification "
        "(interfaces 1r.5.1: 'auth' for the HTTP 401 create rejection)"
    )
    assert len(posts) == 1, "the create attempt was actually driven"
    assert opener.calls


# AC-FR0331-01@v0.10 TRACKS-TRACE IF-TLS-001 ensure without credentials
def test_ensure_milestone_without_credentials_reports_classified_error(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = _ensure_fn()(_REPO, "proj", _TITLE)
    assert isinstance(result, dict), (
        "interfaces 1r.5.1 lists missing credentials among the failures "
        "that return a classified error result"
    )
    assert not result.get("api_verified"), (
        "no credentials means nothing may claim api_verified (1r.5.1)"
    )
    assert "missing_token" in str(result.get("error") or ""), (
        "the uncredentialed failure carries the missing_token "
        "classification so callers can surface actionable guidance"
    )
