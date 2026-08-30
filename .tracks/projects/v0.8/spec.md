---
spec_id: SPEC-008
created: 2026-08-30
status: draft
sha:
---

# 可信发布闭环与流程合同收敛（v0.8） — 需求规格

> 本 spec 为 v0.8 单 spec 收敛：优先覆盖 seed 问题 2、3、4、6、7、8、9 并对问题 5 做回归核验，问题 1（自举 Runtime 安全边界）明确排除。容量分析：19 有效 FR（<30），无需拆分；后续仍需 Human 裁定拆分时由 Sage 另行提案。保持 kernel/provider/language 中性；宿主特定实现仅以 adapter/provider/reference materialization 存在。

## 界面与入口

### E-01 独立 `trac release` Human 发布门禁（新增）

```
$ trac release preview
preview: candidate=abc1234... preview_digest=sha256:… artifact=sha256:… ci_run={repo,workflow,run_id,head_sha} evidence_digests={full_f,local_gates,security} operation_plan=sha256:… status=awaiting_release stale_reason=none

$ trac release --action release
decision: release (candidate=abc1234… preview_digest=sha256:…) status=approved

$ trac release --action delay --reason "…"
decision: delay (candidate=abc1234… preview_digest=sha256:…) status=delayed

$ trac release --action return --to M-DESIGN --reason "…"
decision: return (candidate=abc1234… preview_digest=sha256:…) status=returned

# 失败门禁或 stale preview 时拒绝
$ trac release --action release
error: release rejected — gate failed (m-verify/prism/security) or preview stale (candidate drift); preview_digest mismatch; run `trac release preview` and `trac status`
$ echo $?
1
```

### E-02 `trac run/status/replay/report/check` 发布流水观测（沿用并扩展）

```
$ trac run
[run 01KZ...] stage.entered(M-VERIFY) candidate=abc1234...
[run 01KZ...] candidate.frozen (candidate_sha=abc1234… clean_tree=true)
[run 01KZ...] evidence.reused kind=full_f candidate=abc1234… identity=unchanged  # 或 full_rerun
[run 01KZ...] local_gate.passed kind=quality candidate=abc1234…
[run 01KZ...] local_gate.passed kind=trace candidate=abc1234…
[run 01KZ...] ci.run_observed repo=acme/host workflow=ci.yml run=12345 head=abc1234… candidate=abc1234… status=passed
[run 01KZ...] prism.verdict pass candidate=abc1234…
[run 01KZ...] stage.exited(M-VERIFY) → stage.entered(M-SECURITY)
[run 01KZ...] security.assessed policy_digest=sha256:… candidate=abc1234… status=passed
[run 01KZ...] stage.entered(M-RELEASE) preview_digest=sha256:… candidate=abc1234…
[run 01KZ...] awaiting_release candidate=abc1234… preview_digest=sha256:…
# Human 在 E-01 决策后
[run 01KZ...] publish.planned operation=merge candidate=abc1234… preview_digest=sha256:…
[run 01KZ...] publish.executed operation=merge candidate=abc1234… preview_digest=sha256:… idempotency_key=sha256:… status=done  # 或 reconciled_skip
[run 01KZ...] stage.entered(M-MILESTONE) trace=approved_ac→…→release
[run 01KZ...] milestone.sealed candidate=abc1234… trace_digest=sha256:…
[run 01KZ...] run.completed terminal=released release_tag=v0.8.0 candidate=abc1234… preview_digest=sha256:…

$ trac status
run=01KZ… stage=M-VERIFY candidate=abc1234… full_reuse=full_f ci=bound prism=pass  # 或 stage=awaiting_release / M-PUBLISH / M-MILESTONE / released|delayed|returned|needs_attention
$ trac replay 01KZ… ; trac report --run-id 01KZ… --format md
# 事件流含 candidate.frozen / evidence.reused / local_gate.* / ci.run_observed / prism.verdict / security.assessed / publish.* / milestone.*
$ trac check trace --version v0.8
trace: approved_ac→test-plan/collected_node→candidate→FULL_F→CI→Human approval→external operation→release  candidate=abc1234… status=closed
```

### E-03 GitHub Issue/CI/Project 外部绑定（API 回读）

```
$ trac status
needs_attention: issue_creation missing_credentials repo=acme/host reason=missing_token next="export GITHUB_TOKEN=…; trac run --resume"

$ trac report --run-id 01KZ…
issue_map: FR-0267→acme/host#123 (id=987 url=https://github.com/acme/host/issues/123 baseline_digest=sha256:…) api_verified=true
ci_binding: repo=acme/host workflow=ci.yml run=12345 head=abc1234… candidate=abc1234… api_verified=true
project: release v0.8 milestone closed milestone_id=55 trace_digest=sha256:…
```

### E-04 Runtime 机器合同与派发/证据/脚手架面

```
$ trac run  # assignment 派发前
parities: envelope_version=v2 schema_digest=sha256:… runtime=validator=match prompt=match skill=match fake=match
$ trac replay 01KZ…
format_error: envelope malformed version mismatch expected v2 got v1  # 或 semantic attempt failed
$ trac init
host_contract: language=python toolchain=pytest@8.3.1 quality_guards digest=sha256:… collect="pytest --collect-only" run_selected="pytest {nodes} --junitxml={result}" build="python -m pip wheel --no-deps" artifact="dist/*.whl" smoke="python -c 'import host; host.hello()'"
$ trac validate --file .tracks/project/host-contract.toml
host-contract: valid
```

## 状态与生命周期

### SM-01 发布闭环状态机

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-01.3）。

