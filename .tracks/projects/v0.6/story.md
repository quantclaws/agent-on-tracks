---
story_id: S-001
title: hotfix 工作流：trac hotfix 入口与继承基线的 M-DESIGN→M-IMPL 旅程
created: 2026-08-18
status: draft
sha: 24cdf790d0353fac75d2665b47c2b86363026e63b51b62f767fd941871d4e789
---

# S-001: hotfix 工作流：trac hotfix 入口与继承基线的 M-DESIGN→M-IMPL 旅程

## 1. 原始输入

> 增加 hotfix 工作流。要点是：
>
> 1. 两个场景。一是已发布版本的 hotfix ,目标是为全体用户提供 hotfix；二是正在开发中的版本的 hotfix，目标是供开发者自己、alpha\beta 用户使用。
>
> 2. 两个场景的主要区别是起点分支不同。已发布版本的hotfix 始终从 main 分支 checkout，修复后再merge 回 main，同时活跃的 release分支应该 merge；正在开发中的版本的 hotfix，则从当前活跃分支 checkout，修复后提交到活跃分支，随后与活跃分支一起合并进 main（不属本流程）
>
> 3. hotfix 流程从 M-DESIGN 开始，后续流程与 feature release 一致。
>
> 4. 当前阶段的命令入口为 trac hotfix，参数为 github issue 号。该 issue 号为对应 repo 中的 issues.

> **Scribe [RESOLVED]:** TRIAGE blocker（实现边界）：seed 说"hotfix 流程从 M-DESIGN 开始，后续流程与 feature release 一致"，但宿主当前实现停在 M-IMPL→M-VERIFY 边界（v0.5 裁定；spec-v06-consolidation.md 记录 M-IMPL 后无注册阶段，M-VERIFY/M-RELEASE 计划为 v0.6 末尾独立最小 story）。而 seed 场景 A 的目标是"为全体用户提供 hotfix"，flow.md §16.3.5 把 merge 回 main、同步 merge 活跃 release 分支与 patch 发布放在 M-RELEASE/M-PUBLISH。请问本 story 的实现边界在哪里？方向 A) 与当前 feature release 一致，实现到 M-IMPL 边界为止；merge/发布语义作为流程设计（flow.md §16.3）记录并在 M-VERIFY/M-RELEASE 注册时另行落地，story 如实声明场景 A 的"全体用户"结果在本版尚未执行。方向 B) 本 story 包含最小发布侧实现：场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号，场景 B 的 merge 回活跃分支，使两场景可见结果在本版即可执行。方向 C) 完整实现 hotfix 全链（含 M-VERIFY/M-RELEASE/M-PUBLISH）。不同答案显著改变 v0.6 范围与验收路径。推荐 A：与"后续流程与 feature release 一致"及 v0.6 集中整治定位最贴合，且 M-VERIFY/M-RELEASE 已有独立最小 story 计划；merge 语义在 flow.md 已确立为流程设计，不阻塞 DRAFT。
>> **Maestro:** 裁定 A。依据：1) 用户已裁定 v0.6 不含 spec-v06-consolidation.md 的内容（其中多数已在 v0.5 实现或另有 GitHub issue 跟踪，M-VERIFY/M-RELEASE 的注册属后续独立工作）；2) seed 要求'后续流程与 feature release 一致'，而当前 feature release 的实现边界就是 M-IMPL→M-VERIFY 边界。请 story 如实声明：两场景的 merge/发布语义（场景 A merge 回 main + 同步 merge 活跃 release 分支 + patch 版本；场景 B merge 回活跃分支）已作为流程设计载于 wiki/flow.md §16.3，其执行随 M-VERIFY/M-RELEASE 阶段注册后生效；本版 hotfix 交付入口（trac hotfix <issue> --scenario post-release|dev）+ fix/{issue} 隔离分支 + 继承基线进入 M-DESIGN→M-IMPL 到边界为止的完整可审计旅程。
>> **Scribe:** 已按裁定 A 落文：story §3.1 交付入口（trac hotfix <issue> --scenario post-release|dev）+ fix/{issue} 隔离分支 + 继承基线直接进入 M-DESIGN；§3.2 声明两场景 merge/发布语义（A merge 回 main + 同步 merge 活跃 release 分支 + patch 版本；B merge 回活跃分支）作为流程设计载于 flow.md §16.3、本版不执行、随 M-VERIFY/M-RELEASE 注册后生效；§3.3 与 BS-05/BS-09/BS-10 对应记录非实现偏差退出与 M-IMPL 边界退出；§5 Out-of-Scope 与 §7 风险如实声明场景 A 的'全体用户'结果本版为'修复完成、待发布'边界状态。DRAFT 完成。

## 2. 用户意图

