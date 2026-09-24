# Task Graph

## T-006
- Issue: #153
- Description: verification-only 验收闭口（IF-QUERY-001）：projections.py 全部投影已交付在树（overview/detail/timeline/ac_chain/todos/config/executions/文档 revision+diff），8 条集成锚点实测全绿，无剩余实现面。验收=Runtime 直跑 acceptance_refs；已绿锚无合法 RED，不走 Devon RGR。
- AC refs: AC-FR0302-01, AC-FR0302-02, AC-FR0303-01, AC-FR0303-02, AC-FR0304-01, AC-FR0304-02, AC-FR0307-01, AC-FR0307-02
- FR refs: FR-0302, FR-0303, FR-0304, FR-0307
- IF ids: IF-QUERY-001
- Unit refs: -
- Acceptance refs: tests/integration/test_progress_projection.py::test_dual_counts_presented, tests/integration/test_progress_projection.py::test_progress_matches_real_counts, tests/integration/test_execution_state.py::test_role_task_states_with_log_refs, tests/integration/test_execution_state.py::test_no_agent_session_content_anywhere, tests/integration/test_ac_evidence_view.py::test_ac_chain_complete, tests/integration/test_ac_evidence_view.py::test_missing_stale_unreviewed_marked, tests/integration/test_todo_center.py::test_legit_decisions_listed_with_context, tests/integration/test_todo_center.py::test_waits_never_in_todos
- Scope: tracks/server/projections.py
- Depends on: -
- Batch: 1
- Parallel: False

## T-001
- Issue: #147
- Description: verification-only 验收闭口（IF-CMDSVC-001）：service.py 命令服务交付面已在树内（八 kind 受理/幂等去重/前检/澄清/pause 两段），7 条集成锚点实测全绿。db.py 归属已移交 T-003（supervisor 存储面集中后封闭）；验收=Runtime 直跑 acceptance_refs，已绿锚无合法 RED。
- AC refs: AC-FR0291-01, AC-FR0291-02, AC-FR0292-01, AC-FR0292-02, AC-FR0293-01, AC-FR0293-02, AC-FR0310-01, AC-FR0310-02, AC-NFR0150-01
- FR refs: FR-0291, FR-0292, FR-0293, FR-0310, NFR-0150
- IF ids: IF-CMDSVC-001
- Unit refs: -
- Acceptance refs: tests/integration/test_run_create_web.py::test_create_feature_run_returns_ids, tests/integration/test_run_create_web.py::test_failed_and_duplicate_submit_no_fake_run, tests/integration/test_clarify_web.py::test_reply_continues_same_run, tests/integration/test_clarify_web.py::test_discussion_history_traceable, tests/integration/test_pause_resume.py::test_pause_two_phase_and_priority, tests/integration/test_pause_resume.py::test_resume_from_legal_position, tests/integration/test_perf_budget.py::test_accept_returns_after_persist
- Scope: tracks/supervisor/service.py
- Depends on: T-006, T-003
- Batch: 3
- Parallel: False

## T-002
- Issue: #142
- Description: verification-only 验收闭口（IF-PROJ-001）：readiness.py 四类封闭探针交付面已在树内，3 条集成锚点实测全绿；0289-02 注册已捐赠 T-021（越范围拒绝 owner=service.py 已封闭）。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0289-01, AC-FR0290-01, AC-FR0290-02
- FR refs: FR-0289, FR-0290
- IF ids: IF-PROJ-001
- Unit refs: -
- Acceptance refs: tests/integration/test_project_registry.py::test_register_shows_attribution_and_survives_restart, tests/integration/test_readiness.py::test_readiness_items_displayed, tests/integration/test_readiness.py::test_not_ready_blocks_run_creation
- Scope: tracks/supervisor/readiness.py
- Depends on: T-001
- Batch: 3
- Parallel: True

## T-003
- Issue: #150
- Description: 等待/quota/退避+supervisor 存储面（IF-WAIT-001）：waiting.py classify_wait 五类、enter/resolve_wait 持久化、next_probe 60s→900s 不热循环（交付面已在树）；本任务 RED 义务=db.py ServiceDB 存储面补齐——waits 行读写面、leases 读写面、schedule 单行写面（HEAD 缺失者即合法 RED；T-007/T-009/T-015 消费，交付后 db.py 随本任务封闭）。锚点=quota 两件+退避口径。
- AC refs: AC-FR0299-01, AC-FR0299-02, AC-NFR0152-01
- FR refs: FR-0299, NFR-0152
- IF ids: IF-WAIT-001
- Unit refs: -
- Acceptance refs: tests/integration/test_quota_wait.py::test_known_reset_auto_resume, tests/integration/test_quota_wait.py::test_unknown_reset_probe_plan_no_countdown, tests/integration/test_backoff_policy.py::test_backoff_growth_bounded_and_config_readable
- Scope: tracks/supervisor/waiting.py, tracks/supervisor/db.py
- Depends on: T-006
- Batch: 2
- Parallel: False

