"""Workbench page shells (interfaces §1e.1 / §1l; E-01, E-02).

v0.11 converges the server-rendered page surface to the six-name releases
world (IF-RELEASES-001 §1e.1 / IF-WORKBENCH-001 v0.11 revision,
FR-0333/FR-0334 breaking change, GO confirmed):

- ``login`` renders the dual-column auth shell (E-02 / §1m.1): the hero
  column (image + slogan + image attribution) next to the login panel
  (credential form + inline error line + the hidden name-collection step
  that the client reveals when the login response carries
  ``name_required`` — E-02, §1m.2). The auth shell also bootstraps the
  shared native ES-module entry (``/static/app/shell.js``, §1l.2), so the
  login/name flow runs in-page through the browser client;
- the workbench entries (E-01) render ONE shared workbench shell document
  carrying the ``data-route`` deep link plus run_id/project_id parameters,
  the three-zone chrome skeleton (tab bar + sidebar + main area) and the
  icon-only five-item tab bar in its fixed order — releases/docs/todos/
  settings/account (§1e.1; the v0.10 seven-item set loses the projects,
  runs and review entries). All assets stay same-origin under ``/static/``;
  the shell never inlines material rows or secrets (§1g.3, FR-0303
  boundary).

The removed v0.10 URLs never render a page here — ``app.py`` answers them
with 302 to ``/`` so deep links do not dangle. The docs view keeps loading
the vendored Vditor build from the same origin on demand (§1n.3).

Contract tokens: IF-RELEASES-001, IF-WORKBENCH-001, IF-AUTHNAME-001.
"""

from __future__ import annotations

import html

# Closed set of server-rendered pages (IF-RELEASES-001 §1e.1): the six-name
# releases world — '/' is the releases shell and /docs is the docs deep link.
PAGES = (
    "login",
    "releases",
    "run_detail",
    "todos",
    "release",
    "docs",
)

_TITLES = {
    "login": "tracks - login",
    "releases": "tracks - releases",
    "run_detail": "tracks - run detail",
    "todos": "tracks - todo center",
    "release": "tracks - release",
    "docs": "tracks - docs",
}

# The §1e.1 five-item function set of the tab bar in its fixed,
# non-draggable order (FR-0317), each entry paired with its deep-link
# target — the link tabs carry the URL-table entries, while
# Settings/Account open in-page surfaces and render as buttons, not links.
_TABBAR_ITEMS = (
    ("releases", "/"),
    ("docs", "/docs"),
    ("todos", "/todos"),
    ("settings", None),
    ("account", None),
)

# Inline icon bodies (no xmlns: an HTML-embedded SVG needs none, and shell
# assets stay free of any external reference).
_TABBAR_ICONS = {
    "releases": (
        '<path d="M20.6 13.4l-7.2 7.2a2 2 0 01-2.8 0L3 13V3h10l7.6 7.6a2 2 0 010 2.8z"/>'
        '<circle cx="7.5" cy="7.5" r="1.5"/>'
    ),
    "docs": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/>',
    "todos": '<path d="M4 6h2M4 12h2M4 18h2"/><path d="M9 6h11M9 12h11M9 18h11"/>',
    "settings": (
        '<path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h16"/>'
        '<circle cx="16" cy="7" r="2"/><circle cx="8" cy="12" r="2"/>'
        '<circle cx="18" cy="17" r="2"/>'
    ),
    "account": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
}

# The hero image is the only image asset available on this same-origin
# static surface (the v0.10 Scaffold declares no new asset file); the
# vendored Vditor logo is reused with attribution rather than pointing at a
# missing path.
_HERO_IMAGE = "/static/vendor/vditor/images/logo.png"


def render_page(name: str, context: dict) -> str:
    """Render one page of the PAGES closed set with the given context."""
    if name not in PAGES:
        raise ValueError(f"unknown page {name!r} outside the PAGES closed set")
    if name == "login":
        body = _login_body()
    else:
        body = _workbench_body(name, context)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html.escape(_TITLES[name])}</title>",
            '<link rel="stylesheet" href="/static/styles.css">',
            "</head>",
            f'<body class="{"auth" if name == "login" else "workbench-body"}">',
            body,
            "</body>",
            "</html>",
            "",
        ]
    )


