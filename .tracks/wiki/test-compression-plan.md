# 测试电池压缩计划（v0.10）—— 调查报告与可执行方案

**Date**: 2026-10-02（JST）
**Author**: Archer（Aaron 直接指令调查任务）
**目标**: 电池 ≤15min（硬上限 20min），全量假通道语义不变
**状态**: 调查完成；P0 项建议立即执行（其中 CI 自 9/29 起已红，比"慢"更紧急）

---

## 0. 结论摘要（TL;DR）

1. **假通道本身没坏**：今日实测全量假通道（unit+integration+e2e，-n 4）≈ **19.6 min**；v0.9.0（9/25 tag）同时点 ≈ 13 min——Aaron 的记忆准确。
2. **2 小时电池的元凶不是"全量变慢"，而是 9/28 新入库的一个真派发旅程测试**：`test_hotfix_precheck_classification.py::test_fetch_failures_classified_with_retry_open` 子项 (d) 让 `trac hotfix` 走真实 agent 派发路径。该路径在任何宿主（有无 opencode 二进制）都会坠入 **infra-failure 指数退避梯（30s→900s 封顶，kernel 文档自证 "106 minutes of self-waiting"）**，叠加电池执行器 `subprocess.run` **无超时**。凡选择集含此节点的 `run_tests`（M-TEST）或 `commit_taskgraph`（M-IMPL 锚点）电池 = 1–2 小时（events DB：run_tests p50=2s / **max=7209s**；commit_taskgraph p50=1s / **max=6123s**；本调查期间仍有一个活体电池挂死 ~2h @ 0% CPU）。
3. **CI 自 9/29 起三连红**（9/29、9/30、10/1 全部 failure）：该测试 9/28 01:37 入库（9a36bd2）后，无 opencode 二进制的 CI 宿主同样无法在合理时间内让它通过。最后一次绿 test job = 9/28 15:04（56 min，串行）。
4. **CI 时长 56 min 的原因**：`ci.yml` 的 test/coverage job **串行跑**（无 `-n`）；且 **coverage job（88 min）才是 CI 关键路径**（同一套件 + 覆盖插桩，串行）。
5. **压缩路径**：P0（隔离真派发测试 + 电池超时 + CI 并行）→ 电池尾部 1–2h 消失、CI 恢复绿、关键路径 88→~30 min；P1（本地 -n 4→8 + 分组均衡）→ 本地全量假通道 ≈ **11–12 min**；P2（夹具/进程开销）→ 进一步 ~9–11 min。
6. **opencode 2.x：v0.10 发布前不升**（建议详见 §5）：电池病灶是结构性的，2.x 不治；1.x 线仍活跃维护（v1.18.34，9/30 发布），可先小步升 1.18.30→1.18.34；2.x（现 2.0.6，独立安装通道可与 1.x 并存）放到 v0.10 后用 A/B 基准决策。

---

## 1. Q1 实测分层基线（2026-10-01/02 夜，Apple M4 10 核）

环境注：测量时后台有常驻 opencode 进程（其中一个累计 CPU 1086 min）+ 一个挂死电池（0% CPU，不构成 CPU 竞争）；本地 .venv 为 CPython 3.14（CI 为 3.11）。测量噪声估计 ±10%。

| 层 | 命令（-n 4 --dist loadscope -q） | 实测墙钟 | 收集数（9/25 junit 口径） | 备注 |
|---|---|---|---|---|
| unit | `pytest tests/unit` | **47.9 s** | 3830（v0.9.0 同夜 42.7 s） | 19 个本地失败（环境差异：github_live TypeError、CI 无此失败；另见 §6 清理项） |
| integration（剔除 test_hotfix_precheck_classification.py） | `pytest tests/integration --deselect ...` | **15:10** | 631（v0.9.0 同夜；现 172 文件 ≈ +14 节点） | 9 个本地失败（docs_center/kernel_language_neutrality/guard_parity/taskgraph_satisfiability——宿主环境噪音，CI 口径另核） |
| e2e（假通道，非 ui） | `pytest tests/e2e` | **3:40** | 62 | 全绿；单测试 six_journeys_dual_host 94.8 s 独占尾部 |
| **假通道合计** | | **≈ 19.6 min** | | |
| test_hotfix_precheck_classification.py 单独 | 有界 480 s | **超时未完成**（3 测试仅过 1） | 3 | 真派发路径；见 §3 |
| guard lint（电池第 1 站） | ruff + flake8 + pylint×2 遍 | **≈ 12–25 s** | — | 非瓶颈；ruff 当前树有真实发现（I001/W292） |