- **想完成什么**：为宿主 repo 建立 hotfix 工作流——操作者用一个命令（`trac hotfix <issue> --scenario post-release|dev`）把 GitHub issue 号对应的实现偏差修复带入一条从 M-DESIGN 开始的完整可审计旅程，并按受众区分两个场景：场景 A（post-release）面向已发布版本、最终服务全体用户，场景 B（dev）面向正在开发中的版本、服务开发者自己与 alpha/beta 用户。
- **当前哪里受阻**：宿主只有 feature release 一条完整流程（`trac start` → M-STORY…M-IMPL 边界）；已发布版本或开发中版本上的缺陷修复没有专门入口与隔离分支，只能当作新 feature 从需求阶段重新走一遍，也无法区分"修复已发布版本缺陷"与"修复开发中版本缺陷"两个不同受众与分支语义。
- **完成后能看到什么结果**：操作者执行 `trac hotfix <issue> --scenario post-release|dev`，看到 Runtime 执行 HOTFIX-TRIAGE（bug 预检 + Sage 语义锚定）、按场景创建隔离 `fix/{issue}` 分支、继承目标版本已批准基线并直接进入 M-DESIGN；随后通过既有 `trac run`/`trac status`/`trac replay` 看到 delta 设计、回归测试与修复实现依次完成并到达 M-IMPL 边界，`fix/{issue}` 分支保留修复结果。两场景的 merge/发布语义（场景 A merge 回 main + 同步 merge 活跃 release 分支 + patch 版本；场景 B merge 回活跃分支）按裁定作为流程设计载于 flow.md §16.3，其执行随 M-VERIFY/M-RELEASE 阶段注册后生效，本版如实声明场景 A 的"全体用户"结果尚未执行。

## 3. 核心操作路径

### 3.1. trac hotfix 入口：HOTFIX-TRIAGE、隔离分支与 run 建立

- **变更基线**：新增 — 当前 CLI 没有 hotfix 命令，缺陷修复只能作为新 feature 从 `trac start <version>` 进入 M-START→M-STORY，且存在活跃 run 时新需求进入 backlog。本次新增顶层命令 `trac hotfix <issue> --scenario post-release|dev`，建立独立的 hotfix run（自带项目目录），跳过需求阶段直接进入 M-DESIGN；场景 A 不被活跃 run 阻塞，场景 B 为单活跃 run 原则的唯一受控例外。
- **入口/触发**：操作者在宿主 repo 工作区执行 `trac hotfix <issue> --scenario post-release|dev`；`--scenario` 必填（post-release = 场景 A 已发布版本，dev = 场景 B 开发中版本），缺省时 Runtime 询问 Human，不从 issue 推断。

1. 入口执行 HOTFIX-TRIAGE（flow.md §16.4 入口附属子状态机，非 canonical 顶层阶段），程序预检（确定性、无 LLM）：`<issue>` 是宿主 repo 的 GitHub issue 且 type=bug（宿主 bug issue template 提供「版本 / 对应 FR/NFR」字段，均可选——终端用户不可能知道编号，字段仅作辅助、不完全采信）；`--scenario` 合法；`--scenario dev` 还需存在当前活跃 release 分支。预检失败（非 bug / issue 不可定位 / 场景不合法）→ REJECTED：不建 fix 分支，报告原因与下一步（补全 issue 或改走 feature 流程）。
2. Sage 语义锚定（triage 的语义部分）：Sage 依据 issue 场景/症状与可选 FR/NFR 字段（辅助），在目标版本及历史版本的 spec.md/acceptance.md 中自行决定对应的既有 AC 集合，输出逐条锚定理由与出处（版本+条目），或 NO_ANCHOR 报告（含已检索版本与语料清单）。Runtime 程序校验锚定输出——所引每条 AC 必须真实存在于所指版本的 acceptance.md，引用不实 = validate fail，重派 Sage（≤3 次）。
3. NO_ANCHOR 或重派超限 → awaiting_human：Human 人工锚定（指认 AC）或确认转 feature（FEATURE_ROUTE，两种成因同路——spec/acc 存在但 Sage 无法关联，或功能早于规范文档存在、历史版本无规范文档可引）。锚定确认（ANCHORED）后 Runtime 按场景创建隔离 `fix/{issue}` 分支并建立 hotfix run：场景 A 从 main checkout，场景 B 从当前活跃 release 分支 checkout；需求基线继承目标版本已批准的 spec/acceptance/接口与设计基线（source approval，不重新批准）；不创建 M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL。
4. 直接 `stage.entered(M-DESIGN)`；操作者通过 `trac status`/`trac replay` 看到 hotfix run、`fix/{issue}` 分支与 M-DESIGN 入口。hotfix run 与既有 feature run 的并存按 flow.md §16.3.7 处理：允许两个并发分支，但开发派发保持串行（同一 writer lock），不允许同时运行 feature 与 hotfix 两个 trac 命令（Aaron 裁定）。

