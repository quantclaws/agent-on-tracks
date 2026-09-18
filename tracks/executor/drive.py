"""Structured drive step for the supervisor (IF-DRIVE-001).

One decide -> issue -> execute cycle over the existing kernel/executor
machinery, returning a structured DriveResult instead of looping in-process
(the v0.8 ``cmd_run`` long loop stays the CLI behavior; its internals may
reuse this step). The supervisor maps boundaries onto durable state:
await_external -> wait registry (retry_at persists); await_human -> human
todo; terminal/failed -> command outcome. No busy loop (NFR-0152).

Contract token: IF-DRIVE-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


def drive_once(repo: Path, run_id: str, *, config: DriveConfig) -> DriveResult:
    """Execute exactly one decide->issue->execute cycle and report the
    boundary; the supervisor decides whether to continue, wait or park."""
    raise NotImplementedError("IF-DRIVE-001")
