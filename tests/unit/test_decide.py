"""decide() next-command selection — pure (AC-N02a)."""
from tests.unit.helpers import seq
from tracks.kernel import decide, project

START = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-START"}),
    ("stage.exited", {"stage": "M-START"}),
    ("stage.entered", {"stage": "M-STORY"}),
]


def state_of(*items):
    return project(seq(*START, *items))


def test_triage_dispatches_scribe_once():
    cmd = decide(state_of())
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "scribe" and cmd.params["substate"] == "TRIAGE"
    assert cmd.command_id is None  # store assigns the ULID; decide stays pure

    dispatched = state_of(
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "TRIAGE"},
                                        "command_id": "C1"}}),
    )
    assert decide(dispatched) is None


def test_awaiting_states_halt():
    awaiting = state_of(("outcome.received", {"role": "scribe", "status": "done"}))
    assert awaiting.awaiting == "triage" and decide(awaiting) is None

    escalated = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "r", "attempt": 3}),
    )
    assert escalated.status == "awaiting_human" and decide(escalated) is None


def test_draft_pipeline_order():
    draft = state_of(("human.triage", {"decision": "go"}))
    assert decide(draft).kind == "dispatch_agent"

    produced = state_of(
        ("human.triage", {"decision": "go"}),
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "DRAFT"},
                                        "command_id": "C1"}}),
        ("outcome.received", {"role": "scribe", "status": "done"}),
    )
    assert decide(produced).kind == "validate_document"

    validated = state_of(
        ("human.triage", {"decision": "go"}),
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "DRAFT"},
                                        "command_id": "C1"}}),
        ("outcome.received", {"role": "scribe", "status": "done"}),
        ("verdict.passed", {"check": "schema", "detail": "d"}),
    )
    assert decide(validated).kind == "commit_document"


def test_redispatch_carries_failure_evidence():
    failed = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
    )
    cmd = decide(failed)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["evidence"]["check"] == "schema"  # FR-11


def test_no_go_teardown_sequence():
    rejected = state_of(
        ("outcome.received", {"role": "scribe", "status": "done"}),
        ("human.triage", {"decision": "no_go"}),
    )
    first = decide(rejected)
    assert first.kind == "record_backlog"
    assert first.params["decision"] == "no_go"

    recorded = state_of(
        ("outcome.received", {"role": "scribe", "status": "done"}),
        ("human.triage", {"decision": "no_go"}),
        ("backlog.recorded",
         {"version": "v0.1", "decision": "no_go", "reason": "triage"}),
    )
    # FR-09: backlog -> delete_branch -> complete_run (branch ops are logged
    # commands now, not folded into complete_run).
    second = decide(recorded)
    assert second.kind == "delete_branch"
    assert second.params["branch_name"] == "releases/v0.1"

    deleted = state_of(
        ("outcome.received", {"role": "scribe", "status": "done"}),
        ("human.triage", {"decision": "no_go"}),
        ("backlog.recorded",
         {"version": "v0.1", "decision": "no_go", "reason": "triage"}),
        ("branch.deleted", {"branch_name": "releases/v0.1"}),
    )
    assert decide(deleted).kind == "complete_run"


def test_scope_overflow_triggers_rollback():
    overflowed = state_of(
        ("stage.entered", {"stage": "M-SPEC"}),
        ("verdict.failed", {"check": "scope_overflow", "reason": "31 FRs"}),
    )
    cmd = decide(overflowed)
    assert cmd.kind == "rollback_stage" and cmd.params["to_stage"] == "M-STORY"


def test_exit_issues_frontmatter_seal_once():
    at_exit = state_of(
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
        ("sage.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
    )
    cmd = decide(at_exit)
    assert cmd.kind == "write_frontmatter" and cmd.params["doc"] == "story.md"

    sealed = state_of(
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
        ("sage.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("story.committed", {"commit_sha": "c2", "story_sha": "s2", "final": True}),
        ("stage.exited", {"stage": "M-STORY"}),
    )
    assert decide(sealed) is None


def test_completed_run_halts():
    done = state_of(("run.completed", {"terminal_state": "completed"}))
    assert decide(done) is None
