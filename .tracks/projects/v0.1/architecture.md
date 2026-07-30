---
architecture_id: ARCH-001
spec_ref: SPEC-001
created: 2026-07-30
status: draft
sha:
---

# Agent on Tracks v0.1 — 架构

> 本文从 flow.md 的 13 阶段全景反推骨架，v0.1 只填其中前三片（M-START/M-STORY/M-SPEC）。
> 目录按设计意图分包，使后续版本"只在既有包内新增文件"，而非推翻布局。

## 1. 唯一生产路径

只存在**一个**组合根和一条执行路径：

```
trac (CLI 入口) → tracks.kernel.runtime.run_loop(state, workflow)
                → tracks.effects.executor.execute(cmd)
```

所有命令（`init`、`start`、`run`、`triage`、`review`、`status`、`replay`）均通过
`tracks.cli:main()` 进入并委托给 kernel。不存在替代接线。

### 引擎主循环（伪代码）

```python
def run_loop(run_id: str, workflow: Workflow, deps: RuntimeDeps) -> None:
    while True:
        events = deps.event_store.read(run_id)
        state = project(events)                    # 纯 fold
        commands = decide(state, workflow)          # 纯函数
        if not commands:
            break                                   # awaiting_human 或 completed
        for cmd in commands:
            deps.event_store.append(command_issued(cmd))   # write-ahead（带 command_id）
            result = deps.executor.execute(cmd)             # 业务副作用边界
            deps.event_store.append(result.to_event(cmd.command_id))
```

> **两类 I/O 边界（回应 R1-02）**：系统只有两处 I/O，纯核心（`project`/`decide`）零 I/O。
> ① **业务副作用边界** = `effects/executor`（git、agent、文档写）；② **事件持久化边界** = `kernel/store`（只追加的 SQLite `events` 表）。
> "唯一副作用边界"一律指 ①；② 是范围极窄、仅 append 的持久化通道，不做任何业务决策。
>
> **dispatch 事件模型（回应 R2-02，D-12）**：一次 `dispatch_agent` 不产生独立的 `assignment.dispatched` 事件。`command.issued(dispatch_agent)`（其 payload 已含 `assignment`）本身就是**唯一的 write-ahead 派发事实**——它在 `executor.execute` 阻塞执行 Agent **之前**已落盘；Agent 返回后落 `outcome.received`。四条派发时序（正常/hang/SIGINT/kill-9）都由这一对"issued→（阻塞）→outcome"表达，悬挂即 issued 无 outcome（§5d–§5e）。

## 2. 包结构

骨架按**四个稳定性递减的增长轴**分包。每个轴独立生长，互不推翻：

```
src/tracks/
├── cli.py                # 薄入口：参数解析、退出码、信号安装。无业务逻辑。
├── kernel/               # 事件溯源内核——与任何具体 stage 无关，最稳定
│   ├── events.py         # 信封、Event/Command/Result 类型、序列化
│   ├── store.py          # SQLite events 表追加/读取、投影表重建、blob 外置、事务恢复
│   ├── project.py        # project(events) -> State  纯 fold
│   ├── decide.py         # decide(state, workflow) -> list[Command]  纯函数
│   └── runtime.py        # run_loop、锁、信号/关闭钩子、恢复编排
├── workflows/            # 每 stage 一个模块，声明式 StageDef（状态×守卫×转移）
│   ├── __init__.py       # PIPELINE 有序组装 + 阶段查找
│   ├── review.py         # M-STORY/M-SPEC/M-ACC 共享的评审子状态机（同构，抽出复用）
│   ├── m_start.py        # ← v0.1
│   ├── m_story.py        # ← v0.1（引用 review.py）
│   └── m_spec.py         # ← v0.1（引用 review.py）
│                         #   m_acc.py … m_milestone.py 随后续版本填入本目录
├── effects/              # 业务副作用唯一边界（git/agent/文档写）
│   ├── executor.py       # execute(cmd) 分发到具体副作用
│   ├── gitops.py         # 分支/提交/checkout（唯一 git 包装）
│   └── agents.py         # Agent 适配（v0.1: 确定性 FakeAgent；将来接真 LLM）
└── checks/               # 校验器：CLI 可手跑、引擎可当 verdict 来源
    └── validate.py       # story/spec schema + scope + story→spec 覆盖 trace
                          #   trace.py / reach.py ← v0.2 的家
```

### 四个增长轴（为什么这样分）

| 包 | 增长触发 | v0.1 后预期 |
|:---|:---------|:-----------|
| `kernel/` | 几乎不长——事件模型稳定后冻结 | v0.3 后基本不动 |
| `workflows/` | 每落地一个 flow.md 阶段 +1 文件 | 计划内线性增长至 ~13 |
| `effects/` | 接入真实 Agent、新副作用类型 | 缓慢 |
| `checks/` | 每个反 slop 工具 +1 文件（trace/reach/ratio/dup…） | 按工具 story 增长 |

