---
interfaces_id: IF-008
spec_ref: SPEC-008
arch_ref: ARCH-008
created: 2026-08-31
status: draft
sha:
---

# v0.8 — 接口与类型化 Schema：可信发布闭环与流程合同收敛

## 0. 延续性（什么不变）

- IF-001/003/004/005/006/007 的 `EventEnvelope`、`Command`、Assignment/Outcome、State 投影、D-41 selection/evidence/ledger/FULL、hotfix、doc-gap、Phase 0、guard registry、authenticity/mutation、adapter seam 与 CLI 合同全部继承；本版只追加成员与扩展 payload。
- 既有 `test.baseline_captured`、`test.selected`、`red.validated`、`full.executed`、`evidence.reused`、`evidence.staled` 的选择/结果语义与 `.tracks/projects/project.toml` 三层命令合同不变；v0.8 的 FULL_F 复用消费既有 `evidence.reused`（追加 `kind="full_f"` payload 约定），不新造事件类型。
- 既有 IF 标识不可重定义；§5 重列 inherited headings 供 validator 解析，正文定义仍以 IF-007 及其上游文档为准。
- 外部 CLI 顶层命令集合除新增 release 子命令（待实现 Devon foundation task，USAGE 同步）外不变；既有命令只扩展输出/校验。
- `prism.verdict` 事件类型不变；v0.8 以 payload `scope` 字段区分 verify_final/security 复审（既有 v0.3 语义的向后兼容扩展，缺省 scope 保持历史含义）。

## 1. 跨模块合同

### 1a. 新事件类型（封闭集追加）

事件类型是封闭集；payload 未列字段不得作为通过证据。`modules` 含 2+ 模块的行必须有 integration 覆盖。所有新事件 append-only，发布链上的事件必须携带 `candidate_sha`（NFR-0143）。

| # | event | payload（关键字段） | producer | modules |
|:--|:--|:--|:--|:--|
| 1 | `candidate.frozen` | `candidate_sha: str`, `clean_tree: bool`, `branch: str`, `frozen_at_seq: int` | m_verify executor | executor/m_verify, kernel/release, effects/github, report |
| 2 | `candidate.stale` | `candidate_sha: str`, `reason: "candidate_drift"\|"head_moved"`, `detail: str` | m_verify executor | executor/m_verify, kernel/release, report |
| 3 | `local_gate.passed` / `local_gate.failed` | `kind: LocalGateKind`, `candidate_sha: str`, `contract_digest: str`, `command_echo: list[str]`, `normalized_result: dict`, `reason: "unknown"\|"malformed"\|"missing_contract"\|"timeout"\|null` | host_contract executor | executor/host_contract, guard_registry, kernel/release, report |
| 4 | `ci.run_observed` | `status: "passed"\|"failed"`, `repo: str`, `workflow: str`, `run_id: int`, `head_sha: str`, `candidate_sha: str`, `conclusion: str`, `required_checks: list[str]`, `api_verified: bool` | github effects | effects/github, executor/m_verify, kernel/release, report |
| 5 | `security.assessed` | `status: "passed"\|"failed"\|"unknown"`, `policy_digest: str`, `candidate_sha: str`, `scans: list[dict]`, `prism_scope: "security"` | security executor | executor/security, effects/github, kernel/release, report |
| 6 | `release.previewed` | `candidate_sha: str`, `preview_digest: str`, `artifact_digest: str`, `evidence_digests: dict`, `operation_plan_digest: str`, `contract_policy_digest: str`, `risks: list[str]`, `blob_ref: str` | release_gate executor | executor/release_gate, kernel/release, cli, report |
| 7 | `release.decided` | `action: "release"\|"delay"\|"return"`, `candidate_sha: str`, `preview_digest: str`, `reason: str\|null`, `target: str\|null`, `actor: "human"` | cli（release 面内、writer lock 内） | cli, executor/release_gate, kernel/release, report |
| 8 | `release.rejected` | `action: str`, `candidate_sha: str`, `preview_digest: str\|null`, `reason: "gate_failed"\|"preview_stale"`, `detail: str` | executor/release_gate | executor/release_gate, cli, kernel/release, report |
| 9 | `publish.planned` | `operation_kind: str`, `target: str`, `preview_digest: str`, `candidate_sha: str`, `idempotency_key: str`, `when: str\|null` | publish executor | executor/publish, effects/publish, kernel/release, report |
| 10 | `publish.executed` | `idempotency_key: str`, `status: "done"\|"reconciled_skip"`, `remote_check: dict`, `candidate_sha: str` | publish executor | executor/publish, effects/publish, report |
| 11 | `publish.blocked` / `publish.failed` | `reason: "agent_forbidden"\|"unknown_operation"\|"malformed"`, `detail: str`, `idempotency_key: str\|null` | publish executor | executor/publish, backend 守卫, report |
| 12 | `reconcile_conflict` | `idempotency_key: str`, `expected: dict`, `remote: dict` | publish executor | executor/publish, effects/publish, report |
| 13 | `milestone.trace_closed` | `trace_digest: str`, `candidate_sha: str`, `preview_digest: str`, `trace_blob: str` | milestone executor | executor/milestone, checks/trace, report |
| 14 | `milestone.sealed` | `candidate_sha: str`, `seal_blob: str`, `readonly: true` | milestone executor | executor/milestone, store, report |
| 15 | `refs.cleaned` | `run_id: str`, `namespace: "refs/trac/tmp"`, `remaining: 0` | milestone executor | executor/milestone, report |
| 16 | `issue.closed` / `project.closed` / `milestone.closed` | `issue_number: int\|null`, `comment_ref: str`, `trace_digest: str`, `candidate_sha: str`, `state: "closed"` | milestone executor | executor/milestone, effects/github, report |
| 17 | `issue.mapped` | `item_id: str`, `repo: str`, `issue_number: int`, `url: str`, `baseline_digest: str`, `api_verified: bool` | github effects | effects/github, executor/hotfix, executor/milestone, report |
| 18 | `fake_rejected` | `artifact: "FAKE-*"\|"project=fake-project"`, `context: str`, `mode: "real"` | github effects | effects/github, executor/milestone, report |
| 19 | `attention.required` | `area: "issue_creation"\|"ci_readback"\|"host_contract"\|"freeze"\|"reference_remote"`, `reason: str`（如 `dirty_tree`、`missing_token`、`network_error`、`remote_unavailable`）, `detail: str`, `next: str`（恢复指引） | 各 executor/effects | kernel/release, effects/github, executor/host_contract, cli, report |
| 20 | `host_contract.materialized` | `contract_path: str`, `contract_digest: str`, `version: int` | host_contract executor | executor/host_contract, cli(init), report |
| 21 | `host_contract.invalid` / `host_contract.failed` | `reason: "unknown_language"\|"malformed_result"\|"gate_failed"`, `exit_code: int\|null`, `stdout_tail: str`, `stderr_tail: str`, `normalized_result: dict\|null` | host_contract executor | executor/host_contract, kernel/release, report |
| 22 | `dispatch.parity` | `envelope_version: int`, `schema_digest: str`, `surfaces: dict[str, str]`（prompt/agent/skill/fake/real/validator → 引用版本）, `status: "match"\|"mismatch"` | envelope kernel | kernel/envelope, executor 派发器, report |
| 23 | `dispatch.rejected` | `reason: "version_parity_mismatch"`, `surfaces: dict`, `detail: str` | envelope kernel | kernel/envelope, executor 派发器, report |
| 24 | `format_error` | `kind: FormatErrorKind`, `detail: str`, `surface: str`, `attempt_not_counted: true` | envelope kernel | kernel/envelope, effects/opencode, effects/fake, report |
| 25 | `semantic_attempt_failed` | `kind: str`, `role: str`, `detail: str` | executor | executor, kernel, report |
| 26 | `failure.emitted` / `failure.stored` / `failure.selected` / `failure.injected` / `failure.consumed` / `failure.acked` / `failure.invalidated` | `failure_id: str`, `round: int`, `source: str`（含 `last_failure`、`diagnose_report`）, `role: str\|null`, `record_ref: str\|null`, `rule: str\|null`（selected 的选择规则） | failure_review executor | executor/failure_review, effects/backend, kernel, report |
| 27 | `review.failed` | `area: "failure_evidence"`, `outcome: "evidence_lost"\|"mismatched"`, `detail: str` | failure_review executor | executor/failure_review, kernel/release, report |

