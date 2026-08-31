---
acc_id: ACC-008
created: 2026-08-30
status: draft
sha: f820a848cc41a43266dabd9454d16e231e66a246f5ab2d841148579788830f01
---

# 可信发布闭环与流程合同收敛（v0.8） — 验收标准

## FR-0267 M-VERIFY 注册与 candidate 主身份冻结

### AC-FR0267-01

  - feature 或 hotfix run 在 `M-IMPL` 产出干净 `FULL_F` 后执行 `trac run`，事件流出现 `stage.entered(M-VERIFY)`，随后出现 `candidate.frozen` 事件且 payload 含 `candidate_sha` 为冻结时 `git rev-parse HEAD` 完整 SHA 与 `clean_tree=true`
  - `trac status` 报告 `stage=M-VERIFY candidate=<full SHA>`，`trac replay/report` 可审计 `candidate.frozen` 事件；后续 `trac run` 的全部门禁、preview 与外部操作事件均携带同一 `candidate_sha`

### AC-FR0267-02

  - 进入 `M-VERIFY` 时工作树非 clean（存在未提交变更或已跟踪文件 dirty），`trac run` 不进入 `M-SECURITY`，事件流不出现 `candidate.frozen` 的成功冻结，`trac status` 报告 `needs_attention` 且显示 `reason=dirty_tree` 与恢复指引（提交或清理后重试）
  - 该 dirty 候选未被记录为有效 candidate，主身份不被后续 preview 或外部操作引用

### AC-FR0267-03

  - 冻结后任一证据未绑定同一 `candidate_sha`、发生漂移或 stale（新 commit 产生、分支 HEAD 变化），后续门禁或 preview 生成判定为 `stale`，事件流出现 `candidate.stale` 或门禁 `blocked` 且 `trac status` 报告 `stale` 原因，发布不继续
  - `trac replay` 重建冻结身份与已落事件，已冻结事件不重复生成

## FR-0268 FULL_F 复用判定与 LOCAL_GATES 重跑

### AC-FR0268-01

  - `candidate` 未漂移且与 `M-IMPL` 干净 `FULL_F` 的 `identity` 一致且无 `STALE`（`tree ∧ command ∧ env ∧ selection_id` 四元组全一致且无 `STALE` 传播），`trac run` 在 `M-VERIFY` 落 `evidence.reused kind=full_f` 事件，payload 含 `candidate_sha` 与 `identity_basis`，`trac status` 报告 `full_reuse=full_f`，且本地不重跑 `FULL`
  - `trac replay/report` 可审计被复用 `FULL_F` 的 `evidence identity`

### AC-FR0268-02

  - `candidate` 漂移、`identity` 不一致或含 `STALE` 时，`trac run` 不出现 `evidence.reused kind=full_f`，改为落 `full.executed` 事件并按宿主合同声明的 `LOCAL_GATES` 重跑 `FULL`，`trac status` 报告 `full_rerun` 及判定依据（`drift`/`stale`/`identity_mismatch`）

### AC-FR0268-03

  - `STALE` 证据不被复用为通过依据：含 `STALE` 标记的 `FULL_F` 证据即使 `candidate_sha` 相同，复用判定仍为 `blocked` 或进入重跑分支，`trac replay` 显示该证据被标记 `stale` 且未产生 `evidence.reused`

## FR-0269 宿主合同本地验证执行与未知/畸形 fail-closed

### AC-FR0269-01

  - Archer 在 `M-DESIGN` 物化的版本化宿主合同声明全部本地验证（质量、trace/reach/反 slop、版本、build/artifact、安装后公开出口），`trac run` 在 `M-VERIFY` 依次执行并落 `local_gate.passed` 事件（每项含 `kind`、`candidate_sha`、`contract_digest`、`normalized_result`），全部通过后方可进入下一门禁，`trac status` 显示各验证 `passed`
  - `trac replay` 可审计每项验证的输入 `contract_digest` 与 `normalized result`；`trac validate --file host-contract.toml` 对合同校验通过

### AC-FR0269-02

  - 任一项未声明、未知或畸形（缺工具、缺 `config digest`、超时、输出无法按 `normalized result` 解析），`trac run` 落 `local_gate.failed` 事件，`trac status` 报告 `blocked` 及失败项 `reason=unknown|malformed|missing_contract`，不进入 `M-SECURITY`，且不猜测或降级通过
  - 合同变更导致已落验证 `stale` 时，`trac run --resume` 重跑对应验证而非跳过

## FR-0270 GitHub required CI API 回读与候选绑定

### AC-FR0270-01

  - `M-VERIFY` 执行 GitHub required CI 校验时，事件流出现 `ci.run_observed` 事件且 payload 含 `repo`、`workflow`、`run_id`、`head SHA`、`candidate SHA` 与 `api_verified=true`，`head SHA == candidate SHA` 且运行结论为成功，`trac status` 报告 `ci=bound`，校验通过后方可进入 Prism 复审
  - `trac replay/report` 可审计 API 回读的四元组绑定

