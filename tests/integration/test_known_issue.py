"""Integration: Known Issue policy (FR-0286-05/06, IF-KNOWNISSUE-001)."""

from __future__ import annotations

import pytest

from tracks.executor.repair import list_known_issues_for_preview, register_known_issue

pytestmark = pytest.mark.integration


# AC-FR0286-05@v0.8 TRACKS-TRACE known issue registered listed and waived
def test_known_issue_registered_listed_and_waived(host_repo, trac, event_log, monkeypatch):
    try:
        register_known_issue(host_repo, {"kind": "behavior"}, "a" * 40)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-KNOWNISSUE-001" in str(exc)
    try:
        list_known_issues_for_preview("run")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-KNOWNISSUE-001" in str(exc)

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    trac("run")
    events = event_log()
    registered = [e for e in events if e["type"] == "known_issue.registered"]
    assert registered, "known_issue.registered must appear"
    p = registered[0]["payload"]
    assert p["label"] == "known-issue"
    assert "candidate_sha" in p
    preview = trac("release", "preview").stdout
    assert "known_issues" in preview
    release_attempt = trac("release", "--action", "release")
    assert release_attempt.returncode != 0 or "known_issue not listed" in release_attempt.stdout
    report = trac("report").stdout
    assert "waived" in report.lower() or "known-issue" in report.lower()
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0286-06@v0.8 TRACKS-TRACE exclusions mechanism security no hotfix
def test_exclusions_mechanism_security_no_hotfix(host_repo, trac, event_log):
    try:
        register_known_issue(host_repo, {"kind": "mechanism_failure"}, "a" * 40)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-KNOWNISSUE-001" in str(exc)

    trac("run")
    events = event_log()
    rejected = [e for e in events if e["type"] == "known_issue.rejected" and e["payload"].get("reason") == "not_product_defect"]
    assert rejected, "known_issue.rejected must appear for mechanism failure"
    assert "not_product_defect" in rejected[0]["payload"]["reason"]
    status = trac("status").stdout
    assert "blocked: security not passed" in status or "zero known issue" in status or "blocked" in status
    assert not any(e["type"] == "hotfix.requested" for e in events)
    trac("release", "--action", "release")
    assert "blocked: security not passed" in trac("status").stdout or "blocked" in trac("status").stdout
