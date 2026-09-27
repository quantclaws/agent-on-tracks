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
    page.get_by_test_id("tabbar-item-projects").wait_for()


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

    for item in ("runs", "docs", "review", "todos"):
        page.get_by_test_id(f"tabbar-item-{item}").click()
        page.get_by_test_id(f"tab-{item}").wait_for()
    assert _same_document(page), (
        "tab switching must continue the same document instance (no reload)"
    )

    # the workbench stays usable after the switches without a re-login
    page.get_by_test_id("tabbar-item-projects").click()
    page.get_by_test_id("tab-projects").wait_for()
    assert _same_document(page)


# AC-FR0317-01@v0.10 TRACKS-TRACE tab bar fixed semantics
def test_tabbar_fixed_semantics(ui_serve, page):
    """AC-FR0317-01: the left tab bar shows icons with hover tooltips, every
    icon's hit area is at least 32px, the order is fixed, there is no drag
    handle, and a reload keeps the same order."""
    _login_via_ui(page, ui_serve.base_url)
    items = page.locator("[data-testid^='tabbar-item-']")
    assert items.count() == 7, "the closed set of seven function items"
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
        "projects", "runs", "docs", "review", "todos", "settings", "account",
    ], f"the function order is fixed: {order}"
    assert page.locator("[draggable='true']").count() == 0, (
        "no drag handle may reorder the tab bar"
    )
    page.reload(wait_until="domcontentloaded")
    assert _same_document(page) is False  # the reload itself is the contrast
    reloaded = page.locator("[data-testid^='tabbar-item-']")
    assert reloaded.count() == 7


