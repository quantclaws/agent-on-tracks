---
spec_id: SPEC-007
created: 2026-08-24
status: draft
sha:
---

# 可信测试证据与一致质量基线（v0.7-A）— Test Plan

- **Related acceptance**: `.tracks/projects/v0.7/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.7/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

本计划只断言 interfaces.md §4 的外部出口：

- 既有 `trac run/status/replay/report/check trace/validate` stdout、stderr、exit code 与 JSON。
- `.tracks/runtime/tracks.db` append-only events、其引用的 content-address blobs，以及 drop/rebuild 后投影。
- `.tracks/projects/project.toml`、architecture.md §4.2 registry block、pre-commit/CI config、v0.6 sealed docs/test digests。
- 隔离 git worktree 的实际 diff identity、target/control normalized result 与 rollback clean；真实宿主树保持不变。
- wheel 安装创建的 demo repo：venv/import path/hooksPath/CI binding/adapter contract 与事件证据。

测试不因内部类、私有 state、函数调用次数或 mock 返回值通过；需要的内部判定必须先落 interfaces.md 的 event/CLI/file outlet。

### 1.2. Non-observable Objects (tests do not directly depend on)

- kernel 的 Phase 0/RED_CHECK 分支与 State 内部字段；经 events/status 观察。
- adapter、manifest、parity parser 的私有 helper；经 normalized result、events、validate exit 观察。
- Runtime 的 worktree/temp 目录命名与重试实现；经 command/digest/rollback/replay 观察。
- demo provisioning 的文件复制顺序；经最终真实安装路径证据观察。

### 1.3. Cheating Patterns (CI enforced interception)

| # | Cheating Pattern | v0.7-A 典型症状 |
|:--|:--|:--|
| 1 | assertion 迁就实现 | 把 `target_kill=verified` 放宽为 event 存在 |
| 2 | skip/xfail 逃避 | 被选 node 未执行却计入 closure |
| 3 | marker 冒充证据 | 只补 TRACKS-TRACE 而无 collected node/evidence |
| 4 | Agent 自述冒充执行 | 自述 parity/kill 通过，无 Runtime event/blob |
| 5 | broad/no-op mutation | 一条空 patch 为多个 AC 背书 |
| 6 | mutation tests | patch 修改 tests/ 以制造 kill |
| 7 | foreign candidate evidence | 用另一个 candidate digest 的 experiment/FULL pass |
| 8 | private demo shortcut | editable install、import tests helper、跳过 hooks/CI/adapter |
| 9 | soft guard | `--exit-zero`、scope/threshold/命令三处不一致 |
| 10 | trivial assertion | `assert True`、只断言非空或吞异常 |

### 1.4. Safeguards (CI checks + PR process)

1. **AC trace**：每个 Shield test 的 `def` 紧邻上方使用 `# AC-FRXXXX-YY@v0.7 TRACKS-TRACE ...`；多 AC 各一行。每 AC ≥1 collected node、每 node ≥1 AC。
2. **Authenticity**：Runtime 以冻结 baseline 分类 new/existing；new 必须 AC/IF-specific legal Red，existing 必须 AC-specific counterexample kill。unrelated failures 不计。
3. **Mutation assets**：`tests/counterexamples/v0.7/*.patch` 单 AC/IF、`git apply --check`、scope 不含 tests；target 必死、controls 必绿。
4. **Static anti-pattern scan**：禁止 sole trivial assert、bare swallow、无 issue 的 skip/xfail、test-scope mutation、`--exit-zero`、manifest 多 AC。
5. **Candidate closure**：M-IMPL EXIT 同时要求 `trac check trace --version v0.7` 与 ISLAND_GATE_2，digest/identity 一致。
6. **Frozen tests**：Phase 0 seal 后所有角色都不能修改基线 integration/e2e；Devon scope 仍仅 `tracks/`、`tests/unit/`。
7. **PR classification**：New AC / Spec change / flake-environment issue 三选一；“实现与 spec 不符所以改测试”拒绝。
8. **Testability fallback**：缺 public outlet 时回修 interfaces/acceptance，不 mock internals。

### 1.5. Test Division of Labor

