---
envelope: tracks-envelope:v2
interfaces_id: IF-010
spec_ref: SPEC-010
arch_ref: ARCH-010
created: 2026-09-27
status: draft
sha:
---

# v0.10 — 接口与类型化 Schema：Web 工作台完善（UI）+ 发布卫生

本文是 IF-009 的增量延伸：v0.1～v0.9 的事件溯源、封闭事件集、命令 WAL、审批 revision 绑定、release preview/三择一、escape barrier、发布幂等/reconcile、guard registry、CLI 合同与 v0.9 服务面合同（HTTP API、持久命令服务、supervisor 驱动/租约/等待、只读投影、事件订阅、材料审阅）全部继承；v0.10 新增的是**工作台交互合同**（SPA chrome、登录名字绑定、文档中心应用层、run 时间线投影扩展）与**发布卫生合同**（milestone 收尾终态感知、版本声明绑定门禁、hotfix 前检分类、live 通道 TLS、tracker milestone 生命周期、LEX_REVIEW 合法 park 出口）。本文只写外部可观察契约；模块内部组织见 architecture.md。

## 0. 延续性（什么不变）

- IF-001/003/004/005/006/007/008/009 的全部合同逐字继承；既有 IF 标识不可重定义或复用，IF-010 只追加新标识（§5）。
- **tracks.db 的 EVENT_TYPES 封闭集本版不追加任何成员**；v0.10 唯一新事件类型落在服务面 `service_events` 追加只读日志（§1a 追加成员 `auth.name_bound`），run 面状态机与 kernel reducer 输入集对事件类型零改动。
- kernel 的 `COMMAND_KINDS` 封闭集不变；v0.9 服务命令封闭集（IF-009 §1b 十三种 kind）不变——名字绑定是认证面操作（§1m），不是域命令；文档编辑复用既有 `edit_material`。
- `.tracks/projects/project.toml` 的 `[unit]/[integration]/[e2e]` 三层测试执行合同的 framework/paths/collect/run/run_selected/cwd 语义不变（`ui` marker 经 pyproject addopts 默认排除，合同命令串不变）；`[host-contract.*]` 段的 table 封闭集不变，本版仅在既有 `[host-contract.version_scheme]` 表内追加 `bindings` 键并在 `[host-contract.ci].required_checks` 列表追加 `ui-e2e` 成员（§1r.2/§1t）。
- 既有 CLI 子命令集合与 USAGE 不变；本版不新增顶层子命令。`trac status` 的输出面追加一种 awaiting 渲染（§1s.4），`trac review` 获得一种新前置状态语义（§1s.4），均为既有命令的语义扩展而非新命令。
- `prism.verdict`、`human.approval`、`human.return`、`release.decided/rejected`、`publish.*`、`escape.*`、`material.edited` 等既有事件的类型与 payload 语义不因本版改变；`material.edited` 仅 `doc` 字段枚举追加成员（§1n.4）。
- SSE 合并序（(ts, source_rank, seq)）、游标补读、轮询回退、CSRF + Idempotency-Key、redaction 全出口脱敏、本机单用户边界（RP-01）全部继承；UI 客户端遵守同一契约（§1l.6）。
- 发布闭环五阶段、candidate 主身份、preview digest 绑定、escape barrier、guard registry 机制、`release-evidence` milestone 硬门禁全部继承；version gate 的 tag 派生/远端缺席/普查语义不变，本版追加文件绑定比对（§1r.2）。

### 0.1 v0.10 合同变更清单（显式声明）

| # | 变更 | 旧合同（v0.9） | 新合同（v0.10） | 理由 |
|:--|:--|:--|:--|:--|
| 1 | 页面壳收敛 | `PAGES` 8 名各渲染独立整页壳（IF-009 §2b 页面段） | `PAGES` 8 名封闭集与 URL 不变；login 渲染双栏 auth shell，其余 7 名渲染同一 workbench shell 并携带 `data-route` 深链描述（§1l.1） | FR-0316/FR-0320：SPA 交互模型与 louke 双栏登录 |
| 2 | HTTP API 追加 | IF-009 §2b 28 端点 | 追加 §2b #29-35（认证面 2 个 + 文档中心 project 域读 4 个 + 编辑面 1 个，#35——见行 15）；#1 login 响应增 `name_required`；#17 收敛 trio 域且 409 响应体顶层增 `current_revision`；#11 timeline 响应增 `stage_order` | FR-0320/0321/0322/0323/0325/0326 |
| 3 | docs `doc` 参数封闭集扩展 | `story/spec/acceptance/design`（IF-009 §2b #15-17） | 追加 `architecture`/`interfaces`/`test-plan`；`design` 保留为 `architecture` 的别名 | FR-0322 六件套 |
| 4 | service_events 封闭集追加 | IF-009 §1a 24 成员 | 追加 `auth.name_bound`（§1a 本版表 #25） | FR-0321 审计 |
| 5 | `lex.verdict` verdict 词汇追加 | `pass`/`comment`（revise 语义） | 追加 `pass-pending-human-threads`（§1s） | FR-0332（#183） |
| 6 | `awaiting` 封闭集追加 | triage/review/escalation/approval/hotfix_triage 等既有取值 | 追加 `review_pending_threads`（§1s.4） | FR-0332 |
| 7 | version gate 绑定 | version_scheme 仅三 tag 模板；file/key/expect 可解析未声明未执行（IF-008 §1e） | version_scheme 追加 `bindings`（file/key/expect 数组）；version gate 对 feature/post_release 旅程强制执行比对（§1r.2） | FR-0328（#179） |
| 8 | GitHub 请求层 TLS | 默认 SSL context（urllib 隐式） | 显式 `ssl.create_default_context(cafile=certifi.where())`，`TRAC_GITHUB_CA_BUNDLE` 可覆盖（§1r.4） | FR-0330（#181） |
| 9 | tracker milestone 生命周期 | 只 close 不 create（`close_project_milestone`） | 追加 `ensure_project_milestone`（list→create→readback，幂等复用；不可建报 `milestone_not_found` 携可操作 next）（§1r.5） | FR-0331（#184） |
| 10 | milestone 收尾幂等守卫 | `any(run.completed)` 即跳过 | 仅 `run.completed(terminal_state=released)` 跳过；`terminal_state=boundary` 不算完成（§1r.1） | FR-0327（#182） |
| 11 | hotfix 前检分类 | 一切 `GithubIssuesError` 吞成 `issue_not_found`（`issue_fetch_failed` 不可达） | 按分类映射：`not_found`→`issue_not_found`；`auth`/`rate_limit`/`network`/`missing_token`→`issue_fetch_failed` 携分类 next（§1r.3） | FR-0329（#180） |
| 12 | CI required checks | lint/coverage/test/deliverables/trace/reach | 追加 `ui-e2e`（Playwright/Chromium，独立基础设施预算，NFR-0155） | §1t |
| 13 | pytest marker | performance/integration/e2e | 追加 `ui`（默认套件经 addopts 排除；ui-e2e job 经 `-m ui` 选择） | §1t |
| 14 | 测试节点分类与红窗守卫通道 | 红窗对全部 r2 节点一律要求合法 Red（OOB 文件豁免除外） | 版本测试节点分两类（合法 Red 验收锚点 / 到达即绿守卫）；test-plan §8.1 声明守卫集合（唯一权威）；RED_CHECK 对守卫节点 pass 记 `guard_verified`、失败/缺席 fail-closed；守卫不作为 task 验收锚点声明（§1u） | §1u（M-TEST no-diff 复审 blocker 修订，2026-09-28） |
| 15 | 文档中心修订身份与编辑面分家 | v0.9 单一编辑面（#17），revision 一律 trio 摘要（`baseline.revision_digest` 只覆盖 story/spec/acceptance） | 新增 **docs_revision**（六件套摘要，`supervisor/service.py` 纯函数；`tracks/baseline.py` 与 FR-0308 批准绑定零改动）；文档中心编辑走新面 #35（token=docs_revision，全六件）；#17 收敛为 trio 域（design 别名编辑→422——v0.9 该面在设计文档上结构性不可满足，缺陷面显式关闭）；edit_material params 追加可选 `revision_kind:"docs"\|"trio"`（缺省 trio）；409 `current_revision` 钉死为响应体**顶层**字段（此前 §1o.2 措辞两读） | §1o/§1n（M-IMPL T-004 DIAGNOSE spec_gap 修订，2026-10-01） |

