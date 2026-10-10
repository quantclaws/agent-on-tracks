"""Unified intake entry and suggestion panel face (IF-INTAKE-001, IF-SPLIT-001,
IF-HOTFIX-011).

Covers the three submission sources and the four decision actions over the
documented endpoints (§2b #37-40), the SM-01 acceptance preconditions, the
input classification/adoption contract (§1f.4) observed through the created
run's story capture, and the TRIAGE report schema's fixed columns (§1f.2).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tests._support.intake_fence import wait_intake_terminal
from tests._support.v09_web import (
    http_get,
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tracks.paths import tracks_home
from tracks.store import Store, new_ulid

pytestmark = pytest.mark.integration

_RAW_SEED = "给发布清单页加一个导出按钮，导出为 CSV。"


_COMPLETE_STORY = """---
title: 导出按钮
---

# story

## 1. 目标

发布清单页支持导出 CSV。

## 2. 验收

- 点击导出按钮得到 CSV 文件
"""


def _prepared_repo(tmp_path: Path, trac) -> Path:
    """A host repo scaffolded as a runnable tracks project (web journey)."""
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


def _serve_registered(tmp_path: Path, repo: Path, marker: str):
    """Serve over the repo + web registration; returns the journey tuple."""
    home = tmp_path / f"serve-home-{marker}"
    proc = start_serve(home, repo, port=0)
    base = parse_base_url(wait_for_port_line(proc))
    wait_for_healthz(base)
    cookie, csrf = login_session(base)
    status, body = http_post(
        base,
        "/api/projects",
        {"repo_path": str(repo)},
        cookies=cookie,
        csrf=csrf,
        idempotency_key=f"intake-reg-{marker}",
    )
    assert status == 201, f"registration must succeed: {status} {body[:200]}"
    project_id = json.loads(body)["project_id"]
    return proc, base, cookie, csrf, project_id


def _submit(base: str, cookie: str, csrf: str, project_id: str, payload: dict, key: str):
    return http_post(
        base,
        f"/api/projects/{project_id}/intakes",
        payload,
        cookies=cookie,
        csrf=csrf,
        idempotency_key=key,
    )


def _decide(base: str, cookie: str, csrf: str, intake_id: str, payload: dict, key: str):
    return http_post(
        base,
        f"/api/intakes/{intake_id}/decisions",
        payload,
        cookies=cookie,
        csrf=csrf,
        idempotency_key=key,
    )


def _wait_branch(repo: Path, prefix: str, timeout: float = 120.0) -> str | None:
    """Poll git branch facts until a branch with the prefix exists."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["git", "branch", "--list", f"{prefix}*"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        names = [line.strip().lstrip("* ").strip() for line in out.splitlines()]
        names = [name for name in names if name]
        if names:
            return names[0]
        time.sleep(0.5)
    return None


