# 独立验证

## 自证为什么不可靠

一名开发者写完代码，再由自己证明代码正确，这在小项目里很常见。问题不在诚实，而在视角：生产者知道实现细节，也知道怎样让验证看起来通过。

Agent 会放大这个问题。同一个上下文里，它可能先写 mock，再验证 mock 的行为；可能漏掉需求，却在总结中写“全部完成”；也可能同时调整实现和测试，直到两个错误彼此吻合。

Tracks 因此不把生产者的自述当作完成证据。测试结果由 Runtime 重跑，语义评审交给没有写过该产物的角色，需求覆盖由程序双向清点。

这不是怀疑 Agent 的品格，而是消除**自证结构**：同一个主体不能既定义答案，又宣布自己答对了。

## 三方分离

Tracks 把验证拆成三种责任：

**生产者**创造待验证产物。Scribe 写 story，Sage 写 spec/acceptance，Archer 写设计，Shield 写合同测试，Devon 负责实现。

**验证者**从另一种专业视角寻找缺口。Sage 评 story，Lex 评 spec/acceptance，Prism 评设计、测试合同与实现 range。

**Runtime 裁决者**不做语义创作。它重跑 collection、测试、trace、模板检查和 Git 审计，再根据程序结果推进或回退状态。

Prism 的 PASS 也不是流程事实。它只表示“对绑定 revision 的语义评审通过”；测试是否真的运行、commit 是否存在、阶段是否退出，仍由 Runtime 从权威出口确认。

三方分离使意见可以互相挑战，也使任何单一 Agent 都无法靠一段完成报告推进流程。

## Parity Oracle

有些功能不是简单地“调用后返回 200”，而是在实现一套算法或规则。例如 trace 要找孤儿 ID，reach 要从入口计算可达模块。此时测试需要一个独立答案来源。

Tracks 把这种做法称为 **Parity Oracle**：生产实现与一份独立参考实现读取同一输入，两边输出必须相同。

当前 trace 与 reach 已经使用这种方式。参考实现位于 `tests/ground_truth/`，只使用标准库和测试数据，不得 import `tracks.*`；parity 测试把工具实测输出与参考输出逐项比较。

> [!info] Test oracle
>
> Oracle 是判断测试结果是否正确的依据。Gerard Meszaros 在 *xUnit Test Patterns*（2007）中系统整理了 test oracle 与 test smell；独立 oracle 的价值在于预期值不由被测实现自己生成。

Parity Oracle 只适合有独立计算路径的规则/算法。普通业务行为可以使用明确的 AC 和固定测试数据，不应为了形式完整再复制一套生产系统。

## 结构隔离

独立角色仍可能共享同一模型、同一代码库和同一上下文。只换名字，不足以构成独立验证。Tracks 还要求结构隔离。

M-TEST 中，Shield 在生产实现存在之前，依据 AC、Test Plan 和接口桩编写 integration/e2e 测试。它不能修改产品代码或接口桩，也不能宣布测试 PASS。Runtime 负责 collection、合法 Red 和 trace 门禁，Prism 负责测试合同的语义评审。

M-IMPL 的 Devon 受限视图已经在 flow 合同中锁定，目前正在实现。设计要求 Devon 看不见 Shield 的合同测试原文，只能依据接口和分类反馈实现行为；测试缺陷只能回到 Shield 修，不能由 Devon 顺手修改。

关键测试还可以绑定 counterexample：人为构造一个最小错误行为，测试必须能将它杀死。

> [!info] Mutation testing
>
> DeMillo、Lipton 与 Sayward 1978 年的论文 *Hints on Test Data Selection* 奠定了变异测试思想：主动注入小错误，检查测试是否能够发现它。

这些机制减少生产者和验证者相互迎合的机会，但同一模型仍可能共享偏见；结构独立不等于认知独立。

## 能证明什么

独立验证可以建立一组更强的程序事实：

- 测试在没有实现时可 collect，并以合法原因失败；
- marker 声明的 AC 与测试双向无孤儿；
- 独立参考实现与生产 checker 输出一致；
- counterexample 被测试捕获；
- Agent 的写入范围没有越过 assignment；
- Runtime 从真实程序出口重跑并记录结果。

它不能证明：

- 用户真正需要的就是文档所写内容；
- AC 已穷尽所有重要行为；
- 一个 counterexample 代表所有错误实现；
- ground truth 本身永远没有错误；
- 相同模型扮演的不同角色具有完全独立的判断。

所以，独立验证不是数学证明。它把“请相信我”转换成“这是输入、这是独立检查、这是可重复的结果”，让错误更难隐藏，也让事后复核成为可能。
