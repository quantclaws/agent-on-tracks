"""IF-PHASE-001 kernel Phase 0 projection + blocked routing (T-004 RED unit).

Pins the kernel Phase 0 projection/blocked/decide functions declared in
interfaces.md §1c *before* the GREEN implementation lands in
``tracks/kernel/phase0.py``:

- ``decide_phase0`` issues phase0 commands based on the current Phase 0
  status (UNSEALED → PHASE0_VALIDATING → SEALED|BLOCKED, §1c).
- ``on_phase0_baseline_repaired`` projects the baseline_repaired event into
  state (sets phase0_status to PHASE0_VALIDATING).
- ``on_phase0_blocked`` projects the blocked event (sets phase0_status to
  BLOCKED with the blocked_reason from the payload).

Each assertion fails today because the three functions are
``NotImplementedError("IF-PHASE-001")`` stubs -- the M-IMPL RED on the
contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracks.kernel.events import EventEnvelope
from tracks.kernel.phase0 import (
    decide_phase0,
    on_phase0_baseline_repaired,
    on_phase0_blocked,
)


@dataclass
class _FakeState:
    """Minimal stand-in for the kernel State (tracks/kernel/machine.py:State)
    carrying the Phase 0 projection fields defined in interfaces §1c.
    The real State will be extended; the RED tests verify the projection
    handlers set these fields."""
    phase0_status: str | None = None
    phase0_seal_id: str | None = None
    phase0_blocked_reason: str | None = None
    stage: str | None = None
    substate: str | None = None


# AC-FR0256-01@v0.7 TRACKS-TRACE decide_phase0 issues commands

def test_decide_phase0_returns_commands_when_unsealed():
    state = _FakeState(phase0_status="UNSEALED")
    commands = decide_phase0(state)
    assert isinstance(commands, list)
    assert len(commands) >= 1
    assert commands[0].kind == "phase0_validate"


def test_decide_phase0_returns_empty_when_sealed():
    state = _FakeState(phase0_status="SEALED")
    commands = decide_phase0(state)
    assert isinstance(commands, list)
    assert commands == []


# AC-FR0256-03@v0.7 TRACKS-TRACE BLOCKED parks (no auto phase0_validate)
# interfaces §1c: BLOCKED re-enters PHASE0_VALIDATING only via a NEW
# phase0_validate issued after Human repairs repo facts -- never by
# decide_phase0 itself re-issuing while still BLOCKED.

def test_decide_phase0_parks_when_blocked():
    state = _FakeState(phase0_status="BLOCKED")
    commands = decide_phase0(state)
    assert isinstance(commands, list)
    assert commands == []


# AC-FR0256-03@v0.7 TRACKS-TRACE on_phase0_blocked sets BLOCKED

def test_on_phase0_blocked_projects_blocked_reason():
    state = _FakeState(phase0_status="PHASE0_VALIDATING")
    on_phase0_blocked(
        state,
        payload={"reason": "node identity unrecoverable"},
        event=EventEnvelope(
            seq=1, ts="", run_id="", version="", type="phase0.blocked",
            schema_version=1, command_id=None, task_id=None, payload={},
        ),
    )
    assert state.phase0_status == "BLOCKED"
    assert state.phase0_blocked_reason is not None


# AC-FR0256-01@v0.7 TRACKS-TRACE on_phase0_baseline_repaired sets VALIDATING

def test_on_phase0_baseline_repaired_projects_validating():
    state = _FakeState(phase0_status="UNSEALED")
    on_phase0_baseline_repaired(
        state,
        payload={"ac": "AC-FR0250-03@v0.6", "bound_node": {"node_id": "t", "digest": "d" * 64}},
        event=EventEnvelope(
            seq=2, ts="", run_id="", version="", type="phase0.baseline_repaired",
            schema_version=1, command_id=None, task_id=None, payload={},
        ),
    )
    assert state.phase0_status == "PHASE0_VALIDATING"

