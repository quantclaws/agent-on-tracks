"""Hotfix precheck retry-open journey on the REAL backend (IF-HOTFIX-011).

Live-channel sibling of
``tests/integration/test_hotfix_precheck_classification.py`` sub-case (d):
the repaired fetch channel lets the same precheck pass and the hotfix
journey proceeds through the REAL ``opencode`` agent backend (this
directory's autouse override; see conftest). GitHub itself is still the
loopback stand-in — the live surface under test is the agent dispatch
channel, not the issue fetch.

Moved here 2026-10-02 (test-compression P0-1): the test-level
``TRAC_AGENT_BACKEND=opencode`` override inside the default integration
battery put a real-LLM journey on every run_tests/commit_taskgraph
selection; with a flaky upstream the infra backoff ladder plus the
battery's missing subprocess timeout hung layers for 1-2h. The
integration file keeps the deterministic fake-backend variant of the same
retry-open assertion; only the real-channel journey lives here.
"""

from __future__ import annotations

import pytest

from tests._support.github_api_standin import GithubApiStandIn
from tests._support.hotfix_support import seed_host_issues, seed_v05_approved_baseline

# `trac` / `host_repo` come from this directory's conftest (the live
# fixture re-exports) — no direct fixtures import needed here.

_REPO_ID = "acme/host"

pytestmark = pytest.mark.e2e_live


# AC-FR0329-02@v0.10 TRACKS-TRACE (live channel): retry path stays open on
# the real backend — repairing the fetch channel lets the journey proceed.
def test_fetch_channel_repaired_journey_proceeds_real_backend(trac, host_repo, monkeypatch, live_enabled):
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)
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
        standin.issue_status = None  # the channel is repaired
        r = trac("hotfix", "42", "--scenario", "post-release")
        assert r.returncode == 0, r.stderr
        assert "triage.prechecked" in r.stdout
        assert "REJECTED" not in r.stdout
    finally:
        standin.close()
