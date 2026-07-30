---
doc: decisions
status: active
last_updated: 2026-07-30
---

# 已决定事项

> 本文档记录人类裁定（或从语境直接推出）的事项。任何"已决定"必须来自：
>
> - 用户在对话中的明确表态；
> - 已审定的 v0.1 story；或
> - 已审定的 arch/flow 段落。
>
> 技术实现细节（schema、字段命名、模块划分）**不进入本文档**，由 agent 自行推导；本文档只沉淀"用户层与产品边界"的决定。
>
> 与本文件属性不同的待决、暂行、悬置事项，必须迁到 `open_questions.md`，不能并存。

---

## D-01. 目录与运行时命名

- 项目根目录：**`~/workspace/tracks`**
- 命令行命令：**`trac`**
- Python 导入包名：**`tracks`**（2026-07-30 用户裁定：文档与代码一律使用 `tracks`，不用 `track`；与数据目录、PyPI 名一致）
- PyPI 包名：**`agent-on-tracks`**
- 项目数据目录：**`.tracks/`**（与导入包名一致）

> 早期另有 `.track/` 残留以避免与 GitHub Pages 经典项目心智撞车 + 让目录名与命令/包名不冲突。已清理 `.track/`。

## D-02. 真相源与存储：SQLite 事件溯源（自 v0.1 起采用）

**问题（用户提出，2026-07-30）**：events 用 JSONL，是这版实现从简，还是"80% 工程都不必用数据库、JSONL 就够"？考虑到 backlog 要实现、且 **v0.15 质量看板必须落地**，sqlite 事实上不可避免——若无特殊理由，是否应从一开始就用数据库，否则后面有不必要的迁移成本？此外长期多版本（v0.1…v0.15）下，扁平 JSONL 会否出现文件名/归属冲突？

**结论/裁定**：自 v0.1 起即以 **SQLite 事件溯源**为存储，不走"先 JSONL 后迁移"。

- **数据模型（承重、永久）**：事件溯源。`events` 表 = **唯一真相源**，只追加（INSERT，`seq` 每 run 单调递增，主键 `(run_id, seq)`）；`runs` / `backlog` / 当前态等为**派生投影表**，drop 后可从 `events` 完整重建。**禁止**状态式原地改行。
- **物理存储选 SQLite 的理由**：① v0.15 看板需跨 run / 跨版本 SQL 聚合，DB 不可避免——与其"JSONL 真相 + 事后加 sqlite 缓存"两套机制，不如**一套**；② ACID 事务令"落事件 + 更新投影"原子化，**消掉 torn-write 半行恢复**这一类代码；③ 标准库 `sqlite3`，零新增依赖；④ `runtime/` 已整目录 gitignore，事件不进 git，JSONL 的可 diff/可 review 优势不成立。
- **多版本归属**：事件信封与 `runs` 投影均含 **`version` 字段**（发布版本，如 `0.1`），作为长期多版本的一等查询维度（看板 `GROUP BY version`）。**不**靠目录层级或文件名版本号（会切碎日志、反害跨版本聚合）；ULID `run_id` 本已全局唯一，无命名冲突。
- **两根版本轴勿混**：`schema_version` = 事件 payload 结构版本（随 trac 发布演进、供 upcast）；`version` = 宿主项目发布版本。
- **唯一被禁止的动作**：让任何投影/索引成为权威源或持有决策字段——那才会造成真正的迁移代价。
- **blobs**：payload >8KB 仍内容寻址落 `runtime/blobs/{sha256}`，事件里放 `$ref`，保持 db 精简。

## D-03. v0.1 体验范围

- v0.1 范围：**M-START → M-STORY → M-SPEC**，再到 `stage.exited(M-SPEC)`，包含 `spec.md` 落 sha 为止。
- v0.1 不实现：M-ACC+、反 slop 工具、GitHub 集成、Web UI、inline-comments 完整协议、真 LLM Agent。
- **入口澄清（回应 R2-09）**：`trac start <version>` 从 stdin 接收原始需求（D-08），是 v0.1 唯一入口——它走**固定** M-START→M-STORY→M-SPEC 通路。这与"丢任意已有 story 文档直接生成 spec"的**通用入口**是两件事：后者属 **v0.2+**，v0.1 不做（见 story §范围排除）。故 `start <version>` 与 D-08 不冲突：version 参数在 v0.1 仅接受 `v0.1`。