既有事件的 v0.8 payload 扩展（向后兼容，缺省保持历史含义）：`evidence.reused` 增加 `kind="full_f"` 与 `identity_basis`；`prism.verdict` 增加 `scope: "verify_final"\|"security"` 与 `candidate_sha`、`evidence_digests`；`run.completed` 的 `terminal_state` 扩展取值 `released`、`retry_tail` 并增加 `release_tag`、`preview_digest`；`stage.entered` 的 stage 封闭集追加五个发布阶段。

### 1b. Command kind 追加

| # | kind | params | executor result | modules |
|:--|:--|:--|:--|:--|
| 1 | `freeze_candidate` | `run_id: str` | `candidate.frozen` 或 `attention.required(reason=dirty_tree)` | kernel/release, executor/m_verify |
| 2 | `judge_full_f_reuse` | `candidate_sha: str`, `full_f_evidence_id: str` | `evidence.reused(kind=full_f)` 或 `full.executed`（经 LOCAL_GATES） | kernel/release, executor/m_verify, adapters |
| 3 | `run_local_gates` | `candidate_sha: str`, `contract_digest: str` | 一组 `local_gate.passed/failed` | kernel/release, executor/host_contract, guard_registry |
| 4 | `observe_ci_runs` | `candidate_sha: str`, `ci_binding: dict` | `ci.run_observed` 或 `attention.required` | kernel/release, effects/github |
| 5 | `assess_security` | `candidate_sha: str`, `policy_digest: str` | `security.assessed` | kernel/release, executor/security, effects/github |
| 6 | `generate_preview` | `candidate_sha: str` | `release.previewed` | kernel/release, executor/release_gate |
| 7 | `record_release_decision` | `action: str`, `reason: str\|null`, `target: str\|null` | `release.decided` 或 `release.rejected` | cli, executor/release_gate, kernel/release |
| 8 | `execute_publish` | `preview_digest: str` | `publish.planned` + `publish.executed` 序列（或 blocked/failed/conflict） | kernel/release, executor/publish, effects/publish |
| 9 | `close_milestone` | `candidate_sha: str`, `preview_digest: str` | `milestone.trace_closed` → `issue.closed`/`project.closed`/`milestone.closed` → `milestone.sealed` + `refs.cleaned` | kernel/release, executor/milestone, effects/github |
| 10 | `materialize_host_contract` | `contract_path: str` | `host_contract.materialized` 或 `host_contract.invalid` | cli(init), executor/host_contract |
| 11 | `check_envelope_parity` | `dispatch_id: str` | `dispatch.parity` 或 `dispatch.rejected` | kernel/envelope, executor 派发器 |
| 12 | `review_failure_chain` | `run_id: str` | 通过（无事件）或 `review.failed` | executor/failure_review, kernel |

所有 kind 遵守 command.issued WAL 与 per-kind reconcile；幂等键分别为 candidate_sha / gate kind+contract_digest / operation idempotency_key / preview_digest / failure_id。

### 1c. State 投影追加

```python
release_stage: str | None = None        # M-VERIFY|M-SECURITY|M-RELEASE|M-PUBLISH|M-MILESTONE
candidate_sha: str | None = None        # frozen primary identity
candidate_stale_reason: str | None = None
full_reuse: str | None = None           # full_f | full_rerun + reason
local_gates: dict[str, str] = {}        # kind -> passed|failed|unknown
ci_binding_status: str | None = None    # bound|mismatch|missing|stale|needs_attention
prism_final: str | None = None          # pass|fail (scope=verify_final)
security_status: str | None = None      # passed|failed|unknown
preview_digest: str | None = None
preview_stale_reason: str | None = None
release_decision: str | None = None     # release|delay|return
publish_progress: dict[str, str] = {}   # idempotency_key -> planned|done|reconciled_skip
milestone_status: str | None = None     # trace_closed|sealed|retry_tail
release_tag: str | None = None
attention: dict | None = None           # {area, reason, next}
issue_map_verified: bool = False
```