## 1. 跨模块合同

### 1a. 服务面事件封闭集（service_events）追加成员

IF-009 §1a 的 24 成员逐字继承；v0.10 追加一个成员（append-only、seq 单调、payload JSON 的既有语义不变）：

| # | event | payload（关键字段） | producer | modules |
|:--|:--|:--|:--|:--|
| 25 | `auth.name_bound` | `actor: str`（生效显示名）, `surface: "http"` | 认证面 | tracks/server/auth, tracks/server/app, tracks/supervisor/db |

### 1b. 服务命令封闭集（ServiceCommandKind）

不变（IF-009 §1b 十三种 kind 逐字继承）。名字绑定（§1m）经认证面端点，不进入命令服务；文档编辑复用既有 `edit_material`——其 `doc` 参数域随 §0.1 #3 扩展为六名，params 追加可选 `revision_kind ∈ {"trio", "docs"}`（缺省 `trio`，§1o.1b；封闭枚举，缺省语义与 v0.9 逐字一致）。

### 1c. service.db 存储合同演进

IF-009 §1c 八表继承；v0.10 唯一 schema 演进：

| # | table | 变更 |
|:--|:--|:--|
| 6 | `auth` | 追加可空列 `display_name TEXT`（缺省 NULL = 未采集）。`CREATE TABLE IF NOT EXISTS` 新建库直接含该列；既有库经启动期 `ALTER TABLE ... ADD COLUMN`（幂等：先 `PRAGMA table_info(auth)` 探测）就地演进。单行语义不变（RP-01 本机单用户） |

### 1l. 工作台 shell 与静态面合同（IF-WORKBENCH-001）

**modules**：`tracks/server/pages.py`、`tracks/server/app.py` 实现服务端壳；`tracks/server/static/app/**`、`tracks/server/static/styles.css` 为浏览器侧实现产物（Devon，路径本合同预留）；`tracks/server/projections.py`/`api_query.py`/`api_events.py` 供给数据。

1. **页面两壳与深链**：`PAGES` 封闭集 8 名（login/overview/projects/run_new/run_detail/review/todos/release）与 IF-009 §2b 的 URL 表不变。`login` 渲染双栏 auth shell（§1m.1）；其余 7 名渲染**同一** workbench shell HTML 文档，携带 `data-route`（路由名 + run_id/project_id 等深链参数），客户端路由器据此恢复初始 tab/sidebar。所有 shell 为完整 HTML 文档；全部交互控件携带 `data-testid`（命名 `<区>-<控件>`，如 `tabbar-item-docs`、`doc-save`、`pane-add`）。
2. **SPA 交互模型**：认证后功能区切换、tab 开闭、文档切换、保存均在单页内完成，不经整页刷新（同一 document 实例延续）。静态面为原生 ES modules：shell HTML 以 `<script type="module" src="/static/app/shell.js">` 引导，模块间经 ES import 解析，全部资产同源 `/static/**`；无 npm/Vite/打包器，无构建产物入仓（NFR-0156；AC-FR0316-02 的扫描面）。
3. **chrome 结构**：三段式布局（左侧垂直 tab 条 + sidebar + 多 tab main area）在所有上下文相对位置一致。tab 条仅图标 + hover tooltip，命中区 ≥32px，顺序固定不可拖拽。功能项封闭集七项：Projects/Runs/Docs/Review/Todos/Settings/Account——overview/projects 并入 Projects/Runs 总览语义，run_new 收为 Runs 下新建入口，release 收为 run 详情内发布决定；Settings 经齿轮项只开 tab 不换 sidebar；Account 菜单仅 logout（结构槽位 settings/check updates/logout 中后两者本版无行为/无入口）。
4. **tab 语义（spec SM-04）**：切换 tab 条项 → 同一切换内 sidebar 换到对应导航树且 main area 打开/激活对应 tab（无只换 sidebar 的中间态）；重复点击激活已有唯一实例；已打开 tab 共存不被切换关闭；用户主动关闭是唯一关闭路径，无关闭所有全局动作；tab 集合只在浏览器内存，不跨会话持久化（刷新后空集合为预期行为）。main area 标题区直接显示内容标题（文档标题/run 标题/设置页标题），不显示 tab 条分类名；sidebar 只显当前 tab 条项的下级内容。
5. **显式降级**：sidebar 树节点、main 内容、tab 标签遇到未知或缺失值时渲染可读降级标记（NotFound/未知），不崩溃、不 500、不留空白 tab。
6. **真数据与写安全**：UI 一切状态来自既有查询投影与事件流（无 mock 数据）；API 失败产生用户可观察的失败反馈（错误条/失败标记，不伪报成功）；SSE 断线按 `event_cursor` 补读，重复/乱序事件不使 UI 倒退；写操作一律携带 `X-Trac-CSRF` 与 `Idempotency-Key` 头（IF-009 §2b 契约的客户端遵守面）。

### 1m. 登录壳与名字端到端绑定（IF-AUTHNAME-001）

**modules**：`tracks/server/auth.py`、`tracks/server/app.py` 实现；`tracks/server/pages.py` 承载登录壳；命令服务/事件流消费生效 actor。

1. **双栏 auth shell**：`/login` 渲染左侧 hero 图 + 标语 + 图片署名、右侧登录面板（错误行内展示、窄视口折叠单栏）。hero 图资产同源 `/static/**`（无外链）。登录成功签发既有 `trac_session` Cookie（HttpOnly、SameSite=Strict、Path=/，7 天滚动）——记住登录态由该持久 cookie 承载，无复选框。
2. **名字采集**：`auth.display_name` 未设置时，登录响应（§2b #1）携带 `name_required: true`；workbench 页面路由（7 个非 login 入口）在会话有效但 `display_name` 未设置时 302 到 `/login`（名字未采集不进入工作台数据面）。名字经 `POST /api/auth/name`（§2b #29）提交：非空、去首尾空白、长度 ≤64 字符；生效后写 `auth.display_name`、把既有 sessions 行的 `actor` 更新为该名字、落 `auth.name_bound`（§1a #25）。
3. **生效 actor**：`Session.actor`（签发与会话解析）= `display_name`（已设置）否则 auth 行 `actor` 否则 `local-user`（既有 fallback，仅存在于采集前窗口）。此后该名字出现在：会话称谓（workbench Account 区与 §2b #30 profile 出口）、事件 actor 字段（Web 提交的 `human.anchor`/`human.approval` 等审计事件）、agent assignment 中引用 Human 的称谓、discussion speaker 标签（Web 面讨论操作者恒等于会话 actor，IF-009 §1f.4 既有规则）。名字是同一单用户的身份标注，不创建第二身份；无注册/多账号入口（`/api/auth/register` 等路径不存在，404）。
4. **logout 不变**：`POST /api/auth/logout` 删除服务端会话行且响应清 cookie，浏览器不残留凭据。

### 1n. 文档中心读模型（IF-DOCCENTER-001）

**modules**：`tracks/server/projections.py`、`tracks/server/api_query.py` 实现；`tracks/server/static/app/`（docs 视图、editor、panes）消费；`tracks/baseline.py` 的 revision 语义复用。

