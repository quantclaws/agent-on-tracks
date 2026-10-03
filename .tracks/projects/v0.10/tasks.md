# Task Graph

## T-001
- Issue: #999
- Description: verification-only 验收闭口（2026-10-02 重定义改排，T-003 seq-48481 先例：AC-FR0325-01/02 锤位迁移 T-003 后 payload 已变，锚（auth_name 四锚）已随原始交付转绿——重定义任务的绿锚按 #34 裁定改排 verification-only，完成记录按 B83 不保留、由 Runtime 直跑验收闭口）。原始定义：认证核心与名字端到端绑定 + app.py 路由面（IF-WEBAUTH-001 主合同；IF-AUTHNAME-001 的服务端交付面）：supervisor/db.py 的 auth 表 display_name 幂等演进（PRAGMA 探测 + ALTER）；auth.py 名字校验（非空/去空白/<=64/无控制字符）与生效 actor（display_name ?? auth.actor ?? local-user）、绑定三连（写 display_name、更新既有 sessions 行 actor、落 auth.name_bound 审计）；app.py 的 POST /api/auth/name 与 GET /api/auth/profile 路由（_AuthGlue，CSRF 强制）、login 响应追加 name_required、7 个工作台页面路由在会话有效但名字未采集时 302 回 /login（interfaces §1m/§2b #29/#30）。同时在 app.py 落路由扩展缝：create_app 的路由装配并入 api_query 侧声明的扩展路由（初始为空），使 T-003 的 project 域只读端点 #31-34 落地时无需再改 app.py——六端点的可观察合同（interfaces §2b）不变，装配缝是实现细节（CORE-02），单写域排他由本任务持有 app.py。锚点=logout 清会话与 cookie、未认证 302/401 与 logout 清理、名字绑定流（name_required/校验/持久化/审计/actor 流入 web 决策）、无注册面单身份。已知 test_defect 预登记（Prism PP1-R1-B02，结算决定见 test-plan §8 备案线程 round-2 回复）：本任务锚点 test_name_binding_flows_to_events_and_discussion 在 test_auth_name.py:239 经跟随重定向的 http_get 断言 GET /==302，跟随客户端机械不可观察——GREEN 门在该断言失败时按预登记 sole voice test_defect 直路由 DIAGNOSE→SHIELD_FIX（Shield 改用文件内既有 _get_redirect no-follow 观察），Devon 不追逐该断言、不做产品迁就；同轮 SHIELD_FIX 一并修复 login_session 名字步（PP1-R1-B01）。
- AC refs: AC-FR0318-03, AC-FR0320-01, AC-FR0320-02, AC-FR0321-01, AC-FR0321-02
- FR refs: FR-0318, FR-0320, FR-0321
- IF ids: IF-WEBAUTH-001
- Unit refs: -
- Acceptance refs: tests/integration/test_auth_name.py::test_logout_clears_session_and_cookie, tests/integration/test_auth_name.py::test_unauthenticated_redirect_and_logout_clears, tests/integration/test_auth_name.py::test_name_binding_flows_to_events_and_discussion, tests/integration/test_auth_name.py::test_no_registration_surface_single_identity
- Scope: tracks/server/auth.py, tracks/server/app.py, tracks/supervisor/db.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-002
- Issue: #999
- Description: workbench 壳渲染与 chrome 静态面（IF-WORKBENCH-001）：pages.py 渲染收敛为两壳——login 双栏 auth shell（hero 图+标语+署名+登录面板+行内错误+窄视口折叠）与同一 workbench shell（三段式骨架、data-route 深链、data-testid 锚点集、type=module 引导 /static/app/shell.js、全同源资产、无密钥）；styles.css 承载 tab 条（仅图标+tooltip、命中区>=32px、顺序固定）与三区布局样式。PAGES 8 名封闭集与 URL 不变；v0.9 遗留 unit 断言（test_t010_page_shell_vditor_red.py 的 review 页内嵌资产标签）由 Devon 在 RGR 内按新合同适配（test-plan §11 #4，Devon 自辖）。锚点=无构建链原生 ES modules（GET / 壳 HTML）、真数据一致性（投影逐字段一致、壳不内嵌行）、写安全拒绝面（既有 edits 403/400 回归 + /api/auth/name 缺 CSRF 403 经 deps 由 T-001 闭合）。已知 test_defect 预登记（Prism PP1-R1-B01，已按 test-plan §8 备案线程 round-2 回复结算）：Shield 共享助手 login_session（tests/_support/v09_web.py，Shield 写域）无 §1m.2 名字步，未命名会话 GET / 被 302 回 /login，前两个锚点的 GET /==200 且含 data-route 断言在 GREEN 时刻机械不可绿——结算决定为接受 GREEN 期 test_defect 路由：T-001 首个 GREEN 失败经 DIAGNOSE→SHIELD_FIX 同时修复 login_session 名字步（PP1-R1-B01）与 test_auth_name 的 302 观察（PP1-R1-B02），本任务两锚点随该修复转绿（budget 3 吸收该界）；Devon 不追逐这两个预登记断言、不做产品迁就。
- AC refs: AC-FR0316-01, AC-FR0316-02, AC-FR0317-01, AC-FR0318-01, AC-FR0318-02, AC-FR0319-01, AC-NFR0153-01, AC-NFR0153-02, AC-NFR0154-01, AC-NFR0154-02, AC-NFR0156-01
- FR refs: FR-0316, FR-0317, FR-0318, FR-0319, NFR-0153, NFR-0154, NFR-0156
- IF ids: IF-WORKBENCH-001
- Unit refs: -
- Acceptance refs: tests/integration/test_workbench_shell.py::test_no_build_chain_native_esm, tests/integration/test_workbench_shell.py::test_no_mock_data_parity_with_projections, tests/integration/test_workbench_shell.py::test_mutation_endpoints_reject_missing_csrf
- Scope: tracks/server/pages.py, tracks/server/static/styles.css
- Depends on: T-001
- Batch: 2
- Parallel: True

