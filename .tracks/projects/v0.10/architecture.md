---
envelope: tracks-envelope:v2
architecture_id: ARCH-010
spec_ref: SPEC-010
created: 2026-09-27
status: draft
sha:
---

# v0.10 — 架构：Web 工作台完善（UI）+ 发布卫生

本文是 ARCH-009 的增量延伸。v0.1～v0.9 的事件溯源、单写者、canonical 阶段状态机、发布闭环五阶段、质量守卫 registry、测试流水线、CLI 与 v0.9 的 server/supervisor 双层（Starlette 交付面 + 后台驱动）全部保持不变；v0.10 在此之上叠加 SPA 工作台 chrome（浏览器侧原生 ES modules 应用层）、登录名字绑定、文档中心应用层与 run 时间线视图，并在同版内完成发布卫生修复（#182/#179/#180/#181/#184/#183）。核心取舍：SPA 交互模型落地但**不引入构建链**（NFR-0156 条件未触发，见 §3.1）；发布卫生全部落在既有模块的定点修正上，不新增子系统。

revision 注记：2026-09-28 回滚重起草（human.return M-TEST→M-DESIGN），吸收 Prism no-diff 复审 blocker（test-plan §8 inline 线程 T-001）——新增 §1.0.8 测试节点两类纪律与 interfaces §1u/IF-GREENGUARD-001 守卫通道，§1.2 五条 closure 行重分类（守卫语义标注 + IF-GREENGUARD-001），§4.4/§5.2 同步；模块边界、合同面与其余 closure 与前版无变更。2026-09-28 RESPOND round 2（Prism revise findings）：§1.0.1 增长轴表补第 22 行（tracks/executor/test_execute.py 的守卫通道消费面，PLANNING 不得丢失该 task）。2026-09-28 RESPOND round 3+（deadlock 轮次）：§5.2 登记 FR-090∧FR-0314.4 组合死锁（T-001 双侧实证）及其对本版交付管道与 FR-0332 park-resume 闭环的阻断面；无合同变更。2026-10-01 回滚重起草（human return M-IMPL→M-DESIGN，T-004 DIAGNOSE spec_gap 1-prism-31）：§1.0.4 修订身份重设计（docs_revision 六件套 token + 编辑面分家，`tracks/baseline.py` 与 FR-0308 零触碰）；§1.0.1 行 6/7 与 §1.2 AC-FR0323-01/02 closure 同步；409 `current_revision` 钉死顶层字段。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-009（及此前各版）

