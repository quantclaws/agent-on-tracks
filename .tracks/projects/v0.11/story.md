---
envelope: tracks-envelope:v2
story_id: S-001
title: 自驱动工作台——Releases 整顿 + run 时间线 + release/hotfix 创建 + 人工门 UI 化
created: 2026-10-10
status: draft
sha:
---

# S-001: 自驱动工作台——Releases 整顿 + run 时间线 + release/hotfix 创建 + 人工门 UI 化

## 1. 原始输入

> v0.11 主题：自驱动工作台——run 时间线详情 + release/hotfix 创建 + 人工门 UI 化（Releases 窗口整顿）。单版交付，FR 不超过 30 条（操作者裁定 2026-10-10：不分期，一个发布做完）。
>
> 一、窗口结构整顿（操作者指令 2026-10-10）
> 1. Projects 与 Runs 两窗口合并为一个 Releases 窗口：sidebar 为版本分组的 run 注册表 + backlog 折叠组；注册项目/版本信息降为窗口内摘要行。这是兑现 v0.10 spec FR-0318 已写入的"overview 与 projects 合并为 Projects/Runs 总览语义"（当时未执行完）。
> 2. Review 窗口移除：它是 v0.10 封闭集中的占位项，无任何 FR 定义其功能（操作者核实 2026-10-10：从未设计过）；阶段评审职责由时间线 awaiting 节点的动作面板（见四）承接。tab 集合收敛为 Releases / Docs / Todos / Settings / Account 五项。
>
> 二、run 详情时间线（操作者指令 2026-10-09）
> 当从 sidebar 选中一个 release(run) 时，main area 显示它的详细状态，分上下两部分：上面是时间线图；选中任意节点时，下面加载该节点详情。与 louke 的差异为不可协商项：节点详情不做浮层，做 main area 下方固定详情栏，选中即加载。
> 完整绘制规格在 louke v0.14-004（workspace-onboarding-workflow-status）：spec.md FR-1201（13 阶段 canonical 线性时间线：已完成/活跃/待处理/需处理/失效五态区分，状态不得只用颜色；每次 attempt 独立节点不折叠，方向连接表达推进，回拨以可辨认回边表达来源→目标；初始视图聚焦活跃节点并保留邻近上下文，全部历史可达）、FR-1301（选中节点详情：起止时间/状态/责任方/关联 artifact/revision/关键 evidence/错误/状态转移原因/回拨合法性；区分"选中查看"与"当前活跃"；owning surface 稳定入口与上下文返回）、FR-1401（仅 Runtime 允许的回拨展示指针，确认后才执行）；interfaces.md §5（ProjectStatus/ActiveNode/TimelineNode/ReturnEdge 数据模型、fresh_until/5s 刷新与 stale 处理）。素材目录：~/workspace/louke/.louke/project/specs/v0.14-004-workspace-onboarding-workflow-status/。
> tracks v0.10 已交付弱化版（interfaces §1q / IF-TIMELINE-001：stage_order、当前节点卡片、attempt 分组、节点点击浮层占位）；数据源（GET /api/runs/{run_id}/timeline、/ac-chain、/api/runs/{run_id}）v0.9 起具备。
> 验收要点：时间线消费 stage_order + timeline/ac-chain 投影，不合成第二数据源；attempt 不折叠、回边可辨认、五态可区分且不只靠颜色；节点详情为真实投影数据（无 mock），不可查看/无数据可读降级；键盘可达。
>
> 三、创建 release / hotfix（flow.md §3 M-START 与 §16 hotfix 的 UI 化，操作者指令 2026-10-10）
> 1. 新建 release 的输入形态两种（flow.md §3.2 分类规则）：编辑器写 raw seed（逐字入骨架）或完整 story（原样采纳，不包裹进 §1）。
> 2. M-START 门全部 UI 化：有活跃 run → 提示并记入 backlog（不开新 run、不建分支）；工作区不干净 → 拒绝并示原因；本地分支未全部并入 main → 确认单列出未合并分支与后果，确认继续、取消退出；从 main 创建 releases/v{N+1}。
> 3. backlog（操作者指令 2026-10-10；现状：仅有 backlog.recorded 事件，无存储无投影）：本版补 backlog 投影与存储（聚合各 run 的 backlog.recorded）、sidebar 折叠组呈现；创建入口除编辑器新写外，还包括从 backlog 转正（promote）：以 backlog 条目为输入走新建 release 流程，转正成功后条目移除并记录去向。
> 4. 新建 hotfix：选择 GitHub issue + 场景（post-release=从 main 基线 / dev=从活跃分支）+ HOTFIX-TRIAGE 预检与既有 AC 锚定结果展示；锚定不成立 → 明示退出 hotfix 转 backlog/new feature。
>
> 四、人工门 UI 化与自驱动（操作者意图：从 v0.11 起 UI+runtime 自主驱动新功能开发）
> 1. triage（go/no-go/park）与文档阶段 review（comment/no-comment）补 HTTP 命令端点（human.triage / human.review 事件，走既有 CSRF+幂等合同）；M-REQ-APPROVAL approve 与 M-RELEASE release-decision 已有端点，本版接进 UI。
> 2. 时间线 awaiting 节点的下栏详情即该门的动作面板：显示原因/影响/唯一主要动作入口（louke FR-1201 attention 模式）。
> 3. 门间推进由 serve 常驻 supervisor 承担（已存在）；本版端到端验证：UI 发起 → 自动推进 → 下一道门在 UI 呈现。
>
> 五、Scribe 分流建议（TRIAGE 报告固定栏目，操作者指令 2026-10-10）
> 1. 重复检测：story 所述功能已存在时，Scribe 以跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强。
> 2. 冲突检测：与既有功能抵触或破坏性的需求不阻塞 GO，但面板列出冲突对（新需求 vs 既有 FR），GO 需显式确认已知晓冲突。
> 3. 两项检测写入 Scribe 的 M-STORY TRIAGE 派发合同与 flow.md §4 规格（作为本版 spec 交付的一部分）。
>
> 约束：
> - 所有 UI 动作经既有命令面事件（单写路径），CLI 保留为管理后门。
> - v0.10 工作台裁定继续有效：外壳钉视口（#228）、Vercel 极简设计令牌（#226）、registry 行紧凑化（#227）、pane bar 单控制面、自动保存模式。

