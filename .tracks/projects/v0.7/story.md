---
story_id: S-001
title: 可信测试证据与一致质量基线（v0.7-A）
created: 2026-08-24
status: draft
sha:
---

# S-001: 可信测试证据与一致质量基线（v0.7-A）

## 1. 原始输入

> v0.7-A：可信测试证据与一致质量基线。
>
> 目标：Human 只提供产品意图和需求评审；Agent 自主设计、编写测试与实现。tracks 必须让每条 approved AC 的测试真实性由机器执行证据背书，避免为了满足门禁而制造无关 Red 或宽泛 mutation 的 Goodhart 行为；同一机制必须随 tracks 部署到宿主项目，而不是 tracks 仓库专用补丁。
>
> 依赖顺序：
> 1. Phase 0 先恢复可信绿色基线：补齐 v0.6 三个缺失 AC 的真实 collected-node 绑定；真实覆盖率达到合同阈值且不靠排除作弊；消除或经设计显式修订全部质量违规后使其硬门；注册测试 marks；修复本地与 CI 的环境/分发契约；封存 v0.6 文档。
> 2. 建立单一 canonical quality guard registry。Archer 为宿主选择完整八类守卫并声明 pinned tool、config digest、scope、threshold、timeout/failure policy、Runtime/pre-commit/CI 执行点和 required check；三处必须引用同一合同或由其生成。Runtime 对 parity 做 fail-closed 校验，Prism design criteria 将缺失、--exit-zero、scope/threshold/命令不一致判 REVISE。
> 3. 实现 Test Authenticity Gate。取消"所有测试在当前树一律强凑 Red"：新行为必须在冻结的 pre-implementation baseline 上产生 AC/IF-specific legal Red；已存在行为或回归测试允许未变异 candidate 为绿，但必须杀死 AC-specific counterexample。无关下游失败不得作为该 AC 的 Red。
> 4. Shield 编写 integration/e2e 测试与反例资产，Prism 独立审查反例语义和最小性，Devon 不得修改冻结测试；Runtime 不信 Agent 自报，必须确定性执行实验。
> 5. 定义语言无关 mutation evidence protocol：manifest 只含 protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围及规范化预期结果。Runtime 在隔离 worktree 验证 baseline、git apply、实际 diff identity、target kill、controls remain green、rollback clean，并将 command/env/node/result/failure signature/digests 写入 append-only events。禁止修改 tests 的 mutation，禁止一个宽泛 no-op patch 为不相同合同事实批量背书。
> 6. 保持 kernel 语言中性：宿主 adapter 合同提供 collect、run_selected 和 normalized result；Runtime 只消费版本化 tracks-test-result 协议。当前 pytest/JUnit 逻辑下沉为第一个 reference adapter；未知 adapter fail-closed，不穷举语言。
> 7. executable trace 必须形成 approved AC -> test-plan/public outlet -> 实际 collected node -> candidate-bound baseline/mutation evidence -> 同 candidate FULL pass 的闭环；node 缺失、skip/xfail、身份漂移或控制组失败均阻止退出。
>
> 验收必须覆盖 tracks 自身和至少一个全新宿主 demo：证明 broad mutation、stale patch、错误 candidate、未收集 node、无关下游 Red、target 未死、control 被误伤、adapter/result 畸形、质量守卫三点不一致都会 fail closed；正确实验可崩溃恢复且事件可 replay/report 审计。
>
> 本切片不注册 M-VERIFY，不做 CI SHA 回读、制品构建或发布自动化；这些属于通过本切片可信证据后才启动的 v0.7-B。

