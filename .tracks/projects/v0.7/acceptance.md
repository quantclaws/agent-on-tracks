---
acc_id: ACC-007
created: 2026-08-24
status: draft
sha:
---

# 可信测试证据与一致质量基线（v0.7-A） — 验收标准

## FR-0256 Phase 0 真实 collected-node 绑定补齐

### AC-FR0256-01

  - Phase 0 启动后（SM-01.2），对 AC-FR0250-03@v0.6、AC-NFR0130-01@v0.6、AC-NFR0130-02@v0.6 三条缺口 AC，事件流出现 `phase0.baseline_repaired` 事件且 payload 含被补齐 AC 身份（跨版本引用 `AC-FRXXXX-YY@v0.6`）与经 machine contract `run_selected` 采集的真实 collected node 身份（nodeid + digest），可由 `trac replay` / `trac report` 审计
  - `trac check trace --version v0.6` 对这三条 AC 的闭合由真实 collected node 驱动（非 marker/条目形式）：trace report 的 bound node 字段为真实可 collect 节点而非空 marker 行

### AC-FR0256-02

  - 边界（仅补 marker 不闭合）：若实现仅对三缺口 AC 补 TRACKS-TRACE marker 注释行而无真实 collected node 绑定事件，`trac check trace --version v0.6` 仍报 fail（hard_errors 含对应 AC 的绑定缺失），Phase 0 不产出 `phase0.sealed`，`trac status` 报告 `phase0` 仍处于 `PHASE0_VALIDATING` 或 `BLOCKED`，不进入后续 Phase

### AC-FR0256-03

  - 现场取回范围：Phase 0 的 trace 缺口扫描若在 v0.6 实际实现中发现 AC-FR0250-03/AC-NFR0130-01/AC-NFR0130-02 之外的 collected-node 绑定缺陷（node 缺失、identity 不可恢复），同样以 `phase0.baseline_repaired` 事件补齐真实节点，不得遗漏；`trac check trace --version v0.6` 对 v0.6 全部 approved AC 闭合通过才允许 Phase 0 继续
  - 不可恢复缺陷：三缺口或现场发现缺口无法绑定真实节点（节点缺失、不可 collect、identity 不可恢复）-> HOTFIX-TRIAGE... 无关；Phase 0 子状态进入 `BLOCKED`（SM-01.5），`trac status` 报告 `phase0=blocked` 与阻塞原因，v0.7-A 不成立、不产出 `phase0.sealed`，后续 Phase 不派发

> **Lex [RESOLVED]:** 非阻塞 wording 清理：AC-FR0256-03 第二句有残缺片段「HOTFIX-TRIAGE... 无关」—— spec FR-0256 item 3 路由仅为 BLOCKED+回流 Human，不涉 hotfix。请 Sage 清理为「与 HOTFIX-TRIAGE 无关；Phase 0 进入 BLOCKED(SM-01.5)」。产品语义无变化，不阻塞。

> **Lex [RESOLVED]:** **非阻塞 — 遗漏字/语病**：AC-FR0256-03 第二句「不可恢复缺陷：... -> HOT_TRIAGE... 无关；Phase 0 子状态进入 BLOCKED」存在残缺片断 ( 后接省略号与「无关」). spec FR-0256 item 3 明确此路由仅  后明文回流 Human, 不涉 HOTFIX-TRIAGE. 请 Sage 清理该残片为「与 HOTFIX-TRIAGE 无关；Phase 0 子状态进入 BLOCKED」以消除 implementer 误读. 产品语义无变化, 不构成阻塞.

## FR-0257 Phase 0 覆盖率硬门、marks、环境契约与 v0.6 封存只读

