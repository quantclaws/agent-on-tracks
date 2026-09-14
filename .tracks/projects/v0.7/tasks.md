# Task Graph

## T-001
- Issue: #98
- Description: 实现 IF-ADAPTER-001 基础合同：TestNode/TestRunResult、resolve_adapter 与未知 id/protocol/version 的 fail-closed；project loader 暴露 tracks-test-result v1。
- AC refs: AC-FR0264-01, AC-FR0264-03
- FR refs: FR-0264
- IF ids: IF-ADAPTER-001
- Unit refs: -
- Acceptance refs: tests/integration/test_adapter_contract.py::test_three_interface_seam_protocol_version
- Scope: tracks/adapters/base.py, tracks/project.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-002
- Issue: #98
- Description: 实现 IF-ADAPTER-002 reference adapter：collect、{nodes}/{result} 展开、normalized result exact coverage 与 malformed-result fail-closed；resolved Adapter 身份必须可审计。
- AC refs: AC-FR0264-02
- FR refs: FR-0264
- IF ids: IF-ADAPTER-002
- Unit refs: -
- Acceptance refs: tests/integration/test_adapter_contract.py::test_reference_adapter_equivalent_to_v06, tests/integration/test_adapter_contract.py::test_unknown_adapter_malformed_result_blocked
- Scope: tracks/adapters/reference_pytest.py
- Depends on: T-001
- Batch: 2
- Parallel: True

## T-004
- Issue: #90
- Description: 在已交付 executor collected-node binding 基线上，实现 IF-PHASE-001 的 kernel Phase 0 投影、blocked 路由与 composition 边界；executor façade 由后继质量切片统一拥有。
- AC refs: AC-FR0256-01, AC-FR0256-02, AC-FR0256-03
- FR refs: FR-0256
- IF ids: IF-PHASE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_phase0_binding.py::test_gap_acs_bound_to_real_collected_nodes, tests/integration/test_phase0_binding.py::test_marker_only_closure_stays_fail, tests/integration/test_phase0_binding.py::test_field_gap_recovery_and_blocked_routing
- Scope: tracks/kernel/phase0.py
- Depends on: T-001
- Batch: 2
- Parallel: True

## T-005
- Issue: #91
- Description: 实现 FR-0257 executor Phase 0 纵向切片：IF-PHASE-002 coverage/marks/environment、IF-PHASE-003 稳定 seal_id/只读冻结/drift-BLOCKED，并由唯一 executor/phase0.py façade 对外提供结果。
- AC refs: AC-FR0257-01, AC-FR0257-03, AC-FR0257-04, AC-FR0257-05
- FR refs: FR-0257
- IF ids: IF-PHASE-002, IF-PHASE-003
- Unit refs: -
- Acceptance refs: tests/integration/test_phase0_quality_seal.py::test_coverage_ratio_by_collected_exclude_none, tests/integration/test_phase0_quality_seal.py::test_marks_registered_and_env_contract_valid, tests/integration/test_phase0_quality_seal.py::test_seal_readonly_blocks_rewrite, tests/integration/test_phase0_quality_seal.py::test_unrecoverable_coverage_guard_blocked
- Scope: tracks/executor/phase0.py, tracks/executor/phase0_quality.py, tracks/executor/phase0_seal.py
- Depends on: T-001, T-004, T-006
- Batch: 3
- Parallel: True