### AC-FR0270-02

  - `head SHA` 与 `candidate SHA` 不一致、缺失、非 `required` 或 `stale`（`head SHA` 非当前 `candidate`），`trac run` 落 `ci.run_observed status=failed` 且 `trac status` 报告 `ci=mismatch|missing|stale` 并 `blocked`，不进入发布
  - 不一致的 CI 证据不被后续 `preview` 或外部操作引用

### AC-FR0270-03

  - 缺凭据或网络不可用时，`trac run` 不落成功的 `ci.run_observed`，`trac status` 报告 `needs_attention` 且显示 `reason=missing_token|network_error` 与恢复指引（`export GITHUB_TOKEN=…; trac run --resume`），不静默视为通过或回退为本地校验
  - 凭据恢复后 `trac run --resume` 重试 API 回读，成功后 `ci.run_observed` 出现且状态转 `bound`

## FR-0271 Prism 同 candidate 最终一致性复审

### AC-FR0271-01

  - 本地与 CI 门禁通过后，`trac run` 派发 Prism 对同一 `candidate` 做最终一致性复审，事件流出现 `prism.verdict pass candidate=<SHA>`，`prism` 输入证据集合含 `FULL_F identity`、`local_gate` 结果、`ci.run_observed` 与合同/policy 版本 `digest` 且均指向同一 `candidate SHA`，`trac status` 报告 `prism=pass`，方可进入 `M-SECURITY`

### AC-FR0271-02

  - `prism.verdict` 为 `failed` 或 `revise` 时，`trac run` 不进入 `M-SECURITY`，`trac status` 报告 `prism=failed` 及失败原因，`trac replay` 可审计复审输入与 `verdict` 绑定 `candidate SHA`
  - 修复缺口后按 FR-0286 就地修复（见 FR-0286），新 commit 产生时新 `candidate` 重走 `M-VERIFY`，旧 `candidate` 证据全部 stale

### AC-FR0271-03

  - `prism.verdict=revise` 时 `trac discuss query --file <spec>` 存在 Lex/Prism 锚定的阻塞性发现线程；无锚定线程时 `trac run` 判 `revise_without_findings` 失败，`trac status` 报告 `blocked: revise_without_findings`，不计为有效阻断，`trac replay` 显示该失败
  - 该检查沿用 `trac discuss` 协议：`revise` 必须经 `trac discuss start` 锚定，`verdict=revise` 无对应开放线程时即失败

## FR-0272 M-SECURITY 合同化安全扫描与深度审计

### AC-FR0272-01

  - `trac run` 进入 `M-SECURITY` 后按版本化宿主合同/policy 声明的安全扫描与深度审计清单执行，事件流出现 `security.assessed` 事件且 payload 含 `policy_digest`、`candidate SHA` 与 `status=passed`，`trac status` 报告 `security=passed`，方可进入 `M-RELEASE`
  - `trac replay` 可审计策略版本、阈值与 `normalized result`

### AC-FR0272-02

  - 策略未声明、结果未知、缺失或畸形（策略版本不匹配、输出无法解析），`trac run` 落 `security.assessed status=failed|unknown` 且 `trac status` 报告 `security=failed|unknown` 并 `blocked`，不进入 `M-RELEASE`，且不推断或跳过
  - 安全失败按 FR-0286 就地修复（依赖 CVE 由 Archer 咨询评估换版本/换库，合同级缺陷受控修订），修复后新 commit 产生时新 `candidate` 重走 `M-VERIFY`；安全 finding 零 known issue，`M-SECURITY` 未通过时 `trac release --action release` 一律拒绝；架构级安全问题发布阻断于 `M-SECURITY`，经 FR-0287 逃生门人工处置

## FR-0273 M-RELEASE preview 生成与绑定

### AC-FR0273-01

  - `M-RELEASE` 阶段 `trac run` 聚合 `candidate SHA`、已验证的 `CI/artifact/合同与 FULL_F 证据`、风险与计划，落 `release.previewed` 事件且 payload 含 `preview_digest = hash(candidate SHA + artifact digest + 证据 digests + operation plan digest + 合同/policy digest)`，进入 `AWAITING_RELEASE`，`trac status` 报告 `stage=awaiting_release preview_digest=<sha> candidate=<sha>`
  - `trac release preview` 与 `trac report` 显示 `candidate SHA`、`preview_digest`、`artifact/证据/operation plan digests`、风险/计划摘要与 `status=awaiting_release`

### AC-FR0273-02

  - 后续任一前置证据漂移、新 commit 产生或 `operation plan` 变更导致聚合值变化，`trac release preview` 与 `trac status` 报告 `status=stale` 且 `stale_reason` 含漂移项（`candidate drift` / `evidence staled` / `operation plan changed`），该 stale preview 不可用于 `trac release --action release` 授权
  - 重生成 `preview` 落新 `release.previewed` 事件，旧 `preview_digest` 不被覆盖

## FR-0274 独立 Human 三择一发布门禁

