---
story_id: S-001
title: {一句话标题}
created: 2026-08-18
status: draft
sha:
---

# S-001: {一句话标题}

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留内容）：
  - §1 逐字记录 Human 原始输入，不修改、不转述。
  - §3 每条操作路径复制 3.1 的小节结构（3.2、3.3……）。修改类路径的「变更基线」必须写清
    当前行为与本次变更——下游（Sage/Archer/Shield/Devon）基于此描述变更，而非从零设计；
    新增路径写“无（新增路径）”。
  - §4 只提取需要下游继续展开的用户结果和重要边界，不枚举普通微交互；按路径顺序统一编号
    BS-01、BS-02……（不按路径分组）。
  - BS 编号文法（FR-0130）：`### BS-XX`（两位补零，按核心操作路径顺序统一编号，不按路径分组）。
    ID 一经分配不可变、不可复用；删除的 BS 留 tombstone。
  - 跨版本引用（FR-0130）：本版本内引用保持短格式；跨版本引用且存在歧义时附加版本限定
    `AC-FRXXXX-YY@<version>`（如 `AC-FR0010-01@v0.1`）。限定语法 opt-in，parser 不强制。
  - §5 各项没有时写“无”；Out-of-Scope 只记录明确排除或为防止明显范围扩张而必须记录的事项。
  - §6 每个问题一个段落，粗体一句话开头（格式见正文占位）；不编号、机器不校验。只写无法
    可靠推导、且不同答案会显著改变价值/范围/权限/数据安全/合规或产生不可逆后果的产品问题；
    技术选择不写。没有则整节写“无”。
  - §7 重要风险只写会改变范围或使 story 不成立的。

  不得使用超过3行以上的表格；可改用列表。
-->

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

> **Scribe:** TRIAGE blocker（实现边界）：seed 说"hotfix 流程从 M-DESIGN 开始，后续流程与 feature release 一致"，但宿主当前实现停在 M-IMPL→M-VERIFY 边界（v0.5 裁定；spec-v06-consolidation.md 记录 M-IMPL 后无注册阶段，M-VERIFY/M-RELEASE 计划为 v0.6 末尾独立最小 story）。而 seed 场景 A 的目标是"为全体用户提供 hotfix"，flow.md §16.3.5 把 merge 回 main、同步 merge 活跃 release 分支与 patch 发布放在 M-RELEASE/M-PUBLISH。请问本 story 的实现边界在哪里？方向 A) 与当前 feature release 一致，实现到 M-IMPL 边界为止；merge/发布语义作为流程设计（flow.md §16.3）记录并在 M-VERIFY/M-RELEASE 注册时另行落地，story 如实声明场景 A 的"全体用户"结果在本版尚未执行。方向 B) 本 story 包含最小发布侧实现：场景 A 的 merge 回 main + 同步 merge 活跃 release 分支 + patch 版本号，场景 B 的 merge 回活跃分支，使两场景可见结果在本版即可执行。方向 C) 完整实现 hotfix 全链（含 M-VERIFY/M-RELEASE/M-PUBLISH）。不同答案显著改变 v0.6 范围与验收路径。推荐 A：与"后续流程与 feature release 一致"及 v0.6 集中整治定位最贴合，且 M-VERIFY/M-RELEASE 已有独立最小 story 计划；merge 语义在 flow.md 已确立为流程设计，不阻塞 DRAFT。
>> **Maestro:** 裁定 A。依据：1) 用户已裁定 v0.6 不含 spec-v06-consolidation.md 的内容（其中多数已在 v0.5 实现或另有 GitHub issue 跟踪，M-VERIFY/M-RELEASE 的注册属后续独立工作）；2) seed 要求'后续流程与 feature release 一致'，而当前 feature release 的实现边界就是 M-IMPL→M-VERIFY 边界。请 story 如实声明：两场景的 merge/发布语义（场景 A merge 回 main + 同步 merge 活跃 release 分支 + patch 版本；场景 B merge 回活跃分支）已作为流程设计载于 wiki/flow.md §16.3，其执行随 M-VERIFY/M-RELEASE 阶段注册后生效；本版 hotfix 交付入口（trac hotfix <issue> --scenario post-release|dev）+ fix/{issue} 隔离分支 + 继承基线进入 M-DESIGN→M-IMPL 到边界为止的完整可审计旅程。

## 2. 用户意图

- {用户想完成什么}
- {当前哪里受阻}
- {完成后能看到什么结果}

## 3. 核心操作路径

### 3.1. {路径名}

- **变更基线**：[新增 / 替换 / 修改 / 重命名] — {此路径在当前版本的行为与实现；新增路径写“无（新增路径）”}
- **入口/触发**：{用户从现有哪个任务、对象或公开入口，如何进入本能力}

1. {改变用户任务状态的关键步骤 1}
2. {关键步骤 ...}
3. {用户在何处看到可验证的完成结果}

- **完成结果**：{可观察的完成状态；完成、取消或可恢复失败后，用户接下来能做什么}

## 4. 行为种子

### BS-01 {行为种子标题}

- EARS: `WHEN/IF/WHILE/WHERE {条件}, THE 系统 SHALL {用户可观察行为}`
- 来源: [{路径编号} / 约束 / 非常规要求 / 重要推导]
- 说明: {这项行为保护什么用户结果}

## 5. 范围、约束与例外

- **必须保持的产品约束**：{用户明确要求或既有合同中不能改变的约束；无则写“无”}
- **非常规要求**：{有意偏离宿主项目惯例、安全或可用性默认的要求及理由；无则写“无”}
- **Out-of-Scope**：{明确排除的事项；无则写“无”}

## 6. 开放产品决定

**{问题一句话}** — {不同答案会改变什么产品结果}。方向：A) {…} B) {…}。推荐 {A}，因为 {理由}。

## 7. 必要性与风险

- **既有能力**：{可复用或相关的现有功能/合同；无则写“未发现直接覆盖”}
- **冲突**：{与既有产品方向的实质冲突；无则写“无”}
- **重要风险**：{只写会改变范围或使 story 不成立的风险；无则写“无阻塞风险”}
