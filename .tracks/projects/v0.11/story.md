---
envelope: tracks-envelope:v2
story_id: S-001
title: {一句话标题}
created: 2026-10-10
status: draft
sha:
---

# S-001: {一句话标题}

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留内容）：
  - §1 逐字记录 Human 原始输入，不修改、不转述。
  - §3 每条操作路径复制 3.1 的小节结构（3.2、3.3……）。修改类路径的「变更基线」必须写清
    当前行为与本次变更——下游（Sage/Archer/Shield/Devon）基于此描述变更，而非从零设计；
    新增路径写“无（新增路径）”。
  - §4 只提取需要下游继续展开的用户结果和重要边界，不枚举普通微交互；按路径顺序统一编号
    BS-01、BS-02……（不按路径分组）。
  - BS 编号文法（FR-0130）：`### BS-XX`（两位补零，按核心操作路径顺序统一编号，不按路径分组）。
    ID 一经分配不可变、不可复用；删除的 BS 留 tombstone。
  - 跨版本引用（FR-0130）：本版本内引用保持短格式；跨版本引用且存在歧义时附加版本限定
    `AC-FRXXXX-YY@<version>`（如 `AC-FR0010-01@v0.1`）。限定语法 opt-in，parser 不强制。
  - §5 各项没有时写“无”；Out-of-Scope 只记录明确排除或为防止明显范围扩张而必须记录的事项。
  - §6 每个问题一个段落，粗体一句话开头（格式见正文占位）；不编号、机器不校验。只写无法
    可靠推导、且不同答案会显著改变价值/范围/权限/数据安全/合规或产生不可逆后果的产品问题；
    技术选择不写。没有则整节写“无”。
  - §7 重要风险只写会改变范围或使 story 不成立的。

  不得使用超过3行以上的表格；可改用列表。
-->

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

> **Scribe:** TRIAGE verdict: GO（raw seed，可进入 DRAFT 展开）。证据：(1)五节均有明确操作者指令与变更基线可查——窗口整顿指认 v0.10 FR-0318 与 Review 占位核实，时间线指认 louke v0.14-004 FR-1201/1301/1401 与既有数据源（v0.9 起），M-START/ backlog/hotfix 指认 flow.md §3.2/§16，人工门指认既有 CSRF+幂等端点与 supervisor；(2)交付面明确为 UI 工作台（Releases/Docs/Todos/Settings/Account），约束明确单写路径经命令面事件；(3)单版 FR≤30 的范围约束已有操作者裁定，无需分期判断。DRAFT 将按模板展开 4 条核心路径（窗口整顿/时间线/创建 release-hotfix/人工门）与 BS，不追问普通默认。

## 2. 用户意图

- {用户想完成什么}
- {当前哪里受阻}
- {完成后能看到什么结果}

## 3. 核心操作路径

### 3.1. {路径名}

- **变更基线**：[新增 / 替换 / 修改 / 重命名] — {此路径在当前版本的行为与实现；新增路径写“无（新增路径）”}
- **入口/触发**：{用户从现有哪个任务、对象或公开入口，如何进入本能力}

1. {改变用户任务状态的关键步骤 1}
2. {关键步骤 ...}
3. {用户在何处看到可验证的完成结果}

- **完成结果**：{可观察的完成状态；完成、取消或可恢复失败后，用户接下来能做什么}

## 4. 行为种子

### BS-01 {行为种子标题}

- EARS: `WHEN/IF/WHILE/WHERE {条件}, THE 系统 SHALL {用户可观察行为}`
- 来源: [{路径编号} / 约束 / 非常规要求 / 重要推导]
- 说明: {这项行为保护什么用户结果}

## 5. 范围、约束与例外

- **必须保持的产品约束**：{用户明确要求或既有合同中不能改变的约束；无则写“无”}
- **非常规要求**：{有意偏离宿主项目惯例、安全或可用性默认的要求及理由；无则写“无”}
- **Out-of-Scope**：{明确排除的事项；无则写“无”}

## 6. 开放产品决定

**{问题一句话}** — {不同答案会改变什么产品结果}。方向：A) {…} B) {…}。推荐 {A}，因为 {理由}。

## 7. 必要性与风险

- **既有能力**：{可复用或相关的现有功能/合同；无则写“未发现直接覆盖”}
- **冲突**：{与既有产品方向的实质冲突；无则写“无”}
- **重要风险**：{只写会改变范围或使 story 不成立的风险；无则写“无阻塞风险”}
