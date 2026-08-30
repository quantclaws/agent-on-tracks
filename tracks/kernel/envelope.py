"""Unified versioned agent envelope (FR-0278/FR-0279, IF-ENVELOPE-001/002).

Single deterministic parse path for every role and assignment kind. The wire
form is exactly one fenced ``tracks-envelope`` code block carrying a JSON
object with an ``envelope`` header (``kind`` + ``version``) and a ``payload``.
"Last JSON" extraction, role-specific heuristic scans and prose guessing are
retired: zero or multiple envelope blocks are format errors, never guesses.
"""

from __future__ import annotations

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


def parse_agent_output(text: str) -> dict:
    """Extract and validate the single envelope block from agent output."""
    raise NotImplementedError("IF-ENVELOPE-001")


def build_assignment_envelope(kind: str, task: dict) -> dict:
    """Wrap a Runtime-built assignment in the versioned envelope header."""
    raise NotImplementedError("IF-ENVELOPE-001")


def validate_envelope(envelope: dict, expected_kind: str | None) -> dict:
    """Validate kind/version closed sets and the per-kind payload schema."""
    raise NotImplementedError("IF-ENVELOPE-001")


def envelope_schema_digest(kind: str) -> str:
    """Stable digest of the per-kind payload schema (parity evidence)."""
    raise NotImplementedError("IF-ENVELOPE-002")


def check_envelope_parity(referenced_versions: dict[str, Any]) -> dict:
    """Compare contract versions referenced by prompt/agent/skill/fake/real
    backend/validator surfaces; any mismatch blocks the dispatch."""
    raise NotImplementedError("IF-ENVELOPE-002")
