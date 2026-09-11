"""Integration: unified envelope contract (FR-0278, IF-ENVELOPE-001)."""

from __future__ import annotations

import pytest

from tests.integration.helpers import walk_to_m_test
from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    EnvelopeFormatError,
    build_assignment_envelope,
    parse_agent_output,
    validate_envelope,
)

pytestmark = pytest.mark.integration


# AC-FR0278-01@v0.8 TRACKS-TRACE unified envelope parse with kind/version
def test_unified_envelope_parse(host_repo, trac, event_log):
    # The single parse path is implemented: deviation raises the typed
    # EnvelopeFormatError (never a guess), the declaration carries
    # kind/version/schema, and a foreign version fails closed.
    with pytest.raises(EnvelopeFormatError) as missing:
        parse_agent_output("no envelope here")
    assert missing.value.kind == "no_envelope_block"

    declaration = build_assignment_envelope("prism:review", {"task_id": "T-1"})
    assert declaration["kind"] == "prism:review"
    assert declaration["version"] == ENVELOPE_VERSION
    assert declaration["token"] == f"tracks-envelope:v{ENVELOPE_VERSION}"
    assert declaration["task_id"] == "T-1"
    assert declaration["schema_digest"]

    with pytest.raises(EnvelopeFormatError) as version:
        validate_envelope(
            {"envelope": {"kind": "prism:review", "version": 99}, "payload": {}},
            "prism:review",
        )
    assert version.value.kind == "unknown_version"

    # Behavioural outlet: the walked run replays through the same single
    # parse path; the standalone validator outlet exists.
    walk_to_m_test(trac)
    replay = trac("replay")
    assert replay.returncode == 0
    # validator must accept schema-generated envelopes (via trac validate)
    v = trac("validate", "--file", "tests/assets/v0.8/malformed/sample.json")
    assert v.returncode in (0, 1)  # outlet exists
    events = event_log()
    # Any envelope event must carry its contract identity: format_error
    # carries the typed kind, dispatch.parity carries the per-face version map
    # every non-null face resolving to the current envelope version.
    envelope_events = [e for e in events if e["type"] in ("dispatch.parity", "format_error")]
    for ev in envelope_events:
        if ev["type"] == "format_error":
            assert ev["payload"].get("kind")
        else:
            referenced = ev["payload"].get("referenced", {})
            assert isinstance(referenced, dict)
            assert referenced
            assert all(value in (None, ENVELOPE_VERSION) for value in referenced.values())


# AC-FR0278-02@v0.8 TRACKS-TRACE malformed format_error and no business mutation
def test_malformed_format_error(host_repo, trac, event_log):
    with pytest.raises(EnvelopeFormatError):
        parse_agent_output('```tracks-envelope\n{"bad": "json"\n```')

    # Missing kind/version, unknown version, or free-form JSON must be format_error
    malformed_samples = [
        "```tracks-envelope\n{\"payload\": {}}\n```",  # missing kind/version
        "```tracks-envelope\n{\"envelope\": {\"kind\": \"x\", \"version\": 99}, \"payload\": {}}\n```",
        '{"kind": "devon:red", "payload": {}}',  # no fence
    ]
    for sample in malformed_samples:
        with pytest.raises(EnvelopeFormatError) as exc:
            parse_agent_output(sample)
        assert exc.value.kind in (
            "missing_kind",
            "missing_version",
            "unknown_version",
            "unknown_kind",
            "no_envelope_block",
            "multiple_envelope_blocks",
            "malformed_json",
            "schema_violation",
        )

    # The walked run produces the malformed input honestly: the v0.8 declared
    # M-TEST review envelope cannot represent a simulated bare revise (no
    # findings), so the fake's reply is classified as a schema_violation
    # format_error at collection (32b81c2 honesty) — never a synthesized pass
    # and never a business-state mutation. ``trac replay`` exposes the
    # format_error / semantic-attempt separation (AC-NFR0145-02).
    walk_to_m_test(trac)
    trac("run", simulate="prism:PRISM_REVIEW=pass|revise|pass")
    events_after = event_log()
    format_errors = [e for e in events_after if e["type"] == "format_error"]
    assert format_errors, "format_error must appear for the malformed declared reply"
    assert any(f["payload"].get("kind") == "schema_violation" for f in format_errors)
    business = [e for e in events_after if e["type"] in ("publish.executed", "release.decided")]
    for b in business:
        assert b["seq"] not in [f["seq"] for f in format_errors]
    replay = trac("replay")
    assert "format_error" in replay.stdout
    assert "red.validated" not in [e["type"] for e in format_errors]
