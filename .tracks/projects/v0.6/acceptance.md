---
acc_id: ACC-006
created: 2026-08-19
status: draft
sha: c8ab04cc07dfe4b485f119e1027ef3abb169d5bef5f46848379e23b45ef6e258
---

# hotfix 工作流：trac hotfix 入口与继承基线的 M-DESIGN→M-IMPL 旅程 - 验收标准

## FR-0240 `trac hotfix` 入口与 HOTFIX-TRIAGE 锚定旅程

### AC-FR0240-01

  - 操作者在宿主 repo 工作区执行 `trac hotfix <issue> --scenario post-release|dev` 后，hotfix run 的事件流出现 `hotfix.requested` 事件且 payload 含 `issue=<issue>` 与 `scenario=<post-release|dev>`
  - `--scenario` 必填：缺省时 Runtime 不推断、不建 run，转而询问 Human（非阻塞提示）；issue 号作为该 hotfix 的需求追踪身份写入事件
  - `trac status` 在入口子状态机活跃期间报告当前 HOTFIX-TRIAGE 子状态（PRECHECK/SAGE_TRIAGE/AWAIT_HUMAN 之一）与 issue 号

### AC-FR0240-02

  - PRECHECK 程序预检为确定性纯程序判断（无 LLM 调用痕迹：事件流与 dispatch 审计无 Sage/Archer agent 派发对应此步）：校验 issue 属于宿主 repo、type=bug、`--scenario` 合法、`dev` 场景存在当前活跃 release 分支
  - 预检通过 -> 事件流出现 `triage.prechecked`（含 issue、type=bug、scenario）；HOTFIX-TRIAGE 子状态进入 `SAGE_TRIAGE`（SM-01.2），`trac status` 报告对应子状态

### AC-FR0240-03

  - 预检失败（issue 非 bug / issue 不可定位 / scenario 不合法 / `dev` 无活跃 release 分支）-> 事件流出现 `triage.prechecked REJECTED` 且 payload 含失败原因与下一步（补全 issue 后重试或改走 `trac start`）
  - REJECTED 不建立 hotfix run、不创建 `fix/{issue}` 分支（`git branch --list` 不出现该分支）；CLI 退出码非零（`$?` ≠ 0）
  - 目标版本三件套未批准或 issue 元数据缺失使基线不可定位时同样落入 REJECTED，事件/payload 标注原因与"补全后重试"路径，允许同 issue 后续重试

### AC-FR0240-04

  - SAGE_TRIAGE：Sage 依据 issue 场景/症状与可选 FR/NFR 字段在目标版本及历史版本 spec.md/acceptance.md 中锚定既有 AC 集合，输出逐条锚定理由与出处（版本 + 条目编号，跨版本引用 `AC-FRXXXX-YY@<version>`），或 NO_ANCHOR 报告（含已检索版本与语料清单）
  - Runtime 程序校验锚定输出——所引每条 AC 必须真实存在于所指版本 acceptance.md；校验通过 -> 事件流出现 `anchor.validated`（含锚定的 AC 集合），HOTFIX-TRIAGE 子状态进入 ANCHORED（SM-01.5），`trac status` 报告 ANCHORED 与锚定的 AC 集合

### AC-FR0240-05

  - 锚定校验失败（所引 AC 不真实存在于所指版本 acceptance.md）-> Runtime 重派 Sage，重派计数累加（≤3，SM-01.6）；每次重派可在事件/审计中追溯触发原因
  - NO_ANCHOR 或重派 3 次仍不产出合法锚定 -> HOTFIX-TRIAGE 子状态进入 `AWAIT_HUMAN`（SM-01.7）；系统**不**在 NO_ANCHOR / 重派超限时自动转 feature 路由（不擅自判为新行为）

### AC-FR0240-06

  - AWAIT_HUMAN：`trac status` 报告 `awaiting=awaiting_human origin=hotfix-triage issue=<N>`，等待 Human 决定
  - Human 人工锚定 AC（`human.anchor(manual)`）-> 事件流出现该事件且经程序校验通过后进入 ANCHORED（SM-01.8）；Human 确认转 feature（`human.anchor(feature_route)`）-> 进入 FEATURE_ROUTE（SM-01.9）
  - 两类 NO_ANCHOR 成因（spec/acc 存在但 Sage 无法关联；功能早于规范文档存在、历史版本无规范文档可引）均经 AWAIT_HUMAN 同路转 feature，转出由 Human 确认、不自动

