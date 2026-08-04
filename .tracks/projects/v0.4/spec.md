---
spec_id: SPEC-004
created: 2026-08-05
status: draft
sha:
---

# M-TEST 阶段与需求追踪工具 - 需求规格

## 界面与入口

### E-01 `trac check` 子命令（trace / reach）

```
$ trac check trace
line:12 FR-0010 acceptance section has no AC item for it
line:45 AC-FR0020-01 has no test marker bound
warning: BS-03 has no FR承接 (BS->FR weak link)
$ echo $?
1

$ trac check trace --json
{"status":"fail","hard_errors":["line:12 ...","line:45 ..."],"warnings":["BS-03 ..."]}

$ trac check reach
island module: src/legacy/orphan.py
$ trac check reach
reach ok
$ echo $?
0
```

## 状态与生命周期

### SM-01 M-TEST 子状态机

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-01.7）。

1. stage.entered(M-TEST) -> DISPATCH：M-DESIGN EXIT（prism.verdict(pass) + 程序校验通过）触发
2. DISPATCH -> WRITE：Runtime 按 test-plan 层归属与变绿条件创建 Shield tasks 就绪
3. WRITE -> COLLECT：Shield outcome（测试文件写入宿主 tests/）
4. WRITE -> WRITE：validate 失败重派 Shield（<=3，第 3 次升级 awaiting_human/escalation）
5. COLLECT -> PRISM_REVIEW：collection 成功（test.collected(passed)）
6. COLLECT -> WRITE：collection 失败回 WRITE 重派 Shield（test.collected(failed)）
7. PRISM_REVIEW -> RED_CHECK：prism.verdict(pass)（携带判据包 identity，Runtime 回读匹配）
8. PRISM_REVIEW -> WRITE：prism.verdict(revise)（经 trac discuss 锚定线程）
9. RED_CHECK -> EXIT：全部失败为合法 Red（red.validated(valid)）
10. RED_CHECK -> DIAGNOSE：非法 Red 或意外通过（red.validated(invalid)）
11. DIAGNOSE -> WRITE：测试缺陷 -> Shield 重派
12. DIAGNOSE -> stage.rolled_back(M-DESIGN)：桩/接口缺口（Archer+Prism 裁定，不需 Human）
13. DIAGNOSE -> stage.rolled_back(M-ACC/M-SPEC)：AC/Spec 缺口（Human 批准后回退）
14. EXIT -> stage.exited(M-TEST) -> run.completed(terminal_state="boundary")：trace 闭合 + 测试资产冻结

## 角色与权限

### RP-01 M-TEST 写范围与执行权

单写者纪律：任一时刻只有一个 actor 持有编辑权；Shield/Prism 不 commit/push、不推进状态（Runtime 是唯一流程 authority）。

| #   | 操作                                        | Shield | Prism | Runtime | Human |
| --- | ------------------------------------------- | ------ | ----- | ------- | ----- |
| 1   | 写 tests/integration、tests/e2e、tests/assets、tests/counterexamples | ✅      | ❌     | ✅（受控 commit） | ❌     |
| 2   | 写产品代码 / 接口桩 / tests/ground_truth / 需求设计文档 | ❌      | ❌     | ✅（commit artifact） | ✅（评审期改文档） |
| 3   | 独立执行 collection / RED_CHECK 复跑 / trace 闭合 | ❌      | ❌     | ✅       | ❌     |
| 4   | 评审测试合约（findings 经 trac discuss）    | ❌      | ✅     | ❌       | ❌     |
| 5   | commit / push / 推进阶段                    | ❌      | ❌     | ✅       | ❌     |
| 6   | M-TEST 退出门禁（裁定退出）                 | ❌      | ❌     | ✅（程序证据） | ❌（无 Human 门禁） |
| 7   | 回退 M-DESIGN（桩/接口缺口）                | ❌      | ❌     | ✅（Archer+Prism 裁定） | ❌     |
| 8   | 回退 M-ACC/M-SPEC（AC/Spec 缺口）           | ❌      | ❌     | ✅（展示影响） | ✅（批准需求回退） |

