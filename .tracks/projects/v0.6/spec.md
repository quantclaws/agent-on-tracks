---
spec_id: SPEC-006
created: 2026-08-19
status: draft
sha: 21014f9945c675f4598da9efe0afadea2945fa71d5424598374c8e3b6ff3161d
---

# hotfix 工作流：trac hotfix 入口与继承基线的 M-DESIGN->M-IMPL 旅程 - 需求规格

## 界面与入口

### E-01 `trac hotfix` 入口与 HOTFIX-TRIAGE 出口

```
$ trac hotfix 42 --scenario post-release
run 01KZ... hotfix.requested (issue=42, scenario=post-release)
run 01KZ... triage.prechecked (issue=42 type=bug)
run 01KZ... anchor.validated (AC-FR0030-01@v0.5, AC-FR0030-03@v0.5)
run 01KZ... stage.entered(M-DESIGN) (branch fix/42 from main)
$ trac status
run=01KZ... stage=M-DESIGN substate=... branch=fix/42 scenario=post-release

$ trac hotfix 99 --scenario dev
run 01KZ... hotfix.requested (issue=99, scenario=dev)
run 01KZ... triage.prechecked REJECTED (issue 99 not type=bug)
  next: file a bug issue or use `trac start` for new features
$ echo $?
1

$ trac hotfix 77 --scenario post-release
run 01KZ... hotfix.requested (issue=77, scenario=post-release)
run 01KZ... triage.prechecked (issue=77 type=bug)
run 01KZ... sage_triage NO_ANCHOR (searched: v0.5, v0.4; no correlatable AC)
run 01KZ... awaiting_human (manual anchor or confirm feature route)
$ trac status
run=01KZ... awaiting=awaiting_human origin=hotfix-triage issue=77
```

### E-02 既有 CLI 观察 hotfix run 与边界

```
$ trac run
[run 01KZ...] stage.entered(M-DESIGN)   # hotfix run 已建立，trac run 续跑当前 active run
[run 01KZ...] ... (M-DESIGN delta -> M-TEST RED-first -> M-IMPL isolated branch)
[run 01KZ...] stage.exited(M-IMPL)
[run 01KZ...] run.completed(terminal_state="boundary")
$ trac status
run=01KZ... terminal=boundary branch=fix/42 scenario=post-release
$ trac replay 01KZ...
... hotfix.requested -> triage.prechecked -> anchor.validated ->
    stage.entered(M-DESIGN) -> ... -> run.completed(boundary) ...
$ trac report --run-id 01KZ... --output .tracks/runtime/report --format md
```

### E-03 测试执行选择、失败台账与 FULL 链观察

```
$ trac run
[run 01KZ...] test.baseline_captured(passed) (pre-WRITE R1 snapshot layers=unit+integration+e2e nodes=118 baseline_id=<sha>)
[run 01KZ...] test.collected(passed) (collect=all inherited_r1=118 delta_r2=24)
[run 01KZ...] test.selected (scope=r2_delta basis="v0.6 变更声明" nodes=24 baseline=<sha>)
[run 01KZ...] red.validated(valid) (executed=selected-r2-only; r1_hist=nightly-ci)
[run 01KZ...] prism.verdict(pass)   # Prism 消费当前树 red.validated 证据 + counterexample kill
[run 01KZ...] stage.exited(M-TEST)
...
[run 01KZ...] test.selected (scope=task_if task=T-003 if={IF-PAY-01,IF-PAY-02} nodes=17)
[run 01KZ...] green.committed
[run 01KZ...] evidence.reused (kind=green identity=unchanged reused=<green-evidence-ids>)
...
[run 01KZ...] full.executed (round=FULL_1 suite=unit+integration+e2e failed_nodes=1)
[run 01KZ...] ledger.opened (node=tests/test_pay.py::test_refund state=OPEN)
[run 01KZ...] ledger.transitioned (node=tests/test_pay.py::test_refund OPEN->CLASSIFIED->FIXED)
[run 01KZ...] test.selected (scope=select_diff entry=tests/test_pay.py::test_refund nodes=3)
[run 01KZ...] ledger.transitioned (node=tests/test_pay.py::test_refund FIXED->PROVEN)
[run 01KZ...] full.executed (round=FULL_F passed=true)
[run 01KZ...] stage.exited(M-IMPL)
$ trac status
run=01KZ... stage=M-VERIFY full_reuse=FULL_F (identity unchanged; local full rerun skipped)
$ trac replay 01KZ...
... test.baseline_captured(pre-WRITE R1 snapshot) -> test.collected -> test.selected(r2_delta) -> red.validated -> prism.verdict ->
    full.executed(FULL_1) -> ledger.*(逐条目 FIXED 后立即 SELECT_DIFF 证明至 PROVEN) ->
    full.executed(FULL_F) -> stage.exited(M-IMPL) ...
```

## 状态与生命周期

### SM-01 HOTFIX-TRIAGE 入口子状态机

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-01.4）。HOTFIX-TRIAGE 是 hotfix 入口的附属子状态机（类比 M-START 的子状态集），**不是** canonical 顶层阶段、不进入 §1 序列；其事件落在 hotfix run 自己的事件流（run 于入口即建立）。

1. （新建）-> `PRECHECK`：`trac hotfix <issue> --scenario post-release|dev`（`hotfix.requested`）
2. `PRECHECK` -> `SAGE_TRIAGE`：程序预检通过（`triage.prechecked`：issue 存在且 type=bug、`--scenario` 合法、`dev` 场景存在活跃 release 分支）
3. `PRECHECK` -> `REJECTED`：非 bug / issue 不可定位 / scenario 不合法 / dev 场景无活跃 release 分支
4. `REJECTED` -> 终止：不建 `fix/{issue}` 分支，报告原因与下一步（补全 issue 后重试或改走 `trac start` feature 流程）
5. `SAGE_TRIAGE` -> `ANCHORED`：Sage 锚定 outcome 且程序校验通过（`anchor.validated`：所引每条 AC 真实存在于所指版本 acceptance.md）
6. `SAGE_TRIAGE` -> `SAGE_TRIAGE`：校验失败，重派 Sage（≤3）
7. `SAGE_TRIAGE` -> `AWAIT_HUMAN`：NO_ANCHOR 或 3 次未产出合法锚定
8. `AWAIT_HUMAN` -> `ANCHORED`：Human 指认 AC（人工锚定，`human.anchor(manual)`）
9. `AWAIT_HUMAN` -> `FEATURE_ROUTE`：Human 确认无对应 AC（`human.anchor(feature_route)`）
10. `ANCHORED` -> 终止：Runtime 记录 issue->AC 锚定、按场景创建隔离 `fix/{issue}` 分支、继承目标版本已批准基线、`stage.entered(M-DESIGN)`
11. `FEATURE_ROUTE` -> 终止：退出 hotfix -> backlog/new feature（`backlog.recorded`）；不建 `fix/{issue}` 分支

