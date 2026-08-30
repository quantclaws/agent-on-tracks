---
story_id: S-001
title: 可信发布闭环与流程合同收敛
created: 2026-08-30
status: draft
sha: 94ceb52f9d7f0fb441c2ebd5dc2e79dc1fbfdd9bb5b8a1e3c2d0ec3ac2eebea4
---

# S-001: 可信发布闭环与流程合同收敛

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

> **Scribe [RESOLVED]:** **TRIAGE blocker T-003（Reference host 真实外部依赖与 Issue/CI 闭环边界）：seed 要求‘tracks 自身与至少一个从零创建的 reference host 分别完成 feature 和两种 hotfix 的适用发布旅程’且‘真实 run 在 M-REQ-APPROVAL 后必须创建真实目标仓库 Issue；缺凭据不得静默降级 fake，而应 needs_attention；fake 只能显式测试/模拟模式启用’并‘持久化并 API readback 校验 FR/NFR↔repo↔issue 映射’，‘GitHub required CI 必须由 API 回读并绑定 repo/workflow/run/head SHA’。可推导：第2级既有 v0.7 demo 为‘仓内动态创建全新最小 Python/pytest 宿主 repo，走真实宿主相同安装/contract/Runtime 路径’（Maestro 裁定 A），但 v0.7 未要求 reference host 真实 GitHub Issues/Projects/CI；第3级交互为 CLI+GitHub API。方向：A) reference host 亦分配真实 GitHub 远程（独立测试 repo），与 tracks 自身同等走真实 Issue 创建/API readback/CI SHA 绑定与 Project/milestone 生命周期，凭据缺失同为 needs_attention，确保‘FR/NFR↔issue 映射不猜数字、崩溃不重复创建’在双宿主均实证；B) reference host 仅本地验收（质量/版本/build/artifact 与安装后公开出口验证），GitHub Issue/CI/Project 闭环仅在 tracks 自身实证，reference host 以显式 fake/test 模式绕过真实外部依赖；C) 双模式：凭据齐全时走 A，缺凭据时降为 B 但显式标记且不计入 fail-closed 反例。不同答案改变最终验收的 infra、凭据门禁、‘silent fake fallback 均 fail-closed’的覆盖面及双宿主 6 旅程（2 宿主×3 旅程）的定义。推荐 A：与‘Issue 创建与生命周期必须闭环，重新打开 #14’及‘fake 只能显式测试模式启用’的严格语义一致，且正面回应 v0.7 FAKE-90..FAKE-103 反例；若 Human 考虑 reference host 轻量验收则选 B。请 Human 裁定 reference host 的真实外部依赖范围。
>> **Maestro:** 裁定 A。reference host 必须绑定一个专用真实 GitHub 远程，并与 tracks 自身一样真实执行 Issue 创建/readback、required CI SHA 绑定以及适用的 Project/milestone 生命周期；只有独立的显式反例测试可以使用 fake。凭据或远程不可用时状态必须是 needs_attention，该旅程不算通过，也不得降级为本地成功。reference host 的具体语言/工具仍只是 reference materialization，不进入 kernel 语义。
>>> **Scribe:** 已按裁定 A 落文（DRAFT）：§3.7 与 §5/BS-15 已锁定 reference host 须绑定专用真实 GitHub 远程、与 tracks 自身同等真实执行 Issue 创建/readback、required CI SHA 绑定与适用 Project/milestone 周期，凭据/远程不可用时 needs_attention 不降级本地成功；仅显式反例测试可用 fake。§3.6 同时限定 Python 细节仅属 reference acceptance 不入 kernel。BS-15/BS-16 与最终验收已据此更新。DRAFT 完成。

> **Scribe [RESOLVED]:** **TRIAGE blocker T-002（单一 candidate 身份的权威构成与冻结绑定点）：seed 要求‘所有门禁、外部副作用和归档都绑定同一 candidate’且最终验收‘CI、candidate、artifact、Human approval、外部 operation 和最终 release trace identity 一致’，并在‘Issue 创建、CI、build、merge、tag、artifact、release、归档任一点中断后可 replay/reconcile，已完成副作用不重复’，但未定义 candidate identity 的权威字段集合与冻结时机。宿主事实：v0.5/spec 已定义 candidate_sha=live run 启动时 git HEAD（Maestro 裁定 A），candidate_artifact.sha256 独立（architecture.md §1j），CI SHA 回读、build/artifact 分离，均未合并为单一 composite。方向：A) candidate = composite identity（git HEAD full SHA + artifact sha256 + 合同/policy 版本 digest + CI run head SHA），冻结于首次进入 M-VERIFY 时，M-VERIFY 后所有门禁与外部操作以该 composite 校验一致性，任一字段不一致即 stale/fail-closed；B) candidate = git HEAD SHA 为主身份，artifact/CI SHA 作为附属证据分别校验‘与同一 HEAD 绑定’，不合成单一 ID；C) candidate = run_id 为主身份（Runtime 生成），HEAD/artifact 仅作溯源。不同答案显著改变 M-VERIFY freeze、CI API 绑定、M-PUBLISH idempotency key、Human preview 绑定与归档一致性校验的实现与 NFR。推荐 A：与‘同一 candidate 绑定所有门禁与外部副作用’的语义最贴合，且可直接以 idempotency/reconcile 去重；若 Human 倾向最小变更则选 B。请 Human 裁定 candidate 组成与冻结点。
>> **Maestro:** 裁定 B，并明确冻结点：candidate 主身份是首次进入 M-VERIFY 时由 Runtime 在 clean tree 上冻结的完整 Git commit SHA。CI head SHA、artifact digest、contract/policy digest、FULL_F 与后续 operation plan 都是分别绑定该 candidate SHA 的证据，不合成为 candidate 本身；任一证据未绑定同一 SHA或发生漂移即 stale/fail-closed。M-RELEASE preview 绑定 candidate SHA 加当前证据/artifact/operation-plan digests，M-PUBLISH 以该 preview digest 与逐操作幂等键 reconcile。
>>> **Scribe:** 已按裁定 B 落文（DRAFT）：§3.1 与 BS-01/BS-07 已按 candidate 主身份=首次进入 M-VERIFY 时 clean tree 的完整 Git commit SHA 落地，CI head SHA、artifact digest、合同/policy digest、FULL_F 与 operation plan 均为绑定该 SHA 的附属证据，不合成 candidate；任一未绑定或漂移即 stale/fail-closed。M-RELEASE preview 绑定 candidate SHA + 当期证据/artifact/operation-plan digests，M-PUBLISH 以 preview digest + 逐操作幂等键 reconcile。§5 约束同步更新。DRAFT 完成。

