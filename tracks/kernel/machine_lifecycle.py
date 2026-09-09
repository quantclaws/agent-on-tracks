"""Run & stage lifecycle reducers: story.requested, stage.entered/exited/
rolled_back, review.round_started, backlog.recorded, run.completed,
run.breaker_tripped, branch.created/deleted, repair.round_started.

Extracted from ``machine.py`` for module-size compliance (C0302).

No circular import: imports only ``events``, ``m_test`` helpers and
``stage_registry`` at runtime (the ``release`` stage seam resolves lazily
inside ``_on_stage_entered``); ``State`` is imported under
``TYPE_CHECKING`` only (duck-typed at runtime). ``machine.py`` imports
the reducers from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import EventEnvelope
from .m_test import _reset_doc, _reset_review
from .stage_registry import _STAGES, _rolled_back_entry_substate

if TYPE_CHECKING:
    from .machine import State


def _on_story_requested(s: State, p: dict, ev: EventEnvelope) -> None:
    s.run_id, s.version = ev.run_id, ev.version


def _reset_m_impl_cycle(s: State) -> None:
    """flow.md §10: fresh M-IMPL cycle — reset every per-stage field.

    B83: includes the generation bookkeeping (taskgraph_generation,
    retained_completed_task_ids) so a re-entered residency starts from a
    clean projection."""
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
    s.diagnose_report = None
    s.taskgraph_digest = None
    s.taskgraph_path = None
    s.task_refs = []
    s.taskgraph_generation = 0
    s.retained_completed_task_ids = []
    s.current_task_metadata = None
    s.current_manifest = None


def _on_stage_entered(s: State, p: dict, ev: EventEnvelope) -> None:
    if not s.version and ev.version:
        # T-015: every event envelope carries the run's version identity
        # (§1a); runs without a story.requested projection (house-style
        # seeded fixtures, replayed partial streams) still surface it here so
        # version-keyed seams -- the v0.7 Phase 0 pre-gate -- can resolve.
        # Never overrides story.requested (unconditional in its own reducer)
        # nor hotfix identities: executor-emitted envelopes carry the
        # already-resolved run identity (`_emit` stamps `self.version`).
        s.version = ev.version
    s.stage = p["stage"]
    sd = _STAGES.get(s.stage)
    if sd is None:
        # SM-01 five-stage release pipeline (architecture §338 "kernel/
        # release 注册到 machine"): the release StageDefs live in
        # kernel/release (which imports machine at module scope), so the
        # entry substate resolves through the same lazy seam as decide().
        from .release import release_stage_defs

        for rel in release_stage_defs():
            if rel.stage == s.stage:
                sd = rel
                break
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
        s.diagnose_report = None
        # D-41: a fresh M-TEST cycle re-captures the R1 snapshot (the prior
        # capture belongs to the previous cycle's event prefix) and starts
        # with no selection identity.
        s.baseline_captured = False
        s.active_selection_id = None
    if s.stage == "M-IMPL":
        # flow.md §10: fresh M-IMPL cycle. Reset all per-stage fields.
        _reset_m_impl_cycle(s)
    if s.stage == "M-HOTFIX-TRIAGE":
        s.substate = "PRECHECK"
        s.hotfix_target_version = None
        s.hotfix_anchor_acs = None
        s.hotfix_precheck_passed = False
        s.hotfix_anchor_validated = False
        s.hotfix_branch = None
        s.baseline_inherited = False


def _on_stage_exited(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage_exited = True


def _on_stage_rolled_back(s: State, p: dict, ev: EventEnvelope) -> None:
    s.stage = p["to_stage"]
    # IF-RELEASE-003 (face C): re-entry routes via the target StageDef's
    # initial_substate (M-TEST=DISPATCH, M-IMPL=BASELINE) — no fossilized
    # DRAFT literal. Author stages keep DRAFT; the M-HOTFIX-TRIAGE branch
    # below still overrides to SAGE_TRIAGE.
    s.substate = _rolled_back_entry_substate(p["to_stage"])
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
    if s.stage == "M-HOTFIX-TRIAGE":
        # SM-01.6 second entry source (FR-0243-03): M-DESIGN Prism overturned
        # the anchor -> roll back to SAGE_TRIAGE and redispatch Sage with the
        # SAGE_TRIAGE <=3 budget (the M-DESIGN redispatch budget is NOT
        # consumed).  The run identity fields are preserved; progress/reset
        # like a fresh entry. The SAGE_TRIAGE budget is kept (current_attempt
        # was already zeroed above) so a previous exhaustion still parks
        # immediately at the 3rd failure.
        s.substate = "SAGE_TRIAGE"
        s.hotfix_anchor_acs = None
        s.hotfix_precheck_passed = True
        s.hotfix_anchor_validated = False
        s.hotfix_branch = None
        s.baseline_inherited = False
        s.awaiting = None
        s.status = "active"


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


def _on_run_completed(s: State, p: dict, ev: EventEnvelope) -> None:
    s.status = "completed"
    s.awaiting = None
    s.terminal_state = p.get("terminal_state")


def _on_run_breaker_tripped(s: State, p: dict, ev: EventEnvelope) -> None:
    """B44（#46）：run 级熔断——停车等待人工，损耗报告走 last_failure
    （FR-11 通道：`trac status` 显示 reason；`trac retry` 清 gate 且
    executor 计数窗口随之重置）。不消耗 attempt、不改 substate。"""
    s.status = "awaiting_human"
    s.awaiting = "escalation"
    s.last_failure = {
        "check": "run_breaker",
        "reason": p.get("condition"),
        "evidence": p.get("report"),
    }


def _on_branch_created(s: State, p: dict, ev: EventEnvelope) -> None:
    s.branch_created = True


def _on_branch_deleted(s: State, p: dict, ev: EventEnvelope) -> None:
    s.branch_deleted = True


def _on_repair_round_started(s: State, p: dict, ev: EventEnvelope) -> None:
    """§1.0.14 B: an opened in-place repair round re-arms the parked walk.

    The parked run (M-IMPL/DIAGNOSE awaiting=escalation) records its repair
    disposition at the park evidence chain; the round's classification
    routes the NEXT dispatch through the repair channel, so the escalation
    gate lifts and the walk continues in place (budget bounded -- the chain
    re-parks when the budget exhausts). Never a stage.rolled_back
    (AC-FR0286-01).

    Owner routing (Prism P2, 2026-09-06): only behaviour rounds (owner
    Devon, discipline red_first) re-enter the RGR slot. A cve round
    (owner Archer, advisory) lifts the gate but keeps DIAGNOSE -- decide()
    routes the advisory dispatch, not a Devon RED."""
    if s.status == "awaiting_human" and s.awaiting == "escalation":
        s.status = "active"
        s.awaiting = None
    if s.stage == "M-IMPL" and s.substate == "DIAGNOSE":
        route = p.get("repair_route") or {}
        owner = str(route.get("owner") or "").lower()
        if owner in ("", "devon"):
            # The repair route re-enters the implementation cycle through
            # the task the fix disposition owns (red_first: the RGR slot of
            # the failed task); current_task_id is preserved for the lease.
            s.substate = "RED"
