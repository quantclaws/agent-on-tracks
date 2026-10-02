"""T-005 RED: SPA application-layer exit contracts under IF-WEBUI-001.

Devon-owned unit RED for the v0.10 browser-side application layer
(``tracks/server/static/app/``, interfaces §1l / §3 #2; architecture §1.0.1
module list: shell/router/api/sse/tabs/sidebar/views/editor/panes/discussions/
timeline). The layer has no JavaScript runner by design -- architecture §4.3
records the explicit no-Node-toolchain decision, and test-plan §1.5 keeps the
unit layer Devon-owned -- so the task's legal Red face pins the module exit
contracts statically:

- ``shell.js`` bootstraps the native ES-module graph and ``router.js`` restores
  the server ``data-route`` deep link (interfaces §1l.1/§1l.2);
- ``api.js`` injects ``X-Trac-CSRF`` and ``Idempotency-Key`` on writes, raises
  on HTTP failures and never swallows them (interfaces §1l.6, §2b contract);
- ``sse.js`` backfills from ``event_cursor`` and rejects duplicate/out-of-order
  events so the projection never regresses (interfaces §1l.6);
- ``tabs.js`` implements SM-04: one atomic sidebar+main switch, coexisting
  unique instances, explicit close as the only close path, memory-only state
  (interfaces §1l.4);
- the docs view mounts the vendored Vditor ``ir`` host from the same origin and
  falls back to ``textarea`` when loading fails (interfaces §1n.3);
- ``editor.js`` consumes the 409 top-level ``current_revision`` for the
  reload-discard / force-overwrite options and retains the draft (interfaces
  §1o.2);
- ``panes.js`` keeps panes independent and caps them at four columns
  (interfaces §1n.3);
- ``discussions.js`` is read-only navigation (toggle / next / unresolved-only)
  with no write entry (interfaces §1p);
- ``timeline.js`` consumes the server ``stage_order`` / ``ac-chain`` instead of
  hardcoding the stage set (interfaces §1q).

RED discipline: no module exists yet under ``tracks/server/static/app``, so
every node guards the missing exit contracts into a real ``AssertionError``
(file-read guards and token/structural asserts -- no collection/import errors,
no stub tokens, no assembly errors). Behavioural verification stays with the
frozen ui e2e journeys (test-plan §8 layering).

AC: FR-0322-02, FR-0324-01, FR-0324-02, NFR-0155-01 — TRACKS-TRACE
IF-WEBUI-001 (consuming IF-WORKBENCH-001 / IF-DOCCENTER-001 / IF-DOCSAVE-001 /
IF-DISCUSS-001 / IF-STREAM-001 / IF-TIMELINE-001).
"""

from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "app"


def _module(name: str) -> str:
    """Source of one native ES module; a missing module fails as an assertion."""
    path = _APP / name
    assert path.is_file(), (
        f"tracks/server/static/app/{name} must exist: the v0.10 SPA ships native "
        "ES modules with no build chain (interfaces §1l.2, §3 #2)"
    )
    return path.read_text(encoding="utf-8")


def _sources() -> str:
    """Concatenated source of every app module (cross-module contract scans)."""
    assert _APP.is_dir(), (
        "tracks/server/static/app must exist as the SPA application layer "
        "(interfaces §1l.2)"
    )
    modules = sorted(path for path in _APP.rglob("*.js") if path.is_file())
    assert modules, "the SPA application layer must ship at least one ES module"
    return "\n".join(path.read_text(encoding="utf-8") for path in modules)


# -- shell bootstrap + router deep link (FR-0316, interfaces §1l.1/1l.2) ------


# AC-FR0316-01@v0.10 TRACKS-TRACE IF-WORKBENCH-001 shell bootstraps the native ES-module app
def test_shell_bootstraps_native_esm_and_wires_the_module_graph():
    shell = _module("shell.js")
    assert "import " in shell, "shell.js must be a native ES module (interfaces §1l.2)"
    for dependency in ("./router.js", "./tabs.js", "./sidebar.js", "./views.js"):
        assert dependency in shell, (
            f"shell.js must wire {dependency} (architecture §1.0.1 module graph)"
        )
    assert "bootstrap" in shell, (
        "shell.js exposes the bootstrap entry the workbench shell HTML calls (§1l.2)"
    )


