"""M-ACC reducer/decide branches (FR-0160, SM-04) — pure (NFR-02/NFR-0040).

Mirrors the M-SPEC review loop on acceptance.md: Sage drafts, Lex reviews,
Human gates, EXIT seals. Covers SM-04.1/.2/.5-.12/.14/.15 plus the trace-fail
escalation path (SM-04.3/.4 mechanics; the gate wiring itself is integration).
"""

from tests.unit.helpers import seq
from tracks.kernel import decide, project

ENTER_ACC = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-ACC"}),
]

DISPATCHED = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "sage", "substate": "DRAFT"},
            "command_id": "C1",
        }
    },
)
PRODUCED = ("outcome.received", {"role": "sage", "status": "done"})
VALIDATED = ("verdict.passed", {"check": "template", "detail": "d"})
COMMITTED = ("acceptance.committed", {"commit_sha": "c1", "acceptance_sha": "s1", "final": False})


def state_of(*items):
    return project(seq(*ENTER_ACC, *items))


def test_enter_draft():
    # SM-04.1: entering M-ACC starts at DRAFT; Sage is dispatched on acceptance.md
    s = state_of()
    assert s.stage == "M-ACC" and s.substate == "DRAFT"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "sage" and cmd.params["doc"] == "acceptance.md"


def test_draft_pipeline_validate_then_commit():
    # SM-04.2 pipeline: dispatch -> validate -> commit acceptance.md
    produced = state_of(DISPATCHED, PRODUCED)
    cmd = decide(produced)
    assert cmd.kind == "validate_document" and cmd.params["doc"] == "acceptance.md"
    assert cmd.params["checks"] == ["template"]  # trace runs inside, not via checks
    validated = state_of(DISPATCHED, PRODUCED, VALIDATED)
    cmd = decide(validated)
    assert cmd.kind == "commit_document" and cmd.params["doc"] == "acceptance.md"


def test_committed_enters_lex_review():
    # SM-04.2: non-final acceptance.committed -> LEX_REVIEW, Lex dispatched
    s = state_of(DISPATCHED, PRODUCED, VALIDATED, COMMITTED)
    assert s.acceptance_committed and s.substate == "LEX_REVIEW"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent" and cmd.params["role"] == "lex"


def test_lex_loop():
    # SM-04.5-.7: pass -> HUMAN_REVIEW (awaiting); non-pass -> RESPOND,
    # acceptance must be re-committed.
    passed = state_of(COMMITTED, ("lex.verdict", {"verdict": "pass"}))
    assert passed.substate == "HUMAN_REVIEW" and passed.awaiting == "review"
    assert decide(passed) is None
    respond = state_of(COMMITTED, ("lex.verdict", {"verdict": "comment"}))
    assert respond.substate == "RESPOND" and not respond.acceptance_committed


def test_respond_loop():
    # SM-04.11-.12: RESPOND re-runs dispatch -> validate -> commit
    s = state_of(COMMITTED, ("lex.verdict", {"verdict": "comment"}))
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent" and cmd.params["substate"] == "RESPOND"
    assert cmd.params["doc"] == "acceptance.md"


def test_human_review():
    # SM-04.8-.10: no_comment -> EXIT; comment -> RESPOND (uncommit acceptance)
    exited = state_of(
        COMMITTED, ("lex.verdict", {"verdict": "pass"}), ("human.review", {"action": "no_comment"})
    )
    assert exited.substate == "EXIT" and exited.awaiting is None
    respond = state_of(
        COMMITTED,
        ("lex.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "comment", "diff_ref": "d1"}),
    )
    assert respond.substate == "RESPOND" and not respond.acceptance_committed
    assert respond.review_diff_ref == "d1"


def test_exit_seals_acceptance():
    # SM-04.13 lead-in: EXIT gate-validates then seals acceptance.md
    s = state_of(
        COMMITTED, ("lex.verdict", {"verdict": "pass"}), ("human.review", {"action": "no_comment"})
    )
    cmd = decide(s)
    assert cmd.kind == "validate_document"
    assert cmd.params == {"doc": "acceptance.md", "checks": ["template", "discussion_ready"]}
    sealed = state_of(
        COMMITTED,
        ("lex.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("verdict.passed", {"check": "template", "detail": "d"}),
    )
    cmd = decide(sealed)
    assert cmd.kind == "write_frontmatter" and cmd.params["doc"] == "acceptance.md"


def test_exit_format_fail():
    # SM-04.14: exit-gate verdict.failed blocks the exit and awaits Human
    s = state_of(
        COMMITTED,
        ("lex.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("verdict.failed", {"check": "trace", "reason": "orphan", "attempt": 1}),
    )
    assert s.status == "awaiting_human" and s.awaiting == "review"
    assert decide(s) is None


def test_trace_fail_redispatch_then_escalate():
    # SM-04.3/.4: trace verdict.failed in DRAFT re-dispatches Sage with
    # evidence; the 3rd failure escalates to Human (rollback is then a Human
    # decision via stage.rolled_back).
    failed = state_of(("verdict.failed", {"check": "trace", "reason": "orphan", "attempt": 1}))
    cmd = decide(failed)
    assert cmd.kind == "dispatch_agent" and cmd.params["evidence"]["check"] == "trace"
    escalated = state_of(("verdict.failed", {"check": "trace", "reason": "orphan", "attempt": 3}))
    assert escalated.status == "awaiting_human" and escalated.awaiting == "escalation"


def test_rollback_targets():
    # SM-04.15: rollback resets all committed flags and re-enters DRAFT
    for target in ("M-SPEC", "M-STORY"):
        s = state_of(
            COMMITTED,
            (
                "stage.rolled_back",
                {"from_stage": "M-ACC", "to_stage": target, "reason": "trace gap"},
            ),
        )
        assert s.stage == target and s.substate == "DRAFT"
        assert not s.acceptance_committed and not s.spec_committed
