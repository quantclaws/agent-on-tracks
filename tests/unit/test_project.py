"""project() fold — pure, frozen inputs, no mocks (AC-N02a)."""

from tests.unit.helpers import seq
from tracks.kernel import project

START = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-START"}),
    ("stage.exited", {"stage": "M-START"}),
    ("stage.entered", {"stage": "M-STORY"}),
]


def test_start_lands_in_triage():
    s = project(seq(*START))
    assert (s.stage, s.substate, s.status) == ("M-STORY", "TRIAGE", "active")
    assert s.run_id == "RUN" and s.version == "v0.1"
    assert s.awaiting is None


def test_outcome_in_triage_awaits_human():
    s = project(seq(*START, ("outcome.received", {"role": "scribe", "status": "done"})))
    assert s.awaiting == "triage"


def test_triage_go_enters_draft():
    s = project(
        seq(
            *START,
            ("outcome.received", {"role": "scribe", "status": "done"}),
            ("human.triage", {"decision": "go"}),
        )
    )
    assert s.substate == "DRAFT" and s.awaiting is None


def test_triage_no_go_clears_substate():
    s = project(
        seq(
            *START,
            ("outcome.received", {"role": "scribe", "status": "done"}),
            ("human.triage", {"decision": "no_go"}),
        )
    )
    assert s.triage_decision == "no_go" and s.substate is None


def test_three_failures_escalate():
    events = list(
        seq(
            *START,
            ("human.triage", {"decision": "go"}),
            ("verdict.failed", {"check": "schema", "reason": "r", "attempt": 1}),
            ("verdict.failed", {"check": "schema", "reason": "r", "attempt": 2}),
            ("verdict.failed", {"check": "schema", "reason": "r", "attempt": 3}),
        )
    )
    s = project(events)
    assert s.status == "awaiting_human" and s.awaiting == "escalation"
    assert s.current_attempt == 3
    assert s.last_failure["check"] == "schema"


def test_scope_overflow_is_not_an_attempt():
    s = project(
        seq(
            *START,
            ("stage.entered", {"stage": "M-SPEC"}),
            ("verdict.failed", {"check": "scope_overflow", "reason": "31 FRs"}),
        )
    )
    assert s.scope_overflow is True
    assert s.current_attempt == 0 and s.status == "active"


def test_rollback_returns_to_story_draft():
    s = project(
        seq(
            *START,
            ("stage.entered", {"stage": "M-SPEC"}),
            ("verdict.failed", {"check": "scope_overflow", "reason": "31 FRs"}),
            (
                "stage.rolled_back",
                {"from_stage": "M-SPEC", "to_stage": "M-STORY", "reason": "scope_overflow"},
            ),
        )
    )
    assert (s.stage, s.substate) == ("M-STORY", "DRAFT")
    assert s.scope_overflow is False and s.story_committed is False


def test_draft_commit_moves_to_sage_review_but_final_does_not():
    draft = seq(
        *START,
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
    )
    assert project(draft).substate == "SAGE_REVIEW"

    final = seq(
        *START,
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
        ("sage.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("story.committed", {"commit_sha": "c2", "story_sha": "s2", "final": True}),
    )
    s = project(final)
    assert s.substate == "EXIT"  # R4-02: final commit is not a review trigger


def test_reviewer_comment_enters_respond():
    s = project(
        seq(
            *START,
            ("human.triage", {"decision": "go"}),
            ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
            ("sage.verdict", {"verdict": "comment"}),
        )
    )
    assert s.substate == "RESPOND" and s.story_committed is False


def test_run_completed_is_terminal():
    s = project(seq(*START, ("run.completed", {"terminal_state": "completed"})))
    assert s.status == "completed" and s.terminal_state == "completed"


def test_pending_cleared_by_any_subsequent_event():
    s = project(
        seq(
            *START,
            (
                "command.issued",
                {
                    "command": {
                        "kind": "dispatch_agent",
                        "params": {"role": "scribe", "substate": "TRIAGE"},
                        "command_id": "C1",
                    }
                },
            ),
        )
    )
    assert s.pending and s.pending["command_id"] == "C1"
    s2 = project(
        seq(
            *START,
            (
                "command.issued",
                {
                    "command": {
                        "kind": "dispatch_agent",
                        "params": {"role": "scribe", "substate": "TRIAGE"},
                        "command_id": "C1",
                    }
                },
            ),
            ("outcome.received", {"role": "scribe", "status": "done"}),
        )
    )
    assert s2.pending is None
