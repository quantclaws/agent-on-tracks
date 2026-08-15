# Handoff — tracks v0.5 run `01KZTHE7RMZE6110PK9C54K1E2` 陪跑交接

- **日期**: 2026-08-15（本文由 Aaron 的助手在陪跑会话中写就，交接给下一位跟 run 的人）
- **服务器**: `ssh mini-one-vpn`，仓库 `~/workspace/tracks`，分支 `releases/v0.5`
- **CLI**: 一律用 `.venv/bin/trac`（不是全局 trac）
- **Run**: `01KZTHE7RMZE6110PK9C54K1E2`，M-TEST 阶段
- **本文状态截至**: 2026-08-15 05:15 UTC（13:15 北京时间）。Shield WRITE attempt 3 于 04:17:56 UTC 派发，正在执行中（工作树可见其进行中的反例 patch 重写）

---

## 1. 三十秒版本

Run 卡在 M-TEST。Shield 写测试 → Prism 评审 → RED_CHECK → trace 检查的循环里已经转了 4 轮评审（R1→R4）。刚过去的一轮里 Prism verdict=**revise**，两条新 blocker：

- **R4-01**（小): AC-FR0232-03 的 trace marker 形式上绑定了（commit 6c0525f），但绑到了只断言负向路径的 integration 测试上，语义错绑。要求把 marker 加到 e2e_live 的 `test_real_journey_exposes_events_lineage_report_and_boundary`（tests/e2e_live/test_m_impl_release_evidence.py:414-429 已断言 satisfied+exit 0）函数上方，一行注释即可；integration 负向测试按 §2d 判断保留或改绑 AC-FR0232-04。
- **R4-02**（大）: tests/counterexamples/v0.5/ 下 19 个反例 patch 全部 `git apply --check` 失败——它们改的是 `raise NotImplementedError("IF-IMPL-00X")` 桩行，而那些模块（rgr.py、worktree.py、taskgraph.py 等）如今已真实实现，桩行不存在了。m_impl_lifecycle.patch 是 165 字节的 `# pending-implementation` 占位注释，不是合法 patch。kill-manifest.json 全部 49 条标 pending-implementation 且顶层 contract 声明同口径，与 §12.1（预实现子集退役该语义）矛盾。Prism 要求：对真实实现重写 19 个最小偏差 patch + 替换占位 + 逐个执行 §12.5 kill 协议（apply → 跑绑定节点 → 记录 killed/survived + 新鲜验证 → reverse）+ 更新 manifest。

Shield attempt 3 正在做的就是 R4-01+R4-02。

## 2. 事件链与已翻越的坎（供考古）

| 时间 (UTC) | 事件 | 意义 |
|---|---|---|
| 08-14 21:26–22:30 | Shield WRITE 三连 over_reach escalation | hook 放 tests/ 根（白名单外）+ 私改 pyproject 加 lint 豁免。Prism DENY 正确但理由不回流，Shield 盲猜三轮烧 116 分钟 |
| 08-15 02:02 | 人工 retry 后重启 loop | Shield 一次通过 WRITE（541s）|
| 02:11–02:21 | PRISM_REVIEW attempt 1/2 verdict.failed | **校验器误报**：validate.py `_TRAC_CALL` 正则把 test-plan.md 散文里 "console script with cwd=HOST" 误判为 `trac with` 伪命令 |
| ~03:00 | 人工修 validate.py + 提交 d38d778 | 命令上下文=围栏+反引号 span+行首裸命令；散文不再误报。测试全绿，真实 test-plan.md 校验 0 误报 |
| 03:29 | 重启 loop 失败三连 non_zero_exit (0.2s) | **我的锅**：ssh 双引号串里 `$HOME` 被本地展开成 `/Users/aaronyang`，远端 opencode mkdir EACCES。03:30 用 heredoc 正确重启 |
| 03:30–03:41 | PRISM attempt → pass | verdict=pass，第一个绿评审 |
| 03:41–03:51 | RED_CHECK | integration+e2e 全是合法 assertion_failure（red.validated=**valid**），但 trace 门禁报 `AC-FR0232-03 has no test marker bound` → 回 WRITE |
| 03:51–03:55 | Shield attempt 2（270s）| commit 6c0525f：marker 从 0232-02 改绑 0232-03。trace ok，形式闭合 |
| 03:55–04:17 | PRISM attempt 2（1328s）| verdict=**revise**：R4-01（错绑）+ R4-02（19 patch 过时）|
| 04:17:56 | Shield attempt 3 派发 | **当前正在跑**。工作树已见 10+ 反例 patch 被修改 |

