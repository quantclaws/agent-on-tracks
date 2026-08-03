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
    stage: str | None = None  # M-START | M-STORY | M-SPEC | M-ACC | M-REQ-APPROVAL | M-DESIGN
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
    # M-DESIGN (flow.md §8): one Archer assignment covers three docs; progress
    # is counted, not flagged. Safe defaults keep pre-v0.3 replays unchanged.
    design_validated: int = 0  # docs validated in the current validate sequence
    design_committed: int = 0  # design.committed events in the current cycle
    prism_passed_this_round: bool = False
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


# FR-0160 / BS-01: declarative stage registry. Every per-stage fact lives here
# exactly once; decide() and the reducers consult it (directly or through the
# derived indexes below) instead of per-stage dicts/branches. Adding a stage is
# one more StageDef. Genuinely stage-specific *control flow* (triage reject
# teardown, scope_overflow rollback, the M-REQ-APPROVAL approval sequence,
# rollback_stage handling) stays explicit. M-START is not registered: it has
# no agent dispatch/review structure.
@dataclass(frozen=True)
class StageDef:
    stage: str
    initial_substate: str  # substate on stage.entered
    drafting_role: str | None = None  # agent drafting the stage's doc
    doc: str | None = None  # target doc (draft / validate / seal)
    docs: tuple = ()  # multi-doc target set (doc is None): one dispatch covers
    # the whole set (M-DESIGN trio, flow.md §8 / Decision A); single-doc
    # stages keep () and name their one doc in `doc`.
    committed_event: str | None = None  # event recording the doc commit
    committed_flag: str | None = None  # State field tracking that commit
    review_substate: str | None = None  # substate after a non-final commit
    reviewer: str | None = None  # agent dispatched in the review substate
    verdict_event: str | None = None  # the reviewer's verdict event
    reviewer_passed_flag: str | None = None  # State field: reviewer passed


# M-DESIGN deliverables (flow.md §8): architecture, interfaces, test-plan.
DESIGN_DOCS = ("architecture.md", "interfaces.md", "test-plan.md")

_STAGES = {sd.stage: sd for sd in (
    StageDef(stage="M-STORY", initial_substate="TRIAGE",
             drafting_role="scribe", doc="story.md",
             committed_event="story.committed", committed_flag="story_committed",
             review_substate="SAGE_REVIEW", reviewer="sage",
             verdict_event="sage.verdict",
             reviewer_passed_flag="sage_passed_this_round"),
    # M-ACC mirrors M-SPEC's review loop (Sage drafts, Lex reviews),
    # on acceptance.md.
    StageDef(stage="M-SPEC", initial_substate="DRAFT",
             drafting_role="sage", doc="spec.md",
             committed_event="spec.committed", committed_flag="spec_committed",
             review_substate="LEX_REVIEW", reviewer="lex",
             verdict_event="lex.verdict",
             reviewer_passed_flag="lex_passed_this_round"),
    StageDef(stage="M-ACC", initial_substate="DRAFT",
             drafting_role="sage", doc="acceptance.md",
             committed_event="acceptance.committed",
             committed_flag="acceptance_committed",
             review_substate="LEX_REVIEW", reviewer="lex",
             verdict_event="lex.verdict",
             reviewer_passed_flag="lex_passed_this_round"),
    StageDef(stage="M-REQ-APPROVAL", initial_substate="PREVIEW"),
    # M-DESIGN (flow.md §8): pure technical stage — no human.review/approval
    # gates (BS-05). Multi-doc: one Archer dispatch drafts all DESIGN_DOCS;
    # the per-doc control flow is explicit (_decide_design_draft/_exit), like
    # _decide_approval. doc is None: no single target doc.
    StageDef(stage="M-DESIGN", initial_substate="DRAFT",
             drafting_role="archer", doc=None, docs=DESIGN_DOCS,
             committed_event="design.committed",
             review_substate="PRISM_REVIEW", reviewer="prism",
             verdict_event="prism.verdict",
             reviewer_passed_flag="prism_passed_this_round"),
)}

