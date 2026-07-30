---
spec_id: SPEC-001
story_ref: S-001
created: 2026-07-30
status: draft
sha:
---

# Agent on Tracks v0.1 — 功能规格

## 1. 能力边界

v0.1 实现宿主项目 Runtime 的 **M-START → M-STORY → M-SPEC** 通路，
由 `trac` CLI + 确定性 FakeAgent 驱动，不依赖任何 LLM。

产品能力**终止于** `stage.exited(M-SPEC)` 且 `spec.md` 通过终验。

v0.1 不包含（产品能力排除）：

- M-ACC 及之后所有阶段
- 真实 LLM Agent 集成
- Web UI / GitHub 集成 / CI
- 反 slop 工具（`trac check reach|budget|ratio|dup`）
- inline-comments 完整协议（评审意见为纯文本 diff）
- Agent 会话状态保留

## 2. 功能需求

### M-START / CLI 基础

| ID | 需求 | Story 来源 |
|:---|:---|:---|
| FR-01 | `trac init` 在当前目录创建 `.tracks/` 目录结构（projects/、runtime/、wiki/）。幂等：对已存在结构重复执行 → exit 0，无副作用。 | Story §核心操作路径 步骤1 |
| FR-02 | `trac start <version>` 从 **stdin** 读取原始需求文本。stdin 为空 → exit 1，stderr 报错。 | Story §核心操作路径 步骤2；D-08 |
| FR-03 | `trac start` 在 git 工作区不干净（存在未提交变更）时拒绝执行。exit 1，stderr 说明原因，不创建任何分支。 | Story §行为种子 第2行 |
| FR-04 | `trac start` 从当前 HEAD 创建分支 `releases/<version>` 并切换。 | Story §行为种子 第1行；Flow §3 |
| FR-05 | `trac start` 写入 `.tracks/projects/<version>/story.md`，含 frontmatter（`story_id`、`created`、`status: draft`、空 `sha`）及原始需求正文。 | Story §核心操作路径 步骤2；D-08 |
| FR-06 | `trac start` 追加 `stage.entered(M-START)` 和 `stage.exited(M-START)` 事件。创建 run 并索引到 `runs.jsonl`。 | Story §核心操作路径 步骤2；Arch §5 |

### M-STORY

| ID | 需求 | Story 来源 |
|:---|:---|:---|
| FR-07 | `trac run` 投影当前状态；若处于 M-STORY/TRIAGE，分派 Scribe(FakeAgent) 探索，记录 `assignment.dispatched`。 | Story §核心操作路径 步骤3；Flow §4.1 |
| FR-08 | `trac triage go\|no-go\|park` 追加 `human.triage(decision)` 事件。仅在 state=awaiting_triage 时有效；否则 exit 1。 | Story §核心操作路径 步骤3；D-04 |
| FR-09 | `human.triage(no_go)` 或 `human.triage(park)`：记入 backlog、删除 `releases/<version>` 分支、追加 `run.completed`、exit 0。 | Story §行为种子 第3-4行；D-06 |
| FR-10 | `human.triage(go)`：进入 DRAFT，分派 Scribe(FakeAgent) 按模板写 story.md。 | Story §核心操作路径 步骤3；Flow §4.1 |
| FR-11 | Scribe 产出后：Runtime 校验 story.md（schema + scope：仅允许动 story.md）。失败 → `verdict.failed` + 附带失败证据重派同一 Agent。 | Story §行为种子 第6行；Flow §4.1 |
| FR-12 | 同一校验连续失败 3 次 → 停止重派，进入 `awaiting_human`（升级人类）。 | Story §行为种子 第7行；Arch §3d |
| FR-13 | 校验通过 + 提交后：进入 SAGE_REVIEW，分派 Sage(FakeAgent) 评审。 | Flow §4.1 |
| FR-14 | `sage.verdict(pass)` → 进入 HUMAN_REVIEW。`sage.verdict(comment)` → 进入 RESPOND（分派 Scribe 附 diff）。 | Flow §4.1 |
| FR-15 | HUMAN_REVIEW 中：`trac review no-comment` + 同轮 sage pass → EXIT。 | Story §核心操作路径 步骤3；Flow §4.1 |
| FR-16 | `trac review revise` → RESPOND：分派 Scribe 附 Human diff + 评论，之后进入新一轮 SAGE_REVIEW。 | D-04；Flow §4.1 RESPOND |
| FR-17 | EXIT(M-STORY)：计算 story.md 内容 sha256，写入 frontmatter `sha` 字段，提交，追加 `stage.exited(M-STORY)`。 | Story §行为种子 第12行；Flow §4.1 EXIT |

