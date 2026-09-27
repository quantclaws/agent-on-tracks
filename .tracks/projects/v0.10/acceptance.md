---
envelope: tracks-envelope:v2
acc_id: ACC-010
created: 2026-09-27
status: draft
sha:
---

# Web 工作台完善（UI）+ 发布卫生 — 验收标准

> 本文档为 SPEC-010（FR-0316..FR-0332、NFR-0153..NFR-0156）验收标准的唯一登记处；每条 AC 只写可从系统外部观察、可断言的通过条件。首个可演示里程碑（新用户浏览器登录→看到真数据→文档中心编辑产生新 revision）由 AC-FR0320-01 → AC-FR0321-01 → AC-FR0322-01 → AC-FR0323-01 组合覆盖；发布卫生终态由 AC-FR0327-01（released 可达）收敛。

## FR-0316 SPA 工作台 shell 与无整页刷新

### AC-FR0316-01

- 在工作台内依次切换功能区、开闭 tab、切换文档、保存文档，全程浏览器不发生整页刷新（同一 document 实例延续，无整页白闪与上下文丢失），三段式布局（左侧垂直 tab 条 + sidebar + 多 tab 主区）相对位置在各上下文一致。
- 上述操作后用户可继续进入文档编辑、run 详情或返回总览，无需重新登录或重建上下文。

### AC-FR0316-02

- 交付仓库中无 npm/Vite 构建链产物与第二工具链门禁冲击；若设计引入构建链，同包给出其与 guard-registry/CI required checks 的集成方案文档且门禁仍通过，否则维持原生 ES modules。

## FR-0317 左侧 tab 条固定语义

### AC-FR0317-01

- 左侧 tab 条仅以图标加 hover tooltip 呈现，各功能项经 tooltip 可识别；任一图标命中区不小于 32px。
- 页面内无拖拽手柄，刷新前后图标顺序不变，尝试拖拽不改变顺序。

## FR-0318 Sidebar 联动与 tab 共存

### AC-FR0318-01

- 切换 tab 条项时，sidebar 与 main area 在同一次切换内联动完成，不存在只换 sidebar 不开 tab 的中间态；重复点击同一 tab 条项激活已有唯一实例，不新建重复实例。
- 已打开 tab 共存不被切换关闭；用户主动关闭是唯一关闭路径；界面无关闭所有全局动作。

### AC-FR0318-02

- 刷新或重启浏览器后 tab 集合不恢复（回到空集合，为预期行为而非丢失）；sidebar 只显当前 tab 条项的下级内容，不同时罗列全部 tab 项。
- main area 标题区直接显示内容标题（文档标题、run 标题、设置页标题），不显示 tab 条分类名；多内容以内容标题区分。

### AC-FR0318-03

- 功能项集合为 Projects/Runs/Docs/Review/Todos/Settings/Account 七项；run 新建入口位于 Runs 下，发布决定位于 run 详情内，无独立顶级 release 项。
- 经齿轮项只打开 Settings tab 且 sidebar 内容不变；Account 菜单仅含 logout（check updates 无入口、无行为）；logout 后浏览器不残留凭据，需重新登录才能进入工作台。

## FR-0319 未知值显式降级

### AC-FR0319-01

- 注入未知或缺失值（未知树节点、缺失标题、未知 tab 目标）后，对应位置显示可读降级标记（NotFound 或未知标记），不崩溃、不返回 500、不留空白 tab，用户可继续导航。

## FR-0320 登录双栏与记住态

### AC-FR0320-01

- 登录页呈现双栏：左侧 hero 图加标语加图片署名，右侧登录面板；登录失败时错误行内展示；窄视口下折叠为单栏。
- 登录成功后关闭并重开浏览器，会话仍有效并可直接进入工作台真数据浏览（7 天滚动窗口语义）。

### AC-FR0320-02

- 未认证访问工作台被 302 到 `/login`；logout 后服务端会话被删除且浏览器不残留凭据，再次访问工作台需重新登录。

## FR-0321 名字采集与端到端绑定

### AC-FR0321-01

- 服务端尚无名字的新用户首次登录成功后，被要求输入显示名，完成前不进入工作台数据面；名字持久化后，后续会话称谓、事件 actor 字段（human.anchor 与 human.approval 审计）、agent assignment 称谓、讨论 speaker 标签均显示同一名字。

### AC-FR0321-02

- 全程无注册或多账号入口，不出现第二账号概念；名字仅为同一单用户的身份标注，不改变单用户边界。

