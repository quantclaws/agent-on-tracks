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
