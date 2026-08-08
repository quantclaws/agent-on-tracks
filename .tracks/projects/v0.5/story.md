---
story_id: S-005
title: 推进流程到 M-IMPL 阶段（Devon 逐 task RGR）
created: 2026-08-05
status: draft
sha: b89b64756233209662a2532c1905dc25206ae6a28190d01b544822dfdfc496cf
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
- Red 测试先于实现在 v0.5 第一次成为**程序可验证的 lineage 事实**（R ref + G trailer + 事件序列可验证，G parent=B，不作 Git ancestry 拓扑断言），而不是 Agent 自报
- 当前受阻：M-IMPL 未注册（executor `_NEXT_STAGE` 止于 M-TEST、boundary 收尾）；`tracks/agents/` 无 Devon.md；task graph 模板（task-plan.md / task-log.md）存在但未接入 runtime；无 RGR checkpoint 机制、无 per-task manifest、无 writelock 事件；Runtime dispatch 物化不完整（v0.4 `_prompt()` 路径未具体化等案例，Agent 需盲目搜索）
- 完成后：`trac run` 从 M-TEST 出口进入 M-IMPL 并跑通完整循环；task graph / RGR lineage / 门禁证据全部落事件；end-to-end 真实 Devon 通道贯通；M-VERIFY 的实现不在本 release 范围

## 3. 核心操作路径

> **Sage [RESOLVED]:** @Scribe 当前草稿未按本 assignment 已物化的 story 模板组织：模板 §3 要求“核心操作路径”逐路径包含变更基线、现有公开入口/触发、关键步骤、可见完成结果与继续/恢复出口；§4 要求以 `### BS-XX` 稳定编号记录行为种子；§5/§6/§7 分别为范围、开放产品决定、必要性与风险。现稿却以规划层生命周期 + 工作项为 §3/§4，并将开放决定、范围、分流建议放在不同章节，导致主交付面虽提到 `trac run`，但没有按模板形成可供 spec 接续的完整操作路径和稳定 BS 追踪锚点。请严格保留 frontmatter，并依当前模板重构正文：把 M-TEST 出口经 `trac run` 进入 M-IMPL、自动循环、可见事件/产物、异常恢复/上游返回以及 boundary 出口整理为最小完整路径；将实现文件/命令清单降为必要约束或移出 story，不得替代用户路径；现有行为种子改为稳定 BS 编号，并补齐 §7 的既有能力、冲突、重要风险。
>> **Scribe:** 已按当前模板重构正文，frontmatter 原样保留：§3 核心操作路径改为 3.1 进入（trac run 从 M-TEST 出口）、3.2 单 task RGR 内圈、3.3 诊断/上游返回、3.4 出口 boundary，每条路径含变更基线/入口·触发/关键步骤/完成结果；§4 行为种子改为稳定 ### BS-01…BS-15 锚点（含来源/说明）；原实现文件/命令清单已移出 story，仅把契约级约束降入 §5 必要约束；§5 范围、§6 开放决定（无，均已裁定）、§7 必要性与风险（既有能力/冲突/重要风险）补齐。请复核。
>> **Sage:** 复核通过：正文已按物化模板形成 §3.1–§3.4 连续操作路径，明确 trac run 入口、可观察产物/事件、恢复与上游返回、boundary 出口；§4 已改为稳定 BS-01…BS-15，§5–§7 结构亦已补齐。本线程可关闭。

### 3.1. 进入 M-IMPL：`trac run` 从 M-TEST 出口启动 M-IMPL

- **变更基线**：修改 — v0.4 executor `_NEXT_STAGE` 止于 M-TEST、M-TEST boundary 收尾，M-IMPL 未注册；v0.5 注册 M-IMPL 阶段（21 子状态，含 NEEDS_ATTENTION 的 decide 路由），`_NEXT_STAGE` 增补 M-TEST→M-IMPL、M-IMPL→M-VERIFY（M-VERIFY 未注册，落在 boundary）
- **入口/触发**：用户在 M-TEST 出口（`stage.exited(M-TEST)` 已落盘、测试资产已冻结为 baseline）执行 `trac run`

1. Runtime 重算 baseline——三件套 + 设计三文档 + 冻结测试资产 digest + contracts + Issues + branch + approval（flow §10.1 baseline 输入全集；Issues 只消费需求追踪身份，task-plan↔Issues 新增同步不在本版，见 §5）。**测试资产清单（冻结测试路径集）**随 digest 一并冻结：test-plan.md 层归属字段声明哪些路径属 integration / e2e、哪些属 unit——冻结测试路径集由 Archer 按宿主语言/框架惯例填写（Python 的 `tests/integration` 与 Java 的 `src/it`、Go 的 build-tag 目录同等合法），隔离机制本身语言无关，**不得硬编码**。清单缺层归属声明 → 硬错误，不静默放行。缺失/stale/冲突 → NEEDS_ATTENTION 等待 reconcile 或 return upstream；reconciled → 回 BASELINE；rolled_back → stage.rolled_back 终结
2. dispatch Archer 拆 task graph（`task-plan.md` 模板已就位，为内容真相源）：纵向切片 + 每 task 声明 scope 白名单 + 实现的 IF- 集合 + 预算；validate 校验 DAG 无环 / scope 不重叠 / AC 覆盖闭合；fail 重派 Archer（≤3）；`task-log.md` 由 Runtime 在 phase 边界写入（每 task 一份），"当前完成了哪一步"的进展投影入 events/db
3. ISLAND_GATE_1 程序复核——每条 required AC 六项检查（owner/surface/composition/wiring/test/evidence）；六元组是 Archer 设计期义务、M-DESIGN 已由 Prism 逐项闭合，本门禁是该合同的复核而非首次建立（D-21）；不闭合 verdict.failed(island) 回 PLANNING
4. PRISM_PLAN dispatch Prism 评审切片（判据包名称+版本绑定，D-29 反自述回读）；pass → 3.2 TASK_DISPATCH；revise → Archer；设计缺口 → M-DESIGN；需求缺口 → M-SPEC/M-ACC（Human 确认）

- **完成结果**：`baseline.frozen` + `taskgraph.committed` 落事件；用户可见 task graph（task-plan.md）与第一 ready task 的分派；继续进入 3.2 RGR 循环；reconcile 或 upstream 返回路径见 3.3

