---
architecture_id: ARCH-001
spec_ref: SPEC-001
created: 2026-07-30
status: draft
sha:
---

# Agent on Tracks v0.1 — 架构

## 1. 唯一生产路径

只存在**一个**组合根和一条执行路径：

```
trac (CLI 入口) → track.runtime.run_loop(state, workflow) → track.executor.execute(cmd)
```

所有命令（`init`、`start`、`run`、`triage`、`review`、`status`、`replay`）均通过
`track.cli:main()` 进入并委托给 runtime。不存在替代接线。

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
            deps.event_store.append(command_issued(cmd))   # write-ahead
            result = deps.executor.execute(cmd)             # 唯一副作用
            deps.event_store.append(result.to_event())
```

## 2. 模块清单（≤10 核心模块）

| # | 模块 | 职责 | 生产调用者 |
|:--|:-----|:-----|:-----------|
| 1 | `track/cli.py` | 参数解析，分发到 runtime。入口 `main()`。 | pyproject `[project.scripts]` |
| 2 | `track/runtime.py` | `run_loop()`、锁获取、run 生命周期。 | cli.py |
| 3 | `track/project.py` | `project(events) -> State` — 纯 fold。 | runtime.py, replay |
| 4 | `track/decide.py` | `decide(state, workflow) -> list[Command]` — 纯函数。 | runtime.py |
| 5 | `track/executor.py` | `execute(cmd) -> Result` — **唯一**副作用边界（git、文件、agent）。 | runtime.py |
| 6 | `track/events.py` | Event/Command/Result 数据类、序列化、信封。 | 所有模块 |
| 7 | `track/store.py` | EventStore：JSONL 追加/读取、blob 外置、runs.jsonl 索引。 | runtime.py, cli.py |
| 8 | `track/workflow.py` | Workflow 定义（阶段、子状态、守卫、预算）——类型化数据。 | decide.py |
| 9 | `track/validate.py` | 文档校验器（story schema、spec schema、scope、trace）。 | executor.py |
| 10 | `track/agents.py` | FakeAgent 实现（Scribe、Sage、Lex）。确定性。 | executor.py |

**计数：10 个模块。** 无仅为测试存在的模块。每个模块至少被一个其他生产模块导入（可达性证明见 §5）。

### 公共函数提取（NFR-07）

横切辅助函数放在拥有该概念的模块中：

- `sha256_hex(data: bytes) -> str` → `track/events.py`（store、validate、executor 共用）
- `read_jsonl(path) -> list[dict]` / `append_jsonl(path, obj)` → `track/store.py`
- `git(*args) -> CompletedProcess` → `track/executor.py`（唯一 git 包装）
- `fail(msg, code=1)` → `track/cli.py`（统一错误退出）

不设工具杂物模块。

## 3. 分层约束

```
┌─────────────────────────────────────────────────┐
│  cli.py  （用户 I/O、退出码）                     │
├─────────────────────────────────────────────────┤
│  runtime.py  （循环编排、锁）                     │
├─────────────────────────────────────────────────┤
│  project.py + decide.py  （纯——无 I/O）          │
├─────────────────────────────────────────────────┤
│  executor.py  （唯一副作用）                      │
│    ├── git 操作                                  │
│    ├── 文件写入（story.md、spec.md）             │
│    ├── agent 分派（FakeAgent）                   │
│    └── 校验（委托 validate.py）                  │
├─────────────────────────────────────────────────┤
│  store.py  （事件持久化）                         │
│  events.py / workflow.py / validate.py / agents  │
└─────────────────────────────────────────────────┘
```

规则：

1. `project.py` 和 `decide.py` 仅导入 `events.py` 和 `workflow.py`。禁止 `os`、`subprocess`、`time`。
2. `executor.py` 是**唯一**调用 `subprocess`、写文件、调用 agent 的模块。
3. `store.py` 被 `runtime.py`（追加）和 `cli.py`（status/replay 读取）调用。它不做决策。
4. `agents.py` 仅被 `executor.py` 调用。Agent 不接触 store 或 runtime。

## 4. 数据流

```
stdin（原始需求）
    │
    ▼
cli.py ──► runtime.py ──► store.append(command.issued)
    │              │
    │              ▼
    │         executor.execute(cmd)
    │              │
    │              ├──► agents.py（FakeAgent 产出文档）
    │              ├──► validate.py（Runtime 校验产物）
    │              └──► git（commit / branch / checkout）
    │              │
    │              ▼
    │         store.append(result_event)
    │              │
    ▼              ▼
cli.py ◄──── project(events) ◄──── store.read(run_id)
(stdout)         │
                 ▼
            decide(state, workflow) → 下一批 commands 或 ∅
```

## 5. 生产可达性证明

每个模块均可从入口 `track.cli:main` 到达：

```
cli → runtime → project, decide, executor, store
executor → agents, validate
project → events, workflow
decide → events, workflow
store → events
validate → events
agents → events
```

无仅从测试可达的叶模块。测试文件导入生产模块，反向永不成立。

## 6. 锁与并发

- `runtime/lock` 文件内容为持锁者 PID。
- `trac run` 启动时获取排他锁（`fcntl.flock`）；退出时释放（含信号处理）。
- 第二个进程：`flock` 失败 → 读 lock 文件中 PID → stderr 报告 → exit 1。
- 无轮询、无重试、无等待。

## 7. 事件日志与持久化

- 每 run 一个文件：`.tracks/runtime/events/run-{ULID}.jsonl`
- 信封：`{seq, ts, run_id, type, schema_version, payload}`
- `seq` 从 1 开始逐行递增。回放按 `seq` 排序，忽略 `ts`。
- payload >8KB → 写入 `runtime/blobs/{sha256}`，payload 变为 `{"$ref": "blobs/{sha256}"}`。
- `runs.jsonl`：append-only 索引 `{run_id, started_at, status}`。
- `tracks.db`：可选 SQLite 投影。缺失时所有命令从 JSONL 工作。（v0.1 可完全跳过 DB。）

## 8. FakeAgent 设计

FakeAgent 是**确定性函数** `(assignment) -> outcome`：

- Scribe：从固定模板 + assignment 上下文产出 story.md / spec.md 内容。
- Sage：根据 assignment 标志返回 `verdict(pass)` 或 `verdict(comment, diff)`。
- Lex：与 Sage 同构，作用于 spec.md。

行为由 assignment payload 字段控制（如 `simulate_failure: "schema"`），
使测试能确定性地触发重派/升级路径，无随机性。

## 9. Workflow 定义

以类型化 Python 数据（dataclass）内置于 `track/workflow.py`，非 YAML/DSL。

定义：阶段、子状态、转移、守卫（retry_count < 3、fr_count ≤ 30）、
预算（max_attempts=3）。增加阶段 = 编辑此模块 + 发布新版 trac。

## 10. v0.1 有意识简化

- v0.1 脚手架不含 `tracks.db`（投影缓存延后；事件足够）。
- blob 外置代码路径存在但实际不会触发（FakeAgent payload 很小）。
- 无 `trac export` 命令。
- Backlog = 单个 append-only JSONL 文件 `.tracks/runtime/backlog.jsonl`。
