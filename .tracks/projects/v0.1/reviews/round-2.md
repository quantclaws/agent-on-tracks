# v0.1 规划文档评审 — Round 2

结论：**REVISE**

本轮接受 Decisions 中已记录的 Human 裁定，包括 `tracks` 包名、不限制模块数量、从 v0.1 起采用 SQLite 事件溯源、`title` 字段和信号取消协议。SQLite 统一真相源并消除 JSONL torn-write，是有效改进。

## 阻塞问题

### R2-01 核心用户故事仍未被实现或测试

定位：`test-plan.md:107-112,153-155`；`architecture.md:183-193`；`story.md:15-18,25-35`；`.tracks/wiki/decisions.md:89-96`。

Round 1 对 FakeAgent 的问题不能按当前回应关闭。这里不要求 FakeAgent 具备 LLM 的语义质量，只要求存在**因果链**：用户从 stdin 提交的需求必须影响最终 Story/Spec。当前 Test Plan 明确让 FakeAgent 忽略 stdin，并用固定文档覆盖产物；因此输入“做计算器”和“做发布工具”仍可得到同一份 spec，E2E 也全绿。

Decisions 中没有“FakeAgent 可以忽略需求”的 Human 裁定；D-08 反而确认 stdin 是原始需求入口，Story 也要求 Scribe 从需求起草 Story、Sage 从 Story 起草 Spec。

要求：确定性 FakeAgent 可以简单，但必须消费上下文并保留可断言的需求血缘；happy-path 必须断言最终 Story/Spec 与 stdin、Human revise 有关联，而不只断言初始写文件和事件顺序。

> **[RESOLVED] Aaron（❌ 不采纳，理由见 D-14）：** 此处混淆了两层职责。**(1) 形式/schema 校验**是 runtime 的确定性职责、可测——Scribe 的 story.md、Sage 的 spec.md 若不合规（schema、scope、story→spec 结构化覆盖 trace），`validate` 必然抓住；e2e 正是测这层管路贯通 + 前进性。**(2) story→spec 的语义忠实度**取决于 LLM 把 story"翻译"成 spec 的能力，本质上无法确定性断言；引用/slop 是否成立由**评审类 Agent**（Lex/Prism + 反 slop 引用/trace）把关，不是 runtime 测试能覆盖的。stdin→story 血缘已由 AC 断言（start 原文写入 story.md）。评审要求的"断言 spec 与 stdin 语义关联"落在 (2)：v0.1 无 LLM，也不该用回显哨兵去伪装覆盖——那只会制造虚假信心。故绿灯据实限定为"管路贯通 + 形式校验生效 + 前进性"，不宣称覆盖语义血缘。

### R2-02 dispatch 的事件时序按当前主循环无法实现

定位：`architecture.md:26-40,183-193`；`interfaces.md:39-50,71-83,111-120`；`test-plan.md:145-153`。

主循环是“`command.issued` → `executor.execute()` → append 单个 result”。但一次 `dispatch_agent` 至少需要两个事实：Agent 开始前的 `assignment.dispatched`，以及 Agent 返回后的 `outcome.received`。阻塞式 FakeAgent 执行期间尤其必须已经落下 dispatched 事实。

当前 `execute(cmd) -> result` 在 Agent 返回前无法追加 `assignment.dispatched`，也没有定义一个命令如何产生两段事件。这会直接破坏锁竞争、取消和悬挂 assignment 恢复测试。

要求：拆成可 write-ahead 的两步命令，或明确 executor/runtime 的分段结果协议；必须画出正常、hang、SIGINT、kill -9 四条精确事件序列。

> **qoder:** 成立。louke flow.md 不变量即采用 write-ahead：先落 `assignment.dispatched`，Agent 返回后再落 `outcome.received`，两段事实分离。v0.1 的 command/result 协议应显式支持一个命令产生多段事件，否则 hang/SIGINT/kill -9 的恢复路径无从测试。

> **Aaron:**  把 command.issued(dispatch_agent) 当作唯一的 write-ahead 派发事实，删掉 assignment.dispatched（最简，assignment 本就在命令 payload 里

### R2-03 “同 ID 重签发”不等于副作用可恢复

定位：`architecture.md:168-181`；`interfaces.md:71-83`；`acceptance.md:204-212`。