合法转移遵循 SM-01；SEALED/RELEASED 不可回退；BLOCKED 经修复后重入对应阶段不改写历史。

### 1d. M-VERIFY 公共函数（IF-VERIFY-001/002/005）

**modules**: `executor/m_verify.py` 实现，executor handler 消费，adapters/guard_registry/github 提供输入，`kernel/release.py` 投影。

```python
def freeze_candidate(repo: Path) -> CandidateIdentity: ...
def judge_full_f_reuse(
    candidate_sha: str, full_f_evidence: dict,
    identity_quadruple: dict, stale_marks: tuple[str, ...],
) -> ReuseDecision: ...
def collect_binding_violations(events: list[dict], candidate_sha: str) -> list[str]: ...
def build_prism_final_review_assignment(
    candidate_sha: str, evidence_digests: dict,
) -> dict: ...
```

`freeze_candidate` 在 dirty tree（已跟踪文件有变更）时不得返回身份（调用方落 attention.required）；`judge_full_f_reuse` 仅在四元组全一致且无 STALE 时返回 reuse；`build_prism_final_review_assignment` 的输入证据集合必须全部绑定同一 candidate_sha。

### 1e. Host contract schema 与 normalized result（IF-HOSTCONTRACT-001）

**modules**: `executor/host_contract.py` 实现；M-VERIFY/M-SECURITY/M-RELEASE/PUBLISH handlers、validate、reference_host 消费。

`.tracks/projects/project.toml` 中 `[host-contract.*]` 段的 table 封闭集（FR-0281 的 "project.toml / host-contract.toml" 二择一取并入形态，architecture §3.1）：`[host-contract]`（version=1、language、toolchain、install）、`[[host-contract.local_gate]]`（kind ∈ {quality, trace, reach, anti_slop, version, build, smoke}、source ∈ {command, guard_registry, version_decl}、command、categories、result_channel ∈ {exit_code, file}、timeout_seconds）、`[host-contract.version_scheme]`（feature_tag、patch_line、prerelease_tag；可选 file/key/expect）、`[host-contract.build]`、`[host-contract.smoke]`（steps）、`[[host-contract.security_scan]]`、`[host-contract.ci]`、`[host-contract.tracker]`、`[host-contract.operations.feature/post_release/dev]`（steps、requires）。**占位符两级语义**：基础封闭集为 `{version} {major} {minor} {n} {ulid} {artifact} {prefix} {prefix_bin} {result}`（自 run 版本事实/构建产物解析，可用于 command/steps/TARGET 一级展开）；operations step 的 TARGET 位置额外接受恰为 `[host-contract.version_scheme]` 键名的三个标签引用 `{feature_tag}`/`{patch_line}`/`{prerelease_tag}`——它们以二级展开解析（先按基础封闭集渲染对应 version_scheme 模板，再代入 TARGET）；出现在 TARGET 之外或 version_scheme 缺对应键时即未知占位符。`host-contract.` 命名空间内的其它未知 table 名、未知 kind/channel、上述两级范围之外的未知占位符、缺 `[host-contract].version=1` 均 fail-closed；既有测试执行段（[unit]/[integration]/[e2e]/[nightly]/[adapter]/[layout]/[lint]）不属于 host-contract 命名空间、语义不变。kind=version 的 gate 语义：按 tag 模板推导目标 tag、断言远端不存在（存在即 fail-closed 冲突）、记录推导输入（{n} = 远端 `v{minor}.*` tag 最大 patch +1）；仅当 file/key/expect 显式声明时才比对版本文件（architecture §3.7 记录两宿主不声明的理由）。

> **Prism:** [PRISM-IF008-R2-03][blocker] §1e 占位符封闭集与物化合同自相矛盾：封闭集为 {version} {major} {minor} {n} {ulid} {artifact} {prefix} {prefix_bin} {result}，且明文『未知占位符 fail-closed』；但两份物化合同的 operations steps 均使用封闭集外的 token——tracks 的 .tracks/projects/project.toml L186-204 与 reference 资产 tracks-project.toml L148-166 都出现 tag:{feature_tag} / release:{feature_tag} / tag:{patch_line} / release:{patch_line} / tag:{prerelease_tag}。按本 schema 字面语义，物化合同自身的 operations 即为『未知占位符』，validate 应 fail-closed 拒绝——AC-FR0281-01（test_materialized_contract_valid）与 AC-FR0277 各旅程在字面实现下不可通过，Devon 将被迫自行决定 schema 语义（扩展封闭集或把 {feature_tag}/{patch_line}/{prerelease_tag} 解释为 version_scheme 键引用），违反可实现性判据（Devon 无需再选择 schema）。预期修订：在本节封闭集中显式纳入三个 version_scheme 标签引用（或等价地声明『operations TARGET 占位符 = 封闭集 ∪ version_scheme 键名，后者经 [host-contract.version_scheme] 二级展开』），使物化合同与 schema 自洽，且 fail-closed 语义仍可机械执行。
>> **Archer:** 已采纳等价方案：§1e 改为『占位符两级语义』——基础封闭集 {version} {major} {minor} {n} {ulid} {artifact} {prefix} {prefix_bin} {result}（run 版本事实/构建产物解析，command/steps/TARGET 一级展开）；operations step 的 TARGET 位置额外接受恰为 [host-contract.version_scheme] 键名的三个标签引用 {feature_tag}/{patch_line}/{prerelease_tag}，以二级展开解析（先按基础封闭集渲染对应模板再代入 TARGET）；出现在 TARGET 之外或 version_scheme 缺对应键即未知占位符 fail-closed。两份物化合同（tracks project.toml 与 reference tracks-project.toml）的注释已同步该两级语义，operations steps 与 schema 自洽；因注释 bytes 变化，两宿主 registry 第 8 项 config_digest 已重算并同步（tracks a01f94da… / reference 36307614…，程序复核 16/16 匹配）。Devon 无需再选择 schema 语义。

