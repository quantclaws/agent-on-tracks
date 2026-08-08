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


def test_single_doc_stages_keep_the_single_skill_shape():
    # batch B pin: only the M-DESIGN author assignment is multi-skill; every
    # other stage keeps `skill` (tracks-discuz) + `skill_version`, no list.
    cmd = decide(state_of())  # M-STORY TRIAGE
    assignment = cmd.params["assignment"]
    assert assignment["skill"] == "tracks-discuz"
    assert assignment["skill_version"] == "0.2"
    assert "skills" not in assignment
    drafted = state_of(
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "TRIAGE"},
                                        "command_id": "C1"}}),
        ("outcome.received", {"role": "scribe", "status": "done"}),
        ("human.triage", {"decision": "go"}),
    )
    draft_cmd = decide(drafted)  # M-STORY DRAFT
    assert draft_cmd.params["assignment"]["skill"] == "tracks-discuz"
    assert "skills" not in draft_cmd.params["assignment"]


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


def test_spec_draft_uses_template_validation_only():
    produced = state_of(
        ("stage.entered", {"stage": "M-SPEC"}),
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "sage",
                                                   "substate": "DRAFT"},
                                        "command_id": "C1"}}),
        ("outcome.received", {"role": "sage", "status": "done"}),
    )
    cmd = decide(produced)
    assert cmd.kind == "validate_document"
    assert cmd.params == {"doc": "spec.md", "checks": ["template"]}


def test_redispatch_carries_failure_evidence():
    failed = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
    )
    cmd = decide(failed)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["evidence"]["check"] == "schema"  # FR-11


def test_triage_to_draft_fresh_retry_budget_after_triage_failures():
    """TRIAGE failures (json_truncated / outcome failed) must not bleed their
    attempt budget or evidence into the subsequent DRAFT. After human go, DRAFT
    starts at attempt 0 with no stale last_failure, so decide() emits
    attempt=1 and carries no TRIAGE evidence."""
    failed_triage_then_go = state_of(
        ("outcome.received", {"role": "scribe", "status": "failed",
                              "failure_class": "json_truncated",
                              "self_report": "stdout JSON unparseable"}),
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "TRIAGE"},
                                        "command_id": "C2"}}),
        ("outcome.received", {"role": "scribe", "status": "done"}),
        ("human.triage", {"decision": "go"}),
    )
    assert failed_triage_then_go.substate == "DRAFT"
    assert failed_triage_then_go.current_attempt == 0
    assert failed_triage_then_go.last_failure is None
    cmd = decide(failed_triage_then_go)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["substate"] == "DRAFT"
    assert cmd.params["attempt"] == 1
    assert "evidence" not in cmd.params


def test_human_retry_gives_fresh_draft_budget_after_escalation():
    """After DRAFT exhausts its budget and Human issues `trac retry`, the
    resumed DRAFT must start from a fresh attempt=0 budget. The last_failure
    evidence is preserved so the re-dispatch prompt still carries it (FR-11)."""
    escalated = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 2}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 3}),
    )
    assert escalated.status == "awaiting_human"
    assert escalated.current_attempt == 3
    retried = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 2}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 3}),
        ("human.retry", {}),
    )
    assert retried.status == "active"
    assert retried.current_attempt == 0
    cmd = decide(retried)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["substate"] == "DRAFT"
    assert cmd.params["attempt"] == 1
    assert cmd.params["evidence"]["check"] == "schema"


def test_human_retry_clear_evidence_drops_failure_after_escalation():
    """`human.retry` with clear_evidence=true at escalation drops last_failure
    and resets the dispatch flag, so decide() re-dispatches DRAFT at attempt=1
    with NO evidence. The operator signaled the underlying validator/config was
    fixed, so the old failure evidence is stale."""
    retried = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 2}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 3}),
        ("human.retry", {"clear_evidence": True}),
    )
    assert retried.status == "active"
    assert retried.current_attempt == 0
    assert retried.last_failure is None
    assert retried.doc_dispatched is False
    cmd = decide(retried)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["substate"] == "DRAFT"
    assert cmd.params["attempt"] == 1
    assert "evidence" not in cmd.params


