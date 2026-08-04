RGR(Red-Green-Refactor) 是 Agent On Tracks 在开发阶段使用的一个重要方法。它是 Test Driven Development 的一部分。

这种方法要求先写出一个**失败的测试**（Failing Test）。

完整的 RGR（红-绿-重构）三步循环是：

1. **🔴 红（Red）**：先写一个**失败的测试**。因为这个功能还没实现，测试运行后一定会报错（显示红色），用来明确你要实现的目标。
2. **🟢 绿（Green）**：用**最少的代码**让这个测试通过（变绿），不用考虑代码结构是否优雅。
3. **🔵 重构（Refactor）**：在测试全部通过的前提下，**优化和清理**刚才写的代码，消除重复，提高可读性。重构时测试必须始终保持绿色。

在 **Agent On Tracks**（或 AI Agent 开发）的语境下，这种方法尤为重要——它要求 Agent 先“想清楚验收标准（写测试）”，再“动手写实现代码”，能有效避免 Agent 过度发散或生成无用代码。

RGR 是Kent Beck在《Test-Driven Development: By Example》中提出的工作流，直接应用到 Agent 开发中，会有一点小问题，就是 Agent 可能先编造一个失败的结果，然后再在开发 SUT 的过程中，连同单元测试一起改掉，从而达到全绿的效果。

Agent On Tracks 通过另一个 Agent 的独立评审 + skill 来保证这件事不会发生。

但这还只是第一步。Integration/End to End 测试是先于 SUT ，由另一个 Agent(Shield) 开发的。Shield 在开发Integration/End to End 测试时，只能依靠需求说明和接口来编写，从而完全杜绝了作弊的可能。

SUT 由 Devon 开发，它在开发时，只能接触到自己写的单元测试，而看不到测试目录下的 integration 和 e2e(end to end 测试文件目录)，所以，它除了正确实现规格需求这一条路之外，没有其它的方法可以让 int/e2e测试通过。

如果无法通过，那就一遍遍地 loop -- 这一切是自动化的。而自动循环，正是机器擅长的事。

正确的代码并不一定就是好的代码。一个可发布的产品，它的代码一定要是可维护的。任何设计和实现上的坏味道，都可能传染、积累，直到整个躯体腐烂。这正是 Refactor 阶段要做的事情。因为正确标准已经建立，所以可以放心重构。

更多的信息，请见[Slop Free](slop-free.md).