> **Scribe [RESOLVED]:** **TRIAGE blocker T-001（M-RELEASE Human授权交付面与命令形态）：seed 规定‘M-RELEASE 生成绑定 candidate/artifact/风险/计划与副作用的 preview，只有 Human 可以 release/delay/return，且不能绕过失败门禁’，但未说明 Human 在何处以何种操作执行该授权。可推导阶梯：第2级（宿主项目事实）既有 trac CLI 仅含 triage/review/approve/run/status/replay/check（v0.6 spec E-01/E-02、v0.5 RP-01），无 release 专用命令；第3级交互模式为 terminal CLI；第4级成熟惯例可在 CLI 或 GitHub UI 完成发布授权——存在多个合理结果且选择会改变 spec 的 Interface/E、审计与权限实现。方向：A) 新增 trac CLI 发布门禁（例如 trac release preview / trac release approve --action release|delay|return），Human 在终端执行，preview 与授权决定落 append-only 事件并绑定 candidate，可程序校验‘失败门禁不可绕过’；B) 复用既有 approval（如 trac approve 扩展语义）承载 release 授权，preview 为 report/report 产物，授权记录于同一 approve 事件；C) Human 在 GitHub（tag/release UI 或 Issue comment）授权，Runtime 仅回读外部状态。推荐 A：与‘Human 只提供产品意图、需求评审与最终发布授权；Agent 自主完成其余’及全流程 fail-closed 审计目标一致，且与既有 trac CLI 交付面一致，避免引入外部 UI 依赖；若 Human 意在 GitHub 原生发布面则选 C。请 Human 裁定交付面与命令形态，Sage 据此落 E/NFR。
>> **Maestro:** 裁定 A。新增独立 trac release CLI 交付面，不复用需求阶段 trac approve，也不依赖 GitHub UI/comment 授权。CLI 至少提供读取当前发布 preview 与提交 release、delay、return 三种 Human 决定的能力；确切子命令文法由后续 spec/interface 在此语义内收敛。决定必须 append-only、绑定当前 preview/candidate，任何门禁失败或 preview stale 时 Runtime 拒绝授权。
>>> **Scribe:** 已按裁定 A 落文（DRAFT）：§3.2/§5/BS-08 已新增独立 trac release CLI 交付面（读取当前 preview 与 release/delay/return 三择一），不复用 trac approve，不依赖 GitHub UI；决定 append-only 绑定 preview/candidate digest，门禁失败或 preview stale 时拒绝授权。§2 完成结果与 §6 已据此更新为无开放决定。DRAFT 完成。

## 2. 用户意图

- **想完成什么**：在 v0.7 已建立的可信测试证据与单一 canonical quality guard registry 之上，为 feature 与两种 hotfix 打通从 M-IMPL 之后的完整可信发布闭环——正式注册 M-VERIFY（冻结 candidate 主身份、FULL_F 复用/重跑、按宿主合同声明的本地质量/trace/reach/反 slop/版本/build/artifact 与安装后公开出口验证、GitHub required CI 的 API 回读与 Prism 同 candidate 终审）、依次经 M-SECURITY（版本化合同/policy 声明的安全扫描与深度审计）、M-RELEASE（生成绑定 candidate/artifact/风险/计划/副作用的 preview 并经独立 `trac release` 的 Human release/delay/return 三择一门禁）、M-PUBLISH（以 write-ahead + idempotency + reconcile 执行宿主声明的 merge/tag/artifact/release/deploy 等外部操作，Agent 不触不可逆副作用）、M-MILESTONE（需求→release trace 闭环、Issue/Project 生命周期、证据归档只读封存与临时 refs 清理）；同时收敛结构化 Agent 合同（统一 versioned envelope/schema 与单一解析路径、parity 校验、format/semantic 分离）、对失败证据做端到端 review 与最小增量修复、实现 Archer 语言中性 scaffold/首构并以全新 Python reference host 真实物化验收、以及重开 #14 的 Issue 闭环（真实 Issue、映射持久化与 API 回读、去重与审计关闭）。最终在 tracks 自身与一个全新 Python reference host 上分别走通 feature 与两种 hotfix 的适用发布旅程，要求 CI/candidate/artifact/Human approval/外部 operation/最终 release trace 同一身份、可在任意点中断后 replay/reconcile 且已完成副作用不重复，错误身份/stale evidence/畸形合同/silent fake fallback 均 fail-closed。交付面为现有 `trac` CLI 与 Runtime 机器合同配合 GitHub API（Issue/CI/Project），Human 仅在产品意图、需求评审与最终发布授权处介入（Maestro 裁定 T-001 的独立 `trac release`）。
- **当前哪里受阻**：流水止于 M-IMPL→M-VERIFY 边界（v0.5 FR-0160、v0.6 FR-0246/FR-0254 未注册，发布前置仅 `trac check release-evidence` 独立门禁），无 candidate 冻结与 FULL_F 复用注册、无按宿主合同声明的本地质量等门禁与 “repo/workflow/run/head SHA” 的 CI 绑定、Prism 未对同 candidate 终审；无 M-SECURITY 合同化扫描、无 M-RELEASE preview 与可程序校验的 Human 三择一门禁、外部操作无幂等与 reconcile、trace/归档/生命周期未闭环；Issue 创建在 M-REQ-APPROVAL 后静默降级为 FAKE-90..FAKE-103 / project=fake-project 且无 FR/NFR↔repo↔issue 映射持久化与 API 回读、无去重与审计关闭；Agent 合同分散为 “最后一个 JSON”、角色启发式扫描与散文猜测，多版本不一致且 malformed 未能 fail-closed；LAST_FAILURE/diagnose_report 等失败证据链路未覆盖，普通失败可能覆盖仍需消费的富证据且缺乏一致的消费合同与审计证明；scaffold 隐含 Python/pytest 语义，reference host 未经真实 GitHub 外部验证。
- **完成后能看到什么结果**：操作者以 `trac run` 推 run 经 M-VERIFY（candidate 冻结、FULL_F 复用或重跑、本地验证、CI 回读、Prism 复审）→ M-SECURITY 评估 → 在 `trac release preview` / `trac report` 看到绑定 candidate SHA 与当前 artifact/证据/operation plan digests 的 preview → Human 在终端以独立 `trac release` 执行 release/delay/return（三择一，失败门禁或 stale preview 时被 Runtime 拒绝，不可绕过）→ Runtime 在 M-PUBLISH 以 preview digest + 逐操作幂等键执行宿主声明的外部操作（已完成经 reconcile 跳过）→ M-MILESTONE 关闭 Issue/Project/milestone 并只读封存证据；在 `trac status` 看到 stage、candidate SHA、full_reuse/rerun、CI 绑定与 Prism verdict，在 `trac replay/report` 审计全链事件与 release trace；任一点（Issue 创建、CI 观测、build、merge/tag/artifact/release、归档）中断后可 replay 重建并 reconcile 已完成不重复；tracks 自身与 reference host（专用真实 GitHub 远程，凭据缺失则 `needs_attention`）各自完成 feature、post-release hotfix（合入 main 并在存在活跃 release 分支时同步修复出 patch）与 dev hotfix（仅合入活跃分支经 pre-release 渠道）的适用旅程，且错误身份/stale evidence/畸形合同/silent fake fallback 均可观测地 fail-closed。

