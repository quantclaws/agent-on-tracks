---
story_id: S-001
title: hotfix 工作流：trac hotfix 入口与继承基线的 M-DESIGN→M-IMPL 旅程
created: 2026-08-18
status: draft
sha:
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
- **完成后能看到什么结果**：操作者执行 `trac hotfix <issue> --scenario post-release|dev`，看到 Runtime 验证 issue、按场景创建隔离 `fix/{issue}` 分支、继承目标版本已批准基线并直接进入 M-DESIGN；随后通过既有 `trac run`/`trac status`/`trac replay` 看到 delta 设计、回归测试与修复实现依次完成并到达 M-IMPL 边界，`fix/{issue}` 分支保留修复结果。两场景的 merge/发布语义（场景 A merge 回 main + 同步 merge 活跃 release 分支 + patch 版本；场景 B merge 回活跃分支）按裁定作为流程设计载于 flow.md §16.3，其执行随 M-VERIFY/M-RELEASE 阶段注册后生效，本版如实声明场景 A 的"全体用户"结果尚未执行。

## 3. 核心操作路径

### 3.1. trac hotfix 入口：验证、隔离分支与 run 建立

- **变更基线**：新增 — 当前 CLI 没有 hotfix 命令，缺陷修复只能作为新 feature 从 `trac start <version>` 进入 M-START→M-STORY，且存在活跃 run 时新需求进入 backlog。本次新增顶层命令 `trac hotfix <issue> --scenario post-release|dev`，建立独立的 hotfix run（自带项目目录），跳过需求阶段直接进入 M-DESIGN；场景 A 不被活跃 run 阻塞，场景 B 为单活跃 run 原则的唯一受控例外。
- **入口/触发**：操作者在宿主 repo 工作区执行 `trac hotfix <issue> --scenario post-release|dev`；`--scenario` 必填（post-release = 场景 A 已发布版本，dev = 场景 B 开发中版本），缺省时 Runtime 询问 Human，不从 issue 推断。

1. Runtime 验证：`<issue>` 是宿主 repo 的 GitHub issue 号且可定位；issue 能定位目标版本（issue 元数据/指认的版本与既有 approved Spec/AC 引用，缺失则要求补全后重试）；判定该 issue 是目标版本相对既有 approved 基线的实现偏差（入口锚定）；`--scenario dev` 还需存在当前活跃 release 分支。
2. Runtime 按场景创建隔离 `fix/{issue}` 分支并建立 hotfix run：场景 A 从 main checkout，场景 B 从当前活跃 release 分支 checkout；需求基线继承目标版本已批准的 spec/acceptance/接口与设计基线（source approval，不重新批准）；不创建 M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL。
3. 直接 `stage.entered(M-DESIGN)`；操作者通过 `trac status`/`trac replay` 看到 hotfix run、`fix/{issue}` 分支与 M-DESIGN 入口。hotfix run 与既有 feature run 的并存按 flow.md §16.3.7 处理：允许两个并发分支，但开发派发保持串行（同一 writer lock），不允许同时运行 feature 与 hotfix 两个 trac 命令（Aaron 裁定）。

- **完成结果**：验证通过则 hotfix run 建立并可继续（走 3.2）；验证失败（issue 不可定位、非实现偏差/新行为、场景不合法）时不建立 run，操作者看到明确原因与下一步——补全 issue 信息后重试，或退出 hotfix 转 backlog/new feature（产品决定，需 Human）。

### 3.2. 继承基线的 M-DESIGN→M-IMPL 完整可审计旅程

- **变更基线**：修改/扩展 — 现有 feature release 从 M-STORY 进入、经 M-SPEC/M-ACC/M-REQ-APPROVAL 后在 M-IMPL 边界退出（M-VERIFY/M-RELEASE 未注册）。本次 hotfix 从 M-DESIGN 进入、跳过需求阶段，各阶段语义按 flow.md §16.2/16.3 收窄（delta 设计、回归测试、影响切片），边界仍为 M-IMPL；两场景的 merge/发布语义本版不执行（Maestro 裁定 A）。
- **入口/触发**：hotfix run 建立后（3.1），操作者通过既有 `trac run` 继续派发；Runtime 复用 canonical 阶段序列与全部子状态机、角色、评审协议、重派预算与门禁语义推进。

1. **M-DESIGN**：Archer 产出相对继承基线的 delta 设计（architecture/interfaces/test-plan 三文档的修订增量，产出物归 hotfix run 自己的项目目录），锚定所偏离的 FR/AC（引用目标版本 spec/acceptance 的既有条目，跨版本引用 `AC-FRXXXX-YY@<version>`）；Prism 复核锚定与 delta 设计；contracts 沿用目标版本的 machine contracts（除非修复本身改变合同）。
2. **M-TEST**：Shield 以复现 issue 的回归测试为主、按影响面收窄；AC trace 绑定目标版本的既有 AC，hotfix 不产生新 AC；合法 Red = 回归测试在带缺陷基线上的行为断言失败。操作者通过 `trac status`/`trac replay` 看到测试资产与回归证据。
3. **M-IMPL**：task graph 按影响切片（通常远小于 feature release），scope 白名单沿用目标版本 layout；实现只发生在隔离 `fix/{issue}` 分支（Runtime 是唯一 branch/worktree authority）；场景 B 的基线随活跃分支推进会 stale：merge 前须对活跃分支当前 HEAD reconcile，冲突 → `needs_attention`。Devon 在 `fix/{issue}` 分支上完成 RGR 与 review，修复提交落在该分支。
4. 到达 M-IMPL 边界后以 boundary 终止（`run.completed` terminal_state=boundary）；操作者通过 `trac status`/`trac replay` 看到边界结果与 `fix/{issue}` 分支上的修复提交。

