"""v0.11 T-001 RED: workbench shell convergence (IF-RELEASES-001 §1e.1).

Devon-owned unit RED for the v0.11 page-shell convergence — the five-tab
world of interfaces §0.1 #1 / §1e.1 (IF-WORKBENCH-001 v0.11 revision,
FR-0333/FR-0334 breaking change, GO confirmed):

- ``tracks/server/pages.py``: the ``PAGES`` closed set converges 8 → 6 names
  (login/releases/run_detail/todos/release/docs — ``overview``/``projects``/
  ``run_new``/``review`` are gone, ``docs`` gains its deep link); the tab bar
  functional closed set converges 7 → 5 items in the fixed order
  releases/docs/todos/settings/account with ``data-testid=tabbar-item-<name>``
  anchors and no review/projects/runs entries (§1e.1);
- ``tracks/server/app.py``: the ``_PAGE_ROUTES`` URL table maps ``/`` to the
  releases shell and adds the ``/docs`` deep link; the three removed URLs
  (``/projects``, ``/projects/{pid}/runs/new``, ``/runs/{run_id}/review``)
  answer 302 to ``/`` so deep links never dangle;
- ``tracks/server/static/app/shell.js``: the browser shell opens the
  releases window for run-scoped deep links and no longer knows the removed
  feature windows (the five-tab world; architecture §1.0.1 #1/#6, §1.0.2).

RED discipline: the current tree still ships the v0.10 eight-name world, so
every node below guards the missing v0.11 contract into a real
``AssertionError`` (closed-set membership asserts, route-table equality,
ASGI status/location asserts over a real ServiceDB-backed composition,
static source pins) — no stub tokens, no assembly errors, no skips. The
browser-behaviour half (sidebar/main linkage, no empty tab) stays with the
frozen ui e2e journeys (test-plan §1.5/§11 #8).

AC: FR-0334-01 — TRACKS-TRACE IF-RELEASES-001, IF-WORKBENCH-001.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks.server import app as composition_root
from tracks.server import auth, pages
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.service import CommandService

_APP_DIR = Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "app"

# IF-RELEASES-001 §1e.1: the six-name page closed set of the Releases world.
_V11_PAGES = ("login", "releases", "run_detail", "todos", "release", "docs")
_REMOVED_PAGES = ("overview", "projects", "run_new", "review")

# §1e.1: the five-item tab bar functional closed set in its fixed order.
_V11_TABS = ("releases", "docs", "todos", "settings", "account")
_REMOVED_TABS = ("projects", "runs", "review")

# §1e.1 URL table; the tab-bar links ARE the deep links of this table.
_V11_URL_TABLE = {
    "/": "releases",
    "/login": "login",
    "/runs/{run_id}": "run_detail",
    "/todos": "todos",
    "/runs/{run_id}/release": "release",
    "/docs": "docs",
}
_TAB_LINK_HREFS = {"releases": "/", "docs": "/docs", "todos": "/todos"}
_TAB_CONTROL_FEATURES = ("settings", "account")

# The three removed URL patterns (route-table form) and the concrete old
# paths the redirect drives below speak.
_REMOVED_URL_PATTERNS = (
    "/projects",
    "/projects/{pid}/runs/new",
    "/runs/{run_id}/review",
)
_OLD_CONCRETE_PATHS = ("/projects", "/projects/p1/runs/new", "/runs/r1/review")

_TABBAR_TAG_RE = re.compile(r'<(a|button)\b[^>]*data-testid="tabbar-item-([a-z]+)"[^>]*>')
_TITLES_RE = re.compile(r"const\s+TITLES\s*=\s*\{([^}]*)\}", re.S)
_TITLE_KEY_RE = re.compile(r"^\s*[\"']?([a-z]+)[\"']?\s*:", re.M)

_PASSWORD = "correct horse battery staple"
_NAME = "Ada Lovelace"


def _render(name: str) -> str:
    """Render one §1e.1 page; a missing v0.11 page fails as an assertion.

    ``render_page`` answers out-of-set names with ``ValueError`` — on the
    pre-convergence tree the six v0.11 names are exactly such out-of-set
    names, so the guard turns that into the contract failure it is (the
    page must exist) instead of leaking a non-assertion error shape.
    """
    try:
        document = pages.render_page(name, {"run_id": "R-1", "project_id": "P-1"})
    except NotImplementedError as exc:
        raise AssertionError(f"render_page({name!r}) is still a stub: {exc}") from None
    except ValueError as exc:
        raise AssertionError(
            f"render_page({name!r}) must exist in the v0.11 PAGES closed set "
            f"(IF-RELEASES-001 §1e.1): {exc}"
        ) from None
    assert isinstance(document, str) and document.strip(), (
        f"render_page({name!r}) must render an HTML document"
    )
    return document


def _module(name: str) -> str:
    """Source of one shipped ES module; a missing module fails as an assertion."""
    path = _APP_DIR / name
    assert path.is_file(), f"tracks/server/static/app/{name} must keep shipping"
    return path.read_text(encoding="utf-8")


# -- PAGES closed set (interfaces §1e.1, FR-0333/FR-0334) ----------------------


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-RELEASES-001 PAGES converges to the six-name set
def test_pages_closed_set_is_the_six_name_releases_world():
    removed = [name for name in _REMOVED_PAGES if name in pages.PAGES]
    assert not removed, (
        f"the v0.10 pages {removed} must leave the closed set — PAGES is the "
        "six-name releases world of IF-RELEASES-001 §1e.1 (FR-0333/FR-0334 "
        "breaking change, GO confirmed)"
    )
    assert tuple(pages.PAGES) == _V11_PAGES, (
        "PAGES is exactly login/releases/run_detail/todos/release/docs (§1e.1); "
        "the review/projects/overview/run_new shells are gone and docs exists"
    )


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-WORKBENCH-001 tab bar: five items, fixed order
def test_tabbar_is_the_five_item_closed_set_in_fixed_order():
    document = _render("releases")
    assert 'data-route="releases"' in document, (
        "the releases page renders the shared workbench shell with its deep link"
    )
    rendered = [match.group(2) for match in _TABBAR_TAG_RE.finditer(document)]
    assert rendered == list(_V11_TABS), (
        "the tab bar carries exactly the five functional items in the fixed "
        f"order releases/docs/todos/settings/account (§1e.1); got {rendered}"
    )
    for feature in _REMOVED_TABS:
        assert f"tabbar-item-{feature}" not in document, (
            f"the removed {feature} entry must not render in the tab bar (§1e.1)"
        )


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-RELEASES-001 link tabs carry URL-table deep links
def test_tabbar_links_carry_url_table_deep_links_and_controls_stay_buttons():
    document = _render("releases")
    items = {m.group(2): m for m in _TABBAR_TAG_RE.finditer(document)}
    assert set(items) == set(_V11_TABS), "the five §1e.1 items render and no others"
    for feature, href in _TAB_LINK_HREFS.items():
        opening = items[feature].group(0)
        assert items[feature].group(1) == "a", (
            f"the {feature} tab is a deep link, not an in-page-only control"
        )
        linked = re.search(r'href="([^"]*)"', opening)
        assert linked is not None and linked.group(1) == href, (
            f"the {feature} tab links its §1e.1 URL-table entry {href!r}"
        )
    for feature in _TAB_CONTROL_FEATURES:
        opening = items[feature].group(0)
        assert items[feature].group(1) == "button", (
            f"the {feature} tab opens in-page surfaces — it renders as a button"
        )
        assert "href=" not in opening, (
            f"the {feature} control carries no navigation URL (§1e.1)"
        )


# -- the URL table and the old-URL redirects (app.py, §1e.1) --------------------


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-RELEASES-001 URL table maps / to releases, adds /docs
def test_page_routes_table_is_the_v011_url_table():
    routes = getattr(composition_root, "_PAGE_ROUTES", None)
    assert routes is not None, (
        "the composition root keeps the §2b page route table (_PAGE_ROUTES)"
    )
    table = dict(routes)
    for pattern in _REMOVED_URL_PATTERNS:
        assert pattern not in table, (
            f"{pattern} must not bind a page — it answers 302 to / (§1e.1)"
        )
    assert table == _V11_URL_TABLE, (
        "the page route table is exactly the §1e.1 URL table: / → releases, "
        "/docs deep link present, /login, /runs/{run_id}, /todos and "
        "/runs/{run_id}/release unchanged"
    )


def _named_workbench_app(tmp_path: Path) -> tuple[Any, str]:
    """create_app over a real service home with one name-collected session.

    The page faces this task pins sit behind the §1f.3 auth boundary (page
    misses 302 to /login before the route table answers), so the composition
    seeds the delivered auth faces directly — provisioned password, collected
    display_name, one live session — and hands back the token its drives
    carry. Nothing mocks the system under test: the ASGI drives speak the
    protocol uvicorn speaks against the real composition root.
    """
    home = tmp_path / "service-home"
    store = ServiceDB(home)
    config = SimpleNamespace(permitted_repos=[], secret_values={})
    service = CommandService(home, store, config)
    app = composition_root.create_app(home, service, SimpleNamespace(db=store), config)
    with contextlib.closing(sqlite3.connect(str(home / "service.db"))) as conn:
        auth.provision_password(conn, _PASSWORD)
        auth.bind_display_name(conn, _NAME)
        session = auth.issue_session(conn, _NAME)
    return app, session.token


def _drive(app: Any, path: str, token: str) -> tuple[int, str, bytes]:
    """One authenticated GET through the ASGI protocol: status/location/body."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"cookie", f"trac_session={token}".encode()),
        ],
        "client": ("127.0.0.1", 111),
        "server": ("127.0.0.1", 80),
    }
    messages: list[dict] = []

    async def run() -> None:
        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            messages.append(message)

        await app(scope, receive, send)

    asyncio.run(run())
    start = next(m for m in messages if m["type"] == "http.response.start")
    payload = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    location = ""
    for key, value in start["headers"]:
        if key.decode("latin-1").lower() == "location":
            location = value.decode("latin-1")
    return int(start["status"]), location, payload


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-RELEASES-001 removed URLs answer 302 to /
def test_old_urls_redirect_302_to_the_releases_root(tmp_path: Path):
    app, token = _named_workbench_app(tmp_path)
    for old in _OLD_CONCRETE_PATHS:
        status, location, _ = _drive(app, old, token)
        assert status == 302 and location == "/", (
            f"the removed URL {old} must answer 302 to / so its deep link "
            f"never dangles (§1e.1); got {status} location={location!r}"
        )


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-RELEASES-001 root serves releases, /docs deep link
def test_root_and_docs_serve_the_v011_shells(tmp_path: Path):
    app, token = _named_workbench_app(tmp_path)
    root_status, _, root_body = _drive(app, "/", token)
    assert root_status == 200, "the releases root serves the workbench directly"
    assert b'data-route="releases"' in root_body, (
        "/ is the Releases shell of the five-tab world (§1e.1)"
    )
    docs_status, _, docs_body = _drive(app, "/docs", token)
    assert docs_status == 200 and b'data-route="docs"' in docs_body, (
        "the /docs deep link serves the docs shell (§1e.1 URL table)"
    )


# -- browser shell: the five-tab world (shell.js, architecture §1.0.1 #1) -------


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-WORKBENCH-001 run-scoped deep links open releases
def test_shell_opens_the_releases_window_and_drops_removed_features():
    source = _module("shell.js")
    assert 'openTab("releases"' in source, (
        "run-scoped deep links (run_detail/release) open the releases window — "
        "the run registry rides the Releases shell of the five-tab world (E-01)"
    )
    for feature in _REMOVED_TABS:
        assert f'"{feature}"' not in source, (
            f"shell.js must not know the removed {feature} feature window any "
            "more (§1e.1 five-item closed set)"
        )


# AC-FR0334-01@v0.11 TRACKS-TRACE IF-WORKBENCH-001 feature titles cover the five tabs
def test_shell_feature_titles_cover_exactly_the_five_tabs():
    source = _module("shell.js")
    block = _TITLES_RE.search(source)
    assert block is not None, "shell.js keeps its feature title map for tab windows"
    titles = _TITLE_KEY_RE.findall(block.group(1))
    assert set(titles) == set(_V11_TABS), (
        f"the feature title map covers exactly the five-tab closed set; got "
        f"{sorted(titles)}"
    )
    assert titles, "the title map is non-empty"
