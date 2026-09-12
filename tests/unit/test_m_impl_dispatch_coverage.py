"""Behavior coverage for ``MImplDispatchMixin``: green-gate routing (evidence,
lineage, oscillation), task-review lineage failures, selection identity
persistence, forensics capture and stale-selection propagation.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import m_impl_dispatch as dispatch
from tracks.executor.m_impl_dispatch import (
    MImplDispatchMixin,
    TaskSelectionFailure,
    _TaskSelection,
    _TaskSelectionEvidence,
)
from tracks.executor.test_select import TestSelectError


class _Cmd:
    command_id = "CMD-DISPATCH"
    params: dict = {}


class _Store:
    def __init__(self, events=(), *, blob="blob-1", payloads=None):
        self.events_list = list(events)
        self.blob = blob
        self.payloads = payloads or {}
        self.written: list = []

    def events(self, _run_id):
        return list(self.events_list)

    def write_audit_blob(self, payload):
        self.written.append(payload)
        return self.blob

    def load_payload(self, payload):
        return self.payloads.get(payload.get("$ref"))


class _Host(MImplDispatchMixin):
    def __init__(self, tmp_path: Path, *, events=(), blob="blob-1", payloads=None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _Store(events, blob=blob, payloads=payloads)
        self.emitted: list = []
        self._task = SimpleNamespace(task_id="T-001", if_ids=("IF-IMPL-001",))
        self._evidence_error = None
        self._selection_error: Exception | None = None
        self._lint = (None, "")
        self._oscillation = None

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _emit_gate_failure(self, cmd, **kwargs):
        self.emitted.append(("gate_failed", kwargs))

    def _rebuild_task_log_projection(self):
        return None

    def _devon_evidence_error(self, phase, state):
        return self._evidence_error

    def _ensure_gate_worktree(self, state, phase=None):
        return "cwd", "handle"

    def _green_regression_tests(self, r_sha, state):
        return []

    def _lookup_task(self, task_id):
        return self._task

    def _execute_task_selection(self, cmd, state, cwd, task):
        if self._selection_error is not None:
            raise self._selection_error
        return {"selection_id": "sel"}

    def _run_declared_lint(self, targets):
        return self._lint

    def _lint_targets(self, phase):
        return []

    def _current_manifest(self):
        return {"allowed_paths": []}


def _state(**overrides):
    base = {
        "current_task_id": "T-001",
        "current_attempt": 0,
        "r_tree_identity": "",
        "current_manifest": {"allowed_paths": []},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_green_gate_evidence_error_and_task_not_found(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        dispatch,
        "emit_devon_outcome_failure",
        lambda host, cmd, task_id, attempt, reason: calls.append(reason),
    )
    host = _Host(tmp_path)
    host._evidence_error = "Devon evidence missing: phase"
    host._run_green_gate(_Cmd(), _state())
    assert calls == ["Devon evidence missing: phase"]

    host = _Host(tmp_path)
    host._task = None
    monkeypatch.setattr(dispatch, "cleanup_worktree", lambda handle: None)
    host._run_green_gate(_Cmd(), _state())
    failure = host.emitted[-1][1]
    assert failure["check"] == "contract_error"
    assert "GREEN_GATE task not found" in failure["reason"]


def test_green_gate_lint_note_and_regression(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "cleanup_worktree", lambda handle: None)
    host = _Host(tmp_path)
    host._lint = (None, "lint clean")
    host._run_green_gate(_Cmd(), _state())
    event, payload, _ = host.emitted[-1]
    assert event == "verdict.passed"
    assert payload["lint"] == "lint clean"

    host = _Host(tmp_path)
    host._green_regression_tests = lambda r_sha, state: ["tests/unit/test_r.py"]
    host._run_green_gate(_Cmd(), _state(r_tree_identity="r" * 40))
    assert host.emitted[-1][1]["check"] == "regression"


def test_green_gate_oscillation_detected(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatch, "cleanup_worktree", lambda handle: None)
    monkeypatch.setattr(
        dispatch, "_detect_oscillation", lambda *a, **k: {"healed": ["a"], "newly_red": ["b"]}
    )
    host = _Host(tmp_path)
    host._selection_error = TaskSelectionFailure("failed", "evidence")
    host._run_green_gate(_Cmd(), _state())
    events = [e[0] for e in host.emitted]
    assert "oscillation.detected" in events
    gate = [e for e in host.emitted if e[0] == "gate_failed"][-1]
    assert gate[1]["check"] == dispatch._OSCILLATION_CHECK
    assert gate[1]["failure_class"] == "contract_conflict"


def test_prior_oscillation_sets_store_paths(tmp_path):
    host = _Host(tmp_path)
    assert host._prior_oscillation_sets("T-001") is None

    event = SimpleNamespace(
        type="oscillation.detected", payload={"task_id": "T-001", "healed": [], "newly_red": []}
    )
    host = _Host(tmp_path, events=[event])
    assert host._prior_oscillation_sets("T-001") is None

    ref_event = SimpleNamespace(
        type="oscillation.detected",
        payload={"task_id": "T-001", "$ref": "blob"},
    )
    host = _Host(
        tmp_path,
        events=[ref_event],
        payloads={"blob": {"healed": ["h"], "newly_red": ["n"]}},
    )
    assert host._prior_oscillation_sets("T-001") == {
        "healed": ["h"],
        "newly_red": ["n"],
    }

    host = _Host(tmp_path, events=[ref_event])
    host.store.load_payload = lambda payload: (_ for _ in ()).throw(OSError("gone"))
    assert host._prior_oscillation_sets("T-001") is None


def test_task_review_no_change_failure_and_pass(tmp_path, monkeypatch):
    green = SimpleNamespace(
        seq=1, type="green.no_change", payload={"task_id": "T-001"}
    )
    host = _Host(tmp_path, events=[green])
    monkeypatch.setattr(
        dispatch,
        "_task_review_failure",
        lambda *a, **k: {"check": "budget", "reason": "over budget"},
    )
    host._run_task_review_gate(_Cmd(), _state())
    assert host.emitted[-1][1]["check"] == "budget"

    host = _Host(tmp_path, events=[green])
    monkeypatch.setattr(dispatch, "_task_review_failure", lambda *a, **k: None)
    host._run_task_review_gate(_Cmd(), _state())
    assert host.emitted[-1][0] == "verdict.passed"

    host = _Host(tmp_path, events=[green])
    host._task = None
    host._run_task_review_gate(_Cmd(), _state())
    assert host.emitted[-1][1]["reason"] == "task lookup failed for no-change review"


def test_task_review_committed_missing_identity(tmp_path, monkeypatch):
    green = SimpleNamespace(
        seq=1,
        type="green.committed",
        payload={"task_id": "T-001"},
    )
    host = _Host(tmp_path, events=[green])
    host._run_task_review_gate(_Cmd(), _state())
    assert host.emitted[-1][1]["reason"] == (
        "green.committed lacks task or base identity"
    )


def test_task_review_gate_lineage_missing(tmp_path):
    host = _Host(tmp_path)
    host._run_task_review_gate(_Cmd(), _state())
    assert host.emitted[-1][1]["reason"] == "no green.committed event found"

    stale = SimpleNamespace(seq=1, type="green.committed", payload={"task_id": "T-999"})
    host = _Host(tmp_path, events=[stale])
    host._run_task_review_gate(_Cmd(), _state())
    assert host.emitted[-1][1]["reason"] == "green.committed lacks task identity"


def test_execute_task_selection_deferred_success(tmp_path):
    host = _Host(tmp_path)
    host._task_selection = lambda cmd, state, cwd, task: _TaskSelection(
        object(), ["tests/unit/x.py::t"], {}, "nodes-blob", "sel-1"
    )
    host._run_task_selected_layers = lambda *a: (
        [],
        [{"node": "tests/unit/x.py::t"}],
        {},
        {},
        None,
    )
    host._check_drift_breaker = lambda *a: None
    host._partition_selection_failures = lambda task, failed: ([], failed)
    host._task_selection_evidence = lambda *a: _TaskSelectionEvidence(
        "digest", ("cmd",), "env", ["ev"], "outcomes-ref", [], {}
    )
    captured: dict = {}

    def deferred_payload(*args):
        captured["deferred"] = args[-1]
        return {"deferred": True}

    host._deferred_success_payload = deferred_payload
    result = MImplDispatchMixin._execute_task_selection(
        host, _Cmd(), _state(), "cwd", host._task
    )
    assert result == {"deferred": True}
    assert captured["deferred"] == [{"node": "tests/unit/x.py::t"}]


def test_task_selection_requires_baseline(tmp_path):
    host = _Host(tmp_path)
    host._collect_task_gate_nodes = lambda cwd, task: (object(), [])
    with pytest.raises(TestSelectError):
        host._task_selection(
            _Cmd(), _state(r_tree_identity=""), "cwd", host._task
        )


def test_emit_task_selected_blob_failure(tmp_path):
    host = _Host(tmp_path, blob=None)
    with pytest.raises(TestSelectError):
        host._emit_task_selected(
            _Cmd(), host._task, ["tests/unit/x.py::t"], "b", "r", "c", "t", "s"
        )


def test_task_selection_evidence_blob_failure(tmp_path):
    host = _Host(tmp_path, blob=None)
    host._task_scope_snapshot = lambda *a: ([], {}, "digest")
    host._gate_environment_identity = lambda: "env"
    host._selection_command_identity = lambda commands: ("cmd",)
    host._task_evidence_ids = lambda *a: []
    selection = _TaskSelection(object(), [], {}, "nb", "sel")
    run = SimpleNamespace(outcomes=[], commands={}, templates={}, forensics_ref=None)
    with pytest.raises(TestSelectError):
        host._task_selection_evidence(_state(), "cwd", selection, run)


def test_forensics_root_prunes_and_tolerates_errors(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(dispatch.paths, "runtime_dir", lambda home: tmp_path / "runtime")
    monkeypatch.setattr(dispatch.paths, "tracks_home", lambda repo: tmp_path / "home")
    root = tmp_path / "runtime" / "forensics"
    stale = root / "stale"
    stale.mkdir(parents=True)
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    root_result = host._forensics_root("CMD-1")
    assert root_result == root / "CMD-1"
    assert not stale.exists()

    stale.mkdir()
    os.utime(stale, (old, old))
    monkeypatch.setattr(
        dispatch.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("busy"))
    )
    assert host._forensics_root("CMD-2") == root / "CMD-2"


def test_collect_forensics_ref_shapes(tmp_path):
    host = _Host(tmp_path)
    assert host._collect_forensics_ref(tmp_path, []) is None

    root = tmp_path / "forensics"
    (root / "unit" / "failures").mkdir(parents=True)
    (root / "unit" / "failures" / "bad.json").write_text("{bad", encoding="utf-8")
    assert host._collect_forensics_ref(root, ["n"]) is None

    (root / "unit" / "failures" / "good.json").write_text(
        '{"node": "n"}', encoding="utf-8"
    )
    assert host._collect_forensics_ref(root, ["n"]) == (
        ".tracks/runtime/blobs/blob-1"
    )

    host = _Host(tmp_path, blob=None)
    assert host._collect_forensics_ref(root, ["n"]) is None


def test_snapshot_ref_or_raise_and_hard_failure(tmp_path):
    host = _Host(tmp_path)
    assert host._snapshot_ref_or_raise([], {}, {}, {}) == (
        ".tracks/runtime/blobs/blob-1"
    )

    host = _Host(tmp_path, blob=None)
    with pytest.raises(TestSelectError):
        host._snapshot_ref_or_raise([], {}, {}, {})

    host = _Host(tmp_path)
    with pytest.raises(TaskSelectionFailure) as excinfo:
        host._raise_hard_failure(
            "sel", "outcomes", [{"node": "n"}], [{"node": "d"}], forensics_ref="f"
        )
    assert "forensics_ref" in str(excinfo.value.evidence)


def test_deferred_success_payload_fields(tmp_path):
    host = _Host(tmp_path)
    host._snapshot_ref_or_raise = lambda *a: "snap"
    payload = host._deferred_success_payload(
        "sel",
        "nb",
        "outcomes",
        ["ev"],
        "digest",
        ("cmd",),
        "env",
        [],
        {},
        {},
        {},
        [{"node": "d1"}, {"node": "d2"}],
    )
    assert payload["snapshot_ref"] == "snap"
    assert payload["deferred_failures"] == ["d1", "d2"]


def test_stale_prior_task_selection_changes(tmp_path):
    previous = SimpleNamespace(
        type="test.selected",
        payload={
            "scope": "task_if",
            "task_id": "T-001",
            "selection_id": "old",
            "task_ifs": ["IF-IMPL-001"],
            "baseline": "r-old",
            "nodes": ["tests/unit/x.py::t"],
            "evidence_ids": ["ev-1"],
        },
    )
    other = SimpleNamespace(
        type="green.committed", payload={"selection_id": "old", "evidence_ids": ["ev-1"]}
    )
    host = _Host(tmp_path, events=[other, previous])
    emitted: list = []
    host._emit_stale_refs = lambda *a: emitted.append(a)

    host._stale_prior_task_selection(
        _Cmd(), host._task, ["tests/unit/x.py::t"], "r-new", "new"
    )
    assert emitted and emitted[0][4] == "baseline_changed"

    previous.payload["baseline"] = "r-new"
    host = _Host(tmp_path, events=[previous])
    emitted = []
    host._emit_stale_refs = lambda *a: emitted.append(a)
    host._stale_prior_task_selection(
        _Cmd(), host._task, ["tests/unit/x.py::t"], "r-new", "new"
    )
    assert emitted and emitted[0][4] == "selection_identity_changed"
