---
name: tracks-archer-design
version: 0.1
description: Archer M-DESIGN DRAFT/RESPOND 专用方法论 - 三文档起草、六元组 closure、test-plan §8 分层、Scaffold 宣言、CI/quality contract、外部依赖三层验证与 DESIGN 层标准（仅 Archer 的 M-DESIGN 阶段触发）
---

# tracks-archer-design — M-DESIGN 方法与 DESIGN 层标准

只供 role=archer 且处于 M-DESIGN DRAFT / RESPOND 时使用。本 skill 承载该阶段的详尽方法与阶段标准；宿主中立：具体语言、工具链、命令与路径取值由 assignment / project contract 提供，本 skill 不固化任何宿主技术栈。

## DRAFT 流程（起草）

1. 宿主项目调查：用只读手段（宿主可用的检索/读取工具）调查既有目录结构、技术栈、依赖、CI 配置、测试框架与命名惯例，不猜测。已有项目继承既有架构；全新项目由 Archer 选择并记录取舍。architecture.md §0 延续性声明逐条声明继承或变更，未提及者一律继承。
2. 以 Runtime 物化的模板起草三文档，完整保留各自 frontmatter。
3. 产出接口桩、脚手架与 ground truth（若适用）。
4. 在 architecture.md §1.2 为每条 required AC 写入一个完整 closure block（见下文六元组）。
5. 三文档缺一不可，全部写入 assignment 指定路径；不止步于规划或探索，结束前所有产物真正写入磁盘。

## RESPOND 流程（修订）

1. 每轮先处理 inline discussion 待办（查询/回复命令由 Runtime 注入上下文提供）。
2. 重读当前权威文档与 Prism 的 discussion 线程，按 findings 逐条修订并保存。
3. 重新核对 architecture.md §1.2 的每条 AC、六个精确字段和全部适用 IF- IDs；缺失或占位值必须在本轮修复。
4. 回复处理结果；Prism 发起的线程由 Prism 关闭，Archer 不代为操作。

## 三文档起草方法

- architecture.md：模块边界、依赖关系、技术选型（第三方依赖及版本）、关键取舍、CI 合同、composition root 一节、§1.2 closure、Scaffold 宣言。
- interfaces.md：只写外部可观察契约（数据模式、API 端点/签名、CLI 命令、事件结构、公共函数）；每个条目带 `modules` 列；分类用表格或列表；不写内部类层次、状态机、私有方法、缓存策略等实现细节。
- test-plan.md：先建立 Acceptance 的语义覆盖清单（每个 AC 记录可观察接口、CI gate/job 与分配理由；面向人的 Happy Path 记录 surface/context、动作、输入、可见结果、可用条件与反馈出口），再按 §8 做分层规划。
- 三文档是一个 design revision 整体：使用与 story/spec 相同的语言，专有名词、API 名称和文件路径保留英文；交付时不残留模板指引 blockquote（blockquote 只保留讨论线程）。

## 六元组 closure（ISLAND_GATE_1 输入合同）

对每条 required AC，设计必须填齐并在文档中可定位六项事实：

- owner：哪个模块负责；surface：经哪个交付面被触达（UI/API/CLI/public library 皆算，必须显式命名）；composition：在 composition root 中如何装配；wiring：入口→模块的逐跳真实调用链；test：哪个测试层覆盖、经哪个出口进入；evidence：M-TEST 阶段可产出的程序证据。
- evidence 必须写明具体命令与可观察输出特征，禁止"测试报告""checklist 通过"等泛词。
- 任何无法在六元组中定位的模块是设计缺陷：要么接回某条入口→AC 路径，要么从设计中删除；不得设计"只被测试调用的模块"。
- machine-readable 单行 block：在 architecture.md §1.2，一行即一个 block，禁止表格、合并 AC 或只写散文。语法与字段顺序固定：`- **FR-0010** owner=<...> surface=<...> composition=<...> wiring=<...> test=<...> evidence=<...> IF-MTEST-001`。
- `FR-0010` / `NFR-0010` 是 requirement token：把 `AC-FR0010-01` 写成 `FR-0010`，把 `AC-NFR0010-01` 写成 `NFR-0010`。
- 六个值必须全部非空且具体，不留 `{placeholder}`、`<...>` 或泛词。
- 每个 block 列出该 AC 适用的全部 IF- IDs，且每个 ID 必须存在于 interfaces.md 的 IF Registry；DRAFT 首次建立完整闭合，RESPOND 修订后重新核对 AC、六字段与 IF 集合。
- 此六元组是 M-IMPL ISLAND_GATE_1 的输入合同：设计期填齐，实现期只复核。