- **Unit**：Devon 对每个已实现 FR/NFR 的普遍 RGR 义务，由 coverage ≥95% 保证；§8 不规划 unit。
- **Integration**：Shield 验证 interfaces.md 跨模块合同及边界/错误矩阵。
- **E2E**：Shield 只写两条 happy path：v0.7-A 主旅程与双宿主演证成功旅程。
- **Counterexamples**：Shield 编写，Prism 独立审语义/最小性，Runtime 真跑，Devon 不改冻结资产。
- **Machine evidence**：Runtime 独家产生；Agent 不能自报结论。

---

## 2. Test Environment

### 2.1. Directory Layout

```text
tests/
├── unit/                                  # Devon 自辖，不在 §8 处方
├── integration/
│   ├── test_phase0_binding.py
│   ├── test_phase0_quality_seal.py
│   ├── test_guard_registry.py
│   ├── test_guard_parity.py
│   ├── test_authenticity_gate.py
│   ├── test_counterexample_kill.py
│   ├── test_mutation_manifest.py
│   ├── test_mutation_experiment.py
│   ├── test_adapter_contract.py
│   ├── test_kernel_language_neutrality.py
│   ├── test_trace_closure.py
│   ├── test_v07_events.py
│   ├── test_failclosed_scenarios.py
│   └── test_demo_host.py
├── e2e/
│   ├── test_v07_journey.py
│   └── test_dualhost_acceptance.py
├── counterexamples/v0.7/                 # 单 AC/IF non-test patches
├── assets/                               # 小型离线事件/配置反例
├── e2e_live/                             # 继承外部通道；v0.7 无新增 live AC
└── ground_truth/                         # 既有资产不改；v0.7 §3 不适用
```

本节列出的 v0.7 integration/e2e 与 counterexample 文件均为 **Shield 在 M-TEST 待编写资产**，不是当前既有命令或测试。demo repo 不作为提交的 fixture repo；Runtime 从 wheel 内 `tracks/assets/demo_host/` 动态创建到隔离目录。

### 2.2. Naming Conventions

- File：`test_<contract_area>.py`；function：`test_<observable_contract>`。
- AC marker：`# AC-FR0262-01@v0.7 TRACKS-TRACE manifest minimal fields`。
- Counterexample：`ce_<specific_failure>.patch`；manifest 每次运行由 Runtime 绑定 candidate 后生成，不提交 stale digest。

### 2.3. Execution

- 默认离线且确定性；顺序 unit → integration → e2e。
- integration/e2e 使用注册的 `integration`/`e2e` marks 与路径隔离；performance/live 默认排除。
- git/mutation 使用每测试 fresh repo/worktree，不在 tracks 工作树直接 apply fault。
- crash recovery 由明确的中断 checkpoint/事件回放触发，不用 sleep race。
- xdist 并发与 JUnit output 只来自 project contract；Runtime 不注入。

### 2.3.1. Test Execution Contract (`.tracks/projects/project.toml`)

