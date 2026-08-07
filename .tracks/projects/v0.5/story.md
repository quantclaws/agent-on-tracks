---
story_id: S-005
title: 推进流程到 M-IMPL 阶段（Devon 逐 task RGR）
created: 2026-08-05
status: draft
sha:
---

# S-005: 推进流程到 M-IMPL 阶段（Devon 逐 task RGR）

## 1. 原始输入

> 规划 v0.5，进入 M-IMPL 阶段。
>
> -- Aaron，2026-08-05（由规划助手按既有 flow.md §10 与 v0.1–v0.4 交付现状展开为本草稿，等待 M-STORY 裁定）

> 特别要求，Devon 不得看 Archer 确定的 integration 和 end to end 测试目录，以防他作弊。这在技术上应该能够实现？
>
> -- Aaron，2026-08-05（Human 裁定：Devon 对 Shield 测试目录不可见为 v0.5 强制要求——测试实为 Shield 在 M-TEST 产出。后续澄清：屏蔽路径集**不得硬编码**为 `tests/integration` / `tests/e2e`——不同语言/框架的 integration/e2e 目录惯例不同，屏蔽集由 Archer 设计产物按宿主惯例声明，见 §3.1 视图隔离条款）

> 特别要求：tracks 为宿主项目生成质量守卫框架时（Archer 在 architecture.md 选定守卫、写安装命令、声明 Scaffold 宣言中的守卫配置文件），对**测试代码**的守卫策略为——只做超长文件长度（pylint C0302 或等价物）+ 重复代码检测（pylint R0801 或等价物），**不做**认知复杂度（flake8 CCR001 或等价物）+ 超长函数/方法检测（pylint R0915/R0914 或等价物）。tracks 自身的 pre-commit hook 已按此策略修正（commit `cdef312`），Archer 生成的宿主守卫配置须遵循同一策略。承 tracks-quality-guards skill。
>
> -- Aaron，2026-08-06

> 特别要求：v0.5 启动前，需要有监督地重录一次完整 live run（M-START -> M-TEST），然后通过日志分析找出 runtime dispatch 中的不合理点--v0.4 修复的 `_prompt()` 路径未具体化问题是典型案例（Runtime 本可传绝对路径却让 Agent 自行搜索），疑似还存在其他类似的可优化点。目标是让 Runtime 尽可能物化上下文（路径、文件名、范围），减少 Agent 的盲目搜索。分析结论作为 v0.5 的输入。
>
> -- Aaron，2026-08-06

> **Scribe [RESOLVED]:** §1 第四条原始输入（live run 重录 + dispatch 物化分析作为 v0.5 输入）逐字保留，但 §2/§3/§4/§6 均未承接其范围归属。三种可能：(A) 它是 v0.5 范围内的前置工作项（live-run 分析产出 → 据此在 v0.5 设计 Runtime dispatch 物化上下文，减少 Agent 盲目搜索），应在 §4 增列工作项并影响 §3 路径；(B) 它是 v0.5 的上游输入（分析在 v0.5 story 之外先行完成，结论喂给 v0.5），story 只记录'已消费'，不需展开为工作项；(C) 它是独立于 M-IMPL 主线的并行 story，本 S-005 不覆盖。请裁定属于哪种，我据此在 §2/§4 落地对应范围声明。当前 story 对此输入只有 §1 原文、无下游承接，是 §1→§2 traceability 的阻塞性缺口。
>> **Aaron:** 你说的§2/§3/§4/§6是什么？
>>> **Scribe:** 我指的是当前 story 文档自己的章节号：§2=用户意图，§3=需求描述（含 3.2 行为种子），§4=工作项，§6=范围、约束与例外。我原话的意思是：§1 第四条原始输入（有监督地重录一次完整 live run M-START→M-TEST + dispatch 物化分析，作为 v0.5 输入）目前只在 §1 原文出现，没有在任何下游章节承接其范围归属——即这三种皆未落地：(A) 作为 v0.5 内的前置工作项在 §4 增列并影响 §3；(B) 作为上游输入在 story 外先完成、story 只记\u0027已消费\u0027；(C) 独立 story 不覆盖。这是我发起此线程请裁定的问题，请给出 A/B/C。
>>>> **Aaron:** A -- 必须完成这一步，不然 end to end 测试运行起来会很不顺利，甚至失败。

## 2. 用户意图