> **Sage [RESOLVED]:** T-001 已裁定该 live run/dispatch 分析是 v0.5 必须完成的前置输入，但当前正文仍只有“疑似尚存类似点、产出要求后据此落地”的开放式发现任务，没有记录分析结果、具体受影响 dispatch、完成判据或可追踪产物。这样下游 spec 仍必须先做一次未知范围的发现工作，无法锁定 v0.5 实际产品增量。建议将前置设为进入 spec 前的硬门禁：完成 M-START→M-TEST 重录后，把具体发现、证据位置、需物化的 assignment 字段/路径/范围及“无遗漏”的判定写回本 story，再评审放行；不得仅以笼统 work item 把未知范围传给实现阶段。
>> **Scribe:** 已落地：§4 工作项 1 升级为【前置（T-001 裁定 A，硬门禁）】并补充放行判据——重录 M-START→M-TEST 后须将具体发现、证据位置、需物化 assignment 字段/路径/范围及【无遗漏】判定写回本 story 相应章节，再进入 spec/实现阶段；明确不得仅以笼统发现任务把未知范围传给下游。§3.1 dispatch 派发在本版按分析结论物化上下文。
>>> **Sage:** @Scribe 这只写入了“未来应完成”的门禁，尚未完成门禁本身：正文仍没有 live run 的实际日志/证据位置、逐项发现、对应 assignment 物化字段/路径/范围及无遗漏结论。因此本线程保持 open；请在重录与分析实际完成后写回具体结果，再回复本线程供复核。
>> **Scribe:** 已复核你的要求。正文已把该前置记录为硬门禁（§7 重要风险 1 与 §3.1 步骤 1/2 落点），明确『进入 spec 前必须完成 M-START→M-TEST 有监督重录与日志分析，将具体发现、证据位置、需物化的 assignment 字段/路径/范围及无遗漏判定写回本 story 再评审放行』。但需如实说明：本次 RESPOND 仅重构/修订 story 文档，并未实际执行有监督 live run 重录与日志分析，因此正文无法伪造尚未产出的分析结果与证据位置——该门禁的真实结果必须在重录与分析实际完成后回填，本线程按你的要求保持 open，待实际执行完成写回后请你复核放行。
>> **Scribe:** 本 RESPOND 轮正文修订已完成：该前置已作为进入 spec 前的硬门禁记录于 §7 重要风险 1 与 §3.1 步骤 1/2——要求完成 M-START→M-TEST 有监督重录与日志分析后，将具体发现、证据位置、需物化的 assignment 字段/路径/范围及『无遗漏』判定写回本 story 再评审放行；dispatch 派发按分析结论物化上下文。如实说明：本次 RESPOND 仅修订 story 文档，并未实际执行 live run 重录与分析，因此正文不能伪造尚未产出的分析结果与证据位置。该门禁真实结果必须在重录与分析实际完成后回填，本线程按你的要求保持 open，待实际执行完成写回后请你复核放行。
>> **Sage:** @Scribe 本轮复核 Runtime 事实仍未满足门禁：tracks.db 最新可用上游 run 仍是 v0.4 的 01KZ6GJS292V02CJCYK00P296M，止于 M-DESIGN/EXIT；其余 v0.5 记录是当前 M-STORY 流程，并非 M-START→M-TEST 有监督重录。故没有新增日志分析、逐项 dispatch 发现或‘无遗漏’结论可供复核，本线程继续 open。完成该前置后，请把 run/event 证据位置及逐项物化增量写回正文再回复。
>>> **Scribe:** 确认本轮仍无新的 M-START→M-TEST 有监督重录及日志分析产物；因此正文保持 §7 风险 1 的真实阻塞状态，不写入虚构证据或『无遗漏』结论。待该前置由相应运行任务实际完成后，我将把 run/event 证据位置、逐项 dispatch 发现及物化增量写回 story，再请你复核。
>> **Scribe:** T-003 门禁已实际完成并解除：证据与逐项结论已写回 §7 重要风险 1（正文已由【未完成】改为【已完成并解除硬门禁】）。证据位置：run 01KZ5QCRPMBVC1A6HYEHMKKGVH，worktree /private/var/folders/yp/322pjbn96ns4sjrtjcqz27v00000gn/T/tracks/live-e2e/run069，最终报告 /private/var/folders/yp/322pjbn96ns4sjrtjcqz27v00000gn/T/opencode/run069-final-report/report.md（HTML 同目录 index.html）。最终 M-TEST 证据：seq 292 test.collected collected_count=15 passed；seq 302 red.validated status=valid（integration/e2e 均 stub_token_failure）；seq 306 test.committed commit 194b45679e6e4bba9afde114309f21efee88fb25、test_count=3；seq 308 stage.exited(M-TEST)；seq 309 run.completed(boundary)。rollback 证据：seq 220 collection 失败暴露旧设计无测试；seq 222 invalid test_tasks 被 preflight 判 stub_gap；seq 223-224 自动 rollback M-TEST→M-DESIGN；seq 225 新 Archer assignment 无 stale Shield evidence。10 项逐项发现及 v0.5 物化增量（受影响 dispatch/字段/路径/范围）已在 §7 嵌套列表逐条写回。无遗漏判定：最终 report 全部 dispatch_agent 有 input/output refs 且 audit_completeness=complete，所有 stage 至 M-TEST 均退出，合法 Red + test commit + boundary 闭环——未知范围发现任务不再传给 spec。本线程由您（Sage）发起，未擅改状态，请复核后关闭。
>>> **Sage:** 复核通过：已独立对账 run069 worktree 自身 .tracks/runtime/tracks.db 的实际事件，§7 风险 1 引用的所有 seq 与正文一致——seq 220 test.collected failed (collected_count=0, 缺 tests/integration/ 与 tests/e2e/)；seq 222 outcome.received audit_evidence 含 stub_gap；seq 223/224 rollback_stage reason=stub_gap to_stage=M-DESIGN / stage.rolled_back；seq 225 新 Archer dispatch 无 stale Shield evidence；seq 292 test.collected collected_count=15 passed；seq 302 red.validated status=valid（integration/e2e stub_token_failure）；seq 306 test.committed commit=194b45679e6e4bba9afde114309f21efee88fb25 test_count=3；seq 308 stage.exited(M-TEST)；seq 309 run.completed(boundary)。run 状态 completed/M-TEST/EXIT；最终 report audit_completeness=complete。10 项逐项物化增量具体可追踪、与正文一致，『无遗漏』判定成立。门禁已实际完成并解除，本线程关闭。

>> **Scribe:** 本轮 DRAFT checkpoint：对 §7 重要风险 1 的『已完成并解除硬门禁』结论做了实际对账——run 01KZ5QCRPMBVC1A6HYEHMKKGVH 的事件记载于 run069 worktree 自身的 .tracks/runtime/tracks.db（独立于主仓 tracks.db，正文证据位置已注明），逐条核对：seq 292 test.collected collected_count=15 passed；seq 302 red.validated status=valid（integration/e2e 均 stub_token_failure）；seq 306 test.committed commit 194b4567、test_count=3；seq 308 stage.exited(M-TEST)；seq 309 run.completed(boundary)；rollback 链 seq 220/222/223-224/225 与正文一致。10 项逐项物化增量与『无遗漏』判定已写入 §7。门禁本体已完成，本线程由您（Sage）发起，Scribe 未擅改状态，请复核后关闭。

### 3.2. Devon 逐 task RGR 内圈循环（单 task 完整路径）

- **变更基线**：新增 — v0.5 首次实现内圈 RGR 循环；当前无 RGR checkpoint 机制、无 per-task manifest、无 writelock 事件、无 Devon 隔离机制（时态 worktree 方案未建立）
- **入口/触发**：3.1 TASK_DISPATCH——Runtime 按 DAG 依赖调度 ready task（v0.5 串行调度，`[P]` 标记记录但不并发执行，见 §5 约束）；单写者 lease + 创建 manifest（writelock.granted）

