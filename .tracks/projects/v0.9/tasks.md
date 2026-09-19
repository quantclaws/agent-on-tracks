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
- Description: 命令服务核心（IF-CMDSVC-001）：db.py 服务面八表+service_events；service.py CommandService 先持久化再执行、幂等去重/冲突拒绝、surface=cli 内联、pause 两段受理、run/hotfix 前检与澄清受理、批准/preview 绑定复用既有 gate（不改 CLI）。锚点=创建/澄清/暂停/延迟（0292-02 前检 deferred 至 T-INT；proj 经 deps）。
- AC refs: AC-FR0291-01, AC-FR0291-02, AC-FR0292-01, AC-FR0292-02, AC-FR0293-01, AC-FR0293-02, AC-FR0310-01, AC-FR0310-02, AC-NFR0150-01
- FR refs: FR-0291, FR-0292, FR-0293, FR-0310, NFR-0150
- IF ids: IF-CMDSVC-001
- Unit refs: -
- Acceptance refs: tests/integration/test_run_create_web.py::test_create_feature_run_returns_ids, tests/integration/test_run_create_web.py::test_failed_and_duplicate_submit_no_fake_run, tests/integration/test_clarify_web.py::test_reply_continues_same_run, tests/integration/test_clarify_web.py::test_discussion_history_traceable, tests/integration/test_pause_resume.py::test_pause_two_phase_and_priority, tests/integration/test_pause_resume.py::test_resume_from_legal_position, tests/integration/test_perf_budget.py::test_accept_returns_after_persist
- Scope: tracks/supervisor/db.py, tracks/supervisor/service.py
- Depends on: T-006
- Batch: 2
- Parallel: False

## T-002
- Issue: #142
- Description: 项目登记与就绪探测（IF-PROJ-001）：readiness.py 四类封闭探针逐项 ok+reason；未就绪阻断 create_run；登记归属展示经 T-001（deps 覆盖 service）。锚点=登记两件+就绪两件。
- AC refs: AC-FR0289-01, AC-FR0289-02, AC-FR0290-01, AC-FR0290-02
- FR refs: FR-0289, FR-0290
- IF ids: IF-PROJ-001
- Unit refs: -
- Acceptance refs: tests/integration/test_project_registry.py::test_register_shows_attribution_and_survives_restart, tests/integration/test_project_registry.py::test_out_of_scope_rejected, tests/integration/test_readiness.py::test_readiness_items_displayed, tests/integration/test_readiness.py::test_not_ready_blocks_run_creation
- Scope: tracks/supervisor/readiness.py
- Depends on: T-001
- Batch: 3
- Parallel: True

## T-003
- Issue: #150
- Description: 等待/quota/退避（IF-WAIT-001）：classify_wait 五类封闭集、enter/resolve_wait 持久化、next_probe 60s→900s 不热循环不编造倒计时。锚点=quota 两件+退避口径（NFR0152-01 同文件触及投影读回，deps 覆盖）。
- AC refs: AC-FR0299-01, AC-FR0299-02, AC-NFR0152-01
- FR refs: FR-0299, NFR-0152
- IF ids: IF-WAIT-001
- Unit refs: -
- Acceptance refs: tests/integration/test_quota_wait.py::test_known_reset_auto_resume, tests/integration/test_quota_wait.py::test_unknown_reset_probe_plan_no_countdown, tests/integration/test_backoff_policy.py::test_backoff_growth_bounded_and_config_readable
- Scope: tracks/supervisor/waiting.py
- Depends on: T-001, T-006
- Batch: 3
- Parallel: True

## T-004
- Issue: #166
- Description: 认证+防护（IF-WEBAUTH-001/IF-CMDGUARD-001）：auth.py scrypt 口令/会话 token/CSRF；guard.py 封闭 kind/schema 校验、actor_class 强制、realpath 范围防护；discuss/gate.py+cli.py 裁决权属判定（service 侧惰性 import 复用）。0314-01 deferred（需 app 装配面）。
- AC refs: AC-FR0314-01, AC-FR0314-02, AC-FR0314-03, AC-FR0314-04
- FR refs: FR-0314
- IF ids: IF-WEBAUTH-001, IF-CMDGUARD-001
- Unit refs: -
- Acceptance refs: tests/integration/test_web_auth.py::test_non_human_decision_rejected_audited, tests/integration/test_web_auth.py::test_shell_payload_blocked, tests/integration/test_web_auth.py::test_adjudication_ownership_enforced
- Scope: tracks/server/auth.py, tracks/server/guard.py, tracks/discuss/gate.py, tracks/discuss/cli.py
- Depends on: T-001
- Batch: 3
- Parallel: True

