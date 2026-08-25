"""Phase 0 state-machine declarations (IF-PHASE-001).

Implements the kernel Phase 0 projection / blocked-routing contract from
interfaces.md §1c/§1d:

- ``PHASE0_STATUSES``: the closed status set.
- ``decide_phase0``: issues ``phase0_validate`` only while UNSEALED; parks
  (returns no commands) once validating, sealed or blocked. BLOCKED re-enters
  PHASE0_VALIDATING only via a *new* ``phase0_validate`` issued after Human
  repairs repo facts -- never by auto re-issuing here.
- ``on_phase0_baseline_repaired``: projects the ``phase0.baseline_repaired``
  event, moving UNSEALED → PHASE0_VALIDATING (SM-01.2).
- ``on_phase0_blocked``: projects the ``phase0.blocked`` event, moving
  PHASE0_VALIDATING → BLOCKED (SM-01.5) and recording the reason from the
  event payload.
"""

from __future__ import annotations

from tracks.kernel.events import Command, EventEnvelope

PHASE0_STATUSES = ("UNSEALED", "PHASE0_VALIDATING", "SEALED", "BLOCKED")


def decide_phase0(state) -> list[Command]:
    """Issue phase0 commands based on the current Phase 0 projection.

    Only the UNSEALED state triggers a new ``phase0_validate`` (SM-01.2).
    PHASE0_VALIDATING is mid-cycle (its own re-checks are event driven),
    SEALED is terminal and BLOCKED parks for a Human repair before a fresh
    ``phase0_validate`` can be issued elsewhere.
    """
    if getattr(state, "phase0_status", None) == "UNSEALED":
        return [Command(kind="phase0_validate")]
    return []


def on_phase0_baseline_repaired(state, payload: dict, event: EventEnvelope) -> None:
    """Project ``phase0.baseline_repaired``: UNSEALED → PHASE0_VALIDATING."""
    state.phase0_status = "PHASE0_VALIDATING"


def on_phase0_coverage(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-002")


def on_phase0_guard_hardened(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-002")


def on_phase0_sealed(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-003")


def on_phase0_blocked(state, payload: dict, event: EventEnvelope) -> None:
    """Project ``phase0.blocked``: PHASE0_VALIDATING → BLOCKED (SM-01.5)."""
    state.phase0_status = "BLOCKED"
    state.phase0_blocked_reason = payload.get("reason") or "blocked"


def on_guard_parity(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-GUARD-002")