### AC-FR0257-01

  - Phase 0 校验覆盖率时事件流出现 `phase0.coverage(passed)` 事件且 payload 含 `ratio`（达到 tracks 惯例阈值 ≥95%，由 canonical quality guard registry 第 7 类守卫固化）与 `by=collected`、`exclude=none`；`trac status` 报告 `coverage=<ratio>`
  - 不靠排除达成：事件/审计中不存在 exclude/跳过/ignore 路径使覆盖率达标——无证据即 fail-closed（排除/绕过配置不存在或按无证据即阻断）；`trac report` 可复核覆盖率计量的被 collect 节点集与排除集（排除集为空）

### AC-FR0257-02

  - 质量守卫硬门：Phase 0 校验后事件流出现 `phase0.guard_hardened` 事件且 payload 含 `violations=0` 与 `revised=<n>`（经设计显式修订的合同变更计数）；守卫违规即 fail-closed 阻断（违反任一守卫阈值 = 阻断，不软放行）
  - 「经设计显式修订」= 显式合同变更（更新 registry 的 threshold/scope）+ Prism 按 FR-0259 复核 + Runtime 按 FR-0258 parity 硬门；修订记录可由 `trac replay` / `trac report` 审计，SHALL NOT 等同于放宽阈值或静默削弱（修订后守卫仍硬门，违规即阻断）

### AC-FR0257-03

  - 测试 marks 注册：Phase 0 注册测试 marks，使后续 Phase 的节点分类与选择（继承 v0.6 D-41 FR-0250 语义）可程序识别；`trac status` 报告 `marks=registered`
  - 环境/分发契约修复：本地与 CI 的安装/Tool/Adapter/Runtime 契约一致性、收集/执行命令合同修复并跑通维护——Phase 0 之后 Runtime/pre-commit/CI 三处执行点能引用同一契约（为 FR-0258 三处 parity 与 FR-0264 adapter 合同前置）；`trac validate` 对环境/分发契约校验通过

### AC-FR0257-04

  - v0.6 封存只读：FR-0256 + AC-FR0257-01/02/03 全部通过后事件流出现 `phase0.sealed` 事件，`trac status` 报告 `phase0=sealed`、`baseline=v0.6-readonly`；版本 project/registry 引用更新指向封存基线
  - 不可回退：`SEALED` 状态不回退至 `PHASE0_VALIDATING`（SM-01.6）；后续 Phase 2–6 的事件流/审计中无改写 v0.6 封存基线文档的记录（git 对 v0.6 文档无写提交）；封存是 Phase 0 的不可逆完成动作

### AC-FR0257-05

  - 边界（不可恢复）：覆盖率不达标且不能靠排除、或质量违规无法消除且无法经设计显式修订 -> Phase 0 子状态进入 `BLOCKED`（SM-01.5），`trac status` 报告 `phase0=blocked` 与阻塞原因（覆盖率/守卫），不产出 `phase0.sealed`，后续 Phase 不派发；实现保留「修复 -> 再校验」循环（SM-01.3），不设预算上限，不得以放水通过

## FR-0258 canonical quality guard registry 单一来源与三处 parity fail-closed

### AC-FR0258-01

  - registry 单一来源：Archer 在 M-DESIGN 产出的 architecture.md machine contracts 中存在单一 canonical quality guard registry，逐项声明完整八类守卫（lint+format / 静态检查 / 认知复杂度 / 文件长度 / 方法长度·局部变量 / 重复度 / 覆盖率门槛 / 钩子运行器+CI required checks）的五要素（守卫 / 工具 pinned / 配置位置 / 阈值 / 执行点）+ pinned tool、config digest、scope、threshold、timeout-failure policy、required check；registry 可由 `trac validate` 程序读取
  - 八类缺一不可：任一类守卫缺失 -> registry 校验 fail（`trac validate` 非零退出），不软放行