## T-005
- Issue: #167
- Description: SecretRedactor 全出口脱敏（IF-SECRECY-001）：API/SSE/HTML/日志统一受控引用；口令/token 不落盘。锚点=越权读取拒绝（guard+redaction，guard 经 deps 覆盖）。0315-01 全出口扫描 deferred 至 T-INT。
- AC refs: AC-FR0315-01, AC-FR0315-02
- FR refs: FR-0315
- IF ids: IF-SECRECY-001
- Unit refs: -
- Acceptance refs: tests/integration/test_secrecy.py::test_out_of_scope_read_denied
- Scope: tracks/server/redaction.py
- Depends on: T-004
- Batch: 4
- Parallel: True

## T-007
- Issue: #148
- Description: 租约/代次（IF-LEASE-001）：事务内单调代次、完成 CAS、旧代次结果 late_result 隔离。锚点=并发单派发+迟到隔离+0312 回拨三件（service 经 deps 覆盖）。
- AC refs: AC-FR0296-01, AC-FR0296-02, AC-FR0312-01, AC-FR0312-02, AC-FR0312-03
- FR refs: FR-0296, FR-0312
- IF ids: IF-LEASE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_supervisor_lease.py::test_concurrent_drive_single_dispatch, tests/integration/test_supervisor_lease.py::test_stale_generation_late_result_quarantined, tests/integration/test_web_rollback.py::test_return_with_impact_preview, tests/integration/test_web_rollback.py::test_late_results_isolated_after_rollback, tests/integration/test_web_rollback.py::test_pushed_release_survives_rollback
- Scope: tracks/supervisor/lease.py
- Depends on: T-001
- Batch: 3
- Parallel: True

## T-015
- Issue: #148
- Description: 调度（IF-SCHED-001）：schedule 单行表单活动 run+有序队列；hotfix 显式可审计换队；pause 生效即抑制调度/唤醒。0296-04 换队全链 deferred 至 T-INT。
- AC refs: AC-FR0296-03, AC-FR0296-04
- FR refs: FR-0296
- IF ids: IF-SCHED-001
- Unit refs: -
- Acceptance refs: tests/integration/test_hotfix_swap.py::test_second_run_queued
- Scope: tracks/supervisor/scheduler.py
- Depends on: T-001
- Batch: 3
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
- Depends on: T-001
- Batch: 3
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
- Description: 页面壳与 Vditor 宿主（IF-DOCREV-001）：PAGES 封闭集渲染 + vditor_asset_tags 自源站引用（禁 CDN/禁可选引擎）+ revision 对比区。
- AC refs: AC-FR0294-01, AC-FR0294-02, AC-FR0294-03
- FR refs: FR-0294
- IF ids: IF-DOCREV-001
- Unit refs: -
- Acceptance refs: tests/integration/test_doc_review_web.py::test_revision_visible_and_pending_bound, tests/integration/test_doc_review_web.py::test_diff_between_revisions_visible, tests/integration/test_doc_review_web.py::test_edit_produces_new_revision_stale_approval_rejected
- Scope: tracks/server/pages.py
- Depends on: T-001, T-005, T-006
- Batch: 5
- Parallel: True

## T-011
- Issue: #158
- Description: 只读查询路由（IF-QUERY-001）：api_query.py 全部 GET 端点（含命令状态查询），独立只读连接、响应过 redactor。锚点=命令读回三件（0295 持久化/去重/只读——查询交付面）+总览+时间线+查询 P95。0295-03/0301-02/0305-02 deferred（需真实 serve 栈/CLI 双通道）。
- AC refs: AC-FR0295-01, AC-FR0295-02, AC-FR0295-03, AC-FR0295-04, AC-FR0301-01, AC-FR0301-02, AC-FR0305-01, AC-FR0305-02, AC-NFR0150-02
- FR refs: FR-0295, FR-0301, FR-0305, NFR-0150
- IF ids: IF-QUERY-001
- Unit refs: -
- Acceptance refs: tests/integration/test_command_service.py::test_command_id_survives_restart, tests/integration/test_command_service.py::test_idempotency_dedup_and_conflict, tests/integration/test_command_service.py::test_queries_are_readonly, tests/integration/test_overview_query.py::test_overview_consistent_with_state, tests/integration/test_timeline_diagnosis.py::test_timeline_shows_failures_retries_next, tests/integration/test_perf_budget.py::test_query_p95_under_1s
- Scope: tracks/server/api_query.py
- Depends on: T-001, T-005, T-006
- Batch: 5
- Parallel: True

## T-012
- Issue: #158
- Description: 事件订阅路由（IF-STREAM-001）：api_events.py SSE（id/event/data 帧、游标补读、wait=0 回退）。锚点全部需真实 serve 栈，B94 deferred 至 T-INT。
- AC refs: AC-FR0306-01, AC-FR0306-02, AC-NFR0152-02
- FR refs: FR-0306, NFR-0152
- IF ids: IF-STREAM-001
- Unit refs: -
- Acceptance refs: 
- Scope: tracks/server/api_events.py
- Depends on: T-005, T-006
- Batch: 5
- Parallel: True