- **Unit**：framework `pytest`；paths `tests/unit/`；collect/run/run_selected/cwd 继承现合同。
- **Integration**：framework `pytest`；paths `tests/integration/`；collect `.venv/bin/python -m pytest --collect-only -q tests/integration/`；run `.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；cwd `.`。
- **E2E**：framework `pytest`；paths `tests/e2e/`；collect `.venv/bin/python -m pytest --collect-only -q tests/e2e/`；run `.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`；cwd `.`。
- **Adapter**：`id="reference-pytest"`, `protocol="tracks-test-result"`, `version=1`。
- 所有 `run_selected` 含 `{nodes}`、所有 run/run_selected 含 `{result}` 恰好一次；M-TEST 独立 collect/run。

### 2.4. Test Data

- **Source**：合成、git tracked small fixtures 与 wheel-shipped demo template。v0.6 gap 集固定为 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02，额外 gap 由 fixture 增补。
- **Reproducibility**：candidate/patch/config/node inputs 全部 content digest；固定 timestamps 不参与 identity。
- **Scenario data**：九类 fault 各自生成独立 patch/config/result，不共享一个 broad fixture。
- **Demo**：`demo_calc` 源码 + unit/integration/e2e 三层小节点；Runtime fresh repo、fresh venv、non-editable wheel。
- **Sensitive data**：无新增凭据。既有 live secrets 只在 §6 L3。
- **Cleanup**：experiment worktree/demo temp 可删；events/blobs、wheel SHA、normalized results 保留供 replay。

### 2.5. Installation & Isolation

继承首版安装合同：E2E 从 candidate wheel 安装到 fresh venv，从源码树外 cwd 调用 `trac`，禁止 editable install。v0.7 双宿主 E2E 额外断言：

1. wheel SHA 记录且 `tracks.__file__` 不在源码树；
2. demo 运行 `trac init`，再物化 Archer-style project/guard contracts；
3. Runtime 执行 `git config core.hooksPath .githooks` 并 readback；
4. generated CI binding 与 `[adapter]` 在位；
5. demo 命令只走安装后的 CLI/Runtime，不传私有 overlay、不 import `tests.*`。

---

## 3. Ground Truth Method

v0.7-A 判定不适用独立 ground-truth 脚本。需求是事件/身份/路由/隔离规则，而非数值算法：approved AC 集、collected node 集、git actual diff、config argv、normalized node outcomes 与 fixture 本身分别是独立事实。另写“参考实现”会复刻被测 mutation/parity/closure 算法，反而违反独立性。

预期由小型输入直接重算：hash 使用标准库 `sha256` 对公开 canonical JSON；patch identity 由 `git diff --binary`；node outcome 由 adapter result 文件；registry category set 由 TOML 数据本身。Shield 测试可在测试体中用标准库做一次局部重算，但不创建 `tests/ground_truth/` 新文件、不 import 被测 helper 来求期望。既有 ground_truth 资产继承且不修改。

---

## 4. Test Scope

本计划覆盖 SPEC-007 与 ACC-007 全部 48 条 required AC。Validity/Testability/Decision 均为绿；无开放产品决定。

| Valid | Testable | Decided |
|:--|:--|:--|
| ✅ | ✅ | ✅ |

范围外：第二 adapter、M-VERIFY、CI SHA 回读、nightly result fetch、构建/发布自动化、v0.7-B。

---

## 5. Acceptance Criteria

1. Unit coverage ≥95%，source omit 为空；Devon unit + Shield integration/e2e 合并计量。
2. interfaces.md 每个 `modules` 2+ 的新合同至少一条 integration happy+关键 error/edge。
3. 两条 e2e happy path 全绿；错误矩阵只在 integration。
4. §8 的 48 条 AC 全部有 integration/e2e layer 与已注册 IF。
5. Phase 0 三个已知 gap + 现场额外 gap 全部 real collected-node binding，marker-only 不通过。
6. 八类 registry、三处 parity、无 `--exit-zero`、真实 required-check evidence。
7. new legal Red / existing kill / mutation target-control / candidate-bound closure 均由 Runtime events+blobs 证明。
8. 两宿主各九场景全部 fail-closed，demo path equivalent，crash recovery replay_ok。

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描结果：v0.7-A 新增能力只依赖本地 git、subprocess、wheel/venv 与文件系统，无新增外部服务、凭据、网络或真实时钟。因此本版 AC 全部属于默认 deterministic 通道；不得把任一 v0.7 AC 推给 live。

既有 GitHub/Opencode 外部依赖继承 D-18 三层机制：

### 6.1. Constraints

| # | Constraint | Consequence |
|:--|:--|:--|
| C1 | routine CI 无生产凭据 | fake/contract sim 覆盖既有外部协议 |
| C2 | live 网络/模型不稳定 | 独立 marker/path/job，不污染默认 suite |
| C3 | 不 mock tracks internals | 只替换真实外部 service；v0.7 local gates 真跑 |

### 6.2. Controllable vs Mock

- v0.7 mutation/git/adapter/guard/demo 全为本地真实依赖：用 temp repo/venv 控制，不 mock。
- 既有 GitHub/Opencode 可用 FakeBackend/stand-in；kernel/executor 不替换。

### 6.3. Three Layers

| Layer | Name | v0.7 Coverage | Default |
|:--|:--|:--|:--|
| L1 | deterministic local | 全部 48 AC：temp git、config variants、reference adapter、demo wheel | ✅ |
| L2 | contract sim | 既有 external adapter/fake；v0.7 project/guard/adapter machine contract | ✅ |
| L3 | live smoke | 继承 `tests/e2e_live`，与 v0.7 AC 不重叠 | ❌ routine；weekly/manual；milestone hard |

L3 缺凭据必须显式 `LIVE_SKIPPED: missing <NAME>`；weekly/manual skip 不 fail，release/tag milestone 缺凭据 fail。该 channel 不作为 v0.7-A 通过证据。

### 6.4. Test Infrastructure Responsibility

| # | Component | Responsibility | Boundary |
|:--|:--|:--|:--|
| 1 | temp git/worktree | 真实 apply/diff/rollback | 不决定 target/control 语义 |
| 2 | reference adapter | collect/run/normalize host result | 不判断 AC authenticity |
| 3 | scenario synthesizer | 生成一类最小 fault | 不绕过真实 gate、不合并场景 |
| 4 | demo provisioner | wheel/venv/init、asset architecture 固定落点、同 registry loader/deployer、hooks/CI | 不 import private test shortcut、不读旧 `guards.toml`、不走专用 parser |
| 5 | event/blob reader | 断言公开 evidence | 不读私有 state |

### 6.5. Assertion Basis — Closure with interfaces.md

断言只落 interfaces.md §4：`phase0.*`、`guard.parity`、`authenticity.judged`、`mutation.*`、adapter audit、`demo.equivalence`、`failclosed.*`，以及 §2 CLI 与 §3 files/git。无出口时修 interfaces，不 snoop internals。

`test_deploy_mechanism_generates_host_configs` 必须从 wheel asset 逐字节物化 demo `.tracks/projects/v0.1/architecture.md`、用公开 `load_guard_registry`/`validate_guard_registry`/`deploy_guard_configs`，并断言：八类与三个真实 config digest 通过；package-data allowlist、built wheel 与 fresh repo 均不含仓库 inherited legacy `guards.toml`；deployment record、hook `TRACKS_GUARD_REGISTRY`、CI env 与 `guard.parity.registry` 四者同 digest；tracks/demo digest 因已声明 host profile 差异而不相等。篡改 `flake8.ini` 或任一生成点只产生 hard error/blocked，不允许 fallback。

---

## 7. CI Gate

- **Stable required checks**：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`；merge 全 required。
- **Milestone**：`release-evidence`（tag/release，needs routine 全部）；本版不新增/改名 job。
- **lint**：registry categories 1–6；真实非零阻断，pre-commit/CI parity，无 `--exit-zero`。
- **coverage**：`coverage report --fail-under=95`，source omit none；Phase 0 也真跑并落计量。
- **test**：unit+integration+e2e deterministic；live path 不在此 job。
- **trace**：AC marker + collected node + v0.7 candidate closure；`trac check trace --version v0.7` 在对应 version gate 运行。
- **deliverables/reach**：stubs 接线后无 orphan；wheel 包含 demo template。
- **anti-pattern**：trace/manifest static scan、registry validate、language-neutral scan、mutation tests path ban；违规硬失败。
- **failure semantics**：任何 required check、parity、authenticity、experiment、closure、demo 演证失败都不产生 stage exit；Agent 自述无效。

