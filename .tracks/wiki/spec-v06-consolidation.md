# SC: v0.6 集中整治计划（M-IMPL 加固 + token 效率）

2026-08-16 | 状态：草案（待用户评审） | 证据：run 01KZTHE7RMZE6110PK9C54K1E2 T-017 全天循环（04:23–14:22 UTC）

## 0. 结论（TL;DR）

v0.6 **不直接开 M-IMPL 之后的下一阶段**，先做一轮集中整治（用户裁定 2026-08-16）：M-IMPL 暴露的失败处理与状态迁移问题已系统性拖慢运行（T-017 单任务 10 小时、24 次 outcome、12 次 verdict.failed、6 次 human.retry、6 次 DIAGNOSE、2 次流挂死共 40 分钟）。v0.6 = 三个工作流（token 效率 / 流状态稳健性 / 评审通道）+ 暂缓的下一阶段瘦版。v0.5 收尾只推进 T-017/T-018 收敛，不再动内核。

## 1. 背景与证据（run 01KZTHE7 T-017 全天）

| 症状 | 次数 | 根因归类 | 当日处置 |
|---|---|---|---|
| Devon evidence 缺必填字段 → impl_defect | 4 | 输出合同未前移（WS-A2） | 靠 DIAGNOSE 后置补丁救场 |
| 诊断不传递 / blob hash 不告知 | 多轮 | 信息传递断层（已修） | e01390f/a302a2d/606dd4c |
| 流挂死被宽容接受 → 截断 outcome | 2 | infra/agent 失败不分层（已修） | 18f43bf + 10min 看门狗，现场验证 |
| infra 失败烧 attempt → 3 次耗尽 escalation | 多轮 | 同上（已修） | e01390f |
| 确定性 gate 失败仍走完整 DIAGNOSE | 3 | 无短路（WS-A1） | 每次白付 ~5 分钟 + token |
| Prism 评论只在文档/无传递通道 | 1 | 评审通道缺口（WS-C） | D-35 立项未实施 |

当日已落地修复（v0.5 收尾的一部分，不是 v0.6 工作量）：诊断持久化 `State.diagnose_report`、infra 失败分层与退避、objective 携带 blob refs、GREEN 重派携带诊断、不活跃挂死 timeout 分类。

## 2. 目标 / 非目标

**目标**：同样的 M-IMPL 复杂度下，agent 轮次 −50%、DIAGNOSE 调用 −70%、零人工 retry；流程能自洽处理越权/外带变更/评审回流三类状态分叉。
**非目标**：不改 RGR/gate 语义；不开新阶段全功能（见 WS-D）；不做 web UI。

## 3. 工作流

### WS-A token 效率（P0，先做——全部有当日实证）

- **A1 确定性 gate 失败短路 DIAGNOSE**（F2）：runtime evidence schema 校验失败（reason 已列全缺失字段）直接路由回 fixer 并携带 reason，跳过 Prism。实证：当日 3 次 DIAGNOSE 的结论 100% 复述 runtime 已知事实。
- **A2 输出合同前移**（F3）：assignment/skill 显式列出 evidence 必填字段清单与正例，替代诊断后置补救。实证：4 次 impl_defect 同因。
- **A3 infra 重派会话续传 + 前任告知**（D-39）：轻版确定性提示注入先行，深版 `--session` 续传按 (role, substate, task) 跟踪；仅限 infra 重试，agent-attempt 重试保持全新+诊断。实证：2 次挂死重派后 Devon 需重新理解工作区。
- **A4 infra 失败证据富化**（D-34）：stderr/上游错误摘要入 `last_failure.evidence`（sglang 400、上游截断等真因目前只在 opencode 日志）。
- **A5 评审意见传递通道**（D-35 + F1，用户指示）：代码评论走 blobs（review_summary ≤140 字符 + findings[] + review_body/ref），文档评论维持 inline-discussion；revise 评论随重派传给 Devon/Shield。

### WS-B 流状态稳健性（P1——应对更复杂的状态变化）

- **B1 over_reach 八步轮次**（D-37）：裁定结果回流 Shield、回滚保留合规产物、同因连发熔断。实证：pylint 越权案烧 116 分钟 agent 时间。
- **B2 OOB 提交机制**（D-36）：dispatch 窗口外的人类/外围变更经 trailer 声明后接受，消除否定式归因误伤。
- **B3 loop 健康自检**（F5）：loop 静默死亡的外部守护（launchd/cron 探活 + 自动重启 + 告警）。

### WS-C 语言中性补强（P2）

- **C1 非 pytest 失败行提取**（F4）：FAILED/ERROR 前缀之外增加 FAIL/✗ 等启发式；兜底维持整日志 blob（已建）。

### WS-D 下一阶段瘦版（暂缓，需用户裁定形态）

M-IMPL 后无注册阶段。候选：M-VERIFY（里程碑级 live 硬门禁，承接 D-18 的「milestone 前必跑」）或 M-RELEASE（凭据/发布证据流，承接 FR-0232 release-evidence）。建议 v0.6 末尾以最小 story/spec 起步（D-26：无 story/spec 不做功能）。

## 4. 排期与依赖

| 顺序 | 项 | 依赖 | 预估 |
|---|---|---|---|
| 1 | A1 + A2（同一次 spec 变更） | 无 | 0.5 天 |
| 2 | A3 轻版 → 深版 | 18f43bf 已合 | 1 天 |
| 3 | A5（含 D-35 规范并入 M-SPEC） | 无 | 1 天 |
| 4 | B1（spec-oob-adjudication-loop.md 已有方案） | 无 | 1 天 |
| 5 | A4 + B3 + C1（小项打包） | 无 | 0.5 天 |
| 6 | B2（深版归因可再延） | B1 验收 | 1 天 |
| 7 | WS-D 瘦版 story/spec | 用户裁定 | 另计 |

全部在 dispatch 窗口外静默期实施（D-34/35/36 先例）。

## 5. 验收判据

- 回放 T-017 场景（截断 outcome / 缺字段 / 挂死）：全程零 DIAGNOSE、零人工 retry、infra 重派 ≤2 次收敛
- over_reach 演练：合规产物保留、Shield 收到裁定理由、同因 attempt 熔断生效
- 评审回流：Prism revise 的 findings[] 出现在 Devon/Shield 重派 objective 中
- token 账目：同等任务 agent 输入 token 较 01KZTHE7 基线 −40%
