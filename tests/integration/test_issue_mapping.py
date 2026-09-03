"""Integration: issue mapping and fake rejection (FR-0283, NFR-0148, IF-ISSUE-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The issue-mapped / fake-rejected
event producers are wired via the github effects boundary; until the runtime
wiring for issue.mapped emission is complete the event-level assertions are
legal Red against that product gap.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.effects.github import (
    FakeIssueBackend,
    GithubIssuesError,
    create_issue_verified,
    persist_issue_mapping,
    readback_issue,
    reject_fake_artifact,
    select_issue_backend,
)

pytestmark = pytest.mark.integration


# AC-FR0283-01@v0.8 TRACKS-TRACE real issue created and mapped with api_verified
def test_real_issue_created_and_mapped(host_repo, trac, event_log, monkeypatch, tmp_path):
    # Module contract (IF-ISSUE-001, unit-pinned): the fake stand-in channel
    # can never verify — creation returns api_verified=False without network.
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    probe = create_issue_verified(FakeIssueBackend(tmp_path, "v0.8"), "t", "b", [])
    assert probe.get("api_verified") is False
    assert str(probe.get("issue_number", "")).startswith("FAKE-")

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    walk_to_m_impl_parked(trac)
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped"]
    assert mapped, "issue.mapped must appear after real issue creation"
    assert mapped[0]["payload"]["api_verified"] is True
    for key in ("repo", "issue_number", "url", "baseline_digest"):
        assert key in mapped[0]["payload"]
    report = trac("report").stdout
    assert "api_verified=true" in report or "issue_map" in report.lower()
    status = trac("status")
    assert "needs_attention" not in status.stdout or "api_verified=true" in report
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0283-02@v0.8 TRACKS-TRACE missing credentials needs_attention for issue creation
def test_missing_credentials_needs_attention(host_repo, trac, event_log, monkeypatch):
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    backend = select_issue_backend(host_repo, "v0.8")
    assert backend is not None

    walk_to_m_impl_parked(trac)
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped" and e["payload"].get("api_verified") is True]
    assert not mapped, "without credentials, no api_verified mapping should appear"
    status = trac("status")
    assert "needs_attention" in status.stdout
    assert "issue_creation" in status.stdout or "missing_token" in status.stdout
    assert "export GITHUB_TOKEN" in status.stdout or "next=" in status.stdout
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    trac("run", "--resume")
    events2 = event_log()
    mapped2 = [e for e in events2 if e["type"] == "issue.mapped"]
    assert mapped2 and mapped2[0]["payload"]["api_verified"] is True
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0283-03@v0.8 TRACKS-TRACE fake rejected in real mode
def test_fake_rejected_in_real_mode(host_repo, trac, event_log, monkeypatch):
    # Module contract (IF-ISSUE-001, unit-pinned): a FAKE-prefixed artifact is
    # rejected with the closed fake_rejected verdict, never api_verified.
    verdict = reject_fake_artifact("FAKE-90")
    assert verdict.get("reason") == "fake_rejected"
    assert verdict.get("api_verified") is not True

    walk_to_m_impl_parked(trac)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "real")
    trac("run")
    events = event_log()
    fake = [e for e in events if e["type"] == "fake_rejected"]
    assert fake, "fake_rejected must appear in real mode"
    assert fake[0]["payload"]["mode"] == "real"
    assert "FAKE-" in fake[0]["payload"]["artifact"] or "fake-project" in fake[0]["payload"]["artifact"]
    status = trac("status")
    assert "fake_not_allowed" in status.stdout or "blocked" in status.stdout
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")


# AC-FR0283-04@v0.8 TRACKS-TRACE crash idempotent dedup for issue mapping
def test_crash_idempotent_dedup(host_repo, trac, event_log, monkeypatch, tmp_path):
    # Module contract (IF-ISSUE-001, unit-pinned): the authoritative map at
    # .tracks/runtime/issue-map.json dedups a crash-retry re-persist by item.
    stored = persist_issue_mapping(
        tmp_path, "FR-0270", {"issue_number": "FAKE-1", "api_verified": False}
    )
    assert stored["FR-0270"]["issue_number"] == "FAKE-1"
    restated = persist_issue_mapping(
        tmp_path, "FR-0270", {"issue_number": "FAKE-1", "api_verified": False}
    )
    assert list(restated.keys()) == ["FR-0270"]

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    walk_to_m_impl_parked(trac)
    events = event_log()
    [e for e in events if e["type"] == "issue.mapped"]
    trac("run", "--resume")
    events2 = event_log()
    mapped2 = [e for e in events2 if e["type"] == "issue.mapped"]
    numbers = [m["payload"]["issue_number"] for m in mapped2]
    assert len(numbers) == len(set(numbers)), "issue.mapped must be deduped by repo+baseline"
    replay = trac("replay").stdout
    assert "issue.mapped" in replay or mapped2
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-NFR0148-01@v0.8 TRACKS-TRACE authoritative map consumed by task/commit/report
def test_authoritative_map_consumed(host_repo, trac, event_log, monkeypatch):
    # Module contract (IF-ISSUE-001, unit-pinned): without credentials the
    # readback fails closed with the classified missing_token error — never a
    # silent pass. (The stand-in readback itself is exercised by the unit RED;
    # integration observes the event/CLI outlets below.)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError) as excinfo:
        readback_issue("acme/host", 1)
    assert excinfo.value.classification == "missing_token"

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    walk_to_m_impl_parked(trac)
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped"]
    assert mapped
    report = trac("report").stdout
    assert "api_verified=true" in report
    replay = trac("replay").stdout
    if mapped:
        assert str(mapped[0]["payload"]["issue_number"]) in replay or "issue" in replay.lower()
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-NFR0148-02@v0.8 TRACKS-TRACE fake map not consumed by closers
def test_fake_map_not_consumed_by_closers(host_repo, trac, event_log, monkeypatch):
    # Module contract (IF-ISSUE-001, unit-pinned): a fake-project artifact can
    # never claim api_verified, so closers must not consume it.
    verdict = reject_fake_artifact("project=fake-project")
    assert verdict.get("api_verified") is not True

    walk_to_m_impl_parked(trac)
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "1")
    trac("run")
    events = event_log()
    fake = [e for e in events if e["type"] == "fake_rejected"]
    assert fake, "fake_rejected must appear"
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    trac("run")
    events2 = event_log()
    closed = [e for e in events2 if e["type"] == "issue.closed"]
    for c in closed:
        assert "FAKE" not in str(c["payload"].get("issue_number", ""))
    status = trac("status")
    assert "fake_not_allowed" in status.stdout or "blocked" in status.stdout