## 功能需求

### FR-0010 M-TEST 阶段注册与子状态机驱动

- **来源**：`BS-01` / `BS-06` / `§3.1`
- **交付入口**：`trac run`

machine.py 注册 M-TEST 为 StageDef，子状态机按 SM-01（DISPATCH -> WRITE -> COLLECT -> PRISM_REVIEW -> RED_CHECK -> EXIT / DIAGNOSE）。executor `_NEXT_STAGE` 增补 `M-DESIGN -> M-TEST` 与 `M-TEST -> M-IMPL`。

进入条件（SM-01.1）：M-DESIGN EXIT（`prism.verdict(pass)` + 程序校验通过）触发 `stage.entered(M-TEST)` 进入 DISPATCH。边界退出（SM-01.14）：M-TEST EXIT 完成后发出 `stage.exited(M-TEST)`；因 M-IMPL 未注册，`run.completed(terminal_state="boundary")` 停在 M-TEST->M-IMPL 边界，不进入 M-IMPL。

M-TEST 子状态机与既有 DRAFT/REVIEW/EXIT 模式差异较大，`decide()` 为 M-TEST 增加显式控制流分支（类似 v0.3 的 `_decide_approval`），不能仅靠 StageDef 注册驱动；该控制流仍维持 kernel 纯函数边界（NFR-0030）。既有 M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL / M-DESIGN 阶段行为不变（承自 v0.3 BS-07 回归安全：泛化是接入不是改既有行为）。

### FR-0020 M-TEST WRITE：Shield 任务派发与可 collect 测试资产产出

- **来源**：`BS-02` / `§3.1` / `§3.3`
- **交付入口**：`trac run`

DISPATCH（SM-01.2）：Runtime 按 test-plan 的层归属（integration/e2e）与变绿条件（IF- 标识，FR-0140）创建 Shield tasks；tasks 就绪后进入 WRITE。

WRITE（SM-01.3/.4）：dispatch Shield（FR-0120，与 Archer/Prism 同构的 opencode agent）对着接口桩编写 integration（happy + 关键错误/边界路径）与 e2e（仅 happy path）测试，写入宿主项目 `tests/integration/`、`tests/e2e/`、`tests/assets/`、`tests/counterexamples/`。Shield outcome 后，系统在宿主项目 `tests/integration/` 与 `tests/e2e/` 下存在可 collect 且全部合法失败的测试文件。validate 失败重派 Shield（<=3，第 3 次升级 `awaiting_human`/escalation）。

Shield 不 commit/push、不判定 PASS、不修改产品代码与接口桩（单写者纪律，RP-01）。Shield 的本地自检（collection 通过 + 合法 Red + counterexample killed）是其 outcome 前自检，不构成 Runtime 退出依据（永不信自述，flow.md 不变量 6）。

### FR-0030 M-TEST COLLECT：Runtime 独立 collection

- **来源**：`BS-02` / `§3.1` / flow.md §9.1
- **交付入口**：`trac run`

COLLECT（SM-01.5/.6）：Runtime 独立执行 collection/import，不信 Shield 自述；全部测试必须可 collect（接口桩保证可 import）。collection 成功 -> PRISM_REVIEW（`test.collected(passed)`）；collection 失败 -> 回 WRITE 重派 Shield（`test.collected(failed)`）。

### FR-0040 M-TEST PRISM_REVIEW：判据包绑定与反自述回读

- **来源**：`BS-14` / `§3.1` / `D-29`
- **交付入口**：`trac run`

PRISM_REVIEW（SM-01.7/.8）：dispatch Prism 按 assignment 指定的测试资产判据包审测试合约（忠于 AC + 断言落在公开出口 + counterexample 绑定 + 无伪测试）。测试资产判据包（D-29：M-TEST 的 PRISM_REVIEW 子状态消费）物化为 skill，由 Prism 按 assignment 加载；其物化与回收与既有 skill（tracks-discuz）同构。

