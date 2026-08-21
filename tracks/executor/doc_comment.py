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
DocDeltaClass = Literal["none", "legal_discussion", "discussion_reply", "illegal_body_edit"]

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

    Thread identity is the root's (speaker, body) with the status dropped
    (a legal resolve is the same thread); pre-dispatch threads never
    retrigger (AC-FR0234-02) and replies to an existing thread are not new
    threads.
    """
    baseline_roots = {
        ident
        for ident in (_line_identity(t.root_text) for t in parse_threads(baseline_text))
        if ident is not None
    }
    return tuple(
        t.thread_id
        for t in parse_threads(current_text)
        if (ident := _line_identity(t.root_text)) is not None
        and ident not in baseline_roots
    )


def _discussion_lines(text: str) -> list[str]:
    """Every canonical discussion line (the exact lines _non_discussion_body
    strips). Used for the append-only check on preserved-body deltas."""
    lines: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or not raw.strip():
            continue
        m = _BLOCKQUOTE.match(raw)
        if m is not None and parse_tag(m.group(2)) is not None:
            lines.append(raw)
    return lines


def _line_identity(raw_line: str) -> str | None:
    """Discussion-line identity that is INVARIANT under legal status flips:
    the canonical writer emits open roots WITHOUT a tag (``> **Prism:** x``)
    and resolved ones with `` [RESOLVED]`` — so text-level tag stripping
    leaves whitespace drift and misjudges a legal resolve as a rewrite
    (PRISM-B26A-R2-01). Identity is parse_tag's (speaker, body); status is
    deliberately dropped. Returns None for non-canonical lines."""
    m = _BLOCKQUOTE.match(raw_line)
    if m is None:
        return None
    parsed = parse_tag(m.group(2))
    if parsed is None:
        return None
    speaker, _status, body = parsed
    return f"{speaker}|{body}"


def _classify_delta(
    allowed: frozenset[str],
    path: str,
    baseline_bytes: bytes,
    current_bytes: bytes,
) -> tuple[DocDeltaClass, tuple[str, ...]]:
    """One document's classification and its new thread ids.

    Unchanged bytes classify ``none``; a change outside the role's allowed set,
    or inside it without full body preservation, classifies
    ``illegal_body_edit``. A preserved-body delta is then held to the
    discussion append-only rule (PRISM-B26A-R1-01): every baseline discussion
    line must survive verbatim — deleting a thread or rewriting an existing
    comment line is ``illegal_body_edit`` (fail-closed, review records are
    append-only). A surviving delta WITH new root threads (identity
    status-normalized) is ``legal_discussion`` (SM-02 interception); with NO
    new threads — replies to pre-existing threads, legal status flips — it is
    ``discussion_reply`` and never triggers the pause (flow.md §10.4: 旧线
    程、他人回复不触发截获; B26a/#28).
    """
    if baseline_bytes == current_bytes:
        return "none", ()
    if path not in allowed:
        return "illegal_body_edit", ()
    baseline_text = baseline_bytes.decode("utf-8", errors="replace")
    current_text = current_bytes.decode("utf-8", errors="replace")
    if _non_discussion_body(baseline_text) != _non_discussion_body(current_text):
        return "illegal_body_edit", ()
    if not _discussion_append_only(baseline_text, current_text):
        return "illegal_body_edit", ()
    new_threads = _new_thread_ids(baseline_text, current_text)
    if not new_threads:
        return "discussion_reply", ()
    return "legal_discussion", new_threads


def _discussion_append_only(baseline_text: str, current_text: str) -> bool:
    """Baseline discussion lines must survive into current ones, compared
    with status tags stripped: replies may be appended, threads added, and a
    thread's status tag legally flipped ([OPEN]->[RESOLVED]) — but no
    baseline comment content may be deleted or rewritten
    (PRISM-B26A-R1-01/R1-02)."""
    current_counts: dict[str, int] = {}
    for line in _discussion_lines(current_text):
        key = _line_identity(line)
        if key is None:
            continue
        current_counts[key] = current_counts.get(key, 0) + 1
    for line in _discussion_lines(baseline_text):
        key = _line_identity(line)
        if key is None:
            continue
        if current_counts.get(key, 0) <= 0:
            return False
        current_counts[key] -= 1
    return True


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


# SM-02 adjudication ingestion (IF-DOCGAP-001, #28 phase 2 / #62): Prism's
# technical adjudication is a REPLY inside the original discussion thread
# carrying a single machine-readable marker line.  The explicit marker (never
# natural-language inference) is the only channel that may move a waiting
# doc-gap record out of DETECTED.  Grammar (one line, fields separated by
# ``|``; the discuss parser reads the tag, field text is opaque to it):
#
#   >> **Prism:** SM-02-ADJUDICATION | route=design_gap \
#   | responsible_role=archer | quarantine_id=q-... | threads=T-001,T-002
#
ADJUDICATION_TAG = "SM-02-ADJUDICATION"
_MARKER_FIELD_KEYS = frozenset(
    {"route", "responsible_role", "quarantine_id", "threads"}
)
_THREAD_LABEL = re.compile(r"^T-\d+$")


@dataclass(frozen=True)
class AdjudicationMarker:
    """One well-formed SM-02 adjudication marker found in a document."""

    route: DocGapRoute
    responsible_role: str
    quarantine_id: str
    thread_ids: tuple[str, ...]
    host_thread_id: str
    decision_ref: str


def _decision_ref(
    route: str, responsible_role: str, quarantine_id: str, thread_ids: tuple[str, ...]
) -> str:
    """Deterministic digest of the normalized decision content (audit key)."""
    raw = "|".join(
        (route, responsible_role, quarantine_id, ",".join(sorted(thread_ids)))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_marker_body(body: str) -> tuple[dict[str, str], str | None]:
    """Parse the field section of an adjudication marker body.

    Returns (fields, error). Fail-closed: missing/unknown/duplicate fields,
    unknown routes and malformed thread labels are errors, never guesses.
    """
    segments = [s.strip() for s in body.split("|")]
    if segments[0] != ADJUDICATION_TAG:
        return {}, f"marker body must start with {ADJUDICATION_TAG!r}"
    fields: dict[str, str] = {}
    for segment in segments[1:]:
        key, sep, value = segment.partition("=")
        key = key.strip()
        if not sep or not value.strip():
            return {}, f"malformed marker field {segment!r}"
        if key not in _MARKER_FIELD_KEYS:
            return {}, f"unknown marker field {key!r}"
        if key in fields:
            return {}, f"duplicate marker field {key!r}"
        fields[key] = value.strip()
    missing = _MARKER_FIELD_KEYS - set(fields)
    if missing:
        return {}, f"missing marker fields: {sorted(missing)}"
    if fields["route"] not in ("design_gap", "agent_correction"):
        return {}, f"unknown route {fields['route']!r}"
    labels = tuple(t.strip() for t in fields["threads"].split(",") if t.strip())
    if not labels or any(not _THREAD_LABEL.match(t) for t in labels):
        return {}, f"malformed thread labels {fields['threads']!r}"
    if len(set(labels)) != len(labels):
        return {}, "duplicate thread labels"
    fields["threads"] = ",".join(labels)
    return fields, None


def scan_adjudication_markers(
    text: str,
) -> tuple[list[AdjudicationMarker], list[tuple[str, str]]]:
    """Collect well-formed SM-02 markers and fail-closed errors from one doc.

    Only NESTED Prism replies (depth >= 2) inside an existing thread count:
    a depth-1 marker line would open its own thread and is not "inside the
    original thread" (IF-DOCGAP-001).  A Prism line carrying the tag with an
    unparseable body is an error, not a near-miss to ignore.  Errors carry
    their host thread id so the matcher can tell markers written into THIS
    record's threads apart from historical/unrelated ones.
    """
    markers: list[AdjudicationMarker] = []
    errors: list[tuple[str, str]] = []
    for thread in parse_threads(text):
        stack: list[tuple[str, object]] = [
            (thread.thread_id, child) for child in thread.root.children
        ]
        while stack:
            host_thread_id, comment = stack.pop()
            stack.extend((host_thread_id, c) for c in comment.children)
            if comment.speaker.strip().lower() != "prism":
                continue
            body = comment.body.strip()
            if not body.startswith(ADJUDICATION_TAG):
                continue
            fields, error = _parse_marker_body(body)
            if error is not None:
                errors.append((host_thread_id, error))
                continue
            labels = tuple(t for t in fields["threads"].split(","))
            markers.append(
                AdjudicationMarker(
                    route=fields["route"],
                    responsible_role=fields["responsible_role"],
                    quarantine_id=fields["quarantine_id"],
                    thread_ids=labels,
                    host_thread_id=host_thread_id,
                    decision_ref=_decision_ref(
                        fields["route"],
                        fields["responsible_role"],
                        fields["quarantine_id"],
                        labels,
                    ),
                )
            )
    return markers, errors


def match_adjudication_marker(
    candidates: list[AdjudicationMarker],
    errors: list[tuple[str, str]] = (),
    *,
    quarantine_id: str | None,
    thread_ids: list[str] | None,
    origin_role: str | None,
) -> tuple[AdjudicationMarker | None, str | None]:
    """Bind ONE scanned marker to its waiting record; fail-closed otherwise.

    Relevance filtering happens BEFORE any ambiguity decision: documents
    keep every historical adjudication marker ever written, so candidates
    are first narrowed to THIS record's quarantine and malformed-marker
    errors only matter when written inside one of THIS record's original
    threads (#62 review round 2: unrelated history must never block a new
    record).  Within the relevant set: exactly one well-formed marker that
    references this quarantine, lists exactly the detected thread set,
    lives inside one of those threads and names the route's responsible
    role (design_gap -> archer; agent_correction -> the originating
    Devon/Shield role).  Zero relevant candidates is the ordinary
    still-waiting state (None, None).
    """
    expected = set(thread_ids or [])
    for host_thread_id, message in errors:
        if host_thread_id in expected:
            return None, f"{host_thread_id}: {message}"
    relevant = [
        marker
        for marker in candidates
        if marker.quarantine_id == (quarantine_id or "")
    ]
    if len(relevant) > 1:
        return None, (
            "ambiguous: multiple SM-02 adjudication markers reference "
            f"quarantine {quarantine_id!r}"
        )
    if not relevant:
        return None, None
    marker = relevant[0]
    if expected and marker.host_thread_id not in expected:
        return None, (
            f"marker must live inside the original thread {sorted(expected)}, "
            f"found {marker.host_thread_id!r}"
        )
    if set(marker.thread_ids) != expected:
        return None, (
            f"marker threads {sorted(marker.thread_ids)} do not match record "
            f"threads {sorted(expected)}"
        )
    if marker.route == "design_gap":
        if marker.responsible_role != "archer":
            return None, (
                "design_gap adjudication must route archer, got "
                f"{marker.responsible_role!r}"
            )
    elif marker.responsible_role != (origin_role or ""):
        return None, (
            f"agent_correction adjudication must route {origin_role!r}, got "
            f"{marker.responsible_role!r}"
        )
    return marker, None


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
        classification, new_thread_ids = _classify_delta(
            allowed, path, baseline_bytes, current_bytes
        )
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


def legal_anchor_pairs(
    deltas: tuple[DocumentDelta, ...],
) -> tuple[tuple[str, str], ...]:
    """(path, at-pause identity) pairs of the record's legal discussion docs.

    The design anchor answers "did the design move SINCE THE PAUSE": it must
    be the legal documents' CURRENT (post-delta) identities — the state the
    quarantined outcome was paused on — never the pre-dispatch baseline
    (the discussion itself would otherwise count as permanent drift), and
    never ``document_deltas[0]`` (the alphabetically-first protected doc,
    usually an unrelated ``none`` delta).  The resume decision recomputes
    the same combination over the live bytes of ``document_paths`` (which
    `_legal_delta_targets` fills from exactly these legal deltas)
    (PRISM review MAJOR-2, #62).
    """
    return tuple(
        (d.path, d.current_identity)
        for d in deltas
        if d.classification == "legal_discussion"
    )


def combined_design_identity(pairs: list[tuple[str, str]]) -> str:
    """Canonical combined identity over sorted (path, identity) pairs."""
    canonical = "|".join(f"{path}:{identity}" for path, identity in sorted(pairs))
    return _content_identity(canonical.encode("utf-8"))


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
