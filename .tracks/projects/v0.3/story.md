---
story_id: S-001
title: {一句话标题}
created: 2026-08-03
status: draft
sha:
---

# S-001: {一句话标题}

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留内容）：
  - §1 逐字记录 Human 原始输入，不修改、不转述。
  - §3 每条操作路径复制 3.1 的小节结构（3.2、3.3……）。修改类路径的「变更基线」必须写清
    当前行为与本次变更——下游（Sage/Archer/Shield/Devon）基于此描述变更，而非从零设计；
    新增路径写“无（新增路径）”。
  - §4 只提取需要下游继续展开的用户结果和重要边界，不枚举普通微交互；按路径顺序统一编号
    BS-01、BS-02……（不按路径分组）。
  - §5 各项没有时写“无”；Out-of-Scope 只记录明确排除或为防止明显范围扩张而必须记录的事项。
  - §6 每个问题一个段落，粗体一句话开头（格式见正文占位）；不编号、机器不校验。只写无法
    可靠推导、且不同答案会显著改变价值/范围/权限/数据安全/合规或产生不可逆后果的产品问题；
    技术选择不写。没有则整节写“无”。
  - §7 重要风险只写会改变范围或使 story 不成立的。
-->

## 1. 原始输入

> ---
> story_id: S-003
> title: M-DESIGN 阶段——Archer 设计、Prism 评审、状态机泛化
> created: 2026-07-31
> status: draft
> sha:
> ---
>
> # S-003: M-DESIGN 阶段——Archer 设计、Prism 评审、状态机泛化
>
> ## 1. 原始输入
>
> > v0.2 完成了需求管线（M-STORY → M-SPEC → M-ACC → M-REQ-APPROVAL）。按 flow.md，下一个阶段是 M-DESIGN：Archer 产出 architecture / interfaces / test-plan 三文档，Prism 独立评审。这是纯技术阶段，Human 可选、允许缺席、不设门禁。v0.3 实现这个阶段，同时把状态机从 M-STORY/M-SPEC 硬编码改为表驱动，使后续阶段（M-IMPL 等）的接入成本从 O(n) 降到 O(1)。
>
> ## 2. 用户意图
>
> - tracks 能驱动完整的设计阶段：从已批准的需求 baseline 到三文档评审通过
> - 状态机不再为每个新阶段写 if/else——新增阶段只需声明式注册
> - Archer 和 Prism 成为真实 Agent（opencode 后端），与 Scribe/Sage 同构
> - 完成后 `trac run` 能从 M-REQ-APPROVAL 走到 M-DESIGN 完成
>
> ## 3. 核心操作路径
>
> ### 3.1. 状态机泛化
>
> - **变更基线**：修改 — machine.py 的 decide()/project() 当前硬编码 M-STORY/M-SPEC 的 role、doc、substate 映射、committed 标志和 verdict 处理。每新增一个阶段需改 6-8 处 if/else
> - **入口/触发**：开发者在 machine.py 中注册新阶段定义
>
> 1. 将阶段行为（role、doc、初始 substate、reviewer、committed 事件名）抽取为声明式 StageDef 数据
> 2. decide() 和 project() 的 reducer 改为查表驱动，不再按阶段名分支
> 3. M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL / M-DESIGN 五个阶段均通过 StageDef 注册
> 4. 既有 E2E 测试（fake 后端）全部通过，行为不变
>
> - **完成结果**：新增阶段只需追加一条 StageDef + 对应 Agent 提示词，不改 decide()/project() 逻辑
>
> ### 3.2. M-DESIGN 阶段生命周期
>
> - **变更基线**：新增 — 状态机当前在 M-REQ-APPROVAL 后 run.completed；M-DESIGN 不存在
> - **入口/触发**：M-REQ-APPROVAL 的 human.approval 事件触发 stage.entered(M-DESIGN)
>
> 1. Runtime 进入 M-DESIGN，dispatch Archer 起草三文档
> 2. Archer outcome → validate（结构 + AC→test-plan 覆盖）→ commit
> 3. dispatch Prism 评审；Prism 用 trac discuss 在文档内提问
> 4. prism.verdict(pass) → EXIT（格式终验）→ stage.exited → run.completed
> 5. prism.verdict(revise) → RESPOND → Archer 修订 → 新一轮 Prism 评审
>
> - **完成结果**：三文档（architecture.md / interfaces.md / test-plan.md）已提交、sha 已封印、所有讨论线程 resolved；run 到达 completed 终态（M-IMPL 不在本版）
>
> ### 3.3. Archer Agent
>
> - **变更基线**：新增 — tracks/agents/ 下只有 Scribe.md 和 Sage.md
> - **入口/触发**：Runtime 在 M-DESIGN DRAFT/RESPOND 子状态 dispatch Archer
>
> 1. Archer 读取已批准的三件套（story.md / spec.md / acceptance.md）
> 2. 按模板产出 architecture.md（模块边界、技术选型、延续性声明）
> 3. 按模板产出 interfaces.md（跨模块合同、类型化 schema、CLI 接口）
> 4. 按模板产出 test-plan.md（测试策略、AC→layer 映射、反模式清单）
> 5. RESPOND 时读取 Prism 的 inline-discussion 并修订
>
> - **完成结果**：三文档存在于 .tracks/projects/{version}/，格式通过 validate
>
> ### 3.4. Prism Agent
>
> - **变更基线**：新增 — 无 Prism 提示词
> - **入口/触发**：Runtime 在 M-DESIGN PRISM_REVIEW 子状态 dispatch Prism
>
> 1. Prism 读取三文档 + 三件套，检查一致性
> 2. 用 trac discuss 在文档内锚定提问（复用 v0.2 inline-discussion 协议）
> 3. 收敛后给出 verdict(pass / revise)
>
> - **完成结果**：prism.verdict 事件落盘；pass 时所有讨论线程 resolved
>
> ### 3.5. 设计文档模板
>
> - **变更基线**：修改 — tracks/templates/ 已有 test-plan.md，缺 architecture.md 和 interfaces.md
> - **入口/触发**：Archer 起草时读取模板；validate 按模板校验
>
> 1. 从 v0.2 自身的 architecture.md / interfaces.md 提炼通用模板
> 2. 模板遵循新格式（HTML 注释指引 + 条件章节骨架）
> 3. validate 扩展：支持 architecture / interfaces / test-plan 三种 kind 的结构校验
>
> - **完成结果**：tracks/templates/{architecture,interfaces}.md 存在；trac validate 可校验三种设计文档
>
> ## 4. 行为种子
>
> ### 4.1. BS-01 表驱动阶段注册
>
> - EARS: `WHEN 开发者追加一条 StageDef 并注册, THE 系统 SHALL 使该阶段的 DRAFT→REVIEW→EXIT 生命周期可被 decide()/project() 驱动，无需修改既有 reducer/decide 代码`
> - 来源: [3.1 / 重要推导]
> - 说明: 保护"新阶段接入成本 O(1)"的架构结果
>
> ### 4.2. BS-02 M-DESIGN 进入条件
>
> - EARS: `WHEN M-REQ-APPROVAL 的 approval 事件落盘且三件套 digest 未 stale, THE 系统 SHALL 发出 stage.entered(M-DESIGN) 并 dispatch Archer`
> - 来源: [3.2 / flow.md §8]
> - 说明: 保护"设计只在需求批准后开始"的流程不变量
>
> ### 4.3. BS-03 Archer 产出三文档
>
> - EARS: `WHEN Archer 在 M-DESIGN DRAFT 完成 outcome, THE 系统 SHALL 在 .tracks/projects/{version}/ 下存在 architecture.md、interfaces.md、test-plan.md 且均通过 validate`
> - 来源: [3.3 / flow.md §8]
> - 说明: 保护"设计阶段的交付物是三文档"的合同
>
> ### 4.4. BS-04 Prism 评审与收敛
>
> - EARS: `WHEN Prism verdict(revise), THE 系统 SHALL 进入 RESPOND 并 re-dispatch Archer；WHEN Prism verdict(pass) 且所有讨论线程 resolved, THE 系统 SHALL 进入 EXIT`
> - 来源: [3.4 / flow.md §8.1]
> - 说明: 保护"设计经独立评审收敛"的质量结果
>
> ### 4.5. BS-05 M-DESIGN 无 Human 门禁
>
> - EARS: `WHERE M-DESIGN 阶段, THE 系统 SHALL 仅要求 validate pass + prism.verdict(pass) 即可退出，不等待 human.review 或 human.approval`
> - 来源: [3.2 / flow.md §8.3 硬规则 1]
> - 说明: 保护"纯技术阶段 Human 不阻塞"的流程设计
>
> ### 4.6. BS-06 AC→test-plan 覆盖校验
>
> - EARS: `WHEN validate 检查 test-plan.md, THE 系统 SHALL 验证每条 acceptance.md 中的 AC 在 test-plan 中有 layer 归属（unit / integration / e2e），孤立项报 verdict.failed(trace)`
> - 来源: [3.5 / flow.md §8 validate 说明]
> - 说明: 保护"每条验收标准有测试策略覆盖"的可追溯性
>
> ### 4.7. BS-07 既有阶段行为不变
>
> - EARS: `WHEN v0.3 部署后运行 M-STORY / M-SPEC 的 E2E 测试, THE 系统 SHALL 产生与 v0.2 完全相同的事件序列和终态`
> - 来源: [3.1 / 约束]
> - 说明: 保护"泛化是重构不是改行为"的回归安全
>
> ## 5. 范围、约束与例外
>
> - **必须保持的产品约束**：kernel 纯函数边界（decide/project 不碰 IO）；事件溯源 append-only；Agent 不 commit/不推进状态
> - **非常规要求**：无
> - **Out-of-Scope**：M-IMPL 及之后（Devon / RGR / task graph）；Archer 选技术栈 / test.marker 适配（v0.4）；真实 Lex；GitHub Issue 映射；web 界面
>
> ## 6. 开放产品决定
>
> **M-DESIGN 终态后 run 是否 completed** — v0.3 不实现 M-IMPL，M-DESIGN EXIT 后 run 到达 completed 终态。不同答案：A) completed（本版做法，M-IMPL 留给 v0.5+）B) awaiting_human（提示用户 M-IMPL 未实现）。推荐 A，因为 awaiting_human 暗示用户可以做某事来推进，但实际上做不到。
>
> **Archer 一次 outcome 产出三文档还是三次 dispatch** — flow.md 说"dispatch Archer"（单数），但三文档职责差异大。不同答案：A) 一次 dispatch 产出三文档（简单，与 flow.md 字面一致）B) 三次 dispatch 各产一份（粒度细，validate 可逐文档重派）。推荐 A，因为三文档高度耦合（interfaces 引用 architecture 的模块边界，test-plan 引用 interfaces 的合同），拆开会导致不一致。
>
> ## 7. 必要性与风险
>
> - **既有能力**：v0.2 的 Agent seam（OpencodeBackend）、inline-discussion、validate 框架可直接复用
> - **冲突**：无
> - **重要风险**：状态机泛化是破坏性重构——如果 StageDef 抽象不对，会同时破坏 M-STORY/M-SPEC 的既有行为。缓解：泛化后先跑全部 155 个既有测试确认回归，再接入 M-DESIGN

