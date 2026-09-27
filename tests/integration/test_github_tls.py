"""Live GitHub channel TLS and environment prerequisites (IF-TLS-001).

The TLS contract is proven by the mandated control experiment
(test-plan §1.4-3): the same self-signed localhost endpoint, served by the
test-CA-signed certificate, must complete its handshake when
``TRAC_GITHUB_CA_BUNDLE`` points at the test bundle and must fail with the
``network`` classification under the default certifi bundle. The ops-doc
half audits the environment-prerequisites section and the missing-token
guidance that points at it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.github_api_standin import GithubApiStandIn

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPS_DOC = _REPO_ROOT / "docs" / "getting-started" / "installation.md"
_OPS_SECTION = "Live GitHub 旅程环境前置"


def _fetch_through_live_channel(standin: GithubApiStandIn, issue_number: int = 42):
    """One GitHub issues read through the real effects-layer request path."""
    from tracks.effects.github import GithubBackend, GithubIssuesError

    backend = GithubBackend(_REPO_ROOT, "v0.10")
    try:
        return backend.fetch_issue(issue_number), None
    except GithubIssuesError as exc:
        return None, exc


# AC-FR0330-01@v0.10 TRACKS-TRACE TLS channel verified by CA-bundle control experiment
def test_tls_channel_uses_certifi_bundle(tmp_path, monkeypatch):
    """AC-FR0330-01: under the explicit test-CA bundle the live-channel
    request completes its TLS handshake against the self-signed endpoint;
    under the default certifi bundle the same certificate is classified as a
    network failure — the request layer proves its explicit CA bundle."""
    standin = GithubApiStandIn(
        issues={
            42: {
                "number": 42,
                "title": "tls probe issue",
                "body": "### 版本\nv0.5\n### 症状\nTLS probe.",
                "state": "open",
                "labels": [{"name": "bug"}],
            }
        },
        tls=True,
    ).start()
    try:
        monkeypatch.setenv("GITHUB_TOKEN", "tls-probe-token")
        monkeypatch.setenv("TRAC_GITHUB_REPO", standin.repo)
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)

        # control arm: the default certifi bundle rejects the self-signed
        # certificate (classified network, never a silent downgrade)
        monkeypatch.delenv("TRAC_GITHUB_CA_BUNDLE", raising=False)
        issue, error = _fetch_through_live_channel(standin)
        assert issue is None and error is not None, (
            "the default bundle must not trust the test-CA certificate"
        )
        assert error.classification == "network", (
            f"the TLS failure must classify as network: {error!r}"
        )

        # experiment arm: the explicit test-CA bundle completes the handshake
        monkeypatch.setenv("TRAC_GITHUB_CA_BUNDLE", standin.ca_bundle)
        issue, error = _fetch_through_live_channel(standin)
        assert error is None, (
            f"the CA-bundle override must complete the handshake: {error!r}"
        )
        assert issue is not None and issue.number == 42
        assert issue.title == "tls probe issue"
    finally:
        standin.close()


# AC-FR0330-02@v0.10 TRACKS-TRACE ops doc section and missing-token guidance
def test_ops_doc_section_and_missing_token_guidance(monkeypatch):
    """AC-FR0330-02: the ops document carries the live-journey environment
    prerequisites section (token, repo, TLS/CA bundle) and a missing
    credential fails with the classified, doc-pointing guidance."""
    from tracks.effects.github import GithubIssuesError, select_issue_backend

    text = _OPS_DOC.read_text(encoding="utf-8")
    assert _OPS_SECTION in text, (
        f"{_OPS_DOC.name} must carry the {_OPS_SECTION} section"
    )
    for prerequisite in ("GITHUB_TOKEN", "TRAC_GITHUB_REPO", "TRAC_GITHUB_CA_BUNDLE"):
        assert prerequisite in text, (
            f"the ops section must document {prerequisite}"
        )

    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_CA_BUNDLE", raising=False)
    try:
        select_issue_backend(_REPO_ROOT, "v0.10")
    except GithubIssuesError as exc:
        assert exc.classification == "missing_token"
        message = str(exc)
        assert _OPS_DOC.name in message or _OPS_SECTION in message, (
            f"the missing-token failure must point at the ops guidance: {message!r}"
        )
    else:
        raise AssertionError("a missing GITHUB_TOKEN must fail closed, not fall back")