> **Scribe [RESOLVED]:** TRIAGE blocker（范围/交付形态）：seed 验收要求覆盖 'tracks 自身 + 至少一个全新宿主 demo'，并证明 9 类 fail-closed 场景，但 demo 宿主身份与语言未定。方向 A) demo = tracks 仓内最小全新宿主 fixture（小型 Python 项目），复用第一个 reference adapter（pytest/JUnit 逻辑），不新增适配器——范围最小，只证明机制若内核语言中性可复用于新宿主；方向 B) demo = 不同语言宿主（如 Java/JUnit 真实项目），v0.7-A 内新增第二个 reference adapter，以实证语言中立协议本身——范围显著扩大，但正面证明协议跨语言；方向 C) 形态委托 Archer 在 M-DESIGN 裁定（story 只锁定 'demo 必须可正向演 fail-closed' + '未知 adapter fail-closed'），story 不固定 demo 语言。默认交付面推断为：宿主 repo 内既有 trac CLI/machine contract 接口 + 部署后的 pre-commit/CI 守卫配置（seed 要求机制随 tracks 部署而非仓内补丁——推定为可推导，不需另行询问）。推荐 A：与 seed 6「当前 pytest/JUnit 逻辑下沉为第一个 reference adapter」呼应，且把语言实证留给后续 slice，避免 v0.7-A 范围进一步膨胀；若用户意在实证语言抽取则选 B。另注（非 blocker）：『v0.6 三个缺失 AC』身份在 v0.6 文档中未见显式枚举（v0.6-goal-assessment.md 已缺失、仅存 .lock），Phase 0 的『补齐』目标建议在 story 中表述为『按 collected-node 绑定的 trace 缺口现场取回』而非预设清单。
>> **Maestro:** 裁定 A：v0.7-A 的 demo 是 tracks 测试资产内动态创建的全新最小 Python/pytest 宿主 repo，使用首个 pytest reference adapter；它必须走与真实宿主相同的安装/contract/Runtime 路径，不得调用 tracks 私有测试捷径。第二语言 adapter 与"跨两种语言的实证"不在本切片范围；本切片以 opaque node/result protocol、未知 adapter fail-closed 和无语言语义进入 kernel 证明可扩展边界。Phase 0 三个精确缺口为 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02，必须绑定真实 collected node 与执行证据，不能仅补 marker。
>>> **Scribe:** 已按裁定 A 落文（DRAFT）：(1) §3.6 与 §5 锁定 demo 为 tracks 测试资产内动态创建的全新最小 Python/pytest 宿主 repo，使用首个 pytest reference adapter，走与真实宿主相同的安装/contract/Runtime 路径、不得调用 tracks 私有测试捷径；第二语言 adapter 与跨两种语言实证排除，以 opaque node/result protocol、未知 adapter fail-closed 证明可扩展边界（3.5、BS-11/BS-13）。(2) Phase 0 三缺口身份确认为 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6，必须绑定真实 collected node 与执行证据、不能仅补 marker（3.1、BS-01、§5）。(3) Test Authenticity Gate 语义按 seed 阶段 3 落定：新行为须在冻结 baseline 上产 legal Red，既有/回归允许绿但须 kill countexample，无关下游失败不作该 AC 的 Red（3.3、BS-06~08）；§5 与 §7 声明其与 v0.6 D-41 执行选择语义兼容演进。DRAFT 完成。

## 2. 用户意图

- **想完成什么**：v0.7-A 是 Tracks 平台自身的可信度加固 slice——让每条 approved AC 的测试真实性不由 Agent 自报、不靠形式上的 marker 或"为满足门禁而强凑"的 Red 背书，而是由**机器执行证据**证明：approved AC 必须落到真实 collected node、绑定 candidate/patch baseline/mutation 证据，并最终以同一 candidate 的 FULL pass 闭环；质量门禁不再是逐点声明的散件，而是由一个单一 canonical registry（pinned tool、config digest、scope、threshold、timeout/failure policy、Runtime/pre-commit/CI 执行点、required check）在三处可程序校验的 parity 上约束；该测试真实性机制必须作为 Tracks 平台的能力随部署进入宿主项目，兑现"测试真实性由机器执行证据背书，而非为门禁制造无关 Red"的目标。
- **当前哪里受阻**：v0.6 已交付但真实测试证据仍是软约束——三条 AC（AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6）的 collected-node 绑定缺失或仅为 marker、无 candidate-bound 执行证据；质量门禁的 scope/threshold/命令在三处（Runtime、pre-commit、CI）可能不一致且无程序校验；当前"所有测试在当前树一律强凑 Red"让无关 Red 与宽泛 mutation 可蒙混过关（Goodhart 面）；无语言无关的 mutation evidence protocol，跨语言/多候选的放行无法 fail-closed；kernel 内嵌 pytest/JUnit 语义，语言中性边界未实证。
- **完成后能看到什么结果**：Phase 0 恢复可信绿色基线（三条 AC 补齐真实 collected-node 绑定与执行证据、覆盖率达到合同阈值且不靠排除、质量违规消除或经设计显式修订后硬门、测试 marks 注册、本地与 CI 环境/分发契约修复、v0.6 封存）。此后新行为必须在冻结的 pre-implementation baseline 上产生 AC/IF-specific legal Red；既有行为/回归允许未变异 candidate 为绿但必须 kill AC-specific counterexample；无关下游失败不再是该 AC 的 Red。canonical quality guard registry 三处 parity 由 Runtime 程序校验 fail-closed，Prism 对缺失/--exit-zero/不一致判 REVISE。mutation evidence 以 manifest + 隔离 worktree 实验 + append-only events 形成可 replay/report 审计的证据链。测试执行经 adapter 合同（collect/run_selected/normalized result）解析，pytest/JUnit 逻辑下沉为第一个 reference adapter，未知 adapter fail-closed，kernel 保持无语言语义。executable trace 关联 approved AC -> test-plan/public outlet -> 实际 collected node -> candidate-bound baseline/mutation evidence -> 同一 candidate FULL pass，node 缺失、skip/xfail、身份漂移或控制组失败均阻止退出，并在 tracks 自身与一个全新最小 Python/pytest 宿主 demo 上验收（9 类 fail-closed 场景必须 fail closed；正确实验可崩溃恢复且 events 可 replay/report 审计）。

