---
architecture_id: ARCH-NNN
spec_ref: SPEC-NNN
created: {YYYY-MM-DD}
status: draft
sha:
---

# {版本} — 架构

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留内容）：
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

## 2. 技术选型

{语言/框架/关键依赖的选择与替代方案；与既有依赖冲突时的取舍}

## 3. 交付与运行合同（machine contracts）

{至少覆盖：integration/e2e 测试基础设施、GitHub CI、pre-commit、release version、
build/artifact、发布恢复。每项写合同（外部可观察）与责任方（Runtime / 门禁 / Agent）}

## 4. 有意识简化与风险

{有意简化（含代价）与 spike-pending 项；只写会改变范围或使设计不成立的风险}
