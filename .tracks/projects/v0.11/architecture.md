---
envelope: tracks-envelope:v2
architecture_id: ARCH-011
spec_ref: SPEC-011
created: 2026-10-11
status: draft
sha:
---

# v0.11 — 架构：自驱动工作台（Releases 整顿 + run 时间线 + release/hotfix 创建 + 人工门 UI 化）

本文是 ARCH-010 的增量延伸。v0.1～v0.10 的事件溯源、单写者、canonical 阶段状态机、发布闭环、质量守卫 registry、测试流水线、CLI、v0.9 服务面（Starlette 交付面 + 持久命令服务 + supervisor 单活动 run 串行 + hotfix 换队）、v0.10 SPA 工作台 chrome（两壳/原生 ES modules/登录名字绑定/文档中心/run 时间线弱化版）全部保持不变；v0.11 在此之上做四件事：(1) 工作台窗口整顿——Projects/Runs 合并为 Releases 窗口、Review 移除、五 tab 收敛；(2) run 时间线按 louke v0.14-004 完整绘制并以 main area 下方固定详情栏承载节点详情与 awaiting 动作面板；(3) 创建链 UI 化——统一入口（intake 面 + Scribe 评估建议面板 + SM-01 四动作）、M-START 门、backlog/归档存储投影、hotfix 入口；(4) 人工门 UI 化与自驱动——triage/review 补 HTTP 命令端点、serve worker 补人工门命令执行面、supervisor 自动推进形成「UI 发起 → 自动推进 → 下一道门呈现」闭环。另随版钉定 flow.md §3/§4/§5 合同修订文本（Scribe 评估先于 CREATE_BRANCH、TRIAGE 报告固定栏目、评审人复核合同——§1.0.12，Devon 任务经 layout 授权落地）。

核心取舍：**不为 intake 新建 run**（spec SM-01.7 禁止——选①②③不开 run 不建分支），intake 生命周期落在服务面（service.db 新表 + service_events 审计），其 backlog 效果才落 run 面 `backlog.recorded` 事件；**人工门命令执行补在 serve worker**（v0.9 设计已声明「worker 认领执行」，现网 worker_main 只执行 drive_run——本版把它落成真实现，这是 FR-0345 自驱动闭环的缺件）；时间线零新数据源（只消费 stage_order + timeline/ac-chain/run detail 既有投影，外加一个只读 return-options 投影）。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-010（及此前各版）