## 角色与权限

### RP-01 hotfix 入口、隔离分支与缺口路由权限

单写者纪律继承 v0.5 RP-01：任一时刻只有一个 actor 持有编辑权；Devon/Shield/Prism/Archer 不 commit/push、不推进状态（Runtime 是唯一流程 authority 与唯一 branch/worktree authority）。

| #   | 操作                                                  | 操作者 | Sage | Human | Archer | Prism | Devon/Shield | Runtime |
| --- | ----------------------------------------------------- | ------ | ---- | ----- | ------ | ----- | ------------ | ------- |
| 1   | 执行 `trac hotfix <issue> --scenario ...`             | ✅      | ❌    | ❌     | ❌      | ❌    | ❌            | ❌       |
| 2   | 程序预检（issue type/scenario/活跃分支）             | ❌      | ❌    | ❌     | ❌      | ❌    | ❌            | ✅       |
| 3   | 语义锚定既有 AC（依据 issue 症状 + 可选字段）        | ❌      | ✅    | ❌     | ❌      | ❌    | ❌            | ❌       |
| 4   | 程序校验锚定输出（AC 真实存在于所指版本 acceptance） | ❌      | ❌    | ❌     | ❌      | ❌    | ❌            | ✅       |
| 5   | 重派 Sage（校验失败，≤3）             | ❌      | ❌    | ❌     | ❌      | ❌    | ❌            | ✅       |
| 6   | 人工锚定 AC（awaiting_human）                        | ❌      | ❌    | ✅     | ❌      | ❌    | ❌            | ❌       |
| 7   | 确认转 feature（awaiting_human，产品决定）           | ❌      | ❌    | ✅     | ❌      | ❌    | ❌            | ❌       |
| 8   | 创建 `fix/{issue}` 隔离分支（唯一 branch authority） | ❌      | ❌    | ❌     | ❌      | ❌    | ❌            | ✅       |
| 9   | 继承目标版本已批准基线（source approval，不重新批准）| ❌      | ❌    | ❌     | ❌      | ❌    | ❌            | ✅       |
| 10  | M-DESIGN delta 设计承接锚定 AC 集合                  | ❌      | ❌    | ❌     | ✅      | ❌    | ❌            | ❌       |
| 11  | 复核锚定与 delta 设计（M-DESIGN 评审）              | ❌      | ❌    | ❌     | ❌      | ✅    | ❌            | ❌       |
| 12  | 设计缺口裁定（回 hotfix M-DESIGN，不新增 Human 技术门）| ❌    | ❌    | ❌     | ❌      | ✅    | ❌            | ❌       |
| 13  | 在 `fix/{issue}` 分支实现修复（RGR + review）        | ❌      | ❌    | ❌     | ❌      | ❌    | ✅            | ❌       |
| 14  | commit / push / 推进阶段 / 创建分支                  | ❌      | ❌    | ❌     | ❌      | ❌    | ❌            | ✅       |
| 15  | ac_gap/spec_gap 退出转 backlog/new feature（产品决定）| ❌     | ❌    | ✅     | ❌      | ❌    | ❌            | ✅（执行路由） |

## 功能需求

### FR-0240 `trac hotfix` 入口与 HOTFIX-TRIAGE 锚定旅程

- **来源**：`BS-01` / `§3.1` / flow.md §16.1、§16.4
- **交付入口**：`E-01`（`trac hotfix <issue> --scenario post-release|dev`，新增顶层命令）

操作者在宿主 repo 工作区执行 `trac hotfix <issue> --scenario post-release|dev`（`<issue>` 是宿主 repo 的 GitHub issue 号，作为该 hotfix 的需求追踪身份；`--scenario` 必填，`post-release` = 场景 A 已发布版本，`dev` = 场景 B 开发中版本；缺省时 Runtime 询问 Human，不从 issue 推断）。Runtime 建立 hotfix run 并运行 HOTFIX-TRIAGE 入口子状态机（SM-01；flow.md §16.4，非 canonical 顶层阶段，事件落在 hotfix run 自己的事件流）：

1. **PRECHECK 程序预检**（确定性、无 LLM，SM-01.1-.4）：issue 存在且 type=bug（宿主 bug issue template 提供「版本 / 对应 FR/NFR」字段，均可选--终端用户不可能知道编号，字段仅作辅助、不完全采信）；`--scenario` 合法；`dev` 场景需存在当前活跃 release 分支。预检通过 -> `SAGE_TRIAGE`（SM-01.2）；预检失败（非 bug / issue 不可定位 / scenario 不合法 / dev 无活跃分支）-> `REJECTED`（SM-01.3-.4）：不建 `fix/{issue}` 分支，报告原因与下一步（补全 issue 后重试或改走 `trac start` feature 流程）。

2. **SAGE_TRIAGE 语义锚定**（SM-01.5-.7）：Sage 依据 issue 场景/症状与可选 FR/NFR 字段（辅助），在目标版本及历史版本的 spec.md/acceptance.md 中自行决定对应的既有 AC 集合，输出逐条锚定理由与出处（版本 + 条目），或 NO_ANCHOR 报告（含已检索版本与语料清单）。Runtime 程序校验锚定输出--所引每条 AC 必须真实存在于所指版本的 acceptance.md，引用不实 = validate fail，重派 Sage（≤3，SM-01.6）；NO_ANCHOR 或重派超限 -> `AWAIT_HUMAN`（SM-01.7）。

3. **AWAIT_HUMAN**（SM-01.8-.9）：`trac status` 报告 `awaiting=awaiting_human` 与 origin（hotfix-triage + issue 号）；Human 可人工锚定 AC（`human.anchor(manual)` -> `ANCHORED`，SM-01.8）或确认转 feature（`human.anchor(feature_route)` -> `FEATURE_ROUTE`，SM-01.9）。两种 NO_ANCHOR 成因同路转 feature（spec/acc 存在但 Sage 无法关联；功能早于规范文档存在、历史版本无规范文档可引）--转出是产品决定，需 Human 确认，不自动转。

