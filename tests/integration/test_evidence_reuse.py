"""Integration coverage for D-41 GREEN evidence reuse in REFACTOR_GATE."""

import json

from tests.integration.v05_contract_helpers import (
    first_m_impl_attempt_segment,
    run_m_impl_journey,
)
from tracks.executor.executor import Executor
from tracks.store import Store


# AC-FR0252-01@v0.6 TRACKS-TRACE unchanged refactor reuses green evidence
def test_refactor_identity_unchanged_reuses_green(trac, event_log):
    run_id, _result, events = run_m_impl_journey(trac, event_log)
    segment = first_m_impl_attempt_segment(events)
    task = next(e for e in segment if e["type"] == "task.started")["payload"]["task"]
    task_id = task["task_id"]
    green = next(
        e
        for e in segment
        if e["type"] == "green.committed" and e["payload"].get("task_id") == task_id
    )
    assert green["payload"].get("evidence_ids")
    no_change_rows = [
        e
        for e in segment
        if e["type"] == "refactor.no_change" and e["payload"].get("task_id") == task_id
    ]
    failures = [e["payload"] for e in segment if e["type"] == "verdict.failed"]
    assert no_change_rows, f"unchanged refactor did not converge; failures={failures}"
    no_change = no_change_rows[0]
    reused = [
        e
        for e in segment
        if e["type"] == "evidence.reused"
        and e["payload"].get("kind") == "green"
        and e["payload"].get("task_id") == task_id
    ]
    assert reused, "unchanged REFACTOR_GATE did not emit evidence.reused(kind=green)"
    payload = reused[0]["payload"]
    assert payload.get("reused_evidence_ids") == green["payload"]["evidence_ids"]
    assert payload.get("consumer_gate") == "REFACTOR_GATE"
    identity_basis = payload.get("identity_basis")
    assert set(identity_basis or {}) == {"tree", "command", "env", "selection_id"}
    assert identity_basis == green["payload"]["identity_basis"]
    assert identity_basis["tree"]
    assert identity_basis["command"]
    assert all(isinstance(item, str) for item in identity_basis["command"])
    assert identity_basis["env"]
    assert identity_basis["selection_id"]
    assert green["seq"] < reused[0]["seq"] < no_change["seq"]

    # Reuse means no second task_if selection/execution after Green commit.
    assert not [
        e
        for e in segment
        if green["seq"] < e["seq"] < no_change["seq"]
        and e["type"] == "test.selected"
        and e["payload"].get("scope") == "task_if"
        and e["payload"].get("task_id") == task_id
    ]


# AC-FR0252-02@v0.6 TRACKS-TRACE changed refactor reruns same task scope
def test_refactor_identity_changed_reruns_same_scope(trac, event_log):
    run_id, _result, events = run_m_impl_journey(
        trac, event_log, simulate="devon:REFACTOR=change"
    )
    segment = first_m_impl_attempt_segment(events)
    task = next(e for e in segment if e["type"] == "task.started")["payload"]["task"]
    task_id = task["task_id"]
    selections = [
        e
        for e in segment
        if e["type"] == "test.selected"
        and e["payload"].get("scope") == "task_if"
        and e["payload"].get("task_id") == task_id
    ]
    refactor_outcomes = [
        e["payload"]
        for e in segment
        if e["type"] == "outcome.received" and e["payload"].get("phase") == "refactor"
    ]
    reused = [e["payload"] for e in segment if e["type"] == "evidence.reused"]
    assert len(selections) >= 2, (
        "changed refactor did not rerun SELECT_TASK; "
        f"outcomes={refactor_outcomes}, reused={reused}"
    )
    green, refactor = selections[0], selections[1]
    assert refactor["payload"].get("nodes") == green["payload"].get("nodes")
    assert refactor["payload"].get("task_ifs") == green["payload"].get("task_ifs")
    assert refactor["payload"].get("selection_id") != green["payload"].get("selection_id")
    stale = [
        e
        for e in segment
        if e["type"] == "evidence.staled"
        and any(
            target.get("ref") == green["payload"].get("selection_id")
            for target in e["payload"].get("targets", [])
        )
    ]
    assert stale, "changed REFACTOR_GATE did not stale the prior Green selection"
    assert not [
        e
        for e in segment
        if green["seq"] < e["seq"] < refactor["seq"]
        and e["type"] == "evidence.reused"
        and e["payload"].get("task_id") == task_id
    ]
    committed_rows = [
        e
        for e in segment
        if e["type"] == "refactor.committed"
        and e["payload"].get("task_id") == task_id
    ]
    failures = [e["payload"] for e in segment if e["type"] == "verdict.failed"]
    assert committed_rows, f"changed refactor was not committed; failures={failures}"
    committed = committed_rows[0]
    assert committed["payload"].get("selection_id") == refactor["payload"]["selection_id"]
    assert committed["payload"].get("evidence_ids")


