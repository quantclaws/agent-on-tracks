---
acc_id: ACC-005
created: 2026-08-09
status: draft
sha: 3285e01cf027ec46d2249a5717ce797951bd25fcb2f012c440d833f5a362574e
---

# M-IMPL 阶段推进（Devon 逐 task RGR） - 验收标准

## FR-0010 M-IMPL 阶段注册与子状态机驱动

### AC-FR0010-01

  - M-TEST EXIT（`stage.exited(M-TEST)` + 测试资产冻结为 baseline）后事件流出现 `stage.entered(M-IMPL)`，初始子状态为 `BASELINE`（SM-01.1）；进入前后事件序列中无作为进入前置的 `human.review`/`human.approval` 插队

### AC-FR0010-02

  - `trac status` 在 M-IMPL 期间报告 `stage=M-IMPL` 与当前子状态（BASELINE/PLANNING/ISLAND_GATE_1/PRISM_PLAN/TASK_DISPATCH/RED/RED_GATE/RED_CHECKPOINT/PRISM_RED/GREEN/GREEN_GATE/GREEN_COMMIT/REFACTOR/REFACTOR_GATE/TASK_REVIEW/PRISM_FINAL/TASK_DONE/ISLAND_GATE_2/DIAGNOSE/SHIELD_FIX 之一）
  - M-TEST EXIT 后 `trac run` 能进入 M-IMPL（`stage.entered(M-IMPL)`）；M-IMPL EXIT 后因 M-VERIFY 未注册，事件流出现 `run.completed(terminal_state="boundary")`，`trac status` 报告 `terminal=boundary`，不进入 M-VERIFY

### AC-FR0010-03

  - M-IMPL 子状态机的状态转移严格遵循 SM-01 清单：fake 端到端旅程的事件流按 SM-01 的合法转移依次出现各子状态；fake 通道注入未列出的转移时 `trac run` 不推进且事件流不出现越界 `stage.exited`/`run.completed`

### AC-FR0010-04

  - 既有 M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL / M-DESIGN / M-TEST 阶段行为不变（承自 v0.3 BS-07 回归安全）：fake 端到端旅程 `tests/e2e/test_full_journey.py::test_full_journey_to_boundary` 的 M-TEST 之前事件前缀逐字节稳定，M-IMPL 接入不改变既有阶段的转移、事件与 M-TEST 边界终态

### AC-FR0010-05

  - M-IMPL 子状态机由显式控制流驱动（非仅 StageDef 表驱动）：M-IMPL 期间 `trac status` 报告的子状态序列是既有 DRAFT/REVIEW/EXIT 模式无法产出的，证明 M-IMPL 专属控制流已接入；该控制流仍维持 kernel 纯函数边界（NFR-0010）

## FR-0020 BASELINE 重算与测试资产冻结

### AC-FR0020-01

  - BASELINE（SM-01.1–.2）：M-IMPL 进入后 Runtime 重算 baseline，事件流出现 `baseline.frozen`；baseline current（三件套 + 设计三文档 + 冻结测试资产 digest + contracts + Issues + branch + approval 全部有效）-> 子状态进入 `PLANNING`（SM-01.2）

### AC-FR0020-02

  - baseline 缺失/stale/冲突 -> 子状态进入 `NEEDS_ATTENTION`（SM-01.3）；reconcile 后回 `BASELINE`（SM-01.4）；rolled_back -> 事件流出现 `stage.rolled_back`（SM-01.5），`trac status` 报告终态

### AC-FR0020-03

  - 测试资产清单冻结：随 baseline digest 一并冻结测试路径集（frozen test path set）；test-plan.md 层归属字段声明哪些路径属 integration/e2e、哪些属 unit
  - 清单缺层归属声明 -> 硬错误，不静默放行（BS-02）：`trac validate --file test-plan.md` 对缺层归属声明判失败（非零退出），`trac run` 在 BASELINE 不进入 PLANNING

