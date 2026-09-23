---
envelope: tracks-envelope:v2
architecture_id: ARCH-009
spec_ref: SPEC-009
created: 2026-09-23
status: draft
sha:
---

# v0.9 — 架构：Web 后台持续驱动与最小工作台

本文是 ARCH-008 的增量延伸。v0.1～v0.8 的事件溯源、单写者、canonical 阶段状态机、发布闭环五阶段、质量守卫 registry、测试流水线与既有 CLI 全部保持不变；v0.9 在此之上新增两个顶层包——`tracks/server/`（Web 交付面）与 `tracks/supervisor/`（后台驱动控制层），以及 executor 轴上的结构化驱动步 `tracks/executor/drive.py`。核心收益是运行控制的持久化：命令先持久化再执行、supervisor 沿既有阶段状态机自动推进、外部等待可持久化并可跨重启恢复、人工决定在 Web 面完成且全部复用既有校验。server 不拥有绕过 quality/security/approval 的快捷路径，不复制第二套状态机。

revision 注记：本文 2026-09-23 经 M-DESIGN DRAFT 重起草，合同面与 2026-09-18 revision 无变更——模块边界、§1.2 六元组 closure（74 条 required AC）、Scaffold 宣言、§4.2 guard registry 与全部 IF 引用保持一致；本设计已经 M-IMPL 全量实现与 FULL 套件验证，无可归因设计缺陷（前序失败证据 1-devon-87 经诊断为不可复现的崩溃残留记录）。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-008