**用户可观察结果**：操作者从 `trac hotfix` 与 `trac status` 看到 REJECTED 的原因与下一步、AWAIT_HUMAN 的等待状态与 origin、或锚定通过进入 ANCHORED（FR-0241）。事件 `hotfix.requested` / `triage.prechecked` / `anchor.validated` / `human.anchor(manual|feature_route)` 落在 hotfix run 事件流，可经 `trac replay` / `trac report` 审计。

**关键失败/恢复边界**：Sage 锚定无法定位目标版本已批准基线（issue 元数据缺失、目标版本三件套未批准）-> 预检失败 REJECTED，保留"补全后重试"路径。锚定校验重派 ≤3 后仍不产出合法锚定 -> AWAIT_HUMAN，不自动转 feature。REJECTED 与 FEATURE_ROUTE 不创建 `fix/{issue}` 分支（无分支副作用，NFR-0100）。

### FR-0241 ANCHORED：fix/{issue} 隔离分支、基线继承与 M-DESIGN 进入

- **来源**：`BS-02` / `BS-03` / `BS-04` / `BS-05`（FEATURE_ROUTE 部分）/ `§3.1 步骤 3` / `§5 约束` / flow.md §16.1.2、§16.4
- **交付入口**：`E-01`（`trac hotfix` ANCHORED 出口）/ `E-02`（`trac status` / `trac replay` 观察分支与 M-DESIGN 进入）

锚定确认（Sage 锚定通过 + 程序校验，或 Human 人工锚定，SM-01.5/.8）后 Runtime 进入 ANCHORED（SM-01.10）：

1. **按场景创建隔离 `fix/{issue}` 分支**（BS-02/BS-03，Runtime 是唯一 branch/worktree authority）：
   - 场景 A（`--scenario post-release`）：从 `main` checkout `fix/{issue}` 分支。
   - 场景 B（`--scenario dev`）：从当前活跃 release 分支 checkout `fix/{issue}` 分支；无活跃分支时场景 B 不成立（PRECHECK 已拦截）。

2. **继承目标版本已批准基线**（BS-04，source approval，不重新批准）：继承目标版本已批准的 spec/acceptance/接口与设计基线作为需求基线；**不创建** M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL（有意偏离 feature release 从 M-STORY 进入的常规起点，用户明确要求 seed 第 3 点；§5 非常规要求）。

3. **直接 `stage.entered(M-DESIGN)`**：hotfix run 进入 canonical 阶段序列；Runtime 记录 issue->AC 锚定作为 M-DESIGN baseline 输入（FR-0243 承接）。

**FEATURE_ROUTE 退出**（SM-01.9/.11，BS-05）：Human 确认无对应 AC -> 退出 hotfix 转 backlog/new feature（`backlog.recorded`）；不建 `fix/{issue}` 分支，不残留半建 run。两种 NO_ANCHOR 成因同路（spec/acc 存在但无法关联；功能早于规范文档存在）都按 feature 处理以经 feature release 补齐程序行为规范。

**用户可观察结果**：`trac status` / `trac replay` 显示 hotfix run、`fix/{issue}` 分支、`stage.entered(M-DESIGN)` 与锚定的 AC 集合（跨版本引用 `AC-FRXXXX-YY@<version>`）；操作者可继续通过既有 `trac run` 派发（FR-0242 接续）。FEATURE_ROUTE 时显示退出原因与去向（backlog/new feature），不残留半建 run。

### FR-0242 hotfix run 并存与串行接续

- **来源**：`BS-06` / `§3.1 步骤 4` / `§3.2` / flow.md §16.3.7 / `§6 重要推导`
- **交付入口**：`E-02`（`trac run` 续跑当前 active run；`trac status` / `trac replay` 观察挂起 run）

hotfix run 与既有 feature run 的并存按 flow.md §16.3.7 与 Aaron 裁定处理：

1. **并存分支允许**：场景 A 与活跃 run 完全独立（base = main，不被 `CHECK_ACTIVE` 阻塞）；场景 B 与活跃 feature run 共享活跃分支，是单活跃 run 原则的唯一受控例外。

2. **并发开发活动禁止**（Aaron 裁定）：同一时刻只允许一个 trac 开发命令在运行--feature 与 hotfix 的 `trac run` 不得同时执行；第二个 trac 命令被 writer lock 拒绝（报持有者 PID）。两 run 在共享分支上的写必须串行（同一 writer lock，继承 v0.5 单写者纪律）；场景 B 的 merge 仅在活跃 run 无在飞 dispatch 时进行（merge 语义本版不执行，FR-0246）。

3. **单一 active run 接续**（重要推导，§6）：hotfix run 建立后由既有单一 active run 语义接续--`trac run` 继续当前 active run（hotfix 建立时即 active）、开发派发串行。挂起的 feature run 的状态持久化保留，`trac status` / `trac replay` / `trac report` 可观察挂起 run 的当前状态与审计输出。hotfix run 到达 boundary 终态（FR-0246）后，挂起的 feature run 恢复为 active，`trac run` 续跑 feature run。

4. **场景 B baseline stale reconcile**（flow.md §16.3.3）：场景 B 的 fix 分支基线随活跃分支推进会 stale；merge 前须对活跃分支当前 HEAD reconcile，冲突 -> `needs_attention`（merge 语义本版不执行，FR-0246；但 reconcile 与冲突路径必须保留，否则场景 B 不可达）。

**用户可观察结果**：操作者能从 `trac status` 分辨当前 `trac run` 继续的是哪个 run（run_id + stage + branch + scenario）；挂起 run 的门禁可恢复观察；hotfix boundary 后 feature run 恢复 active。第二个并发 trac 命令被拒绝并报持有者 PID。

### FR-0243 hotfix M-DESIGN delta 设计与锚定承接

- **来源**：`§3.2 步骤 1` / `§5 约束` / flow.md §16.2、§16.3.1、§16.4 规则 1
- **交付入口**：`E-02`（`trac run` M-DESIGN 派发 Archer；`trac status` / `trac replay` 观察 delta 设计）

hotfix 一律从 M-DESIGN 进入（v0.6 起废除 quick_rgr 免设计分流：改动再小也要有 delta 设计与独立评审），复用 canonical M-DESIGN 阶段的子状态机、角色、评审协议、重派预算与门禁语义，差异如下：

1. **delta 设计**（flow.md §16.3.1）：Archer 产出相对继承基线的 delta 设计--architecture/interfaces/test-plan 三文档的修订增量（不是全量产品设计），产出物归 hotfix run 自己的项目目录。

