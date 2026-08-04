---
story_id: S-004
title: 推进流程到 M-TEST 阶段（含需求追踪）
created: 2026-08-04
status: draft
sha:
---

# S-004: 推进流程到 M-TEST 阶段（含需求追踪）

## 1. 原始输入

> 2. 绿的粒度问题 -- 对，不能让 devon 每实现一部分代码，就运行（runtime）一次全部的 end to end，这样会比较花时间。只能运行对应的 int 测试。如果能指定 e2e 子集也可以，不过我觉得 devon 的第一轮实现最好不运行 end to end / 其它没有问题，请修改 flow.md。这是v0.4的内容？不管是不是，我们需要一个新的 release 来将流程推进到 M-TEST 阶段。请着手规划，并完成 shield.md(请借鉴 louke)
>
> —— Aaron，2026-08-04

> 这个功能放0.5，是次序错误吧。M-TEST 没有实现的话，需求追踪不可能完整实现吧？
>
> 当 M-TEST 实现后，尽管由于 Devon 没有实现，但已经可以做静态的需求追踪。而且正是需求追踪，也让 Shield 的工作多了一个评估指标 -- 它写的测试脚本够不够，有没有覆盖需求
>
> —— Aaron，2026-08-04

> 不，需求追踪应该是 M-TEST 的一部分。不做完需求追踪，M-TEST退出没有依据。
>
> —— Aaron，2026-08-04（据此裁定：原 S-002 需求追踪不顺延，并入本 release，作为 M-TEST 退出的程序依据）

## 2. 需求描述（原始要求，待 M-STORY 展开）

本 release 交付两个互为前提的部分：**需求追踪（trace/reach）**与 **M-TEST 阶段（Shield-first 测试资产）**。flow.md 已更新为 M-DESIGN → M-TEST → M-IMPL 次序。

### 2.1. Part A：需求追踪（原 S-002，并入本 release）

完整草稿见同目录 `story-S002-draft.md`（用户意图、核心操作路径、行为种子、范围排除均已成型，M-STORY 以此为输入）。要点：

- ID 文法沿用 louke：BS-01 / FR-0010 / NFR-0010 / AC-FRXXXX-YY；ID 不可变不可复用，删除留 tombstone；跨版本限定引用 `AC-FRXXXX-YY@<version>`（文档短/长格式均可，测试 marker 强制长格式）。
- `trac check trace`：BS→FR→AC→test 双向孤儿检测；FR↔AC、AC↔test 为硬错误，BS→FR 仅 warning。
- `trac check reach`：从声明入口点做模块级 import 可达分析，报告孤岛生产模块。
- 存量基线（legacy baseline）豁免清单兼容非全新宿主项目；工具只报告不改写；CLI + 引擎双消费者，人类可读与 `--json` 双格式，退出码语义稳定。

### 2.2. Part B：M-TEST 阶段

M-DESIGN 退出后 `trac run` 进入 M-TEST：Runtime 按 test-plan 的层归属与变绿条件派发 Shield 对着接口桩编写 integration/e2e 测试。出口门禁 = **collection 成功 + 合法 Red + Prism 测试合约审查 + `trac check trace` 闭合**——trace 闭合是退出的程序依据（Aaron 裁定），无 trace 不得退出。本 release 完成时，`trac run` 能从 M-DESIGN 出口跑通 M-TEST 完整循环并停在 M-TEST→M-IMPL 边界，collection / Red 分类 / trace / 审查证据全部落事件。M-IMPL 的实现不在本 release 范围。

关键语义（已获 Human 确认）：

- **合法 Red**：失败必须可归为行为断言失败、桩合同 token 失败（`NotImplementedError("IF-...")`）或合同声明的 symbol 缺失；collection/语法/fixture/import 错误为非法；测试意外通过视为异常。
- **绿的粒度**（约束 M-IMPL 设计，本 release 只需 test-plan 携带变绿条件字段）：task 级 GREEN_GATE = 单测 + 该 task 的 int 子集（变绿条件 = 所依赖接口的 IF- 归属）；Devon 第一轮不跑 e2e；全量 integration+e2e 变绿是 M-IMPL 出口门禁。
- **trace 闭合作为 Shield 的程序化评估指标**：每条 required AC（integration|e2e 层归属）至少一条测试以长格式 marker 绑定；无测试绑定的 AC 与无主 marker 由 `trac check trace` 指出——Shield 写的测试够不够、覆不覆盖需求，有了机器判据。
- **reach 的消费点**：M-IMPL ISLAND_GATE（孤岛闭合）与 M-VERIFY 反 slop 门禁（flow.md 已标注）；本 release 交付工具本体，消费随后续阶段接入。
- 单写者纪律：Shield 不 commit/push、不改产品代码与接口桩；测试缺陷在 M-IMPL 经 DIAGNOSE→SHIELD_FIX 由 Shield 返场修复（本 release 预留事件与路由定义，不实现 M-IMPL 侧）。

## 3. 工作项（规划层，供 M-STORY/M-SPEC 展开）

1. 需求追踪工具：ID 文法与 `@version` 跨版本引用进 templates（story/spec/acceptance/test-plan）；`trac check trace`；`trac check reach`；legacy baseline 豁免机制；双格式输出与稳定退出码。
2. machine.py：M-TEST StageDef（draft/exit decide；validate = collect + legit_red + `trac check trace` 闭合；exit gate = collect + legit_red + discussion_ready + trace）。
3. executor：`_NEXT_STAGE` 增补 M-DESIGN→M-TEST、M-TEST→M-IMPL；M-IMPL 未注册处以 boundary 收尾。
4. legit-red gate：复用 RED_GATE 分类并扩展"桩合同 token 失败合法、意外通过非法"。
5. opencode backend：AGENT_NAME 增补 shield→Shield；Shield prompt/skill（tracks-discuz）物化与回收；写范围审计（仅 tests/**）。
6. templates：test-plan.md 增补变绿条件字段；design-trace validator 扩展（每条 integration/e2e 的 IF- 归属）。
7. agents：tracks/agents/Shield.md 已就位（v0.1）；Prism 测试资产审查派发接线。
8. 测试：FakeAgent 端到端 M-DESIGN exit→M-TEST 循环→boundary；非法 Red / 意外通过 / trace 孤儿（无测试的 AC、无主 marker）负例。

## 4. 范围排除

- 不实现 M-IMPL（Devon/RGR/task graph）；只停在 M-TEST→M-IMPL 边界。
- 不做 `trac check ratio / dup / budget`（后续 story）；不做注册表与语义层去重；不做函数级调用图（本版 reach 只做模块级 import 图）；不做自动修复/自动重编号。
- 不接真实 LLM Agent（延续 v0.1-v0.3 排除，FakeAgent 验证）。