反自述三件套（D-29，必须交付）：① assignment 写明应加载判据包的名称+版本（Runtime 决定，Prism 不自选）；② Prism verdict outcome 携带实际加载判据包 identity；③ Runtime 回读核对--不匹配判失败重派 Prism。

`prism.verdict(pass)` -> RED_CHECK；`prism.verdict(revise)` -> 回 WRITE 重派 Shield；revise 必须经 `trac discuss` 锚定线程（承自 v0.3 Prism 评审协议：revise 无锚定线程判 `revise_without_findings` failure）。形式校验不混入判据包（D-14 边界：形式校验归 Runtime，语义判据归判据包）。

### FR-0050 M-TEST RED_CHECK：合法 Red 分类

- **来源**：`BS-03` / `§3.1` / flow.md §9.3
- **交付入口**：`trac run`

RED_CHECK（SM-01.9/.10）：Runtime 独立复跑 integration/e2e（不信 Shield 自述），将每条失败分类为合法 Red 或非法 Red。

- 合法 Red：行为断言失败 / 桩合同 token 失败（`NotImplementedError("IF-...")`）/ 合同声明的 symbol 缺失。
- 非法 Red：collection/语法/fixture/import 错误。
- 测试意外通过视为非法（桩只 raise，通过通常意味测试未真正命中桩：断言空洞、mock 掉被测对象、或测了别的东西）。

全部合法 Red -> EXIT（`red.validated(valid)`）；存在非法 Red 或意外通过 -> DIAGNOSE（`red.validated(invalid)`）。

非常规要求：本阶段接受并要求 Red--每条测试必须失败且失败必须合法，与常规"测试应通过"相反，因为本阶段交付的是测试资产而非绿色结果（变绿是 M-IMPL 职责，不在本 release）。

### FR-0060 M-TEST DIAGNOSE：四路诊断路由

- **来源**：`BS-13` / `§3.1` / flow.md §9.1
- **交付入口**：`trac run`

DIAGNOSE（SM-01.11/.12/.13）：RED_CHECK 遇非法 Red 或意外通过时进入。"测试错还是接口错"的分流永不交给 Human；需语义判断时分派 Prism diagnostic review。四路路由：

1. 测试缺陷 -> 回 WRITE 重派 Shield。
2. 桩或接口缺口 -> M-DESIGN（gap advisory，Archer+Prism 裁定，不需 Human）。
3. AC 缺口 -> M-ACC（Runtime 展示影响，Human 批准后回 M-ACC）。
4. Spec 缺口 -> M-SPEC（Runtime 展示影响，Human 批准后回 M-SPEC）。

`verdict.failed(test_defect|stub_gap|ac_gap|spec_gap)` 事件携带 classification、target stage 与 artifact disposition。M-IMPL 侧的 SHIELD_FIX / DIAGNOSE 路由仅预留事件与路由定义，不实现（Out-of-Scope）。

### FR-0070 M-TEST EXIT：trace 闭合门禁与边界退出

- **来源**：`BS-04` / `BS-05` / `BS-06` / `§3.1` / flow.md §9.3
- **交付入口**：`trac run`

EXIT（SM-01.14）：M-TEST 退出门禁 = collection 成功 + 合法 Red + Prism verdict(pass) + `trac check trace` 闭合（FR-0080），全部由 Runtime 复跑取得（不信 Shield 自述）。

trace 闭合要求：每条 required AC（integration|e2e 层归属）至少一条长格式 marker `AC-FRXXXX-YY@<version>` 绑定、无无主 marker。`trac check trace` 统一检查所有 AC（不感知调用方阶段），M-TEST 退出门禁自行过滤只看 required AC（integration|e2e 层 AC）--依据 §1 需求描述 Part B 与 flow.md §9.3。trace 不闭合不得退出。

