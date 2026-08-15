---
name: tracks-prism-impl
version: 0.1
description: D-29 M-IMPL 代码评审判据包 - Prism 在 M-IMPL 评审消费的语义判据
---

# D-29 M-IMPL 代码评审判据包

Prism 在 M-IMPL 评审按 assignment 指定的名称+版本加载本判据包，审 Devon 的实现代码。本判据包只含**语义判据**（实现遵循锁定设计、可读性/职责/DRY/变更影响、测试反模式、命名稳定性、浅层安全扫描），不含形式校验规则（template 合规、binding 完整性、ID 文法归 Runtime 程序校验，D-14 边界）。

## 判据 1：实现遵循锁定设计

- 实现是否遵循锁定 Architecture/Interfaces，不允许实现者重新选择设计。
- 实现不得偏离 interfaces.md 声明的外部契约（事件 type/payload、CLI 输出、文件 schema、git 状态）。
- 实现不得引入设计中未声明的模块边界或依赖方向。

实现重新选择设计或偏离锁定契约的判 revise，经 `trac discuss` 锚定线程指出哪条锁定的 IF/ARC 被偏离。

## 判据 2：可读性、职责、DRY、变更影响

- 代码可读性：命名清晰、结构合理、无过度嵌套。
- 职责单一：每个模块/函数/类有明确的单一职责。
- DRY：无显著重复逻辑（可提取为共享工具）。
- 变更影响：实现修改是否影响设计未声明的下游消费者。

## 判据 3：测试反模式

测试不得出现以下反模式：

- 修改断言迎合实现（cheating pattern #1）。
- 无依据 skip（无 GitHub issue 链接）。
- 断言降级（`assert issubclass(X, Exception)` 而非实际提交并捕获）。
- 吞异常（`try: ... except: pass` 包裹被测代码）。
- 过度 mock（mock 框架内核而非外部依赖）。
- 从实现取 ground truth（因实现输出 X 所以断言 X）。
- 捏造硬编码值（非 fixture/重算的预期值）。
- 无效断言（`assert True` / `assert 1 == 1` / `assert <obj> is not None` 作为唯一断言）。

## 判据 4：命名稳定性

- diff 中不得在目录/模块/文件名中嵌入版本号或时间前缀（`cli_v12.py`、`api_v2/`、`new_xxx.py`），除非 spec 明确声明共存窗口。

命名不稳定的判 revise，指出哪个文件/模块名违反了命名稳定性。

## 判据 6：advisories 消费（PRISM_FINAL 必查，2026-08-15）

Devon 每个 outcome 的 evidence JSON 可携带 `advisories`（`isolation`/`scope_gap` 等风险自报，事件库 `outcome.received` payload 内）。PRISM_FINAL 必须逐任务读取这些 advisories 并独立核验——自报是线索不是结论：`isolation` 需查 dispatch 隔离证据、`scope_gap` 需对照 tasks.json scope_boundary。未核验的 advisory 不得默认无害。

## 判据 5：浅层安全扫描

只报告明显安全问题：

- `eval`/`exec` 使用。
- 硬编码 secret。
- SQL 拼接。
- `shell=True` + 不可信输入。

## 边界（D-14）

本判据包不含形式校验规则：

- template 合规（frontmatter、section 结构）：Runtime 程序校验（FR-0150）。
- binding 完整性（AC↔IF↔test trace）：Runtime trace 工具（FR-0080）。
- ID 文法（BS-XX/FR-XXXX/AC-FRXXXX-YY）：Runtime `trac validate`（FR-0130）。
- 测试集合/执行/Red 分类：Runtime RED_CHECK 程序分类（FR-0050）。

Prism 的语义判据与 Runtime 的形式校验互补：Prism 可放行语义合格的实现，但 trace/binding 不合规的情况仍由 Runtime 程序校验捕获。

## DIAGNOSE 输出合同（2026-08-15，run 01KZTHE7 T-008 教训）

DIAGNOSE dispatch 的诊断结论**必须机器可读**：最终回复以一个裸 JSON object 结束（Runtime 从你最后一条 text 消息提取，散文结论不构成交付）：

```json
{"classification": "test_defect|impl_defect|stub_gap|ac_gap|spec_gap", "reason": "...", "evidence": "..."}
```

`classification` 是五选一的唯一判定；reason 一句话；evidence 指向具体文件/行/命令输出。分析与论证放正文，结论放 JSON。
