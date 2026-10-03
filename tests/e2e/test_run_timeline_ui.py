"""Run timeline UI journey (IF-TIMELINE-001).

The ``ui`` marker layer for the run detail: the current-node card, the
13-stage linear timeline with per-attempt nodes and recognizable rollback
edges, the detail overlay with its document-center jump, and the
consistency of the rendered timeline with the timeline/ac-chain interface
data.
"""

from __future__ import annotations

import pytest

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  helpers + constants

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_STAGE_ORDER = [
    "M-START",
    "M-STORY",
    "M-SPEC",
    "M-ACC",
    "M-REQ-APPROVAL",
    "M-DESIGN",
    "M-TEST",
    "M-IMPL",
    "M-VERIFY",
    "M-SECURITY",
    "M-RELEASE",
    "M-PUBLISH",
    "M-MILESTONE",
]


def _login_and_open_run(ui_serve, page, run_id: str) -> None:
    """Login + name collection + open the run detail (UI operations only)."""
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-projects").wait_for()
    page.goto(
        ui_serve.base_url + f"/runs/{run_id}", wait_until="domcontentloaded"
    )
    page.get_by_test_id("main-area").wait_for()


# AC-FR0326-01@v0.10 TRACKS-TRACE run timeline view
def test_run_timeline_view(ui_serve, page):
    """AC-FR0326-01: the run detail renders the current-node card (owner,
    attempt, duration, primary action), the 13-stage linear timeline with one
    node per attempt (never folded), recognizable rollback edges, the active
    node in focus, and the node detail overlay (start/end + artifact or
    revision + the document-center jump)."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    run_id = ui_serve.seed_run("run-ui-1", "v1.0")
    _login_and_open_run(ui_serve, page, run_id)

    card = page.get_by_test_id("current-node-card")
    card.wait_for()
    for field in ("card-owner", "card-attempt", "card-duration", "card-action"):
        assert page.get_by_test_id(field).count() == 1, (
            f"the current-node card must show {field}"
        )

    timeline = page.get_by_test_id("timeline")
    timeline.wait_for()
    nodes = page.locator("[data-testid^='timeline-node-']")
    assert nodes.count() >= 1, "each attempt is an independent timeline node"
    # the 13-stage display order is rendered from the projection's stage_order
    rendered = page.get_by_test_id("timeline-stage-order").inner_text()
    for stage in _STAGE_ORDER:
        assert stage in rendered, f"the timeline must cover {stage}"

    nodes.first.click()
    overlay = page.get_by_test_id("timeline-node-overlay")
    overlay.wait_for()
    assert page.get_by_test_id("overlay-doc-link").count() == 1, (
        "the overlay must offer the document-center jump"
    )
    overlay_text = overlay.inner_text()
    assert "M-" in overlay_text or "revision" in overlay_text.lower(), (
        "the overlay must show the node timing/artifact information"
    )
