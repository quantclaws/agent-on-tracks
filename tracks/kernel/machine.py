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

from dataclasses import dataclass
from typing import Iterable, Optional

from .events import Command, EventEnvelope


@dataclass
class State:
    run_id: Optional[str] = None
    version: Optional[str] = None
    status: str = "active"  # active | completed | awaiting_human
    stage: Optional[str] = None  # M-START | M-STORY | M-SPEC
    substate: Optional[str] = None
    awaiting: Optional[str] = None  # triage | review | escalation
    current_attempt: int = 0
    review_round: int = 0
    sage_passed_this_round: bool = False
    lex_passed_this_round: bool = False
    triage_decision: Optional[str] = None
    story_committed: bool = False
    spec_committed: bool = False
    backlog_recorded: bool = False
    terminal_state: Optional[str] = None
    # micro-progress inside the current substate
    doc_dispatched: bool = False
    doc_produced: bool = False
    doc_validated: bool = False
    reviewer_dispatched: bool = False
    reviewer_produced: bool = False
    stage_exited: bool = False
    pending: Optional[dict] = None  # last issued command without a result
    last_failure: Optional[dict] = None  # evidence for the next re-dispatch (FR-11)
    scope_overflow: bool = False  # FR-20: pending rollback to M-STORY
    review_diff_ref: Optional[str] = None  # FR-16: human revise commit sha


def _reset_doc(s: State) -> None:
    s.doc_dispatched = s.doc_produced = s.doc_validated = False


def _reset_review(s: State) -> None:
    s.reviewer_dispatched = s.reviewer_produced = False


def apply(s: State, ev: EventEnvelope) -> State:
    t, p = ev.type, ev.payload
    if t != "command.issued":
        s.pending = None  # any subsequent event resolves the write-ahead record
    if t == "story.requested":
        s.run_id, s.version = ev.run_id, ev.version
    elif t == "stage.entered":
        s.stage = p["stage"]
        s.substate = {"M-STORY": "TRIAGE", "M-SPEC": "DRAFT"}.get(s.stage)
        _reset_doc(s)
        _reset_review(s)
        s.current_attempt = 0
        s.stage_exited = False
        s.review_round = 1
        s.sage_passed_this_round = s.lex_passed_this_round = False
    elif t == "stage.exited":
        s.stage_exited = True
    elif t == "stage.rolled_back":
        s.stage = p["to_stage"]
        s.substate = "DRAFT"
        _reset_doc(s)
        _reset_review(s)
        s.current_attempt = 0
        s.spec_committed = False
        s.story_committed = False  # the redone story must be re-committed
        s.scope_overflow = False
    elif t == "command.issued":
        cmd = p.get("command", {})
        s.pending = cmd
        if cmd.get("kind") == "dispatch_agent":
            if s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
                s.reviewer_dispatched = True
            else:
                s.doc_dispatched = True
    elif t == "outcome.received":
        s.pending = None
        if s.substate == "TRIAGE":
            s.awaiting = "triage"
        elif s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
            s.reviewer_produced = True
        else:
            s.doc_produced = True
    elif t == "verdict.passed":
        s.pending = None
        s.doc_validated = True
        s.last_failure = None
    elif t == "verdict.failed":
        s.pending = None
        s.last_failure = {k: p.get(k) for k in ("check", "reason", "evidence", "attempt")}
        _reset_doc(s)
        if p.get("check") == "scope_overflow":
            # FR-20: not a retry — triggers rollback, never escalation.
            s.scope_overflow = True
        else:
            s.current_attempt = int(p.get("attempt", s.current_attempt + 1))
            if s.current_attempt >= 3:
                s.status = "awaiting_human"
                s.awaiting = "escalation"
    elif t == "story.committed":
        s.pending = None
        s.story_committed = True
        if not p.get("final"):
            s.substate = "SAGE_REVIEW"
            _reset_review(s)
    elif t == "spec.committed":
        s.pending = None
        s.spec_committed = True
        if not p.get("final"):
            s.substate = "LEX_REVIEW"
            _reset_review(s)
    elif t == "sage.verdict":
        s.pending = None
        if p["verdict"] == "pass":
            s.sage_passed_this_round = True
            s.substate = "HUMAN_REVIEW"
            s.awaiting = "review"
        else:
            s.substate = "RESPOND"
            _reset_doc(s)
            s.story_committed = False  # RESPOND must re-commit to re-enter review
    elif t == "lex.verdict":
        s.pending = None
        if p["verdict"] == "pass":
            s.lex_passed_this_round = True
            s.substate = "HUMAN_REVIEW"
            s.awaiting = "review"
        else:
            s.substate = "RESPOND"
            _reset_doc(s)
            s.spec_committed = False
    elif t == "human.triage":
        s.awaiting = None
        s.triage_decision = p["decision"]
        if p["decision"] == "go":
            s.substate = "DRAFT"
            _reset_doc(s)
        else:
            s.substate = None
    elif t == "human.review":
        s.awaiting = None
        s.substate = "EXIT" if p["action"] == "no_comment" else "RESPOND"
        if s.substate == "RESPOND":
            _reset_doc(s)
            s.review_diff_ref = p.get("diff_ref")
            if s.stage == "M-STORY":
                s.story_committed = False
            else:
                s.spec_committed = False
    elif t == "review.round_started":
        s.review_round += 1
        _reset_review(s)
        s.sage_passed_this_round = s.lex_passed_this_round = False
    elif t == "backlog.recorded":
        s.backlog_recorded = True
    elif t == "run.completed":
        s.status = "completed"
        s.awaiting = None
        s.terminal_state = p.get("terminal_state")
    elif t == "run.interrupted":
        pass
    return s