# AC-FR0252-03@v0.6 TRACKS-TRACE reuse decision ignores false no-change report
def test_reuse_decision_programmatic_public_interface_route_unchanged(
    trac, event_log
):
    run_id, _result, events = run_m_impl_journey(
        trac,
        event_log,
        simulate="devon:REFACTOR=lie_no_change",
    )
    segment = first_m_impl_attempt_segment(events)
    task_id = next(e for e in segment if e["type"] == "task.started")["payload"][
        "task_id"
    ]
    assert not [
        e
        for e in segment
        if e["type"] == "evidence.reused" and e["payload"].get("task_id") == task_id
    ], "Runtime trusted a false no-change report despite candidate content drift"
    refusals = [
        e
        for e in segment
        if e["type"] == "verdict.failed"
        and e["payload"].get("task_id") == task_id
        and e["payload"].get("check") == "contract_error"
    ]
    assert refusals, "unattributed refactor drift must fail closed as contract_error"
    assert "without a captured diff" in refusals[0]["payload"].get("reason", "")


# AC-FR0252-03@v0.6 TRACKS-TRACE contract drift forbids Green evidence reuse
def test_reuse_decision_detects_run_selected_contract_drift(
    trac, event_log, host_repo, monkeypatch
):
    run_id, _result, events = run_m_impl_journey(trac, event_log)
    segment = first_m_impl_attempt_segment(events)
    task_id = next(e for e in segment if e["type"] == "task.started")["payload"][
        "task_id"
    ]
    store = Store(host_repo / ".tracks")
    executor = Executor(store, host_repo, run_id)
    green = next(
        e
        for e in segment
        if e["type"] == "green.committed" and e["payload"].get("task_id") == task_id
    )
    snapshot_path = host_repo / green["payload"]["snapshot_ref"]
    snapshot_entries = json.loads(snapshot_path.read_text(encoding="utf-8"))["entries"]
    monkeypatch.setattr(
        executor,
        "_task_content_snapshot",
        lambda _root, _rules: dict(snapshot_entries),
    )
    reusable, changed, _green = executor._green_reuse_state(task_id, str(host_repo))
    assert reusable, f"unchanged command/content identity was not reusable: {changed}"

    environment_identity = executor._gate_environment_identity
    monkeypatch.setattr(executor, "_gate_environment_identity", lambda: "drifted-env")
    reusable_after_env_drift, _changed, _green = executor._green_reuse_state(
        task_id, str(host_repo)
    )
    assert not reusable_after_env_drift, (
        "Green evidence was reused after the execution environment changed"
    )
    monkeypatch.setattr(executor, "_gate_environment_identity", environment_identity)

    contract = host_repo / ".tracks" / "projects" / "project.toml"
    lines = contract.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("run_selected"):
            quote = line[-1]
            assert quote in ("'", '"')
            lines[index] = f"{line[:-1]} --tb=long{quote}"
            break
    else:
        raise AssertionError("fixture contract has no run_selected declaration")
    contract.write_text("\n".join(lines) + "\n", encoding="utf-8")

    reusable_after_drift, _changed, _green = executor._green_reuse_state(
        task_id, str(host_repo)
    )
    assert not reusable_after_drift, (
        "Green evidence was reused after the actual run_selected contract changed"
    )
