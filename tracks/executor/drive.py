"""Structured drive step for the supervisor (IF-DRIVE-001).

One decide -> issue -> execute window over the existing kernel/executor
machinery, returning a structured DriveResult instead of looping in-process
(the v0.8 ``cmd_run`` long loop stays the CLI behavior; its internals may
reuse this step). The supervisor maps boundaries onto durable state:
await_external -> wait registry (retry_at persists); await_human -> human
todo; terminal/failed -> command outcome. No busy loop (NFR-0152).

Boundary classification reads the run-plane projection (single source of
truth): a completed run is terminal (the terminal_state is surfaced so an
abandon is never presented as success), a parked run waits at its human gate,
and an active run gets one drive window of the existing loop. Harness
failures are reported with their closed-set ``failure_class``; the
supervisor/waiting layer maps ``recoverable_external`` onto the durable wait
registry (§1j) — this module never writes service-plane state.

Contract token: IF-DRIVE-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tracks import paths
from tracks.store import Store

# Harness failure classes already classified by the effects layer; anything
# else escaping the drive window is a machine-side runtime error and fails
# closed as unrecoverable (never silently retried).
_RECOVERABLE_CLASSES = frozenset(
    {
        "signal",
        "timeout",
        "provider_unavailable",
        "abnormal_step_finish",
        "json_truncated",
        "network",
    }
)


@dataclass(frozen=True)
class WaitSpec:
    """Durable external-wait description (interfaces §1d/§1j)."""

    wait_class: str  # "ci" | "quota" | "network" | "agent" | "external"
    reason: str
    retry_at: str | None  # ISO8601 when the reset time is known
    known_reset: bool
    backoff: dict | None  # {"interval_s", "cap_s", "next_probe_at"}


@dataclass(frozen=True)
class DriveResult:
    """Boundary outcome of one drive step (interfaces §1d)."""

    kind: str  # "continue" | "await_human" | "await_external" | "terminal" | "failed"
    run_id: str
    stage: str | None
    wait: WaitSpec | None
    failure: dict | None  # {"failure_class": "recoverable_external"|"unrecoverable", ...}
    detail: str


@dataclass(frozen=True)
class DriveConfig:
    """Drive-time configuration (NFR-0152 wait defaults via serve flags)."""

    wait_initial_s: int = 60
    wait_cap_s: int = 900


def _last_seq(store: Store, run_id: str) -> int:
    events = list(store.events(run_id))
    return events[-1].seq if events else 0


def _is_boundary(state) -> bool:
    """True when the projection is parked at a durable non-active boundary."""
    return (
        state.status == "completed"
        or state.status == "awaiting_human"
        or bool(state.awaiting)
    )


def _detail_suffix(state) -> str:
    return f"{state.stage or '?'}/{state.substate or '-'}"


def _boundary_result(run_id: str, state) -> DriveResult:
    """Classify a projected state onto the five-state DriveResult set."""
    if state.status == "completed":
        terminal = state.terminal_state or "completed"
        return DriveResult(
            kind="terminal",
            run_id=run_id,
            stage=state.stage,
            wait=None,
            failure=None,
            detail=f"run {run_id} terminal: {terminal} (at {_detail_suffix(state)})",
        )
    if state.status == "awaiting_human" or state.awaiting:
        gate = state.awaiting or state.substate or "human"
        return DriveResult(
            kind="await_human",
            run_id=run_id,
            stage=state.stage,
            wait=None,
            failure=None,
            detail=f"run {run_id} awaiting human decision {gate!r} at {_detail_suffix(state)}",
        )
    return DriveResult(
        kind="continue",
        run_id=run_id,
        stage=state.stage,
        wait=None,
        failure=None,
        detail=f"run {run_id} advanced at {_detail_suffix(state)}",
    )


def _failed_result(run_id: str, stage: str | None, reason: str) -> DriveResult:
    return DriveResult(
        kind="failed",
        run_id=run_id,
        stage=stage,
        wait=None,
        failure={"failure_class": "unrecoverable", "reason": reason},
        detail=f"run {run_id} drive failed: {reason}",
    )


def _failure_from_exception(exc: Exception) -> dict:
    """Map an escaping drive error onto the closed failure_class vocabulary.

    A harness-classified class is a recoverable external condition (the
    waiting layer persists the retry); anything unclassified is a runtime
    defect and fails closed unrecoverable (§1d/§1j)."""
    classified = str(getattr(exc, "failure_class", "") or "")
    recoverable = classified in _RECOVERABLE_CLASSES
    failure = {
        "failure_class": "recoverable_external" if recoverable else "unrecoverable",
        "reason": str(exc) or type(exc).__name__,
    }
    if classified:
        failure["kind"] = classified
    stderr = str(getattr(exc, "stderr", "") or "")
    if stderr:
        failure["stderr"] = stderr
    return failure


def _error_result(run_id: str, stage: str | None, exc: Exception) -> DriveResult:
    """Fail-closed DriveResult for an error escaping the drive window."""
    failure = _failure_from_exception(exc)
    return DriveResult(
        kind="failed",
        run_id=run_id,
        stage=stage,
        wait=None,
        failure=failure,
        detail=f"run {run_id} drive error: {failure['reason']}",
    )


def _run_drive_window(repo: Path, store: Store, run_id: str) -> None:
    """One bounded window of the existing loop (lazy import: the executor
    composition root imports this module's consumers, never the reverse)."""
    from tracks.executor.executor import Executor  # noqa: PLC0415

    Executor(store, repo, run_id).run_loop()


def drive_once(repo: Path, run_id: str, *, config: DriveConfig) -> DriveResult:
    """Execute exactly one decide->issue->execute window and report the
    boundary; the supervisor decides whether to continue, wait or park.

    ``config`` carries the NFR-0152 wait/backoff defaults the supervisor
    turns into the durable WaitPolicy when a recoverable external failure is
    reported (§1j); the drive step itself writes no service-plane state.
    """
    repo = Path(repo)
    store = Store(paths.tracks_home(repo))
    try:
        state = store.state(run_id)
        if state.run_id is None:
            return _failed_result(
                run_id, None, f"unknown run {run_id!r}: no run-plane events"
            )
        if _is_boundary(state):
            return _boundary_result(run_id, state)
        before_seq = _last_seq(store, run_id)
        try:
            _run_drive_window(repo, store, run_id)
        except Exception as exc:  # noqa: BLE001 - structured failure boundary
            return _error_result(run_id, state.stage, exc)
        state = store.state(run_id)
        if not _is_boundary(state) and _last_seq(store, run_id) <= before_seq:
            return _failed_result(
                run_id,
                state.stage,
                "run halted without progress (no durable boundary reached)",
            )
        return _boundary_result(run_id, state)
    finally:
        store.close()