M-TEST 无 Human 门禁（BS-05）：不等待 `human.review` 或 `human.approval`，退出依据全部是程序证据。退出时 Runtime 创建受控测试 commit 冻结测试资产，发出 `stage.exited(M-TEST)`；因 M-IMPL 未注册，`run.completed(terminal_state="boundary")` 停在 M-TEST->M-IMPL 边界。collection / Red 分类 / trace / 审查证据全部落事件。

### FR-0080 trac check trace：双向孤儿检测

- **来源**：`BS-07` / `BS-11` / `§3.2` / story-S002-draft
- **交付入口**：`trac check trace`

`trac check trace` CLI 工具：解析 story/spec/acceptance 三文档 + 测试 marker，对 BS -> FR -> AC -> test 全链做双向孤儿检测。

- FR<->AC 与 AC<->test 为硬错误（非零退出）：存在无 AC 的 FR、回指不存在 FR 的 AC、无测试绑定的 AC、marker 指向不存在 AC 的测试函数均判失败，并列出完整孤儿清单（含文件与行号，不短路）。
- BS->FR 仅 warning（不改变退出码，warning 内容含 BS 编号）--行为种子与 FR 不总是 1:1，硬约束会逼人写凑数 FR。

测试 marker 必须使用长格式 `AC-FRXXXX-YY@<version>`（代码不按版本分目录，所有版本测试共存于同一棵 tests/ 树，缺版本号无法定位 AC 所属版本）：短格式 marker（缺版本号）触发 trace 检查失败并指出位置；长格式 marker 引用不存在的 AC 显式报错（NOT_FOUND），不静默回退到同名 AC。

ID 唯一性：文档中出现重复 FR/AC 编号时 trace 失败并指出冲突双方的文件与行号；tombstone ID 不被计为孤儿（FR-0130）。

trace 统一检查所有 AC，不感知调用方阶段；M-TEST 退出门禁自行过滤 required AC（FR-0070）。工具可独立 CLI 运行，也可被引擎在 M-TEST EXIT 当 verdict 来源调用。

### FR-0090 trac check reach：模块级孤岛检测

- **来源**：`BS-08` / `§3.2` / story-S002-draft
- **交付入口**：`trac check reach`

`trac check reach` CLI 工具：从声明入口点（pyproject `[project.scripts]` / `__main__` / 显式白名单）构建模块级 import 图，报告从任何入口都不可达的生产模块（孤岛）。

- 纯测试模块（`tests/` 下或仅含测试的模块）不计入，不产生误报。
- 无任何入口声明时报错而非静默通过（避免空通过）。
- 本版只做模块级 import 图，不做函数级调用图（Out-of-Scope）。

工具可独立 CLI 运行，也可被引擎当 verdict 来源调用（M-IMPL ISLAND_GATE 孤岛闭合与 M-VERIFY 反 slop 门禁消费，flow.md §10/§11）。本 release 交付工具本体，消费随后续阶段接入。

### FR-0100 存量基线豁免

- **来源**：`BS-09` / `§3.2` / story-S002-draft / Human 裁定
- **交付入口**：`trac check trace` / `trac check reach`（读取 `.tracks/` 下的存量基线声明）

宿主项目携带存量文档/编号/模块时，trace 与 reach 两个检查都提供兼容能力，机制是同一份存量基线（legacy baseline）豁免清单：在采纳 tracks 时（`trac init` adoption）声明哪些既有文档/编号豁免 trace 的 ID 纪律、哪些既有模块豁免 reach 的孤岛检测。

- 基线以 `.tracks/` 下的声明文件维护，列出豁免的文档/AC 标识与模块路径；不强制重编号、不强制补链路（tracks 自身的 v0.1 即为存量样本）。
- 基线只冻结采纳时刻的存量、不回填历史（承自 louke STR-1410 cutover baseline / STR-1411 legacy baseline）；基线之后新增的同形内容仍正常报错。
- 被列入基线的文档/编号 trace 不计为孤儿；被列入基线的模块 reach 不计为孤岛。

