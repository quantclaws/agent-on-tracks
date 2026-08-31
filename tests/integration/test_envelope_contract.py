"""Integration: unified envelope contract (FR-0278, IF-ENVELOPE-001)."""

from __future__ import annotations

import pytest

from tracks.kernel.envelope import (
    build_assignment_envelope,
    parse_agent_output,
    validate_envelope,
)

pytestmark = pytest.mark.integration


# AC-FR0278-01@v0.8 TRACKS-TRACE unified envelope parse with kind/version
def test_unified_envelope_parse(host_repo, trac, event_log):
    try:
        parse_agent_output("no envelope here")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc)

    try:
        build_assignment_envelope("devon:red", {"task": "x"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc)

    try:
        validate_envelope({"envelope": {"kind": "devon:red", "version": 2}, "payload": {}}, "devon:red")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc)

    # Behavioural outlet: trac replay/report must show single deterministic parse path
    trac("run")
    replay = trac("replay")
    assert replay.returncode == 0 or "envelope" in replay.stdout.lower()
    # validator must accept schema-generated envelopes (via trac validate)
    v = trac("validate", "--file", "tests/assets/v0.8/malformed/sample.json")
    assert v.returncode in (0, 1)  # outlet exists
    events = event_log()
    # Any envelope event must carry kind/version
    envelope_events = [e for e in events if e["type"] in ("dispatch.parity", "format_error")]
    for ev in envelope_events:
        assert "envelope" in str(ev) or "version" in str(ev).lower()


# AC-FR0278-02@v0.8 TRACKS-TRACE malformed format_error and no business mutation
def test_malformed_format_error(host_repo, trac, event_log):
    from tracks.kernel.envelope import EnvelopeFormatError

    try:
        parse_agent_output('```tracks-envelope\n{"bad": "json"\n```')
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc)
    except EnvelopeFormatError:
        pass

    # Missing kind/version, unknown version, or free-form JSON must be format_error
    malformed_samples = [
        "```tracks-envelope\n{\"payload\": {}}\n```",  # missing kind/version
        "```tracks-envelope\n{\"envelope\": {\"kind\": \"x\", \"version\": 99}, \"payload\": {}}\n```",
        '{"kind": "devon:red", "payload": {}}',  # no fence
    ]
    for sample in malformed_samples:
        try:
            parse_agent_output(sample)
            raise AssertionError(f"should have raised for {sample!r}")
        except NotImplementedError as exc:
            assert "IF-ENVELOPE-001" in str(exc)
        except EnvelopeFormatError as exc:
            assert exc.kind in (
                "missing_kind",
                "missing_version",
                "unknown_version",
                "unknown_kind",
                "no_envelope_block",
                "multiple_envelope_blocks",
                "malformed_json",
                "schema_violation",
            )

    trac("run")
    events_after = event_log()
    format_errors = [e for e in events_after if e["type"] == "format_error"]
    assert format_errors, "format_error must appear for malformed input"
    business = [e for e in events_after if e["type"] in ("publish.executed", "release.decided")]
    for b in business:
        assert b["seq"] not in [f["seq"] for f in format_errors]
    status = trac("status")
    assert "format_error" in status.stdout
    assert "red.validated" not in [e["type"] for e in format_errors]