# AC-FR0338-01@v0.11 TRACKS-TRACE three sources and four actions
def test_intake_sources_and_actions(tmp_path: Path, host_repo, trac):
    """AC-FR0338-01: all three sources submit through the unified entry (text,
    a stand-in GitHub issue id, a backlog entry ULID); the suggestion face
    exposes the four actions; ``remove_backlog`` is rejected for a non-backlog
    source; an unevaluated intake refuses every decision; and an
    unfetchable issue is rejected with a closed fetch-failure reason and no
    intake residue."""
    repo = _prepared_repo(tmp_path, trac)
    # the issue-source premise: the stand-in issue corpus (IF-HOTFIX-003
    # read channel) carries issue 501; anything else is confirmed missing
    from tests._support.hotfix_support import seed_host_issues

    seed_host_issues(
        repo,
        {
            "501": {
                "title": "intake source issue",
                "body": "### 版本\nv0.9\n### 症状\n统一入口取文样例",
                "labels": ["bug"],
            }
        },
    )
    # the backlog source premise: one recorded entry on the run plane
    entry_id = new_ulid()
    store = Store(tracks_home(repo))
    try:
        store.append(
            entry_id,
            "v0.9",
            "backlog.recorded",
            {"reason": "active_run", "source": "text", "content": "待转正条目"},
        )
    finally:
        store.close()

    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo, "src")
    try:
        # source 1: text
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "text", "content": _RAW_SEED},
            "intake-src-text-1",
        )
        assert status == 202, f"text source must be accepted: {status} {body[:200]}"
        text_intake = json.loads(body)
        assert text_intake["intake_id"] and text_intake["command_id"], body[:200]

        # source 2: issue id (stand-in read channel, L2 contract sim)
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "issue", "issue": 501},
            "intake-src-issue-1",
        )
        assert status == 202, f"issue source must be accepted: {status} {body[:200]}"
        issue_intake = json.loads(body)

        # source 3: backlog entry (转正入口)
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "backlog", "origin_entry": entry_id},
            "intake-src-backlog-1",
        )
        assert status == 202, f"backlog source must be accepted: {status} {body[:200]}"
        backlog_intake = json.loads(body)

        # the read model resolves all three and the list filters by status
        for intake in (text_intake, issue_intake, backlog_intake):
            row_status, body = http_get(
                base,
                f"/api/projects/{project_id}/intakes/{intake['intake_id']}",
                cookies=cookie,
            )
            assert row_status == 200, f"the intake row must resolve: {row_status}"
        status, body = http_get(
            base, f"/api/projects/{project_id}/intakes?status=submitted", cookies=cookie
        )
        assert status == 200, f"the list must resolve: {status} {body[:200]}"
        listed = json.loads(body)["intakes"]
        assert {row["intake_id"] for row in listed} >= {
            text_intake["intake_id"],
            issue_intake["intake_id"],
            backlog_intake["intake_id"],
        }, f"all three submissions must list as submitted: {listed!r}"

        # closed-set validation: an unknown source is refused 422 (§2b #39)
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "carrier-pigeon", "content": "nope"},
            "intake-src-bogus-1",
        )
        assert status == 422, f"an unknown source must be refused: {status} {body[:200]}"

        # unevaluated intakes refuse decisions (§1b #15 precondition)
        status, body = _decide(
            base, cookie, csrf, text_intake["intake_id"],
            {"action": "backlog"},
            "intake-decide-early-1",
        )
        assert status == 422, (
            f"a submitted (not evaluated) intake must refuse decisions: {status} {body[:200]}"
        )
        assert json.loads(body)["error"]["reason"] == "intake_not_ready", body[:200]

        # the four actions: fence all three intakes to evaluated first
        for intake in (text_intake, issue_intake, backlog_intake):
            evaluated = wait_intake_terminal(
                base, cookie, project_id, intake["intake_id"]
            )
            assert evaluated["status"] == "evaluated", (
                f"the fake-backend evaluation must land evaluated: {evaluated!r}"
            )

        # action ① backlog on the issue-sourced intake
        status, body = _decide(
            base, cookie, csrf, issue_intake["intake_id"],
            {"action": "backlog"},
            "intake-decide-backlog-1",
        )
        assert status == 202, f"the backlog action must be accepted: {status} {body[:200]}"

        # action ③ archive on the text-sourced intake
        status, body = _decide(
            base, cookie, csrf, text_intake["intake_id"],
            {"action": "archive"},
            "intake-decide-archive-1",
        )
        assert status == 202, f"the archive action must be accepted: {status} {body[:200]}"

        # action ② remove_backlog: legal only for the backlog source (SM-01.5)
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "text", "content": "再来一条文本输入"},
            "intake-src-text-2",
        )
        assert status == 202, f"the second text source must be accepted: {status}"
        second_text = json.loads(body)
        wait_intake_terminal(base, cookie, project_id, second_text["intake_id"])
        status, body = _decide(
            base, cookie, csrf, second_text["intake_id"],
            {"action": "remove_backlog"},
            "intake-decide-remove-text-1",
        )
        assert status == 422, (
            f"remove_backlog must refuse a text-sourced intake: {status} {body[:200]}"
        )
        assert json.loads(body)["error"]["reason"] == "validation_failed", body[:200]

        # ...and accepted for the backlog-sourced intake (its entry is removed)
        status, body = _decide(
            base, cookie, csrf, backlog_intake["intake_id"],
            {"action": "remove_backlog"},
            "intake-decide-remove-backlog-1",
        )
        assert status == 202, (
            f"remove_backlog must accept the backlog source: {status} {body[:200]}"
        )

        # an unfetchable issue is rejected at acceptance, no intake residue
        # (§1b #14 reuses the IF-HOTFIX-011 classification mapping: a
        # confirmed-missing issue is the narrow issue_not_found member, a
        # transport-class failure the issue_fetch_failed member — both are
        # the contract's closed fetch-failure set)
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "issue", "issue": 999_981},
            "intake-src-issue-missing-1",
        )
        assert status == 409, (
            f"a missing issue must reject the submission: {status} {body[:200]}"
        )
        reason = json.loads(body)["error"]["reason"]
        assert reason in ("issue_fetch_failed", "issue_not_found"), (
            f"the rejection carries a closed fetch-failure reason: {reason!r}"
        )
        status, body = http_get(
            base, f"/api/projects/{project_id}/intakes", cookies=cookie
        )
        rows = json.loads(body)["intakes"]
        assert all(
            row["source"] != "issue" or row.get("issue") != 999_981 for row in rows
        ), f"a rejected submission must leave no intake row: {rows!r}"
    finally:
        stop_serve(proc)


