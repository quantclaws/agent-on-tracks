"""#176 OOB rev2（2026-09-28，stealth 咨询后重构）：manifest 和解与 prompt 平移。

事故（v0.10 run 01M3E7SAANXKW1V73W8B8Q3G86，第三次活体）：写作者输出跨派发
窗口滞留主树（#138 prompt 主树绝对路径诱导 + #176 审计盲区）→ manifest 失配
→ escalation。修复两点：

1. 和解缝（result_payload）：dirty-exists 宽限从"仅零新文件时启用"泛化为
   "对缺失子集生效"；manifest include 路径先经 _confined_repo_path 归一化
   （拒绝绝对路径与 ``..`` 穿越）再做前缀匹配。
2. 根因缝（opencode_prompt/paths）：worktree 居住的派发，prompt 的 target
   路径平移到 worktree 根（此前只有审计白名单平移，prompt 用主树绝对路径
   与 agent 的 worktree cwd 自相矛盾）。
"""

import subprocess

import pytest


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "pyproject.toml").write_text("# host\n", encoding="utf-8")
    (main / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return main


def _executor(repo):
    from tracks.executor.result_payload import ResultPayloadMixin

    class _Ex(ResultPayloadMixin):
        def __init__(self):
            self.repo = repo

        def _dirty_files(self):
            proc = _git(self.repo, "status", "--porcelain", "-uall")
            return {
                line[3:].split(" -> ", 1)[-1].strip().strip('"')
                for line in proc.stdout.splitlines()
                if line.strip()
            }

    return _Ex()


# -- _confined_repo_path --------------------------------------------------------


def test_confined_repo_path_normalizes_and_rejects_escape():
    from tracks.executor.result_payload import _confined_repo_path

    assert _confined_repo_path("tests/e2e/./x.py") == "tests/e2e/x.py"
    assert _confined_repo_path("tests/e2e/../../outside.py") is None
    assert _confined_repo_path("/abs/path.py") is None
    assert _confined_repo_path("") is None
    assert _confined_repo_path("..") is None


# -- 和解：部分归属 + 缺失子集宽限 ----------------------------------------------


def test_partial_window_diff_tolerates_stranded_dirty_manifest_paths(repo):
    """4 个本窗口归属 + 46 个前窗口滞留（dirty 存在）→ 无失配（第三次事故
    的精确形态：旧实现 `not test_files` 前置条件跳过宽限导致 escalation）。"""
    stranded = repo / "tests" / "integration" / "test_version_gate.py"
    stranded.parent.mkdir(parents=True)
    stranded.write_text("def test_gate():\n    assert True\n", encoding="utf-8")

    ex = _executor(repo)
    attributed = ["tests/e2e/test_workbench_ui.py"]
    include = attributed + ["tests/integration/test_version_gate.py"]
    test_files, manifest_error = ex._shield_manifest_check(attributed, include)
    assert manifest_error is None
    assert test_files == sorted(include)


def test_missing_clean_tracked_path_still_fails(repo):
    """manifest 声明了磁盘上干净（非 dirty）的路径 → 宽限不适用，失配保留。"""
    tracked = repo / "tests" / "tracked.py"
    tracked.parent.mkdir()
    tracked.write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "t")

    ex = _executor(repo)
    _t, manifest_error = ex._shield_manifest_check([], ["tests/tracked.py"])
    assert manifest_error is not None


def test_symlinked_manifest_path_not_tolerated(repo):
    real = repo / "tests" / "real.py"
    real.parent.mkdir()
    real.write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "link.py").symlink_to(real)

    ex = _executor(repo)
    _t, manifest_error = ex._shield_manifest_check([], ["tests/link.py"])
    assert manifest_error is not None


def test_include_paths_confine_traversal_entries(repo):
    """穿越形态的 include 被 _shield_include_paths 丢弃 → 失配 fail-closed。"""
    result = {
        "artifact_manifest": {
            "include": [
                {"path": "tests/e2e/../../evil.py"},
                {"path": "/etc/passwd"},
                {"path": "tests/e2e/ok.py"},
            ]
        }
    }
    include = _executor(repo)._shield_include_paths(result, ["tests/e2e/", "tests/integration/"])
    assert include == ["tests/e2e/ok.py"]


# -- prompt 平移 ----------------------------------------------------------------


def test_prompt_rebases_doc_path_to_worktree_root(repo, tmp_path):
    from tracks.effects.opencode import OpencodeBackend

    b = OpencodeBackend(repo, "v0.10")
    wt = tmp_path / "wt"
    doc = repo / ".tracks" / "projects" / "v0.10" / "spec.md"
    prompt = b._prompt("sage", "DRAFT", "spec.md", doc, None, root=wt)
    assert str(wt / ".tracks/projects/v0.10/spec.md") in prompt
    assert str(doc) not in prompt


def test_prompt_without_root_keeps_main_tree_target(repo):
    from tracks.effects.opencode import OpencodeBackend

    b = OpencodeBackend(repo, "v0.10")
    doc = repo / ".tracks" / "projects" / "v0.10" / "spec.md"
    prompt = b._prompt("sage", "DRAFT", "spec.md", doc, None)
    assert str(doc) in prompt


def test_prompt_docs_set_rebased_to_worktree_root(repo, tmp_path):
    from tracks.effects.opencode import OpencodeBackend

    b = OpencodeBackend(repo, "v0.10")
    wt = tmp_path / "wt"
    assignment = {"docs": ["spec.md", "acceptance.md"]}
    prompt = b._prompt("sage", "DRAFT", None, None, assignment, root=wt)
    vdir = repo / ".tracks" / "projects" / "v0.10"
    # 变异可判别（R2-01）：_rebased 退化为恒等时下列断言必失败——
    # 断言完整平移路径表，不用 or 析取。
    for name in ("spec.md", "acceptance.md"):
        assert str(wt / ".tracks" / "projects" / "v0.10" / name) in prompt
        assert str(vdir / name) not in prompt