1. **文档树**：`GET /api/projects/{pid}/docs/tree`（§2b #31）返回 `{versions: [{version, docs: [{doc, revision, updated_at}], editable_run_id}]}`。`versions` 按版本号逆序（数值元组比较，最新置顶；`v<M>.<m>-hotfix-<issue>` 目录排在其基线版本之后同组内）。`docs` 为六件套封闭集 `{story, spec, acceptance, architecture, interfaces, test-plan}` 中实际存在的成员；缺失成员不出现在列表（UI 对未知/缺失显式降级）。`revision` 为该版本目录的 **docs_revision**（§1o.1a——六件套摘要，文档中心专用身份；不是 `baseline.revision_digest`，后者只覆盖 story/spec/acceptance 三件且仍专属批准绑定面）。`editable_run_id` 为同属该版本且非终态的最近 run（无则 null）——编辑入口（§1o）只在该字段非空且与浏览版本一致时可用。
2. **文档读取**：`GET /api/projects/{pid}/docs/{version}/{doc}`（§2b #32）返回 `{revision, content, history: [{revision, ts, actor}]}`——`revision` 为该版本目录的 **docs_revision**（§1o.1a；history 同源既有 material.edited 事件 + git 历史的合并语义，from/to 按编辑面各自 token 记录）；`GET .../diff?from=&to=`（§2b #33）返回 `{from, to, unified_diff}`。既有 run 域读取端点（IF-009 §2b #15/16）保持不变（revision 字段为 trio 摘要，v0.9 形态）。
3. **编辑器宿主**：文档中心以 Vditor `ir` 即时渲染模式承载查看/编辑，资产经同源 `/static/vendor/vditor/` 按需加载（IF-009 §2b 的 vendor/禁 CDN/可选引擎禁用不变）；Vditor 加载失败（资产 404/脚本错误/初始化异常）回退 `<textarea>`，文档仍可查看与编辑，内容不丢。多 pane 分屏至多 4 列，每 pane 独立文件选择器与工具栏、独立加载不同文档；超出 4 列的请求不生效且既有 pane 不受影响。
4. **doc 参数封闭集扩展**：`doc ∈ {story, spec, acceptance, architecture, interfaces, test-plan}`；`design` 保留为 `architecture` 的向后兼容别名。`material.edited` 事件的 `doc` 字段枚举同步扩展为该六名（事件类型不变，payload 枚举追加——§0.1 #3）。

### 1o. 显式保存与冲突恢复（IF-DOCSAVE-001）

**modules**：`tracks/server/api_command.py`（两编辑路由）、`tracks/supervisor/service.py`（既有受理面 + docs_revision 纯函数定义）实现；`tracks/server/projections.py` 消费 docs_revision 读模型；`tracks/server/static/app/`（editor）消费。

1a. **docs_revision（文档中心修订身份，本版新增）**：`docs_revision(vdir)` 为纯函数摘要——对六件套按固定顺序（story/spec/acc/architecture/interfaces/test_plan 标签）以与 `baseline.revision_digest` 相同的 `label:body_sha` 构造拼接后 sha256；六件套任一文档变更即移动。定义于 `tracks/supervisor/service.py`（T-004 写域），`tracks/server/projections.py` 经既有 server→supervisor import 方向消费。**不触碰 `tracks/baseline.py`**：`revision_digest`（trio）与 FR-0308 批准绑定语义逐字不变。trio 文档（story/spec/acceptance）经任一编辑面保存都会同时移动 trio 摘要与 docs_revision——批准绑定不被绕过是结构保证而非约定。

1b. **编辑面分家**：两个编辑面、两种 token，各自封闭——
   - **文档中心面（本版新增，六件套全域）**：`POST /api/projects/{pid}/docs/{version}/{doc}/edits`（§2b #35），`{base_revision, content}`，base_revision 为 **docs_revision**。服务端解析该版本的 `editable_run_id`（无可编辑 run → 422 `no_editable_run`），经命令服务提交 `edit_material`（kind 不变，params 追加可选 `revision_kind: "docs"`，缺省 `"trio"`）；`revision_kind="docs"` 时 base 校验与 `material.edited` 的 `from_revision/to_revision` 均为 docs_revision 值。
   - **运行域审阅面（v0.9 语义不变，限 trio）**：`POST /api/runs/{run_id}/docs/{doc}/edits`（§2b #17），base_revision 为 **trio 摘要**（v0.9 批准绑定 token）。`doc` 域收敛为 `{story, spec, acceptance}`；`design` 别名与三份设计文档名在该面返回 422 并指向文档中心端点（v0.9 的 design 别名编辑在 trio token 下结构性不可满足——design 文档保存不可能移动 trio 摘要——本版显式关闭该缺陷面）。
2. **409 两选项**：任一编辑面以 409 拒绝（revision/mtime 冲突）时，响应体在既有 `{"error": {...}}` 旁**顶层**追加 `current_revision` 字段（该编辑面的当前 token 值：文档中心面为 docs_revision，运行域面为 trio 摘要）——本条为唯一权威形态（顶层字段，不在 error 对象内）。UI 向用户提供两选项：**重载放弃**（以服务端内容与 current_revision 重载编辑器，放弃本地改动）与**强制覆盖**（保留本地编辑内容，以 `current_revision` 为新 `base_revision` 重提）；不静默覆盖；写失败（网络/5xx）时编辑内容保留可重试。
3. **保存门**：保存控件仅在 dirty（内容与服务端 revision 不一致）时可用；服务端接受产生新 revision（`material.edited` + Runtime 提交 + 待批准条目更新）。trio 文档保存后，对旧 trio revision 的批准按既有 `stale_revision` 拒绝（FR-0308 不绕过）；设计文档保存移动 docs_revision（文档中心基线切换），设计文档无 revision 绑定的批准（批准绑定只存在于 trio 面）。

### 1p. inline discussion 只读投影（IF-DISCUSS-001）

**modules**：`tracks/server/projections.py`、`tracks/server/api_query.py` 实现（复用 `tracks/discuss` parser，协议解析只在服务端）；`tracks/server/static/app/`（discussions 覆盖层）消费。

1. **读模型**：`GET /api/projects/{pid}/docs/{version}/{doc}/discussions`（§2b #34）返回 `{threads: [{thread_id, status, initiator, anchor_line, summary, entry_line, awaiting}]}`——`status ∈ {open, resolved, reopen}`；`awaiting` 为该线程当前等待方（按 tracks/discuss 协议推导；无则为 null）；`entry_line` 为线程在正文中的起始行（UI 导航锚点）。客户端不解析、不拼接讨论协议格式。
2. **导航语义**：UI 提供显示/隐藏切换、下一个 discussion、只看未决（status ≠ resolved）过滤；`RESOLVED` 语义仅发起人（既有裁决权属合同 IF-009 §1f.4 不变）。
3. **本版无写回**：UI 不出现 resolve/reply 入口；API 无讨论写回端点（对讨论路径的变更请求得到 404/405）；写回留既有 CLI（`trac discuss`）。

### 1q. run 时间线投影扩展（IF-TIMELINE-001）

**modules**：`tracks/server/api_query.py`、`tracks/server/projections.py` 实现（stage 序取自 `tracks/kernel/stage_registry.canonical_stage_order` 的只读事实表组合，见下）；`tracks/server/static/app/`（timeline 视图）消费。数据源为既有 `GET /api/runs/{run_id}/timeline` 与 `GET /api/runs/{run_id}/ac-chain`（IF-009 §1e），本版只加投影字段，不改事件语义。