## 3. 关键背景知识（不读会踩坑）

### 3.1 你在跟的是什么系统

tracks 是一个 agent-on-tracks 工作流系统（v0.5）：Runtime（trac CLI）编排 Shield（写测试）、Devon（写实现）、Prism（评审）、Archer（配置/布局所有者）。M-TEST 阶段 Shield 写 integration/e2e 测试 → Prism 评审 → RED_CHECK（Runtime 独立跑测试验证"合法 Red"）→ trace 闭合检查 → EXIT。attempt≤3，超限 escalation 等人。

### 3.2 监控纪律（用户明确要求）

- **不要写自动监控脚本**。之前 Agent 写的 night-monitor 系列已全部删除。由人（你）用 `tail logs/trac-run.log` + `trac status` + 事件库亲自看，因为需要**语义判断**（同因复发要停下分析，不能盲目 retry）。
- 观察时用可中断的循环（`for i in $(seq 1 N); do sleep 60; ...done` 包在 ssh 里），别 nohup 常驻。
- escalation 时人工判断后：`.venv/bin/trac retry --actor 你的名字`（会清 escalation 并重置 attempt 预算），然后确认 loop 还活着。

### 3.3 loop 进程与重启（重要，我踩过的坑）

- loop 进程：`pgrep -fl 'trac run'`。当前一个在跑（03:30 UTC 起）。
- **重启 loop 必须用单引号 heredoc**，让 `$HOME` 在远端展开：
  ```bash
  ssh mini-one-vpn 'bash -s' <<'REMOTE'
  cd ~/workspace/tracks
  nohup env -u TRAC_AGENT_MODEL -u TRAC_AGENT_TIMEOUT \
    PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
    HOME="$HOME" .venv/bin/trac run >> logs/trac-run.log 2>&1 &
  REMOTE
  ```
  双引号串会让本地 shell 展开成 `/Users/aaronyang` → opencode EACCES → 0.2s 三连失败（03:29 事故）。
- escalation 后 restart 前先 `trac retry --actor NAME`。
- `.env`：TRAC_LIVE_PROVIDER=litellm、base `http://192.168.0.102:4001/v1`、model deepseek-v4-flash。Shield/Prism 单次 dispatch 正常时长 5–25 分钟（Prism 评审长的到 22 分钟），0.2s 失败=环境问题不是模型问题。

### 3.4 本轮会话已完成的修复与产物（都在 git 里）

| commit | 内容 |
|---|---|
| d38d778 | validate.py 命令上下文修复（误报根因）+ 回滚我此前越权写的 Shield.md 条款和 project.toml 白名单手改 + 回归测试 |
| a24d99a | **SC-D38 提案**：8 步 over_reach 裁决轮次（部分回滚+理由回流+Shield 自接受分支+申诉通道+Archer 回流+loop 级 attempt 重置），v0.6 backlog，先例格式仿 SC-D35 |
| 6c0525f | Shield 自己的 marker 修复（attempt 2 checkpoint）|
| 7664873 | prism (revise) checkpoint |

未提交残留：
- `tests/` 根下 Shield 的旧 hook 副本（untracked，`tests/doc_gap_injection.py`、`tests/_doc_gap_hook/`）——白名单已回滚，这两个属越权残留，等 Shield 按 8 步机制自行处理或人工确认后删
- 我本地 workspace 的 `mcp-tracks-status-*` 临时文件（远端无关）

### 3.5 遗留开放项（非阻塞，v0.6 排期）

