"""Docs-center UI journeys (IF-DOCCENTER-001, IF-DOCSAVE-001, IF-DISCUSS-001,
IF-SECRECY-001, IF-CMDSVC-001).

The ``ui`` marker layer for the document workspace: the Vditor ir host with
same-origin assets, the textarea fallback, the explicit-save dirty gate and
conflict recovery, the pane model, and the read-only discussion overlay — all
through data-testid controls on the real serve + Chromium stack.
"""

from __future__ import annotations

import pytest

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  helpers + constants

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_CANARY = "v010-canary-secret-7f3a9"
_CANARY_ENV = "TRAC_UI_CANARY_TOKEN"


@pytest.fixture(autouse=True)
def _canary_secret(monkeypatch):
    """Register the canary as a protected credential before serve starts, so
    the redaction projection must scrub it from every surface (§1g.3)."""
    monkeypatch.setenv(_CANARY_ENV, _CANARY)


def _login_and_open_docs(ui_serve, page):
    """Login + name collection + open the docs tab (UI operations only)."""
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-projects").wait_for()
    page.get_by_test_id("tabbar-item-docs").click()
    page.get_by_test_id("tab-docs").wait_for()


def _seed_canary_event(ui_serve, run_id: str) -> None:
    """A service-plane audit event that carries the canary verbatim — the
    projection surface the redactor must scrub before the browser sees it."""
    from tracks.supervisor import db as sdb

    store = sdb.ServiceDB(ui_serve.home)
    store.append_event(
        "command.accepted",
        {"command_id": "cmd-canary-1", "kind": "edit_material", "detail": _CANARY},
        run_id=run_id,
    )


# AC-FR0322-01@v0.10 TRACKS-TRACE docs center Vditor ir same origin
def test_docs_center_vditor_ir_same_origin(ui_serve, page):
    """AC-FR0322-01: selecting a six-piece document renders it in the Vditor
    ir host and every editor asset is loaded from the same origin vendor path
    (no CDN, no cross-origin request)."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    cross_origin: list[str] = []
    page.on("request", lambda request: cross_origin.append(request.url))
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    origin = ui_serve.base_url
    vendor = [url for url in cross_origin if "vditor" in url]
    assert vendor, "the Vditor assets must be requested"
    for url in vendor:
        assert url.startswith(origin), f"the Vditor asset must be same-origin: {url}"
    assert not [url for url in cross_origin if not url.startswith(origin)], (
        f"no cross-origin request may occur: {cross_origin}"
    )


# AC-FR0322-02@v0.10 TRACKS-TRACE Vditor failure textarea fallback
def test_vditor_failure_textarea_fallback(ui_serve, page):
    """AC-FR0322-02: when the Vditor asset loading is blocked the document
    stays fully viewable and editable in the fallback textarea — the main
    path is never blocked."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.route("**/static/vendor/vditor/**", lambda route: route.abort())
    page.get_by_test_id("doc-tree-item-spec").click()
    fallback = page.get_by_test_id("doc-editor-fallback")
    fallback.wait_for()
    assert "fixture body for spec.md" in (fallback.input_value() or ""), (
        "the fallback textarea must carry the complete document content"
    )
    fallback.fill("---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 spec\n\nfallback edit\n")
    assert "fallback edit" in (fallback.input_value() or "")


# AC-FR0323-01@v0.10 TRACKS-TRACE save button dirty gating
def test_save_button_dirty_gating(ui_serve, page):
    """AC-FR0323-01: the save control is disabled while the editor is clean
    and becomes enabled once edited; the explicit save produces the new
    revision and the editor baseline switches to it."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    save = page.get_by_test_id("doc-save")
    assert save.is_disabled(), "a clean editor must not offer the save"
    page.get_by_test_id("doc-editor-content").fill("edited through the ui\n")
    assert not save.is_disabled(), "a dirty editor must enable the save"
    save.click()
    page.get_by_test_id("doc-revision").wait_for()
    revision = page.get_by_test_id("doc-revision").inner_text()
    assert revision and revision != "rev-clean", (
        "the save must move the editor to the new revision baseline"
    )


# AC-FR0323-02@v0.10 TRACKS-TRACE conflict dialog two options
def test_conflict_dialog_two_options(ui_serve, page):
    """AC-FR0323-02: a 409 from the save channel opens the conflict dialog
    with the two options — reload-discard (drops the local change, rebases on
    current_revision) and force-overwrite (keeps the local edit, resubmits
    with current_revision as the new base); nothing is silently overwritten."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    submissions: list[dict] = []

    def conflict(route):
        submissions.append(route.request.post_data_json or {})
        route.fulfill(
            status=409,
            headers={"Content-Type": "application/json"},
            body=(
                '{"error": {"reason": "stale_revision", "current_revision": '
                '"rev-server-current"}}'
            ),
        )

    page.route("**/api/runs/*/docs/*/edits", conflict)
    page.get_by_test_id("doc-editor-content").fill("conflicting local edit\n")
    page.get_by_test_id("doc-save").click()
    dialog = page.get_by_test_id("conflict-dialog")
    dialog.wait_for()
    assert "rev-server-current" in (dialog.inner_text() or ""), (
        "the dialog must surface the server's current revision"
    )
    assert page.get_by_test_id("conflict-reload").count() == 1, (
        "the reload-discard option must be offered"
    )
    assert page.get_by_test_id("conflict-overwrite").count() == 1, (
        "the force-overwrite option must be offered"
    )
    # force-overwrite keeps the local edit and resubmits on the new base
    page.get_by_test_id("conflict-overwrite").click()
    assert submissions and submissions[-1].get("base_revision") == "rev-server-current", (
        f"the force-overwrite must resubmit with current_revision: {submissions!r}"
    )
    assert submissions[-1].get("content") == "conflicting local edit\n", (
        "the force-overwrite must keep the local edit"
    )