### top 慢测试分类（三层合并，按型归类）

**A. 真后端 LLM 等待/退避型（默认电池内仅 1 个，即元凶）**
- `test_hotfix_precheck_classification.py::test_fetch_failures_classified_with_retry_open` —— >480 s（有界截断；活体样本挂死 ≥2 h @ 0.64 s CPU）。子项 (a)–(c) 各 ~0.2 s（precheck 快速分类失败），子项 (d)「通道修复后重试开放」进入真实派发 → 退避梯/真 LLM。

**B. 夹具宿主启动/子进程编排型（绝对主导）**
- integration 尾部 23–29 s/个：`test_publish_idempotency::*`（28.8/25.7/23.9/23.5 s）、`test_verify_candidate::test_drift_marks_stale_no_refreeze`（28.5 s）、`test_milestone_close_real::*`（28.5/26.2/25.5 s）、`test_inplace_repair`（27.9 s）、`test_journey_versioning`（27.0/25.4 s）、`test_milestone_lifecycle`、`test_publish_reconcile`、`test_issue_mapping`、`test_release_preview`、`test_failclosed_scenarios`、`test_hotfix_coexist` 等——每个 = host_repo 构建（git init+venv 符号链接，~0.1 s）+ **十数次 `python -m tracks.cli.main` 子进程启动**（每次 ~0.5–0.9 s：解释器+import 0.2 s + runtime/gate 初始化）。
- e2e：`test_release_journeys_matrix::test_six_journeys_dual_host`（**94.8 s**，单测试独占一个 worker）、`test_reference_host_journey`（24.6 s）、`test_dualhost_acceptance`（20.6 s）。
- adapter 契约测试（`test_reference_adapter_equivalent_to_v06` ~15 s）：内嵌**全量 unit collect**（306 文件 import），无 LLM——**修正：观察到的"启动真实 opencode"来自 hotfix 旅程测试，不是 adapter 测试**。

**C. 纯计算型**：unit top 仅 2.0–2.6 s（fullf_evidence_oob、waiver_semantics、live_contract、reference_host_red 等）——忽略不计。

---

## 2. Q2 与 v0.9「13 分钟」的差距归因

**v0.9.0 基线取证**（tag=62c1e7e，9/25 03:15；同夜 04:42–04:50 的 capture_baseline junit 即 v0.9 全量）：

| 指标 | v0.9.0（9/25） | 现在（10/1） | Δ |
|---|---|---|---|
| integration 文件数 | 162 | 172 | **+6.2%** |
| integration 收集节点 | 631 | ≈645（估） | +2% 上下 |
| integration 墙钟（-n 4） | 479.4 s | 910 s（剔除 hotfix 文件） | **+90%** |
| junit 单测时间和 | 1830 s | ≈3260 s（墙钟×3.58 有效并发） | **+78%** |
| 单测中位数 | 0.40 s | ~同量级 | ≈0 |
| 尾部 top | 18–20 s | 23–29 s | +40% |
| unit 墙钟 | 42.7 s | 47.9 s | +12% |
| e2e | ~3–4 min（24 文件） | 3:40（27 文件） | ≈0 |
| **全量假通道** | **≈13 min** ✓ | **≈19.6 min** | **+6.6 min** |

**归因**（对 integration 墙钟增量 +431 s）：
- **规模增长 ≈ 29 s（~7%）**：479 × (172/162) 外推。
- **单测试变慢 ≈ 400 s（~93%）**：中位数不变、尾部与深旅程测试普涨——v0.10 的旅程测试每测试驱动**更多 trac 子进程步骤**（新增 docs_center/lex_review_park/version_gate/ui 基建等旅程段）。tracks 包 import 成本本身没涨（v0.9 与现在都 ~0.16–0.19 s、模块数同为 209）。
- 测量口径注：今日含后台负载噪声（±10–15%），保守表述为「规模 ~一成以内，变慢 ~九成」。

**另两项与记忆的校正**：
- 9fc12ab（9/17，即 v0.8.0 tip）是 **-n 8 → -n 4 的降并行**（operator directive）——v0.9 的 13 min 时代已经跑 -n 4，降并行不是回归原因（反而是恢复 -n 8 的依据，见 P1-4）。
- CI test job 44 min（9/17）→ 56–57 min（9/24–28）与套件增长同步；**CI 串行（无 -n）**才是它比本地慢 3 倍的原因。

