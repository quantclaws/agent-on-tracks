"""Kernel state projection for the v0.7 capability seam (T-015 assembly).

architecture §1.0.9/§1.1: the generic version seam owns registration and
resolution only -- these pure projectors fold the ``phase0.*`` append-only
events into the kernel ``State`` so the Phase 0 status is fully rebuildable
from the event stream (AC-NFR0140-01: dropping the projection and replaying
rebuilds the identical ``phase0_status`` / seal identity).

``phase0.baseline_repaired`` and ``phase0.blocked`` project through the
T-004 ``kernel.phase0`` handlers (SM-01.2/SM-01.5); the intermediate
coverage/guard_hardened progression and the SEALED terminal projection live
here (SM-01.3/SM-01.4). No I/O, no clock, no env reads (NFR-02).
"""

from __future__ import annotations


def on_phase0_coverage(state, payload: dict, event) -> None:
    """Project ``phase0.coverage``: Phase 0 stays validating until sealed."""
    state.phase0_status = "PHASE0_VALIDATING"


def on_phase0_guard_hardened(state, payload: dict, event) -> None:
    """Project ``phase0.guard_hardened``: still validating (SM-01.3)."""
    state.phase0_status = "PHASE0_VALIDATING"


def on_phase0_sealed(state, payload: dict, event) -> None:
    """Project ``phase0.sealed``: SEALED resumes M-TEST (SM-01.4)."""
    state.phase0_status = "SEALED"
    state.phase0_seal_id = payload.get("seal_id")
