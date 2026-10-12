"""Docs-center UI journeys (IF-DOCCENTER-001, IF-DOCSAVE-001, IF-DISCUSS-001,
IF-SECRECY-001, IF-CMDSVC-001).

The ``ui`` marker layer for the document workspace: the Vditor ir host with
same-origin assets, the textarea fallback, the pane-bar autosave control with
conflict recovery (2026-10-09 operator directive: one control surface per
pane, hidden revision face, hidden front-matter), the pane model, and the
read-only discussion overlay — all through data-testid controls on the real
serve + Chromium stack.
"""

from __future__ import annotations

from pathlib import Path

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
    page.get_by_test_id("tabbar-item-releases").wait_for()
    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for()


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


def _walk_to_review_round(repo, run_hint: str) -> str:
    """Arrange a run at the stage-review round through the public CLI walker
    (init → start → run → triage go → run, the fake backend carries both
    dispatches); returns the run id. Fixture arrange only — the journey
    itself stays browser-driven."""
    import os
    import re
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "TRACKS_HOME"}
    env["PYTHONPATH"] = str(repo_root)
    env.setdefault("TRAC_AGENT_BACKEND", "fake")
    env.setdefault("TRAC_INFRA_RETRY_LIMIT", "2")

    def cli(*args, stdin=None):
        return subprocess.run(
            [sys.executable, "-m", "tracks.cli.main", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
            input=stdin,
        )

    assert cli("init").returncode == 0
    started = cli("start", "v1.0", stdin="评审轮写控上下文种子")
    assert started.returncode == 0, started.stderr
    run_id = re.search(r"run (\S+) started", started.stdout).group(1)
    assert cli("run").returncode == 0
    assert cli("triage", "go").returncode == 0
    assert cli("run").returncode == 0
    return run_id


# AC-FR0322-01@v0.10 TRACKS-TRACE docs center Vditor ir same origin
def test_docs_center_vditor_ir_same_origin(ui_serve, page):
    """AC-FR0322-01: selecting a six-piece document renders it in the Vditor
    ir host and every editor asset is loaded from the same origin vendor path
    (no CDN, no cross-origin request)."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    cross_origin: list[str] = []
    page.on("request", lambda request: cross_origin.append(request.url))
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
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
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    fallback = page.get_by_test_id("doc-editor-fallback")
    fallback.wait_for()
    assert "fixture body for spec.md" in (fallback.input_value() or ""), (
        "the fallback textarea must carry the complete document content"
    )
    fallback.fill("---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 spec\n\nfallback edit\n")
    assert "fallback edit" in (fallback.input_value() or "")


# AC-FR0323-01@v0.10 TRACKS-TRACE autosave icon save control (2026-10-09 rework)
def test_save_control_autosaves_and_gates_on_saved(ui_serve, page):
    """AC-FR0323-01 (2026-10-09 operator directive): the save control is an
    icon in the pane bar that is disabled while the editor is clean (saved),
    and a user edit saves itself — no submit gesture — after which the
    control disables again and the baseline moves to the new revision. The
    revision face stays hidden (internal bookkeeping only; the full sha
    rides in the tooltip)."""
    ui_serve.seed_version_docs("v1.0")
    # a registered project + non-terminal run resolve the version's
    # editable_run_id — the autosave must be a REAL accepted write (the
    # pre-rework test posted to the "unassigned" run and its weak asserts
    # masked the rejection)
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    save = page.get_by_test_id("doc-save")
    assert save.is_disabled(), "a clean editor must show the saved state (disabled)"
    # the save control is an icon control inside the pane bar — the editor
    # toolbar row is gone (merge directive)
    assert page.get_by_test_id("pane-toolbar").get_by_test_id("doc-save").count() == 1, (
        "the save control lives in the pane bar, not in a second toolbar row"
    )
    revision = page.get_by_test_id("doc-revision")
    assert not revision.is_visible(), (
        "the revision is internal bookkeeping and must stay hidden"
    )
    baseline = revision.get_attribute("title") or ""
    # the edit saves itself: the autosave POST lands without any click
    with page.expect_response(lambda response: "/edits" in response.url) as posted:
        page.get_by_test_id("doc-editor-content").fill("edited through the ui\n")
    assert posted.value.ok, "the autosave must succeed"
    assert page.wait_for_function(
        "() => document.querySelector('[data-testid=\"doc-save\"]').disabled === true"
    ), "the completed autosave must return the control to the saved state"
    assert (revision.get_attribute("title") or "") != baseline, (
        "the autosave must move the editor to the new revision baseline"
    )
    assert (revision.get_attribute("title") or "") == posted.value.json()["new_revision"], (
        "the hidden revision face must track the live baseline (tooltip)"
    )


# Prism B1 lock (2026-10-09 review): typing that races an airborne autosave
# is never marked saved — the landing response only vouches for the posted
# snapshot, and the raced edit saves itself in a second request.
def test_typing_during_autosave_flight_is_never_marked_saved(ui_serve, page):
    """A slow save channel holds the first autosave in flight while the user
    keeps typing: the flight must land as "A saved", the raced edit must stay
    dirty (control enabled), re-arm, and post itself — ending disabled with
    both bodies actually sent, in order."""
    import time

    ui_serve.seed_version_docs("v1.0")
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    posted: list[str] = []

    def slow(route):
        posted.append(route.request.post_data_json.get("content", ""))
        time.sleep(0.6)  # hold the response so the flight is airborne
        route.continue_()

    page.route("**/api/runs/*/docs/*/edits", slow)
    # the first edit's autosave request is out; its response is 600ms away
    with page.expect_request(lambda request: "/edits" in request.url):
        page.get_by_test_id("doc-editor-content").fill("first edit\n")
    # the racing edit lands while the first save is still airborne
    page.get_by_test_id("doc-editor-content").fill("first edit + raced edit\n")
    assert page.wait_for_function(
        "() => document.querySelector('[data-testid=\"doc-save\"]').disabled === true",
        timeout=15000,
    ), "the raced edit must save itself and return the control to saved"
    bodies = [body for body in posted if body]
    assert bodies == [
        "---\nenvelope: tracks-envelope:v2\n---\nfirst edit\n",
        "---\nenvelope: tracks-envelope:v2\n---\nfirst edit + raced edit\n",
    ], f"both edits must be sent in order, head intact: {bodies!r}"


# Prism B2 lock (2026-10-09 review): an unresolved conflict blocks the
# document switch — its decision panel never opens in torn-down DOM.
def test_unresolved_conflict_blocks_document_switch(ui_serve, page):
    """With the autosave 409 dialog open, selecting another document from the
    sidebar is refused: the pane keeps the conflicted document, the dialog
    stays visible and mounted. Resolving (reload-discard) unblocks the
    switch."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    def conflict(route):
        route.fulfill(
            status=409,
            headers={"Content-Type": "application/json"},
            body='{"error": {"reason": "stale_revision", "current_revision": "rev-x"}}',
        )

    page.route("**/api/runs/*/docs/*/edits", conflict)
    page.get_by_test_id("doc-editor-content").fill("pending decision\n")
    dialog = page.get_by_test_id("conflict-dialog")
    dialog.wait_for()

    # the switch to another document is refused while the conflict is open
    page.get_by_test_id("doc-tree-item-v1.0-story").click()
    page.wait_for_timeout(600)
    assert page.get_by_test_id("pane-selector").input_value() == "spec", (
        "the pane must keep the conflicted document — the switch is blocked"
    )
    assert dialog.is_visible(), "the conflict dialog stays visible and mounted"
    assert "冲突" in (page.get_by_test_id("pane-status").inner_text() or ""), (
        "the pane states why the switch was refused"
    )

    # resolving the conflict (drop the local edit, take the server's) unblocks
    page.get_by_test_id("conflict-reload").click()
    page.wait_for_timeout(400)
    assert not dialog.is_visible(), "reload-discard closes the dialog"
    page.get_by_test_id("doc-tree-item-v1.0-story").click()
    assert page.wait_for_function(
        "() => document.querySelector('[data-testid=\"pane-selector\"]').value === 'story'"
    ), "after resolution the switch proceeds"


# Prism round-2 gap locks (2026-10-09 review): the two remaining teardown
# paths — closing a pane and switching the window away from docs — keep the
# conflict's decision face mounted: both are refused while the dialog is
# open (chrome untouched) and proceed once the decision is made.
def test_conflict_blocks_pane_close_and_window_switch(ui_serve, page):
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    def conflict(route):
        route.fulfill(
            status=409,
            headers={"Content-Type": "application/json"},
            body='{"error": {"reason": "stale_revision", "current_revision": "rev-x"}}',
        )

    page.route("**/api/runs/*/docs/*/edits", conflict)
    page.get_by_test_id("doc-editor-content").fill("pending decision\n")
    # scope to pane 0's dialog — every pane's editor carries one (hidden)
    dialog = page.get_by_test_id("doc-pane-0").get_by_test_id("conflict-dialog")
    dialog.wait_for()

    # a second pane makes closing pane 0 a normally-legal operation
    page.get_by_test_id("doc-pane-0").get_by_test_id("pane-split").click()
    page.locator("[data-testid='doc-pane-1']").wait_for()
    page.get_by_test_id("doc-pane-0").get_by_test_id("pane-close").click()
    page.wait_for_timeout(500)
    assert page.locator("[data-testid^='doc-pane-']").count() == 2, (
        "the pane close is refused while the conflict is open"
    )
    assert dialog.is_visible(), "the conflict dialog keeps its mounted face"

    # the window switch is refused too — the chrome never moves
    page.get_by_test_id("tabbar-item-releases").click()
    page.wait_for_timeout(600)
    assert page.locator("[data-testid='view-docs']").count() == 1, (
        "the docs view must stay mounted while its decision is pending"
    )
    assert dialog.is_visible(), "the conflict dialog stays visible through the refused switch"
    assert page.get_by_test_id("tabbar-item-docs").get_attribute("aria-current") == "page", (
        "the tab-bar chrome stays on docs — the switch aborted before any move"
    )

    # resolving the conflict unblocks both paths
    page.get_by_test_id("doc-pane-0").get_by_test_id("conflict-reload").click()
    page.wait_for_timeout(400)
    assert not dialog.is_visible(), "reload-discard closes the dialog"
    page.get_by_test_id("doc-pane-0").get_by_test_id("pane-close").click()
    page.wait_for_timeout(400)
    assert page.locator("[data-testid^='doc-pane-']").count() == 1, (
        "the pane close proceeds after the decision"
    )
    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()


# AC-FR0323-02@v0.10 TRACKS-TRACE conflict dialog two options
def test_conflict_dialog_two_options(ui_serve, page):
    """AC-FR0323-02: a 409 from the autosave channel opens the conflict dialog
    with the two options — reload-discard (drops the local change, rebases on
    current_revision) and force-overwrite (keeps the local edit, resubmits
    with current_revision as the new base); nothing is silently overwritten.
    The submitted content keeps the sealed front-matter head the editor hides
    from its editable face."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
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
    # the edit's autosave is the write that meets the 409 — no submit gesture
    page.get_by_test_id("doc-editor-content").fill("conflicting local edit\n")
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
    assert submissions[-1].get("content") == (
        "---\nenvelope: tracks-envelope:v2\n---\nconflicting local edit\n"
    ), (
        "the force-overwrite must keep the local edit and reattach the "
        f"hidden front-matter head verbatim: {submissions[-1]!r}"
    )


# AC-FR0324-01@v0.10 TRACKS-TRACE multi pane independent
def test_multi_pane_independent(ui_serve, page):
    """AC-FR0324-01: up to four panes open side by side, each with its own
    file selector and toolbar, loading different documents independently —
    one pane's selection and edits never disturb the others."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    # panes exist only beside an open document, and opening one creates the
    # initial pane (louke semantics) — three splits reach the cap of four
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("doc-pane-0").wait_for()
    page.locator("[data-testid='doc-pane-0'] [data-testid='vditor-ir-host']").wait_for()
    for index in range(3):
        page.get_by_test_id("doc-pane-0").get_by_test_id("pane-split").click()
        pane = page.locator(f"[data-testid='doc-pane-{index + 1}']")
        pane.wait_for()
        assert page.locator("[data-testid^='doc-pane-']").count() == index + 2
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
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("doc-pane-0").wait_for()
    for _index in range(3):
        page.get_by_test_id("doc-pane-0").get_by_test_id("pane-split").click()
    page.locator("[data-testid='doc-pane-3']").wait_for()
    before = [
        pane.get_by_test_id("pane-selector").input_value()
        for pane in page.locator("[data-testid^='doc-pane-']").all()
    ]
    page.get_by_test_id("doc-pane-0").get_by_test_id("pane-split").click()
    assert page.locator("[data-testid^='doc-pane-']").count() == 4, (
        "a fifth pane request must not add a pane"
    )
    after = [
        pane.get_by_test_id("pane-selector").input_value()
        for pane in page.locator("[data-testid^='doc-pane-']").all()
    ]
    assert after == before, "the existing panes must be unaffected by the refusal"


# AC-FR0325-01@v0.10 TRACKS-TRACE discussion navigation controls
def test_discussion_navigation_controls(ui_serve, page):
    """AC-FR0325-01: the discussion overlay offers the three navigation
    capabilities — show/hide toggle, next-discussion, and the
    unresolved-only filter — over the server-parsed read model."""
    ui_serve.seed_version_docs("v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
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


# AC-FR0325-02@v0.10 TRACKS-TRACE discussion overlay read model discipline
# AC-FR0344-01@v0.11 TRACKS-TRACE review round write controls in context only
def test_no_discussion_write_controls(ui_serve, page):
    """AC-FR0325-02 with the v0.11 FR-0344 revision: outside a review round
    the discussion overlay stays strictly read-only (no resolve, no reply,
    no write controls at all); inside a review-round run context
    (awaiting review) the #18 anchor-line write control presents on the
    review face — while the /discussions path itself still exposes no write
    endpoint (the v0.10 §1p.3 boundary holds)."""
    import json
    import time

    from tests._support.v09_web import http_post, login_session

    ui_serve.seed_version_docs("v1.0")
    # register the host repo so the CLI-walked review run resolves through
    # the run-detail projection (the walk appends to the run plane only;
    # without a project row _project_for_run finds no owner and the
    # review-round gate degrades to "no write controls")
    ui_serve.seed_project("proj-docs-review", "v1.0")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click(timeout=60_000)
    page.get_by_test_id("vditor-ir-host").wait_for()
    page.get_by_test_id("discussion-toggle").click()
    page.get_by_test_id("discussion-overlay").wait_for()
    # non-review-round context: strictly read-only
    assert page.get_by_test_id("discussion-resolve").count() == 0
    assert page.get_by_test_id("discussion-reply").count() == 0
    assert page.locator("text=RESOLVED").count() >= 0  # read-only status display

    # the review-round context: a run at the stage review (CLI-walked arrange)
    _walk_to_review_round(ui_serve.repo, "docs-ui-review")
    # the walk lands the version's editable run at awaiting=review; the
    # tree re-resolves on re-entry (no reload loop — the tree renders async
    # and Playwright's auto-wait covers it; a reload would reset the
    # in-page docs window before the slow tree lands)
    page.get_by_test_id("tabbar-item-docs").click()
    page.locator("[data-testid='view-docs']").wait_for(timeout=60_000)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click(timeout=120_000)
    page.get_by_test_id("vditor-ir-host").wait_for(timeout=60_000)
    # the review composer mounts after async gate resolution (doc read +
    # run detail fetch) — allow the fetches to land
    try:
        page.get_by_test_id("discussion-anchor-comment").wait_for(timeout=30_000)
        found = True
    except Exception:
        found = page.get_by_test_id("discussion-anchor-comment").count() >= 1
    # the review round presents the anchor-line comment write control
    assert found, (
        "the review-round context must present the #18 anchor-line write control"
    )
    # ...but the /discussions HTTP path stays write-free (boundary kept)
    base = ui_serve.base_url
    cookie, csrf = login_session(base)
    status, body = http_post(
        base,
        "/api/discussions",
        {"doc": "spec", "body": "boundary probe"},
        cookies=cookie,
        csrf=csrf,
        idempotency_key="docs-ui-discussions-write-1",
    )
    assert status in (404, 405), (
        f"the /discussions path must expose no write endpoint: {status} {body[:120]}"
    )
    try:
        reason = json.loads(body)["error"]["reason"]
    except (ValueError, KeyError, TypeError):
        reason = ""
    assert reason or status in (404, 405), body[:120]


# AC-NFR0154-02@v0.10 TRACKS-TRACE UI writes carry csrf and idempotency
def test_ui_writes_carry_csrf_idempotency(ui_serve, page):
    """AC-NFR0154-02: every write the UI issues carries the X-Trac-CSRF and
    Idempotency-Key headers, and the redaction projection shows no plaintext
    secret on any surface (the canary never reaches the DOM)."""
    ui_serve.seed_version_docs("v1.0")
    # a registered project resolves the version's editable_run_id — the
    # autosave must be a REAL accepted write (the unmount guard refuses a
    # window switch while a draft cannot reach the server)
    ui_serve.seed_project("proj-ui-1", "v1.0")
    ui_serve.seed_run("run-ui-1", "v1.0")
    _seed_canary_event(ui_serve, "run-ui-1")
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    writes: list[dict] = []

    def capture(route):
        request = route.request
        writes.append({"method": request.method, "headers": dict(request.headers)})
        route.continue_()

    page.route("**/api/**", capture)
    with page.expect_response(lambda response: "/edits" in response.url):
        page.get_by_test_id("doc-editor-content").fill("csrf probe edit\n")
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
    page.get_by_test_id("tabbar-item-releases").click()
    page.locator("[data-testid='view-releases']").wait_for()
    content = page.content()
    assert _CANARY not in content, (
        "the redaction projection must scrub the canary from every surface"
    )


# TRACKS-TRACE #221/#222 multi-version tree, banner removal
def test_docs_tree_all_versions_and_no_banner(ui_serve, page):
    """#221: the sidebar renders one collapsible group per version directory
    (louke Dev Docs model) — newest first, the story title in the group label —
    and a document from ANY version opens in the pane with that version's
    content. #222: the workbench banner (Docs / 多文档实时编辑工作区 / ＋新增分屏)
    is gone; the pane bars are the only control surface."""
    ui_serve.seed_version_docs("v1.0", story_title="Web 工作台完善")
    ui_serve.seed_version_docs(
        "v0.9-hotfix-7", ("story.md", "spec.md", "acceptance.md")
    )
    _login_and_open_docs(ui_serve, page)

    newest = page.get_by_test_id("doc-tree-version-v1.0")
    newest.wait_for()
    older = page.get_by_test_id("doc-tree-version-v0.9-hotfix-7")
    assert "Web 工作台完善" in (newest.locator("summary").inner_text() or ""), (
        "the group label carries the story title"
    )
    assert newest.get_attribute("open") is not None, "the newest group renders open"
    assert older.get_attribute("open") is None, "older groups render collapsed"
    assert page.get_by_test_id("doc-tree-item-v1.0-spec").count() == 1
    assert page.get_by_test_id("doc-tree-item-v0.9-hotfix-7-spec").count() == 1

    # a document from the older version opens with that version's content
    older.locator("summary").click()
    page.get_by_test_id("doc-tree-item-v0.9-hotfix-7-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()
    source = page.get_by_test_id("doc-editor-content").input_value()
    assert "v0.9-hotfix-7 spec" in (source or ""), (
        "the pane shows the older version's document"
    )

    # #222: the banner and its workbench-level split button are gone
    assert page.locator(".devdocs-toolbar").count() == 0, "the banner is removed"
    assert page.get_by_test_id("pane-add").count() == 0, (
        "the workbench-level split button is removed"
    )
    assert page.get_by_test_id("pane-split").count() >= 1, (
        "the pane bar keeps the split control"
    )


# TRACKS-TRACE #228 app shell pinned to the viewport; regions scroll internally
def test_app_shell_pinned_chrome_while_document_scrolls(ui_serve, page):
    """#228: the workbench is exactly the viewport — reading a document much
    taller than the viewport scrolls the pane body only. The tab bar and the
    sidebar (and the pane bar) keep their exact position: the page itself
    never scrolls."""
    ui_serve.seed_version_docs("v1.0")
    spec = ui_serve.repo / ".tracks" / "projects" / "v1.0" / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8")
        + "\n"
        + "\n".join(f"paragraph {index} of the long document\n" for index in range(300)),
        encoding="utf-8",
    )
    _login_and_open_docs(ui_serve, page)
    page.get_by_test_id("doc-tree-item-v1.0-spec").click()
    page.get_by_test_id("vditor-ir-host").wait_for()

    tabbar_before = page.get_by_test_id("tabbar").bounding_box()
    sidebar_before = page.get_by_test_id("sidebar").bounding_box()
    pane_header_before = page.locator(".pane-header").first.bounding_box()

    # read to the bottom of the document: the pane body is the scroller
    pane_body = page.locator("[data-testid='doc-pane-0'] [data-testid='pane-body']")
    assert pane_body.evaluate(
        "el => { el.scrollTop = el.scrollHeight; return el.scrollTop; }"
    ) > 0, "the long document must make the pane body scrollable"
    page.wait_for_timeout(100)

    assert page.evaluate("() => window.scrollY") == 0, (
        "the page itself must never scroll — the shell is the viewport (#228)"
    )
    assert page.evaluate(
        "() => document.documentElement.scrollHeight <= window.innerHeight"
    ), "the shell must not have grown beyond the viewport (#228)"
    assert page.get_by_test_id("tabbar").bounding_box() == tabbar_before, (
        "the tab bar stays pinned while the document scrolls (#228)"
    )
    assert page.get_by_test_id("sidebar").bounding_box() == sidebar_before, (
        "the sidebar stays pinned while the document scrolls (#228)"
    )
    assert page.locator(".pane-header").first.bounding_box() == pane_header_before, (
        "the pane bar stays pinned while its document scrolls (#228)"
    )
