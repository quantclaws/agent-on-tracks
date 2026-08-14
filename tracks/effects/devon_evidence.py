"""Structured evidence extraction for Devon M-IMPL dispatches."""

from __future__ import annotations


def extract_devon_evidence(proc, final_text_event, first_json_object) -> dict:
    event = final_text_event(proc)
    if event is None:
        return {}
    part = event.get("part")
    text = part.get("text") if isinstance(part, dict) else None
    payload = first_json_object(text.strip()) if isinstance(text, str) else None
    if not isinstance(payload, dict):
        return {}
    fields = (
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
        "advisories",
    )
    return {field: payload[field] for field in fields if field in payload}
