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
            status = getattr(state, "publish_status", None)
            if status in (None, "planned", "executing"):
                return Command(kind="execute_publish")
            # A terminal publish face never re-executes on its own: ``blocked``
            # (AC-FR0275-04: unknown/malformed preflight, remote conflict,
            # agent_forbidden -- the publish does not continue; a fix means a
            # new preview/decision) and ``done``/``reconciled_skip`` (the
            # M-PUBLISH face hands to close_milestone) both park the decider.
            # Without this guard a zero-effect preflight failure re-issued
            # execute_publish in a tight loop (live: 1500+ events in 30s).
            return None
        return None
    return None


def _decide_publish(substate: str, state: State) -> Command | None:
    """SM-01.7.1 -> SM-01.8: the reconciled M-PUBLISH hands to the milestone."""
    if substate == _RELEASE_STAGE_SUBSTATES["M-PUBLISH"] and state.stage_exited:
        return Command(kind="close_milestone")
    return None


def _decide_milestone(substate: str, state: State) -> Command | None:
    """SM-01.8.1 -> SM-01.16: the itemized closing tail resumes.

    trace_closed -> issue/project closed -> sealed -> refs.cleaned ->
    run.completed; the handler skips sub-steps whose own event already landed
    (any prior command_id counts), so a new close_milestone command continues
    the interrupted tail without re-emitting completed steps. Once
    run.completed lands the projection flips status=completed and decide()
    halts before this decider is consulted again.
    """
    if substate == _RELEASE_STAGE_SUBSTATES["M-MILESTONE"]:
        return Command(kind="close_milestone")
    return None