## T-003
- Issue: #999
- Description: verification-only 验收闭口（IF-QUERY-001 主合同；IF-DOCCENTER-001/IF-TIMELINE-001/IF-DISCUSS-001 读模型面）：docs tree（版本逆序/六件套/read+diff/editable_run_id）、discussions 线程投影、timeline stage_order 扩展的交付面已在树，四个锚点（tree 六件套读取、discussions 读模型、stage_order 一致性、timeline 入口）实测绿（green-by-delivery，red-window residual map 登记），验收=Runtime 直跑 acceptance_refs，不走 Devon RGR。PLANNING round 1 attempt 3 修订（commit_taskgraph taskgraph 硬拒，seq 48481）：Shield 按 docs_revision 修订设计重写的冻结锚静态 import tracks/supervisor/service.py（计算 docs_revision 期望值），构成 T-003 锚点→service.py 静态依赖边——service.py 原 owner（T-004/T-011）均在 T-003 下游，补边成环且批次倒挂（本任务已完成、payload 冻结面只余重定义通道），结构上不可行；最小合法闭边=service.py 随锚点归本任务 scope（edge-closure/provenance 持有：内容交付属 T-004 lineage 的 replay WIP，提交通道属 T-011 GREEN 的 seeded-WIP lineage；T-004 同步让出 service.py 维持非集成任务 scope 排他；T-011 union scope 不变）。payload 变更按 B83 不保留前次完成记录（seq 47657），锚已绿故改型 verification-only 重验收口（标准 RGR 无合法 Red——T-010 seq 46995 先例）。deferred=test_docs_center.py 文件级守卫镜像不变（save 锚归 T-011、conflict 锚归 T-004，本任务的文件级镜像由 T-011 硬门禁收口）。PLANNING round 1 attempt 3 共位修订：锚 test_discussions_read_model_and_nav_state 的 §8 行 AC（AC-FR0325-01）与守卫行 AC（AC-FR0325-02，其守卫节点在本任务 deferred 文件级镜像内）自 T-001 移回本任务 ac_refs，FR-0325 的 fr_refs 归属与 if_ids 增列 IF-DISCUSS-001 随之一并对齐——锚-AC 共位不变量机械自检要求锚点行 AC 登记在 owner 任务，discussions 读模型交付面本来就在本任务 scope（api_query.py），T-001 纯 auth 面不持有该 AC。
- AC refs: AC-FR0322-01, AC-FR0325-01, AC-FR0325-02, AC-FR0326-01, AC-FR0326-02
- FR refs: FR-0322, FR-0325, FR-0326
- IF ids: IF-QUERY-001, IF-DISCUSS-001
- Unit refs: -
- Acceptance refs: tests/integration/test_docs_center.py::test_tree_version_desc_and_six_piece_read, tests/integration/test_run_timeline_api.py::test_stage_order_and_timeline_consistency, tests/integration/test_run_timeline_api.py::test_timeline_entry_present, tests/integration/test_docs_center.py::test_discussions_read_model_and_nav_state
- Scope: tracks/server/api_query.py, tracks/server/projections.py, tracks/supervisor/service.py
- Depends on: T-001
- Batch: 2
- Parallel: True