## 3. 核心操作路径

### 3.1. M-VERIFY 注册、candidate 冻结与 FULL_F 复用及宿主合同门禁

- **变更基线**：新增 — 当前流水止于 M-IMPL 边界（v0.5 FR-0160 不实现 M-VERIFY/M-RELEASE，v0.6 FR-0254 将 M-VERIFY 复用语义固化为 canonical 但本版不注册；门禁仅 `trac check release-evidence` 独立校验），无 candidate 冻结、无 FULL_F 复用注册、无按宿主合同声明的本地质量/trace/reach/反 slop/版本/build/artifact 与安装后公开出口验证、无 GitHub required CI 的 API 回读绑定、Prism 未对同一 candidate 终审；Runtime 尚无对 candidate SHA 与各证据一致性的统一绑定。
- **入口/触发**：feature 或 hotfix run 在 M-IMPL 产出干净 FULL_F 后由 `trac run` 自动进入新注册的 M-VERIFY；操作者以 `trac status` / `trac replay` / `trac report` / `trac check` 观察门禁。

1. Runtime 在 clean tree 上冻结 candidate 主身份（Maestro 裁定 T-002 B：首次进入 M-VERIFY 时的完整 Git commit SHA），记录 candidate SHA 并作为后续所有门禁、preview 与外部操作的唯一主身份；CI head SHA、artifact digest、合同/policy 版本 digest、FULL_F 证据与后续 operation plan 均为绑定该 SHA 的附属证据，不合成为 candidate 本身。
2. 判定 FULL_F 复用：若 candidate 未漂移且与 M-IMPL 干净 FULL_F 的 identity 一致且无 STALE → 复用该 FULL_F（事件 `evidence.reused kind=full_f`，不本地重跑）；否则按宿主合同声明的 LOCAL_GATES 重跑 FULL。
3. 按宿主合同/policy 的版本化声明（由 Archer 在 M-DESIGN 物化，见 3.6）依次执行本地质量、trace/reach/反 slop、版本、build/artifact 与安装后公开出口验证；未声明、未知或畸形结果一律 fail-closed。
4. 对 GitHub required CI 执行 API 回读，校验 repo/workflow/run/head SHA 与当前 candidate SHA 严格绑定，不一致、缺失或 stale 即 fail-closed；Prism 对同一 candidate 做最终一致性复审， verdict 绑定 candidate SHA。
5. 操作者在 `trac status` 看到 `stage=M-VERIFY`、candidate SHA、`full_reuse` 或 `full_rerun` 判定、`ci.run_observed` 与各本地验证及 Prism verdict；在 `trac replay/report` 可审计冻结事件、复用判定、CI 绑定与本地验证事件。

- **完成结果**：M-VERIFY 通过则进入 M-SECURITY；任一门禁失败、stale 或未知策略即 fail-closed 阻断，不进入发布。可崩溃恢复：冻结身份与已落事件由重放重建，已完成的 CI 观测与验证经证据一致性校验后不重复。

### 3.2. 发布闭环：M-SECURITY → M-RELEASE preview 与独立 Human 门禁 → M-PUBLISH 幂等执行 → M-MILESTONE 归档与生命周期

- **变更基线**：新增 — 当前无 M-SECURITY/M-RELEASE/M-PUBLISH/M-MILESTONE 四阶段，安全扫描未合同化，发布前置无 preview 与 Human 三择一门禁，外部 merge/tag/artifact/release/deploy 无 write-ahead 幂等与 reconcile，需求到 release 的 trace、Issue/Project/归档/封存未闭环。
- **入口/触发**：M-VERIFY 通过后 `trac run` 依次进入 M-SECURITY；M-RELEASE 产出 preview 后进入 `awaiting_release`，Human 在终端执行独立 `trac release`（Maestro 裁定 T-001 A）；M-PUBLISH 与 M-MILESTONE 由 Runtime 自动推进，操作者以 `trac status/replay/report` 与 `trac release preview` 观察。

