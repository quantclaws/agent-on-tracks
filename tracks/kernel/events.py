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
