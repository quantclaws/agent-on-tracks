"""B43（#45）：trac run 进程代码版本戳单元测试。

r1 实证（run 01M0AMKV 异常 #1）：运行中进程外修改 ``tracks/**`` 代码，
进程不热加载继续跑旧逻辑 → 误报 + escalation。修复合同：进程启动时取
指纹，每次派发前复核，漂移即 RuntimeCodeDriftError fail-fast。

#173（run 01M2QTJB，20 分钟 2 次换手）：v0.9 dogfood 里 Devon GREEN
写的产品代码就是 ``tracks/**``——replay 落盘属 runtime 自有写域，按路径
归属吸收并重定基线（不换手）；外来写入仍换手；混合时 fail-closed 换手。
"""

from pathlib import Path

import pytest

from tracks.executor.code_stamp import (
    DRIFT_MESSAGE,
    RuntimeCodeDriftError,
    code_file_stamps,
    code_stamp,
    stamps_digest,
)
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


# -- #173：per-file 底座与两种取法的一致性 ---------------------------------------


def test_stamps_digest_pins_the_historical_format(tmp_path):
    """Prism #173 R1（IMPL-3）：不能拿实现自证（stamps_digest ∘
    code_file_stamps == code_stamp 是定义）。用固定 fixture 钉死**历史**
    格式的字面哈希——``"<path>:<digest>"`` 按路径排序、换行连接、整体
    sha256；fixture 刻意含 ``tracks/foo-bar.py`` 与 ``tracks/foo/x.py``
    共存（'-'(0x2D) < '/'(0x2F) 的排序分叉点），任何未来重写改格式或
    改排序都会在此断裂。"""
    repo = tmp_path / "pin"
    repo.mkdir()
    (repo / "tracks").mkdir()
    (repo / "tracks" / "foo").mkdir()
    (repo / "tracks" / "__init__.py").write_bytes(b"")
    (repo / "tracks" / "foo-bar.py").write_bytes(b"A = 1\n")
    (repo / "tracks" / "foo" / "x.py").write_bytes(b"B = 2\n")
    (repo / "tracks" / "logic.py").write_bytes(b"X = 1\n")
    stamps = code_file_stamps(repo)
    assert stamps is not None
    assert stamps_digest(stamps) == (
        "9f2fdd4ade962ef1f1a77aa058003047801f833f694178a81820ff51c92ed0a7"
    )
    # 与历史单值取法同构（二级断言：字面哈希是主钉）
    assert stamps_digest(stamps) == code_stamp(repo)


def test_code_file_stamps_none_without_tracks_pkg(tmp_path):
    assert code_file_stamps(tmp_path) is None


# -- #173：自有写域吸收 / 外来换手 / 混合 fail-closed -----------------------------


