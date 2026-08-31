---
spec_id: SPEC-008
created: 2026-08-31
status: draft
sha:
---

# 可信发布闭环与流程合同收敛（v0.8）— Test Plan

- **Related acceptance**: `.tracks/projects/v0.8/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.8/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

本计划只断言 interfaces.md §4 的外部出口：

- 既有 `trac run/status/replay/report/check/validate/start/hotfix/approve` 的 stdout/stderr、exit code 与新增 release 子命令（待实现 foundation task）的 E-01 形态输出。
- `.tracks/runtime/tracks.db` append-only events 及其引用的 content-address blobs（preview/trace/failures）、`issue-map.json`、project.toml 的 `[host-contract.*]` 段。
- git 本地与远端事实（ls-remote、tag/branch 列表、`refs/trac/tmp` 残留）与 stand-in/真实 GitHub API 回读状态（issues/actions runs/milestones/releases）。
- wheel 安装创建的 reference host repo 的部署落点、hooks/CI/adapter 合同与同构事件链。

测试不因内部类、私有 state、函数调用次数或 mock 返回值通过；需要的内部判定必须先落 interfaces.md 的 event/CLI/file outlet。

### 1.2. Non-observable Objects (tests do not directly depend on)

- kernel 的发布阶段 reducer 与 State 内部字段；经 events/status 观察。
- host_contract/security/publish 的私有 helper；经 normalized result、事件、退出码观察。
- envelope 解析器内部错误分类实现；经 format_error 事件 kind 观察。
- stand-in GitHub 服务的内部存储；仅经其 REST 响应观察。

### 1.3. Cheating Patterns (CI enforced interception)

| # | Cheating Pattern | v0.8 典型症状 |
|:--|:--|:--|
| 1 | assertion 迁就实现 | 把「CI head SHA==candidate」放宽为「存在 ci 事件」 |
| 2 | skip/xfail 逃避 | L3 旅程缺凭据静默 skip 而非 needs_attention 计数 |
| 3 | silent fake fallback | 真实模式缺 token 时用 FakeIssueBackend 让旅程变绿 |
| 4 | Agent 自述冒充执行 | 自述「已 merge/tag」，无 publish.executed 与远端互验 |
| 5 | 幂等键造假 | 用不同 target 串改幂等键绕过 reconciled_skip 断言 |
| 6 | preview 绑定缩水 | preview_digest 计算遗漏 operation_plan/contract digest 分量 |
| 7 | envelope 宽松解析 | 保留「最后一个 JSON」回退使畸形输出通过 |
| 8 | 富证据覆盖 | 普通失败后未 ACK 的 last_failure 记录消失 |
| 9 | trivial assertion | `assert True`、只断言事件存在不看 payload 绑定 |
| 10 | format_error 计为 attempt | 畸形 envelope 烧掉语义预算或推进阶段 |

### 1.4. Safeguards (CI checks + PR process)

1. **AC trace**：每个 Shield test 的 `def` 紧邻上方使用 `# AC-FRXXXX-YY@v0.8 TRACKS-TRACE ...`；多 AC 各一行。每 AC ≥1 collected node、每 node ≥1 AC；变绿条件引用所依赖 IF- 标识（FR-0140）。
2. **Assertion taboos**：禁止 sole trivial assert、bare swallow、无 issue 链接的 skip/xfail（静态扫描阻断 merge）。
3. **发布链绑定断言**：凡断言发布链事件的测试必须同时断言 payload 的 candidate_sha/preview_digest/idempotency_key 绑定，不得只断言事件类型存在。
4. **阻断出口可观察**：凡断言 blocked/needs_attention 的测试必须同时断言 `repair_route` 非空且 exit_class/defect_class 落入封闭集（或 `next=` 指引存在）——「无下一步的死端」按反模式处理；就地修复断言必须含「无自动 stage.rolled_back」（architecture §1.0.14 / FR-0286）。
5. **模拟模式显式性**：stand-in API/bare 远程/FAKE 通道只经显式 env（TRAC_GITHUB_API_BASE、TRAC_FAKE_SIMULATE、TRAC_AGENT_BACKEND、--assignment-overlay）启用；测试结束显式清理，防泄漏到真实模式用例。
6. **L3 不可静默 skip**：milestone/release-tag 通道缺凭据必须 fail（`LIVE_SKIPPED` 语义只适用于 weekly/manual），与 NFR-0149-01 的 needs_attention 计数一致。
7. **PR classification**：New AC / Spec change / flake-environment issue 三选一；「实现与 spec 不符所以改测试」拒绝。
8. **Testability fallback**：缺 public outlet 时回修 interfaces/acceptance，不 mock internals。

### 1.5. Test Division of Labor

- **Unit**：Devon 对每个已实现 FR/NFR 的普遍 RGR 义务，由 coverage ≥95% 保证；§8 不规划 unit。
- **Integration**：Shield 验证 interfaces.md 跨模块合同及边界/错误矩阵。
- **E2E**：Shield 覆盖发布旅程 happy path（tracks feature、post-release、dev、reference host、六旅程矩阵），共 5 条 deterministic + 1 条 live 通道旅程组。
- **Stand-in 基础设施**：Shield 建于 `tests/_support/`（GitHub REST 子集 stand-in、bare 远程工厂、kill-9 断点 harness）。
- **Machine evidence**：Runtime 独家产生；Agent 不能自报结论。

---

## 2. Test Environment

### 2.1. Directory Layout