2. **锚定承接**（flow.md §16.4 规则 1）：Archer 的 delta 设计必须承接 HOTFIX-TRIAGE 锚定的 AC 集合（逐条引用目标版本 spec/acceptance 既有条目，跨版本引用 `AC-FRXXXX-YY@<version>`）；Prism 在 M-DESIGN 评审时复核锚定。Prism 推翻锚定 -> 回 SAGE_TRIAGE 重新锚定（上游产物缺陷，不消费 M-DESIGN 重派预算，SM-01.6 重派）。

3. **contracts 沿用**：machine contracts 沿用目标版本的 machine contracts，除非修复本身改变合同。

**用户可观察结果**：操作者从 `trac status` / `trac replay` 看到 M-DESIGN delta 设计产出（三文档修订增量归 hotfix run 项目目录）、锚定 AC 集合的承接与 Prism 复核结果。delta 设计与独立评审不可省略（废除 quick_rgr）。

### FR-0244 hotfix M-TEST 回归测试 RED-first、Archer-decided 归属与空 Shield 增量放行

- **来源**：`BS-07` / `§3.2 步骤 2` / `§5 约束` / Human 裁定（2026-08-19，T-003）/ flow.md §16.3.2
- **交付入口**：`E-02`（`trac run` M-TEST 派发 Shield；`trac check trace` trace 闭合；`trac status` / `trac replay`）

hotfix M-TEST 复用 canonical M-TEST 阶段子状态机与角色，差异如下（BS-07，Human 裁定 T-003）：

1. **RED-first 回归纪律**：回归用例必须先复现 RED 再变绿（修复前既有用例必然全绿--形式缺失或未触发 RED 条件）。合法 Red = 回归测试在带缺陷基线上的行为断言失败。

2. **Archer-decided 归属**（delta test-plan）：回归用例归属哪一层由 Archer 在 delta test-plan 决定--integration/e2e 层归 Shield 在 M-TEST 补写、unit 层归 Devon 在 M-IMPL RED 阶段补写；两种归属都保持"先复现（RED）后修复（GREEN）"的 RGR 纪律。

3. **AC trace 绑定既有 AC**：AC trace 绑定目标版本既有 AC（跨版本引用 `AC-FRXXXX-YY@<version>`）；hotfix **不产生新 AC**（没有自己的 M-ACC）。回归测试挂对应既有 AC 之下--bug 即"有 AC 而此前没有检验"；找不到对应 AC 即说明是新 feature（退出 hotfix，FR-0248）。

4. **空 Shield 增量放行**：若 Archer 将回归用例全部划归 unit 层，M-TEST 的 Shield 增量为空，Runtime 凭"空 Shield 增量声明 + `trac check trace` 由 unit 层回归用例闭合"放行 M-TEST。

**用户可观察结果**：操作者从 `trac status` / `trac replay` / `trac check trace` 看到回归测试资产、RED-first 证据、Archer-decided 归属、空 Shield 增量声明时的 unit 层 trace 闭合证据。M-TEST 的 Shield 增量可空、由 unit 层回归用例跨层 trace 闭合，是主路径 M-TEST 步骤的非常规分支（产品不变量），不应仅靠 flow.md §16.3.2 引用兜底。

### FR-0245 hotfix M-IMPL 隔离分支实现与场景 B stale reconcile

- **来源**：`BS-08` / `§3.2 步骤 3` / `§5 约束` / flow.md §16.2、§16.3.3
- **交付入口**：`E-02`（`trac run` M-IMPL 派发 Devon；`trac status` / `trac replay`）

hotfix M-IMPL 复用 canonical M-IMPL 阶段子状态机（v0.5 SM-01）与角色，差异如下：

1. **隔离分支实现**（BS-08）：实现只发生在隔离 `fix/{issue}` 分支；Runtime 保持唯一 branch/worktree authority。task graph 按影响切片（通常远小于 feature release），scope 白名单沿用目标版本 layout。Devon 在 `fix/{issue}` 分支上完成 RGR 与 review，修复提交落在该分支。

2. **场景 B baseline stale reconcile**（flow.md §16.3.3）：场景 B 的基线随活跃分支推进会 stale；merge 前须对活跃分支当前 HEAD reconcile，冲突 -> `needs_attention`（merge 语义本版不执行，FR-0246；但 reconcile 与冲突路径必须保留）。

**用户可观察结果**：操作者从 `trac status` / `trac replay` 看到 `fix/{issue}` 分支上的 RGR 旅程、修复提交、场景 B 的 stale reconcile 与 `needs_attention`（冲突时）。修复实现不发生在活跃分支或 feature run 工作区。

### FR-0246 M-IMPL 边界退出与发布语义声明

- **来源**：`BS-09` / `§3.2 步骤 4` / `§5 非常规要求` / Maestro 裁定 / flow.md §16.3.4、§16.3.5
- **交付入口**：`E-02`（`trac run` boundary；`trac status` / `trac replay` / `trac report`）

hotfix 到达 M-IMPL 边界后以 boundary 终止（BS-09，Maestro 裁定 A）：

1. **boundary 终止**：`run.completed(terminal_state="boundary")`（沿用 v0.5 FR-0160 边界退出语义）；`fix/{issue}` 分支保留修复结果，全程可审计。

2. **发布语义本版不执行**：场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号、场景 B 的 merge 回活跃分支作为发布语义载于 flow.md §16.3.5，本版不执行，随 M-VERIFY/M-RELEASE 阶段注册后生效。操作者在本版看到的是"修复完成、待发布"的边界状态，不伪称发布已执行。

**用户可观察结果**：`trac status` 报告 `terminal=boundary`、`fix/{issue}` 分支、scenario 标识；`trac replay` / `trac report` 显示完整可审计旅程（`hotfix.requested` -> triage -> M-DESIGN delta -> M-TEST RED-first -> M-IMPL isolated -> boundary）。场景 A 的"为全体用户提供 hotfix"在本版以"修复完成、待发布"边界状态呈现（Maestro 裁定）。

### FR-0247 设计缺口回 hotfix M-DESIGN

- **来源**：`BS-10` / `§3.3 步骤 3` / flow.md §16.3.6
- **交付入口**：`E-02`（`trac run` return upstream；`trac status` / `trac replay`）

hotfix 旅程中发现 Archer 负责的设计缺口（delta 设计、测试计划或接口缺口）-> 回 hotfix 自己的 M-DESIGN，由 Archer + Prism 裁定，**不新增 Human 技术门**（BS-10，flow.md §16.3.6）。回到 M-DESIGN 的缺口在 hotfix run 内部闭环后重新进入 §3.2 的旅程（FR-0243 重新承接）。

