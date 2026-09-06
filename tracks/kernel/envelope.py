"""Unified versioned agent envelope (FR-0278/FR-0279, IF-ENVELOPE-001/002).

Single deterministic parse path for every role and assignment kind. The wire
form is exactly one fenced ``tracks-envelope`` code block carrying a JSON
object with an ``envelope`` header (``kind`` + ``version``) and a ``payload``.
"Last JSON" extraction, role-specific heuristic scans and prose guessing are
retired: zero or multiple envelope blocks are format errors, never guesses.

Injection face (operator OOB 2026-09-06, user-authorized): the payload
schemas registered here are embedded into dispatch assignments via
``build_assignment_envelope``. The agent .md output contracts defer to the
assignment ("kind/payload 字段、payload schema 与 classification vocabulary
一律以 assignment 注入为权威，不在本提示词固化") — which declared nothing
while this module was a stub, so reviewers learned the payload shape from
rejection errors after burning a full reasoning hop. The schema now lives in
exactly one place: edit it here (or promote it to version 3) and every
dispatch that declares it carries the new contract on its next materialization.

Rollout stage: only kinds with a registered payload schema are DECLARED on
dispatches (prism review family + DIAGNOSE). Writer/authority kinds stay
undeclared until T-024 registers their schemas and fixtures; their replies
keep the legacy channels. On a declared dispatch a MISSING block falls
through to legacy handling (a compliance miss is not ambiguity — T-024
tightens this to a hard format_error once the six parity faces embed the
token); structural breakage (multiple blocks, malformed JSON, schema
violations) fails closed into ``format_error``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Literal

ENVELOPE_PROTOCOL = "tracks-envelope"
ENVELOPE_VERSION = 2
ENVELOPE_FENCE_TAG = "tracks-envelope"

FormatErrorKind = Literal[
    "missing_kind",
    "missing_version",
    "unknown_version",
    "unknown_kind",
    "no_envelope_block",
    "multiple_envelope_blocks",
    "malformed_json",
    "schema_violation",
]


class EnvelopeFormatError(ValueError):
    """Deterministic parse failure; never counted as a semantic attempt."""

    def __init__(self, kind: FormatErrorKind, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# ---------------------------------------------------------------------------
# Kind closed set (role:substate form, kernel-maintained per interfaces.md §1j)
# ---------------------------------------------------------------------------

# Review family payload contract (SC-D35 §2.2): mirrored from the live
# opencode backend validators so the injected schema and the mechanical
# checks cannot drift. review_body carries the full critique; review_summary
# and each finding summary are index lines capped at REVIEW_SUMMARY_MAX.
REVIEW_SUMMARY_MAX = 140

REVIEW_FINDING_FIELDS = (
    "id",
    "severity",
    "defect_classification",
    "criterion",
    "artifact",
    "ac_refs",
    "summary",
)
REVIEW_FINDING_SCHEMA: dict = {
    "id": {"required": True},
    "severity": {"required": True},
    "defect_classification": {"required": True},
    "criterion": {"required": True},
    "artifact": {"required": True},
    "ac_refs": {"type": "array", "required": True},
    "summary": {"type": "string", "required": True, "max_chars": REVIEW_SUMMARY_MAX},
}

REVIEW_SCHEMA: dict = {
    "verdict": {"enum": ("pass", "revise"), "required": True},
    "review_summary": {
        "type": "string",
        "required_on_revise": True,
        "max_chars": REVIEW_SUMMARY_MAX,
    },
    "review_body": {"type": "string", "required_on_revise": True},
    "findings": {
        "type": "array",
        "required_on_revise": True,
        "min_items": 1,
        "items": REVIEW_FINDING_SCHEMA,
    },
}

# DIAGNOSE payload contract ({"classification", "reason", "evidence"}); the
# classification vocabulary is the kernel single-truth (kernel.m_impl) and is
# inlined into the declaration so it rides the same injection as the fields.
DIAGNOSE_SCHEMA: dict = {
    "classification": {"required": True},
    "reason": {"type": "string", "required": True},
    "evidence": {"required": True},
}

REVIEW_KINDS = ("prism:review", "prism:plan", "prism:red", "prism:final")
DIAGNOSE_KINDS = ("prism:diagnose",)

# kind -> payload schema descriptor (None = header-only validation until
# T-024 registers the schema; the header closed set still applies).
ENVELOPE_KINDS: dict[str, dict | None] = {
    **dict.fromkeys(REVIEW_KINDS, REVIEW_SCHEMA),
    **dict.fromkeys(DIAGNOSE_KINDS, DIAGNOSE_SCHEMA),
    # Writer/authority kinds: closed but schema-pending (T-024).
    "devon:red": None,
    "devon:green": None,
    "devon:refactor": None,
    "shield:write": None,
    "shield:shield_fix": None,
    "archer:planning": None,
    "archer:ruling": None,
}

# Substate -> envelope kind. Prism review substates share the review payload
# contract; DIAGNOSE has its own. Unknown pairs are simply not envelope kinds.
_PRISM_SUBSTATE_KINDS = {
    "PRISM_REVIEW": "prism:review",
    "PRISM_PLAN": "prism:plan",
    "PRISM_RED": "prism:red",
    "PRISM_FINAL": "prism:final",
    "DIAGNOSE": "prism:diagnose",
}


def envelope_kind(role: str | None, substate: str | None) -> str | None:
    """Map (role, substate) onto the closed kind set; None when unmapped."""
    upper = (substate or "").strip().upper()
    if role == "prism":
        return _PRISM_SUBSTATE_KINDS.get(upper)
    if role == "devon":
        return {"RED": "devon:red", "GREEN": "devon:green", "REFACTOR": "devon:refactor"}.get(upper)
    if role == "shield":
        return {"WRITE": "shield:write", "SHIELD_FIX": "shield:shield_fix"}.get(upper)
    if role == "archer":
        return {"PLANNING": "archer:planning", "RULING": "archer:ruling"}.get(upper)
    return None


def envelope_declared_kind(role: str | None, substate: str | None) -> str | None:
    """Kinds whose schema is registered and therefore DECLARED on dispatch.

    Header-only kinds stay undeclared for now: their reply parsing keeps the
    legacy channels until T-024 registers their payload schemas and fixtures.
    """
    kind = envelope_kind(role, substate)
    if kind is not None and ENVELOPE_KINDS.get(kind) is not None:
        return kind
    return None


# ---------------------------------------------------------------------------
# Payload validators (canonical semantics; effects/opencode.py delegates here)
# ---------------------------------------------------------------------------


def validate_review_payload(payload: dict) -> str | None:
    """SC-D35 review payload; None when valid, else the violation detail.

    A pass verdict carries no further requirements (matching the mechanical
    channel); a revise must carry a non-empty index summary within cap, a
    non-empty body, and at least one well-formed finding.
    """
    if payload.get("verdict") not in ("pass", "revise"):
        return f"review payload verdict must be 'pass'|'revise', got {payload.get('verdict')!r}"
    if payload["verdict"] == "pass":
        return None
    summary = payload.get("review_summary")
    if not isinstance(summary, str) or not summary.strip():
        return "review payload revise requires non-empty review_summary"
    if len(summary) > REVIEW_SUMMARY_MAX:
        return f"review_summary exceeds {REVIEW_SUMMARY_MAX} chars"
    body = payload.get("review_body")
    if not isinstance(body, str) or not body.strip():
        return "review payload revise requires non-empty review_body"
    findings = payload.get("findings")
    if not isinstance(findings, list) or not findings:
        return "review payload revise requires non-empty findings[]"
    for idx, finding in enumerate(findings, start=1):
        error = validate_review_finding(finding, idx)
        if error is not None:
            return error
    return None


def validate_review_finding(finding: object, idx: int) -> str | None:
    if not isinstance(finding, dict):
        return f"findings[{idx}] must be an object"
    missing = [field for field in REVIEW_FINDING_FIELDS if field not in finding]
    if missing:
        return f"findings[{idx}] missing fields: {', '.join(missing)}"
    summary = finding.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return f"findings[{idx}].summary must be a non-empty string"
    if len(summary) > REVIEW_SUMMARY_MAX:
        return f"findings[{idx}].summary exceeds {REVIEW_SUMMARY_MAX} chars"
    if not isinstance(finding.get("ac_refs"), list):
        return f"findings[{idx}].ac_refs must be a list"
    return None


def validate_diagnose_payload(payload: dict) -> str | None:
    """DIAGNOSE payload ({classification, reason, evidence})."""
    from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS

    if payload.get("classification") not in DIAGNOSE_CLASSIFICATIONS:
        return (
            "diagnose payload classification must be one of: "
            + ", ".join(DIAGNOSE_CLASSIFICATIONS)
        )
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return "diagnose payload requires non-empty reason"
    if payload.get("evidence") in (None, ""):
        return "diagnose payload requires non-empty evidence"
    return None


_PAYLOAD_VALIDATORS: dict[str, Callable[[dict], str | None]] = {
    **dict.fromkeys(REVIEW_KINDS, validate_review_payload),
    **dict.fromkeys(DIAGNOSE_KINDS, validate_diagnose_payload),
}


# ---------------------------------------------------------------------------
# Parsing (IF-ENVELOPE-001 single path)
# ---------------------------------------------------------------------------

_FENCE_OPEN_RE = re.compile(r"^```[ \t]*tracks-envelope[ \t]*\r?$", re.MULTILINE)
_FENCE_CLOSE_RE = re.compile(r"^```[ \t]*\r?$", re.MULTILINE)


def parse_agent_output(text: str) -> dict:
    """Extract and validate the single envelope block from agent output.

    Returns the parsed ``{"envelope": {...}, "payload": {...}}`` object. Any
    deviation raises :class:`EnvelopeFormatError` with a FormatErrorKind —
    never a guess, never a heuristic fallback.
    """
    if not isinstance(text, str):
        raise EnvelopeFormatError("malformed_json", "reply text is not a string")
    opens = list(_FENCE_OPEN_RE.finditer(text))
    if not opens:
        raise EnvelopeFormatError(
            "no_envelope_block",
            f"no fenced {ENVELOPE_FENCE_TAG} block in reply",
        )
    blocks: list[str] = []
    for open_match in opens:
        after = text[open_match.end() :]
        close = _FENCE_CLOSE_RE.search(after)
        if close is None:
            raise EnvelopeFormatError(
                "malformed_json", "tracks-envelope block is not closed"
            )
        blocks.append(after[: close.start()])
    if len(blocks) > 1:
        raise EnvelopeFormatError(
            "multiple_envelope_blocks",
            f"{len(blocks)} tracks-envelope blocks in reply; exactly one is the contract",
        )
    try:
        parsed = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise EnvelopeFormatError(
            "malformed_json", f"envelope block is not JSON: {exc}"
        ) from exc
    return validate_envelope(parsed, None)


def validate_envelope(envelope: dict, expected_kind: str | None) -> dict:
    """Validate kind/version closed sets and the per-kind payload schema."""
    if not isinstance(envelope, dict):
        raise EnvelopeFormatError("malformed_json", "envelope must be a JSON object")
    header = envelope.get("envelope")
    if not isinstance(header, dict):
        raise EnvelopeFormatError("missing_kind", "missing envelope header object")
    kind = header.get("kind")
    if not isinstance(kind, str) or not kind:
        raise EnvelopeFormatError("missing_kind", "envelope header has no kind")
    if "version" not in header:
        raise EnvelopeFormatError(
            "missing_version", f"envelope header for {kind} has no version"
        )
    if header.get("version") != ENVELOPE_VERSION:
        raise EnvelopeFormatError(
            "unknown_version",
            f"envelope version {header.get('version')!r} != {ENVELOPE_VERSION}",
        )
    if kind not in ENVELOPE_KINDS:
        raise EnvelopeFormatError(
            "unknown_kind",
            f"kind {kind!r} not in closed set: {', '.join(sorted(ENVELOPE_KINDS))}",
        )
    if expected_kind is not None and kind != expected_kind:
        raise EnvelopeFormatError(
            "schema_violation",
            f"reply kind {kind!r} != expected {expected_kind!r}",
        )
    if "payload" not in envelope:
        raise EnvelopeFormatError("schema_violation", "envelope carries no payload")
    validator = _PAYLOAD_VALIDATORS.get(kind)
    if validator is not None:
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise EnvelopeFormatError("schema_violation", "payload must be a JSON object")
        error = validator(payload)
        if error is not None:
            raise EnvelopeFormatError("schema_violation", error)
    return envelope


# ---------------------------------------------------------------------------
# Schema digest + parity (IF-ENVELOPE-002)
# ---------------------------------------------------------------------------


def envelope_schema_digest(kind: str) -> str:
    """Stable digest of the per-kind payload schema (parity evidence)."""
    if kind not in ENVELOPE_KINDS:
        raise EnvelopeFormatError(
            "unknown_kind",
            f"kind {kind!r} not in closed set: {', '.join(sorted(ENVELOPE_KINDS))}",
        )
    canonical = json.dumps(
        ENVELOPE_KINDS[kind], sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parity_version(value: Any) -> int | None:
    """Normalize a face's declared version token; None = undeclared (skip).

    Accepts the bare int (assignment/validator faces) and the machine token
    (``tracks-envelope:v2``) the six parity faces embed in materialized
    artifacts.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        token = value.strip()
        prefix = f"{ENVELOPE_PROTOCOL}:v"
        if token.startswith(prefix):
            try:
                return int(token[len(prefix) :])
            except ValueError:
                return None
    return None