## 3. 核心操作路径

### 3.1. Phase 0：恢复可信绿色基线（v0.6 收尾与封存）

- **变更基线**：修改 — 当前 v0.6 交付状态：三条 AC（AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6）只有 marker/条目形式，无真实 collected-node 绑定与 candidate-bound 执行证据（Maestro 裁定 T-001 已锁定该确切身份，必须绑定真实 collected node 与执行证据，不能仅补 marker）；覆盖率未达合同阈值或靠排除达成；质量违规未消除、门禁未硬；测试 marks 未注册；本地与 CI 的环境/分发契约存在缺口。本次把这些缺失补齐并把 v0.6 文档封存为只读基线。
- **入口/触发**：操作者启动 v0.7-A（既有 `trac run` / 版本目录 v0.7 完成 M-STORY 评审后进入），Phase 0 为依赖顺序第 1 条，必须先完成且通过门禁后其余 Phase 才能开始。

1. 依据 collected-node 绑定 trace 缺口定位：对 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02 补真实 collected node 与执行证据，不使用仅加 marker 方式，全部经 machine contract 的 `run_selected` 采集并绑定节点身份。
2. 使真实覆盖率达到合同阈值且不依靠排除（exclude/绕过不存在或按无证据即 fail-closed）；质量违规全部消除或经设计显式修订后使守卫硬门（违规即 fail-closed 阻断），并注册测试 marks。
3. 修复本地与 CI 的环境/分发契约（安装/Tool/Adapter/Runtime 契约一致性、收集/执行命令合同）并跑通维护。
4. 全部校验通过后将 v0.6 文档封存为只读基线，更新版本 project/registry 引用。
5. 操作者在 `trac status`/`trac replay`/`trac check` 看到 baseline captured 证据、每 AC 的 collected node 绑定、覆盖率计量、质量门通过结果与封存标记。

- **完成结果**：可信绿色基线建立（证据可审计、不靠排除、质量违规消除后硬门、环境分发契约修复），v0.6 封存只读。此后后续 Phase 可在此基线上执行；不可恢复的缺陷会阻止进入下一步（fail-closed）。

### 3.2. 建立单一 canonical quality guard registry 与三处 parity 校验

- **变更基线**：新增 — 当前质量守卫由 Archer 在 M-DESIGN 的 architecture.md machine contracts 中声明（v0.5 质量门禁分层、tracks-quality-guards 八类目录），但缺乏单一可验证 registry 声明全部守卫的 pinned tool、config digest、scope、threshold、failure policy；Runtime、pre-commit、CI 三处执行点互相独立、无程序一致性校验。本路径引入单一 canonical registry 为唯一来源，三处必须引用同一合同或由其生成，且 Runtime 对 parity 做 fail-closed 校验。
- **入口/触发**：Archer 在 M-DESIGN 阶段产出守卫选择与 registry（`trac run` 派发）；此后任何质量守卫执行点（Git/CI hook 安装、Runtime 门禁、pre-commit）以 registry 为契约源。

1. Archer 为宿主选定完整八类守卫（见 tracks-quality-guards 目录），逐项声明 pinned tool/config digest/scope/threshold/timeout-failure policy/执行点（Runtime、pre-commit、CI）与 required check，写入单一 registry。
2. 三处执行点引用同一 registry（或由其生成实际配置），Runtime 运行程序 parity 比对（missing、scope/threshold/command 不一致、`--exit-zero` 等）即 fail-closed 阻断。
3. Prism 在 M-DESIGN/TEST 评审时按设计判据复核 registry：缺失守卫、命令以 `--exit-zero` 兜底、scope/threshold/command 与其他执行点不一致 —> REVISE（回流 Archer 重新产出 registry）。

