"""Run timeline UI journey (IF-TIMELINE-002, IF-WEBUI-001).

The ``ui`` marker layer for the run detail: the current-node card, the
13-stage linear timeline with per-attempt nodes and recognizable rollback
edges, the v0.11 fixed detail pane (data-testid=detail-pane, no overlay)
with the document-center jump, and the five-state labels carried by
``data-state``.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

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

_FIVE_STATES = {"done", "active", "pending", "attention", "void"}


def _login_and_open_run(ui_serve, page, run_id: str) -> None:
    """Login + name collection + open the run detail (UI operations only)."""
    page.set_default_timeout(8_000)
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()
    page.goto(
        ui_serve.base_url + f"/runs/{run_id}", wait_until="domcontentloaded"
    )
    page.get_by_test_id("main-area").wait_for()


# AC-FR0326-01@v0.10 TRACKS-TRACE run timeline view
# AC-FR0335-01@v0.11 TRACKS-TRACE timeline five state labels by data state
# AC-FR0336-02@v0.11 TRACKS-TRACE fixed detail pane replaces the overlay
def test_run_timeline_view(ui_serve, page):
    """AC-FR0326-01 with the v0.11 face: the run detail renders the
    current-node card (owner, attempt, duration, primary action), the
    13-stage linear timeline with one node per attempt (never folded), the
    five states carried by ``data-state`` labels, and the node details load
    into the fixed detail pane below the main area — no floating overlay —
    with the document-center jump."""
    ui_serve.seed_project("proj-ui-1", "v1.0")
    run_id = ui_serve.seed_run("run-ui-1", "v1.0")
    _login_and_open_run(ui_serve, page, run_id)

    card = page.get_by_test_id("current-node-card")
    card.wait_for()
    # the run's identity opens the face (no main header strip): a deep link
    # resolves the registry label from the overview projection (bare version
    # for a title-less run, the #227 rule)
    heading = page.get_by_test_id("run-heading")
    heading.wait_for()
    assert (heading.inner_text() or "").strip() == "v1.0", (
        "the deep-linked run detail carries the run's identity (run-heading)"
    )
    for field in ("card-owner", "card-attempt", "card-duration", "card-action", "card-status"):
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
    # the five states ride the data-state label channel (never color alone)
    for index in range(nodes.count()):
        state = nodes.nth(index).get_attribute("data-state") or ""
        assert state in _FIVE_STATES, (
            f"every node carries a five-state data-state label: {state!r}"
        )

    nodes.first.click()
    pane = page.get_by_test_id("detail-pane")
    pane.wait_for()
    assert page.get_by_test_id("timeline-node-overlay").count() == 0, (
        "the floating overlay is replaced by the fixed detail pane"
    )
    assert page.get_by_test_id("detail-doc-link").count() == 1, (
        "the pane must offer the document-center jump"
    )
    pane_text = pane.inner_text()
    assert "M-" in pane_text or "revision" in pane_text.lower(), (
        "the pane must show the node timing/artifact information"
    )


# AC-FR0336-01@v0.11 TRACKS-TRACE read owning document and return to selected run node
def test_owning_document_stays_open_until_explicit_return(ui_serve, page):
    ui_serve.seed_project("proj-doc-return", "v1.0")
    ui_serve.seed_version_docs("v1.0")
    run_id = ui_serve.seed_run("run-doc-return", "v1.0")
    ui_serve.seed_run("run-newer-unrelated", "v2.0")
    _login_and_open_run(ui_serve, page, run_id)
    page.get_by_test_id("timeline-node-M-DESIGN").click()
    page.get_by_test_id("detail-owning-surface").click()

    expect(page.get_by_test_id("view-docs")).to_be_visible()
    expect(page.get_by_test_id("doc-editor-content").first).to_have_value(
        "\n# v1.0 interfaces.md\n\nfixture body for interfaces.md\n"
    )
    # Reading must survive the former 300 ms forced-return timer.
    page.wait_for_timeout(800)
    expect(page.get_by_test_id("view-docs")).to_be_visible()
    page.get_by_test_id("docs-return-to-run").click()
    expect(page.get_by_test_id("current-node-card")).to_be_visible()
    expect(page.get_by_test_id("run-heading")).to_have_text("v1.0")
    expect(page.get_by_test_id("timeline-node-M-DESIGN")).to_have_attribute(
        "data-state-selected", "true"
    )
    expect(page).to_have_url(ui_serve.base_url + f"/runs/{run_id}")


# AC-FR0358-01@v0.12 TRACKS-TRACE runtime progress updates the visible card without a human action
def test_runtime_progress_updates_without_a_human_action(ui_serve, page):
    from tracks import paths
    from tracks.store import Store

    ui_serve.seed_project("proj-live-state", "v1.0")
    run_id = ui_serve.seed_run("run-live-state", "v1.0", stage="M-STORY")
    _login_and_open_run(ui_serve, page, run_id)
    expect(page.get_by_test_id("card-status")).to_contain_text("M-STORY")
    page.get_by_test_id("timeline-node-M-START").click()
    store = Store(paths.tracks_home(ui_serve.repo))
    try:
        store.append(run_id, "v1.0", "stage.exited", {"stage": "M-STORY"})
        store.append(run_id, "v1.0", "stage.entered", {"stage": "M-SPEC"})
    finally:
        store.close()
    expect(page.get_by_test_id("card-status")).to_contain_text("M-SPEC", timeout=15_000)
    expect(page.get_by_test_id("timeline-node-M-START")).to_have_attribute(
        "data-state-selected", "true"
    )


# AC-FR0358-01@v0.12 TRACKS-TRACE environment preparation progress is readable as it happens
def test_environment_progress_is_visible_without_reloading(ui_serve, page):
    from tracks import paths
    from tracks.store import Store

    ui_serve.seed_project("proj-environment", "v0.1")
    run_id = ui_serve.seed_run("run-environment", "v0.1", stage="M-TEST")
    _login_and_open_run(ui_serve, page, run_id)
    for event_type, label in (
        ("environment.preparation_started", "准备开发环境"),
        ("environment.step_completed", "环境准备步骤完成"),
        ("environment.prepared", "开发环境已就绪"),
    ):
        store = Store(paths.tracks_home(ui_serve.repo))
        try:
            store.append(run_id, "v0.1", event_type, {"step": 0})
        finally:
            store.close()
        expect(page.get_by_test_id("timeline-last-event")).to_contain_text(label, timeout=15_000)


# AC-FR0326-01@v0.10 TRACKS-TRACE displayed stages follow the server projection's order
def test_timeline_uses_server_supplied_stage_order(ui_serve, page):
    ui_serve.seed_project("proj-stage-order", "v1.0")
    run_id = ui_serve.seed_run("run-stage-order", "v1.0")
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/runs/{run_id}/timeline?slim=1")
    ) as observed:
        _login_and_open_run(ui_serve, page, run_id)
    supplied = observed.value.json()["stage_order"]
    assert supplied, "the actual server response must declare its stage order"
    nodes = page.locator("[data-testid^='timeline-node-']")
    expect(nodes).to_have_count(len(supplied))
    assert [nodes.nth(i).get_attribute("data-testid") for i in range(len(supplied))] == [
        f"timeline-node-{stage}" for stage in supplied
    ]
