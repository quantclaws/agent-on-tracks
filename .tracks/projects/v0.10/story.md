---
envelope: tracks-envelope:v2
story_id: S-001
title: Web 工作台完善（UI）+ 发布卫生
created: 2026-09-26
status: draft
sha:
---

# S-001: Web 工作台完善（UI）+ 发布卫生

## 1. 原始输入

> v0.10 主题：Web 工作台完善（UI）+ 发布卫生。
>
> 背景与调研输入：.tracks/projects/planning/v0.10-ui-research.md（louke spec 谱系 v0.8–v0.17 的 UI 演进调研与 tracks v0.9 现状盘点，作为 story 素材）。
>
> Human 已定方向（六条，不可协商项）：
> 1. SPA 架构：单页应用交互模型，功能区切换/tab 开闭/文档切换/保存不经整页刷新。约束：技术选型优先原生 ES modules（不引入 npm/Vite 构建链）；若 M-DESIGN 论证确需构建链，必须同时给出其与 guard-registry/CI required checks 的集成方案（louke 否决构建链的原因就是避免第二工具链冲击门禁体系）。
> 2. 登录与身份：沿用 louke 现有登录 UI 设计（双栏 auth shell：hero 图 + 登录面板，见 louke/web/app.py:_login_shell）；允许记住登录态（持久 cookie 承载）；初次登录要求输入名字并持久化，该名字此后用于与 agent 的对话与留痕——AC 必须端到端绑定：名字出现在会话、事件 actor 字段（human.anchor/human.approval 审计）、agent assignment 称谓与讨论 speaker 标签，不能只是登录页装饰字段。维持本机单用户边界（RP-01），不引入注册/多账号。
> 3. Workbench 结构（固定为需求，引 louke v0.13-001 条款）：左侧垂直 tab 条（仅图标+tooltip，命中区≥32px，顺序固定不可拖拽重排）+ 多级 sidebar + 多 tab main area；切换 tab 条项时 sidebar 切到对应导航树且 main area 打开/激活对应 tab；已打开 tab 共存不被切换关闭，重复点击激活已有唯一实例，用户主动关闭是唯一关闭路径，tab 集合不跨会话持久化；Settings 只开 tab 不换 sidebar，Account 菜单仅 logout；未知/缺失值显式降级不崩溃不空白。tracks 的功能项集合在 story 阶段收敛（候选：Projects/Runs/Docs/Review/Todos/Settings/Account）。
> 4. 需求文档中心：Vditor ir 即时渲染的查看/编辑（v0.9 已 vendor 资产，本版补应用层），编辑经既有 edit_material 修订通道产生新 revision、不绕过批准绑定（FR-0308），显式保存 + 乐观并发 409（重载放弃/强制覆盖两选项）；多 pane 分屏（≤4 列，pane 独立文件选择器与工具栏）；inline discussion 呈现与导航（显示/隐藏、下一个、只看未决），RESOLVED 仅发起人。注意：服务端目前没有 discussion 写回 API——v0.10 的 discussion 只做读/导航，写回（resolve/reply）留 CLI；如评审后认为必须 UI 写回，单独提案加 mutate 端点，不手写协议拼接。Vditor 加载失败回退 textarea。
> 5. run 详情（13 阶段时间线，上卡下图：当前节点卡片 + 线性时间线 attempt 不折叠 + 回拨回边 + 节点详情浮层）为可选项：spec 容量不够则整包延后 v0.11，在 spec 冻结时明确取舍。数据源（timeline/ac-chain API）v0.9 已具备。
> 6. 接真数据作为 NFR 纳入：无 mock 数据、无假成功（API 失败必须产生用户可观察的失败反馈）；服务端唯一权威，UI 只消费投影/事件流；测试分 API e2e 与 UI e2e（Playwright/Chromium）两层，UI e2e 不得以 API 请求替代关键 UI 操作，控件用 data-testid 绑定；SSE 断线按序号补读，重复/乱序事件不使 UI 倒退；写操作统一 CSRF + Idempotency-Key（v0.9 已具备）。注意 Playwright 的浏览器下载/CI 新 required check 是独立基础设施预算，要在 spec 中显式计入。
>
> 发布卫生（并入本版）：
> - #182（blocker，必须先修）：M-MILESTONE 收尾死锁——_complete_milestone 的幂等守卫 any(run.completed) 误认 M-IMPL 闭包边界的 run.completed(boundary) 伪完成，导致 released 终态永不落地、close_milestone 无限重发熔断。不修则 v0.10 自己的发布也到不了 released 终态。
> - #179：制品声明版本（pyproject version 与 tracks.__version__）必须与发布 tag 一致——新增 AC，宿主合同 version gate 声明 file/key/expect 绑定（处理 guard-registry config_digest 重锚，先例 e5637a1），并把版本号 bump 到发布版本。
> - #180：hotfix precheck 把 GithubIssuesError 一律吞成 issue_not_found（注释承诺的 issue_fetch_failed 分类不可达），按分类区分映射并给出对应恢复指引。
> - #181：live GitHub 通道在标准 macOS Python 下 TLS 校验失败（gh/git 正常），effects/github.py 请求层显式使用 certifi CA bundle，并把 live 旅程环境前置（GITHUB_TOKEN/TRAC_GITHUB_REPO/TLS）写进 ops 文档。
>
> 明确排除（v0.10 不做）：Chat/Agent 会话流、模型绑定页、质量面板、i18n、setup 向导/onboarding 门禁、Settings 完整三页、跨会话 tab 持久化、toolbar 拖拽重排、WebSocket、多用户/公网边界。
>
> 既有事实约束（继承）：8 页面封闭集与完整 JSON API 已在 v0.9 交付；supervisor 单活动 run 串行 + hotfix 换队语义（FR-0296）不变，UI 不得发明并发推进入口；web 层不直接耦合 runtime，HTTP 改动经命令服务；docs/ 文档补写不在本版（排 v0.11）。容量参照：v0.9 FR 预算 28。
>
> 首个可演示里程碑建议：一个新用户从浏览器登录，看到真实的项目/run/文档数据，并能在文档中心编辑一份需求文档产生新 revision。