---

## 8. AC Coverage

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0256-01 | integration + e2e | tests/integration/test_phase0_binding.py::test_gap_acs_bound_to_real_collected_nodes + tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure | IF-PHASE-001 |
| AC-FR0256-02 | integration | tests/integration/test_phase0_binding.py::test_marker_only_closure_stays_fail | IF-PHASE-001, IF-TRACE-002 |
| AC-FR0256-03 | integration | tests/integration/test_phase0_binding.py::test_field_gap_recovery_and_blocked_routing | IF-PHASE-001 |
| AC-FR0257-01 | integration | tests/integration/test_phase0_quality_seal.py::test_coverage_ratio_by_collected_exclude_none | IF-PHASE-002, IF-GUARD-001 |
| AC-FR0257-02 | integration | tests/integration/test_phase0_quality_seal.py::test_guard_hardened_violations_zero_revisions_audited | IF-GUARD-001, IF-GUARD-002 |
| AC-FR0257-03 | integration | tests/integration/test_phase0_quality_seal.py::test_marks_registered_and_env_contract_valid | IF-PHASE-002, IF-ADAPTER-001 |
| AC-FR0257-04 | integration + e2e | tests/integration/test_phase0_quality_seal.py::test_seal_readonly_blocks_rewrite + tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure | IF-PHASE-003 |
| AC-FR0257-05 | integration | tests/integration/test_phase0_quality_seal.py::test_unrecoverable_coverage_guard_blocked | IF-PHASE-003 |
| AC-FR0258-01 | integration | tests/integration/test_guard_registry.py::test_registry_single_source_eight_categories | IF-GUARD-001 |
| AC-FR0258-02 | integration | tests/integration/test_guard_parity.py::test_parity_mismatch_blocks_fail_closed | IF-GUARD-002 |
| AC-FR0258-03 | integration | tests/integration/test_guard_parity.py::test_deploy_mechanism_generates_host_configs | IF-GUARD-002 |
| AC-FR0258-04 | integration | tests/integration/test_guard_registry.py::test_registry_migration_no_silent_gap | IF-GUARD-001 |
| AC-FR0259-01 | integration | tests/integration/test_guard_registry.py::test_prism_revise_routes_back_to_archer | IF-GUARD-001 |
| AC-FR0259-02 | integration | tests/integration/test_guard_parity.py::test_registry_real_execution_evidence | IF-GUARD-001, IF-GUARD-002 |
| AC-FR0260-01 | integration + e2e | tests/integration/test_authenticity_gate.py::test_new_behaviour_legal_red_on_frozen_baseline + tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure | IF-AUTH-001 |
| AC-FR0260-02 | integration | tests/integration/test_authenticity_gate.py::test_unrelated_downstream_failure_isolated | IF-AUTH-001 |
| AC-FR0260-03 | integration | tests/integration/test_authenticity_gate.py::test_phase0_gate_blocks_mtest_before_seal | IF-PHASE-003, IF-AUTH-001 |
| AC-FR0260-04 | integration | tests/integration/test_authenticity_gate.py::test_d41_selection_semantics_coexist | IF-AUTH-001 |
| AC-FR0260-05 | integration | tests/integration/test_authenticity_gate.py::test_broad_mutation_irrelevant_red_rejected | IF-AUTH-001, IF-MUTATION-001 |
| AC-FR0261-01 | integration + e2e | tests/integration/test_counterexample_kill.py::test_existing_green_requires_kill_verified + tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure | IF-AUTH-002, IF-MUTATION-002 |
| AC-FR0261-02 | integration | tests/integration/test_counterexample_kill.py::test_ac_specific_binding_and_minimality_reviewed | IF-AUTH-002, IF-MUTATION-002 |
| AC-FR0261-03 | integration | tests/integration/test_counterexample_kill.py::test_frozen_tests_untouchable_role_separation | IF-AUTH-002, IF-PHASE-003 |
| AC-FR0262-01 | integration | tests/integration/test_mutation_manifest.py::test_manifest_minimal_field_set_language_neutral | IF-MUTATION-001 |
| AC-FR0262-02 | integration | tests/integration/test_mutation_manifest.py::test_no_batch_endorsement_noop_patch | IF-MUTATION-001 |
| AC-FR0262-03 | integration | tests/integration/test_mutation_manifest.py::test_tests_scope_mutation_blocked | IF-MUTATION-001 |
| AC-FR0263-01 | integration + e2e | tests/integration/test_mutation_experiment.py::test_experiment_chain_target_kill_controls_green + tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure | IF-MUTATION-002 |
| AC-FR0263-02 | integration | tests/integration/test_mutation_experiment.py::test_events_append_only_replayable | IF-MUTATION-002 |
| AC-FR0263-03 | integration | tests/integration/test_mutation_experiment.py::test_crash_recovery_reruns_no_phantom_pass | IF-MUTATION-002 |
| AC-FR0263-04 | integration | tests/integration/test_mutation_experiment.py::test_failure_matrix_fail_closed | IF-MUTATION-002 |
| AC-FR0264-01 | integration | tests/integration/test_adapter_contract.py::test_three_interface_seam_protocol_version | IF-ADAPTER-001 |
| AC-FR0264-02 | integration | tests/integration/test_adapter_contract.py::test_reference_adapter_equivalent_to_v06 | IF-ADAPTER-002 |
| AC-FR0264-03 | integration | tests/integration/test_adapter_contract.py::test_unknown_adapter_malformed_result_blocked | IF-ADAPTER-001 |
| AC-FR0264-04 | integration | tests/integration/test_kernel_language_neutrality.py::test_no_language_tokens_kernel_executor_cli | IF-ADAPTER-003 |
| AC-FR0265-01 | integration + e2e | tests/integration/test_trace_closure.py::test_closure_candidate_bound_pass + tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure | IF-CLOSURE-001 |
| AC-FR0265-02 | integration | tests/integration/test_trace_closure.py::test_blocking_conditions_hard_errors | IF-CLOSURE-001 |
| AC-FR0265-03 | integration | tests/integration/test_trace_closure.py::test_candidate_digest_consistency | IF-CLOSURE-001, IF-MUTATION-001 |
| AC-FR0266-01 | integration + e2e | tests/integration/test_failclosed_scenarios.py::test_tracks_host_nine_scenarios_blocked + tests/e2e/test_dualhost_acceptance.py::test_dualhost_nine_scenarios_all_fail_closed | IF-FAILCLOSED-001 |
| AC-FR0266-02 | integration | tests/integration/test_demo_host.py::test_demo_created_via_real_install_path | IF-DEMO-001 |
| AC-FR0266-03 | integration + e2e | tests/integration/test_failclosed_scenarios.py::test_crash_recovery_replay_ok + tests/e2e/test_dualhost_acceptance.py::test_dualhost_nine_scenarios_all_fail_closed | IF-FAILCLOSED-001 |
| AC-FR0266-04 | integration | tests/integration/test_failclosed_scenarios.py::test_any_leak_or_inequivalence_blocks | IF-FAILCLOSED-001, IF-DEMO-001 |
| AC-NFR0140-01 | integration | tests/integration/test_v07_events.py::test_append_only_and_projection_rebuild | IF-AUTH-001, IF-MUTATION-002 |
| AC-NFR0140-02 | integration | tests/integration/test_v07_events.py::test_identity_five_part_binding | IF-MUTATION-001 |
| AC-NFR0140-03 | integration | tests/integration/test_v07_events.py::test_wal_replay_reruns_missing_never_pass | IF-MUTATION-002 |
| AC-NFR0140-04 | integration | tests/integration/test_v07_events.py::test_unknown_missing_drift_control_fail_closed | IF-AUTH-001, IF-MUTATION-002 |
| AC-NFR0141-01 | integration | tests/integration/test_guard_parity.py::test_parity_event_programmatic_no_agent_selfreport | IF-GUARD-002 |
| AC-NFR0141-02 | integration | tests/integration/test_kernel_language_neutrality.py::test_language_invariant_dual_check | IF-ADAPTER-003 |
| AC-NFR0142-01 | integration | tests/integration/test_demo_host.py::test_path_equivalence_evidence | IF-DEMO-001 |
| AC-NFR0142-02 | integration + e2e | tests/integration/test_failclosed_scenarios.py::test_demo_host_crash_recovery_rebuild + tests/e2e/test_dualhost_acceptance.py::test_dualhost_nine_scenarios_all_fail_closed | IF-FAILCLOSED-001 |

