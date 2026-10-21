"""Self-drive gate UI journey (IF-WEBUI-001, IF-GATEUI-001, IF-AWAIT-001,
IF-DRIVE-001, IF-DOCSWRITE-001, IF-WEBGATE-001).

The §9.4 browser happy path: a run arrives at the triage gate → the detail
pane's action panel (reason/impact/one primary action) → the gate completes
inside the panel → the supervisor advances by itself → the next gate presents
on the timeline with the active pointer moved → the review round's write face
→ the stage completes. Every write carries CSRF + Idempotency-Key.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager

import pytest
from playwright.sync_api import expect

from tests._support.intake_fence import wait_run_awaiting
from tests._support.ui_browser import SERVE_PASSWORD  # noqa: F401  constants
from tests._support.v012_runs import wait_event
from tests._support.v09_web import (
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tracks.discuss.parser import parse_threads

pytestmark = [pytest.mark.e2e, pytest.mark.ui]

_WAIT_MS = 8_000

_RUN_PARAMS = {
    "journey": "feature",
    "version": "v0.9",
    "story": None,
    "issue": None,
    "target": None,
    "preempt": False,
}


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
    # the review-round write face lives in the Docs window (§11 row 3): seed
    # the run's version trio so the document is selectable there.
    vdir = repo / ".tracks" / "projects" / "v0.9"
    vdir.mkdir(parents=True, exist_ok=True)
    for name in ("story.md", "spec.md", "acceptance.md"):
        (vdir / name).write_text(
            "---\nenvelope: tracks-envelope:v2\n---\n\n"
            f"# v0.9 {name}\n\nreview-round body\n",
            encoding="utf-8",
        )
    return repo


@contextmanager
def _gate_run(tmp_path, repo, marker: str):
    """Serve over the repo with one feature run walked to the triage gate."""
    home = tmp_path / f"gate-home-{marker}"
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base, collect_name=False)
        status, body = http_post(
            base,
            "/api/projects",
            {"repo_path": str(repo)},
            cookies=cookie,
            csrf=csrf,
            idempotency_key=f"awaiting-reg-{marker}",
        )
        assert status == 201, f"registration must succeed: {status} {body[:200]}"
        project_id = json.loads(body)["project_id"]
        status, body = http_post(
            base,
            f"/api/projects/{project_id}/runs",
            _RUN_PARAMS,
            cookies=cookie,
            csrf=csrf,
            idempotency_key=f"awaiting-run-{marker}",
        )
        assert status == 202, f"run creation must be accepted: {status} {body[:200]}"
        run_id = json.loads(body)["run_id"]
        wait_run_awaiting(base, cookie, run_id, ("triage",))
        yield base, run_id
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


def _open_run(page, base_url: str, run_id: str) -> None:
    page.goto(base_url + f"/runs/{run_id}", wait_until="domcontentloaded")
    expect(page.get_by_test_id("detail-pane")).to_be_visible()


_COMMENTS = "退回：请补充验收边界。"


def _submit_return(page, comments: str = _COMMENTS) -> None:
    page.get_by_test_id("review-comments").fill(comments)
    expect(page.get_by_test_id("review-return")).to_be_enabled()
    page.get_by_test_id("review-return").click()


def _assert_review_thread(repo, run_id: str, comments: str = _COMMENTS) -> None:
    """Assert the v0.12 doc-thread write (AC-FR0359-01).

    The OOB panel face is current; the missing behavior is the contract's
    "return writes a document root thread before human.review". Until the
    implementation lands this assertion is a legal Red (not an element miss).
    """
    event = wait_event(repo, run_id, "human.review")
    payload = event["payload"]
    assert payload.get("comments") == comments, payload
    assert payload.get("doc") == "story", payload
    assert payload.get("thread_id"), (
        "the return decision must carry the written document thread id"
    )
    story = (repo / ".tracks" / "projects" / "v0.9" / "story.md").read_text(
        encoding="utf-8"
    )
    threads = parse_threads(story)
    assert any(comments in thread.root.body for thread in threads), threads


def _return_with_thread(page, repo, run_id: str, comments: str = _COMMENTS) -> None:
    _submit_return(page, comments)
    _assert_review_thread(repo, run_id, comments)


# AC-FR0343-01@v0.11 TRACKS-TRACE awaiting panel primary action normal gate terse
# AC-FR0359-01@v0.12 TRACKS-TRACE the return decision writes the document root thread before human.review
def test_awaiting_panel_reason_impact_action(trac, tmp_path, page):
    """AC-FR0343-01 (v0.12 face): the awaiting node's detail pane presents
    the gate's action panel with its single primary action entry; the normal
    triage gate stays terse (no template reason/impact), per FR-0359-02."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "panel") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        panel = page.get_by_test_id("gate-panel")
        expect(panel).to_be_visible()
        # FR-0359-02: a normal-flow gate is terse — the template
        # reason/impact lines are reserved for exception gates (the
        # exception face is covered by tests/e2e/test_run_workflow_ui.py).
        assert page.get_by_test_id("gate-reason").count() == 0
        assert page.get_by_test_id("gate-impact").count() == 0
        actions = page.locator("[data-testid^='gate-action-']")
        assert actions.count() == 1, (
            f"the panel carries the one primary action entry (§1k.2): {actions.count()}"
        )
        assert page.get_by_test_id("gate-action-triage").count() == 1, (
            "the triage gate maps to the triage action"
        )
        page.get_by_test_id("gate-action-triage").click()
        expect(page.get_by_test_id("review-approve")).to_be_visible(timeout=120_000)
        _return_with_thread(page, repo, run_id)


