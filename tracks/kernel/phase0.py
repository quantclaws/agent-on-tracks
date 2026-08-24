"""Phase 0 state-machine declarations (IF-PHASE-001)."""

from __future__ import annotations

from tracks.kernel.events import Command, EventEnvelope

PHASE0_STATUSES = ("UNSEALED", "PHASE0_VALIDATING", "SEALED", "BLOCKED")


def decide_phase0(state) -> list[Command]:
    raise NotImplementedError("IF-PHASE-001")


def on_phase0_baseline_repaired(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-001")


def on_phase0_coverage(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-002")


def on_phase0_guard_hardened(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-002")


def on_phase0_sealed(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-003")


def on_phase0_blocked(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-PHASE-001")


def on_guard_parity(state, payload: dict, event: EventEnvelope) -> None:
    raise NotImplementedError("IF-GUARD-002")