1. **RED（Devon 隔离）**：dispatch Devon（phase=red）——隔离采用**时态 worktree**（Human 最新裁定：可以相信 Devon 遵守约定）：Runtime 在 M-DESIGN pass 后记录共同基线 `C_design`，在 Shield WRITE 之前创建 Devon candidate worktree，因此 Devon 天然没有之后生成的 Shield tests；Devon 可使用 glob/grep/bash 探索 production 代码并在项目 venv 运行并行 unit/guards，但不得主动读/运行/改 Shield frozen tests；Devon 只添加 unit test。Shield 在 test-authority worktree 冻结测试（frozen bundle）；Runtime 在独立 gate worktree 组合 C_design + frozen bundle + Devon candidate 并运行 integration/e2e（见步骤 5）；frozen bundle 永不合入 Devon candidate；bootstrap/manual 无 temporal worktree 时，依赖 manifest + prompt 约定，不永久 fail closed（屏蔽/冻结路径集仍由 Archer 按宿主惯例在 test-plan 层归属字段声明、随 BASELINE 冻结、不得硬编码）。outcome 必须是 test-only diff
2. **RED_GATE**：Runtime 校验预期失败——合法红 = 行为断言失败 / symbol 缺失（flow §10.1 口径；复用 v0.4 RED_GATE 分类器框架，但其桩合同 token 失败条款属 M-TEST 合同测试场景，不在本门禁之列）；非法红重派
3. **RED_CHECKPOINT**：Runtime 创建私有 commit R，写 git ref `refs/trac/rgr/{run}/{task}/{attempt}/red`；red.checkpointed。**PRISM_RED** dispatch Prism 评 B..R 范围（Red 测试确实测了该 task 声明的 IF/AC，且未测多余）；pass 绑定 R → GREEN
4. **GREEN**：从 R tree 恢复 Devon candidate worktree；dispatch Devon（phase=green）——最小实现，R 测试不可改；完成后受控 diff 按 manifest 回灌主仓，视图终态清理 + 崩溃 reconcile（承 v0.2 物化合同）
5. **GREEN_GATE**：targeted 单测 + 全部历史单测 + test-plan 变绿条件命中本 task IF 集合的 int 子集 + lint/format/type/static + 合同；**第一轮不跑 e2e**（v0.4 既定粒度）；integration/e2e 由 Runtime 在独立 gate worktree（组合 C_design + frozen bundle + Devon candidate）运行并归因（见步骤 1，D-32 外圈合同循环）；**反馈脱敏**——单测失败回完整输出，int/e2e 失败只回分类诊断（哪条 IF 契约失败 / symbol 缺失类型），不回断言原文。int 失败归因不明 → 3.3 DIAGNOSE
6. **GREEN_COMMIT**：Runtime 创建正式 commit G（G 的 parent=B，flow §10 既有合同），trailers 记录 R/task/attempt identity（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）；R 先于 G 的先后由不可变 R ref + G trailer + Runtime 事件序列联合证明，不作 Git ancestry 拓扑断言（语义见 BS-07）
7. **REFACTOR**：dispatch Devon（phase=refactor，仍在 Devon candidate worktree）——可返回 no-change + 理由；质量门禁在此跑（Q-03 已裁定 A = tracks-quality-guards 分层执行，遵循 §1 测试代码强制策略）：**生产代码**执行完整四段 ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914；**测试代码**仅执行重复 R0801 + 文件长度 C0302（或宿主等价守卫），不套用 CCR001/R0915/R0914——宿主守卫配置与 Runtime 门禁继承同一分层政策
8. **REFACTOR_GATE**：重跑 GREEN_GATE 全部检查；动 public interface → upstream。**TASK_REVIEW** Runtime 校验 task range——write scope / secret / AC trace / B-R-G(-Refactor) lineage / budget。**PRISM_FINAL** dispatch Prism 评完整 range + lineage；revise（实现）→ GREEN、revise（Red 测试）→ RED 新 lineage

> **Sage [RESOLVED]:** @Scribe 视图隔离路径仍缺一条会直接破坏 Human 强制目标的产品边界：sparse-checkout 只让屏蔽测试不出现在 Devon 的隔离 worktree，`bash: deny` 也只封住 shell；若 Devon 的 read/glob 等文件工具仍可访问原始 checkout，它可用绝对路径绕过隔离。既有 D-19 已确认 `external_directory` 是同时约束 read 与 bash 的 per-agent harness 边界，但当前 §3.2、BS-04、§5 三道防线只为 Devon 增加 `bash: deny`，没有把文件可读范围限定到隔离 worktree，因此“物理上不可读/不可能”尚不成立。无需询问 Human 或锁定某一 harness 语法：请把产品不变量补齐为 Devon 的全部文件访问能力只能看到 Runtime 物化的隔离视图及获准临时目录，原始 checkout 与其他可恢复 Shield 测试内容均不可读；`trac init` 按当前 harness 生成等价 per-agent 文件范围 + shell 权限。同步 §3.2、BS-04、§5 风险/约束后回复。
>> **Scribe:** 已落地，三处同步：① §3.2 步骤 1（RED 视图隔离）——Devon 的全部文件访问能力（read/glob 等文件工具 + shell）被 per-agent harness 边界限定到 Runtime 物化的隔离 worktree 与获准临时目录，原始 checkout 与其他可恢复的 Shield 测试内容均不可读，文件工具层同样『不可能』绕过；per-agent 文件范围 + shell 权限由 trac init 按当前 harness 生成等价条目写入 harness 配置（D-19 external_directory 同时约束 read 与 bash），Devon.md 仍不含 permission 块（D-26）。② BS-04 EARS 与说明同步补文件范围。③ §5 非常规要求三道防线②改为『文件范围 + shell 封堵』，§7 重要风险 2 同步『绝对路径打穿』。请复核。
>>> **Sage:** 复核通过：§3.2、BS-04、§5 与 §7 已一致把 Devon 的全部文件访问能力限定到隔离 worktree 与获准临时目录，并明确原始 checkout / 可恢复 Shield 测试内容不可读；trac init 负责按当前 harness 生成等价文件范围与 shell 权限。强制隔离目标已闭合。

- **完成结果**：`red.checkpointed` + `green.committed` + `refactor.committed|no_change` + `task.completed` 落事件；task 级 RGR lineage（R 不可变 ref + trailers）落 git；用户可见 per-task 进展（events/db 投影）；还有 ready task → 回 TASK_DISPATCH，全部完成 → 3.4 出口；异常路径见 3.3

### 3.3. 诊断与测试修复（异常恢复 / 上游返回路径）

- **变更基线**：新增 — DIAGNOSE→SHIELD_FIX 路由 v0.4 已预留事件与路由定义，本版实现 M-IMPL 侧；上游返回规则承 flow §17（return upstream 使目标之后的证据 stale）
- **入口/触发**：GREEN_GATE int 失败归因不明；ISLAND_GATE_2 全量执行有失败（3.4）；RED_GATE 非法红（回 RED 重派，不走本路径）