## D-04. v0.1 CLI（人类入口全集）

```text
trac init
trac start v0.1
trac run
trac triage go|no-go|park
trac review no-comment
trac review revise
trac status
trac replay <run-id>
```

- `triage`：`human.triage(...)` 的人类命令入口，命令参数与写入事件类型一一对应。
- `review no-comment | revise`：评审意见落地，纯文本 diff。无完整 inline-comments。
- `status`：状态查询命令；与 `replay` 是不同语义。
- `replay <run-id>`：执行动作——回放事件并打印，不与 `status` 合并。

> flow.md 第 92 行（mermaid RESPOND 状态）的语义等同上述 `review revise`。

## D-05. 状态查询 vs 回放

- `trac status` 只读取当前活跃 run 的最末状态、子状态、待处理事件——**不**回放全部事件。
- `trac replay <run-id>` 是执行类动作：读取该 run 的全部事件并打印，再补终态摘要（选项 B）。
- 这两个命令是**两类语义**，不能合并为一条；任何状态查询不应用 `replay` 的全 fold 实现冒充。

## D-06. NO-GO / PARK / scope_overflow 的分支命运

- **`human.triage(no_go)` / `human.triage(park)`**：记入 backlog + 删除 `releases/v0.1` 分支（flow §4.1 REJECTED）。v0.1 的 backlog 是**最小实现**——`record_backlog` 命令追加 `backlog.recorded` 事件，投影为 `backlog` 表（无独立子系统/UI）；分支删除后 story.md 随之消失，条目仅留存于 backlog。
- **`scope_overflow (FR>30)`**：属于需求调整，**不删分支**；保留 release branch 历史并返回 M-STORY 重新切片。（历史保留的具体机制——是否打 tag、如何命名——尚未经用户裁定，留待 spec 明确，不在此臆造。）

> 用户裁定：NO-GO/PARK 路径下"新建分支只包含 story.md，因此删除分支无可惜"。

## D-07. 错误恢复与单写者

- **双进程同跑两个 `trac run`**：第二个进程**立即失败并退出**，stderr 报告 lock holder（行为种子 §11）。
- 不存在"等锁"路径——双引擎并跑只在出错时发生，不做轮询退让。
- 单写者锁：`runtime/lock` 文件 + 进程 PID；锁失效（崩溃残留）如何处理由 agent 自决。

## D-08. v0.1 原始需求入口（story.md 来源）

- `trac start v0.1` **从 stdin 接收原始需求**。
- 原始需求被 Runtime 写入 `.tracks/projects/v0.1/story.md`，并设置 frontmatter（含 `story_id`、`created`、`status: draft`、空 `title`、空 `sha`）。
- **`title` 字段**（用户裁定 2026-07-30）：frontmatter 必含 `title`——人类讨论问题时用有意义的名字引用一个 story，而非编号（人不擅长记数字）。start 时留空；由 Scribe 在 DRAFT 起草时写入非空的有意义名，schema 校验其非空（FR-11）。与 `sha`（start 留空、EXIT 填入）同构。
- 之后 `trac run` 启动 M-STORY 主循环。
- 用户无须先自行创建 story.md 文件；也不接受 `trac start` 通过文件路径传原始需求——v0.1 体验以 stdin 入口为准。
- 如未来 v0.2+ 支持 `--story <file>` / 配置路径等通用入口，另立决策条目。

## D-09. 用户参与方式

v0.1 全部人类动作通过 CLI 命令传入（见 D-04）。人类不编辑事件文件、不直接动 `tracks.db`、不绕过 `trac` 直接 push。

## D-10. 文档与流程之间的关系

- arch.md / flow.md：已审核的架构文档，与本文档并列存在，**不互相覆盖**。
- v0.1 story：v0.1 范围与行为种子的唯一真源；它与 arch/flow 不一致时，以 **story 优先** + 进入 `open_questions.md` 待处理。
- new wiki page：`decisions.md`（本文件）沉淀已裁决的产品层决定；`open_questions.md` 沉淀待决与暂行；agent 推导技术细节不入 wiki。

---

## D-11. 取消协议：意图走信号，事实走日志

