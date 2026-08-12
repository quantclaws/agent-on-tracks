---
spec_id: SPEC-005
created: 2026-08-09
status: draft
sha: 5d784d12d287d6f3fc387544750a8df7fdb09b4d112a03fb1071e2450f407f6a
---

# M-IMPL 阶段推进（Devon 逐 task RGR） — 需求规格

## 修订日志

### R-1（2026-08-09）：flow.md §10 lineage 措辞校正

- **决定**：flow.md §10 lines 574–606 已明定「私有 R ref + G parent=B + trailers」，但 lines 643/655 仍保留旧「B/R/G commit 拓扑」表述。本版本统一为：R 先于 G 的 lineage 由不可变 R ref（`refs/trac/rgr/{run}/{task}/{attempt}/red`）+ G trailer（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）+ Runtime 事件序列联合证明，G 的 parent=B，不作 Git ancestry 拓扑断言。Red 先于实现是程序可验证的 lineage 事实，不是 Agent 自报。
- **取代**：flow.md §10 lines 643/655 残留的「B/R/G commit 拓扑」措辞；BS-07 来源中标注的校正锚点由 spec 阶段承接（本文档完成该承接）。
- **影响 FR**：FR-0070（R 不可变 ref + compare-and-set）、FR-0120（G commit parent=B + trailers）。

> **Lex [RESOLVED]:** 修订日志与 FR 正文存在若干 FR 交叉引用错位，影响下游 M-DESIGN/M-IMPL 的可追踪性（trac validate 不校验修订日志/正文中的 FR 引用语义，故提请 Sage 修订）：(1) R-1 line 16「影响 FR：FR-0070（R 不可变 ref + compare-and-set）、FR-0080（G commit parent=B + trailers）」——FR-0080 是 RED_GATE 合法 Red 分类，不含 G commit；「G commit parent=B + trailers」在 FR-0120（GREEN_COMMIT），应改为 FR-0120。(2) R-2 line 20/22「dispatch materialization 合同的确定性回归基线（FR-0150）」「影响 FR：FR-0150（dispatch 物化完整性合同）」——FR-0150 是 DIAGNOSE 四路诊断与 SHIELD_FIX，不含 dispatch 物化合同；10 项物化增量在 FR-0190（dispatch 物化完整性合同），应改为 FR-0190。(3) FR-0070 line 198 与 FR-0110 line 230 均以「（FR-0130）」标注 gate worktree 组合运行机制，但 FR-0130（REFACTOR 与质量门禁分层）未定义该三 worktree 方案；该机制在 FR-0070 自身正文定义，应自引 FR-0070 或删除括注。以上均为引用错位、FR 实体内容无误，非阻塞。
>> **Sage:** 已逐项核实并修订（trac validate=valid）：(1) R-1 line 16 FR-0080→FR-0120（GREEN_COMMIT 才是 G commit parent=B + trailers，FR-0080 是 RED_GATE 合法 Red 分类）。(2) R-2 line 22/24 FR-0150→FR-0190（dispatch 物化完整性合同在 FR-0190，FR-0150 是 DIAGNOSE 四路诊断与 SHIELD_FIX）。(3) line 200 删除「（FR-0130）」改为「三 worktree 方案在本 FR 定义」（FR-0070 自身正文即定义处，自引冗余）；line 232「FR-0130」→「三 worktree 方案见 FR-0070」（FR-0130 是 REFACTOR 与质量门禁分层，未定义该机制，应交叉引用 FR-0070）。三处 FR 实体内容均未改动，仅校正引用归属。请闭环。

### R-2（2026-08-09）：live run dispatch 物化分析结论承接

- **决定**：S-005 §7 重要风险 1 已完成有监督 M-START->M-TEST 重录（run `01KZ5QCRPMBVC1A6HYEHMKKGVH`）与日志分析，结论为「未知范围发现任务不再传给 spec」。本版本承接其 10 项逐项物化增量作为 dispatch materialization 合同的确定性回归基线（FR-0190）。
- **取代**：S-005 §1 第四条原始输入（作为 v0.5 输入的开放式发现任务）。
- **影响 FR**：FR-0190（dispatch 物化完整性合同）。

### R-3（2026-08-10）：v0.5 planning contract 四项缺口补全

- **决定**：Human return（event seq 395）指出 v0.5 planning contract 存在四项缺口需在 FR/ACC 补全后方可恢复 M-TEST，并裁决数据生命周期：(1) Archer 在 M-IMPL/PLANNING 产出的 `tasks.json` 为 task graph 唯一机器真相（替代原 `task-plan.md`），`tasks.md` 仅为人类可读投影、由 Runtime 确定性生成；(2) `test-plan.md`（M-DESIGN）继续指导 Shield 的 M-TEST 环境、fixture、ground-truth、分层与冻结，不复制到 `tasks.json`；(3) 每个 Devon task 必须携带 GitHub issue number + FR/NFR/ACC/IF/test 引用 + 依赖/批次信息 + 实现意图；Devon 提交时在 commit trailers 中包含 issue# 和 FR/NFR/ACC provenance；(4) Runtime 只做确定性结构校验（schema/引用/DAG/issue shape/scope 边界），语义评审由 Prism 负责；(5) 不预声明精确输出文件集，observed diff 为权威，允许 refactor 和文件拆分。本版本补全四项：FR-0180 扩展为 `tasks.json` schema + 校验语义不变量；新增 FR-0210（Shield test-plan 测试归属边界）；新增 FR-0220（Issues 消费语义）。
- **取代**：FR-0180 原 `task-plan.md` 为真相源的描述性措辞升级为 `tasks.json` 机器真相 + `tasks.md` 投影；FR-0220 原「派发不引用 issue」措辞修正为 task 必须携带 issue number；范围排除「不做 GitHub Issue 映射」条目补充 FR-0220 引用。
- **影响 FR**：FR-0180（扩展 schema + 校验语义）、FR-0210（新增）、FR-0220（新增）。acceptance.md 需平行增补对应 AC（另行 assignment）。

### R-4（2026-08-10）：planning contract FR 级不变量显式化

- **决定**：R-3 在 revision log 与 FR 正文中完成了 tasks.json/task-plan.md 真相源替换与 issue 引用补全，但 Human 裁定的两项产品不变量尚未在 FR 级显式落定：(1)「不将 Shield 准备复制到 tasks.json」——R-3 仅在 revision log 述及「不复制到 tasks.json」，未作为 FR 级不变量；本版本在 FR-0210 增补第 6 项「test-plan 与 tasks.json 内容边界」，显式声明 test-plan.md（M-DESIGN）是 Shield 准备（环境/fixtures/测试数据/ground-truth/黑盒边界/测试层归属/冻结测试权限）的权威来源，tasks.json 不复制 Shield 准备内容、两者职责不重叠。(2) tasks.json schema 的「test 引用」——R-3 schema 标题含「test」但描述仅列 AC/FR/NFR/IF 标识，未显式包含 test 函数/文件引用；本版本在 FR-0180 schema 补全 test 引用描述（test-plan.md §8 AC Coverage 表中归属本 task 的 test 函数/文件标识）。
- **取代**：R-3 revision log 中「不复制到 tasks.json」的隐式表述升级为 FR-0210 第 6 项显式不变量；FR-0180 schema「FR/NFR/ACC/IF/test 引用」描述补全 test 标识。
- **影响 FR**：FR-0180（schema test 引用补全）、FR-0210（新增第 6 项内容边界不变量）。

### R-5（2026-08-12）：S-001 Q-04 release-blocker repair 承接

- **决定**：承接 S-001（Q-04 release-blocker repair）story，在现有 v0.5 spec 基础上**增量**补全「真实 OpencodeBackend live 旅程 + 当前 live 证据的发布前置检查」。新增 FR-0230（真实 OpencodeBackend opt-in live 旅程）、FR-0231（真实 live 旅程审计证据绑定与真实性标记）、FR-0232（`trac check release-evidence` 发布前置检查）、FR-0233（无凭据例行 CI opt-in 例外）与 NFR-0080（release-evidence 检查确定性/可审计性）；新增交付面 E-03。挂载点沿用既有 `trac run` live opt-in 入口与 `trac check` 命令家族（新增 `release-evidence` 子命令）。
- **取代**：无——本版本不取代、不改写既有 FR/NFR 与 ID；既有 M-IMPL 阶段推进需求与 M-IMPL→M-VERIFY 边界保持不变。release-evidence 检查只证明「当前候选有真实成功 live evidence」，不实现 M-VERIFY/M-RELEASE（范围排除同步补充）。
- **影响 FR**：FR-0230~FR-0233（新增）、NFR-0080（新增）、E-03（新增）。