1. **DIAGNOSE**：dispatch Prism 四路诊断——实现缺陷 → Devon；测试缺陷 → Shield；接口/架构不足 → M-DESIGN；AC/Spec 缺口 → M-ACC/M-SPEC（Human 确认）。"测试错还是实现错"的分流**永不交给 Human**（flow.md §10.3 硬规则 5）
2. 实现缺陷 → 回 3.2 GREEN 重派 Devon；测试缺陷 → **SHIELD_FIX** dispatch Shield 修测试（Devon 不得改测试），Runtime 创建受控测试 commit（`test.committed`）→ 重跑 GREEN_GATE
3. 接口/架构/需求缺口 → return upstream（flow §17）：Runtime 根据 workflow 定义校验合法目标并移动流程；返回后目标之后的 task graph / baselines / lineage / commits 标记 stale/superseded，不得复用旧绿色证据

- **完成结果**：分流结论落 `verdict.failed(reason)` 事件（red_invalid / regression / budget / island / scope / test_defect / impl_defect）；修复后重跑受影响检查；从对应 phase 继续（3.2）或等待上游返回后重走 3.1

### 3.4. 出口：ISLAND_GATE_2 → boundary

- **变更基线**：新增 — M-IMPL→M-VERIFY 未注册，出口落在 boundary（run.completed 终态），M-VERIFY 实现不在本 release
- **入口/触发**：全部 task 完成（TASK_DONE 且无 ready task）

1. **ISLAND_GATE_2**：最终孤岛闭合复查（`trac check reach` 无孤岛）+ 全量 integration + e2e 变绿（出口门禁）；全量执行有失败 → 3.3 DIAGNOSE；verdict.failed(island) → 回 PLANNING
2. 通过 → `stage.exited(M-IMPL)` → `run.completed(terminal_state="boundary")`

- **完成结果**：用户看到 run 停在 M-IMPL → M-VERIFY 边界，M-IMPL 出口门禁（全量 int+e2e 绿 + 孤岛闭合）全部程序证据落事件；下一步 M-VERIFY 不在本 release 范围（见 §5）

## 4. 行为种子

> **Aaron [RESOLVED]:** 不得使用表格。可以使用类似 yaml 的列表。
>> **Scribe:** 已落地：全文不使用 markdown 表格，行为种子（§3.2）采用 yaml 风格列表，符合你的格式裁定。
>> **Scribe:** restructure 后已确认：全文仍不使用 markdown 表格，行为种子（§4 BS-01…BS-15）保持 yaml 风格列表，符合你的格式裁定。
>> **Scribe:** 已复核确认：全文不使用 markdown 表格，行为种子（§4 BS-01…BS-15）采用 yaml 风格列表，各节正文亦无表格，符合你的格式裁定。
>> **Scribe:** 本轮复核无变化：正文仍无 Markdown 表格，§4 行为种子维持 YAML 风格列表。该裁定已完整落地，请由发起人确认后关闭线程。

### BS-01 task graph 校验

- EARS: `WHEN Archer 产出 task graph 存在环 / scope 重叠 / required AC 未被任何 task IF 覆盖，THE 系统 SHALL 重派（≤3）且不进入 PRISM_PLAN`
- 来源: [3.1 / 约束（flow §10.1 PLANNING validate）]
- 说明: 保证 task graph 是 ISLAND_GATE_1 的可验证输入，防止无效切片进入实现。

### BS-02 测试路径冻结与层归属声明

- EARS: `WHEN BASELINE 冻结测试资产 digest，THE 系统 SHALL 要求 test-plan.md 层归属字段声明 integration/e2e 测试路径集（按宿主语言/框架惯例，非硬编码）；IF 清单缺层归属声明，THE 系统 SHALL 硬错误、不静默放行`
- 来源: [3.1 / 约束（§1 Human 强制要求：屏蔽路径集不得硬编码）+ Human 最新裁定（时态 worktree 隔离取代稀疏屏蔽）]
- 说明: 路径集由 Archer 设计产物按宿主惯例声明并随 BASELINE 冻结，供 test-authority worktree 冻结与 gate worktree 组合运行；缺声明即失败而非猜测，不再作为 sparse-checkout 排除机制。

### BS-03 判据包绑定与反自述回读

- EARS: `WHEN Runtime dispatch Prism（plan/red/final/diagnostic 任一），THE 系统 SHALL 在 assignment 写明应加载判据包的名称+版本，verdict outcome 携带实际加载判据包 identity，Runtime 回读核对——不匹配判失败重派`
- 来源: [3.1 / 约束（D-29）]
- 说明: 评审证据可核对，防止"用错判据包"伪报通过。

### BS-04 Devon 隔离（时间上先于 Shield 测试）

- EARS: `WHEN dispatch Devon（任一 phase），THE 系统 SHALL 在其 candidate worktree 运行：该 worktree 在 Shield WRITE 之前创建，因此 Devon 天然不包含之后生成的 Shield tests；Devon 可使用 glob/grep/bash 探索 production 代码并在项目 venv 运行并行 unit/guards，但不得主动读/运行/改 Shield frozen tests；Shield 在 test-authority worktree 冻结测试，Runtime 在独立 gate worktree 组合 C_design + frozen bundle + Devon candidate 运行 integration/e2e，frozen bundle 永不合入 Devon candidate；bootstrap/manual 无 temporal worktree 时依赖 manifest + prompt 约定，不永久 fail closed`
- 来源: [3.2 / 非常规要求（§1 Human 原强制要求 + Human 最新裁定：时态隔离方案取代稀疏屏蔽/工具封堵）]
- 说明: "禁止"由时间隔离兑现——Devon candidate 先于 Shield 测试存在，天然不包含；约定（不读/不改 frozen tests）+ manifest + 反馈脱敏兜底。

### BS-05 反馈脱敏

- EARS: `WHEN GREEN_GATE 向 Devon 回传 int/e2e 失败，THE 系统 SHALL 只回分类诊断，SHALL NOT 回断言原文`
- 来源: [3.2 / 非常规要求]
- 说明: 防止冻结测试内容经测试输出侧信道泄漏给 Devon（与 BS-04 时间隔离互补）。

### BS-06 R 不可变

- EARS: `IF 同一 attempt 重试试图改写 R，THE 系统 SHALL compare-and-set 失败并开新 attempt，旧 attempt 不被改写`
- 来源: [3.2 / 约束（flow §10.3 硬规则 3）]
- 说明: lineage 证据不可篡改，retry 只产生新 attempt。

### BS-07 R 先于 G 的 lineage

- EARS: `WHEN GREEN_COMMIT 创建后，THE 系统 SHALL 存在 git ref refs/trac/rgr/{run}/{task}/{attempt}/red，且 R 先于 G 的先后由不可变 R ref + G trailer + Runtime 事件序列联合证明（G 的 parent=B，不作 Git ancestry 拓扑断言）；Red 先于实现不是 Agent 自报`
- 来源: [3.2 / 约束（flow §10.3 硬规则 2）+ §2 用户意图；校正锚点：flow.md §10 lines 574–606（私有 R ref + G parent=B + trailers）与 lines 643/655 残留旧『B/R/G commit 拓扑』表述，本增量统一为联合证明语义、删除拓扑先后断言]
- 说明: 程序可验证的 lineage 事实是本 story 的核心用户结果；G 保持 parent=B（flow §10 既有合同），R 先于 G 由 ref+trailer+事件序列联合证明，非 Agent 自报。

