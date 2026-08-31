---
architecture_id: ARCH-008
spec_ref: SPEC-008
created: 2026-08-31
status: draft
sha:
---

# v0.8 — 架构：可信发布闭环与流程合同收敛

本文是 ARCH-007 的增量延伸。v0.1～v0.7 的事件溯源、单写者、canonical 阶段、Phase 0 封存、canonical quality guard registry、Test Authenticity Gate、mutation evidence、adapter seam 与既有 CLI 全部保持不变；v0.8 在 M-IMPL 之后注册发布闭环五阶段（M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH→M-MILESTONE），以单一 candidate Git SHA 锚定全部门禁、preview、Human 授权与外部操作，并收敛结构化 Agent envelope、失败证据链与 Issue 闭环。本文不含业务行为；新模块的桩与配置由 §2 Scaffold 宣言交付，行为体是 Devon foundation tasks。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-007

- 唯一生产路径 `cli -> kernel.machine.decide -> executor.execute -> store.append`、SQLite append-only events、投影可重建、单写者锁与 per-kind reconcile 不变。
- `kernel/` 纯控制、`executor/` 副作用、`checks/` 静态闭合、`effects/` 外部通道、`adapters/` 宿主测试框架语义边界与 `cli/` 交付面的分层不变；v0.8 新增 `kernel/release.py`、`kernel/envelope.py`、`executor/{m_verify,host_contract,security,release_gate,publish,milestone,failure_review,reference_host}.py`、`effects/publish.py` 增长点，不新增顶层包。
- v0.7 的 Phase 0 seal、guard registry/parity、authenticity/mutation、candidate-bound closure（`trac check trace --version v0.7`）、双宿主 9 场景 fail-closed 演证不变；v0.8 的 M-VERIFY 消费其产出（干净 FULL_F、registry、closure）作为发布输入证据。
- `.tracks/projects/project.toml` 三层 `collect`/`run`/`run_selected`、`{nodes}`/`{result}` 所有权、`[nightly]`、`[adapter]`、`[layout]`、`[lint]` 逐字不变；v0.8 在同一文件追加 `[host-contract.*]` 段承载发布闭环宿主合同（FR-0281 的 "project.toml / host-contract.toml" 二择一取 project.toml 形态——Runtime scaffold 域只允许既有 project.toml，且单文件避免双合同真相；schema 见 interfaces §1e）。
- 既有 `trac start/hotfix/run/status/replay/report/check/validate/approve/return/recover/retry/triage/review/discuss/init` 语义与 USAGE 不变；唯一新增顶层子命令是 release（**待实现：Devon foundation task 交付 cmd_release 并同步 USAGE 与 validate 的 TRAC_SUBCOMMANDS**；本文以散文形式引用该待实现命令的语法，不加反引号、不入代码 fence，正是 fabricated-command guard 要求的「to-be-created tooling 以 foundation task 标注」形态，同 v0.6 hotfix 先例）。`trac run --resume` 中的 resume 是 run 既有「活跃 run 续跑+reconcile」语义的显式别名 flag，同为待实现 foundation task 项。
- v0.6/v0.7 全部 IF 标识不可重定义或复用；IF-008 只追加新标识。
- wheel 安装、隔离 venv、源码树外 cwd、GitHub Actions stable required checks（lint/coverage/test/deliverables/trace/reach）与 live channel 三层机制不变；milestone `release-evidence` 仍为 tag 硬门禁，v0.8 在其内追加双宿主发布旅程证据（§4.3）。
- hotfix 的 HOTFIX-TRIAGE、`fix/{issue}` 隔离分支、DELTA 设计/测试计划、M-IMPL 边界与 stale reconcile 不变；v0.8 只注册其后的发布旅程（FR-0277）。

### 0.2 v0.8 变更

- 新增 `kernel/release.py`：注册 M-VERIFY/M-SECURITY/M-RELEASE/M-PUBLISH/M-MILESTONE 为 StageDef（SM-01），版本能力门槛 `RELEASE_PIPELINE_VERSION="v0.8"`（feature 与 hotfix 身份均按数值继承）；对进入发布闭环的 run，M-IMPL boundary 不再是终态而进入 M-VERIFY，未达版本门槛的回落路径保持 boundary 终态（既有 test_full_journey_v05 回归守护）。
- 新增 `executor/host_contract.py` + `.tracks/projects/project.toml` 的 `[host-contract.*]` 段：版本化宿主合同（local gates/build/smoke/version/security/ci/tracker/operations），Runtime 仅执行声明命令并消费 `tracks-gate-result` v1 normalized result；`trac validate --file .tracks/projects/project.toml` 增加对 host-contract 段的 schema 校验（**待实现 foundation task**：校验器扩展；既有 canonical 文件路径校验入口不变）。
- 新增 `executor/m_verify.py`：candidate 冻结（clean tree 校验 + 完整 HEAD SHA，Maestro 裁定 T-002 B）、FULL_F 复用判定（注册 v0.6 FR-0254 canonical 语义）、本地门禁执行、GitHub required CI API 回读、Prism 同 candidate 终审派发。
- 新增 `executor/security.py`（M-SECURITY 扫描 + Prism 安全策略复审派发）、`executor/release_gate.py`（preview 聚合与 stale 判定、Human 三择一前置校验）、`executor/publish.py`（write-ahead + 幂等键 + reconcile）、`executor/milestone.py`（trace 闭环、Issue/Project/milestone 关闭、只读封存、临时 refs 清理）、`effects/publish.py`（不可逆外部操作唯一效果边界，Runtime 独占）。
- 新增 `kernel/envelope.py`：统一 `tracks-envelope` v2（kind/version 头 + 单一确定性解析路径），OpencodeBackend/FakeBackend/派发物化/validator 同引一版本；format_error 与 semantic_attempt_failed 分离落事件；派发前 parity 校验。
- 新增 `executor/failure_review.py`：failure.emitted→stored→selected→injected→consumed→acked→invalidated 链与 review reducer；last_failure/diagnose_report 保留为链上 source，富证据在 ACK 前不得被覆盖。
- 扩展 `effects/github.py`：真实 run 缺凭据转 needs_attention（废除静默 fake 回退——v0.7 `select_issue_backend` 的 missing-token 降级路径被删除）、`issue.mapped` 权威映射持久化与 API 回读、`fake_rejected`、milestone/Project 关闭、CI runs API 回读；显式测试/模拟模式（TRAC_FAKE_* / TRAC_AGENT_BACKEND=fake / --assignment-overlay）仍是合法 fake 通道。
- 新增 `executor/reference_host.py` + `tracks/assets/reference_host/**`（wheel 数据资产）：从零创建 Python reference host，绑定专用真实 GitHub 远程（Maestro 裁定 T-003 A），与 tracks 自身同等走发布旅程。
- EVENT_TYPES 追加 §1a v0.8 封闭集成员；COMMAND_KINDS 追加 `freeze_candidate`、`judge_full_f_reuse`、`run_local_gates`、`observe_ci_runs`、`assess_security`、`generate_preview`、`record_release_decision`、`execute_publish`、`close_milestone`、`materialize_host_contract`、`check_envelope_parity`、`review_failure_chain`（Devon foundation tasks 落地）。
- 语言中立收尾（NFR-0147）：kernel/executor 运行时代码中残留的 venv/pytest/wheel 词边界 token（kernel/contracts.py、executor/{executor,test_select,quality_gate,worktree,m_impl_runtime}.py 现存 `.venv/bin/...` 字面量与 guard 命令文本）迁出为合同数据/资产区，扫描 token 扩展为 `pytest|junit|java|venv|wheel|pip`（词边界、大小写不敏感；允许区不变并追加 reference_host 资产区）；Devon foundation task 执行迁移。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | v0.8 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `cli/main.py` | 新增 cmd_release（release preview / --action release\|delay\|return，待实现 foundation task）；status/report/replay 扩展发布字段；validate 增加 host-contract 校验入口 | FR-0267~0277 | IF-RELEASE-003, IF-VERIFY-001 |
| 2 | `kernel/release.py` | 五阶段 StageDef、SM-01 路由与 reducer、release 状态投影 | FR-0267~0277 | IF-VERIFY-001 等 |
| 3 | `kernel/envelope.py` | envelope v2 类型、解析/校验、parity | FR-0278/0279 | IF-ENVELOPE-001/002 |
| 4 | `executor/m_verify.py` | 冻结、复用判定、绑定校验、Prism 终审装配 | FR-0267~0271 | IF-VERIFY-001~005 |
| 5 | `executor/host_contract.py` | 合同加载/校验/执行/normalized result | FR-0281 | IF-HOSTCONTRACT-001/002 |
| 6 | `executor/security.py` | 扫描执行 + 安全复审派发 + 聚合 | FR-0272 | IF-SECURITY-001 |
| 7 | `executor/release_gate.py` | operation plan、preview digest、stale、授权校验 | FR-0273/0274 | IF-RELEASE-002/003 |
| 8 | `executor/publish.py` + `effects/publish.py` | write-ahead、幂等键、reconcile；效果边界独占不可逆操作 | FR-0275 | IF-PUBLISH-001/002 |
| 9 | `executor/milestone.py` | release trace、Issue/Project/milestone 关闭、封存、refs 清理 | FR-0276/0284 | IF-MILESTONE-001, IF-ISSUE-002 |
| 10 | `executor/failure_review.py` | 失败证据链与 review | FR-0280 | IF-FAILURE-001 |
| 11 | `effects/github.py` | needs_attention、issue.mapped、fake_rejected、CI readback、milestone 关闭 | FR-0283 | IF-ISSUE-001 |
| 12 | `executor/reference_host.py` + `assets/reference_host/**` | reference host 物化与验收 | FR-0282 | IF-REFERENCE-001 |
| 13 | `checks/trace.py` | v0.8 release trace 闭环（--version v0.8 追加 release 段） | NFR-0143 | IF-TRACE-003 |

### 1.0.2 M-VERIFY 与 candidate 主身份

M-VERIFY 是 canonical StageDef（非前置门），feature/hotfix run 在 M-IMPL 干净 FULL_F 后经 `trac run` 自动进入。进入时 Runtime 在 clean tree（`git status --porcelain` 对已跟踪文件为空）上冻结完整 HEAD SHA 为 candidate 主身份，落 `candidate.frozen (candidate_sha, clean_tree=true, branch)`；已存在同 run 的成功冻结事件时幂等不重冻（SM-01.17）。dirty 树落 `attention.required reason=dirty_tree`，不进入 M-VERIFY 门禁链。后续所有门禁/preview/外部操作事件必须携带同一 `candidate_sha`；`collect_binding_violations` 纯函数扫描漂移并落 `candidate.stale`。FULL_F 复用判定引用被复用证据的 `identity_basis`（v0.6 四元组 tree ∧ command ∧ env ∧ selection_id 全一致且无 STALE 传播）才落 `evidence.reused kind=full_f`；否则按宿主合同 LOCAL_GATES 重跑 FULL 落 `full.executed`。

