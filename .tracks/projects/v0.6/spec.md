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

## 非功能需求

### NFR-0100 HOTFIX-TRIAGE 确定性与 fail-closed 可审计性

- **来源**：`BS-01` / `§5 约束` / flow.md §16.4

HOTFIX-TRIAGE 的 PRECHECK 程序预检必须确定性（无 LLM）：issue type/scenario/活跃分支校验是纯程序判断。SAGE_TRIAGE 的锚定输出经程序校验（所引每条 AC 必须真实存在于所指版本 acceptance.md，引用不实 = validate fail），校验结果可由事件/审计记录回溯（沿用 v0.5 NFR-0020 append-only 事件溯源）。REJECTED 与 FEATURE_ROUTE 不创建 `fix/{issue}` 分支（无分支副作用，fail-closed）：锚定不成立时不擅自判为新行为，交 Human 决定（FR-0240 AWAIT_HUMAN）。事件 `hotfix.requested` / `triage.prechecked` / `anchor.validated` / `human.anchor(manual|feature_route)` / `stage.entered(M-DESIGN)` / `backlog.recorded` 均 append-only 写入事件流，不改写既有行。

### NFR-0110 hotfix run 可恢复性与可观察性

- **来源**：`§7 重要风险` / `§6 重要推导`

hotfix run 与既有 feature run 并存时，run 选择/续跑语义必须让操作者能从 `trac status` 分辨当前 `trac run` 继续的是哪个 run（run_id + stage + branch + scenario）；挂起的 run 的状态持久化保留，`trac status` / `trac replay` / `trac report` 可观察挂起 run 的当前状态与审计输出，不要求操作者手工重建。hotfix run 到达 boundary 终态后挂起的 feature run 恢复 active，其门禁可恢复观察（不因挂起而不可恢复）。Runtime 中断或重启后必须能从 append-only 事件流恢复 hotfix run 与挂起 run 的精确状态（沿用 v0.5 FR-0200 可休眠与崩溃恢复）。

## 范围排除

- 不实现 M-VERIFY/M-RELEASE/M-PUBLISH 的注册与执行，包括场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号/tag/artifact 发布，以及场景 B 的 merge 回活跃分支与 pre-release/开发渠道发布（FR-0246，BS-09，Maestro 裁定 A）；发布语义作为流程设计载于 flow.md §16.3，随 M-VERIFY/M-RELEASE 注册后生效。Human release gate 同步推迟。
- 不为 hotfix 创建新的 M-SPEC/M-ACC（FR-0241 source approval，BS-04）或新增非既有 trac CLI/CI 交付面（沿用 `trac run` / `trac status` / `trac replay` / `trac report` / `trac check` 观察与继续；新增顶层命令仅 `trac hotfix`，FR-0240）。
- 不实现 quick_rgr 免设计分流（v0.6 起废除；改动再小也要有 delta 设计与独立评审，FR-0243）。
- 不创建或维护宿主 repo 的 GitHub bug issue template（宿主 repo 责任；FR-0240 PRECHECK 读取其提供的可选「版本 / 对应 FR/NFR」字段，字段缺失时 PRECHECK 不因此 fail--只降级为无辅助）。
- 新行为/新功能需求的完整开发流程不在本 spec（hotfix 只负责识别并转 backlog/new feature，FR-0248；该需求如何走 feature release 不在此）。