## T-004
- Issue: #166
- Description: verification-only 验收闭口（IF-WEBAUTH-001/IF-CMDGUARD-001）：auth/guard/discuss 裁决权属交付面已在树内（scrypt 会话/CSRF、封闭 kind/schema、actor_class 强制、realpath 防护），2 条集成锚点实测全绿；0314-01 需 app 装配面、0314-04 裁决入口 owner=service.py（T-001 已封闭）——两者 deferred 至 T-INT 收口。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0314-02, AC-FR0314-03
- FR refs: FR-0314
- IF ids: IF-WEBAUTH-001, IF-CMDGUARD-001
- Unit refs: -
- Acceptance refs: tests/integration/test_web_auth.py::test_non_human_decision_rejected_audited, tests/integration/test_web_auth.py::test_shell_payload_blocked
- Scope: tracks/server/auth.py, tracks/server/guard.py, tracks/discuss/gate.py, tracks/discuss/cli.py
- Depends on: T-001
- Batch: 4
- Parallel: True

## T-005
- Issue: #167
- Description: verification-only 验收闭口（IF-SECRECY-001）：redaction.py 交付面已在树内（SecretRedactor 全出口脱敏、受控引用），越权读取锚点实测全绿；0315-01 全出口扫描镜像归 T-021（secrecy 文件）。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0315-01, AC-FR0315-02
- FR refs: FR-0315
- IF ids: IF-SECRECY-001
- Unit refs: -
- Acceptance refs: tests/integration/test_secrecy.py::test_out_of_scope_read_denied
- Scope: tracks/server/redaction.py
- Depends on: T-004
- Batch: 5
- Parallel: True

## T-007
- Issue: #148
- Description: verification-only 验收闭口（IF-LEASE-001）：lease.py 交付面已在树内（事务内单调代次、完成 CAS、quarantine_late_result），3 条集成锚点实测全绿；0296-02/0312-02 的 worker.late_result 发射面冻结测试直调 db.complete_command（owner=db.py，T-003 已封闭）——deferred 至 T-INT 收口。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0312-01, AC-FR0312-02, AC-FR0312-03
- FR refs: FR-0296, FR-0312
- IF ids: IF-LEASE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_web_rollback.py::test_return_with_impact_preview, tests/integration/test_web_rollback.py::test_pushed_release_survives_rollback
- Scope: tracks/supervisor/lease.py
- Depends on: T-001, T-003
- Batch: 4
- Parallel: True

## T-015
- Issue: #148
- Description: verification-only 验收闭口（IF-SCHED-001）：scheduler.py 交付面已在树内（schedule 单行表单+有序队列+pause 抑制），second_run 锚点实测全绿；0296-04 注册捐赠 T-019（换队 seam owner=db/service 已封闭）。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0296-03
- FR refs: FR-0296
- IF ids: IF-SCHED-001
- Unit refs: -
- Acceptance refs: tests/integration/test_hotfix_swap.py::test_second_run_queued
- Scope: tracks/supervisor/scheduler.py
- Depends on: T-001, T-003
- Batch: 4
- Parallel: True

## T-008
- Issue: #149
- Description: 驱动引擎（IF-DRIVE-001）：drive.py drive_once 单步五态（CLI cmd_run 行为不变）+ worker 子进程认领/驱动/pause 边界生效。锚点=自动推进+人工门停住+abandon 三件（含不可恢复终态；db/service 经 deps 覆盖）。
- AC refs: AC-FR0297-01, AC-FR0297-02, AC-FR0313-01, AC-FR0313-02, AC-FR0313-03
- FR refs: FR-0297, FR-0313
- IF ids: IF-DRIVE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_auto_drive.py::test_auto_advance_stages, tests/integration/test_auto_drive.py::test_human_gate_blocks_advance, tests/integration/test_abandon_web.py::test_abandon_terminal_auditable, tests/integration/test_abandon_web.py::test_abandon_no_fake_success, tests/integration/test_abandon_web.py::test_unrecoverable_failure_terminal
- Scope: tracks/supervisor/worker.py, tracks/supervisor/worker_main.py, tracks/executor/drive.py
- Depends on: T-001, T-003
- Batch: 4
- Parallel: True

