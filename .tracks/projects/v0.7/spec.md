---
spec_id: SPEC-007
created: 2026-08-24
status: draft
sha:
---

# 可信测试证据与一致质量基线（v0.7-A） — 需求规格

## 界面与入口

### E-01 v0.7-A 新增可观察出口（既有 trac CLI，无新增顶层命令）

```
$ trac run                      # v0.7-A 旅程，Phase 0 先行
[run 01KZ...] phase0.baseline_repaired (ac=AC-FR0250-03@v0.6 bound_node=tests/integration/test_evidence_reuse.py::test_prism_consumes_runtime_evidence_without_suite_rerun)
[run 01KZ...] phase0.baseline_repaired (ac=AC-NFR0130-01@v0.6 bound_node=...::test_identity_fields_append_only_auditable)
[run 01KZ...] phase0.baseline_repaired (ac=AC-NFR0130-02@v0.6 bound_node=...::test_stale_propagation_uniform_across_gates)
[run 01KZ...] phase0.coverage(passed) (ratio=0.97 by=collected exclude=none)
[run 01KZ...] phase0.guard_hardened (violations=0 revised=0)
[run 01KZ...] phase0.sealed (v0.6 baseline=readonly)
$ trac status
run=01KZ... phase0=sealed baseline=v0.6-readonly coverage=0.97 guards=hardened marks=registered
$ trac run                      # 后续 Phase
[run 01KZ...] authenticity.judged (ac=AC-FR0260-01 category=new red=legal counterexample=none)
[run 01KZ...] authenticity.judged (ac=AC-FR0261-01 category=existing green=allowed counterexample_kill=verified)
[run 01KZ...] guard.parity(passed) (registry=digest:abc runtime=match pre_commit=match ci=match)
[run 01KZ...] mutation.manifest (ac=AC-FR0262-01 patch_digest=sha:... target=opaque-id-7 control=opaque-id-3)
[run 01KZ...] mutation.experiment(passed) (baseline=verified apply=identity_match target_kill=verified controls=green rollback=clean)
$ trac check trace --version v0.7
status=pass closure=candidate-bound (approved_ac -> outlet -> collected_node -> baseline_evidence -> mutation_evidence -> full_pass)
$ trac run                      # 双宿主验收
[run 01KZ...] host=tracks 9_scenarios=all_fail_closed crash_recovery=replay_ok
[run 01KZ...] host=demo-pytest 9_scenarios=all_fail_closed crash_recovery=replay_ok
$ trac replay 01KZ... ; trac report --run-id 01KZ... --format md
```

## 状态与生命周期

### SM-01 v0.6 基线 Phase 0 生命周期

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-01.4）。

1. （v0.6 交付态）→ `UNSEALED`：v0.7-A 启动，Phase 0 为依赖顺序第 1 条
2. `UNSEALED` → `PHASE0_VALIDATING`：Phase 0 开始校验（collected-node 绑定 / 覆盖率 / 守卫硬门 / marks / 环境契约）
3. `PHASE0_VALIDATING` → `PHASE0_VALIDATING`：发现缺陷 → 修复 → 再校验（不设预算上限，不得以放水通过）
4. `PHASE0_VALIDATING` → `SEALED`：全部校验通过且封存（`phase0.sealed`，v0.6 文档只读基线建立）
5. `PHASE0_VALIDATING` → `BLOCKED`：不可恢复缺陷（三缺口无法绑定真实节点 / 覆盖率不达标且不能靠排除 / 质量违规无法消除或无法显式设计修订）—— v0.7-A 不成立，回流 Human
6. `SEALED` → 终态：后续 Phase 2–6 在此冻结基线上执行；`SEALED` 不可回退至 `PHASE0_VALIDATING`，后续 Phase 不得改写该基线（BS-03）

## 角色与权限

### RP-01 测试真实性证据与反例资产的角色分离

单写者纪律继承 v0.5/v0.6 RP-01：任一时刻只有一个 actor 持有编辑权；Devon/Shield/Prism/Archer 不 commit/push、不推进状态（Runtime 是唯一流程 authority 与唯一 branch/worktree authority）。本表只列 v0.7-A 新增或有意收紧的角色边界。

