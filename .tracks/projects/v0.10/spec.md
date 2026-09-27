---
envelope: tracks-envelope:v2
spec_id: SPEC-010
created: 2026-09-26
status: draft
sha:
---

# Web 工作台完善（UI）+ 发布卫生 — 需求规格

> 本 spec 将 v0.10 story（S-001）落为可验证需求：在 v0.9 已交付的 8 页面封闭集与完整 JSON API 之上叠加 SPA 工作台 chrome（登录名字绑定 + 文档中心应用层 + 可选 run 时间线），全程接真数据；并在同一版内收紧发布卫生（#182/#179/#180/#181）使 v0.10 自身可达 released。容量分析：15 有效 FR（FR-0316..FR-0330，承接项目全局序号，<30 无需拆分；4 NFR 不计）。继承边界：8 页面封闭集与查询/命令/SSE 契约、supervisor 单活动 run 串行 + hotfix 换队（FR-0296）、批准绑定（FR-0308）、preview 绑定（FR-0309）、受控重试位置（FR-0311）、命令生命周期与等待投影（v0.9 SM-01/SM-02/RP-01）全部继承，本版不重写；本版新增的仅是工作台交互壳、文档应用层与发布卫生修复。规格期锁定决策在此一次性确定：SPA 交互模型不妥协但构建链有条件（FR-0316）、功能项收敛七项（FR-0318）、tab 不跨会话持久化（FR-0318）、discussion 本版只读（FR-0325）、run 时间线整包取舍（FR-0326）、Playwright 基础设施为独立预算（NFR-0155）。

**技术选型与实现边界**（继承 story §5 约束 + Human 六条不可协商项）：SPA 技术选型优先原生 ES modules，不引入 npm/Vite 构建链；若 M-DESIGN 论证确需构建链，必须同包给出其与 guard-registry/CI required checks 的集成方案，否则维持原生。supervisor 单活动 run 串行 + hotfix 换队语义（FR-0296）不变，UI 不得发明并发推进入口。web 层不直接耦合 runtime，HTTP 改动经命令服务。Vditor 资产沿用已 vendor 同源（`server/static/vendor/vditor/`，可选引擎禁用），UI 只做应用层。服务端无 discussion 写回 API，本版 discussion 只读，写回留 CLI。docs/ 文档补写不在本版（排 v0.11）。

## 界面与入口

### E-01 工作台 shell（三段式）

```
┌──────┬──────────────┬────────────────────────────┐
│ tab条 │ sidebar      │ main area（多内容共存）     │
│ [P]   │ (只显当前tab │ 标题区直接显示内容标题       │
│ [R]   │  项下级内容， │ (文档标题/run标题/设置页标题)，│
│ [D]   │  例:选[D]显  │ 不显示tab条分类名；         │
│ [V]   │  文档树，    │ ┌──────────┬──────────┐    │
│ [T]   │  选[R]显run  │ │ Doc标题  │ Run标题× │ ← 主动关闭唯一路径 │
│ [⚙]   │  列表)       │ └──────────┴──────────┘    │
│ [👤]   │              │ 切换/开闭/保存不经整页刷新   │
│       │              │ 未知值显式降级，不空白       │
└──────┴──────────────┴────────────────────────────┘
```

sidebar 随 tab 条项切换只显该项下级内容，不同时罗列全部 tab 项（见 FR-0318）。main area 标题区直接显示内容标题（抽象“文档”标题：具体文档标题、run 标题、设置页标题等），不显示 tab 条分类名。Settings 经 `[⚙]` 只开 tab 不换 sidebar 内容；`[👤]` 点击弹菜单（结构槽位：settings / check updates / logout；v0.10 按 story 只实现 logout，settings 入口为 `[⚙]`，check updates 不做，见 FR-0318）。

> **Aaron:** main area 不显示 tab 的标题，而是直接显示『文档』（抽象含义，既包括具体的文档，也包括 run, setting page）的标题
>> **Sage:** 已采纳：E-01 wireframe 与 FR-0318 已修订为“标题区直接显示内容标题（文档标题/run标题/设置页标题等抽象文档标题），不显示 tab 条分类名，多内容以内容标题区分”。E-01 为示意，验收以 FR-0318 文字为准。请确认后由你设 resolved。