## 2. 用户意图

- **用户想完成什么**：把 v0.9 交付的 8 个服务端渲染壳与完整 JSON API 推进为可用的单页工作台——登录记住并绑定名字，左侧 tab 条加多 tab 导航共存，文档中心用 Vditor 即时渲染查看并编辑产生新 revision（run 时间线视容量可选），全程接真数据无假成功；并在同一版内收紧发布卫生（#182/#179/#180/#181），让 v0.10 自己的发布能走到 released 终态。
- **当前哪里受阻**：v0.9 页面是孤立整页刷新的壳，无应用 JS、无导航共存；登录页只有密码字段且 actor 固定为 `local-user`，Vditor 资产已 vendor 但无应用层，timeline/ac-chain 有 API 但无可视化；另 M-MILESTONE 收尾死锁使任何发布到不了 released，版本声明、预检分类、live TLS 各有缺口。
- **完成后能看到什么结果**：一个新用户从浏览器登录，看到真实的项目、run 与文档数据，并在文档中心编辑一份需求文档产生新 revision（首个可演示里程碑）；发布卫生落地后版本门、预检指引与 live 通道恢复，v0.10 自身可发布。

## 3. 核心操作路径

### 3.1. SPA 工作台外壳与导航

- **变更基线**：修改 — v0.9 为 8 页面封闭集（`tracks/server/pages.py:PAGES`：login/overview/projects/run_new/run_detail/review/todos/release）的服务端渲染孤立页，经整页导航切换，无应用 JS、无 tab 共存、无 sidebar 联动。本次改为 SPA 交互模型，在既有页面与 API 语义上叠加 workbench chrome。
- **入口/触发**：浏览器打开工作台任意入口（`/`、`/projects`、`/runs/{id}` 等），经认证后进入 workbench shell。

