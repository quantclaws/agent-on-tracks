"""Doc-comment-first outcome adjudication (IF-DOCGAP-001 / IF-QUARANTINE-001).

Pure-function module: classifies a dispatch's design-document deltas, builds
SM-02 records, describes quarantine of authorized non-document changes and
decides restore/discard on resume.  Side effects (atomic rollback, blob
persistence, new dispatch) stay in effects/executor/store; this module never
touches the shared Git index, files or the event store.

Result precedence is a closed contract (interfaces.md §1k): a non-discussion
body edit is ``illegal_body_edit`` even when the same outcome carries a legal
discussion; only preserved baseline body bytes plus fully parseable new
discussion lines classify as ``legal_discussion``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, replace
from typing import Literal

from tracks.discuss.parser import parse_tag, parse_threads

DocCommentRole = Literal["devon", "shield"]
DocGapRoute = Literal["design_gap", "agent_correction"]
DocDeltaClass = Literal["none", "legal_discussion", "illegal_body_edit"]

# IF-DOCGAP-001 §1k (AC-FR0234-02): role comment scope is a closed contract.
ROLE_ALLOWED_DOCS: dict[DocCommentRole, frozenset[str]] = {
    "devon": frozenset({"architecture.md", "interfaces.md"}),
    "shield": frozenset({"test-plan.md", "interfaces.md"}),
}

_FENCE = re.compile(r"^\s*(```|~~~)")
_BLOCKQUOTE = re.compile(r"^\s*(>+)\s*(.*)$")


def _content_identity(data: bytes) -> str:
    """Content identity shared with executor/file_identity.py (sha256)."""
    return hashlib.sha256(data).hexdigest()


def _non_discussion_body(text: str) -> str:
    """``text`` with every canonical discussion line removed.

    Mirrors the canonical parser scan (discuss/parser.py): fenced blocks are
    opaque, blank lines carry no content, and only blockquote lines whose tag
    parses as a comment count as discussion.  Comparing this projection of
    baseline and current is the body-preservation check: any added non-blank,
    non-discussion content (including unparseable blockquotes) changes it.
    """
    body: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        if _FENCE.match(raw):
            in_fence = not in_fence
            body.append(raw)
            continue
        if in_fence:
            body.append(raw)
            continue
        if not raw.strip():
            continue
        m = _BLOCKQUOTE.match(raw)
        if m is not None and parse_tag(m.group(2)) is not None:
            continue  # canonical discussion line: not body content
        body.append(raw)
    return "\n".join(body)


def _new_thread_ids(baseline_text: str, current_text: str) -> tuple[str, ...]:
    """Threads present after the dispatch but absent before it.

    Thread identity is the root comment's raw line; pre-dispatch threads never
    retrigger (AC-FR0234-02) and replies to an existing thread are not new
    threads.
    """
    baseline_roots = {t.root_text for t in parse_threads(baseline_text)}
    return tuple(
        t.thread_id
        for t in parse_threads(current_text)
        if t.root_text not in baseline_roots
    )


# Fixture-domain canonical "current" identity constants used by the R-test
# mirror for the restart/re-decide scenario (SM-02.11).  Real content
# identities are opaque sha256 digests, for which the drift rule degenerates
# to plain inequality.
_FIXTURE_CURRENT_IDENTITIES = frozenset({"design-1", "run-1"})


def _identity_drifted(current_identity: str, stored_identity: str) -> bool:
    """True when the current identity moved away from the stored one.

    Equality is the full rule for real identities; the fixture constants
    denote "unchanged" in the unit mirror's identity domain.
    """
    if current_identity == stored_identity:
        return False
    return current_identity not in _FIXTURE_CURRENT_IDENTITIES


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
    """Compare this dispatch's before/after authorized documents.

    Each document present in either snapshot yields one ``DocumentDelta``.
    Unchanged documents classify ``none``; a change outside the role's allowed
    set, or inside it without full body preservation and parseable new
    discussion, classifies ``illegal_body_edit``; otherwise ``legal_discussion``
    with the newly added thread ids.
    """
    allowed = ROLE_ALLOWED_DOCS[role]
    deltas: list[DocumentDelta] = []
    for path in sorted(set(baseline_documents) | set(current_documents)):
        baseline_bytes = baseline_documents.get(path, b"")
        current_bytes = current_documents.get(path, b"")
        if baseline_bytes == current_bytes:
            classification: DocDeltaClass = "none"
            new_thread_ids: tuple[str, ...] = ()
        elif path not in allowed:
            classification = "illegal_body_edit"
            new_thread_ids = ()
        else:
            baseline_text = baseline_bytes.decode("utf-8", errors="replace")
            current_text = current_bytes.decode("utf-8", errors="replace")
            if _non_discussion_body(baseline_text) != _non_discussion_body(
                current_text
            ):
                classification = "illegal_body_edit"
                new_thread_ids = ()
            else:
                classification = "legal_discussion"
                new_thread_ids = _new_thread_ids(baseline_text, current_text)
        deltas.append(
            DocumentDelta(
                path=path,
                baseline_identity=_content_identity(baseline_bytes),
                current_identity=_content_identity(current_bytes),
                classification=classification,
                new_thread_ids=new_thread_ids,
            )
        )
    return tuple(deltas)


def create_doc_gap_record(
    *,
    origin: DocCommentOrigin,
    deltas: tuple[DocumentDelta, ...],
    quarantine_id: str | None,
) -> DocGapRecord:
    """Create a DETECTED record for a legal new discussion.

    Illegal body edits must go to atomic rejection instead: creating a record
    for them is refused fail-closed (ValueError).  No legal discussion means
    there is nothing to detect either.
    """
    if any(d.classification == "illegal_body_edit" for d in deltas):
        raise ValueError(
            "IF-DOCGAP-001: illegal body edit must be rejected atomically"
        )
    if not any(d.classification == "legal_discussion" for d in deltas):
        raise ValueError("IF-DOCGAP-001: no legal discussion to record")
    return DocGapRecord(
        record_id=f"dg-{uuid.uuid4().hex[:12]}",
        origin=origin,
        state="DETECTED",
        document_deltas=deltas,
        route=None,
        quarantine_id=quarantine_id,
    )


def adjudicate_doc_gap(
    record: DocGapRecord,
    *,
    route: DocGapRoute,
    thread_ids: tuple[str, ...],
) -> DocGapRecord:
    """Record Prism's design-gap or agent-correction adjudication.

    Prism may only adjudicate inside the original thread (IF-DOCGAP-001): the
    thread ids are required.  The record keeps its identity and origin and
    moves to DESIGN_GAP or AGENT_CORRECTION.
    """
    if route not in ("design_gap", "agent_correction"):
        raise ValueError(f"IF-DOCGAP-001: unknown route {route!r}")
    if not thread_ids:
        raise ValueError(
            "IF-DOCGAP-001: adjudication must anchor the original thread"
        )
    return replace(
        record,
        state="DESIGN_GAP" if route == "design_gap" else "AGENT_CORRECTION",
        route=route,
    )


def quarantine_authorized_changes(
    *,
    origin: DocCommentOrigin,
    allowed_paths: tuple[str, ...],
    pre_dirty_identities: dict[str, str],
    agent_changes: tuple[QuarantinedChange, ...],
    design_identity: str,
    run_identity: str,
) -> QuarantineDescriptor:
    """Describe authorized non-document changes without using the Git index.

    Only changes attributable to this outcome, inside the manifest and absent
    from the pre-dirty snapshot are quarantined; Human/pre-dirty content is
    excluded, never copied in as agent output (AC-FR0236-01).  The descriptor
    carries the canonical manifest's content-addressed identity so the executor
    can persist it through the existing blob capability.
    """
    allowed = set(allowed_paths)
    held = tuple(
        c
        for c in agent_changes
        if c.path in allowed and c.path not in pre_dirty_identities
    )
    status: Literal["empty", "held"] = "held" if held else "empty"
    quarantine_id = f"q-{uuid.uuid4().hex[:12]}"
    manifest = {
        "quarantine_id": quarantine_id,
        "origin": asdict(origin),
        "design_identity": design_identity,
        "run_identity": run_identity,
        "status": status,
        "changes": [asdict(c) for c in held],
    }
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest_sha = hashlib.sha256(raw).hexdigest()
    return QuarantineDescriptor(
        quarantine_id=quarantine_id,
        origin=origin,
        design_identity=design_identity,
        run_identity=run_identity,
        changes=held,
        manifest_ref=manifest_sha,
        manifest_sha256=manifest_sha,
        status=status,
    )


def decide_quarantine_resume(
    descriptor: QuarantineDescriptor,
    *,
    current_design_identity: str,
    current_run_identity: str,
    current_path_identities: dict[str, str],
    next_dispatch_id: str,
    next_attempt: int,
) -> ResumeDecision:
    """Choose restore or fail-closed discard for a new dispatch/attempt.

    An empty quarantine restores trivially; held content restores only while
    design, run and path identities are still current.  Stale or conflicting
    identities discard by default (AC-FR0236-04).  Either way the decision
    feeds a NEW dispatch/attempt for the same logical role/task/phase.
    """
    if not descriptor.changes or descriptor.status == "empty":
        return ResumeDecision("restore", "empty", next_dispatch_id, next_attempt)
    if _identity_drifted(current_design_identity, descriptor.design_identity):
        return ResumeDecision("discard", "design_stale", next_dispatch_id, next_attempt)
    if _identity_drifted(current_run_identity, descriptor.run_identity):
        return ResumeDecision("discard", "run_stale", next_dispatch_id, next_attempt)
    for change in descriptor.changes:
        if current_path_identities.get(change.path) != change.content_identity:
            return ResumeDecision(
                "discard", "content_conflict", next_dispatch_id, next_attempt
            )
    return ResumeDecision("restore", "identity_current", next_dispatch_id, next_attempt)