- 将 tracks 流程从 M-TEST 出口推进到 M-IMPL 阶段：`trac run` 能驱动 M-IMPL 完整循环（BASELINE 重算 → Archer 拆 task graph → ISLAND_GATE_1 → Prism 评审切片 → Devon 逐 task Red→Green→Refactor → Prism Red checkpoint 与最终 range 评审 → ISLAND_GATE_2 出口），停在 M-IMPL → M-VERIFY 边界
- 落地"大小两个测试循环"的内圈（D-32）：Devon 逐 task 走 RGR，用内圈小循环驱动外圈合同循环（Shield 的 integration/e2e，v0.4 已冻结为 baseline）变绿；全量 integration + e2e 变绿是 M-IMPL 出口门禁
- Red 测试先于实现在 v0.5 第一次成为**程序可验证的 lineage 事实**（B/R/G commit 拓扑），而不是 Agent 自报
- 当前受阻：M-IMPL 未注册（executor `_NEXT_STAGE` 止于 M-TEST、boundary 收尾）；`tracks/agents/` 无 Devon.md；task graph 模板（task-plan.md / task-log.md）存在但未接入 runtime；无 RGR checkpoint 机制、无 per-task manifest、无 writelock 事件；Runtime dispatch 物化不完整（v0.4 `_prompt()` 路径未具体化等案例，Agent 需盲目搜索）
- 完成后：`trac run` 从 M-TEST 出口进入 M-IMPL 并跑通完整循环；task graph / RGR lineage / 门禁证据全部落事件；end-to-end 真实 Devon 通道贯通；M-VERIFY 的实现不在本 release 范围

## 3. 需求描述（规划层，待 M-STORY 展开）

### 3.1. M-IMPL 阶段生命周期（flow.md §10.1，共 21 子状态）

按 flow.md §10.1 子状态机实现，分四簇：

**规划簇**（BASELINE → PLANNING → ISLAND_GATE_1 → PRISM_PLAN）：

1. BASELINE：Runtime 重算 baseline——三件套 + 设计三文档 + 冻结测试资产 digest + contracts + Issues + branch + approval（flow §10.1 baseline 输入全集；Issues 只消费需求追踪身份，task-plan↔Issues 新增同步不在本版，见 §6）。**测试资产清单（屏蔽路径集）**随 digest 一并冻结：test-plan.md 层归属字段（承 v0.4）声明哪些路径属 integration / e2e，哪些属 unit——屏蔽路径集由 Archer 按宿主语言/框架惯例填写（Python 的 `tests/integration` 与 Java 的 `src/it`、Go 的 build-tag 目录同等合法），隔离机制本身语言无关。清单缺层归属声明 → 硬错误，不静默放行。缺失/stale/冲突 → NEEDS_ATTENTION 等待 reconcile 或 return upstream
1a. NEEDS_ATTENTION：等待 reconcile 或 return upstream（flow §17 通用返回规则）；reconciled → 回 BASELINE；rolled_back → stage.rolled_back 终结
2. PLANNING：dispatch Archer 拆 task graph（`task-plan.md` 模板已就位）；纵向切片 + 每 task 声明 scope 白名单 + 实现的 IF- 集合 + 预算；validate 校验 DAG 无环 / scope 不重叠 / AC 覆盖闭合；fail 重派 Archer（≤3）
3. ISLAND_GATE_1：程序复核——每条 required AC 六项检查（owner/surface/composition/wiring/test/evidence）；六元组是 Archer 设计期义务、M-DESIGN 已由 Prism 逐项闭合，本门禁是该合同的复核而非首次建立（D-21）；不闭合 verdict.failed(island) 回 PLANNING
4. PRISM_PLAN：dispatch Prism 评审切片；pass → TASK_DISPATCH；revise → Archer；设计缺口 → M-DESIGN；需求缺口 → M-SPEC/M-ACC（Human 确认）

**RGR 循环簇**（TASK_DISPATCH → RED → RED_GATE → RED_CHECKPOINT → PRISM_RED → GREEN → GREEN_GATE → GREEN_COMMIT → REFACTOR → REFACTOR_GATE → TASK_REVIEW → PRISM_FINAL → TASK_DONE）：