## T-006
- Issue: #92
- Description: 在已交付 IF-GUARD-001 registry 基线上实现 IF-GUARD-002 parity/deployment；以合并 scope 完成 guard_registry 公共出口适配、三处 mismatch 与 --exit-zero fail-closed，并重验 IF-GUARD-001/002 完整守卫切片。
- AC refs: AC-FR0257-02, AC-FR0258-01, AC-FR0258-02, AC-FR0258-03, AC-FR0258-04, AC-FR0259-01, AC-FR0259-02, AC-NFR0141-01
- FR refs: FR-0257, FR-0258, FR-0259, NFR-0141
- IF ids: IF-GUARD-002
- Unit refs: -
- Acceptance refs: tests/integration/test_phase0_quality_seal.py::test_guard_hardened_violations_zero_revisions_audited, tests/integration/test_guard_registry.py::test_registry_single_source_eight_categories, tests/integration/test_guard_registry.py::test_registry_migration_no_silent_gap, tests/integration/test_guard_registry.py::test_prism_revise_routes_back_to_archer, tests/integration/test_guard_parity.py::test_parity_mismatch_blocks_fail_closed, tests/integration/test_guard_parity.py::test_deploy_mechanism_generates_host_configs, tests/integration/test_guard_parity.py::test_registry_real_execution_evidence, tests/integration/test_guard_parity.py::test_parity_event_programmatic_no_agent_selfreport, tests/integration/test_commit_hook_hard_gate.py::test_phase0_hard_gate_rejects_violation_no_exit_zero
- Scope: tracks/executor/guard_registry.py, tracks/executor/guard_parity.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-009
- Issue: #94
- Description: 实现 IF-AUTH-001 new-behaviour authenticity、legal Red/irrelevant failure 隔离，并冻结 judge_authenticity façade；existing 分支仅委托 authenticity_existing，后继不得改 façade。
- AC refs: AC-FR0260-01, AC-FR0260-02, AC-FR0260-03, AC-FR0260-04, AC-FR0260-05
- FR refs: FR-0260
- IF ids: IF-AUTH-001
- Unit refs: -
- Acceptance refs: tests/integration/test_authenticity_gate.py::test_new_behaviour_legal_red_on_frozen_baseline, tests/integration/test_authenticity_gate.py::test_unrelated_downstream_failure_isolated, tests/integration/test_authenticity_gate.py::test_phase0_gate_blocks_mtest_before_seal, tests/integration/test_authenticity_gate.py::test_d41_selection_semantics_coexist, tests/integration/test_authenticity_gate.py::test_broad_mutation_irrelevant_red_rejected
- Scope: tracks/executor/authenticity.py
- Depends on: T-001
- Batch: 2
- Parallel: True

## T-010
- Issue: #96
- Description: 交付 IF-MUTATION-001 的 executor package composition 出口，使 mutation_manifest helper 可稳定发现；最小字段、AC/IF identity、target/control 与禁止 tests scope 的既有行为由后继 T-011 在 mutation public façade 原子边界内迁移并接线。
- AC refs: AC-FR0262-01, AC-FR0262-02, AC-FR0262-03, AC-NFR0140-02
- FR refs: FR-0262, NFR-0140
- IF ids: IF-MUTATION-001
- Unit refs: -
- Acceptance refs: tests/integration/test_mutation_manifest.py::test_manifest_minimal_field_set_language_neutral, tests/integration/test_mutation_manifest.py::test_no_batch_endorsement_noop_patch, tests/integration/test_mutation_manifest.py::test_tests_scope_mutation_blocked, tests/integration/test_v07_events.py::test_identity_five_part_binding
- Scope: tracks/executor/__init__.py
- Depends on: T-009
- Batch: 3
- Parallel: True

## T-011
- Issue: #97
- Description: 交付 IF-MUTATION-002 isolated experiment foundation、append-only WAL/replay 与缺失结果重跑的 public façade：原子拥有 mutation.py 与 mutation_manifest.py，接通既有 mutation_experiment foundation；AC-FR0261 的 tests_in_scope authenticity enforcement 由后继 T-012 拥有。
- AC refs: AC-FR0263-01, AC-FR0263-02, AC-FR0263-03, AC-FR0263-04, AC-NFR0140-03
- FR refs: FR-0263, NFR-0140
- IF ids: IF-MUTATION-002
- Unit refs: -
- Acceptance refs: tests/integration/test_mutation_experiment.py::test_experiment_chain_target_kill_controls_green, tests/integration/test_mutation_experiment.py::test_events_append_only_replayable, tests/integration/test_mutation_experiment.py::test_crash_recovery_reruns_no_phantom_pass, tests/integration/test_mutation_experiment.py::test_failure_matrix_fail_closed, tests/integration/test_v07_events.py::test_wal_replay_reruns_missing_never_pass
- Scope: tracks/executor/mutation.py, tracks/executor/mutation_manifest.py
- Depends on: T-002, T-010
- Batch: 4
- Parallel: True