```text
tests/
├── unit/                                  # Devon 自辖，不在 §8 处方
├── integration/
│   ├── test_verify_candidate.py
│   ├── test_verify_fullf_reuse.py
│   ├── test_verify_local_gates.py
│   ├── test_verify_ci_readback.py
│   ├── test_verify_prism_final.py
│   ├── test_security_assessment.py
│   ├── test_release_preview.py
│   ├── test_release_gate_cli.py
│   ├── test_publish_idempotency.py
│   ├── test_publish_reconcile.py
│   ├── test_milestone_lifecycle.py
│   ├── test_journey_versioning.py
│   ├── test_journey_recovery.py
│   ├── test_envelope_contract.py
│   ├── test_envelope_parity.py
│   ├── test_failure_evidence_chain.py
│   ├── test_host_contract.py
│   ├── test_reference_host.py
│   ├── test_issue_mapping.py
│   ├── test_issue_close.py
│   ├── test_pipeline_regression.py
│   ├── test_release_trace.py
│   ├── test_failclosed_release.py
│   ├── test_inplace_repair.py
│   ├── test_known_issue.py
│   ├── test_escape_gate.py
│   ├── test_kernel_language_neutrality.py     # 既有文件追加 venv/wheel token 用例
│   └── test_github_effects.py                 # 既有文件追加 needs_attention 用例
├── e2e/
│   ├── test_release_journey.py
│   ├── test_hotfix_release_journeys.py
│   ├── test_reference_host_journey.py
│   ├── test_release_journeys_matrix.py
│   └── test_full_journey_v05.py               # 既有 capability 隔离回归（追加断言）
├── e2e_live/
│   └── test_release_journeys_live.py          # L3 双宿主六旅程（milestone/weekly）
├── counterexamples/v0.8/                   # 单 AC/IF non-test patches
├── assets/v0.8/                            # malformed envelope 回归语料、FAKE-90..103 反例fixtures
├── _support/                               # stand-in GitHub 服务、bare 远程工厂、kill-9 harness
└── ground_truth/                           # 既有资产不改；v0.8 §3 不适用
```

本节列出的 dedicated v0.8 integration/e2e 与 counterexample 均为 Shield-owned 测试资产。reference host repo 由 Runtime 从 wheel 内 `tracks/assets/reference_host/` 动态创建到隔离目录，不作为提交 fixture。

### 2.2. Naming Conventions

- File：`test_<contract_area>.py`；function：`test_<observable_contract>`。
- AC marker：`# AC-FR0274-02@v0.8 TRACKS-TRACE release rejected fail-closed`。
- Counterexample：`ce_<specific_failure>.patch`。

### 2.3. Execution

- 默认离线且确定性（stand-in 服务监听 loopback，不算外网）；顺序 unit → integration → e2e。
- integration/e2e 使用注册 marks 与路径隔离；live/performance 默认排除。
- git/发布操作使用每测试 fresh temp repo + bare 远程；kill-9 演练以 WAL 断点（事件 append 后、效果执行前）驱动，不用 sleep race。
- stand-in env 在 fixture setup 设置、teardown 显式撤销，防止跨用例泄漏。

### 2.3.1. Test Execution Contract (`.tracks/projects/project.toml`)

- **Unit**：framework `pytest`；paths `tests/unit/`；collect/run/run_selected/cwd 继承现合同。
- **Integration**：framework `pytest`；paths `tests/integration/`；collect `.venv/bin/python -m pytest --collect-only tests/integration/`；run `.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；cwd `.`。
- **E2e**：framework `pytest`；paths `tests/e2e/`；collect `.venv/bin/python -m pytest --collect-only tests/e2e/`；run `.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；cwd `.`。
- **Adapter**：`id="reference-pytest"`, `protocol="tracks-test-result"`, `version=1`（不变）。

### 2.4. Test Data

- **Source**：合成与 git tracked 小 fixtures；v0.7 malformed response 故障语料固化于 `tests/assets/v0.8/malformed/`（Shield 整理，逐条对应 format_error 断言）；v0.7 `FAKE-90..FAKE-103`/`project=fake-project` 事件样本作为反例输入。
- **Reproducible**：candidate/preview/idempotency digest 全部由公开 canonical JSON 公式局部重算（标准库 sha256）；固定 timestamps 不参与 identity。
- **Scenario data**：dirty tree、drift commit、stale preview、同键异果、缺凭据、Agent 越权、畸形合同各自独立最小 fixture，不共享 broad fixture。
- **Sensitive data**：L3 secrets 只在 CI env；测试不落盘凭据。
- **Cleanup**：temp repo/bare 远程/stand-in 服务可清理；events/blobs 保留供 replay 断言。

### 2.5. Installation & Isolation

继承首版安装合同：E2E 从 candidate wheel 安装到 fresh venv，源码树外 cwd 调用，禁止 editable install。v0.8 追加：

1. reference host E2E 从 wheel 资产物化（fresh venv + non-editable + 源码树外 + init），断言部署落点封闭集逐字节；
2. 发布旅程 E2E 的 merge/tag/release 经 bare 远程或 stand-in API，测试结束断言远端与事件互验一致；
3. smoke 断言隔离 prefix 安装后公开入口可发现（trac --help / hostcalc 入口）。

---

## 3. Ground Truth Method

v0.8 判定不适用独立 ground-truth 脚本。需求的正确性对象是事件/身份/路由/幂等规则，而非数值算法：approved AC 集、事件 payload schema、git/bare 远端事实、GitHub API 回读、fixture 本身分别是独立事实。预期中的 digest 类计算（preview_digest、idempotency_key、trace_digest）以标准库 sha256 对公开 canonical JSON 在测试体内做一次局部重算（与实现同输入不同代码路径的公式复算），不创建 `tests/ground_truth/` 新文件、不 import 被测 helper 求期望。既有 ground_truth 资产继承且不修改。

