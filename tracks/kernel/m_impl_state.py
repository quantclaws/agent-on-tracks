"""M-IMPL state constants, classification vocabulary, and event reducers.

Extracted from :mod:`tracks.kernel.m_impl` for module-size compliance
(C0302): the criteria-pack/context constants, the DIAGNOSE classification
vocabulary (single source of truth), and the baseline/ledger/cycle
reducers. :mod:`tracks.kernel.m_impl` re-exports every name below, so the
machine import surface is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tracks.baseline import BASELINE_DOC_NAMES

from .events import EventEnvelope
from .m_test import _reset_doc, _reset_review

if TYPE_CHECKING:
    from .machine import State

# -- M-IMPL constants -------------------------------------------------------

# D-29 criteria pack identity (architecture.md §3.4): Prism in M-IMPL loads
# the impl criteria pack and echoes the identity in its verdict. #172 dual
# delivery: the same identity names the pack Archer/Devon load for their
# pre-emission self-check — the version must track the skill frontmatter
# (tracks/skills/tracks-prism-impl/SKILL.md; was drift-stuck at 0.1 while
# the skill shipped 0.2, synced 2026-09-20 with the 0.3 writer note).
_M_IMPL_CRITERIA_PACK = {"name": "tracks-prism-impl", "version": "0.3"}
# M-IMPL context docs: trio + design trio (flow.md §10 BASELINE).
_M_IMPL_CONTEXT_DOCS = BASELINE_DOC_NAMES
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
        # B83 symmetry (#139): the in-flight task lease belongs to the
        # REPLACED graph -- its task may be merged/redefined/deferred away.
        # _do_select_task's ``current_task_id`` guard would otherwise no-op
        # forever (command-stall tight-loop, run 01M19FJV seq 3638+); the
        # executor-side counterparts (started_this_cycle cutoff, stale
        # writelock release) already key on latest_taskgraph_seq. A
        # re-selection fires task.started, which resets every per-task
        # field (attempt, green_committed, r_tree_identity, ...).
        s.current_task_id = None
        s.current_task_metadata = None
        s.current_manifest = None
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


def _clear_task_residency(s: State) -> None:
    """Clear the current task's residency state (graph replacement / waive)."""
    s.current_task_id = None
    s.current_task_metadata = None
    s.current_manifest = None
    s.green_committed = False
    s.refactor_done = False
    s.r_tree_identity = None
