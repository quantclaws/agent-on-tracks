"""Integration: issue/project/milestone close (FR-0284, IF-ISSUE-002).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The M-MILESTONE close/seal producers
are wired by later runtime tasks (kernel/release routing T-039 + executor
handler T-001-era); until then the event-level assertions are legal Red
against that product gap.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked

pytestmark = pytest.mark.integration


# AC-FR0284-01@v0.8 TRACKS-TRACE close with trace comment and sealed refs clean
def test_close_with_trace_comment(host_repo, trac, event_log, monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    walk_to_m_impl_parked(trac)
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    # M-MILESTONE close must produce issue.closed with trace linkage
    closed = [e for e in events if e["type"] == "issue.closed"]
    assert closed, "issue.closed must appear in M-MILESTONE"
    for c in closed:
        p = c["payload"]
        for k in ("candidate_sha", "trace_digest", "comment_ref", "state"):
            assert k in p, f"issue.closed missing {k}"
        assert p["state"] == "closed"
        # Comment must reference trace
        assert p["comment_ref"]
    # project/milestone also closed
    assert any(e["type"] == "project.closed" for e in events)
    assert any(e["type"] == "milestone.closed" for e in events)
    # No fake-project
    report = trac("report").stdout
    assert "fake-project" not in report
    # Refs cleaned
    assert any(e["type"] == "refs.cleaned" for e in events)
    refs = subprocess.run(["git", "for-each-ref", "refs/trac/tmp"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert refs.strip() == ""
    # Mutual verification with release.trace
    assert "release.trace" in report.lower() or "trace_digest" in report
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0284-02@v0.8 TRACKS-TRACE fake counterexamples rejected and not sealed
def test_fake_counterexamples_rejected(host_repo, trac, event_log, monkeypatch):
    walk_to_m_impl_parked(trac)
    events = event_log()
    assert events is not None
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    trac("run")
    events2 = event_log()
    rejected = [e for e in events2 if e["type"] == "fake_rejected"]
    assert rejected, "fake_rejected must appear for FAKE corpus"
    assert not any(e["type"] == "milestone.sealed" for e in events2 if "FAKE" in str(e))
    report = trac("report").stdout
    assert "api_verified=false" in report or "fake_rejected" in report
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