## T-012
- Issue: #95
- Description: verification-only 验收闭口（r12 FULL-ledger replan）：T-012 已在 tracks/executor/mutation_experiment.py 交付 AC-FR0261-03 的 tests-in-scope role separation，冻结锚点 test_frozen_tests_untouchable_role_separation 当前实绿，Runtime 直接重验。existing-green/AC-specific counterexample 判定及 AC-NFR0140-04 identity-drift 实红改由新 T-017 原子拥有 tracks/executor/authenticity_existing.py 后实施；本任务不得再拥有或修改该文件，避免历史已完成任务吞掉 FULL 新发现的实现缺口。
- AC refs: AC-FR0261-03
- FR refs: FR-0261
- IF ids: IF-AUTH-002, IF-MUTATION-002
- Unit refs: -
- Acceptance refs: tests/integration/test_counterexample_kill.py::test_frozen_tests_untouchable_role_separation
- Scope: tracks/executor/mutation_experiment.py
- Depends on: T-005, T-009, T-011
- Batch: 5
- Parallel: True

## T-013
- Issue: #99
- Description: 实现 IF-CLOSURE-001 candidate-bound 闭环出口切片，原子拥有公共入口接线与行为实现（#77 facade 判据，修复 plan_defect：原 scope 仅 trace.py 而 4 锚点全挂 cli/main.py 出口）：cli/main.py 的 check-trace v0.7 分派与 closure JSON 渲染（v0.4/v0.6 classic 输出逐字节不变，版本隔离）；executor/version_extensions.py 通用 version capability seam（注册/解析/未知或缺 callback 即阻断，不含 v0.7 业务结论）；executor/v07_runtime.py 唯一 v0.7 extension 装配（trace callback 组装 per-AC evidence 调用 checks.trace 纯 join、status/replay/report 渲染、island_gate_2 callback 惰性派发 tracks.executor.failclosed.demonstrate_failclosed——该函数由 T-016 落地，本任务只交付调用合同）；checks/trace.py 纯 join（已交付基线）与 report.py 闭环证据链展示。replan（test_freeze_contamination）：unit RED 义务显式点名——tests/unit/test_trace_closure_candidate.py 整文件（check_closure_candidate 纯函数 13 节点）进入本任务 unit_refs/R manifest，其 @v0.7 marker 规范化与后续一切修改必须发生在本任务 RGR lineage 内，M-TEST freeze 不再出现不可归属的 tracked-dirty unit 文件。数据前提：锚点 test_closure_candidate_bound_pass 的 status=pass 与 test_candidate_digest_consistency 的非空 records 依赖 Shield 侧 host_repo v0.7 seed（approved AC + baseline/mutation/FULL 同 candidate 证据链，test-plan §2.4）；仅因缺 seed 数据的 GREEN 失败按 test_defect 归 Shield（SHIELD_FIX），不属实现缺陷。
- AC refs: AC-FR0265-01, AC-FR0265-02, AC-FR0265-03
- FR refs: FR-0265
- IF ids: IF-CLOSURE-001
- Unit refs: tests/unit/test_trace_closure_candidate.py
- Acceptance refs: tests/integration/test_trace_closure.py::test_closure_candidate_bound_pass, tests/integration/test_trace_closure.py::test_blocking_conditions_hard_errors, tests/integration/test_trace_closure.py::test_candidate_digest_consistency, tests/integration/test_check_trace.py::test_v06_trace_output_version_isolated_no_closure_leak
- Scope: tracks/checks/trace.py, tracks/cli/main.py, tracks/executor/version_extensions.py, tracks/executor/v07_runtime.py, tracks/report.py
- Depends on: T-009, T-010, T-011, T-012
- Batch: 6
- Parallel: True

