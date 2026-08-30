"""B64 (#82): Shield 写域对 Devon RED 单测的确定性所有权墙。

run 01M0S0FQ T-002（2026-08-25）：test_defect 误路由把 Shield 派进 Devon 的
RED 单测（用户裁定：Devon 的 RED 单测只能 Devon 改，经 red_defect → RED
re-pin）。提示词纪律（adac580）约束判断，但 Shield 的审计白名单来自粗粒度
[layout.shield] 目录，确定性包含 RED 单测路径。B64 补硬排除——**数据驱动，
零硬编码**：禁写域 = 本 run 全部 task.started manifest 的 red_test_paths
（Archer 制定的语言中立数据），由 executor 注入 assignment，Auditor 优先
否决（exact/prefix/glob，先于一切 allow 授权）。
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import git_repo
from tracks.effects.audit import Auditor
from tracks.executor.executor import Executor
from tracks.store import Store


def _b64_executor(tmp_path: Path):
    """Executor on a store with two dispatched tasks' manifests."""
    repo = git_repo(tmp_path)
    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    run_id = "RUN-B64"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-IMPL"})
    for task_id, red in (
        ("T-001", ["tests/unit/test_adapter.py"]),
        ("T-002", ["tests/unit/test_reference_pytest_adapter.py"]),
    ):
        store.append(
            run_id,
            "v0.1",
            "task.started",
            {
                "task_id": task_id,
                "manifest": {
                    # allowed_paths must NOT leak into the red scope
                    "allowed_paths": ["tracks/adapters/impl.py"],
                    "red_test_paths": red,
                },
            },
        )
    return Executor(store, repo, run_id), repo, store, run_id


# -- red-test scope 收集 -------------------------------------------------------------


def test_red_test_scope_unions_only_red_test_paths(tmp_path):
    """B64: scope = 所有已派发任务 manifest 的 red_test_paths 并集；
    allowed_paths 不混入。"""
    ex, *_ = _b64_executor(tmp_path)
    assert ex._red_test_scope() == [
        "tests/unit/test_adapter.py",
        "tests/unit/test_reference_pytest_adapter.py",
    ]


def test_shield_fix_assignment_carries_red_scope(tmp_path):
    """B64: M-IMPL 的 Shield WRITE（SHIELD_FIX）assignment 注入
    forbidden_paths = red scope；M-TEST 不注入（Shield 是基线作者）。"""
    ex, repo, store, run_id = _b64_executor(tmp_path)

    def _params(stage):
        return {
            "role": "shield",
            "substate": "WRITE",
            "stage": stage,
            "assignment": {"test_tasks": [{"ac_id": "AC-1"}]},
        }

    state = store.state(run_id)
    state.stage = "M-IMPL"
    params = _params("M-IMPL")
    ex._enrich_shield_write_params(params, state)
    assert params["assignment"]["forbidden_paths"] == ex._red_test_scope()

    state = store.state(run_id)
    state.stage = "M-TEST"
    params = _params("M-TEST")
    ex._enrich_shield_write_params(params, state)
    assert "forbidden_paths" not in params["assignment"]


# -- Auditor 所有权否决 ---------------------------------------------------------------


def _auditor(tmp_path, allowed, forbidden):
    repo = tmp_path / "audit-repo"
    repo.mkdir(exist_ok=True)
    return Auditor(repo, allowed=[repo / a if not a.startswith(".") else a for a in allowed], forbidden=forbidden)


def test_forbidden_veto_beats_allow_grant(tmp_path):
    """B64: forbidden 命中（exact/prefix/glob）优先否决——即使 allow 的
    目录前缀本可覆盖（Shield 的 [layout] 目录粒度场景）。"""
    repo = tmp_path / "r1"
    repo.mkdir()
    aud = Auditor(
        repo,
        allowed=[str(repo / "tests")],  # coarse layout grant
        forbidden=["tests/unit/test_red.py"],
    )
    assert not aud._is_allowed("tests/unit/test_red.py")  # veto beats prefix
    assert aud._is_allowed("tests/unit/test_other.py")  # sibling unaffected
    assert aud._is_allowed("tests/integration/test_acc.py")


def test_forbidden_glob_and_dir_prefix(tmp_path):
    """B64: manifest 模式三态——glob 与目录前缀同样否决。"""
    repo = tmp_path / "r2"
    repo.mkdir()
    aud = Auditor(
        repo,
        allowed=[repo / "tests"],
        forbidden=["tests/unit/**", "tests/red"],
    )
    assert not aud._is_allowed("tests/unit/deep/test_x.py")  # glob
    assert not aud._is_allowed("tests/red/anything.py")  # dir prefix
    assert aud._is_allowed("tests/integration/test_y.py")


def test_audit_flags_forbidden_write_as_overreach(tmp_path):
    """B64 端到端语义：forbidden 内的运行期写入被 audit 报 over-reach
    （Shield 改 RED 单测 → 回滚 + 失败派发的机器依据）。"""
    repo = git_repo(tmp_path)
    (repo / "tests" / "unit").mkdir(parents=True)
    (repo / "tests" / "unit" / "test_red.py").write_text("def test_x(): pass\n", encoding="utf-8")
    from tests.unit.helpers import git as git_cmd

    git_cmd(repo, "add", ".")
    git_cmd(repo, "commit", "-m", "baseline")
    aud = Auditor(repo, allowed=[str(repo / "tests")], forbidden=["tests/unit/test_red.py"])
    baseline = aud.baseline()
    (repo / "tests" / "unit" / "test_red.py").write_text(
        "def test_x(): assert False  # Shield's illegal edit\n", encoding="utf-8"
    )
    assert aud.audit(baseline) is not None
    assert "test_red.py" in aud.audit(baseline)