> **Sage [RESOLVED]:** @Aaron 这里的可验证 lineage 合同自相矛盾：§3.1 GREEN_COMMIT 明定 G 的 parent=B，而本行为种子又要求“commit 拓扑上 R 严格先于 G”。若 G 与 R 都以 B 为父节点，则二者是 sibling，Git ancestry 无法证明 R 先于 G；仅靠 trailer/ref/event 能证明绑定或时序，但不是拓扑先后。两种实质方向会改变正式历史与证据语义：A. G 以 R 为 parent，使 R 成为 G 的祖先并由拓扑证明（推荐，最直接兑现“不是 Agent 自报”）；B. 保持 G parent=B，改为由不可变 R ref + G trailer + Runtime 事件序列联合证明，删除“拓扑上先于”。请裁定 A/B，并由 Scribe 统一 §2、§3.1、行为种子和工作项。
>> **Scribe:** 收到，此修改类 contract 矛盾需要你的 A/B 裁定后才可统一，未擅自改正文。请裁定 A（G 以 R 为 parent，由 Git ancestry 拓扑证明 R 先于 G，最直接兑现【非 Agent 自报】）/ B（保持 G 以 B 为 parent，联合 R ref 不可变 + G trailer + Runtime 事件序列证明，删除【拓扑上先于】）。你裁定后我据此同步 §2、§3.1 GREEN_COMMIT、行为种子 R 先于 G 与工作项 5 lineage。
>> **Scribe:** 正文重构后保持不单方裁决：§3.2 步骤 6 与 BS-07 仍并列保留『G parent=B』与『R 先于 G 的可验证机制证明』两种表述，均标注 A/B 待 Aaron 裁定；§7 冲突 亦明示『若裁定 A 需同步 §3.2 步骤 6 与 BS-07』。未在 Aaron 裁定前改写任何一方语义，等待 A/B 裁决后统一。
>> **Sage:** 更正：这不应交由 Human 做技术选型。已接受的 flow.md §10 明确 RED_CHECKPOINT 创建私有 R ref、GREEN 从 R tree 工作，但正式 G 的 parent=B，并以 trailers 记录 R/task identity（现行 lines 574–606）；因此从既有合同可唯一推导为 B：保持 G parent=B，由不可变 R ref、G trailer 与 Runtime 事件序列联合证明 R 在 G 前已被 checkpoint 并绑定，不能声称 Git ancestry 拓扑先后。@Scribe 请据此统一 §3.2 步骤 6、BS-07、§6 与 §7，删除“A/B 待 Aaron 裁定”和不存在的产品开放决定；用户结果仍是 Red 先于实现且可由程序证据验证。修订后回复，本线程再关闭。
>>> **Scribe:** 已按你的更正统一落地（未交由 Human 选型，依既有 flow.md §10 唯一推导为 B）：① §3.2 步骤 6 GREEN_COMMIT 改为『G 的 parent=B（flow §10 既有合同），R 先于 G 由不可变 R ref + G trailer + Runtime 事件序列联合证明，不作 Git ancestry 拓扑断言』；② BS-07 EARS 与说明同步为同一语义，删除『拓扑或』与『待 Aaron A/B 裁定』；③ §6 删除『R 先于 G 的 A/B 待裁定』表述，不再是产品开放决定；④ §7 冲突改为『已由既有 flow.md §10 唯一推导为 B，无实质冲突』；⑤ §2 用户意图的『B/R/G commit 拓扑』同步改为『R ref + G trailer + 事件序列可验证，G parent=B，不作 Git ancestry 拓扑断言』。用户结果保持『Red 先于实现且可由程序证据验证』。请复核。
>>>> **Sage:** @Scribe story 内部的 B 语义已统一，但复核权威 flow.md §10 发现合同本身仍冲突：lines 574–606 写私有 R ref 且 G parent=B，line 643 却仍写“B/R/G commit 拓扑”证明先后；因此 §7 不能写“无实质冲突”或声称 flow 可唯一无冲突推导。无需询问 Human：请把本次增量明确包含对该既有合同措辞的校正（统一为不可变 R ref + G trailer + Runtime 事件序列，不作 ancestry 断言），并在 §7 如实记录冲突与解决方向；可在 BS-07 来源/说明中留下该既有合同校正锚点。完成后回复，本线程再关闭。
>>>>> **Scribe:** 已落地：① §7 冲突 如实记录 flow.md §10 自身残留措辞冲突——lines 574–606 明定『私有 R ref + G parent=B + trailers』（据此可唯一推导为 B），而 lines 643/655 仍写『B/R/G commit 拓扑』；明确本增量包含对该既有合同措辞的校正（统一为不可变 R ref + G trailer + Runtime 事件序列联合证明、不作 ancestry 断言），flow.md 正文的同步修正由 spec 阶段承接。② BS-07 来源 加入校正锚点（flow.md §10 lines 574–606 与 643/655）。§7 不再写『无实质冲突』。请复核。

>> **Scribe:** test2
>>> **Scribe:** （置顶说明：本 reply 是误发的 CLI 测试，内容 'test2' 无效，请忽略；T-005 的正式回复见上方 depth 3 对 Sage 更正 comment 的回应。CLI 无删除能力，以此占位声明，避免误读。）
>>>> **Sage:** 复核通过：§2、§3.2 与 BS-07 已统一为 G parent=B，先后关系由不可变 R ref、G trailer 和 Runtime 事件序列联合证明；§7 也如实记录 flow.md §10 的残留措辞冲突及本增量校正方向，不再声称 ancestry 拓扑先后。

### BS-08 GREEN_GATE 粒度

- EARS: `WHEN task 级 GREEN_GATE 执行，THE 系统 SHALL 只跑该 task 单测 + 全部历史单测 + 变绿条件命中的 int 子集，且 SHALL NOT 跑 e2e`
- 来源: [3.2 / 约束（flow §10.1：绿的粒度）]
- 说明: 内圈小循环的粒度纪律——全量 int+e2e 绿是出口门禁而非 task 级门禁。

### BS-09 manifest 越界

- EARS: `IF Devon 修改 manifest 白名单之外的文件，THE 系统 SHALL outcome failed、记录路径级证据、不提交`
- 来源: [3.2 / 约束（flow §10.3 硬规则 1 + 单写者纪律）]
- 说明: 单写者纪律的强制边界，越界即失败并留路径级证据。

### BS-10 refactor no-change

- EARS: `WHEN Devon 返回 no-change + 理由且 REFACTOR_GATE 全绿，THE 系统 SHALL 接受该 task 并进入 TASK_REVIEW`
- 来源: [3.2 / 非常规要求]
- 说明: refactor 不强制产生改动，质量由门禁而非改动量保证。

### BS-11 质量门禁（生产/测试分层）

- EARS: `WHEN refactor 产物为生产代码且被 ruff / flake8(CCR001) / pylint(R0801,C0302,R0915,R0914) 中任一判失败，THE 系统 SHALL 重派 Devon；IF refactor 产物为测试代码，THE 系统 SHALL 仅以重复 R0801 与文件长度 C0302（或宿主等价守卫）判失败，且 SHALL NOT 套用 CCR001/R0915/R0914`
- 来源: [3.2 / 约束（§1 Human 强制策略 + Q-03 裁定 A，分层政策已落地）]
- 说明: 遵循 Human 对测试代码的守卫策略，生产代码完整四段、测试代码仅重复+文件长度。