### AC-FR0020-04

  - 冻结的测试路径集供 test-authority worktree 冻结与 gate worktree 组合运行（FR-0070）；路径集由 Archer 按宿主语言/框架惯例填写（Python `tests/integration` 与 Java `src/it`、Go build-tag 目录同等合法），隔离机制本身语言无关、不硬编码

## FR-0030 PLANNING：Archer 拆 task graph

### AC-FR0030-01

  - PLANNING（SM-01.6）：dispatch Archer 拆 task graph，`task-plan.md` 为内容真相源（Runtime 解析）；事件流出现 `taskgraph.committed`，`trac status` 报告 `substate=PLANNING`
  - 每个 task 纵向切片 + 声明 scope 白名单（manifest 授权文件集）+ 实现的 IF- 集合 + 预算

### AC-FR0030-02

  - validate 校验（SM-01.6 前置）：DAG 无环 / scope 不重叠 / required AC 被至少一个 task 的 IF- 集合覆盖（AC 覆盖闭合）；任一不满足不进入 ISLAND_GATE_1
  - validate fail 重派 Archer（≤3）：第 1/2 次失败后重派同一 Archer；第 3 次失败 -> `status=awaiting_human`、`awaiting=escalation`，`trac run` 不再产出 Archer 派发 command

### AC-FR0030-03

  - `task-log.md` 由 Runtime 在 phase 边界写入（每 task 一份）；"当前完成了哪一步"的进展投影入 events/db，`trac report` 可展示 per-task 进展；task-plan.md 为内容真相源、task-log.md 为 Runtime 写入的进展投影

## FR-0040 ISLAND_GATE_1：程序复核

### AC-FR0040-01

  - ISLAND_GATE_1（SM-01.8）：每条 required AC 六项检查（owner / surface / composition / wiring / test / evidence）通过后子状态进入 `PRISM_PLAN`（SM-01.10）；`trac status` 报告 `substate=ISLAND_GATE_1`
  - 六元组是 Archer 设计期义务、M-DESIGN 已由 Prism 逐项闭合，本门禁是该合同的复核而非首次建立（D-21）

### AC-FR0040-02

  - 不闭合 -> 事件流出现 `verdict.failed(island)`，子状态回 `PLANNING`（SM-01.9），重派 Archer

## FR-0050 PRISM_PLAN：判据包绑定与切片评审

### AC-FR0050-01

  - PRISM_PLAN（SM-01.10）：dispatch Prism 评审 task graph 切片，`trac status` 报告 `substate=PRISM_PLAN`
  - 反自述三件套（D-29，必须交付）：① assignment 写明应加载判据包的名称+版本（Runtime 决定，Prism 不自选）--派发 command 的 assignment payload 含判据包名称与版本字段；② Prism verdict outcome 携带实际加载判据包 identity（`prism.verdict` 事件 payload 含判据包 identity）；③ Runtime 回读核对：outcome identity 与 assignment 指定 identity 不匹配 -> 判失败重派 Prism（产生 `verdict.failed`，不进入 TASK_DISPATCH）

### AC-FR0050-02

  - `prism.verdict(pass)` -> 子状态进入 `TASK_DISPATCH`（SM-01.14）；`prism.verdict(revise)` -> 回 `PLANNING` 重派 Archer（SM-01.11）
  - 设计缺口 -> `stage.rolled_back` 回 M-DESIGN（SM-01.12，Archer+Prism 裁定，不经 Human）；需求缺口 -> `stage.rolled_back` 回 M-SPEC/M-ACC（SM-01.13，事件流出现 `human.*` 批准后才 `stage.rolled_back`）

### AC-FR0050-03

  - Prism `revise` 必须经 `trac discuss` 锚定线程（承自 v0.3 Prism 评审协议）：revise verdict 无对应 open 线程 -> 判 `revise_without_findings` failure，重派 Prism；`trac discuss query` 可观察到该锚定线程

### AC-FR0050-04

  - 反自述三件套适用于 plan / red / final / diagnostic 全部四种 Prism 派发（BS-03）：任一 Prism 派发均含判据包名称+版本指定、outcome identity 回读、不匹配判失败重派