---

## 4. Test Scope

本计划覆盖 SPEC-008 与 ACC-008 全部 81 条 required AC（FR-0267～FR-0287 共 66 条 + NFR-0143～NFR-0149 共 15 条）。Validity/Testability/Decision 均为绿；无开放产品决定。

| Valid | Testable | Decided |
|:--|:--|:--|
| ✅ | ✅ | ✅ |

范围外：自举 Runtime 安全边界（seed 问题 1）、测试流水线重发明（问题 5 的重发明部分——FR-0285 仅回归核验）、nightly result fetch、第二语言 adapter、RFC discussion 新状态、通用多 registry/多部署平台。

---

## 5. Acceptance Criteria

1. Unit coverage ≥95%，source omit 为空；Devon unit + Shield integration/e2e 合并计量。
2. interfaces.md 每个 `modules` 2+ 的新合同至少一条 integration happy+关键 error/edge。
3. deterministic e2e happy path（4 条文件级旅程 + 六旅程矩阵）全绿；错误矩阵只在 integration。
4. §8 的 81 条 AC 全部有 integration/e2e layer 与已注册 IF。
5. 发布链事件的断言全部含 candidate_sha/preview_digest/idempotency_key 绑定（§1.4-3）。
6. 语言中立扫描（pytest|junit|java|venv|wheel|pip，词边界，允许区外）零命中且注入副本必红。
7. L1/L2 默认 CI 全绿；L3 旅程在 milestone/release-tag 通道凭据齐全时全绿、缺失时 fail（never skip）。
8. 双宿主六旅程的 release.trace 同一性在 report 导出中互验一致。

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描结果：v0.8 存在宿主技术栈之外的真实外部依赖——GitHub REST API（issues/actions runs/milestones/releases/projects）、真实 git 远端（merge/tag push）、advisory 网络（pip-audit）、live agent 通道（既有）。三层机制如下。

### 6.1. Constraints

| # | Constraint | Consequence |
|:--|:--|:--|
| C1 | routine CI 无生产凭据/不动真实远端 | L2 stand-in 承载协议合同 |
| C2 | 真实远端操作不可逆 | L3 只在专用测试 repo（journey/reference）执行 |
| C3 | 不 mock tracks internals | 只替换真实外部 service；门禁/幂等/绑定逻辑真跑 |

### 6.2. Controllable vs Mock

- 可替换（外部依赖）：GitHub API（stand-in REST 子集，经 TRAC_GITHUB_API_BASE 显式指向）、git 远端（本地 bare repo）、advisory 源（stand-in/缓存）、wall clock（不参与 identity）。
- 不可 mock：candidate 冻结/绑定校验、preview digest、幂等 reconcile、envelope 解析、失败证据链——这些是被测对象。

### 6.3. Three Layers

| Layer | Name | v0.8 Coverage | Default |
|:--|:--|:--|:--|
| L1 | deterministic local | 纯逻辑 AC：envelope 解析/parity、digest 复算、状态机投影、failure 链、选择规则 | ✅ CI default |
| L2 | contract sim | stand-in GitHub + bare 远端：全部门禁/幂等/reconcile/Issue 映射/旅程矩阵（含 reference host deterministic 旅程） | ✅ CI default |
| L3 | real env | 双宿主六旅程（真实远程+真实凭据+live agent）、pip-audit 真实 advisory | ❌ milestone(tag)/weekly |

L3 缺凭据在 weekly/manual 输出 `LIVE_SKIPPED: missing <NAME>` 且不 fail；在 release/tag milestone **必须 fail**（release-evidence job 的 release-journeys 步 fail-closed）。NFR-0149-01 的「凭据缺失计 needs_attention 不计通过」在 L3 用例中体现为：真实模式无凭据的旅程断言 status=needs_attention 且无本地成功 release.trace——该断言本身在 L2 可确定性验证。

### 6.4. Test Infrastructure Responsibility

| # | Component | Responsibility | Boundary |
|:--|:--|:--|:--|
| 1 | stand-in GitHub 服务 | 实现 issues/actions-runs/milestones/releases/projects REST 子集（同协议形态） | 不实现 tracks 业务；不伪造 api_verified |
| 2 | bare 远程工厂 | 本地 git 裸仓，接受真实 push/ls-remote | 不决定幂等语义 |
| 3 | kill-9 harness | 在 WAL 断点中断进程并重启 replay | 不注入额外副作用 |
| 4 | reference 物化器 | 从 wheel 资产部署 + 绑定远程 | 不 import tracks 测试 helper |
| 5 | advisory stand-in | pip-audit 的本地缓存/离线源（L2） | L3 用真实网络 |

### 6.5. Assertion Basis — Closure with interfaces.md

断言只落 interfaces.md §4：§1a 全部新事件（含 payload 绑定字段）、§2 CLI（release 子命令 E-01 形态、status/replay/report/check trace v0.8）、§3 文件（project.toml 的 [host-contract.*] 段、blobs、issue-map.json、refs/trac/tmp）与远端/git 出口。无出口时修 interfaces，不 snoop internals。

`tests/assets/v0.8/malformed/` 的 envelope 样本必须全部经真实 `validate_envelope` 拒绝（样本本身不手写「预期失败原因」，由 validator 输出分类）；fixtures 若含合法样本则必须通过同一 validator（FR-0278-01 的「示例由 schema 生成或经 validator 校验」断言）。