> **可扩展性声明（回应 R1-05 / R2-05）**：`kernel/`（events/store/project/decide/runtime）**与具体 stage 无关**——它只认 `Workflow` 数据与事件信封。要区分两件事：kernel 的**机制/协议**（信封形状、fold 算法、命令↔结果配对、dispatch/reconcile 契约）设计为稳定，落地新阶段**不重写**它；kernel 的**枚举/数据**（`events.py` 的 `EventType` 集合、`Command.kind`）随阶段**additive 生长**——往 `Literal` 集合加一个成员是平凡的加法，不是引擎结构性改动。所以承载完整 13 阶段的机制是"新阶段只往 `workflows/` 增声明式数据、往 `events.py` 追加事件类型枚举，**引擎机制不按阶段返工**"，而非"kernel 一字节不改"。因此 v0.1 **不**预先规定 baseline digest / B·R·G lineage / CI 证据 / 幂等 publish / milestone 闭包等未来阶段合同——它们已在 `wiki/flow.md` 记录，随实现该阶段的版本逐版规格化；此处刻意**不建未来阶段空模块、也不提前抽象其扩展接口**（YAGNI）。

### 结构预算（NFR-06）

- **不限制模块数量**（早期"≤10 模块"上限已废除）。模块多而小、复用率高是被鼓励的：拆分降低耦合，复用降低总行数——总行数才是被约束的量。
- 单个生产 `.py` 文件 **≤1000 行**；超过即视为设计问题，必须拆分。
- **不设 `utils.py`/`helpers.py`/`common.py` 杂物模块**（见 §11）。

## 3. 分层约束

依赖只能自上而下，同层不横向依赖内核之外的包：

```
┌───────────────────────────────────────────────┐
│  cli.py           用户 I/O、退出码、信号安装    │
├───────────────────────────────────────────────┤
│  kernel/runtime   循环编排、锁、关闭钩子、恢复  │
├───────────────────────────────────────────────┤
│  kernel/project + kernel/decide   纯——无 I/O   │
│      └── 只依赖 kernel/events + workflows       │
├───────────────────────────────────────────────┤
│  effects/executor   唯一副作用边界              │
│      ├── effects/gitops   分支/提交/checkout    │
│      ├── effects/agents   FakeAgent 分派        │
│      └── checks/validate  校验产物              │
├───────────────────────────────────────────────┤
│  kernel/store     事件持久化                    │
│  kernel/events    类型与序列化（被各层引用）    │
│  workflows/*      声明式数据（被 decide 引用）  │
└───────────────────────────────────────────────┘
```

规则：

1. `kernel/project.py` 与 `kernel/decide.py` 仅导入 `kernel/events` 和 `workflows`。禁止 `os`、`subprocess`、`time`、环境变量。
2. `effects/` 是**唯一**调用 `subprocess`、写文件、调用 agent 的包。
3. `kernel/store.py` 被 `runtime`（追加）和 `cli`（status/replay 读取）调用，不做决策。
4. `effects/agents.py` 仅被 `effects/executor.py` 调用；Agent 不接触 store/runtime。
5. `workflows/` 是纯声明数据，不导入 kernel/effects（避免环）。`workflows/review.py` 被三个 stage 模块引用，是"多模块共享同一逻辑"的正例（见 §11）。
6. `checks/` 可被 `effects/executor`（引擎当 verdict 源）与 `cli`（手跑）双向调用，自身不产生事件。

## 4. 生产可达性证明

每个模块均可从入口 `tracks.cli:main` 到达（对应 v0.2 `trac check reach` 将自动验证的事实）：

```
cli → kernel.runtime → kernel.project, kernel.decide, effects.executor, kernel.store
kernel.runtime → (signal/atexit 钩子)
effects.executor → effects.gitops, effects.agents, checks.validate
kernel.project → kernel.events, workflows
kernel.decide → kernel.events, workflows
workflows.__init__ → workflows.m_start, m_story, m_spec
workflows.m_story, m_spec → workflows.review
kernel.store → kernel.events
checks.validate → kernel.events
effects.agents → kernel.events
```

无仅从测试可达的叶模块。测试文件导入生产模块，反向永不成立。

## 5. 锁、并发与取消

### 5a. 单写者锁

- `runtime/lock` 文件内容为持锁者 PID。
- **所有写事件日志的命令**（`trac run`/`start`/`triage`/`review`）执行前获取排他锁（`fcntl.flock`）；退出时释放（含信号处理）。`status`/`replay` 只读，不取锁。
- 锁被持有时：`flock` 失败 → 读 lock 文件 PID → stderr 报告 → exit 1，且不向事件日志写入任何行。
- 无轮询、无重试、无等待。