---

## 3. 根因链（2h/1h42m 电池 + CI 红）

### 3.1 病灶测试的行为解剖

`test_fetch_failures_classified_with_retry_open`（9/28 01:37 入库，9a36bd2 Shield WRITE）：

1. 测试级 `monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")` **覆盖**集成层 conftest 的 autouse fake 注入（时序：autouse 先、测试级后，后者胜出）——通道语义从假通道泄漏为真后端。
2. 子项 (a)–(c)：凭证缺失/HTTP 401/403/网络不可达分类——stand-in 本地 HTTP，**各 0.2 s，快且安全**（steps 日志实证：4 次 `trac hotfix` rc=1，间隔 ~180 ms）。
3. 子项 (d)：通道修复 → `trac hotfix 42` 通过 precheck → 进入 hotfix 旅程的 **agent 派发**：
   - 有 opencode 二进制的宿主（本机）：真派发 → napi/deepseek-v4-flash 真实 LLM 流（分钟级/次）。
   - 任何宿主：派发失败/不可用 → `infra_failure_streak` 指数退避（run_loop.py:762 `time.sleep`，30→60→…→**900 s 封顶**；`TRAC_INFRA_RETRY_LIMIT` 控次数；machine_outcomes.py:185 文档字符串自证历史事故 **"106 minutes of self-waiting"**）。
   - **PATH 剥离实验（无 opencode）同样挂 ≥300 s** → 退避梯宿主无关 → **CI（无 opencode 二进制）也无法通过**。
4. 电池执行器无兜底：`tracks/executor/test_execute.py::_run_layer_command` 的 `subprocess.run(...)` **无 timeout**；选择集 XML 未产出前 runtime 永远等待。

### 3.2 证据索引

- events DB（`.tracks/runtime/tracks.db`，全 runs）：`run_tests` n=170、p50=2 s、p90=20 s、**max=7209 s**（今日 11:14–13:14 UTC 一次 invalid-red 后 13:17 立即重发，至今仍挂）；`commit_taskgraph` n=151、p50=1 s、**max=6123 s**（9/30 两次 102/100 min）。**电池平时是秒级——尾部 100% 由病灶选择集触发。**
- 活体样本：PID 86222（run 01M3E7SAANXKW1V73W8B8Q3G86，M-TEST RED_CHECK 选择集，29 节点，含病灶节点）22:17 JST 起挂 ~2 h；worker 86229 的子进程 86341 `python -m tracks.cli.main hotfix 42 --scenario post-release` 累计 CPU 0.64 s；`sample` 采栈：主线程在 `time_sleep→nanosleep`（退避梯）。
- CI：9/29、9/30、10/1 三个 run 全部 failure（病灶测试 9/28 01:37 入库后）；最后绿 test job 9/28 15:04（56 min）。
- 附加浪费（顺带发现）：9/24 起两个遗留 `http.server`（8891/8789 端口）未清理；当前树 ruff 有真实发现（本地 lint 门禁会挂）。

### 3.3 对「已知事实」的独立验证结论

| 用户提供 | 验证结果 |
|---|---|
| CI 9/24–28 ~56 min | ✓（gh 实测 9/24 57、9/28 56；**补充：9/17 是 44 min；9/29 起转红**） |
| 9fc12ab = 四 worker | ✓ 文件属实，但方向是 **8→4 降并行**（非引入并行） |
| test_github_tls.py:100 强制 opencode | ✓ 属实但**无害**：它在凭证检查处 fail-closed，从不派发（快） |
| test_hotfix_precheck_classification.py:89 | ✓ **这是元凶**，病灶在子项 (d) 的派发+退避梯 |
| adapter 三测试启动真实 opencode | ✗ 误归因：它们只做全量 unit collect（~15 s）；真 opencode 启动来自 hotfix 旅程 |
| 电池 = guard lint→多轮 integration→unit→选择集复验 | ✓ 结构属实；**但多轮"全量" pass 并非 2h 主因**（全量假通道 ~20 min；2h 是选择集含病灶节点时的无界等待） |

---

## 4. Q3 压缩方案（按性价比排序）

> 预算路径：现状 2h 尾部/假通道 19.6 min → **P0** 后电池尾部消除+CI 可绿 → **P1** 后本地全量 ≈ 11–12 min（≤15 ✓）→ **P2** 后 ≈ 9–11 min。上限 20 min 全程满足。

### P0-1 隔离真派发测试（预期：run_tests/commit_taskgraph 尾部 1–2h → 分钟级；CI 恢复可绿）【最高优先】

