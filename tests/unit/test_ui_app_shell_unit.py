"""#228 app-shell layout contract: the workbench pins to the viewport.

The browser-side layer has no JavaScript runner by design (architecture §4.3),
so the app-shell contract is pinned statically on ``styles.css`` the same way
T-005 pins the ES-module contracts: every structural rule below is a real
assertion over the shipped stylesheet, and the behavioural half (the chrome
stays pinned while a long document scrolls) lives in the ui e2e layer
(``tests/e2e/test_docs_ui.py::test_app_shell_pinned_chrome_while_document_scrolls``).
"""

from __future__ import annotations

import re
from pathlib import Path

_STYLES = (
    Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "styles.css"
)


def _css() -> str:
    """The shipped stylesheet; a missing file fails as an assertion."""
    assert _STYLES.is_file(), "tracks/server/static/styles.css must ship"
    return _STYLES.read_text(encoding="utf-8")


def _block(css: str, selector: str) -> str:
    """The declaration block of one exact selector line (braces balanced)."""
    match = re.search(
        re.escape(selector) + r"\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert match is not None, f"styles.css must keep the {selector!r} rule"
    return match.group(1)


# AC-#228@v0.10 TRACKS-TRACE IF-WORKBENCH-001 the shell is exactly the viewport
def test_workbench_is_pinned_to_the_viewport_and_never_scrolls():
    css = _css()
    workbench = _block(css, ".workbench")
    assert "grid-template-rows: minmax(0, 1fr)" in workbench, (
        "the single grid row must allow children to shrink below their "
        "content so each region owns its scrollbar (#228)"
    )
    assert "height: 100dvh" in workbench and "height: 100vh" in workbench, (
        "the shell is exactly the viewport height (100vh fallback + 100dvh) (#228)"
    )
    assert "overflow: hidden" in workbench, (
        "the page itself never scrolls — regions scroll internally (#228)"
    )
    assert "min-height: 100vh" not in workbench, (
        "a growing shell reintroduces page-level scrolling (#228)"
    )


# AC-#228@v0.10 TRACKS-TRACE IF-WORKBENCH-001 the chrome regions scroll internally
def test_chrome_regions_own_their_scrollbars():
    css = _css()
    assert "overflow-y: auto" in _block(css, ".tabbar"), (
        "the tab bar scrolls internally if it ever outgrows the viewport (#228)"
    )
    assert "overflow-y: auto" in _block(css, ".sidebar"), (
        "the sidebar (version tree, run registry) scrolls internally (#228)"
    )
    main_body = _block(css, ".main-body")
    assert "flex: 1" in main_body and "min-height: 0" in main_body, (
        "the main window column fills the viewport slice left by the header (#228)"
    )
    view = _block(css, ".view")
    assert "overflow-y: auto" in view, (
        "generic views scroll inside the main window, not the page (#228)"
    )


# AC-#228@v0.10 TRACKS-TRACE IF-DOCCENTER-001 the document pane scrolls, not the app
def test_document_pane_scrolls_in_its_body():
    css = _css()
    docs_view = _block(css, '.view[data-testid="view-docs"]')
    assert "overflow: hidden" in docs_view, (
        "the docs workspace delegates scrolling to its panes (#228)"
    )
    panes = _block(css, ".panes")
    assert "grid-template-rows: minmax(0, 1fr)" in panes, (
        "the pane row is bounded by the viewport slice, not the document (#228)"
    )
    pane = _block(css, ".pane")
    assert re.search(r"min-height:\s*0", pane), (
        "the pane must not grow with its document — otherwise the whole pane "
        "(including its bar) scrolls away (#228)"
    )
    assert "min-height: 480px" not in pane, (
        "the fixed pane floor grew the pane past the viewport slice (#228)"
    )
    pane_body = _block(css, ".pane-body")
    assert "overflow: auto" in pane_body and "min-height: 0" in pane_body, (
        "the pane body is the scrolling surface for the document (#228)"
    )
