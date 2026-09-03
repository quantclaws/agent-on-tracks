"""T-001 RED round 6 (attempt 2): end-to-end CLI probes for the two faces
pinned at render-core level by test_t001_release_cli_faces_red.py —

- (I)-CLI: `trac release preview` renders the known_issues list from
  known_issue.registered events (rejected fakes excluded) — must-not-drop
  escalation face (attempt-2 known_issue escalation).
- (G)-CLI: `trac status` renders the publish blocked state with its reason
  (publish.blocked, e.g. agent_forbidden) — must-not-drop escalation face
  (attempt-2 T-021 escalation, "在 status 渲染 blocked 状态").

The render cores exist and are live CLI paths (cmd_release preview prints
_render_preview_line; cmd_status prints _release_status_lines); these tests
drive the real cmd_release/cmd_status entrypoints over a real Store so the
behavior token is pinned at the CLI boundary, not only the helper.

AC: FR-0274 / FR-0287 — TRACKS-TRACE IF-RELEASE-003. RED discipline: only
unit tests are added; targets fail with assertion_failure on the contract
token (zero assembly errors); the no-false-blocked guard intentionally passes
on the pre-fix baseline.
"""

from __future__ import annotations

from pathlib import Path

from tracks.store import Store

_CANDIDATE = "2f6c3a1d" * 8
_PREVIEW = "sha256:" + "7e1b" * 16


def _setup(tmp_path) -> tuple[Store, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo / ".tracks")
    return store, repo, "RUN"


def _seed_preview(store: Store, run_id: str) -> None:
    store.append(run_id, "v0.8", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.8", "stage.entered", {"stage": "M-RELEASE"})
    store.append(
        run_id,
        "v0.8",
        "release.previewed",
        {
            "candidate_sha": _CANDIDATE,
            "preview_digest": _PREVIEW,
            "evidence_digests": {},
        },
    )


# -- (I) CLI e2e: release preview renders the known_issues list ---------------


# AC-FR0274@v0.8 TRACKS-TRACE IF-RELEASE-003 known_issues preview e2e
def test_cmd_release_preview_renders_known_issues_e2e(tmp_path, capsys):
    """`trac release preview` over a real Store prints the known_issues list:
    every known_issue.registered title appears; known_issue.rejected fakes
    never do."""
    from tracks.cli.main import cmd_release

    store, repo, run_id = _setup(tmp_path)
    _seed_preview(store, run_id)
    store.append(
        run_id, "v0.8", "known_issue.registered",
        {"title": "flaky ci on windows", "reason": "environment", "issue": "123"},
    )
    store.append(
        run_id, "v0.8", "known_issue.registered",
        {"title": "upstream segfault", "reason": "upstream", "issue": None},
    )
    store.append(
        run_id, "v0.8", "known_issue.rejected",
        {"title": "shield-rejected fake", "reason": "fake_report"},
    )
    rc = cmd_release(repo, "preview")
    out = capsys.readouterr().out
    assert rc == 0, (
        f"assertion failure: release preview must succeed, rc={rc}"
    )
    assert "known_issues" in out, (
        "assertion failure: release preview output must render the "
        f"known_issues list (FR-0274), got: {out!r}"
    )
    for title in ("flaky ci on windows", "upstream segfault"):
        assert title in out, (
            "assertion failure: the rendered known_issues list must include "
            f"the registered issue {title!r}, got: {out!r}"
        )
    assert "shield-rejected fake" not in out, (
        "assertion failure: known_issue.rejected must never be listed as a "
        f"known issue, got: {out!r}"
    )


# -- (G) CLI e2e: status renders the publish blocked state ---------------------


# AC-FR0287@v0.8 TRACKS-TRACE IF-RELEASE-003 status blocked e2e
def test_cmd_status_renders_publish_blocked_e2e(tmp_path, capsys):
    """`trac status` over a real Store prints a blocked line for a
    publish.blocked event carrying the block reason (agent_forbidden)."""
    from tracks.cli.main import cmd_status

    store, repo, run_id = _setup(tmp_path)
    _seed_preview(store, run_id)
    store.append(
        run_id, "v0.8", "publish.planned",
        {"preview_digest": _PREVIEW, "candidate_sha": _CANDIDATE},
    )
    store.append(
        run_id, "v0.8", "publish.blocked",
        {"reason": "agent_forbidden", "preview_digest": _PREVIEW},
    )
    rc = cmd_status(repo)
    out = capsys.readouterr().out
    assert rc == 0, (
        f"assertion failure: status must succeed, rc={rc}"
    )
    blocked = [
        ln for ln in out.splitlines()
        if "publish" in ln and "blocked" in ln
    ]
    assert blocked, (
        "assertion failure: status output must render the publish blocked "
        f"state (FR-0287), got: {out!r}"
    )
    assert any("agent_forbidden" in ln for ln in blocked), (
        "assertion failure: the blocked status line must carry the block "
        f"reason (agent_forbidden), got: {blocked!r}"
    )


# AC-FR0287@v0.8 TRACKS-TRACE IF-RELEASE-003 no false blocked e2e
def test_cmd_status_publish_done_not_blocked_e2e(tmp_path, capsys):
    """A completed publish must not surface a blocked state in status output
    (fail-closed honesty guard; intentionally passes pre-fix)."""
    from tracks.cli.main import cmd_status

    store, repo, run_id = _setup(tmp_path)
    _seed_preview(store, run_id)
    store.append(
        run_id, "v0.8", "publish.executed",
        {"status": "done", "operation_kind": "tag", "target": "v0.8.0"},
    )
    rc = cmd_status(repo)
    out = capsys.readouterr().out
    assert rc == 0, (
        f"assertion failure: status must succeed, rc={rc}"
    )
    assert not [
        ln for ln in out.splitlines() if "publish" in ln and "blocked" in ln
    ], (
        "assertion failure: a done publish must not render a publish "
        f"blocked state, got: {out!r}"
    )
