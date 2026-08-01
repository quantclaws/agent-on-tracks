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
    "spec.decisions_finalized",
    "acceptance.committed",
    "human.triage",
    "human.review",
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
)

# interfaces §4 — v0.1 closed command set.
COMMAND_KINDS = (
    "dispatch_agent",
    "validate_document",
    "commit_document",
    "create_branch",
    "delete_branch",
    "write_frontmatter",
    "finalize_spec_decisions",
    "record_backlog",
    "complete_run",
    "rollback_stage",
    # v0.2 scope expansion (IF-003 §10b): M-REQ-APPROVAL / FR-0200
    "generate_preview",
    "record_approval",
    "create_issues",
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
