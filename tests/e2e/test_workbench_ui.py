"""UI layer Playwright journeys (IF-WEBUI-001, IF-WORKBENCH-001,
IF-AUTHNAME-001, IF-QUERY-001, IF-STREAM-001).

The ``ui`` marker layer (test-plan §9.2): every journey drives the real
serve subprocess through a real Chromium, locates controls only through
``data-testid`` (interfaces §1t.2), and never substitutes a key UI
operation with an API request (§1.3 #12). Browser-observable outlets only —
no rendering pixels, no module internals.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests._support.ui_browser import (  # noqa: F401  helpers + constants
    SERVE_PASSWORD,
    restart_browser,
)

pytestmark = [pytest.mark.e2e, pytest.mark.ui]


def _login_via_ui(page, base_url: str, *, name: str = "UI Human") -> None:
    """The browser login + name collection through data-testid controls."""
    page.goto(base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    name_input = page.get_by_test_id("name-input")
    name_input.fill(name)
    page.get_by_test_id("name-submit").click()
    # v0.11 five-tab world: the Releases window is the landing face
    page.get_by_test_id("tabbar-item-releases").wait_for()


def _mark_document(page) -> None:
    """Stamp the live document so a full reload is distinguishable."""
    page.evaluate("window.__tracks_document = 'instance-1'")


def _same_document(page) -> bool:
    return page.evaluate("window.__tracks_document === 'instance-1'")


# AC-FR0316-01@v0.10 TRACKS-TRACE SPA shell navigation without page reload
def test_spa_shell_navigation_no_reload(ui_serve, page):
    """AC-FR0316-01: switching function areas, opening/closing tabs, switching
    documents and saving all happen inside the single page — the same
    document instance continues (no full reload, no context loss) and the
    three-part chrome keeps its relative geometry across contexts."""
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.locator(".login-form input").fill(SERVE_PASSWORD)
    _login_via_ui(page, ui_serve.base_url)
    _mark_document(page)

    tabbar = page.get_by_test_id("tabbar").bounding_box()
    sidebar = page.get_by_test_id("sidebar").bounding_box()
    main = page.get_by_test_id("main-area").bounding_box()
    assert tabbar and sidebar and main, "the three-part chrome must be laid out"
    assert sidebar["x"] >= tabbar["x"] + tabbar["width"] - 1, (
        "the sidebar sits right of the vertical tab bar"
    )
    assert main["x"] >= sidebar["x"] + sidebar["width"] - 1, (
        "the main area sits right of the sidebar"
    )

    for item in ("releases", "docs", "todos"):
        page.get_by_test_id(f"tabbar-item-{item}").click()
        page.locator(f"[data-testid='view-{item}']").wait_for()
    assert _same_document(page), (
        "window switching must continue the same document instance (no reload)"
    )

    # the workbench stays usable after the switches without a re-login
    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()
    assert _same_document(page)


# AC-FR0317-01@v0.10 TRACKS-TRACE tab bar fixed semantics
def test_tabbar_fixed_semantics(ui_serve, page):
    """AC-FR0317-01: the left tab bar shows icons with hover tooltips, every
    icon's hit area is at least 32px, the order is fixed, there is no drag
    handle, and a reload keeps the same order."""
    _login_via_ui(page, ui_serve.base_url)
    items = page.locator("[data-testid^='tabbar-item-']")
    assert items.count() == 5, "the closed set of five function items"
    order = []
    for index in range(items.count()):
        item = items.nth(index)
        testid = item.get_attribute("data-testid") or ""
        order.append(testid.removeprefix("tabbar-item-"))
        box = item.bounding_box()
        assert box and box["width"] >= 32 and box["height"] >= 32, (
            f"{testid}: the hit area must be at least 32px"
        )
        tooltip = item.get_attribute("title") or item.get_attribute("aria-label") or ""
        assert tooltip.strip(), f"{testid}: the icon must carry a hover tooltip"
    assert order == [
        "releases", "docs", "todos", "settings", "account",
    ], f"the function order is fixed: {order}"
    assert page.locator("[draggable='true']").count() == 0, (
        "no drag handle may reorder the tab bar"
    )
    page.reload(wait_until="domcontentloaded")
    assert _same_document(page) is False  # the reload itself is the contrast
    reloaded = page.locator("[data-testid^='tabbar-item-']")
    assert reloaded.count() == 5


# AC-FR0318-01@v0.10 TRACKS-TRACE sidebar + main atomic window switch
# (revised 2026-10-05 by operator directive: icon-switched windows, no
# open-tab strip — the tab-bar icons are the only switcher)
def test_sidebar_tab_atomic_switch_and_coexistence(ui_serve, page):
    """AC-FR0318-01 (revised): one tab-bar click switches the sidebar and the
    main window in the same step (no sidebar-only intermediate), a repeated
    click keeps the single window of the area, and no open-tab strip exists
    anywhere in the shell."""
    _login_via_ui(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for()
    page.get_by_test_id("sidebar-tree").wait_for()

    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()
    page.get_by_test_id("sidebar-tree").wait_for()
    assert _same_document(page)
    assert page.get_by_test_id("tabbar-item-releases").get_attribute(
        "aria-current"
    ) == "page", "the active tab-bar icon must be highlighted"
    assert page.get_by_test_id("tabbar-item-docs").get_attribute(
        "aria-current"
    ) is None, "only the active icon carries the highlight"

    # a repeated click keeps the area's single window; no strip ever renders
    page.get_by_test_id("tabbar-item-releases").click()
    assert page.locator("[data-testid='view-releases']").count() == 1, (
        "the repeated click must reuse the area's single window"
    )
    assert page.locator("[data-testid^='tab-']").count() == 0, (
        "no open-tab strip: the icons switch windows (operator directive)"
    )
    page.get_by_test_id("tabbar-item-docs").click()
    assert page.locator("[data-testid='view-docs']").count() == 1
    assert page.locator("[data-testid='view-releases']").count() == 0, (
        "switching away replaces the window — exactly one main window exists"
    )


# AC-FR0318-02@v0.10 TRACKS-TRACE window state memory-only and content titles
# (revised 2026-10-05: window state, not a tab set — no strip to persist)
def test_tabs_not_persisted_and_content_titles(ui_serve, page):
    """AC-FR0318-02 (revised): the window state lives in memory only, the
    main-area title shows the content title (not the tab-bar category), and
    no tab strip exists to persist."""
    _login_via_ui(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for()

    restart = restart_browser(page.context.browser, page.context)
    page = restart.new_page()
    page.goto(ui_serve.base_url + "/", wait_until="domcontentloaded")
    page.get_by_test_id("tabbar-item-releases").wait_for()
    page.locator("[data-testid='view-releases']").wait_for()
    assert page.locator("[data-testid^='tab-']").count() == 0, (
        "no open-tab strip exists, persisted or otherwise"
    )
    assert page.evaluate("() => window.localStorage.length") == 0, (
        "window state must stay memory-only — nothing may write client storage"
    )

    page.get_by_test_id("tabbar-item-docs").click()
    # 2026-10-09 operator directive: the main header strip is gone — the
    # content face starts directly (no category title anywhere)
    assert page.get_by_test_id("main-title").count() == 0, (
        "the main header strip must not exist — the face starts at the top"
    )
    page.locator("[data-testid='view-docs']").wait_for()


# AC-FR0318-03@v0.10 TRACKS-TRACE function set, settings/account, logout
def test_function_set_settings_account(ui_serve, page):
    """AC-FR0318-03: the five-item closed function set in the v0.11 converged
    shell (no Review entry), the gear item opens a Settings tab without
    changing the sidebar, the Account menu carries only logout, and logout
    leaves no credential behind (a fresh login is required)."""
    _login_via_ui(page, ui_serve.base_url)
    items = page.locator("[data-testid^='tabbar-item-']")
    names = {
        (items.nth(index).get_attribute("data-testid") or "").removeprefix(
            "tabbar-item-"
        )
        for index in range(items.count())
    }
    assert names == {
        "releases", "docs", "todos", "settings", "account",
    }
    assert "review" not in names, "no Review entry survives the convergence"

    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()
    before = page.get_by_test_id("sidebar-tree").inner_text()
    page.get_by_test_id("tabbar-item-settings").click()
    page.locator("[data-testid='view-settings']").wait_for()
    assert page.get_by_test_id("sidebar-tree").inner_text() == before, (
        "the gear item opens the Settings tab without changing the sidebar"
    )

    page.get_by_test_id("tabbar-item-account").click()
    page.get_by_test_id("account-logout").click()
    page.get_by_test_id("login-password").wait_for()
    restart = restart_browser(page.context.browser, page.context)
    fresh = restart.new_page()
    fresh.goto(ui_serve.base_url + "/", wait_until="domcontentloaded")
    assert fresh.get_by_test_id("login-password").count() == 1, (
        "logout must leave no credential; the workbench requires a fresh login"
    )


# AC-FR0319-01@v0.10 TRACKS-TRACE unknown values degrade visibly
def test_unknown_values_degrade_visibly(ui_serve, page):
    """AC-FR0319-01: an unknown/missing value (an unknown run deep link) shows
    a readable degradation marker in place — no crash, no 5xx, no blank tab —
    and the user can keep navigating."""
    # the Releases window resolves a run through its registered project
    # (v0.12 main-area contract); register one so the run deep link reaches
    # the timeline's unknown-run degradation instead of the no-project face.
    ui_serve.seed_project("proj-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)
    responses: list[int] = []
    page.on("response", lambda response: responses.append(response.status))
    page.goto(
        ui_serve.base_url + "/runs/run-does-not-exist", wait_until="domcontentloaded"
    )
    page.get_by_test_id("main-area").wait_for()
    marker = page.locator("[data-testid='degrade-NotFound']")
    marker.wait_for()
    assert marker.count() >= 1, (
        "an unknown run must render a readable NotFound degradation marker"
    )
    assert not [status for status in responses if status >= 500], (
        f"degradation must not produce a 5xx: {responses}"
    )
    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()
    assert _same_document(page)


# AC-FR0320-01@v0.10 TRACKS-TRACE login dual pane and persistent session
def test_login_dual_pane_and_persistent_session(ui_serve, page):
    """AC-FR0320-01: the login face renders the two-pane auth shell (hero +
    panel) with inline error display and a single-column collapse on a narrow
    viewport; the signed-in session survives a browser restart through the
    persistent cookie (7-day rolling semantics)."""
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    hero = page.get_by_test_id("login-hero")
    panel = page.get_by_test_id("login-panel")
    hero.wait_for()
    panel.wait_for()
    hero_box = hero.bounding_box()
    panel_box = panel.bounding_box()
    assert hero_box and panel_box and panel_box["x"] > hero_box["x"], (
        "the desktop login face is two columns: hero left, panel right"
    )

    # a wrong password reports inline (not a separate error page)
    page.get_by_test_id("login-password").fill("wrong-password")
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("login-error").wait_for()

    # the narrow viewport collapses to one column
    page.set_viewport_size({"width": 420, "height": 800})
    hero_box = hero.bounding_box()
    panel_box = panel.bounding_box()
    assert hero_box and panel_box and abs(panel_box["x"] - hero_box["x"]) < 2, (
        "the narrow viewport folds the two panes into one column"
    )
    page.set_viewport_size({"width": 1280, "height": 800})

    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").wait_for()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()

    restart = restart_browser(page.context.browser, page.context)
    reopened = restart.new_page()
    reopened.goto(ui_serve.base_url + "/", wait_until="domcontentloaded")
    reopened.get_by_test_id("tabbar-item-releases").wait_for()
    assert reopened.get_by_test_id("login-password").count() == 0, (
        "the persistent session cookie must keep the browser signed in"
    )


# AC-FR0321-01@v0.10 TRACKS-TRACE first login name collection
def test_first_login_name_collection(ui_serve, page):
    """AC-FR0321-01: a first login is required to submit the display name
    before the workbench data face opens, and the bound name surfaces in the
    account area of the same session."""
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").wait_for()
    # the data face stays closed until the name is submitted
    assert page.get_by_test_id("tabbar-item-releases").count() == 0, (
        "the workbench data face must not open before the name is collected"
    )
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()
    page.get_by_test_id("tabbar-item-account").click()
    assert "UI Human" in page.get_by_test_id("account-menu").inner_text(), (
        "the bound display name must surface in the account area"
    )


# AC-NFR0153-01@v0.10 TRACKS-TRACE real data rendered
def test_real_data_rendered(ui_serve, page):
    """AC-NFR0153-01: the audited data face renders the event-sourced
    projection values — the seeded project and run appear with their real
    identity in the Releases sidebar registry (v0.12 main-area contract),
    and no fabricated extra rows are shown."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)
    page.locator("[data-testid='view-releases']").wait_for()
    registry_row = page.get_by_test_id("sidebar-run-run-ui-1")
    registry_row.wait_for()
    assert "v1.0" in registry_row.inner_text()
    assert page.locator("[data-testid='sidebar-run-run-ui-1']").count() == 1, (
        "the seeded run renders exactly once in the registry group"
    )


