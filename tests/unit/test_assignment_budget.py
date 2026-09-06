"""Unit: M5 dispatch-side assignment hard budget (convergence plan 2026-09-05).

An assignment JSON over TRAC_ASSIGNMENT_BUDGET (default 16KB) is a
structural task-graph defect: it is rejected before any backend I/O as
plan_defect (scope replan, no agent attempt burned) -- revision
archaeology and escalation add-ons belong in the event log, not the
prompt (b92: 200-600k token dispatches with healthy cards <2KB).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.unit.helpers import (
    M_IMPL_REQUIRED_ASSIGNMENT_KEYS,
    git_repo,
    m_impl_docs,
    m_impl_store,
    m_impl_task,
)
from tracks.executor.executor import Executor, _assignment_budget
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.kernel import m_impl
from tracks.kernel.machine import State

# -- budget knob ---------------------------------------------------------------


def test_assignment_budget_default_16kb(monkeypatch):
    monkeypatch.delenv("TRAC_ASSIGNMENT_BUDGET", raising=False)
    assert _assignment_budget() == 16384


def test_assignment_budget_env_override_and_disable(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "2048")
    assert _assignment_budget() == 2048
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "0")
    assert _assignment_budget() == 0


def test_assignment_budget_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "not-a-number")
    assert _assignment_budget() == 16384


# -- kernel routing ------------------------------------------------------------


def test_route_plan_defect_gate_failure_replans_without_attempt():
    st = State()
    st.substate = "GREEN_GATE"
    st.current_attempt = 2
    st.doc_dispatched = True
    m_impl._route_m_impl_gate_failure(st, "plan_defect", None)
    assert st.substate == "PLANNING"
    # NOT a writer failure: the attempt budget must stay intact.
    assert st.current_attempt == 2


# -- dispatch-side rejection ----------------------------------------------------


class _BudgetStub(Executor):
    """Executor with the dispatch pipeline's heavy halves stubbed out."""

    def __init__(self):  # noqa: D107 - test stub
        self.emitted: list[tuple[str, dict]] = []
        self.backend_calls: list[dict] = []
        self.run_id = "run"
        self.repo = None

    def _assignment_with_evidence(self, assignment, params):
        return assignment

    def _invalid_m_impl_assignment(self, role, substate, assignment):
        return None

    def _release_empty_hotfix_shield(self, cmd, state, task_id, role, substate, assignment):
        return False

    def _reject_invalid_test_tasks(self, state, role, substate, assignment, cmd, task_id):
        return False

    def _dispatch_agent_backend(self, cmd, state, task_id, role, substate, doc, doc_path, assignment):
        self.backend_calls.append({"role": role, "assignment": assignment})

    def _emit(self, type, payload, **kwargs):
        self.emitted.append((type, payload))


def _cmd(assignment: dict) -> SimpleNamespace:
    return SimpleNamespace(
        params={
            "role": "shield",
            "substate": "WRITE",
            "doc": None,
            "assignment": assignment,
        },
        command_id="c1",
    )


def _state() -> State:
    st = State()
    st.stage = "M-TEST"
    st.substate = "WRITE"
    st.current_attempt = 0
    st.current_task_id = "T-1"
    return st


def test_oversized_assignment_rejected_as_plan_defect(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "256")
    stub = _BudgetStub()
    big = {"description": "x" * 512}
    stub._do_dispatch_agent(_cmd(big), _state(), "T-1", False)
    assert stub.backend_calls == []
    assert stub.emitted and stub.emitted[0][0] == "verdict.failed"
    assert stub.emitted[0][1]["check"] == "plan_defect"
    assert "hard budget" in stub.emitted[0][1]["reason"]


def test_oversized_rejection_carries_dedup_audit_breakdown(monkeypatch):
    """M5 audit-first rule (operator OOB 2026-09-06): the FIRST response to
    an over-budget card is a per-section byte breakdown (dedup audit), not
    a budget raise."""
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "256")
    stub = _BudgetStub()
    big = {"description": "x" * 512, "manifest": {"allowed_paths": ["tracks/app.py"]}}
    stub._do_dispatch_agent(_cmd(big), _state(), "T-1", False)
    assert stub.emitted and stub.emitted[0][0] == "verdict.failed"
    assert "dedup audit" in stub.emitted[0][1]["reason"]
    evidence = stub.emitted[0][1]["evidence"]
    assert "section bytes" in evidence
    assert "description=" in evidence
    assert "manifest=" in evidence


def test_budgeted_assignment_dispatches(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "8192")
    stub = _BudgetStub()
    small = {"description": "do the thing"}
    stub._do_dispatch_agent(_cmd(small), _state(), "T-1", False)
    assert len(stub.backend_calls) == 1
    assert stub.backend_calls[0]["assignment"] is small
    assert stub.emitted == []


def test_budget_zero_disables_check(monkeypatch):
    monkeypatch.setenv("TRAC_ASSIGNMENT_BUDGET", "0")
    stub = _BudgetStub()
    big = {"description": "x" * 4096}
    stub._do_dispatch_agent(_cmd(big), _state(), "T-1", False)
    assert len(stub.backend_calls) == 1


