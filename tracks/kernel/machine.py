"""Pure state machine (NFR-02): `project(events) -> State` fold and
`decide(state) -> Command | None` next-step selection.

No clock, no filesystem, no I/O, no env reads. All non-determinism enters only
as events. `decide` returns a Command with `command_id=None`; the store assigns
the ULID at issue time so this stays pure.

v0.1 drives the happy path M-START → M-STORY → M-SPEC to EXIT. Reject
(no_go/park) and rollback teardown emit their leading command; RESPOND
(review comments) is scaffolded but not exercised by the happy path.
"""

# pylint: disable=too-many-lines
# v0.5: machine.py serves as the single-file state machine with all reducers
# and decide() logic. Splitting would add import complexity without reducing
# cognitive load, as all reducers operate on the same State dataclass.

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .events import Command, EventEnvelope
from .m_impl import (
    _M_IMPL_CONTEXT_DOCS,  # noqa: F401
    _M_IMPL_CRITERIA_PACK,  # noqa: F401
    _M_IMPL_REVIEW_SUBSTATES,  # noqa: F401
    _decide_m_impl,
    _on_baseline_frozen,
    _on_green_committed,
    _on_m_impl_outcome_done,
    _on_m_impl_prism_verdict,
    _on_m_impl_verdict_failed,
    _on_m_impl_verdict_passed,
    _on_red_checkpointed,
    _on_refactor_committed,
    _on_refactor_no_change,
    _on_task_completed,
    _on_task_started,
    _on_taskgraph_committed,
    _on_writelock_granted,
    _on_writelock_released,
    _set_m_impl_dispatch_flags,
)
from .m_test import (
    _CRITERIA_PACK,  # noqa: F401
    _M_TEST_CONTEXT_DOCS,  # noqa: F401
    _consume_attempt,
    _decide_m_test,
    _m_test_diagnose_route,  # noqa: F401
    _m_test_exit_route,  # noqa: F401
    _m_test_prism_dispatch,  # noqa: F401
    _m_test_shield_dispatch,  # noqa: F401
    _on_m_test_outcome_done,
    _on_m_test_verdict_failed,
    _on_red_validated,
    _on_test_collected,
    _on_test_committed,
    _on_test_written,
    _reset_doc,
    _reset_review,
)


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
    # M-TEST (flow.md §9 / SM-01, FR-0010~0070). The shared <=3 attempt budget
    # reuses `current_attempt` (reset on stage.entered); the fields below track
    # the per-substate progress that decide() / reducers consult.
    test_collected: bool = False  # COLLECT succeeded
    red_validated: bool = False  # RED_CHECK: all failures are legit Red
    red_findings: list | None = None  # RED_CHECK per-failure classifications
    trace_passed: bool = False  # EXIT trac check trace closure
    test_committed: bool = False  # test asset frozen (commit_tests done)
    criteria_pack_loaded: dict | None = None  # Prism's loaded pack identity
    diagnose_classification: str | None = None  # DIAGNOSE routing verdict
    # v0.5 ResultCheckpoint pipeline (batch 1): when set, decide() drives
    # validate_result -> checkpoint_result -> publish_result instead of the
    # normal substate logic. Cleared by the domain event reducer (or
    # verdict.failed). None on old replays (backward compatible).
    active_result: dict | None = None
    # v0.5 no_diff peer review: when requires_diff fires on an author result
    # with no workspace diff, enter explain->review before failing.
    no_diff_explanation: str | None = None
    no_diff_reviewer_dispatched: bool = False
    # M-IMPL (flow.md §10): task-graph implementation with inner RGR cycle.
    baseline_frozen: bool = False
    taskgraph_committed: bool = False
    tasks_total: int = 0
    tasks_completed: int = 0
    writelock_held: bool = False
    current_task_id: str | None = None
    r_tree_identity: str | None = None  # Red checkpoint sha (B..R lineage)
    green_committed: bool = False  # formal G commit for current task
    refactor_done: bool = False  # refactor committed or no-change
    island_2_passed: bool = False  # ISLAND_GATE_2 exit gate
    taskgraph_digest: str | None = None
    taskgraph_path: str | None = None
    task_refs: list[dict] = field(default_factory=list)
    current_task_metadata: dict | None = None
    current_manifest: dict | None = None


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

