"""M-TEST state-machine logic (flow.md §9 / SM-01, FR-0010~0070).

Extracted from ``machine.py`` for module-size compliance (C0302). Contains the
M-TEST reducers, decide() control flow, criteria-pack constants, and the shared
attempt-accounting helpers used across all stages.

No circular import: this module imports only from ``events`` at runtime;
``State`` is imported under ``TYPE_CHECKING`` only (duck-typed at runtime).
``machine.py`` imports the helpers, reducers, and decide functions from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import WRITE_MANIFEST_CONTRACT
from .events import Command, EventEnvelope

if TYPE_CHECKING:
    from .machine import State


# -- shared helpers (used by reducers across all stages) --------------------


def _reset_doc(s: State) -> None:
    s.doc_dispatched = s.doc_produced = s.doc_validated = False


def _reset_review(s: State) -> None:
    s.reviewer_dispatched = s.reviewer_produced = False


def _consume_attempt(s: State) -> None:
    """Shared attempt accounting: increment; escalate to awaiting_human at >=3."""
    s.current_attempt += 1
    if s.current_attempt >= 3:
        s.status = "awaiting_human"
        s.awaiting = "escalation"


# -- M-TEST constants -------------------------------------------------------

# D-29 criteria pack identity (architecture.md §3.4): Runtime decides the pack
# name+version; Prism loads it and echoes the identity in its verdict.
_CRITERIA_PACK = {"name": "tracks-prism-test", "version": "0.1"}
# Shield / M-TEST Prism read-only context docs (interfaces.md §3a).
_M_TEST_CONTEXT_DOCS = ("test-plan.md", "interfaces.md", "acceptance.md")


# -- M-TEST reducers (flow.md §9 / SM-01, FR-0010~0070) -----------------------


def _on_m_test_outcome_done(s: State) -> None:
    """SM-01.3: Shield outcome done -> COLLECT; Prism outcome -> produced."""
    if s.substate == "WRITE":
        s.substate = "COLLECT"
    elif s.substate == "PRISM_REVIEW":
        s.reviewer_produced = True


def _on_test_baseline_captured(s: State, p: dict, ev: EventEnvelope) -> None:
    """D-41/IF-SELECT-001 (v5): the pre-WRITE R1 snapshot is durable. Only a
    passed capture unblocks the first Shield WRITE dispatch; a failed capture
    is routed fail-closed by its verdict.failed(baseline_defect) companion."""
    if p.get("status") == "passed":
        s.baseline_captured = True


def _on_test_selected(s: State, p: dict, ev: EventEnvelope) -> None:
    """D-41/IF-SELECT-002: project the latest selection identity so gate
    handlers and replay share one stamped selection per run."""
    s.active_selection_id = p.get("selection_id")


def _on_test_collected(s: State, p: dict, ev: EventEnvelope) -> None:
    # D-41 v3 timing (flow.md §9.1): passed -> RED_CHECK (Runtime selects and
    # executes R2 before Prism sees the run); failed -> WRITE (re-dispatch
    # Shield).
    if p["status"] == "passed":
        s.test_collected = True
        s.substate = "RED_CHECK"
    else:
        s.test_collected = False
        s.substate = "WRITE"
        _reset_doc(s)
        # FRB-K2: a REMOVED collect failure carrying the executor's forward
        # companion marker (companion == "test_defect") defers this round's
        # single attempt charge to its companion verdict.failed(test_defect);
        # any other failed collect consumes here.
        if p.get("companion") != "test_defect":
            _consume_attempt(s)  # shared <=3 budget


def _on_red_validated(s: State, p: dict, ev: EventEnvelope) -> None:
    # D-41 v3 timing: valid -> PRISM_REVIEW (Prism consumes the current-tree
    # red evidence); invalid -> DIAGNOSE. The FR-0244 empty-Shield hotfix
    # increment bypass (emitted at the WRITE dispatch, basis=unit-only) keeps
    # its direct release route to EXIT -- no Prism round on an empty increment.
    s.red_findings = p.get("findings")
    if p["status"] == "valid":
        s.red_validated = True
        if p.get("basis") == "unit-only hotfix increment":
            s.substate = "EXIT"
        else:
            s.substate = "PRISM_REVIEW"
            _reset_review(s)
    else:
        s.red_validated = False
        s.substate = "DIAGNOSE"


def _on_test_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    # SM-01.14: test asset frozen. The executor follows up with stage.exited +
    # run.completed (M-IMPL not registered -> boundary).
    s.active_result = None  # v0.5: pipeline publish complete
    s.test_committed = True
    # M-IMPL RGR: a runtime-committed Shield test fix re-freezes the R tree --
    # the GREEN regression gate must diff against the fix commit, not the
    # original RED checkpoint, or every sanctioned fix reads as drift (run
    # 01KZTHE7 T-017: Shield fix 7d8234b flagged "R unit tests changed").
    commit_sha = p.get("commit_sha")
    if commit_sha:
        s.r_tree_identity = commit_sha


def _on_test_written(s: State, p: dict, ev: EventEnvelope) -> None:
    # v0.5 batch 2: Shield WRITE checkpoint published. Transition to COLLECT
    # (same as _on_m_test_outcome_done for WRITE -> COLLECT).
    s.active_result = None  # v0.5: pipeline publish complete
    if s.substate == "WRITE":
        s.substate = "COLLECT"


def _park_for_operator(s: State) -> None:
    """B59 (#75): runtime/contract defects that re-dispatching an agent can
    never fix park awaiting_human/escalation for the operator."""
    s.status = "awaiting_human"
    s.awaiting = "escalation"


def _route_upstream_tree_defect(s: State) -> None:
    """baseline_defect / contract_error(M-DESIGN): an unusable tree is an
    upstream design defect -- rollback M-DESIGN, never a Shield re-dispatch,
    no attempt charge (D-41 v5 / FRB-K1)."""
    s.diagnose_classification = "stub_gap"
    s.substate = "DIAGNOSE"


def _on_m_test_verdict_failed(s: State, p: dict) -> None:
    """M-TEST verdict.failed routing (FR-0040/0060/0070).

    - ``criteria_pack_mismatch`` (PRISM_REVIEW): re-dispatch Prism, consume
      the shared <=3 budget (anti-self-report triple, D-29).
    - ``trace`` (EXIT, SM-01.15): re-dispatch Shield (WRITE), consume budget.
    - ``baseline_defect``: rollback M-DESIGN, no Human, no attempt charge.
    - ``contract_error`` with ``target_stage=M-DESIGN`` (FRB-K1): the
      executor's fail-closed companion to red.validated(invalid) over an
      unusable tree -- same upstream/design route as baseline_defect:
      rollback M-DESIGN, never a Shield re-dispatch, no attempt charge.
    - ``test_defect`` (DIAGNOSE, SM-01.11): re-dispatch Shield (WRITE),
      consume exactly once as an ordinary rewrite round. For the REMOVED
      collect/verdict pair (FRB-K2) the collect half defers via its forward
      ``companion="test_defect"`` marker and THIS verdict is the pair's one
      charge; an unmarked test_defect verdict is its own round's only charge.
    - ``stub_gap`` (DIAGNOSE, SM-01.12): rollback M-DESIGN, no Human.
    - ``ac_gap``/``spec_gap`` (DIAGNOSE, SM-01.13): await Human approval, then
      rollback M-ACC / M-SPEC.
    """
    check = p.get("check")
    if check == "criteria_pack_mismatch":
        _reset_review(s)
        _consume_attempt(s)
        return
    if check == "baseline_defect":
        # D-41 (v5): the inherited tree was already un-importable BEFORE the
        # first Shield WRITE -- an upstream/design/contract defect. Route
        # fail-closed to M-DESIGN rollback; never re-dispatch Shield and never
        # silently continue with a vacated R1 snapshot.
        _route_upstream_tree_defect(s)
        return
    if check == "contract_error" and p.get("target_stage") == "M-DESIGN":
        # FRB-K1: contract_error(rollback -> M-DESIGN) mirrors baseline_defect:
        # an unusable tree is an upstream defect discovered before any agent
        # wrote -- rollback without burning the shared attempt budget.
        _route_upstream_tree_defect(s)
        return
    if check in ("collect_defect", "test_freeze_contamination"):
        # collect_defect (operator finding 2026-08-24, run 01M0S0FQ
        # incident): the collect/classification pipeline was blind to the
        # agent's committed test artifacts -- a runtime/contract defect, never
        # Shield's: no attempt charge and NO auto re-dispatch (re-running
        # Shield cannot fix the collector); `trac retry` re-enters RED_CHECK
        # once repaired, preserving the checkpointed test work.
        # test_freeze_contamination (B59 #75): ANY dirty file (tracked or
        # untracked) under tests/ at commit_tests is residue from a discarded
        # cycle (three run-01M0S0FQ freeze commits silently absorbed prior
        # Devon RED residue into the baseline) -- never Shield's defect,
        # never auto-fixable by re-dispatch: park for the operator to clean
        # the tree, then `trac retry` re-enters the freeze.
        _park_for_operator(s)
        return
    if check in ("trace", "commit"):
        # trace: SM-01.15 — re-dispatch Shield (WRITE), consume budget.
        # commit: D-30/F-1 — hook rejected test commit -> re-dispatch Shield
        # (mirrors check=="trace": reset trace_passed, WRITE, consume budget).
        s.trace_passed = False
        s.substate = "WRITE"
        _reset_doc(s)
        _consume_attempt(s)
        return
    classification = check  # test_defect | stub_gap | ac_gap | spec_gap
    s.diagnose_classification = classification
    # Same State-carried snapshot as M-IMPL: the WRITE re-dispatch reads the
    # verdict details from diagnose_report so provider-failure retries and
    # `trac retry --clear-evidence` never strip the diagnosis (FR-11).
    s.diagnose_report = {
        k: p.get(k)
        for k in ("check", "reason", "evidence", "log_ref", "attempt")
    }
    if classification == "test_defect":
        s.substate = "WRITE"
        _reset_doc(s)
        # FRB-K2: always consume exactly once, as an ordinary rewrite round.
        # A paired collect half (companion marker) deferred its consume to
        # THIS verdict; an unmarked test_defect verdict is its round's only
        # charge. No latent pairing state is consulted or left behind.
        _consume_attempt(s)
    elif classification == "stub_gap":
        # rollback M-DESIGN -- decide() produces rollback_stage(M-DESIGN)
        s.diagnose_classification = "stub_gap"
    elif classification in ("ac_gap", "spec_gap"):
        # Human must approve the rollback (SM-01.13)
        s.status = "awaiting_human"
        s.awaiting = "rollback"
        s.return_target = "M-ACC" if classification == "ac_gap" else "M-SPEC"
    else:
        # Pipeline validation failures (template, discussion_diff, no_diff,
        # forbidden_diff, digest_drift, publish_error, etc.): reset the
        # appropriate dispatch flag and consume an attempt to prevent
        # infinite re-dispatch loops. no_diff_justified (v0.5 no_diff peer
        # review: reviewer rejected the no-diff explanation) is also routed
        # here — it consumes an attempt like any other author failure.
        if s.substate == "PRISM_REVIEW":
            _reset_review(s)
        else:
            s.substate = "WRITE"
            _reset_doc(s)
        _consume_attempt(s)


# -- M-TEST decide() control flow (flow.md §9 / SM-01) -----------------------


def _m_test_shield_dispatch(s: State) -> Command:
    """DISPATCH/WRITE: dispatch Shield to write integration/e2e tests."""
    objective = "write integration/e2e tests against interface stubs"
    report = s.diagnose_report or {}
    reason = report.get("reason")
    evidence = report.get("evidence")
    if reason or evidence:
        # test_defect re-dispatch: carry the DIAGNOSE verdict details so
        # Shield fixes the pinpointed defects instead of re-deriving them.
        parts = [f"reason: {reason}"] if reason else []
        if evidence:
            parts.append(f"evidence: {evidence}")
        objective += (
            f". Fix the diagnosed test defects (Prism verdict "
            f"{report.get('check') or 'test_defect'}): " + "; ".join(parts)
        )
    params = {
        "role": "shield",
        "substate": "WRITE",
        "objective": objective,
        "stage": "M-TEST",
        "attempt": s.current_attempt + 1,
        "docs": list(_M_TEST_CONTEXT_DOCS),
        "assignment": {
            "kind": "WRITE",
            "skills": ["tracks-discuz", "tracks-shield"],
            "docs": list(_M_TEST_CONTEXT_DOCS),
            # B28/#30 slim: front-load the manifest contract (schema +
            # example + pre-return self-checks) so the writer validates its
            # own output instead of burning dispatches on shape errors.
            "manifest_contract": dict(WRITE_MANIFEST_CONTRACT),
        },
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)  # FR-11
    return Command(kind="dispatch_agent", params=params)


def _m_test_prism_dispatch(s: State) -> Command:
    """PRISM_REVIEW: dispatch Prism with the criteria pack (D-29 triple ①)."""
    params = {
        "role": "prism",
        "substate": "PRISM_REVIEW",
        "objective": "review test contract against the criteria pack",
        "stage": "M-TEST",
        "attempt": s.current_attempt + 1,
        "docs": list(_M_TEST_CONTEXT_DOCS),
        "assignment": {
            "kind": "PRISM_REVIEW",
            "skills": ["tracks-discuz", "tracks-prism-test"],
            "docs": list(_M_TEST_CONTEXT_DOCS),
            "criteria_pack": dict(_CRITERIA_PACK),
        },
    }
    if s.last_failure:
        params["evidence"] = dict(s.last_failure)
    return Command(kind="dispatch_agent", params=params)


def _m_test_dispatch_route(s: State) -> Command:
    """DISPATCH route (SM-01.2): first Shield dispatch.

    D-41 (v5): the pre-WRITE R1 snapshot is captured BEFORE the first
    Shield WRITE dispatch (stage.entered < test.baseline_captured < first
    Shield WRITE, event-order guarantee).
    """
    if not s.baseline_captured:
        return Command(kind="capture_baseline", params={"stage": "M-TEST"})
    return _m_test_shield_dispatch(s)


def _m_test_write_route(s: State) -> Command | None:
    """WRITE re-dispatch routes (SM-01.4/.6/.8/.11/.15).

    Re-dispatch cycles must never run ahead of the stamped capture; an
    in-flight Shield outcome halts decide().
    """
    if not s.baseline_captured:
        return Command(kind="capture_baseline", params={"stage": "M-TEST"})
    if s.doc_dispatched:
        return None  # awaiting Shield outcome
    return _m_test_shield_dispatch(s)


def _m_test_review_route(s: State) -> Command | None:
    """PRISM_REVIEW route (SM-01.7): await an in-flight verdict or dispatch."""
    if s.reviewer_dispatched:
        return None  # awaiting Prism verdict
    return _m_test_prism_dispatch(s)


def _m_test_returned_route(s: State) -> Command:
    """RETURNED route: SM-01.13 Human-approved rollback or SM-05.7 escalation
    return (human_return)."""
    reason = "human_return" if s.returned else "diagnose_rollback"
    return Command(
        kind="rollback_stage", params={"to_stage": s.return_target, "reason": reason}
    )


def _decide_m_test(s: State, sub: str) -> Command | None:
    """M-TEST explicit control flow (architecture.md §1.2, SM-01).

    Pure (NFR-0030): no I/O, no clock/env. Each substate maps to at most one
    Command; substates not listed produce None (halt / awaiting result).
    """
    if sub == "DISPATCH":
        return _m_test_dispatch_route(s)
    if sub == "WRITE":
        return _m_test_write_route(s)
    if sub == "COLLECT":
        return Command(kind="collect_tests", params={"stage": "M-TEST"})  # SM-01.5
    if sub == "PRISM_REVIEW":
        return _m_test_review_route(s)
    if sub == "RED_CHECK":
        return Command(kind="run_tests", params={"stage": "M-TEST"})  # SM-01.9
    if sub == "EXIT":
        return _m_test_exit_route(s)  # SM-01.14/.15
    if sub == "DIAGNOSE":
        return _m_test_diagnose_route(s)  # SM-01.11/.12/.13
    if sub == "RETURNED":
        # SM-01.13: Human approved ac_gap/spec_gap rollback (diagnose_rollback)
        # or SM-05.7: Human return from escalation (human_return).
        return _m_test_returned_route(s)
    return None


def _m_test_exit_route(s: State) -> Command | None:
    """EXIT: trace gate -> commit tests (pipeline) -> seal. trace_passed and
    test_committed are set by reducers; decide() only fires the next step.

    v0.6 hotfix empty-Shield increment (FR-0244-04, IF-HOTFIX-007): when the
    delta test-plan §8 has no integration/e2e rows and >=1 unit row, the
    M-TEST EXIT path skips the Shield WRITE dispatch entirely (no
    test.written/test.committed). The executor (T-007 wiring) emits
    ``increment.declared`` and the kernel's EXIT route produces the hotfix
    plan-level trace closure check instead of the normal Shield dispatch.
    After trace_passed, the route goes straight to write_frontmatter (no
    commit_tests — no Shield tests were written).
    """
    if not s.trace_passed:
        # SM-01.14: trace gate — hotfix empty-shield runs carry the hotfix
        # scope flag so the executor (T-007) passes hotfix_ctx to the trace
        # closure check (check_hotfix_plan_closure, plan-level).
        if _is_hotfix_empty_shield(s):
            return Command(
                kind="check_trace",
                params={"stage": "M-TEST", "hotfix": True},
            )
        return Command(kind="check_trace", params={"stage": "M-TEST"})  # SM-01.14
    if not s.test_committed:
        # SM-01.14: freeze test assets — hotfix empty-shield has no Shield
        # tests to commit (no test.written). Skip to write_frontmatter/EXIT.
        if _is_hotfix_empty_shield(s):
            if not s.stage_exited:
                return Command(kind="write_frontmatter", params={"stage": "M-TEST"})
            return None
        return Command(kind="commit_tests", params={"stage": "M-TEST"})  # SM-01.14
    if not s.stage_exited:
        return Command(kind="write_frontmatter", params={"stage": "M-TEST"})
    return None


def _is_hotfix_empty_shield(s: State) -> bool:
    """True when the M-TEST run is a hotfix with empty Shield increment
    (FR-0244-04): the delta test-plan §8 has no integration/e2e rows and
    >=1 unit row, so no Shield WRITE dispatch occurred (no test.written/
    test.committed). The executor (T-007) emits ``increment.declared`` and
    the kernel's EXIT route skips the commit_tests gate.

    Detection: the state carries ``hotfix_anchor_acs`` (set by
    anchor.validated during HOTFIX-TRIAGE) and no Shield tests were collected
    (``test_collected`` stays False). A hotfix run WITH integration/e2e
    Shield tests reaches EXIT via the normal WRITE->COLLECT->PRISM_REVIEW->
    RED_CHECK->EXIT flow (``test_collected`` True).
    """
    return s.hotfix_anchor_acs is not None and not s.test_collected


def _m_test_diagnose_route(s: State) -> Command | None:
    """DIAGNOSE four-way routing (FR-0060). test_defect and ac_gap/spec_gap
    are routed by the reducer (WRITE / awaiting_human); stub_gap produces the
    rollback command here (no Human gate, SM-01.12)."""
    if s.diagnose_classification == "stub_gap":
        return Command(kind="rollback_stage", params={"to_stage": "M-DESIGN", "reason": "stub_gap"})
    return None  # test_defect -> WRITE (reducer); ac_gap/spec_gap -> awaiting