| #   | 操作                                              | Shield | Prism | Devon | Runtime |
| --- | ------------------------------------------------- | ------ | ----- | ----- | ------- |
| 1   | 编写 integration/e2e 测试与反例资产               | ✅      | ❌     | ❌    | ❌       |
| 2   | 独立审查反例语义与最小性（counterexample kill）   | ❌      | ✅     | ❌    | ❌       |
| 3   | 修改冻结测试（Phase 0 封存后的基线测试资产）      | ❌      | ❌     | ❌    | ❌       |
| 4   | 确定性执行 authenticity/mutation 实验（不信自述） | ❌      | ❌     | ❌    | ✅       |
| 5   | 产出 candidate/patch（M-IMPL）                    | ❌      | ❌     | ✅    | ❌       |
| 6   | append-only 落证据事件 / guard parity 程序校验    | ❌      | ❌     | ❌    | ✅       |
| 7   | 声明 canonical quality guard registry（M-DESIGN） | ❌      | ❌     | ❌    | ❌（Archer 独家，见 FR-0258） |

## 功能需求

### FR-0256 Phase 0 真实 collected-node 绑定补齐

- **来源**：`BS-01` / `§3.1` / Maestro 裁定 T-001
- **交付入口**：`E-01`（`trac run` Phase 0；`trac status` / `trac replay` / `trac check trace`）

Phase 0 为 v0.7-A 依赖顺序第 1 条，必须先完成且通过门禁后其余 Phase 才能开始（SM-01.2–.4）。操作者启动 v0.7-A（既有 `trac run` / 版本目录 v0.7 完成 M-STORY 评审后进入）触发 Phase 0。

1. **三缺口 AC 绑定真实 collected node**：对 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6（Maestro 裁定 T-001 锁定的确切身份），按 collected-node 绑定 trace 缺口现场取回——全部经 machine contract 的 `run_selected` 采集并绑定真实节点身份， SHALL NOT 以仅补 TRACKS-TRACE marker 或条目形式为闭合（既有 `trac check trace` marker 模式即这三缺口所暴露的缺陷来源）。绑定证据以 `phase0.baseline_repaired` 事件 append-only 落入，携带 AC 身份（跨版本引用 `AC-FRXXXX-YY@v0.6`）与被绑定的真实 collected node 身份。
2. **现场取回范围**：三缺口为已知子集；Phase 0 的产品不变量是「baseline 中所有 approved AC 都有真实 collected-node 绑定与执行证据」——若 trace 缺口扫描在 v0.6 实际实现中发现其它未暴露的 collected-node 绑定缺陷，同样按本纪律现场取回，不得遗漏。
3. **不可恢复缺陷路由**：三缺口无法绑定真实节点（节点缺失、不可 collect、identity 不可恢复）→ `PHASE0_VALIDATING → BLOCKED`（SM-01.5），v0.7-A 不成立，回流 Human，不静默以 marker 蒙混。

**用户可观察结果**：操作者从 `trac status` / `trac replay` 看到 `phase0.baseline_repaired` 事件、每条缺口 AC 的真实 collected node 绑定（非 marker），`trac check trace` 对 v0.6 基线闭合由真实节点驱动。Phase 0 未通过则后续 Phase 不执行。

**关键失败/恢复边界**：Phase 0 保留「修复 → 再校验」循环（SM-01.3），不设预算上限，不得以放水通过；不可恢复缺陷 BLOCKED 阻止 v0.7-A 成立。

### FR-0257 Phase 0 覆盖率硬门、marks、环境契约与 v0.6 封存只读

- **来源**：`BS-02` / `BS-03` / `§3.1` / seed 阶段 1
- **交付入口**：`E-01`（`trac run` Phase 0；`trac status` / `trac report`）

Phase 0 在 FR-0256 之外同时校验并固化可信绿色基线的其余维度，全部通过后封存 v0.6 为只读基线。

1. **真实覆盖率**（BS-02）：真实覆盖率达到合同阈值（tracks 惯例 ≥95%，由 canonical quality guard registry 第 7 类守卫固化，FR-0258），SHALL NOT 依靠 exclude/跳过/ignore 达成——无证据即 fail-closed（排除/绕过不存在或按无证据即阻断）。覆盖率计量以 `phase0.coverage` 事件落入，携带 ratio 与 by=collected、exclude=none。
2. **质量守卫硬门**（BS-02，非常规要求）：质量违规全部消除或经设计显式修订后使守卫硬门（违规即 fail-closed 阻断）。「经设计显式修订」= 显式合同变更（更新 registry 的 threshold/scope）+ Prism 按 FR-0259 复核 + Runtime 按 FR-0258 parity 硬门，SHALL NOT 等同于放宽阈值或静默削弱（flow.md §8 硬规则 2 不变量保持）；既有软门禁的有意收紧。硬门结果以 `phase0.guard_hardened` 事件落入，携带 violations 与 revised 计数。
3. **测试 marks 注册**（BS-02）：注册测试 marks，使后续 Phase 的节点分类与选择（继承 v0.6 D-41 FR-0250 语义）可程序识别。
4. **环境/分发契约修复**（BS-02）：修复本地与 CI 的环境/分发契约（安装/Tool/Adapter/Runtime 契约一致性、收集/执行命令合同）并跑通维护——这是 FR-0263 adapter 合同与 FR-0258 guard registry 三处部署的前置：Phase 0 之后三处执行点必须能引用同一契约。
5. **v0.6 封存只读**（BS-03，重要推导）：FR-0256 + 本条 1–4 全部校验通过后（SM-01.4），将 v0.6 文档封存为只读基线（`phase0.sealed`），更新版本 project/registry 引用；`SEALED` 不可回退，后续 Phase 2–6 在此冻结基线上打桩/冻结实验，SHALL NOT 改写该基线。封存是 Phase 0 的不可逆完成动作，保证后续 Phase 的冻结对比底。