1. 用户看到三段式布局：左侧垂直 tab 条、对应 sidebar 导航树、多 tab main area，三区相对位置在所有上下文一致。
2. 用户切换 tab 条项，sidebar 在同一切换内切到对应导航树且 main area 打开或激活对应 tab，不存在只换 sidebar 不开 tab 的中间态；功能区切换、tab 开闭、文档切换、保存均不经整页刷新。
3. 用户重复点击同一 tab 条项时激活已有唯一实例，已打开 tab 共存不被切换关闭；用户主动关闭是唯一关闭路径，无关闭所有全局动作；tab 集合只在浏览器内存，不跨会话持久化。
4. 用户打开 Settings 只开 tab 不换 sidebar 内容，用户点 Account 菜单仅见 logout，logout 后浏览器不残留凭据。

- **完成结果**：用户在 Projects/Runs/Docs/Review/Todos 间切换不丢上下文，可继续进入文档编辑（3.3）或 run 详情（3.4）或返回总览；刷新后 tab 集合不恢复为预期行为而非丢失；未知或缺失值显式降级展示。
- **功能项收敛（重要推导）**：本 story 收敛为 Projects/Runs/Docs/Review/Todos/Settings/Account 七项（seed 候选原样采用）。依据：v0.9 的 overview 与 projects 合并为 Projects/Runs 总览语义，run_new 收为 Runs 下新建入口，release 收为 run 详情内发布决定而非顶级项，review/todos 对应 Review/Todos，Docs 为文档中心新增顶级入口；Settings/Account 为 chrome 项。

### 3.2. 登录与名字绑定

- **变更基线**：修改 — v0.9 登录页为功能壳（`pages.py:_login_body` 仅密码字段），actor 取 auth 表首行或 `_LOCAL_ACTOR_FALLBACK=local-user`（`server/app.py`），会话为 7 天滚动 HttpOnly `SameSite=Strict` cookie。本次沿用 louke 双栏 auth shell 设计并加入名字绑定。
- **入口/触发**：未认证访问工作台被 302 到 `/login`，或用户直接打开登录页。

1. 用户看到双栏 auth shell：左侧 hero 图加标语加图片署名，右侧登录面板；错误行内展示；移动端折叠为单栏。
2. 用户输入凭据登录，记住登录态由持久 cookie 承载（不另设复选框）；会话保持 7 天滚动窗口语义不变。
3. 用户初次登录被要求输入名字（显示名），名字持久化在服务端并与会话绑定。
4. 用户此后在工作台活动，其名字出现在与 agent 的对话称谓、事件 actor 字段（human.anchor/human.approval 审计）、agent assignment 称谓与讨论 speaker 标签。

- **完成结果**：记住态跨浏览器重启有效，用户可继续进入工作台真数据浏览（3.1/3.5）；logout 后凭据清空；本机单用户边界不变，不出现注册或多账号入口。

### 3.3. 需求文档中心查看与编辑

- **变更基线**：修改 — v0.9 的 review 页已 vendor Vditor 同源资产（`server/static/vendor/vditor/`）并提供查看与基础编辑加版本对比壳，经 `POST /api/runs/{run_id}/docs/{doc}/edits`（edit_material）写回；无多 pane、无 discussion 导航、无显式保存加 409 两选项的应用层。本次补应用层，不换修订通道。
- **入口/触发**：Docs 顶级导航或 run 详情跳文档；对象为 `.tracks/projects/<version>/` 六件套（story/spec/acceptance/architecture/interfaces/test-plan）。