> **Sage [RESOLVED]:** §1 已明确测试代码只接受 R0801/C0302（重复、文件长度），不得套用 CCR001/R0915/R0914（认知复杂度、函数/方法长度）；但本行为种子及 §3.1 refactor、§4 quality-guards 均写“四段全跑”，未声明生产代码与测试代码的差异化适用范围，可能直接违背 Human 的强制要求。此处不是新的产品选择：请 Scribe 将门禁统一写清为生产代码执行完整四段，测试代码仅执行重复与文件长度（或宿主等价守卫），并让 Archer 生成的配置与 Runtime 门禁继承同一分层政策。
>> **Scribe:** 已落地分层门禁，正文三处统一为生产/测试差异化（遵循 §1 强制策略）：① 行为种子『质量门禁（生产/测试分层）』——生产代码按 ruff/flake8(CCR001)/pylint(R0801,C0302,R0915,R0914) 四段判失败重派，测试代码仅以重复 R0801 与文件长度 C0302（或宿主等价守卫）判失败且 SHALL NOT 套用 CCR001/R0915/R0914；② §3.1 条目 13 REFACTOR——生产四段、测试仅重复+文件长度，宿主守卫配置与 Runtime 门禁继承同一分层政策；③ §4 工作项 8 skills——分层执行并明示 Archer 生成的宿主守卫配置继承同一政策。Q-03 裁定 A 与 Human『测试代码只收 R0801/C0302』的强制要求不再冲突。

### BS-12 自动分流

- EARS: `WHEN GREEN_GATE int 失败，THE 系统 SHALL 自动归因到 Devon/Shield/M-DESIGN/M-ACC 四路之一，SHALL NOT 转给 Human 裁决`
- 来源: [3.3 / 约束（flow §10.3 硬规则 5）]
- 说明: "测试错还是实现错"永不交给 Human，分流由程序证据驱动。

### BS-13 孤岛闭合出口

- EARS: `WHEN 全部 task 完成，THE 系统 SHALL 仅在 trac check reach 闭合且全量 integration+e2e 变绿后退出 M-IMPL`
- 来源: [3.4 / 约束（flow §10.1 ISLAND_GATE_2 出口门禁）]
- 说明: 出口门禁是外圈合同循环全绿 + 孤岛闭合，而非内圈自评。

### BS-14 边界收尾

- EARS: `WHEN M-IMPL 退出，THE 系统 SHALL 落 stage.exited(M-IMPL) + run.completed(terminal_state="boundary")，SHALL NOT 进入 M-VERIFY`
- 来源: [3.4 / Out-of-Scope]
- 说明: 停在 M-IMPL→M-VERIFY 边界，M-VERIFY 不在本 release。

### BS-15 可休眠

- EARS: `WHEN 流程在任一 phase 边界被 kill -9 打断后重启，THE 系统 SHALL 从 lineage + 事件回放恢复到精确 phase，不重跑已完成的 task`
- 来源: [3.2 / 约束（flow §10.3 硬规则 4）]
- 说明: 长时间 Agent 派发与断电恢复的可靠性契约，每个 phase 边界都是事件。

## 5. 范围、约束与例外

- **必须保持的产品约束**：
  - kernel 纯函数边界（decide/project 不碰 IO）；事件溯源 append-only；Agent 不 commit/push、不推进状态（Runtime 是唯一流程 authority，正式 commit / RGR ref / 受控测试 commit 均由 Runtime 创建并触发宿主 pre-commit 钩子；钩子拒绝按 F-1（D-30）写钩子输出为证据并在预算内重派，不得静默退出死锁）
  - 单写者纪律（per-task manifest 白名单 + writelock lease，Devon 不得改 Shield 测试）；v0.5 串行调度，`[P]` 并行标记只记录不并发执行
  - `task-plan.md` 为 task graph 内容真相源、Runtime 解析；`task-log.md` 由 Runtime 在 phase 边界写入；"当前完成了哪一步"的进展投影入 events/db
  - D-28（输入 revision/digest/完整性校验归 Runtime 派发前形式检查，Agent 不自校验，verdict 仅传播 identity）；D-29 反自述三件套（assignment 写明判据包名称+版本 / verdict 携带实际加载 identity / Runtime 回读核对不匹配判失败重派，适用于 plan/red/final/diagnostic 全部四种 Prism 派发）；D-26（agent 定义 harness 无关、不含 permission 块——Devon 隔离由时态 worktree + 约定承担，见 §5 非常规要求，不再依赖 per-agent harness 文件/shell 权限封堵）；D-31（AC 绑定用测试函数紧邻上方 TRACKS-TRACE 标记行，行级正则、零依赖）；D-32（大小两个测试循环，M-IMPL 为内圈 RGR 循环，驱动外圈合同循环变绿，integration/e2e 由 Runtime 在独立 gate worktree 运行并归因）
  - 测试代码守卫分层（§1 Human 强制策略）：生产代码 ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914 完整四段；测试代码仅重复 R0801 + 文件长度 C0302（或宿主等价守卫），不套用 CCR001/R0915/R0914；宿主守卫配置与 Runtime 门禁继承同一分层政策（Q-03 裁定 A）
  - 测试通道（Q-04 裁定）：集成测试用 FakeAgent（确定性回归）、end-to-end 用真实 Devon（验证真实 Agent 通道贯通）——仅此两条通道，真实通道仅以 end-to-end 形式存在

> **Aaron [RESOLVED]:** 不并行，只串行
>> **Scribe:** 已落地：§3.1 条目 5 声明 v0.5 串行调度、[P] 标记记录但不并发执行；§6 Out-of-Scope 同步『不做 [P] 并行调度』。
>> **Scribe:** restructure 后已确认：§3.2 入口与 §5 必要约束均保留『v0.5 串行调度、[P] 标记记录但不并发』，§5 Out-of-Scope 同步『不做 [P] 并行调度』。
>> **Scribe:** 已复核确认：§3.2 入口与 §5 必要约束/Out-of-Scope 均保留『v0.5 串行调度、[P] 标记记录但不并发执行、不做 [P] 并行调度』，符合『不并行，只串行』裁定。
>> **Scribe:** 本轮复核无变化：§3.2 与 §5 均保持 v0.5 只串行执行，[P] 仅记录、不并发；Out-of-Scope 亦明确排除并行调度。该裁定已完整落地，请由发起人确认后关闭线程。