- **完成结果**：质量守卫成为决定"通过门禁"的单一事实源且三处程序可校验一致；guard 配置不是宿主仓库专用，而是由 Tracks 部署机制随 pre-commit/CI 进入宿主；操作者看到 parity 校验记录与 fail-closed 阻断点（`trac status`/`trac report`）。

### 3.3. Test Authenticity Gate：legal Red 资格判定

- **变更基线**：修改 — 当前 M-TEST 的 RED 合法判定对所有测试在当前树一律要求 Red（v0.6 D-41 的 legal Red 语义在被选 R2 集；"所有测试在当前树都必须强制 Red"容易被无关 Red 或宽泛 mutation 满足）。本次改为 Authenticity Gate：新行为（pre-implementation baseline 上不存在的 AC/IF）必须在冻结的 pre-implementation baseline 上产生 AC/IF-specific legal Red；已存在行为或回归测试允许未变异 candidate 为绿，但必须杀死 AC-specific counterexample；无关下游失败不得作为该 AC 的 Red。
- **入口/触发**：任一 test candidate 绑定到 approved AC/IF 进入 M-TEST（`trac run` M-TEST stage）时；基线以冻结的 pre-implementation baseline（v0.6 封存基线或 run 内的 baseline snapshot）为准。

1. 依据 AC 收集节点集合，判定 candidate 行为类别（新 vs 既有）：新 —> 在冻结 baseline 上执行并产出该 AC/IF 的 legal Red 证据；既有 —> 允许绿，但必须同时产出 AC-specific counterexample kill 证据。
2. 无关下游测试失败（不持有 target、control/其它 AC node）不计为该 AC 的 Red；目标 node 未收集、adapter/result 畸形、control 被误伤等 fail-closed 阻断。
3. 判定事件 append-only 写入，可 `trac replay`/`trac report` 审计；Runtime 以确定性实验执行，不信 Agent 自报。

- **完成结果**：approved AC 的测试真实性由机器执行证据背书；新行为有 legal Red 证据、既有行为有 counterexample kill；无关失败不冒名；可审计（`trac replay`/`trac report`）。与 v0.6 D-41 执行选择语义（R2 差分、空 R2 fail-closed）兼容演进——本路径锁定 Red 出处合法性，D-41 锁定哪些节点被选择执行。

### 3.4. mutation evidence protocol：manifest 与隔离 worktree 实验

- **变更基线**：新增 — 当前只有"Green 通过"的正面证据和 Prism 的 counterexample kill，没有语言无关的 mutation evidence protocol，没有对 candidate/patch 的隔离 worktree 验证、target/control node 身份、rollback clean、append-only 事件化证据。本次定义 mutation evidence protocol manifest（仅含 protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围、规范化期望结果），Runtime 在隔离 worktree 确定性执行并写入 append-only events。
- **入口/触发**：Tracks 对 candidate/patch 做 mutation 验证（Devon candidate、Shield 回归、独立 trial）时，操作者通过 `trac replay`/`trac report` 看到审计链。

1. mutation manifest：只含 protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围与规范化期望结果；禁止修改 tests 的 mutation；禁止一条宽泛 no-op patch（无 selectable diff identity）为多个合同事实批量背书。
2. Runtime 在隔离 worktree 确定性执行：验证 baseline、git apply、实际 diff identity 核对、target kill、control node 保持绿、rollback clean；command/env/node/result/failure signature/digests 写入 append-only events。
3. 验证全部生成物（manifest、blob、audit 事件）供 replay/report；错误 candidate、stale patch、未收集 node、target 未死、control 被误伤、adapter/result 畸形等场景 fail-closed；正确实验可崩溃恢复（事件可回放）。

- **完成结果**：每条 mutation evidence 具备独立可审计链，target 死亡与 control 保持绿由机器验证而非 Agent 自述；错误 patch 无法在无证据下进入。

### 3.5. 语言无关的 adapter 合同与 kernel 语言中立