### M-SPEC

| ID | 需求 | Story 来源 |
|:---|:---|:---|
| FR-18 | `stage.entered(M-SPEC)` 后：进入 DRAFT，分派 Sage(FakeAgent) 继承 M-STORY 上下文写 spec.md。 | Story §核心操作路径 步骤4；Flow §5.1 |
| FR-19 | Sage 产出后：校验 spec.md（schema + scope + story→spec 覆盖 trace）。失败 → 附证据重派（≤3 次）。 | Flow §5.1；Story §行为种子 第6行 |
| FR-20 | spec.md 中 FR 超过 30 条：`verdict.failed(scope_overflow)` → `stage.rolled_back` → 回退 M-STORY。分支**不删除**。 | Story §行为种子 第8行；D-06 |
| FR-21 | 校验通过 + 提交后：进入 LEX_REVIEW，分派 Lex(FakeAgent)。`lex.verdict(pass\|comment)` 协议与 sage 同构。 | Flow §5.1 |
| FR-22 | M-SPEC 的 HUMAN_REVIEW：`trac review no-comment` + 同轮 lex pass → EXIT。`trac review revise` → RESPOND。 | Flow §5.1；D-04 |
| FR-23 | EXIT(M-SPEC)：格式终验通过 → `stage.exited(M-SPEC)`。Run 完成。 | Story §核心操作路径 步骤4；Flow §5.1 EXIT |

### 横切 / CLI

| ID | 需求 | Story 来源 |
|:---|:---|:---|
| FR-24 | `trac status` 打印当前活跃 run 的阶段、子状态、待处理事项。exit 0。无活跃 run → 提示无活跃 run，exit 0。 | Story §核心操作路径 步骤5；D-05 |
| FR-25 | `trac replay <run-id>` 折叠该 run 全部事件，逐行打印，末尾输出终态摘要。exit 0。未知 run-id → exit 1。 | Story §核心操作路径 步骤6；D-05 |
| FR-26 | replay 终态与 `trac status` 对同一 run 报告的状态语义一致。 | Story §行为种子 第9行 |
| FR-27 | 单写者锁：第二个 `trac run` 在锁被持有时立即失败（exit 1，stderr 报告持锁者 PID）。无轮询/等待。 | Story §行为种子 第11行；D-07 |
| FR-28 | 事件日志：每 run 一个 JSONL 文件 `run-{ULID}.jsonl`，append-only，`seq` 单调递增。payload >8KB 外置到 `runtime/blobs/{sha256}`。 | Arch §5 |
| FR-29 | 进程可在等待人类时中断（Ctrl-C / kill）；重新 `trac run` 从事件日志恢复精确子状态。无内存悬挂状态。 | Story §行为种子 第10行；Flow §4.1 |
| FR-30 | Write-ahead：每条命令执行前先落 `command.issued` 事件；结果在执行后落盘。崩溃恢复复用同一循环。 | Arch §2 第5点 |

## 3. 非功能需求

| ID | 需求 | 来源 |
|:---|:---|:---|
| NFR-01 | v0.1 不接真实 LLM。FakeAgent 是一等公民、确定性执行器替身——不是临时 mock。 | Story §范围排除；Arch §7 |
| NFR-02 | 核心 Runtime ≤10 个 Python 模块（不含 CLI 入口和 workflow 数据定义则需显式裁定）。 | Arch §7；Handoff |
| NFR-03 | `project()` 和 `decide()` 是纯函数：禁止时钟、文件系统、I/O。一切不确定性仅以事件形式进入。 | Arch §2 第3点 |
| NFR-04 | Runtime 永不信任 Agent 自述。verdict 由 Runtime 对产物运行校验工具后产出。 | Arch §3c；Flow §2 不变量6 |
| NFR-05 | `tracks.db` 是可抛弃投影/缓存。删除后重放事件必须复现相同状态。事件是唯一真相源。 | D-02；Arch §4 |
| NFR-06 | 无向后兼容别名。不支持 `.track/`。每个概念只有一条规范路径。 | Handoff；D-01 |
| NFR-07 | 代码精练；共享逻辑提取为公共函数。模块间无重复。 | Human 指令（2026-07-30） |

## 4. 需求计数

- 功能需求：**30** 条（达上限）
- 非功能需求：**7** 条