## FR-0060 TASK_DISPATCH：DAG 调度与单写者 manifest

### AC-FR0060-01

  - TASK_DISPATCH（SM-01.14）：Runtime 按 DAG 依赖选 ready task（v0.5 串行调度，`[P]` 标记记录但不并发执行）；单写者 lease + 创建 manifest（per-task 白名单 + writelock lease）
  - `writelock.granted` 事件落盘后进入 RED，事件流出现 `task.started`；`trac status` 报告 `substate=TASK_DISPATCH`

### AC-FR0060-02

  - task 变绿子集 = 单测 + 变绿条件（test-plan 声明的 IF- 归属）命中本 task IF- 集合的 int 子集
  - 全部 task 完成 -> 子状态进入 `ISLAND_GATE_2`（SM-01.41）；还有 ready task -> 回 `TASK_DISPATCH`（SM-01.40）

### AC-FR0060-03

  - `[P]` 并行标记只记录不并发执行（Aaron 裁定）：fake 端到端旅程中带 `[P]` 标记的 task 仍按串行顺序执行，事件流中不出现并发 task.started 交错

## FR-0070 RED：Devon 隔离与私有 R ref

### AC-FR0070-01

  - RED（SM-01.15）：dispatch Devon（phase=red）在 Devon candidate worktree 运行；`trac status` 报告 `substate=RED`
  - Devon candidate worktree 在 Shield WRITE 之前创建（M-DESIGN pass 后记录共同基线 `C_design`），因此 Devon 天然不包含之后生成的 Shield tests

### AC-FR0070-02

  - Devon 只添加 unit test，不碰产品代码与 Shield 测试（flow §10.3 硬规则 1）：Devon outcome 必须是 test-only diff；outcome 含产品代码或 Shield 测试改动 -> 判失败
  - Devon 的全部文件访问能力（read/glob 等文件工具 + shell）被 per-agent harness 边界限定到 Runtime 物化的隔离 worktree 与获准临时目录，原始 checkout 与其他可恢复的 Shield 测试内容均不可读

### AC-FR0070-03

  - RED_CHECKPOINT（SM-01.18）：Runtime 创建私有 commit R，写 git ref `refs/trac/rgr/{run}/{task}/{attempt}/red`；事件流出现 `red.checkpointed`
  - `git show refs/trac/rgr/{run}/{task}/{attempt}/red` 存在且指向一个含 test-only diff 的 commit

### AC-FR0070-04

  - R 不可变（BS-06）：同一 attempt 重试试图改写 R ref 时 compare-and-set 失败并开新 attempt，旧 attempt 不被改写；`git rev-parse refs/trac/rgr/{run}/{task}/{attempt}/red` 在重试前后指向同一 SHA

### AC-FR0070-05

  - 三 worktree 方案：Runtime 在独立 gate worktree 组合 `C_design` + frozen bundle + Devon candidate 运行 integration/e2e；frozen bundle 永不合入 Devon candidate（`git log` 中 Devon candidate 分支不含 Shield 测试 commit）
  - Shield 在 test-authority worktree 冻结测试（frozen bundle）；bootstrap/manual 无 temporal worktree 时依赖 manifest + prompt 约定，不永久 fail closed

## FR-0080 RED_GATE：合法 Red 分类

### AC-FR0080-01

  - RED_GATE（SM-01.16）：Runtime 校验预期失败；合法红 = 行为断言失败 / symbol 缺失（flow §10.1 口径）；全部合法红 -> 子状态进入 `RED_CHECKPOINT`（SM-01.16）
  - 复用 v0.4 RED_GATE 分类器框架，但其桩合同 token 失败条款（`NotImplementedError("IF-...")`）属 M-TEST 合同测试场景，不在本门禁之列--M-IMPL 的合法红是 unit 层行为断言失败 / symbol 缺失

### AC-FR0080-02

  - 非法红（collection/语法/fixture/import 错误、测试意外通过）重派 Devon（SM-01.17）：事件流出现 `verdict.failed(red_invalid)`，子状态回 `RED`；测试意外通过亦视为非法（Red 阶段要求失败是交付态而非异常）

