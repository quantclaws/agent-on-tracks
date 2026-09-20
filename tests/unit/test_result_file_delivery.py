"""Unit tests: #174 result-file delivery (#174 mechanism side).

``load_declared_reply`` (kernel/envelope.py) prefers the dispatch's result
file (``json.dump`` delivery, escaping correct by construction) and falls
back to the fenced-block text path; the executor collection face
(``_format_error_shortcircuit``) reads ``assignment.result_file.result_path``,
emits a single ``envelope.file_delivered`` audit event on the file path, and
still runs the declared-kind re-check on a file-delivered envelope.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tracks.executor.executor import Executor
from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    EnvelopeFormatError,
    build_assignment_envelope,
    load_declared_reply,
)
from tracks.kernel.events import Command
from tracks.kernel.machine_outcomes import _handle_failed_outcome, _is_infra_failure
from tracks.store import Store

RUN_ID = "run-result-file-0001"
TASK_ID = "T-7"
CMD_ID = "cmd-result-file-0001"


def _declared_assignment(kind: str = "prism:final", result_path: str | None = None) -> dict:
    task = {"task_id": TASK_ID}
    assignment: dict = {"task": task, "envelope": build_assignment_envelope(kind, task)}
    if result_path is not None:
        assignment["result_file"] = {"result_path": result_path}
    return assignment


def _valid_envelope(kind: str = "prism:final", payload: dict | None = None) -> dict:
    return {
        "envelope": {"kind": kind, "version": ENVELOPE_VERSION},
        "payload": payload if payload is not None else {"verdict": "pass"},
    }


def _fenced(payload: dict, kind: str = "prism:final") -> str:
    return "```tracks-envelope\n" + json.dumps(_valid_envelope(kind, payload)) + "\n```\n"


def _cmd(**params) -> Command:
    return Command(kind="dispatch_agent", params=dict(params), command_id=CMD_ID)


@pytest.fixture()
def exec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    store = Store(tmp_path / "store")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    ex = Executor(store, repo, RUN_ID)
    try:
        yield ex, store
    finally:
        store.close()


# ---------------------------------------------------------------------------
# kernel: load_declared_reply
# ---------------------------------------------------------------------------


def test_file_delivery_wins_and_marks_via(tmp_path):
    """A valid result file is returned with the _delivered_via marker, even
    when the text reply is garbage (the pointer-only final reply shape)."""
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_valid_envelope()), encoding="utf-8")
    parsed = load_declared_reply("result: pointer-only reply", str(path))
    assert parsed["_delivered_via"] == "file"
    assert parsed["envelope"]["kind"] == "prism:final"
    assert parsed["payload"] == {"verdict": "pass"}


def test_file_delivery_does_not_pollute_validated_structure(tmp_path):
    """The marker rides a shallow copy: validate_envelope sees the file
    bytes exactly (no extra key at validation time)."""
    path = tmp_path / "result.json"
    body = _valid_envelope()
    path.write_text(json.dumps(body), encoding="utf-8")
    parsed = load_declared_reply(_fenced({"verdict": "pass"}), str(path))
    assert parsed["_delivered_via"] == "file"
    assert {k: v for k, v in parsed.items() if not k.startswith("_")} == body


def test_missing_path_falls_back_to_text(tmp_path):
    path = tmp_path / "absent.json"
    parsed = load_declared_reply(_fenced({"verdict": "pass"}), str(path))
    assert "_delivered_via" not in parsed
    assert parsed["payload"] == {"verdict": "pass"}


def test_none_and_empty_path_use_text_directly():
    for result_path in (None, ""):
        parsed = load_declared_reply(_fenced({"verdict": "pass"}), result_path)
        assert "_delivered_via" not in parsed


def test_unparseable_file_falls_back_to_text(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("{ not json", encoding="utf-8")
    parsed = load_declared_reply(_fenced({"verdict": "pass"}), str(path))
    assert "_delivered_via" not in parsed
    assert parsed["payload"] == {"verdict": "pass"}


def test_schema_invalid_file_falls_back_to_text(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"envelope": {"kind": "prism:final", "version": 2}}), encoding="utf-8")
    parsed = load_declared_reply(_fenced({"verdict": "pass"}), str(path))
    assert "_delivered_via" not in parsed


def test_both_paths_failed_prefixes_both_failures(tmp_path):
    """File unparseable + text without a block: the raised text-path error
    carries the file reason in its detail prefix."""
    path = tmp_path / "result.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(EnvelopeFormatError) as exc_info:
        load_declared_reply("prose without any block", str(path))
    assert exc_info.value.kind == "no_envelope_block"
    assert exc_info.value.detail.startswith("file delivery failed (unparseable ")
    assert "text fallback:" in exc_info.value.detail


def test_unreadable_file_reports_and_prefixes(tmp_path):
    """A directory at result_path is unreadable-as-file: same prefix rule."""
    with pytest.raises(EnvelopeFormatError) as exc_info:
        load_declared_reply("prose without any block", str(tmp_path))
    assert exc_info.value.detail.startswith("file delivery failed (unreadable ")
    assert "text fallback:" in exc_info.value.detail


# ---------------------------------------------------------------------------
# executor: collection-face wiring
# ---------------------------------------------------------------------------


def test_file_delivery_emits_audit_and_skips_text(exec_env, tmp_path):
    """File path success: envelope attached, one envelope.file_delivered
    audit event (path + sha256 digest), no format_error."""
    ex, store = exec_env
    path = tmp_path / "result.json"
    body = _valid_envelope()
    raw_bytes = json.dumps(body).encode("utf-8")
    path.write_bytes(raw_bytes)
    assignment = _declared_assignment(result_path=str(path))
    result = {"raw_output": "result: pointer-only reply"}
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is False
    assert result["envelope"]["payload"] == {"verdict": "pass"}
    events = list(store.events(RUN_ID))
    assert [e.type for e in events] == ["envelope.file_delivered"]
    assert events[0].payload["path"] == str(path)
    assert events[0].payload["digest"] == hashlib.sha256(raw_bytes).hexdigest()
    assert events[0].payload["task_id"] == TASK_ID
    assert events[0].command_id == CMD_ID


def test_file_delivery_wrong_kind_still_fails_closed(exec_env, tmp_path):
    """File validates but speaks another kind: schema_violation (the audit
    already landed; the reply never rejoins silently)."""
    ex, store = exec_env
    path = tmp_path / "result.json"
    path.write_text(json.dumps(_valid_envelope(kind="prism:review")), encoding="utf-8")
    assignment = _declared_assignment(kind="prism:final", result_path=str(path))
    result = {"raw_output": "result: pointer-only reply"}
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is True
    kinds = [e.type for e in store.events(RUN_ID)]
    assert kinds == ["envelope.file_delivered", "format_error", "verdict.failed"]
    assert "envelope" not in result


def test_legacy_assignment_without_result_file_keeps_text_path(exec_env):
    """No result_file key: the text path runs with no audit event."""
    ex, store = exec_env
    assignment = _declared_assignment()
    result = {"raw_output": _fenced({"verdict": "pass"})}
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_bad_file_falls_back_to_text_without_audit(exec_env, tmp_path):
    """Unparseable file + valid text: text wins, no audit event."""
    ex, store = exec_env
    path = tmp_path / "result.json"
    path.write_text("{ not json", encoding="utf-8")
    assignment = _declared_assignment(result_path=str(path))
    result = {"raw_output": _fenced({"verdict": "pass"})}
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is False
    assert list(store.events(RUN_ID)) == []


def test_devon_green_file_delivery_flows_through(exec_env, tmp_path):
    """Second required kind: devon:green via file joins the normal flow."""
    ex, store = exec_env
    path = tmp_path / "devon.json"
    body = _valid_envelope(
        kind="devon:green",
        payload={
            "phase": "green",
            "changed_paths": [],
            "commands": [{"cmd": "pytest", "result": "pass", "output_summary": "1 passed"}],
            "manifest_compliance": True,
            "pre_identity": "pre",
            "post_identity": "post",
            "r_identity": "r-1",
            "no_change_reason": "already in baseline",
            "implemented_if_ids": ["IF-1"],
        },
    )
    raw_bytes = json.dumps(body).encode("utf-8")
    path.write_bytes(raw_bytes)
    assignment = _declared_assignment(kind="devon:green", result_path=str(path))
    result = {"raw_output": "result: pointer-only reply"}
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="devon", substate="GREEN"), TASK_ID, assignment
    )
    assert handled is False
    assert result["envelope"]["payload"]["phase"] == "green"
    assert [e.type for e in store.events(RUN_ID)] == ["envelope.file_delivered"]


# ---------------------------------------------------------------------------
# executor: crash-without-reply routes infra (generalized DIAGNOSE guard)
# ---------------------------------------------------------------------------


def _crash_result(**extra) -> dict:
    result = {"status": "failed", "failure_class": "non_zero_exit", "self_report": "x"}
    result.update(extra)
    return result


def test_crash_empty_reply_consumes_no_attempt_and_no_verdict(exec_env):
    """prism:final crash, zero reply bytes: no format_error, no
    verdict.failed -- the outcome.received infra classification owns it
    (streak +1, attempt untouched)."""
    ex, store = exec_env
    assignment = _declared_assignment()
    result = _crash_result(returncode=1, raw_output="")
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is False
    assert "envelope" not in result
    assert [e.type for e in store.events(RUN_ID)] == []
    outcome = {
        "role": "prism",
        "status": "failed",
        "failure_class": "non_zero_exit",
        "self_report": "opencode exited 1",
    }
    assert _is_infra_failure(outcome) is True
    from tracks.kernel.machine import State

    state = State(stage="M-IMPL", substate="PRISM_FINAL", current_attempt=2)
    _handle_failed_outcome(state, outcome)
    assert state.infra_failure_streak == 1
    assert state.current_attempt == 2


def test_crash_missing_raw_output_stays_infra(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment(kind="devon:green")
    result = _crash_result(exit_code=1)
    assert "raw_output" not in result
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="devon", substate="GREEN"), TASK_ID, assignment
    )
    assert handled is False
    assert [e.type for e in store.events(RUN_ID)] == []
    outcome = {
        "role": "devon",
        "status": "failed",
        "failure_class": "non_zero_exit",
        "self_report": "opencode exited 1",
    }
    assert _is_infra_failure(outcome) is True
    from tracks.kernel.machine import State

    state = State(stage="M-IMPL", substate="GREEN", current_attempt=1)
    _handle_failed_outcome(state, outcome)
    assert state.infra_failure_streak == 1
    assert state.current_attempt == 1


def test_crash_with_real_bytes_still_judged(exec_env):
    """Crash markers + real reply bytes: content judgement, no exemption."""
    ex, store = exec_env
    assignment = _declared_assignment()
    result = _crash_result(returncode=1, raw_output="prose without any block")
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is True
    kinds = [e.type for e in store.events(RUN_ID)]
    assert kinds == ["format_error", "verdict.failed"]


def test_failed_outcome_without_crash_markers_keeps_legacy_shortcut(exec_env):
    """status=failed + failure_class but no crash signal: the pre-existing
    failure-classification shortcut still owns it (no format_error)."""
    ex, store = exec_env
    assignment = _declared_assignment()
    result = {"status": "failed", "failure_class": "agent_error", "self_report": "x"}
    handled = ex._format_error_shortcircuit(
        result, _cmd(role="prism", substate="PRISM_FINAL"), TASK_ID, assignment
    )
    assert handled is False
    assert list(store.events(RUN_ID)) == []


# ---------------------------------------------------------------------------
# run_loop: infra backoff schedule (30s base, x2, capped)
# ---------------------------------------------------------------------------


def test_infra_backoff_schedule_caps_at_default_900(exec_env, monkeypatch):
    """User decision (OOB 2026-09-20): the model channel never changes; a
    quota-dead gateway waits. 30/60/120/... capped at 900s (15min)."""
    ex, _store = exec_env
    monkeypatch.delenv("TRAC_INFRA_BACKOFF_MAX_SECONDS", raising=False)
    assert [ex._infra_backoff_delay(s) for s in (1, 2, 3, 4, 5, 6, 7, 10)] == [
        30, 60, 120, 240, 480, 900, 900, 900,
    ]


def test_infra_backoff_cap_env_override(exec_env, monkeypatch):
    ex, _store = exec_env
    monkeypatch.setenv("TRAC_INFRA_BACKOFF_MAX_SECONDS", "1800")
    assert [ex._infra_backoff_delay(s) for s in (5, 6, 7)] == [480, 960, 1800]
    # A too-small override never lowers the historic 300s floor.
    monkeypatch.setenv("TRAC_INFRA_BACKOFF_MAX_SECONDS", "60")
    assert [ex._infra_backoff_delay(s) for s in (5, 6)] == [300, 300]
    # Garbage falls back to the 900s default.
    monkeypatch.setenv("TRAC_INFRA_BACKOFF_MAX_SECONDS", "not-an-int")
    assert ex._infra_backoff_delay(6) == 900