- 唯一生产路径 `cli -> kernel.machine.decide -> executor.execute -> store.append`、SQLite append-only events、投影可重建、单写者 writer_lock 与 per-kind reconcile 不变。tracks.db 的 EVENT_TYPES 封闭集本版追加**一个**成员 `backlog.dispositioned`（§1.0.7）；其余事件类型与 payload 语义不变，`backlog.recorded` 仅 payload 追加可选键（§1.0.7）。
- `kernel/` 纯控制、`executor/` 副作用、`checks/` 静态闭合、`effects/` 外部通道、`adapters/` 宿主测试框架语义、`cli/` 交付面、`server/` Web 交付面、`supervisor/` 后台驱动控制层的分层不变；kernel 纯性纪律不变（评审出口规则修订仍在 effects 层计算，kernel 只归约既有 verdict 词汇，§1.0.13）。
- 既有 CLI 子命令集合与 USAGE 不变；本版不新增顶层子命令，不修改 `trac start`/`trac triage`/`trac review` 的既有语义（CLI 保留为管理后门；v0.5 AC-FR0267-02 的脏树不拒绝裁定在 CLI 面继续有效，UI M-START 门的脏树拒绝是 flow.md §3.1 语义在新入口的实现，见 §5.2 风险）。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]` 测试执行合同、`[nightly]/[adapter]/[lint]` 段、`[host-contract.*]` 段逐字不变（本版唯一合同面变更为 `[layout.devon].writable` 追加单文件授权 `.tracks/wiki/flow.md`，§1.0.12——文件 bytes 变化使 hooks-runner 的 project.toml digest 重锚，§4.2）；`ui` marker 通道隔离（addopts 排除、ui-e2e job 选择）不变。
- 服务命令封闭集（IF-009 §1b 十三种 kind）本版追加五个成员（§1.0.5/§1.0.9）；既有十三种 kind 的受理/校验/幂等/actor_class 语义逐字不变。
- 发布闭环五阶段、candidate 主身份、preview digest 绑定、escape barrier、publish 幂等/reconcile、Issue 闭环、reference host、三旅程合同、version gate bindings 全部继承；版本号设计期 bump 到 0.11.0（§2 已交付），pyproject bytes 变化引起六条 pyproject-backed config_digest 重锚（§4.2，先例 e5637a1）。
- 质量守卫八类与 canonical registry 机制不变；CI required checks 七名（lint/coverage/test/deliverables/trace/reach/ui-e2e）不变；`release-evidence` milestone 硬门禁不变。
- SSE 合并序/游标补读/轮询回退、CSRF + Idempotency-Key、redaction 全出口脱敏、本机单用户边界（RP-01）、supervisor 单活动 run 串行 + hotfix 换队语义全部不变；v0.10 工作台裁定（外壳钉视口、Vercel 极简设计令牌、registry 行紧凑化、pane bar 单控制面、自动保存模式）继续有效。
- Vditor vendored 资产与 manifest 逐字节不变；`docs_revision`/trio 摘要双身份与 `tracks/baseline.py` 零触碰原则不变（本版仅将 docs_revision 的定义域补全为全域函数，§1.0.11）。

### 0.2 v0.11 变更

- 页面壳收敛：`PAGES` 封闭集 8 名 → 6 名（login/releases/run_detail/todos/release/docs 视图并入 Releases 壳无独立 URL——`/docs` 深链由 tab 语义承载）；URL 表移除 `/projects`、`/projects/{pid}/runs/new`、`/runs/{run_id}/review`（旧 URL 302 至 `/`）；tab 条功能项 7 → 5（FR-0334 破坏性变更，GO 已确认）。
- 服务面新增 intake 面：service.db 新 `intakes` 表、service_events 追加 `intake.submitted`/`intake.evaluated`/`intake.decided` 三成员、命令封闭集追加 `submit_intake`/`decide_intake`/`dispose_backlog`；新 supervisor intake 评估 worker（`tracks/supervisor/intake.py`，待实现 Devon）。
- 人工门命令端点与服务化执行：命令封闭集追加 `record_triage`/`record_review`；新 HTTP 端点 `POST /api/runs/{run_id}/triage|review`；serve worker 补人工门命令执行面（`tracks/supervisor/gate_exec.py` + worker_main/serve_cmd 认领，待实现 Devon）——record_stage_approval/record_release_decision 自 v0.9 起只有受理无执行，本版一并落实现。
- run 详情/时间线投影追加 `awaiting_gate` 与 `return_options` 两个只读字段组；`GET /api/projects/{pid}/releases`、`GET /api/projects/{pid}/intakes[/{id}]`、`GET /api/projects/{pid}/hotfix-preview` 三个只读投影新端点。
- 评审轮写入通道：`docs_revision` 定义域补全（缺失文档计 `label:missing`，全域纯函数）；运行域编辑面 #17 接受 pre-approval 模式（trio 摘要不可得时 `base_revision: null` 合法）；`submit_clarification` params 扩展（`thread_token` 与 `anchor_line` 二选一，后者发起新根线程）。
- 评审出口规则修订（FR-0347 机器侧）：`lex.verdict` 的 revise 分支收窄为「存在评审人**自有**未决 findings 线程」；非本人发起未决线程不再构成 revise 依据（Human 裁决线程的 park 分支保持，§1.0.13）。
- 浏览器侧应用层新增 releases/intake 视图模块、timeline 重绘（五态/回边/固定详情栏/动作面板）、docs 评审轮写控（待实现 Devon）。
- 合同/配置面（随本设计物理交付，§2）：`pyproject.toml`（0.11.0）、`tracks/__init__.py`（0.11.0）、`.tracks/projects/project.toml`（[layout.devon] 追加 `.tracks/wiki/flow.md` 单文件写授权）。flow.md §3/§4/§5 的合同修订文本**不在 scaffold 域内**（scaffold 保留域拒收 `.tracks/**`，两次 checkpoint 拒绝实证）——修订全文钉在本设计 §1.0.12，由 Devon 任务经 layout 授权落地（§1.0.12）。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | v0.11 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `tracks/server/pages.py` | PAGES 6 名封闭集与 URL 重定（移除 review/projects/run_new，`/` 改 releases）；tab 条五项（releases/docs/todos/settings/account）；旧 URL 302 至 `/`；data-testid 锚点更新 | FR-0333/FR-0334 | IF-RELEASES-001, IF-WORKBENCH-001 |
| 2 | `tracks/server/static/app/releases.js`（新）+ `views.js`/`sidebar.js`/`tabs.js`/`shell.js` | Releases 窗口视图：版本分组注册表、backlog 折叠组、归档查询组、统一新建入口、hotfix 入口（**待实现 Devon**） | FR-0333/FR-0338/FR-0341 | IF-RELEASES-001, IF-INTAKE-001, IF-HOTFIXUI-001 |
| 3 | `tracks/server/static/app/timeline.js`（重绘）+ `intake.js`（新） | 13 阶段完整时间线（attempt 独立节点/回边/五态）、固定详情栏、awaiting 动作面板、Scribe 建议面板（**待实现 Devon**） | FR-0335/FR-0336/FR-0343 | IF-TIMELINE-002, IF-AWAIT-001, IF-INTAKE-001 |
| 4 | `tracks/server/static/app/docs.js`/`discussions.js`/`editor.js` | 评审轮写控：直接修改 + inline-comments 发起/回复（**待实现 Devon**） | FR-0344 | IF-DOCSWRITE-001 |
| 5 | `tracks/server/static/styles.css` | Releases 窗口/固定详情栏/动作面板/建议面板样式（**待实现 Devon**） | FR-0333/FR-0336 | IF-RELEASES-001, IF-TIMELINE-002 |
| 6 | `tracks/server/app.py` | 路由表：页面重定 + 新只读端点 3 + 新命令端点 4（§2b #36-42） | FR-0333..FR-0345 | IF-RELEASES-001, IF-GATEUI-001 |
| 7 | `tracks/server/api_query.py` + `projections.py` | `project_releases`（注册表+backlog+归档聚合）、intake 读模型、run detail `awaiting_gate`、`return_options`、hotfix-preview 投影 | FR-0333/FR-0335/FR-0337/FR-0341/FR-0343 | IF-RELEASES-001, IF-TIMELINE-002, IF-RETURN-001, IF-AWAIT-001 |
| 8 | `tracks/server/api_command.py` | 新命令端点：intakes 提交/决策、backlog disposition、triage、review（全部经 `_mutate` 共用 CSRF/幂等/受理管道） | FR-0338/FR-0340/FR-0342 | IF-INTAKE-001, IF-GATEUI-001 |
| 9 | `tracks/supervisor/db.py` | `intakes` 表（§1c）；SERVICE_EVENT_TYPES 追加三成员 | FR-0338 | IF-INTAKE-001 |
| 10 | `tracks/supervisor/service.py` | 命令封闭集 +5 kind 与受理前置；intake/backlog/clarification 的受理期同步执行（edit_material 先例）；`docs_revision` 全域化 | FR-0338..FR-0344 | IF-INTAKE-001, IF-BACKLOG-001, IF-DOCSWRITE-001 |
| 11 | `tracks/supervisor/intake.py`（新，**待实现 Devon**） | Scribe 评估派发：assignment 物化 → opencode 会话 → envelope 解析 → report 落 intakes 行；重试预算 ≤3；崩溃恢复（evaluating 超时回收） | FR-0338/FR-0346 | IF-INTAKE-001 |
| 12 | `tracks/supervisor/gate_exec.py`（新，**待实现 Devon**）+ `worker_main.py` + `serve_cmd.py` | 人工门命令执行面：worker 认领 record_triage/record_review/record_stage_approval/record_release_decision，经共享人门管道提交 human.* 事件；supervisor tick 认领 accepted 门命令并驱动 | FR-0342/FR-0345 | IF-GATEUI-001, IF-DRIVE-001 |
| 13 | `tracks/cli/run_cmd.py` + `tracks/executor/result_checkpoint.py` | 人门管道核心抽为共享函数（cmd_triage/cmd_review 与 gate_exec 共用；CLI 语义不变） | FR-0342 | IF-GATEUI-001 |
| 14 | `tracks/kernel/events.py` + `machine.py`（reducer 注册）+ `machine_lifecycle.py` | EVENT_TYPES 追加 `backlog.dispositioned`；reducer 幂等标记（run-less 行不参与活跃判定） | FR-0340 | IF-BACKLOG-001 |
| 15 | `tracks/effects/opencode_review.py` | 评审出口 revise 分支收窄：仅评审人自有未决 findings 线程构成 revise；非本人线程不构成 revise 依据（Human park 分支不变） | FR-0347 | IF-REVIEW-002 |
| 16 | `tracks/templating.py` | `classify_story_input`（raw seed vs complete story 纯函数）+ complete story 原样采纳渲染 + frontmatter title 预填 | FR-0338/FR-0339 | IF-INTAKE-001, IF-MSTART-001 |
| 17 | `tracks/agents/Scribe.md`（**待实现 Devon**） | TRIAGE 派发合同补重复/冲突两栏目纪律（与 §1.0.12 文本同源） | FR-0346 | IF-SPLIT-001 |
| 18 | `tracks/agents/Sage.md` + `Lex.md` + `Prism.md`（**待实现 Devon**） | 评审人复核合同纪律（非本人线程不构成 revise；自有发现必开锚定线程） | FR-0347 | IF-REVIEW-002 |
| 19 | `.tracks/wiki/flow.md`（**待实现 Devon**，经 [layout.devon] 单文件授权） | §3.1 v0.11 重排 + §4.6 TRIAGE 报告栏目 + §4.5-4/§5.3-4 评审人复核合同——按 §1.0.12 钉定的修订文本逐字落地 | FR-0339/FR-0346/FR-0347 | IF-MSTART-001, IF-SPLIT-001, IF-REVIEW-002 |
| 20 | `pyproject.toml` / `tracks/__init__.py` | 0.11.0（§2 已交付；version gate 绑定对象） | NFR-0158 | IF-VERSION-001 |
| 21 | `.tracks/projects/project.toml` | [layout.devon].writable 追加 `.tracks/wiki/flow.md`（§2 已交付；FR-0120 layout 由 Archer 设计） | FR-0339/FR-0346/FR-0347 | IF-VERSION-001 |

### 1.0.2 Releases 窗口与五 tab 收敛（FR-0333/FR-0334）

`PAGES` 封闭集收敛为 6 名：`login`、`releases`（`/`）、`run_detail`（`/runs/{run_id}`）、`todos`（`/todos`）、`release`（`/runs/{run_id}/release`）、`docs`（`/docs`，docs 视图获得显式深链入口；其余功能区经 tab 切换达）。移除的三个旧 URL（`/projects`、`/projects/{pid}/runs/new`、`/runs/{run_id}/review`）302 至 `/`——深链不悬空。tab 条功能项封闭集五项（FR-0318 七项收敛的破坏性变更，GO 已由操作者指令确认）：Releases/Docs/Todos/Settings/Account；Settings/Account 语义不变（齿轮只开 tab、Account 菜单仅 logout）。Releases 窗口结构按 E-01：sidebar 为版本分组的 run 注册表 + backlog 折叠组 + 归档查询组（与 backlog 存储区分、视觉区分）；注册项目/版本信息仅为窗口内摘要行；main area 为 run 详情（时间线+固定下栏，§1.0.3）、新建入口（§1.0.5）或面板。Review 的阶段评审职责由 awaiting 动作面板承接（§1.0.10），不出现悬空门。

### 1.0.3 时间线完整绘制与固定详情栏（FR-0335/FR-0336）

零新数据源：时间线只消费 `stage_order` + `GET /api/runs/{run_id}/timeline` + `GET /api/runs/{run_id}/ac-chain` + run detail（既有投影）。客户端绘制：13 阶段 canonical 线性轨道上每次 attempt 为独立节点不折叠、方向连接表达推进、回拨（stage 回退/escape 事件对）以可辨认回边表达来源→目标；节点五态（已完成/活跃/待处理/需处理/失效）由事件序派生且以「图标形状+状态标签」双通道区分（不只靠颜色，`data-state` 属性承载机器可断言标签）；初始视图聚焦活跃节点并保留邻近上下文，全部历史可导航到达（滚动/跳转）。鼠标选中任意节点（不要求键盘可达，操作者指令 2026-10-10）→ main area 下方**固定详情栏**（非浮层，不可协商项）加载节点详情：起止时间/状态/责任方/关联 artifact/revision/关键 evidence/错误/状态转移原因/回拨合法性，全部为真实投影数据；不可查看或无数据的节点呈现可读降级，不伪造。详情栏明确区分「选中查看」与「当前活跃」（两套视觉标记）；提供 owning surface 稳定入口（跳文档中心对应文档/操作面）与返回（回到同一 run 与可识别节点上下文，客户端路由态承载）。v0.10 IF-TIMELINE-001 的浮层占位被本设计替换。

### 1.0.4 回拨安全执行（FR-0337）

回拨指针只为 Runtime 明确允许的目标呈现：新只读投影 `return_options`（run detail 附带）经与 CLI `trac return` 同一合法性规则（目标校验 + `_already_executed_ops` 清单）计算 `targets: [{stage, already_executed}]`；`targets` 为空即无回拨入口（不允许的回拨无入口）。用户发起回拨 → 先呈现目标与后果（含 already_executed 清单——不伪装撤销外部副作用）→ 显式确认 → 走既有 `POST /api/runs/{run_id}/return`（#25，params 携 `confirm`）；服务端复验（既有 return_stage 执行语义）失败给出原因与合法继续位置。时间线上的历史回拨回边来自事件序（既有数据），与发起面分离。

### 1.0.5 统一入口与 intake 面（FR-0338）

intake 是服务面新概念：run 之前的建议评估会话。生命周期：`submit_intake`（三来源：`text`（本身即故事/seed）、`issue`（作新功能输入的 GitHub issue id，经 effects/github fetch 取正文）、`backlog`（条目引用 + origin_entry））→ status=submitted → supervisor intake worker 派发 Scribe（assignment 物化 + opencode 会话，与 run 派发同一 effects 缝，fake/live backend 通道继承）→ envelope 解析 report（可行性 + 重复检测 + 冲突检测 + 建议标题 + 输入形态判定，栏目与 flow.md §4.6 同规则）→ status=evaluated → 建议面板呈现四动作 → `decide_intake`：`backlog`（落 run 面 `backlog.recorded`，SM-01.2）/ `archive`（归档存储，SM-01.3）/ `remove_backlog`（仅来源为 backlog，移除原条目并记录去向，SM-01.5）/ `develop`（走 M-START 门，SM-01.4）。raw seed 逐字入骨架、complete story 原样采纳不包裹进 §1（`templating.classify_story_input` 机器分类为权威，Scribe 判定为建议）。统一入口与建议面板不出现 hotfix 推荐、不路由到 hotfix 流程（用户按意图显式分流，hotfix 意图走独立入口 §1.0.8）。intake 生命周期事件落 service_events（`intake.submitted/evaluated/decided`）；持久状态在 `intakes` 表；崩溃恢复：evaluating 超时回收重派（≤3 预算），失败可读呈现。

### 1.0.6 M-START 门 UI 化（FR-0339）

`decide_intake(action=develop)` 的同步执行按序过门（flow.md §3.1 v0.11 重排版）：(1) 活跃 run 存在（schedule.active_run）→ 提示并落 `backlog.recorded`（reason=active_run），不开新 run、不建分支——这是合法终态而非错误；(2) 工作区不干净（`git status --porcelain` 非空）→ 拒绝并示原因（reason=dirty_tree）；(3) 本地 releases/* 未全部并入 main → 单列未合并分支与后果，无 `confirm` 参数时返回待确认态（branches 清单），`confirm=true` 继续、`confirm=false` 取消退出；(4) 通过后从 main 创建 `releases/v{N+1}`（N+1 = 项目既有 release 版本最大 minor + 1，服务端推导）并委托既有 `create_run`（journey=feature）受理——调度、run 面 bootstrap、story capture 全部复用；story capture 以 Scribe 建议标题预填 frontmatter title、按输入形态渲染（raw 骨架/complete 采纳）。失败或拒绝均给出原因与合法继续位置，不建半截 run 或分支（受理前置全部先于持久化，SM-01.5）。选其余三动作不开 run 不建分支（SM-01.7）。CLI `trac start` 语义不变（管理后门）。

### 1.0.7 backlog/归档存储与去向（FR-0340）

backlog 的唯一真相是 run 面 `backlog.recorded` 事件（既有事件类型，payload 追加可选键 `source`/`content`/`intake_id`——统一入口与 active-run 门两处新来源都落它；`trac start` 既有 payload 形态不变）。新事件 `backlog.dispositioned` {entry, disposition: removed|promoted, to} 承载「移除原条目并记录去向」：从 backlog 删除（SM-01.5）、转正成功（SM-01.6，`to`=新版本号）。Releases 投影从事件序派生 backlog 条目（ULID 身份、按 dispositioned 折叠已处置项、去向可查）——不新增第二存储。归档与 backlog 在存储上区分：归档条目只存在于服务面（intakes 行 status=archived + `intake.decided(action=archive)` 审计），归档查询组读服务面；两处投影在 `GET /api/projects/{pid}/releases` 单端点聚合呈现（视觉区分由 UI 承载）。转正 = `submit_intake(source=backlog, origin_entry=...)` 走新建流程，develop 成功后 `dispose_backlog(disposition=promoted)`（或 decide_intake 联动落同一事件，二实现归 Devon，合同唯一）。

### 1.0.8 hotfix 入口（FR-0341）

新只读端点 `GET /api/projects/{pid}/hotfix-preview?issue=&scenario=`：服务端跑既有 `precheck_hotfix_report`（issue fetch 经 stand-in/live GitHub 通道）+ 既有 AC 锚定可得性结果（目标版本 baseline 三件存在性 + approved 状态），返回报告字段供 UI 呈现。创建走既有 `create_run`（journey=hotfix_post|hotfix_dev，§1b #3 前检在受理期执行——锚定不成立即 rejected，不建半截 fix 分支）；UI 在锚定不成立时明示退出 hotfix 并指引转 backlog/new feature（统一入口）。in-run 的 hotfix_triage 锚定门本版不 UI 化（§5.1 显式简化）。

### 1.0.9 人工门命令端点与 worker 执行（FR-0342/FR-0345，自驱动闭环核心）

新命令 kind `record_triage` {run_id, decision: go|no_go|park} 与 `record_review` {run_id, action: comment|no_comment}，新端点 `POST /api/runs/{run_id}/triage|review`，全部经 `_mutate` 管道（CSRF + Idempotency-Key + guard + 受理前置：awaiting 必须匹配——triage 面 awaiting=triage；review 面 awaiting ∈ {review, review_pending_threads}，后者为 FR-0332 §1s.3 的恢复语义）。**执行面**（v0.9 已声明、现网缺件，本版落地）：supervisor tick 追加门命令认领——accepted 的四类人门命令（record_triage/record_review/record_stage_approval/record_release_decision）由 worker 认领执行：`gate_exec` 经共享人门管道（CLI `_do_human_pipeline`/`cmd_approve`/`cmd_release` 核心抽共享，CLI 语义零变更）在 writer_lock 下提交 `human.triage`/`human.review`/`human.approval`/`release.decided` 域事件与 checkpoint 提交，标记命令 completed。执行后 run 已越过门 → supervisor 下一 tick 常规 drive（既有机制）推进到下一道边界——「UI 发起 → 自动推进 → 下一道门在时间线呈现（活跃指针前移，SSE/轮询承载）」闭环成立，门间无手工 dispatch。gate 与 gate 不重叠幂等（受理期幂等键 + awaiting 前置双保险：门已过再提交即 rejected(awaiting_mismatch)，不产生重复事件）。

### 1.0.10 awaiting 动作面板（FR-0343）

run detail 投影追加 `awaiting_gate` 字段组：{type（awaiting 封闭集成员）, reason, impact, primary_action}。type→primary_action 映射为封闭合同（interfaces §1v）：triage → triage 面板（go/no-go/park）；review / review_pending_threads → comment/no-comment（后者语义为恢复重评）；approval → approve/revise（既有 #19）；release-decision → release 面（既有 #21）；escalation → retry（既有 #26）；rollback → 回拨确认面（§1.0.4）；hotfix_triage → 详情 + owning surface 指引（本版不 UI 化锚定提交，§5.1）。下栏详情与动作面板为同一 surface（E-02/E-04）；非 awaiting 节点的下栏不呈现门动作入口，仅呈现节点详情。Review 移除后的阶段评审（Sage/Lex/Human review 门）全部经 review/triage 面板 + 评审轮写入通道（§1.0.11）承接，无悬空门。

### 1.0.11 评审轮文档评论与编辑写入（FR-0344）

两处补全：(1) `docs_revision` 定义域补全为全域纯函数——缺失文档计 `label:missing`（与 `m_impl_baseline_digest` 的 missing 约定同构），版本目录存在即有 token；六件套齐全目录的值与 v0.10 逐字节一致（纯扩展，既有断言不受影响），文档中心编辑面 #35 因此在任何评审轮可用。(2) 运行域编辑面 #17 的 pre-approval 模式：trio 摘要不可得（spec/acc 尚未存在——批准基线不可能存在）时接受 `base_revision: null`（无基线可绑定，FR-0308 结构保证不受影响）；摘要可得时 null 被拒（必须绑定）。inline-comments：`submit_clarification` params 扩展为 `thread_token`/`anchor_line` 二选一——anchor_line 模式经 tracks/discuss writer 以会话 actor 发起新根线程（open），thread_token 模式为既有回复语义；写经命令面事件（受理期同步执行，edit_material 先例），落盘可查。写失败（网络/5xx）编辑内容不丢可重试，不伪报成功（api.js 既有失败反馈面）。

### 1.0.12 flow.md v0.11 修订合同（FR-0339/FR-0346/FR-0347，钉定文本 + 落地通道）

flow.md 是 Runtime 管辖的协议资产：scaffold 保留域拒收 `.tracks/**`（两次 checkpoint 拒绝实证，verdict.failed reason=declared scaffold path rejected），Archer 不能以 scaffold 物化它。落地通道：**本节钉定修订全文（逐字权威），由 Devon 任务经 `[layout.devon].writable` 的 `.tracks/wiki/flow.md` 单文件授权（§2 已交付 project.toml）逐字落地**；`tracks/agents/Scribe.md` 补 TRIAGE 两栏目纪律、`Sage.md`/`Lex.md`/`Prism.md` 补评审人复核纪律（同为 Devon 任务，与本节同源）。统一入口建议面板的重复/冲突内容与 TRIAGE 报告同一规则（intake report schema 的固定栏目，§1.0.5）。

**Devon 落地任务钉定文本**（对 flow.md 的全部修改，逐字）：

1. **§3 M-START**：「进入条件」替换为：「Human 经统一入口提交输入（本身即故事/seed、作新功能输入的 GitHub issue id、backlog 条目三种来源），Scribe 评估呈现建议面板后选择「进入开发」（v0.11 重排：Scribe 评估先于 CREATE_BRANCH）。hotfix 意图走独立 hotfix 入口（§16），统一入口不推荐 hotfix。」§3.1 子状态机重排为：`[*] → INTAKE（三种来源: raw seed / issue id / backlog 条目；dispatch Scribe 评估：可行性/重复/冲突 + 建议标题）→ PANEL（建议面板呈现四动作）`；PANEL 四转移：`→ BACKLOG（选进入 backlog (SM-01.2)：backlog.recorded 聚合存储；不开 run, 不建分支）`、`→ ARCHIVED（选不进入开发归档 (SM-01.3)：归档存储 (与 backlog 区分, 可查)；不开 run, 不建分支）`、`→ REMOVED（选从 backlog 删除 (SM-01.5, 仅 backlog 来源)：移除来源条目并记录去向；不开 run, 不建分支）`、`→ CHECK_ACTIVE（选进入开发 (SM-01.4)）`；`CHECK_ACTIVE → BACKLOG（有活跃 run (提示并记入 backlog)）/ → CHECK_WORKSPACE（无活跃 run）`；`CHECK_WORKSPACE → FAILED（有未提交修改）/ → CHECK_BRANCHES（干净）`；`FAILED：报告原因, 拒绝 start`；`CHECK_BRANCHES → AWAIT_CONFIRM（有未合并分支：单列未合并分支清单与后果）/ → CREATE_BRANCH（全部已合并）`；`AWAIT_CONFIRM → CREATE_BRANCH（Human 确认继续）/ → [*]（Human 取消 (不建分支)）`；`CREATE_BRANCH：从 main 创建 releases/v{N+1}；Scribe 定标题写入 frontmatter title → CLASSIFY_INPUT`（raw seed → WRITE_SEED 逐字入骨架；complete story → ADOPT_STORY 原样采纳不包裹/重写）→ `stage.exited -> M-STORY`。节尾注记追加：「可休眠点：AWAIT_CONFIRM；INTAKE→PANEL 期间（Scribe 评估中）。」「统一入口状态机（SM-01）：(1)（提交输入）→ `待决`；(2) `待决` → `backlog`；(3) `待决` → `已归档`；(4) `待决` → `开发中`（通过 M-START 门，Scribe 定标题，创建分支与 run）；(5) `待决`/`backlog` → `已删除`（仅来源为 backlog 时可从 backlog 删除）；(6) `backlog` → `开发中`（以条目为输入走新建流程，成功后移除原条目并记录去向）；(7) 选进入 backlog/归档/删除时为终态——不开 run、不建分支。未列出的状态转移即不允许。」「入口面：工作台统一入口（Releases 窗口）为常规路径，全部写操作经命令面事件单写路径；CLI（`trac start`）保留为管理后门。」
2. **§4.1 TRIAG** 节点描述追加一行：「TRIAGE : 报告固定栏目: 重复检测 + 冲突检测 (§4.6)」。
3. **新增 §4.6 TRIAGE 报告固定栏目（v0.11，FR-0346）**：「Scribe 撰写任一新 story 的 TRIAGE 报告时，报告必须含两个固定栏目；同一规则适用于统一入口建议面板的重复/冲突内容（两处同源，见工作台统一入口合同）：1. **重复检测**：story 所述功能已存在时，以跨版本引用指认既有 FR/AC（如 `FR-0316@v0.10`），并给出建议——NO-GO（纯重复）或改写为增强（在既有 FR 上叠加增量）。2. **冲突检测**：列出新需求 vs 既有 FR 的冲突对（新需求条目 ↔ 既有 FR 引用），破坏性/抵触需求不阻塞 GO，但 GO 需显式确认已知晓冲突。3. 触发规则：两栏目对每次 TRIAGE 均适用（无发现时栏目显式记「无」）；检测只落报告栏目，不做运行时拦截或自动 NO-GO——Human 依据栏目自行裁定。」
4. **§4.5 硬规则追加第 4 条**：「4. **评审人复核合同（v0.11，FR-0347）**：评审人（Sage/Lex/Prism）复核的文档存在未解决的非本人发起线程时，该线程不构成 revise 依据——内容验证通过即 pass，线程确认留给其发起人；revise 仅限评审人自有新发现且必须开锚定线程（可追溯）。评审人不为他人未决线程触发无发现修订。」
5. **§5.3 硬规则追加第 4 条**：「4. **评审人复核合同（v0.11，FR-0347，与 §4.5-4 同文）**：评审人复核的文档存在未解决的非本人发起线程时，该线程不构成 revise 依据——内容验证通过即 pass，线程确认留给其发起人；revise 仅限评审人自有新发现且必须开锚定线程（可追溯）。」

以上为 flow.md 的全部 v0.11 修改；节编号、既有小节文本与 mermaid 其余部分不变。锚点测试（test_flow_contracts.py 三节点）断言本节钉定文本在落地后的 flow.md 中逐句可定位。

### 1.0.13 评审出口规则修订（FR-0347 机器侧）

`opencode_review` 的 verdict 计算修订：revise 分支收窄为「存在**评审人自有**未决 findings 线程」（按线程 initiator 与当前评审角色匹配判定）；非本人发起的未决线程不构成 revise 依据——内容验证通过即 `pass`，线程确认留给其发起人（作者侧 discussion_ready 出口闸与作者 RESPOND 义务不变）。仅余 Human 裁决未决线程时的 `pass-pending-human-threads` park 分支（v0.10 §1s）语义不变。Sage 面 verdict 为 agent 自报，纪律由 agent 合同（§1.0.12）承载，机器侧无第二通道。

### 1.1 Composition Root

服务面装配入口继承（cmd_serve → ServiceDB/recover → CommandService → Scheduler/WorkerManager → create_app → uvicorn），v0.11 追加 intake 评估与门命令认领两个后台通道：

```text
cmd_serve（tracks/cli/serve_cmd.py，既有）
  -> supervisor/db.py ServiceDB(home)（建表 + intakes 表幂等演进，interfaces §1c）
  -> recover_on_startup -> CommandService -> Scheduler/WorkerManager（既有）
  -> server/app.create_app(home, service, supervisor, config)
       （_AuthBoundary 不变；api_query 增 releases/intakes/hotfix-preview/awaiting_gate/return_options；
         api_command 增 intakes/decisions/dispositions/triage/review 五组端点；
         pages 六名两壳；/static 挂载不变）
  -> uvicorn 运行（既有）

serve supervisor tick（既有 _supervisor_tick，v0.11 追加两步）
  -> _drive_accepted / _accept_fresh_drive（既有 drive 通道不变）
  -> 门命令认领（新）：accepted 的 record_triage/record_review/record_stage_approval/
     record_release_decision -> worker.spawn -> worker_main._execute
        -> gate_exec（共享人门管道，writer_lock 内）
        -> human.triage / human.review / human.approval / release.decided 落 tracks.db
        -> 命令 completed；下一 tick 常规 drive 推进至下一道边界（自驱动闭环）
  -> intake 评估认领（新）：intakes.status=submitted -> intake.evaluate
        -> assignment 物化（<service_home>/intakes/<id>/assignment.md）
        -> effects/opencode 会话（role=scribe；fake/live backend 通道继承）
        -> envelope 解析 report -> intakes.status=evaluated + intake.evaluated 落 service_events

统一入口链路（浏览器）
  GET / -> releases 壳 -> /static/app/releases.js
    -> sidebar：GET /api/projects/{pid}/releases（注册表+backlog+归档聚合投影）
    -> 新建 release：POST /api/projects/{pid}/intakes（submit_intake）
       -> 轮询 GET /api/projects/{pid}/intakes/{id} 至 evaluated
       -> 建议面板（report 固定栏目）-> 四动作：
          POST /api/intakes/{id}/decisions（decide_intake：backlog/archive/remove_backlog/develop）
          develop -> M-START 门（§1.0.6）-> 既有 create_run -> supervisor drive -> M-STORY
    -> 新建 hotfix：GET hotfix-preview -> 确认 -> 既有 POST /api/projects/{pid}/runs（journey=hotfix_*）
  run 详情：GET /runs/{run_id} -> timeline.js
    -> GET timeline(+stage_order)/ac-chain/run detail（awaiting_gate/return_options 附带）
    -> 固定详情栏 + awaiting 动作面板
    -> 门动作：POST /api/runs/{id}/triage|review|approvals|release-decision|retry|return
       -> 命令受理 -> worker 执行 -> supervisor drive -> 下一道门经 SSE/轮询呈现
```

### 1.2 Required AC closure (ISLAND_GATE_1)

- **FR-0333** owner=tracks/server/projections.py+tracks/server/static/app/releases.js:AC-FR0333-01 surface=GET /api/projects/{pid}/releases+E-01 Releases 窗口 sidebar composition=project_releases 聚合投影（版本分组注册表+backlog+归档，§1.0.2/§1.0.7）；releases.js 视图装配（§1.1） wiring=打开 Releases 窗口→sidebar 呈现版本分组 run 注册表与 backlog 折叠组、注册信息为摘要行→选中 release(run) → main area 进 run 详情（FR-0335/0336）→选中 backlog 条目→呈现转正入口（submit_intake(source=backlog)）→可返回注册表或进 Docs/Todos test=integration:tests/integration/test_releases_projection.py::test_registry_backlog_groups + e2e:tests/e2e/test_releases_ui.py::test_releases_sidebar_and_selection evidence=`.venv/bin/python -m pytest -q tests/integration/test_releases_projection.py` 输出 passed 且投影含版本分组/backlog 组/摘要行字段 IF-RELEASES-001 IF-BACKLOG-001
- **FR-0333** owner=tracks/server/projections.py+tracks/server/static/app/releases.js:AC-FR0333-02 surface=GET /api/projects/{pid}/releases+E-01 归档查询组 composition=归档读模型（intakes 行 status=archived，§1.0.7）与 backlog 投影同端点异节聚合 wiring=归档条目呈现于独立归档查询组（与 backlog 折叠组视觉区分、存储区分——run 面事件 vs 服务面 intakes）→归档条目可查但无转正/开发入口→sidebar 只显 Releases 下级内容 test=integration:tests/integration/test_releases_projection.py::test_archive_group_distinct_queryable evidence=`.venv/bin/python -m pytest -q tests/integration/test_releases_projection.py` 输出 passed 且 archive 节与 backlog 节字段来源可区分 IF-RELEASES-001 IF-BACKLOG-001
- **FR-0334** owner=tracks/server/pages.py+tracks/server/static/app/tabs.js:AC-FR0334-01 surface=E-01 tab 条+页面 URL 集 composition=PAGES 6 名封闭集与 URL 重定（§1.0.2）；tab 条五项装配 wiring=查看 tab 条→仅 Releases/Docs/Todos/Settings/Account 五项且无 Review 入口→经 tab 切换功能区 sidebar 与 main area 联动（SM-04 继承）→旧 URL（/projects、/runs/{id}/review、/projects/{pid}/runs/new）302 至 /→无 Review 页面或空 tab test=integration:tests/integration/test_workbench_tabs.py::test_five_tab_closed_set + e2e:tests/e2e/test_releases_ui.py::test_five_tabs_no_review evidence=`.venv/bin/python -m pytest -q tests/integration/test_workbench_tabs.py` 输出 passed 且 PAGES 集不含 review/projects/run_new、tab 集恰五项 IF-RELEASES-001 IF-WORKBENCH-001
- **FR-0334** owner=tracks/server/projections.py+tracks/server/static/app/timeline.js:AC-FR0334-02 surface=run detail awaiting_gate+E-02 下栏动作面板 composition=awaiting_gate 投影与 type→primary_action 封闭映射（§1.0.10） wiring=阶段评审（triage/review/approval/release-decision）到达→对应 awaiting 节点下栏呈现该门动作面板→评审在面板完成（门端点+评审轮写入通道）→全程无无处评审的悬空门 test=integration:tests/integration/test_gate_endpoints.py::test_awaiting_gate_mapping + e2e:tests/e2e/test_awaiting_ui.py::test_stage_review_completed_in_panel evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且每个阶段评审 gate 类型映射到非空 primary_action IF-AWAIT-001 IF-GATEUI-001
- **FR-0335** owner=tracks/server/static/app/timeline.js+tracks/server/api_query.py:AC-FR0335-01 surface=E-02 时间线+GET timeline/ac-chain composition=五态/attempt 节点/回边的客户端组合规则（§1.0.3，stage_order 服务端单源） wiring=查看 run 时间线→13 阶段线性轨道、attempt 独立节点不折叠、方向连接、回拨回边可辨认来源→目标→五态以图标+标签双通道区分（data-state）→初始视图聚焦活跃节点保留邻近上下文→全部历史可导航 test=e2e:tests/e2e/test_releases_ui.py::test_timeline_full_rendering evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_releases_ui.py` 输出 passed 且节点数与 attempt 事件数一致、五态标签可定位 IF-TIMELINE-002 IF-WEBUI-001
- **FR-0335** owner=tracks/server/api_query.py+tracks/server/static/app/timeline.js:AC-FR0335-02 surface=GET /api/runs/{run_id}/timeline+ac-chain composition=只读投影消费（零新数据源，§1.0.3） wiring=时间线渲染内容与 timeline/ac-chain 响应逐字段一致（不合成第二来源）→无 mock 数据→数据缺失节点显式可读降级（不伪造节点） test=integration:tests/integration/test_timeline_full.py::test_timeline_data_parity_no_mock evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_full.py` 输出 passed 且 parity 断言逐字段成立、降级样本渲染标记存在 IF-TIMELINE-002 IF-QUERY-001
- **FR-0336** owner=tracks/server/static/app/timeline.js:AC-FR0336-01 surface=E-02 固定详情栏 composition=节点详情组合规则（§1.0.3：事件序派生起止/状态/责任方/artifact/revision/evidence/错误/转移原因/回拨合法性） wiring=鼠标选中任意节点→main area 下方固定详情栏加载节点详情（真实投影字段齐备）→「选中查看」与「当前活跃」双标记区分→owning surface 稳定入口跳转→返回回到同一 run 与可识别节点上下文 test=e2e:tests/e2e/test_releases_ui.py::test_node_detail_fixed_pane evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_releases_ui.py` 输出 passed 且详情字段齐备、跳转返回后上下文还原 IF-TIMELINE-002 IF-WEBUI-001
- **FR-0336** owner=tracks/server/static/app/timeline.js:AC-FR0336-02 surface=E-02 固定详情栏 composition=同上+可读降级规则 wiring=不可查看/无数据节点→详情栏呈现可读降级（不伪造数据）→详情不以浮层呈现（无 overlay 元素）→详情栏位置固定于 main area 下方（data-testid=detail-pane 常驻） test=integration:tests/integration/test_timeline_full.py::test_detail_degradation_no_floating + e2e:tests/e2e/test_releases_ui.py::test_fixed_pane_position_no_overlay evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_full.py` 输出 passed 且降级样本有标记、无浮层断言成立 IF-TIMELINE-002
- **FR-0337** owner=tracks/server/projections.py+tracks/server/static/app/timeline.js:AC-FR0337-01 surface=run detail return_options+E-02 回拨指针 composition=return_options 只读投影（与 CLI trac return 同一合法性规则共享计算，§1.0.4） wiring=读取 return_options→仅 Runtime 允许的目标出现在 targets→回拨指针只为 targets 非空时呈现→不允许的回拨无入口（UI 无控件） test=integration:tests/integration/test_timeline_full.py::test_return_options_gate_pointer evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_full.py` 输出 passed 且不可回拨形态 targets 为空、合法形态含 already_executed 清单 IF-RETURN-001 IF-TIMELINE-002
- **FR-0337** owner=tracks/server/api_command.py+tracks/server/static/app/timeline.js:AC-FR0337-02 surface=POST /api/runs/{run_id}/return+E-02 确认面 composition=既有 return_stage 端点（#25）接进确认面；服务端复验语义继承 wiring=发起回拨→先呈现目标与后果（already_executed 清单）→显式确认（confirm）→Runtime 复验通过才执行→执行后不伪装撤销外部副作用→拒绝/复验失败给出原因与合法继续位置 test=integration:tests/integration/test_timeline_full.py::test_return_confirm_revalidation + e2e:tests/e2e/test_return_ui.py::test_return_confirm_face evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_full.py` 输出 passed 且无确认被拒、复验失败样本含原因 IF-RETURN-001
- **FR-0338** owner=tracks/supervisor/service.py+tracks/supervisor/intake.py+tracks/server/static/app/intake.js:AC-FR0338-01 surface=POST /api/projects/{pid}/intakes+GET intakes/{id}+POST /api/intakes/{id}/decisions+E-03 建议面板 composition=intake 面（§1.0.5：submit→Scribe 评估→report 固定栏目→四动作） wiring=经统一入口提交一段输入（text/issue/backlog 三来源均可）→Scribe 可行性/重复/冲突建议呈现面板→四动作可选（进入 backlog/从 backlog 删除/不进入开发归档/进入开发）→从 backlog 删除仅来源为 backlog 时可用（SM-01.5） test=integration:tests/integration/test_intake.py::test_intake_sources_and_actions + e2e:tests/e2e/test_intake_ui.py::test_scribe_panel_four_actions evidence=`.venv/bin/python -m pytest -q tests/integration/test_intake.py` 输出 passed 且三来源各形成 evaluated intake、四动作受理语义正确 IF-INTAKE-001
- **FR-0338** owner=tracks/templating.py+tracks/supervisor/service.py:AC-FR0338-02 surface=decide_intake(develop) 的 story capture+E-03 composition=classify_story_input 纯函数 + 采纳/骨架渲染（§1.0.5/§1.0.6） wiring=输入为 raw seed→逐字入骨架（§1 逐字保留）；输入为完整 story→原样采纳不包裹进 §1 不改写重排→统一入口与建议面板无 hotfix 推荐与路由（分流显式） test=integration:tests/integration/test_intake.py::test_raw_seed_verbatim_complete_story_adopted_no_hotfix_routing evidence=`.venv/bin/python -m pytest -q tests/integration/test_intake.py` 输出 passed 且 seed 逐字比对成立、采纳文档与输入等价、无 hotfix 路由面 IF-INTAKE-001 IF-MSTART-001
- **FR-0339** owner=tracks/supervisor/service.py+tracks/supervisor/worker_main.py:AC-FR0339-01 surface=decide_intake(develop)+create_run+releases/v{N+1} 可观察出口 composition=M-START 门同步执行 + create_run 委托 + bootstrap story capture（title/story_form params 扩展，§1.0.6） wiring=建议面板选进入开发→Scribe 建议标题写入 frontmatter title→过门（§1.0.6 四步）→从 main 创建 releases/v{N+1}→建立 run 进入 M-STORY→失败/拒绝给出原因与合法继续位置（不残留半截 run 或分支） test=integration:tests/integration/test_mstart_gate.py::test_develop_creates_branch_run_title + e2e:tests/e2e/test_intake_ui.py::test_develop_happy_creates_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_mstart_gate.py` 输出 passed 且分支名=releases/v{N+1}、story frontmatter title 非空、run 进入 M-STORY IF-MSTART-001
- **FR-0339** owner=tracks/supervisor/service.py:AC-FR0339-02 surface=decide_intake(develop) 的门拒绝/确认出口 composition=门前置（§1.0.6：active/dirty/unmerged 三检查先于持久化） wiring=有活跃 run→提示并记入 backlog（backlog.recorded reason=active_run）不开新 run 不建分支→工作区不干净→拒绝并示原因不建分支→未合并分支→单列清单与后果→confirm=true 继续/取消退出（不建分支） test=integration:tests/integration/test_mstart_gate.py::test_active_run_backlog_dirty_refusal_unmerged_confirm evidence=`.venv/bin/python -m pytest -q tests/integration/test_mstart_gate.py` 输出 passed 且三拒绝/确认形态各自的 backlog 事件与无分支断言成立 IF-MSTART-001 IF-BACKLOG-001
- **FR-0339** owner=tracks/supervisor/service.py+.tracks/wiki/flow.md:AC-FR0339-03 surface=decide_intake 三非开发动作+flow.md §3/§4 composition=四动作受理（§1.0.5）+ flow.md §3.1 v0.11 重排版（§1.0.12 钉定文本，Devon 任务经 [layout.devon] 授权落地） wiring=选择进入 backlog/从 backlog 删除/不进入开发归档任一项→不开 run、不建分支（SM-01.7）→flow.md §3/§4 含 Scribe 评估先于 CREATE_BRANCH 的重排文本（INTAKE/PANEL 先于 CHECK_ACTIVE/CREATE_BRANCH） test=integration:tests/integration/test_flow_contracts.py::test_flowmd_scribe_before_create_branch + tests/integration/test_mstart_gate.py::test_nondevelop_actions_no_run_branch evidence=`.venv/bin/python -m pytest -q tests/integration/test_flow_contracts.py` 输出 passed 且三动作后无 run/分支残留、重排文本逐句可定位 IF-MSTART-001
- **FR-0340** owner=tracks/kernel/events.py+tracks/supervisor/service.py+tracks/server/projections.py:AC-FR0340-01 surface=backlog.recorded 事件+GET /api/projects/{pid}/releases backlog 节 composition=backlog 投影事件序派生 + 归档服务面存储（§1.0.7） wiring=backlog.recorded 事件产生（含统一入口选进入 backlog）→条目聚合存储→sidebar 折叠组呈现→选择归档→与 backlog 存储区分（服务面 intakes）→归档查询组可查 test=integration:tests/integration/test_releases_projection.py::test_backlog_storage_and_archive_distinct evidence=`.venv/bin/python -m pytest -q tests/integration/test_releases_projection.py` 输出 passed 且 backlog 节来自 run 面事件、archive 节来自服务面、两存储可区分 IF-BACKLOG-001
- **FR-0340** owner=tracks/supervisor/service.py+tracks/server/projections.py:AC-FR0340-02 surface=backlog.dispositioned 事件+转正流程 composition=去向事件 + 转正=submit_intake(source=backlog) 联动（§1.0.7） wiring=从 backlog 删除或转正成功→原条目移除（投影折叠）→去向可查（dispositioned 事件 disposition/to 字段）→转正以条目为输入走新建 release 流程→成功后过 M-START 门与后续阶段 test=integration:tests/integration/test_releases_projection.py::test_disposition_recorded_and_promote_flow evidence=`.venv/bin/python -m pytest -q tests/integration/test_releases_projection.py` 输出 passed 且 dispositioned 事件携带去向、promote 后 backlog 折叠不含原条目 IF-BACKLOG-001 IF-INTAKE-001
- **FR-0341** owner=tracks/server/api_query.py+tracks/server/static/app/releases.js:AC-FR0341-01 surface=GET /api/projects/{pid}/hotfix-preview+既有 create_run(journey=hotfix_*)+E-03 hotfix 入口 composition=只读预检投影（§1.0.8）+ 既有受理前检（§1b #3） wiring=新建 hotfix→要求选择 GitHub issue 与场景（post-release/dev）→呈现 HOTFIX-TRIAGE 预检与既有 AC 锚定结果→锚定成立→经既有 create_run 继续建 hotfix test=integration:tests/integration/test_hotfix_entry.py::test_hotfix_preview_and_anchor_display + e2e:tests/e2e/test_intake_ui.py::test_hotfix_entry_anchored_proceeds evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_entry.py` 输出 passed 且预检字段与 stand-in GitHub 报告一致、锚定成立路径建 fix 分支 IF-HOTFIXUI-001 IF-HOTFIX-011
- **FR-0341** owner=tracks/server/api_query.py+tracks/supervisor/service.py:AC-FR0341-02 surface=hotfix-preview 拒绝形态+create_run rejected composition=预检只读呈现 + 受理前置 fail-closed（§1.0.8） wiring=锚定不成立→UI 明示退出 hotfix→指引转 backlog/new feature（统一入口）→退出时不建半截 fix 分支、不残留半建 run（受理前置先于持久化） test=integration:tests/integration/test_hotfix_entry.py::test_anchor_fail_exits_no_half_branch evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_entry.py` 输出 passed 且 rejected 原因呈现、无 fix 分支与 run 残留 IF-HOTFIXUI-001
- **FR-0342** owner=tracks/server/api_command.py+tracks/supervisor/gate_exec.py:AC-FR0342-01 surface=POST /api/runs/{run_id}/triage|review+human.triage/human.review 事件 composition=新 kind 受理（awaiting 前置）+ worker 门执行（§1.0.9） wiring=UI 完成 triage（go/no-go/park）→事件流落 human.triage 对应事件（payload decision/actor）→完成文档阶段 review（comment/no-comment）→落 human.review→写请求携带 X-Trac-CSRF 与 Idempotency-Key→重复提交不产生重复事件（幂等键去重+awaiting 前置双保险） test=integration:tests/integration/test_gate_endpoints.py::test_triage_review_events_csrf_idempotency evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且事件恰一条/门、缺头请求被拒 IF-GATEUI-001 IF-CMDSVC-001
- **FR-0342** owner=tracks/supervisor/gate_exec.py+tracks/supervisor/serve_cmd.py:AC-FR0342-02 surface=既有 approvals/release-decision 端点+worker 执行 composition=门命令认领与共享人门管道（§1.0.9，v0.9 缺件落地） wiring=M-REQ-APPROVAL approve→POST approvals→worker 执行→human.approval 落 tracks.db；M-RELEASE release-decision→同理落 release.decided→CLI 仍可用（管理后门）但常规门处理无需离开 UI test=integration:tests/integration/test_gate_endpoints.py::test_approve_release_executed_via_worker + e2e:tests/e2e/test_awaiting_ui.py::test_approve_release_in_panel evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且两命令 completed、域事件落盘可查 IF-GATEUI-001 IF-WEBGATE-001
- **FR-0343** owner=tracks/server/projections.py+tracks/server/static/app/timeline.js:AC-FR0343-01 surface=run detail awaiting_gate+E-02 下栏动作面板 composition=awaiting_gate 字段组 + type→primary_action 封闭映射（§1.0.10） wiring=时间线节点处于 awaiting→下栏详情呈现该门动作面板：原因/影响/唯一主要动作入口→下栏详情与动作面板为同一 surface→用户在下栏直接进入门处理 test=e2e:tests/e2e/test_awaiting_ui.py::test_awaiting_panel_reason_impact_action evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_awaiting_ui.py` 输出 passed 且 reason/impact/动作控件均可定位 IF-AWAIT-001 IF-WEBUI-001
- **FR-0343** owner=tracks/server/projections.py+tracks/server/static/app/timeline.js:AC-FR0343-02 surface=run detail awaiting_gate+E-02 下栏 composition=同上映射的非 awaiting 空值语义 wiring=非 awaiting 节点→awaiting_gate 为 null→下栏不呈现门动作入口仅节点详情→Review 移除后阶段评审均在对应 awaiting 节点动作面板完成（无悬空门） test=integration:tests/integration/test_gate_endpoints.py::test_non_awaiting_no_gate_entry + e2e:tests/e2e/test_awaiting_ui.py::test_no_dangling_stage_review evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且非 awaiting 形态无门入口 IF-AWAIT-001
- **FR-0344** owner=tracks/supervisor/service.py+tracks/server/static/app/docs.js+discussions.js:AC-FR0344-01 surface=POST #35 edits+POST #18 clarifications（扩展）+评审轮文档面 composition=评审轮写入通道（§1.0.11：受理期同步执行） wiring=文档阶段评审轮→评审中的文档在 UI 直接修改（#35，docs_revision base）→发表 inline-comments（#18 anchor_line 发起/thread_token 回复）→写入经命令面事件（material.edited/clarification 落盘）可查 test=integration:tests/integration/test_review_write_channel.py::test_review_round_edit_and_comments + e2e:tests/e2e/test_awaiting_ui.py::test_review_round_doc_write evidence=`.venv/bin/python -m pytest -q tests/integration/test_review_write_channel.py` 输出 passed 且保存产生新 docs_revision、讨论线程落文档可解析 IF-DOCSWRITE-001 IF-DOCSAVE-001
- **FR-0344** owner=tracks/supervisor/service.py:AC-FR0344-02 surface=#17/#35/#18 的 pre-approval 形态 composition=docs_revision 全域化 + #17 null-base pre-approval（§1.0.11） wiring=pre-approval 模式（trio 基线为空/文档不全）→保存与评论提交不被阻塞（docs_revision 含 missing 标签恒可得；#17 null base 合法）→写失败编辑内容不丢可重试→不伪报成功 test=integration:tests/integration/test_review_write_channel.py::test_pre_approval_empty_baseline_not_blocked evidence=`.venv/bin/python -m pytest -q tests/integration/test_review_write_channel.py` 输出 passed 且 M-STORY 形态（仅 story.md）保存与评论成功、失败样本内容保留 IF-DOCSWRITE-001
- **FR-0345** owner=tracks/supervisor/serve_cmd.py+gate_exec.py+tracks/server/static/app/timeline.js:AC-FR0345-01 surface=门命令执行+supervisor drive+SSE/轮询呈现 composition=自驱动闭环（§1.0.9：受理→worker 执行→下一 tick drive→下一道门） wiring=UI 发起门动作→无需手工触发下一步→supervisor 自动推进→下一道门到达时在时间线 UI 呈现（活跃指针前移、awaiting_gate 更新）→用户沿时间线连续处理多道门直到 run 进入下一阶段或完成 test=integration:tests/integration/test_gate_endpoints.py::test_gate_action_auto_advances + e2e:tests/e2e/test_awaiting_ui.py::test_self_drive_next_gate_appears evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且门事件后 run 越门推进、无手工 dispatch IF-GATEUI-001 IF-DRIVE-001
- **FR-0345** owner=tracks/supervisor/serve_cmd.py+gate_exec.py:AC-FR0345-02 surface=serve 常驻 supervisor 推进通道 composition=同上（门间推进归 supervisor） wiring=门间推进无需手工 dispatch 命令→CLI 不作为常规推进路径（UI 旅程全程不经 CLI） test=integration:tests/integration/test_gate_endpoints.py::test_no_manual_dispatch_needed evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且连续两道门间无 drive_run 人工受理 IF-GATEUI-001
- **FR-0346** owner=tracks/supervisor/intake.py+tracks/agents/Scribe.md:AC-FR0346-01 surface=intake report 固定栏目+E-03 建议面板重复/冲突栏目+TRIAGE 派发合同 composition=report schema 固定栏目（§1.0.5/§1.0.12，与 flow.md §4.6 同规则） wiring=Scribe 评估产出报告→含重复检测栏目（跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强）与冲突检测栏目（新需求 vs 既有 FR 冲突对，GO 需显式确认）→统一入口建议面板呈现同一规则内容 test=integration:tests/integration/test_intake.py::test_report_dup_conflict_columns evidence=`.venv/bin/python -m pytest -q tests/integration/test_intake.py` 输出 passed 且 report 含两栏目 schema、跨版本引用可解析 IF-SPLIT-001 IF-INTAKE-001
- **FR-0346** owner=.tracks/wiki/flow.md+tracks/agents/Scribe.md:AC-FR0346-02 surface=flow.md §4.6+TRIAGE 派发合同文本 composition=合同文本（§1.0.12 钉定文本，Devon 任务落地 flow.md 与 Scribe.md） wiring=两项检测的报告格式与触发规则文本写入 M-STORY TRIAGE 派发合同与 flow.md §4→检测只落报告栏目（不做运行时拦截）→无自动驳回行为可观察（无 auto-NO-GO 路径） test=integration:tests/integration/test_flow_contracts.py::test_triage_contract_columns_no_runtime_interception evidence=`.venv/bin/python -m pytest -q tests/integration/test_flow_contracts.py` 输出 passed 且 flow.md/派发合同文本条款可定位、拦截面缺席断言成立 IF-SPLIT-001
- **FR-0347** owner=.tracks/wiki/flow.md+tracks/effects/opencode_review.py:AC-FR0347-01 surface=flow.md §4/§5 评审协议文本 composition=评审人复核合同条款（§1.0.12 钉定文本，Devon 任务落地 flow.md 与评审 agent 合同） wiring=flow.md §4.5-4/§5.3-4 含条款：未解决的非本人发起线程不构成 revise 依据、内容验证通过即 pass、线程确认留给其发起人 test=integration:tests/integration/test_flow_contracts.py::test_reviewer_contract_clause evidence=`.venv/bin/python -m pytest -q tests/integration/test_flow_contracts.py` 输出 passed 且两节条款文本可定位 IF-REVIEW-002
- **FR-0347** owner=tracks/effects/opencode_review.py+tracks/agents/Sage.md+Lex.md+Prism.md:AC-FR0347-02 surface=lex.verdict 计算出口+评审派发合同 composition=revise 分支收窄为自有未决线程（§1.0.13；agent 纪律待实现 Devon） wiring=内容验证通过、仅余非本人发起未决线程的一次评审→verdict=pass 且不触发无发现文档修订→评审人自有新发现的 revise 均带锚定线程（线程 initiator 可追溯） test=integration:tests/integration/test_review_exit_rule.py::test_pass_without_self_findings_revise_anchored evidence=`.venv/bin/python -m pytest -q tests/integration/test_review_exit_rule.py` 输出 passed 且非本人线程形态 verdict=pass、自有线程形态 revise 携锚定 IF-REVIEW-002
- **NFR-0157** owner=tracks/server/api_command.py+tracks/server/static/app/api.js:AC-NFR0157-01 surface=全部新写端点+UI 写请求头 composition=_mutate 共用管道（新端点全部经命令面，§1.0.9）；api.js 统一注入头 wiring=UI 发起的全部写操作经命令面事件提交（intakes/decisions/dispositions/triage/review 均有 command.accepted）→写请求携带 X-Trac-CSRF 与 Idempotency-Key→重复提交不产生重复副作用→CLI 不呈现为第二常规写路径 test=integration:tests/integration/test_gate_endpoints.py::test_all_new_writes_csrf_idempotency_single_path + e2e:tests/e2e/test_awaiting_ui.py::test_ui_writes_carry_headers evidence=`.venv/bin/python -m pytest -q tests/integration/test_gate_endpoints.py` 输出 passed 且新端点缺头被拒、幂等去重成立 IF-CMDSVC-001 IF-GATEUI-001
- **NFR-0158** owner=tracks/server/static/app/**+tracks/server/api_query.py:AC-NFR0158-01 surface=Releases/时间线/面板全部数据面 composition=只读投影+SSE 消费（零新数据源，§1.0.3） wiring=UI 全部状态来自服务端查询投影与事件流（releases/intakes/timeline/ac-chain/run detail）→无 mock 数据→API 失败呈现用户可观察失败反馈（不伪报成功）→SSE 断线/重复乱序不使 UI 倒退（游标补读继承） test=integration:tests/integration/test_timeline_full.py::test_no_second_source_failure_visible + e2e:tests/e2e/test_releases_ui.py::test_api_failure_feedback_new_surfaces evidence=`.venv/bin/python -m pytest -q tests/integration/test_timeline_full.py` 输出 passed 且新数据面无第二来源、失败注入样本反馈可见 IF-QUERY-001 IF-STREAM-001 IF-TIMELINE-002
- **NFR-0158** owner=pyproject.toml+tracks/server/pages.py+tracks/server/static/styles.css:AC-NFR0158-02 surface=仓库合同面+工作台 chrome composition=v0.10 裁定继承（§0.1）+五 tab 世界下的裁定回归 wiring=v0.10 工作台裁定（外壳钉视口/Vercel 极简设计令牌/registry 行紧凑化/pane bar 单控制面/自动保存）继续有效→现有行为无退化（v0.10 e2e 资产按五 tab 世界修订后回归） test=integration:tests/integration/test_flow_contracts.py::test_v010_rulings_anchored（守卫，§8.1） + e2e:tests/e2e/test_workbench_ui.py::test_v010_rulings_regression evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且五项裁定断言全部成立 IF-WORKBENCH-001 IF-GREENGUARD-001

## 2. Scaffold 宣言

本节是 M-DESIGN 阶段在宿主项目创建/修改文件的唯一合同。以下文件已随本设计一并物理交付（全部为声明性/配置性/合同文本修订，无业务行为）；此外不创建任何文件（`tests/ground_truth/` 不适用——test-plan §3 判定）。

- `pyproject.toml` — version 0.11.0（kind: config；bytes 变化已同步 §4.2 六条 pyproject-backed config_digest 重锚）
- `tracks/__init__.py` — `__version__ = "0.11.0"`（kind: config；与 pyproject 同步 bump，version gate 绑定对象）
- `.tracks/projects/project.toml` — `[layout.devon].writable` 追加单文件授权 `.tracks/wiki/flow.md`（kind: config；canonical 合同路径，FR-0120 layout 由 Archer 设计；为 §1.0.12 钉定文本的 Devon 落地任务开写域；bytes 变化已同步 §4.2 hooks-runner 的 project.toml digest 重锚）

本节以外不创建 scaffold。`.tracks/wiki/flow.md` **不在 scaffold 域内**（保留域拒收 `.tracks/**`）——其 v0.11 修订文本钉定于 §1.0.12，由 Devon 任务经 `[layout.devon]` 单文件授权落地，属待实现任务而非本节交付。§1.0.1 表中的 `tracks/supervisor/intake.py`、`gate_exec.py`、`tracks/server/static/app/releases.js`/`intake.js`、timeline.js 重绘、pages/projections/api_*/service/db/worker/templating/opencode_review/Scribe.md/Sage.md/Lex.md/Prism.md/flow.md 的行为体或修订均为**待实现 Devon 任务**；`tests/integration/test_releases_projection.py`、`test_workbench_tabs.py`、`test_timeline_full.py`、`test_intake.py`、`test_mstart_gate.py`、`test_hotfix_entry.py`、`test_gate_endpoints.py`、`test_review_write_channel.py`、`test_flow_contracts.py`、`test_review_exit_rule.py` 与 `tests/e2e/test_releases_ui.py`、`test_intake_ui.py`、`test_awaiting_ui.py`、`test_return_ui.py` 及 `test_workbench_ui.py` 回归修订均为**待实现 Shield 任务**；本文不得把它们当作既有可执行能力。

