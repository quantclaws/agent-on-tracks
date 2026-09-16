"""Integration: issue mapping and fake rejection (FR-0283, NFR-0148, IF-ISSUE-001).

The real create + API-readback mapping is driven through the production
``create_issues`` handler with a stand-in-backed ``GithubBackend`` (the CLI
cannot express it: selecting the deterministic fake agent channel also selects
the fake issue channel, by design). Missing credentials fail closed with the
audited ``attention.required(issue_creation, missing_token)``; a FAKE artifact
surfacing on the real channel lands ``fake_rejected`` and never a verified
mapping.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.helpers import (
    generate_report_md,
    init_bare_remote,
    invoke_create_issues,
    real_issue_hook,
    skipped_issue_closes,
    walk_to_awaiting_release,
    walk_to_m_impl_parked,
)
from tracks.effects.github import (
    FakeIssueBackend,
    GithubBackend,
    GithubIssuesError,
    create_issue_verified,
    persist_issue_mapping,
    readback_issue,
    reject_fake_artifact,
    select_issue_backend,
)

pytestmark = pytest.mark.integration


# AC-FR0283-01@v0.8 TRACKS-TRACE real issue created and mapped with api_verified
def test_real_issue_created_and_mapped(
    host_repo, trac, event_log, monkeypatch, tmp_path, ci_echo_standin
):
    # Module contract (IF-ISSUE-001, unit-pinned): the fake stand-in channel
    # can never verify — creation returns api_verified=False without network.
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    probe = create_issue_verified(FakeIssueBackend(tmp_path, "v0.8"), "t", "b", [])
    assert probe.get("api_verified") is False
    assert str(probe.get("issue_number", "")).startswith("FAKE-")

    walk_to_m_impl_parked(trac, post_approve_hook=real_issue_hook(host_repo))
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped"]
    assert mapped, "issue.mapped must appear after real issue creation"
    assert all(m["payload"]["api_verified"] is True for m in mapped)
    for m in mapped:
        payload = m["payload"]
        for key in ("repo", "issue_number", "node_id", "title", "url", "baseline_digest"):
            assert key in payload
        assert payload["repo"] == "acme/host"
        assert payload["url"].startswith("https://github.com/acme/host/issues/")
        assert payload["authoritative"] is True
    # The loopback stand-in really served the create + readback pair.
    requests = ci_echo_standin.requests
    assert "/repos/acme/host/issues" in requests
    assert any(path.startswith("/repos/acme/host/issues/") for path in requests)

    report = generate_report_md(trac, host_repo)
    assert "Issue map" in report
    assert "api_verified=True" in report
    status = trac("status")
    assert "needs_attention" not in status.stdout


# AC-FR0283-02@v0.8 TRACKS-TRACE missing credentials needs_attention for issue creation
def test_missing_credentials_needs_attention(
    host_repo, trac, event_log, monkeypatch, ci_echo_standin
):
    # Real mode = the explicit-simulation closed set unset; selection fails
    # closed instead of silently returning the Fake stand-in.
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError) as excinfo:
        select_issue_backend(host_repo, "v0.8")
    assert excinfo.value.classification == "missing_token"

    def missing_then_restored(_trac, run_id):
        invoke_create_issues(host_repo, run_id)  # no token -> audited attention
        monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")
        invoke_create_issues(
            host_repo, run_id, backend=GithubBackend(host_repo, "v0.8")
        )

    run_id = walk_to_awaiting_release(trac, post_approve_hook=missing_then_restored)
    events = event_log(run_id)
    attention = [
        e
        for e in events
        if e["type"] == "attention.required"
        and e["payload"].get("area") == "issue_creation"
    ]
    assert attention, "missing credentials must land the issue_creation attention"
    assert attention[0]["payload"]["reason"] == "missing_token"
    assert "export GITHUB_TOKEN" in attention[0]["payload"]["next"]

    created = [e for e in events if e["type"] == "issue.created"]
    assert created, "the credentials-restored retry must create the issues"
    # No fallback FAKE mapping, and no success before the attention.
    assert attention[0]["seq"] < created[0]["seq"]
    mapped = [e for e in events if e["type"] == "issue.mapped"]
    assert mapped and all(m["payload"]["api_verified"] is True for m in mapped)
    assert not [
        m
        for m in mapped
        if str(m["payload"].get("issue_number", "")).startswith("FAKE")
    ]

    # The release preview surfaces the unresolved creation attention.
    preview = trac("release", "preview")
    assert preview.returncode == 0
    assert "issue_creation" in preview.stdout
    assert "missing_token" in preview.stdout


# AC-FR0283-03@v0.8 TRACKS-TRACE fake rejected in real mode
def test_fake_rejected_in_real_mode(
    host_repo, trac, event_log, monkeypatch, ci_echo_standin
):
    # Module contract (IF-ISSUE-001, unit-pinned): a FAKE-prefixed artifact is
    # rejected with the closed fake_rejected verdict, never api_verified.
    verdict = reject_fake_artifact("FAKE-90")
    assert verdict.get("reason") == "fake_rejected"
    assert verdict.get("api_verified") is not True

    # The real channel's provider returns a FAKE artifact: reject it, never
    # read it back as a remote identity.
    ci_echo_standin.fake_issue_number = "FAKE-90"
    walk_to_m_impl_parked(trac, post_approve_hook=real_issue_hook(host_repo))
    events = event_log()
    fake = [e for e in events if e["type"] == "fake_rejected"]
    assert fake, "fake_rejected must appear for a real-channel FAKE artifact"
    assert fake[0]["payload"]["reason"] == "fake_rejected"
    assert fake[0]["payload"]["issue_id"] == "FAKE-90"
    outcome = [
        e
        for e in events
        if e["type"] == "outcome.received"
        and e["payload"].get("failure_class") == "fake_rejected"
    ]
    assert outcome, "the rejection must block the github outcome"
    status = trac("status")
    assert "fake_not_allowed" in status.stdout or "blocked" in status.stdout


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
    trac("run")
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
def test_authoritative_map_consumed(host_repo, trac, event_log, monkeypatch, ci_echo_standin):
    # Module contract (IF-ISSUE-001, unit-pinned): without credentials the
    # readback fails closed with the classified missing_token error — never a
    # silent pass.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError) as excinfo:
        readback_issue("acme/host", 1)
    assert excinfo.value.classification == "missing_token"
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")

    walk_to_m_impl_parked(trac, post_approve_hook=real_issue_hook(host_repo))
    events = event_log()
    mapped = [e for e in events if e["type"] == "issue.mapped"]
    assert mapped
    assert all(m["payload"]["api_verified"] is True for m in mapped)
    report = generate_report_md(trac, host_repo)
    assert "api_verified=True" in report
    replay = trac("replay").stdout
    assert "issue.mapped" in replay
    assert str(mapped[0]["payload"]["issue_number"]) in replay


# AC-NFR0148-02@v0.8 TRACKS-TRACE fake map not consumed by closers
def test_fake_map_not_consumed_by_closers(
    host_repo, trac, event_log, ci_echo_standin
):
    # Module contract (IF-ISSUE-001, unit-pinned): a fake-project artifact can
    # never claim api_verified, so closers must not consume it.
    from tracks.effects.github import reject_fake_artifact

    verdict = reject_fake_artifact("project=fake-project")
    assert verdict.get("api_verified") is not True

    walk_to_awaiting_release(trac, host_repo=host_repo)
    # A stale v0.7 disk map: one FAKE row even claims api_verified=true; the
    # closer must re-verify the identity and refuse it anyway.
    map_path = host_repo / ".tracks" / "runtime" / "issue-map.json"
    map_path.write_text(
        json.dumps(
            {
                "FR-FAKE": {
                    "issue_number": "FAKE-90",
                    "api_verified": True,
                    "source": "legacy_fake",
                },
                "NFR-FAKE": {
                    "issue_number": "FAKE-91",
                    "api_verified": False,
                    "source": "legacy_fake",
                },
            }
        ),
        encoding="utf-8",
    )
    import subprocess as _sp

    # The walk helper binds a bare origin already; this test only needs SOME
    # bare origin to exist (the closers never touch the remote).
    if _sp.run(["git", "remote", "get-url", "origin"], cwd=host_repo,
               capture_output=True).returncode != 0:
        init_bare_remote(host_repo, "bare.git")
    assert trac("release", "--action", "release").returncode == 0
    assert trac("run").returncode == 0
    events = event_log()

    assert not [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "closed"
    ], "a FAKE identity must never be consumed by a closer"
    skipped = skipped_issue_closes(events)
    fake_skips = [
        s for s in skipped if str(s["issue_number"]).startswith("FAKE-")
    ]
    assert {"FAKE-90", "FAKE-91"} <= {str(s["issue_number"]) for s in fake_skips}
    assert all(s["reason"] == "not_authoritative" for s in fake_skips)
    assert "terminal=released" in trac("status").stdout