### AC-FR0274-01

  - `preview` 处于 `awaiting_release` 且全部前置门禁通过且 `preview` 未 stale 时，Human 在终端执行 `trac release --action release`，事件流出现 `release.decided action=release candidate=<SHA> preview_digest=<sha>`，`trac status` 报告 `decision=release` 及绑定，发布进入 `M-PUBLISH`
  - `trac replay/report` 可审计 `release.decided` 与 `preview_digest/candidate` 绑定；`trac approve` 与 GitHub UI/comment 不产生 `release.decided` 事件

### AC-FR0274-02

  - 前置门禁失败或 `preview` stale 时执行 `trac release --action release`，`trac release` 退出非零，终端显示 `error: release rejected — gate failed or preview stale` 且不落成功的 `release.decided`，状态保持 `AWAITING_RELEASE`，`trac status` 报告 `rejected: gate failed or preview stale` 与下一步（`trac release preview`）
  - 该拒绝不产生阶段转移，发布不进入 `M-PUBLISH`

### AC-FR0274-03

  - Human 执行 `trac release --action delay --reason "…"` 时落 `release.decided action=delay`，`trac status` 报告 `decision=delay` 并保持 `DELAYED`，操作者经 `trac release preview` 看到重生成指引，重生成后可回到 `AWAITING_RELEASE`
  - Human 执行 `trac release --action return --to M-DESIGN --reason "…"` 时落 `release.decided action=return target=M-DESIGN`，发布回到指定上游阶段，目标之后的证据与 `operation plan` 标记 `stale`

### AC-FR0274-04

  - `delay` 与 `return` 亦绑定当期 `preview_digest/candidate` 并校验 `stale`：`preview` stale 时 `trac release --action delay|return` 退出非零且提示 `preview stale, run trac release preview`，不落对应 `release.decided`
  - 交付面独占性：执行 `trac approve` 或在 GitHub UI 评论/tag 操作不产生 `release.decided`，`trac replay` 中无对应发布授权事件

## FR-0275 M-PUBLISH 外部操作幂等执行与远端 reconcile

### AC-FR0275-01

  - `M-PUBLISH` 阶段 `trac run` 对宿主合同声明的外部操作清单（merge/tag/artifact/release/deploy 等）先落 `publish.planned` 事件（含 `operation_kind`、`target`、`preview_digest`、`idempotency_key = hash(preview_digest + operation_kind + operation_target)`），再执行并落 `publish.executed status=done idempotency_key=<sha>`，`trac status` 报告 `publish=planned|executing|done` 及幂等键
  - 远端状态（`git ls-remote` / tag 存在性 / GitHub release/artifact API 回读）与本地事件互验一致

### AC-FR0275-02

  - 已完成副作用经远端比对存在时，`trac run --resume` 落 `publish.executed status=reconciled_skip idempotency_key=<same>`，不重复执行外部操作，`trac replay` 显示该操作的 `reconciled_skip` 与远端比对结果

### AC-FR0275-03

  - Agent（Devon/Shield）试图执行或模拟不可逆外部操作时，`trac run` 判 `blocked` 且事件流出现 `publish.blocked reason=agent_forbidden`，不产生 `publish.executed` 的成功记录，外部远端无对应副作用

### AC-FR0275-04

  - 外部操作声明未知、缺失或畸形（未在宿主合同/policy 声明或 `operation_target` 非法），`trac run` 落 `publish.failed reason=unknown_operation|malformed` 且 `trac status` 报告 `blocked`，发布不继续
  - 远端不一致（人工 `tag/push`、并发发布）以远端为准，相同幂等键下 `publish.executed` 不重复产生 `done`，必要时落 `reconcile_conflict` 可审计

## FR-0276 M-MILESTONE 归档与生命周期闭环

### AC-FR0276-01

  - `M-PUBLISH` 完成后 `trac run` 进入 `M-MILESTONE`，落 `milestone.trace_closed trace_digest=<sha>` 事件且 trace 含 `approved AC→test-plan/collected node→candidate→FULL_F→CI→Human approval→external operation→release` 闭环，随后落 `milestone.sealed` 与 `refs.cleaned`（临时 `refs/trac/tmp/*` 已清理），`trac status` 报告 `terminal=released`，`trac report` 可导出不可变 `release.trace` 并与外部 `tag/release` 互验一致
  - `Project/milestone` 显示 `closed`，临时 refs 经 `git for-each-ref refs/trac/tmp` 查询为空

### AC-FR0276-02

  - 发布已成功而归档失败时，`trac run` 进入 `RETRY_TAIL`，`trac status` 报告 `retry_tail`，`trac run --resume` 仅重试收尾（`milestone.trace_closed` → `milestone.sealed`），事件流不出现重复的 `publish.executed status=done`（已成功的外部发布经 `reconciled_skip` 跳过）

## FR-0277 Feature 与两类 hotfix 真实发布旅程

### AC-FR0277-01

  - 操作者执行 `trac start <version>` 创建 feature 并 `trac run` 走完全旅程，`M-PUBLISH` 后 `git log` 显示合入 `main`、 `git tag` 含公开 tag、`GitHub release` 存在且 `trac report` 的 `release.trace` 验证 `CI/candidate/artifact/Human approval/外部 operation` 同一 `candidate SHA`，`trac status` 终态 `released` 且 `release_tag` 为公开版本