### AC-FR0258-02

  - 三处 parity 程序校验：Runtime 运行程序 parity 比对 Runtime 门禁 / pre-commit / CI required check 三处——任一处 missing 守卫、scope/threshold/命令不一致、以 `--exit-zero` 兜底 -> 事件流出现 `guard.parity` 事件且 payload 含 `status=blocked`、`registry=<digest>` 与三处匹配结果（不一致项标注），门禁 fail-closed 阻断；`trac status` 报告 parity 阻断点
  - 三处一致时 `guard.parity(passed)` 事件 payload 含 `registry=<digest>`、`runtime=match`、`pre_commit=match`、`ci=match`；parity 校验由 Runtime 程序执行，SHALL NOT 以 Agent 自报为通过依据（无 Agent 自述事件被采信为 parity 通过）

### AC-FR0258-03

  - 部署机制非仓内补丁：guard 配置由 Tracks 部署机制随 pre-commit/CI 进入宿主项目（不是 tracks 仓库专用补丁）——宿主 repo 的 `.githooks/pre-commit` 与 CI required checks 引用由 registry 生成的配置；`trac report` 可审计部署记录与 registry digest 关联
  - Phase 0（AC-FR0257-03）环境/分发契约修复后，三处执行点引用同一 registry 合同（`guard.parity` 通过）

### AC-FR0258-04

  - 取代散在声明迁移无脱节：registry 取代既有 v0.6 architecture.md §4.2 散在守卫表——v0.6 §4.2 每项守卫在 registry 中都有对应五要素条目（无缺失/无静默脱节）；`trac validate` 对 registry 完整性校验通过，迁移期间不产生既有守卫既无 registry 条目又无显式排除的静默状态

## FR-0259 Prism registry REVISE 判据

### AC-FR0259-01

  - REVISE 触发：Prism 在 M-DESIGN/M-TEST 评审 registry 时，IF registry 缺失守卫（八类缺一）、命令以 `--exit-zero` 兜底、或 scope/threshold/命令与其他执行点不一致，事件流出现 Prism REVISE 判定（回流 Archer 重新产出 registry）；REVISE 不产出阶段出口证据，`trac status` 报告回流去向为 Archer
  - 「经设计显式修订」（AC-FR0257-02）的合同变更同样经 Prism 复核——修订后守卫仍硬门、违规即阻断；Prism 不接受借修订之名放宽阈值（Goodhart 后门），放宽即 REVISE

### AC-FR0259-02

  - 真实执行证据：registry 每项守卫有真实执行证据（CI required checks 的通过/失败输出），不接受文档声明或自述——仅有声明而无执行证据视为守卫缺失（REVISE）；`trac report` 可复核守卫执行证据来源

## FR-0260 Test Authenticity Gate：legal Red 资格判定

### AC-FR0260-01

  - 新行为 legal Red：新行为（冻结 pre-implementation baseline 上不存在的 AC/IF）进入 M-TEST 验证时，事件流出现 `authenticity.judged` 事件且 payload 含 `category=new`、`red=legal`、被绑定的 AC/IF 与 collected node 身份、counterexample 状态；legal Red 判据继承 v0.6（行为断言失败/桩合同 token 失败/symbol 缺失）
  - legal Red 针对冻结 baseline（Phase 0 封存的 v0.6 只读基线 SM-01.4，或 run 内 `test.baseline_captured` stamped 快照），不针对当前可变树——`authenticity.judged` 的 baseline 字段引用冻结 baseline 身份

### AC-FR0260-02

  - 无关下游失败隔离：IF 失败的 collected node 与该 AC/IF 无绑定（无关下游失败），`authenticity.judged` 事件的 payload 标注该失败 `unrelated=true` 且不作为该 AC 的 Red 依据；`trac replay` 可复核该失败被隔离出该 AC 的资格判定
  - 目标 node 未收集、adapter/result 畸形、control 被误伤 -> `authenticity.judged` 标注 `status=blocked` 且 fail-closed 阻断（不归为合法 Red，也不归为通过），不产出 `prism.verdict`