> **Maestro [RESOLVED]:** Human 裁定（2026-08-19，逐字记录）——关于如何找到对应既有 AC：
> 
> 「我的想法是，trac hotfix <issue> xxx 时，内部先做一个 triage，来找到对应的 acc.
> 
> 1. github issue 必须为 bug 类型，并且在 template 中要求填入版本、对应的 FR/NFR，从而 acc 可推导（根据描述）。但是，最终用户不可能知道 FR/NFR 的编号，所以，这些字段是可选项。这也意味着我们无法仅靠这些字段 来推断 acc，所以，必须引入 llm 的语义分析能力。
> 2. 由Sage（或者 Archer）根据issue中填写的 FR/NFR版本号（辅助，不完全采信），以及历史的 spec/acc ，根据 issue 中提出的场景、症状，自行决定对应的 acc
> 3. 有可能 Archer 无法推断出对应的 acc；原因是，一、spec/acc 确实存在，但 Archer 无法关联起来；二、该功能存在于历史版本中，当时还没有规范的 spec/acc 等文档。这种情况下，需要当成 feature 来做，以便补齐程序行为规范。」
> 
> 已据此落定流程设计 wiki/flow.md §16.4（HOTFIX-TRIAGE 入口子状态机，编程实现的技术参考）：语义锚定角色定为 Sage（需求语义归 Sage；Archer 在 M-DESIGN 承接锚定集合、Prism 复核）；锚定输出经程序校验（所引 AC 必须真实存在于所指版本 acceptance.md）；重派 ≤3；NO_ANCHOR 或超限 → awaiting_human，Human 可人工锚定或确认转 feature。请 Scribe 据此修订 story：§3.1 入口验证改述为 HOTFIX-TRIAGE（bug 类型预检 + 可选字段辅助 + Sage 语义锚定 + AWAIT_HUMAN 人工锚定/转 feature）；BS-01、BS-05 对应更新（BS-05 补「功能早于规范文档存在」成因）；§5 补充宿主 bug issue template 约束（版本/FR/NFR 字段可选）；§7 补锚定失败风险；流程引用更新为 §16.4。
>> **Scribe:** 已按裁定全部落文（RESPOND）：§3.1 入口改述为 HOTFIX-TRIAGE 四步（程序预检/REJECTED → Sage 语义锚定+程序校验重派≤3 → NO_ANCHOR/超限 → awaiting_human 人工锚定或确认转 feature → ANCHORED 建分支进 M-DESIGN），小节标题同步更新；BS-01 改述为 HOTFIX-TRIAGE 版（含 awaiting_human 路径）；BS-05 补「功能早于规范文档存在」与「spec/acc 存在但 Sage 无法关联」两成因并引 §16.4；§5 补宿主 bug issue template 约束（版本/FR/NFR 字段可选、仅辅助、不完全采信）；§7 补锚定失败风险（Sage 无法锚定 → awaiting_human 人工路径，Human 确认转 feature 不残留半建 run）；流程引用更新为 §16.4（§3.1、BS-01、BS-05、§6、§7）；§2 完成结果与 §3.3 入口/步骤 1 同步对齐 triage 语义。

- **完成结果**：锚定确认（Sage 锚定通过或 Human 人工锚定）后 hotfix run 建立并可继续（走 3.2）；预检失败（REJECTED）不建立 run，操作者看到明确原因与下一步——补全 issue 后重试或改走 feature 流程；NO_ANCHOR 经 Human 确认转 feature（FEATURE_ROUTE）则退出 hotfix 转 backlog/new feature（产品决定，需 Human）。

### 3.2. 继承基线的 M-DESIGN→M-IMPL 完整可审计旅程

- **变更基线**：修改/扩展 — 现有 feature release 从 M-STORY 进入、经 M-SPEC/M-ACC/M-REQ-APPROVAL 后在 M-IMPL 边界退出（M-VERIFY/M-RELEASE 未注册）。本次 hotfix 从 M-DESIGN 进入、跳过需求阶段，各阶段语义按 flow.md §16.2/16.3 收窄（delta 设计、回归测试、影响切片），边界仍为 M-IMPL；两场景的 merge/发布语义本版不执行（Maestro 裁定 A）。
- **入口/触发**：hotfix run 建立后（3.1），操作者通过既有 `trac run` 继续派发；Runtime 复用 canonical 阶段序列与全部子状态机、角色、评审协议、重派预算与门禁语义推进。

1. **M-DESIGN**：Archer 产出相对继承基线的 delta 设计（architecture/interfaces/test-plan 三文档的修订增量，产出物归 hotfix run 自己的项目目录），锚定所偏离的 FR/AC（引用目标版本 spec/acceptance 的既有条目，跨版本引用 `AC-FRXXXX-YY@<version>`）；Prism 复核锚定与 delta 设计；contracts 沿用目标版本的 machine contracts（除非修复本身改变合同）。
2. **M-TEST**：回归用例必须先复现 RED 再变绿（修复前既有用例必然全绿——形式缺失或未触发 RED 条件）；合法 Red = 回归测试在带缺陷基线上的行为断言失败。回归用例归属由 Archer 在 delta test-plan 决定：integration/e2e 层归 Shield（M-TEST 补写），unit 层归 Devon（M-IMPL RED 阶段补写）。若 Archer 将回归用例全部划归 unit 层，M-TEST 的 Shield 增量为空，Runtime 凭"空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合"放行 M-TEST。AC trace 绑定目标版本既有 AC（跨版本引用 AC-FRXXXX-YY@<version>），hotfix 不产生新 AC。操作者通过 `trac status`/`trac replay` 看到测试资产与回归证据（含空 Shield 增量声明时的 unit 层 trace 闭合证据）。
3. **M-IMPL**：task graph 按影响切片（通常远小于 feature release），scope 白名单沿用目标版本 layout；实现只发生在隔离 `fix/{issue}` 分支（Runtime 是唯一 branch/worktree authority）；场景 B 的基线随活跃分支推进会 stale：merge 前须对活跃分支当前 HEAD reconcile，冲突 → `needs_attention`。Devon 在 `fix/{issue}` 分支上完成 RGR 与 review，修复提交落在该分支。
4. 到达 M-IMPL 边界后以 boundary 终止（`run.completed` terminal_state=boundary）；操作者通过 `trac status`/`trac replay` 看到边界结果与 `fix/{issue}` 分支上的修复提交。