- 唯一生产路径 `cli -> kernel.machine.decide -> executor.execute -> store.append`、SQLite append-only events、投影可重建、单写者 writer_lock 与 per-kind reconcile 不变；**tracks.db 的 EVENT_TYPES 封闭集本版不追加任何成员**；v0.10 唯一新事件类型为服务面 `service_events` 追加成员 `auth.name_bound`（interfaces §1a #25）。
- `kernel/` 纯控制、`executor/` 副作用、`checks/` 静态闭合、`effects/` 外部通道、`adapters/` 宿主测试框架语义、`cli/` 交付面、`server/` Web 交付面、`supervisor/` 后台驱动控制层的分层不变。kernel 纯性纪律不变：FR-0332 的 park/resume 全部经事件归约与既有 `human.review` 命令面完成，kernel 不读文档文件（§1.0.7）。
- 服务命令封闭集（IF-009 §1b 十三种 kind）不变；本版零新增命令 kind（名字绑定是认证面操作，文档编辑复用 edit_material，milestone ensure 是关闭链内部步骤）。
- 既有 CLI 子命令集合与 USAGE 不变；本版不新增顶层子命令（`trac status`/`trac review` 语义追加为既有命令的内部扩展，interfaces §2c）。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]` 测试执行合同的命令串逐字不变（`ui` marker 的排除由 pyproject addopts 承载，合同命令不带 `-m`）；`[layout]`/`[lint]`/`[adapter]`/`[nightly]` 段不变。
- 发布闭环（M-VERIFY→M-SECURITY→M-RELEASE→M-PUBLISH→M-MILESTONE）、candidate 主身份、preview digest 绑定、publish 幂等/reconcile、escape barrier、失败证据链、Issue 闭环、reference host 与三旅程合同全部继承。
- 质量守卫八类与 canonical registry 机制不变；本版因 pyproject.toml 内容变化（certifi/playwright 依赖、ui marker、package-data、version bump）与 project.toml 内容变化（version bindings、required_checks）重锚六条 pyproject-backed config_digest 与 hooks-runner 的 project.toml digest（§4.2，先例 e5637a1）。
- Vditor 3.11.3 vendored 资产与 manifest 逐字节不变（同源、禁 CDN、可选引擎禁用；本版只补应用层加载策略）。
- SSE 合并序/游标补读/轮询回退、CSRF + Idempotency-Key、redaction 全出口脱敏、本机单用户边界（RP-01）、supervisor 单活动 run 串行 + hotfix 换队语义全部不变；UI 客户端是这些既有合同的遵守面而非新来源。

### 0.2 v0.10 变更

- 页面壳收敛：`PAGES` 8 名封闭集与 URL 表不变，渲染收敛为两个文档——login 双栏 auth shell（E-02）与同一 workbench shell（E-01，携带 `data-route` 深链）（interfaces §1l.1）。v0.9 遗留 unit 断言中涉及「review 页内嵌 Vditor 资产标签」的一条随之迁移（Vditor 改为 docs 视图按需同源加载，§5.2 记录）。
- 新增浏览器侧应用层 `tracks/server/static/app/`（原生 ES modules：shell/router/api/sse/tabs/sidebar/views/editor/panes/discussions/timeline）与 `tracks/server/static/styles.css`——实现产物归 Devon（interfaces §3 #2 预留路径与打包）。
- `tracks/server/auth.py`：`auth` 表追加 `display_name` 可空列（启动期幂等演进），会话 actor 端到端绑定显示名（interfaces §1m）。
- `tracks/server/app.py`/`api_query.py`/`projections.py`：路由表追加 6 个端点（认证面 2 + 文档中心 project 域 4）；timeline 投影追加 `stage_order`；`_DOC_FILES` 扩展六件套（`design` 别名保留）。
- `tracks/server/api_command.py`：edit_material 的 409 响应追加 `current_revision`（interfaces §1o.2）。
- 发布卫生四处定点修正：`tracks/executor/milestone_chain.py`（终态感知守卫 + ensure-then-close 接线）、`tracks/executor/hotfix_face.py`+`hotfix.py`（前检分类映射）、`tracks/effects/github.py`（certifi TLS + `ensure_project_milestone`）、`tracks/executor/host_contract.py`+`verify_version.py`（version bindings 解析与执行）。
- FR-0332 跨层小改：`tracks/effects/opencode_review.py`（评审出口三分支计算 + LEX_REVIEW 派发注入 open_threads）、`tracks/kernel/machine_verdicts.py`（park 归约）、`tracks/kernel/machine.py`（State 投影字段）、`tracks/cli/status_cmd.py`（清单渲染）、`tracks/agents/Lex.md`（verdict 词汇与线程复用纪律）。
- 合同/配置面（随本设计物理交付，§2）：`pyproject.toml`（0.10.0 + certifi + playwright + ui marker + package-data）、`tracks/__init__.py`（0.10.0）、`.tracks/projects/project.toml`（bindings + required_checks）、`.github/workflows/ci.yml`（ui-e2e job + `-m` 选择器）、`.github/workflows/nightly.yml`（`-m` 选择器）、`docs/getting-started/installation.md`（live 前置节）。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | v0.10 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `tracks/server/static/app/*.js` | SPA 应用层（shell 引导、客户端路由、api/sse 客户端、tab/sidebar 状态、五个功能区视图、editor/panes/discussions/timeline）（**待实现 Devon**） | FR-0316/0317/0318/0319/0322/0324/0325/0326、NFR-0153/0154 | IF-WORKBENCH-001, IF-DOCCENTER-001, IF-DOCSAVE-001, IF-DISCUSS-001, IF-TIMELINE-001, IF-WEBUI-001 |
| 2 | `tracks/server/static/styles.css` | workbench chrome 样式（**待实现 Devon**） | FR-0316/0317 | IF-WORKBENCH-001 |
| 3 | `tracks/server/pages.py` | 两壳渲染（login 双栏 auth shell + workbench shell/`data-route`）；data-testid 锚点集 | FR-0316/0317/0320/0321 | IF-WORKBENCH-001, IF-AUTHNAME-001 |
| 4 | `tracks/server/auth.py` | `display_name` 列、名字校验、生效 actor（display_name ?? auth.actor ?? local-user） | FR-0321 | IF-AUTHNAME-001 |
| 5 | `tracks/server/app.py` | 路由追加（§2b #29-35）；login 响应 `name_required`；workbench 入口名字门（302） | FR-0320/0321/0322/0325 | IF-AUTHNAME-001, IF-DOCCENTER-001, IF-DISCUSS-001 |
| 6 | `tracks/server/api_query.py` + `projections.py` | docs tree/版本化 read/diff/discussions 读模型（tree/read 的 revision 字段 = docs_revision，§1.0.4）；timeline `stage_order`；`_DOC_FILES` 六件套 | FR-0322/0324/0325/0326 | IF-DOCCENTER-001, IF-DISCUSS-001, IF-TIMELINE-001, IF-QUERY-001 |
| 7 | `tracks/server/api_command.py` + `tracks/supervisor/service.py` | 文档中心编辑面 `POST /api/projects/{pid}/docs/{version}/{doc}/edits`（token=docs_revision；`docs_revision` 纯函数定义于 service.py）；#17 收敛 trio 域；409 顶层 `current_revision` | FR-0323 | IF-DOCSAVE-001 |
| 8 | `tracks/supervisor/db.py` | auth 表 `display_name` 列的建表/演进 | FR-0321 | IF-AUTHNAME-001 |
| 9 | `tracks/executor/milestone_chain.py` | `_complete_milestone` 终态感知守卫；关闭链接入 ensure-then-close | FR-0327/0331 | IF-MILESTONE-002, IF-TRACKER-001 |
| 10 | `tracks/effects/github.py` | 显式 certifi SSL context（`TRAC_GITHUB_CA_BUNDLE` 覆盖）；`ensure_project_milestone`（list→create→readback） | FR-0330/0331 | IF-TLS-001, IF-TRACKER-001 |
| 11 | `tracks/executor/hotfix_face.py` + `hotfix.py` | GithubIssuesError 分类承接与 `issue_fetch_failed` 分类指引映射 | FR-0329 | IF-HOTFIX-011 |
| 12 | `tracks/executor/host_contract.py` + `verify_version.py` | version_scheme `bindings` 解析与 version gate 绑定比对（`{release_version}` 占位符） | FR-0328 | IF-VERSION-001 |
| 13 | `tracks/effects/opencode_review.py` | 评审出口三分支（pass / pass-pending-human-threads / revise）；LEX_REVIEW 派发注入 `open_threads` | FR-0332 | IF-REVIEW-001 |
| 14 | `tracks/kernel/machine_verdicts.py` + `machine.py` | `lex.verdict(pass-pending-human-threads)` 归约为 AWAIT_HUMAN park；State 投影 pending 线程清单 | FR-0332 | IF-REVIEW-001 |
| 15 | `tracks/cli/status_cmd.py` | `awaiting=review_pending_threads` 的待决线程清单渲染 | FR-0332 | IF-REVIEW-001 |
| 16 | `tracks/agents/Lex.md` | verdict 词汇与 findings 线程跨 attempt 复用纪律（agent 合同文档，Devon 实现） | FR-0332 | IF-REVIEW-001 |
| 17 | `pyproject.toml` | 0.10.0；certifi（运行时）/playwright（dev）依赖；`ui` marker 与 addopts 排除；`server/static/app/*` package-data（§2 已交付） | FR-0328/0330、NFR-0155 | IF-VERSION-001, IF-TLS-001, IF-WEBUI-001 |
| 18 | `tracks/__init__.py` | `__version__ = "0.10.0"`（§2 已交付） | FR-0328 | IF-VERSION-001 |
| 19 | `.tracks/projects/project.toml` | version_scheme `bindings`；required_checks 追加 `ui-e2e`（§2 已交付） | FR-0328、NFR-0155 | IF-VERSION-001, IF-WEBUI-001 |
| 20 | `.github/workflows/ci.yml` + `nightly.yml` | `ui-e2e` required job；browserless job 的 `-m 'not performance and not ui'`（§2 已交付） | NFR-0155 | IF-WEBUI-001 |
| 21 | `docs/getting-started/installation.md` | 「Live GitHub 旅程环境前置」节（§2 已交付） | FR-0330 | IF-TLS-001 |
| 22 | `tracks/executor/test_execute.py` | RED_CHECK 消费 test-plan §8.1 守卫清单：守卫 pass 记 `guard_verified`、失败/缺席 fail-closed（**待实现 Devon**；§1.0.8/interfaces §1u） | IF-GREENGUARD-001（T-001 修订） | IF-GREENGUARD-001 |

### 1.0.2 SPA 工作台壳：两壳 + 原生 ES modules（FR-0316/0317/0318/0319）

服务端只负责两个 HTML 文档：login（双栏 auth shell）与 workbench shell。workbench shell 是三 段式骨架（tab 条 + sidebar + main area）加一份 `data-route` 深链描述；8 个既有 URL 全部有效（深链），未认证经既有 `_AuthBoundary` 302 至 `/login`。浏览器侧 `shell.js` 是客户端装配入口：读取 `data-route` → 初始化 router/tabs/sidebar → api.js（统一注入 `X-Trac-CSRF` 与 `Idempotency-Key`、错误一律转成可见失败反馈）与 sse.js（`event_cursor` 补读、重复/乱序不倒退）→ 功能区视图（Projects/Runs/Docs/Review/Todos/Settings）按需挂载。tab 集合只在浏览器内存（SM-04），刷新为空集合是预期行为；未知/缺失值一律渲染可读降级标记。web 层不直接耦合 runtime 的既有约束不变：全部变更经命令服务、全部读取经投影。

### 1.0.3 名字绑定的端到端链路（FR-0321）

名字的唯一持久化点是 `service.db` 的 `auth.display_name`（单行语义，RP-01 不变）。生效 actor = `display_name`（已采集）否则 auth 行 actor 否则 `local-user` fallback。绑定动作（`POST /api/auth/name`）一次完成三件事：写 `display_name`、把既有 sessions 行 actor 更新为该名字、落 `auth.name_bound` 审计事件。此后所有以会话 actor 为准的面——Web 提交的 `human.approval`/`human.anchor` 审计、assignment 称谓、discussion speaker 标签（IF-009 §1f.4 的会话 actor 规则）——自动携带该名字，无需第二通道。名字未采集时 workbench 入口 302 回 `/login`（不进数据面），登录页客户端据 `GET /api/auth/profile` 的 `name_set` 决定呈现密码步还是名字步。

### 1.0.4 文档中心应用层（FR-0322/0323/0324/0325）

文档中心是 project 域读模型 + **专属编辑面**的组合（2026-10-01 DIAGNOSE spec_gap 修订：初版把六件套编辑挂在不动的 trio 摘要上，对设计文档结构性不可满足）。修订身份分两层：`baseline.revision_digest`（trio）专属批准绑定面（FR-0308 逐字不变，`tracks/baseline.py` 零触碰）；新 `docs_revision`（六件套 `label:body_sha` 摘要，`supervisor/service.py` 纯函数，T-004 写域；projections 经既有 server→supervisor 方向消费）是文档中心的乐观并发 token——任一文档变更即移动。编辑面分家：文档中心编辑走新 `POST /api/projects/{pid}/docs/{version}/{doc}/edits`（base=docs_revision，服务端解析 editable_run_id 后经命令服务提交 `edit_material` + `revision_kind:"docs"`，`material.edited` from/to 为 docs_revision 值）；运行域 `#17` 收敛为 trio 域（design 别名编辑 422 指向文档中心面——v0.9 该别名编辑在 trio token 下不可满足，缺陷面显式关闭）。trio 文档经任一面保存同时移动双身份——批准绑定不被绕过是结构保证。409 响应体**顶层**携带 `current_revision`（该面当前 token；唯一权威形态），UI 给出重载放弃/强制覆盖（以 `current_revision` 为新 base 重提本地内容）两选项；写失败草稿保留。目录树（版本号数值逆序、最新置顶、六件套、可编辑 run 标注）与版本化 read/diff/discussions 走 project 域端点（tree/read 的 revision 字段为 docs_revision）。编辑器宿主：docs 视图打开时按需注入同源 Vditor 资产并以 `ir` 模式实例化；注入或初始化失败回退 textarea（内容不丢、主路径不阻断）。保存控件 dirty 门。分屏 ≤4 列、每 pane 独立选择器/工具栏/工具文档。discussion 覆盖层只读：服务端经 tracks/discuss parser 产出线程投影（客户端不解析协议），UI 提供显示/隐藏、下一个、只看未决；本版无 UI 写回（无 mutate 端点，写回留 `trac discuss`）。

### 1.0.5 run 时间线视图（FR-0326，整包进 v0.10）

纯前端 + 一个投影字段：timeline 响应追加 `stage_order`（13 阶段显示序，服务端从 `canonical_stage_order()` 组合 M-START 与 M-REQ-APPROVAL 得出，客户端不硬编码阶段集合）。timeline.js 消费既有 timeline/ac-chain 数据：顶部当前节点卡片（责任方/attempt/运行时长/唯一主要动作——由 run detail 的 control_state/todos/wait/executions 组合）、线性时间线每次 attempt 独立节点不折叠、回拨（指向上游的 stage 回退/escape 事件对）以可辨认回边呈现、点节点开浮层（起止时间/artifact/revision，链接跳文档中心）。零服务端新数据源。

### 1.0.6 发布卫生四处修正（FR-0327/0328/0329/0330/0331）

- **#182（blocker）**：`milestone_chain._complete_milestone` 的幂等守卫从「任意 `run.completed`」改为「仅 `terminal_state=released`」——M-IMPL 闭包边界的 `run.completed(terminal_state=boundary)` 不再被误认为收尾完成，released 终态可达、`close_milestone` 不再无限重发。
- **#179**：version gate 追加 file/key/expect 绑定执行（`bindings` 数组；`{release_version}` = 派生 tag 去一个前导 `v`；feature/post_release 旅程强制、dev 豁免；错配指认 file/key 并 fail-closed）。版本号设计期 bump 到 0.10.0（§2 已交付），发布期升版惯例由该门禁取代；pyproject bytes 变化的 registry digest 重锚随 §4.2 同包完成。
- **#180**：`precheck_hotfix_report` 链按 `GithubIssuesError.classification` 承接：`not_found`/确认缺失 → `issue_not_found`；`auth`/`rate_limit`/`network`/`missing_token` → `issue_fetch_failed` 携分类 next（ops 前置节/权限/限流等待/连通性与 TLS），REJECTED 可重试路径保持开启。`precheck_hotfix` 纯函数签名不变。
- **#181**：`effects/github.py` 全部 HTTPS 调用点显式 `ssl.create_default_context(cafile=certifi.where())`（`TRAC_GITHUB_CA_BUNDLE` 可覆盖）；live 环境前置节随 §2 落 docs/getting-started/installation.md。
- **#184**：`ensure_project_milestone`（list→create→readback、同标题幂等复用、绝不假报 api_verified）接入 M-MILESTONE 关闭链（首次接触点）；不可自动补建时 `attention.required(reason=milestone_not_found)` 携可操作 next（按模板标题手工建后 `trac run --resume`），保持 audited skip 形态。

### 1.0.7 LEX_REVIEW 合法 park（FR-0332）

kernel 纯性不破：评审出口（effects 层，可读文档）按 tracks/discuss 解析计算三分支——全 resolved → `pass`；未决线程且每个的裁决权属方为 Human → `pass-pending-human-threads`（携 `pending_threads`）；其余 → `revise` 不变。kernel 归约新 verdict 为 `status=awaiting_human` + `awaiting=review_pending_threads` + State 投影 pending 清单（事件可重放），run 合法 park 等人而非空转重试；`trac status` 渲染清单；Human 处理后 `trac review` 在该 awaiting 下的语义为恢复重评（重进 LEX_REVIEW），HUMAN_REVIEW 下既有语义不变。重进时派发物化面向 assignment 注入 `open_threads` 清单，Lex 在既有线程内续评（`tracks/agents/Lex.md` 纪律），findings 线程跨 attempt 复用不重复开。

### 1.0.8 测试节点两类纪律：合法 Red 验收锚点 vs 到达即绿守卫

M-TEST 复审发现并回卷的设计缺陷（Prism no-diff blocker，test-plan §8 线程 T-001）：原 §8 锚点归属句把全部 integration 节点一概声明为「可由 task 声明转绿的验收锚点」，但 5 个节点验证的是设计期/继承期已落地事实（声明存在性、静态纪律、缺席条件、继承行为回归），到达即绿、永不可能构造合法 Red——红窗必填集包含它们使 task 图不可满足（RED_CHECK 判 unexpected_pass → 误诊为 test_defect）。本版确立两类纪律（合同在 interfaces §1u / IF-GREENGUARD-001）：

- **合法 Red 验收锚点**：断言本版未实现产品行为；先红后绿；integration 项可由 task 声明转绿。
- **到达即绿守卫**：验证已落地事实；test-plan §8.1 清单逐节点声明（唯一权威）；从红窗必填集排除、不作 task 验收锚点；每次执行必须保持绿（守卫失败即真回归，fail-closed）。RED_CHECK 消费面（守卫 pass 记 `guard_verified`）为待实现 Devon 项（executor/test_execute.py）。

5 个守卫节点：AC-FR0325-02 的缺席条件、AC-FR0328-02 的设计期合同事实、AC-FR0329-01 的继承行为回归、AC-NFR0155-01 的静态纪律半边、AC-NFR0155-02 的设计期声明存在性（逐节点理由见 test-plan §8.1）。选窗纪律：合法红必填集 = r2 ∩ §8 合法 Red 锚点；守卫经 §8.1 豁免；操作者修复随带的回归测试经既有 OOB 通道（oob.accepted 记录文件）豁免——9d65be5 等流内修复的单测被卷入红窗是观察面缺口（Tracks-OOB trailer 已在提交上但未产生 oob.accepted 事件），属 Runtime 观察面修复事项而非设计变更；本次回滚重进 M-TEST 时基线按现行树重建，已提交增量经既有 r2_discharged 语义收口。

### 1.1 Composition Root

Web 服务装配入口继承 ARCH-009（cmd_serve → ServiceDB/recover → CommandService → Scheduler/WorkerManager → create_app → uvicorn），v0.10 在 create_app 的路由表追加 §2b #29-35 七个端点与 workbench 入口名字门：

```text
cmd_serve（tracks/cli/serve_cmd.py，既有）
  -> supervisor/db.py ServiceDB(home)（建表 + auth.display_name 列幂等演进，interfaces §1c）
  -> recover_on_startup -> CommandService -> Scheduler/WorkerManager（既有）
  -> server/app.create_app(home, service, supervisor, config)
       （_AuthBoundary 认证中间件不变；_AuthGlue 增 name/profile 面；
        api_query 增 docs tree/versioned read/diff/discussions 与 timeline stage_order；
        api_command 的 edits 409 增 current_revision；pages 两壳渲染；/static 挂载不变）
  -> uvicorn 运行（既有）
```

浏览器侧装配（工作台，随 workbench shell 到达）：

```text
GET <任意工作台入口> -> workbench shell（data-route） -> /static/app/shell.js（type=module）
  -> router.js 解析深链 -> tabs.js/sidebar.js 建立 chrome 状态（内存，SM-04）
  -> api.js（CSRF/Idempotency-Key/失败反馈）+ sse.js（event_cursor 补读）
  -> views（projects/runs/docs/review/todos/settings）经 §2b 只读投影渲染
  -> docs 视图：editor.js 按需注入 /static/vendor/vditor/**（失败 -> textarea 回退）
     + panes.js（≤4）+ discussions.js（只读投影导航）
  -> runs 详情：timeline.js 消费 timeline(+stage_order)/ac-chain
```

发布卫生链路（CLI/runner 面，既有入口不变）：

```text
trac run -> worker -> kernel.machine.decide -> executor
  -> M-VERIFY: verify_version._execute_version_decl_gate
       （tag 派生/远端缺席（既有）+ bindings 逐条 file/key/expect 比对（新））
  -> M-MILESTONE: milestone_chain._close_milestone_project
       （ensure_project_milestone（新）-> close_project_milestone（既有））
       -> _complete_milestone（终态感知守卫（新）-> run.completed(released)）
trac hotfix -> hotfix_face.precheck_hotfix_report
  -> fetch 分类承接（新）-> hotfix.precheck_hotfix（既有纯规则）/分类 rejected+next
LEX_REVIEW: machine_decide._decide_review -> dispatch 物化（注入 open_threads（新））
  -> Lex -> opencode_review 评审出口三分支（新）-> lex.verdict
  -> machine_verdicts 归约（pass-pending-human-threads -> AWAIT_HUMAN park（新））
  -> trac status 渲染清单（新）；trac review 恢复重评（新语义分支）
```

### 1.2 Required AC closure (ISLAND_GATE_1)

- **FR-0316** owner=tracks/server/pages.py+tracks/server/static/app/shell.js:AC-FR0316-01 surface=工作台入口页面（/、/projects、/runs/{id} 等深链） composition=create_app 页面路由→pages 两壳渲染；浏览器侧 shell.js 装配 router/tabs/sidebar/api/sse（§1.1） wiring=认证后经任一入口 URL 取得同一 workbench shell→shell.js 按 data-route 恢复功能区→切换/开闭/文档切换/保存均经 fetch+DOM 局部更新、document 实例延续 test=e2e:tests/e2e/test_workbench_ui.py::test_spa_shell_navigation_no_reload evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py`（ui-e2e required check 预装 Chromium）输出 passed 且断言全程同一 document、无整页刷新 IF-WORKBENCH-001 IF-WEBUI-001
- **FR-0316** owner=tracks/server/pages.py:AC-FR0316-02 surface=页面 HTML 与仓库文件集 composition=静态面零构建链（§3.1） wiring=shell 仅以 type=module 引导 /static/app/shell.js；仓库无 package.json/vite/webpack 配置与构建产物目录；guard registry 与既有 required checks 不受影响 test=integration:tests/integration/test_workbench_shell.py::test_no_build_chain_native_esm evidence=`.venv/bin/python -m pytest -q tests/integration/test_workbench_shell.py` 输出 passed 且扫描断言无第二工具链文件、资产全同源 IF-WORKBENCH-001
- **FR-0317** owner=tracks/server/pages.py+tracks/server/static/styles.css:AC-FR0317-01 surface=E-01 左侧 tab 条 composition=workbench shell 骨架的 tab 条区（data-testid=tabbar-item-*） wiring=渲染→图标+hover tooltip、命中区≥32px、顺序固定；拖拽尝试不改变顺序、刷新前后顺序不变 test=e2e:tests/e2e/test_workbench_ui.py::test_tabbar_fixed_semantics evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且 Playwright 量测命中区与顺序稳定性 IF-WORKBENCH-001 IF-WEBUI-001
- **FR-0318** owner=tracks/server/static/app/tabs.js+sidebar.js:AC-FR0318-01 surface=E-01 sidebar+main area composition=shell.js 装配 tabs/sidebar 状态机（SM-04） wiring=点击 tab 条项→同一次切换内 sidebar 换树且 main area 打开/激活对应 tab；重复点击激活唯一实例；tab 共存不被切换关闭；主动关闭是唯一关闭路径且无关闭所有动作 test=e2e:tests/e2e/test_workbench_ui.py::test_sidebar_tab_atomic_switch_and_coexistence evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且不存在只换 sidebar 不开 tab 的中间态 IF-WORKBENCH-001 IF-WEBUI-001
- **FR-0318** owner=tracks/server/static/app/tabs.js:AC-FR0318-02 surface=E-01 三区 composition=同上 wiring=刷新/重启浏览器后 tab 集合为空集合（预期行为）；sidebar 只显当前 tab 条项下级内容；main area 标题区显示内容标题而非 tab 条分类名 test=e2e:tests/e2e/test_workbench_ui.py::test_tabs_not_persisted_and_content_titles evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且刷新后 tab 数为零 IF-WORKBENCH-001 IF-WEBUI-001
- **FR-0318** owner=tracks/server/pages.py+tracks/server/app.py:AC-FR0318-03 surface=E-01 功能项集+Settings/Account composition=七项封闭集装配（Projects/Runs/Docs/Review/Todos/Settings/Account） wiring=齿轮项只开 Settings tab 不换 sidebar；Account 菜单仅 logout→POST /api/auth/logout→sessions 行删除且 cookie 清空→再访问工作台需重新登录 test=e2e:tests/e2e/test_workbench_ui.py::test_function_set_settings_account + integration:tests/integration/test_auth_name.py::test_logout_clears_session_and_cookie evidence=`.venv/bin/python -m pytest -q tests/integration/test_auth_name.py` 输出 passed 且 logout 后 sessions 表无该行 IF-WORKBENCH-001 IF-WEBAUTH-001
- **FR-0319** owner=tracks/server/static/app/sidebar.js+views:AC-FR0319-01 surface=E-01 三区（树节点/main 内容/tab 标签） composition=降级渲染规则装配于各视图 wiring=注入未知树节点/缺失标题/未知 tab 目标→对应位置渲染可读降级标记（NotFound/未知）→不崩溃、不 500、不留空白 tab、可继续导航 test=e2e:tests/e2e/test_workbench_ui.py::test_unknown_values_degrade_visibly evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且降级标记文本可定位 IF-WORKBENCH-001 IF-WEBUI-001
- **FR-0320** owner=tracks/server/pages.py+tracks/server/auth.py:AC-FR0320-01 surface=E-02 /login composition=双栏 auth shell + _AuthGlue.login wiring=登录页左 hero 图+标语+署名、右登录面板、错误行内、窄视口单栏；登录成功 Set-Cookie（HttpOnly/SameSite=Strict/持久 7 天滚动）；关闭重开浏览器（携带 cookie 的存储态）直进工作台真数据 test=e2e:tests/e2e/test_workbench_ui.py::test_login_dual_pane_and_persistent_session evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且新浏览器上下文凭持久 cookie 免登录 IF-AUTHNAME-001 IF-WEBAUTH-001 IF-WEBUI-001
- **FR-0320** owner=tracks/server/app.py+tracks/server/auth.py:AC-FR0320-02 surface=未认证访问面+POST /api/auth/logout composition=_AuthBoundary 中间件（既有） wiring=未认证访问工作台页面 302→/login、API 401；logout 删服务端会话行并清 cookie test=integration:tests/integration/test_auth_name.py::test_unauthenticated_redirect_and_logout_clears evidence=`.venv/bin/python -m pytest -q tests/integration/test_auth_name.py` 输出 passed 且 302/401 形态与 sessions 删除均可审计 IF-WEBAUTH-001
- **FR-0321** owner=tracks/server/auth.py+tracks/server/app.py:AC-FR0321-01 surface=E-02 名字采集步+POST /api/auth/name+事件 actor 出口 composition=auth.display_name 持久化+生效 actor 绑定（§1.0.3） wiring=首登且 display_name 未设→login 响应 name_required=true 且工作台入口 302 回 /login→提交名字→auth.name_bound 落 service_events→此后会话 actor=名字→Web 批准落 human.approval actor=名字、讨论 speaker=名字 test=integration:tests/integration/test_auth_name.py::test_name_binding_flows_to_events_and_discussion + e2e:tests/e2e/test_workbench_ui.py::test_first_login_name_collection evidence=`.venv/bin/python -m pytest -q tests/integration/test_auth_name.py` 输出 passed 且 tracks.db 的 human.approval 事件 actor 字段为采集的名字 IF-AUTHNAME-001
- **FR-0321** owner=tracks/server/auth.py+tracks/server/pages.py:AC-FR0321-02 surface=登录/账户面 composition=单用户边界不变（RP-01） wiring=无注册端点（/api/auth/register 等路径 404）；auth 表恒单行；名字仅身份标注不产生第二账号概念 test=integration:tests/integration/test_auth_name.py::test_no_registration_surface_single_identity evidence=`.venv/bin/python -m pytest -q tests/integration/test_auth_name.py` 输出 passed 且注册路径 404、auth 表行数为 1 IF-AUTHNAME-001 IF-WEBAUTH-001
- **FR-0322** owner=tracks/server/projections.py+tracks/server/api_query.py:AC-FR0322-01 surface=GET /api/projects/{pid}/docs/tree+E-03 composition=project_docs_tree 读模型（§1.0.4） wiring=目录树版本号逆序置顶→选择六件套任一文档→GET 版本化内容→Vditor ir 即时渲染且资产全同源 test=integration:tests/integration/test_docs_center.py::test_tree_version_desc_and_six_piece_read + e2e:tests/e2e/test_docs_ui.py::test_docs_center_vditor_ir_same_origin evidence=`.venv/bin/python -m pytest -q tests/integration/test_docs_center.py` 输出 passed 且树顺序为版本降序、六名可读 IF-DOCCENTER-001 IF-QUERY-001
- **FR-0322** owner=tracks/server/static/app/editor.js:AC-FR0322-02 surface=E-03 编辑器宿主 composition=Vditor 按需注入+回退分支 wiring=Playwright route 阻断 vendor 资产→编辑器回退 textarea→内容完整可查看可编辑、主路径不阻断 test=e2e:tests/e2e/test_docs_ui.py::test_vditor_failure_textarea_fallback evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_docs_ui.py` 输出 passed 且 textarea 可见可用 IF-DOCCENTER-001 IF-WEBUI-001
- **FR-0323** owner=tracks/server/api_command.py+tracks/supervisor/service.py:AC-FR0323-01 surface=保存控件+POST /api/projects/{pid}/docs/{version}/{doc}/edits（文档中心编辑面） composition=edit_material + revision_kind:"docs"（docs_revision token，§1.0.4；trio 摘要与 FR-0308 零触碰） wiring=未编辑保存禁用→编辑后可用→六件套显式保存经文档中心面以 docs_revision 为 base→新 docs_revision 落地（material.edited+Runtime 提交）→界面切新基线；trio 文档保存同时移动 trio 摘要→对旧 trio revision 的批准被 stale_revision 拒绝 test=integration:tests/integration/test_docs_center.py::test_save_produces_revision_and_stale_approval_rejected + e2e:tests/e2e/test_docs_ui.py::test_save_button_dirty_gating evidence=`.venv/bin/python -m pytest -q tests/integration/test_docs_center.py` 输出 passed 且设计文档保存后 docs_revision 前后不同、trio 保存后旧批准被拒 IF-DOCSAVE-001 IF-DOCREV-001 IF-WEBGATE-001
- **FR-0323** owner=tracks/server/api_command.py+tracks/server/static/app/editor.js:AC-FR0323-02 surface=409 冲突恢复面（两编辑面） composition=409 响应体顶层 current_revision（interfaces §1o.2 唯一权威形态：文档中心面携当前 docs_revision、运行域面携 trio 摘要） wiring=构造 revision/mtime 冲突→409 顶层携 current_revision→UI 提供重载放弃与强制覆盖两选项（不静默覆盖；强制覆盖以 current_revision 为新 base 重提）→写失败本地草稿保留可重试 test=integration:tests/integration/test_docs_center.py::test_conflict_409_two_options_and_draft_retained + e2e:tests/e2e/test_docs_ui.py::test_conflict_dialog_two_options evidence=`.venv/bin/python -m pytest -q tests/integration/test_docs_center.py` 输出 passed 且 409 顶层 current_revision 等于服务端当前 token、强制覆盖重提成功 IF-DOCSAVE-001
- **FR-0324** owner=tracks/server/static/app/panes.js:AC-FR0324-01 surface=E-03 分屏容器 composition=panes 模块装配（≤4 列） wiring=新增 pane→每 pane 独立文件选择器与工具栏→独立加载不同文档对照、选择与编辑互不干扰 test=e2e:tests/e2e/test_docs_ui.py::test_multi_pane_independent evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_docs_ui.py` 输出 passed 且两 pane 加载不同文档互不干扰 IF-DOCCENTER-001 IF-WEBUI-001
- **FR-0324** owner=tracks/server/static/app/panes.js:AC-FR0324-02 surface=E-03 分屏容器 composition=同上 wiring=请求第 5 列→不再新增 pane 且原有 pane 内容不受影响 test=e2e:tests/e2e/test_docs_ui.py::test_pane_cap_four evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_docs_ui.py` 输出 passed 且 pane 数保持 4 IF-DOCCENTER-001 IF-WEBUI-001
- **FR-0325** owner=tracks/server/projections.py:AC-FR0325-01 surface=GET /api/projects/{pid}/docs/{version}/{doc}/discussions+E-03 覆盖层 composition=project_doc_discussions（服务端 tracks/discuss parser） wiring=线程投影（status/initiator/awaiting/entry_line）→UI 显示/隐藏、下一个、只看未决；RESOLVED 仅发起人（既有裁决权属不变） test=integration:tests/integration/test_docs_center.py::test_discussions_read_model_and_nav_state + e2e:tests/e2e/test_docs_ui.py::test_discussion_navigation_controls evidence=`.venv/bin/python -m pytest -q tests/integration/test_docs_center.py` 输出 passed 且线程状态与服务端解析一致 IF-DISCUSS-001 IF-WEBAUTH-001
- **FR-0325** owner=tracks/server/app.py:AC-FR0325-02 surface=UI 面+API 面 composition=无写回端点（本版边界） wiring=UI 无 resolve/reply 控件；对讨论路径的写请求 404/405；写回仅经 trac discuss test=integration:tests/integration/test_docs_center.py::test_no_discussion_mutation_endpoint（到达即绿守卫：缺席条件，test-plan §8.1，不作 task 验收锚） + e2e:tests/e2e/test_docs_ui.py::test_no_discussion_write_controls evidence=`.venv/bin/python -m pytest -q tests/integration/test_docs_center.py` 输出 passed 且写入口不存在 IF-DISCUSS-001 IF-GREENGUARD-001
- **FR-0326** owner=tracks/server/api_query.py+tracks/server/static/app/timeline.js:AC-FR0326-01 surface=E-04 run 详情时间线视图+GET timeline/ac-chain composition=timeline 响应 stage_order 扩展+timeline.js 视图 wiring=顶部当前节点卡片（责任方/attempt/运行时长/唯一主要动作）+13 阶段线性时间线（attempt 独立节点不折叠、回拨回边、视口聚焦活跃节点邻域）+节点浮层（起止/artifact/revision 跳文档中心）；内容与接口数据一致 test=integration:tests/integration/test_run_timeline_api.py::test_stage_order_and_timeline_consistency + e2e:tests/e2e/test_run_timeline_ui.py::test_run_timeline_view evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_timeline_api.py` 输出 passed 且 stage_order 为 13 阶段序、节点数与 attempt 事件一致 IF-TIMELINE-001 IF-QUERY-001
- **FR-0326** owner=tracks/server/pages.py:AC-FR0326-02 surface=Runs 功能区 run 详情 composition=冻结裁定 2026-09-27 整包进 v0.10 wiring=run 详情提供时间线视图入口（卡片/时间线/浮层均可见），无延后分支残留形态 test=integration:tests/integration/test_run_timeline_api.py::test_timeline_entry_present evidence=`.venv/bin/python -m pytest -q tests/integration/test_run_timeline_api.py` 输出 passed 且时间线入口存在 IF-TIMELINE-001
- **FR-0327** owner=tracks/executor/milestone_chain.py:AC-FR0327-01 surface=M-MILESTONE 收尾命令面+run.completed 事件 composition=_complete_milestone 终态感知守卫（§1.0.6） wiring=事件日志含 run.completed(terminal_state=boundary) 时守卫不误判→收尾链照常落 run.completed(terminal_state=released, release_tag)→released 可达；已含 released 才幂等跳过；close_milestone 不无限重发 test=integration:tests/integration/test_milestone_complete.py::test_released_reachable_despite_boundary_completion evidence=`.venv/bin/python -m pytest -q tests/integration/test_milestone_complete.py` 输出 passed 且事件序恰含一个 released 终态完成、无重复 close 发放 IF-MILESTONE-002
- **FR-0328** owner=tracks/executor/verify_version.py+tracks/executor/host_contract.py:AC-FR0328-01 surface=version gate（M-VERIFY 本地门） composition=version_decl 门禁 bindings 执行（§1.0.6） wiring=声明 bindings→渲染 expect（release_version=派生 tag 去 v 前缀）→逐绑定读 file 提取 key 比对→三者一致放行；任一错配 local_gate.failed 并指认 file/key test=integration:tests/integration/test_version_gate.py::test_version_bindings_enforced evidence=`.venv/bin/python -m pytest -q tests/integration/test_version_gate.py` 输出 passed 且错配 payload 指认 pyproject.toml/project.version 或 tracks/__init__.py/__version__ IF-VERSION-001
- **FR-0328** owner=.tracks/projects/project.toml+pyproject.toml:AC-FR0328-02 surface=宿主合同+guard registry composition=version_scheme.bindings 声明+config_digest 重锚（§4.2，§2 已交付） wiring=合同加载→bindings 含 file/key/expect 两绑定；registry 六条 pyproject-backed 与 hooks-runner digest 与现网文件 bytes 一致（零失配）；版本号已 bump 到 0.10.0 test=integration:tests/integration/test_version_gate.py::test_contract_bindings_declared_and_registry_anchored（到达即绿守卫：设计期合同事实，test-plan §8.1，不作 task 验收锚） evidence=`.venv/bin/python -m pytest -q tests/integration/test_version_gate.py` 输出 passed 且 registry 加载零 digest 失配 IF-VERSION-001 IF-GUARD-001 IF-GUARD-002 IF-GREENGUARD-001
- **FR-0329** owner=tracks/executor/hotfix.py+tracks/executor/hotfix_face.py:AC-FR0329-01 surface=hotfix 前检（trac hotfix/服务端共用 precheck_hotfix_report） composition=分类承接（§1.0.6） wiring=不存在的 issue 号（404/确认缺失）→rejected(issue_not_found) 且 next 指引可操作 test=integration:tests/integration/test_hotfix_precheck_classification.py::test_missing_issue_reports_not_found_with_next（到达即绿守卫：继承 v0.9 行为回归，test-plan §8.1，不作 task 验收锚） evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_precheck_classification.py` 输出 passed 且 reason=issue_not_found、next 非空 IF-HOTFIX-011 IF-GREENGUARD-001
- **FR-0329** owner=tracks/executor/hotfix_face.py:AC-FR0329-02 surface=同上 composition=同上 wiring=注入 GithubIssuesError(auth/rate_limit/network/missing_token)→rejected(issue_fetch_failed)（不再吞成 issue_not_found）且按分类携恢复指引、重试路径开启（修复后重提通过） test=integration:tests/integration/test_hotfix_precheck_classification.py::test_fetch_failures_classified_with_retry_open evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_precheck_classification.py` 输出 passed 且四类分类各自映射正确 IF-HOTFIX-011
- **FR-0330** owner=tracks/effects/github.py:AC-FR0330-01 surface=live GitHub 请求层 composition=显式 SSL context（certifi.where()，TRAC_GITHUB_CA_BUNDLE 覆盖）装配进全部 HTTPS 调用点 wiring=本地 HTTPS stand-in（测试 CA 签发的证书）+TRAC_GITHUB_CA_BUNDLE 指向测试 CA→请求经真实 TLS 握手成功；默认 certifi 束下同一自签证书请求按 network 分类失败（对照） test=integration:tests/integration/test_github_tls.py::test_tls_channel_uses_certifi_bundle evidence=`.venv/bin/python -m pytest -q tests/integration/test_github_tls.py` 输出 passed 且两种结果对照成立 IF-TLS-001
- **FR-0330** owner=docs/getting-started/installation.md+tracks/effects/github.py:AC-FR0330-02 surface=ops 文档+分类错误出口 composition=「Live GitHub 旅程环境前置」节（§2 已交付） wiring=文档含 GITHUB_TOKEN/TRAC_GITHUB_REPO/TLS（含 TRAC_GITHUB_CA_BUNDLE）前置节；缺 GITHUB_TOKEN→missing_token 分类且指引指向该节；按指引修复后重试成功 test=integration:tests/integration/test_github_tls.py::test_ops_doc_section_and_missing_token_guidance evidence=`.venv/bin/python -m pytest -q tests/integration/test_github_tls.py` 输出 passed 且文档段落存在 IF-TLS-001
- **FR-0331** owner=tracks/effects/github.py+tracks/executor/milestone_chain.py:AC-FR0331-01 surface=tracker 首次接触点+close 面 composition=ensure_project_milestone 接入关闭链（§1.0.6） wiring=stand-in 远端无该 milestone→首次接触创建并经 API 回读（api_verified=true, created=true）→再次接触幂等复用不重复建→close 正常闭环 test=integration:tests/integration/test_milestone_ensure.py::test_ensure_milestone_create_reuse_readback evidence=`.venv/bin/python -m pytest -q tests/integration/test_milestone_ensure.py` 输出 passed 且远端恰有一个同标题 milestone IF-TRACKER-001
- **FR-0331** owner=tracks/executor/milestone_chain.py:AC-FR0331-02 surface=milestone_not_found 反馈出口（attention.required） composition=同上 wiring=远端缺失且无法自动补建（创建被拒/无凭据）→milestone_not_found 且 next 明确操作者前置建 milestone 义务（按模板标题手工建后 trac run --resume）；手工补建后重试成功 test=integration:tests/integration/test_milestone_ensure.py::test_milestone_not_found_actionable_next evidence=`.venv/bin/python -m pytest -q tests/integration/test_milestone_ensure.py` 输出 passed 且 attention.required 携带可操作 next IF-TRACKER-001
- **FR-0332** owner=tracks/effects/opencode_review.py+tracks/kernel/machine_verdicts.py+tracks/cli/status_cmd.py:AC-FR0332-01 surface=lex.verdict 词汇表+trac status composition=评审出口三分支+kernel park 归约（§1.0.7） wiring=仅余 Human 线程（全部未决线程裁决权属方为 Human）→lex.verdict(pass-pending-human-threads) 携 pending_threads→run park 到 AWAIT_HUMAN（awaiting=review_pending_threads）→trac status 显示待决线程清单→Human 处理后 trac review 恢复重评→线程已决转 pass test=integration:tests/integration/test_lex_review_park.py::test_pass_pending_human_threads_parks_and_lists evidence=`.venv/bin/python -m pytest -q tests/integration/test_lex_review_park.py` 输出 passed 且 status 输出含待决线程清单 IF-REVIEW-001
- **FR-0332** owner=tracks/effects/opencode_review.py+tracks/agents/Lex.md:AC-FR0332-02 surface=LEX_REVIEW 派发上下文+文档线程面 composition=open_threads 注入+线程复用纪律 wiring=重试循环重进 LEX_REVIEW→assignment 注入当前 open_threads 清单→Lex 在既有线程内续评→两轮后文档不出现同一 findings 的重复新线程且不空转 test=integration:tests/integration/test_lex_review_park.py::test_findings_threads_reused_across_attempts evidence=`.venv/bin/python -m pytest -q tests/integration/test_lex_review_park.py` 输出 passed 且第二轮文档线程数不增、派发上下文含 open_threads IF-REVIEW-001
- **NFR-0153** owner=tracks/server/static/app/api.js+views:AC-NFR0153-01 surface=全部数据面（总览/run 详情/文档/待办） composition=UI 只消费查询投影与事件流 wiring=抽查各数据面展示与服务端投影逐字段一致；无硬编码 mock 数据 test=integration:tests/integration/test_workbench_shell.py::test_no_mock_data_parity_with_projections + e2e:tests/e2e/test_workbench_ui.py::test_real_data_rendered evidence=`.venv/bin/python -m pytest -q tests/integration/test_workbench_shell.py` 输出 passed 且投影一致 IF-WORKBENCH-001 IF-QUERY-001
- **NFR-0153** owner=tracks/server/static/app/api.js:AC-NFR0153-02 surface=失败反馈出口 composition=api.js 统一错误面 wiring=注入 API 失败（Playwright route 注入 500/断网）→界面出现可观察失败反馈、不伪报成功；恢复后可继续操作 test=e2e:tests/e2e/test_workbench_ui.py::test_api_failure_visible_feedback evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且失败反馈可定位 IF-WORKBENCH-001 IF-WEBUI-001
- **NFR-0154** owner=tracks/server/static/app/sse.js:AC-NFR0154-01 surface=SSE 订阅与断线重连 composition=sse.js 游标补读（既有 IF-STREAM-001 的客户端遵守面） wiring=断线重连后按 event_cursor 补读；注入重复/乱序事件→UI 投影不倒退、单调一致 test=integration:tests/integration/test_event_stream.py（既有服务端面） + e2e:tests/e2e/test_workbench_ui.py::test_sse_reconnect_backfill_monotonic evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且重连后投影游标单调 IF-STREAM-001 IF-WORKBENCH-001
- **NFR-0154** owner=tracks/server/static/app/api.js:AC-NFR0154-02 surface=全部 UI 写操作 composition=api.js 统一注入 X-Trac-CSRF 与 Idempotency-Key wiring=UI 发起的写请求携带两头（Playwright 请求观测）；缺失/错误 CSRF 被 403 拒绝；秘密经 redaction 投影展示无明文 test=integration:tests/integration/test_workbench_shell.py::test_mutation_endpoints_reject_missing_csrf + e2e:tests/e2e/test_docs_ui.py::test_ui_writes_carry_csrf_idempotency evidence=`.venv/bin/python -m pytest -q tests/integration/test_workbench_shell.py` 输出 passed 且缺头请求被拒 IF-CMDSVC-001 IF-SECRECY-001 IF-WORKBENCH-001
- **NFR-0155** owner=tests/e2e（Shield 资产）:AC-NFR0155-01 surface=ui-e2e required check wiring=关键旅程（登录→真数据浏览→文档中心编辑产生新 revision）经 Playwright 真实浏览器完成；UI 测试控件定位全部经 data-testid、不以 API 请求替代关键 UI 操作（静态扫描 UI 测试源码佐证） test=e2e:tests/e2e/test_workbench_ui.py::test_first_demo_milestone_journey + integration:tests/integration/test_workbench_shell.py::test_ui_tests_bind_data_testid（到达即绿守卫：静态纪律扫描半边，test-plan §8.1，不作 task 验收锚；e2e 旅程半仍为终态锚点） evidence=`.venv/bin/python -m pytest -q -m ui tests/e2e/test_workbench_ui.py` 输出 passed 且旅程全程浏览器操作 IF-WEBUI-001 IF-GREENGUARD-001
- **NFR-0155** owner=.github/workflows/ci.yml+.tracks/projects/project.toml:AC-NFR0155-02 surface=CI 合同 composition=ui-e2e job 与 required_checks 绑定（§2 已交付） wiring=ci.yml 含 ui-e2e job（playwright install --with-deps chromium 后 -m ui）；host-contract required_checks 含 ui-e2e；浏览器下载与 job 分钟在本文 §4.3/§4.4 显式声明为独立基础设施预算 test=integration:tests/integration/test_workbench_shell.py::test_ui_e2e_infra_declared（到达即绿守卫：设计期声明存在性，test-plan §8.1，不作 task 验收锚） evidence=`.venv/bin/python -m pytest -q tests/integration/test_workbench_shell.py` 输出 passed 且两处声明一致 IF-WEBUI-001 IF-GREENGUARD-001
- **NFR-0156** owner=pyproject.toml+tracks/server/pages.py:AC-NFR0156-01 surface=仓库文件集+页面 HTML composition=零构建链（§3.1） wiring=无 npm/Vite 构建链产物；页面 script 均 type=module 且同源；guard registry 与 CI required checks 门禁通过 test=integration:tests/integration/test_workbench_shell.py::test_no_build_chain_native_esm evidence=`.venv/bin/python -m pytest -q tests/integration/test_workbench_shell.py` 输出 passed 且无第二工具链产物 IF-WORKBENCH-001

## 2. Scaffold 宣言

本节是 M-DESIGN 阶段在宿主项目创建/修改文件的唯一合同。以下文件已随本设计一并物理交付（全部为既有文件的声明性/配置性修订，无业务行为）；此外不创建任何文件（`tests/ground_truth/` 不适用——test-plan §3 判定）。

- `pyproject.toml` — version 0.10.0、运行时依赖追加 certifi==2026.7.22、dev 依赖追加 playwright==1.63.0、`ui` marker 与 addopts 排除、`server/static/app/*` package-data（kind: config；bytes 变化已同步 §4.2 六条 pyproject-backed config_digest）
- `tracks/__init__.py` — `__version__ = "0.10.0"`（kind: config；与 pyproject 同步 bump，version gate 绑定对象）
- `.tracks/projects/project.toml` — `[host-contract.version_scheme]` 追加 bindings 两绑定、`[host-contract.ci].required_checks` 追加 ui-e2e（kind: config；bytes 变化已同步 §4.2 hooks-runner config_digest；该路径本即随设计三文档由 Runtime 提交机制一并暂存）
- `.github/workflows/ci.yml` — 新增 `ui-e2e` required job（Chromium 安装 + `-m ui`）、browserless job 的 `-m` 选择器追加 `and not ui`、`release-evidence` needs 追加 ui-e2e、头部注释同步（kind: ci-skeleton）
- `.github/workflows/nightly.yml` — 全量回归的 `-m` 选择器追加 `and not ui`（kind: ci-skeleton）
- `docs/getting-started/installation.md` — 追加「Live GitHub 旅程环境前置」节（GITHUB_TOKEN/TRAC_GITHUB_REPO/TLS 与 TRAC_GITHUB_CA_BUNDLE、分类诊断指引），并修正相邻一句关于缺 token 静默 fake 降级的过时描述（kind: data；spec FR-0330 指定的 ops 文档面，属定点前置节而非 docs/ 全面补写——docs/ 补写整体仍排 v0.11）

本节以外不创建 scaffold。§1.0.1 表中的 `tracks/server/static/app/*.js`、`tracks/server/static/styles.css` 与各 Python 模块的行为体/修订均为**待实现 Devon 任务**；`tests/integration/test_workbench_shell.py`、`test_auth_name.py`、`test_docs_center.py`、`test_run_timeline_api.py`、`test_milestone_complete.py`、`test_version_gate.py`、`test_hotfix_precheck_classification.py`、`test_github_tls.py`、`test_milestone_ensure.py`、`test_lex_review_park.py` 与 `tests/e2e/test_workbench_ui.py`、`test_docs_ui.py`、`test_run_timeline_ui.py` 及 Playwright fixture（tests/_support）均为**待实现 Shield 任务**；本文不得把它们当作既有可执行能力。

**本版无接口桩**：v0.10 不新增 Python 模块——全部变更落在 v0.9 已交付且可 import 的既有模块上（pages/auth/app/api_*/projections/milestone_chain/github/hotfix*/host_contract/verify_version/machine_verdicts/machine/status_cmd），Shield 的契约测试在 Devon 实现前即可加载这些模块，合法 Red 来自缺失行为而非缺失模块；v0.9 的桩先例（全新包）不适用于修订面。

