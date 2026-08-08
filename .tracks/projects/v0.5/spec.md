---
spec_id: SPEC-005
created: 2026-08-09
status: draft
sha: caacbbda538ff009ef42cd5d3701b7b8253aee82966bfe4e6f385891461c7fe0
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

- **决定**：S-005 §7 重要风险 1 已完成有监督 M-START→M-TEST 重录（run `01KZ5QCRPMBVC1A6HYEHMKKGVH`）与日志分析，结论为「未知范围发现任务不再传给 spec」。本版本承接其 10 项逐项物化增量作为 dispatch materialization 合同的确定性回归基线（FR-0190）。
- **取代**：S-005 §1 第四条原始输入（作为 v0.5 输入的开放式发现任务）。
- **影响 FR**：FR-0190（dispatch 物化完整性合同）。

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
| 4   | 写 task-plan.md（task graph 内容真相源）     | ❌     | ❌      | ✅      | ❌     | ✅（解析/校验） | ❌     |
| 5   | 写 task-log.md（phase 边界进展投影）         | ❌     | ❌      | ❌      | ❌     | ✅       | ❌     |
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

BASELINE（SM-01.1–.5）：Runtime 重算 baseline，输入集 = 三件套（story/spec/acceptance）+ 设计三文档（architecture/interfaces/test-plan）+ 冻结测试资产 digest + contracts + Issues + branch + approval（flow §10.1 baseline 输入全集；Issues 只消费需求追踪身份，task-plan↔Issues 同步不在本版）。baseline current → PLANNING（SM-01.2）；缺失/stale/冲突 → NEEDS_ATTENTION（SM-01.3），等待 reconcile（→ BASELINE，SM-01.4）或 return upstream（→ `stage.rolled_back`，SM-01.5）。

**测试资产清单冻结**：随 baseline digest 一并冻结测试路径集（frozen test path set）。test-plan.md 层归属字段声明哪些路径属 integration/e2e、哪些属 unit——冻结测试路径集由 Archer 按宿主语言/框架惯例填写（Python 的 `tests/integration` 与 Java 的 `src/it`、Go 的 build-tag 目录同等合法），隔离机制本身语言无关，**不得硬编码**。清单缺层归属声明 → 硬错误，不静默放行（BS-02）。冻结的测试路径集供 test-authority worktree 冻结与 gate worktree 组合运行（FR-0130）。

### FR-0030 PLANNING：Archer 拆 task graph

- **来源**：`BS-01` / `§3.1 步骤 2` / flow §10.1
- **交付入口**：`E-01` / `trac run`

PLANNING（SM-01.6–.7）：dispatch Archer 拆 task graph。`task-plan.md` 为 task graph 内容真相源（Runtime 解析）。每个 task 纵向切片 + 声明 scope 白名单（manifest 授权文件集）+ 实现的 IF- 集合 + 预算。

validate 校验（SM-01.6→ISLAND_GATE_1 的前置）：DAG 无环 / scope 不重叠 / required AC 被至少一个 task 的 IF- 集合覆盖（AC 覆盖闭合）。validate fail 重派 Archer（≤3，第 3 次升级 `awaiting_human`/escalation）。

`task-log.md` 由 Runtime 在 phase 边界写入（每 task 一份），「当前完成了哪一步」的进展投影入 events/db（task-plan.md 为内容真相源、task-log.md 为 Runtime 写入的进展投影）。

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

GREEN_COMMIT（SM-01.30）：Runtime 创建正式 commit G（G 的 parent=B，flow §10 既有合同）。trailers 记录 R/task/attempt identity（`Tracks-Task` / `Tracks-Attempt` / `Tracks-R`）。`green.committed` 事件。

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

### FR-0180 task-plan / task-log 真相源与 Runtime 解析

- **来源**：`§3.1 步骤 2` / `§5 约束` / Aaron 裁定（task-plan.md 为真相源）
- **交付入口**：`trac run`（Runtime 解析 task-plan.md）+ `trac validate --file task-plan.md`

`task-plan.md` 为 task graph 内容真相源、Runtime 解析（Aaron 裁定：真相放 task-plan.md）。`task-log.md` 由 Runtime 在 phase 边界写入（每 task 一份）。「当前完成了哪一步」的进展投影入 events/db（Aaron 裁定：当前完成了哪一步放 db）。

task-plan.md 模板既有 Task List（ID / Task description / Related test / Target file / Depends on / Parallel marker / Status）+ Dependency Graph + Runtime Review Result。M-IMPL PLANNING（FR-0030）解析 task-plan.md 驱动 DAG 调度；validate 校验 DAG 无环 / scope 不重叠 / required AC 覆盖闭合。task-log.md 模板既有 Phase 1 Red / Phase 2 Green / Phase 3 Refactor / Runtime Quality Gate，由 Runtime 在对应 phase 边界写入。

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

## 范围排除

- 不实现 M-VERIFY 及后续阶段，只停在 M-IMPL → M-VERIFY 边界（FR-0160，BS-14）。
- 不做 `[P]` 并行调度（只串行，`[P]` 仅记录不并发执行，Aaron 裁定）。
- 不做真实 LLM 通道的其它形态（集成 FakeAgent、end-to-end 真实 Devon——仅此两条通道，真实通道仅以 end-to-end 形式存在，不做真实通道的单元级/集成级接入，Q-04 裁定）。
- 不做 hotfix / bug-fix 变体（flow.md §16，后续 story）。
- 不做 `trac check ratio / dup / budget` 命令（trace/reach 已在 v0.4 交付；budget 在 v0.5 仅体现为 TASK_REVIEW 的 attempt/lineage 预算校验）。
- 不做函数级调用图（reach 维持模块级，M-VERIFY 反 slop 门禁的更细粒度分析属未来）。
- 不做 GitHub Issue 映射（task-plan ↔ Issues 同步不在本版，Issues 只消费需求追踪身份）。
- 不做 M-IMPL 内的 Human 门禁（flow.md：仅有的两个 Human gate 是 M-REQ-APPROVAL 与 M-RELEASE，M-IMPL 全程程序证据）。
- pre-commit hook 钩子拒绝按 F-1（D-30）写钩子输出为证据并在预算内重派，不静默退出死锁——此为既有约束继承，不在本版本新增。
