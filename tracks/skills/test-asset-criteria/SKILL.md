---
name: test-asset-criteria
version: 0.1
description: D-29 测试资产判据包 - Prism 在 M-TEST PRISM_REVIEW 消费的语义判据
---

# D-29 测试资产判据包

Prism 在 M-TEST PRISM_REVIEW 按 assignment 指定的名称+版本加载本判据包，审 Shield 编写的测试合约。本判据包只含**语义判据**（忠于 AC、断言落公开出口、counterexample 绑定、无伪测试），不含形式校验规则（marker 长格式、绑定完整性、ID 文法归 Runtime 程序校验，FR-0080/FR-0130，D-14 边界）。

## 判据 1：忠于 AC

每条测试必须忠于其引用的 AC（docstring/注释首行的 `AC-FRXXXX-YY@<version>` marker）：

- 测试断言的行为与 AC 描述的可观察效果一致，不断言 AC 未要求的内容。
- 测试不通过"重新定义成功条件"来适配实现（cheating pattern #1）。
- AC 要求的可观察出口在测试中被实际读取和断言，而非仅调用后忽略结果。
- 多条 AC 共用一个测试函数时，每条 AC 的可观察效果都有对应断言。

不忠于 AC 的测试判 revise，经 `trac discuss` 锚定线程指出哪条 AC 的哪个可观察效果缺失或偏离。

## 判据 2：断言落公开出口

测试断言只能落在 interfaces.md §4 定义的外部可观察出口（事件、CLI 输出、文件 schema、git 状态）：

- 不断言内部 State 字段、私有方法、中间数据结构（interfaces.md §1.2 non-observable）。
- 不通过 mock/patch 框架内核（M-TEST 状态机、Red 分类、trace/reach 逻辑）来"使测试通过"（cheating pattern #5）。
- 不通过 import 内部模块来窥探状态（如直接读 kernel.machine.State 字段而非经 `trac status`/events 观察）。
- 事件断言读取 events 表的 type + payload 字段，不依赖事件 seq 或 ts（确定性，NFR-0020）。

断言未落公开出口的测试判 revise，指出应使用哪个 interfaces.md §4 出口。

## 判据 3：counterexample 绑定

每条 required 测试（integration/e2e 层）绑定一个 counterexample：

- counterexample 是一个只偏离目标合同的最小行为补丁（git patch），验证该测试能将其杀死（killed）。
- counterexample 不偏离无关合同（最小性：只改目标行为，不改其他）。
- counterexample 的 kill 证据在测试或 manifest 中可追溯（tests/counterexamples/ 下的 patch 文件 + kill 记录）。
- 缺失 counterexample 或 counterexample 不能被测试杀死的判 revise。

## 判据 4：无伪测试

测试不得是伪测试（cheating patterns §1.3）：

- 非 `assert True` / `assert 1 == 1` / `assert <obj> is not None` 作为唯一断言（#8）。
- 非 `try: ... except: pass` 包裹被测代码（#4）。
- 非 skip/ignore 无 GitHub issue 链接（#2）。
- 非硬编码预期值（#7）：预期值来自 fixture 数据或 ground truth 重算，非"因为当前实现输出 X 所以断言 X"。
- 非断言降级（#3）：`assert issubclass(X, Exception)` 而非实际提交并捕获。
- 非过度 mock（#5）：mock 框架内核而非外部依赖。

伪测试判 revise，指出违反了哪条 cheating pattern。

## 判据 5：合法 Red（M-TEST 特有）

M-TEST 交付的是测试资产而非绿色结果。每条测试必须失败且失败必须合法（FR-0050）：

- 合法 Red：行为断言失败（AssertionError）、桩合同 token 失败（`NotImplementedError("IF-...")`）、合同声明的 symbol 缺失。
- 非法 Red：collection/语法/fixture/import 错误（测试本身有缺陷，非被测合同未实现）。
- 测试意外通过视为非法（桩只 raise，通过意味断言空洞或未命中桩）。

Prism 在 PRISM_REVIEW 阶段判断测试合约的语义合法性；Runtime 在 RED_CHECK 阶段独立复跑做程序化分类（不信 Prism 自述）。Prism 的语义判断与 Runtime 的程序分类互不替代。

## 边界（D-14）

本判据包不含形式校验规则：

- marker 长格式强制（`AC-FRXXXX-YY@<version>`）：Runtime 程序校验（FR-0080/FR-0130）。
- 绑定完整性（每条 required AC ≥1 marker、无无主 marker）：Runtime trace 工具（FR-0080）。
- ID 文法（BS-XX/FR-XXXX/AC-FRXXXX-YY）：Runtime `trac validate`（FR-0130）。
- tombstone 规则：Runtime trace 工具（FR-0080/FR-0130）。

Prism 的语义判据与 Runtime 的形式校验互补：Prism 可放行语义合格的测试合约，但 marker 不合规的情况仍由 EXIT trace 复跑捕获（SM-01.15 恢复路径）。