## 3. 技术选型

### 3.1 原生 ES modules，不引入构建链（NFR-0156 条件未触发）

SPA 交互模型是硬需求（FR-0316），构建链不是。本设计以原生 ES modules 交付应用层：shell HTML 以 `<script type="module">` 引导 `/static/app/shell.js`，模块依赖经浏览器原生 import 解析，资产全部同源 `/static/**`；无 package.json、无 Vite/npm、无打包产物入仓。取舍：模块图需手工保持平坦（约十余个小模块，单页工作台规模内可控），换取与 guard-registry/CI required checks 体系的零冲击——louke 否决构建链的理由（避免第二工具链冲击门禁）在本宿主同样成立，NFR-0156 的「确需构建链则同包给出门禁集成方案」分支因此不触发。代价：无 TypeScript/打包优化，交互细腻度上限低于框架 SPA；换来部署形态（wheel package-data）与可审计性与 v0.9 完全一致。

### 3.2 certifi==2026.7.22 进入运行时依赖（FR-0330）

标准 macOS Python（python.org 构建）不带系统 CA 集成，urllib 默认 SSL context 在校验 api.github.com 时失败——这是 live 通道在本机不可用而 gh/git 正常的根因。修复为请求层显式 `ssl.create_default_context(cafile=certifi.where())`，certifi 因此成为第三个运行时依赖（pinned，与 starlette/uvicorn 同纪律）。`TRAC_GITHUB_CA_BUNDLE` 提供企业代理/自签 CA 的显式覆盖。代价：多一个 pinned 依赖（纯数据包，供应链风险低）；换来 live 通道在标准安装下可用且可诊断。

