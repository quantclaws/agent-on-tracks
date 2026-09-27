"""Playwright/Chromium fixture surface for the v0.10 UI e2e layer.

IF-WEBUI-001 / test-plan §6.4 row 2: starts and recycles a real Chromium,
exposes isolated browser contexts and a ``storage_state`` round-trip that
models a browser restart (the session persistence AC), and never implements
business assertions or UI operations — the ``ui`` marker tests drive the page
themselves through ``data-testid`` anchors (interfaces §1t.2).

The serve process is the real subprocess of the inherited v0.9 fixture
(IF-SERVE-001: isolated temp home + host repo + random port, health-gated
before the browser opens anything).
"""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright

from tests._support.v09_web import (
    _SERVE_PASSWORD,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

SERVE_PASSWORD = _SERVE_PASSWORD

# The docs-center version tree the UI e2e reads (test-plan §2.4: multi-version
# fixture seed). Six-piece closed set per §1n.4; the hotfix dir carries only
# the trio that exists so the UI's explicit degradation is observable.
_UI_VERSIONS = ("v1.0", "v0.9-hotfix-7")
_TRIO = ("story.md", "spec.md", "acceptance.md")
_SIX_PIECE = _TRIO + ("architecture.md", "interfaces.md", "test-plan.md")


@dataclass
class UiServe:
    """A running serve process plus the observable storage it projects."""

    base_url: str
    home: Path
    repo: Path
    port: int

    def seed_project(self, project_id: str, version: str = "v1.0") -> str:
        """Register the host repo in the service store (§1c table 2).

        Fixture data only: the observable row the read projections resolve
        the run through, written exactly like the documented schema.
        """
        conn = sqlite3.connect(str(self.home / "service.db"))
        try:
            conn.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?)",
                (project_id, str(self.repo), version, "local-user", "2026-09-27T00:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()
        return project_id

    def seed_version_docs(self, version: str, docs=_SIX_PIECE) -> Path:
        """Write one version dir of the docs tree under the host repo."""
        vdir = self.repo / ".tracks" / "projects" / version
        vdir.mkdir(parents=True, exist_ok=True)
        for name in docs:
            (vdir / name).write_text(
                f"---\nenvelope: tracks-envelope:v2\n---\n\n"
                f"# {version} {name}\n\nfixture body for {name}\n",
                encoding="utf-8",
            )
        return vdir

    def seed_run(self, run_id: str, version: str = "v1.0", *, stage: str = "M-IMPL") -> str:
        """Append a real run to the host repo's tracks.db (event outlet)."""
        from tracks.paths import tracks_home
        from tracks.store import Store

        store = Store(tracks_home(self.repo))
        try:
            store.append(run_id, version, "story.requested", {"raw_chars": 1})
            store.append(run_id, version, "stage.entered", {"stage": stage})
        finally:
            store.close()
        return run_id

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True
        )


@pytest.fixture
def ui_serve(tmp_path):
    """Real serve subprocess over an isolated home + host repo (ui layer)."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "ui@example.com"),
        ("config", "user.name", "UI Human"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("ui host\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True
    )
    proc = start_serve(home, repo, port=0)
    try:
        line = wait_for_port_line(proc)
        base = parse_base_url(line)
        port = int(base.rsplit(":", 1)[1])
        wait_for_healthz(base)
        yield UiServe(base_url=base, home=home, repo=repo, port=port)
    finally:
        stop_serve(proc)


@pytest.fixture
def browser():
    """One Chromium per test; closed on teardown."""
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(headless=True)
        try:
            yield launched
        finally:
            launched.close()


@pytest.fixture
def context(browser):
    """An isolated browser context (empty cookie jar per test)."""
    created = browser.new_context()
    try:
        yield created
    finally:
        created.close()


@pytest.fixture
def page(context):
    """A fresh tab in the isolated context."""
    return context.new_page()


def restart_browser(browser: Browser, previous: BrowserContext) -> BrowserContext:
    """Model a browser restart: same profile storage, fresh process state."""
    state = previous.storage_state()
    previous.close()
    return browser.new_context(storage_state=state)


__all__ = [
    "SERVE_PASSWORD",
    "UiServe",
    "browser",
    "context",
    "page",
    "restart_browser",
    "ui_serve",
]
