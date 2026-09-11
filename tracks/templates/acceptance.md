---
envelope: tracks-envelope:v2
acc_id: ACC-NNN
created: {YYYY-MM-DD}
status: draft
sha:
---

# {功能标题} — 验收标准

<!-- 模板指引（生成文档时阅读；填写完成后删除本注释，交付的文档只留验收内容）：
  - 职责边界：本文档是验收标准的唯一登记处；spec.md 只留需求描述，test-plan.md 以本文档为输入。
  - 组织：spec 中每条有效 FR/NFR 一个二级章节（标题与 spec 一致），其下每条 AC 一个三级标题
    `### AC-FRXXXX-YY`（四位 FR 编号 + 两位序号；YY 在条目内从 01 递增，不跨条目复用）。
  - 同步：FR 删除时对应章节一并删除（历史经 git 追溯），已用过的 YY 序号不再复用。
    删除的 ID 留 tombstone（`<!-- tombstone: AC-FRXXXX-YY -->`）。
  - 跨版本引用（FR-0130）：本版本内引用保持短格式；跨版本引用且存在歧义时附加版本限定
    `AC-FRXXXX-YY@<version>`（如 `AC-FR0010-01@v0.1`）。限定语法 opt-in，parser 不强制。
  - 内容约束：每条 AC 只写可从系统外部观察、可断言的通过条件（暴露的 API/CLI/UI 行为、
    结构化日志、落盘数据），不引用内部状态或实现细节；成功路径与边界/异常路径分开立条。
   - 决策记录在 inline-discussion 中；只有未 resolved 的 inline-discussion 才能阻塞评审退出。
-->

## FR-0001 {标题}

### AC-FR0001-01

  - {可观察、可断言的成功条件}
  - {可观察、可断言的成功条件}

### AC-FR0001-02

  - {边界条件 / 异常路径 / 错误处理}

## NFR-0001 {标题}

### AC-NFR0001-01

  - {性能 / 安全 / 兼容性 / 可维护性——可量化指标}
