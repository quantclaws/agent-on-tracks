"""T-001 RED (round 6): release/escape CLI faces still missing on the
delivered baseline — (I)-CLI known_issues list rendering in `trac release
preview`, and (G)-CLI publish blocked-state rendering in `trac status`.

Scope: unit-only probes over the tracks/cli/main.py render cores
(_render_preview_line / _release_status_lines), which cmd_release preview and
cmd_status call directly. The executor halves of (G)/(I) are delivered
(publish.blocked / known_issue.* emitters); this file pins the CLI halves the
description declares must-not-drop. (F) issue wiring is transferred to T-042;
(H) release.previewed emission routes via T-039 (release.py, outside this
cycle's allowed paths).

AC: FR-0274 / FR-0287 — TRACKS-TRACE IF-RELEASE-003.
"""

from types import SimpleNamespace


def _ev(seq, event_type, payload):
    return SimpleNamespace(seq=seq, type=event_type, payload=payload)


def _primary():
    return SimpleNamespace(status="active", terminal_state=None)


# -- (I) CLI: release preview renders the known_issues list -------------------


# AC-FR0274@v0.8 TRACKS-TRACE IF-RELEASE-003 known_issues preview render
def test_preview_renders_known_issues_list():
    """`trac release preview` must render the known_issues list: every
    known_issue.registered event contributes a title entry to the preview
    rendering."""
    import tracks.cli.main as cli

    render = getattr(cli, "_render_preview_line", None)
    assert callable(render), (
        "assertion failure: cli must expose the release preview renderer"
    )
    preview = {
        "candidate_sha": "a" * 40,
        "preview_digest": "d" * 64,
        "evidence_digests": {},
    }
    events = [
        _ev(1, "release.previewed", dict(preview)),
        _ev(
            2,
            "known_issue.registered",
            {"title": "flaky ci on windows", "reason": "environment",
             "issue": "123"},
        ),
        _ev(
            3,
            "known_issue.registered",
            {"title": "upstream segfault", "reason": "upstream",
             "issue": None},
        ),
    ]
    out = render(preview, None, events)
    assert "known_issues" in out, (
        "assertion failure: release preview must render the known_issues "
        f"list (FR-0274), got: {out!r}"
    )
    for title in ("flaky ci on windows", "upstream segfault"):
        assert title in out, (
            "assertion failure: the known_issues list must include the "
            f"registered issue {title!r}, got: {out!r}"
        )


# AC-FR0274@v0.8 TRACKS-TRACE IF-RELEASE-003 rejected issues excluded
def test_preview_known_issues_excludes_rejected():
    """Only known_issue.registered contributes entries; known_issue.rejected
    (shield-rejected fake reports) must never be listed as known issues."""
    import tracks.cli.main as cli

    render = getattr(cli, "_render_preview_line", None)
    assert callable(render), (
        "assertion failure: cli must expose the release preview renderer"
    )
    events = [
        _ev(
            1,
            "known_issue.registered",
            {"title": "real known issue", "reason": "env", "issue": "7"},
        ),
        _ev(
            2,
            "known_issue.rejected",
            {"title": "shield-rejected fake", "reason": "fake_report"},
        ),
    ]
    out = render(
        {"candidate_sha": "a" * 40, "preview_digest": "d" * 64,
         "evidence_digests": {}},
        None,
        events,
    )
    assert "known_issues" in out and "real known issue" in out, (
        "assertion failure: preview must render the registered known_issues "
        f"list, got: {out!r}"
    )
    assert "shield-rejected fake" not in out, (
        "assertion failure: known_issue.rejected must never be listed as a "
        f"known issue, got: {out!r}"
    )


# -- (G) CLI: status renders the publish blocked state ------------------------


# AC-FR0287@v0.8 TRACKS-TRACE IF-RELEASE-003 status blocked render
def test_status_renders_publish_blocked_state():
    """`trac status` must render the publish blocked state: a publish.blocked
    event (e.g. agent_forbidden) surfaces as a blocked status line carrying
    its reason."""
    import tracks.cli.main as cli

    lines = getattr(cli, "_release_status_lines", None)
    assert callable(lines), (
        "assertion failure: cli must expose the release status renderer"
    )
    events = [
        _ev(
            1,
            "release.previewed",
            {"candidate_sha": "a" * 40, "preview_digest": "d" * 64},
        ),
        _ev(
            2,
            "publish.planned",
            {"preview_digest": "d" * 64, "candidate_sha": "a" * 40},
        ),
        _ev(
            3,
            "publish.blocked",
            {"reason": "agent_forbidden", "preview_digest": "d" * 64},
        ),
    ]
    out = lines(events, _primary())
    blocked = [ln for ln in out if "publish" in ln and "blocked" in ln]
    assert blocked, (
        "assertion failure: status must render the publish blocked state, "
        f"got: {out!r}"
    )
    assert any("agent_forbidden" in ln for ln in blocked), (
        "assertion failure: the blocked status line must carry the block "
        f"reason (agent_forbidden), got: {blocked!r}"
    )


# AC-FR0287@v0.8 TRACKS-TRACE IF-RELEASE-003 no false blocked
def test_status_publish_done_not_blocked():
    """A completed publish must not render a blocked state (fail-closed
    honesty: blocked is reported only from publish.blocked evidence)."""
    import tracks.cli.main as cli

    lines = getattr(cli, "_release_status_lines", None)
    assert callable(lines), (
        "assertion failure: cli must expose the release status renderer"
    )
    events = [
        _ev(
            1,
            "publish.executed",
            {"status": "done", "operation_kind": "tag", "target": "v0.8.0"},
        ),
    ]
    out = lines(events, _primary())
    assert not [ln for ln in out if "blocked" in ln], (
        "assertion failure: a done publish must not render a blocked state, "
        f"got: {out!r}"
    )
