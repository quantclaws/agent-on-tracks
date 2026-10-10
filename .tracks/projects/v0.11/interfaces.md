---
envelope: tracks-envelope:v2
interfaces_id: IF-011
spec_ref: SPEC-011
arch_ref: ARCH-011
created: 2026-10-11
status: draft
sha:
---

# v0.11 — 接口与类型化 Schema：自驱动工作台

本文是 IF-010 的增量延伸：v0.1～v0.10 的事件溯源、封闭事件集、命令 WAL、审批 revision 绑定、release preview/三择一、escape barrier、发布幂等/reconcile、guard registry、CLI 合同、v0.9 服务面合同（HTTP API、持久命令服务、supervisor 驱动/租约/等待、只读投影、事件订阅、材料审阅）与 v0.10 工作台交互合同（SPA chrome、名字绑定、文档中心、docs_revision 双身份、时间线弱化版、守卫节点分类）全部继承；v0.11 新增的是** Releases 窗口合同**（五 tab 收敛、注册表/backlog/归档投影）、**intake 面**（统一入口、Scribe 评估、SM-01 四动作）、**M-START 门与 backlog 去向合同**、**hotfix 入口呈现合同**、**人工门命令端点与 worker 执行合同**（含自驱动闭环）、**awaiting 动作面板合同**、**时间线完整绘制与回拨安全合同**、**评审轮写入通道合同**与**两份 flow.md 合同**（TRIAGE 报告栏目、评审人复核）。本文只写外部可观察契约；模块内部组织见 architecture.md。

## 0. 延续性（什么不变）