1. `M-IMPL boundary` → `M-VERIFY`：`trac run` 自动进入；进入时在 clean tree 上冻结 candidate SHA（FR-0267）
2. `M-VERIFY` → `M-SECURITY`：全部 M-VERIFY 门禁通过（SM-01.2.1：FULL_F 复用或重跑通过 + 本地验证通过 + CI API 绑定通过 + Prism 同 candidate 终审通过）
3. `M-VERIFY` → `BLOCKED`：任一门禁 fail-closed / stale / 未知或畸形（SM-01.2.2）
4. `M-SECURITY` → `M-RELEASE`：安全评估通过（SM-01.3.1：版本化合同/policy 声明的安全扫描与深度审计均 passed）
5. `M-SECURITY` → `BLOCKED`：安全未知/缺失/畸形或策略失败（SM-01.3.2）
6. `M-RELEASE` → `AWAITING_RELEASE`：preview 已生成并落盘（SM-01.4：`preview_digest` 绑定 candidate SHA + 当期 artifact/证据/operation plan digests）
7. `AWAITING_RELEASE` → `M-PUBLISH`：Human `release` 且全部前置门禁仍 passed 且 preview 未 stale（SM-01.5.1）
8. `AWAITING_RELEASE` → `DELAYED`：Human `delay`（SM-01.5.2）
9. `AWAITING_RELEASE` → `RETURNED`：Human `return` 至指定上游阶段（SM-01.5.3）
10. `AWAITING_RELEASE` → `BLOCKED`：Human `release` 时前置门禁失败或 preview stale → Runtime 拒绝授权，状态不变（SM-01.5.4，fail-closed 不可绕过）
11. `DELAYED` → `AWAITING_RELEASE`：重生成 preview 或门禁修复后回到可决策状态（SM-01.6）
12. `M-PUBLISH` → `M-MILESTONE`：宿主声明的外部操作清单全部 write-ahead + idempotency + reconcile 完成（SM-01.7.1：已完成经远端比对跳过，未完成继续）
13. `M-PUBLISH` → `BLOCKED`：外部操作声明未知/畸形或 Agent 试图执行不可逆副作用（SM-01.7.2）
14. `M-MILESTONE` → `RELEASED`：trace 闭环 + Issue/Project/milestone 归档 + 证据只读封存 + 临时 refs 清理完成（SM-01.8.1）
15. `M-MILESTONE` → `RETRY_TAIL`：发布已成功而归档失败 → 仅重试收尾，不重复外部发布（SM-01.8.2）
16. `RETRY_TAIL` → `RELEASED`：收尾重试成功（SM-01.8.3）
17. 任意状态 → 同状态（崩溃恢复）：`trac replay` 重建状态，`trac run --resume` reconcile；已完成副作用经幂等键跳过（SM-01.9）
18. `BLOCKED` / `RETURNED` → 上游：按 FR-0274/0275/0276 路由至对应阶段再进入（SM-01.10）

## 角色与权限

### RP-01 发布闭环与外部副作用权限

单写者纪律继承 v0.5/v0.6/v0.7 RP-01：任一时刻只有一个 actor 持有编辑权；Devon/Shield/Prism/Archer/Sage 不 commit/push、不推进阶段（Runtime 是唯一流程 authority 与唯一 branch/worktree authority）。

| # | 操作 | Human | Archer | Shield | Devon | Prism | Sage | Runtime | Agent* |
|---|------|-------|--------|--------|-------|-------|------|---------|--------|
| 1 | 提供产品意图、需求评审 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 2 | 声明宿主语言/工具链/依赖安装/质量守卫/测试 collect/run/build/artifact/安装后出口合同（host-contract） | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 3 | 冻结 candidate 主身份、判定 FULL_F 复用、执行本地门禁、CI API 回读 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 4 | Prism 同 candidate 最终一致性复审、安全策略复审 | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 5 | 生成 preview（绑定 candidate/artifact/证据/operation plan） | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 6 | 经独立 `trac release` 提交 release/delay/return 三择一决定 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 7 | 校验 preview 未 stale 且前置门禁已过，拒绝绕过失败门禁 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 8 | 以 write-ahead + idempotency + reconcile 执行 merge/tag/artifact/release/deploy 等外部操作 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 9 | 执行或模拟不可逆外部操作 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌（禁止） |
| 10 | 关闭 Issue（附 release trace comment）、关闭 Project/milestone、只读封存证据、清理临时 refs | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 11 | 创建真实目标仓库 Issue、持久化并 API 回读校验 FR/NFR↔repo↔issue 映射 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 12 | 派发/回收 assignment 时校验 envelope 版本 parity、分离 format/semantic verdict | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 13 | 消费失败证据（产生→append-only→选择→注入→消费→ACK→失效→replay） | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅（产生/保留/选择/注入/校验） | ❌ |
| 14 | commit/push/推进阶段/创建分支与受控 ref | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |

*Agent 指 Devon/Shield 等实现类 Agent 的统称；Sage/Scribe 仅在 M-STORY/M-SPEC 等需求阶段持有编辑权，不参与发布执行。

## 功能需求

### FR-0267 M-VERIFY 注册与 candidate 主身份冻结

- **来源**：`BS-01` / `§3.1` / Maestro 裁定 T-002 B
- **交付入口**：`E-02`（`trac run` 自动进入 M-VERIFY；`trac status/replay/report`）

Runtime 注册 M-VERIFY 为 StageDef（修改 v0.5 FR-0160 与 v0.6 FR-0246 的 M-IMPL boundary 终态语义：对进入发布闭环的 feature/hotfix，boundary 不再为终态而进入 M-VERIFY；未进入发布闭环的回落路径仍以 boundary 呈现，见 §5）。