### 1.0.3 宿主合同与语言中立（FR-0281/NFR-0147）

`.tracks/projects/project.toml` 的 `[host-contract.*]` 段（schema 见 interfaces §1e）是 tracks 宿主的发布闭环机器真相：`[host-contract]` 语言/工具链声明、`[[host-contract.local_gate]]` 七类（quality/trace/reach/anti_slop/version/build/smoke）、`[host-contract.version_scheme]` 版本方案、`[host-contract.build]`/`[host-contract.smoke]`、`[[host-contract.security_scan]]`、`[host-contract.ci]` required-CI 绑定、`[host-contract.tracker]`、`[host-contract.operations.*]` 三旅程操作计划。Runtime 只执行声明命令（shlex + subprocess(shell=False)、合同内 timeout）并消费 `tracks-gate-result` v1：`exit_code` 通道由 Runtime 合成 normalized result（status=passed|failed + exit/stdout/stderr 摘要），`file` 通道解析 JSON。quality 类 gate 以 `source="guard_registry"` 引用本宿主 canonical registry（§4.2）执行其 canonical 命令——registry 仍是守卫唯一真相，host-contract 段不复制阈值。version 类 gate 以 `source="version_decl"` 由 Runtime 原生执行：按 tag 模板推导目标 tag、断言其远端不存在（冲突 fail-closed）并记录推导输入（{n} 取远端 tag 普查）；可选的 file/key/expect 绑定仅在显式声明时比对（tracks 与 reference host 均不声明，理由见 §3.7）。未声明、未知工具、超时、输出不可解析一律 fail-closed 落 `local_gate.failed`/`host_contract.invalid`，并在 M-VERIFY 前置失败时把 run 停在 `needs_attention: host_contract revise` 回流 M-DESIGN（BS-14 容错修订；Runtime 不猜命令）。语言扫描允许区：`tracks/adapters/**`、`tracks/assets/**`（demo_host 与 reference_host 数据）、`tracks/executor/{demo_host,reference_host}.py`（宿主物化隔离模块）；kernel/cli/executor 其余运行时代码禁 `pytest|junit|java|venv|wheel|pip` 词边界 token（含注释/docstring）。

### 1.0.4 M-SECURITY

按 `[security_scan]` 逐项安装（声明 install）并执行 pinned 工具（pip-audit 2.7.3 依赖审计、bandit 1.8.3 静态安全 lint，均 exit_code 通道、阈值 0），每项落 normalized result 并绑定 candidate；随后派发 Prism 安全策略复审（RP-01 #4：输入为扫描结果 + policy digest + candidate SHA，verdict 落 `prism.verdict scope=security`）；聚合通过才落 `security.assessed status=passed`。策略版本/阈值由合同 pin，未知/缺失/畸形 fail-closed（`status=unknown` 阻断）。修复后同 candidate 重跑 M-SECURITY 落新事件。

### 1.0.5 M-RELEASE preview 与独立 Human 门禁

M-RELEASE 聚合 candidate SHA、artifact digest（build gate 产物的 sha256）、证据 digests（FULL_F identity、各 local_gate、CI 四元组、security）、合同/policy digest 与 operation plan digest，计算 `preview_digest = sha256(canonical_json(上述全部))`，落 `release.previewed` 并进入 AWAITING_RELEASE（blob 存 `.tracks/runtime/blobs/release/{run}/`，content-address）。stale 判定在每次读取时重算聚合值，不一致即 `stale_reason ∈ {candidate_drift, evidence_staled, operation_plan_changed}`；重生成落新事件，旧 preview 不覆盖。Human 经独立 release CLI 三择一（release/delay/return）：决定 append-only 落 `release.decided (action, candidate_sha, preview_digest, reason?, target?)`；Runtime 校验 preview 未 stale 且 M-VERIFY/M-SECURITY 门禁全过，否则拒绝（非零退出 + `release.rejected`，状态不变，SM-01.10）。`trac approve` 与 GitHub UI/comment 不产生 release.decided（交付面独占性）。

### 1.0.6 M-PUBLISH 幂等与 reconcile

operation plan 由 `[operations.{feature,post_release,dev}]` 声明步（语法 KIND:TARGET[:when=COND]）+ 版本事实（{minor}/{n}/{ulid}/{artifact}）解析而成。每步先落 `publish.planned`（write-ahead，含 `idempotency_key = sha256(preview_digest + operation_kind + operation_target)`）再经 `effects/publish.py` 执行，落 `publish.executed status=done`。崩溃恢复按幂等键读远端实际状态（`git ls-remote` / tag 存在性 / release/artifact API 回读）：已完成落 `reconciled_skip`，未完成继续；同键远端结果不同落 `reconcile_conflict` 并 blocked（远端为准）。Agent 路径不可达 `effects/publish.py`（backend 守卫 + manifest 审计）；检测到 Agent 触达落 `publish.blocked reason=agent_forbidden`。发布执行期间 Runtime 在本地创建 `refs/trac/tmp/{run_id}/*` 标记供恢复，M-MILESTONE 清理。

### 1.0.7 M-MILESTONE 与 RETRY_TAIL

`build_release_trace` 纯连接 approved AC→test-plan/collected node→candidate 证据→FULL_F→CI→Human approval→外部 operation→release，落 `milestone.trace_closed (trace_digest)`；随后关闭 Issue（权威映射再校验 + release trace 关联 comment：candidate SHA / preview_digest / release_tag）、关闭 Project/milestone（tracker 声明）、只读封存证据 blob（`milestone.sealed`）、清理 `refs/trac/tmp/{run}/*`（`refs.cleaned`；`git for-each-ref refs/trac/tmp` 为空）。run.completed 落 `terminal_state=released release_tag=...`。发布已成功而归档失败 → `retry_tail`：重试仅限收尾事件，已成功外部操作经 reconciled_skip 跳过（BS-10）。

### 1.0.8 三旅程与版本方案（FR-0277）

feature：operations.feature 全量（merge main、公开 tag、artifact、release）。post-release hotfix：从 `fix/{issue}` 分支经 M-VERIFY 后 merge main；当远端存在与目标 base version 同名的 `release/{minor}` 分支（活跃 release 分支判定，`when=active_release_branch`）时同步 merge 该分支，并按 `[version] patch_line`（现有 patch 最大值 +1）产生 patch tag/release。dev hotfix：precheck 要求活跃 release 分支存在（缺失即 fail-closed 退出非零、不建 fix 分支），仅 merge 活跃分支并打 prerelease_tag（不打公开 tag、不建 release 对象），`trac report` 显示 channel=pre-release。三旅程共享 candidate 绑定与逐操作幂等。

### 1.0.9 Envelope v2 与 parity（FR-0278/0279）

`kernel/envelope.py` 定义 `tracks-envelope` v2：输出线形为全文恰好一个 ```tracks-envelope``` fenced JSON block（envelope 头 kind ∈ 封闭集 + version=2 + payload）；`parse_agent_output` 是唯一解析路径——零个/多个 block、JSON 畸形、缺 kind/version、未知版本均抛 `EnvelopeFormatError` 落 `format_error` 事件（不计 semantic attempt、业务状态不变更）。输入侧 assignment 物化带同一 envelope 头。派发前 `check_envelope_parity` 校验 Prompt/agent/skill/fake backend/真实 backend/Runtime validator 六面引用的合同版本一致（各面携带机器可读 token `tracks-envelope:v2`），不一致落 `dispatch.rejected reason=version_parity_mismatch` 拒绝派发。示例与 fixtures 必须经真实消费者 validator 校验（CI 静态检查）；v0.7 malformed response 故障语料为回归输入。

### 1.0.10 失败证据链（FR-0280）

存储 `.tracks/runtime/failures/{run}/`（append-only 记录，键 (round, source, seq)）；`select_failure` 的轮次/来源选择规则是全部角色共享的单一实现（consistent evidence 输入合同）；注入目标 assignment 的 evidence 段；Agent outcome 携带 `evidence_ack` → `failure.acked`；被新证据取代前必须 ACK 或显式 `failure.invalidated`。`review_failure_chain` 纯 reducer 核验链一致性，丢失/错配落 `review.failed`（blocked: evidence lost or mismatched）。last_failure/diagnose_report 现有机制映射为链上 source，语义保留；review 未发现缺口则零新增改动（不预设 failure_history）。

### 1.0.11 Issue 闭环（FR-0283/0284）

`select_issue_backend` 的静默降级路径删除：真实 run（无显式模拟模式 env）缺 GITHUB_TOKEN/TRAC_GITHUB_REPO 时抛分类错误 → Runtime 落 `attention.required reason=missing_token`（needs_attention，含恢复指引），不落成功 issue.created、不产生 FAKE-N。创建后立即 API 回读（GET /repos/{repo}/issues/{n}）校验，落 `issue.mapped (item_id, repo, issue_number, url, baseline_digest, api_verified=true)`；映射持久化 `.tracks/runtime/issue-map.json`，去重键 `repo + baseline_digest`（崩溃恢复幂等）。真实模式下出现 FAKE-N 产物 → `fake_rejected` + blocked。task/commit（Tracks-Task trailers 的 issue#）/report/关闭 effector 只消费权威映射。M-MILESTONE 关闭时 `close_issues_with_comment` 附 release trace comment，`close_project_milestone` 关闭 tracker 声明的 Project/milestone。

### 1.0.12 Reference host（FR-0282）

`executor/reference_host.py` 是数据驱动物化器：从安装 wheel 的 `tracks/assets/reference_host/**` 逐字节部署 pyproject/flake8/host_calc.py/tests 与 `tracks-project.toml`（含并入的 `[host-contract.*]` 段，→ reference repo 的 `.tracks/projects/project.toml`）、`architecture.md`（→ `.tracks/projects/v0.1/architecture.md`）、`ci.yml`（→ `.github/workflows/ci.yml`）到 fresh repo（fresh venv + non-editable wheel 安装的 trac），绑定 `remote_url`（专用真实 GitHub 远程；stand-in 通道用于确定性测试）。Python 细节只存在于资产数据与该隔离模块；物化后与 tracks 自身同构走 M-VERIFY→M-MILESTONE。凭据/远程不可用 → needs_attention，旅程不计通过（Maestro 裁定 T-003 A）。

### 1.0.13 角色所有权

| # | 职责 | Archer | Shield | Devon | Prism | Runtime |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | host-contract/registry/接口桩/reference 资产 | ✅ 独家设计/物化 | ❌ | ❌ | 评审 | 执行/校验 |
| 2 | integration/e2e 测试资产 | ❌ | ✅ 独家 | ❌ | 评审 | collect/run |
| 3 | 业务实现与 foundation tasks（含 cmd_release、envelope 行为体、语言 token 迁移） | ❌ | ❌ | ✅ | 评审 | 门禁/提交 |
| 4 | candidate 冻结/门禁执行/CI 回读/preview/幂等执行/归档 | ❌ | ❌ | ❌ | 同 candidate 复审 | ✅ 程序独家 |
| 5 | release/delay/return 三择一 | Human 独占（release CLI） | ❌ | ❌ | ❌ | 校验/落事件 |
| 6 | 不可逆外部操作 | ❌ | ❌ | ❌ | ❌ | ✅ 唯一（effects/publish） |