def test_self_write_rebaseline_absorbs_without_handover(tmp_path):
    """Devon GREEN 的 replay 落盘（已 note）属自有写域：审计事件
    reason=self_write_rebaseline、基线前移、不抛异常、第二次检查静默。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    # replay 落盘先发生（内容已变），note 是对已变内容的声明（Prism #173 R1）
    (repo / "tracks" / "logic.py").write_text("X = 2  # devon green\n", encoding="utf-8")
    ex._note_runtime_writes(["tracks/logic.py", "tests/unit/test_x_red.py"])
    # tests/ 路径与 stamp 无关（过滤后只留 tracks/**.py）
    assert ex._runtime_written_paths == {"tracks/logic.py"}
    ex._fail_fast_on_code_drift()  # 不抛：吸收
    events = [e for e in ex.store.events("RUN-DRIFT") if e.type == "code.drift"]
    assert len(events) == 1
    assert events[-1].payload["reason"] == "self_write_rebaseline"
    assert events[-1].payload["paths"] == ["tracks/logic.py"]
    assert ex.store.state("RUN-DRIFT").status == "active"
    # 基线已前移：同一棵树再查是 no-op
    ex._fail_fast_on_code_drift()
    assert len([e for e in ex.store.events("RUN-DRIFT") if e.type == "code.drift"]) == 1
    assert ex._code_stamp == code_stamp(repo)


def test_self_write_new_and_deleted_files_absorb(tmp_path):
    """自有写入的新增/删除 tracks/**.py 同样吸收（stamp 覆盖文件集合）。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    (repo / "tracks" / "new_module.py").write_text("Y = 1\n", encoding="utf-8")
    (repo / "tracks" / "logic.py").unlink()
    ex._note_runtime_writes(["tracks/new_module.py", "tracks/logic.py"])
    ex._fail_fast_on_code_drift()  # 不抛
    assert ex._code_stamp == code_stamp(repo)


def test_foreign_write_still_hands_over_with_path_list(tmp_path):
    """操作者热修（未 note 的路径）：M7 换手不变，事件列出外来路径。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    (repo / "tracks" / "operator_patch.py").write_text("Z = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeCodeDriftError):
        ex._fail_fast_on_code_drift()
    events = [e for e in ex.store.events("RUN-DRIFT") if e.type == "code.drift"]
    assert events[-1].payload["reason"] == "handover"
    assert events[-1].payload["foreign_paths"] == ["tracks/operator_patch.py"]


def test_mixed_self_and_foreign_fails_closed(tmp_path):
    """自有 + 外来混合：fail-closed 换手（宁误换手不吞操作者写入）。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    (repo / "tracks" / "logic.py").write_text("X = 2  # devon\n", encoding="utf-8")
    ex._note_runtime_writes(["tracks/logic.py"])
    (repo / "tracks" / "operator_patch.py").write_text("Z = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeCodeDriftError):
        ex._fail_fast_on_code_drift()
    events = [e for e in ex.store.events("RUN-DRIFT") if e.type == "code.drift"]
    assert events[-1].payload["reason"] == "handover"
    assert events[-1].payload["foreign_paths"] == ["tracks/operator_patch.py"]


def test_note_runtime_writes_skipped_on_host_project(tmp_path):
    """宿主项目（无 tracks/ 包）：note 是 no-op，漂移检查跳过。"""
    repo = tmp_path / "host-app"
    repo.mkdir()
    (repo / "README.md").write_text("app\n", encoding="utf-8")
    ex = _executor(repo)
    ex._note_runtime_writes(["tracks/anything.py"])
    assert ex._runtime_written_paths == set()
    ex._fail_fast_on_code_drift()  # no-op, 不抛


# -- Prism #173 R1 回归：过期条目不得吸收外来写入 -----------------------------------


def test_same_bytes_note_claims_nothing_and_later_hotfix_hands_over(tmp_path):
    """R1 blocker 回归：seeded-WIP 零增量回放（mirror 同字节 copy）后按名
    note——内容未变，声明为空；之后操作者热修同一路径必须换手（而不是被
    过期条目吸收成 self_write_rebaseline）。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    # 回放落盘同字节内容（seed-WIP 往返的等价物）：路径在、内容没变
    ex._note_runtime_writes(["tracks/logic.py"])
    assert ex._runtime_written_paths == set()
    # 操作者热修同一路径 → 外来 → M7 换手
    (repo / "tracks" / "logic.py").write_text("X = 9  # operator\n", encoding="utf-8")
    with pytest.raises(RuntimeCodeDriftError):
        ex._fail_fast_on_code_drift()


def test_note_only_records_content_changed_paths(tmp_path):
    """note 是「内容已变」的断言：变更路径入集、同字节路径不入、基线内
    被删除的路径入集、基线外新文件在变更时入集。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    (repo / "tracks" / "logic.py").write_text("X = 2\n", encoding="utf-8")  # 变更
    (repo / "tracks" / "new.py").write_text("N = 1\n", encoding="utf-8")  # 新增
    (repo / "tracks" / "__init__.py").unlink()  # 删除（基线内）
    ex._note_runtime_writes(
        [
            "tracks/logic.py",  # changed → note
            "tracks/__init__.py",  # deleted from baseline → note
            "tracks/new.py",  # new + changed → note
        ]
    )
    assert ex._runtime_written_paths == {
        "tracks/logic.py",
        "tracks/__init__.py",
        "tracks/new.py",
    }


def test_boundary_clears_self_write_set_after_absorb(tmp_path):
    """检查边界清集：吸收后的残留声明不得跨边界存活。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    ex._runtime_written_paths.add("tracks/logic.py")  # 模拟未走 note 的直注
    (repo / "tracks" / "logic.py").write_text("X = 2\n", encoding="utf-8")
    ex._fail_fast_on_code_drift()  # 吸收
    assert ex._runtime_written_paths == set()
    # 之后操作者再改 → 外来 → 换手
    (repo / "tracks" / "logic.py").write_text("X = 3  # operator\n", encoding="utf-8")
    with pytest.raises(RuntimeCodeDriftError):
        ex._fail_fast_on_code_drift()


def test_boundary_clears_set_even_without_drift(tmp_path):
    """无漂移的检查也清集——上一窗口的声明已全部结算。"""
    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    ex._runtime_written_paths.add("tracks/logic.py")
    ex._fail_fast_on_code_drift()  # no drift
    assert ex._runtime_written_paths == set()


def test_package_removal_with_live_baseline_hands_over(tmp_path):
    """R1 major 回归：基线存在而 tracks/ 整包消失 → 外来漂移换手（runtime
    自身没有整包删除路径；旧 B43 行为同样换手）。"""
    import shutil

    repo = _repo_with_tracks_pkg(tmp_path)
    ex = _executor(repo)
    shutil.rmtree(repo / "tracks")
    with pytest.raises(RuntimeCodeDriftError):
        ex._fail_fast_on_code_drift()
    events = [e for e in ex.store.events("RUN-DRIFT") if e.type == "code.drift"]
    assert events[-1].payload["reason"] == "handover"
    assert events[-1].payload["foreign_paths"] == ["tracks/"]
