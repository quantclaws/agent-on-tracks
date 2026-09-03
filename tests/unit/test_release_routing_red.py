"""T-039 RED: release routing anchors (IF-VERIFY-005 / FR-0271).

Declares the red obligations for tracks/kernel/release.py:

1. release_stage_defs() registers the five release stages (RELEASE_STAGES)
   as StageDef records consumable by the machine seam
   (tracks/kernel/machine.py `_rolled_back_entry_substate` reads
   .stage / .initial_substate). M-RELEASE's initial_substate must precede
   the AWAITING_RELEASE human gate (SM-01.4/.6).

2. decide_release_stage(stage, substate, state) routes the six delivery
   commands (T-039 delivery surface):

   trigger                                        Command kind
   --------------------------------------------   ---------------------
   M-VERIFY chain complete (SM-01.2.1 -> .3)      assess_security
   M-RELEASE entry, security passed (.4/.6)       generate_preview
   release decision at AWAITING_RELEASE (.5.1)    execute_publish
   reconciled M-PUBLISH (SM-01.7.1 -> .8)         close_milestone
   classified defect_repair route (§1.0.14 B)     dispatch_repair
   classified irreparable product defect (§1.0.14 C) register_known_issue

   Repair routing consumes an already-classified repair_route dict
   (interfaces.md classify_defect_route closed set) carried in
   state.last_failure; classification itself belongs to T-029, the
   routing to T-039. Any trigger without a match routes None (fail-closed).
"""

from __future__ import annotations

from tracks.kernel.events import Command
from tracks.kernel.machine import StageDef, State
from tracks.kernel.release import (
    RELEASE_STAGES,
    decide_release_stage,
    release_stage_defs,
)


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _stage_defs() -> list[StageDef]:
    try:
        defs = list(release_stage_defs())
    except NotImplementedError as exc:
        _fail(
            "release_stage_defs() still raises NotImplementedError "
            f"(IF-VERIFY-001 stub): {exc}"
        )
    return defs


def _route(stage: str, substate: str, state: State):
    try:
        return decide_release_stage(stage, substate, state)
    except NotImplementedError as exc:
        _fail(
            f"decide_release_stage({stage!r}, {substate!r}, ...) still raises "
            f"NotImplementedError (IF-VERIFY-001 stub): {exc}"
        )


def _fresh_state(**overrides) -> State:
    state = State()
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def _initial_substates(defs: list[StageDef]) -> dict[str, str]:
    return {sd.stage: sd.initial_substate for sd in defs}


def test_release_stage_defs_register_five_stages():
    defs = _stage_defs()
    by_stage: dict[str, StageDef] = {}
    for sd in defs:
        if not isinstance(sd, StageDef):
            _fail(
                "release_stage_defs() yielded "
                f"{type(sd).__name__}, expected a StageDef record"
            )
        if not sd.stage or not sd.initial_substate:
            _fail(
                f"StageDef for {sd.stage!r} lacks .stage/.initial_substate "
                "(machine seam contract, machine.py _rolled_back_entry_substate)"
            )
        by_stage[sd.stage] = sd
    missing = [s for s in RELEASE_STAGES if s not in by_stage]
    if missing:
        _fail(f"release stages not registered by release_stage_defs(): {missing}")
    extra = [s for s in by_stage if s not in RELEASE_STAGES]
    if extra:
        _fail(f"unexpected stages registered: {extra}")
    if len(defs) != len(RELEASE_STAGES):
        _fail(f"expected {len(RELEASE_STAGES)} stage defs, got {len(defs)}")
    if by_stage["M-RELEASE"].initial_substate == "AWAITING_RELEASE":
        _fail(
            "M-RELEASE initial_substate must precede the AWAITING_RELEASE "
            "human gate (SM-01.4/.6: preview is generated before the gate)"
        )