### AC-FR0277-02

  - 操作者执行 `trac hotfix <issue> --scenario post-release` 创建 post-release hotfix，`M-PUBLISH` 后 `git log` 显示合入 `main`，且当存在活跃 `release` 分支时 `git log` 显示同步 `merge` 至活跃分支并产生 `patch` tag/release（语义由宿主 `version contract` 声明），`trac replay` 显示预验证、`M-VERIFY`、安全策略与 `Human release` 均经同一 `candidate SHA` 绑定

### AC-FR0277-03

  - 操作者执行 `trac hotfix <issue> --scenario dev` 创建 dev hotfix，`M-PUBLISH` 后 `git log` 显示仅合入活跃 `release` 分支，`git tag` 不含公开 tag、`GitHub release` 不含公开 release，交付经 pre-release/开发渠道，`trac report` 显示 `channel=pre-release` 且无公开 `release_tag`

### AC-FR0277-04

  - 三旅程均受同一 `candidate SHA` 绑定与逐操作幂等约束，已合入/已发布后 `trac run --resume` 经远端 `reconcile` 不重复 `tag/release`，事件流对应操作显示 `reconciled_skip`
  - 无活跃 `release` 分支时执行 `trac hotfix --scenario dev`，`trac hotfix` 退出非零，`trac status` 报告 `precheck failed: no active release branch`，不建 `fix/{issue}` 分支

## FR-0278 结构化 Agent 合同统一 envelope 与单一解析

### AC-FR0278-01

  - 任意角色与 `assignment kind` 的输入 `assignment/evidence` 与输出 `outcome/verdict` 均以统一 envelope 落事件，`envelope` 含 `kind` 与 `version` 字段，`trac replay/report` 显示单一确定性解析路径；示例与 fixtures 由 `schema` 生成或经真实消费者 `validator` 校验通过，`trac validate` 对其校验通过

### AC-FR0278-02

  - 输入缺少 `kind/version`、未知版本或游离手写 JSON（未由 `schema` 生成且未通过 `validator`），派发或回收时落 `format_error` 事件，`trac status` 报告 `format_error` 且业务状态不变更，无 `red.validated` 或阶段推进产生

## FR-0279 合同版本 parity、format/semantic 分离与畸形回归

### AC-FR0279-01

  - 派发前 `Prompt/agent/skill/fake backend/真实 backend/Runtime validator` 引用的合同版本不一致时，`trac run` 派发前落 `dispatch.rejected reason=version_parity_mismatch` 事件，`trac status` 报告 `blocked: version parity mismatch`，不进入 Agent 执行，业务事件流无新增 `outcome`

### AC-FR0279-02

  - `malformed` 输入落 `format_error` 事件，语义 `attempt` 失败落 `semantic_attempt_failed` 事件，两者在 `trac replay` 中分离且各自可审计；`format_error` 不被计为 `semantic_attempt`，不污染业务状态（无 `publish` 或 `release.decided` 因 `format_error` 产生）

### AC-FR0279-03

  - 以 `v0.7` 全部 `malformed response` 类故障为回归语料，重复注入该类畸形 envelope 输入时均落 `format_error` 且 `blocked`，`trac report` 显示回归覆盖可审计

## FR-0280 失败证据端到端 review 与一致可审计消费

### AC-FR0280-01

  - 失败产生时事件流依次出现 `failure.emitted` → `failure.stored append-only` → `failure.selected`（含 `round/source` 选择规则）→ `failure.injected`（注入目标 `assignment`）→ `failure.consumed` → `failure.acked` → `failure.invalidated`，`trac replay/report` 可逐环节回放；`last_failure` / `diagnose_report` 等已有机制若存在则保留且可被对应 Agent 消费
  - 普通失败发生时仍需消费的富证据（`last_failure` / `diagnose_report` 未 `ACK`）不被静默覆盖，事件流中该富证据的 `stored` 记录仍可被后续 `selected/injected` 引用

### AC-FR0280-02

  - 所有相关角色的 `evidence` 输入合同含一致的 `round/source` 选择规则，`trac replay` 显示同一 `round` 的 `failure.selected` 在各角色间一致；消费证明（`consumed`/`acked`/`invalidated`）可在事件流中逐角色审计
  - `review` 未发现真实缺口时不产生额外 `failure_history` 复杂改动，事件流无新增 `failure_history` 相关事件

### AC-FR0280-03

  - `failure` 证据丢失或错配（`injected` 与 `stored` 不一致、富证据被覆盖），`trac run` 判 `review_failed` 且 `trac status` 报告 `blocked: evidence lost or mismatched`；重放后状态与中断前一致

## FR-0281 Archer 语言中性 scaffold 与首构合同物化及容错回流