5. TASK_DISPATCH：Runtime 按 DAG 依赖调度 ready task（v0.5 串行调度，`[P]` 标记记录但不并发执行——见 §5 Q-01）；单写者 lease + 创建 manifest（writelock.granted）
6. RED：**视图隔离** dispatch Devon（phase=red）——Runtime 物化隔离工作视图（worktree + sparse-checkout，按 BASELINE 冻结的屏蔽路径集排除 Shield 测试目录；unit 路径保留），Devon 只添加 unit test；视图里不存在的东西物理上不可读——"禁止"兑现为"不可能"。Devon 权限条目 `bash: deny`（worktree 共享 `.git`，有 shell 即可自行 checkout 被排除路径，隔离即失效；测试运行与门禁全是 Runtime 职责），该条目由 `trac init` 写入 harness 配置（D-19），Devon.md 保持 harness 无关、不含 permission 块（D-26）。outcome 必须是 test-only diff
7. RED_GATE：Runtime 校验预期失败——合法红 = 行为断言失败 / symbol 缺失（flow §10.1 口径；复用 v0.4 RED_GATE 分类器框架，但其桩合同 token 失败条款属 M-TEST 合同测试场景，不在本门禁之列）；非法红重派
8. RED_CHECKPOINT：Runtime 创建私有 commit R，写 git ref `refs/trac/rgr/{run}/{task}/{attempt}/red`（B/R 拓扑中 R 先于 G）；red.checkpointed
9. PRISM_RED：dispatch Prism 评 B..R 范围（Red 测试确实测了该 task 声明的 IF/AC，且未测多余）；pass 绑定 R → GREEN
10. GREEN：从 R tree 恢复工作区（仍为隔离视图）；dispatch Devon（phase=green）——最小实现，R 测试不可改；完成后受控 diff 按 manifest 回灌主仓，视图终态清理 + 崩溃 reconcile（承 v0.2 物化合同）
11. GREEN_GATE：targeted 单测 + 全部历史单测 + test-plan 变绿条件命中本 task IF 集合的 int 子集 + lint/format/type/static + 合同；**第一轮不跑 e2e**（v0.4 既定粒度）；**反馈脱敏**——单测失败回完整输出，int/e2e 失败只回分类诊断（哪条 IF 契约失败 / symbol 缺失类型），不回断言原文：隔离不能被测试输出侧信道打穿。int 失败归因不明 → DIAGNOSE
12. GREEN_COMMIT：Runtime 创建正式 commit G（parent=B），trailers 记录 R/task/attempt identity（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）
13. REFACTOR：dispatch Devon（phase=refactor，仍为隔离视图）——可返回 no-change + 理由；质量门禁在此跑（Q-03 已裁定为 A = tracks-quality-guards 分层执行，遵循 §1 测试代码强制策略与 T-002）：**生产代码**执行完整四段 ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914；**测试代码**仅执行重复 R0801 + 文件长度 C0302（或宿主等价守卫），不套用 CCR001/R0915/R0914——宿主守卫配置与 Runtime 门禁继承同一分层政策）
14. REFACTOR_GATE：重跑 GREEN_GATE 全部检查；动 public interface → upstream
15. TASK_REVIEW：Runtime 校验 task range——write scope / secret / AC trace / B-R-G(-Refactor) lineage / budget
16. PRISM_FINAL：dispatch Prism 评完整 range + lineage；revise（实现）→ GREEN、revise（Red 测试）→ RED 新 lineage
17. TASK_DONE：task.completed → TASK_DISPATCH（还有 ready task）或 ISLAND_GATE_2（全部完成）

**诊断簇**（DIAGNOSE → SHIELD_FIX）：

18. DIAGNOSE：dispatch Prism 做四路诊断——实现缺陷 → Devon；测试缺陷 → Shield；接口/架构不足 → M-DESIGN；AC/Spec 缺口 → M-ACC/M-SPEC。"测试错还是实现错"的分流**永不交给 Human**（flow.md §10.3 硬规则 5）
19. SHIELD_FIX：dispatch Shield 修测试（Devon 不得改测试；v0.4 已预留事件与路由定义，本版实现 M-IMPL 侧）；Runtime 创建受控测试 commit → 重跑 GREEN_GATE

**出口簇**（ISLAND_GATE_2）：

20. ISLAND_GATE_2：最终孤岛闭合复查（`trac check reach` 无孤岛）+ 全量 integration + e2e 变绿 → stage.exited(M-IMPL) → run.completed(boundary，M-VERIFY 未注册)