## 界面与入口

### E-01 `trac run` 驱动 M-IMPL 完整循环

```
$ trac run
[run 01KZ...] stage.entered(M-IMPL)
[run 01KZ...] baseline.frozen (baseline current)
[run 01KZ...] taskgraph.committed (T-001..T-012, 12 tasks)
[run 01KZ...] island_gate_1 passed
[run 01KZ...] prism_plan verdict=pass
[run 01KZ...] task.started T-001 (attempt 1)
[run 01KZ...] red.checkpointed T-001/R1 (ref refs/trac/rgr/.../red)
[run 01KZ...] prism_red verdict=pass (T-001, B..R range)
[run 01KZ...] green.committed T-001/G1 (parent=B, trailers bound)
[run 01KZ...] refactor.no_change T-001
[run 01KZ...] prism_final verdict=pass (T-001 full range + lineage)
[run 01KZ...] task.completed T-001
[run 01KZ...] ... (T-002..T-012)
[run 01KZ...] island_gate_2 passed (reach ok, int+e2e green)
[run 01KZ...] stage.exited(M-IMPL)
[run 01KZ...] run.completed(terminal_state="boundary")
$ echo $?
0
```

### E-02 `trac check reach` 孤岛闭合（M-IMPL ISLAND_GATE 消费）

```
$ trac check reach
reach ok
$ trac check reach
island module: src/legacy/orphan.py
$ echo $?
1
```

### E-03 `trac check release-evidence` 当前 live 证据发布前置检查

```
$ trac check release-evidence
release-evidence: satisfied (backend=opencode, run 01KZ..., candidate HEAD abc1234, branch releases/v0.5)
$ echo $?
0

$ trac check release-evidence
release-evidence: NOT satisfied — live evidence missing/failed/stale/SHA mismatch/not real
  next: rerun the opt-in live journey at current HEAD, then re-check
$ echo $?
1
```

## 状态与生命周期

### SM-01 M-IMPL 子状态机

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-01.7）。

1. `stage.entered(M-IMPL)` → BASELINE：M-TEST EXIT（`stage.exited(M-TEST)` + 测试资产冻结）触发
2. BASELINE → PLANNING：baseline current（三件套 + 设计三文档 + 冻结测试资产 digest + contracts + branch + approval 全部有效）
3. BASELINE → NEEDS_ATTENTION：缺失/stale/冲突
4. NEEDS_ATTENTION → BASELINE：已 reconcile
5. NEEDS_ATTENTION → `stage.rolled_back`：rolled_back（return upstream）
6. PLANNING → ISLAND_GATE_1：validate pass（DAG 无环 / scope 不重叠 / required AC 覆盖闭合）
7. PLANNING → PLANNING：validate fail，重派 Archer（≤3）
8. ISLAND_GATE_1 → PRISM_PLAN：闭合（每条 required AC 六项检查通过）
9. ISLAND_GATE_1 → PLANNING：`verdict.failed(island)`
10. PRISM_PLAN → TASK_DISPATCH：`prism.verdict(pass)`
11. PRISM_PLAN → PLANNING：revise → Archer
12. PRISM_PLAN → `stage.rolled_back`：设计缺口 → M-DESIGN
13. PRISM_PLAN → `stage.rolled_back`：需求缺口 → M-SPEC/M-ACC（Human 确认）
14. TASK_DISPATCH → RED：`task.started`（Runtime 按 DAG 依赖选 ready task，单写者 lease + 创建 manifest）
15. RED → RED_GATE：Devon outcome（test-only diff）
16. RED_GATE → RED_CHECKPOINT：合法 Red（行为断言失败 / symbol 缺失）
17. RED_GATE → RED：非法 Red，重派 Devon
18. RED_CHECKPOINT → PRISM_RED：`red.checkpointed`（创建私有 commit R + git ref）
19. PRISM_RED → GREEN：`prism.verdict(pass)` 绑定 R
20. PRISM_RED → RED：revise → 新 attempt
21. GREEN → GREEN_GATE：Devon outcome（从 R tree 恢复 worktree，最小实现，R 测试不可改）
22. GREEN_GATE → GREEN_COMMIT：全过（targeted 单测 + 全部历史单测 + int 子集 + lint/format/type/static + 合同）
23. GREEN_GATE → GREEN：实现缺陷，重派 Devon
24. GREEN_GATE → DIAGNOSE：int 失败归因不明
25. DIAGNOSE → GREEN：实现缺陷 → Devon
26. DIAGNOSE → SHIELD_FIX：测试缺陷 → Shield
27. DIAGNOSE → `stage.rolled_back`：接口/架构不足 → M-DESIGN
28. DIAGNOSE → `stage.rolled_back`：AC/Spec 缺口 → M-ACC/M-SPEC（Human 确认）
29. SHIELD_FIX → GREEN_GATE：重跑受影响测试（Runtime 创建受控测试 commit）
30. GREEN_COMMIT → REFACTOR：`green.committed`（创建正式 commit G，parent=B，trailers 绑定 R/task/attempt）
31. REFACTOR → REFACTOR_GATE：Devon outcome（可返回 no_change + 理由）
32. REFACTOR_GATE → TASK_REVIEW：通过（committed | no_change）
33. REFACTOR_GATE → REFACTOR：失败，重派
34. REFACTOR_GATE → `stage.rolled_back`：动 public interface → upstream
35. TASK_REVIEW → PRISM_FINAL：校验通过（write scope / secret / AC trace / B-R-G-Refactor lineage / budget）
36. TASK_REVIEW → GREEN：budget/scope fail → Devon
37. PRISM_FINAL → TASK_DONE：`prism.verdict(pass)`
38. PRISM_FINAL → GREEN：revise（实现）→ Devon
39. PRISM_FINAL → RED：revise（Red 测试）→ 新 lineage
40. TASK_DONE → TASK_DISPATCH：`task.completed`，还有 ready task
41. TASK_DONE → ISLAND_GATE_2：全部 task 完成
42. ISLAND_GATE_2 → `stage.exited(M-IMPL)` → `run.completed(terminal_state="boundary")`：通过（`trac check reach` 闭合 + 全量 int+e2e 变绿）
43. ISLAND_GATE_2 → DIAGNOSE：全量执行有失败
44. ISLAND_GATE_2 → PLANNING：`verdict.failed(island)`

## 角色与权限

### RP-01 M-IMPL 写范围与执行权

单写者纪律：任一时刻只有一个 actor 持有编辑权；Devon/Shield/Prism 不 commit/push、不推进状态（Runtime 是唯一流程 authority，正式 commit / RGR ref / 受控测试 commit 均由 Runtime 创建并触发宿主 pre-commit 钩子）。

| #   | 操作                                         | Devon | Shield | Archer | Prism | Runtime | Human |
| --- | -------------------------------------------- | ----- | ------ | ------ | ----- | ------- | ----- |
| 1   | 写 unit test（Red phase 只添加）             | ✅     | ❌      | ❌      | ❌     | ✅（受控 commit R） | ❌     |
| 2   | 写产品代码（Green/Refactor phase）           | ✅     | ❌      | ❌      | ❌     | ✅（受控 commit G） | ❌     |
| 3   | 写 integration/e2e/assets/counterexamples    | ❌     | ✅（SHIELD_FIX） | ❌ | ❌ | ✅（受控 test commit） | ❌     |
| 4   | 写 tasks.json（task graph 机器真相源）       | ❌     | ❌      | ✅      | ❌     | ✅（解析/校验/生成 tasks.md 投影） | ❌     |
| 5   | 写 tasks.md（人类可读投影，Runtime 确定性生成） | ❌     | ❌      | ❌      | ❌     | ✅       | ❌     |
| 6   | 创建 git ref `refs/trac/rgr/...`             | ❌     | ❌      | ❌      | ❌     | ✅       | ❌     |
| 7   | 创建正式 commit G（parent=B + trailers）     | ❌     | ❌      | ❌      | ❌     | ✅       | ❌     |
| 8   | 评审 task range / Red checkpoint / final range | ❌  | ❌      | ❌      | ✅     | ❌       | ❌     |
| 9   | commit / push / 推进阶段                     | ❌     | ❌      | ❌      | ❌     | ✅       | ❌     |
| 10  | M-IMPL 退出门禁（裁定退出）                  | ❌     | ❌      | ❌      | ❌     | ✅（程序证据） | ❌（无 Human 门禁） |
| 11  | 回退 M-DESIGN（接口/架构缺口）               | ❌     | ❌      | ❌      | ❌     | ✅（Prism 裁定） | ❌     |
| 12  | 回退 M-ACC/M-SPEC（AC/Spec 缺口）            | ❌     | ❌      | ❌      | ❌     | ✅（展示影响） | ✅（批准需求回退） |

