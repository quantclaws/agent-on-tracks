# v0.1 规划文档评审 — Round 1

结论：**REVISE**

> **作者回应（round-1 处理）**：逐条批注见各问题下方 `↳ 回应`。采纳项已在基线提交 `2bb8e68` 之后的提交中实现，可 diff 查看。图例：✅ 采纳 / ◐ 部分采纳 / ❌ 不采纳（附理由）。

## 核心结论

### 用户现在能否走完一个故事？

**现有文档不能证明能走完。** 主路径虽然被列出，但原始需求会被固定 FakeAgent 文档覆盖；`init` 后如何保持工作区满足 `start` 的 clean gate 未定义；Human 直接修改文档后，`review revise` 如何识别、保留、校验并提交 diff 也没有闭合合同。

### 当前设计是否有明显技术坏味道？

**有。** 最严重的是“类型化契约”实际仍由 `str + dict` 组成、事件持久化与“唯一副作用边界”互相冲突、事件与 backlog 双真相，以及 write-ahead 缺少可关联身份。这些问题会在恢复、重试和后续完整 Flow 中形成分叉实现。

### 当前测试是否可能只是做样子？

**风险很高。** happy-path 使用与 stdin 无关的固定产物，并以事件日志为主要断言；即使 Runtime 生成错误 Story/Spec，只要按顺序写出预期事件，测试仍可能全绿。锁测试还有无法建立前置状态的问题。

已确认的 Human 决定，不作为问题：

- Python 导入包名为 `tracks`；
- 废除约 10 个核心模块的上限，不限制模块数量。

## 阻塞问题

### R1-01 FakeAgent 绕过原始需求

`test-plan.md` 规定 FakeAgent 不解析 stdin，story/spec 使用烘焙在生产代码中的固定文档。这样任意需求都能生成同一份 spec，E2E 仍然全绿，未验证核心旅程“原始需求 → Story → Spec”。

要求：确定性 FakeAgent 必须消费输入，最终 Story/Spec 应能追溯到 stdin；happy-path E2E 必须断言该关联。

> ↳ **回应（❌ 不采纳）**：用户已裁定。FakeAgent 在 v0.1（乃至之后）定位为**确定性路径选择器**，不是文档作者；e2e 的目标是穷举**重要工作流路径**、保证 tracks 部署到宿主环境后各种情况下工作流都能前进，而非验证需求文本的血缘。且 `stdin → story` 链已由 **AC-05a** 断言（start 把 stdin 原文写入 story.md 正文并提交）。透传哨兵只是把最平凡的"写文件→读文件"再验一遍，不新增任何状态机分支，属低价值。

### R1-02 副作用边界自相矛盾

`architecture.md` 一方面声明 `effects/` / executor 是唯一副作用边界，另一方面由 runtime 直接调用 `event_store.append()`，而 `kernel/store.py` 自己执行文件 I/O。

要求：统一定义。要么 EventStore 纳入 effects/executor 边界，要么明确区分“业务副作用边界”和“事件持久化边界”。

> ↳ **回应（✅ 采纳）**：architecture 明确区分两类 I/O 边界——`effects/executor` = **业务副作用**唯一边界（git / agent / 文档写），`kernel/store` = **事件持久化**唯一边界（append-only JSONL）。`project()` / `decide()` 仍零 I/O。原"唯一副作用边界"改述为此二分。

### R1-03 Write-ahead 缺少关联身份

事件信封缺少 wiki arch 已定义的 `task_id`；Command、Outcome、Verdict 缺少稳定的 `command_id` / `task_id` / attempt identity。当前 schema 无法可靠配对 `command.issued` 与结果，也无法证明崩溃恢复重新签发的是同一 assignment。

要求：补齐稳定身份与关联字段，并定义悬挂命令的投影和恢复规则。

> ↳ **回应（✅ 采纳）**：`Command` 增加 `command_id`（ULID）；结果事件携带 `command_id` 回指，配对不再依赖位置顺序。补充悬挂命令投影规则：`project()` 见 `command.issued` 无后继结果 → 恢复时按同 `command_id` / `task_id` 重签发（见 AC-29b）。Assignment 已有 `task_id`（run_id+substate+attempt）。

### R1-03b “类型化 Schema”仍是 stringly-typed

`EventEnvelope.payload: dict`、`Command.kind: str + params: dict`、`Transition.guard: str`、`command_kind: str` 没有形成可由类型系统校验的事件/命令联合类型。这会把字段拼写、payload 形状和非法转移错误推迟到运行期，也接近另造一个字符串工作流 DSL。