# AC-FR0316-01@v0.10 TRACKS-TRACE IF-WORKBENCH-001 router restores the data-route deep link
def test_router_restores_data_route_deep_link():
    router = _module("router.js")
    assert "data-route" in router, (
        "router.js reads the server data-route deep link (interfaces §1l.1)"
    )
    assert "dataset" in router, (
        "router.js restores tab/sidebar from the shell dataset attributes (§1l.1)"
    )


# -- api client: write safety + observable failure (interfaces §1l.6) ---------


# AC-NFR0154-02@v0.10 TRACKS-TRACE IF-CMDSVC-001 UI writes carry X-Trac-CSRF and Idempotency-Key
def test_api_client_injects_csrf_and_idempotency_on_mutations():
    api = _module("api.js")
    assert "X-Trac-CSRF" in api, "every UI write carries X-Trac-CSRF (interfaces §1l.6)"
    assert "Idempotency-Key" in api, (
        "every UI write carries Idempotency-Key (interfaces §1l.6)"
    )
    assert "fetch(" in api, "api.js is the same-origin fetch client (§1l.2)"
    assert re.search(r"\b(POST|PUT|PATCH|DELETE)\b", api), (
        "api.js gates the write headers on the mutation methods (§1l.6)"
    )


# AC-NFR0153-02@v0.10 TRACKS-TRACE IF-WORKBENCH-001 API failures surface instead of false success
def test_api_client_raises_on_http_failure_without_swallowing():
    api = _module("api.js")
    assert "response.ok" in api or ".status" in api, (
        "api.js inspects the response status so failures are observable (§1l.6)"
    )
    assert "throw" in api, "api.js surfaces failures by throwing, never reporting success"
    swallowed = re.search(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}", api)
    assert swallowed is None, "api.js must not swallow failures in an empty catch block"


# -- sse client: cursor backfill + monotonic projection (interfaces §1l.6) ----


# AC-NFR0154-01@v0.10 TRACKS-TRACE IF-STREAM-001 sse backfills by event_cursor
def test_sse_client_backfills_by_event_cursor():
    sse = _module("sse.js")
    assert "event_cursor" in sse, "reconnect backfill resumes from event_cursor (§1l.6)"
    assert "EventSource" in sse, "sse.js consumes the existing event stream transport"
    assert "lastEventCursor" in sse, "sse.js tracks the last applied cursor"


# AC-NFR0154-01@v0.10 TRACKS-TRACE IF-STREAM-001 duplicate/out-of-order events never regress
def test_sse_client_ignores_non_advancing_events():
    sse = _module("sse.js")
    guard = re.search(r"if\s*\([^)]*lastEventCursor[^)]*\)", sse)
    assert guard is not None, (
        "sse.js must reject duplicate/out-of-order events with a cursor guard (§1l.6)"
    )


# -- tabs + sidebar SM-04 (FR-0318, interfaces §1l.4) ------------------------


# AC-FR0318-01@v0.10 TRACKS-TRACE IF-WORKBENCH-001 SM-04 atomic sidebar+main switch
def test_tab_activation_switches_sidebar_and_main_area_together():
    tabs = _module("tabs.js")
    assert "activateTab" in tabs, "tabs.js exposes the SM-04 activation seam"
    assert "sidebar" in tabs, "activation updates the sidebar in the same switch (§1l.4)"
    assert "main-area" in tabs, "activation opens/activates the main-area tab (§1l.4)"


# AC-FR0318-01@v0.10 TRACKS-TRACE IF-WORKBENCH-001 coexisting tabs, unique instance, explicit close
def test_tabs_reuse_instances_and_close_only_on_explicit_action():
    tabs = _module("tabs.js")
    assert "openTab" in tabs, "tabs.js opens new tabs on demand"
    assert re.search(r"\b(?:find|some|has)\s*\(", tabs), (
        "openTab reuses the existing unique tab instance for a route (§1l.4)"
    )
    assert "closeTab" in tabs, "user close is the only close path (§1l.4)"
    assert "closeAll" not in _sources(), "no global close-all action exists (§1l.4)"


# AC-FR0318-02@v0.10 TRACKS-TRACE IF-WORKBENCH-001 tab state is memory-only
def test_tab_state_is_memory_only():
    sources = _sources()
    for storage in ("localStorage", "sessionStorage", "indexedDB"):
        assert storage not in sources, (
            f"tab state must not persist across sessions ({storage} is forbidden, §1l.4)"
        )


# -- docs view + editor + panes (FR-0322-02 / FR-0323-02 / FR-0324) ----------