## FR-0090 PRISM_RED：Red checkpoint 范围评审

### AC-FR0090-01

  - PRISM_RED（SM-01.19）：dispatch Prism 评 B..R 范围（Red 测试确实测了该 task 声明的 IF/AC，且未测多余）；反自述三件套（D-29，同 FR-0050）；`trac status` 报告 `substate=PRISM_RED`

### AC-FR0090-02

  - `prism.verdict(pass)` 绑定 R -> 子状态进入 `GREEN`（SM-01.21）；`prism.verdict(revise)` -> 回 `RED` 新 attempt（SM-01.20）
  - revise 必须经 `trac discuss` 锚定线程（同 FR-0050-03）

## FR-0100 GREEN：最小实现与受控 diff 回灌

### AC-FR0100-01

  - GREEN（SM-01.21）：从 R tree 恢复 Devon candidate worktree；dispatch Devon（phase=green）--最小实现，R 测试不可改；`trac status` 报告 `substate=GREEN`
  - Devon outcome 含对 R 测试的改动 -> 判失败（R 测试不可改）

### AC-FR0100-02

  - 完成后受控 diff 按 manifest 回灌主仓：Devon candidate worktree 中 manifest 白名单之外的文件改动不被回灌；视图终态清理 + 崩溃 reconcile（承 v0.2 物化合同）

## FR-0110 GREEN_GATE：粒度门禁与反馈脱敏

### AC-FR0110-01

  - GREEN_GATE（SM-01.22）：targeted 单测 + 全部历史单测 + test-plan 变绿条件命中本 task IF- 集合的 int 子集 + lint/format/type/static + 合同；`trac status` 报告 `substate=GREEN_GATE`
  - 第一轮不跑 e2e（v0.4 既定粒度，BS-08）：GREEN_GATE 事件序列中不出现 e2e 执行
  - 全过 -> 子状态进入 `GREEN_COMMIT`（SM-01.22）；实现缺陷 -> 回 `GREEN` 重派 Devon（SM-01.23）

### AC-FR0110-02

  - integration/e2e 由 Runtime 在独立 gate worktree（组合 `C_design` + frozen bundle + Devon candidate）运行并归因（D-32 外圈合同循环，三 worktree 方案见 FR-0070）

### AC-FR0110-03

  - 反馈脱敏（BS-05）：单测失败回完整输出；int/e2e 失败只回分类诊断（哪条 IF 契约失败 / symbol 缺失类型），不回断言原文--防止冻结测试内容经测试输出侧信道泄漏给 Devon
  - Devon 收到的 int/e2e 失败反馈中不含测试断言原文（可通过 dispatch assignment payload 观察）

### AC-FR0110-04

  - int 失败归因不明 -> 子状态进入 `DIAGNOSE`（SM-01.24）

## FR-0120 GREEN_COMMIT：正式 commit G 与 lineage 证明