1. **冻结点与主身份**：THE 系统 SHALL 在 run 首次进入 M-VERIFY 时，在 clean tree 上冻结完整 Git commit SHA 作为 candidate 主身份（Maestro 裁定 T-002 B），记录 `candidate.frozen` 事件并作为后续所有门禁、preview 与外部操作的唯一主身份；CI head SHA、artifact digest、合同/policy 版本 digest、FULL_F 证据与后续 operation plan 均为绑定该 SHA 的附属证据，不合成为 candidate 本身。
2. **clean 校验**：THE 系统 SHALL 在冻结前校验工作树 clean（无未提交变更且已跟踪文件无 dirty）；IF 不 clean，THE 系统 SHALL fail-closed 阻断进入 M-VERIFY 并转 `needs_attention`，SHALL NOT 以脏树生成候选。
3. **绑定不变式**：后续任一证据未绑定同一 candidate SHA、发生漂移或 stale，THE 系统 SHALL 视为 stale/fail-closed 阻断。

**用户可观察结果**：操作者在 `trac status` 看到 `stage=M-VERIFY candidate=<full SHA>`，在 `trac replay/report` 看到 `candidate.frozen` 事件及 clean 校验；dirty 树时看到 `needs_attention` 与恢复指引。

**关键失败/恢复边界**：dirty 树、冻结失败或事件未落盘 → 阻断不进入 M-SECURITY；崩溃后经 `trac replay` 重建冻结身份与事件，已落事件不重复冻结（SM-01.17）。

### FR-0268 FULL_F 复用判定与 LOCAL_GATES 重跑

- **来源**：`BS-02` / `§3.1` / 承接 v0.6 FR-0254（注册生效）
- **交付入口**：`E-02`（`trac run` M-VERIFY；`trac status/replay`）

本条注册 v0.6 FR-0254 固化的 canonical 复用语义（此前 dormancy，随本 spec 注册后生效）：

1. **复用条件**：THE 系统 SHALL 在 M-VERIFY 判定 FULL_F 复用时，IF candidate 未漂移且与 M-IMPL 产出的干净 FULL_F 的 identity 一致且无 STALE（继承 v0.6 NFR-0130 的 `tree ∧ command ∧ env ∧ selection_id` 四元组全一致且无 STALE 传播），则 SHALL 复用该 FULL_F 证据且 SHALL NOT 本地重跑 FULL，落 `evidence.reused kind=full_f` 事件，携带 candidate SHA 与被复用证据身份。
2. **重跑分支**：ELSE（漂移、identity 不一致或含 STALE）THE 系统 SHALL 按宿主合同声明的 LOCAL_GATES 重跑 FULL（FR-0269 的本地门禁集合在 FULL 链内执行），落 `full.executed` 事件。
3. **身份一致性**：复用判定 SHALL 引用被复用证据的 `identity_basis`，stale 证据 SHALL NOT 作为通过依据。

**用户可观察结果**：`trac status` 显示 `full_reuse=full_f` 或 `full_rerun` 及判定依据；`trac replay/report` 可审计复用所引用的 FULL_F evidence identity。

**关键失败/恢复边界**：复用事件与重跑事件均 append-only；崩溃后重放时已复用不再重跑，已重跑不重复。

### FR-0269 宿主合同本地验证执行与未知/畸形 fail-closed

- **来源**：`BS-03` / `§3.1`
- **交付入口**：`E-02`（`trac run` M-VERIFY 本地验证；`trac status/replay`）/ `E-04`（host-contract，经 `trac validate` 校验）

1. **声明来源**：所有 M-VERIFY 本地验证 SHALL 由版本化宿主合同/policy 声明（由 Archer 在 M-DESIGN 物化，见 FR-0281），Runtime SHALL 仅执行合同并消费版本化 normalized result，SHALL NOT 硬编码宿主语言、测试框架、包管理器或构建工具。
2. **验证集合**：THE 系统 SHALL 按合同声明依次执行本地质量、trace/reach/反 slop、版本、build/artifact 与安装后公开出口验证；每项结果 SHALL 落 `local_gate.*` 事件并绑定 candidate SHA。
3. **未知/畸形 fail-closed**：IF 某项未声明、未知或畸形（缺工具、缺 config digest、超时、输出无法按 normalized result 解析），THE 系统 SHALL fail-closed 阻断且 SHALL NOT 猜测或降级通过；`trac status` 报告失败项与 reason。

**用户可观察结果**：`trac status` 显示各本地验证 `passed/failed` 及门禁阻断原因；`trac replay` 可审计每项本地验证的输入 contract digest 与 normalized result。

**关键失败/恢复边界**：本地验证事件 append-only；合同缺失或畸形不进入 M-SECURITY；合同变更导致已落验证 stale → 重跑对应验证（SM-01.17）。

### FR-0270 GitHub required CI API 回读与候选绑定

- **来源**：`BS-04` / `§3.1`
- **交付入口**：`E-02`（`trac run` M-VERIFY CI 门禁）/ `E-03`（GitHub API 回读面；`trac status/replay`）

1. **API 回读绑定**：THE 系统 SHALL 经 GitHub API 回读并绑定 required CI 的 repo/workflow/run/head SHA 与当前 candidate SHA 一致性；校验组合为 `repo + workflow + run_id + head SHA == candidate SHA` 且运行结论为成功；结果落 `ci.run_observed` 事件并携带 API 观测的 `repo/workflow/run_id/head SHA/candidate SHA/api_verified`。
2. **fail-closed 条件**：WHILE 不一致、缺失、非 required 或 stale（head SHA 非当前 candidate），THE 系统 SHALL fail-closed 阻断，不进入发布。
3. **凭据与网络**：缺凭据或网络不可用，THE 系统 SHALL 转 `needs_attention` 并报告原因与恢复指引（`trac status`），SHALL NOT 静默视为通过或回退为本地校验。

**用户可观察结果**：`trac status` 显示 `ci=bound|missing|mismatch|stale|needs_attention` 及 `run_id/head SHA`；`trac replay/report` 显示 API 回读事件与绑定校验。