**用户可观察结果**：操作者从 `trac status` / `trac replay` 看到退出原因为设计缺口、去向为 hotfix 自己的 M-DESIGN；闭环后重新进入 M-DESIGN->M-IMPL 旅程。设计缺口在 hotfix run 内部闭环，不需 Human 技术审批。

### FR-0248 非实现偏差中段退出（ac_gap/spec_gap -> backlog/new feature）

- **来源**：`BS-05`（中段部分）/ `§3.3 步骤 2` / flow.md §16.3.6
- **交付入口**：`E-02`（`trac run` 退出路由；`trac status` / `trac replay`）

hotfix 旅程中发现需要新行为/新验收（`ac_gap` / `spec_gap`）-> 说明该 issue 不是既有 approved 基线的实现偏差 -> 退出 hotfix 转 backlog/new feature（产品决定，需 Human，BS-05）。被判定为新行为的 issue 不再以 hotfix 身份继续，避免把新功能伪装成缺陷修复。

**用户可观察结果**：操作者从 `trac status` / `trac replay` 看到退出原因为 `ac_gap` / `spec_gap`、去向为 backlog/new feature；该 issue 后续走 feature release 补齐程序行为规范（具体 feature 流程不在本 spec）。

### FR-0250 测试执行选择语义：全量 collect 与 R2/T-DELTA 差分执行

- **来源**：`D-41` / flow.md §9.0、§9.1、§9.3 规则 6
- **交付入口**：`E-03`（`trac run` M-TEST COLLECT/RED_CHECK；`trac status` / `trac replay` / `trac report`）

M-TEST 阶段对测试节点的分类与执行选择语义（D-41，canonical 见 flow.md §9.0）：

1. **节点分类程序化（逐节点 digest，非整文件；v5 增补 baseline 来源）**：分类由 Runtime 依据 **R1 快照**——本 run 进入 M-TEST 时、首个 Shield WRITE 派发之前对继承测试树（prior-to-Shield 当前工作树）做 unit+integration+e2e 全量 collect 并持久化的 `test.baseline_captured` stamped 快照（baseline/tree identity + 逐节点源/体 digest blob）——与 WRITE 后 COLLECT 捕获的当前三层 inventory 程序判定并落事件，可审计。分类 baseline 的来源是该 prior-to-Shield stamped capture：既不是既往 run 的事件猜测，也不是 WRITE 后可变树的事后重算（二者均为缺陷行为）；COLLECT 分类时刻无 passed 快照 = fail-closed。首次项目可合法捕获空集（`empty_baseline=true` 仅由成功 capture 产生——缺失 capture ≠ 空 baseline）。capture 中任一层 collect/import 失败 = 继承树在 Shield 写入前即不可 import，属上游/设计/合同缺陷，fail-closed 路由上游（不静默空置 R1、不重派 Shield）。中断/重启/replay 复用同一 stamped capture（`test.selected.baseline == test.baseline_captured.baseline_id` 可复核）。R2/T-DELTA 恰好且仅为两类：**新增节点**、或**同一 nodeid 节点源/体 digest 变化**；R1/T-HIST = nodeid 在 baseline 且节点 digest 未变化——同一文件内未变化的兄弟节点保持 R1/T-HIST（禁止整文件 digest 分类：历史文件追加一个新测试不得把未变兄弟节点翻成 R2 再以"意外通过"失败它们）。变更的 support/fixture 不参与分类，但**仍参与全量 collect/import**（保证可 import），当前 R2 测试在其上执行；未变历史行为不被本地重验——其回归等待 M-IMPL FULL 链与 nightly CI。baseline 历史节点缺失于本次全量 collect = fail-closed 测试资产删除（REMOVED）：不静默注销，路由为 test contract defect（DIAGNOSE → Shield）并阻断 M-TEST 退出；本版无任何授权删除路径（未来如引入，须经显式上游 contract 授权——当前不存在）。

2. **collect 全量（v5 明确三层）**：COLLECT 对 `[unit]` + integration + e2e 三层的继承 R1/T-HIST 与当前 R2/T-DELTA 全部节点执行 collection/import，全部节点必须可 collect（桩保证可 import；变更 support/fixture 同样参与）；unit 层节点同样进入 collect 分类计数（历史 unit = R1/T-HIST 计入 inherited_r1）；collect 失败回 Shield（既有 M-TEST 子状态机语义不变）。

3. **执行差分与时序（v3；v6 结果输入修订）**：M-TEST 子状态时序为 `入口 baseline capture(pre-WRITE) → WRITE → 全量 COLLECT → Runtime RED_CHECK(SELECT_R2) → PRISM_REVIEW → EXIT`。RED_CHECK 本地执行只限实际 R2/T-DELTA 节点并逐节点判定合法 Red（合法 Red 判据不变：行为断言失败/桩合同 token 失败/symbol 缺失；collection/语法/fixture/import 错误非法）——逐节点分类的**输入**是 `{result}` JUnit XML 的逐 testcase 记录（FR-0255-1a：覆盖不精确/缺失/畸形 = contract_error fail-closed；stdout/stderr 仅为日志）——通常即 Shield 的 int/e2e 增量，历史 unit 节点永不进入 M-TEST 执行记录；非法 RED 在 Prism 之前由 Runtime 路 DIAGNOSE/WRITE 处理完毕，因此 PRISM_REVIEW 消费的是**当前树**的 `red.validated` 身份证据。**feature M-TEST 的 R2 选择集为空 = fail-closed**（不得以空选择 vacuous 通过；hotfix unit-only 显式增量声明的放行旁路保留，FR-0244）。**M-TEST 永不本地执行 R1/T-HIST 全量套件**（该限定仅约束 M-TEST；M-IMPL 的 SELECT_TASK 与 FULL 链合法执行 R1/T-HIST 节点）。nightly CI 运行**当前完整 FULL 套件**（R1+R2 全部节点，unit+integration+e2e——历史回归责任强调 R1/T-HIST，但 nightly 套件不是仅 R1 子集；contract 见 FR-0255），结果在未来被取回（result fetch future，本版不实现取回通道，见范围排除），不构成当前本地门禁。全量的唯一本地执行例外是 M-IMPL 出口门禁的 FULL 链（FR-0253）。

4. **Prism 消费证据**：PRISM_REVIEW 在 RED_CHECK 之后进行，消费当前树的 `red.validated` 执行证据（selection binding + 逐节点合法 Red 分类）并独立运行 counterexample kill（对合法 Red 节点构造反例验证测试有效性），不以重跑普通套件作为评审手段。

