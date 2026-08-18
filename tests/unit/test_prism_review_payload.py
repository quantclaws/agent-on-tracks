"""Unit tests for the D-35 (SC-D35 §2.2) structured review payload contract.

Covers extraction/validation of the Prism review JSON (M-TEST/M-IMPL
channel), the machine-side review_ref threading into last_failure, and the
publish-side field threading that previously dropped findings (B23: the
verdict event carried no review fields, leaving revise re-dispatch evidence
empty — live run 01M0AMKV PRISM rounds 1-3, 2026-08-18).
"""

from __future__ import annotations

import json
import subprocess

from tracks.effects.opencode import OpencodeBackend
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import State, apply


def _proc(*texts: str) -> subprocess.CompletedProcess:
    lines = "".join(
        json.dumps({"type": "text", "part": {"text": t}}) + "\n" for t in texts
    )
    return subprocess.CompletedProcess(["opencode"], 0, stdout=lines, stderr="")


def _finding(**overrides) -> dict:
    base = {
        "id": "PRISM-V06-R1-01",
        "severity": "blocker",
        "defect_classification": "test_defect",
        "criterion": "1+2",
        "artifact": "tests/integration/test_x.py:40-43",
        "ac_refs": ["AC-FR0245-01"],
        "summary": "断言降级",
    }
    base.update(overrides)
    return base


def _revise_payload(**overrides) -> dict:
    base = {
        "verdict": "revise",
        "defect_classification": "test_defect",
        "review_summary": "三条断言质量缺陷",
        "findings": [_finding()],
        "review_body": "## 完整评审正文\n...",
    }
    base.update(overrides)
    return base


# -- extraction / validation -------------------------------------------


def test_valid_revise_payload_extracts() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc("分析正文", json.dumps(_revise_payload(), ensure_ascii=False))
    )
    assert error is None
    assert payload is not None
    assert payload["verdict"] == "revise"
    assert payload["findings"][0]["id"] == "PRISM-V06-R1-01"


def test_no_json_is_legal_absence() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc("纯散文回复，无 JSON")
    )
    assert payload is None
    assert error is None


def test_bad_verdict_rejected() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc(json.dumps({"verdict": "maybe"}))
    )
    assert payload is None
    assert "verdict" in error


def test_revise_requires_summary() -> None:
    p = _revise_payload()
    del p["review_summary"]
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "review_summary" in error


def test_revise_summary_140_limit() -> None:
    p = _revise_payload(review_summary="x" * 141)
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "140" in error


def test_revise_requires_findings() -> None:
    p = _revise_payload(findings=[])
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "findings" in error


def test_finding_missing_field_rejected() -> None:
    bad = _finding()
    del bad["ac_refs"]
    p = _revise_payload(findings=[bad])
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "ac_refs" in error


def test_finding_summary_140_limit() -> None:
    p = _revise_payload(findings=[_finding(summary="y" * 141)])
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "140" in error


def test_revise_requires_review_body() -> None:
    p = _revise_payload()
    del p["review_body"]
    _, error = OpencodeBackend._prism_review_payload_from(_proc(json.dumps(p)))
    assert "review_body" in error


def test_pass_payload_needs_no_findings() -> None:
    payload, error = OpencodeBackend._prism_review_payload_from(
        _proc(json.dumps({"verdict": "pass"}))
    )
    assert error is None
    assert payload is not None
    assert payload["verdict"] == "pass"


# -- machine: review_ref into last_failure (AC-03) ----------------------


def _prism_event(payload: dict) -> EventEnvelope:
    return EventEnvelope(
        seq=1,
        ts="2026-08-19T00:00:00+00:00",
        run_id="r",
        version="v0.6",
        type="prism.verdict",
        schema_version=1,
        command_id=None,
        task_id=None,
        payload=payload,
    )


def test_machine_last_failure_carries_review_ref() -> None:
    s = State()
    s.stage = "M-TEST"
    s.substate = "PRISM_REVIEW"
    apply(
        s,
        _prism_event(
            {
                "verdict": "revise",
                "review_summary": "缺陷摘要",
                "findings": [_finding()],
                "review_ref": "deadbeef",
            }
        ),
    )
    assert s.last_failure is not None
    assert s.last_failure["review_ref"] == "deadbeef"
    assert s.last_failure["reason"] == "缺陷摘要"
    assert s.last_failure["evidence"] == [_finding()]


def test_machine_old_event_without_ref_replays_clean() -> None:
    s = State()
    s.stage = "M-TEST"
    s.substate = "PRISM_REVIEW"
    apply(s, _prism_event({"verdict": "revise", "defect_classification": None}))
    assert s.last_failure is not None
    assert "review_ref" not in s.last_failure