### 1.1 Composition Root

**M-VERIFY 路径**：

```text
trac run -> cli.main.cmd_run -> Executor.run_loop -> kernel.machine.decide
  -> version capability seam resolves v0.8 extension (RELEASE_PIPELINE_VERSION)
  -> kernel/release.release_stage_defs registered on the canonical stage table
  -> M-IMPL boundary guard routes RELEASE-capable runs into stage.entered(M-VERIFY)
  -> Command(freeze_candidate) -> Executor handler -> executor/m_verify.freeze_candidate
      (clean-tree gate; dirty -> attention.required(reason=dirty_tree))
  -> Command(judge_full_f_reuse) -> m_verify.judge_full_f_reuse
      -> evidence.reused(kind=full_f)|full.executed (per LOCAL_GATES)
  -> Command(run_local_gates) -> executor/host_contract.load_host_contract
      -> execute_gate per [[local_gate]] (registry-sourced quality + declared commands)
      -> local_gate.passed|failed per kind, each bound to candidate_sha
  -> Command(observe_ci_runs) -> effects/github CI readback
      -> ci.run_observed(repo,workflow,run_id,head_sha,candidate_sha,api_verified)
  -> Prism final-review dispatch (envelope v2) -> prism.verdict(scope=verify_final)
  -> stage.exited(M-VERIFY) -> stage.entered(M-SECURITY) only when all gates passed
```

**发布闭环路径**：

```text
M-SECURITY: Command(assess_security) -> executor/security.run_security_scans
  (contract [[security_scan]] installs+runs pinned tools) -> Prism scope=security
  -> security.assessed(policy_digest, candidate_sha, status)
M-RELEASE: Command(generate_preview) -> executor/release_gate.build_operation_plan
  + compute_preview_digest + generate_preview -> release.previewed -> AWAITING_RELEASE
Human gate: cmd_release (independent CLI surface, foundation task)
  -> Command(record_release_decision) -> release_gate.validate_release_decision
  -> release.decided|release.rejected -> M-PUBLISH|DELAYED|RETURNED
M-PUBLISH: Command(execute_publish) -> executor/publish.plan_operations
  (write-ahead publish.planned + idempotency_key) -> effects/publish.*
  (Runtime-only irreversible ops) -> publish.executed(done|reconciled_skip)
M-MILESTONE: Command(close_milestone) -> executor/milestone.*
  -> milestone.trace_closed -> issue.closed/project.closed/milestone.closed
  -> milestone.sealed + refs.cleaned -> run.completed(terminal_state=released)
```

**Envelope/failure 链路径**：每次派发前 `check_envelope_parity`（dispatch.parity / dispatch.rejected）；回收时 `parse_agent_output`（format_error vs semantic_attempt_failed）；失败产生时 `failure_review.record_failure` → select → inject → outcome `evidence_ack` → acked/invalidated；`review_failure_chain` 在每次注入前核验。

**Issue 路径**：M-REQ-APPROVAL ISSUES 子状态 → `create_issues`（既有）→ effects/github（真实通道；缺凭据 attention.required）→ API 回读 → `issue.mapped` → task/commit/report/关闭 effector 消费 issue-map.json。

### 1.2 Required AC closure (ISLAND_GATE_1)