**本版无接口桩**：v0.11 的 Shield 契约测试全部经 serve fixture（真实子进程/HTTP）或只读投影函数进入被测面，不直接 import 新模块——新模块（intake.py/gate_exec.py/releases.js 等）缺席时测试以合法 Red（端点 404/投影缺席）而非收集失败呈现；v0.10「修订面无桩」先例继续适用。

## 3. 技术选型

### 3.1 零新运行时依赖（intake/门执行均复用既有缝）

intake 评估复用 effects/opencode 会话缝（与 run 派发同一 backend 通道：fake/live 由 TRAC_AGENT_BACKEND 既有开关决定）；门执行复用共享人门管道与 SQLite/git 本地操作；issue 取文复用 effects/github（stand-in/live 通道既有）。无新 Python 依赖、无新 JS 依赖、无构建链（NFR-0156 条件未触发继承）；Chromium 预算沿用 ui-e2e 通道（NFR-0155 继承）。

### 3.2 intake 落服务面而非 run 面（关键取舍）

SM-01.7 禁止评估期建 run；若为评估伪建 run 会污染调度/backlog 投影/活跃判定。代价：服务面新增一张表与三个 service_events 成员、supervisor 多一个后台认领通道；换来 run 面事件集几乎零扰动（仅 backlog.dispositioned 一个新成员）、四动作的非建分支出口天然成立（无 run 可残半）。备选「intake 临时文件存储」被否：崩溃恢复与审计弱于 SQLite 表。