---

## 9. Phase 0 SM-01 Transition Coverage

| # | Transition | Observable | integration test |
|:--|:--|:--|:--|
| 1 | v0.6 delivered → UNSEALED | status phase0=unsealed | test_phase0_binding::test_gap_acs_bound_to_real_collected_nodes |
| 2 | UNSEALED → PHASE0_VALIDATING | phase0 command issued | same |
| 3 | PHASE0_VALIDATING → self | failed check fixed then rerun; prior events retained | test_phase0_quality_seal::test_unrecoverable_coverage_guard_blocked |
| 4 | PHASE0_VALIDATING → SEALED | baseline_repaired + coverage + guard_hardened + sealed | test_phase0_quality_seal::test_seal_readonly_blocks_rewrite |
| 5 | PHASE0_VALIDATING → BLOCKED | phase0.blocked + status reason | test_phase0_binding::test_field_gap_recovery_and_blocked_routing |
| 6 | SEALED terminal/non-return | rewrite/drift rejected | test_phase0_quality_seal::test_seal_readonly_blocks_rewrite |

---

## 10. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | `tests/integration/test_run_contract_audit.py` | 回归现有 command/result 行为在 reference adapter 后同输入同输出；不改原断言语义 | FR-0264 重构而非行为变更 |
| 2 | `tests/integration/test_check_trace.py` | 回归 v0.6 及早期 trace 输出不变；v0.7 另走 candidate closure | 版本能力隔离 |
| 3 | `tests/integration/test_commit_hook_rejection.py` | 增硬门反例：pylint 违规被 hook 拒绝、无 `--exit-zero` | Phase 0 hardening |
| 4 | v0.5/v0.6 journey e2e | 回归早期版本不触发 Phase 0/双宿主演证 | capability backward compatibility |
| 5 | wheel/package-data integration | 安装 wheel 后定位 demo architecture/flake8/project/source/test assets，并证明 allowlist/wheel/fresh repo 无 legacy `guards.toml` 或预制 hook/CI（不修改仓库 inherited file） | demo 必须走真实安装路径与同一 registry mechanism |

