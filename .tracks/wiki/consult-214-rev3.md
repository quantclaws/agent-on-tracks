# 咨询：#214 RULING 消费路径 rev3 —— 裁定 verdict 直接路由（替代存储后消费）

**日期**: 2026-09-30　**提出**: Maestro (OOB 修复通道)　**状态**: 待 Archer 意见 + Prism 审查

## 1. 事实（已核实，非推测）

T-002 (v0.10 run) 的 live 故障链，全部有 event 证据：

1. Archer RULING 交付成功：`verdict.passed(check=ruling)` 携带 `ruling_outcome`（devon_side.action=redeliver_green，含 instruction + scope_boundary）。
2. 但 kernel 未把 Devon 派出去——`command.issued(role=devon)` 计数为 **0**。
3. decide() 看到的 substate 是 **DIAGNOSE**（上一周期残留），于是继续派 Prism DIAGNOSE。
4. Prism DIAGNOSE 对已裁定事项返回 unknown → failure → 再 DIAGNOSE → 死循环，直到 breaker/人工介入。

## 2. 为什么 rev1/rev2（存储后消费）结构性不成立

rev1: `check=="ruling"` 只存 `s.m_impl_ruling_outcome`，不改 substate → decide() 沿 DIAGNOSE 走，outcome 永远无人消费。
rev2 (commit 76532af): 存 outcome **并** `s.substate = "RULING"` → 期望 `_decide_m_impl_ruling_entry` 先 `_consume_ruling_outcome` 再派 Devon。

rev2 的致命伤：**kernel 是事件溯源的，State 靠重放全部 event 重建**。裁定 verdict 落地之后，流里还有后续的 `verdict.failed`（DIAGNOSE 死循环产生的），重放时它们的 routing 会把 substate 再度覆盖回 DIAGNOSE。重放的终态 = 最后一个 substate 变更事件的产物，rev2 在 verdict-passed 处设置的 RULING 存活不到 decide()。"存储 + 稍后消费"在 reducer 重放语义下要求**流里不再出现覆盖事件**，而故障循环恰恰保证会出现。

## 3. rev3 方案：裁定 verdict 在应用时直接路由

`tracks/kernel/m_impl_routing.py` `_on_m_impl_verdict_passed` 的 `check == "ruling"` 分支改为：

- `devon_side.action == "redeliver_green"` → `s.substate = "GREEN"`，`current_attempt = 0`，`last_failure = None`，把 instruction/scope/task_id 写入已有的 `s.m_impl_red_ruling` 字段
- `action == "deliver_red"` → 同上但 substate = RED
- `action` 缺失或 `hold` → `substate = "TASK_DISPATCH"`，`current_task_id = None`（进入下一任务）
- 未知 action → substate = RULING（fall through 到 Archer 再裁定，操作者可见）

decide() 侧不变的部分：`_m_impl_devon_dispatch` 已有读取 `m_impl_red_ruling` 并注入 `ruling_instruction`/`ruling_scope` 后清空的逻辑（m_impl_decide.py:278-286），直接复用。

不再任何人写 `s.m_impl_ruling_outcome` → `_consume_ruling_outcome`（m_impl_decide.py:97-134）成为死代码，`_decide_m_impl_ruling_entry` 退化为直接调 `_m_impl_archer_ruling_dispatch`。State 字段 `m_impl_ruling_outcome`（machine.py）可删。

**为什么这样是对的**：reducer 不发 command（纯函数约束保留），但 reducer **设 substate 天然合法**——GREEN/RED 本来就是 reducer 常规路由目标。rev3 把"消费"内联到事件应用时刻，天然单调：流里裁定之后的事件（例如 Devon GREEN 失败）走正常 failure 路由，裁定一次性消费完毕，不会二次消费。

## 4. 待裁决的问题

- **Q1（架构）**: reducer 内直接根据 verdict payload 设目标 substate，是否符合 kernel 的分层契约？有没有我看不到的副作用（如 GREEN_GATE 前置条件、attempt 预算、worktree 生命周期假设被 RED→GREEN 跳变打破）？
- **Q2（字段复用）**: `m_impl_red_ruling` 原是 E2（split-RED green-on-arrival）专用，rev3 让它同时承载裁定指令。复用还是新字段？PLANNING 侧（Archer 读 red_ruling 转 verification-only）会不会被 redeliver_green 的 payload 误触发？
- **Q3（存量流自愈）**: 当前 run 的事件流已被 rev1/rev2 故障污染（裁定之后还有一串 DIAGNOSE verdict）。rev3 上线后 `trac retry` 重放，终态 substate 仍会是 DIAGNOSE（后续 verdict.failed 覆盖）。这是否意味着 T-002 需要： (a) 清 evidence 后走一轮新 DIAGNOSE→RULING（这次能正确消费）；或 (b) 有更干净的状态复位手段？
- **Q4（hold 语义）**: hold → TASK_DISPATCH + `current_task_id = None`，select_task 会选下一个任务。T-002 未完成就被跳过是否正确语义？还是 hold 应停留在 RULING 等人工？
- **Q5（测试义务）**: 作为回归证据，最少需要哪些单测？（我的初稿：routing 的 4 分支各一 + 重放覆盖场景一——裁定后跟一个 verdict.failed(GREEN) 终态应是 GREEN 的 failure 路由而非 DIAGNOSE。）

## 5. 相关文件

- `tracks/kernel/m_impl_routing.py:193-236`（rev3 diff，未提交）
- `tracks/kernel/m_impl_decide.py:97-143`（待清理的死代码）、`278-286`（指令注入）、`470-499`（路由表）
- `tracks/kernel/machine.py:382-385`（字段定义）
- `tracks/executor/verdict_face.py` `_emit_archer_ruling_verdict`（F1，保持不变）
