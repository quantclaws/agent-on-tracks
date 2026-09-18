---
envelope: tracks-envelope:v2
interfaces_id: IF-009
spec_ref: SPEC-009
arch_ref: ARCH-009
created: 2026-09-18
status: draft
sha:
---

# v0.9 — 接口与类型化 Schema：Web 后台持续驱动与最小工作台

本文是 IF-008 的增量延伸：v0.1～v0.8 的事件溯源、封闭事件集、命令 WAL、审批 revision 绑定、release preview/三择一、escape barrier、发布幂等/reconcile、guard registry 与 CLI 合同全部继承；v0.9 新增的是**服务面合同**——HTTP API、持久命令服务、supervisor 驱动/租约/等待、只读投影与事件订阅，以及文档材料的 Web 审阅/编辑。本文只写外部可观察契约；模块内部组织见 architecture.md。

## 0. 延续性（什么不变）

- IF-001/003/004/005/006/007/008 的全部合同（EventEnvelope/Command、Assignment/Outcome、State 投影、D-41 selection/evidence/ledger/FULL、hotfix、Phase 0、guard registry、authenticity/mutation、adapter seam、发布闭环五阶段、失败证据链、逃生门）逐字继承；既有 IF 标识不可重定义或复用，IF-009 只追加新标识（§5）。
- **tracks.db 的 EVENT_TYPES 封闭集本版不追加任何成员**；v0.9 全部新事件类型落在服务面 `service_events` 追加只读日志（§1a），run 面状态机（v0.8 SM-01 含 22 条转移）与 kernel reducer 输入集零改动（SM-02.8）。
- kernel 的 `COMMAND_KINDS` 封闭集不变；v0.9 服务命令集（§1b）是应用层封闭集，经命令服务映射到既有 kernel 命令或既有 gate 语义。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]/[nightly]/[adapter]/[layout]/[lint]/[host-contract.*]` 各段逐字不变。
- 既有 CLI 子命令集合与语义不变；唯一新增顶层子命令是 serve（**待实现 Devon foundation task**：交付 cmd_serve 并同步 USAGE 与 `_COMMANDS`；本文以散文形式引用该待实现命令，同 v0.8 release 先例）。
- `prism.verdict`、`human.approval`、`human.return`、`release.decided/rejected`、`publish.*`、`escape.*` 等既有事件的类型与 payload 语义不因 Web 入口改变；Web 只是这些既有合同的新交付面。
- `trac discuss` 的线程格式/状态机/命令不变；v0.9 仅在其上追加裁决权属校验（§1f.4）。

## 1. 跨模块合同

### 1a. 服务面事件封闭集（service_events）

服务面事件写入 `<service_home>/service.db` 的 `service_events` 表（§3 #1），append-only、seq 单调（表内自增主键承载），payload 为 JSON；未列字段不得作为通过证据。`modules` 含 2+ 模块的行必须有 integration 覆盖。actor_class 封闭集：`human`（认证会话/CLI 操作者）、`system`（supervisor/worker 内部）、`agent`（Agent 后端，仅出现于被拒绝的尝试记录）。

| # | event | payload（关键字段） | producer | modules |
|:--|:--|:--|:--|:--|
| 1 | `service.started` | `pid: int`, `host: str`, `port: int`, `home: str`, `permitted_repos: list[str]`, `version: str` | serve 入口 | tracks/cli/serve_cmd, tracks/server/app, tracks/supervisor/db |
| 2 | `service.stopped` | `reason: "explicit"\|"signal"`, `uptime_s: float` | serve 入口 | tracks/server/app, tracks/supervisor/db |
| 3 | `project.registered` | `project_id: str`, `repo_path: str`, `version: str`, `actor: str` | 命令服务 | tracks/supervisor/service, tracks/supervisor/db, tracks/server/api_command |
| 4 | `project.registration_rejected` | `repo_path: str`, `reason: "outside_permitted_scope"\|"not_a_tracks_repo"`, `actor: str` | 命令服务 | tracks/supervisor/service, tracks/server/api_command |
| 5 | `project.readiness_checked` | `project_id: str`, `ok: bool`, `checks: {contract, harness_model, credentials_ref, tools}`（每项 `{ok: bool, reason: str\|null}`）, `actor: str` | readiness | tracks/supervisor/readiness, tracks/supervisor/service, tracks/server/api_command |
| 6 | `command.accepted` | `command_id: str`, `kind: ServiceCommandKind`, `idempotency_key: str`, `params_digest: str`, `actor: str`, `actor_class: str`, `surface: "cli"\|"http"\|"internal"`, `project_id: str\|null`, `run_id: str\|null` | 命令服务 | tracks/supervisor/service, tracks/supervisor/db, tracks/server/api_command, tracks/cli |
| 7 | `command.deduplicated` | `idempotency_key: str`, `original_command_id: str`, `command_id: str`（= original） | 命令服务 | tracks/supervisor/service, tracks/supervisor/db |
| 8 | `command.rejected` | `kind: str\|null`, `idempotency_key: str\|null`, `reason: "validation_failed"\|"idempotency_conflict"\|"stale_revision"\|"stale_preview"\|"guard_blocked"\|"unauthenticated"\|"forbidden_actor"\|"active_run_exists"\|"not_found"\|"scope_violation"`, `detail: str`, `actor: str\|null`, `actor_class: str\|null` | 命令服务/防护 | tracks/supervisor/service, tracks/server/guard, tracks/server/auth |
| 9 | `command.claimed` | `command_id: str`, `worker_id: str`, `generation: int` | supervisor | tracks/supervisor/worker, tracks/supervisor/lease, tracks/supervisor/db |
| 10 | `command.completed` | `command_id: str`, `result: dict`, `effects: list[str]`（已发生外部副作用描述，可空） | worker 管理器 | tracks/supervisor/worker, tracks/supervisor/db |
| 11 | `command.failed` | `command_id: str`, `failure_class: "recoverable_external"\|"unrecoverable"`, `reason: str`, `detail: str` | worker 管理器 | tracks/supervisor/worker, tracks/supervisor/db |
| 12 | `command.requeued` | `command_id: str`, `from_generation: int`, `reason: "lease_expired"\|"service_restart"` | 恢复 | tracks/supervisor/recover, tracks/supervisor/lease, tracks/supervisor/db |
| 13 | `wait.entered` | `run_id: str`, `wait_class: "ci"\|"quota"\|"network"\|"agent"\|"external"`, `reason: str`, `retry_at: str\|null`（ISO8601）, `known_reset: bool`, `backoff: {interval_s: int, cap_s: int, next_probe_at: str}\|null` | waiting | tracks/supervisor/waiting, tracks/supervisor/worker |
| 14 | `wait.resolved` | `run_id: str`, `wait_class: str`, `resolved_by: "condition_met"\|"retry_at_reached"\|"human_resume"\|"human_retry"` | waiting | tracks/supervisor/waiting, tracks/supervisor/worker |
| 15 | `lease.acquired` | `run_id: str`, `worker_id: str`, `generation: int`, `ttl_s: int` | lease | tracks/supervisor/lease, tracks/supervisor/worker |
| 16 | `lease.released` | `run_id: str`, `worker_id: str`, `generation: int`, `reason: "completed"\|"paused"\|"preempted"\|"expired"` | lease | tracks/supervisor/lease, tracks/supervisor/worker |
| 17 | `worker.late_result` | `run_id: str`, `command_id: str`, `generation: int`, `current_generation: int`, `disposition: "quarantined"` | lease/完成边界 | tracks/supervisor/lease, tracks/supervisor/worker |
| 18 | `schedule.changed` | `active_run: str\|null`, `queued: list[str]`, `reason: "hotfix_preemption"\|"run_terminal"\|"run_paused"\|"run_resumed"\|"run_created"` | 调度器 | tracks/supervisor/scheduler, tracks/supervisor/service |
| 19 | `run.pause_requested` | `run_id: str`, `actor: str`, `command_id: str`（SM-02.5「已接收」） | 命令服务 | tracks/supervisor/service, tracks/supervisor/worker, tracks/server/projections |
| 20 | `run.paused` | `run_id: str`, `actor: str`, `command_id: str`, `at_boundary: str`（SM-02.5「已暂停」） | worker 管理器 | tracks/supervisor/worker, tracks/server/projections |
| 21 | `run.resumed` | `run_id: str`, `actor: str`, `command_id: str`, `from_stage: str` | 命令服务 | tracks/supervisor/service, tracks/supervisor/scheduler |
| 22 | `material.edited` | `run_id: str`, `doc: "story"\|"spec"\|"acceptance"\|"design"`, `from_revision: str`, `to_revision: str`, `actor: str`, `surface: "http"` | 命令服务 | tracks/supervisor/service, tracks/server/api_command, tracks/baseline |
| 23 | `access.denied` | `surface: str`（路由或命令 kind）, `reason: "unauthenticated"\|"forbidden_actor"\|"scope_violation"`, `actor: str\|null`, `actor_class: str\|null` | auth/防护 | tracks/server/auth, tracks/server/guard |
| 24 | `auth.login` | `actor: str`, `outcome: "succeeded"\|"failed"`（不记录口令或 token 本体） | auth | tracks/server/auth, tracks/supervisor/db |

### 1b. 服务命令封闭集（ServiceCommandKind）

命令服务（`tracks/supervisor/service.py`）只受理以下 kind；HTTP 面（§2b）与 CLI 面共用同一集合与同一校验。所有 kind 遵守 SM-01：先持久化（`command.accepted`）再执行；幂等键去重（§1b.2）；执行只允许经 supervisor worker（HTTP 面不在 handler 内执行长任务）。

| # | kind | params（关键字段） | actor_class | 语义/映射 |
|:--|:--|:--|:--|:--|
| 1 | `register_project` | `repo_path: str` | human | 许可范围内登记；越范围 → rejected(outside_permitted_scope)（§1g） |
| 2 | `check_readiness` | `project_id: str` | human | 执行四类探测并落 `project.readiness_checked`（§1a#5） |
| 3 | `create_run` | `project_id: str`, `journey: "feature"\|"hotfix_post"\|"hotfix_dev"`, `version: str`, `story: str\|null`, `issue: int\|null`, `target: str\|null`, `preempt: bool` | human | feature 映射 `trac start` 语义；hotfix_* 复用 v0.8 hotfix 前检；活跃 run 存在且 preempt=false → rejected(active_run_exists)；preempt=true 触发 §1i 换队序列 |
| 4 | `submit_clarification` | `run_id: str`, `doc: str`, `thread_token: str`, `body: str` | human | 经 tracks/discuss writer 追加回复（续接原 run，不新开会话） |
| 5 | `edit_material` | `run_id: str`, `doc: str`, `base_revision: str`, `content: str` | human | 走既有修订流程产生新 revision（落 `material.edited` + Runtime 提交）；base_revision 不匹配 → rejected(stale_revision)；不绕过批准绑定 |
| 6 | `record_stage_approval` | `run_id: str`, `object: str`, `expected_revision: str`, `decision: "approve"\|"revise"` | human | 复用 `cmd_approve` 的 revision 校验；过期 → rejected(stale_revision) |
| 7 | `record_release_decision` | `run_id: str`, `action: "release"\|"delay"\|"return"`, `preview_digest: str`, `reason: str\|null`, `target: str\|null` | human | 复用 v0.8 `release_gate.validate_release_decision`；stale → rejected(stale_preview) |
| 8 | `pause_run` | `run_id: str` | human | 先落 `run.pause_requested`（已接收），worker 到边界后落 `run.paused`（SM-02.5） |
| 9 | `resume_run` | `run_id: str` | human | `run.resumed`；从原合法位置继续 |
| 10 | `abandon_run` | `run_id: str`, `reason: str` | human | 复用 v0.8 轻量终止语义（terminal=cancelled，零新增外部副作用） |
| 11 | `return_stage` | `run_id: str`, `to: str`, `reason: str`, `confirm: bool` | human | 复用 v0.8 通用逃生门（escape barrier/迟到隔离/证据 stale）；跨越已执行不可逆操作需 confirm=true，否则 rejected(validation_failed) 并回显 already_executed 清单 |
| 12 | `retry_run` | `run_id: str`, `clear_evidence: bool` | human | 复用 v0.8 repair/escape 重试语义（SM-01.8）；证据过期/不可恢复 → rejected 且说明原因 |
| 13 | `drive_run` | `run_id: str` | system | supervisor 内部驱动命令：认领 lease 后驱动既有 Executor 至边界（§1d）；human 提交 → rejected(forbidden_actor) |

**1b.1 前置校验顺序（fail-fast，全部先于持久化执行）**：认证/actor_class → 命令防护（§1g）→ 参数 schema → 幂等键查重 → 业务前置（就绪/revision/digest/preview 校验）。任一失败即拒：HTTP 面返回 §2b 对应状态码 + 落 `command.rejected`（或 `access.denied`）；被拒请求不产生任何执行副作用（SM-01.5）。

**1b.2 幂等键合同**：同一 `idempotency_key` + 同一 `params_digest`（canonical JSON 的 sha256）→ 返回原 `command_id` 与当前结果（落 `command.deduplicated`）；同键不同 payload → 409 + `command.rejected(reason=idempotency_conflict)`；缺键的 HTTP 变更请求 → 400（validation_failed）。键的作用域为 service_home 全局（跨项目唯一）。

**1b.3 查询类请求**（§2b 全部 GET + `/healthz`）只读：不经过命令服务、不落任何事件、不取 writer_lock；读路径用独立只读连接（§3 #4）。

### 1c. service.db 存储合同

`<service_home>/service.db`（SQLite，WAL 模式；server 进程是唯一写者）：

| # | table | 关键列 | 写入者/读取者 |
|:--|:--|:--|:--|
| 1 | `service_events` | `seq INTEGER PRIMARY KEY AUTOINCREMENT`, `ts TEXT`, `type TEXT`（§1a 封闭集）, `project_id TEXT NULL`, `run_id TEXT NULL`, `command_id TEXT NULL`, `payload TEXT`(JSON) | 写：命令服务/supervisor；读：时间线与诊断投影、测试 |
| 2 | `projects` | `project_id TEXT PRIMARY KEY`, `repo_path TEXT UNIQUE`, `version TEXT`, `registered_by TEXT`, `registered_at TEXT` | 写：命令服务；读：投影/auth scope |
| 3 | `commands` | `command_id TEXT PRIMARY KEY`, `kind TEXT`, `params_json TEXT`, `params_digest TEXT`, `idempotency_key TEXT UNIQUE`, `actor TEXT`, `actor_class TEXT`, `surface TEXT`, `project_id TEXT NULL`, `run_id TEXT NULL`, `status TEXT`（`accepted\|claimed\|completed\|failed\|rejected`）, `claim_generation INTEGER NULL`, `result_json TEXT NULL`, `created_at TEXT`, `updated_at TEXT` | 写：命令服务/worker 管理器；读：命令状态查询、恢复 |
| 4 | `waits` | `run_id TEXT PRIMARY KEY`, `wait_class TEXT`, `reason TEXT`, `retry_at TEXT NULL`, `known_reset INTEGER`, `backoff_json TEXT NULL`, `entered_at TEXT`（每 run 至多一条活动等待） | 写：waiting；读：投影/恢复 |
| 5 | `leases` | `run_id TEXT PRIMARY KEY`, `worker_id TEXT`, `generation INTEGER`, `acquired_at TEXT`, `expires_at TEXT` | 写：lease；读：调度/防旧 |
| 6 | `auth` | `actor TEXT PRIMARY KEY`, `password_hash TEXT`（scrypt）, `created_at TEXT` | 写：serve 首次启动供给；读：auth |
| 7 | `sessions` | `token_hash TEXT PRIMARY KEY`, `actor TEXT`, `csrf_hash TEXT`, `created_at TEXT`, `expires_at TEXT` | 写/读：auth（只存散列，不存 token 明文） |
| 8 | `schedule` | `id INTEGER PRIMARY KEY CHECK (id=1)`（单行）, `active_run TEXT NULL`, `queue_json TEXT`（有序 run_id 列表） | 写：调度器；读：投影 |

### 1d. 结构化驱动步（IF-DRIVE-001）

**modules**：`tracks/executor/drive.py` 实现；`tracks/supervisor/worker*.py` 消费；`tracks/executor/run_loop.py` 复用其单步语义（CLI 行为不变）。

```python
@dataclass(frozen=True)
class WaitSpec:
    wait_class: str            # "ci" | "quota" | "network" | "agent" | "external"
    reason: str
    retry_at: str | None       # ISO8601；已知 reset 时间
    known_reset: bool
    backoff: dict | None       # {"interval_s": int, "cap_s": int, "next_probe_at": str}