1. 用户经目录树选择文档，以 Vditor `ir` 即时渲染模式浏览与编辑。
2. 用户显式点击保存才写回，保存按钮仅在 dirty 时可用；编辑经既有 edit_material 修订通道产生新 revision，不绕过批准绑定（FR-0308）。
3. 用户遇到 revision/mtime 冲突收到 409，在重载放弃与强制覆盖两选项中选择；写失败时编辑内容不丢。
4. 用户可开多 pane 分屏对照（最多 4 列，每 pane 独立文件选择器与工具栏，独立加载不同文档）。
5. 用户使用 inline discussion 读与导航：显示或隐藏切换、下一个 discussion、只看未决过滤；RESOLVED 语义仅发起人；本版 discussion 只做读与导航，写回（resolve/reply）留 CLI。

- **完成结果**：编辑闭环产生新 revision，用户可继续审阅并提交批准或返回列表；此即首个可演示里程碑的后半段。

### 3.4. run 详情 13 阶段时间线（可选项，整包取舍）

- **变更基线**：新增 — v0.9 已交付数据源（`GET /api/runs/{id}/timeline`、`GET /api/runs/{id}/ac-chain`），无前端可视化。本次为纯前端加投影建模工作。
- **入口/触发**：Runs 列表进入 run 详情，用户选择时间线视图。

1. 用户在顶部看到当前节点卡片（责任方、attempt、运行时长、唯一主要动作）。
2. 用户沿 13 阶段线性时间线浏览，每次 attempt 独立节点不折叠，回拨用可辨认回边，视口聚焦活跃节点前后若干节点。
3. 用户点节点打开详情浮层，看到起止时间与 artifact/revision 并可跳文档中心。

- **完成结果**：用户可解释当前节点与历史 attempt 及回拨关系，并跳转到对应文档或操作入口；若 spec 冻结时容量不够则整包延后 v0.11，不做部分交付。

### 3.5. 真数据浏览、失败可见与事件韧性

- **变更基线**：修改 — v0.9 的非 review 页为 `data-page` 占位壳，无应用 JS，真数据 NFR 未在 UI 层兑现。本次让 UI 只消费既有查询投影与事件流，不新增数据源。
- **入口/触发**：任何数据面（总览、run 详情、文档、待办）正常浏览，以及 API 失败或 SSE 断线时。

1. 用户看到的任何 UI 状态均来自真实 API 与事件流，无 mock 数据；API 失败产生用户可观察的失败反馈，不伪报成功。
2. 用户浏览器断线重连后，UI 按序号补读事件，重复或乱序事件不使 UI 倒退。
3. 用户的写操作遵守既有 CSRF 与 `Idempotency-Key` 契约（v0.9 已具备，UI 层遵守同一契约）；秘密经既有 redaction 投影后展示。

- **完成结果**：用户可区分等待外部、失败与待人工，重连后看到单调一致的投影，可继续操作或按失败指引恢复。

### 3.6. 发布卫生：收尾死锁与制品通道修复

- **变更基线**：修改 — v0.9/v0.8 遗留四项：`milestone_chain.py:_complete_milestone` 的 `any(run.completed)` 幂等守卫误认 M-IMPL 闭包边界的 `run.completed(boundary)` 伪完成，released 终态永不落地且 close_milestone 无限重发熔断；`pyproject.toml version`（0.8.0）与 `tracks.__version__` 与发布 tag 不一致且无 version gate；hotfix precheck 把 `GithubIssuesError` 一律映射为 `issue_not_found`；`effects/github.py` 请求层未显式使用 certifi CA bundle，标准 macOS Python 下 TLS 校验失败。
- **入口/触发**：M-MILESTONE 收尾、发布校验、hotfix 入口前检、live GitHub 旅程。

1. 用户（发布者）走完 M-MILESTONE 收尾不再死锁：幂等守卫区分闭包边界伪完成与真正的 released 终态，released 可达；close_milestone 不再无限重发熔断（#182 blocker，必须先修）。
2. 制品声明版本与发布 tag 一致：宿主合同 version gate 声明 file/key/expect 绑定并处理 guard-registry `config_digest` 重锚，版本号 bump 到发布版本（#179）。
3. hotfix 前检按 `GithubIssuesError` 分类区分映射并给出对应恢复指引，不再一律吞成 `issue_not_found`（#180）。
4. live GitHub 通道在标准 macOS Python 下恢复：请求层显式使用 certifi CA bundle，live 旅程环境前置（GITHUB_TOKEN/TRAC_GITHUB_REPO/TLS）写进 ops 文档（#181）。

