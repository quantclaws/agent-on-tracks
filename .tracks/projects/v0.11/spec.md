---
envelope: tracks-envelope:v2
spec_id: SPEC-011
created: 2026-10-10
status: draft
sha: 008593c4ad23376689ccddf6ae4243459ae4a576d2703b80346456e45e328c89
---

# 自驱动工作台——Releases 整顿 + run 时间线 + release/hotfix 创建 + 人工门 UI 化 — 需求规格

> 本 spec 将 v0.11 story（S-001）落为可验证需求：在 v0.10 SPA 工作台 chrome 与既有查询/命令/SSE 契约之上，合并 Projects/Runs 为 Releases 窗口并移除 Review 占位，按 louke v0.14-004 FR-1201/1301/1401 绘制完整 run 时间线（固定下栏详情），将 flow.md §3 M-START 与 §16 hotfix UI 化（含 backlog 存储与投影补齐、统一入口 Scribe 评估先于建分支），将 triage/review 人工门补 HTTP 命令端点并接进时间线 awaiting 动作面板做自驱动端到端验证，并将 Scribe 重复/冲突检测落为 TRIAGE 派发合同与 flow.md §4。容量：15 有效 FR（FR-0333..FR-0347，承接项目全局序号，<30 单版交付无需拆分；2 NFR 不计）。继承边界：v0.10 工作台 chrome（FR-0316..FR-0326 交互壳/tab/登录/文档中心/时间线弱化版）、v0.9 查询/命令/SSE 与 supervisor 单活动 run 串行 + hotfix 换队、批准绑定（FR-0308）、preview 绑定（FR-0309）、受控重试（FR-0311）、timeline/ac-chain/run detail 数据源（v0.9 起）全部继承，本版不重写；本版新增的仅是 Releases 窗口结构、时间线完整绘制与固定下栏、统一创建入口与 backlog 存储投影、hotfix UI 化、triage/review 命令端点与动作面板、评审轮写入通道与分流合同。

## 界面与入口

### E-01 Releases 窗口

```
┌──────┬──────────────────────┬────────────────────────────┐
│ tab条 │ sidebar              │ main area                  │
│ [Re]  │ 版本分组 run 注册表   │ run 详情 / 新建入口 / 面板  │
│ [Do]  │  v0.12 ── run ×N     │ 标题区显示 run/文档标题     │
│ [To]  │  v0.11 ── run ×M     │ (不显示 tab 分类名)         │
│ [Se]  │ ▸ backlog (n) 折叠组  │                            │
│ [Ac]  │ ▸ 归档查询组(与 backlog│                            │
│       │    存储区分、视觉区分) │                            │
└──────┴──────────────────────┴────────────────────────────┘
```

tab 条仅 Releases / Docs / Todos / Settings / Account 五项，无 Review。sidebar 只显 Releases 下级内容；注册项目/版本信息仅为窗口内摘要行。

### E-02 run 时间线 + 固定详情栏

```
┌─────────────────────────────────────────────────┐
│ 时间线：M-START ●─●─◆活跃─● (attempt 独立节点)    │
│          ╰──回拨回边（可辨认来源→目标）           │
├─────────────────────────────────────────────────┤
│ 固定详情栏（main area 下方，非浮层）：            │
│  节点详情 / awaiting 时为该门的动作面板           │
│  [跳 owning surface] [返回 run/节点上下文]       │
└─────────────────────────────────────────────────┘
```

### E-03 统一新建入口与 Scribe 建议面板

```
┌─────────────────────────────────────────────────┐
│ [新建 release] 输入一段话：故事/seed │ issue id    │
│              │ backlog 条目(从 sidebar 发起转正)   │
│ [新建 hotfix] 选择 issue + 场景               │
├─────────────────────────────────────────────────┤
│ Scribe 建议面板：可行性/重复/冲突 + 四动作：     │
│  [进入 backlog] [从 backlog 删除] [归档] [进入开发]│
│ M-START 门：活跃 run 提示/脏树拒绝/未合并确认     │
└─────────────────────────────────────────────────┘
```

