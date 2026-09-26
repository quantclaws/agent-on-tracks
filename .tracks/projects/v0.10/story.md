---
envelope: tracks-envelope:v2
story_id: S-001
title: {一句话标题}
created: 2026-09-26
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
