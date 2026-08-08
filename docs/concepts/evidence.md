# 证据模型

## 什么不算证据

Agent 说“已经完成”“测试通过”“没有改其它文件”，都不构成 Tracks 的流程证据。

这些话可能有诊断价值，却来自生产者自己。模型可能漏枚举、误读日志或把计划中的动作写成已经发生。即使内容完全诚实，它也没有绑定哪个 commit、哪次 attempt 和哪份 baseline。

因此，Agent backend 返回的 `self_report` 和 stdout JSON 只进入诊断记录。权威产物是 Runtime 从目标文件、受控 diff 和程序出口重新读取的结果。

没有绑定输入 revision 的旧测试报告、聊天截图或手工复制的 CI 摘要同样不能推进流程。

## 程序证据

Tracks 使用的程序证据包括：

**事件流**：`events` 是 append-only 事实源；`runs` 与 `backlog` 是可重建投影。

**文件与 diff**：Agent 运行前后由 Runtime 比较工作区，目标 diff 是权威产物，越权路径形成 audit evidence。

**进程出口**：Runtime 根据宿主测试合同自己执行 collection 和测试，记录退出码、stdout/stderr 摘要与合法 Red 分类。

**Digest**：文档 body、baseline 与冻结 artifact 使用 SHA-256 绑定内容；checkpoint 前再次复验，内容变化会使证据失效。

**Git 事实**：受控 commit、base SHA、commit SHA 和 command identity 共同证明某次结果落在哪个仓库状态。

**讨论状态**：inline discussion 的 open/reopen/resolved 和 ready blocker 来自文档全文解析，不靠 reviewer 的“没有意见”。

以后实现 M-VERIFY/M-PUBLISH 后，CI、build artifact 与发布平台回读也会成为同一模型中的证据；它们目前属于已锁定设计，不是现行能力。

## 身份绑定

一份报告只有在回答“针对什么、由谁、哪次运行产生”以后，才能用于流程判断。

**`command_id`** 标识 Runtime 发出的单次 command。`command.issued` 与结果事件共享它，恢复时也使用原 identity。

**baseline/revision digest** 绑定 story、spec、acceptance 或其它输入内容。Human approval 与 digest 绑定，输入一变，旧 approval 不能继续使用。

**base SHA 与 commit SHA** 把结果放进 Git 历史。若 checkpoint 时 HEAD 已偏离 assignment 的 base，Runtime 停止而不是把结果提交到错误基线。

**attempt 与 task identity** 区分同一角色的重试。旧 attempt 保留审计记录，不被新一次输出覆盖。

**actor** 说明 Human 决定或 Agent assignment 的来源；criteria pack identity 则证明 Prism 实际按哪份判据审查。

这种绑定不能保证结果语义正确，但能防止把正确结果错贴到错误版本上。

## freshness 与 stale

证据不是永久通行证。它依赖的输入一旦变化，证据就可能 stale。

需求 baseline 改动会改变 revision digest，旧 approval 失效，Runtime 重新生成 preview。讨论线程重排会让 `T-NNN` 漂移，写命令返回 stale 并要求重新 query。Human 修复程序或配置后，可以显式 `retry --clear-evidence`，表示旧 failure evidence 不再适用。

Tracks 不会把所有历史 verdict 从事件流删除。历史仍用于审计；当前 projection 根据新的 identity 判断哪些结果仍可使用。

> [!info] Freshness
>
> Freshness 表示证据仍然绑定当前输入，而不是“报告最近生成”。内容 digest、revision 和 dependency identity 比时间戳更能判断一份结果是否仍有效。

## 能证明什么

当前实现能程序化证明：

- 文档符合 Tracks 的结构约定；
- FR/NFR、AC 与 test marker 的声明链不存在指定类型的孤儿；
- Python 生产模块在静态 import 图上是否可由声明入口到达；
- integration/e2e 可 collect，并以允许的 Red 类型失败；
- Agent 修改是否越过写范围；
- capture 与 checkpoint 之间 artifact digest 是否变化；
- 同一事件流是否折叠出同一状态。

它不能单独证明：

- marker 所在测试忠实、完整地表达 AC；
- 需求本身正确、有价值或符合真实世界；
- 动态加载模块一定可达；
- 一个 counterexample 代表所有错误；
- 计划中的 M-IMPL、M-VERIFY 和发布门禁已经生效。

证据模型的价值不是宣布“软件绝对正确”，而是把每个流程结论缩小成可复核、可重跑、有明确边界的事实。
