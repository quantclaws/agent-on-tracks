---
change_id: SC-D38
date: 2026-08-15
status: proposed (pending v0.6 M-SPEC incorporation)
decision_ref: D-37 (.tracks/wiki/decisions.md)
target_fr: TBD (v0.6 M-SPEC 立项时分配)
---

# SC-D38 over_reach 处置 8 步轮次（裁决—申诉—生产者回流） — 规范变更提案

> 用户裁定（2026-08-15）：7 步轮次 + Shield 自接受免申诉分支。本文件为规范级追踪
> 文本，待 v0.6 M-SPEC 阶段经修订日志（R-n）正式并入 spec.md；实现完成前
> flow.md/arch.md 不收录（文档只描述已存在的行为）。

## 1. 背景与问题

run 01KZTHE7RMZE6110PK9C54K1E2 实证（M-TEST WRITE，2026-08-14 21:26–22:30 UTC）：

- Shield 将测试辅助 hook 写在 `tests/` 根（白名单外），并自行修改
  `pyproject.toml` 加 lint 豁免以迁就放错位置的文件；
- Runtime 审计正确检出 over_reach，但处置是**整轮 force 回滚**（合规测试文件一并
  丢失）+ **计失败**（attempt 不重置），evidence 只有一行路径清单；
- `adjudicate()`（opencode.py:502-505）确实派了 Prism 裁定，且裁定正确
  （DENY：pyproject 属真越权；合规解法存在——hook 可移入 tests/integration/，
  22:17 会话中 Shield 被回滚后自己完成了这个移动并恢复 pyproject）；
- **但裁定结果不回流**：adjudicate 只返回 bool 给审计器，DENY 后直接回滚计失败。
  Shield 每轮重派时看不到"为什么错"和"正确解法是什么"，只能盲猜，同因三轮
  烧掉 116 分钟 agent 时间后 escalation。

问题不是"Agent 会越界"（这正常，机制就是为它准备的），而是**越界后的信息流
断了**：裁决者有正确答案，生产者收不到。

## 2. 现状与目标轮次对照

| 步 | 目标轮次 | 现状 | 缺口 |
|---|---|---|---|
| 1 | Shield 完成 work + manifest，退出 | 已有 | — |
| 2 | Runtime 检出 over_reach：**只回滚越权路径，保留合规产物**；失败原因（over_reach + 路径清单 + "无权限，如有必要可申诉"语义）进入重派 evidence | 整轮回滚；evidence 仅路径清单，无申诉语义 | 部分回滚；evidence 语义 |
| 2.5 | **Shield 读拒绝理由后自接受**：不申诉，直接按理由修正（如移入白名单目录、恢复配置），回到 Runtime 验收；attempt 重置（这是修正不是失败重试） | 无（Shield 看不到理由） | 理由回流后此分支自然成立 |
| 3 | Shield 不接受 → 提出申请（理由 + 请求的白名单变更），Runtime 转交评审者 Prism | 无申请通道；adjudicate 是失败后自动触发，非 Shield 主动 | 申诉协议（outcome 新字段或 manifest 申请条目） |
| 4 | Prism 否决 + **给出解决方案**（如"移入 tests/integration/ 即合规"）→ Shield 按方案执行；**attempt 重置**（新 loop） | DENY 一行，无方案字段；attempt 不重置 | Prism 裁定输出协议扩展 |
| 5 | Prism 同意（合法 scope extension）→ 意见返回**生产者 Archer**：白名单变更由 Archer 执行（Shield/Devon 不改配置，单写者纪律） | ALLOW 直接放行，Archer 不知情 | Archer 通知 + 变更通道 |
| 6 | Archer 同意 → 改 [layout] 声明；不同意 → 给出替代方案（变更设计或指导），同样回流 | 无此通道 | Archer 决策协议 |
| 7 | Archer 意见回 Shield → **新 loop，attempt 重置** | 无 | loop 重置语义 |
| 8 | 循环直至 EXIT（既有 ≤3 升级预算以 loop 为单位重算，防无限循环由 loop 计数上限兜底） | 连发即耗尽 | 预算语义调整 |

