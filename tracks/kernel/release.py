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
from .machine import StageDef, State

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

# architecture §1.0.14 blocked-recovery classes: in-place repair (FR-0286)
# plus env-transient retry and the Human escape gate (FR-0287). No automatic
# rollback to M-DESIGN/M-PLANNING; classification only decides who repairs.
DefectClass = Literal[
    "behavior",  # Devon RED-first: new unit regression; frozen int/e2e kept
    "gate",  # verification-only: re-running the repaired gate is the proof
    "cve",  # Archer advisory consult (no stage return); Devon executes
    "contract",  # controlled contract revision (delta doc + review)
]
BlockExitClass = Literal[
    "env_transient",  # A: operator restores env; in-place retry, candidate kept
    "defect_repair",  # B: in-place repair; fix commit -> new candidate re-walk
    "irreparable",  # C: known issue (product defects) or Human escape gate
    "human_escape",  # D: trac return / abandon (RP-01 #15/16)
]

REPAIR_BUDGET_DEFAULT = 3

# SM-01.5/6: the M-RELEASE human gate. It opens only after the preview is
# generated (SM-01.4), so M-RELEASE's initial substate must precede it.
M_RELEASE_GATE_SUBSTATE = "AWAITING_RELEASE"

# Entry substates for the five release stages (SM-01.1): the four linear
# stages re-enter here after a rollback; M-RELEASE re-enters before the gate.
_RELEASE_STAGE_SUBSTATES = {
    "M-VERIFY": "VERIFYING",
    "M-SECURITY": "ASSESSING",
    "M-RELEASE": "PREVIEWING",
    "M-PUBLISH": "PUBLISHING",
    "M-MILESTONE": "CLOSING",
}


def release_stage_defs() -> tuple:
    """StageDef registrations appended to the canonical stage table.

    M-VERIFY/M-SECURITY/M-PUBLISH/M-MILESTONE are linear; M-RELEASE carries
    the AWAITING_RELEASE → DELAYED/RETURNED human-gate substates (SM-01.5/6/8).
    Like M-TEST/M-IMPL, the release stages are flow-driven: no drafting_role/
    doc/reviewer — the executor walk and ``decide_release_stage`` route them.
    """
    return tuple(
        StageDef(stage=stage, initial_substate=_RELEASE_STAGE_SUBSTATES[stage])
        for stage in RELEASE_STAGES
    )


def _repair_command(route: dict) -> Command | None:
    """§1.0.14 classified route -> repair command; other classes stay at walk."""
    if route.get("exit_class") == "defect_repair":
        return Command(kind="dispatch_repair")
    if route.get("exit_class") == "irreparable":
        return Command(kind="register_known_issue")
    return None


def _decide_verify(substate: str, state: State) -> Command | None:
    """SM-01.2.1 -> SM-01.3: the completed M-VERIFY chain hands to security."""
    if substate == _RELEASE_STAGE_SUBSTATES["M-VERIFY"] and state.stage_exited:
        return Command(kind="assess_security")
    return None


def _decide_release(substate: str, state: State) -> Command | None:
    """SM-01.3.1 -> .4/.6 entry preview; SM-01.5.1 -> .7 accepted release."""
    if substate == _RELEASE_STAGE_SUBSTATES["M-RELEASE"]:
        return Command(kind="generate_preview")
    if substate == M_RELEASE_GATE_SUBSTATE:
        if getattr(state, "release_decision", None) == "release":
            return Command(kind="execute_publish")
        return None
    return None


def _decide_publish(substate: str, state: State) -> Command | None:
    """SM-01.7.1 -> SM-01.8: the reconciled M-PUBLISH hands to the milestone."""
    if substate == _RELEASE_STAGE_SUBSTATES["M-PUBLISH"] and state.stage_exited:
        return Command(kind="close_milestone")
    return None


_DECIDERS = {
    "M-VERIFY": _decide_verify,
    "M-RELEASE": _decide_release,
    "M-PUBLISH": _decide_publish,
}


def decide_release_stage(stage: str, substate: str, state: State) -> Command | None:
    """Route decide() for the five release stages (SM-01.1–.10).

    A classified repair route (architecture §1.0.14; classification is
    T-029's ``classify_defect_route``, carried in ``state.last_failure``)
    dominates: defect_repair dispatches the in-place repair, irreparable
    product defects register a known issue, env_transient/human_escape retry
    at walk level (None). Otherwise the walk boundaries route the executor
    commands; anything unmatched routes None (fail-closed).
    """
    failure = getattr(state, "last_failure", None)
    route = failure.get("repair_route") if isinstance(failure, dict) else None
    if isinstance(route, dict):
        return _repair_command(route)
    decider = _DECIDERS.get(stage)
    if decider is None:
        return None
    return decider(substate, state)


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


def classify_defect_route(reason: str, context: dict) -> dict:
    """Single closed-set classifier reason -> repair_route (§1.0.14, FR-0286).

    Returns {exit_class, defect_class, owner, discipline, budget_remaining}.
    Classification only decides WHO repairs (Devon RED-first / Shield targeted
    tests / Archer advisory) — it never produces a stage rollback. An unknown
    reason fails closed: it never emits a pass or an empty route.
    """
    raise NotImplementedError("IF-REPAIR-001")
