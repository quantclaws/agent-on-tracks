"""Do not send stale FULL failures to another semantic diagnosis."""

from types import SimpleNamespace

import pytest

from tracks.executor.m_impl_islands import MImplIslandsMixin
from tracks.kernel.machine import State


def _event(seq, kind, **payload):
    return SimpleNamespace(seq=seq, type=kind, payload=payload)


class _Host(MImplIslandsMixin):
    run_id = "RUN"

    def __init__(self):
        self.events = [
            _event(1, "full.executed", passed=False, execution_commit="old"),
            _event(2, "oob.accepted", sha="repair"),
        ]
        self.store = SimpleNamespace(events=lambda _: self.events)
        self.head = "repair"
        self.dirty = {}
        self.issued = []

    def _repo_head_sha(self):
        return self.head

    def _dirty_snapshot(self):
        return self.dirty

    def issue(self, cmd):
        self.issued.append(cmd)


def _state(**changes):
    values = {"stage": "M-IMPL", "substate": "DIAGNOSE", "tasks_total": 1, "tasks_completed": 1,
              "last_failure": {"check": "full_suite"}}
    return State(**(values | changes))


# AC-FR0253-05@v0.6 TRACKS-TRACE accepted repairs require real FULL proof
def test_accepted_repair_requests_existing_full_gate_without_marking_pass():
    host = _Host()
    state = _state()
    assert host._reverify_repaired_full_candidate(state)
    assert [cmd.kind for cmd in host.issued] == ["check_island_2"]
    assert host.issued[0].params["previous_execution_commit"] == "old"
    assert host.issued[0].params["candidate_sha"] == "repair"
    assert state.substate == "DIAGNOSE"
    assert not state.island_2_passed


# AC-FR0253-05@v0.6 TRACKS-TRACE only accepted, clean, completed repairs rewalk
@pytest.mark.parametrize("condition", [
    "unchanged", "unaccepted", "dirty", "legacy", "passed", "pending",
    "incomplete", "reviewing", "other_failure", "awaiting",
])
def test_unverified_or_ineligible_candidate_does_not_rewalk(condition):
    host = _Host()
    state = _state()
    if condition == "unchanged":
        host.head = "old"
    elif condition == "unaccepted":
        host.events.pop()
    elif condition == "dirty":
        host.dirty = {"answer.py": "new-human-edit"}
    elif condition == "legacy":
        host.events[0].payload.pop("execution_commit")
    elif condition == "passed":
        host.events[0].payload["passed"] = True
    elif condition == "pending":
        state.pending = {"kind": "dispatch_agent"}
    elif condition == "incomplete":
        state.tasks_completed = 0
    elif condition == "reviewing":
        state.reviewer_dispatched = True
    elif condition == "other_failure":
        state.last_failure = {"check": "red_invalid"}
    else:
        state.awaiting = "escalation"
    assert not host._reverify_repaired_full_candidate(state)
    assert not host.issued


# AC-FR0253-05@v0.6 TRACKS-TRACE fresh failures are diagnosed, never hot-looped
def test_new_failure_on_repaired_candidate_is_not_reverified_again():
    host = _Host()
    host.events.append(_event(3, "full.executed", passed=False, execution_commit="repair"))
    assert not host._reverify_repaired_full_candidate(_state())
    assert not host.issued