**用户可观察结果**：操作者从 `trac status` 看到 `phase0=sealed`、`baseline=v0.6-readonly`、覆盖率计量、guard hardened、marks registered；`trac replay` / `trac report` 可审计各维度校验事件与封存标记。

**关键失败/恢复边界**：覆盖率不达标且不能靠排除、质量违规无法消除或无法显式设计修订 → `PHASE0_VALIDATING → BLOCKED`（SM-01.5）；封存前任何维度未通过不得进入 `SEALED`。

### FR-0258 canonical quality guard registry 单一来源与三处 parity fail-closed

- **来源**：`BS-04` / `§3.2` / seed 阶段 2 / tracks-quality-guards 八类目录
- **交付入口**：`E-01`（`trac status` / `trac report` parity 校验记录）/ machine contract（Archer M-DESIGN 产出 registry）/ 部署到宿主的 pre-commit + CI required checks

当前质量守卫由 Archer 在 M-DESIGN 的 architecture.md machine contracts 中声明（v0.5 质量门禁分层、tracks-quality-guards 八类目录），但缺乏单一可验证 registry 声明全部守卫的 pinned tool/config digest/scope/threshold/timeout-failure policy/执行点/required check，且 Runtime/pre-commit/CI 三处执行点互相独立、无程序一致性校验。本路径引入单一 canonical registry 为唯一来源。

1. **registry 单一来源**（BS-04）：Archer 在 M-DESIGN 为宿主选定完整八类守卫（lint+format / 静态检查 / 认知复杂度 / 文件长度 / 方法长度·局部变量 / 重复度 / 覆盖率门槛 / 钩子运行器+CI required checks，见 tracks-quality-guards skill），逐项声明 pinned tool、config digest、scope、threshold、timeout-failure policy、执行点（Runtime/pre-commit/CI）与 required check，写入单一 canonical quality guard registry。registry 是唯一合同源——三处执行点必须引用同一 registry 或由其生成实际配置。
2. **三处 parity 程序校验 fail-closed**（BS-04）：Runtime 运行程序 parity 比对三处（Runtime 门禁 / pre-commit / CI required check）——missing 守卫、scope/threshold/命令不一致、以 `--exit-zero` 兜底等任一不一致即 fail-closed 阻断，SHALL NOT 以 Agent 自报为通过依据（NFR-0141）。parity 校验以 `guard.parity` 事件 append-only 落入，携带 registry digest 与三处匹配结果。
3. **部署机制非仓内补丁**（§5 约束）：guard 配置不是 tracks 仓库专用补丁，而是由 Tracks 部署机制随 pre-commit/CI 进入宿主项目——兑现 seed「机制随 tracks 部署到宿主项目，而不是 tracks 仓库专用补丁」。Phase 0（FR-0257 第 4 步）修复环境/分发契约后，三处执行点必须能引用同一 registry 合同。
4. **取代既有散在声明**（§7 冲突）：registry 取代既有散在 architecture.md 中的 machine contract 守卫声明，迁移期间须确保不产生静默脱节——既有 v0.6 §4.2 守卫表的每项在 registry 中都有对应五要素条目（守卫 / 工具 pinned / 配置位置 / 阈值 / 执行点）。

**用户可观察结果**：操作者从 `trac status` / `trac report` 看到 `guard.parity` 校验记录、registry digest、三处匹配结果与 fail-closed 阻断点；registry 在 architecture.md machine contracts 中可程序读取。

**关键失败/恢复边界**：三处任一不一致即阻断门禁，不依赖 Agent 自述或评审自言；parity 校验由 Runtime 程序执行（NFR-0141）。

### FR-0259 Prism registry REVISE 判据

- **来源**：`BS-05` / `§3.2` / seed 阶段 2 / Prism design criteria
- **交付入口**：`E-01`（`trac run` M-DESIGN/M-TEST 评审；`trac status` / `trac replay`）