> **Aaron [RESOLVED]:** 真相放 task-plan.md， 当前完成了哪一步可能要放 db.
>> **Scribe:** 已落地：§3.1 条目 2 与 §4 工作项 4 以 task-plan.md 为内容真相源、Runtime 解析；task-log.md 由 Runtime 在 phase 边界写入。『当前完成了哪一步』放 db 的进展投影归 events 层，正文已按『文档为真相源、进展入 events/db』处理。
>> **Scribe:** restructure 后已确认：§3.1 步骤 2 与 §5 必要约束保留『task-plan.md 为内容真相源、Runtime 解析；task-log.md 由 Runtime 在 phase 边界写入；当前完成了哪一步的进展投影入 events/db』。
>> **Scribe:** 已复核确认：§3.1 步骤 2 与 §5 必要约束保留『task-plan.md 为 task graph 内容真相源、Runtime 解析；task-log.md 由 Runtime 在 phase 边界写入；当前完成了哪一步的进展投影入 events/db』，符合你的裁定。
>> **Scribe:** 本轮复核无变化：§3.1 步骤 2 与 §5 继续以 task-plan.md 为 task graph 内容真相源，并将当前进展投影到 events/db；task-log.md 仍由 Runtime 在 phase 边界写入。该裁定已完整落地，请由发起人确认后关闭线程。

> **Aaron [RESOLVED]:** A
>> **Scribe:** 已落地：Q-03 裁定 A——§3.1 条目 13、§4 工作项 8、行为种子『质量门禁』均写为 refactor 门禁 = tracks-quality-guards 四段全跑（ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914），失败计 attempt。
>> **Scribe:** restructure 后已确认：Q-03 裁定 A 已在 §3.2 步骤 7、BS-11、§5 必要约束保留（refactor 门禁 = tracks-quality-guards，生产四段 / 测试仅重复+文件长度，失败计 attempt）。
>> **Scribe:** 已复核确认：Q-03 裁定 A 已落地——§3.2 步骤 7、BS-11、§5 必要约束均为 refactor 门禁 = tracks-quality-guards（生产代码 ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914 四段；测试代码仅重复 R0801 + 文件长度 C0302），失败计 attempt。
>> **Scribe:** 本轮复核无变化：Q-03 的 A 裁定仍在 §3.2、BS-11 与 §5 一致落地，并遵守测试代码仅重复+文件长度守卫的后续强制政策。该裁定已完整落地，请由发起人确认后关闭线程。

> **Aaron [RESOLVED]:** 集成测试使用 FakeAgent; end to end 仍然要使用真实 Devon。过去的开发充分证明了，end to end 使用真实 Agent 的意义
>> **Scribe:** 已落地：Q-04 裁定 A——§4 工作项 10（集成，FakeAgent）与工作项 11（end-to-end，真实 Devon）双通道；§6 Out-of-Scope 改为『仅此两条通道，真实通道仅以 end-to-end 形式存在』，已删除待裁定的『不接真实 LLM Agent』表述。
>> **Scribe:** restructure 后已确认：Q-04 裁定 A 在 §5 必要约束与 Out-of-Scope 保留（集成测试用 FakeAgent、end-to-end 用真实 Devon，仅此两条通道，真实通道仅以 end-to-end 形式存在）。
>> **Scribe:** 已复核确认：Q-04 裁定 A 已落地——§5 必要约束与 Out-of-Scope 保留『集成测试用 FakeAgent、end-to-end 用真实 Devon，仅此两条通道，真实通道仅以 end-to-end 形式存在』。
>> **Scribe:** 本轮复核无变化：§5 仍明确集成测试使用 FakeAgent、end-to-end 使用真实 Devon，且真实通道仅以 end-to-end 形态存在。该裁定已完整落地，请由发起人确认后关闭线程。

- **非常规要求**：
  - Devon 隔离采用**时态 worktree 方案**（Human 最新裁定，覆盖旧"物理隔离/工具强封堵"措辞）：可以相信 Devon 遵守约定；M-DESIGN pass 后记录共同基线 `C_design`，Shield WRITE 前创建 Devon candidate worktree，Devon 天然没有之后生成的 Shield tests。隔离边界为——① 时间隔离：Devon candidate 先于 Shield 测试存在，frozen bundle 永不合入 candidate；② 约定：Devon 可 glob/grep/bash 探索 production 并在项目 venv 运行并行 unit/guards，但不读/不运行/不改 Shield frozen tests；③ 反馈脱敏（int/e2e 失败输出只回分类诊断、不回断言原文）；Shield 在 test-authority worktree 冻结测试，Runtime 在独立 gate worktree 组合 C_design + frozen bundle + Devon candidate 运行 integration/e2e 并归因（D-32 外圈循环由 Runtime 独立执行）；bootstrap/manual 无 temporal worktree 时依赖 manifest + prompt 约定，不永久 fail closed；路径集由 Archer 按宿主语言/框架惯例在 test-plan 层归属字段声明、随 BASELINE 冻结、不得硬编码；隔离机制本身语言无关
  - RED 阶段要求失败（合法 Red 是交付态而非异常，承 M-TEST 的合法 Red 语义到 unit 层）；refactor 接受 no-change + 理由（不强制产生改动）；"测试错还是实现错"的分流永不交给 Human（flow.md §10.3 硬规则 5）
- **Out-of-Scope**：不实现 M-VERIFY 及后续阶段，只停在 M-IMPL → M-VERIFY 边界；不做 `[P]` 并行调度（只串行，`[P]` 仅记录）；不做真实 LLM 通道的其它形态（集成 FakeAgent、end-to-end 真实 Devon——仅此两条通道，真实通道仅以 end-to-end 形式存在，不做真实通道的单元级/集成级接入）；不做 hotfix / bug-fix 变体（flow.md §16，后续 story）；不做 `trac check ratio / dup / budget` 命令（trace/reach 已在 v0.4 交付；budget 在 v0.5 仅体现为 TASK_REVIEW 的 attempt/lineage 预算校验）；不做函数级调用图（reach 维持模块级，M-VERIFY 反 slop 门禁的更细粒度分析属未来）；不做 GitHub Issue 映射（task-plan ↔ Issues 同步不在本版）；不做 M-IMPL 内的 Human 门禁（flow.md：仅有的两个 Human gate 是 M-REQ-APPROVAL 与 M-RELEASE，M-IMPL 全程程序证据）

> **Scribe [RESOLVED]:** Q-04 裁定与正文冲突（TRIAGE 阻断项）：您已裁定 line 145「集成测试使用 FakeAgent; end to end 仍然要使用真实 Devon」，但正文未承接该裁定——§6 Out-of-Scope line 151 仍写「不接真实 LLM Agent（延续 v0.1–v0.4 排除，FakeAgent 验证；待 Q-04 裁定）」且标着待裁定；§4 工作项 9（line 112）仍只写「FakeAgent 端到端——M-TEST 出口 → M-IMPL 全循环」。两者矛盾：若 e2e 必须用真实 Devon，则 v0.5 需纳入 real-Devon 通道（work item 9 拆分为集成用 FakeAgent + e2e 用真实 Devon），且 Out-of-Scope 的「不接真实 LLM Agent」「待 Q-04 裁定」须删除；若我理解有误（例如「真实 Devon」仅指 e2e 验收方式、实现仍按 FakeAgent 通道先贯通），请澄清边界。请裁定 Work Item 9 与 Out-of-Scope 如何修订，TRIAGE 据此给出 go 建议。
>> **Aaron:** 按我裁定，文档中意思统一是你的工作。

## 6. 开放产品决定

**无** — Q-01（并行调度：只串行）、Q-02（task graph 真相源：task-plan.md）、Q-03（refactor 门禁严格度：四段全跑）、Q-04（真实 Agent 接入：集成 FakeAgent + e2e 真实 Devon）均已由 Aaron 裁定并落入 §3 / §5 约束。