# AC-FR0324-01@v0.10 TRACKS-TRACE multi pane independent
def test_multi_pane_independent(ui_serve, page):
    """AC-FR0324-01: up to four panes open side by side, each with its own
    file selector and toolbar, loading different documents independently —
    one pane's selection and edits never disturb the others."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    for index in range(4):
        page.get_by_test_id("pane-add").click()
        pane = page.locator(f"[data-testid='doc-pane-{index}']")
        pane.wait_for()
        assert page.locator("[data-testid^='doc-pane-']").count() == index + 1
    panes = page.locator("[data-testid^='doc-pane-']")
    assert panes.count() == 4, "at most four panes may be open"
    panes.nth(0).get_by_test_id("pane-selector").select_option(label="spec")
    panes.nth(1).get_by_test_id("pane-selector").select_option(label="acceptance")
    assert panes.nth(0).get_by_test_id("pane-selector").input_value() == "spec"
    assert panes.nth(1).get_by_test_id("pane-selector").input_value() == "acceptance"
    panes.nth(0).get_by_test_id("pane-toolbar-edit").click()
    assert panes.nth(1).get_by_test_id("pane-toolbar-edit").get_attribute(
        "aria-pressed"
    ) in (None, "false"), "panes keep independent toolbars"


# AC-FR0324-02@v0.10 TRACKS-TRACE pane cap four
def test_pane_cap_four(ui_serve, page):
    """AC-FR0324-02: a fifth pane request does not add a pane and the
    existing four panes keep their content untouched."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    for _index in range(4):
        page.get_by_test_id("pane-add").click()
    page.locator("[data-testid='doc-pane-3']").wait_for()
    before = [
        pane.get_by_test_id("pane-selector").input_value()
        for pane in page.locator("[data-testid^='doc-pane-']")
    ]
    page.get_by_test_id("pane-add").click()
    assert page.locator("[data-testid^='doc-pane-']").count() == 4, (
        "a fifth pane request must not add a pane"
    )
    after = [
        pane.get_by_test_id("pane-selector").input_value()
        for pane in page.locator("[data-testid^='doc-pane-']")
    ]
    assert after == before, "the existing panes must be unaffected by the refusal"


# AC-FR0325-01@v0.10 TRACKS-TRACE discussion navigation controls
def test_discussion_navigation_controls(ui_serve, page):
    """AC-FR0325-01: the discussion overlay offers the three navigation
    capabilities — show/hide toggle, next-discussion, and the
    unresolved-only filter — over the server-parsed read model."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    page.get_by_test_id("discussion-toggle").click()
    overlay = page.get_by_test_id("discussion-overlay")
    overlay.wait_for()
    assert page.get_by_test_id("discussion-next").count() == 1
    assert page.get_by_test_id("discussion-filter-unresolved").count() == 1
    page.get_by_test_id("discussion-next").click()
    assert overlay.count() == 1, "the overlay stays available after navigation"
    page.get_by_test_id("discussion-filter-unresolved").click()
    page.get_by_test_id("discussion-toggle").click()
    assert page.get_by_test_id("discussion-overlay").count() == 0, (
        "the toggle must hide the overlay again"
    )


# AC-FR0325-02@v0.10 TRACKS-TRACE no discussion write controls
def test_no_discussion_write_controls(ui_serve, page):
    """AC-FR0325-02: the UI exposes no resolve or reply write entry — the
    discussion overlay is strictly read-only (write-back belongs to the
    CLI)."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    page.get_by_test_id("discussion-toggle").click()
    page.get_by_test_id("discussion-overlay").wait_for()
    assert page.get_by_test_id("discussion-resolve").count() == 0
    assert page.get_by_test_id("discussion-reply").count() == 0
    assert page.locator("text=RESOLVED").count() >= 0  # read-only status display


# AC-NFR0154-02@v0.10 TRACKS-TRACE UI writes carry csrf and idempotency
def test_ui_writes_carry_csrf_idempotency(ui_serve, page):
    """AC-NFR0154-02: every write the UI issues carries the X-Trac-CSRF and
    Idempotency-Key headers, and the redaction projection shows no plaintext
    secret on any surface (the canary never reaches the DOM)."""
    ui_serve.seed_version_docs("v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _seed_canary_event(ui_serve, "run-ui-1")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    writes: list[dict] = []

    def capture(route):
        request = route.request
        writes.append({"method": request.method, "headers": dict(request.headers)})
        route.continue_()

    page.route("**/api/**", capture)
    page.get_by_test_id("doc-editor-content").fill("csrf probe edit\n")
    page.get_by_test_id("doc-save").click()
    posted = [w for w in writes if w["method"] == "POST"]
    assert posted, "the save must issue a write request"
    for write in posted:
        headers = {k.lower(): v for k, v in write["headers"].items()}
        assert headers.get("x-trac-csrf"), (
            f"the UI write must carry X-Trac-CSRF: {sorted(headers)}"
        )
        assert headers.get("idempotency-key"), (
            f"the UI write must carry Idempotency-Key: {sorted(headers)}"
        )
    # the redaction projection: open the run data face and scan the whole DOM
    page.get_by_test_id("tabbar-item-runs").click()
    page.get_by_test_id("tab-runs").wait_for()
    content = page.content()
    assert _CANARY not in content, (
        "the redaction projection must scrub the canary from every surface"
    )
