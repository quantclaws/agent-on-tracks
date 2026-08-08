# 恢复被中断的 run

## 可以恢复哪些中断

Tracks 把 stage、substate、command 和 outcome 写成事件，所以进程退出不等于流程消失。常见中断包括：

- 你在等待 Agent 时按下 Ctrl-C；
- trac 进程收到 SIGTERM；
- 进程被 `kill -9` 或主机突然重启；
- Agent 子进程退出、凭据失败或返回无效结果；
- `command.issued` 已经落盘，但结果事件还没来得及写入。

不同中断的恢复强度不同。正常 Ctrl-C 可以执行清理代码；SIGKILL 无法执行 `finally`，可能留下锁、部分工作区修改和 pending command。Tracks 能恢复流程状态，不等于所有外部副作用都天然 exactly-once。

> [!info] Exactly-once
>
> 分布式系统通常难以保证副作用只发生一次。更常见的策略是至少一次执行，再用幂等操作和 reconcile 判断是否已经完成。Tracks 对不同 command 分别定义恢复规则。

## Runtime 如何恢复

Runtime 在执行副作用之前先写 `command.issued`。如果进程在结果落盘前死亡，下一次 `trac run` 会看到 state 中仍有 pending command。

恢复顺序是：

1. 从完整事件流 fold 当前状态；
2. 找到 issued 但没有结果的 command；
3. 使用原来的 kind、参数和 `command_id` 调用 reconcile；
4. 记录恢复结果；
5. 再进入普通 decide/execute 循环。

Crash recovery 使用原 command identity，不自动把这次中断记作 Agent 失败，也不消耗 failure attempt。

具体能否避免重复副作用取决于 command kind。例如 git commit 可以凭 command identity 检查是否已经存在；纯读取校验可以安全重跑；外部 Agent 若已完成但结果尚未落盘，可能需要再次 dispatch。文档不应把 reconcile 解释成对所有外部世界的 exactly-once 保证。

## 用户操作

如果 `trac run` 只是长时间等待 Agent，先观察 stderr。生产 OpenCode backend 当前没有统一超时；等待不表示 Runtime 已经失去状态。

如果决定取消，按一次 Ctrl-C，等待 trac 和 Agent 子进程组退出。随后执行：

```bash
trac status
trac replay <run-id>
trac run
```

主机崩溃或 `kill -9` 后同样按这个顺序处理。下一次 mutating command 会检查 `.tracks/runtime/lock` 中的 PID；若它是已经死亡的数字 PID，Runtime 会尝试回收 stale lock，再处理 pending command。

如果 status 显示 escalation，例如：

```text
awaiting=escalation attempts=3 reason=[template] ...
```

Human 阅读证据并确认可以重试后：

```bash
trac retry
trac run
```

`trac retry --clear-evidence` 会有意清除旧 evidence 并重置后续 dispatch，只有在已确认 evidence stale 时使用。它不是“再试一次”的通用按钮。

## 不应手工修改什么

不要把恢复变成第二次事故。通常不应手工编辑：

- SQLite `events` 表；
- `runs` 或 `backlog` 投影；
- pending command payload；
- `.tracks/runtime/lock`；
- content-addressed blob；
- Git 中由 Runtime 管理的 checkpoint ref。

尤其不要把删除 lock 当成第一步。先读取其中 PID，确认进程是否仍存在。如果持锁 trac 还活着，删除 lock 会允许第二个 writer 进入；两个 Runtime 同时修改状态比等待更危险。

派生的 `runs`/`backlog` 投影丢失时，`trac status` 可以从完整 events 重建。这个保证不覆盖 SQLite 文件损坏、events 被删除或 event 引用的 blob 丢失。

## 无法自动恢复的边界

以下情况可能需要 `needs_attention` 和人工核对：

- 外部操作可能成功，但 Runtime 无法从 API、Git 或文件系统确认；
- 凭据缺失或无权读取外部状态；
- PID 被复用，stale lock 无法仅凭 PID 安全判断；
- Human 或外部工具在 Agent 运行期间改了同一文件；
- Agent 留下部分工作区修改，无法证明哪些字节属于它；
- event 引用的 blob 被人工删除；
- baseline、candidate 或 approval identity 互相不一致。

此时 Runtime 停下来，不是恢复失败，而是拒绝在证据不足时猜测。先保留现场，使用 `status`、`replay`、`git status` 和 `git diff` 建立事实，再决定 reconcile、return upstream 或取消 run。

`trac replay` 只帮助你理解状态，它不会修复任何副作用。真正的恢复入口仍是取得单写者锁后的 `trac run`。
