"""Out-of-band (OOB) run workflow UI journeys (IF-STAGEPATH-001,
IF-STAGEWORKFLOW-001, IF-ROUNDS-001, IF-RUNCARD-001, IF-REVIEWDECISION-001,
IF-WEBUI-001).

AC-FR0355: the 13-stage path, five-state labels, node times and gate wait.
AC-FR0356: the stage's actual workflow aggregate with repeat folding and the
human-gate end. AC-FR0357: lazy per-round list/detail with four zones and
the thinking degradation. AC-FR0358: readable card, local times and a gate
wait that heartbeats do not reset. AC-FR0359: two review decisions, the
returned comment landing as a document thread, terse normal gates, retained
exception reason/impact and inline failure feedback.

All operations are real browser actions through ``data-testid`` anchors on
the same document; observations are DOM text/attributes, persisted files and
the event plane (never a second UI synthesis).
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from playwright.sync_api import expect

from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  constants
from tests._support.v012_runs import (
    events,
    seed_awaiting_review,
    seed_exception_gate,
    seed_heartbeat_wait,
    seed_no_stage_events,
    seed_rounds,
    seed_stage_path_run,
    seed_workflow_gate_run,
    seed_workflow_run,
    wait_event,
    write_docs,
)
from tracks.discuss.parser import parse_threads

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_VERSION = "v1.0"
_STAGES = (
    "M-START",
    "M-STORY",
    "M-SPEC",
    "M-ACC",
    "M-REQ-APPROVAL",
    "M-DESIGN",
    "M-TEST",
    "M-IMPL",
    "M-VERIFY",
    "M-SECURITY",
    "M-RELEASE",
    "M-PUBLISH",
    "M-MILESTONE",
)
_FIVE_STATES = {"done", "active", "pending", "attention", "void"}
_LOCAL_TIME = re.compile(r"start/end: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} → ")


def _waiting_seconds(text: str) -> int:
    match = re.search(r"已等待\s*([^·]+)", text)
    assert match, text
    seconds = 0
    for value, unit in re.findall(r"(\d+)(天|小时|分|秒)", match.group(1)):
        seconds += int(value) * {"天": 86400, "小时": 3600, "分": 60, "秒": 1}[unit]
    return seconds


def _seed_run(ui_serve, run_id: str, seeder, *, project_id: str | None = None, **kwargs):
    if project_id is not None:
        ui_serve.seed_project(project_id, _VERSION)
    result = seeder(ui_serve.repo, run_id, version=_VERSION, **kwargs)
    if hasattr(result, "close"):
        result.close()


def _login_and_open_run(ui_serve, page, run_id: str) -> None:
    page.set_default_timeout(10_000)
    page.goto(ui_serve.base_url + "/login", wait_until="domcontentloaded")
    page.get_by_test_id("login-password").fill(SERVE_PASSWORD)
    page.get_by_test_id("login-submit").click()
    page.get_by_test_id("name-input").fill("UI Human")
    page.get_by_test_id("name-submit").click()
    page.get_by_test_id("tabbar-item-releases").wait_for()
    page.goto(ui_serve.base_url + f"/runs/{run_id}", wait_until="domcontentloaded")
    page.get_by_test_id("main-area").wait_for()
    expect(page.get_by_test_id("current-node-card")).to_be_visible(timeout=15_000)


def _expand_rounds(page, row: int) -> None:
    page.get_by_test_id("stage-rounds").locator("summary").first.click()
    detail = page.get_by_test_id(f"stage-round-{row}")
    detail.wait_for()
    detail.locator("summary").first.click()
    expect(detail.locator("h5").first).to_be_visible(timeout=15_000)


# AC-FR0355-01@v0.12 TRACKS-TRACE stage path states times and gate wait
def test_stage_path_states_times_and_gate_wait(ui_serve, page):
    """AC-FR0355-01: the 13-stage path distinguishes done/current/pending
    through ``data-state``, marks the current gate ``attention``, exposes
    the back edge, and a selected node shows its start/end/owner plus the
    owning-surface entry; the card shows the gate wait."""
    _seed_run(ui_serve, "run-path", seed_stage_path_run, project_id="proj-path")
    _login_and_open_run(ui_serve, page, "run-path")

    order = page.get_by_test_id("timeline-stage-order")
    expect(order).to_be_visible()
    for stage in _STAGES:
        assert stage in order.inner_text(), f"the path must show {stage}"
    nodes = page.locator("[data-testid^='timeline-node-']")
    assert nodes.count() == len(_STAGES)
    states = {
        nodes.nth(index).get_attribute("data-state") for index in range(nodes.count())
    }
    assert states <= _FIVE_STATES, f"only the five-state vocabulary: {states}"
    for required in ("done", "attention", "pending", "void"):
        assert required in states, f"the path must render a {required} node"
    assert page.get_by_test_id("timeline-back-edge-M-STORY-M-ACC").count() == 1
    assert (
        page.get_by_test_id("timeline-node-M-SPEC").get_attribute("data-state")
        == "attention"
    )
    status = page.get_by_test_id("card-status")
    expect(status).to_be_visible()
    assert "已等待" in status.inner_text(), "the current gate shows its wait"

    page.get_by_test_id("timeline-node-M-START").click()
    detail = page.get_by_test_id("node-detail")
    expect(detail).to_be_visible()
    text = detail.inner_text()
    assert "state: done" in text
    assert _LOCAL_TIME.search(text), f"start/end local times must render: {text!r}"
    assert "owner:" in text
    assert page.get_by_test_id("detail-owning-surface").count() == 1
    assert page.get_by_test_id("detail-doc-link").count() == 1


# AC-FR0355-02@v0.12 TRACKS-TRACE stage path degradation readable
def test_stage_path_degradation_readable(ui_serve, page):
    """AC-FR0355-02: a run without stage events still renders the 13 labels,
    a selected node degrades to the readable "no recorded events" line
    (never a fabricated timestamp), and an unknown run says Unknown run."""
    _seed_run(ui_serve, "run-degrade", seed_no_stage_events, project_id="proj-degrade")
    _login_and_open_run(ui_serve, page, "run-degrade")
    expect(page.get_by_test_id("timeline-stage-order")).to_be_visible()
    nodes = page.locator("[data-testid^='timeline-node-']")
    assert nodes.count() == len(_STAGES), "the path still renders all 13 labels"
    page.get_by_test_id("timeline-node-M-START").click()
    detail = page.get_by_test_id("node-detail")
    expect(detail).to_be_visible()
    text = detail.inner_text()
    assert "no recorded events for M-START yet" in text
    assert "start/end:" not in text, "missing data must not fabricate timestamps"

    page.goto(ui_serve.base_url + "/runs/does-not-exist", wait_until="domcontentloaded")
    page.get_by_text("Unknown run: does-not-exist").wait_for()


# AC-FR0356-01@v0.12 TRACKS-TRACE stage workflow aggregate folds
def test_stage_workflow_aggregated_folds(ui_serve, page):
    """AC-FR0356-01: the selected stage's actual workflow renders the summary
    counts (dispatches, rounds, human actions) and the mermaid aggregate with
    the repeated-dispatch fold, ending at the current stage."""
    _seed_run(ui_serve, "run-workflow", seed_workflow_run, project_id="proj-workflow")
    _login_and_open_run(ui_serve, page, "run-workflow")
    panel = page.get_by_test_id("stage-path")
    expect(panel).to_be_visible()
    summary = page.get_by_test_id("stage-workflow-summary")
    expect(summary).to_be_visible()
    text = summary.inner_text()
    assert "派发 4 次" in text, text
    assert "交互轮次 1" in text, text
    assert "人工动作 1" in text, text
    diagram = page.get_by_test_id("stage-workflow-diagram")
    expect(diagram.locator("svg")).to_be_visible(timeout=30_000)
    svg_text = diagram.text_content() or ""
    assert "重派" in svg_text, f"repeated dispatches must fold: {svg_text!r}"
    assert "RED" in svg_text and "GREEN" in svg_text
    # the aggregate terminal is the current gate: a dispatch-bearing window
    # whose breaker tripped must still render the human gate, not a bare
    # stage node (AC-FR0356-01).
    _seed_run(ui_serve, "run-workflow-gate", seed_workflow_gate_run)
    page.goto(
        ui_serve.base_url + "/runs/run-workflow-gate", wait_until="domcontentloaded"
    )
    page.get_by_test_id("main-area").wait_for()
    expect(page.get_by_test_id("current-node-card")).to_be_visible(timeout=15_000)
    gate_svg = page.get_by_test_id("stage-workflow-diagram").text_content() or ""
    assert "人工门" in gate_svg and "等待裁决" in gate_svg, gate_svg


# AC-FR0357-01@v0.12 TRACKS-TRACE per-round lazy accordion four zones
def test_per_round_detail_lazy_accordion(ui_serve, page):
    """AC-FR0357-01: the round list is fetched only on first expansion; a
    round row fetches its detail once and shows the four titled zones with
    input, output and tool counts (cached on re-expansion)."""
    _seed_run(ui_serve, "run-rounds", seed_rounds, project_id="proj-rounds", count=2)
    requests: list[str] = []
    page.on(
        "request",
        lambda request: requests.append(request.url) if "/rounds" in request.url else None,
    )
    _login_and_open_run(ui_serve, page, "run-rounds")
    expect(page.get_by_test_id("stage-path")).to_be_visible()
    assert not [url for url in requests if "/stages/M-TEST/rounds" in url], (
        "the rounds list must not load before the first expansion"
    )
    _expand_rounds(page, 1)
    rows = page.locator("[data-testid^='stage-round-']")
    assert rows.count() == 2
    row = page.get_by_test_id("stage-round-1")
    expect(row).to_be_visible()
    assert [title.inner_text() for title in row.locator("h5").all()] == [
        "输入",
        "输出",
        "思考过程",
        "工具",
    ]
    row_text = row.inner_text()
    assert "round 1 prompt" in row_text
    assert "round 1 output" in row_text
    assert "read ×1" in row_text
    detail_hits = [url for url in requests if "/rounds/cmd-round-1" in url]
    assert len(detail_hits) == 1, f"the detail fetches once: {detail_hits!r}"
    row.locator("summary").first.click()
    page.get_by_test_id("stage-round-1").locator("summary").first.click()
    page.wait_for_timeout(300)
    assert len([url for url in requests if "/rounds/cmd-round-1" in url]) == 1, (
        "a terminal round stays cached on re-expansion"
    )


# AC-FR0357-02@v0.12 TRACKS-TRACE round thinking missing message
def test_round_thinking_missing_message(ui_serve, page):
    """AC-FR0357-02: a rotated session log yields the readable "会话日志已轮转
    清理" explanation while input/output stay readable — no fabricated
    thinking text."""
    _seed_run(ui_serve, "run-thinking", seed_rounds, project_id="proj-thinking", count=1)
    _login_and_open_run(ui_serve, page, "run-thinking")
    _expand_rounds(page, 1)
    row = page.get_by_test_id("stage-round-1")
    thinking = row.locator(".stage-round-thinking")
    thinking.locator("summary").click()
    expect(thinking).to_contain_text("该轮的会话日志已轮转清理，仅存输入/输出")
    row_text = row.inner_text()
    assert "round 1 prompt" in row_text
    assert "round 1 output" in row_text


# AC-FR0358-01@v0.12 TRACKS-TRACE run card local time heartbeat not reset
def test_run_card_local_time_and_heartbeat_not_reset(ui_serve, page):
    """AC-FR0358-01: the card shows Chinese labels/human durations, node
    times are browser-local, and the gate wait is measured from the last
    business event — a heartbeat does not reset it."""
    _seed_run(ui_serve, "run-card", seed_heartbeat_wait, project_id="proj-card")
    _login_and_open_run(ui_serve, page, "run-card")
    for field in ("card-owner", "card-attempt", "card-duration", "card-status"):
        assert page.get_by_test_id(field).count() == 1, field
    duration = page.get_by_test_id("card-duration").inner_text()
    assert duration.startswith("已运行"), duration
    status = page.get_by_test_id("card-status").inner_text()
    assert _waiting_seconds(status) >= 60, (
        f"the wait must start at the last business event, not the heartbeat: {status!r}"
    )

    page.get_by_test_id("timeline-node-M-STORY").click()
    detail = page.get_by_test_id("node-detail").inner_text()
    assert _LOCAL_TIME.search(detail), detail
    entered = next(
        event for event in events(ui_serve.repo, "run-card")
        if event["type"] == "stage.entered"
    )
    expected = datetime.fromisoformat(entered["ts"]).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert expected in detail, f"the pane must render browser-local time: {detail!r}"


# AC-FR0359-01@v0.12 TRACKS-TRACE review two decisions thread and continue
def test_review_two_decisions_thread_and_continue(ui_serve, page):
    """AC-FR0359-01: the review face offers only approve/return; approve
    permits empty comments and advances; return requires comments, writes
    them as a document root thread before the event, and the empty ac-chain
    is hidden rather than blank."""
    write_docs(ui_serve.repo, _VERSION)
    _seed_run(ui_serve, "run-review-approve", seed_awaiting_review, project_id="proj-review")
    _seed_run(ui_serve, "run-review-return", seed_awaiting_review)

    _login_and_open_run(ui_serve, page, "run-review-approve")
    expect(page.get_by_test_id("gate-panel")).to_be_visible()
    assert page.get_by_test_id("review-approve").count() == 1
    assert page.get_by_test_id("review-return").count() == 1
    assert page.get_by_test_id("review-return").is_disabled()
    assert not page.get_by_test_id("review-approve").is_disabled()
    assert page.get_by_test_id("ac-chain").count() == 0, (
        "an empty acceptance chain is hidden, not left as a blank placeholder"
    )
    page.get_by_test_id("review-approve").click()
    approved = wait_event(ui_serve.repo, "run-review-approve", "human.review")
    assert approved["payload"].get("action") == "no_comment"
    assert "comments" not in approved["payload"]
    for key in ("doc", "thread_id"):
        assert key not in approved["payload"]
    expect(page.get_by_test_id("review-approve")).to_have_count(0, timeout=120_000)

    page.goto(ui_serve.base_url + "/runs/run-review-return", wait_until="domcontentloaded")
    page.get_by_test_id("main-area").wait_for()
    comments = "退回：请补充验收边界。"
    page.get_by_test_id("review-comments").fill(comments)
    assert not page.get_by_test_id("review-return").is_disabled()
    assert page.get_by_test_id("review-approve").is_disabled()
    page.get_by_test_id("review-return").click()
    returned = wait_event(ui_serve.repo, "run-review-return", "human.review")
    assert returned["payload"].get("comments") == comments
    threads = parse_threads(
        (ui_serve.repo / ".tracks" / "projects" / _VERSION / "story.md").read_text(
            encoding="utf-8"
        )
    )
    assert len(threads) == 1, f"exactly one root thread must be written: {threads!r}"
    assert threads[0].status == "open"
    assert comments in threads[0].root.body
    assert returned["payload"].get("thread_id") == threads[0].thread_id
    feedback = page.get_by_test_id("review-feedback")
    assert feedback.get_attribute("data-failed") != "true", (
        "a landed return must not show the failure face"
    )


# AC-FR0359-02@v0.12 TRACKS-TRACE normal gate terse exception keeps reason impact inline failure
def test_normal_gate_terse_exception_keeps_reason_impact_and_inline_failure(
    ui_serve, page
):
    """AC-FR0359-02: a normal review gate renders no reason/impact; an
    exception gate keeps both; a failed review write surfaces the inline
    feedback with ``data-failed=true`` (never a fake success)."""
    write_docs(ui_serve.repo, _VERSION)
    _seed_run(ui_serve, "run-normal-gate", seed_awaiting_review, project_id="proj-gates")
    _seed_run(ui_serve, "run-exception-gate", seed_exception_gate)

    _login_and_open_run(ui_serve, page, "run-normal-gate")
    expect(page.get_by_test_id("gate-panel")).to_be_visible()
    assert page.get_by_test_id("gate-reason").count() == 0, (
        "a normal review gate must not stack template reason text"
    )
    assert page.get_by_test_id("gate-impact").count() == 0

    page.route(
        "**/api/runs/run-normal-gate/review",
        lambda route: route.abort(),
    )
    page.get_by_test_id("review-approve").click()
    feedback = page.get_by_test_id("review-feedback")
    expect(feedback).to_be_visible(timeout=15_000)
    assert feedback.get_attribute("data-failed") == "true"
    assert "失败" in feedback.inner_text()
    page.unroute("**/api/runs/run-normal-gate/review")

    page.goto(ui_serve.base_url + "/runs/run-exception-gate", wait_until="domcontentloaded")
    page.get_by_test_id("main-area").wait_for()
    expect(page.get_by_test_id("gate-reason")).to_be_visible()
    expect(page.get_by_test_id("gate-impact")).to_be_visible()
    assert page.get_by_test_id("gate-reason").inner_text().strip()
    assert page.get_by_test_id("gate-impact").inner_text().strip()


# AC-NFR0160-01@v0.12 TRACKS-TRACE rounds lazy first paint many rounds
def test_rounds_lazy_first_paint(ui_serve, page):
    """AC-NFR0160-01 (UI half): a large round count does not fetch or block
    the first paint; the rounds are fetched only when the execution detail
    is expanded, then remain navigable."""
    _seed_run(ui_serve, "run-large", seed_rounds, project_id="proj-large", count=12)
    requests: list[str] = []
    page.on(
        "request",
        lambda request: requests.append(request.url) if "/rounds" in request.url else None,
    )
    _login_and_open_run(ui_serve, page, "run-large")
    expect(page.get_by_test_id("timeline")).to_be_visible()
    assert not [url for url in requests if "/rounds" in url], (
        "the first paint must not fetch per-round data"
    )
    page.get_by_test_id("stage-rounds").locator("summary").first.click()
    page.get_by_test_id("stage-round-12").wait_for(timeout=15_000)
    assert page.locator("[data-testid^='stage-round-']").count() == 12
    assert [url for url in requests if "/stages/M-TEST/rounds" in url]