## test-plan §8 分层规划

- §8 只规划 integration 与 e2e 两个测试层（Shield 的交付范围）；layer 取值为逻辑层标识：integration、e2e 或 integration + e2e；默认落盘约定为 tests/integration 与 tests/e2e，实际路径/命令由 assignment / project contract 运行时提供。
- 不在 §8 规划 unit 测试：unit 是 Devon 对每个已实现 FR/NFR 的普遍义务，由覆盖率门禁（test-plan §5.1）保证，不预设 test function 名或文件名。
- e2e 限定 happy path；边界/错误情形划入 integration。若某条 AC 没有可观察的 integration/e2e 出口，说明设计有缺陷（interfaces 缺出口或 AC 需修订），不得用 unit-only 行跳过。
- 每个接口出口必须在 test-plan 中找到至少一种覆盖方式；跨模块接口（跨越 2 个以上模块）必须有集成测试覆盖。

## ground truth

- 当 test-plan §3 判定适用时，产出独立重算预期值的最小可运行验证脚本（真实可运行，非桩；独立来源按 §3.1 取其一：手工小脚本、约定第三方库或测试数据本身）。
- 独立性：不 import 被测系统，算法策略区别于实现提示；若脚本开始复刻被测行为本身，停下来重新设计验证切片。独立性由 Prism 审核。
- 规模与所验证内容相称；§3 判定不适用时不得创建 tests/ground_truth/，并在设计中显式说明。

## Scaffold 宣言（契约受限的脚手架清单）

- architecture.md 的「Scaffold 宣言」是 M-DESIGN 阶段在宿主项目创建文件的唯一写盘合同：一项一个文件，格式 `- path — purpose + kind`；kind 从 stub / config / data / ground-truth / ci-skeleton 命名。未声明写盘即审计违规，Runtime 拒绝 outcome 并回滚。
- scaffold 内容只限声明、配置、数据、ground truth 与 ci-skeleton——不写任何业务行为；业务行为属于实现阶段（Devon）。
- 接口桩：与真实模块同路径的源文件，完整签名但行为体仅抛合同 token，让 Shield 的契约测试在 Devon 实现之前即可加载；桩只声明合同所需的公开/跨模块接口，禁止任何可被当作成功结果的默认行为；桩文件的声明身份冻结后由 Devon 补全实现，不得改变声明合同。
- 脚手架禁止任何业务行为；声明性质量守卫配置与 ci-skeleton 属脚手架由 Archer 物理交付，生效副作用归 Runtime，Devon 只编写合同标注「待实现」的产物。（输出合同提示：最终回复遵循 core 的 `tracks-envelope:v2` 条件式合同，本 skill 不展开 envelope 语法。）

## CI 与质量守卫合同

- CI 合同覆盖八类抽象：环境、依赖准备、质量检查、测试、构建、证据、失败语义与稳定 required check 的存在性；具体平台形态由宿主技术栈决定，本 skill 不固化任何平台专有术语。
- 质量守卫合同五要素：安装命令、配置位置、阈值、执行点、CI required check。五要素写入「交付与运行合同」；Archer 定义并写入合同，Runtime 执行生效副作用；Devon 只编写合同显式标注「待实现」的产物文件，不执行安装或生效。
- 质量守卫栈按目录与安装分工的清单由 Runtime 注入的守卫目录 skill 提供；某类确无可用工具时，把缺失作为显式设计决定记录在 architecture.md，不得静默留空。
- 安装与隔离（test-plan §2.5）：若项目产出可安装构建物，首版设计必须声明 e2e 的安装方法（与最终用户一致）、隔离安装目标、运行时工作目录（非源码树）与初始化步骤；后续版本继承该声明，仅当安装方式本身变更时修订。

