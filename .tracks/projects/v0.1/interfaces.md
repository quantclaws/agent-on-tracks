---
interfaces_id: IF-001
spec_ref: SPEC-001
arch_ref: ARCH-001
created: 2026-07-30
status: draft
sha:
---

# Agent on Tracks v0.1 — 接口与类型化 Schema

## 1. 总则

- 所有跨模块数据结构以 Python dataclass 定义，集中在 `tracks/kernel/events.py`。
- 序列化格式：JSON（事件日志）；内存中为 dataclass 实例。
- 字段命名：snake_case。事件 type 命名：`domain.action`（过去式）。
- 本文档是 Architecture §2 各模块间契约的字段级定义。
- **封闭集用 `Literal`/`Enum`（回应 R1-03b）**：`type`、`kind`、`role`、`decision`、`verdict`、`check`、`status`、`stage`、`substate` 均为受限字面量集合，编译期即可查拼写。下文以 `Literal[...]` 标注；`payload` 走 per-event 具体 dataclass 判别联合为后续方向，v0.1 先以 `Literal` 收口 type。
- **`guard` 刻意保留字符串**：workflow 是可序列化、可检视的声明式数据（v0.2 doc↔code trace 依赖），guard 是注册表中纯函数的**名字**，由 workflow well-formedness 测试保证每名可解析——非自由字符串，也不改为直接函数引用。

## 2. 事件信封（EventEnvelope）

```python
@dataclass(frozen=True)
class EventEnvelope:
    seq: int                  # run 内单调递增，从 1 开始
    ts: str                   # ISO-8601 UTC（仅诊断用，回放不依赖）
    run_id: str               # ULID
    version: str              # 宿主项目发布版本，如 "0.1"（多版本按此聚合）
    type: EventType           # Literal 封闭集，见 §3（domain.action 过去式）
    schema_version: int       # 事件 payload 结构版本，当前固定 1（与 version 是两根轴）
    payload: dict             # 类型化内容，见 §3 各条（后续演进为 per-event dataclass 判别联合）
```

> 存储：事件持久化于 SQLite `events` 表（`tracks.db`），主键 `(run_id, seq)`，只追加；上述信封字段即表的列（`payload` 存 JSON 文本）。派生投影表（`runs`/`backlog`）drop 可重建（D-02）。

## 3. 事件类型清单（v0.1 全集）

| type | payload 字段 | 触发者 |
|:-----|:-------------|:-------|
| `stage.entered` | `stage: str` | runtime |
| `stage.exited` | `stage: str` | runtime |
| `stage.rolled_back` | `from_stage: str, to_stage: str, reason: str` | runtime |
| `run.completed` | `terminal_state: str` | runtime |
| `run.interrupted` | `at_substate: str, reason: "signal" \| "crash_recovered"` | runtime（取消/关闭时，D-11） |
| `command.issued` | `command: Command`（见 §4） | runtime（write-ahead） |
| `assignment.dispatched` | `role: str, substate: str, assignment: Assignment`（见 §5） | executor |
| `outcome.received` | `role: str, status: str, artifact_ref: str \| None, self_report: str` | executor |
| `verdict.passed` | `check: str, detail: str` | executor（validate 后） |
| `verdict.failed` | `check: str, reason: str, evidence: str, attempt: int` | executor |
| `story.committed` | `commit_sha: str, story_sha: str` | executor |
| `spec.committed` | `commit_sha: str` | executor |
| `human.triage` | `decision: "go" \| "no_go" \| "park"` | cli → store（须先取得 `runtime/lock`） |
| `human.review` | `action: "no_comment" \| "comment", diff_ref: str \| None` | cli → store（须先取得 `runtime/lock`） |
| `sage.verdict` | `verdict: "pass" \| "comment", diff_ref: str \| None` | executor |
| `lex.verdict` | `verdict: "pass" \| "comment", diff_ref: str \| None` | executor |
| `backlog.recorded` | `version: str, decision: str, reason: str` | executor |

## 4. Command（命令）

```python
@dataclass(frozen=True)
class Command:
    command_id: str    # ULID，写 command.issued 时生成；结果事件回指此 id
    kind: Literal["dispatch_agent", "validate_document", "commit_document",
                  "create_branch", "delete_branch", "write_frontmatter",
                  "record_backlog", "complete_run", "rollback_stage"]
    params: dict       # kind 相关参数
```

> 结果事件（`outcome.received`、`verdict.*`、`story.committed`、`spec.committed` 等）payload 携带 `command_id`，与其 `command.issued` 配对，不依赖位置顺序。悬挂命令（issued 无同 id 结果）的投影/恢复规则见 Architecture §5d。

| kind | params | 语义 |
|:-----|:-------|:-----|
| `dispatch_agent` | `role, substate, assignment` | 分派 FakeAgent |
| `validate_document` | `doc_type, path, checks` | 校验文档 |
| `commit_document` | `path, message` | git add + commit |
| `create_branch` | `branch_name, base` | 创建并切换分支 |
| `delete_branch` | `branch_name` | 删除分支 |
| `write_frontmatter` | `path, fields: dict` | 更新 frontmatter |
| `record_backlog` | `version, decision, reason` | 追加 backlog |
| `complete_run` | `terminal_state` | 标记 run 结束 |
| `rollback_stage` | `to_stage, reason` | 回退阶段 |