### AC-FR0281-01

  - 新 host 执行 `trac init` 或首个 feature 进入 `M-DESIGN` 时，Archer 物化版本化 `host-contract`（`project.toml`/`host-contract.toml`），文件含 `language`、`toolchain`、`依赖安装`、`质量守卫 pinned tool/config digest/scope/threshold`、`测试 collect/run_selected`、`build/artifact`、`安装后出口 smoke`，事件流出现 `host_contract.materialized`，`trac validate --file host-contract.toml` 校验通过

### AC-FR0281-02

  - `trac run` 仅按 `host-contract` 执行命令并消费版本化 `normalized result`，`trac replay` 显示执行命令与 `host-contract` 声明一致；未知语言/框架或畸形 `normalized result` 时落 `host_contract.invalid` 事件，`trac status` 报告 `blocked: unknown language or malformed result`，不进入后续门禁

### AC-FR0281-03

  - 安装、编译/import、`collect`、`build` 或 `smoke` 失败时，事件流出现 `host_contract.failed` 且 payload 含 `exit code/stdout/stderr/normalized result` 机器证据，`trac status` 报告 `needs_attention: host_contract revise`，发布回流至 `M-DESIGN` 由 Archer 修订，`trac run` 不猜正确命令
  - 修订后 `host-contract` 重新物化并经 `trac validate` 通过，方可继续

## FR-0282 Python reference host 真实物化验收与宿主机同等外部闭环

### AC-FR0282-01

  - 从零创建全新 Python `reference host` 并执行 `trac init` + `trac run` 走完全旅程（`M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH→M-MILESTONE`），事件流出现与 tracks 自身同构的 `candidate.frozen`/`evidence.reused|full.executed`/`local_gate.*`/`ci.run_observed`/`prism.verdict`/`security.assessed`/`release.previewed`/`release.decided`/`publish.*`/`milestone.sealed` 链路，`trac report` 显示 `candidate/CI/artifact/Human approval/外部 operation` 同一 `candidate SHA`
  - `trac status` 在 reference host 上报告 `terminal=released`（feature）或对应旅程终态

### AC-FR0282-02

  - `reference host` 绑定专用真实 GitHub 远程，凭据或远程不可用时 `trac status` 报告 `needs_attention: missing_credentials|remote_unavailable`，`trac report` 显示该旅程 `status=needs_attention`，不计为通过，且不降级为本地成功（无 `release.decided` 因本地校验产生）

### AC-FR0282-03

  - Python 细节（`venv/pytest/wheel` 等）仅在 `reference host` 的 `acceptance` 与 `trac replay` 的 `host_contract` 执行细节中出现，`kernel` 规范与 `trac validate` 的 `host-contract` schema 中无 `python` 硬编码字段，`trac validate` 对 `kernel` 无语言语义引用校验通过

## FR-0283 Issue 真实创建、映射持久化与 API 回读校验

### AC-FR0283-01

  - 真实 run 通过 `M-REQ-APPROVAL` 后首个 `task` 派发前，事件流出现 `issue.created` 且 `GitHub Issue` 经 API 回读存在，事件含 `repo`、`issue id/number/url` 与 `baseline digest` 的 `FR/NFR ↔ repo ↔ issue` 映射，`trac report` 显示 `issue_map: FR-0267→acme/host#123 api_verified=true`，`trac status` 不再报告 `needs_attention`
  - 后续 `task`、`commit`（`Tracks-Task` trailers 含 `issue#`）、`report` 均引用该权威映射的 `issue number`

### AC-FR0283-02

  - 缺凭据或网络不可用时不产生 `issue.created` 的成功记录，`trac status` 报告 `needs_attention: issue_creation missing_credentials repo=acme/host reason=missing_token next="export GITHUB_TOKEN=…; trac run --resume"`，且不产生 `FAKE-N` 映射
  - 凭据恢复后 `trac run --resume` 重新创建并出现 `issue.created` 与 `issue.mapped api_verified=true`

### AC-FR0283-03

  - `fake backend` 仅在显式测试/模拟模式（`TRAC_FAKE_*` / `--assignment-overlay` / `assignment_simulation=true`）下产生 `FAKE-N` 映射，真实模式下产生 `FAKE-90..FAKE-103` 类事件时，`trac run` 判 `blocked` 且 `trac status` 报告 `blocked: fake_not_allowed`，`trac replay` 显示 `issue.mapped api_verified=false`

### AC-FR0283-04

  - `issue.mapped` 事件 `append-only` 持久化并经 API 回读校验（`repo + issue id/number/url + baseline digest` 一致），`trac replay` 可审计 `issue.created` → `issue.mapped api_verified=true`
  - 崩溃后 `trac run --resume` 以 `repo + baseline digest` 去重键幂等校验，已创建 Issue 不重复创建（`GitHub API` 中同一 `baseline digest` 的 Issue 不出现重复 `number`），事件流不出现重复 `issue.created` 相同映射

## FR-0284 Issue/Project/milestone 关闭时机与审计及反例保障

