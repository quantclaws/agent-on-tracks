"""Behavior coverage for the M-IMPL FULL chain mixin (``MImplFullChainMixin``).

Drives FULL layer execution, eligibility/evidence binding, failed-WAL
reconciliation, ledger transitions and SELECT_DIFF/FULL_F fixed proofs
through a bare mixin host with the process/collect seams stubbed
(FR-0286 §5, B39/B91, ISLAND_GATE_2).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import m_impl_full_chain as full_chain
from tracks.executor.m_impl_full_chain import MImplFullChainMixin
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _Store:
    def __init__(self, events=(), state=None):
        self._events = list(events)
        self._state = state or State(run_id="RUN")
        self.blobs: list = []

    def events(self, run_id):
        return list(self._events)

    def state(self, run_id):
        return self._state

    def write_audit_blob(self, payload):
        self.blobs.append(payload)
        return f"blob-{len(self.blobs)}"


class _Host(MImplFullChainMixin):
    def __init__(self, tmp_path: Path, *, events=(), blob=None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _Store(events)
        self.store.blob = blob  # None means "use real write_audit_blob"
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self.passed_island: list = []

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _vdir(self) -> Path:
        return self._vdir_path

    def _waived_nodes(self, nodes):
        return set()

    def _current_baseline_digest(self) -> str:
        return "BASE"

    def _collect_all_declared_layers(self):
        return {"tests/unit/test_a.py::t1": "unit"}, None

    def _dirty_tree_stamp(self) -> str:
        return "TREE"

    def _gate_environment_identity(self) -> str:
        return "ENV"

    def _selection_command_identity(self, commands):
        return tuple(sorted(commands))

    def _result_staging_path(self, command_id, name):
        path = self.repo / ".tracks-runtime" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _pass_island_2(self, cmd, state):
        self.passed_island.append((cmd, state))


def _ev(seq, type, payload=None, command_id=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, command_id=command_id)


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="full_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# argv identity
# ---------------------------------------------------------------------------


def test_normalize_full_argv_binds_result_path():
    argv = ["pytest", "--junitxml=/tmp/r.xml", "other"]
    assert MImplFullChainMixin._normalize_full_argv(
        argv, "/tmp/r.xml"
    ) == ["pytest", "--junitxml=<result>", "other"]
    assert MImplFullChainMixin._normalize_full_argv(argv) == argv


def test_full_command_identity_is_canonical(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        full_chain, "resolve_selected_command", lambda run, nodes, result, cwd: ["pytest", result]
    )
    host = _Host(tmp_path)
    sections = {
        "unit": SimpleNamespace(cwd=".", run="pytest {result}"),
        "integration": SimpleNamespace(cwd="tests", run="pytest {result}"),
    }
    identity = host._declared_full_identity(sections, {})
    assert len(identity) == 2
    assert any('"layer":"unit"' in item for item in identity)


def test_declared_anchor_layers_reads_plan(tmp_path: Path):
    host = _Host(tmp_path)
    plan = host._vdir() / "test-plan.md"
    plan.write_text(
        "## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | unit | tests/unit/test_a.py | IF-1 |\n"
        "| AC-FR0001-02 | integration | tests/integration/test_b.py | IF-2 |\n",
        encoding="utf-8",
    )
    assert host._declared_anchor_layers() == {"unit", "integration"}
    plan.unlink()
    assert host._declared_anchor_layers() is None


# ---------------------------------------------------------------------------
# FULL layers
# ---------------------------------------------------------------------------


def test_run_full_layers_success(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    section = SimpleNamespace(run="pytest {result}", cwd=".")
    monkeypatch.setattr(
        full_chain, "resolve_selected_command", lambda *a: ["pytest", "x"]
    )
    monkeypatch.setattr(full_chain, "audit_selection_argv", lambda *a: True)
    monkeypatch.setattr(
        full_chain,
        "execute_gate_command",
        lambda command, cwd, label: SimpleNamespace(argv=["pytest", "x"], cwd=cwd, exit_code=0),
    )
    monkeypatch.setattr(full_chain, "parse_test_result", lambda path: {"n": object()})
    monkeypatch.setattr(
        full_chain,
        "require_exact_node_coverage",
        lambda cases, selected: {node: SimpleNamespace(status="passed", detail="") for node in selected},
    )
    outcomes, echo, cwds, codes, result_paths = host._run_full_layers(
        _cmd(), "FULL_1", {"unit": section}, {"n": "unit"}
    )
    assert outcomes == [{"node": "n", "status": "passed", "detail": ""}]
    assert echo == {"unit": ["pytest", "x"]}
    assert codes == {"unit": 0}
    assert "unit" in result_paths


def test_run_full_layers_audit_divergence_raises(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    section = SimpleNamespace(run="pytest {result}", cwd=".")
    monkeypatch.setattr(full_chain, "resolve_selected_command", lambda *a: ["pytest", "x"])
    monkeypatch.setattr(full_chain, "audit_selection_argv", lambda *a: False)
    with pytest.raises(full_chain.TestSelectError, match="FULL argv diverges"):
        host._run_full_layers(_cmd(), "FULL_1", {"unit": section}, {"n": "unit"})


# ---------------------------------------------------------------------------
# eligibility / evidence
# ---------------------------------------------------------------------------


def test_split_waived_failures_and_eligibility(tmp_path: Path):
    host = _Host(tmp_path)
    host._waived_nodes = lambda nodes: {"waived"}
    blocking, waived = host._split_waived_failures(
        [{"node": "waived"}, {"node": "blocked"}]
    )
    assert [f["node"] for f in blocking] == ["blocked"]
    assert waived == ["waived"]
    eligible = MImplFullChainMixin._full_f_eligible
    assert eligible([{"node": "x"}], [], {"unit": 0}, {}) is False
    assert eligible([], ["waived"], {"unit": 1}, {}) is True
    assert eligible([], [], {"unit": 0}, {}) is True
    assert eligible([], [], {"unit": 5}, {"unit": True}) is True
    assert eligible([], [], {"unit": 5}, {"unit": False}) is False


def test_full_round_sections_requires_all(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        full_chain,
        "load_contract",
        lambda repo: SimpleNamespace(unit=object(), integration=None, e2e=object()),
    )
    with pytest.raises(full_chain.TestSelectError, match="FULL requires"):
        host._full_round_sections()


def test_full_round_selection_error_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._collect_all_declared_layers = lambda: (None, "collect failed")
    with pytest.raises(full_chain.TestSelectError, match="collect failed"):
        host._full_round_selection(_cmd(), "FULL_1")

    host._collect_all_declared_layers = lambda: ({"n": "unit"}, None)
    host._current_baseline_digest = lambda: ""
    with pytest.raises(full_chain.TestSelectError, match="baseline"):
        host._full_round_selection(_cmd(), "FULL_1")

    host._current_baseline_digest = lambda: "BASE"
    monkeypatch.setattr(full_chain, "git", lambda *a, **k: SimpleNamespace(stdout="COMMIT\n"))
    monkeypatch.setattr(full_chain, "make_selection_id", lambda **k: "SEL")
    monkeypatch.setattr(full_chain, "selection_event_payload", lambda **k: {"payload": True})
    host.store.write_audit_blob = lambda payload: None
    with pytest.raises(full_chain.TestSelectError, match="nodes blob write failed"):
        host._full_round_selection(_cmd(), "FULL_1")

    host.store.write_audit_blob = lambda payload: "ref"
    selection = host._full_round_selection(_cmd("C-9"), "FULL_1")
    assert selection.selection_id == "SEL"
    assert selection.nodes == ["n"]
    assert host.emitted[0][0] == "test.selected"


def test_full_round_evidence_declared_layers_and_blob_failure(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    header = SimpleNamespace(
        tree_stamp="TREE",
        selection_id="SEL",
        state=State(run_id="RUN"),
    )
    run = SimpleNamespace(
        command_exit_codes={"unit": 5},
        command_echo={"unit": ["pytest"]},
        command_cwds={"unit": "/x"},
        result_paths={"unit": "/x/r.xml"},
        outcomes=[{"node": "n", "status": "failed", "detail": "boom"}],
    )
    host._declared_anchor_layers = lambda: None
    host._full_command_identity = lambda *a: ("cmd",)
    monkeypatch.setattr(
        full_chain,
        "evidence_by_node_for",
        lambda identity, outcomes, attempt: {o["node"]: "e1" for o in outcomes},
    )
    host.store.write_audit_blob = lambda payload: None
    with pytest.raises(full_chain.TestSelectError, match="outcomes blob write failed"):
        host._full_round_evidence(header, run)

    host.store.write_audit_blob = lambda payload: "ref"
    host._declared_anchor_layers = lambda: {"integration"}
    evidence = host._full_round_evidence(header, run)
    assert evidence.empty_pass_layers == {"unit": True}  # undeclared layer, rc5
    assert [f["node"] for f in evidence.blocking] == ["n"]
    assert evidence.outcomes_ref == "ref"


def test_finalize_full_round_payload(tmp_path: Path):
    host = _Host(tmp_path)
    header = SimpleNamespace(
        round_name="FULL_1",
        ledger={},
        selection_id="SEL",
        selection_event=SimpleNamespace(seq=3, command_id="C-3"),
        commit="COMMIT",
        tree_stamp="TREE",
        candidate_sha="SHA",
        judgment_reason="stale",
        cmd=_cmd("C-9"),
    )
    run = SimpleNamespace(
        command_echo={"unit": ["pytest"]},
        command_cwds={"unit": "/x"},
        command_exit_codes={"unit": 0},
    )
    evidence = SimpleNamespace(
        blocking=[],
        waived_nodes=[],
        evidence_by_node={"n": "e1"},
        outcomes_ref="ref",
        command_identity=("cmd",),
        env_identity="ENV",
        empty_pass_layers={},
    )
    payload = host._finalize_full_round(header, run, evidence)
    assert payload["passed"] is True
    assert payload["serves_as_full_f"] is True
    assert payload["candidate_sha"] == "SHA"
    assert payload["reason"] == "stale"
    event, event_payload, kwargs = host.emitted[0]
    assert event == "full.executed"
    assert "failures" not in event_payload and "evidence_by_node" not in event_payload
    assert kwargs == {"command_id": "C-9"}


def test_execute_full_round_composes_pieces(tmp_path: Path):
    host = _Host(tmp_path)
    calls: list = []
    host._full_round_header = lambda *a: calls.append("header") or SimpleNamespace(
        sections={"unit": SimpleNamespace()}, inventory={}
    )
    host._run_full_layers = lambda *a: ([], {}, {}, {}, {})
    host._full_round_evidence = lambda header, run: calls.append("evidence") or "EVIDENCE"
    host._finalize_full_round = lambda header, run, evidence: calls.append("finalize") or {"ok": True}
    assert host._execute_full_round(_cmd(), State(), "FULL_1", {}) == {"ok": True}
    assert calls == ["header", "evidence", "finalize"]


# ---------------------------------------------------------------------------
# failed WAL reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_full_failure_wal_error_paths(tmp_path: Path):
    host = _Host(tmp_path)
    host._read_runtime_blob = lambda ref: "not-a-list"
    with pytest.raises(full_chain.LedgerCorruptionError, match="replayable outcomes"):
        host._reconcile_full_failure_wal(_cmd(), State(), _ev(2, "full.executed", {}), {})

    host._read_runtime_blob = lambda ref: ["bad"]
    with pytest.raises(full_chain.LedgerCorruptionError, match="malformed"):
        host._reconcile_full_failure_wal(_cmd(), State(), _ev(2, "full.executed", {}), {})

    host._read_runtime_blob = lambda ref: [{"node": "n", "status": "failed"}]
    with pytest.raises(full_chain.LedgerCorruptionError, match="evidence identity"):
        host._reconcile_full_failure_wal(_cmd(), State(), _ev(2, "full.executed", {}), {})


def test_reconcile_full_failure_wal_matches_and_records(tmp_path: Path):
    host = _Host(tmp_path)
    outcomes = [{"node": "n", "status": "failed", "detail": "boom", "evidence_id": "e1"}]
    host._read_runtime_blob = lambda ref: list(outcomes)
    host.store = _Store(
        [
            _ev(1, "test.selected", {"scope": "full", "selection_id": "SEL"}),
            _ev(2, "full.executed", {"failed_nodes": ["n"], "outcomes_ref": "ref"}),
        ]
    )
    host._record_full_failures = lambda cmd, state, full, ledger: host.emitted.append(
        ("recorded", {"selection_id": full["selection_id"]}, {})
    )
    host._reconcile_full_failure_wal(_cmd(), State(), _ev(2, "full.executed", {"failed_nodes": ["n"], "outcomes_ref": "ref"}), {})
    assert host.emitted[0][1]["selection_id"] == "SEL"


def test_reconcile_full_failure_wal_mismatch_and_missing_selection(tmp_path: Path):
    host = _Host(tmp_path)
    outcomes = [{"node": "n", "status": "failed", "detail": "boom", "evidence_id": "e1"}]
    host._read_runtime_blob = lambda ref: list(outcomes)
    event = _ev(2, "full.executed", {"failed_nodes": ["other"], "outcomes_ref": "ref"})
    with pytest.raises(full_chain.LedgerCorruptionError, match="disagrees"):
        host._reconcile_full_failure_wal(_cmd(), State(), event, {})

    event = _ev(2, "full.executed", {"failed_nodes": ["n"], "outcomes_ref": "ref"})
    host.store = _Store([_ev(1, "test.selected", {"scope": "delta"})])
    with pytest.raises(full_chain.LedgerCorruptionError, match="preceding selection"):
        host._reconcile_full_failure_wal(_cmd(), State(), event, {})


def test_wal_failed_nodes_match_waived_semantics():
    match = MImplFullChainMixin._wal_failed_nodes_match
    failures = [{"node": "a"}, {"node": "w"}]
    assert match({"failed_nodes": ["a"]}, failures) is False  # legacy: full set expected
    assert match({"failed_nodes": ["a", "w"]}, failures) is True
    assert match({"failed_nodes": ["a"], "waived_nodes": ["w"]}, failures) is True
    assert match({"failed_nodes": ["x"], "waived_nodes": ["w"]}, failures) is False


def test_record_full_failures_transitions(tmp_path: Path):
    host = _Host(tmp_path)
    failure = {"node": "n", "status": "failed", "detail": "boom"}
    failure["failure_signature"] = MImplFullChainMixin._full_failure_signature(failure)

    host._record_full_failures(
        _cmd("C-1"),
        State(current_attempt=1),
        {"selection_id": "SEL", "failures": [dict(failure)], "evidence_by_node": {"n": "e1"}},
        {},
    )
    event, payload, _ = host.emitted[0]
    assert event == "ledger.opened"
    assert payload["state"] == "OPEN"
    assert payload["selection_id"] == "SEL"
    assert payload["evidence_id"] == "e1"

    host2 = _Host(tmp_path)
    ledger = {f'["n", "{failure["failure_signature"]}"]': "PROVEN"}
    host2._record_full_failures(
        _cmd("C-2"),
        State(current_attempt=0),
        {"selection_id": "SEL", "failures": [dict(failure)], "evidence_by_node": {}},
        ledger,
    )
    assert host2.emitted[0][1]["from"] == "PROVEN"
    assert host2.emitted[0][1]["to"] == "OPEN"

    host3 = _Host(tmp_path)
    host3._waived_nodes = lambda nodes: {"n"}
    host3._record_full_failures(
        _cmd(), State(), {"selection_id": "SEL", "failures": [dict(failure)], "evidence_by_node": {}}, {}
    )
    assert host3.emitted == []


def test_full_failure_owner_resolution(tmp_path: Path):
    host = _Host(tmp_path, events=[_ev(1, "task.started", {"task_id": "T-1", "manifest": {"m": 1}}), _ev(2, "red.checkpointed", {"task_id": "T-1", "r_sha": "R"})])
    state = State(
        run_id="RUN",
        task_refs=[{"task_id": "T-1", "acceptance_refs": ["tests/integration/test_a.py::t1"]}],
    )
    owner = host._full_failure_owner("tests/integration/test_a.py::t1", state)
    assert owner["task_id"] == "T-1"
    assert owner["manifest"] == {"m": 1}
    assert owner["r_sha"] == "R"

    single = State(run_id="RUN", task_refs=[{"task_id": "T-9"}])
    assert host._full_failure_owner("tests/integration/unknown.py::t1", single)["task_id"] == "T-9"

    ambiguous = State(
        run_id="RUN",
        task_refs=[
            {"task_id": "T-1", "acceptance_refs": ["tests/integration/test_a.py::t1"]},
            {"task_id": "T-2", "acceptance_refs": ["tests/integration/test_a.py::t1"]},
        ],
    )
    assert host._full_failure_owner("tests/integration/test_a.py::t1", ambiguous) == {}


def test_transition_full_ledger(tmp_path: Path):
    host = _Host(tmp_path, events=[])
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(full_chain, "rebuild_ledger", lambda events: {})
    assert host._transition_full_ledger(_cmd(), "CLASSIFIED", "FIXED", "r", "devon") is False

    host.store = _Store(
        [_ev(1, "ledger.opened", {"node": "n", "failure_signature": "sig", "task_id": "T-1"})]
    )
    monkeypatch.setattr(full_chain, "rebuild_ledger", lambda events: {'["n", "sig"]': "CLASSIFIED"})
    assert host._transition_full_ledger(_cmd("C-7"), "CLASSIFIED", "FIXED", "r", "devon") is True
    monkeypatch.undo()
    payload = host.emitted[0][1]
    assert payload["from"] == "CLASSIFIED" and payload["to"] == "FIXED"
    assert payload["task_id"] == "T-1"


def test_fixed_ledger_key_and_last_done_paths(tmp_path: Path):
    host = _Host(tmp_path)
    with pytest.raises(full_chain.LedgerCorruptionError):
        host._fixed_ledger_key({})
    assert host._fixed_ledger_key({'["n", "s"]': "FIXED"}) == '["n", "s"]'
    host.store = _Store(
        [
            _ev(1, "outcome.received", {"role": "devon", "status": "done", "changed_paths": ["a"]}),
            _ev(2, "outcome.received", {"role": "devon", "status": "failed"}),
        ]
    )
    assert host._last_done_outcome_paths() == ["a"]


def test_diff_test_reach_filters_tests_paths(tmp_path: Path):
    inventory = {"tests/unit/test_a.py::t1": "unit", "src/app.py": "unit"}
    assert MImplFullChainMixin._diff_test_reach(inventory, "tests/unit/test_a.py") == {
        "tests/unit/test_a.py::t1"
    }
    assert MImplFullChainMixin._diff_test_reach(inventory, "src/app.py") == set()


# ---------------------------------------------------------------------------
# fixed proofs
# ---------------------------------------------------------------------------


def _proof_host(tmp_path: Path):
    host = _Host(tmp_path)
    host._fixed_ledger_key = lambda ledger: '["n", "sig"]'
    host._last_done_outcome_paths = lambda: ["tests/unit/test_a.py"]
    host._collect_all_declared_layers = lambda: ({"tests/unit/test_a.py::t1": "unit"}, None)
    host._prove_fixed_via_diff = lambda *a: host.emitted.append(("via-diff", {}, {}))
    host._prove_fixed_via_fallback = lambda *a: host.emitted.append(("via-fallback", {}, {}))
    host._execute_diff_selection = lambda *a: {"failures": [], "outcomes_ref": "ref"}
    host._record_full_failures = lambda *a: None
    host._execute_full_round = lambda *a: {"failed_nodes": [], "outcomes_ref": "ref", "passed": True}
    return host


def test_prove_fixed_ledger_entries_routes(tmp_path: Path, monkeypatch):
    host = _proof_host(tmp_path)
    selection = SimpleNamespace(reliable=True, nodes={"n1"}, basis="b")
    monkeypatch.setattr(full_chain, "select_diff", lambda *a: selection)
    host._prove_fixed_ledger_entries(_cmd(), State(), {'["n", "sig"]': "FIXED"})
    assert host.emitted[0][0] == "via-diff"

    host2 = _proof_host(tmp_path)
    monkeypatch.setattr(full_chain, "select_diff", lambda *a: SimpleNamespace(reliable=False))
    host2._prove_fixed_ledger_entries(_cmd(), State(), {'["n", "sig"]': "FIXED"})
    assert host2.emitted[0][0] == "via-fallback"

    host3 = _proof_host(tmp_path)
    host3._collect_all_declared_layers = lambda: (None, "collect boom")
    with pytest.raises(full_chain.TestSelectError, match="collect boom"):
        host3._prove_fixed_ledger_entries(_cmd(), State(), {'["n", "sig"]': "FIXED"})


def test_prove_fixed_via_diff_proven_and_failed(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._execute_diff_selection = lambda *a: {
        "failures": [{"node": "n", "failure_signature": "sig"}],
        "outcomes_ref": "ref",
    }
    host._record_full_failures = lambda *a: None
    monkeypatch.setattr(full_chain, "rebuild_ledger", lambda events: {})
    monkeypatch.setattr(full_chain, "ledger_is_clean", lambda ledger: False)
    host._prove_fixed_via_diff(
        _cmd("C-1"), State(current_attempt=1), "n", "sig", SimpleNamespace(), {}, {}
    )
    events = [e[0] for e in host.emitted]
    assert events[0] == "ledger.transitioned"
    assert host.emitted[0][1]["to"] == "OPEN"
    assert "verdict.failed" in events


def test_prove_fixed_via_diff_clean_and_final_pass(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._execute_diff_selection = lambda *a: {"failures": [], "outcomes_ref": "ref"}
    host._record_full_failures = lambda *a: None
    host._execute_full_round = lambda *a: {"failed_nodes": [], "outcomes_ref": "ref", "passed": True}
    monkeypatch.setattr(full_chain, "rebuild_ledger", lambda events: {})
    monkeypatch.setattr(full_chain, "ledger_is_clean", lambda ledger: True)
    host._prove_fixed_via_diff(
        _cmd("C-1"), State(), "n", "sig", SimpleNamespace(), {}, {}
    )
    assert host.emitted[0][1]["to"] == "PROVEN"
    assert host.passed_island  # final FULL_F passed -> ISLAND_GATE_2

    host2 = _Host(tmp_path)
    host2._execute_diff_selection = lambda *a: {"failures": [], "outcomes_ref": "ref"}
    host2._record_full_failures = lambda *a: None
    host2._execute_full_round = lambda *a: {
        "failed_nodes": ["n"],
        "outcomes_ref": "ref",
        "passed": False,
    }
    monkeypatch.setattr(full_chain, "ledger_is_clean", lambda ledger: True)
    host2._prove_fixed_via_diff(_cmd("C-2"), State(), "n", "sig", SimpleNamespace(), {}, {})
    assert host2.emitted[-1][0] == "verdict.failed"
    assert host2.emitted[-1][1]["reason"].startswith("FULL_F revealed")


def test_prove_fixed_via_fallback_transitions(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._record_full_failures = lambda *a: None
    host._execute_full_round = lambda *a: {
        "failures": [{"node": "n", "failure_signature": "sig"}],
        "outcomes_ref": "ref",
        "passed": False,
    }
    monkeypatch.setattr(full_chain, "rebuild_ledger", lambda events: {})
    monkeypatch.setattr(full_chain, "ledger_is_clean", lambda ledger: True)
    ledger = {'["n", "sig"]': "FIXED"}
    host._prove_fixed_via_fallback(_cmd("C-1"), State(current_attempt=0), ledger)
    assert host.emitted[0][1]["to"] == "OPEN"
    assert host.emitted[-1][0] == "verdict.failed"

    host2 = _Host(tmp_path)
    host2._record_full_failures = lambda *a: None
    host2._execute_full_round = lambda *a: {
        "failures": [],
        "outcomes_ref": "ref",
        "passed": True,
    }
    host2._prove_fixed_via_fallback(_cmd("C-2"), State(), ledger)
    assert host2.emitted[0][1]["to"] == "PROVEN"
    assert host2.passed_island


# ---------------------------------------------------------------------------
# SELECT_DIFF layers / selection
# ---------------------------------------------------------------------------


def test_run_diff_layers_success_and_missing_contract(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    section = SimpleNamespace(run_selected="pytest {nodes} {result}", cwd=".")
    monkeypatch.setattr(full_chain, "stage_audited_selection", lambda *a: ["pytest", "x"])
    monkeypatch.setattr(
        full_chain,
        "execute_gate_command",
        lambda command, cwd, label: SimpleNamespace(argv=["pytest", "x"], exit_code=0),
    )
    monkeypatch.setattr(full_chain, "parse_test_result", lambda path: {"n": object()})
    monkeypatch.setattr(
        full_chain,
        "require_exact_node_coverage",
        lambda cases, selected: {node: SimpleNamespace(status="failed", detail="d") for node in selected},
    )
    outcomes, commands = host._run_diff_layers(
        _cmd(), ["n"], {"unit": section}, {"n": "unit"}
    )
    assert outcomes == [{"node": "n", "status": "failed", "detail": "d"}]
    assert commands == {"unit": ["pytest", "x"]}

    with pytest.raises(full_chain.TestSelectError, match="lacks \\[unit\\]"):
        host._run_diff_layers(_cmd(), ["n"], {"unit": None}, {"n": "unit"})


def test_execute_diff_selection_error_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        full_chain,
        "load_contract",
        lambda repo: SimpleNamespace(unit=object(), integration=object(), e2e=object()),
    )
    host._current_baseline_digest = lambda: ""
    with pytest.raises(full_chain.TestSelectError, match="frozen baseline"):
        host._execute_diff_selection(
            _cmd(), State(), "n", "sig", SimpleNamespace(nodes={"n"}, basis="b"), {"n": "unit"}
        )

    host._current_baseline_digest = lambda: "BASE"
    with pytest.raises(full_chain.TestSelectError, match="absent from full collect"):
        host._execute_diff_selection(
            _cmd(), State(), "n", "sig", SimpleNamespace(nodes={"missing"}, basis="b"), {"n": "unit"}
        )

    monkeypatch.setattr(full_chain, "git", lambda *a, **k: SimpleNamespace(stdout="COMMIT\n"))
    monkeypatch.setattr(full_chain, "make_selection_id", lambda **k: "SEL")
    host.store.write_audit_blob = lambda payload: None
    with pytest.raises(full_chain.TestSelectError, match="nodes blob write failed"):
        host._execute_diff_selection(
            _cmd(), State(), "n", "sig", SimpleNamespace(nodes={"n"}, basis="b"), {"n": "unit"}
        )


def test_execute_diff_selection_success(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        full_chain,
        "load_contract",
        lambda repo: SimpleNamespace(unit=object(), integration=object(), e2e=object()),
    )
    monkeypatch.setattr(full_chain, "git", lambda *a, **k: SimpleNamespace(stdout="COMMIT\n"))
    monkeypatch.setattr(full_chain, "make_selection_id", lambda **k: "SEL")
    host.store.write_audit_blob = lambda payload: "ref"
    host._run_diff_layers = lambda *a: (
        [{"node": "n", "status": "failed", "detail": "boom"}],
        {"unit": ["pytest"]},
    )
    monkeypatch.setattr(
        full_chain,
        "evidence_by_node_for",
        lambda identity, outcomes, attempt: {o["node"]: "e1" for o in outcomes},
    )
    state = State(run_id="RUN", current_task_id="T-1", current_attempt=1)
    result = host._execute_diff_selection(
        _cmd("C-1"), state, "n", "sig", SimpleNamespace(nodes={"n"}, basis="b"), {"n": "unit"}
    )
    assert result["failed_nodes"] == ["n"]
    assert result["evidence_by_node"] == {"n": "e1"}
    assert result["outcomes_ref"] == ".tracks/runtime/blobs/ref"
    selected = host.emitted[0]
    assert selected[0] == "test.selected"
    assert selected[1]["ledger_node"] == "n"
    assert selected[1]["failure_signature"] == "sig"
    assert selected[1]["task_id"] == "T-1"


def test_signed_failures_only_failed_statuses(tmp_path: Path):
    host = _Host(tmp_path)
    outcomes = [
        {"node": "a", "status": "failed", "detail": "x"},
        {"node": "b", "status": "passed", "detail": ""},
        {"node": "c", "status": "error", "detail": "e"},
    ]
    failures = host._signed_failures(outcomes)
    assert [f["node"] for f in failures] == ["a", "c"]
    assert all(f["failure_signature"] for f in failures)


def test_full_failure_signature_is_stable():
    a = {"node": "n", "status": "failed", "detail": "d"}
    b = {"node": "n", "status": "failed", "detail": "d"}
    assert MImplFullChainMixin._full_failure_signature(a) == MImplFullChainMixin._full_failure_signature(b)
    assert MImplFullChainMixin._full_failure_signature(a) != MImplFullChainMixin._full_failure_signature(
        {"node": "n", "status": "failed", "detail": "other"}
    )