## T-014
- Issue: #100
- Description: verification-only 验收闭口（r10 plan_defect replan）：T-014 已在 tracks/executor/demo_host.py 交付 IF-ADAPTER-003 去令牌化——adapter id 来自宿主 [adapter] 合同、展示名语言中立；tests/integration/test_demo_host.py 的真实安装路径与 path-equivalence 两锚点实测为绿，Runtime 直接重验，无 RED-implementation。AC-FR0264-04/AC-NFR0141-02 与 IF-ADAPTER-003 provenance 仍由本任务覆盖；但其最终语言中立 §8 锚点改归 T-016：后序 GREEN 4842683 越界在 executor.py:3911 引入 reference-pytest fallback，且当前图已把 executor.py 原子归属 T-016 用于 run-loop/failclosed 接线。按 #77，最终修改该公共运行路径并使语言锚点转绿的任务必须拥有文件与锚点；本任务不得扩大到 executor.py 或与 T-016 重叠。
- AC refs: AC-FR0264-04, AC-NFR0141-02
- FR refs: FR-0264, NFR-0141
- IF ids: IF-ADAPTER-003
- Unit refs: -
- Acceptance refs: tests/integration/test_demo_host.py::test_demo_created_via_real_install_path, tests/integration/test_demo_host.py::test_path_equivalence_evidence
- Scope: tracks/executor/demo_host.py
- Depends on: T-001, T-002, T-006
- Batch: 3
- Parallel: True

## T-015
- Issue: #101
- Description: verification-only 验收闭口（plan_defect replan）：v0.7 引擎命令修复（framework_runner phantom 改合同来源）与 before_mtest capability seam 接入已由前代 GREEN commits 交付且锚点 test_append_only_and_projection_replay 已绿；probe 证实 island_gate_2 无生产调用点（仅 machine.py:2019 接通 before_mtest），该 run-loop 派发接线按 #77 文件所有权原子边界移交 T-016（行为 demonstrate_failclosed 与公共入口 _do_check_island_2/executor 组装同任务），executor.py/m_impl_runtime.py 移出本 task scope。r10 replan 再让渡 tracks/kernel/state.py（phase0 coverage/guard_hardened/sealed 纯投影，machine.py 事件路由已消费）给 T-016——本 task 为 verification-only 无代码提交通道，投影文件必须由实施任务原子拥有并随其 lineage 提交。本 task 保留 kernel/executor 组装文件所属权与 AC-NFR0140-01/04 覆盖闭合，验收由 Runtime 直接执行冻结 integration 锚点（append-only 事件 + 投影重建），无 RED-implementation，不走 RGR。
- AC refs: AC-NFR0140-01, AC-NFR0140-04
- FR refs: NFR-0140
- IF ids: IF-AUTH-001, IF-MUTATION-002
- Unit refs: -
- Acceptance refs: tests/integration/test_v07_events.py::test_append_only_and_projection_replay
- Scope: tracks/executor/helpers.py, tracks/executor/validate.py, tracks/kernel/contracts.py, tracks/kernel/machine.py, tracks/kernel/events.py
- Depends on: T-005, T-006, T-009, T-010, T-011, T-012, T-013
- Batch: 7
- Parallel: False