_STAGES = {
    sd.stage: sd
    for sd in (
        StageDef(
            stage="M-STORY",
            initial_substate="TRIAGE",
            drafting_role="scribe",
            doc="story.md",
            committed_event="story.committed",
            committed_flag="story_committed",
            review_substate="SAGE_REVIEW",
            reviewer="sage",
            verdict_event="sage.verdict",
            reviewer_passed_flag="sage_passed_this_round",
        ),
        # M-ACC mirrors M-SPEC's review loop (Sage drafts, Lex reviews),
        # on acceptance.md.
        StageDef(
            stage="M-SPEC",
            initial_substate="DRAFT",
            drafting_role="sage",
            doc="spec.md",
            committed_event="spec.committed",
            committed_flag="spec_committed",
            review_substate="LEX_REVIEW",
            reviewer="lex",
            verdict_event="lex.verdict",
            reviewer_passed_flag="lex_passed_this_round",
        ),
        StageDef(
            stage="M-ACC",
            initial_substate="DRAFT",
            drafting_role="sage",
            doc="acceptance.md",
            committed_event="acceptance.committed",
            committed_flag="acceptance_committed",
            review_substate="LEX_REVIEW",
            reviewer="lex",
            verdict_event="lex.verdict",
            reviewer_passed_flag="lex_passed_this_round",
        ),
        StageDef(stage="M-REQ-APPROVAL", initial_substate="PREVIEW"),
        # M-DESIGN (flow.md §8): pure technical stage — no human.review/approval
        # gates (BS-05). Multi-doc: one Archer dispatch drafts all DESIGN_DOCS;
        # the per-doc control flow is explicit (_decide_design_draft/_exit), like
        # _decide_approval. doc is None: no single target doc.
        StageDef(
            stage="M-DESIGN",
            initial_substate="DRAFT",
            drafting_role="archer",
            doc=None,
            docs=DESIGN_DOCS,
            committed_event="design.committed",
            review_substate="PRISM_REVIEW",
            reviewer="prism",
            verdict_event="prism.verdict",
            reviewer_passed_flag="prism_passed_this_round",
        ),
        # M-TEST (flow.md §9 / SM-01): no drafting_role/doc/reviewer -- the Shield
        # and Prism dispatches are driven by the explicit `_decide_m_test` control
        # flow (like `_decide_approval`), not by the StageDef table. initial_substate
        # is DISPATCH (SM-01.1).
        StageDef(stage="M-TEST", initial_substate="DISPATCH"),
        # M-IMPL (flow.md §10): explicit control flow via `_decide_m_impl`, like
        # M-TEST. No drafting_role/doc/reviewer -- Archer/Devon/Prism/Shield
        # dispatches are driven by the explicit control flow.
        StageDef(stage="M-IMPL", initial_substate="BASELINE"),
    )
}

# Derived lookups, keyed like the facts they replace.
# committed_flag None (M-DESIGN's multi-doc design.committed) is routed through
# its own reducer (_on_design_committed), not _on_doc_committed.
_COMMITTED_EVENT = {
    sd.committed_event: sd for sd in _STAGES.values() if sd.committed_event and sd.committed_flag
}
# review substate -> an owning StageDef (read for its reviewer role).
_REVIEW_SUBSTATE = {sd.review_substate: sd for sd in _STAGES.values() if sd.review_substate}
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
    s.active_result = None  # v0.5: clear pipeline on stage transition
    if s.stage == "M-REQ-APPROVAL":
        # re-entry after RETURNED rollback restarts the approval cycle fresh
        s.preview_ready = s.approved = s.returned = s.issues_created = False
        s.return_target = s.approval_digest = s.approval_actor = None
    if s.stage == "M-TEST":
        # SM-01.1: fresh M-TEST cycle. current_attempt (shared <=3 budget) is
        # already reset above; reset the per-substate progress fields.
        s.test_collected = s.red_validated = s.trace_passed = False
        s.test_committed = False
        s.red_findings = s.criteria_pack_loaded = None
        s.diagnose_classification = None
    if s.stage == "M-IMPL":
        # flow.md §10: fresh M-IMPL cycle. Reset all per-stage fields.
        s.baseline_frozen = False
        s.taskgraph_committed = False
        s.tasks_total = 0
        s.tasks_completed = 0
        s.writelock_held = False
        s.current_task_id = None
        s.r_tree_identity = None
        s.green_committed = False
        s.refactor_done = False
        s.island_2_passed = False
        s.diagnose_classification = None
        s.taskgraph_digest = None
        s.taskgraph_path = None
        s.task_refs = []
        s.current_task_metadata = None
        s.current_manifest = None


