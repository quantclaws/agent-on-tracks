"""Integration contracts for D-41 FULL-chain ledger convergence."""

import hashlib
import json

from tests.integration.v05_contract_helpers import (
    first_m_impl_attempt_segment,
    run_m_impl_journey,
)
from tests.unit.helpers import seq
from tracks.executor.executor import Executor
from tracks.executor.test_select import (
    DiffSelection,
    ledger_is_clean,
    rebuild_ledger,
    run_full_chain,
    select_diff,
)
from tracks.kernel import decide, project
from tracks.kernel.events import Command
from tracks.store import Store


def _failure_signature(node, status, detail):
    material = json.dumps(
        {"node": node, "status": status, "detail": detail},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _opened(node="tests/unit/test_a.py::test_a", signature="sig-a"):
    return {
        "type": "ledger.opened",
        "payload": {
            "node": node,
            "failure_signature": signature,
            "state": "OPEN",
            "selection_id": "selection-a",
            "evidence_id": "evidence-a",
            "reason": "assertion failed",
        },
    }


def _transition(before, after, node="tests/unit/test_a.py::test_a", signature="sig-a"):
    return {
        "type": "ledger.transitioned",
        "payload": {
            "node": node,
            "failure_signature": signature,
            "from": before,
            "to": after,
            "attempt": 1,
            "actor": "runtime",
            "reason": "integration journey",
        },
    }


# AC-FR0253-01@v0.6 TRACKS-TRACE FULL_1 opens one ledger identity per failure
def test_full1_opens_ledger_entries_per_failed_node(host_repo):
    store = Store(host_repo / ".tracks")
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    executor = Executor(store, host_repo, "RUN")
    failures = [
        {"node": "tests/unit/test_a.py::test_a", "status": "failed", "detail": "A"},
        {"node": "tests/integration/test_b.py::test_b", "status": "error", "detail": "B"},
    ]
    full = {
        "selection_id": "selection-full-1",
        "failures": failures,
        "evidence_by_node": {item["node"]: f"evidence-{index}" for index, item in enumerate(failures)},
    }
    executor._record_full_failures(
        Command("check_island_2", command_id="C-FULL-1"),
        store.state("RUN"),
        full,
        {},
    )
    opened = [event for event in store.events("RUN") if event.type == "ledger.opened"]
    assert [event.payload["node"] for event in opened] == [item["node"] for item in failures]
    assert len({event.payload["failure_signature"] for event in opened}) == 2
    assert all(event.payload["state"] == "OPEN" for event in opened)
    assert all(event.payload["selection_id"] == "selection-full-1" for event in opened)
    assert len({event.payload["evidence_id"] for event in opened}) == 2


# AC-FR0253-02@v0.6 TRACKS-TRACE existing diagnose transitions remain replayable
def test_ledger_transitions_via_existing_diagnose_paths(host_repo, monkeypatch):
    store = Store(host_repo / ".tracks")
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-IMPL"})
    store.append("RUN", "v0.5", "ledger.opened", _opened()["payload"])
    executor = Executor(store, host_repo, "RUN")
    command = Command("check_island_2", command_id="C-CONVERGE")
    assert executor._transition_full_ledger(
        command, "OPEN", "CLASSIFIED", "Prism diagnosis", "prism"
    )
    assert executor._transition_full_ledger(
        command, "CLASSIFIED", "FIXED", "Shield repair", "shield"
    )

    def clean_fallback(cmd, state, round_name, ledger):
        executor._emit(
            "full.executed",
            {
                "round": round_name,
                "suite": ["unit", "integration", "e2e"],
                "passed": True,
                "failed_nodes": [],
                "command_echo": {},
                "evidence_ids": [],
                "serves_as_full_f": True,
                "outcomes_ref": "results.json",
                "gate": "ISLAND_GATE_2",
            },
            command_id=cmd.command_id,
        )
        return {
            "passed": True,
            "failed_nodes": [],
            "failures": [],
            "outcomes_ref": "results.json",
        }

    monkeypatch.setattr(executor, "_execute_full_round", clean_fallback)
    monkeypatch.setattr(executor, "_collect_all_declared_layers", lambda: ({}, None))
    ledger = rebuild_ledger(store.events("RUN"))
    executor._prove_fixed_ledger_entries(command, store.state("RUN"), ledger)
    events = list(store.events("RUN"))
    rebuilt = rebuild_ledger(events)
    assert list(rebuilt.values()) == ["PROVEN"]
    assert ledger_is_clean(rebuilt)
    transitions = [event.payload for event in events if event.type == "ledger.transitioned"]
    assert [(item["from"], item["to"]) for item in transitions] == [
        ("OPEN", "CLASSIFIED"),
        ("CLASSIFIED", "FIXED"),
        ("FIXED", "PROVEN"),
    ]
    assert any(
        event.type == "verdict.passed" and event.payload.get("check") == "island_2"
        for event in events
    )


# AC-FR0253-03@v0.6 TRACKS-TRACE SELECT_DIFF proves one FIXED entry
def test_select_diff_proves_fixed_entries_green():
    entry = {"node": "tests/unit/test_a.py::test_a", "state": "FIXED"}
    selection = select_diff(
        entry,
        ["tracks/a.py"],
        lambda _path: {"tests/integration/test_a.py::test_if_a"},
    )
    assert selection == DiffSelection(
        nodes=["tests/integration/test_a.py::test_if_a", entry["node"]],
        reliable=True,
        basis="repair:tracks/a.py",
    )


# AC-FR0253-04@v0.6 TRACKS-TRACE unreliable SELECT_DIFF falls back to FULL
def test_unprovable_diff_falls_back_to_full_serving_fullf():
    rounds = []
    full_results = iter(
        [
            {
                "passed": False,
                "failed_nodes": ["node-a"],
                "failures": [{"node": "node-a", "failure_signature": "sig-a"}],
            },
            {"passed": True, "failed_nodes": [], "serves_as_full_f": True},
        ]
    )

    def execute(round_name):
        rounds.append(round_name)
        return next(full_results)

    assert run_full_chain(
        execute,
        lambda _nodes: None,
        lambda _node: True,
        lambda _entry: DiffSelection(["node-a"], False, "unreliable"),
        lambda _entry, _selection: True,
    ) == "exited"
    assert rounds == ["FULL_1", "fallback_full"]


# AC-FR0253-05@v0.6 TRACKS-TRACE FULL_F loops until clean without chain cap
def test_fullf_new_failures_loop_until_clean_no_cap():
    rounds = []
    def failed(*nodes):
        return {
            "passed": False,
            "failed_nodes": list(nodes),
            "failures": [
                {"node": node, "failure_signature": f"signature-{node}"}
                for node in nodes
            ],
        }

    results = iter(
        [
            failed("node-a"),
            failed("node-a", "node-b"),
            failed("node-b", "node-c"),
            {"passed": True, "failed_nodes": []},
        ]
    )
    opened = []
    assert run_full_chain(
        lambda round_name: rounds.append(round_name) or next(results),
        lambda nodes: opened.extend(nodes),
        lambda _node: True,
        lambda entry: DiffSelection([entry["node"]], True, "reliable"),
        lambda _entry, _selection: True,
    ) == "exited"
    assert rounds == ["FULL_1", "FULL_F", "FULL_F", "FULL_F"]
    assert [entry["node"] for entry in opened] == ["node-a", "node-b", "node-c"]


# AC-FR0253-06@v0.6 TRACKS-TRACE FULL_F reopens a proven identical signature
def test_fullf_reobserved_signature_reopens_proven_entry(host_repo):
    node = "tests/unit/test_a.py::test_a"
    failure = {"node": node, "status": "failed", "detail": "same failure"}
    signature = _failure_signature(**failure)
    store = Store(host_repo / ".tracks")
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    store.append("RUN", "v0.5", "ledger.opened", _opened(node, signature)["payload"])
    for before, after in (("OPEN", "CLASSIFIED"), ("CLASSIFIED", "FIXED"), ("FIXED", "PROVEN")):
        store.append("RUN", "v0.5", "ledger.transitioned", _transition(before, after, node, signature)["payload"])
    executor = Executor(store, host_repo, "RUN")
    ledger = rebuild_ledger(store.events("RUN"))
    executor._record_full_failures(
        Command("check_island_2", command_id="C-FULL-F"),
        store.state("RUN"),
        {
            "selection_id": "selection-full-f",
            "failures": [failure],
            "evidence_by_node": {node: "evidence-full-f"},
        },
        ledger,
    )
    events = list(store.events("RUN"))
    assert len([event for event in events if event.type == "ledger.opened"]) == 1
    assert events[-1].type == "ledger.transitioned"
    assert events[-1].payload["from"] == "PROVEN"
    assert events[-1].payload["to"] == "OPEN"
    assert list(rebuild_ledger(events).values()) == ["OPEN"]


# AC-NFR0120-01@v0.6 TRACKS-TRACE ledger WAL rebuild is crash-identical
def test_ledger_wal_rebuild_after_crash_identical(host_repo):
    before_crash = [_opened(), _transition("OPEN", "CLASSIFIED")]
    first = rebuild_ledger(before_crash)
    replayed = rebuild_ledger(json.loads(json.dumps(before_crash)))
    assert replayed == first
    resumed = rebuild_ledger([*before_crash, _transition("CLASSIFIED", "FIXED")])
    assert list(resumed.values()) == ["FIXED"]

    store = Store(host_repo / ".tracks")
    store.append("RUN", "v0.5", "story.requested", {"raw_chars": 1})
    store.append(
        "RUN",
        "v0.5",
        "test.selected",
        {"scope": "full", "selection_id": "selection-crash"},
    )
    outcome_ref = store.write_audit_blob(
        [
            {
                "node": "tests/unit/test_a.py::test_a",
                "status": "failed",
                "detail": "crash-window failure",
                "evidence_id": "evidence-crash",
            }
        ]
    )
    full_event = store.append(
        "RUN",
        "v0.5",
        "full.executed",
        {
            "round": "FULL_1",
            "passed": False,
            "failed_nodes": ["tests/unit/test_a.py::test_a"],
            "outcomes_ref": f".tracks/runtime/blobs/{outcome_ref}",
        },
    )
    executor = Executor(store, host_repo, "RUN")
    command = Command("check_island_2", command_id="C-REPLAY")
    executor._reconcile_full_failure_wal(
        command, store.state("RUN"), full_event, {}
    )
    rebuilt_after_crash = rebuild_ledger(store.events("RUN"))
    assert list(rebuilt_after_crash.values()) == ["OPEN"]
    executor._reconcile_full_failure_wal(
        command, store.state("RUN"), full_event, rebuilt_after_crash
    )
    assert len([event for event in store.events("RUN") if event.type == "ledger.opened"]) == 1


# AC-NFR0120-02@v0.6 TRACKS-TRACE dirty ledger blocks M-IMPL exit
def test_dirty_ledger_fail_closed_no_exit():
    state = project(
        seq(
            ("story.requested", {"raw_chars": 1}),
            ("stage.entered", {"stage": "M-IMPL"}),
            ("full.executed", {"round": "FULL_1", "passed": False}),
            ("ledger.opened", _opened()["payload"]),
            ("verdict.passed", {"check": "island_2"}),
        )
    )
    assert state.substate == "EXIT"
    assert state.ledger_open == 1
    assert decide(state) is None


# AC-FR0254-01@v0.6 TRACKS-TRACE dormant M-VERIFY cannot bypass local gates
def test_mverify_reuse_dormant_no_bypass_rerun(trac, event_log):
    run_id, _result, events = run_m_impl_journey(trac, event_log)
    assert not [
        event
        for event in events
        if event["type"] == "stage.entered" and event["payload"].get("stage") == "M-VERIFY"
    ]
    assert not [
        event
        for event in events
        if event["type"] == "evidence.reused" and event["payload"].get("kind") == "full_f"
    ]
    assert [event for event in events if event["type"] == "full.executed"]
    assert run_id


# AC-FR0254-02@v0.6 TRACKS-TRACE FULL executes only at ISLAND_GATE_2
def test_no_candidate_full_rerun_outside_island_gate(trac, event_log):
    _run_id, _result, events = run_m_impl_journey(trac, event_log)
    segment = first_m_impl_attempt_segment(events)
    full = [event for event in segment if event["type"] == "full.executed"]
    assert len(full) == 1
    assert full[0]["payload"]["gate"] == "ISLAND_GATE_2"
    assert full[0]["payload"]["round"] == "FULL_1"
    assert full[0]["payload"]["serves_as_full_f"] is True