- **FR-0267** owner=kernel/release.py+executor/m_verify.py:AC-FR0267-01 surface=trac-run-M-VERIFY+trac-status/replay composition=release_stage_defs 注册 + freeze_candidate handler wiring=M-IMPL-boundary→stage.entered(M-VERIFY)→clean-tree 校验→candidate.frozen(candidate_sha=HEAD,clean_tree=true)→全链事件携带同 SHA test=integration+e2e:tests/integration/test_verify_candidate.py::test_clean_tree_freezes_candidate+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_candidate.py`输出`passed`且事件流含`candidate.frozen candidate_sha=<full SHA> clean_tree=true`、status 显示`stage=M-VERIFY candidate=<full SHA>` IF-VERIFY-001
- **FR-0267** owner=executor/m_verify.py:AC-FR0267-02 surface=trac-run+trac-status composition=freeze_candidate 的 clean-tree 守卫 wiring=dirty-tree→attention.required(reason=dirty_tree)→不进 M-VERIFY、无 candidate.frozen、后续 preview/operation 不引用 test=integration:tests/integration/test_verify_candidate.py::test_dirty_tree_needs_attention evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_candidate.py`输出`passed`且 status 含`needs_attention`与`reason=dirty_tree`、事件流无成功冻结 IF-VERIFY-001
- **FR-0267** owner=executor/m_verify.py:AC-FR0267-03 surface=trac-status+trac-replay composition=collect_binding_violations 漂移扫描 wiring=新 commit/分支 HEAD 变化→candidate.stale 或门禁 blocked→replay 重建冻结且不重复冻结 test=integration:tests/integration/test_verify_candidate.py::test_drift_marks_stale_no_refreeze evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_candidate.py`输出`passed`且 replay 中 candidate.frozen 恰一次、stale 原因可审计 IF-VERIFY-001
- **FR-0268** owner=executor/m_verify.py:AC-FR0268-01 surface=trac-run+trac-status composition=judge_full_f_reuse 复用分支 wiring=未漂移∧identity 四元组一致∧无 STALE→evidence.reused(kind=full_f,identity_basis)→不本地重跑 test=integration+e2e:tests/integration/test_verify_fullf_reuse.py::test_undrifted_identity_reuses_full_f+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_fullf_reuse.py`输出`passed`且 status 显示`full_reuse=full_f`、事件流无本轮 full.executed IF-VERIFY-002
- **FR-0268** owner=executor/m_verify.py:AC-FR0268-02 surface=trac-run+trac-status composition=judge_full_f_reuse 重跑分支 wiring=drift/stale/identity_mismatch→full.executed+按 LOCAL_GATES 重跑→status 显示 full_rerun 及依据 test=integration:tests/integration/test_verify_fullf_reuse.py::test_drift_or_stale_reruns_full evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_fullf_reuse.py`输出`passed`且无 evidence.reused、status 含`full_rerun reason=drift|stale|identity_mismatch` IF-VERIFY-002
- **FR-0268** owner=executor/m_verify.py:AC-FR0268-03 surface=trac-replay composition=复用资格的 STALE 排除 wiring=含 STALE 标记的 FULL_F 即使 SHA 相同也进重跑分支、证据标记 stale 且无 evidence.reused test=integration:tests/integration/test_verify_fullf_reuse.py::test_stale_evidence_not_reused evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_fullf_reuse.py`输出`passed`且 stale 证据被标记、复用判定为 rerun IF-VERIFY-002 IF-EVIDENCE-001
- **FR-0269** owner=executor/host_contract.py:AC-FR0269-01 surface=trac-run-M-VERIFY+trac-validate composition=load_host_contract→execute_gate 逐项 wiring=七类 local_gate（quality 引用 §4.2 registry）→local_gate.passed(kind,candidate_sha,contract_digest,normalized_result) test=integration:tests/integration/test_verify_local_gates.py::test_contract_gates_run_and_pass evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_local_gates.py`输出`passed`且每类 gate 均落 passed 事件、`trac validate --file .tracks/projects/v0.8/architecture.md`退出 0 IF-VERIFY-003 IF-HOSTCONTRACT-001
- **FR-0269** owner=executor/host_contract.py:AC-FR0269-02 surface=trac-run+trac-status composition=未知/畸形 fail-closed wiring=未声明/缺工具/超时/不可解析→local_gate.failed(reason=unknown|malformed|missing_contract)→blocked 不进 M-SECURITY；合同变更→resume 重跑对应验证 test=integration:tests/integration/test_verify_local_gates.py::test_missing_or_malformed_gate_fails_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_local_gates.py`输出`passed`且注入畸形合同副本后 status 含`blocked`与失败项 reason IF-VERIFY-003 IF-HOSTCONTRACT-001
- **FR-0270** owner=effects/github.py:AC-FR0270-01 surface=trac-run-M-VERIFY+trac-report composition=observe_ci_runs API 回读 wiring=GET actions/runs 按 head_sha 过滤→repo+workflow+run_id+head SHA==candidate SHA 且 conclusion=success→ci.run_observed(api_verified=true) test=integration+e2e:tests/integration/test_verify_ci_readback.py::test_api_readback_binds_candidate+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_ci_readback.py`输出`passed`且 stand-in API 下事件含四元组绑定与`api_verified=true`、status 显示`ci=bound` IF-VERIFY-004
- **FR-0270** owner=effects/github.py:AC-FR0270-02 surface=trac-run+trac-status composition=observe_ci_runs fail-closed 分支 wiring=不一致/缺失/非 required/stale→ci.run_observed(status=failed)→ci=mismatch|missing|stale→blocked；不一致证据不被 preview 引用 test=integration:tests/integration/test_verify_ci_readback.py::test_mismatch_missing_stale_blocks evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_ci_readback.py`输出`passed`且三类注入全部 blocked IF-VERIFY-004
- **FR-0270** owner=effects/github.py:AC-FR0270-03 surface=trac-run+trac-status composition=凭据/网络 needs_attention 分支 wiring=缺凭据/网络错误→attention.required(reason=missing_token|network_error)→不落成功观测→凭据恢复 resume 重试转 bound test=integration:tests/integration/test_verify_ci_readback.py::test_missing_credentials_needs_attention evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_ci_readback.py`输出`passed`且无 token 时 status 为 needs_attention、恢复后事件出现且 ci=bound IF-VERIFY-004
- **FR-0271** owner=executor/m_verify.py:AC-FR0271-01 surface=trac-run-PRISM+trac-status composition=build_prism_final_review_assignment wiring=本地+CI 门禁过→同 candidate 复审（FULL_F identity/local_gate/ci_run_observed/合同 digest 同 SHA）→prism.verdict(pass,scope=verify_final,candidate_sha) test=integration:tests/integration/test_verify_prism_final.py::test_same_candidate_consistency_pass evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_prism_final.py`输出`passed`且 verdict 绑定同一 candidate SHA、status 显示`prism=pass` IF-VERIFY-005
- **FR-0271** owner=kernel/release.py:AC-FR0271-02 surface=trac-run+trac-status composition=prism 失败阻断 wiring=verdict=failed|revise→不进 M-SECURITY→修复后同 candidate 重审落新 verdict test=integration:tests/integration/test_verify_prism_final.py::test_prism_fail_blocks_m_impl_gap evidence=`.venv/bin/python -m pytest -q tests/integration/test_verify_prism_final.py`输出`passed`且失败时无 stage.entered(M-SECURITY)、重审事件绑定同 SHA IF-VERIFY-005
- **FR-0272** owner=executor/security.py:AC-FR0272-01 surface=trac-run-M-SECURITY+trac-replay composition=run_security_scans+Prism scope=security wiring=合同 pinned 扫描逐项→normalized result 绑 candidate→security.assessed(policy_digest,status=passed) test=integration:tests/integration/test_security_assessment.py::test_contract_scans_pass evidence=`.venv/bin/python -m pytest -q tests/integration/test_security_assessment.py`输出`passed`且事件含 policy_digest 与 status=passed IF-SECURITY-001
- **FR-0272** owner=executor/security.py:AC-FR0272-02 surface=trac-run+trac-status composition=aggregate_security_status fail-closed wiring=未声明/版本不匹配/不可解析→status=failed|unknown→blocked→修复后同 candidate 重跑落新事件 test=integration:tests/integration/test_security_assessment.py::test_unknown_or_malformed_blocks evidence=`.venv/bin/python -m pytest -q tests/integration/test_security_assessment.py`输出`passed`且畸形策略注入后 blocked、重跑后新 security.assessed 出现 IF-SECURITY-001
- **FR-0273** owner=executor/release_gate.py:AC-FR0273-01 surface=release-preview-CLI+trac-report composition=generate_preview 聚合绑定 wiring=candidate+artifact+证据+operation plan+合同 digest→preview_digest→release.previewed→AWAITING_RELEASE→preview 可经独立 release CLI 与 report 审计 test=integration+e2e:tests/integration/test_release_preview.py::test_preview_digest_binds_all+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_preview.py`输出`passed`且 preview 输出含全部 digest 与`status=awaiting_release` IF-RELEASE-002
- **FR-0273** owner=executor/release_gate.py:AC-FR0273-02 surface=release-preview-CLI+trac-status composition=judge_preview_stale wiring=证据漂移/新 commit/plan 变更→stale_reason→不可用于授权；重生成落新事件旧 preview 不覆盖 test=integration:tests/integration/test_release_preview.py::test_stale_preview_reported evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_preview.py`输出`passed`且新 commit 后 preview 显示`status=stale`及原因、重生成后旧 digest 仍在事件流 IF-RELEASE-002
- **FR-0274** owner=cli/main.py+executor/release_gate.py:AC-FR0274-01 surface=独立 release CLI+trac-status/replay composition=record_release_decision→validate_release_decision wiring=preview 未 stale∧门禁全过→release.decided(action=release,candidate,preview_digest)→M-PUBLISH；trac approve/GitHub UI 不产生该事件 test=integration+e2e:tests/integration/test_release_gate_cli.py::test_release_action_allowed+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_gate_cli.py`输出`passed`且决定事件绑定 preview/candidate、status 显示`decision=release` IF-RELEASE-003
- **FR-0274** owner=executor/release_gate.py:AC-FR0274-02 surface=独立 release CLI composition=validate_release_decision 拒绝分支 wiring=门禁失败或 stale→非零退出+release.rejected→不落成功 decided→状态保持 AWAITING_RELEASE test=integration:tests/integration/test_release_gate_cli.py::test_rejected_gate_or_stale evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_gate_cli.py`输出`passed`且拒绝路径退出码 1、无阶段转移 IF-RELEASE-003
- **FR-0274** owner=kernel/release.py:AC-FR0274-03 surface=独立 release CLI+trac-status composition=delay/return 转移 wiring=delay→DELAYED（重生成指引）；return --to→回上游阶段、目标后证据标 stale test=integration:tests/integration/test_release_gate_cli.py::test_delay_and_return evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_gate_cli.py`输出`passed`且 delay/return 事件绑定 preview、return 后下游证据 stale IF-RELEASE-003
- **FR-0274** owner=cli/main.py:AC-FR0274-04 surface=独立 release CLI+trac-approve composition=stale 校验对三择一统一+交付面独占 wiring=stale preview 时 delay|return 亦非零拒绝；trac approve 与 GitHub 操作不产生 release.decided test=integration:tests/integration/test_release_gate_cli.py::test_surface_exclusivity evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_gate_cli.py`输出`passed`且 approve 路径事件流无 release.decided IF-RELEASE-003
- **FR-0275** owner=executor/publish.py+effects/publish.py:AC-FR0275-01 surface=trac-run-M-PUBLISH+trac-status/replay composition=plan_operations→effects 执行 wiring=publish.planned(operation_kind,target,preview_digest,idempotency_key)→publish.executed(done)→远端与事件互验一致 test=integration+e2e:tests/integration/test_publish_idempotency.py::test_planned_then_executed_done+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_publish_idempotency.py`输出`passed`且事件含幂等键、stand-in 远端 tag/branch 与事件一致 IF-PUBLISH-001
- **FR-0275** owner=executor/publish.py:AC-FR0275-02 surface=trac-run-resume+trac-replay composition=reconcile_operation wiring=已完成副作用经远端比对→publish.executed(reconciled_skip,同幂等键)→不重复执行 test=integration:tests/integration/test_publish_idempotency.py::test_resume_reconciled_skip evidence=`.venv/bin/python -m pytest -q tests/integration/test_publish_idempotency.py`输出`passed`且 resume 后远端无重复 tag/merge IF-PUBLISH-002
- **FR-0275** owner=executor/publish.py:AC-FR0275-03 surface=trac-run+trac-replay composition=assert_agent_forbidden 权限隔离 wiring=Agent 试图触达外部操作面→publish.blocked(reason=agent_forbidden)→无成功 executed、远端无副作用 test=integration:tests/integration/test_publish_idempotency.py::test_agent_forbidden evidence=`.venv/bin/python -m pytest -q tests/integration/test_publish_idempotency.py`输出`passed`且注入 Agent 操作意图后 blocked、远端为空 IF-PUBLISH-001
- **FR-0275** owner=executor/publish.py:AC-FR0275-04 surface=trac-run+trac-status composition=未知操作与远端冲突 wiring=未声明/畸形 operation→publish.failed(unknown_operation|malformed)→blocked；远端不一致以远端为准、必要时 reconcile_conflict test=integration:tests/integration/test_publish_idempotency.py::test_unknown_operation_and_conflict evidence=`.venv/bin/python -m pytest -q tests/integration/test_publish_idempotency.py`输出`passed`且未声明操作被拒、同键异果落 reconcile_conflict IF-PUBLISH-001 IF-PUBLISH-002
- **FR-0276** owner=executor/milestone.py:AC-FR0276-01 surface=trac-run-M-MILESTONE+trac-report composition=build_release_trace→seal/cleanup wiring=milestone.trace_closed(trace_digest)→issue/project/milestone 关闭→milestone.sealed+refs.cleaned→terminal=released；trace 与外部 tag/release 互验 test=integration+e2e:tests/integration/test_milestone_lifecycle.py::test_trace_closed_sealed_refs_clean+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_milestone_lifecycle.py`输出`passed`且 for-each-ref refs/trac/tmp 为空、report 可导出 release.trace IF-MILESTONE-001
- **FR-0276** owner=kernel/release.py:AC-FR0276-02 surface=trac-run-resume+trac-status composition=RETRY_TAIL 边界 wiring=发布成功+归档失败→retry_tail→仅重试收尾、已成功操作 reconciled_skip、无重复 done test=integration:tests/integration/test_milestone_lifecycle.py::test_retry_tail_no_republish evidence=`.venv/bin/python -m pytest -q tests/integration/test_milestone_lifecycle.py`输出`passed`且中断后事件流无第二个 done IF-MILESTONE-001 IF-PUBLISH-002
- **FR-0277** owner=kernel/release.py+executor/release_gate.py:AC-FR0277-01 surface=trac-start+trac-run+git/远端+trac-report composition=operations.feature 计划执行 wiring=全旅程→merge main+公开 tag+artifact+release→release.trace 验证同一 candidate test=integration+e2e:tests/integration/test_journey_versioning.py::test_feature_public_release+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_journey_versioning.py`输出`passed`且远端含公开 tag、status 终态`released` IF-JOURNEY-001
- **FR-0277** owner=executor/release_gate.py:AC-FR0277-02 surface=trac-hotfix-post-release+git+trac-replay composition=operations.post_release 条件步 wiring=fix 分支验证→merge main→活跃 release 分支同步 merge→patch tag/release；预验证/M-VERIFY/安全/Human gate 同 feature test=integration+e2e:tests/integration/test_journey_versioning.py::test_post_release_patch+tests/e2e/test_hotfix_release_journeys.py::test_post_release_hotfix_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_journey_versioning.py`输出`passed`且 main 与 release 分支均含修复、patch tag 存在 IF-JOURNEY-001
- **FR-0277** owner=executor/release_gate.py:AC-FR0277-03 surface=trac-hotfix-dev+git+trac-report composition=operations.dev 计划 wiring=仅 merge 活跃分支+prerelease tag、无公开 tag/release→channel=pre-release test=integration+e2e:tests/integration/test_journey_versioning.py::test_dev_prerelease_only+tests/e2e/test_hotfix_release_journeys.py::test_dev_hotfix_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_journey_versioning.py`输出`passed`且 git tag 无公开项、report 显示 channel=pre-release IF-JOURNEY-001
- **FR-0277** owner=executor/publish.py:AC-FR0277-04 surface=trac-run-resume+trac-hotfix-dev composition=三旅程同一性与 dev precheck wiring=同 candidate 绑定+幂等→resume 不重复 tag/release(reconciled_skip)；无活跃分支 dev hotfix 非零退出不建分支 test=integration:tests/integration/test_journey_versioning.py::test_identity_and_idempotent_journeys+test_journey_versioning.py::test_dev_precheck_fails_without_release_branch evidence=`.venv/bin/python -m pytest -q tests/integration/test_journey_versioning.py`输出`passed`且 precheck 失败退出码 1、无 fix 分支 IF-JOURNEY-001 IF-PUBLISH-002
- **FR-0278** owner=kernel/envelope.py:AC-FR0278-01 surface=trac-replay/report+trac-validate composition=parse_agent_output/build_assignment_envelope wiring=统一 envelope(kind,version=2)→单一解析路径→示例 fixtures 经 validator 校验 test=integration:tests/integration/test_envelope_contract.py::test_unified_envelope_parse evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_contract.py`输出`passed`且 replay 显示 envelope 版本、全部角色/kind 的样本过 validator IF-ENVELOPE-001
- **FR-0278** owner=kernel/envelope.py:AC-FR0278-02 surface=trac-replay+trac-status composition=EnvelopeFormatError→format_error wiring=缺 kind/version、未知版本、游离 JSON、零/多 envelope block→format_error→业务状态不变更、无 red.validated/阶段推进 test=integration:tests/integration/test_envelope_contract.py::test_malformed_format_error evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_contract.py`输出`passed`且畸形注入只落 format_error IF-ENVELOPE-001
- **FR-0279** owner=kernel/envelope.py:AC-FR0279-01 surface=trac-run+trac-replay composition=check_envelope_parity 派发前校验 wiring=六面版本不一致→dispatch.rejected(version_parity_mismatch)→不进 Agent 执行 test=integration:tests/integration/test_envelope_parity.py::test_parity_mismatch_rejects evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_parity.py`输出`passed`且注入旧版本 token 后派发被拒 IF-ENVELOPE-002
- **FR-0279** owner=kernel/envelope.py:AC-FR0279-02 surface=trac-replay composition=format/semantic verdict 分离 wiring=format_error 与 semantic_attempt_failed 分别落事件；format 不计 attempt、不产生 publish/release.decided test=integration:tests/integration/test_envelope_parity.py::test_format_vs_semantic_events_separated evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_parity.py`输出`passed`且两类事件可分离审计 IF-ENVELOPE-002
- **FR-0279** owner=kernel/envelope.py:AC-FR0279-03 surface=trac-report composition=malformed 回归语料 wiring=v0.7 malformed 故障语料逐条注入→全部 format_error+blocked→report 显示回归覆盖 test=integration:tests/integration/test_envelope_parity.py::test_malformed_regression_corpus evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_parity.py`输出`passed`且语料全部 fail-closed IF-ENVELOPE-002
- **FR-0280** owner=executor/failure_review.py:AC-FR0280-01 surface=trac-replay/report composition=record→select→inject→ack→invalidate 链 wiring=七环节事件逐项落盘；未 ACK 富证据不被普通失败覆盖 test=integration:tests/integration/test_failure_evidence_chain.py::test_chain_events_replay evidence=`.venv/bin/python -m pytest -q tests/integration/test_failure_evidence_chain.py`输出`passed`且链事件按序可回放、stored 记录仍可被 selected 引用 IF-FAILURE-001
- **FR-0280** owner=executor/failure_review.py:AC-FR0280-02 surface=trac-replay composition=共享选择规则 wiring=同一 round 的 failure.selected 在各角色一致；无缺口时不产生 failure_history 新事件 test=integration:tests/integration/test_failure_evidence_chain.py::test_consistent_selection_rules evidence=`.venv/bin/python -m pytest -q tests/integration/test_failure_evidence_chain.py`输出`passed`且各角色选择一致、事件流无 failure_history 条目 IF-FAILURE-001
- **FR-0280** owner=executor/failure_review.py:AC-FR0280-03 surface=trac-run+trac-status composition=review_failure_chain 校验 wiring=injected 与 stored 不一致/富证据被覆盖→review.failed→blocked: evidence lost or mismatched；重放后一致 test=integration:tests/integration/test_failure_evidence_chain.py::test_lost_or_mismatched_blocks evidence=`.venv/bin/python -m pytest -q tests/integration/test_failure_evidence_chain.py`输出`passed`且注入丢失后 blocked IF-FAILURE-001
- **FR-0281** owner=executor/host_contract.py:AC-FR0281-01 surface=trac-init+trac-validate composition=load_host_contract+validate_host_contract wiring=Archer 物化 project.toml 的 [host-contract.*] 段→host_contract.materialized→validate schema 通过 test=integration:tests/integration/test_host_contract.py::test_materialized_contract_valid evidence=`.venv/bin/python -m pytest -q tests/integration/test_host_contract.py`输出`passed`且事件含 host_contract.materialized、`trac validate --file .tracks/projects/project.toml`对 host-contract 段校验通过 IF-HOSTCONTRACT-001
- **FR-0281** owner=executor/host_contract.py:AC-FR0281-02 surface=trac-run+trac-replay composition=execute_gate 合同驱动执行 wiring=仅执行声明命令→normalized result；未知语言/畸形→host_contract.invalid→blocked test=integration:tests/integration/test_host_contract.py::test_execute_per_contract_only evidence=`.venv/bin/python -m pytest -q tests/integration/test_host_contract.py`输出`passed`且 replay 中执行命令与声明一致、未知框架注入 blocked IF-HOSTCONTRACT-001 IF-HOSTCONTRACT-002
- **FR-0281** owner=executor/host_contract.py:AC-FR0281-03 surface=trac-run+trac-status composition=容错回流 wiring=安装/编译/collect/build/smoke 失败→host_contract.failed(exit/stdout/stderr/result)→needs_attention: host_contract revise→回 M-DESIGN；修订后重校验通过 test=integration:tests/integration/test_host_contract.py::test_failed_gate_machine_evidence_revision_loop evidence=`.venv/bin/python -m pytest -q tests/integration/test_host_contract.py`输出`passed`且失败 payload 含机器证据、修订后继续 IF-HOSTCONTRACT-002
- **FR-0282** owner=executor/reference_host.py:AC-FR0282-01 surface=trac-init+trac-run(reference repo)+trac-report composition=create_reference_host→同构旅程 wiring=wheel 资产部署+remote 绑定→candidate.frozen/…/milestone.sealed 同构链路→report 同一性 test=integration+e2e:tests/integration/test_reference_host.py::test_reference_host_journey_same_shape+tests/e2e/test_reference_host_journey.py::test_reference_host_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_reference_host.py`输出`passed`且 reference 事件链与 tracks 同构、同一 candidate SHA IF-REFERENCE-001
- **FR-0282** owner=executor/reference_host.py:AC-FR0282-02 surface=trac-status+trac-report composition=凭据缺失 needs_attention wiring=缺凭据/远端不可用→needs_attention: missing_credentials|remote_unavailable→不计通过、不降级本地成功 test=integration:tests/integration/test_reference_host.py::test_missing_credentials_needs_attention evidence=`.venv/bin/python -m pytest -q tests/integration/test_reference_host.py`输出`passed`且无本地成功 release.decided IF-REFERENCE-001
- **FR-0282** owner=executor/reference_host.py+assets:AC-FR0282-03 surface=trac-validate+代码扫描 composition=Python 细节隔离 wiring=venv/pytest/wheel 细节仅在 reference 资产与隔离模块；kernel 规范与 host-contract schema 无 python 字段→validate 通过 test=integration:tests/integration/test_reference_host.py::test_python_details_isolated evidence=`.venv/bin/python -m pytest -q tests/integration/test_reference_host.py`输出`passed`且 kernel/schema 扫描无语言 token IF-REFERENCE-001 IF-HOSTCONTRACT-001
- **FR-0283** owner=effects/github.py:AC-FR0283-01 surface=trac-run+trac-report composition=create_issues→API 回读→issue.mapped wiring=M-REQ-APPROVAL 后首个 task 前→issue.created(api 回读存在)→issue.mapped(api_verified=true)→task/commit/report 引用权威映射 test=integration:tests/integration/test_issue_mapping.py::test_real_issue_created_and_mapped evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_mapping.py`输出`passed`且 report 显示 issue_map 与 api_verified=true、status 无 needs_attention IF-ISSUE-001
- **FR-0283** owner=effects/github.py:AC-FR0283-02 surface=trac-run+trac-status composition=缺凭据 needs_attention wiring=无凭据→attention.required(issue_creation missing_tokens, next 指引)→无成功 issue.created、无 FAKE-N→恢复后 resume 重建 test=integration:tests/integration/test_issue_mapping.py::test_missing_credentials_needs_attention evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_mapping.py`输出`passed`且缺 token 时 status 报告恢复指引 IF-ISSUE-001
- **FR-0283** owner=effects/github.py:AC-FR0283-03 surface=trac-run+trac-replay composition=fake 隔离 wiring=显式模拟模式才产生 FAKE-N；真实模式 FAKE 产物→fake_rejected+blocked: fake_not_allowed test=integration:tests/integration/test_issue_mapping.py::test_fake_rejected_in_real_mode evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_mapping.py`输出`passed`且真实模式注入 FAKE 后 blocked IF-ISSUE-001
- **FR-0283** owner=effects/github.py:AC-FR0283-04 surface=trac-run-resume+GitHub-API composition=repo+baseline digest 去重 wiring=崩溃后 resume→已创建不重复（API 同 baseline 无重复 number）→无重复 issue.created 同映射 test=integration:tests/integration/test_issue_mapping.py::test_crash_idempotent_dedup evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_mapping.py`输出`passed`且二次 resume 无重复创建 IF-ISSUE-001
- **FR-0284** owner=executor/milestone.py:AC-FR0284-01 surface=trac-run-M-MILESTONE+GitHub-API composition=close_issues_with_comment+close_project_milestone wiring=映射再校验→issue.closed(comment 含 trace)→project.closed/milestone.closed→refs 为空、fake-project 未出现 test=integration:tests/integration/test_issue_close.py::test_close_with_trace_comment evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_close.py`输出`passed`且关闭事件与 release.trace 互验一致 IF-ISSUE-002 IF-MILESTONE-001
- **FR-0284** owner=effects/github.py:AC-FR0284-02 surface=trac-run+trac-report composition=FAKE 反例保障 wiring=v0.7 FAKE-90..103/fake-project 反例输入→issue.mapped 缺失或 api_verified=false→fake_rejected→不产生成功关闭/封存 test=integration:tests/integration/test_issue_close.py::test_fake_counterexamples_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_close.py`输出`passed`且反例不产生 issue.closed/milestone.sealed IF-ISSUE-002
- **FR-0285** owner=kernel/m_test.py(既有)：AC-FR0285-01 surface=trac-run-M-TEST+trac-replay composition=既有 WRITE→COLLECT→RED_CHECK→PRISM_REVIEW 链回归 wiring=red.validated→prism.verdict 消费该证据且仅隔离 kill→链路完整；缺失环节 pipeline incomplete test=integration:tests/integration/test_pipeline_regression.py::test_write_collect_redcheck_prism_chain_intact evidence=`.venv/bin/python -m pytest -q tests/integration/test_pipeline_regression.py`输出`passed`且 replay 链四环节齐备 IF-PIPELINE-001
- **FR-0285** owner=kernel/m_test.py(既有)：AC-FR0285-02 surface=trac-run+trac-status composition=生产者自检边界 wiring=Shield 自检不产生 red.validated/prism.verdict 权威记录；FULL 重跑不冒充 RED_CHECK test=integration:tests/integration/test_pipeline_regression.py::test_no_selfcheck_authority evidence=`.venv/bin/python -m pytest -q tests/integration/test_pipeline_regression.py`输出`passed`且自检事件无权威结论 IF-PIPELINE-001
- **FR-0285** owner=executor/validate.py(既有扩展)：AC-FR0285-03 surface=trac-validate+trac-report composition=流水线定义冻结 wiring=v0.8 无新增流水线阶段→validate 通过、report 显示流水线版本与 v0.7 一致 test=integration:tests/integration/test_pipeline_regression.py::test_no_new_pipeline_definition evidence=`.venv/bin/python -m pytest -q tests/integration/test_pipeline_regression.py`输出`passed`且版本一致 IF-PIPELINE-001
- **NFR-0143** owner=kernel/release.py:AC-NFR0143-01 surface=trac-replay/report+trac-status composition=collect_binding_violations 全链核验 wiring=五阶段事件均携带同 candidate_sha→replay 验证一致性；漂移→release.trace=inconsistent+blocked: candidate mismatch test=integration:tests/integration/test_release_trace.py::test_same_candidate_all_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_trace.py`输出`passed`且注入异 SHA 证据后 trace=inconsistent IF-TRACE-003 IF-VERIFY-001
- **NFR-0143** owner=executor/milestone.py:AC-NFR0143-02 surface=trac-report-md composition=release.trace 同一性证明导出 wiring=trace 含 candidate/artifact/evidence/preview/human approval event id/operation digests→与外部 tag/release 互验 test=integration+e2e:tests/integration/test_release_trace.py::test_trace_export_digests+tests/e2e/test_release_journey.py::test_feature_release_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_trace.py`输出`passed`且导出 trace 与远端 digest 互验一致 IF-TRACE-003
- **NFR-0144** owner=executor/publish.py:AC-NFR0144-01 surface=trac-run-resume+远端验证 composition=同 preview 重复执行→reconciled_skip wiring=第二次 resume 落 reconciled_skip(同键)→远端无重复 tag/release/merge；未完成继续 done test=integration:tests/integration/test_publish_reconcile.py::test_repeat_operation_skips_no_duplicates+test_publish_reconcile.py::test_unfinished_continues evidence=`.venv/bin/python -m pytest -q tests/integration/test_publish_reconcile.py`输出`passed`且远端记录唯一 IF-PUBLISH-002
- **NFR-0144** owner=executor/publish.py:AC-NFR0144-02 surface=trac-run+trac-status composition=同键异果冲突 wiring=人工 tag 覆写/并发发布→reconcile_conflict→blocked: reconcile_conflict 不继续 test=integration:tests/integration/test_publish_reconcile.py::test_same_key_remote_diff_conflict evidence=`.venv/bin/python -m pytest -q tests/integration/test_publish_reconcile.py`输出`passed`且冲突事件可审计 IF-PUBLISH-002
- **NFR-0145** owner=kernel/envelope.py:AC-NFR0145-01 surface=trac-replay composition=确定性解析+parity wiring=envelope(kind/version)+schema 单路径可判别→replay 显示 deterministic 与 schema_digest 一致；版本不一致 dispatch.rejected test=integration:tests/integration/test_envelope_parity.py::test_deterministic_parse_and_parity evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_parity.py`输出`passed`且无回退启发式命中 IF-ENVELOPE-002
- **NFR-0145** owner=kernel/envelope.py:AC-NFR0145-02 surface=trac-replay composition=malformed 业务隔离 wiring=format_error 不产生业务状态变更（无 publish/release.decided 因其产生）→两类事件分离 test=integration:tests/integration/test_envelope_parity.py::test_malformed_no_business_mutation evidence=`.venv/bin/python -m pytest -q tests/integration/test_envelope_parity.py`输出`passed`且业务状态不变 IF-ENVELOPE-001 IF-ENVELOPE-002
- **NFR-0146** owner=executor/failure_review.py:AC-NFR0146-01 surface=trac-replay/report composition=append-only 链与重放一致 wiring=全链事件 append-only→重放后状态与中断前一致；未 ACK 富证据在普通失败后仍可被引用 test=integration:tests/integration/test_failure_evidence_chain.py::test_append_only_replay_identical evidence=`.venv/bin/python -m pytest -q tests/integration/test_failure_evidence_chain.py`输出`passed`且重放一致 IF-FAILURE-001
- **NFR-0146** owner=executor/failure_review.py:AC-NFR0146-02 surface=trac-replay composition=各角色消费证明 wiring=consumed/acked/invalidated 逐角色可审计→同一 round 选择一致 test=integration:tests/integration/test_failure_evidence_chain.py::test_per_role_consumption_proofs evidence=`.venv/bin/python -m pytest -q tests/integration/test_failure_evidence_chain.py`输出`passed`且逐角色证明可回放 IF-FAILURE-001
- **NFR-0147** owner=executor/validate.py+executor/host_contract.py:AC-NFR0147-01 surface=trac-validate+代码扫描 composition=语言 token 扫描扩展（pytest|junit|java|venv|wheel|pip 词边界）+合同驱动执行 wiring=kernel/executor/cli 运行时代码（允许区外）零 token→validate 通过；畸形 normalized result blocked test=integration:tests/integration/test_kernel_language_neutrality.py::test_no_venv_wheel_hardcoding evidence=`.venv/bin/python -m pytest -q tests/integration/test_kernel_language_neutrality.py`输出`passed`且注入 token 副本使扫描非零 IF-HOSTCONTRACT-001 IF-ADAPTER-003
- **NFR-0147** owner=executor/validate.py:AC-NFR0147-02 surface=trac-validate+trac-report composition=kernel 无 python 分支 wiring=Python 细节仅在 reference 资产/隔离模块/schema 声明值→kernel 执行面无语言分支 test=integration:tests/integration/test_kernel_language_neutrality.py::test_kernel_schema_language_free evidence=`.venv/bin/python -m pytest -q tests/integration/test_kernel_language_neutrality.py`输出`passed`且 schema/规范扫描无 python 硬编码字段 IF-ADAPTER-003 IF-HOSTCONTRACT-001
- **NFR-0148** owner=effects/github.py:AC-NFR0148-01 surface=trac-report+trac-replay composition=权威映射消费 wiring=映射 api_verified=true→task/commit/report/关闭引用与映射一致→引用来源 issue.mapped test=integration:tests/integration/test_issue_mapping.py::test_authoritative_map_consumed evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_mapping.py`输出`passed`且 commit trailer 引用权威 number IF-ISSUE-001
- **NFR-0148** owner=effects/github.py:AC-NFR0148-02 surface=trac-run+trac-status composition=fake 映射拒绝消费 wiring=真实模式 fake 映射→fake_rejected→不被关闭 effector 消费 test=integration:tests/integration/test_issue_mapping.py::test_fake_map_not_consumed_by_closers evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_mapping.py`输出`passed`且关闭路径不引用 fake 映射 IF-ISSUE-001
- **NFR-0149** owner=executor/reference_host.py+kernel/release.py:AC-NFR0149-01 surface=trac-run 双宿主+trac-report composition=六旅程矩阵（2 宿主×3 旅程） wiring=每旅程 CI/candidate/artifact/Human/operation 同 SHA 同 preview_digest→终态 released（dev 为 pre-release delivered）；缺凭据 needs_attention 不计通过 test=e2e:tests/e2e/test_release_journeys_matrix.py::test_six_journeys_dual_host evidence=`.venv/bin/python -m pytest -q tests/e2e/test_release_journeys_matrix.py`输出`passed`且六旅程 report 同一性成立 IF-JOURNEY-001 IF-REFERENCE-001
- **NFR-0149** owner=executor/publish.py+executor/milestone.py:AC-NFR0149-02 surface=trac-replay+trac-run-resume composition=kill -9 中断矩阵 wiring=Issue/CI/build/merge/tag/release/归档逐点中断→replay 重建→resume reconcile_skip→远端无重复→trace 与中断前一致 test=integration:tests/integration/test_journey_recovery.py::test_interrupt_replay_reconcile_matrix evidence=`.venv/bin/python -m pytest -q tests/integration/test_journey_recovery.py`输出`passed`且七中断点全部恢复且无重复副作用 IF-PUBLISH-002 IF-MILESTONE-001
- **NFR-0149** owner=executor/m_verify.py+publish.py+github.py:AC-NFR0149-03 surface=trac-run+trac-report composition=错误身份/stale/畸形/silent fake 注入矩阵 wiring=双宿主任一注入→blocked(reason=identity_mismatch|stale|malformed_contract|fake_not_allowed)→不产生成功 release.trace test=integration:tests/integration/test_failclosed_release.py::test_identity_stale_malformed_fake_blocked evidence=`.venv/bin/python -m pytest -q tests/integration/test_failclosed_release.py`输出`passed`且四类 reason 全部可审计 IF-VERIFY-001 IF-PUBLISH-002 IF-ISSUE-001