## 功能需求

### FR-0010 M-IMPL 阶段注册与子状态机驱动

- **来源**：`BS-01` / `§3.1`
- **交付入口**：`E-01` / `trac run`

machine.py 注册 M-IMPL 为 StageDef，子状态机按 SM-01（BASELINE → PLANNING → ISLAND_GATE_1 → PRISM_PLAN → TASK_DISPATCH → RGR 循环 → TASK_DONE → ISLAND_GATE_2 → EXIT，含 NEEDS_ATTENTION / DIAGNOSE / SHIELD_FIX 异常分支）。executor `_NEXT_STAGE` 增补 `M-TEST -> M-IMPL` 与 `M-IMPL -> M-VERIFY`（M-VERIFY 未注册，落在 boundary）。

进入条件（SM-01.1）：M-TEST EXIT（`stage.exited(M-TEST)` + 测试资产冻结）触发 `stage.entered(M-IMPL)` 进入 BASELINE。边界退出（SM-01.42）：ISLAND_GATE_2 通过后发出 `stage.exited(M-IMPL)`；因 M-VERIFY 未注册，`run.completed(terminal_state="boundary")` 停在 M-IMPL→M-VERIFY 边界，不进入 M-VERIFY。

M-IMPL 子状态机与既有 DRAFT/REVIEW/EXIT 模式差异较大，`decide()` 为 M-IMPL 增加显式控制流分支（类似 v0.3 的 M-REQ-APPROVAL 与 v0.4 的 M-TEST），不能仅靠 StageDef 注册驱动；该控制流仍维持 kernel 纯函数边界（NFR-0030）。既有 M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL / M-DESIGN / M-TEST 阶段行为不变（承自 v0.3 BS-07 回归安全：泛化是接入不是改既有行为）。

### FR-0020 BASELINE 重算与测试资产冻结

- **来源**：`§3.1 步骤 1` / flow §10.1
- **交付入口**：`E-01` / `trac run`

BASELINE（SM-01.1–.5）：Runtime 重算 baseline，输入集 = 三件套（story/spec/acceptance）+ 设计三文档（architecture/interfaces/test-plan）+ 冻结测试资产 digest + contracts + Issues + branch + approval（flow §10.1 baseline 输入全集；Issues 只消费需求追踪身份，tasks.json↔Issues 双向同步不在本版）。baseline current → PLANNING（SM-01.2）；缺失/stale/冲突 → NEEDS_ATTENTION（SM-01.3），等待 reconcile（→ BASELINE，SM-01.4）或 return upstream（→ `stage.rolled_back`，SM-01.5）。

**测试资产清单冻结**：随 baseline digest 一并冻结测试路径集（frozen test path set）。test-plan.md 层归属字段声明哪些路径属 integration/e2e、哪些属 unit——冻结测试路径集由 Archer 按宿主语言/框架惯例填写（Python 的 `tests/integration` 与 Java 的 `src/it`、Go 的 build-tag 目录同等合法），隔离机制本身语言无关，**不得硬编码**。清单缺层归属声明 → 硬错误，不静默放行（BS-02）。冻结的测试路径集供 test-authority worktree 冻结与 gate worktree 组合运行（FR-0130）。

### FR-0030 PLANNING：Archer 拆 task graph

- **来源**：`BS-01` / `§3.1 步骤 2` / flow §10.1
- **交付入口**：`E-01` / `trac run`

PLANNING（SM-01.6–.7）：dispatch Archer 拆 task graph。`tasks.json` 为 task graph 唯一机器真相（Runtime 解析），`tasks.md` 为人类可读投影（Runtime 确定性生成）。每个 task 纵向切片 + 声明 GitHub issue number + FR/NFR/ACC/IF/test 引用 + 依赖/批次信息 + 实现意图 + scope 边界（manifest 授权范围，不预声明精确输出文件集）+ IF- 集合 + 预算。

validate 校验（SM-01.6→ISLAND_GATE_1 的前置）：DAG 无环 / scope 边界不重叠 / required AC 被至少一个 task 的 IF- 集合覆盖（AC 覆盖闭合）/ issue number 非空且为正整数。validate fail 重派 Archer（≤3，第 3 次升级 `awaiting_human`/escalation）。

`tasks.md` 由 Runtime 确定性生成（从 `tasks.json` 投影），「当前完成了哪一步」的进展投影入 events/db（`tasks.json` 为机器真相源、`tasks.md` 为 Runtime 生成的人类可读投影）。

### FR-0040 ISLAND_GATE_1：程序复核

- **来源**：`§3.1 步骤 3` / flow §10.1 / D-21
- **交付入口**：`E-01` / `trac run`

ISLAND_GATE_1（SM-01.8–.9）：每条 required AC 六项检查（owner / surface / composition / wiring / test / evidence）。六元组是 Archer 设计期义务、M-DESIGN 已由 Prism 逐项闭合，本门禁是该合同的复核而非首次建立（D-21）。闭合 → PRISM_PLAN（SM-01.10）；`verdict.failed(island)` → 回 PLANNING（SM-01.9）。

### FR-0050 PRISM_PLAN：判据包绑定与切片评审

- **来源**：`BS-03` / `§3.1 步骤 4` / D-29
- **交付入口**：`E-01` / `trac run`

PRISM_PLAN（SM-01.10–.13）：dispatch Prism 评审 task graph 切片。反自述三件套（D-29，承自 v0.4 FR-0040）：① assignment 写明应加载判据包的名称+版本（Runtime 决定，Prism 不自选）；② Prism verdict outcome 携带实际加载判据包 identity；③ Runtime 回读核对——不匹配判失败重派 Prism。适用于 plan / red / final / diagnostic 全部四种 Prism 派发（BS-03）。

`prism.verdict(pass)` → TASK_DISPATCH（SM-01.14）；revise → Archer（SM-01.11）；设计缺口 → M-DESIGN（SM-01.12）；需求缺口 → M-SPEC/M-ACC（Human 确认，SM-01.13）。revise 必须经 `trac discuss` 锚定线程（承自 v0.3 Prism 评审协议：revise 无锚定线程判 `revise_without_findings` failure）。

### FR-0060 TASK_DISPATCH：DAG 调度与单写者 manifest

- **来源**：`§3.2 入口` / flow §10.1 / flow §10.3 硬规则 1
- **交付入口**：`E-01` / `trac run`

TASK_DISPATCH（SM-01.14）：Runtime 按 DAG 依赖选 ready task（v0.5 串行调度，`[P]` 标记记录但不并发执行）。单写者 lease + 创建 manifest（per-task 白名单 + writelock lease）。`writelock.granted` 事件落盘后进入 RED（`task.started`）。

task 变绿子集 = 单测 + 变绿条件（test-plan 声明的 IF- 归属）命中本 task IF- 集合的 int 子集。全部 task 完成 → ISLAND_GATE_2（SM-01.41）；还有 ready task → 回 TASK_DISPATCH（SM-01.40）。

### FR-0070 RED：Devon 隔离与私有 R ref

- **来源**：`BS-04` / `BS-06` / `§3.2 步骤 1/3` / flow §10.3 硬规则 2/3
- **交付入口**：`E-01` / `trac run`

RED（SM-01.15）：dispatch Devon（phase=red）在 Devon candidate worktree 运行。Devon 只添加 unit test，不碰产品代码与 Shield 测试（flow §10.3 硬规则 1）。outcome 必须是 test-only diff。