- **变更基线**：修改/重构 — 当前测试收集/执行逻辑内嵌在 kernel/executor（project.py load_contract、executor collect/run command）与 machine contract 的 [unit]/[integration]/[e2e] 段（collect/run/run_selected、JUnit XML）。本次定义宿主 adapter 合同三接口（collect、run_selected 与 normalized result，携带 protocol version）作为统一 seam；pytest/JUnit 逻辑下沉为第一个 reference adapter；kernel 不再出现语言语义，只消费版本化 tracks-test-result 协议；未知 adapter 或畸形结果 fail-closed。
- **入口/触发**：host adapter 由 Archer 在 M-DESIGN 依 `tracks-test-result` 协议声明；执行时 Runtime 经 adapter 调用测试收集/选择/结果（`trac run` 门禁、`trac check`）。

1. adapter contract（collect、run_selected、normalized result）：Runtime 依赖版本化 tracks-test-result 协议输入；kernel/executor 不再直接引用 pytest/JUnit/java。
2. 现行 pytest 路径下沉为第一个 reference adapter（维持 collect/run_selected/JUnit normalise 语义，复用 v0.6 既有逻辑）。未知 adapter 或畸形结果（缺 AC/IO、identity 错误）—> fail-closed 拒绝进入并在事件标注原因。
3. 用户/宿主通过 adapter 声明语言；断言结果经同一 normalized channel 交付（report/replay/审计），仅 adapter 代收差异。

- **完成结果**：kernel 保持语言无关（无语言语义、未知适配器 fail-closed）；pytest/JUnit 逻辑可复用、不变。第二语言 adapter 与跨两种语言的实证不在本切片（Maestro 裁定 A），以 opaque node/result protocol、未知 adapter fail-closed 及无语言语义进入 kernel 证明可扩展边界。

### 3.6. executable trace 闭环与双宿主 fail-closed 验收

- **变更基线**：新增 — 当前 approved AC 与 test-plan/public outlet 有 trace（TRACKS-TRACE marker、`trac check trace`）但未形成 candidate-bound baseline/mutation evidence —> FULL pass 的机器锁死闭环；node 缺失、skip/xfail、身份漂移、控制组失败都有 exit 通道。本路径形成"approved AC —> test-plan/public outlet —> 实际 collected node —> candidate-bound baseline evidence —> mutation evidence —> 同一 candidate FULL pass"闭环并要求 node 缺失、skip/xfail、身份漂移或控制组失败即阻止退出；验收覆盖 tracks 自身与一个全新最小 Python/pytest 宿主 demo。
- **入口/触发**：v0.7-A 验收阶段，操作者以既有 `trac run`/`trac check trace`/`trac status` 观察两个宿主各自执行 9 类 fail-closed 场景；验收通过后进入 v0.7-B。

1. trace 闭环校验：executable trace 校验每棵 AC 从 approved AC 经 test-plan outlet 至实际 collected node 并绑定 candidate/baseline/mutation evidence；不满足即 fail-closed 阻止退出（node 缺失、skip/xfail、身份漂移、控制组失败均阻断）。
2. 本 slice 验收在 tracks 自身 + 全新最小 Python/pytest 宿主 demo（demo 在 tracks 测试资产内动态创建，必须走与真实宿主相同的安装/Runtime 合同路径，不得调用 tracks 私有测试捷径——Maestro 裁定 A）上覆盖 9 类 fail-closed 场景：broad mutation、stale patch、错误 candidate、未收集 node、无关下游 Red、target 未死、control 被误伤、adapter/result 畸形、quality guard 三处不一致。全部 fail closed.
3. 崩溃恢复：正确实验中断/重启后 events 可 `trac replay`/`trac report` 审计，重建且不丢失证据。

- **完成结果**：双宿主通过 9 类 fail-closed 演证，证据闭环与崩溃恢复可审计；验收通过后启动 v0.7-B（M-VERIFY 注册、CI SHA 回读、制品构建/发布自动化——不在本切片）。

## 4. 行为种子

### BS-01 Phase 0 补齐真实 collected-node 绑定与封存

- EARS: `WHEN Phase 0 启动, THE 系统 SHALL 为 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6 补真实 collected node 与执行证据（按 trace 缺口现场取回），且 SHALL NOT 以仅补 TRACKS-TRACE marker 为闭合`
- 来源: [3.1 / Maestro 裁定 T-001]
- 说明: 三条缺口 AC 必须绑定真实 collected node 与执行证据，不能仅补 marker；否则后续 Phase 无可信冻结 baseline。

### BS-02 Phase 0 覆盖率、硬门、marks 与环境契约