**视图隔离条款**（Human 强制要求，见 §1；上文条目 1/6/10/11/13 与工作项 5a 为其落点）：Devon 在 red/green/refactor 全部 phase 对 Shield 合同测试（integration/e2e）**不可见**。三道防线：① 物理不存在——隔离视图按屏蔽路径集 sparse-checkout 排除，视图里不存在的读不到；② shell 封堵——`bash: deny` 关闭经共享 `.git` 自行 checkout 的后门；③ 反馈脱敏——int/e2e 失败输出只回分类诊断、不回断言原文，隔离不被输出侧信道打穿。屏蔽路径集由 Archer 按宿主语言/框架惯例在 test-plan 层归属字段声明，随 BASELINE 冻结，**不得硬编码**；隔离机制本身语言无关。

### 3.2. 行为种子（规划层，EARS 句式）

行为种子采用 yaml 风格列表（Aaron 裁定，见下）：
- **task graph 校验**：WHEN Archer 产出 task graph 存在环 / scope 重叠 / required AC 未被任何 task IF 覆盖，THE 系统 SHALL 重派（≤3）且不进入 PRISM_PLAN
- **R 先于 G 的 lineage**：WHEN GREEN_COMMIT 创建后，THE 系统 SHALL 存在 git ref `refs/trac/rgr/{run}/{task}/{attempt}/red` 且 commit 拓扑上 R 严格先于 G
- **R 不可变**：IF 同一 attempt 重试试图改写 R，THE 系统 SHALL compare-and-set 失败并开新 attempt，旧 attempt 不被改写
- **GREEN_GATE 粒度**：WHEN task 级 GREEN_GATE 执行，THE 系统 SHALL 只跑该 task 单测 + 全部历史单测 + 变绿条件命中的 int 子集，且 SHALL NOT 跑 e2e
- **视图隔离（Devon 盲于合同测试）**：WHEN dispatch Devon（任一 phase），THE 系统 SHALL 在隔离视图内运行：屏蔽路径集（按宿主惯例声明、非硬编码）对应的 Shield 测试在视图中物理不存在；Devon 权限条目 bash: deny（trac init 写入 harness 配置，Devon.md 不含 permission）；IF 屏蔽路径集缺层归属声明，THE 系统 SHALL 硬错误、不静默放行
- **反馈脱敏**：WHEN GREEN_GATE 向 Devon 回传 int/e2e 失败，THE 系统 SHALL 只回分类诊断，SHALL NOT 回断言原文
- **判据包绑定与反自述回读**：WHEN Runtime dispatch Prism（plan/red/final/diagnostic 任一），THE 系统 SHALL 在 assignment 写明应加载判据包的名称+版本，verdict outcome 携带实际加载判据包 identity，Runtime 回读核对——不匹配判失败重派（D-29）
- **manifest 越界**：IF Devon 修改 manifest 白名单之外的文件，THE 系统 SHALL outcome failed、记录路径级证据、不提交
- **refactor no-change**：WHEN Devon 返回 no-change + 理由且 REFACTOR_GATE 全绿，THE 系统 SHALL 接受该 task 并进入 TASK_REVIEW
- **自动分流**：WHEN GREEN_GATE int 失败，THE 系统 SHALL 自动归因到 Devon/Shield/M-DESIGN/M-ACC 四路之一，SHALL NOT 转给 Human 裁决
- **质量门禁（生产/测试分层）**：WHEN refactor 产物为**生产代码**且被 ruff / flake8(CCR001) / pylint(R0801,C0302,R0915,R0914) 中任一判失败，THE 系统 SHALL 重派 Devon；IF refactor 产物为**测试代码**，THE 系统 SHALL 仅以重复 R0801 与文件长度 C0302（或宿主等价守卫）判失败，且 SHALL NOT 套用 CCR001/R0915/R0914
- **孤岛闭合出口**：WHEN 全部 task 完成，THE 系统 SHALL 仅在 `trac check reach` 闭合且全量 integration+e2e 变绿后退出 M-IMPL
- **可休眠**：流程在任一 phase 边界被 kill -9 打断后重启，THE 系统 SHALL 从 lineage + 事件回放恢复到精确 phase，不重跑已完成的 task
- **边界收尾**：WHEN M-IMPL 退出，THE 系统 SHALL 落 stage.exited(M-IMPL) + run.completed(terminal_state="boundary")，SHALL NOT 进入 M-VERIFY