1. **stage_order**：timeline 响应追加 `stage_order: list[str]`——13 阶段显示序：`M-START` 居首，随后为 `canonical_stage_order()` 序列并在 `M-ACC` 与 `M-DESIGN` 之间插入 `M-REQ-APPROVAL`（即 M-START、M-STORY、M-SPEC、M-ACC、M-REQ-APPROVAL、M-DESIGN、M-TEST、M-IMPL、M-VERIFY、M-SECURITY、M-RELEASE、M-PUBLISH、M-MILESTONE）。客户端不硬编码阶段集合。
2. **视图语义（客户端组合规则）**：顶部当前节点卡片（责任方/attempt/运行时长/唯一主要动作）由 run detail（control_state、executions、progress、todos、wait，IF-009 §1e）组合；线性时间线上每次 attempt 为独立节点不折叠（timeline 事件按 attempt 分组），回拨（stage.exited 指向上游阶段/escape 事件对）以可辨认回边呈现；视口聚焦活跃节点前后若干节点；点节点打开浮层（起止时间与 artifact/revision，链接跳文档中心对应文档）。时间线内容与 timeline/ac-chain 接口数据一致，不合成第二来源。

### 1r. 发布卫生合同

#### 1r.1 milestone 收尾终态感知（IF-MILESTONE-002）

**modules**：`tracks/executor/milestone_chain.py` 实现。

`_complete_milestone` 的幂等守卫改为终态感知：仅当事件日志已含 `run.completed` 且其 payload `terminal_state == "released"` 时跳过（真幂等）；`terminal_state == "boundary"`（M-IMPL 闭包边界伪完成，IF-009 前既有语义）不视为收尾完成，守卫不得据此跳过——收尾链照常落 `run.completed(terminal_state=released, release_tag)` 且 `close_milestone` 不再无限重发。其余收尾步骤（refs 清理、seal、project/milestone 关闭、issue 关闭）语义不变。

#### 1r.2 制品声明版本绑定（IF-VERSION-001）

**modules**：`tracks/executor/host_contract.py`（解析）、`tracks/executor/verify_version.py`（执行）实现；宿主合同 `.tracks/projects/project.toml` 承载声明。

1. **声明 schema**：`[host-contract.version_scheme]` 追加可选键 `bindings`——`{file, key, expect}` 内联表数组；既有 `file`/`key`/`expect` 单数键解析保持（声明时等价于单元素 bindings）。`file` 为仓库相对路径；`key` 按文件类型分派：`.toml` 经 tomllib 点路径（如 `project.version`），`.py` 为模块级 `NAME = "..."` 字面量赋值（如 `__version__`）；`expect` 为模板，以 run 版本事实 + 门供应占位符 `{release_tag}`（本旅程派生 tag）与 `{release_version}`（派生 tag 去一个前导 `v`）渲染，未知占位符 fail-closed。
2. **执行语义**：`source="version_decl"` 的 version gate 在既有 tag 派生/远端缺席校验之外追加：当 bindings 非空且旅程为 feature/post_release 时，逐绑定读取 file、提取 key、与渲染后的 expect 比对；任一错配即 `local_gate.failed`（kind=version，reason=`version_decl_mismatch`），payload 指认具体 `file` 与 `key`；全部一致才放行。dev（prerelease）旅程豁免文件绑定（prerelease tag 为短暂身份，不要求文件升版）。bindings 未声明时行为与 v0.9 逐字一致。
3. **本宿主声明**（architecture §4.3）：pyproject.toml 的 `project.version` 与 `tracks/__init__.py` 的 `__version__` 均绑定 `{release_version}`；版本号在设计期 bump 到发布版本（发布期升版惯例由本门禁取代）；pyproject bytes 变化引起的 guard-registry `config_digest` 重锚随本设计同包完成（先例 e5637a1）。

#### 1r.3 hotfix 前检分类映射（IF-HOTFIX-011）

**modules**：`tracks/executor/hotfix_face.py`（fetch 分类承接）、`tracks/executor/hotfix.py`（分类指引映射）实现。

`precheck_hotfix` 纯函数签名与规则不变（IF-HOTFIX-003 继承：`issue=None` 即确认缺失 → `issue_not_found`）。其上游 fetch 面（`precheck_hotfix_report`/`select_issue_backend` 链）改为分类承接：`GithubIssuesError.classification == "not_found"`（含 fetch_issue 的 404→None）→ `issue_not_found`；`auth`/`rate_limit`/`network`/`missing_token` → `issue_fetch_failed`，且 `next` 按分类给出可操作指引（missing_token：按 ops 文档前置节配置 `GITHUB_TOKEN`；auth：检查 token 权限；rate_limit：等待限额重置后重试；network：检查连通性/TLS 或 `TRAC_GITHUB_CA_BUNDLE`）。`issue_fetch_failed` 保持 REJECTED 可重试路径开启（修复后重提前检）。`HotfixRejectionReason` 封闭集成员不变（两个原因本就在集合内，本版使后者可达）。

#### 1r.4 live GitHub 通道 TLS（IF-TLS-001）

**modules**：`tracks/effects/github.py` 实现；`docs/getting-started/installation.md` 承载 ops 前置节。

1. **请求层**：`effects/github.py` 的全部 HTTPS 调用点（`_urlopen`/`_get_any`/`_api_json` 链路）显式使用 `ssl.create_default_context(cafile=certifi.where())` 发起 TLS 校验；certifi 进入运行时依赖（pyproject pinned `certifi==2026.7.22`）。环境变量 `TRAC_GITHUB_CA_BUNDLE` 提供显式 CA bundle 覆盖（企业代理/自签 CA 场景；设置后取代 certifi 路径）。
2. **可诊断**：TLS/连接失败维持 `GithubIssuesError` 分类（network 等）呈现，不静默降级；live 旅程环境前置（`GITHUB_TOKEN`/`TRAC_GITHUB_REPO`/TLS 与 `TRAC_GITHUB_CA_BUNDLE`）写进 ops 文档的「Live GitHub 旅程环境前置」节。

#### 1r.5 tracker milestone 生命周期补全（IF-TRACKER-001）

**modules**：`tracks/effects/github.py`（`ensure_project_milestone`）、`tracks/executor/milestone_chain.py`（首次接触点接线）实现。

1. **ensure 语义**：`ensure_project_milestone(repo_id, project, title)`——`GET /repos/{repo}/milestones?state=all` 按标题精确匹配：命中即复用（返回 `{milestone, number, state, api_verified: true, created: false}`）；未命中且具备凭据时 `POST /repos/{repo}/milestones` 创建，随后 GET 回读校验，返回 `created: true, api_verified: true`；同一标题幂等复用不重复建。任何失败（无凭据/非权威/创建或回读失败）返回分类 error，绝不假报 `api_verified`。
2. **接线**：M-MILESTONE 关闭链（`_close_milestone_project`）在 close 之前先经 ensure 确保远端存在（首次接触点语义）；ensure 不可得时落 `attention.required`（area=project_close，reason=`milestone_not_found`），`next` 携带可操作指引（按 `milestone_template` 渲染标题手工创建后 `trac run --resume` 重试），并保持既有 audited skip 形态，不阻断其余收尾步骤的既有语义。

### 1s. LEX_REVIEW 合法 park 出口与线程复用（IF-REVIEW-001）

**modules**：`tracks/effects/opencode_review.py`（verdict 计算与派发上下文注入）、`tracks/kernel/machine_verdicts.py`（归约）、`tracks/kernel/machine.py`（State 投影字段）、`tracks/cli/status_cmd.py`（渲染）、`tracks/agents/Lex.md`（agent 纪律，Devon 实现）共同承载。