### 3.3 Playwright==1.63.0（dev 依赖）+ ui marker 通道隔离（NFR-0155）

UI e2e 需要真实浏览器。取 Playwright Python 1.63.0（当前最新稳定线），只用其 sync API——不引入 pytest-playwright 插件（其 fixture 约定与本项目既有 fixture 风格差异大，少一层魔法多一分可读性；浏览器/页面 fixture 由 Shield 在 tests/_support 手写）。通道隔离经 `ui` marker：pyproject addopts 与各 browserless CI job 的显式 `-m` 排除 ui（测试执行合同命令串因此逐字不变）；新 required check `ui-e2e` 安装 Chromium 后以 `-m ui` 运行。浏览器二进制不进仓库（`playwright install` 下载到 CI/本地缓存），其下载与 job 分钟为独立基础设施预算（AC-NFR0155-02 的声明面在 §4.3/§4.4 与 test-plan §2.5/§6）。不选 Selenium/自研 CDP：Playwright 是当前 louke 谱系验证过的选型（调研文档 D6），且自带 Chromium 发行与等待语义。

### 3.4 服务端最小新增面

文档中心的新增端点为 project 域读投影（tree/read/diff/discussions，只读）+ 一个编辑面（#35，写经命令服务 `edit_material` + `revision_kind`，§1o.1b）——写不经 HTTP handler 直达 runtime，web 层不直接耦合 runtime、HTTP 改动经命令服务的约束原样保持。discussion 协议解析只在服务端（tracks/discuss parser 复用），客户端消费线程投影——避免在 JS 里复制协议语法（与 FR-0325「不手写协议拼接」同源纪律）。`stage_order` 由服务端从 kernel 事实表组合后下发，客户端不硬编码 13 阶段集合（server 读 kernel 的只读事实表是既有先例：projections 已 import `tracks.kernel.machine.project`）。