> **Sage:** §1 已明确测试代码只接受 R0801/C0302（重复、文件长度），不得套用 CCR001/R0915/R0914（认知复杂度、函数/方法长度）；但本行为种子及 §3.1 refactor、§4 quality-guards 均写“四段全跑”，未声明生产代码与测试代码的差异化适用范围，可能直接违背 Human 的强制要求。此处不是新的产品选择：请 Scribe 将门禁统一写清为生产代码执行完整四段，测试代码仅执行重复与文件长度（或宿主等价守卫），并让 Archer 生成的配置与 Runtime 门禁继承同一分层政策。
>> **Scribe:** 已落地分层门禁，正文三处统一为生产/测试差异化（遵循 §1 强制策略）：① 行为种子『质量门禁（生产/测试分层）』——生产代码按 ruff/flake8(CCR001)/pylint(R0801,C0302,R0915,R0914) 四段判失败重派，测试代码仅以重复 R0801 与文件长度 C0302（或宿主等价守卫）判失败且 SHALL NOT 套用 CCR001/R0915/R0914；② §3.1 条目 13 REFACTOR——生产四段、测试仅重复+文件长度，宿主守卫配置与 Runtime 门禁继承同一分层政策；③ §4 工作项 8 skills——分层执行并明示 Archer 生成的宿主守卫配置继承同一政策。Q-03 裁定 A 与 Human『测试代码只收 R0801/C0302』的强制要求不再冲突。

> **Sage:** @Aaron 这里的可验证 lineage 合同自相矛盾：§3.1 GREEN_COMMIT 明定 G 的 parent=B，而本行为种子又要求“commit 拓扑上 R 严格先于 G”。若 G 与 R 都以 B 为父节点，则二者是 sibling，Git ancestry 无法证明 R 先于 G；仅靠 trailer/ref/event 能证明绑定或时序，但不是拓扑先后。两种实质方向会改变正式历史与证据语义：A. G 以 R 为 parent，使 R 成为 G 的祖先并由拓扑证明（推荐，最直接兑现“不是 Agent 自报”）；B. 保持 G parent=B，改为由不可变 R ref + G trailer + Runtime 事件序列联合证明，删除“拓扑上先于”。请裁定 A/B，并由 Scribe 统一 §2、§3.1、行为种子和工作项。
>> **Scribe:** 收到，此修改类 contract 矛盾需要你的 A/B 裁定后才可统一，未擅自改正文。请裁定 A（G 以 R 为 parent，由 Git ancestry 拓扑证明 R 先于 G，最直接兑现【非 Agent 自报】）/ B（保持 G 以 B 为 parent，联合 R ref 不可变 + G trailer + Runtime 事件序列证明，删除【拓扑上先于】）。你裁定后我据此同步 §2、§3.1 GREEN_COMMIT、行为种子 R 先于 G 与工作项 5 lineage。

> **Aaron:** 不得使用表格。可以使用类似 yaml 的列表。
>> **Scribe:** 已落地：全文不使用 markdown 表格，行为种子（§3.2）采用 yaml 风格列表，符合你的格式裁定。

> **Sage:** T-001 已裁定该 live run/dispatch 分析是 v0.5 必须完成的前置输入，但当前正文仍只有“疑似尚存类似点、产出要求后据此落地”的开放式发现任务，没有记录分析结果、具体受影响 dispatch、完成判据或可追踪产物。这样下游 spec 仍必须先做一次未知范围的发现工作，无法锁定 v0.5 实际产品增量。建议将前置设为进入 spec 前的硬门禁：完成 M-START→M-TEST 重录后，把具体发现、证据位置、需物化的 assignment 字段/路径/范围及“无遗漏”的判定写回本 story，再评审放行；不得仅以笼统 work item 把未知范围传给实现阶段。
>> **Scribe:** 已落地：§4 工作项 1 升级为【前置（T-001 裁定 A，硬门禁）】并补充放行判据——重录 M-START→M-TEST 后须将具体发现、证据位置、需物化 assignment 字段/路径/范围及【无遗漏】判定写回本 story 相应章节，再进入 spec/实现阶段；明确不得仅以笼统发现任务把未知范围传给下游。§3.1 dispatch 派发在本版按分析结论物化上下文。

## 4. 工作项