1. M-SECURITY 按版本化宿主合同/policy 声明的安全扫描与深度审计清单执行，策略版本与阈值由合同 pin；未知、缺失或畸形结果 fail-closed。
2. M-RELEASE 聚合 candidate SHA、当前已绑定的 CI/artifact/合同与 FULL_F 证据、风险与计划，生成绑定 operation plan 的 preview（preview digest）；preview 通过 `trac release preview` 与 `trac report` 可见，发布进入 `awaiting_release`。
3. Human 在终端经独立 `trac release` 提交三择一决定：`release` 放行、`delay` 推迟、`return` 退回；决定 append-only 并绑定 preview/candidate digest；Runtime 校验 preview 未 stale 且全部前置门禁已过，否则拒绝授权（fail-closed，不可绕过失败门禁，Maestro 裁定 T-001）。
4. M-PUBLISH 对宿主声明的外部操作清单（merge/tag/artifact/release/deploy 等）以 write-ahead（先落意图事件）+ idempotency key（preview digest + 逐操作键）+ reconcile（ crash 后比对远端实际状态，已完成不重复）执行；Agent 不执行或模拟不可逆副作用，权限仅 Runtime 持有。
5. M-MILESTONE 形成 approved AC→test-plan/collected node→candidate 证据→FULL_F→CI→Human approval→外部 operation→release 的可审计 trace 闭环，执行 Issue/Project/milestone 生命周期（关闭与审计 comment）、证据归档只读封存与临时 refs 清理；若发布已成功而归档失败，仅重试收尾，不重复已成功的 merge/tag/release（seed 约束）。
6. 在 Issue 创建、CI 观测、build、merge/tag/artifact/release、归档任一点中断后可 `trac replay` 重建状态并 `trac run` reconcile，已完成副作用经幂等键跳过，未完成继续。

- **完成结果**：`release` 成功则 `terminal=released`，形成不可变 release trace，可经 `trac report` 导出并与外部 tag/release 互验；`delay` 停留 `delayed` 等待重授权；`return` 回到指定上游阶段；失败门禁或 stale preview 阻止 release。可经 `trac status` 分辨当前 Human 决定与 candidate/preview 绑定。

### 3.3. Feature 与两种 hotfix 的真实发布旅程

- **变更基线**：修改/扩展 — 当前 feature 与 hotfix 均止于 M-IMPL 边界（v0.6 FR-0246 boundary，不执行 merge/发布语义，载于 wiki/flow.md §16.3）；hotfix 分 post-release 与 dev 两场景但均未真实发布。
- **入口/触发**：操作者经 `trac start <version>` 创建 feature，或经 `trac hotfix <issue> --scenario post-release|dev` 创建 hotfix；run 经 M-IMPL→M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH 走完整旅程，适用性按分支存在性判定。

1. Feature 完成规范发布旅程：经 M-VERIFY 与 M-SECURITY 后产出 preview，经 Human `trac release` 放行后在 M-PUBLISH 合入 main、打 tag、推 artifact、发 release，完成公开版本。
2. post-release hotfix 从 fix 分支在 M-VERIFY 验证后合入 main，并在存在活跃 release 分支时同步修复（同步 merge 活跃 release 分支），产生 patch 发布；预验证、M-VERIFY、安全 policy 与 Human release gate 同 feature 保留。
3. dev hotfix 仅合入活跃 release 分支，沿既有 pre-release/开发渠道交付，不冒充公开版本（不打公开 tag/release）。
4. 三种旅程的发布操作均受同一 candidate SHA 绑定与逐操作幂等约束，已合入/已发布后 reconcile 不重复 tag/release；无活跃 release 分支时 dev hotfix 不成立（precheck fail-closed）。

- **完成结果**：Feature 产生公开 release；post-release hotfix 产生 patch release 并视情况同步 release 分支；dev hotfix 随活跃分支预发布交付；均可在 `git log`、`git tag`、GitHub release 与 `trac report` 的 release trace 验证 CI/candidate/artifact/Human approval/外部 operation 同一性。

### 3.4. 结构化 Agent 合同收敛（统一 envelope/schema 与单一解析）

- **变更基线**：替换 — 现有各角色与 assignment kind 的输入 assignment/evidence 与输出 outcome/verdict 分散为 “最后一个 JSON” 提取、角色专属启发式扫描、散文猜测等多种解析，易受 malformed 注入；Prompt/agent/skill/fake backend/真实 backend/Runtime validator 所引合同版本不一致，示例游离于 schema。
- **入口/触发**：任意 assignment 派发前与 Agent 返回后，由 Runtime 的派发器与 validator 经版本化 envelope/schema 自动校验；操作者以 `trac replay/report` 审计 format vs semantic。

1. 所有角色与 assignment kind 的输入与输出统一为版本化、可判别 envelope + JSON schema（单一确定性解析路径，含 kind/version 字段，废弃多路径回退）。
2. 派发前对 envelope 做 parity 校验：Prompt/agent/skill/fake backend/真实 backend/Runtime validator 引用的合同版本必须一致，不一致即 fail-closed 拒绝派发。
3. 示例与 fixtures 必须由 schema 生成或经真实消费者 validator 校验通过，不手写游离 JSON。
4. 区分格式失败与语义 attempt 失败并分离 verdict，分别落事件；以 v0.7 全部 malformed response 故障为回归语料。

- **完成结果**：非法 envelope 在派发前被拒绝，不进入 Agent 执行；malformed 不污染业务状态；解析结果可程序复核，`trac replay` 展示 format_error 与 semantic attempt 的分离及版本一致性。

### 3.5. 失败证据端到端 review 与最小增量修复