Prism 在 M-DESIGN/M-TEST 评审时按设计判据复核 canonical quality guard registry（FR-0258），防止 registry 退化为形式条目。

1. **REVISE 触发条件**（BS-05）：IF registry 缺失守卫（八类缺一不可）、命令以 `--exit-zero` 兜底、或 scope/threshold/命令与其他执行点不一致，Prism SHALL 判 REVISE 并回流 Archer 重新产出 registry。
2. **设计判据非形式条目**：registry 的每项守卫必须有真实执行证据（CI required checks 的通过/失败输出），不接受文档声明或自述（继承 tracks-quality-guards §1 证据语义）；「经设计显式修订」（FR-0257 第 2 步）的合同变更同样经 Prism 复核，不得借修订之名放宽阈值（Goodhart 后门）。

**用户可观察结果**：操作者从 `trac status` / `trac replay` 看到 Prism REVISE 判据与回流去向（Archer 重新产出 registry）；REVISE 不产出阶段出口证据。

### FR-0260 Test Authenticity Gate：legal Red 资格判定（修改 v0.6 FR-0250 RED_CHECK）

- **来源**：`BS-06` / `BS-08` / `§3.3` / seed 阶段 3 / §7 冲突
- **交付入口**：`E-01`（`trac run` M-TEST stage；`trac replay` / `trac report`）

本条是对 v0.6 FR-0250 RED_CHECK 语义的有意修订（非常规要求，§5），不是叠加新机制。v0.6 FR-0250 第 3 步「feature M-TEST 的 R2 选择集为空 = fail-closed」与「所有测试在当前树一律强凑 Red」的 Goodhart 面由本条改写为 Authenticity Gate；v0.6 FR-0250 的节点分类（R1/R2、R2/T-DELTA）、全量 collect、selection identity、tree_stamp、`test.baseline_captured`/`test.selected`/`red.validated` 事件、空 R2 fail-closed、hotfix unit-only 旁路（FR-0244）全部不变。

1. **冻结 baseline**：基线以冻结的 pre-implementation baseline 为准——Phase 0 封存的 v0.6 只读基线（SM-01.4，FR-0257），或 run 内的 `test.baseline_captured` stamped 快照（v0.6 FR-0250 第 1 步 prior-to-Shield R1 快照）。Authenticity Gate 的合法性判定针对该冻结 baseline，不针对当前可变树。
2. **新行为须 AC/IF-specific legal Red**（BS-06）：新行为（冻结 baseline 上不存在的 AC/IF）进入验证时，THE 系统 SHALL 要求在冻结 baseline 上产生 AC/IF-specific legal Red（有机器执行证据），SHALL NOT 以当前树无关 Red 或宽泛 mutation 充当。合法 Red 判据继承 v0.6（行为断言失败 / 桩合同 token 失败 / symbol 缺失；collection/语法/fixture/import 错误非法）。判定以 `authenticity.judged` 事件 append-only 落入，携带 AC/IF、category=new、red=legal、counterexample 状态。
3. **无关下游失败隔离**（BS-08）：IF 失败的 collected node 与该 AC/IF 无绑定（无关下游失败），THE 系统 SHALL NOT 将其作为该 AC 的 Red 依据，SHALL 将其隔离出该 AC 的资格判定。目标 node 未收集、adapter/result 畸形、control 被误伤等 fail-closed 阻断（不归为合法 Red，也不归为通过）。
4. **Runtime 确定性执行**：Runtime 以确定性实验执行 authenticity 判定，不信 Agent 自报（NFR-0140）；判定事件 append-only 写入，可 `trac replay` / `trac report` 审计。
5. **与 D-41 选择语义兼容演进**（§7 冲突）：本条锁定 Red 出处合法性；节点选择/分类/台账继续遵循 v0.6 D-41 语义（R2 差分、full ledger、空 R2 fail-closed、hotfix unit-only 旁路不变）。已存在行为允许绿的判定见 FR-0261。

**用户可观察结果**：操作者从 `trac run` / `trac replay` 看到 `authenticity.judged` 事件（category=new/existing、red=legal/none、counterexample 状态、被绑定的 AC/IF 与 collected node）；新行为有 legal Red 证据、无关失败不冒名为 Red。

**关键失败/恢复边界**：目标 node 未收集、adapter/result 畸形、control 被误伤 → fail-closed 阻断，不产出 `prism.verdict`；冻结 baseline 缺失（Phase 0 未封存）→ Phase 2+ 不执行。

### FR-0261 AC-specific counterexample kill：已存在行为允许绿的真实性证明

