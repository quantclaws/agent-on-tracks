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

### 已交付的 Shield 修复

新增的主工作树 `M-IMPL / SHIELD_FIX` 派发会在调用 Agent 前保存审计收据：原始候选、写域、预存修改内容、实际物化的提示词和协议身份，以及启动后的子进程 PID。收据内容由 Runtime 写入 content-addressed blob，其引用绑定原 `command_id`，Agent 的结果文件不能替代这份收据。

控制进程中断后，使用原来的 `trac run --resume` 恢复入口。若原来的子进程已结束、候选没有变化且结果文件已交付，Runtime 根据原始基线重新审计 manifest、写域和文档变化，走普通结果检查和提交路径，不再调用 Agent。声明了 envelope 的 Shield WRITE 使用与 Runtime 相同的文件优先解析器；最终回复仅包含结果文件路径也是合法交付。

若原来的子进程仍存活，恢复入口保留原 pending command，等待原工作结束；不会并行派出第二个 Agent。若候选、manifest 或无法归属的工作内容发生变化，结果不会被猜测性采纳，工作树予以保留并报告需要重新验证。临时物化的 Agent 文件在成功接管后恢复到派发前的状态。

这条自动接管路径目前只覆盖有可信收据的主树 Shield 修复。历史上没有收据的派发，以及在隔离 worktree 中运行的 Devon/M-TEST 写入，仍使用既有恢复规则。进程 PID 无法确认、收据缺失或损坏时，也不能仅凭一个 JSON 文件证明工作已被安全接管。

### 修复提交后的 FULL 重验

全部任务完成后，若 run 正在诊断旧候选的 FULL 失败，新 `Tracks-OOB` 修复提交被 Runtime 登记且工作树干净，续跑会先调用现有 FULL settlement/proof 链，重新执行测试并逐条核销失败台账。提交声明只触发重验，不充当修复成功证据。仍然失败的项使用新候选的执行记录进入正常诊断；同一候选不会因旧失败反复自动重验。没有执行候选身份的历史证据、未登记提交、未提交修改、未完成任务或正在等待人工的流程不适用这条路径。

## 用户操作

如果 `trac run` 只是长时间等待 Agent，先观察 stderr。OpenCode backend 默认在事件流连续 1800 秒无输出时结束本次调用；`TRAC_AGENT_TIMEOUT` 调整的是无输出超时，不是整项工作的总时限。等待本身不表示 Runtime 已经失去状态。

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
