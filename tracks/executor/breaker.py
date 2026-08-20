"""B44（#46）：run 级熔断器——杜绝整夜无声研磨。

r1 实证（run 01M0AMKV，2026-08-19/20）：最后 10 小时零任务进展仍在持续
消耗（3 次回滚 + 评审空转 + over_reach 整体作废 1h21m + 无限重派），任何
机制都不叫停，直到人工早上发现。用户裁定 2026-08-20：熔断条件任一触发
即 awaiting_human/escalation 并附损耗摘要，停止派发等待人工。

条件（环境变量可调，0 = 关闭该条件）：
- ``TRAC_BREAKER_ROLLBACKS``（默认 2）：run 内累计 stage.rolled_back 数；
- ``TRAC_BREAKER_TASK_FAILURES``（默认 6）：同一 task 的 verdict.failed
  累计数（与单 substate 的 3-attempt 预算区分——r1 的灾难正是跨 substate
  /跨阶段反复失败从未被 run 级看到）；
- ``TRAC_BREAKER_DISPATCHES``（默认 120）：dispatch_agent 累计数。

计数窗口：自最近一次 ``human.retry`` / ``human.recover``（人工重置点）之
后的全部事件——``trac retry`` 即恢复路径（清 escalation + 重置计数）。
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable

_RESET_EVENTS = ("human.retry", "human.recover")


def _threshold(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, "").strip() or default)
    except ValueError:
        return default


class RunBreaker:
    """run 级损耗计数与熔断判定（计数窗口：上次人工重置点之后）。"""

    def __init__(
        self,
        rollbacks: int = 0,
        dispatches: int = 0,
        task_failures: dict[str, int] | None = None,
        verdict_failed: int = 0,
        failure_classes: Counter | None = None,
    ):
        self.rollbacks = rollbacks
        self.dispatches = dispatches
        self.task_failures: dict[str, int] = dict(task_failures or {})
        self.verdict_failed = verdict_failed
        self.failure_classes: Counter = failure_classes or Counter()

    # -- 种子：从事件流重建（进程重启后计数不丢） ---------------------------

    @classmethod
    def from_events(cls, events: Iterable) -> RunBreaker:
        breaker = cls()
        for ev in events:
            breaker.note(ev.type, ev.payload or {})
        return breaker

    # -- 增量：executor._emit 的统一漏斗 ------------------------------------

    def _reset(self) -> None:
        """人工重置点后的窗口清零（Prism batch2 advisory 2：显式重置，
        不重调 __init__——后者日后加必填参/副作用会静默破裂）。"""
        self.rollbacks = 0
        self.dispatches = 0
        self.task_failures = {}
        self.verdict_failed = 0
        self.failure_classes = Counter()

    def note(self, type: str, payload: dict) -> None:
        if type in _RESET_EVENTS:
            # 人工重置点：计数窗口清零（进程内在途不会出现——retry 时
            # run 已停；这是防御性处理，保证语义完整）。
            self._reset()
            return
        if type == "stage.rolled_back":
            self.rollbacks += 1
        elif type == "command.issued":
            command = payload.get("command") or {}
            if command.get("kind") == "dispatch_agent":
                self.dispatches += 1
        elif type == "verdict.failed":
            self.verdict_failed += 1
            cls_name = payload.get("failure_class") or payload.get("check") or "unknown"
            self.failure_classes[cls_name] += 1
            task_id = payload.get("task_id")
            if task_id:
                self.task_failures[str(task_id)] = (
                    self.task_failures.get(str(task_id), 0) + 1
                )

    # -- 判定 ---------------------------------------------------------------

    def _worst_task(self) -> tuple[str | None, int]:
        """(失败最多的 task_id, 次数)；无任务失败时 (None, 0)。"""
        worst_task, worst = None, 0
        for task_id, count in self.task_failures.items():
            if count > worst:
                worst_task, worst = task_id, count
        return worst_task, worst

    def _trip_condition(self) -> str | None:
        """首个越限条件（描述串）；未越限返回 None。"""
        limit_rollbacks = _threshold("TRAC_BREAKER_ROLLBACKS", 2)
        if limit_rollbacks > 0 and self.rollbacks >= limit_rollbacks:
            return f"rollbacks>={limit_rollbacks}"
        limit_task = _threshold("TRAC_BREAKER_TASK_FAILURES", 6)
        if limit_task > 0:
            worst_task, worst = self._worst_task()
            if worst_task is not None and worst >= limit_task:
                return f"task_failures:{worst_task}>={limit_task}"
        limit_dispatches = _threshold("TRAC_BREAKER_DISPATCHES", 120)
        if limit_dispatches > 0 and self.dispatches >= limit_dispatches:
            return f"dispatches>={limit_dispatches}"
        return None

    def evaluate(self) -> dict | None:
        """任一条件越限 → 返回 run.breaker_tripped 事件的 payload；否则 None。"""
        condition = self._trip_condition()
        if condition is None:
            return None

        top_classes = dict(
            sorted(self.failure_classes.items(), key=lambda kv: -kv[1])[:5]
        )
        report = (
            f"run breaker tripped ({condition}): "
            f"rollbacks={self.rollbacks}, dispatches={self.dispatches}, "
            f"verdict.failed={self.verdict_failed}, "
            f"task_failures={self.task_failures or '{}'}, "
            f"failure_classes={top_classes or '{}'} — "
            "dispatching halted; inspect `trac replay` for the loss pattern; "
            "`trac retry --actor <name>` resets this breaker window."
        )
        return {
            "condition": condition,
            "rollbacks": self.rollbacks,
            "dispatches": self.dispatches,
            "verdict_failed": self.verdict_failed,
            "task_failures": self.task_failures,
            "failure_classes": top_classes,
            "report": report,
        }