- 取消一个正在执行的 run 的唯一通道是向持锁进程发 OS 信号（PID 在 `runtime/lock` 中）；由唯一写者（runtime 自己）将其转成事实：终止 Agent 子进程、落 `run.interrupted` 事件、释放锁、exit 130。单写者不变量不破。
- v0.1 前台运行，Ctrl-C 即此通道；**不新增 `trac cancel` 命令**（D-04 CLI 集合不变），`cancel` 推迟到接入真实 LLM Agent 的版本。
- 取消发生在 `command.issued` 已落盘而结果未落盘时：恢复后 decide 重新签发同一 assignment，**不消耗 attempt**（取消是人类决定，不是 Agent 失败）。

---

## D-12. dispatch 事件模型：`command.issued` 即派发事实

（用户裁定 2026-07-30，回应 R2-02）一次 `dispatch_agent` **不产生**独立的 `assignment.dispatched` 事件——删除该事件类型。`command.issued(dispatch_agent)`（其 payload 已含 `assignment`）本身就是**唯一的 write-ahead 派发事实**：它在 executor 阻塞执行 Agent 之前已落盘，Agent 返回后落 `outcome.received`。正常/hang/SIGINT/kill-9 四条时序都由这一对"issued→（阻塞）→outcome"表达，悬挂即 issued 无 outcome。理由：单结果主循环无法一命令产两事件，且 assignment 本就在命令 payload 内，第二个事件冗余。

## D-13. 副作用可恢复性：per-kind reconcile

（用户裁定 2026-07-30，回应 R2-03）稳定 `command_id` 只能**识别**操作、不能使其幂等；崩溃可能发生在 git/文件写已成功、结果事件未落盘之后，SQLite ACID 管不到边界另一侧。故每个 `Command.kind` 定义 **`reconcile（查真实世界事实）→ execute if needed → observe`** 规则：恢复悬挂命令前先查 git/文件系统实际状态，已完成则跳过、仅补记结果事件（如 `commit_document` 先 `git log --grep=<command_id>` 查该提交是否已存在）。杜绝重复提交/空提交/重复删分支。逐 kind 规则见 architecture §5e。

## 决策日志

| ID    | 决定日期       | 标题                           | 来源                                                              |
| :---- | :------------ | :----------------------------- | :---------------------------------------------------------------- |
| D-01  | 2026-07-30    | 目录与运行时命名                | 用户表态 `目录名已确定为 .tracks, 数据库名为 tracks.db`           |
| D-02  | 2026-07-30    | 真相源与存储：SQLite 事件溯源    | 用户裁定：v0.15 看板必须 + DB 不可避免；arch.md §2、§5             |
| D-03  | 2026-07-30    | v0.1 体验范围                   | story §原始输入、§范围排除                                         |
| D-04  | 2026-07-30    | v0.1 CLI 集合                   | 用户表述 `trac review no-comment` 之外 `trac review revise`        |
| D-05  | 2026-07-30    | status vs replay 不同语义       | 用户表述 `trac status 是查询动作, replay 是执行动作, 两者怎混`     |
| D-06  | 2026-07-30    | NO-GO/PARK/scope_overflow 分支  | 用户裁定 + flow §4.1                                              |
| D-07  | 2026-07-30    | 双进程单写者                   | 用户裁定 + story §行为种子 §11                                     |
| D-08  | 2026-07-30    | v0.1 输入来源：stdin           | 用户裁定：`从命令行 stdin 接收`                                   |
| D-09  | 2026-07-30    | 用户参与方式                    | 用户裁定 + flow.md 不变量 1                                       |
| D-10  | 2026-07-30    | 文档层级关系                    | arch/flow/story/decisions.md 责任分工                               |
| D-11  | 2026-07-30    | 取消协议与 cancel 推迟          | 用户裁定：并发取消需求 + 同意 v0.1 仅 Ctrl-C                        |
| D-12  | 2026-07-30    | dispatch 事件模型（删 assignment.dispatched） | 用户裁定（R2-02 内联）：`command.issued(dispatch_agent)` 即派发事实 |
| D-13  | 2026-07-30    | 副作用 per-kind reconcile       | 用户裁定（R2-03 内联）：reconcile→按需执行→观察结果                 |
