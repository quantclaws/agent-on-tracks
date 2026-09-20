"""Pure state machine (NFR-02): `project(events) -> State` fold and
`decide(state) -> Command | None` next-step selection.

No clock, no filesystem, no I/O, no env reads. All non-determinism enters only
as events. `decide` returns a Command with `command_id=None`; the store assigns
the ULID at issue time so this stays pure.

Composition (v0.8 kernel-quality split): this module keeps the `State`
projection type, the `_APPLY` reducer registry with `apply`/`project`, and the
`decide` routing core. The stage table lives in `stage_registry`; the reducers
live in `machine_lifecycle` / `machine_outcomes` / `machine_verdicts` /
`machine_human_gates` / `machine_results` / `machine_doc_gaps`; the
stage-specific control flow stays in `m_impl` / `m_test` / `hotfix` / `phase0` /
`release`. Every moved name is re-exported below, so the pre-split import
surface of this module is unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from . import state as kernel_state
from .events import Command, EventEnvelope
from .hotfix import (
    _decide_hotfix_triage,
    _on_anchor_validated,
    _on_baseline_inherited,
    _on_hotfix_requested,
    _on_human_anchor,
    _on_increment_declared,
    _on_triage_prechecked,
)
from .m_impl import (
    _M_IMPL_CONTEXT_DOCS as _M_IMPL_CONTEXT_DOCS,
)
from .m_impl import (
    _M_IMPL_CRITERIA_PACK as _M_IMPL_CRITERIA_PACK,
)
from .m_impl import (
    _M_IMPL_REVIEW_SUBSTATES as _M_IMPL_REVIEW_SUBSTATES,
)
from .m_impl import (
    _decide_m_impl,
    _on_baseline_frozen,
    _on_full_executed,
    _on_green_committed,
    _on_green_no_change,
    _on_ledger_opened,
    _on_ledger_transitioned,
    _on_red_checkpointed,
    _on_refactor_committed,
    _on_refactor_no_change,
    _on_task_completed,
    _on_task_started,
    _on_taskgraph_committed,
    _on_writelock_granted,
    _on_writelock_released,
)
from .m_impl import (
    _on_m_impl_outcome_done as _on_m_impl_outcome_done,
)
from .m_impl import (
    _on_m_impl_prism_verdict as _on_m_impl_prism_verdict,
)
from .m_impl import (
    _on_m_impl_verdict_failed as _on_m_impl_verdict_failed,
)
from .m_impl import (
    _on_m_impl_verdict_passed as _on_m_impl_verdict_passed,
)
from .m_impl import (
    _set_m_impl_dispatch_flags as _set_m_impl_dispatch_flags,
)
from .m_test import (
    _CRITERIA_PACK as _CRITERIA_PACK,
)
from .m_test import (
    _M_TEST_CONTEXT_DOCS as _M_TEST_CONTEXT_DOCS,
)
from .m_test import (
    _consume_attempt as _consume_attempt,
)
from .m_test import (
    _decide_m_test,
    _on_red_validated,
    _on_test_baseline_captured,
    _on_test_collected,
    _on_test_committed,
    _on_test_selected,
    _on_test_written,
)
from .m_test import (
    _m_test_diagnose_route as _m_test_diagnose_route,
)
from .m_test import (
    _m_test_exit_route as _m_test_exit_route,
)
from .m_test import (
    _m_test_prism_dispatch as _m_test_prism_dispatch,
)
from .m_test import (
    _m_test_shield_dispatch as _m_test_shield_dispatch,
)
from .m_test import (
    _on_m_test_outcome_done as _on_m_test_outcome_done,
)
from .m_test import (
    _on_m_test_verdict_failed as _on_m_test_verdict_failed,
)
from .m_test import (
    _reset_doc as _reset_doc,
)
from .m_test import (
    _reset_review as _reset_review,
)
from .machine_decide import (
    _decide_design_draft as _decide_design_draft,
)
from .machine_decide import (
    _decide_design_exit as _decide_design_exit,
)
from .machine_decide import (
    _decide_draft as _decide_draft,
)
from .machine_decide import (
    _decide_exit as _decide_exit,
)
from .machine_decide import (
    _decide_exit_gate,
    _decide_no_diff_explain,
    _decide_no_diff_review,
    _decide_pipeline,
    _decide_reject,
    _decide_result_pipeline,
    _decide_review,
    _decide_triage,
)
from .machine_decide import (
    _dispatch as _dispatch,
)
from .machine_decide import (
    _no_diff_dispatch_params as _no_diff_dispatch_params,
)
from .machine_doc_gaps import (
    _new_doc_gap_record as _new_doc_gap_record,
)
from .machine_doc_gaps import (
    _on_doc_comment_adjudicated,
    _on_doc_comment_detected,
    _on_doc_gap_design_dispatched,
    _on_doc_gap_design_failed,
    _on_doc_gap_design_reviewed,
    _on_doc_gap_design_revised,
    _on_outcome_quarantined,
    _on_outcome_resolve,
    _on_outcome_resumed,
)
from .machine_human_gates import (
    _on_approval_recorded,
    _on_human_approval,
    _on_human_recover,
    _on_human_retry,
    _on_human_return,
    _on_human_review,
    _on_human_triage,
    _on_issues_created,
    _on_preview_generated,
)
from .machine_lifecycle import (
    _on_backlog_recorded,
    _on_branch_created,
    _on_branch_deleted,
    _on_known_issue_registered,
    _on_repair_round_started,
    _on_review_round_started,
    _on_run_breaker_tripped,
    _on_run_completed,
    _on_stage_entered,
    _on_stage_exited,
    _on_stage_rolled_back,
    _on_story_requested,
)
from .machine_lifecycle import (
    _reset_m_impl_cycle as _reset_m_impl_cycle,
)
from .machine_outcomes import (
    _FORMAT_FAILURE_CLASSES as _FORMAT_FAILURE_CLASSES,
)
from .machine_outcomes import (
    _FORMAT_RETRY_LIMIT as _FORMAT_RETRY_LIMIT,
)
from .machine_outcomes import (
    _FORMAT_VERDICT_ROUTE as _FORMAT_VERDICT_ROUTE,
)
from .machine_outcomes import (
    _INFRA_FAILURE_CLASSES as _INFRA_FAILURE_CLASSES,
)
from .machine_outcomes import (
    _handle_failed_outcome as _handle_failed_outcome,
)
from .machine_outcomes import (
    _handle_format_failure as _handle_format_failure,
)
from .machine_outcomes import (
    _handle_hotfix_failed_outcome as _handle_hotfix_failed_outcome,
)
from .machine_outcomes import (
    _handle_infra_failure as _handle_infra_failure,
)
from .machine_outcomes import (
    _handle_verdict_format_failure as _handle_verdict_format_failure,
)
from .machine_outcomes import (
    _infra_retry_limit as _infra_retry_limit,
)
from .machine_outcomes import (
    _is_format_verdict as _is_format_verdict,
)
from .machine_outcomes import (
    _is_infra_failure as _is_infra_failure,
)
from .machine_outcomes import (
    _on_command_issued,
    _on_outcome_received,
)
from .machine_outcomes import (
    _reset_m_test_dispatch_flag as _reset_m_test_dispatch_flag,
)
from .machine_outcomes import (
    _route_failed_outcome_by_stage as _route_failed_outcome_by_stage,
)
from .machine_outcomes import (
    _track_hotfix_command_progress as _track_hotfix_command_progress,
)
from .machine_outcomes import (
    _track_non_dispatch_milestone as _track_non_dispatch_milestone,
)
from .machine_results import (
    _on_no_diff_detected,
    _on_no_diff_explained,
    _on_no_diff_reviewed,
    _on_result_checkpointed,
    _on_result_submitted,
    _on_result_validated,
)
from .machine_verdicts import (
    _escalate_or_continue as _escalate_or_continue,
)
from .machine_verdicts import (
    _on_design_committed,
    _on_doc_committed,
    _on_prism_verdict,
    _on_reviewer_verdict,
    _on_verdict_failed,
    _on_verdict_passed,
)
from .machine_verdicts import (
    _on_design_verdict_failed as _on_design_verdict_failed,
)
from .machine_verdicts import (
    _review_last_failure as _review_last_failure,
)
from .phase0 import on_phase0_baseline_repaired, on_phase0_blocked
from .stage_registry import (
    _AUTHOR_REENTRY_DRAFT as _AUTHOR_REENTRY_DRAFT,
)
from .stage_registry import (
    _COMMITTED_EVENT as _COMMITTED_EVENT,
)
from .stage_registry import (
    _REVIEW_SUBSTATE as _REVIEW_SUBSTATE,
)
from .stage_registry import (
    _STAGES as _STAGES,
)
from .stage_registry import (
    _VERDICT_OWNERS as _VERDICT_OWNERS,
)
from .stage_registry import (
    DESIGN_DOCS as DESIGN_DOCS,
)
from .stage_registry import (
    StageDef as StageDef,
)
from .stage_registry import (
    _rolled_back_entry_substate as _rolled_back_entry_substate,
)
from .stage_registry import (
    _uncommit as _uncommit,
)
from .stage_registry import (
    canonical_stage_order as canonical_stage_order,
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
    # B32 (#32): forward-recovery target/why past a mis-typed stub_gap rollback.
    # human.recover records these; decide(RECOVER_PENDING) issues recover_stage.
    recover_target: str | None = None
    recover_reason: str | None = None
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
    # v0.7 Phase 0 (interfaces §1c/§1d, architecture §1.0.2, T-015 组装):
    # projection of the phase0.* append-only events; None = classic pre-v0.7
    # runs (no Phase 0 pre-gate). Rebuilt identically from the event stream
    # after a projection drop (AC-NFR0140-01).
    phase0_status: str | None = None  # UNSEALED|PHASE0_VALIDATING|SEALED|BLOCKED
    phase0_seal_id: str | None = None
    phase0_blocked_reason: str | None = None
    # v0.6 R6/D-41 selection semantics (interfaces §1c/§1j): the pre-WRITE R1
    # snapshot gate and the latest stamped selection identity.
    baseline_captured: bool = False  # test.baseline_captured(passed) persisted
    active_selection_id: str | None = None  # latest test.selected identity
    full_chain_round: str | None = None  # FULL_1 | FULL_F | fallback_full
    ledger_open: int = 0  # OPEN|CLASSIFIED|FIXED|STALE entries
    ledger_rebuilt: bool = False  # ledger WAL has been projected during replay
    ledger_entries: dict = field(default_factory=dict)  # identity -> replay metadata/state
    # Review pin (FR-0244-04): the explicit persisted unit-only hotfix
    # increment fact -- the ONLY key that releases an empty-R2 M-TEST
    # selection. Projected by the increment.declared reducer from a
    # shield=empty event with nonempty unit rows.
    increment_declared: dict | None = None
    # Full DIAGNOSE verdict details for the fixer dispatch. Carried on State,
    # NOT last_failure: last_failure is dropped by `trac retry
    # --clear-evidence` and overwritten by every failed fixer attempt
    # (FR-0210), which previously left the fixer re-deriving Prism's whole
    # analysis from scratch (run 01KZTHE7 T-008/T-017, 2026-08-15/16).
    diagnose_report: dict | None = None
    # Consecutive infrastructure failures (opencode process killed, provider
    # unreachable, missing binary, timeout) digested by the runtime itself -
    # they never consume the agent attempt budget and never pollute
    # last_failure (user stance: infra errors are not agent failures and must
    # not be passed to the next agent). Bounded: >=_INFRA_RETRY_LIMIT in a
    # row escalates to awaiting_human instead of storming a degraded gateway.
    infra_failure_streak: int = 0
    # Consecutive output-contract format failures (final reply JSON failed a
    # mechanical manifest check, e.g. review_summary over the 140-char cap).
    # Digested like infra: never consumes the semantic attempt budget (the
    # assignment's semantics were never evaluated -- only the reply shape
    # was wrong). Bounded: >=_FORMAT_RETRY_LIMIT in a row escalates to
    # awaiting_human instead of looping (operator finding 2026-08-24: Prism
    # burned the whole shared M-TEST budget on review_summary>140 chars
    # while the discipline was already in its prompt -- mechanical
    # constraints must be enforced programmatically, not by prompts).
    format_failure_streak: int = 0
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
    taskgraph_generation: int = 0
    retained_completed_task_ids: list = field(default_factory=list)
    # FR-0286 §5 waiver: task ids closed by a registered Known Issue -- an
    # independent terminal state (never folded into task.completed, PROVEN or
    # FIXED). A waived task is done for selection/exit purposes; the release
    # trace marks its ACs waived instead of passed.
    waived_task_ids: list = field(default_factory=list)
    current_task_metadata: dict | None = None
    current_manifest: dict | None = None
    # SM-02 doc-gap adjudication projection (IF-DOCGAP-001 / IF-QUARANTINE-001):
    # per-record waiting/route/quarantine/resume state rebuilt from the §1m
    # doc-gap event closed set. Never an ordinary success terminal.
    doc_gaps: dict = field(default_factory=dict)
    # v0.6 hotfix (interfaces.md §1c, IF-HOTFIX-002): HOTFIX-TRIAGE entry
    # automaton projection fields. Safe defaults keep pre-v0.6 replays
    # unchanged. Run identity (issue/scenario) is set by hotfix.requested;
    # the rest track per-substage progress through PRECHECK/SAGE_TRIAGE/
    # AWAIT_HUMAN toward the ANCHORED terminal.
    hotfix_issue: int | None = None
    hotfix_scenario: str | None = None
    hotfix_target_version: str | None = None
    hotfix_anchor_acs: list | None = None
    hotfix_precheck_passed: bool = False
    hotfix_anchor_validated: bool = False
    hotfix_branch: str | None = None
    baseline_inherited: bool = False
    # v0.8 release pipeline (SM-01, kernel/release reducers): the five-stage
    # projection. Safe defaults keep pre-v0.8 replays unchanged. The candidate
    # SHA is the single primary identity every release event carries
    # (NFR-0143); a NEW candidate.frozen resets the whole downstream evidence
    # projection (SM-01.20 full re-walk) while a same-SHA re-freeze is an
    # idempotent no-op (SM-01.17).
    candidate_sha: str | None = None
    candidate_clean: bool = False
    candidate_stale: bool = False
    full_reuse: str | None = None  # "full_f" while the reuse evidence binds
    reuse_identity_basis: list | None = None
    local_gates: dict = field(default_factory=dict)  # kind -> gate projection
    ci_status: str | None = None  # bound|mismatch|missing|stale|needs_attention
    security_status: str | None = None  # passed|failed|unknown
    security_policy_digest: str | None = None
    preview_digest: str | None = None
    release_decision: str | None = None  # release|delay|return
    release_decision_digest: str | None = None
    publish_status: str | None = None  # planned|executing|reconciled_skip|done|blocked
    # SM-01.15 batch-completion projection: the approved preview's unique
    # operation count and the idempotency keys already terminal
    # (done/reconciled_skip). M-PUBLISH only exits once every planned
    # operation is terminal -- a partial batch stays parked (blocked) and
    # never hands to close_milestone.
    publish_expected: int = 0
    publish_done_keys: list[str] = field(default_factory=list)
    # True from the first ``publish.planned`` of the active batch: the WAL
    # for an in-flight batch is preserved as ``pending`` so the next drive
    # replays the same execute_publish command (reconcile) instead of
    # re-planning it under a new idempotency identity.
    publish_started: bool = False
    milestone_status: str | None = None  # closing|sealed|released|retry_tail


def _release_apply(name: str):
    """Lazy release-kernel reducer binding (SM-01, architecture §338).

    ``release.py`` imports machine at module scope (``StageDef``/``State``),
    so the ``_APPLY`` registration resolves the kernel module per call --
    the same lazy seam discipline as ``decide()``'s
    ``decide_release_stage`` import. Python's import cache makes the
    per-call resolution a dict lookup.
    """

    def _apply(s: State, p: dict, ev: EventEnvelope) -> None:
        from . import release

        getattr(release, name)(s, p, ev)

    return _apply


# -- event reducers: one small handler per event type, looked up by `apply`.
# Each is `(state, payload, envelope) -> None` and mutates state in place.


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
    "human.retry": _on_human_retry,
    "review.round_started": _on_review_round_started,
    "backlog.recorded": _on_backlog_recorded,
    "run.completed": _on_run_completed,
    "run.breaker_tripped": _on_run_breaker_tripped,
    "branch.created": _on_branch_created,
    "branch.deleted": _on_branch_deleted,
    # M-REQ-APPROVAL (SM-05). issue.created has no reducer: per-item progress
    # is reconciled by the executor from the event log (D-06), not from State.
    "preview.generated": _on_preview_generated,
    "human.approval": _on_human_approval,
    "human.return": _on_human_return,
    "human.recover": _on_human_recover,
    "stage.recovered": _on_stage_entered,  # re-entry reuses the fresh-cycle reset
    "approval.recorded": _on_approval_recorded,
    "issues.created": _on_issues_created,
    # v0.4 M-TEST (flow.md §9 / SM-01)
    "test.collected": _on_test_collected,
    "red.validated": _on_red_validated,
    "test.committed": _on_test_committed,
    # v0.6 R6/D-41 selection semantics (interfaces §1a/§1j)
    "test.baseline_captured": _on_test_baseline_captured,
    "test.selected": _on_test_selected,
    "full.executed": _on_full_executed,
    "ledger.opened": _on_ledger_opened,
    "ledger.transitioned": _on_ledger_transitioned,
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
    "green.no_change": _on_green_no_change,
    "refactor.committed": _on_refactor_committed,
    "refactor.no_change": _on_refactor_no_change,
    "task.completed": _on_task_completed,
    # v0.5 SM-02 doc-gap adjudication (IF-DOCGAP-001 / IF-QUARANTINE-001)
    "doc_comment.detected": _on_doc_comment_detected,
    "outcome.quarantined": _on_outcome_quarantined,
    "doc_comment.adjudicated": _on_doc_comment_adjudicated,
    "outcome.restored": _on_outcome_resolve,
    "outcome.discarded": _on_outcome_resolve,
    "outcome.resumed": _on_outcome_resumed,
    # SM-02 design_gap nested workflow (#62 finding 1)
    "doc_gap.design_dispatched": _on_doc_gap_design_dispatched,
    "doc_gap.design_revised": _on_doc_gap_design_revised,
    "doc_gap.design_failed": _on_doc_gap_design_failed,
    "doc_gap.design_reviewed": _on_doc_gap_design_reviewed,
    # v0.6 hotfix (interfaces.md §1a, IF-HOTFIX-002): HOTFIX-TRIAGE entry
    # automaton events (SPEC-006 SM-01). Reducer signatures are frozen as
    # (State, payload) by the scaffold; wrap to the (State, payload, envelope)
    # dispatch shape used by apply().
    "hotfix.requested": lambda s, p, ev: _on_hotfix_requested(s, p),
    "triage.prechecked": lambda s, p, ev: _on_triage_prechecked(s, p),
    "anchor.validated": lambda s, p, ev: _on_anchor_validated(s, p),
    "human.anchor": lambda s, p, ev: _on_human_anchor(s, p),
    "increment.declared": lambda s, p, ev: _on_increment_declared(s, p),
    "baseline.inherited": lambda s, p, ev: _on_baseline_inherited(s, p),
    # v0.7 Phase 0 projection (T-015 组装): baseline_repaired/blocked delegate
    # to the T-004 kernel.phase0 handlers (frozen (State, payload, envelope)
    # shape); coverage/guard_hardened/sealed project the SM-01 progression
    # through kernel.state (pure, AC-NFR0140-01 rebuild invariant).
    "phase0.baseline_repaired": on_phase0_baseline_repaired,
    "phase0.coverage": kernel_state.on_phase0_coverage,
    "phase0.guard_hardened": kernel_state.on_phase0_guard_hardened,
    "phase0.sealed": kernel_state.on_phase0_sealed,
    "phase0.blocked": on_phase0_blocked,
    # v0.8 release pipeline (SM-01, kernel/release reducers -- architecture
    # §338 "kernel/release 注册到 machine"): the five-stage projection over
    # the candidate-bound release event face (NFR-0143 same-identity chain).
    "candidate.frozen": _release_apply("on_candidate_frozen"),
    "candidate.stale": _release_apply("on_candidate_stale"),
    "evidence.reused": _release_apply("on_evidence_reused_full_f"),
    "local_gate.passed": _release_apply("on_local_gate_result"),
    "local_gate.failed": _release_apply("on_local_gate_result"),
    "ci.run_observed": _release_apply("on_ci_run_observed"),
    "security.assessed": _release_apply("on_security_assessed"),
    "release.previewed": _release_apply("on_release_previewed"),
    "release.decided": _release_apply("on_release_decided"),
    "publish.planned": _release_apply("on_publish_events"),
    "publish.executed": _release_apply("on_publish_events"),
    "publish.blocked": _release_apply("on_publish_events"),
    "publish.failed": _release_apply("on_publish_events"),
    "milestone.trace_closed": _release_apply("on_milestone_events"),
    "milestone.closed": _release_apply("on_milestone_events"),
    "milestone.sealed": _release_apply("on_milestone_events"),
    "issue.closed": _release_apply("on_milestone_events"),
    "project.closed": _release_apply("on_milestone_events"),
    "refs.cleaned": _release_apply("on_milestone_events"),
    # §1.0.14 B: an opened in-place repair round re-arms the parked walk --
    # the repair disposition owns the next dispatch (Devon red_first), so
    # the escalation gate lifts and decide() routes the repair attempt.
    "repair.round_started": _on_repair_round_started,
    # FR-0286 §5 C-class waiver: the registered Known Issue lifts the
    # escalation gate and closes the failed task as waived (independent
    # terminal; never PROVEN/FIXED) so the task graph can reach M-IMPL EXIT.
    "known_issue.registered": _on_known_issue_registered,
}


# B40 pending-WAL hazard: these mid-dispatch AUDIT events are emitted while
# the dispatched command is still executing (command.issued already landed,
# outcome.received has not). They are tagged with the dispatch's own
# command_id and must NOT resolve its write-ahead pending record -- clearing
# it there strands the in-flight command: recovery sees no pending, never
# re-issues, and the run silently stalls (live 01M284JK: SIGKILL during a
# hung scribe DRAFT left dispatch.parity as the last event). Events that
# TERMINAL a dispatch (outcome.received, dispatch.rejected, format_error)
# keep the legacy clear-any-event semantics.
_PENDING_PRESERVING_AUDIT = frozenset(
    {"dispatch.parity", "failure.injected", "worktree.opened", "worktree.closed"}
)

# SM-01.15 publish-batch WAL: an execute_publish command is not resolved by
# its own per-operation events. Once the batch started (publish.planned), the
# pending write-ahead record must survive planned/executed/failed events so
# the next drive's D-13 recovery replays the SAME command_id -- the remote
# then reconciles already-done operations (reconciled_skip) and continues the
# unfinished ones. A zero-effect preflight failure (no planned record) keeps
# the legacy clear-on-failure semantics: the decider stays parked and a fix
# requires a new preview/decision.
_PENDING_PRESERVING_PUBLISH = frozenset(
    {
        "publish.planned",
        "publish.executed",
        "publish.failed",
        "publish.blocked",
        "reconcile_conflict",
    }
)


def _preserves_publish_pending(s: State, ev: EventEnvelope) -> bool:
    return (
        ev.type in _PENDING_PRESERVING_PUBLISH
        and s.pending is not None
        and s.pending.get("kind") == "execute_publish"
        and ev.command_id == s.pending.get("command_id")
        and (ev.type == "publish.planned" or s.publish_started)
    )


def apply(s: State, ev: EventEnvelope) -> State:
    if ev.type != "command.issued":
        preserves = (
            ev.type in _PENDING_PRESERVING_AUDIT
            and s.pending is not None
            and ev.command_id is not None
            and ev.command_id == s.pending.get("command_id")
        ) or _preserves_publish_pending(s, ev)
        if not preserves:
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
    return _decide_stage_route(stage, sub, s)


def _decide_m_design_diagnose(s: State) -> Command | None:
    """M-DESIGN DIAGNOSE routing (FR-0243-03): Prism overturned the hotfix
    anchor -> roll back to M-HOTFIX-TRIAGE/SAGE_TRIAGE without consuming the
    M-DESIGN redispatch budget (upstream product defect)."""
    if s.diagnose_classification == "anchor_overturned":
        return Command(
            kind="rollback_stage",
            params={"to_stage": "M-HOTFIX-TRIAGE", "reason": "anchor_overturned"},
        )
    return None


def _resolve_before_mtest(version: str | None):
    """Resolve ``before_mtest`` on the generic capability seam (§1.0.9).

    Lazy import: the seam lives in the executor package whose composition
    root registers the v0.7 extension at import time; importing it at module
    scope here would cycle machine -> executor -> machine. A registered
    extension that lacks the capability blocks fail-closed -- the
    ``CapabilityBlockedError`` propagates to the run loop (never a silent
    classic-behaviour fallback, interfaces §1h).
    """
    from tracks.executor.version_extensions import resolve_capability

    return resolve_capability(version or "", "before_mtest")


def _phase0_m_test_gate(s: State) -> tuple[bool, Command | None]:
    """v0.7 Phase 0 pre-gate at M-TEST entry (architecture §1.0.2/§1.1).

    Returns ``(park, command)``: ``park`` halts decide() (a BLOCKED Phase 0
    waits for Human repo repair -- §1c: no auto phase0_validate re-issue),
    ``command`` is the phase0_validate pre-gate issued while Phase 0 is not
    sealed, and ``(False, None)`` keeps the classic M-TEST routing (SEALED,
    or a version whose extension selects no Phase 0 callbacks).
    """
    status = s.phase0_status
    if status == "SEALED":
        return False, None
    before_mtest = _resolve_before_mtest(s.version)
    if before_mtest is None:
        return False, None
    if status == "BLOCKED":
        return True, None
    commands = [cmd for cmd in (before_mtest(s) or ()) if cmd is not None]
    return False, (commands[0] if commands else None)


def _decide_stage_route(stage: str, sub: str, s: State) -> Command | None:
    """Stage dispatch routing after the common preamble checks in ``decide()``.
    Extracted to keep ``decide()`` cognitive complexity ≤15."""
    from .release import RELEASE_STAGES, decide_release_stage

    if stage in RELEASE_STAGES:
        # v0.8 release pipeline (SM-01): the five release stages route
        # through the release kernel's decider (classified repair-route
        # dominance included). Checked first so M-RELEASE's own gate
        # substates (AWAITING_RELEASE/DELAYED/RETURNED) keep their release
        # semantics instead of falling into the generic approval routing.
        # Lazy import: release.py imports machine at module scope
        # (StageDef/State) — same seam discipline as release_stage_defs.
        return decide_release_stage(stage, sub, s)
    if stage == "M-HOTFIX-TRIAGE":
        return _decide_hotfix_triage(s, sub)
    if stage == "M-TEST":
        park, pre_gate = _phase0_m_test_gate(s)
        if park:
            return None
        if pre_gate is not None:
            return pre_gate
        return _decide_m_test(s, sub)
    if stage == "M-IMPL":
        return _decide_m_impl(s, sub)
    if stage == "M-DESIGN" and sub == "DIAGNOSE":
        return _decide_m_design_diagnose(s)
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
    if sub == "RECOVER_PENDING":
        # B32 (#32): forward recovery -- re-enter the recorded target stage
        # (v0.6: M-IMPL) instead of rolling back. Params mirror the
        # human.recover payload so the executor never re-derives intent.
        return Command(
            kind="recover_stage",
            params={"to_stage": s.recover_target, "reason": s.recover_reason},
        )
    return None  # HUMAN_REVIEW or unknown: halt
