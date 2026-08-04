---
acc_id: ACC-004
created: 2026-08-05
status: draft
sha:
---

# M-TEST 阶段与需求追踪工具 - 验收标准

## FR-0010 M-TEST 阶段注册与子状态机驱动

### AC-FR0010-01

  - M-DESIGN EXIT（`prism.verdict(pass)` + 程序校验通过）后事件流出现 `stage.entered(M-TEST)`，初始子状态为 `DISPATCH`（SM-01.1）
  - 进入前事件序列中无 `human.review`/`human.approval` 在 M-DESIGN EXIT 之后插队（M-TEST 由程序证据触发，非 Human 门禁）

### AC-FR0010-02

  - `trac status` 在 M-TEST 期间报告 `stage=M-TEST` 与当前子状态（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT/DIAGNOSE 之一）
  - M-DESIGN EXIT 后 `trac run` 能进入 M-TEST（`stage.entered(M-TEST)`），M-TEST EXIT 后无 M-IMPL 进入而是 `run.completed(terminal_state="boundary")`（`_NEXT_STAGE` 接续与 boundary 收尾的可观察效果）

### AC-FR0010-03

  - M-TEST 子状态机的状态转移严格遵循 SM-01 清单（DISPATCH -> WRITE -> COLLECT -> PRISM_REVIEW -> RED_CHECK -> EXIT / DIAGNOSE）：fake 端到端旅程的事件流按该序列依次出现各子状态，未列出的转移不被产出--fake 通道注入非法转移时 `trac run` 不推进且事件流不出现越界 `stage.exited`/`run.completed`

### AC-FR0010-04

  - 既有 M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL / M-DESIGN 阶段行为不变：fake 端到端旅程 `tests/e2e/test_full_journey.py::test_full_journey_to_boundary` 的 M-DESIGN 之前事件前缀逐字节稳定，M-TEST 接入不改变其转移、事件与 boundary 终态

### AC-FR0010-05

  - M-TEST 子状态机由显式控制流驱动（非仅 StageDef 表驱动）：M-TEST 期间 `trac status` 报告的子状态序列（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT/DIAGNOSE）是既有 DRAFT/REVIEW/EXIT 模式无法产出的，证明 M-TEST 专属控制流已接入
  - kernel 纯函数边界维持见 NFR-0030（drop 投影表后从事件重建 M-TEST 状态一致）

## FR-0020 M-TEST WRITE：Shield 任务派发与可 collect 测试资产产出

### AC-FR0020-01

  - DISPATCH（SM-01.2）：Runtime 按 test-plan 的层归属（integration/e2e）与变绿条件（IF- 标识，FR-0140）创建 Shield tasks；tasks 就绪后事件流进入 `WRITE` 子状态，`trac status` 报告 `substate=WRITE`

### AC-FR0020-02

  - WRITE（SM-01.3）：dispatch Shield（FR-0120）后，宿主项目 `tests/integration/`、`tests/e2e/`、`tests/assets/`、`tests/counterexamples/` 下存在 Shield 写入的测试文件
  - integration 测试覆盖 happy + 关键错误/边界路径；e2e 测试仅 happy path

### AC-FR0020-03

  - Shield outcome 后，宿主项目 `tests/integration/` 与 `tests/e2e/` 下存在可 collect（可 import、无 collection 错误）且全部合法失败的测试文件（SM-01.3）

### AC-FR0020-04

  - WRITE validate 失败重派 Shield（SM-01.4）：`outcome.received(status!=done)` 或 validate 失败产生 `verdict.failed`，随后重派同一 Shield；累计第 3 次失败 -> `status=awaiting_human`、`awaiting=escalation`，`trac run` 不再产出 Shield 派发 command

### AC-FR0020-05

  - Shield 不 commit/push、不判定 PASS、不修改产品代码与接口桩（单写者纪律，RP-01）：Shield outcome 后 git 工作区中产品代码、接口桩、需求/设计文档无由 Shield 产生的改动；越权写由 FR-0120 审计检出并回滚
  - Shield 的本地自检（collection 通过 + 合法 Red + counterexample killed）是其 outcome 前自检，不构成 Runtime 退出依据：退出依据仅来自 Runtime 复跑（FR-0030/FR-0050/FR-0070）