def test_decide_release_stage_routes_six_commands():
    subs = _initial_substates(_stage_defs())
    # SM-01.2.1 -> SM-01.3: completed M-VERIFY chain routes security assessment.
    cmd = _route(
        "M-VERIFY", subs["M-VERIFY"], _fresh_state(stage="M-VERIFY", stage_exited=True)
    )
    if not (isinstance(cmd, Command) and cmd.kind == "assess_security"):
        _fail(f"completed M-VERIFY chain must route Command('assess_security'), got {cmd!r}")
    # Fail-closed: an M-VERIFY visit without chain completion routes nothing.
    cmd = _route("M-VERIFY", subs["M-VERIFY"], _fresh_state(stage="M-VERIFY"))
    if cmd is not None:
        _fail(f"uncompleted M-VERIFY must route None, got {cmd!r}")
    # SM-01.3.1 -> SM-01.4/.6: entering M-RELEASE routes preview generation.
    cmd = _route("M-RELEASE", subs["M-RELEASE"], _fresh_state(stage="M-RELEASE"))
    if not (isinstance(cmd, Command) and cmd.kind == "generate_preview"):
        _fail(f"M-RELEASE entry must route Command('generate_preview'), got {cmd!r}")
    # SM-01.5.1 -> SM-01.7: Human release decision at AWAITING_RELEASE routes publish.
    state = _fresh_state(stage="M-RELEASE", release_decision="release")
    cmd = _route("M-RELEASE", "AWAITING_RELEASE", state)
    if not (isinstance(cmd, Command) and cmd.kind == "execute_publish"):
        _fail(f"release decision at AWAITING_RELEASE must route Command('execute_publish'), got {cmd!r}")
    # SM-01.7.1 -> SM-01.8: reconciled M-PUBLISH routes the milestone closer.
    cmd = _route(
        "M-PUBLISH", subs["M-PUBLISH"], _fresh_state(stage="M-PUBLISH", stage_exited=True)
    )
    if not (isinstance(cmd, Command) and cmd.kind == "close_milestone"):
        _fail(f"reconciled M-PUBLISH must route Command('close_milestone'), got {cmd!r}")
    # architecture §1.0.14 B: classified in-place repair routes repair dispatch.
    repair_route = {
        "exit_class": "defect_repair",
        "defect_class": "behavior",
        "owner": "guru",
        "discipline": 0,
        "budget_remaining": 3,
    }
    state = _fresh_state(
        stage="M-VERIFY",
        last_failure={"reason": "ci_stale", "repair_route": repair_route},
    )
    cmd = _route("M-VERIFY", subs["M-VERIFY"], state)
    if not (isinstance(cmd, Command) and cmd.kind == "dispatch_repair"):
        _fail(f"classified defect_repair route must route Command('dispatch_repair'), got {cmd!r}")
    # architecture §1.0.14 C: classified irreparable product defect routes
    # known-issue registration.
    repair_route = {
        "exit_class": "irreparable",
        "defect_class": "behavior",
        "owner": "human",
        "discipline": 0,
        "budget_remaining": 0,
    }
    state = _fresh_state(
        stage="M-PUBLISH",
        last_failure={"reason": "publish_failed", "repair_route": repair_route},
    )
    cmd = _route("M-PUBLISH", subs["M-PUBLISH"], state)
    if not (isinstance(cmd, Command) and cmd.kind == "register_known_issue"):
        _fail(f"classified irreparable route must route Command('register_known_issue'), got {cmd!r}")


def test_release_routing_replaces_notimplemented_error():
    try:
        release_stage_defs()
    except NotImplementedError as exc:
        _fail(f"release_stage_defs() is still an IF-VERIFY-001 stub: {exc}")
    state = _fresh_state(stage="M-RELEASE")
    try:
        decide_release_stage("M-RELEASE", "AWAITING_RELEASE", state)
    except NotImplementedError as exc:
        _fail(f"decide_release_stage() is still an IF-VERIFY-001 stub: {exc}")