> **Aaron:** sidebar 在选择 tab 中的某个 item 之后，要相应切换为显示该 tab 下级内容，而不是像图中列出的对所有 tab item 的显示
>> **Sage:** 已采纳：E-01 原同时罗列全部 tab 项为示意错误，已改为“只显当前 tab 项下级内容（例：选[D]显文档树，选[R]显 run 列表）”，FR-0318 补充“sidebar 只显当前 tab 条项下级内容，不同时罗列全部 tab 项”，与 BS-03 联动原子性一致。请确认后由你设 resolved。

> **Aaron:** [👤]点击时，弹出菜单，目前包括 settings, check updates, logout (是否实现这些功能看 story 要求)
>> **Sage:** 已按 story 过滤采纳：E-01 与 FR-0318 明确 [👤] 结构槽位为 settings/check updates/logout，但 v0.10 按 story（BS-03/Account 仅 logout，Settings 经 [⚙]）只实现 logout；settings 入口为 [⚙]（菜单项若出现则同为打开 Settings tab，无独立行为），check updates 无 story BS、已列入范围排除本版不做。请确认后由你设 resolved。

### E-02 登录双栏 auth shell + 名字采集

```
┌──────────────────────┬──────────────────────────────┐
│ hero 图 + 标语        │ 登录面板                     │
│ 图片署名              │ 凭据字段 + [登录]            │
│ (移动端折叠为单栏)    │ 错误行内展示                 │
│                      │ 初次登录→名字采集步：         │
│                      │ 显示名 [____] [继续]         │
└──────────────────────┴──────────────────────────────┘
```

### E-03 文档中心（Vditor ir + 分屏 + discussion 导航）

```
┌────────────┬─────────────────────────────────────────┐
│ 目录树     │ pane1 [doc选择器|工具栏]  pane2 […] (≤4列)│
│ (版本逆序， │ ┌─────────────────────────────────────┐ │
│ 最新置顶)  │ │ Vditor ir 即时渲染 viewed/edited    │ │
│ story/spec │ │ [保存](dirty才可用) 409→[重载放弃|强制覆盖]│ │
│ acceptance │ │ discussion: [显示/隐藏][下一个][只看未决]│ │
│ …          │ └─────────────────────────────────────┘ │
│            │ Vditor失败→textarea回退，内容不丢       │
└────────────┴─────────────────────────────────────────┘
```

目录树按版本号逆序排列，最新版本置顶（见 FR-0322）。

> **Aaron:** 文档中心按版本号逆序显示，最新的排在最上面。
>> **Sage:** 已采纳：E-03 目录树标注“版本逆序、最新置顶”并补充说明，FR-0322 增加“目录树按版本号逆序排列，最新版本置顶”为规范要求，E-03 为示意、验收以 FR-0322 为准。请确认后由你设 resolved。

### E-04 run 时间线（可选项，整包）

```
┌─────────────────────────────────────────────────┐
│ 当前节点卡片：责任方/attempt/运行时长/唯一主要动作 │
│ M-START ●─●─●─◆活跃前后若干─● (attempt不折叠)      │
│         ╰──回拨回边（可辨认）                     │
│ 点节点→浮层：起止时间/artifact/revision→跳文档中心 │
└─────────────────────────────────────────────────┘
```

## 状态与生命周期

### SM-04 工作台 tab 集合（v0.10 新增，浏览器内存）

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-04.2）。

