# Task Graph

## T-001
- Issue: #44
- Description: 【verification-only §1.0.3】验证 M-IMPL 阶段注册与子状态机驱动（FR-0010，IF-IMPL-001/IF-MTEST-001）已由既有实现满足：运行既有 unit+integration 子集与 GREEN_GATE 标准检查集，无 RED-implementation。
- AC refs: AC-FR0010-01, AC-FR0010-02, AC-FR0010-03, AC-FR0010-04, AC-FR0010-05
- FR refs: FR-0010
- IF ids: IF-IMPL-001, IF-MTEST-001
- Test refs: tests/integration/test_m_impl_cycle.py, tests/unit/test_machine_m_impl.py
- Scope: tracks/kernel/m_impl.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-002
- Issue: #45
- Description: 【verification-only §1.0.3】验证 BASELINE 重算与冻结（FR-0020）、PLANNING task graph 拆分（FR-0030）、tasks.json schema/校验（FR-0180）、Issues 消费（FR-0220）的既有实现：运行既有测试子集与质量门禁。
- AC refs: AC-FR0020-01, AC-FR0020-02, AC-FR0020-03, AC-FR0020-04, AC-FR0030-01, AC-FR0030-02, AC-FR0030-03, AC-FR0180-01, AC-FR0180-02, AC-FR0180-04, AC-FR0180-05, AC-FR0220-01, AC-FR0220-02, AC-FR0220-03, AC-FR0220-04
- FR refs: FR-0020, FR-0030, FR-0180, FR-0220
- IF ids: IF-IMPL-002
- Test refs: tests/integration/test_baseline_recalc.py, tests/integration/test_taskgraph_validate.py, tests/integration/test_tasksjson_validate.py, tests/integration/test_issue_consumption.py
- Scope: tracks/executor/taskgraph.py, tracks/baseline.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-003
- Issue: #47
- Description: 【verification-only §1.0.3】验证 ISLAND_GATE_1 程序复核（FR-0040）、PRISM_PLAN 判据包绑定（FR-0050）、TASK_DISPATCH DAG 调度与 manifest（FR-0060）的既有实现。
- AC refs: AC-FR0040-01, AC-FR0040-02, AC-FR0050-01, AC-FR0050-02, AC-FR0050-03, AC-FR0050-04, AC-FR0060-01, AC-FR0060-02, AC-FR0060-03
- FR refs: FR-0040, FR-0050, FR-0060
- IF ids: IF-IMPL-001, IF-IMPL-002
- Test refs: tests/integration/test_planning_dispatch.py, tests/integration/test_devon_dispatch.py
- Scope: tracks/executor/m_impl_runtime.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-004
- Issue: #50
- Description: 【verification-only §1.0.3】验证 RED 隔离与私有 R ref（FR-0070）、RED_GATE 合法 Red 分类（FR-0080）、PRISM_RED checkpoint 评审（FR-0090）、GREEN 最小实现（FR-0100）、GREEN_COMMIT lineage（FR-0120）的既有实现。
- AC refs: AC-FR0070-01, AC-FR0070-02, AC-FR0070-03, AC-FR0070-04, AC-FR0070-05, AC-FR0080-01, AC-FR0080-02, AC-FR0090-01, AC-FR0090-02, AC-FR0100-01, AC-FR0100-02, AC-FR0120-01, AC-FR0120-02
- FR refs: FR-0070, FR-0080, FR-0090, FR-0100, FR-0120
- IF ids: IF-IMPL-002
- Test refs: tests/integration/test_rgr_contract.py, tests/integration/test_worktree_contract.py, tests/integration/test_red_check.py
- Scope: tracks/executor/worktree.py, tracks/executor/rgr.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-005
- Issue: #54
- Description: 【verification-only §1.0.3】验证 GREEN_GATE 粒度门禁与反馈脱敏（FR-0110）、REFACTOR 质量门禁分层（FR-0130）的既有实现。
- AC refs: AC-FR0110-01, AC-FR0110-02, AC-FR0110-03, AC-FR0110-04, AC-FR0130-01, AC-FR0130-02, AC-FR0130-03
- FR refs: FR-0110, FR-0130
- IF ids: IF-IMPL-001, IF-IMPL-002, IF-IMPL-005
- Test refs: tests/integration/test_execution_gates.py, tests/integration/test_quality_gate_contract.py
- Scope: tracks/executor/quality_gate.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-006
- Issue: #57
- Description: 【verification-only §1.0.3】验证 TASK_REVIEW 与 PRISM_FINAL（FR-0140）、DIAGNOSE 四路诊断与 SHIELD_FIX（FR-0150）的既有实现；验收锚点为既有 gate/diagnose 测试。
- AC refs: AC-FR0140-01, AC-FR0140-02, AC-FR0140-03, AC-FR0150-01, AC-FR0150-02, AC-FR0150-03, AC-FR0150-04
- FR refs: FR-0140, FR-0150
- IF ids: IF-IMPL-001, IF-IMPL-002, IF-IMPL-007
- Test refs: tests/integration/test_execution_gates.py, tests/integration/test_diagnose.py
- Scope: tests/unit/test_verify_review_diagnose.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-007
- Issue: #59
- Description: 【verification-only §1.0.3】验证 ISLAND_GATE_2 出口门禁（FR-0160，IF-REACH-002 出口）与 kernel 纯函数边界（NFR-0010）的既有实现。
- AC refs: AC-FR0160-01, AC-FR0160-02, AC-FR0160-03, AC-FR0160-04, AC-NFR0010-01, AC-NFR0010-02
- FR refs: FR-0160, NFR-0010
- IF ids: IF-IMPL-001, IF-IMPL-002
- Test refs: tests/integration/test_island_gate_2.py, tests/integration/test_check_reach.py
- Scope: tests/unit/test_verify_island2_boundary.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-008
- Issue: #60
- Description: 【verification-only §1.0.3】验证 Devon opencode agent 接入与 manifest 越界审计（FR-0170，IF-DEVON-001）的既有实现。
- AC refs: AC-FR0170-01, AC-FR0170-02, AC-FR0170-03
- FR refs: FR-0170
- IF ids: IF-DEVON-001
- Test refs: tests/integration/test_opencode_manifest.py, tests/integration/test_devon_dispatch.py
- Scope: tracks/deliverables.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-009
- Issue: #62
- Description: 【verification-only §1.0.3】验证 dispatch 物化完整性合同（FR-0190：assignment 字段全集、增量物化、result identity）的既有实现。
- AC refs: AC-FR0190-01, AC-FR0190-02, AC-FR0190-03, AC-FR0190-04, AC-FR0190-05, AC-FR0190-06, AC-FR0190-07, AC-FR0190-08, AC-FR0190-09, AC-FR0190-10
- FR refs: FR-0190
- IF ids: IF-IMPL-001, IF-IMPL-002, IF-VALIDATE-001
- Test refs: tests/integration/test_dispatch_materialization.py, tests/integration/test_result_checkpoint_cli.py
- Scope: tracks/project.py, tracks/executor/result_checkpoint.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-010
- Issue: #63
- Description: 【verification-only §1.0.3】验证可休眠与崩溃恢复（FR-0200）的既有实现：phase 边界事件、lineage+事件回放恢复。
- AC refs: AC-FR0200-01, AC-FR0200-02
- FR refs: FR-0200
- IF ids: IF-IMPL-002, IF-IMPL-004
- Test refs: tests/integration/test_crash_recovery.py, tests/integration/test_rc_crash_digest.py
- Scope: tests/unit/test_verify_crash_recovery.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-011
- Issue: #64
- Description: 【verification-only §1.0.3】验证 Shield test-plan 测试归属与黑盒边界（FR-0210，IF-SHIELD-001 出口）的既有实现。
- AC refs: AC-FR0210-01, AC-FR0210-02, AC-FR0210-03, AC-FR0210-04, AC-FR0210-05, AC-FR0210-06
- FR refs: FR-0210
- IF ids: IF-IMPL-002, IF-SHIELD-001
- Test refs: tests/integration/test_testplan_ownership.py, tests/integration/test_shield_dispatch.py
- Scope: tracks/executor/validate.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-012
- Issue: #71
- Description: 【verification-only §1.0.3】验证 append-only 事件溯源（NFR-0020）与 dispatch 活动性可观测（NFR-0030，含 trac retry）的既有实现。
- AC refs: AC-NFR0020-01, AC-NFR0020-02, AC-NFR0030-01, AC-NFR0030-02, AC-NFR0030-03
- FR refs: NFR-0020, NFR-0030
- IF ids: IF-IMPL-001, IF-IMPL-002
- Test refs: tests/integration/test_trac_retry.py, tests/integration/test_store.py
- Scope: tests/unit/test_verify_nfr_observability.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 2h