- **变更基线**：修改 — 现有已具备 `trac replay/report` 的 append-only 事件流与 last_failure/diagnose_report 等机制（需保留），但未对 “产生→append-only 保留→按轮次/来源选择→注入→Agent 消费→ACK→失效→replay” 做端到端 review，普通失败可能静默覆盖仍需消费的富证据，且各角色证据输入合同与消费证明不一致。
- **入口/触发**：每次失败产生时与后续 assignment 注入前，由 Runtime 执行 review 链并在 `trac replay/report` 留审计；修复后经重跑验证。

1. 对全链做 review，核验失败证据的产生、append-only 保留、按轮次/来源选择、注入目标 assignment、Agent 消费、ACK、失效与 replay 环节。
2. 已有 infra 未覆盖的 last_failure/diagnose_report 等机制保留；仅按 review 证明的真实缺口实施最小 delta，不预设复杂 `failure_history`。
3. 约束：普通失败不得静默覆盖仍需消费的富证据；所有相关角色拥有 consistent evidence 输入合同与可机械审计的消费证明（event 可回放）。

- **完成结果**：失败证据不丢失、不错配、不重复覆盖，Agent 消费可审计，重放后与中断前一致；review 未发现缺口则不产生额外改动。

### 3.6. Archer 语言中性 scaffold/首构与 Python reference host 真实物化

- **变更基线**：新增 — 当前 scaffold/构建路径隐含 Python/pytest 假设，Runtime 偶有硬编码 venv/pytest/wheel 语义；现要求 Archer 在 M-DESIGN 选择并物化宿主语言、工具链、依赖安装、质量守卫、测试 collect/run、build/artifact 与安装后公开出口合同，Runtime 仅执行合同并消费版本化 normalized result。
- **入口/触发**：新 host 初始化 `trac init` 或首个 feature 的 M-DESIGN 阶段，由 Archer 物化上述合同（写入 repo 配置如 `project.toml` / contracts），Runtime 校验并执行；失败回流见步骤 3。

1. Archer 物化宿主合同：选定语言与工具链，声明质量守卫 pinned tool/config digest/scope/threshold、测试 collect/run_selected、build/artifact、安装后出口 smoke。
2. Runtime 仅按合同执行命令并解析版本化 normalized result，不理解 venv/pytest/wheel 等语言语义；未知语言/框架或畸形结果 fail-closed。
3. 允许 Archer 犯错：安装、编译/import、collect、build 或 smoke 失败必须形成机器证据并回 M-DESIGN 由 Archer 修订，Runtime 不猜正确命令。
4. 至少用一个全新 Python reference host 做真实物化验收（与 tracks 自身同等走 3.1-3.3 的发布旅程，含真实 GitHub 外部依赖，见 3.7 与 Maestro 裁定 T-003），但 Python 细节仅落 reference acceptance，不泄漏至 kernel 规范/接口。

- **完成结果**：新 host 可在 clean 机器上按合同完成安装→collect→build→smoke；物化失败可回流修订；reference host 验收通过证明 kernel 语言中性。

### 3.7. Issue 创建与生命周期闭环（重开 #14）

- **变更基线**：修改 — 当前真实 run 在 M-REQ-APPROVAL 后创建 Issue 的路径被静默降级为 FAKE-N（v0.7 仅产生 FAKE-90..FAKE-103、project=fake-project 事件），无持久化映射、无 API 回读、无去重与审计关闭，Project/milestone 未由 M-MILESTONE 关闭。
- **入口/触发**：M-REQ-APPROVAL 通过后首个 task 派发前由 Runtime 执行 Issue 创建；M-MILESTONE 执行关闭与归档，操作者以 `trac status/replay/report` 与 GitHub Issue/Project 观察。

1. 真实 run 在 M-REQ-APPROVAL 后必须创建真实目标仓库 Issue；缺凭据或网络不可用不静默降级 fake，转 `needs_attention`（`trac status` 报告原因与恢复指引，Maestro 裁定 T-003）。
2. fake backend 仅由显式测试/模拟模式（如 `TRAC_FAKE_*` / `--assignment-overlay`）启用，普通 run 严禁 fake；真实模式下 FAKE-N 产物即 fail-closed。
3. 持久化并 API 回读校验 FR/NFR↔repo↔issue id/number/url↔baseline digest 映射；`trac report` 可审计该映射。
4. task、commit、report 与关闭 effector 仅消费权威映射，不从 FAKE-N 或文本猜数字；崩溃恢复时幂等校验，已创建不重复。
5. 实现关闭时机与审计 comment（关联 release trace），并由 M-MILESTONE 关闭对应 release Project/milestone；清理临时 refs。
6. 将 v0.7 FAKE-90..FAKE-103 事件作为反例测试，验证真实模式下 fake 被拒绝且映射缺失被 fail-closed。

- **完成结果**：每个 FR/NFR 有可追溯的真实 Issue 链接，commit/report 引用权威映射，Project/milestone 状态与 release 一致，临时 refs 无残留；双宿主（tracks 自身与 reference host 的专用真实远程）均满足该闭环，否则 `needs_attention` 阻塞发布。

## 4. 行为种子

### BS-01 candidate 冻结合入主身份（Maestro 裁定 T-002 B）

- EARS: `WHEN run 首次进入 M-VERIFY, THE 系统 SHALL 在 clean tree 上冻结完整 Git commit SHA 作为 candidate 主身份，且 SHALL 保持该 SHA 为后续所有门禁、preview 与外部操作的唯一主身份`
- 来源: [3.1 / seed 问题 2 / Maestro 裁定 T-002 B]
- 说明: 以单一 Git SHA 作为全链锚点，避免 composite 身份的歧义；后续 CI/artifact/合同等证据分别绑定该 SHA。

### BS-02 FULL_F 复用判定