1. （切换 tab 条项）→ `已打开`：sidebar 切到对应导航树且 main area 打开或激活对应 tab，两动作在同一切换内完成，不存在只换 sidebar 不开 tab 的中间态（FR-0318）
2. `已打开` → `已激活`：重复点击同一 tab 条项激活已有唯一实例，不新建重复实例（FR-0318）
3. `已激活` → `已打开`（失活但共存）：切换到其它 tab 时原 tab 保留上下文，不被切换关闭（FR-0318）
4. `已打开`/`已激活` → `已关闭`：用户主动关闭是唯一关闭路径；无关闭所有全局动作（FR-0318）
5. 刷新/重启浏览器 → `空集合`：tab 集合不跨会话持久化，为预期行为而非丢失（FR-0318）
6. Settings 项 → `已打开 Settings tab`：只开 tab，不改变 sidebar 内容（FR-0318）

### SM-05 文档保存与冲突（v0.10 新增）

1. （浏览）→ `干净`：内容与服务端 revision 一致，保存不可用（FR-0323）
2. `干净` → `脏`：用户编辑后未保存，保存可用（FR-0323）
3. `脏` → `保存中`：用户点击显式保存，经 edit_material 通道提交 base_revision（FR-0323）
4. `保存中` → `干净`：服务端接受并返回新 revision，UI 切换到新 revision 基线（FR-0323）
5. `保存中` → `冲突`：服务端返回 409（revision/mtime 冲突），用户在重载放弃与强制覆盖两选项中选择（FR-0323）
6. `冲突` → `干净`：用户选重载放弃，编辑器重载服务端版本，放弃本地改动（FR-0323）
7. `冲突` → `保存中`：用户选强制覆盖，以服务端最新基线重提（FR-0323）
8. `保存中`/`冲突` → `脏`（写失败保留）：网络或服务端失败时编辑内容不丢，可重试（FR-0323）

## 角色与权限

### RP-01 本机单用户边界（v0.10 继承，无新增角色）

继承 v0.9 RP-01 全部矩阵与单写者纪律（Runtime 是唯一流程/副作用 authority；认证 Human 经 UI 提交命令，Supervisor/Worker 只受理代转与落实转移，Agent 不产生人工决定、不伪造批准）。本版不新增角色、不引入注册/多账号（story Out-of-Scope）。名字绑定（FR-0321）是同一单用户的身份标注，不改变该边界：名字改变 actor 字段的显示值，不创建第二身份；logout 后凭据清空。discussion RESOLVED 仅发起人（FR-0325）沿用既有裁决语义，不新增裁决角色。

## 功能需求

### FR-0316 SPA 工作台 shell 与无整页刷新

- **来源**：`BS-01` / `§3.1` / 约束 D1
- **交付入口**：`E-01`（工作台 shell，覆盖 `/`、`/projects`、`/runs/{id}` 等既有页面入口的 SPA 叠加）

WHEN 认证用户在工作台内切换功能区、开闭 tab、切换文档或保存，THE 系统 SHALL 在单页内完成切换与保存，不经整页刷新；三段式布局（左侧垂直 tab 条 + 多级 sidebar + 多 tab main area）在所有上下文相对位置一致。技术选型优先原生 ES modules，不引入 npm/Vite 构建链；若 M-DESIGN 论证确需构建链，MUST 同包给出其与 guard-registry/CI required checks 的集成方案，否则维持原生。

**用户可观察结果**：功能区切换、tab 开闭、文档切换、保存均无整页白闪与上下文丢失，用户可继续进入文档编辑或 run 详情或返回总览。

### FR-0317 左侧 tab 条固定语义

- **来源**：`BS-02` / `§3.1` / 约束 D3（引 louke v0.13-001）
- **交付入口**：`E-01` 左侧垂直 tab 条

WHEN 用户查看左侧 tab 条，THE 系统 SHALL 仅以图标加 hover tooltip 呈现，命中区不小于 32px，图标顺序固定且不支持拖拽重排。

**用户可观察结果**：tab 条在所有上下文外观与顺序一致，无拖拽手柄与顺序变化。

### FR-0318 Sidebar 联动与 tab 共存

- **来源**：`BS-03` / `§3.1` / 约束 D3（引 louke v0.13-001）
- **交付入口**：`E-01`（sidebar 导航树 + main area）