normalized result（`tracks-gate-result` v1）：`{"schema": "tracks-gate-result", "version": 1, "status": "passed"|"failed", "exit_code": int|null, "summary": {...}}`。`exit_code` 通道由 Runtime 合成（status=exit==0）；`file` 通道解析 `{result}` JSON，缺文件/非法 JSON/缺 schema/version/status 即 malformed。kernel/executor 不解释任何命令的语言语义（NFR-0147；扫描 token `pytest|junit|java|venv|wheel|pip` 词边界禁于允许区外）。

`[ci]` 绑定：`repo_env`（如 TRAC_GITHUB_REPO）、`workflow`、`required_checks` 列表、`conclusion="success"`。`[operations]` step 语法：`KIND:TARGET[:when=COND]`，KIND ∈ {merge, tag, artifact, release}，TARGET 支持占位符，COND ∈ {active_release_branch}；`requires` 声明旅程前置（缺失 precheck fail-closed）。

### 1f. Security policy（IF-SECURITY-001）

**modules**: `executor/security.py` 实现，effects/github（Prism 派发经 backend）、host_contract 消费。

`[[security_scan]]` 每项：id、tool、tool_version、install（可空）、command、result_channel、threshold、timeout_seconds。执行序列：install（声明时）→ command → normalized result；聚合 `aggregate_security_status`：全部 passed → passed；任一 failed → failed；缺失/畸形/未知 → unknown（阻断）。Prism 安全复审派发输入 `{scan_results, policy_digest, candidate_sha}`，verdict 落 `prism.verdict scope=security`；复审不通过视同 failed。policy_digest = sha256(canonical_json(全部 scan 声明))。

### 1g. Preview 与 Human 三择一（IF-RELEASE-002/003）

**modules**: `executor/release_gate.py` 实现，cli（release 面）、kernel/release、report 消费。

```python
def build_operation_plan(contract, journey: str, version_facts: dict) -> dict: ...
def compute_preview_digest(
    candidate_sha: str, artifact_digest: str, evidence_digests: dict,
    operation_plan_digest: str, contract_policy_digest: str,
) -> str: ...
def generate_preview(candidate_sha: str, digests: dict, risks: list, plan: dict) -> dict: ...
def judge_preview_stale(current_aggregate: dict, preview: dict) -> StaleReason | None: ...
def validate_release_decision(
    decision: str, preview: dict, gate_status: dict,
) -> tuple[bool, str | None]: ...
```

`preview_digest = "sha256:" + sha256(utf8(canonical_json({candidate_sha, artifact_digest, evidence_digests(sorted keys), operation_plan_digest, contract_policy_digest})))`。preview blob 为 content-address JSON；重生成追加新事件。`validate_release_decision` 对三个 action 统一校验 preview 未 stale；release 额外要求 M-VERIFY/M-SECURITY 全过；delay/return stale 时同样拒绝。

### 1h. Publish 幂等与 reconcile（IF-PUBLISH-001/002）

**modules**: `executor/publish.py` 实现，`effects/publish.py` 唯一效果边界，kernel/release 投影。

```python
def plan_operations(operation_plan: dict, preview_digest: str) -> list[dict]: ...
def operation_idempotency_key(preview_digest: str, kind: str, target: str) -> str: ...
def reconcile_operation(planned: dict, remote_state: dict) -> ReconcileVerdict: ...
def assert_agent_forbidden(actor: str) -> None: ...
```

`idempotency_key = "sha256:" + sha256(utf8(canonical_json({preview_digest, kind, target})))`。执行序：publish.planned（WAL）→ effects 执行 → publish.executed(done)。reconcile 判据：远端存在且与计划一致 → reconciled_skip；不存在 → pending（执行）；存在但内容/指向不同 → conflict（reconcile_conflict，远端为准，blocked）。Agent（非 Runtime actor）触达 effects/publish 面即 `publish.blocked reason=agent_forbidden`。

### 1i. Milestone trace 与生命周期（IF-MILESTONE-001/IF-ISSUE-002）

**modules**: `executor/milestone.py` 实现，checks/trace、effects/github、store 消费。

release trace 字段封闭集：`{candidate_sha, artifact_digest, evidence_digests, preview_digest, human_approval_event_seq, operation_digests, release_tag, trace_digest}`；`trace_digest = "sha256:"+sha256(canonical_json(其余字段))`。关闭 Issue 前必须再校验 `issue.mapped api_verified=true`；关闭 comment 必须含 candidate SHA / preview_digest / release_tag。`refs.cleaned` 后 `git for-each-ref refs/trac/tmp` 必须为空。RETRY_TAIL 只重发 `milestone.trace_closed→milestone.sealed` 段，不重发 publish。

### 1j. Envelope 协议（IF-ENVELOPE-001/002）

**modules**: `kernel/envelope.py` 定义；effects/opencode、effects/fake、executor 派发器/validator、templates 物化消费。

```python
ENVELOPE_PROTOCOL = "tracks-envelope"
ENVELOPE_VERSION = 2
ENVELOPE_FENCE_TAG = "tracks-envelope"

def parse_agent_output(text: str) -> dict: ...
def build_assignment_envelope(kind: str, task: dict) -> dict: ...
def validate_envelope(envelope: dict, expected_kind: str | None) -> dict: ...
def envelope_schema_digest(kind: str) -> str: ...
def check_envelope_parity(referenced_versions: dict[str, Any]) -> dict: ...
```

线形：输出文本中**恰好一个** fenced block，info string 为 `tracks-envelope`，内容为单个 JSON 对象 `{"envelope": {"kind": <str>, "version": 2}, "payload": <per-kind schema>}`。kind 封闭集由 kernel 维护（role:substate 形态，如 `devon:red`、`prism:review`、`shield:write`）。解析失败分类 `FormatErrorKind`（见 kernel stub）落 `format_error`；语义失败落 `semantic_attempt_failed`。六面 parity：prompt 模板、agent 定义、skill、fake backend、真实 backend、Runtime validator 各自携带机器可读 token `tracks-envelope:v2`（物化产物内嵌），派发前逐一比对；不一致 `dispatch.rejected reason=version_parity_mismatch`，不进入 Agent 执行。示例与 fixtures 必须经 `validate_envelope` 校验（CI 静态检查 tests/assets 内 envelope 样本）；手写游离 JSON 不构成有效样本。