**用户可观察结果**：操作者从 `trac run` / `trac status` 看到 `test.baseline_captured`（入口 pre-WRITE R1 快照，含 baseline/tree identity 与逐节点 digest blob）、`test.collected`（全量 collect，payload 含继承与增量两类节点计数）、`test.selected`（scope=r2_delta，含 selection identity 且 `baseline == baseline_captured.baseline_id`）、`red.validated`（仅覆盖被选 R2/T-DELTA 节点）与 `prism.verdict`——事件顺序 `test.baseline_captured` 先于本 run 首个 Shield WRITE 派发、`red.validated` 先于 `prism.verdict`；M-TEST 事件流无任何 R1/T-HIST 节点的本地执行记录；`trac replay` / `trac report` 可审计选择依据与被选集合。

### FR-0251 task 级 GREEN 选择（per-task SELECT_TASK）

- **来源**：`D-41` / flow.md §10.1 GREEN_GATE、§9.0 selection identity
- **交付入口**：`E-03`

M-IMPL 每个 task 的 GREEN_GATE 做 per-task 选择（SELECT_TASK）：targeted unit 节点来自该 task 不可变 R commit 的 Runtime 捕获 RED artifact manifest 与本 task GREEN-touched unit 文件（test-plan §8 IF 行只含 integration/e2e，不承载 unit 归属）；integration 节点 = 变绿条件命中本 task IF 集合的节点（§8 IF 行归属）；不含 e2e 节点与 R1/T-HIST 全量（e2e 只在 FULL 链执行，FR-0253）。每次 SELECT_TASK 以 `test.selected`（scope=task_if）落事件，携带 selection identity（task IF 集合 + 被选节点集合 + baseline/commit，NFR-0130）；上游变化使该选择与其证据 stale，stale 证据不复用为通过依据。

**用户可观察结果**：操作者从 `trac replay` / `trac report` 看到每 task 的 SELECT_TASK 选择身份、对应 GREEN_GATE 执行证据与绑定关系；GREEN_GATE 不出现 e2e 或历史全量执行记录。

### FR-0252 REFACTOR 阶段的 identity 绑定证据复用

- **来源**：`D-41` / flow.md §10.1 REFACTOR_GATE
- **交付入口**：`E-03`

REFACTOR outcome 后 REFACTOR_GATE 按变更 identity 判定：

1. **identity 未变**（no-change outcome，或工作区内容 digest 与 Green commit 一致）→ 复用 Green 证据：本 task 的 targeted unit + integration 结果按 evidence identity 直接复用（`evidence.reused` kind=green 落事件，引用被复用证据的身份），不重跑。

2. **identity 有变化** → 按同一 selection scope 重跑（同 SELECT_TASK 的 targeted unit + integration），新证据绑定新 attempt/actor；不出现跨 scope 复用旧证据的放行。

3. 动 public interface → upstream 路由不变（既有子状态机语义）。

**用户可观察结果**：操作者从 `trac replay` / `trac report` 看到 `refactor.committed|no_change` 之后是 `evidence.reused`（identity 未变）或新的选择与执行事件（identity 有变化）；复用与重跑的判定依据是程序化的内容 identity 比对（非 Agent 自述）。

### FR-0253 FULL 链收敛与失败台账（FULL_1 → 台账 → SELECT_DIFF → FULL_F）

- **来源**：`D-41` / flow.md §10.5、§9.0
- **交付入口**：`E-03`（`trac run` ISLAND_GATE_2；`trac status` / `trac replay` / `trac report`）

全部 task 完成后 ISLAND_GATE_2 执行 FULL（unit + integration + e2e）出口门禁：

1. **FULL_1**：首轮全量（`full.executed` round=FULL_1），结果逐节点写入失败台账。FULL_1 干净且台账为空（零失败条目）时，同一 stamp 的执行事件直接标注 `serves_as_full_f=true` 充当 FULL_F，M-IMPL 照常出口（`stage.exited(M-IMPL)`）——**不紧邻重复执行一次等价的 FULL_F**（干净首轮充当，与第 4 步 fallback 充当同一充当语义的退化情形）。

2. **失败台账**：每个失败节点一条 append-only 台账事件（`ledger.opened` state=OPEN），台账身份 = `(node, failure_signature)`——同一节点的新 failure signature 是新台账身份，同一 signature 重启先前身份；状态机 `OPEN → CLASSIFIED → FIXED → PROVEN`（逐转移落 `ledger.transitioned`），上游变化置 `STALE`；闭合转移集另含 `FIXED → OPEN`（证明失败，见第 3 步）与 `PROVEN → OPEN`（reopen：FULL_F 重现已 `PROVEN` 的同一 `(node, failure_signature)` 时确定性地重启先前身份并重证）；分类/修复走既有 DIAGNOSE/重派路径（测试缺陷→Shield、实现缺陷→Devon、上游缺口→对应阶段），per-agent attempt 预算照旧消费。

3. **SELECT_DIFF 逐条目立即证明（v3）**：修复循环是 per bug/fix 的——每条目到达 `FIXED` 后**立即**按 selection identity 对其运行确定性差分选择（`test.selected` scope=select_diff，携带该条目身份）重跑受影响节点：通过 → 该条目转移 `FIXED → PROVEN`；同签名失败 → 该条目转移 `FIXED → OPEN` 并重新分类/修复。某条目推导不出可靠选择集时回退 FULL（round=fallback_full）：该次 fallback 的结果同样逐条目落转移——通过条目 `FIXED → PROVEN`、同签名失败条目 `FIXED → OPEN`。多条目 union/batching 仅在每条目的选择与证据仍可单独归因时作为可选优化，不构成规范语义。

4. **差分不可证明回退 FULL 的充当关系**：fallback FULL 若干净可直接充当 FULL_F（事件标注 fallback 充当关系），不再另行执行 FULL_F。

5. **FULL_F 收敛**：全部条目 `PROVEN` 且无 `STALE` 后执行 FULL_F；出现新失败则追加台账回到第 2 步——重现已 `PROVEN` 的同一 `(node, failure_signature)` 时按 reopen 转移重启该条目（不新建身份），全新 signature 才是新增条目；循环直至干净——**全链循环不设预算**（无循环次数上限、无"超限放弃"路径），仅 per-agent 派发 attempt 预算（≤3/escalation）照旧。FULL_F 干净 = M-IMPL 出口证据（`stage.exited(M-IMPL)`）；充当情形（干净首轮 FULL_1 或 fallback FULL 已标注 `serves_as_full_f=true`）下不再另行执行 FULL_F，出口证据即该标注充当关系的同一执行事件。

