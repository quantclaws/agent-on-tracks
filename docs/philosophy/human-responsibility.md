# 人类、Agent 与 Runtime 的责任边界

## 人类不可委托的责任

Agent 可以推导技术方案，却不能替你决定产品为什么存在。

Human 负责产品意图：用户是谁、问题是否值得解决、哪些差异构成产品价值、哪些后果不可接受。范围、优先级、隐私含义、不可逆业务语义也属于这一层。

当需求 baseline 即将冻结，Human 需要确认“这是我要的结果”；当发布即将产生真实外部副作用，Human 需要确认“现在可以发布”。这些决定不能从 lint、测试或模型评分中自动推导。

Human 不需要替 Agent 选择数据库、接口拆分或测试策略。把技术选择抛给 Human，并不会让流程更安全，只会把专业责任转移给信息最少的人。

## Agent 负责的专业工作

Tracks 把软件团队的专业分工交给不同 Agent：

- Scribe 从原始输入展开 story；
- Sage 把 story 转为 spec 与 acceptance；
- Lex 独立评审需求工件；
- Archer 设计架构、接口与测试计划；
- Shield 编写实现之前的合同测试；
- Prism 评审设计、测试和实现；
- Devon 的逐 task RGR 职责已经锁定，正在 M-IMPL 中实现。

Agent 可以提交 finding、advisory 和 revision，但不能把技术争议包装成一道选择题交给 Human。若接口不足，流程返回 M-DESIGN；若 AC 缺失，才回到需要 Human 确认的产品语义层。

## Runtime 负责的机械事实

Runtime 不负责创作，它负责让流程遵守已经确定的规则。

它是唯一能够推进 stage、签发 assignment、授予写 lease、创建受控 commit 和记录事件的组件。Agent 说“完成了”不改变状态；Human 在编辑器里改了一行，也不会绕开新的校验和评审。

Runtime 还负责从现实世界重读事实：Git diff、测试退出码、trace 结果、文档 digest、CI 与 artifact。它不把聊天摘要当作证据。

未来 Web 界面会替代 Human 直接输入 `trac` 命令，但不会取代 Runtime。按钮只是另一种输入通道，状态推进和程序门禁仍然由同一个 Runtime 执行。

## 两个 Human gate

完整 flow 设计只有两个 Human gate **stage**。

**M-REQ-APPROVAL** 冻结需求 baseline。Human 看到 story、spec、acceptance 及其 digest，选择 approve 或 return。该阶段已经实现。

**M-RELEASE** 授权 merge、tag、publish 和部署等不可逆副作用。Human 可以 release、delay 或 return，但不能用发布确认绕过失败门禁。该阶段设计已锁定，尚未实现。

“两个 gate stage”不等于 Human 只出现两次。M-STORY 仍有 triage，需求文档仍有 review，重试耗尽也会 escalation 到 Human。区别在于，这些触点不会让 Human 接管架构或实现选择。

## 边界与例外

自动化必须知道何时停下。

高风险领域可能需要法规、医学、安全或组织政策要求的额外人工复核。Runtime 能证明门禁运行过，不能替机构承担法律责任。

Human 可以直接修改受管文档或代码，但 Runtime 会把这些字节当作新的输入：重新计算 baseline，标记旧证据 stale，并要求相应评审。Human 写的内容不会因为作者身份而自动获得 PASS。

凭据、外部服务所有权和无法读取的发布事实也可能触发 `needs_attention`。此时 Human 提供授权或事实，Runtime 再继续；不能用一句“我确认成功”覆盖未知外部状态。

Tracks 的目标不是把人从软件工程中删除，而是让 Human 留在真正需要价值判断和责任承担的位置。
