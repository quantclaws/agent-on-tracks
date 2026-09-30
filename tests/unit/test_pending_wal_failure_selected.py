"""#216 (2026-09-30): failure.selected must preserve the pending-WAL record.

Live fire (run 01M3E7SAANXKW1V73W8B8Q3G86, T-007 PRISM_FINAL, seq 3195-3197):
a network switch killed the in-flight Prism stream right after the dispatch's
audit burst landed. The burst is command.issued -> failure.selected ->
failure.injected, all tagged with the dispatch's own command_id. B40's
_PENDING_PRESERVING_AUDIT covered failure.injected but NOT failure.selected,
so the FIRST audit event of every dispatch cleared the write-ahead pending
record. Mid-dispatch process death then stranded the run permanently:
pending=None (nothing for D-13 _recover to re-issue) while
reviewer_dispatched stayed True (decide() returns None, "awaiting verdict"),
and the drive loop exited silently with no park, no escalation, no failure.
"""

from tracks.kernel.machine import State, apply
from tests.unit.helpers import ev

_CID = "01M3RTYDQNCKAHDTJSV2G7FQ8X"


def _dispatched_state() -> State:
    """Replay the live burst: dispatch issued, audit events landed, process
    died before any outcome. Pending must survive for D-13 recovery."""
    s = State(run_id="RUN", stage="M-IMPL", substate="PRISM_FINAL")
    s = apply(s, ev(1, "command.issued", {"command": {
        "command_id": _CID, "kind": "dispatch_agent", "params": {"role": "prism"},
    }}, command_id=_CID))
    s = apply(s, ev(2, "failure.selected", {
        "failure_id": "3-devon-59", "role": "prism", "rule": "round/source-latest-unacked-lookback-3",
    }, command_id=_CID))
    s = apply(s, ev(3, "failure.injected", {
        "failure_id": "3-devon-59", "role": "prism", "task_id": "T-007",
    }, command_id=_CID))
    return s


def test_failure_selected_preserves_pending_wal():
    s = _dispatched_state()
    assert s.pending is not None, (
        "failure.selected cleared the pending WAL record: mid-dispatch process "
        "death can never be recovered (D-13 _recover no-ops) — #216"
    )
    assert s.pending.get("kind") == "dispatch_agent"
    assert s.pending.get("command_id") == _CID


def test_terminal_events_still_clear_pending():
    """The fix must not over-preserve: a dispatch-TERMINAL event (format_error)
    keeps the legacy clear semantics so a dead reply cannot be re-issued."""
    s = _dispatched_state()
    s = apply(s, ev(4, "format_error", {
        "detail": "no fenced tracks-envelope block in reply", "task_id": "T-007",
    }, command_id=_CID))
    assert s.pending is None


def test_foreign_command_id_failure_selected_still_clears():
    """Preservation is scoped to the dispatch's own command_id: a failure
    selection belonging to another command must keep clearing pending."""
    s = State(run_id="RUN", stage="M-IMPL", substate="PRISM_FINAL")
    s = apply(s, ev(1, "command.issued", {"command": {
        "command_id": _CID, "kind": "dispatch_agent", "params": {"role": "prism"},
    }}, command_id=_CID))
    s = apply(s, ev(2, "failure.selected", {"failure_id": "x-1"}, command_id="OTHER"))
    assert s.pending is None


def test_failure_selected_without_command_id_clears_pending():
    """OOB-216-A1 boundary pin: an emission WITHOUT command_id cannot match
    the preserve scope (machine.py requires ev.command_id is not None) and
    keeps the legacy clear semantics. Today every product emission carries a
    non-None dispatch cid (dispatch.py:694); this test exists so a future
    emission point that drops the cid fails loudly here instead of silently
    regressing the WAL-strand hazard."""
    s = State(run_id="RUN", stage="M-IMPL", substate="PRISM_FINAL")
    s = apply(s, ev(1, "command.issued", {"command": {
        "command_id": _CID, "kind": "dispatch_agent", "params": {"role": "prism"},
    }}, command_id=_CID))
    s = apply(s, ev(2, "failure.selected", {"failure_id": "x-1"}, command_id=None))
    assert s.pending is None