1. **前置（T-001 裁定 A，硬门禁）——live run 重录与 dispatch 物化分析**：v0.5 进入 spec 评审前的硬门禁——有监督地重录一次完整 live run（M-START → M-TEST），日志分析定位 runtime dispatch 中未物化上下文的点（v0.4 `_prompt()` 路径未具体化为典型案例——如 Runtime 本已可传绝对路径却让 Agent 自行搜索，疑似尚存其它可优化点），产出 dispatch 物化要求（路径、文件名、范围物化，减少 Agent 盲目搜索）。**放行判据**：重录完成后，须将具体发现、证据位置、需物化的 assignment 字段/路径/范围及"无遗漏"判定写回本 story（§3.1 / 本工作项），再进入 spec/实现阶段——不得仅以笼统的发现任务把未知范围传给下游；分析与发现作为 v0.5 输入，据此在 work item 3（executor）和 §3.1 dispatch 派发落地上下文物化
2. **machine.py**：M-IMPL StageDef（21 子状态含 NEEDS_ATTENTION 的 decide 路由）；writelock / task identity 进 State
3. **executor**：`_NEXT_STAGE` 增补 M-TEST→M-IMPL、M-IMPL→M-VERIFY（未注册处 boundary）；新命令 kind——`create_task_graph` / `dispatch_devon`（phase=red|green|refactor）/ `create_r_checkpoint` / `create_green_commit` / `create_refactor_commit` / `dispatch_prism`（plan/red/final/diagnostic 四种 assignment）/ `run_green_gate` / `run_refactor_gate` / `diagnose` / `shield_fix` / `task_completed`；按 work item 1 分析结论物化 dispatch 上下文
4. **task graph**：`task-plan.md` 解析 + validate（DAG 无环 / scope 白名单不重叠 / required AC 覆盖闭合 / 六项字段非空）；`task-log.md` 由 Runtime 在 phase 边界写入（每 task 一份）
5. **RGR lineage**：`refs/trac/rgr/{run}/{task}/{attempt}/red` git ref 管理；B/R/G(-Refactor) trailers（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）；compare-and-set 语义。注：R/G/Refactor/受控测试 commit 均由 Runtime 创建、触发宿主 pre-commit 钩子；钩子拒绝按 F-1（D-30，`41a4ed4`）写钩子输出为证据并在预算内重派，不得静默退出死锁
6. **manifest + writelock**：per-task scope 白名单；`writelock.granted` / `writelock.released` 事件；越界 → outcome failed（复用 v0.2 baseline + 后置审计，写范围收窄到 manifest）
6a. **视图隔离**：test-plan.md 层归属字段扩展为"路径 → 层（unit/integration/e2e）"清单（Archer 按宿主语言惯例填写，Python `tests/integration`、Java `src/it`、Go build-tag 目录等同等合法）；BASELINE 冻结屏蔽路径集进测试资产 digest；dispatch Devon 前物化 sparse-checkout 隔离视图（opencode cwd 指向视图）、`bash: deny`（harness 配置中 Devon 权限条目，由 `trac init` 写入，D-19；Devon.md harness 无关、不含 permission 块，D-26）、回灌后终态清理 + 崩溃 reconcile；GREEN_GATE 反馈按层脱敏
7. **agents**：`tracks/agents/Devon.md`（首次交付——frontmatter version: 0.5，遵循 opencode agent 定义格式；harness 无关、不含 permission 块，D-26，隔离权限见 work item 6a）；Prism 的 plan/red/final/diagnostic 四种判据包接线与 skill 物化（判据包本体新建，参照 v0.4 test-asset-criteria 先例；承 D-29 反自述三件套）
8. **skills**：`tracks-quality-guards` 接入 refactor 门禁，分层执行（遵循 §1 强制策略与 T-002）——**生产代码** ruff + flake8 CCR001 + pylint 重复+体量；**测试代码**仅重复 R0801 + 文件长度 C0302（或宿主等价守卫），不套用 CCR001/R0915/R0914；Archer 生成的宿主守卫配置与 Runtime 门禁继承同一分层政策；阈值继承 pyproject.toml，不重复定义
9. **events**：`events.py` 追加事件类型——`baseline.frozen` / `taskgraph.committed` / `task.started` / `writelock.granted|released` / `red.checkpointed` / `green.committed` / `refactor.committed|no_change` / `test.committed`（SHIELD_FIX 受控测试 commit）/ `task.completed`；`verdict.failed` 增补 reason（red_invalid / regression / budget / island / scope / test_defect / impl_defect）
10. **测试（集成，FakeAgent）**：FakeAgent 集成测试——M-TEST 出口 → M-IMPL 全循环（含 2 个串行 task）→ boundary；负例——非法红、manifest 越界、R 被改写（compare-and-set 拒绝）、budget/scope fail 回 GREEN、test_defect vs impl_defect 分流、ISLAND_GATE_2 孤岛阻断出口、**视图中屏蔽路径物理不存在（Devon 读不到）、屏蔽清单缺层归属声明硬错误、int/e2e 失败反馈不含断言原文**；RGR lineage ground truth（git ref 拓扑独立校验脚本，不 import tracks）
11. **测试（end-to-end，真实 Devon）**：end-to-end 使用真实 Devon（Q-04 裁定，见 §5）跑通 M-IMPL 完整循环——M-TEST 出口 → task graph → 串行 task RGR → 门禁 → ISLAND_GATE_2 → boundary；与集成测试（work item 10，FakeAgent）互为独立通道，验证真实 Agent 通道在 M-IMPL 贯通（按 v0.2 双通道模式）

