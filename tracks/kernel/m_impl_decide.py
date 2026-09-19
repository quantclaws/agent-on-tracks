"""M-IMPL decide() control flow and dispatch builders.

Extracted from :mod:`tracks.kernel.m_impl` for module-size compliance
(C0302): the per-substate decide functions, the task-type predicates, and
the Archer/Devon/Prism/Shield dispatch assignment builders.
:mod:`tracks.kernel.m_impl` re-exports every name below, so the machine
import surface is unchanged.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .contracts import (
    DEVON_EVIDENCE_CONTRACT,
    PRISM_DIAGNOSE_CONTRACT,
    WRITE_MANIFEST_CONTRACT,
)
from .events import Command
from .m_impl_state import (
    _M_IMPL_CONTEXT_DOCS,
    _M_IMPL_CRITERIA_PACK,
    _M_IMPL_REVIEW_SUBSTATES,
    DIAGNOSE_CLASSIFICATIONS,
)

if TYPE_CHECKING:
    from .machine import State

# -- M-IMPL dispatch builders (flow.md §10 / D-29) --------------------------


def _set_m_impl_dispatch_flags(s: State, cmd: dict) -> None:
    """Set dispatch flags for an M-IMPL dispatch_agent command."""
    params = cmd.get("params", {})
    if params.get("scope") == "security":
        # Out-of-band review (the park chain's security-policy audit): it is
        # not the current task's dispatch -- setting doc/reviewer flags would
        # route its outcome through the task's RGR gate (live 01M2AG4: a
        # security review dispatch set doc_dispatched and its pass was then
        # judged as the RED outcome, red_invalid).
        return
    sub = params.get("substate")
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
    last_check = (s.last_failure or {}).get("check")
    if last_check in ("evidence_malformed", "manifest_malformed"):
        objective = (
            "simplify the output contract (repeated shape violations, S3): "
            "the writer failed the same envelope schema twice in a row -- "
            "rule a machine-executable contract simplification delta; the "
            "current envelope overstrains the writer"
        )
    elif last_check == "diagnosis_exhausted":
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
        # flowing back. Declared dispatches (envelope v2): the fenced
        # tracks-envelope block IS the evidence object -- the bare-JSON
        # wording alone made the live agent skip the fence (live 01M19FJ
        # T-042: reply ended with bare JSON, classified
        # no_envelope_block).
        "objective": (
            f"implement task {s.current_task_id} ({phase}); your FINAL reply "
            "must end with the evidence JSON object per skill "
            "tracks-devon-rgr §4 - prose or Markdown reports are not a "
            "deliverable; when the assignment declares tracks-envelope:v2 "
            "the evidence object is the payload of exactly one fenced "
            "```tracks-envelope block (no prose outside it)"
            + _green_gate_anchor_clause(s) + _shield_diagnosis_clause(s)
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


_GREEN_ANCHOR_CAP = 15


def _node_names(carrier) -> list[str]:
    """Anchor names from one failed_nodes carrier (list of dicts/strings)."""
    if not isinstance(carrier, list):
        return []
    names: list[str] = []
    for node in carrier:
        if isinstance(node, dict):
            name = str(node.get("node") or "")
        else:
            name = str(node) if node else ""
        if name:
            names.append(name)
    return names


def _evidence_failed_nodes(evidence) -> list[str]:
    """Anchor names embedded in the evidence channel (JSON string or dict)."""
    data = None
    if isinstance(evidence, str):
        try:
            data = json.loads(evidence)
        except (ValueError, TypeError):
            data = None
    elif isinstance(evidence, dict):
        data = evidence
    if not isinstance(data, dict):
        return []
    return _node_names(data.get("failed_nodes"))


def _green_gate_anchor_clause(s: State) -> str:
    """GREEN re-dispatch: name the anchors the gate actually failed on.

    FR-11 carries the failed nodes in the evidence channel, but a generic
    "implement task X (green)" objective lets the writer pick its own
    priorities -- live evidence (run 01M19FJVES7G113RD8QXXY3PQZ) it spent
    two 30+ minute rounds on adjacent faces while the same 15 event-producer
    anchors stayed red. The re-dispatch must point at the failure the gate
    will re-run, not at the task in the abstract (M2: the fixer rules on
    live evidence, never on archaeology). Deterministic and bounded: sorted,
    capped, and empty when no carrier holds parseable nodes.
    """
    last_failure = s.last_failure or {}
    names = _evidence_failed_nodes(last_failure.get("evidence"))
    # Payload-level carrier (diagnosis verdicts): the anchors ride the
    # last_failure dict directly, not inside the evidence prose.
    names += _node_names(last_failure.get("failed_nodes"))
    if not names:
        return ""
    unique = sorted(dict.fromkeys(names))
    shown = unique[:_GREEN_ANCHOR_CAP]
    clause = "; ".join(shown)
    suffix = f" (+{len(unique) - len(shown)} more)" if len(unique) > len(shown) else ""
    return (
        "; the gate failed on these anchors and will re-run them: "
        f"{clause}{suffix}"
    )


def _m_impl_prism_dispatch(s: State, sub: str) -> Command:
    """PRISM_PLAN/PRISM_RED/PRISM_FINAL/DIAGNOSE: dispatch Prism with criteria."""
    objective = {
        "PRISM_PLAN": "review task graph plan",
        "PRISM_RED": "review Red checkpoint B..R",
        "PRISM_FINAL": "review complete task range and lineage",
        "DIAGNOSE": "diagnose failure attribution",
    }.get(sub, "review") + (
        # Declared dispatches: the analysis is welcome, but the FINAL
        # message must be the verdict envelope itself (live 01M19FJ
        # PRISM_PLAN: the model stopped after its prose analysis and never
        # emitted the fenced verdict -- no_envelope_block).
        "; your FINAL message must be exactly one fenced ```tracks-envelope "
        "block carrying the verdict payload per the assignment schema "
        "(prose analysis may precede it, nothing may follow it)"
    )
    assignment = _m_impl_base_assignment(s, "prism", sub, ["tracks-discuz", "tracks-prism-impl"])
    assignment["criteria_pack"] = dict(_M_IMPL_CRITERIA_PACK)
    if sub in ("DIAGNOSE", "PRISM_FINAL"):
        # #89（B90 现场）：D-39 会话复用把 agent 定义/skill 词表冻结在
        # 会话初建时——提示词合同修订到不了已存在的会话（run 01M0S0FQ
        # T-012：#89 词表已提交，Prism 仍只能报 impl_defect）。词表由
        # runtime 机器内联进 assignment，随每次派发新鲜送达，与会话缓存
        # 无关；单一真相源为 kernel.DIAGNOSE_CLASSIFICATIONS。
        assignment["classification_vocabulary"] = list(DIAGNOSE_CLASSIFICATIONS)
    if sub == "DIAGNOSE":
        # 2026-09-19 (run 01M2QTJB T-006): three DIAGNOSE attempts in a row
        # never emitted the envelope (no_envelope_block) -- schema+vocabulary
        # alone did not teach the shape. Inject the concrete two-layer
        # example (same therapy as the Devon evidence contract, whose
        # missing_kind burns stopped the moment its example showed the
        # full envelope structure).
        assignment["verdict_contract"] = dict(PRISM_DIAGNOSE_CONTRACT)
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
            "must end with the artifact manifest JSON object per "
            "Shield §输出合同 - prose or Markdown reports are not a "
            "deliverable; when the assignment declares tracks-envelope:v2 "
            "the manifest is the payload of exactly one fenced "
            "```tracks-envelope block (envelope header with kind/version + "
            "payload, nothing outside the block)"
            + _shield_diagnosis_clause(s)
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
