"""Prism DIAGNOSE fail-closed contract tests.

When a Prism DIAGNOSE dispatch returns no valid ``{classification, reason,
evidence}`` JSON (prose-only violation), the Runtime must NOT fallback-derive
a classification from prose. It emits ``verdict.failed`` with
``check="diagnose_contract_violation"`` so the kernel consumes the attempt
and redispatches Prism (budget exhaustion escalates).
"""

from __future__ import annotations

from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _RecordingExecutor(Executor):
    """Executor stripped to just the verdict-emit surface: ``__init__`` is
    replaced to avoid the Store/repo/backend seam; ``_emit`` captures events."""

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


def _cmd() -> Command:
    return Command(kind="dispatch_agent", params={"role": "prism"}, command_id="C-DIAG")


def test_diagnose_no_verdict_emits_contract_violation():
    """Prism returned prose only (no verdict) -> fail-closed, no fallback."""
    ex = _RecordingExecutor()
    state = _diagnose_state(attempt=0)
    # Prism violated the contract: final reply was prose, no JSON extracted.
    result = {"status": "done", "self_report": "4788 chars of prose, no JSON"}
    cmd = _cmd()

    ex._emit_diagnose_verdict(result, state, cmd, task_id="T-DIAG-1")

    assert len(ex.emitted) == 1
    event_type, payload, command_id = ex.emitted[0]
    assert event_type == "verdict.failed"
    assert payload["check"] == "diagnose_contract_violation"
    assert payload["target_stage"] == "M-IMPL"
    assert payload["attempt"] == 1
    assert payload["task_id"] == "T-DIAG-1"
    assert "contract violation" in payload["reason"]
    assert command_id == "C-DIAG"


def test_diagnose_unknown_classification_emits_contract_violation():
    """Prism emitted a JSON classification outside the five-allowed set."""
    ex = _RecordingExecutor()
    state = _diagnose_state(attempt=1)
    result = {"status": "done", "verdict": "bogus_label"}
    cmd = _cmd()

    ex._emit_diagnose_verdict(result, state, cmd, task_id="T-DIAG-1")

    assert len(ex.emitted) == 1
    event_type, payload, _ = ex.emitted[0]
    assert event_type == "verdict.failed"
    assert payload["check"] == "diagnose_contract_violation"
    assert payload["attempt"] == 2


def test_diagnose_valid_classification_does_not_emit_contract_violation():
    """A valid classification still routes through the normal verdict path."""
    for valid in ("test_defect", "impl_defect", "stub_gap", "ac_gap", "spec_gap"):
        ex = _RecordingExecutor()
        state = _diagnose_state(attempt=0)
        result = {"status": "done", "verdict": valid}
        cmd = _cmd()

        ex._emit_diagnose_verdict(result, state, cmd, task_id="T-DIAG-1")

        assert len(ex.emitted) == 1, f"expected one event for {valid}"
        event_type, payload, _ = ex.emitted[0]
        assert event_type == "verdict.failed"
        assert payload["check"] == valid, f"check should be {valid}, got {payload['check']}"
        assert payload["check"] != "diagnose_contract_violation"