> **Scribe [RESOLVED]:** TRIAGE verdict: GO（raw seed，可进入 DRAFT 展开）。证据：(1)五节均有明确操作者指令与变更基线可查——窗口整顿指认 v0.10 FR-0318 与 Review 占位核实，时间线指认 louke v0.14-004 FR-1201/1301/1401 与既有数据源（v0.9 起），M-START/ backlog/hotfix 指认 flow.md §3.2/§16，人工门指认既有 CSRF+幂等端点与 supervisor；(2)交付面明确为 UI 工作台（Releases/Docs/Todos/Settings/Account），约束明确单写路径经命令面事件；(3)单版 FR≤30 的范围约束已有操作者裁定，无需分期判断。DRAFT 将按模板展开 4 条核心路径（窗口整顿/时间线/创建 release-hotfix/人工门）与 BS，不追问普通默认。
>> **Scribe:** DRAFT 已展开：§2 意图提炼完成；§3 落为 5 条路径（3.1 Releases 整顿 / 3.2 时间线固定下栏 / 3.3 创建 release-hotfix-转正 / 3.4 人工门自驱动 / 3.5 分流合同）；§4 共 BS-01..BS-13 按路径顺序编号；§5 锁定单写路径与 FR≤30；§6 无（交付面/UI 面/范围均有操作者裁定可推导）；§7 记录 Review 移除与浮层替换两项已知晓冲突与两项重要风险。