1. **verdict 词汇追加**：`lex.verdict` 的 `verdict` 字段封闭集追加 `pass-pending-human-threads`；此时 payload 追加 `pending_threads: [{doc, thread_id, summary}]`（非空）。计算规则（评审出口处，以 tracks/discuss 解析为准）：全部线程 resolved → `pass`；存在未决线程且**每个**未决线程的裁决权属方为 Human（根评论 @Human，`adjudication_owner` 语义）→ `pass-pending-human-threads`；其余 → `revise`（语义不变）。
2. **park 语义**：kernel 归约 `pass-pending-human-threads` → `status=awaiting_human`、`awaiting="review_pending_threads"`（封闭集追加成员），substate 停留 LEX_REVIEW，reviewer 通过标志不置位；`pending_threads` 投影进 State（事件可重放推导），空转的重试循环不再发生。
3. **resume**：`trac review`（human.review 事件）在 `awaiting == "review_pending_threads"` 时语义为「Human 已处理待决线程，恢复评审」——归约为清除 awaiting 并重进 LEX_REVIEW（重新 dispatch Lex）；其后按 §1s.1 重新计算（线程已决 → `pass` 进入 HUMAN_REVIEW；仍仅余 Human 线程 → 再次合法 park；出现其他未决 → `revise` 回 RESPOND）。HUMAN_REVIEW 子态下 `trac review` 的既有语义不变。
4. **状态面**：`trac status` 对 `awaiting=review_pending_threads` 渲染待决 Human 线程清单（doc + thread_id + 摘要），指引用户处理后恢复。
5. **线程复用**：LEX_REVIEW（含重进）的 dispatch assignment 由派发物化面注入 `open_threads` 清单（目标文档当前 open/reopen 线程的 thread_id/status/initiator/awaiting/anchor 摘要，tracks/discuss 解析产生）；Lex 的 findings 续写必须在既有线程内 reply，跨 attempt 不逐轮新开重复线程（agent 纪律由 tracks/agents/Lex.md 承载，机器侧以 open_threads 注入 + 评审出口计算保证不空转）。

### 1t. UI e2e 通道（IF-WEBUI-001）

**modules**：`tests/e2e/`（ui marker 测试，Shield）、`tests/_support/`（Playwright fixture，Shield）承载测试资产；`.github/workflows/ci.yml` 承载 `ui-e2e` job；`pyproject.toml` 承载依赖与 marker。

1. **分层**：UI e2e 以 Playwright（Chromium）真实浏览器驱动，测试落 `tests/e2e/` 并标 `@pytest.mark.ui`；默认套件（pyproject addopts 与各 browserless CI job 的显式 `-m`）排除 `ui`；CI 新 required check `ui-e2e` 安装 Chromium（`playwright install --with-deps chromium`）后以 `-m ui` 运行。Playwright 浏览器下载与该 job 的 CI 分钟为独立基础设施预算，不挤占 FR 预算（NFR-0155）。
2. **控件绑定**：UI e2e 定位控件一律经 `data-testid`（§1l.1），不依赖 CSS class/DOM 层级；关键旅程（登录、真数据浏览、文档中心编辑产生新 revision）不得以 API 请求替代关键 UI 操作。
3. **宿主合同**：`[host-contract.ci].required_checks` 追加 `ui-e2e`；`release-evidence` milestone 硬门禁的 needs 同步追加。

### 1u. 到达即绿守卫节点合同（IF-GREENGUARD-001）

**modules**：`tracks/executor/test_execute.py`（RED_CHECK 逐节点分类的消费面，**待实现 Devon**）；守卫节点测试资产由 Shield 在 tests/integration/tests/e2e 承载；声明面是版本 test-plan.md 的 §8.1（Archer 设计面，本节只定义契约形态）。

1. **两类节点**：版本测试资产分「合法 Red 验收锚点」与「到达即绿守卫」。验收锚点断言本版未实现的产品行为——实现前合法 Red、实现后转绿。守卫验证设计期/继承期已落地的事实（声明存在性、静态纪律扫描、缺席条件、继承行为回归）——到达即绿是其正确形态；把一个永远不可能红的节点塞进红窗必填集是设计缺陷（task graph 不可满足）。
2. **声明面**：test-plan §8.1 以每行一个测试节点 id 的清单声明守卫集合（含 AC 与 IF 归属标注）；该清单是守卫身份的唯一权威，不存在隐式守卫；守卫节点同时保留其 §8 表行（AC 覆盖闭合与 IF 归属不变）。清单引用的节点必须在收集集合内可解析，无法解析即声明失实（fail-closed）。
3. **RED_CHECK 语义**：r2 选窗内，属守卫集合的节点通过时分类为 `guard_verified`（合法，不触发 unexpected_pass）；守卫节点失败或缺席即 red.validated(invalid) fail-closed（守卫守护的是已落地合同，失败即真回归而非「没到实现期」）。未声明节点的到达即绿维持既有 `unexpected_pass` 语义不变；OOB 通道（oob.accepted 记录文件）的豁免语义不变。
4. **M-IMPL 锚点纪律**：任务图不得把守卫节点声明为 task 的红必填验收锚点（验收锚点子集只含合法 Red 类）；守卫经每次 RED_CHECK 的必过语义与 ISLAND_GATE_2/FULL 持续验证，其 AC 归属仍在 §8 行内。
5. **选窗纪律**：随操作者修复提交落地的回归测试是已落地行为的验证器而非版本验收仪器，经既有 OOB 通道豁免红窗（观察面语义继承不变）；合法红必填集 = r2 ∩（§8 合法 Red 锚点），守卫经 §8.1、OOB 文件经 oob.accepted 分别豁免。

## 2. CLI 接口合同

### 2a. serve 子命令

不变（IF-009 §2a 逐字继承；无新增 flag）。

### 2b. HTTP API 合同

IF-009 §2b 的基底约定（认证边界、CSRF + Idempotency-Key、错误封装 `{"error": {"reason", "detail"}}`）与 #1-28 端点逐字继承，本版变更如下：

| # | method/path | 输入 | 成功 | 失败 | 备注 |
|:--|:--|:--|:--|:--|:--|
| 1 | `POST /api/auth/login` | `{password}` | 200 `{actor, csrf_token, name_required: bool}` + Set-Cookie | 401 `unauthenticated` | 【公开】；`name_required` 为 §1m.2 追加字段 |
| 17 | `POST /api/runs/{run_id}/docs/{doc}/edits` | `{base_revision, content}` | 202 `{command_id, new_revision}` | 409 `stale_revision`（响应体**顶层**追加 `current_revision`，值为其编辑面当前 token——trio 摘要）/422 | `doc` 域收敛为 `{story, spec, acceptance}`（`design` 别名与设计文档名 → 422 指向 #35）；token 与 v0.9 批准绑定一致；§1o.1b |
| 35 | `POST /api/projects/{pid}/docs/{version}/{doc}/edits` | `{base_revision, content}`（base 为 docs_revision） | 202 `{command_id, new_revision}`（new_revision 为新 docs_revision） | 409 `stale_revision`（顶层 `current_revision`=当前 docs_revision）/422 `no_editable_run` | 文档中心编辑面（六件套全域）；服务端解析 editable_run_id 后经命令服务提交 `edit_material`（params 追加 `revision_kind:"docs"`）；§1o.1b |
| 11 | `GET /api/runs/{run_id}/timeline?...` | query | 200 既有 timeline schema + `stage_order: list[str]`（§1q.1） | 404 | 只读；追加字段 |
| 29 | `POST /api/auth/name` | `{name}` | 200 `{actor}`（生效显示名；落 `auth.name_bound`） | 400 `validation_failed`（空/超长/控制字符）/401 `unauthenticated`/403（CSRF） | 需认证会话 + CSRF；幂等可重提 |
| 30 | `GET /api/auth/profile` | — | 200 `{actor, name_set: bool}` | 401 | 只读 |
| 31 | `GET /api/projects/{pid}/docs/tree` | — | 200 §1n.1 tree schema | 404 | 只读；版本逆序 |
| 32 | `GET /api/projects/{pid}/docs/{version}/{doc}` | path | 200 `{revision, content, history}` | 404 | 只读；doc 域为 §1n.4 六名 |
| 33 | `GET /api/projects/{pid}/docs/{version}/{doc}/diff?from=&to=` | query | 200 `{from, to, unified_diff}` | 404/422 | 只读 |
| 34 | `GET /api/projects/{pid}/docs/{version}/{doc}/discussions` | — | 200 §1p.1 threads schema | 404 | 只读；无对应写回端点（本版） |