## 5. 开放产品决定

### Q-01 task 并行调度

- **为什么必须由 Human 决定**：flow.md 的 `[P]` 并行标记与"单写者 lease"存在张力——真并行需要多 Devon 并发 lease 与文件级冲突仲裁，改变 Runtime 写者模型的产品语义
- **可选方向**：A. v0.5 串行调度，`[P]` 只记录不并发（推荐）；B. v0.5 即实现并发 lease
- **Agent 推荐**：A——单写者纪律是 tracks 的承重墙，v0.5 的目标是把内圈循环跑通，并发留给独立 story
> **Aaron:** 不并行，只串行
>> **Scribe:** 已落地：§3.1 条目 5 声明 v0.5 串行调度、[P] 标记记录但不并发执行；§6 Out-of-Scope 同步『不做 [P] 并行调度』。

### Q-02 task graph 的真相源

- **为什么必须由 Human 决定**：task graph 放文档（task-plan.md，与其它文档同族、内容真相源在文档）还是放 events 投影（派生数据）
- **可选方向**：A. task-plan.md 为内容真相源，Runtime 解析（推荐，同 story/spec 纪律）；B. 存 tracks.db 投影表
- **Agent 推荐**：A——"文档即内容真相源"是 tracks 既成纪律（story/spec/test-plan 内容真相源皆在文档；D-02 裁定的是事件流存 SQLite，与内容真相源分属两层），task graph 与 spec 同级

> **Aaron:** 真相放 task-plan.md， 当前完成了哪一步可能要放 db.
>> **Scribe:** 已落地：§3.1 条目 2 与 §4 工作项 4 以 task-plan.md 为内容真相源、Runtime 解析；task-log.md 由 Runtime 在 phase 边界写入。『当前完成了哪一步』放 db 的进展投影归 events 层，正文已按『文档为真相源、进展入 events/db』处理。

### Q-03 refactor 质量门禁的严格度

- **为什么必须由 Human 决定**：重构阶段跑 ruff/flake8/pylint 是把 pre-commit 门禁搬进 Runtime——门禁失败会触发重派 Devon，影响 attempt 预算消耗速度
- **可选方向**：A. refactor 门禁 = pre-commit 四段全跑，失败计 attempt（推荐）；B. 只跑 flake8 CCR001（认知复杂度一项）
- **Agent 推荐**：A——tracks-quality-guards 已就位，复用成本最低；认知复杂度之外的重复/超长同样是 slop 来源

> **Aaron:** A
>> **Scribe:** 已落地：Q-03 裁定 A——§3.1 条目 13、§4 工作项 8、行为种子『质量门禁』均写为 refactor 门禁 = tracks-quality-guards 四段全跑（ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914），失败计 attempt。

### Q-04 Devon 是否接真实 agent

- **为什么必须由 Human 决定**：延续 v0.1–v0.4 的"不接真实 LLM"排除，还是借 M-IMPL（行为最复杂的阶段）首次接真实 agent
- **可选方向**：A. 集成测试用 FakeAgent、end-to-end 用真实 Devon（裁定，见下）；B. 全部 FakeAgent
- **Agent 推荐**：A（Aaron 裁定）——集成测试用 FakeAgent 保证确定性回归，end-to-end 用真实 Devon 验证真实 Agent 通道贯通（work item 10/11 双通道落地）

> **Aaron:** 集成测试使用 FakeAgent; end to end 仍然要使用真实 Devon。过去的开发充分证明了，end to end 使用真实 Agent 的意义
>> **Scribe:** 已落地：Q-04 裁定 A——§4 工作项 10（集成，FakeAgent）与工作项 11（end-to-end，真实 Devon）双通道；§6 Out-of-Scope 改为『仅此两条通道，真实通道仅以 end-to-end 形式存在』，已删除待裁定的『不接真实 LLM Agent』表述。