## 真实外部依赖三层验证

判据：spec 中出现宿主自身技术栈之外的外部依赖（外部服务 API、模型 provider、子进程可执行文件、真实网络/凭据握手，或任何只能在真实环境验证的行为）时，必须产出三层机制并填入 test-plan §6。交付 test-plan 前必跑 checklist：扫描 spec 外部依赖 → 存在则三层机制必须已设计。

- 通道隔离：live 测试走独立通道，默认套件排除。
- 环境探测 skip：凭据/可执行文件缺失时 skip 且输出显式 `LIVE_SKIPPED: missing <X>`；skip 不 fail，skip ≠ silent。
- 独立 job 硬门禁：CI 独立 job 配真凭据只跑 live 通道，release/tag 触发作为发版硬门禁，可辅以周期性调度防 live 通道腐烂。
- fake（每次跑）与 live（周期/里程碑跑）的 AC 不重叠；纯本地确定性逻辑（确无外部依赖）才允许不设 live 通道，且须在设计中显式说明。三层机制的具体实现工具绑定 Archer 为宿主做出的技术栈决策，不预设语言/平台。

## 宿主测试执行合同

- Archer 为宿主项目**设计并声明**测试执行合同（协议资产 `.tracks/projects/project.toml` 的 [integration]/[e2e] 段）：framework、paths、collect/run 命令与 cwd；设计并声明之后**不负责执行**——命令适配宿主 toolchain，由宿主环境运行。
- framework/paths/collect/run/cwd 的具体取值由 assignment / project contract 声明，本 skill 不固化任何宿主语言、解释器或测试工具；测试层是逻辑层标识（unit / integration / e2e），实际落盘路径由 project contract 提供。
- 该合同在 M-DESIGN 阶段随三文档一起提交，属于产物摘要所列交付物。

## 质量标准（DESIGN 层）

- DESIGN-01 三文档一个整体：三文档全部落盘、frontmatter 完整、缺一不可。
- DESIGN-02 三向闭包：每个 AC → interfaces 出口 → test-plan 覆盖，无 orphan。
- DESIGN-03 六元组逐条闭合：closure block 可解析、字段顺序正确、六值非空具体、IF Registry 存在。
- DESIGN-04 §8 层分配显式：只规划 integration/e2e，unit 由覆盖率门禁保证，e2e 限定 happy path。
- DESIGN-05 ground truth 独立：§3 适用才产出，独立重算、不 import 被测系统、规模相称。
- DESIGN-06 Scaffold 唯一写盘合同：只创建宣言声明文件，未声明写盘即审计违规。
- DESIGN-07 CI 合同五要素齐备：安装命令、配置位置、阈值、执行点、required check。
- DESIGN-08 外部依赖三层验证：通道隔离、环境探测 skip、独立 job 硬门禁三件套齐全。
- DESIGN-09 测试执行合同设计并声明、不负责执行，命令适配宿主 toolchain。
- DESIGN-10 讨论协议合规：线程经讨论机制操作，Prism 线程由 Prism 关闭。

## 退出前自审（程序性动作，引用 ID）

- 三文档已落盘且 frontmatter 完整？（DESIGN-01）
- 每条 AC 的 closure block 已逐条核对？（DESIGN-03）
- §8 分层与接口覆盖已闭合？（DESIGN-02 / DESIGN-04）
- ground truth 已按 §3 判定处理？（DESIGN-05）
- Scaffold 宣言与实际写盘一致？（DESIGN-06）
- CI 合同与质量守卫五要素已写入合同？（DESIGN-07）
- 外部依赖三层机制已设计？（DESIGN-08）
- 测试执行合同已声明且未越权执行？（DESIGN-09）
- 讨论待办已处理且未代操作他人线程？（DESIGN-10）

## 讨论协议（展开）

- 讨论一律经 Runtime 注入的讨论机制（相关 skill 与命令由 Runtime 注入上下文提供），不手工编辑 blockquote。
- 禁止对自建 inline 讨论线程自评 resolved；finding 仅能由非作者以新证据关闭。
- Prism 发起的线程由 Prism 设 resolved，Archer 不代为操作；Archer 自建的澄清线程在得到答案后按协议关闭。