> **Maestro [RESOLVED]:** Human 裁定（2026-08-19，逐字记录）——hotfix 的 M-TEST 阶段回归用例归属：
> 
> 「另外，在 M-TEST 阶段，Shield是需要补 RED 用例的。因为之前的测试用例都肯定是通过的（无论是形式上缺；还是形式上具备，但没有触发 RED 的条件），要能验证代码修复了，就必然要先复现（即找到 RED 条件），然后再让它变绿。
> 
> 但是，究竟是 Shield 来补测试，还是 Devon 补测试（归属单元测试），这都有可能，需要 Archer 来决定。」
> 
> 已据此落定 wiki/flow.md §16.3.2：回归用例必须先复现 RED 再变绿（修复前既有用例必然全绿——形式缺失或未触发 RED 条件）；归属由 Archer 在 delta test-plan 决定——integration/e2e 层归 Shield（M-TEST 补写），unit 层归 Devon（M-IMPL RED 阶段补写）；若全部划归 unit 层，M-TEST 的 Shield 增量为空，Runtime 凭空增量声明 + trac check trace 由 unit 层回归用例闭合放行。请 Scribe 修订：§3.2 第 2 点（M-TEST）改述归属规则与 RED-first 纪律；BS-07 对应更新（不再固定 Shield/M-TEST 为唯一回归载体）；§5 约束补充。
>> **Sage:** Sage review 发现：本裁定（ownership split + 空 M-TEST 分支）未完整落入 story 正文，Scribe 的 RESPOND 仍缺。核对：(1) §3.2 第 2 点（line 64）有 RED-first（'合法 Red = 回归测试在带缺陷基线上的行为断言失败'）但缺 ownership split——仍写'Shield 以复现 issue 的回归测试为主'，未述 Archer 在 delta test-plan 决定归属、integration/e2e→Shield(M-TEST 补写)/unit→Devon(M-IMPL RED 阶段补写)、以及 M-TEST Shield 增量可空 + Runtime 凭'空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合'放行。(2) BS-07 EARS（line 129）仍以'WHEN hotfix 进入 M-TEST'触发并固定 Shield/M-TEST 为唯一回归载体，未覆盖空 M-TEST 分支。(3) §5 约束未补 ownership split。flow.md §16.3.2（line 982）已含完整 ownership split，但 story 的行为种子 BS-07 是下游 spec 作者继承的锁定不变量——若 BS-07 固定 Shield/M-TEST 为唯一载体，下游 FR 会与 Human 裁定及 flow.md 冲突。请 Scribe 完成 T-003 RESPOND：§3.2 第 2 点与 BS-07 改述以反映 Archer-decided ownership + 空 M-TEST 分支，§5 补约束。这是主路径 M-TEST 步骤的非常规分支（空测试阶段 + 跨层 trace 闭合），属产品不变量，不应仅靠 flow.md 引用兜底。
>> **Scribe:** 已按 T-003 finding 完成 RESPOND（三处落文）：(1) §3.2 第 2 点（M-TEST）改述为 RED-first + Archer 在 delta test-plan 决定归属（integration/e2e→Shield 在 M-TEST 补写、unit→Devon 在 M-IMPL RED 阶段补写）+ 空 Shield 增量时 Runtime 凭'空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合'放行，AC trace 绑定既有 AC 不产生新 AC。(2) BS-07 EARS 改述以覆盖 Archer-decided ownership + 空 M-TEST 分支放行语义（不再固定 Shield/M-TEST 为唯一回归载体），标题与来源同步更新为 Human 裁定 T-003 + flow.md §16.3.2。(3) §5 必须保持的产品约束补 ownership split + 空 M-TEST 分支放行语义。请 Sage 复核。

- **完成结果**：hotfix run 在 M-IMPL 边界结束，`fix/{issue}` 分支保留修复结果，全程可审计；场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号、场景 B 的 merge 回活跃分支作为发布语义载于 flow.md §16.3，本版不执行，随 M-VERIFY/M-RELEASE 注册后生效；操作者在本版看到的是"修复完成、待发布"的边界状态，并可继续观察既有 run 状态与审计输出。