## T-013
- Issue: #160
- Description: 变更类 HTTP 路由（IF-WEBGATE-001）：api_command.py 全部变更端点（会话+CSRF+guard、Rejection→状态码映射、accept surface=http）。锚点=批准两件+三择一两件+受控重试两件（service 经 deps 覆盖）。0309-01 deferred（发布全链归 T-INT）。
- AC refs: AC-FR0308-01, AC-FR0308-02, AC-FR0309-01, AC-FR0309-02, AC-FR0309-03, AC-FR0311-01, AC-FR0311-02
- FR refs: FR-0308, FR-0309, FR-0311
- IF ids: IF-WEBGATE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_web_approval.py::test_approval_bound_and_advances, tests/integration/test_web_approval.py::test_stale_revision_rejected, tests/integration/test_web_release.py::test_stale_preview_rejected, tests/integration/test_web_release.py::test_delay_and_return_bound_to_digest, tests/integration/test_controlled_retry.py::test_retry_from_legal_position_no_duplicates, tests/integration/test_controlled_retry.py::test_stale_evidence_retry_rejected
- Scope: tracks/server/api_command.py
- Depends on: T-001, T-004, T-005
- Batch: 5
- Parallel: True

## T-014
- Issue: #140
- Description: 服务装配与入口（IF-SERVE-001）：app.py create_app 装配、serve_cmd.py cmd_serve（口令供给/recover 接入/uvicorn/优雅停止）、cli/main.py 注册 serve+USAGE 同步。锚点=重启恢复（ast 实测触 app+db+recover+service，本任务拥有 app/serve_cmd，其余经 deps 覆盖）。deferred：0288-01/03、NFR0150-03（真实服务进程栈）。
- AC refs: AC-FR0288-01, AC-FR0288-02, AC-FR0288-03, AC-NFR0150-03
- FR refs: FR-0288, NFR-0150
- IF ids: IF-SERVE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_serve_lifecycle.py::test_restart_recovers_registry_commands_waits
- Scope: tracks/server/app.py, tracks/cli/serve_cmd.py, tracks/cli/main.py
- Depends on: T-001, T-002, T-003, T-004, T-005, T-006, T-007, T-008, T-009, T-010, T-011, T-012, T-013, T-015
- Batch: 6
- Parallel: False

## T-INT
- Issue: #998
- Description: 终局收口：全图 14 条跨域锚点声明为本任务 acceptance（deferred 留在源任务作早期信号、不计其 verdict，互斥不变），与可靠性八场景元扫描 + 守卫 registry/coverage 门槛继承 2 条共 16 条经真实 serve 子进程栈一次硬门禁转绿（跳过 RED，REFACTOR/质量门禁不豁免）。
- AC refs: AC-NFR0151-01, AC-NFR0151-02
- FR refs: NFR-0151
- IF ids: IF-MTEST-001, IF-MTEST-002
- Unit refs: -
- Acceptance refs: tests/integration/test_reliability_matrix.py::test_eight_reliability_scenarios_covered, tests/integration/test_reliability_matrix.py::test_coverage_threshold_inherited, tests/integration/test_web_auth.py::test_unauthenticated_rejected_then_ok, tests/integration/test_secrecy.py::test_no_plaintext_secrets_any_surface, tests/integration/test_hotfix_swap.py::test_hotfix_preemption_audited, tests/integration/test_command_service.py::test_cli_http_equivalent_events, tests/integration/test_overview_query.py::test_query_during_long_task_nonblocking, tests/integration/test_timeline_diagnosis.py::test_logs_locatable_by_dimensions, tests/integration/test_event_stream.py::test_live_push_without_refresh, tests/integration/test_event_stream.py::test_reconnect_backfill_no_regression, tests/integration/test_backoff_policy.py::test_poll_fallback_caps_and_idle_silence, tests/integration/test_web_release.py::test_release_via_web_happy, tests/integration/test_run_create_web.py::test_hotfix_precheck_rejected, tests/integration/test_serve_lifecycle.py::test_health_and_browserless_progress, tests/integration/test_serve_lifecycle.py::test_unhealthy_no_fake_available, tests/integration/test_perf_budget.py::test_soak_memory_bounded
- Scope: tracks/server/app.py, tracks/server/api_command.py, tracks/server/api_query.py, tracks/server/api_events.py, tracks/supervisor/service.py, tracks/supervisor/worker.py, tracks/cli/serve_cmd.py, tracks/cli/main.py
- Depends on: T-001, T-002, T-003, T-004, T-005, T-006, T-007, T-008, T-009, T-010, T-011, T-012, T-013, T-014, T-015
- Batch: 7
- Parallel: False