def _on_stage_exited(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage_exited = True


def _on_stage_rolled_back(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage = p["to_stage"]
    s.substate = "DRAFT"
    _reset_doc(s)
    _reset_review(s)
    s.current_attempt = 0
    # SM-01.12 evidence scoping: ONLY the stub_gap rollback (M-TEST ->
    # M-DESIGN) clears last_failure -- the failed-outcome evidence describes a
    # design-contract gap, not a test-run gap, so it is stale relative to the
    # Archer re-dispatch and must not leak into it. Every other rollback
    # (scope_overflow/ac_gap/spec_gap/human_return) PRESERVES last_failure so
    # the downstream drafter still receives the original failure evidence
    # (FR-11, AC-20a: Scribe re-scopes the story from the overflow evidence).
    # The route is read from the stage.rolled_back payload, not from the
    # already-mutated current state, so the decision is replay-stable.
    if p.get("to_stage") == "M-DESIGN" and p.get("reason") == "stub_gap":
        s.last_failure = None
    s.spec_committed = False
    s.story_committed = False  # the redone story must be re-committed
    s.acceptance_committed = False
    s.scope_overflow = False
    s.returned = False
    s.return_target = None
    s.active_result = None  # v0.5: clear pipeline on rollback
    # M-DESIGN re-entry after stub_gap rollback (SM-01.12): reset the design
    # doc counters so _decide_design_draft re-validates and re-commits the trio.
    if s.stage == "M-DESIGN":
        s.design_validated = s.design_committed = 0
        s.prism_passed_this_round = False
        s.exit_validated = False
        s.review_round = 1


def _on_command_issued(s: State, p: dict, ev: EventEnvelope) -> None:
    cmd = p.get("command", {})
    s.pending = cmd
    if cmd.get("kind") != "dispatch_agent":
        if s.stage == "M-TEST" and cmd.get("kind") == "collect_tests":
            s.substate = "COLLECT"  # SM-01.3: WRITE -> COLLECT
        return
    # v0.5 no_diff peer review (checked before M-TEST so the right flag is set).
    if s.substate == "NO_DIFF_REVIEW":
        s.no_diff_reviewer_dispatched = True
        return
    if s.substate == "NO_DIFF_EXPLAIN":
        s.doc_dispatched = True
        return
    if s.stage == "M-TEST":
        # SM-01.2: DISPATCH -> WRITE on the first Shield dispatch. Subsequent
        # Shield re-dispatches (WRITE/SM-01.4/.6/.8/.11/.15) stay in WRITE.
        # PRISM_REVIEW dispatches set reviewer_dispatched so _decide_m_test
        # can guard against re-dispatch while awaiting the verdict.
        if s.substate == "DISPATCH":
            s.substate = "WRITE"
        sub = cmd.get("params", {}).get("substate")
        if sub == "PRISM_REVIEW":
            s.reviewer_dispatched = True
        else:
            s.doc_dispatched = True
        return
    if s.stage == "M-IMPL":
        _set_m_impl_dispatch_flags(s, cmd)
        return
    if s.substate in _REVIEW_SUBSTATE:
        s.reviewer_dispatched = True
    else:
        s.doc_dispatched = True


def _on_outcome_received(s: State, p: dict, ev: EventEnvelope) -> None:
    status = p.get("status")
    if s.substate == "ISSUES":
        if status != "done":
            _consume_attempt(s)
        return
    if status != "done":
        _handle_failed_outcome(s, p)
        return
    # v0.5: when the outcome carries a result_checkpoint payload, atomically
    # project active_result in the same event - no crash window between
    # outcome.received and a separate result.submitted event.
    checkpoint = p.get("result_checkpoint")
    if checkpoint is not None:
        s.active_result = dict(checkpoint)
    if s.stage == "M-TEST":
        if checkpoint is None:
            _on_m_test_outcome_done(s)
        return
    if s.stage == "M-IMPL":
        if checkpoint is None:
            _on_m_impl_outcome_done(s)
        return
    if s.substate == "TRIAGE":
        s.awaiting = "triage"
    elif s.substate in _REVIEW_SUBSTATE:
        s.reviewer_produced = True
    else:
        s.doc_produced = True


def _reset_m_test_dispatch_flag(s: State) -> None:
    """Reset the flag guarding the current M-TEST substate's dispatcher.

    A write-flags-only reset left reviewer_dispatched stuck True after a
    failed PRISM_REVIEW dispatch, so decide() halted forever even after
    human.retry cleared the escalation gate (run 01KZTHE7RMZE6110PK9C54K1E2
    stalled post prism non_zero_exit)."""
    if s.substate == "PRISM_REVIEW":
        _reset_review(s)
    elif s.substate == "NO_DIFF_REVIEW":
        s.no_diff_reviewer_dispatched = False
    else:
        _reset_doc(s)


def _handle_failed_outcome(s: State, p: dict) -> None:
    """FR-0210: a failed dispatch_agent outcome consumes an attempt from the
    same accounting as verdict.failed, carries failure evidence into the
    re-dispatch prompt (FR-11), and escalates to awaiting_human at the 3rd
    attempt. run048: None/absent/unknown status is treated as ``failed`` with
    ``agent_error`` so the 3-attempt escalation evidence stays meaningful."""
    s.last_failure = {
        "check": p.get("failure_class") or "agent_error",
        "reason": p.get("self_report"),
        "evidence": p.get("audit_evidence") or p.get("artifact_ref"),
    }
    if s.stage == "M-TEST":
        # D-32/SM-01.12: an invalid M-DESIGN test-task contract surfaces as a
        # stub_gap failed outcome (emitted by the executor instead of calling
        # the backend). It routes straight to DIAGNOSE/stub_gap — no attempt
        # consumed, no Human, no three wasted Shield attempts — and decide()
        # rolls back to M-DESIGN.
        if p.get("failure_class") == "stub_gap":
            _reset_doc(s)
            s.diagnose_classification = "stub_gap"
            s.substate = "DIAGNOSE"
            return
        _reset_m_test_dispatch_flag(s)
        _consume_attempt(s)
        return
    if s.stage == "M-IMPL":
        if (
            p.get("role") == "devon"
            and p.get("status") == "failed"
            and s.substate in ("RED", "GREEN")
        ):
            # A backend execution failure has unknown attribution. Preserve its
            # evidence, spend the same bounded attempt, and let Prism diagnose
            # it instead of blindly repeating the failed phase.
            _reset_doc(s)
            _reset_review(s)
            s.diagnose_classification = None
            s.substate = "DIAGNOSE"
            _consume_attempt(s)
            return
        if s.substate in _M_IMPL_REVIEW_SUBSTATES:
            _reset_review(s)
        else:
            _reset_doc(s)
        _consume_attempt(s)
        return
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    _consume_attempt(s)


def _on_verdict_passed(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.stage == "M-IMPL":
        _on_m_impl_verdict_passed(s, p)
        return
    if s.stage == "M-TEST":
        # EXIT trace gate passed (SM-01.14): trace closure verified; the next
        # decide() step issues commit_tests.
        s.trace_passed = True
        s.last_failure = None
        return
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
    is_human = bool(s.active_result and s.active_result.get("actor_kind") == "human")
    s.active_result = None  # v0.5: pipeline failure clears the checkpoint
    if is_human:
        # Human pipeline failure: keep the awaiting gate, no agent retry,
        # no escalation, no substate change.
        return
    if s.stage == "M-TEST":
        _on_m_test_verdict_failed(s, p)
        return
    if s.stage == "M-IMPL":
        _on_m_impl_verdict_failed(s, p)
        return
    if s.stage == "M-DESIGN":
        _on_design_verdict_failed(s, p)
        return
    if p.get("check") == "commit":
        # D-30/F-1: hook rejected commit (document or EXIT seal) -> re-dispatch
        # drafter with evidence; mirror DRAFT validate-failure budget, not AC-1502.
        # v0.5: if the failure came from a reviewer checkpoint (substate is a
        # review substate), reset the reviewer instead of the author.
        if s.substate in _REVIEW_SUBSTATE:
            _reset_review(s)
        else:
            _reset_doc(s)
            _uncommit(s)
            s.substate = "DRAFT"
        s.exit_validated = False
        _escalate_or_continue(s, p)
        return
    if s.substate == "EXIT":
        # AC-1502: gate failed at review exit -> block exit, await human.
        s.status = "awaiting_human"
        s.awaiting = "review"
        return
    # v0.5: if the failure came from a reviewer pipeline (substate is a review
    # substate), reset the reviewer instead of the author doc.
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    if p.get("check") == "scope_overflow":
        s.scope_overflow = True  # FR-20: rollback, never escalation
        return
    _escalate_or_continue(s, p)


def _escalate_or_continue(s: State, p: dict) -> None:
    """Consume an attempt and escalate on the 3rd failure."""
    s.current_attempt = int(p.get("attempt", s.current_attempt + 1))
    if s.current_attempt >= 3:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


def _on_design_verdict_failed(s: State, p: dict) -> None:
    """BS-05: no human gate in M-DESIGN - a failed validate, even at the
    EXIT gate, falls back to Archer re-dispatch; 3rd attempt escalates.
    D-30/F-1: design_committed also resets.
    D-32: a failed reviewer ResultCheckpoint (e.g. Prism review no_diff) resets
    only the reviewer flags and retries the same actor — it is NOT a revise
    verdict, must not discard the already-committed design docs, and never a
    Human."""
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
        _escalate_or_continue(s, p)
        return
    s.design_validated = 0
    s.design_committed = 0
    if s.substate == "EXIT":
        s.exit_validated = False
        s.substate = "RESPOND"
    _reset_doc(s)
    _escalate_or_continue(s, p)


def _on_doc_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # story/spec/acceptance.committed share one shape: the committed flag,
    # then (non-final) the stage's review substate. Facts come from the stage
    # owning this committed event, not from s.stage (FR-0160).
    sd = _COMMITTED_EVENT[ev.type]
    setattr(s, sd.committed_flag, True)
    s.active_result = None  # v0.5: pipeline publish complete
    if not p.get("final"):
        s.substate = sd.review_substate
        _reset_review(s)


def _on_design_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # M-DESIGN: one design.committed event per doc (payload carries `doc`).
    # After the third doc of the cycle the stage enters PRISM_REVIEW.
    # v0.5: active_result is only cleared after the last doc — publish_result
    # emits all 3 design.committed events in one call; clearing on the first
    # would break reconcile (active_result=None → publish skipped).
    s.design_committed += 1
    if s.design_committed >= len(DESIGN_DOCS):
        s.active_result = None  # pipeline publish complete (all docs)
        s.substate = _STAGES["M-DESIGN"].review_substate
        _reset_review(s)


def _on_prism_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    if s.stage == "M-IMPL":
        _on_m_impl_prism_verdict(s, p)
        return
    if s.stage == "M-TEST":
        # SM-01.7/.8: pass -> RED_CHECK; revise -> WRITE (re-dispatch Shield).
        # The shared <=3 budget is consumed on revise (NOT reset -- unlike
        # M-DESIGN, M-TEST shares one budget across WRITE/PRISM_REVIEW/EXIT).
        s.criteria_pack_loaded = p.get("criteria_pack")
        if p["verdict"] == "pass":
            s.substate = "RED_CHECK"
            return
        # REVISE: route by defect_classification (default test_defect).
        # `or` treats absent AND None/empty (e.g. old replay events carrying
        # defect_classification=null) as test_defect; unknown non-empty tokens
        # still fall through all branches -> fail-closed (substate unchanged).
        dc = p.get("defect_classification") or "test_defect"
        if dc == "test_defect":
            s.substate = "WRITE"
            _reset_doc(s)
            _consume_attempt(s)
        elif dc == "test_plan_defect":
            # Rollback to M-DESIGN (like stub_gap in DIAGNOSE)
            s.substate = "DIAGNOSE"
            s.diagnose_classification = "stub_gap"
        elif dc == "acceptance_defect":
            # awaiting Human, then rollback M-ACC
            s.status = "awaiting_human"
            s.awaiting = "rollback"
            s.return_target = "M-ACC"
        elif dc == "spec_defect":
            # awaiting Human, then rollback M-SPEC
            s.status = "awaiting_human"
            s.awaiting = "rollback"
            s.return_target = "M-SPEC"
        return
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
    # Fresh 3-attempt budget for RESPOND (flow.md §8.3: 重派 Archer <=3 per
    # RESPOND round); DRAFT/EXIT failures must not consume the RESPOND budget.
    s.current_attempt = 0


def _on_reviewer_verdict(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    owners = _VERDICT_OWNERS[ev.type]
    if p["verdict"] == "pass":
        setattr(s, owners[0].reviewer_passed_flag, True)
        s.substate = "HUMAN_REVIEW"
        s.awaiting = "review"
        return
    s.substate = "RESPOND"
    _reset_doc(s)
    # Fresh 3-attempt budget for RESPOND (flow.md: 重派 <=3 per RESPOND round);
    # DRAFT failures must not consume the RESPOND budget.
    s.current_attempt = 0
    if len(owners) == 1:
        # RESPOND must re-commit to re-enter review; a uniquely owned verdict
        # event (sage.verdict -> story) names the doc itself.
        setattr(s, owners[0].committed_flag, False)
    else:
        _uncommit(s)  # shared verdict event: the current stage picks the doc


def _on_human_triage(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    s.awaiting = None
    s.triage_decision = p["decision"]
    if p["decision"] == "go":
        s.substate = "DRAFT"
        _reset_doc(s)
        # Fresh retry budget: TRIAGE failures (and their evidence) must not
        # carry into DRAFT -- the two substates share `current_attempt` but
        # budget per substate (FR-11).  DRAFT starts at attempt 0 so decide()
        # emits attempt=1; last_failure=None so no stale TRIAGE evidence leaks
        # into the DRAFT dispatch prompt.
        s.current_attempt = 0
        s.last_failure = None
    else:
        s.substate = None


def _on_human_review(s: State, p: dict, ev: EventEnvelope) -> None:
    s.active_result = None  # v0.5: pipeline publish complete
    s.awaiting = None
    s.substate = "EXIT" if p["action"] == "no_comment" else "RESPOND"
    if s.substate != "RESPOND":
        return
    _reset_doc(s)
    s.review_diff_ref = p.get("diff_ref")
    # Fresh 3-attempt budget for the new RESPOND round triggered by the human
    # comment; prior DRAFT/RESPOND failures must not consume it.
    s.current_attempt = 0
    _uncommit(s)


def _on_human_retry(s: State, p: dict, ev: EventEnvelope) -> None:
    # Fix 4: human.retry clears escalation and lets decide() re-dispatch from
    # the current substate with a fresh attempt budget. last_failure is
    # preserved so the re-dispatch prompt still carries failure evidence (FR-11)
    # for the agent to act on. Failure handling has already reset the
    # appropriate dispatch flag (doc/review) before escalation; no need to
    # indiscriminately reset both here.
    #
    # --clear-evidence (payload clear_evidence=true): the operator signals the
    # underlying program/config/validator was fixed, so the old failure
    # evidence is stale. last_failure is dropped and the dispatch flag reset so
    # decide() re-dispatches from a clean slate (attempt=1, no evidence). This
    # also covers the active-state recovery path: an ordinary retry already
    # cleared escalation, a dispatch was issued and then killed by Maestro
    # (doc_dispatched stays True, no result), and the stale evidence must not
    # leak into the next dispatch. apply() already cleared s.pending (any non-
    # command.issued event does); _reset_doc lets decide() issue a fresh one.
    if p.get("clear_evidence"):
        s.last_failure = None
        _reset_doc(s)
    s.awaiting = None
    s.status = "active"
    s.current_attempt = 0


def _on_review_round_started(s: State, p: dict, ev: EventEnvelope) -> None:
    s.review_round += 1
    _reset_review(s)
    s.sage_passed_this_round = s.lex_passed_this_round = False
    s.prism_passed_this_round = False


def _on_backlog_recorded(s: State, p: dict, ev: EventEnvelope) -> None:
    s.backlog_recorded = True
    # SM-01.2 phantom guard: a `backlog.recorded` event on a run with no prior
    # `stage.entered` (queue-while-active, flow.md §3.1) is a placeholder, not a
    # live run. Mark it `backlog` so `active_run()` / `cmd_status` skip it - it
    # must never count as the active run and block future starts. The reject
    # teardown path (FR-09) reaches this reducer with `stage` already set
    # (M-STORY), so the conditional leaves that flow's `status='active'` intact
    # for `decide()` to continue with delete_branch -> complete_run. This is the
    # projection source of truth: `rebuild_projections()` re-folds events
    # through this reducer, so the distinction survives a drop+rebuild.
    if s.stage is None:
        s.status = "backlog"


def _on_preview_generated(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-05.2 (also the C-02 / stale re-preview path: back to the human gate)
    s.preview_ready = True
    s.approved = False
    s.substate = "AWAIT_HUMAN"
    s.awaiting = "approval"
    s.status = "awaiting_human"


def _on_human_approval(s: State, p: dict, ev: EventEnvelope) -> None:
    if s.awaiting == "rollback":
        # SM-01.13 / flow.md §10.1: Human approved the ac_gap/spec_gap
        # rollback (M-TEST or M-IMPL) -- clear the gate and let decide()
        # produce rollback_stage(return_target).
        s.awaiting = None
        s.status = "active"
        s.substate = "RETURNED"
        return
    # SM-05.3 - the ONLY entry into APPROVED (FR-0180 hard human gate)
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

# -- v0.5 ResultCheckpoint pipeline reducers (batch 1: M-STORY/M-SPEC/M-ACC) ---


def _on_result_submitted(s: State, p: dict, ev: EventEnvelope) -> None:
    """Capture a result into the pipeline. The payload carries everything the
    pipeline needs: source actor, stage/substate, artifacts to validate,
    allowed_paths to checkpoint, base_sha, checks, domain_event to publish,
    actor_kind (agent|human), commit_label for checkpoint commits.

    v0.5 review-A: result_id (stable audit identity), digests (artifact sha256
    at capture time, re-verified at checkpoint), forbid_diff (no-comment gate),
    discussion_only (reviewer diff must be canonical discussion change)."""
    s.active_result = dict(p)
    s.pending = None


def _on_result_validated(s: State, p: dict, ev: EventEnvelope) -> None:
    """Validation passed; mark the active result as validated. For author
    (DRAFT/RESPOND) results, also set doc_validated so decide() never re-issues
    validate_document after the pipeline completes."""
    s.pending = None
    if s.active_result is None:
        return
    s.active_result["validated"] = True
    if s.active_result.get("substate") in ("DRAFT", "RESPOND"):
        s.doc_validated = True


def _on_result_checkpointed(s: State, p: dict, ev: EventEnvelope) -> None:
    """Checkpoint committed (or no-change); mark the active result."""
    s.pending = None
    if s.active_result is None:
        return
    s.active_result["checkpointed"] = True
    s.active_result["commit_sha"] = p.get("commit_sha")
    s.active_result["created_commit"] = p.get("created_commit", False)


# -- v0.5 no_diff peer review reducers --------------------------------------
# requires_diff on an author result with no diff emits no_diff.detected.
# Flow: NO_DIFF_EXPLAIN -> NO_DIFF_REVIEW -> (pass: resume pipeline) |
# (revise: verdict.failed(no_diff_justified)). active_result is preserved
# across the review so the pipeline can resume after a pass.


def _on_no_diff_detected(s: State, p: dict, ev: EventEnvelope) -> None:
    s.pending = None
    if s.active_result is not None:
        s.active_result["no_diff_origin_substate"] = s.substate
    s.substate = "NO_DIFF_EXPLAIN"
    s.doc_dispatched = False  # re-dispatch author for the explanation
    s.no_diff_explanation = None
    s.no_diff_reviewer_dispatched = False
    # active_result is NOT cleared — pipeline resumes after review pass.


def _on_no_diff_explained(s: State, p: dict, ev: EventEnvelope) -> None:
    s.pending = None
    s.no_diff_explanation = p.get("explanation", "")
    s.substate = "NO_DIFF_REVIEW"
    s.no_diff_reviewer_dispatched = False  # dispatch reviewer


def _on_no_diff_reviewed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.pending = None
    verdict = p.get("verdict")
    if verdict == "pass":
        # Pipeline resumes: mark validated AND no_diff-approved so the
        # checkpoint's requires_diff gate is skipped.
        if s.active_result is not None:
            s.active_result["validated"] = True
            s.active_result["no_diff_approved"] = True
            s.substate = s.active_result.pop("no_diff_origin_substate", "DRAFT")
        s.no_diff_explanation = None
        s.no_diff_reviewer_dispatched = False
        return
    # Reviewer rejected: route through the stage's verdict.failed handler.
    origin = s.active_result.pop("no_diff_origin_substate", "DRAFT") if s.active_result else "DRAFT"
    explanation = s.no_diff_explanation
    s.active_result = None
    s.substate = origin
    s.no_diff_explanation = None
    s.no_diff_reviewer_dispatched = False
    s.last_failure = {
        "check": "no_diff_justified",
        "reason": "reviewer rejected no-diff explanation",
        "evidence": explanation,
        "attempt": p.get("attempt"),
    }
    if s.stage == "M-TEST":
        _on_m_test_verdict_failed(s, {"check": "no_diff_justified", **p})
        return
    if s.stage == "M-IMPL":
        _on_m_impl_verdict_failed(s, {"check": "no_diff_justified", **p})
        return
    if s.stage == "M-DESIGN":
        _on_design_verdict_failed(s, {"check": "no_diff_justified", **p})
        return
    if s.substate in _REVIEW_SUBSTATE:
        _reset_review(s)
    else:
        _reset_doc(s)
    _escalate_or_continue(s, {"attempt": p.get("attempt", s.current_attempt + 1)})


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
    "human.retry": _on_human_retry,
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
    # v0.4 M-TEST (flow.md §9 / SM-01)
    "test.collected": _on_test_collected,
    "red.validated": _on_red_validated,
    "test.committed": _on_test_committed,
    # v0.5 batch 2: M-TEST Shield WRITE pipeline publish
    "test.written": _on_test_written,
    # v0.5 ResultCheckpoint pipeline (batch 1)
    "result.submitted": _on_result_submitted,
    "result.validated": _on_result_validated,
    "result.checkpointed": _on_result_checkpointed,
    # v0.5 no_diff peer review
    "no_diff.detected": _on_no_diff_detected,
    "no_diff.explained": _on_no_diff_explained,
    "no_diff.reviewed": _on_no_diff_reviewed,
    # v0.5 M-IMPL (flow.md §10)
    "baseline.frozen": _on_baseline_frozen,
    "taskgraph.committed": _on_taskgraph_committed,
    "task.started": _on_task_started,
    "writelock.granted": _on_writelock_granted,
    "writelock.released": _on_writelock_released,
    "red.checkpointed": _on_red_checkpointed,
    "green.committed": _on_green_committed,
    "refactor.committed": _on_refactor_committed,
    "refactor.no_change": _on_refactor_no_change,
    "task.completed": _on_task_completed,
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


def _dispatch(
    role: str,
    substate: str,
    objective: str,
    doc: str | None = None,
    stage: str | None = None,
    attempt: int | None = None,
    review_round: int | None = None,
) -> Command:
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
            sd.drafting_role,
            sub,
            f"write {sd.doc}",
            sd.doc,
            stage=stage,
            attempt=s.current_attempt + 1,
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
        return Command(kind="validate_document", params={"doc": sd.doc, "checks": ["template"]})
    if not getattr(s, sd.committed_flag):
        return Command(
            kind="commit_document",
            params={"doc": sd.doc, "message": f"{stage}: draft {sd.doc}"},
        )
    return None


def _decide_design_draft(s: State, sub: str) -> Command | None:
    """M-DESIGN DRAFT/RESPOND pipeline (flow.md §8): ONE Archer dispatch covers
    all three docs (Decision A). v0.5 batch 2: the ResultCheckpoint pipeline
    handles validate+checkpoint+publish; decide() only fires the dispatch."""
    sd = _STAGES["M-DESIGN"]
    if not s.doc_dispatched:
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": sd.drafting_role,
                "substate": sub,
                "objective": f"write {', '.join(DESIGN_DOCS)}",
                "stage": "M-DESIGN",
                "attempt": s.current_attempt + 1,
                "review_round": s.review_round,
                "docs": list(DESIGN_DOCS),
                "assignment": {
                    "kind": sub,
                    "template_kind": None,
                    "templates": [doc.removesuffix(".md") for doc in DESIGN_DOCS],
                    # batch B: multi-skill — the design doc-set plus the
                    # host guard-stack catalog (single-skill shape stays
                    # for every other stage, see _dispatch).
                    "skills": ["tracks-discuz", "tracks-quality-guards"],
                    "docs": list(DESIGN_DOCS),
                },
            },
        )
        if s.last_failure:
            cmd.params["evidence"] = dict(s.last_failure)  # FR-11
        return cmd
    return None  # awaiting outcome -> pipeline drives validate+checkpoint+publish


def _decide_design_exit(s: State) -> Command | None:
    """M-DESIGN EXIT (flow.md §8 / Decision A): gate-validate the three docs
    (template + discussion_ready), write nothing extra, then exit — the executor
    completes the run at the M-IMPL boundary (M-IMPL is not in v0.3).

    D-28: the test-plan additionally runs the design trace + the structured
    test-task contract check (checks=["trace", "test_tasks"]) so an invalid
    M-DESIGN→M-TEST contract never exits."""
    if s.stage_exited:
        return None  # crash-hole parity with _decide_exit
    if not s.exit_validated:
        doc = DESIGN_DOCS[min(s.design_validated, len(DESIGN_DOCS) - 1)]
        checks = ["template", "discussion_ready"]
        if doc == "test-plan.md":
            checks += ["trace", "test_tasks"]
        return Command(kind="validate_document", params={"doc": doc, "checks": checks})
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
            params={"doc": doc, "checks": ["template", "discussion_ready"]},
        )
    # Executor seals frontmatter sha, commits, emits stage.exited
    # (+ story/spec.committed final sha, + next stage.entered / run.completed).
    return Command(kind="write_frontmatter", params={"doc": doc, "stage": stage, "field": "sha"})


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
        sd.drafting_role,
        "TRIAGE",
        "explore raw requirement",
        sd.doc,
        stage=stage,
        attempt=s.current_attempt + 1,
        review_round=s.review_round,
    )


def _decide_review(s: State, stage: str, sub: str) -> Command | None:
    sd = _STAGES[stage]
    reviewer = _REVIEW_SUBSTATE[sub].reviewer
    if s.reviewer_dispatched:
        return None
    cmd = _dispatch(
        reviewer,
        sub,
        f"{reviewer} review",
        sd.doc,
        stage=stage,
        attempt=s.current_attempt + 1,
        review_round=s.review_round,
    )
    if s.last_failure:
        cmd.params["evidence"] = dict(s.last_failure)  # FR-11
    if sd.docs:
        # Multi-doc stage (M-DESIGN): the reviewer's assignment names the whole
        # doc set like the drafter's (flow.md §8; no single target doc).
        cmd.params["docs"] = list(sd.docs)
        cmd.params["assignment"]["docs"] = list(sd.docs)
        # D-29: Prism's M-DESIGN review consumes the design criteria pack
        # (tracks-prism-design) alongside the discussion protocol; switch the
        # single-skill shape to the multi-skill list, like the Archer DRAFT.
        cmd.params["assignment"].pop("skill", None)
        cmd.params["assignment"].pop("skill_version", None)
        cmd.params["assignment"]["skills"] = ["tracks-discuz", "tracks-prism-design"]
    return cmd


def _decide_exit_gate(s: State, stage: str) -> Command | None:
    if stage == "M-DESIGN":
        return _decide_design_exit(s)
    return _decide_exit(s, stage)


def _no_diff_dispatch_params(s, substate, role, docs, doc, objective):
    """Build dispatch_agent params for NO_DIFF_EXPLAIN / NO_DIFF_REVIEW."""
    ar = s.active_result or {}
    ctx = {
        "artifacts": ar.get("artifacts", []),
        "base_sha": ar.get("base_sha"),
        "result_id": ar.get("result_id"),
        "stage": ar.get("stage", s.stage),
        "substate": ar.get("no_diff_origin_substate", s.substate),
    }
    if substate == "NO_DIFF_REVIEW":
        ctx["explanation"] = s.no_diff_explanation
    params = {
        "role": role,
        "substate": substate,
        "objective": objective,
        "stage": s.stage,
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "no_diff_context": ctx,
        "assignment": {
            "kind": substate,
            "skill": "tracks-discuz",
            "template_kind": doc.removesuffix(".md") if doc else None,
        },
    }
    if docs:
        params["docs"] = docs
        params["assignment"]["docs"] = list(docs)
    if doc:
        params["doc"] = doc
    if substate == "NO_DIFF_EXPLAIN" and s.last_failure:
        params["evidence"] = dict(s.last_failure)  # FR-11
    return params


def _decide_no_diff_explain(s: State) -> Command | None:
    """Re-dispatch the original author to explain why no diff was produced."""
    if s.doc_dispatched:
        return None  # awaiting the explanation outcome
    if s.stage == "M-TEST":
        role, docs, doc = "shield", list(_M_TEST_CONTEXT_DOCS), None
        objective = "explain why no tests/ diff was produced"
    elif s.stage == "M-IMPL":
        role, docs, doc = "devon", list(_M_IMPL_CONTEXT_DOCS), None
        objective = "explain why no implementation diff was produced"
    elif s.stage == "M-DESIGN":
        role, docs, doc = "archer", list(DESIGN_DOCS), None
        objective = "explain why no design diff was produced"
    else:
        sd = _STAGES.get(s.stage)
        if sd is None:
            return None
        role, docs, doc = sd.drafting_role, [], sd.doc
        objective = f"explain why no {doc} diff was produced"
    return Command(
        kind="dispatch_agent",
        params=_no_diff_dispatch_params(s, "NO_DIFF_EXPLAIN", role, docs, doc, objective),
    )


def _decide_no_diff_review(s: State) -> Command | None:
    """Dispatch the stage's reviewer to judge the no-diff explanation."""
    if s.no_diff_reviewer_dispatched:
        return None  # awaiting the reviewer verdict
    if s.stage == "M-TEST":
        reviewer, docs, doc = "prism", list(_M_TEST_CONTEXT_DOCS), None
    elif s.stage == "M-IMPL":
        reviewer, docs, doc = "prism", list(_M_IMPL_CONTEXT_DOCS), None
    elif s.stage == "M-DESIGN":
        reviewer, docs, doc = "prism", list(DESIGN_DOCS), None
    else:
        sd = _STAGES.get(s.stage)
        if sd is None:
            return None
        reviewer, docs, doc = sd.reviewer, [], sd.doc
    return Command(
        kind="dispatch_agent",
        params=_no_diff_dispatch_params(
            s, "NO_DIFF_REVIEW", reviewer, docs, doc, "review the no-diff explanation"
        ),
    )


def _decide_result_pipeline(s: State) -> Command | None:
    """v0.5 ResultCheckpoint pipeline: validate -> checkpoint -> publish.

    Generic and stage-agnostic. ``active_result`` carries everything the
    executor needs: artifacts to validate, allowed_paths to checkpoint,
    checks to run, and the domain_event to publish. The domain event's
    reducer clears ``active_result`` (or verdict.failed on failure)."""
    ar = s.active_result
    result_id = ar.get("result_id")
    digests = ar.get("digests")
    if not ar.get("validated"):
        return Command(
            kind="validate_result",
            params={
                "artifacts": ar.get("artifacts", []),
                "checks": ar.get("checks", []),
                "base_sha": ar.get("base_sha"),
                "requires_diff": ar.get("requires_diff", False),
                "forbid_diff": ar.get("forbid_diff", False),
                "discussion_only": ar.get("discussion_only", False),
                "verdict": ar.get("verdict"),
                "actor_kind": ar.get("actor_kind"),
                "result_id": result_id,
                "digests": digests,
                "manifest_error": ar.get("manifest_error"),
            },
        )
    if not ar.get("checkpointed"):
        return Command(
            kind="checkpoint_result",
            params={
                "allowed_paths": ar.get("allowed_paths", []),
                "base_sha": ar.get("base_sha"),
                "source": ar.get("source"),
                "stage": ar.get("stage"),
                "requires_diff": ar.get("requires_diff", False),
                "forbid_diff": ar.get("forbid_diff", False),
                "verdict": ar.get("verdict"),
                "commit_label": ar.get("commit_label"),
                "actor_kind": ar.get("actor_kind"),
                "result_id": result_id,
                "digests": digests,
                "no_diff_approved": ar.get("no_diff_approved", False),
            },
        )
    return Command(
        kind="publish_result",
        params={
            "domain_event": ar.get("domain_event", {}),
            "commit_sha": ar.get("commit_sha"),
            "created_commit": ar.get("created_commit", False),
            "source": ar.get("source"),
            "stage": ar.get("stage"),
            "substate": ar.get("substate"),
            "artifacts": ar.get("artifacts", []),
            "verdict": ar.get("verdict"),
            "actor_kind": ar.get("actor_kind"),
            "base_sha": ar.get("base_sha"),
            "result_id": result_id,
        },
    )


def decide(s: State) -> Command | None:
    """Next command to issue, or None to halt (awaiting human / completed)."""
    # v0.5 no_diff peer review: takes priority over the active_result pipeline
    # so the explain->review flow drives dispatch_agent instead of validate.
    if s.substate == "NO_DIFF_EXPLAIN":
        return _decide_no_diff_explain(s)
    if s.substate == "NO_DIFF_REVIEW":
        return _decide_no_diff_review(s)
    # v0.5 ResultCheckpoint pipeline takes absolute priority: when
    # active_result is set, drive validate->checkpoint->publish regardless
    # of status or awaiting. This allows the human pipeline to run under
    # the awaiting gate.
    if s.active_result is not None:
        return _decide_result_pipeline(s)
    if s.status != "active":
        return None
    if s.awaiting:
        return None
    stage, sub = s.stage, s.substate
    if stage == "M-STORY" and s.triage_decision in ("no_go", "park"):
        return _decide_reject(s)
    if s.scope_overflow:
        # FR-20: >30 FRs in spec - roll back to M-STORY, re-scope the story.
        return Command(
            kind="rollback_stage", params={"to_stage": "M-STORY", "reason": "scope_overflow"}
        )
    if stage == "M-TEST":
        return _decide_m_test(s, sub)
    if stage == "M-IMPL":
        return _decide_m_impl(s, sub)
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
        return Command(
            kind="record_approval", params={"actor": s.approval_actor, "digest": s.approval_digest}
        )
    if sub == "ISSUES":
        if not s.issues_created:
            return Command(kind="create_issues", params={"digest": s.approval_digest})
        if s.stage_exited:
            return None  # crash-hole parity with _decide_exit: never re-exit
        # SM-05.6: boundary exit — no document to seal; the executor emits
        # stage.exited + run.completed(terminal_state="boundary").
        return Command(kind="write_frontmatter", params={"stage": stage})
    if sub == "RETURNED":
        return Command(
            kind="rollback_stage", params={"to_stage": s.return_target, "reason": "human_return"}
        )
    return None  # HUMAN_REVIEW or unknown: halt