基线声明的具体存储 schema（字段命名、文件格式）属设计层，由 Archer 依据 `.tracks/` 既有目录约定裁定；本 FR 锁定豁免语义与作用范围。

### FR-0110 双格式输出与稳定退出码

- **来源**：`§3.2` / story-S002-draft
- **交付入口**：`trac check trace` / `trac check reach`

trace 与 reach 两个工具均支持人类可读（默认）与 `--json` 双格式输出。退出码语义稳定：0=通过（无硬错误，可有 warning）、非 0=有硬错误。

CLI 与引擎双消费者：人类经默认格式读证据；引擎调用读取 `--json` 与退出码作为 verdict 来源（FR-0070 M-TEST EXIT 消费 trace）。

### FR-0120 Shield opencode agent 接入与写范围审计

- **来源**：`BS-10` / `§3.3` / Shield.md
- **交付入口**：`trac run`（M-TEST WRITE/RESPOND 派发 Shield）

Shield 作为真实 opencode agent 接入（承自 v0.3 Archer/Prism 接入模式）：

- `AGENT_NAME` 增补 `shield -> Shield`；Shield prompt/skill（tracks-discuz）物化与回收与 Archer/Prism 同构。
- Shield.md 加入 deliverables 一致性集合（`check_deliverables` 校验其 version + IQ frontmatter），与既有五个 agent 提示词同构。

写范围审计：Shield 仅可写 `tests/integration/`、`tests/e2e/`、`tests/assets/`、`tests/counterexamples/`；越权写文件被审计检出并通过 git 回滚（`over_reach` failure_class，承自 v0.3 写范围审计机制）。Shield 不写产品代码、接口桩、`tests/ground_truth/`、需求/设计文档。

Shield 读 test-plan / interfaces / acceptance + 接口桩 -> 写测试 -> 本地自检（collection 通过 + 合法 Red + counterexample killed）-> 返回 outcome。Shield 的具体编写方法与自检清单见 Shield.md；本 FR 锁定 opencode 接入、deliverables 一致性与写范围边界。

### FR-0130 ID 文法与跨版本引用进模板

- **来源**：`BS-11` / `§3.4` / story-S002-draft
- **交付入口**：`trac validate`（story/spec/acceptance 模板校验）+ 模板（`tracks/templates/`）

story/spec/acceptance 三模板增补 ID 文法与跨版本引用规则。ID 文法沿用 louke：

- `BS-XX`（两位，按核心操作路径顺序统一编号，不按路径分组）。
- `FR-XXXX` / `NFR-XXXX`（四位补零；初稿按 100 间隔预留，首轮评审后按 10 间隔插入，二轮评审后连续编号）。
- `AC-FRXXXX-YY`（全称 = AC + FR 四位编号 + FR 内两位序号；YY 从 01 起，跨单元不复用）。

ID 一经分配不可变、不可复用；删除的 ID 留 tombstone（事件日志与历史证据会引用旧 ID），tombstone 不被 trace 计为孤儿（FR-0080）。

跨版本限定引用：本版本内引用保持短格式 `AC-FRXXXX-YY`；跨版本引用且存在歧义时附加版本限定 `AC-FRXXXX-YY@<version>`（如 `AC-FR0010-01@v0.1`）。限定语法 opt-in--仅在歧义时使用，parser 不强制、不把短格式自动升级为限定形式；限定引用只信任目标版本文档，解析失败显式报错（NOT_FOUND），不静默回退到短格式。

文档与测试的约束不对称：文档中短格式与长格式（带版本号）均允许，短格式 opt-in 消歧；测试文件中 marker 必须使用长格式（FR-0080 强制）。

既有事实（避免下游重复实现）：spec 模板已含 `FR-XXXX`/`NFR-XXXX` 文法并由 `check_spec_items` 强制（严格标题、唯一 ID、来源/交付入口字段）；acceptance 模板已含 `AC-FRXXXX-YY` 文法指引。本次补的缺口：story 模板无 `BS-XX` 强制文法（仅编号约定、无机器校验与不可变/tombstone 规则）、三模板均无 `@version` 跨版本引用与 tombstone 规则、marker 长格式未强制。`trac validate` 可校验编号文法与跨版本引用。

