"""Integration: required CI API readback (FR-0270, IF-VERIFY-004)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


# AC-FR0270-01@v0.8 TRACKS-TRACE api readback binds candidate with four-tuple
def test_api_readback_binds_candidate(host_repo, trac, event_log, monkeypatch):
    # Use explicit stand-in env to satisfy §1.4-5 explicitness
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    # Anchor that the github effects module declares IF-VERIFY-004
    from tracks.effects import github as gh

    assert hasattr(gh, "issue_items")
    try:
        from tracks.executor.m_verify import freeze_candidate

        freeze_candidate(host_repo)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-VERIFY-001" in str(exc) or "IF-VERIFY-004" in str(exc)

    trac("run")
    events = event_log()
    observed = [e for e in events if e["type"] == "ci.run_observed"]
    assert observed, "ci.run_observed must appear with repo/workflow/run_id/head SHA binding"
    payload = observed[0]["payload"]
    for key in ("repo", "workflow", "run_id", "head_sha", "candidate_sha", "api_verified"):
        assert key in payload, f"ci.run_observed missing {key}"
    assert payload["head_sha"] == payload["candidate_sha"]
    assert payload["api_verified"] is True
    assert payload["conclusion"] == "success"
    status = trac("status")
    assert "ci=bound" in status.stdout
    # Teardown explicit env
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0270-02@v0.8 TRACKS-TRACE mismatch missing stale blocks and not referenced
def test_mismatch_missing_stale_blocks(host_repo, trac, event_log, monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    # Simulate mismatch by not aligning head/candidate (harness would inject)
    trac("run")
    events = event_log()
    observed = [e for e in events if e["type"] == "ci.run_observed"]
    # Force a mismatch assertion: if observed, head must equal candidate, so
    # mismatch must be reported as failed status
    if observed:
        for o in observed:
            if o["payload"].get("status") == "failed":
                assert o["payload"]["head_sha"] != o["payload"]["candidate_sha"] or o["payload"].get(
                    "reason"
                ) in ("mismatch", "missing", "stale")
    # Blocked on mismatch/missing/stale
    status = trac("status")
    assert "ci=mismatch" in status.stdout or "ci=missing" in status.stdout or "ci=stale" in status.stdout or "blocked" in status.stdout
    # Mismatched evidence must not be referenced by preview
    preview_events = [e for e in events if e["type"] == "release.previewed"]
    for prev in preview_events:
        assert prev["payload"].get("candidate_sha") not in [
            o["payload"].get("candidate_sha") for o in observed if o["payload"].get("status") == "failed"
        ]
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


# AC-FR0270-03@v0.8 TRACKS-TRACE missing credentials needs_attention with resume
def test_missing_credentials_needs_attention(host_repo, trac, event_log, monkeypatch):
    # Ensure real-mode (no fake env) and no token
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")

    trac("run")
    events = event_log()
    # Must not have a successful ci.run_observed
    successful = [e for e in events if e["type"] == "ci.run_observed" and e["payload"].get("api_verified") is True]
    assert not successful, "without credentials, no successful ci.run_observed should appear"
    status = trac("status")
    assert "needs_attention" in status.stdout
    assert "missing_token" in status.stdout or "network_error" in status.stdout
    assert "next=" in status.stdout or "GITHUB_TOKEN" in status.stdout
    attention = [e for e in events if e["type"] == "attention.required"]
    if attention:
        assert any(a["payload"].get("area") == "ci_readback" for a in attention)
        assert any(a["payload"].get("next") for a in attention)
    # After restoring credentials, resume must succeed
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    trac("run", "--resume")
    events2 = event_log()
    # Teardown explicit env before assertion to avoid leakage
    monkeypatch.delenv("TRAC_GITHUB_API_BASE", raising=False)
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    bound = [e for e in events2 if e["type"] == "ci.run_observed" and e["payload"].get("api_verified") is True]
    assert bound, "after credential restore, ci.run_observed with api_verified must appear"
    status2 = trac("status")
    assert "ci=bound" in status2.stdout