WHEN 用户切换 tab 条项，THE 系统 SHALL 在同一切换内将 sidebar 切到对应导航树并在 main area 打开或激活对应 tab（SM-04.1），不存在只换 sidebar 不开 tab 的中间态；sidebar 只显当前 tab 条项的下级内容，不同时罗列全部 tab 项；main area 标题区直接显示内容标题（抽象“文档”标题：具体文档标题、run 标题、设置页标题等），不显示 tab 条分类名，多内容以内容标题区分。已打开 tab 共存不被切换关闭，重复点击激活已有唯一实例（SM-04.2/SM-04.3），用户主动关闭是唯一关闭路径，无关闭所有全局动作（SM-04.4），tab 集合只在浏览器内存、不跨会话持久化（SM-04.5）。功能项集合收敛为 Projects/Runs/Docs/Review/Todos/Settings/Account 七项：overview 与 projects 合并为 Projects/Runs 总览语义，run_new 收为 Runs 下新建入口，release 收为 run 详情内发布决定而非顶级项。Settings 经 `[⚙]` 只开 tab 不换 sidebar 内容（SM-04.6）；`[👤]` 点击弹菜单，结构槽位为 settings / check updates / logout，v0.10 按 story 只实现 logout（logout 后浏览器不残留凭据），settings 入口为 `[⚙]`（菜单 settings 项若出现则同为打开 Settings tab，无独立行为），check updates 无 story BS、不在本版实现。

**用户可观察结果**：Projects/Runs/Docs/Review/Todos 间切换不丢上下文；刷新后 tab 不恢复为预期行为；Settings/Account 行为符合上述约束。

### FR-0319 未知值显式降级

- **来源**：`BS-04` / `§3.1` / 约束 D3（引 louke v0.13-001）
- **交付入口**：`E-01`（sidebar 树节点、main 内容、tab 标签全覆盖）

WHEN sidebar 树节点、main 内容或 tab 标签遇到未知或缺失值，THE 系统 SHALL 显式降级展示（可读 NotFound/未知标记），不崩溃、不 500、不留空白 tab。

**用户可观察结果**：枚举漂移或缺失数据时用户看到明确降级标记，可继续导航。

### FR-0320 登录双栏与记住态

- **来源**：`BS-05` / `§3.2` / 约束 D2
- **交付入口**：`E-02` + `POST /api/auth/login` + `POST /api/auth/logout`（既有认证面叠加）

WHEN 用户打开登录页，THE 系统 SHALL 呈现双栏 auth shell（左侧 hero 图加标语加图片署名，右侧登录面板；错误行内展示；移动端折叠为单栏），并以持久 cookie 承载记住登录态（HttpOnly + SameSite=Strict + Path=/，7 天滚动窗口语义不变，不另设记住复选框）。未认证访问工作台 SHALL 302 到 `/login`；logout SHALL 删除服务端会话且浏览器不残留凭据。

**用户可观察结果**：记住态跨浏览器重启有效，可直接进入工作台真数据浏览；logout 后需重新登录。

### FR-0321 名字采集与端到端绑定

- **来源**：`BS-06` / `§3.2` / 约束 D2 + RP-01
- **交付入口**：`E-02` 名字采集步 + `E-01`/`E-03` 会话称谓可观察出口 + 事件/assignment/discussion 可观察出口（`human.anchor`/`human.approval` 审计、`agent assignment` 称谓、讨论 speaker 标签）

WHEN 服务端尚无持久化名字且用户首次登录成功，THE 系统 SHALL 要求输入名字（显示名）并持久化在服务端与会话绑定，完成前不进入工作台数据面；此后该名字 SHALL 用于与 agent 的对话称谓、事件 actor 字段（`human.anchor`/`human.approval` 审计）、agent assignment 称谓与讨论 speaker 标签，不只是登录页装饰字段。本机单用户边界不变，不引入注册或多账号入口。

**用户可观察结果**：新用户登录后一次采集名字，此后在会话、审计事件、assignment 与 discussion 中均看到同一名字；无第二账号概念。

### FR-0322 文档中心查看宿主与编辑器韧性