### FR-0140 test-plan 变绿条件字段与 design-trace IF- 校验

- **来源**：`BS-12` / `§3.4` / flow.md §10 GREEN_GATE / Aaron 绿粒度裁定
- **交付入口**：`trac validate --file test-plan.md` + test-plan.md 模板

test-plan.md 模板增补变绿条件字段：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。

design-trace validator（v0.3 既有 `check_design_trace`，AC -> test-layer 归属校验）扩展：校验每条 integration/e2e 的 IF- 归属。

绿的粒度（约束 M-IMPL 设计，本 release 只需 test-plan 携带变绿条件字段，不实现变绿执行）：task 级 GREEN_GATE = 单测 + 该 task 的 int 子集（变绿条件 = 所依赖接口的 IF- 归属）；Devon 第一轮不跑 e2e；全量 integration+e2e 变绿是 M-IMPL 出口门禁（Out-of-Scope）。`trac validate --file test-plan.md` 校验变绿条件字段与 IF- 归属。

## 非功能需求

### NFR-0010 trace/reach 工具只报告不改写

- **来源**：`§3.2` / story-S002-draft / §5 约束

`trac check trace` 与 `trac check reach` 任何检查运行前后，被检查项目的文件内容无任何变化。工具只报告，不改写；发现问题时列出证据与位置，修复由作者完成。

### NFR-0020 trace/reach 输出确定性与幂等

- **来源**：`§3.2` / story-S002-draft

同一输入多次运行 `trac check trace` / `trac check reach`，输出完全一致（字节级）。无随机顺序、无时间戳、无环境依赖的输出漂移；孤儿/孤岛清单的列出顺序稳定可复现。

### NFR-0030 M-TEST 控制流维持 kernel 纯函数边界

- **来源**：`§5 约束` / `§7 风险` / v0.3 BS-01

M-TEST 子状态机的显式控制流分支（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT/DIAGNOSE）在 `decide()`/`project()` 内仍为纯函数：不碰 IO、不读 clock/env、不读文件系统；所有非确定性只作为事件进入。collection 复跑、trace 调用、判据包加载等副作用归 executor，不归 kernel（承自 v0.1 NFR-02）。

### NFR-0040 M-TEST 事件维持 append-only 事件溯源

- **来源**：`§5 约束` / `D-02`

M-TEST 全程事件（`stage.entered` / `command.issued` / `outcome.received` / `test.collected` / `prism.verdict` / `red.validated` / `verdict.failed` / `test.committed` / `stage.exited` / `stage.rolled_back` / `review.round_started`）均 append-only 写入 events 表，不改写既有行；投影表可从事件完整重建（承自 D-02）。

## 范围排除

- 不实现 M-IMPL（Devon / RGR / task graph），只停在 M-TEST -> M-IMPL 边界（FR-0070）。
- M-IMPL 侧的 SHIELD_FIX / DIAGNOSE 路由仅预留事件与路由定义，不实现（FR-0060 仅覆盖 M-TEST DIAGNOSE）。
- 不做 `trac check ratio / dup / budget`（后续 story）；不做注册表与语义层去重（Agent 评审职责，非本版工具）。
- 不做函数级调用图（本版 reach 只做模块级 import 图，FR-0090）。
- 不做自动修复 / 自动重编号（工具只报告，NFR-0010）。
- 不接真实 LLM Agent（延续 v0.1-v0.3 排除，FakeAgent 验证）。
- 绿的粒度的执行（task 级 GREEN_GATE 变绿子集运行）不在本 release；本 release 只需 test-plan 携带变绿条件字段（FR-0140），变绿是 M-IMPL 出口门禁。
- reach 的下游消费（M-IMPL ISLAND_GATE、M-VERIFY 反 slop 门禁）不在本 release；本 release 交付工具本体（FR-0090）。