### AC-FR0260-03

  - 冻结 baseline 前置：Phase 0 未封存（`phase0.sealed` 未出现）时，后续 Phase 的 `trac run` 不派发 M-TEST authenticity 判定，`trac status` 报告 Phase 0 未完成；冻结 baseline 缺失 -> Phase 2+ 不执行

### AC-FR0260-04

  - 与 D-41 选择语义兼容：FR-0260 修订的是 RED_CHECK 的 Red 出处合法性，v0.6 FR-0250 的节点分类（R1/R2、R2/T-DELTA）、全量 collect、selection identity、tree_stamp、`test.baseline_captured`/`test.selected`/`red.validated` 事件、空 R2 fail-closed、hotfix unit-only 旁路（FR-0244）不变——`authenticity.judged` 与 `red.validated` 共存于事件流，前者锁 Red 出处合法性，后者锁节点选择与执行集合
  - `trac replay` 可同时复核 authenticity 判定与 D-41 选择语义，两者判定依据可独立追溯

### AC-FR0260-05

  - 边界（无关 Red/宽泛 mutation 不充当）：以当前树无关 Red 或宽泛 mutation（无 AC/IF-specific diff identity）充当新行为 legal Red -> `authenticity.judged` 标注 `red=illegal` 且 fail-closed 阻断，不产出 `prism.verdict`；`trac status` 报告阻断原因为非法 Red

## FR-0261 AC-specific counterexample kill

### AC-FR0261-01

  - 已存在行为允许绿 + counterexample kill：已存在行为或回归测试进入验证时，事件流出现 `authenticity.judged` 事件且 payload 含 `category=existing`、`green=allowed`、`counterexample_kill=verified`；未变异 candidate 为绿被允许，但 SHALL 同时产出 AC-specific counterexample kill 证据
  - counterexample kill 缺失 -> `authenticity.judged` 标注 `counterexample_kill=missing` 且 fail-closed 阻断，不产出 `prism.verdict`；SHALL NOT 允许以豁免绿逃避该证明

### AC-FR0261-02

  - AC-specific 反例绑定：counterexample kill 的反例身份绑定该 AC/IF（payload 含 AC/IF + 反例 node 身份），非宽泛化、非跨 AC 借用；反例最小性由 Prism 独立审查（RP-01 第 2 行），审查记录可由 `trac replay` / `trac report` 审计
  - counterexample kill 被宽泛化（反例不绑定 specific AC/IF 或可豁免）-> fail-closed 阻断（Goodhart 后门复发防护）

### AC-FR0261-03

  - 角色分离：Shield 编写 integration/e2e 测试与反例资产（RP-01 第 1 行）；Prism 独立审查反例语义和最小性（第 2 行）；Devon SHALL NOT 修改冻结测试（Phase 0 封存后的基线测试资产，第 3 行）——事件流/审计中无 Devon 修改冻结测试的记录，冻结测试的修改权对全部角色关闭（包括 Runtime）
  - Devon 修改冻结测试 -> 修改无效且阻断（违反 RP-01 第 3 行），`trac status` 报告违规

## FR-0262 mutation evidence manifest 最小化与语言无关

### AC-FR0262-01

  - manifest 最小字段集：mutation evidence 以 `mutation.manifest` 事件 append-only 落入，payload 只含 protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围与规范化预期结果；SHALL NOT 含候选语义细节（语言、框架、测试体内容）——`trac report` 可复核 manifest 字段集为最小且不含候选语义
  - target/control node 以 opaque ID 引用（不携带候选语义），使协议保持语言无关

### AC-FR0262-02

  - 逐 AC/IF 背书：manifest 以 AC/IF 为单位，每条 mutation 证据独立可审计；一条宽泛 no-op patch（无 selectable diff identity）为多个不同 AC/IF 批量背书 -> fail-closed 阻断（`mutation.manifest` 标注 `status=blocked` 原因=no_selectable_diff_identity），不允许批量背书
  - `trac replay` 可复核每条 manifest 绑定单一 AC/IF 与单一 candidate/patch digest

