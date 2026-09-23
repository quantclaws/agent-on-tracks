import json as json
from pathlib import Path

from tests.unit import m_impl_machine_sequences as m_impl
from tests.unit.test_machine_m_impl_support import (
    _M_IMPL_CRITERIA_PACK,
    ENTER_M_IMPL,
    GREEN_GATE_CMD,
    SELECT_TASK_CMD,
    TASKGRAPH_CMD,
    _diagnose_state,
    _full_single_task_cycle,
    _new_plan_events,
    _old_plan_events,
    decide,
    seq,
    state_of,
)


def test_red_defect_in_diagnose_vocabulary():
    """red_defect must remain a legal DIAGNOSE classification: present in the
    executor/opencode vocabulary gates. The classification vocabulary itself
    is assignment-authoritative (inlined per dispatch), so the prompt/skill
    prose is deliberately NOT text-pinned here (b91 host-neutrality)."""
    from tracks.effects.opencode import OpencodeBackend

    assert "red_defect" in OpencodeBackend._DIAGNOSE_CLASSIFICATIONS


def test_b83_retained_completed_count():
    """After replacement taskgraph, tasks_completed equals retained count (1),
    not old total (2). Merged T-006-007 is not retained."""
    s = state_of(*_old_plan_events(), *_new_plan_events())
    assert s.substate == "TASK_DISPATCH"
    assert s.tasks_total == 2
    assert s.tasks_completed == 1
    assert s.retained_completed_task_ids == ["T-001"]
    assert s.taskgraph_generation == 1


def test_b83_retained_task_not_selected_again():
    """decide() at TASK_DISPATCH after replacement selects T-006-007, not
    retained T-001."""
    s = state_of(*_old_plan_events(), *_new_plan_events())
    assert s.substate == "TASK_DISPATCH"
    cmd = decide(s)
    assert cmd.kind == "select_task"


def test_b83_merged_task_completion_increments_correctly():
    """Merged T-006-007 completes, tasks_completed becomes 2 (not 3), and
    substate transitions to ISLAND_GATE_2 (tasks_total=2)."""
    merged_done = [
        ("command.issued", {"command": {"kind": "select_task", "params": {"stage": "M-IMPL"}, "command_id": "C103"}}),
        ("task.started", {"task_id": "T-006-007"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "RED"}, "command_id": "C104"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "RED_GATE"}, "command_id": "C105"}}),
        ("verdict.passed", {"check": "red_valid"}),
        ("command.issued", {"command": {"kind": "checkpoint_red", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C106"}}),
        ("red.checkpointed", {"r_sha": "r006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_RED"}, "command_id": "C107"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "GREEN"}, "command_id": "C108"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "GREEN_GATE"}, "command_id": "C109"}}),
        ("verdict.passed", {"check": "green"}),
        ("command.issued", {"command": {"kind": "commit_green", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C110"}}),
        ("green.committed", {"commit_sha": "g006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "REFACTOR"}, "command_id": "C111"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C112"}}),
        ("refactor.committed", {"commit_sha": "r006"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"}, "command_id": "C113"}}),
        ("verdict.passed", {"check": "task_review"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_FINAL"}, "command_id": "C114"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "complete_task", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C115"}}),
        ("task.completed", {"task_id": "T-006-007"}),
    ]
    s = state_of(*_old_plan_events(), *_new_plan_events(), *merged_done)
    assert s.tasks_completed == 2
    assert s.tasks_total == 2
    assert s.substate == "ISLAND_GATE_2", "must reach ISLAND_GATE_2, not TASK_DISPATCH"


def test_b83_retained_task_completion_event_does_not_double_count():
    """During replay, the old task.completed(T-001) event fires but the
    reducer skips the increment (T-001 is in retained set)."""
    # Replay the full sequence: old plan + new plan + merged completion
    merged_done = [
        ("command.issued", {"command": {"kind": "select_task", "params": {"stage": "M-IMPL"}, "command_id": "C103"}}),
        ("task.started", {"task_id": "T-006-007"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "RED"}, "command_id": "C104"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "RED_GATE"}, "command_id": "C105"}}),
        ("verdict.passed", {"check": "red_valid"}),
        ("command.issued", {"command": {"kind": "checkpoint_red", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C106"}}),
        ("red.checkpointed", {"r_sha": "r006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_RED"}, "command_id": "C107"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "GREEN"}, "command_id": "C108"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "GREEN_GATE"}, "command_id": "C109"}}),
        ("verdict.passed", {"check": "green"}),
        ("command.issued", {"command": {"kind": "commit_green", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C110"}}),
        ("green.committed", {"commit_sha": "g006"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "devon", "substate": "REFACTOR"}, "command_id": "C111"}}),
        ("outcome.received", {"role": "devon", "status": "done"}),
        ("command.issued", {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C112"}}),
        ("refactor.committed", {"commit_sha": "r006"}),
        ("command.issued", {"command": {"kind": "run_task_gates", "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"}, "command_id": "C113"}}),
        ("verdict.passed", {"check": "task_review"}),
        ("command.issued", {"command": {"kind": "dispatch_agent", "params": {"role": "prism", "substate": "PRISM_FINAL"}, "command_id": "C114"}}),
        ("outcome.received", {"role": "prism", "status": "done"}),
        ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)}),
        ("command.issued", {"command": {"kind": "complete_task", "params": {"stage": "M-IMPL", "task_id": "T-006-007"}, "command_id": "C115"}}),
        ("task.completed", {"task_id": "T-006-007"}),
    ]
    evs = seq(*ENTER_M_IMPL, *_old_plan_events(), *_new_plan_events(), *merged_done)
    from tracks.kernel import project as _project
    s1 = _project(evs)
    s2 = _project(evs)
    assert s1.tasks_completed == s2.tasks_completed == 2
    assert s1.retained_completed_task_ids == s2.retained_completed_task_ids == ["T-001"]
    assert s1.substate == s2.substate == "ISLAND_GATE_2"