Devon 对迁移函数的 unit 更新仍由 RGR/coverage 自辖；本表不处方 unit 文件/函数。

---

## 11. E2E Happy Paths

### 11.1 v0.7-A 主旅程

`test_v07a_journey_phase0_to_closure`：v0.6 baseline 含三 gap → `trac run` Phase 0 真实绑定、coverage/guards/marks/env 通过、seal → M-TEST new legal Red + existing green/counterexample kill → M-IMPL candidate mutation experiment target killed/control green → FULL pass → `trac check trace --version v0.7` candidate-bound pass。e2e 不展开每个错误原因，错误矩阵归 integration。

### 11.2 双宿主演证旅程

`test_dualhost_nine_scenarios_all_fail_closed`：从 candidate wheel 安装 Runtime → tracks host 9 场景全部 blocked → 动态创建 demo host、从固定 architecture 落点经同 loader/deployer 产生 equivalence=true 与四向 registry-digest 关联 → demo 9 场景全部 blocked → controlled interruption/restart → 两个 summary 均 `all_fail_closed=true, crash_recovery=replay_ok` → boundary。不得用 test-private shortcut。

---

## 12. Fail-closed / Counterexample Matrix

### 12.1 双宿主九场景

| # | scenario | 最小 fault | 必须观察的阻断 |
|:--|:--|:--|:--|
| 1 | broad_mutation | no selectable diff / multi-AC endorsement | manifest `no_selectable_diff_identity` |
| 2 | stale_patch | patch digest/baseline 不匹配 | experiment `stale_patch`/apply mismatch |
| 3 | wrong_candidate | manifest candidate ≠ worktree candidate | blocked `wrong_candidate` |
| 4 | uncollected_node | target 不在 adapter collect set | blocked `uncollected_node` |
| 5 | unrelated_red | 失败 node 无 AC 绑定 | authenticity unrelated=true，Red 不成立 |
| 6 | target_survived | patch apply 但 target 仍绿 | blocked `target_survived` |
| 7 | control_hit | control node 失败 | blocked `control_hit` |
| 8 | malformed_adapter_result | unknown adapter 或缺/重/多 node result | blocked unknown/malformed |
| 9 | guard_parity_mismatch | 在已由各宿主 canonical registry 成功部署的 isolated 副本中，只漂移 pre-commit/CI 命令、digest 引用或注入 exit-zero | guard.parity blocked；demo 不换 parser/registry source |