要求：事件、命令和 verdict 使用 discriminated union / 具体 dataclass；stage、substate、role、kind 使用 Enum 或 Literal；guard 使用明确的纯函数，而不是自由字符串。

> ↳ **回应（◐ 部分采纳）**：`type` / `kind` / `role` / `decision` / `verdict` / `check` / `status` / `stage` / `substate` 改用 `Literal` / `Enum`（封闭集，编译期查拼写）。per-event 具体 dataclass 判别联合方向认可，v0.1 先以 `Literal` 收口、判别联合列为后续。**但 `guard: str` 保留**：workflow 是**可序列化、可检视的声明式数据**（v0.2 的 doc↔code trace 工具依赖它），guard 由注册表按名解析为纯函数、由 workflow well-formedness 测试保证每个 guard 名可解析。把 guard 变成直接函数引用会破坏"workflow 即数据"这一支撑设计——此处是刻意取舍，非疏漏。

### R1-04 单写者 E2E 场景不可执行

AC-27a 假设 `trac run` 停在 `awaiting_human` 时仍持锁；但主循环在无 command 时退出并释放锁。

要求：用可控的长时间 FakeAgent 执行建立锁竞争，再启动第二个 `trac run`；不要依赖 awaiting_human 持锁。

> ↳ **回应（✅ 采纳）**：确为设计错误——主循环在 awaiting_human 处 `break` 并释放锁，无法在该点制造锁竞争。AC-27a 改为：用可控**阻塞式 FakeAgent**（长执行，`simulate=hang`）使第一个 `trac run` 执行期间持锁，再启第二个 `trac run` → exit 1。不再依赖 awaiting_human 持锁。

### R1-05 完整 Flow 尚未形成架构合同

架构声称从 13 阶段全景反推，但目前只为未来 stage 预留文件位置，没有覆盖完整用户故事中的基础合同：baseline digest/freshness、Human approval/release gate、task graph、B/R/G lineage、CI/artifact/security evidence、幂等 publish operation、milestone trace 闭包。

要求：参考 `story.md` 的参考文献，特别是 `wiki/flow.md`，补出能承载完整用户故事的核心身份、证据、恢复和副作用模型；v0.1 仍只实现前三阶段，不创建未来阶段空模块。

> ↳ **回应（◐ 部分采纳）**：采纳"补一段可扩展性声明"——architecture 显式说明 `kernel`（events/store/project/decide）**阶段无关**，未来阶段只增 `workflows/` 数据 + 事件类型，不改 kernel，这是承载完整 flow 的机制。**但不采纳在 v0.1 接口预先规定** baseline digest / lineage / CI evidence / publish 等未来阶段合同：这违反"v0.1 不创建未来阶段空模块"（评审自身亦要求），属 YAGNI；这些合同已在 `wiki/flow.md` 记录，将随实现各阶段的版本逐版规格化。

## 其他必须修正

### R1-06 既有权威文档仍有错误

- `decisions.md` 仍把任意 Story→Spec 错放到 v0.2+；
- `decisions.md` 仍声称 v0.1 不记录 backlog；
- `decisions.md` 仍引入未经要求的 attic tag；
- `wiki/arch.md` 仍使用错误的 `trac.db`，应为 `tracks.db`；
- `story.md` 尚未补全 stdin 与 `review revise` 路径。

> ↳ **回应**：`trac.db`→`tracks.db`（✅ 修正 arch.md 两处笔误）；"v0.1 不记录 backlog"（✅ D-06 与 spec/interfaces/AC 冲突，改述为 v0.1 记录**最小 backlog**——append `backlog.jsonl`，非完整子系统）；attic tag（✅ 删除——属实现细节，违反 decisions.md "技术细节不入本档"章程，且非用户要求）；"任意 Story→Spec 错放 v0.2+"（❌ 不采纳——D-03 是用户批准的范围决定，源自 story §范围排除，通用入口本就刻意留给 v0.2+）；story 的 stdin 已由 D-08+FR-02 闭合，revise 路径见 R1-11（采纳）。

### R1-07 DB 验收为空验证

架构声明 v0.1 完全跳过 `tracks.db`，Spec/Acceptance 却要求删除 DB 后重建状态。若 DB 从未创建，该 AC 会无条件通过。

要求：要么实现并验证可重建缓存，要么从 v0.1 Spec/Acceptance 移除该能力。