- **D-37 / SC-D38**：over_reach 处置 8 步轮次提案已在 `.tracks/wiki/spec-oob-adjudication-loop.md`，等 v0.6 M-SPEC 立项并入。D-36（OOB 排期）相关。
- **死代码发现**：`create_devon_worktree`/`create_test_authority_worktree`（tracks/executor/worktree.py:27/44）无生产调用，仅 unit test 用。v0.5 内核只接线了 gate worktree（m_impl_runtime.py:1087）。这影响 R4-02 交付形态：worktree_isolation/baseline_events 相关绑定按 §12.3 应标 pending-implementation 而非硬造 patch。Shield attempt 3 似乎已理解这一点（它在排查 fake backend 的 devon dispatch 物化路径）。
- **§12 协议缺口**（用户裁定方向）："kill patch 必须可应用" 隐含所有绑定都该有活路径。建议 v0.6 分层：已接线绑定走完整 kill 协议；未接线标 pending + 指向接线 FR；死代码不留在 tracks/。
- handoff-20260815.md（用户手放的旧 handoff）两条技术错误已勘误（baseline 每次 dispatch 前重新快照；manifest_malformed 早退路径），见 4a6142d。

## 4. 关键文件索引

| 文件 | 用途 |
|---|---|
| `logs/trac-run.log` | loop 日志，`tail -f` 用 |
| `.tracks/runtime/tracks.db` | 事件库（SQLite）。`SELECT seq, ts, type FROM events WHERE run_id='01KZTHE7RMZE6110PK9C54K1E2' ORDER BY seq DESC LIMIT 10;` |
| `.tracks/runtime/prompts/` | 每次 dispatch 的完整 prompt 存档（Role-时间戳.md），看 Shield 在干嘛 |
| `.tracks/projects/v0.5/test-plan.md` | 测试计划（Prism findings 就锚定在这里，`grep 'PRISM-V05-R4'`）|
| `tests/counterexamples/v0.5/kill-manifest.json` | 49 条绑定的 kill 证据清单（R4-02 的整改对象）|
| `tracks/executor/validate.py` | 已修的命令上下文校验 |
| `tracks/effects/audit.py` / `opencode.py` / `adjudicate.py` | 审计/派发/裁决管线（SC-D38 改造对象）|
| `.tracks/wiki/spec-oob-adjudication-loop.md` | SC-D38 提案全文 |
| `.tracks/wiki/handoff-20260815.md` | 旧 handoff（含勘误标记）|

## 5. 你接手后的操作手册

1. **看 attempt 3 结果**：
   ```bash
   ssh mini-one-vpn "cd ~/workspace/tracks && .venv/bin/trac status && tail -8 logs/trac-run.log"
   ```
   - `shield done` → 看 checkpoint commit + 等下一轮 PRISM_REVIEW
   - `shield failed` + escalation → 读 reason，判断：真缺陷就让 Shield 重派（retry+重启 loop）；校验/环境误报就先修再 retry（先例：d38d778）
   - 还在跑 → 耐心等（R4-02 工作量大，60-90 分钟正常）
2. **看 Prism 下一轮 verdict**：`verdict=pass` → 自动进 RED_CHECK → 留意 trace 门禁（上次 R4-01 就是 trace 过了语义没过）；`revise` → 读新 findings 决策
3. **语义判断要点**：R4-02 验收不看"19 个 patch 都改了"，看 kill-manifest 里每条绑定的 result/verification 是否新鲜真实（apply → 跑 → killed 记录 → reverse），顶层 contract 声明是否改口
4. **EXIT 条件**：Prism pass + RED_CHECK 全合法 Red + trace ok → M-TEST EXIT 进 M-IMPL
5. 全程不要替 Shield 写测试、不要手改白名单（我就是前车之鉴，已回滚，见 d38d778）

## 6. 成本与积分说明

用户关心过消耗归属：Shield/Prism 走的是仓库 `.env` 配置的 litellm 端点（`http://192.168.0.102:4001/v1`，deepseek-v4-flash）——**这是用户自己的 key**，不是 autoclaw 的积分。陪跑监控本身（ssh + sqlite 查询）零模型消耗；只有你（接手人）用自己的助手会话产生的消耗才走你自己的账户。watchdog/哨兵式观察不烧积分。

## 7. 联系与升级路径

- 服务器只此一台：mini-one-vpn。工作目录 `~/workspace/tracks`。
- run 前史与决策链：`.tracks/wiki/decisions.md`（D-35/D-36/D-37）+ `.tracks/wiki/handoff-20260815.md`
- 遇到本 handoff 描述与实际不符：以 git log + 事件库为准，本文写于 attempt 3 进行中。
