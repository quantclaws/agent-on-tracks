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


# ---------------------------------------------------------------------------
# #214 rev3 (2026-09-30): the RULING verdict routes DIRECTLY at application
# time; decide() never stores-then-consumes. The consumption of the carried
# ruling (m_impl_red_ruling) is mirrored by the command.issued reducer so
# replay replays the full write->consume lifecycle.
# ---------------------------------------------------------------------------


def _apply_one(st: State, etype: str, payload: dict, command_id=None, task_id=None) -> None:
    from tracks.kernel.machine import EventEnvelope, apply

    apply(
        st,
        EventEnvelope(
            seq=0,
            ts="2026-09-30T00:00:00Z",
            run_id="run",
            version="v0.10",
            type=etype,
            schema_version=1,
            command_id=command_id,
            task_id=task_id,
            payload=payload,
        ),
    )


def _ruling_payload(action, instruction="re-deliver per the ruling", scope=None):
    return {
        "check": "ruling",
        "ruling_outcome": {
            "devon_side": {
                "action": action,
                "instruction": instruction,
                "scope_boundary": scope if scope is not None else ["tracks/server/pages.py"],
                "task_id": "T-002",
            },
            "shield_side": {"action": "hold_contract"},
            "ordering": "devon-first",
        },
    }


def _command_issued(cmd_id: str, role: str, sub: str) -> dict:
    return {
        "command": {
            "command_id": cmd_id,
            "kind": "dispatch_agent",
            "params": {"role": role, "substate": sub},
        }
    }


def test_ruling_redeliver_green_overrides_stale_diagnose_and_resets_flags():
    """The live #214 shape: stale DIAGNOSE substate plus the RULING dispatch's
    doc flag still set. The verdict must route to GREEN with a fresh budget
    AND clear doc_dispatched — otherwise decide() parks on the stale flag
    (B62-class silent halt). green_committed must drop or the B56 guard
    no-ops the re-commit (#86 livelock)."""
    st = _state_in_green_gate()
    st.substate = "DIAGNOSE"
    st.doc_dispatched = True  # stale flag from the Archer RULING dispatch
    st.green_committed = True  # a prior green.committed for the same R slot
    st.current_attempt = 2
    st.last_failure = {"check": "unknown"}
    _apply_one(
        st, "verdict.passed", _ruling_payload("redeliver_green"),
        command_id="c-ruling", task_id="T-002",
    )
    assert st.substate == "GREEN"
    assert st.current_attempt == 0
    assert st.last_failure is None
    assert st.doc_dispatched is False
    assert st.green_committed is False
    assert st.m_impl_red_ruling == {
        "action": "redeliver_green",
        "instruction": "re-deliver per the ruling",
        "scope_boundary": ["tracks/server/pages.py"],
        "task_id": "T-002",
    }


def test_ruling_deliver_red_routes_red():
    st = _state_in_green_gate()
    st.substate = "RULING"
    st.doc_dispatched = True
    st.green_committed = True
    st.refactor_done = True
    _apply_one(
        st, "verdict.passed",
        _ruling_payload("deliver_red", instruction="re-pin the red anchors"),
        command_id="c-ruling", task_id="T-002",
    )
    assert st.substate == "RED"
    assert st.doc_dispatched is False
    assert st.green_committed is False
    assert st.refactor_done is False
    assert st.current_attempt == 0
    assert st.m_impl_red_ruling["action"] == "deliver_red"
    assert st.m_impl_red_ruling["instruction"] == "re-pin the red anchors"


def test_ruling_hold_advances_to_task_dispatch_and_supersedes_pending_ruling():
    st = _state_in_green_gate()
    st.substate = "RULING"
    st.doc_dispatched = True
    st.current_task_id = "T-002"
    st.m_impl_red_ruling = {"action": "redeliver_green", "instruction": "stale"}
    _apply_one(
        st, "verdict.passed", _ruling_payload("hold", instruction=""),
        command_id="c-ruling", task_id="T-002",
    )
    assert st.substate == "TASK_DISPATCH"
    assert st.current_task_id is None
    assert st.m_impl_red_ruling is None
    assert st.doc_dispatched is False