> ↳ **回应（✅ 采纳）**：确为空验证——architecture §10 说 v0.1 不建 `tracks.db`，删除不存在之物必然通过。AC-N04a 改为测**真实的事件溯源属性**：删除派生缓存（如 `runs.jsonl`）后，`status` / `replay` 仍能仅从 `events/*.jsonl` 折叠出正确终态。

### R1-08 Backlog 出现双重 authority

既有 `backlog.recorded` 事件，又定义独立 append-only `runtime/backlog.jsonl`，但未说明投影/重建关系。

要求：事件日志保持唯一真相；backlog 文件只能是明确可重建的投影，或是定义清楚的用户资产。

> ↳ **回应（✅ 采纳）**：定义 `backlog.jsonl` 为 `backlog.recorded` 事件的**可重建投影**（唯一真相仍是事件日志，与 tracks.db 同地位），architecture 与 D-06 同步说明。

### R1-09 AC/Test Plan 覆盖不完整

- FR-28 的 append-only 和 >8KB blob 外置没有对应 AC；
- AC-N06a 依赖 v0.1 明确排除的未来 `trac check dup`；
- happy-path 的多次 `trac run` 步骤与“持续运行到下一个 Human 等待点”的主循环语义不一致。

要求：补齐可在 v0.1 执行的外部断言，并让 E2E 步骤与主循环停止条件一致。

> ↳ **回应（◐ 部分采纳）**：AC-N06a 去掉对未来 `trac check dup` 的依赖（仅留代码审查）；FR-28 blob 外置补 AC-28b（store 单测，注入 >8KB payload 触发）；happy-path 各步 `trac run` 语义明确为"运行至下一个 Human 门或完成"，与主循环停止条件对齐。append-only 已由 seq 严格递增 + 恢复测试间接覆盖。

### R1-10 `init → start` 用户入口尚未闭合

`trac init` 必须建立 `.tracks/` 和 runtime 的 gitignore 规则，但 `trac start` 又要求工作区干净。当前 Spec 未定义 init 是否提交、是否要求用户先提交，或如何在不留下脏修改的情况下安装 `.gitignore`。按现有合同实现，用户可能第一步 init 后就被下一步 start 拒绝。

要求：明确并验收 `trac init` 后立即执行 `trac start` 的行为；happy-path 必须按用户真实命令顺序证明该入口可走通。

> ↳ **回应（✅ 采纳，与我发现的真缺口同族）**：确为入口断裂——init 写 `.tracks/` 脚手架（含 runtime 的 `.gitignore`）后工作区变脏，start 的 clean-gate 会拒绝。FR-01 补：init **提交自身脚手架**（一条 `chore: init tracks` 提交），结束后工作区干净；happy-path 按真实 `init → start` 顺序验证入口贯通。附带把 FR-02（空 stdin）/FR-03（脏工作区）两条拒绝路径从 happy-path 拆入独立 `test_start_guards.py`。

### R1-11 Human revise 路径只有事件，没有文档闭环

Flow 规定 Human 直接修改文档后执行 `trac review revise`，但 Interfaces/Acceptance 没有定义 Runtime 如何取得 Human diff、限制 scope、处理未知脏文件、提交修改并把 diff 传入 RESPOND。当前测试只断言 `human.review(comment)` 和下一次分派，可能完全不验证 Human 修改是否被保留和响应。

要求：补齐“Human 编辑 → diff 捕获 → scope/schema 校验 → Runtime commit → RESPOND assignment”的外部合同与 E2E。

> ↳ **回应（✅ 采纳）**：补 revise 闭环合同——Human 在磁盘改文档后 `trac review revise`：Runtime 捕获 in-scope 文档的工作区 diff、校验 scope（若有白名单外脏文件则拒绝）、以 Human 署名提交、记 `human.review(comment, diff_ref=commit_sha)`，RESPOND 分派携 `diff_ref`。补 AC 断言 Human 修改被保留并进入下一轮。

## 通过后门槛

1. 所有阻塞问题关闭；
2. FR↔AC↔Test Plan 双向无孤项；
3. Architecture、Interfaces、Flow 使用同一身份与副作用术语；
4. happy-path E2E 真实覆盖 `init → start(stdin) → M-STORY → M-SPEC → status → replay`；
5. happy-path 断言最终 Story/Spec 的内容与输入需求、评审修改相关，而不只断言事件顺序；
6. 再进入脚手架或业务实现。