每场景分别在 `host=tracks`、`host=demo-pytest` 落 detail event；scenario 名不得复用。任一 outcome=leaked 则 summary blocked。

### 12.2 Shield counterexample assets

| # | patch | bound test / AC |
|:--|:--|:--|
| 1 | `ce_phase0_marker_only.patch` | marker-only 不闭合 / AC-FR0256-02 |
| 2 | `ce_coverage_source_omit.patch` | source exclude 拒绝 / AC-FR0257-01 |
| 3 | `ce_guard_exit_zero.patch` | parity 硬门 / AC-FR0257-02, AC-FR0258-02 |
| 4 | `ce_registry_missing_category.patch` | 八类完整性 / AC-FR0258-01 |
| 5 | `ce_unrelated_red_counted.patch` | unrelated 隔离 / AC-FR0260-02/05 |
| 6 | `ce_existing_green_without_kill.patch` | existing kill 必须 / AC-FR0261-01 |
| 7 | `ce_broad_noop_endorsement.patch` | 单 AC/diff identity / AC-FR0262-02 |
| 8 | `ce_mutation_touches_tests.patch` | tests scope ban / AC-FR0262-03 |
| 9 | `ce_target_survives.patch` | target kill / AC-FR0263-04 |
| 10 | `ce_control_hit.patch` | controls green / AC-FR0263-04 |
| 11 | `ce_unknown_adapter_accepted.patch` | adapter fail-closed / AC-FR0264-03 |
| 12 | `ce_kernel_language_token.patch` | language invariant / AC-FR0264-04, AC-NFR0141-02 |
| 13 | `ce_foreign_candidate_closure.patch` | candidate digest consistency / AC-FR0265-03 |
| 14 | `ce_sealed_baseline_rewrite.patch` | v0.6 readonly / AC-FR0257-04 |

每 patch 的 manifest 只列一个 AC/IF（共享反例需生成独立 manifest），current candidate 上 `git apply --check` 成功，Runtime kill/control 实跑结果入 events；stale patch 文本视同缺失证据。