### AC-FR0240-07

  - 入口子状态机事件 `hotfix.requested` / `triage.prechecked` / `anchor.validated` / `human.anchor(manual|feature_route)` 均 append-only 写入 hotfix run 自己的事件流（事件表无既有行改写），可经 `trac replay <run_id>` 与 `trac report --run-id <run_id> --output .tracks/runtime/report --format md` 审计
  - 事件 payload 含 issue 号、scenario、锚定的 AC 集合（或 NO_ANCHOR + 已检索语料清单），可由审计输出复核入口判定证据链

## FR-0241 ANCHORED：fix/{issue} 隔离分支、基线继承与 M-DESIGN 进入

### AC-FR0241-01

  - 锚定确认（Sage 锚定通过 + 程序校验，或 Human 人工锚定，SM-01.5/.8）后 Runtime 进入 ANCHORED（SM-01.10）并按场景创建隔离 `fix/{issue}` 分支：
    - 场景 A（`--scenario post-release`）：`fix/{issue}` 分支从 `main` checkout（`git log --oneline fix/{issue} ^main` 为空，base 为 main HEAD）
    - 场景 B（`--scenario dev`）：`fix/{issue}` 分支从当前活跃 release 分支 checkout（base 为活跃 release 分支 HEAD）
  - Runtime 是唯一 branch/worktree authority：分支创建/HEAD 推进均经 Runtime 事件/审计可追溯（`git branch --list fix/{N}` 与事件流一致）

### AC-FR0241-02

  - 需求基线继承目标版本已批准的 spec/acceptance/接口与设计基线（source approval，不重新批准）：hotfix run 项目目录下**不创建** M-STORY / M-SPEC / M-ACC / M-REQ-APPROVAL 产物（对应阶段文件不存在或仅符号引用目标版本基线，无本 run 自有的批准流程事件）
  - hotfix run 不产生新 AC：`trac status` / `trac replay` 显示锚定的 AC 集合为跨版本引用 `AC-FRXXXX-YY@<version>`，指向目标版本既有条目而非本 run 新建

### AC-FR0241-03

  - ANCHORED 直接进入 canonical 阶段序列：事件流出现 `stage.entered(M-DESIGN)`；Runtime 记录 issue→AC 锚定作为 M-DESIGN baseline 输入（由 FR-0243 承接）
  - `trac status` / `trac replay` 显示 hotfix run、`fix/{issue}` 分支、`stage.entered(M-DESIGN)` 与锚定的 AC 集合；操作者可通过既有 `trac run` 继续派发（FR-0242 接续）

### AC-FR0241-04

  - FEATURE_ROUTE 退出（SM-01.9/.11，Human 确认转 feature）：事件流出现 `backlog.recorded`，退出原因与去向（backlog/new feature）经 `trac status` / `trac replay` 可见
  - FEATURE_ROUTE 不创建 `fix/{issue}` 分支（`git branch --list fix/{N}` 不出现该分支）、不残留半建 run（无活跃 hotfix run / 无 dangling worktree）

## FR-0242 hotfix run 并存与串行接续

### AC-FR0242-01

  - hotfix run 建立后由既有单一 active run 语义接续：`trac run` 继续当前 active run（hotfix 建立时即 active）；开发派发串行
  - `trac status` 输出含 `run_id` + `stage` + `branch` + `scenario`，足以让操作者分辨当前 `trac run` 继续的是哪个 run（hotfix vs 挂起的 feature run）；两 run 并存（场景 A 与活跃 run 独立、base=main；场景 B 与活跃 feature run 共享活跃分支——单活跃 run 原则的唯一受控例外）

### AC-FR0242-02

  - hotfix run 到达 boundary 终态（FR-0246）后，挂起的 feature run 恢复为 active：`trac status` 报告 active run 切换回 feature run（run_id 切换）；`trac run` 续跑 feature run（事件流出现 feature run 的后续派发）
  - 场景 A 的 hotfix 与活跃 run 完全独立，不被 `CHECK_ACTIVE` 阻塞（事件流无 CHECK_ACTIVE 拒绝记录）

### AC-FR0242-03

  - 挂起的 feature run 状态持久化保留：`trac status` / `trac replay` / `trac report` 在挂起期间可观察挂起 run 的当前状态与审计输出，不要求操作者手工重建
  - hotfix boundary 后挂起 feature run 的门禁可恢复观察（门禁状态可由 `trac check` / `trac status` 读出，不因挂起而不可恢复）

### AC-FR0242-04

  - 并发开发活动禁止（Aaron 裁定）：同一时刻只允许一个 trac 开发命令运行——feature 与 hotfix 的 `trac run` 不得同时执行；第二个并发 trac 命令被 writer lock 拒绝，CLI 报告持有者 PID（事件流出现 writelock 拒绝记录或 CLI stderr 含持有者 PID），退出非零，不推进派发

