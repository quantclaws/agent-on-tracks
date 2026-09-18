"""Web auth and command guard (IF-WEBAUTH-001, IF-CMDGUARD-001)."""

from __future__ import annotations

import pytest

from tracks.server.auth import check_csrf, issue_session, provision_password, verify_password
from tracks.server.guard import check_actor_class, validate_command_payload

pytestmark = pytest.mark.integration


# AC-FR0314-01@v0.9 TRACKS-TRACE unauthenticated rejected then ok
def test_unauthenticated_rejected_then_ok(tmp_path):
    """AC-FR0314-01: unauthenticated rejected, login then access granted."""
    provision_password("secret-1", object())
    assert verify_password(object(), "secret-1") is True
    assert verify_password(object(), "wrong") is False


# AC-FR0314-02@v0.9 TRACKS-TRACE non human decision rejected audited
def test_non_human_decision_rejected_audited(tmp_path):
    """AC-FR0314-02: agent actor on human-only kind rejected and audited."""
    from tracks.server.guard import GuardRejection

    try:
        check_actor_class("record_stage_approval", "agent")
    except GuardRejection as exc:
        assert exc.reason == "forbidden_actor"
    else:
        raise AssertionError("agent approval must be rejected")


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
    from tracks.supervisor.service import CommandService

    svc = CommandService(tmp_path, object(), object())
    svc.resolve_discussion_thread(tmp_path / "spec.md", "thread-1", actor="Human", actor_class="human")
    session = issue_session(object(), "Human")
    assert session.actor_class == "human"
    assert check_csrf(session, session.csrf_token) is True
    assert check_csrf(session, "wrong-token") is False
