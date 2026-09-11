"""Coverage for the VERIFY_FINAL Prism runtime boundary.

These tests use the real Opencode review parser and the existing discussion
parser/locate contract.  They do not append a target prism.verdict as an
Arrange fact.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from tracks.discuss.locate import locate, token_for
from tracks.discuss.parser import parse_threads
from tracks.effects.opencode import OpencodeBackend
from tracks.executor import m_verify
from tracks.executor.executor import Executor
from tracks.kernel.events import EventEnvelope
from tracks.store import Store

_CANDIDATE = "a" * 40
_FOREIGN = "b" * 40


def _proc(payload: dict) -> subprocess.CompletedProcess:
    text = json.dumps(payload, ensure_ascii=False)
    output = json.dumps({"type": "text", "part": {"text": text}}) + "\n"
    return subprocess.CompletedProcess(["opencode"], 0, stdout=output, stderr="")


def _finding() -> dict:
    return {
        "id": "PRISM-V08-R1-01",
        "severity": "blocker",
        "defect_classification": "behavior",
        "criterion": "FR-0271-03",
        "artifact": "tracks/executor/executor.py:4030",
        "ac_refs": ["AC-FR0271-03"],
        "summary": "最终复审阻断证据未绑定线程",
    }


def _review_payload(**overrides) -> dict:
    payload = {
        "verdict": "revise",
        "review_summary": "需要修复同 candidate 终审绑定",
        "review_body": "## review\nblocking finding",
        "findings": [_finding()],
    }
    payload.update(overrides)
    return payload


def test_real_adapter_maps_verify_final_to_structured_review_contract():
    """VERIFY_FINAL must use the real pass/revise parser and field contract."""
    assignment = {
        "stage": "M-VERIFY",
        "scope": "verify_final",
        "candidate_sha": _CANDIDATE,
        "evidence_digests": {"full_f": "digest"},
    }
    assert OpencodeBackend._review_dispatch("prism", "VERIFY_FINAL", assignment)
    payload, error = OpencodeBackend._prism_review_payload_from(_proc(_review_payload()))
    assert error is None
    assert payload["verdict"] == "revise"
    assert payload["findings"] == [_finding()]


def test_verify_final_assignment_declares_discussion_ref_contract():
    assignment = m_verify.build_prism_final_review_assignment(
        _CANDIDATE, {"full_f": "digest"}
    )
    refs = assignment["verify_final_review"]["discussion_refs"]
    assert refs["required_on"] == "revise"
    assert set(refs["item_fields"]) == {
        "file",
        "thread_id",
        "token",
        "finding_id",
    }
    assert "blocker" in refs["anchor_rule"]


def _executor_with_candidate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "architecture.md").write_text("# Gate\n", encoding="utf-8")
    store = Store(repo / ".tracks")
    store.append(
        "RUN", "v0.8", "candidate.frozen", {"candidate_sha": _CANDIDATE}
    )
    emitted = []
    advanced = []
    monkeypatch.setattr(
        Executor,
        "_emit",
        lambda self, event_type, payload, **kwargs: emitted.append(
            EventEnvelope(
                seq=len(emitted) + 1,
                ts="",
                run_id="RUN",
                version="v0.8",
                type=event_type,
                schema_version=1,
                command_id=kwargs.get("command_id"),
                task_id=kwargs.get("task_id"),
                payload=payload,
            )
        ),
    )
    executor = object.__new__(Executor)
    executor.repo = repo
    executor.store = store
    executor.run_id = "RUN"
    executor.version = "v0.8"
    monkeypatch.setattr(executor, "_advance_verify_chain", lambda *args: advanced.append(args))
    return executor, store, emitted, advanced


def _dispatch(executor, result, *, candidate_sha=_CANDIDATE):
    executor._emit_dispatch_verdict(
        result,
        "prism",
        SimpleNamespace(stage="M-VERIFY"),
        {
            "substate": "VERIFY_FINAL",
            "candidate_sha": candidate_sha,
            "scope": "verify_final",
            "evidence_digests": {"full_f": "digest"},
        },
        SimpleNamespace(
            command_id="VERIFY-1",
            params={
                "candidate_sha": candidate_sha,
                "scope": "verify_final",
                "evidence_digests": {"full_f": "digest"},
            },
        ),
        None,
    )


def test_verify_final_preserves_agent_fields_in_scoped_verdict(tmp_path, monkeypatch):
    executor, store, emitted, _advanced = _executor_with_candidate(tmp_path, monkeypatch)
    result = _review_payload(
        discussion_refs=[{"file": "architecture.md", "thread_id": "T-001"}]
    )
    _dispatch(executor, {"status": "done", **result})
    verdict = next(event.payload for event in emitted if event.type == "prism.verdict")
    assert verdict["scope"] == "verify_final"
    assert verdict["candidate_sha"] == _CANDIDATE
    assert verdict["findings"] == [_finding()]
    assert verdict["discussion_refs"] == result["discussion_refs"]
    store.close()


def test_summary_only_or_fake_ref_is_not_a_discussion_anchor(tmp_path, monkeypatch):
    executor, store, emitted, advanced = _executor_with_candidate(tmp_path, monkeypatch)
    _dispatch(
        executor,
        {
            "status": "done",
            "verdict": "revise",
            "review_summary": "summary without an anchored thread",
        },
    )
    assert emitted[-1].type == "attention.required"
    assert emitted[-1].payload["reason"] == "revise_without_findings"
    assert not advanced

    emitted.clear()
    _dispatch(
        executor,
        {
            "status": "done",
            "verdict": "revise",
            "review_summary": "fake reference",
            "discussion_refs": [{"file": "architecture.md", "thread_id": "T-999"}],
        },
    )
    assert emitted[-1].type == "attention.required"
    assert emitted[-1].payload["reason"] == "revise_without_findings"
    assert not advanced
    emitted.clear()
    _dispatch(
        executor,
        {
            "status": "done",
            **_review_payload(),
            "discussion_refs": [
                {
                    "file": "architecture.md",
                    "thread_id": "T-001",
                    "token": {},
                    "finding_id": _finding()["id"],
                }
            ],
        },
    )
    assert emitted[-1].type == "attention.required"
    assert emitted[-1].payload["reason"] == "revise_without_findings"
    assert not advanced
    store.close()


def test_real_discussion_ref_is_the_supported_anchor_shape(tmp_path, monkeypatch):
    text = "# Gate\n\n> **Prism [open]:** blocking finding\n"
    executor, store, emitted, advanced = _executor_with_candidate(tmp_path, monkeypatch)
    doc = executor.repo / "architecture.md"
    doc.write_text(text, encoding="utf-8")
    current_text = doc.read_text(encoding="utf-8")
    threads = parse_threads(current_text)
    assert len(threads) == 1
    thread = threads[0]
    token = token_for(thread)
    located = locate(current_text, token, thread.thread_id)
    assert located.status == "unique"
    assert thread.thread_id == "T-001"

    _dispatch(
        executor,
        {
            "status": "done",
            **_review_payload(review_summary="anchored finding"),
            "discussion_refs": [
                {
                    "file": "architecture.md",
                    "thread_id": thread.thread_id,
                    "token": token,
                    "finding_id": _finding()["id"],
                }
            ],
        },
    )
    assert any(event.type == "prism.verdict" for event in emitted)
    assert emitted[-1].type == "attention.required"
    assert emitted[-1].payload["reason"] == "verify_final_rejected"
    assert advanced == []
    store.close()


def test_invalid_or_foreign_verify_final_cannot_advance(tmp_path, monkeypatch):
    executor, store, emitted, advanced = _executor_with_candidate(tmp_path, monkeypatch)
    _dispatch(executor, {"status": "done", "verdict": "maybe"}, candidate_sha=_CANDIDATE)
    assert not any(event.type == "prism.verdict" for event in emitted)
    assert not advanced

    emitted.clear()
    _dispatch(
        executor,
        {"status": "done", "verdict": "pass"},
        candidate_sha=_FOREIGN,
    )
    assert not advanced
    assert any(event.type == "attention.required" for event in emitted)

    emitted.clear()
    _dispatch(
        executor,
        {
            "status": "done",
            "verdict": "pass",
            "candidate_sha": _FOREIGN,
        },
        candidate_sha=_CANDIDATE,
    )
    assert not advanced
    assert any(event.type == "attention.required" for event in emitted)
    store.close()