## 5. Assignment（分派给 Agent 的任务）

```python
@dataclass(frozen=True)
class Assignment:
    task_id: str               # run_id + substate + attempt
    role: Literal["scribe", "sage", "lex"]
    objective: str             # 目标描述
    scope_whitelist: list[str] # 允许触碰的文件路径
    budget: Budget
    context: list[str]         # 显式给定的上下文文件路径
    failure_evidence: str | None  # 重派时附带上次失败原始输出
    simulate: str | None       # FakeAgent 控制字段（测试用）
```

> `simulate` 的值仅允许在 cli/executor 边界读取 `TRAC_FAKE_SIMULATE` 环境变量注入；
> `project()` 与 `decide()` 禁止读取环境变量或以任何方式感知测试模式——纯函数边界不因测试而破例。

```python
@dataclass(frozen=True)
class Budget:
    max_attempts: int = 3      # 硬性尝试上限
    max_net_lines: int = 500   # 本次最大净新增行数
    max_new_files: int = 1     # 最大新文件数
```

## 6. Outcome（Agent 返回）

```python
@dataclass(frozen=True)
class Outcome:
    command_id: str            # 回指触发本产出的命令
    status: Literal["done", "blocked", "failed"]
    artifact_ref: str | None   # 产出文件路径或 commit ref
    self_report: str           # Agent 自述（Runtime 不信任）
```

## 7. Verdict（Runtime 裁定）

```python
@dataclass(frozen=True)
class Verdict:
    passed: bool
    check: Literal["schema", "scope", "trace", "scope_overflow", "format"]
    reason: str                # 人类可读说明
    evidence: str              # 工具原始输出（非摘要）
    attempt: int               # 当前尝试次数
```

## 8. 投影状态（State）

```python
@dataclass
class State:
    run_id: str
    status: str                # "active" | "completed" | "awaiting_human"
    stage: str                 # "M-START" | "M-STORY" | "M-SPEC"
    substate: str              # "TRIAGE" | "DRAFT" | "SAGE_REVIEW" | ...
    awaiting: str | None       # "triage" | "review" | "escalation" | None
    current_attempt: int
    review_round: int
    sage_passed_this_round: bool
    lex_passed_this_round: bool
    last_verdict: Verdict | None
    version: str               # e.g. "v0.1"
```

`project(events) -> State` 是纯 fold：按 seq 顺序逐条应用转移规则。

## 9. Workflow 定义类型

```python
@dataclass(frozen=True)
class StageDef:
    name: str
    substates: list[str]
    initial_substate: str

@dataclass(frozen=True)
class Transition:
    from_substate: str
    to_substate: str
    guard: str                 # 守卫函数名（注册表按名解析为纯函数；见 §1）
    command_kind: str          # 触发时产出的 Command.kind（同 §4 Literal 集）

@dataclass(frozen=True)
class Workflow:
    stages: list[StageDef]
    transitions: list[Transition]
    max_attempts: int = 3
    max_fr_count: int = 30
```

## 10. CLI 接口契约

| 命令 | 输入 | 成功输出 | 失败输出 | exit code |
|:-----|:-----|:---------|:---------|:----------|
| `trac init` | 无 | stdout: "initialized" | stderr: 原因 | 0 / 1 |
| `trac start <ver>` | stdin: 原始需求 | stdout: run_id | stderr: 原因 | 0 / 1 |
| `trac run` | 无 | stdout: 最终子状态摘要 | stderr: 原因 | 0 / 1 |
| `trac triage <d>` | 无 | stdout: "recorded" | stderr: 原因 | 0 / 1 |
| `trac review <a>` | 无（revise 时可附 stdin diff） | stdout: "recorded" | stderr: 原因 | 0 / 1 |
| `trac status` | 无 | stdout: 状态摘要 | — | 0 |
| `trac replay <id>` | 无 | stdout: 事件行 + 终态 | stderr: 原因 | 0 / 1 |

## 11. 文件/存储契约

| 路径 | 格式 | 写入者 | 读取者 |
|:-----|:-----|:-------|:-------|
| `.tracks/projects/<ver>/story.md` | Markdown + YAML frontmatter | executor | validate, agents |
| `.tracks/projects/<ver>/spec.md` | Markdown + YAML frontmatter | executor | validate, agents |
| `.tracks/runtime/tracks.db` → `events` 表 | SQLite（真相源，只追加） | store | project, cli(replay) |
| `.tracks/runtime/tracks.db` → `runs` 表 | SQLite（派生投影，可重建） | store | cli(status) |
| `.tracks/runtime/tracks.db` → `backlog` 表 | SQLite（派生投影，可重建） | store | cli(status) |
| `.tracks/runtime/blobs/{sha256}` | 内容寻址文件（payload >8KB） | store | project, cli(replay) |
| `.tracks/runtime/lock` | 纯文本 PID | runtime | runtime |
