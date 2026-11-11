"""Fresh wheel installation through the user's first Web development entry."""

import json
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests._support.v012_install import parse_serving_line, wait_healthz
from tests.e2e.test_install_journey import _start_background
from tests.e2e.test_intake_ui import _login, _submit_via_ui

pytestmark = [pytest.mark.e2e, pytest.mark.ui]


# AC-FR0348-01@v0.12 TRACKS-TRACE installed wheel accepts the first story through Web UI
# AC-FR0350-01@v0.12 TRACKS-TRACE first service start registers its installed project
def test_installed_project_can_start_development_in_web(v012_global, tmp_path, page):
    project = tmp_path / "new-product"
    installed = v012_global.run("install", str(project), cwd=tmp_path)
    assert installed.returncode == 0, installed.stderr
    started = _start_background(v012_global, project, tmp_path)
    assert started.returncode == 0, started.stderr
    serving = next(line for line in started.stdout.splitlines() if "serving on http" in line)
    base = parse_serving_line(serving)["url"]
    try:
        assert wait_healthz(base)["projects"] == 1
        _login(page, base, password="v012-journey-password")
        _submit_via_ui(page, base, "实现整数加法 add(a, b)，支持正数、负数和零。")
        page.get_by_test_id("panel-action-develop").click()
        expect(page.get_by_test_id("current-node-card")).to_be_visible(timeout=30_000)
        expect(page.get_by_test_id("gate-action-triage")).to_be_visible(timeout=30_000)
        page.get_by_test_id("gate-action-triage").click()
        expect(page.get_by_test_id("review-approve")).to_be_visible(timeout=60_000)
        expect(page.get_by_test_id("stage-workflow-diagram").locator("svg")).to_be_visible(
            timeout=30_000
        )
        with sqlite3.connect(project / ".tracks" / "runtime" / "tracks.db") as conn:
            requested = conn.execute(
                "SELECT version, payload FROM events WHERE type = 'story.requested'"
            ).fetchall()
        assert len(requested) == 1
        version, payload = requested[0]
        assert json.loads(payload)["workflow_profile"] == "full-v1"
        page.get_by_test_id("detail-owning-surface").click()
        expect(page.get_by_test_id("view-docs")).to_be_visible()
        expect(page.get_by_test_id(f"doc-tree-item-{version}-story")).to_be_visible()
        expect(page.get_by_test_id("doc-editor-content")).to_have_value(
            re.compile("实现整数加法")
        )
    finally:
        stopped = v012_global.run("serve", "--repo", str(project), "--stop", cwd=tmp_path)
        assert stopped.returncode == 0, stopped.stderr