### 1k. 失败证据链（IF-FAILURE-001）

**modules**: `executor/failure_review.py` 实现；effects/backend（outcome 携带 `evidence_ack`）、executor 注入点、kernel 投影消费。

存储：`.tracks/runtime/failures/{run}/{failure_id}.json`（append-only，content-address）；failure_id = `{round}-{source}-{seq}`。选择规则（单一实现）：给定 (role, round) 取该 round 内该 role 未 ACK 的最新 source 记录；无则向前一轮，最多回看 N=3 轮（常量冻结）。注入：assignment envelope 的 `evidence` 段引用 failure_id 列表。ACK：outcome payload `evidence_ack: [failure_id...]` → `failure.acked`。失效：被新证据替代且已 ACK → `failure.invalidated`；未 ACK 不得失效（普通失败 append 新记录，不覆盖）。`review_failure_chain(events)` 校验 injected 引用存在、stored 顺序、ACK 来源角色一致；违规落 `review.failed`。last_failure/diagnose_report 是 source 取值（保留既有语义）。

### 1l. Issue 权威映射（IF-ISSUE-001）

**modules**: `effects/github.py` 实现；executor（create_issues/关闭）、hotfix、report 消费。

```python
def select_issue_backend(repo: Path, version: str): ...   # 修改：真实模式缺凭据抛 GithubIssuesError(classification="auth"|"not_found")
def create_issue_verified(backend, title, body, labels) -> IssueMapping: ...
def persist_issue_mapping(repo, mapping) -> None: ...      # .tracks/runtime/issue-map.json，键 repo+baseline_digest
def readback_issue(repo_id, number) -> dict: ...           # GET /repos/{repo}/issues/{n}
def reject_fake_artifact(context, artifact) -> None: ...   # 真实模式 FAKE-N → fake_rejected
```

显式模拟模式封闭集：`TRAC_FAKE_SIMULATE`、`TRAC_AGENT_BACKEND=fake`、`--assignment-overlay`（assignment_simulation）。真实模式 = 三者皆未设置；此时后端选择不得返回 FakeIssueBackend，缺 GITHUB_TOKEN/TRAC_GITHUB_REPO 抛分类错误 → Runtime 落 `attention.required area=issue_creation`。`issue.mapped` 是 task/commit/report/关闭 effector 的唯一 issue 号来源；Tracks-Task trailers 的 issue# 由 Runtime 从映射回填校验。

### 1m. 三旅程与版本方案（IF-JOURNEY-001）

**modules**: `executor/release_gate.py`（plan）、`executor/publish.py`（执行）、kernel/release（路由）。

版本事实解析：`{major}/{minor}` 取 run 目标 version 数值；`{n}` = 远端 tag 中 `v{minor}.*` 的最大 patch +1；`{ulid}` = run_id。旅程判定：feature（trac start）/post-release/dev（trac hotfix --scenario）。dev precheck：远端无 `release/{minor}` 分支 → CLI 非零退出 + `precheck failed: no active release branch`（不建 fix 分支、不建 run 副作用——沿用 v0.6 REJECTED 审计形态）。post-release 的 `merge:release/{minor}:when=active_release_branch` 在分支缺失时静默跳过该步（计划 digest 按解析后实际步集计算）。

### 1n. Reference host（IF-REFERENCE-001）

**modules**: `executor/reference_host.py` 实现；wheel assets（`tracks/assets/reference_host/**`）、guard_registry、host_contract、github effects 消费。

```python
def create_reference_host(
    template_dir: Path, target_dir: Path, wheel: Path, remote_url: str | None,
) -> dict: ...
def verify_reference_equivalence(report: dict) -> tuple[bool, tuple[str, ...]]: ...
```

部署落点封闭集：`pyproject.toml`/`flake8.ini`/`host_calc.py`/`tests/**` → repo 根；`tracks-project.toml`（含并入的 `[host-contract.*]` 段）→ `.tracks/projects/project.toml`；`architecture.md` → `.tracks/projects/v0.1/architecture.md`；`ci.yml` → `.github/workflows/ci.yml`。真实路径要求与 demo 相同（fresh venv、non-editable wheel、源码树外、trac init）+ remote 绑定（remote_url 为 None 且非显式模拟 → needs_attention，不降级本地成功）。Python 细节只存在于资产与该隔离模块（NFR-0147 允许区）。

## 2. CLI 接口合同

### 2a. release 子命令（待实现 Devon foundation task；USAGE 与 TRAC_SUBCOMMANDS 同步后生效）

trac 入口下的子命令文法（散文引用形态，防 fabricated-command guard 误报；实现后即为真实命令）：

```text
release preview
release --action release
release --action delay --reason "<text>"
release --action return --to <stage> --reason "<text>"
```

- `release preview`：stdout 按 E-01 形态输出 `preview: candidate=<full SHA> preview_digest=sha256:… artifact=… ci_run={...} evidence_digests={...} operation_plan=… status=awaiting_release|stale stale_reason=…`；无活跃发布 run 时退出码 2 并提示先经 `trac run` 进入 M-RELEASE。
- `--action release`：成功输出 `decision: release (candidate=… preview_digest=…) status=approved` 退出 0；被拒输出 `error: release rejected — gate failed (m-verify/prism/security) or preview stale (candidate drift); preview_digest mismatch; run the release preview subcommand and status` 退出 1，状态保持 AWAITING_RELEASE。
- `--action delay [--reason]`：`decision: delay … status=delayed`；`--action return --to <stage> [--reason]`：`decision: return … status=returned`；preview stale 时两者同样退出 1 并提示重生成 preview。
- 决定事件 append-only（writer lock 内）；该面不复用 approve，不读 GitHub。

### 2b. `trac run` / `trac status` / `trac replay` / `trac report` 扩展