- **来源**：`BS-07` / `§3.3` / seed 阶段 3 / §5 约束（角色分离）/ §7 风险
- **交付入口**：`E-01`（`trac run` M-TEST/PRISM_REVIEW；`trac replay` / `trac report`）

本条承接 FR-0260 第 5 步「已存在行为允许绿」的 Goodhart 治理修订（非常规要求），为「已存在行为或回归测试允许未变异 candidate 为绿」补充真实性证明，防止 Goodhart 后门在「允许绿」处复发（§7 风险）。

1. **已存在行为允许绿 + counterexample kill**（BS-07）：已存在行为或回归测试进入验证时，THE 系统 SHALL 允许未变异 candidate 为绿，但 SHALL 要求杀死 AC-specific counterexample 作为真实性证明，SHALL NOT 允许以豁免绿逃避该证明。判定以 `authenticity.judged` 事件落入，携带 category=existing、green=allowed、counterexample_kill=verified|missing。
2. **AC-specific 反例绑定**：counterexample kill 必须绑定该 AC/IF 的 specific 反例（非宽泛化、非跨 AC 借用），最小性由独立评审（Prism 按 seed 阶段 4 独立审查反例语义和最小性，RP-01 第 2 行）——SHALL NOT 由 candidate 自身的 Agent 自述满足（§7 风险：counterexample kill 被实现为可豁免/可宽泛化即 Goodhart 后门复发）。
3. **角色分离**（§5 约束，RP-01）：Shield 编写 integration/e2e 测试与反例资产（第 1 行），Prism 独立审查反例语义和最小性（第 2 行），Devon SHALL NOT 修改冻结测试（Phase 0 封存后的基线测试资产，第 3 行）——冻结测试的修改权对全部角色关闭，包括 Runtime。
4. **不可豁免**：counterexample kill 不可被实现为可豁免路径——kill 证明缺失即该 AC 的真实性不成立，fail-closed 阻断，不产出 `prism.verdict`。

**用户可观察结果**：操作者从 `trac run` / `trac replay` 看到 `authenticity.judged`（category=existing）携带 counterexample_kill 状态与所绑定的 AC-specific 反例身份；反例资产与 Prism 审查记录可审计；冻结测试无修改事件。

**关键失败/恢复边界**：counterexample kill 缺失或被宽泛化 → fail-closed 阻断；Devon 修改冻结测试 → 违反 RP-01 第 3 行，修改无效且阻断。

### FR-0262 mutation evidence manifest 最小化与语言无关

- **来源**：`BS-09` / `§3.4` / seed 阶段 5
- **交付入口**：`E-01`（`trac run` Devon/Shield 验证流程；`trac replay` / `trac report`）

本条定义 mutation evidence protocol 的 manifest 内容合同，是 FR-0263 隔离 worktree 实验的输入。

1. **manifest 最小字段集**（BS-09）：mutation evidence manifest SHALL 只记录：protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围与规范化预期结果。SHALL NOT 记录候选语义细节（语言、框架、测试体内容）——语言中立协议的刻意设计（§5 非常规要求）：协议不携带候选语义，target/control node 以 opaque ID 引用，使 kernel 与协议本身保持语言无关（FR-0263、NFR-0141）。
2. **逐 AC/IF 背书**：manifest 以 AC/IF 为单位，每条 mutation 证据独立可审计；SHALL NOT 允许一条宽泛 no-op patch（无 selectable diff identity）为不相同合同事实批量背书（§5 约束：真实性背书规则）。
3. **禁止 mutation tests**（§5 约束）：SHALL NOT 允许修改测试的 mutation——mutation 针对候选实现/patch，不针对测试资产。
4. **事件化**：manifest 以 `mutation.manifest` 事件 append-only 落入（NFR-0140），携带 protocol version 与上述字段；manifest blob 供 replay/report。

**用户可观察结果**：操作者从 `trac replay` / `trac report` 看到 `mutation.manifest` 事件与 manifest blob；manifest 字段集可程序校验为最小且不含候选语义。

### FR-0263 mutation evidence 隔离 worktree 确定性验证与 append-only 事件

- **来源**：`BS-10` / `§3.4` / seed 阶段 5
- **交付入口**：`E-01`（`trac run` 验证；`trac replay` / `trac report`）

本条定义 Runtime 对 mutation evidence 的确定性实验执行（不信 Agent 自报），承接 FR-0262 manifest 输入。

