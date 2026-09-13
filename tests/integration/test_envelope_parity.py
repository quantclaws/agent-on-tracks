"""Integration: envelope parity and malformed regression (FR-0279, IF-ENVELOPE-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.envelope import (
    DEFAULT_PARITY_FACES,
    ENVELOPE_VERSION,
    PARITY_STAGED,
    EnvelopeFormatError,
    build_assignment_envelope,
    check_envelope_parity,
    envelope_schema_digest,
    parse_agent_output,
    validate_envelope,
)
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_CORPUS = Path(__file__).resolve().parents[1] / "assets" / "v0.8" / "malformed"
_MALFORMED_KINDS = {
    "missing_kind.json": "missing_kind",
    "multiple_blocks.json": "multiple_envelope_blocks",
    "no_fence.json": "no_envelope_block",
    "unknown_version.json": "unknown_version",
}


def _start_run(host_repo, trac, summary: str):
    """Init/start a real run and return (store, run_id); caller closes store."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.8", stdin=summary).returncode == 0
    store = Store(paths.tracks_home(host_repo))
    return store, store.active_run()


# AC-FR0279-01@v0.8 TRACKS-TRACE parity mismatch rejects dispatch before execution
def test_parity_mismatch_rejects(host_repo, trac, event_log, monkeypatch):
    from tracks.effects.fake import FakeBackend

    # Kernel contract: a face declaring a different version is a mismatch...
    faces = dict.fromkeys(DEFAULT_PARITY_FACES, ENVELOPE_VERSION)
    faces["agent"] = 1
    verdict = check_envelope_parity(faces)
    assert verdict["consistent"] is False
    assert verdict["mismatches"] == [{"face": "agent", "version": 1, "reason": "version"}]

    # ...and malformed/undeclared face values are mismatches with a reason too.
    invalid = check_envelope_parity({"prompt": "v2", "agent": "v1"})
    assert invalid["consistent"] is False
    assert all("reason" in mismatch for mismatch in invalid["mismatches"])

    # Schema digest is real and deterministic; unknown kinds still reject.
    assert envelope_schema_digest("devon:red") == envelope_schema_digest("devon:red")
    with pytest.raises(EnvelopeFormatError) as exc:
        envelope_schema_digest("nope:nope")
    assert exc.value.kind == "unknown_kind"

    # Runtime contract: a stale declared face blocks the dispatch before any
    # agent execution (dispatch.rejected, never a dispatch.parity success).
    store, run = _start_run(host_repo, trac, "parity mismatch rejects")
    try:
        monkeypatch.setattr(FakeBackend, "envelope_version", 1)
        executor = Executor(store, host_repo, run)
        cmd = Command(kind="dispatch_agent", params={}, command_id="PARITY-MISMATCH")
        assignment = {
            "envelope": build_assignment_envelope("prism:final"),
            "envelope_version": ENVELOPE_VERSION,
        }
        assert executor._dispatch_parity_ok(cmd, None, assignment) is False
        events = [e for e in store.events(run) if e.command_id == cmd.command_id]
        rejected = [e for e in events if e.type == "dispatch.rejected"]
        assert len(rejected) == 1
        assert rejected[0].payload["reason"] == "version_parity_mismatch"
        assert rejected[0].payload["mismatches"]
        assert not any(e.type == "dispatch.parity" for e in events)
        assert not any(e.type == "outcome.received" for e in events)
    finally:
        store.close()