## 2. Scaffold 宣言

- `tracks/kernel/release.py` — 发布五阶段 StageDef/路由/reducer 完整签名，行为体仅 raise `IF-VERIFY-001` 等（kind: stub）
- `tracks/kernel/envelope.py` — envelope v2 类型与解析/parity 签名，行为体仅 raise `IF-ENVELOPE-001/002`（kind: stub）
- `tracks/executor/m_verify.py` — 冻结/复用/绑定/终审装配签名，行为体仅 raise `IF-VERIFY-001/002/005`（kind: stub）
- `tracks/executor/host_contract.py` — 合同加载/校验/执行/normalized result 签名，行为体仅 raise `IF-HOSTCONTRACT-001`（kind: stub）
- `tracks/executor/security.py` — 安全扫描/复审/聚合签名，行为体仅 raise `IF-SECURITY-001`（kind: stub）
- `tracks/executor/release_gate.py` — plan/preview/stale/授权校验签名，行为体仅 raise `IF-RELEASE-002/003`（kind: stub）
- `tracks/executor/publish.py` — write-ahead/幂等键/reconcile 签名，行为体仅 raise `IF-PUBLISH-001/002`（kind: stub）
- `tracks/executor/milestone.py` — trace/关闭/封存/清理签名，行为体仅 raise `IF-MILESTONE-001/IF-ISSUE-002`（kind: stub）
- `tracks/executor/failure_review.py` — 失败证据链签名，行为体仅 raise `IF-FAILURE-001`（kind: stub）
- `tracks/executor/reference_host.py` — reference host 物化签名，行为体仅 raise `IF-REFERENCE-001`（kind: stub）
- `tracks/effects/publish.py` — 不可逆外部操作效果边界签名，行为体仅 raise `IF-PUBLISH-001/002`（kind: stub）
- `.tracks/projects/project.toml` — 追加 `[host-contract.*]` 段：tracks 宿主发布闭环合同（七类 local gates、version、build/smoke、security、ci、tracker、三旅程 operations；真实机器配置，非桩）。既有 [unit]/[integration]/[e2e]/[nightly]/[adapter]/[layout]/[lint] 段逐字不变；本文件 bytes 变化已同步 §4.2 第 8 项 config_digest（kind: config）
- `tracks/assets/reference_host/pyproject.toml` — reference 宿主 pinned 工具与守卫配置模板（kind: data）
- `tracks/assets/reference_host/flake8.ini` — reference 宿主认知复杂度配置（kind: config）
- `tracks/assets/reference_host/tracks-project.toml` — reference 宿主三层测试合同/adapter/layout 模板，含并入的 `[host-contract.*]` 发布闭环合同段（kind: data）
- `tracks/assets/reference_host/architecture.md` — 部署到 reference repo `.tracks/projects/v0.1/architecture.md` 的八类 canonical registry（host=reference-host）（kind: data）
- `tracks/assets/reference_host/ci.yml` — 部署到 reference repo `.github/workflows/ci.yml` 的 required-CI workflow（kind: data）
- `tracks/assets/reference_host/host_calc.py` — reference 宿主最小确定性源码语料（kind: data）
- `tracks/assets/reference_host/tests/unit/test_host_calc.py` — reference unit 节点语料（kind: data）
- `tracks/assets/reference_host/tests/integration/test_host_contract.py` — reference integration 节点语料（kind: data）
- `tracks/assets/reference_host/tests/e2e/test_host_journey.py` — reference e2e 节点语料（kind: data）
- `pyproject.toml` — `[tool.setuptools.package-data]` 显式 allowlist 追加 reference_host 资产（既有文件唯一改动；§4.2 六条 pyproject-backed config_digest 已随新 bytes 同步）（kind: config）

