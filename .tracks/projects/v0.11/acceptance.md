---
envelope: tracks-envelope:v2
acc_id: ACC-011
created: 2026-10-10
status: draft
sha:
---

# 自驱动工作台——Releases 整顿 + run 时间线 + release/hotfix 创建 + 人工门 UI 化 — 验收标准

> 本文档为 SPEC-011（FR-0333..FR-0347、NFR-0157..NFR-0158）验收标准的唯一登记处；每条 AC 只写可从系统外部观察、可断言的通过条件。自驱动端到端主链由 AC-FR0333-01 → AC-FR0335-01 → AC-FR0336-01 → AC-FR0338-01 → AC-FR0339-01 → AC-FR0342-01 → AC-FR0345-01 组合覆盖。

## FR-0333 Releases 注册表与 backlog/归档组

### AC-FR0333-01

- 打开 Releases 窗口，sidebar 按版本分组呈现 run 注册表与 backlog 折叠组，注册项目与版本信息仅为窗口内摘要行
- sidebar 选中一个 release(run)，main area 进入该 run 详情；选中 backlog 条目，呈现转正入口
- 用户可从注册表进入 run 审查或转正，可返回注册表或进入 Docs/Todos 继续

### AC-FR0333-02

- 归档以独立归档查询组呈现，与 backlog 折叠组在视觉上区分
- 归档条目可查但不进入开发；sidebar 不同时罗列 Releases 以外 tab 的下级内容

## FR-0334 五 tab 收敛与 Review 承接

### AC-FR0334-01

- tab 条仅呈现 Releases / Docs / Todos / Settings / Account 五项，无 Review 入口
- 经 tab 条切换功能区，sidebar 与 main area 联动，不出现 Review 页面或空 tab

### AC-FR0334-02

- 任一阶段评审到达时，对应时间线 awaiting 节点的下栏动作面板可完成该评审
- 全程无因 Review 移除导致的无处评审的悬空门

## FR-0335 时间线完整绘制

### AC-FR0335-01

- run 时间线呈现 13 阶段 canonical 线性时间线，每次 attempt 为独立节点不折叠，方向连接表达推进，回拨以可辨认回边表达来源至目标
- 已完成/活跃/待处理/需处理/失效五态可区分且不只靠颜色
- 初始视图聚焦活跃节点并保留邻近上下文，全部历史可导航到达

### AC-FR0335-02

- 时间线内容与 `GET /api/runs/{run_id}/timeline` 及 `GET /api/runs/{run_id}/ac-chain` 接口数据一致，不合成第二来源
- 无 mock 数据；数据缺失时显式降级，不伪造节点

## FR-0336 节点详情固定下栏

### AC-FR0336-01

- 以鼠标选中时间线任意节点，main area 下方固定详情栏加载该节点详情：起止时间/状态/责任方/关联 artifact/revision/关键 evidence/错误/状态转移原因/回拨合法性
- 详情明确区分选中查看与当前活跃，提供 owning surface 稳定入口，返回时回到同一 run 与可识别的节点上下文

### AC-FR0336-02

- 详情为真实投影数据；不可查看或无数据的节点呈现可读降级，不伪造数据
- 选中节点详情不以浮层呈现；详情栏位置固定于 main area 下方

## FR-0337 回拨安全执行

### AC-FR0337-01

- 仅 Runtime 明确允许的先前 attempt 展示回拨指针；不允许的回拨无入口

### AC-FR0337-02

- 发起回拨前呈现目标与后果并经明确确认，Runtime 复验通过后才执行
- 执行后不伪装撤销外部副作用；拒绝或复验失败时给出原因与合法继续位置

## FR-0338 统一入口与 Scribe 建议面板

### AC-FR0338-01

- 经统一入口提交一段输入，系统呈现 Scribe 可行性/重复/冲突建议面板，提供四个动作：进入 backlog、从 backlog 删除、不进入开发归档、进入开发
- 三种来源均可提交：本身即故事/seed、作新功能输入的 GitHub issue id、backlog 条目；从 backlog 删除仅当来源为 backlog 时可用

### AC-FR0338-02

- 输入为 raw seed 时逐字入骨架；输入为完整 story 时原样采纳，不包裹进 §1，不改写重排
- 统一入口与建议面板不出现 hotfix 推荐，不路由到 hotfix 流程；hotfix 意图有独立 hotfix 入口

## FR-0339 进入开发才建分支与 M-START 门

### AC-FR0339-01

- 在建议面板选择进入开发，标题由 Scribe 确定并写入 frontmatter title，通过 M-START 门后从 main 创建 `releases/v{N+1}` 分支并建立 run
- 新 release 进入 M-STORY；失败或拒绝均给出原因与合法继续位置，不残留半截 run 或分支

### AC-FR0339-02

- 有活跃 run 时提示并记入 backlog，不开新 run、不建分支
- 工作区不干净时拒绝并说明原因，不建分支
- 本地分支未全部并入 main 时单列出未合并分支与后果，经确认（继续/取消）才通过；取消则退出，不建分支

### AC-FR0339-03