- 唯一生产路径 `cli -> kernel.machine.decide -> executor.execute -> store.append`、SQLite append-only events、投影可重建、单写者 writer_lock 与 per-kind reconcile 不变；**tracks.db 的 EVENT_TYPES 封闭集本版不追加任何成员**（kernel reducer 输入集零改动，SM-02.8）；v0.9 新事件类型全部落在服务面 `service_events` 日志（interfaces §1a/§1c）。
- `kernel/` 纯控制、`executor/` 副作用、`checks/` 静态闭合、`effects/` 外部通道、`adapters/` 宿主测试框架语义边界与 `cli/` 交付面的分层不变；v0.9 新增 `tracks/server/`、`tracks/supervisor/` 两个顶层包与 `tracks/executor/drive.py`、`tracks/cli/serve_cmd.py` 增长点（§1.0.1）。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]/[nightly]/[adapter]/[layout]/[lint]/[host-contract.*]` 各段逐字不变；测试执行合同（三层 collect/run/run_selected、`{nodes}`/`{result}` 所有权）不变。
- 既有 `trac init/start/hotfix/run/triage/review/approve/return/recover/retry/status/replay/report/validate/discuss/check/release/abandon` 语义与 USAGE 不变；唯一新增顶层子命令是 serve（**待实现 Devon foundation task**：交付 `tracks/cli/serve_cmd.py:cmd_serve` 并同步 USAGE 与 `_COMMANDS`；本文以散文形式引用该待实现命令，同 v0.8 release 先例）。
- v0.1～v0.8 全部 IF 标识不可重定义或复用；IF-009 只追加新标识（interfaces §5）。
- 发布闭环（M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH→M-MILESTONE）、candidate 主身份、preview digest 绑定与过期拒绝、publish 幂等/reconcile、escape barrier/迟到结果隔离、就地修复/Known Issue、envelope v2 与 parity、失败证据链、Issue 闭环、reference host 与三旅程合同全部继承；Web 面只是这些既有合同的新交付面（interfaces §1b #6/7/10/11/12）。
- wheel 安装、隔离 venv、源码树外 cwd、GitHub Actions stable required checks（lint/coverage/test/deliverables/trace/reach）与 live channel 三层机制（L1/L2 默认 CI、L3 milestone/weekly）不变；`release-evidence` milestone 硬门禁不变。
- 质量守卫八类与 canonical registry 机制不变；本版仅因 pyproject.toml 内容变化（首个运行时依赖 + server 资产打包）重算六条 pyproject-backed config_digest（§4.2），`.flake8` 与 project.toml 的 digest 不变。

### 0.2 v0.9 变更

- 新增 `tracks/server/` 包：Starlette 应用工厂（Web composition root）、本机单用户认证/会话/CSRF、命令防护与访问范围、出口统一脱敏、HTTP API（命令/查询/事件订阅）、页面壳与 Vditor 宿主、只读投影。web 层不直接耦合 runtime：全部变更经命令服务受理，全部读取经投影（interfaces §1b/§1e）。
- 新增 `tracks/supervisor/` 包：持久命令服务（CLI/Web 共用）、service.db（服务面 SQLite：service_events/projects/commands/waits/leases/auth/sessions/schedule）、租约/代次防旧、单活动 run 调度与 hotfix 换队、外部等待（含 quota）持久化与有界退避、worker 子进程管理、启动恢复、就绪探测。
- 新增 `tracks/executor/drive.py`：`drive_once` 结构化单步驱动（continue/await_human/await_external/terminal/failed 五态），supervisor 借此沿既有阶段状态机推进；`cmd_run` 的 CLI 可观察行为不变（内部复用属实现自由）。
- `pyproject.toml`：首个运行时依赖 `starlette==0.49.3`、`uvicorn==0.49.0`（§3.1）；package-data 追加 `server/static/**`（§3.4）。版本字段不变（0.8.0）——发布期统一升版是既有惯例（v0.8 在 release commit 升版），本设计不在设计期升版。
- `tracks/server/static/vendor/vditor/**`：Vditor 3.11.3 最小离线子集 + manifest.json（逐文件 sha256），自源站提供，禁用未 vendor 的可选渲染引擎（interfaces §2b）。
- 无数据库迁移框架：service.db 为新建独立 SQLite 文件（`CREATE TABLE IF NOT EXISTS`），tracks.db schema 不动。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | v0.9 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `tracks/cli/serve_cmd.py` | 新增 cmd_serve（服务生命周期入口；USAGE/_COMMANDS 注册为待实现 foundation task） | FR-0288 | IF-SERVE-001 |
| 2 | `tracks/server/app.py` | Starlette 应用工厂与 Web composition root（装配 db/service/supervisor/auth/路由/静态面） | FR-0288、FR-0301～0307 | IF-SERVE-001 |
| 3 | `tracks/server/auth.py` | 口令供给（scrypt）、会话、CSRF、裁决权属入口校验 | FR-0314、FR-0293 | IF-WEBAUTH-001 |
| 4 | `tracks/server/guard.py` | 封闭 kind 校验、actor_class 强制、路径范围防护 | FR-0314、FR-0315 | IF-CMDGUARD-001, IF-SECRECY-001 |
| 5 | `tracks/server/redaction.py` | 出口统一脱敏（API/SSE/HTML/日志） | FR-0315 | IF-SECRECY-001 |
| 6 | `tracks/server/api_command.py` | 变更类 HTTP 路由 → 命令服务 | FR-0291～0295、0308～0313 | IF-CMDSVC-001, IF-WEBGATE-001 |
| 7 | `tracks/server/api_query.py` | 只读 HTTP 路由 → 投影（含 /healthz、/api/service/config） | FR-0288、0301～0305、0307、NFR-0152 | IF-QUERY-001, IF-SERVE-001 |
| 8 | `tracks/server/api_events.py` | SSE 事件订阅与游标补读 | FR-0306 | IF-STREAM-001 |
| 9 | `tracks/server/pages.py` | 页面壳（E-01..E-08）与 Vditor 宿主 | FR-0294、FR-0301～0307 | IF-DOCREV-001 |
| 10 | `tracks/server/projections.py` | 只读读模型（总览/详情/进度/AC 链/待办/时间线/执行状态/配置） | FR-0301～0307 | IF-QUERY-001 |
| 11 | `tracks/supervisor/db.py` | service.db schema 与 SERVICE_EVENT_TYPES 封闭集、追加只读日志 | FR-0288、0295、0296、0300 | IF-CMDSVC-001, IF-RECOVER-001 |
| 12 | `tracks/supervisor/service.py` | CommandService：受理/持久化/幂等去重/actor_class；CLI 同路复用 | FR-0291、0292、0295、0310、0311、0313 | IF-CMDSVC-001, IF-WEBGATE-001 |
| 13 | `tracks/supervisor/lease.py` | 租约/代次、完成 CAS、迟到结果隔离 | FR-0296 | IF-LEASE-001 |
| 14 | `tracks/supervisor/scheduler.py` | 单活动 run 调度、hotfix 换队序列 | FR-0296 | IF-SCHED-001 |
| 15 | `tracks/supervisor/waiting.py` | 外部等待持久化、quota 信号分类、NFR-0152 退避 | FR-0298、0299、NFR-0152 | IF-WAIT-001 |
| 16 | `tracks/supervisor/worker.py` + `worker_main.py` | worker 子进程认领/驱动/回收、pause 生效边界、防旧终止 | FR-0296、0297、0300、0310 | IF-DRIVE-001, IF-LEASE-001, IF-PAUSE-001 |
| 17 | `tracks/supervisor/recover.py` | 启动恢复（重认领/等待保留/租约过期/既有 WAL reconcile 衔接） | FR-0288、0300 | IF-RECOVER-001 |
| 18 | `tracks/supervisor/readiness.py` | 四类就绪探测（合同/harness·model/凭据引用/必需工具） | FR-0290 | IF-PROJ-001 |
| 19 | `tracks/executor/drive.py` | drive_once 结构化单步（五态 DriveResult） | FR-0297、0298、0299 | IF-DRIVE-001 |
| 20 | `pyproject.toml` | 首个运行时依赖、server 资产 package-data | 全部 | §4.2 registry |
| 21 | `tracks/server/static/vendor/vditor/**` | Vditor 3.11.3 最小离线子集 + manifest | FR-0294 | IF-DOCREV-001 |

### 1.0.2 命令服务：CLI 与 Web 同一受理面（FR-0295）

`tracks/supervisor/service.py:CommandService` 是全部变更命令的唯一受理面：认证/actor_class → 命令防护（封闭 kind + schema）→ 幂等键查重 → 持久化（command.accepted）→ 执行。CLI 与 Web 的差别只在执行形态：CLI 受理后在既有 writer_lock 内同步执行（既有行为不变）；HTTP 受理即返回 command_id，由 supervisor worker 在边界执行（不在 HTTP handler 内跑长任务）。cmd_approve/cmd_release/cmd_return/cmd_abandon/cmd_retry/cmd_start/cmd_hotfix 的业务校验核心抽为共享函数供两面调用（Devon 的重构自由度），合同保证：同一 kind 经 CLI 或 HTTP 提交产生等价事件类型与业务效果（AC-FR0295-03）；Web 面禁止直接 append human.approval 等域事件（复用既有校验，story §3.3）。

### 1.0.3 supervisor：租约、调度与 worker（FR-0296/0297/0310）

- **驱动**：调度器只对活动 run 派发 drive_run；worker 子进程循环 drive_once 至边界（await_human/await_external/terminal/failed）或 pause 生效点。kernel.machine.decide 仍是唯一合法转移来源——supervisor 不复制状态机，只决定何时调用下一步、等待什么、何时重试/续租/恢复。
- **租约/代次**：leases 表单行 per run；代次在 SQLite 事务内单调递增；命令认领/完成携带代次，完成经 CAS，旧代次结果落 worker.late_result(quarantined) 且不影响 run 状态；租约过期先 SIGTERM（宽限）后 SIGKILL 旧 worker，再授新代次。
- **单活动 + 换队**：schedule 单行表（active_run + 有序 queue）；hotfix 紧急场景经显式可审计换队（pause 活动 run → 激活 hotfix → hotfix 终态后 resume），全程经命令服务受理留审计；preempt=false 时拒绝（active_run_exists）。真并发不实现（范围排除）。
- **pause 两阶段**：run.pause_requested（已接收，落盘即阻断新驱动与自动唤醒）→ worker 到边界后 run.paused（已暂停）；resume 后从原合法位置继续。

### 1.0.4 外部等待与 quota（FR-0298/0299/NFR-0152）

drive_once 返回 await_external 时，waiting 模块将失败分类为 wait_class 封闭集（ci/quota/network/agent/external）：harness 返回的 provider 配额/限流响应可解析出恢复时刻 → known_reset=true + retry_at 精确唤醒；不可解析 → 有界退避探测（初始 60s、上限 900s，服务配置项），不编造倒计时、无进展不热循环。等待行持久化于 waits 表，服务重启后保留（IF-RECOVER-001）。等待不进入人工待办（FR-0307）。UI 实时通道优先推送；断开回退轮询 5s、空闲上限 60s（同配置）。

### 1.0.5 只读投影与事件订阅（FR-0301～0307）

`tracks/server/projections.py` 现场计算全部读模型：tracks.db 只读连接（run 面事件/State 投影）+ service.db 只读连接（命令/等待/租约/调度）+ 项目文档（AC 登记处）。进度双计数分列（测试数 ≠ 需求闭合数），不合成百分比；诊断/待办与 supervisor 同一事件源，不维护只在内存中的"运行中"事实。SSE（api_events.py）按 (ts, source_rank, seq) 合并序推送，游标补读；快照 cursor 与事件一致，重复/乱序不倒退；轮询回退共用同一数据路径。读路径永不取 writer_lock、独立事务、即时返回。

### 1.0.6 认证、防护与脱敏（FR-0314/0315）

本机单用户口令（首次启动供给，scrypt 存储）→ 登录签会话（HttpOnly+SameSite=Strict Cookie + 随会话 CSRF，服务端只存散列）。除 /healthz 与登录面外全部页面/API 需认证；变更端点需 CSRF 头。命令防护：HTTP 只接受封闭 kind 结构化 JSON，任意 shell 载荷拒绝；human-only kind 的 actor_class 由服务端从会话推导（不接受客户端自报），非人身份尝试落 command.rejected(forbidden_actor)+access.denied 可审计。裁决权属：请求 Human/指定评审者裁决的讨论线程仅被请求方经认证入口可 resolved（interfaces §1f.4）。脱敏：redaction 在全部出口统一过滤凭据值与密钥模式；文件/产物读取限于授权项目及证据范围（realpath 前缀校验）。

### 1.0.7 材料审阅与 Vditor 基础集成（FR-0294）

文档页（E-06）经 Vditor 3.11.3 vendored 资产承载查看/编辑/版本对比；资产自源站 /static/vendor/vditor/ 提供（禁 CDN），未 vendor 的可选渲染引擎在集成配置禁用。Web 编辑经命令服务 edit_material 走既有修订流程：产生新 revision（material.edited 审计 + Runtime 提交）、待批准条目更新、对旧 revision 的批准按既有 staleness 拒绝——不绕过 FR-0308 绑定。revision 标识沿用 baseline.revision_digest 语义。

### 1.0.8 角色所有权

| # | 职责 | Archer | Shield | Devon | Prism | Runtime |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | 本设计三文档、接口桩、vendored 资产、pyproject/registry 合同 | ✅ 独家设计/物化 | ❌ | ❌ | 评审 | 校验/生效 |
| 2 | tests/integration、tests/e2e、tests/_support（serve fixture、SSE 客户端、canary 密钥） | ❌ | ✅ 独家 | ❌ | 评审 | collect/run |
| 3 | server/supervisor/drive/serve_cmd 行为体、UI 静态面（app.js/styles.css/页面模板）、guard/redactor 行为体、讨论裁决权属校验接入 | ❌ | ❌ | ✅ | 评审 | 门禁/提交 |
| 4 | 命令执行、副作用、发布、lease 授予的事实发生 | ❌ | ❌ | ❌ | ❌ | ✅ 程序独家 |
| 5 | 批准/发布三择一/pause/resume/abandon/回拨/重试的决定 | Human 独占（认证 Web 入口或既有 CLI） | ❌ | ❌ | ❌ | 校验/落事件 |
| 6 | 裁决线程 resolved（被请求方） | 被请求 Human/评审者 | ❌ | ❌ | ❌ | 校验/审计 |

### 1.1 Composition Root

Web 服务装配入口（trac serve 路径；serve 子命令为待实现 foundation task，散文引用）：

```text
cmd_serve（tracks/cli/serve_cmd.py，入口 handler，待实现）
  -> 解析 flags（--repo 许可范围/--host/--port/--home/等待与轮询配置）+ 口令供给
  -> supervisor/db.py ServiceDB(home)（建表 IF NOT EXISTS；WAL）
  -> supervisor/recover.recover_on_startup（command.requeued/等待保留/租约过期）
  -> supervisor/service.CommandService(home, db, config)
  -> supervisor/scheduler.Scheduler(db) + supervisor/worker.WorkerManager(db, scheduler, config)
  -> server/app.create_app(home, service, supervisor, config)
       （auth 中间件 + redactor + api_command/api_query/api_events 路由 + pages + /static）
  -> uvicorn 运行 app（serve 进程内 supervisor 为后台任务；worker 为子进程）
```

驱动路径（自动推进）：

```text
supervisor 后台任务 -> scheduler.next_runnable
  -> lease.acquire_lease(run_id, generation)
  -> worker.spawn(command=drive_run) -> 子进程 worker_main
  -> executor/drive.drive_once -> 既有 run_loop 机制 -> kernel.machine.decide
  -> 边界：await_human（停在人工门）| await_external（waiting.enter_wait 持久化 retry_at）
       | terminal | failed -> command.completed/failed（经 lease CAS）
```

人工命令路径（Web）：

```text
POST /api/runs/{id}/approvals -> api_command.record_approval
  -> auth.resolve_session + check_csrf -> guard.validate_command_payload + check_actor_class(human)
  -> CommandService.accept(surface=http) -> 幂等查重 -> command.accepted 持久化 -> 202 command_id
  -> worker 认领执行：复用 cmd_approve 共享校验（expected_revision 绑定）-> human.approval（tracks.db）
```

只读路径：

```text
GET /api/... -> api_query.* -> projections.project_*（tracks.db/service.db 只读连接）
GET /api/runs/{id}/events?after=<cursor> -> api_events.stream_events（SSE/轮询共用合并事件源）
```

### 1.2 Required AC closure (ISLAND_GATE_1)

- **FR-0288** owner=tracks/cli/serve_cmd.py+tracks/server/app.py:AC-FR0288-01 surface=serve-CLI+GET-/healthz+工作台页面 composition=create_app 装配（§1.1） wiring=trac serve→cmd_serve→ServiceDB/recover→create_app→uvicorn；浏览器关闭期间 worker 子进程继续 drive_once→stage.entered 落 tracks.db test=integration+e2e:tests/integration/test_serve_lifecycle.py::test_health_and_browserless_progress+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_serve_lifecycle.py`输出`passed`且健康端点返回`"status":"ok"`与`"projects":<n>`、浏览器零交互期间出现新`stage.entered` IF-SERVE-001
- **FR-0288** owner=tracks/supervisor/recover.py:AC-FR0288-02 surface=serve-CLI+GET-/api/projects+GET-/api/commands/{id} composition=recover_on_startup 装配于 cmd_serve 启动序 wiring=停止→重启→recover_on_startup→projects/commands/waits 表保留→command.requeued(reason=service_restart)→GET /api/commands/{id} 可查原结果 test=integration:tests/integration/test_serve_lifecycle.py::test_restart_recovers_registry_commands_waits evidence=`.venv/bin/python -m pytest -q tests/integration/test_serve_lifecycle.py`输出`passed`且重启后登记项目仍在、claimed 命令被 requeue、waits 行 retry_at 不变 IF-SERVE-001 IF-RECOVER-001
- **FR-0288** owner=tracks/server/api_query.py:AC-FR0288-03 surface=GET-/healthz+serve-stdout composition=health_response 装配 wiring=注入不可用依赖（service.db 目录只读）→healthz 503 reasons 含具体原因→serve stdout 打印原因→工作台页面不呈现可用 test=integration:tests/integration/test_serve_lifecycle.py::test_unhealthy_no_fake_available evidence=`.venv/bin/python -m pytest -q tests/integration/test_serve_lifecycle.py`输出`passed`且 503 payload 的 reasons 为非空列表、每项含 check 与 reason 字段、无 200 假象 IF-SERVE-001
- **FR-0289** owner=tracks/supervisor/service.py:AC-FR0289-01 surface=POST-/api/projects+总览/详情页 composition=CommandService.accept(register_project) wiring=UI 登记→guard.check_path_scope（许可范围内）→project.registered→归属随全部 run 操作面返回→重启后仍在 test=integration+e2e:tests/integration/test_project_registry.py::test_register_shows_attribution_and_survives_restart+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_project_registry.py`输出`passed`且 projects 行含 repo_path+version、重启后一致 IF-PROJ-001
- **FR-0289** owner=tracks/server/guard.py:AC-FR0289-02 surface=POST-/api/projects composition=check_path_scope 装配 wiring=越范围路径→realpath 前缀校验失败→project.registration_rejected(reason=outside_permitted_scope)→403；未登记项目创建 run→404/422 且不产生 run test=integration:tests/integration/test_project_registry.py::test_out_of_scope_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_project_registry.py`输出`passed`且拒绝事件可审计、无假 run IF-PROJ-001 IF-SECRECY-001
- **FR-0290** owner=tracks/supervisor/readiness.py:AC-FR0290-01 surface=POST-/api/projects/{pid}/readiness+E-03 页 composition=run_readiness 四探针 wiring=check_readiness→contract/harness_model/credentials_ref/tools 逐项探测→project.readiness_checked(ok,checks,reason)→页面逐项展示 test=integration+e2e:tests/integration/test_readiness.py::test_readiness_items_displayed+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_readiness.py`输出`passed`且四项 checks 各含 ok 与 reason 字段 IF-PROJ-001
- **FR-0290** owner=tracks/supervisor/readiness.py:AC-FR0290-02 surface=就绪页+新建 run 入口 composition=同上 wiring=注入缺失工具→tools.ok=false+reason→create_run 前置失败→command.rejected(validation_failed)→不产生 run；修复后重查转就绪 test=integration:tests/integration/test_readiness.py::test_not_ready_blocks_run_creation evidence=`.venv/bin/python -m pytest -q tests/integration/test_readiness.py`输出`passed`且拒绝含具体原因、runs 表无新增 IF-PROJ-001
- **FR-0291** owner=tracks/supervisor/service.py:AC-FR0291-01 surface=POST-/api/projects/{pid}/runs+E-02 总览 composition=accept(create_run) wiring=提交 story+版本→幂等持久化→202 {command_id,run_id}→worker 建 run→总览列表出现（待澄清/待推进） test=integration+e2e:tests/integration/test_run_create_web.py::test_create_feature_run_returns_ids+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_create_web.py`输出`passed`且 command_id 持久可查 IF-CMDSVC-001
- **FR-0291** owner=tracks/supervisor/service.py:AC-FR0291-02 surface=POST-/api/projects/{pid}/runs composition=同上 wiring=校验失败→command.rejected(validation_failed)+无 run；同幂等键重复提交→command.deduplicated→同一 command_id/run_id test=integration:tests/integration/test_run_create_web.py::test_failed_and_duplicate_submit_no_fake_run evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_create_web.py`输出`passed`且重复提交前后 run 数不变 IF-CMDSVC-001
- **FR-0292** owner=tracks/supervisor/service.py:AC-FR0292-01 surface=POST-/api/projects/{pid}/runs(hotfix_post/hotfix_dev) composition=accept(create_run) 复用 v0.8 hotfix 前检 wiring=Web 入口→既有 hotfix 前检→两类旅程至发布/终态；dev 无公开 tag/release test=e2e:tests/e2e/test_web_hotfix_journeys.py::test_post_release_hotfix_web_journey+test_dev_hotfix_web_journey evidence=`.venv/bin/python -m pytest -q tests/e2e/test_web_hotfix_journeys.py`输出`passed`且 post-release 有 patch tag、dev 仅 prerelease（channel=pre-release） IF-CMDSVC-001 IF-JOURNEY-001
- **FR-0292** owner=tracks/supervisor/service.py:AC-FR0292-02 surface=POST-/api/projects/{pid}/runs composition=同上 wiring=前检不满足（如无活跃 release 分支起 dev）→command.rejected(validation_failed)+具体原因→不产生 run test=integration:tests/integration/test_run_create_web.py::test_hotfix_precheck_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_create_web.py`输出`passed`且无 fix 分支、无 run 副作用 IF-CMDSVC-001 IF-HOTFIX-001
- **FR-0293** owner=tracks/server/api_command.py:AC-FR0293-01 surface=POST-/api/runs/{id}/clarifications+E-06 composition=accept(submit_clarification) wiring=回复→tracks/discuss writer 追加到原文档线程→待办清除→supervisor 续推同一 run（run_id 不变） test=integration+e2e:tests/integration/test_clarify_web.py::test_reply_continues_same_run+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_clarify_web.py`输出`passed`且回复落原文档、无新 run IF-CMDSVC-001 IF-DOCGAP-001
- **FR-0293** owner=tracks/server/projections.py:AC-FR0293-02 surface=E-06 讨论历史+docs API composition=project_timeline/docs 读模型 wiring=提问/回复/时间/操作者经文档内容与线程 token 可回溯 test=integration:tests/integration/test_clarify_web.py::test_discussion_history_traceable evidence=`.venv/bin/python -m pytest -q tests/integration/test_clarify_web.py`输出`passed`且历史含操作者与时间 IF-DOCGAP-001
- **FR-0294** owner=tracks/server/api_query.py:AC-FR0294-01 surface=GET-/api/runs/{id}/docs/{doc}+E-06 composition=read_doc 读模型 wiring=返回当前 content+revision 标识+历史；待批准条目 revision 与实际审阅一致 test=integration:tests/integration/test_doc_review_web.py::test_revision_visible_and_pending_bound evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_review_web.py`输出`passed`且 revision 与 revision_digest 一致 IF-DOCREV-001
- **FR-0294** owner=tracks/server/pages.py:AC-FR0294-02 surface=E-06 页面+GET-.../diff composition=Vditor 宿主+doc_diff wiring=相邻 revision unified_diff 可见；注入修订后界面显示新 revision；页面自源站引用 /static/vendor/vditor/（无 CDN） test=integration:tests/integration/test_doc_review_web.py::test_diff_between_revisions_visible evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_review_web.py`输出`passed`且 diff 非空、资产 URL 同源 IF-DOCREV-001
- **FR-0294** owner=tracks/supervisor/service.py:AC-FR0294-03 surface=POST-/api/runs/{id}/docs/{doc}/edits composition=accept(edit_material) wiring=编辑→既有修订流程→material.edited(from_revision→to_revision)+Runtime 提交→待批准条目更新→旧 revision 批准按 stale_revision 拒绝 test=integration:tests/integration/test_doc_review_web.py::test_edit_produces_new_revision_stale_approval_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_review_web.py`输出`passed`且新旧 revision 不同、旧批准被拒 IF-DOCREV-001 IF-WEBGATE-001
- **FR-0295** owner=tracks/supervisor/service.py:AC-FR0295-01 surface=全部变更端点+GET-/api/commands/{id} composition=accept 持久化先行 wiring=提交→command.accepted（先持久化）→返回 command_id→服务重启后该 command_id 及结果仍可查询 test=integration:tests/integration/test_command_service.py::test_command_id_survives_restart evidence=`.venv/bin/python -m pytest -q tests/integration/test_command_service.py`输出`passed`且重启后 status/result 一致 IF-CMDSVC-001
- **FR-0295** owner=tracks/supervisor/db.py:AC-FR0295-02 surface=全部变更端点 composition=幂等键唯一约束 wiring=同键同 payload（含 HTTP 超时重试/双击）→command.deduplicated→只一次业务效果；同键异 payload→409 idempotency_conflict test=integration:tests/integration/test_command_service.py::test_idempotency_dedup_and_conflict evidence=`.venv/bin/python -m pytest -q tests/integration/test_command_service.py`输出`passed`且 run/批准/pause 各仅一次 IF-CMDSVC-001
- **FR-0295** owner=tracks/supervisor/service.py:AC-FR0295-03 surface=CLI+HTTP 双入口 composition=同一 accept 路径 wiring=同 kind（如 record_stage_approval）分别经 CLI 与 HTTP 提交→事件类型与业务效果等价（surface 字段区分来源） test=integration:tests/integration/test_command_service.py::test_cli_http_equivalent_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_command_service.py`输出`passed`且两路径事件类型序列一致 IF-CMDSVC-001
- **FR-0295** owner=tracks/server/projections.py:AC-FR0295-04 surface=全部 GET 端点 composition=只读读模型 wiring=查询前后 tracks.db 事件计数与 service_events 计数不变；查询不取 writer_lock test=integration:tests/integration/test_command_service.py::test_queries_are_readonly evidence=`.venv/bin/python -m pytest -q tests/integration/test_command_service.py`输出`passed`且事件数不变 IF-CMDSVC-001 IF-QUERY-001
- **FR-0296** owner=tracks/supervisor/lease.py:AC-FR0296-01 surface=并发触发+时间线 composition=lease 单代次 wiring=并发请求+并发两实例推进同一 run→代次 CAS 只一胜者→无重复 dispatch/publish（tracks.db 无重复阶段转移、发布副作用一次） test=integration:tests/integration/test_supervisor_lease.py::test_concurrent_drive_single_dispatch evidence=`.venv/bin/python -m pytest -q tests/integration/test_supervisor_lease.py`输出`passed`且无重复 dispatch 事件 IF-LEASE-001
- **FR-0296** owner=tracks/supervisor/lease.py:AC-FR0296-02 surface=租约过期注入+时间线 composition=完成 CAS 防旧 wiring=旧 worker 代次过期→新 worker 获新代次→旧代次完成 CAS 失败→worker.late_result(quarantined)→run 状态不受影响 test=integration:tests/integration/test_supervisor_lease.py::test_stale_generation_late_result_quarantined evidence=`.venv/bin/python -m pytest -q tests/integration/test_supervisor_lease.py`输出`passed`且 late_result 事件可审计、状态投影无变化 IF-LEASE-001
- **FR-0296** owner=tracks/supervisor/scheduler.py:AC-FR0296-03 surface=总览/详情排队态 composition=schedule 单行表 wiring=第二已受理 run→queue 成员→投影 control_state=排队中→不被驱动 test=integration:tests/integration/test_hotfix_swap.py::test_second_run_queued evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_swap.py`输出`passed`且排队 run 无 drive_run 派发 IF-SCHED-001
- **FR-0296** owner=tracks/supervisor/scheduler.py:AC-FR0296-04 surface=hotfix 提交+时间线 composition=换队序列 wiring=create_run(hotfix,preempt=true)→pause_run(活动 feature)→schedule.changed(hotfix_preemption)→hotfix 至发布→resume_run(feature)→各步命令行含 actor+command_id；期间无双重 dispatch test=integration:tests/integration/test_hotfix_swap.py::test_hotfix_preemption_audited evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_swap.py`输出`passed`且换队事件链完整、feature 从原合法位置继续 IF-SCHED-001 IF-PAUSE-001
- **FR-0297** owner=tracks/supervisor/worker.py+tracks/executor/drive.py:AC-FR0297-01 surface=E-05 观察+事件流 composition=worker 循环 drive_once wiring=澄清与批准完成后零人工命令→drive_once 沿既有阶段状态机推进至人工门/终态→tracks.db 出现连续 stage.entered/exited test=integration+e2e:tests/integration/test_auto_drive.py::test_auto_advance_stages+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_auto_drive.py`输出`passed`且推进轨迹可 replay IF-DRIVE-001
- **FR-0297** owner=tracks/executor/drive.py:AC-FR0297-02 surface=E-05/E-07 composition=DriveResult.await_human wiring=到达原合同人工门→await_human→run 停止→待办中心出现对应决定→不批准不越过 test=integration:tests/integration/test_auto_drive.py::test_human_gate_blocks_advance evidence=`.venv/bin/python -m pytest -q tests/integration/test_auto_drive.py`输出`passed`且 awaiting 状态持续、无越门事件 IF-DRIVE-001
- **FR-0298** owner=tracks/supervisor/waiting.py:AC-FR0298-01 surface=E-05 等待显示+重启 composition=enter_wait 持久化 wiring=注入可恢复外部等待→wait.entered(reason,retry_at)→UI 显示→服务重启后 waits 行保留→条件满足自动恢复 test=integration+e2e:tests/integration/test_wait_persistence.py::test_wait_survives_restart_and_auto_resumes+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_wait_persistence.py`输出`passed`且 retry_at 跨重启不变 IF-WAIT-001 IF-RECOVER-001
- **FR-0298** owner=tracks/supervisor/waiting.py:AC-FR0298-02 surface=探测频率观测 composition=WaitPolicy 退避 wiring=等待期间对外部条件的请求间隔≥退避间隔（初始 60s 至上限 900s）→无热循环 test=integration:tests/integration/test_wait_persistence.py::test_no_hot_loop_bounded_probing evidence=`.venv/bin/python -m pytest -q tests/integration/test_wait_persistence.py`输出`passed`且观测间隔不超口径 IF-WAIT-001
- **FR-0299** owner=tracks/supervisor/waiting.py:AC-FR0299-01 surface=E-05+额度信号注入 composition=classify_wait(known_reset) wiring=harness 返回带 reset 时间的配额信号→wait.entered(quota,known_reset=true,retry_at=reset)→到达后无人工干预自动续跑 test=integration:tests/integration/test_quota_wait.py::test_known_reset_auto_resume evidence=`.venv/bin/python -m pytest -q tests/integration/test_quota_wait.py`输出`passed`且 wait.resolved(resolved_by=retry_at_reached) 落盘 IF-WAIT-001
- **FR-0299** owner=tracks/supervisor/waiting.py:AC-FR0299-02 surface=E-05+待办中心 composition=classify_wait(unknown_reset) wiring=未知 reset→显示探测计划（backoff）而非倒计时→间隔合 NFR-0152→该等待不出现在待办中心 test=integration:tests/integration/test_quota_wait.py::test_unknown_reset_probe_plan_no_countdown evidence=`.venv/bin/python -m pytest -q tests/integration/test_quota_wait.py`输出`passed`且 UI 无倒计时字段、待办为空 IF-WAIT-001
- **FR-0300** owner=tracks/supervisor/recover.py:AC-FR0300-01 surface=worker 重启注入 composition=recover+lease 新代次 wiring=worker 执行中被杀→lease 过期→命令 command.requeued(lease_expired)→新 worker 重认领→未完成步骤继续→已完成外部副作用经既有幂等键跳过（不重复发布） test=integration+e2e:tests/integration/test_restart_recovery.py::test_worker_kill_reclaim_no_duplicate_effects+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_restart_recovery.py`输出`passed`且 publish.executed 无重复 done IF-RECOVER-001 IF-PUBLISH-002
- **FR-0300** owner=tracks/supervisor/recover.py:AC-FR0300-02 surface=server 重启注入 composition=recover_on_startup wiring=server 重启→waits/commands 找回→恢复推进全程无丢单无重复发布 test=integration:tests/integration/test_restart_recovery.py::test_server_restart_resumes_without_loss evidence=`.venv/bin/python -m pytest -q tests/integration/test_restart_recovery.py`输出`passed`且事件链连续 IF-RECOVER-001
- **FR-0301** owner=tracks/server/projections.py:AC-FR0301-01 surface=GET-/api/projects/{pid}/overview+E-02 composition=project_overview wiring=总览字段（当前/历史 run、版本、状态、待人工数量）与 tracks.db+service.db 实际一致 test=integration:tests/integration/test_overview_query.py::test_overview_consistent_with_state evidence=`.venv/bin/python -m pytest -q tests/integration/test_overview_query.py`输出`passed`且逐字段与事件投影一致 IF-QUERY-001
- **FR-0301** owner=tracks/server/api_query.py:AC-FR0301-02 surface=长任务期间查询 composition=只读独立事务 wiring=worker 长任务进行中发起查询→即时返回→事件序列不变→不阻塞推进 test=integration:tests/integration/test_overview_query.py::test_query_during_long_task_nonblocking evidence=`.venv/bin/python -m pytest -q tests/integration/test_overview_query.py`输出`passed`且查询耗时有界、事件计数不变 IF-QUERY-001
- **FR-0302** owner=tracks/server/projections.py:AC-FR0302-01 surface=E-05 详情 composition=project_run_detail wiring=当前阶段/任务完成数/质量状态返回；tests_passed 与 ac_closed/ac_total 分列 test=integration:tests/integration/test_progress_projection.py::test_dual_counts_presented evidence=`.venv/bin/python -m pytest -q tests/integration/test_progress_projection.py`输出`passed`且两计数来源字段并存 IF-QUERY-001
- **FR-0302** owner=tracks/server/projections.py:AC-FR0302-02 surface=E-05 详情 composition=同上 wiring=抽查断言显示数值=系统真实计数（测试结果事件/AC 登记处×证据链）；无端造百分比字段 test=integration:tests/integration/test_progress_projection.py::test_progress_matches_real_counts evidence=`.venv/bin/python -m pytest -q tests/integration/test_progress_projection.py`输出`passed`且响应无百分比合成字段 IF-QUERY-001
- **FR-0303** owner=tracks/server/projections.py:AC-FR0303-01 surface=E-05 执行状态区 composition=project_executions wiring=各角色/任务 executing|waiting|failed 状态与 log_ref 返回，可跳转日志 test=integration:tests/integration/test_execution_state.py::test_role_task_states_with_log_refs evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_state.py`输出`passed`且状态与派发/回收事件一致 IF-QUERY-001
- **FR-0303** owner=tracks/server/pages.py:AC-FR0303-02 surface=全部页面/API/导出 composition=响应 schema 封闭 wiring=schema 无 Agent 会话内容字段；全量响应与日志扫描无会话内容泄漏 test=integration:tests/integration/test_execution_state.py::test_no_agent_session_content_anywhere evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_state.py`输出`passed`且 schema 无会话内容键 IF-QUERY-001
- **FR-0304** owner=tracks/server/projections.py:AC-FR0304-01 surface=GET-/api/runs/{id}/ac-chain composition=project_ac_chain wiring=每 AC→测试 node→实际结果→候选→证据引用链路完整返回 test=integration:tests/integration/test_ac_evidence_view.py::test_ac_chain_complete evidence=`.venv/bin/python -m pytest -q tests/integration/test_ac_evidence_view.py`输出`passed`且链路字段齐全 IF-QUERY-001
- **FR-0304** owner=tracks/server/projections.py:AC-FR0304-02 surface=同上 composition=同上 wiring=构造缺失/过期/未审阅证据→对应 AC 标注 missing|stale|unreviewed test=integration:tests/integration/test_ac_evidence_view.py::test_missing_stale_unreviewed_marked evidence=`.venv/bin/python -m pytest -q tests/integration/test_ac_evidence_view.py`输出`passed`且三类标注各现 IF-QUERY-001
- **FR-0305** owner=tracks/server/projections.py:AC-FR0305-01 surface=E-05 时间线 composition=project_timeline wiring=事件/错误/重试次数/阻断原因/下一步返回且与实际发生一致 test=integration:tests/integration/test_timeline_diagnosis.py::test_timeline_shows_failures_retries_next evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_diagnosis.py`输出`passed`且注入故障后时间线含原因与下一步 IF-QUERY-001
- **FR-0305** owner=tracks/server/api_query.py:AC-FR0305-02 surface=timeline 过滤+结构化日志 composition=timeline 过滤参数+日志 JSON 行 wiring=按 run/command/task/AC 过滤命中对应记录；服务 stdout 日志行含同关联键 test=integration:tests/integration/test_timeline_diagnosis.py::test_logs_locatable_by_dimensions evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_diagnosis.py`输出`passed`且四维过滤均命中 IF-QUERY-001
- **FR-0306** owner=tracks/server/api_events.py:AC-FR0306-01 surface=SSE 订阅 composition=stream_events wiring=在线期间状态变化经 SSE 推送（无人工刷新） test=integration:tests/integration/test_event_stream.py::test_live_push_without_refresh evidence=`.venv/bin/python -m pytest -q tests/integration/test_event_stream.py`输出`passed`且新事件帧到达 IF-STREAM-001
- **FR-0306** owner=tracks/server/api_events.py:AC-FR0306-02 surface=断线重连 composition=游标补读 wiring=断线期间事件→after=cursor 重连补齐全部缺失；注入重复/乱序事件→投影不回退（按游标忽略） test=integration:tests/integration/test_event_stream.py::test_reconnect_backfill_no_regression evidence=`.venv/bin/python -m pytest -q tests/integration/test_event_stream.py`输出`passed`且补齐数与注入数一致、游标单调 IF-STREAM-001
- **FR-0307** owner=tracks/server/projections.py:AC-FR0307-01 surface=GET-/api/todos+E-07 composition=project_todos wiring=合法人工决定（批准/发布/澄清/triage）出现且含理由/上下文/跳转入口；candidate 可发布即出现发布待办 test=integration:tests/integration/test_todo_center.py::test_legit_decisions_listed_with_context evidence=`.venv/bin/python -m pytest -q tests/integration/test_todo_center.py`输出`passed`且各项含 action_ref IF-QUERY-001
- **FR-0307** owner=tracks/server/projections.py:AC-FR0307-02 surface=同上 composition=同上 wiring=构造 quota/CI 等待→待办中心不含该项（仅在 run detail 等待区呈现） test=integration:tests/integration/test_todo_center.py::test_waits_never_in_todos evidence=`.venv/bin/python -m pytest -q tests/integration/test_todo_center.py`输出`passed`且等待期间 todos 为空 IF-QUERY-001
- **FR-0308** owner=tracks/server/api_command.py:AC-FR0308-01 surface=POST-/api/runs/{id}/approvals composition=accept(record_stage_approval)复用 cmd_approve 校验 wiring=批准绑定 expected_revision→human.approval（操作者/revision/决定落 tracks.db，可审计）→自动推进下一阶段 test=integration+e2e:tests/integration/test_web_approval.py::test_approval_bound_and_advances+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_approval.py`输出`passed`且批准事件含 actor 与 revision IF-WEBGATE-001
- **FR-0308** owner=tracks/server/api_command.py:AC-FR0308-02 surface=同上 composition=同上 wiring=revision 过期后提交批准→command.rejected(stale_revision)+提示当前 revision→状态不推进 test=integration:tests/integration/test_web_approval.py::test_stale_revision_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_approval.py`输出`passed`且无 human.approval 落盘 IF-WEBGATE-001
- **FR-0309** owner=tracks/server/api_command.py:AC-FR0309-01 surface=GET-release-preview+POST-release-decision+E-08 composition=accept(record_release_decision)复用 release_gate 校验 wiring=全门通过→发布待办出现→preview 显示 digest 与过期状态→release 执行→UI 显示真实 remote 结果并可查 AC 证据链 test=integration+e2e:tests/integration/test_web_release.py::test_release_via_web_happy+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_release.py`输出`passed`且 release.decided 绑定 preview_digest、stand-in 远端 tag 存在 IF-WEBGATE-001 IF-RELEASE-003
- **FR-0309** owner=tracks/server/api_command.py:AC-FR0309-02 surface=同上 composition=同上 wiring=preview 过期后提交 release→command.rejected(stale_preview)→run 停留可发布/等待状态→无发布副作用 test=integration:tests/integration/test_web_release.py::test_stale_preview_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_release.py`输出`passed`且远端无新 tag IF-WEBGATE-001 IF-RELEASE-002
- **FR-0309** owner=tracks/server/api_command.py:AC-FR0309-03 surface=同上 composition=同上 wiring=delay/return 各产生明确终态并绑定决定时 digest；决定可审计 test=integration:tests/integration/test_web_release.py::test_delay_and_return_bound_to_digest evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_release.py`输出`passed`且 release.decided(action=delay|return) 绑定 digest IF-WEBGATE-001 IF-RELEASE-003
- **FR-0310** owner=tracks/supervisor/service.py:AC-FR0310-01 surface=POST-/pause+E-05 状态 composition=pause 两阶段 wiring=pause→run.pause_requested（已接收）→worker 边界→run.paused（已暂停）→注入 retry_at 到期/额度恢复→run 保持暂停 test=integration+e2e:tests/integration/test_pause_resume.py::test_pause_two_phase_and_priority+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_pause_resume.py`输出`passed`且两阶段事件有序、无自动唤醒 IF-PAUSE-001 IF-CMDSVC-001
- **FR-0310** owner=tracks/supervisor/worker.py:AC-FR0310-02 surface=POST-/resume composition=resume_run wiring=resume→run.resumed(from_stage)→从暂停前合法位置继续→已完成工作与证据保留 test=integration:tests/integration/test_pause_resume.py::test_resume_from_legal_position evidence=`.venv/bin/python -m pytest -q tests/integration/test_pause_resume.py`输出`passed`且证据链完整 IF-PAUSE-001 IF-CMDSVC-001
- **FR-0311** owner=tracks/supervisor/service.py:AC-FR0311-01 surface=POST-/api/runs/{id}/retry composition=accept(retry_run)复用 repair/escape wiring=可恢复失败修复条件后重试→从原合法位置继续→不重复已发生外部副作用 test=integration:tests/integration/test_controlled_retry.py::test_retry_from_legal_position_no_duplicates evidence=`.venv/bin/python -m pytest -q tests/integration/test_controlled_retry.py`输出`passed`且无重复副作用事件 IF-WEBGATE-001 IF-ESCAPE-001
- **FR-0311** owner=tracks/supervisor/service.py:AC-FR0311-02 surface=同上 composition=同上 wiring=证据过期的失败重试→command.rejected+原因（不复活过期证据）；不可恢复错误无重试入口（控制区不呈现） test=integration:tests/integration/test_controlled_retry.py::test_stale_evidence_retry_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_controlled_retry.py`输出`passed`且拒绝原因可读 IF-WEBGATE-001
- **FR-0312** owner=tracks/server/api_command.py:AC-FR0312-01 surface=POST-/api/runs/{id}/return composition=accept(return_stage)复用 escape wiring=仅允许 Runtime 允许的上游阶段→确认前显示将失效证据与影响（already_executed 清单）→确认后回目标阶段重新推进 test=integration+e2e:tests/integration/test_web_rollback.py::test_return_with_impact_preview+tests/e2e/test_web_journey.py::test_feature_web_vertical_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_rollback.py`输出`passed`且 escape.barrier_established 先于指针移动 IF-WEBGATE-001 IF-ESCAPE-001
- **FR-0312** owner=tracks/supervisor/lease.py+tracks/executor/escape.py:AC-FR0312-02 surface=同上 composition=屏障+迟到隔离 wiring=回拨后注入旧 worker/旧 Agent 迟到结果→escape.late_outcome(quarantined)/worker.late_result→被回拨流程不推进→下游证据 evidence.staled 并在重验后恢复 test=integration:tests/integration/test_web_rollback.py::test_late_results_isolated_after_rollback evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_rollback.py`输出`passed`且迟到结果零推进 IF-ESCAPE-001 IF-LEASE-001
- **FR-0312** owner=tracks/server/api_command.py:AC-FR0312-03 surface=同上 composition=同上 wiring=已推送 tag/release 的 run 回拨→远端 tag/release 仍在→影响清单如实列出已发生副作用 test=integration:tests/integration/test_web_rollback.py::test_pushed_release_survives_rollback evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_rollback.py`输出`passed`且远端对象不变 IF-ESCAPE-001
- **FR-0313** owner=tracks/supervisor/service.py:AC-FR0313-01 surface=POST-/api/runs/{id}/abandon composition=accept(abandon_run)复用轻量终止 wiring=abandon→terminal=cancelled→审计与已完成证据保留可查 test=integration:tests/integration/test_abandon_web.py::test_abandon_terminal_auditable evidence=`.venv/bin/python -m pytest -q tests/integration/test_abandon_web.py`输出`passed`且终态为 cancelled IF-WEBGATE-001 IF-ESCAPE-002
- **FR-0313** owner=tracks/supervisor/service.py:AC-FR0313-02 surface=同上 composition=同上 wiring=abandon 无新增远端副作用、不撤销已发生远端操作、不显示已成功完成类误导终态 test=integration:tests/integration/test_abandon_web.py::test_abandon_no_fake_success evidence=`.venv/bin/python -m pytest -q tests/integration/test_abandon_web.py`输出`passed`且远端无变化 IF-ESCAPE-002
- **FR-0313** owner=tracks/supervisor/worker.py:AC-FR0313-03 surface=E-05 终态显示 composition=DriveResult.failed(unrecoverable) wiring=注入不可恢复失败→command.failed(failure_class=unrecoverable)→run 停明确失败终态（区别于等待外部）→不进重试循环→时间线可解释原因与下一步→不冒充成功 test=integration:tests/integration/test_abandon_web.py::test_unrecoverable_failure_terminal evidence=`.venv/bin/python -m pytest -q tests/integration/test_abandon_web.py`输出`passed`且无 wait.entered、无重试命令 IF-DRIVE-001
- **FR-0314** owner=tracks/server/auth.py:AC-FR0314-01 surface=全部页面/API composition=auth 中间件 wiring=未认证访问→API 401/页面 302 登录页；认证后可访问 test=integration:tests/integration/test_web_auth.py::test_unauthenticated_rejected_then_ok evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_auth.py`输出`passed`且两类拒绝形态各现 IF-WEBAUTH-001
- **FR-0314** owner=tracks/server/guard.py:AC-FR0314-02 surface=批准/发布决定端点 composition=check_actor_class wiring=非人类 actor_class 提交批准/发布→403+command.rejected(forbidden_actor)+access.denied→无状态推进 test=integration:tests/integration/test_web_auth.py::test_non_human_decision_rejected_audited evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_auth.py`输出`passed`且审计事件含尝试记录 IF-CMDGUARD-001
- **FR-0314** owner=tracks/server/guard.py:AC-FR0314-03 surface=全部变更端点 composition=validate_command_payload wiring=任意 shell 类载荷（未知 kind/自由文本执行字段）→400+command.rejected(guard_blocked)→无执行效果 test=integration:tests/integration/test_web_auth.py::test_shell_payload_blocked evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_auth.py`输出`passed`且无新进程、无事件副作用 IF-CMDGUARD-001
- **FR-0314** owner=tracks/supervisor/service.py:AC-FR0314-04 surface=讨论 resolved 入口（Web+CLI） composition=resolve_discussion_thread wiring=请求 Human/指定评审者裁决的线程：非被请求方 resolved→无效（状态不变）+access.denied；被请求方经认证入口响应→resolved 生效 test=integration:tests/integration/test_web_auth.py::test_adjudication_ownership_enforced evidence=`.venv/bin/python -m pytest -q tests/integration/test_web_auth.py`输出`passed`且越权 resolved 后线程状态不变 IF-WEBAUTH-001
- **FR-0315** owner=tracks/server/redaction.py:AC-FR0315-01 surface=全部 UI/API/日志 composition=SecretRedactor 全出口装配 wiring=植入 canary 密钥（env+run 数据）→扫描全部页面/API 响应/SSE 帧/服务日志→无明文（仅 ${NAME} 引用） test=integration:tests/integration/test_secrecy.py::test_no_plaintext_secrets_any_surface evidence=`.venv/bin/python -m pytest -q tests/integration/test_secrecy.py`输出`passed`且 canary 零命中 IF-SECRECY-001
- **FR-0315** owner=tracks/server/guard.py:AC-FR0315-02 surface=文件/产物读取端点 composition=check_path_scope wiring=授权项目及证据范围外读取→403+access.denied(scope_violation)并记录 test=integration:tests/integration/test_secrecy.py::test_out_of_scope_read_denied evidence=`.venv/bin/python -m pytest -q tests/integration/test_secrecy.py`输出`passed`且审计事件落盘 IF-SECRECY-001
- **NFR-0150** owner=tracks/supervisor/service.py:AC-NFR0150-01 surface=POST 受理响应 composition=accept 先持久化即返回 wiring=提交命令→响应在持久化完成即返回（不含执行时长）；长任务运行期间提交新命令不被阻塞 test=integration:tests/integration/test_perf_budget.py::test_accept_returns_after_persist evidence=`.venv/bin/python -m pytest -q tests/integration/test_perf_budget.py`输出`passed`且受理延迟与执行时长解耦 IF-CMDSVC-001
- **NFR-0150** owner=tracks/server/projections.py:AC-NFR0150-02 surface=总览/详情/时间线查询 composition=只读投影 wiring=单用户单 run 负载下采样查询延迟→P95<1s test=integration:tests/integration/test_perf_budget.py::test_query_p95_under_1s evidence=`.venv/bin/python -m pytest -q tests/integration/test_perf_budget.py`输出`passed`且 P95 采样值低于阈值 IF-QUERY-001
- **NFR-0150** owner=tracks/server/app.py:AC-NFR0150-03 surface=soak 持续负载 composition=投影无界增长自由 wiring=大量事件+多次重试持续负载→经 ps 采样服务进程 RSS→预热后无单调增长且保持有界 test=integration:tests/integration/test_perf_budget.py::test_soak_memory_bounded evidence=`.venv/bin/python -m pytest -q tests/integration/test_perf_budget.py`输出`passed`且 RSS 序列无单调增长趋势 IF-SERVE-001
- **NFR-0151** owner=tests(Shield 资产):AC-NFR0151-01 surface=测试套件自身 composition=八场景标记扫描 wiring=服务重启/断网/模型额度/重复并发请求/并发 worker/UI 断线重连/旧 worker 迟到结果/人工 pause 竞态八类各存在至少一条自动化测试并被收集 test=integration:tests/integration/test_reliability_matrix.py::test_eight_reliability_scenarios_covered evidence=`.venv/bin/python -m pytest -q tests/integration/test_reliability_matrix.py`输出`passed`且八场景映射的收集节点齐备 IF-MTEST-001 IF-MTEST-002
- **NFR-0151** owner=pyproject.toml+§4.2-registry:AC-NFR0151-02 surface=CI coverage 必检 composition=守卫 registry 继承 wiring=本版 architecture §4.2 registry 含 coverage_threshold 且 fail-under>=95→CI coverage 必检通过 test=integration:tests/integration/test_reliability_matrix.py::test_coverage_threshold_inherited evidence=`.venv/bin/python -m pytest -q tests/integration/test_reliability_matrix.py`输出`passed`且 registry 加载校验零错误、阈值>=95 IF-GUARD-001 IF-GUARD-002
- **NFR-0152** owner=tracks/supervisor/waiting.py:AC-NFR0152-01 surface=等待探测观测+GET-/api/service/config composition=WaitPolicy wiring=注入未知 reset 额度等待→探测间隔自 60s 退避增长且≤900s；config 端点可读出默认值 test=integration:tests/integration/test_backoff_policy.py::test_backoff_growth_bounded_and_config_readable evidence=`.venv/bin/python -m pytest -q tests/integration/test_backoff_policy.py`输出`passed`且间隔序列合口径、config 含默认值 IF-WAIT-001
- **NFR-0152** owner=tracks/server/api_events.py:AC-NFR0152-02 surface=轮询回退观测 composition=SSE/轮询同源 wiring=SSE 断开→轮询间隔≤空闲上限 60s；服务空闲（无活动 run）→无持续轮询负载（请求频率为零或低于空闲口径） test=integration:tests/integration/test_backoff_policy.py::test_poll_fallback_caps_and_idle_silence evidence=`.venv/bin/python -m pytest -q tests/integration/test_backoff_policy.py`输出`passed`且空闲期观测零轮询 IF-STREAM-001

## 2. Scaffold 宣言

本节是 M-DESIGN 阶段在宿主项目创建文件的唯一合同。以下文件已随本设计一并物理交付；此外不创建任何文件（`tests/ground_truth/` 不适用——test-plan §3 判定）。

- `tracks/server/__init__.py` — Web 交付面包声明（kind: stub）
- `tracks/server/app.py` — create_app/health_response 签名，行为体仅 raise IF-SERVE-001（kind: stub）
- `tracks/server/auth.py` — 口令/会话/CSRF 签名，行为体仅 raise IF-WEBAUTH-001（kind: stub）
- `tracks/server/guard.py` — 命令防护/actor_class/路径范围签名，行为体仅 raise IF-CMDGUARD-001/IF-SECRECY-001（kind: stub）
- `tracks/server/redaction.py` — SecretRedactor 签名，行为体仅 raise IF-SECRECY-001（kind: stub）
- `tracks/server/api_command.py` — 变更类路由签名，行为体仅 raise 对应 IF token（kind: stub）
- `tracks/server/api_query.py` — 只读路由签名，行为体仅 raise 对应 IF token（kind: stub）
- `tracks/server/api_events.py` — SSE/游标签名，行为体仅 raise IF-STREAM-001（kind: stub）
- `tracks/server/pages.py` — 页面壳/Vditor 宿主签名，行为体仅 raise IF-DOCREV-001（kind: stub）
- `tracks/server/projections.py` — 读模型签名，行为体仅 raise IF-QUERY-001（kind: stub）
- `tracks/supervisor/__init__.py` — 后台驱动控制层包声明（kind: stub）
- `tracks/supervisor/db.py` — service.db schema/SERVICE_EVENT_TYPES 封闭集/访问层签名，行为体仅 raise 对应 IF token（kind: stub）
- `tracks/supervisor/service.py` — CommandService 签名，行为体仅 raise IF-CMDSVC-001 等（kind: stub）
- `tracks/supervisor/lease.py` — 租约/代次签名，行为体仅 raise IF-LEASE-001（kind: stub）
- `tracks/supervisor/scheduler.py` — 调度/换队签名，行为体仅 raise IF-SCHED-001（kind: stub）
- `tracks/supervisor/waiting.py` — 等待/退避签名，行为体仅 raise IF-WAIT-001（kind: stub）
- `tracks/supervisor/worker.py` — worker 管理签名，行为体仅 raise IF-DRIVE-001 等（kind: stub）
- `tracks/supervisor/worker_main.py` — 子进程入口签名，行为体仅 raise IF-DRIVE-001（kind: stub）
- `tracks/supervisor/recover.py` — 启动恢复签名，行为体仅 raise IF-RECOVER-001（kind: stub）
- `tracks/supervisor/readiness.py` — 就绪探测签名，行为体仅 raise IF-PROJ-001（kind: stub）
- `tracks/cli/serve_cmd.py` — cmd_serve 签名，行为体仅 raise IF-SERVE-001（kind: stub）
- `tracks/executor/drive.py` — DriveResult/WaitSpec/drive_once 签名，行为体仅 raise IF-DRIVE-001（kind: stub）
- `pyproject.toml` — 新增运行时依赖 starlette==0.49.3/uvicorn==0.49.0；package-data 追加 server/static/**（本文件 bytes 变化已同步 §4.2 六条 pyproject-backed config_digest）（kind: config）
- `tracks/server/static/vendor/vditor/index.min.js` — Vditor 3.11.3 编辑器本体（MIT）（kind: data）
- `tracks/server/static/vendor/vditor/index.css` — Vditor 样式（kind: data）
- `tracks/server/static/vendor/vditor/js/lute/lute.min.js` — markdown 引擎（按需加载）（kind: data）
- `tracks/server/static/vendor/vditor/js/highlight.js/highlight.min.js` — 代码高亮（按需加载）（kind: data）
- `tracks/server/static/vendor/vditor/js/highlight.js/styles/github.min.css` — 固定高亮主题（kind: data）
- `tracks/server/static/vendor/vditor/js/i18n/en_US.js` — 界面语言（kind: data）
- `tracks/server/static/vendor/vditor/js/i18n/zh_CN.js` — 界面语言（kind: data）
- `tracks/server/static/vendor/vditor/js/icons/ant.js` — 图标集（kind: data）
- `tracks/server/static/vendor/vditor/js/icons/material.js` — 图标集（kind: data）
- `tracks/server/static/vendor/vditor/images/logo.png` — 编辑器引用图片（kind: data）
- `tracks/server/static/vendor/vditor/images/img-loading.svg` — 编辑器引用图片（kind: data）
- `tracks/server/static/vendor/vditor/LICENSE` — MIT 许可文本（kind: data）
- `tracks/server/static/vendor/vditor/manifest.json` — 版本/来源/逐文件 sha256/排除项清单（CI 校验一致性）（kind: data）

本节以外不创建 scaffold。上述 stub 的行为体/接线（cmd_serve 行为体与 USAGE/_COMMANDS 注册、create_app 路由装配、CommandService 受理/去重/执行、lease CAS、调度与换队、waiting 退避、worker 子进程管理、recover、readiness、drive_once、投影、SSE、页面与 app.js/styles.css、redactor/guard/auth 行为体、裁决权属校验接入 discuss、GuardRejection 到 HTTP 状态码的映射）均为**待实现 Devon foundation/实现 tasks**；本文不得把它们当作既有可执行能力。

## 3. 技术选型

### 3.1 starlette==0.49.3 + uvicorn==0.49.0（louke 验证线内）

SPEC-009 锁定 starlette+uvicorn+SQLite 选型。louke 的验证约束是 `starlette>=0.38,<1.0`、`uvicorn>=0.30,<1.0`；starlette 1.x（2026-03 起）不在 louke 验证线内，故取 0.x 线最新补丁：starlette 0.49.3（2025-11-01）、uvicorn 0.49.0。代价：不是绝对最新线（1.6.0），换来与已验证选型同线的行为面（中间件/响应/静态文件形态）零意外；升级 1.x 留给后续版本单独评估。核心传递依赖仅 anyio，不取任何 extras（不引入 jinja2/python-multipart/itsdangerous——页面壳用字符串模板、请求体一律 JSON、会话用不透明随机 token）。这是本项目首个运行时依赖：此前 `dependencies = []`；取舍——Web 服务是 v0.9 核心交付面，依赖进 `[project.dependencies]` 而非 optional extras，安装即得；CLI 其余路径不 import server 包（serve 子命令内 lazy import，缺依赖时其余子命令不受影响）。

### 3.2 服务面独立 SQLite（service.db），不动 tracks.db

服务面状态（projects/commands/waits/leases/auth/sessions/schedule/service_events）落在 `<service_home>/service.db`（WAL；server 进程唯一写者）。不扩展 tracks.db schema、不追加 EVENT_TYPES 成员——kernel reducer 的 pending 语义与 v0.8 状态机输入集零风险（SM-02.8）。代价：时间线需合并两个事件源（以 (ts, source_rank, seq) 确定性排序，interfaces §1e）；换来 run 面零迁移、零回归面。不引入数据库迁移框架（CREATE TABLE IF NOT EXISTS 即最小 schema 扩展，规划明示）。SQLite 单写者约束与 server 唯一写者一致；读路径独立只读连接（NFR-0150）。

### 3.3 SSE 手写（StreamingResponse），不引入 sse-starlette

text/event-stream 帧格式极简（id/event/data 三段），Starlette StreamingResponse 足够；断开回退 JSON 轮询共用同一数据路径（interfaces §1e/§2b #28）。少一个依赖 = 少一个 pinned 攻击面。代价：心跳/重连管理自行实现（契约已固定）。

### 3.4 Vditor 3.11.3 vendored 最小子集（自源站，禁 CDN）

Human 裁定文档前端采用 Vditor 基础集成。npm 最新 4.0.0（2026-08-30）发布不足一个月，取稳定 3.x 线最新 3.11.3（2026-08-11）。louke 以 CDN 方式不 pin 加载——与 tracks 离线确定性立场冲突，故 vendor 进 wheel：index.min.js/index.css + lute + highlight（固定 github 主题）+ i18n(en/zh) + icons + 两张图片 + LICENSE + manifest.json（逐文件 sha256，CI 校验一致性），共 12 个资产文件约 6MB。可选渲染引擎（math/mermaid/echarts 等）与 emoji 不 vendor、集成配置禁用——材料是 tracks 文档，无此内容；代价：未来文档若含此类内容需补充 vendoring 并修订 manifest（显式设计决定）。

### 3.5 scrypt 口令 + 不透明会话 token（stdlib only）

认证不引入 bcrypt/itsdangerous：`hashlib.scrypt`（n=2^15, r=8, p=1）存口令散列；会话 token `secrets.token_urlsafe(32)`，服务端只存 sha256；CSRF 随会话随机、恒时比较。SameSite=Strict Cookie + 自定义头双重防护。单用户本机实例下强度足够且零新依赖。代价：口令轮换需重启供给（本版不提供密码管理 UI，§5.1）。

### 3.6 worker 子进程隔离（不用线程）

执行上下文含进程内状态（run_loop 恢复逻辑），多 HTTP 线程共享全局是规划明示的禁项；worker 为 supervisor 的子进程（`python -m tracks.supervisor.worker_main`），崩溃边界=进程边界，租约过期可 SIGTERM/SIGKILL 物理终止。代价：每命令一次进程启动开销（单活动 run 串行下可忽略）；换来恢复/防旧的物理隔离与既有 writer_lock 纪律的原样复用。

### 3.7 静态面零构建链

UI 形态不作需求（spec 明示）：页面为服务端渲染壳 + 原生 ES modules（app.js/styles.css，Devon 实现），无 Node/打包器进入构建链。SPA 与否由实现自由度决定，交互结果与信息内容受接口合同约束。代价：交互细腻度上限低于框架 SPA；换来构建链与 reproducibility 零新增。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

既有三层合同逐字不变：framework=`pytest`；paths=`tests/unit/`、`tests/integration/`、`tests/e2e/`；collect/run/run_selected 使用宿主 `.venv/bin/python -m pytest`；cwd=`.`；`{result}`/`{nodes}` 展开所有权、xdist/JUnit flags、`[nightly]`、`[adapter]`、`[layout]`、`[lint]`、`[host-contract.*]` 段全部不变。v0.9 新增测试资产落在既有 Shield 域（tests/integration、tests/e2e、tests/_support、tests/assets），无需修改 layout。本文件本版 bytes 不变（§4.2 第 8 项 config_digest 不变）。

### 4.2 Canonical quality guard registry（v0.9）

以下 TOML block 是 tracks 宿主 guard registry 的 v0.9 canonical 真相（承接 ARCH-008 §4.2，唯一输入变更：`pyproject.toml` 因首个运行时依赖与 server 资产 package-data 而 bytes 变化，六条 pyproject-backed config_digest 同步为新值 `1e6590bc…`；`.flake8` 与 `.tracks/projects/project.toml` bytes 未变，对应 digest 不变）。字段语义、digest 公式（单文件 sha256(raw bytes)）、八类强制、fail_closed、禁 `--exit-zero` 全部继承 ARCH-008 §4.2 / IF-008 §1k，不再重复。

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
config_digest = "sha256:1e6590bcf94b84546a5d7ab02df0c936faf41faef3a9962574ac8a781116fab1"
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
config_digest = "sha256:1e6590bcf94b84546a5d7ab02df0c936faf41faef3a9962574ac8a781116fab1"
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
config_digest = "sha256:1e6590bcf94b84546a5d7ab02df0c936faf41faef3a9962574ac8a781116fab1"
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
config_digest = "sha256:1e6590bcf94b84546a5d7ab02df0c936faf41faef3a9962574ac8a781116fab1"
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
command = ".venv/bin/pylint --disable=all --enable=R0801 tracks"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.similarities"]
config_digest = "sha256:1e6590bcf94b84546a5d7ab02df0c936faf41faef3a9962574ac8a781116fab1"
scope = ["tracks", "tests"]
threshold = "R0801 min-similarity-lines=4 (product scope; test-file similarity is covered by review, not this release gate)"
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
config_digest = "sha256:1e6590bcf94b84546a5d7ab02df0c936faf41faef3a9962574ac8a781116fab1"
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
config_digest = "sha256:a794021f35c6004ea296fb98dcd6cf6babd20d5ce4cedd5a97806f1ecba05df1"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test,deliverables,trace,reach; milestone=release-evidence"
timeout_seconds = 3600
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test,deliverables,trace,reach"
```

### 4.3 CI / pre-commit / release

- Stable required checks 继承不变：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`；job 名不改。v0.9 不新增 required check：Web 测试全部进入既有 `test` job 的 deterministic 套件（§4.4）。
- pre-commit hook 不变；新增源码目录（tracks/server、tracks/supervisor）自动纳入既有守卫 scope（registry scope 为 `tracks`）。
- 安装命令（副作用归 Runtime）不变：`python -m venv .venv`、`.venv/bin/pip install -e '.[dev]'`、`git config core.hooksPath .githooks`；starlette/uvicorn 经 `[project.dependencies]` 随安装进入 venv，无额外安装步。
- `release-evidence` milestone 硬门禁继承不变（v0.8 扩展不动）；v0.9 不新增 live 通道（test-plan §6）。
- Vditor 资产完整性：CI 静态校验（`deliverables` 既有机制面，**待实现 Devon foundation task** 的 validate 扩展可后续纳入）以 `tracks/server/static/vendor/vditor/manifest.json` 的逐文件 sha256 为准对账；本版由 integration 测试（test-plan §8 AC-FR0294-02 的同源断言）承载一致性验证。

### 4.4 Integration/e2e 基础设施

- Shield integration 资产落 `tests/integration/test_{serve_lifecycle,project_registry,readiness,run_create_web,clarify_web,doc_review_web,command_service,supervisor_lease,hotfix_swap,auto_drive,wait_persistence,quota_wait,restart_recovery,overview_query,progress_projection,execution_state,ac_evidence_view,timeline_diagnosis,event_stream,todo_center,web_approval,web_release,pause_resume,controlled_retry,web_rollback,abandon_web,web_auth,secrecy,perf_budget,reliability_matrix,backoff_policy}.py`；e2e 新增 `test_web_journey.py`（纵向闭环 happy path）与 `test_web_hotfix_journeys.py`（两旅程 Web 入口）。
- **serve fixture（Shield 建于 tests/_support/）**：每测试会话以子进程启动真实服务（serve 命令、`--port 0` 随机端口、临时 `--home`、临时项目仓库、`--password-stdin` 供给测试口令），经 stdlib urllib/http.client 驱动；SSE 经 http.client 流式读取；不引入 httpx/TestClient 依赖（§5.1）。
- fault 注入经公开合同面：quota/网络等待经 harness 信号注入（fake backend / stand-in 响应）、租约过期经 lease 表与信号、断线重连经 SSE 断连、旧 worker 迟到结果经代次构造；不 mock server/supervisor 内部。
- 发布链断言沿用 v0.8 stand-in GitHub（TRAC_GITHUB_API_BASE）+ 本地 bare 远程；Web 入口与其组合（E2E 旅程的发布腿不变）。
- deterministic suite 默认离线：serve/worker/SSE 全在 loopback；stand-in 服务监听 loopback；Vditor 资产自源站提供（禁 CDN，测试断言）。

### 4.5 Build / artifact

build backend 与 wheel 名不变；package-data 追加 `server/static/**`（vendored Vditor 与页面静态面随 wheel 分发）；M-VERIFY build gate（`pip wheel --no-deps`）与 smoke（隔离 prefix 安装 + `--help`）不变。版本字段本版不动（0.8.0），发布期统一升版（既有 release-commit 惯例；version gate 为 tag 推导式，不绑定 pyproject 版本字段，继承 ARCH-008 §3.7）。

### 4.6 发布恢复

服务面恢复合同：serve 重启 → recover_on_startup（claimed 命令 requeue、waits 保留 retry_at、租约过期授新代次）；worker 崩溃 → lease 过期 → 命令重认领；未完成外部 effects 沿用 v0.8 WAL/幂等键/reconcile（publish.planned/publish.executed/reconciled_skip），v0.9 不新增第二套恢复机制。命令与等待的全部状态可经 `GET /api/commands/{id}`、run detail 与 timeline 重建解释。

## 5. 有意识简化与风险

### 5.1 有意识简化

- 服务安装/自启不承诺（规格期锁定）：一条命令手动启动 + 健康检查；无守护/自启/日志轮转。
- 口令轮换/多用户/RBAC 不做（单用户本机实例）：口令首次启动供给，轮换需重启供给；会话固定 actor=local-user。
- 测试驱动真实服务子进程 + stdlib HTTP 客户端，不引入 httpx/starlette TestClient：多付一点 fixture 代码，换零新依赖与真实 socket 覆盖（连 uvicorn 服务形态一并被测）。
- `/healthz` 为唯一未认证端点（探活面，返回 status/projects 计数/version/uptime）；本机单用户实例下可接受，公网部署不在范围（范围排除）。
- Vditor 仅基础集成（查看/编辑/版本对比），可选渲染引擎与 emoji 不 vendor 且禁用（§3.4）。
- 真并发多 run（worktree 隔离/多 worker 并行）不实现：单活动 run 串行 + hotfix 显式换队（Human 裁定）；supervisor 预留 run 级隔离边界。
- Ground Truth 不适用（test-plan §3）：预期来自事件 schema、HTTP 响应合同、digest 局部重算与 fixture 本身。
- UI 形态不锁定（SPA/多页由实现自由度决定）；完成标准是单条真实纵向闭环，不是页面齐备。

### 5.2 风险

- **drive_once 抽取触及 run_loop 既有路径**：CLI 行为不变是硬约束（既有 e2e 回归守护）；任何行为差异即缺陷而非风格。缓解：drive.py 只做结构化封装，decide/issue/execute 语义零改动；iface 合同（§1d 五态）先行。
- **worker 子进程与 CLI 并发**：同一项目被 `trac run`（CLI 长循环）与 supervisor worker 同时驱动会破坏单执行者假设。合同：同一项目同一时间只允许一种驱动面（serve 运行时 CLI 的 run 对该 run 拒绝/排队由 lease 裁决）；缓解：writer_lock + lease 代次；残余窗口（zombie worker 在失去租约后、被杀前已写入的事件）由完成 CAS 拒绝其 outcome 并落 worker.late_result 审计，run 状态不受其影响。
- **quota 信号解析的脆弱性**：known reset 时间依赖 harness/provider 响应格式（如 "earliest recover at <ts>"）；解析失败必须落 unknown-reset 有界退避而非误报精确时刻。缓解：classify_wait 保守解析（仅明确模式命中才 known_reset），测试覆盖两类注入。
- **SSE 长连接与事件量**：大量事件下逐帧推送的成本；缓解：帧即事件（无聚合）、轮询回退存在；soak 测试（AC-NFR0150-03）覆盖。
- **vendored 资产的供应链**：Vditor 资产 pin 版本 + manifest sha256；升级需显式修订 manifest（显式设计决定，禁止静默换版）。
- **首版单进程 supervisor**：supervisor 崩溃即服务停止（服务生命周期合同要求重启恢复，AC-FR0288-02/FR-0300-02 覆盖）；多副本 supervisor 不在范围。
