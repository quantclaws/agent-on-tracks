"""B43（#45）：trac run 进程的代码版本戳。

r1 实证（2026-08-19/20，run 01M0AMKV 异常 #1）：操作者在运行中进程外修
改 ``tracks/**`` 代码（§2f 补丁），Python 不热加载——进程继续跑旧逻辑，
EXIT 校验用旧代码再次误报 blockquote 行 → escalation + kill/restart 才
恢复，浪费一轮 EXIT 校验 + 人工干预。

合同：``trac run`` 进程启动时对 ``tracks/**/*.py`` 工作树内容取指纹；
run_loop 每轮派发前复核，指纹漂移即 fail-fast（``RuntimeCodeDriftError``）
——提示重启 ``trac run``，绝不带着旧逻辑继续派发。与 D-36 OOB 通道（#43）
配合：操作者带补丁提交（Tracks-OOB trailer）→ 本检查停车 → 重启后新代
码生效，回滚语义不受影响。

无 ``tracks/`` 包的仓库（宿主项目）不受影响（指纹为 None，检查跳过）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class RuntimeCodeDriftError(RuntimeError):
    """trac run 进程存活期间 tracks/** 代码发生了变化。"""


def code_stamp(repo: Path) -> str | None:
    """``repo/tracks/**/*.py`` 工作树内容的确定性指纹。

    覆盖文件集合与逐文件内容（sha256），与 mtime 无关（touch 不触发）；
    排除 ``__pycache__``。目录不存在（宿主项目）返回 None。
    """
    pkg = Path(repo) / "tracks"
    if not pkg.is_dir():
        return None
    parts: list[str] = []
    for p in sorted(pkg.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        parts.append(f"{p.relative_to(repo).as_posix()}:{digest}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


DRIFT_MESSAGE = (
    "tracks/** code changed since this `trac run` process started — "
    "the running process still executes the OLD logic. Restart "
    "`trac run` to pick up the change (committed or not). "
    "Aborting before further dispatches (B43/#45 fail-fast)."
)