### 3.3 门命令执行归 worker（而非 HTTP 同步）

人门事件的提交通常快，但必须与 supervisor drive 串行化（writer_lock/单写者）；HTTP 同步执行会在 drive 持锁期阻塞请求，且 approve 之后的首个 decide 窗口可能派发 agent（长任务绝不可在 handler 内跑）。worker 认领 + 下一 tick drive 的两段式与 v0.9「HTTP 受理即返回、worker 边界执行」设计一致，并使 v0.9 已声明未落地的执行面一次性补齐。intake/backlog/clarification 类（纯短写）按 edit_material 先例受理期同步执行——两类执行形态的分界写入 interfaces §1b。

### 3.4 docs_revision 全域化（而非引入第三 token）

评审轮写入缺口的两解：新 pre-approval token 或补全既有 token 定义域。取后者：missing 标签与 `m_impl_baseline_digest` 约定同构、六件套齐全目录值不变（纯扩展）、UI/base 合同零改写；#17 面 null-base 仅在 trio 不可得时合法（结构上不可能存在待绑定批准）。`tracks/baseline.py` 与 FR-0308 绑定语义继续零触碰。

### 3.5 不引入的机制

不引入 WebSocket（SSE 足够）、不引入前端路由库/状态库（继承原生 ES modules 纪律）、不引入 intake 的 UI 实时推送（轮询 GET intakes/{id} 足够——评估分钟级，SSE 服务面事件流不在本版扩容）、不做 hotfix_triage in-run 锚定门的 UI 化（§5.1）、不改 `trac start` CLI 语义（管理后门）。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