# AC-FR0322-02@v0.10 TRACKS-TRACE IF-DOCCENTER-001 Vditor ir host with textarea fallback
def test_docs_view_mounts_vditor_ir_same_origin_with_textarea_fallback():
    sources = _sources()
    assert "/static/vendor/vditor/" in sources, (
        "the docs view loads the vendored Vditor build from the same origin (§1n.3)"
    )
    assert "Vditor" in sources, "the docs view hosts Vditor on demand (§1n.3)"
    assert re.search(r"""["']ir["']""", sources), (
        "Vditor mounts in instant-rendering (ir) mode (§1n.3)"
    )
    assert "textarea" in sources, (
        "a Vditor load failure falls back to a textarea without losing content (§1n.3)"
    )


# AC-FR0323-02@v0.10 TRACKS-TRACE IF-DOCSAVE-001 409 options + draft retained
def test_editor_handles_409_with_top_level_current_revision():
    editor = _module("editor.js")
    assert "409" in editor, "editor.js handles the stale-revision conflict (§1o.2)"
    assert "current_revision" in editor, (
        "the 409 top-level current_revision drives the two recovery options (§1o.2)"
    )
    assert "base_revision" in editor, (
        "force-overwrite resubmits with current_revision as the new base_revision (§1o.2)"
    )
    assert "draft" in editor, "a failed write keeps the local draft retryable (§1o.2)"


# AC-FR0324-01@v0.10 TRACKS-TRACE IF-DOCCENTER-001 panes independent selector+toolbar
def test_panes_are_independent_with_own_selector_and_toolbar():
    panes = _module("panes.js")
    assert "toolbar" in panes, "each pane owns an independent toolbar (§1n.3)"
    assert "select" in panes, "each pane owns an independent document selector (§1n.3)"


# AC-FR0324-02@v0.10 TRACKS-TRACE IF-DOCCENTER-001 the fifth pane request is a no-op
def test_pane_cap_four_blocks_a_fifth_pane():
    panes = _module("panes.js")
    assert re.search(r"MAX_PANES\s*=\s*4", panes), "the pane cap is four columns (§1n.3)"
    assert re.search(r"if\s*\([^)]*MAX_PANES[^)]*\)", panes), (
        "addPane refuses a fifth column without mutating existing panes (§1n.3)"
    )


# -- discussions overlay (FR-0325) + timeline view (FR-0326) -----------------


# AC-FR0325-01@v0.10 TRACKS-TRACE IF-DISCUSS-001 discussion navigation three capabilities
# AC-FR0325-02@v0.10 TRACKS-TRACE IF-DISCUSS-001 read-only overlay, no write entry
def test_discussion_overlay_is_read_only_navigation():
    disc = _module("discussions.js")
    assert "entry_line" in disc, "discussion navigation anchors to entry_line (§1p.1)"
    assert "toggle" in disc, "the overlay toggles show/hide (§1p.2)"
    assert "nextThread" in disc, "next-discussion navigation exists (§1p.2)"
    assert "resolved" in disc, "the unresolved-only filter uses thread status (§1p.2)"
    assert not re.search(r"\b(POST|PUT|PATCH|DELETE)\b", disc), (
        "the overlay is read-only: no resolve/reply write entry (§1p.3)"
    )


# AC-FR0326-01@v0.10 TRACKS-TRACE IF-TIMELINE-001 timeline consumes stage_order/ac-chain
def test_timeline_view_consumes_server_stage_order_without_hardcoding():
    timeline = _module("timeline.js")
    assert "stage_order" in timeline, "timeline.js consumes the server stage_order (§1q.1)"
    assert "ac-chain" in timeline, "timeline.js reuses the existing ac-chain data (§1q.2)"
    assert "M-START" not in timeline, (
        "the client must not hardcode the stage set; it reads stage_order (§1q.1)"
    )


# -- NFR-0155-01 control-binding half (interfaces §1l.1) ---------------------


# AC-NFR0155-01@v0.10 TRACKS-TRACE IF-WEBUI-001 interactive controls bind data-testid locators
def test_interactive_controls_bind_data_testid_anchors():
    sources = _sources()
    assert "data-testid" in sources, "interactive controls carry data-testid (§1l.1)"
    assert "doc-save" in sources, "the explicit save control carries its testid (§1l.1)"
    assert "pane-add" in sources, "the add-pane control carries its testid (§1l.1)"