**关键失败/恢复边界**：台账 fail-closed 语义见 NFR-0120——脏台账不视为干净、不得跳过 FULL_F；Runtime 中断或重启后台账由事件回放重建（NFR-0120），FULL 链从重建状态精确续跑。

### FR-0254 M-VERIFY 对干净 FULL_F 的复用

- **来源**：`D-41` / flow.md §11、§11.3 规则 3
- **交付入口**：`E-03`（`trac status` LOCAL_GATES 观察；`trac replay` / `trac report`）

1. **未漂移复用**：candidate 相对 M-IMPL 干净 FULL_F 未漂移（identity 一致：candidate commit 与 FULL_F 执行时的 baseline/commit 一致且相关条目无 STALE）→ 复用 FULL_F 证据（`evidence.reused` kind=full_f 落事件），**不重复本地全量**；随后进入既有 CI 门禁等待 `ci.run_observed(passed)` API 回读（ordinary candidate CI readback），回读通过才退出。该候选 CI 回读与推迟实现的 nightly 结果取回（result fetch future）是两回事，不得混同。

2. **漂移/stale 重跑**：漂移或证据 stale 时按 LOCAL_GATES 重跑 FULL（既有 M-VERIFY 语义不变）。

3. **注册边界**：本版不注册 M-VERIFY（沿用既有 release 节奏）；本条固化 D-41 的 M-VERIFY 复用语义为 canonical 合同，随 M-VERIFY 阶段注册后生效（同 FR-0246 发布语义的处理方式）。

**用户可观察结果**：`trac status` 显示 full_reuse（复用）或 rerun（重跑）及判定依据；`trac replay` / `trac report` 可审计复用所引用的 FULL_F evidence identity。

### FR-0255 测试命令与并发的 Archer 独家所有权及 nightly CI contract

- **来源**：`D-41` / flow.md §8.3 规则 4、§9.0、§11.3 规则 3
- **交付入口**：`E-03`（执行审计事件中的命令回显）

1. **命令独家所有权**：测试命令由 Archer 的 machine contract 独家定义——contract 以与现行 project.toml 一致的扁平 `[unit]`/`[integration]`/`[e2e]` 段逐层声明 `run`（全量）与 `run_selected`（含 `{nodes}` 占位符）命令字符串；worker/dist 并发 flag 作为 Archer 选定的 argv **内嵌于命令字符串**（不设独立 `workers`/`dist` 键）。`run_selected` 缺失 = contract_error fail-closed：Runtime 拒绝执行选择，**永不向 `run` 追加 nodeid 或合成任何调用**。Runtime 命令构造与审计只经三个可执行纯函数（v5；v6 修订签名）：`resolve_selected_command(template, nodes, result_path, cwd) -> argv`（显式接收被选节点与结果路径，{nodes}/{result} 替换 + argv0 相对 cwd 解析）、`audit_no_concurrency_injection(expected_argv, actual_argv, cwd) -> bool`（期望/实际两侧应用同一 argv0 解析规则后逐字比对，不等 fail-closed）与组合形态 `audit(template, nodes, result_path, actual_argv, cwd)`；**永不注入并发**——执行审计记录实际命令行，与 contract 定义逐字一致，无 contract 之外的 `-n`/`--dist`/worker 覆盖参数。

1a. **逐节点结果机器可读通道（v6）**：每条 `run` 与 `run_selected` 模板各含 `{result}` 占位符**恰好一次**（`run_selected` 另含 `{nodes}` 恰好一次），指 Runtime 提供的唯一可写 JUnit XML 路径；Archer 把结果写入 flag（pytest 即 `--junitxml={result}`）与并发 flag 同样内嵌于命令字符串——Runtime 替换封闭集 = 声明的 `{nodes}`/`{result}` 加 cwd/argv0 解析，**永不注入 `--junitxml` 等 junit flag**；占位符缺失或出现多次 = contract_error fail-closed（loader/validate 强制）。执行结果的权威是 JUnit XML：testcase 节点身份必须恰好覆盖本次被选集合（全量 run 时 = 本次 collect 的 FULL 全集）；文件缺失/畸形、身份重复、被选节点缺席、多余节点一律 contract_error fail-closed；stdout/stderr 仅为日志，永不作为逐节点分类权威。逐节点合法 Red 从 testcase 的 failure/error 详情推导（FR-0250 判据不变；pass/skipped/xfailed 遵循既有合法 Red 规则——被选 R2 节点 pass/skip 即非法意外通过）；FULL 链失败台账消费同一份归一化逐节点记录。结果路径位于 Runtime temp/blob staging、按 run/command 唯一、不入树身份；temp 清理前归一化持久化为 outcomes blob（事件携 `outcomes_ref` 引用）；WAL replay 发现无已持久化结果时重跑该命令，绝不把缺失结果当作通过。

2. **原子实现切片（v3）**：validator schema 激活、project.toml 合同扩展（`[unit]` 段、全部三层 `run_selected`、`[nightly]` 段）与 prompt/runtime 变更是**一个原子实现切片**（经正常 Tracks pipeline 交付）。本 spec 轮不声称 project.toml 内容不变——该文件的工作树编辑由另一授权 agent 并发持有，本文档轮不拥有它；缺键 fail-closed 自 schema 切片激活起生效（激活前 Runtime 行为不变），激活后任一层缺 `run_selected` 或 `[nightly]` 缺必填键即 contract_error fail-closed。

3. **nightly CI contract**：machine contract 声明**当前完整 FULL 套件**（R1+R2 全部节点，unit+integration+e2e）的 nightly CI job（D-18 三层机制的周期回归层；历史回归责任强调 R1/T-HIST，但 nightly 运行的是当前 FULL 而非仅 R1 子集）。本版 nightly 的可审计面 = **contract 文件 + `trac validate` 校验**；nightly 派发/回读事件不在本版承诺范围（随 result fetch future 一并考虑）。nightly 结果取回通道本版不实现（见范围排除），不影响本地门禁语义（FR-0250）。

**用户可观察结果**：操作者从执行审计/`trac report` 核对每次测试执行的命令行与 contract 一致、Runtime 未追加并发参数与 junit flag；machine contract 含 nightly CI job 条目；每条 run/run_selected 模板含 `{result}`（run_selected 另含 `{nodes}`）且各恰好一次；每次门禁执行的归一化逐节点结果以 `outcomes_ref` blob 可审计（v6）。