### AC-FR0284-01

  - `M-MILESTONE` 关闭时事件流出现 `issue.closed` 且 `GitHub Issue` 经 API 回读为 `closed`，`issue` 评论含 `release trace` 关联（`candidate SHA` / `preview_digest` / `release_tag`），同时出现 `project.closed` / `milestone.closed`，临时 `refs/trac/tmp/*` 经 `git for-each-ref` 查询为空，`trac report` 显示 `project=fake-project` 未出现且关闭事件与 `release.trace` 互验一致

### AC-FR0284-02

  - 以 `v0.7` `FAKE-90..FAKE-103` / `project=fake-project` 事件为反例输入的测试中，`trac run` 判 `blocked` 且事件流出现 `fake_rejected`，`trac report` 显示 `issue.mapped` 缺失或 `api_verified=false`，该反例不产生成功的 `issue.closed` 或 `milestone.sealed`

## FR-0285 单次权威测试流水线回归核验

### AC-FR0285-01

  - `M-TEST` 阶段 `trac run` 的事件流依次出现 `WRITE` → `COLLECT` → `red.validated`（`Runtime RED_CHECK`）→ `prism.verdict`（`Prism REVIEW` 且仅做隔离 `counterexample kill`），`trac replay` 显示该链路完整且 `prism.verdict` 消费 `red.validated` 证据
  - 任一环节缺失时 `trac run` 判 `pipeline incomplete` 且 `trac status` 报告对应缺失环节

### AC-FR0285-02

  - `Shield` 的生产者自检事件（若存在）不产生 `red.validated` 或 `prism.verdict` 的权威门禁通过记录，`trac status` 不以 `Shield` 自检作为 `M-TEST` 通过依据；普通 `FULL` 套件重跑不替代 `RED_CHECK` 链路，事件流中 `M-TEST` 期间无 `full.executed` 冒充 `red.validated`

### AC-FR0285-03

  - 回归核验仅验证既有链路保持，不产生新流水线定义：`trac validate` 对流水线定义无新增阶段，`trac report` 显示流水线版本与 `v0.7` 一致

## FR-0286 发布闭环就地修复与 Known Issue 政策

### AC-FR0286-01

  - `M-VERIFY`/`M-SECURITY`/`M-PUBLISH`/`M-MILESTONE` 发现缺陷时，`trac run` 不自动回退至 `M-DESIGN`/`M-PLANNING`，事件流不出现 `stage.rolled_back` 至 `M-DESIGN` 的自动回退；分类仅决定由谁修（`Devon` 实现/`Shield` 定点测试/`Archer` 咨询合同），`trac status` 报告 `repair=in_place round=<n>/3`，`trac replay` 显示修复职责与纪律
  - `trac discuss query` 无对应自动回退的 `stage.rolled_back` 事件

### AC-FR0286-02

  - 行为缺陷修复按 `RED-first`：新增 `unit` 回归复现（不改冻结 `int/e2e`），事件流出现新增 `unit` 测试的 `red.validated` 后变绿；门禁缺陷修复按 `verification-only`：修复后重跑该 `gate` 即证明，事件流不出现人造无意义失败测试的 `red.validated`；依赖 `CVE` 由 `Archer` 经咨询派发评估换版本/换库（不回阶段），`Devon` 执行；合同级缺陷经受控合同修订（`delta` 文档+评审）不回阶段，`trac replay` 可审计
  - 冻结 `int/e2e` 在修复前后对比无改动（除 `Shield` 定点新增外），`git diff` 显示 `tests/integration` 与 `tests/e2e` 原冻结文件未被修改

### AC-FR0286-03

  - 修复后产生新 `commit` 时，事件流出现 `evidence.staled reason=fix_new_candidate` 且旧 `candidate` 的 `FULL_F`/`CI`/`preview`/`Human 决定` 标记 `stale`，`trac status` 报告 `candidate` 为新 SHA 且 `full_reuse` 重新判定，`trac run` 完整重走 `M-VERIFY`（出现新 `candidate.frozen`），旧 `preview_digest` 不被复用

### AC-FR0286-04

  - 就地修复预算（默认 3）穷尽且 `Prism` 确认归因不变，或修复需变更冻结 `interfaces`/`AC` 且超出受控合同修订可承载范围，或外部依赖无可用修复时，`trac status` 报告 `blocked: irreparable`，可经 Known Issue 登记或 `trac return`/`trac abandon`（FR-0287）处置

### AC-FR0286-05

  - `Prism` 确认产品质量缺陷归因后，`GitHub Issue` 出现 `known-issue` 标签且关联 `candidate` 与证据，`trac release preview` 显示 `known_issues=[{issue=acme/host#45 waiver=AC-FRXXXX}]`，`trac status` 报告 `known_issues` 列表；未在 `preview` 列出的未修复缺陷不允许存在（`trac release --action release` 拒绝并提示 `known_issue not listed`）
  - `M-MILESTONE` 的 `release.trace` 对 waiver `AC` 标记 `waived` 并关联下版 `backlog`，`trac report` 显示 `waiver` 语义；下版 `triage` 的 `trac replay` 显示消费该 `known-issue`