# AC-FR0334-02@v0.11 TRACKS-TRACE stage review completed inside panel
# AC-FR0359-01@v0.12 TRACKS-TRACE the return decision writes the document root thread before human.review
def test_stage_review_completed_in_panel(trac, tmp_path, page):
    """AC-FR0334-02: the stage gates complete inside the awaiting panel —
    triage (go) in the panel, then the review gate presents and its return
    decision also completes in the panel; no Review page is ever needed and
    no gate dangles."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "streview") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        page.get_by_test_id("gate-action-triage").click()
        # the supervisor advances by itself; the review gate presents
        # (self-drive through DRAFT needs worker ticks — allow 120s)
        expect(page.get_by_test_id("review-approve")).to_be_visible(timeout=120_000)
        _return_with_thread(page, repo, run_id)
        # the review decision completed in the panel: the gate entry leaves
        expect(page.get_by_test_id("review-return")).to_have_count(
            0, timeout=120_000
        )


# AC-FR0343-02@v0.11 TRACKS-TRACE non awaiting nodes carry no gate entry
def test_no_dangling_stage_review(trac, tmp_path, page):
    """AC-FR0343-02: nodes that are not awaiting render only their details —
    no gate action entry in the node's pane — so the Review removal leaves no
    dangling review surface on a non-awaiting node. The run-level gate action
    lives in the current-node card and is selection-invariant."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "dangling") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        nodes = page.locator("[data-testid^='timeline-node-']")
        assert nodes.count() >= 1, "the timeline renders the run's nodes"
        # the run IS awaiting triage so the initial pane IS the gate panel
        # (one primary entry); the no-gate rule below covers non-awaiting
        # nodes only (AC-FR0343-02).
        assert page.locator("[data-testid^='gate-action-']").count() >= 1, (
            "the awaiting run presents its gate panel on load"
        )
        # a completed (non-awaiting) node: details only; the run-level gate
        # action stays in the current-node card (selection-invariant).
        nodes.first.click()
        expect(page.get_by_test_id("detail-pane")).to_be_visible()
        assert page.locator(
            "[data-testid='detail-pane'] [data-testid^='gate-action-']"
        ).count() == 0, (
            "a non-awaiting node's detail pane must not present a gate action"
        )
        assert page.locator(
            "[data-testid='current-node-card'] [data-testid^='gate-action-']"
        ).count() >= 1, (
            "the run-level gate action stays in the current-node card"
        )


# AC-FR0345-01@v0.11 TRACKS-TRACE self drive next gate appears pointer moves
# AC-FR0359-01@v0.12 TRACKS-TRACE the return decision writes the document root thread before human.review
def test_self_drive_next_gate_appears(trac, tmp_path, page):
    """AC-FR0345-01: after the panel's triage action no manual trigger is
    needed — the supervisor advances the run and the next gate presents on the
    timeline with the active pointer moved forward."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "drive") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        page.get_by_test_id("gate-action-triage").click()
        # the supervisor advances by itself; the next gate presents in the
        # panel (no manual dispatch)
        expect(page.get_by_test_id("review-approve")).to_be_visible(timeout=120_000)
        # the pointer moved forward: the triage action left the panel...
        expect(page.get_by_test_id("gate-action-triage")).to_have_count(0)
        # ...and the timeline's active marker (data-state-active rides the
        # active AND attention states, §1l.1) sits on the gate's stage node.
        # Both gates are M-STORY substates (TRIAGE → HUMAN_REVIEW), so the
        # stage node — not a different stage — is the pointer's home; the
        # observable movement is the gate face itself, asserted above.
        active = page.locator(
            "[data-testid^='timeline-node-'][data-state-active='true']"
        )
        assert active.count() == 1, (
            "exactly the gate's stage node carries the active marker"
        )
        _return_with_thread(page, repo, run_id)


# AC-FR0342-02@v0.11 TRACKS-TRACE approve and release decision in panel
def test_approve_release_in_panel(tmp_path, page):
    """AC-FR0342-02: the approval and release-decision gates complete through
    the panel over the existing endpoints — the run parked at M-REQ-APPROVAL
    shows the approve action and the landed decision is observable on the
    run's event plane."""
    from tests._support.webgate_seed import seed_approval_run

    home, repo, _current = seed_approval_run(tmp_path, "run-await-approval")
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        _login(page, base)
        _open_run(page, base, "run-await-approval")
        approve = page.get_by_test_id("gate-action-approve")
        expect(approve).to_be_visible()
        approve.click()
        # the panel's decision lands on the command plane: the gate entry
        # leaves the panel (the run advances past the approval boundary;
        # worker execution + self-drive need ticks — allow 120s)
        expect(page.get_by_test_id("gate-action-approve")).to_have_count(
            0, timeout=120_000
        )
    finally:
        stop_serve(proc)