## T-013
- Issue: #66
- Description: 【标准 RGR】实现真实 OpencodeBackend opt-in live 旅程（FR-0230）与 live 审计证据绑定/真实性标记（FR-0231，IF-LIVE-001）：tracks/executor/live_evidence.py 从 stub 到实现；RED 锚点为 e2e_live 与 release_evidence 既有失败测试。
- AC refs: AC-FR0230-01, AC-FR0230-02, AC-FR0230-03, AC-FR0231-01, AC-FR0231-02
- FR refs: FR-0230, FR-0231
- IF ids: IF-LIVE-001, IF-RELEASE-001
- Test refs: tests/e2e_live/test_m_impl_release_evidence.py, tests/integration/test_release_evidence.py
- Scope: tracks/executor/live_evidence.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 3h

## T-014
- Issue: #74
- Description: 【标准 RGR】实现设计评论优先检查与等待可见（FR-0234-01/02/03）、Prism 裁定路由（FR-0235-01/02）、quarantine 隔离/恢复/过期（FR-0236）与 doc-gap 事件持久化投影（NFR-0090-01/03，IF-DOCGAP-001/IF-QUARANTINE-001）：doc_comment.py/machine.py/store.py 从 stub 到实现。
- AC refs: AC-FR0234-01, AC-FR0234-02, AC-FR0234-03, AC-FR0235-01, AC-FR0235-02, AC-FR0236-01, AC-FR0236-02, AC-FR0236-03, AC-FR0236-04, AC-NFR0090-01, AC-NFR0090-03
- FR refs: FR-0234, FR-0235, FR-0236, NFR-0090
- IF ids: IF-DOCGAP-001, IF-QUARANTINE-001
- Test refs: tests/integration/test_doc_comment_first.py, tests/integration/test_doc_comment_quarantine.py, tests/e2e/test_doc_comment_journey.py
- Scope: tracks/executor/doc_comment.py, tracks/kernel/machine.py, tracks/store/store.py
- Depends on: -
- Batch: 1
- Parallel: True
- Budget: 3h