**关键失败/恢复边界**：CI 观测事件 append-only；CI 已绑定经 replay 不重复 API 调用（除非 stale）；凭据恢复后 `trac run --resume` 重试绑定。

### FR-0271 Prism 同 candidate 最终一致性复审

- **来源**：`BS-05` / `§3.1`
- **交付入口**：`E-02`（`trac run` M-VERIFY Prism 派发；`trac status/replay`）

1. **同 candidate 复审**：THE 系统 SHALL 在 M-VERIFY 完成本地与 CI 门禁后，由 Prism 对同一 candidate 做最终一致性复审；`prism.verdict` 必须绑定 candidate SHA 并复核跨门禁一致性（FULL_F 身份、本地验证、CI 绑定、合同/policy 版本 digest 均指向同一 candidate）。
2. **阻断**：IF 复审不通过（`verdict=failed/revise`），THE 系统 SHALL 阻断进入 M-SECURITY；`trac status` 报告 Prism 失败原因。

**用户可观察结果**：`trac status` 显示 `prism=pass|fail` 及绑定 candidate；`trac replay` 显示 Prism 输入证据集合与 verdict。

**关键失败/恢复边界**：Prism verdict 事件 append-only 且绑定 candidate；Prism 失败不进入后续发布阶段；修复缺口后重审同一 candidate。

### FR-0272 M-SECURITY 合同化安全扫描与深度审计

- **来源**：`BS-06` / `§3.2`
- **交付入口**：`E-02`（`trac run` M-SECURITY；`trac status/replay`）/ `E-04`（security policy contract）

1. **策略声明执行**：THE 系统 SHALL 按版本化宿主合同/policy 声明的安全扫描与深度审计清单执行；策略版本与阈值由合同 pin，执行结果为版本化 normalized result并绑定 candidate SHA，落 `security.assessed` 事件。
2. **未知/畸形 fail-closed**：IF 结果未知、缺失或畸形（未声明策略、policy 版本不匹配、输出无法解析），THE 系统 SHALL fail-closed 阻断，SHALL NOT 推断或跳过。
3. **阶段归属**：M-SECURITY 失败 SHALL 阻止进入 M-RELEASE；通过后方可生成 preview。

**用户可观察结果**：`trac status` 显示 `security=passed|failed|unknown` 及 policy digest；`trac replay` 可审计策略声明与执行结果。

**关键失败/恢复边界**：安全评估事件 append-only；安全失败可在修复策略/代码后重跑同一 candidate 的 M-SECURITY。

### FR-0273 M-RELEASE preview 生成与绑定

- **来源**：`BS-07` / `§3.2` / Maestro 裁定 T-002 B
- **交付入口**：`E-01`（`trac release preview`）/ `E-02`（`trac report`；`trac status`）

1. **聚合绑定**：THE 系统 SHALL 在 M-RELEASE 聚合 candidate SHA、当前已验证的 CI/artifact/合同与 FULL_F 证据、风险与计划，生成绑定 operation plan 的 preview；计算 `preview_digest = hash(candidate SHA + artifact digest + 证据 digests + operation plan digest + 合同/policy digest)`，落 `release.previewed` 事件并进入 `AWAITING_RELEASE`（SM-01.6）。
2. **可审计性**：preview SHALL 经 `trac release preview` 与 `trac report` 可审计，包含 candidate SHA、preview_digest、artifact/证据/operation plan digests、风险/计划摘要与 stale 判定。
3. **stale 判定**：后续任一前置证据漂移、新 commit 产生或 operation plan 变更导致 preview_digest 不再等于当期聚合值时，THE 系统 SHALL 标记 preview stale 并在 `trac release preview` 与 `trac status` 报告 `stale_reason`。

**用户可观察结果**：操作者在 `trac release preview` 与 `trac report` 看到 preview 各绑定 digest 及 `status=awaiting_release|stale`；`trac replay` 显示 preview 生成事件。

**关键失败/恢复边界**：preview 生成事件 append-only；stale preview 不可用于 Human 授权（FR-0274）；重生成 preview 落新事件，旧 preview 不覆盖。

### FR-0274 独立 Human 三择一发布门禁

- **来源**：`BS-08` / `§3.2` / Maestro 裁定 T-001 A
- **交付入口**：`E-01`（独立 `trac release` 三择一；`trac status/replay/report`）

1. **交付面**：THE 系统 SHALL 仅允许 Human 经独立 `trac release` 以 `release` / `delay` / `return` 三择一决定放行；决定 SHALL append-only 落 `release.decided` 事件并绑定 `preview_digest` 与 `candidate SHA`；`trac approve` 与 GitHub UI/comment SHALL NOT 作为发布授权面。
2. **前置校验与 fail-closed**：THE 系统 SHALL 在接受 `release` 前校验 preview 未 stale 且全部前置门禁（M-VERIFY/M-SECURITY）已过；WHILE 前置门禁失败或 preview stale，THE 系统 SHALL 拒绝授权（fail-closed）且 Human 决定 SHALL NOT 绕过失败门禁（SM-01.10）。`delay` 与 `return` 亦 SHALL 绑定当期 preview/candidate 并校验 stale（stale 时拒绝，提示重生成 preview）。
3. **状态转移**：`release` 通过校验 → 进入 M-PUBLISH（SM-01.7）；`delay` → `DELAYED`（SM-01.8）；`return` → 回到指定上游阶段（SM-01.9），目标之后的证据/operation plan 标记 stale。

**用户可观察结果**：`trac status` 显示 `decision=release|delay|return` 及 `preview_digest/candidate` 绑定；拒绝时显示 `rejected: gate failed or preview stale` 与下一步；`trac replay/report` 可审计 Human 决定事件。

**关键失败/恢复边界**：Human 决定事件 append-only；拒绝不产生阶段转移；Human 长时未决策时发布停留 `AWAITING_RELEASE`，操作者经 `trac release preview` 与 `trac status` 看到 stale 原因与下一步。

