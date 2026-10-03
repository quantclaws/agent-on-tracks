# 推进策略会诊（Aaron 直接指令，2026-10-02 中午）

**Date**: 2026-10-02（JST）
**Author**: Archer（会诊；非阶段产物，证据全部可定位）
**Run**: 01M3E7SAANXKW1V73W8B8Q3G86 @ M-IMPL/PLANNING（commit_taskgraph 在途）
**结论先行**：本轮电池让它跑完（ETA ~12:14 JST，只剩最后一个探测）；剩余三任务是**严格串行链**，并行机制在当前架构不存在且依赖关系也使问题无意义；最短合法路径 ≈ 今晚 20:00–24:00 出 release，摆幅由 3 个 Devon RGR 决定；Q4 值得立项，且调查发现比"重入成本"更严重的事实——**锚点探测门禁自 9/30 起输出为零（全部 timeout-skip 空转），本轮亦然**。

---

## 0. 事实底座（取证）

| # | 事实 | 证据 |
|---|---|---|
| F1 | 时间线：10:27:11 Aaron retry（seq 3848）→ 10:27:51 test.committed（8ac2470）→ 10:27:54 baseline.frozen → 10:32:36 重规划 envelope 交付 → 10:32:37 commit_taskgraph 签发（seq 3863）→ 至今无新事件 | events DB seq 3848–3863 |
| F2 | "全量锚点电池"= B33（#34）规划期锚点探测 `probe_task_anchors`：对**每个**含 acceptance_refs 的任务执行 `shlex.split(integration.run) + refs`——`run` 模板的 `{result}` **不展开**，refs 追加在 `tests/integration/` 目录参数之后 | tracks/executor/anchor_probe.py:160-164,199 |
| F3 | pytest 并集语义：目录 + 目录内节点 = **全量 integration 套件**。即每个任务的探测实际跑一遍整套件；OOB-F-2 只修了 unit 探测（run_selected），integration 侧未修 | anchor_probe.py:224-227 注释自证；活体 PID 81144/10151/50945 命令行逐字吻合（含字面 `--junitxml={result}`） |
| F4 | 探测超时硬编码 600s（无 env 旋钮），超时 → `skipped`（infra，**fail-open**，B35 语义）；只有 `green 且非 verification-only` 才硬拒（规则 1） | anchor_probe.py:117,123-125,245-258 |
| F5 | 新图 10 个任务带 acceptance_refs（T-005/T-011 无）→ 10 次探测 × 600s = **100 min 即"历史时长 1.5-2h"的真身**。9/30 23:59 的 taskgraph.committed payload：全部 12 个探测 `skipped: probe infra failure`——**该门禁自 9/30 起零输出空转**（病灶时代套件挂死 → 超时；今天套件健康但 >600s 仍超时） | blob 075ac64b…（seq 3247）；今日探测逐一在 600s 整点死亡（11:53:41 / 12:03:41） |
| F6 | 规则 1 在当前实现下**结构性不可触发**：并集语义下探测 green 要求整套件绿，而 M-IMPL 中途套件恒有合法红残留 → 探测永不 green → 硬门禁形同虚设（即使不超时） | F3+F4 推导 |
| F7 | P0+P1-4 已于 00:56 落地（a7f4133）：hotfix (d) 改 fake、电池超时 900+60s/node、测试宿主 TRAC_INFRA_RETRY_LIMIT=2、-n 8 恢复、CI 并行化 | git show a7f4133 |
| F8 | 依赖链：T-004 depends_on [T-003]（已完成）→ 立即可派；T-005 depends_on [T-002,T-003,**T-004**]；T-011 depends_on 全部 11 个前置。**三任务严格串行** | tasks.json（10:32 新图） |
| F9 | `parallel`/`batch` 字段在 kernel/executor **零消费**（grep 无命中）；writelock 是单租约布尔（`writelock_held`），M-IMPL 单任务派发串行——当前架构不支持多任务并发 | tracks/kernel/m_impl_{decide,routing,state}.py；9/30 事件 `writelock.released/granted` 序列 |
| F10 | T-011 scope_boundary = 全部 11 个任务 scope 的并集（收口任务），与任何任务并发都越权 | tasks.json |
| F11 | 9/30 重放尾链实测时长：taskgraph.committed → island_1（1s）→ PRISM_PLAN **95s** → 首任务派发（+2min） | events seq 3247–3261 |
| F12 | T-004 在树 WIP：`api_command.py`/`service.py` dirty（回滚 GREEN 尝试的 replay 种子，T-004 描述已声明"按修订后合同继续交付"）；两锚为 red-window residual map 唯二合法红 | git status；tasks.json T-004 描述；4e46575 |