- **改什么**：
  - 拆分 `tests/integration/test_hotfix_precheck_classification.py`：保留 (a)–(c) 快路径分类测试（stand-in、无派发、~1 s）；把子项 (d)「retry-open 旅程」移入 `tests/e2e_live/`（既有 live-channel 惯例：conftest 串行真后端、CI `live-opencode` job 已存在且 opt-in 非必需）。
  - `pyproject.toml` addopts 无需加 marker（e2e_live 不在 testpaths 内）——若选择 marker 方案则 `addopts` 增加 `and not live` 并在文件头 `pytestmark`。
  - `.tracks/projects/v0.10/test-plan.md` §8 AC-FR0329-02 的层归属重锚（integration→e2e_live）+ `architecture.md` §5.1 required-AC 过滤同步——Archer 设计修订 + Prism 评审（既有流程）。
- **预期节省**：选择集电池 p99 从 1–2 h → ≤20 min；CI test job 恢复确定性。
- **风险**：AC 覆盖语义迁移（trace closure 重锚是一次设计修订）；live job 频率（周度+manual）意味着 (d) 的回归发现变慢——用 nightly job 兜底。
- **验证**：`pytest tests/integration/test_hotfix_precheck_classification.py` 全绿 <2 min；含旧节点 ID 的选择集重放映射到新位置（Runtime 选择集是节点 ID 制，需同步 baseline 重采集）；`tests/e2e_live` 在 live job 绿。

### P0-2 电池级超时 + 测试宿主退避梯参数（预期：任何未来挂死测试封顶 ~20 min）

- **改什么**：
  - `tracks/executor/test_execute.py::_run_layer_command`：`subprocess.run(..., timeout=BASE+PER_NODE*len(nodes))`（建议 base 900 s + 60 s/节点）；`subprocess.TimeoutExpired` → `verdict.failed check=battery_timeout`（infra 通道，不烧作者 attempt）。
  - 测试夹具层（`tests/_support/fixtures.py` 的 `trac`）：默认注入 `TRAC_INFRA_BACKOFF_MAX_SECONDS=5`、`TRAC_INFRA_RETRY_LIMIT=2`（kernel 已支持 env 调参，见 run_loop.py:737 / machine_outcomes.py:188）——**测试宿主里退避梯 106 min 是纯浪费**，真后端语义由 e2e_live 承担。
- **预期节省**：尾部封顶（1–2 h → ≤20 min/层）；对 p50 无影响。
- **风险**：超时分类语义需 Prism 认可（infra vs test_defect）；新 unit 测试覆盖 `battery_timeout` 通道。
- **验证**：注入 `time.sleep(10⁵)` 型测试节点 → 电池在 timeout 处 fail-closed 并落 verdict.failed。

### P0-3 CI 并行化 + coverage 关键路径（预期：test 56→~17 min；coverage 88→~30 min）

- **改什么**：`.github/workflows/ci.yml` 的 `test` 与 `coverage` 两个 step 的 pytest 命令加 `-n 4 --dist loadscope`（CI runner 4 vCPU；`pytest-xdist==3.8.0` 已在 dev extras；coverage 已 `parallel=true` + `coverage combine` 既有——天然兼容）。
- **预期节省**：CI 关键路径（coverage job）88→~25–30 min；test job 56→~16–18 min。
- **风险**：CI 4 vCPU 上 xdist 稳定性（本地 -n 4 长期绿是先例）；建议先 test 后 coverage 分两个 PR 各观察 3 次。
- **验证**：连续 3 次 push 的 job 时长分布；失败率不升。

### P1-4 本地并行 -n 4 → -n 8 + 分组均衡（预期：假通道 19.6 → ~11–12 min）

- **改什么**：
  - `.tracks/projects/project.toml` `[unit]/[integration]/[e2e]` 的 `run`/`run_selected` 模板 `-n 4`→`-n 8`（10 核 M4；**这是恢复 9/17 operator directive 之前的取值**——需 Aaron 确认推翻旧指令）；同步 v0.10 `architecture.md` guard-registry `config_digest` 重锚（9fc12ab 先例：digest 随合同变更同步重锚）。
  - 倾斜处理：unit 改 `--dist worksteal`（xdist 3.8 支持，节点细粒度无需分组）；integration 保持 loadscope（模块内 tmp 状态安全）+ 对 ≥25 s 的 6–8 个重模块（publish/milestone/journey 族）加 `@pytest.mark.xdist_group` 拆分（Shield 增量）；e2e 的 `test_six_journeys_dual_host`（94.8 s）参数化拆分（Shield）。