## FR-0243 hotfix M-DESIGN delta 设计与锚定承接

### AC-FR0243-01

  - hotfix 一律从 M-DESIGN 进入（v0.6 起废除 quick_rgr 免设计分流）：改动再小也产出 delta 设计；无 delta 设计时 M-DESIGN 门禁不放行（事件流不出现 `stage.exited(M-DESIGN)`）
  - Archer 产出相对继承基线的 delta 设计——architecture/interfaces/test-plan 三文档的修订增量（不是全量产品设计），产出物归 hotfix run 自己的项目目录（`.tracks/projects/v0.6/` 或 hotfix run 自有目录下，路径经 `trac status` / `trac report` 可见，且与目标版本基线目录区分）
  - machine contracts 沿用目标版本的 machine contracts（delta 设计不重写）；除非修复本身改变合同，delta 不引入新 contracts

### AC-FR0243-02

  - 锚定承接：Archer 的 delta 设计逐条引用 HOTFIX-TRIAGE 锚定的 AC 集合（跨版本引用 `AC-FRXXXX-YY@<version>`，引用与锚定输出一致）；Prism 在 M-DESIGN 评审复核锚定与 delta 设计
  - `trac status` / `trac replay` 显示 M-DESIGN delta 设计产出（三文档修订增量）、锚定 AC 集合的承接与 Prism 复核结果；delta 设计与独立评审不可省略

### AC-FR0243-03

  - Prism 在 M-DESIGN 评审推翻锚定（认定锚定集合不成立或不完整）-> 事件流出现回 SAGE_TRIAGE 的路由记录（上游产物缺陷），不消费 M-DESIGN 重派预算；Sage 重新锚定（SM-01.6 重派，≤3）产出新锚定输出后重新进入 M-DESIGN 承接
  - 锚定推翻与重派的触发原因、计数、新锚定输出经事件/审计可追溯

## FR-0244 hotfix M-TEST 回归测试 RED-first、Archer-decided 归属与空 Shield 增量放行

### AC-FR0244-01

  - RED-first 回归纪律：回归用例在带缺陷基线上先复现 RED（合法 Red = 回归测试在带缺陷基线上的行为断言失败），修复后变绿；事件/审计证据包含"先复现（RED）后修复（GREEN）"的 RGR 顺序，无"修复前已绿"的回归用例被当作合法 Red 放行

### AC-FR0244-02

  - Archer-decided 归属（delta test-plan）：回归用例归属由 Archer 在 delta test-plan 决定——integration/e2e 层归 Shield 在 M-TEST 补写、unit 层归 Devon 在 M-IMPL RED 阶段补写；delta test-plan 的层归属字段声明每条回归用例的归属（可由 `trac validate --file test-plan.md` 校验）
  - 两种归属都保持"先复现（RED）后修复（GREEN）"的 RGR 纪律（Shield 在 M-TEST 补写的用例、Devon 在 M-IMPL RED 阶段补写的用例均先复现 RED）

### AC-FR0244-03

  - AC trace 绑定目标版本既有 AC（跨版本引用 `AC-FRXXXX-YY@<version>`），与 HOTFIX-TRIAGE 锚定集合一致；hotfix 不产生新 AC（无本 run 自有 acceptance.md 新条目、无新 AC 编号分配）
  - `trac check trace` 对回归用例的 AC 绑定做闭合校验：回归用例挂对应既有 AC 之下（"bug 即有 AC 而此前没有检验"）；找不到对应 AC 的用例不被当作合法 hotfix 回归（路由至 FR-0248）

### AC-FR0244-04

  - 空 Shield 增量放行：若 Archer 将回归用例全部划归 unit 层，M-TEST 的 Shield 增量为空（M-TEST 期间无 Shield 写测试的活动事件 / 无受控测试 commit）
  - Runtime 凭"空 Shield 增量声明"+ `trac check trace` 由 unit 层回归用例闭合放行 M-TEST（`trac check trace` 退出码 0 且 unit 层回归用例闭合所有 required AC trace）；事件流记录放行依据（空 Shield 增量声明 + unit 层 trace 闭合证据），可由 `trac status` / `trac replay` 观察
  - 空 Shield 增量放行是主路径 M-TEST 步骤的非常规分支（产品不变量）：放行条件由程序门禁可复核，不靠 flow.md 引用兜底

## FR-0245 hotfix M-IMPL 隔离分支实现与场景 B stale reconcile