### E-04 awaiting 动作面板与评审轮写入

```
┌─────────────────────────────────────────────────┐
│ 动作面板（E-02 下栏处于 awaiting 时）：           │
│  原因 / 影响 / [唯一主要动作]                    │
│  triage: go/no-go/park; review: comment/no-     │
│  comment; approve; release-decision             │
│ 评审轮文档：直接修改 + inline-comments           │
│  (pre-approval 模式，基线为空不阻塞保存)         │
└─────────────────────────────────────────────────┘
```

## 状态与生命周期

### SM-01 统一入口输入处置

未列出的状态转移即不允许；FR 描述可直接引用本清单行号（如 SM-01.3）。

1. （提交输入）→ `待决`：输入进入 Scribe 建议面板，四动作可选
2. `待决` → `backlog`：用户选进入 backlog（聚合存储，sidebar 折叠组呈现）
3. `待决` → `已归档`：用户选不进入开发归档（与 backlog 存储区分，可查但不进入开发）
4. `待决` → `开发中`：用户选进入开发且通过 M-START 门（Scribe 定标题，创建分支与 run）
5. `待决`/`backlog` → `已删除`：仅来源为 backlog 时可从 backlog 删除（移除原条目并记录去向）
6. `backlog` → `开发中`：从 backlog 转正（以条目为输入走新建流程，成功后移除原条目并记录去向）
7. 选进入 backlog/归档/删除时 → 终态：不开 run、不建分支

## 功能需求

### FR-0333 Releases 注册表与 backlog/归档组

- **来源**：`BS-01` / `§3.1`
- **交付入口**：`E-01`（Releases 窗口 sidebar + main area）

WHEN 用户打开 Releases 窗口，THE 系统 SHALL 在 sidebar 以版本分组呈现 run 注册表与 backlog 折叠组，注册项目与版本信息仅为窗口内摘要行；归档以独立归档查询组呈现，与 backlog 在视觉与存储上区分。WHEN 用户在 sidebar 选中一个 release(run)，main area 进入该 run 详情（FR-0335/FR-0336）；选中 backlog 条目则呈现转正入口（FR-0340）。

**用户可观察结果**：用户在 Releases 窗口内完成版本浏览 → run 审查 → 创建/转正闭环，可返回注册表或进入 Docs/Todos 继续；归档可查但不进入开发。

### FR-0334 五 tab 收敛与 Review 承接

- **来源**：`BS-02` / `§3.1`
- **交付入口**：`E-01`（tab 条）+ `E-04`（awaiting 动作面板承接点）

WHEN 用户查看 tab 条，THE 系统 SHALL 仅呈现 Releases / Docs / Todos / Settings / Account 五项且无 Review 入口；阶段评审到达时，THE 系统 SHALL 在时间线 awaiting 节点的动作面板（FR-0343）承接评审职责，不出现无处评审的悬空门。本条为对 v0.10 FR-0318 七项收敛（含 Review）的破坏性变更，GO 已由操作者指令显式确认已知晓。

**用户可观察结果**：tab 条无 Review；任一阶段评审均可在对应 awaiting 节点动作面板完成。

### FR-0335 时间线完整绘制

- **来源**：`BS-03` / `§3.2` / louke FR-1201
- **交付入口**：`E-02` + `GET /api/runs/{run_id}/timeline` + `GET /api/runs/{run_id}/ac-chain`（既有数据源，沿 v0.9）

WHEN 用户查看 run 时间线，THE 系统 SHALL 呈现 13 阶段 canonical 线性时间线：每次 attempt 为独立节点不折叠、方向连接表达推进、回拨以可辨认回边表达来源至目标；已完成/活跃/待处理/需处理/失效五态可区分且不只靠颜色；初始视图聚焦活跃节点并保留邻近上下文，全部历史可导航到达。时间线只消费 stage_order 与 timeline/ac-chain 投影，不合成第二数据源，不使用 mock 数据。

**用户可观察结果**：用户可解释当前进展、历史 attempt 与回拨关系；投影与接口数据一致。