- EARS: `WHEN M-VERIFY 判定 FULL_F 复用, IF candidate 未漂移且与 M-IMPL 干净 FULL_F 的 identity 一致且无 STALE, THE 系统 SHALL 复用该 FULL_F 证据且 SHALL NOT 本地重跑 FULL, ELSE THE 系统 SHALL 按宿主合同声明的 LOCAL_GATES 重跑 FULL`
- 来源: [3.1 / seed 问题 2]
- 说明: 未漂移复用干净 FULL_F，漂移时重跑，保证门禁与证据一致且不重复全量。

### BS-03 宿主合同本地门禁未知/畸形 fail-closed

- EARS: `WHEN M-VERIFY 执行宿主合同声明的本地质量、trace/reach/反 slop、版本、build/artifact 与安装后公开出口验证, IF 某项未声明、未知或畸形, THE 系统 SHALL fail-closed 阻断且 SHALL NOT 猜测或降级通过`
- 来源: [3.1 / seed 问题 2]
- 说明: 策略由版本化宿主合同声明，Runtime 不硬编码语言/工具，畸形不放行。

### BS-04 GitHub required CI API 回读绑定

- EARS: `WHEN M-VERIFY 校验 GitHub required CI, THE 系统 SHALL 经 API 回读并绑定 repo/workflow/run/head SHA 与当前 candidate SHA 一致，且 WHILE 不一致、缺失或 stale, THE 系统 SHALL fail-closed 阻断`
- 来源: [3.1 / seed 问题 2]
- 说明: 以 API 真实观测绑定 CI 与 candidate，防止过期或错位 CI 被误用。

### BS-05 Prism 同 candidate 终审

- EARS: `WHEN M-VERIFY 完成本地与 CI 门禁, THE 系统 SHALL 由 Prism 对同一 candidate 做最终一致性复审且 verdict 绑定 candidate SHA, 且 IF 复审不通过, THE 系统 SHALL 阻断进入发布`
- 来源: [3.1 / seed 问题 2]
- 说明: 以独立复审守住跨门禁一致性。

### BS-06 安全策略评估 fail-closed

- EARS: `WHEN M-SECURITY 执行安全扫描与深度审计, THE 系统 SHALL 按版本化宿主合同/policy 声明执行且 IF 结果未知或畸形, THE 系统 SHALL fail-closed 阻断`
- 来源: [3.2 / seed 问题 3]
- 说明: 安全策略由宿主声明，Runtime 不推断，未知不放行。

### BS-07 M-RELEASE preview 绑定

- EARS: `WHEN M-RELEASE 生成发布 preview, THE 系统 SHALL 绑定 candidate SHA、当前已验证的 artifact/证据与 operation plan digests 形成 preview digest，且 SHALL 使该 preview 可经 trac release preview / trac report 审计`
- 来源: [3.2 / seed 问题 3 / Maestro 裁定 T-002 B]
- 说明: preview 作为 Human 决策的唯一依据，必须与 candidate 及当期证据一致。

### BS-08 独立 Human 三择一门禁（Maestro 裁定 T-001 A）

- EARS: `WHEN preview 进入 awaiting_release, THE 系统 SHALL 仅允许 Human 经独立 trac release 以 release/delay/return 三择一决定放行，且 SHALL 要求决定 append-only 并绑定 preview/candidate digest；WHILE 前置门禁失败或 preview stale, THE 系统 SHALL 拒绝授权且 Human 决定 SHALL NOT 绕过失败门禁`
- 来源: [3.2 / seed 问题 3 / Maestro 裁定 T-001 A]
- 说明: 交付面为独立 `trac release`（不复用 trac approve，不依赖 GitHub UI），保障 “失败不可绕过” 且可审计。

### BS-09 外部操作幂等与 reconcile（Agent 不触不可逆副作用）

- EARS: `WHEN M-PUBLISH 执行宿主声明的 merge/tag/artifact/release/deploy 等外部操作, THE 系统 SHALL 以 write-ahead + idempotency key（preview digest + 逐操作键）+ reconcile 执行，且 SHALL 保证已完成副作用经远端比对后不重复；THE 系统 SHALL 保证 Agent 不执行或模拟不可逆副作用`
- 来源: [3.2 / seed 问题 3]
- 说明: 以幂等与远端 reconcile 实现可崩溃恢复，已成功不重复，未完成继续。

### BS-10 归档与生命周期闭环（发布成功后仅重试收尾）

- EARS: `WHEN M-MILESTONE 执行归档与生命周期, THE 系统 SHALL 形成需求到 release 的 trace 闭环、关闭 Issue/Project/milestone、只读封存证据并清理临时 refs, 且 IF 发布已成功而归档失败, THE 系统 SHALL 仅重试收尾且 SHALL NOT 重复已成功的外部发布`
- 来源: [3.2 / seed 问题 3]
- 说明: 归档失败不触发重复发布，保证外部副作用的精确一次语义。

### BS-11 Feature 与两种 hotfix 真实发布

- EARS: `WHEN feature 或 hotfix 进入发布, THE 系统 SHALL 使 feature 完成公开发布、post-release hotfix 合入 main 并在存在活跃 release 分支时同步修复产生 patch 发布、dev hotfix 仅合入活跃 release 分支经 pre-release 渠道交付且不冒充公开版本，且 SHALL 为三者保留 M-VERIFY、安全 policy 与 Human release gate`
- 来源: [3.3 / seed 问题 4]
- 说明: 三旅程均需走真实发布闭环，分支策略区分受众与版本语义。

### BS-12 结构化 Agent 合同单一解析与 parity

- EARS: `WHEN 派发或回收任意 assignment, THE 系统 SHALL 经统一 versioned envelope/schema 以单一确定性路径解析，且 IF Prompt/agent/skill/fake backend/真实 backend/Runtime validator 所引合同版本不一致, THE 系统 SHALL fail-closed 拒绝派发；THE 系统 SHALL 分离 format 失败与 semantic attempt，且 SHALL 以 v0.7 全部 malformed 故障为回归语料使畸形 fail-closed`
- 来源: [3.4 / seed 问题 6]
- 说明: 废弃 “最后一个 JSON” 与启发式扫描，以 schema 与 parity 保证输入输出可判别。

