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

> **Scribe:** **TRIAGE blocker T-003（Reference host 真实外部依赖与 Issue/CI 闭环边界）：seed 要求‘tracks 自身与至少一个从零创建的 reference host 分别完成 feature 和两种 hotfix 的适用发布旅程’且‘真实 run 在 M-REQ-APPROVAL 后必须创建真实目标仓库 Issue；缺凭据不得静默降级 fake，而应 needs_attention；fake 只能显式测试/模拟模式启用’并‘持久化并 API readback 校验 FR/NFR↔repo↔issue 映射’，‘GitHub required CI 必须由 API 回读并绑定 repo/workflow/run/head SHA’。可推导：第2级既有 v0.7 demo 为‘仓内动态创建全新最小 Python/pytest 宿主 repo，走真实宿主相同安装/contract/Runtime 路径’（Maestro 裁定 A），但 v0.7 未要求 reference host 真实 GitHub Issues/Projects/CI；第3级交互为 CLI+GitHub API。方向：A) reference host 亦分配真实 GitHub 远程（独立测试 repo），与 tracks 自身同等走真实 Issue 创建/API readback/CI SHA 绑定与 Project/milestone 生命周期，凭据缺失同为 needs_attention，确保‘FR/NFR↔issue 映射不猜数字、崩溃不重复创建’在双宿主均实证；B) reference host 仅本地验收（质量/版本/build/artifact 与安装后公开出口验证），GitHub Issue/CI/Project 闭环仅在 tracks 自身实证，reference host 以显式 fake/test 模式绕过真实外部依赖；C) 双模式：凭据齐全时走 A，缺凭据时降为 B 但显式标记且不计入 fail-closed 反例。不同答案改变最终验收的 infra、凭据门禁、‘silent fake fallback 均 fail-closed’的覆盖面及双宿主 6 旅程（2 宿主×3 旅程）的定义。推荐 A：与‘Issue 创建与生命周期必须闭环，重新打开 #14’及‘fake 只能显式测试模式启用’的严格语义一致，且正面回应 v0.7 FAKE-90..FAKE-103 反例；若 Human 考虑 reference host 轻量验收则选 B。请 Human 裁定 reference host 的真实外部依赖范围。
>> **Maestro:** 裁定 A。reference host 必须绑定一个专用真实 GitHub 远程，并与 tracks 自身一样真实执行 Issue 创建/readback、required CI SHA 绑定以及适用的 Project/milestone 生命周期；只有独立的显式反例测试可以使用 fake。凭据或远程不可用时状态必须是 needs_attention，该旅程不算通过，也不得降级为本地成功。reference host 的具体语言/工具仍只是 reference materialization，不进入 kernel 语义。

> **Scribe:** **TRIAGE blocker T-002（单一 candidate 身份的权威构成与冻结绑定点）：seed 要求‘所有门禁、外部副作用和归档都绑定同一 candidate’且最终验收‘CI、candidate、artifact、Human approval、外部 operation 和最终 release trace identity 一致’，并在‘Issue 创建、CI、build、merge、tag、artifact、release、归档任一点中断后可 replay/reconcile，已完成副作用不重复’，但未定义 candidate identity 的权威字段集合与冻结时机。宿主事实：v0.5/spec 已定义 candidate_sha=live run 启动时 git HEAD（Maestro 裁定 A），candidate_artifact.sha256 独立（architecture.md §1j），CI SHA 回读、build/artifact 分离，均未合并为单一 composite。方向：A) candidate = composite identity（git HEAD full SHA + artifact sha256 + 合同/policy 版本 digest + CI run head SHA），冻结于首次进入 M-VERIFY 时，M-VERIFY 后所有门禁与外部操作以该 composite 校验一致性，任一字段不一致即 stale/fail-closed；B) candidate = git HEAD SHA 为主身份，artifact/CI SHA 作为附属证据分别校验‘与同一 HEAD 绑定’，不合成单一 ID；C) candidate = run_id 为主身份（Runtime 生成），HEAD/artifact 仅作溯源。不同答案显著改变 M-VERIFY freeze、CI API 绑定、M-PUBLISH idempotency key、Human preview 绑定与归档一致性校验的实现与 NFR。推荐 A：与‘同一 candidate 绑定所有门禁与外部副作用’的语义最贴合，且可直接以 idempotency/reconcile 去重；若 Human 倾向最小变更则选 B。请 Human 裁定 candidate 组成与冻结点。
>> **Maestro:** 裁定 B，并明确冻结点：candidate 主身份是首次进入 M-VERIFY 时由 Runtime 在 clean tree 上冻结的完整 Git commit SHA。CI head SHA、artifact digest、contract/policy digest、FULL_F 与后续 operation plan 都是分别绑定该 candidate SHA 的证据，不合成为 candidate 本身；任一证据未绑定同一 SHA或发生漂移即 stale/fail-closed。M-RELEASE preview 绑定 candidate SHA 加当前证据/artifact/operation-plan digests，M-PUBLISH 以该 preview digest 与逐操作幂等键 reconcile。

> **Scribe:** **TRIAGE blocker T-001（M-RELEASE Human授权交付面与命令形态）：seed 规定‘M-RELEASE 生成绑定 candidate/artifact/风险/计划与副作用的 preview，只有 Human 可以 release/delay/return，且不能绕过失败门禁’，但未说明 Human 在何处以何种操作执行该授权。可推导阶梯：第2级（宿主项目事实）既有 trac CLI 仅含 triage/review/approve/run/status/replay/check（v0.6 spec E-01/E-02、v0.5 RP-01），无 release 专用命令；第3级交互模式为 terminal CLI；第4级成熟惯例可在 CLI 或 GitHub UI 完成发布授权——存在多个合理结果且选择会改变 spec 的 Interface/E、审计与权限实现。方向：A) 新增 trac CLI 发布门禁（例如 trac release preview / trac release approve --action release|delay|return），Human 在终端执行，preview 与授权决定落 append-only 事件并绑定 candidate，可程序校验‘失败门禁不可绕过’；B) 复用既有 approval（如 trac approve 扩展语义）承载 release 授权，preview 为 report/report 产物，授权记录于同一 approve 事件；C) Human 在 GitHub（tag/release UI 或 Issue comment）授权，Runtime 仅回读外部状态。推荐 A：与‘Human 只提供产品意图、需求评审与最终发布授权；Agent 自主完成其余’及全流程 fail-closed 审计目标一致，且与既有 trac CLI 交付面一致，避免引入外部 UI 依赖；若 Human 意在 GitHub 原生发布面则选 C。请 Human 裁定交付面与命令形态，Sage 据此落 E/NFR。
>> **Maestro:** 裁定 A。新增独立 trac release CLI 交付面，不复用需求阶段 trac approve，也不依赖 GitHub UI/comment 授权。CLI 至少提供读取当前发布 preview 与提交 release、delay、return 三种 Human 决定的能力；确切子命令文法由后续 spec/interface 在此语义内收敛。决定必须 append-only、绑定当前 preview/candidate，任何门禁失败或 preview stale 时 Runtime 拒绝授权。

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
