"""HOTFIX-TRIAGE entry substate machine (v0.6, IF-HOTFIX-002).

Kernel-side control flow for the hotfix entry triage (SPEC-006 SM-01 /
flow.md §16.4). HOTFIX-TRIAGE is an *attached* substate machine, not a
canonical top-level stage: the stage value ``M-HOTFIX-TRIAGE`` is written by
``cmd_hotfix`` and never enters the ``_NEXT_STAGE`` chain; its events land on
the hotfix run's own event stream (the run is established at entry).

This module keeps the kernel pure-function boundary (NFR-0010): decide()
produces Commands, reducers consume events; all I/O (issue fetch, branch
probing, file scanning) lives in ``tracks/executor/hotfix.py``.

M-DESIGN scaffold stub (ARCH-006 §2): signatures are frozen by
interfaces.md §1a/§1b/§1c; Devon fills the bodies without changing the
declared contracts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracks.kernel.events import Command
    from tracks.kernel.machine import State

# IF-HOTFIX-002 closed sets (interfaces.md §1c). The stage value is not
# registered in _STAGES/_NEXT_STAGE: entry/exit transitions are driven by
# cmd_hotfix and the ANCHORED terminal transition (stage.entered M-DESIGN).
HOTFIX_TRIAGE_STAGE = "M-HOTFIX-TRIAGE"

HOTFIX_TRIAGE_SUBSTATES = ("PRECHECK", "SAGE_TRIAGE", "AWAIT_HUMAN")

# verdict.failed check member emitted when anchor validation fails (SM-01.6).
ANCHOR_INVALID_CHECK = "anchor_invalid"

# prism.verdict payload member routing an overturned anchor back to
# SAGE_TRIAGE without consuming the M-DESIGN redispatch budget (FR-0243-03).
ANCHOR_OVERTURNED = "anchor_overturned"


def _decide_hotfix_triage(s: State, sub: str) -> Command | None:
    """SM-01 control-flow branch for stage=M-HOTFIX-TRIAGE (IF-HOTFIX-002).

    Dispatches by substate (closed set ``HOTFIX_TRIAGE_SUBSTATES``):

    - ``PRECHECK``      -> Command(kind="precheck_hotfix")
    - ``SAGE_TRIAGE``   -> Command(kind="dispatch_agent", role="sage",
                                    substate="SAGE_TRIAGE") then
                           Command(kind="validate_anchor") on outcome
    - ``AWAIT_HUMAN``   -> None (parked; ``trac hotfix anchor`` /
                           ``trac hotfix feature-route`` resume it)

    Terminal transitions (not parkable substates): ANCHORED ->
    ``complete_hotfix_entry`` then stage.entered(M-DESIGN) (SM-01.10);
    REJECTED -> run.completed(terminal_state="rejected") (SM-01.4);
    FEATURE_ROUTE -> backlog.recorded + run.completed(feature_route)
    (SM-01.11). Undeclared transitions are not allowed.

    SAGE_TRIAGE anchor-validation failures redispatch Sage with the shared
    <=3 attempt budget; NO_ANCHOR or exhaustion parks at AWAIT_HUMAN and
    never auto-routes to feature (SM-01.7, NFR-0100-03).
    """
    raise NotImplementedError("IF-HOTFIX-002: _decide_hotfix_triage")


def _on_hotfix_requested(s: State, p: dict) -> None:
    """Reducer: ``hotfix.requested`` {issue, scenario} (IF-HOTFIX-002).

    Projects the run-establishment fact: ``hotfix_issue`` /
    ``hotfix_scenario`` on State; substate stays PRECHECK (SM-01.1).
    """
    raise NotImplementedError("IF-HOTFIX-002: _on_hotfix_requested")


def _on_triage_prechecked(s: State, p: dict) -> None:
    """Reducer: ``triage.prechecked`` (IF-HOTFIX-002, SM-01.2/.3/.4).

    ``status="pass"`` -> substate SAGE_TRIAGE, project
    ``hotfix_target_version`` / ``hotfix_precheck_passed``;
    ``status="rejected"`` -> terminal routing to
    run.completed(terminal_state="rejected") — no branch side effect.
    """
    raise NotImplementedError("IF-HOTFIX-002: _on_triage_prechecked")


def _on_anchor_validated(s: State, p: dict) -> None:
    """Reducer: ``anchor.validated`` {acs, source, attempt} (IF-HOTFIX-002).

    Projects ``hotfix_anchor_acs`` / ``hotfix_anchor_validated``; SM-01.5/.8
    route to the ANCHORED terminal transition (complete_hotfix_entry).
    """
    raise NotImplementedError("IF-HOTFIX-002: _on_anchor_validated")


def _on_human_anchor(s: State, p: dict) -> None:
    """Reducer: ``human.anchor`` {mode, acs, issue, actor} (IF-HOTFIX-002).

    ``mode="manual"`` -> validate_anchor then ANCHORED (SM-01.8);
    ``mode="feature_route"`` -> FEATURE_ROUTE terminal (SM-01.9/.11):
    backlog.recorded + run.completed(feature_route), no fix branch.
    """
    raise NotImplementedError("IF-HOTFIX-002: _on_human_anchor")


def _on_baseline_inherited(s: State, p: dict) -> None:
    """Reducer: ``baseline.inherited`` {target_version, baseline_digest,
    anchor_acs, baseline_doc_paths} (IF-HOTFIX-005 via IF-HOTFIX-002).

    Projects ``baseline_inherited``; source-approval record only — the
    hotfix run never creates M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL artifacts
    (FR-0241-02).
    """
    raise NotImplementedError("IF-HOTFIX-002: _on_baseline_inherited")


def _on_increment_declared(s: State, p: dict) -> None:
    """Reducer: ``increment.declared`` {shield, unit_rows, trace_status}
    (IF-HOTFIX-007 via IF-HOTFIX-002).

    Empty-Shield-increment M-TEST release evidence (FR-0244-04): records the
    declared unit-layer regression rows as the plan-level closure basis.
    """
    raise NotImplementedError("IF-HOTFIX-002: _on_increment_declared")