**Devon 隔离（时态 worktree 方案，BS-04）**：Runtime 在 M-DESIGN pass 后记录共同基线 `C_design`，在 Shield WRITE 之前创建 Devon candidate worktree，因此 Devon 天然没有之后生成的 Shield tests。Devon 可使用 glob/grep/bash 探索 production 代码并在项目 venv 运行并行 unit/guards，但不得主动读/运行/改 Shield frozen tests。Devon 的全部文件访问能力（read/glob 等文件工具 + shell）被 per-agent harness 边界限定到 Runtime 物化的隔离 worktree 与获准临时目录，原始 checkout 与其他可恢复的 Shield 测试内容均不可读。Shield 在 test-authority worktree 冻结测试（frozen bundle）；Runtime 在独立 gate worktree 组合 C_design + frozen bundle + Devon candidate 运行 integration/e2e（三 worktree 方案在本 FR 定义）。frozen bundle 永不合入 Devon candidate。bootstrap/manual 无 temporal worktree 时依赖 manifest + prompt 约定，不永久 fail closed（屏蔽/冻结路径集仍由 Archer 按宿主惯例在 test-plan 层归属字段声明、随 BASELINE 冻结、不得硬编码）。per-agent 文件范围 + shell 权限由 `trac init` 按当前 harness 生成等价条目写入 harness 配置（D-19 external_directory 同时约束 read 与 bash）。Devon.md 不含 permission 块（D-26）。

RED_CHECKPOINT（SM-01.18）：Runtime 创建私有 commit R，写 git ref `refs/trac/rgr/{run}/{task}/{attempt}/red`；`red.checkpointed` 事件。**R 不可变（BS-06）**：同一 attempt 重试试图改写 R 时 compare-and-set 失败并开新 attempt，旧 attempt 不被改写。

### FR-0080 RED_GATE：合法 Red 分类

- **来源**：`§3.2 步骤 2` / flow §10.1 / BS-08
- **交付入口**：`E-01` / `trac run`

RED_GATE（SM-01.16–.17）：Runtime 校验预期失败。合法红 = 行为断言失败 / symbol 缺失（flow §10.1 口径）。非法红（collection/语法/fixture/import 错误、测试意外通过）重派 Devon（SM-01.17）。

复用 v0.4 RED_GATE 分类器框架，但其桩合同 token 失败条款（`NotImplementedError("IF-...")`）属 M-TEST 合同测试场景，不在本门禁之列——M-IMPL 的合法红是 unit 层行为断言失败 / symbol 缺失。

### FR-0090 PRISM_RED：Red checkpoint 范围评审

- **来源**：`BS-03` / `§3.2 步骤 3` / D-29
- **交付入口**：`E-01` / `trac run`

PRISM_RED（SM-01.19–.20）：dispatch Prism 评 B..R 范围（Red 测试确实测了该 task 声明的 IF/AC，且未测多余）。反自述三件套（D-29，同 FR-0050）。`prism.verdict(pass)` 绑定 R → GREEN（SM-01.21）；revise → RED 新 attempt（SM-01.20）。

### FR-0100 GREEN：最小实现与受控 diff 回灌

- **来源**：`§3.2 步骤 4` / flow §10.1
- **交付入口**：`E-01` / `trac run`

GREEN（SM-01.21）：从 R tree 恢复 Devon candidate worktree；dispatch Devon（phase=green）——最小实现，R 测试不可改。完成后受控 diff 按 manifest 回灌主仓，视图终态清理 + 崩溃 reconcile（承 v0.2 物化合同）。

### FR-0110 GREEN_GATE：粒度门禁与反馈脱敏

- **来源**：`BS-05` / `BS-08` / `§3.2 步骤 5` / flow §10.1
- **交付入口**：`E-01` / `trac run`

GREEN_GATE（SM-01.22–.24）：targeted 单测 + 全部历史单测 + test-plan 变绿条件命中本 task IF- 集合的 int 子集 + lint/format/type/static + 合同。**第一轮不跑 e2e**（v0.4 既定粒度，BS-08）。integration/e2e 由 Runtime 在独立 gate worktree（组合 C_design + frozen bundle + Devon candidate）运行并归因（D-32 外圈合同循环，三 worktree 方案见 FR-0070）。

**反馈脱敏（BS-05）**：单测失败回完整输出；int/e2e 失败只回分类诊断（哪条 IF 契约失败 / symbol 缺失类型），不回断言原文——防止冻结测试内容经测试输出侧信道泄漏给 Devon（与 BS-04 时间隔离互补）。int 失败归因不明 → DIAGNOSE（SM-01.24）。

实现缺陷 → 回 GREEN 重派 Devon（SM-01.23）；全过 → GREEN_COMMIT（SM-01.22）。

### FR-0120 GREEN_COMMIT：正式 commit G 与 lineage 证明

- **来源**：`BS-07` / `§3.2 步骤 6` / flow §10.3 硬规则 2 / 修订日志 R-1
- **交付入口**：`E-01` / `trac run`

GREEN_COMMIT（SM-01.30）：Runtime 创建正式 commit G（G 的 parent=B，flow §10 既有合同）。trailers 记录 R/task/attempt identity（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）+ issue# + FR/NFR/ACC provenance（Human 裁定：Devon 提交时在 trailers 中包含 issue# 和 FR/NFR/ACC 引用，使后续 bug fix 保留 provenance）。`green.committed` 事件。

**R 先于 G 的 lineage 证明（BS-07，修订日志 R-1）**：R 先于 G 由不可变 R ref（`refs/trac/rgr/{run}/{task}/{attempt}/red`）+ G trailer（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）+ Runtime 事件序列联合证明，不作 Git ancestry 拓扑断言（G 与 R 均以 B 为父节点，Git ancestry 无法证明 R 先于 G）。Red 先于实现不是 Agent 自报——程序可验证的 lineage 事实由 ref + trailer + 事件序列三件联合兑现。本版本校正 flow.md §10 lines 643/655 残留的「B/R/G commit 拓扑」措辞（修订日志 R-1）。

### FR-0130 REFACTOR 与质量门禁分层

- **来源**：`BS-10` / `BS-11` / `§3.2 步骤 7/8` / Q-03 裁定 A / tracks-quality-guards skill
- **交付入口**：`E-01` / `trac run`

REFACTOR（SM-01.31）：dispatch Devon（phase=refactor，仍在 Devon candidate worktree）——可返回 no_change + 理由（BS-10，不强制产生改动，质量由门禁而非改动量保证）。

REFACTOR_GATE（SM-01.32–.34）：重跑 GREEN_GATE 全部检查。质量门禁分层（BS-11，Q-03 裁定 A = tracks-quality-guards 分层执行，遵循 §1 测试代码强制策略）：

- **生产代码**：执行完整四段——ruff + flake8 CCR001（认知复杂度）+ pylint R0801（重复）+ pylint C0302（文件长度）+ pylint R0915（方法长度）+ pylint R0914（参数数量）。任一判失败重派 Devon。
- **测试代码**：仅执行重复 R0801 + 文件长度 C0302（或宿主等价守卫），**不套用** CCR001 / R0915 / R0914。宿主守卫配置与 Runtime 门禁继承同一分层政策。

动 public interface → upstream（SM-01.34）。通过（committed | no_change）→ TASK_REVIEW（SM-01.32）。

### FR-0140 TASK_REVIEW 与 PRISM_FINAL

- **来源**：`§3.2 步骤 8` / flow §10.1
- **交付入口**：`E-01` / `trac run`

TASK_REVIEW（SM-01.35–.36）：Runtime 校验 task range——write scope / secret / AC trace / B-R-G(-Refactor) lineage / budget。校验通过 → PRISM_FINAL（SM-01.37）；budget/scope fail → GREEN 重派 Devon（SM-01.36）。

PRISM_FINAL（SM-01.37–.39）：dispatch Prism 评完整 range + lineage。反自述三件套（D-29）。`prism.verdict(pass)` → TASK_DONE（SM-01.40）；revise（实现）→ GREEN（SM-01.38）；revise（Red 测试）→ RED 新 lineage（SM-01.39）。

### FR-0150 DIAGNOSE 四路诊断与 SHIELD_FIX

- **来源**：`BS-12` / `§3.3` / flow §10.3 硬规则 5 / flow §17
- **交付入口**：`E-01` / `trac run`

DIAGNOSE（SM-01.25–.28）：dispatch Prism 四路诊断——「测试错还是实现错」的分流**永不交给 Human**（flow.md §10.3 硬规则 5，BS-12）。四路路由：

1. 实现缺陷 → 回 GREEN 重派 Devon（SM-01.25）
2. 测试缺陷 → SHIELD_FIX dispatch Shield 修测试（Devon 不得改测试，SM-01.26）；Runtime 创建受控测试 commit（`test.committed`）→ 重跑 GREEN_GATE（SM-01.29）
3. 接口/架构不足 → return upstream M-DESIGN（SM-01.27，Archer+Prism 裁定，不需 Human）
4. AC/Spec 缺口 → return upstream M-ACC/M-SPEC（SM-01.28，Human 批准后回退）

