"""Behavior coverage for ``ExecObserveMixin``: OOB observation deferral,
dirty-tree snapshot shaping, the tasks.md projection guard fail-closed
branches and the tasks.md restore helper.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from tracks.executor import observe
from tracks.executor.observe import ExecObserveMixin


class _Host(ExecObserveMixin):
    def __init__(self, tmp_path: Path):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.version = "v0.8"
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self.emitted: list = []
        self._oob_head = "old-head"
        self.store = SimpleNamespace(
            state=lambda _run: SimpleNamespace(pending=None),
            events=lambda _run: [],
        )

    def _vdir(self):
        return self._vdir_path

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _extract_pre_snapshot_for_guards(self, params):
        return {}

    def _is_path_in_diff(self, rel, pre, post):
        return False


def test_observe_oob_defers_while_pending(monkeypatch, tmp_path):
    host = _Host(tmp_path)
    monkeypatch.setattr(observe.oob, "head_sha", lambda _repo: "new-head")
    host.store = SimpleNamespace(
        state=lambda _run: SimpleNamespace(pending="dispatch"),
        events=lambda _run: [],
    )
    host._observe_oob()
    assert host.emitted == []
    assert host._oob_head == "old-head"


def test_dirty_files_blank_and_rename_lines(monkeypatch, tmp_path):
    host = _Host(tmp_path)

    def fake_git(*_args, **_kwargs):
        return SimpleNamespace(stdout="  \nR  old.py -> new.py\n M changed.py\n")

    monkeypatch.setattr(observe, "git", fake_git)
    assert host._dirty_files() == {"new.py", "changed.py"}


def test_dirty_snapshot_blank_and_rename_lines(monkeypatch, tmp_path):
    host = _Host(tmp_path)
    (host.repo / "new.py").write_text("new", encoding="utf-8")

    def fake_git(*_args, **_kwargs):
        return SimpleNamespace(stdout="\nR  old.py -> new.py\n")

    monkeypatch.setattr(observe, "git", fake_git)
    snapshot = host._dirty_snapshot()
    assert list(snapshot) == ["new.py"]
    assert len(snapshot["new.py"]) == 64


def test_resolve_pre_dirty_legacy_fallback(tmp_path):
    host = _Host(tmp_path)
    state = SimpleNamespace(stage="M-TEST")
    assert host._resolve_pre_dirty(state, "WRITE", {}) == {}
    assert host._resolve_pre_dirty(
        SimpleNamespace(stage="M-TEST"), "WRITE", {"pre_dirty": ["a.py"]}
    ) == {"a.py"}


def test_tasks_md_guard_vdir_failure(tmp_path, monkeypatch):
    host = _Host(tmp_path)

    def boom():
        raise RuntimeError("no vdir")

    monkeypatch.setattr(host, "_vdir", boom)
    assert host._tasks_md_guard() == "skipped"


def test_tasks_md_guard_read_and_parse_failures(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    tasks_json = host._vdir_path / "tasks.json"
    tasks_json.mkdir()
    assert host._tasks_md_guard() == "skipped"

    tasks_json.rmdir()
    tasks_json.write_text("{not json", encoding="utf-8")
    assert host._tasks_md_guard() == "skipped"

    monkeypatch.setitem(sys.modules, "tracks.executor.taskgraph", None)
    assert host._tasks_md_guard() == "skipped"
    monkeypatch.undo()

    tasks_json.write_text(
        '{"schema": 2, "tasks": [{"task_id": "T-001", "issue_number": 1,'
        ' "description": "d", "ac_refs": ["AC-FR0001-01"], "fr_refs": ["FR-0001"],'
        ' "if_ids": ["IF-IMPL-001"], "scope_boundary": "tracks/a.py",'
        ' "depends_on": [], "batch": "1", "parallel": false,'
        ' "unit_refs": [], "acceptance_refs": []}]}',
        encoding="utf-8",
    )
    import tracks.executor.taskgraph as taskgraph

    monkeypatch.setattr(
        taskgraph,
        "render_tasks_md",
        lambda tasks: (_ for _ in ()).throw(RuntimeError("render")),
    )
    assert host._tasks_md_guard() == "skipped"

    monkeypatch.setattr(taskgraph, "render_tasks_md", lambda tasks: "rendered\n")
    (host._vdir_path / "tasks.md").mkdir()
    guard = host._tasks_md_guard()
    assert isinstance(guard, observe._TasksMdGuard)
    assert guard.post_md is None


def test_tasks_md_guard_mismatch_returns_guard(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    (host._vdir_path / "tasks.json").write_text(
        '{"schema": 2, "tasks": []}', encoding="utf-8"
    )
    import tracks.executor.taskgraph as taskgraph

    monkeypatch.setattr(taskgraph, "render_tasks_md", lambda tasks: "rendered\n")
    guard = host._tasks_md_guard()
    assert guard.path == host._vdir_path / "tasks.md"
    assert guard.rendered == "rendered\n"
    assert guard.post_md is None


def test_apply_tasks_md_guard_ok_when_decision_unclassified(
    tmp_path, monkeypatch
):
    host = _Host(tmp_path)
    import tracks.executor.taskgraph as taskgraph

    monkeypatch.setattr(taskgraph, "classify_tasks_md_guard", lambda *a: "other")
    guard = observe._TasksMdGuard(host._vdir_path / "tasks.md", "r", None)
    assert host._apply_tasks_md_guard(None, None, None, {}, guard) == "ok"

    monkeypatch.setattr(
        host, "_dirty_snapshot", lambda: (_ for _ in ()).throw(OSError("dirty"))
    )
    assert host._apply_tasks_md_guard(None, None, None, {}, guard) == "ok"


def test_tasks_md_relpath_symlink_and_outside(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    host = _Host(tmp_path)
    host.repo = link
    in_real = real / ".tracks" / "v0.8" / "tasks.md"
    assert host._tasks_md_relpath(in_real) == ".tracks/v0.8/tasks.md"

    outside = tmp_path / "elsewhere" / "tasks.md"
    assert host._tasks_md_relpath(outside) == "tasks.md"


def test_restore_tasks_md_failure_prints(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    ExecObserveMixin._restore_tasks_md(
        blocker / "tasks.md", "rendered", "restore failed"
    )
    assert "restore failed" in capsys.readouterr().err


def test_dirty_tree_stamp_unreadable_and_skipped(monkeypatch, tmp_path):
    host = _Host(tmp_path)
    calls = {"n": 0}

    def fake_git(_repo, *args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(stdout="deadbeef\n")
        return SimpleNamespace(stdout=" D gone.py\n")

    monkeypatch.setattr(observe, "git", fake_git)
    stamp = host._dirty_tree_stamp()
    assert stamp and stamp != "deadbeef"

    monkeypatch.setattr(observe, "porcelain_path", lambda line: None)
    calls["n"] = 0
    assert host._dirty_tree_stamp() == "deadbeef"


def test_oob_accepted_test_files_filters_non_test_paths():
    events = [
        SimpleNamespace(
            type="oob.accepted",
            payload={"files": ["tests/unit/test_a.py", 1, "tracks/a.py", ""]},
        ),
        SimpleNamespace(type="verdict.passed", payload={"files": ["tests/e2e/x.py"]}),
    ]
    host = object.__new__(ExecObserveMixin)
    host.run_id = "RUN"
    host.store = SimpleNamespace(events=lambda _run: events)
    assert host._oob_accepted_test_files() == {"tests/unit/test_a.py"}