- **来源**：`BS-07` 部分 / `BS-11` / `§3.3` / 约束 D4
- **交付入口**：`E-03` + `GET /api/runs/{run_id}/docs/{doc}` + `GET /api/runs/{run_id}/docs/{doc}/diff`（既有文档查询面应用层）

WHEN 用户经目录树选择 `.tracks/projects/<version>/` 六件套文档（story/spec/acceptance/architecture/interfaces/test-plan），THE 系统 SHALL 以 Vditor `ir` 即时渲染模式承载浏览与编辑，资产经同源 vendor 路径加载（可选引擎保持禁用）；目录树按版本号逆序排列，最新版本置顶。WHEN Vditor 加载失败，THE 系统 SHALL 回退到 textarea，保证文档仍可查看与编辑（SM-05 之外的主路径韧性）。

**用户可观察结果**：文档以即时渲染呈现；Vditor 不可用时用户仍可用 textarea 继续查看与编辑。

### FR-0323 显式保存经修订通道与冲突恢复

- **来源**：`BS-07` / `BS-08` / `§3.3` / 约束 D4 + FR-0308
- **交付入口**：`E-03` 保存控件 + `POST /api/runs/{run_id}/docs/{doc}/edits`（既有 edit_material 通道）

WHEN 用户在文档中心编辑并点击显式保存（SM-05.3），THE 系统 SHALL 经既有 edit_material 修订通道提交（含用户审阅所基于的 `base_revision`）并产生新 revision，不绕过批准绑定（FR-0308）；保存按钮仅在 dirty 时可用（SM-05.1/SM-05.2）。WHEN 服务端以 409 拒绝（revision/mtime 冲突，SM-05.5），THE 系统 SHALL 向用户提供重载放弃与强制覆盖两选项（SM-05.6/SM-05.7），不静默覆盖；写失败时编辑内容不丢（SM-05.8）。

**用户可观察结果**：保存产生新 revision 并切换基线；冲突时用户二选一恢复；失败不丢草稿。此即首个可演示里程碑后半段。

### FR-0324 多 pane 分屏独立

- **来源**：`BS-09` / `§3.3` / 约束 D4
- **交付入口**：`E-03` 分屏容器

WHEN 用户打开多 pane 分屏，THE 系统 SHALL 支持至多 4 列，每 pane 独立文件选择器与工具栏并可独立加载不同文档对照。

**用户可观察结果**：用户可并排对照不同文档，各 pane 选择与编辑互不干扰。

### FR-0325 inline discussion 读与导航边界

- **来源**：`BS-10` / `§3.3` / 约束 D4（服务端无写回 API）
- **交付入口**：`E-03` discussion 覆盖层（读/导航） + CLI（写回 resolve/reply，既有通道）

WHEN 用户使用 inline discussion，THE 系统 SHALL 提供显示或隐藏切换、下一个 discussion、只看未决过滤，且 RESOLVED 语义仅发起人；本版只做读与导航，写回（resolve/reply）留 CLI，客户端不手写协议拼接。如评审后认为必须 UI 写回，MUST 单独提案加 mutate 端点。

**用户可观察结果**：用户可定位并过滤未决讨论；UI 上无 resolve/reply 写入口。

### FR-0326 run 详情 13 阶段时间线（可选项，整包取舍）

- **来源**：`BS-12` / `§3.4` / 约束 D5
- **交付入口**：`E-04` + `GET /api/runs/{run_id}/timeline` + `GET /api/runs/{run_id}/ac-chain`（既有数据源，纯前端加投影建模）

WHEN run 详情时间线进入本版，THE 系统 SHALL 呈现顶部当前节点卡片（责任方、attempt、运行时长、唯一主要动作）加 13 阶段线性时间线（每次 attempt 独立节点不折叠，回拨用可辨认回边，视口聚焦活跃节点前后若干节点），点节点打开详情浮层（起止时间与 artifact/revision 并可跳文档中心）。若 spec 冻结时容量不够则整包延后 v0.11，不做部分交付；在 spec 冻结时明确取舍。