# Derived lookups, keyed like the facts they replace.
# committed_flag None (M-DESIGN's multi-doc design.committed) is routed through
# its own reducer (_on_design_committed), not _on_doc_committed.
_COMMITTED_EVENT = {sd.committed_event: sd
                    for sd in _STAGES.values()
                    if sd.committed_event and sd.committed_flag}
# review substate -> an owning StageDef (read for its reviewer role).
_REVIEW_SUBSTATE = {sd.review_substate: sd
                    for sd in _STAGES.values() if sd.review_substate}
# verdict event -> owning stages; a verdict event may be shared by several
# stages (lex.verdict: M-SPEC/M-ACC). See _on_reviewer_verdict.
_VERDICT_OWNERS: dict[str, list[StageDef]] = {}
for sd in _STAGES.values():
    if sd.verdict_event:
        _VERDICT_OWNERS.setdefault(sd.verdict_event, []).append(sd)


def _uncommit(s: State) -> None:
    """RESPOND must re-commit the current stage's doc to re-enter review."""
    sd = _STAGES.get(s.stage)
    setattr(s, (sd.committed_flag if sd else None) or "story_committed", False)


# -- event reducers: one small handler per event type, looked up by `apply`.
# Each is `(state, payload, envelope) -> None` and mutates state in place.

def _on_story_requested(s: State, p: dict, ev: EventEnvelope) -> None:
    s.run_id, s.version = ev.run_id, ev.version


