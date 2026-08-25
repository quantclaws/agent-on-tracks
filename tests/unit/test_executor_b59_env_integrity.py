"""B59 (#75): 回退/重启后环境完整性单元测试。

run 01M0S0FQ 实证：被丢弃的 M-IMPL 周期的主树残留（tracks/project.py）
穿越一切 rollback / retry --clear-evidence 存活，经 M-TEST freeze 泄入新
基线，并让 Devon 在污染的主树里测出假 GREEN（attempt 2 主树 10 passed /
干净 worktree 6 failed）。三道防线中的两道在此覆盖（第三道 .venv 链接见
test_worktree_unit.py）：

1. rollback 时按 write-scope quarantine（stash，绝不销毁）残留（含 untracked）；
2. commit_tests 拒绝吸收 tests/ 下一切脏文件（fail closed，设计流中 Shield
   WRITE 产物在管线内已提交，freeze 时 tests/ 必须全干净 —— Prism OOB R1
   Blocker 01），由 m_test reducer 路由 awaiting_human（见 test_machine_m_test.py）。
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git, git_repo
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store


def _b59_executor(tmp_path: Path):
    """Executor on a repo whose run has one dispatched T-001 with the
    canonical manifest write-scope (allowed_paths + red_test_paths)."""
    repo = git_repo(tmp_path)
    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    run_id = "RUN-B59"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-IMPL"})
    store.append(
        run_id,
        "v0.1",
        "task.started",
        {
            "task_id": "T-001",
            "manifest": {
                "allowed_paths": ["tracks/project.py", "tests/unit"],
                "red_test_paths": ["tests/unit/test_red.py"],
            },
        },
    )
    return Executor(store, repo, run_id), repo, store, run_id


def _commit_baseline(repo: Path) -> None:
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")


# -- write-scope 收集与匹配 --------------------------------------------------------


def test_rollback_write_scope_unions_task_manifests(tmp_path):
    """B59: scope = 所有已派发任务 manifest 的 allowed_paths ∪ red_test_paths。"""
    ex, *_ = _b59_executor(tmp_path)
    assert ex._rollback_write_scope() == [
        "tests/unit",
        "tests/unit/test_red.py",
        "tracks/project.py",
    ]


def test_path_in_write_scope_exact_prefix_glob(tmp_path):
    """B59: 精确文件 / 目录前缀 / glob 三种 manifest 模式都要命中。"""
    patterns = ["tracks/project.py", "tests/unit", ".tracks/projects/**"]
    assert Executor._path_in_write_scope("tracks/project.py", patterns)
    assert Executor._path_in_write_scope("tests/unit/test_app.py", patterns)
    assert Executor._path_in_write_scope(".tracks/projects/v0.7/tasks.json", patterns)
    assert not Executor._path_in_write_scope("README.md", patterns)
    assert not Executor._path_in_write_scope("tests/integration/test_x.py", patterns)


# -- rollback residue quarantine ---------------------------------------------------


def test_quarantine_stashes_only_scope_residue(tmp_path):
    """B59: scope 内残留（已跟踪修改 + untracked，Prism OOB R1 Blocker 01）
    被 stash（树回到基线）；scope 外修改不动。"""
    ex, repo, *_ = _b59_executor(tmp_path)
    (repo / "tracks").mkdir()
    (repo / "tracks" / "project.py").write_text("base\n", encoding="utf-8")
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_app.py").write_text("base\n", encoding="utf-8")
    _commit_baseline(repo)
    # 被丢弃周期的实现残留（scope 内已跟踪）+ untracked 残留（scope 内）
    # + 无关修改（scope 外）
    (repo / "tracks" / "project.py").write_text("residue\n", encoding="utf-8")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    (repo / "tests" / "unit" / "new_untracked.py").write_text("x\n", encoding="utf-8")

    quarantined = ex._quarantine_rollback_residue()

    assert sorted(quarantined) == ["tests/unit/new_untracked.py", "tracks/project.py"]
    status = git(repo, "status", "--porcelain").stdout
    assert "tracks/project.py" not in status  # stashed 回基线
    assert "tests/unit/new_untracked.py" not in status  # untracked 也隔离
    assert "README.md" in status  # scope 外不动
    assert "trac B59 rollback residue quarantine" in git(repo, "stash", "list").stdout


def test_quarantine_noop_on_clean_tree(tmp_path):
    """B59: 干净树 / 无 scope 内残留时是 no-op（返回空，不动树）。"""
    ex, repo, *_ = _b59_executor(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")  # scope 外
    assert ex._quarantine_rollback_residue() == []
    assert "README.md" in git(repo, "status", "--porcelain").stdout


def test_rollback_stage_emits_quarantined_and_cleans_m_impl_residue(tmp_path):
    """B59: _do_rollback_stage 在 M-IMPL 回退时隔离主树残留并把清单写进
    stage.rolled_back payload。"""
    ex, repo, store, run_id = _b59_executor(tmp_path)
    (repo / "tracks").mkdir()
    (repo / "tracks" / "project.py").write_text("base\n", encoding="utf-8")
    _commit_baseline(repo)
    (repo / "tracks" / "project.py").write_text("residue\n", encoding="utf-8")

    cmd = Command(
        kind="rollback_stage",
        params={"to_stage": "M-DESIGN", "reason": "lineage"},
        command_id="C9",
    )
    ex._do_rollback_stage(cmd, store.state(run_id), "T-001", False)

    rolled = [e for e in store.events(run_id) if e.type == "stage.rolled_back"]
    assert rolled and rolled[-1].payload["quarantined"] == ["tracks/project.py"]
    assert "tracks/project.py" not in git(repo, "status", "--porcelain").stdout


# -- commit_tests 溯源收紧 ----------------------------------------------------------


def test_commit_tests_rejects_tracked_residue_under_tests(tmp_path):
    """B59: freeze 时 tests/ 下已跟踪文件的修改是废弃周期残留 —— fail
    closed 发 verdict.failed(test_freeze_contamination)，绝不吸收进基线。"""
    ex, repo, store, run_id = _b59_executor(tmp_path)
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_app.py").write_text("base\n", encoding="utf-8")
    _commit_baseline(repo)
    (repo / "tests" / "unit" / "test_app.py").write_text("residue\n", encoding="utf-8")

    cmd = Command(
        kind="commit_tests", params={"stage": "M-TEST"}, command_id="C6"
    )
    ex._do_commit_tests(cmd, store.state(run_id), None, False)

    failures = [
        e for e in store.events(run_id) if e.type == "verdict.failed"
    ]
    assert failures, "must fail closed on tracked tests/ residue"
    assert failures[-1].payload["check"] == "test_freeze_contamination"
    assert "tests/unit/test_app.py" in failures[-1].payload["evidence"]
    assert not [
        e for e in store.events(run_id) if e.type == "test.committed"
    ]


def test_commit_tests_rejects_untracked_residue_under_tests(tmp_path):
    """B59 (Prism OOB R1 Blocker 01): 设计流中 Shield WRITE 产物在管线内
    已提交，freeze 时 tests/ 必须全干净 —— untracked 文件同样是废弃周期
    残留，同样 fail closed（原版放行 untracked 正是 01M0S0FQ 三次 freeze
    commit 吸收脏文件的污染向量）。"""
    ex, repo, store, run_id = _b59_executor(tmp_path)
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_app.py").write_text("base\n", encoding="utf-8")
    _commit_baseline(repo)
    (repo / "tests" / "unit" / "test_new.py").write_text("def test_x(): pass\n", encoding="utf-8")

    cmd = Command(
        kind="commit_tests", params={"stage": "M-TEST"}, command_id="C6"
    )
    ex._do_commit_tests(cmd, store.state(run_id), None, False)

    failures = [
        e for e in store.events(run_id) if e.type == "verdict.failed"
    ]
    assert failures, "must fail closed on untracked tests/ residue too"
    assert failures[-1].payload["check"] == "test_freeze_contamination"
    assert "tests/unit/test_new.py" in failures[-1].payload["evidence"]
    assert not [
        e for e in store.events(run_id) if e.type == "test.committed"
    ]


def test_commit_tests_freezes_clean_tests_tree(tmp_path):
    """B59 对照组：tests/ 全干净（设计流 —— WRITE 管线内已提交）时正常
    freeze，无 contamination 停车。"""
    ex, repo, store, run_id = _b59_executor(tmp_path)
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_app.py").write_text("base\n", encoding="utf-8")
    _commit_baseline(repo)

    cmd = Command(
        kind="commit_tests", params={"stage": "M-TEST"}, command_id="C6"
    )
    ex._do_commit_tests(cmd, store.state(run_id), None, False)

    assert [
        e for e in store.events(run_id) if e.type == "test.committed"
    ], "clean tests/ must freeze normally"
    assert not [
        e
        for e in store.events(run_id)
        if e.type == "verdict.failed"
        and e.payload.get("check") == "test_freeze_contamination"
    ]