分流结论落 `verdict.failed(reason)` 事件（`red_invalid` / `regression` / `budget` / `island` / `scope` / `test_defect` / `impl_defect`）。return upstream 后目标之后的 task graph / baselines / lineage / commits 标记 stale/superseded，不得复用旧绿色证据（flow §17.1）。

### FR-0160 ISLAND_GATE_2：出口门禁与边界退出

- **来源**：`BS-13` / `BS-14` / `§3.4` / flow §10.1
- **交付入口**：`E-01` / `trac run`

ISLAND_GATE_2（SM-01.42–.44）：最终孤岛闭合复查（`trac check reach` 无孤岛——工具本体由 v0.4 FR-0090 交付，本版本消费）+ 全量 integration + e2e 变绿（出口门禁，BS-13）。全量执行有失败 → DIAGNOSE（SM-01.43）；`verdict.failed(island)` → 回 PLANNING（SM-01.44）。

通过 → `stage.exited(M-IMPL)` → `run.completed(terminal_state="boundary")`（SM-01.42，BS-14）。停在 M-IMPL→M-VERIFY 边界，M-VERIFY 实现不在本 release。M-IMPL 无 Human 门禁：不等待 `human.review` 或 `human.approval`，退出依据全部是程序证据。

### FR-0170 Devon opencode agent 接入与 manifest 越界审计

- **来源**：`BS-04` / `BS-09` / `§3.2` / `§5 约束` / Devon.md / D-26
- **交付入口**：`trac run`（M-IMPL RED/GREEN/REFACTOR 派发 Devon）

Devon 作为真实 opencode agent 接入（承自 v0.3 Archer/Prism 与 v0.4 Shield 接入模式）：

- `AGENT_NAME` 增补 `devon -> Devon`；Devon prompt/skill 物化与回收与 Archer/Prism/Shield 同构。
- Devon.md 加入 deliverables 一致性集合（`check_deliverables` 校验其 version + IQ frontmatter），与既有六个 agent 提示词同构。
- Devon.md 不含 permission 块（D-26：agent 定义 harness 无关、不含 permission 块——Devon 隔离由时态 worktree + 约定承担，FR-0070）。

**manifest 越界审计（BS-09）**：Devon 修改 manifest 白名单之外的文件时 outcome failed、记录路径级证据、不提交。越权写文件被审计检出并通过 git 回滚（`over_reach` failure_class，承自 v0.3 写范围审计机制）。单写者纪律的强制边界，越界即失败并留路径级证据。

### FR-0180 tasks.json / tasks.md 真相源、schema 与校验语义

- **来源**：`§3.1 步骤 2` / `§5 约束` / Human 裁定（tasks.json 为唯一机器真相） / flow §10.1 PLANNING / 修订日志 R-3
- **交付入口**：`trac run`（Runtime 解析 tasks.json）+ `trac validate --file tasks.json`（独立 CLI 校验）

**`tasks.json` 为 task graph 唯一机器真相**（Human 裁定：tasks.json 是 Archer 在 M-IMPL/PLANNING 为 Devon 产生的唯一机器真相），Runtime 解析驱动 DAG 调度（FR-0030）。`tasks.md` 为人类可读投影，由 Runtime 确定性生成（Human 裁定：tasks.md 仅为人类可读投影，宜由 Runtime 确定性生成）。「当前完成了哪一步」的进展投影入 events/db。

**`tasks.json` schema（产品不变量，修订日志 R-3）**：Archer 在 PLANNING 产出的 `tasks.json` 必须为每个 task 声明以下信息项，缺任一项判 validate fail（SM-01.7）：

- **task ID**：唯一标识（如 `T-001`），DAG 依赖引用此标识
- **GitHub issue number**：关联的 GitHub issue 编号（正整数）；Devon 提交时在 commit trailers 中包含 issue# + FR/NFR/ACC provenance（Human 裁定）
- **纵向切片描述 + 实现意图**（flow §10.1：纵向切片）
- **FR/NFR/ACC/IF/test 引用**：本 task 覆盖的 AC 标识（如 `AC-FR0030-01`）+ 关联 FR/NFR 标识 + IF- 标识列表 + 关联 test 引用（test-plan.md §8 AC Coverage 表中归属本 task 的 test 函数/文件标识），供 required AC 覆盖校验与 provenance 追溯
- **scope 边界**：manifest 授权范围（FR-0030，flow §10.1）；不预声明精确输出文件集，observed diff 为权威，允许 refactor 和文件拆分（Human 裁定）
- **依赖/批次信息**：依赖的 task ID 列表（`-` 表示无依赖，可立即调度）+ 批次标记
- **并行标记**：`[P]` 表示可并行（v0.5 串行只记录不并发执行，范围排除）；空表示串行
- **IF- 集合**：本 task 实现的 IF- 标识列表（flow §10.1：每 task 声明实现的接口）；标识必须在 interfaces.md §5 注册表中已定义（有效性校验）
- **预算**：本 task 的 attempt 预算（≤3，与 `m_impl_attempt` 共享）

`tasks.json` 还须包含 Dependency Graph（与 task 依赖列一致的有向图）与 Runtime Review Result checklist（each task associated with correct test / dependencies marked correctly / parallel markers reasonable / no missing spec requirements）。具体 JSON schema 由设计层（interfaces.md §1d TaskNode）承接。

**`trac validate --file tasks.json` 校验语义（产品不变量，修订日志 R-3）**：独立 CLI 与 PLANNING 内联校验（SM-01.6）使用同一组校验规则——Runtime 只做确定性结构校验，语义评审由 Prism 负责（Human 裁定）：

1. **DAG 无环**：依赖关系形成的有向图无环（拓扑排序）；环 -> fail，错误指明环路（如 `cycle: T-001->T-002->T-001`）
2. **scope 边界不重叠**：各 task 的 scope 边界集合交集为空；重叠 -> fail，错误指明冲突 task 与范围
3. **required AC 覆盖闭合**：acceptance.md 中每个 required AC 至少被一个 task 的关联 AC + IF- 集合覆盖；缺口 -> fail，错误指明未覆盖 AC ID
4. **IF- 标识有效性**：task 声明的 IF- 标识在 interfaces.md §5 注册表中已定义；未注册 -> fail
5. **issue number 有效性**：每个 task 的 GitHub issue number 非空且为正整数；缺失/非法 -> fail

任一校验不满足 -> 非零退出 + stderr 指明位置（task ID / AC ID / issue number）。PLANNING 内联校验 fail 重派 Archer（≤3，SM-01.7，第 3 次升级 `awaiting_human`/escalation）；独立 CLI 校验 fail 仅报告不重派。既有 IF- 归属校验（v0.4 FR-0140，每条 integration/e2e AC 的 IF- 归属非空且已注册）行为不回归。

**Runtime 与 Prism 职责分工（Human 裁定）**：Runtime 校验 schema / 引用 / DAG / issue shape / scope 边界等确定性结构不变量；Prism 评审语义质量（task 分解合理性、issue 分组质量、ground truth 正确性、tasks.md 语义保真度）。`tasks.md` 语义保真度不由 Runtime 校验，由 Prism 评审。

### FR-0190 dispatch 物化完整性合同

- **来源**：`§3.1 步骤 1/2` / `§7 重要风险 1` / 修订日志 R-2 / S-005 §7 逐项物化增量
- **交付入口**：`trac run`（Runtime 在 `command.issued` 前物化 assignment 上下文）

Runtime dispatch 必须在 `command.issued` 前向 agent assignment 物化完整上下文，agent 不得自行搜索/猜。物化字段集（承自 S-005 §7 live run 分析结论的 10 项逐项增量，修订日志 R-2）：

