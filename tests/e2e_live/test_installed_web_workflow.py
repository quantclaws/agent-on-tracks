"""Real installed-project Web journey, stopping at the final release decision.

This test has no scenario overlays, seeded design/test assets, fake Agents or
manual event repair. The human persona reviews a fully specified synthetic
CLI product. A blocked runtime produces diagnostic artifacts and fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from contextlib import suppress

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from tests._support.v012_install import parse_serving_line
from tests.e2e.test_intake_ui import _submit_via_ui
from tests.e2e_live.conftest import write_host_opencode_config
from tests.e2e_live.harness import (
    append_log,
    installed_trac_command,
    live_env,
    run_logged,
    timeout_env,
)
from tests.e2e_live.m_test_helpers import _events, _git

pytestmark = [pytest.mark.e2e_live, pytest.mark.ui]

REQUIREMENT = """为个人开发者制作一个本地、低频使用的 Python 整数加法命令行工具 tinycalc。
交付面为 `python -m tinycalc A B`，恰好接收两个十进制整数，成功时 stdout
只输出二者之和和换行、退出码为 0。例如 `2 3` 输出 `5`，`-7 7` 输出 `0`，
`0 -4` 输出 `-4`。参数数量错误或非整数输入时 stderr 给出可读说明，退出码为 2，
stdout 为空。无需网络、持久化、Web UI、配置文件或其它业务功能。
运行时只依赖 Python 标准库，支持 Python 3.11+；提供可 pip 安装的 wheel、
README 使用示例与独立测试。测试和质量工具放在宿主自己的 .venv 中。
本项目是全新项目，不继承其它仓库的产品功能。需求已给出用户、规模、环境、
输入输出和边界；普通实现细节按成熟惯例自主选择并记录，不新增业务范围。
"""


class InstalledWebPilot:
    """Only public CLI and Web operations; artifacts retain the failed residency."""

    def __init__(self, repo, install, browser_page):
        self.repo = repo
        self.install = install
        self.page = browser_page
        self.base = ""
        self.run_id = ""
        self.env = live_env(install, {"TRAC_SERVE_PASSWORD": "tracks-synthetic-pilot"})
        self.log = install.artifact_dir / "web-pilot-cli.log"
        self.page.on("pageerror", lambda error: append_log(self.log, f"\nBROWSER_ERROR: {error}\n"))

    def cli(self, *args, timeout=120):
        result = run_logged(
            installed_trac_command(self.install.isolated_bin, *args),
            self.repo, self.env, self.log, timeout=timeout,
        )
        assert result.returncode == 0, f"trac {args}: {result.stderr[-3000:]}"
        return result

    def read(self, suffix=""):
        response = self.page.request.get(f"{self.base}/api/runs/{self.run_id}{suffix}")
        assert response.ok, f"read {suffix}: {response.status} {response.text()[:500]}"
        return response.json()

    def start(self, remote):
        assert not (self.repo / ".venv").exists(), "the pilot must start without host tools"
        self.cli("install", str(self.repo))
        write_host_opencode_config(self.repo)
        # The provider config contains an environment reference, never the key.
        # Commit normal host setup before asking M-START to enforce a clean tree.
        _git(self.repo, "add", ".opencode/opencode.json")
        _git(self.repo, "commit", "-m", "configure the synthetic project's model provider")
        _git(self.repo, "remote", "add", "origin", f"https://github.com/{remote}.git")
        _git(self.repo, "push", "-u", "origin", "main")
        self.open_server()
        _submit_via_ui(self.page, self.base, REQUIREMENT)
        self.enter_development()

    def open_server(self):
        started = self.cli("serve", "--repo", str(self.repo), "--port", "0", "--background")
        line = next(line for line in started.stdout.splitlines() if "serving on http" in line)
        self.base = parse_serving_line(line)["url"]
        self.page.set_default_timeout(8000)
        self.page.goto(self.base + "/login", wait_until="domcontentloaded")
        self.page.get_by_test_id("login-password").fill("tracks-synthetic-pilot")
        self.page.get_by_test_id("login-submit").click()
        try:
            # Reopening a project reuses its collected human identity.
            self.page.get_by_test_id("tabbar-item-releases").wait_for(timeout=3000)
        except PlaywrightTimeoutError:
            self.page.get_by_test_id("name-input").fill("UI Human")
            self.page.get_by_test_id("name-submit").click()
        expect(self.page.get_by_test_id("tabbar-item-releases")).to_be_visible()

    def enter_development(self):
        self.page.get_by_test_id("panel-action-develop").click()
        # A resumed evaluated intake returns to the registry; opening its
        # newly created row is part of the public Web path.
        if not self.page.get_by_test_id("current-node-card").is_visible():
            rows = self.page.locator("[data-testid^='sidebar-run-']")
            expect(rows.first).to_be_visible(timeout=60_000)
            rows.first.click()
        expect(self.page.get_by_test_id("current-node-card")).to_be_visible(timeout=180_000)
        self.run_id = self.page.locator("[data-run-id]").first.get_attribute("data-run-id")
        assert self.run_id
        (self.install.artifact_dir / "pilot-run-id.txt").write_text(self.run_id)

    def resume_intake(self, intake_id):
        self.open_server()
        self.page.get_by_test_id(f"pending-intake-resume-{intake_id}").click()
        expect(self.page.get_by_test_id("eval-result")).to_be_visible()
        self.enter_development()

    def review_document(self, stage):
        doc = {"M-STORY": "story", "M-SPEC": "spec", "M-ACC": "acceptance",
               "M-REQ-APPROVAL": "acceptance"}[stage]
        snapshot = self.read(f"/docs/{doc}")
        assert "tinycalc" in snapshot["content"], f"{stage} lost the approved product identity"
        self.page.get_by_test_id(f"timeline-node-{stage}").click()
        self.page.get_by_test_id("detail-owning-surface").click()
        expect(self.page.get_by_test_id("view-docs")).to_be_visible()
        expect(self.page.get_by_test_id("doc-editor-content").first).to_have_value(
            re.compile("tinycalc")
        )
        if stage == "M-SPEC":
            self.page.screenshot(path=str(self.install.artifact_dir / "pilot-spec.png"), full_page=True)
        self.page.get_by_test_id("docs-return-to-run").click()
        expect(self.page.get_by_test_id("current-node-card")).to_be_visible()

    def drive_to_candidate(self):
        deadline = time.monotonic() + timeout_env("TRAC_LIVE_TOTAL_TIMEOUT", 10800)
        answered = set()
        last = None
        while time.monotonic() < deadline:
            state = self.read()
            identity = (state.get("stage"), state.get("substate"), state.get("control_state"))
            if identity != last:
                print("PILOT_STATE=" + json.dumps(identity), flush=True)
                (self.install.artifact_dir / "pilot-state.json").write_text(
                    json.dumps(state, ensure_ascii=False, indent=2)
                )
                last = identity
            assert not state.get("blocked"), json.dumps(state.get("blocked"), ensure_ascii=False)
            gate = state.get("awaiting_gate") or {}
            kind, stage = gate.get("type"), state.get("stage")
            assert kind != "escalation", json.dumps(state, ensure_ascii=False)
            if stage == "M-RELEASE" and state.get("substate") == "AWAITING_RELEASE":
                return state
            assert state.get("status") != "completed", "premature boundary completion"
            key = (stage, kind)
            if kind and key not in answered:
                assert not gate.get("pending_human_threads"), json.dumps(gate, ensure_ascii=False)
                if kind == "triage":
                    self.page.get_by_test_id("gate-action-triage").click()
                elif kind == "review":
                    self.review_document(stage)
                    self.page.get_by_test_id("review-approve").click()
                elif kind == "approval":
                    self.review_document(stage)
                    self.page.get_by_test_id("gate-action-approve").click()
                else:
                    raise AssertionError(f"unexpected human gate: {gate}")
                answered.add(key)
            self.page.wait_for_timeout(1000)
        raise AssertionError("real installed Web journey exceeded its deadline")

    def verify_candidate(self, state):
        preview = self.read("/release-preview")
        assert preview["stale"] is False
        candidate = _git(self.repo, "rev-parse", "HEAD")
        assert preview["candidate_sha"] == candidate
        assert preview["preview_digest"].startswith("sha256:")
        version = state["version"]
        self.cli("check", "trace", "--version", version, "--json", timeout=300)
        self.cli("check", "reach", "--json", timeout=300)
        events = _events(self.repo, self.run_id)
        assert any(e["type"] == "environment.prepared" for e in events)
        ci = [e["payload"] for e in events if e["type"] == "ci.run_observed"][-1]
        assert ci["api_verified"] is True
        assert ci["head_sha"] == ci["candidate_sha"] == candidate
        assert any(e["type"] in ("issue.created", "issue.mapped") for e in events)
        assert not any(e["type"] in ("publish.executed", "milestone.sealed") for e in events)
        branch = _git(self.repo, "symbolic-ref", "--short", "HEAD")
        assert _git(self.repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0] == candidate
        self._verify_installed_product(events)
        self.page.screenshot(path=str(self.install.artifact_dir / "pilot-candidate.png"), full_page=True)
        (self.install.artifact_dir / "pilot-candidate.json").write_text(json.dumps({
            "candidate": candidate, "preview": preview, "run_id": self.run_id,
            "tracks_wheel": str(self.install.wheel), "publication": "awaiting final approval",
        }, ensure_ascii=False, indent=2))

    def _verify_installed_product(self, events):
        artifact = [e["payload"] for e in events if e["type"] == "artifact.built"][-1]
        consumer = self.install.artifact_dir / "product-consumer"
        python = consumer / "bin/python"
        subprocess.run([str(self.install.isolated_python), "-m", "venv", str(consumer)], check=True)
        subprocess.run([
            str(python), "-m", "pip", "install", "--no-index", artifact["artifact_path"],
        ], cwd=self.install.artifact_dir, check=True, capture_output=True)
        for args, expected in [(["2", "3"], "5\n"), (["-7", "7"], "0\n"), (["0", "-4"], "-4\n")]:
            result = subprocess.run([str(python), "-m", "tinycalc", *args],
                cwd=self.install.artifact_dir, capture_output=True, text=True, check=False)
            assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
        bad = subprocess.run([str(python), "-m", "tinycalc", "x", "1"],
            cwd=self.install.artifact_dir, capture_output=True, text=True, check=False)
        assert bad.returncode == 2 and bad.stderr.strip() and not bad.stdout


# AC-FR0348-01@v0.12 TRACKS-TRACE fresh installed Web project reaches real candidate evidence
def test_real_installed_web_workflow(live_root, live_install, live_enabled, live_github_repo, page):
    pilot = InstalledWebPilot(live_root, live_install, page)
    try:
        pilot.start(live_github_repo)
        state = pilot.drive_to_candidate()
        pilot.verify_candidate(state)
    finally:
        with suppress(Exception):
            page.screenshot(path=str(live_install.artifact_dir / "pilot-last-screen.png"), full_page=True)
            (live_install.artifact_dir / "pilot-last-page.html").write_text(page.content())
        with suppress(Exception):
            pilot.cli("serve", "--repo", str(live_root), "--stop")