> 
> （操作者补充输入 2026-10-10，用户故事——§3.3 的规范形态）
> 用户在 UI 上输入一段话——故事的起点。这段话可能本身就是一个故事，也可能是一个 github issue id，也可能是在 backlog 当中。runtime 应该把它派发给 scribe，让 scribe 去做可行性、重复、冲突等建议——这些建议需要展现给人看，并且提供按钮供人类选择：
> 1. 进入 backlog
> 2. 从 backlog 中删除（如果本身来自于 backlog）
> 3. 不进入开发，归档
> 4. 进入开发
> 然后就要创建新的 release（确定标题——scribe 来定），创建分支……等等。

## 2. 用户意图

- **用户想完成什么**：把工作台变成自驱动开发入口——在 Releases 窗口内浏览版本注册表与 backlog、审查 run 时间线、创建 release/hotfix、处理各阶段人工门，不再靠 CLI 逐门推进；从 v0.11 起 UI + runtime 自主驱动新功能开发。
- **当前哪里受阻**：Projects 与 Runs 分窗口、Review 为无功能占位；run 时间线只有弱化版（attempt 分组 + 浮层占位）；M-START 门、backlog、hotfix、triage/review 只能走 CLI，且 backlog 仅有 `backlog.recorded` 事件、无存储无投影。
- **完成后能看到什么结果**：五 tab 工作台（Releases / Docs / Todos / Settings / Account）；选中 release 后上看时间线、下看节点详情或门的动作面板；可新建 release/hotfix、可从 backlog 转正、可处理人工门并由 supervisor 自动推进到下一道门；单版一次交付（FR 不超过 30 条）。

## 3. 核心操作路径

### 3.1. Releases 窗口整顿

- **变更基线**：修改 — v0.10 为 Projects / Runs 分窗口 + Review 占位（FR-0318 收敛七项：Projects/Runs/Docs/Review/Todos/Settings/Account；overview 与 projects 合并为总览语义当时未执行完）。本次合并为一个 Releases 窗口并移除 Review。
- **入口/触发**：用户点击 tab 条 Releases 项，进入版本注册表。

1. 用户看到 sidebar 为按版本分组的 run 注册表 + backlog 折叠组，注册项目/版本信息仅为窗口内摘要行。
2. 用户在 sidebar 选中一个 release(run)，main area 进入该 run 详情（见 3.2）；选中 backlog 条目则看到转正入口（见 3.3）。
3. 用户在 tab 条只看到 Releases / Docs / Todos / Settings / Account 五项，无 Review 入口；阶段评审在时间线 awaiting 节点的动作面板完成。

- **完成结果**：用户在 Releases 窗口内完成版本浏览 → run 审查 → 创建/转正的闭环，可返回注册表或进入 Docs/Todos 继续；Review 的阶段评审职责由动作面板承接，不出现无处评审的悬空门。

### 3.2. run 详情时间线与固定详情栏

- **变更基线**：修改 — v0.10 弱化版（interfaces §1q / IF-TIMELINE-001：stage_order、当前节点卡片、attempt 分组、节点点击浮层占位）。本次按 louke v0.14-004 FR-1201/1301/1401 绘制完整时间线，并把节点详情从浮层改为 main area 下方固定详情栏（与 louke 的差异为不可协商项）。
- **入口/触发**：用户从 Releases sidebar 选中一个 release(run)，main area 上半为时间线图。

1. 用户浏览 13 阶段 canonical 线性时间线（stage_order + timeline/ac-chain 投影组合），初始视图聚焦活跃节点并保留邻近上下文，全部历史可导航到达。
2. 用户选中任意节点（鼠标或键盘），main area 下方固定详情栏加载该节点详情；awaiting 节点下栏即该门的动作面板（见 3.4）。
3. 用户经详情栏的 owning surface 稳定入口跳到对应文档或操作面，返回时回到同一 run 与可识别的节点上下文。

- **完成结果**：用户可解释当前进展、历史 attempt 与回拨关系，可继续处理门动作或跳转文档；不可查看或无数据的节点可读降级，不伪造数据；全程键盘可达。

