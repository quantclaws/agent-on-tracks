# v0.1 规划文档评审 — Round 1

结论：**REVISE**

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

### R1-02 副作用边界自相矛盾

`architecture.md` 一方面声明 `effects/` / executor 是唯一副作用边界，另一方面由 runtime 直接调用 `event_store.append()`，而 `kernel/store.py` 自己执行文件 I/O。

要求：统一定义。要么 EventStore 纳入 effects/executor 边界，要么明确区分“业务副作用边界”和“事件持久化边界”。

### R1-03 Write-ahead 缺少关联身份

事件信封缺少 wiki arch 已定义的 `task_id`；Command、Outcome、Verdict 缺少稳定的 `command_id` / `task_id` / attempt identity。当前 schema 无法可靠配对 `command.issued` 与结果，也无法证明崩溃恢复重新签发的是同一 assignment。

要求：补齐稳定身份与关联字段，并定义悬挂命令的投影和恢复规则。

### R1-03b “类型化 Schema”仍是 stringly-typed

`EventEnvelope.payload: dict`、`Command.kind: str + params: dict`、`Transition.guard: str`、`command_kind: str` 没有形成可由类型系统校验的事件/命令联合类型。这会把字段拼写、payload 形状和非法转移错误推迟到运行期，也接近另造一个字符串工作流 DSL。

要求：事件、命令和 verdict 使用 discriminated union / 具体 dataclass；stage、substate、role、kind 使用 Enum 或 Literal；guard 使用明确的纯函数，而不是自由字符串。

### R1-04 单写者 E2E 场景不可执行

AC-27a 假设 `trac run` 停在 `awaiting_human` 时仍持锁；但主循环在无 command 时退出并释放锁。

要求：用可控的长时间 FakeAgent 执行建立锁竞争，再启动第二个 `trac run`；不要依赖 awaiting_human 持锁。

### R1-05 完整 Flow 尚未形成架构合同

架构声称从 13 阶段全景反推，但目前只为未来 stage 预留文件位置，没有覆盖完整用户故事中的基础合同：baseline digest/freshness、Human approval/release gate、task graph、B/R/G lineage、CI/artifact/security evidence、幂等 publish operation、milestone trace 闭包。

要求：参考 `story.md` 的参考文献，特别是 `wiki/flow.md`，补出能承载完整用户故事的核心身份、证据、恢复和副作用模型；v0.1 仍只实现前三阶段，不创建未来阶段空模块。

## 其他必须修正

### R1-06 既有权威文档仍有错误

- `decisions.md` 仍把任意 Story→Spec 错放到 v0.2+；
- `decisions.md` 仍声称 v0.1 不记录 backlog；
- `decisions.md` 仍引入未经要求的 attic tag；
- `wiki/arch.md` 仍使用错误的 `trac.db`，应为 `tracks.db`；
- `story.md` 尚未补全 stdin 与 `review revise` 路径。

### R1-07 DB 验收为空验证

架构声明 v0.1 完全跳过 `tracks.db`，Spec/Acceptance 却要求删除 DB 后重建状态。若 DB 从未创建，该 AC 会无条件通过。

要求：要么实现并验证可重建缓存，要么从 v0.1 Spec/Acceptance 移除该能力。

### R1-08 Backlog 出现双重 authority

既有 `backlog.recorded` 事件，又定义独立 append-only `runtime/backlog.jsonl`，但未说明投影/重建关系。

要求：事件日志保持唯一真相；backlog 文件只能是明确可重建的投影，或是定义清楚的用户资产。

### R1-09 AC/Test Plan 覆盖不完整

- FR-28 的 append-only 和 >8KB blob 外置没有对应 AC；
- AC-N06a 依赖 v0.1 明确排除的未来 `trac check dup`；
- happy-path 的多次 `trac run` 步骤与“持续运行到下一个 Human 等待点”的主循环语义不一致。

要求：补齐可在 v0.1 执行的外部断言，并让 E2E 步骤与主循环停止条件一致。

### R1-10 `init → start` 用户入口尚未闭合

`trac init` 必须建立 `.tracks/` 和 runtime 的 gitignore 规则，但 `trac start` 又要求工作区干净。当前 Spec 未定义 init 是否提交、是否要求用户先提交，或如何在不留下脏修改的情况下安装 `.gitignore`。按现有合同实现，用户可能第一步 init 后就被下一步 start 拒绝。

要求：明确并验收 `trac init` 后立即执行 `trac start` 的行为；happy-path 必须按用户真实命令顺序证明该入口可走通。

### R1-11 Human revise 路径只有事件，没有文档闭环

Flow 规定 Human 直接修改文档后执行 `trac review revise`，但 Interfaces/Acceptance 没有定义 Runtime 如何取得 Human diff、限制 scope、处理未知脏文件、提交修改并把 diff 传入 RESPOND。当前测试只断言 `human.review(comment)` 和下一次分派，可能完全不验证 Human 修改是否被保留和响应。

要求：补齐“Human 编辑 → diff 捕获 → scope/schema 校验 → Runtime commit → RESPOND assignment”的外部合同与 E2E。

## 通过后门槛

1. 所有阻塞问题关闭；
2. FR↔AC↔Test Plan 双向无孤项；
3. Architecture、Interfaces、Flow 使用同一身份与副作用术语；
4. happy-path E2E 真实覆盖 `init → start(stdin) → M-STORY → M-SPEC → status → replay`；
5. happy-path 断言最终 Story/Spec 的内容与输入需求、评审修改相关，而不只断言事件顺序；
6. 再进入脚手架或业务实现。