# AC-FR0338-02@v0.11 TRACKS-TRACE raw seed verbatim complete story adopted
def test_raw_seed_verbatim_complete_story_adopted_no_hotfix_routing(
    tmp_path: Path, host_repo, trac
):
    """AC-FR0338-02: a raw seed enters development as the §1 skeleton carrying
    the input verbatim; a complete story is adopted as-is (no §1 wrapping, no
    rewriting); and neither the intake read model nor the report routes to the
    hotfix flow — the unified entry has no hotfix recommendation surface."""
    repo = _prepared_repo(tmp_path, trac)
    # the version-derivation premise: one existing release branch (v{N+1} rule)
    subprocess.run(
        ["git", "branch", "releases/v1.0"], cwd=repo, check=True, capture_output=True
    )
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo, "adopt")
    try:
        def develop_from(content: str, form: str, key: str) -> str:
            status, body = _submit(
                base, cookie, csrf, project_id,
                {"source": "text", "content": content},
                f"intake-adopt-{key}",
            )
            assert status == 202, f"the {form} submission must be accepted: {body[:200]}"
            intake_id = json.loads(body)["intake_id"]
            evaluated = wait_intake_terminal(base, cookie, project_id, intake_id)
            assert evaluated["status"] == "evaluated", (
                f"the {form} evaluation must land evaluated: {evaluated!r}"
            )
            # the read model exposes no hotfix routing recommendation
            assert "hotfix" not in json.dumps(evaluated).lower(), (
                f"the suggestion face must not route to hotfix (§1f.5): {evaluated!r}"
            )
            status, body = _decide(
                base, cookie, csrf, intake_id,
                {"action": "develop"},
                f"intake-adopt-decide-{key}",
            )
            assert status == 202, (
                f"the develop action must be accepted for the {form} input: "
                f"{status} {body[:200]}"
            )
            return intake_id

        develop_from(_RAW_SEED, "raw", "raw-1")

        # the develop delegation creates the next release branch from the rule
        branch = _wait_branch(repo, "releases/v")
        assert branch is not None, (
            "the develop action must create the release branch (M-START gate §1g.1)"
        )
        assert branch == "releases/v2.0", (
            f"the branch derives from the max existing release minor + 1: {branch!r}"
        )
        deadline = time.time() + 90
        story = ""
        while time.time() < deadline:
            probe = subprocess.run(
                ["git", "show", f"{branch}:.tracks/projects/v2.0/story.md"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                story = probe.stdout
                break
            time.sleep(0.5)
        assert story, f"the story capture must exist on {branch} (§4c.2)"
        assert _RAW_SEED in story, (
            f"the raw seed must enter the skeleton verbatim (§1f.4): {story!r}"
        )

        # complete story: adopted as-is — content equality with the input
        develop_from(_COMPLETE_STORY, "complete", "complete-1")
        branch2 = _wait_branch(repo, "releases/v3.")
        assert branch2 is not None, "the second develop must create the next branch"
        deadline = time.time() + 90
        story2 = ""
        while time.time() < deadline:
            probe = subprocess.run(
                ["git", "show", f"{branch2}:.tracks/projects/v3.0/story.md"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0:
                story2 = probe.stdout
                break
            time.sleep(0.5)
        assert story2, "the complete story capture must exist on the new branch"
        assert story2.strip() == _COMPLETE_STORY.strip(), (
            f"a complete story must be adopted verbatim, no §1 wrapping or "
            f"rewriting (§1f.4):\n---input---\n{_COMPLETE_STORY}\n---captured---\n{story2}"
        )
    finally:
        stop_serve(proc)


# AC-FR0346-01@v0.11 TRACKS-TRACE report duplicate conflict columns schema
def test_report_dup_conflict_columns(tmp_path: Path, host_repo, trac):
    """AC-FR0346-01: the evaluated intake's report carries the fixed TRIAGE
    columns — duplicate detection (cross-version FR/AC refs with a no_go/
    enhance suggestion) and conflict detection (new vs existing FR pairs) —
    and the machine accepts the evaluated intake without any automatic
    rejection (report columns only, no runtime interception, §1o.3)."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo, "report")
    try:
        status, body = _submit(
            base, cookie, csrf, project_id,
            {"source": "text", "content": _RAW_SEED},
            "intake-report-1",
        )
        assert status == 202, f"the submission must be accepted: {status} {body[:200]}"
        intake_id = json.loads(body)["intake_id"]
        evaluated = wait_intake_terminal(base, cookie, project_id, intake_id)
        assert evaluated["status"] == "evaluated", (
            f"the evaluation must land evaluated: {evaluated!r}"
        )

        report = evaluated.get("report")
        assert isinstance(report, dict), (
            f"the evaluated intake must expose the report (§1f.2): {evaluated!r}"
        )
        # fixed columns: feasibility / duplicates / conflicts / title / form
        feasibility = report["feasibility"]
        assert feasibility["verdict"] in ("feasible", "blocked"), (
            f"the feasibility verdict stays inside its closed set: {feasibility!r}"
        )
        assert isinstance(feasibility.get("blockers"), list)
        duplicates = report["duplicates"]
        assert isinstance(duplicates["found"], bool)
        for item in duplicates.get("items", []):
            assert set(item) >= {"ref", "summary", "suggestion"}, (
                f"a duplicate citation names ref/summary/suggestion: {item!r}"
            )
            assert item["suggestion"] in ("no_go", "enhance"), (
                f"the duplicate suggestion stays inside its closed set: {item!r}"
            )
        conflicts = report["conflicts"]
        assert isinstance(conflicts["found"], bool)
        for pair in conflicts.get("pairs", []):
            assert set(pair) >= {"new_item", "existing_ref"}, (
                f"a conflict pair names both sides (§1f.2): {pair!r}"
            )
        assert isinstance(report.get("title"), str) and report["title"].strip(), (
            f"the suggested title is non-empty (§1f.2): {report!r}"
        )
        assert report.get("input_form") in ("raw", "complete"), (
            f"the input form stays inside its closed set: {report!r}"
        )
        assert isinstance(report.get("summary"), str)

        # report columns only: the machine still accepts every action on a
        # flagged report — no automatic rejection is observable (§1o.3)
        status, body = _decide(
            base, cookie, csrf, intake_id,
            {"action": "backlog"},
            "intake-report-decide-1",
        )
        assert status == 202, (
            f"a flagged report must not trigger runtime interception: "
            f"{status} {body[:200]}"
        )
    finally:
        stop_serve(proc)