- IF-001/003/004/005/006/007/008/009/010 的全部合同逐字继承；既有 IF 标识不可重定义或复用，IF-011 只追加新标识（§5）。
- tracks.db 的 EVENT_TYPES 封闭集本版追加一个成员 `backlog.dispositioned`（§1d）；其余成员与 payload 语义不变，`backlog.recorded` 仅 payload 追加可选键（§1d.2）。
- kernel 的 `COMMAND_KINDS` 封闭集不变；服务命令封闭集（IF-009 §1b 十三种 kind）追加五个成员（§1b），既有十三种 kind 的受理/校验/幂等/actor_class 语义逐字不变。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]` 测试执行合同、`[host-contract.*]` 等段逐字不变；本版唯一变更为 `[layout.devon].writable` 追加 `.tracks/wiki/flow.md` 单文件授权（§0.1 #14）；`ui` marker 通道隔离不变。
- 既有 CLI 子命令集合与 USAGE 不变；`trac start`/`trac triage`/`trac review` 语义不变（管理后门）。服务命令的 CLI/HTTP 等价语义（IF-CMDSVC-001）对新 kind 同样适用。
- `prism.verdict`、`human.approval`、`human.triage`、`human.review`、`release.decided`、`material.edited`、`backlog.recorded` 等既有事件的类型与 payload 语义不因本版改变；`lex.verdict` 的 verdict 词汇封闭集不变，仅 revise 分支的计算条件收窄（§1p.2）。
- SSE 合并序/游标补读/轮询回退、CSRF + Idempotency-Key、redaction 全出口脱敏、本机单用户边界（RP-01）全部继承；全部新端点遵守同一契约（§2b 基底约定）。
- v0.10 的 docs_revision/trio 双身份与 `tracks/baseline.py` 零触碰原则继承；`docs_revision` 仅定义域补全（§1n.1，纯扩展）。守卫节点两类纪律（IF-GREENGUARD-001/§1u of IF-010）继承，v0.11 的守卫集合见 test-plan §8.1。

### 0.1 v0.11 合同变更清单（显式声明）

| # | 变更 | 旧合同（v0.10） | 新合同（v0.11） | 理由 |
|:--|:--|:--|:--|:--|
| 1 | PAGES/URL 收敛 | `PAGES` 8 名；URL 含 /projects、/projects/{pid}/runs/new、/runs/{run_id}/review；tab 条七项（IF-010 §1l.3） | `PAGES` 6 名（login/releases/run_detail/todos/release/docs）；三个旧 URL 302 至 `/`；tab 条五项 Releases/Docs/Todos/Settings/Account（§1e.1） | FR-0333/FR-0334（破坏性变更 GO 已确认） |
| 2 | 服务命令封闭集 | 十三种 kind（IF-009 §1b） | 追加 `submit_intake`/`decide_intake`/`dispose_backlog`/`record_triage`/`record_review`（§1b 表 #14-18） | FR-0338/FR-0340/FR-0342 |
| 3 | service_events 封闭集 | IF-009 §1a 24 成员 + IF-010 #25 | 追加 `intake.submitted`/`intake.evaluated`/`intake.decided`（§1a #26-28） | FR-0338 审计 |
| 4 | service.db schema | IF-009 §1c 八表 + IF-010 auth 演进 | 追加 `intakes` 表（§1c #9） | FR-0338 |
| 5 | tracks.db 事件集 | EVENT_TYPES 封闭（IF-010 零追加） | 追加 `backlog.dispositioned`；`backlog.recorded` payload 追加可选键 source/content/intake_id（§1d） | FR-0340 去向可查 |
| 6 | 人工门命令执行 | 受理持久化（command.accepted）即终态——worker 仅执行 drive_run，无门执行面 | 四类人门命令（含既有 record_stage_approval/record_release_decision）由 supervisor 认领、worker 经共享人门管道执行落 human.*/release.decided 域事件（§1j.3） | FR-0342/FR-0345（v0.9 声明面的落地） |
| 7 | run detail 投影 | control_state/executions/todos/wait/stage（IF-009 §1e） | 追加 `awaiting_gate` 与 `return_options` 字段组（§1k.1/§1m.1） | FR-0343/FR-0337 |
| 8 | docs_revision 定义域 | 六件套不全的版本目录无 token（OSError → None → 编辑面拒绝） | 缺失文档计 `label:missing`，目录存在即有 token；齐全目录值逐字节不变（§1n.1） | FR-0344 评审轮写入 |
| 9 | #17 编辑面 base | base_revision 必须等于当前 trio 摘要 | trio 摘要不可得（pre-approval 窗口）时接受 `base_revision: null`；摘要可得时 null 被拒（§1n.2） | FR-0344 |
| 10 | submit_clarification params | {run_id, doc, thread_token, body}（thread_token 必填） | thread_token 与 anchor_line 二选一：anchor_line 以会话 actor 发起新根线程（§1n.3） | FR-0344 inline-comments |
| 11 | lex.verdict revise 条件 | 存在未决线程且裁决权属方非 Human → revise（IF-010 §1s.1） | revise 仅当存在评审人自有未决 findings 线程；非本人线程不构成 revise 依据（§1p.2；Human park 分支不变） | FR-0347 |
| 12 | M-START 门脏树语义 | CLI 面：脏树不拒绝（v0.5 AC-FR0267-02）；服务面 create_run 无脏树检查 | UI 门（decide_intake develop）按 flow.md §3.1 拒绝脏树；CLI 语义不变（§1g.2，显式分叉记录） | FR-0339 |
| 13 | 时间线呈现 | IF-TIMELINE-001 弱化版（浮层占位） | 完整绘制 + 固定详情栏替换浮层（§1l；IF-TIMELINE-002 为权威） | FR-0335/FR-0336（不可协商项） |
| 14 | flow.md 修订交付通道与 Devon 写域 | flow.md 历史 revisions 经 operator/Runtime 提交；agent scaffold 保留域拒收 `.tracks/**`（doc_face 关闭集） | ARCH-011 §1.0.12 钉定 v0.11 修订全文；`[layout.devon].writable` 追加 `.tracks/wiki/flow.md` 单文件授权，Devon 任务逐字落地；Archer 不物化 flow.md（§1o/§1p 合同文本以钉定文本为准） | FR-0339/FR-0346/FR-0347（两次 checkpoint 拒绝实证后的合规通道） |

## 1. 跨模块合同

### 1a. 服务面事件封闭集（service_events）追加成员

IF-009 §1a 24 成员与 IF-010 #25 逐字继承；v0.11 追加三个成员（append-only、seq 单调、payload JSON 既有语义不变）：

| # | event | payload（关键字段） | producer | modules |
|:--|:--|:--|:--|:--|
| 26 | `intake.submitted` | `intake_id`, `project_id`, `source ∈ {text, issue, backlog}`, `content_sha`, `issue?`, `origin_entry?`, `actor` | 命令服务（submit_intake 受理） | tracks/supervisor/service, tracks/supervisor/db |
| 27 | `intake.evaluated` | `intake_id`, `report`（§1f.2 schema）, `attempt`, `actor="scribe-evaluator"` | intake 评估 worker | tracks/supervisor/intake |
| 28 | `intake.decided` | `intake_id`, `action ∈ {backlog, archive, remove_backlog, develop}`, `outcome`（backlog_entry_id / archive / gate 结果）, `actor` | 命令服务（decide_intake 受理） | tracks/supervisor/service |

### 1b. 服务命令封闭集（ServiceCommandKind）扩展

IF-009 §1b 十三种 kind 逐字继承；v0.11 追加五种。**执行形态分界**（architecture §3.3）：短写类（#14/15/16 与既有 #4）受理期同步执行（edit_material 先例）；人门类（#17/18 与既有 #6/7）由 supervisor 认领、worker 执行（§1j.3）。

| # | kind | params | actor | 语义与失败 |
|:--|:--|:--|:--|:--|
| 14 | `submit_intake` | `project_id`, `source ∈ {text, issue, backlog}`, `content?`（text/backlog 来源的输入正文）, `issue?`（issue 来源的 issue number）, `origin_entry?`（backlog 来源的条目 ULID） | human | 建 intakes 行（status=submitted）+ `intake.submitted`；issue 来源受理期取文（失败 → rejected(issue_fetch_failed) 携分类 next，IF-HOTFIX-011 分类映射复用）；source 三选一闭合校验 |
| 15 | `decide_intake` | `intake_id`, `action ∈ {backlog, archive, remove_backlog, develop}`, `confirm?`（develop 的未合并分支确认） | human | 前置：intake 存在且 status=evaluated（否则 rejected(intake_not_ready)）；remove_backlog 仅 source=backlog（否则 rejected(validation_failed)）；各动作语义见 §1f.3/§1g |
| 16 | `dispose_backlog` | `project_id`, `entry_id`, `disposition ∈ {removed, promoted}`, `to?`（promoted 的目标版本） | human | 前置：条目存在且未处置；落 `backlog.dispositioned`（§1d.3）；promoted 需 to 非空 |
| 17 | `record_triage` | `run_id`, `decision ∈ {go, no_go, park}` | human | 受理前置：run 存在且 awaiting="triage"（否则 rejected(awaiting_mismatch)）；worker 执行落 `human.triage`（payload decision/actor，语义与 `trac triage` 等价） |
| 18 | `record_review` | `run_id`, `action ∈ {comment, no_comment}` | human | 受理前置：awaiting ∈ {review, review_pending_threads}（后者为 FR-0332 §1s.3 恢复语义）；worker 执行落 `human.review`（与 `trac review` 等价） |

幂等键合同（IF-009 §1b.2）对新 kind 全量适用：同键同 params → 原命令去重；同键异 params → 409 idempotency_conflict；HTTP 变更缺键 → 400。

### 1c. service.db 存储合同演进

IF-009 §1c 八表与 IF-010 auth 演进继承；v0.11 追加一张表：

| # | table | 关键列 | 写入者/读取者 |
|:--|:--|:--|:--|
| 9 | `intakes` | `intake_id TEXT PRIMARY KEY`, `project_id TEXT`, `source TEXT`, `content TEXT`, `issue INTEGER NULL`, `origin_entry TEXT NULL`, `status TEXT`（`submitted\|evaluating\|evaluated\|decided\|failed`）, `report_json TEXT NULL`, `decision TEXT NULL`, `decided_at TEXT NULL`, `created_at TEXT`, `updated_at TEXT` | 写：命令服务/intake worker；读：intake 读模型、Releases 投影归档节、恢复（evaluating 超时回收）。`CREATE TABLE IF NOT EXISTS` 幂等建表 |

### 1d. tracks.db 事件合同扩展

1. **EVENT_TYPES 追加 `backlog.dispositioned`**：payload `{entry: <backlog.recorded 的 run_id ULID>, disposition: "removed"|"promoted", to: <版本号|null>, reason?: str}`；事件身份沿用 run-less ULID（与被处置条目同一 ULID 空间）；kernel 归约为幂等标记（不参与活跃 run 判定，`_on_backlog_recorded` 的 phantom 语义不变）。
2. **`backlog.recorded` payload 追加可选键**：`source ∈ {text, issue, backlog, active_run, triage}`、`content`（条目正文）、`intake_id`——统一入口（§1f.3 动作①）与 M-START 门 active-run 出口（§1g.2）两处新来源携带；既有 `trac start`/triage no-go 的 payload 形态不变（缺省键视为 legacy）。
3. **投影语义**：backlog 条目 = backlog.recorded 事件按 ULID 聚合；存在指向该 entry 的 backlog.dispositioned 即已处置（条目从折叠组移除、去向可查：disposition/to 字段）。Releases 投影（§1e.2）是唯一呈现面；store 的 `backlog` 表不改 schema。

### 1e. Releases 窗口与投影合同（IF-RELEASES-001）

**modules**：`tracks/server/pages.py`（壳/URL/tab 集）、`tracks/server/projections.py`+`api_query.py`（聚合投影）、`tracks/server/static/app/releases.js`/`tabs.js`/`sidebar.js`（视图，待实现 Devon）。

1. **页面与 tab 封闭集**：`PAGES = {login, releases, run_detail, todos, release, docs}`；URL 表：`/`→releases、`/login`、`/runs/{run_id}`→run_detail、`/todos`、`/runs/{run_id}/release`→release、`/docs`→docs。旧 URL `/projects`、`/projects/{pid}/runs/new`、`/runs/{run_id}/review` 302 至 `/`。tab 条功能项封闭集五项（固定顺序）：releases/docs/todos/settings/account——Settings 齿轮只开 tab 不换 sidebar、Account 菜单仅 logout（继承）。SM-04 tab 语义、显式降级、真数据与写安全（IF-010 §1l.4/5/6）逐字继承。
2. **`GET /api/projects/{pid}/releases`**（§2b #36）返回 `{summary: {project_id, registered_version, repo_name}, versions: [{version, runs: [{run_id, stage, status, awaiting, updated_ts}]}], backlog: {count, entries: [{entry_id, version, source, content, recorded_ts, disposition: null|"removed"|"promoted", to}]}, archive: [{intake_id, content, decided_ts}]}`——versions 按版本号逆序；runs 经 run 面投影；backlog 节按 §1d.3 派生；archive 节读 intakes 行（status=archived 对应 decision=archive）。sidebar 三组（注册表/backlog 折叠组/归档查询组）与摘要行语义：注册项目/版本信息仅为窗口内摘要行，不在 sidebar 罗列其他 tab 下级内容。

### 1f. intake 面：统一入口与建议面板（IF-INTAKE-001）

**modules**：`tracks/supervisor/service.py`（受理/同步执行）、`tracks/supervisor/intake.py`（评估 worker，待实现 Devon）、`tracks/server/api_query.py`+`api_command.py`（端点）、`tracks/server/static/app/intake.js`（面板，待实现 Devon）、`tracks/templating.py`（分类）。

1. **生命周期与状态机（SM-01）**：`submitted → evaluating → evaluated → decided`（失败态 `failed` 可重派，≤3 预算）。未列出的状态转移不允许：提交输入→待决（evaluated）；待决→backlog（动作①，§1d.2 事件）；待决→已归档（动作③，intakes 行 status=archived）；待决→开发中（动作④，M-START 门 §1g）；待决/backlog→已删除（动作②，仅来源为 backlog，落 backlog.dispositioned(removed)）；backlog→开发中（转正 = submit_intake(source=backlog, origin_entry) 走新建流程，develop 成功后处置原条目为 promoted）。动作①②③为终态：不开 run、不建分支。
2. **Scribe 评估 report schema**（`intake.evaluated` payload 与 intakes.report_json 同构）：`{feasibility: {verdict: "feasible"|"blocked", blockers: [...]}, duplicates: {found: bool, items: [{ref: "FR-XXXX@vN", summary, suggestion: "no_go"|"enhance"}]}, conflicts: {found: bool, pairs: [{new_item, existing_ref}]}, title: str（建议标题，非空）, input_form: "raw"|"complete", summary: str}`——重复/冲突栏目与 flow.md §4.6 同规则（跨版本引用指认既有 FR/AC；冲突对列新需求 vs 既有 FR；GO 需显式确认已知晓冲突）。
3. **端点语义**：`POST /api/projects/{pid}/intakes`（#39）→ 202 `{command_id, intake_id}`；`GET /api/projects/{pid}/intakes/{id}`（#38）→ `{intake_id, source, status, report?, decision?, created_ts}`；`GET /api/projects/{pid}/intakes`（#37）→ 列表（`?status=` 过滤）。轮询承载评估期（无 SSE 推送，architecture §5.1）。
4. **输入分类与采纳**：`classify_story_input(content)` 纯函数——解析出 story frontmatter（story_id/title 等）与固定章节结构即 `complete`，否则 `raw`。raw → story 骨架（§1 逐字保留）；complete → 原样采纳为初始 story 文档，不包裹进 §1、不改写重排。机器分类为权威（intake report 的 input_form 为建议）。
5. **分流边界**：统一入口与建议面板不出现 hotfix 推荐、不路由到 hotfix 流程；hotfix 意图走独立 hotfix 入口（§1i）。

### 1g. M-START 门（IF-MSTART-001）

**modules**：`tracks/supervisor/service.py`（decide_intake(develop) 同步执行）、`tracks/supervisor/worker_main.py`（bootstrap story capture 扩展）、`tracks/templating.py`（title/形态渲染）。

1. **develop 动作执行序**（flow.md §3.1 v0.11 版）：(a) 活跃 run（schedule.active_run）→ 落 `backlog.recorded` {reason: active_run, source, content, intake_id}，响应 200 `{outcome: "backlog_recorded", active_run}`——合法终态，不开新 run 不建分支；(b) 工作区不干净（`git status --porcelain` 非空）→ rejected(dirty_tree) 携原因，不建分支；(c) releases/* 未全并入 main → 无 confirm 时 409 `unmerged_release_branches` {branches: [...], consequence}；confirm=true 继续、confirm=false 取消退出（不建分支）；(d) 通过 → 版本推导 `releases/v{N+1}`（项目既有 release 版本最大 minor + 1）→ 委托既有 `create_run`（journey=feature, version, story, 追加 `title` 与 `story_form ∈ {raw, complete}` 可选 params）受理与调度。
2. **story capture 扩展**：run 面 bootstrap 以 `title` 预填 story frontmatter（Scribe 建议标题，decide_intake 携带），按 `story_form` 渲染（raw 骨架/complete 原样采纳）；无这些 params 的 create_run（既有调用面）行为不变。
3. **不建半截**：全部门前置于持久化（SM-01.5）；拒绝/确认等待不残留命令效果。CLI `trac start` 语义不变（管理后门；脏树分叉见 §0.1 #12）。

### 1h. backlog/归档存储与去向（IF-BACKLOG-001）

**modules**：`tracks/kernel/events.py`+`machine_lifecycle.py`（事件/reducer）、`tracks/supervisor/service.py`（处置执行）、`tracks/server/projections.py`（投影）。

1. **真相与派生**：backlog 唯一真相是 `backlog.recorded` 事件（§1d.2）；去向唯一真相是 `backlog.dispositioned`（§1d.3）。投影从事件序派生，不新增第二存储。
2. **归档存储区分**：归档条目只存在于服务面（intakes 行 + `intake.decided(action=archive)` 审计），与 backlog（run 面事件）物理分储；归档可查（§1e.2 archive 节）但不进入开发（无转正/开发入口）。
3. **处置语义**：`dispose_backlog`（§1b #16）或 decide_intake 联动落同一 `backlog.dispositioned` 事件（合同唯一，实现归 Devon）；处置后条目从 backlog 投影移除、去向（removed/promoted + to）可查。

### 1i. hotfix 入口呈现（IF-HOTFIXUI-001）

**modules**：`tracks/server/api_query.py`（只读预检投影）、`tracks/supervisor/service.py`（既有 create_run 前检复用）、`tracks/server/static/app/releases.js`（入口视图）。

1. **`GET /api/projects/{pid}/hotfix-preview?issue=&scenario=`**（#44，只读）：scenario ∈ {post-release, dev}；服务端跑既有 `precheck_hotfix_report`（issue 取文经 stand-in/live 通道）+ AC 锚定可得性（目标版本 baseline 三件存在性与 approved 状态），返回 `{scenario, issue: {number, title, url?}, precheck: {status: "ok"|"rejected", reason?, next?}, anchoring: {status: "anchored"|"unanchored", target_version?, details}}`。
2. **创建与退出**：创建走既有 `POST /api/projects/{pid}/runs`（journey=hotfix_post|hotfix_dev；§1b #3 受理前检 fail-closed——锚定不成立即 rejected，不建半截 fix 分支）；UI 在 `anchoring.status=unanchored` 或 precheck rejected 时明示退出 hotfix 并指引转 backlog（统一入口）/new feature。in-run hotfix_triage 锚定门不 UI 化（architecture §5.1，面板呈现原因/影响与指引）。

### 1j. 人工门命令端点与 worker 执行（IF-GATEUI-001）

**modules**：`tracks/server/api_command.py`（端点）、`tracks/supervisor/serve_cmd.py`（认领）、`tracks/supervisor/gate_exec.py`（执行，待实现 Devon）、`tracks/executor/result_checkpoint.py`+`tracks/cli/run_cmd.py`（共享人门管道抽取）。

1. **新端点**：`POST /api/runs/{run_id}/triage`（#42）`{decision}` → record_triage；`POST /api/runs/{run_id}/review`（#43）`{action}` → record_review。受理前置（awaiting 匹配，§1b #17/18）+ CSRF + 幂等 + guard 全管道；重复提交不产生重复事件（幂等键去重 + awaiting 前置：门已过再提交即 rejected(awaiting_mismatch)）。
2. **approve/release-decision 接入**：既有 #19/#21 端点语义不变，UI 面板直连。
3. **worker 执行面**：supervisor tick 追加门命令认领——accepted 的 record_triage/record_review/record_stage_approval/record_release_decision 由 worker 认领（claim CAS + 租约防旧继承），`gate_exec` 经共享人门管道（与 CLI 等价：`trac triage`/`trac review`/`trac approve`/`trac release` 的校验与提交核心）在 writer_lock 下落 `human.triage`/`human.review`/`human.approval`/`release.decided` 域事件，命令标记 completed；执行失败落 command.failed（closed failure_class）。**自驱动闭环**：门事件落盘后，下一 tick 常规 drive 推进 run 至下一道边界——门间无需手工 dispatch，CLI 不作为常规推进路径。
4. **失败语义**：门命令执行与 run 推进解耦——执行成功即命令 completed（run 是否立即越门由 kernel 归约决定）；崩溃恢复经既有 requeue（§4.6 of ARCH-011）。

### 1k. awaiting 动作面板投影（IF-AWAIT-001）

**modules**：`tracks/server/projections.py`（awaiting_gate 字段组）、`tracks/server/static/app/timeline.js`（面板）。

1. **`awaiting_gate` 字段组**（run detail 追加字段）：`null`（非 awaiting）或 `{type, reason, impact, primary_action}`——`type` 为 awaiting 封闭集成员；`reason`/`impact` 从 run 面事实派生（等待原因与影响面，人可读）；`primary_action` 见下。
2. **type → primary_action 封闭映射**：triage → `triage`（#42）；review / review_pending_threads → `review`（#43，后者为恢复重评）；approval → `approve`（#19）；AWAIT_RELEASE → `release-decision`（#21）；escalation → `retry`（#26）；rollback → `return`（#25 确认面）；hotfix_triage → `guidance`（无动作端点，原因/影响 + owning surface 指引）。映射外无入口；非 awaiting 节点（awaiting_gate=null）下栏不呈现门动作入口，仅节点详情。
3. **同一 surface**：下栏详情与动作面板为同一 surface（E-02/E-04）；Review 移除后的阶段评审经 review 面板 + 评审轮写入通道（§1n）承接。

### 1l. 时间线完整绘制（IF-TIMELINE-002）

**modules**：`tracks/server/static/app/timeline.js`（重绘）；数据源为既有 timeline/ac-chain/run detail 投影（零新数据源，含 stage_order 单源）。

1. **绘制合同**：13 阶段 canonical 线性轨道；每次 attempt 独立节点不折叠；方向连接表达推进；回拨（stage 回退/escape 事件对）以可辨认回边表达来源→目标；节点五态 `done/active/pending/attention/void`（已完成/活跃/待处理/需处理/失效）由事件序派生，以图标形状 + `data-state` 标签双通道区分（不只靠颜色）；初始视图聚焦活跃节点保留邻近上下文；全部历史可导航到达。
2. **固定详情栏**：鼠标选中节点（不要求键盘可达）→ main area 下方固定详情栏（`data-testid=detail-pane`，非浮层、位置固定）加载节点详情：起止时间/状态/责任方/关联 artifact/revision/关键 evidence/错误/状态转移原因/回拨合法性——全部真实投影数据；不可查看/无数据节点可读降级（不伪造）；「选中查看」与「当前活跃」双标记区分；owning surface 稳定入口 + 返回同一 run 与可识别节点上下文。awaiting 节点时详情栏即动作面板（§1k）。
3. **数据一致**：渲染内容与 timeline/ac-chain 响应逐字段一致，不合成第二来源，无 mock 数据。

### 1m. 回拨安全（IF-RETURN-001）

**modules**：`tracks/server/projections.py`（return_options）、`tracks/server/static/app/timeline.js`（指针/确认面）、既有 return_stage 命令（#25）。

1. **`return_options` 字段组**（run detail 追加）：`{allowed: bool, targets: [{stage, already_executed: [...]}]}`——与 CLI `trac return` 同一合法性规则共享计算（目标校验 + `_already_executed_ops`）；`allowed=false`（targets 空）即无回拨入口（UI 无控件）。
2. **确认与复验**：发起回拨 → 呈现目标与后果（含 already_executed 清单——不伪装撤销外部副作用）→ 显式确认 → `POST /api/runs/{run_id}/return` {to, reason, confirm: true}（#25 既有语义：confirm 缺省即 409 回显 already_executed 清单要求确认）；服务端复验失败给出原因与合法继续位置。

### 1n. 评审轮写入通道（IF-DOCSWRITE-001）

**modules**：`tracks/supervisor/service.py`（docs_revision 全域化/null-base/clarification 扩展）、`tracks/server/static/app/docs.js`+`discussions.js`+`editor.js`（写控）。

1. **docs_revision 全域化**：缺失文档计 `label:missing`（`doc_body_sha` 语义对缺失文件退化为固定 missing 标签，与 `m_impl_baseline_digest` 约定同构）；版本目录存在即有 token；六件套齐全目录的值与 v0.10 逐字节一致（纯扩展）。`tracks/baseline.py` 与 trio 摘要（FR-0308 绑定 token）零触碰。
2. **#17 pre-approval 模式**：trio 摘要不可得（spec/acc 未存在——批准基线结构上不可能存在）时 `base_revision: null` 合法（`material.edited` 的 from_revision 记 null）；摘要可得时 null 被拒（stale_revision，必须绑定）。#35 面 base 恒为当前 docs_revision（全域化后无不可得形态）。
3. **inline-comments**：`submit_clarification` params 扩展——`thread_token` 与 `anchor_line` 二选一（都缺/都给 → 422）：thread_token 模式为既有回复；anchor_line 模式经 tracks/discuss writer 以会话 actor 在该行发起新根线程（open），落盘可查（服务端解析投影可见）。对 `/discussions` 路径仍无写端点（v0.10 §1p.3 边界保持——本扩展落 #18 澄清面，不新增讨论 mutate 端点）。
4. **失败语义**：写失败（网络/5xx）编辑内容不丢可重试、不伪报成功（api.js 既有失败反馈面）；评审轮写控仅在 awaiting_gate.type ∈ {review, review_pending_threads, triage} 的 run 上下文呈现（非评审轮无额外写控，既有编辑面语义不变）。

### 1o. TRIAGE 报告固定栏目（IF-SPLIT-001）

**modules**：`.tracks/wiki/flow.md` §4.6（钉定文本 ARCH-011 §1.0.12，Devon 任务落地）、`tracks/agents/Scribe.md`（派发合同纪律，待实现 Devon）、`tracks/supervisor/intake.py`（report schema §1f.2）。

1. **栏目合同**：任一新 story 的 TRIAGE 报告含重复检测栏目（跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强）与冲突检测栏目（新需求 vs 既有 FR 冲突对；GO 需显式确认已知晓冲突）；触发规则：每次 TRIAGE 均适用，无发现时栏目显式记「无」。
2. **同源规则**：统一入口建议面板的重复/冲突内容与 TRIAGE 报告同一规则（intake report schema 的固定栏目 §1f.2；两处 schema 同构）。
3. **边界**：只落报告栏目，不做运行时拦截或自动 NO-GO——无自动驳回行为可观察（运行时无拦截路径）。

### 1p. 评审人复核合同（IF-REVIEW-002）

**modules**：`.tracks/wiki/flow.md` §4.5-4/§5.3-4（钉定文本 ARCH-011 §1.0.12，Devon 任务落地）、`tracks/effects/opencode_review.py`（verdict 计算修订）、`tracks/agents/Sage.md`+`Lex.md`+`Prism.md`（纪律，待实现 Devon）。

1. **合同条款**：评审人复核的文档存在未解决的非本人发起线程时，该线程不构成 revise 依据——内容验证通过即 pass，线程确认留给其发起人；revise 仅限评审人自有新发现且必须开锚定线程（可追溯）。
2. **机器侧计算**：`lex.verdict` 的 revise 分支收窄为「存在评审人自有未决 findings 线程」（线程 initiator 与当前评审角色匹配）；非本人未决线程不参与 revise 判定；仅余 Human 裁决未决线程时的 `pass-pending-human-threads` park 分支（IF-010 §1s）语义不变；verdict 词汇封闭集不变。作者侧 RESPOND 义务与 discussion_ready 出口闸不变（安全网）。

## 2. CLI 接口合同

### 2a. serve 子命令

不变（IF-009 §2a 逐字继承；无新增 flag——intake/门认领为 supervisor 内部通道）。

### 2b. HTTP API 合同

IF-009 §2b 基底约定（认证边界、CSRF + Idempotency-Key、错误封装 `{"error": {"reason", "detail"}}`）与 #1-35 端点逐字继承（#17/#18 按 §0.1 #9/#10 修订）；v0.11 追加：

| # | method/path | 输入 | 成功 | 失败 | 备注 |
|:--|:--|:--|:--|:--|:--|
| 36 | `GET /api/projects/{pid}/releases` | — | 200 §1e.2 schema | 404 | 只读；聚合投影 |
| 37 | `GET /api/projects/{pid}/intakes?status=` | query | 200 `{intakes: [...]}` | 404 | 只读 |
| 38 | `GET /api/projects/{pid}/intakes/{intake_id}` | path | 200 §1f.3 schema | 404 | 只读；轮询评估期 |
| 39 | `POST /api/projects/{pid}/intakes` | `{source, content?, issue?, origin_entry?}` | 202 `{command_id, intake_id}` | 422（source 闭合校验）/409 `issue_fetch_failed` | → submit_intake（§1b #14） |
| 40 | `POST /api/intakes/{intake_id}/decisions` | `{action, confirm?}` | 202 `{command_id}`；develop 活跃 run 形态 200 `{outcome: backlog_recorded}` | 409 `unmerged_release_branches` {branches}/422 `dirty_tree`/422 `intake_not_ready` | → decide_intake（§1b #15/§1g） |
| 41 | `POST /api/projects/{pid}/backlog/{entry_id}/dispositions` | `{disposition, to?}` | 202 `{command_id}` | 404/422 | → dispose_backlog（§1b #16） |
| 42 | `POST /api/runs/{run_id}/triage` | `{decision}` | 202 `{command_id}` | 422 `awaiting_mismatch` | → record_triage（§1b #17） |
| 43 | `POST /api/runs/{run_id}/review` | `{action}` | 202 `{command_id}` | 422 `awaiting_mismatch` | → record_review（§1b #18） |
| 44 | `GET /api/projects/{pid}/hotfix-preview?issue=&scenario=` | query | 200 §1i.1 schema | 404/422 | 只读预检 |

run detail（`GET /api/runs/{run_id}`）追加字段：`awaiting_gate`（§1k.1）与 `return_options`（§1m.1）——追加式，既有字段不变。页面（HTML）：`PAGES` 6 名与 URL 表按 §1e.1；旧 URL 302 至 `/`；Vditor 资产同源不变。

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `<service_home>/service.db` | 继承 IF-009 §3 #1；`intakes` 表幂等演进（§1c #9） | 持久，跨重启 |
| 2 | `<service_home>/intakes/<intake_id>/assignment.md` | intake 评估 worker 物化（Scribe 派发 brief，含输入正文/来源/报告 schema 要求） | 评估会话期；评估完成可清理（report 已落 intakes 行） |
| 3 | 各项目 `.tracks/runtime/tracks.db` | `backlog.recorded`（payload 扩展）与 `backlog.dispositioned`（新）经命令服务/门执行写 | Releases 投影/CLI status；append-only |
| 4 | `.tracks/wiki/flow.md` | §3/§4/§5 v0.11 修订（钉定文本 ARCH-011 §1.0.12；Devon 任务经 `[layout.devon]` 单文件授权落地，§0.1 #14） | 全角色流程权威；test_flow_contracts 锚点断言钉定文本可定位 |
| 5 | `tracks/agents/Scribe.md`/`Sage.md`/`Lex.md`/`Prism.md` | 派发合同纪律修订（**待实现 Devon**） | agent 派发物化面 |
| 6 | `pyproject.toml` / `tracks/__init__.py` | version 0.11.0（设计期 bump，已交付） | version gate 绑定比对对象 |
| 7 | `tracks/server/static/app/*.js` 与 `styles.css` | releases/intake 新模块与 timeline 重绘（**待实现 Devon**；package-data 通配已覆盖） | 浏览器同源加载；ES modules 无构建链 |
| 8 | `.tracks/projects/project.toml` | `[layout.devon].writable` 追加 `.tracks/wiki/flow.md`（ARCH-011 §2 已交付）；其余段逐字继承 | 既有消费者 + runtime auditor（Devon 写域授权） |

## 4. 可观察出口（测试断言基础）

### 4a. HTTP/CLI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | §2b #36-44 新端点的 JSON schema/状态码 | 逐端点；错误 reason 封闭集（issue_fetch_failed/unmerged_release_branches/dirty_tree/intake_not_ready/awaiting_mismatch）；releases 聚合三节字段；hotfix-preview 两状态形态 |
| 2 | run detail 追加字段 | `awaiting_gate`（type/reason/impact/primary_action 或 null）与 `return_options`（targets/already_executed）语义正确 |
| 3 | 旧 URL 重定 | /projects、/projects/{pid}/runs/new、/runs/{id}/review → 302 `/`；PAGES 6 名；tab 条恰五项（页面 HTML data-testid） |
| 4 | #17/#18 修订形态 | #17 null-base pre-approval（trio 不可得时合法、可得时 409）；#18 anchor_line/thread_token 二选一（422 形态）；讨论线程落盘可解析 |

### 4b. service.db / 事件出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `service_events` §1a 新事件（#26-28） | append-only、seq 单调、payload 封闭集（report schema §1f.2） |
| 2 | `intakes` 表 | 状态机转移合法（submitted→evaluating→evaluated→decided）；失败态与重派预算；崩溃恢复后无重复 intake |
| 3 | tracks.db 事件 | `human.triage`/`human.review`（payload decision/action/actor）经门端点恰落一条；`human.approval`/`release.decided` 经 worker 执行落盘；`backlog.recorded` 新来源 payload；`backlog.dispositioned` 去向字段 |

### 4c. git / 远端 / CI 出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | 分支事实 | develop 成功 → `releases/v{N+1}` 从 main 创建、run 进入 M-STORY；拒绝/取消形态无分支无 run；hotfix 锚定不成立无 fix 分支 |
| 2 | story capture | frontmatter title 非空（Scribe 建议）；raw 骨架 §1 逐字；complete 采纳与输入等价 |
| 3 | CI required checks | 七名继承（零 workflow 变更事实）；新测试资产纳入既有 job 的 paths 语义 |

### 4d. 浏览器可观察出口（UI e2e，IF-WEBUI-001 继承）

| # | outlet | assertions |
|:--|:--|:--|
| 1 | Releases 窗口 | sidebar 三组（版本分组注册表/backlog 折叠/归档查询）+ 摘要行；选中 run→详情；选中 backlog 条目→转正入口 |
| 2 | 时间线 | attempt 独立节点数=事件分组数；回边可辨认；五态 data-state 标签；固定详情栏（无浮层）；选中/活跃双标记；owning surface 跳转返回 |
| 3 | 动作面板 | awaiting 节点 reason/impact/动作控件可定位；非 awaiting 无门入口；门动作后活跃指针前移（SSE/轮询） |
| 4 | 建议面板 | 重复/冲突栏目呈现；四动作控件；remove_backlog 仅 backlog 来源可用；无 hotfix 推荐面 |
| 5 | 写安全 | 全部 UI 写请求携带 X-Trac-CSRF 与 Idempotency-Key；失败反馈可见不伪报成功 |

## 5. IF Registry

### IF-CMDSVC-001 持久命令受理与幂等去重合同（继承 IF-009 §1b；v0.11 新 kind 全量适用 §1b.2 幂等键合同）

### IF-WEBGATE-001 Web 人工门绑定与拒绝合同（继承 IF-009/IF-010；v0.11 门端点 §1j 沿用其绑定纪律）

### IF-DRIVE-001 结构化驱动步与自动推进合同（继承 IF-009；v0.11 自驱动闭环 §1j.3 复用）

### IF-QUERY-001 只读投影查询合同（继承 IF-009/IF-010；v0.11 追加 §1e/§1f/§1i 读模型）

### IF-STREAM-001 事件订阅与游标补读合同（继承 IF-009；自驱动呈现经既有 SSE/轮询）

### IF-DOCSAVE-001 显式保存与 409 两选项合同（继承 IF-010 §1o；评审轮写入 §1n 复用其面）

### IF-HOTFIX-011 hotfix 前检分类映射合同（继承 IF-010 §1r.3；intake issue 取文 §1b #14 复用分类承接）

### IF-WEBUI-001 UI e2e 通道合同（继承 IF-010 §1t：ui marker 通道隔离、data-testid 绑定、独立基础设施预算）

### IF-GREENGUARD-001 到达即绿守卫节点合同（继承 IF-010 §1u：两类节点纪律、§8.1 唯一权威、guard_verified/fail-closed）

### IF-VERSION-001 制品声明版本绑定合同（继承 IF-010 §1r.2；0.11.0 设计期 bump 见 ARCH-011 §4.3）

### 其余继承合同组（本版零语义变更，聚合登记）

IF-MTEST-001 / IF-MTEST-002 / IF-SHIELD-001 / IF-TRACE-001..003 / IF-REACH-001..002 / IF-VALIDATE-001 / IF-IMPL-001..007 / IF-DEVON-001 / IF-LIVE-001 / IF-RELEASE-001..003 / IF-DOCGAP-001 / IF-QUARANTINE-001 / IF-HOTFIX-001..010 / IF-SELECT-001..002 / IF-EVIDENCE-001 / IF-LEDGER-001 / IF-FULLCHAIN-001 / IF-RUNCONTRACT-001 / IF-NIGHTLY-001 / IF-PHASE-001..003 / IF-GUARD-001..002 / IF-AUTH-001..002 / IF-MUTATION-001..002 / IF-CLOSURE-001 / IF-DEMO-001 / IF-FAILCLOSED-001 / IF-ADAPTER-003 / IF-VERIFY-001..005 / IF-REPAIR-001..002 / IF-KNOWNISSUE-001 / IF-ESCAPE-001..002 / IF-SECURITY-001 / IF-PUBLISH-001..002 / IF-MILESTONE-001..002 / IF-JOURNEY-001 / IF-ENVELOPE-001..002 / IF-FAILURE-001 / IF-HOSTCONTRACT-001..002 / IF-REFERENCE-001 / IF-ISSUE-001..002 / IF-PIPELINE-001 / IF-SERVE-001 / IF-PROJ-001 / IF-CMDGUARD-001 / IF-LEASE-001 / IF-SCHED-001 / IF-WAIT-001 / IF-RECOVER-001 / IF-PAUSE-001 / IF-WEBAUTH-001 / IF-SECRECY-001 / IF-DOCREV-001 / IF-TIMELINE-001（均继承 IF-006/007/008/009/010 既有登记，语义逐字不变）

### IF-WORKBENCH-001 SPA 工作台 shell 合同（继承 IF-010 §1l）

- **v0.11 修订**：功能项封闭集七项 → 五项（Releases/Docs/Todos/Settings/Account，FR-0334 破坏性变更）；SM-04 tab 语义、降级、真数据、写安全、两壳结构逐字继承。权威新 closed set 见 §1e.1（IF-RELEASES-001 承载）。
- **modules**：tracks/server/pages.py, tracks/server/app.py, tracks/server/static/app, tracks/server/static/styles.css, tracks/server/projections.py, tracks/server/api_events.py。
- **关联**：FR-0333、FR-0334、NFR-0158。

### IF-RELEASES-001 Releases 窗口与聚合投影合同

- **合同**：§1e——PAGES/URL/tab 五项封闭集与旧 URL 重定；`GET /api/projects/{pid}/releases` 三节聚合（版本分组注册表/backlog/归档）+ 摘要行语义；sidebar 三组结构。
- **modules**：tracks/server/pages.py, tracks/server/projections.py, tracks/server/api_query.py, tracks/server/static/app/releases.js（待实现 Devon）。
- **关联**：FR-0333、FR-0334。

### IF-INTAKE-001 统一入口与建议面板合同

- **合同**：§1f——intake 生命周期与 SM-01 状态机；三来源提交；Scribe 评估 report schema（可行性/重复/冲突/标题/输入形态）；四动作；输入分类与采纳（raw 逐字/complete 原样）；不推荐不路由 hotfix；轮询评估期。
- **modules**：tracks/supervisor/service.py, tracks/supervisor/intake.py（待实现 Devon）, tracks/supervisor/db.py, tracks/server/api_query.py, tracks/server/api_command.py, tracks/templating.py, tracks/server/static/app/intake.js（待实现 Devon）。
- **关联**：FR-0338、FR-0340（转正入口半边）、FR-0346（面板栏目半边）。

### IF-MSTART-001 M-START 门 UI 化合同

- **合同**：§1g——develop 动作执行序（active→backlog 记录/dirty 拒绝/unmerged 确认/v{N+1} 推导/create_run 委托）；story capture 的 title/story_form 扩展；不建半截；CLI 分叉记录。
- **modules**：tracks/supervisor/service.py, tracks/supervisor/worker_main.py, tracks/templating.py。
- **关联**：FR-0339。

### IF-BACKLOG-001 backlog/归档存储与去向合同

- **合同**：§1d/§1h——backlog.recorded payload 扩展与 backlog.dispositioned 事件；事件序派生投影（无第二存储）；归档服务面分储；处置与去向可查。
- **modules**：tracks/kernel/events.py, tracks/kernel/machine_lifecycle.py, tracks/supervisor/service.py, tracks/server/projections.py。
- **关联**：FR-0340、FR-0333（backlog 组半边）。

### IF-HOTFIXUI-001 hotfix 入口呈现合同

- **合同**：§1i——hotfix-preview 只读投影（预检+锚定可得性）；创建走既有 create_run 前检；锚定不成立退出指引；in-run 锚定门不 UI 化边界。
- **modules**：tracks/server/api_query.py, tracks/supervisor/service.py（既有前检复用）, tracks/executor/hotfix_face.py+hotfix.py（复用）, tracks/server/static/app/releases.js（待实现 Devon）。
- **关联**：FR-0341。

### IF-GATEUI-001 人工门命令端点与执行合同

- **合同**：§1j——record_triage/record_review 端点与受理前置；approve/release-decision 接入；worker 门执行面（共享人门管道、writer_lock、与 CLI 等价）；自驱动闭环（认领→执行→下一 tick drive→下一道门呈现）。
- **modules**：tracks/server/api_command.py, tracks/supervisor/serve_cmd.py, tracks/supervisor/gate_exec.py（待实现 Devon）, tracks/supervisor/worker_main.py, tracks/executor/result_checkpoint.py, tracks/cli/run_cmd.py。
- **关联**：FR-0342、FR-0345、NFR-0157。

### IF-AWAIT-001 awaiting 动作面板合同

- **合同**：§1k——awaiting_gate 字段组；type→primary_action 封闭映射；非 awaiting 无门入口；同一 surface；Review 承接。
- **modules**：tracks/server/projections.py, tracks/server/static/app/timeline.js（待实现 Devon）。
- **关联**：FR-0343、FR-0334（承接半边）。

### IF-TIMELINE-002 时间线完整绘制与固定详情栏合同

- **合同**：§1l——五态/attempt/回边绘制；固定详情栏字段与降级；选中/活跃区分；owning surface 与返回；数据一致（零新数据源）。替换 IF-TIMELINE-001 的浮层占位（IF-TIMELINE-001 的 stage_order 投影语义继续有效）。
- **modules**：tracks/server/static/app/timeline.js（待实现 Devon）, tracks/server/api_query.py, tracks/server/projections.py, tracks/kernel/stage_registry.py（只读事实表）。
- **关联**：FR-0335、FR-0336、NFR-0158。

### IF-RETURN-001 回拨安全合同

- **合同**：§1m——return_options 投影（合法目标共享计算）；指针仅 targets 非空呈现；确认面（目标+后果+already_executed）；服务端复验；不伪装撤销。
- **modules**：tracks/server/projections.py, tracks/server/static/app/timeline.js（待实现 Devon）, tracks/server/api_command.py（#25 复用）。
- **关联**：FR-0337。

### IF-DOCSWRITE-001 评审轮写入通道合同

- **合同**：§1n——docs_revision 全域化（missing 标签，纯扩展）；#17 null-base pre-approval；submit_clarification 的 anchor_line/thread_token 二选一（新根线程发起）；失败内容不丢；/discussions 无写端点边界保持。
- **modules**：tracks/supervisor/service.py, tracks/discuss（writer 复用）, tracks/server/static/app/docs.js+discussions.js+editor.js（待实现 Devon）。
- **关联**：FR-0344。

### IF-SPLIT-001 TRIAGE 报告固定栏目合同

- **合同**：§1o——重复/冲突两栏目格式与触发规则（flow.md §4.6 同文）；统一入口面板同源规则；只落栏目无运行时拦截。
- **modules**：.tracks/wiki/flow.md（Devon 任务按 ARCH-011 §1.0.12 钉定文本落地）, tracks/agents/Scribe.md（待实现 Devon）, tracks/supervisor/intake.py（report schema）。
- **关联**：FR-0346。

### IF-REVIEW-002 评审人复核合同

- **合同**：§1p——非本人未决线程不构成 revise 依据（内容验证通过即 pass）；revise 仅限自有新发现且必开锚定线程；lex.verdict revise 分支收窄（词汇封闭集与 Human park 分支不变）；agent 纪律三文件。
- **modules**：.tracks/wiki/flow.md（Devon 任务按 ARCH-011 §1.0.12 钉定文本落地）, tracks/effects/opencode_review.py, tracks/agents/Sage.md+Lex.md+Prism.md（待实现 Devon）。
- **关联**：FR-0347。
