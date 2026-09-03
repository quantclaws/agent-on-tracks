---
name: tracks-prism-impl
version: 0.2
description: M-IMPL 代码评审判据包 - Prism 在 M-IMPL 评审消费的语义判据
---

# M-IMPL 代码评审判据包

Prism 在 M-IMPL 评审按 assignment 指定的名称+版本加载本判据包，审实现的代码、测试与任务图。本判据包只含**语义判据**；形式校验（template 合规、binding 完整性、ID 文法、测试集合/Red 程序分类）归 Runtime 程序校验，不在此范围。判据以稳定 ID（IMPL-1…IMPL-5）唯一表达，程序性自审只引用这些 ID。

## IMPL-1 实现遵循锁定设计

- 实现是否遵循锁定 Architecture/Interfaces，不允许实现者重新选择设计。
- 实现不得偏离 interfaces.md 声明的外部契约（事件 type/payload、CLI 输出、文件 schema、git 状态）。
- 实现不得引入设计中未声明的模块边界或依赖方向。

实现重新选择设计或偏离锁定契约的判 revise，指出哪条锁定的 IF/ARC 被偏离。

## IMPL-2 可读性、职责、DRY、变更影响

- 代码可读性：命名清晰、结构合理、无过度嵌套。
- 职责单一：每个模块/函数/类有明确的单一职责。
- DRY：无显著重复逻辑（可提取为共享工具）。
- 变更影响：实现修改是否影响设计未声明的下游消费者。

## IMPL-3 测试反模式

测试不得出现以下反模式：

- 修改断言迎合实现。
- 无依据 skip（无外部跟踪事项引用，非测试自身环境所致的跳过）。
- 断言降级（断言"抛了异常"而不实际执行并捕获被测行为）。
- 吞异常（捕获包裹被测代码而什么都不做）。
- 过度 mock（mock 框架内核而非外部依赖，用于使测试"通过"）。
- 从实现取 ground truth（因实现输出 X 所以断言 X）。
- 捏造硬编码值（非 fixture/重算的预期值）。
- 无效断言（恒真断言 / 断言对象非空 作为唯一断言）。

## IMPL-4 命名稳定性

- diff 中不得在目录/模块/文件名中嵌入版本号或时间前缀（`cli_v12.py`、`api_v2/`、`new_xxx.py`），除非 spec 明确声明共存窗口。

命名不稳定的判 revise，指出哪个文件/模块名违反了命名稳定性。

## IMPL-5 浅层安全扫描

只报告明显安全问题：

- `eval`/`exec` 使用。
- 硬编码 secret。
- SQL 拼接。
- `shell=True` + 不可信输入。

## 程序性自审（PRISM_FINAL）

- 逐任务读取 outcome 携带的 advisories（`isolation` / `scope_gap` 等自报）并独立核验——自报是线索不是结论：`isolation` 需查派发隔离证据，`scope_gap` 需对照任务 scope 边界。未核验的 advisory 不得默认无害。
- 对 integration/e2e 检查每个 required AC 有真实收集/evidence，跨模块未被 mock。
- 每个判据实际执行验证并记录当轮证据（命令 / 文件 / 行号），未验证的判据不得默认 pass。

## 子状态路由语义

classification 的具体 token、默认取值与 payload 字段以 assignment 注入的 `classification_vocabulary` 与 schema 为权威；以下为本判据包保留的路由语义（冲突时以 assignment 为准）：

### M-IMPL PRISM_PLAN —— 任务图评审

- 任务图缺陷——依赖序错误、任务型误分类、依赖/预算/批次错误，以及任何指向任务图产物（tasks.json / tasks.md）的 finding——一律缺省分类，回 PLANNING 由重规划者就地重分解。把任务图缺陷误标为设计缺口会触发错误的硬回滚，且无受支持通道能从更早阶段前向回到 M-IMPL。
- 设计缺口分类仅限设计文档自身的缺口/互斥；桩缺口分类仅限 interfaces 承诺的桩/接口在代码中不存在且任务图调整无法补救。
- **可满足性（机器不可判的核心判据）**：每个声明的 acceptance 锚点在其 owner 任务的 GREEN 时刻必须可行绿——锚点所在层的全部接口已由该任务或其依赖链前置任务实现；把结构性不可能转绿的锚点塞给某任务是排序缺陷。e2e 层目标是终态覆盖锚点，**不属于** acceptance 覆盖义务：不得以"e2e 文件真实存在"为由要求把 e2e 路径写进 acceptance 锚点（那是错层），也不再对 e2e 目标要求收集可达。
- 依次核验**结构**与**覆盖**：unit 层锚点全部指向 unit 层路径、acceptance 锚点全部指向 integration 层路径且非空（实际落盘路径由 assignment / project layout 提供）；全体任务的 acceptance 锚点并集 ⊇ test-plan §8 全部 integration 行目标（反向脏锚——声明了但 §8 无行——同样拒绝）。

### M-IMPL PRISM_RED —— RED 工件评审

- RED 单测工件自身缺陷（断言与冻结合同相悖、锚错文件、fixture 错）→ red_defect，回 RED 由原实现者重钉。
- R-tree 全绿、无可合法 Red、任务重复/应改为 verification-only → plan_defect，回 PLANNING 由重规划者拆除/改型。绝不得把"无合法 Red target"标为 RED 测试缺陷：重钉不会创造失败断言，只烧预算。
- 普通工件 revise → 缺省，回 RED。

### M-IMPL PRISM_FINAL —— 实现评审

- 实现缺陷（允许范围内产品代码）→ 实现缺陷分类，回 GREEN。
- RED 单测自身缺陷 → red_defect，回 RED 新 lineage。
- 任务图/scope 缺陷 → plan_defect，回 PLANNING。
- 普通工件 revise → 缺省，回 GREEN。

### DIAGNOSE —— 合同争议诊断（归因不明时）

消费失败证据与 assignment 注入的 `classification_vocabulary`。**所有权纪律（先定缺陷文件域，再看失败表象）**：

- RED 单测自身缺陷 → red_defect，唯一合法修复者是原 RED 实现者（重钉重写）；绝不得归为冻结测试缺陷——测试编写者不得触碰 RED 单测（角色分离）。
- Shield 冻结验收测试自身缺陷 → test_defect，唯一合法修复者是测试编写者。
- 允许范围内产品代码缺陷 → impl_defect，修复者是实现者。
- 修复需要任务范围外文件、无可合法 Red、或任务重复/过时 → plan_defect，由重规划者处理。

错配所有权（如把 RED 单测缺陷归给测试编写者）会把修复派给无权且不该触碰该文件的角色，制造结构死锁。诊断结论必须为唯一判定 + 一句话 reason + evidence 指向具体文件/行/命令输出；分析与论证放正文，结论放交付。其余情形按 assignment 注入的 vocabulary 与 schema 输出。

- DIAGNOSE 只归因不开处方：输出 classification + evidence + 约束/事实陈述（如依赖序、hash 事实），**禁止 remedy 处方**（不设计补救方案、不裁定二选一）；方案设计权在 Archer PLANNING。

## 边界

本判据包不含形式校验规则：template 合规、binding 完整性、ID 文法、测试集合/执行/Red 程序分类均归 Runtime 程序校验。Prism 的语义判据与 Runtime 的形式校验互补：Prism 可放行语义合格的实现，但 trace/binding 不合规的情况仍由运行时捕获。