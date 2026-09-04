"""Unit: M1-S1 contract_conflict routing into the RULING channel.

The kernel must route a contract_conflict gate failure into RULING (the
Archer paired-delta channel) without consuming the writer attempt budget,
and RULING's decide must dispatch Archer exactly once.
"""

from __future__ import annotations

from tracks.kernel import m_impl
from tracks.kernel.machine import State


def _state_in_green_gate() -> State:
    events = []
    # walk a minimal M-IMPL run into GREEN_GATE via projection primitives
    from tracks.kernel.machine import EventEnvelope, apply

    def emit(st: State, etype: str, payload: dict) -> None:
        ev = EventEnvelope(
            seq=len(events) + 1,
            ts="2026-09-05T00:00:00Z",
            run_id="run",
            version="v0.8",
            type=etype,
            schema_version=1,
            command_id=None,
            task_id=None,
            payload=payload,
        )
        events.append(ev)
        apply(st, ev)

    st = State()
    emit(st, "stage.entered", {"stage": "M-IMPL"})
    return st


def test_route_m_impl_gate_failure_contract_conflict_sets_ruling():
    st = _state_in_green_gate()
    st.substate = "GREEN_GATE"
    st.current_attempt = 1
    m_impl._route_m_impl_gate_failure(st, "contract_conflict", None)
    assert st.substate == "RULING"
    # NOT a writer failure: the attempt budget must stay intact.
    assert st.current_attempt == 1


def test_decide_m_impl_ruling_dispatches_archer_once():
    st = _state_in_green_gate()
    st.substate = "RULING"
    st.doc_dispatched = False
    cmd = m_impl._decide_m_impl_ruling(st)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "RULING"
    # second decide: awaiting the outcome
    st.doc_dispatched = True
    assert m_impl._decide_m_impl_ruling(st) is None


def test_ruling_dispatch_carries_oscillation_evidence():
    st = _state_in_green_gate()
    st.substate = "RULING"
    st.doc_dispatched = False
    st.last_failure = {
        "check": "contract_conflict",
        "reason": "anchor oscillation (S1)",
        "evidence": '{"oscillation": {"healed": ["a"], "newly_red": ["b"]}}',
    }
    cmd = m_impl._m_impl_archer_ruling_dispatch(st)
    assert cmd.params["evidence"]["check"] == "contract_conflict"
    assert "oscillation" in cmd.params["evidence"]["evidence"]


def test_outcome_done_ruling_routes_diagnose():
    st = _state_in_green_gate()
    st.substate = "RULING"
    m_impl._on_m_impl_outcome_done(st)
    assert st.substate == "DIAGNOSE"
    assert st.diagnose_classification is None