页面（HTML；除 `/login` 与公开面外需认证，未认证 302 至 `/login`）：8 个 URL 入口不变（IF-009 §2b 页面段）；login 渲染 §1m.1 双栏 auth shell，其余渲染同一 workbench shell（§1l.1，携带 `data-route`）；会话有效但名字未采集时访问工作台入口 302 至 `/login`（§1m.2）。Vditor 资产同源不变（禁 CDN、可选引擎禁用）。

### 2c. `trac status` 与 `trac review` 语义追加

- `trac status`：run 处于 `awaiting=review_pending_threads` 时渲染待决 Human 线程清单（每线程 doc + thread_id + 摘要），指引处理后以 `trac review` 恢复（§1s.4）。
- `trac review`：既有 HUMAN_REVIEW 语义不变；追加 §1s.3 的 park 恢复语义（`awaiting=review_pending_threads` 前置下恢复 LEX_REVIEW 重评）。

## 3. 文件 / 存储契约

| # | 路径 | 格式/写入者 | 读取者/生命周期 |
|:--|:--|:--|:--|
| 1 | `<service_home>/service.db` | 继承 IF-009 §3 #1；`auth` 表追加 `display_name` 可空列（§1c） | 持久，跨重启；启动期幂等演进 |
| 2 | `tracks/server/static/app/*.js` 与 `tracks/server/static/styles.css` | 工作台应用层实现产物（**待实现 Devon**；路径由本合同预留，wheel package-data 已含 `server/static/app/*`） | 浏览器经同源 `/static/app/**` 加载；ES modules 无构建链 |
| 3 | `tracks/server/static/vendor/vditor/**` + manifest.json | 继承 IF-009 §3 #6 不变（vendored 资产与逐文件 sha256 对账） | 文档中心按需同源加载（§1n.3） |
| 4 | `.tracks/projects/project.toml` | 本版变更：`[host-contract.version_scheme].bindings`、`[host-contract.ci].required_checks`（§0.1 #7/#12）；其余段逐字不变 | 既有消费者 + version gate 绑定执行（§1r.2） |
| 5 | `pyproject.toml` / `tracks/__init__.py` | version 升至 `0.10.0`（设计期 bump，§1r.2.3）；pyproject 增 certifi（运行时）与 playwright（dev）依赖、`ui` marker、`server/static/app/*` package-data | version gate 绑定比对的被测文件 |
| 6 | `docs/getting-started/installation.md` | 追加「Live GitHub 旅程环境前置」节（§1r.4.2；Archer 随设计物化） | 操作者；live 通道失败指引的指向 |
| 7 | 页面/静态面 HTML | workbench shell 携带 `data-route` 与 `data-testid` 锚点集（§1l.1） | UI e2e 经 `data-testid` 定位（§1t.2） |
| 8 | `.tracks/runtime/tracks.db` 与 blobs | 继承不变（EVENT_TYPES 封闭集零追加） | 既有读写者 |

## 4. 可观察出口（测试断言基础）

### 4a. HTTP/CLI 出口

> **Prism [RESOLVED]:** BLOCKER, review of the docs_revision re-draft (e987707): the assertion-basis table 4a was not updated for endpoint 35. Row 1 still enumerates the new-endpoint outlet as '2b #29-34' — the new project-domain WRITE face 35 (POST /api/projects/{pid}/docs/{version}/{doc}/edits) is absent from the only table test-plan 6.5 designates as the assertion basis for HTTP outlets, so the Shield re-entry revision of the two AC-FR0323 anchors (test-plan 11 row 6) would be asserting a 35-shaped endpoint with no 4a outlet row. Row 2 still frames current_revision as an appended field of the EXISTING endpoints #1/#17/#11 only, while the authoritative form is now a response-body TOP-LEVEL field on both edit faces (17 carries the trio digest, 35 carries docs_revision) and 35 additionally owns the 422 no_editable_run reason that belongs in row 1's error-reason closed set. Expected revision @Archer, all one-line edits inside interfaces.md: (1) 4a row 1 enumerate #29-35 and add no_editable_run to the error reasons; (2) 4a row 2 restate current_revision as the top-level 409 field of BOTH edit faces per 1o.2; (3) 0.1 row 2 update the endpoint summary ('project-domain 4' predates 35 — row 15 declares it but the summary enumeration contradicts it). The normative sections 1o/2b/1n and the closures are consistent and complete; this is derived-layer convergence only, but a stale assertion basis is exactly the two-readable shape that produced the T-004 DIAGNOSE, so it should not ride an advisory into M-TEST re-entry.
>> **Archer:** All three findings fixed: (1) 4a row 1 now enumerates §2b #29-35 with no_editable_run added to the error-reason closed set; (2) 4a row 2 restates current_revision as the top-level 409 field of both edit faces (#17 trio digest, #35 docs_revision) per §1o.2, removing the appended-field framing; (3) §0.1 row 2 now enumerates the endpoints as auth 2 + project-domain read 4 + edit face 1 (#35) pointing at row 15. Also converged the same stale enumeration in architecture.md (growth-table row 5 and the §1.1 route paragraph: #29-35, seven endpoints). Normative layers untouched. Please re-review and resolve.
>>> **Prism:** Verified against commit e64725b: all three blocker items landed — 4a row 1 enumerates 29-35 with no_editable_run in the error-reason closed set, 4a row 2 states current_revision as the top-level 409 field of both edit faces with their token identities per 1o.2, and 0.1 row 2 breaks the enumeration out as auth 2 + project-domain read 4 + edit face 1 pointing at row 15; the bonus convergence of architecture row 5 and the 1.1 route paragraph (29-35, seven endpoints) removes the same staleness there. Assertion basis now matches the normative 1o/2b/1n contract exactly; this round's verdict is pass on the documents. The two advisories (architecture 3.4 read-only opener, IF-DOCREV-001 registry pointer) remain open as convenience items, carried in findings. Mechanics: this root's single @mention makes you the adjudication owner, so the resolved flip is yours (operator Archer) — same pattern as the test-plan T-001 close. Please set-status resolved on your next dispatch so discussion_ready passes at the EXIT gate; substance is settled.

| # | outlet | assertions |
|:--|:--|:--|
| 1 | §2b #29-35 新端点的 JSON schema/状态码 | 逐端点；错误 reason 封闭集（含 #35 的 `no_editable_run`）；tree 版本逆序；discussions 线程字段；timeline `stage_order` 为 §1q.1 十三阶段序 |
| 2 | §2b #1/#17/#11 既有端点的追加字段 | `name_required`、`stage_order` 存在且语义正确；`current_revision` 为两编辑面 409 的响应体**顶层**字段（#17 携 trio 摘要、#35 携 docs_revision，§1o.2 唯一权威形态） |
| 3 | 页面 HTML | login 双栏形态（hero/署名/面板/行内错误位）；workbench shell 单一文档 + `data-route`；`type="module"` 引导；`data-testid` 锚点存在；无 CDN/外链资产；无密钥 |
| 4 | `trac status` 输出 | `awaiting=review_pending_threads` 渲染待决线程清单 |
| 5 | `trac hotfix` 前检出口 | `issue_not_found` vs `issue_fetch_failed` 分类与各分类 next 指引 |
| 6 | version gate 事件 | `local_gate.passed`/`local_gate.failed(kind=version, reason=version_decl_mismatch)` payload 指认 file/key |
| 7 | serve stdout/stderr 与退出码 | 继承 IF-009 §4a #1 不变 |

### 4b. service.db / 事件出口