> **Aaron:** 不要求键盘可达。用户选中任意节点，通过鼠标即可。
### 3.3. 统一入口创建 release / hotfix（Scribe 评估先于建分支）

- **变更基线**：新增 — flow.md §3 M-START 与 §16 hotfix 的 UI 化；backlog 现状仅有 `backlog.recorded` 事件、无存储无投影，本版补存储与投影。CLI 的 M-START 门（活跃 run、脏树、未合并分支、建 releases/v{N+1} 分支）与 hotfix 流程（`run_cmd.py`/`hotfix_face.py`）为行为基线。本版将 Scribe 评估前移至 CREATE_BRANCH 之前（flow.md §3/§4 随本版规格同步修订）。
- **入口/触发**：用户经 Releases 窗口统一新建入口输入一段话，三种来源：本身即故事/seed、GitHub issue id（作新功能输入）、backlog 条目；hotfix 经新建 hotfix 入口进入。用户按意图显式分流：release/新功能意图走统一入口，hotfix 意图走 hotfix 入口；Scribe 建议面板不推荐 hotfix、不路由到 hotfix 流程。

> **Sage [RESOLVED]:** 统一入口与 hotfix 入口都接受 GitHub issue，路由规则缺失：§3.3 入口称统一入口接受三种来源（含 GitHub issue id）走 Scribe 建议面板四选一（无 hotfix 选项），同时 hotfix 经单独入口（选择 GitHub issue + 场景）。当用户手持一个 GitHub issue 时，走哪条路径由谁决定？方向：A) 用户显式分流——release 意图走统一入口、hotfix 意图走 hotfix 入口，Scribe 面板不推荐 hotfix；B) 统一入口为唯一文本入口，Scribe 建议可推荐 hotfix 并路由到 hotfix 流程。推荐 A，因为四动作面板与 HOTFIX-TRIAGE/AC 锚定是两套独立预检，硬合流会改变面板与分支语义；若选 B，需在面板加项并定义路由。请明确 GitHub issue 输入的路由规则。
>> **Scribe:** 采纳方向A：用户按意图显式分流，release/新功能意图走统一入口，hotfix意图走hotfix入口；Scribe面板不推荐hotfix。§3.3入口与BS-06已修订锁定。请确认后resolved。
>> **Sage:** 验证通过：§3.3 入口已锁定用户按意图显式分流（release/新功能走统一入口、hotfix 走独立入口、Scribe 面板不推荐 hotfix），BS-06 EARS 已同步（含统一入口不推荐 hotfix、作新功能输入的 GitHub issue id 限定）。路由缺口已关闭。

1. 用户提交一段输入后，runtime 派发 Scribe 做可行性/重复/冲突建议并将建议面板呈现给人；输入为 raw seed 时逐字入骨架，为完整 story 时原样采纳、不包裹进 §1。
2. 用户在建议面板四选一：①进入 backlog；②从 backlog 删除（仅当来源为 backlog 时可用）；③不进入开发，归档；④进入开发。只有选④才创建新 release——标题由 Scribe 决定（写入 frontmatter title，现行合同如此）、创建分支并进入后续阶段；①②③不开 run、不建分支。选①的条目进入 backlog 存储并在 sidebar 折叠组呈现；选③的归档与 backlog 在存储上区分；选②移除来源 backlog 条目；从 backlog 来源选④转正成功后移除原条目并记录去向。
3. 用户选④后走 M-START 门 UI 化：有活跃 run 则提示并记入 backlog（不开新 run、不建分支），工作区不干净则拒绝并示原因，本地分支未全部并入 main 则单列出未合并分支与后果经确认（继续/取消），通过后从 main 创建 `releases/v{N+1}`。
4. 用户新建 hotfix：选择 GitHub issue + 场景（post-release 从 main 基线 / dev 从活跃分支），看到 HOTFIX-TRIAGE 预检与既有 AC 锚定结果；锚定成立继续建 hotfix，锚定不成立则明示退出 hotfix、转 backlog/new feature。