### FR-0336 节点详情固定下栏

- **来源**：`BS-04` / `§3.2` / louke FR-1301
- **交付入口**：`E-02` 下方固定详情栏（非浮层）

WHEN 用户以鼠标选中时间线任意节点，THE 系统 SHALL 在 main area 下方固定详情栏加载该节点详情：起止时间/状态/责任方/关联 artifact/revision/关键 evidence/错误/状态转移原因/回拨合法性；明确区分选中查看与当前活跃；提供 owning surface 稳定入口与返回同一 run 与可识别节点上下文。详情为真实投影数据；不可查看或无数据的节点可读降级，不伪造数据。本条替换 v0.10 IF-TIMELINE-001 浮层占位，固定下栏为不可协商项；节点选中以鼠标为准，不要求键盘可达。

**用户可观察结果**：选中即在下栏看到真数据详情或可读降级，可跳转 owning surface 并返回原上下文；无浮层。

### FR-0337 回拨安全执行

- **来源**：`BS-05` / `§3.2` / louke FR-1401
- **交付入口**：`E-02` 回拨指针 + 确认面

WHEN 用户查看回拨，THE 系统 SHALL 仅为 Runtime 明确允许的先前 attempt 展示回拨指针；WHEN 用户发起回拨，THE 系统 SHALL 先说明目标与后果并经明确确认与 Runtime 复验后才执行，不伪装撤销外部副作用。

**用户可观察结果**：不允许的回拨无入口；执行前用户看到目标与后果并显式确认。

### FR-0338 统一入口与 Scribe 建议面板

- **来源**：`BS-06` / `§3.3` / flow.md §3.2
- **交付入口**：`E-03`（统一新建入口 + 建议面板）

WHEN 用户经统一入口提交一段输入，THE 系统 SHALL 接受三种来源（本身即故事/seed、作新功能输入的 GitHub issue id、backlog 条目），派发 Scribe 做可行性/重复/冲突建议并呈现建议面板，提供四个动作：进入 backlog、从 backlog 删除（仅来源为 backlog 时可用，SM-01.5）、不进入开发归档、进入开发（SM-01.2/SM-01.3/SM-01.4）。输入为 raw seed 时逐字入骨架，为完整 story 时原样采纳、不包裹进 §1。用户按意图显式分流：release/新功能意图走统一入口，hotfix 意图走独立 hotfix 入口（FR-0341）；统一入口不推荐 hotfix，Scribe 面板不路由到 hotfix 流程。

**用户可观察结果**：用户提交一段话后看到 Scribe 建议与四选一面板；完整 story 不被改写包裹；hotfix 意图有独立入口。

### FR-0339 进入开发才建分支与 M-START 门

- **来源**：`BS-07` / `§3.3` / flow.md §3
- **交付入口**：`E-03`（M-START 门面）+ `releases/v{N+1}` 分支可观察出口 + flow.md §3 修订

WHEN 用户在建议面板选择进入开发（SM-01.4/SM-01.6），THE 系统 SHALL 由 Scribe 确定标题（写入 frontmatter title）并走 M-START 门：有活跃 run 则提示并记入 backlog（不开新 run、不建分支），工作区不干净则拒绝并示原因，本地分支未全部并入 main 则单列出未合并分支与后果并经确认（继续/取消），通过后从 main 创建 `releases/v{N+1}`；WHEN 用户选择其余三项，THE 系统 SHALL 不开 run、不建分支（SM-01.7）。Scribe 评估先于 CREATE_BRANCH 的重排随本版同步修订 flow.md §3/§4。失败或拒绝均给出原因与合法继续位置，不建半截 run 或分支。

**用户可观察结果**：只有选进入开发才建分支建 run；其余三项无新 run/分支；门拒绝时用户看到原因与下一步。

### FR-0340 backlog/归档区分与转正去向

- **来源**：`BS-08` / `§3.3`
- **交付入口**：`E-01`（sidebar backlog 折叠组 + 归档查询组）+ `backlog.recorded` 事件聚合投影

