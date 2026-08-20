"""D-36 浅版 OOB（Out-of-Band）提交通道（issue #43）。

操作者（Human/Maestro）在 runtime 视野外对仓库的提交，通过 git commit
trailer ``Tracks-OOB: <reason>`` 显式声明后被 runtime 接受：

- 不计入 Agent 产出、不触发 over_reach 归因；
- worktree 回放不覆盖主树侧已提交的同名文件（B36 反碾压守卫，见
  ``executor._replay_worktree_to_main``）；
- executor 在 run_loop 迭代顶部观察到时发出 ``oob.accepted`` 事件
  （append-only 事件流完整性，重放一致）。

浅版边界（D-36 build-gate 裁定）：不做 dispatch_ref..HEAD 的逐提交历史
归因（深版）；静默期（run 停止期间）的变更由下一次 baseline 冻结吸收
（既有语义，FR-0170）。信任模型：信任操作者，Agent 无 git 权限，伪造
trailer 不作为设计约束。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

OOB_TRAILER = "Tracks-OOB"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def head_sha(repo: Path) -> str | None:
    """当前 HEAD 的完整 sha；无提交（孤儿仓库）时返回 None。"""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _trailer_reason(body: str) -> str | None:
    """从提交说明正文提取 ``Tracks-OOB: <reason>`` trailer。

    宽松行匹配（信任操作者，不做防伪造）：正文任意一行形如
    ``Tracks-OOB: <非空 reason>`` 即视为声明。
    """
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(OOB_TRAILER + ":"):
            reason = stripped[len(OOB_TRAILER) + 1:].strip()
            if reason:
                return reason
    return None


def commits_since(repo: Path, since_sha: str | None) -> list[dict]:
    """``since_sha..HEAD`` 的全部提交，拓扑序旧→新。

    返回 ``[{"sha", "subject", "oob": bool, "reason": str | None}]``。
    ``since_sha`` 为空、不可解析、或不再是 HEAD 祖先（历史被改写/
    rebase/amend 后的旧观察点——Prism review A1：非祖先 sha 的
    ``A..B`` 语义等价于 B 的全部可达历史，会吐全量伪事件）时返回 []。
    """
    if not since_sha:
        return []
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{since_sha}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0:
        return []
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", since_sha, "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        return []
    out = _git(
        repo,
        "log",
        "--reverse",
        "--format=%H%x1f%s%x1f%b%x1e",
        f"{since_sha}..HEAD",
    )
    commits: list[dict] = []
    for block in out.split("\x1e"):
        if not block.strip():
            continue
        parts = block.strip("\n").split("\x1f", 2)
        if len(parts) != 3:
            continue
        sha, subject, body = (p.strip("\n") for p in parts)
        reason = _trailer_reason(body)
        commits.append(
            {
                "sha": sha,
                "subject": subject,
                "oob": reason is not None,
                "reason": reason,
            }
        )
    return commits


def oob_commits(repo: Path, since_sha: str | None) -> list[dict]:
    """``since_sha..HEAD`` 中携带 ``Tracks-OOB`` trailer 的提交子集。"""
    return [c for c in commits_since(repo, since_sha) if c["oob"]]


def changed_paths(repo: Path, sha: str) -> set[str]:
    """单个提交相对其第一父提交的变更路径（NUL 分隔，路径特殊字符安全）。

    合并提交按 ``-m`` 逐父展开后取并集；OOO 操作者提交为普通提交，
    ``-m`` 行为与普通 diff-tree 一致。
    """
    out = _git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "-m",
        "-z",
        sha,
    )
    return {p for p in out.split("\0") if p}