- **完成结果**：hotfix run 在 M-IMPL 边界结束，`fix/{issue}` 分支保留修复结果，全程可审计；场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号、场景 B 的 merge 回活跃分支作为发布语义载于 flow.md §16.3，本版不执行，随 M-VERIFY/M-RELEASE 注册后生效；操作者在本版看到的是"修复完成、待发布"的边界状态，并可继续观察既有 run 状态与审计输出。

### 3.3. 失败与退出路径

- **变更基线**：新增 — hotfix 没有自己的 M-SPEC/M-AC 可回；非实现偏差与需求缺口的退出路径为本次新增，回退语义按 flow.md §16.3.6 处理。
- **入口/触发**：3.1 入口验证判定 issue 不是实现偏差，或 3.2 旅程中出现 ac_gap/spec_gap、设计缺口时开始。

1. 入口验证发现 issue 是新行为（找不到可锚定的既有 AC、实现偏差不成立）→ 不建立 hotfix run，退出 hotfix 转 backlog/new feature（产品决定，需 Human）。
2. 旅程中发现需要新行为/新验收（ac_gap/spec_gap）→ 说明该 issue 不是实现偏差 → 退出 hotfix 转 backlog/new feature（产品决定，需 Human）。
3. 设计缺口（Archer 负责的 delta 设计、测试计划或接口缺口）→ 回 hotfix 自己的 M-DESIGN，由 Archer+Prism 裁定，不新增 Human 技术门。

- **完成结果**：操作者能看到退出原因与去向（backlog/new feature 或回到 hotfix 自己的 M-DESIGN）；被判定为新行为的 issue 不再以 hotfix 身份继续，避免把新功能伪装成缺陷修复；回到 M-DESIGN 的缺口在 hotfix run 内部闭环后重新进入 3.2 的旅程。

## 4. 行为种子

### BS-01 hotfix 入口验证与 run 建立

- EARS: `WHEN 操作者执行 trac hotfix <issue> --scenario post-release|dev, THE 系统 SHALL 验证 issue 属于宿主 repo、可定位目标版本且为既有 approved Spec/AC 的实现偏差，验证通过后创建隔离 fix/{issue} 分支、建立 hotfix run 并直接进入 M-DESIGN`
- 来源: [3.1 / 用户明确要求 / Maestro 裁定 / flow.md §16.1]
- 说明: 保护 hotfix 只服务于真正的实现偏差，并让入口成为场景声明的唯一载体。

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

- EARS: `IF 入口验证或旅程判定 issue 不是既有 approved 基线的实现偏差（新行为/ac_gap/spec_gap）, THE 系统 SHALL 退出 hotfix 并转 backlog/new feature，作为产品决定请求 Human，而不是以 hotfix 身份继续`
- 来源: [3.1 / 3.3 / flow.md §16.1.1、16.3.6]
- 说明: 防止把新功能伪装成缺陷修复；退出去向属产品决定，需 Human。

### BS-06 场景 B 并发串行

- EARS: `WHEN 场景 B hotfix 与活跃 feature run 共享活跃分支, THE 系统 SHALL 串行化两 run 在该分支上的写并保持同一 writer lock，且 SHALL NOT 允许同时运行 feature 与 hotfix 的开发派发；merge 后活跃 run 的 baseline 按既有 stale 检测在下一门禁重新校验`
- 来源: [3.1 / 3.2 / flow.md §16.3.7 / 重要推导]
- 说明: 保护共享分支不被并发写破坏，是单活跃 run 原则的唯一受控例外。

### BS-07 回归测试绑定既有 AC

- EARS: `WHEN hotfix 进入 M-TEST, THE 系统 SHALL 以复现 issue 的回归测试为主并将 AC trace 绑定目标版本既有 AC（跨版本引用 AC-FRXXXX-YY@<version>），且 SHALL NOT 为 hotfix 产生新 AC；合法 Red = 回归测试在带缺陷基线上的行为断言失败`
- 来源: [3.2 / flow.md §16.3.2 / 重要推导]
- 说明: 回归测试是对既有 AC 的检验补强，不扩展验收面。

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