1. **隔离 worktree 确定性执行**（BS-10）：验证 mutation evidence 时，THE 系统 SHALL 在隔离 worktree 确定性执行：验证 baseline、git apply、实际 diff identity 核对、target kill、control node 保持绿、rollback clean。任一步骤不满足即 fail-closed（错误 candidate、stale patch、未收集 node、target 未死、control 被误伤、adapter/result 畸形等场景）。
2. **append-only 事件**：command/env/node/result/failure signature/digests 写入 append-only 事件（`mutation.experiment`），SHALL NOT 改写既有行；事件携 baseline/applies/target_kill/controls/rollback 各步结果与 digest。
3. **崩溃恢复**：正确实验中断/重启后 events 可 `trac replay` / `trac report` 审计，重建且不丢失证据（NFR-0140）；WAL replay 发现无已持久化结果时重跑该实验，绝不把缺失结果当作通过。
4. **Runtime 不信自报**（§5 约束：冻结基线纪律）：Runtime 必须确定性执行实验，不接受 Agent（Devon/Shield/Prism）的自述为通过依据——counterexample kill 的真实性同样由 Runtime 确定性执行验证，与 Prism 的反例语义/最小性审查（FR-0261 第 2 步）分工：Prism 审语义，Runtime 验执行。

**用户可观察结果**：操作者从 `trac replay` / `trac report` 看到 `mutation.experiment` 事件链（baseline → apply → diff identity → target kill → controls green → rollback clean）与各步 digest；错误 patch 无法在无证据下进入。

**关键失败/恢复边界**：target 未死 / control 被误伤 / adapter result 畸形 / stale patch → fail-closed；正确实验崩溃可恢复（事件回放重建）。

### FR-0264 宿主 adapter 合同与 kernel 语言中立（修改 v0.6 FR-0255）

- **来源**：`BS-11` / `§3.5` / seed 阶段 6 / Maestro 裁定 A
- **交付入口**：`E-01`（`trac run` 门禁 / `trac check`）/ machine contract（Archer M-DESIGN 依 `tracks-test-result` 协议声明 adapter）

本条是对 v0.6 FR-0255 的重构（修改/重构，§3.5 变更基线），不是叠加新机制。v0.6 FR-0255 的测试命令 Archer 独家所有权（`run`/`run_selected` 模板、`{nodes}`/`{result}` 占位符、并发 flag 内嵌、`run_selected` 缺失 fail-closed）全部保持，但当前测试收集/执行逻辑内嵌在 kernel/executor（project.py load_contract、executor collect/run command）与 machine contract 的 `[unit]`/`[integration]`/`[e2e]` 段——本条将这些语言语义从 kernel 抽出为宿主 adapter 合同 seam。

1. **adapter 合同三接口**（BS-11）：宿主 adapter 合同提供 collect、run_selected 与 normalized result（携带 protocol version）作为统一 seam；Runtime 只消费版本化 tracks-test-result 协议输入；kernel/executor SHALL NOT 直接引用 pytest/JUnit/java 或任何语言语义（NFR-0141 语言中立不变量）。
2. **pytest/JUnit 下沉为第一个 reference adapter**：现行 pytest 路径下沉为第一个 reference adapter，维持 collect/run_selected/JUnit normalise 语义，复用 v0.6 既有逻辑（FR-0255 命令独家所有权与 `{result}` JUnit XML 通道不变）。Archer 在 M-DESIGN 依 `tracks-test-result` 协议声明 host adapter；执行时 Runtime 经 adapter 调用测试收集/选择/结果（`trac run` 门禁、`trac check`）。
3. **未知 adapter / 畸形结果 fail-closed**（BS-11）：IF adapter 未知或结果畸形（缺 AC/IO、identity 错误），THE 系统 SHALL fail-closed 拒绝接入并在事件标注原因，SHALL NOT 穷举语言——kernel 不内置任何语言的特殊分支，未知 adapter 一律拒绝（NFR-0141）。
4. **用户/宿主经 adapter 声明语言**：宿主通过 adapter 声明语言；断言结果经同一 normalized channel 交付（report/replay/审计），仅 adapter 代收差异——kernel 与协议层对语言无感知。

**用户可观察结果**：操作者从 `trac run` / `trac check` 看到 adapter 声明与 normalized result；`trac replay` / `trac report` 可审计 adapter 身份与 protocol version；kernel 无语言语义分支。

**关键失败/恢复边界**：未知 adapter / 畸形结果 → fail-closed 拒绝接入；reference adapter 行为与 v0.6 既有 pytest 路径一致（不变更已交付语义）。

### FR-0265 executable trace candidate-bound 闭环锁死（修改 v0.6 FR-0253 出口门禁）

- **来源**：`BS-12` / `§3.6` / seed 阶段 7
- **交付入口**：`E-01`（`trac check trace`；`trac run` 验收/门禁；`trac status` / `trac replay`）

