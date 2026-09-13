"""Behavior coverage for the M-VERIFY gate chain (``ExecVerifyGatesMixin``).

Drives freeze-candidate -> FULL_F reuse judgment -> host-contract load /
materialize / local-gate execution directly through a bare mixin host with
the external execution seams stubbed: identity judgment, stale re-run,
repair provenance, default-contract materialization and per-gate fail-closed
outcomes (IF-VERIFY-001/002/003, IF-HOSTCONTRACT-001).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import verify_gates
from tracks.executor.host_contract import LocalGateDecl
from tracks.executor.verify_gates import ExecVerifyGatesMixin
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _FakeStore:
    def __init__(self, events=(), state=None):
        self._events = list(events)
        self._state = state or State(run_id="RUN")
        self.blobs: dict = {}

    def events(self, run_id):
        return list(self._events)

    def state(self, run_id):
        return self._state

    def write_audit_blob(self, payload):
        ref = f"blob-{len(self.blobs)}"
        self.blobs[ref] = payload
        return ref

    def append(self, event, payload, **kwargs):
        self._events.append(_ev(len(self._events) + 1, event, payload, command_id=kwargs.get("command_id")))


class _Host(ExecVerifyGatesMixin):
    def __init__(self, tmp_path: Path):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _FakeStore()
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self.blobs: dict = {}
        self.failed_blocks: list[tuple] = []
        self.stopped: list[tuple] = []
        self.facts: dict = {}
        self.declared = None

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))
        self.store.append(event, payload or {}, **kwargs)

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _read_runtime_blob(self, ref):
        if ref not in self.blobs:
            raise verify_gates.TestSelectError(f"missing blob {ref}")
        return self.blobs[ref]

    def _waived_nodes(self, nodes):
        return set()

    def _fail_verify_block(self, cmd, candidate_sha, reason, detail):
        self.failed_blocks.append((candidate_sha, reason, detail))

    def _emit_host_contract_failure(self, cmd, candidate_sha, contract_digest):
        self.failed_blocks.append((candidate_sha, "execute_failed", contract_digest))

    def _park_repair_route(self, cmd, candidate_sha, stop_class, reason):
        self.stopped.append((candidate_sha, stop_class, reason))

    def _park_stop_reason(self, event_type):
        return f"reason:{event_type}"

    def _release_version_facts(self, state):
        return dict(self.facts)


def _ev(seq, type, payload=None, command_id=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, command_id=command_id)


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="verify_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# freeze candidate
# ---------------------------------------------------------------------------


def test_freeze_candidate_reconcile_and_missing_sha(tmp_path: Path):
    host = _Host(tmp_path)
    host._latest_event = lambda t: object() if t == "candidate.frozen" else None
    host._park_freeze_candidate = lambda cmd: "SHA"
    host._do_freeze_candidate(_cmd(), State(), "T", reconcile=True)
    assert host.issued == []

    host2 = _Host(tmp_path)
    host2._park_freeze_candidate = lambda cmd: None
    host2._do_freeze_candidate(_cmd(), State(), "T", reconcile=False)
    assert host2.issued == []


def test_freeze_candidate_hands_to_full_f_judgment(tmp_path: Path):
    host = _Host(tmp_path)
    host._park_freeze_candidate = lambda cmd: "SHA"
    host._do_freeze_candidate(_cmd(), State(), "T", reconcile=False)
    cmd, _ = host.issued[0]
    assert cmd.kind == "judge_full_f_reuse"
    assert cmd.params == {"candidate_sha": "SHA"}


# ---------------------------------------------------------------------------
# FULL_F producer judgment
# ---------------------------------------------------------------------------


NODE = "tests/unit/test_a.py::test_a"


def _selection(seq=1, command_id="C1", commit="SHA", selection_id="SEL", nodes=None):
    return _ev(
        seq,
        "test.selected",
        {
            "scope": "full",
            "nodes": [NODE] if nodes is None else nodes,
            "selection_id": selection_id,
            "commit": commit,
        },
        command_id=command_id,
    )


def _execution(
    seq=2,
    command_id="C1",
    commit="SHA",
    selection_seq=1,
    selection_id="SEL",
    passed=True,
    eligible=True,
    outcomes_ref="out1",
    identity_basis=("x",),
):
    return _ev(
        seq,
        "full.executed",
        {
            "execution_commit": commit,
            "selection_event_seq": selection_seq,
            "selection_id": selection_id,
            "selection_command_id": command_id,
            "passed": passed,
            "full_f_eligible": eligible,
            "outcomes_ref": outcomes_ref,
            "identity_basis": list(identity_basis),
        },
        command_id=command_id,
    )


def test_full_f_producer_accepts_complete_passed_execution(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = _FakeStore([_selection(), _execution()])
    host.blobs["out1"] = [
        {"node": NODE, "status": "passed", "evidence_id": "e1"},
    ]
    producer, selection = host._full_f_producer("SHA")
    assert producer is not None and producer.type == "full.executed"
    assert selection is not None and selection.seq == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evs: [evs[0], _execution(commit="OTHER")],
        lambda evs: [evs[0], _execution(passed=False)],
        lambda evs: [evs[0], _execution(eligible=False)],
        lambda evs: [evs[0], _execution(selection_seq="1")],
        lambda evs: [evs[0], _execution(selection_seq=9)],
        lambda evs: [evs[0], _execution(seq=1)],
        lambda evs: [evs[0], _execution(selection_id="OTHER")],
        lambda evs: [evs[0], _execution(identity_basis=())],
    ],
)
def test_full_f_producer_rejects_invalid_producers(tmp_path: Path, mutate):
    host = _Host(tmp_path)
    events = mutate([_selection(), _execution()])
    host.store = _FakeStore(events)
    host.blobs["out1"] = [{"node": NODE, "status": "passed", "evidence_id": "e1"}]
    assert host._full_f_producer("SHA") == (None, None)


def test_full_f_producer_unreadable_outcomes_rejected(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = _FakeStore([_selection(), _execution(outcomes_ref="missing")])
    assert host._full_f_producer("SHA") == (None, None)


def test_producer_execution_valid_rules(tmp_path: Path):
    host = _Host(tmp_path)
    outcomes = [{"node": NODE, "status": "passed", "evidence_id": "e1"}]
    assert host._producer_execution_valid(outcomes, {NODE}) is True
    assert host._producer_execution_valid("nope", {NODE}) is False
    assert host._producer_execution_valid([], {NODE}) is False
    assert host._producer_execution_valid(outcomes, set()) is False
    assert host._producer_execution_valid([{"node": NODE, "status": "passed"}], {NODE}) is False
    assert host._producer_execution_valid([{"node": NODE, "status": "failed"}], {NODE}) is False
    host._waived_nodes = lambda nodes: {NODE}
    assert host._producer_execution_valid([{"node": NODE, "status": "failed", "evidence_id": "e"}], {NODE}) is True


def test_producer_pair_valid_requires_same_execution():
    event = _execution()
    selection = _selection()
    assert ExecVerifyGatesMixin._producer_pair_valid(event, selection, event.payload) is True
    broken = dict(event.payload)
    broken["outcomes_ref"] = ""
    assert ExecVerifyGatesMixin._producer_pair_valid(event, selection, broken) is False


# ---------------------------------------------------------------------------
# current FULL identity + reuse emission
# ---------------------------------------------------------------------------


def test_current_full_f_identity_requires_sections(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        verify_gates,
        "load_contract",
        lambda repo: SimpleNamespace(unit=None, integration=None, e2e=None),
    )
    with pytest.raises(verify_gates.TestSelectError):
        host._current_full_f_identity(SimpleNamespace(payload={"basis": "b"}))


def test_current_full_f_identity_requires_collect_and_basis(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    section = SimpleNamespace()
    monkeypatch.setattr(
        verify_gates,
        "load_contract",
        lambda repo: SimpleNamespace(unit=section, integration=section, e2e=section),
    )
    host._collect_all_declared_layers = lambda: (None, "collect boom")
    with pytest.raises(verify_gates.TestSelectError, match="collect boom"):
        host._current_full_f_identity(SimpleNamespace(payload={"basis": "b"}))

    host._collect_all_declared_layers = lambda: ({"n1": "unit"}, None)
    host._current_baseline_digest = lambda: "BASE"
    host._dirty_tree_stamp = lambda: "TREE"
    monkeypatch.setattr(
        verify_gates, "git", lambda *a, **k: SimpleNamespace(stdout="COMMIT\n")
    )
    with pytest.raises(verify_gates.TestSelectError, match="basis"):
        host._current_full_f_identity(SimpleNamespace(payload={"basis": ""}))

    host._declared_full_identity = lambda sections, inventory: ("cmd",)
    host._gate_environment_identity = lambda: "ENV"
    identity = host._current_full_f_identity(SimpleNamespace(payload={"basis": "basis-1"}))
    assert identity["tree"] == "TREE"
    assert identity["command"] == ["cmd"]
    assert identity["env"] == "ENV"
    assert identity["selection_id"]


def test_full_f_judgment_inputs_without_producer_records_stale_marks(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = _FakeStore(
        [
            _ev(1, "candidate.stale", {"candidate_sha": "SHA"}),
            _ev(2, "evidence.staled", {}),
            _ev(3, "other", {}),
        ]
    )
    producer, evidence, producer_seq, stale_marks, decision = host._full_f_judgment_inputs("SHA")
    assert producer is None
    assert evidence == {}
    assert producer_seq == -1
    assert stale_marks == ("candidate.stale", "evidence.staled")
    assert decision.decision in ("reuse", "rerun")


def test_full_f_judgment_inputs_identity_failure_degrades_to_empty(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = _FakeStore([_selection(), _execution()])
    host.blobs["out1"] = [{"node": NODE, "status": "passed", "evidence_id": "e1"}]

    def _boom(selection):
        raise verify_gates.TestSelectError("no identity")

    host._current_full_f_identity = _boom
    producer, evidence, _seq, _stale, decision = host._full_f_judgment_inputs("SHA")
    assert producer is not None
    assert evidence["execution_commit"] == "SHA"
    assert decision is not None


def test_emit_full_f_reuse_is_idempotent(tmp_path: Path):
    host = _Host(tmp_path)
    producer = _execution()
    host.store = _FakeStore([producer])
    decision = SimpleNamespace(identity_basis=("a", "b"), decision="reuse", reason="current")
    assert host._emit_full_f_reuse(_cmd("C1"), "SHA", producer, decision) == "reused"
    assert host.emitted == [
        (
            "evidence.reused",
            {
                "kind": "full_f",
                "candidate_sha": "SHA",
                "identity_basis": ["a", "b"],
                "source_event_seq": producer.seq,
            },
            {"command_id": "C1"},
        )
    ]
    assert host._emit_full_f_reuse(_cmd("C1"), "SHA", producer, decision) == "reused"
    assert len(host.emitted) == 1


def test_emit_full_f_judgment_reuse_path(tmp_path: Path):
    host = _Host(tmp_path)
    producer = _execution()
    host.store = _FakeStore([producer])
    decision = SimpleNamespace(identity_basis=("a",), decision="reuse", reason="current")
    host._full_f_judgment_inputs = lambda sha: (producer, {}, producer.seq, (), decision)
    assert host._emit_full_f_judgment(_cmd("C1"), "SHA") == "reused"


def test_rerun_full_f_marks_stale_and_returns_rerun(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    producer = _execution()
    decision = SimpleNamespace(identity_basis=("a",), reason="identity drift")
    monkeypatch.setattr(verify_gates, "rebuild_ledger", lambda events: {})
    host._execute_full_round = lambda *a, **k: {"passed": True, "full_f_eligible": True}
    assert (
        host._rerun_full_f(_cmd("C1"), State(), "SHA", producer, {"stale": True}, (), decision)
        == "rerun"
    )
    assert host.emitted[0][0] == "evidence.staled"
    assert host.emitted[0][1]["reason"] == "identity drift"


def test_rerun_full_f_execution_failure_emits_failed(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    decision = SimpleNamespace(identity_basis=("a",), reason="stale")
    monkeypatch.setattr(verify_gates, "rebuild_ledger", lambda events: {})

    def _boom(*a, **k):
        raise verify_gates.TestSelectError("no full")

    host._execute_full_round = _boom
    assert host._rerun_full_f(_cmd("C1"), State(), "SHA", None, {}, (), decision) == "failed"
    assert host.emitted[0][0] == "full.executed"
    assert host.emitted[0][1]["reason"].startswith("rerun_error:")


def test_rerun_full_f_ineligible_result_fails(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    decision = SimpleNamespace(identity_basis=("a",), reason="stale")
    monkeypatch.setattr(verify_gates, "rebuild_ledger", lambda events: {})
    host._execute_full_round = lambda *a, **k: {"passed": True, "full_f_eligible": False}
    assert host._rerun_full_f(_cmd(), State(), "SHA", None, {}, (), decision) == "failed"


def test_do_judge_full_f_reuse_issues_local_gates_or_stops(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_full_f_judgment = lambda cmd, sha, state: "reused"
    host._do_judge_full_f_reuse(_cmd("C1"), State(), "T", False)
    assert host.issued[0][0].kind == "run_local_gates"
    assert host.issued[0][0].params == {"candidate_sha": ""}

    host2 = _Host(tmp_path)
    host2._emit_full_f_judgment = lambda cmd, sha, state: "failed"
    host2._do_judge_full_f_reuse(_cmd("C1", candidate_sha="SHA"), State(), "T", False)
    assert host2.issued == []


# ---------------------------------------------------------------------------
# repair provenance
# ---------------------------------------------------------------------------


def test_repair_rewalk_allowed_requires_open_round_and_trailer(tmp_path: Path):
    host = _Host(tmp_path)
    host._park_repair_budget_used = lambda: 0
    assert host._repair_rewalk_allowed("SHA") is False
    host._park_repair_budget_used = lambda: 1
    host._park_candidate_seen = lambda types, sha: True
    assert host._repair_rewalk_allowed("SHA") is False
    host._park_candidate_seen = lambda types, sha: False
    host._head_carries_repair_trailer = lambda sha: True
    assert host._repair_rewalk_allowed("SHA") is True


def test_head_carries_repair_trailer_matches_round_and_body(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host.store = _FakeStore([_ev(1, "repair.round_started", {"candidate_sha": "SHA", "round": 2})])
    monkeypatch.setattr(
        verify_gates,
        "git",
        lambda *a, **k: SimpleNamespace(stdout="fix\n\nTracks-Repair-Round: RUN/2\n"),
    )
    assert host._head_carries_repair_trailer("SHA") is True
    monkeypatch.setattr(
        verify_gates, "git", lambda *a, **k: SimpleNamespace(stdout="fix\n")
    )
    assert host._head_carries_repair_trailer("SHA") is False
    host.store = _FakeStore([])
    assert host._head_carries_repair_trailer("SHA") is False


# ---------------------------------------------------------------------------
# local-gate chain
# ---------------------------------------------------------------------------


def test_do_run_local_gates_routes_to_ci_or_park(tmp_path: Path):
    host = _Host(tmp_path)
    host._run_contract_gates = lambda cmd, sha, state, resume=False: (object(), "D")
    host._do_run_local_gates(_cmd(), State(), "T", False)
    assert host.issued[0][0].kind == "observe_ci_runs"

    host2 = _Host(tmp_path)
    host2._run_contract_gates = lambda cmd, sha, state, resume=False: (None, None)
    host2._verify_stop_class = lambda sha: "gate"
    host2._do_run_local_gates(_cmd(), State(), "T", False)
    assert host2.stopped == [("", "gate", "reason:local_gate.failed")]


def test_verify_stop_class_prefers_contract_then_gate(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = _FakeStore(
        [
            _ev(1, "host_contract.invalid", {"candidate_sha": "SHA"}),
            _ev(2, "local_gate.failed", {"candidate_sha": "SHA"}),
            _ev(3, "local_gate.failed", {"candidate_sha": "OTHER"}),
        ]
    )
    assert host._verify_stop_class("SHA") == "contract"
    host.store = _FakeStore([_ev(1, "local_gate.failed", {"candidate_sha": "SHA"})])
    assert host._verify_stop_class("SHA") == "gate"
    host.store = _FakeStore([])
    assert host._verify_stop_class("SHA") == ""


def test_load_or_default_contract_host_declared(tmp_path: Path):
    host = _Host(tmp_path)
    contract_path = host.repo.joinpath(*verify_gates.CANONICAL_CONTRACT_RELPATH)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(verify_gates._DEFAULT_HOST_CONTRACT_TOML, encoding="utf-8")
    contract, digest, source = host._load_or_default_contract(_cmd(), "SHA")
    assert contract is not None
    assert source == "host"
    assert len(digest) == 64
    assert host.failed_blocks == []


def test_load_or_default_contract_malformed_host_fails_closed(tmp_path: Path):
    host = _Host(tmp_path)
    contract_path = host.repo.joinpath(*verify_gates.CANONICAL_CONTRACT_RELPATH)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text("this is not = [ valid toml", encoding="utf-8")
    assert host._load_or_default_contract(_cmd(), "SHA") == (None, None, None)
    assert host.failed_blocks[0][1] == "missing_contract"


def test_load_or_default_contract_materializes_runtime_default(tmp_path: Path):
    host = _Host(tmp_path)
    contract, digest, source = host._load_or_default_contract(_cmd(), "SHA")
    assert contract is not None
    assert source == "runtime_default"
    materialized = host.repo / ".tracks" / "runtime" / "materialized-host-contract.toml"
    assert materialized.exists()
    assert materialized.read_text(encoding="utf-8") == verify_gates._DEFAULT_HOST_CONTRACT_TOML


def test_load_or_default_contract_default_write_failure(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)

    def _boom(path):
        raise OSError("no contract")

    monkeypatch.setattr(verify_gates, "load_host_contract", _boom)
    assert host._load_or_default_contract(_cmd(), "SHA") == (None, None, None)
    assert host.failed_blocks[0][1] == "missing_contract"


_DECLARED_CONTRACT_TOML = (
    "[host-contract]\n"
    "version = 1\n"
    'language = "python"\n'
    'toolchain = "cpython"\n'
    'install = "true"\n'
    "\n[[host-contract.local_gate]]\n"
    'kind = "quality"\n'
    'source = "command"\n'
    'command = "true"\n'
    'categories = ["quality"]\n'
    'result_channel = "exit_code"\n'
    "timeout_seconds = 60\n"
)


def _write_declared(host: _Host, body: str = _DECLARED_CONTRACT_TOML) -> Path:
    contract_path = host.repo.joinpath(*verify_gates.CANONICAL_CONTRACT_RELPATH)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(body, encoding="utf-8")
    return contract_path


def test_materialize_host_contract_declared_once(tmp_path: Path):
    """IF-HOSTCONTRACT-002: the M-DESIGN completion materializes the declared
    contract once per digest (a re-issued command never duplicates it)."""
    host = _Host(tmp_path)
    _write_declared(host)
    state = State(run_id="RUN", stage="M-DESIGN")

    host._do_materialize_host_contract(_cmd(), state, None, False)
    host._do_materialize_host_contract(_cmd("C-2"), state, None, False)

    materialized = [e for e in host.emitted if e[0] == "host_contract.materialized"]
    assert len(materialized) == 1
    payload = materialized[0][1]
    assert payload["source"] == "host"
    assert payload["stage"] == "M-DESIGN"
    assert payload["version"] == 1
    assert payload["contract_digest"]
    assert payload["contract_path"].endswith(".tracks/projects/project.toml")


def test_materialize_host_contract_invalid_fails_closed(tmp_path: Path):
    """A declared-but-invalid contract fails closed with host_contract.invalid
    (never a silent fallback to the runtime default)."""
    host = _Host(tmp_path)
    _write_declared(
        host,
        _DECLARED_CONTRACT_TOML + '\n[host-contract.bogus]\nkey = 1\n',
    )

    host._do_materialize_host_contract(
        _cmd(), State(run_id="RUN", stage="M-DESIGN"), None, False
    )

    assert [e[0] for e in host.emitted] == ["host_contract.invalid"]
    assert host.emitted[0][1]["reason"] == "malformed"
    assert not host.repo.joinpath(
        ".tracks", "runtime", "materialized-host-contract.toml"
    ).exists()


def test_materialize_host_contract_runtime_default(tmp_path: Path):
    """An undeclared host materializes the runtime default at completion."""
    host = _Host(tmp_path)

    host._do_materialize_host_contract(
        _cmd(), State(run_id="RUN", stage="M-DESIGN"), None, False
    )

    assert host.emitted[0][0] == "host_contract.materialized"
    assert host.emitted[0][1]["source"] == "runtime_default"
    assert host.emitted[0][1]["stage"] == "M-DESIGN"


def test_run_contract_gates_skips_recorded_materialization(tmp_path: Path, monkeypatch):
    """The M-VERIFY consumer never re-records a digest already materialized
    (Archer's completion record stands as the single materialization)."""
    host = _Host(tmp_path)
    host.store._events = [
        _ev(1, "host_contract.materialized", {"contract_digest": "DIGEST"})
    ]
    contract = SimpleNamespace(contract_version=1, language="py", toolchain="pip")
    host._load_or_default_contract = lambda cmd, sha: (contract, "DIGEST", "host")
    monkeypatch.setattr(verify_gates, "validate_host_contract", lambda c, r: ())
    host._execute_verify_gates = lambda *a: True

    assert host._run_contract_gates(_cmd(), "SHA", State()) == (contract, "DIGEST")

    assert not [e for e in host.emitted if e[0] == "host_contract.materialized"]


def test_run_contract_gates_happy_path_and_resume(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    contract = SimpleNamespace(contract_version=1, language="py", toolchain="pip")
    host._load_or_default_contract = lambda cmd, sha: (contract, "DIGEST", "host")
    monkeypatch.setattr(verify_gates, "validate_host_contract", lambda contract, repo: ())
    host._execute_verify_gates = lambda *a: True
    assert host._run_contract_gates(_cmd(), "SHA", State()) == (contract, "DIGEST")
    assert host.emitted[0][0] == "host_contract.materialized"
    assert host.emitted[0][1]["source"] == "host"

    host2 = _Host(tmp_path)
    host2._load_or_default_contract = lambda cmd, sha: (contract, "DIGEST", "host")
    monkeypatch.setattr(verify_gates, "validate_host_contract", lambda c, r: ())
    monkeypatch.setattr(verify_gates, "has_complete_passed_gates", lambda *a: True)
    assert host2._run_contract_gates(_cmd(), "SHA", State(), resume=True) == (contract, "DIGEST")
    assert host2.emitted == []


def test_run_contract_gates_fail_closed_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._load_or_default_contract = lambda cmd, sha: (None, None, None)
    assert host._run_contract_gates(_cmd(), "SHA", State()) == (None, None)

    host2 = _Host(tmp_path)
    contract = SimpleNamespace(contract_version=1, language="py", toolchain="pip")
    host2._load_or_default_contract = lambda cmd, sha: (contract, "DIGEST", "host")
    monkeypatch.setattr(verify_gates, "validate_host_contract", lambda c, r: ("bad table",))
    assert host2._run_contract_gates(_cmd(), "SHA", State()) == (None, None)
    assert host2.failed_blocks[0][1] == "malformed"

    host3 = _Host(tmp_path)
    host3._load_or_default_contract = lambda cmd, sha: (contract, "DIGEST", "host")
    monkeypatch.setattr(verify_gates, "validate_host_contract", lambda c, r: ())
    host3._execute_verify_gates = lambda *a: False
    assert host3._run_contract_gates(_cmd(), "SHA", State()) == (None, None)
    assert host3.failed_blocks[0][1] == "execute_failed"


def test_execute_verify_gates_order_and_scope(tmp_path: Path):
    host = _Host(tmp_path)
    gates = [SimpleNamespace(kind="a"), SimpleNamespace(kind="b")]
    contract = SimpleNamespace(local_gates=gates)
    seen: list = []
    host._run_one_local_gate = lambda *args: seen.append(args[5]) or (args[5] == 0)
    host._release_version_facts = lambda state: {"version": "v1"}
    assert host._execute_verify_gates(_cmd(), "SHA", "D", contract, State()) is False
    assert seen == [0, 1]


def test_run_one_local_gate_registry_without_version_fails_closed(tmp_path: Path):
    host = _Host(tmp_path)
    gate = SimpleNamespace(command="", source="guard_registry", kind="quality")
    assert host._run_one_local_gate(_cmd(), "SHA", "D", None, State(), 0, gate, {}) is False
    assert host.emitted[0][0] == "local_gate.failed"
    assert host.emitted[0][1]["reason"] == "unknown"
    assert "guard_registry" in host.emitted[0][1]["detail"]


def test_run_one_local_gate_registry_resolves_active_version(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host.store.home = host.repo / ".tracks"
    captured = {}

    def _execute(gate, repo, architecture, scope):
        captured["architecture"] = architecture
        return SimpleNamespace(
            status="passed", command_echo=(), exit_code=0, summary={}
        )

    monkeypatch.setattr(verify_gates, "execute_registry_gate", _execute)
    gate = LocalGateDecl("quality", "guard_registry", "", ("lint_format",), "exit_code", 1)
    assert host._run_one_local_gate(
        _cmd(), "SHA", "D", None, State(version="v0.8"), 0, gate, {}
    ) is True
    assert captured["architecture"] == host.store.home / "projects/v0.8/architecture.md"
    assert host.emitted[0][0] == "local_gate.passed"


def test_run_one_local_gate_version_decl_delegates(tmp_path: Path):
    host = _Host(tmp_path)
    calls: list = []
    host._execute_version_decl_gate = lambda *args: calls.append(args) or True
    gate = SimpleNamespace(command="", source="version_decl", kind="release")
    assert host._run_one_local_gate(_cmd(), "SHA", "D", SimpleNamespace(), State(), 0, gate, {}) is True
    assert len(calls) == 1


def test_run_one_local_gate_exec_error_and_failed_status(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    gate = LocalGateDecl(
        kind="quality",
        source="inline",
        command="false",
        categories=(),
        result_channel="exit_code",
        timeout_seconds=1,
    )

    def _boom(gate_arg, repo, scope):
        raise OSError("missing binary")

    monkeypatch.setattr(verify_gates, "execute_gate", _boom)
    assert host._run_one_local_gate(_cmd(), "SHA", "D", SimpleNamespace(), State(), 0, gate, {}) is False
    assert host.emitted[0][1]["detail"] == "missing binary"

    host2 = _Host(tmp_path)
    monkeypatch.setattr(
        verify_gates,
        "execute_gate",
        lambda gate_arg, repo, scope: SimpleNamespace(
            status="malformed", command_echo=(), exit_code=None, summary={"x": 1}
        ),
    )
    assert host2._run_one_local_gate(_cmd(), "SHA", "D", SimpleNamespace(), State(), 1, gate, {}) is False
    assert host2.emitted[0][0] == "local_gate.failed"
    assert host2.emitted[0][1]["reason"] == "malformed"


def test_emit_gate_exec_error_payload_shape(tmp_path: Path):
    host = _Host(tmp_path)
    gate = SimpleNamespace(command="boom", source="inline", kind="security")
    host._emit_gate_exec_error(_cmd("C-7"), gate, 3, "SHA", "D", ValueError("bad"))
    event, payload, kwargs = host.emitted[0]
    assert event == "local_gate.failed"
    assert kwargs == {"command_id": "C-7"}
    assert payload["gate_identity"] == "security[3]"
    assert payload["candidate_sha"] == "SHA"
    assert payload["contract_digest"] == "D"
    assert payload["normalized_result"]["status"] == "failed"
    assert payload["reason"] == "unknown"
    assert payload["detail"] == "bad"
    assert payload["command_echo"] == []