崩溃可能发生在 Git commit/branch/file write 已成功、结果事件尚未落盘之后。直接以同 `command_id` 再执行，会重复提交、遇到空提交、覆盖文档或再次删除分支。稳定 ID 只能识别操作，不能自动使操作幂等。

要求：每种 v0.1 副作用定义 `reconcile → execute if needed → observe result` 规则；恢复悬挂命令前先检查 Git/文件系统实际事实。测试至少覆盖“commit 已成功但 result 未记录”的恢复。

> **qoder:** 成立。稳定 command_id 只识别操作，不赋予幂等性。louke M-PUBLISH 即按 `reconcile → execute if needed → observe result` 处理外部副作用，恢复前先查 Git/文件系统实际事实。v0.1 的 commit/branch/file write 需逐一定义 reconcile 规则，并测试“commit 已成功但 result 未记录”的恢复。

> **Aaron:** 给每种 Command.kind 定义 reconcile → 按需执行 → 观察结果 规则（如 commit_document 先查 git log 有没有该 message/sha 再决定是否提交）

### R2-04 Schema 的采纳没有真正落到接口

定位：`architecture.md:175-180`；`interfaces.md:18-35,39-71,85-98,111-175`。

- Architecture 的 `events` 表有 `command_id/task_id` 列，但 `EventEnvelope` 没有这两个字段；Interfaces 又称信封字段即表列；
- 结果事件表格没有列出 `command_id`，`Verdict` 也没有 `command_id`；
- 文档声称 stage/substate/status 使用 Literal/Enum，实际 `State`、`StageDef`、`Transition` 仍是 `str`；
- `payload` 和 `Command.params` 仍是无 schema 的 `dict`，per-event 类型被推迟；
- `task_id = run_id + substate + attempt` 在第二个 review round 会重复，缺少 review_round/assignment identity。

要求：v0.1 现在就提供具体 EventPayload/Command 联合类型，并使文档中的 Python schema、SQLite 列和事件表格逐字段一致。不要把核心类型安全推迟到后续版本。

### R2-05 完整 Flow 的扩展结论仍自相矛盾

定位：`architecture.md:53-84`；`interfaces.md:14-19`；`.tracks/wiki/flow.md:238-255,286-349,379-415,430-455`。

Architecture 声称未来只增加 workflow 数据和事件类型、kernel 不改；但事件类型集中在 `kernel/events.py`，增加事件类型本身就是修改 kernel。并且完整 Flow 的 baseline freshness、证据 identity、RGR lineage、外部 operation reconcile 都会影响通用投影与恢复模型。

要求：不需要在 v0.1 实现未来阶段，也不需要创建空模块；但必须从 `flow.md` 提炼稳定的跨阶段身份、证据和幂等操作扩展接口，说明未来阶段如何扩展而不修改纯内核协议。当前“到时往 events.py 加类型”不能证明架构承载了整个用户故事。

> **Aaron:** 即使未来要进行修改，这也没什么不可以吧。

## 测试与一致性问题

### R2-06 多个测试场景仍不可执行或与设计不一致

定位：`acceptance.md:77-78,164-184,190-206`；`test-plan.md:31-42,56-66,76-93`。

- AC-29a 仍要求 kill 一个停在 `awaiting_human` 的 `trac run`，但当前设计在该状态无 command 时正常退出并释放锁；这里首先是 **AC 与设计不一致**，不是必须把设计改成常驻进程。Flow/Story 只要求等待人类后可停止并重启恢复。AC-29a 应改为：`trac run` 正常退出为 awaiting_human，之后新进程执行 Human action + 再次 `trac run`，断言不重复分派且状态精确恢复；真正的 kill 应由 AC-29b 覆盖 Agent 执行中的中断；
- happy-path 第 7 步声称 run 停在 M-SPEC DRAFT，第 8 步再运行到 Human；按“运行到下一个 Human 门”的规则，第 7 步应直接跑到 M-SPEC HUMAN_REVIEW；
- AC-09a 仍断言 backlog **文件**，当前设计已改为 SQLite backlog 投影表；
- AC-28b 写为 store 单测，但 Test Plan 把 `test_store.py` 放在 integration 目录，并且 Acceptance 自称只断言外部接口；
- AC-23a 要 status 报告 completed，AC-24b 又说无活跃 run 时只报告无活跃 run，完成后的用户输出未定。