**用户可观察结果**：用户可解释当前节点与历史 attempt 及回拨关系，并跳转到对应文档或操作入口；或该包整体不在本版。

### FR-0327 里程碑收尾 released 可达

- **来源**：`BS-16` / `§3.6` / #182 blocker
- **交付入口**：M-MILESTONE 收尾命令面（`_complete_milestone` + `close_milestone`） + `run.completed(terminal_state=released, release_tag)` 事件可观察出口

WHEN M-MILESTONE 收尾执行 `_complete_milestone`，THE 系统 SHALL 区分 M-IMPL 闭包边界的 `run.completed(boundary)` 伪完成与真正的 released 终态：幂等守卫不得将边界伪完成误认为收尾完成，使 released 可达且 `close_milestone` 不无限重发熔断。不修则 v0.10 自身发布到不了 released，必须先修。

**用户可观察结果**：发布者走完收尾看到 released 终态落地；不再出现 close 重发熔断。

### FR-0328 制品声明版本门

- **来源**：`BS-17` / `§3.6` / #179
- **交付入口**：发布校验 version gate（宿主合同门） + `pyproject.toml project.version` 与 `tracks.__version__` 与发布 tag 一致性可观察出口

WHEN 发布制品时，THE 系统 SHALL 要求 `pyproject.toml project.version` 与 `tracks.__version__` 与发布 tag 一致；宿主合同 version gate SHALL 声明 file/key/expect 绑定并处理 guard-registry `config_digest` 重锚（先例 e5637a1），版本号 bump 到发布版本。

**用户可观察结果**：版本不一致时发布校验明确拒绝并指认错配的 file/key；一致时放行。

### FR-0329 hotfix 前检分类映射与恢复指引

- **来源**：`BS-18` / `§3.6` / #180
- **交付入口**：hotfix 入口前检（`precheck_hotfix` / `precheck_hotfix_report`，CLI + 服务端共用） + 前检失败反馈可观察出口

WHEN hotfix 前检捕获 `GithubIssuesError`，THE 系统 SHALL 按分类区分映射并给出对应恢复指引，不再一律吞成 `issue_not_found`：真正缺失才报 `issue_not_found`，抓取失败（auth/rate_limit/network/missing_token 等）报 `issue_fetch_failed` 并保持重试路径开启，每类携带可操作 `next` 指引。注释承诺的 `issue_fetch_failed` 分类不可达问题在本版消除。

**用户可观察结果**：用户看到分类明确的前检拒绝原因与下一步操作，而非笼统的 issue 不存在。

### FR-0330 live GitHub 通道 TLS 与环境前置

- **来源**：`BS-18` / `§3.6` / #181
- **交付入口**：live GitHub 请求层（`effects/github.py` `_urlopen`/`_request`/`_get`） + ops 文档 live 旅程环境前置节可观察出口

WHEN live GitHub 通道在标准 macOS Python 下请求时，THE 系统 SHALL 显式使用 certifi CA bundle 发起 TLS 校验，恢复 live 通道；并将 live 旅程环境前置（`GITHUB_TOKEN`/`TRAC_GITHUB_REPO`/TLS）写进 ops 文档。失败时按 `GithubIssuesError` 分类可诊断呈现。

**用户可观察结果**：标准 macOS Python 下 live 通道不再 TLS 失败；缺环境变量时用户按 ops 文档指引修复后重试。

## 非功能需求

### NFR-0153 无 mock 数据与可观察失败

- **来源**：`BS-13` / `§3.5` / 约束 D6

WHEN UI 呈现任何状态，THE 系统 SHALL 只消费服务端查询投影与事件流，不以 mock 数据伪造；API 失败时 SHALL 产生用户可观察的失败反馈，不伪报成功。服务端是唯一权威，UI 缓存不得改变状态事实。

### NFR-0154 事件韧性与写安全

- **来源**：`BS-14` / `§3.5` / 约束 D6（v0.9 已具备，UI 层遵守同一契约）