> **Prism [RESOLVED]:** [PRISM-ARCH008-R2-02][blocker] scaffold 宣言项 tracks/executor/host_contract.py 的实际文件内容与锁定设计矛盾：stub docstring（tracks/executor/host_contract.py:3）声明『.tracks/projects/host-contract.toml is the machine truth Archer materializes per host』——这正是本次 attempt-1 被 Runtime commit check 拒绝的独立合同路径（evidence: scaffold path is not allowed: .tracks/projects/host-contract.toml），也与本文 §0.1/§1.0.3/§3.1 及 interfaces §1e 锁定的『并入 .tracks/projects/project.toml 的 [host-contract.*] 命名空间段』形态相抵触。该 stub 是 Devon foundation task（load_host_contract/validate_host_contract 行为体）的种子，机器真相位置的双重声明会把已消除的双合同真相歧义重新引入实现基线。预期修订：修订该 scaffold 文件 docstring，将机器真相指向 .tracks/projects/project.toml 的 [host-contract.*] 段（与 §2 本项 kind:stub 声明一并保持一致）；其余签名无需变动。
>> **Archer:** 已修复：tracks/executor/host_contract.py 模块 docstring 已改写为『[host-contract.*] namespaced sections of the host's .tracks/projects/project.toml are the machine truth』，与 §0.1/§1.0.3/§3.1、interfaces §1e 及 §2 本项 kind:stub 声明完全一致；签名未变动。全部 11 个桩已复查，运行时代码与桩中独立合同路径表述 grep 零命中，双合同真相歧义不再进入实现基线。
>> **Prism:** 已复核 commit 982371b：docstring 现指向 [host-contract.*] sections of .tracks/projects/project.toml，全仓 grep 无独立路径残留（仅剩二择一说明的合法提及），签名未动。验证通过。