### FR-0275 M-PUBLISH 外部操作幂等执行与远端 reconcile

- **来源**：`BS-09` / `§3.2`
- **交付入口**：`E-02`（`trac run` M-PUBLISH；`trac status/replay`）/ `E-03`（远端 Git/artifact/release/deploy 状态）

1. **声明来源**：外部操作清单（merge/tag/artifact/release/deploy 等）SHALL 由版本化宿主合同/policy 声明；未声明的操作 SHALL NOT 执行。
2. **幂等执行**：THE 系统 SHALL 对每项外部操作以 write-ahead（先落 `publish.planned` 意图事件）+ `idempotency_key = hash(preview_digest + operation_kind + operation_target)` + reconcile 执行；已完成副作用经远端实际状态比对（Git 远端 `ls-remote` / tag 存在性 / GitHub release/artifact API 回读）后 SHALL 跳过重复，已落 `publish.executed status=reconciled_skip`；未完成继续执行并落 `publish.executed status=done`。
3. **权限隔离**：Agent SHALL NOT 执行或模拟不可逆副作用；外部操作权限仅 Runtime 持有（RP-01 #8/9）；检测到 Agent 试图触达外部操作面 SHALL fail-closed。
4. **崩溃恢复**：任意点中断后 `trac replay` 重建意图与幂等键，`trac run --resume` reconcile 已完成不重复（SM-01.17）。

**用户可观察结果**：`trac status` 显示 `publish=planned|executing|reconciled_skip|done` 及各操作幂等键；`trac replay` 显示 `publish.planned/publish.executed` 事件链与远端比对结果；外部远端（`git log/tag`、GitHub release）与本地事件互验一致。

**关键失败/恢复边界**：远端不一致（manual tag/push、并发发布）以远端为准避免重复或错过；未知或畸形操作声明 fail-closed 阻塞。

### FR-0276 M-MILESTONE 归档与生命周期闭环

- **来源**：`BS-10` / `§3.2`
- **交付入口**：`E-02`（`trac run` M-MILESTONE；`trac status/replay/report`）/ `E-03`（Issue/Project/milestone 归档面）

1. **trace 闭环**：THE 系统 SHALL 在 M-MILESTONE 形成 `approved AC → test-plan/collected node → candidate 证据 → FULL_F → CI → Human approval → 外部 operation → release` 的可审计 trace 闭环；trace 落 `milestone.trace_closed` 事件并含 `trace_digest`。
2. **生命周期**：THE 系统 SHALL 按审计时机执行 Issue/Project/milestone 生命周期（关闭与审计 comment，见 FR-0284）、证据归档只读封存与临时 refs 清理（`refs/trac/tmp/*` 等）；清理后 `trac replay` 显示 `milestone.sealed` 与 `refs.cleaned`。
3. **成功后仅重试收尾**：IF 发布已成功而归档失败，THE 系统 SHALL 仅重试收尾（SM-01.15 → SM-01.16）且 SHALL NOT 重复已成功的外部发布（FR-0275 的已完成幂等跳过保证）。

**用户可观察结果**：`trac report` 可导出不可变 release trace 并与外部 tag/release 互验；`trac status` 显示 `terminal=released` 或 `retry_tail`；Project/milestone 状态与 release 一致，临时 refs 无残留。

**关键失败/恢复边界**：归档失败不触发重复发布；M-MILESTONE 事件 append-only；崩溃后重放不重复封存。

### FR-0277 Feature 与两类 hotfix 真实发布旅程

- **来源**：`BS-11` / `§3.3` / 承接 v0.6 FR-0246（发布语义注册生效）
- **交付入口**：`E-02`（`trac start` / `trac hotfix --scenario` 创建；`trac run` 全旅程；`trac status/replay/report` + Git/远端验证）

1. **Feature 公开旅程**：WHEN feature 进入发布，THE 系统 SHALL 使其经 M-VERIFY 与 M-SECURITY 后产出 preview，经 Human `release` 放行后在 M-PUBLISH 合入 main、打 tag、推 artifact、发 release，完成公开版本；`trac report` 的 release trace 验证 CI/candidate/artifact/Human approval/外部 operation 同一性。
2. **post-release hotfix**：WHEN `--scenario post-release` hotfix 进入发布，THE 系统 SHALL 使其从 `fix/{issue}` 分支在 M-VERIFY 验证后合入 main，并在存在活跃 release 分支时同步修复（同步 merge 活跃 release 分支），产生 patch 发布（`patch` 语义由宿主 version contract 声明）；预验证、M-VERIFY、安全 policy 与 Human release gate 同 feature 保留。
3. **dev hotfix**：WHEN `--scenario dev` hotfix 进入发布，THE 系统 SHALL 使其仅合入活跃 release 分支，沿既有 pre-release/开发渠道交付，SHALL NOT 打公开 tag/release 冒充公开版本。
4. **同一性与幂等**：三旅程的发布操作均 SHALL 受同一 candidate SHA 绑定与逐操作幂等约束（FR-0275），已合入/已发布后 reconcile 不重复 tag/release；无活跃 release 分支时 dev hotfix 不成立 → `precheck failed` fail-closed（SM-01.10）。

**用户可观察结果**：Feature 产生公开 tag/release；post-release 产生 patch tag/release 并视情况同步活跃分支；dev hotfix 随活跃分支预发布交付；均可在 `git log` / `git tag` / GitHub release 与 `trac report` 的 release trace 验证同一性。

**关键失败/恢复边界**：分支存在性 precheck fail-closed；已发布经远端 reconcile 不重复；中段 ac_gap/spec_gap 仍按 v0.6 FR-0248 退出 hotfix 转 backlog（不进入发布）。

