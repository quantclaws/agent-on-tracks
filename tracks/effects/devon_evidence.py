"""Structured evidence extraction for Devon M-IMPL dispatches."""

from __future__ import annotations

from tracks.kernel.envelope import EnvelopeFormatError, parse_agent_output

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


def extract_devon_evidence(proc, final_text_event, first_json_object) -> dict:
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
