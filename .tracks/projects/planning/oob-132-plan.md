# OOB 修复计划：#132 Agent 会话生命周期（最小高价值切片）

状态：planned，2026-09-27。执行者：ZCode（Human Aaron 授权 OOB 通道：计划/实施/Prism 审核）。不重开 v0.10 baseline；#132 的完整 feature（压缩/重建策略的开放设计问题）仍留正式版本。

## 目标与切片依据

#132 两次事故计费（2026-09-02 v0.8 Devon 462 消息挂死；2026-09-27 v0.10 Archer 318k token 会话撞 TPM，85 分钟白烧）共同根因有二，均可小切片修复：

1. **派发无看门狗**：runtime 对零产出挂死完全无感知，靠人工 kill。
2. **病态会话无自动重建**：infra 连败后仍复用同一膨胀会话，注定再撞墙。

#132 自己的分析已给出倾向：任务内倾向压缩、病态/边界倾向重建；且 B92 已把 assignment 注入做成自包含上下文（重建的操作性损失小）。本 OOB 只做"重建"侧 + 看门狗前置，**压缩/摘要策略不做**（开放设计问题，留正式版本）。

## 范围

### W1 派发级看门狗（#132 声明的前置依赖）

- `tracks/effects/opencode.py` 的 act() 执行路径加无产出超时：X 分钟内无任何输出 part（以 opencode 流事件为准）→ 进程组 kill → 抛分类 infra 错误（`zero_output_timeout`），走既有 infra failure 退避/升级路径。
- 默认值保守（提案 15 分钟，避免长工具执行误杀）；`TRAC_DISPATCH_WATCHDOG_S` 可调（0 = 关闭，逃生开关惯例同 `TRAC_AGENT_SESSION_REUSE`）。

### W2 infra 连败自动会话重建

- 位置：executor 侧 infra_failure_streak 递增的钩子处（`machine_outcomes._handle_infra_failure` 相邻层，不进 kernel——kernel 中性原则，重建属 effects/executor 职责）。
- 语义：同一 (run, role) 的 infra streak ≥ N（提案 3，`TRAC_SESSION_REBUILD_STREAK` 可调）→ 将 `.tracks/runtime/sessions/<run>.json` 中该条目归档至 `invalidated-auto-<ts>/`（沿用 b91/b92/b94 手术惯例，自动化之）→ 下次派发自动新建会话。
- streak 经 human.retry/成功派发清零（既有语义）。

### 明确不做

压缩/摘要、首 token 延迟趋势启发式、任务边界相位决策、看门狗的 part 时间戳精细判据（先用进程输出活性近似）。

## 实施与验证

- 本地可全验证：unit（fake backend + 超时/连败模拟）+ integration；pytest 无需网络。
- 仓库规范遵守：模块行数/lint/复杂度门禁（pre-commit 全绿后再提交）。
- 提交：`Tracks-OOB: <reason>` trailer 声明（runtime 吸收惯例），commit message 引 #132。
- **Prism 审核**：实施完成后经运行时评审通道派发 Prism 复审（需网络），按其 findings 修复直至通过。

## 时序约束（Aaron 的网络窗口）

1. 现在：本计划落档；所有网络依赖操作（issue 评论等）提前完成。
2. 离线小时：本地起草补丁与测试（草稿存 scratch，**不动仓库工作树**——run 停靠期间保持树干净，避免漂移噪声）。
3. 网络恢复 + v0.10 停靠确认后：应用补丁 → 本地全量测试 → Prism 审核 → Tracks-OOB 提交 → 恢复驱动 v0.10（下一基线吸收 OOB）。

## 风险

- 看门狗误杀长工具执行：默认 15 分钟 + env 可调 + 0 关闭。
- 自动重建丢会话记忆：B92 自包含注入已把损失降到"重读 skill/文档"级（#132 自述）；仅 infra 连败触发，正常会话不受影响。
- OOB 与 run 停靠交互：停靠期树干净；OOB 提交带 trailer，下一基线冻结吸收（M7 惯例）。