def _login_body() -> str:
    """Dual-column auth shell (E-02 / §1m.1).

    The name step exposes the frozen browser-contract locators
    (``name-input``/``name-submit``) on the input/control so the UI e2e binds
    the real elements, alongside the shell's own field anchors
    (``login-name``/``login-name-continue``) the page-shell contract carries.
    """
    return "\n".join(
        [
            '<main class="auth-shell" data-testid="login-shell">',
            '<section class="auth-hero" data-testid="login-hero">',
            f'<img class="auth-hero-image" src="{_HERO_IMAGE}" alt="tracks workbench logo">',
            '<p class="auth-slogan">One workbench for the whole run.</p>',
            '<p class="auth-credit" data-testid="login-hero-credit">Image: Vditor (MIT)</p>',
            "</section>",
            '<section class="auth-panel" data-testid="login-panel">',
            '<h1 class="auth-title">tracks</h1>',
            '<form class="auth-form login-form" data-testid="login-form" method="post"'
            ' action="/api/auth/login">',
            '<label for="password">Password</label>',
            '<input id="password" name="password" type="password" autocomplete="current-password"'
            ' data-testid="login-password" required>',
            '<button type="submit" data-testid="login-submit">Log in</button>',
            "</form>",
            '<p class="auth-error" data-testid="login-error" role="alert" hidden></p>',
            '<form class="auth-form" data-testid="login-name-form" hidden>',
            '<label for="display-name" data-testid="login-name">Display name</label>',
            '<input id="display-name" name="name" data-testid="name-input"'
            ' maxlength="64" required>',
            '<button type="button" data-testid="name-submit">'
            '<span data-testid="login-name-continue">Continue</span></button>',
            "</form>",
            "</section>",
            "</main>",
            '<script type="module" src="/static/app/shell.js"></script>',
        ]
    )


def _workbench_body(name: str, context: dict) -> str:
    """Shared workbench shell (E-01 / §1l): chrome + deep link + bootstrap."""
    return "\n".join(
        [
            f'<div id="workbench" class="workbench" data-route="{name}"'
            f"{_deep_link_attrs(context)}>",
            '<nav class="tabbar" data-testid="tabbar" aria-label="Workbench functions">',
            *(_tabbar_item(feature, href) for feature, href in _TABBAR_ITEMS),
            "</nav>",
            '<aside class="sidebar" data-testid="sidebar" aria-label="Context navigation"></aside>',
            '<main class="main" data-testid="main-area">',
            # 2026-10-09 operator directive: the main header strip is gone —
            # the content face starts at the window's top edge; the
            # 2026-10-05 directive (icon-switched windows, no open-tab
            # strip) stands: the tab-bar icons are the only switcher.
            '<section class="main-body" data-testid="main-body"></section>',
            "</main>",
            "</div>",
            '<script type="module" src="/static/app/shell.js"></script>',
        ]
    )


def _tabbar_item(feature: str, href: str | None) -> str:
    """One icon-only tab bar entry with its hover tooltip (FR-0317)."""
    label = feature.capitalize()
    icon = (
        '<svg class="tabbar-icon" viewBox="0 0 24 24" width="20" height="20"'
        ' fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        f"{_TABBAR_ICONS[feature]}</svg>"
    )
    if href is not None:
        return (
            f'<a class="tabbar-item" data-testid="tabbar-item-{feature}"'
            f' href="{href}" title="{label}" aria-label="{label}">{icon}</a>'
        )
    return (
        f'<button type="button" class="tabbar-item" data-testid="tabbar-item-{feature}"'
        f' title="{label}" aria-label="{label}">{icon}</button>'
    )


def _deep_link_attrs(context: dict) -> str:
    """Escaped run_id/project_id deep-link attributes (§1l.1)."""
    attrs = ""
    run_id = _escape(context.get("run_id"))
    project_id = _escape(context.get("project_id"))
    if run_id:
        attrs += f' data-run-id="{run_id}"'
    if project_id:
        attrs += f' data-project-id="{project_id}"'
    return attrs


def _escape(value: object) -> str:
    return html.escape(str(value)) if value is not None else ""