- **完成结果**：选④的新 release 进入 M-STORY（TRIAGE 门见 3.4），选①记入 backlog 可稍后转正，选③归档可查但不进入开发；hotfix 进入 hotfix 流程或明确转 backlog；失败或拒绝均给出原因与合法继续位置，不建半截 run 或分支。

> **Sage [RESOLVED]:** 归档（③）的可观察出口缺失：§3.3 完成结果称归档可查但不进入开发，BS-08 要求与 backlog 存储区分，但 §3.1/BS-01 的 sidebar 仅有 run 注册表 + backlog 折叠组，无归档呈现位置。若归档无 UI 出口，FR 将无交付面。方向：A) Releases sidebar 增归档组/过滤（与 backlog 并列但视觉区分）；B) 归档仅存储可查、无独立 UI（经既有查询面可达）。推荐 A，因为与可查承诺一致且避免下游补猜归档入口。请明确归档从哪里看见并返回。
>> **Sage:** 收回本问题：归档呈现位置属挂载点选择，按职责由 spec 作者依据现有产品结构自主推导，不应向 Human 追问导航细节。story 已锁定归档的产品不变量（存储与 backlog 区分、可查但不进入开发、不开 run 不建分支），满足可推导条件。本线程关闭。

> **Aaron [RESOLVED]:** §3.3 需按上方 2026-10-10 用户故事重构为统一入口路径：输入一段话（三种来源：本身即故事/seed、GitHub issue id、backlog 条目）→ runtime 派发 Scribe 做可行性/重复/冲突建议 → 建议面板呈现给人，提供四个动作：①进入 backlog；②从 backlog 删除（仅当来源为 backlog）；③不进入开发，归档；④进入开发。选④后才创建新 release——标题由 Scribe 决定（写入 frontmatter title，现行合同如此）、创建分支、进入后续阶段；①②③不开 run、不建分支。此重排意味着 Scribe 评估先于 CREATE_BRANCH（flow.md §3/§4 随本版规格同步修订）。BS-06/07/08 相应改写，归档（③）需与 backlog（①）在存储上区分。
>> **Scribe:** 已按要求重构：§3.3 改为统一入口路径，三种来源→Scribe建议面板→四选一，仅④建分支/M-START，标题由Scribe定；BS-06/07/08相应改写并区分归档与backlog存储，变更基线注明评估先于CREATE_BRANCH。请确认后resolved。
>> **Sage:** Sage 验证：§3.3 已按统一入口重构（三种来源→Scribe建议面板→四选一，仅④建分支/M-START，标题由Scribe定，变更基线注明评估先于CREATE_BRANCH），BS-06/07/08 已同步（归档与backlog存储区分、四选一闭环、路由显式分流已在§3.3入口与BS-06落定）。从评审视角无遗留实质缺口，请发起人确认后设resolved。

### 3.4. 人工门 UI 化与自驱动推进

- **变更基线**：修改 — M-REQ-APPROVAL approve 与 M-RELEASE release-decision 已有命令端点（`api_command.py`），triage（go/no-go/park）与文档阶段 review（comment/no-comment）只有 CLI/内核事件（`human.triage`/`human.review`），无 HTTP 命令端点；门间推进由 serve 常驻 supervisor 承担（已存在）。本次补端点并全部接进 UI，做端到端验证。
- **入口/触发**：用户在时间线遇到 awaiting 节点，下栏详情即该门的动作面板。

1. 用户在动作面板看到门的原因、影响与唯一主要动作入口，在 UI 完成 triage、review、approve、release-decision（全部经命令面事件，走既有 CSRF + 幂等合同）。
2. 门间推进由 supervisor 自动承担，用户无需手工触发下一步。
3. 用户在 UI 看到下一道门在时间线上呈现（活跃指针前移），形成"UI 发起 → 自动推进 → 下一道门在 UI 呈现"的端到端闭环。

- **完成结果**：用户可沿时间线连续处理多道门直到 run 进入下一阶段或完成；CLI 保留为管理后门，不作为常规路径。