- **完成结果**：v0.10 自身可走完发布到达 released 终态；live 通道失败时用户看到分类与恢复指引而非笼统报错，可继续发布或按指引修复环境后重试。

## 4. 行为种子

### BS-01 SPA 切换不经整页刷新

- EARS: `WHEN 用户切换功能区、开闭 tab、切换文档或保存, THE 系统 SHALL 在单页内完成切换与保存，不经整页刷新`
- 来源: [3.1 / Human 已定方向 D1]
- 说明: 锁定 SPA 交互模型本身，技术选型留 M-DESIGN 但交互结果不妥协。

### BS-02 Tab 条固定语义

- EARS: `WHEN 用户查看左侧 tab 条, THE 系统 SHALL 仅以图标加 hover tooltip 呈现，命中区不小于 32px，图标顺序固定且不支持拖拽重排`
- 来源: [3.1 / Human 已定方向 D3，引 louke v0.13-001]
- 说明: 保护 chrome 一致性，顺序与命中区是固定需求而非可选项。

### BS-03 Sidebar 联动与 Tab 共存

- EARS: `WHEN 用户切换 tab 条项, THE 系统 SHALL 在同一切换内将 sidebar 切到对应导航树并在 main area 打开或激活对应 tab；已打开 tab 共存不被切换关闭，重复点击激活已有唯一实例，用户主动关闭是唯一关闭路径，tab 集合不跨会话持久化；Settings 只开 tab 不换 sidebar，Account 菜单仅含 logout`
- 来源: [3.1 / Human 已定方向 D3，引 louke v0.13-001]
- 说明: 联动原子性与共存语义是 workbench 导航的核心契约。

### BS-04 未知值显式降级

- EARS: `WHEN sidebar 树节点、main 内容或 tab 标签遇到未知或缺失值, THE 系统 SHALL 显式降级展示，不崩溃、不 500、不留空白 tab`
- 来源: [3.1 / Human 已定方向 D3，引 louke v0.13-001]
- 说明: 防止枚举漂移击穿 chrome，降级是固定需求。

### BS-05 登录双栏与记住态

- EARS: `WHEN 用户打开登录页, THE 系统 SHALL 呈现双栏 auth shell（hero 图加标语加署名加登录面板，错误行内展示），并以持久 cookie 承载记住登录态`
- 来源: [3.2 / Human 已定方向 D2]
- 说明: 登录视觉沿用 louke 设计，记住态无额外复选框。

### BS-06 名字端到端绑定

- EARS: `WHEN 用户初次登录输入名字并持久化后, THE 系统 SHALL 将该名字用于与 agent 的对话与留痕（会话称谓、事件 actor 字段 human.anchor/human.approval 审计、agent assignment 称谓、讨论 speaker 标签），维持本机单用户边界且不引入注册或多账号`
- 来源: [3.2 / Human 已定方向 D2，约束 RP-01]
- 说明: 名字不是装饰字段，AC 必须端到端绑定；单用户边界不变。

### BS-07 文档编辑经修订通道产生新 revision

- EARS: `WHEN 用户在文档中心以 Vditor ir 模式编辑并显式保存, THE 系统 SHALL 经既有 edit_material 修订通道产生新 revision 且不绕过批准绑定（FR-0308），保存按钮仅在 dirty 时可用，写失败时编辑内容不丢`
- 来源: [3.3 / Human 已定方向 D4]
- 说明: 保护修订与批准绑定的不变量，首个可演示里程碑的后半段。

### BS-08 乐观并发冲突两选项

- EARS: `WHEN 文档保存遇到 revision 或 mtime 冲突返回 409, THE 系统 SHALL 向用户提供重载放弃与强制覆盖两选项`
- 来源: [3.3 / Human 已定方向 D4]
- 说明: 并发冲突的恢复路径是固定需求，不静默覆盖。

