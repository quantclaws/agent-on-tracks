"""E2E happy path: the v0.12.1 Releases / Todos UI revision.

The UI journey (test-plan §9.2): a LEGACY run (a run with no explicit
release-tail stage events) opens in the Releases window; the timeline
SYNTHESIZES the release-tail transitions so the stage workflow window is
not empty; the selected stage node carries only an outline ring (the
``state-*`` colours stay, the ``aria`` marks are present); the backlog
sidebar entries show a READABLE title (never a ULID); and the Todos inbox
cards cover the three sources, are clickable and keep their
``data-testid="todo-*"`` anchors.

This is the UI happy path only; the boundary/error paths live in the
integration layer (``tests/integration/test_hotfix262_ui_perf.py`` and
``tests/integration/test_hotfix262_stage_events.py``).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  constants

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_VERSION = "v1.0"
_WAIT_MS = 10_000

# The release-tail stages the legacy run must synthesize.
_RELEASE_TAIL = ("M-RELEASE", "M-PUBLISH", "M-MILESTONE")


def _login(page, base_url: str) -> None:
    page.set_default_timeout(_WAIT_MS)
    page.goto(base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()


def _seed_legacy_run(ui_serve, run_id: str) -> None:
    """A legacy run: no explicit release-tail stage events.

    The seed mirrors the offline v0.12.0 slice (the 7473-shape): the run
    walks the pre-release stages and then records the publish/milestone
    facts WITHOUT the explicit ``stage.exited`` / ``stage.entered`` events
    the v0.12.1 contract adds.
    """
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(ui_serve.repo))
    try:
        store.append(run_id, _VERSION, "story.requested", {"raw_chars": 1})
        for stage in ("M-START", "M-STORY", "M-SPEC", "M-ACC", "M-IMPL", "M-VERIFY"):
            store.append(run_id, _VERSION, "stage.entered", {"stage": stage})
            store.append(run_id, _VERSION, "stage.exited", {"stage": stage})
        store.append(
            run_id,
            _VERSION,
            "publish.planned",
            {"candidate_sha": "44c14de0000000000000000000000000000000000"},
        )
        store.append(
            run_id,
            _VERSION,
            "milestone.trace_closed",
            {"trace_digest": "sha256:legacy7473"},
        )
        store.append(
            run_id,
            _VERSION,
            "run.completed",
            {"terminal_state": "released"},
        )
    finally:
        store.close()


def _seed_backlog(ui_serve, entry_id: str) -> None:
    """A backlog entry whose intake carries a readable title."""
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(ui_serve.repo))
    try:
        store.append(
            entry_id,
            _VERSION,
            "backlog.recorded",
            {
                "reason": "active_run",
                "source": "text",
                "content": "修复发布门禁的凭据回退",
                "intake_id": None,
                "title": "修复发布门禁的凭据回退",
            },
        )
    finally:
        store.close()


def _seed_untitled_backlog(ui_serve, entry_id: str) -> None:
    """A backlog entry with NO readable content at all.

    The contract's fallback chain ends in the localized placeholder
    (``待办条目 (<version/date>)``); the ULID must never be rendered.
    """
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(ui_serve.repo))
    try:
        store.append(
            entry_id,
            _VERSION,
            "backlog.recorded",
            {"reason": "active_run", "source": "text", "content": "", "intake_id": None},
        )
    finally:
        store.close()


# AC-FR0335-01@v0.11 TRACKS-TRACE legacy run stage window is not empty
def test_legacy_run_stage_window_nonempty(ui_serve, page):
    """AC-FR0335-01: opening a LEGACY run renders the synthesized release
    transitions -- the stage workflow window is NOT empty and the
    M-MILESTONE node is closed as done.

    Contract: interfaces §1f item 3 (the legacy fallback), §4c outlet 3.
    """
    ui_serve.seed_project("proj-hf262-legacy", _VERSION)
    _seed_legacy_run(ui_serve, "run-hf262-legacy")
    _login(page, ui_serve.base_url)
    page.goto(
        ui_serve.base_url + "/runs/run-hf262-legacy", wait_until="domcontentloaded"
    )
    page.get_by_test_id("main-area").wait_for()

    # The stage path renders the 13 canonical stages.
    order = page.get_by_test_id("timeline-stage-order")
    expect(order).to_be_visible()
    for stage in _RELEASE_TAIL:
        assert stage in order.inner_text(), (
            f"the stage path must show {stage}"
        )

    # The legacy run's release-tail stages are SYNTHESIZED: the M-PUBLISH
    # and M-MILESTONE nodes are rendered even though the run carries no
    # explicit stage events for them.
    for stage in ("M-PUBLISH", "M-MILESTONE"):
        node = page.get_by_test_id(f"timeline-node-{stage}")
        expect(node).to_be_visible(timeout=15_000)
        state = node.get_attribute("data-state")
        assert state == "done", (
            f"the synthesized {stage} node must be rendered as done (got {state!r})"
        )

    # The stage workflow window is NOT empty for the synthesized stage.
    page.get_by_test_id("timeline-node-M-PUBLISH").click()
    summary = page.get_by_test_id("stage-workflow-summary")
    expect(summary).to_be_visible(timeout=15_000)
    assert page.get_by_test_id("stage-workflow-empty").count() == 0, (
        "the synthesized release-tail stage must have a non-empty workflow window"
    )


# AC-FR0333-01@v0.11 TRACKS-TRACE todos cards are clickable and readable
# AC-FR0307-01@v0.9 TRACKS-TRACE todos cards carry their context
def test_todos_cards_clickable_readable(ui_serve, page):
    """AC-FR0333-01 / AC-FR0307-01: the Todos inbox shows a readable card
    per item -- ``<version> · <title> — <stage> · <kind_label>`` -- that
    navigates to the run timeline, keeping the ``data-testid="todo-*"``
    anchor.

    Contract: interfaces §1g item 5, §4c outlet 6.
    """
    from tracks.paths import tracks_home
    from tracks.store import Store

    ui_serve.seed_project("proj-hf262-todos", _VERSION)
    store = Store(tracks_home(ui_serve.repo))
    try:
        store.append("run-hf262-todo", _VERSION, "story.requested", {"raw_chars": 1})
        store.append(
            "run-hf262-todo", _VERSION, "stage.entered", {"stage": "M-RELEASE"}
        )
        store.append(
            "run-hf262-todo",
            _VERSION,
            "release.previewed",
            {"preview_digest": "sha256:abc", "candidate_sha": "c" * 40},
        )
    finally:
        store.close()

    _login(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-todos").click()
    page.locator('[data-testid="view-todos"]').wait_for()

    card = page.get_by_test_id("todo-run-hf262-todo")
    expect(card).to_be_visible(timeout=15_000)
    text = card.inner_text()
    assert _VERSION in text, f"the todo card must carry the version: {text!r}"
    assert "M-RELEASE" in text, f"the todo card must carry the stage: {text!r}"

    # The card is clickable and navigates to the run timeline.
    card.click()
    page.wait_for_url("**/runs/run-hf262-todo")
    expect(page.get_by_test_id("timeline")).to_be_visible(timeout=15_000)


# AC-FR0340-01@v0.11 TRACKS-TRACE backlog titles are readable
def test_backlog_titles_readable(ui_serve, page):
    """AC-FR0340-01: the Releases sidebar's backlog group shows a READABLE
    title for each entry and never a ULID.

    Contract: interfaces §1g item 4, §4c outlet 5.
    """
    ui_serve.seed_project("proj-hf262-backlog", _VERSION)
    entry_id = "01HQF262BACKLOGENTRY0000000"
    untitled_id = "01HQF262BACKLOGUNTITLED000"
    _seed_backlog(ui_serve, entry_id)
    _seed_untitled_backlog(ui_serve, untitled_id)

    _login(page, ui_serve.base_url)
    group = page.get_by_test_id("releases-group-backlog")
    expect(group).to_be_visible(timeout=15_000)

    entry = page.get_by_test_id(f"sidebar-backlog-{entry_id}")
    expect(entry).to_be_visible(timeout=15_000)
    text = entry.inner_text()
    assert entry_id not in text, (
        f"the backlog entry must never render its ULID (got {text!r})"
    )
    assert text.strip(), "the backlog entry must show a readable title"
    assert "修复发布门禁" in text, (
        f"the backlog entry must show the intake title (got {text!r})"
    )

    # The untitled entry falls back to the localized placeholder -- never to
    # its ULID.
    fallback = page.get_by_test_id(f"sidebar-backlog-{untitled_id}")
    expect(fallback).to_be_visible(timeout=15_000)
    fallback_text = fallback.inner_text()
    assert untitled_id not in fallback_text, (
        f"the fallback title must never render the ULID (got {fallback_text!r})"
    )
    assert fallback_text.strip(), (
        "the untitled backlog entry must still show a localized placeholder"
    )
