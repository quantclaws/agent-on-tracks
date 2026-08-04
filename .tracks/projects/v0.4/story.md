---
story_id: S-004
title: 推进流程到 M-TEST 阶段（含需求追踪）
created: 2026-08-04
status: draft
sha:
---

# S-004: 推进流程到 M-TEST 阶段（含需求追踪）

## 1. 原始输入

> # S-004: 推进流程到 M-TEST 阶段（含需求追踪）
>
> ## 1. 原始输入
>
> > 2. 绿的粒度问题 -- 对，不能让 devon 每实现一部分代码，就运行（runtime）一次全部的 end to end，这样会比较花时间。只能运行对应的 int 测试。如果能指定 e2e 子集也可以，不过我觉得 devon 的第一轮实现最好不运行 end to end / 其它没有问题，请修改 flow.md。这是v0.4的内容？不管是不是，我们需要一个新的 release 来将流程推进到 M-TEST 阶段。请着手规划，并完成 shield.md(请借鉴 louke)
> >
> > -- Aaron，2026-08-04
>
> > 这个功能放0.5，是次序错误吧。M-TEST 没有实现的话，需求追踪不可能完整实现吧？
> >
> > 当 M-TEST 实现后，尽管由于 Devon 没有实现，但已经可以做静态的需求追踪。而且正是需求追踪，也让 Shield 的工作多了一个评估指标 -- 它写的测试脚本够不够，有没有覆盖需求
> >
> > -- Aaron，2026-08-04
>
> > 不，需求追踪应该是 M-TEST 的一部分。不做完需求追踪，M-TEST退出没有依据。
> >
> > -- Aaron，2026-08-04（据此裁定：原 S-002 需求追踪不顺延，并入本 release，作为 M-TEST 退出的程序依据）
>
> ## 2. 需求描述（原始要求，待 M-STORY 展开）
>
> 本 release 交付两个互为前提的部分：**需求追踪（trace/reach）**与 **M-TEST 阶段（Shield-first 测试资产）**。flow.md 已更新为 M-DESIGN -> M-TEST -> M-IMPL 次序。
>
> ### 2.1. Part A：需求追踪（原 S-002，并入本 release）
>
> 完整草稿见同目录 `story-S002-draft.md`（用户意图、核心操作路径、行为种子、范围排除均已成型，M-STORY 以此为输入）。要点：
>
> - ID 文法沿用 louke：BS-01 / FR-0010 / NFR-0010 / AC-FRXXXX-YY；ID 不可变不可复用，删除留 tombstone；跨版本限定引用 `AC-FRXXXX-YY@<version>`（文档短/长格式均可，测试 marker 强制长格式）。
> - `trac check trace`：BS->FR->AC->test 双向孤儿检测；FR↔AC、AC↔test 为硬错误，BS->FR 仅 warning。
> - `trac check reach`：从声明入口点做模块级 import 可达分析，报告孤岛生产模块。
> - 存量基线（legacy baseline）豁免清单兼容非全新宿主项目；工具只报告不改写；CLI + 引擎双消费者，人类可读与 `--json` 双格式，退出码语义稳定。
>
> ### 2.2. Part B：M-TEST 阶段
>
> M-DESIGN 退出后 `trac run` 进入 M-TEST：Runtime 按 test-plan 的层归属与变绿条件派发 Shield 对着接口桩编写 integration/e2e 测试。出口门禁 = **collection 成功 + 合法 Red + Prism 测试合约审查 + `trac check trace` 闭合**--trace 闭合是退出的程序依据（Aaron 裁定），无 trace 不得退出。本 release 完成时，`trac run` 能从 M-DESIGN 出口跑通 M-TEST 完整循环并停在 M-TEST->M-IMPL 边界，collection / Red 分类 / trace / 审查证据全部落事件。M-IMPL 的实现不在本 release 范围。
>
> 关键语义（已获 Human 确认）：
>
> - **合法 Red**：失败必须可归为行为断言失败、桩合同 token 失败（`NotImplementedError("IF-...")`）或合同声明的 symbol 缺失；collection/语法/fixture/import 错误为非法；测试意外通过视为异常。
> - **绿的粒度**（约束 M-IMPL 设计，本 release 只需 test-plan 携带变绿条件字段）：task 级 GREEN_GATE = 单测 + 该 task 的 int 子集（变绿条件 = 所依赖接口的 IF- 归属）；Devon 第一轮不跑 e2e；全量 integration+e2e 变绿是 M-IMPL 出口门禁。
> - **trace 闭合作为 Shield 的程序化评估指标**：每条 required AC（integration|e2e 层归属）至少一条测试以长格式 marker 绑定；无测试绑定的 AC 与无主 marker 由 `trac check trace` 指出--Shield 写的测试够不够、覆不覆盖需求，有了机器判据。
> - **reach 的消费点**：M-IMPL ISLAND_GATE（孤岛闭合）与 M-VERIFY 反 slop 门禁（flow.md 已标注）；本 release 交付工具本体，消费随后续阶段接入。
> - 单写者纪律：Shield 不 commit/push、不改产品代码与接口桩；测试缺陷在 M-IMPL 经 DIAGNOSE->SHIELD_FIX 由 Shield 返场修复（本 release 预留事件与路由定义，不实现 M-IMPL 侧）。
>
> ## 3. 工作项（规划层，供 M-STORY/M-SPEC 展开）
>
> 1. 需求追踪工具：ID 文法与 `@version` 跨版本引用进 templates（story/spec/acceptance/test-plan）；`trac check trace`；`trac check reach`；legacy baseline 豁免机制；双格式输出与稳定退出码。
> 2. machine.py：M-TEST StageDef（draft/exit decide；validate = collect + legit_red + `trac check trace` 闭合；exit gate = collect + legit_red + discussion_ready + trace）。
> 3. executor：`_NEXT_STAGE` 增补 M-DESIGN->M-TEST、M-TEST->M-IMPL；M-IMPL 未注册处以 boundary 收尾。
> 4. legit-red gate：复用 RED_GATE 分类并扩展"桩合同 token 失败合法、意外通过非法"。
> 5. opencode backend：AGENT_NAME 增补 shield->Shield；Shield prompt/skill（tracks-discuz）物化与回收；写范围审计（仅 tests/**）。
> 6. templates：test-plan.md 增补变绿条件字段；design-trace validator 扩展（每条 integration/e2e 的 IF- 归属）。
> 7. agents：tracks/agents/Shield.md 已就位（v0.1）；Prism 测试资产审查派发接线。
> 8. 测试：FakeAgent 端到端 M-DESIGN exit->M-TEST 循环->boundary；非法 Red / 意外通过 / trace 孤儿（无测试的 AC、无主 marker）负例。
>
> ## 4. 范围排除
>
> - 不实现 M-IMPL（Devon/RGR/task graph）；只停在 M-TEST->M-IMPL 边界。
> - 不做 `trac check ratio / dup / budget`（后续 story）；不做注册表与语义层去重；不做函数级调用图（本版 reach 只做模块级 import 图）；不做自动修复/自动重编号。
> - 不接真实 LLM Agent（延续 v0.1-v0.3 排除，FakeAgent 验证）。

> **Sage:** D-29（decisions.md L304-312）裁定 v0.4 必须交付「测试资产判据包（M-TEST 的 PRISM_REVIEW 子状态消费）+ 配套反自述三件套」：① assignment 写明应加载判据包的名称+版本（Runtime 决定，Prism 不自选）；② verdict outcome 携带实际加载判据包的 identity；③ Runtime 回读核对，不匹配判失败重派。S-004 工作项 7 仅写「Prism 测试资产审查派发接线」，BS-05 只要求 prism.verdict(pass)，均未覆盖判据包 skill 本体与回读机制；判据包也未列入 §5 Out-of-Scope。这是 D-29 已裁定的 v0.4 强制交付物，缺它则 M-TEST 退出门禁（BS-05）的 Prism 评审无程序化判据来源、也不满足 D-29 的反自述约束。推荐：补一条工作项（测试资产判据包 skill 物化 + 反自述三件套接线）与对应行为种子（Prism verdict 须绑定实际加载判据包 identity，Runtime 回读不匹配判失败重派）。若有意顺延，请在 §6 开放产品决定登记并说明与 D-29 v0.4 强制条款的关系。
>> **Scribe:** 已采纳，D-29 是 v0.4 强制交付物，不作为开放产品决定顺延。已将判据包与反自述三件套纳入 story 正文（非 §1 原始输入块内的工作项清单——那是 Aaron 的规划框架，逐字保留）：① §3.1 step 6 PRISM_REVIEW 增补「按 assignment 指定的测试资产判据包（D-29）审测试合约；Prism verdict 携带实际加载判据包 identity，Runtime 回读核对不匹配判失败重派」；② 新增 BS-14「Prism 判据包绑定与反自述回读」（EARS 覆盖 assignment 指定名称+版本 / verdict 携带 identity / Runtime 回读三件套）；③ §5 必须保持的产品约束增补 D-29 反自述三件套条款。BS-05（M-TEST 无 Human 门禁）保持聚焦「退出依据全是程序证据」，判据包绑定由 BS-14 专责。判据包 skill 本体物化与 Runtime 回读机制属 spec/design 层展开，story 已锁定行为边界。

## 2. 用户意图

- 将 tracks 流程从 M-DESIGN 出口推进到 M-TEST 阶段：trac run 能驱动 M-TEST 完整循环（Shield 对着接口桩写 integration/e2e 测试 -> collection -> Prism 评审 -> 合法 Red 校验 -> trace 闭合 -> 边界退出），停在 M-TEST->M-IMPL 边界
- 交付需求追踪工具（trac check trace / trac check reach），使"每条需求可追到测试函数、每个生产模块可达自入口点"成为机器可验证的事实，并作为 M-TEST 退出的程序依据（Aaron 裁定：不做完需求追踪，M-TEST 退出没有依据）
- 当前受阻：流程在 M-DESIGN 出口终止（run.completed(boundary)），M-TEST 阶段未实现；需求追踪工具不存在，AC 与测试的绑定关系无机器判据，Shield 的测试覆盖充分性无程序化评估指标
- 完成后：trac run 从 M-DESIGN 出口进入 M-TEST 并跑通完整循环；trac check trace 和 trac check reach 可独立 CLI 运行也可被引擎当 verdict 来源调用；绿的粒度（task 级只跑对应 int 子集）通过 test-plan 变绿条件字段约束 M-IMPL 设计

## 3. 核心操作路径

### 3.1. M-TEST 阶段生命周期

- **变更基线**：新增 - executor _NEXT_STAGE 在 M-DESIGN 后无后继（run.completed(boundary)）；machine.py 无 M-TEST StageDef 与子状态机（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT/DIAGNOSE，flow.md §9.1 已定义但未实现）；无 collection/legit-Red 校验逻辑
- **入口/触发**：M-DESIGN EXIT（prism.verdict(pass) + 程序校验通过）触发 stage.entered(M-TEST)

1. machine.py 注册 M-TEST StageDef（子状态机按 flow.md §9.1：DISPATCH -> WRITE -> COLLECT -> PRISM_REVIEW -> RED_CHECK -> EXIT / DIAGNOSE）
2. executor _NEXT_STAGE 增补 M-DESIGN->M-TEST、M-TEST->M-IMPL；M-IMPL 未注册处以 boundary 收尾（承自 v0.3 Decision A）
3. DISPATCH：Runtime 按 test-plan 的层归属与变绿条件创建 Shield tasks
4. WRITE：dispatch Shield 写 integration/e2e 测试；validate 失败重派 Shield（<=3）
5. COLLECT：Runtime 独立执行 collection；失败回 WRITE
6. PRISM_REVIEW：dispatch Prism 按 assignment 指定的测试资产判据包（D-29）审测试合约（忠于 AC + 断言落在公开出口 + counterexample 绑定）；Prism verdict 携带实际加载判据包 identity，Runtime 回读核对不匹配判失败重派；revise 回 WRITE
7. RED_CHECK：Runtime 独立复跑 integration/e2e；失败须全部为合法 Red（行为断言失败 / 桩合同 token 失败 NotImplementedError('IF-...') / symbol 缺失）；collection/语法/fixture/import 错误为非法；测试意外通过视为异常 -> DIAGNOSE
8. DIAGNOSE：四路诊断（测试缺陷->Shield 重派 / 桩或接口缺口->M-DESIGN / AC/Spec 缺口->M-ACC/M-SPEC）
9. EXIT：'trac check trace' 闭合 + 冻结测试资产 -> stage.exited -> run.completed(terminal_state="boundary")

- **完成结果**：trac run 从 M-DESIGN 出口跑通 M-TEST 完整循环，停在 M-TEST->M-IMPL 边界；collection / Red 分类 / trace / 审查证据全部落事件

### 3.2. 需求追踪工具

- **变更基线**：新增 - trac check 仅支持 deliverables 子命令；无 ID 文法约束（BS/FR/NFR/AC 编号无统一文法）；无 trace/reach 分析；无存量基线豁免机制。既有 validate.py 的 check_trace（FR-0170 AC<->FR）与 check_design_trace（BS-06 AC->layer）是 validate-time 局部检查，非 CLI 工具、不含 test marker 绑定与 BS->FR 链
- **入口/触发**：开发者 CLI 运行 'trac check trace' / 'trac check reach'；引擎在 M-TEST EXIT 调用 trace 作为 verdict 来源

1. ID 文法（BS-XX / FR-XXXX / NFR-XXXX / AC-FRXXXX-YY / 跨版本限定引用 AC-FRXXXX-YY@<version>）进 story/spec/acceptance templates；ID 不可变不可复用，删除留 tombstone
2. 'trac check trace'：解析三文档 + 测试 marker，BS->FR->AC->test 全链双向孤儿检测；FR<->AC 与 AC<->test 为硬错误，BS->FR 仅 warning（行为种子与 FR 不总是 1:1）
3. 'trac check reach'：从声明入口点（pyproject [project.scripts] / __main__ / 显式白名单）构建模块级 import 图，报告从任何入口都不可达的生产模块（孤岛）
4. 存量基线豁免：采纳 tracks 时声明豁免清单，基线内容不计孤儿；基线只冻结采纳时刻的存量、不回填历史；基线外新增内容正常报错
5. 双格式输出（人类可读 + --json），退出码 0=通过 / 非0=有硬错误；工具只报告不改写

- **完成结果**：两个工具可独立 CLI 运行也可被引擎当 verdict 来源调用；同一输入多次运行输出完全一致

### 3.3. Shield Agent 接入

- **变更基线**：修改 - tracks/agents/Shield.md 已就位（v0.1，完整 prompt，仅声明 tracks-discuz skill）；opencode.py AGENT_NAME 有 scribe/sage/lex/archer/prism，无 shield 条目；无 Shield dispatch 接线；无写范围审计（tests/** 限定）
- **入口/触发**：Runtime 在 M-TEST WRITE 子状态 dispatch Shield（与 Archer/Prism 同构的 opencode agent）

1. AGENT_NAME 增补 shield -> Shield
2. Shield prompt/skill（tracks-discuz）物化与回收（与 Archer/Prism 同构）
3. 写范围审计：Shield 仅可写 tests/integration/、tests/e2e/、tests/assets/、tests/counterexamples/；越权写被审计检出并通过 git 回滚
4. Shield 读 test-plan/interfaces/acceptance + 接口桩 -> 写 integration（happy + 关键错误路径）+ e2e（仅 happy path）-> 本地自检（collection 通过 + 合法 Red + counterexample killed）-> 返回 outcome

- **完成结果**：Shield 作为真实 opencode agent 被 dispatch；测试资产写入宿主项目 tests/ 目录；写范围越权被审计回滚
- **待澄清**：Aaron 提示 Shield 还有多项 review 技能需在 prompt 中提示使用。经查 decisions.md，Shield.md 当前仅声明 tracks-discuz skill；D-29 判据包（criteria packs）归属于 Prism，在 PRISM_REVIEW 子状态消费。若需为 Shield 增补技能，请 Human 澄清具体范围

> **Scribe [RESOLVED]:** Aaron 提示 Shield 有多项 review 技能需在 prompt 中提示使用。经查 decisions.md 与 Shield.md：Shield.md 当前仅声明 tracks-discuz skill（用于 inline-discussion）；D-29 判据包（criteria packs）归属于 Prism，在 PRISM_REVIEW 子状态消费，不归 Shield。Shield 的退出前自审清单（collection 通过 / 合法 Red / 断言落公开出口 / counterexample killed / 无伪测试）是自检项，非独立 review 技能。我推荐维持现状--Shield 仅 tracks-discuz，review 职责归 Prism（D-29 已裁定）。若 Aaron 指的是其他技能，请澄清具体范围与来源 decision。

### 3.4. 模板与 ID 文法更新

- **变更基线**：修改 - 部分已有：spec 模板已含 FR-XXXX/NFR-XXXX 文法并由 validate.check_spec_items 强制（严格标题 ### FR-XXXX、唯一 ID、来源/交付入口字段），acceptance 模板已含 AC-FRXXXX-YY 文法指引（validate 仅校验 acceptance frontmatter，AC 格式靠模板指引）。本次补的缺口：story 模板无 BS-XX 强制文法（仅有编号约定、无机器校验与不可变/tombstone 规则），三模板均无 @version 跨版本引用与 tombstone 规则，测试 marker 长格式未强制，test-plan.md template 无变绿条件字段，design-trace validator（check_design_trace）只校验 AC->layer 归属不校验 IF- 标识，trace/reach 工具不存在
- **入口/触发**：Archer 起草设计文档 / Shield 起草测试时读取模板；validate 按模板校验

> **Sage:** 基线称「story/spec/acceptance templates 无 ID 文法约束（编号无统一格式要求）」，但 spec.md 模板已含 FR-XXXX/NFR-XXXX 文法指引（tracks/templates/spec.md L13 注释 + validate.check_spec_items 强制：严格标题 ### FR-XXXX、唯一 ID、来源/交付入口字段），acceptance.md 模板已含 AC-FRXXXX-YY 文法（tracks/templates/acceptance.md L13）。真实缺口是：story 模板无 BS-XX 强制文法、三模板均无 @version 跨版本引用与 tombstone 规则、test marker 长格式未强制、trace/reach 工具不存在。基线应改为「部分已有（FR-XXXX/AC-FRXXXX-YY 已在 spec/acceptance 模板与 validate 中强制），本次补 BS 文法、@version 跨版本引用、tombstone、marker 长格式与 trace/reach 工具」，避免下游误以为要从零设计已有文法、重复实现 check_spec_items 已覆盖的校验。
>> **Scribe:** 已采纳，基线修正为「部分已有」。核实项目事实：spec 模板 L13 已含 FR-XXXX/NFR-XXXX 文法（四位补零、唯一 ID、来源/交付入口字段），validate.check_spec_items 强制（严格标题 ### FR-XXXX、重复 ID、缺来源/交付入口字段判失败）；acceptance 模板 L13 已含 AC-FRXXXX-YY 文法指引（validate 仅校验 acceptance frontmatter，AC 格式靠模板指引）。§3.4 变更基线已改为「部分已有：spec 已含并由 check_spec_items 强制，acceptance 已含文法指引；本次补的缺口：story 无 BS-XX 强制文法（仅编号约定、无机器校验与不可变/tombstone 规则）、三模板均无 @version 跨版本引用与 tombstone、marker 长格式未强制、test-plan 无变绿条件字段、design-trace validator 不校验 IF-、trace/reach 工具不存在」，避免下游误以为从零设计已有文法。

1. story/spec/acceptance templates 增补 ID 文法（BS-XX / FR-XXXX / NFR-XXXX / AC-FRXXXX-YY）与跨版本引用规则（AC-FRXXXX-YY@<version>）；文档中短格式与长格式（带版本号）均允许，短格式 opt-in 消歧
2. test-plan.md template 增补变绿条件字段：每条 integration/e2e 归属的 AC 声明所依赖接口的 IF- 标识，供 M-IMPL task 变绿子集划分
3. design-trace validator 扩展：校验每条 integration/e2e 的 IF- 归属
4. 测试 marker 规则：测试文件中 marker 强制长格式 AC-FRXXXX-YY@<version>（代码不按版本分目录，缺版本号无法定位 AC 所属版本）；短格式 marker 触发 trace 检查失败

- **完成结果**：模板携带 ID 文法与变绿条件字段；validate 可校验编号文法、跨版本引用与 IF- 归属
- **下游工作项（spec/design 阶段，按 D-26 不在 story 范围）**：Archer.md 提示词需同步更新--1) test-plan 必须声明变绿条件（IF- 标识，对应 BS-12）；2) 强调接口桩是 M-TEST 的 ATDD 基础设施；3) 提及 trace 闭合作为 M-TEST 退出门禁；4) Archer.md 中'M-IMPL 的输入合同'更新为'M-TEST + M-IMPL 的输入合同'

## 4. 行为种子

### 4.1. BS-01 M-TEST 进入条件

- EARS: `WHEN M-DESIGN EXIT 完成（prism.verdict(pass) + 程序校验通过）, THE 系统 SHALL 发出 stage.entered(M-TEST) 并按 test-plan 层归属与变绿条件创建 Shield tasks`
- 来源: [3.1 / flow.md §9]
- 说明: 保护"M-TEST 只在设计基线批准后开始"的流程不变量

### 4.2. BS-02 Shield 产出可 collect 的 Red 测试资产

- EARS: `WHEN Shield 在 M-TEST WRITE 完成 outcome, THE 系统 SHALL 在宿主项目 tests/integration/ 与 tests/e2e/ 下存在可 collect 且全部合法失败的测试文件`
- 来源: [3.3 / flow.md §9.1 / Shield.md]
- 说明: 保护"M-TEST 交付的是可 collect 的 Red 测试资产，不是绿色结果"的合同

### 4.3. BS-03 合法 Red 分类边界

- EARS: `WHEN Runtime 在 RED_CHECK 独立复跑 integration/e2e 测试, THE 系统 SHALL 将每条失败分类为合法 Red（行为断言失败 / 桩合同 token 失败 NotImplementedError('IF-...') / 合同声明的 symbol 缺失）或非法 Red（collection/语法/fixture/import 错误），测试意外通过视为非法`
- 来源: [3.1 / 约束 / flow.md §9.3]
- 说明: 保护"退出依据是 Runtime 复跑的程序证据而非 Shield 自述"的可验证性

### 4.4. BS-04 trace 闭合作为 M-TEST 退出门禁

- EARS: `WHEN M-TEST EXIT 校验, THE 系统 SHALL 要求 'trac check trace' 闭合（每条 required AC 至少一条长格式 marker AC-FRXXXX-YY@<version> 绑定、无无主 marker），trace 不闭合不得退出`
- 来源: [3.1 / 约束 / Aaron 裁定]
- 说明: 保护"需求追踪是 M-TEST 退出的程序依据"（Aaron：不做完需求追踪，M-TEST 退出没有依据）

### 4.5. BS-05 M-TEST 无 Human 门禁

- EARS: `WHERE M-TEST 阶段, THE 系统 SHALL 仅要求 collection 成功 + 合法 Red + Prism verdict(pass) + trace 闭合即可退出，不等待 human.review 或 human.approval`
- 来源: [3.1 / flow.md §9.3 / 重要推导（承自 v0.3 BS-05）]
- 说明: 保护"退出依据全部是程序证据"的流程设计

### 4.6. BS-06 M-TEST 边界退出

- EARS: `WHEN M-TEST EXIT 完成, THE 系统 SHALL 发出 run.completed(terminal_state="boundary") 停在 M-TEST->M-IMPL 边界，不进入 M-IMPL`
- 来源: [3.1 / 重要推导（承自 v0.3 Decision A）]
- 说明: 保护"M-IMPL 不在本 release 范围"的边界

### 4.7. BS-07 trace 双向孤儿检测

- EARS: `WHEN 开发者运行 'trac check trace', THE 系统 SHALL 对 BS->FR->AC->test 全链做双向孤儿检测：FR<->AC 与 AC<->test 为硬错误（非零退出），BS->FR 仅 warning（不改变退出码）`
- 来源: [3.2 / story-S002-draft]
- 说明: 保护"每条需求可追到测试函数"的可追踪性；BS->FR 弱链接避免硬约束逼人写凑数 FR

### 4.8. BS-08 reach 模块级孤岛检测

- EARS: `WHEN 开发者运行 'trac check reach', THE 系统 SHALL 从声明入口点做模块级 import 可达分析，报告从任何入口都不可达的生产模块（孤岛）；纯测试模块不计入；无任何入口声明时报错而非静默通过`
- 来源: [3.2 / story-S002-draft]
- 说明: 保护"每个生产模块可达自入口点"（louke 尸检 79/117 幽灵模块的直接对策）

### 4.9. BS-09 存量基线豁免

- EARS: `WHERE 宿主项目携带存量文档/编号/模块, THE 系统 SHALL 对列入存量基线的内容豁免 trace/reach 检查；基线只冻结采纳时刻的存量、不回填历史；基线之后新增的同形内容仍正常报错`
- 来源: [3.2 / 约束 / Human 裁定]
- 说明: 保护"兼容非全新宿主项目"的采纳可行性（tracks 自身 v0.1 即为存量样本）

### 4.10. BS-10 Shield 写范围审计

- EARS: `WHEN Shield 完成 outcome, THE 系统 SHALL 审计 Shield 的写范围仅限于 tests/**（integration/e2e/assets/counterexamples），越权写文件被检出并通过 git 回滚`
- 来源: [3.3 / 约束 / Shield.md]
- 说明: 保护"Shield 不改产品代码与接口桩"的单写者纪律

### 4.11. BS-11 ID 文法与跨版本引用

- EARS: `WHEN 开发者在 story/spec/acceptance 中声明编号, THE 系统 SHALL 按 louke 文法校验（BS-XX / FR-XXXX / NFR-XXXX / AC-FRXXXX-YY）；测试 marker 必须使用长格式 AC-FRXXXX-YY@<version>，缺版本号的短格式 marker 触发 trace 检查失败`
- 来源: [3.4 / story-S002-draft / 约束]
- 说明: 保护"编号唯一、不可变、跨版本可定位"的追踪链完整性（代码不按版本分目录，缺版本号无法定位 AC 所属版本）

### 4.12. BS-12 变绿条件字段

- EARS: `WHEN Archer 产出 test-plan.md, THE 系统 SHALL 要求每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分`
- 来源: [3.4 / 约束 / flow.md §10 GREEN_GATE / Aaron 绿粒度裁定]
- 说明: 保护"绿的粒度：task 级 GREEN_GATE 只跑该 task 单测 + 对应 int 子集，Devon 第一轮不跑 e2e"的实现基础

### 4.13. BS-13 DIAGNOSE 四路诊断

- EARS: `WHEN M-TEST RED_CHECK 遇到非法 Red 或意外通过, THE 系统 SHALL 进入 DIAGNOSE 并路由：测试缺陷->Shield 重派 / 桩或接口缺口->M-DESIGN / AC/Spec 缺口->M-ACC/M-SPEC`
- 来源: [3.1 / flow.md §9.1]
- 说明: 保护"测试错还是接口错的分流不交给 Human"的自动化诊断（flow.md：需语义判断时分派 Prism diagnostic review）

### 4.14. BS-14 Prism 判据包绑定与反自述回读

- EARS: `WHEN Runtime 派发 Prism 在 M-TEST PRISM_REVIEW 评审, THE 系统 SHALL 在 assignment 写明应加载的测试资产判据包名称+版本（Runtime 决定，Prism 不自选），Prism verdict outcome 携带实际加载判据包 identity，Runtime 回读核对--不匹配判失败重派`
- 来源: [3.1 / D-29 / 约束]
- 说明: 保护"Prism 评审的程序化判据来源可验证、不可自述"（D-29 反自述三件套）；缺此则 M-TEST 退出门禁（BS-05）的 Prism 评审无机器判据来源

## 5. 范围、约束与例外

- **必须保持的产品约束**：kernel 纯函数边界（decide/project 不碰 IO）；事件溯源 append-only；Agent 不 commit/push/不推进状态（Runtime 是唯一流程 authority）；单写者纪律（Shield 不改产品代码与接口桩）；工具只报告不改写；'trac check trace' 统一检查所有 AC（不感知调用方阶段），M-TEST 退出门禁自行过滤只看 required AC（integration|e2e 层 AC）--依据 §1 需求描述 Part B 与 flow.md §9.3；D-29 反自述三件套（Prism 评审的判据包由 assignment 指定名称+版本、verdict 携带实际加载判据包 identity、Runtime 回读核对不匹配判失败重派）
- **非常规要求**：M-TEST 接受并要求 Red（每条测试必须失败且失败必须合法）--与常规"测试应通过"相反，因为本阶段交付的是测试资产而非绿色结果；测试 marker 强制长格式 AC-FRXXXX-YY@<version> 而文档中短格式可--不对称约束（代码不按版本分目录，所有版本测试共存于同一棵 tests/ 树）
- **Out-of-Scope**：不实现 M-IMPL（Devon/RGR/task graph），只停在 M-TEST->M-IMPL 边界；不做 trac check ratio / dup / budget（后续 story）；不做注册表与语义层去重（Agent 评审职责，非本版工具）；不做函数级调用图（本版 reach 只做模块级 import 图）；不做自动修复/自动重编号；不接真实 LLM Agent（延续 v0.1-v0.3 排除，FakeAgent 验证）；M-IMPL 侧的 SHIELD_FIX/DIAGNOSE 路由仅预留事件与路由定义，不实现

## 6. 开放产品决定

无。'trac check trace' 检查范围（统一检查所有 AC，M-TEST 退出门禁自行过滤 required AC）已由 §1 需求描述 Part B 与 flow.md §9.3 确定，纳入 §5 产品约束，无开放产品决定。

## 7. 必要性与风险

- **既有能力**：v0.3 的 StageDef 表驱动注册（BS-01）使 M-TEST 接入成本为一条 StageDef + 对应 reducer；Shield.md 已就位（v0.1）；validate.py 的 check_trace / check_design_trace 可作为 'trac check trace' 的局部基础；inline-discussion 协议（tracks-discuz）可直接复用；flow.md §9 M-TEST 子状态机已定义
- **冲突**：无
- **重要风险**：M-TEST 子状态机（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT/DIAGNOSE）与既有 StageDef 的 DRAFT/REVIEW/EXIT 模式差异较大--machine.py 的 decide() 需为 M-TEST 增加显式控制流分支（类似 M-REQ-APPROVAL 的 _decide_approval），不能仅靠 StageDef 注册驱动。缓解：flow.md §9.1 已完整定义子状态机与事件清单，spec 阶段按此展开；FakeAgent 端到端测试覆盖完整循环与负例
