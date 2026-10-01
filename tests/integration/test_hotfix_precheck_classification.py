"""Hotfix precheck classification mapping (IF-HOTFIX-011, IF-HOTFIX-003).

Drives the ``trac hotfix`` CLI/event outlets (interfaces §4a #5) over the
deterministic fake corpus and the loopback GitHub stand-in: a confirmed
missing issue keeps the inherited ``issue_not_found`` reason, while every
fetch-failure classification (missing_token / auth / rate_limit / network)
surfaces as ``issue_fetch_failed`` with the classification's actionable
next — never silently as not-found — and the retry path stays open.
"""

from __future__ import annotations

import subprocess

import pytest

from tests._support.github_api_standin import GithubApiStandIn
from tests._support.hotfix_support import seed_host_issues, seed_v05_approved_baseline

pytestmark = pytest.mark.integration

_REPO_ID = "acme/host"


def _issue_events(host_repo, run_id):
    from tracks import paths
    from tracks.store import Store

    store = Store(paths.tracks_home(host_repo))
    try:
        return [
            {"type": e.type, "payload": dict(e.payload or {})}
            for e in store.events(run_id)
            if e.type == "triage.prechecked"
        ]
    finally:
        store.close()


def _latest_run_id(host_repo):
    from tracks import paths
    from tracks.store import Store

    store = Store(paths.tracks_home(host_repo))
    try:
        return store.latest_run()
    finally:
        store.close()


# AC-FR0329-01@v0.10 TRACKS-TRACE missing issue reports not_found with next
def test_missing_issue_reports_not_found_with_next(trac, host_repo):
    """AC-FR0329-01: an issue number absent from the corpus is a confirmed
    miss — the precheck rejects with ``issue_not_found`` and an actionable
    next, exits non-zero, and leaves no fix branch (the inherited reason
    keeps its narrow meaning after FR-0329)."""
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "7777", "--scenario", "post-release")
    assert r.returncode != 0, r.stdout
    output = r.stdout + r.stderr
    assert "REJECTED" in output
    assert "issue_not_found" in output, (
        f"a confirmed miss must report issue_not_found: {output!r}"
    )
    assert "next:" in output, "the rejection must carry an actionable next"
    run_id = _latest_run_id(host_repo)
    rejected = [
        e for e in _issue_events(host_repo, run_id) if e["payload"].get("status") == "rejected"
    ]
    assert rejected and rejected[-1]["payload"]["reason"] == "issue_not_found"
    branch = subprocess.run(
        ["git", "branch", "--list", "fix/7777"],
        cwd=host_repo, capture_output=True, text=True,
    ).stdout
    assert not branch.strip(), "a rejected precheck must not create the fix branch"


# AC-FR0329-02@v0.10 TRACKS-TRACE fetch failures classified with retry open
def test_fetch_failures_classified_with_retry_open(trac, host_repo, monkeypatch):
    """AC-FR0329-02: every fetch-failure classification surfaces as
    ``issue_fetch_failed`` with the classification's actionable next (the
    miss-meaning of ``issue_not_found`` is not overloaded), and repairing the
    channel lets the same precheck pass — the retry path stays open."""
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)
    # the live-issue channel: no fake backend, credentials + stand-in base.
    # The real channel is required for the FETCH-FAILURE sub-cases (a)-(c)
    # below — the fake corpus never exercises credential/HTTP/network
    # classification. Sub-case (d) switches back to the fake backend before
    # the journey proceeds (see there). The real-backend variant of the
    # retry-open journey lives in
    # tests/e2e_live/test_hotfix_precheck_retry_open_live.py
    # (moved there 2026-10-02: letting (d) dispatch on the real LLM channel
    # put the default battery on the infra backoff ladder; with the
    # battery's then-missing subprocess timeout that hung run_tests for
    # 1-2h and reddened CI).
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("GITHUB_TOKEN", "classification-probe-token")
    monkeypatch.setenv("TRAC_GITHUB_REPO", _REPO_ID)

    standin = GithubApiStandIn(
        issues={
            42: {
                "number": 42,
                "title": "crash on event replay after boundary",
                "body": "### 版本\nv0.5\n### 对应 FR/NFR\nFR-0030\n### 症状\nReplay raises.",
                "state": "open",
                "labels": [{"name": "bug"}],
            }
        }
    ).start()
    try:
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)

        # (a) missing credentials: the backend selection itself fails closed
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        r = trac("hotfix", "42", "--scenario", "post-release")
        assert r.returncode != 0, r.stdout
        output = r.stdout + r.stderr
        assert "issue_fetch_failed" in output, (
            f"a missing token must classify as issue_fetch_failed: {output!r}"
        )
        assert "GITHUB_TOKEN" in output, (
            f"the missing_token next must point at the credential: {output!r}"
        )
        monkeypatch.setenv("GITHUB_TOKEN", "classification-probe-token")

        # (b) injected HTTP classifications through the stand-in
        cases = (
            (401, ("token", "权限")),
            (403, ("限额", "reset")),
        )
        for status_code, keywords in cases:
            standin.issue_status = status_code
            r = trac("hotfix", "42", "--scenario", "post-release")
            assert r.returncode != 0, r.stdout
            output = (r.stdout + r.stderr).lower()
            assert "issue_fetch_failed" in output, (
                f"HTTP {status_code} must classify as issue_fetch_failed: {output!r}"
            )
            assert "issue_not_found" not in output, (
                f"HTTP {status_code} must not be reported as a confirmed miss: {output!r}"
            )
            assert any(k.lower() in output for k in keywords), (
                f"HTTP {status_code} next must carry the classification guidance: {output!r}"
            )

        # (c) network failure: an unreachable API base classifies network
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:1")
        r = trac("hotfix", "42", "--scenario", "post-release")
        assert r.returncode != 0, r.stdout
        output = (r.stdout + r.stderr).lower()
        assert "issue_fetch_failed" in output, (
            f"a network failure must classify as issue_fetch_failed: {output!r}"
        )
        assert any(k.lower() in output for k in ("连通", "tls", "ca_bundle")), (
            f"the network next must carry the connectivity/TLS guidance: {output!r}"
        )
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)

        # (d) the retry path stays open: the repaired channel passes. Back
        # to the FAKE backend before the journey proceeds (test-compression
        # P0-1, 2026-10-02): this layer asserts the retry-open semantics,
        # which the fake corpus (seed_host_issues seeded issue 42 above)
        # serves deterministically; the real-channel journey is the
        # e2e_live sibling. Without this switch the default battery rides
        # the real LLM channel into the infra backoff ladder.
        monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
        standin.issue_status = None
        r = trac("hotfix", "42", "--scenario", "post-release")
        assert r.returncode == 0, r.stderr
        assert "triage.prechecked" in r.stdout
        assert "REJECTED" not in r.stdout
    finally:
        standin.close()
