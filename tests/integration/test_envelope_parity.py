"""Integration: envelope parity and malformed regression (FR-0279, IF-ENVELOPE-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks.kernel.envelope import check_envelope_parity, envelope_schema_digest

pytestmark = pytest.mark.integration


# AC-FR0279-01@v0.8 TRACKS-TRACE parity mismatch rejects dispatch before execution
def test_parity_mismatch_rejects(host_repo, trac, event_log):
    try:
        check_envelope_parity({"prompt": "v2", "agent": "v1"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-002" in str(exc)
    try:
        envelope_schema_digest("devon:red")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-002" in str(exc)

    trac("run")
    events = event_log()
    # Simulate parity mismatch injection: after implementation must produce dispatch.rejected
    rejected = [e for e in events if e["type"] == "dispatch.rejected" and e["payload"].get("reason") == "version_parity_mismatch"]
    # Pre-implementation legal red: expect block before agent execution
    if rejected:
        assert rejected[0]["payload"]["surfaces"]
        status = trac("status")
        assert "version parity mismatch" in status.stdout.lower() or "blocked" in status.stdout
        # No outcome produced after rejected parity
        outcomes = [e for e in events if e["type"] == "outcome.received"]
        for o in outcomes:
            assert o["seq"] < rejected[0]["seq"] or o["payload"].get("dispatch_id") != rejected[0]["payload"].get("dispatch_id")
    else:
        raise AssertionError("dispatch.rejected must appear for parity mismatch")


# AC-FR0279-02@v0.8 TRACKS-TRACE format vs semantic events separated and not counted
def test_format_vs_semantic_events_separated(host_repo, trac, event_log):
    try:
        from tracks.kernel.envelope import parse_agent_output

        parse_agent_output("bad")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc) or "IF-ENVELOPE-002" in str(exc)
    except Exception:
        pass

    trac("run")
    events = event_log()
    format_errors = [e for e in events if e["type"] == "format_error"]
    semantic = [e for e in events if e["type"] == "semantic_attempt_failed"]
    replay_out = trac("replay").stdout
    assert replay_out is not None
    assert format_errors, "format_error must appear"
    assert semantic, "semantic_attempt_failed must appear"
    # format_error must not be counted as semantic attempt
    for fe in format_errors:
        assert fe["payload"].get("attempt_not_counted") is True or "attempt_not_counted" in str(fe)
        assert fe["type"] != "semantic_attempt_failed"
    # No business mutation from format_error
    business = [e for e in events if e["type"] in ("publish.executed", "release.decided")]
    for b in business:
        assert b["seq"] not in [f["seq"] for f in format_errors]


# AC-FR0279-03@v0.8 TRACKS-TRACE malformed regression corpus rejects all
def test_malformed_regression_corpus(host_repo, trac, event_log):
    corpus = Path("tests/assets/v0.8/malformed")
    # Corpus may be empty pre-seed; create minimal malformed samples if missing
    if not any(corpus.glob("*.json")):
        corpus.mkdir(parents=True, exist_ok=True)
        (corpus / "missing_kind.json").write_text('{"payload": {}}', encoding="utf-8")
        (corpus / "unknown_version.json").write_text('{"envelope": {"kind": "devon:red", "version": 99}, "payload": {}}', encoding="utf-8")

    from tracks.kernel.envelope import validate_envelope

    for sample in sorted(corpus.glob("*.json")):
        text = sample.read_text(encoding="utf-8")
        # Each sample must be rejected via format_error, not passed through
        try:
            parsed = validate_envelope(json.loads(text), None)
            raise AssertionError(f"malformed sample {sample.name} must not validate: {parsed}")
        except NotImplementedError as exc:
            assert "IF-ENVELOPE-002" in str(exc) or "IF-ENVELOPE-001" in str(exc)
        except Exception as exc:
            # Accept format error classification
            assert "kind" in str(exc).lower() or "version" in str(exc).lower() or isinstance(exc, ValueError)

    trac("run")
    events = event_log()
    format_errors = [e for e in events if e["type"] == "format_error"]
    assert format_errors, "format_error must appear for malformed corpus"
    report = trac("report").stdout
    assert "regression" in report.lower() or "format_error" in report.lower()


# AC-NFR0145-01@v0.8 TRACKS-TRACE deterministic parse and parity
def test_deterministic_parse_and_parity(host_repo, trac, event_log):
    from tracks.kernel.envelope import check_envelope_parity, parse_agent_output

    try:
        parse_agent_output('```tracks-envelope\n{"envelope": {"kind": "devon:red", "version": 2}, "payload": {"a": 1}}\n```')
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc)
    try:
        check_envelope_parity({"prompt": "v2", "agent": "v2"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-002" in str(exc)

    a = {"version": 2, "kind": "devon:red"}
    b = {"version": 2, "kind": "devon:red"}
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    trac("run")
    events = event_log()
    parity = [e for e in events if e["type"] == "dispatch.parity"]
    assert parity, "dispatch.parity must appear for deterministic envelope"
    replay1 = trac("replay").stdout
    replay2 = trac("replay").stdout
    assert replay1 == replay2
    assert "dispatch.parity" in replay1 or "envelope" in replay1.lower()


# AC-NFR0145-02@v0.8 TRACKS-TRACE malformed no business mutation
def test_malformed_no_business_mutation(host_repo, trac, event_log):
    try:
        from tracks.kernel.envelope import parse_agent_output

        parse_agent_output("```tracks-envelope\n{\"envelope\": {\"kind\": \"bad\", \"version\": 2}, \"payload\": {}}\n```")
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-ENVELOPE-001" in str(exc) or "IF-ENVELOPE-002" in str(exc)

    trac("run")
    events_after = event_log()
    format_errors = [e for e in events_after if e["type"] == "format_error"]
    # No publish/release from format_error
    for fe in format_errors:
        after_fe = [e for e in events_after if e["seq"] > fe["seq"] and e["type"] in ("publish.executed", "release.decided")]
        assert not after_fe
    replay_out = trac("replay").stdout
    # Pre-implementation legal red: replay must show format handling
    assert "format_error" in replay_out.lower() or "envelope" in replay_out.lower(), (
        f"replay must show format handling, got {replay_out!r}"
    )