### 3.3. 失败与退出路径

- **变更基线**：新增 — hotfix 没有自己的 M-SPEC/M-AC 可回；非实现偏差与需求缺口的退出路径为本次新增，回退语义按 flow.md §16.3.6 处理。
- **入口/触发**：3.1 入口 HOTFIX-TRIAGE 判定 issue 不是实现偏差（预检 REJECTED / NO_ANCHOR 经 Human 确认转 feature），或 3.2 旅程中出现 ac_gap/spec_gap、设计缺口时开始。

1. 入口 triage 发现 issue 无法锚定为既有 AC 的实现偏差（NO_ANCHOR / 重派超限，含「功能早于规范文档存在」、spec/acc 存在但无法关联两成因）→ awaiting_human：Human 人工锚定则 hotfix 继续，Human 确认转 feature 则退出 hotfix 转 backlog/new feature（产品决定，需 Human）。
2. 旅程中发现需要新行为/新验收（ac_gap/spec_gap）→ 说明该 issue 不是实现偏差 → 退出 hotfix 转 backlog/new feature（产品决定，需 Human）。
3. 设计缺口（Archer 负责的 delta 设计、测试计划或接口缺口）→ 回 hotfix 自己的 M-DESIGN，由 Archer+Prism 裁定，不新增 Human 技术门。

- **完成结果**：操作者能看到退出原因与去向（backlog/new feature 或回到 hotfix 自己的 M-DESIGN）；被判定为新行为的 issue 不再以 hotfix 身份继续，避免把新功能伪装成缺陷修复；回到 M-DESIGN 的缺口在 hotfix run 内部闭环后重新进入 3.2 的旅程。

## 4. 行为种子

### BS-01 hotfix 入口 HOTFIX-TRIAGE 与 run 建立

- EARS: `WHEN 操作者执行 trac hotfix <issue> --scenario post-release|dev, THE 系统 SHALL 执行 HOTFIX-TRIAGE：程序预检 issue 属于宿主 repo 且为 bug 类型（宿主 bug template 的版本/FR/NFR 字段可选、仅辅助），Sage 语义锚定既有 approved Spec/AC 的实现偏差并经程序校验（所引 AC 真实存在于所指版本 acceptance.md，失败重派 ≤3），锚定确认后创建隔离 fix/{issue} 分支、建立 hotfix run 并直接进入 M-DESIGN；无法锚定（NO_ANCHOR 或重派超限）时 SHALL 进入 awaiting_human 由 Human 人工锚定或确认转 feature`
- 来源: [3.1 / 用户明确要求 / Maestro 裁定（2026-08-19）/ flow.md §16.1、16.4]
- 说明: 保护 hotfix 只服务于真正的实现偏差，并让入口成为场景声明的唯一载体；无法锚定时不擅自判为新行为，交 Human 决定。

### BS-02 场景 A 分支基线

- EARS: `WHEN --scenario post-release, THE 系统 SHALL 从 main 分支 checkout fix/{issue} 分支`
- 来源: [3.1 / 用户明确要求（seed 第 2 点）]
- 说明: 场景 A 面向已发布版本、服务全体用户，起点必须是 main。

### BS-03 场景 B 分支基线

- EARS: `WHEN --scenario dev, THE 系统 SHALL 从当前活跃 release 分支 checkout fix/{issue} 分支`
- 来源: [3.1 / 用户明确要求（seed 第 2 点）]
- 说明: 场景 B 面向开发中版本，起点是当前活跃分支；无活跃分支时场景 B 不成立。

### BS-04 需求基线继承（source approval）

- EARS: `WHEN hotfix run 建立, THE 系统 SHALL 继承目标版本已批准的 spec/acceptance/接口与设计基线作为需求基线，且 SHALL NOT 重新创建 M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL`
- 来源: [3.1 / 用户明确要求（seed 第 3 点）/ flow.md §16.1.2]
- 说明: 让修复不必重新批准需求，只针对实现偏差，从 M-DESIGN 开始。

### BS-05 非实现偏差退出

- EARS: `IF 入口 triage 或旅程判定 issue 不是既有 approved 基线的实现偏差（新行为 / ac_gap / spec_gap / NO_ANCHOR 经 Human 确认转 feature——含「功能早于规范文档存在」、spec/acc 存在但无法关联两成因）, THE 系统 SHALL 退出 hotfix 并转 backlog/new feature，作为产品决定请求 Human，而不是以 hotfix 身份继续`
- 来源: [3.1 / 3.3 / Maestro 裁定（2026-08-19）/ flow.md §16.1.1、16.3.6、16.4]
- 说明: 防止把新功能伪装成缺陷修复；无法锚定的成因包括功能早于规范文档存在、spec/acc 存在但 Sage 无法关联——经 Human 确认后按 feature 补齐行为规范。

### BS-06 场景 B 并发串行