# AC-FR0279-02@v0.8 TRACKS-TRACE format vs semantic events separated and not counted
def test_format_vs_semantic_events_separated(host_repo, trac, event_log):
    with pytest.raises(EnvelopeFormatError) as bad:
        parse_agent_output("bad")
    assert bad.value.kind == "no_envelope_block"

    store, run = _start_run(host_repo, trac, "format vs semantic events")
    try:
        executor = Executor(store, host_repo, run)
        attempts_before = store.state(run).current_attempt
        cmd = Command(kind="dispatch_agent", params={}, command_id="FORMAT-VS-SEMANTIC")
        assignment = {"envelope": build_assignment_envelope("prism:final")}
        handled = executor._format_error_shortcircuit(
            {"raw_output": "bad"}, cmd, None, assignment
        )
        assert handled is True

        events = [e for e in store.events(run) if e.command_id == cmd.command_id]
        assert [e.type for e in events] == ["format_error"]
        assert events[0].payload["kind"] == "no_envelope_block"
        # A format_error is a separate event class, never a semantic attempt,
        # and it consumes no attempt.
        assert all(e.type != "semantic_attempt_failed" for e in events)
        assert store.state(run).current_attempt == attempts_before
    finally:
        store.close()


# AC-FR0279-03@v0.8 TRACKS-TRACE malformed regression corpus rejects all
def test_malformed_regression_corpus(host_repo, trac, event_log):
    samples = sorted(_CORPUS.glob("*.json"))
    assert samples, "malformed corpus must not be empty"
    malformed = [s for s in samples if s.name in _MALFORMED_KINDS]

    # Every malformed sample is rejected with its classified format error:
    # structured variants through validate_envelope, reply-shaped/free-form
    # text through the full reply parse path.
    nested = {"missing_kind.json", "unknown_version.json"}
    for sample in malformed:
        text = sample.read_text(encoding="utf-8")
        with pytest.raises(EnvelopeFormatError) as exc:
            if sample.name in nested:
                validate_envelope(json.loads(text), None)
            else:
                parse_agent_output(text)
        assert exc.value.kind == _MALFORMED_KINDS[sample.name]

    # The corpus also carries a valid control sample the parser must accept.
    control = _CORPUS / "sample.json"
    parsed = validate_envelope(json.loads(control.read_text(encoding="utf-8")), None)
    assert parsed["envelope"]["kind"] == "devon:red"

    # Runtime contract: each malformed reply becomes exactly one format_error,
    # with no semantic attempt and no business mutation.
    store, run = _start_run(host_repo, trac, "malformed corpus")
    try:
        executor = Executor(store, host_repo, run)
        attempts_before = store.state(run).current_attempt
        for idx, sample in enumerate(malformed):
            cmd = Command(
                kind="dispatch_agent", params={}, command_id=f"MALFORMED-{idx}"
            )
            assignment = {"envelope": build_assignment_envelope("prism:final")}
            handled = executor._format_error_shortcircuit(
                {"raw_output": sample.read_text(encoding="utf-8")},
                cmd,
                None,
                assignment,
            )
            assert handled is True
            events = [e for e in store.events(run) if e.command_id == cmd.command_id]
            assert [e.type for e in events] == ["format_error"]
        all_events = list(store.events(run))
        assert len([e for e in all_events if e.type == "format_error"]) == len(malformed)
        assert not any(e.type == "semantic_attempt_failed" for e in all_events)
        assert not any(e.type == "outcome.received" for e in all_events)
        assert store.state(run).current_attempt == attempts_before
    finally:
        store.close()