本节以外不创建 scaffold。上述 stub 的行为体/接线（kernel/release 注册到 machine、cmd_release 与 USAGE/TRAC_SUBCOMMANDS 同步、EVENT/COMMAND 封闭集追加、envelope 行为体与 OpencodeBackend/FakeBackend 接入、github.py needs_attention/映射/CI 回读、validate 的 host-contract 映射与语言 token 扩展、既有代码 venv/wheel token 迁移、trace v0.8 扩展、CI release-evidence 旅程步）均为**待实现 Devon foundation tasks**；本文不得把它们当作既有可执行能力。`tests/ground_truth/` 不新增文件：test-plan §3 判定不适用（既有资产继承且不修改）。

## 3. 技术选型

### 3.1 host-contract 并入 project.toml 的命名空间段

`.tracks/projects/project.toml` 是 v0.4 起的测试执行合同（project.py loader、layout/lint 消费者众多）；发布闭环合同（gates/security/operations）语义与生命周期不同（随版本演进、由 M-VERIFY 消费）。FR-0281 提供 "project.toml / host-contract.toml" 二择一：本仓取**并入 project.toml 的 `[host-contract.*]` 命名空间段**——Runtime 的 scaffold 写域只认可既有 project.toml 路径，且单一合同文件避免双真相；`host-contract.` 前缀使两种合同的 table 名互不冲突、提取无歧义（封闭集见 interfaces §1e），既有 project.py loader 对新增段不解析不破坏（未知段的校验扩展是待实现 foundation task）。代价是 guard registry 第 8 项的 config_digest 输入 bytes 随 host-contract 演进而变化——每次合同修订必须同步 §4.2 声明 digest（与本文件同 commit），由 parity fail-closed 强制。

### 3.2 安全扫描工具

pip-audit==2.7.3（依赖漏洞审计，Apache-2.0）+ bandit==1.8.3（静态安全 lint，Apache-2.0）：Python 生态标准件、离线可跑（pip-audit 需查询 OSV/PyPI——它默认联网。设计取舍：M-SECURITY 的 pip-audit 需要网络访问 advisory 库，这与「本地验证」有张力）。裁定：pip-audit 允许网络（advisory 数据是外部事实，属 D-18 外部依赖分层——L2 用本地缓存/stand-in advisory，L3 真实网络；缺网络时按合同 fail-closed 落 `security=unknown` 阻断，不静默通过）。工具安装由合同 `install` 声明、Runtime 执行，不进入 pyproject dev 依赖（不改变守卫 registry 的 config_digest 输入集）。

### 3.3 envelope fenced block 而非 stdout JSON 行协议

Agent 后端输出含散文与工具噪声；要求全文恰好一个 tagged fence（```tracks-envelope```）使解析确定且对噪声免疫，比「最后一个 JSON 对象」可判别（多/零即错），比独占 stdout 更贴近现有 opencode 会话形态。FakeBackend 原生产出 envelope；OpencodeBackend 替换其提取器。风险：prompt 约束必须严格；以 v0.7 malformed 语料回归兜底。

### 3.4 CI 回读与 Issue/Project 复用 effects/github REST 通道

GitHub Actions runs / Issues / milestones / releases 全部走既有 urllib REST 边界（不引入 PyGithub 依赖）；新增 `TRAC_GITHUB_API_BASE` 环境变量（默认 api.github.com）作为 L2 stand-in 通道 seam——测试/模拟模式专属，显式设置才生效，真实模式不因缺省降级。

### 3.5 语言 token 迁移（NFR-0147 收尾）

现存 `.venv/bin/...` 字面量（executor/{executor,test_select,quality_gate,worktree}.py 的 argv0 等价集、m_impl_runtime 的 guard 命令文本、kernel/contracts.py criteria 文案）迁出：argv0 等价改为对照**当前合同声明的 argv0 集合**（contract data）判定；guard 命令文本改由 registry §4.2 数据驱动生成；criteria 文案去 token 化（以「守卫命令」指称）。迁移由单一 Devon foundation task 承载、以扩展后的 `scan_language_tokens` 全绿验收；不得以 allowlist 豁免产品代码。

### 3.6 reference host 复用 wheel 资产物化路径（而非专用安装器）

镜像 v0.7 demo_host：资产随 wheel（package-data 显式 allowlist）、Runtime 在隔离目录逐字节部署 + fresh venv + non-editable 安装。差异点：reference host 绑定真实远程并走全发布旅程，其 registry host="reference-host"、required checks 仅 lint/coverage/test（reference 产品无 tracks 侧 deliverables/trace/reach 自检）。代价是 wheel 增补少量资产；换来与 demo 同一 loader/registry/contract 消费链，无第二套 parser。

### 3.7 version gate 不绑定版本文件（tag 推导式）

`[version]` 的 file/key/expect 绑定设计为可选且 tracks/reference 均不声明：两宿主的 pyproject.toml 都是 guard registry 的 config_digest 输入，若版本号 bump 进入候选提交，六条 pyproject-backed 声明 digest 必然漂移、parity 永久 blocked。改为 tag 推导式 gate（目标 tag 远端不存在 + 推导输入记录）后，发布版本权威 = run 目标 version + tag 模板 + 远端 tag 普查，与仓库文件解耦；wheel 元数据版本不作为发布门禁事实（与本仓库 pyproject version 跨 v0.5～v0.7 不随版本推进的既有实践一致）。代价：artifact 文件名版本可能与 release tag 数字不同步；release.trace 以 artifact digest（而非文件名版本）绑定同一性。需要文件版本绑定的宿主（如对外分发的库）可在自己的 host-contract 声明 file/key/expect，并自行承担其 registry digest 联动修订。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

既有三层合同逐字不变：framework=`pytest`；paths=`tests/unit/`、`tests/integration/`、`tests/e2e/`；collect/run/run_selected 使用宿主 `.venv/bin/python -m pytest`；cwd=`.`；`{result}` 与 `{nodes}` 展开所有权与 worker/JUnit flags 内嵌不变；`[nightly]`、`[adapter]`、`[layout]`、`[lint]` 不变。v0.8 新增测试资产全部落在既有 Shield 域（tests/integration、tests/e2e、tests/e2e_live、tests/assets、tests/counterexamples、tests/_support），无需修改 layout。发布闭环宿主合同位于本文件的 `[host-contract.*]` 段（schema 见 interfaces §1e）；`trac validate --file .tracks/projects/project.toml` 对该段的 schema 校验是待实现 Devon foundation task（既有 canonical 路径校验入口不变）。

### 4.2 Canonical quality guard registry 与 host-contract

以下 TOML block 是 tracks 宿主 guard registry 的 v0.8 canonical 真相（承接 ARCH-007 §4.2，仅三处输入变更：pyproject.toml 因 package-data allowlist 增删 reference_host 资产行而 bytes 变化，六条 pyproject-backed config_digest 同步为新值 `b9ac8d99…`；`.tracks/projects/project.toml` 因追加 `[host-contract.*]` 段而 bytes 变化，第 8 项 config_digest 同步为 `a01f94da…` 且 config_sections 追加 `host-contract`；`.flake8` bytes 未变）。字段语义、digest 公式（单文件 sha256(raw bytes)；多文件 path→sha256 canonical JSON）、八类强制、fail_closed、禁 `--exit-zero` 全部继承 ARCH-007 §4.2 / IF §1k，不再重复。

