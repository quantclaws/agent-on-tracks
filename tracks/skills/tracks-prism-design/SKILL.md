---
envelope: tracks-envelope:v2
name: tracks-prism-design
version: 0.2
description: M-DESIGN 评审判据包 - Prism 在 M-DESIGN PRISM_REVIEW 消费的语义判据
---

# M-DESIGN 评审判据包

Prism 在 M-DESIGN PRISM_REVIEW 按 assignment 指定的名称+版本加载本判据包，审起草的设计三件套（architecture、interfaces、test-plan）。本判据包只含**语义判据**；形式校验（template 合规、ID 文法、trace/binding 完整性、讨论就绪门禁）归 Runtime 程序校验，不在此范围。判据以稳定 ID（DESIGN-1…DESIGN-9）唯一表达，程序性自审只引用这些 ID。

## DESIGN-1 需求、AC 与三向闭包

每个有效 FR/NFR 和 AC 都有 `observable IF → required layer(s) → CI gate/job → rationale`：

- 每个接口条目都有输入、输出、状态、权限、错误、恢复和 `modules` 列。
- AC → IF → ARC 双向无 orphan；路径、命令、状态和失败语义一致。
- 面向人的主旅程使用公开出口并要求 e2e；后台 API 不能替代可见反馈。

三向闭包存在 orphan 或语义不一致的判 revise，经讨论协议锚定线程指出哪条 AC/IF/ARC 路径缺失或不一致。

## DESIGN-2 六元组闭合（island gate 输入）

每条 required AC 检查 owner / surface / composition / wiring / test / evidence 六项在设计文档中可定位：

- 任一缺失，或模块无法从任何交付面到达 → REVISE。
- 六元组 evidence 列为泛词（无具体命令/可观察输出）→ REVISE（亦见 DESIGN-8）。

## DESIGN-3 Test Plan 可执行性

- unit/integration/e2e 边界符合风险；required 多层证据不能互相替代。
- 公开 integration/e2e 命令真实存在或被本设计明确锁定为 foundation task。
- ground truth 独立于被测 validator；不得 mock 核心后声称 integration PASS。
- 若项目产出可安装构建物：E2E 必须通过真实安装路径执行（与最终用户一致的安装命令、隔离安装目标、非源码树工作目录），不得从源码导入或 editable install 冒充（test-plan §2.5）。首版设计必须声明，后续版本仅安装方式变更时修订；未声明即 REVISE。

## DESIGN-4 宿主项目测试执行合同（`.tracks/projects/project.toml`）

`.tracks/projects/project.toml` 是宿主协议的 canonical 位置；M-DESIGN 交付必须包含它，缺失即 REVISE。

- 合同必须声明 `[integration]` 段（`framework`、`paths`、`collect`、`run`、`cwd`）；`[e2e]` 段在 test-plan 有 e2e 层时必须存在，否则可选。
- `framework`、`paths`、`collect`、`run`、`cwd` 的实际取值由 project contract 声明，合法性以 assignment 注入为权威；本判据不固化任何具体 framework/命令/解释器。
- `collect`/`run` 命令必须使用宿主项目自己的工具链与环境（按 contract 声明），不得依赖 Tracks 运行时自带的解释器或依赖。
- `paths` 必须与 test-plan 声明的测试层路径一致。

## DESIGN-5 评审清单（逐项对照工件核验，不采信作者自述）

- 测试策略是否覆盖主要风险：对照 acceptance 的风险面核验 test-plan 的策略与层分配。
- 每条 AC 是否可追溯到测试层与 interfaces 出口（不止是 ID 出现）。
- 反模式 CI 门禁是否已启用或显式豁免。
- 测试数据来源是否可复现（若存在数据依赖）。
- 测试目录布局是否已文档化（推荐布局或项目定制说明）。
- Ground Truth 方法：若 test-plan §3 判定不适用，是否在设计中显式说明且未创建 ground truth 产物；若 §3 判定适用，ground truth 是否已文档化且为最小可运行的独立验证脚本（非桩、规模相称）。
- interfaces.md 与 test-plan 是否闭合：每个外部出口都有测试覆盖。
- 跨模块接口是否已标记（modules 列）并纳入集成覆盖。
- e2e 范围是否限定为 happy path（边界/错误情形划入 integration）。

作者自证的评审清单（文档中出现作者勾选的 review checklist）直接判 revise（亦见 DESIGN-8）。

## DESIGN-6 架构与接口

- 模块边界清晰，依赖方向合理，技术选型有取舍记录。
- interfaces.md 只含外部可观察契约，不含内部实现细节。
- 跨模块接口有集成测试覆盖。

## DESIGN-7 可实现性

- 实现者与测试编写者无需再选择 schema、adapter、版本源、build、runner、CI DAG 或失败语义。
- 不把 Spec 外产品决定伪装为架构；真正产品 gap 必须锚定 FR/AC 并 REVISE。

## DESIGN-8 合同真实性（以下任一情形直接 REVISE）

- 引用不存在的命令/工具/路径（把待实现物写成既有事实而未标注 foundation task）。
- 作者自证的评审清单（文档中出现作者勾选的 review checklist）。
- ground truth 与 test-plan §3 判定不符：§3 适用却缺 ground truth、或非最小可运行验证脚本（桩/不可运行/规模失控）、或被测系统 import、或算法与实现策略雷同；§3 不适用却创建了 ground truth 产物。
- 六元组 evidence 列为泛词（无具体命令/可观察输出）。
- 宿主工程质量守卫缺失或不完整（无 lint/复杂度/pre-commit/覆盖率/CI 门禁的 machine contracts，对照 `tracks-quality-guards` skill 的目录）。

## DESIGN-9 脚手架（Scaffold 宣言）

- architecture.md 必须有 Scaffold 宣言；宿主项目中实际创建的文件与宣言逐项一致——宣言外文件、或宣言列出但未创建的文件 → REVISE。
- scaffold 内容只含声明、配置、数据与 ground truth；脚手架中出现任何业务行为（可运行业务逻辑、罐头行为）→ REVISE。
- ground truth（当 test-plan §3 适用时）必须是最小可运行的独立验证脚本（真实可运行，非桩，规模相称）；§3 不适用时不得存在——与 DESIGN-8 的 ground truth 审核一致。
- 每项质量守卫在 machine contracts 中有安装命令、配置位置、阈值与执行点（对照 `tracks-quality-guards` skill 的目录）。

## 边界

本判据包不含形式校验规则：template 合规（frontmatter、section 结构）、ID 文法、binding 完整性（AC↔IF↔ARC trace）、讨论就绪门禁均归 Runtime 程序校验。Prism 的语义判据与 Runtime 的形式校验互补：Prism 可放行语义合格的设计三件套，但 template/trace 不合规的情况仍由出口门禁复跑捕获。