WHEN `backlog.recorded` 事件产生或用户选择进入 backlog（SM-01.2），THE 系统 SHALL 聚合存储并在 sidebar 折叠组呈现 backlog 投影；WHEN 用户选择归档（SM-01.3），THE 系统 SHALL 与 backlog 在存储上区分归档并在归档查询组可查；WHEN 用户从 backlog 删除（SM-01.5）或转正成功（SM-01.6），THE 系统 SHALL 移除原条目并记录去向。创建入口除编辑器新写外包括从 backlog 转正：以 backlog 条目为输入走新建 release 流程。

**用户可观察结果**：backlog 条目在 sidebar 可见可转正；归档与 backlog 分开可查；删除/转正后原条目消失且去向可查。

### FR-0341 hotfix 创建与锚定退出

- **来源**：`BS-09` / `§3.3` / flow.md §16
- **交付入口**：`E-03`（新建 hotfix 入口）+ HOTFIX-TRIAGE 预检与 AC 锚定结果呈现面

WHEN 用户新建 hotfix，THE 系统 SHALL 要求选择 GitHub issue 与场景（post-release 从 main 基线 / dev 从活跃分支），并展示 HOTFIX-TRIAGE 预检与既有 AC 锚定结果；IF 锚定不成立，THE 系统 SHALL 明示退出 hotfix 并指引转 backlog/new feature，不建半截 fix 分支。

**用户可观察结果**：用户看到预检与锚定结果，锚定成立继续建 hotfix，不成立看到明确退出与转 backlog 指引。

### FR-0342 人工门命令端点与 UI 接入

- **来源**：`BS-10` / `§3.4`
- **交付入口**：`E-04`（门动作面板）+ 新增 HTTP 命令端点（triage/review）+ 既有 approve/release-decision 端点

WHEN 用户在 UI 处理 triage（go/no-go/park）或文档阶段 review（comment/no-comment），THE 系统 SHALL 经 HTTP 命令端点触发 `human.triage` / `human.review` 事件并走既有 CSRF 与幂等合同；M-REQ-APPROVAL approve 与 M-RELEASE release-decision 沿已有端点接进 UI。全部 UI 动作经命令面事件单写路径，CLI 保留为管理后门。

**用户可观察结果**：四类门动作均可在 UI 完成并落对应事件；重复提交受幂等保护。

### FR-0343 awaiting 动作面板

- **来源**：`BS-11` / `§3.4` / louke FR-1201 attention 模式
- **交付入口**：`E-02` 下栏（awaiting 时）+ `E-04`

WHEN 时间线节点处于 awaiting，THE 系统 SHALL 在其下栏详情呈现该门的动作面板：原因、影响与唯一主要动作入口（louke attention 模式）。下栏详情与动作面板为同一 surface；Review 移除后的阶段评审在此承接（FR-0334）。

**用户可观察结果**：用户在 awaiting 节点下栏直接看到原因/影响与唯一主要动作，可进入处理。

### FR-0344 评审轮文档评论与编辑写入

- **来源**：`BS-12` / `§3.4`
- **交付入口**：`E-04`（评审轮文档写入面）+ 命令面事件

WHEN 用户在文档阶段评审轮处理评审中的文档，THE 系统 SHALL 在 UI 提供文档评论与编辑写入通道（直接修改 + inline-comments，支持 pre-approval 模式：基线为空不阻塞保存），写入经命令面事件。本条补评审轮 base_revision 为空阻塞 UI 写入的实测缺口；否则 M-SPEC/M-ACC 评审门只能回退文件编辑。

**用户可观察结果**：评审中的文档可在 UI 直接修改与发表 inline-comments，基线为空时仍可保存。

### FR-0345 自驱动端到端

- **来源**：`BS-13` / `§3.4`
- **交付入口**：`E-02`（下一道门呈现）+ serve 常驻 supervisor 推进可观察出口

WHEN 用户在 UI 发起门动作后，THE 系统 SHALL 由 serve 常驻 supervisor 自动推进，并在下一道门到达时于时间线 UI 呈现（活跃指针前移）；本版验证 UI 发起至下一道门呈现的端到端闭环。门间无手工推进。