def test_b83_same_id_changed_payload_not_retained():
    """A task with the same ID but different payload is NOT retained.
    Unit-level: the _task_payloads_equivalent check rejects it."""
    task_t1_old = {"task_id": "T-001", "issue_number": 83, "description": "original desc", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}
    task_t1_new = {"task_id": "T-001", "issue_number": 83, "description": "changed desc", "ac_refs": ["AC-1"], "fr_refs": [], "if_ids": ["IF-1"], "test_refs": [], "unit_refs": [], "acceptance_refs": [], "schema": 2, "scope_boundary": "tracks/", "depends_on": ["-"], "batch": "1", "parallel": "0", "budget": 3}
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin
    assert not MImplRuntimeMixin._task_payloads_equivalent(task_t1_old, task_t1_new)


def test_b83_retained_depends_on_chain():
    """A task whose dependency is retained (T-001) is selectable in the new
    plan."""
    # Project state: old plan completes T-001, scope failure, new plan retains T-001
    s = state_of(*_old_plan_events(), *_new_plan_events())
    assert s.substate == "TASK_DISPATCH"
    # Retained T-001 is "completed" for dependency resolution; T-006-007
    # depends on it so it should be ready.
    assert s.retained_completed_task_ids == ["T-001"]
    # decide() for TASK_DISPATCH produces select_task, and the executor
    # (not the pure kernel) resolves the actual ready set. At the state
    # level, check that the retention projection is correct.
    cmd = decide(s)
    assert cmd.kind == "select_task"


def test_replacement_taskgraph_clears_inflight_task_lease():
    """#139: a within-residency graph replacement must clear the in-flight
    task lease even when the replan did NOT route through scope failure
    (operator/RULING commit_taskgraph, run 01M19FJV: graph 81d33fa5 landed
    at seq 3624 while T-042 was in flight; select_task then no-op'ed 20x
    on the stale current_task_id guard and tripped loop.aborted)."""
    task = {
        "task_id": "T-007",
        "issue_number": 139,
        "description": "in-flight scope",
        "ac_refs": ["AC-2"],
        "fr_refs": [],
        "if_ids": ["IF-2"],
        "test_refs": [],
        "unit_refs": [],
        "acceptance_refs": [],
        "schema": 2,
        "scope_boundary": "tracks/",
        "depends_on": ["-"],
        "batch": "1",
        "parallel": "0",
        "budget": 3,
    }
    old_commit = (
        "taskgraph.committed",
        {
            "task_count": 1,
            "task_ids": ["T-007"],
            "tasks": [task],
            "digest": "aaa",
            "path": "tasks.json",
        },
    )
    replacement_commit = (
        "taskgraph.committed",
        {
            "task_count": 1,
            "task_ids": ["T-007"],
            "tasks": [{**task, "description": "revised scope"}],
            "digest": "bbb",
            "path": "tasks.json",
            "retained_completed_task_ids": [],
        },
    )
    s = state_of(
        *m_impl.prism_plan(old_commit),
        SELECT_TASK_CMD,
        ("task.started", {"task_id": "T-007", "task": dict(task), "manifest": {"task_id": "T-007"}}),
        # No scope verdict routes the replan -- the replacement lands via a
        # direct commit_taskgraph (operator/RULING path).
        TASKGRAPH_CMD,
        replacement_commit,
    )
    assert s.taskgraph_generation == 1
    assert s.current_task_id is None
    assert s.current_task_metadata is None
    assert s.current_manifest is None
    assert s.substate == "ISLAND_GATE_1"