- 选择进入 backlog、从 backlog 删除、不进入开发归档三项中任一项，均不开 run、不建分支
- flow.md §3/§4 含 Scribe 评估先于 CREATE_BRANCH 的重排文本

## FR-0340 backlog/归档区分与转正去向

### AC-FR0340-01

- `backlog.recorded` 事件产生或用户选择进入 backlog 后，条目聚合存储并在 sidebar 折叠组呈现
- 用户选择归档的条目与 backlog 在存储上区分，并在归档查询组可查

### AC-FR0340-02

- 从 backlog 删除或转正成功后，原条目移除并记录去向，去向可查
- 从 backlog 转正以条目为输入走新建 release 流程；转正成功后进入 M-START 门与后续阶段

## FR-0341 hotfix 创建与锚定退出

### AC-FR0341-01

- 新建 hotfix 要求选择 GitHub issue 与场景（post-release 从 main 基线 / dev 从活跃分支）
- 呈现 HOTFIX-TRIAGE 预检与既有 AC 锚定结果；锚定成立继续建 hotfix

### AC-FR0341-02

- 锚定不成立时明示退出 hotfix 并指引转 backlog/new feature
- 退出时不建半截 fix 分支，不残留半建 run

## FR-0342 人工门命令端点与 UI 接入

### AC-FR0342-01

- 在 UI 完成 triage（go/no-go/park）后，事件流落 `human.triage` 对应事件；完成文档阶段 review（comment/no-comment）后，事件流落 `human.review` 对应事件
- 上述写请求携带 `X-Trac-CSRF` 与 `Idempotency-Key` 头；重复提交不产生重复事件

### AC-FR0342-02

- M-REQ-APPROVAL approve 与 M-RELEASE release-decision 可在 UI 沿已有端点完成并落对应事件
- CLI 仍可用作管理后门，但常规门处理无需离开 UI

## FR-0343 awaiting 动作面板

### AC-FR0343-01

- 时间线节点处于 awaiting 时，其下栏详情呈现该门的动作面板：原因、影响与唯一主要动作入口
- 下栏详情与动作面板为同一 surface；用户可在下栏直接进入门处理

### AC-FR0343-02

- 非 awaiting 节点下栏不呈现门动作入口，仅呈现节点详情
- Review 移除后，阶段评审均可在对应 awaiting 节点动作面板完成，无悬空门

## FR-0344 评审轮文档评论与编辑写入

### AC-FR0344-01

- 在文档阶段评审轮，评审中的文档可在 UI 直接修改与发表 inline-comments，写入经命令面事件落盘可查

### AC-FR0344-02

- pre-approval 模式下基线为空时，保存与评论提交不被阻塞
- 写失败时编辑内容不丢，可重试；不伪报成功

## FR-0345 自驱动端到端

### AC-FR0345-01

- 在 UI 发起门动作后，无需手工触发下一步，下一道门到达时在时间线 UI 呈现且活跃指针前移
- 用户可沿时间线连续处理多道门直到 run 进入下一阶段或完成

### AC-FR0345-02

- 门间推进无需手工 dispatch 命令；CLI 不作为常规推进路径

## FR-0346 分流检测栏目与合同

### AC-FR0346-01

- 任一新 story 的 TRIAGE 报告含重复检测栏目（跨版本引用指认既有 FR/AC，建议 NO-GO 或改写为增强）与冲突检测栏目（列出新需求 vs 既有 FR 的冲突对，GO 需显式确认已知晓冲突）
- 统一入口建议面板的重复/冲突内容与同一规则一致

### AC-FR0346-02

- 两项检测的报告格式与触发规则文本写入 M-STORY TRIAGE 派发合同与 flow.md §4
- 检测只落报告栏目，不做运行时拦截或自动 NO-GO；无自动驳回行为可观察

## FR-0347 评审人复核合同：非本人发起线程不构成 revise 依据

### AC-FR0347-01

- flow.md §4/§5 评审协议文本含条款：未解决的非本人发起线程不构成 revise 依据，内容验证通过即 pass，线程确认留给其发起人

### AC-FR0347-02

- 内容验证通过、仅余非本人发起未决线程的一次评审， verdict 为 pass 且不触发无发现文档修订
- 评审人自有新发现的 revise 均带锚定线程可追溯

## NFR-0157 单写路径与写安全

### AC-NFR0157-01

- UI 发起的全部写操作经命令面事件提交，写请求携带 `X-Trac-CSRF` 与 `Idempotency-Key` 头
- 重复提交不产生重复副作用；CLI 不呈现为第二常规写路径

## NFR-0158 工作台继承、真数据与可观察失败

### AC-NFR0158-01

- UI 全部状态来自服务端查询投影与事件流（timeline/ac-chain/run detail），不新增数据源，无 mock 数据
- API 失败时呈现用户可观察的失败反馈，不伪报成功；SSE 断线或事件重复乱序不使 UI 倒退

### AC-NFR0158-02

- v0.10 工作台裁定继续有效：外壳钉视口、Vercel 极简设计令牌、registry 行紧凑化、pane bar 单控制面、自动保存模式，现有行为无退化
