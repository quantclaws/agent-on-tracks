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
from collections.abc import Callable, Sequence
from typing import Any, Literal

from tracks.frontmatter import split_frontmatter

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
# S3 ruling (round 17, 2026-09-06, RULING blob dedfc37d): 140 chars could not
# hold the aggregated multi-finding summary in the working language and
# rejected substantively valid reviews twice -- the contract simplification
# delta raises the cap to 400 and adds the deterministic findings[0]
# derivation in validate_review_payload.
REVIEW_SUMMARY_MAX = 400

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
    "VERIFY_FINAL": "prism:final",
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


def _derive_review_summary(payload: dict) -> str | None:
    """S3 ruling (round 17, 2026-09-06): the aggregated summary line is a
    DERIVED field -- when the writer omits it while findings[] already carry
    a valid per-finding summary, derive it deterministically instead of
    rejecting a substantively valid review. Returns the derived value or
    None (caller rejects when None)."""
    findings = payload.get("findings")
    first = findings[0] if isinstance(findings, list) and findings else None
    derived = first.get("summary") if isinstance(first, dict) else None
    if isinstance(derived, str) and derived.strip():
        return derived
    return None


def _validate_revise_requirements(payload: dict) -> str | None:
    """Revise-side field requirements (SC-D35 §2.2): summary within cap,
    non-empty body, at least one well-formed finding. A missing summary is
    derived from findings[0] (S3 ruling) and normalized in place so every
    consumer (opencode merge, audit) sees the derived value."""
    summary = payload.get("review_summary")
    if not isinstance(summary, str) or not summary.strip():
        derived = _derive_review_summary(payload)
        if derived is not None:
            payload["review_summary"] = derived
            summary = derived
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


def validate_review_payload(payload: dict) -> str | None:
    """SC-D35 review payload; None when valid, else the violation detail.

    A pass verdict carries no further requirements (matching the mechanical
    channel); a revise must satisfy the revise-side field requirements.
    """
    if payload.get("verdict") not in ("pass", "revise"):
        return f"review payload verdict must be 'pass'|'revise', got {payload.get('verdict')!r}"
    if payload["verdict"] == "pass":
        return None
    return _validate_revise_requirements(payload)


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


# The six documented parity identities every ENFORCED dispatch must account
# for (TASK.md): prompt template, agent definition, skill, fake backend, real
# backend, Runtime validator. This is the kernel DEFAULT of
# ``check_envelope_parity``: a caller passing no explicit ``required_faces``
# is claiming a COMPLETE six-face map — every one of the six must be present
# and resolve to ENVELOPE_VERSION; a partial map (missing key, None, empty,
# garbled or unknown token) can never pass. Never the opposite default.
DEFAULT_PARITY_FACES: tuple[str, ...] = (
    "prompt",
    "agent",
    "skill",
    "fake_backend",
    "real_backend",
    "validator",
)

# The EXPLICIT legacy opt-out: pass this (an empty sequence) as
# ``required_faces`` to restore the staged semantics — faces that declare
# nothing (missing key/None) are skipped and only declared mismatches block.
# Permissiveness is NEVER the default; a legacy caller must ask for it
# explicitly (documented for coordinator migration of the old unit tests).
PARITY_STAGED: tuple[str, ...] = ()


# The frontmatter key carrying the machine parity token. It is a versioned
# artifact's parity declaration, NOT a document content field: FR-150 requires
# produced documents to match their kind template's semantic fields, never to
# repeat this token. validate.check_template excludes exactly this key.
FRONTMATTER_TOKEN_KEY = "envelope"

_FACE_TOKEN_RE = re.compile(
    rf"^{FRONTMATTER_TOKEN_KEY}:[ \t]*(\S+)[ \t]*$", re.MULTILINE
)


def scan_face_token(text: object) -> int | None:
    """Scan the machine parity token out of a versioned artifact's bytes.

    The declared token lives ONLY in the frontmatter (``envelope:
    tracks-envelope:v2``), never in the body: a body mention is prose, not a
    declaration. Returns the normalized parity version, or None when the face
    declares nothing (missing key, foreign token, garbled version) — the
    caller decides whether that absence is a mismatch under required parity.
    Pure text parsing: no file I/O, no effect concerns in the kernel.
    """
    if not isinstance(text, str):
        return None
    frontmatter, _body = split_frontmatter(text)
    match = _FACE_TOKEN_RE.search(frontmatter) if frontmatter else None
    if match is None:
        return None
    return _parity_version(match.group(1))


def _face_parity_mismatch(face: str, value: Any, enforced: bool) -> dict | None:
    """One face's parity verdict: the mismatch entry, or None when it passes.

    Under enforced parity (declared dispatch) an absent/foreign declaration
    (None value, missing key, garbled token) is itself a mismatch, so a
    partial map can never pass. Under staged parity (undeclared) a face that
    declares nothing is skipped exactly like today.
    """
    version = _parity_version(value)
    if version is None:
        if not enforced:
            return None
        return {
            "face": face,
            "version": None,
            "reason": "missing" if value is None else "invalid",
        }
    if version != ENVELOPE_VERSION:
        return {"face": face, "version": version, "reason": "version"}
    return None


def check_envelope_parity(
    referenced_versions: dict[str, Any],
    required_faces: Sequence[str] | None = None,
) -> dict:
    """Compare contract versions referenced by the parity faces.

    ``required_faces`` selects the enforcement mode:

    - ``None`` (DEFAULT): the six documented identities
      (:data:`DEFAULT_PARITY_FACES` — prompt, agent, skill, fake_backend,
      real_backend, validator) are ALL REQUIRED. The map must carry every one
      and each must resolve to the kernel validator's version; a required face
      absent from the map, or one whose value is None/empty/garbled (missing
      frontmatter key, invalid token), is itself a mismatch (reason
      "missing"/"invalid") — a partial map can never pass. A face declaring a
      DIFFERENT version is a mismatch (reason "version") and blocks the
      dispatch (``dispatch.rejected reason=version_parity_mismatch``).
    - An explicit non-empty sequence: only those named faces are enforced
      (internal dynamic artifact maps carrying distinct per-artifact face
      names, e.g. ``agent:<Name>``/``skill:<name>``/``template:<kind>``).
    - An explicit empty sequence (:data:`PARITY_STAGED`): the documented
      legacy opt-out — faces that declare nothing are skipped, only declared
      mismatches block (staged rollout semantics for legacy callers that opt
      out explicitly; permissiveness is never the default).
    """
    provided = referenced_versions or {}
    required = DEFAULT_PARITY_FACES if required_faces is None else tuple(required_faces)
    enforced = bool(required)
    mismatches: list[dict] = []
    for face in sorted(set(required) | set(provided)):
        mismatch = _face_parity_mismatch(face, provided.get(face), enforced)
        if mismatch is not None:
            mismatches.append(mismatch)
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