## FR-0030 M-TEST COLLECT：Runtime 独立 collection

### AC-FR0030-01

  - COLLECT（SM-01.5）：Runtime 独立执行 collection/import，不读 Shield 自述；全部测试必须可 collect（接口桩保证可 import）

### AC-FR0030-02

  - collection 成功 -> 事件流出现 `test.collected(passed)`，子状态进入 `PRISM_REVIEW`（SM-01.5）
  - collection 失败 -> 事件流出现 `test.collected(failed)`，子状态回 `WRITE` 重派 Shield（SM-01.6）

### AC-FR0030-03

  - Runtime 复跑 collection 的证据落事件（`test.collected`），可在 `trac report` 中作为独立活动展示；Shield stdout 中"collection 通过"的自述不构成通过证据

## FR-0040 M-TEST PRISM_REVIEW：判据包绑定与反自述回读

### AC-FR0040-01

  - PRISM_REVIEW（SM-01.7）：dispatch Prism 按 assignment 指定的测试资产判据包（D-29）审测试合约（忠于 AC + 断言落在公开出口 + counterexample 绑定 + 无伪测试）；`trac status` 报告 `substate=PRISM_REVIEW`

### AC-FR0040-02

  - 反自述三件套（D-29，必须交付）：
    - ① assignment 写明应加载判据包的名称+版本（Runtime 决定，Prism 不自选）--派发 command 的 assignment payload 含判据包名称与版本字段
    - ② Prism verdict outcome 携带实际加载判据包 identity（`prism.verdict` 事件 payload 含判据包 identity）
    - ③ Runtime 回读核对：outcome identity 与 assignment 指定 identity 不匹配 -> 判失败重派 Prism（产生 `verdict.failed`，不进入 RED_CHECK）

### AC-FR0040-03

  - `prism.verdict(pass)` -> 子状态进入 `RED_CHECK`（SM-01.7）
  - `prism.verdict(revise)` -> 子状态回 `WRITE` 重派 Shield（SM-01.8）

### AC-FR0040-04

  - Prism `revise` 必须经 `trac discuss` 锚定线程：revise verdict 无对应 open 线程 -> 判 `revise_without_findings` failure（承自 v0.3 Prism 评审协议），重派 Prism；`trac discuss query` 可观察到该锚定线程

### AC-FR0040-05

  - 形式校验不混入判据包（D-14 边界）：测试资产判据包 skill 文本可读，其内容为语义判据（忠于 AC、断言落公开出口、counterexample 绑定、无伪测试），不含形式校验规则（marker 长格式、绑定完整性、ID 文法--这些归 Runtime，FR-0080/FR-0130）；形式校验失败由 Runtime 程序校验产出，不依赖判据包

### AC-FR0040-06

  - 测试资产判据包物化为 skill，其物化与回收（Runtime dispatch 前物化到 opencode 发现路径、终态/失败后清理、崩溃后 reconcile 清理悬挂物化）与既有 skill（tracks-discuz）同构

## FR-0050 M-TEST RED_CHECK：合法 Red 分类

### AC-FR0050-01

  - RED_CHECK（SM-01.9）：Runtime 独立复跑 integration/e2e（不读 Shield 自述）；复跑证据落事件

### AC-FR0050-02

  - 合法 Red 分类（每条失败须可归为以下之一）：行为断言失败 / 桩合同 token 失败（`NotImplementedError("IF-...")`）/ 合同声明的 symbol 缺失

### AC-FR0050-03

  - 非法 Red 分类：collection/语法/fixture/import 错误

### AC-FR0050-04

  - 测试意外通过视为非法（桩只 raise，通过通常意味测试未真正命中桩：断言空洞、mock 掉被测对象、或测了别的东西）

### AC-FR0050-05

  - 全部合法 Red -> 事件流出现 `red.validated(valid)`，子状态进入 `EXIT`（SM-01.9）
  - 存在非法 Red 或意外通过 -> 事件流出现 `red.validated(invalid)`，子状态进入 `DIAGNOSE`（SM-01.10）

