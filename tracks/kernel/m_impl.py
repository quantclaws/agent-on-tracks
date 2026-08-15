"""M-IMPL state-machine logic (flow.md §10).

Extracted from ``machine.py`` for module-size compliance (C0302). Contains
the M-IMPL reducers, decide() control flow, criteria-pack constants, and
dispatch builders.

No circular import: this module imports only from ``events`` at runtime;
``State`` is imported under ``TYPE_CHECKING`` only (duck-typed at runtime).
``machine.py`` imports the helpers, reducers, and decide functions from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    """PLANNING: task graph validated and committed -> ISLAND_GATE_1."""
    s.taskgraph_committed = True
    s.tasks_total = p.get("task_count", 0)
    s.taskgraph_digest = p.get("digest")
    s.taskgraph_path = p.get("path") or p.get("tasks_json")
    refs = p.get("tasks", [])
    s.task_refs = [dict(ref) for ref in refs if isinstance(ref, dict)]
    s.substate = "ISLAND_GATE_1"


def _on_task_started(s: State, p: dict, ev: EventEnvelope) -> None:
    """TASK_DISPATCH: Runtime selected a ready task -> RED (fresh RGR cycle)."""
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


def _on_writelock_granted(s: State, p: dict, ev: EventEnvelope) -> None:
    s.writelock_held = True


def _on_writelock_released(s: State, p: dict, ev: EventEnvelope) -> None:
    s.writelock_held = False


def _on_red_checkpointed(s: State, p: dict, ev: EventEnvelope) -> None:
    """RED_CHECKPOINT: private R commit created -> PRISM_RED."""
    s.r_tree_identity = p.get("r_sha")
    s.substate = "PRISM_RED"
    _reset_review(s)


def _on_green_committed(s: State, p: dict, ev: EventEnvelope) -> None:
    """GREEN_COMMIT: formal G commit (parent=B) -> REFACTOR."""
    s.green_committed = True
    s.substate = "REFACTOR"
    _reset_doc(s)


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
    """TASK_DONE: task.completed -> TASK_DISPATCH (more tasks) or ISLAND_GATE_2."""
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


# -- M-IMPL outcome / verdict routing ---------------------------------------


def _on_m_impl_outcome_done(s: State) -> None:
    """Route a successful (status=done) M-IMPL outcome by substate."""
    if s.substate == "PLANNING":
        s.doc_produced = True
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
        else:
            s.substate = "RED"
            _reset_doc(s)
            _consume_attempt(s)
    elif s.substate == "PRISM_FINAL":
        if verdict == "pass":
            s.substate = "TASK_DONE"
        else:
            dc = p.get("defect_classification", "impl_defect")
            if dc == "red_defect":
                s.substate = "RED"
                s.green_committed = False
            else:
                s.substate = "GREEN"
            s.refactor_done = False
            _reset_doc(s)
            _consume_attempt(s)


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


def _on_m_impl_verdict_failed(s: State, p: dict) -> None:
    """verdict.failed routing for M-IMPL (flow.md §10.1)."""
    check = p.get("check")
    if s.substate == "DIAGNOSE":
        _route_m_impl_diagnose(s, check)
        return
    _route_m_impl_gate_failure(s, check)


def _route_m_impl_diagnose(s: State, check: str) -> None:
    """DIAGNOSE four-way routing (flow.md §10.1, FR-0150)."""
    s.diagnose_classification = check
    if check == "impl_defect":
        s.substate = "GREEN"
        _reset_doc(s)
        _consume_attempt(s)
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
    else:
        _reset_doc(s)
        _consume_attempt(s)


def _route_m_impl_gate_failure(s: State, check: str) -> None:
    """Gate failure routing (non-DIAGNOSE substates)."""
    if check == "criteria_pack_mismatch":
        _reset_review(s)
        _consume_attempt(s)
    elif check in ("unknown_attribution", "full_suite"):
        s.substate = "DIAGNOSE"
        _reset_review(s)
    elif check == "island":
        s.substate = "PLANNING"
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "red_invalid":
        s.substate = "RED"
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "regression":
        s.substate = "REFACTOR" if s.substate == "REFACTOR_GATE" else "GREEN"
        _reset_doc(s)
        _consume_attempt(s)
    elif check in ("budget", "scope"):
        s.substate = "GREEN"
        _reset_doc(s)
        _consume_attempt(s)
    elif check == "public_interface":
        s.substate = "DIAGNOSE"
        s.diagnose_classification = "stub_gap"
        _reset_review(s)
    elif check == "verification_failed":
        # Runtime acceptance of a verification-only task failed (user ruling
        # 2026-08-15): the pre-implemented contract did not hold. No blind
        # retry - route into the existing four-way DIAGNOSE so Prism
        # attributes the failure (impl_defect -> Devon GREEN fix,
        # test_defect -> Shield SHIELD_FIX, spec/ac_gap -> upstream stage,
        # stub_gap -> M-DESIGN rollback).
        s.substate = "DIAGNOSE"
        s.diagnose_classification = None
        _reset_review(s)
    elif check in ("test_defect", "impl_defect", "stub_gap", "ac_gap", "spec_gap"):
        # Gate-level attribution routing directly into DIAGNOSE with a preset
        # classification: reset the reviewer flag so decide() can dispatch the
        # Prism DIAGNOSE review. Without this, a stale reviewer_dispatched
        # (e.g. left over from the previous DIAGNOSE round whose verdict
        # routed back to GREEN via _route_m_impl_diagnose, which deliberately
        # does not reset - task_review pass resets it later) makes
        # _decide_m_impl_prism return None forever and the run loop exits.
        s.substate = "DIAGNOSE"
        s.diagnose_classification = check
        _reset_review(s)
    else:
        _reset_doc(s)
        _consume_attempt(s)


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
        "commands": None,
        "r_tree_identity": None,
        "pre_dirty_snapshot": None,
        "result_identity": None,
    }


def _m_impl_archer_dispatch(s: State) -> Command:
    """PLANNING: dispatch Archer to decompose the task graph."""
    assignment = _m_impl_base_assignment(
        s, "archer", "PLANNING", ["tracks-discuz", "tracks-quality-guards"]
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
    assignment["task_id"] = s.current_task_id
    assignment["if_ids"] = None  # executor materializes from task graph
    assignment["ac_refs"] = None  # executor materializes from task graph
    assignment["test_refs"] = None  # executor materializes from task graph
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
            "deliverable"
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


def _m_impl_shield_dispatch(s: State) -> Command:
    """SHIELD_FIX: dispatch Shield to fix diagnosed test defects.

    The machine substate stays SHIELD_FIX (flow.md §10), but the dispatched
    agent substate is WRITE per test_dispatch_materialization.py lines 35-43.
    """
    assignment = _m_impl_base_assignment(s, "shield", "WRITE", ["tracks-discuz"])
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
        "objective": "SHIELD_FIX: fix diagnosed test defects",
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
        return Command(
            kind="rollback_stage",
            params={"to_stage": s.return_target, "reason": "diagnose_rollback"},
        )
    return None


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


def _is_verification_task(s: State) -> bool:
    """§1.0.3 two-tier model: a task whose description carries the
    verification-only marker has no RED-implementation - acceptance is
    Runtime-executed (user ruling 2026-08-15), not a Devon RGR cycle."""
    meta = s.current_task_metadata or {}
    return "verification-only" in (meta.get("description") or "")


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
    if sub == "RED" and _is_preset_anchor_task(s):
        # Standard RGR with a preset anchor (frozen failing tests): the RED
        # phase is a Runtime anchor confirmation - Devon has no new unit
        # test to write, so dispatching it only produces an empty
        # changed_paths evidence the gate must reject (run 01KZTHE7 T-013).
        return Command(
            kind="anchor_red",
            params={"stage": "M-IMPL", "task_id": s.current_task_id},
        )
    return _m_impl_devon_dispatch(s, sub)


def _decide_m_impl_exit(s: State) -> Command | None:
    """EXIT: seal stage (no doc) -> stage.exited -> boundary."""
    if not s.stage_exited:
        return Command(kind="write_frontmatter", params={"stage": "M-IMPL"})
    return None
