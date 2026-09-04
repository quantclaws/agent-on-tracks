"""Unit: M5 dispatch-side assignment hard budget (convergence plan 2026-09-05).

An assignment JSON over TRAC_ASSIGNMENT_BUDGET (default 8KB) is a
structural task-graph defect: it is rejected before any backend I/O as
plan_defect (scope replan, no agent attempt burned) -- revision
archaeology and escalation add-ons belong in the event log, not the
prompt (b92: 200-600k token dispatches with healthy cards <2KB).
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.executor.executor import Executor, _assignment_budget
from tracks.kernel import m_impl
from tracks.kernel.machine import State

# -- budget knob ---------------------------------------------------------------


def test_assignment_budget_default_8kb(monkeypatch):
    monkeypatch.delenv("TRAC_ASSIGNMENT_BUDGET", raising=False)
    assert _assignment_budget() == 8192


def test_assignment_budget_env_override_and_disable(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "2048")
    assert _assignment_budget() == 2048
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "0")
    assert _assignment_budget() == 0


def test_assignment_budget_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "not-a-number")
    assert _assignment_budget() == 8192


# -- kernel routing ------------------------------------------------------------


def test_route_plan_defect_gate_failure_replans_without_attempt():
    st = State()
    st.substate = "GREEN_GATE"
    st.current_attempt = 2
    st.doc_dispatched = True
    m_impl._route_m_impl_gate_failure(st, "plan_defect", None)
    assert st.substate == "PLANNING"
    # NOT a writer failure: the attempt budget must stay intact.
    assert st.current_attempt == 2


# -- dispatch-side rejection ----------------------------------------------------


class _BudgetStub(Executor):
    """Executor with the dispatch pipeline's heavy halves stubbed out."""

    def __init__(self):  # noqa: D107 - test stub
        self.emitted: list[tuple[str, dict]] = []
        self.backend_calls: list[dict] = []
        self.run_id = "run"
        self.repo = None

    def _assignment_with_evidence(self, assignment, params):
        return assignment

    def _invalid_m_impl_assignment(self, role, substate, assignment):
        return None

    def _release_empty_hotfix_shield(self, cmd, state, task_id, role, substate, assignment):
        return False

    def _reject_invalid_test_tasks(self, state, role, substate, assignment, cmd, task_id):
        return False

    def _dispatch_agent_backend(self, cmd, state, task_id, role, substate, doc, doc_path, assignment):
        self.backend_calls.append({"role": role, "assignment": assignment})

    def _emit(self, type, payload, **kwargs):
        self.emitted.append((type, payload))


def _cmd(assignment: dict) -> SimpleNamespace:
    return SimpleNamespace(
        params={
            "role": "shield",
            "substate": "WRITE",
            "doc": None,
            "assignment": assignment,
        },
        command_id="c1",
    )


def _state() -> State:
    st = State()
    st.stage = "M-TEST"
    st.substate = "WRITE"
    st.current_attempt = 0
    st.current_task_id = "T-1"
    return st


def test_oversized_assignment_rejected_as_plan_defect(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "256")
    stub = _BudgetStub()
    big = {"description": "x" * 512}
    stub._do_dispatch_agent(_cmd(big), _state(), "T-1", False)
    assert stub.backend_calls == []
    assert stub.emitted and stub.emitted[0][0] == "verdict.failed"
    assert stub.emitted[0][1]["check"] == "plan_defect"
    assert "hard budget" in stub.emitted[0][1]["reason"]


def test_budgeted_assignment_dispatches(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "8192")
    stub = _BudgetStub()
    small = {"description": "do the thing"}
    stub._do_dispatch_agent(_cmd(small), _state(), "T-1", False)
    assert len(stub.backend_calls) == 1
    assert stub.backend_calls[0]["assignment"] is small
    assert stub.emitted == []


def test_budget_zero_disables_check(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "0")
    stub = _BudgetStub()
    big = {"description": "x" * 4096}
    stub._do_dispatch_agent(_cmd(big), _state(), "T-1", False)
    assert len(stub.backend_calls) == 1
