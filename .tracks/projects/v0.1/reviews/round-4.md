# v0.1 差分复审 — Round 4

范围：**仅审第三轮之后的差分**（提交 `a6b996e` + 当前未提交修改），不重复审查未变化部分。

结论：**REVISE（差分有 3 个小缺口）**

## 已通过

- D-14 已把 Human 对“形式校验 vs 语义翻译”的裁定正式落入 Decisions；
- FR-23、AC-23b、`spec.committed.spec_sha` 的修改方向正确，关闭了 R3-06 的主体问题；
- Round 2 中新增的 qoder 批注只是评审历史，不改变生产合同。

## 差分问题

### R4-01 新增 AC-23b 未进入 Test Plan 映射

定位：`acceptance.md:160-165`；`test-plan.md:62`。

Test Plan 仍写 `AC-18a ~ AC-23a`，没有覆盖新增加的 AC-23b。按当前范围，测试计划无法证明最终 spec sha。

要求：改为覆盖 `AC-23a/b`，并在 happy-path 明确断言 frontmatter sha、事件 spec_sha 和正文 hash 三者相等。

### R4-02 最终 `spec.committed` 事件的产生时点不明确

定位：`spec.md:60-65`；`acceptance.md:160-165`；`interfaces.md:50-54`。

FR-23 新增了“写 sha、提交、stage.exited”，但没有明确追加 `spec.committed`；AC-23b 却要求存在带 `spec_sha` 的 `spec.committed`。同时 DRAFT 阶段本来就会提交 spec，测试可能误匹配早期 `spec.committed`。

要求：FR-23 明确最终提交后追加 `spec.committed(final=true, commit_sha, spec_sha)`，或新增明确的 finalization 事件；AC 必须匹配 EXIT 阶段的最终事件，不能匹配草稿提交。

### R4-03 sha 算法仍有自引用歧义

定位：`spec.md:65`；`acceptance.md:165`。

FR-23 写“计算 spec.md 内容 sha256，写入同一文件”，字面上会产生自引用；AC-23b 则写“正文 sha256”。两者应使用同一个可复现算法。

要求：明确 hash 输入，例如“UTF-8 正文（不含 frontmatter）”，或“将 frontmatter.sha 规范化为空串后的完整文件字节”；Story 和 Spec 使用同一算法与换行规范。

## 差分通过门槛

上述三点补齐后，本轮差分可通过。此结论不代表 Round 3 中未修改的问题已经关闭。