## T-004
- Issue: #999
- Description: verification-only 验收闭口（IF-DOCSAVE-001 冲突面 + docs_revision/token 交付面，AC-FR0323-02；IF-DOCREV-001/IF-WEBGATE-001 复用面）：supervisor/service.py 的 docs_revision 纯函数（§1o.1a 六件套固定序 label:body_sha 摘要，baseline.py/FR-0308 零触碰）+ edit_material params 可选 revision_kind 面，与 api_command.py 的运行域面 #17 收敛（trio 域，design 别名与设计文档名 →422 指向 #35）、409 响应体顶层 current_revision（§1o.2 唯一权威形态）、文档中心面 #35 的 edit_project_doc 处理器与 EXTENSION_ROUTES 声明，均已交付在树（replay WIP：api_command.py/service.py dirty；R 树 5e332315 的 14 枚 unit 钉全绿；冲突锚 test_conflict_409_two_options_and_draft_retained 实测绿——GREEN_GATE attempt 2（seq 3931）确认修订合同端到端工作）。验收=Runtime 直跑 acceptance_refs，不走 Devon RGR。#35 保存腿（锚 test_save_produces_revision_and_stale_approval_rejected + AC-FR0323-01）经 plan_defect（seq 48472）裁定随写域移交 T-011：app.py 组装根挂载（消费 api_command.EXTENSION_ROUTES）是该锚唯一缺失面，而 app.py 归已完成冻结的 T-001（scope 不可再分，B83 等价性），任一非集成任务持有 app.py 即触发 scope overlap 硬拒——integration 任务的 union scope 豁免是唯一合法写域（PLAN-03 最后可绿 owner）。本任务 WIP 的提交通道由 T-011 的 GREEN lineage 承接（verification-only 无提交通道，v0.7 T-016 先例）。 scope 收敛为 tracks/server/api_command.py（PLANNING round 1 attempt 3 静态边闭边手术，seq 48481：service.py 随其静态消费锚归 T-003 持有，本任务 verification-only 无写通道、让出不损交付事实——docs_revision 纯函数等内容交付属本任务 lineage 的 replay WIP，提交通道属 T-011 GREEN）。
- AC refs: AC-FR0323-02
- FR refs: FR-0323
- IF ids: IF-DOCSAVE-001, IF-DOCREV-001, IF-WEBGATE-001
- Unit refs: -
- Acceptance refs: tests/integration/test_docs_center.py::test_conflict_409_two_options_and_draft_retained
- Scope: tracks/server/api_command.py
- Depends on: T-003
- Batch: 3
- Parallel: False