def test_human_retry_clear_evidence_recovers_active_killed_dispatch():
    """Recovery scenario: an ordinary retry cleared escalation, then a DRAFT
    dispatch was issued (attempt=1, carrying stale evidence) and killed by
    Maestro before producing a result. The run is active with doc_dispatched
    stuck True and stale last_failure. `human.retry` with clear_evidence=true
    resets the dispatch state so decide() issues a fresh attempt=1 dispatch
    with no evidence; the abandoned command.issued stays in the log untouched
    (append-only) but is no longer pending."""
    killed = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 2}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 3}),
        ("human.retry", {}),
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "DRAFT",
                                                   "attempt": 1,
                                                   "evidence": {"check": "schema"}},
                                        "command_id": "C1"}}),
    )
    assert killed.status == "active"
    assert killed.doc_dispatched is True
    assert killed.pending is not None
    assert killed.last_failure["check"] == "schema"
    cleared = state_of(
        ("human.triage", {"decision": "go"}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 2}),
        ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 3}),
        ("human.retry", {}),
        ("command.issued", {"command": {"kind": "dispatch_agent",
                                        "params": {"role": "scribe",
                                                   "substate": "DRAFT",
                                                   "attempt": 1,
                                                   "evidence": {"check": "schema"}},
                                        "command_id": "C1"}}),
        ("human.retry", {"clear_evidence": True}),
    )
    assert cleared.status == "active"
    assert cleared.current_attempt == 0
    assert cleared.last_failure is None
    assert cleared.doc_dispatched is False
    assert cleared.pending is None
    cmd = decide(cleared)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["substate"] == "DRAFT"
    assert cmd.params["attempt"] == 1
    assert "evidence" not in cmd.params


def test_human_retry_ordinary_preserves_evidence_after_escalation():
    """Ordinary `human.retry` (no clear_evidence) preserves last_failure so the
    re-dispatch prompt carries the failure evidence (FR-11). Backward compat:
    a payload without the clear_evidence key behaves like clear_evidence=false."""
    for payload in ({}, {"clear_evidence": False}):
        retried = state_of(
            ("human.triage", {"decision": "go"}),
            ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 1}),
            ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 2}),
            ("verdict.failed", {"check": "schema", "reason": "bad", "attempt": 3}),
            ("human.retry", payload),
        )
        assert retried.last_failure["check"] == "schema"
        cmd = decide(retried)
        assert cmd.params["evidence"]["check"] == "schema"


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
    # AC-FR0150-02: EXIT first issues the review-exit gate (template + discussion_ready).
    cmd = decide(at_exit)
    assert cmd.kind == "validate_document" and cmd.params["doc"] == "story.md"
    assert cmd.params["checks"] == ["template", "discussion_ready"]

    # Once the gate passes (exit_validated), EXIT seals the frontmatter sha.
    gated = state_of(
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
        ("sage.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("verdict.passed", {"check": "template,discussion_ready"}),
    )
    cmd = decide(gated)
    assert cmd.kind == "write_frontmatter" and cmd.params["doc"] == "story.md"

    sealed = state_of(
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
        ("sage.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("verdict.passed", {"check": "template,discussion_ready"}),
        ("story.committed", {"commit_sha": "c2", "story_sha": "s2", "final": True}),
        ("stage.exited", {"stage": "M-STORY"}),
    )
    assert decide(sealed) is None


def test_exit_gate_failure_blocks_exit():
    blocked = state_of(
        ("human.triage", {"decision": "go"}),
        ("story.committed", {"commit_sha": "c", "story_sha": "s"}),
        ("sage.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        ("verdict.failed", {"check": "discussion_ready", "reason": "unresolved thread"}),
    )
    # AC-FR0150-02: a failed review-exit gate blocks exit and awaits human.
    assert blocked.status == "awaiting_human" and blocked.awaiting == "review"
    assert decide(blocked) is None


def _doc_exit_state(stage, committed_event, *extra):
    return state_of(
        ("stage.entered", {"stage": stage}),
        (committed_event, {"commit_sha": "c", "spec_sha": "s",
                           "acceptance_sha": "a"}),
        ("lex.verdict", {"verdict": "pass"}),
        ("human.review", {"action": "no_comment"}),
        *extra,
    )


def test_spec_and_acceptance_exit_use_only_discussion_gate():
    for stage, event, doc in (
        ("M-SPEC", "spec.committed", "spec.md"),
        ("M-ACC", "acceptance.committed", "acceptance.md"),
    ):
        first = decide(_doc_exit_state(stage, event))
        assert first.kind == "validate_document"
        assert first.params == {"doc": doc,
                                "checks": ["template", "discussion_ready"]}

        gated = decide(_doc_exit_state(
            stage, event,
            ("verdict.passed", {"check": "template,discussion_ready"}),
        ))
        assert gated.kind == "write_frontmatter"
        assert gated.params["doc"] == doc


def test_completed_run_halts():
    done = state_of(("run.completed", {"terminal_state": "completed"}))
    assert decide(done) is None