### FR-0278 结构化 Agent 合同统一 envelope 与单一解析

- **来源**：`BS-12` / `§3.4`
- **交付入口**：`E-04`（Runtime 派发与回收面；`trac replay/report`；`trac validate`）

1. **统一 envelope/schema**：THE 系统 SHALL 对所有角色与 assignment kind 的输入 assignment/evidence 与输出 outcome/verdict 使用统一、版本化、可判别的 envelope + JSON schema；envelope 必须含 `kind` 与 `version` 字段，仅支持单一确定性解析路径，废弃“最后一个 JSON”、角色专属启发式扫描与散文猜测等多路径回退。
2. **示例与 fixtures 约束**：所有示例与 fixtures SHALL 由 schema 生成或经真实消费者 validator 校验通过；游离手写 JSON SHALL NOT 作为有效输入输出样本。

**用户可观察结果**：`trac replay` 显示 envelope 版本与解析结果；非法 envelope 显示 `format_error` 与拒绝原因。

**关键失败/恢复边界**：缺 `kind/version` 或未知版本 → `format_error` fail-closed；解析结果可程序复核。

### FR-0279 合同版本 parity、format/semantic 分离与畸形回归

- **来源**：`BS-12` / `§3.4`
- **交付入口**：`E-04`（Runtime 派发前 parity 面；`trac replay/report`）

1. **parity 校验**：THE 系统 SHALL 在派发前对 envelope 做 parity 校验：Prompt/agent/skill/fake backend/真实 backend/Runtime validator 引用的合同版本必须一致；IF 不一致，THE 系统 SHALL fail-closed 拒绝派发，落 `dispatch.rejected reason=version_parity_mismatch` 事件，不进入 Agent 执行。
2. **format vs semantic 分离**：THE 系统 SHALL 分离格式失败与语义 attempt 失败并落分离的 verdict 事件（`format_error` vs `semantic_attempt_failed`）；malformed 输入 SHALL NOT 污染业务状态。
3. **畸形回归语料**：THE 系统 SHALL 以 v0.7 全部 malformed response 类故障为回归语料，使畸形输入 fail-closed 可回归验证；`trac replay` 展示 format 与 semantic 的分离及版本一致性。

**用户可观察结果**：派发前不一致被拒绝且事件可审计；format 与 semantic 分别落事件，畸形不入业务状态。

**关键失败/恢复边界**：format 失败不计为 semantic attempt；业务状态不因 malformed 变更。

### FR-0280 失败证据端到端 review 与一致可审计消费

- **来源**：`BS-13` / `§3.5`
- **交付入口**：`E-04`（Runtime review 链面；`trac replay/report`）/ `E-02`（`trac status` needs_attention）

1. **端到端 review**：THE 系统 SHALL 对失败证据全链做 review，核验产生 → append-only 保留 → 按轮次/来源选择 → 注入目标 assignment → Agent 消费 → ACK → 失效 → replay 各环节；已有 infra 未覆盖的 `last_failure` / `diagnose_report` 等机制如果存在 SHALL 保留。
2. **最小 delta 原则**：THE 系统 SHALL 仅按 review 证明的真实缺口实施最小 delta，SHALL NOT 预设复杂 `failure_history`；review 未发现缺口则不产生额外改动。
3. **不覆盖与一致合同**：普通失败发生时 THE 系统 SHALL NOT 静默覆盖仍需消费的富证据（仍需消费的 last_failure/diagnose_report 等 SHALL 保留至被对应 Agent ACK 或显式失效）；所有相关角色 SHALL 拥有 consistent 的 evidence 输入合同（含轮次/来源选择规则）与可机械审计的消费证明（ACK/失效/replay 事件可回放）。

**用户可观察结果**：`trac replay/report` 可审计失败证据的产生、选择、注入、消费、ACK、失效与 replay 环节；`trac status` 在需消费证据未 ACK 时提示 `awaiting_evidence_ack`。

**关键失败/恢复边界**：富证据丢失或错配 → review 失败并 fail-closed；重放后与中断前一致。

### FR-0281 Archer 语言中性 scaffold 与首构合同物化及容错回流

- **来源**：`BS-14` / `§3.6`
- **交付入口**：`E-04`（Archer M-DESIGN 物化面；`trac init` / `trac validate --file host-contract.toml`）/ `E-02`（`trac run` 执行与回流面）

1. **合同选择与物化**：WHEN 新 host 初始化 `trac init` 或首个 feature 进入 M-DESIGN，Archer SHALL 选择并物化宿主合同：声明语言与工具链、依赖安装、质量守卫 pinned tool/config digest/scope/threshold、测试 collect/run_selected、build/artifact 与安装后公开出口 smoke；写入版本化 host-contract（`project.toml` / `host-contract.toml`）并落 `host_contract.materialized` 事件。
2. **Runtime 语言中性执行**：Runtime SHALL 仅按合同执行命令并消费版本化 normalized result，SHALL NOT 理解 venv/pytest/wheel 或任何语言语义；未知语言/框架或畸形结果 SHALL fail-closed 并落 `host_contract.invalid` 事件。
3. **容错回流**：IF 安装、编译/import、collect、build 或 smoke 失败，THE 系统 SHALL 形成机器证据（exit code/stdout/stderr/normalized result）并回 M-DESIGN 由 Archer 修订，Runtime SHALL NOT 猜正确命令；`trac status` 显示 `needs_attention: host_contract revise`。

**用户可观察结果**：新 host 可在 clean 机器上按合同完成安装→collect→build→smoke；物化失败经事件可审计并回流至 Archer。

**关键失败/恢复边界**：Archer 修订后重校验 host-contract；合同缺失仍 fail-closed。

### FR-0282 Python reference host 真实物化验收与宿主机同等外部闭环