### AC-FR0262-03

  - 禁止 mutation tests：mutation 针对候选实现/patch，SHALL NOT 针对测试资产——`mutation.manifest` 的允许变更范围不含测试资产路径；修改测试的 mutation -> fail-closed 阻断

## FR-0263 mutation evidence 隔离 worktree 确定性验证与 append-only 事件

### AC-FR0263-01

  - 隔离 worktree 确定性执行：验证 mutation evidence 时事件流出现 `mutation.experiment` 事件链，payload 含各步结果与 digest——baseline=verified、apply=identity_match（git apply 后实际 diff identity 核对）、target_kill=verified（target node 死亡）、controls=green（control node 保持绿）、rollback=clean；任一步骤不满足即 fail-closed（错误 candidate、stale patch、未收集 node、target 未死、control 被误伤、adapter/result 畸形）
  - Runtime 确定性执行，不信 Agent 自报——`mutation.experiment` 证据由 Runtime 执行产生，无 Agent（Devon/Shield/Prism）自述被采信为通过

### AC-FR0263-02

  - append-only 事件：command/env/node/result/failure signature/digests 写入 append-only 事件（`mutation.experiment`），SHALL NOT 改写既有行（事件表无既有行改写）；`trac replay` / `trac report` 可审计各步 digest 与命令回显

### AC-FR0263-03

  - 崩溃恢复：正确实验中断/重启后 events 可 `trac replay` / `trac report` 审计，重建且不丢失证据；WAL replay 发现无已持久化结果时重跑该实验，绝不把缺失结果当作通过（`mutation.experiment` 不出现 `status=passed` 而 results 缺失的情形）

### AC-FR0263-04

  - 边界：target 未死（target_kill != verified）、control 被误伤（controls != green）、adapter/result 畸形、stale patch（apply identity 不匹配）-> `mutation.experiment` 标注 `status=blocked` 且 fail-closed 阻断，错误 patch 无法在无证据下进入

## FR-0264 宿主 adapter 合同与 kernel 语言中立

### AC-FR0264-01

  - adapter 合同三接口：宿主 adapter 合同提供 collect、run_selected 与 normalized result（携带 protocol version）作为统一 seam；Runtime 经 adapter 调用测试收集/选择/结果——`trac run` 门禁与 `trac check` 的执行审计记录 adapter 身份与 protocol version；Runtime 只消费版本化 tracks-test-result 协议输入
  - kernel/executor SHALL NOT 直接引用 pytest/JUnit/java 或任何语言语义（NFR-0141）——`trac validate` 对 kernel/executor 无语言语义引用校验通过

### AC-FR0264-02

  - pytest/JUnit 下沉为第一个 reference adapter：现行 pytest 路径下沉为 reference adapter，维持 collect/run_selected/JUnit normalise 语义，复用 v0.6 既有逻辑（FR-0255 命令独家所有权、`{nodes}`/`{result}` 占位符、并发 flag 内嵌、`run_selected` 缺失 fail-closed 不变）；reference adapter 的行为与 v0.6 既有 pytest 路径一致（同输入同输出），`trac replay` 可复核

### AC-FR0264-03

  - 未知 adapter/畸形结果 fail-closed：IF adapter 未知或结果畸形（缺 AC/IO、identity 错误），Runtime fail-closed 拒绝接入并在事件标注原因（`status=blocked` reason=unknown_adapter|malformed_result）；SHALL NOT 穷举语言——kernel 不内置任何语言的特殊分支，未知 adapter 一律拒绝
  - `trac status` / `trac report` 可审计拒绝原因；拒绝不产出后续门禁通过证据

### AC-FR0264-04

  - kernel 语言中立不变量：kernel/executor 无 pytest/JUnit/java 或任何语言语义引用——`trac validate` 合同校验与 Runtime 执行路径双重复核（非自述）；宿主通过 adapter 声明语言，断言结果经同一 normalized channel 交付（report/replay/审计），仅 adapter 代收差异

