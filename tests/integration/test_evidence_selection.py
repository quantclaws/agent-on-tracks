"""Integration: AC-FR0251-01 per-task SELECT_TASK GREEN_GATE selection.

Contract (IF-SELECT-002 / acceptance AC-FR0251-01): during each M-IMPL task
GREEN_GATE, Runtime must emit ``test.selected(scope=task_if, task_id,
task_ifs, nodes, selection_id, baseline, commit, tree_stamp)`` BEFORE any
selected execution. The selected set is the task's targeted unit nodes (the
immutable R commit's Runtime-captured RED artifact manifest union
GREEN-touched unit files) union the integration nodes mapped by the task's
IF set (test-plan §8 IF rows); it never contains e2e or R1/T-HIST full
run (e2e local execution lives only in the FULL chain, FR-0253). The
contract ``run_selected`` consumes ``{nodes}``/``{result}``; the recorder
writes exact JUnit and records argv. GREEN_GATE pass binds that selection
identity (IF-EVIDENCE-001: evidence identity includes selection_id).

This RED assembles the current atomic project contract (the fake Archer
writes flat [unit]/[integration]/[e2e] sections each declaring collect/
run/run_selected with {result}/{nodes} embedded, plus the [nightly] section
-- interfaces §1m/§1n) and valid M-IMPL state via the public journey, then
exercises the existing ``_run_green_gate`` behavior. It fails FIRST because
``test.selected(scope=task_if)`` is absent: the current ``_run_green_gate``
runs manifest unit commands directly without emitting a selection event
(the IF-SELECT-002 contract token is missing at the GREEN_GATE seam). The
composition / pre-execution order / no-full-run / evidence-binding
assertions bind the Runtime implementation to the locked selection contract.
"""

from __future__ import annotations

import json

from tests.integration.v05_contract_helpers import (
    first_m_impl_attempt_segment,
    run_m_impl_journey,
)


