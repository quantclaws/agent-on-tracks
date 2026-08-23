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

from .events import Command

if TYPE_CHECKING:
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
    if sub == "PRECHECK":
        if not s.doc_dispatched:
            return _command_precheck_hotfix()
        return None  # awaiting triage.prechecked outcome

    if sub == "SAGE_TRIAGE":
        return _decide_sage_triage(s)

    if sub == "AWAIT_HUMAN":
        return None  # parked; human.anchor event resumes the flow

    return None


def _command_precheck_hotfix() -> Command:
    """PRECHECK (SM-01.1-.4): deterministic issue fetch / scenario / active
    release branch / target-version location — pure program check, no LLM
    (IF-HOTFIX-003 via IF-HOTFIX-002)."""
    return Command(kind="precheck_hotfix")


def _decide_sage_triage(s: State) -> Command | None:
    """SAGE_TRIAGE control flow (SM-01.5-.7, IF-HOTFIX-002/004).

    - First dispatch: issue Sage with the anchor-search assignment.
    - After the Sage outcome arrives (``doc_produced``), issue the programmatic
      cross-version anchor validation (``validate_anchor``).
    - After ``anchor.validated`` (``hotfix_anchor_validated``), issue the
      ANCHORED terminal (``complete_hotfix_entry``, SM-01.10).
    """
    if not s.doc_dispatched:
        return _command_dispatch_sage(s)
    if not s.doc_produced:
        return None  # awaiting Sage outcome
    # Sage outcome received; issue validate_anchor if not yet passed
    if not s.hotfix_anchor_validated:
        if not s.doc_validated:
            return Command(kind="validate_anchor")
        return None  # awaiting validation result (or redispatch)
    # anchor validated, complete the entry
    if not s.baseline_inherited:
        return Command(kind="complete_hotfix_entry")
    return None  # already completed


def _command_dispatch_sage(s: State) -> Command:
    """SAGE_TRIAGE (SM-01.5): dispatch Sage with the anchor-search assignment
    (IF-HOTFIX-004). Carries the current failure evidence (FR-11) so the
    re-dispatch prompt still shows why the previous attempt failed."""
    params = {
        "role": "sage",
        "substate": "SAGE_TRIAGE",
        "objective": "anchor existing ACs for the hotfix issue",
        "stage": "M-HOTFIX-TRIAGE",
        "attempt": s.current_attempt + 1,
        "assignment": {
            "kind": "SAGE_TRIAGE",
            "skill": "tracks-sage",
            "template_kind": None,
        },
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


def _on_hotfix_requested(s: State, p: dict) -> None:
    """Reducer: ``hotfix.requested`` {issue, scenario} (IF-HOTFIX-002).

    Projects the run-establishment fact: ``hotfix_issue`` /
    ``hotfix_scenario`` on State; substate stays PRECHECK (SM-01.1).
    """
    s.hotfix_issue = p.get("issue")
    s.hotfix_scenario = p.get("scenario")


def _on_triage_prechecked(s: State, p: dict) -> None:
    """Reducer: ``triage.prechecked`` (IF-HOTFIX-002, SM-01.2/.3/.4).

    ``status="pass"`` -> substate SAGE_TRIAGE, project
    ``hotfix_target_version`` / ``hotfix_precheck_passed``;
    ``status="rejected"`` -> terminal routing to
    run.completed(terminal_state="rejected") — no branch side effect.
    """
    if p.get("status") == "pass":
        s.substate = "SAGE_TRIAGE"
        s.hotfix_precheck_passed = True
        s.hotfix_target_version = p.get("target_version")
        _reset_hotfix_dispatch(s)
    else:
        # REJECTED terminal: the executor will emit run.completed(rejected).
        # The rejected state is not a parkable substate; the reducer sets
        # substate to None so decide() returns None and the executor handles
        # the terminal transition.
        s.substate = None


def _on_anchor_validated(s: State, p: dict) -> None:
    """Reducer: ``anchor.validated`` {acs, source, attempt} (IF-HOTFIX-002).

    Projects ``hotfix_anchor_acs`` / ``hotfix_anchor_validated``; SM-01.5/.8
    route to the ANCHORED terminal transition (complete_hotfix_entry).
    """
    s.hotfix_anchor_acs = p.get("acs")
    s.hotfix_anchor_validated = True
    s.doc_validated = True  # mark validate_anchor as completed


def _on_human_anchor(s: State, p: dict) -> None:
    """Reducer: ``human.anchor`` {mode, acs, issue, actor} (IF-HOTFIX-002).

    ``mode="manual"`` -> validate_anchor then ANCHORED (SM-01.8);
    ``mode="feature_route"`` -> FEATURE_ROUTE terminal (SM-01.9/.11):
    backlog.recorded + run.completed(feature_route), no fix branch.
    """
    mode = p.get("mode")
    if mode == "manual":
        s.hotfix_anchor_acs = p.get("acs")
        s.status = "active"
        s.awaiting = None
        # Human already supplied the anchor refs: skip the Sage dispatch step
        # and go straight to programmatic validation in the SAGE_TRIAGE
        # decide() control flow (validate_anchor -> ANCHORED, SM-01.8).
        s.substate = "SAGE_TRIAGE"
        s.doc_dispatched = True
        s.doc_produced = True
        s.doc_validated = False
    elif mode == "feature_route":
        # FEATURE_ROUTE terminal: the executor emits backlog.recorded {issue,
        # decision: feature_route} then run.completed(feature_route). No fix
        # branch, no dangling run (SM-01.9/.11, NFR-0100-03). Substate goes
        # None so decide() halts until the terminal events land.
        s.substate = None
        s.status = "active"
        s.awaiting = None


def _on_baseline_inherited(s: State, p: dict) -> None:
    """Reducer: ``baseline.inherited`` {target_version, baseline_digest,
    anchor_acs, baseline_doc_paths} (IF-HOTFIX-005 via IF-HOTFIX-002).

    Projects ``baseline_inherited``; source-approval record only — the
    hotfix run never creates M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL artifacts
    (FR-0241-02).
    """
    s.baseline_inherited = True


def _on_increment_declared(s: State, p: dict) -> None:
    """Reducer: ``increment.declared`` {shield, unit_rows, trace_status}
    (IF-HOTFIX-007 via IF-HOTFIX-002).

    Empty-Shield-increment M-TEST release evidence (FR-0244-04): projects the
    explicit unit-only increment fact onto State. Only a shield=empty
    declaration carrying NONEMPTY unit rows is a real increment (review pin:
    ``shield=empty`` with zero declared rows carries no increment to release,
    and the M-TEST empty-R2 bypass consumes exactly this persisted fact --
    never a bare hotfix issue).
    """
    if p.get("shield") == "empty" and p.get("unit_rows"):
        s.increment_declared = {
            "shield": "empty",
            "unit_rows": list(p["unit_rows"]),
            "trace_status": p.get("trace_status", ""),
        }
    # No substate change — the existing M-TEST EXIT flow handles this.
    s.last_failure = None  # clear any stale failure evidence


def _reset_hotfix_dispatch(s: State) -> None:
    """Reset the hotfix dispatch flags so decide() can issue the next command."""
    s.doc_dispatched = False
    s.doc_produced = False
    s.doc_validated = False