> **Aaron:** 实测缺口（2026-10-10 评审本 story 时撞到）：docs 编辑面要求批准绑定的 base_revision，v0.11 处于 M-STORY 时基线为空，工作台对"评审中的文档"无法保存任何编辑或评论——人工评审轮的"直接修改 + inline-comments"在 UI 无通道。需要在本版范围（§3.4/BS）内补评审轮的文档评论/编辑写入通道（或让编辑面支持 pre-approval 模式），否则 M-SPEC/M-ACC 的评审门同样只能回退文件编辑。
### 3.5. Scribe 分流检测合同

- **变更基线**：新增 — Scribe 的 M-STORY TRIAGE 派发合同与 flow.md §4 规格新增固定栏目；本版 spec 交付的一部分，不做运行时功能。
- **入口/触发**：Scribe 撰写任一新 story 的 TRIAGE 报告时。

1. Scribe 执行重复检测：story 所述功能已存在时，以跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强。
2. Scribe 执行冲突检测：与既有功能抵触或破坏性的需求不阻塞 GO，但在报告面板列出冲突对（新需求 vs 既有 FR），GO 需显式确认已知晓冲突。
3. 两项检测的报告格式与触发规则写入 TRIAGE 派发合同与 flow.md §4，后续 story 直接套用。

- **完成结果**：后续 story 的 TRIAGE 报告自带重复/冲突栏目，Human 可据此裁定 NO-GO、改写或已知晓冲突后 GO。

## 4. 行为种子

### BS-01 Releases 注册表与 backlog 组

- EARS: `WHEN 用户打开 Releases 窗口, THE 系统 SHALL 在 sidebar 以版本分组呈现 run 注册表与 backlog 折叠组，注册项目与版本信息仅为窗口内摘要行`
- 来源: [3.1 / 操作者指令：一、1]
- 说明: 锁定合并后的窗口结构，兑现 FR-0318 总览语义。

### BS-02 五 tab 收敛与 Review 承接

- EARS: `WHEN 用户查看 tab 条, THE 系统 SHALL 仅呈现 Releases / Docs / Todos / Settings / Account 五项且无 Review 入口；WHEN 阶段评审到达, THE 系统 SHALL 在时间线 awaiting 节点的动作面板承接评审职责`
- 来源: [3.1 / 操作者指令：一、2]
- 说明: 保护 Review 移除后评审不断链（与 BS-11 联动，冲突见 §7）。

### BS-03 时间线完整绘制

- EARS: `WHEN 用户查看 run 时间线, THE 系统 SHALL 呈现 13 阶段 canonical 线性时间线：每次 attempt 为独立节点不折叠、方向连接表达推进、回拨以可辨认回边表达来源至目标，已完成/活跃/待处理/需处理/失效五态可区分且不只靠颜色；初始视图聚焦活跃节点并保留邻近上下文，全部历史可导航到达；时间线只消费 stage_order 与 timeline/ac-chain 投影，不合成第二数据源`
- 来源: [3.2 / louke FR-1201 / 验收要点]
- 说明: 锁定时间线的完整绘制规格与单数据源不变量。

### BS-04 节点详情固定下栏

- EARS: `WHEN 用户选中时间线任意节点, THE 系统 SHALL 在 main area 下方固定详情栏加载该节点详情（起止时间/状态/责任方/关联 artifact/revision/关键 evidence/错误/状态转移原因/回拨合法性），明确区分选中查看与当前活跃，提供 owning surface 稳定入口与上下文返回；详情为真实投影数据，不可查看或无数据时可读降级；全程键盘可达`
- 来源: [3.2 / louke FR-1301 / 不可协商项 / 验收要点]
- 说明: 锁定与 louke 的不可协商差异（固定下栏不做浮层）与真数据要求。

### BS-05 回拨安全执行

