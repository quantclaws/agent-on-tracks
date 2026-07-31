"""Pure state machine (NFR-02): `project(events) -> State` fold and
`decide(state) -> Command | None` next-step selection.

No clock, no filesystem, no I/O, no env reads. All non-determinism enters only
as events. `decide` returns a Command with `command_id=None`; the store assigns
the ULID at issue time so this stays pure.

v0.1 drives the happy path M-START → M-STORY → M-SPEC to EXIT. Reject
(no_go/park) and rollback teardown emit their leading command; RESPOND
(review comments) is scaffolded but not exercised by the happy path.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .events import Command, EventEnvelope


@dataclass
class State:
    run_id: str | None = None
    version: str | None = None
    status: str = "active"  # active | completed | awaiting_human
    stage: str | None = None  # M-START | M-STORY | M-SPEC | M-ACC | M-REQ-APPROVAL
    substate: str | None = None
    awaiting: str | None = None  # triage | review | escalation | approval
    current_attempt: int = 0
    review_round: int = 0
    sage_passed_this_round: bool = False
    lex_passed_this_round: bool = False
    triage_decision: str | None = None
    story_committed: bool = False
    spec_committed: bool = False
    acceptance_committed: bool = False
    backlog_recorded: bool = False
    terminal_state: str | None = None
    # M-REQ-APPROVAL (FR-0180/0190/0200, IF-003 §10c)
    preview_ready: bool = False
    approved: bool = False
    returned: bool = False
    return_target: str | None = None  # human.return target stage
    approval_digest: str | None = None  # revision digest (D-01)
    approval_actor: str | None = None
    issues_created: bool = False  # idempotency key semantics = D-06
    # micro-progress inside the current substate
    doc_dispatched: bool = False
    doc_produced: bool = False
    doc_validated: bool = False
    reviewer_dispatched: bool = False
    reviewer_produced: bool = False
    stage_exited: bool = False
    exit_validated: bool = False  # FR-150/AC-1502: review-exit gate passed
    pending: dict | None = None  # last issued command without a result
    last_failure: dict | None = None  # evidence for the next re-dispatch (FR-11)
    scope_overflow: bool = False  # FR-20: pending rollback to M-STORY
    review_diff_ref: str | None = None  # FR-16: human revise commit sha
    branch_created: bool = False  # release branch exists (M-START create_branch)
    branch_deleted: bool = False  # release branch torn down (FR-09 reject)


def _reset_doc(s: State) -> None:
    s.doc_dispatched = s.doc_produced = s.doc_validated = False


def _reset_review(s: State) -> None:
    s.reviewer_dispatched = s.reviewer_produced = False


# FR-0160: stage -> (drafting role, target doc). M-ACC mirrors M-SPEC's review
# loop (Sage drafts, Lex reviews) on acceptance.md.
_STAGE_ROLE_DOC = {
    "M-STORY": ("scribe", "story.md"),
    "M-SPEC": ("sage", "spec.md"),
    "M-ACC": ("sage", "acceptance.md"),
}

_COMMITTED_FLAG = {
    "M-STORY": "story_committed",
    "M-SPEC": "spec_committed",
    "M-ACC": "acceptance_committed",
}


def _uncommit(s: State) -> None:
    """RESPOND must re-commit the current stage's doc to re-enter review."""
    setattr(s, _COMMITTED_FLAG.get(s.stage, "story_committed"), False)


# -- event reducers: one small handler per event type, looked up by `apply`.
# Each is `(state, payload, envelope) -> None` and mutates state in place.

def _on_story_requested(s: State, p: dict, ev: EventEnvelope) -> None:
    s.run_id, s.version = ev.run_id, ev.version