- EARS: `WHEN Phase 0 校验覆盖率与质量门禁, THE 系统 SHALL 以真实覆盖率达到合同阈值且不靠排除/跳过/ignore 达成，质量违规消除或经设计显式修订后使守卫硬门（违规即阻断），且 SHALL 注册测试 marks 并修复本地与 CI 的环境/分发契约`
- 来源: [3.1 / seed 阶段 1]
- 说明: 保证可信基线不靠排除遮掩。

### BS-03 v0.6 封存只读

- EARS: `WHEN Phase 0 校验全部通过, THE 系统 SHALL 将 v0.6 文档封存为只读基线，且 SHALL NOT 允许后续 Phase 改写该基线`
- 来源: [3.1 / seed 阶段 1 / 重要推导]
- 说明: 封存保证冻结对比底，后续 Phase 2–6 的实验在此基线上打桩/冻结。

### BS-04 canonical guard registry 单一来源与三处 parity fail-closed

- EARS: `WHERE 宿主需执行质量守卫, THE 系统 SHALL 以单一 canonical quality guard registry 为唯一来源校验 pinned tool/config digest/scope/threshold/timeout-failure policy/执行点/required check，且 WHILE Runtime、pre-commit、CI 三处不一致（缺失守卫、--exit-zero、scope/threshold/命令不一致）, THE 系统 SHALL fail-closed 阻断，且 SHALL NOT 以 Agent 自报为通过依据`
- 来源: [3.2 / seed 阶段 2]
- 说明: 质量守卫一致性由程序校验，三处不一致即阻断。

### BS-05 Prism registry 判据 —> REVISE

- EARS: `WHEN Prism 评审质量守卫 registry, IF 缺失守卫、以 --exit-zero 兜底或 scope/threshold/命令不一致, THE 系统 SHALL 判 REVISE 并回流 Archer`
- 来源: [3.2 / seed 阶段 2 / Prism design criteria]
- 说明: 设计判据防止 registry 退化为形式条目。

### BS-06 新行为须 AC/IF-specific legal Red on 冻结 baseline

- EARS: `WHEN 新行为（冻结 pre-implementation baseline 上不存在的 AC/IF）进入验证, THE 系统 SHALL 要求在冻结 baseline 上产生 AC/IF-specific legal Red（有机器执行证据），且 SHALL NOT 以当前树无关 Red 或宽泛 mutation 充当`
- 来源: [3.3 / seed 阶段 3]
- 说明: 新行为必须真实触发合法性断言失败，非导入无关节点。

### BS-07 已存在行为允许绿 + counterexample kill

- EARS: `WHEN 已存在行为或回归测试进入验证, THE 系统 SHALL 允许未变异 candidate 为绿，但 SHALL 要求杀死 AC-specific counterexample 作为真实性证明，且 SHALL NOT 允许以豁免绿逃避该证明`
- 来源: [3.3 / seed 阶段 3]
- 说明: 防止为 candidate 验证而制造无关 Red，或豁免真实现缺陷。

### BS-08 无关下游失败不作该 AC 的 Red

- EARS: `IF 失败的 collected node 与该 AC/IF 无绑定（无关下游失败）, THE 系统 SHALL NOT 将其作为该 AC 的 Red 依据，且 SHALL 将其隔离出该 AC 的资格判定`
- 来源: [3.3 / seed 阶段 3]
- 说明: 避免借用无关下游失败为 AC/IF 背书。

### BS-09 mutation evidence manifest 最小化

- EARS: `WHEN 生成 mutation evidence manifest, THE 系统 SHALL 只记录 protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围与规范化预期结果，且 SHALL NOT 记录候选语义细节`
- 来源: [3.4 / seed 阶段 5]
- 说明: 保持语言无关，协议不携带候选语义。

### BS-10 隔离 worktree 确定性验证 + 逐 AC/IF 背书

- EARS: `WHEN 验证 mutation evidence, THE 系统 SHALL 在隔离 worktree 确定性执行 baseline 验证、git apply、实际 diff identity 核对、target kill、controls 保持绿与 rollback clean，将 command/env/node/result/failure signature/digests 写入 append-only 事件，且 SHALL NOT 允许 mutation tests 或一条宽泛 no-op patch 为不相同合同事实批量背书`
- 来源: [3.4 / seed 阶段 5]
- 说明: 每条 AC/IF 的 mutation 证据独立可审计；Runtime 不信 Agent 自报。

### BS-11 adapter 合同与未知 adapter fail-closed