## FR-0265 executable trace candidate-bound 闭环锁死

### AC-FR0265-01

  - 闭环拓扑：`trac check trace --version v0.7` 输出 `status=pass` 且 `closure=candidate-bound`，每棵 AC 的闭环链可由 `trac replay` / `trac report` 复核：approved AC → test-plan/public outlet → 实际 collected node → candidate-bound baseline evidence → mutation evidence（FR-0262/0263）→ 同一 candidate FULL pass
  - 闭环由 `trac check trace` 与 M-IMPL ISLAND_GATE_2 出口门禁双闭合——两者均通过才允许 M-IMPL 退出与 v0.7-A 验收通过

### AC-FR0265-02

  - 阻断条件：WHILE node 缺失（collected node 与 AC 绑定断裂）、skip/xfail（被选节点未真实执行）、身份漂移（selection/evidence identity 不一致）、控制组失败（mutation experiment 的 control node 未保持绿），`trac check trace --version v0.7` 输出 `status=fail` 且 hard_errors 含对应环节，M-IMPL 不产出 `stage.exited(M-IMPL)`，v0.7-A 验收不通过
  - `trac status` 报告阻断环节（node_missing/skip_xfail/identity_drift/control_failure）

### AC-FR0265-03

  - candidate-bound：baseline evidence 与 mutation evidence 绑定同一 candidate（candidate/patch digest 一致，FR-0262 manifest 字段）——`trac replay` 可复核闭环各环节的 candidate digest 一致；SHALL NOT 以他 candidate 的证据为本次 candidate 闭环背书（他 candidate digest 不一致 -> fail-closed，继承 v0.6 NFR-0130 identity 判据扩展至 mutation evidence）

## FR-0266 双宿主 9 类 fail-closed 演证与崩溃恢复

### AC-FR0266-01

  - 双宿主演证：v0.7-A 验收阶段，`trac run` 在 `host=tracks` 与 `host=demo-pytest` 两个宿主上各自执行 9 类 fail-closed 场景，事件流出现每类场景的演证事件且 payload 标注 `all_fail_closed=true`；9 类场景为：(1) broad mutation、(2) stale patch、(3) 错误 candidate、(4) 未收集 node、(5) 无关下游 Red、(6) target 未死、(7) control 被误伤、(8) adapter/result 畸形、(9) quality guard 三处不一致
  - 任一场景未 fail closed（场景被错误放行）-> 该宿主演证事件标注 `status=blocked`，v0.7-A 验收不通过，`trac status` 报告未通过的场景与宿主

### AC-FR0266-02

  - demo 形态：全新最小 Python/pytest 宿主 demo 在 tracks 测试资产内动态创建，使用首个 pytest reference adapter（FR-0264-02）；SHALL 走与真实宿主相同的安装/contract/Runtime 路径（venv/pinned 工具供给、`git config core.hooksPath`、CI required check 绑定、adapter 合同声明），SHALL NOT 调用 tracks 私有测试捷径
  - demo 路径等价性：demo 机制自证与真实宿主路径等价，且有独立校验证据落入事件（`trac replay` / `trac report` 可审计）；若被迫调用私有测试捷径 -> 双宿主验收演证不成立，v0.7-A 不成立

### AC-FR0266-03

  - 崩溃恢复：正确实验中断/重启后 events 可 `trac replay` / `trac report` 审计，重建且不丢失证据——演证事件标注 `crash_recovery=replay_ok`；重建结果与中断前一致（继承 v0.5 FR-0200 崩溃恢复语义）

### AC-FR0266-04

  - 边界：任一场景未 fail closed 或 demo 路径不等价 -> v0.7-A 验收不通过，不进入 v0.7-B（BS-14，见 spec 范围排除）；`trac status` 报告验收未通过原因