| # | outlet | assertions |
|:--|:--|:--|
| 1 | `service_events` §1a 全事件（含 #25 `auth.name_bound`） | append-only、seq 单调、payload 字段封闭集 |
| 2 | `auth`/`sessions` 表 | `display_name` 持久化；名字生效后 sessions.actor 一致；logout 删除会话行 |
| 3 | tracks.db 既有事件 | `human.approval`/`human.anchor` 等 actor 字段为生效显示名；无新增事件类型；`run.completed(terminal_state=released)` 在边界伪完成存在时照常落地且仅一次 |

### 4c. git / 远端 / CI 出口（继承 + 追加）

| # | outlet | assertions |
|:--|:--|:--|
| 1 | 文档文件与 revision digest | 文档中心编辑产生新 revision；旧 revision 批准 stale 拒绝（继承）；409 两选项后基线一致 |
| 2 | bare/stand-in 远端事实 | 发布链继承；`ensure_project_milestone` 创建/复用/回读可观察；`milestone_not_found` 携可操作 next |
| 3 | CI required checks | `ui-e2e` 出现在 `[host-contract.ci].required_checks` 与 ci.yml job 集；browserless job 的 `-m` 表达式排除 `ui` |
| 4 | TLS 通道 | 本地 HTTPS stand-in（测试 CA + `TRAC_GITHUB_CA_BUNDLE`）握手成功；默认 certifi 束下同一自签证书请求分类失败；live 通道（e2e_live 既有）真实可达 |

### 4d. 浏览器可观察出口（UI e2e，IF-WEBUI-001）

| # | outlet | assertions |
|:--|:--|:--|
| 1 | document 实例延续 | 功能区切换/tab 开闭/文档切换/保存全程无整页刷新（Playwright 同一 document 句柄/无 navigation 事件） |
| 2 | chrome 几何与行为 | tab 条命中区 ≥32px、tooltip、顺序固定；sidebar-main 联动原子性；tab 共存/唯一实例/主动关闭唯一路径/刷新空集合 |
| 3 | 降级标记 | 未知/缺失值处渲染可读降级标记，无空白 tab、无 5xx |
| 4 | 编辑器行为 | Vditor ir 宿主、保存 dirty 门、409 两选项、分屏 ≤4 列、discussion 导航三能力、Vditor 失败回退 textarea |
| 5 | 网络纪律 | 页面加载与交互期间无跨域请求（同源资产）；UI 发起的写请求携带 `X-Trac-CSRF` 与 `Idempotency-Key` |

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

### IF-RELEASE-001 / IF-RELEASE-002 / IF-RELEASE-003 release 合同组（继承 IF-006 / IF-008；v0.9 经 Web 入口复用不变）

### IF-DOCGAP-001 文档评论优先裁定合同（继承 IF-006）

### IF-QUARANTINE-001 outcome 隔离恢复合同（继承 IF-006）

### IF-HOTFIX-001 / IF-HOTFIX-002 / IF-HOTFIX-003 / IF-HOTFIX-004 / IF-HOTFIX-005 / IF-HOTFIX-006 / IF-HOTFIX-007 / IF-HOTFIX-008 / IF-HOTFIX-009 / IF-HOTFIX-010 hotfix 合同组（继承 IF-006；v0.10 经 §1r.3 恢复 issue_fetch_failed 可达性）

### IF-SELECT-001 / IF-SELECT-002（继承 IF-006）

### IF-EVIDENCE-001 evidence identity/reuse/stale 合同（继承 IF-006）

### IF-LEDGER-001 / IF-FULLCHAIN-001 / IF-RUNCONTRACT-001 / IF-NIGHTLY-001（继承 IF-006）

### IF-PHASE-001 / IF-PHASE-002 / IF-PHASE-003 / IF-GUARD-001 / IF-GUARD-002 / IF-AUTH-001 / IF-AUTH-002 / IF-MUTATION-001 / IF-MUTATION-002 / IF-CLOSURE-001 / IF-DEMO-001 / IF-FAILCLOSED-001（继承 IF-007）

### IF-ADAPTER-003 kernel/executor/cli 语言中立不变量（继承 IF-007；v0.9 允许区不变，本版无新增允许区）

### IF-VERIFY-001 / IF-VERIFY-002 / IF-VERIFY-003 / IF-VERIFY-004 / IF-VERIFY-005 发布验证合同组（继承 IF-008）

### IF-REPAIR-001 / IF-REPAIR-002 / IF-KNOWNISSUE-001 就地修复与 Known Issue 合同组（继承 IF-008）

### IF-ESCAPE-001 / IF-ESCAPE-002 逃生门合同组（继承 IF-008）

### IF-SECURITY-001 合同化安全评估合同（继承 IF-008）

### IF-PUBLISH-001 / IF-PUBLISH-002 发布幂等与 reconcile 合同（继承 IF-008）

### IF-MILESTONE-001 归档与生命周期合同（继承 IF-008；本版 §1r.1 修正其收尾幂等守卫的实现缺陷，合同语义经 IF-MILESTONE-002 补强）

### IF-JOURNEY-001 三旅程与版本方案合同（继承 IF-008）

### IF-ENVELOPE-001 / IF-ENVELOPE-002 envelope 合同组（继承 IF-008）

### IF-FAILURE-001 失败证据链合同（继承 IF-008）

### IF-HOSTCONTRACT-001 / IF-HOSTCONTRACT-002 宿主合同（继承 IF-008；version_scheme 的 bindings 键与本宿主声明见 §1r.2）

### IF-REFERENCE-001 reference host 合同（继承 IF-008）

### IF-ISSUE-001 / IF-ISSUE-002 Issue 合同组（继承 IF-008）

### IF-PIPELINE-001 单次权威测试流水线回归合同（继承 IF-008）

### IF-SERVE-001 服务生命周期与健康合同（继承 IF-009）

### IF-PROJ-001 项目登记与就绪检查合同（继承 IF-009）

### IF-CMDSVC-001 持久命令受理与幂等去重合同（继承 IF-009；UI 客户端的 CSRF/Idempotency-Key 遵守面见 §1l.6）

### IF-CMDGUARD-001 命令防护与 actor 类别合同（继承 IF-009）

### IF-DRIVE-001 结构化驱动步与自动推进合同（继承 IF-009）

### IF-LEASE-001 单一有效执行者租约与防旧合同（继承 IF-009）

### IF-SCHED-001 单活动 run 调度与 hotfix 换队合同（继承 IF-009）

### IF-WAIT-001 外部等待与退避合同（继承 IF-009）

### IF-RECOVER-001 崩溃重启恢复合同（继承 IF-009）

### IF-PAUSE-001 pause/resume 两阶段优先合同（继承 IF-009）

### IF-WEBGATE-001 Web 人工门绑定与拒绝合同（继承 IF-009；edit_material 的 doc 域扩展不改变 revision 绑定语义）

### IF-QUERY-001 只读投影查询合同（继承 IF-009；本版追加 §1n/§1q 读模型）

### IF-STREAM-001 事件订阅与游标补读合同（继承 IF-009；UI 客户端补读遵守面见 §1l.6）

### IF-WEBAUTH-001 本机单用户认证与裁决权属合同（继承 IF-009；名字绑定扩展见 §1m）

### IF-SECRECY-001 凭据脱敏与访问范围合同（继承 IF-009）

### IF-DOCREV-001 材料审阅/编辑 revision 合同（继承 IF-009；六件套扩展见 §1n.4；运行域编辑面收敛为 trio 域、文档中心编辑面与 docs_revision token 见 §1o.1a/1b）

### IF-WORKBENCH-001 SPA 工作台 shell 合同

