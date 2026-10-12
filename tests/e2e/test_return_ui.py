"""Rollback confirm-face UI journey (IF-WEBUI-001, IF-RETURN-001).

The return happy path over a rollback-capable run: the pointer presents only
where the shared legality allows, the confirm face names the target and the
already-executed consequences before the explicit confirmation, and the
executed return never pretends to undo external side effects.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  constants
from tests._support.v09_web import (
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tracks.paths import tracks_home
from tracks.store import Store

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_WAIT_MS = 8_000

_TS = "2026-10-11T00:00:00+00:00"


def _seed_return_run(repo: Path, run_id: str) -> None:
    """A run whose history already carries a published release (§4c outlet).

    The sealed done-op vocabulary the shared legality rule counts (§1m.1):
    publish.executed with status=done + operation_kind/target (mirrors the
    integration timeline face's _seed_publish_executed_run).
    """
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-RELEASE"})
        store.append(
            run_id, "v0.9", "release.previewed", {"preview_digest": "sha256:" + "c" * 64}
        )
        store.append(
            run_id, "v0.9", "publish.executed",
            {"status": "done", "operation_kind": "merge", "target": "main"},
        )
    finally:
        store.close()


@contextmanager
def _return_serve(tmp_path, repo):
    """Serve over the seeded repo with the project row registered."""
    home = tmp_path / "return-home"
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        conn = sqlite3.connect(str(home / "service.db"))
        try:
            conn.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?)",
                ("proj-return-ui", str(repo), "v0.9", "local-user", _TS),
            )
            conn.commit()
        finally:
            conn.close()
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


# AC-FR0337-02@v0.11 TRACKS-TRACE return confirm face consequences and no undo
def test_return_confirm_face(tmp_path, host_repo, page):
    """AC-FR0337-02 (ui half): the return pointer presents only for allowed
    targets; the confirm face names the target and the already-executed
    consequences (external side effects are not disguised as undoable); the
    explicit confirmation executes the return and the face reflects the
    server's verdict."""
    _seed_return_run(host_repo, "run-return-ui-1")
    with _return_serve(tmp_path, host_repo) as base:
        _login(page, base)
        page.goto(base + "/runs/run-return-ui-1", wait_until="domcontentloaded")
        expect(page.get_by_test_id("detail-pane")).to_be_visible()

        # the pointer: only where return_options allows (§1m.1)
        pointers = page.locator("[data-testid^='return-pointer-']")
        assert pointers.count() >= 1, (
            "the rollback-capable run must present its return pointer"
        )
        pointers.first.click()

        # the confirm face: target + consequences + explicit confirmation
        face = page.get_by_test_id("return-confirm-face")
        expect(face).to_be_visible()
        expect(page.get_by_test_id("return-target")).to_be_visible()
        consequences = page.get_by_test_id("return-consequences")
        expect(consequences).to_be_visible()
        assert consequences.inner_text().strip(), (
            "the already-executed consequences must be readable before confirm"
        )

        # the explicit confirmation executes the return (§1m.2)
        page.get_by_test_id("return-confirm-button").click()
        verdict = page.get_by_test_id("return-verdict")
        expect(verdict).to_be_visible()
        assert "失败" not in verdict.inner_text(), (
            "the confirmed return must execute against the legal target"
        )
