"""Behavior coverage for the M-IMPL RED diagnosis mixin
(``m_impl_diagnose``): classification fallback inference, ref-slot
exhaustion, anchor/walk RED contract failures and the verification-only
task runner.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import m_impl_diagnose as diag
from tracks.executor.m_impl_diagnose import MImplDiagnoseMixin
from tracks.executor.test_select import TestSelectError


class _Cmd:
    command_id = "CMD-DIAG"
    params: dict = {}


class _Store:
    def __init__(self, blobs=None):
        self.events_list: list = []
        self._blobs = blobs if blobs is not None else ["blob-1"]

    def events(self, _run_id):
        return list(self.events_list)

    def state(self, _run_id):
        return SimpleNamespace(hotfix_issue=None)

    def write_audit_blob(self, payload):
        return self._blobs.pop(0) if self._blobs else None


class _Host(MImplDiagnoseMixin):
    def __init__(self, tmp_path: Path, *, blobs=None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _Store(blobs)
        self.emitted: list = []
        self._argv = (["python", "-m", "pytest"], tmp_path / "result.json", tmp_path)

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _rebuild_task_log_projection(self):
        return None

    def _lookup_task(self, task_id):
        return None

    def _selected_test_argv(self, test_refs, command_id, layer):
        return self._argv


def _state(meta=None, **overrides):
    base = {
        "current_task_id": "T-001",
        "current_attempt": 0,
        "current_task_metadata": meta if meta is not None else {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# -- pure classification helpers --------------------------------------------


def test_red_classification_label_fallbacks():
    assert diag._red_classification_label(None) == "missing"
    assert diag._red_classification_label(42) == "unknown"
    assert diag._red_classification_label("  ") == "missing"
    assert diag._red_classification_label(" assertion_failure ") == "assertion_failure"


def test_red_inference_from_command_shapes():
    assert diag._red_inference_from_command("not-a-dict") == []
    assert diag._red_inference_from_command({"output_summary": 1}) == []
    assert diag._red_inference_from_command(
        {"result": "pass", "output_summary": "assert x failed"}
    ) == []
    assert diag._red_inference_from_command(
        {"result": "fail", "output_summary": "ERROR collecting tests"}
    ) == []
    assert diag._red_inference_from_command(
        {"result": "fail", "output_summary": "AssertionError: nope"}
    ) == ["assertion_failure"]
    assert diag._red_inference_from_command(
        {"result": "fail", "output_summary": "has no attribute 'run'"}
    ) == ["symbol_missing"]
    assert diag._red_inference_from_command(
        {"output_summary": "classify_red -> symbol_missing"}
    ) == ["symbol_missing"]


def test_red_classifications_missing_commands_shape():
    assert diag._m_impl_red_classifications({"commands": "nope"}) == (
        ["missing"],
        False,
    )
    assert diag._m_impl_red_classifications(
        {"commands": [{"result": "fail", "output_summary": "AssertionError"}]}
    ) == (["assertion_failure"], True)


def test_red_classification_error_verdict_branches():
    illegal = diag._m_impl_red_classification_error(
        {
            "results": [{"classification": "assertion_failure"}],
            "verdict": "bogus",
        }
    )
    assert illegal is not None
    assert "illegal verdict" in illegal[0]

    mismatch = diag._m_impl_red_classification_error(
        {
            "results": [{"classification": "assertion_failure"}],
            "verdict": "symbol_missing",
        }
    )
    assert mismatch is not None
    assert "does not match classification" in mismatch[0]


def test_ref_slot_exhaustion_raises(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        diag, "git", lambda *a, **k: SimpleNamespace(returncode=0, stdout="sha")
    )
    with pytest.raises(TestSelectError):
        host._red_ref_free_attempt("T-001", 1)


# -- anchor / walk / verify runners -----------------------------------------


def test_run_anchor_suite_timeout(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    result_path = tmp_path / "result.json"
    result_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd="pytest", timeout=1800)
        ),
    )
    rc, _out, err = host._run_anchor_suite(["pytest"], tmp_path, result_path)
    assert rc == 124
    assert "timed out" in err
    assert not result_path.exists()


def test_fail_anchor_contract_emits(tmp_path):
    host = _Host(tmp_path)
    host._fail_anchor_contract(_Cmd(), "T-001", 2, "integration", RuntimeError("x"))
    event, payload, _ = host.emitted[-1]
    assert event == "verdict.failed"
    assert payload["check"] == "contract_error"
    assert "integration" in payload["reason"]


def test_do_anchor_red_missing_refs_and_contract_error(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host._do_anchor_red(_Cmd(), _state(), "T-001", False)
    assert host.emitted[-1][1]["reason"] == "preset-anchor task declares no test_refs"

    def boom(*_a, **_k):
        raise TestSelectError("no contract")

    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_selected_test_argv", boom)
    host._do_anchor_red(_Cmd(), _state({"test_refs": ["tests/x.py::t"]}), "T-001", False)
    assert host.emitted[-1][1]["check"] == "contract_error"
    assert "integration" in host.emitted[-1][1]["reason"]


def test_do_anchor_red_green_anchors_confirm(tmp_path, monkeypatch):
    # T-013 precedent: anchors already green through accumulated WIP --
    # the Runtime CONFIRMS (never fabricates a red): the event states the
    # anchors were green and pins the R ref to the base tree.
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_run_anchor_suite", lambda *a: (0, "1 passed", ""))
    host._pin_r_ref = lambda tid, attempt: ("refs/trac/rgr/RUN/T/1/red", "base")
    host._do_anchor_red(
        _Cmd(), _state({"test_refs": ["tests/x.py::t"]}), "T-001", False
    )
    event, payload, _ = host.emitted[-1]
    assert event == "red.checkpointed"
    assert payload["anchors"] == "green"
    assert "already green" in payload["reason"]
    assert payload["r_sha"] == "refs/trac/rgr/RUN/T/1/red"


def test_do_anchor_red_success_checkpoints(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_run_anchor_suite", lambda *a: (1, "1 failed", ""))
    host._pin_r_ref = lambda tid, attempt: ("refs/trac/rgr/RUN/T/1/red", "base")
    host._do_anchor_red(
        _Cmd(), _state({"test_refs": ["tests/x.py::t"]}), "T-001", False
    )
    event, payload, _ = host.emitted[-1]
    assert event == "red.checkpointed"
    assert payload["anchor"] is True
    assert payload["evidence"]


def test_do_walk_red_selection_none_and_success(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host._walk_red_selection = lambda *a: None
    host._do_walk_red(_Cmd(), _state(), "T-001", {"integration": True})
    assert host.emitted == []

    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_run_anchor_suite", lambda *a: (0, "unit green", ""))
    host._pin_r_ref = lambda tid, attempt: ("ref", "base")
    host._do_walk_red(
        _Cmd(),
        _state(),
        "T-001",
        {"integration": True, "unit_refs": ["tests/unit/x.py::t"]},
    )
    assert host.emitted[-1][0] == "red.checkpointed"
    assert host.emitted[-1][1]["walk_red"] is True


def test_walk_red_selection_no_unit_refs_and_contract_error(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    assert host._walk_red_selection(_Cmd(), "T-001", 1, {"unit_refs": []}) is None
    assert host.emitted[-1][1]["reason"] == (
        "walk_red integration task declares no unit_refs"
    )

    host = _Host(tmp_path)
    monkeypatch.setattr(
        host,
        "_selected_test_argv",
        lambda *a: (_ for _ in ()).throw(TestSelectError("no unit contract")),
    )
    assert host._walk_red_selection(
        _Cmd(), "T-001", 1, {"unit_refs": ["tests/unit/x.py::t"]}
    ) is None
    assert host.emitted[-1][1]["check"] == "contract_error"


def test_walk_red_regression_failure(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(host, "_run_anchor_suite", lambda *a: (1, "1 failed", ""))
    host._do_walk_red(
        _Cmd(), _state(), "T-001", {"integration": True, "unit_refs": ["x"]}
    )
    event, payload, _ = host.emitted[-1]
    assert event == "verdict.failed"
    assert "not green" in payload["reason"]


def test_verify_task_missing_refs_and_contract_error(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host._do_verify_task(_Cmd(), _state(), "T-001", False)
    assert host.emitted[-1][1]["reason"] == (
        "verification-only task declares no test_refs"
    )

    host = _Host(tmp_path)
    monkeypatch.setattr(
        host,
        "_selected_test_argv",
        lambda *a: (_ for _ in ()).throw(TestSelectError("no contract")),
    )
    host._do_verify_task(
        _Cmd(), _state({"test_refs": ["tests/x.py::t"]}), "T-001", False
    )
    assert host.emitted[-1][1]["check"] == "contract_error"


def test_verify_task_timeout_and_failure_evidence(tmp_path, monkeypatch):
    host = _Host(tmp_path, blobs=["blob-9"])
    result_path = tmp_path / "result.json"
    result_path.write_text("x", encoding="utf-8")
    host._argv = (["python", "-m", "pytest"], result_path, tmp_path)
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd="pytest", timeout=1800)
        ),
    )
    host._do_verify_task(
        _Cmd(), _state({"test_refs": ["tests/x.py::t"]}), "T-001", False
    )
    event, payload, _ = host.emitted[-1]
    assert event == "verdict.failed"
    assert payload["reason"].startswith("verification test_refs failed rc=124")
    assert ".tracks/runtime/blobs/blob-9" in payload["evidence"]


def test_verify_task_success_completes(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    result_path = tmp_path / "result.json"
    result_path.write_text("x", encoding="utf-8")
    host._argv = (["python", "-m", "pytest"], result_path, tmp_path)
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="1 passed", stderr=""),
    )
    host._do_verify_task(
        _Cmd(), _state({"test_refs": ["tests/x.py::t"]}), "T-001", False
    )
    assert [e[0] for e in host.emitted][-2:] == ["task.completed", "writelock.released"]
    assert not result_path.exists()
