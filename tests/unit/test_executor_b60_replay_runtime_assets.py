"""B60 (#76): writer worktree replay 不得携带 runtime-asset 符号链接。

run 01M0S0FQ T-001（B59 修复后 retry）：``ensure_runtime_assets`` 在 agent
运行**前**把 ``.venv`` 以符号链接放进 devon worktree；canonical
``.gitignore`` 的 ``.venv/`` 尾斜杠模式只匹配目录、不匹配符号链接，于是
replay 的 ``git add -A`` 把链接 stage 进 diff，``git apply`` 到主树被拒
（主树 ``.venv`` 是真实目录），mirror 兜底 ``copy2`` 目录 → [Errno 21]
Is a directory → 三次派发 1 秒连败 escalation，run 硬阻塞。B59 的
"ignored under the canonical .gitignore" 论证只对 ``.opencode``（无尾
斜杠）成立——同根因第五个泄漏。

覆盖：replay 显式 unstage（apply 快路径 / mirror 兜底路径 / 空增量路径，
fixture 刻意用**修复前**的 ``.venv/`` 模式证明 executor 侧免疫不依赖
gitignore）+ canonical 模式合同（符号链接形态必须被 ignore）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit.helpers import git, git_repo
from tracks.executor.executor import Executor
from tracks.executor.worktree import (
    create_devon_worktree,
    ensure_runtime_assets,
)
from tracks.store import Store

# 修复前的 canonical 模式形态：尾斜杠只匹配目录，不匹配符号链接。
# executor 侧防线必须在这种形态下依然成立。
_PRE_FIX_GITIGNORE = ".venv/\n.opencode\n"


def _b60_writer_worktree(tmp_path: Path) -> tuple[Executor, Path, object, Path]:
    """Main repo + devon worktree，复刻 _open_writer_worktree 的完整顺序：
    checkout HEAD → ensure_runtime_assets（先建链接，后跑 agent）。"""
    repo = git_repo(tmp_path)
    (repo / ".gitignore").write_text(_PRE_FIX_GITIGNORE, encoding="utf-8")
    # 主树 runtime assets：真实目录（.venv 带 bin/python，模拟生产环境）
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / ".opencode").mkdir()
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "gitignore")

    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    run_id = "RUN-B60"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    ex = Executor(store, repo, run_id)

    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    handle = create_devon_worktree(str(repo), sha, run_id, "T-001")
    ensure_runtime_assets(str(repo), handle.path)
    return ex, repo, handle, Path(handle.path)


# -- replay 排除 runtime assets -----------------------------------------------------


def test_replay_apply_path_excludes_runtime_asset_symlinks(tmp_path):
    """B60 主修复：agent 有真实增量时，replay diff 只携带 agent 文件——
    `.venv` 符号链接被显式 unstage，apply 快路径成功，主树 `.venv`
    仍是真实目录（绝不被链接替换）。"""
    ex, repo, handle, wt = _b60_writer_worktree(tmp_path)
    (wt / "tracks").mkdir()
    (wt / "tracks" / "app.py").write_text("IMPLEMENTED_IF = 'IF-1'\n", encoding="utf-8")

    error, had_delta = ex._replay_worktree_to_main(handle)

    assert error is None
    assert had_delta is True
    assert (repo / "tracks" / "app.py").read_text(encoding="utf-8")
    assert (repo / ".venv").is_dir() and not (repo / ".venv").is_symlink()
    assert ".venv" not in git(repo, "status", "--porcelain").stdout


def test_replay_mirror_fallback_excludes_runtime_asset_symlinks(tmp_path):
    """B60 生产失败签名：apply 被拒（主树已有同名 untracked 冲突文件）走
    mirror 兜底——修复前 `.venv` 在 changed 列表里，copy2 目录报 Errno 21；
    修复后 mirror 只见 agent 文件，覆盖冲突文件，无错误。"""
    ex, repo, handle, wt = _b60_writer_worktree(tmp_path)
    (wt / "tracks").mkdir()
    (wt / "tracks" / "app.py").write_text("worktree content\n", encoding="utf-8")
    # 主树同名 untracked 文件 → git apply 拒绝 → _mirror_worktree_changes
    (repo / "tracks").mkdir(exist_ok=True)
    (repo / "tracks" / "app.py").write_text("operator content\n", encoding="utf-8")

    error, had_delta = ex._replay_worktree_to_main(handle)

    assert error is None, f"mirror must not choke on runtime assets: {error}"
    assert had_delta is True
    assert (repo / "tracks" / "app.py").read_text(encoding="utf-8") == "worktree content\n"
    assert (repo / ".venv").is_dir() and not (repo / ".venv").is_symlink()


def test_replay_empty_delta_with_only_asset_links_is_clean(tmp_path):
    """B60 空增量路径：worktree 里只有 runtime-asset 链接、agent 无改动时，
    replay 是干净 no-op（`(None, False)`），不产生任何主树变更。"""
    ex, repo, handle, _wt = _b60_writer_worktree(tmp_path)
    before = git(repo, "status", "--porcelain").stdout

    error, had_delta = ex._replay_worktree_to_main(handle)

    assert error is None
    assert had_delta is False
    assert git(repo, "status", "--porcelain").stdout == before


# -- canonical .gitignore 合同 ------------------------------------------------------


def test_canonical_gitignore_matches_venv_symlink_form(tmp_path):
    """B60 第二道防线：canonical `.gitignore` 的 `.venv` 模式必须同时匹配
    目录与符号链接形态（尾斜杠形态正是 #76 的漏洞——check-ignore 对
    symlink 不命中，add -A 就会吸收它）。"""
    repo = tmp_path / "gignore"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t.tt")
    git(repo, "config", "user.name", "T")
    canonical = Path(__file__).resolve().parents[2] / ".gitignore"
    (repo / ".gitignore").write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "canonical gitignore")
    target = tmp_path / "venv_target"
    target.mkdir()
    (repo / ".venv").symlink_to(target)

    check = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", ".venv"],
        capture_output=True,
        check=False,
    )

    assert check.returncode == 0, "canonical .gitignore must ignore the .venv symlink form"


# -- #173：replay 落盘路径进入自有写入集 --------------------------------------------


def test_replay_notes_written_paths_as_runtime_owned(tmp_path):
    """#173：replay 把增量路径记入 ``_runtime_written_paths``（只留
    tracks/**.py）——v0.9 dogfood 的 Devon GREEN 落盘由此被漂移检查按
    自有写域吸收，消除每边界 M7 换手税。

    主仓必须带 tracks/ 包（漂移检查只对 tracks/**.py 取基线；B60 架子的
    宿主形态 repo 无包，note 是 no-op——那是另一条已测路径）。"""
    repo = git_repo(tmp_path)
    pkg = repo / "tracks"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "app.py").write_text("BASE = 0\n", encoding="utf-8")
    git(repo, "add", "tracks")
    git(repo, "commit", "-m", "tracks package")

    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    run_id = "RUN-173"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    ex = Executor(store, repo, run_id)

    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    handle = create_devon_worktree(str(repo), sha, run_id, "T-001")
    ensure_runtime_assets(str(repo), handle.path)
    wt = Path(handle.path)

    (wt / "tracks" / "app.py").write_text("IMPLEMENTED_IF = 'IF-2'\n", encoding="utf-8")
    (wt / "tests" / "unit").mkdir(parents=True, exist_ok=True)
    (wt / "tests" / "unit" / "test_app_red.py").write_text("def test_x():\n    assert 0\n")

    error, _had_delta = ex._replay_worktree_to_main(handle)

    assert error is None
    assert ex._runtime_written_paths == {"tracks/app.py"}
    # 落盘后的漂移检查走吸收路径（不抛、基线前移）
    ex._fail_fast_on_code_drift()
    drift = [e for e in ex.store.events(run_id) if e.type == "code.drift"]
    assert drift[-1].payload["reason"] == "self_write_rebaseline"
    assert drift[-1].payload["paths"] == ["tracks/app.py"]
