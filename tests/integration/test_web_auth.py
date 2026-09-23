"""Web auth and command guard (IF-WEBAUTH-001, IF-CMDGUARD-001)."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from tracks.server.auth import (
    check_csrf,
    issue_session,
    provision_password,
    resolve_session,
    verify_password,
)
from tracks.server.guard import check_actor_class, validate_command_payload
from tracks.supervisor.db import ServiceDB

pytestmark = pytest.mark.integration

# A thread whose root comment asks a specific party to adjudicate
# (interfaces §1f.4): only that party may resolve it.
_HUMAN_REQUEST_DOC = "# Spec\n\n> **Agent:** 本轮实现完成，请 @Human 裁决是否接受。\n"


def _store_connection(home: Path) -> sqlite3.Connection:
    """A writer connection to the documented service store (interfaces §1c)."""
    conn = sqlite3.connect(str(Path(home) / "service.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _thread_status(doc: Path, thread_id: str) -> str:
    from tracks.discuss.parser import parse_threads

    threads = parse_threads(doc.read_text(encoding="utf-8"))
    return next(t for t in threads if t.thread_id == thread_id).status


# AC-FR0314-01@v0.9 TRACKS-TRACE unauthenticated rejected then ok
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
def test_unauthenticated_rejected_then_ok(tmp_path):
    """AC-FR0314-01: unauthenticated rejected, login then access granted."""
    home = tmp_path / "home"
    ServiceDB(home)
    with contextlib.closing(_store_connection(home)) as conn:
        # unauthenticated: no session resolves and no credential verifies
        assert resolve_session(conn, None) is None
        assert resolve_session(conn, "no-such-token") is None
        assert verify_password(conn, "secret-1") is False

        # login: the provisioned credential verifies and its session resolves
        provision_password(conn, "secret-1")
        assert verify_password(conn, "secret-1") is True
        assert verify_password(conn, "wrong") is False
        actor = str(conn.execute("SELECT actor FROM auth LIMIT 1").fetchone()[0])
        session = issue_session(conn, actor)
        resolved = resolve_session(conn, session.token)
        assert resolved is not None
        assert resolved.actor == actor
        assert resolved.actor_class == "human"
        assert check_csrf(resolved, session.csrf_token) is True
        assert check_csrf(resolved, "wrong-token") is False


# AC-FR0314-02@v0.9 TRACKS-TRACE non human decision rejected audited
def test_non_human_decision_rejected_audited(tmp_path):
    """AC-FR0314-02: agent actor on human-only kind rejected and audited."""
    from tracks.server.guard import GuardRejection
    from tracks.supervisor.service import CommandService, Rejection

    # the guard tier refuses the non-human actor on a human-only kind
    try:
        check_actor_class("record_stage_approval", "agent")
    except GuardRejection as exc:
        assert exc.reason == "forbidden_actor"
    else:
        raise AssertionError("agent approval must be rejected")

    # the command service audits the attempt and advances nothing (§1f.5)
    home = tmp_path / "home"
    home.mkdir()
    db = ServiceDB(home)
    svc = CommandService(home, db, object())
    try:
        svc.accept(
            kind="record_stage_approval",
            params={
                "run_id": "run-1",
                "object": "spec",
                "expected_revision": "rev-1",
                "decision": "approve",
            },
            actor="agent-user",
            actor_class="agent",
            surface="http",
            idempotency_key="agent-approval-1",
        )
    except Rejection as exc:
        assert exc.reason == "forbidden_actor"
    else:
        raise AssertionError("an agent actor must not submit a human decision")

    events = db.read_events()
    assert any(
        event["type"] == "command.rejected"
        and (event["payload"] or {}).get("reason") == "forbidden_actor"
        and (event["payload"] or {}).get("kind") == "record_stage_approval"
        for event in events
    ), "the rejected attempt must be audited as command.rejected"
    assert any(
        event["type"] == "access.denied"
        and (event["payload"] or {}).get("reason") == "forbidden_actor"
        for event in events
    ), "the rejected attempt must be audited as access.denied"
    assert db.find_by_idempotency("agent-approval-1") is None, (
        "a rejected decision persists no command (no state advance)"
    )


# AC-FR0314-03@v0.9 TRACKS-TRACE shell payload blocked
def test_shell_payload_blocked():
    """AC-FR0314-03: shell payloads blocked with no execution effect."""
    from tracks.server.guard import GuardRejection

    try:
        validate_command_payload("run_shell", {"cmd": "rm -rf /"})
    except GuardRejection as exc:
        assert exc.reason in ("guard_blocked", "validation_failed")
    else:
        raise AssertionError("shell payload must be blocked")


# AC-FR0314-04@v0.9 TRACKS-TRACE adjudication ownership enforced
def test_adjudication_ownership_enforced(tmp_path):
    """AC-FR0314-04: only requested party resolves; others denied and audited."""
    from tracks.discuss.parser import parse_threads
    from tracks.supervisor.service import CommandService, Rejection

    home = tmp_path / "home"
    db = ServiceDB(home)
    doc = tmp_path / "spec.md"
    doc.write_text(_HUMAN_REQUEST_DOC, encoding="utf-8")
    thread_id = parse_threads(_HUMAN_REQUEST_DOC)[0].thread_id
    svc = CommandService(home, db, object())
    with contextlib.closing(_store_connection(home)) as conn:
        # an authenticated operator the thread did not request cannot resolve
        reviewer = issue_session(conn, "Sage")
        try:
            svc.resolve_discussion_thread(
                doc, thread_id, actor=reviewer.actor, actor_class=reviewer.actor_class
            )
        except Rejection as exc:
            assert exc.reason == "forbidden_actor"
        else:
            raise AssertionError("an unrequested party must not resolve the thread")
        assert _thread_status(doc, thread_id) == "open", "the denied attempt changes nothing"
        denied = [event for event in db.read_events() if event["type"] == "access.denied"]
        assert any(
            (event["payload"] or {}).get("reason") == "forbidden_actor" for event in denied
        ), "the denied attempt must be audited"

        # the requested party resolves through its authenticated session
        requested = issue_session(conn, "Human")
        svc.resolve_discussion_thread(
            doc, thread_id, actor=requested.actor, actor_class=requested.actor_class
        )
        assert _thread_status(doc, thread_id) == "resolved"
        assert check_csrf(requested, requested.csrf_token) is True
        assert check_csrf(requested, "wrong-token") is False