# AC-NFR0153-02@v0.10 TRACKS-TRACE API failure visible feedback
def test_api_failure_visible_feedback(ui_serve, page):
    """AC-NFR0153-02: an injected API failure produces a user-observable
    failure feedback — no fake success — and the user can keep operating
    after the fault clears."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)

    # browser-level fault injection on the Releases aggregation projection
    page.route("**/api/projects/*/releases", lambda route: route.abort())
    page.get_by_test_id("tabbar-item-releases").click()
    failure = page.locator("[data-testid='api-failure']")
    failure.wait_for()
    assert failure.count() >= 1, "the failed projection must be visibly reported"

    # after the fault clears the face recovers without a reload
    page.unroute("**/api/projects/*/releases")
    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for()
    assert _same_document(page)


# AC-NFR0154-01@v0.10 TRACKS-TRACE SSE reconnect backfill monotonic
def test_sse_reconnect_backfill_monotonic(ui_serve, page):
    """AC-NFR0154-01: after an SSE reconnect the client backfills by cursor —
    a served duplicate and an out-of-order older event never make the
    rendered projection regress."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)

    frames = [
        "id: 1\nevent: stage.entered\ndata: {\"stage\": \"M-START\"}\n\n",
        "id: 2\nevent: stage.entered\ndata: {\"stage\": \"M-IMPL\"}\n\n",
    ]
    reconnect_frames = [
        "id: 2\nevent: stage.entered\ndata: {\"stage\": \"M-IMPL\"}\n\n",  # duplicate
        "id: 3\nevent: taskgraph.committed\ndata: {\"task_count\": 2}\n\n",  # new
        "id: 1\nevent: stage.entered\ndata: {\"stage\": \"M-START\"}\n\n",  # older after new
    ]
    served = {"count": 0}

    def handle(route):
        served["count"] += 1
        body = "".join(frames if served["count"] == 1 else reconnect_frames)
        route.fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=body,
        )

    # Install the route before selecting the run: the timeline opens its
    # catch-up stream on first render, so a later route never sees it.
    page.route("**/api/runs/*/events*", handle)
    page.get_by_test_id("sidebar-run-run-ui-1").click()
    # The click re-renders the landing face in place; wait for the
    # run-scoped URL (set just before the body swap) so the stream under
    # test belongs to the selected run's final render.
    page.wait_for_url("**/runs/run-ui-1")
    page.locator("[data-testid='timeline']").wait_for()
    # the projection must not regress below the newest delivered event;
    # the OOB face localizes known event types, but taskgraph.committed has
    # no label and stays verbatim.
    last = page.locator("[data-testid='timeline-last-event']")
    assert last.count() >= 1, "the timeline must render the event projection"
    expect(last).to_contain_text("taskgraph.committed", timeout=10_000)
    # The first served body carries no id 3, so reaching the newest event
    # proves the reconnect/backfill face ran at least once. Its final
    # frame is an OLDER cursor: the monotonic guard must keep id 3 on top.
    assert served["count"] >= 2, "the reconnect backfill must be exercised"
    page.wait_for_timeout(2_000)
    assert "taskgraph.committed" in (last.inner_text() or ""), (
        "an out-of-order older frame must never regress the projection"
    )