## T-005
- Issue: #999
- Description: verification-only 验收闭口（2026-10-02 重定义改排，T-001/T-003 seq-48481 先例：T-011 unit_refs 手术追加描述注记使 payload 变化，锚（unit RED 16 节点）已随原始交付全绿——重定义任务绿锚按 #34 裁定改排 verification-only，完成按 Runtime 直跑验收闭口）。原始定义：浏览器侧 SPA 应用层（IF-WEBUI-001 交付面；IF-DOCCENTER-001 编辑器/分屏、IF-DISCUSS-001 覆盖层、IF-STREAM-001 sse 消费的 JS 面）：tracks/server/static/app 原生 ES modules——shell 引导、router（data-route 深链）、api 客户端（统一注入 X-Trac-CSRF 与 Idempotency-Key、失败转可观察反馈）、sse 客户端（event_cursor 补读、重复/乱序不倒退）、tabs/sidebar 状态机（SM-04：原子联动/共存/唯一实例/主动关闭唯一路径/不跨会话）、五功能区视图（含 docs 视图按需同源注入 Vditor ir + 失败回退 textarea、编辑器 409 两选项流（重载放弃/强制覆盖——以服务端内容与顶层 current_revision 重载或以其为新 base_revision 重提，IF-DOCSAVE-001 §1o.2 修订形态，不静默覆盖、写失败内容保留可重试）、panes <=4 独立分屏、discussions 只读导航三能力、timeline 视图消费 stage_order/ac-chain）。集成层无本任务验收锚点（UI 行为经 ui e2e 终态锚点 + ISLAND_GATE_2/FULL 兜底 + ui-e2e required check 承载，test-plan §8 分层纪律）；按 v0.9 T-012 先例以文件级 deferred 承载守卫与继承镜像：tests/integration/test_workbench_shell.py（§8.1 的 ui 静态纪律守卫与 ui-e2e 基础设施声明守卫，到达即绿）与 tests/integration/test_event_stream.py（SSE 重连补读继承锚点，AC-NFR0154-01 登记在 T-002；服务端 SSE 面本版不变）。质量承载：JS 层无静态守卫（architecture §4.3 显式记录的空缺），靠 ui e2e 与评审。PLANNING round 2 修订（Prism PP11-R1-B01 blocker / DIAGNOSE plan_defect seq 4176）：unit_refs 补声明本任务的 unit RED 面 tests/unit/test_t005_static_app_red.py——SPA 任务唯一合法 Red 层（其 AC 的 integration 在场均为 §8.1 到达即绿守卫与终态 e2e，非验收锚）；Devon 在 RED 落笔该文件（测试尚不存在属合法 pre-RED 形态，probe 的 entry/collect 签名按 anchor_probe 规则 2 仅 advisory），以 Python 静态钉住 tracks/server/static/app 各模块的出口合同：api 客户端统一注入 X-Trac-CSRF 与 Idempotency-Key、sse 客户端 event_cursor 补读且重复/乱序不倒退、tabs/sidebar SM-04（原子联动/共存/唯一实例/主动关闭唯一路径/不跨会话）、panes ≤4、editor 409 两选项流消费顶层 current_revision、discussions 只读导航三能力。修复前 unit_refs+acceptance_refs 双空使 test_refs 物化恒空、Devon intake fail-closed（'Devon assignment missing: test_refs'，m_impl_anchor.py:690-697）。边界备案（PRISM_FINAL PF2-B03，失败组 2-archer-67）：login/name 流的服务端缝（pages.py login 壳 testid 对齐 name-input/name-submit + type=module 引导）归 T-011 union scope——pages.py 属 T-002 已完成冻结写域，Devon 不得触碰；本任务只交付消费侧：static/app 内登录/名字 JS 流（api.js setCsrfToken 调用方、name_required 就地展开、名字提交携 X-Trac-CSRF）。PF2-B01（tabbar 事件委托 + tab-strip 节点创建）与 PF2-B02（25/27 冻结 locator 对齐）均在本写域内。
- AC refs: AC-FR0322-02, AC-FR0324-01, AC-FR0324-02, AC-NFR0155-01, AC-NFR0155-02
- FR refs: FR-0322, FR-0324, NFR-0155
- IF ids: IF-WEBUI-001
- Unit refs: tests/unit/test_t005_static_app_red.py
- Acceptance refs: 
- Scope: tracks/server/static/app
- Depends on: T-002, T-003, T-004
- Batch: 4
- Parallel: False