def _on_stage_entered(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage = p["stage"]
    sd = _STAGES.get(s.stage)
    s.substate = sd.initial_substate if sd else None
    _reset_doc(s)
    _reset_review(s)
    s.current_attempt = 0
    s.stage_exited = False
    s.exit_validated = False
    s.review_round = 1
    s.sage_passed_this_round = s.lex_passed_this_round = False
    s.prism_passed_this_round = False
    s.design_validated = s.design_committed = 0
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
    if s.substate in _REVIEW_SUBSTATE:
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
        if s.substate in _REVIEW_SUBSTATE:
            _reset_review(s)
        else:
            _reset_doc(s)
        _consume_attempt(s)
        return
    if s.substate == "TRIAGE":
        s.awaiting = "triage"
    elif s.substate in _REVIEW_SUBSTATE:
        s.reviewer_produced = True
    else:
        s.doc_produced = True


def _on_verdict_passed(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.stage == "M-DESIGN":
        # Multi-doc sequence: each passed validate advances one design doc
        # (DRAFT/RESPOND pipeline and the EXIT gate share the counter).
        s.design_validated += 1
        if s.substate == "EXIT" and s.design_validated >= len(DESIGN_DOCS):
            s.exit_validated = True
    elif s.substate == "EXIT":
        s.exit_validated = True  # review-exit gate passed (AC-1502)
    else:
        s.doc_validated = True
    s.last_failure = None


def _on_verdict_failed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.last_failure = {k: p.get(k) for k in ("check", "reason", "evidence", "attempt")}
    if s.stage == "M-DESIGN":
        # BS-05: no human gate in M-DESIGN — a failed validate, even at the
        # EXIT gate, falls back to Archer re-dispatch; 3rd attempt escalates
        # (same budget as the DRAFT three-attempt escalation pattern).
        s.design_validated = 0
        if s.substate == "EXIT":
            s.exit_validated = False
            s.substate = "RESPOND"
        _reset_doc(s)
        s.current_attempt = int(p.get("attempt", s.current_attempt + 1))
        if s.current_attempt >= 3:
            s.status = "awaiting_human"
            s.awaiting = "escalation"
        return
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


def _on_doc_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # story/spec/acceptance.committed share one shape: the committed flag,
    # then (non-final) the stage's review substate. Facts come from the stage
    # owning this committed event, not from s.stage (FR-0160).
    sd = _COMMITTED_EVENT[ev.type]
    setattr(s, sd.committed_flag, True)
    if not p.get("final"):
        s.substate = sd.review_substate
        _reset_review(s)


def _on_design_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # M-DESIGN: one design.committed event per doc (payload carries `doc`).
    # After the third doc of the cycle the stage enters PRISM_REVIEW.
    s.design_committed += 1
    if s.design_committed >= len(DESIGN_DOCS):
        s.substate = _STAGES["M-DESIGN"].review_substate
        _reset_review(s)


def _on_prism_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    # M-DESIGN has no human review gate (BS-05 / flow.md §8.3): pass goes
    # straight to EXIT; revise re-dispatches Archer via RESPOND.
    s.prism_passed_this_round = p["verdict"] == "pass"
    if p["verdict"] == "pass":
        s.substate = "EXIT"
        s.design_validated = 0  # the exit gate re-validates all three docs
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    s.design_validated = 0
    s.design_committed = 0


def _on_reviewer_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    owners = _VERDICT_OWNERS[ev.type]
    if p["verdict"] == "pass":
        setattr(s, owners[0].reviewer_passed_flag, True)
        s.substate = "HUMAN_REVIEW"
        s.awaiting = "review"
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    if len(owners) == 1:
        # RESPOND must re-commit to re-enter review; a uniquely owned verdict
        # event (sage.verdict -> story) names the doc itself.
        setattr(s, owners[0].committed_flag, False)
    else:
        _uncommit(s)  # shared verdict event: the current stage picks the doc


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
    s.prism_passed_this_round = False


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
    "story.committed": _on_doc_committed,
    "spec.committed": _on_doc_committed,
    "acceptance.committed": _on_doc_committed,
    "design.committed": _on_design_committed,
    "sage.verdict": _on_reviewer_verdict,
    "lex.verdict": _on_reviewer_verdict,
    # M-DESIGN (flow.md §8): Prism's verdict has its own reducer — pass goes
    # to EXIT (never HUMAN_REVIEW), revise to RESPOND (BS-05: no human gate).
    "prism.verdict": _on_prism_verdict,
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


def _dispatch(role: str, substate: str, objective: str, doc: str | None = None,
              stage: str | None = None, attempt: int | None = None,
              review_round: int | None = None) -> Command:
    params = {"role": role, "substate": substate, "objective": objective}
    if doc:
        params["doc"] = doc
    if stage:
        params["stage"] = stage
    if attempt is not None:
        params["attempt"] = attempt
    if review_round is not None:
        params["review_round"] = review_round
    assignment = {
        "kind": substate,
        "template_kind": doc.removesuffix(".md") if doc else None,
        "skill": "tracks-discuz",
        "skill_version": "0.2",
    }
    params["assignment"] = assignment
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
    sd = _STAGES[stage]
    if not s.doc_dispatched:
        cmd = _dispatch(
            sd.drafting_role, sub, f"write {sd.doc}", sd.doc,
            stage=stage, attempt=s.current_attempt + 1,
            review_round=s.review_round,
        )
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
        return Command(kind="validate_document",
                       params={"doc": sd.doc, "checks": ["template"]})
    if not getattr(s, sd.committed_flag):
        return Command(
            kind="commit_document",
            params={"doc": sd.doc, "message": f"{stage}: draft {sd.doc}"},
        )
    return None


def _decide_design_draft(s: State, sub: str) -> Command | None:
    """M-DESIGN DRAFT/RESPOND pipeline (flow.md §8): ONE Archer dispatch covers
    all three docs (Decision A); validate per doc (template + AC trace, BS-06),
    then commit per doc (each commit emits design.committed); after the third
    commit the reducer enters PRISM_REVIEW."""
    sd = _STAGES["M-DESIGN"]
    if not s.doc_dispatched:
        cmd = Command(kind="dispatch_agent", params={
            "role": sd.drafting_role, "substate": sub,
            "objective": f"write {', '.join(DESIGN_DOCS)}",
            "stage": "M-DESIGN",
            "attempt": s.current_attempt + 1,
            "review_round": s.review_round,
            "docs": list(DESIGN_DOCS),
            "assignment": {"kind": sub, "template_kind": None,
                           "skill": "tracks-discuz", "skill_version": "0.2",
                           "docs": list(DESIGN_DOCS)},
        })
        if s.last_failure:
            cmd.params["evidence"] = dict(s.last_failure)  # FR-11
        return cmd
    if not s.doc_produced:
        return None
    if s.design_validated < len(DESIGN_DOCS):
        return Command(
            kind="validate_document",
            params={"doc": DESIGN_DOCS[s.design_validated],
                    "checks": ["template", "trace"]})
    if s.design_committed < len(DESIGN_DOCS):
        doc = DESIGN_DOCS[s.design_committed]
        return Command(kind="commit_document",
                       params={"doc": doc,
                               "message": f"M-DESIGN: {sub.lower()} {doc}"})
    return None


def _decide_design_exit(s: State) -> Command | None:
    """M-DESIGN EXIT (flow.md §8 / Decision A): gate-validate the three docs
    (template + discussion_ready), write nothing extra, then exit — the executor
    completes the run at the M-IMPL boundary (M-IMPL is not in v0.3)."""
    if s.stage_exited:
        return None  # crash-hole parity with _decide_exit
    if not s.exit_validated:
        return Command(
            kind="validate_document",
            params={"doc": DESIGN_DOCS[min(s.design_validated,
                                           len(DESIGN_DOCS) - 1)],
                    "checks": ["template", "discussion_ready"]})
    return Command(kind="write_frontmatter", params={"stage": "M-DESIGN"})


def _decide_exit(s: State, stage: str) -> Command | None:
    """EXIT: gate-validate (FR-150/AC-1502) then seal the document sha (FR-17/FR-23)."""
    if s.stage_exited:
        return None
    doc = _STAGES[stage].doc
    if not s.exit_validated:
        # Review-exit gate: only unresolved inline discussions block beyond
        # ordinary document/template validity.
        return Command(
            kind="validate_document",
            params={"doc": doc, "checks": ["template", "discussion_ready"]})
    # Executor seals frontmatter sha, commits, emits stage.exited
    # (+ story/spec.committed final sha, + next stage.entered / run.completed).
    return Command(
        kind="write_frontmatter", params={"doc": doc, "stage": stage, "field": "sha"}
    )


def _decide_pipeline(s: State, stage: str, sub: str) -> Command | None:
    """DRAFT/RESPOND: M-DESIGN runs its own multi-doc pipeline (flow.md §8)."""
    if stage == "M-DESIGN":
        return _decide_design_draft(s, sub)
    return _decide_draft(s, stage, sub)


def _decide_triage(s: State, stage: str) -> Command | None:
    if s.doc_dispatched:
        return None  # awaiting triage (set on outcome.received)
    sd = _STAGES[stage]
    return _dispatch(
        sd.drafting_role, "TRIAGE", "explore raw requirement", sd.doc,
        stage=stage, attempt=s.current_attempt + 1,
        review_round=s.review_round,
    )


def _decide_review(s: State, stage: str, sub: str) -> Command | None:
    sd = _STAGES[stage]
    reviewer = _REVIEW_SUBSTATE[sub].reviewer
    if s.reviewer_dispatched:
        return None
    cmd = _dispatch(
        reviewer, sub, f"{reviewer} review", sd.doc,
        stage=stage, attempt=s.current_attempt + 1,
        review_round=s.review_round,
    )
    if s.last_failure:
        cmd.params["evidence"] = dict(s.last_failure)  # FR-11
    if sd.docs:
        # Multi-doc stage (M-DESIGN): the reviewer's assignment names the whole
        # doc set like the drafter's (flow.md §8; no single target doc).
        cmd.params["docs"] = list(sd.docs)
        cmd.params["assignment"]["docs"] = list(sd.docs)
    return cmd


def _decide_exit_gate(s: State, stage: str) -> Command | None:
    if stage == "M-DESIGN":
        return _decide_design_exit(s)
    return _decide_exit(s, stage)


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
        return _decide_pipeline(s, stage, sub)
    if sub == "TRIAGE":
        return _decide_triage(s, stage)
    if sub in _REVIEW_SUBSTATE:
        return _decide_review(s, stage, sub)
    if sub == "EXIT":
        return _decide_exit_gate(s, stage)
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