### AC-FR0050-06

  - 非常规要求可验证：本阶段接受并要求 Red--每条测试必须失败且失败必须合法；fake 通道下全部测试意外通过时 `trac run` 不退出 M-TEST（进入 DIAGNOSE），与常规"测试应通过"相反

## FR-0060 M-TEST DIAGNOSE：四路诊断路由

### AC-FR0060-01

  - DIAGNOSE 在 RED_CHECK 遇非法 Red 或意外通过时进入（SM-01.11）；"测试错还是接口错"的分流不交给 Human，需语义判断时分派 Prism diagnostic review

### AC-FR0060-02

  - 测试缺陷 -> 回 `WRITE` 重派 Shield（SM-01.11 路由 1）；`verdict.failed` 事件 `classification=test_defect`

### AC-FR0060-03

  - 桩或接口缺口 -> `stage.rolled_back` 回 M-DESIGN（SM-01.12）；`verdict.failed` 事件 `classification=stub_gap`、`target_stage=M-DESIGN`；该回退由 Archer+Prism 裁定，不经 Human 批准（事件流无 `human.*` 触发该回退）

### AC-FR0060-04

  - AC 缺口 -> `stage.rolled_back` 回 M-ACC（SM-01.13）；`verdict.failed` 事件 `classification=ac_gap`、`target_stage=M-ACC`；该回退需 Human 批准（事件流出现 `human.*` 批准后才 `stage.rolled_back`）

### AC-FR0060-05

  - Spec 缺口 -> `stage.rolled_back` 回 M-SPEC（SM-01.13）；`verdict.failed` 事件 `classification=spec_gap`、`target_stage=M-SPEC`；该回退需 Human 批准

### AC-FR0060-06

  - `verdict.failed(test_defect|stub_gap|ac_gap|spec_gap)` 事件携带 classification、target stage 与 artifact disposition；`trac report` 可展示该分类与路由结果

### AC-FR0060-07

  - M-IMPL 侧的 SHIELD_FIX / DIAGNOSE 路由仅预留事件与路由定义，不实现（Out-of-Scope）：M-TEST 期间不产出 M-IMPL 侧 SHIELD_FIX 事件、不进入 M-IMPL

## FR-0070 M-TEST EXIT：trace 闭合门禁与边界退出

### AC-FR0070-01

  - EXIT 退出门禁（SM-01.14）= collection 成功 + 合法 Red + Prism verdict(pass) + `trac check trace` 闭合，全部由 Runtime 复跑取得（不信 Shield 自述）；任一不满足不退出

### AC-FR0070-02

  - trace 闭合要求：每条 required AC（integration|e2e 层归属）至少一条长格式 marker `AC-FRXXXX-YY@<version>` 绑定、无无主 marker；`trac check trace` 输出无硬错误

### AC-FR0070-03

  - `trac check trace` 统一检查所有 AC（不感知调用方阶段）；M-TEST 退出门禁自行过滤只看 required AC（integration|e2e 层 AC）--依据 §1 需求描述 Part B 与 flow.md §9.3
  - 非 required AC（如 unit 层 AC）的 trace 缺口不阻塞 M-TEST 退出，但仍由 `trac check trace` 报告

### AC-FR0070-04

  - trace 不闭合不得退出：`trac check trace` 在 EXIT 复跑返回非零时，事件流不出现 `stage.exited(M-TEST)`

### AC-FR0070-05

  - trace 闭合复跑失败的程序化恢复（SM-01.15）：EXIT 复跑 `trac check trace` 失败时不退出也不阻塞--Runtime 携带 trace findings 重派 Shield 修复 marker/绑定（EXIT -> WRITE），复用 <=3 升级预算；第 3 次失败升级 `awaiting_human`/escalation（与 WRITE validate 失败同构）
  - escalation 不违反 BS-05：BS-05 约束退出不等待 `human.review`/`human.approval`，escalation 是反复修复失败后的处置（非退出前置门禁）

### AC-FR0070-06

  - M-TEST 无 Human 门禁（BS-05）：退出依据全部是程序证据，事件流中 M-TEST 期间不出现作为退出前置的 `human.review` 或 `human.approval`