**用户可观察结果**：用户沿时间线连续处理多道门直到 run 进入下一阶段或完成；下一道门自动出现在时间线。

### FR-0346 分流检测栏目与合同

- **来源**：`BS-14` / `§3.5`
- **交付入口**：Scribe M-STORY TRIAGE 派发合同 + flow.md §4 + `E-03` 建议面板的重复/冲突栏目（本版唯一运行时可见面）

WHEN Scribe 撰写 TRIAGE 报告，THE 系统 SHALL 要求报告含重复检测（跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强）与冲突检测（列出新需求 vs 既有 FR 的冲突对，GO 需显式确认已知晓冲突）两栏目；两项检测的报告格式与触发规则 SHALL 写入 M-STORY TRIAGE 派发合同与 flow.md §4，作为本版 spec 交付的一部分。本条只落报告格式与派发合同，不做运行时拦截或自动 NO-GO。

**用户可观察结果**：后续 story 的 TRIAGE 报告自带重复/冲突栏目；统一入口建议面板的重复/冲突内容与同一规则一致。

### FR-0347 评审人复核合同：非本人发起线程不构成 revise 依据

- **来源**：`操作者裁定 2026-10-10`
- **交付入口**：评审派发合同 + flow.md §4/§5（与 FR-0346 同交付面，无独立 UI 入口）

WHEN 评审人（Sage/Lex/Prism）复核的文档存在未解决的非本人发起线程，THE 评审派发合同 SHALL 规定：该线程不构成 revise 依据——内容验证通过即 pass，线程确认留给其发起人；revise 仅限评审人自有新发现且必须开锚定线程。合同文本随本版写入 flow.md §4/§5 评审协议。

**用户可观察结果**：后续评审不以他人未决线程触发无发现修订；评审人自有新发现均带锚定线程可追溯。


> **Aaron [RESOLVED]:** 增补 FR-0347 评审人合同：WHEN 评审人（Sage/Lex/Prism）复核的文档存在未解决的非本人发起线程，THE 评审派发合同 SHALL 规定：该线程不构成 revise 依据——内容验证通过即 pass，线程确认留给其发起人；revise 仅限评审人自有新发现且必须开锚定线程。来源：操作者裁定 2026-10-10（同日两次 revise_without_findings 三连升级的根治点；runtime 审计与 discussion_ready 出口闸均正确，缺的是评审人派发合同条款）。合同文本随本版写入 flow.md §4/§5 评审协议，与 FR-0346 同交付面。
>> **Sage:** 已落实 FR-0347，来源记操作者裁定，交付面评审派发合同加 flow.md S4/S5，validate 通过，请发起人确认后设 resolved。

## 非功能需求

### NFR-0157 单写路径与写安全

- **来源**：`BS-10` / `BS-12` / `§5`

WHEN UI 发起任何写操作，THE 系统 SHALL 经既有命令面事件单写路径提交并遵守既有 CSRF 与 Idempotency-Key 契约；CLI 仅为管理后门，不扩展为第二常规路径。

### NFR-0158 工作台继承、真数据与可观察失败

- **来源**：`BS-03` / `BS-04` / `§5`

WHEN UI 呈现任何状态，THE 系统 SHALL 只消费服务端查询投影与事件流（沿 v0.9 起的 timeline/ac-chain/run detail，不新增数据源），不以 mock 数据伪造；API 失败时 SHALL 产生用户可观察的失败反馈，不伪报成功。v0.10 工作台裁定继续有效：外壳钉视口、Vercel 极简设计令牌、registry 行紧凑化、pane bar 单控制面、自动保存模式。

## 范围排除

- Docs / Todos / Settings / Account 现有行为不变，本版只做 Releases 窗口与时间线/创建/门动作的新增与修改。
- 时间线数据源不新增（沿用 v0.9 起的 timeline / ac-chain / run detail）。
- Scribe 分流检测只落报告格式与派发合同，不做运行时拦截或自动 NO-GO。