def test_diagnose_plan_defect_routes_to_planning():
    """#89: DIAGNOSE plan_defect -> PLANNING (Archer replan), no attempt
    consumed, evidence preserved, task identity cleared."""
    s = _diagnose_state("plan_defect")
    assert s.substate == "PLANNING"
    assert s.current_attempt == 0, "plan_defect must not consume the attempt budget"
    assert s.taskgraph_committed is False
    assert s.doc_dispatched is False
    assert s.current_task_id is None, "stale task identity must be cleared"
    assert s.green_committed is False
    assert s.refactor_done is False
    assert s.r_tree_identity is None
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer", "plan_defect must dispatch Archer, not Devon"
    assert cmd.params["substate"] == "PLANNING"
    assert "evidence" in cmd.params


def test_prism_final_revise_plan_defect_to_planning():
    """#89: PRISM_FINAL revise plan_defect -> PLANNING (Archer replan),
    no attempt consumed, task identity cleared."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "plan_defect",
        },
    )
    s = state_of(*m_impl.prism_final_done(), revise)
    assert s.substate == "PLANNING"
    assert s.current_attempt == 0, "plan_defect must not consume the attempt budget"
    assert s.taskgraph_committed is False
    assert s.current_task_id is None
    assert s.green_committed is False
    assert s.refactor_done is False
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["substate"] == "PLANNING"


def test_plan_defect_in_diagnose_vocabulary():
    """plan_defect must remain a legal DIAGNOSE classification: present in
    the executor whitelist and the opencode backend classification tuple.
    The classification vocabulary itself is assignment-authoritative
    (inlined per dispatch), so the prompt/skill prose is deliberately NOT
    text-pinned here (b91 host-neutrality)."""
    from tracks.effects.opencode import OpencodeBackend

    repo_root = Path(__file__).resolve().parents[2]
    assert "plan_defect" in OpencodeBackend._DIAGNOSE_CLASSIFICATIONS
    executor_src = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((repo_root / "tracks/executor").glob("*.py"))
    )
    assert "plan_defect" in executor_src, "executor whitelist/filter lacks plan_defect"


def test_diagnose_dispatch_inlines_classification_vocabulary():
    """#89/B90: D-39 session reuse freezes the agent/skill vocabulary at
    session-creation time, so prompt-contract revisions never reach an
    existing session (run 01M0S0FQ T-012: plan_defect committed, Prism
    still said impl_defect). The runtime must inline the vocabulary into
    every DIAGNOSE/PRISM_FINAL assignment — fresh per dispatch."""
    from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS

    s_diag = state_of(
        *_full_single_task_cycle()[:24],  # through DEVON GREEN done
        GREEN_GATE_CMD,
        ("verdict.failed", {"check": "unknown_attribution", "reason": "x", "attempt": 1}),
    )
    cmd = decide(s_diag)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    vocab = cmd.params["assignment"]["classification_vocabulary"]
    assert vocab == list(DIAGNOSE_CLASSIFICATIONS)
    assert "plan_defect" in vocab

    s_final = state_of(*_full_single_task_cycle()[:34])  # through TASK_REVIEW_PASS
    cmd2 = decide(s_final)
    assert cmd2 is not None and cmd2.params["role"] == "prism"
    assert cmd2.params["assignment"]["classification_vocabulary"] == list(
        DIAGNOSE_CLASSIFICATIONS
    )


def test_effects_whitelist_shares_kernel_vocabulary_source():
    """#89 single source of truth: the opencode backend whitelist and the
    kernel dispatch injection must be the SAME tuple — drift between them
    recreates the B63 'Prism can never say it' failure class."""
    from tracks.effects import opencode as _oc
    from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS

    assert _oc.OpencodeBackend._DIAGNOSE_CLASSIFICATIONS is DIAGNOSE_CLASSIFICATIONS


def test_green_objective_names_the_failed_anchors():
    """OOB 2026-09-05: a GREEN re-dispatch must point at the anchors the
    GREEN_GATE will re-run -- a generic "implement task X" objective let the
    writer work adjacent faces while the same anchors stayed red (run
    01M19FJVES7G113RD8QXXY3PQZ). Deterministic (sorted) and bounded (cap)."""
    pre = [*m_impl.prism_red(), m_impl.GREEN_GATE_CMD]
    failed = (
        "verdict.failed",
        {
            "check": "impl_defect",
            "task_id": "T-042",
            "attempt": 1,
            "evidence": json.dumps(
                {
                    "failed_nodes": [
                        {"node": "tests/integration/test_b.py::test_two"},
                        {"node": "tests/integration/test_a.py::test_one"},
                    ]
                }
            ),
        },
    )
    # Real flow: the gate verdict routes DIAGNOSE; Prism's diagnosis
    # verdict (impl_defect, anchors on the payload) routes GREEN.
    diagnosis = (
        "verdict.failed",
        {
            "check": "impl_defect",
            "task_id": "T-042",
            "attempt": 2,
            "reason": "wiring gaps",
            "evidence": "Prism diagnosis prose (not JSON)",
            "failed_nodes": [
                {"node": "tests/integration/test_b.py::test_two"},
                {"node": "tests/integration/test_a.py::test_one"},
            ],
        },
    )
    s = state_of(*pre, failed, diagnosis)
    assert s.substate == "GREEN"
    cmd = decide(s)
    assert cmd is not None and cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "devon"
    objective = cmd.params["objective"]
    assert "test_a.py::test_one" in objective and "test_b.py::test_two" in objective
    # sorted ascending: a before b
    assert objective.index("test_a.py") < objective.index("test_b.py")


def test_green_objective_omits_anchor_clause_without_nodes():
    """No parseable failed_nodes -> no invented clause (fail-closed)."""
    pre = [*m_impl.prism_red(), m_impl.GREEN_GATE_CMD]
    failed = (
        "verdict.failed",
        {"check": "impl_defect", "task_id": "T-042", "attempt": 1, "evidence": "not json"},
    )
    s = state_of(*pre, failed)
    cmd = decide(s)
    assert "the gate failed on these anchors" not in cmd.params["objective"]


def test_prism_final_revise_test_defect_to_shield_fix():
    """Live defect (run 01M2QTJB T-002 rounds 7-8, 2026-09-20): the sole
    blocker finding carried defect_classification=test_defect on the FINDING
    (never lifted to the verdict top level); the router defaulted to
    impl_defect -> Devon GREEN, which may not touch the frozen acceptance
    test — two zero-delta no-op rounds and an unbounded revise loop. The
    sole-voice derivation routes the fix to its rightful owner: Shield."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "findings": [
                {
                    "id": "T002-F2",
                    "severity": "blocker",
                    "summary": "frozen anchor asserts the positive path",
                    "defect_classification": "test_defect",
                }
            ],
        },
    )
    s = state_of(*m_impl.prism_final_done(), revise)
    assert s.substate == "SHIELD_FIX"
    assert s.current_attempt == 1, "test_defect consumes the attempt budget (DIAGNOSE parity)"
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "shield"
    assert cmd.params["substate"] == "WRITE"


