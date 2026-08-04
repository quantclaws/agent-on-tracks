## Slop-Free

AI 生成的代码常常会夹杂着大量的垃圾代码，即使它们通过了测试，工作起来也正常；但仍然是垃圾代码。这些垃圾代码会从多个方面损害你的项目：

1. 当 AI 需要进行一次代码探索任务时，扫描垃圾代码会使用更多的 token；
2. 垃圾代码的坏味道会引起 AI 的幻觉。

Agent On Tracks 保证你的 Agent 写出来的代码没有一个字节的垃圾。

Devon 是你项目中的程序员。他会按照 R-G-R 的次序编写代码。它先是一个由 Spec 推导出来的测试验证函数；由于没有任何实现，所以，此时代码运行起来会是红的 -- 这就是 RGR 中，第一个 R 的来源（RED）。接下来，它会编写生产代码（SUT, Software Under Test），让测试通过，此时就完成了 Green 阶段。

接下来的 R （Refactor） 阶段，正是消除代码中的坏味道的地方。如果 Devon 写了超长的文件、超长的函数、超过认知复杂度的代码或者重复的代码，Tracks 会立即检测到，并且要求 Devon 立即进行修改。

Tracks 并没有发明新的检测方法 -- 我们只不过是应用了成熟的软件工程理论和工具。代码检测并不消耗任何 token；带着具体的任务范围的重构，也只会消耗少量 token；关键是，你的项目继续保持代码整洁、高信息密度，从而 Agent 可以继续保持高效的工作。

我们使用的理论包括：

- Cognitive complexity）。它由Ann Campbell（当时在 SonarQube 负责 Java 代码分析）。
- 基于 token 滑动窗口、Rabin-Karp 风格 hash 和编辑距离相似度算法，检测代码重复。
- 基于 McCabe/McConnell 法则确定最长函数长度
- 基于"The Magical Number Seven, Plus or Minus Two"(George A. Miller)，确定最大函数局部变量数
- 基于"Structured Design"(Edward Yourdon, 1979), "Code Complete"(Steve McConnell)，确定最大文件长度

你的 AI 生成的代码，符合所有最佳软件工程实践！

> [!info] 关于 ai-slop-cleaner
> [ai-slop-cleaner](https://github.com/realsigridjin/ai-slop-cleaner) 是著名的 Ralph skills set 中的一个。它并不是代码质检工具。它是帮助你消除文章中的 AI 味儿的。

> [!info] 与 SpecKit 对比
> Spec Kit 用九项 Article 宪法 + Phase -1 Simplicity/Anti-Abstraction Gate + test-first 文件顺序，约束写在 markdown 模板里由 Agent 自己勾选。与 Agent on Tracks 相比，它主打 Agent 自觉，无法强制 Agent 执行。此外，Speckit 是同一个会话中进行 Agent 提示词互换，但上下文依然是共享的。这种模式上，难免受到代码实施者的干扰。