1. **state-specific Human return**：M-TEST/M-IMPL escalation 必须允许 `to_stage=M-DESIGN`，不能用 requirement-only gate（M-TEST 为既有回归基线，run 01KZ5QCRPMBVC1A6HYEHMKKGVH seq 223-224；M-IMPL 为 v0.5 新增 escalation 路径 SM-01.12/27 → M-DESIGN，同一不变量覆盖）。
2. **canonical 路径**：canonical `.tracks/project/project.toml` 是唯一允许的 `.tracks/**` project contract 路径；随设计 checkpoint 去重提交，其他 `.tracks/**` 仍 fail closed。
3. **M-DESIGN 输出合同**：test-plan canonical `## 8. AC Coverage`，interfaces canonical `## 5. IF Registry`；每条 integration/e2e AC 解析为 `{ac_id, layers, if_ids}`，missing/empty registry、坏/缺 header、duplicate AC、empty test cell、unregistered IF 均 fail closed；standalone `trac validate test-plan.md` 同门禁。
4. **test_tasks 注入**：Runtime 在 `command.issued` 前向 Shield assignment 注入非空 `test_tasks`；仅 role=Shield/substate=WRITE 适用；无效输入不调用 backend，failed outcome=`stub_gap`，自动回 M-DESIGN。
5. **ResultCheckpoint**：invalid result retry 必须按 actor/substate 清 dispatch flags；Shield WRITE `requires_diff=true`，无 tests diff 不得发布 `test.written`；动态 test path allowed/artifact 集合从 WAL 持久化 `pre_dirty_snapshot` 与 post 内容身份比较，不可只做路径集合差；crash recovery 复用 persisted snapshot；不混入 unchanged Human dirty files。
6. **collection 口径**：所有 `tests/**/*.py`（含 conftest/helper）可 checkpoint，但 collect-only 只对 `test_*.py`/`*_test.py` test modules；只有 helper 时 fail closed；conftest 不得被单独判 no-tests。
7. **host project command**：contract 中相对 `.venv/bin/python{,3}` 在宿主 worktree 不存在时，只能回退当前 Runtime 的 venv `sys.executable`，不得系统 Python；项目自有 venv 存在时优先。
8. **rollback evidence**：仅 M-TEST `stub_gap`→M-DESIGN 清 stale failure，`scope_overflow` 等语义回退仍保留 evidence。
9. **dispatch materialization 完整性**：绝对 target doc/doc-set、role/substate/attempt/review_round、docs/templates/skills、criteria-pack identity、test_tasks、pre_dirty_snapshot、result/checkpoint identity 均由 Runtime 物化；agent 不得自行搜索/猜。
10. **覆盖范围**：覆盖 Scribe TRIAGE/DRAFT/RESPOND、Sage、Lex、Archer DRAFT、Prism design/test review、Shield WRITE，以及 Runtime validate/checkpoint/publish/collect/run/red/commit/seal。

> **Lex [RESOLVED]:** FR-0190 物化字段集第 1 项将 S-005 §7 逐项发现的「M-TEST escalation 必须允许 to_stage=M-DESIGN」改为「M-IMPL escalation 必须允许 to_stage=M-DESIGN」。该 live run 分析（run 01KZ5QCRPMBVC1A6HYEHMKKGVH）的发现场景是 M-TEST stub_gap->M-DESIGN 回退（seq 223-224），story §7 据此把 M-TEST escalation 锁为 v0.5 确定性回归基线。本 FR 声称「承自 S-005 §7 ... 10 项逐项增量」但该项的 stage 引用发生了变更。请确认：(a) 这是有意将发现泛化到 v0.5 新增的 M-IMPL escalation（SM-01.12/27 -> M-DESIGN）吗？若是，建议写为「M-TEST/M-IMPL escalation」以同时覆盖既有回归与新场景；(b) M-TEST escalation 的回归基线是否仍由第 10 项覆盖范围（Runtime validate/checkpoint/publish/collect/run/red/commit/seal）隐式锁定，还是需要在此显式保留 M-TEST 引用？非阻塞，但影响 dispatch 物化合同的回归覆盖可断言性。
>> **Sage:** 采用建议 (a)：第 1 项改为「M-TEST/M-IMPL escalation 必须允许 to_stage=M-DESIGN」，并括注依据（M-TEST 为既有回归基线 run 01KZ5QCRPMBVC1A6HYEHMKKGVH seq 223-224；M-IMPL 为 v0.5 新增 escalation 路径 SM-01.12/27 → M-DESIGN，同一不变量覆盖）。理由：story §7 line 282 锁定的回归基线是 M-TEST escalation（run 实际场景即 stub_gap→M-DESIGN），不应丢失；M-IMPL 新增 SM-01.12/27→M-DESIGN 同属 dispatch 物化不变量，与 M-TEST 共享同一「state-specific Human return」语义，合并表述既保留回归可断言性又覆盖新场景。第 4/8 项仍保留 M-TEST 语义不动（其发现场景本就是 M-TEST），无需改动。第 10 项覆盖范围（Runtime validate/checkpoint/publish/collect/run/red/commit/seal）是横切覆盖，不按 stage 区分，足以隐式锁定两个 stage 的回归。trac validate=valid。请闭环。

本 FR 锁定物化合同的不变量与覆盖范围；具体字段 schema 与物化实现属设计层（Archer architecture.md 承接），本 FR 不指定内部实现。

### FR-0200 可休眠与崩溃恢复

- **来源**：`BS-15` / `§3.2` / flow §10.3 硬规则 4
- **交付入口**：`E-01` / `trac run`

每个 phase 边界都是事件，重启从 lineage + 事件回放恢复到精确 phase，不重跑已完成的 task（BS-15）。崩溃 reconcile 承 v0.2 物化合同：受控 diff 回灌后视图终态清理。R ref 不可变（FR-0070）保证崩溃后 lineage 证据不丢失；G commit 的 trailers 保证崩溃后 R-G 绑定可重建。

### FR-0210 Shield test-plan 测试归属与黑盒边界

- **来源**：`§3.3` / `§5 约束` / flow §9 / D-32 外圈合同循环 / test-plan §1.1/§1.2 / 修订日志 R-3
- **交付入口**：`trac run`（M-IMPL SHIELD_FIX 派发 Shield，SM-01.26）+ `trac validate`（test-plan.md 层归属校验，FR-0190 第 3 项 M-DESIGN 输出合同）

Shield 在 M-IMPL 承担 integration/e2e 测试的**测试归属与黑盒边界**不变量（D-32 外圈合同循环；既有 v0.4 Shield WRITE 模式延续）：

1. **测试层归属**：integration 测试覆盖 interfaces.md 模块接口契约；e2e 测试覆盖用户可见的 happy path；ground truth 由独立方提供（非实现者）。Devon 写 unit test（Red phase），Shield 写 integration/e2e（SHIELD_FIX，SM-01.26）；Devon 不得改 Shield 测试（flow §10.3 硬规则 1，FR-0070）。
2. **黑盒可观察边界（test-plan §1.1/§1.2）**：测试只断言系统外部可观察对象——CLI stdout/stderr/exit code、`.tracks/runtime/tracks.db` events 表、文档文件、git refs/commits/worktree、`project.toml`/agent 提示词 schema、`trac check` 结构化输出、`command.issued` payload。内部数据结构（kernel State 字段、executor subprocess 管理、taskgraph/rgr/worktree/quality_gate 内部表示、opencode prompt 构造、audit manifest 表示）不直接依赖——需要时可观察的内部状态必须经 interfaces.md 提供出口（test-plan §1.2 Observable contract）。
3. **AC 变绿条件**：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识，interfaces.md §5 注册表取值）；M-IMPL task 变绿子集划分按此归属筛选命中本 task IF- 集合的 integration 测试（FR-0060/FR-0140）。
4. **测试修改边界**：Shield 仅在 SHIELD_FIX（SM-01.26，DIAGNOSE 判定测试缺陷，SM-01.25→.26）写 integration/e2e/assets/counterexamples；Runtime 创建受控测试 commit（`test.committed`）→ 重跑 GREEN_GATE（SM-01.29）。Shield 不写 unit test（Devon 职责）、不改产品代码（Devon 职责）、不写 tasks.json（Archer 职责，RP-01 第 4 项）。
5. **test_tasks 注入消费**：Shield WRITE 按 Runtime 注入的 `test_tasks` 修测试，不自衍 AC 层归属（FR-0190 第 4 项，FR-0150 SHIELD_FIX 路由）。
6. **test-plan 与 tasks.json 内容边界（Human 裁定）**：test-plan.md（M-DESIGN）是 Shield 准备的权威来源——Shield 环境、fixtures/测试数据、ground-truth 来源、黑盒可观察边界、测试层归属、冻结测试权限均由 test-plan.md 定义。tasks.json（FR-0180）仅为 Devon 实现规划与 Runtime 调度服务，**不复制 Shield 准备内容**（Human 裁定：不将 Shield 准备复制到 tasks.json）；两者职责不重叠，tasks.json 的 task 条目通过 AC/IF-/test 引用关联到 test-plan.md 声明的测试归属，不在自身内重复声明测试环境或 fixture 细节。