本条是对 v0.6 FR-0253 FULL 链出口门禁 trace 闭合的强化（修改，§3.6 变更基线），不是叠加新机制。v0.6 已有 TRACKS-TRACE 绑定 marker 与 `trac check trace` 闭合校验（FR-0080，三缺口即其暴露的缺口，FR-0256 补齐），但未形成 candidate-bound baseline/mutation evidence → FULL pass 的机器锁死闭环。

1. **闭环拓扑**（BS-12）：executable trace SHALL 形成闭环：approved AC → test-plan/public outlet → 实际 collected node → candidate-bound baseline evidence → mutation evidence（FR-0262/0263）→ 同一 candidate FULL pass。环中任一环节缺失即 fail-closed 阻止退出（`trac check trace` 与 M-IMPL ISLAND_GATE_2 出口门禁双闭合）。
2. **阻断条件**（BS-12）：WHILE node 缺失、skip/xfail、身份漂移或控制组失败，THE 系统 SHALL 阻止退出——node 缺失（collected node 与 AC 绑定断裂）、skip/xfail（被选节点未真实执行）、身份漂移（selection/evidence identity 不一致，NFR-0130/0140）、控制组失败（mutation experiment 的 control node 未保持绿，FR-0263）均阻断。
3. **candidate-bound**：baseline evidence 与 mutation evidence 必须绑定同一 candidate（candidate/patch digest 一致，FR-0262 manifest 字段），SHALL NOT 以他 candidate 的证据为本次 candidate 闭环背书——堵证据复用洞（继承 v0.6 NFR-0130 identity 判据，扩展至 mutation evidence）。

**用户可观察结果**：操作者从 `trac check trace --version v0.7` 看到 `closure=candidate-bound` 与闭环各环节绑定；`trac status` / `trac replay` 可审计每棵 AC 的闭环证据链。

**关键失败/恢复边界**：任一环节缺失/漂移/控制组失败 → fail-closed 阻止 M-IMPL 退出与 v0.7-A 验收通过。

### FR-0266 双宿主 9 类 fail-closed 演证与崩溃恢复

- **来源**：`BS-13` / `§3.6` / seed 验收 / Maestro 裁定 A
- **交付入口**：`E-01`（`trac run` v0.7-A 验收；`trac replay` / `trac report`）

v0.7-A 验收阶段，操作者以既有 `trac run` / `trac check trace` / `trac status` 观察双宿主各自执行 9 类 fail-closed 场景；验收通过后进入 v0.7-B（BS-14，见范围排除）。

1. **双宿主**（BS-13，Maestro 裁定 A）：演证 SHALL 在 tracks 自身 + 全新最小 Python/pytest 宿主 demo 上进行。demo 是 tracks 测试资产内动态创建的全新最小 Python/pytest 宿主 repo，使用首个 pytest reference adapter（FR-0263 第 2 步），SHALL 走与真实宿主相同的安装/contract/Runtime 路径，SHALL NOT 调用 tracks 私有测试捷径（§5 约束：demo 形态；§7 风险：demo 路径等价性）。
2. **9 类 fail-closed 场景**（BS-13）：双宿主 SHALL 正向演证以下 9 类场景全部 fail closed：(1) broad mutation、(2) stale patch、(3) 错误 candidate、(4) 未收集 node、(5) 无关下游 Red、(6) target 未死、(7) control 被误伤、(8) adapter/result 畸形、(9) quality guard 三处不一致（FR-0258）。每类场景的演证事件 append-only 落入（NFR-0140）。
3. **崩溃恢复**（BS-13）：正确实验中断/重启后 events 可 `trac replay` / `trac report` 审计，重建且不丢失证据——SHALL 允许正确实验崩溃恢复。
4. **demo 路径等价性**（§7 风险）：demo 机制必须自证与真实宿主路径等价，且有独立校验证据——若被迫调用私有测试捷径，双宿主验收演证不成立，v0.7-A 不成立。

**用户可观察结果**：操作者从 `trac run` / `trac status` 看到 `host=tracks` 与 `host=demo-pytest` 各自的 9 类场景 `all_fail_closed` 与 `crash_recovery=replay_ok`；`trac replay` / `trac report` 可审计每类场景的演证事件与 demo 路径等价性证据。

**关键失败/恢复边界**：任一场景未 fail closed → v0.7-A 验收不通过；demo 路径不等价 → v0.7-A 不成立。

## 非功能需求

### NFR-0140 authenticity/mutation 证据 append-only 可审计性与 fail-closed

- **来源**：`BS-03` / `BS-06`~`BS-10` / `§5 约束`（冻结基线纪律）/ v0.6 NFR-0130