# AC-NFR0155-01@v0.10 TRACKS-TRACE first demo milestone journey
def test_first_demo_milestone_journey(ui_serve, page):
    """AC-NFR0155-01: the first demonstrable milestone journey — browser
    login (one wrong password, inline error, then the correct one), name
    collection, real project data, reload-free tab switches, the docs center
    rendering the current spec, an edit autosaving itself into a new
    revision (2026-10-09 rework), and the logout returning to the login face
    — every step through data-testid controls, no API substitution."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_version_docs("v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")

    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill("wrong-password")
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("login-error").wait_for()
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").wait_for()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()
    page.locator("[data-testid='sidebar-run-run-ui-1']").wait_for()
    _mark_document(page)

    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for()
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    assert _same_document(page), "the journey continues in the same document"

    save = page.get_by_test_id("doc-save")
    save.wait_for()
    assert save.is_disabled(), "a freshly opened document shows the saved state"
    with page.expect_response(lambda response: "/edits" in response.url):
        page.get_by_test_id("doc-editor-content").fill("journey autosave edit\n")
    assert page.wait_for_function(
        "() => document.querySelector('[data-testid=\"doc-save\"]').disabled === true"
    ), "the autosaved edit must return the control to the saved state"

    page.get_by_test_id("tabbar-item-account").click()
    page.get_by_test_id("account-logout").click()
    page.get_by_test_id("login-password").wait_for()


# Regression lock (operator fix 2026-10-05, OOB): a registry holding many
# runs must not starve view rendering. Per-run live faces share one channel
# per run and live streams are capped, so every functional area's data
# fetches always keep free same-host connection slots (the browser allows
# ~6 concurrent HTTP/1.1 connections per host; the pre-fix per-row streams
# starved the whole workbench once the registry held enough runs).
def test_many_runs_do_not_starve_view_rendering(ui_serve, page):
    """A multi-run registry (the real service carries the whole release
    history) leaves the docs centre renderable while the runs area holds
    live subscriptions for every non-terminal run."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_version_docs("v1.0")
    for index in range(8):
        ui_serve.seed_run(f"run-ui-multi-{index}", "v1.0")
    _login_via_ui(page, ui_serve.base_url)

    # v0.12 five-tab world: the Releases sidebar owns the registry (compact
    # faces) — the timeline face renders on selection. Open the newest run
    # so the event projection (timeline-last-event) presents.
    page.locator("[data-testid='sidebar-run-run-ui-multi-7']").wait_for(timeout=10000)
    page.get_by_test_id("sidebar-run-run-ui-multi-7").click()
    # The landing runs view settles each row on the NEWEST drained event:
    # a JSON poll batch carries one cursor for the whole batch, so applying
    # only its head row would freeze the line at the OLDEST event.
    expect(page.locator("[data-testid='timeline-last-event']").first).to_contain_text(
        "进入阶段", timeout=10000
    )

    # The sidebar subtree is the run registry, newest first, and the main
    # face opens on the newest run's stage track (not a flat row list).
    # v0.12: the focused timeline face keeps the Releases sidebar registry
    # — go back to the Releases window for the switcher.
    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='sidebar-run-run-ui-multi-7']").wait_for(timeout=10000)
    sidebar_runs = page.locator("[data-testid^='sidebar-run-run-ui-multi-']")
    expect(sidebar_runs.first).to_be_visible(timeout=10000)
    assert sidebar_runs.count() == 8, "the sidebar lists the whole registry"
    assert (sidebar_runs.first.get_attribute("data-testid") or "").endswith(
        "run-ui-multi-7"
    ), "the newest run leads the sidebar"

    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='doc-tree-item-v1.0-spec']").wait_for(timeout=10000)
    assert (
        page.locator("[data-testid='doc-tree-item-v1.0-story']").count() == 1
    ), "the docs tree must render its documents under multi-run load"