## T-016
- Issue: #100
- Description: verification-only 验收闭口（r19 plan_defect replan，PRISM-016-R18-01，T-017/R14 同型 déjà-livré 边界）：IF-FULLCHAIN-001/IF-LEDGER-001 陈旧身份结算发射方已由本任务 GREEN 3242986 落地——settle_stale_identities（test_select.py:227 纯结算规划器）与 _settle_stale_island_2_open（m_impl_runtime.py:1450 island_2 OPEN 分支真实接线，full_settlement 轮后 OPEN→CLASSIFIED→FIXED→既有 fallback 全量 PROVEN）；RED 重钉 739e62f5 修复正确但对当前树 11/11 全绿，实现已在树、无合法 Red。Runtime 直接重验 7 个冻结 integration 锚点后 task.completed，无 RGR、无再次 RED 重钉；此后 island_2 经已落地结算路径收敛 200 条陈旧 OPEN 身份。前代 failclosed 切片（IF-FAILCLOSED-001 双宿主九场景/crash-recovery、island_gate_2 接线、SM-01.3 resume、state 投影、reach 分类）为已交付 provenance，六锚点为绿色回归锚，禁止重实现或恢复 reference-pytest fallback。scope 保留 failclosed/executor/m_impl_runtime/test_select/state/reach 最终文件 ownership/provenance（R02 显式 owner 要求）；unit_refs 保持为空——test_t016_stale_settlement_red.py 与 test_t017_candidate_identity_red.py 均为已落地 regression 资产。AC-FR0265-01/FR-0266/NFR-0142 与 IF-FAILCLOSED-001/IF-FULLCHAIN-001/IF-LEDGER-001 closure 承前（r17 closure 编辑不变）。
- AC refs: AC-FR0265-01, AC-FR0266-01, AC-FR0266-02, AC-FR0266-03, AC-FR0266-04, AC-NFR0142-01, AC-NFR0142-02
- FR refs: FR-0265, FR-0266, NFR-0142
- IF ids: IF-FAILCLOSED-001, IF-FULLCHAIN-001, IF-LEDGER-001
- Unit refs: -
- Acceptance refs: tests/integration/test_trace_closure.py::test_closure_candidate_bound_pass, tests/integration/test_failclosed_scenarios.py::test_tracks_host_nine_scenarios_blocked, tests/integration/test_failclosed_scenarios.py::test_crash_recovery_replay_ok, tests/integration/test_failclosed_scenarios.py::test_any_leak_or_inequivalence_blocks, tests/integration/test_failclosed_scenarios.py::test_demo_host_crash_recovery_rebuild, tests/integration/test_kernel_language_neutrality.py::test_no_language_tokens_kernel_executor_cli, tests/integration/test_kernel_language_neutrality.py::test_language_invariant_dual_check
- Scope: tracks/executor/failclosed.py, tracks/executor/executor.py, tracks/executor/m_impl_runtime.py, tracks/executor/test_select.py, tracks/kernel/state.py, tracks/checks/reach.py
- Depends on: T-006, T-011, T-012, T-013, T-014, T-015
- Batch: 8
- Parallel: False

## T-017
- Issue: #95
- Description: verification-only 验收闭口（r15 plan_defect replan，PRISM-017-R14-01）：candidate-identity fail-closed 修复已由前代 lineage GREEN e9253c4 落地——tracks/executor/authenticity_existing.py:109-134 对 caller-supplied、无可信 experiment binding 的显式 candidate_digest fail-closed；8 个 RED 单测（tests/unit/test_t017_candidate_identity_red.py）与 3 个冻结 integration 锚点实测全绿，标准 RGR 无合法 RED。Runtime 直接重验 3 锚点后 task.completed，无 RGR、无新 SHIELD_FIX。scope 保留 tracks/executor/authenticity_existing.py 最终文件 ownership/provenance，禁止修改 tracks/executor/authenticity.py 冻结 façade；unit_refs 保持为空——t017 单测已是落地 regression 资产，不再冒充本代待写 RED 义务。AC-FR0261-01/02 与 IF-AUTH-002/IF-MUTATION-002 closure 承前。
- AC refs: AC-FR0261-01, AC-FR0261-02
- FR refs: FR-0261, NFR-0140
- IF ids: IF-AUTH-002, IF-MUTATION-002
- Unit refs: -
- Acceptance refs: tests/integration/test_counterexample_kill.py::test_existing_green_requires_kill_verified, tests/integration/test_counterexample_kill.py::test_ac_specific_binding_and_minimality_reviewed, tests/integration/test_v07_events.py::test_unknown_missing_drift_control_fail_closed
- Scope: tracks/executor/authenticity_existing.py
- Depends on: T-012, T-016
- Batch: 9
- Parallel: False