# AC-FR0251-01@v0.6 TRACKS-TRACE green gate emits task_if selection before execution
def test_green_gate_emits_task_if_selection_before_execution(
    trac, event_log, host_repo
):
    """Per-task GREEN_GATE must emit test.selected(scope=task_if) BEFORE any
    selected execution; the selected set is targeted unit union task-IF-
    mapped integration, never e2e/R1 full; the pass verdict binds the
    selection identity. Fails first because the current _run_green_gate
    emits no test.selected(scope=task_if)."""
    run_id, _result, events = run_m_impl_journey(trac, event_log)
    segment = first_m_impl_attempt_segment(events)

    # The first task's GREEN_GATE emits its selection before execution.
    task_if_selections = [
        e
        for e in segment
        if e["type"] == "test.selected" and e["payload"].get("scope") == "task_if"
    ]
    assert task_if_selections, (
        "GREEN_GATE emitted no test.selected(scope=task_if): per-task SELECT_TASK "
        "selection must precede any selected execution (AC-FR0251-01, IF-SELECT-002)"
    )

    selection = task_if_selections[0]
    payload = selection["payload"]

    # SELECT_TASK is per-task: the first task.started carries the gated task
    # identity + declared IF set; the selection must bind THAT task and its IFs.
    task_started = next(e for e in segment if e["type"] == "task.started")
    task = task_started["payload"]["task"]
    assert payload.get("task_id") == task["task_id"], (
        "test.selected(scope=task_if) must bind the gated task id"
    )
    assert sorted(payload.get("task_ifs") or []) == sorted(task.get("if_ids") or []), (
        "test.selected task_ifs must equal the task's declared IF set"
    )

    # IF-SELECT-002 selection identity: every identity field is present and
    # non-empty (selection_id = sha256(canonical_json({scope,basis,nodes,
    # baseline,commit,tree_stamp})); tree_stamp is the dirty-aware worktree
    # stamp, separate from commit).
    for field in (
        "nodes",
        "nodes_blob",
        "baseline",
        "commit",
        "tree_stamp",
        "selection_id",
    ):
        assert payload.get(field), f"test.selected(scope=task_if) lacks identity field {field}"

    # Composition: targeted unit union task-IF-mapped integration; NEVER e2e,
    # NEVER an R1/T-HIST full-run node set (e2e executes only in FULL chain).
    selected_nodes = payload.get("nodes") or []
    assert selected_nodes, "SELECT_TASK must select a non-empty node set"
    assert not any(n.startswith("tests/e2e/") for n in selected_nodes), (
        "SELECT_TASK must never select e2e nodes (e2e runs only in the FULL chain)"
    )

    # The task's targeted unit test_refs (immutable R RED manifest) must be in
    # the selected set -- SELECT_TASK includes the targeted unit nodes.
    for unit_ref in task.get("test_refs") or []:
        assert unit_ref in selected_nodes, (
            f"SELECT_TASK must include the task's targeted unit node {unit_ref}"
        )

    # Integration composition comes from the task's DECLARED anchor refs
    # (B50/#65: schema-2 ``acceptance_refs``, legacy schema-1 maps the
    # non-unit ``test_refs``), never the retired §8 IF-index inference.
    # Compare selected file identities to those declared integration cells,
    # not to Runtime implementation internals.
    declared_integration = [
        r
        for r in (task.get("acceptance_refs") or task.get("test_refs") or [])
        if r.startswith("tests/integration/")
    ]
    expected_integration_files = {
        ref.partition("::")[0] for ref in declared_integration
    }
    selected_integration_files = {
        node.partition("::")[0]
        for node in selected_nodes
        if node.startswith("tests/integration/")
    }
    assert selected_integration_files == expected_integration_files, (
        "SELECT_TASK integration nodes must equal the task's declared "
        f"integration anchors: selected={sorted(selected_integration_files)} "
        f"declared={sorted(expected_integration_files)}"
    )

    # Pre-execution event order: test.selected(scope=task_if) precedes the
    # GREEN_GATE pass verdict (selection is emitted BEFORE any selected run).
    green_verdicts = [
        e for e in segment if e["type"] == "verdict.passed" and e["payload"].get("check") == "green"
    ]
    failures = [e["payload"] for e in segment if e["type"] == "verdict.failed"]
    assert green_verdicts, f"GREEN_GATE never produced a pass verdict; failures={failures}"
    assert selection["seq"] < green_verdicts[0]["seq"], (
        "test.selected(scope=task_if) must precede the GREEN_GATE pass verdict "
        "(selection before any selected execution)"
    )

    # No FULL run / e2e execution at GREEN_GATE: SELECT_TASK never triggers
    # the FULL chain (that lives only in ISLAND_GATE_2 / FR-0253).
    assert not [
        e
        for e in segment
        if e["type"] == "full.executed" and e["seq"] < green_verdicts[0]["seq"]
    ], (
        "GREEN_GATE must not trigger a FULL run (SELECT_TASK != FULL)"
    )

    # Evidence binding (IF-EVIDENCE-001): the GREEN_GATE pass verdict binds
    # this selection identity -- the pass judgment rests on this selection's
    # execution evidence, not on a stale or unrelated identity.
    assert green_verdicts[0]["payload"].get("selection_id") == payload["selection_id"], (
        "GREEN_GATE pass verdict must bind the SELECT_TASK selection identity"
    )
    outcomes_ref = green_verdicts[0]["payload"].get("outcomes_ref")
    assert outcomes_ref
    outcomes = json.loads((host_repo / outcomes_ref).read_text(encoding="utf-8"))
    assert sorted(item["node"] for item in outcomes) == sorted(selected_nodes)
    assert all(item["status"] == "passed" for item in outcomes)

    committed = next(
        e
        for e in segment
        if e["type"] == "green.committed"
        and e["payload"].get("task_id") == task["task_id"]
    )
    assert committed["payload"].get("evidence_ids"), (
        "green.committed must expose evidence ids for REFACTOR reuse"
    )
