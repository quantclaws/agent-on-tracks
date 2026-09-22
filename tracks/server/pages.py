"""HTML shell and Vditor host pages (interfaces §2b pages, E-01..E-08).

Serves the page shells for the workbench; material review/edit is hosted by
the vendored Vditor build (view / basic edit / revision compare) loaded from
the same origin at ``/static/vendor/vditor/`` — never from a CDN, optional
render engines disabled (§2b). Pages never embed secrets (§1g.3) and never
embed Agent session content (FR-0303 boundary).

Contract tokens: IF-DOCREV-001, IF-SECRECY-001.
"""

from __future__ import annotations

import html

# Closed set of server-rendered pages (E-01..E-08).
PAGES = (
    "login",
    "overview",
    "projects",
    "run_new",
    "run_detail",
    "review",
    "todos",
    "release",
)

_TITLES = {
    "login": "tracks - login",
    "overview": "tracks - overview",
    "projects": "tracks - projects",
    "run_new": "tracks - new run",
    "run_detail": "tracks - run detail",
    "review": "tracks - material review",
    "todos": "tracks - todo center",
    "release": "tracks - release",
}

# Vendored same-origin assets (interfaces §2b, architecture §3.4). The editor
# core loads its lute/highlight/i18n/icon add-ons from the same origin on
# demand; the optional engines (math/mermaid/echarts/emoji) are not vendored
# and stay disabled.
_VDITOR_ROOT = "/static/vendor/vditor/"
_VDITOR_CSS = _VDITOR_ROOT + "index.css"
_VDITOR_JS = _VDITOR_ROOT + "index.min.js"


def vditor_asset_tags() -> list[str]:
    """Same-origin <link>/<script> tags for the vendored Vditor assets."""
    return [
        f'<link rel="stylesheet" href="{_VDITOR_CSS}">',
        f'<script src="{_VDITOR_JS}"></script>',
    ]


def render_page(name: str, context: dict) -> str:
    """Render one page of the PAGES closed set with the given context."""
    if name not in PAGES:
        raise ValueError(f"unknown page {name!r} outside the PAGES closed set")
    head = [f"<title>{html.escape(_TITLES[name])}</title>"]
    if name == "review":
        head.extend(vditor_asset_tags())
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            *head,
            "</head>",
            "<body>",
            _body(name, context),
            "</body>",
            "</html>",
            "",
        ]
    )


def _body(name: str, context: dict) -> str:
    if name == "login":
        return _login_body()
    if name == "review":
        return _review_body(context)
    return _placeholder_body(name, context)


def _login_body() -> str:
    return "\n".join(
        [
            '<main id="page-login" data-page="login">',
            "<h1>tracks login</h1>",
            '<form id="login-form" method="post" action="/api/auth/login">',
            '<label for="password">Password</label>',
            '<input id="password" name="password" type="password"'
            ' autocomplete="current-password" required>',
            '<button type="submit">Log in</button>',
            "</form>",
            "</main>",
        ]
    )


def _placeholder_body(name: str, context: dict) -> str:
    attrs = ""
    run_id = _escape(context.get("run_id"))
    project_id = _escape(context.get("project_id"))
    if run_id:
        attrs += f' data-run-id="{run_id}"'
    if project_id:
        attrs += f' data-project-id="{project_id}"'
    return "\n".join(
        [
            f'<main id="page-{name}" data-page="{name}"{attrs}>',
            f"<h1>{html.escape(_TITLES[name])}</h1>",
            "</main>",
        ]
    )


def _review_body(context: dict) -> str:
    doc = _escape(context.get("doc")) or "material"
    revision = _escape(context.get("revision"))
    return "\n".join(
        [
            f'<main id="page-review" data-page="review" data-doc="{doc}">',
            f"<h1>Material review: {doc}</h1>",
            f'<p id="doc-revision" data-revision="{revision}">'
            f"Current revision: <code>{revision}</code></p>",
            _revision_history(context),
            '<div id="vditor-host"></div>',
            _revision_compare(context),
            "</main>",
        ]
    )


def _revision_history(context: dict) -> str:
    history = context.get("history") or []
    items = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        revision = _escape(entry.get("revision"))
        actor = _escape(entry.get("actor"))
        ts = _escape(entry.get("ts"))
        items.append(f'<li data-revision="{revision}">{revision} - {actor} - {ts}</li>')
    if not items:
        return ""
    return "\n".join(['<ul id="revision-history">', *items, "</ul>"])


def _revision_compare(context: dict) -> str:
    diff = context.get("diff")
    diff = diff if isinstance(diff, dict) else {}
    history = context.get("history") or []
    previous = None
    for entry in history:
        if isinstance(entry, dict) and entry.get("revision") != context.get("revision"):
            previous = entry.get("revision")
            break
    old = _escape(diff.get("from") or previous)
    new = _escape(diff.get("to") or context.get("revision"))
    unified = diff.get("unified_diff")
    parts = [
        '<section id="revision-compare">',
        "<h2>Revision compare</h2>",
        f'<p class="revision-pair">from <code>{old}</code> to <code>{new}</code></p>',
    ]
    if unified:
        parts.append(f'<pre class="unified-diff">{_escape(unified)}</pre>')
    parts.append("</section>")
    return "\n".join(parts)


def _escape(value: object) -> str:
    return html.escape(str(value)) if value is not None else ""