# AC-FR0344-01@v0.11 TRACKS-TRACE review round doc write through the panel
# AC-FR0359-01@v0.12 TRACKS-TRACE the return decision writes the document root thread before human.review
def test_review_round_doc_write(trac, tmp_path, page):
    """AC-FR0344-01 (ui half): in the review round the reviewed document's
    Docs window presents the anchor-line comment composer and the published
    comment lands on the command plane (no inline document editor remains on
    the run face, §11 row 3)."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "docwrite") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        # triage go → the supervisor advances to the review round (allow 120s
        # for worker ticks + DRAFT)
        page.get_by_test_id("gate-action-triage").click()
        expect(page.get_by_test_id("review-approve")).to_be_visible(timeout=120_000)
        # the review-round write face lives in the Docs window (OOB removed
        # the run-face document editor); inline comments ride the anchor-line
        # clarification composer.
        page.get_by_test_id("tabbar-item-docs").click()
        page.locator("[data-testid='view-docs']").wait_for()
        page.get_by_test_id("doc-tree-item-v0.9-story").click(timeout=60_000)
        page.get_by_test_id("discussion-anchor-comment").wait_for(timeout=30_000)
        comment_box = page.get_by_test_id("comment-body")
        expect(comment_box).to_be_visible()
        comment_box.fill("评审轮 inline 意见")
        # the anchor rides the source caret line (§1n.3). The composer bar is
        # under the async editor surface, so focus the source and invoke the
        # publish control in the same task: the click handler reads the anchor
        # before any awaiting, and the write still goes through the app path.
        source = page.get_by_test_id("doc-editor-content")
        source.evaluate(
            """el => {
                el.focus();
                const at = el.value.indexOf('#');
                el.setSelectionRange(at, at);
                document.querySelector(
                    '[data-testid="comment-publish"]'
                ).click();
            }"""
        )
        # the write face confirms the command-plane landing (no fake success):
        # the anchor comment must appear in the document before the decision.
        story = repo / ".tracks" / "projects" / "v0.9" / "story.md"
        deadline = time.monotonic() + 30
        while "评审轮 inline 意见" not in story.read_text(encoding="utf-8"):
            assert time.monotonic() < deadline, "the inline comment never landed"
            time.sleep(0.2)
        expect(page.get_by_test_id("comment-error")).to_be_hidden()
        # the review decision itself must also write its own root thread
        # (AC-FR0359-01); the OOB face is current, the missing behavior is
        # the thread write.
        _open_run(page, base, run_id)
        _return_with_thread(page, repo, run_id)


# AC-NFR0157-01@v0.11 TRACKS-TRACE ui writes carry csrf and idempotency headers
# AC-FR0359-01@v0.12 TRACKS-TRACE the return decision writes the document root thread before human.review
def test_ui_writes_carry_headers(trac, tmp_path, page):
    """AC-NFR0157-01 (ui half): every write the gate panel issues carries the
    X-Trac-CSRF and Idempotency-Key headers — the single command-plane write
    path, replay-safe by key."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "headers") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        writes: list[dict] = []

        def capture(route):
            request = route.request
            writes.append({"method": request.method, "headers": dict(request.headers)})
            route.continue_()

        page.route("**/api/**", capture)
        page.get_by_test_id("gate-action-triage").click()
        expect(page.get_by_test_id("review-approve")).to_be_visible(timeout=120_000)
        _submit_return(page)
        posted = [w for w in writes if w["method"] == "POST"]
        assert posted, "the gate action must issue the panel's write"
        for write in posted:
            headers = {k.lower(): v for k, v in write["headers"].items()}
            assert headers.get("x-trac-csrf"), (
                f"the panel write must carry X-Trac-CSRF: {sorted(headers)}"
            )
            assert headers.get("idempotency-key"), (
                f"the panel write must carry Idempotency-Key: {sorted(headers)}"
            )
        _assert_review_thread(repo, run_id)