## 3. 设计要点

### 3.1 部分回滚（步 2）

`_shield_write_audit` / `_non_shield_write_audit` 现行为：doc_offending 或
over_paths 任一命中 → `rollback_agent_changes(force=True)` 回滚**全部**
agent_changed 路径。目标行为：

- 仅回滚 `over_paths ∪ doc_offending`；
- 合规路径（白名单内且非 doc 违规）保留在工作树；
- 重派 prompt 的 evidence 结构化携带：`{failure: over_reach, paths: [...],
  ruling: <若已裁定>, appeal: "若确有必要，在 outcome 中声明申请及理由"}`。

### 3.2 自接受分支（步 2.5，用户 2026-08-15 增补）

拒绝理由回流后，Shield 的下一轮 dispatch 天然可读 evidence。规范要求：

- Shield 读理由后若自接受（认定拒绝正确），直接修正并交付，**无需**走申诉；
- Runtime 对"携带 over_reach 历史且本轮零越权"的 dispatch **重置 attempt**
  （语义：这是收到裁定后的新工作，不是同因失败重试）；
- 该分支不产生额外事件（就是一次普通成功 dispatch），可从
  `over_reach → 下一轮 success` 的事件序列审计观察。

### 3.3 申诉协议（步 3）

Shield outcome manifest 增加可选 `scope_appeal` 字段：
`{paths: [...], reason: str, proposed_change: str}`。Runtime 识别到该字段时
不按普通失败处置，而是进入 ADJUDICATE 分派（复用 adjudicate.py，prompt 扩展
为可携带 Shield 的申请理由）。

### 3.4 Prism 裁定输出协议（步 4/5）

`adjudicate()` 返回值从 `bool` 扩展为 `{verdict: ALLOW|DENY, rationale: str,
remedy: str | None, producer_action: str | None}`：

- DENY 必带 `remedy`（否决时的解决方案，如"移入 tests/integration/"）；
- ALLOW 必带 `producer_action`（给 Archer 的白名单变更建议）；
- rationale 全程进入事件流（audit blob），可审计。

### 3.5 Archer 回流（步 5/6/7）

ALLOW → Runtime 向 Archer 派白名单变更任务（M-DESIGN 语义的配置修订，
[layout] 是 Archer 的物理交付）；Archer 不同意则给替代方案，意见经 Runtime
回 Shield，新 loop 重置 attempt。Archer 决策本身走既有 doc 评审协议。

### 3.6 预算语义（步 8）

- attempt ≤3 预算以 **loop** 为单位：一次裁定（自接受/申诉/Archer 回流）闭环
  后重置；
- 全程设 loop 上限（建议 3），超限 escalation——防止 Shield 与评审者之间
  ping-pong 无限循环。

## 4. 验收锚点（v0.6 spec 立项时展开为 AC）

- AC-1 部分回滚：构造 Shield 产出 3 文件（2 合规 + 1 越权），断言越权路径回滚、
  合规路径保留、evidence 含申诉语义。
- AC-2 自接受：over_reach 后下一轮 Shield 修正交付，断言 attempt 重置且事件
  序列无 ADJUDICATE。
- AC-3 申诉通道：Shield outcome 带 scope_appeal，断言 Runtime 转 Prism 而非
  直接计失败。
- AC-4 DENY 带方案：断言 Prism DENY outcome 含 remedy 且进入重派 evidence。
- AC-5 ALLOW 走 Archer：断言 Archer 收到 producer_action，[layout] 由 Archer
  修订，Shield 不触碰配置文件。
- AC-6 loop 预算：断言裁定闭环后 attempt 重置、loop 计数递增、超限 escalation。

## 5. Out of Scope

- 不改变 over_reach 检测本身（baseline/agent_changed_paths 语义不变）；
- 不引入 Human 强制介入点（全程程序化，escalation 仍是最终兜底）；
- Devon（M-IMPL）的同构处置在 v0.6 M-IMPL 设计时对齐，不在本 SC 展开。