- `trac run`：进入/推进发布五阶段；输出行示例 `candidate.frozen (candidate_sha=… clean_tree=true)`、`local_gate.passed (kind=quality)`、`ci.run_observed (repo=… workflow=ci.yml run=… head=… status=passed)`、`prism.verdict (pass scope=verify_final)`、`security.assessed (policy_digest=… status=passed)`、`awaiting_release (preview_digest=…)`、`publish.planned/executed (operation=merge idempotency_key=… status=done|reconciled_skip)`、`milestone.sealed (trace_digest=…)`、`run.completed (terminal=released release_tag=…)`。任一门禁 blocked/needs_attention 时非零退出或 park（沿用既有 awaiting 机制）。`--resume` 为显式续跑别名（既有活跃 run reconcile 语义）。
- `trac status` 新增稳定字段：`stage=M-VERIFY|M-SECURITY|awaiting_release|M-PUBLISH|M-MILESTONE|released|retry_tail|delayed|returned`、`candidate=<full SHA>`、`full_reuse=full_f|full_rerun`、`ci=bound|mismatch|missing|stale|needs_attention`、`prism=pass|fail`、`security=passed|failed|unknown`、`preview_digest=…`、`decision=release|delay|return`、`publish=<op:done|reconciled_skip…>`、`terminal=released`、`needs_attention: <area> <reason> next=…`（E-03 形态）。
- `trac replay`/`trac report`：按 seq 展示 §1a 全 payload；report 新增 `Release pipeline`、`Issue map`、`Release trace` sections（issue_map/ci_binding/project 行按 E-03 形态），只渲染事件/blob 不重算结论。`trac check trace --version v0.8` 在既有 candidate-bound 闭环后追加 release 段（trace: approved_ac→…→release status=closed）。

### 2c. `trac validate` 扩展（待实现 foundation task）

追加校验：`trac validate --file .tracks/projects/project.toml` 对 `[host-contract.*]` 段的 schema 校验（§1e；既有 canonical 路径入口不变）；envelope 样本（tests/assets 内）经真实 validator；语言 token 扫描扩展 `pytest|junit|java|venv|wheel|pip`（允许区：adapters、assets、executor/{demo_host,reference_host}.py）；TRAC_SUBCOMMANDS 与 USAGE 同步（含 release）。

### 2d. 操作者交互闭合

| # | surface/context | 状态 | 动作/可用条件 | 可见结果与继续路径 |
|:--|:--|:--|:--|:--|
| 1 | M-IMPL 完成后宿主 repo | clean tree | `trac run` | 进入 M-VERIFY，status 显示 candidate 与门禁进度；dirty 时 needs_attention 指引 |
| 2 | M-VERIFY | 门禁进行中 | `trac run` / `trac status` | 全过进 M-SECURITY；任一失败 blocked 及 reason |
| 3 | awaiting_release | preview 就绪 | release preview 子命令读 preview；三择一决定 | release→M-PUBLISH；delay→delayed（重生成指引）；return→回上游 |
| 4 | M-PUBLISH/M-MILESTONE | 决定后 | `trac run` | 外部操作幂等执行；中断后 resume reconcile 不重复 |
| 5 | 旅程验收 | released | `trac report` / git tag / GitHub release | release.trace 同一性互验 |
| 6 | 任意审计时点 | 有 run id | `trac replay` / `trac report` | §1a 事件逐环节可回放 |

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `.tracks/projects/project.toml` 的 `[host-contract.*]` 段 | TOML；Archer（M-DESIGN 物化）；git tracked | host_contract loader/Runtime/validate；canonical（tracks 与 reference 各自一份，均并入各自 project.toml） |
| 2 | `.tracks/runtime/blobs/release/{run}/{seq}-preview.json` | canonical JSON；Runtime | release 决定/trace/replay；append-only content-address |
| 3 | `.tracks/runtime/failures/{run}/{failure_id}.json` | canonical JSON；Runtime | failure_review/replay；append-only |
| 4 | `.tracks/runtime/issue-map.json` | JSON（item→repo/number/url/baseline_digest/api_verified）；Runtime | task/commit/report/关闭 effector；权威映射 |
| 5 | `.tracks/runtime/blobs/release/{run}/{seq}-trace.json` | release trace §1i；Runtime | report 导出/互验；sealed 后只读 |
| 6 | `refs/trac/tmp/{run_id}/*` | git refs；Runtime（发布执行标记） | M-MILESTONE 清理；refs.cleaned 验证为空 |
| 7 | `tracks/assets/reference_host/**` | 数据/配置模板；Archer；wheel package data | reference_host 物化器逐字节部署；不入 kernel 语义 |
| 8 | `tests/_support/`（stand-in GitHub 服务、bare 远程工厂） | Shield 测试基础设施；git tracked | L2 旅程/门禁测试；显式 env 指向 |
| 9 | `.venv`、dist/、隔离 smoke prefix | Runtime 执行产物 | build/smoke gate；临时可清理 |

## 4. 可观察出口（测试断言基础）

### 4a. Event 出口

| # | outlet | 关键字段 | AC |
|:--|:--|:--|:--|
| 1 | `candidate.frozen/stale` | full SHA、clean_tree、drift reason | FR-0267 全部、NFR-0143 |
| 2 | `evidence.reused(kind=full_f)` / `full.executed` | identity_basis、rerun reason | FR-0268 |
| 3 | `local_gate.passed/failed` | kind、contract_digest、normalized_result | FR-0269、FR-0281 |
| 4 | `ci.run_observed` | 四元组绑定、api_verified | FR-0270 |
| 5 | `prism.verdict(scope=verify_final/security)` | candidate_sha、evidence_digests | FR-0271、FR-0272 |
| 6 | `security.assessed` | policy_digest、scans | FR-0272 |
| 7 | `release.previewed/decided/rejected` | preview_digest、action、绑定 | FR-0273、FR-0274 |
| 8 | `publish.planned/executed/blocked/failed`、`reconcile_conflict` | idempotency_key、remote_check | FR-0275、NFR-0144 |
| 9 | `milestone.*`、`issue.closed/project.closed/milestone.closed`、`refs.cleaned` | trace_digest、comment_ref | FR-0276、FR-0284 |
| 10 | `dispatch.parity/rejected`、`format_error`、`semantic_attempt_failed` | surfaces、kind | FR-0278、FR-0279、NFR-0145 |
| 11 | `failure.*`、`review.failed` | failure_id、round、source | FR-0280、NFR-0146 |
| 12 | `host_contract.materialized/invalid/failed` | 机器证据 payload | FR-0281 |
| 13 | `issue.mapped`、`fake_rejected`、`attention.required` | api_verified、mode、next | FR-0282/0283、NFR-0148 |