## T-009
- Issue: #152
- Description: 崩溃重启恢复（IF-RECOVER-001）：recover_on_startup——claimed 命令 requeue、waits 的 retry_at 保留、租约过期授新代次、未完成 effects 衔接既有 WAL/幂等 reconcile。
- AC refs: AC-FR0298-01, AC-FR0298-02, AC-FR0300-01, AC-FR0300-02
- FR refs: FR-0298, FR-0300
- IF ids: IF-RECOVER-001
- Unit refs: -
- Acceptance refs: tests/integration/test_wait_persistence.py::test_wait_survives_restart_and_auto_resumes, tests/integration/test_wait_persistence.py::test_no_hot_loop_bounded_probing, tests/integration/test_restart_recovery.py::test_worker_kill_reclaim_no_duplicate_effects, tests/integration/test_restart_recovery.py::test_server_restart_resumes_without_loss
- Scope: tracks/supervisor/recover.py
- Depends on: T-001, T-003
- Batch: 4
- Parallel: True

## T-010
- Issue: #146
- Description: verification-only 验收闭口（IF-DOCREV-001）：pages.py 交付面已在树内（PAGES 封闭集渲染、vditor_asset_tags 自源站引用、revision 对比区），diff 锚点实测全绿；0294-01 冻结测试未播种（需 Shield fixture 修复）且 owner=api_query（T-011）、0294-03 stale_revision 发射面 owner=service.py（T-001 已封闭）——deferred 至 T-INT 收口。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0294-02
- FR refs: FR-0294
- IF ids: IF-DOCREV-001
- Unit refs: -
- Acceptance refs: tests/integration/test_doc_review_web.py::test_diff_between_revisions_visible
- Scope: tracks/server/pages.py
- Depends on: T-001, T-005, T-006
- Batch: 6
- Parallel: True

## T-011
- Issue: #158
- Description: 只读查询路由（IF-QUERY-001）：api_query.py 全部 GET 端点，独立只读连接、响应过 redactor。锚点=命令读回三件+总览+时间线；跨域锚点（0295-03/0301-02/0305-02/NFR0150-02）归 T-INT 镜像收口（卡预算精简，源侧 deferred 移除，覆盖不变）。
- AC refs: AC-FR0295-01, AC-FR0295-02, AC-FR0295-03, AC-FR0295-04, AC-FR0301-01, AC-FR0301-02, AC-FR0305-01, AC-FR0305-02, AC-NFR0150-02
- FR refs: FR-0295, FR-0301, FR-0305, NFR-0150
- IF ids: IF-QUERY-001
- Unit refs: -
- Acceptance refs: tests/integration/test_command_service.py::test_command_id_survives_restart, tests/integration/test_command_service.py::test_idempotency_dedup_and_conflict, tests/integration/test_command_service.py::test_queries_are_readonly, tests/integration/test_overview_query.py::test_overview_consistent_with_state, tests/integration/test_timeline_diagnosis.py::test_timeline_shows_failures_retries_next
- Scope: tracks/server/api_query.py
- Depends on: T-001, T-003, T-005, T-006
- Batch: 6
- Parallel: True

## T-012
- Issue: #158
- Description: verification-only 验收闭口（IF-STREAM-001）：api_events.py SSE 交付面已在树内（id/event/data 帧、游标补读、wait=0 回退），轮询回退锚点实测全绿；0306 两件镜像归 T-016（event_stream 文件）。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0306-01, AC-FR0306-02, AC-NFR0152-02
- FR refs: FR-0306, NFR-0152
- IF ids: IF-STREAM-001
- Unit refs: -
- Acceptance refs: tests/integration/test_backoff_policy.py::test_poll_fallback_caps_and_idle_silence
- Scope: tracks/server/api_events.py
- Depends on: T-003, T-005, T-006
- Batch: 6
- Parallel: True