## 2. 用户意图

- {用户想完成什么}
- {当前哪里受阻}
- {完成后能看到什么结果}

## 3. 核心操作路径

### 3.1. {路径名}

- **变更基线**：[新增 / 替换 / 修改 / 重命名] — {此路径在当前版本的行为与实现；新增路径写“无（新增路径）”}
- **入口/触发**：{用户从现有哪个任务、对象或公开入口，如何进入本能力}

1. {改变用户任务状态的关键步骤 1}
2. {关键步骤 ...}
3. {用户在何处看到可验证的完成结果}

- **完成结果**：{可观察的完成状态；完成、取消或可恢复失败后，用户接下来能做什么}

## 4. 行为种子

### 4.1. BS-01 {行为种子标题}

- EARS: `WHEN/IF/WHILE/WHERE {条件}, THE 系统 SHALL {用户可观察行为}`
- 来源: [{路径编号} / 约束 / 非常规要求 / 重要推导]
- 说明: {这项行为保护什么用户结果}

## 5. 范围、约束与例外

- **必须保持的产品约束**：{用户明确要求或既有合同中不能改变的约束；无则写“无”}
- **非常规要求**：{有意偏离宿主项目惯例、安全或可用性默认的要求及理由；无则写“无”}
- **Out-of-Scope**：{明确排除的事项；无则写“无”}

## 6. 开放产品决定

**{问题一句话}** — {不同答案会改变什么产品结果}。方向：A) {…} B) {…}。推荐 {A}，因为 {理由}。

## 7. 必要性与风险

- **既有能力**：{可复用或相关的现有功能/合同；无则写“未发现直接覆盖”}
- **冲突**：{与既有产品方向的实质冲突；无则写“无”}
- **重要风险**：{只写会改变范围或使 story 不成立的风险；无则写“无阻塞风险”}