- **预期节省**：integration 15:10→~8–9 min；e2e 3:40→~2 min；unit 48→~30 s。
- **风险**：与后台常驻 opencode 抢核（电池在 CI/夜间更稳）；stand-in 端口为 ephemeral，端口冲突风险低；loadscope→8 worker 后模块粒度倾斜需观察首轮 `--durations`。
- **验证**：`-n 8` 全量连跑 2 次对比墙钟与 worker 尾差（目标最慢 worker ≤ 平均 1.3×）。

### P1-5 电池轮次去重：同身份选择集结果复用（预期：尾部重放 -30–50%）

- **改什么**：`tracks/executor/test_execute.py`：执行前查询同 `selection_id`（已含 nodes+basis+commit+tree_stamp 身份）的既有 per-node outcomes → 命中则复用（`evidence.reused` 事件语义已存在，扩展到电池层）；`commit_taskgraph` 锚点集与 `run_tests` 选择集重叠节点不再重复执行。
- **预期节省**：M-IMPL 每任务锚点复验与 28 min 级 gate 重放（run_task_gates n=977、max 1686 s）显著缩短；对 p50（秒级）无感。
- **风险**：复用条件的证据完整性（必须同身份同基线）；实现量中等。
- **验证**：A/B 一个任务周期：同选择集二次执行应 0 s 并落 reuse 事件；改树后必须重跑。

### P2-6 夹具/子进程开销（预期：integration 再 -10–20%）

- **改什么**：
  - `tracks/cli/main.py` 延迟重 import（boot 0.2 s 里可省 ~0.1 s；全套件约 1.1 万次 trac 启动 ≈ integration 墙钟 ~10%）。
  - `tests/_support/fixtures.py::StepLog.step` 的每行 `flush()` 改缓冲（高频 journey 测试的 I/O 浪费）。
  - session 级 `host_repo` 仅对无跨测试状态的模块逐个评估（Shield 增量，收益中/风险中，非本轮承诺）。
- **验证**：同层 `--durations` 前后对比。

### P2-7 结构性清理（小额+卫生）

- pylint 两遍合一遍（`R0801,C0302,R0915,R0914` 单次跑，`ci.yml` lint job + guard registry 同步）：每电池轮 -10 s。
- 清理 9/24 遗留的 8891/8789 `http.server` 与孤儿进程（宿主卫生，防端口/资源噪音）。
- 修复当前树 ruff 真实发现（I001/W292 两处，`tests/unit/test_pending_wal_failure_selected.py`、`test_t010_lex_review_park_red.py`）——本地 lint 门禁当前会挂。
- `collect_tests`（p50 4–6 s ×12 次/天）与 `capture_baseline`（4–7 s）忽略不计。

### 汇总表

| 项 | 动作 | 本地假通道 | 电池尾部 | CI 关键路径 | 风险 |
|---|---|---|---|---|---|
| 现状 | — | 19.6 min | 1–2 h | 88 min（红） | — |
| P0-1/2/3 | 隔离+超时+CI 并行 | 19.6 min | **≤20 min 封顶** | **~30 min（绿）** | 低 |
| +P1-4/5 | -n 8+去重 | **~11–12 min** | 分钟级 | ~25 min | 中低 |
| +P2 | 夹具/进程 | ~9–11 min | — | ~22 min | 低 |

---

## 5. Q4 opencode 2.x 升级评估（Aaron 特别问）

**事实底座**：当前 1.18.30（9/9 发布）。1.x 仍在活跃维护（v1.18.34，9/30；v1.18.27 引入 provider/stream-chunk 5 min 默认超时——与本仓挂死形态直接相关的加固）。**v2 现版本 2.0.6**，官方独立安装通道（`brew anomalyco/tap/opencode-v2`、npm `@opencode/cli`）——**可与 1.x 并存**，支持并行灰度。2.x 为大重写（新文档树 /v2/docs）。

**适配面盘点（迁移成本）**：`tracks/effects/opencode*.py` 11 个模块 **3,097 LOC**，深度耦合 1.18 具体行为：
- `opencode run --format json` 管道 stdout 死锁 → **PTY 绕过**（master_fd spawn）；
- NDJSON 流回退解析、会话中途失效检测、manifest 畸形修复、`--auto` post-completion loop 处理。
- 配置面：`~/.config/opencode/opencode.json`（napi/deepseek-v4-flash 主模型、step5 代理、插件 opencode-logger + 本地 prompt-tracker）与项目级 `opencode.json`（permission external_directory 规则）——v2 配置/插件兼容性未验证。
- 回归网只有 `tests/e2e_live/` 11 文件（live channel）+ 真实 dispatch。