### AC-FR0070-07

  - 退出时 Runtime 创建受控测试 commit 冻结测试资产，事件流出现 `test.committed` 与 `stage.exited(M-TEST)`；git log 含该受控测试 commit

### AC-FR0070-08

  - 因 M-IMPL 未注册，`stage.exited(M-TEST)` 后事件流出现 `run.completed(terminal_state="boundary")`，停在 M-TEST->M-IMPL 边界，不进入 M-IMPL；`trac status` 报告 `terminal=boundary`

## FR-0080 trac check trace：双向孤儿检测

### AC-FR0080-01

  - `trac check trace` 解析 story/spec/acceptance 三文档 + 测试 marker，对 BS -> FR -> AC -> test 全链做双向孤儿检测；可独立 CLI 运行

### AC-FR0080-02

  - FR<->AC 硬错误（非零退出）：存在无 AC 的 FR（spec `### FR-XXXX` 无 acceptance `## FR-XXXX` 章节或章节内无 AC）、回指不存在 FR 的 AC（acceptance `### AC-FRXXXX-YY` 内嵌 FR 不在 spec 中）均判失败

### AC-FR0080-03

  - AC<->test 硬错误（非零退出）：存在无测试绑定的 AC（required AC 无长格式 marker）、marker 指向不存在 AC 的测试函数均判失败

### AC-FR0080-04

  - BS->FR 仅 warning（不改变退出码）：无 FR 承接的 BS 只产生 warning，退出码仍为 0（当无硬错误时）；warning 内容含 BS 编号

### AC-FR0080-05

  - 孤儿清单完整列出（含文件与行号，不短路）：存在多处孤儿时全部报告，不因首条短路

### AC-FR0080-06

  - 测试 marker 必须使用长格式 `AC-FRXXXX-YY@<version>`（代码不按版本分目录，所有版本测试共存于同一棵 tests/ 树）：短格式 marker（缺版本号）触发 trace 检查失败并指出位置

### AC-FR0080-07

  - 长格式 marker 引用不存在的 AC 显式报错（NOT_FOUND），不静默回退到同名 AC

### AC-FR0080-08

  - ID 唯一性：文档中出现重复 FR/AC 编号时 trace 失败并指出冲突双方的文件与行号

### AC-FR0080-09

  - tombstone ID 不被计为孤儿（FR-0130）：被标记为 tombstone 的 ID 在 trace 检查中不产生孤儿报告

### AC-FR0080-10

  - `trac check trace` 统一检查所有 AC，不感知调用方阶段；M-TEST 退出门禁（FR-0070）自行过滤只看 required AC（integration|e2e 层 AC）

### AC-FR0080-11

  - 工具可被引擎在 M-TEST EXIT 当 verdict 来源调用（FR-0070 消费 trace）：引擎调用读取退出码（与 `--json` 输出）作为门禁判据

## FR-0090 trac check reach：模块级孤岛检测

### AC-FR0090-01

  - `trac check reach` 从声明入口点（pyproject `[project.scripts]` / `__main__` / 显式白名单）构建模块级 import 图；可独立 CLI 运行

### AC-FR0090-02

  - 报告从任何入口都不可达的生产模块（孤岛），列出模块名

### AC-FR0090-03

  - 纯测试模块（`tests/` 下或仅含测试的模块）不计入，不产生误报

### AC-FR0090-04

  - 无任何入口声明时报错而非静默通过（避免空通过），退出码非零

### AC-FR0090-05

  - 本版只做模块级 import 图，不做函数级调用图（Out-of-Scope）：reach 输出不包含函数级调用关系

### AC-FR0090-06

  - 工具可被引擎当 verdict 来源调用（M-IMPL ISLAND_GATE 孤岛闭合与 M-VERIFY 反 slop 门禁消费，flow.md §10/§11）：本 release 交付工具本体，下游消费随后续阶段接入（本 release 不实现 ISLAND_GATE/反 slop 消费）

## FR-0100 存量基线豁免

### AC-FR0100-01

  - 宿主项目携带存量文档/编号/模块时，trace 与 reach 两个检查都提供兼容能力，机制是同一份存量基线（legacy baseline）豁免清单：在采纳 tracks 时（`trac init` adoption）声明豁免内容；基线以 `.tracks/` 下的声明文件维护

