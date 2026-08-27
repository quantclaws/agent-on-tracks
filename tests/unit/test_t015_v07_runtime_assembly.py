"""T-015 RED: v0.7 runtime assembly on the generic capability seam.

Architecture §1.0.9/§1.0.2/§1.1: Executor.run_loop and kernel.machine hold
only the language-neutral, version-neutral capability seam; the v0.7
extension resolves ``before_mtest`` (Phase 0 pre-gate: any non-SEALED
``phase0_status`` routes to ``Command(phase0_validate)`` before M-TEST
work), the ``phase0.*`` append-only events join the closed event set and
project ``phase0_status`` (AC-NFR0140-01: the projection is fully
rebuildable from the event stream), and earlier versions select no v0.7
extension. The T-013 composition root (version_extensions/v07_runtime) is
consumed by import only.
"""

from __future__ import annotations

from tracks.executor.version_extensions import (
    CapabilityBlockedError,
    resolve_capability,
)
from tracks.kernel.events import COMMAND_KINDS, EVENT_TYPES, EventEnvelope
from tracks.kernel.machine import State, decide, project

PHASE0_EVENT_TYPES = (
    "phase0.baseline_repaired",
    "phase0.coverage",
    "phase0.guard_hardened",
    "phase0.sealed",
    "phase0.blocked",
)


def _resolve(version: str, capability: str):
    """Resolve after importing the T-015 seam call points.

    Importing the executor (run_loop) and kernel.machine modules must have
    composed the v0.7 extension; a blocked/missing capability is normalized
    to ``None`` so the RED failure lands on the contract token.
    """
    import tracks.executor.executor  # noqa: F401  (Executor.run_loop seam point)
    import tracks.kernel.machine  # noqa: F401  (kernel.machine seam point)

    try:
        return resolve_capability(version, capability)
    except CapabilityBlockedError:
        return None


def _envelope(seq: int, etype: str, payload: dict | None = None) -> EventEnvelope:
    return EventEnvelope(
        seq=seq,
        ts="",
        run_id="R",
        version="v0.7",
        type=etype,
        schema_version=1,
        command_id=None,
        task_id=None,
        payload=payload or {},
    )


def _v07_state(phase0_status: str | None, version: str = "v0.7") -> State:
    state = State(
        run_id="R",
        version=version,
        status="active",
        stage="M-TEST",
        substate="DISPATCH",
    )
    state.phase0_status = phase0_status
    return state


def _decide_or_none(state: State):
    """decide() under RED normalization: any routing crash still means
    'no Phase 0 pre-gate' for this pin, so it degrades to None."""
    try:
        return decide(state)
    except Exception:  # noqa: BLE001 -- RED normalization to a clean assertion
        return None


# AC-NFR0140-01@v0.7 TRACKS-TRACE phase0 events join the closed sets
def test_phase0_events_and_command_join_the_closed_sets():
    missing = [etype for etype in PHASE0_EVENT_TYPES if etype not in EVENT_TYPES]
    assert missing == [], f"interfaces §3 closed event set lacks phase0 events: {missing}"
    assert "phase0_validate" in COMMAND_KINDS, (
        "interfaces §4 closed command set lacks the phase0_validate command "
        "issued by the kernel Phase 0 entry guard"
    )


# AC-NFR0140-01@v0.7 TRACKS-TRACE v0.7 exposes before_mtest; v0.6 selects none
def test_v07_composition_resolves_before_mtest_and_earlier_versions_select_none():
    callback = _resolve("v0.7", "before_mtest")
    assert callable(callback), (
        "architecture §1.0.9/§1.1: the composed v0.7 extension must expose "
        "before_mtest so Executor.run_loop/kernel.machine can drive Phase 0"
    )
    assert _resolve("v0.6", "before_mtest") is None, (
        "architecture §1.0.9: earlier versions select no v0.7 extension"
    )


# AC-NFR0140-01@v0.7 TRACKS-TRACE before_mtest drives the Phase 0 entry guard
def test_before_mtest_drives_the_phase0_entry_guard():
    callback = _resolve("v0.7", "before_mtest")
    assert callable(callback), (
        "architecture §1.0.9/§1.1: v0.7 before_mtest capability is missing"
    )
    commands = list(callback(_v07_state(None)))
    assert [c.kind for c in commands] == ["phase0_validate"], (
        "before_mtest must drive Phase 0: an unsealed v0.7 run routes to "
        "phase0_validate (kernel.phase0 entry guard)"
    )
    assert list(callback(_v07_state("SEALED"))) == [], "SEALED resumes M-TEST"
    assert list(callback(_v07_state("BLOCKED"))) == [], (
        "BLOCKED parks -- no auto phase0_validate re-issue"
    )


# AC-NFR0140-01@v0.7 TRACKS-TRACE machine.decide pre-gates M-TEST for v0.7
def test_machine_decide_gates_v07_mtest_entry_with_phase0_validate():
    cmd = _decide_or_none(_v07_state(None))
    assert cmd is not None and cmd.kind == "phase0_validate", (
        "kernel.machine must route an unsealed v0.7 M-TEST entry through the "
        "Phase 0 pre-gate before any M-TEST command (architecture §1.0.2)"
    )


def test_machine_decide_never_auto_issues_phase0_when_sealed_blocked_or_v06():
    for version, status in (("v0.7", "SEALED"), ("v0.7", "BLOCKED"), ("v0.6", None)):
        cmd = _decide_or_none(_v07_state(status, version=version))
        assert not (cmd is not None and cmd.kind == "phase0_validate"), (
            f"{version} with phase0_status={status}: phase0_validate must not "
            "be issued"
        )


# AC-NFR0140-01@v0.7 TRACKS-TRACE phase0 events project a rebuildable status
def test_phase0_events_project_status_and_rebuild_invariant():
    events = [
        _envelope(1, "phase0.baseline_repaired", {"ac": "AC-FR0250-03@v0.6"}),
        _envelope(2, "phase0.blocked", {"reason": "node identity unrecoverable"}),
    ]
    validating = project(events[:1])
    assert getattr(validating, "phase0_status", None) == "PHASE0_VALIDATING", (
        "phase0.baseline_repaired must project phase0_status (SM-01.2)"
    )
    state = project(events)
    assert getattr(state, "phase0_status", None) == "BLOCKED", (
        "phase0.blocked must project phase0_status (SM-01.5)"
    )
    assert getattr(state, "phase0_blocked_reason", None) == (
        "node identity unrecoverable"
    )
    rebuilt = project(list(events))  # projection drop -> pure event replay
    assert getattr(rebuilt, "phase0_status", None) == getattr(
        state, "phase0_status", None
    ), "AC-NFR0140-01: rebuilding the projection from events must not drift"
    assert getattr(rebuilt, "phase0_blocked_reason", None) == getattr(
        state, "phase0_blocked_reason", None
    )