_DECIDERS = {
    "M-VERIFY": _decide_verify,
    "M-RELEASE": _decide_release,
    "M-PUBLISH": _decide_publish,
    "M-MILESTONE": _decide_milestone,
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
    """Project candidate_sha/clean_tree; idempotent re-freeze is a no-op
    (SM-01.17). A NEW candidate identity restarts the whole downstream
    evidence projection -- every verify/security/preview fact of the
    previous candidate is stale relative to it and the chain re-walks
    (SM-01.20, interfaces §1d)."""
    sha = str(payload.get("candidate_sha") or "")
    if not sha:
        return
    if state.candidate_sha == sha:
        return
    state.candidate_sha = sha
    state.candidate_clean = bool(payload.get("clean_tree"))
    state.candidate_stale = False
    state.full_reuse = None
    state.reuse_identity_basis = None
    state.local_gates = {}
    state.ci_status = None
    state.security_status = None
    state.security_policy_digest = None
    state.preview_digest = None
    state.release_decision = None
    state.release_decision_digest = None
    state.publish_status = None
    state.publish_expected = 0
    state.publish_done_keys = []
    state.publish_started = False
    state.milestone_status = None


def on_candidate_stale(state: State, payload: dict, event) -> None:
    """Mark the frozen identity drifted; downstream evidence goes stale."""
    sha = str(payload.get("candidate_sha") or "")
    if sha and state.candidate_sha and sha != state.candidate_sha:
        return  # a stale mark of an already-superseded candidate
    state.candidate_stale = True


def on_evidence_reused_full_f(state: State, payload: dict, event) -> None:
    """Project full_reuse=full_f with the reused evidence identity_basis."""
    if str(payload.get("kind") or "") != "full_f":
        return
    state.full_reuse = "full_f"
    basis = payload.get("identity_basis")
    state.reuse_identity_basis = (
        [str(item) for item in basis] if isinstance(basis, list) else None
    )


def on_local_gate_result(state: State, payload: dict, event) -> None:
    """Project per-kind local gate status bound to candidate_sha."""
    kind = str(payload.get("kind") or "")
    if not kind:
        return
    sha = str(payload.get("candidate_sha") or "")
    if state.candidate_sha and sha and sha != state.candidate_sha:
        return  # a foreign-candidate gate result never projects
    state.local_gates[kind] = {
        "status": "passed" if event.type == "local_gate.passed" else "failed",
        "reason": payload.get("reason"),
        "contract_digest": payload.get("contract_digest"),
    }


def on_ci_run_observed(state: State, payload: dict, event) -> None:
    """Project ci=bound|mismatch|missing|stale|needs_attention."""
    sha = str(payload.get("candidate_sha") or "")
    if state.candidate_sha and sha and sha != state.candidate_sha:
        return
    if payload.get("api_verified") is True:
        state.ci_status = "bound"
        return
    state.ci_status = str(
        payload.get("reason") or payload.get("status") or "missing"
    )


def on_security_assessed(state: State, payload: dict, event) -> None:
    """Project security=passed|failed|unknown with policy digest."""
    sha = str(payload.get("candidate_sha") or "")
    if state.candidate_sha and sha and sha != state.candidate_sha:
        return
    status = str(payload.get("status") or "")
    state.security_status = status if status in ("passed", "failed") else "unknown"
    if payload.get("policy_digest"):
        state.security_policy_digest = str(payload["policy_digest"])


def on_release_previewed(state: State, payload: dict, event) -> None:
    """Project the active preview_digest; AWAITING_RELEASE entry (SM-01.6)."""
    sha = str(payload.get("candidate_sha") or "")
    if state.candidate_sha and sha and sha != state.candidate_sha:
        return
    if payload.get("preview_digest"):
        state.preview_digest = str(payload["preview_digest"])
    steps = _preview_steps(payload)
    if steps is not None:
        # SM-01.15: the approved plan's unique operation set is the batch
        # completion target; a new preview resets prior per-key progress.
        state.publish_expected = len(set(steps))
        state.publish_done_keys = []
    if state.stage == "M-RELEASE" and state.substate == "PREVIEWING":
        state.substate = M_RELEASE_GATE_SUBSTATE


def _preview_steps(payload: dict) -> list[str] | None:
    """The preview's resolved plan steps, or None for legacy previews.

    ``None`` keeps the pre-plan projection semantics (a preview without an
    operation_plan never blocks the legacy exit); a plan with zero resolved
    steps is an explicit empty batch and projects as such.
    """
    plan = payload.get("operation_plan")
    if not isinstance(plan, dict):
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return None
    return [str(step) for step in steps]


def on_release_decided(state: State, payload: dict, event) -> None:
    """Project decision=release|delay|return bound to preview_digest.

    ``release`` keeps M-RELEASE/AWAITING_RELEASE so the stage decider
    issues execute_publish (§1.0.6); the M-PUBLISH entry projects from the
    publish.planned write-ahead event. delay/return park the gate substates
    (the universal ``trac return`` owns pointer moves, SM-01.19)."""
    action = str(payload.get("action") or "")
    if action not in ("release", "delay", "return"):
        return
    state.release_decision = action
    if payload.get("preview_digest"):
        state.release_decision_digest = str(payload["preview_digest"])
    if action == "delay":
        state.substate = "DELAYED"
    elif action == "return":
        state.substate = "RETURNED"


def on_publish_events(state: State, payload: dict, event) -> None:
    """Project publish=planned|executing|reconciled_skip|done|blocked.

    SM-01.15: M-PUBLISH exits (``stage_exited``) only once EVERY operation
    of the approved preview plan is terminal (done/reconciled_skip). A
    partial stop -- a failed/conflicting operation while others remain --
    leaves the stage parked and fail-closed: close_milestone is never
    handed a half-published batch. The write-ahead ``pending`` record of the
    started batch survives the failure (machine.apply) so the next drive
    replays the SAME command and reconciles the finished operations.
    """
    etype = event.type
    if etype == "publish.planned":
        state.publish_status = "planned"
        state.publish_started = True
        # SM-01.7 walk: publish execution is the M-PUBLISH body -- the
        # decision kept M-RELEASE/AWAITING_RELEASE so the decider could
        # issue execute_publish; the write-ahead projects the entry.
        if state.stage == "M-RELEASE":
            state.stage = "M-PUBLISH"
            state.substate = "PUBLISHING"
            state.stage_exited = False
        return
    if etype == "publish.executed":
        status = str(payload.get("status") or "done")
        terminal = status in ("done", "reconciled_skip")
        key = str(payload.get("idempotency_key") or "")
        if terminal and key and key not in state.publish_done_keys:
            state.publish_done_keys.append(key)
        state.publish_status = status if terminal else "executing"
        if terminal and _publish_batch_complete(state):
            state.stage_exited = True  # §1.0.6: hands to close_milestone
            state.pending = None
            state.publish_started = False
        return
    # publish.blocked / publish.failed / reconcile_conflict: the batch is
    # not terminal -- never a stage exit (fail-closed).
    state.publish_status = "blocked"
    state.stage_exited = False


def _publish_batch_complete(state: State) -> bool:
    """Every plan operation terminal, or a legacy preview without a plan."""
    if state.publish_expected <= 0:
        return True
    return len(set(state.publish_done_keys)) >= state.publish_expected


def on_milestone_events(state: State, payload: dict, event) -> None:
    """Project milestone/terminal=released|retry_tail (SM-01.14–.16)."""
    etype = event.type
    if etype == "milestone.trace_closed":
        # §1.0.7: the trace closure opens the M-MILESTONE closing tail.
        if state.stage == "M-PUBLISH":
            state.stage = "M-MILESTONE"
            state.substate = "CLOSING"
            state.stage_exited = False
        state.milestone_status = "closing"
        return
    if etype in ("issue.closed", "project.closed"):
        return  # per-item projections carry no stage face
    if etype == "milestone.closed":
        state.milestone_status = "released"
        return
    if etype == "milestone.sealed":
        state.milestone_status = "sealed"
        return
    # refs.cleaned: the closing tail completed (§1.0.7 terminal face;
    # run.completed carries the authoritative terminal_state).
    state.milestone_status = "released"


def classify_defect_route(reason: str, context: dict) -> dict:
    """Single closed-set classifier reason -> repair_route (§1.0.14, FR-0286).

    Returns {exit_class, defect_class, owner, discipline, budget_remaining}.
    Classification only decides WHO repairs (Devon RED-first / Shield targeted
    tests / Archer advisory) — it never produces a stage rollback. An unknown
    reason fails closed: it never emits a pass or an empty route.
    """
    raise NotImplementedError("IF-REPAIR-001")
