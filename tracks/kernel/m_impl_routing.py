"""M-IMPL outcome, verdict, and failure routing.

Extracted from :mod:`tracks.kernel.m_impl` for module-size compliance
(C0302): the stage-sensitive prism/verdict routers, the DIAGNOSE
attribution routing, the data-driven gate-failure routes, and the
OPEN-ledger diagnosis focus. :mod:`tracks.kernel.m_impl` re-exports every
name below, so the machine import surface is unchanged.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .m_impl_decide import _is_verification_task
from .m_impl_state import _M_IMPL_REVIEW_SUBSTATES, _clear_task_residency
from .m_test import _consume_attempt, _reset_doc, _reset_review

if TYPE_CHECKING:
    from .machine import State

# -- M-IMPL outcome / verdict routing ---------------------------------------


def _on_m_impl_outcome_done(s: State) -> None:
    """Route a successful (status=done) M-IMPL outcome by substate."""
    if s.substate == "PLANNING":
        s.doc_produced = True
    elif s.substate == "RULING":
        # M1-S1: Archer's paired-delta ruling is delivered. Route into
        # DIAGNOSE with the ruling as evidence so Prism's classification
        # dispatches the ruled fix (until the M3 bundle channel lands the
        # pairing natively).
        s.substate = "DIAGNOSE"
        s.diagnose_classification = None
        _reset_review(s)
    elif s.substate == "RED":
        s.substate = "RED_GATE"
    elif s.substate == "GREEN":
        s.substate = "GREEN_GATE"
    elif s.substate == "REFACTOR":
        s.substate = "REFACTOR_GATE"
    elif s.substate == "SHIELD_FIX":
        if _is_verification_task(s):
            # test_defect on a verification-only task is fixed: re-run the
            # Runtime acceptance via the RED->verify_task path. GREEN_GATE
            # would instead check for a Devon GREEN evidence and trip over
            # the stale pre-fix RED outcome (run 01KZTHE7 T-008).
            s.substate = "RED"
            _reset_doc(s)
        else:
            s.substate = "GREEN_GATE"
    elif s.substate in _M_IMPL_REVIEW_SUBSTATES:
        s.reviewer_produced = True


def _on_m_impl_prism_verdict(s: State, p: dict) -> None:
    """Stage-sensitive prism.verdict routing for M-IMPL (flow.md §10.1)."""
    s.criteria_pack_loaded = p.get("criteria_pack")
    _reset_review(s)
    verdict = p["verdict"]
    if s.substate == "PRISM_PLAN":
        if verdict == "pass":
            s.substate = "TASK_DISPATCH"
        else:
            _route_prism_plan_revise(s, p)
    elif s.substate == "PRISM_RED":
        if verdict == "pass":
            s.substate = "GREEN"
            _reset_doc(s)
        elif (
            p.get("defect_classification")
            or _sole_finding_classification(p)
        ) == "plan_defect":
            # A fully green R tree has no lawful RED target: the task is
            # duplicate, obsolete, or wrongly typed as standard RGR. Re-pinning
            # RED cannot create a legal failure and deterministically burns the
            # Devon budget (run 01M0S0FQ T-017, v0.7 boundary, 2026-08-29).
            # Archer must remove/re-scope it or mark it verification-only.
            _route_scope_replan(s)
        else:
            # Genuine RED-test defects and ordinary RED review revisions stay
            # in Devon's RED domain and create a fresh immutable R slot.
            s.substate = "RED"
            _reset_doc(s)
            _consume_attempt(s)
    elif s.substate == "PRISM_FINAL":
        if verdict == "pass":
            s.substate = "TASK_DONE"
        else:
            _route_prism_final_revise(s, p)


def _route_prism_plan_revise(s: State, p: dict) -> None:
    """PRISM_PLAN revise: route by defect_classification (flow.md §10.1),
    falling back to the findings' sole voice (see
    ``_sole_finding_classification``)."""
    dc = p.get("defect_classification") or _sole_finding_classification(p)
    if dc in ("design_gap", "stub_gap"):
        s.substate = "DIAGNOSE"
        s.diagnose_classification = "stub_gap"
    elif dc == "ac_gap":
        s.status = "awaiting_human"
        s.awaiting = "rollback"
        s.return_target = "M-ACC"
    elif dc == "spec_gap":
        s.status = "awaiting_human"
        s.awaiting = "rollback"
        s.return_target = "M-SPEC"
    else:
        s.substate = "PLANNING"
        _reset_doc(s)
        # The revised task graph must be re-committed and re-reviewed: with
        # the round-1 flag left set, _decide_m_impl_planning returns None
        # forever once the revision outcome lands (doc_produced=True,
        # taskgraph_committed=True) - the loop exits and no restart
        # re-commits (run 01KZTHE7 PLANNING round 2, 2026-08-15).
        s.taskgraph_committed = False
        _consume_attempt(s)


def _sole_finding_classification(p: dict) -> str | None:
    """The findings' defect classification when they speak with one voice.

    Live defect (run 01M2QTJB T-002, 2026-09-20 rounds 7-8): the reviewer
    put the routing classification on each FINDING (defect_classification:
    test_defect, per the criteria pack's substate-routing vocabulary) and
    spelled the routing in the summary prose, but never lifted it to the
    verdict's top level -- the router read ``p.get('defect_classification')``
    (None), defaulted to impl_defect, and re-dispatched Devon GREEN against
    a frozen-test defect Devon may not touch: two zero-delta no-op rounds
    and an unbounded revise loop. Conservative derivation: exactly ONE
    distinct classification across findings wins; none, or mixed voices,
    stay None (the router's default keeps the historical behavior).
    """
    classifications = {
        str(f.get("defect_classification"))
        for f in (p.get("findings") or [])
        if isinstance(f, dict) and f.get("defect_classification")
    }
    if len(classifications) == 1:
        return classifications.pop()
    return None


def _route_prism_final_revise(s: State, p: dict) -> None:
    """PRISM_FINAL revise: route by defect_classification (flow.md §10.1).

    The classification comes from the verdict top level, falling back to
    the findings' sole voice (see ``_sole_finding_classification``)."""
    dc = p.get("defect_classification") or _sole_finding_classification(p)
    if dc == "red_defect":
        s.substate = "RED"
        s.green_committed = False
    elif dc == "plan_defect":
        # #89: PRISM_FINAL revise plan_defect — Archer's scope-split
        # defect, not Devon's. Route to PLANNING replan; do NOT consume
        # attempt. _route_scope_replan handles all state cleanup.
        _route_scope_replan(s)
        return
    elif dc == "test_defect":
        # Criteria-pack routing semantics (tracks-prism-impl, PRISM_FINAL):
        # a frozen-acceptance test's own defect is the test writer's domain
        # -- Shield, never Devon (whose manifest forbids the frozen tests).
        # Mirrors DIAGNOSE's test_defect branch exactly (live rounds 7-8:
        # the missing branch pinned the run in a GREEN no-op loop).
        s.substate = "SHIELD_FIX"
        _reset_doc(s)
        _consume_attempt(s)
        return
    else:
        s.substate = "GREEN"
        s.green_committed = False
    s.refactor_done = False
    _reset_doc(s)
    _consume_attempt(s)


def _on_m_impl_verdict_passed(s: State, p: dict) -> None:
    """verdict.passed routing for M-IMPL gates (flow.md §10.1)."""
    check = p.get("check")
    if check == "island_1":
        s.substate = "PRISM_PLAN"
        _reset_review(s)
    elif check == "red_valid":
        s.substate = "RED_CHECKPOINT"
    elif check == "green":
        s.substate = "GREEN_COMMIT"
    elif check == "task_review":
        s.substate = "PRISM_FINAL"
        _reset_review(s)
    elif check == "island_2":
        s.island_2_passed = True
        s.substate = "EXIT"
    s.last_failure = None


_RED_NO_RICE = frozenset({"missing", "unexpected_pass"})


def _red_missing_only(evidence) -> bool:
    if not isinstance(evidence, str) or not evidence.strip():
        return False
    try:
        payload = json.loads(evidence)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    classifications = payload.get("classifications")
    if not isinstance(classifications, list) or not classifications:
        return False
    return all(item in _RED_NO_RICE for item in classifications)


def _route_red_invalid(s: State, evidence) -> None:
    if _red_missing_only(evidence):
        _route_scope_replan(s)
        return
    s.substate = "RED"
    _reset_doc(s)
    _consume_attempt(s)


def _on_m_impl_verdict_failed(s: State, p: dict) -> None:
    """verdict.failed routing for M-IMPL (flow.md §10.1)."""
    check = p.get("check")
    if s.substate == "DIAGNOSE":
        # Snapshot the full verdict details: SHIELD_FIX re-dispatches (any
        # attempt, even after `trac retry --clear-evidence`) read them from
        # State so the fixer never re-derives Prism's analysis.
        s.diagnose_report = {
            k: p.get(k)
            for k in ("check", "reason", "evidence", "log_ref", "attempt")
        }
        _route_m_impl_diagnose(s, check)
        return
    _route_m_impl_gate_failure(s, check, p.get("evidence"))


def _route_m_impl_diagnose(s: State, check: str) -> None:
    """DIAGNOSE four-way routing (flow.md §10.1, FR-0150)."""
    s.diagnose_classification = check
    if check == "impl_defect":
        if s.r_tree_identity is None:
            s.substate = "RED"
        else:
            # #86: legal same-R re-commit vs crash-replay -- with an R
            # checkpoint held, impl_defect re-enters GREEN but must drop the
            # green_committed flag so the runtime idempotency guard lets the
            # revise issue a NEW green.committed for the same R slot instead
            # of no-op'ing forever (decide() then re-emits commit_green).
            s.substate = "GREEN"
            s.green_committed = False
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "red_defect":
        # B63 blocker (#81): a defective RED unit test is Devon's own RED
        # artifact — the only rightful owner is Devon and the only legal
        # rewrite phase is RED (re-pin). test_defect would misroute to
        # Shield (frozen-acceptance domain); impl_defect re-enters GREEN,
        # which may not touch the frozen R tests (run 01M0S0FQ T-002:
        # structurally unsatisfiable GREEN loop). Mirror PRISM_FINAL's
        # red_defect branch: the G/R state resets so the full cycle
        # re-verifies forward.
        s.substate = "RED"
        s.green_committed = False
        s.refactor_done = False
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "plan_defect":
        # #89: DIAGNOSE plan_defect — the fix requires modifying files outside
        # the current task's manifest allowed_paths (task-graph scope-split
        # defect). Route to Archer replan: PLANNING, clear task identity,
        # preserve evidence, do NOT consume attempt (Archer's scope error, not
        # Devon's).
        _route_scope_replan(s)
    elif check == "test_defect":
        s.substate = "SHIELD_FIX"
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "stub_gap":
        pass  # decide() produces rollback_stage(M-DESIGN)
    elif check in ("ac_gap", "spec_gap"):
        s.status = "awaiting_human"
        s.awaiting = "rollback"
        s.return_target = "M-ACC" if check == "ac_gap" else "M-SPEC"
    elif check == "unknown":
        # M2 (convergence plan 2026-09-05): the honest exit -- Prism could
        # not reproduce the failure on the forensic package, so it named no
        # owner. Stay in DIAGNOSE: the re-dispatch assignment carries the
        # forensic package (executor surfaces it on every verdict), so the
        # next round rules on live evidence, never post-teardown inference
        # (T-042 :78 misdiagnosis class). Consumes the diagnosis budget --
        # an empty diagnosis is still a spent round; a repeated unknown
        # arrives as diagnosis_exhausted and routes the authority instead.
        _reset_review(s)
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "diagnosis_exhausted":
        # M1-S2/M2: repeated unknown attribution -- DIAGNOSE extracted no
        # new information twice in a row. Route the contract authority
        # (Archer RULING, same channel as contract_conflict): the ruling
        # rides the forensic package + diagnosis history in last_failure;
        # NOT a writer failure -- no attempt consumed.
        s.substate = "RULING"
        _reset_review(s)
        _reset_doc(s)
    else:
        # B62 (#80): an unrecognized check (e.g. diagnose_contract_violation
        # from a contract-violating DIAGNOSE reply) STAYS in DIAGNOSE for the
        # promised re-dispatch + budget-exhaustion escalation. Unlike the
        # impl_defect/test_defect branches above (which leave this substate
        # and deliberately keep reviewer flags until task_review resets
        # them), staying here means decide() runs _decide_m_impl_prism,
        # which returns None on a stale reviewer_dispatched — the loop then
        # exits silently and the attempt budget can never be consumed (run
        # 01M0S0FQ T-006, 2026-08-25: DIAGNOSE attempt 2 contract violation
        # stalled active/DIAGNOSE across process restarts).
        _reset_review(s)
        _reset_doc(s)
        _consume_attempt(s)


def _route_scope_replan(s: State) -> None:
    """B83 (#83): a TASK_REVIEW scope verdict is an Archer task-plan defect.

    The plan's file ownership is unimplementable (the delivered interfaces
    require touching a path no task owns), so Devon re-dispatch against the
    same immutable manifest is a deterministic budget-burn loop (run
    01M0S0FQ T-007: guard_registry.py facade). Route to PLANNING for Archer
    to replan (merge/re-scope tasks); the replacement commit re-derives
    retained completions from event history. No Devon/Shield dispatch, no
    attempt consumed; task residency state is cleared so the replaced
    graph's task cannot be resurrected (the stale writelock lease is
    released at the next select_task)."""
    s.substate = "PLANNING"
    _reset_doc(s)
    s.taskgraph_committed = False
    _clear_task_residency(s)


def _route_parked_failure(s: State, check: str) -> None:
    """Failures that park the run for the Human instead of auto-routing.

    Extracted from ``_route_m_impl_gate_failure`` for cognitive-complexity
    compliance (CCR001); behavior is unchanged.
    """
    if check == "lineage":
        # B57 (#73, re-fixed 2026-08-27): a lineage failure means the recorded
        # RGR lineage (R ref + G trailers + event sequence) cannot be jointly
        # proven. R refs and G commits are immutable, so re-running the gate
        # re-verifies the same events and fails deterministically forever --
        # the old else-branch (stay in substate + consume attempt) parked
        # TASK_REVIEW in an eternal re-verification loop (run 01M0S0FQ: three
        # identical verdict.failed(lineage) rounds survived both `trac retry`
        # and `retry --clear-evidence`). The G was produced by defective
        # runtime code: the recorded lineage is untrustworthy, not reworkable
        # in place -- park for the Human (stop-fix-restart).
        # The original fix hardcoded return_target=M-DESIGN for every lineage
        # failure. That repeats the exact mistake B49 (#64) condemned --
        # sending runtime bugs to be "fixed" in a design that is not
        # defective -- and its blast radius was paid in full on 2026-08-27
        # (run 01M0S0FQ T-013: B91 shipped a bad G, the rollback re-ran an
        # unaffected M-DESIGN and regenerated three design docs). A lineage
        # failure implicates only M-IMPL machinery, and it is a Human gate:
        # the Human chooses the return target at approval time
        # (`trac approve --to {M-ACC|M-SPEC|M-DESIGN}`, closed set enforced by
        # the CLI; forward re-entry without rollback goes through the existing
        # `trac recover --to M-IMPL`, B32 #32). return_target stays None here;
        # _on_human_approval adopts the approved to_stage into it.
        s.status = "awaiting_human"
        s.awaiting = "rollback"
        _reset_review(s)
    elif check == "contract_error":
        # B49 (#64): a gate-contract mismatch (planning convention vs runtime
        # enforcement, or an unusable Archer-owned contract) cannot be
        # reliably auto-attributed. The 2026-08-24 stub_gap auto-rollback
        # loop (run 01M0S0FQ, three M-DESIGN rollbacks for runtime-side
        # defects) proved routing it into DIAGNOSE->M-DESIGN sends runtime
        # bugs to be "fixed" in a design that is not defective. Park for the
        # operator: human.retry after the underlying fix re-dispatches the
        # gate from the current substate (fresh attempt budget, no rollback,
        # no Devon dispatch burned).
        s.status = "awaiting_human"
        s.awaiting = "escalation"
        _reset_review(s)


# Data-driven simple failure routes: check -> (substate, classification).
# These checks have no overlap with the chained branches below (distinct
# check strings), so the dict lookup can run first without changing
# routing semantics.
_FAILURE_ROUTES = {
    "public_interface": ("DIAGNOSE", "stub_gap"),
    "verification_failed": ("DIAGNOSE", None),
    # M1-S1 (convergence plan 2026-09-05): mechanical oscillation signature
    # -- consecutive GREEN_GATE failures swapped anchor outcomes (healed AND
    # newly_red AND common). Mutually exclusive anchors: no writer retry can
    # satisfy both (T-042 test_verify_candidate.py:150 <->
    # test_inplace_repair.py rewalk, 2026-09-04, ~40 burned dispatches).
    # contract_conflict is NOT in this dict: it needs _reset_doc as well
    # (see the explicit branch below) -- a stale doc_dispatched left over
    # from the interrupted GREEN dispatch would make _decide_m_impl_ruling
    # return None forever and the loop would exit silently with the run
    # parked in RULING (live 2026-09-05: the drift-restart watcher then
    # refused the non-drift death and the run stalled).
    # Gate-level attribution routing directly into DIAGNOSE with a preset
    # classification: reset the reviewer flag so decide() can dispatch the
    # Prism DIAGNOSE review. Without this, a stale reviewer_dispatched
    # (e.g. left over from the previous DIAGNOSE round whose verdict routed
    # back to GREEN via _route_m_impl_diagnose, which deliberately does not
    # reset - task_review pass resets it later) makes _decide_m_impl_prism
    # return None forever and the run loop exits.
    "test_defect": ("DIAGNOSE", "test_defect"),
    "impl_defect": ("DIAGNOSE", "impl_defect"),
    "stub_gap": ("DIAGNOSE", "stub_gap"),
    "ac_gap": ("DIAGNOSE", "ac_gap"),
    "spec_gap": ("DIAGNOSE", "spec_gap"),
}


def _route_m_impl_gate_failure(s: State, check: str, evidence=None) -> None:
    """Gate failure routing (non-DIAGNOSE substates)."""
    simple = _FAILURE_ROUTES.get(check)
    if simple is not None:
        s.substate, s.diagnose_classification = simple
        _reset_review(s)
        return
    if check == "plan_defect":
        # M5 (convergence plan 2026-09-05): dispatch-side assignment
        # budget rejection -- the card itself is structurally over
        # budget (scope bloat), a task-graph defect. Same route as the
        # DIAGNOSE plan_defect (#89): Archer replans, no attempt charge.
        _route_scope_replan(s)
        return
    if check == "contract_conflict":
        # M1-S1: route the contract authority (Archer RULING, dual-side
        # read visibility) with the oscillation dossier; NOT a writer
        # failure -- no attempt consumed. Reset the doc flags too so
        # decide() dispatches Archer instead of awaiting a phantom
        # outcome (silent-exit bug, live 2026-09-05).
        s.substate = "RULING"
        _reset_review(s)
        _reset_doc(s)
        return
    if check == "criteria_pack_mismatch":
        _reset_review(s)
        _consume_attempt(s)
    elif check in ("unknown_attribution", "full_suite"):
        if check == "full_suite":
            _focus_open_ledger(s)
        s.substate = "DIAGNOSE"
        _reset_review(s)
    elif check == "island":
        s.substate = "PLANNING"
        _reset_doc(s)
        # A failed island gate invalidates the current task graph. The
        # re-dispatched planning result must pass through commit_taskgraph
        # again; retaining this flag strands PLANNING after Archer returns.
        s.taskgraph_committed = False
        _consume_attempt(s)
    elif check == "red_invalid":
        _route_red_invalid(s, evidence)
    elif check == "lint":
        # B4 (issue #5, user ruling 2026-08-18): mechanical lint findings
        # route back to the producing phase for rework but do NOT consume
        # the attempt budget — attempts are reserved for semantic
        # (agent-caused) failures; a lint round-trip never burns it.
        s.substate = "RED" if s.substate == "RED_GATE" else "GREEN"
        _reset_doc(s)
    elif check == "regression":
        # B63 (#81): R-test regression must be ATTRIBUTED, not blindly
        # retried. A GREEN-phase agent cannot legally modify RED-approved
        # tests, so when the R tests themselves are defective (wrong
        # fixture) there is no legal path to green: the agent either
        # re-violates (convicted again by the digest check) or freezes
        # while earlier residue keeps convicting it — either way a blind
        # GREEN re-dispatch reproduces the same failure forever (run
        # 01M0S0FQ T-002 2026-08-25: three regression rounds -> escalation
        # -> operator archaeology). Mirror verification_failed: route into
        # the four-way DIAGNOSE so Prism attributes — impl_defect -> Devon
        # GREEN fix, red_defect -> RED re-pin of the defective tests
        # (T-006 round-3 precedent), test_defect -> Shield. Applies to the
        # REFACTOR-gate variant identically (impl_defect re-enters GREEN,
        # the gate chain re-verifies forward).
        s.substate = "DIAGNOSE"
        _reset_review(s)
        _consume_attempt(s)
    elif check == "budget":
        s.substate = "GREEN"
        # #86 (follow-up): the budget route re-enters GREEN for a task whose
        # green.committed may already be recorded (same immutable R -> same
        # lineage slot). Without this reset the B56 guard no-ops the re-commit
        # and decide() livelocks on commit_green (run 01M0S0FQ T-004: the
        # route back was budget, not PRISM_FINAL revise).
        s.green_committed = False
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "scope":
        _route_scope_replan(s)
    elif check in ("lineage", "contract_error"):
        _route_parked_failure(s, check)
    else:
        _reset_substate_dispatch_flag(s)
        _consume_attempt(s)


def _reset_substate_dispatch_flag(s: State) -> None:
    """Reset the flag the CURRENT substate actually waits on.

    2026-09-19 (run 01M2QTJB PRISM_RED): the generic fallthrough used to
    clear only doc flags -- a reply_format_error verdict on a review
    substate left reviewer_dispatched set and decide() parked the run
    forever after the review reply failed classification."""
    if s.substate in _M_IMPL_REVIEW_SUBSTATES:
        _reset_review(s)
    else:
        _reset_doc(s)


def _focus_open_ledger(s: State) -> None:
    """Bind DIAGNOSE/fixer dispatches to the next deterministic OPEN identity."""
    candidates = [
        entry
        for _key, entry in sorted(s.ledger_entries.items())
        if entry.get("state") == "OPEN"
    ]
    if not candidates:
        return
    entry = candidates[0]
    if entry.get("task_id"):
        s.current_task_id = entry.get("task_id")
        task = entry.get("task")
        s.current_task_metadata = dict(task) if isinstance(task, dict) else None
        manifest = entry.get("manifest")
        s.current_manifest = dict(manifest) if isinstance(manifest, dict) else None
        s.r_tree_identity = entry.get("r_sha") or None