### BS-09 多 pane 分屏独立

- EARS: `WHEN 用户打开多 pane 分屏, THE 系统 SHALL 支持至多 4 列，每 pane 独立文件选择器与工具栏并可独立加载不同文档`
- 来源: [3.3 / Human 已定方向 D4]
- 说明: 对照阅读的分屏语义，每 pane 独立是固定需求。

### BS-10 Discussion 读与导航边界

- EARS: `WHEN 用户使用 inline discussion, THE 系统 SHALL 提供显示或隐藏切换、下一个 discussion、只看未决过滤，且 RESOLVED 语义仅发起人；本版只做读与导航，写回留 CLI`
- 来源: [3.3 / Human 已定方向 D4，约束：服务端无 discussion 写回 API]
- 说明: 锁定本版不做 UI 写回，避免手写协议拼接；如需写回须单独提案加 mutate 端点。

### BS-11 编辑器失败回退

- EARS: `WHEN Vditor 加载失败, THE 系统 SHALL 回退到 textarea，保证文档仍可查看与编辑`
- 来源: [3.3 / Human 已定方向 D4]
- 说明: 编辑器韧性策略，失败不阻断文档中心主路径。

### BS-12 Run 时间线可选整包

- EARS: `WHEN run 详情时间线进入本版, THE 系统 SHALL 呈现顶部当前节点卡片加 13 阶段线性时间线（attempt 不折叠、回拨回边、节点详情浮层），数据源为既有 timeline 与 ac-chain API；若 spec 冻结时容量不够则整包延后 v0.11`
- 来源: [3.4 / Human 已定方向 D5]
- 说明: 可选项的取舍规则是整包延后，不做部分交付。

### BS-13 无假数据与可观察失败

- EARS: `WHEN UI 呈现任何状态, THE 系统 SHALL 只消费服务端查询投影与事件流，不以 mock 数据伪造，API 失败时产生用户可观察的失败反馈`
- 来源: [3.5 / Human 已定方向 D6]
- 说明: 无 mock、无假成功是本版 NFR 的用户可观察面。

### BS-14 事件与写安全

- EARS: `WHEN SSE 断线或事件重复乱序, THE 系统 SHALL 按序号补读且不使 UI 倒退；WHEN 用户提交写操作, THE 系统 SHALL 遵守 CSRF 与 Idempotency-Key 契约`
- 来源: [3.5 / Human 已定方向 D6，约束：v0.9 已具备]
- 说明: 事件流健壮与写安全沿用既有契约，UI 层遵守同一契约。

### BS-15 测试双层与控件绑定

- EARS: `WHEN 验证本版, THE 系统 SHALL 分 API e2e 与 UI e2e（Playwright/Chromium）两层，UI e2e 不得以 API 请求替代关键 UI 操作，控件以 data-testid 绑定；Playwright 浏览器下载与 CI 新 required check 计入独立基础设施预算并在 spec 中显式声明`
- 来源: [3.5 / Human 已定方向 D6]
- 说明: 测试分层与预算归属是 NFR 的一部分，防止 UI 测试被 API 替代掏空。

### BS-16 里程碑收尾可达 released