WHEN SSE 断线或事件重复乱序，THE 系统 SHALL 按序号（snapshot `event_cursor` 与流 cursor 可互换）补读且不使 UI 倒退；WHEN 用户提交写操作，THE 系统 SHALL 遵守既有 CSRF（`X-Trac-CSRF`）与 `Idempotency-Key` 契约；秘密经既有 redaction 投影后展示。

### NFR-0155 测试分层与控件绑定及基础设施预算

- **来源**：`BS-15` / `§3.5` / 约束 D6

验证本版 SHALL 分 API e2e 与 UI e2e（Playwright/Chromium）两层，UI e2e 不得以 API 请求替代关键 UI 操作，控件以 `data-testid` 绑定，不依赖 CSS class/DOM 层级；Playwright 浏览器下载与 CI 新 required check 计入独立基础设施预算并在 test-plan/architecture 中显式声明，不挤占 FR 预算。

### NFR-0156 构建链门禁集成条件

- **来源**：约束 D1 / `§5`

若 M-DESIGN 引入 npm/Vite 构建链，MUST 同包给出其与 guard-registry/CI required checks 门禁体系的集成方案（资产可审计、无第二工具链冲击门禁）；否则维持原生 ES modules。 louke 否决构建链的理由供参考，不构成独立功能需求。

## 范围排除

- Chat/Agent 会话流、模型绑定页、质量面板、i18n、setup 向导/onboarding 门禁、Settings 完整三页、跨会话 tab 持久化、toolbar 拖拽重排、WebSocket、多用户/公网边界（story 明确排除）；`[👤]` 菜单的 check updates（版本检查/更新提示）无 story BS，本版不做。
- discussion UI 写回（resolve/reply）在本版不做，留 CLI；单独提案前不加 mutate 端点。
- docs/ 文档补写排 v0.11。
- run 时间线若 spec 冻结时容量不够则整包延后 v0.11（FR-0326），不做部分交付。

> **Lex:** Lex 语义复核（attempt 3，消费 1-lex-1）：重读 spec 全文对照 story S-001。T-001（标题区显示内容标题）/T-002（sidebar 只显当前项下级）/T-003（[👤] 仅 logout，check updates 已入范围排除）/T-004（目录树版本逆序置顶）的 Sage 修订与 BS-03/BS-07/BS-11 及 D3/D4 一致，无矛盾、无范围偏移、无路径断裂；18 BS 全部有合同，主路径与 Out-of-Scope 完整，Lex 无修订要求。ready 仅待 Human 确认 T-001~T-004（resolved 权限在发起人 Aaron）。
>> **Sage:** 确认收到 Lex attempt 3 复核（消费 1-lex-1）：T-001~T-004 修订与 BS-03/BS-07/BS-11 及 D3/D4 一致、无矛盾与范围偏移，Sage 本轮无进一步 spec 修订（15 FR + 4 NFR 不变）。T-001~T-004 待 Human（Aaron）确认设 resolved；本线程请 Lex 设 resolved。

> **Lex [RESOLVED]:** Lex 语义复核（RESPOND）：已重读 spec 全文并对照 story S-001。T-001（标题区显示内容标题）/T-002（sidebar 只显当前项下级）/T-003（[👤] 仅 logout，check updates 已入范围排除）/T-004（目录树版本逆序置顶）的 Sage 修订与 BS-03/BS-07/BS-11 及约束 D3/D4 一致，未引入矛盾、范围偏移或路径断裂；18 BS 全部有合同，主路径与 Out-of-Scope 完整，Lex 无修订要求。ready 仅待 Human 确认：请确认 T-001~T-004 后由你设 resolved，本线程在你回复后由 Lex 设 resolved。
>> **Sage:** 确认收到 Lex 语义复核：T-001~T-004 修订与 BS-03/BS-07/BS-11 及 D3/D4 一致、无矛盾与范围偏移，Sage 本轮无进一步 spec 修订（15 FR + 4 NFR 不变，交付面与 Out-of-Scope 保持）。T-001~T-004 待 Human 确认设 resolved；本线程请 Lex 设 resolved。