- EARS: `WHEN 宿主执行测试收集/选择/结果, THE 系统 SHALL 经宿主 adapter 合同（collect、run_selected、normalized result）访问，且 IF adapter 未知或结果畸形, THE 系统 SHALL fail-closed 拒绝接入，且 SHALL NOT 穷举语言`
- 来源: [3.5 / seed 阶段 6 / Maestro 裁定 A]
- 说明: kernel 保持语言无关；pytest/JUnit 下沉为第一个 reference adapter。

### BS-12 executable trace 闭环锁死

- EARS: `WHEN 验收/门禁执行 trace 闭环, THE 系统 SHALL 形成 approved AC —> test-plan/public outlet —> 实际 collected node —> candidate-bound baseline/mutation evidence —> 同 candidate FULL pass 的闭环，且 WHILE node 缺失、skip/xfail、身份漂移或控制组失败, THE 系统 SHALL 阻止退出`
- 来源: [3.6 / seed 阶段 7]
- 说明: 环中任一环节缺失即阻断。

### BS-13 双宿主 9 类 fail-closed 演证

- EARS: `WHEN v0.7-A 验收, THE 系统 SHALL 在 tracks 自身与全新最小 Python/pytest 宿主 demo（动态创建，走与真实宿主相同的安装/contract/Runtime 路径、不调用 tracks 私有测试捷径）上正向演证 broad mutation、stale patch、错误 candidate、未收集 node、无关下游 Red、target 未死、control 被误伤、adapter/result 畸形、守卫三点不一致 9 类 fail-closed 场景，且 SHALL 允许正确实验崩溃恢复并可由事件 replay/report 审计`
- 来源: [3.6 / seed 验收 / Maestro 裁定 A]
- 说明: demo 走真实 http 安装路径而非私有捷径；第二语言 adapter 不在本切片。

### BS-14 本切片范围边界

- EARS: `WHEN v0.7-A 范围内, THE 系统 SHALL NOT 注册 M-VERIFY、执行 CI SHA 回读或制品构建/发布自动化，且 SHALL 将其作为通过本切片可信证据后启动的 v0.7-B 事项`
- 来源: [§5 / seed 末段]
- 说明: 明确本切片边界，不超出到 v0.7-B 范围。

## 5. 范围、约束与例外

- **必须保持的产品约束**：
  - 语言中性不变量：kernel 不出现语言语义；宿主 adapter 合同 = collect、run_selected、normalized result；Runtime 只消费版本化 tracks-test-result 协议；未知 adapter fail-closed，不穷举语言。
  - 冻结基线纪律：legal Red 判定针对冻结 pre-implementation baseline；mutation 实验在隔离 worktree 确定性执行；Runtime 不信 Agent 自报，必须确定性执行实验并 append-only 落事件。
  - 三处一致：Runtime、pre-commit、CI 必须引用同一 canonical quality guard registry 合同或由其生成；Runtime 对 parity 做 fail-closed 程序校验。
  - 真实性背书规则：一条宽泛 no-op patch 不得为不相同 AC/IF 批量背书；禁止 mutation tests。
  - demo 形态（Maestro 裁定 A）：全新宿主 demo 是 tracks 测试资产内动态创建的全新最小 Python/pytest 宿主 repo，使用首个 pytest reference adapter，必须走与真实宿主相同的安装/contract/Runtime 路径，不得调用 tracks 私有测试捷径。
  - 三缺口确切身份（Maestro 裁定）：AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6，必须绑定真实 collected node 与执行证据。
  - 与既有执行选择语义相容：Test Authenticity Gate 锁定 Red 出处合法性；节点选择/分类/台账继续遵循 v0.6 D-41 语义（R2 差分、full ledger、空 R2 fail-closed、hotfix unit-only 旁路不变）。
  - Shield 编写 integration/e2e 测试与反例资产，Prism 独立审查反例语义和最小性，Devon 不得修改冻结测试（seed 阶段 4）。

- **非常规要求**：
  - 取消"所有测试在当前树一律强凑 Red"（v0.6 既有执行语义的 Goodhart 治理修订）——已存在行为允许绿但须 kill AC-specific counterexample，这是对 D-41 既有 RED 语义的有意偏离，用户明确要求（seed 阶段 3）。
  - 验收宿主是 tracks 测试资产内动态创建的全新最小宿主 repo（自举式 demo）而非宿主外的既有项目——用户裁定 A；它必须走真实安装/contract/Runtime 路径，不得调用私有捷径。
  - mutation evidence 采用 opaque target/control node IDs（协议不携带候选语义）——语言中立协议的刻意设计。
  - 质量守卫硬门化：消除或经设计显式修订全部质量违规后使之硬门（违规即阻断），是既有软门禁的有意收紧。

