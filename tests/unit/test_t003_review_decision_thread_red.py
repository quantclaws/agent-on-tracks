"""T-003 RED: review two-decision contract and return-thread write.

Devon-owned unit RED for the v0.12 OOB review slice (interfaces §1h/§1j,
IF-REVIEWDECISION-001 / IF-GATEUI-001 / IF-DOCSWRITE-001 / IF-AWAIT-001):

- acceptance shape (§1h.2): ``action=comment`` requires a non-empty
  ``comments`` string; ``action=no_comment`` requires ``comments``
  absent/empty; the opposite shape is a ``validation_failed`` refusal and
  the HTTP face answers 422 (§2c #5);
- worker execution (§1h.3): ``action=comment`` writes one open root thread
  into the current stage document FIRST (anchored at the document's first
  non-empty line, same actor as the ``human.review`` event), then lands
  ``human.review`` with ``{actor, comments, doc, thread_id}``. A thread
  write failure fails the command with ``review_thread_write_failed``,
  leaves no ``human.review`` and does not move the gate.
  ``action=no_comment`` skips the document write and carries none of the
  three keys;
- the review decision face (§1h.1): approve stays available with empty
  comments, return needs non-empty comments, and the hint names the document
  thread + Scribe dual channel (the frozen e2e anchor owns the browser
  journey).

RED discipline: every failing node guards the absent behavior into a real
``AssertionError`` (no stub tokens, no assembly errors, no ``pytest.fail``).
Fixtures seed the real run plane (``tracks.store.Store``) and real service
plane (``tracks.supervisor.db.ServiceDB``); nothing mocks the system under
test. The frozen acceptance anchor
(``tests/integration/test_review_decision_contract.py``) is never read or
run here.

AC: FR-0359 — TRACKS-TRACE IF-REVIEWDECISION-001 / IF-GATEUI-001 /
IF-DOCSWRITE-001 / IF-AWAIT-001.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import re
import sqlite3
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks import paths, templating
from tracks.discuss.parser import parse_threads
from tracks.executor.helpers import git
from tracks.frontmatter import split_frontmatter
from tracks.server.api_command import _rejection_status
from tracks.store import Store
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.gate_exec import execute_gate_command
from tracks.supervisor.service import CommandService, Rejection

_VERSION = "v0.2"
_RUN = "R-REVIEW"
_ACTOR = "web-human"
_COMMENT = "请补充第 3 条的验收样例"
_MISSING = object()
_APP = Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "app"


# -- fixtures: real run plane + real service plane ----------------------------


def _review_gate_events() -> list:
    """A Sage-passed M-STORY parks the run at awaiting=review."""
    return [
        ("story.requested", {"raw_chars": 4}),
        ("stage.entered", {"stage": "M-STORY"}),
        ("outcome.received", {"status": "done", "role": "scribe"}),
        ("sage.verdict", {"verdict": "pass"}),
    ]


def _review_repo(base: Path) -> tuple[Path, Path]:
    """A git repo whose run plane waits at the M-STORY review gate."""
    base.mkdir(parents=True, exist_ok=True)
    repo = git_repo(base)
    vdir = paths.version_dir(paths.tracks_home(repo), _VERSION)
    vdir.mkdir(parents=True, exist_ok=True)
    story = vdir / "story.md"
    story.write_text(
        templating.render_story_skeleton(
            "给工作台加一个导出按钮", datetime.date.today().isoformat()
        ),
        encoding="utf-8",
    )
    git(repo, "add", str(story))
    git(
        repo,
        "commit",
        "-m",
        f"M-START: capture raw requirement for {_VERSION}",
        "--only",
        "--",
        str(story),
    )
    store = Store(paths.tracks_home(repo))
    try:
        for event_type, payload in _review_gate_events():
            store.append(_RUN, _VERSION, event_type, payload)
        assert store.state(_RUN).awaiting == "review", (
            "fixture: the seeded M-STORY run must wait at the review gate"
        )
    finally:
        store.close()
    return repo, story


def _service_plane(home: Path, repo: Path) -> tuple[Path, ServiceDB, CommandService]:
    home.mkdir(parents=True, exist_ok=True)
    db = ServiceDB(home)
    with contextlib.closing(sqlite3.connect(home / "service.db")) as conn:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES ('P1', ?, ?, 'human',"
            " '2026-10-21T00:00:00+00:00')",
            (str(repo), _VERSION),
        )
        conn.commit()
    return home, db, CommandService(home, db, {})


# -- acceptance shape (§1h.2) -------------------------------------------------


def _try_review(service: CommandService, *, key: str, action: str, comments=_MISSING):
    """Accept one review command; return (receipt, rejection)."""
    params = {"run_id": _RUN, "action": action}
    if comments is not _MISSING:
        params["comments"] = comments
    try:
        return service.accept(
            "record_review",
            params,
            actor=_ACTOR,
            actor_class="human",
            surface="http",
            idempotency_key=key,
        ), None
    except Rejection as rejection:
        return None, rejection


def _outcome(rejection: Rejection | None) -> str:
    return "acceptance (no refusal)" if rejection is None else rejection.reason


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001 comments shape
def test_record_review_acceptance_requires_comments_only_for_comment(tmp_path: Path):
    repo, _story = _review_repo(tmp_path / "repo-base")
    _home, _db, service = _service_plane(tmp_path / "service-home", repo)

    invalid = (
        ("comment", _MISSING, "missing comments on a return"),
        ("comment", "", "empty comments on a return"),
        ("no_comment", "不该带意见", "comments on an approval"),
    )
    for index, (action, comments, label) in enumerate(invalid):
        receipt, rejection = _try_review(
            service, key=f"bad-{index}", action=action, comments=comments
        )
        assert receipt is None and rejection is not None, (
            f"{label} must be refused (§1h.2): action={action!r}"
            f" comments={comments!r} answered {_outcome(rejection)}"
        )
        assert rejection.reason == "validation_failed", (
            f"{label} carries the closed reason validation_failed (§1h.2);"
            f" got {rejection.reason}"
        )

    accepted = (
        ("comment", "需要补样例", "comment with a non-empty body"),
        ("no_comment", _MISSING, "no_comment with comments omitted"),
        ("no_comment", "", "no_comment with empty comments"),
    )
    for index, (action, comments, label) in enumerate(accepted):
        receipt, rejection = _try_review(
            service, key=f"ok-{index}", action=action, comments=comments
        )
        assert rejection is None and receipt is not None and receipt.status == "accepted", (
            f"{label} is a legal review shape (§1h.2); got {_outcome(rejection)}"
        )


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001 HTTP 422 mapping
def test_record_review_validation_failed_maps_to_422():
    assert _rejection_status("record_review", "validation_failed") == 422, (
        "§2c #5: the review face's shape refusal answers 422 validation_failed;"
        f" got {_rejection_status('record_review', 'validation_failed')}"
    )
    assert _rejection_status("record_review", "awaiting_mismatch") == 422, (
        "§2c #5: the review face's awaiting refusal also answers 422;"
        f" got {_rejection_status('record_review', 'awaiting_mismatch')}"
    )


# -- worker execution (§1h.3) -------------------------------------------------


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001/IF-GATEUI-001 thread first
def test_worker_review_comment_writes_root_thread_then_review_event(tmp_path: Path):
    repo, story = _review_repo(tmp_path)
    head_before = split_frontmatter(story.read_text(encoding="utf-8"))[0]

    result, failure = execute_gate_command(
        repo,
        _RUN,
        "record_review",
        {"run_id": _RUN, "action": "comment", "comments": _COMMENT},
        _ACTOR,
    )
    assert failure is None, (
        "a legal return must complete the gate command instead of failing:"
        f" {failure}"
    )
    assert result is not None, "the gate command returns a completed outcome (§1j.3)"

    after = story.read_text(encoding="utf-8")
    threads = parse_threads(after)
    assert len(threads) == 1, (
        "action=comment writes exactly one document root thread (§1h.3);"
        f" got {len(threads)}"
    )
    thread = threads[0]
    assert thread.status == "open", "the review thread opens in the open state (§1h.3)"
    assert thread.initiator == _ACTOR, (
        "the thread speaker is the review command's actor — the same value as"
        " human.review.payload.actor (§1h.3)"
    )
    assert _COMMENT in after, "the review comments are the thread body (§1h.3)"

    head_after, body_after = split_frontmatter(after)
    assert head_after == head_before, (
        "the document frontmatter stays byte-identical — the thread lands in the body"
    )
    assert body_after.lstrip().startswith("> **"), (
        "the root thread is anchored at the document's first non-empty line,"
        " ahead of the existing body (§1h.3)"
    )

    store = Store(paths.tracks_home(repo))
    try:
        reviews = [event for event in store.events(_RUN) if event.type == "human.review"]
        assert len(reviews) == 1, "exactly one human.review event lands (§1h.3)"
        payload = reviews[0].payload
        assert payload.get("actor") == _ACTOR, (
            "human.review.payload.actor is the command actor"
        )
        assert payload.get("comments") == _COMMENT, (
            "human.review keeps the return comments (§1j.1)"
        )
        assert payload.get("doc") == "story", (
            "human.review.payload.doc is the label story|spec|acceptance (§1j.1)"
        )
        assert payload.get("thread_id") == thread.thread_id, (
            "human.review.payload.thread_id names the root thread just written (§1j.1)"
        )
        assert re.fullmatch(r"T-\d+", str(payload.get("thread_id"))), (
            "thread_id follows the T-NNN discussion identity (§1j.1)"
        )
        assert store.state(_RUN).awaiting is None, (
            "the recorded review advances the gate (§1h.3)"
        )
    finally:
        store.close()


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001 no_comment skips writing
def test_worker_review_no_comment_skips_thread_and_event_keys(tmp_path: Path):
    repo, story = _review_repo(tmp_path)
    before = story.read_text(encoding="utf-8")

    _result, failure = execute_gate_command(
        repo,
        _RUN,
        "record_review",
        {"run_id": _RUN, "action": "no_comment"},
        _ACTOR,
    )
    assert failure is None, f"a legal no-comment approval must complete: {failure}"
    assert story.read_text(encoding="utf-8") == before, (
        "action=no_comment skips the document write entirely (§1h.3)"
    )
    assert parse_threads(story.read_text(encoding="utf-8")) == [], (
        "the approval path opens no discussion thread"
    )

    store = Store(paths.tracks_home(repo))
    try:
        reviews = [event for event in store.events(_RUN) if event.type == "human.review"]
        assert len(reviews) == 1, "exactly one human.review event lands (§1h.3)"
        payload = reviews[0].payload
        assert payload.get("actor") == _ACTOR
        for key in ("comments", "doc", "thread_id"):
            assert key not in payload, (
                "action=no_comment carries none of comments/doc/thread_id (§1j.1);"
                f" found {key!r}"
            )
        assert store.state(_RUN).awaiting is None, (
            "the recorded approval advances the gate (§1h.3)"
        )
    finally:
        store.close()


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001 thread failure is closed
def test_worker_review_thread_failure_fails_without_review_event(tmp_path: Path):
    repo, story = _review_repo(tmp_path)
    before = story.read_text(encoding="utf-8")
    vdir = story.parent
    os.chmod(vdir, 0o555)
    try:
        _result, failure = execute_gate_command(
            repo,
            _RUN,
            "record_review",
            {"run_id": _RUN, "action": "comment", "comments": _COMMENT},
            _ACTOR,
        )
    finally:
        os.chmod(vdir, 0o755)

    assert failure is not None, (
        "a thread write failure must fail the command instead of reporting success"
        " (§1h.3); the readable path still owes review_thread_write_failed"
    )
    assert failure.get("reason") == "review_thread_write_failed", (
        "the closed failure reason is review_thread_write_failed (§1h.3);"
        f" got {failure}"
    )
    assert story.read_text(encoding="utf-8") == before, (
        "a failed thread write leaves the stage document unchanged"
    )

    store = Store(paths.tracks_home(repo))
    try:
        assert [
            event for event in store.events(_RUN) if event.type == "human.review"
        ] == [], "a failed thread write lands no human.review event (§1h.3)"
        assert store.state(_RUN).awaiting == "review", (
            "a failed thread write does not advance the gate (§1h.3)"
        )
    finally:
        store.close()


# -- review decision face (§1h.1) ---------------------------------------------


def _review_face_source() -> str:
    source = (_APP / "timeline.js").read_text(encoding="utf-8")
    assert "function reviewDecisionFace" in source, (
        "the review gate panel renders through reviewDecisionFace (§1h.1)"
    )
    start = source.index("function reviewDecisionFace")
    end = source.index("export function primaryActionFace", start)
    return source[start:end]


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001 approve/return enablement
def test_review_decision_face_approve_empty_return_requires_comments():
    face = _review_face_source()
    assert re.search(r"approve\.disabled\s*=\s*!empty\b", face), (
        "通过 is available with empty comments — approve is enabled while the"
        " comment box is empty (§1h.1)"
    )
    assert re.search(r"ret\.disabled\s*=\s*empty\b", face), (
        "退回 requires non-empty comments — return is disabled while the"
        " comment box is empty (§1h.1)"
    )


# AC-FR0359-01@v0.12 TRACKS-TRACE IF-REVIEWDECISION-001 dual-channel hint copy
def test_review_decision_face_hint_names_thread_and_scribe():
    face = _review_face_source()
    assert re.search(r"线程[^\n]{0,60}Scribe|Scribe[^\n]{0,60}线程", face), (
        "the review hint states the dual channel: the opinion first becomes a"
        " document thread and is still carried to Scribe (§1h.1)"
    )