### 5b. 取消协议（D-11：意图走信号，事实走日志）

真实 Agent 可能执行数十分钟，人类中途取消是合理决定。取消**不引入第二个写者**：

- 取消是一个**意图** → 经 OS 信号送达持锁进程（PID 在 lock 文件里）。
- 唯一写者（runtime 自己）把信号转成**事实**：终止 Agent 子进程组 → 落 `run.interrupted` 事件 → 释放锁 → exit 130。单写者不变量毫发无损。
- v0.1 前台运行，Ctrl-C 即此通道，**不新增 CLI**。`trac cancel`（读 lock PID → 发 SIGTERM）推迟到接入真实 Agent 的版本（D-11）。
- 语义：取消发生在 `command.issued` 已落盘、结果未落盘时 → 恢复时 decide 看到"已签发无结果"→ 重新签发同一 assignment，**不消耗 attempt**（取消是人类决定，非 Agent 失败）。

### 5c. 关闭钩子与崩溃恢复

- 优雅路径：`cli.py` 安装 SIGINT/SIGTERM handler + `atexit`，负责杀子进程组、落 `run.interrupted`、释放锁。
- 钩子只是礼貌，**不是保证**：`kill -9` 不触发任何钩子。真正的保证来自 write-ahead + fold 恢复（§6）。
- **崩溃于事务中途**：进程可能死在"落事件 + 更新投影"事务未提交时。SQLite ACID 保证该事务整体回滚——重启后 `events` 表**不出现半截事件**，无需 JSONL 的半行截断处理。这条使 FR-29 的"精确恢复"成立。

### 5d. 命令关联与悬挂命令恢复（回应 R1-03）

- 每条 `Command` 带 `command_id`（ULID）；`command.issued` 与其结果事件共享该 `command_id`，配对**不依赖位置顺序**。
- **悬挂命令投影规则**：`project()` 折叠时若见某 `command.issued` 无携同 `command_id` 的后继结果 → 标记为悬挂。恢复时 `decide()` 对悬挂命令**重签发同一命令/assignment**（同 `command_id`/`task_id`），`attempt` 不增（区别于校验失败重派）。这正是崩溃/取消后"证明重签发的是同一 assignment"的依据。

### 5e. 副作用可恢复性：per-kind reconcile（回应 R2-03）

稳定 `command_id` 只能**识别**操作，不能使操作幂等。崩溃可能发生在 git commit / 建删分支 / 写文件**已成功、结果事件尚未落盘**之后——SQLite 事务只覆盖 ①事件+投影，管不到 ②边界另一侧的 git/文件系统。因此每个副作用命令在（重）执行前必须先 **reconcile**：`reconcile（查真实世界事实）→ execute if needed（未完成才执行）→ observe（据实际事实落结果事件）`。commit 通过在提交消息尾加 `Tracks-Command: <command_id>` trailer 使"是否已提交"可被探测。

| kind | reconcile 探测真实事实 | 判为"已完成" | 未完成动作 |
|:-----|:-----------------------|:-------------|:-----------|
| `create_branch` | `git rev-parse --verify <branch>` | 分支已存在且指向 base | 创建并切换 |
| `delete_branch` | `git rev-parse --verify <branch>` | 分支已不存在 | 删除 |
| `commit_document` | `git log --grep=<command_id>` + 工作区是否 clean | 已有携带该 `command_id` trailer 的提交 | `git add` + commit |
| `write_frontmatter` | 读目标文件 frontmatter 字段现值 | 字段已等于目标值 | 写入 |
| `record_backlog` | 查 `events` 是否已有该 `command_id` 的 `backlog.recorded` | 已有 | 追加 |
| `dispatch_agent` | 查产物文件是否已存在 | —（产物覆盖写，重派天然幂等） | 重派同 assignment |
| `validate_document` | 纯读校验，天然幂等 | — | 重跑 |
| `complete_run` | 查是否已有 `run.completed` | 已有 | 追加 |
| `rollback_stage` | 查是否已有 `stage.rolled_back(to=…)` | 已有 | 执行回退 |

- 恢复悬挂命令时**先 reconcile 再决定是否 execute**，绝不盲目重跑；观察到的真实结果才落结果事件。
- 测试至少覆盖"`commit_document` 已成功但结果事件未落盘"的恢复：重跑 reconcile 探到已有提交 → 跳过 commit、仅补记 `story.committed`，`git log` 不出现重复/空提交（AC-29d）。

## 6. 事件存储与持久化