### AC-FR0120-01

  - GREEN_COMMIT（SM-01.30）：Runtime 创建正式 commit G，事件流出现 `green.committed`；`trac status` 报告 `substate=GREEN_COMMIT`
  - `git log` 中 G 的 parent=B（flow §10 既有合同）；G 的 trailers 记录 R/task/attempt identity（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`），`git log --format='%B' -1 <G>` 含三个 trailer

### AC-FR0120-02

  - R 先于 G 的 lineage 证明（BS-07，修订日志 R-1）：`green.committed` 事件发生时存在 git ref `refs/trac/rgr/{run}/{task}/{attempt}/red`，且 R 先于 G 由不可变 R ref + G trailer（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）+ Runtime 事件序列（`red.checkpointed` seq < `green.committed` seq）联合证明
  - 不作 Git ancestry 拓扑断言：G 与 R 均以 B 为父节点，`git merge-base --is-ancestor <R> <G>` 不成立（R 不是 G 的祖先），但 lineage 仍可由 ref + trailer + 事件序列三件联合兑现

## FR-0130 REFACTOR 与质量门禁分层

### AC-FR0130-01

  - REFACTOR（SM-01.31）：dispatch Devon（phase=refactor，仍在 Devon candidate worktree）；可返回 no_change + 理由（BS-10，不强制产生改动，质量由门禁而非改动量保证）；`trac status` 报告 `substate=REFACTOR`
  - Devon 返回 no_change + 理由且 REFACTOR_GATE 全绿 -> 接受该 task 进入 `TASK_REVIEW`（SM-01.32）

### AC-FR0130-02

  - REFACTOR_GATE（SM-01.32–.34）：重跑 GREEN_GATE 全部检查
  - 质量门禁分层（BS-11，Q-03 裁定 A = tracks-quality-guards 分层执行）：生产代码执行完整四段--ruff + flake8 CCR001（认知复杂度）+ pylint R0801（重复）+ pylint C0302（文件长度）+ pylint R0915（方法长度）+ pylint R0914（参数数量），任一判失败重派 Devon
  - 测试代码仅执行重复 R0801 + 文件长度 C0302（或宿主等价守卫），不套用 CCR001 / R0915 / R0914；宿主守卫配置与 Runtime 门禁继承同一分层政策

### AC-FR0130-03

  - 动 public interface -> `stage.rolled_back` upstream（SM-01.34）：REFACTOR 产物改动 public interface 时事件流出现 `stage.rolled_back`
  - 通过（committed | no_change）-> 子状态进入 `TASK_REVIEW`（SM-01.32）；失败 -> 回 `REFACTOR` 重派 Devon（SM-01.33）

## FR-0140 TASK_REVIEW 与 PRISM_FINAL

### AC-FR0140-01

  - TASK_REVIEW（SM-01.35）：Runtime 校验 task range--write scope / secret / AC trace / B-R-G(-Refactor) lineage / budget；校验通过 -> 子状态进入 `PRISM_FINAL`（SM-01.37）；`trac status` 报告 `substate=TASK_REVIEW`
  - budget/scope fail -> 回 `GREEN` 重派 Devon（SM-01.36）

### AC-FR0140-02

  - PRISM_FINAL（SM-01.37–.39）：dispatch Prism 评完整 range + lineage；反自述三件套（D-29）；`trac status` 报告 `substate=PRISM_FINAL`
  - `prism.verdict(pass)` -> 子状态进入 `TASK_DONE`（SM-01.40）；revise（实现）-> 回 `GREEN`（SM-01.38）；revise（Red 测试）-> 回 `RED` 新 lineage（SM-01.39）

### AC-FR0140-03

  - revise 必须经 `trac discuss` 锚定线程（同 FR-0050-03）；`task.completed` 事件在 PRISM_FINAL pass 后出现，`trac report` 可展示该 task 的完整 RGR lineage

## FR-0150 DIAGNOSE 四路诊断与 SHIELD_FIX

### AC-FR0150-01

  - DIAGNOSE（SM-01.25–.28）：dispatch Prism 四路诊断；"测试错还是实现错"的分流永不交给 Human（flow.md §10.3 硬规则 5，BS-12）；`trac status` 报告 `substate=DIAGNOSE`
  - 实现缺陷 -> 回 `GREEN` 重派 Devon（SM-01.25）；测试缺陷 -> `SHIELD_FIX`（SM-01.26）；接口/架构不足 -> `stage.rolled_back` 回 M-DESIGN（SM-01.27，Archer+Prism 裁定，不经 Human）；AC/Spec 缺口 -> `stage.rolled_back` 回 M-ACC/M-SPEC（SM-01.28，Human 批准后才 `stage.rolled_back`）

### AC-FR0150-02

  - 分流结论落 `verdict.failed(reason)` 事件，reason 取值限于 `red_invalid` / `regression` / `budget` / `island` / `scope` / `test_defect` / `impl_defect`；`trac report` 可展示该分类与路由结果

### AC-FR0150-03

  - SHIELD_FIX（SM-01.26，.29）：dispatch Shield 修测试（Devon 不得改测试）；Runtime 创建受控测试 commit，事件流出现 `test.committed`；重跑 GREEN_GATE（SM-01.29）
  - Shield 修复的测试改动落在冻结测试路径集内（FR-0020），Devon candidate worktree 中不出现该改动

### AC-FR0150-04

  - return upstream 后目标之后的 task graph / baselines / lineage / commits 标记 stale/superseded，不得复用旧绿色证据（flow §17.1）：rollback 后重新进入的 task 不复用 rollback 前的 `green.committed` / `task.completed` 证据

## FR-0160 ISLAND_GATE_2：出口门禁与边界退出

### AC-FR0160-01

  - ISLAND_GATE_2（SM-01.42–.44）：全部 task 完成（TASK_DONE 且无 ready task）后进入；最终孤岛闭合复查（`trac check reach` 无孤岛）+ 全量 integration + e2e 变绿（出口门禁，BS-13）；`trac status` 报告 `substate=ISLAND_GATE_2`
  - `trac check reach` 退出码 0（无孤岛）且全量 int+e2e 通过 -> 退出；`trac check reach` 输出孤岛模块或全量执行有失败 -> 不退出

### AC-FR0160-02

  - 全量执行有失败 -> 子状态进入 `DIAGNOSE`（SM-01.43）；`verdict.failed(island)` -> 回 `PLANNING`（SM-01.44）

### AC-FR0160-03

  - 通过 -> 事件流出现 `stage.exited(M-IMPL)` -> `run.completed(terminal_state="boundary")`（SM-01.42，BS-14）；停在 M-IMPL->M-VERIFY 边界，M-VERIFY 实现不在本 release：`trac status` 报告 `terminal=boundary`，事件流不出现 `stage.entered(M-VERIFY)`

### AC-FR0160-04

  - M-IMPL 无 Human 门禁（BS-14）：退出依据全部是程序证据，事件流中 M-IMPL 期间不出现作为退出前置的 `human.review` 或 `human.approval`（需求缺口回退 SM-01.13/28 的 Human 批准是回退前置，非退出前置）

## FR-0170 Devon opencode agent 接入与 manifest 越界审计

### AC-FR0170-01

  - `AGENT_NAME` 增补 `devon -> Devon`；Devon 作为真实 opencode agent 被 dispatch（承自 v0.3 Archer/Prism 与 v0.4 Shield 接入模式）；Devon prompt/skill 物化与回收（dispatch 前物化到 opencode 发现路径、终态/失败后清理、崩溃后 reconcile 清理悬挂物化）与 Archer/Prism/Shield 同构

### AC-FR0170-02

  - `tracks/agents/Devon.md` 加入 deliverables 一致性集合：`trac check deliverables` 校验其存在性 + frontmatter `version` + `IQ`（与既有六个 agent 提示词同构）；缺失/非法 -> 非零退出阻塞合并
  - Devon.md 不含 permission 块（D-26：agent 定义 harness 无关、不含 permission 块--Devon 隔离由时态 worktree + 约定承担，FR-0070）

### AC-FR0170-03

  - manifest 越界审计（BS-09）：Devon 修改 manifest 白名单之外的文件时 outcome failed、记录路径级证据、不提交；越权写文件被审计检出并通过 git 回滚（`over_reach` failure_class，承自 v0.3 写范围审计机制）
  - 回滚仅移除可证明由 Devon 产生的改动，Human 既有修改不被覆盖

## FR-0180 task-plan / task-log 真相源与 Runtime 解析

### AC-FR0180-01

  - `task-plan.md` 为 task graph 内容真相源、Runtime 解析（Aaron 裁定：真相放 task-plan.md）；M-IMPL PLANNING（FR-0030）解析 task-plan.md 驱动 DAG 调度
  - task-plan.md 模板既有 Task List（ID / Task description / Related test / Target file / Depends on / Parallel marker / Status）+ Dependency Graph + Runtime Review Result

### AC-FR0180-02

  - `trac validate --file task-plan.md` 校验 DAG 无环 / scope 不重叠 / required AC 覆盖闭合；任一不满足判失败（非零退出）

### AC-FR0180-03

  - `task-log.md` 由 Runtime 在 phase 边界写入（每 task 一份），模板既有 Phase 1 Red / Phase 2 Green / Phase 3 Refactor / Runtime Quality Gate；"当前完成了哪一步"的进展投影入 events/db（Aaron 裁定：当前完成了哪一步放 db），`trac report` 可重建 per-task 进展

## FR-0190 dispatch 物化完整性合同

### AC-FR0190-01

  - Runtime dispatch 必须在 `command.issued` 前向 agent assignment 物化完整上下文，agent 不得自行搜索/猜：派发 command 的 assignment payload 含绝对 target doc/doc-set、role/substate/attempt/review_round、docs/templates/skills、criteria-pack identity、test_tasks、pre_dirty_snapshot、result/checkpoint identity（修订日志 R-2，物化字段集第 9 项）

### AC-FR0190-02

  - state-specific Human return（第 1 项）：M-TEST/M-IMPL escalation 必须允许 `to_stage=M-DESIGN`，不能用 requirement-only gate；M-TEST 为既有回归基线（run `01KZ5QCRPMBVC1A6HYEHMKKGVH` seq 223-224），M-IMPL 为 v0.5 新增 escalation 路径（SM-01.12/27 -> M-DESIGN），同一不变量覆盖

### AC-FR0190-03

  - canonical 路径（第 2 项）：canonical `.tracks/project/project.toml` 是唯一允许的 `.tracks/**` project contract 路径；随设计 checkpoint 去重提交，其他 `.tracks/**` 仍 fail closed

### AC-FR0190-04

  - M-DESIGN 输出合同（第 3 项）：test-plan canonical `## 8. AC Coverage`，interfaces canonical `## 5. IF Registry`；每条 integration/e2e AC 解析为 `{ac_id, layers, if_ids}`；missing/empty registry、坏/缺 header、duplicate AC、empty test cell、unregistered IF 均 fail closed；standalone `trac validate test-plan.md` 同门禁

### AC-FR0190-05

  - test_tasks 注入（第 4 项）：Runtime 在 `command.issued` 前向 Shield assignment 注入非空 `test_tasks`；仅 role=Shield/substate=WRITE 适用；无效输入不调用 backend，failed outcome=`stub_gap`，自动回 M-DESIGN

### AC-FR0190-06

  - ResultCheckpoint（第 5 项）：invalid result retry 必须按 actor/substate 清 dispatch flags；Shield WRITE `requires_diff=true`，无 tests diff 不得发布 `test.written`；动态 test path allowed/artifact 集合从 WAL 持久化 `pre_dirty_snapshot` 与 post 内容身份比较，不可只做路径集合差；crash recovery 复用 persisted snapshot；不混入 unchanged Human dirty files

### AC-FR0190-07

  - collection 口径（第 6 项）：所有 `tests/**/*.py`（含 conftest/helper）可 checkpoint，但 collect-only 只对 `test_*.py`/`*_test.py` test modules；只有 helper 时 fail closed；conftest 不得被单独判 no-tests

### AC-FR0190-08

  - host project command（第 7 项）：contract 中相对 `.venv/bin/python{,3}` 在宿主 worktree 不存在时，只能回退当前 Runtime 的 venv `sys.executable`，不得系统 Python；项目自有 venv 存在时优先

### AC-FR0190-09

  - rollback evidence（第 8 项）：仅 M-TEST `stub_gap`->M-DESIGN 清 stale failure，`scope_overflow` 等语义回退仍保留 evidence

### AC-FR0190-10

  - 覆盖范围（第 10 项）：物化合同覆盖 Scribe TRIAGE/DRAFT/RESPOND、Sage、Lex、Archer DRAFT、Prism design/test review、Shield WRITE，以及 Runtime validate/checkpoint/publish/collect/run/red/commit/seal

## FR-0200 可休眠与崩溃恢复

### AC-FR0200-01

  - 每个 phase 边界都是事件，重启从 lineage + 事件回放恢复到精确 phase，不重跑已完成的 task（BS-15）：M-IMPL 期间 `kill -9` 打断后 `trac run` 重启，事件流不重复出现已完成的 `red.checkpointed`/`green.committed`/`task.completed`，从打断的 phase 继续

### AC-FR0200-02

  - R ref 不可变（FR-0070）保证崩溃后 lineage 证据不丢失：崩溃重启后 `refs/trac/rgr/{run}/{task}/{attempt}/red` 仍指向崩溃前的同一 SHA
  - G commit 的 trailers 保证崩溃后 R-G 绑定可重建：崩溃重启后 `git log --format='%B' -1 <G>` 仍含 `Tracks-Task` / `Tracks-Attempt` / `Tracks-R` trailer，可重建 R-G 绑定
  - 崩溃 reconcile 承 v0.2 物化合同：受控 diff 回灌后视图终态清理

## NFR-0010 M-IMPL 控制流维持 kernel 纯函数边界

### AC-NFR0010-01

  - M-IMPL 子状态机的显式控制流分支（BASELINE/PLANNING/ISLAND_GATE_1/PRISM_PLAN/TASK_DISPATCH/RED/RED_GATE/RED_CHECKPOINT/PRISM_RED/GREEN/GREEN_GATE/GREEN_COMMIT/REFACTOR/REFACTOR_GATE/TASK_REVIEW/PRISM_FINAL/TASK_DONE/ISLAND_GATE_2/DIAGNOSE/SHIELD_FIX/EXIT）在 `decide()`/`project()` 内仍为纯函数：不碰 IO、不读 clock/env、不读文件系统

### AC-NFR0010-02

  - 所有非确定性只作为事件进入：collection 复跑、trace/reach 调用、判据包加载、worktree 操作、git commit/ref 创建等副作用归 executor，不归 kernel（承自 v0.1 NFR-02）；drop 投影表后从事件重建的 M-IMPL 状态与原状态一致

## NFR-0020 M-IMPL 事件维持 append-only 事件溯源

### AC-NFR0020-01

  - M-IMPL 全程事件（`stage.entered` / `baseline.frozen` / `taskgraph.committed` / `task.started` / `writelock.granted|released` / `red.checkpointed` / `prism.verdict(pass|revise)` / `green.committed` / `refactor.committed|no_change` / `verdict.failed(red_invalid|regression|budget|island|scope|test_defect|impl_defect)` / `test.committed` / `task.completed` / `stage.exited` / `stage.rolled_back`）均 append-only 写入 events 表，不改写既有行

### AC-NFR0020-02

  - 投影表可从事件完整重建（承自 D-02）：drop 投影表后 `trac status`/`trac report` 重建的 M-IMPL 状态与活动记录与原一致

## NFR-0030 dispatch 活动性可观测

### AC-NFR0030-01

  - `trac run` 必须为长时间 Agent 派发发出简洁、已 flush 的控制台活动：开始输出时间戳/Agent/stage(substate)/task(attempt)，完成输出状态/失败与耗时；不得流式输出海量 Agent stdout
  - 生产 Runtime Agent 派发不设 elapsed-time 超时（D-11 取消协议）；活动性由 Runtime/operator 观测，显式 Human Ctrl-C 取消并清理进程组

### AC-NFR0030-02

  - ≤3 次失败后 `trac run` 与 `trac status` 必须暴露 attempt 计数 + 失败类 + 原因；`trac status` 输出含当前 attempt 计数与最近失败类

### AC-NFR0030-03

  - Human 可运行 `trac retry` 追加 `human.retry` 事件、清除升级 gate、重置一份新的 ≤3 attempt 预算、保留失败证据，且不自动重新派发（承自 flow §17.3）
  - `trac retry` 在非 escalation 状态下拒绝（退出非零）并提示原因；`trac retry --clear-evidence` 仅在 escalation 或 clean active 状态下允许