### AC-FR0100-02

  - 被列入基线的文档/编号 trace 不计为孤儿；被列入基线的模块 reach 不计为孤岛

### AC-FR0100-03

  - 基线只冻结采纳时刻的存量、不回填历史（承自 louke STR-1410 cutover baseline / STR-1411 legacy baseline）：基线声明中仅含采纳时刻已存在的存量

### AC-FR0100-04

  - 基线之后新增的同形内容仍正常报错：采纳后新增的无 AC 的 FR、无主 marker、孤岛模块仍被 trace/reach 报告

### AC-FR0100-05

  - 不强制重编号、不强制补链路（tracks 自身的 v0.1 即为存量样本）：基线豁免不触发任何文档/编号/模块的自动修改（NFR-0010）

### AC-FR0100-06

  - 基线声明的具体存储 schema（字段命名、文件格式）属设计层，由 Archer 依据 `.tracks/` 既有目录约定裁定；本 FR 锁定豁免语义与作用范围

## FR-0110 双格式输出与稳定退出码

### AC-FR0110-01

  - `trac check trace` 与 `trac check reach` 均支持人类可读（默认）与 `--json` 双格式输出

### AC-FR0110-02

  - 退出码语义稳定：0=通过（无硬错误，可有 warning）、非 0=有硬错误；多次运行同一输入退出码一致

### AC-FR0110-03

  - CLI 与引擎双消费者：人类经默认格式读证据；引擎调用读取 `--json` 与退出码作为 verdict 来源（FR-0070 M-TEST EXIT 消费 trace）

## FR-0120 Shield opencode agent 接入与写范围审计

### AC-FR0120-01

  - `AGENT_NAME` 增补 `shield -> Shield`；Shield 作为真实 opencode agent 被 dispatch（承自 v0.3 Archer/Prism 接入模式）
  - Shield prompt/skill（tracks-discuz）物化与回收（dispatch 前物化到 opencode 发现路径、终态/失败后清理、崩溃后 reconcile 清理悬挂物化）与 Archer/Prism 同构

### AC-FR0120-02

  - `tracks/agents/Shield.md` 加入 deliverables 一致性集合：`trac check deliverables` 校验其存在性 + frontmatter `version` + `IQ`（与既有五个 agent 提示词同构）；缺失/非法 -> 非零退出阻塞合并

### AC-FR0120-03

  - Shield 写范围审计：Shield 仅可写 `tests/integration/`、`tests/e2e/`、`tests/assets/`、`tests/counterexamples/`；允许集即此四目录

### AC-FR0120-04

  - 越权写文件被审计检出并通过 git 回滚（`over_reach` failure_class，承自 v0.3 写范围审计机制）：outcome failed、记录路径级证据、无提交、无推进；回滚仅移除可证明由 Shield 产生的改动，Human 既有修改不被覆盖

### AC-FR0120-05

  - Shield 不写产品代码、接口桩、`tests/ground_truth/`、需求/设计文档：上述路径的 Shield 改动被审计判为越权并回滚

### AC-FR0120-06

  - Shield 读 test-plan/interfaces/acceptance + 接口桩 -> 写测试 -> 本地自检（collection 通过 + 合法 Red + counterexample killed）-> 返回 outcome；Shield 的具体编写方法与自检清单见 Shield.md，本 FR 锁定 opencode 接入、deliverables 一致性与写范围边界

## FR-0130 ID 文法与跨版本引用进模板

### AC-FR0130-01

  - story/spec/acceptance 三模板增补 ID 文法与跨版本引用规则：`BS-XX`（两位，按核心操作路径顺序统一编号）、`FR-XXXX`/`NFR-XXXX`（四位补零）、`AC-FRXXXX-YY`（AC + FR 四位编号 + FR 内两位序号，YY 从 01 起，跨单元不复用）

### AC-FR0130-02

  - ID 一经分配不可变、不可复用；删除的 ID 留 tombstone（事件日志与历史证据会引用旧 ID）；tombstone 不被 trace 计为孤儿（FR-0080）