### AC-FR0245-01

  - 实现只发生在隔离 `fix/{issue}` 分支：修复提交（RGR 的 G commit / refactor commit / 最终修复 commit）的 git history 全部落在 `fix/{issue}` 分支（`git log --oneline fix/{issue}` 含修复提交，活跃分支 / feature run 工作区不含这些提交）
  - Runtime 保持唯一 branch/worktree authority：分支创建、HEAD 推进、worktree 创建/清理均经 Runtime 事件/审计可追溯；Devon 在 `fix/{issue}` 分支完成 RGR 与 review，task graph 按影响切片、scope 白名单沿用目标版本 layout
  - 修复实现不发生在活跃分支或 feature run 工作区：场景 B 的活跃分支与 `fix/{issue}` 分支写隔离（共享分支上的写串行见 FR-0242-04）

### AC-FR0245-02

  - 场景 B baseline stale reconcile（flow.md §16.3.3）：场景 B 的 fix 分支基线随活跃分支推进会 stale；merge 前须对活跃分支当前 HEAD reconcile，冲突 -> `needs_attention`（事件流 / `trac status` 报告 `needs_attention` 与冲突证据）
  - merge 语义本版不执行（FR-0246），但 reconcile 与冲突路径必须保留（否则场景 B 不可达）：reconcile 触发条件、冲突 `needs_attention` 状态经 `trac status` / `trac replay` 可观察，可由人工或后续阶段闭环

## FR-0246 M-IMPL 边界退出与发布语义声明

### AC-FR0246-01

  - hotfix 到达 M-IMPL 边界后以 boundary 终止：事件流出现 `stage.exited(M-IMPL)` -> `run.completed(terminal_state="boundary")`（沿用 v0.5 AC-FR0160-03 边界退出语义）
  - `fix/{issue}` 分支保留修复结果（分支未删除、修复提交可由 `git log`/`git show` 复核）；`trac status` 报告 `terminal=boundary`、`fix/{issue}` 分支、scenario 标识

### AC-FR0246-02

  - 发布语义本版不执行：场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号 / tag / artifact 发布、场景 B 的 merge 回活跃分支与 pre-release/开发渠道发布——事件流**不出现**这些发布动作的事件，`trac status` / `trac replay` 不报告"已发布"
  - 场景 A 的"为全体用户提供 hotfix"在本版以"修复完成、待发布"的边界状态呈现（`trac status` 报告 boundary 与 scenario=post-release，不伪称发布已执行）；发布语义作为流程设计载于 flow.md §16.3，随 M-VERIFY/M-RELEASE 注册后生效

### AC-FR0246-03

  - `trac replay <run_id>` 显示完整可审计旅程：`hotfix.requested` -> `triage.prechecked` -> `anchor.validated` -> `stage.entered(M-DESIGN)` -> M-DESIGN delta -> M-TEST RED-first -> M-IMPL isolated -> `run.completed(boundary)`
  - `trac report --run-id <run_id> --output .tracks/runtime/report --format md` 可生成包含上述事件序列与锚定/分支/boundary 证据的审计报告

## FR-0247 设计缺口回 hotfix M-DESIGN

### AC-FR0247-01

  - hotfix 旅程中发现 Archer 负责的设计缺口（delta 设计、测试计划或接口缺口）-> 事件流出现回 hotfix 自己的 M-DESIGN 的路由记录；`trac status` / `trac replay` 显示退出原因为设计缺口、去向为 hotfix 自己的 M-DESIGN
  - 由 Archer + Prism 裁定（M-DESIGN 评审闭环），**不新增 Human 技术门**：事件流中该回路由不出现 `human.review` / `human.approval` 作为前置门禁（区别于 ac_gap/spec_gap 的产品决定退出，FR-0248）

### AC-FR0247-02

  - 回到 M-DESIGN 的缺口在 hotfix run 内部闭环后重新进入 §3.2 的旅程（FR-0243 重新承接锚定集合）：事件流显示 M-DESIGN 闭环 -> 重新进入 M-TEST/M-IMPL 的派发序列，可由 `trac replay` 复核闭环与重新承接

## FR-0248 非实现偏差中段退出（ac_gap/spec_gap -> backlog/new feature）

### AC-FR0248-01

  - hotfix 旅程中发现需要新行为 / 新验收（`ac_gap` / `spec_gap`）-> 事件流出现退出路由记录（含原因 `ac_gap` / `spec_gap` 与去向 backlog/new feature）；`trac status` / `trac replay` 显示退出原因与去向
  - 作为产品决定请求 Human：退出路由前出现 Human 决定证据（`human.review` / `human.approval` 之一作为前置，区别于设计缺口的内部闭环 FR-0247）
  - 被判定为新行为的 issue 不再以 hotfix 身份继续：hotfix run 不继续派发 M-TEST/M-IMPL；该 issue 后续走 feature release 补齐程序行为规范（具体 feature 流程不在本 spec，但 hotfix run 不残留半建 run、不创建 `fix/{issue}` 之后的新提交作为发布证据）