## T-006
- Issue: #181
- Description: live GitHub 通道 TLS 与 ensure 原语（IF-TLS-001 主合同；IF-TRACKER-001 的 github.py 交付面，#181）：effects/github.py 全部 HTTPS 调用点（_urlopen/_get_any/_api_json 链路）显式 ssl.create_default_context(cafile=certifi.where())，TRAC_GITHUB_CA_BUNDLE 显式覆盖；新增 ensure_project_milestone（list 按标题精确匹配→命中复用 api_verified=true created=false；未命中且具备凭据 POST 创建→GET 回读校验 created=true api_verified=true；任何失败返回分类 error 绝不假报 verified）；missing_token 失败消息指向 ops 前置节（docs/getting-started/installation.md 已随 M-DESIGN §2 交付）。锚点=TLS 对照实验（测试 CA 束握手成功 + 默认 certifi 束下同自签证书分类 network 失败）、ops 文档节与 missing_token 指引、ensure 创建/复用/回读——AC-FR0331-01 登记在 T-011（tracker 生命周期集成闭合面），本任务是该锚点的最后可绿 owner（ensure 原语随本任务交付），共位例外按 PLAN-03 在此写明。
- AC refs: AC-FR0330-01, AC-FR0330-02
- FR refs: FR-0330, FR-0331
- IF ids: IF-TLS-001
- Unit refs: -
- Acceptance refs: tests/integration/test_github_tls.py::test_tls_channel_uses_certifi_bundle, tests/integration/test_github_tls.py::test_ops_doc_section_and_missing_token_guidance, tests/integration/test_milestone_ensure.py::test_ensure_milestone_create_reuse_readback
- Scope: tracks/effects/github.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-007
- Issue: #182
- Description: milestone 收尾终态感知守卫与 ensure 接线（IF-MILESTONE-002 主合同；IF-TRACKER-001 的接线交付面，#182 blocker）：milestone_chain.py 的 _complete_milestone 幂等守卫从「任意 run.completed」改为「仅 run.completed(terminal_state=released)」——M-IMPL 闭包边界的 terminal_state=boundary 伪完成不再被误认收尾完成，released 终态可达且 close_milestone 不无限重发；关闭链 _close_milestone_project 接入 ensure-then-close（close 前先经 T-006 的 ensure_project_milestone 确保远端存在），ensure 不可得时落 attention.required(area=project_close, reason=milestone_not_found) 携可操作 next（按 milestone_template 标题手工建后 trac run --resume）并保持 audited skip。锚点=released 在边界伪完成共存下恰好落地一次且 close 子步不重发、milestone_not_found 指引与重试成功——AC-FR0331-02 登记在 T-011（tracker 生命周期集成闭合面），本任务是该锚点的最后可绿 owner（接线随本任务交付；ensure 原语经 depends_on 由 T-006 前置），共位例外按 PLAN-03 在此写明。
- AC refs: AC-FR0327-01
- FR refs: FR-0327, FR-0331
- IF ids: IF-MILESTONE-002
- Unit refs: -
- Acceptance refs: tests/integration/test_milestone_complete.py::test_released_reachable_despite_boundary_completion, tests/integration/test_milestone_ensure.py::test_milestone_not_found_actionable_next
- Scope: tracks/executor/milestone_chain.py
- Depends on: T-006
- Batch: 2
- Parallel: True

