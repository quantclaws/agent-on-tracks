"""Public interface stubs for doc-comment-first outcome adjudication.

Contracts: IF-DOCGAP-001 and IF-QUARANTINE-001. Devon replaces only the
function bodies during M-IMPL; declarations are frozen by interfaces.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DocCommentRole = Literal["devon", "shield"]
DocGapRoute = Literal["design_gap", "agent_correction"]
DocDeltaClass = Literal["none", "legal_discussion", "illegal_body_edit"]


@dataclass(frozen=True)
class DocCommentOrigin:
    run_id: str
    role: DocCommentRole
    task_id: str | None
    phase: str
    dispatch_id: str
    attempt: int


@dataclass(frozen=True)
class DocumentDelta:
    path: str
    baseline_identity: str
    current_identity: str
    classification: DocDeltaClass
    new_thread_ids: tuple[str, ...]


@dataclass(frozen=True)
class DocGapRecord:
    record_id: str
    origin: DocCommentOrigin
    state: Literal[
        "DETECTED",
        "AWAITING_ADJUDICATION",
        "DESIGN_GAP",
        "AGENT_CORRECTION",
        "READY_TO_RESUME",
        "RESTORED",
        "DISCARDED",
        "RESUMED",
    ]
    document_deltas: tuple[DocumentDelta, ...]
    route: DocGapRoute | None
    quarantine_id: str | None


@dataclass(frozen=True)
class QuarantinedChange:
    path: str
    operation: Literal["add", "modify", "delete"]
    baseline_identity: str | None
    content_identity: str | None


@dataclass(frozen=True)
class QuarantineDescriptor:
    quarantine_id: str
    origin: DocCommentOrigin
    design_identity: str
    run_identity: str
    changes: tuple[QuarantinedChange, ...]
    manifest_ref: str
    manifest_sha256: str
    status: Literal["empty", "held", "restored", "discarded"]


@dataclass(frozen=True)
class ResumeDecision:
    action: Literal["restore", "discard"]
    reason: Literal[
        "empty",
        "identity_current",
        "design_stale",
        "run_stale",
        "content_conflict",
    ]
    next_dispatch_id: str
    next_attempt: int


def classify_design_document_deltas(
    *,
    role: DocCommentRole,
    baseline_documents: dict[str, bytes],
    current_documents: dict[str, bytes],
) -> tuple[DocumentDelta, ...]:
    """Classify this dispatch's design-document deltas under IF-DOCGAP-001."""
    raise NotImplementedError("IF-DOCGAP-001")


def create_doc_gap_record(
    *,
    origin: DocCommentOrigin,
    deltas: tuple[DocumentDelta, ...],
    quarantine_id: str | None,
) -> DocGapRecord:
    """Create a DETECTED record for a legal new discussion."""
    raise NotImplementedError("IF-DOCGAP-001")


def adjudicate_doc_gap(
    record: DocGapRecord,
    *,
    route: DocGapRoute,
    thread_ids: tuple[str, ...],
) -> DocGapRecord:
    """Record Prism's design-gap or agent-correction adjudication."""
    raise NotImplementedError("IF-DOCGAP-001")


def quarantine_authorized_changes(
    *,
    origin: DocCommentOrigin,
    allowed_paths: tuple[str, ...],
    pre_dirty_identities: dict[str, str],
    agent_changes: tuple[QuarantinedChange, ...],
    design_identity: str,
    run_identity: str,
) -> QuarantineDescriptor:
    """Describe authorized non-document changes without using the Git index."""
    raise NotImplementedError("IF-QUARANTINE-001")


def decide_quarantine_resume(
    descriptor: QuarantineDescriptor,
    *,
    current_design_identity: str,
    current_run_identity: str,
    current_path_identities: dict[str, str],
    next_dispatch_id: str,
    next_attempt: int,
) -> ResumeDecision:
    """Choose restore or fail-closed discard for a new dispatch/attempt."""
    raise NotImplementedError("IF-QUARANTINE-001")
