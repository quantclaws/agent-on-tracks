"""M-IMPL state-machine logic (flow.md §10).

Extracted from ``machine.py`` for module-size compliance (C0302). Contains
the M-IMPL reducers, decide() control flow, criteria-pack constants, and
dispatch builders.

No circular import: this module imports only from ``events`` at runtime;
``State`` is imported under ``TYPE_CHECKING`` only (duck-typed at runtime).
``machine.py`` imports the helpers, reducers, and decide functions from here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .contracts import DEVON_EVIDENCE_CONTRACT, WRITE_MANIFEST_CONTRACT
from .events import Command, EventEnvelope
from .m_test import _consume_attempt, _reset_doc, _reset_review

if TYPE_CHECKING:
    from .machine import State


# -- M-IMPL constants -------------------------------------------------------

# D-29 criteria pack identity (architecture.md §3.4): Prism in M-IMPL loads
# the impl criteria pack and echoes the identity in its verdict.
_M_IMPL_CRITERIA_PACK = {"name": "tracks-prism-impl", "version": "0.1"}
# M-IMPL context docs: trio + design trio (flow.md §10 BASELINE).
_M_IMPL_CONTEXT_DOCS = (
    "story.md",
    "spec.md",
    "acceptance.md",
    "architecture.md",
    "interfaces.md",
    "test-plan.md",
)
# Prism review substates in M-IMPL (flow.md §10.1).
_M_IMPL_REVIEW_SUBSTATES = ("PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE")


# -- M-IMPL event reducers (flow.md §10.2) ----------------------------------


def _on_baseline_frozen(s: State, p: dict, ev: EventEnvelope) -> None:
    """BASELINE: status=current -> PLANNING; stale/missing -> NEEDS_ATTENTION."""
    if p.get("status") == "current":
        s.baseline_frozen = True
        s.substate = "PLANNING"
    else:
        s.substate = "NEEDS_ATTENTION"


def _on_taskgraph_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    """PLANNING: task graph validated and committed -> ISLAND_GATE_1.

    On replacement (re-plan), carries retained_completed_task_ids so that
    only payload-equivalent completed tasks carry over (B83)."""
    is_replacement = bool(s.task_refs)
    s.taskgraph_committed = True
    s.tasks_total = p.get("task_count", 0)
    s.taskgraph_digest = p.get("digest")
    s.taskgraph_path = p.get("path") or p.get("tasks_json")
    refs = p.get("tasks", [])
    s.task_refs = [dict(ref) for ref in refs if isinstance(ref, dict)]
    retained = p.get("retained_completed_task_ids", [])
    s.retained_completed_task_ids = list(retained) if isinstance(retained, list) else []
    s.tasks_completed = len(s.retained_completed_task_ids)
    if is_replacement:
        s.taskgraph_generation += 1
    s.substate = "ISLAND_GATE_1"


def _on_task_started(s: State, p: dict, ev: EventEnvelope) -> None:
    """TASK_DISPATCH: Runtime selected a ready task -> RED (fresh RGR cycle).

    All tasks start at RED, including integration tasks: an integration
    task's RED is a runtime walk_red anchor confirmation (seal the unit
    R-tree on site, no Devon dispatch), never a GREEN start with an
    unsealed R lineage (run 01M19FJVES7G113RD8QXXY3PQZ T-042)."""
    s.current_task_id = p.get("task_id")
    task = p.get("task")
    s.current_task_metadata = dict(task) if isinstance(task, dict) else None
    manifest = p.get("manifest")
    s.current_manifest = dict(manifest) if isinstance(manifest, dict) else None
    s.substate = "RED"
    _reset_doc(s)
    s.current_attempt = 0
    s.green_committed = False
    s.refactor_done = False
    s.r_tree_identity = None
    s.diagnose_classification = None
    s.diagnose_report = None


def _on_writelock_granted(s: State, p: dict, ev: EventEnvelope) -> None:
    s.writelock_held = True


def _on_writelock_released(s: State, p: dict, ev: EventEnvelope) -> None:
    s.writelock_held = False


def _on_red_checkpointed(s: State, p: dict, ev: EventEnvelope) -> None:
    """RED_CHECKPOINT: private R commit created -> PRISM_RED.

    B91 follow-up (re-baseline): a sanctioned Shield-fix checkpoint
    (``sanction: shield_fix``) lands MID-CYCLE after ``test.committed`` -- it
    only re-anchors the lineage baseline (SM-01.14 already pointed
    r_tree_identity at the same fix commit) and must NOT re-enter the RED
    review substate, which would derail the running GREEN cycle."""
    s.r_tree_identity = p.get("r_sha")
    if p.get("sanction") == "shield_fix":
        return
    s.substate = "PRISM_RED"
    _reset_review(s)


def _on_green_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    """GREEN_COMMIT: formal G commit (parent=B) -> REFACTOR."""
    s.green_committed = True
    s.substate = "REFACTOR"
    _reset_doc(s)


def _on_green_no_change(s: State, p: dict, ev: EventEnvelope) -> None:
    """GREEN_COMMIT: no worktree diff + explicit no_change_reason -> TASK_REVIEW."""
    s.green_committed = True
    s.refactor_done = True
    s.substate = "TASK_REVIEW"


def _on_refactor_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    """REFACTOR_GATE: refactor commit -> TASK_REVIEW."""
    s.refactor_done = True
    s.substate = "TASK_REVIEW"
    _reset_doc(s)


def _on_refactor_no_change(s: State, p: dict, ev: EventEnvelope) -> None:
    """REFACTOR_GATE: Devon returns no-change + reason -> TASK_REVIEW."""
    s.refactor_done = True
    s.substate = "TASK_REVIEW"
    _reset_doc(s)


def _on_task_completed(s: State, p: dict, ev: EventEnvelope) -> None:
    """TASK_DONE: task.completed -> TASK_DISPATCH (more tasks) or ISLAND_GATE_2.

    Skips increment for tasks already counted in retained_completed_task_ids
    (B83 generation-aware projection)."""
    task_id = p.get("task_id")
    if task_id is not None and task_id in s.retained_completed_task_ids:
        return
    s.tasks_completed += 1
    s.green_committed = False
    s.refactor_done = False
    s.r_tree_identity = None
    s.current_task_id = None
    s.current_task_metadata = None
    s.current_manifest = None
    _reset_doc(s)
    if s.tasks_completed >= s.tasks_total:
        s.substate = "ISLAND_GATE_2"
    else:
        s.substate = "TASK_DISPATCH"


def _on_full_executed(s: State, p: dict, ev: EventEnvelope) -> None:
    """Project the latest FULL round for replay and exit gating."""
    s.full_chain_round = p.get("round")


def _on_ledger_opened(s: State, p: dict, ev: EventEnvelope) -> None:
    """A newly opened ledger identity is non-terminal until PROVEN."""
    s.ledger_rebuilt = True
    s.ledger_open += 1
    key = f"{p.get('node', '')}\0{p.get('failure_signature', '')}"
    s.ledger_entries[key] = {**p, "state": "OPEN"}
    if p.get("task_id"):
        s.current_task_id = p.get("task_id")
        task = p.get("task")
        s.current_task_metadata = dict(task) if isinstance(task, dict) else None
        manifest = p.get("manifest")
        s.current_manifest = dict(manifest) if isinstance(manifest, dict) else None
        s.r_tree_identity = p.get("r_sha") or None


def _on_ledger_transitioned(s: State, p: dict, ev: EventEnvelope) -> None:
    """Project non-terminal ledger count across the closed transition set."""
    s.ledger_rebuilt = True
    before = p.get("from")
    after = p.get("to")
    key = f"{p.get('node', '')}\0{p.get('failure_signature', '')}"
    if key in s.ledger_entries:
        s.ledger_entries[key] = {**s.ledger_entries[key], **p, "state": after}
    if before == "PROVEN" and after == "OPEN":
        s.ledger_open += 1
    elif before in ("OPEN", "CLASSIFIED", "FIXED", "STALE") and after == "PROVEN":
        s.ledger_open = max(0, s.ledger_open - 1)
    if after == "CLASSIFIED" and p.get("task_id"):
        s.current_task_id = p.get("task_id")
        task = p.get("task")
        s.current_task_metadata = dict(task) if isinstance(task, dict) else None
        manifest = p.get("manifest")
        s.current_manifest = dict(manifest) if isinstance(manifest, dict) else None
        s.r_tree_identity = p.get("r_sha") or None
    if after == "FIXED":
        s.substate = "ISLAND_GATE_2"
        _reset_doc(s)


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
        elif p.get("defect_classification") == "plan_defect":
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
    """PRISM_PLAN revise: route by defect_classification (flow.md §10.1)."""
    dc = p.get("defect_classification")
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


def _route_prism_final_revise(s: State, p: dict) -> None:
    """PRISM_FINAL revise: route by defect_classification (flow.md §10.1)."""
    dc = p.get("defect_classification", "impl_defect")
    if dc == "red_defect":
        s.substate = "RED"
        s.green_committed = False
    elif dc == "plan_defect":
        # #89: PRISM_FINAL revise plan_defect — Archer's scope-split
        # defect, not Devon's. Route to PLANNING replan; do NOT consume
        # attempt. _route_scope_replan handles all state cleanup.
        _route_scope_replan(s)
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
    s.current_task_id = None
    s.current_task_metadata = None
    s.current_manifest = None
    s.green_committed = False
    s.refactor_done = False
    s.r_tree_identity = None


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
        _reset_doc(s)
        _consume_attempt(s)


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


# -- M-IMPL dispatch builders (flow.md §10 / D-29) --------------------------


def _set_m_impl_dispatch_flags(s: State, cmd: dict) -> None:
    """Set dispatch flags for an M-IMPL dispatch_agent command."""
    sub = cmd.get("params", {}).get("substate")
    if sub in _M_IMPL_REVIEW_SUBSTATES:
        s.reviewer_dispatched = True
    else:
        s.doc_dispatched = True


def _m_impl_base_assignment(s: State, role: str, sub: str, skills: list) -> dict:
    """Build the M-IMPL dispatch assignment with all required keys.

    test_dispatch_materialization.py requires every dispatch_agent assignment
    to carry: target_doc, doc_set, role, substate, attempt, review_round,
    docs, templates, skills, criteria_pack, test_tasks, manifest, phase,
    r_tree_identity, pre_dirty_snapshot, result_identity.

    Executor-materialized values are None (filled by the executor at issue
    time); the machine provides what it knows.
    """
    return {
        "kind": sub,
        "target_doc": None,
        "doc_set": list(_M_IMPL_CONTEXT_DOCS),
        "role": role,
        "substate": sub,
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "docs": list(_M_IMPL_CONTEXT_DOCS),
        "templates": [d.removesuffix(".md") for d in _M_IMPL_CONTEXT_DOCS],
        "skills": list(skills),
        "criteria_pack": None,
        "test_tasks": None,
        "manifest": None,
        "phase": None,
        "task_id": None,
        "task": None,
        "issue_number": None,
        "ac_refs": None,
        "fr_refs": None,
        "if_ids": None,
        "test_refs": None,
        # B50 (#65): split contract placeholders -- executor materializes
        # both from the task graph alongside test_refs.
        "unit_refs": None,
        "acceptance_refs": None,
        "commands": None,
        "r_tree_identity": None,
        "pre_dirty_snapshot": None,
        "result_identity": None,
    }


def _m_impl_archer_ruling_dispatch(s: State) -> Command:
    """RULING: dispatch Archer to rule on the failure the writers cannot
    resolve alone.

    Two entries share this channel: the S1 oscillation dossier (executor
    materializes the latest oscillation.detected payload into the dispatch
    context) and the M2 diagnosis exhaustion (repeated unknown attribution
    -- the forensic package + diagnosis history ride last_failure). Archer
    rules a machine-executable paired delta and the runtime lands both
    sides through the single-writer channels (M3 bundles the pairing;
    until then the delta lands as DIAGNOSE evidence)."""
    assignment = _m_impl_base_assignment(
        s, "archer", "RULING", ["tracks-discuz", "tracks-archer-planning"]
    )
    assignment["target_doc"] = None
    if (s.last_failure or {}).get("check") == "diagnosis_exhausted":
        objective = (
            "rule on the diagnosis-exhausted failure (repeated unknown "
            "attribution, M2): the forensic package and diagnosis history "
            "ride the evidence; produce a machine-executable paired delta "
            "{devon_side, shield_side, ordering}"
        )
    else:
        objective = "rule on the oscillating anchor pair (S1 contract conflict)"
    params = {
        "role": "archer",
        "substate": "RULING",
        "objective": objective,
        "stage": "M-IMPL",
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "docs": list(_M_IMPL_CONTEXT_DOCS),
        "assignment": assignment,
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


def _m_impl_archer_dispatch(s: State) -> Command:
    """PLANNING: dispatch Archer to decompose the task graph.

    Skills: the stage methodology skill (tracks-archer-planning) plus the
    discussion protocol. The large guard-stack catalog (tracks-quality-guards)
    is NOT injected by default — PLANNING does not design the host guard
    stack; inject it only when an assignment's task actually requires it.
    """
    assignment = _m_impl_base_assignment(
        s, "archer", "PLANNING", ["tracks-discuz", "tracks-archer-planning"]
    )
    assignment["target_doc"] = "tasks.json"
    params = {
        "role": "archer",
        "substate": "PLANNING",
        "objective": "decompose requirements into implementation task graph",
        "stage": "M-IMPL",
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "docs": list(_M_IMPL_CONTEXT_DOCS),
        "assignment": assignment,
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


def _m_impl_devon_dispatch(s: State, sub: str) -> Command:
    """RED/GREEN/REFACTOR: dispatch Devon for one isolated RGR phase.

    Three isolated dispatch modes selected from assignment.phase: Devon
    completes only the assigned phase and stops - never a whole RGR cycle
    in one assignment (phase-separated M-IMPL flow). The dedicated
    tracks-devon-rgr skill carries the phase-specific checklist and output
    schema; tracks-discuz is irrelevant to Devon (no discussion threads).

    Public assignment keys (task_id/if_ids/ac_refs/test_refs/commands) are
    explicit placeholders: the machine provides task_id (known state) and
    leaves the rest None for the executor to materialize from the task graph
    (NFR-0030: pure machine, no I/O, no invented task values).
    """
    phase = sub.lower()  # "red", "green", "refactor"
    assignment = _m_impl_base_assignment(s, "devon", sub, ["tracks-devon-rgr"])
    assignment["phase"] = phase
    # B30/#30 family (live T-003 GREEN: evidence JSON missing 7 fields):
    # front-load the evidence contract so Devon self-validates before
    # returning instead of burning a full dispatch per missing field.
    assignment["evidence_contract"] = dict(DEVON_EVIDENCE_CONTRACT)
    assignment["task_id"] = s.current_task_id
    assignment["if_ids"] = None  # executor materializes from task graph
    assignment["ac_refs"] = None  # executor materializes from task graph
    assignment["test_refs"] = None  # executor materializes from task graph
    assignment["unit_refs"] = None  # executor materializes (B50 split)
    assignment["acceptance_refs"] = None  # executor materializes (B50 split)
    assignment["commands"] = None  # executor materializes (test/guard cmds)
    if s.r_tree_identity and sub in ("GREEN", "REFACTOR"):
        assignment["r_tree_identity"] = s.r_tree_identity
    params = {
        "role": "devon",
        "substate": sub,
        # The JSON clause is load-bearing: the opencode backend extracts the
        # evidence object from Devon's LAST text message only. Without this
        # reminder Devon ends with prose/Markdown summaries and the outcome
        # is red_invalid three attempts in a row (run 01KZTHE7 T-001,
        # 2026-08-15) despite the skill schema and the failure evidence
        # flowing back.
        "objective": (
            f"implement task {s.current_task_id} ({phase}); your FINAL reply "
            "must end with the bare evidence JSON object per skill "
            "tracks-devon-rgr §4 - prose or Markdown reports are not a "
            "deliverable" + _shield_diagnosis_clause(s)
        ),
        "stage": "M-IMPL",
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "docs": list(_M_IMPL_CONTEXT_DOCS),
        "assignment": assignment,
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


# flow.md §10.1 DIAGNOSE 七元分类词表（#89 单一真相源：kernel 定义，
# effects/_DIAGNOSE_CLASSIFICATIONS 与 assignment 注入均引用此处）。
# plan_defect（#89）：诊断出的修复需要修改当前任务 manifest
# allowed_paths 之外的文件——任务图 scope 切分缺陷，Archer 重规划所有。
DIAGNOSE_CLASSIFICATIONS = (
    "test_defect",
    "impl_defect",
    "red_defect",
    "plan_defect",
    "stub_gap",
    "ac_gap",
    "spec_gap",
    # M2 (convergence plan 2026-09-05): the honest exit -- Prism could not
    # reproduce the failure on the forensic package, so it must NOT guess
    # an owner. Stays in DIAGNOSE (forensics-forced re-dispatch); a
    # repeated consecutive unknown escalates to Archer RULING as
    # diagnosis_exhausted (executor streak detection, M1-S2).
    "unknown",
)


def _m_impl_prism_dispatch(s: State, sub: str) -> Command:
    """PRISM_PLAN/PRISM_RED/PRISM_FINAL/DIAGNOSE: dispatch Prism with criteria."""
    objective = {
        "PRISM_PLAN": "review task graph plan",
        "PRISM_RED": "review Red checkpoint B..R",
        "PRISM_FINAL": "review complete task range and lineage",
        "DIAGNOSE": "diagnose failure attribution",
    }.get(sub, "review")
    assignment = _m_impl_base_assignment(s, "prism", sub, ["tracks-discuz", "tracks-prism-impl"])
    assignment["criteria_pack"] = dict(_M_IMPL_CRITERIA_PACK)
    if sub in ("DIAGNOSE", "PRISM_FINAL"):
        # #89（B90 现场）：D-39 会话复用把 agent 定义/skill 词表冻结在
        # 会话初建时——提示词合同修订到不了已存在的会话（run 01M0S0FQ
        # T-012：#89 词表已提交，Prism 仍只能报 impl_defect）。词表由
        # runtime 机器内联进 assignment，随每次派发新鲜送达，与会话缓存
        # 无关；单一真相源为 kernel.DIAGNOSE_CLASSIFICATIONS。
        assignment["classification_vocabulary"] = list(DIAGNOSE_CLASSIFICATIONS)
    if s.r_tree_identity and sub == "PRISM_RED":
        assignment["r_tree_identity"] = s.r_tree_identity
    params = {
        "role": "prism",
        "substate": sub,
        "objective": objective,
        "stage": "M-IMPL",
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "docs": list(_M_IMPL_CONTEXT_DOCS),
        "assignment": assignment,
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


def _shield_diagnosis_clause(s: State) -> str:
    """Prism DIAGNOSE details for the fixer objective ('' when none).

    Read from State.diagnose_report, not last_failure: last_failure is
    dropped by `trac retry --clear-evidence` and overwritten by every failed
    attempt (FR-0210), which left fixers re-deriving Prism's whole analysis
    from scratch (run 01KZTHE7 T-008/T-017: 52 minutes burned
    re-archaeologying defects Prism had already pinpointed). Shared by the
    SHIELD_FIX (test_defect) and Devon GREEN (impl_defect) re-dispatches.
    """
    report = s.diagnose_report or {}
    reason = report.get("reason")
    evidence = report.get("evidence")
    if not reason and not evidence:
        return ""
    parts = []
    if reason:
        parts.append(f"reason: {reason}")
    if evidence:
        parts.append(f"evidence: {evidence}")
    label = report.get("check") or "test_defect"
    return f" Prism DIAGNOSE verdict ({label}) - " + "; ".join(parts)


def _m_impl_shield_dispatch(s: State) -> Command:
    """SHIELD_FIX: dispatch Shield to fix diagnosed test defects.

    The machine substate stays SHIELD_FIX (flow.md §10), but the dispatched
    agent substate is WRITE per test_dispatch_materialization.py lines 35-43.
    """
    assignment = _m_impl_base_assignment(
        s, "shield", "WRITE", ["tracks-discuz", "tracks-shield"]
    )
    # B28/#30 slim (PRISM-B28-R1-04): same writer manifest contract as the
    # M-TEST Shield WRITE dispatch — SHIELD_FIX returns a manifest too.
    assignment["manifest_contract"] = dict(WRITE_MANIFEST_CONTRACT)
    # SHIELD_FIX writes tests for the diagnosed defect: derive the Shield
    # WRITE test_tasks contract from the current task metadata. The base
    # assignment leaves test_tasks=None, which the executor rejects as
    # stale - the whole SHIELD_FIX path had never been driven by a real
    # dispatch before run 01KZTHE7 T-008 (2026-08-15).
    meta = s.current_task_metadata or {}
    assignment["test_tasks"] = [
        {
            "ac_id": ac,
            "layers": ["integration"],
            "if_ids": list(meta.get("if_ids") or []),
        }
        for ac in (meta.get("ac_refs") or [])
        if isinstance(ac, str) and ac
    ]
    params = {
        "role": "shield",
        "substate": "WRITE",
        # The JSON clause is load-bearing: the opencode backend extracts the
        # artifact manifest from Shield's LAST text message only. Without
        # this reminder Shield ends with prose/Markdown and the outcome is
        # manifest_malformed (run 01KZTHE7 T-017, 2026-08-16).
        "objective": (
            "SHIELD_FIX: fix diagnosed test defects; your FINAL reply "
            "must end with the bare artifact manifest JSON object per "
            "Shield §输出合同 - prose or Markdown reports are not a "
            "deliverable" + _shield_diagnosis_clause(s)
        ),
        "stage": "M-IMPL",
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "docs": list(_M_IMPL_CONTEXT_DOCS),
        "assignment": assignment,
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


# -- M-IMPL decide() control flow (flow.md §10.1) ---------------------------


def _decide_m_impl(s: State, sub: str) -> Command | None:
    """M-IMPL explicit control flow (architecture.md §1.2, flow.md §10.1).

    Pure (NFR-0030): no I/O, no clock/env. Each substate maps to at most one
    Command; substates not listed produce None (halt / awaiting result).
    """
    if sub == "BASELINE":
        return Command(kind="freeze_baseline", params={"stage": "M-IMPL"})
    if sub == "NEEDS_ATTENTION":
        return None  # awaiting reconcile or return upstream
    if sub == "RULING":
        return _decide_m_impl_ruling(s)
    if sub == "PLANNING":
        return _decide_m_impl_planning(s)
    if sub in ("ISLAND_GATE_1", "ISLAND_GATE_2"):
        return _decide_m_impl_island(s, sub)
    if sub in _M_IMPL_REVIEW_SUBSTATES:
        return _decide_m_impl_prism(s, sub)
    if sub in ("RED", "GREEN", "REFACTOR", "SHIELD_FIX"):
        return _decide_m_impl_agent(s, sub)
    if sub in ("RED_GATE", "GREEN_GATE", "TASK_REVIEW"):
        return Command(kind="run_task_gates", params={"stage": "M-IMPL", "gate": sub})
    if sub == "RED_CHECKPOINT":
        return Command(
            kind="checkpoint_red", params={"stage": "M-IMPL", "task_id": s.current_task_id}
        )
    if sub == "GREEN_COMMIT":
        return Command(
            kind="commit_green", params={"stage": "M-IMPL", "task_id": s.current_task_id}
        )
    if sub == "REFACTOR_GATE":
        return Command(kind="run_refactor_gate", params={"stage": "M-IMPL"})
    if sub == "TASK_DISPATCH":
        return Command(kind="select_task", params={"stage": "M-IMPL"})
    if sub == "TASK_DONE":
        return Command(
            kind="complete_task", params={"stage": "M-IMPL", "task_id": s.current_task_id}
        )
    if sub == "EXIT":
        return _decide_m_impl_exit(s)
    if sub == "RETURNED":
        return _m_impl_returned_route(s)
    return None


def _m_impl_returned_route(s: State) -> Command:
    """RETURNED: SM-01.13 Human-approved rollback or SM-05.7 escalation
    return. IF-RELEASE-003 (face D) mirrors m_test: a human return
    (s.returned) carries reason=human_return; a Human-approved DIAGNOSE
    rollback keeps diagnose_rollback."""
    return Command(
        kind="rollback_stage",
        params={
            "to_stage": s.return_target,
            "reason": "human_return" if s.returned else "diagnose_rollback",
        },
    )


def _decide_m_impl_ruling(s: State) -> Command | None:
    """RULING: dispatch Archer with the oscillation dossier (M1-S1).

    Archer is the contract authority with dual-side read visibility: the
    assignment carries the oscillation dossier (healed/newly_red/common
    anchors + the S1 signature evidence) and asks for a machine-executable
    paired delta {devon_side, shield_side, ordering}. No Devon/Shield
    dispatch ever repeats against the unsatisfiable anchor pair."""
    if not s.doc_dispatched:
        return _m_impl_archer_ruling_dispatch(s)
    return None  # awaiting Archer outcome


def _decide_m_impl_planning(s: State) -> Command | None:
    """PLANNING: dispatch Archer -> validate+commit taskgraph -> ISLAND_GATE_1."""
    if not s.doc_dispatched:
        return _m_impl_archer_dispatch(s)
    if not s.doc_produced:
        return None  # awaiting Archer outcome
    if not s.taskgraph_committed:
        return Command(kind="commit_taskgraph", params={"stage": "M-IMPL"})
    return None  # taskgraph.committed event transitions to ISLAND_GATE_1


def _decide_m_impl_island(s: State, sub: str) -> Command | None:
    """ISLAND_GATE_1: six-tuple check; ISLAND_GATE_2: reach + full suites."""
    if sub == "ISLAND_GATE_1":
        return Command(kind="check_island_1", params={"stage": "M-IMPL"})
    return Command(kind="check_island_2", params={"stage": "M-IMPL"})


def _decide_m_impl_prism(s: State, sub: str) -> Command | None:
    """Prism dispatch substates: PRISM_PLAN/RED/FINAL, DIAGNOSE."""
    if sub == "DIAGNOSE" and s.diagnose_classification == "stub_gap":
        return Command(kind="rollback_stage", params={"to_stage": "M-DESIGN", "reason": "stub_gap"})
    if s.reviewer_dispatched:
        return None  # awaiting Prism verdict
    return _m_impl_prism_dispatch(s, sub)


def _is_preset_anchor_task(s: State) -> bool:
    """Standard-RGR tasks whose RED anchor is the frozen failing tests
    themselves (Archer's "RED 锚点为既有失败测试" pattern): there is no new
    unit test to write, so the RED phase is a Runtime-executed anchor
    confirmation (run the anchors, expect red), never a Devon dispatch."""
    meta = s.current_task_metadata or {}
    return "RED 锚点" in (meta.get("description") or "")


def _is_integration_task(s: State) -> bool:
    """B94 integration tasks carry no new RED unit test to write either:
    their RED is a runtime walk_red anchor confirmation (seal the unit
    R-tree on site from unit_refs, expect green), never a Devon dispatch.
    The integration flag itself triggers, independent of any description
    marker (unlike _is_preset_anchor_task)."""
    meta = s.current_task_metadata or {}
    return bool(meta.get("integration"))


def _is_verification_task(s: State) -> bool:
    """§1.0.3 two-tier model: a task whose description carries the
    verification-only marker has no RED-implementation - acceptance is
    Runtime-executed (user ruling 2026-08-15), not a Devon RGR cycle.

    Marker must be a LEADING declaration (Archer's emitted forms
    ``verification-only 验收闭口(...)`` or ``【verification-only ...】``);
    a bare substring match misclassifies any task whose description merely
    mentions the phrase in prose (run 01M0S0FQ v0.7 boundary: T-016's
    description says "并 verification-only 重验 demo_host" while describing
    T-014's handling, turning the real implementation task into
    verification-only -> RED/GREEN loop on verify_task with Devon never
    dispatched)."""
    meta = s.current_task_metadata or {}
    desc = (meta.get("description") or "").lstrip()
    return desc.startswith("verification-only") or desc.startswith("【verification-only")


def _decide_m_impl_agent(s: State, sub: str) -> Command | None:
    """RED/GREEN/REFACTOR/SHIELD_FIX: dispatch the agent if not already."""
    if s.doc_dispatched:
        return None  # awaiting outcome
    if sub == "SHIELD_FIX":
        return _m_impl_shield_dispatch(s)
    if sub in ("RED", "GREEN") and _is_verification_task(s):
        # RGR structurally cannot apply to a verification-only task (frozen
        # tests, nothing to implement). RED routes here initially; GREEN is
        # reached when a DIAGNOSE mis-routes a stale-outcome artifact (e.g.
        # the SHIELD_FIX gate tripping over a pre-fix RED outcome, run
        # 01KZTHE7 T-008 2026-08-15) - either way the resolution is to run
        # the Runtime acceptance, never a Devon RGR phase.
        return Command(
            kind="verify_task",
            params={"stage": "M-IMPL", "task_id": s.current_task_id},
        )
    if sub == "RED" and (_is_preset_anchor_task(s) or _is_integration_task(s)):
        # Standard RGR with a preset anchor (frozen failing tests): the RED
        # phase is a Runtime anchor confirmation - Devon has no new unit
        # test to write, so dispatching it only produces an empty
        # changed_paths evidence the gate must reject (run 01KZTHE7 T-013).
        # Integration tasks ride the same anchor_red command channel: their
        # RED is the walk_red confirmation (seal the unit R-tree on site),
        # triggered by the integration flag itself, no description marker.
        return Command(
            kind="anchor_red",
            params={"stage": "M-IMPL", "task_id": s.current_task_id},
        )
    return _m_impl_devon_dispatch(s, sub)


def _decide_m_impl_exit(s: State) -> Command | None:
    """EXIT: seal stage (no doc) -> stage.exited -> boundary."""
    if not s.island_2_passed or s.full_chain_round is None or s.ledger_open:
        return None
    if not s.stage_exited:
        return Command(kind="write_frontmatter", params={"stage": "M-IMPL"})
    return None