## 1. Q1：本轮电池——跑完，且它是本轮**最后一个**可省环节之外全无可省

**ETA**：探测序列 T-001→…→T-010b 串行，每个 600s。实测 T-009 探测死于 11:53:41（600s 整）、T-010 死于 12:03:41、**T-010b（最后一个）12:03:41 起跑 → 12:13:41 超时** → 静态门已过（失败会早有事件）→ taskgraph.committed ≈ **12:14** → PRISM_PLAN ≈ 12:15–12:20 → T-004 派发 ≈ **12:18–12:22**（F11 节奏）。

**能否合法缩短本轮**：三个选项，结论是"让它跑完"：

1. **P1-5（同身份选择集复用）现在落地？不能，且不对症。** (a) 本轮电池不是选择集电池——它是锚点探测（F2），P1-5 的 selection_id 复用机制不覆盖探测路径；(b) 探测结果全是 timeout-skip，**没有可复用的 per-node outcome**；(c) 它是 `tracks/executor` 产品码改动 = Devon 写域 + 自己的 RGR+门禁周期（小时级），而本轮剩余仅 ~20 min；(d) 在门禁在途时改执行器代码会污染证据身份（B49 #64 的门禁-合同一致性纪律）。**对后续 gate 重放的收益也有限**：P0 之后 RED_CHECK/GREEN_GATE 都是 run_selected 窄选择集（T-004 的 56 节点级），分钟级，复用省的是边角。
2. **kill 跑路循环/重试**：`trac run --resume` 会重发 commit_taskgraph → 100 min 重头来。负收益。
3. **kill 当前探测 pytest（微观选项，不推荐）**：探测的所有非 green 结局（timeout-skip / SIGTERM 后 rc=-15 → 按保守分类落 entry_or_collect_error advisory）**都不阻塞 commit**（规则 1 只在 green 触发，F4/F6），所以掐死最后这个探测在结果上与等它超时**等价**，能省 ~10 min。但这是手工戳在途门禁进程，省 10 分钟开一个坏先例——**不建议**；列出来仅为完整披露机器事实。

**顺带的门禁卫生发现**（不阻塞本轮，进 Q4 议题）：探测命令携带未展开的 `--junitxml={result}`，若某次探测真跑完，会在仓库根落一个字面 `{result}` 文件。

## 2. Q2：并行——问题不成立（双重否定）

1. **依赖上不可能**：T-004→T-005→T-011 是严格链（F8）。T-005 的 SPA 编辑器 409 两选项流消费 T-004 交付的 §1o.2 顶层 `current_revision` 形态（接口先后，不是巧合）；T-011 是全图收口。
2. **架构上也不支持**：`parallel: true` 与 `batch` 是规划期注释字段，运行时不消费（F9）；writelock 单租约 + 单 dispatch 通道 = 单任务串行。即使 T-004/T-005 scope 不相交（`api_command.py+service.py` vs `static/app`），也没有机制并发派发；T-011 的并集 scope（F10）永远排斥并发。

**建设性转译**：如果 Aaron 的意图是"剩余工作能否摊薄墙钟"，合法杠杆不在任务间并行，而在 (a) 不再有下一次重入（每次重入 = M-DESIGN 修订 + M-TEST 重冻结链 + 100 min 空转电池 ≈ 3–4h 机器成本，远超一个任务的实现量——Aaron 的痛点成立且可量化）；(b) Q4 议题把重入的固定成本砍掉。

## 3. Q3：最短合法时间线（自 12:14 JST committed 起算）

| 环节 | 估算 | 依据 | 可压缩性 |
|---|---|---|---|
| PRISM_PLAN | ~2–5 min | F11（9/30 实测 95s） | 门禁，不压 |
| T-004 RGR + 门禁 | **1.5–3h** | deepseek 实测 1.5–3.5h 取下沿：WIP 在树（F12）+ spec_gap 合同已冻结 + 锚点已合法红，无探索成本 | 不压；它是本轮风险最高任务（spec_gap 回滚发源地），快的前提是**合同不再动** |
| T-005 RGR + 门禁 | 1.5–3h | 纯静态 JS 面，无集成锚点，deferred 文件级守卫到达即绿；ui-e2e 终态兜底 | 不压 |
| T-011 RGR + 门禁 | 1.5–3h | 收口任务（AC-FR0331-01/02 登记处），scope 大但实现面小（集成闭合） | 不压 |
| ISLAND_GATE_2 / full | ~15 min | P0+P1-4 后全量假通道 ≈11–12 min（compression plan §4 汇总行） | 已压过（昨晚） |
| M-VERIFY 本地门 + build/smoke + security | ~30–45 min | project.toml host-contract 五门 + pip wheel + pip-audit/bandit | 不压 |
| M-RELEASE（CI 7 required checks） | ~35–45 min | P0-3 后 coverage 关键路径 ~25–30 min | 已压过（昨晚） |
| **合计** | **≈ 5.7–10.3h** | → **release 落点 ≈ 今晚 20:00–24:00 JST**（中位 ~21:30） | — |