### BS-13 失败证据端到端不覆盖与可审计

- EARS: `WHEN 产生失败证据, THE 系统 SHALL 保证 append-only 保留并按轮次/来源选择注入对应 assignment，且 WHILE 普通失败发生, THE 系统 SHALL NOT 静默覆盖仍需消费的富证据；所有相关角色 SHALL 拥有 consistent evidence 输入合同与可机械审计的消费证明（ACK/失效/replay 可回放）`
- 来源: [3.5 / seed 问题 7]
- 说明: 以最小 delta 修缺口，不预设复杂 failure_history，但保证富证据不丢失与可审计消费。

### BS-14 语言中性 scaffold 与容错修订

- EARS: `WHEN Archer 物化宿主合同时, THE 系统 SHALL 由 Archer 声明语言、工具链、依赖安装、质量守卫、测试 collect/run、build/artifact 与安装后公开出口，且 Runtime SHALL 仅执行合同并消费版本化 normalized result 而不硬编码语言语义；IF 安装/编译/collect/build/smoke 失败, THE 系统 SHALL 形成机器证据并回 M-DESIGN 由 Archer 修订且 Runtime SHALL NOT 猜正确命令`
- 来源: [3.6 / seed 问题 8]
- 说明: Runtime 保持 kernel/provider/language 中性，Archer 可犯错但必须以证据回流修订。

### BS-15 Issue 真实创建与映射校验（重开 #14）

- EARS: `WHEN 真实 run 通过 M-REQ-APPROVAL, THE 系统 SHALL 创建真实目标仓库 Issue 且 SHALL 持久化并经 API 回读校验 FR/NFR↔repo↔issue id/number/url↔baseline digest 映射；IF 凭据缺失或不可用, THE 系统 SHALL 进入 needs_attention 且 SHALL NOT 静默降级为 fake；fake backend SHALL 仅在显式测试/模拟模式下启用，且 task/commit/report/关闭 effector SHALL 仅消费权威映射而不从 FAKE-N 或文本猜数字`
- 来源: [3.7 / seed 问题 9 / Maestro 裁定 T-003 A]
- 说明: 以真实 Issue 与权威映射闭环 #14，崩溃恢复不重复创建，FAKE-90..103 作为反例 test。

### BS-16 关闭时机与 Project 归档

- EARS: `WHEN M-MILESTONE 关闭生命周期时, THE 系统 SHALL 按审计时机关闭 Issue（附 release trace 关联 comment）并关闭对应 Project/milestone 且清理临时 refs, 且 SHALL 使 v0.7 FAKE-90..FAKE-103 事件在反例测试中被拒绝`
- 来源: [3.7 / seed 问题 9]
- 说明: 以可审计的关闭与归档完成生命周期，杜绝 silent fake 回退。

## 5. 范围、约束与例外

- **必须保持的产品约束**：
  - candidate 主身份为首次进入 M-VERIFY 时在 clean tree 上冻结的完整 Git commit SHA（Maestro 裁定 T-002 B）；CI head SHA、artifact digest、合同/policy 版本 digest、FULL_F 与 operation plan 均为绑定该 SHA 的附属证据，不合成为 candidate 本身；任一证据未绑定同一 SHA 或漂移即 stale/fail-closed。
  - M-VERIFY 未漂移时复用同 identity 干净 FULL_F，漂移/stale 重跑；本地质量/trace/reach/反 slop/版本/build/artifact 与安装后公开出口验证均由版本化宿主合同声明，Runtime 不硬编码语言/框架/包管理器/构建工具；Prism 对同一 candidate 终审。
  - M-SECURITY 与后续门禁的安全扫描及深度审计由版本化宿主合同/policy 声明，未知或畸形 fail-closed。
  - M-RELEASE 生成绑定 candidate SHA 与当前 artifact/证据/operation plan digests 的 preview（Maestro 裁定 T-002），预览经 `trac release preview` 可审计；仅 Human 经独立 `trac release` 以 release/delay/return 三择一决定放行（Maestro 裁定 T-001 A），决定 append-only 并绑定 preview/candidate，失败门禁或 stale preview 不可绕过。
  - M-PUBLISH 以 write-ahead + idempotency（preview digest + 逐操作键）+ reconcile 执行宿主声明的 merge/tag/artifact/release/deploy 等外部操作，已完成经远端比对不重复；Agent 不执行或模拟不可逆副作用。
  - M-MILESTONE 完成需求到 release trace 闭环、Issue/Project 生命周期、证据归档只读封存与临时 refs 清理；发布已成功而归档失败仅重试收尾，不重复发布。
  - Feature 与两类 hotfix 均需真实发布且保留 M-VERIFY、安全 policy 与 Human gate：post-release 合入 main 并视存在同步活跃 release 分支产生 patch，dev 仅合入活跃分支经 pre-release 渠道。
  - 结构化 Agent 合同：所有角色/assignment kind 的输入与输出使用统一 versioned envelope/schema 与单一确定性解析路径，Prompt/agent/skill/fake/真实 backend/Runtime validator 同引一合同版本，示例由 schema 生成或经真实 validator 校验，派发前 parity 校验，format 与 semantic attempt 分离。
  - 失败证据链路 append-only：last_failure/diagnose_report 等已有机制保留，仅按 review 证明的真实缺口实施最小 delta，不预设复杂 failure_history；普通失败不覆盖富证据，所有角色拥有 consistent evidence 输入合同与可审计消费证明。
  - Archer 语言中性：Archer 选择并物化宿主语言/工具链/依赖/质量守卫/测试/构建/出口合同，Runtime 仅执行合同并消费 normalized result；允许 Archer 犯错并以机器证据回 M-DESIGN 修订。
  - Issue 闭环：真实 run 在 M-REQ-APPROVAL 后创建真实目标仓库 Issue，缺凭据转 needs_attention，不静默 fake；fake 仅显式测试/模拟模式启用；持久化并 API 回读校验 FR/NFR↔repo↔issue 映射，task/commit/report/关闭仅消费权威映射，不猜数字；崩溃不重复创建。

