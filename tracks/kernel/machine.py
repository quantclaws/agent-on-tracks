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
    stage: str | None = None  # M-START | M-STORY | M-SPEC
    substate: str | None = None
    awaiting: str | None = None  # triage | review | escalation
    current_attempt: int = 0
    review_round: int = 0
    sage_passed_this_round: bool = False
    lex_passed_this_round: bool = False
    triage_decision: str | None = None
    story_committed: bool = False
    spec_committed: bool = False
    backlog_recorded: bool = False
    terminal_state: str | None = None
    # micro-progress inside the current substate
    doc_dispatched: bool = False
    doc_produced: bool = False
    doc_validated: bool = False
    reviewer_dispatched: bool = False
    reviewer_produced: bool = False
    stage_exited: bool = False
    pending: dict | None = None  # last issued command without a result
    last_failure: dict | None = None  # evidence for the next re-dispatch (FR-11)
    scope_overflow: bool = False  # FR-20: pending rollback to M-STORY
    review_diff_ref: str | None = None  # FR-16: human revise commit sha


def _reset_doc(s: State) -> None:
    s.doc_dispatched = s.doc_produced = s.doc_validated = False


def _reset_review(s: State) -> None:
    s.reviewer_dispatched = s.reviewer_produced = False


# -- event reducers: one small handler per event type, looked up by `apply`.
# Each is `(state, payload, envelope) -> None` and mutates state in place.

def _on_story_requested(s: State, p: dict, ev: EventEnvelope) -> None:
    s.run_id, s.version = ev.run_id, ev.version


def _on_stage_entered(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage = p["stage"]
    s.substate = {"M-STORY": "TRIAGE", "M-SPEC": "DRAFT"}.get(s.stage)
    _reset_doc(s)
    _reset_review(s)
    s.current_attempt = 0
    s.stage_exited = False
    s.review_round = 1
    s.sage_passed_this_round = s.lex_passed_this_round = False


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
    s.scope_overflow = False


def _on_command_issued(s: State, p: dict, ev: EventEnvelope) -> None:
    cmd = p.get("command", {})
    s.pending = cmd
    if cmd.get("kind") != "dispatch_agent":
        return
    if s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
        s.reviewer_dispatched = True
    else:
        s.doc_dispatched = True


def _on_outcome_received(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.substate == "TRIAGE":
        s.awaiting = "triage"
    elif s.substate in ("SAGE_REVIEW", "LEX_REVIEW"):
        s.reviewer_produced = True
    else:
        s.doc_produced = True


def _on_verdict_passed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.doc_validated = True
    s.last_failure = None


def _on_verdict_failed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.last_failure = {k: p.get(k) for k in ("check", "reason", "evidence", "attempt")}
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
    s.spec_committed = False


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
    if s.stage == "M-STORY":
        s.story_committed = False
    else:
        s.spec_committed = False


def _on_review_round_started(s: State, p: dict, ev: EventEnvelope) -> None:
    s.review_round += 1
    _reset_review(s)
    s.sage_passed_this_round = s.lex_passed_this_round = False


def _on_backlog_recorded(s: State, p: dict, ev: EventEnvelope) -> None:
    s.backlog_recorded = True


def _on_run_completed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.status = "completed"
    s.awaiting = None
    s.terminal_state = p.get("terminal_state")


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
    "sage.verdict": _on_sage_verdict,
    "lex.verdict": _on_lex_verdict,
    "human.triage": _on_human_triage,
    "human.review": _on_human_review,
    "review.round_started": _on_review_round_started,
    "backlog.recorded": _on_backlog_recorded,
    "run.completed": _on_run_completed,
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
    """NO-GO / PARK teardown (D-06): record backlog, then complete the run."""
    if not s.backlog_recorded:
        return Command(
            kind="record_backlog",
            params={"version": s.version, "decision": s.triage_decision, "reason": "triage"},
        )
    return Command(kind="complete_run", params={"terminal_state": s.triage_decision})


def _decide_draft(s: State, stage: str, sub: str) -> Command | None:
    """DRAFT / RESPOND pipeline: dispatch -> validate -> commit."""
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


def _decide_exit(s: State, stage: str) -> Command | None:
    """EXIT: seal the document sha once (FR-17/FR-23)."""
    if s.stage_exited:
        return None
    doc = "story.md" if stage == "M-STORY" else "spec.md"
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
    return None  # HUMAN_REVIEW or unknown: halt