### AC-FR0286-06

  - 发布机制本身失败（`artifact`/`tag`/`CI`/`registry` 无可用修复）不适用 Known Issue，`trac status` 报告 `blocked: publish mechanism failure` 且 `known-issue` 登记被拒绝（`trac replay` 显示 `known_issue rejected: not product defect`）
  - `run` 内修复不衍生新 `hotfix` run（单活跃 run 原则保持），事件流不出现新 `hotfix.requested`，`trac hotfix` 仍需 `trac start` 另起；安全 finding 未修复时 `trac release --action release` 一律拒绝（`trac status` 报告 `blocked: security not passed, zero known issue`），架构级安全问题 `run` 停于 `M-SECURITY` 经 `trac return`/`trac abandon` 人工处置

## FR-0287 Human 逃生门：指针回拨与终止出口

### AC-FR0287-01

  - 任意可回拨阶段（`M-VERIFY`/`M-SECURITY`/`M-RELEASE`/`AWAITING_RELEASE`/`M-PUBLISH`/`M-MILESTONE`）执行 `trac return --to M-TEST --reason "…"` 或 ` --to M-IMPL` 等，事件流出现 `human.return actor=Human from=<source> to=<target> reason=…`，`trac status` 报告 `human_return` 及 `evidence.staled` 清单；`Runtime` 自动策略不阻止 `Human` 回拨，`trac run` 接受该回拨并移动指针
  - `Agent` 咨询（`Prism`/`Archer` 影响评估）仅产生 `advisory` 事件，不改变阶段状态，`trac replay` 显示 `advisory` 与 `human.return` 分离

### AC-FR0287-02

  - 回拨建立 `escape barrier`（`cutover sequence`）并 `quiesce`/取消在飞 `dispatch`：`trac replay` 显示 `escape.barrier established cutover=seq< N>`，`barrier` 前派发而后到达的 `outcome` 以 `escape.late_outcome quarantined` `append-only` 审计并禁止 `checkpoint`/`publish` 或覆盖已回拨 `State`，事件流中该 `late_outcome` 不产生 `design.committed` 或 `publish.executed`，`trac status` 显示 `late_outcome=quarantined`

### AC-FR0287-03

  - 回拨到 `T` 时，`T` 之后全部证据（`candidate` 冻结/`FULL_F`/`CI` 绑定/安全评估/`preview`/`release` 决定）落 `evidence.staled reason=human_return`，不删除、可 `replay`，重进时 `trac run` 不复用旧证据而重新执行对应门禁；回拨到 `M-TEST` 之前时 `trac status` 显示测试冻结已解除（`frozen_tests=unfrozen`）

### AC-FR0287-04

  - 回拨跨越已执行不可逆操作（`merge`/`tag`/`artifact`/`release`）时，终端先报告已执行清单（`already_executed=[merge, tag=v0.8.0, artifact=sha256:…]`），`Human` 显式确认后才移动指针；已执行操作落为外部事实，重进 `M-PUBLISH` 经 `reconcile` 识别为 `reconciled_skip`，`trac replay` 显示确认事件与 `reconcile`

### AC-FR0287-05

  - 执行 `trac abandon --reason "…"`（`Human-only`，`foundation task`），事件流出现 `run.completed terminal=cancelled reason=…`，`trac status` 报告 `terminal=cancelled`，不删证据、不碰 `issues`/分支、零外部副作用（`git for-each-ref refs/trac` 无新增外部 `tag`/`branch`，`GitHub Issue` 状态不变），终态 `run` 执行 `trac run` 退出非零且提示 `run is cancelled, use trac start for new run`，重做经 `trac start` 新 `run`

## NFR-0143 Candidate 绑定一致性与全链可审计性

### AC-NFR0143-01

  - 冻结后全链（`M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH→M-MILESTONE`）事件均携带同一 `candidate SHA`，`trac replay/report` 可回放 `candidate.frozen` / `evidence.reused|full.executed` / `local_gate.*` / `ci.run_observed` / `prism.verdict` / `security.assessed` / `release.previewed` / `release.decided` / `publish.*` / `milestone.*` 并验证 `candidate SHA` 一致性
  - 任一证据未绑定同一 `candidate SHA` 或漂移/`stale` 时，`trac report` 的 `release.trace` 校验为 `inconsistent` 且 `trac status` 报告 `blocked: candidate mismatch`

### AC-NFR0143-02

  - `release.trace` 含 `candidate SHA` / `artifact digest` / `evidence digests` / `preview_digest` / `Human approval event id` / `external operation digests` 的同一性证明，`trac report --format md` 可导出该 trace 并与外部 `tag/release` 的 digest 互验一致

## NFR-0144 M-PUBLISH 幂等与远端 reconcile 正确性

### AC-NFR0144-01

  - 相同 `preview_digest` 下重复执行同一 `operation`（`merge/tag/artifact/release/deploy`）时，第二次 `trac run --resume` 落 `publish.executed status=reconciled_skip idempotency_key=<same>`，远端（`git log --all --oneline` / `git tag --list` / `GitHub release list` / `artifact store`）不出现重复 `tag/release/merge` 记录
  - 未完成操作在 `reconcile` 后继续执行并落 `publish.executed status=done`

