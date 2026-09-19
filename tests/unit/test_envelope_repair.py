"""Unit tests: OOB 2026-09-19 runtime hotfixes (run 01M2QTJB).

Three bounded repairs, each with positive + boundary cases:

1. ``repair_envelope_block`` (kernel/envelope.py): the agent pasted pytest
   output verbatim into the envelope payload string, so bare double quotes
   (e.g. events["command_id"] == "never") terminated the JSON string early
   -- six consecutive malformed_json (Expecting ',' delimiter at char
   2543-2922, the same char twice via session reuse). The executor
   collection face retries such replies through the bounded repair instead
   of failing closed, emitting an ``envelope.repaired`` audit event.
2. repeat same-kind format_error clears the poisoned work-unit session
   (``session.cleared``) so the re-dispatch cold-starts instead of
   deterministically replaying the same broken answer.
3. an opencode process crash (exit != 0 / non_zero_exit, e.g. exit 1 with
   429 quota noise) yielding an empty reply is infra -- no
   diagnose_contract_violation verdict, no attempt burned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks.executor.executor import Executor
from tracks.executor.verdict_face import _is_process_crash
from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    EnvelopeFormatError,
    build_assignment_envelope,
    extract_envelope_block,
    parse_agent_output,
    repair_envelope_block,
)
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.store import Store

RUN_ID = "run-envelope-repair-0001"
TASK_ID = "T-7"
CMD_ID = "cmd-envelope-repair-0001"


def _declared_assignment(kind: str = "prism:final", task_id: str = TASK_ID) -> dict:
    task = {"task_id": task_id}
    return {"task": task, "envelope": build_assignment_envelope(kind, task)}


def _fenced(block: str) -> str:
    return "```tracks-envelope\n" + block + "\n```\n"


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


def _cmd(**params) -> Command:
    return Command(kind="dispatch_agent", params=dict(params), command_id=CMD_ID)


def _pass_block(note: str) -> str:
    return json.dumps(
        {
            "envelope": {"kind": "prism:final", "version": ENVELOPE_VERSION},
            "payload": {"verdict": "pass", "note": note},
        },
        sort_keys=True,
    )


def _bare_quotes(block: str) -> str:
    """Turn a valid block into the live breakage: strip the JSON escapes so
    payload strings carry bare double quotes (the agent pasted pytest output
    verbatim instead of escaping it)."""
    assert '\\"' in block
    return block.replace('\\"', '"')


# ---------------------------------------------------------------------------
# Fix 1: kernel bounded repair
# ---------------------------------------------------------------------------


def test_repair_bare_quotes_live_shape():
    """The live breakage class: a payload string embedding bare double
    quotes (pytest assertion pasted verbatim) repairs to the intended value
    with one escape_quote audit entry per bare quote."""
    broken = _bare_quotes(_pass_block('see events["command_id"]'))
    with pytest.raises(EnvelopeFormatError) as exc_info:
        parse_agent_output(_fenced(broken))
    assert exc_info.value.kind == "malformed_json"
    parsed, repairs = repair_envelope_block(broken)
    assert parsed is not None
    assert parsed["payload"]["note"] == 'see events["command_id"]'
    assert len(repairs) == 2
    assert all(entry["op"] == "escape_quote" for entry in repairs)
    assert all(isinstance(entry["pos"], int) for entry in repairs)
    assert all(entry["error"] for entry in repairs)


def test_repair_already_valid_block_needs_no_repair():
    block = _pass_block("clean note")
    parsed, repairs = repair_envelope_block(block)
    assert parsed is not None
    assert parsed["payload"] == {"verdict": "pass", "note": "clean note"}
    assert repairs == []


def test_repair_budget_caps_at_three_escapes():
    """Four bare quotes need four escapes: after exactly three the budget
    is spent and the block stays a format_error (fail-closed, no spin)."""
    broken = _bare_quotes(json.dumps({"a": '1 ["k1"] and ["k2"]'}, sort_keys=True))
    parsed, repairs = repair_envelope_block(broken)
    assert parsed is None
    assert len(repairs) == 3
    assert [entry["op"] for entry in repairs] == ["escape_quote"] * 3


@pytest.mark.parametrize(
    "block",
    [
        '{"a": }',  # Expecting value: not the truncation class
        "not json at all",  # Expecting value at char 0
        '{} extra',  # Extra data: well-formed tokens, wrong shape
    ],
    ids=["expecting-value", "not-json", "extra-data"],
)
def test_repair_ignores_non_structural_breaks(block):
    parsed, repairs = repair_envelope_block(block)
    assert parsed is None
    assert repairs == []


def test_repair_valid_json_but_schema_violation_fails():
    """Parses yet violates the payload schema: repair failure, not a pass."""
    block = json.dumps(
        {
            "envelope": {"kind": "prism:review", "version": ENVELOPE_VERSION},
            "payload": {"verdict": "revise"},  # revise requires summary/body/findings
        },
        sort_keys=True,
    )
    parsed, repairs = repair_envelope_block(block)
    assert parsed is None
    assert repairs == []


def test_repair_break_without_adjacent_quote_gives_up():
    """A structural error whose break carries no adjacent quote (stray
    prose after a closed string) is unrepairable -- returned at once,
    never spun on."""
    parsed, repairs = repair_envelope_block('{"a": "x" garbage}')
    assert parsed is None
    assert repairs == []


def test_extract_envelope_block_shapes():
    # raw slice mirrors parse_agent_output (json.loads tolerates the
    # surrounding newline); repair positions stay comparable to the decode
    # error char offsets.
    assert extract_envelope_block(_fenced('{"a": 1}')).strip() == '{"a": 1}'
    assert extract_envelope_block("no fence here") is None
    assert extract_envelope_block("```tracks-envelope\n{\"a\": 1}\n") is None
    assert extract_envelope_block(_fenced('{"a": 1}') + _fenced('{"b": 2}')) is None
    assert extract_envelope_block(None) is None
    assert extract_envelope_block(42) is None


# ---------------------------------------------------------------------------
# Fix 1: executor collection integration
# ---------------------------------------------------------------------------


def test_declared_repairable_reply_flows_through_with_audit(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    raw = _fenced(_bare_quotes(_pass_block('see events["command_id"]')))
    result = {"raw_output": raw}
    handled = ex._format_error_shortcircuit(
        result,
        _cmd(role="prism", substate="PRISM_FINAL"),
        TASK_ID,
        assignment,
    )
    assert handled is False  # repaired: normal flow, not a format_error
    assert result["envelope"]["payload"]["note"] == 'see events["command_id"]'
    events = list(store.events(RUN_ID))
    assert [e.type for e in events] == ["envelope.repaired"]
    audit = events[0].payload
    assert len(audit["repairs"]) == 2
    assert "Expecting ',' delimiter" in audit["original_error"]
    assert audit["task_id"] == TASK_ID


def test_repaired_wrong_kind_still_fails_closed(exec_env):
    """Repair succeeds but the envelope speaks another kind: schema_violation
    (not a silent accept, not the original malformed_json)."""
    ex, store = exec_env
    review_block = _bare_quotes(
        json.dumps(
            {
                "envelope": {"kind": "prism:review", "version": ENVELOPE_VERSION},
                "payload": {"verdict": "pass", "note": 'see ["x"]'},
            },
            sort_keys=True,
        )
    )
    result = {"raw_output": _fenced(review_block)}
    handled = ex._format_error_shortcircuit(
        result,
        _cmd(role="prism", substate="PRISM_FINAL"),
        TASK_ID,
        _declared_assignment(kind="prism:final"),
    )
    assert handled is True
    events = list(store.events(RUN_ID))
    assert [e.type for e in events] == [
        "envelope.repaired",
        "format_error",
        "verdict.failed",
    ]
    assert events[1].payload["kind"] == "schema_violation"
    assert events[2].payload["check"] == "reply_format_error"
    assert "envelope" not in result


def test_declared_unrepairable_reply_keeps_format_error_path(exec_env):
    ex, store = exec_env
    assignment = _declared_assignment()
    result = {"raw_output": _fenced("not json")}
    handled = ex._format_error_shortcircuit(
        result,
        _cmd(role="prism", substate="PRISM_FINAL"),
        TASK_ID,
        assignment,
    )
    assert handled is True
    kinds = [e.type for e in store.events(RUN_ID)]
    assert kinds == ["format_error", "verdict.failed"]


# ---------------------------------------------------------------------------
# Fix 2: repeat same-kind format_error clears the poisoned session
# ---------------------------------------------------------------------------


def _stub_clearing_backend():
    calls: list[str] = []

    def clear_workunit_session(key: str) -> bool:
        calls.append(key)
        return True

    return calls, clear_workunit_session


def test_repeat_same_kind_clears_session_and_audits(exec_env):
    ex, store = exec_env
    calls, clear = _stub_clearing_backend()
    ex.backend.clear_workunit_session = clear
    assignment = _declared_assignment()
    cmd = _cmd(role="prism", substate="PRISM_FINAL")
    raw = {"raw_output": _fenced("not json")}  # malformed_json, unrepairable
    assert ex._format_error_shortcircuit(dict(raw), cmd, TASK_ID, assignment) is True
    assert calls == []  # first breakage: no history, session kept
    assert ex._format_error_shortcircuit(dict(raw), cmd, TASK_ID, assignment) is True
    assert calls == ["prism:PRISM_FINAL:" + TASK_ID]
    kinds = [e.type for e in store.events(RUN_ID)]
    assert kinds == [
        "format_error",
        "verdict.failed",
        "session.cleared",
        "format_error",
        "verdict.failed",
    ]
    cleared = [e for e in store.events(RUN_ID) if e.type == "session.cleared"][0]
    assert cleared.payload == {
        "key": "prism:PRISM_FINAL:" + TASK_ID,
        "reason": "repeat_format_error:malformed_json",
    }
    assert cleared.task_id == TASK_ID


def test_cross_kind_sequence_keeps_session(exec_env):
    """malformed_json then no_envelope_block is independent breakage, not a
    deterministic replay: no clear, no audit."""
    ex, store = exec_env
    calls, clear = _stub_clearing_backend()
    ex.backend.clear_workunit_session = clear
    assignment = _declared_assignment()
    cmd = _cmd(role="prism", substate="PRISM_FINAL")
    assert ex._format_error_shortcircuit(
        {"raw_output": _fenced("not json")}, cmd, TASK_ID, assignment
    ) is True
    assert ex._format_error_shortcircuit({"raw_output": ""}, cmd, TASK_ID, assignment) is True
    assert calls == []
    assert [e.type for e in store.events(RUN_ID)] == [
        "format_error",
        "verdict.failed",
        "format_error",
        "verdict.failed",
    ]


def test_undeclared_replies_never_touch_sessions(exec_env):
    ex, store = exec_env
    calls, clear = _stub_clearing_backend()
    ex.backend.clear_workunit_session = clear
    cmd = _cmd(role="prism", substate="PRISM_FINAL")
    raw = {"raw_output": _fenced("not json")}
    assert ex._format_error_shortcircuit(dict(raw), cmd, TASK_ID, None) is False
    assert ex._format_error_shortcircuit(dict(raw), cmd, TASK_ID, None) is False
    assert calls == []
    assert list(store.events(RUN_ID)) == []


def test_repeat_without_clearing_backend_stays_silent(exec_env):
    """A backend without clear_workunit_session (older/test doubles) keeps
    the exact legacy event sequence on repeats."""

    class _LegacyBackend:
        pass

    ex, store = exec_env
    ex.backend = _LegacyBackend()
    assignment = _declared_assignment()
    cmd = _cmd(role="prism", substate="PRISM_FINAL")
    raw = {"raw_output": _fenced("not json")}
    assert ex._format_error_shortcircuit(dict(raw), cmd, TASK_ID, assignment) is True
    assert ex._format_error_shortcircuit(dict(raw), cmd, TASK_ID, assignment) is True
    assert [e.type for e in store.events(RUN_ID)] == [
        "format_error",
        "verdict.failed",
        "format_error",
        "verdict.failed",
    ]


# ---------------------------------------------------------------------------
# Fix 3: process crash routes infra, never contract-violation
# ---------------------------------------------------------------------------


class _RecordingExecutor(Executor):
    """Executor stripped to the verdict-emit surface (mirrors
    test_executor_diagnose_contract.py)."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict, str | None]] = []

    def _emit(self, type, payload, command_id=None, task_id=None):
        self.emitted.append((type, dict(payload), command_id))
        return None