- **非常规要求**：
  - 发布前置从单一 `trac check release-evidence` 扩展为注册的 M-VERIFY/M-SECURITY/M-RELEASE/M-PUBLISH/M-MILESTONE 连续闭环，且单 candidate 绑定所有门禁与副作用——是对 v0.5/v0.6 “止于 M-IMPL 边界” 的有意扩展，用户明确要求（seed “让 feature 与 hotfix 从 M-IMPL 后继续走完…”）。
  - 新增独立 `trac release` Human 门禁交付面（preview 读取与 release/delay/return 三择一，不复用需求评审的 `trac approve`，不依赖 GitHub UI）——为满足 “只有 Human 可 release/delay/return 且不可绕过失败门禁” 的审计与权限边界，属有意新增（Maestro 裁定 T-001 A）。
  - Issue 创建在缺凭据时显式转 `needs_attention` 而非静默 fake——是对 v0.7 FAKE-90..FAKE-103 静默降级的有意纠正（重开 #14）。

- **Out-of-Scope**：
  - 自举 Runtime 安全边界（seed 问题 1）——不进入 v0.8 spec，相关规避仅作仓库开发基础设施。
  - 单次权威测试流水线重发明（seed 问题 5）——WRITE→COLLECT→RED_CHECK→PRISM_REVIEW 已实现，v0.8 仅回归核验；Shield 生产者自检不作权威门禁。
  - nightly result fetch、第二语言 adapter、RFC discussion 新状态、深版 OOB commit、通用多 registry/多部署平台——不在本版本范围，除非为上述发布闭环不可避免的最小依赖并先经 Human 裁定（seed 规格约束）。
  - 第二语言 adapter 的 kernel 泄漏：Python 细节仅属 reference acceptance，不进入 kernel 规范（seed 问题 8）。

## 6. 开放产品决定

无。T-001（`trac release` 独立 Human 门禁与 preview 绑定）已由 Maestro 裁定 A 落地为 `trac release` CLI 三择一且不可绕过失败门禁；T-002（candidate 主身份为 M-VERIFY 冻结 Git SHA，附属证据分别绑定）已由 Maestro 裁定 B 落地为 Git SHA 主身份与证据绑定模型；T-003（reference host 专用真实 GitHub 远程及 needs_attention）已由 Maestro 裁定 A 落地为双宿主真实外部依赖。单 spec <30 的拆分、结构化 envelope 的字段细节、失败证据 review 的具体增量与 Archer 合同的配置文法属 spec/design 阶段的技术收敛，不在本节。

## 7. 必要性与风险

- **既有能力**：v0.7 已交付可信测试证据与单一 canonical quality guard registry 及其三处 parity 校验、WRITE→COLLECT→RED_CHECK→PRISM_REVIEW 的权威测试链与 opaque adapter 合同（BS-14）、以及以真实安装/contract/Runtime 路径动态创建最小 Python/pytest 宿主 demo 的能力（Maestro 裁定 A）；v0.6 已交付 hotfix 的 HOTFIX-TRIAGE、隔离 `fix/{issue}` 分支、DELTA 设计/测试计划、M-IMPL 边界与 stale reconcile；v0.5 已交付 live evidence 的 candidate SHA（Git HEAD）与 artifact SHA 绑定、FR/NFR↔issue 映射的 GitHub Issues 能力雏形、以及 `trac run/status/replay/report/check` 的事件审计链。可复用为 M-VERIFY 的冻结/复用与本地门禁、M-RELEASE 的 preview/Human 门禁、M-PUBLISH 的幂等 reconcile、以及 Issue 闭环的映射与审计。
- **冲突**：本 story 将 v0.5/v0.6 “止于 M-IMPL 边界”的实现边界扩展为注册的 M-VERIFY 与发布四阶段（M-SECURITY/M-RELEASE/M-PUBLISH/M-MILESTONE），与既有spec中 “不实现 M-VERIFY/M-RELEASE” 的范围排除形成有意演进，需在 spec 中以 “修改/替换” 标注对 FR-0160/FR-0246/FR-0254 等的变更基线而非叠加；Issue 真实创建对 v0.7 静默 fake 的纠正（FAKE-90..FAKE-103 转反例）与既有 fake-backend 开发便利性存在张力，需以 “显式测试/模拟模式才允许 fake” 的契约隔离。
- **重要风险**：
  - 若单 spec 无法在 <30 项 内完整覆盖问题 2/3/4/6/7/8/9 的最小完备 FR 集（seed 约束 “优先单 spec，<30，证明无法容纳才提案拆分”），需提出拆分并等待 Human 裁定，发布闭环的 spec 交付将延期——Sage 须先做容量分析。
  - 若宿主合同/policy 未声明或声明不全，M-VERIFY/M-SECURITY 将大面积 fail-closed 导致发布不可达——Archer 对首个真实宿主与 reference host 的合同物化必须可修订回流（BS-14）。
  - 若 reference host 的专用真实 GitHub 远程或凭据长期不可用（Maestro 裁定 T-003 A 的 needs_attention），双宿主 6 旅程（2 宿主×3 旅程）的验收将常驻阻塞——需为 CI 与验收提供显式的凭据/网络指引与重试/reconcile 演练。
  - 若 Human 长时未在 `trac release` 作三择一决定，或 preview 因新 commit/门禁变化 stale，发布将停留 `awaiting_release` 并需重生成 preview——需在 `trac status/report` 明确 “stale 原因与下一步”。
  - 若外部远端的实际状态与本地事件不一致（manual tag/push、并发发布），M-PUBLISH 的 reconcile 必须以远端为准避免重复或错过——幂等键设计与远端 readback 须覆盖该边界。
