# 查看一个 run

## 查看当前状态

当 `trac run` 停下来，第一步通常不是再跑一次，而是先问 Runtime：现在究竟停在哪里？

```bash
trac status
```

如果项目还没有 run：

```text
no runs yet
```

正在等待 Human 时，输出类似：

```text
run 01K...: stage=M-ACC substate=HUMAN_REVIEW status=awaiting_human awaiting=review
```

已经到达当前版本边界时：

```text
run 01K...: completed terminal=boundary stage=M-TEST
```

`status` 不接受 run ID，也没有 `--json`。它选择最近更新的真实 run，包括已经 completed 的 run；只有 backlog placeholder 会被排除。因此，如果项目里有多个历史 run，而你想查看指定一个，应该复制 run ID 后使用 replay。

`trac run` 返回 0 只表示这次调用正常停下，不表示整个 release 已完成。它可能停在 Human gate、escalation 或当前实现边界。最终判断以 `trac status` 输出的 `status`、`awaiting` 与 `terminal` 为准。

## 回放事件

`status` 给出一行摘要；`replay` 展开这个结论是怎样形成的：

```bash
trac replay 01K2EXAMPLE...
```

每行是一条事件：

```text
1	2026-08-08T...	story.requested	{"raw_chars": 42}
2	2026-08-08T...	stage.entered	{"stage": "M-START"}
3	2026-08-08T...	command.issued	{"command": {...}}
```

最后一行是把全部事件交给 `project()` 后得到的状态：

```text
final: stage=M-ACC substate=HUMAN_REVIEW status=awaiting_human awaiting=review
```

事件行由四部分组成：run 内递增的 `seq`、时间戳、事件类型和 JSON payload。它是便于人阅读的 TSV，不是一个完整 JSON 文档。

Replay **不会执行 command，也不会修复外部副作用**。它只读取事件并重新折叠状态。未知 run ID 会写入 stderr 并返回 1：

```text
unknown run: 01K2UNKNOWN...
```

> [!info] Event replay
>
> 在事件溯源系统中，当前状态由历史事件依次投影得到。Tracks 的 `events` 是事实源，`runs` 与 `backlog` 是可重建投影；replay 用来复核投影，不是重放现实世界的副作用。

## 阅读证据

查故障时，不要从头逐字阅读所有 payload。先找以下几类事件：

**`command.issued`**：Runtime 打算执行什么。记录 command kind、参数、`command_id` 和 task identity。

**结果事件**：同一个 `command_id` 是否出现对应 outcome、commit 或 verdict。只有 issued 没有结果，表示命令在进程中断时可能处于 pending。

**`verdict.failed`**：程序门禁为什么拒绝推进。优先看 classification、目标 stage 和 reason，不要只看 Agent 的文字摘要。

**Human 事件**：triage、review、approval 是否绑定当前 revision。上游文档变化以后，旧 approval 即使仍在事件流中，也可能已经 stale。

**attempt**：同一 assignment 已失败多少次。crash recovery 本身不应消耗失败 attempt；真正的校验失败和 revise 才会推进计数。

一个常用检查顺序是：

1. 从 `status` 取得 run ID 和当前 substate；
2. replay 该 run；
3. 从末尾向前找到最后一个 `command.issued`；
4. 检查是否有同 `command_id` 的结果；
5. 若已进入 escalation，阅读 attempts 与第一条 reason。

## 常见异常

**`boundary`** 不是错误。它表示当前版本已经走到已实现流程的末端，而下一个 stage 尚未注册。到达 boundary 后不应反复执行 `trac run` 期待它越过尚未实现的阶段。

**`awaiting_human`** 表示流程需要 Human 输入。查看 `awaiting=triage|review|approval|escalation`，再使用对应命令；重复运行 `trac run` 通常不会替 Human 做决定。

**`needs_attention`** 表示 Runtime 无法安全自动选择下一步，例如外部事实未知、baseline 冲突或凭据缺失。应先阅读事件和工作区，不要直接删除 Runtime 数据。

**`stale`** 表示证据绑定的输入已经变化。正确处理通常是重新生成或重跑受影响证据，而不是把旧 verdict 手工改回 pass。

**最近 run 不是目标 run**：`status` 只展示最近更新的真实 run。查看历史必须显式执行 `trac replay <run-id>`。

如果问题与进程中断、锁或 pending command 有关，继续阅读[恢复被中断的 run](recover-run.md)。
