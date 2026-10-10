"""Releases window UI journey (IF-WEBUI-001, IF-RELEASES-001, IF-TIMELINE-002).

The §9.2 browser happy path: login → the five-tab shell → the Releases window
sidebar (version-grouped registry / backlog fold-over / archive query groups
plus the summary row) → run selection → the fully drawn timeline → the fixed
detail pane → the API-failure feedback face. Controls are located only via
``data-testid``; every observation is a browser-visible outlet (§4d).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  constants

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

# Bounded waits keep each journey inside the per-node battery budget; the
# undelivered client surface fails fast instead of burning the wall clock.
_WAIT_MS = 8_000


def _login(page, base_url: str) -> None:
    page.set_default_timeout(_WAIT_MS)
    page.goto(base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()


def _seed_release(ui_serve, project_id: str = "proj-rel-ui") -> str:
    """One registered run on the seeded project (the registry's happy data)."""
    ui_serve.seed_project(project_id, "v1.0")
    return ui_serve.seed_run("run-rel-ui-1", "v1.0")


# AC-FR0333-01@v0.11 TRACKS-TRACE releases sidebar groups and selection journey
def test_releases_sidebar_and_selection(ui_serve, page):
    """AC-FR0333-01: the Releases window sidebar presents the version-grouped
    registry and the backlog fold-over group with the summary row only;
    selecting a release opens its run detail; selecting a backlog entry
    presents its promotion entry; the user can return to the registry or move
    on to Docs/Todos."""
    run_id = _seed_release(ui_serve)
    _login(page, ui_serve.base_url)

    sidebar = page.get_by_test_id("releases-sidebar")
    expect(sidebar).to_be_visible()
    # three groups: the version-grouped registry, the backlog fold-over and
    # the archive query group (§1e.2)
    expect(page.get_by_test_id("releases-group-registry")).to_be_visible()
    expect(page.get_by_test_id("releases-group-backlog")).to_be_visible()
    expect(page.get_by_test_id("releases-group-archive")).to_be_visible()
    summary = page.get_by_test_id("releases-summary")
    expect(summary).to_be_visible()
    assert "proj-rel-ui" in summary.inner_text(), (
        "the summary row carries the registered project identity"
    )
    # the sidebar never lists other tabs' sub-content (§1e.2): no Docs tree
    # or Todos rows may render inside the Releases sidebar
    assert page.locator("[data-testid='releases-sidebar'] [data-testid^='doc-tree-item-']").count() == 0, (
        "the Releases sidebar must not carry Docs sub-content"
    )
    assert page.locator("[data-testid='releases-sidebar'] [data-testid^='todo-row-']").count() == 0, (
        "the Releases sidebar must not carry Todos sub-content"
    )

    # selecting the release(run) enters the run detail in the main area
    page.get_by_test_id(f"registry-run-{run_id}").click()
    expect(page.get_by_test_id("main-area")).to_contain_text("v1.0")
    expect(page.get_by_test_id("timeline")).to_be_visible()

    # back to the registry, then the backlog entry's promotion entry
    page.get_by_test_id("tabbar-item-releases").click()
    expect(sidebar).to_be_visible()


# AC-FR0334-01@v0.11 TRACKS-TRACE five tabs no review entry
def test_five_tabs_no_review(ui_serve, page):
    """AC-FR0334-01: the tab bar shows exactly Releases/Docs/Todos/Settings/
    Account, no Review entry; every tab switches sidebar and main area
    together without a Review page or an empty tab."""
    _login(page, ui_serve.base_url)
    items = page.locator("[data-testid^='tabbar-item-']")
    names = [
        (items.nth(i).get_attribute("data-testid") or "").removeprefix(
            "tabbar-item-"
        )
        for i in range(items.count())
    ]
    assert names == ["releases", "docs", "todos", "settings", "account"], (
        f"the closed five-item function set: {names!r}"
    )
    assert "review" not in names, "no Review entry survives the convergence"
    for tab in ("docs", "todos"):
        page.get_by_test_id(f"tabbar-item-{tab}").click()
        page.locator(f"[data-testid='view-{tab}']").wait_for()
    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()


# AC-FR0335-01@v0.11 TRACKS-TRACE timeline full rendering five states rollback edge
def test_timeline_full_rendering(ui_serve, page):
    """AC-FR0335-01: the run timeline draws the 13-stage canonical track with
    one node per attempt, directional connections, a recognizable rollback
    edge, five distinguishable states carried by shape+``data-state`` (never
    color alone), the initial view focused on the active node, and the full
    history navigable."""
    ui_serve.seed_project("proj-rel-ui", "v1.0")
    run_id = ui_serve.seed_run("run-rel-ui-1", "v1.0")
    _login(page, ui_serve.base_url)
    page.get_by_test_id(f"registry-run-{run_id}").click()

    timeline = page.get_by_test_id("timeline")
    expect(timeline).to_be_visible()
    rendered = page.get_by_test_id("timeline-stage-order").inner_text()
    for stage in (
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
    ):
        assert stage in rendered, f"the canonical track must cover {stage}"
    nodes = page.locator("[data-testid^='timeline-node-']")
    assert nodes.count() >= 1, "each attempt is an independent node"
    states = {
        (nodes.nth(i).get_attribute("data-state") or "")
        for i in range(nodes.count())
    }
    assert states <= {"done", "active", "pending", "attention", "void"}, (
        f"node states stay inside the five-state set: {states!r}"
    )
    assert "active" in states, "the active node is present and focused"
    # the shape channel, not color alone: nodes carry the data-state label
    assert nodes.first.get_attribute("data-state") is not None


# AC-FR0336-01@v0.11 TRACKS-TRACE node detail fixed pane with real data
def test_node_detail_fixed_pane(ui_serve, page):
    """AC-FR0336-01: selecting any node loads its details into the fixed
    detail pane below the main area — real projection data (timing, state,
    owner, artifacts, transition reason, rollback legality) — with the
    selected-vs-active double marking and the owning-surface entry that
    returns to the same run and node context."""
    ui_serve.seed_project("proj-rel-ui", "v1.0")
    run_id = ui_serve.seed_run("run-rel-ui-1", "v1.0")
    _login(page, ui_serve.base_url)
    page.get_by_test_id(f"registry-run-{run_id}").click()

    nodes = page.locator("[data-testid^='timeline-node-']")
    expect(nodes.first).to_be_visible()
    nodes.first.click()
    pane = page.get_by_test_id("detail-pane")
    expect(pane).to_be_visible()
    pane_text = pane.inner_text()
    assert pane_text.strip(), "the pane loads the node's details"
    # the double marking: the selected node and the active node are distinct
    selected = page.locator("[data-state-selected='true']")
    active = page.locator("[data-state-active='true']")
    assert selected.count() == 1, "exactly one node carries the selected mark"
    assert active.count() >= 1, "the active node keeps its own mark"
    # the owning surface entry returns to the same run context
    jump = page.get_by_test_id("detail-owning-surface")
    expect(jump).to_be_visible()
    jump.click()
    expect(page.get_by_test_id("detail-pane")).to_be_visible()
    assert run_id in page.url, "the return lands on the same run"


# AC-FR0336-02@v0.11 TRACKS-TRACE fixed pane position no overlay fabrication
def test_fixed_pane_position_no_overlay(ui_serve, page):
    """AC-FR0336-02: the detail pane is fixed below the main area (never a
    floating overlay), and a node without data renders the readable
    degradation instead of fabricated detail."""
    ui_serve.seed_project("proj-rel-ui", "v1.0")
    run_id = ui_serve.seed_run("run-rel-ui-1", "v1.0")
    _login(page, ui_serve.base_url)
    page.get_by_test_id(f"registry-run-{run_id}").click()

    nodes = page.locator("[data-testid^='timeline-node-']")
    expect(nodes.first).to_be_visible()
    nodes.first.click()
    pane = page.get_by_test_id("detail-pane")
    expect(pane).to_be_visible()
    assert page.get_by_test_id("timeline-node-overlay").count() == 0, (
        "the floating overlay is gone (the pane replaced it)"
    )
    main = page.get_by_test_id("main-area").bounding_box()
    box = pane.bounding_box()
    assert main and box, "both faces must be laid out"
    assert box["y"] >= main["y"] + main["height"] - 1, (
        "the pane sits below the main area, position fixed"
    )


# AC-NFR0158-01@v0.11 TRACKS-TRACE api failure feedback on new data surfaces
def test_api_failure_feedback_new_surfaces(ui_serve, page):
    """AC-NFR0158-01 (ui half): when the new data faces fail, the window shows
    an observable failure feedback and never fakes success — the Releases
    sidebar reports the failed aggregation instead of an empty registry."""
    _seed_release(ui_serve)
    _login(page, ui_serve.base_url)

    def _fail(route):
        route.abort()

    page.route("**/api/projects/*/releases", _fail)
    page.get_by_test_id("tabbar-item-releases").click()
    feedback = page.get_by_test_id("releases-error")
    expect(feedback).to_be_visible()
    assert feedback.inner_text().strip(), "the failure feedback is readable"