### 3.5 service.db 最小 schema 演进

`auth` 表追加 `display_name TEXT` 可空列：新建库经 `CREATE TABLE IF NOT EXISTS` 直接含列；既有库启动期 `PRAGMA table_info` 探测后幂等 `ALTER TABLE ADD COLUMN`。不引入迁移框架（v0.9 先例），tracks.db schema 不动。

### 3.6 不引入的运行时机制

不引入 WebSocket（SSE 既有合同足够）、不引入前端路由库/状态库（原生 History pushState + 内存态）、不引入讨论 mutate 端点（FR-0325 边界）、不引入注册/多账号任何构件（RP-01）。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

三层合同（`[unit]`/`[integration]`/`[e2e]`）的 framework/paths/collect/run/run_selected/cwd 逐字继承 v0.9，命令串不变。`ui` marker 的默认排除由 pyproject `[tool.pytest.ini_options].addopts`（`-q -m 'not performance and not ui'`）承载——合同命令不带 `-m`，自动继承 addopts；命令行显式 `-m` 覆盖 addopts（ui-e2e job 的 `-m ui` 依此生效）。`[nightly]`/`[adapter]`/`[layout]`/`[lint]` 段不变；`[host-contract.*]` 段仅 §0.1 #7/#12 两处追加（bindings 键与 required_checks 成员）。本文件 bytes 已变（§2 已交付），hooks-runner 的 config_digest 随 §4.2 重锚。