def project(events: Iterable[EventEnvelope]) -> State:
    s = State()
    for ev in events:
        apply(s, ev)
    return s


def _dispatch(role: str, substate: str, objective: str, doc: Optional[str] = None) -> Command:
    params = {"role": role, "substate": substate, "objective": objective}
    if doc:
        params["doc"] = doc
    return Command(kind="dispatch_agent", params=params)


def decide(s: State) -> Optional[Command]:
    """Next command to issue, or None to halt (awaiting human / completed)."""
    if s.status != "active" or s.awaiting:
        return None

    stage, sub = s.stage, s.substate

    # NO-GO / PARK teardown (D-06): backlog, then complete_run.
    if stage == "M-STORY" and s.triage_decision in ("no_go", "park"):
        if not s.backlog_recorded:
            return Command(
                kind="record_backlog",
                params={"version": s.version, "decision": s.triage_decision, "reason": "triage"},
            )
        return Command(kind="complete_run", params={"terminal_state": s.triage_decision})

    if s.scope_overflow:
        # FR-20: >30 FRs in spec — roll back to M-STORY, re-scope the story.
        return Command(
            kind="rollback_stage",
            params={"to_stage": "M-STORY", "reason": "scope_overflow"},
        )

    if sub == "TRIAGE":
        if not s.doc_dispatched:
            return _dispatch("scribe", "TRIAGE", "explore raw requirement")
        return None  # awaiting triage (set on outcome.received)

    if sub in ("DRAFT", "RESPOND"):
        role = "scribe" if stage == "M-STORY" else "sage"
        doc = "story.md" if stage == "M-STORY" else "spec.md"
        committed = s.story_committed if stage == "M-STORY" else s.spec_committed
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
            # v0.1: validate is pass-through (D-16); the pipe still runs.
            return Command(kind="validate_document", params={"doc": doc, "checks": []})
        if not committed:
            return Command(
                kind="commit_document", params={"doc": doc, "message": f"{stage}: draft {doc}"}
            )
        return None

    if sub in ("SAGE_REVIEW", "LEX_REVIEW"):
        reviewer = "sage" if sub == "SAGE_REVIEW" else "lex"
        if not s.reviewer_dispatched:
            return _dispatch(reviewer, sub, f"{reviewer} review")
        return None  # verdict processed by reducer → substate advanced

    if sub == "HUMAN_REVIEW":
        return None

    if sub == "EXIT":
        doc = "story.md" if stage == "M-STORY" else "spec.md"
        if not s.stage_exited:
            # Executor writes frontmatter sha, commits, emits stage.exited
            # (+ story/spec.committed sha, + next stage.entered / run.completed).
            return Command(kind="write_frontmatter", params={"doc": doc, "stage": stage, "field": "sha"})
        return None

    return None