## NFR-0140 authenticity/mutation 证据 append-only 可审计性与 fail-closed

### AC-NFR0140-01

  - append-only 事件：`phase0.baseline_repaired` / `phase0.coverage` / `phase0.guard_hardened` / `phase0.sealed` / `authenticity.judged` / `mutation.manifest` / `mutation.experiment` / `guard.parity` 全部 append-only 写入事件流（事件表无既有行改写，沿用 v0.5 NFR-0020）；`trac replay` / `trac report` 可审计全部事件
  - 投影表可从事件完整重建：drop 投影表后 `trac status` / `trac report` 重建的状态与原一致

### AC-NFR0140-02

  - identity 绑定：每条 authenticity/mutation 证据绑定 AC/IF + candidate/patch digest + selection/evidence identity + attempt + actor（扩展自 v0.6 NFR-0130 四元组）；`trac replay` 可复核每条证据的身份字段完整
  - 身份漂移（identity 不一致）的证据一律不得作为通过依据（继承 v0.6 AC-NFR0130-02 判据，扩展至 mutation evidence）

### AC-NFR0140-03

  - 崩溃恢复：Runtime 中断或重启后由事件回放重建 authenticity/mutation 状态，重建结果（含全部 append-only 事件）与中断前一致；WAL replay 发现无已持久化结果时重跑该实验/判定，绝不把缺失结果当作通过（`authenticity.judged` / `mutation.experiment` 不出现 `status=passed` 而 results 缺失的情形）

### AC-NFR0140-04

  - fail-closed：未知状态、缺失条目、identity 漂移、控制组失败一律 fail-closed——不得视为通过、不得跳过闭环（FR-0265）；触发原因落事件可审计；Runtime 不信 Agent 自报，必须确定性执行实验并 append-only 落事件

## NFR-0141 canonical guard registry parity 与 kernel 语言中立的程序校验

### AC-NFR0141-01

  - guard parity 程序校验：`guard.parity` 事件携带 registry digest 与三处（Runtime/pre-commit/CI）匹配结果，可由 `trac replay` / `trac report` 复核；parity 校验由 Runtime 程序执行，不依赖 Agent 自述或评审自言——无 Agent 自述事件被采信为 parity 通过
  - 三处不一致（missing/`--exit-zero`/scope·threshold·命令不一致）即 fail-closed 阻断（AC-FR0258-02）

### AC-NFR0141-02

  - kernel 语言中立不变量：kernel/executor SHALL NOT 出现语言语义（不引用 pytest/JUnit/java 或任何语言特殊分支）——`trac validate` 合同校验与 Runtime 执行路径双重复核（非自述）；未知 adapter 或畸形结果 fail-closed 拒绝接入（不穷举语言，AC-FR0264-03）
  - 三处一致（Runtime/pre-commit/CI）必须引用同一 canonical quality guard registry 合同或由其生成——Runtime 对 parity 做 fail-closed 程序校验

## NFR-0142 双宿主 demo 路径等价性与崩溃恢复

### AC-NFR0142-01

  - demo 路径等价性：全新最小 Python/pytest 宿主 demo 走与真实宿主相同的安装/contract/Runtime 路径（venv/pinned 工具供给、`git config core.hooksPath`、CI required check 绑定、adapter 合同声明），SHALL NOT 调用 tracks 私有测试捷径；路径等价性以独立校验证据落入事件，可由 `trac replay` / `trac report` 审计
  - 第二语言 adapter 与「跨两种语言的实证」不在本切片（spec 范围排除）——本切片以 opaque node/result protocol、未知 adapter fail-closed、kernel 无语言语义证明可扩展边界

### AC-NFR0142-02

  - 崩溃恢复：正确实验中断/重启后 events 可 `trac replay` / `trac report` 重建且不丢失证据（继承 v0.5 FR-0200 崩溃恢复语义）；重建结果与中断前一致
