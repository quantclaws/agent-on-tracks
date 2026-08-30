---
story_id: S-001
title: {一句话标题}
created: 2026-08-30
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

> v0.8：可信发布闭环与流程合同收敛。
>
> 目标：在 v0.7 已建立可信测试证据的基础上，让 feature 与 hotfix 从 M-IMPL 后继续走完 M-VERIFY、M-SECURITY、M-RELEASE、M-PUBLISH、M-MILESTONE，所有门禁、外部副作用和归档都绑定同一 candidate，可崩溃恢复、可审计、fail-closed。Human 只提供产品意图、需求评审与最终发布授权；Agent 自主完成需求分析、设计、测试与实现。
>
> 已编号裁定与范围：
> 1. 自举 Runtime 安全边界不是宿主产品需求，不进入 v0.8 spec；tracks 开发自身的规避方式属于仓库开发基础设施，不发布最终用户永远不用的代码。
> 2. 正式注册 M-VERIFY：冻结 candidate；未漂移时复用同 identity 的干净 FULL_F，漂移/stale 才重跑；运行宿主合同声明的本地质量、trace/reach/反 slop、版本、build/artifact 与安装后公开出口验证；GitHub required CI 必须由 API 回读并绑定 repo/workflow/run/head SHA；Prism 对同一 candidate 做最终一致性复审。Runtime 不硬编码宿主语言、测试框架、包管理器或构建工具。
> 3. 发布闭环：实现 M-SECURITY、M-RELEASE、M-PUBLISH、M-MILESTONE。安全扫描及深度审计由版本化宿主合同/policy 声明；未知或畸形结果 fail-closed。M-RELEASE 生成绑定 candidate、artifact、风险、计划与副作用的 preview，只有 Human 可以 release/delay/return，且不能绕过失败门禁。M-PUBLISH 以 write-ahead + idempotency + reconcile 执行 merge/tag/artifact/release/deploy 等宿主声明的外部操作；Agent 不执行或模拟不可逆副作用。M-MILESTONE 完成需求到 release 的 trace 闭环、Issue/Project 生命周期、证据归档、只读封存和临时 refs 清理。发布已成功而归档失败时只重试收尾，不重复发布。
> 4. Feature 与 hotfix 都必须真正发布。Feature 完成规范发布旅程；post-release hotfix 从 fix 分支验证后合入 main，并在存在活跃 release 分支时同步修复，产生 patch 发布；dev hotfix 只合入活跃 release 分支，沿既有 pre-release/开发渠道交付，不冒充公开版本。两种 hotfix 都保留 M-VERIFY、安全 policy 和 Human release gate。
> 5. 单次权威测试的核心已经实现：WRITE→COLLECT→Runtime RED_CHECK→PRISM_REVIEW，Prism 消费 red.validated 且只做隔离 counterexample kill。v0.8 只做回归核验；不得以本项重新发明测试流水线。Shield 的生产者自检不是权威门禁，不得退化为普通 FULL 套件重跑。
> 6. 完整收敛结构化 Agent 合同：所有角色与 assignment kind 的输入 assignment/evidence、输出 outcome/verdict 使用统一、版本化、可判别的 envelope 与 schema；只有一个确定性解析路径，不再依赖最后一个 JSON、角色专属启发式扫描或散文猜测。Prompt/agent/skill/fake backend/真实 backend/Runtime validator 引用同一合同版本；示例必须由 schema 生成或通过真实消费者 validator；派发前做 parity 校验；格式失败与语义 attempt 分离；以 v0.7 全部 malformed response 类故障作为回归语料。
> 7. 对失败证据做端到端 review：核验产生、append-only 保留、按轮次/来源选择、注入、Agent 消费、ACK、失效与 replay。已有 infra 不覆盖 last_failure、diagnose_report 等机制必须保留。仅按 review 证明的真实缺口实施最小 delta，不预设复杂 failure_history；但普通失败不得把仍需消费的富证据静默覆盖，且所有相关角色必须有一致的 evidence 输入合同和可机械审计的消费证明。
> 8. Archer scaffold/首次构建必须语言中性：Archer 选择并物化宿主语言、工具链、依赖安装、质量守卫、测试 collect/run、build/artifact 与安装后公开出口合同；Runtime 只执行合同并消费版本化 normalized result，不理解 venv/pytest/wheel 或任何语言语义。允许 Archer 犯错：安装、编译/import、collect、build 或 smoke 失败必须形成机器证据并回 M-DESIGN 由 Archer 修订，Runtime 不猜正确命令。至少用一个全新 Python reference host 做真实物化验收，但 Python 细节只属于 reference acceptance，不得泄漏到 kernel 规范。
> 9. Issue 创建与生命周期必须闭环，重新打开 #14：真实 run 在 M-REQ-APPROVAL 后必须创建真实目标仓库 Issue；缺凭据不得静默降级 fake，而应 needs_attention。fake backend 只能由显式测试/模拟模式启用。持久化并 API readback 校验 FR/NFR↔repo↔issue id/number/url↔baseline digest 映射；task、commit、report 和关闭效果器只消费权威映射，不从 FAKE-N 或文本猜数字；崩溃恢复不得重复创建。实现问题关闭时机与审计 comment，并由 M-MILESTONE 关闭 release Project/milestone。v0.7 仅产生 FAKE-90..FAKE-103、project=fake-project 的事件必须成为反例测试。
>
> 规格约束：优先把问题2、3、4、6、7、8、9放在一个 spec 中，spec 项总数必须少于30；先尝试完整单 spec，只有需求分析证明无法容纳时才提出拆分并等待 Human 裁定，不能自行拆。保持 kernel/provider/language 中性；宿主特定实现只可作为 adapter/provider/reference materialization。问题1明确排除，问题5只回归核验。nightly result fetch、第二语言 adapter、RFC discussion 新状态、深版 OOB commit、通用多 registry/多部署平台不在本版本范围，除非它们是上述发布闭环不可避免的最小依赖并先经 Human 裁定。
>
> 最终验收：tracks 自身与至少一个从零创建的 reference host，分别完成 feature 和两种 hotfix 的适用发布旅程；CI、candidate、artifact、Human approval、外部 operation 和最终 release trace identity 一致；在 Issue 创建、CI、build、merge、tag、artifact、release、归档任一点中断后可 replay/reconcile，已完成副作用不重复，错误身份、stale evidence、畸形合同、silent fake fallback 均 fail-closed。

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