- **合同**：§1l——页面两壳与深链（PAGES/URL 封闭集不变）、原生 ES modules 无构建链、三段式 chrome、tab 条固定语义（图标+tooltip、≥32px、顺序固定）、SM-04 tab 语义（原子联动/共存/唯一实例/主动关闭/不跨会话）、内容标题区、显式降级、真数据与写安全遵守面。
- **modules**：tracks/server/pages.py, tracks/server/app.py, tracks/server/static/app（待实现 Devon）, tracks/server/static/styles.css（待实现 Devon）, tracks/server/projections.py, tracks/server/api_events.py。
- **关联**：FR-0316、FR-0317、FR-0318、FR-0319、NFR-0153、NFR-0154、NFR-0156。

### IF-AUTHNAME-001 登录壳与名字端到端绑定合同

- **合同**：§1m——双栏 auth shell 与持久会话；`name_required` 采集门（未采集不进工作台数据面）；`POST /api/auth/name` 校验与持久化；生效 actor 端到端流入会话称谓、事件 actor、assignment 称谓与讨论 speaker；单用户边界不变、无注册面。
- **modules**：tracks/server/auth.py, tracks/server/app.py, tracks/server/pages.py, tracks/supervisor/db.py（schema 演进）, tracks/supervisor/service.py（actor 消费面）。
- **关联**：FR-0320、FR-0321。

### IF-DOCCENTER-001 文档中心读模型与编辑器宿主合同

- **合同**：§1n——project 域文档树（版本逆序置顶、六件套、editable_run_id）、版本化读取与 diff、Vditor ir 同源宿主与 textarea 回退、≤4 独立分屏。
- **modules**：tracks/server/projections.py, tracks/server/api_query.py, tracks/server/static/app（待实现 Devon）。
- **关联**：FR-0322、FR-0324。

### IF-DOCSAVE-001 显式保存与 409 两选项合同

- **合同**：§1o——docs_revision 六件套修订身份（§1o.1a，定义于 supervisor/service.py，baseline/FR-0308 零触碰）；编辑面分家（§1o.1b：文档中心面 #35 token=docs_revision 全六件，运行域面 #17 限 trio）；dirty 门；trio 保存移动双身份、批准绑定结构保证不被绕过；409 顶层 `current_revision`（唯一权威形态）；重载放弃/强制覆盖两选项；写失败内容不丢；edit_material params 追加可选 `revision_kind`。
- **modules**：tracks/server/api_command.py, tracks/supervisor/service.py（受理面 + docs_revision 纯函数）, tracks/server/projections.py（读模型消费）, tracks/server/static/app（待实现 Devon）。
- **关联**：FR-0323。

### IF-DISCUSS-001 inline discussion 只读投影合同

- **合同**：§1p——服务端解析的 threads 读模型（status/initiator/awaiting/entry_line）、显示/隐藏/下一个/只看未决导航、RESOLVED 仅发起人（继承裁决权属）、本版无 UI/API 写回。
- **modules**：tracks/server/projections.py, tracks/server/api_query.py, tracks/discuss（parser 复用）, tracks/server/static/app（待实现 Devon）。
- **关联**：FR-0325。

### IF-TIMELINE-001 run 时间线投影扩展合同

- **合同**：§1q——timeline 响应追加 `stage_order`（13 阶段显示序，服务端从 kernel 事实表组合）；当前节点卡片/attempt 不折叠/回拨回边/详情浮层的客户端组合规则；与既有 timeline/ac-chain 数据一致。
- **modules**：tracks/server/api_query.py, tracks/server/projections.py, tracks/kernel/stage_registry.py（只读事实表）, tracks/server/static/app（待实现 Devon）。
- **关联**：FR-0326。

### IF-MILESTONE-002 milestone 收尾终态感知合同

- **合同**：§1r.1——`_complete_milestone` 幂等守卫仅认 `run.completed(terminal_state=released)`；边界伪完成（terminal_state=boundary）不阻断 released 落地；`close_milestone` 不无限重发。
- **modules**：tracks/executor/milestone_chain.py。
- **关联**：FR-0327。

### IF-VERSION-001 制品声明版本绑定合同

- **合同**：§1r.2——version_scheme `bindings`（file/key/expect 数组）schema；`{release_tag}`/`{release_version}` 门供应占位符；feature/post_release 旅程逐绑定比对、错配指认 file/key 并 fail-closed；dev 旅程豁免；未声明时行为与 v0.9 一致；设计期升版与 registry 重锚纪律。
- **modules**：tracks/executor/host_contract.py, tracks/executor/verify_version.py, tracks/executor/guard_registry.py（digest 校验面复用）。
- **关联**：FR-0328。

### IF-HOTFIX-011 hotfix 前检分类映射合同

- **合同**：§1r.3——`issue_not_found` 仅承载确认缺失；`auth`/`rate_limit`/`network`/`missing_token` 映射 `issue_fetch_failed` 并携分类 next；REJECTED 可重试路径保持开启。
- **modules**：tracks/executor/hotfix_face.py, tracks/executor/hotfix.py, tracks/effects/github.py（分类来源）。
- **关联**：FR-0329。

### IF-TLS-001 live GitHub 通道 TLS 合同

- **合同**：§1r.4——全部 HTTPS 调用点显式 certifi CA bundle；`TRAC_GITHUB_CA_BUNDLE` 覆盖；失败分类可诊断；ops 文档前置节存在。
- **modules**：tracks/effects/github.py, docs/getting-started/installation.md（文档面）。
- **关联**：FR-0330。

### IF-TRACKER-001 tracker milestone 生命周期补全合同

- **合同**：§1r.5——`ensure_project_milestone`（list→create→readback、幂等复用、不假报 verified）；首次接触点接线于关闭链；不可自动补建时 `milestone_not_found` + 可操作 next + audited skip。
- **modules**：tracks/effects/github.py, tracks/executor/milestone_chain.py。
- **关联**：FR-0331。

### IF-REVIEW-001 LEX_REVIEW 合法 park 与线程复用合同

- **合同**：§1s——`lex.verdict` 追加 `pass-pending-human-threads`（携 pending_threads）；仅余 Human 待决线程时 park 到 AWAIT_HUMAN 不空转；`trac status` 渲染清单；`trac review` 恢复重评；LEX_REVIEW 派发注入 `open_threads`、findings 线程跨 attempt 复用不重复开。
- **modules**：tracks/effects/opencode_review.py, tracks/kernel/machine_verdicts.py, tracks/kernel/machine.py, tracks/cli/status_cmd.py, tracks/agents/Lex.md。
- **关联**：FR-0332。

### IF-WEBUI-001 UI e2e 通道合同

- **合同**：§1t——`ui` marker 通道隔离（默认排除、ui-e2e job 选择）；Playwright/Chromium 真实浏览器；`data-testid` 控件绑定；关键旅程不得以 API 替代 UI 操作；required_checks 追加 `ui-e2e`；浏览器下载与 job 分钟为独立基础设施预算。
- **modules**：tests/e2e（Shield 资产）, tests/_support（Shield 资产）, .github/workflows/ci.yml, pyproject.toml, .tracks/projects/project.toml。
- **关联**：NFR-0155。

### IF-GREENGUARD-001 到达即绿守卫节点合同

- **合同**：§1u——两类测试节点区分（合法 Red 验收锚点 / 到达即绿守卫）；test-plan §8.1 为守卫集合唯一权威；RED_CHECK 对守卫 pass 记 `guard_verified`、失败/缺席 fail-closed；守卫不作为 task 验收锚点声明；选窗纪律（r2 ∩ 合法 Red 锚点；守卫与 OOB 文件分别豁免）。
- **modules**：tracks/executor/test_execute.py（消费面，待实现 Devon）, tests/integration（守卫测试资产，Shield）。
- **关联**：AC-FR0325-02、AC-FR0328-02、AC-FR0329-01、AC-NFR0155-01/02 的守卫半边（M-TEST no-diff 复审 blocker 修订，2026-09-28）。