- **来源**：`BS-14` / `§3.6` / Maestro 裁定 T-003 A
- **交付入口**：`E-04`（reference host 初始化与验收面；`trac init` / `trac run`）/ `E-03`（reference host 专用真实 GitHub 远程）

1. **至少一个全新 Python reference host**：THE 系统 SHALL 支持至少一个从零创建的全新 Python reference host 的真实物化验收（与 tracks 自身同等走 FR-0267~FR-0277 的发布旅程，含真实 GitHub 外部依赖，见 FR-0283/FR-0270/FR-0275）。
2. **双宿主同等外部依赖**：reference host SHALL 绑定专用真实 GitHub 远程，并与 tracks 自身一样真实执行 Issue 创建/readback、required CI SHA 绑定以及适用的 Project/milestone 生命周期（Maestro 裁定 T-003 A）；凭据或远程不可用时 SHALL 进入 `needs_attention`，该旅程不算通过，SHALL NOT 降级为本地成功。
3. **kernel 中性**：Python 细节（venv/pytest/wheel 等）仅 SHALL 落于 reference acceptance，不泄漏至 kernel 规范/接口；仅显式反例测试可使用 fake。

**用户可观察结果**：reference host 验收在 `trac report` 显示与 tracks 自身同构的 candidate/CI/artifact/Human/外部 operation 同一性；凭据缺失时 `trac status` 报告 `needs_attention`。

**关键失败/恢复边界**：reference host 外部依赖失败与 tracks 自身同等 fail-closed；本地模拟不计入通过。

### FR-0283 Issue 真实创建、映射持久化与 API 回读校验

- **来源**：`BS-15` / `§3.7` / Maestro 裁定 T-003 A
- **交付入口**：`E-03`（GitHub Issues API 面；`trac status/replay/report`）/ `E-02`（`trac run` M-REQ-APPROVAL 后流程）

1. **真实创建**：WHEN 真实 run 通过 M-REQ-APPROVAL，THE 系统 SHALL 在首个 task 派发前创建真实目标仓库 Issue；IF 凭据缺失或不可用，THE 系统 SHALL 进入 `needs_attention`（`trac status` 报告原因与恢复指引）且 SHALL NOT 静默降级为 fake。
2. **fake 隔离**：fake backend SHALL 仅由显式测试/模拟模式（如 `TRAC_FAKE_*` / `--assignment-overlay` / `assignment_simulation`）启用；真实模式下产生 FAKE-N（`FAKE-90..FAKE-103`）产物，THE 系统 SHALL 视为 fail-closed。
3. **持久化与 API 回读**：THE 系统 SHALL 持久化并经 API 回读校验 `FR/NFR ↔ repo ↔ issue id/number/url ↔ baseline digest` 映射；映射落 `issue.mapped` 事件并经 `trac report` 可审计。
4. **权威映射消费**：task、commit、report 与关闭 effector SHALL 仅消费权威映射，SHALL NOT 从 FAKE-N 或文本猜数字。
5. **崩溃幂等**：崩溃恢复时 THE 系统 SHALL 幂等校验，已创建的不重复创建（以 `repo + baseline digest` 去重键）。

**用户可观察结果**：每个 FR/NFR 有可追溯的真实 Issue 链接，commit/report 引用权威映射，`trac report` 显示映射校验；FAKE-N 在真实模式下显示 `rejected: fake_not_allowed`。

**关键失败/恢复边界**：映射缺失或 API 回读不一致 → fail-closed；断点重放不重复创建。

### FR-0284 Issue/Project/milestone 关闭时机与审计及反例保障

- **来源**：`BS-16` / `§3.7`
- **交付入口**：`E-03`（`trac run` M-MILESTONE 关闭面；GitHub Issue/Project）/ `E-02`（`trac replay/report`）

1. **关闭时机与审计**：WHEN M-MILESTONE 关闭生命周期时，THE 系统 SHALL 按审计时机关闭 Issue（附 release trace 关联 comment，含 `candidate SHA` / `preview_digest` / `release_tag`），并关闭对应 release Project/milestone 且清理临时 refs。
2. **反例保障**：THE 系统 SHALL 使 v0.7 `FAKE-90..FAKE-103` 与 `project=fake-project` 事件在反例测试中被拒绝（`issue.mapped` 缺失或 `api_verified=false` 即 fail-closed）。
3. **一致性**：Project/milestone 状态 SHALL 与 release 一致；临时 refs 清理后无残留。

**用户可观察结果**：Issue 关闭含 release trace 关联评论，Project/milestone 显示 `closed`，`trac report` 显示关闭事件与 FAKE 反例拒绝。

**关键失败/恢复边界**：关闭前映射再校验，未通过不关闭；反例未拒绝即验收失败。

### FR-0285 单次权威测试流水线回归核验

- **来源**：`§5 Out-of-Scope` 约束 / seed 问题 5 / `§3.4` 承接 v0.7
- **交付入口**：`E-02`（`trac run` M-TEST；`trac status/replay`）/ `E-04`（`trac validate`）

1. **不变式**：单次权威测试核心 `WRITE → COLLECT → Runtime RED_CHECK → PRISM_REVIEW`（Prism 消费 `red.validated` 且只做隔离 counterexample kill）SHALL 保持不变；v0.8 SHALL 仅做回归核验，SHALL NOT 以本项重新发明测试流水线。
2. **Shield 生产者自检边界**：Shield 的生产者自检 SHALL NOT 作为权威门禁，SHALL NOT 退化为普通 FULL 套件重跑；Full 套件的执行仍由 FR-0268/0269/0271 的 M-VERIFY 链与 v0.7 究竟决定。
3. **回归覆盖**：任一流水线环节缺失或 Prism 未消费 `red.validated` 的隔离 kill，回归 SHALL fail-closed。

