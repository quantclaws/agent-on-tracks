# OOB 设计：Agent 会话生命周期（三层机制）

> 状态：已实施（带外，2026-09-19）。裁定记录见 decisions.md D-42。
> 本目录（projects/v0.9/oob/）是临时修复文档的手工存放处——本版手工维护，不作为 feature 实现；未来版本再决定是否机制化。

## 问题

#44（D-39 用户简化版）把会话键坍缩为纯 agent 名、且任何重派一律续传。长 run（多轮 planning × 多次重试）下会话无界增长：Archer 常驻会话达 **1301 parts、累计 24M input tokens、单回合 749K context、从未压缩**，每回合重发全上下文导致配额指数消耗（429 "token plan entitlement exhausted"，run 01M2QTJB 2026-09-18/19），并表现为 6 次 `no_envelope_block`（回合尾部断流）与 exit-1 级联。D-39 深版设计的两个承重约束——(role, substate, task) 键与 attempt-fresh 边界——均未落地。

## 设计原则（用户裁定 2026-09-19）

控制会话大小，同时保留有用信息：

1. 上下文完全不适用当前/下一阶段任务 → 新建 session（如版本切换）
2. 上下文对接下来的任务关键 → 必须复用（如 Archer 结果正确仅格式错）
3. 上下文临近阈值 → 必须压缩（阈值 = 模型窗口的百分比，非上限本身）

## 机制

### 1. 键结构

```
key = (run_id, role, work_unit, task_id)
```

- `run_id`：版本隔离（注册表按 run 分文件，沿用既有机制）
- `work_unit`：由 (role, substate) 派生的工作单元（effects 层映射，不改 act() 协议）：
  - devon 的 RED/GREEN/REFACTOR/DIAGNOSE → 同任务同键（RGR 上下文关键，任务级连续）
  - shield 的 WRITE/NO_DIFF_EXPLAIN → M-TEST 写作单元同键；SHIELD_FIX 按任务
  - archer 的 DRAFT/RESPOND → 设计单元；PLANNING → 实施规划单元
  - 其余（评审等短会话）→ substate 自身
- `task_id`：assignment 携带 task 时取其 id，否则 `-`

### 2. 新建 session 的条件（原则 1 + 失效处理）

| 场景 | 机制 |
|---|---|
| 版本切换 | 新 run_id → 新注册表 |
| 工作单元切换 | 键变化 → 新建 |
| 会话失效 | miss / overflow 文本 / **non_zero_exit 硬崩**（裁定：保持清会话——runtime 负责把 agent 需要的信息（任务、verdict 等）传递进去，agent 只需重建自己的探索/思考/结论）|
| 压缩失败 | 回退新建 |

### 3. 复用（原则 2）

**同键一律续传**——infra 重派、format-error 重发、revise 重派统一处理。FR-11 证据注入负责纠偏，会话不需要失忆。**此条取代 D-39 的 attempt-fresh 边界**（有压缩兜底后，全新开始的理由不再成立）。

### 4. 压缩闭环（原则 3）

```
派发前：注册表 ctx_tokens ≥ 85% × 模型窗口？
  ├─ 是（且上次压缩有效）→ opencode run --session <id> --command compact → 派发
  │     压缩失败或压缩后仍超阈值 → 弃会话新建
  └─ 否 → 直接派发
派发后：解析最后一个 step_finish.tokens.input → 更新注册表 ctx_tokens
```

- 探测数据源：派发 stdout 的 `step_finish` 事件携带 `tokens.input`（该回合上下文尺寸，实测可得）
- 模型窗口：`opencode models --verbose` 输出 models.dev 目录含 `limit.context`（如 napi/hy4=1048576、napi-r/muse-spark=1048576、zhipu/glm-5.3=1024000），进程内缓存解析；解析失败 → 跳过压缩（fail-open，overflow 健康检查仍兜底）
- 阈值：**85%**（用户裁定；留 15% 给压缩回合自身与单次工作单元增长）
- 自愈：单回合中途超限 → 派发失败 → infra 重派 → 派发前检查看到膨胀尺寸 → 压缩/清会话 → 重派成功。一次失败换一次收缩，无死循环。

### 5. 注册表格式

`.tracks/runtime/sessions/<run_id>.json` 从 `{agent_name: session_id}` 升级为：

```json
{"devon:M-IMPL-TASK:T-001": {"session_id": "ses_...", "ctx_tokens": 87321, "compacted": false}}
```

迁移：旧格式（纯字符串值）条目直接丢弃——现存旧会话均小或已死，冷启动代价分钟级。

## 验证状态

- `step_finish.tokens.input` 探测：已验证（历史 blob 实测）
- `opencode models --verbose` 窗口目录：已验证（含 limit.context）
- `--command compact` 压缩：配额恢复后自然验证；失败回退路径（清会话）已内建，正确性不依赖压缩成功

## 回退

- 总开关：`TRAC_AGENT_SESSION_REUSE=0`（既有逃生口，逐次全新派发）
- 单会话：键内任一失效信号触发弃用，下次派发全新