# -- M5 card diet: non-writer cards are lean by construction ---------------------
# (convergence plan 2026-09-05 root fix, run 01M19FJVES7G113RD8QXXY3PQZ)


def _fat_task() -> dict:
    """A task whose description carries the revision archaeology that blew
    the budget on the deadlocked RULING card (7KB member)."""
    task = m_impl_task()
    task["description"] = "revision archaeology " + "x" * 7000
    return task


def _diet_executor(tmp_path) -> Executor:
    repo = git_repo(tmp_path, gitignore=True)
    m_impl_docs(repo)
    return Executor(m_impl_store(repo), repo, "RUN")


def _diet_state(task: dict) -> State:
    st = State()
    st.stage = "M-IMPL"
    st.substate = "RULING"
    st.current_attempt = 0
    st.review_round = 0
    st.current_task_id = task["task_id"]
    st.task_refs = [task]
    st.taskgraph_digest = "digest"
    st.current_manifest = None
    st.r_tree_identity = "rt"
    return st


def _card_bytes(card: dict) -> int:
    return len(json.dumps(card, ensure_ascii=False, default=str))


def test_archer_ruling_materialized_card_is_lean(tmp_path, monkeypatch):
    monkeypatch.delenv("TRAC_ASSIGNMENT_BUDGET", raising=False)
    task = _fat_task()
    st = _diet_state(task)
    base = m_impl._m_impl_base_assignment(
        st, "archer", "RULING", ["tracks-discuz", "tracks-archer-planning"]
    )
    card = _diet_executor(tmp_path)._materialize_m_impl_assignment(
        st, {"role": "archer", "substate": "RULING", "assignment": base}, "cid"
    )
    assert _card_bytes(card) < _assignment_budget()
    assert card["manifest"] is None
    assert card["task"] == {
        "task_id": "T-001",
        "issue_number": 1,
        "ac_refs": ["AC-FR0001-01"],
        "if_ids": ["IF-IMPL-001"],
    }
    # small runtime identity fields kept
    assert card["task_id"] == "T-001"
    assert isinstance(card["commands"], dict) and "guard" in card["commands"]
    assert isinstance(card["result_identity"], str) and card["result_identity"]
    assert card["pre_dirty_snapshot"] == {}
    # per-task ref keys stay at kernel base values (None)
    assert card["ac_refs"] is None and card["if_ids"] is None
    assert card["test_refs"] is None
    # key-presence materialization contract still holds
    assert card.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


def test_prism_diagnose_materialized_card_is_lean(tmp_path, monkeypatch):
    monkeypatch.delenv("TRAC_ASSIGNMENT_BUDGET", raising=False)
    task = _fat_task()
    st = _diet_state(task)
    st.substate = "DIAGNOSE"
    base = m_impl._m_impl_base_assignment(
        st, "prism", "DIAGNOSE", ["tracks-discuz", "tracks-prism-impl"]
    )
    base["classification_vocabulary"] = list(m_impl.DIAGNOSE_CLASSIFICATIONS)
    card = _diet_executor(tmp_path)._materialize_m_impl_assignment(
        st, {"role": "prism", "substate": "DIAGNOSE", "assignment": base}, "cid"
    )
    assert _card_bytes(card) < _assignment_budget()
    assert card["manifest"] is None
    assert card["task"] == {
        "task_id": "T-001",
        "issue_number": 1,
        "ac_refs": ["AC-FR0001-01"],
        "if_ids": ["IF-IMPL-001"],
    }
    assert "description" not in card["task"]
    assert card["task_id"] == "T-001"
    assert card["criteria_pack"] and card["r_tree_identity"] == "rt"
    assert card.keys() >= M_IMPL_REQUIRED_ASSIGNMENT_KEYS


def test_devon_green_card_keeps_full_writer_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("TRAC_ASSIGNMENT_BUDGET", raising=False)
    task = _fat_task()
    st = _diet_state(task)
    st.substate = "GREEN"
    # Production Devon cards ride the task.started manifest through
    # state.current_manifest -- the writer execution contract.
    st.current_manifest = {
        "allowed_paths": ["tracks/app.py"],
        "forbidden_paths": [".tracks/projects/**"],
        "big_contract_member": "y" * 12000,
    }
    base = m_impl._m_impl_base_assignment(st, "devon", "GREEN", ["tracks-devon-rgr"])
    base["phase"] = "green"
    card = _diet_executor(tmp_path)._materialize_m_impl_assignment(
        st, {"role": "devon", "substate": "GREEN", "assignment": base}, "cid"
    )
    manifest = card["manifest"]
    assert isinstance(manifest, dict) and "big_contract_member" in manifest
    assert card["task"]["description"] == task["description"]
    assert card["ac_refs"] == ["AC-FR0001-01"] and card["if_ids"] == ["IF-IMPL-001"]
    # The diet leaves the writer burden untouched: an oversized writer card
    # still trips the M5 budget (the b92 token-blowup class).
    assert _card_bytes(card) > _assignment_budget()


def test_task_manifest_task_ref_is_slim_identity(tmp_path):
    task = _fat_task()
    st = _diet_state(task)
    node = MImplRuntimeMixin._task_node(task)
    manifest = _diet_executor(tmp_path)._task_manifest(node, st)
    assert manifest["task_ref"] == {"task_id": "T-001"}
