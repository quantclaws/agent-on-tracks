# Agent 角色

## 为什么分角色

一个通用 Agent 可以从需求写到代码，但“什么都能做”不等于“每一步都值得相信”。Tracks 用角色分工约束输入、输出和写范围，也让评审者不必继承生产者的全部上下文。

角色代表结构位置，不代表不同模型。多个角色可能由同一种 LLM 执行；独立性来自 assignment、权限、工件 revision 和评审方向，而不是名字本身。

每个角色都应能用一句话说明职责，也能用一句话说明**不得做什么**。

## 需求角色

**Scribe（已实现）**：把原始需求展开成 story，保留 raw seed，补齐用户旅程和 Behaviour Seed。不得写 spec/acceptance，也不在 triage 前擅自展开被 park 的需求。

**Sage（已实现）**：在 M-STORY 评审 story，在 M-SPEC/M-ACC 起草 spec 与 acceptance。作为 reviewer 时不直接改作者正文，finding 通过 inline discussion 锚定。

**Lex（已实现）**：独立评审 spec 与 acceptance 的忠实性、可断言性和范围保真。Lex 的定义禁止编辑工件正文，只返回 PASS/REVISE 与 finding。

## 设计与评审角色

**Archer（已实现）**：在 M-DESIGN 产出 architecture、interfaces、test-plan、接口桩与必要 ground truth。它负责把已批准需求变成可实现合同，不得修改需求工件或编写最终实现。

**Prism（已实现）**：独立评审设计候选、Shield 测试合同和实现 range。它不写代码、不修改正文、不 commit/push；PASS 只是一项绑定 revision 的语义 verdict，不代表测试已经通过或阶段已经退出。

Prism 还承担合同争议诊断：测试失败究竟来自实现、测试、接口还是 AC。归因决定流程返回哪里，技术分流不交给 Human 猜测。

## 测试与实现角色

**Shield（已实现）**：在 M-TEST 对着接口桩编写 integration/e2e 合同测试。Shield 不改产品代码和接口桩，不选择新框架，不 commit/push，也不能判定 PASS。Runtime 负责 collection、合法 Red 和 trace，Prism 评审测试是否忠于 AC。

**Devon（设计已锁定，正在实现）**：在 M-IMPL 按 task 走 Red-Green-Refactor，补 unit tests、实现最小 Green，再清理坏味道。Devon 只能修改 manifest 授权文件，不得修改 Shield 测试或 Git history。

两者的分离构成大小两个测试循环：Shield 的外圈合同先于实现冻结，Devon 的内圈 RGR 逐步让合同变绿。受限视图和反馈脱敏属于 M-IMPL 的已锁定设计，不应当作当前已发布能力。

## 安全与知识角色

以下角色已经进入完整 flow 设计，但尚未实现：

**Judge（计划中）**：在 M-SECURITY 做语义安全审计，把 finding 归因到实现、测试、设计或产品合同。

**Librarian（计划中）**：在 M-MILESTONE 从已归档 release 提炼知识，只修改授权 wiki/docs。

**Guide Agent（路线图）**：未来 Web 界面中的常驻引导角色，帮助 Human 理解当前阶段、可做决定和证据位置。Guide 不取代 Runtime，也不获得推进状态的权力。

## Agent 的共同限制

所有角色共享几条约束：

- assignment 固定输入 revision、输出目标和预算；
- Agent 不直接推进 stage；
- Agent 不创建正式 commit、不 push、不改 Git history；
- 写入范围由 Runtime 授权并在结束后用 diff 审计；
- stdout JSON 和 self-report 只作诊断，不是权威产物；
- 找到上游缺口时提交有锚点的 advisory，不自行改需求；
- 技术归因由 Agent 团队完成，产品语义才返回 Human。

这些约束使角色成为可组合的专业节点，而不是一组拥有仓库全部权限的聊天人格。