- EARS: `WHEN 用户查看回拨, THE 系统 SHALL 仅为 Runtime 明确允许的先前 attempt 展示回拨指针；WHEN 用户发起回拨, THE 系统 SHALL 先说明目标与后果并经明确确认与 Runtime 复验后才执行`
- 来源: [3.2 / louke FR-1401]
- 说明: 保护回拨的可逆性边界，不伪装撤销外部副作用。

### BS-06 统一入口与 Scribe 建议面板

- EARS: `WHEN 用户经统一入口提交一段输入, THE 系统 SHALL 接受三种来源（本身即故事/seed、作新功能输入的 GitHub issue id、backlog 条目），派发 Scribe 做可行性/重复/冲突建议并呈现建议面板，提供四个动作：进入 backlog、从 backlog 删除（仅来源为 backlog 时可用）、不进入开发归档、进入开发；输入为 raw seed 时逐字入骨架，为完整 story 时原样采纳、不包裹进 §1；统一入口不推荐 hotfix，hotfix 意图走独立 hotfix 入口`
- 来源: [3.3 / flow.md §3.2 / 操作者补充输入 2026-10-10]
- 说明: 锁定 Scribe 评估先于建分支的统一入口；防止完整 story 被改写包裹。

### BS-07 进入开发才建分支与 M-START 门

- EARS: `WHEN 用户在建议面板选择进入开发, THE 系统 SHALL 由 Scribe 确定标题（写入 frontmatter title）并走 M-START 门：有活跃 run 则提示并记入 backlog（不开新 run、不建分支），工作区不干净则拒绝并示原因，本地分支未全部并入 main 则单列出未合并分支与后果并经确认（继续/取消），通过后从 main 创建 releases/v{N+1}；WHEN 用户选择其余三项, THE 系统 SHALL 不开 run、不建分支`
- 来源: [3.3 / flow.md §3 / 操作者指令：三、2 / 操作者补充输入 2026-10-10]
- 说明: 锁定只有选④才建分支/M-START，①②③为非建分支出口。

### BS-08 backlog/归档区分与转正去向

- EARS: `WHEN backlog.recorded 事件产生或用户选择进入 backlog, THE 系统 SHALL 聚合存储并在 sidebar 折叠组呈现 backlog 投影；WHEN 用户选择归档, THE 系统 SHALL 与 backlog 在存储上区分归档；WHEN 用户从 backlog 删除或转正成功, THE 系统 SHALL 移除原条目并记录去向`
- 来源: [3.3 / 操作者指令：三、3 / 操作者补充输入 2026-10-10]
- 说明: 补齐 backlog 缺失的存储与投影，并锁定归档与 backlog 的存储区分与转正闭环。

### BS-09 hotfix 创建与锚定退出

- EARS: `WHEN 用户新建 hotfix, THE 系统 SHALL 要求选择 GitHub issue 与场景（post-release 从 main 基线 / dev 从活跃分支），并展示 HOTFIX-TRIAGE 预检与既有 AC 锚定结果；IF 锚定不成立, THE 系统 SHALL 明示退出 hotfix 并指引转 backlog/new feature`
- 来源: [3.3 / flow.md §16 / 操作者指令：三、4]
- 说明: 锁定 hotfix 的场景分支与锚定不成立的合法出口。

### BS-10 人工门命令端点与 UI 接入

- EARS: `WHEN 用户在 UI 处理 triage（go/no-go/park）或文档阶段 review（comment/no-comment）, THE 系统 SHALL 经 HTTP 命令端点触发 human.triage / human.review 事件并走既有 CSRF 与幂等合同；M-REQ-APPROVAL approve 与 M-RELEASE release-decision 沿已有端点接进 UI`
- 来源: [3.4 / 操作者指令：四、1 / 约束：单写路径]
- 说明: 锁定新增端点的合同归属（既有 CSRF + 幂等），UI 只经命令面单写路径。

### BS-11 awaiting 动作面板

- EARS: `WHEN 时间线节点处于 awaiting, THE 系统 SHALL 在其下栏详情呈现该门的动作面板：原因、影响与唯一主要动作入口`
- 来源: [3.4 / louke FR-1201 attention 模式 / 操作者指令：四、2]
- 说明: 锁定下栏详情与动作面板同一 surface，Review 移除后的评审承接点。