def _on_stage_entered(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage = p["stage"]
    s.substate = {"M-STORY": "TRIAGE", "M-SPEC": "DRAFT", "M-ACC": "DRAFT",
                  "M-REQ-APPROVAL": "PREVIEW"}.get(s.stage)
    _reset_doc(s)
    _reset_review(s)
    s.current_attempt = 0
    s.stage_exited = False
    s.exit_validated = False
    s.review_round = 1
    s.sage_passed_this_round = s.lex_passed_this_round = False
    if s.stage == "M-REQ-APPROVAL":
        # re-entry after RETURNED rollback restarts the approval cycle fresh
        s.preview_ready = s.approved = s.returned = s.issues_created = False
        s.return_target = s.approval_digest = s.approval_actor = None


def _on_stage_exited(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage_exited = True


def _on_stage_rolled_back(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage = p["to_stage"]
    s.substate = "DRAFT"
    _reset_doc(s)
    _reset_review(s)
    s.current_attempt = 0
    s.spec_committed = False
    s.story_committed = False  # the redone story must be re-committed
    s.acceptance_committed = False
    s.scope_overflow = False
    s.returned = False
    s.return_target = None


def _on_command_issued(s: State, p: dict, ev: EventEnvelope) -> None:
    cmd = p.get("command", {})
    s.pending = cmd
    if cmd.get("kind") != "dispatch_agent":
        return
    if s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
        s.reviewer_dispatched = True
    else:
        s.doc_dispatched = True


def _consume_attempt(s: State) -> None:
    """Shared attempt accounting: increment; escalate to awaiting_human at >=3."""
    s.current_attempt += 1
    if s.current_attempt >= 3:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _on_outcome_received(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.substate == "ISSUES":
        # FR-0200 / NFR-0030 style: a failed create_issues outcome consumes an
        # attempt; the 3rd failure escalates. Per-item progress is recorded by
        # issue.created events, so retries resume instead of rebuilding (D-06).
        if p.get("status") == "failed":
            _consume_attempt(s)
        return
    if p.get("status") == "failed":
        # FR-0210 exit gate: a failed dispatch_agent outcome (protocol / audit /
        # existence failure, incl. over-reach) is NOT a produced document. It
        # consumes an attempt from the same accounting as verdict.failed, carries
        # failure evidence into the re-dispatch prompt (FR-11), and escalates to
        # awaiting_human at the 3rd attempt (Aaron: over-reach re-dispatches, <=3).
        s.last_failure = {
            "check": p.get("failure_class", "agent_failed"),
            "reason": p.get("self_report"),
            "evidence": p.get("audit_evidence") or p.get("artifact_ref"),
        }
        if s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
            _reset_review(s)
        else:
            _reset_doc(s)
        _consume_attempt(s)
        return
    if s.substate == "TRIAGE":
        s.awaiting = "triage"
    elif s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
        s.reviewer_produced = True
    else:
        s.doc_produced = True


def _on_verdict_passed(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.substate == "EXIT":
        s.exit_validated = True  # review-exit gate passed (AC-1502)
    else:
        s.doc_validated = True
    s.last_failure = None


def _on_verdict_failed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.last_failure = {k: p.get(k) for k in ("check", "reason", "evidence", "attempt")}
    if s.substate == "EXIT":
        # AC-1502: gate failed at review exit -> block exit, await human.
        s.status = "awaiting_human"
        s.awaiting = "review"
        return
    _reset_doc(s)
    if p.get("check") == "scope_overflow":
        s.scope_overflow = True  # FR-20: rollback, never escalation
        return
    s.current_attempt = int(p.get("attempt", s.current_attempt + 1))
    if s.current_attempt >= 3:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _on_story_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.story_committed = True
    if not p.get("final"):
        s.substate = "SAGE_REVIEW"
        _reset_review(s)


def _on_spec_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.spec_committed = True
    if not p.get("final"):
        s.substate = "LEX_REVIEW"
        _reset_review(s)


def _on_acceptance_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.acceptance_committed = True
    if not p.get("final"):
        s.substate = "LEX_REVIEW"
        _reset_review(s)


def _on_sage_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    if p["verdict"] == "pass":
        s.sage_passed_this_round = True
        s.substate = "HUMAN_REVIEW"
        s.awaiting = "review"
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    s.story_committed = False  # RESPOND must re-commit to re-enter review


def _on_lex_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    if p["verdict"] == "pass":
        s.lex_passed_this_round = True
        s.substate = "HUMAN_REVIEW"
        s.awaiting = "review"
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    _uncommit(s)  # spec or acceptance, by current stage (FR-0160)


def _on_human_triage(s: State, p: dict, ev: EventEnvelope) -> None:
    s.awaiting = None
    s.triage_decision = p["decision"]
    if p["decision"] == "go":
        s.substate = "DRAFT"
        _reset_doc(s)
    else:
        s.substate = None


def _on_human_review(s: State, p: dict, ev: EventEnvelope) -> None:
    s.awaiting = None
    s.substate = "EXIT" if p["action"] == "no_comment" else "RESPOND"
    if s.substate != "RESPOND":
        return
    _reset_doc(s)
    s.review_diff_ref = p.get("diff_ref")
    _uncommit(s)


def _on_review_round_started(s: State, p: dict, ev: EventEnvelope) -> None:
    s.review_round += 1
    _reset_review(s)
    s.sage_passed_this_round = s.lex_passed_this_round = False


def _on_backlog_recorded(s: State, p: dict, ev: EventEnvelope) -> None:
    s.backlog_recorded = True


def _on_preview_generated(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.2 (also the C-02 / stale re-preview path: back to the human gate)
    s.preview_ready = True
    s.approved = False
    s.substate = "AWAIT_HUMAN"
    s.awaiting = "approval"
    s.status = "awaiting_human"


def _on_human_approval(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.3 — the ONLY entry into APPROVED (FR-0180 hard human gate)
    s.approved = True
    s.awaiting = None
    s.status = "active"
    s.substate = "APPROVED"
    s.approval_digest = p["digest"]
    s.approval_actor = p["actor"]


def _on_human_return(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.4/.7
    s.returned = True
    s.awaiting = None
    s.status = "active"
    s.substate = "RETURNED"
    s.return_target = p["to_stage"]


def _on_approval_recorded(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.5 (C-01): recording the approval identity IS the APPROVED->ISSUES
    # transition, materializing ISSUES as an observable substate.
    s.approval_digest = p["digest"]
    s.approval_actor = p["actor"]
    s.substate = "ISSUES"


def _on_issues_created(s: State, p: dict, ev: EventEnvelope) -> None:
    s.issues_created = True


def _on_run_completed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.status = "completed"
    s.awaiting = None
    s.terminal_state = p.get("terminal_state")


def _on_branch_created(s: State, p: dict, ev: EventEnvelope) -> None:
    s.branch_created = True


def _on_branch_deleted(s: State, p: dict, ev: EventEnvelope) -> None:
    s.branch_deleted = True


# run.interrupted has no reducer: it changes no state (v0.1); `apply`'s prelude
# still clears `pending` for it.
_APPLY = {
    "story.requested": _on_story_requested,
    "stage.entered": _on_stage_entered,
    "stage.exited": _on_stage_exited,
    "stage.rolled_back": _on_stage_rolled_back,
    "command.issued": _on_command_issued,
    "outcome.received": _on_outcome_received,
    "verdict.passed": _on_verdict_passed,
    "verdict.failed": _on_verdict_failed,
    "story.committed": _on_story_committed,
    "spec.committed": _on_spec_committed,
    "acceptance.committed": _on_acceptance_committed,
    "sage.verdict": _on_sage_verdict,
    "lex.verdict": _on_lex_verdict,
    "human.triage": _on_human_triage,
    "human.review": _on_human_review,
    "review.round_started": _on_review_round_started,
    "backlog.recorded": _on_backlog_recorded,
    "run.completed": _on_run_completed,
    "branch.created": _on_branch_created,
    "branch.deleted": _on_branch_deleted,
    # M-REQ-APPROVAL (SM-05). issue.created has no reducer: per-item progress
    # is reconciled by the executor from the event log (D-06), not from State.
    "preview.generated": _on_preview_generated,
    "human.approval": _on_human_approval,
    "human.return": _on_human_return,
    "approval.recorded": _on_approval_recorded,
    "issues.created": _on_issues_created,
}


def apply(s: State, ev: EventEnvelope) -> State:
    if ev.type != "command.issued":
        s.pending = None  # any subsequent event resolves the write-ahead record
    handler = _APPLY.get(ev.type)
    if handler is not None:
        handler(s, ev.payload, ev)
    return s


def project(events: Iterable[EventEnvelope]) -> State:
    s = State()
    for ev in events:
        apply(s, ev)
    return s


def _dispatch(role: str, substate: str, objective: str, doc: str | None = None) -> Command:
    params = {"role": role, "substate": substate, "objective": objective}
    if doc:
        params["doc"] = doc
    return Command(kind="dispatch_agent", params=params)


def _decide_reject(s: State) -> Command:
    """NO-GO / PARK teardown (D-06, FR-09): record backlog → delete the release
    branch → complete the run, each as its own logged command."""
    if not s.backlog_recorded:
        return Command(
            kind="record_backlog",
            params={"version": s.version, "decision": s.triage_decision, "reason": "triage"},
        )
    if not s.branch_deleted:
        return Command(kind="delete_branch", params={"branch_name": f"releases/{s.version}"})
    return Command(kind="complete_run", params={"terminal_state": s.triage_decision})


def _decide_draft(s: State, stage: str, sub: str) -> Command | None:
    """DRAFT / RESPOND pipeline: dispatch -> validate -> commit."""
    role, doc = _STAGE_ROLE_DOC[stage]
    committed = getattr(s, _COMMITTED_FLAG[stage])
    if not s.doc_dispatched:
        cmd = _dispatch(role, sub, f"write {doc}", doc)
        if s.last_failure:
            cmd.params["evidence"] = dict(s.last_failure)  # FR-11
        if sub == "RESPOND" and s.review_diff_ref:
            cmd.params["diff_ref"] = s.review_diff_ref  # FR-16
        return cmd
    if not s.doc_produced:
        return None
    if not s.doc_validated:
        # FR-150/AC-1503: outcome-time format gate — Scribe/Sage must produce a
        # template-conforming doc before it is committed / enters review.
        return Command(kind="validate_document", params={"doc": doc, "checks": ["template"]})
    if not committed:
        return Command(
            kind="commit_document", params={"doc": doc, "message": f"{stage}: draft {doc}"}
        )
    return None


def _decide_exit(s: State, stage: str) -> Command | None:
    """EXIT: gate-validate (FR-150/AC-1502) then seal the document sha (FR-17/FR-23)."""
    if s.stage_exited:
        return None
    doc = _STAGE_ROLE_DOC[stage][1]
    if not s.exit_validated:
        # review-exit gate: template conformance + all discussion threads resolved.
        return Command(
            kind="validate_document",
            params={"doc": doc, "checks": ["template", "discussion_ready"]})
    # Executor seals frontmatter sha, commits, emits stage.exited
    # (+ story/spec.committed final sha, + next stage.entered / run.completed).
    return Command(
        kind="write_frontmatter", params={"doc": doc, "stage": stage, "field": "sha"}
    )


def decide(s: State) -> Command | None:
    """Next command to issue, or None to halt (awaiting human / completed)."""
    if s.status != "active" or s.awaiting:
        return None
    stage, sub = s.stage, s.substate
    if stage == "M-STORY" and s.triage_decision in ("no_go", "park"):
        return _decide_reject(s)
    if s.scope_overflow:
        # FR-20: >30 FRs in spec — roll back to M-STORY, re-scope the story.
        return Command(
            kind="rollback_stage", params={"to_stage": "M-STORY", "reason": "scope_overflow"}
        )
    if sub in ("DRAFT", "RESPOND"):
        return _decide_draft(s, stage, sub)
    if sub == "TRIAGE":
        if s.doc_dispatched:
            return None  # awaiting triage (set on outcome.received)
        return _dispatch("scribe", "TRIAGE", "explore raw requirement")
    if sub in ("SAGE_REVIEW", "LEX_REVIEW"):
        reviewer = "sage" if sub == "SAGE_REVIEW" else "lex"
        return None if s.reviewer_dispatched else _dispatch(reviewer, sub, f"{reviewer} review")
    if sub == "EXIT":
        return _decide_exit(s, stage)
    return _decide_approval(s, stage, sub)


def _decide_approval(s: State, stage: str, sub: str) -> Command | None:
    # M-REQ-APPROVAL (SM-05, FR-0180). AWAIT_HUMAN halts at the top of
    # decide() via awaiting="approval": without a human.approval event
    # decide() never produces a downstream command (hard human gate).
    if sub == "PREVIEW":
        return None if s.preview_ready else Command(kind="generate_preview")
    if sub == "APPROVED":
        # SM-05.5: record the approval identity; its approval.recorded event
        # moves the substate to ISSUES (C-01).
        return Command(kind="record_approval",
                       params={"actor": s.approval_actor, "digest": s.approval_digest})
    if sub == "ISSUES":
        if not s.issues_created:
            return Command(kind="create_issues", params={"digest": s.approval_digest})
        if s.stage_exited:
            return None  # crash-hole parity with _decide_exit: never re-exit
        # SM-05.6: boundary exit — no document to seal; the executor emits
        # stage.exited + run.completed(terminal_state="boundary").
        return Command(kind="write_frontmatter", params={"stage": stage})
    if sub == "RETURNED":
        return Command(kind="rollback_stage",
                       params={"to_stage": s.return_target, "reason": "human_return"})
    return None  # HUMAN_REVIEW or unknown: halt