**对「运行时派发速度」的预期收益**：dispatch_agent 实测（events DB，n=199）**p50=170 s、p90=27 min、max=103 min**——时长主导项是模型流（deepseek-v4-flash 长上下文推理）与任务复杂度，opencode 进程开销占比小。2.x 官方宣称的缓存/token 效率改进（"5×"）**未在本仓验证**；即便为真，合理预期是 p50 降 10–30%（缓存命中部分），不会把 170 s 变 30 s。**对「真后端测试耗时」**：本仓病灶是退避梯+无超时+隔离缺失（结构问题，§4 P0），升级 2.x 不解决；P0 落地后真后端测试已移出默认电池，2.x 对电池时长几乎无杠杆。

**风险**：3.1k LOC 适配面全部要按 v2 的 CLI/API/输出格式重验证；PTY/NDJSON/会话语义 workaround 可能全部失效或需重写；v2.0.x 早期（2.0.0–2.0.6）成熟度待观察；迁移期双版本配置漂移。

**建议（明确回答：升/不升/何时升）**：
1. **v0.10 发布前：不升 2.x。** 先收 P0/P1（结构性、低风险、可量化）；升级窗口错开发布节奏。
2. **立即做：1.18.30 → 1.18.34**（同线补丁，含超时/流处理/二进制签名修复；适配面零改动预期；用 e2e_live 一次回归确认）。
3. **v0.10 发布后：开 2.x spike（独立分支 + side-by-side 安装）**，A/B 基准至少 20 次 dispatch_agent（p50/p90/token 用量/失败率）+ e2e_live 全量；**决策门槛**：p50 改善 ≥25% 且 e2e_live 全绿且适配 diff ≤ ~800 LOC 才立项迁移；否则停在 1.x 线（其维护活跃度足以支撑）。
4. 全量切换不早于 2.x 再出 2–3 个稳定 minor。

---

## 6. 附录：证据与复现索引

- 分层基线：本报告 §1 表（2026-10-01/02 实测，`.venv/bin/python -m pytest ... -n 4 --dist loadscope -q --durations`）。
- v0.9 junit：`/var/folders/.../T/tracks-results/01M2QTJBG3CRVY750KN3NYQ9AZ/01M3AEZYX2813R80KEYMPE19KA-full-full_f-{unit,integration}.xml`（631 节点 / sum 1830 s / median 0.40 s / max 20.07 s）。
- events DB：`.tracks/runtime/tracks.db`（runs/events 表；run_tests n=170 max 7209 s；commit_taskgraph n=151 max 6123 s；dispatch_agent n=199 p50 170 s p90 1623 s）。
- 活体挂死：PID 86222/86229/86341（run 01M3E7SAANXKW1V73W8B8Q3G86）；`sample 86341` 主线程 `time_sleep`；退避梯 `tracks/executor/run_loop.py:731-762`（cap 900 s、`TRAC_INFRA_BACKOFF_MAX_SECONDS`）、`tracks/kernel/machine_outcomes.py:185`（"106 minutes of self-waiting"、`TRAC_INFRA_RETRY_LIMIT`）。
- steps 日志：`$TMPDIR/tracks-steps/tests_integration_test_hotfix_precheck_classification__*.steps.ndjson`（(a)–(c) 各 ~0.2 s；(d) 无返回）。
- PATH 剥离实验：无 opencode 仍挂 ≥300 s（宿主无关退避梯）。
- CI：gh run list/view——9/17 test 44 min；9/24 test 57 / coverage 88 min；9/28 test 56 min（绿）；9/29、9/30、10/1 run 级 failure（病灶测试 9/28 01:37 入库，commit 9a36bd2）。
- 电池合同：`.tracks/projects/project.toml`（[unit]/[integration]/[e2e] run/run_selected；[nightly]；host-contract local_gate）；CI：`.github/workflows/ci.yml`（test/coverage 串行）。
- 9fc12ab diff（-n 8→4 的降并行 + v0.7/v0.8 registry digest 重锚先例）。
- opencode：本机 1.18.30；releases v1.18.34（9/30）、v1.18.27（stream timeouts）；v2 = 2.0.6（/v2/docs、独立安装通道）。