### AC-NFR0144-02

  - 幂等键相同而远端结果不同（人工 `tag` 覆写、并发发布冲突）时，`trac run` 落 `reconcile_conflict` 事件，`trac status` 报告 `blocked: reconcile_conflict`，不继续发布

## NFR-0145 结构化合同解析确定性与版本一致性

### AC-NFR0145-01

  - 所有 `assignment/evidence/outcome/verdict` 以单一 `envelope(kind/version)+schema` 可判别，无回退启发式：`trac replay` 显示解析路径为 `deterministic` 且 `version` 与 `schema_digest` 一致
  - `Prompt/agent/skill/fake backend/真实 backend/Runtime validator` 引用的合同版本不一致时，派发前落 `dispatch.rejected reason=version_parity_mismatch`，不进入 Agent 执行

### AC-NFR0145-02

  - `format_error` 与 `semantic_attempt_failed` 在事件流中分离且分别可审计，`malformed` 输入不产生业务状态变更（无 `publish` / `release.decided` 因 `format_error` 产生），`trac replay` 显示两者分离

## NFR-0146 失败证据 append-only 与可审计消费

### AC-NFR0146-01

  - 失败证据的产生、保留、选择、注入、消费、`ACK`、失效与 `replay` 在事件流中 `append-only`，`trac replay/report` 可逐环节回放且重放后状态与中断前一致；`last_failure` / `diagnose_report` 等已有机制若存在则保留且可被消费
  - 仍需消费的富证据（未 `ACK` 的 `last_failure`）不被普通失败静默覆盖，事件流中该富证据的 `stored` 记录在普通失败后仍可被 `selected/injected` 引用

### AC-NFR0146-02

  - 所有相关角色的 `evidence` 输入合同含一致的 `round/source` 选择规则，`trac replay` 显示同一 `round` 的 `failure.selected` 在各角色间一致；消费证明（`consumed`/`acked`/`invalidated`）可在事件流中逐角色审计

## NFR-0147 语言中性与未知合同 fail-closed

### AC-NFR0147-01

  - `Runtime` 仅按 `host-contract` 执行并消费版本化 `normalized result`，`kernel` 与 `executor` 代码经 `grep -r "pytest|venv|wheel"` 无语言硬编码（除 `reference host` 隔离目录与测试资产），`trac validate` 对 `kernel` 无语言语义引用校验通过
  - 未知语言/框架或畸形 `normalized result` 时 `trac status` 报告 `blocked: unknown language or malformed result`，不进入后续门禁

### AC-NFR0147-02

  - `Python` 细节（`venv/pytest/wheel`）仅在 `reference host` 的 `host_contract` 执行细节与测试资产中出现，`kernel` 规范与 `host-contract` schema 中无 `python` 硬编码字段，`trac report` 的 `kernel` 执行面不含 `python` 分支

## NFR-0148 Issue 权威映射与静默 fake 拒绝

### AC-NFR0148-01

  - 真实 run 的 `FR/NFR ↔ repo ↔ issue id/number/url ↔ baseline digest` 映射为权威来源，`trac report` 显示该映射 `api_verified=true` 且可与 `GitHub Issue` API 回读互验；`task`/`commit`（`Tracks-Task` trailer）/`report`/关闭 `effector` 的 `issue` 引用与权威映射一致，`trac replay` 显示引用来源为 `issue.mapped`

### AC-NFR0148-02

  - 真实模式下 `FAKE-90..FAKE-103` / `project=fake-project` 事件在 `issue.mapped` 校验中落 `fake_rejected`，`trac status` 报告 `blocked: fake_not_allowed`，该 `fake` 映射不被后续关闭 `effector` 消费

## NFR-0149 双宿主 6 旅程可崩溃恢复与身份一致

### AC-NFR0149-01

  - `tracks` 自身与至少一个全新 `Python reference host`（专用真实 `GitHub` 远程）分别完成 `feature` 与两类 `hotfix` 的适用发布旅程（共 6 旅程），`trac report` 在每宿主上显示 `CI/candidate/artifact/Human approval/外部 operation` 同一 `candidate SHA` 与同一 `preview_digest`，`trac status` 在每旅程终态为 `released`（或 `dev hotfix` 的 `pre-release delivered`），凭据缺失旅程计为 `needs_attention` 不计通过

### AC-NFR0149-02

  - 在 `Issue` 创建、`CI` 观测、`build`、`merge/tag/artifact/release`、归档任一点 `kill -9` 中断后，`trac replay` 重建状态，`trac run --resume` `reconcile` 已完成副作用显示 `reconciled_skip`，未完成继续，远端不出现重复副作用，`trac report` 的 `release.trace` 与中断前一致

### AC-NFR0149-03

  - 错误身份、`stale evidence`、畸形合同、`silent fake fallback` 在双宿主任一注入下均落 `blocked` 且 `trac status` 报告对应 `reason=identity_mismatch|stale|malformed_contract|fake_not_allowed`，`trac report` 显示该失败可审计且不产生成功的 `release.trace`

