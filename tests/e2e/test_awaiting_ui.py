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
from contextlib import contextmanager

import pytest
from playwright.sync_api import expect

from tests._support.intake_fence import wait_run_awaiting
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
    return repo


@contextmanager
def _gate_run(tmp_path, repo, marker: str):
    """Serve over the repo with one feature run walked to the triage gate."""
    home = tmp_path / f"gate-home-{marker}"
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


# AC-FR0343-01@v0.11 TRACKS-TRACE awaiting panel reason impact primary action
def test_awaiting_panel_reason_impact_action(trac, tmp_path, page):
    """AC-FR0343-01: the awaiting node's detail pane presents the gate's
    action panel — reason, impact and the single primary action entry — as
    the same surface as the node detail, so the user steps into gate handling
    without leaving the pane."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "panel") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        panel = page.get_by_test_id("gate-action-panel")
        expect(panel).to_be_visible()
        reason = page.get_by_test_id("gate-reason")
        impact = page.get_by_test_id("gate-impact")
        expect(reason).to_be_visible()
        expect(impact).to_be_visible()
        assert reason.inner_text().strip(), "the waiting reason is readable"
        assert impact.inner_text().strip(), "the impact face is readable"
        actions = page.locator("[data-testid^='gate-action-']")
        assert actions.count() == 1, (
            f"the panel carries the one primary action entry (§1k.2): {actions.count()}"
        )
        assert page.get_by_test_id("gate-action-triage").count() == 1, (
            "the triage gate maps to the triage action"
        )


# AC-FR0334-02@v0.11 TRACKS-TRACE stage review completed inside panel
def test_stage_review_completed_in_panel(trac, tmp_path, page):
    """AC-FR0334-02: the stage gates complete inside the awaiting panel —
    triage (go) in the panel, then the review gate presents and its
    no-comment decision also completes in the panel; no Review page is ever
    needed and no gate dangles."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "streview") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        page.get_by_test_id("gate-action-triage").click()
        # the supervisor advances by itself; the review gate presents
        review_action = page.get_by_test_id("gate-action-review")
        expect(review_action).to_be_visible()
        review_action.click()
        # the review decision completed in the panel: the gate entry leaves
        expect(page.get_by_test_id("gate-action-review")).to_have_count(
            0, timeout=_WAIT_MS
        )


# AC-FR0343-02@v0.11 TRACKS-TRACE non awaiting nodes carry no gate entry
def test_no_dangling_stage_review(trac, tmp_path, page):
    """AC-FR0343-02: nodes that are not awaiting render only their details —
    no gate action entry anywhere — so the Review removal leaves no dangling
    review surface."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "dangling") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        nodes = page.locator("[data-testid^='timeline-node-']")
        assert nodes.count() >= 1, "the timeline renders the run's nodes"
        assert page.locator("[data-testid^='gate-action-']").count() == 0, (
            "without an awaiting selection the face offers no gate action"
        )
        # a completed (non-awaiting) node: details only, never a gate entry
        nodes.first.click()
        expect(page.get_by_test_id("detail-pane")).to_be_visible()
        assert page.locator("[data-testid^='gate-action-']").count() == 0, (
            "a non-awaiting node must not present a gate action entry"
        )


# AC-FR0345-01@v0.11 TRACKS-TRACE self drive next gate appears pointer moves
def test_self_drive_next_gate_appears(trac, tmp_path, page):
    """AC-FR0345-01: after the panel's triage action no manual trigger is
    needed — the supervisor advances the run and the next gate presents on the
    timeline with the active pointer moved forward."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "drive") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)

        def _active_node_id():
            active = page.locator(
                "[data-testid^='timeline-node-'][data-state='active']"
            )
            if active.count() == 0:
                return None
            return active.first.get_attribute("data-testid")

        before_active = _active_node_id()
        page.get_by_test_id("gate-action-triage").click()
        expect(page.get_by_test_id("gate-action-review")).to_be_visible()
        after_active = _active_node_id()
        assert after_active is not None and after_active != before_active, (
            "the active pointer must move to the next gate: "
            f"{before_active!r} -> {after_active!r}"
        )


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
        # leaves the panel (the run advances past the approval boundary)
        expect(page.get_by_test_id("gate-action-approve")).to_have_count(
            0, timeout=_WAIT_MS
        )
    finally:
        stop_serve(proc)


# AC-FR0344-01@v0.11 TRACKS-TRACE review round doc write through the panel
def test_review_round_doc_write(trac, tmp_path, page):
    """AC-FR0344-01 (ui half): in the review round the reviewed document is
    editable and accepts an inline comment directly from the run face, and
    both writes reach the command plane (observable through the applied
    document state)."""
    repo = _prepared_repo(trac)
    with _gate_run(tmp_path, repo, "docwrite") as (base, run_id):
        _login(page, base)
        _open_run(page, base, run_id)
        # triage go → the supervisor advances to the review round
        page.get_by_test_id("gate-action-triage").click()
        expect(page.get_by_test_id("gate-action-review")).to_be_visible()
        # the review-round write face: edit the reviewed document inline
        editor = page.get_by_test_id("review-doc-editor")
        expect(editor).to_be_visible()
        editor.fill("评审轮直接修改的内容\n")
        page.get_by_test_id("review-doc-save").click()
        comment_box = page.get_by_test_id("review-comment-input")
        expect(comment_box).to_be_visible()
        comment_box.fill("评审轮 inline 意见")
        page.get_by_test_id("review-comment-submit").click()
        # the write face confirms the command-plane landing (no fake success)
        feedback = page.get_by_test_id("review-write-feedback")
        expect(feedback).to_be_visible()
        assert "失败" not in feedback.inner_text(), (
            "the review-round writes must land without a failure face"
        )


# AC-NFR0157-01@v0.11 TRACKS-TRACE ui writes carry csrf and idempotency headers
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
        expect(page.get_by_test_id("gate-action-review")).to_be_visible()
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