# AC-NFR0145-01@v0.8 TRACKS-TRACE deterministic parse and parity
def test_deterministic_parse_and_parity(host_repo, trac, event_log):
    devon_red_payload = {
        "phase": "red",
        "changed_paths": ["tests/unit/test_deterministic_parse.py"],
        "commands": [
            {"cmd": "pytest tests/unit", "result": "fail", "output_summary": "assertion"}
        ],
        "manifest_compliance": True,
        "pre_identity": "pre",
        "post_identity": "post",
        "implemented_if_ids": ["IF-ENVELOPE-001"],
    }
    reply = (
        "```tracks-envelope\n"
        '{"envelope": {"kind": "devon:red", "version": 2}, "payload": '
        + json.dumps(devon_red_payload)
        + "}\n```\n"
    )
    first = parse_agent_output(reply)
    assert first["envelope"] == {"kind": "devon:red", "version": 2}
    assert first["payload"] == devon_red_payload
    assert parse_agent_output(reply) == first  # same bytes -> same parse

    # Default parity: the six documented faces are ALL required.
    complete = dict.fromkeys(DEFAULT_PARITY_FACES, ENVELOPE_VERSION)
    assert check_envelope_parity(complete) == {"consistent": True, "mismatches": []}
    incomplete = dict(complete)
    del incomplete["agent"]
    missing = check_envelope_parity(incomplete)
    assert missing["consistent"] is False
    assert missing["mismatches"] == [{"face": "agent", "version": None, "reason": "missing"}]

    # Explicit non-empty required_faces: only those named faces are enforced.
    assert (
        check_envelope_parity({"prompt": ENVELOPE_VERSION}, required_faces=("prompt",))[
            "consistent"
        ]
        is True
    )
    named = check_envelope_parity(
        {"prompt": ENVELOPE_VERSION}, required_faces=("prompt", "agent")
    )
    assert named["consistent"] is False
    assert any(
        m["face"] == "agent" and m["reason"] == "missing" for m in named["mismatches"]
    )

    # Explicit staged opt-out (PARITY_STAGED): undeclared faces skip, only
    # declared mismatches block.
    assert check_envelope_parity({}, required_faces=PARITY_STAGED)["consistent"] is True
    staged = check_envelope_parity({"agent": 1}, required_faces=PARITY_STAGED)
    assert staged == {
        "consistent": False,
        "mismatches": [{"face": "agent", "version": 1, "reason": "version"}],
    }

    # Runtime outlet: re-dispatching the same reply parses identically and
    # never produces a format_error.
    store, run = _start_run(host_repo, trac, "deterministic parse")
    try:
        executor = Executor(store, host_repo, run)
        assignment = {"envelope": build_assignment_envelope("devon:red")}
        parsed = []
        for idx in range(2):
            cmd = Command(
                kind="dispatch_agent", params={}, command_id=f"DETERMINISTIC-{idx}"
            )
            result = {"raw_output": reply}
            assert executor._format_error_shortcircuit(result, cmd, None, assignment) is False
            parsed.append(result["envelope"])
        assert parsed[0] == parsed[1]
        assert not [e for e in event_log(run) if e["type"] == "format_error"]
    finally:
        store.close()


# AC-NFR0145-02@v0.8 TRACKS-TRACE malformed no business mutation
def test_malformed_no_business_mutation(host_repo, trac, event_log):
    malformed = (
        "```tracks-envelope\n"
        '{"envelope": {"kind": "bad", "version": 2}, "payload": {}}\n'
        "```"
    )
    with pytest.raises(EnvelopeFormatError) as exc:
        parse_agent_output(malformed)
    assert exc.value.kind == "unknown_kind"

    store, run = _start_run(host_repo, trac, "malformed no business mutation")
    try:
        executor = Executor(store, host_repo, run)
        attempts_before = store.state(run).current_attempt
        cmd = Command(kind="dispatch_agent", params={}, command_id="MALFORMED-UNKNOWN-KIND")
        assignment = {"envelope": build_assignment_envelope("prism:final")}
        handled = executor._format_error_shortcircuit(
            {"raw_output": malformed}, cmd, None, assignment
        )
        assert handled is True

        events = list(store.events(run))
        format_errors = [e for e in events if e.type == "format_error"]
        assert len(format_errors) == 1
        assert format_errors[0].payload["kind"] == "unknown_kind"
        # A malformed reply never counts as a semantic attempt and never
        # mutates business state.
        assert not any(e.type == "semantic_attempt_failed" for e in events)
        assert not any(e.type == "outcome.received" for e in events)
        assert not any(e.type in ("publish.executed", "release.decided") for e in events)
        assert store.state(run).current_attempt == attempts_before
    finally:
        store.close()