## 非功能需求

### NFR-0100 HOTFIX-TRIAGE 确定性与 fail-closed 可审计性

- **来源**：`BS-01` / `§5 约束` / flow.md §16.4

HOTFIX-TRIAGE 的 PRECHECK 程序预检必须确定性（无 LLM）：issue type/scenario/活跃分支校验是纯程序判断。SAGE_TRIAGE 的锚定输出经程序校验（所引每条 AC 必须真实存在于所指版本 acceptance.md，引用不实 = validate fail），校验结果可由事件/审计记录回溯（沿用 v0.5 NFR-0020 append-only 事件溯源）。REJECTED 与 FEATURE_ROUTE 不创建 `fix/{issue}` 分支（无分支副作用，fail-closed）：锚定不成立时不擅自判为新行为，交 Human 决定（FR-0240 AWAIT_HUMAN）。事件 `hotfix.requested` / `triage.prechecked` / `anchor.validated` / `human.anchor(manual|feature_route)` / `stage.entered(M-DESIGN)` / `backlog.recorded` 均 append-only 写入事件流，不改写既有行。

### NFR-0110 hotfix run 可恢复性与可观察性

- **来源**：`§7 重要风险` / `§6 重要推导`

hotfix run 与既有 feature run 并存时，run 选择/续跑语义必须让操作者能从 `trac status` 分辨当前 `trac run` 继续的是哪个 run（run_id + stage + branch + scenario）；挂起的 run 的状态持久化保留，`trac status` / `trac replay` / `trac report` 可观察挂起 run 的当前状态与审计输出，不要求操作者手工重建。hotfix run 到达 boundary 终态后挂起的 feature run 恢复 active，其门禁可恢复观察（不因挂起而不可恢复）。Runtime 中断或重启后必须能从 append-only 事件流恢复 hotfix run 与挂起 run 的精确状态（沿用 v0.5 FR-0200 可休眠与崩溃恢复）。

### NFR-0120 失败台账持久性、可重建性与 fail-closed

- **来源**：`D-41` / flow.md §10.5、§9.0

失败台账条目以 append-only 事件写入（write-ahead：先落事件再据以判定/推进），不改写既有行；台账身份 = `(node, failure_signature)`（同节点新签名新建身份、同签名 reopen 先前身份）。闭合转移集：`OPEN → CLASSIFIED → FIXED → PROVEN`（正向收敛）、`FIXED → OPEN`（证明失败：SELECT_DIFF/fallback 重跑同签名失败，条目重开重分类/重修）、`PROVEN → OPEN`（reopen：后续全量复现已 PROVEN 同一签名）、`OPEN|CLASSIFIED|FIXED → STALE` 与 `STALE → OPEN`。Runtime 中断或重启后由事件回放重建台账状态，重建结果（含全部闭合转移）与中断前一致（沿用 v0.5 NFR-0020 append-only 事件溯源与 FR-0200 崩溃恢复语义），FULL 链从重建状态精确续跑。未知状态、缺失条目、非法转移一律 fail-closed——不得视为干净、不得跳过 FULL_F：脏台账上 M-IMPL 不产出 `stage.exited(M-IMPL)`，M-VERIFY 复用（FR-0254）以干净 FULL_F 为前提。selection identity 与 evidence identity 绑定每条台账记录；上游变化使相关 selection/evidence 置 `STALE`。

### NFR-0130 selection/evidence identity 可审计性与复用判定一致性

- **来源**：`D-41` / flow.md §9.0、§10.5、§11

一次选择的身份 = 选择依据（task IF 集合/变更影响集/delta 声明）+ 被选节点集合 + baseline/commit + tree_stamp（确定性 dirty-aware 工作树内容 stamp：干净相关树取 HEAD 身份，脏则对相关变更路径内容做确定性 sha256；Runtime 状态不入 stamp；与 commit 分离——同节点集合同 HEAD 不同未提交内容不共享选择身份，堵证据复用洞）；每条执行证据绑定节点身份 + selection identity + baseline/commit + attempt + actor。全部选择与证据身份 append-only 落事件，经 `trac replay` / `trac report` 可审计。复用判定只认 identity 一致的证据：SELECT_TASK 的 stale 判定（FR-0251）、REFACTOR 复用 Green（FR-0252）、SELECT_DIFF 差分选择（FR-0253）与 M-VERIFY 复用 FULL_F（FR-0254）共用同一 identity 判据，identity 不一致（stale）的证据一律不得作为通过依据；上游变化置 `STALE` 的传播在各处一致，不由各阶段自定变体。

## 范围排除

- 不实现 M-VERIFY/M-RELEASE/M-PUBLISH 的注册与执行，包括场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号/tag/artifact 发布，以及场景 B 的 merge 回活跃分支与 pre-release/开发渠道发布（FR-0246，BS-09，Maestro 裁定 A）；发布语义作为流程设计载于 flow.md §16.3，随 M-VERIFY/M-RELEASE 注册后生效。Human release gate 同步推迟。
- 不为 hotfix 创建新的 M-SPEC/M-ACC（FR-0241 source approval，BS-04）或新增非既有 trac CLI/CI 交付面（沿用 `trac run` / `trac status` / `trac replay` / `trac report` / `trac check` 观察与继续；新增顶层命令仅 `trac hotfix`，FR-0240）。
- 不实现 quick_rgr 免设计分流（v0.6 起废除；改动再小也要有 delta 设计与独立评审，FR-0243）。
- 不创建或维护宿主 repo 的 GitHub bug issue template（宿主 repo 责任；FR-0240 PRECHECK 读取其提供的可选「版本 / 对应 FR/NFR」字段，字段缺失时 PRECHECK 不因此 fail--只降级为无辅助）。
- 不实现 nightly 回归结果的取回机制（result fetch future）：nightly CI 运行当前完整 FULL 套件（R1+R2，unit+integration+e2e；历史回归责任强调 R1/T-HIST）的 contract 与「结果未来取回」语义载于 flow.md §9.0（FR-0250/FR-0255），本版不实现把 nightly 结果取回为本地门禁输入的通道，也不承诺 nightly 派发/回读事件（本版 nightly 可审计面 = contract 文件 + validate 校验）；M-VERIFY 的 CI 证据回读沿用既有 `ci.run_observed` API 语义（flow.md §11）。
- 新行为/新功能需求的完整开发流程不在本 spec（hotfix 只负责识别并转 backlog/new feature，FR-0248；该需求如何走 feature release 不在此）。