## FR-0250 测试执行选择语义：全量 collect 与 R2/T-DELTA 差分执行

### AC-FR0250-01

  - 节点分类程序化且可审计（逐节点 digest，非整文件）：R2/T-DELTA 恰好且仅为新增节点或同一 nodeid 节点源/体 digest 变化（无依赖声明归因项）；R1/T-HIST = baseline 节点且 digest 未变化；分类由 Runtime 判定并落事件，分类依据（baseline 冻结测试资产的逐节点 digest + 本次变更声明）经 `trac replay` / `trac report` 可追溯；同输入复跑分类一致；同一文件内未变化兄弟节点保持 R1/T-HIST（整文件 digest 分类 = 缺陷，不得把未变兄弟节点翻成 R2 再以意外通过失败）
  - R1 快照机制可执行且来源唯一（v5）：进入 M-TEST 时、本 run 首个 Shield WRITE 派发之前，Runtime 持久化 `test.baseline_captured(passed)`——对继承测试树做 unit+integration+e2e 全量 collect，payload 含 stamped baseline/tree identity（`baseline_id`）、三层 layers、节点计数与逐节点 digest blob；事件 seq 先于本 run 首个 Shield WRITE 派发；WRITE 后 COLLECT 的分类以该快照为唯一 baseline 来源（`test.selected.baseline == test.baseline_captured.baseline_id` 可复核）——既非既往 run 事件猜测、也非 WRITE 后可变树重算；首次项目可合法捕获空集（`empty_baseline=true` 仅由成功 capture 产生），COLLECT 分类时刻缺失 passed 快照 = fail-closed；capture 中任一层 collect/import 失败 = 上游/设计/合同缺陷 fail-closed 路由（不静默空置 R1、不重派 Shield）；中断/重启/replay 复用同一 stamped capture
  - COLLECT 为全量（unit+integration+e2e 三层）：`test.collected(passed)` payload 含全部可 collect 节点计数并区分 inherited(R1)/delta(R2) 两类——历史 unit 节点计入 inherited_r1 并出现在逐节点分类表（class=r1, layer=unit）；变更 support/fixture 同样参与 collect/import；任一节点 collect 失败 -> `test.collected(failed)` 回 Shield（既有子状态机），不放行带不可 collect 节点的基线

### AC-FR0250-02

  - M-TEST 时序（v3，v5 增补入口）：`入口 baseline capture(pre-WRITE) -> WRITE -> 全量 COLLECT -> RED_CHECK(SELECT_R2) -> PRISM_REVIEW -> EXIT`——事件流中 `test.baseline_captured` 先于本 run 首个 Shield WRITE 派发、`red.validated` 先于 `prism.verdict`；RED_CHECK 非法失败/意外通过在 Prism 之前路由 DIAGNOSE/WRITE
  - RED_CHECK 本地执行仅限实际 R2/T-DELTA：执行前出现 `test.selected`（scope=r2_delta，payload 含选择依据 + 被选节点集合 + baseline/commit = selection identity）；每条执行/`red.validated` 证据的被选节点集合 ⊆ R2/T-DELTA 且逐节点判定合法 Red（判据不变：行为断言失败/桩合同 token 失败/symbol 缺失）
  - feature M-TEST 的 R2 选择集为空 = fail-closed（不产出合法 Red 判定、不 vacuous 通过；hotfix unit-only 显式增量声明的放行旁路保留，AC-FR0244-04）
  - M-TEST 永不全量：M-TEST 阶段事件流与执行审计无任何 R1/T-HIST 节点的本地执行记录（含历史 unit 节点——unit 仅参与 collect 分类，不被 M-TEST 执行；当前完整 FULL 套件 R1+R2 的夜间回归归 nightly CI contract，FR-0255）；`trac status` / `trac replay` 可复核"M-TEST 无本地全量执行"

### AC-FR0250-03

  - PRISM_REVIEW 在 RED_CHECK 之后消费 Runtime **当前树**证据：Prism 评审引用 `red.validated` 证据 identity（selection binding）并运行隔离 counterexample kill（反例构造与结论可追溯）；PRISM_REVIEW 期间事件流无普通套件重跑的执行记录（评审不以重跑套件为手段）

### AC-FR0250-04

  - baseline 历史节点缺失于本次全量 collect = fail-closed 测试资产删除（本版无授权删除路径）：`test.collected(failed)`（error_class=asset_deleted）路由为 test contract defect（DIAGNOSE → Shield），M-TEST 不产出合法 Red 判定、不退出；静默注销/排除该历史节点（不计失败、继续门禁）为缺陷行为

