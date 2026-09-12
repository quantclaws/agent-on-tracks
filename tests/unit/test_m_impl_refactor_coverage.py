"""Behavior coverage for the M-IMPL REFACTOR gate (``m_impl_refactor``):

evidence-shape routing, green reuse, refactor diff reconstruction, forbidden
path rejection, no-change emission and the commit fail-closed branches.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor import m_impl_refactor as refactor
from tracks.executor.m_impl_dispatch import TaskSelectionFailure
from tracks.executor.m_impl_refactor import MImplRefactorMixin


class _Cmd:
    command_id = "CMD-REF"


def _state(**overrides):
    base = {
        "current_task_id": "T-001",
        "current_attempt": 0,
        "current_manifest": {"allowed_paths": ["tracks/a.py"]},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _Host(MImplRefactorMixin):
    def __init__(self, tmp_path: Path):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.emitted: list = []
        self.cleaned: list = []
        self._evidence_error = None
        self._last_outcome: dict = {}
        self._reuse = (False, [], None)
        self._task = None
        self._selection_error: Exception | None = None
        self._committed = True

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _emit_gate_failure(self, cmd, **kwargs):
        self.emitted.append(("gate_failed", kwargs))

    def _emit_refactor_evidence_error(self, cmd, reason, state):
        MImplRefactorMixin._emit_refactor_evidence_error(self, cmd, reason, state)

    def _devon_evidence_error(self, phase, state):
        return self._evidence_error

    def _ensure_gate_worktree(self, state, phase="refactor"):
        return "cwd", "handle"

    def _last_devon_outcome(self):
        return self._last_outcome

    def _green_reuse_state(self, task_id, repo):
        return self._reuse

    def _reuse_green_evidence(self, cmd, task_id, outcome, green):
        self.emitted.append(("reuse", task_id))

    def _flag_stale_green_evidence(self, cmd, task_id, green, observed):
        self.emitted.append(("flag_stale", task_id))

    def _lookup_task(self, task_id):
        return self._task

    def _refactor_diff_reconstructable(self, outcome, observed_changed):
        return MImplRefactorMixin._refactor_diff_reconstructable(
            self, outcome, observed_changed
        )

    def _execute_task_selection(self, cmd, state, cwd, task):
        if self._selection_error is not None:
            raise self._selection_error
        return {"origins": ["cwd"]}

    def _commit_refactor_changes(self, cmd, state, task_id, changed, selection):
        if not self._committed:
            return False
        self.emitted.append(("committed", changed))
        return True

    def _rebuild_task_log_projection(self):
        return None


def _run(monkeypatch, host, *, outcome=None, reuse=None, task=None, error=None):
    monkeypatch.setattr(refactor, "cleanup_worktree", lambda handle: host.cleaned.append(handle))
    host._last_outcome = outcome or {"changed_paths": ["tracks/a.py"]}
    if reuse is not None:
        host._reuse = reuse
    if task is not None:
        host._task = task
    if error is not None:
        host._selection_error = error
    host._do_run_refactor_gate(_Cmd(), _state(), "T-001", False)


def test_refactor_gate_evidence_error_returns(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    host._evidence_error = "Devon evidence missing: phase"
    _run(monkeypatch, host)
    assert host.emitted[0][0] == "verdict.failed"
    assert host.emitted[0][1]["check"] == "evidence_malformed"


def test_refactor_gate_reuses_green_evidence(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    _run(monkeypatch, host, reuse=(True, ["tracks/a.py"], "green"))
    assert ("reuse", "T-001") in host.emitted
    assert host.cleaned == ["handle"]


def test_refactor_gate_forbidden_and_no_change(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    task = SimpleNamespace(task_id="T-001")
    monkeypatch.setattr(
        MImplRefactorMixin,
        "_refactor_diff_missing",
        lambda self, outcome, observed: False,
    )
    host._task = task
    _run(monkeypatch, host, outcome={"changed_paths": ["other/b.py"]})
    failures = [e for e in host.emitted if e[0] == "verdict.failed"]
    assert failures and failures[0][1]["check"] == "scope"

    host = _Host(tmp_path)
    host._task = task
    _run(
        monkeypatch,
        host,
        outcome={"changed_paths": [], "no_change_reason": "already done"},
    )
    assert any(e[0] == "refactor.no_change" for e in host.emitted)

    host = _Host(tmp_path)
    host._task = task
    host._committed = False
    _run(monkeypatch, host)
    assert ("committed", ["tracks/a.py"]) not in host.emitted


def test_refactor_gate_task_selection_failure(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    _run(
        monkeypatch,
        host,
        task=SimpleNamespace(task_id="T-001"),
        error=TaskSelectionFailure("bad selection", "evidence"),
    )
    assert any(e[0] == "gate_failed" for e in host.emitted)
    failures = [e for e in host.emitted if e[0] == "gate_failed"]
    assert failures[0][1]["check"] == "regression"


def test_refactor_gate_task_not_found_and_missing_diff(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    _run(monkeypatch, host, task=None)
    failures = [e for e in host.emitted if e[0] == "gate_failed"]
    assert failures and "REFACTOR_GATE task not found" in failures[0][1]["reason"]

    host = _Host(tmp_path)
    monkeypatch.setattr(
        MImplRefactorMixin,
        "_refactor_diff_missing",
        lambda self, outcome, observed: True,
    )
    monkeypatch.setattr(
        MImplRefactorMixin,
        "_refactor_diff_reconstructable",
        lambda self, outcome, observed: False,
    )
    _run(monkeypatch, host, task=SimpleNamespace(task_id="T-001"))
    failures = [e for e in host.emitted if e[0] == "gate_failed"]
    assert "without a captured diff" in failures[0][1]["reason"]


def test_refactor_diff_helpers():
    assert MImplRefactorMixin._refactor_diff_missing({}, ["tracks/a.py"]) is True
    assert MImplRefactorMixin._refactor_diff_missing(
        {"diff_ref": "diff"}, ["tracks/a.py"]
    ) is False
    assert MImplRefactorMixin._refactor_diff_missing(
        {"diff_ref": "no-change"}, ["tracks/a.py"]
    ) is True
    assert MImplRefactorMixin._refactor_diff_missing({"diff_ref": "d"}, []) is False

    host = object.__new__(MImplRefactorMixin)
    host._generate_diff_from_changed_paths = lambda union: "diff"
    assert host._refactor_diff_reconstructable({}, ["a.py"]) is True
    host._generate_diff_from_changed_paths = lambda union: None
    assert host._refactor_diff_reconstructable({}, ["a.py"]) is False

    outside = MImplRefactorMixin._refactor_outside_paths(
        ["tracks/a.py", "other/b.py"], ["tracks/**"]
    )
    assert outside == ["other/b.py"]


def test_commit_refactor_changes_paths(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    add_calls: list = []
    monkeypatch.setattr(refactor, "git", lambda repo, *args: add_calls.append(args))

    monkeypatch.setattr(
        "tracks.executor.executor._scoped_commit_if_staged",
        lambda repo, message, paths: None,
    )
    assert (
        MImplRefactorMixin._commit_refactor_changes(
            host, _Cmd(), _state(), "T-001", ["tracks/a.py"], {}
        )
        is False
    )
    assert host.emitted[-1][1]["reason"] == "refactor evidence has no captured diff"

    host = _Host(tmp_path)
    proc = SimpleNamespace(returncode=1, stderr="hook", stdout="")
    monkeypatch.setattr(
        "tracks.executor.executor._scoped_commit_if_staged",
        lambda repo, message, paths: proc,
    )
    host._emit_commit_failure = lambda proc, state, cid: host.emitted.append(
        ("commit_failure", proc)
    )
    assert (
        MImplRefactorMixin._commit_refactor_changes(
            host, _Cmd(), _state(), "T-001", ["tracks/a.py"], {"x": 1}
        )
        is False
    )
    assert host.emitted[-1][0] == "commit_failure"

    host = _Host(tmp_path)
    proc = SimpleNamespace(returncode=0, stderr="", stdout="")
    monkeypatch.setattr(
        "tracks.executor.executor._scoped_commit_if_staged",
        lambda repo, message, paths: proc,
    )
    assert (
        MImplRefactorMixin._commit_refactor_changes(
            host, _Cmd(), _state(), "T-001", ["tracks/a.py"], {"x": 1}
        )
        is True
    )
    assert host.emitted[-1][0] == "refactor.committed"


def test_emit_refactor_evidence_error_branches(tmp_path):
    host = _Host(tmp_path)
    host._emit_refactor_evidence_error(
        _Cmd(), "Devon evidence missing: phase", _state()
    )
    assert host.emitted[-1][1]["check"] == "evidence_malformed"
    host._emit_refactor_evidence_error(_Cmd(), "refactor failure", _state())
    assert host.emitted[-1][1]["check"] == "regression"