### 4b. CLI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | release 子命令（preview/三择一） | E-01 形态输出、退出码、拒绝不转移状态 |
| 2 | `trac status` | §2b 稳定字段、needs_attention 指引 |
| 3 | `trac replay/report` | §1a 全 payload、issue_map/ci_binding/release trace 段 |
| 4 | `trac check trace --version v0.8` | release 段 closed/缺失环节报错 |
| 5 | `trac validate --file .tracks/projects/project.toml` | host-contract 段 schema fail-closed |

### 4c. 文件/Git/远端出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | project.toml [host-contract.*] 段 / issue-map.json / blobs | schema、digest、append-only |
| 2 | 远端 git（ls-remote、tag 列表、分支） | 与事件互验；reconcile 无重复 |
| 3 | GitHub API（issues/actions/releases/milestones） | 回读状态与事件一致 |
| 4 | `git for-each-ref refs/trac/tmp` | 清理后为空 |
| 5 | reference repo 部署落点 | §1n 封闭集逐字节 |

## 5. IF Registry

### IF-MTEST-001 M-TEST 测试收集合同（继承 IF-006）

### IF-MTEST-002 M-TEST 测试执行合同（继承 IF-006）

### IF-SHIELD-001 Shield 测试编写合同（继承 IF-006）

### IF-TRACE-001 trace 需求追踪合同（继承 IF-006）

### IF-TRACE-002 trace 闭合检查合同（继承 IF-006）

### IF-TRACE-003 release trace 同一性导出合同

- **合同**：`trac check trace --version v0.8` 与 report 导出的 release trace 按 §1i 字段封闭集连接 approved AC→…→release；candidate/artifact/evidence/preview/human approval/operation 各 digest 同一性证明；任一漂移 trace=inconsistent。
- **modules**：checks/trace.py, executor/milestone.py, report.py。
- **关联**：NFR-0143。

### IF-REACH-001 reach 模块可达性合同（继承 IF-006）

### IF-REACH-002 reach 孤岛检查合同（继承 IF-006）

### IF-VALIDATE-001 trac validate 文档校验合同（继承 IF-006；v0.8 追加 host-contract/envelope 样本/语言 token 扩展）

### IF-IMPL-001 M-IMPL kernel 状态机合同（继承 IF-006；v0.8 边界改道见 IF-VERIFY-001）

### IF-IMPL-002 M-IMPL executor handler 合同（继承 IF-006）

### IF-IMPL-003 task graph 解析与校验合同（继承 IF-006）

### IF-IMPL-004 RGR git 操作合同（继承 IF-006）

### IF-IMPL-005 质量门禁分层执行合同（继承 IF-006）

### IF-IMPL-006 三 worktree 方案合同（继承 IF-006）

### IF-IMPL-007 tasks.json/tasks.md 真相源合同（继承 IF-006）

### IF-DEVON-001 Devon agent 与 manifest 越界审计合同（继承 IF-006）

### IF-LIVE-001 真实外部旅程与 evidence 合同（继承 IF-006）

### IF-RELEASE-001 release-evidence 合同（继承 IF-006；v0.8 由 M-VERIFY 链取代为发布权威，本合同保留为既有 live evidence 校验）

### IF-DOCGAP-001 文档评论优先裁定合同（继承 IF-006）

### IF-QUARANTINE-001 outcome 隔离恢复合同（继承 IF-006）

### IF-HOTFIX-001..010 hotfix 合同组（继承 IF-006；v0.8 dev precheck 扩展见 IF-JOURNEY-001）

### IF-SELECT-001/002（继承 IF-006）

### IF-EVIDENCE-001 evidence identity/reuse/stale 合同（继承 IF-006；v0.8 的 FULL_F 复用消费其 stale 语义）

### IF-LEDGER-001、IF-FULLCHAIN-001、IF-RUNCONTRACT-001、IF-NIGHTLY-001（继承 IF-006）

### IF-PHASE-001/002/003、IF-GUARD-001/002、IF-AUTH-001/002、IF-MUTATION-001/002、IF-CLOSURE-001、IF-DEMO-001、IF-FAILCLOSED-001（继承 IF-007）

### IF-ADAPTER-003 kernel/executor/cli 语言中立不变量（继承 IF-007；v0.8 扫描 token 扩展 pytest|junit|java|venv|wheel|pip）

### IF-VERIFY-001 candidate 冻结与绑定不变式合同

- **合同**：§1a#1/#2、§1d——clean tree 冻结完整 HEAD SHA、幂等不重冻、全链事件绑定校验、dirty→needs_attention；M-IMPL boundary 对 RELEASE 能力版本改道 M-VERIFY（未达门槛保持 boundary）。
- **modules**：kernel/release.py, executor/m_verify.py, kernel/machine.py（边界守卫）。
- **关联**：FR-0267、NFR-0143、NFR-0149-03。

### IF-VERIFY-002 FULL_F 复用判定合同

- **合同**：§1d——四元组一致∧无 STALE∧未漂移才 reuse（evidence.reused kind=full_f + identity_basis）；否则 LOCAL_GATES 重跑 full.executed；stale 证据不作通过依据。
- **modules**：executor/m_verify.py, executor/host_contract.py, kernel/release.py。
- **关联**：FR-0268。

### IF-VERIFY-003 宿主合同本地门禁合同

- **合同**：§1e——七类 gate 按合同执行（quality 引用 registry canonical 命令）、normalized result 绑定 candidate、未知/畸形/超时 fail-closed、合同变更重跑对应验证。
- **modules**：executor/host_contract.py, guard_registry.py, kernel/release.py。
- **关联**：FR-0269。

### IF-VERIFY-004 required CI API 回读合同