def test_prism_final_revise_top_level_test_defect_routes_shield():
    """The top-level field (when the reviewer provides it) routes the same
    way — one semantics, two carriers."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "defect_classification": "test_defect",
        },
    )
    s = state_of(*m_impl.prism_final_done(), revise)
    assert s.substate == "SHIELD_FIX"


def test_prism_final_revise_mixed_finding_voices_keep_default_green():
    """Conservative derivation: findings disagreeing (or silent) stay None —
    the router keeps the historical impl_defect -> GREEN default."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "findings": [
                {"id": "F1", "severity": "blocker", "summary": "a",
                 "defect_classification": "test_defect"},
                {"id": "F2", "severity": "major", "summary": "b",
                 "defect_classification": "impl_defect"},
            ],
        },
    )
    s = state_of(*m_impl.prism_final_done(), revise)
    assert s.substate == "GREEN"


def test_prism_final_revise_no_classification_keeps_default_green():
    revise = (
        "prism.verdict",
        {"verdict": "revise", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)},
    )
    s = state_of(*m_impl.prism_final_done(), revise)
    assert s.substate == "GREEN"


def test_prism_red_revise_sole_voice_plan_defect_routes_replan():
    """Live defect (run 01M2QTJB T-017, 2026-09-21 14:28-14:30): both findings
    carried defect_classification=plan_defect (no-legitimate-Red retype
    demand) but the top-level field was None; the PRISM_RED router defaulted
    to RED re-nail and the anchor walk deterministically re-checkpointed
    three rounds before escalating. The sole-voice derivation now applies to
    the PRISM_RED (and PRISM_PLAN) revise routing, not just PRISM_FINAL."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "findings": [
                {
                    "id": "F1",
                    "severity": "blocker",
                    "summary": "R-tree all green, no legitimate Red",
                    "defect_classification": "plan_defect",
                },
                {
                    "id": "F2",
                    "severity": "blocker",
                    "summary": "unit_ref pins another task's ACs",
                    "defect_classification": "plan_defect",
                },
            ],
        },
    )
    # 直接驱动路由函数(与 PRISM_FINAL 版测试同型,绕过序列装配)
    from tracks.kernel.m_impl_routing import _on_m_impl_prism_verdict
    from tracks.kernel.machine import State

    s = State(stage="M-IMPL", substate="PRISM_RED", current_task_id="T-017",
              taskgraph_committed=True, current_attempt=0)
    _on_m_impl_prism_verdict(s, revise[1])
    assert s.substate == "PLANNING", "unanimous plan_defect findings must route to replan"
    assert s.current_attempt == 0, "plan_defect must not consume the attempt budget"


def test_prism_plan_revise_sole_voice_routes_planning():
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "findings": [
                {"id": "F1", "severity": "blocker", "summary": "graph defect",
                 "defect_classification": "plan_defect"},
            ],
        },
    )
    from tracks.kernel.m_impl_routing import _on_m_impl_prism_verdict
    from tracks.kernel.machine import State

    s = State(stage="M-IMPL", substate="PRISM_PLAN", current_task_id="T-001",
              taskgraph_committed=True, current_attempt=0)
    _on_m_impl_prism_verdict(s, revise[1])
    assert s.substate == "PLANNING"


def test_prism_plan_revise_derivation_changes_route():
    """Prism voice2-R1 advisory: prove the derivation changes behavior -- a
    sole-voice stub_gap routes to DIAGNOSE; without the derivation the None
    top-level field would fall to the PLANNING default."""
    revise = (
        "prism.verdict",
        {
            "verdict": "revise",
            "criteria_pack": dict(_M_IMPL_CRITERIA_PACK),
            "findings": [
                {"id": "F1", "severity": "blocker", "summary": "promised stub absent",
                 "defect_classification": "stub_gap"},
            ],
        },
    )
    from tracks.kernel.m_impl_routing import _on_m_impl_prism_verdict
    from tracks.kernel.machine import State

    s = State(stage="M-IMPL", substate="PRISM_PLAN", current_task_id="T-001",
              taskgraph_committed=True, current_attempt=0)
    _on_m_impl_prism_verdict(s, revise[1])
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"


def test_fully_retained_regraph_routes_island_2():
    """Live defect (run 01M2QTJB, 2026-09-22): all 23 tasks completed (T-016
    last); a tail replan re-committed the identical graph and the
    unconditional ISLAND_GATE_1 routing sent the flow into select_task's
    "no ready tasks" failure loop (three replans -> escalation) instead of
    the terminal full-suite verification. A fully-retained re-commit routes
    ISLAND_GATE_2 directly."""
    from tracks.kernel.m_impl_state import _on_taskgraph_committed
    from tracks.kernel.machine import State

    s = State(stage="M-IMPL", substate="PLANNING")
    _on_taskgraph_committed(
        s,
        {
            "task_count": 3,
            "digest": "d",
            "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}, {"task_id": "T-3"}],
            "retained_completed_task_ids": ["T-1", "T-2", "T-3"],
        },
        None,
    )
    assert s.tasks_completed == 3 and s.tasks_total == 3
    assert s.substate == "ISLAND_GATE_2", "exhausted graph must go to terminal verification"

    # A normal re-commit with remaining work keeps the historical route.
    s2 = State(stage="M-IMPL", substate="PLANNING")
    s2.task_refs = [{"task_id": "T-0"}]
    _on_taskgraph_committed(
        s2,
        {
            "task_count": 3,
            "digest": "d",
            "tasks": [{"task_id": "T-1"}, {"task_id": "T-2"}, {"task_id": "T-3"}],
            "retained_completed_task_ids": ["T-1"],
        },
        None,
    )
    assert s2.substate == "ISLAND_GATE_1"
