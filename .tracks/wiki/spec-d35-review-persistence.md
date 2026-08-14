---
change_id: SC-D35
date: 2026-08-15
status: accepted (pending v0.6 M-SPEC incorporation)
decision_ref: D-35 (.tracks/wiki/decisions.md)
target_fr: FR-0240
---

# SC-D35 评审意见按阶段分通道持久化 — 规范变更提案

> 用户裁定（2026-08-15）：必须做、暂不阻塞当前 v0.5 run。本文件为规范级追踪文本，
> 待下个版本 M-SPEC 阶段经修订日志（R-n）正式并入 spec.md；实现完成前 flow.md/arch.md
> 不收录（文档只描述已存在的行为）。

## 1. 背景与问题

manifest 评审协议最初只针对 M-DESIGN 设计：评审 finding 经 `trac discuss` 锚定进
markdown 文档正文，随 checkpoint commit 入 git，diff 即审计记录 —— 文档是天然载体。

M-TEST / M-IMPL 的评审对象是 Shield/Devon 的**代码**，意见无法嵌入代码，初始协议
下 Prism 的 outcome manifest 只返回路由元数据（verdict / criteria_pack /
defect_classification），评审正文无处结构化持久化。

实证（run 01KZTHE7RMZE6110PK9C54K1E2）：
- `prism.verdict` 事件（seq664）payload 仅 `{criteria_pack, diff_ref, result_id,
  verdict}` —— 无任何意见字段；
- FR-11 重派链路拿不到评审意见，Shield attempt 2 的 evidence 仍为上一轮遗留的
  `{"check": "non_zero_exit", "reason": "opencode exited 1"}`（叠加 42s 版本竞态：
  trac run 05:22:22 启动早于 f10dca3 05:23:04 提交）；
- M-TEST 阶段靠补偿通道工作（意见锚定 test-plan.md，在 Shield 上下文文档内可见），
  M-IMPL 阶段该通道语义错位（代码缺陷锚到设计文档），为已知缺口。

## 2. FR-0240 评审意见分阶段持久化协议

### 2.1 分通道行为

| 阶段 | 评审载体 | source of truth | 正文位置 |
|------|---------|----------------|---------|
| M-DESIGN | `trac discuss` 文档内锚定线程（现状不变） | git（checkpoint diff） | 文档正文，不单独成文 |
| M-TEST | manifest 结构化字段 + 单独成文 | 事件流（结构化副本）+ git（文档锚定保留） | blobs/{sha256} |
| M-IMPL | 同 M-TEST | 同上 | blobs/{sha256} |

M-DESIGN 的文档锚定通道**保留且不降级**；M-TEST/M-IMPL 新增结构化通道与其并行
（Prism 仍可锚定讨论线程，但 manifest 必须携带结构化字段）。

### 2.2 manifest 协议（M-TEST / M-IMPL，verdict ≠ pass 时）

```json
{
  "verdict": "revise",
  "criteria_pack": {"name": "...", "version": "..."},
  "defect_classification": "test_defect",
  "review_summary": "≤140 字符，一句话，manifest 只放摘要不放正文",
  "findings": [
    {
      "id": "PRISM-V05-R2-01",
      "severity": "blocker",
      "defect_classification": "test_defect",
      "criterion": "1+2",
      "artifact": "tests/integration/test_release_evidence.py:27-204",
      "ac_refs": ["AC-FR0232-01"],
      "summary": "≤140 字符"
    }
  ],
  "review_body": "完整评审正文（markdown），仅用于持久化，Runtime 不解析"
}
```

- `findings[].id` 沿用现行格式 `PRISM-<VER>-R<round>-<nn>`（存量格式不变）。
- 顶层 `defect_classification` 保留为路由主值（Runtime 四路回退依据，向后兼容）；
  `findings[].defect_classification` 为逐条归类，两者可不一致（主值由 Prism 综合
  裁定）。
- `review_summary` / `findings[].summary` 硬上限 140 字符；超限视为 manifest
  malformed（与现有 manifest 校验同一失败类）。

### 2.3 持久化数据流

1. `result_checkpoint` 在 publish `prism.verdict` 前调用
   `store.write_audit_blob(review_body)` → 得 `sha256`（blob 先写、事件后提交，
   R3-07 保证已提交事件引用的 blob 必存在）。
2. 事件 payload 行内保留小结构：`verdict` / `criteria_pack` / `review_summary` /
   `findings[]` / `review_ref`；`review_body` 不进行内。
3. 整个 payload 超 `BLOB_THRESHOLD`（8KB）时 store 自动转 `{"$ref": sha}`，
   读取方经 `load_payload()` 透明解引用 —— 与现行大 payload 机制一致。
4. `machine._on_prism_verdict` 将 `review_summary` + `findings` + `review_ref`
   写入 `s.last_failure`，重派 Shield/Devon 的 evidence（FR-11）由此携带完整
   意见索引；Agent 可按 ref 从 blobs/ 取正文。

### 2.4 兼容性

- 存量 `prism.verdict` 事件（无 findings/review_ref 字段）重放不报错；消费方
  一律 `p.get()` 容错，缺失时回退现行 fallback reason。
- `result_checkpoint.py` 的字段透传与 `machine.py` 的 last_failure 写入已实现
  并提交（f10dca3 链路）；剩余工作 = Prism.md manifest 约定补字段 + 校验 +
  review_ref 透传，DB 零改动。

## 3. 验收标准

- **AC-FR0240-01**：M-TEST/M-IMPL 产生 revise verdict 时，`prism.verdict` 事件
  payload 必含 `review_summary`（≤140 字符）与 `findings[]`（每条含 id/severity/
  defect_classification/criterion/artifact/ac_refs/summary 七字段）。
- **AC-FR0240-02**：`review_body` 必经 blobs/ 内容寻址持久化，事件行内携带
  `review_ref`；已提交事件引用的 blob 文件必存在。
- **AC-FR0240-03**：revise 后的重派命令 `evidence` 必含 review_summary、
  findings、review_ref 三者。
- **AC-FR0240-04**：M-DESIGN 评审行为不变：仍走文档锚定，其 `prism.verdict`
  不强制 findings 字段（出现时同样透传，不拒绝）。
- **AC-FR0240-05**：无 findings/review_ref 的存量事件在重放与新代码下行为
  与旧版一致（fallback reason 路径）。
- **AC-FR0240-06**：`review_summary` 或 `findings[].summary` 超 140 字符时，
  outcome 判为 manifest malformed，不产生带超限字段的 `prism.verdict` 事件。

## 4. 触发条件与升级规则

进入 M-IMPL 前复核代码评审的文档锚定补偿通道是否成立；若不成立且本 FR 未实现，
SC-D35 升级为阻塞项（用户标准：无自然通道可绕 / 事后大返工）。

## 5. 影响面清单

| 位置 | 变更 | 状态 |
|------|------|------|
| `.opencode/agents/Prism.md` + `tracks/agents/Prism.md` | manifest 字段约定 + 140 字符约束 | 待做 |
| `tracks/executor/result_checkpoint.py` | review_body → write_audit_blob → review_ref | 部分（透传已就位） |
| `tracks/kernel/machine.py` | last_failure 增补 review_ref | 部分（summary/findings 已就位） |
| `tests/unit/test_machine_m_test.py` | review_ref 透传断言 | 待做（AC-03/05） |
| 数据库 / blobs 机制 | 无改动（阈值溢出 + audit blob 均为现行机制） | 完成 |
