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

## D-02. 真相源单点化

- 宿主项目里的真相源：**`.tracks/runtime/events/`** JSONL 日志。
- `tracks.db`：**可抛弃投影缓存**——删除后必须可由事件回放完整重建。
- `.tracks/runtime/tracks.db` 不持有任何业务决策性字段；与 `status` / `replay` 等命令相比，仅作为索引或派生副本。

## D-03. v0.1 体验范围

- v0.1 范围：**M-START → M-STORY → M-SPEC**，再到 `stage.exited(M-SPEC)`，包含 `spec.md` 落 sha 为止。
- v0.1 不实现：M-ACC+、反 slop 工具、GitHub 集成、Web UI、inline-comments 完整协议、真 LLM Agent。
- "丢任意 story 生成 spec" 的通用入口是 **v0.2+**；v0.1 仅支持 `v0.1` 这一个项目的固定通路。

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

- **`human.triage(no_go)` / `human.triage(park)`**：记入 backlog + 删除 `releases/v0.1` 分支（flow §4.1 REJECTED）。**v0.1 不实现 backlog 子系统**——实际效果是 story.md 与分支一起消失，下次重头来。
- **`scope_overflow (FR>30)`**：属于需求调整，**不删分支**；Runtime 在 release branch 上打 attic tag（如 `attic/{ver}-pre-spec`）保留历史，并返回 M-STORY 重新切片。

> 用户裁定：NO-GO/PARK 路径下"新建分支只包含 story.md，因此删除分支无可惜"。

## D-07. 错误恢复与单写者

- **双进程同跑两个 `trac run`**：第二个进程**立即失败并退出**，stderr 报告 lock holder（行为种子 §11）。
- 不存在"等锁"路径——双引擎并跑只在出错时发生，不做轮询退让。
- 单写者锁：`runtime/lock` 文件 + 进程 PID；锁失效（崩溃残留）如何处理由 agent 自决。

## D-08. v0.1 原始需求入口（story.md 来源）

- `trac start v0.1` **从 stdin 接收原始需求**。
- 原始需求被 Runtime 写入 `.tracks/projects/v0.1/story.md`，并设置 frontmatter（含 `story_id`、`created`、`status: draft`、空 `sha`）。
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

## 决策日志

| ID    | 决定日期       | 标题                           | 来源                                                              |
| :---- | :------------ | :----------------------------- | :---------------------------------------------------------------- |
| D-01  | 2026-07-30    | 目录与运行时命名                | 用户表态 `目录名已确定为 .tracks, 数据库名为 tracks.db`           |
| D-02  | 2026-07-30    | 真相源单点化                    | arch.md §2、§5                                                    |
| D-03  | 2026-07-30    | v0.1 体验范围                   | story §原始输入、§范围排除                                         |
| D-04  | 2026-07-30    | v0.1 CLI 集合                   | 用户表述 `trac review no-comment` 之外 `trac review revise`        |
| D-05  | 2026-07-30    | status vs replay 不同语义       | 用户表述 `trac status 是查询动作, replay 是执行动作, 两者怎混`     |
| D-06  | 2026-07-30    | NO-GO/PARK/scope_overflow 分支  | 用户裁定 + flow §4.1                                              |
| D-07  | 2026-07-30    | 双进程单写者                   | 用户裁定 + story §行为种子 §11                                     |
| D-08  | 2026-07-30    | v0.1 输入来源：stdin           | 用户裁定：`从命令行 stdin 接收`                                   |
| D-09  | 2026-07-30    | 用户参与方式                    | 用户裁定 + flow.md 不变量 1                                       |
| D-10  | 2026-07-30    | 文档层级关系                    | arch/flow/story/decisions.md 责任分工                               |
| D-11  | 2026-07-30    | 取消协议与 cancel 推迟          | 用户裁定：并发取消需求 + 同意 v0.1 仅 Ctrl-C                        |
