"""B86/B88（#77 批次 1）：非派发命令紧循环熔断（STALL）。

r1 实证（B86/B88 实录）：decide() 在守卫 bug 下会以 3Hz/280ms 无限重发同一
kind 命令（commit_green 烧 1600+ 事件、select_task 烧 900+，95% CPU），直到
操作者撞见杀进程。这类"非派发命令紧循环"此前没有任何熔断——agent 派发有
budget/escalation（RunBreaker / max_dispatches），命令层没有。

合同（保守）：
- 跟踪连续发出的 command.issued 中**同 kind 且同 task_id/gate 标识**的序列；
- 任何非 command.issued 事件（outcome.received / verdict.* / taskgraph.committed
  / task.started / worktree.* / evidence.* / test.selected / loop.aborted /
  human.* 等进展事件）出现即清零计数；
- 计数 ≥ STALL_COMMAND_LIMIT → 停车：append loop.aborted(reason=command_stall)
  + 打印处置指引 banner + 抛 CommandStallError（CLI 捕获转为非零退出码，
  与 B43 RuntimeCodeDriftError 处理链一致）。

dispatch_agent 类命令不计入（它有独立的 attempt budget/escalation 语义，
重复派发是 retry 语义不是 stall；本熔断只针对被守卫 bug 卡住的非派发命令
kind，如 commit_green / select_task / run_task_gates / commit_taskgraph）。

计数是循环内内存状态，无需持久化（崩溃重启清零可接受——紧循环 20 次在
数秒内达成）。挂在 executor._emit 的统一漏斗上，事件一进一出即计数，无
额外事件流扫描开销。
"""

from __future__ import annotations

# B86 3Hz：20 次 ≈ 7 秒；B88 280ms：20 次 ≈ 6 秒——均在人眼可见前熔断。
# 正常 gate 链最慢的合法重发（dispatch 级 retry / verdict.failed 重派）远
# 低于此，且每次合法重发之间必有进展事件清零。
STALL_COMMAND_LIMIT = 20

# 最后保留的 command_id 摘要条数（loop.aborted detail 引用）。
_STALL_ID_SUMMARY = 3


class CommandStallError(RuntimeError):
    """run_loop 检测到非派发命令紧循环（STALL），已落 loop.aborted 后抛出。"""


class CommandStallTracker:
    """连续同标识 command.issued 计数；进展事件清零。

    标识 = (kind, 参数中的 task_id 或 gate)；两参皆无时退化为 kind 单键。
    dispatch_agent 不计入也不清零（不计入语义）。
    """

    def __init__(self, limit: int = STALL_COMMAND_LIMIT):
        self._limit = limit
        self._key: tuple | None = None
        self._kind: str | None = None
        self._count = 0
        self._last_ids: list[str] = []

    def _reset(self) -> None:
        self._key = None
        self._kind = None
        self._count = 0
        self._last_ids = []

    @staticmethod
    def _issued_key(payload: dict) -> tuple | None:
        """command.issued 载荷 → (kind, task_id|gate) 标识；非 command.issued
        或 dispatch_agent 返回 None（前者走 _reset，后者不计入）。"""
        command = payload.get("command") or {}
        kind = command.get("kind") or ""
        if kind == "dispatch_agent":
            return None
        params = command.get("params") or {}
        task_id = params.get("task_id")
        if task_id:
            return (kind, "task", str(task_id))
        gate = params.get("gate")
        if gate:
            return (kind, "gate", str(gate))
        return (kind,)

    def observe(self, event_type: str, payload: dict) -> None:
        """从 executor._emit 漏斗逐事件观察。"""
        if event_type != "command.issued":
            # 任何进展事件（非 command.issued）清零计数。
            self._reset()
            return
        key = self._issued_key(payload)
        if key is None:
            return  # dispatch_agent：不计入也不清零。
        cid = (payload.get("command") or {}).get("command_id")
        if key == self._key:
            self._count += 1
        else:
            self._key = key
            self._kind = key[0]
            self._count = 1
        if cid:
            self._last_ids.append(str(cid))
            self._last_ids = self._last_ids[-_STALL_ID_SUMMARY:]

    def should_trip(self) -> bool:
        return self._count >= self._limit

    def summary(self) -> str:
        """loop.aborted detail：kind + 计数 + 最后 command_id 摘要。"""
        ids = ", ".join(self._last_ids) if self._last_ids else "-"
        return f"kind={self._kind or '?'} count={self._count} last_command_ids=[{ids}]"