@dataclass(frozen=True)
class DriveResult:
    kind: str                  # "continue" | "await_human" | "await_external" | "terminal" | "failed"
    run_id: str
    stage: str | None
    wait: WaitSpec | None      # kind="await_external" 时非空
    failure: dict | None       # kind="failed" 时非空：{"failure_class": ..., "reason": ...}
    detail: str

def drive_once(repo: Path, run_id: str, *, config: DriveConfig) -> DriveResult: ...
```

`drive_once` 执行一次 decide→issue→execute 周期并返回结构化结果；`await_external` 携带可持久化的 WaitSpec（含 harness 额度信号的 known/unknown reset 区分，§1j）；`failed` 区分 `recoverable_external` 与 `unrecoverable`。supervisor 只在 `continue` 时立即续步；其余一律交回调度/等待层——后台不在进程内热循环（NFR-0152）。既有 `cmd_run` 长循环语义不变（Devon 内部复用 drive_once 属实现自由，CLI 可观察行为不变）。

### 1e. 只读投影合同（IF-QUERY-001）

**modules**：`tracks/server/projections.py` 实现；`tracks/server/api_query.py`、`api_events.py` 消费。所有投影从 tracks.db（只读连接）+ service.db（只读连接）+ 项目文档（只读）现场计算；不维护只在内存中的"运行中"事实。

- `project_overview(home) -> {projects: [{project_id, repo_path, version}], runs: [{run_id, title, version, stage, control_state, human_todos}], human_todo_count: int}`：待人工数量只计合法人工决定（§1i）。
- `project_run_detail(home, run_id) -> {run_id, version, stage, substate, control_state, pause: {requested: bool, effective: bool}, wait: WaitSpec|None, progress: {tasks_done: int, tasks_total: int, tests_passed: int|None, ac_closed: int, ac_total: int}, quality: {gates: [...]}, executions: [{role, task_id, state: "executing"|"waiting"|"failed", log_ref: str}], lease: {generation: int, worker_id: str}}`。测试计数与验收子句闭合计数分列、来源可指（progress.tests_passed 来自最近一次测试结果事件，progress.ac_closed/ac_total 来自 AC 登记处 × 证据链）；不合成百分比、不合成证据。
- `project_timeline(home, run_id, *, run_id_f=None, command_id=None, task_id=None, ac_id=None, after_seq=None) -> {events: [...], cursor: str}`：合并 tracks.db 事件（per-run seq）与 service_events 行，排序键 `(ts, source_rank, seq)`（source_rank: tracks=0, service=1 确定性平局）；每条返回 `{source, seq, ts, type, run_id, command_id, task_id, ac_refs, summary, payload_ref}`；cursor 为不透明游标（末条 (ts, source_rank, seq) 的 urlsafe 编码）。
- `project_ac_chain(home, run_id) -> [{ac_id, layer, test_nodes: [...], latest_result: str|None, candidate_sha: str|None, evidence: [{ref, status: "ok"|"missing"|"stale"|"unreviewed"}]}]`。
- `project_todos(home) -> [{run_id, kind: "stage_approval"|"release_decision"|"clarification"|"triage", object: str, revision: str|None, context: str, action_ref: str}]`：quota/CI/网络等待与环境修复指引不得出现在此（它们在 run detail 的 wait/diagnostics 呈现）。
- `project_service_config(home) -> {wait_initial_s: 60, wait_cap_s: 900, poll_interval_s: 5, poll_idle_cap_s: 60, lease_ttl_s: 30, version: str}`（默认值即 NFR-0152 锁定口径；启动 flag 覆盖后的生效值）。

### 1f. 认证、会话与裁决权属（IF-WEBAUTH-001）

**modules**：`tracks/server/auth.py` 实现；`tracks/server/app.py` 装配（中间件）；命令服务消费 actor_class。

1. **口令供给**：首次启动（`auth` 表为空）要求 `--password-stdin` 或 `TRAC_SERVE_PASSWORD` 环境变量或 TTY 交互输入其一，否则 serve 以非零退出并打印指引；口令以 `hashlib.scrypt`（n=2^15, r=8, p=1, salt=16B 随机）落 `auth` 表；其后启动不再要求口令。明文口令不落盘、不进入日志。
2. **登录/会话**：`POST /api/auth/login`（§2b#1）成功签发 `trac_session` Cookie（HttpOnly、SameSite=Strict、Path=/；loopback http 下不带 Secure）与随会话 CSRF token；服务端只存 token 的 sha256 与 `csrf_hash`，滚动 7 天过期。`POST /api/auth/logout` 使会话失效。
3. **认证边界**：除 `GET /healthz` 与登录页/登录 API 外，全部页面与 `/api/*` 需认证会话；未认证 → API 401 JSON `{"error":"unauthenticated"}`、页面 302 至登录页。变更端点另需 `X-Trac-CSRF` 头等于会话 CSRF token（恒时比较）；不符 → 403 + `access.denied(reason=unauthenticated|forbidden_actor)`。
4. **裁决权属（FR-0314.4 可验证规则）**：讨论线程根评论显式请求特定方（`@Human` 或 `@<评审者>`）裁决的，`set-status resolved`（含 Web 等价入口）仅当操作者等于被请求方时生效；否则状态不变并落 `access.denied(reason=forbidden_actor)`（可审计）。Web 面操作者恒等于认证会话 actor（不接受客户端自报）；CLI 面沿用 discuss 既有 operator 一致性规则并追加同一校验。Agent 发起者自 resolve 此类线程一律无效。
5. **actor_class 判定**：HTTP 会话 → `human`；supervisor/worker 内部调用（进程内直传，不经 HTTP）→ `system`；凡以非 human actor_class 调 human-only kind（§1b 标注 human 者）→ 403 + `command.rejected(reason=forbidden_actor)` + `access.denied`，不产生状态推进。

### 1g. 命令防护与访问范围（IF-CMDGUARD-001 / IF-SECRECY-001）

**modules**：`tracks/server/guard.py`、`tracks/server/redaction.py` 实现；`tracks/server/api_*.py` 装配。

1. **结构化命令**：HTTP 面只接受 §1b 封闭 kind 的结构化 JSON；不存在任何自由文本执行字段；未知 kind/多余字段/类型不符 → 400 + `command.rejected(reason=guard_blocked|validation_failed)`。任意 shell 载荷（如 `{"kind":"run_shell",...}` 或携带 shell 文本的参数字段）被拒绝且无执行效果。
2. **项目访问范围**：许可范围 = serve 启动时 `--repo` 列表（realpath 规范化后的封闭集）；登记越范围 → `project.registration_rejected(reason=outside_permitted_scope)`；文件/产物读取（材料内容、日志、blob）必须解析在已登记项目的仓库内或其 `.tracks/` 证据范围内，越界 → 403 + `access.denied(reason=scope_violation)`。
3. **脱敏（IF-SECRECY-001）**：`redaction.py` 在 API 响应、SSE 帧、HTML 页面与结构化服务日志的**所有出口**统一过滤：环境变量凭据值（启动时采集需保护的名字集合，值仅存内存）与常见密钥模式（token/key 形态）一律替换为 `${NAME}` 受控引用或 `***`；`auth.login`/日志永不记录口令与 token 本体。就绪检查只报告凭据**引用名**是否配置，不回显值。

### 1h. 命令生命周期（SM-01 落点）

| # | SM-01 行 | 事件/出口 |
|:--|:--|:--|
| 1 | .1 先持久化再执行 | `command.accepted`（commands 行 status=accepted）→ 返回 command_id |
| 2 | .2 唯一执行者认领 | `command.claimed`（lease generation 绑定，§1i） |
| 3 | .3 完成 | `command.completed`（result + effects 摘要） |
| 4 | .4 失败 | `command.failed`（recoverable_external / unrecoverable 区分） |
| 5 | .5 拒绝 | `command.rejected`，无副作用 |
| 6 | .6 去重 | `command.deduplicated`，返回原 command_id 与原/当前结果 |
| 7 | .7 恢复重认领 | `command.requeued`（service_restart / lease_expired） |
| 8 | .8 受控重试 | `retry_run` kind；不复活过期证据（沿用 v0.8 stale 语义） |

### 1i. 调度与租约（IF-SCHED-001 / IF-LEASE-001 / IF-PAUSE-001）

**modules**：`tracks/supervisor/scheduler.py`、`lease.py`、`worker.py` 实现。

- **单活动 run**：`schedule` 单行表承载 `active_run` 与有序 `queue`；调度器只对 active run 派发 `drive_run`；其余已受理 run 为排队态（总览/详情可见）。`schedule.changed` 记录每次切换原因。
- **hotfix 优先级换队**：`create_run(journey=hotfix_*, preempt=true)` 且存在活动 run 时，调度器经命令服务依序落：pause_run（活动 run，actor=提交人）→ 激活 hotfix（`schedule.changed reason=hotfix_preemption`）→ hotfix 终态后 resume_run（原 run）。全程命令行可审计（command_id + actor）；preempt=false 时 rejected(active_run_exists)。
- **租约/代次**：`lease.acquired(run_id, worker_id, generation, ttl_s)`；generation 单调递增（取 leases 表现值 +1，SQLite 事务内完成）；worker 存活期间 supervisor 续约（ttl 30s）。认领/完成命令携带 generation；完成经 CAS（`status=claimed AND claim_generation=?`），失败即 `worker.late_result(disposition=quarantined)`——run 状态不受其影响。租约过期：supervisor 先 SIGTERM 子进程（宽限期）后 SIGKILL，再授新代次；被杀 worker 的未完成命令 `command.requeued(reason=lease_expired)`。
- **pause 优先（SM-02.5）**：`run.pause_requested` 一落盘即阻断一切新驱动与自动唤醒（额度恢复、retry_at 到期均不得越过）；worker 在当前驱动边界收尾后落 `run.paused`；`resume_run` 后从原合法位置继续。`暂停请求已接收` 与 `已暂停` 在投影中可区分（§1e）。
- **worker 进程隔离**：worker 为 supervisor 的子进程（`python -m tracks.supervisor.worker_main`），按既有 writer_lock 写 run 面；HTTP handler 永不触达执行路径；进程内执行上下文不跨 HTTP 线程共享（沿用 v0.9 规划改造点）。

### 1j. 外部等待与退避配置（IF-WAIT-001）

**modules**：`tracks/supervisor/waiting.py` 实现；worker/projections 消费。

- 等待分类封闭集：`ci | quota | network | agent | external`；进入即 `wait.entered`（持久化原因与 retry_at/探测计划），恢复即 `wait.resolved`。
- 额度信号来源：harness（opencode backend）返回的 provider 配额/限流类错误响应（既有检测：quota/rate_limit/429/4008 等模式）；**已知 reset 时间**（响应可解析出恢复时刻）→ `known_reset=true` + `retry_at` 精确唤醒；**未知 reset** → `known_reset=false` + 有界退避探测：初始间隔 `wait_initial_s`（默认 60s）指数增长至上限 `wait_cap_s`（默认 900s），探测频率永不低于上限口径（不热循环），不编造倒计时。
- UI 实时通道优先推送（SSE）；断开时轮询回退 `poll_interval_s`（默认 5s），空闲上限 `poll_idle_cap_s`（默认 60s）；服务空闲（无活动 run）时不得有持续轮询负载。
- 上述默认值为规格锁定口径（NFR-0152），经 serve 子命令的 flag（待实现 foundation task）覆盖后的生效值由 `GET /api/service/config` 读出。

### 1k. SM-02 控制态投影（用户可见）

run 的控制态由投影推导（tracks.db 阶段/awaiting + service.db 的 pause/wait/schedule），不是 kernel 新状态机：

| # | 控制态 | 推导 |
|:--|:--|:--|
| 1 | `推进中` | run active 且无等待/暂停/人工门 |
| 2 | `等待外部` | 存在活动 wait 行（quota/CI/网络/agent）；quota/CI 等待不得进入「等待人工」 |
| 3 | `等待人工` | tracks.db awaiting 非空或 AWAITING_RELEASE 且未暂停 |
| 4 | `暂停请求已接收` | 最新 pause 事件为 `run.pause_requested`（尚无 `run.paused`） |
| 5 | `已暂停` | 最新 pause 事件为 `run.paused` |
| 6 | `排队中` | 已受理且非活动 run（schedule.queue 成员） |
| 7 | 终态 | 继承 v0.8 SM-01 终态（released/cancelled/delayed/returned/retry_tail/failed 等），本投影不新增终态；不可恢复失败终态与「等待外部」严格区分（`command.failed(failure_class=unrecoverable)` + run 停在明确失败终态） |

## 2. CLI 接口合同

### 2a. serve 子命令（待实现 Devon foundation task；USAGE 与 `_COMMANDS` 同步后生效）

trac 入口下的子命令文法（散文引用形态，防 fabricated-command 误报；实现后即为真实命令）：

```text
serve --repo <path> [--repo <path> ...] [--host 127.0.0.1] [--port 8000]
      [--home <dir>] [--password-stdin]
      [--wait-initial-s 60] [--wait-cap-s 900] [--poll-interval-s 5] [--poll-idle-cap-s 60]
```

- `--repo`（可重复）声明许可范围（realpath 封闭集）；`--home` 默认 `<首个 --repo>/.tracks/service/`（其下建 service.db），`TRAC_SERVE_HOME` 环境变量可覆盖默认值；`--port 0` 表示随机空闲端口（测试通道）。
- 启动成功：stdout 打印 `serving on http://<host>:<port> (pid <n>)` 与 `Ctrl-C to stop`，落 `service.started`；SIGINT/SIGTERM 优雅停止落 `service.stopped` 并退出 0。
- 启动失败：端口占用 → 退出 1 + stderr；首次启动无口令供给 → 退出 2 + stderr 指引；service.db 不可写 → 退出 1 + 具体原因。健康检查未通过时进程存活但 `/healthz` 返回 503 与原因，工作台不呈现「可用」假象。
- 本版不承诺守护进程/自启/日志轮转（规格期锁定）。

### 2b. HTTP API 合同

基底：`http://<host>:<port>`；除标注【公开】者外全部需认证（§1f.3）；变更端点需 `Idempotency-Key` 头（或同名 body 字段）与 `X-Trac-CSRF` 头。错误统一 `{"error": {"reason": <封闭 reason>, "detail": <str>}}`；成功 JSON 以各端点 schema 为准。

| # | method/path | 输入 | 成功 | 失败 | 备注 |
|:--|:--|:--|:--|:--|:--|
| 1 | `POST /api/auth/login` | `{password}` | 200 `{actor, csrf_token}` + Set-Cookie | 401 `unauthenticated`（落 `auth.login outcome=failed`） | 【公开】 |
| 2 | `POST /api/auth/logout` | — | 204 | — | 会话失效 |
| 3 | `GET /healthz` | — | 200 `{"status":"ok","projects":<int>,"version":<str>,"uptime_s":<float>}` | 503 `{"status":"unavailable","reasons":[{check,reason}]}` | 【公开】唯一豁免认证的端点（本机单用户实例的探活面；§5 风险记录） |
| 4 | `GET /api/service/config` | — | 200 §1e config schema | — | 生效配置（NFR-0152 口径可读出） |
| 5 | `POST /api/projects` | `{repo_path}` | 201 `{project_id, repo_path, version}` | 403 `outside_permitted_scope` / 422 `not_a_tracks_repo` | → register_project |
| 6 | `GET /api/projects` | — | 200 `[{project_id, repo_path, version, readiness: {ok, checks}}]` | — | 只读 |
| 7 | `POST /api/projects/{pid}/readiness` | `{}` | 200 `{ok, checks:{contract,harness_model,credentials_ref,tools}}` | 404 `not_found` | → check_readiness |
| 8 | `POST /api/projects/{pid}/runs` | §1b#3 create_run params | 202 `{command_id, run_id}`（去重时 200 同原 command_id/run_id） | 403/409/422 按 reason | 重复提交（同键同 payload）返回同一 run，不新建 |
| 9 | `GET /api/projects/{pid}/overview` | — | 200 §1e overview schema | 404 | 只读、即时返回（NFR-0150-02） |
| 10 | `GET /api/runs/{run_id}` | — | 200 §1e run_detail schema | 404 | 只读快照，含 `event_cursor` |
| 11 | `GET /api/runs/{run_id}/timeline?command_id=&task_id=&ac_id=&after=` | query | 200 §1e timeline schema | 404 | 只读 |
| 12 | `GET /api/runs/{run_id}/ac-chain` | — | 200 §1e ac_chain schema | 404 | 只读 |
| 13 | `GET /api/runs/{run_id}/todos` | — | 200 §1e todos schema | 404 | 只读 |
| 14 | `GET /api/todos` | — | 200 全局人工待办 | — | 待办中心 |
| 15 | `GET /api/runs/{run_id}/docs/{doc}` | doc ∈ story/spec/acceptance/design | 200 `{revision, content, history: [{revision, ts, actor}]}` | 404 | 只读 |
| 16 | `GET /api/runs/{run_id}/docs/{doc}/diff?from=&to=` | revision 对 | 200 `{from, to, unified_diff}` | 404/422 | 相邻/任意 revision 对比 |
| 17 | `POST /api/runs/{run_id}/docs/{doc}/edits` | `{base_revision, content}` | 202 `{command_id, new_revision}` | 409 `stale_revision` | → edit_material |
| 18 | `POST /api/runs/{run_id}/clarifications` | `{doc, thread_token, body}` | 202 `{command_id}` | 404/422 | → submit_clarification |
| 19 | `POST /api/runs/{run_id}/approvals` | `{object, expected_revision, decision}` | 202 `{command_id}` | 409 `stale_revision` / 403 `forbidden_actor` | → record_stage_approval |
| 20 | `GET /api/runs/{run_id}/release-preview` | — | 200 `{preview_digest, generated_at, stale: bool, stale_reason, summary}` | 404/409 | 只读；stale 时 UI 禁止决定 |
| 21 | `POST /api/runs/{run_id}/release-decision` | `{action, preview_digest, reason?, target?}` | 202 `{command_id}` | 409 `stale_preview` / 403 | → record_release_decision |
| 22 | `POST /api/runs/{run_id}/pause` | `{}` | 202 `{command_id, state: "pause_requested"}` | 404/409 | 两段态先「已接收」 |
| 23 | `POST /api/runs/{run_id}/resume` | `{}` | 202 `{command_id}` | 404/409 | — |
| 24 | `POST /api/runs/{run_id}/abandon` | `{reason}` | 202 `{command_id}` | 404/409 | → abandon_run |
| 25 | `POST /api/runs/{run_id}/return` | `{to, reason, confirm}` | 202 `{command_id}` | 409 + `already_executed` 清单（需 confirm 重提） | → return_stage |
| 26 | `POST /api/runs/{run_id}/retry` | `{clear_evidence?}` | 202 `{command_id}` | 409（证据过期/不可恢复，附原因） | → retry_run |
| 27 | `GET /api/commands/{command_id}` | — | 200 `{command_id, kind, status, result?, failure?}` | 404 | 命令状态轮询（受理后查询结果） |
| 28 | `GET /api/runs/{run_id}/events?after=<cursor>` | cursor | 200 SSE 流（`text/event-stream`；帧 `id: <cursor>` / `event: <type>` / `data: <json>`）或 `wait=0` 时 200 JSON `{events:[...], cursor}` | 404 | 游标补读 + 实时推送；快照 cursor 与事件一致 |

页面（HTML，需认证；未认证 302 → 登录页）：`/`（总览 E-02）、`/login`、`/projects`（登记/就绪 E-03）、`/projects/{pid}/runs/new`（E-04）、`/runs/{run_id}`（详情/时间线/控制区 E-05）、`/runs/{run_id}/review`（澄清与材料审阅 E-06）、`/todos`（待办中心 E-07）、`/runs/{run_id}/release`（发布预览决定 E-08）。文档页经 Vditor 基础集成承载（查看/编辑/版本对比），Vditor 资产一律自源站 `/static/vendor/vditor/` 提供（禁止 CDN），可选渲染引擎（math/mermaid/echarts/emoji 等）在集成配置中禁用（未 vendor）。

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `<service_home>/service.db` | SQLite（WAL）；server 进程唯一写者 | 全部服务面表（§1c）；持久，跨重启 |
| 2 | `<service_home>/` 默认 `<首个 --repo>/.tracks/service/` | 目录 | `TRAC_SERVE_HOME`/`--home` 覆盖；测试经临时 home 隔离 |
| 3 | 各项目 `.tracks/runtime/tracks.db` 与 blobs | 继承 v0.1～v0.8 不变 | worker 子进程经 writer_lock 写；Web 读路径只读连接 |
| 4 | 读路径连接 | `sqlite3.connect(..., uri mode=ro)`、独立事务、短生命周期 | 永不取 writer_lock、永不阻塞推进写路径（NFR-0150） |
| 5 | 服务结构化日志 | stdout JSON 行 `{ts, level, msg, run_id?, command_id?, task_id?, ac_id?}` | 经 §1g.3 脱敏；可按 run/command/task/AC 维度定位 |
| 6 | `tracks/server/static/vendor/vditor/**` + `manifest.json` | vendored 第三方资产（MIT）；Archer 设计期物化 | wheel package-data；`manifest.json` 记录 version/source/逐文件 sha256，CI 校验一致性 |
| 7 | `.tracks/projects/project.toml` | 不变（测试执行合同与 host-contract 段逐字继承） | 既有消费者 |
| 8 | `tracks/server/static/{app.js,styles.css}` 与页面模板 | 实现产物（**待实现 Devon**），路径与打包归本文预留 | 经 §4 出口断言其存在与形态 |

## 4. 可观察出口（测试断言基础）

### 4a. HTTP/CLI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | serve stdout/stderr 与退出码 | §2a 形态；启动失败分类退出码 |
| 2 | `GET /healthz` | 200/503 形态、projects 计数、version、uptime |
| 3 | §2b 全部 API 的 JSON schema/状态码 | 逐端点；错误 reason 封闭集 |
| 4 | 页面 HTML | 登录页形态；Vditor 资产自源站引用（无 CDN 域名）；页面不出现密钥 |
| 5 | SSE 帧 | `id/event/data` 三段；游标单调；重连补读无重复无倒退 |

### 4b. service.db / 事件出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `service_events` §1a 全事件 | append-only、seq 单调、payload 字段封闭集 |
| 2 | `commands` 行 | status 机（accepted→claimed→completed/failed/rejected）、幂等键唯一 |
| 3 | `waits` / `leases` / `schedule` 行 | 等待持久化（retry_at 跨重启保留）、代次单调、单活动 run |
| 4 | 结构化服务日志（stdout） | JSON 行可解析；含 run/command/task/AC 关联键；无密钥 |

### 4c. tracks.db / git / 远端出口（继承）

| # | outlet | assertions |
|:--|:--|:--|
| 1 | tracks.db 既有事件（stage.*、human.*、release.*、publish.* 等） | Web 入口产生的事件类型与 CLI 等价（AC-FR0295-03）；无新增事件类型 |
| 2 | 文档文件与 revision digest | Web 编辑产生新 revision；批准绑定既有 digest 语义 |
| 3 | bare/stand-in 远端事实 | 发布链路继承 v0.8（publish.* 幂等/reconcile） |

## 5. IF Registry

### IF-MTEST-001 M-TEST 测试收集合同（继承 IF-006）

### IF-MTEST-002 M-TEST 测试执行合同（继承 IF-006）

### IF-SHIELD-001 Shield 测试编写合同（继承 IF-006）

### IF-TRACE-001 / IF-TRACE-002 / IF-TRACE-003 trace 合同组（继承 IF-006 / IF-008）

### IF-REACH-001 / IF-REACH-002 reach 合同（继承 IF-006）

### IF-VALIDATE-001 trac validate 校验合同（继承 IF-006；v0.8 扩展不变）

### IF-IMPL-001 / IF-IMPL-002 / IF-IMPL-003 / IF-IMPL-004 / IF-IMPL-005 / IF-IMPL-006 / IF-IMPL-007 M-IMPL 合同组（继承 IF-006）

### IF-DEVON-001 Devon manifest 越界审计合同（继承 IF-006）

### IF-LIVE-001 真实外部旅程与 evidence 合同（继承 IF-006）

### IF-RELEASE-001 / IF-RELEASE-002 / IF-RELEASE-003 release 合同组（继承 IF-006 / IF-008；v0.9 经 Web 入口复用）

### IF-DOCGAP-001 文档评论优先裁定合同（继承 IF-006）

### IF-QUARANTINE-001 outcome 隔离恢复合同（继承 IF-006）

### IF-HOTFIX-001 / IF-HOTFIX-002 / IF-HOTFIX-003 / IF-HOTFIX-004 / IF-HOTFIX-005 / IF-HOTFIX-006 / IF-HOTFIX-007 / IF-HOTFIX-008 / IF-HOTFIX-009 / IF-HOTFIX-010 hotfix 合同组（继承 IF-006；v0.9 经 Web 入口复用前检）

### IF-SELECT-001 / IF-SELECT-002（继承 IF-006）

### IF-EVIDENCE-001 evidence identity/reuse/stale 合同（继承 IF-006）

### IF-LEDGER-001 / IF-FULLCHAIN-001 / IF-RUNCONTRACT-001 / IF-NIGHTLY-001（继承 IF-006）

### IF-PHASE-001 / IF-PHASE-002 / IF-PHASE-003 / IF-GUARD-001 / IF-GUARD-002 / IF-AUTH-001 / IF-AUTH-002 / IF-MUTATION-001 / IF-MUTATION-002 / IF-CLOSURE-001 / IF-DEMO-001 / IF-FAILCLOSED-001（继承 IF-007）

### IF-ADAPTER-003 kernel/executor/cli 语言中立不变量（继承 IF-007；v0.9 允许区追加 `tracks/server/**`、`tracks/supervisor/**` 的既有扫描语义不变——新包同样禁语言 token 硬编码规则之外无例外）

### IF-VERIFY-001 / IF-VERIFY-002 / IF-VERIFY-003 / IF-VERIFY-004 / IF-VERIFY-005 发布验证合同组（继承 IF-008）

### IF-REPAIR-001 / IF-REPAIR-002 / IF-KNOWNISSUE-001 就地修复与 Known Issue 合同组（继承 IF-008）

### IF-ESCAPE-001 / IF-ESCAPE-002 逃生门合同组（继承 IF-008；v0.9 经 Web 入口复用）

### IF-SECURITY-001 合同化安全评估合同（继承 IF-008）

### IF-PUBLISH-001 / IF-PUBLISH-002 发布幂等与 reconcile 合同（继承 IF-008）

### IF-MILESTONE-001 归档与生命周期合同（继承 IF-008）

### IF-JOURNEY-001 三旅程与版本方案合同（继承 IF-008）

### IF-ENVELOPE-001 / IF-ENVELOPE-002 envelope 合同组（继承 IF-008）

### IF-FAILURE-001 失败证据链合同（继承 IF-008）

### IF-HOSTCONTRACT-001 / IF-HOSTCONTRACT-002 宿主合同（继承 IF-008）

### IF-REFERENCE-001 reference host 合同（继承 IF-008）

### IF-ISSUE-001 / IF-ISSUE-002 Issue 合同组（继承 IF-008）

### IF-PIPELINE-001 单次权威测试流水线回归合同（继承 IF-008）

### IF-SERVE-001 服务生命周期与健康合同

- **合同**：§2a/§1a#1-2——单条命令手动启动/停止；`/healthz` 200/503 形态；浏览器关闭不影响推进；重启恢复已登记项目、进行中/等待中命令与等待状态；启动失败分类退出码；不承诺守护/自启/日志轮转。
- **modules**：tracks/cli/serve_cmd.py, tracks/server/app.py, tracks/supervisor/db.py, tracks/supervisor/recover.py。
- **关联**：FR-0288。

### IF-PROJ-001 项目登记与就绪检查合同

- **合同**：§1b#1-2/§1a#3-5——许可范围封闭集内的登记与归属展示；越范围/非 tracks 仓库拒绝；四类就绪探测（合同/harness·model/凭据引用/必需工具）逐项 ok+reason；未就绪阻断创建并给出原因。
- **modules**：tracks/supervisor/service.py, tracks/supervisor/readiness.py, tracks/server/api_command.py, tracks/server/projections.py。
- **关联**：FR-0289、FR-0290。

### IF-CMDSVC-001 持久命令受理与幂等去重合同

- **合同**：§1b/§1h——先持久化再执行；持久 command_id 跨重启可查；幂等键 + params_digest 去重（同键同参返回原结果、同键异参冲突拒绝、缺键拒绝）；CLI/HTTP 同 kind 同事件等价；查询只读不落事件不取写锁；HTTP 不在 handler 内执行长任务。
- **modules**：tracks/supervisor/service.py, tracks/supervisor/db.py, tracks/server/api_command.py, tracks/cli（复用面）。
- **关联**：FR-0291、FR-0292、FR-0295。

### IF-CMDGUARD-001 命令防护与 actor 类别合同

- **合同**：§1g.1/§1f.5——封闭 kind 与 schema 校验；任意 shell 不可经 HTTP 提交执行；human-only kind 的 actor_class 强制；伪造/越权尝试落 `command.rejected`+`access.denied` 可审计且不产生状态推进。
- **modules**：tracks/server/guard.py, tracks/server/auth.py, tracks/supervisor/service.py。
- **关联**：FR-0314。

### IF-DRIVE-001 结构化驱动步与自动推进合同

- **合同**：§1d——drive_once 单步结构化结果五态；supervisor 沿既有阶段状态机自动推进至人工门/终态/等待，无需逐条人工命令；人工门可靠等待不越过；CLI 行为不变。
- **modules**：tracks/executor/drive.py, tracks/executor/run_loop.py, tracks/supervisor/worker.py, tracks/supervisor/scheduler.py。
- **关联**：FR-0297。

### IF-LEASE-001 单一有效执行者租约与防旧合同

- **合同**：§1i——同 run 任一时刻至多一个有效执行者；代次单调；完成 CAS；旧代次迟到结果 quarantine 且可审计；租约过期先终止旧 worker 再授新代次；CLI 与 Web 同一仲裁不双重 dispatch/publish。
- **modules**：tracks/supervisor/lease.py, tracks/supervisor/worker.py, tracks/supervisor/db.py。
- **关联**：FR-0296。

### IF-SCHED-001 单活动 run 调度与 hotfix 换队合同

- **合同**：§1i——单活动 + 有序队列（排队可见）；hotfix 换队 pause→插队→resume 全程经命令服务留审计；preempt=false 拒绝；真并发不实现（范围排除）。
- **modules**：tracks/supervisor/scheduler.py, tracks/supervisor/service.py, tracks/server/projections.py。
- **关联**：FR-0296。

### IF-WAIT-001 外部等待与退避合同

- **合同**：§1j——等待原因与 retry_at 持久化并跨重启保留；quota 已知 reset 精确唤醒、未知有界退避（默认 60s→900s）不编造倒计时；不热循环；等待不冒充人工待办；配置可读出。
- **modules**：tracks/supervisor/waiting.py, tracks/supervisor/worker.py, tracks/server/projections.py。
- **关联**：FR-0298、FR-0299、NFR-0152。

### IF-RECOVER-001 崩溃重启恢复合同

- **合同**：§1h.7/§1i——服务/worker 重启后找回进行中/等待中命令与等待状态（retry_at 保留）；未完成 effects 按既有 WAL/reconcile 恢复；已完成副作用经幂等键跳过；不丢单不重复。
- **modules**：tracks/supervisor/recover.py, tracks/supervisor/worker.py, tracks/supervisor/db.py。
- **关联**：FR-0288、FR-0300。

### IF-PAUSE-001 pause/resume 两阶段优先合同

- **合同**：§1i/§1k——「已接收→已暂停」两段可区分；pause 优先于一切自动唤醒；resume 从原合法位置继续且保留已完成工作。
- **modules**：tracks/supervisor/service.py, tracks/supervisor/worker.py, tracks/server/projections.py。
- **关联**：FR-0310。

### IF-WEBGATE-001 Web 人工门绑定与拒绝合同

- **合同**：§1b#6/7/10/11/12——Web 批准绑定实际审阅 revision（过期拒绝）；发布三择一绑定 preview_digest（stale 拒绝）；回拨沿用允许目标/不可逆确认/屏障/证据 stale；受控重试不复活过期证据不绕质量门；全部复用既有校验、不直接 append 事件。
- **modules**：tracks/server/api_command.py, tracks/supervisor/service.py, tracks/cli/gate_cmd.py（共享校验）, tracks/executor/release_gate.py, tracks/executor/escape.py。
- **关联**：FR-0308、FR-0309、FR-0311、FR-0312、FR-0313。

### IF-QUERY-001 只读投影查询合同

- **合同**：§1e——总览/详情/进度/AC 链/待办/诊断/时间线投影 schema；进度双计数分列、来源可指、不编造百分比；诊断同源事件；查询只读独立事务不阻塞写路径；执行状态为 run/任务级（不含 Agent 会话内容）。
- **modules**：tracks/server/projections.py, tracks/server/api_query.py。
- **关联**：FR-0301、FR-0302、FR-0303、FR-0304、FR-0305、FR-0307。

### IF-STREAM-001 事件订阅与游标补读合同

- **合同**：§2b#28/§1e timeline——SSE 推送 + 游标补读；快照 cursor 与事件一致；重复/乱序不使 UI 倒退；实时与历史补全共用同一事件源；断开回退轮询（5s/60s 上限口径）。
- **modules**：tracks/server/api_events.py, tracks/server/projections.py。
- **关联**：FR-0306、NFR-0152。

### IF-WEBAUTH-001 本机单用户认证与裁决权属合同

- **合同**：§1f——口令供给/scrypt 存储/会话与 CSRF；未认证拒绝（API 401/页面 302）；裁决线程仅被请求方经认证入口可 resolved；伪造不自效并留审计。
- **modules**：tracks/server/auth.py, tracks/server/guard.py, tracks/discuss（复用）。
- **关联**：FR-0314、FR-0293。

### IF-SECRECY-001 凭据脱敏与访问范围合同

- **合同**：§1g.2-3——受控凭据引用；UI/API/日志/SSE 全出口脱敏；文件与产物读取限于授权项目及证据范围，越界拒绝并审计。
- **modules**：tracks/server/redaction.py, tracks/server/guard.py, tracks/server/api_query.py。
- **关联**：FR-0315。

### IF-DOCREV-001 材料审阅/编辑 revision 合同

- **合同**：§2b#15-17/§1b#5——材料当前版本与 revision 标识展示；revision 间可见对比；Web 编辑经既有修订流程产生新 revision、待批准条目随之更新、不绕过批准绑定；Vditor 基础集成（查看/编辑/版本对比），资产自源站提供。
- **modules**：tracks/server/pages.py, tracks/server/api_query.py, tracks/server/api_command.py, tracks/supervisor/service.py, tracks/baseline.py（复用）。
- **关联**：FR-0294。