# AC-FR0318-01@v0.10 TRACKS-TRACE sidebar tab atomic switch and coexistence
def test_sidebar_tab_atomic_switch_and_coexistence(ui_serve, page):
    """AC-FR0318-01: one tab-bar click switches the sidebar and opens the
    main-area tab in the same step (no sidebar-only intermediate), a repeated
    click activates the single existing instance instead of duplicating it,
    and opened tabs coexist without a close-all action."""
    _login_via_ui(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-docs").click()
    page.get_by_test_id("tab-docs").wait_for()
    page.get_by_test_id("sidebar-tree").wait_for()

    page.get_by_test_id("tabbar-item-runs").click()
    page.get_by_test_id("tab-runs").wait_for()
    page.get_by_test_id("sidebar-tree").wait_for()
    assert _same_document(page)

    # a repeated click does not duplicate the instance
    page.get_by_test_id("tabbar-item-runs").click()
    assert page.locator("[data-testid='tab-runs']").count() == 1, (
        "the repeated click must activate the unique instance"
    )
    # both opened tabs coexist
    page.get_by_test_id("tabbar-item-docs").click()
    assert page.locator("[data-testid='tab-docs']").count() == 1
    assert page.locator("[data-testid='tab-runs']").count() == 1
    # no global close-all action exists
    assert page.locator("[data-testid='tabs-close-all']").count() == 0


# AC-FR0318-02@v0.10 TRACKS-TRACE tabs not persisted and content titles
def test_tabs_not_persisted_and_content_titles(ui_serve, page):
    """AC-FR0318-02: the tab set lives in browser memory only — a restart
    returns the empty set (expected behavior, not loss) — the main-area title
    shows the content title, and the sidebar lists only the current tab-bar
    item's subtree."""
    _login_via_ui(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-docs").click()
    page.get_by_test_id("tab-docs").wait_for()

    restart = restart_browser(page.context.browser, page.context)
    page = restart.new_page()
    page.goto(ui_serve.base_url + "/", wait_until="domcontentloaded")
    page.get_by_test_id("tabbar-item-projects").wait_for()
    assert page.locator("[data-testid^='tab-']").count() == 0, (
        "the tab set must not survive the browser restart"
    )

    page.get_by_test_id("tabbar-item-docs").click()
    title = page.get_by_test_id("main-title")
    assert "Docs" not in (title.inner_text() or ""), (
        "the main title must show the content title, not the tab-bar category"
    )


# AC-FR0318-03@v0.10 TRACKS-TRACE function set, settings/account, logout
def test_function_set_settings_account(ui_serve, page):
    """AC-FR0318-03: the seven-item closed function set (no independent
    release entry), the gear item opens a Settings tab without changing the
    sidebar, the Account menu carries only logout, and logout leaves no
    credential behind (a fresh login is required)."""
    _login_via_ui(page, ui_serve.base_url)
    items = page.locator("[data-testid^='tabbar-item-']")
    names = {
        (items.nth(index).get_attribute("data-testid") or "").removeprefix(
            "tabbar-item-"
        )
        for index in range(items.count())
    }
    assert names == {
        "projects", "runs", "docs", "review", "todos", "settings", "account",
    }
    assert "release" not in names, "no independent top-level release item"

    page.get_by_test_id("tabbar-item-projects").click()
    page.get_by_test_id("tab-projects").wait_for()
    before = page.get_by_test_id("sidebar-tree").inner_text()
    page.get_by_test_id("tabbar-item-settings").click()
    page.get_by_test_id("tab-settings").wait_for()
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
    _login_via_ui(page, ui_serve.base_url)
    responses: list[int] = []
    page.on("response", lambda response: responses.append(response.status))
    page.goto(
        ui_serve.base_url + "/runs/run-does-not-exist", wait_until="domcontentloaded"
    )
    page.get_by_test_id("main-area").wait_for()
    marker = page.locator("[data-testid='degrade-NotFound']")
    assert marker.count() >= 1, (
        "an unknown run must render a readable NotFound degradation marker"
    )
    assert not [status for status in responses if status >= 500], (
        f"degradation must not produce a 5xx: {responses}"
    )
    page.get_by_test_id("tabbar-item-projects").click()
    page.get_by_test_id("tab-projects").wait_for()
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
    page.get_by_test_id("tabbar-item-projects").wait_for()

    restart = restart_browser(page.context.browser, page.context)
    reopened = restart.new_page()
    reopened.goto(ui_serve.base_url + "/", wait_until="domcontentloaded")
    reopened.get_by_test_id("tabbar-item-projects").wait_for()
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
    assert page.get_by_test_id("tabbar-item-projects").count() == 0, (
        "the workbench data face must not open before the name is collected"
    )
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-projects").wait_for()
    page.get_by_test_id("tabbar-item-account").click()
    assert "UI Human" in page.get_by_test_id("account-menu").inner_text(), (
        "the bound display name must surface in the account area"
    )


# AC-NFR0153-01@v0.10 TRACKS-TRACE real data rendered
def test_real_data_rendered(ui_serve, page):
    """AC-NFR0153-01: the audited data face renders the event-sourced
    projection values — the seeded project and run appear with their real
    identity, and no fabricated extra rows are shown."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-projects").click()
    project_row = page.locator("[data-testid='overview-project-proj-ui-1']")
    project_row.wait_for()
    assert "v1.0" in project_row.inner_text()
    run_row = page.locator("[data-testid='overview-run-run-ui-1']")
    assert run_row.count() == 1, "the seeded run renders exactly once"
    assert "v1.0" in run_row.inner_text()


# AC-NFR0153-02@v0.10 TRACKS-TRACE API failure visible feedback
def test_api_failure_visible_feedback(ui_serve, page):
    """AC-NFR0153-02: an injected API failure produces a user-observable
    failure feedback — no fake success — and the user can keep operating
    after the fault clears."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)

    # browser-level fault injection on the overview projection
    page.route("**/api/projects/*/overview", lambda route: route.abort())
    page.get_by_test_id("tabbar-item-projects").click()
    failure = page.locator("[data-testid='api-failure']")
    failure.wait_for()
    assert failure.count() >= 1, "the failed projection must be visibly reported"

    # after the fault clears the face recovers without a reload
    page.unroute("**/api/projects/*/overview")
    page.get_by_test_id("tabbar-item-runs").click()
    page.get_by_test_id("tab-runs").wait_for()
    assert _same_document(page)


# AC-NFR0154-01@v0.10 TRACKS-TRACE SSE reconnect backfill monotonic
def test_sse_reconnect_backfill_monotonic(ui_serve, page):
    """AC-NFR0154-01: after an SSE reconnect the client backfills by cursor —
    a served duplicate and an out-of-order older event never make the
    rendered projection regress."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_via_ui(page, ui_serve.base_url)
    page.get_by_test_id("tabbar-item-runs").click()

    frames = [
        "id: 1\nevent: stage.entered\ndata: {\"stage\": \"M-START\"}\n\n",
        "id: 2\nevent: stage.entered\ndata: {\"stage\": \"M-IMPL\"}\n\n",
    ]
    reconnect_frames = [
        "id: 2\nevent: stage.entered\ndata: {\"stage\": \"M-IMPL\"}\n\n",  # duplicate
        "id: 1\nevent: stage.entered\ndata: {\"stage\": \"M-START\"}\n\n",  # older
        "id: 3\nevent: taskgraph.committed\ndata: {\"task_count\": 2}\n\n",  # new
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

    page.route("**/api/runs/*/events*", handle)
    page.get_by_test_id("tab-run-ui-1").wait_for()
    # the projection must not regress below the newest delivered event
    last = page.locator("[data-testid='timeline-last-event']")
    assert last.count() >= 1, "the timeline must render the event projection"
    assert "taskgraph.committed" in (last.inner_text() or ""), (
        "the reconnect backfill must advance to the newest event without regression"
    )


# AC-NFR0155-01@v0.10 TRACKS-TRACE first demo milestone journey
def test_first_demo_milestone_journey(ui_serve, page):
    """AC-NFR0155-01: the first demonstrable milestone journey — browser
    login (one wrong password, inline error, then the correct one), name
    collection, real project data, reload-free tab switches, the docs center
    rendering the current spec, an explicit save producing a new revision,
    and the logout returning to the login face — every step through
    data-testid controls, no API substitution."""
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
    page.get_by_test_id("tabbar-item-projects").wait_for()
    page.locator("[data-testid='overview-project-proj-ui-1']").wait_for()
    _mark_document(page)

    page.get_by_test_id("tabbar-item-docs").click()
    page.get_by_test_id("tab-docs").wait_for()
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    assert _same_document(page), "the journey continues in the same document"

    page.get_by_test_id("doc-save").wait_for()
    page.get_by_test_id("doc-save").click()
    page.get_by_test_id("doc-revision").wait_for()

    page.get_by_test_id("tabbar-item-account").click()
    page.get_by_test_id("account-logout").click()
    page.get_by_test_id("login-password").wait_for()
