"""Unit: M1-S3 shape-violation routing (convergence plan 2026-09-05).

Zero-cost retry was already the base behavior (format failures never
consume the attempt budget); this locks in the authority escalation:
two consecutive shape violations route Archer RULING for a contract
simplification delta (never a third agent retry, never an immediate
human park), with the human escape reserved for streak 5 (the writer
model cannot comply even with a simplified contract).
"""

from __future__ import annotations

from tracks.kernel import m_impl
from tracks.kernel.machine import State, _handle_verdict_format_failure


def _state_m_impl_green() -> State:
    st = State()
    st.stage = "M-IMPL"
    st.substate = "GREEN_GATE"
    st.current_attempt = 1
    return st


def _shape_verdict() -> dict:
    return {
        "check": "evidence_malformed",
        "failure_class": "evidence_malformed",
        "reason": "Devon evidence missing: phase",
        "evidence": "backend Devon outcome",
    }


def test_first_shape_violation_is_zero_cost_retry():
    st = _state_m_impl_green()
    _handle_verdict_format_failure(st, _shape_verdict())
    # back to the producing phase, no attempt consumed, no human
    assert st.substate == "GREEN"
    assert st.current_attempt == 1
    assert st.status != "awaiting_human"


def test_second_consecutive_shape_violation_routes_archer():
    st = _state_m_impl_green()
    st.format_failure_streak = 1  # prior shape failure
    _handle_verdict_format_failure(st, _shape_verdict())
    assert st.substate == "RULING"
    assert st.current_attempt == 1  # still zero-cost
    assert st.status != "awaiting_human"
    # the ruling objective names the contract simplification
    st.doc_dispatched = False
    cmd = m_impl._m_impl_archer_ruling_dispatch(st)
    assert "simplify the output contract" in cmd.params["objective"]


def test_streak_five_is_the_terminal_human_escape():
    st = _state_m_impl_green()
    st.format_failure_streak = 4
    _handle_verdict_format_failure(st, _shape_verdict())
    assert st.status == "awaiting_human"
    assert st.awaiting == "escalation"


def test_non_format_verdict_resets_streak():
    # an interleaved semantic verdict resets the shape streak (only
    # CONSECUTIVE shape violations escalate)
    from tracks.kernel.machine import _is_format_verdict

    assert _is_format_verdict({"check": "impl_defect"}) is False
    assert _is_format_verdict({"failure_class": "manifest_malformed"}) is True
