"""Structured evidence extraction for Devon M-IMPL dispatches."""

from __future__ import annotations

import json
from pathlib import Path

from tracks.kernel.envelope import (
    EnvelopeFormatError,
    parse_agent_output,
    validate_envelope,
)

# The Devon outcome fields the dispatch payload forwards (the evidence
# contract shared by the extractor and the Runtime's outcome payload).
DEVON_EVIDENCE_FIELDS = (
    "phase",
    "changed_paths",
    "commands",
    "results",
    "manifest_compliance",
    "pre_identity",
    "post_identity",
    "r_identity",
    "no_change_reason",
    "implemented_if_ids",
    "result_identity",
)


def _envelope_payload(text: str) -> dict | None:
    """The declared envelope payload when *text* carries a valid envelope.

    A declared dispatch instructs the agent to reply with exactly one
    ``tracks-envelope`` block: the block's payload IS the evidence object
    (single deterministic path). A missing block (legacy reply) or an
    invalid one (the Runtime's collection face classifies it as
    ``format_error``) falls back to the legacy extraction untouched.
    """
    if "tracks-envelope" not in text:
        return None
    try:
        parsed = parse_agent_output(text)
    except EnvelopeFormatError:
        return None
    payload = parsed.get("payload")
    return payload if isinstance(payload, dict) else None


def _result_file_payload(result_path: str | None) -> dict | None:
    """The #174 result file's payload when it parses (None otherwise).

    Live defect this closes (run 01M2QTJB T-002, 2026-09-20): Devon wrote a
    complete devon:green envelope to the inbox file and replied with a
    one-line pointer — exactly as the contract teaches — but the evidence
    extractor only read the reply TEXT (fence/bare JSON), found the pointer,
    and classified the outcome evidence_malformed "missing: phase,
    changed_paths, ..." twice in a row, escalating to an S3 contract-
    simplification RULING for a contract that was not broken. The file is
    the machine-serialized channel and therefore the FIRST source; its
    schema is re-validated authoritatively by the collection face, so a
    lenient read here can only fail closed to the text paths.

    Prism #174b R1 (minor): acceptance mirrors the collection face — the
    file must pass ``validate_envelope`` (kind-agnostic), so a parseable
    but schema-invalid file never feeds evidence the collection face would
    itself have rejected in favor of the text path.
    """
    if not result_path:
        return None
    try:
        data = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        validated = validate_envelope(data, None)
    except EnvelopeFormatError:
        return None
    payload = validated.get("payload")
    return payload if isinstance(payload, dict) else None


def extract_devon_evidence(
    proc, final_text_event, first_json_object, result_path: str | None = None
) -> dict:
    payload = _result_file_payload(result_path)
    if payload is None:
        event = final_text_event(proc)
        if event is None:
            return {}
        part = event.get("part")
        text = part.get("text") if isinstance(part, dict) else None
        if not isinstance(text, str):
            return {}
        payload = _envelope_payload(text)
        if payload is None:
            payload = first_json_object(text.strip())
    if not isinstance(payload, dict):
        return {}
    fields = (*DEVON_EVIDENCE_FIELDS, "advisories")
    return {field: payload[field] for field in fields if field in payload}
