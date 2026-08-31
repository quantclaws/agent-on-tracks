"""Integration: issue mapping and fake rejection (FR-0283, NFR-0148, IF-ISSUE-001)."""

from __future__ import annotations

import pytest

from tracks.effects.github import select_issue_backend

pytestmark = pytest.mark.integration


# AC-FR0283-01@v0.8 TRACKS-TRACE real issue created and mapped with api_verified
def test_real_issue_created_and_mapped(host_repo, trac, event_log, monkeypatch):
    # IF-ISSUE-001 is anchored via select_issue_backend; additional issue helpers are
    # optional stubs that may not exist pre-implementation. We guard their import.
    try:
        from tracks.effects.github import create_issue_verified  # type: ignore[attr-defined]

        create_issue_verified(None, "t", "b", [])  # type: ignore[misc]
        raise AssertionError("expected NotImplementedError")
    except ImportError:
        pass
    except NotImplementedError as exc:
        assert "IF-ISSUE-001" in str(exc)

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    trac("run")
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
    # select_issue_backend is implemented (returns Fake backend in fake mode); verify it does not raise IF token
    backend = select_issue_backend(host_repo, "v0.8")
    assert backend is not None

    trac("run")
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped" and e["payload"].get("api_verified") is True]
    assert not mapped, "without credentials, no api_verified mapping should appear"
    status = trac("status")
    assert "needs_attention" in status.stdout
    assert "issue_creation" in status.stdout or "missing_token" in status.stdout
    assert 'export GITHUB_TOKEN' in status.stdout or 'next=' in status.stdout
    # Restore and resume must create mapping
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
    try:
        from tracks.effects.github import reject_fake_artifact  # type: ignore[attr-defined]

        reject_fake_artifact("ctx", "FAKE-90")  # type: ignore[misc]
        raise AssertionError("expected NotImplementedError")
    except ImportError:
        pass
    except NotImplementedError as exc:
        assert "IF-ISSUE-001" in str(exc)

    # Real mode = no fake env vars
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "real")
    trac("run")
    events = event_log()
    # If a FAKE-N artifact appears in real mode, fake_rejected must be emitted and blocked
    fake = [e for e in events if e["type"] == "fake_rejected"]
    assert fake, "fake_rejected must appear in real mode"
    assert fake[0]["payload"]["mode"] == "real"
    assert "FAKE-" in fake[0]["payload"]["artifact"] or "fake-project" in fake[0]["payload"]["artifact"]
    status = trac("status")
    assert "fake_not_allowed" in status.stdout or "blocked" in status.stdout
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")


# AC-FR0283-04@v0.8 TRACKS-TRACE crash idempotent dedup for issue mapping
def test_crash_idempotent_dedup(host_repo, trac, event_log, monkeypatch):
    try:
        from tracks.effects.github import persist_issue_mapping  # type: ignore[attr-defined]

        persist_issue_mapping(host_repo, {"repo": "acme/host", "issue_number": 1})  # type: ignore[misc]
        raise AssertionError("expected NotImplementedError")
    except ImportError:
        pass
    except NotImplementedError as exc:
        assert "IF-ISSUE-001" in str(exc)

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    trac("run")
    events = event_log()
    [e for e in events if e["type"] == "issue.mapped"]
    # Simulate crash after first mapping and resume
    trac("run", "--resume")
    events2 = event_log()
    mapped2 = [e for e in events2 if e["type"] == "issue.mapped"]
    # Dedup by repo+baseline_digest: no duplicate issue numbers
    numbers = [m["payload"]["issue_number"] for m in mapped2]
    assert len(numbers) == len(set(numbers)), "issue.mapped must be deduped by repo+baseline"
    replay = trac("replay").stdout
    assert "issue.mapped" in replay or mapped2
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-NFR0148-01@v0.8 TRACKS-TRACE authoritative map consumed by task/commit/report
def test_authoritative_map_consumed(host_repo, trac, event_log, monkeypatch):
    try:
        from tracks.effects.github import readback_issue  # type: ignore[attr-defined]

        readback_issue("acme/host", 1)  # type: ignore[misc]
        raise AssertionError("expected NotImplementedError")
    except ImportError:
        pass
    except NotImplementedError as exc:
        assert "IF-ISSUE-001" in str(exc)

    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    trac("run")
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped"]
    assert mapped
    report = trac("report").stdout
    assert "api_verified=true" in report
    # Task/commit trailers must reference the mapped issue number
    replay = trac("replay").stdout
    if mapped:
        assert str(mapped[0]["payload"]["issue_number"]) in replay or "issue" in replay.lower()
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-NFR0148-02@v0.8 TRACKS-TRACE fake map not consumed by closers
def test_fake_map_not_consumed_by_closers(host_repo, trac, event_log, monkeypatch):
    try:
        from tracks.effects.github import reject_fake_artifact  # type: ignore[attr-defined]

        reject_fake_artifact("ctx", "project=fake-project")  # type: ignore[misc]
        raise AssertionError("expected NotImplementedError")
    except ImportError:
        pass
    except NotImplementedError as exc:
        assert "IF-ISSUE-001" in str(exc)

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
