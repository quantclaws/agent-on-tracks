---
name: tracks-prism-test
version: 0.2
description: 测试资产判据包 - Prism 在 M-TEST PRISM_REVIEW 消费的语义判据
---

# 测试资产判据包

Prism 在 M-TEST PRISM_REVIEW 按 assignment 指定的名称+版本加载本判据包，审测试编写者交付的测试资产（integration/e2e 层）。本判据包只含**语义判据**；形式校验（trace 标记格式、绑定完整性、ID 文法）归 Runtime 程序校验，不在此范围。判据以稳定 ID（TEST-1…TEST-5）唯一表达，程序性自审只引用这些 ID。

## TEST-1 忠于 AC

每条测试必须忠于其引用的 AC。AC 引用由测试用例上方的 trace 标记注释行声明（`# AC-FRXXXX-YY@<version> TRACKS-TRACE <描述>`，特征词不可省略）：

- 测试断言的行为与 AC 描述的可观察效果一致，不断言 AC 未要求的内容。
- 测试不通过"重新定义成功条件"来适配实现（cheating 模式）。
- AC 要求的可观察出口在测试中被实际读取和断言，而非仅调用后忽略结果。
- 多条 AC 共用一个测试用例时，每条 AC 的可观察效果都有对应断言。

不忠于 AC 的测试判 revise，指出哪条 AC 的哪个可观察效果缺失或偏离。

## TEST-2 断言落公开出口

测试断言只能落在 interfaces.md §4 定义的外部可观察出口（事件、CLI 输出、文件 schema、git 状态）：

- 不断言内部状态字段、私有方法、中间数据结构（§1.2 non-observable）。
- 不通过 mock 框架内核（测试状态机、Red 分类、trace 逻辑）来"使测试通过"。
- 不通过 import 被测系统内部状态来窥探（而非经公开出口观察）。
- 事件断言读取事件的 type + payload 字段，不依赖事件序号或时间戳（确定性）。

断言未落公开出口的测试判 revise，指出应使用哪个 §4 出口。

## TEST-3 counterexample 绑定

每条 required 测试（integration/e2e 层）绑定一个 counterexample：

- counterexample 是一个只偏离目标合同的最小行为补丁（git patch），验证该测试能将其杀死（killed）。
- counterexample 不偏离无关合同（最小性：只改目标行为，不改其他）。
- counterexample 的 kill 证据在测试或 manifest 中可追溯（patch 文件 + kill 记录）。
- 缺失 counterexample 或 counterexample 不能被测试杀死的判 revise。

## TEST-4 无伪测试

测试不得是伪测试（cheating 模式）：

- 非恒真断言 / 断言对象非空 作为唯一断言。
- 非捕获包裹被测代码而什么都不做（吞异常）。
- 非无依据 skip/ignore（无外部跟踪事项引用，非测试自身环境所致的跳过）。
- 非硬编码预期值（预期值来自 fixture 数据或 ground truth 重算，非"因为当前实现输出 X 所以断言 X"）。
- 非断言降级（断言"抛了异常"而不实际执行并捕获被测行为）。
- 非过度 mock（mock 框架内核而非外部依赖）。

伪测试判 revise，指出违反了哪种 cheating 模式。

## TEST-5 合法 Red（M-TEST 特有）

M-TEST 交付的是测试资产而非绿色结果。每条测试必须失败且失败必须合法：

- 合法 Red：行为断言失败、合同声明的桩 token 抛错（桩只抛出合同未实现标记，不实现业务）、合同声明的符号缺失。
- 非法 Red：collection/语法/fixture/import 错误（测试本身有缺陷，非被测合同未实现）。
- 测试意外通过视为非法（桩只抛出时通过意味断言空洞或未命中桩）。

**时序与证据消费（canonical）**：M-TEST 子状态时序为 WRITE → 全量 COLLECT → Runtime RED_CHECK(SELECT_R2) → PRISM_REVIEW → EXIT——Red 程序分类先于你的评审，非法 Red 已在程序分类阶段被处理，不会到达你这里。因此你消费的是当前树的 `red.validated` 身份证据（selection binding + 逐节点合法 Red 分类），以它为合法 Red 判定的事实基础；对合法 Red 节点只执行**隔离 counterexample kill**（最小反例补丁 + 单点实跑），**绝不重跑普通套件**——把套件重跑当评审手段是合同违规。Prism 的语义判断与 Runtime 的程序分类互不替代。

## 程序性自审（全量清单制）

每次 PRISM_REVIEW 必须对上述 5 条判据逐项实际执行验证，verdict 报告必须逐判据给出结论与当轮证据（命令 / 文件 / 行号）；未实际执行验证的判据不得默认 pass。执行时包含：

- **绑定语义核查**：trace 标记所在测试用例的实际断言必须覆盖该 AC 的核心可观察效果（含正向路径，不只负向）；形式上已绑定但断言语义属于其他 AC 的判 revise。
- **可应用性核查**：逐个 counterexample patch 在当前 HEAD 实际执行应用检查；kill 记录各条口径必须与当前树一致，过时口径视同缺失。
- **触发可达性核查**：测试触发条件（fixture、路径守卫、前置状态）在合规运行时下可达；守卫恒不触发的死代码测试判 revise。
- **永续性核查**：断言所依赖的 fixture / 仓库 / 路径必须与合规运行时的真实产物位置一致——不会因运行时合规而永久 Red，也不要求运行时接受伪造产物才能绿。

assignment 的 evidence 字段是上一轮失败的上下文，**不是本次评审范围**；只评审 evidence 指向的主题而放行其余判据，视为未完成评审。

## 缺陷分类与路由（M-TEST 语境）

- 测试资产自身缺陷（test_defect 语义）→ 经代码 finding 结构化通道交付，回 WRITE 由测试编写者修复；禁止用文档线程承载测试代码 finding。
- 文档自身缺陷（test-plan / acceptance / spec 语义）→ 经讨论协议锚定对应文档线程，回相应上游阶段由文档作者修复。
- classification 的具体 token 与路由取值以 assignment 注入的词汇与 schema 为权威。

## 边界

本判据包不含形式校验规则：trace 标记长格式（marker 长格式）、绑定完整性（每条 required AC ≥1 标记、无无主标记）、ID 文法、tombstone 规则均归 Runtime 程序校验。Prism 的语义判据与 Runtime 的形式校验互补：Prism 可放行语义合格的测试资产，但标记不合规的情况仍由出口 gate 复跑捕获。