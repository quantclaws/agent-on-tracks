"""Review decision contract: return writes the doc thread first, then the
review event (IF-REVIEWDECISION-001, IF-GATEUI-001, IF-DOCSWRITE-001).

AC-FR0359-01 (integration half): ``POST /api/runs/{run_id}/review`` with
``action=comment`` requires non-empty comments, writes one document root
thread before recording ``human.review`` (payload carries comments/doc/
thread_id), and the gate proceeds. Replays with the same idempotency key
produce neither a second event nor a second thread. A missing comment is
``validation_failed`` with no write.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests._support.v012_install import git_init
from tests._support.v012_runs import (
    events,
    prepare_home,
    seed_awaiting_review,
    wait_event,
    write_docs,
)
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
from tracks.discuss.parser import parse_threads

pytestmark = pytest.mark.integration

_VERSION = "v1.0"
_RUN_ID = "run-review-1"
_COMMENTS = "请补充验收边界后再通过。"


@pytest.fixture
def review_serve(tmp_path: Path):
    repo = git_init(tmp_path / "repo")
    home = tmp_path / "home"
    prepare_home(home, repo, project_id="proj-review", version=_VERSION)
    vdir = write_docs(repo, _VERSION, body="评审轮正文\n")
    store = seed_awaiting_review(repo, _RUN_ID, version=_VERSION, stage="M-STORY")
    store.close()
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        yield base, cookie, csrf, repo, vdir
    finally:
        stop_serve(proc)


def _post_review(base, cookie, csrf, payload, key):
    return http_post(
        base,
        f"/api/runs/{_RUN_ID}/review",
        payload,
        cookies=cookie,
        csrf=csrf,
        idempotency_key=key,
    )


def _threads(vdir: Path):
    return parse_threads((vdir / "story.md").read_text(encoding="utf-8"))


def _wait_command(base, cookie, command_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        status, body = http_get(base, f"/api/commands/{command_id}", cookies=cookie)
        assert status == 200, body[:200]
        last = json.loads(body)
        if last.get("status") in ("completed", "failed"):
            return last
        time.sleep(0.2)
    raise AssertionError(f"command {command_id} never settled: {last!r}")


# AC-FR0359-01@v0.12 TRACKS-TRACE return writes thread then human review event
def test_return_writes_thread_then_review_event(review_serve):
    """AC-FR0359-01: the return decision refuses missing comments, then
    writes exactly one parseable root thread and one ``human.review`` event
    carrying comments/doc/thread_id; the command completes (gate proceeds)
    and a replayed submission adds neither a thread nor an event."""
    base, cookie, csrf, repo, vdir = review_serve
    before_events = len(events(repo, _RUN_ID))

    missing = _post_review(base, cookie, csrf, {"action": "comment"}, "review-missing-1")
    assert missing[0] == 422, missing[1][:200]
    assert json.loads(missing[1])["error"]["reason"] == "validation_failed"
    assert len(_threads(vdir)) == 0
    assert len(events(repo, _RUN_ID)) == before_events

    wrong_action = _post_review(
        base,
        cookie,
        csrf,
        {"action": "no_comment", "comments": _COMMENTS},
        "review-wrong-action-1",
    )
    assert wrong_action[0] == 422, wrong_action[1][:200]

    status, body = _post_review(
        base,
        cookie,
        csrf,
        {"action": "comment", "comments": _COMMENTS},
        "review-comment-1",
    )
    assert status in (200, 202), f"the return decision must be accepted: {body[:200]}"
    command_id = json.loads(body)["command_id"]

    event = wait_event(repo, _RUN_ID, "human.review")
    payload = event["payload"]
    assert payload["comments"] == _COMMENTS
    assert payload["doc"] == "story"
    assert payload.get("thread_id"), "the event must carry the written thread id"
    assert payload.get("actor"), "the event must carry the acting principal"

    threads = _threads(vdir)
    assert len(threads) == 1, f"exactly one root thread must be written: {threads!r}"
    thread = threads[0]
    assert thread.status == "open"
    assert _COMMENTS in thread.root.body
    assert thread.thread_id == payload["thread_id"]
    assert thread.initiator == payload["actor"]

    settled = _wait_command(base, cookie, command_id)
    assert settled["status"] == "completed", settled

    replay_status, replay_body = _post_review(
        base,
        cookie,
        csrf,
        {"action": "comment", "comments": _COMMENTS},
        "review-comment-1",
    )
    assert replay_status in (200, 202), replay_body[:200]
    assert json.loads(replay_body)["command_id"] == command_id
    time.sleep(0.5)
    human_events = [
        item for item in events(repo, _RUN_ID) if item["type"] == "human.review"
    ]
    assert len(human_events) == 1, (
        f"an idempotent replay must not repeat the domain event: {human_events!r}"
    )
    assert len(_threads(vdir)) == 1, "an idempotent replay must not repeat the thread"


# AC-FR0359-01@v0.12 TRACKS-TRACE thread write failure fails the command
def test_thread_write_failure_fails_the_command_without_the_event(review_serve):
    """AC-FR0359-01: when the document thread cannot be written, the review
    command fails, no ``human.review`` event is recorded and no thread lands —
    a dropped decision must not advance the gate silently (§1h.3, §10 #22).

    The failure is injected black-box: the version's document directory and
    its documents are made read-only, so neither an in-place write nor a
    write-temp-then-rename can land (§6.2: no installer/reviewer internals
    are mocked).
    """
    base, cookie, csrf, repo, vdir = review_serve
    before_events = len(events(repo, _RUN_ID))
    documents = sorted(vdir.glob("*.md"))
    dir_mode = vdir.stat().st_mode
    file_modes = {path: path.stat().st_mode for path in documents}
    vdir.chmod(0o555)
    for path in documents:
        path.chmod(0o444)
    try:
        status, body = _post_review(
            base,
            cookie,
            csrf,
            {"action": "comment", "comments": _COMMENTS},
            "review-thread-write-failed-1",
        )
        assert status in (200, 202), (
            f"the decision is accepted and fails in the worker: {body[:200]}"
        )
        command_id = json.loads(body)["command_id"]
        settled = _wait_command(base, cookie, command_id)
        assert settled["status"] == "failed", (
            f"a failed thread write must fail the command: {settled!r}"
        )
    finally:
        for path, mode in file_modes.items():
            path.chmod(mode)
        vdir.chmod(dir_mode)
    assert len(events(repo, _RUN_ID)) == before_events, (
        "a failed thread write must not record human.review"
    )
    assert len(_threads(vdir)) == 0, "a failed thread write must not leave a thread"


# AC-FR0359-01@v0.12 TRACKS-TRACE a second decision with a fresh key is refused
def test_second_decision_with_a_fresh_key_is_refused(review_serve):
    """AC-FR0359-01: once the review gate has been recorded, a further
    decision under a fresh idempotency key is refused by the awaiting
    precondition (422 ``awaiting_mismatch``) and writes neither a second
    ``human.review`` event nor a second thread (§1h.2, §1h.4)."""
    base, cookie, csrf, repo, vdir = review_serve
    status, body = _post_review(
        base, cookie, csrf, {"action": "comment", "comments": _COMMENTS}, "review-first-1"
    )
    assert status in (200, 202), body[:200]
    settled = _wait_command(base, cookie, json.loads(body)["command_id"])
    assert settled["status"] == "completed", settled
    wait_event(repo, _RUN_ID, "human.review")

    second_status, second_body = _post_review(
        base, cookie, csrf, {"action": "comment", "comments": _COMMENTS}, "review-second-1"
    )
    assert second_status == 422, second_body[:200]
    assert json.loads(second_body)["error"]["reason"] == "awaiting_mismatch", (
        second_body[:200]
    )
    time.sleep(0.5)
    human_events = [
        item for item in events(repo, _RUN_ID) if item["type"] == "human.review"
    ]
    assert len(human_events) == 1, (
        f"a refused decision must not add a second event: {human_events!r}"
    )
    assert len(_threads(vdir)) == 1, "a refused decision must not add a second thread"