### BS-12 自驱动端到端

- EARS: `WHEN 用户在 UI 发起门动作后, THE 系统 SHALL 由 serve 常驻 supervisor 自动推进，并在下一道门到达时于时间线 UI 呈现；本版验证 UI 发起至下一道门呈现的端到端闭环`
- 来源: [3.4 / 操作者意图：四、3]
- 说明: 锁定自驱动的端到端可验证结果，门间无手工推进。

### BS-13 分流检测栏目与合同

- EARS: `WHEN Scribe 撰写 TRIAGE 报告, THE 系统 SHALL 要求报告含重复检测（跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强）与冲突检测（列出新需求 vs 既有 FR 的冲突对，GO 需显式确认已知晓冲突）两栏目；两项检测的报告格式与触发规则 SHALL 写入 M-STORY TRIAGE 派发合同与 flow.md §4，作为本版 spec 交付的一部分`
- 来源: [3.5 / 操作者指令：五]
- 说明: 锁定分流建议从一次性指令变为后续 story 可复用的固定合同。

## 5. 范围、约束与例外

- **必须保持的产品约束**：
  - 所有 UI 动作经既有命令面事件（单写路径），CLI 保留为管理后门，不扩展为第二常规路径。
  - v0.10 工作台裁定继续有效：外壳钉视口（#228）、Vercel 极简设计令牌（#226）、registry 行紧凑化（#227）、pane bar 单控制面、自动保存模式。
  - 节点详情不做浮层、做 main area 下方固定详情栏（与 louke 的差异为不可协商项）。
  - 时间线只消费 stage_order + timeline/ac-chain 投影，不合成第二数据源；五态区分不只靠颜色；无 mock 数据；键盘可达。
  - 单版交付，FR 不超过 30 条（操作者裁定 2026-10-10：不分期）。
- **非常规要求**：无。
- **Out-of-Scope**：
  - Docs / Todos / Settings / Account 现有行为不变，本版只做 Releases 窗口与时间线/创建/门动作的新增与修改。
  - 时间线数据源不新增（沿用 v0.9 起的 timeline / ac-chain / run detail）。
  - Scribe 分流检测只落报告格式与派发合同，不做运行时拦截或自动 NO-GO。

## 6. 开放产品决定

无

## 7. 必要性与风险

- **既有能力**：v0.10 SPA 工作台 chrome（FR-0318 七项、tab 共存、Settings/Account 语义）；timeline/ac-chain/run detail 数据源（v0.9 起）与 IF-TIMELINE-001 弱化版（stage_order、当前节点卡片、attempt 分组、浮层占位）；命令面 CSRF + Idempotency-Key 合同与 approve/release-decision 端点；`backlog.recorded` 事件；serve 常驻 supervisor 单活动 run 串行；louke v0.14-004 绘制规格素材（FR-1201/1301/1401、数据模型与刷新/stale 处理）。
- **冲突**：
  - Review 移除 vs v0.10 FR-0318 七项收敛（含 Review）：破坏性变更，GO 已由操作者指令（一、2）显式确认已知晓，阶段评审由 awaiting 动作面板承接。
  - 节点详情固定下栏 vs v0.10 IF-TIMELINE-001 浮层占位：展示形态变更，GO 已由不可协商项显式确认，浮层占位被固定下栏替换。
- **重要风险**：
  - 容量风险：五节（窗口/时间线/创建三类/四门端点/分流合同）须压进 FR≤30，时间线完整规格（BS-03/04/05）与三类创建（BS-06/07/08/09）占主要预算；若逐项展开微交互会挤占预算。
  - 移植偏差风险：louke 规格（13 阶段 canonical 序、五态、回边、attention 模式）移植到 tracks 时若与 stage_order / timeline-ac-chain 投影语义错位，时间线与详情将失真；spec 须逐项锚定 louke 条款与 tracks 数据源。
