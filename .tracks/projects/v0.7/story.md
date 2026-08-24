---
story_id: S-001
title: {一句话标题}
created: 2026-08-24
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

> v0.7-A：可信测试证据与一致质量基线。
>
> 目标：Human 只提供产品意图和需求评审；Agent 自主设计、编写测试与实现。tracks 必须让每条 approved AC 的测试真实性由机器执行证据背书，避免为了满足门禁而制造无关 Red 或宽泛 mutation 的 Goodhart 行为；同一机制必须随 tracks 部署到宿主项目，而不是 tracks 仓库专用补丁。
>
> 依赖顺序：
> 1. Phase 0 先恢复可信绿色基线：补齐 v0.6 三个缺失 AC 的真实 collected-node 绑定；真实覆盖率达到合同阈值且不靠排除作弊；消除或经设计显式修订全部质量违规后使其硬门；注册测试 marks；修复本地与 CI 的环境/分发契约；封存 v0.6 文档。
> 2. 建立单一 canonical quality guard registry。Archer 为宿主选择完整八类守卫并声明 pinned tool、config digest、scope、threshold、timeout/failure policy、Runtime/pre-commit/CI 执行点和 required check；三处必须引用同一合同或由其生成。Runtime 对 parity 做 fail-closed 校验，Prism design criteria 将缺失、--exit-zero、scope/threshold/命令不一致判 REVISE。
> 3. 实现 Test Authenticity Gate。取消“所有测试在当前树一律强凑 Red”：新行为必须在冻结的 pre-implementation baseline 上产生 AC/IF-specific legal Red；已存在行为或回归测试允许未变异 candidate 为绿，但必须杀死 AC-specific counterexample。无关下游失败不得作为该 AC 的 Red。
> 4. Shield 编写 integration/e2e 测试与反例资产，Prism 独立审查反例语义和最小性，Devon 不得修改冻结测试；Runtime 不信 Agent 自报，必须确定性执行实验。
> 5. 定义语言无关 mutation evidence protocol：manifest 只含 protocol version、AC/IF、candidate/patch digest、opaque target/control node IDs、runner identity、允许变更范围及规范化预期结果。Runtime 在隔离 worktree 验证 baseline、git apply、实际 diff identity、target kill、controls remain green、rollback clean，并将 command/env/node/result/failure signature/digests 写入 append-only events。禁止修改 tests 的 mutation，禁止一个宽泛 no-op patch 为不相同合同事实批量背书。
> 6. 保持 kernel 语言中性：宿主 adapter 合同提供 collect、run_selected 和 normalized result；Runtime 只消费版本化 tracks-test-result 协议。当前 pytest/JUnit 逻辑下沉为第一个 reference adapter；未知 adapter fail-closed，不穷举语言。
> 7. executable trace 必须形成 approved AC -> test-plan/public outlet -> 实际 collected node -> candidate-bound baseline/mutation evidence -> 同 candidate FULL pass 的闭环；node 缺失、skip/xfail、身份漂移或控制组失败均阻止退出。
>
> 验收必须覆盖 tracks 自身和至少一个全新宿主 demo：证明 broad mutation、stale patch、错误 candidate、未收集 node、无关下游 Red、target 未死、control 被误伤、adapter/result 畸形、质量守卫三点不一致都会 fail closed；正确实验可崩溃恢复且事件可 replay/report 审计。
>
> 本切片不注册 M-VERIFY，不做 CI SHA 回读、制品构建或发布自动化；这些属于通过本切片可信证据后才启动的 v0.7-B。

> **Scribe:** TRIAGE blocker（范围/交付形态）：seed 验收要求覆盖 'tracks 自身 + 至少一个全新宿主 demo'，并证明 9 类 fail-closed 场景，但 demo 宿主身份与语言未定。方向 A) demo = tracks 仓内最小全新宿主 fixture（小型 Python 项目），复用第一个 reference adapter（pytest/JUnit 逻辑），不新增适配器——范围最小，只证明机制若内核语言中性可复用于新宿主；方向 B) demo = 不同语言宿主（如 Java/JUnit 真实项目），v0.7-A 内新增第二个 reference adapter，以实证语言中立协议本身——范围显著扩大，但正面证明协议跨语言；方向 C) 形态委托 Archer 在 M-DESIGN 裁定（story 只锁定 'demo 必须可正向演 fail-closed' + '未知 adapter fail-closed'），story 不固定 demo 语言。默认交付面推断为：宿主 repo 内既有 trac CLI/machine contract 接口 + 部署后的 pre-commit/CI 守卫配置（seed 要求机制随 tracks 部署而非仓内补丁——推定为可推导，不需另行询问）。推荐 A：与 seed 6「当前 pytest/JUnit 逻辑下沉为第一个 reference adapter」呼应，且把语言实证留给后续 slice，避免 v0.7-A 范围进一步膨胀；若用户意在实证语言抽取则选 B。另注（非 blocker）：『v0.6 三个缺失 AC』身份在 v0.6 文档中未见显式枚举（v0.6-goal-assessment.md 已缺失、仅存 .lock），Phase 0 的『补齐』目标建议在 story 中表述为『按 collected-node 绑定的 trace 缺口现场取回』而非预设清单。
>> **Maestro:** 裁定 A：v0.7-A 的 demo 是 tracks 测试资产内动态创建的全新最小 Python/pytest 宿主 repo，使用首个 pytest reference adapter；它必须走与真实宿主相同的安装/contract/Runtime 路径，不得调用 tracks 私有测试捷径。第二语言 adapter 与“跨两种语言的实证”不在本切片范围；本切片以 opaque node/result protocol、未知 adapter fail-closed 和无语言语义进入 kernel 证明可扩展边界。Phase 0 三个精确缺口为 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02，必须绑定真实 collected node 与执行证据，不能仅补 marker。

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