本 FR 锁定测试归属与黑盒边界不变量；具体测试文件布局、fixtures、CI 钩子属设计层（test-plan.md / architecture.md 承接）。

### FR-0220 Issues 消费语义

- **来源**：`§3.1 步骤 1` / flow §10.1 BASELINE 输入全集 / flow §7.2 硬规则 3 / 修订日志 R-3
- **交付入口**：`trac run`（M-IMPL BASELINE 重算，SM-01.1→.2）+ `trac validate`（baseline 完整性校验）

**Issues 作为 BASELINE 只读输入**（flow §10.1 baseline 输入全集；flow §7.2 硬规则 3：Issues 是需求追踪身份，非执行单元）：

1. **消费语义**：M-IMPL BASELINE 重算时，Issues 作为 baseline digest 输入之一参与 freshness 判定（与三件套 + 设计三文档 + 冻结测试资产 + contracts + branch + approval 并列）。Issues 只消费其**需求追踪身份**（issue number + 关联 spec section），不消费 issue 状态（open/closed）、评论、assignee 等执行元数据。
2. **非执行单元**（flow §7.2 硬规则 3）：实施切片（task graph）在 M-IMPL PLANNING 由 Archer 产出（FR-0030），与 Issues 映射但**不互相冒充**——task graph 不是 Issues 的镜像，Issues 不作为 DAG 节点。
3. **task 携带 issue number（Human 裁定）**：每个 Devon task 在 tasks.json 中必须携带 GitHub issue number（FR-0180 schema 必填项）；Devon dispatch 的 assignment payload 携带 task 关联的 issue number + FR/NFR/ACC provenance；Devon 提交时在 commit trailers 中包含 issue# + FR/NFR/ACC 引用，使后续 bug fix 保留 provenance。Devon 按 tasks.json 的 task ID + IF- 集合 + issue number 工作。
4. **范围排除（tasks.json ↔ Issues 双向同步）**：本版不做 tasks.json task 与 GitHub Issue 的双向同步——issue 创建/更新由 M-REQ-APPROVAL 阶段（v0.2 FR-0200）一次性创建，tasks.json 中 issue number 为只读引用（从 M-REQ-APPROVAL 产出继承），M-IMPL 不回写 task 状态到 issue、不创建子 issue、不关闭 issue。Issues 只在 BASELINE 重算时被读取一次，digest 变化触发 NEEDS_ATTENTION（SM-01.3）。

本 FR 锁定 Issues 消费语义不变量；具体 issue 读取实现（GitHub API / 本地缓存 / mock）属设计层，本 FR 不指定。范围排除条目引用本 FR（见范围排除）。

### FR-0230 真实 OpencodeBackend 的 opt-in live 旅程

- **来源**：`BS-01` / `BS-02` / `§3.1`
- **交付入口**：`E-01`（既有 `trac run` live opt-in，`TRAC_AGENT_BACKEND=opencode` + 真实凭据）/ 既有 `tests/e2e_live` credential-aware live 通道

当操作者为 M-IMPL live 旅程显式 opt in 并提供真实 OpencodeBackend 所需凭据时，Runtime 用**真实 OpencodeBackend** 派发 Devon，从 M-IMPL BASELINE 开始（M-TEST EXIT + 测试资产冻结，SM-01.1），至少执行一个 task 的完整 RGR 旅程——先经过 RED，再进入 GREEN、REFACTOR、review（TASK_REVIEW/PRISM_FINAL）、task 完成、ISLAND_GATE_2 并到达 M-IMPL boundary（BS-01/BS-02）。该旅程不得由 FakeBackend、模拟 outcome 或人工补写 stage event 代替（BS-01/BS-04）。

操作者可从既有公开运行结果（事件/状态记录、Git R/G lineage、`trac report`）验证该 task 确实依次经过这些阶段（BS-02）。成功时复用 FR-0160 边界退出：发出 `stage.exited(M-IMPL)` / `run.completed(terminal_state="boundary")`（SM-01.42）。失败、取消或证据不完整时只显示失败/未完成状态，**不产生可用于发布的成功证据**，修复后应对当前候选重新运行（BS-04/§5 约束）。

既有 FakeBackend 确定性例行通道保持不变：FakeBackend 继续服务确定性例行测试，但其结果不能成为 release evidence（§5 约束/BS-04）。

### FR-0231 真实 live 旅程的审计证据绑定与真实性标记

- **来源**：`BS-03` / `BS-04` / `§3.1` / `§3.2` / `§5 约束`
- **交付入口**：审计证据存储（供 `trac check release-evidence` 消费，FR-0232）；agent I/O 沿用既有 OpencodeBackend 审计与凭据脱敏惯例

真实 live 旅程成功到达 M-IMPL boundary 时，保存可审计的 **agent I/O 与事件证据**，并关联到：该次 run（run_id）、真实 backend 身份（`backend=opencode`）、事件区间、以及 candidate SHA（live run 启动时被测 tracks 仓库的 git HEAD，见 FR-0232/BS-05）。证据沿用既有凭据脱敏边界（§5 约束），保证发布判断可回溯到实际运行而非依赖 agent 自述（BS-03）。

**真实性边界（BS-04）**：仅当旅程由真实 OpencodeBackend 驱动且成功到达 M-IMPL boundary 时，其证据才可标记为**可用于发布的 live evidence**；由 FakeBackend、simulated outcome、manual stage event 驱动，或旅程未成功到达边界，一律不得标记为可用于发布的 live evidence。此不变量保证失败不伪报成功（§3.1 完成结果）。

### FR-0232 `trac check release-evidence`：当前 live 证据发布前置检查

- **来源**：`BS-05` / `BS-06` / `BS-08` / `§3.2` / Maestro 对 TRIAGE blocker 1/2/3 的裁定
- **交付入口**：`E-03`（`trac check release-evidence`，新增子命令，挂载在既有 `trac check` 家族）

操作者在待发布分支对**当前候选**运行发布前置检查。检查读取最近一次成功的真实 live 旅程及其审计记录，确认记录来自 `backend=opencode`，并可追溯到该次 run 的 agent I/O、事件区间和相关 Git 结果（BS-03/BS-08）。

**candidate SHA 判定（BS-05，Maestro 裁定 A）**：candidate SHA = live run 启动时被测 tracks 仓库的 git HEAD，检查时要求它与待发布分支当前 HEAD 一致。live run 之后任何 commit 都使旧证据 stale，必须对新 HEAD 重跑。

**通过（BS-08）**：current successful live evidence 与待发布分支 HEAD、运行身份和审计记录全部匹配时，报告 `release-evidence: satisfied`（CLI 可见结果 + exit 0）。

**不满足/fail closed（BS-06，Maestro 裁定 A）**：live evidence 缺失、失败、过期、candidate SHA 不匹配，或来源为 FakeBackend/simulated/manual 时，报告未满足及可恢复的下一步（在当前 HEAD 重新执行真实旅程后再次检查），exit 非零，阻止发布继续使用该证据。**Human release approval 不得绕过该 fail-closed 结果**（flow §13.2：Human 不能用发布确认绕过失败的门禁）。

该检查只证明「当前候选有真实成功 live evidence」，**不代替**未来 M-VERIFY/M-RELEASE（§5 约束/FR-0160）；M-IMPL boundary 仍是产品流程边界。

### FR-0233 无凭据例行 CI 的 opt-in 例外

- **来源**：`BS-07` / `§3.3` / Maestro 对 TRIAGE blocker 1 的裁定
- **交付入口**：既有 CI/live 通道（`tests/e2e_live` credential-aware live channel）/ `trac check release-evidence`（发布验证）

例行 CI 没有真实 provider 凭据时，允许跳过 opt-in 真实 Devon 旅程并**清楚报告 skipped**，其他例行检查照常执行；不得把 skip 伪装成 live success（BS-07）。有凭据且显式启用 live 通道时，CI 执行真实旅程并报告成功、失败或取消；成功产出可供 `trac check release-evidence`（FR-0232）核验的 evidence，失败不产出成功证据（BS-07/§3.3）。

发布验证始终显式执行 `trac check release-evidence`（FR-0232）；例行 CI 的 fake/simulated 结果或 credential-less skip 都**不能替代**它（BS-07/§5 约束）。该例外只保护日常 CI 可运行，不改变发布候选必须有 current successful live evidence 的要求（§5 非常规要求）。

