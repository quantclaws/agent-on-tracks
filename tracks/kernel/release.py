"""v0.8 release pipeline kernel (SM-01): M-VERIFY → M-SECURITY → M-RELEASE →
M-PUBLISH → M-MILESTONE stage registration, substate routing and reducers.

Pure control flow like every kernel module: no clock, no filesystem, no
network, no host-language semantics. All external facts enter as events
produced by the executor; the candidate SHA is the single primary identity
every release event must carry (NFR-0143).
"""

from __future__ import annotations

from typing import Literal

from .events import Command
from .machine import State

RELEASE_PIPELINE_VERSION = "v0.8"

RELEASE_STAGES: tuple[str, ...] = (
    "M-VERIFY",
    "M-SECURITY",
    "M-RELEASE",
    "M-PUBLISH",
    "M-MILESTONE",
)

VerifyBlockReason = Literal[
    "dirty_tree",
    "freeze_failed",
    "full_f_unavailable",
    "local_gate_failed",
    "local_gate_malformed",
    "ci_mismatch",
    "ci_missing",
    "ci_stale",
    "prism_failed",
    "candidate_mismatch",
]

ReleaseDecision = Literal["release", "delay", "return"]
TerminalReleaseState = Literal["released", "retry_tail"]


def release_stage_defs() -> tuple:
    """StageDef registrations appended to the canonical stage table.

    M-VERIFY/M-SECURITY/M-PUBLISH/M-MILESTONE are linear; M-RELEASE carries
    the AWAITING_RELEASE → DELAYED/RETURNED human-gate substates (SM-01.5/6/8).
    """
    raise NotImplementedError("IF-VERIFY-001")


def decide_release_stage(stage: str, substate: str, state: State) -> Command | None:
    """Route decide() for the five release stages (SM-01.1–.10)."""
    raise NotImplementedError("IF-VERIFY-001")


def on_candidate_frozen(state: State, payload: dict, event) -> None:
    """Project candidate_sha/clean_tree; idempotent re-freeze is a no-op."""
    raise NotImplementedError("IF-VERIFY-001")


def on_candidate_stale(state: State, payload: dict, event) -> None:
    """Mark the frozen identity drifted; downstream evidence goes stale."""
    raise NotImplementedError("IF-VERIFY-001")


def on_evidence_reused_full_f(state: State, payload: dict, event) -> None:
    """Project full_reuse=full_f with the reused evidence identity_basis."""
    raise NotImplementedError("IF-VERIFY-002")


def on_local_gate_result(state: State, payload: dict, event) -> None:
    """Project per-kind local gate status bound to candidate_sha."""
    raise NotImplementedError("IF-VERIFY-003")


def on_ci_run_observed(state: State, payload: dict, event) -> None:
    """Project ci=bound|mismatch|missing|stale|needs_attention."""
    raise NotImplementedError("IF-VERIFY-004")


def on_security_assessed(state: State, payload: dict, event) -> None:
    """Project security=passed|failed|unknown with policy digest."""
    raise NotImplementedError("IF-SECURITY-001")


def on_release_previewed(state: State, payload: dict, event) -> None:
    """Project the active preview_digest; AWAITING_RELEASE entry (SM-01.6)."""
    raise NotImplementedError("IF-RELEASE-002")


def on_release_decided(state: State, payload: dict, event) -> None:
    """Project decision=release|delay|return bound to preview_digest."""
    raise NotImplementedError("IF-RELEASE-003")


def on_publish_events(state: State, payload: dict, event) -> None:
    """Project publish=planned|executing|reconciled_skip|done|blocked."""
    raise NotImplementedError("IF-PUBLISH-001")


def on_milestone_events(state: State, payload: dict, event) -> None:
    """Project milestone/terminal=released|retry_tail (SM-01.14–.16)."""
    raise NotImplementedError("IF-MILESTONE-001")