---

## 7. CI Gate

- **Stable required checks**（继承，名称不变）：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`；merge 全 required。
- **Milestone**：`release-evidence`（tag/release，needs routine 全部）扩展 release-journeys 步（**待实现 Devon foundation task**）：凭据探针（TRAC_LIVE_API_KEY、GITHUB_TOKEN、TRAC_JOURNEY_REMOTE_TOKEN/REPO、TRAC_REFERENCE_HOST_TOKEN/REPO）缺一 fail；运行双宿主六旅程 + `trac report` 同一性校验 + 既有 live journey。
- **lint/coverage**：registry categories 1–6 与 coverage ≥95% 不变；新增源码纳入 scope。
- **test**：unit+integration+e2e deterministic（L1/L2）；live path 不在此 job。
- **trace**：AC marker + collected node 闭合；`trac check trace --version v0.8` 在对应 version gate 运行（release 段在 released 前为 pending 状态可判）。
- **anti-pattern**：AC trace 静态扫描、assertion taboos、模拟模式显式性扫描（stand-in env 必须伴随显式设置断言）、envelope 样本 validator 校验、语言 token 扩展扫描（含注入副本必红）。
- **failure semantics**：任何 required check、门禁、parity、reconcile 冲突、旅程 leak 都不产生 stage exit；Agent 自述无效。

---

## 8. AC Coverage

锚点归属（task graph schema v2）：每行 `test` 列的 integration 项是可由 task 声明转绿的验收锚点；e2e 项是 ISLAND_GATE_2/FULL 兜底的终态锚点，不写入 task 验收声明。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0267-01 | integration + e2e | tests/integration/test_verify_candidate.py::test_clean_tree_freezes_candidate + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-VERIFY-001 |
| AC-FR0267-02 | integration | tests/integration/test_verify_candidate.py::test_dirty_tree_needs_attention | IF-VERIFY-001 |
| AC-FR0267-03 | integration | tests/integration/test_verify_candidate.py::test_drift_marks_stale_no_refreeze | IF-VERIFY-001 |
| AC-FR0268-01 | integration + e2e | tests/integration/test_verify_fullf_reuse.py::test_undrifted_identity_reuses_full_f + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-VERIFY-002 |
| AC-FR0268-02 | integration | tests/integration/test_verify_fullf_reuse.py::test_drift_or_stale_reruns_full | IF-VERIFY-002 |
| AC-FR0268-03 | integration | tests/integration/test_verify_fullf_reuse.py::test_stale_evidence_not_reused | IF-VERIFY-002, IF-EVIDENCE-001 |
| AC-FR0269-01 | integration | tests/integration/test_verify_local_gates.py::test_contract_gates_run_and_pass | IF-VERIFY-003, IF-HOSTCONTRACT-001 |
| AC-FR0269-02 | integration | tests/integration/test_verify_local_gates.py::test_missing_or_malformed_gate_fails_closed | IF-VERIFY-003, IF-HOSTCONTRACT-001 |
| AC-FR0270-01 | integration + e2e | tests/integration/test_verify_ci_readback.py::test_api_readback_binds_candidate + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-VERIFY-004 |
| AC-FR0270-02 | integration | tests/integration/test_verify_ci_readback.py::test_mismatch_missing_stale_blocks | IF-VERIFY-004 |
| AC-FR0270-03 | integration | tests/integration/test_verify_ci_readback.py::test_missing_credentials_needs_attention | IF-VERIFY-004 |
| AC-FR0271-01 | integration | tests/integration/test_verify_prism_final.py::test_same_candidate_consistency_pass | IF-VERIFY-005 |
| AC-FR0271-02 | integration | tests/integration/test_verify_prism_final.py::test_prism_fail_blocks_m_impl_gap | IF-VERIFY-005 |
| AC-FR0272-01 | integration | tests/integration/test_security_assessment.py::test_contract_scans_pass | IF-SECURITY-001 |
| AC-FR0272-02 | integration | tests/integration/test_security_assessment.py::test_unknown_or_malformed_blocks | IF-SECURITY-001 |
| AC-FR0273-01 | integration + e2e | tests/integration/test_release_preview.py::test_preview_digest_binds_all + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-RELEASE-002 |
| AC-FR0273-02 | integration | tests/integration/test_release_preview.py::test_stale_preview_reported | IF-RELEASE-002 |
| AC-FR0274-01 | integration + e2e | tests/integration/test_release_gate_cli.py::test_release_action_allowed + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-RELEASE-003 |
| AC-FR0274-02 | integration | tests/integration/test_release_gate_cli.py::test_rejected_gate_or_stale | IF-RELEASE-003 |
| AC-FR0274-03 | integration | tests/integration/test_release_gate_cli.py::test_delay_and_return | IF-RELEASE-003 |
| AC-FR0274-04 | integration | tests/integration/test_release_gate_cli.py::test_surface_exclusivity | IF-RELEASE-003 |
| AC-FR0275-01 | integration + e2e | tests/integration/test_publish_idempotency.py::test_planned_then_executed_done + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-PUBLISH-001 |
| AC-FR0275-02 | integration | tests/integration/test_publish_idempotency.py::test_resume_reconciled_skip | IF-PUBLISH-002 |
| AC-FR0275-03 | integration | tests/integration/test_publish_idempotency.py::test_agent_forbidden | IF-PUBLISH-001 |
| AC-FR0275-04 | integration | tests/integration/test_publish_idempotency.py::test_unknown_operation_and_conflict | IF-PUBLISH-001, IF-PUBLISH-002 |
| AC-FR0276-01 | integration + e2e | tests/integration/test_milestone_lifecycle.py::test_trace_closed_sealed_refs_clean + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-MILESTONE-001 |
| AC-FR0276-02 | integration | tests/integration/test_milestone_lifecycle.py::test_retry_tail_no_republish | IF-MILESTONE-001, IF-PUBLISH-002 |
| AC-FR0277-01 | integration + e2e | tests/integration/test_journey_versioning.py::test_feature_public_release + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-JOURNEY-001 |
| AC-FR0277-02 | integration + e2e | tests/integration/test_journey_versioning.py::test_post_release_patch + tests/e2e/test_hotfix_release_journeys.py::test_post_release_hotfix_journey | IF-JOURNEY-001 |
| AC-FR0277-03 | integration + e2e | tests/integration/test_journey_versioning.py::test_dev_prerelease_only + tests/e2e/test_hotfix_release_journeys.py::test_dev_hotfix_journey | IF-JOURNEY-001 |
| AC-FR0277-04 | integration | tests/integration/test_journey_versioning.py::test_identity_and_idempotent_journeys + tests/integration/test_journey_versioning.py::test_dev_precheck_fails_without_release_branch | IF-JOURNEY-001, IF-PUBLISH-002 |
| AC-FR0278-01 | integration | tests/integration/test_envelope_contract.py::test_unified_envelope_parse | IF-ENVELOPE-001 |
| AC-FR0278-02 | integration | tests/integration/test_envelope_contract.py::test_malformed_format_error | IF-ENVELOPE-001 |
| AC-FR0279-01 | integration | tests/integration/test_envelope_parity.py::test_parity_mismatch_rejects | IF-ENVELOPE-002 |
| AC-FR0279-02 | integration | tests/integration/test_envelope_parity.py::test_format_vs_semantic_events_separated | IF-ENVELOPE-002 |
| AC-FR0279-03 | integration | tests/integration/test_envelope_parity.py::test_malformed_regression_corpus | IF-ENVELOPE-002 |
| AC-FR0280-01 | integration | tests/integration/test_failure_evidence_chain.py::test_chain_events_replay | IF-FAILURE-001 |
| AC-FR0280-02 | integration | tests/integration/test_failure_evidence_chain.py::test_consistent_selection_rules | IF-FAILURE-001 |
| AC-FR0280-03 | integration | tests/integration/test_failure_evidence_chain.py::test_lost_or_mismatched_blocks | IF-FAILURE-001 |
| AC-FR0281-01 | integration | tests/integration/test_host_contract.py::test_materialized_contract_valid | IF-HOSTCONTRACT-001 |
| AC-FR0281-02 | integration | tests/integration/test_host_contract.py::test_execute_per_contract_only | IF-HOSTCONTRACT-001, IF-HOSTCONTRACT-002 |
| AC-FR0281-03 | integration | tests/integration/test_host_contract.py::test_failed_gate_machine_evidence_revision_loop | IF-HOSTCONTRACT-002 |
| AC-FR0282-01 | integration + e2e | tests/integration/test_reference_host.py::test_reference_host_journey_same_shape + tests/e2e/test_reference_host_journey.py::test_reference_host_release_journey | IF-REFERENCE-001 |
| AC-FR0282-02 | integration | tests/integration/test_reference_host.py::test_missing_credentials_needs_attention | IF-REFERENCE-001 |
| AC-FR0282-03 | integration | tests/integration/test_reference_host.py::test_python_details_isolated | IF-REFERENCE-001, IF-HOSTCONTRACT-001 |
| AC-FR0283-01 | integration | tests/integration/test_issue_mapping.py::test_real_issue_created_and_mapped | IF-ISSUE-001 |
| AC-FR0283-02 | integration | tests/integration/test_issue_mapping.py::test_missing_credentials_needs_attention | IF-ISSUE-001 |
| AC-FR0283-03 | integration | tests/integration/test_issue_mapping.py::test_fake_rejected_in_real_mode | IF-ISSUE-001 |
| AC-FR0283-04 | integration | tests/integration/test_issue_mapping.py::test_crash_idempotent_dedup | IF-ISSUE-001 |
| AC-FR0284-01 | integration | tests/integration/test_issue_close.py::test_close_with_trace_comment | IF-ISSUE-002, IF-MILESTONE-001 |
| AC-FR0284-02 | integration | tests/integration/test_issue_close.py::test_fake_counterexamples_rejected | IF-ISSUE-002 |
| AC-FR0285-01 | integration | tests/integration/test_pipeline_regression.py::test_write_collect_redcheck_prism_chain_intact | IF-PIPELINE-001 |
| AC-FR0285-02 | integration | tests/integration/test_pipeline_regression.py::test_no_selfcheck_authority | IF-PIPELINE-001 |
| AC-FR0285-03 | integration | tests/integration/test_pipeline_regression.py::test_no_new_pipeline_definition | IF-PIPELINE-001 |
| AC-FR0271-03 | integration | tests/integration/test_verify_prism_final.py::test_revise_requires_anchored_findings | IF-VERIFY-005 |
| AC-FR0286-01 | integration | tests/integration/test_inplace_repair.py::test_no_auto_rollback_in_place_rounds | IF-REPAIR-001 |
| AC-FR0286-02 | integration | tests/integration/test_inplace_repair.py::test_repair_disciplines_and_frozen_tests | IF-REPAIR-001 |
| AC-FR0286-03 | integration | tests/integration/test_inplace_repair.py::test_fix_new_candidate_rewalks_verify | IF-REPAIR-002 |
| AC-FR0286-04 | integration | tests/integration/test_inplace_repair.py::test_irreparable_blocked_routes_to_known_issue_or_escape | IF-REPAIR-002, IF-KNOWNISSUE-001 |
| AC-FR0286-05 | integration | tests/integration/test_known_issue.py::test_known_issue_registered_listed_and_waived | IF-KNOWNISSUE-001 |
| AC-FR0286-06 | integration | tests/integration/test_known_issue.py::test_exclusions_mechanism_security_no_hotfix | IF-KNOWNISSUE-001, IF-JOURNEY-001 |
| AC-FR0287-01 | integration | tests/integration/test_escape_gate.py::test_universal_return_moves_pointer | IF-ESCAPE-001 |
| AC-FR0287-02 | integration | tests/integration/test_escape_gate.py::test_escape_barrier_quarantines_late_outcomes | IF-ESCAPE-001 |
| AC-FR0287-03 | integration | tests/integration/test_escape_gate.py::test_return_stales_downstream_evidence | IF-ESCAPE-001 |
| AC-FR0287-04 | integration | tests/integration/test_escape_gate.py::test_irreversible_confirm_then_reconcile_skip | IF-ESCAPE-001, IF-PUBLISH-002 |
| AC-FR0287-05 | integration | tests/integration/test_escape_gate.py::test_abandon_terminal_zero_side_effects | IF-ESCAPE-002 |
| AC-NFR0143-01 | integration | tests/integration/test_release_trace.py::test_same_candidate_all_events | IF-TRACE-003, IF-VERIFY-001 |
| AC-NFR0143-02 | integration + e2e | tests/integration/test_release_trace.py::test_trace_export_digests + tests/e2e/test_release_journey.py::test_feature_release_journey | IF-TRACE-003 |
| AC-NFR0144-01 | integration | tests/integration/test_publish_reconcile.py::test_repeat_operation_skips_no_duplicates + tests/integration/test_publish_reconcile.py::test_unfinished_continues | IF-PUBLISH-002 |
| AC-NFR0144-02 | integration | tests/integration/test_publish_reconcile.py::test_same_key_remote_diff_conflict | IF-PUBLISH-002 |
| AC-NFR0145-01 | integration | tests/integration/test_envelope_parity.py::test_deterministic_parse_and_parity | IF-ENVELOPE-002 |
| AC-NFR0145-02 | integration | tests/integration/test_envelope_parity.py::test_malformed_no_business_mutation | IF-ENVELOPE-001, IF-ENVELOPE-002 |
| AC-NFR0146-01 | integration | tests/integration/test_failure_evidence_chain.py::test_append_only_replay_identical | IF-FAILURE-001 |
| AC-NFR0146-02 | integration | tests/integration/test_failure_evidence_chain.py::test_per_role_consumption_proofs | IF-FAILURE-001 |
| AC-NFR0147-01 | integration | tests/integration/test_kernel_language_neutrality.py::test_no_venv_wheel_hardcoding | IF-HOSTCONTRACT-001, IF-ADAPTER-003 |
| AC-NFR0147-02 | integration | tests/integration/test_kernel_language_neutrality.py::test_kernel_schema_language_free | IF-ADAPTER-003, IF-HOSTCONTRACT-001 |
| AC-NFR0148-01 | integration | tests/integration/test_issue_mapping.py::test_authoritative_map_consumed | IF-ISSUE-001 |
| AC-NFR0148-02 | integration | tests/integration/test_issue_mapping.py::test_fake_map_not_consumed_by_closers | IF-ISSUE-001 |
| AC-NFR0149-01 | e2e | tests/e2e/test_release_journeys_matrix.py::test_six_journeys_dual_host | IF-JOURNEY-001, IF-REFERENCE-001 |
| AC-NFR0149-02 | integration | tests/integration/test_journey_recovery.py::test_interrupt_replay_reconcile_matrix | IF-PUBLISH-002, IF-MILESTONE-001 |
| AC-NFR0149-03 | integration | tests/integration/test_failclosed_release.py::test_identity_stale_malformed_fake_blocked | IF-VERIFY-001, IF-PUBLISH-002, IF-ISSUE-001 |

---

## 9. SM-01 发布闭环转移覆盖

| # | Transition | Observable | integration test |
|:--|:--|:--|:--|
| 1 | M-IMPL boundary → M-VERIFY | stage.entered + candidate.frozen | test_verify_candidate::test_clean_tree_freezes_candidate |
| 2 | M-VERIFY → M-SECURITY | 全门禁过 + stage.exited | test_verify_prism_final::test_same_candidate_consistency_pass |
| 3 | M-VERIFY → BLOCKED | 任一门禁 fail-closed/stale | test_verify_local_gates::test_missing_or_malformed_gate_fails_closed、test_verify_ci_readback::test_mismatch_missing_stale_blocks |
| 4 | M-SECURITY → M-RELEASE / BLOCKED | security.assessed status | test_security_assessment 全部 |
| 5 | M-RELEASE → AWAITING_RELEASE | release.previewed | test_release_preview::test_preview_digest_binds_all |
| 6 | AWAITING_RELEASE → M-PUBLISH / DELAYED / RETURNED / 拒绝 | release.decided/rejected | test_release_gate_cli 全部 |
| 7 | DELAYED → AWAITING_RELEASE | 重生成 preview | test_release_gate_cli::test_delay_and_return |
| 8 | M-PUBLISH → M-MILESTONE / BLOCKED | publish.executed done 集 / blocked | test_publish_idempotency 全部 |
| 9 | M-MILESTONE → RELEASED / RETRY_TAIL | milestone.sealed / retry_tail | test_milestone_lifecycle 全部 |
| 10 | 任意 → 同状态（崩溃恢复） | replay 重建 + resume reconcile | test_journey_recovery::test_interrupt_replay_reconcile_matrix |

### 9.1 阻断恢复转移（architecture §1.0.14：就地修复 + Known Issue + 逃生门，FR-0286/FR-0287）

| # | Transition | Observable | integration test |
|:--|:--|:--|:--|
| R1 | needs_attention → 同阶段重试（A 类，candidate 保持） | 恢复 env 后 resume；失败门禁重跑、已完成门禁不重执行、candidate.frozen 不重复 | test_verify_ci_readback::test_missing_credentials_needs_attention（恢复后 ci=bound 且无重复冻结）；test_verify_candidate::test_dirty_tree_needs_attention |
| R2 | 缺陷 → 就地修复轮（B 类，SM-01 无回退） | repair.round_started(round≤3, classification)；status repair=in_place round=n/3；事件流无 stage.rolled_back 至 M-DESIGN 的自动回退 | test_inplace_repair::test_no_auto_rollback_in_place_rounds |
| R3 | 修复 commit → 新 candidate 重走（SM-01.20） | evidence.staled(reason=fix_new_candidate)；新 candidate.frozen；full_reuse 重判；旧 preview_digest 不复用 | test_inplace_repair::test_fix_new_candidate_rewalks_verify |
| R4 | 同 candidate 原地重跑（无 HEAD 移动修复） | 新 prism.verdict/security.assessed 绑定同一 candidate_sha（FR-0271-02/FR-0272-02） | test_verify_prism_final::test_prism_fail_blocks_m_impl_gap；test_security_assessment::test_unknown_or_malformed_blocks（瞬态路径） |
| R5 | 不可修复 → Known Issue / 逃生门（C 类） | blocked: irreparable；known-issue 登记/拒绝（not_product_defect）；preview 列出、trace waived+backlog | test_inplace_repair::test_irreparable_blocked_routes_to_known_issue_or_escape；test_known_issue 全部 |
| R6 | 通用回拨（SM-01.19，D 类） | escape.barrier_established(cutover_seq)→late outcome quarantine→human.return→evidence.staled(human_return)；不可逆清单确认后指针移动 | test_escape_gate::test_universal_return_moves_pointer + test_escape_barrier_quarantines_late_outcomes + test_return_stales_downstream_evidence + test_irreversible_confirm_then_reconcile_skip |
| R7 | 终止（SM-01.21/22） | terminal=cancelled；零外部副作用；trac run 拒绝 | test_escape_gate::test_abandon_terminal_zero_side_effects |
| R8 | Prism revise 锚定（FR-0271-03） | 无锚定线程的 revise 判 revise_without_findings、不计有效阻断 | test_verify_prism_final::test_revise_requires_anchored_findings |

---

## 10. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | `tests/e2e/test_full_journey_v05.py` | 追加断言：v0.5 journey 不产生 candidate.frozen/publish.*/milestone.* 事件 | capability 隔离（未达 RELEASE_PIPELINE_VERSION 保持 boundary） |
| 2 | `tests/integration/test_kernel_language_neutrality.py` | 追加 venv/wheel/pip token 用例与允许区断言 | NFR-0147 扫描扩展 |
| 3 | `tests/integration/test_github_effects.py` | 追加真实模式缺凭据 needs_attention 用例 | silent fake 废除 |
| 4 | `tests/integration/test_check_trace.py` | 追加 v0.8 release 段与早期版本输出隔离 | IF-TRACE-003 版本隔离 |
| 5 | `tests/integration/test_run_contract_audit.py` | 回归既有 test 命令执行在 host-contract 引入后同输入同输出 | 合同并存无回归 |

Devon 的 unit 更新由 RGR/coverage 自辖；本表不处方 unit 文件/函数。

---

## 11. E2E Happy Paths

### 11.1 tracks feature 发布旅程

`test_feature_release_journey`：seed 项目（stand-in 远程 + stand-in API + 显式模拟 overlay 的 agent 通道）→ `trac start`/`trac run` 走 M-STORY→M-IMPL→M-VERIFY（candidate 冻结、full_reuse=full_f、本地门禁、ci=bound、prism=pass）→ M-SECURITY → awaiting_release → 独立 release 子命令 `--action release` → M-PUBLISH merge/tag/artifact/release 全 done → M-MILESTONE 归档 → `terminal=released`；`trac report` 的 release.trace 与远端互验同一 candidate/preview_digest。e2e 不展开错误原因，错误矩阵归 integration。

### 11.2 tracks hotfix 两条旅程

`test_post_release_hotfix_journey`：在已 release 的 repo（含活跃 release 分支）经 hotfix 入口创建 fix 分支 → 旅程到 M-VERIFY 验证合入 main + 同步 release 分支 + patch tag/release。`test_dev_hotfix_journey`：仅合入活跃分支 + prerelease tag，断言无公开 tag/release、channel=pre-release。

### 11.3 reference host 旅程

`test_reference_host_release_journey`：从 candidate wheel 物化 reference host（bare 远程 + stand-in API）→ 其 v0.1 旅程全链与 tracks 同构（事件链形态断言）→ 同一性 report。

### 11.4 六旅程矩阵

`test_six_journeys_dual_host`：两宿主 × 三旅程在 stand-in 远程上顺序执行（共享 harness、独立 repo），断言每旅程终态与 release.trace 同一性；缺凭据计数路径在此覆盖 needs_attention 断言（真实模式无凭据环境变量组合）。live 孪生（真实远程/凭据/agent）在 `tests/e2e_live/test_release_journeys_live.py`，仅 milestone/weekly 运行。

---

## 12. Fail-closed / Counterexample Matrix

### 12.1 注入矩阵（integration）

| # | injection | 必须观察的阻断 |
|:--|:--|:--|
| 1 | dirty tree 进 M-VERIFY | attention.required(dirty_tree)，无冻结 |
| 2 | 冻结后新 commit | candidate.stale + 门禁 blocked |
| 3 | 含 STALE 的 FULL_F | 复用判定进重跑分支 |
| 4 | 删除/篡改合同 gate 声明 | local_gate.failed(missing_contract/unknown) |
| 5 | gate 输出畸形 JSON（file 通道） | local_gate.failed(malformed) |
| 6 | CI head SHA 异于 candidate | ci=mismatch blocked |
| 7 | 无 token 的 CI 回读 | needs_attention(missing_token)，非通过 |
| 8 | Prism 终审 failed/revise | 不进 M-SECURITY |
| 9 | 安全策略版本不匹配 | security=unknown blocked |
| 10 | stale preview 下三择一 | 拒绝退出 1，无 decided |
| 11 | 门禁失败下 release 决定 | release.rejected，状态不变 |
| 12 | 未声明 operation kind | publish.failed(unknown_operation) |
| 13 | 同幂等键远端异果 | reconcile_conflict blocked |
| 14 | Agent 触达发布效果面 | publish.blocked(agent_forbidden) |
| 15 | 真实模式 FAKE-N 映射 | fake_rejected + blocked: fake_not_allowed |
| 16 | 归档中映射 api_verified=false | 关闭前阻断，RETRY_TAIL |
| 17 | envelope 缺 kind/多 block/未知版本 | format_error，业务状态不变 |
| 18 | 六面版本不一致 | dispatch.rejected(version_parity_mismatch) |
| 19 | 未 ACK 富证据遭遇普通失败 | stored 记录保留、review 通过 |
| 20 | injected 引用不存在的 stored | review.failed blocked |
| 21 | 修复预算穷尽且 Prism 归因不变 | blocked: irreparable，可转 Known Issue 或逃生门 |
| 22 | Known Issue 未在 preview 列出时请求 release | release 拒绝并提示 known_issue not listed |
| 23 | 机制失败/安全 finding 申请 Known Issue | known_issue.rejected(not_product_defect)；安全未过 release 一律拒绝 |
| 24 | barrier 后到达的 late outcome | escape.late_outcome quarantine，无 checkpoint/publish/state 覆盖 |
| 25 | abandon 后的 run 推进 | trac run 非零拒绝；零外部副作用（无新 tag/branch、issue 不变） |
| 26 | 修复轮内修改冻结 int/e2e | 冻结保护拒绝（git diff 断言原冻结文件未改） |

### 12.2 Shield counterexample assets（`tests/counterexamples/v0.8/`）

| # | patch | bound AC |
|:--|:--|:--|
| 1 | `ce_binding_drop.patch`（移除事件 candidate_sha 携带） | AC-NFR0143-01 |
| 2 | `ce_preview_digest_omit_plan.patch`（digest 漏分量） | AC-FR0273-01 |
| 3 | `ce_idempotency_key_weak.patch`（键弱化） | AC-NFR0144-01 |
| 4 | `ce_envelope_last_json_fallback.patch`（恢复回退解析） | AC-FR0278-02 |
| 5 | `ce_fake_silent_fallback.patch`（恢复缺 token 降级） | AC-FR0283-02 |
| 6 | `ce_gate_exit_zero.patch`（门禁软放行） | AC-FR0269-02 |
| 7 | `ce_agent_publish.patch`（Agent 直推远端） | AC-FR0275-03 |
| 8 | `ce_stale_evidence_reuse.patch`（stale 复用） | AC-FR0268-03 |
| 9 | `ce_rich_evidence_overwrite.patch`（未 ACK 覆盖） | AC-FR0280-01 |
| 10 | `ce_kernel_venv_token.patch`（kernel 硬编码语言 token） | AC-NFR0147-01 |

每 patch 单 AC/IF、`git apply --check` 可应用、scope 不含 tests/；由 Runtime 在隔离 worktree 真跑对应真实 gate 验证 kill（v0.7 mutation 机制复用）。

### 12.3 阻断恢复断言（§1.0.14 / §9.1）

上表 1–26 的每条注入在断言阻断之外，同时断言恢复出口：blocked 注入携带非空 `repair_route`（exit_class 落入 A–D 封闭集）或 needs_attention 注入携带 `next=` 指引；就地修复注入断言无自动 stage.rolled_back；修复 commit 注入追加一轮修复动作断言新 candidate 重走（evidence.staled(reason=fix_new_candidate) + 新 candidate.frozen，旧证据未被复用）；逃生注入断言 barrier 先于指针移动、late outcome 不产生 checkpoint/publish。恢复断言不新增 §8 行——归属既有 AC（AC-FR0267-03、AC-FR0269-02、AC-FR0270-03、AC-FR0271-02/03、AC-FR0272-02、AC-FR0273-02、AC-FR0275-04、AC-FR0286-01..06、AC-FR0287-01..05、AC-NFR0149-02/03 的修复后路径）。
