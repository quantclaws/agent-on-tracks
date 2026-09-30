"""T-010 (v0.10 contract adaptation): page shells under IF-WORKBENCH-001.

Devon-owned unit adaptation per test-plan §11 #4 and architecture §1.0.1
(page-shell convergence): v0.10 renders two shells (interfaces §0.1 #1, §1l)
— ``login`` renders the dual-column auth shell (E-02 / §1m.1) and the seven
workbench entries render ONE shared workbench shell carrying the
``data-route`` deep link plus run_id/project_id parameters and the native
ES-module bootstrap (§1l.1/§1l.2). The material review entry renders the
shared shell too: the editor host moved to the docs view, which loads the
vendored Vditor build on demand (§1n.3), so the server shell no longer
inlines editor asset tags or material rows (FR-0303 boundary); the
same-origin intent of the editor assets is carried by the integration
same-origin cases and the ui e2e journey (test-plan §11 #4).

The closed-set regression half keeps its v0.9 AC anchors; the shell
convergence half anchors the v0.10 acceptance criteria it serves.

AC: FR-0316-02, FR-0318-03, FR-0320-01, FR-0321-01, FR-0321-02,
NFR-0153-01 — TRACKS-TRACE IF-WORKBENCH-001, IF-AUTHNAME-001, IF-DOCREV-001.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from tracks.server import pages

# Content-addressed revision ids (baseline.revision_digest semantics); the
# review caller hands them in via context and the shell must NOT inline them.
_REVISION_OLD = "1" * 64
_REVISION_NEW = "2" * 64
_DIFF_LINE = "+revision-two material line"

# The FR-0318 seven-entry function set of the tab bar in its fixed order.
_TAB_ORDER = ("projects", "runs", "docs", "review", "todos", "settings", "account")

_WORKBENCH_ENTRIES = tuple(name for name in pages.PAGES if name != "login")

_MODULE_BOOTSTRAP = '<script type="module" src="/static/app/shell.js"></script>'

_ASSET_REF_RE = re.compile(r'(?:src|href)="([^"]+)"')


def _calling(call: Callable[[], Any], label: str) -> Any:
    """Call a rendering seam; a NotImplementedError stub becomes an assertion."""
    try:
        return call()
    except NotImplementedError as exc:
        raise AssertionError(f"{label} is still a stub: {exc}") from None


def _doc_context() -> dict:
    """Material-review context: current revision, history and revision diff."""
    return {
        "run_id": "R-T010",
        "project_id": "P-T010",
        "doc": "spec",
        "revision": _REVISION_NEW,
        "content": "# SPEC-009\n\nreviewed material body\n",
        "history": [
            {"revision": _REVISION_OLD, "ts": "2026-09-20T10:00:00+00:00", "actor": "Human"},
            {"revision": _REVISION_NEW, "ts": "2026-09-20T10:05:00+00:00", "actor": "Human"},
        ],
        "diff": {
            "from": _REVISION_OLD,
            "to": _REVISION_NEW,
            "unified_diff": (
                "--- a/spec.md\n+++ b/spec.md\n@@ -1 +1 @@\n"
                "-first material line\n" + _DIFF_LINE + "\n"
            ),
        },
    }


def _render(name: str) -> str:
    html = _calling(lambda: pages.render_page(name, _doc_context()), f"render_page({name!r})")
    assert isinstance(html, str) and html.strip(), f"render_page({name!r}) must render HTML"
    return html


def _asset_refs(html: str) -> list[str]:
    """Every src/href reference the shell document makes."""
    return _ASSET_REF_RE.findall(html)


def _tab_segment(html: str, name: str) -> str:
    """The rendered markup of one tab bar entry, from its opening tag."""
    token = f'data-testid="tabbar-item-{name}"'
    start = html.rindex("<", 0, html.index(token))
    end = html.index("</a>", start) if html.startswith("<a", start) else html.index("</button>", start)
    return html[start:end]


# -- page shell: the closed PAGES set renders complete HTML documents --------


# AC-FR0294-01@v0.9 TRACKS-TRACE IF-DOCREV-001 closed-set pages render HTML shells
def test_pages_closed_set_renders_html_shells():
    assert len(pages.PAGES) == 8, "PAGES is the E-01..E-08 closed set"
    assert set(pages.PAGES) == {
        "login", "overview", "projects", "run_new",
        "run_detail", "review", "todos", "release",
    }, "PAGES must cover the interfaces §2b page list"
    for name in pages.PAGES:
        html = _render(name)
        lowered = html.lower()
        assert "<!doctype html" in lowered, f"{name} page must be a complete HTML document"
        assert "<html" in lowered and "</html>" in lowered, f"{name} page must close <html>"
        assert "<body" in lowered and "</body>" in lowered, f"{name} page must have a body"


# AC-FR0294-01@v0.9 TRACKS-TRACE IF-DOCREV-001 out-of-set page names rejected
def test_pages_reject_unknown_page_name():
    try:
        pages.render_page("not_a_page", _doc_context())
    except NotImplementedError as exc:
        raise AssertionError(f"render_page is still a stub: {exc}") from None
    except ValueError:
        return
    raise AssertionError("render_page must reject names outside the PAGES closed set")


# -- login (E-02): dual-column auth shell with inline error and name step -----


# AC-FR0320-01@v0.10 TRACKS-TRACE IF-WORKBENCH-001 login renders the dual-column auth shell
def test_login_renders_dual_column_auth_shell():
    html = _render("login")
    for anchor in (
        'data-testid="login-shell"',
        'data-testid="login-hero"',
        'data-testid="login-hero-credit"',
        'data-testid="login-panel"',
        'data-testid="login-form"',
        'data-testid="login-password"',
        'data-testid="login-submit"',
        'data-testid="login-error"',
    ):
        assert anchor in html, f"login shell must carry {anchor}"
    assert html.index("login-hero") < html.index("login-panel"), (
        "the hero column precedes the login panel (dual-column auth shell)"
    )
    assert 'action="/api/auth/login"' in html, "the credential form posts to the auth face"
    assert 'role="alert"' in html, "the login error line renders inline as an alert slot"
    for ref in _asset_refs(html):
        assert ref.startswith("/"), f"the auth shell stays same-origin: {ref}"


# AC-FR0321-01@v0.10 TRACKS-TRACE IF-AUTHNAME-001 login carries the name step anchors
# AC-FR0321-02@v0.10 TRACKS-TRACE IF-AUTHNAME-001 no registration surface on the auth shell
def test_login_carries_hidden_name_collection_step():
    html = _render("login")
    assert (
        '<form class="auth-form" data-testid="login-name-form" hidden>' in html
    ), "the name-collection step ships hidden; the client reveals it on name_required"
    assert 'data-testid="login-name"' in html, "the display-name input carries its anchor"
    assert 'data-testid="login-name-continue"' in html, "the continue control carries its anchor"
    assert 'maxlength="64"' in html, "the name input mirrors the §1m.2 length bound"
    assert "register" not in html.lower(), "the auth shell has no registration surface"


# -- workbench shell (E-01): one shared document with data-route deep link ----


# AC-FR0316-02@v0.10 TRACKS-TRACE IF-WORKBENCH-001 seven entries render one shared shell
def test_workbench_entries_render_shared_shell_with_data_route():
    segments = []
    for name in _WORKBENCH_ENTRIES:
        html = _render(name)
        assert f'data-route="{name}"' in html, f"{name} must carry its data-route deep link"
        assert _MODULE_BOOTSTRAP in html, f"{name} must bootstrap the native ES-module shell"
        for anchor in ('data-testid="tabbar"', 'data-testid="sidebar"', 'data-testid="main-area"'):
            assert anchor in html, f"{name} shell must carry {anchor}"
        body = html[html.index("<body") : html.index("</body>")]
        segments.append(body.replace(f'data-route="{name}"', "data-route=ROUTE"))
    assert len(set(segments)) == 1, "the seven workbench entries share one shell document"


# AC-NFR0153-01@v0.10 TRACKS-TRACE IF-WORKBENCH-001 shell deep-links, never inlines rows
def test_workbench_shell_carries_deep_link_without_material_rows():
    html = _calling(lambda: pages.render_page("review", _doc_context()), "render_page(review)")
    assert 'data-route="review"' in html, "the review entry renders the shared shell"
    assert 'data-run-id="R-T010"' in html, "the deep link carries the run parameter"
    assert 'data-project-id="P-T010"' in html, "the deep link carries the project parameter"
    for leaked in (_REVISION_OLD, _REVISION_NEW, _DIFF_LINE, "reviewed material body", "SPEC-009"):
        assert leaked not in html, "the shell must not inline material rows"


# AC-FR0316-02@v0.10 TRACKS-TRACE IF-WORKBENCH-001 review entry hosts no inline editor
def test_review_entry_renders_shell_without_inline_editor():
    html = _render("review")
    assert "/static/vendor/vditor/" not in html, (
        "the shell must not inline editor asset tags; the docs view loads "
        "the vendored build on demand (§1n.3, test-plan §11 #4)"
    )
    for ref in _asset_refs(html):
        assert ref.startswith("/"), f"shell references stay same-origin: {ref}"


# AC-FR0318-03@v0.10 TRACKS-TRACE IF-WORKBENCH-001 tab bar: seven icon entries, fixed order
def test_tabbar_renders_seven_fixed_icon_entries():
    html = _render("overview")
    positions = []
    for name in _TAB_ORDER:
        token = f'data-testid="tabbar-item-{name}"'
        assert token in html, f"the tab bar must carry the {name} entry"
        positions.append(html.index(token))
    assert positions == sorted(positions), "tab bar keeps the fixed seven-entry order (FR-0317)"
    for name in _TAB_ORDER:
        segment = _tab_segment(html, name)
        assert "<svg" in segment, f"the {name} entry is icon-only"
        assert f'title="{name.capitalize()}"' in segment, f"the {name} entry carries its tooltip"
        assert f'aria-label="{name.capitalize()}"' in segment, f"the {name} entry is labelled"