def test_ruling_unknown_action_stays_ruling_and_redispatches_archer():
    st = _state_in_green_gate()
    st.substate = "RULING"
    st.doc_dispatched = True
    _apply_one(
        st, "verdict.passed", _ruling_payload("bogus_action"),
        command_id="c-ruling", task_id="T-002",
    )
    assert st.substate == "RULING"
    assert st.doc_dispatched is False
    cmd = m_impl._decide_m_impl_ruling(st)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer" and cmd.params["substate"] == "RULING"


def test_ruled_green_dispatch_carries_instruction_and_command_issued_consumes():
    st = _state_in_green_gate()
    st.substate = "GREEN"
    st.current_task_id = "T-002"
    st.m_impl_red_ruling = {
        "action": "redeliver_green",
        "instruction": "re-deliver per the ruling",
        "scope_boundary": ["tracks/server/pages.py"],
        "task_id": "T-002",
    }
    cmd = m_impl._m_impl_devon_dispatch(st, "GREEN")
    assert cmd.params["assignment"]["ruling_instruction"] == "re-deliver per the ruling"
    assert cmd.params["assignment"]["ruling_scope"] == ["tracks/server/pages.py"]
    # decide() builds but does not consume; the write survives...
    assert st.m_impl_red_ruling is not None
    _apply_one(st, "command.issued", _command_issued("c-devon", "devon", "GREEN"))
    # ...until the command.issued reducer mirrors the consumption (replay-safe)
    assert st.m_impl_red_ruling is None
    assert st.doc_dispatched is True


def test_red_ruling_survives_unrelated_dispatch():
    # a Prism DIAGNOSE dispatch is not a consumer; the pending ruling stays
    st = _state_in_green_gate()
    st.m_impl_red_ruling = {"action": "redeliver_green", "instruction": "x"}
    _apply_one(st, "command.issued", _command_issued("c-prism", "prism", "DIAGNOSE"))
    assert st.m_impl_red_ruling is not None


def test_e2_red_ruling_consumed_by_planning_dispatch_command_issued():
    """E2 (green_on_arrival) regression guard: the PLANNING dispatch carries
    the typed ruling and its command.issued consumes it — same lifecycle as
    the #214 rev3 ruled-action channel."""
    st = _state_in_green_gate()
    st.substate = "PLANNING"
    st.m_impl_red_ruling = {
        "task_type_ruling": "green_on_arrival",
        "measured_classifications": ["unexpected_pass"],
    }
    cmd = m_impl._m_impl_archer_dispatch(st)
    assert cmd.params["assignment"]["red_ruling"]["task_type_ruling"] == "green_on_arrival"
    assert "verification-only" in cmd.params["objective"]
    assert st.m_impl_red_ruling is not None  # decide() builds, never mutates
    _apply_one(st, "command.issued", _command_issued("c-plan", "archer", "PLANNING"))
    assert st.m_impl_red_ruling is None


def test_post_ruling_failure_routes_normally_without_resurrecting_the_ruling():
    """rev2's fatal stream shape replayed under rev3: ruling -> GREEN, then a
    failure lands. It routes as an ordinary GREEN-phase failure (substate
    unchanged, doc flag reset, attempt consumed) — the consumed ruling is
    never re-applied and the DIAGNOSE loop is not resurrected."""
    st = _state_in_green_gate()
    st.substate = "DIAGNOSE"
    st.doc_dispatched = True
    _apply_one(
        st, "verdict.passed", _ruling_payload("redeliver_green"),
        command_id="c-ruling", task_id="T-002",
    )
    _apply_one(st, "command.issued", _command_issued("c-devon", "devon", "GREEN"))
    assert st.m_impl_red_ruling is None
    _apply_one(
        st, "verdict.failed", {"check": "unknown", "reason": "x", "attempt": 1},
        command_id="c-devon", task_id="T-002",
    )
    assert st.substate == "GREEN"
    assert st.doc_dispatched is False
    assert st.current_attempt == 1
    assert st.m_impl_red_ruling is None
