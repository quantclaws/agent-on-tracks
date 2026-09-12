"""Behavior coverage for ``OpencodeReviewMixin``: structured review/DIAGNOSE
reply extraction (envelope + legacy bare-JSON), strict-channel failure gates
and payload threading.
"""

from __future__ import annotations

import json
import subprocess

from tracks.effects import opencode_review
from tracks.effects.opencode import OpencodeBackend
from tracks.kernel.envelope import ENVELOPE_VERSION, EnvelopeFormatError


def _proc(*texts: str) -> subprocess.CompletedProcess:
    lines = "".join(
        json.dumps({"type": "text", "part": {"text": t}}) + "\n" for t in texts
    )
    return subprocess.CompletedProcess(["opencode"], 0, stdout=lines, stderr="")


def _finding(**overrides) -> dict:
    base = {
        "id": "PRISM-R-01",
        "severity": "blocker",
        "defect_classification": "test_defect",
        "criterion": "1+2",
        "artifact": "tests/integration/test_x.py:1",
        "ac_refs": ["AC-FR0001-01"],
        "summary": "断言降级",
    }
    base.update(overrides)
    return base


def _revise_json(**overrides) -> str:
    payload = {
        "verdict": "revise",
        "defect_classification": "test_defect",
        "review_summary": "缺陷",
        "findings": [_finding()],
        "review_body": "body",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _envelope_reply(payload: dict, kind: str = "prism:review") -> str:
    envelope = {
        "envelope": {"kind": kind, "version": ENVELOPE_VERSION},
        "payload": payload,
    }
    return "```tracks-envelope\n" + json.dumps(envelope, sort_keys=True) + "\n```\n"


def _backend() -> OpencodeBackend:
    backend = object.__new__(OpencodeBackend)
    backend._capture_io = lambda proc, prompt, console_input: {"stub": True}
    return backend


def test_review_channel_failure_malformed_and_verify_final():
    backend = _backend()
    bad = _proc(json.dumps({"verdict": "maybe"}))
    malformed = backend._review_channel_failure(
        "prism", "PRISM_REVIEW", {"stage": "M-TEST"}, bad, "p", None
    )
    assert malformed["status"] == "failed"
    assert "review payload malformed" in malformed["self_report"]

    empty = _proc("plain prose")
    verify = backend._review_channel_failure(
        "prism", "VERIFY_FINAL", {"stage": "M-VERIFY"}, empty, "p", None
    )
    assert "VERIFY_FINAL requires" in verify["audit_evidence"]

    assert (
        backend._review_channel_failure(
            "prism", "PRISM_REVIEW", {"stage": "M-TEST"}, empty, "p", None
        )
        is None
    )
    assert (
        backend._review_channel_failure(
            "devon", "RED", {"stage": "M-IMPL"}, empty, "p", None
        )
        is None
    )


def test_has_structured_findings_and_merge_non_done():
    backend = _backend()
    proc = _proc(_revise_json())
    assert (
        backend._has_structured_findings(
            "prism", "PRISM_REVIEW", {"stage": "M-TEST"}, proc
        )
        is True
    )
    assert (
        backend._has_structured_findings(
            "prism", "PRISM_REVIEW", {"stage": "M-TEST"}, _proc("no json")
        )
        is False
    )

    assert backend._merge_review_payload(
        {"status": "failed"}, "prism", "PRISM_REVIEW", {"stage": "M-TEST"}, proc, "p", None
    ) == {"status": "failed"}


def test_merge_review_payload_missing_payload_and_threading():
    backend = _backend()
    result = {"status": "done", "verdict": "pass"}
    assert (
        backend._merge_review_payload(
            result,
            "prism",
            "PRISM_REVIEW",
            {"stage": "M-TEST"},
            _proc("no json"),
            "p",
            None,
        )["verdict"]
        == "pass"
    )

    threaded = backend._merge_review_payload(
        {"status": "done"},
        "prism",
        "PRISM_REVIEW",
        {"stage": "M-TEST"},
        _proc(json.dumps({"verdict": "pass", "review_summary": "ok"})),
        "p",
        None,
    )
    assert threaded["review_summary"] == "ok"
    assert threaded["verdict"] == "pass"

    design = backend._merge_review_payload(
        {"status": "done", "verdict": "revise"},
        "prism",
        "PRISM_REVIEW",
        {"stage": "M-DESIGN"},
        _proc(json.dumps({"verdict": "pass", "review_summary": "ok"})),
        "p",
        None,
    )
    assert design["review_summary"] == "ok"
    assert design["verdict"] == "revise"


def test_validate_review_finding_delegates():
    assert OpencodeBackend._validate_review_finding("not-a-dict", 0) == (
        "findings[0] must be an object"
    )
    assert OpencodeBackend._validate_review_finding(_finding(), 0) is None


def test_envelope_reply_payload_paths(monkeypatch):
    payload, error, found = OpencodeBackend._envelope_reply_payload(
        _envelope_reply({"verdict": "pass"})
    )
    assert (payload, error, found) == ({"verdict": "pass"}, None, True)

    no_block = OpencodeBackend._envelope_reply_payload(
        "prose mentioning tracks-envelope only"
    )
    assert no_block == (None, None, False)

    broken = OpencodeBackend._envelope_reply_payload(
        "```tracks-envelope\n{\"envelope\":"
    )
    assert broken[2] is True
    assert broken[0] is None and broken[1] is not None

    def raise_format(*_args, **_kwargs):
        raise EnvelopeFormatError("malformed_json", "boom")

    monkeypatch.setattr(opencode_review, "parse_agent_output", raise_format)
    assert OpencodeBackend._envelope_reply_payload("```tracks-envelope\n") == (
        None,
        "envelope format error (malformed_json): boom",
        True,
    )


def test_review_payload_from_reply_envelope_branches():
    payload, error = OpencodeBackend._review_payload_from_reply(
        _envelope_reply({"verdict": "pass"})
    )
    assert error is None and payload["verdict"] == "pass"

    payload, error = OpencodeBackend._review_payload_from_reply(
        _envelope_reply({"summary": "no verdict"})
    )
    assert payload is None and "verdict" in error

    payload, error = OpencodeBackend._review_payload_from_reply(
        "```tracks-envelope\n{\"envelope\":"
    )
    assert payload is None and error is not None

    payload, error = OpencodeBackend._review_payload_from_reply(42)
    assert payload is None and error is None


def test_review_payload_from_reply_rejects_non_mapping_envelope_payload(
    monkeypatch,
):
    monkeypatch.setattr(
        opencode_review,
        "parse_agent_output",
        lambda _text: {"payload": ["not", "a", "mapping"]},
    )
    payload, error = OpencodeBackend._review_payload_from_reply(
        "```tracks-envelope\n{}"
    )
    assert payload is None
    assert "verdict/review_summary/findings/review_body" in error


def test_diagnose_payload_from_reply_paths():
    backend = _backend()
    valid = backend._diagnose_payload_from_reply(
        _envelope_reply(
            {"classification": "impl_defect", "reason": "r", "evidence": "e"},
            kind="prism:diagnose",
        )
    )
    assert valid == {"classification": "impl_defect", "reason": "r", "evidence": "e"}

    invalid = backend._diagnose_payload_from_reply(
        "```tracks-envelope\n{\"envelope\":"
    )
    assert invalid is None

    assert backend._diagnose_payload_from_reply(42) is None


def test_diagnose_classification_from_filters():
    backend = _backend()
    assert backend._diagnose_classification_from(_proc("no json")) is None
    assert (
        backend._diagnose_classification_from(
            _proc(json.dumps({"classification": "nonsense"}))
        )
        is None
    )
    valid = backend._diagnose_classification_from(
        _proc(json.dumps({"classification": "impl_defect", "reason": "r"}))
    )
    assert valid["classification"] == "impl_defect"