- **合同**：§1a#4——repo+workflow+run_id+head SHA==candidate 且 required checks 全绿才 bound；不一致/缺失/stale fail-closed；缺凭据 needs_attention；已绑定 replay 不重复 API 调用。
- **modules**：effects/github.py, executor/m_verify.py, kernel/release.py。
- **关联**：FR-0270。

### IF-VERIFY-005 Prism 同 candidate 终审合同

- **合同**：§1d——本地+CI 门禁后派发同 candidate 一致性复审（scope=verify_final），输入证据集合同 SHA；failed/revise 阻断；重审绑定同 SHA。
- **modules**：executor/m_verify.py, kernel/release.py, effects/backend。
- **关联**：FR-0271。

### IF-SECURITY-001 合同化安全评估合同

- **合同**：§1f——pinned 扫描 + Prism scope=security 复审 + 聚合；unknown/missing/malformed fail-closed；修复后同 candidate 重跑。
- **modules**：executor/security.py, effects/github.py, kernel/release.py。
- **关联**：FR-0272。

### IF-RELEASE-002 preview 聚合与 stale 合同

- **合同**：§1g——preview_digest 公式、blob content-address、AWAITING_RELEASE 进入、stale 三因、重生成追加不覆盖。
- **modules**：executor/release_gate.py, kernel/release.py, cli。
- **关联**：FR-0273。

### IF-RELEASE-003 独立 Human 三择一门禁合同

- **合同**：§2a/§1g——release/delay/return 决定 append-only 绑定 preview/candidate；统一 stale 校验；失败门禁不可绕过；交付面独占（approve/GitHub UI 不产生 release.decided）。
- **modules**：cli/main.py, executor/release_gate.py, kernel/release.py。
- **关联**：FR-0274。

### IF-PUBLISH-001 外部操作 write-ahead 与幂等合同

- **合同**：§1h——计划/执行/幂等键、Agent 禁入效果边界、未声明/畸形操作拒绝。
- **modules**：executor/publish.py, effects/publish.py, kernel/release.py。
- **关联**：FR-0275-01/03/04。

### IF-PUBLISH-002 远端 reconcile 合同

- **合同**：§1h——远端为准的 skip/pending/conflict 判定、reconcile_conflict 审计、精确一次语义。
- **modules**：executor/publish.py, effects/publish.py。
- **关联**：FR-0275-02/04、NFR-0144、NFR-0149-02。

### IF-MILESTONE-001 归档与生命周期合同

- **合同**：§1i——trace 闭环、Issue/Project/milestone 关闭、只读封存、refs 清理、RETRY_TAIL 只重试收尾。
- **modules**：executor/milestone.py, effects/github.py, checks/trace.py。
- **关联**：FR-0276、FR-0284-01、NFR-0149-02。

### IF-JOURNEY-001 三旅程与版本方案合同

- **合同**：§1m——feature/post-release/dev 操作计划、patch/prerelease 版本推导、dev precheck fail-closed、三旅程同一 candidate 与幂等。
- **modules**：executor/release_gate.py, executor/publish.py, kernel/release.py。
- **关联**：FR-0277、NFR-0149-01。

### IF-ENVELOPE-001 统一 envelope 与单一解析合同

- **合同**：§1j——线形、kind/version 封闭集、FormatErrorKind、format_error 不计 attempt、示例经 validator。
- **modules**：kernel/envelope.py, effects/opencode.py, effects/fake.py。
- **关联**：FR-0278、NFR-0145-02。

### IF-ENVELOPE-002 parity 与 format/semantic 分离合同

- **合同**：§1j——六面版本 token 比对、dispatch.rejected、format_error vs semantic_attempt_failed 分离、v0.7 malformed 语料回归。
- **modules**：kernel/envelope.py, executor 派发器, templates/agents/skills 物化。
- **关联**：FR-0279、NFR-0145。

### IF-FAILURE-001 失败证据链合同

- **合同**：§1k——七环节 append-only、共享选择规则、ACK 前不覆盖/不失效、review 校验 fail-closed。
- **modules**：executor/failure_review.py, effects/backend.py。
- **关联**：FR-0280、NFR-0146。

### IF-HOSTCONTRACT-001 宿主合同与 normalized result 合同

- **合同**：§1e——schema 封闭集、占位符、两通道 normalized result、语言中立扫描扩展。
- **modules**：executor/host_contract.py, executor/validate.py。
- **关联**：FR-0269、FR-0281-01/02、NFR-0147。

### IF-HOSTCONTRACT-002 物化与容错回流合同

- **合同**：Archer M-DESIGN 物化 + host_contract.materialized；失败机器证据回流 M-DESIGN 修订；Runtime 不猜命令。
- **modules**：executor/host_contract.py, cli(init), kernel/release.py。
- **关联**：FR-0281-01/03。

### IF-REFERENCE-001 reference host 真实物化验收合同

- **合同**：§1n——部署落点封闭集、fresh venv/wheel/init、真实远程绑定、needs_attention 不降级、Python 细节隔离。
- **modules**：executor/reference_host.py, assets/reference_host/**。
- **关联**：FR-0282、NFR-0149-01。

### IF-ISSUE-001 Issue 真实创建与权威映射合同

- **合同**：§1l——真实模式缺凭据 needs_attention、issue.mapped + API 回读、repo+baseline 去重、显式模拟模式封闭集、fake_rejected。
- **modules**：effects/github.py, executor/milestone.py。
- **关联**：FR-0283、NFR-0148。

### IF-ISSUE-002 关闭时机与审计合同

- **合同**：§1i——关闭前映射再校验、comment 含 trace 三元组、project/milestone 关闭、FAKE 反例拒绝。
- **modules**：executor/milestone.py, effects/github.py。
- **关联**：FR-0284、NFR-0148-02。

### IF-PIPELINE-001 单次权威测试流水线回归合同

- **合同**：WRITE→COLLECT→red.validated→prism.verdict(隔离 kill) 链不变；Shield 自检非权威；v0.8 无新流水线定义；缺失环节 pipeline incomplete。
- **modules**：kernel/m_test.py, executor（既有）。
- **关联**：FR-0285。