## FR-0322 文档中心查看宿主与编辑器韧性

### AC-FR0322-01

- 目录树按版本号逆序排列，最新版本置顶；经目录树选择六件套任一文档（story、spec、acceptance、architecture、interfaces、test-plan）后，内容以 Vditor ir 即时渲染模式呈现，资产经同源 vendor 路径加载。

### AC-FR0322-02

- 阻断 Vditor 资产加载后，文档仍可用 textarea 查看与编辑，内容完整，主路径不阻断。

## FR-0323 显式保存经修订通道与冲突恢复

### AC-FR0323-01

- 未编辑时保存不可用，编辑后保存可用；点击显式保存后经既有修订通道产生新 revision，界面切换到新 revision 基线；对旧 revision 的批准被拒绝（批准绑定不被绕过）。

### AC-FR0323-02

- 构造 revision 或 mtime 冲突（服务端返回 409）后，界面向用户提供重载放弃与强制覆盖两选项，不静默覆盖；任一写失败后编辑内容保留，可重试而不丢失。

## FR-0324 多 pane 分屏独立

### AC-FR0324-01

- 用户可打开至多 4 列分屏，每 pane 有独立文件选择器与工具栏，可独立加载不同文档对照，各 pane 选择与编辑互不干扰。

### AC-FR0324-02

- 请求超过 4 列时不再新增 pane，原有 pane 内容不受影响。

## FR-0325 inline discussion 读与导航边界

### AC-FR0325-01

- discussion 覆盖层提供显示或隐藏切换、下一个 discussion、只看未决过滤三能力；RESOLVED 状态仅由发起人动作产生，非发起人不能改变条目的解决状态。

### AC-FR0325-02

- 界面上无 resolve 或 reply 写入口；写回经 CLI 完成，客户端不拼接讨论协议。

## FR-0326 run 详情 13 阶段时间线（本版生效，Human 冻结裁定 2026-09-27）

> **Aaron:** FR-0326 冻结决定（Human 裁定，2026-09-27）：run 详情 13 阶段时间线整包进 v0.10。容量依据：本版 FR 17 条（v0.9 为 28），预算明确有余；数据源（timeline/ac-chain）v0.9 已交付，纯前端+投影建模。请把 AC-FR0326-01 落为生效验收（去掉条件式表述），AC-FR0326-02 的取舍记录改为指向本裁定（保留零残留断言作为延后分支的回溯口径亦可，由你按文档惯例定），并同步 spec.md 中 FR-0326 的『可选项』标记与本裁定一致。
>> **Sage:** 已按冻结裁定落实：AC-FR0326-01 去掉“进入本版时”条件式、落为生效验收；AC-FR0326-02 改为指向本裁定（整包进 v0.10，零残留断言保留为延后分支回溯口径）；章节标题去“可选项”改为“本版生效，Human 冻结裁定 2026-09-27”；spec.md FR-0326 同步为生效表述（来源追加冻结裁定，去掉容量取舍与“或整体不在本版”，用户可观察结果去条件分支）。两文件均 trac validate valid。请确认后由你设 resolved。

### AC-FR0326-01

- run 详情顶部当前节点卡片显示责任方、attempt、运行时长与唯一主要动作；13 阶段线性时间线每次 attempt 独立节点不折叠，回拨以可辨认回边呈现，视口聚焦活跃节点前后若干节点。
- 点击节点打开详情浮层，显示起止时间与 artifact 或 revision，并可跳转到文档中心；时间线内容与既有 timeline 与 ac-chain 接口数据一致。

### AC-FR0326-02

- Human 冻结裁定 2026-09-27：整包进 v0.10（延后分支回溯口径：若曾延后，界面无时间线残留入口——卡片、时间线、浮层均不出现，无部分交付）。

> **Lex:** Lex M-ACC 语义确认（消费 1-lex-10，该证据为 M-SPEC 遗留与本文件无关）：重读 acceptance 全文 220 行对照 SPEC-010。T-001 冻结落实与 Aaron 裁定一致——AC-FR0326-01 去条件式落为生效验收（卡片/时间线/浮层/跳转/接口一致性均从公开出口断言），AC-FR0326-02 指向冻结裁定（零残留断言保留为延后分支回溯口径，Human 已授权，无害冗余），spec.md FR-0326 同步生效（来源追加裁定、去取舍分支）。无矛盾、无范围偏移、无覆盖缺口。Lex 无修订要求；T-001 待 Human 确认（resolved 权限在 Aaron）。

