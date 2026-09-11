"""Integration: issue/project/milestone close (FR-0284, IF-ISSUE-002).

Drives the real release chain over the loopback CI stand-in and a bare remote.
Authoritative mappings (real create + API readback) close for real: comment +
PATCH + readback through the stand-in; FAKE/known-issue identities are audited
as ``state=skipped reason=not_authoritative`` and never claimed closed. The
pre-wiring stubs (dict-only close, hardcoded clean) are superseded by these
stronger effect assertions.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import (
    assert_temp_refs_empty,
    generate_report_md,
    real_issue_hook,
    skipped_issue_closes,
    walk_and_release,
)

pytestmark = pytest.mark.integration


# AC-FR0284-01@v0.8 TRACKS-TRACE close with trace comment and sealed refs clean
def test_close_with_trace_comment(host_repo, trac, event_log, ci_echo_standin):
    run_id = walk_and_release(
        trac, host_repo, post_approve_hook=real_issue_hook(host_repo)
    )
    assert trac("run").returncode == 0, "publish + M-MILESTONE must complete"
    events = event_log(run_id)
    trace = next(
        e["payload"]
        for e in reversed(events)
        if e["type"] == "milestone.trace_closed"
    )

    # M-MILESTONE close really closes the authoritative mapping (API readback).
    closed = [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "closed"
    ]
    assert closed, "the authoritative mapping must close"
    for entry in closed:
        for key in ("candidate_sha", "trace_digest", "comment_ref", "state"):
            assert key in entry, f"issue.closed missing {key}"
        assert entry["state"] == "closed"
        assert entry["remote_state"] == "closed"
        assert entry["api_verified"] is True
        assert entry["comment_ref"]
        assert entry["trace_digest"] == trace["trace_digest"]
        # Comment must reference the release trace identity.
        assert trace["candidate_sha"] in entry["comment"]
        assert trace["preview_digest"] in entry["comment"]
        assert trace["release_tag"] in entry["comment"]

    # Known-issue registrations without an authoritative mapping stay skipped.
    skipped = skipped_issue_closes(events)
    assert {100, 101} <= {s["issue_number"] for s in skipped}
    assert all(s["reason"] == "not_authoritative" for s in skipped)
    assert not [
        s for s in skipped if str(s["issue_number"]).startswith("FAKE-")
    ], "the real channel must not persist FAKE creations"

    # project/milestone also closed (audited).
    assert any(e["type"] == "project.closed" for e in events)
    assert any(e["type"] == "milestone.closed" for e in events)

    # Refs cleaned for real.
    assert any(e["type"] == "refs.cleaned" for e in events)
    assert_temp_refs_empty(host_repo)

    report = generate_report_md(trac, host_repo)
    # No fake-project anywhere: the real channel never selects the stand-in.
    assert "fake-project" not in report
    # Mutual verification with release.trace: map rows verified, comment tokens.
    assert "Issue map" in report
    assert "api_verified=True" in report
    issue_number = closed[0]["issue_number"]
    assert f"`#{issue_number}`" in report
    assert trace["trace_digest"] in report


# AC-FR0284-02@v0.8 TRACKS-TRACE fake counterexamples rejected and not sealed
def test_fake_counterexamples_rejected(host_repo, trac, event_log, ci_echo_standin):
    walk_and_release(trac, host_repo)
    assert trac("run").returncode == 0
    events = event_log()

    # The v0.7-style FAKE corpus (explicit fake channel) never verifies.
    mapped = [e["payload"] for e in events if e["type"] == "issue.mapped"]
    assert mapped, "the fake channel still maps its identities for audit"
    assert all(m["api_verified"] is False for m in mapped)
    assert all(m["authoritative"] is False for m in mapped)

    # Those non-authoritative identities are never claimed closed/sealed.
    assert not [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "closed"
    ]
    skipped = skipped_issue_closes(events)
    assert skipped, "FAKE corpus must be audited as skipped"
    assert all(s["reason"] == "not_authoritative" for s in skipped)
    assert {100, 101} <= {s["issue_number"] for s in skipped}
    assert not [
        e
        for e in events
        if e["type"] == "milestone.sealed" and "FAKE" in str(e["payload"])
    ]

    report = generate_report_md(trac, host_repo)
    assert "api_verified=False" in report
    assert "FAKE-" in report