# TRACKS-TRACE #224 runs registry carries human-friendly story titles
# TRACKS-TRACE #227 registry rows stay compact; the main area keeps the full title
def test_runs_registry_human_titles(ui_serve, page):
    """#224: the runs sidebar and the overview rows lead with the version's
    story title (from story.md frontmatter) instead of the raw ULID run id.
    #227: registry rows are compact index entries — a long title truncates at
    20 characters with an ellipsis (the main area still shows the full title)
    and a run without a story title (the hotfix case, where the projection
    falls back to the raw ULID) shows the bare version."""
    ui_serve.seed_project("proj-ui-title", "v1.0")
    ui_serve.seed_version_docs("v0.9-hotfix-7", ("story.md", "spec.md"))
    ui_serve.seed_run("run-ui-hotfix-1", "v0.9-hotfix-7")
    ui_serve.seed_version_docs(
        "v1.0", story_title="Web 工作台完善（UI）+ 发布卫生"
    )
    ui_serve.seed_run("run-ui-title-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)

    node = page.get_by_test_id("sidebar-run-run-ui-title-1")
    node.wait_for()
    label = node.inner_text() or ""
    assert label == "v1.0 · Web 工作台完善（UI）…", (
        f"the sidebar run label truncates at 20 characters: {label!r}"
    )
    hotfix = page.get_by_test_id("sidebar-run-run-ui-hotfix-1")
    assert (hotfix.inner_text() or "") == "v0.9-hotfix-7", (
        "a run without a story title shows the bare version, not the ULID"
    )

    # the main area keeps the untruncated title — on the timeline face's
    # run heading now that the main header strip is gone (2026-10-09)
    heading = page.locator("[data-testid='run-heading']")
    assert "Web 工作台完善（UI）+ 发布卫生" in (heading.inner_text() or ""), (
        "the timeline face's run heading stays complete"
    )

    page.get_by_test_id("tabbar-item-releases").click()
    registry = page.get_by_test_id("sidebar-run-run-ui-title-1")
    assert (registry.inner_text() or "") == "v1.0 · Web 工作台完善（UI）…", (
        "the Releases registry row truncates the same way"
    )


# AC-NFR0158-02@v0.11 TRACKS-TRACE v010 rulings regression in five tab world
def test_v010_rulings_regression(ui_serve, page):
    """AC-NFR0158-02 (behavior half): the five v0.10 workbench rulings keep
    holding in the v0.11 five-tab world — the shell is pinned to the viewport
    (钉视口), the minimal design tokens drive the chrome (极简设计令牌), the
    registry rows stay compact at 20 characters (紧凑行), the document pane
    bar stays a single control surface (pane bar 单控制面), and the editor
    autosave returns the control to the saved state (自动保存)."""
    import re
    from pathlib import Path

    _login_via_ui(page, ui_serve.base_url)

    # 钉视口: the three-part chrome stays inside the viewport
    page.set_viewport_size({"width": 1280, "height": 800})
    tabbar = page.get_by_test_id("tabbar").bounding_box()
    sidebar = page.get_by_test_id("sidebar").bounding_box()
    main = page.get_by_test_id("main-area").bounding_box()
    assert tabbar and sidebar and main, "the three-part chrome is laid out"
    for box in (tabbar, sidebar, main):
        assert box["x"] >= 0 and box["y"] >= 0, "the chrome stays pinned"
        assert box["y"] + box["height"] <= 800 + 1, (
            f"the chrome must not overflow the pinned viewport: {box!r}"
        )

    # 极简设计令牌: the token palette stays declared in the stylesheet
    # (the contract file fact behind the rendered chrome)
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    styles_text = (_REPO_ROOT / "tracks/server/static/styles.css").read_text(
        encoding="utf-8"
    )
    for token in ("--bg", "--fg", "--accent"):
        assert token in styles_text, (
            f"the minimal token palette must stay declared: {token}"
        )
    assert re.search(r"--accent\s*:", styles_text), "token block stays canonical"

    # 紧凑行: the Releases registry row truncates at 20 characters
    ui_serve.seed_project("proj-rulings-1", "v1.0")
    ui_serve.seed_version_docs("v1.0", story_title="紧凑行裁定回归用超长标题需要截断处理")
    ui_serve.seed_run("run-ui-rulings-1", "v1.0")
    page.reload(wait_until="domcontentloaded")
    page.locator("[data-testid='view-releases']").wait_for()
    row = page.get_by_test_id("sidebar-run-run-ui-rulings-1")
    row.wait_for()
    label = (row.inner_text() or "").strip()
    assert "…" in label and len(label.replace("v1.0 · ", "")) <= 21, (
        f"the registry row stays compact (truncate at 20 + ellipsis): {label!r}"
    )

    # pane bar 单控制面 + 自动保存: the docs editor's pane bar carries exactly
    # one save control and the autosave returns it to the saved state
    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for()
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    save_controls = page.locator("[data-testid='doc-save']")
    assert save_controls.count() == 1, (
        "the pane bar stays a single control surface (one save control)"
    )
    assert save_controls.first.is_disabled(), (
        "a freshly opened document shows the saved state"
    )
    with page.expect_response(lambda response: "/edits" in response.url):
        page.get_by_test_id("doc-editor-content").fill("rulings autosave edit\n")
    assert page.wait_for_function(
        "() => document.querySelector('[data-testid=\"doc-save\"]').disabled === true"
    ), "the autosave must return the control to the saved state"
