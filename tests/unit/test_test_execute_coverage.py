"""Behavior coverage for the M-TEST execution mixin (``ExecTestRunMixin``).

Drives RED_CHECK selection/WAL integrity, the collect-blindness screens,
layer execution classification, trace closure and test freezing directly on
a bare mixin host with the external seams stubbed (IF-TESTSEL, FR-0250-02,
FRB-G, FR-0286).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git as _git
from tests.unit.helpers import git_repo
from tracks.executor import test_execute
from tracks.executor.test_execute import ExecTestRunMixin
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _FakeStore:
    def __init__(self, events=(), home=None):
        self._events = list(events)
        self.home = home or Path("/tmp")

    def events(self, run_id):
        return list(self._events)

    def write_audit_blob(self, payload):
        return "BLOB"

    def state(self, run_id):
        return State(run_id=run_id)


class _Host(ExecTestRunMixin):
    def __init__(self, tmp_path: Path, *, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.backend = None
        self.store = _FakeStore(events)
        self.store.home = self.repo / ".tracks"
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))


def _ev(seq, type, payload=None, command_id=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, command_id=command_id)


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="test_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# RED_CHECK selection errors
# ---------------------------------------------------------------------------


def test_red_check_selection_empty_r2_emits_failure(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        test_execute, "load_contract", lambda repo: SimpleNamespace()
    )
    monkeypatch.setattr(test_execute, "_contract_sections", lambda contract: [("unit", SimpleNamespace())])
    host._selection_context = lambda: ("BASE", {"unit": []}, [])
    monkeypatch.setattr(
        test_execute,
        "require_nonempty_r2_selection",
        lambda *a, **k: (_ for _ in ()).throw(test_execute.EmptyR2SelectionError("empty")),
    )
    host._has_persisted_unit_only_increment = lambda: False
    host._emit_empty_r2_failure = lambda cmd, state: host.emitted.append(("empty_r2", {}, {}))
    assert host._red_check_selection(_cmd(), State()) is None
    assert host.emitted[0][0] == "empty_r2"


def test_red_check_selection_contract_and_select_errors(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "load_contract", lambda repo: SimpleNamespace())
    monkeypatch.setattr(test_execute, "_contract_sections", lambda contract: [])
    host._selection_context = lambda: ("BASE", {}, [])
    host._has_persisted_unit_only_increment = lambda: False
    host._emit_contract_error_red = lambda cmd, state, reason: host.emitted.append(("contract_error", {"reason": reason}, {}))

    def _raise_contract(*a, **k):
        raise test_execute.ContractError("bad contract")

    monkeypatch.setattr(test_execute, "require_nonempty_r2_selection", _raise_contract)
    assert host._red_check_selection(_cmd(), State()) is None
    assert host.emitted[0][1]["reason"].startswith("contract error:")

    host2 = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "load_contract", lambda repo: SimpleNamespace())
    monkeypatch.setattr(test_execute, "_contract_sections", lambda contract: [])
    host2._selection_context = lambda: ("BASE", {}, [])
    host2._has_persisted_unit_only_increment = lambda: False
    host2._emit_contract_error_red = lambda cmd, state, reason: host2.emitted.append(("contract_error", {"reason": reason}, {}))
    monkeypatch.setattr(
        test_execute,
        "require_nonempty_r2_selection",
        lambda *a, **k: (_ for _ in ()).throw(test_execute.TestSelectError("no select")),
    )
    assert host2._red_check_selection(_cmd(), State()) is None
    assert host2.emitted[0][1]["reason"] == "no select"


# ---------------------------------------------------------------------------
# stale selection identity (FA-5)
# ---------------------------------------------------------------------------


def test_stale_selection_fields_detects_each_divergence():
    fields = ExecTestRunMixin._stale_selection_fields
    assert fields(None, "S", "T", "B", ["n"]) == []
    persisted = {"selection_id": "S", "tree_stamp": "T", "baseline": "B", "nodes": ["n"]}
    assert fields(persisted, "S", "T", "B", ["n"]) == []
    assert fields(persisted, "S2", "T", "B", ["n"]) == ["selection_id"]
    assert fields(persisted, "S", "T2", "B", ["n"]) == ["tree_stamp"]
    assert fields(persisted, "S", "T", "B2", ["n"]) == ["baseline"]
    assert fields(persisted, "S", "T", "B", ["n", "m"]) == ["nodes"]


def test_emit_stale_selection_failure_payload(tmp_path: Path):
    host = _Host(tmp_path)
    host._red_log_blob_ref = lambda payload: "blob-ref"
    host._emit_stale_selection_failure(
        _cmd("C-9"),
        State(current_attempt=1),
        {"selection_id": "old", "tree_stamp": "T0", "baseline": "B0", "nodes": ["a"]},
        ["selection_id", "nodes"],
        "new",
        "T1",
        "B1",
        ["b"],
    )
    event, payload, kwargs = host.emitted[0]
    assert event == "verdict.failed"
    assert kwargs == {"command_id": "C-9"}
    assert payload["check"] == "stale"
    assert payload["target_stage"] == "M-TEST"
    assert payload["artifact_disposition"] == "rollback"
    assert payload["evidence"]["stale_fields"] == ["selection_id", "nodes"]
    assert payload["evidence"]["persisted"]["selection_id"] == "old"
    assert payload["evidence"]["recomputed"]["nodes"] == ["b"]
    assert payload["attempt"] == 2
    assert payload["log_ref"] == "blob-ref"


# ---------------------------------------------------------------------------
# selection node persistence / emission
# ---------------------------------------------------------------------------


def test_persist_selection_nodes_failure_fails_closed(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = SimpleNamespace(write_audit_blob=lambda payload: None)
    host._emit_contract_error_red = lambda cmd, state, reason: host.emitted.append(("contract_error", {"reason": reason}, {}))
    assert host._persist_selection_nodes(_cmd(), State(), {"unit": ["n"]}, ["n"]) is None
    assert "blob write failed" in host.emitted[0][1]["reason"]


def test_persist_selection_nodes_maps_node_to_layer(tmp_path: Path):
    host = _Host(tmp_path)

    def _write(payload):
        host.written = payload
        return "ref1"

    host.store = SimpleNamespace(write_audit_blob=_write)
    blob = host._persist_selection_nodes(_cmd(), State(), {"unit": ["a"], "integration": ["b"]}, ["a", "b"])
    assert blob == ".tracks/runtime/blobs/ref1"
    assert host.written == [{"node": "a", "layer": "unit"}, {"node": "b", "layer": "integration"}]


def test_emit_unit_only_increment_valid_emits_red_valid(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_unit_only_increment_valid(_cmd("C-2"), "SEL", "blob")
    event, payload, kwargs = host.emitted[0]
    assert event == "red.validated"
    assert payload["status"] == "valid"
    assert payload["basis"] == "unit-only hotfix increment"
    assert kwargs == {"command_id": "C-2"}


def test_emit_test_selected_uses_event_payload(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        test_execute,
        "selection_event_payload",
        lambda **kwargs: {"built": kwargs},
    )
    monkeypatch.setattr(test_execute, "_R2_SCOPE", "r2_delta")
    monkeypatch.setattr(test_execute, "_R2_BASIS", "pre_write")
    host._emit_test_selected(_cmd("C-3"), "BASE", ["a"], "blob", "COMMIT", "TREE", "SEL")
    payload = host.emitted[0][1]["built"]
    assert payload["nodes"] == ["a"]
    assert payload["selection_id"] == "SEL"


# ---------------------------------------------------------------------------
# collect-blindness screens
# ---------------------------------------------------------------------------


def _collected(count, per_node_blob=None, status=None):
    payload = {"collected_count": count}
    if per_node_blob is not None:
        payload["per_node_blob"] = per_node_blob
    if status is not None:
        payload["status"] = status
    return _ev(1, "test.collected", payload)


def test_total_blindness_reason_requires_zero_baseline_and_modules(tmp_path: Path):
    host = _Host(tmp_path)
    (host.repo / "tests" / "unit").mkdir(parents=True)
    (host.repo / "tests" / "unit" / "test_x.py").write_text("", encoding="utf-8")
    reason = host._total_blindness_reason(
        _collected(0), _ev(0, "test.baseline_captured", {"nodes_count": 0}), ["tests/unit"]
    )
    assert "collect pipeline blindness" in reason
    assert host._total_blindness_reason(None, None, ["tests/unit"]) is None
    assert host._total_blindness_reason(_collected(1), _ev(0, "b", {"nodes_count": 0}), ["tests/unit"]) is None
    assert host._total_blindness_reason(_collected(0), _ev(0, "b", {"nodes_count": 3}), ["tests/unit"]) is None
    assert host._total_blindness_reason(_collected(0), _ev(0, "b", {"nodes_count": 0}), []) is None


def test_new_file_blindness_reason_detects_unseen_modules(tmp_path: Path):
    host = _Host(tmp_path)
    collected = _collected(1, per_node_blob="b")
    written = _ev(1, "test.written", {"result_id": "R", "commit_sha": "COMMIT"})
    host._latest_event = lambda t, **k: written if t == "test.written" else collected
    host._checkpoint_base_sha = lambda result_id: "BASE"
    host._changed_test_modules = lambda base, commit, layers: ["tests/unit/test_new.py"]
    host._read_runtime_blob = lambda ref: [{"node": "tests/unit/test_old.py::test_old"}]
    reason = host._new_file_blindness_reason(collected, ["tests/unit"])
    assert "collect pipeline blindness" in reason
    assert "tests/unit/test_new.py" in reason


def test_new_file_blindness_reason_guards(tmp_path: Path):
    host = _Host(tmp_path)
    host._latest_event = lambda t, **k: None
    assert host._new_file_blindness_reason(None, ["tests/unit"]) is None
    collected = _collected(1, per_node_blob="b")
    host._latest_event = lambda t, **k: _ev(1, "test.written", {"result_id": "R", "commit_sha": "C"})
    host._checkpoint_base_sha = lambda result_id: "BASE"
    host._changed_test_modules = lambda base, commit, layers: []
    assert host._new_file_blindness_reason(collected, ["tests/unit"]) is None
    host._changed_test_modules = lambda base, commit, layers: ["tests/unit/test_new.py"]

    def _raise(ref):
        raise test_execute.TestSelectError("unreadable")

    host._read_runtime_blob = _raise
    assert host._new_file_blindness_reason(collected, ["tests/unit"]) is None
    host._read_runtime_blob = lambda ref: [{"node": "tests/unit/test_new.py::t"}]
    assert host._new_file_blindness_reason(collected, ["tests/unit"]) is None
    host._latest_event = lambda t, **k: None
    assert host._new_file_blindness_reason(collected, ["tests/unit"]) is None


def test_checkpoint_base_sha_reads_checkpoint_event(tmp_path: Path):
    host = _Host(
        tmp_path,
        events=[
            _ev(1, "result.checkpointed", {"result_id": "R1", "base_sha": "BASE"}),
            _ev(2, "result.checkpointed", {"result_id": "R2"}),
        ],
    )
    assert host._checkpoint_base_sha("R1") == "BASE"
    assert host._checkpoint_base_sha("R2") is None
    assert host._checkpoint_base_sha("R3") is None


def test_changed_test_modules_filters_layers(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)

    def _git(repo, *args, **kwargs):
        return SimpleNamespace(stdout="tests/unit/test_a.py\nsrc/app.py\ntests/integration/test_b.py\n")

    monkeypatch.setattr(test_execute, "git", _git)
    assert host._changed_test_modules("B", "C", ["tests/unit"]) == ["tests/unit/test_a.py"]
    assert host._changed_test_modules("B", "C", ["tests/none"]) == []


def test_collected_prefixes_returns_file_prefixes(tmp_path: Path):
    host = _Host(tmp_path)
    host._read_runtime_blob = lambda ref: [
        {"node": "tests/unit/test_a.py::test_1"},
        {"node": "tests/unit/test_a.py::test_2"},
    ]
    assert host._collected_prefixes(_collected(2, per_node_blob="b")) == {"tests/unit/test_a.py"}


def test_declared_layer_paths_reads_contract_sections(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "load_contract", lambda repo: SimpleNamespace())
    monkeypatch.setattr(
        test_execute,
        "_contract_sections",
        lambda contract: [
            ("unit", SimpleNamespace(paths=["tests/unit"])),
            ("integration", None),
            ("legacy", {"paths": ["tests/integration", 3, ""]}),
        ],
    )
    assert host._declared_layer_paths() == ["tests/unit", "tests/integration"]


def test_declared_layer_paths_contract_error_yields_empty(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)

    def _boom(repo):
        raise test_execute.ContractError("no contract")

    monkeypatch.setattr(test_execute, "load_contract", _boom)
    assert host._declared_layer_paths() == []


# ---------------------------------------------------------------------------
# empty-R2 screens / discharge
# ---------------------------------------------------------------------------


def test_emit_empty_r2_failure_collect_defect_wins(tmp_path: Path):
    host = _Host(tmp_path)
    host._collect_defect_evidence = lambda: "blind"
    host._r2_discharge_evidence = lambda: "discharged"
    host._emit_empty_r2_failure(_cmd("C-1"), State())
    event, payload, _ = host.emitted[0]
    assert event == "verdict.failed"
    assert payload["check"] == "collect_defect"
    assert payload["reason"] == "blind"
    assert payload["target_stage"] == "M-TEST"


def test_emit_empty_r2_failure_discharge(tmp_path: Path):
    host = _Host(tmp_path)
    host._collect_defect_evidence = lambda: None
    host._r2_discharge_evidence = lambda: "already committed"
    host._emit_empty_r2_failure(_cmd("C-1"), State())
    event, payload, _ = host.emitted[0]
    assert event == "verdict.passed"
    assert payload["check"] == "r2_discharged"


def test_emit_empty_r2_failure_default(tmp_path: Path):
    host = _Host(tmp_path)
    host._collect_defect_evidence = lambda: None
    host._r2_discharge_evidence = lambda: None
    host._emit_empty_r2_failure(_cmd("C-1"), State(current_attempt=4))
    event, payload, _ = host.emitted[0]
    assert event == "verdict.failed"
    assert payload["check"] == "empty_r2"
    assert payload["attempt"] == 5


def test_r2_discharge_evidence_requires_all_conditions(tmp_path: Path):
    host = _Host(tmp_path)
    host._retry_cutoff_seq = lambda: 5
    events = [
        _ev(6, "no_diff.reviewed", {"verdict": "pass"}),
        _ev(7, "test.committed", {"commit_sha": "abcdef1234567890"}),
        _ev(8, "test.collected", {"status": "passed", "collected_count": 4}),
    ]
    host.store = _FakeStore(events)

    def _latest(t, **k):
        return next(
            (
                e
                for e in reversed(host.store.events("RUN"))
                if e.type == t and (not k.get("status") or e.payload.get("status") == k["status"])
            ),
            None,
        )

    host._latest_event = _latest
    detail = host._r2_discharge_evidence()
    assert "increment already committed (abcdef123456)" in detail
    assert "collected (4 nodes)" in detail

    host.store = _FakeStore([events[1], events[2]])
    assert host._r2_discharge_evidence() is None  # no accepted no-diff
    host.store = _FakeStore([events[0], events[1], _ev(8, "test.collected", {"status": "passed", "collected_count": 0})])
    assert host._r2_discharge_evidence() is None


# ---------------------------------------------------------------------------
# red-check execution
# ---------------------------------------------------------------------------


def test_execute_red_check_outcome_blob_failure_fails_closed(tmp_path: Path):
    host = _Host(tmp_path)
    host._execute_selected_layers = lambda *a: ([], [], True, None)
    host.store = SimpleNamespace(write_audit_blob=lambda payload: None)
    host._emit_contract_error_red = lambda cmd, state, reason: host.emitted.append(("contract_error", {"reason": reason}, {}))
    host._execute_red_check(_cmd(), State(), [], {}, "SEL", "blob")
    assert "outcomes evidence blob write failed" in host.emitted[0][1]["reason"]


def test_execute_red_check_emits_valid_then_invalid(tmp_path: Path):
    host = _Host(tmp_path)
    host._execute_selected_layers = lambda *a: ([], [], True, None)
    host.store = SimpleNamespace(write_audit_blob=lambda payload: "ref")
    host._execute_red_check(_cmd(), State(), [], {}, "SEL", "blob")
    assert host.emitted[0][0] == "red.validated"
    assert host.emitted[0][1]["status"] == "valid"
    assert host.emitted[0][1]["outcomes_ref"] == ".tracks/runtime/blobs/ref"

    host2 = _Host(tmp_path)
    host2._execute_selected_layers = lambda *a: ([], [{"classification": "impl_defect"}], False, None)
    host2.store = SimpleNamespace(write_audit_blob=lambda payload: "ref")
    captured: list = []
    host2._emit_red_check_invalid = lambda *args: captured.append(args)
    host2._execute_red_check(_cmd(), State(), [], {}, "SEL", "blob")
    assert len(captured) == 1


def test_execute_red_check_layer_error_routes_contract_error(tmp_path: Path):
    host = _Host(tmp_path)
    host._execute_selected_layers = lambda *a: ([], [], False, "TestResultError: bad xml")
    host._emit_contract_error_red = lambda cmd, state, reason: host.emitted.append(("contract_error", {"reason": reason}, {}))
    host._execute_red_check(_cmd(), State(), [], {}, "SEL", "blob")
    assert host.emitted[0][1]["reason"] == "TestResultError: bad xml"


def test_execute_selected_layers_cleans_up_and_reports_error(tmp_path: Path):
    host = _Host(tmp_path)
    host._oob_accepted_test_files = lambda: set()
    cleaned: list = []
    host._cleanup_staged_results = lambda staged: cleaned.append(list(staged))
    section = SimpleNamespace()

    def _raise(*args):
        raise test_execute.TestResultError("missing result")

    host._run_layer_command = _raise
    outcomes, findings, all_legit, error = host._execute_selected_layers(
        _cmd(), [("unit", section)], {"unit": ["tests/unit/test_a.py::t"]}, {}
    )
    assert outcomes == [] and findings == []
    assert all_legit is False
    assert error.startswith("TestResultError:")
    assert cleaned == [[]]


def test_execute_selected_layers_skips_empty_layers(tmp_path: Path):
    host = _Host(tmp_path)
    host._oob_accepted_test_files = lambda: set()
    host._cleanup_staged_results = lambda staged: None
    called: list = []
    host._run_layer_command = lambda *a: called.append(a) or {}
    outcomes, _findings, all_legit, error = host._execute_selected_layers(
        _cmd(), [("unit", SimpleNamespace())], {"unit": []}, {}
    )
    assert (outcomes, all_legit, error) == ([], True, None)
    assert called == []


def test_record_layer_outcomes_classifies_and_detects_oob():
    mapping = {
        "tests/unit/test_a.py::t_fail": SimpleNamespace(status="failed", detail="AssertionError: nope"),
        "tests/unit/test_a.py::t_pass": SimpleNamespace(status="passed", detail=""),
        "tests/unit/test_a.py::t_skip": SimpleNamespace(status="skipped", detail=""),
    }
    outcomes: list = []
    findings: list = []
    legit = ExecTestRunMixin._record_layer_outcomes(
        list(mapping), mapping, outcomes, findings, {"tests/unit/test_a.py"}
    )
    assert legit is True  # OOB-declared green-on-arrival is legal (oob_verified)
    by_node = {o["node"]: o["classification"] for o in outcomes}
    assert by_node["tests/unit/test_a.py::t_fail"] in ("assertion_failure", "impl_defect", "test_defect")
    assert by_node["tests/unit/test_a.py::t_pass"] == "oob_verified"
    assert by_node["tests/unit/test_a.py::t_skip"] == "oob_verified"
    assert len(findings) == 3


def test_emit_red_check_invalid_payload_shape(tmp_path: Path):
    host = _Host(tmp_path)
    host._red_log_blob_ref = lambda payload: "log-ref"
    host._diagnose_classification = lambda: "test_defect"
    findings = [{"classification": "test_defect", "test_id": "n", "detail": "d"}]
    host._emit_red_check_invalid(
        _cmd("C-5"), State(current_attempt=2), findings, {}, [], "SEL", "blob", ".tracks/out"
    )
    event, payload, kwargs = host.emitted[0]
    assert event == "red.validated" and payload["status"] == "invalid"
    event2, payload2, _ = host.emitted[1]
    assert event2 == "verdict.failed"
    assert payload2["artifact_disposition"] == "rewrite"
    assert payload2["check"] == "test_defect"
    assert payload2["attempt"] == 3


def test_emit_red_check_invalid_defaults_to_rollback(tmp_path: Path):
    host = _Host(tmp_path)
    host._red_log_blob_ref = lambda payload: "log-ref"
    host._diagnose_classification = lambda: "impl_defect"
    host._emit_red_check_invalid(
        _cmd(), State(), [], {}, [], "SEL", "blob", ".tracks/out"
    )
    assert host.emitted[1][1]["artifact_disposition"] == "rollback"
    assert host.emitted[1][1]["reason"] == "invalid"


# ---------------------------------------------------------------------------
# trace closure
# ---------------------------------------------------------------------------


def test_do_check_trace_reconcile_and_emit(tmp_path: Path):
    host = _Host(tmp_path)
    host._vdir = lambda: tmp_path / "vdir"
    (tmp_path / "vdir").mkdir()
    host._trace_report = lambda cmd, state, vdir, tests: ("REPORT", ["blocked"])
    host._emit_trace_verdict = lambda cmd, state, report, blocking: host.emitted.append(
        ("trace", {"report": report, "blocking": blocking}, {})
    )
    host._do_check_trace(_cmd(), State(trace_passed=True), "T", reconcile=True)
    assert host.emitted == []
    host._do_check_trace(_cmd(), State(), "T", reconcile=False)
    assert host.emitted[0][1] == {"report": "REPORT", "blocking": ["blocked"]}


def test_trace_report_plain_required_filter(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    vdir = tmp_path / "vdir"
    vdir.mkdir()
    (vdir / "acceptance.md").write_text("AC-FR0001-01", encoding="utf-8")
    (vdir / "test-plan.md").write_text("plan", encoding="utf-8")
    report = SimpleNamespace(hard_errors=["AC-FR0001-01 missing", "other error"])
    monkeypatch.setattr("tracks.checks.trace.check_trace_full_file", lambda v, t: report)
    monkeypatch.setattr(test_execute, "required_ac_ids", lambda acc, plan: {"AC-FR0001-01"})
    result, blocking = host._trace_report(_cmd(), SimpleNamespace(), vdir, host.repo / "tests")
    assert result is report
    assert blocking == ["AC-FR0001-01 missing"]

    monkeypatch.setattr(test_execute, "required_ac_ids", lambda acc, plan: set())
    _result, blocking = host._trace_report(_cmd(), SimpleNamespace(), vdir, host.repo / "tests")
    assert blocking == list(report.hard_errors)


def test_trace_report_hotfix_emits_increment_declared(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    vdir = tmp_path / "vdir"
    vdir.mkdir()
    (vdir / "test-plan.md").write_text("plan", encoding="utf-8")
    report = SimpleNamespace(status="pass", hard_errors=[])
    monkeypatch.setattr("tracks.checks.trace.check_trace_full_file", lambda v, t, hotfix_ctx=None: report)
    monkeypatch.setattr("tracks.executor.test_tasks.parse_hotfix_unit_rows", lambda text: [{"row": 1}])
    state = State(hotfix_anchor_acs=["AC-FR0001-01@v0.5"])
    result, blocking = host._trace_report(_cmd(hotfix=True), state, vdir, host.repo / "tests")
    assert result is report and blocking == []
    assert host.emitted[0][0] == "increment.declared"
    assert host.emitted[0][1]["shield"] == "empty"
    assert host.emitted[0][1]["unit_rows"] == [{"row": 1}]


def test_trace_report_anchored_hotfix_uses_declared_rows(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    vdir = tmp_path / "vdir"
    vdir.mkdir()
    host.store = _FakeStore(
        [_ev(1, "increment.declared", {"unit_rows": [{"row": 9}]})]
    )
    captured = {}

    def _check(v, t, hotfix_ctx=None):
        captured.update(hotfix_ctx)
        return SimpleNamespace(status="pass", hard_errors=[])

    monkeypatch.setattr("tracks.checks.trace.check_trace_full_file", _check)
    state = State(hotfix_anchor_acs=["AC-FR0001-01@v0.5"])
    result, blocking = host._trace_report(_cmd(), state, vdir, host.repo / "tests")
    assert result.status == "pass"
    assert captured["declared_unit_rows"] == [{"row": 9}]
    assert captured["anchor_acs"] == ["AC-FR0001-01@v0.5"]
    assert blocking == []


def test_emit_trace_verdict_pass_and_fail(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_trace_verdict(_cmd("C-1"), State(), object(), [])
    assert host.emitted[0][0] == "verdict.passed"
    host._emit_trace_verdict(_cmd("C-2"), State(current_attempt=1), object(), ["a", "b"])
    event, payload, _ = host.emitted[1]
    assert event == "verdict.failed"
    assert payload["reason"] == "a; b"
    assert payload["attempt"] == 2


# ---------------------------------------------------------------------------
# commit tests
# ---------------------------------------------------------------------------


def test_do_commit_tests_reconcile_short_circuits(tmp_path: Path):
    host = _Host(tmp_path)
    host._do_commit_tests(_cmd(), State(test_committed=True), "T", reconcile=True)
    assert host.emitted == []


def test_do_commit_tests_residue_fails_closed(tmp_path: Path):
    repo = git_repo(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_residue.py").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "tests/test_residue.py")
    _git(repo, "commit", "-m", "seed")
    (repo / "tests" / "test_residue.py").write_text("dirty\n", encoding="utf-8")
    host = _Host(tmp_path)
    host.repo = repo
    host._do_commit_tests(_cmd("C-4"), State(current_attempt=1), "T", reconcile=False)
    event, payload, _ = host.emitted[0]
    assert event == "verdict.failed"
    assert payload["check"] == "test_freeze_contamination"
    assert "tests/test_residue.py" in payload["evidence"]
    assert payload["attempt"] == 2


def test_do_commit_tests_commit_failure_and_success(tmp_path: Path, monkeypatch):
    repo = git_repo(tmp_path)
    (repo / "tests").mkdir()
    host = _Host(tmp_path)
    host.repo = repo
    monkeypatch.setattr(test_execute, "_scoped_commit_if_staged", lambda *a, **k: SimpleNamespace(returncode=1, stderr="rejected", stdout=""))
    host._emit_commit_failure = lambda proc, state, command_id: host.emitted.append(
        ("commit_failure", {"command_id": command_id}, {})
    )
    host._do_commit_tests(_cmd("C-8"), State(), "T", reconcile=False)
    assert host.emitted[0][0] == "commit_failure"

    host2 = _Host(tmp_path)
    host2.repo = repo
    monkeypatch.setattr(test_execute, "_scoped_commit_if_staged", lambda *a, **k: None)
    monkeypatch.setattr(test_execute, "emit_test_committed", lambda facade, cmd, tests_dir: host2.emitted.append(("committed", {}, {})))
    host2._do_commit_tests(_cmd("C-9"), State(), "T", reconcile=False)
    assert host2.emitted[0][0] == "committed"


# ---------------------------------------------------------------------------
# diagnose classification
# ---------------------------------------------------------------------------


def test_diagnose_classification_token_handling(tmp_path: Path):
    host = _Host(tmp_path)
    assert host._diagnose_classification() == "test_defect"
    host.backend = SimpleNamespace(token=lambda role, key, default: "impl_defect")
    assert host._diagnose_classification() == "impl_defect"
    host.backend = SimpleNamespace(token=lambda role, key, default: "bogus")
    assert host._diagnose_classification() == "test_defect"


# ---------------------------------------------------------------------------
# _do_run_tests orchestration + layer command
# ---------------------------------------------------------------------------


def _selection(*, selected_all=("n1",), selected_by_layer=None):
    return SimpleNamespace(
        contract=SimpleNamespace(),
        sections=[("unit", SimpleNamespace())],
        baseline_id="BASE",
        selected_by_layer=selected_by_layer or {"unit": list(selected_all)},
        selected_all=list(selected_all),
    )


def test_do_run_tests_reconcile_and_selection_none(tmp_path: Path):
    host = _Host(tmp_path)
    host._do_run_tests(_cmd(), State(red_validated=True), "T", reconcile=True)
    assert host.emitted == []
    host._red_check_selection = lambda cmd, state: None
    host._do_run_tests(_cmd(), State(), "T", reconcile=False)
    assert host.emitted == []


def test_do_run_tests_empty_selection_uses_unit_only_increment(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "make_selection_id", lambda **k: "SEL")
    host._red_check_selection = lambda cmd, state: _selection(selected_all=(), selected_by_layer={})
    host._dirty_tree_stamp = lambda: "TREE"
    host._persist_selection_nodes = lambda *a: "nodes-blob"
    host._emit_test_selected = lambda *a: host.emitted.append(("selected", {}, {}))
    host._emit_unit_only_increment_valid = lambda *a: host.emitted.append(("unit-only", {}, {}))
    host._do_run_tests(_cmd(), State(), "T", reconcile=False)
    assert [e[0] for e in host.emitted] == ["selected", "unit-only"]


def test_do_run_tests_stale_and_blob_failure_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "make_selection_id", lambda **k: "SEL")
    host._red_check_selection = lambda cmd, state: _selection()
    host._dirty_tree_stamp = lambda: "TREE"
    host._stale_selection_fields = lambda *a: ["tree_stamp"]
    host._emit_stale_selection_failure = lambda *a: host.emitted.append(("stale", {}, {}))
    host._do_run_tests(_cmd(), State(), "T", reconcile=False)
    assert [e[0] for e in host.emitted] == ["stale"]

    host2 = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "make_selection_id", lambda **k: "SEL")
    host2._red_check_selection = lambda cmd, state: _selection()
    host2._dirty_tree_stamp = lambda: "TREE"
    host2._persist_selection_nodes = lambda *a: None
    host2._do_run_tests(_cmd(), State(), "T", reconcile=False)
    assert host2.emitted == []


def test_do_run_tests_executes_red_check(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(test_execute, "make_selection_id", lambda **k: "SEL")
    host._red_check_selection = lambda cmd, state: _selection()
    host._dirty_tree_stamp = lambda: "TREE"
    host._persist_selection_nodes = lambda *a: "nodes-blob"
    host._emit_test_selected = lambda *a: None
    host._execute_red_check = lambda *a: host.emitted.append(("executed", {}, {}))
    host._do_run_tests(_cmd(), State(), "T", reconcile=False)
    assert [e[0] for e in host.emitted] == ["executed"]


def test_collect_defect_evidence_combines_signatures(tmp_path: Path):
    host = _Host(tmp_path)
    host._declared_layer_paths = lambda: ["tests/unit"]
    host._latest_event = lambda t, **k: None
    assert host._collect_defect_evidence() is None
    host._latest_event = lambda t, **k: _ev(1, t, {"collected_count": 0, "nodes_count": 0})
    host._total_blindness_reason = lambda c, b, p: "blind-signature"
    assert host._collect_defect_evidence() == "blind-signature"
    host._total_blindness_reason = lambda c, b, p: None
    host._new_file_blindness_reason = lambda c, p: "new-file-signature"
    assert host._collect_defect_evidence() == "new-file-signature"


def test_run_layer_command_success_and_audit_divergence(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._result_staging_path = lambda command_id, name: tmp_path / f"{name}.xml"
    section = SimpleNamespace(run_selected="pytest {nodes} --junitxml={result}", cwd=".")
    logs: dict = {}
    monkeypatch.setattr(test_execute, "resolve_selected_command", lambda *a: ["pytest", "n"])
    monkeypatch.setattr(test_execute, "audit_selection_argv", lambda *a: True)
    monkeypatch.setattr(
        test_execute.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="out", stderr="err"),
    )
    case = SimpleNamespace(status="failed", detail="boom")
    monkeypatch.setattr(test_execute, "parse_test_result", lambda path: {"n": case})
    monkeypatch.setattr(test_execute, "require_exact_node_coverage", lambda cases, nodes: cases)
    mapping = host._run_layer_command(_cmd("C-1"), "unit", section, ["n"], [tmp_path / "x"], logs)
    assert mapping == {"n": case}
    assert logs["unit"]["returncode"] == 1
    assert logs["unit"]["command_echo"] == ["pytest", "n"]

    monkeypatch.setattr(test_execute, "audit_selection_argv", lambda *a: False)
    with pytest.raises(test_execute.TestSelectError, match="diverges"):
        host._run_layer_command(_cmd("C-1"), "unit", section, ["n"], [], {})
