"""Cross-module data contracts (interfaces.md §2–§7).

v0.1 keeps `payload`/`params` as plain dicts (interfaces §1 defers the per-event
discriminated union). The event `type` and command `kind` are the closed set
defined in interfaces §3/§4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# interfaces §3 — v0.1 closed event set (domain.action, past tense).
EVENT_TYPES = (
    "story.requested",
    "stage.entered",
    "stage.exited",
    "stage.rolled_back",
    "branch.created",
    "branch.deleted",
    "run.completed",
    "run.interrupted",
    "command.issued",
    "outcome.received",
    "verdict.passed",
    "verdict.failed",
    "story.committed",
    "spec.committed",
    "acceptance.committed",
    "human.triage",
    "human.review",
    "human.retry",
    "sage.verdict",
    "lex.verdict",
    "backlog.recorded",
    "review.round_started",
    # v0.2 scope expansion (IF-003 §10a): M-REQ-APPROVAL + GitHub Issues
    "preview.generated",
    "human.approval",
    "human.return",
    # B32 (#32): forward-recovery channel past a mis-typed stub_gap rollback.
    # human.recover is the CLI-side intent; stage.recovered (payload carries
    # the target stage) re-enters that stage via _on_stage_entered.
    "human.recover",
    "stage.recovered",
    "approval.recorded",
    "issue.created",
    "issues.created",
    # v0.3 scope expansion (flow.md §8.2): M-DESIGN (Archer drafts, Prism reviews)
    "design.committed",
    "prism.verdict",
    # v0.4 scope expansion (flow.md §9): M-TEST (Shield writes tests, Runtime
    # independently collects/runs, Prism reviews the test contract).
    "test.collected",
    "red.validated",
    "test.committed",
    # v0.6 R6/D-41 selection semantics (interfaces §1a/§1j): M-TEST entry
    # captures the pre-WRITE R1 snapshot; gate handlers record selections.
    "test.baseline_captured",
    "test.selected",
    "evidence.reused",
    "evidence.staled",
    "full.executed",
    "ledger.opened",
    "ledger.transitioned",
    # v0.5 ResultCheckpoint pipeline (batch 1: M-STORY/M-SPEC/M-ACC):
    # capture -> validate -> independent checkpoint -> publish result/verdict.
    "result.submitted",
    "result.validated",
    "result.checkpointed",
    # v0.5 ResultCheckpoint pipeline (batch 2: M-DESIGN/M-TEST):
    # test.written is published after Shield WRITE checkpoint (WRITE -> COLLECT).
    "test.written",
    # v0.5 no_diff peer review: when requires_diff fires on an author result
    # with no workspace diff, enter explain->review before failing.
    "no_diff.detected",
    "no_diff.explained",
    "no_diff.reviewed",
    # B2 (run 01KZTHE7): trac start sweeps leaked worktrees; audited event.
    "worktree.swept",
    # B1 (issue #2): per-dispatch writer worktrees; audited lifecycle events.
    "worktree.opened",
    "worktree.closed",
    # v0.6 hotfix (interfaces.md §1a, IF-HOTFIX-002): HOTFIX-TRIAGE entry
    # substate machine events (SM-01) + the anonymous M-TEST release basis.
    "hotfix.requested",
    "triage.prechecked",
    "anchor.validated",
    "human.anchor",
    "increment.declared",
    "baseline.inherited",
    # B38 (#39): GREEN resubmit with no worktree diff and explicit
    # no_change_reason — implementation already in baseline.
    "green.no_change",
    # D-36 浅版 (#43): operator out-of-band commits declared via the
    # `Tracks-OOB:` git trailer are accepted (not agent output, no
    # over-reach, worktree replay never clobbers them). Emitted by the
    # executor's run-loop observation; machine replay is a no-op.
    "oob.accepted",
    # B44 (#46): run-level circuit breaker — rollbacks / per-task failures /
    # cumulative dispatches past their thresholds park the run at
    # awaiting_human/escalation with a loss report instead of grinding all
    # night. human.retry resets the counting window.
    "run.breaker_tripped",
    # v0.5 SM-02 doc-comment-first (IF-DOCGAP-001 / IF-QUARANTINE-001):
    # outcome-level design-document adjudication lifecycle. Emitted by the
    # executor before ordinary validation; machine.py projects the per-record
    # doc-gap state from these (state.doc_gaps).
    "doc_comment.detected",
    "doc_comment.adjudicated",
    "outcome.quarantined",
    "outcome.rejected",
    "outcome.restored",
    "outcome.discarded",
    "outcome.resumed",
    # SM-02 design_gap nested workflow (#62 finding 1): a design_gap
    # adjudication drives an actual nested Archer design-revision dispatch
    # and Prism design review before the origin threads may close and the
    # paused M-TEST/M-IMPL outcome resumes.  These are SM-02-scoped events
    # that project into the doc-gap record - they are NOT design.committed /
    # prism.verdict (those reducers clobber the origin stage state) and
    # carry no Human gate.  WAL issue() dispatches the nested agents.
    "doc_gap.design_dispatched",
    "doc_gap.design_revised",
    "doc_gap.design_failed",
    "doc_gap.design_reviewed",
    # #85: loop 终止的持久记录——B43 drift abort 等 fail-fast 退出只 print
    # 到 stdout，screen 无重定向时证据全丢。executor 在 abort 前落此审计
    # 事件；reducer 无需行为（projection 忽略），replay 是 no-op。
    "loop.aborted",
    # M7 (convergence plan 2026-09-05): installed-distribution isolated
    # bootstrap -- tracks/**
    # drift at a dispatch boundary records a handover (audit only; the
    # state machine does not project it -- the run stays active and the
    # restart watcher takes over).
    "code.drift",
    # v0.7 Phase 0（interfaces §1c/§1d，architecture §1.0.2/§1.1，T-015 组装）：
    # M-DESIGN EXIT 到 M-TEST 入口的 Runtime 前置门 append-only 事件序列，
    # 最终 sealed 或 blocked；投影可从事件流完整重建（AC-NFR0140-01）。
    "phase0.baseline_repaired",
    "phase0.coverage",
    "phase0.guard_hardened",
    "phase0.sealed",
    "phase0.blocked",
    # T-042 (SM-01 five-stage release closure, interfaces §1a rows 1-32):
    # the release-pipeline event face -- candidate identity, verify gates,
    # CI binding, security, preview/decision, publish, milestone terminal,
    # plus the adjacent v0.8 contract faces (envelope parity, failure
    # evidence chain, escape barrier). Appended to the closed set per
    # architecture §338 "EVENT/COMMAND 封闭集追加".
    "candidate.frozen",
    "candidate.stale",
    "local_gate.passed",
    "local_gate.failed",
    "ci.run_observed",
    "security.assessed",
    "release.previewed",
    "release.decided",
    "release.rejected",
    # FR-0277-02: strict release-branch sync products -- prepared (identity
    # recorded before verification), verified (P-bound evidence), failed
    # (fail-closed stop before any preview/decision/irreversible operation).
    "sync_product.prepared",
    "sync_product.verified",
    "sync_product.failed",
    "publish.planned",
    "publish.executed",
    "publish.blocked",
    "publish.failed",
    "reconcile_conflict",
    "milestone.trace_closed",
    "milestone.closed",
    "milestone.sealed",
    "issue.closed",
    "project.closed",
    "refs.cleaned",
    "host_contract.materialized",
    "host_contract.invalid",
    "host_contract.failed",
    # §1a row 29 closed pair: the registration AND its rejection are both
    # members (executor/repair.register_known_issue emits either; the
    # registration rides release_gate preview listing + milestone waiver).
    "known_issue.registered",
    "known_issue.rejected",
    # FR-0286 §5: the park chain's C-class observation -- the disposition is
    # identified (association refs recorded) but the registration lands on
    # the NEXT drive, keeping the escalation park a legal Human escape
    # source until the operator drives again.
    "known_issue.pending",
    "escape.barrier_established",
    "escape.late_outcome",
    "advisory.recorded",
    "review.failed",
    "semantic_attempt_failed",
    "format_error",
    "dispatch.parity",
    "dispatch.rejected",
    "failure.emitted",
    "failure.stored",
    "failure.selected",
    "failure.injected",
    "failure.consumed",
    "failure.acked",
    "failure.invalidated",
    # §1a row 28: the classified in-place repair round opener (FR-0286);
    # emitted by executor/repair.open_repair_round, projected by the
    # machine's re-arm reducer (§1.0.14 B).
    "repair.round_started",
    # B94 drift breaker (deferred anchor legitimancy)
    "drift_breaker",
    # T-042 (FR-0270, IF-VERIFY-004): ISSUES-path event trio — create_issue_verified
    # lands issue.mapped (issue_number + api_verified vocabulary shared with the
    # milestone closers), the real channel rejects FAKE artifacts (fake_rejected,
    # never api_verified), and missing-credential / blocked surfaces route as
    # attention.required (IF-ISSUE-001 needs_attention vocabulary). Consumed by
    # report.py issue surfaces and the CLI known_issues/needs_attention renders.
    "issue.mapped",
    "fake_rejected",
    "attention.required",
)

# interfaces §4 — v0.1 closed command set.
COMMAND_KINDS = (
    "dispatch_agent",
    "validate_document",
    "commit_document",
    "create_branch",
    "delete_branch",
    "write_frontmatter",
    "record_backlog",
    "complete_run",
    "rollback_stage",
    # v0.2 scope expansion (IF-003 §10b): M-REQ-APPROVAL / FR-0200
    "generate_preview",
    "record_approval",
    "create_issues",
    # v0.4 scope expansion (IF-004 §1b): M-TEST executor handlers
    "collect_tests",
    "run_tests",
    "check_trace",
    "commit_tests",
    # v0.6 R6/D-41 (interfaces §1j): M-TEST entry pre-WRITE R1 snapshot capture.
    "capture_baseline",
    # v0.5 ResultCheckpoint pipeline (batch 1): validate -> checkpoint -> publish.
    "validate_result",
    "checkpoint_result",
    "publish_result",
    # v0.6 hotfix (interfaces.md §1b, IF-HOTFIX-002): commands produced by
    # kernel/_decide_hotfix_triage and executed by executor handlers.
    "precheck_hotfix",
    "validate_anchor",
    "complete_hotfix_entry",
    # v0.7 Phase 0 pre-gate command (interfaces §1c/§4, T-015 组装): issued by
    # the kernel entry guard through the before_mtest capability while
    # phase0_status != SEALED; executed by the Executor phase0 handler.
    "phase0_validate",
    # M1-S1 (convergence plan 2026-09-05): mechanical oscillation signature —
    # consecutive GREEN_GATE failures swapped anchor outcomes; audit event
    # (the state change rides the paired verdict.failed contract_conflict).
    "oscillation.detected",
    # T-042 (SM-01 five-stage release closure, architecture §338 "EVENT/
    # COMMAND 封闭集追加"): the release-chain command kinds whose executor
    # handlers consume the kernel release deciders' routing.
    "freeze_candidate",
    "judge_full_f_reuse",
    "run_local_gates",
    "observe_ci_runs",
    "assess_security",
    "execute_publish",
    "close_milestone",
    "register_known_issue",
    # IF-HOSTCONTRACT-002 / AC-FR0281-01: Archer's M-DESIGN completion (and
    # ``trac init``) materialize the versioned host contract -- the event
    # half is host_contract.materialized / host_contract.invalid.
    "materialize_host_contract",
)


@dataclass(frozen=True)
class EventEnvelope:
    seq: int
    ts: str
    run_id: str
    version: str
    type: str
    schema_version: int
    command_id: str | None
    task_id: str | None
    payload: dict


@dataclass(frozen=True)
class Command:
    kind: str
    params: dict = field(default_factory=dict)
    command_id: str | None = None  # assigned by store/executor at issue time