## 6. 范围、约束与例外

- **必须保持的产品约束**：kernel 纯函数边界（decide/project 不碰 IO）；事件溯源 append-only；Agent 不 commit/push、不推进状态（Runtime 是唯一流程 authority，正式 commit 均由 Runtime 创建）；单写者纪律（per-task manifest 白名单 + writelock lease，Devon 不得改 Shield 测试）；D-28（输入 revision/digest/完整性校验归 Runtime 派发前形式检查，Agent 不自校验，verdict 仅传播 identity）；D-29 反自述三件套（assignment 写明判据包名称+版本 / verdict 携带实际加载 identity / Runtime 回读核对不匹配判失败重派，适用于 plan/red/final/diagnostic 全部四种 Prism 派发）；D-19/D-26（权限在 harness 配置、由 `trac init` 写入，agent 定义 harness 无关、不含 permission 块）；D-31（AC 绑定用测试函数紧邻上方 TRACKS-TRACE 标记行，行级正则、零依赖）；D-32（大小两个测试循环，M-IMPL 为内圈 RGR 循环，驱动外圈合同循环变绿）
- **非常规要求**：视图隔离是**物理隔离**（屏蔽路径在隔离视图中物理不存在），不是提示词纪律——"禁止"兑现为"不可能"；RED 阶段要求失败（合法 Red 是交付态而非异常，承 M-TEST 的合法 Red 语义到 unit 层）；refactor 接受 no-change + 理由（不强制产生改动）；"测试错还是实现错"的分流永不交给 Human（flow.md §10.3 硬规则 5）
- **Out-of-Scope**：不实现 M-VERIFY 及后续阶段，只停在 M-IMPL → M-VERIFY 边界；不做 `[P]` 并行调度（Q-01 已裁定：只串行，`[P]` 仅记录）；不做真实 LLM 通道的其它形态（Q-04 已裁定：集成测试用 FakeAgent、end-to-end 用真实 Devon——仅此两条通道，真实通道仅以 end-to-end 形式存在，不做真实通道的单元级/集成级接入）；不做 hotfix / bug-fix 变体（flow.md §16，后续 story）；不做 `trac check ratio / dup / budget` 命令（trace/reach 已在 v0.4 交付；budget 在 v0.5 仅体现为 TASK_REVIEW 的 attempt/lineage 预算校验）；不做函数级调用图（reach 维持模块级，M-VERIFY 反 slop 门禁的更细粒度分析属未来）；不做 GitHub Issue 映射（task-plan ↔ Issues 同步不在本版）；不做 M-IMPL 内的 Human 门禁（flow.md：仅有的两个 Human gate 是 M-REQ-APPROVAL 与 M-RELEASE，M-IMPL 全程程序证据）

> **Scribe [RESOLVED]:** Q-04 裁定与正文冲突（TRIAGE 阻断项）：您已裁定 line 145「集成测试使用 FakeAgent; end to end 仍然要使用真实 Devon」，但正文未承接该裁定——§6 Out-of-Scope line 151 仍写「不接真实 LLM Agent（延续 v0.1–v0.4 排除，FakeAgent 验证；待 Q-04 裁定）」且标着待裁定；§4 工作项 9（line 112）仍只写「FakeAgent 端到端——M-TEST 出口 → M-IMPL 全循环」。两者矛盾：若 e2e 必须用真实 Devon，则 v0.5 需纳入 real-Devon 通道（work item 9 拆分为集成用 FakeAgent + e2e 用真实 Devon），且 Out-of-Scope 的「不接真实 LLM Agent」「待 Q-04 裁定」须删除；若我理解有误（例如「真实 Devon」仅指 e2e 验收方式、实现仍按 FakeAgent 通道先贯通），请澄清边界。请裁定 Work Item 9 与 Out-of-Scope 如何修订，TRIAGE 据此给出 go 建议。
>> **Aaron:** 按我裁定，文档中意思统一是你的工作。

## 7. 分流建议

Go——flow.md §10 规格完整（子状态机 / 事件清单 / 5 条硬规则全部就位），模板（task-plan / task-log）与 skill（tracks-quality-guards）先期存在，v0.4 交付的 trace/reach/RED_GATE 分类直接复用；无架构级返工风险。建议按"规划簇 → RGR 单 task → 多 task 串行 → 诊断簇 → 出口簇"切片实现。