## T-008
- Issue: #179
- Description: 制品声明版本门绑定执行（IF-VERSION-001，#179）：host_contract.py 解析 [host-contract.version_scheme].bindings（{file,key,expect} 内联表数组；既有单数键等价单元素；.toml 经 tomllib 点路径、.py 经模块级 NAME = "..." 字面量；expect 以 {release_tag}/{release_version} 占位渲染，未知占位符 fail-closed）；verify_version.py 在既有 tag 派生/远端缺席校验之外追加绑定比对——bindings 非空且旅程为 feature/post_release 时逐绑定读 file 提 key 比对，任一错配 local_gate.failed(kind=version, reason=version_decl_mismatch) 指认 file 与 key，全部一致放行；dev 旅程豁免；未声明时与 v0.9 逐字一致。本宿主声明（pyproject project.version 与 tracks/__init__.py __version__ 绑定 {release_version}、版本 0.10.0、registry digest 重锚）已随 M-DESIGN §2 交付。锚点=绑定比对执行（一致放行/错配指认）；deferred=test_version_gate.py 文件级守卫镜像（§8.1 的合同声明+registry 重锚守卫，到达即绿；同文件绑定执行锚点在本任务 acceptance）。
- AC refs: AC-FR0328-01, AC-FR0328-02
- FR refs: FR-0328
- IF ids: IF-VERSION-001
- Unit refs: -
- Acceptance refs: tests/integration/test_version_gate.py::test_version_bindings_enforced
- Scope: tracks/executor/host_contract.py, tracks/executor/verify_version.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-009
- Issue: #180
- Description: hotfix 前检分类映射与恢复指引（IF-HOTFIX-011，#180）：hotfix_face.py 的 precheck_hotfix_report 承接 fetch 面异常——GithubIssuesError.classification == not_found（含 fetch_issue 的 404→None 确认缺失）→ issue_not_found 保持窄义；auth/rate_limit/network/missing_token → issue_fetch_failed（消除注释承诺但不可达的分类）；hotfix.py 按分类映射 next 指引（missing_token 指向 GITHUB_TOKEN 前置、auth 检查 token 权限、rate_limit 等限额重置、network 检查连通性/TLS 与 TRAC_GITHUB_CA_BUNDLE）且 REJECTED 可重试路径保持开启；precheck_hotfix 纯函数签名与规则不变。锚点=抓取失败分类（缺凭据/401/403 注入）携分类 next 且修复后重提通过、issue_not_found 不被复用；deferred=test_hotfix_precheck_classification.py 文件级守卫镜像（§8.1 的不存在 issue 继承行为守卫，到达即绿）。
- AC refs: AC-FR0329-01, AC-FR0329-02
- FR refs: FR-0329
- IF ids: IF-HOTFIX-011
- Unit refs: -
- Acceptance refs: tests/integration/test_hotfix_precheck_classification.py::test_fetch_failures_classified_with_retry_open
- Scope: tracks/executor/hotfix_face.py, tracks/executor/hotfix.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-010
- Issue: #183
- Description: LEX_REVIEW 合法 park 出口（IF-REVIEW-001 §1s.1-§1s.4 park 语义切片，AC-FR0332-01，#183）：opencode_review.py 评审出口 verdict 封闭集追加 pass-pending-human-threads（全部线程 resolved→pass；存在未决且每个未决线程的裁决权属方为 Human（tracks/discuss 解析的根评论 @Human adjudication_owner，服务端计算不信 agent 自报）→pass-pending-human-threads 携非空 pending_threads[{doc,thread_id,summary}]；其余→revise 语义不变）；machine_verdicts.py/machine.py 归约 park（status=awaiting_human、awaiting=review_pending_threads、substate 停留 LEX_REVIEW、reviewer 通过标志不置位、pending_threads 投影进 State 可重放推导、human.review 后清除 awaiting 重进 LEX_REVIEW）；status_cmd.py 渲染待决 Human 线程清单与恢复指引；executor 检查点管线的 park no-diff 豁免——result_payload.py 的 requires_diff 策略（:379 邻域）与 result_audit.py 的 no-diff 硬失败放行（:358-375 邻域）对 park 零 diff outcome 放行；run_cmd.py `trac review` 恢复面准入追加 awaiting=review_pending_threads（替换 HEAD 旧准入，仅恢复面准入、不承载 no-diff 豁免）。PP7-R3-B01 重组（round 4，Prism PRISM_PLAN seq 47771）：锚点 A 可行绿所需全部文件并入单一写域——管线三文件（result_payload/result_audit/run_cmd）自 T-010b 移入本任务；两轮诊断（seq 2343/2418）实证发布半边（park no-diff 豁免）与恢复半边（review_pending_threads 准入）为锚 test_pass_pending_human_threads_parks_and_lists 的 green 阻断点。前史承前轮（round 3 二次改型）：实现曾 quarantine 进 stash@{0} 且从未进入 main 历史，锚在 HEAD 110c532 实测红（:160 no lex.verdict published），标准 RGR 重跑交付（完成事件 seq 47025 随 payload 重定义按 B83 不予保留）；stash@{0} 仅作行为参考、禁止 pop（含与已落地 T-002 scope 重叠的陈旧 pages.py 227 行 delta）；原 unit 钉文件已随 quarantine 移除，RED 由 Devon 重写。注入切片归 T-010b（dispatch.py+Lex.md，batch 2，depends_on 本任务）。
- AC refs: AC-FR0332-01
- FR refs: FR-0332
- IF ids: IF-REVIEW-001
- Unit refs: -
- Acceptance refs: tests/integration/test_lex_review_park.py::test_pass_pending_human_threads_parks_and_lists
- Scope: tracks/effects/opencode_review.py, tracks/kernel/machine_verdicts.py, tracks/kernel/machine.py, tracks/cli/status_cmd.py, tracks/executor/result_payload.py, tracks/executor/result_audit.py, tracks/cli/run_cmd.py
- Depends on: -
- Batch: 1
- Parallel: True