## T-015
- Issue: #68
- Description: 【标准 RGR】实现 `trac check release-evidence` 当前 live 证据发布前置检查（FR-0232）与确定性/可审计性（NFR-0080，IF-RELEASE-001）：checks/release_evidence.py 从 stub 到实现 + cli/main.py 子命令接线（含 NFR-0030 status 可观测既有合同）。
- AC refs: AC-FR0232-01, AC-FR0232-02, AC-FR0232-03, AC-FR0232-04, AC-FR0232-05, AC-NFR0080-01, AC-NFR0080-02
- FR refs: FR-0232, NFR-0080
- IF ids: IF-RELEASE-001
- Test refs: tests/integration/test_release_evidence.py, tests/e2e_live/test_m_impl_release_evidence.py
- Scope: tracks/checks/release_evidence.py, tracks/cli/main.py
- Depends on: T-013
- Batch: 2
- Parallel: True
- Budget: 3h

## T-016
- Issue: #75
- Description: 【标准 RGR】实现 executor 侧 doc-gap 接线：outcome 前置评论检查（FR-0234-04）、闭环后恢复/重派发（FR-0235-03）、非法正文编辑的失败重试（FR-0237-02）、fail-closed 守卫（NFR-0090-02）。
- AC refs: AC-FR0234-04, AC-FR0235-03, AC-FR0237-02, AC-NFR0090-02
- FR refs: FR-0234, FR-0235, FR-0237, NFR-0090
- IF ids: IF-DOCGAP-001, IF-QUARANTINE-001
- Test refs: tests/integration/test_doc_comment_first.py, tests/integration/test_doc_comment_quarantine.py, tests/e2e/test_doc_comment_journey.py
- Scope: tracks/executor/executor.py
- Depends on: T-014
- Batch: 2
- Parallel: True
- Budget: 3h

## T-017
- Issue: #69
- Description: 【标准 RGR】补全无凭据例行 CI 的 opt-in 例外（FR-0233）：完成 .github/workflows/ci.yml 骨架中标注 TODO(devon) 的 live-opencode 与 release-evidence job（AC-FR0233-01 的 conftest 凭据探针已实现，经 e2e_live 通道验证）。
- AC refs: AC-FR0233-01, AC-FR0233-02, AC-FR0233-03
- FR refs: FR-0233
- IF ids: IF-LIVE-001, IF-RELEASE-001
- Test refs: tests/e2e_live/test_m_impl_release_evidence.py
- Scope: .github/workflows/ci.yml
- Depends on: T-013, T-015
- Batch: 3
- Parallel: True
- Budget: 3h

## T-018
- Issue: #77
- Description: 【标准 RGR】实现非法正文编辑的整回合原子拒绝审计（FR-0237-01/03，IF-DOCGAP-001/IF-QUARANTINE-001）：effects/opencode.py post-write 审计回滚 + report.py 拒绝事件渲染。
- AC refs: AC-FR0237-01, AC-FR0237-03
- FR refs: FR-0237
- IF ids: IF-DOCGAP-001, IF-QUARANTINE-001
- Test refs: tests/integration/test_doc_comment_first.py, tests/e2e/test_doc_comment_journey.py
- Scope: tracks/effects/opencode.py, tracks/report.py
- Depends on: T-016
- Batch: 3
- Parallel: True
- Budget: 3h
