---
architecture_id: ARCH-NNN
spec_ref: SPEC-NNN
created: {YYYY-MM-DD}
status: draft
sha:
---

# {版本} — 架构

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留内容）：
  - 交付文档中出现的 blockquote 一律是 inline-discussion 讨论线程，模板指引不得残留为 blockquote。
  - 职责边界：本文档只描述架构决策——模块边界、技术选型与宿主项目 machine contracts。
    跨模块的类型化 schema / CLI 合同写入 interfaces.md；测试策略写入 test-plan.md。
  - 延续性优先：若存在上一版架构，§0 必须逐条声明继承或变更；未提及者一律继承。
    判定原则：除非 story/spec 明确要求，否则延续既有架构。
  - contracts 由 Archer 设计（flow.md §8 硬规则 2）：安装/更新/回读副作用只归 Runtime，
    任何角色不得安装或修改 hook 绕过门禁。
  - 决策记录在 inline-discussion 中；只有未 resolved 的 inline-discussion 才能阻塞评审退出。
  - 一般不使用表格，因为它不利于使用 tracks-discuz skill 来评论。如果必须要使用，则表格必须增加行序号列以方便引用。
-->

## 0. 延续性声明（什么不变）

{上一版架构合同中逐项声明：不变 / 变更（含理由）。首版架构写「无（首版）」}

## 1. 模块边界

{包/模块划分、增长轴、分层约束；新代码落在哪个既有增长轴上，或为何需要新轴}

## 2. Scaffold 宣言

<!-- 模板指引（填写完成后删除本注释，交付的文档只留内容）：
  - Archer 作为团队 kickoff 脚手架负责人（team-lead scaffolder），在 M-DESIGN 阶段
    可以创建哪些宿主项目文件，以本节为唯一合同：一项一个文件，格式
    `- path — purpose`（path 为仓库相对路径），kind 从
    stub / config / data / ground-truth / ci-skeleton 中命名。
  - 只有本节列出的文件可以被创建；scaffold 内容仅限声明、配置、数据与
    ground truth——不得包含任何业务行为（业务行为属于实现阶段）。
  - tests/ground_truth/** 为固定例外：ground truth 必须存在且被完整实现。
  - 未声明的写盘是审计违规（undeclared_scaffold）：Runtime 拒绝该 outcome 并回滚。
  - 质量守卫的配置文件在这里逐一列出；守卫的安装命令与 CI required check
    写入「交付与运行合同（machine contracts）」，分工见 skill tracks-quality-guards。
-->

- {path} — {purpose}（kind: stub / config / data / ground-truth / ci-skeleton）

## 3. 技术选型

{语言/框架/关键依赖的选择与替代方案；与既有依赖冲突时的取舍}

## 4. 交付与运行合同（machine contracts）

{至少覆盖：integration/e2e 测试基础设施、GitHub CI、pre-commit、release version、
build/artifact、发布恢复。每项写合同（外部可观察）与责任方（Runtime / 门禁 / Agent）}

## 5. 有意识简化与风险

{有意简化（含代价）与 spike-pending 项；只写会改变范围或使设计不成立的风险}