## T-010b
- Issue: #183
- Description: LEX_REVIEW findings 线程跨 attempt 复用——注入切片（AC-FR0332-02，IF-REVIEW-001 §1s.5，#183）：LEX_REVIEW（含重进）的 dispatch assignment 注入目标文档 open_threads 清单（thread_id/status=open|reopen/initiator/awaiting/anchor 摘要，tracks/discuss 解析产生）——dispatch.py 注入面（派发前解析文档 open 线程并物化进 assignment；跨 attempt 重进注入稳定不新开重复线程）、tracks/agents/Lex.md §1s.5 agent 纪律文档面（findings 续写必须在既有线程内 reply，不逐轮新开）。PP7-R3-B01 重组（round 4，Prism PRISM_PLAN seq 47771）：管线三文件（result_payload/result_audit/run_cmd）随锚点 A 归 T-010——park no-diff 豁免的机械位置在 executor 检查点管线（result_payload requires_diff 策略 + result_audit 硬失败放行），run_cmd.py 仅承载恢复面准入（PP7-R3-A01 订正：前版描述把 no-diff 豁免误写进 run_cmd 职责，均不在本任务写域）；本任务写域收敛为 dispatch.py+Lex.md。锚 test_findings_threads_reused_across_attempts 不依赖 park 机制（驱动 revise verdict 路径，HEAD 上已推进至 :235 注入断言、仅注入面缺失），在 HEAD 110c532 实测红（:235 open_threads is None in both recorded assignments），标准 RGR 交付；stash@{0} 仅作行为参考、禁止 pop。depends_on T-010 保留切片序（batch 2）；T-011 union scope 与全体任务写域并集仍闭合（本任务与 T-010 合计仍为原 9 文件，无孤儿）。
- AC refs: AC-FR0332-02
- FR refs: FR-0332
- IF ids: IF-REVIEW-001
- Unit refs: -
- Acceptance refs: tests/integration/test_lex_review_park.py::test_findings_threads_reused_across_attempts
- Scope: tracks/executor/dispatch.py, tracks/agents/Lex.md
- Depends on: T-010
- Batch: 2
- Parallel: False

