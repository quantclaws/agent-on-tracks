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


# -- executor _emit_verdict threading (M-IMPL path, B23 mirror) ---------


def test_emit_verdict_threads_structured_fields_and_blob(monkeypatch, tmp_path):
    """The M-IMPL legacy emit path must thread review fields + blob ref,
    or Devon/Archer revise re-dispatches carry no findings (D-35 §2.3)."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store

    class _State:
        stage = "M-IMPL"
        review_round = 1

    class _Cmd:
        command_id = "c1"

    body = "## review body\nfull text"
    ex._emit_verdict(
        role="prism",
        verdict="revise",
        result={
            "diff_ref": None,
            "criteria_pack": {"name": "tracks-prism-impl", "version": "0.1"},
            "review_summary": "范围断言不足",
            "findings": [_finding()],
            "review_body": body,
        },
        state=_State(),
        p={},
        cmd=_Cmd(),
        task_id=None,
    )
    prism_events = [e for e in emitted if e["type"] == "prism.verdict"]
    assert prism_events, emitted
    payload = prism_events[0]["payload"]
    assert payload["review_summary"] == "范围断言不足"
    assert payload["findings"] == [_finding()]
    assert payload["review_ref"]
    assert "review_body" not in payload
    from tracks import paths

    blob_path = paths.blobs_dir(store.home) / payload["review_ref"]
    assert blob_path.exists()
    assert body in blob_path.read_text(encoding="utf-8")


# -- PRISM-D35-R1 fixes: requires_diff exemption, merge guard, publish ----


def test_m_test_prism_payload_requires_diff_off_for_structured_revise(
    monkeypatch, tmp_path
):
    """PRISM-D35-R1-02: a JSON-carried revise must not be hard-failed by the
    reviewer no-diff policy — requires_diff yields to structured findings."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    monkeypatch.setattr(
        Executor, "_doc_path", lambda self, doc: tmp_path / doc, raising=True
    )
    ex = object.__new__(Executor)
    ex.store = store
    result = {
        "verdict": "revise",
        "review_summary": "缺陷",
        "findings": [_finding()],
        "review_body": "正文",
    }
    payload = ex._m_test_prism_payload(result, base_sha="x", result_id="r1")
    assert payload["requires_diff"] is False
    assert payload["domain_event"]["payload"]["review_ref"]

    result_threaded_only = {"verdict": "revise"}  # doc-anchored, no JSON
    payload2 = ex._m_test_prism_payload(result_threaded_only, "x", "r2")
    assert payload2["requires_diff"] is True


def test_merge_pass_does_not_override_open_thread_revise(tmp_path):
    """PRISM-D35-R1-ADV4: JSON pass while the reviewer's own threads are open
    keeps the derived revise (threads must be closed first)."""
    from tracks.effects.opencode import OpencodeBackend

    backend = OpencodeBackend(repo=tmp_path, version="v0.6")
    payload = json.dumps({"verdict": "pass"})
    result = {"status": "done", "verdict": "revise"}
    merged = backend._merge_review_payload(
        result,
        role="prism",
        substate="PRISM_REVIEW",
        assignment={"stage": "M-TEST"},
        proc=_proc(payload),
        prompt="p",
        console_input=None,
    )
    assert merged["verdict"] == "revise"

    result_ready = {"status": "done", "verdict": "pass"}
    merged2 = backend._merge_review_payload(
        result_ready,
        role="prism",
        substate="PRISM_REVIEW",
        assignment={"stage": "M-TEST"},
        proc=_proc(payload),
        prompt="p",
        console_input=None,
    )
    assert merged2["verdict"] == "pass"


def test_merge_derived_revise_without_json_fails(tmp_path):
    from tracks.effects.opencode import OpencodeBackend

    backend = OpencodeBackend(repo=tmp_path, version="v0.6")
    merged = backend._merge_review_payload(
        {"status": "done", "verdict": "revise"},
        role="prism",
        substate="PRISM_REVIEW",
        assignment={"stage": "M-TEST"},
        proc=_proc("纯散文"),
        prompt="p",
        console_input=None,
    )
    assert merged["status"] == "failed"
    assert merged["failure_class"] == "manifest_malformed"


# -- PRISM-D35-R2 fixes: dc threading, M-DESIGN passthrough coverage -----


def test_emit_verdict_threads_defect_classification(monkeypatch, tmp_path):
    """PRISM-D35-R2-01: the M-IMPL emit path must carry defect_classification
    or every PRISM_PLAN/FINAL revise falls to the default route."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    emitted = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, ev, payload, **kw: emitted.append({"type": ev, "payload": payload}),
    )
    ex = object.__new__(Executor)
    ex.store = store

    class _State:
        stage = "M-IMPL"
        review_round = 1

    class _Cmd:
        command_id = "c1"

    ex._emit_verdict(
        role="prism",
        verdict="revise",
        result={
            "diff_ref": None,
            "criteria_pack": None,
            "defect_classification": "red_defect",
            "review_summary": "范围断言不足",
            "findings": [_finding()],
            "review_body": "正文",
        },
        state=_State(),
        p={},
        cmd=_Cmd(),
        task_id=None,
    )
    prism_events = [e for e in emitted if e["type"] == "prism.verdict"]
    payload = prism_events[0]["payload"]
    assert payload["defect_classification"] == "red_defect"


def test_design_payload_passthrough_threads_fields(monkeypatch, tmp_path):
    """PRISM-D35-R2-02: M-DESIGN passthrough (AC-FR0240-04) end to end —
    payload builder threads optional fields + blob ref; requires_diff and
    the doc-anchored channel stay unchanged."""
    from tracks.executor.executor import Executor
    from tracks.store import Store

    store = Store(tmp_path)
    monkeypatch.setattr(Executor, "_doc_path", lambda self, doc: tmp_path / doc)
    ex = object.__new__(Executor)
    ex.store = store
    result = {
        "verdict": "revise",
        "review_summary": "设计缺口",
        "findings": [_finding()],
        "review_body": "正文",
    }
    payload = ex._design_payload("PRISM_REVIEW", "prism", result, "x", "r1")
    domain = payload["domain_event"]["payload"]
    assert domain["review_summary"] == "设计缺口"
    assert domain["findings"] == [_finding()]
    assert domain["review_ref"]
    assert "review_body" not in domain
    assert payload["requires_diff"] is True  # doc-anchored channel unchanged