### 4.2 Canonical quality guard registry（v0.10）

以下 TOML block 是 tracks 宿主 guard registry 的 v0.10 canonical 真相（承接 ARCH-009 §4.2）。本版输入变更二处：`pyproject.toml`（certifi/playwright 依赖、ui marker/addopts、package-data、version 0.10.0）与 `.tracks/projects/project.toml`（version bindings、required_checks 追加 ui-e2e）；六条 pyproject-backed config_digest 重锚为 `ee98aae6…`，hooks-runner 的 project.toml digest 重锚为 `30f157b9…`；`.flake8` bytes 未变，cognitive-complexity digest 不变。字段语义、digest 公式（单文件 sha256(raw bytes)）、八类强制、fail_closed、禁 `--exit-zero` 全部继承，不再重复。

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
config_digest = "sha256:ee98aae608431dc34bc69872b6d00fddfe95a75d7aec1267be6d880b391833e7"
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
config_digest = "sha256:ee98aae608431dc34bc69872b6d00fddfe95a75d7aec1267be6d880b391833e7"
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
config_digest = "sha256:ee98aae608431dc34bc69872b6d00fddfe95a75d7aec1267be6d880b391833e7"
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
config_digest = "sha256:ee98aae608431dc34bc69872b6d00fddfe95a75d7aec1267be6d880b391833e7"
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
config_digest = "sha256:ee98aae608431dc34bc69872b6d00fddfe95a75d7aec1267be6d880b391833e7"
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
config_digest = "sha256:ee98aae608431dc34bc69872b6d00fddfe95a75d7aec1267be6d880b391833e7"
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
config_digest = "sha256:30f157b9945856397876b0b897684b6642492a79fba30c3b8beda21d82636781"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test,deliverables,trace,reach,ui-e2e; milestone=release-evidence"
timeout_seconds = 3600
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test,deliverables,trace,reach,ui-e2e"
```

### 4.3 CI / pre-commit / release

- Stable required checks 追加一个成员：`ui-e2e`（job 名即 check 名；NFR-0155）。既有六名（lint/coverage/test/deliverables/trace/reach）不变；`release-evidence` milestone 硬门禁的 needs 同步追加 ui-e2e。browserless 三个 job（coverage/test/nightly）的显式 `-m` 追加 `and not ui`（命令行 `-m` 覆盖 addopts，必须显式排除）；`ui-e2e` job：venv 安装 `.[dev]` → 物化 .opencode 部署面（与 test job 同序）→ `playwright install --with-deps chromium`（浏览器下载，独立基础设施预算）→ `pytest tests/e2e -m ui`。
- pre-commit hook 不变（只跑守卫，不跑测试）；新增源码（tracks/server 修订、tracks/executor 修订等）自动纳入既有守卫 scope（registry scope 为 `tracks`）。`tracks/server/static/app/**` 是 JavaScript——守卫栈无 JS  linter（显式设计决定：JS 层靠 UI e2e 与评审承载质量，不引入 Node 工具链，与 §3.1 一致）；`*.js` 不在 ruff/flake8/pylint scope 内，文件长度守卫（C0302）按既有 scope（tracks、tests 的 Python 文件）不适用 JS——此空缺为显式记录而非静默留空。
- 安装命令（副作用归 Runtime）不变：`python -m venv .venv`、`.venv/bin/pip install -e '.[dev]'`、`git config core.hooksPath .githooks`；certifi/playwright 随依赖声明进入 venv；浏览器二进制仅在需要 UI 层时经 `playwright install chromium` 准备（ui-e2e job 与本地自选）。
- version gate 的 bindings 声明（interfaces §1r.2.3）：`pyproject.toml:project.version` 与 `tracks/__init__.py:__version__` 均绑定 `{release_version}`。版本号升版纪律本版起变更：**设计期 bump**（本设计已交付 0.10.0），既有「发布期 release commit 统一升版」惯例由 version gate 的绑定比对取代——M-VERIFY 之前文件版本必须等于目标发布版本，错配即 `version_decl_mismatch` 并指认 file/key。
- `release-evidence` milestone 硬门禁继承不变；live 通道（live-opencode weekly/manual + release-evidence）继承不变，本版修复其 TLS 可用性（FR-0330）。

### 4.4 Integration/e2e 基础设施

- Shield integration 新资产落 `tests/integration/`（既有 Shield 写域）：`test_workbench_shell.py`（壳合同/无构建链/真数据一致性/CSRF 拒绝面/ui-e2e 声明面）、`test_auth_name.py`（双栏壳/名字采集/端到端绑定/无注册面/logout）、`test_docs_center.py`（树/读/编辑/409/discussions 投影）、`test_run_timeline_api.py`（stage_order 与时间线一致性/入口存在）、`test_milestone_complete.py`（released 可达）、`test_version_gate.py`（绑定执行与 registry 重锚）、`test_hotfix_precheck_classification.py`（分类映射）、`test_github_tls.py`（TLS 通道）、`test_milestone_ensure.py`（ensure/缺失指引）、`test_lex_review_park.py`（park/恢复/线程复用）。
- e2e 新资产落 `tests/e2e/`（既有写域），浏览器层标 `@pytest.mark.ui`：`test_workbench_ui.py`（首个可演示里程碑旅程 + chrome 语义 + SSE/失败反馈）、`test_docs_ui.py`（编辑器/保存/409/分屏/discussion 导航/写安全头）、`test_run_timeline_ui.py`（时间线视图）。`tests/_support/` 追加 Playwright 浏览器 fixture（Shield；chromium 启动、上下文隔离、storage_state 复用以模拟浏览器重启）。
- **serve fixture 继承 v0.9**（真实子进程、临时 home/项目仓、随机端口、口令供给）；UI 层在其上叠 Playwright 驱动真实浏览器，不经 TestClient/进程内 app。
- fault 注入经公开合同面：API 失败经 Playwright route 拦截、Vditor 失败经 vendor 资产阻断、SSE 断线经浏览器上下文断连、409 经服务端 revision 构造、TLS 经本地 HTTPS stand-in + 测试 CA（`TRAC_GITHUB_CA_BUNDLE` 指向 `tests/assets/v0.10/` 的测试 CA bundle；对照面为默认 certifi 束下同一自签证书的分类失败）、GithubIssuesError 分类经既有 stand-in/异常注入、milestone ensure 经 stand-in GitHub（`TRAC_GITHUB_API_BASE`）。
- 测试数据：`tests/assets/v0.10/`（Shield 写域）固化测试 CA/证书、未知枚举投影样本、多版本文档树夹具种子；canary 密钥纪律继承 v0.9。
- deterministic suite 默认离线：serve/worker/SSE/stand-in 全在 loopback；UI 层 Chromium 为本地二进制（无网络）；Vditor 资产同源（UI 断言无跨域请求）。
- RED_CHECK 消费面（**待实现 Devon**）：r2 选窗内属 test-plan §8.1 守卫清单的节点通过时记 `guard_verified`（合法）、失败/缺席 fail-closed；未声明节点的到达即绿维持 unexpected_pass 语义（interfaces §1u / IF-GREENGUARD-001）。守卫测试资产已随 M-TEST WRITE 交付（commit 9a36bd2），其分类身份由本设计补记。

### 4.5 Build / artifact

build backend 与 wheel 名不变；package-data 追加 `server/static/app/*`（§2 已交付）；M-VERIFY build gate（`pip wheel --no-deps`）与 smoke（隔离 prefix 安装 + `--help`）不变。版本字段已随本设计升至 0.10.0（§4.3 升版纪律）；wheel 产物名将自报 0.10.0，与 v0.10.0 tag 经 version gate 绑定比对。

### 4.6 发布恢复

服务面恢复合同继承 v0.9（recover_on_startup/lease 过期重认领/waits 保留）。本版追加两处可恢复性事实：milestone 关闭链的 ensure 步骤幂等（同标题复用，崩溃重试不重复建、不假报 verified）；hotfix 前检的 `issue_fetch_failed` 保持 REJECTED 可重试（修复环境后重提）。`run.completed(released)` 的终态感知守卫使 M-MILESTONE 收尾在含边界伪完成的日志上可重入且恰好完成一次。

## 5. 有意识简化与风险

### 5.1 有意识简化

- PAGES 8 名封闭集与 URL 不变、渲染收敛为两壳：保留深链与既有注册表语义，把 SPA 化限制在渲染层；登录页保持服务端渲染 + 少量客户端步进（密码步/名字步），不进 workbench shell。
- 文档中心编辑只绑定「有非终态 run 的版本」（tree 的 `editable_run_id`）；历史版本只读——编辑通道复用 edit_material 的代价。
- Settings tab 只放最小内容（关于/版本显示），完整三页明确排除（spec 范围排除）；Account 菜单仅 logout。
- run 时间线的「唯一主要动作」由客户端从既有投影字段组合（control_state/todos/wait），不新增服务端动作推导。
- 名字不可改（UI 无改名入口；`POST /api/auth/name` 技术上幂等可重提，但 UI 仅在未采集时呈现）；口令轮换维持 v0.9 简化。
- UI e2e 只覆盖 happy path 与关键韧性（Vditor 回退/SSE 重连/失败反馈/降级）；边界矩阵留在 integration（§8 分层纪律）。
- 无 JS linter/format 守卫（§4.3 显式记录的空缺）。

### 5.2 风险

- **v0.9 遗留 unit 断言迁移**：`tests/unit/test_t010_page_shell_vditor_red.py` 断言 review 页内嵌 Vditor 资产标签；本设计改为 docs 视图按需注入（FR-0322 回退路径的前提）。该断言的合同意图（资产同源）由 integration 的同源断言与 UI e2e 承接，Devon 在 RGR 中按新合同更新该 unit 文件（属实现期合同适配，不是测试迁就实现——新合同即本设计）。
- **ui-e2e 的自举窗口**：ui-e2e required check 随本设计生效，首个 `ui` 标记测试由 M-TEST 的 Shield WRITE 交付；两者之间推送的分支上该 job 因零收集而 fail-closed（诚实红，非假绿）。v0.10 的发布点（M-PUBLISH）在 M-TEST 之后，届时非空；该排序写入本记录供评审知情。
- **Chromium 下载的 CI 波动**：浏览器下载走 Playwright CDN（独立预算的组成部分）；失败形态为 job 红而非套件假绿。若后续需缓存/镜像，作为 CI 改进单独立项，不进本版范围。
- **version gate 绑定与未来旅程**：post_release 旅程（v0.10.1 等）要求文件版本先升到补丁号——这是 FR-0329/FR-0328 想要的行为（门禁指认 file/key）；操作指引随 mismatch 的 detail 给出（升版后重试）。
- **`auth` 表演进的老库**：既有 service.db 经幂等 ALTER 增列；演进失败（只读库）落在 serve 启动健康面（healthz 503），不产生半迁移状态。
- **Lex park 的滥用面**：`pass-pending-human-threads` 仅在「全部未决线程的裁决权属方为 Human」时产生（服务端按 tracks/discuss 解析计算，不信 agent 自报）；一个非 Human 待决线程即回落 revise——机器判定，无自报通道。
- **OOB 观察面缺口（操作记录，非本版设计变更）**：9d65be5 等流内修复提交携带 Tracks-OOB trailer 但未产生 oob.accepted 事件（run 处于升级/停放窗口时观察器未覆盖），其随带单测被 r2 选窗卷入——doctrine 与通道已在 §1.0.8/interfaces §1u 钉死（守卫 §8.1 豁免 + OOB 文件豁免），观察面缺口归 Runtime 修复事项；本次回滚重进 M-TEST 时基线重建 + 既有 r2_discharged 语义收口，不遗留红窗污染。
- **discuss 门禁组合死锁（FR-090 ∧ FR-0314.4，本版评审通道的活缺陷）**：当线程发起者 ≠ 根评论唯一 @mention 的请求裁决方时，`trac discuss set-status` 无解——CLI 面要求 operator == 裁决权属方（IF-009 §1f.4），writer 面要求 operator == 发起者（FR-090），两规则合取无任何 operator 可满足（本 run 的 T-001 已由 Prism/Archer 双侧实证）。两个后果：(a) 本设计自身交付管道被它阻断——M-DESIGN EXIT 的 discussion_ready 在 T-001 上 fail-closed，直至 Human 经 IDE 手改状态标记（协议特许通道）或 Runtime 修正组合（建议语义：裁决权属方存在时优先于发起者规则）；(b) 对本版设计的直接依赖警示——FR-0332 的 park-resume 流依赖讨论线程的可关闭性（Lex 发起、@Human 裁决的线程在收敛后恰落入同一死锁形态），该组合修复是 IF-REVIEW-001 闭环的前置条件，归 Runtime/Devon 修复事项而非本文档吸收。