def _diagnose_state(attempt: int = 0) -> State:
    return State(
        stage="M-IMPL",
        substate="DIAGNOSE",
        current_attempt=attempt,
        current_task_id="T-DIAG-1",
    )


def _diagnose_cmd() -> Command:
    return Command(kind="dispatch_agent", params={"role": "prism"}, command_id="C-DIAG")


def test_is_process_crash_predicate():
    assert _is_process_crash(
        {"status": "failed", "failure_class": "non_zero_exit"}
    ) is True
    assert _is_process_crash({"status": "failed", "returncode": 1}) is True
    assert _is_process_crash({"status": "failed", "exit_code": 1}) is True
    assert _is_process_crash(
        {"status": "failed", "failure_class": "provider_unavailable"}
    ) is True
    assert _is_process_crash({"status": "done", "verdict": "test_defect"}) is False
    assert _is_process_crash({"status": "done", "self_report": "prose"}) is False
    assert _is_process_crash({"status": "done", "returncode": 0}) is False
    assert _is_process_crash({}) is False


def test_crash_empty_reply_emits_no_contract_violation():
    """Returncode 1 + empty stdout (the live 429/exit-1 shape): the verdict
    face stays silent -- the outcome.received infra classification owns the
    turn (streak + backoff re-dispatch, no attempt burned)."""
    ex = _RecordingExecutor()
    result = {
        "status": "failed",
        "returncode": 1,
        "self_report": "opencode exited 1",
        "failure_class": "non_zero_exit",
        "agent_io": {"stdout": "", "stderr": "429 quota exhausted"},
    }
    ex._emit_diagnose_verdict(result, _diagnose_state(), _diagnose_cmd(), "T-DIAG-1")
    assert ex.emitted == []