### AC-FR0130-03

  - 跨版本限定引用 `AC-FRXXXX-YY@<version>`（如 `AC-FR0010-01@v0.1`）：限定语法 opt-in--仅在歧义时使用，parser 不强制、不把短格式自动升级为限定形式；限定引用只信任目标版本文档，解析失败显式报错（NOT_FOUND），不静默回退到短格式

### AC-FR0130-04

  - 文档与测试的约束不对称：文档中短格式与长格式（带版本号）均允许，短格式 opt-in 消歧；测试文件中 marker 必须使用长格式（FR-0080 强制）

### AC-FR0130-05

  - `trac validate` 可校验编号文法与跨版本引用：不合文法的编号、解析失败的限定引用判失败并指出位置

### AC-FR0130-06

  - 既有文法不被重复实现（避免下游误判从零设计）：`trac validate --file spec.md` 仍按既有 `check_spec_items` 强制 `FR-XXXX`/`NFR-XXXX` 文法（严格标题、唯一 ID、来源/交付入口字段），`trac validate --file acceptance.md` 仍识别 `AC-FRXXXX-YY` 文法；本次新增的是 story `BS-XX` 强制文法、三模板 `@version` 跨版本引用与 tombstone 规则、marker 长格式强制，既有校验行为不回归

## FR-0140 test-plan 变绿条件字段与 design-trace IF- 校验

### AC-FR0140-01

  - test-plan.md 模板增补变绿条件字段：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分

### AC-FR0140-02

  - design-trace validator（v0.3 既有 `check_design_trace`，AC -> test-layer 归属校验）扩展：校验每条 integration/e2e 的 IF- 归属；缺 IF- 归属的 integration/e2e AC 判失败并指出位置

### AC-FR0140-03

  - `trac validate --file test-plan.md` 校验变绿条件字段与 IF- 归属：缺字段或 IF- 归属不合法时 validate 失败（非零退出）

### AC-FR0140-04

  - 绿的粒度（约束 M-IMPL 设计，本 release 只需 test-plan 携带变绿条件字段，不实现变绿执行）：task 级 GREEN_GATE = 单测 + 该 task 的 int 子集（变绿条件 = 所依赖接口的 IF- 归属）；Devon 第一轮不跑 e2e；全量 integration+e2e 变绿是 M-IMPL 出口门禁（Out-of-Scope，本 release 不实现变绿执行）

## NFR-0010 trace/reach 工具只报告不改写

### AC-NFR0010-01

  - `trac check trace` 与 `trac check reach` 任何检查运行前后，被检查项目的文件内容无任何变化（git 工作区无由工具产生的改动）

### AC-NFR0010-02

  - 工具只报告，不改写：发现问题时列出证据与位置，修复由作者完成；不做自动修复/自动重编号

## NFR-0020 trace/reach 输出确定性与幂等

### AC-NFR0020-01

  - 同一输入多次运行 `trac check trace` / `trac check reach`，输出完全一致（字节级）

### AC-NFR0020-02

  - 无随机顺序、无时间戳、无环境依赖的输出漂移；孤儿/孤岛清单的列出顺序稳定可复现

## NFR-0030 M-TEST 控制流维持 kernel 纯函数边界

### AC-NFR0030-01

  - M-TEST 子状态机的显式控制流分支（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT/DIAGNOSE）在 `decide()`/`project()` 内仍为纯函数：不碰 IO、不读 clock/env、不读文件系统

### AC-NFR0030-02

  - 所有非确定性只作为事件进入：collection 复跑、trace 调用、判据包加载等副作用归 executor，不归 kernel（承自 v0.1 NFR-02）；drop 投影表后从事件重建的 M-TEST 状态与原状态一致

## NFR-0040 M-TEST 事件维持 append-only 事件溯源

### AC-NFR0040-01

  - M-TEST 全程事件（`stage.entered` / `command.issued` / `outcome.received` / `test.collected` / `prism.verdict` / `red.validated` / `verdict.failed` / `test.committed` / `stage.exited` / `stage.rolled_back` / `review.round_started`）均 append-only 写入 events 表，不改写既有行

### AC-NFR0040-02

  - 投影表可从事件完整重建（承自 D-02）：drop 投影表后 `trac status`/`trac report` 重建的 M-TEST 状态与活动记录与原一致