- **必须保持的产品约束**：入口命令固定为 `trac hotfix <issue> --scenario post-release|dev`，`--scenario` 必填、缺省时询问 Human 而非从 issue 推断。hotfix 一律从 M-DESIGN 进入（跳过 M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL），后续流程与 feature release 一致——复用 canonical 阶段序列、全部子状态机、角色、评审协议、重派预算与门禁语义；改动再小也要有 delta 设计与独立评审（v0.6 起废除 quick_rgr 免设计分流）。需求基线继承目标版本已批准的 spec/acceptance/接口与设计基线（source approval，不重新批准）；hotfix 不产生新 AC，AC trace 绑定目标版本既有 AC。实现只发生在隔离 `fix/{issue}` 分支，Runtime 是唯一 branch/worktree authority。不允许同时运行 feature 与 hotfix 两个 trac 命令（开发派发串行）；场景 B 是单活跃 run 原则的唯一受控例外。M-IMPL 仍是本版流程边界。
- **非常规要求**：hotfix 直接进入 M-DESIGN、跳过需求阶段并继承已批准基线（source approval）——这是对 feature release 从 M-STORY 进入的常规起点的有意偏离，用户明确要求（seed 第 3 点）。场景 B 允许与活跃 feature run 并存并共享活跃分支（单活跃 run 原则的唯一受控例外），但开发派发必须串行——用户裁定（flow.md §16.3.7 / Aaron）。场景 A 的"为全体用户提供 hotfix"在本版以"修复完成、待发布"的边界状态呈现，不伪称发布已执行——Maestro 裁定。
- **Out-of-Scope**：M-VERIFY/M-RELEASE/M-PUBLISH 的注册与执行，包括场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号/tag/artifact 发布，以及场景 B 的 merge 回活跃分支与 pre-release/开发渠道发布；Human release gate。新行为/新功能需求的完整开发流程（hotfix 只负责识别并转 backlog/new feature，该需求如何走 feature release 不在本 story）。为 hotfix 创建新的 M-SPEC/M-ACC 或新增非既有 trac CLI/CI 交付面（沿用 trac run/status/replay/report/check 观察与继续）。快速修复免设计分流（quick_rgr）分流。

## 6. 开放产品决定

无。入口命令与 `--scenario` 取值、两场景的受众与分支起点、继承基线（source approval）、从 M-DESIGN 进入的旅程与 M-IMPL 边界、以及 merge/发布语义推迟到 M-VERIFY/M-RELEASE 注册后执行，均已由 seed、Maestro 裁定与 flow.md §16.3 确立；hotfix 与活跃 feature run 的并存与串行规则已有 flow.md §16.3.7 与 Aaron 裁定；交付面沿用既有 trac CLI。hotfix run 建立后由既有单一 active run 语义接续（`trac run` 继续当前 run、开发派发串行）为重要推导，不构成开放产品决定；run 状态模型扩展、入口验证的具体检查项与 delta 设计格式属后续规格/设计阶段的技术设计，不改变产品结果。

## 7. 必要性与风险

- **既有能力**：feature release 已实现 M-DESIGN→M-TEST→M-IMPL 的 canonical 阶段序列与子状态机、角色、评审协议、重派预算与门禁语义（machine.py/executor）；GitHub Issues effect 边界（FR-0200，tracks/effects/github.py）可承载 issue 验证与需求追踪身份；既有 `trac run`/`trac status`/`trac replay`/`trac report`/`trac check` CLI 可继续与观察 hotfix run；flow.md §16 已把 hotfix 变体的适用性、入口、差异清单与并发规则写成流程设计。
- **冲突**：seed 场景 A 的目标"为全体用户提供 hotfix"与宿主停在 M-IMPL→M-VERIFY 边界（M-VERIFY/M-RELEASE 未注册）之间存在范围张力；已按 Maestro 裁定 A 收敛为"本版实现到 M-IMPL 边界，发布语义作为流程设计声明、执行随 M-VERIFY/M-RELEASE 注册后生效"。此外，hotfix 允许在活跃 feature run 期间创建（场景 A 不被 CHECK_ACTIVE 阻塞）与 `trac start` 的"活跃 run 时新需求入 backlog"行为不同，属 flow.md §16.3.7 已裁定的有意偏离。
- **重要风险**：若入口验证无法可靠定位目标版本已批准基线（issue 元数据缺失、目标版本三件套未批准），hotfix run 无法建立，场景 A/B 均不可达——实现必须保留"补全后重试"路径。场景 B 与活跃 feature run 共享活跃分支，若写串行/同一 writer lock 与 stale reconcile 未正确实现，两 run 会在共享分支上互相破坏——本版到 M-IMPL 边界为止虽不执行 merge，但场景 B 的 fix 分支基线随活跃分支推进会 stale（冲突 → needs_attention），必须保留 reconcile 与冲突路径，否则场景 B 不可达。hotfix 与既有 feature run 并存时，若 run 选择/续跑语义让操作者无法分辨当前 `trac run` 继续的是哪个 run、或使挂起 run 的门禁不可恢复，会破坏"修复完成、待发布"与后续恢复的可见性——本版按既有单一 active run 语义接续（重要推导），实现须保证挂起 run 可恢复观察。