**用户可观察结果**：`trac replay` 显示 `WRITE→COLLECT→red.validated→prism.verdict(kill)` 链路保持；缺失环节在 `trac status` 显示 `pipeline incomplete`。

**关键失败/恢复边界**：流水线改动需经新 spec 授权，非本 spec 行为。

## 非功能需求

### NFR-0143 Candidate 绑定一致性与全链可审计性

- **来源**：`BS-01` / `BS-07` / `BS-08` / `BS-09` / `§3.1` / `§3.2` / Maestro 裁定 T-002 B

Candidate 主身份冻结后的全链（M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH→M-MILESTONE）必须以 `candidate SHA` 为锚点的 append-only 事件溯源；任一证据未绑定同一 SHA、漂移或 stale 即视为失效，`trac replay/report` 可回放冻结、复用、CI 绑定、preview、Human 决定与幂等键并验证一致性。`release.trace` 必须含 `candidate SHA` / `artifact digest` / `evidence digests` / `preview_digest` / `Human approval event id` / `external operation digests` 的同一性证明。

### NFR-0144 M-PUBLISH 幂等与远端 reconcile 正确性

- **来源**：`BS-09` / `BS-10` / `§3.2`

`publish.planned` → `publish.executed` 必须满足精确一次语义：write-ahead + `preview_digest` + 逐操作幂等键 + 远端 readback reconcile。已完成副作用经远端比对后必须跳过重复，未完成必须继续；幂等键相同而操作结果不同必须 fail-closed 并落 `reconcile_conflict` 事件。并发发布或人工远端改动不得导致重复 tag/release/merge。

### NFR-0145 结构化合同解析确定性与版本一致性

- **来源**：`BS-12` / `§3.4`

所有 assignment/evidence/outcome/verdict 必须以单一确定性解析路径（envelope `kind/version` + schema）可判别，无回退启发式；Prompt/agent/skill/fake/真实 backend/Runtime validator 同引一合同版本，版本不一致必须在派发前被 `dispatch.rejected` 拒绝；`format_error` 与 `semantic_attempt_failed` 必须分离且分别落事件，malformed 不污染业务状态。

### NFR-0146 失败证据 append-only 与可审计消费

- **来源**：`BS-13` / `§3.5`

失败证据的产生、append-only 保留、按轮次/来源选择、注入、消费、ACK、失效与 replay 必须全链可回放；同一证据在仍需消费时不得被普通失败静默覆盖；各角色证据输入合同必须一致且消费证明可在 `trac replay/report` 中逐环节验证；`last_failure` / `diagnose_report` 等已有机制必须保留。

### NFR-0147 语言中性与未知合同 fail-closed

- **来源**：`BS-03` / `BS-14` / `§3.6`

Runtime 必须保持 kernel/provider/language 中性，不硬编码宿主语言、测试框架、包管理器或构建工具；Archer 声明的 host-contract 为唯一执行依据，未知语言/框架/工具或畸形 normalized result 必须 fail-closed； kernel 规范不得包含 `venv/pytest/wheel` 等 Python 细节，Python 细节仅属 reference acceptance。

### NFR-0148 Issue 权威映射与静默 fake 拒绝

- **来源**：`BS-15` / `BS-16` / `§3.7` / Maestro 裁定 T-003 A

真实 run 的 `FR/NFR ↔ repo ↔ issue id/number/url ↔ baseline digest` 映射必须为权威来源，持久化并经 API 回读校验；task/commit/report/关闭 effector 必须仅消费权威映射，不得从 `FAKE-N` 或文本猜数字；真实模式下 `FAKE-90..FAKE-103` / `project=fake-project` 事件必须被 `issue.mapped` 校验拒绝且落 `fake_rejected` 事件；凭据缺失必须转 `needs_attention`，不得静默降级为 fake。

### NFR-0149 双宿主 6 旅程可崩溃恢复与身份一致

- **来源**：`§3.3` / `§3.7` / `§2` 最终验收 / Maestro 裁定 T-003 A

tracks 自身与至少一个全新 Python reference host（专用真实 GitHub 远程）必须分别完成 feature 与两类 hotfix 的适用发布旅程（共 6 旅程计分；凭据缺失时计为 `needs_attention` 不计通过）；CI、candidate、artifact、Human approval、外部 operation 与最终 release trace 必须同一 `candidate SHA` 与同一 `preview_digest`；在 Issue 创建、CI 观测、build、merge/tag/artifact/release、归档任一点中断后必须可 `replay/reconcile`，已完成副作用不重复，错误身份、stale evidence、畸形合同、silent fake fallback 均须可观测地 fail-closed 并在 `trac report` 中互验。

## 范围排除

- 自举 Runtime 安全边界（seed 问题 1）——不进入本 spec；相关规避仅作仓库开发基础设施。
- 单次权威测试流水线重发明（seed 问题 5）—— `WRITE→COLLECT→RED_CHECK→PRISM_REVIEW` 已实现，v0.8 仅按 FR-0285 回归核验；Shield 生产者自检不作权威门禁，不得退化为普通 FULL 套件重跑。
- nightly result fetch、第二语言 adapter、RFC discussion 新状态、深版 OOB commit、通用多 registry/多部署平台——不在本版本范围，除非为发布闭环不可避免的最小依赖并先经 Human 裁定（seed 规格约束）。
- 第二语言 adapter 的 kernel 泄漏——Python 细节仅属 reference acceptance（FR-0282/NFR-0147），不进入 kernel 规范/接口。
- 通用多宿主编排与跨宿主并发调度——本版仅 tracks 自身与一个 reference host 的 6 旅程；规模化多宿主编排不在范围。
- 发布闭环之外的既有流程重制——`trac start/hotfix` 的起止与 HOTFIX-TRIAGE、M-TEST/M-IMPL 既有语义保持不变，仅新增 M-VERIFY 及后续发布四阶段的注册与执行。