```toml
[quality_registry]
version = 1
host = "tracks"

[[quality_guard]]
id = "lint-format"
category = "lint_format"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff", "tool.ruff.lint"]
config_digest = "sha256:b9ac8d992fd344c070b66b8b731577a277c226bf9065b099eb750115432e7ecb"
scope = ["tracks", "tests"]
threshold = "line-length=100; select=E,F,W,I,B,UP,SIM,C4; ignore=SIM108; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "static-semantic"
category = "static_analysis"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff.lint"]
config_digest = "sha256:b9ac8d992fd344c070b66b8b731577a277c226bf9065b099eb750115432e7ecb"
scope = ["tracks", "tests"]
threshold = "F and B semantic rule families; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "cognitive-complexity"
category = "cognitive_complexity"
tool = "flake8+flake8-cognitive-complexity"
tool_version = "7.3.0+0.1.0"
command = ".venv/bin/flake8 tracks"
config_paths = [".flake8"]
config_sections = ["flake8"]
config_digest = "sha256:f899995c15557184f3a2d082469fcd7f15ae8f22928823249e6479a611c46382"
scope = ["tracks"]
threshold = "CCR001 max-cognitive-complexity=15; tests exempt"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "file-length"
category = "file_length"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=C0302 tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.format"]
config_digest = "sha256:b9ac8d992fd344c070b66b8b731577a277c226bf9065b099eb750115432e7ecb"
scope = ["tracks", "tests"]
threshold = "C0302 max-module-lines=1200"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "method-length-locals"
category = "method_length_locals"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0915,R0914 tracks"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.design"]
config_digest = "sha256:b9ac8d992fd344c070b66b8b731577a277c226bf9065b099eb750115432e7ecb"
scope = ["tracks"]
threshold = "R0915 max-statements=50; R0914 max-locals=15; tests exempt"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "duplication"
category = "duplication"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0801 tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.similarities"]
config_digest = "sha256:b9ac8d992fd344c070b66b8b731577a277c226bf9065b099eb750115432e7ecb"
scope = ["tracks", "tests"]
threshold = "R0801 min-similarity-lines=5; comments/docstrings/signatures ignored"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "coverage-threshold"
category = "coverage_threshold"
tool = "coverage+pytest"
tool_version = "7.15.2+9.1.1"
command = ".venv/bin/coverage report --fail-under=95"
config_paths = ["pyproject.toml"]
config_sections = ["tool.coverage.run", "tool.coverage.report"]
config_digest = "sha256:b9ac8d992fd344c070b66b8b731577a277c226bf9065b099eb750115432e7ecb"
scope = ["tracks"]
threshold = "line coverage >=95; by=collected; source omit=none"
timeout_seconds = 1800
failure_policy = "fail_closed"
execution_points = ["runtime", "ci"]
required_check = "coverage"

[[quality_guard]]
id = "hooks-runner-ci-required"
category = "hooks_runner_ci_required_checks"
tool = "git-hooks+github-actions"
tool_version = "git-env-fingerprinted+checkout@v4+setup-python@v5"
command = "sh .githooks/pre-commit"
config_paths = [".tracks/projects/project.toml"]
config_sections = ["unit", "integration", "e2e", "adapter", "host-contract"]
config_digest = "sha256:a01f94da8565ec7b3c5aa8f7eab3c18e283f67f2e339428407dd060e531f8b5d"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test,deliverables,trace,reach; milestone=release-evidence"
timeout_seconds = 3600
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test,deliverables,trace,reach"
```

`.tracks/projects/project.toml` 的 `[host-contract.*]` 段（本版物化）与 registry 的关系：`[[host-contract.local_gate]] kind="quality"` 以 `source="guard_registry"` 消费本节 registry 的 canonical 命令与阈值——registry 仍是守卫唯一真相，host-contract 段不复制任何阈值；`trac validate`（待实现扩展）校验两者引用一致性。第 8 项 config_digest 因本文件追加 host-contract 段而更新为本节声明的 `a01f94da…`（唯一真相即上方 TOML block 与 §4.2 引言，二者一致），config_sections 同步追加 `host-contract`（section 存在性校验）；pyproject.toml 的变更为纯追加 reference_host package-data allowlist 行，六条 pyproject-backed config_digest 随新 bytes 同步为 `b9ac8d99…`。pip-audit/bandit 的 install/执行由 `[[host-contract.security_scan]]` 声明，Runtime 在 M-SECURITY 执行——不进入守卫 registry（其 config_digest 输入集不含新文件）。

> **Prism [RESOLVED]:** [PRISM-ARCH008-R2-01][blocker] §4.2 收尾段与 canonical registry 自相矛盾的 guard #8 digest 声明。本段声明『第 8 项 config_digest …更新为 dd4d84cc…』，但同节 registry TOML block（hooks-runner-ci-required，L423）与 §4.2 引言（L296）均声明 sha256:c0237faa…，且实测 .tracks/projects/project.toml 当前 bytes 的 sha256 即为 c0237faa…。同一 canonical 节内出现两个互斥 digest 值，其一必为假——这是 attempt-1（独立 host-contract.toml 形态）的陈旧残留。同段『pyproject.toml 因 package-data allowlist 移除已删资产行而更新』亦与事实不符：scaffold commit 对 pyproject.toml 是纯追加（9 行 reference_host allowlist，无删除），§2 宣言的『追加 reference_host 资产』才是正确描述。预期修订：整段订正为 c0237faa… 并改写 pyproject 变更描述为纯追加，或直接删除该重复收尾段，使 §4.2 全文只存在一个 digest 真相。
>> **Archer:** 已按『单一 digest 真相』订正：§4.2 收尾段现声明第 8 项 config_digest 为本节 TOML block 与引言一致的同一值（config_sections 含 host-contract），pyproject.toml 变更描述改写为『纯追加 reference_host package-data allowlist 行，六条 pyproject-backed digest 随新 bytes 同步为 b9ac8d99…』。附带说明：处理 PRISM-IF008-R2-03 时为使物化合同注释与 schema 自洽，同步了两份 project.toml 的占位符注释，project.toml bytes 再变一次——§4.2 全部声明（引言/TOML block/收尾段）已统一为最终值 a01f94da…（reference 侧为 36307614…），并已程序复核 tracks 8 项 + reference 8 项 config_digest 全部匹配当前真实 bytes。残留的 dd4d84cc/c0237faa 字样仅存在于本线程引用文本（历史记录，不改写）。
>> **Prism:** 已复核 commit 982371b：引言/TOML block/收尾段三处统一为 a01f94da… 且实测 bytes 一致（16/16 全量 digest 匹配，含 reference 侧 36307614…），pyproject 变更描述已订正为纯追加。验证通过。

### 4.3 CI / pre-commit / release

- Stable required checks 继承不变：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`；job 名不改。M-VERIFY 的 CI 回读即校验本组 required checks + 当前 workflow `ci.yml` 的 run（head SHA==candidate、conclusion=success、required 全绿）。
- pre-commit hook（Runtime 由 registry 部署）不变；v0.8 无新增本地 hook 项。
- `release-evidence` milestone 硬门禁扩展（**待实现 Devon foundation task**：在既有 tag-triggered job 内追加 release-journeys 步）：凭据探针追加 TRAC_JOURNEY_REMOTE_TOKEN/TRAC_JOURNEY_REPO（tracks 旅程专用测试远程）与 TRAC_REFERENCE_HOST_TOKEN/TRAC_REFERENCE_HOST_REPO（reference host 远程）；运行双宿主六旅程（live agent 通道沿用 TRAC_LIVE_* secrets），`trac report` 导出六旅程 release.trace 并校验同一性；缺任一凭据 fail（never skip）。live-opencode weekly job 不变。
- 发布 tag 的产生本身走 M-PUBLISH（dogfooding）；CI 的 tag 触发与 Runtime 推 tag 幂等键不冲突（reconcile 以远端为准）。
- 安装命令（副作用归 Runtime）不变：`python -m venv .venv`、`.venv/bin/pip install -e '.[dev]'`、`git config core.hooksPath .githooks`。

### 4.4 Integration/e2e 基础设施

- Shield integration 资产落 `tests/integration/test_{verify_*,security,release_*,publish_*,milestone,journey_*,envelope_*,failure_*,host_contract,reference_host,issue_*,pipeline_regression,release_trace,failclosed_release}.py`；e2e 仅 `test_release_journey.py`、`test_hotfix_release_journeys.py`、`test_reference_host_journey.py`、`test_release_journeys_matrix.py` 四条 happy path；live 旅程落 `tests/e2e_live/test_release_journeys_live.py`（L3）。
- L2 stand-in 基础设施（Shield 建于 `tests/_support/`）：GitHub REST 子集 stand-in 服务（issues/actions runs/milestones/releases/projects cards，监听 loopback、由 `TRAC_GITHUB_API_BASE` 指向）+ 本地 bare git 远程工厂。模拟通道显式性：仅当测试设置该 env/overlay 时生效，产品缺省 api.github.com。
- fault 注入经公开门禁与隔离 repo 副本；不 mock kernel/executor。release 子命令（待实现 foundation task）的 CLI 断言经真实子进程调用。
- deterministic suite 默认离线（stand-in loopback 不算外网）。

### 4.5 Build / artifact

build backend 与 wheel 名不变。M-VERIFY build gate 用 `pip wheel --no-deps`（无新依赖）；artifact digest = 产物 sha256；package-data allowlist 增 reference_host 资产（§2）。发布 artifact 附着 GitHub release 由 M-PUBLISH `artifact:` 步执行。smoke gate 在 `$TMPDIR` 隔离 prefix 安装并调用 `{prefix_bin}/trac --help`（reference host 为 hostcalc 入口）。

### 4.6 发布恢复

所有 v0.8 事件 append-only。冻结/门禁/CI 观测/preview/决定/计划/执行/封存各事件是 WAL：reconcile 以幂等键（candidate SHA、preview digest、operation key、repo+baseline digest）与远端 readback 去重；崩溃后 `trac replay` 重建 + run 续跑（resume 别名为显式形态）。归档失败走 RETRY_TAIL 只重试收尾。stand-in/测试远程与真实远程同一 reconcile 代码路径。

## 5. 有意识简化与风险

### 5.1 有意识简化

- M-SECURITY 的「深度审计」实现为合同 pinned 的机器扫描 + Prism 安全策略复审派发，不引入独立审计 agent 角色；语义由 RP-01 #4 约束。
- Projects 关联沿用 classic projects columns API（TRAC_GITHUB_PROJECT 声明才启用）；机器可验证的生命周期以 milestone API 为 canonical（project.closed 仅在声明时发出）。
- stand-in GitHub 服务只实现发布闭环消费的 REST 子集，不追求协议全量；L3 真实通道补足。
- 活跃 release 分支判定取「远端存在与目标 base version 同名的 release/{minor} 分支」这一确定性规则，不做分支活跃度启发式。
- Ground Truth 不适用（test-plan §3）：预期来自事件 schema、git/远端事实与 fixture 本身；digest 用标准库 sha256 局部重算。
- 双宿主旅程中 tracks 侧的 L3 旅程在专用测试远程（journey repo）执行而非生产 tracks 仓库；生产仓库的 v0.8 发布本身另行 dogfood 全链。两者共用同一代码路径。

### 5.2 风险

- **pip-audit 依赖 advisory 网络**：L1/L2 需 stand-in advisory 或缓存；缺网络时 security=unknown fail-closed 会阻断发布（预期行为，但要求 CI 提供网络或显式 stand-in）。
- **live 六旅程的成本与凭据面扩大**：milestone 门禁新增两对 repo/token secret；凭据缺失即 fail——需要 repo 变量预配置，否则 v0.8 无法发布（设计如此，Maestro 裁定 T-003 A）。
- **语言 token 迁移触碰既有执行路径**（argv0 等价、worktree 资产链接）：以迁移前后同输入同输出回归收口；任何行为差异即缺陷而非风格。
- **envelope 解析收紧的迁移期失败**：真实 agent 若不产出合规 fence 会 format_error；prompt 模板与 malformed 语料回归必须先行（foundation task 排序约束）。
- **candidate 冻结要求 M-VERIFY 进入时 clean tree**：M-IMPL 收尾若有残留未提交产物会反复 needs_attention；依赖既有单写者/提交纪律保证收敛。
- **六旅程 kill -9 矩阵的稳定性**：中断注入必须落在确定位点（事件 append 后、效果执行前），以 WAL 断点驱动而非 sleep race。