- EARS: `WHEN M-MILESTONE 收尾执行 _complete_milestone, THE 系统 SHALL 区分 M-IMPL 闭包边界伪完成与真正的 released 终态，使 released 可达且 close_milestone 不无限重发熔断`
- 来源: [3.6 / #182 blocker]
- 说明: 不修则 v0.10 自己的发布也到不了 released，必须先修。

### BS-17 制品版本门

- EARS: `WHEN 发布制品时, THE 系统 SHALL 要求 pyproject version 与 tracks.__version__ 与发布 tag 一致，宿主合同 version gate 声明 file/key/expect 绑定并处理 guard-registry config_digest 重锚`
- 来源: [3.6 / #179]
- 说明: 版本一致性由宿主合同门禁承载，bump 到发布版本。

### BS-18 预检分类与 live 通道恢复

- EARS: `WHEN hotfix 前检捕获 GithubIssuesError, THE 系统 SHALL 按分类区分映射并给出对应恢复指引，不再一律吞成 issue_not_found；WHEN live GitHub 通道在标准 macOS Python 下请求时, THE 系统 SHALL 显式使用 certifi CA bundle，并将 live 旅程环境前置写入 ops 文档`
- 来源: [3.6 / #180/#181]
- 说明: 预检可恢复性与 live 通道可诊断性，两项合并为通道卫生。

## 5. 范围、约束与例外

- **必须保持的产品约束**：
  - SPA 技术选型优先原生 ES modules，不引入 npm/Vite 构建链；若 M-DESIGN 论证确需构建链，必须同时给出其与 guard-registry/CI required checks 的集成方案。
  - supervisor 单活动 run 串行加 hotfix 换队语义（FR-0296）不变，UI 不得发明并发推进入口。
  - web 层不直接耦合 runtime，HTTP 改动经命令服务。
  - 文档编辑经 edit_material 产生新 revision，不绕过批准绑定（FR-0308）；显式保存加 409 两选项。
  - 服务端无 discussion 写回 API，本版 discussion 只做读与导航，写回留 CLI；如需 UI 写回须单独提案加 mutate 端点，不手写协议拼接。
  - 写操作遵守 CSRF 加 Idempotency-Key，SSE 按序号补读；秘密经 redaction 投影后展示；Vditor 同源 vendor。
  - 本机单用户边界（RP-01）不变，不引入注册或多账号；名字绑定不改变该边界。
  - run 时间线数据源为既有 timeline/ac-chain API；容量不够整包延后 v0.11，在 spec 冻结时明确取舍。
  - Playwright 浏览器下载与 CI 新 required check 计为独立基础设施预算，在 spec 中显式计入。
  - docs/ 文档补写不在本版（排 v0.11）；容量参照 v0.9 FR 预算 28。
- **非常规要求**：无。
- **Out-of-Scope**：
  - Chat/Agent 会话流、模型绑定页、质量面板、i18n、setup 向导/onboarding 门禁、Settings 完整三页、跨会话 tab 持久化、toolbar 拖拽重排、WebSocket、多用户/公网边界（seed 明确排除）。
  - discussion UI 写回（resolve/reply）在本版不做，留 CLI；单独提案前不加 mutate 端点。
  - docs/ 文档补写排 v0.11。

## 6. 开放产品决定

无

## 7. 必要性与风险

- **既有能力**：8 页面封闭集与完整 JSON API（projects/runs/timeline/ac-chain/todos/docs/diff/edits/events SSE）；7 天滚动会话加 CSRF 加写操作幂等键加 redaction 投影；Vditor 同源 vendor 资产；edit_material 修订流程与 FR-0308 批准绑定；timeline/ac-chain 数据源；supervisor 单活动 run 串行加 hotfix 换队（FR-0296）；guard-registry 与 CI required checks 门禁体系。
- **冲突**：无——调研文档中较宽松的表述（技术选型留 M-DESIGN 裁定、功能项未锁定）以本 story 采用的 Human 六条不可协商项为准（原生 ES modules 优先加门禁集成条件、七项功能项收敛），不构成实质冲突。
- **重要风险**：
  - #182 为 blocker，不先修则 v0.10 自己的发布到不了 released 终态，全部 UI 增量无法交付。
  - 容量风险：D1 加 D3 加 D4 占主要预算，D5 按冻结取舍整包延后，Playwright 基础设施为独立预算；若把每个查看微功能展开为独立需求会挤占预算并偏离首个可演示里程碑。
  - 若 M-DESIGN 引入构建链而未同步给出 guard-registry/CI 集成方案，将冲击门禁体系；约束已要求必须同包给出，否则维持原生 ES modules。