## T-013
- Issue: #160
- Description: verification-only 验收闭口（IF-WEBGATE-001）：api_command.py 交付面已在树内（全部变更端点、会话+CSRF+guard、Rejection→状态码映射），3 条集成锚点实测全绿；0309-01 发布全链与 0308-02/0309-02/0311-02 三件 stale 拒绝（绑定面冻结测试直调 service.accept，owner=service.py，T-001 已封闭）deferred 至 T-INT 收口。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0308-02, AC-FR0309-03, AC-FR0311-01
- FR refs: FR-0308, FR-0309, FR-0311
- IF ids: IF-WEBGATE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_web_release.py::test_delay_and_return_bound_to_digest, tests/integration/test_controlled_retry.py::test_retry_from_legal_position_no_duplicates
- Scope: tracks/server/api_command.py
- Depends on: T-001, T-004, T-005
- Batch: 6
- Parallel: True

## T-014
- Issue: #140
- Description: verification-only 验收闭口（IF-SERVE-001）：服务装配交付面已在树内（create_app 装配、cmd_serve 口令供给/recover 接入/uvicorn/优雅停止、CLI 注册），重启恢复锚点实测全绿；0288-03 注册捐赠 T-022、NFR0150-03 捐赠 T-023。已绿锚无合法 RED，验收=Runtime 直跑。
- AC refs: AC-FR0288-01, AC-FR0288-02
- FR refs: FR-0288, NFR-0150
- IF ids: IF-SERVE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_serve_lifecycle.py::test_restart_recovers_registry_commands_waits
- Scope: tracks/server/app.py, tracks/cli/serve_cmd.py, tracks/cli/main.py
- Depends on: T-001, T-002, T-003, T-004, T-005, T-006, T-007, T-008, T-009, T-010, T-011, T-012, T-013, T-015
- Batch: 7
- Parallel: False

## T-017
- Issue: #166
- Description: verification-only 验收闭口（IF-WEBAUTH-001/IF-CMDGUARD-001）：裁决入口与认证面已在树内（service.resolve_discussion_thread 实现、app 中间件），无桩无合法 RED（F-T017-RED-01）；web_auth 两红锚为冻结测试调用缺陷（参数错位/未播种），经 verification_failed→DIAGNOSE(test_defect)→SHIELD_FIX 通道修复后复验。unit 钉桩移除（F-T017-RED-02：借用 t001 桩钉他人 AC）。
- AC refs: AC-FR0314-01, AC-FR0314-04
- FR refs: FR-0314
- IF ids: IF-WEBAUTH-001, IF-CMDGUARD-001
- Unit refs: -
- Acceptance refs: tests/integration/test_web_auth.py
- Scope: tracks/server/app.py
- Depends on: T-001, T-004
- Batch: 9
- Parallel: False

## T-018
- Issue: #160
- Description: WEBGATE seam 收口（T-013 捐赠 0308-01/0309-01/0309-02/0311-02）：service.accept 的stale revision/preview/evidence 绑定落地；三文件级验收（跳 RED，unit 钉桩）。
- AC refs: AC-FR0308-01, AC-FR0309-01, AC-FR0309-02, AC-FR0311-02
- FR refs: FR-0308, FR-0309, FR-0311
- IF ids: IF-WEBGATE-001
- Unit refs: tests/unit/test_t001_command_service_red.py
- Acceptance refs: tests/integration/test_web_approval.py, tests/integration/test_web_release.py, tests/integration/test_controlled_retry.py
- Scope: tracks/server/app.py
- Depends on: T-001, T-013
- Batch: 9
- Parallel: False

## T-019
- Issue: #148
- Description: LEASE/SCHED seam 收口（T-007 捐赠 0296-01/02、T-015 捐赠 0296-04）：db.complete_command CAS 失败发 worker.late_result+换队序列落地；三文件级验收（跳 RED，unit 钉桩）。
- AC refs: AC-FR0296-01, AC-FR0296-02, AC-FR0296-04
- FR refs: FR-0296
- IF ids: IF-LEASE-001, IF-SCHED-001, IF-PAUSE-001
- Unit refs: tests/unit/test_t001_command_service_red.py
- Acceptance refs: tests/integration/test_supervisor_lease.py, tests/integration/test_web_rollback.py, tests/integration/test_hotfix_swap.py
- Scope: tracks/supervisor/lease.py, tracks/supervisor/db.py, tracks/supervisor/service.py, tracks/supervisor/scheduler.py
- Depends on: T-001, T-003, T-007, T-015
- Batch: 9
- Parallel: False

