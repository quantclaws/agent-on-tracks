"""B43（#45）：trac run 进程代码版本戳单元测试。

r1 实证（run 01M0AMKV 异常 #1）：运行中进程外修改 tracks/** 代码，
进程不热加载继续跑旧逻辑 → 误判 + escalation。修复合同：进程启动时取
指纹，每次派发前复核，漂移即 RuntimeCodeDriftError fail-fast。
"""

from pathlib import Path

import pytest

from tracks.executor.code_stamp import DRIFT_MESSAGE, RuntimeCodeDriftError, code_stamp
from tracks.executor.executor import Executor
from tracks.store import Store


def _repo_with_tracks_pkg(tmp_path: Path) -> Path:
    """带最小 tracks/ 包的仓库（宿主项目无包 → stamp=None 是另一路径）。"""
    repo = tmp_path / "host"
    repo.mkdir()
    pkg = repo / "tracks"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "logic.py").write_text("X = 1\n", encoding="utf-8")
    return repo


def _executor(repo: Path) -> Executor:
    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    run_id = "RUN-DRIFT"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    return Executor(store, repo, run_id)


class _StubBackend:
    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        return {"status": "failed", "failure_class": "agent_failed", "self_report": "x"}


# -- code_stamp 纯函数 ----------------------------------------------------------


def test_code_stamp_none_without_tracks_pkg(tmp_path):
    assert code_stamp(tmp_path) is None


def test_code_stamp_deterministic_and_content_sensitive(tmp_path):
    repo = _repo_with_tracks_pkg(tmp_path)
    s1 = code_stamp(repo)
    assert s1 == code_stamp(repo)  # 确定性（排序 + 内容）
    (repo / "tracks" / "logic.py").write_text("X = 2\n", encoding="utf-8")
    assert code_stamp(repo) != s1  # 内容变化 → 漂移


def test_code_stamp_ignores_pycache_and_non_py(tmp_path):
    repo = _repo_with_tracks_pkg(tmp_path)
    before = code_stamp(repo)
    pycache = repo / "tracks" / "__pycache__"
    pycache.mkdir()
    (pycache / "logic.cpython-314.pyc").write_bytes(b"\x00\x01")
    (repo / "tracks" / "notes.txt").write_text("not code", encoding="utf-8")
    assert code_stamp(repo) == before  # .pyc / 非 .py 不触发


# -- executor fail-fast ----------------------------------------------------------


def test_run_loop_fails_fast_on_code_drift(tmp_path, capsys):
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    ex.backend = _StubBackend()
    # 进程启动后操作者修改 tracks/**（未提交，正是 r1 场景）
    (repo / "tracks" / "logic.py").write_text("X = 2  # hotfix\n", encoding="utf-8")
    with pytest.raises(RuntimeCodeDriftError, match="OLD logic"):
        ex.run_loop()
    # M7（convergence plan 2026-09-05）：阶段边界优雅交接，不再自杀式
    # abort；stderr 提示 handover 与接管语义
    err = capsys.readouterr().err
    assert "run handover" in err and "code drift" in err
    assert ex._code_stamp is not None
    # 漂移审计事件 code.drift 落盘（audit-only），run 保持 active，
    # 不产生 loop.aborted / evidence.staled 级联
    events = list(ex.store.events("RUN-DRIFT"))
    assert events[-1].type == "code.drift"
    assert events[-1].payload["reason"] == "handover"
    assert not [e for e in events if e.type == "loop.aborted"]
    assert ex.store.state("RUN-DRIFT").status == "active"


def test_loop_aborted_replay_does_not_crash(tmp_path):
    """#85：含 loop.aborted 的事件流重放不炸（reducer 无行为，projection
    忽略该事件）。M7 起漂移生产者落 code.drift；current 与历史两类
    audit-only 事件都必须重放无害。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    ex.backend = _StubBackend()
    (repo / "tracks" / "logic.py").write_text("X = 2  # hotfix\n", encoding="utf-8")
    with pytest.raises(RuntimeCodeDriftError):
        ex.run_loop()
    events = list(ex.store.events("RUN-DRIFT"))
    assert events[-1].type == "code.drift"
    state = ex.store.state("RUN-DRIFT")  # 重放含 code.drift 的事件流
    assert state.status == "active"
    assert state.stage == "M-STORY"
    # #85 历史语料：loop.aborted 事件同样被 reducer/projection 忽略
    ex.store.append("RUN-DRIFT", "v0.1", "loop.aborted", {"reason": "code_drift"})
    state = ex.store.state("RUN-DRIFT")
    assert state.status == "active"
    assert state.stage == "M-STORY"


def test_run_loop_proceeds_when_code_unchanged(tmp_path):
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    ex.backend = _StubBackend()
    ex.max_dispatches = 1
    state = ex.run_loop()  # 不漂移：正常派发一轮后按 gate 停
    assert state.stage == "M-STORY"


def test_host_project_without_tracks_pkg_never_fails(tmp_path):
    """宿主项目（无 tracks/ 包）：stamp=None，检查跳过，永不误停。"""
    repo = tmp_path / "host-app"
    repo.mkdir()
    (repo / "README.md").write_text("app\n", encoding="utf-8")
    ex = _executor(repo)
    assert ex._code_stamp is None
    ex.backend = _StubBackend()
    ex.max_dispatches = 1
    ex.run_loop()  # 无异常


def test_drift_message_actionable():
    assert "Restart" in DRIFT_MESSAGE and "B43" in DRIFT_MESSAGE


def test_executor_records_stamp_at_construction(tmp_path):
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    assert ex._code_stamp == code_stamp(repo)
