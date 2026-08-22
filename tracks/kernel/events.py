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
    # v0.5 ResultCheckpoint pipeline (batch 1): validate -> checkpoint -> publish.
    "validate_result",
    "checkpoint_result",
    "publish_result",
    # v0.6 hotfix (interfaces.md §1b, IF-HOTFIX-002): commands produced by
    # kernel/_decide_hotfix_triage and executed by executor handlers.
    "precheck_hotfix",
    "validate_anchor",
    "complete_hotfix_entry",
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