## T-020
- Issue: #146
- Description: DOCREV seam 收口（T-010 捐赠 0294-01/03）：revision 可见性播种修复（Shield fixture）与edit/approval 修订绑定落地；文件级验收（跳 RED，unit 钉桩）。
- AC refs: AC-FR0294-01, AC-FR0294-03
- FR refs: FR-0294
- IF ids: IF-DOCREV-001
- Unit refs: tests/unit/test_t001_command_service_red.py
- Acceptance refs: tests/integration/test_doc_review_web.py
- Scope: tracks/supervisor/service.py
- Depends on: T-001, T-006, T-010
- Batch: 9
- Parallel: False

## T-021
- Issue: #142
- Description: PROJ seam 收口（T-002 捐赠 0289-02）：register_project 越范围拒绝（service 接 guard）落地；文件级验收（跳 RED，unit 钉桩）。
- AC refs: AC-FR0289-02
- FR refs: FR-0289
- IF ids: IF-PROJ-001, IF-SECRECY-001
- Unit refs: tests/unit/test_t001_command_service_red.py
- Acceptance refs: tests/integration/test_project_registry.py, tests/integration/test_secrecy.py
- Scope: tracks/supervisor/service.py, tracks/server/guard.py
- Depends on: T-001, T-002, T-004, T-005
- Batch: 9
- Parallel: False

## T-022
- Issue: #140
- Description: verification-only 验收闭口（IF-SERVE-001/IF-RECOVER-001）：服务生命周期面已在树内（serve 装配/健康检查/unhealthy 不伪装可用），serve_lifecycle 文件实测全绿，无桩无合法 RED（F-T022-RED-01）；unit 钉桩移除（F-T022-RED-02：借用 t001 桩钉他人 AC）。验收=Runtime 直跑。
- AC refs: AC-FR0288-03
- FR refs: FR-0288
- IF ids: IF-SERVE-001, IF-RECOVER-001
- Unit refs: -
- Acceptance refs: tests/integration/test_serve_lifecycle.py
- Scope: tracks/server/app.py, tracks/cli/serve_cmd.py
- Depends on: T-001, T-003, T-009, T-014
- Batch: 9
- Parallel: False

## T-023
- Issue: #168
- Description: QUERY perf/CMDSVC seam 收口（T-014 捐赠 NFR0150-03）：soak 内存有界经真实 serve 栈落地；并收 hotfix 前检镜像（run_create 文件，CMDSVC 域 preflight，tokens 在 if_ids 内）；文件级验收（跳 RED，unit 钉桩）。
- AC refs: AC-NFR0150-03
- FR refs: NFR-0150
- IF ids: IF-QUERY-001, IF-CMDSVC-001, IF-SERVE-001
- Unit refs: tests/unit/test_t001_command_service_red.py
- Acceptance refs: tests/integration/test_perf_budget.py, tests/integration/test_run_create_web.py
- Scope: tracks/supervisor/service.py, tracks/server/app.py
- Depends on: T-001, T-011, T-014
- Batch: 9
- Parallel: False

## T-024
- Issue: #169
- Description: verification-only 验收闭口（出口过渡修复）：全图 23 任务已完成，出口过渡任务以一条 绿锚（event_stream live_push，T-012 交付面）触发 task.completed→ISLAND_GATE_2（reach 已修）。无实现面、无 RED 义务，验收=Runtime 直跑该锚。
- AC refs: 
- FR refs: 
- IF ids: 
- Unit refs: -
- Acceptance refs: tests/integration/test_event_stream.py::test_live_push_without_refresh
- Scope: tracks/server/app.py
- Depends on: T-012
- Batch: 11
- Parallel: False

## T-016
- Issue: #169
- Description: 可靠性矩阵终局收口（IF-MTEST-001/002）：八场景元扫描+守卫 registry/coverage 门槛继承经完整套件收口（seams 由 T-017..T-023 落地后执行；跳 RED，unit 钉桩）。
- AC refs: AC-NFR0151-01, AC-NFR0151-02
- FR refs: NFR-0151
- IF ids: IF-MTEST-001, IF-MTEST-002
- Unit refs: tests/unit/test_t001_command_service_red.py
- Acceptance refs: tests/integration/test_reliability_matrix.py::test_eight_reliability_scenarios_covered, tests/integration/test_reliability_matrix.py::test_coverage_threshold_inherited, tests/integration/test_command_service.py, tests/integration/test_overview_query.py, tests/integration/test_timeline_diagnosis.py, tests/integration/test_event_stream.py
- Scope: tracks/server/app.py
- Depends on: T-017, T-018, T-019, T-020, T-021, T-022, T-023
- Batch: 10
- Parallel: False