## FR-0251 task 级 GREEN 选择（per-task SELECT_TASK）

### AC-FR0251-01

   - 每 task GREEN_GATE 前出现 `test.selected`（scope=task_if，payload 含 task id、task IF 集合、被选节点集合、baseline/commit）；被选集 = 该 task 不可变 R commit 的 Runtime 捕获 RED artifact manifest 与 GREEN-touched unit 文件导出的 targeted unit 节点 + 命中本 task IF 集合的 integration 节点（test-plan §8 IF 行只含 integration/e2e，unit 归属不经 §8）
  - 被选集不含 e2e、不含 R1/T-HIST 全量（e2e 的本地执行仅出现在 FULL 链事件中，FR-0253）；GREEN_GATE 通过判定只依据该被选集的执行证据，且证据绑定该 selection identity

### AC-FR0251-02

  - 上游变化（task IF 集合或 baseline 变化）使既有 SELECT_TASK 选择及其证据 stale：stale 证据不被复用为通过依据，GATE 按新 identity 重新选择并重跑同 scope（新 `test.selected` 与新执行证据可见）

## FR-0252 REFACTOR 阶段的 identity 绑定证据复用

### AC-FR0252-01

  - identity 未变（`refactor.no_change` outcome 或工作区内容 digest 与 Green commit 一致）-> 事件流出现 `evidence.reused`（kind=green，payload 引用被复用 Green 证据的 evidence identity）；REFACTOR_GATE 无新的测试执行事件即通过（committed | no_change 语义不变）

### AC-FR0252-02

  - identity 有变化 -> 按同一 selection scope 重跑（同 task IF 的 targeted unit + integration）：新 `test.selected`（selection identity 更新）与新执行证据（绑定新 attempt/actor）可见；不出现跨 scope 复用旧证据的放行

### AC-FR0252-03

  - 复用与重跑判定程序化且落事件（判定依据 = 内容 identity 比对，非 Agent 自述）；REFACTOR 动 public interface -> upstream 路由不变（既有子状态机语义）

## FR-0253 FULL 链收敛与失败台账（FULL_1 → 台账 → SELECT_DIFF → FULL_F）

### AC-FR0253-01

   - 全部 task 完成后 ISLAND_GATE_2 进入 FULL 链：首个事件 `full.executed`（round=FULL_1，suite=unit+integration+e2e）；FULL_1 每个失败节点生成一条台账事件 `ledger.opened`（state=OPEN，绑定节点身份 + failure_signature + selection identity + evidence identity）；台账身份 = `(node, failure_signature)`——同一节点新 signature 新建身份，同一 signature 重启先前身份
   - FULL_1 干净且台账为空 -> 同一 stamp 的 `full.executed`（round=FULL_1）标注 `serves_as_full_f=true` 直接充当 FULL_F，M-IMPL 照常出口（`stage.exited(M-IMPL)`）；事件流中不出现紧邻的等价 `full.executed`（round=FULL_F）重复执行；fallback 充当语义（AC-FR0253-04）不变

### AC-FR0253-02

   - 台账状态机逐转移落事件 `ledger.transitioned`（OPEN->CLASSIFIED->FIXED->PROVEN；上游变化置 STALE；闭合转移集另含 FIXED->OPEN 证明失败与 FULL_F 重现已 PROVEN 的同一 `(node, failure_signature)` -> 确定性 reopen `PROVEN->OPEN`）；分类/修复走既有 DIAGNOSE/重派路径（测试缺陷→Shield、实现缺陷→Devon、上游缺口→对应阶段），per-agent attempt 预算照旧消费（≤3/escalation 事件可见）

### AC-FR0253-03

   - 修复-证明循环是逐条目的：每条目到达 FIXED 后立即出现其确定性 `test.selected`（scope=select_diff，payload 含该条目身份 + 受影响节点集合 + 选择依据 = selection identity）并重跑证明——通过则该条目转移 `FIXED→PROVEN`；同签名失败则该条目转移 `FIXED→OPEN` 并重新分类/修复；多条目 union/batching 仅在每条目选择/证据可单独归因时作为可选优化出现，不构成通过前提
   - 差分证明结果落事件可审计

### AC-FR0253-04

   - SELECT_DIFF 不可证明（无法给出可靠选择集）-> 回退 FULL：事件流出现 `full.executed`（round=fallback_full）；fallback 结果逐条目落转移——通过条目 `FIXED→PROVEN`、同签名失败条目 `FIXED→OPEN`；该 FULL 干净时直接充当 FULL_F（事件标注 fallback 充当关系），不再另行执行 FULL_F，M-IMPL 照常出口

