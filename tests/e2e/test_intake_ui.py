"""Unified entry UI journey (IF-WEBUI-001, IF-INTAKE-001, IF-MSTART-001,
IF-HOTFIXUI-001).

The §9.3 browser happy path: the unified entry takes one paragraph (raw
seed), the Scribe suggestion panel presents the feasibility/duplicate/conflict
columns with the four actions, entering development passes the M-START gate
and lands the release branch + run, and the separate hotfix entry walks its
anchored half. Controls bind to ``data-testid`` only.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager

import pytest
from playwright.sync_api import expect

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  constants
from tests._support.v09_web import (
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_WAIT_MS = 8_000

_SEED_TEXT = "给发布清单页加一个导出按钮，导出为 CSV。"


def _prepared_repo(trac):
    """The host repo as a runnable tracks project (web journey recipe)."""
    repo = trac.repo
    contract_dir = repo / ".tracks" / "projects"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "project.toml").write_text(
        "[unit]\ncollect = \"python3 -m pytest --collect-only tests/unit\"\n"
        "run = \"python3 -m pytest tests/unit -q\"\n"
        "[integration]\ncollect = \"python3 -m pytest --collect-only tests/integration\"\n"
        "run = \"python3 -m pytest tests/integration -q\"\n",
        encoding="utf-8",
    )
    assert trac("init").returncode == 0
    return repo


@contextmanager
def _entry_serve(tmp_path, repo, marker: str):
    """Serve over the repo with web-plane registration ready."""
    home = tmp_path / f"entry-home-{marker}"
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        status, body = http_post(
            base,
            "/api/projects",
            {"repo_path": str(repo)},
            cookies=cookie,
            csrf=csrf,
            idempotency_key=f"intakeui-reg-{marker}",
        )
        assert status == 201, f"registration must succeed: {status} {body[:200]}"
        yield base
    finally:
        stop_serve(proc)


def _login(page, base_url: str) -> None:
    page.set_default_timeout(_WAIT_MS)
    page.goto(base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()


def _submit_via_ui(page, base_url: str, text: str) -> None:
    """The unified entry: submit one paragraph through the browser."""
    page.get_by_test_id("intake-input").fill(text)
    page.get_by_test_id("intake-submit").click()
    expect(page.get_by_test_id("intake-panel")).to_be_visible()


# AC-FR0338-01@v0.11 TRACKS-TRACE scribe panel columns and four actions
def test_scribe_panel_four_actions(trac, tmp_path, page):
    """AC-FR0338-01 (ui half): the suggestion panel presents the Scribe
    feasibility/duplicate/conflict columns and exactly the four actions;
    ``remove_backlog`` is unavailable for a non-backlog source, and no hotfix
    recommendation surface exists anywhere in the entry."""
    repo = _prepared_repo(trac)
    with _entry_serve(tmp_path, repo, "panel") as base:
        _login(page, base)
        _submit_via_ui(page, base, _SEED_TEXT)
        # the three report columns render (§1f.2 fixed columns)
        expect(page.get_by_test_id("panel-feasibility")).to_be_visible()
        expect(page.get_by_test_id("panel-duplicates")).to_be_visible()
        expect(page.get_by_test_id("panel-conflicts")).to_be_visible()
        # the four actions
        for action in ("backlog", "remove", "archive", "develop"):
            expect(page.get_by_test_id(f"panel-action-{action}")).to_be_visible()
        # a text source may not offer the backlog-only removal
        remove = page.get_by_test_id("panel-action-remove")
        assert remove.is_disabled() or remove.get_attribute("aria-disabled") == "true", (
            "remove_backlog must be unavailable for a text source (SM-01.5)"
        )
        # no hotfix recommendation surface (§1f.5)
        panel_text = page.get_by_test_id("intake-panel").inner_text().lower()
        assert "hotfix" not in panel_text, (
            "the unified entry must not recommend the hotfix flow"
        )


# AC-FR0339-01@v0.11 TRACKS-TRACE develop happy journey creates release branch
def test_develop_happy_creates_release(trac, tmp_path, page):
    """AC-FR0339-01 (ui half): choosing enter-development passes the M-START
    gate — the ``releases/v{N+1}`` branch and its run come into existence and
    the run's timeline presents the M-STORY activity."""
    repo = _prepared_repo(trac)
    subprocess.run(
        ["git", "branch", "releases/v1.0"], cwd=repo, check=True, capture_output=True
    )
    with _entry_serve(tmp_path, repo, "develop") as base:
        _login(page, base)
        _submit_via_ui(page, base, _SEED_TEXT)
        page.get_by_test_id("panel-action-develop").click()
        # the gate passes and the release window regains the new version row
        deadline = time.time() + 60
        seen = False
        while time.time() < deadline and not seen:
            content = page.content()
            seen = "releases/v2.0" in content or "v2.0" in content
            if not seen:
                time.sleep(0.5)
        assert seen, "the new release version must present after the gate"
        # the M-STORY activity presents on the timeline
        active = page.locator("[data-testid^='timeline-node-'][data-state='active']")
        expect(active.first).to_be_visible()
        assert "M-STORY" in page.get_by_test_id("timeline").inner_text(), (
            "the new release must enter M-STORY visibly"
        )


# AC-FR0341-01@v0.11 TRACKS-TRACE hotfix entry anchored proceeds
def test_hotfix_entry_anchored_proceeds(trac, tmp_path, page):
    """AC-FR0341-01 (ui half): the independent hotfix entry takes the issue
    selection and the scenario, presents the precheck and AC anchoring, and an
    anchored entry proceeds into the hotfix run (the create action stays
    enabled against the anchored preview)."""
    from tests.integration.test_journey_versioning import _seed_hotfix_host

    repo = _prepared_repo(trac)
    _seed_hotfix_host(repo)
    with _entry_serve(tmp_path, repo, "hotfix") as base:
        _login(page, base)
        page.get_by_test_id("hotfix-entry").click()
        page.get_by_test_id("hotfix-issue-input").fill("42")
        page.get_by_test_id("hotfix-scenario-post-release").click()
        page.get_by_test_id("hotfix-preview-submit").click()
        preview = page.get_by_test_id("hotfix-preview")
        expect(preview).to_be_visible()
        assert "anchored" in preview.inner_text(), (
            "the anchoring result must present on the entry"
        )
        proceed = page.get_by_test_id("hotfix-proceed")
        expect(proceed).to_be_visible()
        proceed.click()
        # the anchored entry proceeds: the hotfix run's face presents
        expect(page.get_by_test_id("main-area")).to_be_visible()
        deadline = time.time() + 60
        seen_fix = False
        while time.time() < deadline and not seen_fix:
            seen_fix = "fix/42" in page.content()
            if not seen_fix:
                time.sleep(0.5)
        assert seen_fix, "the anchored hotfix must proceed to its fix branch"