- EARS: `WHEN 场景 B hotfix 与活跃 feature run 共享活跃分支, THE 系统 SHALL 串行化两 run 在该分支上的写并保持同一 writer lock，且 SHALL NOT 允许同时运行 feature 与 hotfix 的开发派发；merge 后活跃 run 的 baseline 按既有 stale 检测在下一门禁重新校验`
- 来源: [3.1 / 3.2 / flow.md §16.3.7 / 重要推导]
- 说明: 保护共享分支不被并发写破坏，是单活跃 run 原则的唯一受控例外。

### BS-07 回归测试绑定既有 AC（Archer-decided ownership + 空 M-TEST 分支）

- EARS: `WHEN hotfix 进入 M-TEST, THE 系统 SHALL 以先复现 RED 再变绿的回归用例验证修复（合法 Red = 回归测试在带缺陷基线上的行为断言失败），归属由 Archer 在 delta test-plan 决定--integration/e2e 层归 Shield 在 M-TEST 补写、unit 层归 Devon 在 M-IMPL RED 阶段补写，且 SHALL 将 AC trace 绑定目标版本既有 AC（跨版本引用 AC-FRXXXX-YY@<version>）并 SHALL NOT 为 hotfix 产生新 AC；若 Archer 将回归用例全部划归 unit 层，THE 系统 SHALL 凭空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合放行 M-TEST`
- 来源: [3.2 / Human 裁定（2026-08-19，T-003）/ flow.md §16.3.2 / 重要推导]
- 说明: 回归测试是对既有 AC 的检验补强，不扩展验收面；M-TEST 的 Shield 增量可空、由 unit 层回归用例跨层 trace 闭合，是主路径 M-TEST 步骤的非常规分支（产品不变量），不应仅靠 flow.md 引用兜底。

> **Sage [RESOLVED]:** Reviewer finding（blocker）：BS-07 与 §3.2 第 2 点、§5 未完整反映 Human 裁定（2026-08-19，见 T-003）的 ownership split + 空 M-TEST 分支——这是主路径 M-TEST 步骤的非常规分支（空测试阶段 + 跨层 trace 闭合），属产品不变量，不应仅靠 flow.md §16.3.2 引用兜底。三处核对：(1) §3.2 第 2 点（line 64）仍写'Shield 以复现 issue 的回归测试为主'，固定 Shield/M-TEST 为唯一回归载体，缺 Archer 在 delta test-plan 决定归属、integration/e2e→Shield(M-TEST 补写)/unit→Devon(M-IMPL RED 阶段补写)、以及 M-TEST Shield 增量可空 + Runtime 凭'空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合'放行。(2) BS-07 EARS（line 130）以'WHEN hotfix 进入 M-TEST'触发并固定 Shield/M-TEST 为唯一回归载体，未覆盖空 M-TEST 分支——BS-07 是下游 spec 作者继承的锁定不变量，若固定 Shield/M-TEST 为唯一载体，下游 FR 会与 Human 裁定及 flow.md §16.3.2（line 982）冲突。(3) §5 约束（line 154）未补 ownership split。请 Scribe RESPOND：§3.2 第 2 点、BS-07 改述以反映 Archer-decided ownership + 空 M-TEST 分支放行语义，§5 补约束。依据：Human 裁定 T-003（line 68-74）+ flow.md §16.3.2（line 982，已含完整 ownership split）。
>> **Scribe:** 已按 T-004 finding 完成 RESPOND（与 T-003 同一 finding，三处落文）：(1) §3.2 第 2 点（M-TEST）改述 RED-first + Archer 在 delta test-plan 决定归属（integration/e2e→Shield M-TEST 补写、unit→Devon M-IMPL RED 阶段补写）+ 空 Shield 增量时 Runtime 凭'空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合'放行。(2) BS-07 EARS 改述覆盖 Archer-decided ownership + 空 M-TEST 分支放行语义，不再固定 Shield/M-TEST 为唯一回归载体，来源更新为 Human 裁定 T-003 + flow.md §16.3.2。(3) §5 必须保持的产品约束补 ownership split + 空 M-TEST 分支放行语义。请 Sage 复核并 resolved。
>> **Sage:** 复核通过：三处落文均已正确反映 Human 裁定 T-003 的 ownership split + 空 M-TEST 分支。(1) §3.2 第 2 点（line 64）含 RED-first + Archer 在 delta test-plan 决定归属（integration/e2e→Shield M-TEST 补写、unit→Devon M-IMPL RED 阶段补写）+ 空 Shield 增量时 Runtime 凭'空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合'放行。(2) BS-07 EARS（line 131）覆盖 Archer-decided ownership + 空 M-TEST 分支放行，不再固定 Shield/M-TEST 为唯一载体。(3) §5（line 158）补 ownership split + 空 M-TEST 分支放行约束。finding 已闭合，resolved。

### BS-08 隔离分支实现

- EARS: `WHEN hotfix 处于 M-IMPL, THE 系统 SHALL 只在隔离 fix/{issue} 分支上执行修复实现，且 Runtime 保持唯一 branch/worktree authority`
- 来源: [3.2 / 用户明确要求 / flow.md §16.2]
- 说明: 防止修复与活跃分支或 feature run 串写。