### AC-FR0253-05

  - 全部条目 PROVEN 且无 STALE 后执行 `full.executed`（round=FULL_F）；FULL_F 出现新失败 -> 追加台账回到分类/修复循环直至干净——事件序列可含任意多轮收敛循环（无 loop-count cap、无"超限放弃"路径），仅 per-agent attempt 预算消费
   - FULL_F 干净 -> `stage.exited(M-IMPL)`；台账存在 OPEN|CLASSIFIED|FIXED|STALE 或未知状态条目时不出现 `stage.exited(M-IMPL)`（fail-closed，NFR-0120）

### AC-FR0253-06

  - FULL_F 再次揭示先前已 PROVEN 的同一 `(node, failure_signature)` -> 确定性 reopen 转移 `PROVEN->OPEN`（重启先前台账身份并重证，不新建身份）；同一节点的新 failure signature -> 新台账身份（`ledger.opened` 新条目）；clean 判据与崩溃回放（NFR-0120）对 reopen 语义一致

## FR-0254 M-VERIFY 对干净 FULL_F 的复用

### AC-FR0254-01

  - 复用规则绑定：candidate 相对 M-IMPL 干净 FULL_F 未漂移（identity 一致且相关条目无 STALE）时复用 FULL_F 证据（`evidence.reused` kind=full_f）、等待 CI 证据 `ci.run_observed(passed)` API 回读通过才退出、漂移/stale 才按 LOCAL_GATES 重跑——该 canonical 语义载于本 spec 与 flow.md §11 且 trace 一致；本版 M-VERIFY 不注册，语义随其注册后生效（同 FR-0246 发布语义处理）

### AC-FR0254-02

  - 本版负断言：v0.6 已注册阶段的事件流中，FULL（unit+integration+e2e 全量）的本地执行仅发生在 ISLAND_GATE_2 的 FULL 链（FR-0253）；不存在绕过复用规则、以其它名义触发的候选本地全量重跑执行记录

## FR-0255 测试命令与并发的 Archer 独家所有权及 nightly CI contract

### AC-FR0255-01

  - 执行审计记录每次测试执行的实际命令行，与 Archer machine contract 定义的测试命令（worker/dist flag 与 `--junitxml={result}` 内嵌于命令字符串）逐字一致；审计比对程序对 contract 模板替换展开（期望 argv）与实际执行 argv 在**同一 argv0 解析规则**下解析后逐字比对——两侧解析一致才可比，不等即 fail-closed；选择执行使用 contract 声明的 `run_selected`（含 `{nodes}` 占位符）——schema 切片激活后 `run_selected` 缺失 = contract_error fail-closed，事件与执行审计无 Runtime 向 `run` 追加 nodeid 或合成调用的记录；无 Runtime 注入的 contract 之外并发参数（无 `-n`/`--dist`/worker 覆盖）——"Runtime 永不注入并发"由审计比对复核，非自述
  - 【v6】逐节点结果机器可读通道：contract loader/validate 对每条 run/run_selected 强制 `{result}` 占位符恰好一次（run_selected 另含 `{nodes}` 恰好一次），缺失或重复 = contract_error fail-closed；事件与执行审计无 Runtime 注入的 `--junitxml` 等 junit flag（结果写入 flag 只能由 Archer 内嵌）；每次门禁执行的测试结果经 `{result}` JUnit XML 机器读取——testcase 身份恰好覆盖被选集（全量 run = collect FULL 全集），文件缺失/畸形、身份重复、被选缺席、多余节点一律 contract_error fail-closed，stdout/stderr 不作逐节点分类权威；归一化逐节点结果以 `outcomes_ref` blob 在 temp 清理前持久化并可由 `trac replay` 审计，replay 无已持久化结果时重跑该命令（不把缺失结果当通过）

### AC-FR0255-02

  - machine contract 含 nightly CI job contract（当前完整 FULL 套件 R1+R2 夜间回归的周期回归层，历史回归责任强调 R1/T-HIST，D-18 三层机制）：本版其存在性经 contract 文件 + `trac validate` 校验可审计（无 nightly 派发/回读事件承诺）；本版不实现 nightly 结果取回通道（spec 范围排除），本地门禁（red.validated/GREEN/FULL 链/M-VERIFY 复用）均不依赖 nightly 结果

## NFR-0100 HOTFIX-TRIAGE 确定性与 fail-closed 可审计性

### AC-NFR0100-01

  - PRECHECK 程序预检确定性（无 LLM）：issue type / scenario / 活跃分支校验是纯程序判断——事件流与 dispatch 审计在 PRECHECK 步骤无 agent 派发记录（无 Sage/Archer/Prism/Shield/Devon 派发对应此步），结果由程序规则唯一确定（同输入复跑结果一致，沿用 v0.5 NFR-0020 append-only 事件溯源）