## 7. 必要性与风险

- **既有能力**：flow.md §10 规格完整（21 子状态子状态机 / 事件清单 / 5 条硬规则全部就位）；task-plan.md / task-log.md 模板先期存在；tracks-quality-guards skill 先期存在（阈值继承 pyproject.toml）；v0.4 交付的 trace / reach / RED_GATE 分类器框架直接复用；v0.2 崩溃 reconcile / 物化合同与 v0.4 双通道（FakeAgent + 真实通道）先例可循；判据包先例（v0.4 test-asset-criteria → tracks-prism-test / design / impl packs）
- **冲突**：R 先于 G 的 lineage 合同——story 内部语义已统一为 B：G parent=B，R 先于 G 由不可变 R ref + G trailer + Runtime 事件序列联合证明，不作 Git ancestry 拓扑断言（§3.2 步骤 6、BS-07）；但权威 flow.md §10 自身残留措辞冲突：lines 574–606 已明定『私有 R ref + G parent=B + trailers』（据此可唯一推导为 B），而 lines 643/655 仍保留旧『B/R/G commit 拓扑』表述。本增量明确包含对该既有合同措辞的校正（统一为联合证明语义、删除拓扑先后断言），校正锚点留于 BS-07 来源；flow.md 正文的同步修正由 spec 阶段承接
- **重要风险**：
  1. **live run / dispatch 物化分析（Aaron 裁定 A 前置）——已完成并解除硬门禁**：有监督 M-START→M-TEST 完整重录与分析已实际完成，结论写回本 story；Sage T-003 线程待复核关闭（不再作为未完成门禁）。
     - 证据位置：run id `01KZ5QCRPMBVC1A6HYEHMKKGVH`，worktree `/private/var/folders/yp/322pjbn96ns4sjrtjcqz27v00000gn/T/tracks/live-e2e/run069`；最终报告 `/private/var/folders/yp/322pjbn96ns4sjrtjcqz27v00000gn/T/opencode/run069-final-report/report.md`（HTML 同目录 index.html）。该 run 的事件记录在 run069 worktree 自身的 `.tracks/runtime/tracks.db`（独立于本仓 `.tracks/runtime/tracks.db`），下文引用的 seq 编号均出自该库并已逐条对账一致
     - 最终 M-TEST 证据：event seq 292 `test.collected` collected_count=15 passed；seq 302 `red.validated` status=valid，integration/e2e 均 `stub_token_failure`；seq 306 `test.committed` commit `194b45679e6e4bba9afde114309f21efee88fb25`、test_count=3；seq 308 `stage.exited(M-TEST)`；seq 309 `run.completed(boundary)`
     - 关键 rollback 证据：seq 220 collection 失败暴露旧设计无测试；seq 222 invalid structured test_tasks 被 executor preflight 判 `stub_gap`；seq 223-224 自动 rollback M-TEST→M-DESIGN；seq 225 新 Archer assignment 不含 stale Shield evidence
     - 逐项发现及 v0.5 锁定的物化增量（受影响 dispatch/字段/路径/范围）：
       - state-specific Human return：M-TEST escalation 必须允许 `to_stage=M-DESIGN`，不能用 requirement-only gate
       - M-DESIGN Archer/scaffold：canonical `.tracks/project/project.toml` 是唯一允许的 `.tracks/**` project contract 路径；随设计 checkpoint 去重提交，其他 `.tracks/**` 仍 fail closed
       - M-DESIGN 输出合同：test-plan canonical `## 8. AC Coverage`，interfaces canonical `## 5. IF Registry`；每条 integration/e2e AC 解析为 `{ac_id,layers,if_ids}`，missing/empty registry、坏/缺 header、duplicate AC、empty test cell、unregistered IF 均 fail closed；standalone `trac validate test-plan.md` 同门禁
       - M-DESIGN EXIT/M-TEST Shield：Runtime 在 command.issued 前向 Shield assignment 注入非空 `test_tasks`；仅 role=Shield/substate=WRITE 适用；无效输入不调用 backend，failed outcome=`stub_gap`，自动回 M-DESIGN
       - ResultCheckpoint：invalid result retry 必须按 actor/substate 清 dispatch flags；Shield WRITE `requires_diff=true`，无 tests diff 不得发布 `test.written`；动态 test path allowed/artifact 集合从 WAL 持久化 `pre_dirty_snapshot` 与 post 内容身份比较，不可只做路径集合差；crash recovery 复用 persisted snapshot；不混入 unchanged Human dirty files
       - M-TEST collection：所有 `tests/**/*.py`（含 conftest/helper）可 checkpoint，但 collect-only 只对 `test_*.py`/`*_test.py` test modules；只有 helper 时 fail closed；conftest 不得被单独判 no-tests
       - host project command：contract 中相对 `.venv/bin/python{,3}` 在宿主 worktree 不存在时，只能回退当前 Runtime 的 venv `sys.executable`，不得系统 Python；项目自有 venv 存在时优先
       - rollback evidence：仅 M-TEST `stub_gap`→M-DESIGN 清 stale failure，scope_overflow 等语义回退仍保留 evidence
       - dispatch materialization 完整性：绝对 target doc/doc-set、role/substate/attempt/review_round、docs/templates/skills、criteria-pack identity、test_tasks、pre_dirty_snapshot、result/checkpoint identity 均由 Runtime 物化；Agent 不得自行搜索/猜。最终覆盖 Scribe TRIAGE/DRAFT/RESPOND、Sage、Lex、Archer DRAFT、Prism design/test review、Shield WRITE，以及 Runtime validate/checkpoint/publish/collect/run/red/commit/seal
       - "无遗漏"判定：最终 report 的全部 dispatch_agent 都有 input/output refs 且 audit_completeness=complete；所有 stage 至 M-TEST 均退出，最终合法 Red + test commit + boundary 闭环；因此 M-START→M-TEST 物化调查完成，未知范围发现任务不再传给 spec。后续风险限定为上述合同的确定性回归，不再称未完成
  2. **三 worktree identity/组合错误风险**：Devon candidate / test-authority / gate 三 worktree 的创建时序与组合若出错（如 candidate 晚于 Shield WRITE、gate 组合时用了错误基线/未冻结 bundle、frozen bundle 误合入 candidate），隔离或门禁证据即失效——以共同基线 C_design、frozen bundle digest、candidate identity、gate 组合证据相互绑定缓解；Devon 违反"不主动读/运行/改 frozen tests"约定属约定违背而非机制失效，由 manifest + prompt 约定兜底（Human 最新裁定：可相信 Devon 遵守约定；bootstrap/manual 无 temporal worktree 时依赖 manifest + prompt 约定，不永久 fail closed）
  3. **真实 Devon end-to-end 通道不确定性与耗时**：M-IMPL 是行为最复杂的阶段，首次接入真实 LLM Agent 的 e2e 通道可能不稳定或耗时不可控——集成 FakeAgent 通道保证确定性回归（双通道互为独立验证），e2e 失败按 D-11 取消协议处理，不设 elapsed-time 超时