### BS-09 M-IMPL 边界退出与发布语义声明

- EARS: `WHEN hotfix 完成 M-IMPL, THE 系统 SHALL 在 M-IMPL 边界以 boundary 终止，且 SHALL NOT 在本版执行场景 A 的 merge 回 main/同步 merge 活跃 release 分支/patch 版本号或场景 B 的 merge 回活跃分支——该发布语义作为流程设计载于 flow.md §16.3，随 M-VERIFY/M-RELEASE 注册后生效`
- 来源: [3.2 / Maestro 裁定 / flow.md §16.3.4、16.3.5]
- 说明: 如实声明"为全体用户发布"的结果在本版尚未执行，避免把边界当发布。

### BS-10 设计缺口回 hotfix M-DESIGN

- EARS: `IF hotfix 旅程中发现 Archer 负责的设计缺口, THE 系统 SHALL 将其回 hotfix 自己的 M-DESIGN 由 Archer+Prism 裁定，且 SHALL NOT 新增 Human 技术门`
- 来源: [3.3 / flow.md §16.3.6]
- 说明: 设计缺口在 hotfix 内部闭环，不需 Human 技术审批。

## 5. 范围、约束与例外

- **必须保持的产品约束**：入口命令固定为 `trac hotfix <issue> --scenario post-release|dev`，`--scenario` 必填、缺省时询问 Human 而非从 issue 推断。入口先过 HOTFIX-TRIAGE（flow.md §16.4）：宿主 bug issue template 提供「版本 / 对应 FR/NFR」字段，均可选——终端用户不可能知道编号，字段仅作辅助、不完全采信；语义锚定角色为 Sage（需求语义归 Sage，Archer 在 M-DESIGN 承接锚定集合、Prism 复核），锚定输出必须经程序校验（所引 AC 真实存在于所指版本 acceptance.md，引用不实重派 ≤3）；NO_ANCHOR 或重派超限 → awaiting_human，Human 可人工锚定或确认转 feature。hotfix 一律从 M-DESIGN 进入（跳过 M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL），后续流程与 feature release 一致——复用 canonical 阶段序列、全部子状态机、角色、评审协议、重派预算与门禁语义；改动再小也要有 delta 设计与独立评审（v0.6 起废除 quick_rgr 免设计分流）。需求基线继承目标版本已批准的 spec/acceptance/接口与设计基线（source approval，不重新批准）；hotfix 不产生新 AC，AC trace 绑定目标版本既有 AC（跨版本引用 AC-FRXXXX-YY@<version>）。M-TEST 回归用例必须先复现 RED 再变绿（修复前既有用例必然全绿--形式缺失或未触发 RED 条件），归属由 Archer 在 delta test-plan 决定：integration/e2e 层归 Shield 在 M-TEST 补写、unit 层归 Devon 在 M-IMPL RED 阶段补写；若全部划归 unit 层，M-TEST 的 Shield 增量为空，Runtime 凭"空 Shield 增量声明 + trac check trace 由 unit 层回归用例闭合"放行 M-TEST。实现只发生在隔离 `fix/{issue}` 分支，Runtime 是唯一 branch/worktree authority。不允许同时运行 feature 与 hotfix 两个 trac 命令（开发派发串行）；场景 B 是单活跃 run 原则的唯一受控例外。M-IMPL 仍是本版流程边界。
- **非常规要求**：hotfix 直接进入 M-DESIGN、跳过需求阶段并继承已批准基线（source approval）——这是对 feature release 从 M-STORY 进入的常规起点的有意偏离，用户明确要求（seed 第 3 点）。场景 B 允许与活跃 feature run 并存并共享活跃分支（单活跃 run 原则的唯一受控例外），但开发派发必须串行——用户裁定（flow.md §16.3.7 / Aaron）。场景 A 的"为全体用户提供 hotfix"在本版以"修复完成、待发布"的边界状态呈现，不伪称发布已执行——Maestro 裁定。
- **Out-of-Scope**：M-VERIFY/M-RELEASE/M-PUBLISH 的注册与执行，包括场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号/tag/artifact 发布，以及场景 B 的 merge 回活跃分支与 pre-release/开发渠道发布；Human release gate。新行为/新功能需求的完整开发流程（hotfix 只负责识别并转 backlog/new feature，该需求如何走 feature release 不在本 story）。为 hotfix 创建新的 M-SPEC/M-ACC 或新增非既有 trac CLI/CI 交付面（沿用 trac run/status/replay/report/check 观察与继续）。快速修复免设计分流（quick_rgr）分流。

## 6. 开放产品决定