## FR-0327 里程碑收尾 released 可达

### AC-FR0327-01

- 走完 M-MILESTONE 收尾后出现 `run.completed(terminal_state=released, release_tag)` 事件并落地 released 终态；M-IMPL 闭包边界的 `run.completed(boundary)` 不再被误认为收尾完成。
- 收尾期间 close_milestone 不无限重发熔断；v0.10 自身发布可达 released。

## FR-0328 制品声明版本门

### AC-FR0328-01

- `pyproject.toml project.version`、`tracks.__version__` 与发布 tag 三者一致时发布校验放行；任一错配时校验明确拒绝并指认错配的 file 与 key。

### AC-FR0328-02

- 宿主合同 version gate 含 file、key、expect 绑定声明；guard-registry `config_digest` 重锚处理后门禁通过。

## FR-0329 hotfix 前检分类映射与恢复指引

### AC-FR0329-01

- 以不存在的 issue 号触发前检，得到 `issue_not_found` 并携带可操作的下一步指引。

### AC-FR0329-02

- 构造抓取失败（缺凭据、认证失败、限流、网络失败任一）后，前检得到 `issue_fetch_failed`（不再是 `issue_not_found`），携带对应分类的恢复指引且重试路径保持开启。

## FR-0330 live GitHub 通道 TLS 与环境前置

### AC-FR0330-01

- 在标准 macOS Python 下发起 live GitHub 请求，TLS 校验经 certifi CA bundle 成功，live 通道恢复可用。

### AC-FR0330-02

- ops 文档含 live 旅程环境前置节（GITHUB_TOKEN、TRAC_GITHUB_REPO、TLS）；缺失环境变量时失败按分类可诊断呈现并指向该指引，按指引修复后重试成功。

## FR-0331 tracker milestone 生命周期补全与缺失指引

### AC-FR0331-01

- tracker 声明 repo、project 与 milestone_template 且具备凭据时，首次接触 tracker 后远端同标题 milestone 存在（不存在则创建并经 API 回读）；重复接触同一标题不重复建（幂等复用）。

### AC-FR0331-02

- 远端缺失且无法自动补建时，得到 `milestone_not_found` 并携带可操作下一步（明确操作者前置建 milestone 义务）；按指引手工建后重试成功。

## FR-0332 LEX_REVIEW 仅被 Human 线程阻塞的合法出口与线程复用

### AC-FR0332-01

- LEX_REVIEW 仅被 Human 线程阻塞（无其它待修语义问题）时，产出 `pass-pending-human-threads` 合法 verdict 并 park 到 AWAIT_HUMAN；`trac status` 显示待决 Human 线程清单，用户可据此继续人工处理后恢复。

### AC-FR0332-02

- 重试循环重进 LEX_REVIEW 时，Lex findings 线程跨 attempt 复用，不逐轮新开重复线程。

## NFR-0153 无 mock 数据与可观察失败

### AC-NFR0153-01

- 抽查总览、run 详情、文档、待办任一数据面，其展示状态与服务端查询投影及事件流一致，无硬编码伪造数据。

### AC-NFR0153-02

- 注入 API 失败后，界面出现用户可观察的失败反馈，不伪报成功；故障恢复后用户可继续操作或按失败指引恢复。

## NFR-0154 事件韧性与写安全

### AC-NFR0154-01

- SSE 断线重连后界面按序号补读事件，构造重复或乱序事件后界面不倒退，投影单调一致。

### AC-NFR0154-02

- 写操作遵守既有 CSRF 与 Idempotency-Key 契约：缺失或错误的 CSRF 凭据被拒绝；秘密经 redaction 投影后展示，无明文泄露。

## NFR-0155 测试分层与控件绑定及基础设施预算

### AC-NFR0155-01

- 关键旅程（登录、浏览真数据、文档中心编辑产生新 revision）经 Playwright 真实浏览器操作完成，全程无 API 请求替代关键 UI 步骤；控件经 `data-testid` 定位，不依赖 CSS class 或 DOM 层级。

### AC-NFR0155-02

- Playwright 浏览器下载与 CI 新 required check 在 test-plan 或 architecture 中显式声明为独立基础设施预算，不挤占 FR 预算。

## NFR-0156 构建链门禁集成条件

### AC-NFR0156-01

- 未引入构建链时仓库无第二工具链产物；若引入 npm 或 Vite 构建链，同包集成方案存在且 guard-registry 与 CI required checks 门禁通过。