def check_envelope_parity(referenced_versions: dict[str, Any]) -> dict:
    """Compare contract versions referenced by the parity faces.

    Faces that declare nothing (None/missing) are skipped — the rollout is
    staged and a face may legitimately predate the envelope; a face that
    declares a DIFFERENT version than the kernel validator is a mismatch and
    blocks the dispatch (``dispatch.rejected reason=version_parity_mismatch``).
    """
    mismatches: list[dict] = []
    for face, value in (referenced_versions or {}).items():
        version = _parity_version(value)
        if version is None:
            continue
        if version != ENVELOPE_VERSION:
            mismatches.append({"face": face, "version": version})
    return {"consistent": not mismatches, "mismatches": mismatches}


# ---------------------------------------------------------------------------
# Assignment injection face
# ---------------------------------------------------------------------------


def build_assignment_envelope(kind: str, task: dict | None = None) -> dict:
    """The reply-contract declaration a dispatch assignment carries.

    This is the authoritative schema the agent .md contracts defer to: the
    declaration pins the reply kind, the payload schema (inline, so the
    writer of the reply can read the field sets and caps without guessing),
    and the schema digest (so parity can detect a stale declaration without
    re-deriving the schema). ``task`` pins the reply to the task identity
    when the dispatch is task-scoped.
    """
    if kind not in ENVELOPE_KINDS:
        raise EnvelopeFormatError(
            "unknown_kind",
            f"kind {kind!r} not in closed set: {', '.join(sorted(ENVELOPE_KINDS))}",
        )
    declaration = {
        "protocol": ENVELOPE_PROTOCOL,
        "version": ENVELOPE_VERSION,
        # The machine token the agent .md output contracts key on ("当
        # assignment 声明 tracks-envelope:v2 时…") and the parity faces embed.
        "token": f"{ENVELOPE_PROTOCOL}:v{ENVELOPE_VERSION}",
        "kind": kind,
        "payload_schema": ENVELOPE_KINDS[kind],
        "schema_digest": envelope_schema_digest(kind),
    }
    task_id = (task or {}).get("task_id")
    if task_id:
        declaration["task_id"] = task_id
    return declaration
