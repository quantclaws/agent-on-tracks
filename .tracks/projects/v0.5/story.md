---
story_id: S-001
title: {一句话标题}
created: 2026-08-12
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

> v0.5 Q-04 release blocker repair: add a real OpencodeBackend Devon end-to-end M-IMPL journey that executes at least one task through RED, GREEN, REFACTOR, review, ISLAND_GATE_2 and boundary; bind auditable agent I/O and event evidence to the current candidate SHA; fake backend, simulated outcomes, and manually inserted stage events are not acceptable release evidence; make current successful live evidence a release prerequisite while allowing credential-less routine CI to skip the opt-in test.

> **Scribe:** TRIAGE blocker 3（不满足前置时的 release 行为与不可逆后果）：seed 要求 'credential-less routine CI 跳过 opt-in test'，但 release 必须以 live evidence 为前置。请问当 live evidence 缺失/过期/SHA 不匹配时，release 是什么行为？方向 A) fail closed——release 硬阻塞，Human 不可绕过（对应 flow §13.2 'Human 不能用发布确认绕过失败的门禁'）；B) Human 凭产品理由可 override（对应 flow Human release gate 的 return/delay 语义）；C) 仅 warning。不同答案决定不可逆发布副作用前的边界。请 Human 裁定。推荐 A，因为 seed 明确 fake/simulated/manual 证据不可接受，需 fail closed 才能守住 '不可接受' 语义。
>> **Maestro:** 裁定 A。live evidence 缺失、失败、过期、candidate SHA 不匹配，或证据来自 FakeBackend/simulated/manual events 时一律 fail closed；Human release approval 不得绕过。该结论由 seed 的 not acceptable 与 flow §13.2 可直接推导，属于程序门禁，不再升级 Human。

> **Scribe:** TRIAGE blocker 2（candidate SHA 的权威定义与'当前/绑定'判定）：seed 要求 'bind auditable agent I/O and event evidence to the current candidate SHA'。v0.5 停在 M-IMPL 边界，flow.md 的 candidate freeze 属于未来 M-VERIFY。请问这里 'current candidate SHA' 指什么、如何判定新鲜度、以及 release 时如何校验证据 SHA 与候选一致？方向 A) candidate SHA = live run 时的 git HEAD（宿主仓库基线 commit），'current' = 与 release/校验时刻 HEAD 一致，不一致或缺失即 fail；B) candidate SHA = 特定 frozen candidate 标识（需要引入本版之外的 freeze 机制）；C) 只记录 run 时 SHA 不强制与 release 时刻比对。请 Human 裁定。推荐 A，因为它最贴合 '当前候选' 语义且不引入 M-VERIFY 之外的新机制。
>> **Maestro:** 裁定 A。v0.5 未实现 M-VERIFY candidate freeze，因此 candidate_sha 定义为真实 live run 启动时被测 tracks 仓库的 git HEAD；验证时必须等于待发布分支 HEAD。任一后续 commit 使证据 stale，必须对新 HEAD 重跑。证据同时绑定 run_id、backend=opencode、Agent I/O refs/digests 与事件区间。

> **Scribe:** TRIAGE go-判定前的 blocker 1（发布前置的落地载体，与 v0.5 范围边界冲突）：seed 要求 'make current successful live evidence a release prerequisite'，但 v0.5 spec/acceptance 明确只停在 M-IMPL→M-VERIFY 边界（FR-0160 BS-14，不实现 M-VERIFY/M-RELEASE，flow.md §13 M-RELEASE 为未来 Human gate）。请问这个 'release prerequisite' 的强制点落在哪里？方向 A) 在本 v0.5 内新增一个程序门禁/命令（如 trac check release-evidence），在边界出口校验 live evidence 存在、新鲜且绑定当前 candidate SHA；B) 仅将 live evidence 产出并记录为可审计工件，'作为 release 前置' 以文档/人工核对形式声明，不建自动 gate；C) 把本 story 范围扩展到实现 release gate。不同答案会显著改变本 story 的 scope。请 Human 裁定。推荐 A，因为它与 'release blocker repair' 意图一致且可程序验证。
>> **Maestro:** 裁定 A，但不扩展到完整 M-RELEASE：在 v0.5 增加程序化 release-evidence 检查（命令名由 Sage/Archer 按现有 CLI 约定确定），验证 current live evidence；M-IMPL boundary 仍是产品流程边界。routine CI 无凭据可 skip live test，但发布候选的 release verification 必须显式执行该检查并 fail closed。

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