三层合同（`[unit]/[integration]/[e2e]`）的 framework/paths/collect/run/run_selected/cwd 命令串与 `[nightly]/[adapter]/[layout.shield]/[lint]`、`[host-contract.*]` 段逐字继承 v0.10 现行值；本版唯一文件变更为 `[layout.devon].writable` 追加 `.tracks/wiki/flow.md` 单文件授权（§1.0.12 的 Devon 落地任务写域，FR-0120 layout 由 Archer 设计）——文件 bytes 变化使 hooks-runner 的 project.toml digest 重锚（§4.2）；`ui` marker 默认排除与 ui-e2e job 选择语义继承。M-TEST 收集/执行与 M-IMPL RED_CHECK/GREEN_GATE 沿用该合同；本版新增测试资产全部落既有 paths（tests/integration、tests/e2e[+ui marker]），合同无需扩容。

### 4.2 Canonical quality guard registry（v0.11）

以下 TOML block 是 tracks 宿主 guard registry 的 v0.11 canonical 真相（承接 ARCH-010 §4.2）。本版输入变更二处：`pyproject.toml`（version 0.11.0）与 `.tracks/projects/project.toml`（[layout.devon] 追加单文件授权，§1.0.12）——六条 pyproject-backed config_digest 重锚为 `7fbdde4b…`，hooks-runner 的 project.toml digest 重锚为 `e1735466…`（§2 已交付）；cognitive-complexity 的 `.flake8` digest（`f899995c…`）随文件 bytes 不变而继承。字段语义、digest 公式（单文件 sha256(raw bytes)）、八类强制、fail_closed、禁 `--exit-zero` 全部继承，不再重复。

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
config_digest = "sha256:7fbdde4b93974638223e837c6b805728a6b4ad3ded03486da9142332afad099f"
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
config_digest = "sha256:7fbdde4b93974638223e837c6b805728a6b4ad3ded03486da9142332afad099f"
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
config_digest = "sha256:7fbdde4b93974638223e837c6b805728a6b4ad3ded03486da9142332afad099f"
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
config_digest = "sha256:7fbdde4b93974638223e837c6b805728a6b4ad3ded03486da9142332afad099f"
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
config_digest = "sha256:7fbdde4b93974638223e837c6b805728a6b4ad3ded03486da9142332afad099f"
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
command = ".venv/bin/coverage report --fail-under=89"
config_paths = ["pyproject.toml"]
config_sections = ["tool.coverage.run", "tool.coverage.report"]
config_digest = "sha256:7fbdde4b93974638223e837c6b805728a6b4ad3ded03486da9142332afad099f"
scope = ["tracks"]
threshold = "line coverage >=89; by=collected; source omit=pure-I/O backends (opencode subprocess/session/pty, fake backends, evidence I/O — pyproject.toml [tool.coverage] omit)"
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
config_digest = "sha256:e17354666c372290369aaf8517b31b6891953acd8550cf3e3845d5ade427d12c"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test,deliverables,trace,reach,ui-e2e; milestone=release-evidence"
timeout_seconds = 3600
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test,deliverables,trace,reach,ui-e2e"
```

### 4.3 CI / pre-commit / release

- Required checks 七名不变（lint/coverage/test/deliverables/trace/reach/ui-e2e）；`release-evidence` milestone 硬门禁不变。browserless job 的 `-m` 排除表达式、ui-e2e job 的 Chromium 安装与 `-m ui` 语义全部继承；本版 CI 零 workflow 变更。
- pre-commit hook 不变（只跑守卫）；新源码（supervisor/intake、server 修订、static/app 新模块）自动纳入既有守卫 scope。JS 层守卫空缺显式继承 v0.10 决定（无 JS linter，UI e2e 与评审承载；不引入 Node 工具链）。
- 安装命令（副作用归 Runtime）不变：`python -m venv .venv`、`.venv/bin/pip install -e '.[dev]'`、`git config core.hooksPath .githooks`。
- version gate：bindings 声明不变（pyproject.toml:project.version 与 tracks/__init__.py:__version__ 绑定 {release_version}）；版本已设计期 bump 至 0.11.0（§2 已交付），M-VERIFY 前文件版本必须等于目标发布版本（v0.10 §4.3 纪律继承）。

### 4.4 Integration/e2e 基础设施

- Shield integration 新资产落 `tests/integration/`：`test_releases_projection.py`（注册表/backlog/归档/去向投影）、`test_workbench_tabs.py`（PAGES/URL/tab 封闭集）、`test_timeline_full.py`（数据 parity/五态派生输入/return_options/确认复验/降级）、`test_intake.py`（三来源/评估报告/四动作/分类/无 hotfix 路由/报告栏目）、`test_mstart_gate.py`（门四步/非建分支出口）、`test_hotfix_entry.py`（预检呈现/锚定成败两路）、`test_gate_endpoints.py`（门端点/CSRF+幂等/worker 执行/自动推进/awaiting 映射）、`test_review_write_channel.py`（评审轮编辑与评论/pre-approval）、`test_flow_contracts.py`（flow.md/派发合同钉定文本锚点组，§1.0.12）、`test_review_exit_rule.py`（revise 收窄规则）。
- e2e 新资产落 `tests/e2e/`（`@pytest.mark.ui`）：`test_releases_ui.py`（Releases 窗口/五 tab/时间线完整绘制/固定详情栏/失败反馈）、`test_intake_ui.py`（建议面板/develop happy/hotfix 入口）、`test_awaiting_ui.py`（动作面板/评审轮写入/自驱动闭环/写安全头）、`test_return_ui.py`（回拨确认面）；`test_workbench_ui.py` 为 v0.10 资产按五 tab 世界修订（test-plan §11）。
- serve fixture 继承（真实子进程、隔离 home/项目仓、随机端口、口令供给）；UI 层在其上叠 Playwright。fault 注入经公开合同面：API 失败经 Playwright route、门命令经 awaiting 前置构造、Scribe 评估经 fake backend（TRAC_AGENT_BACKEND）、issue 取文经 stand-in GitHub（TRAC_GITHUB_API_BASE）、dirty tree 经夹具仓预置未提交文件、unmerged 经未合并分支夹具。
- 测试数据：`tests/assets/v0.11/`（Shield 写域）固化多版本 run 注册表种子、backlog/dispositioned 事件种子、含/不含 AC 锚定的 hotfix 预检夹具；canary 密钥纪律继承。
- deterministic suite 默认离线：全部新面在 loopback + fake/stand-in。
- 电池墙钟有界继承（900s 基线 + 60s/节点，TRAC_BATTERY_TIMEOUT_BASE/PER_NODE）；Scribe intake 评估的 dispatch 沿用既有超时语义。
- RED_CHECK 守卫通道（interfaces §1u / IF-GREENGUARD-001）继承：§8.1 清单（test-plan）为守卫集合唯一权威，守卫 pass 记 guard_verified、失败/缺席 fail-closed。

### 4.5 Build / artifact

build backend 与 wheel 名不变；package-data 集合不变（`server/static/app/*` 已含新模块——通配模式覆盖新增文件，无 pyproject 改动）；M-VERIFY build gate 与 smoke 不变；版本字段 0.11.0 与 v0.11.0 tag 经 version gate 绑定比对。

### 4.6 发布恢复

服务面恢复合同继承（recover_on_startup/lease 过期重认领/waits 保留）。本版追加三处可恢复性事实：(1) intake 评估的幂等重派——evaluating 状态在 serve 重启后经超时回收重新派发（≤3 预算），报告以最后成功解析的 envelope 为准，不产生重复 intake；(2) 门命令的 requeue 语义继承（claimed → accepted + command.requeued），awaiting 前置保证重放安全（门已过即拒，不重复落事件）；(3) decide_intake 的受理前置全部先于持久化——门拒绝/确认等待不残留半截命令效果（SM-01.5），develop 成功路径委托 create_run 的既有崩溃恢复（bootstrap 幂等）。

## 5. 有意识简化与风险

### 5.1 有意识简化

- in-run hotfix_triage 锚定门不 UI 化：入口预检/锚定结果呈现面已交付（FR-0341），锚定引用提交（human.anchor 的 refs 输入）留在 CLI 后门——面板对该 awaiting 呈现原因/影响与 owning surface 指引，不出现半套表单。
- intake 评估状态用轮询而非 SSE 推送（评估分钟级；服务面事件流扩容留待真实需求）。
- 归档条目不支持复活为待决（SM-01 无该转移；从归档恢复=重新提交输入）。
- 时间线历史回拨回边只读呈现（事件序），「回拨合法性」详情字段对历史节点显示当时事件的 payload，不回溯重算历史合法性。
- backlog 投影从事件序派生，store 的 `backlog` 投影表（CLI status 消费）本版不改其 schema——CLI 面显示已处置条目与否属管理后门呈现细节，不入合同。
- 无 JS linter/format 守卫（v0.10 显式空缺继承）。

### 5.2 风险

- **v0.9 门命令执行缺件的落地风险**：record_stage_approval/record_release_decision/submit_clarification 自 v0.9 起只有受理无执行（worker_main 仅 drive_run）——本版把执行面连同新 triage/review 一起落地是 FR-0345 的前置；共享人门管道抽取若与 CLI 行为漂移，会破坏「同一 kind 双面等价」（IF-CMDSVC-001）。缓解：抽取为纯共享函数 + CLI 语义零变更 + test_gate_endpoints 对两面等价的断言。
- **M-START 门语义的 CLI/UI 分叉**：UI 门按 flow.md §3.1 拒绝脏树，CLI `trac start` 按 v0.5 AC-FR0267-02 裁定不拒绝——两入口长期并存于同一仓库。缓解：分叉写入 interfaces §0.1 变更清单与本节，UI 门为 flow.md 语义的唯一实现面；后续统一需 spec 修订（记录为已知分叉而非静默）。
- **intake 评估的 agent 依赖**：建议面板依赖真实 Scribe 派发（fake backend 可测，live 面受 provider 波动）；评估失败/超时的可读呈现与 ≤3 重试预算是面板可用性下限。缓解：失败形态入 test_intake 注入矩阵。
- **五 tab 收敛的破坏半径**：v0.10 e2e/integration 资产中 tab 集/页面 URL 断言需随版修订（test-plan §11 列举）；修订属 spec 变更驱动，非迁就实现。
- **docs_revision 全域化的兼容面**：missing 标签改变「文档不全目录」的 token 值（此前该目录根本无 token）——既有断言只覆盖六件套齐全目录，值不变；若有隐藏消费者依赖「无 token=拒绝」语义，pre-approval null-base（#17）与 #35 的新可达性即其替代。评审期重点核对 _docs_revision_or_none 的全部调用点。
- **评审出口收窄的回归面**：revise 分支收窄改变 Lex 面既有三分支行为的中间分支语义（非 Human 非自有线程 → 此前 revise、今后 pass）；v0.10 test_lex_review_park 的形态断言需按新规则复核（作者侧 RESPOND 义务与 discussion_ready 出口闸不变是安全网）。
- **supervisor 认领通道的并发面**：门命令认领与 drive 认领同处 tick 循环——认领顺序（先门后 drive）保证门事件先于下一 drive 窗口落盘；漏认领只延迟不丢失（accepted 持久）。租约/防旧 CAS 继承仲裁。
