"""HTML shell and Vditor host pages (interfaces §2b pages, E-01..E-08).

Serves the page shells for the workbench; material review/edit is hosted by
the vendored Vditor build (view / basic edit / revision compare) loaded from
the same origin at ``/static/vendor/vditor/`` — never from a CDN, optional
render engines disabled (§2b). Pages never embed secrets (§1g.3) and never
embed Agent session content (FR-0303 boundary).

Contract tokens: IF-DOCREV-001, IF-SECRECY-001.
"""

from __future__ import annotations

from typing import Any

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


def render_page(name: str, context: dict) -> Any:
    """Render one page of the PAGES closed set with the given context."""
    raise NotImplementedError("IF-DOCREV-001")


def vditor_asset_tags() -> list[str]:
    """Same-origin <link>/<script> tags for the vendored Vditor assets."""
    raise NotImplementedError("IF-DOCREV-001")
