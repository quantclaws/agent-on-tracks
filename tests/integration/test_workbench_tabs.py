"""Five-tab workbench convergence (IF-RELEASES-001 §1e.1, IF-WORKBENCH-001).

v0.11 collapses the PAGES closed set to six names and the function tab bar to
five items (Review folds into the awaiting panel; the standalone
projects/runs pages fold into the Releases window). Asserted over the served
page HTML face (interfaces §4a outlet 3): the tab closed set, the URL table
with legacy 302 redirects, and the pages-module symbol itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_get,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration


def _start_tabs_serve(tmp_path: Path):
    """Real serve subprocess over an isolated home + host repo (§6.4 row 1)."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("tabs host\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "tabs@example.com"),
        ("config", "user.name", "Tabs Human"),
        ("add", "README.md"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


_LEGACY_URLS = (
    "/projects",
    "/projects/proj-tabs-1/runs/new",
    "/runs/run-tabs-1/review",
)


def _redirect_of(base: str, cookie: str, path: str) -> tuple[int, str]:
    """One no-follow GET: returns (status, Location) without following."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(base + path, method="GET")
    req.add_header("Cookie", cookie)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


# AC-FR0334-01@v0.11 TRACKS-TRACE five tab closed set, legacy URLs 302
def test_five_tab_closed_set(tmp_path: Path):
    """AC-FR0334-01: the served shell exposes exactly the five function tabs
    (releases/docs/todos/settings/account) with no Review entry, the PAGES
    closed set is the six-name v0.11 table, and every legacy URL
    (/projects, /projects/{pid}/runs/new, /runs/{id}/review) 302-redirects to
    the Releases window root — no dead Review page, no empty tab."""
    proc, home, repo = _start_tabs_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)

        # the pages-module closed set (IF-RELEASES-001 modules: pages.py)
        from tracks.server.pages import PAGES

        assert set(PAGES) == {
            "login",
            "releases",
            "run_detail",
            "todos",
            "release",
            "docs",
        }, f"PAGES must be the six-name v0.11 closed set: {PAGES!r}"

        # the served tab bar carries exactly the five function items in the
        # fixed order; no review/projects/runs entries remain (page HTML face)
        status, body = http_get(base, "/", cookies=cookie)
        assert status == 200, f"the Releases root page must render: {status}"
        html = body.decode("utf-8")
        for tab in ("releases", "docs", "todos", "settings", "account"):
            assert f"tabbar-item-{tab}" in html, (
                f"the tab bar must carry the {tab} item"
            )
        for gone in ("tabbar-item-review", "tabbar-item-projects", "tabbar-item-runs"):
            assert gone not in html, (
                f"the collapsed {gone} entry must not render anywhere in the shell"
            )

        # legacy URLs redirect to the Releases window root (§1e.1 URL table)
        for legacy in _LEGACY_URLS:
            code, location = _redirect_of(base, cookie, legacy)
            assert code == 302, (
                f"{legacy} must 302 to / in the v0.11 URL table: got {code}"
            )
            assert location.rstrip("/") == "" or location.rstrip("/") == base.rstrip("/"), (
                f"{legacy} must redirect to the Releases root: {location!r}"
            )
    finally:
        stop_serve(proc)