- **Out-of-Scope**：
  - 第二语言/框架 adapter 与"跨两种语言的实证"（Maestro 裁定 A 排除；以 opaque node/result protocol、未知 adapter fail-closed 证明可扩展边界）。
  - M-VERIFY 注册、CI SHA 回读、制品构建、发布自动化与 Human release gate（属 v0.7-B，seed 末段）。
  - nightly 结果取回通道（v0.6 已排除，随 result fetch future）。
  - 为新行为新增 canonical 阶段或既有 trac CLI/CI 之外的交付面。

## 6. 开放产品决定

无。demo 宿主形态（Maestro 裁定 A：仓内动态创建最小 Python/pytest 宿主、复用 reference adapter、不新增第二 adapter）、三缺口 AC 的确切身份（AC-FR0250-03/AC-NFR0130-01/AC-NFR0130-02@v0.6）、Goodhart 治理方向（取消一律强凑 Red、已存在行为允许绿但 kill counterexample）、交付面（既有 trac CLI/machine contract + 部署到宿主的 pre-commit/CI 守卫配置）均已由 seed 与裁定确立；adapter 合同形态、quality guard registry schema、mutation evidence protocol 版本与隔离 worktree 实现细节属后续规格/设计阶段技术设计，不改变产品结果。

## 7. 必要性与风险

- **既有能力**：v0.6 D-41 已交付测试执行选择语义（R1 快照 baseline capture、R2 差分、FULL 链台账、selection/evidence identity）与逐节点机器可读结果通道（JUnit XML、{nodes}/{result} 封闭集）——可复用为 Test Authenticity Gate 与 mutation evidence 的节点/证据基础；TRACKS-TRACE 绑定 marker 与 `trac check trace` 闭合校验已存在（三缺口即其暴露的缺口）；machine contract 命令所有权与 `--junitxml={result}` 通道可下沉为 reference adapter；tracks-quality-guards skill 定义八类守卫目录与安装/执行点分工（可扩展为 canonical registry 的声明与三处部署）；既有 `trac run/status/replay/report/check/validate` CLI 供操作者观察。
- **冲突**：Test Authenticity Gate"已存在行为允许绿"与 v0.6 D-41"feature M-TEST 的 R2 选择集为空 = fail-closed（不得 vacuous 通过）"存在表面张力——语义分层解决：前者约束 Red 出处合法性（属于既有行为的回归节点允许不 Red，但以 counterexample kill 证明真实性），后者约束节点选择与执行集合（空选择不得作为通过）；story 按"兼容演进"表述，spec 阶段需显式对齐两者（§5 约束已记录）。取消"一律强凑 Red"是对 v0.6 既有执行语义的有意修订，须在 M-SPEC 中写明对既有 FR-0250/0253 的变更关系而非叠加新机制。quality guard registry 取代既有散在 architecture.md 中的 machine contract 守卫声明，须确保迁移期间不产生静默脱节。
- **重要风险**：
  - 若 Phase 0 无法恢复可信绿色基线（三缺口无法绑定真实节点、覆盖率不达标且不能靠排除、质量违规无法消除或无法显式设计修订），后续 Phase 全部失去冻结对比底，v0.7-A 不成立（seed 依赖顺序第 1 条，Phase 0 必须先于一切完成）——实现必须保留"修复—>再校验"路径且不得以放水通过。
  - 若动态创建 demo 无法完全走真实安装/contract/Runtime 路径（被迫调用私有测试捷径），双宿主验收演证不成立——demo 机制必须自证与真实宿主路径等价，且有独立校验证据。
  - 若 counterexample kill 被实现为可豁免/可宽泛化，Goodhart 后门会在"已存在行为允许绿"处复发——kill 证明必须绑定 AC-specific 反例且最小性由独立评审（Prism 按 seed 阶段 4 独立审查）。
  - guard registry 三处 parity 若只做声明不做法定校验，canonical 来源形同虚设——parity 校验必须由 Runtime 程序执行并 fail-closed，不依赖 Agent 自述或评审自言。
  - 三条 AC 的身份既定（AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02）但若 v0.6 实际实现中还有其它未暴露的 collected-node 绑定缺陷，Phase 0 的"补齐"范围可能扩大——story 保留"按 trace 缺口现场取回"的灵活表述，但实现须确保不遗漏。