无。入口命令与 `--scenario` 取值、两场景的受众与分支起点、继承基线（source approval）、从 M-DESIGN 进入的旅程与 M-IMPL 边界、以及 merge/发布语义推迟到 M-VERIFY/M-RELEASE 注册后执行，均已由 seed、Maestro 裁定与 flow.md §16.3 确立；入口如何找到对应既有 AC 已由 Human 裁定（2026-08-19）并经 flow.md §16.4（HOTFIX-TRIAGE：bug 预检 + Sage 语义锚定 + 程序校验 + awaiting_human）落定为流程设计，不再构成开放产品决定；hotfix 与活跃 feature run 的并存与串行规则已有 flow.md §16.3.7 与 Aaron 裁定；交付面沿用既有 trac CLI。hotfix run 建立后由既有单一 active run 语义接续（`trac run` 继续当前 run、开发派发串行）为重要推导，不构成开放产品决定；run 状态模型扩展、HOTFIX-TRIAGE 的编程实现细节与 delta 设计格式属后续规格/设计阶段的技术设计，不改变产品结果。

## 7. 必要性与风险

- **既有能力**：feature release 已实现 M-DESIGN→M-TEST→M-IMPL 的 canonical 阶段序列与子状态机、角色、评审协议、重派预算与门禁语义（machine.py/executor）；GitHub Issues effect 边界（v0.2 FR-0200，tracks/effects/github.py）可承载 issue 验证与需求追踪身份；既有 `trac run`/`trac status`/`trac replay`/`trac report`/`trac check` CLI 可继续与观察 hotfix run；flow.md §16 已把 hotfix 变体的适用性、入口 triage（§16.4）、差异清单与并发规则写成流程设计。
- **冲突**：seed 场景 A 的目标"为全体用户提供 hotfix"与宿主停在 M-IMPL→M-VERIFY 边界（M-VERIFY/M-RELEASE 未注册）之间存在范围张力；已按 Maestro 裁定 A 收敛为"本版实现到 M-IMPL 边界，发布语义作为流程设计声明、执行随 M-VERIFY/M-RELEASE 注册后生效"。此外，hotfix 允许在活跃 feature run 期间创建（场景 A 不被 CHECK_ACTIVE 阻塞）与 `trac start` 的"活跃 run 时新需求入 backlog"行为不同，属 flow.md §16.3.7 已裁定的有意偏离。
- **重要风险**：若入口 HOTFIX-TRIAGE 无法可靠定位目标版本已批准基线（issue 元数据缺失、目标版本三件套未批准），hotfix run 无法建立，场景 A/B 均不可达——实现必须保留"补全后重试"路径。若 Sage 无法锚定既有 AC（spec/acc 存在但无法关联，或功能早于规范文档存在、历史版本无规范文档可引），入口进入 awaiting_human 由 Human 人工锚定或确认转 feature——实现必须保留该人工路径，否则历史遗留缺陷的修复不可达；Human 确认转 feature 后须能完整退出 hotfix 转入 backlog/new feature，不残留半建 run。场景 B 与活跃 feature run 共享活跃分支，若写串行/同一 writer lock 与 stale reconcile 未正确实现，两 run 会在共享分支上互相破坏——本版到 M-IMPL 边界为止虽不执行 merge，但场景 B 的 fix 分支基线随活跃分支推进会 stale（冲突 → needs_attention），必须保留 reconcile 与冲突路径，否则场景 B 不可达。hotfix 与既有 feature run 并存时，若 run 选择/续跑语义让操作者无法分辨当前 `trac run` 继续的是哪个 run、或使挂起 run 的门禁不可恢复，会破坏"修复完成、待发布"与后续恢复的可见性——本版按既有单一 active run 语义接续（重要推导），实现须保证挂起 run 可恢复观察。

> **Sage [RESOLVED]:** §7 既有能力裸引 'FR-0200, tracks/effects/github.py' 作为 GitHub Issues effect 边界。核对宿主事实：github.py:1 docstring 确标注 'FR-0200'（v0.2 时期标签，对应 v0.2 spec.md:507 'FR-0200 spec -> GitHub Issues 拆分'），但 v0.5 spec.md:447 已把 FR-0200 重新分配为'可休眠与崩溃恢复'（crash recovery, owner=executor/executor.py + executor/rgr.py, AC-FR0200-01/02），且 v0.5 spec.md:480 自身引用 GitHub Issues 时显式写作 'v0.2 FR-0200' 以区分。本处裸用 'FR-0200' 在 v0.6 上下文有歧义：下游 Sage 起草 spec 时若继承 'FR-0200 = GitHub Issues' 会与紧邻基线 v0.5 的 FR-0200（crash recovery）冲突，复刻 v0.5 已纠正过的 NFR-0040 跨版本 ID 复用问题（v0.5 spec.md:593 Lex blocker）。建议改为 'v0.2 FR-0200' 或仅引模块路径 'tracks/effects/github.py'（capability claim 可核验，无需 FR 号）。非 blocker--不改变产品结果，仅为下游引用精度。
>> **Sage:** resolution: non-blocking precision note. Story meets reviewer pass criteria; resolving to unblock M-SPEC. Scribe may revise FR-0200 to v0.2 FR-0200 or module path only.
>> **Scribe:** 已采纳：§7 既有能力改为 'v0.2 FR-0200，tracks/effects/github.py'，与 v0.5 spec.md 对 GitHub Issues 的引用口径一致，避免下游 spec 继承歧义。