**唯一还剩的合法压缩项**：没有了。摆幅（±4.5h）100% 来自三个 Devon RGR 的实际时长；所有门禁/电池/CI 要么已是分钟级，要么昨晚已压。剩下的最大风险不是慢，是**又一次重入**——T-004 的 §1o.2 合同本轮必须一次说死（已死：spec_gap 修订冻结），Devon 侧任何"顺手改合同"都会把今晚变成明天。

## 4. Q4：值得立 v0.11 议题——且性质比"重入成本"更重

调查暴露的真问题：**锚点探测门禁（B33 规则 1）自 9/30 起零输出空转**（F5/F6）——每次重入烧 100 min 买到的不是"慢的保护"，是"没有保护"。重入成本与门禁有效性是同一个洞。建议立项（草案要点）：

**标题（草案）**：`v0.11: 规划期锚点探测窄化 + 重入增量模式（B33 门禁恢复真实输出，重入电池 100min→<5min）`

**要点**：
1. **P-a 探测窄化（OOB-F-2 的 integration 对偶修复）**：`_probe_one_task` 的 acceptance 探测改用 `integration.run_selected`（{nodes}/{result} 展开），不再 `run` 目录模板 + refs 追加。效果：单任务探测 = 任务自己的锚点节点（秒级）；全图探测电池 100 min → <2 min。顺带修字面 `{result}` 落盘污染。
2. **P-b retained 豁免（必须与 P-a 同船，强制配对）**：窄化后探测真正测量任务自身锚点 → retained 完成任务锚点恒绿 → 规则 1 会把每次含保留任务的重规划**硬拒**（现在因 F6 的空转才没爆）。把 `_retained_completed_ids`（B83 payload 等价机制，现成）移到探测之前，retained 任务跳过探测或记 informational。不配对的窄化 = 给重入通道装地雷。
3. **P-c 重入增量模式（Aaron 的原始诉求）**：重规划 commit_taskgraph 时，只对 (i) payload 相对上次 taskgraph.committed 有变化的任务、(ii) 新增任务做探测；未变任务沿用上轮 probe_summary（证据身份 = 图 digest + tree stamp，tree 变即失效，fail-closed）。
4. **P-d 探测超时可调**：600s 硬编码 → `TRAC_ANCHOR_PROBE_TIMEOUT`（窄化后默认可降到 ~120s）。
5. **P-e P1-5 选择集复用（原 compression plan P1-5 归并入本议题）**：battery 层同 selection_id 复用 per-node outcomes，服务于 gate 重放；证据完整性约束不变（同身份同基线，tree_stamp 变即重跑）。
6. **验收锚（草案）**：构造含 retained 任务的重规划场景 → commit_taskgraph 总耗时 <5 min 且规则 1 对一个"锚已绿却排标准 RGR 的**新**任务"仍然硬拒（门禁从空转恢复为真保护，双向都要证）。

**依据附件**：本文 §0 F2–F6；9/30 全 skip payload（blob 075ac64b…）；今日三次 600s 整点超时活体（PID 81144/10151/50945）。

## 5. 给 Aaron 的直接建议（按序）

1. **现在**：什么都不动，等 ~12:14 committed → ~12:20 T-004 自动派发。（本会诊自身的 CPU 占用结束后，后续电池还有少量余量红利。）
2. **T-004 窗口**：任何人不得再动 §1o.2 合同文本；Devon 若报合同疑问，走既有 spec_gap 通道但认知到价格 = +3–4h。
3. **今天下班前**：把 Q4 议题立了（要点在 §4），v0.11 第一批做——它直接决定 v0.10.1 及以后每次重入的固定成本，且当前门禁空转状态本身就是质量债。
4. **不做**：不并行（无处可并）、不塞 P1-5 进本轮、不掐在途进程、不跳过任何门禁。