def test_crash_marker_without_returncode_emits_nothing():
    ex = _RecordingExecutor()
    result = {
        "status": "failed",
        "failure_class": "non_zero_exit",
        "self_report": "opencode exited 1",
    }
    ex._emit_diagnose_verdict(result, _diagnose_state(), _diagnose_cmd(), "T-DIAG-1")
    assert ex.emitted == []


def test_crash_with_complete_reply_still_judges_normally():
    """A failed turn that still delivered a valid classification is judged
    on the classification -- the crash guard only gates the violation
    branch, never a real verdict."""
    ex = _RecordingExecutor()
    result = {"status": "failed", "failure_class": "non_zero_exit", "verdict": "test_defect"}
    ex._emit_diagnose_verdict(result, _diagnose_state(), _diagnose_cmd(), "T-DIAG-1")
    assert len(ex.emitted) == 1
    event_type, payload, _ = ex.emitted[0]
    assert event_type == "verdict.failed"
    assert payload["check"] == "test_defect"


def test_genuine_prose_violation_still_fails_closed():
    """No crash markers + no classification: the contract violation still
    fires exactly as before (no behavior change for real violations)."""
    ex = _RecordingExecutor()
    result = {"status": "done", "self_report": "4788 chars of prose, no JSON"}
    ex._emit_diagnose_verdict(result, _diagnose_state(), _diagnose_cmd(), "T-DIAG-1")
    assert len(ex.emitted) == 1
    event_type, payload, _ = ex.emitted[0]
    assert event_type == "verdict.failed"
    assert payload["check"] == "diagnose_contract_violation"