- SQLite 单文件 `.tracks/runtime/tracks.db`（标准库 `sqlite3`，零依赖）。
- **`events` 表 = 唯一真相源**，只追加。列：`run_id, seq, version, ts, type, schema_version, payload(JSON), command_id, task_id`；主键 `(run_id, seq)`。
- 信封：`{seq, ts, run_id, version, type, schema_version, command_id, task_id, payload}`，字段逐一对应上列（`command_id`/`task_id` 无关联时为 NULL）；`version` = 发布版本（如 `0.1`），供多版本按 `version` 聚合。
- `seq` 每 run 从 1 递增。回放按 `(run_id, seq)` 排序，忽略 `ts`。
- payload >8KB → 写入 `runtime/blobs/{sha256}`，payload 变为 `{"$ref": "blobs/{sha256}"}`。
- **派生投影表** `runs`（`{run_id, version, started_at, status}`）、`backlog` 等：随事件在同一事务更新；drop 后可由 `events` 折叠完整重建（AC-N04a）。
- 落事件 + 更新投影在同一 SQLite 事务内原子提交（见 §5c）。
- **不可改写（append-only，回应 R2-07）**：`store` 对 `events` 表**只暴露 `append`/`read`，不提供任何 update/delete API**；已写入的行字节不再变动（新事实一律追加新行）。`(run_id, seq)` 唯一仅防重复 seq，本身不证明不可改写——不可改写由"无改写路径 + 字节级不变"测试证明（AC-28c）。

## 7. FakeAgent 设计

`effects/agents.py` 提供**确定性函数** `(assignment) -> outcome`：

- Scribe：从固定模板 + assignment 上下文产出 story.md / spec.md 内容。
- Sage：按 assignment 标志返回 `verdict(pass)` 或 `verdict(comment, diff)`。
- Lex：与 Sage 同构，作用于 spec.md。

行为由 assignment 的 `simulate` 字段控制（如 `"schema_fail"`、`"scope_overflow"`），
使测试确定性触发重派/升级路径，无随机性。`simulate` 仅在 cli/executor 边界从
`TRAC_FAKE_SIMULATE` 环境变量注入；`project()`/`decide()` 禁止感知测试模式（见 interfaces §5）。

## 8. Workflow 定义

以类型化 Python 数据（dataclass）声明，**一个 stage 一个模块**放在 `workflows/`：

- 每个 stage 模块导出一个 `StageDef`（状态、子状态、转移、守卫、预算）。
- `workflows/__init__.py` 把它们组装为有序 `PIPELINE`（Workflow）。
- 一个 stage 的子状态机 ≈ 一个 StageDef；13 个阶段绝不塞进单文件（否则 M-IMPL 单机就 >100 行，全程上千行）。
- 与 wiki `flow.md` 逐节 **1:1 对应**——文档↔代码 trace（v0.2 工具）可直接消费此对应关系。
- M-STORY/M-SPEC/M-ACC 的评审子机同构，抽入 `workflows/review.py` 复用，三处引用。
- 守卫示例：`retry_count < 3`、`fr_count ≤ 30`。增加阶段 = 在 `workflows/` 加一个模块 + 在 `__init__` 注册 + 发布新版 trac。

## 9. 公共逻辑归属（NFR-06，替代 utils 杂物袋）

横切辅助放进**拥有该概念的最低层模块**；找不到 owner 时建**命名的**专属小模块，绝不建 `utils/helpers/common`：

- `sha256_hex(data)` → `kernel/events.py`（序列化概念的一部分，store/validate/executor 共用）
- 事件读写 / 投影重建 → `kernel/store.py`（SQLite 存储概念）
- `git(*args)` → `effects/gitops.py`（唯一 git 包装）
- `fail(msg, code=1)` → `cli.py`（统一错误退出）

**关于"同一方法多模块引用"**（用户提问）：这确实存在，且是好事——正解不是把内容挤进一个大文件，而是让它**属于一个语义明确的模块**，其他模块 import 它。判据：给它一个能说清"它是什么概念"的模块名。若一段逻辑无法归入任何已有概念、也说不清自己是什么概念，那是设计异味（该逻辑本身有问题），不是建杂物袋的理由。`workflows/review.py` 被三个 stage 复用，就是这条规则的正例。

## 10. v0.1 有意识简化

- v0.1 的 `tracks.db` 只建 `events` + 最小投影表（`runs`、`backlog`）；跨版本聚合/看板查询是 v0.15 交付，不在 v0.1。
- blob 外置代码路径在 FakeAgent happy-path 不触发（payload 很小），但由 store 单测注入 >8KB payload 专门验证（AC-28b）。
- 无 `trac export` / `trac cancel` 命令（cancel 见 §5b / D-11）。
- Backlog = `runtime/tracks.db` 中的 `backlog` 表，是 `backlog.recorded` 事件的**可重建投影**（唯一真相仍是 `events` 表；drop 后可由事件重放重建）。v0.1 只做最小记录（一行/事件），不含 backlog 子系统。
- `checks/` 目录 v0.1 只有 `validate.py`；trace/reach 是 v0.2 的交付物。