## T-011
- Issue: #184
- Description: 终局集成收口（integration task，B94）：union scope 授权（集成接线修复的写域豁免来自全部任务 scope 并集）；硬门禁=全图 deferred 并集（tests/integration/test_docs_center.py、test_workbench_shell.py、test_event_stream.py、test_version_gate.py、test_hotfix_precheck_classification.py 的文件级镜像）在收口时刻全绿——§8.1 五个到达即绿守卫（讨论无写回端点、版本合同声明+registry 重锚、不存在 issue 继承行为、ui 静态纪律、ui-e2e 基础设施声明）经 IF-GREENGUARD-001 的 guard_verified/fail-closed 语义持续验证，SSE 重连继承锚点保持绿；RED 豁免、REFACTOR 与质量门禁不豁免。AC-FR0331-01/02 在本任务登记（IF-TRACKER-001 的集成闭合面：ensure 原语（T-006）+ 关闭链接线与守卫（T-007）+ 收口硬门禁构成 tracker milestone 生命周期的整体验收；两锚点的执行分别由 T-006/T-007 转绿，本任务收口其集成闭合——共位例外按 PLAN-03 在此写明）。；追加 plan_defect（seq 48472）收口职责：app.py 组装根挂载文档中心编辑面 #35——一行路由表追加消费 api_command.EXTENSION_ROUTES（T-001 既有 api_query 侧缝仅声明 #31-34 只读端点；app.py 仅本任务 union scope 可写：T-001 已完成冻结、非集成任务持有即 scope overlap 硬拒）。锚 test_save_produces_revision_and_stale_approval_rejected 与 AC-FR0323-01（FR-0323/IF-DOCSAVE-001）自 T-004 随写域迁移登记于本任务（锚-AC 共位，最后可绿 owner）；T-004 的 replay WIP（api_command.py/service.py：docs_revision 纯函数、revision_kind 参数面、#35 处理器与 EXTENSION_ROUTES 声明、409 顶层 current_revision）随本任务 GREEN 的 seeded WIP 进入 lineage 提交（承接 T-004 verification-only 的无提交通道，v0.7 T-016 先例）。GREEN 门 = 本锚 202 + 五文件 deferred 并集全绿。追加 plan_defect（PRISM_FINAL PF2-B03，失败组 2-archer-67）收口职责：login/name 流 + CSRF 播种的服务端缝。实证缺口：pages.py 的 login 壳（_login_body）无 type=module 引导、登录为原生表单 POST 至 JSON 端点（csrf_token 随 §2b #1 JSON 响应返回但永不到达浏览器侧）、testid 用 login-name/login-name-continue 而冻结 e2e 合同（tests/e2e/test_workbench_ui.py:28-30）钉的是 name-input/name-submit；api.js setCsrfToken 因此无调用方，一切 UI 写携空 X-Trac-CSRF 被 auth.py check_csrf 403，全部冻结旅程（含 AC-FR0320-01/AC-FR0321-01/AC-NFR0154-02 横切面）死于 name 步。缝落在 pages.py——T-002 已完成冻结（GREEN 7a6f7f1）、非集成任务再持有即 scope overlap 硬拒，仅本任务 union scope 可写（Prism 裁定给出两个合法出口：extend T-005 scope 或 T-011 union scope；取后者——T-001/T-002 均已完成冻结、剥已完成任务 scope 无先例，集成接线修复本是 union scope 豁免的存在理由）。落地内容：login 壳 testid 对齐冻结合同（name-input/name-submit）、追加 module 引导使登录页进入 JS 流（name_required=true 时就地展开名字步、无整页跳转，§1m.1 行内错误由 JS 流承载）。消费侧（static/app 的登录/名字 JS 流与 setCsrfToken 调用方）属 T-005 B01/B02 修复轮写域，本任务只落 pages.py 缝；app.py/auth.py 无需变更（登录响应已携 csrf_token，check_csrf 语义不变）。验收由冻结 e2e 旅程在 ISLAND_GATE_2/FULL 承载——§8 无对应 integration 行，不新增锚点、不动任何任务的 anchor/scope/deps。追加 plan_defect（seq 4376）闭口：walk_red 要求 integration 入口声明 unit pins 以密封 R 树——unit_refs 补 tests/unit/test_t005_static_app_red.py（全图唯一在树 unit RED 面，随 T-005 交付已绿；执行器 #225 同船豁免 E1 规则 1 对 integration 任务 unit_refs 的误拒——walk_red 契约上 unit pins 按设计即绿，探测记录保留为规划期信号）。本手术即 T-005 重定义注记所述「T-011 unit_refs 手术」。
- AC refs: AC-FR0331-01, AC-FR0331-02, AC-FR0323-01
- FR refs: FR-0331, FR-0323
- IF ids: IF-TRACKER-001, IF-DOCSAVE-001
- Unit refs: tests/unit/test_t005_static_app_red.py
- Acceptance refs: tests/integration/test_docs_center.py::test_save_produces_revision_and_stale_approval_rejected
- Scope: tracks/server/auth.py, tracks/server/app.py, tracks/supervisor/db.py, tracks/server/pages.py, tracks/server/static/styles.css, tracks/server/api_query.py, tracks/server/projections.py, tracks/server/api_command.py, tracks/supervisor/service.py, tracks/server/static/app, tracks/effects/github.py, tracks/executor/milestone_chain.py, tracks/executor/host_contract.py, tracks/executor/verify_version.py, tracks/executor/hotfix_face.py, tracks/executor/hotfix.py, tracks/effects/opencode_review.py, tracks/kernel/machine_verdicts.py, tracks/kernel/machine.py, tracks/cli/status_cmd.py, tracks/agents/Lex.md, tracks/executor/dispatch.py, tracks/executor/result_payload.py, tracks/executor/result_audit.py, tracks/cli/run_cmd.py
- Depends on: T-001, T-002, T-003, T-004, T-005, T-006, T-007, T-008, T-009, T-010, T-010b
- Batch: 5
- Parallel: False