本条扩展 v0.6 NFR-0130（selection/evidence identity 可审计性）至 v0.7-A 的 authenticity 判定与 mutation evidence。`phase0.baseline_repaired` / `phase0.coverage` / `phase0.guard_hardened` / `phase0.sealed` / `authenticity.judged` / `mutation.manifest` / `mutation.experiment` / `guard.parity` 全部 append-only 写入事件流，不改写既有行（继承 v0.5 NFR-0020 append-only 事件溯源与 v0.5 FR-0200 崩溃恢复）。每条 authenticity/mutation 证据绑定 AC/IF + candidate/patch digest + selection/evidence identity + attempt + actor（扩展自 NFR-0130 四元组）。Runtime 中断或重启后由事件回放重建 authenticity/mutation 状态，重建结果与中断前一致；WAL replay 发现无已持久化结果时重跑该实验/判定，绝不把缺失结果当作通过。未知状态、缺失条目、identity 漂移、控制组失败一律 fail-closed——不得视为通过、不得跳过闭环（FR-0265）。Runtime 不信 Agent 自报，必须确定性执行实验并 append-only 落事件（§5 约束：冻结基线纪律）。

### NFR-0141 canonical guard registry parity 与 kernel 语言中立的程序校验

- **来源**：`BS-04` / `BS-11` / `§5 约束`（语言中性不变量、三处一致）

guard registry 三处 parity 校验（FR-0258）必须由 Runtime 程序执行并 fail-closed，不依赖 Agent 自述或评审自言——`guard.parity` 事件携带 registry digest 与三处匹配结果，可由 `trac replay` / `trac report` 复核。kernel 语言中立不变量（FR-0263）：kernel/executor SHALL NOT 出现语言语义（不引用 pytest/JUnit/java 或任何语言特殊分支）；未知 adapter 或畸形结果 fail-closed 拒绝接入（不穷举语言）；该不变量由 `trac validate` 合同校验与 Runtime 执行路径双重复核，非自述。三处一致（Runtime/pre-commit/CI）必须引用同一 canonical quality guard registry 合同或由其生成——Runtime 对 parity 做 fail-closed 程序校验，Prism 对缺失/`--exit-zero`/不一致判 REVISE（FR-0259）。

### NFR-0142 双宿主 demo 路径等价性与崩溃恢复

- **来源**：`BS-13` / `§5 约束`（demo 形态）/ §7 风险

全新最小 Python/pytest 宿主 demo（FR-0266）必须自证与真实宿主路径等价：走与真实宿主相同的安装/contract/Runtime 路径（venv/pinned 工具供给、`git config core.hooksPath`、CI required check 绑定、adapter 合同声明），SHALL NOT 调用 tracks 私有测试捷径。路径等价性以独立校验证据落入事件（可由 `trac replay` / `trac report` 审计）。正确实验中断/重启后 events 可 replay/report 重建且不丢失证据（继承 v0.5 FR-0200 崩溃恢复）。第二语言 adapter 与「跨两种语言的实证」不在本切片（见范围排除）。

## 范围排除

- 不注册 M-VERIFY、不执行 CI SHA 回读、不实现制品构建/发布自动化与 Human release gate（BS-14，seed 末段）——这些属通过本切片可信证据后才启动的 v0.7-B 事项。
- 不实现第二语言/框架 adapter 与「跨两种语言的实证」（Maestro 裁定 A 排除；以 opaque node/result protocol、未知 adapter fail-closed、kernel 无语言语义证明可扩展边界，FR-0263/NFR-0141）。
- 不实现 nightly 结果取回通道（v0.6 已排除，随 result fetch future；v0.6 FR-0255 nightly CI contract 与本切片无关）。
- 不新增 canonical 阶段或既有 trac CLI/CI 之外的交付面（沿用 `trac run` / `trac status` / `trac replay` / `trac report` / `trac check` / `trac validate`；新增顶层命令无）。
- 不变更 v0.6 FR-0250 的节点分类（R1/R2、R2/T-DELTA）、全量 collect、selection identity、tree_stamp、`test.baseline_captured`/`test.selected`/`red.validated` 事件、空 R2 fail-closed、hotfix unit-only 旁路（FR-0244）——FR-0260 只修订 RED_CHECK 的 Red 出处合法性语义，D-41 选择语义兼容演进。
- 不变更 v0.6 FR-0255 的命令独家所有权、`{nodes}`/`{result}` 占位符、并发 flag 内嵌、`run_selected` 缺失 fail-closed——FR-0264 只将语言语义从 kernel 抽出为 adapter seam，reference adapter 复用 v0.6 既有 pytest 逻辑。