## 非功能需求

### NFR-0010 M-IMPL 控制流维持 kernel 纯函数边界

- **来源**：`§5 约束` / v0.3 BS-01 / v0.4 NFR-0030

M-IMPL 子状态机的显式控制流分支（BASELINE/PLANNING/ISLAND_GATE_1/PRISM_PLAN/TASK_DISPATCH/RED/RED_GATE/RED_CHECKPOINT/PRISM_RED/GREEN/GREEN_GATE/GREEN_COMMIT/REFACTOR/REFACTOR_GATE/TASK_REVIEW/PRISM_FINAL/TASK_DONE/ISLAND_GATE_2/DIAGNOSE/SHIELD_FIX/EXIT）在 `decide()`/`project()` 内仍为纯函数：不碰 IO、不读 clock/env、不读文件系统；所有非确定性只作为事件进入。collection 复跑、trace/reach 调用、判据包加载、worktree 操作、git commit/ref 创建等副作用归 executor，不归 kernel（承自 v0.1 NFR-02）。

### NFR-0020 M-IMPL 事件维持 append-only 事件溯源

- **来源**：`§5 约束` / D-02 / v0.4 NFR-0040

M-IMPL 全程事件（`stage.entered` / `baseline.frozen` / `taskgraph.committed` / `task.started` / `writelock.granted|released` / `red.checkpointed` / `prism.verdict(pass|revise)` / `green.committed` / `refactor.committed|no_change` / `verdict.failed(red_invalid|regression|budget|island|scope|test_defect|impl_defect)` / `test.committed` / `task.completed` / `stage.exited` / `stage.rolled_back`）均 append-only 写入 events 表，不改写既有行；投影表可从事件完整重建（承自 D-02）。

### NFR-0030 dispatch 活动性可观测

- **来源**：`§5 约束` / flow §17.3

`trac run` 必须为长时间 Agent 派发发出简洁、已 flush 的控制台活动：开始输出时间戳/Agent/stage(substate)/task(attempt)，完成输出状态/失败与耗时；不得流式输出海量 Agent stdout。生产 Runtime Agent 派发不设 elapsed-time 超时（D-11 取消协议）；活动性由 Runtime/operator 观测，显式 Human Ctrl-C 取消并清理进程组。≤3 次失败后 `trac run` 与 `trac status` 必须暴露 attempt 计数 + 失败类 + 原因；Human 可运行 `trac retry` 追加 `human.retry` 事件、清除升级 gate、重置一份新的 ≤3 attempt 预算、保留失败证据，且不自动重新派发（承自 flow §17.3）。

### NFR-0080 release-evidence 检查的确定性与可审计性

> **Lex [RESOLVED]:** 【Blocker：NFR-0040 跨版本 ID 复用】required check #4 要求核对新 NFR ID 是否符合跨版本不可变/不复用文法。经比对，NFR-0040 已被分配过且含义不同：v0.2『NFR-0040 集成测试状态机全覆盖』、v0.4『NFR-0040 M-TEST 事件维持 append-only 事件溯源』。本次 R-5 又新增『NFR-0040 release-evidence 检查的确定性与可审计性』——同一 ID 第三次复用、含义各异。且 v0.5 内部已存在同号冲突：line 487 NFR-0020 来源仍引用『v0.4 NFR-0040』表示 append-only 事件溯源，而新 NFR-0040（line 497）却是另一语义，旧引用与新实体将歧义。按 ID 不可复用文法（docs/concepts/traceability.md：删除留 tombstone 避免旧测试悄悄指向新需求），该新增 NFR 必须改用从未用过的 ID（NFR-0010~0070 均已被 v0.2/v0.4 占用，建议 NFR-0080 及以上），并同步修订日志 R-5 line 41 的『新增 NFR-0040』表述。FR-0230~0233 本身均为从未使用的新 ID，不构成问题。请 Sage 改用全新 ID 后闭环。
>> **Sage:** 已按要求将新增 release-evidence NFR 从复用的 NFR-0040 改为全新 ID NFR-0080，并同步修订日志；历史 v0.4 NFR-0040 引用保持不变。具体改动位置如下：
>> - **line 41**（R-5 修订日志·决定）：『与 NFR-0040（release-evidence 检查确定性/可审计性）』→『NFR-0080』
>> - **line 43**（R-5 修订日志·影响 FR）：『NFR-0040（新增）』→『NFR-0080（新增）』
>> - **line 497**（NFR 实体标题）：『### NFR-0040 release-evidence 检查的确定性与可审计性』→『### NFR-0080 ...』
>> 未改动：line 487 NFR-0020 来源中的『v0.4 NFR-0040』（指 append-only 事件溯源，为历史引用，按规则保留）。FR-0230~FR-0233 语义未改动。请复核。
>> **Lex:** 复核通过：新增 release-evidence NFR 已改为全新 ID NFR-0080，R-5 修订日志（line 41/43）与需求标题（line 497）一致；未再有正文将 release-evidence 语义指派给 NFR-0040；历史 v0.4 NFR-0040 引用（line 487 NFR-0020 来源）保持不变；trac validate 通过。blocker 已闭环，无其它产品歧义。

- **来源**：`BS-03` / `BS-04` / `BS-06` / `§5 约束`

`trac check release-evidence`（FR-0232）必须**确定性 fail closed**：对缺失、失败、过期、candidate SHA 不匹配或非真实来源的证据**不误报成功**，只依赖可审计证据（agent I/O、事件、Git 结果、`backend=opencode` 身份），不依赖 agent 自述或人工插入。检查本身是可复核的程序门禁：其输入证据与判定结果可由事件/审计记录回溯（沿用 NFR-0020 append-only 事件与凭据脱敏边界）。例行 CI 的 credential-less skip 或 fake/simulated 结果既不产生、也不满足 release evidence（BS-07）。

## 范围排除

- 不实现 M-VERIFY 及后续阶段，只停在 M-IMPL → M-VERIFY 边界（FR-0160，BS-14）。
- 不做 `[P]` 并行调度（只串行，`[P]` 仅记录不并发执行，Aaron 裁定）。
- 不做真实 LLM 通道的其它形态（集成 FakeAgent、end-to-end 真实 Devon——仅此两条通道，真实通道仅以 end-to-end 形式存在，不做真实通道的单元级/集成级接入，Q-04 裁定）。
- 不做 hotfix / bug-fix 变体（flow.md §16，后续 story）。
- 不做 `trac check ratio / dup / budget` 命令（trace/reach 已在 v0.4 交付；budget 在 v0.5 仅体现为 TASK_REVIEW 的 attempt/lineage 预算校验）。
- 不做函数级调用图（reach 维持模块级，M-VERIFY 反 slop 门禁的更细粒度分析属未来）。
- 不做 GitHub Issue 映射（tasks.json ↔ Issues 双向同步不在本版；tasks.json 中 issue number 为只读引用，FR-0220 锁定消费语义，范围排除的是 tasks.json ↔ Issues 双向同步）。
- 不做 M-IMPL 内的 Human 门禁（flow.md：仅有的两个 Human gate 是 M-REQ-APPROVAL 与 M-RELEASE，M-IMPL 全程程序证据）。
- pre-commit hook 钩子拒绝按 F-1（D-30）写钩子输出为证据并在预算内重派，不静默退出死锁——此为既有约束继承，不在本版本新增。
- 不实现 M-VERIFY/M-RELEASE/M-PUBLISH 的 candidate freeze、发布副作用或完整发布阶段；`trac check release-evidence`（FR-0232）只证明「当前候选有真实成功 live evidence」，落在 M-IMPL→M-VERIFY 边界之外、作为独立证据门禁，不提前实现后续阶段（FR-0232，§5 约束）。
- 不把 live 旅程扩展为覆盖全部生产 task——本版只要求至少一个 task 的完整真实 RGR 旅程（FR-0230，§5 Out-of-Scope）。
- 不强制所有例行 CI 或所有开发者提供真实 provider 凭据——credential-less routine CI 允许只跳过 opt-in live 测试（FR-0233），但 skip 不等于发布证据（FR-0232，§5 约束）。
- 不为 live 旅程或发布前置检查新增 CLI/CI 之外的交付面（沿用 `trac run` live opt-in 与 `trac check release-evidence`，§5 Out-of-Scope）。