### AC-NFR0100-02

  - SAGE_TRIAGE 的锚定输出经程序校验：所引每条 AC 必须真实存在于所指版本 acceptance.md，引用不实 = validate fail（`trac validate` 对应路径返回非零退出）；校验结果（通过 / 失败 / 重派计数）可由事件 / 审计记录回溯
  - 校验结果与重派计数经 `trac replay` / `trac report` 可见，可由独立方复核（沿用 v0.5 AC-NFR0020-01/02 append-only 与可重建语义）

### AC-NFR0100-03

  - fail-closed：REJECTED 与 FEATURE_ROUTE 不创建 `fix/{issue}` 分支（无分支副作用）——`git branch --list fix/{N}` 在 REJECTED 与 FEATURE_ROUTE 后均不出现该分支
  - 锚定不成立时（NO_ANCHOR / 重派超限）系统不擅自判为新行为：进入 AWAIT_HUMAN 交 Human 决定，事件流不出现"自动转 feature"路由（FR-0240-05）

### AC-NFR0100-04

  - HOTFIX-TRIAGE 事件 `hotfix.requested` / `triage.prechecked` / `anchor.validated` / `human.anchor(manual|feature_route)` / `stage.entered(M-DESIGN)` / `backlog.recorded` 均 append-only 写入事件流，不改写既有行（沿用 v0.5 AC-NFR0020-01）；投影表可从事件完整重建（drop 投影表后 `trac status` / `trac report` 重建的入口状态与原一致）

## NFR-0110 hotfix run 可恢复性与可观察性

### AC-NFR0110-01

  - hotfix run 与既有 feature run 并存时，run 选择 / 续跑语义让操作者能从 `trac status` 分辨当前 `trac run` 继续的是哪个 run（输出含 `run_id` + `stage` + `branch` + `scenario`）；挂起 run 的状态持久化保留
  - 挂起 run 的当前状态与审计输出可由 `trac status` / `trac replay` / `trac report` 观察，不要求操作者手工重建（区别于无持久化的 ad-hoc 状态）

### AC-NFR0110-02

  - hotfix run 到达 boundary 终态后挂起的 feature run 恢复 active，其门禁可恢复观察：`trac status` 报告 active run 切换回 feature run，`trac check` / `trac status` 可读出 feature run 的门禁状态（不因挂起而不可恢复，区别于挂起即丢失）

### AC-NFR0110-03

  - Runtime 中断或重启后必须能从 append-only 事件流恢复 hotfix run 与挂起 run 的精确状态（沿用 v0.5 AC-FR0200-01/02 可休眠与崩溃恢复语义）：重启后 `trac status` 报告的状态与中断前一致，不重跑已完成的入口子状态 / 已完成的阶段派发，从打断处继续

## NFR-0120 失败台账持久性、可重建性与 fail-closed

### AC-NFR0120-01

  - 台账 append-only + WAL：`ledger.*` 事件先落事件流再据以判定/推进，不改写既有行；Runtime 中断/重启后由事件回放重建台账，重建状态（含 `PROVEN->OPEN` reopen 转移）与中断前一致（`trac status` / `trac report` 显示相同台账），FULL 链从重建状态精确续跑，不重复与重建状态一致的历史执行轮次

### AC-NFR0120-02

  - fail-closed：未知状态、缺失条目、非法转移一律不视为干净——脏台账（含未知/缺失/STALE 条目）上不跳过 FULL_F、不产出 `stage.exited(M-IMPL)`，M-VERIFY 复用不成立（FR-0254 以干净 FULL_F 为前提）；fail-closed 触发原因落事件可审计

## NFR-0130 selection/evidence identity 可审计性与复用判定一致性

### AC-NFR0130-01

  - 每条 `test.selected` 携带完整 selection identity（选择依据 + 被选节点集合 + baseline/commit + tree_stamp 脏工作树内容 stamp）；每条执行/复用证据绑定节点身份 + selection identity + baseline/commit + attempt + actor；全部身份信息 append-only 落事件，经 `trac replay` / `trac report` 可审计

### AC-NFR0130-02

  - 复用判定只认 identity 一致的证据：SELECT_TASK stale 判定（FR-0251）、REFACTOR 复用 Green（FR-0252）、SELECT_DIFF 差分选择（FR-0253）与 M-VERIFY 复用 FULL_F（FR-0254）共用同一 identity 判据，identity 不一致（stale）的证据一律不作通过依据；上游变化置 STALE 的传播在各处一致可见，不由各阶段自定变体