要求：逐条按真实进程生命周期修正，并实际走读完整 CLI 序列，确保每一步都有可建立的前置状态。

### R2-07 append-only 没有被证明

定位：`spec.md:75-77`；`architecture.md:173-181`；`acceptance.md:193-198`；`round-1.md:104-112`。

`(run_id, seq)` 唯一和 seq 递增不能证明旧事件不会 UPDATE/DELETE。“间接覆盖”不足以验收唯一真相源不可改写。

要求：存储 API 不暴露 update/delete；测试记录已有事件内容，运行后续流程及投影重建后断言旧行逐字节不变。若采用 SQLite trigger 强制不可改写，也应测试拒绝语义。

## 产品决定与源文档

### R2-08 `init` 自动提交仍缺少完整用户合同

定位：`spec.md:33-38`；`acceptance.md:21-27,42-52`；`.tracks/wiki/flow.md:43-50`。

自动创建 Git commit 是新的外部行为，不只是内部修复。当前未定义：不在 main 时 init 提交到哪里、main 尚不存在时怎么办、Git author 未配置时如何失败、已有 `.tracks/` 部分结构如何 reconcile。若 init 在 feature branch 提交，而 start 固定从 main 建 release，初始化资产不会出现在 release branch。

要求：在 Spec/Acceptance 中补齐这些前置与失败语义，或重新裁定 init 不自动提交的方案；happy-path 之外至少覆盖“当前不在 main”和“无可用 main”场景。

> **qoder:** louke v0.14-004 FR-0701 已定义这套环境 readiness 门禁，init 应直接采纳：依次确认 (1) 当前目录存在且为 git repo；(2) `gh` 已安装可执行；(3) `gh auth status` 确认目标 host 已登录；(4) token scopes 同时含 `gist`/`project`/`repo`/`workflow`。任一缺失或结果不确定 → fail closed，明确指出未满足项 + 阻断影响 + 可执行修复位置，要求用户补齐；不自动安装工具、不自动改认证（须 Human 授权）。外部检查需有界等待 + 新鲜事实，超时/不可解析/网络错误 fail closed，不沿用过期成功。secret（token/credential）不进日志/事件/错误详情，只展示 redacted identity + scope 名。注意 louke 口径：scope 齐全只表示本门禁通过，后续 push/workflow/release 仍以各自真实结果为准。

> **Aaron:** init 要检查当前目录是否存在，是否为git repo，检查 gh 命令是否存在,gh 是否授权；授权是否包含了 'delete_repo', 'project', 'repo', 'workflow'等。init 要补齐这些信息，在信息不全时，要求用户输入;权限 不足时，要先自行刷新权限。

### R2-09 权威材料仍有未裁定冲突

定位：`.tracks/wiki/decisions.md:44-49,70-74,89-96`；`story.md:21-38`；`spec.md:33-38`；`.tracks/wiki/arch.md:35-63`；`architecture.md:42-44`；`round-1.md:34,42,86,94`。

- D-03 称 v0.1 只支持固定 `v0.1` 通路、任意 Story→Spec 属于 v0.2+；但 D-08、Spec `start <version>` 及 Story 的宿主项目入口表达的是原始需求输入。D-03 的来源“Story 范围排除”实际没有这条排除；
- Story 的核心路径仍未写 stdin，也只写了 `review no-comment`，没有同步已批准的 revise 路径；
- wiki arch 仍写“执行器唯一副作用边界”，与 v0.1 Architecture 新定义的“两类 I/O 边界”不一致；
- Round 1 回应仍残留 JSONL 表述，已与 SQLite 决策过时。

要求：先统一 Story、Decisions、wiki arch 和 v0.1 五份规划文档，再进入脚手架。

## 通过门槛

1. 用两个不同 stdin 输入证明最终 Story/Spec 存在不同且可追溯的产物；
2. 正常、阻塞、取消、崩溃四条 dispatch 事件序列均可实现、可恢复；
3. 所有副作用具备 command-specific reconcile 规则；
4. Schema、SQLite 表和事件类型逐字段一致且无自由 `dict` 核心契约；
5. 完整 happy-path 按真实 CLI 进程生命周期走读无矛盾；
6. 再进入脚手架或业务实现。
