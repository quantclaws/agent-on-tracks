---
architecture_id: ARCH-006
spec_ref: SPEC-006
created: 2026-08-19
status: draft
sha:
---

# v0.6 — 架构

本文是 ARCH-005（v0.5）的增量延伸。v0.1 的内核机制（事件溯源、单一生产路径、四增长轴分包、单写者锁、per-kind reconcile）、v0.2 的物化合同与 audit、v0.3 的 M-DESIGN Archer/Prism、v0.4 的 M-TEST 与 checks/ 增长轴、v0.5 的 M-IMPL 控制流与 Devon 接入、live evidence 与 doc-comment-first 全部保持不变。v0.6 在既有包内新增 hotfix 工作流：trac hotfix 入口命令（**待实现：Devon foundation task 交付 cmd_hotfix 并同步 USAGE/TRAC_SUBCOMMANDS**）、HOTFIX-TRIAGE 入口子状态机、`fix/{issue}` 隔离分支、基线继承、M-DESIGN delta → M-TEST RED-first → M-IMPL 隔离实现 → boundary 的 hotfix 旅程、run 并存与串行接续、缺口路由。唯一新增顶层 CLI 命令是 trac hotfix（SPEC-006 范围排除）。凡未提及者，一律继承 ARCH-005。本文以散文形式引用该待实现命令的语法（不加反引号、不入代码 fence），正是 fabricated-command guard 要求的「to-be-created tooling 以 foundation task 标注」形态。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-005 不变项

- 唯一工作流生产路径 `cli -> kernel.runtime.run_loop -> effects.executor.execute`：不变。hotfix 的状态变更命令仍只此一路；trac hotfix 是新入口 shell（待实现 foundation task），内部仍经 kernel decide → executor execute（§1.1）。
- 四增长轴分包 kernel/workflows/effects/checks + discuss 旁路：不变。v0.6 在 `kernel/`（hotfix 子状态机）、`executor/`（hotfix 副作用 + 新模块 hotfix.py）、`effects/`（github issue 读取 + fake 通道行为）、`cli/`（trac hotfix / status / approve 扩展）、`checks/`（trace 跨版本解析）上生长，不引入新增长轴。
- 事件溯源 SQLite append-only + 投影可重建：不变。HOTFIX-TRIAGE 全程事件 append-only（NFR-0100-04）；投影表 drop 后 `trac status` / `trac report` 重建的入口状态与原一致。
- 单写者锁 / 取消协议 / per-kind reconcile：不变。FR-0242-04 的「第二个并发 trac 命令被拒绝并报持有者 PID」直接复用既有 `writer_lock`（`LockHeld` → stderr `runtime lock held by pid <N>` + exit 1），零代码增量，仅补回归覆盖。
- 纯核心 `project`/`decide` 零 I/O：不变。PRECHECK 的 I/O（issue fetch、分支探测、文件扫描）全在 executor 侧；kernel 只消费事件。
- FakeBackend 确定性函数 + OpencodeBackend：不变。fake 通道以 `.tracks/runtime/host-issues.json` 种子文件承载确定性 issue 语料，`TRAC_FAKE_SIMULATE` 注入 Sage 锚定与 Prism 裁定分支。
- inline-discussion 旁路、模板 + 校验（templating.py + executor/validate.py）、质量守卫栈 + pre-commit + CI required checks、IF- 标识注册表不可复用规则：全部不变（§4.2/§4.3）。
- v0.4/v0.5 已建立的 20 个 IF- 标识不可变、不可复用；v0.6 增补 IF-HOTFIX-001～009 共 9 个新标识（interfaces.md §5）。

### 0.2 v0.6 变更项

- `tracks/cli/main.py` 新增 `cmd_hotfix`（trac hotfix <issue> --scenario post-release|dev 入口 + trac hotfix anchor / trac hotfix feature-route 两个 AWAIT_HUMAN 子动作；三形态均为待实现 foundation task，随实现同步 USAGE 与 validate 的 TRAC_SUBCOMMANDS）；`cmd_status` 扩展（branch/scenario/issue 字段 + 挂起 run 清单）；`cmd_approve` 扩展（hotfix ac_gap/spec_gap 退出决定）。
- 新增 `tracks/kernel/hotfix.py`：HOTFIX-TRIAGE 子状态机（`_decide_hotfix_triage` 显式控制流 + 事件 reducer + 锚定引用解析纯函数）。`machine.py` 接线：stage 值 `M-HOTFIX-TRIAGE`（非 canonical 顶层阶段，不进 `_NEXT_STAGE` 链）、State 增量字段、M-DESIGN Prism anchor 推翻回滚路由。
- 新增 `tracks/executor/hotfix.py`：PRECHECK 程序预检（issue fetch + scenario/活跃分支/目标版本定位）、锚定程序校验（validate_anchor）、ANCHORED 入口完成（fix/{issue} 分支 + 基线继承 + `stage.entered(M-DESIGN)`）。
- `tracks/effects/github.py`：`FakeIssueBackend` / `GithubBackend` 增补 `fetch_issue`（读通道）；fake 通道新增宿主 issue 种子文件契约 `.tracks/runtime/host-issues.json`。
- `tracks/kernel/events.py`：EVENT_TYPES 追加 5 个 hotfix 事件；COMMAND_KINDS 追加 3 个命令；`run.completed.terminal_state` 追加 4 个取值；`verdict.failed.check` 追加 `anchor_invalid`。
- `tracks/capabilities.py`：版本解析支持 hotfix 身份后缀（`v0.5-hotfix-42` → 能力等价 `v0.5`），fail-closed 规则不变。
- `tracks/checks/trace.py` + `tracks/executor/validate.py` + `tracks/executor/test_tasks.py`：跨版本 AC 引用解析（`AC-FRXXXX-YY@<version>` 解析到所指版本 acceptance.md）；hotfix delta test-plan 的层归属校验（unit 层仅对 hotfix delta plan 合法）。
- `tracks/kernel/m_test.py` + `tracks/executor/executor.py`：hotfix M-TEST 空 Shield 增量放行路径（§3.6）。
- `tracks/executor/m_impl_runtime.py`：hotfix M-IMPL BASELINE digest 增补场景 B 活跃分支 HEAD 输入（stale → NEEDS_ATTENTION 即 reconcile 冲突路径）；`executor.py` 的 hotfix run 完成处理附带分支恢复 checkout。
- `tracks/effects/fake.py` + `tracks/effects/fake_shield.py`：fake 通道 hotfix 行为（Sage 锚定 outcome、Prism anchor 推翻 token、delta 三文档、跨版本回归 test 任务）。
- `.github/workflows/ci.yml`：live 通道增补 hotfix GitHub 读取探针（weekly `live-opencode` job 扩展 + milestone `release-evidence` job 扩展）——**待 Devon foundation task 补全**（§4.3）。
- `.tracks/projects/project.toml` 测试执行合同：路径与内容不变（v0.6 无测试目录变更），继续作为当前 Runtime 消费的 canonical 合同。

## 1. 模块边界

### 1.0.1 增长轴归属

v0.6 新代码落在既有增长轴上：

| # | 包 | v0.6 增长 | 触发 | IF- 标识 |
|:---|:---|:---|:---|:---|
| 1 | `cli/` | cmd_hotfix（入口 + anchor/feature-route 子动作）、cmd_status 扩展、cmd_approve 扩展 | FR-0240/FR-0242/NFR-0110 | IF-HOTFIX-001, IF-HOTFIX-006 |
| 2 | `kernel/`（新模块 hotfix.py + machine.py 接线 + events.py） | M-HOTFIX-TRIAGE 子状态机 + State 字段 + 事件/命令 + anchor 推翻回滚路由 | FR-0240 SM-01 | IF-HOTFIX-002 |
| 3 | `executor/`（新模块 hotfix.py + executor.py/m_impl_runtime.py 增量） | PRECHECK / validate_anchor / complete_hotfix_entry / 场景 B baseline / boundary 分支恢复 | FR-0240/FR-0241/FR-0245/FR-0246 | IF-HOTFIX-003/004/005/008 |
| 4 | `effects/`（github.py + fake.py + fake_shield.py） | issue 读通道 + fake hotfix 行为 | FR-0240 PRECHECK / SAGE_TRIAGE | IF-HOTFIX-003/004 |
| 5 | `checks/`（trace.py）+ `executor/validate.py` + `executor/test_tasks.py` | 跨版本 AC 引用解析 + delta test-plan 层归属校验 | FR-0244 | IF-HOTFIX-007 |
| 6 | `kernel/m_test.py` + `executor/executor.py` | 空 Shield 增量放行（increment.declared） | FR-0244-04 | IF-HOTFIX-007 |
| 7 | `capabilities.py` + `paths.py`（无新增，复用 version_dir） | hotfix 版本身份解析 | FR-0241 | IF-HOTFIX-005 |
| 8 | `kernel/m_impl.py` + `cli/main.py`（approve 扩展） | ac_gap/spec_gap 退出路由 + Human 决定证据 | FR-0247/FR-0248 | IF-HOTFIX-009 |

新增 `kernel/hotfix.py` 与 `executor/hotfix.py` 不是新增长轴：前者是 kernel 轴内 M-HOTFIX-TRIAGE 控制流模块（被 machine.py 分发，保持 decide()/project() 纯函数边界）；后者是 executor 轴内副作用模块（被 executor.py handler 调用，可执行 git/文件/网络副作用，不被 kernel 引用）。分层约束与 v0.5 的 kernel/m_impl.py + executor/m_impl_runtime.py 同构。

### 1.0.2 HOTFIX-TRIAGE 控制流（kernel/hotfix.py）

HOTFIX-TRIAGE 是 hotfix 入口的附属子状态机（SPEC-006 SM-01；flow.md §16.4），**不是** canonical 顶层阶段：stage 值 `M-HOTFIX-TRIAGE` 由 `cmd_hotfix` 写入 `stage.entered`，不注册进 `_NEXT_STAGE` 链（M-DESIGN 之后的接续完全复用 canonical 序列）。事件落在 hotfix run 自己的事件流（run 于入口即建立）。

子状态封闭集：`PRECHECK | SAGE_TRIAGE | AWAIT_HUMAN`；终态转移 `ANCHORED → stage.entered(M-DESIGN)`、`REJECTED → run.completed(terminal_state="rejected")`、`FEATURE_ROUTE → backlog.recorded + run.completed(terminal_state="feature_route")`。未列出的转移不允许（SM-01 行号即合同）。

`_decide_hotfix_triage` 按 substate 分发（kernel 纯函数，I/O 全在 executor）：

- `PRECHECK`：产出 `precheck_hotfix` command。executor 执行确定性预检（IF-HOTFIX-003），产出 `triage.prechecked(status="pass")` 或 `triage.prechecked(status="rejected", reason, next)`。decide 路由：pass → SAGE_TRIAGE；rejected → run.completed(rejected)（不建 fix 分支）。
- `SAGE_TRIAGE`：产出 `dispatch_agent(role=sage, substate=SAGE_TRIAGE)` command，assignment 携带 issue 语料 + 目标/历史版本 spec/acc 路径 + 可选字段提示（IF-HOTFIX-004）。Sage outcome 后 executor 产出 `validate_anchor` command：锚定引用逐条解析（所引每条 AC 必须真实存在于所指版本 acceptance.md），产出 `anchor.validated` 或 `verdict.failed(check="anchor_invalid")`。decide 路由：validated → `complete_hotfix_entry`；invalid → 重派 Sage（≤3，SM-01.6）；NO_ANCHOR 或重派超限 → AWAIT_HUMAN（SM-01.7，不自动转 feature）。
- `AWAIT_HUMAN`：`status="awaiting_human"`、`awaiting="hotfix_triage"`；`trac status` 报告 `awaiting=awaiting_human origin=hotfix-triage issue=<N>`。Human 动作经 CLI 子命令落地：trac hotfix anchor <AC@ver ...> → `human.anchor(mode="manual")` + 程序校验 → ANCHORED（SM-01.8）；trac hotfix feature-route → `human.anchor(mode="feature_route")` → FEATURE_ROUTE（SM-01.9/.11）。
- ANCHORED 入口完成（SM-01.10，原子）：`complete_hotfix_entry` command → executor 记录 issue→AC 锚定、按场景创建 `fix/{issue}` 分支（场景 A base=main HEAD；场景 B base=活跃 release 分支 HEAD）、继承目标版本已批准基线（source approval，见 §3.4）、`stage.entered(M-DESIGN)`。
- `M-DESIGN Prism anchor 推翻`（SM-01.6 重派入口的第二个来源，FR-0243-03）：M-DESIGN PRISM_REVIEW verdict 携带 `anchor_verdict="overturned"` → `stage.rolled_back(from_stage="M-DESIGN", to_stage="M-HOTFIX-TRIAGE", reason="anchor_overturned")` → 回 SAGE_TRIAGE 重派 Sage（不消费 M-DESIGN 重派预算）。

HOTFIX-TRIAGE attempt 预算：SAGE_TRIAGE 锚定校验失败重派 ≤3（复用 `current_attempt` 机制）；第 3 次仍不产出合法锚定 → AWAIT_HUMAN。PRECHECK 无 attempt（确定性，失败即 REJECTED 终态，允许同 issue 补全后重试——重试是新 run）。

### 1.0.3 hotfix run 身份与版本能力

- **run 版本身份**：hotfix run 的 `version` = `{target_version}-hotfix-{issue}`（如 `v0.5-hotfix-42`）。单一身份字符串贯穿 store/runs、backend 选择、`paths.version_dir`——hotfix run 项目目录自然为 `.tracks/projects/v0.5-hotfix-42/`，与目标版本基线目录 `.tracks/projects/v0.5/` 区分（AC-FR0243-01）。
- **能力继承**：`capabilities._version_tuple` 扩展接受 `-hotfix-\d+` 后缀并按 base 版本返回数值组（`v0.5-hotfix-42` ≡ `v0.5`），`supports_m_impl` 等能力谓词对 hotfix run 正确返回 True（否则 M-TEST EXIT 会错误落在 M-TEST boundary 而非进入 M-IMPL）。malformed 仍 fail closed。
- **run 生命周期**：trac hotfix 在 `hotfix.requested` 时建立 run（即使 REJECTED 也保留事件与 run 行作审计，随即 `run.completed(terminal_state="rejected")`，无活跃 run 残留、无分支副作用——「不建立 hotfix run」的observable 合同 = 无 active run + 无 fix 分支，事件流照常可审计，与 E-01 输出一致）。
- **不创建需求阶段产物**：hotfix run 的 stage 序列从 `M-HOTFIX-TRIAGE` 直达 `M-DESIGN`，store 中不存在该 run 的 M-STORY/M-SPEC/M-ACC/M-REQ-APPROVAL 事件；hotfix 项目目录不创建 story/spec/acceptance（FR-0241-02）。

### 1.0.4 run 并存、单一 active 与恢复模型（FR-0242/NFR-0110）

- **并存允许**：`cmd_hotfix` 不做 `cmd_start` 式的 active-run backlog 挡板（场景 A 不被 CHECK_ACTIVE 阻塞是 SPEC 明确的有意偏离；场景 B 依赖活跃 release 分支，天然要求活跃 feature run 或 releases/* HEAD）。
- **单一 active run**：沿用既有 `active_run()` 语义（最新非 completed run），零 schema 变更：hotfix run 建立即成为最新 → active；`trac run` 续跑 hotfix run；hotfix boundary/completed 后 hotfix 行退出 active 候选，挂起的 feature run 自然恢复 active（updated_ts 排序下它是下一个非 completed run）。
- **挂起 run 可观察**：`cmd_status` 在显示 active run（含 branch/scenario/issue 字段）之后追加 `suspended:` 行，逐个列出其余非 completed run 的 `run_id + stage + substate + branch`（数据来自 runs 表投影，可重建）。`trac replay <run_id>` / `trac report --run-id` 既有能力不变。
- **boundary 分支恢复**：hotfix run `run.completed` 处理器（executor）在完成事件后执行分支恢复 checkout——切换工作树到恢复为 active 的 run 的分支（Runtime 是唯一 branch/worktree authority），payload 记录 `restored_active_run` / `restored_branch`。事件库与 blob 在 runtime 目录（gitignored），分支切换不破坏任何 run 状态。
- **崩溃恢复**：`_recover()` 复用 per-kind reconcile（D-13）；`precheck_hotfix` / `validate_anchor` / `complete_hotfix_entry` 各有幂等 reconcile 语义（已成功则跳过；`triage.prechecked(pass)` / `anchor.validated` / `branch.created(fix/N)` 事件即幂等标记）。重启后 `trac status` 报告的 HOTFIX-TRIAGE 子状态与中断前一致。

### 1.1 Composition Root

hotfix 的装配入口有二：trac hotfix（入口旅程，同步驱动 triage）与 `trac run`（M-DESIGN 之后续跑）。每条 required AC 的六元组 composition 列在此节可追溯：

**hotfix 入口路径（FR-0240/FR-0241）**（cmd_hotfix 待实现 foundation task；下述调用形态为合同语法，非既有命令）：

```
CLI 入口形态（trac 顶层 hotfix 子命令）: <issue> --scenario post-release|dev
  -> tracks.cli.main:cmd_hotfix
    -> 参数校验（--scenario 缺省：stderr 提示 + exit 2，不建 run，不推断）
    -> with writer_lock(home):            # 复用单写者锁（FR-0242-04）
       store.append(hotfix.requested)     # run 于入口即建立
       store.append(stage.entered, {"stage": "M-HOTFIX-TRIAGE"})
       Executor(store, repo, run_id).run_loop()
         -> kernel.machine.decide(state)
           -> _decide_hotfix_triage(s, sub)         # IF-HOTFIX-002
             -> Command(kind="precheck_hotfix")     # PRECHECK
             -> Command(kind="dispatch_agent", role=sage, substate=SAGE_TRIAGE)
             -> Command(kind="validate_anchor")     # 锚定程序校验
             -> Command(kind="complete_hotfix_entry")  # ANCHORED 原子入口完成
         -> Executor._execute(cmd, state)
           -> _do_precheck_hotfix     # executor/hotfix.py：issue fetch + scenario/活跃分支/目标版本定位
           -> _do_dispatch_agent      # 既有派发管线（sage 为既有 agent，assignment 携带 anchor 语料字段）
           -> _do_validate_anchor     # executor/hotfix.py：跨版本 AC 存在性校验
           -> _do_complete_hotfix_entry  # executor/hotfix.py：create_branch(fix/N) + 基线继承 + stage.entered(M-DESIGN)
         -> store.append(event)       # triage.prechecked / anchor.validated / branch.created / stage.entered
    -> 终态打印：REJECTED -> stderr 原因 + next + exit 1；AWAIT_HUMAN -> 状态行 + exit 0；ANCHORED -> 状态行 + exit 0
```

**AWAIT_HUMAN 子动作路径（FR-0240-06）**（同为 cmd_hotfix 子动作形态，待实现）：

```
hotfix 子动作: anchor AC-FRXXXX-YY@<ver> [...]   |   hotfix 子动作: feature-route
  （二者均为 trac 顶层 hotfix 子命令的动作参数）
  -> cmd_hotfix（子动作形态）
    -> writer_lock 下校验 active run 处于 awaiting=hotfix_triage
    -> submit_human_result（复用 v0.5 ResultCheckpoint 管线）
       domain_event = human.anchor {mode: manual|feature_route, acs, issue, actor}
    -> manual: validate_anchor -> anchor.validated(source=human) -> complete_hotfix_entry
       feature_route: backlog.recorded {issue, decision: feature_route} -> run.completed(feature_route)
```

**M-DESIGN delta 与后续阶段路径（FR-0243～FR-0246，复用 canonical 序列）**：

```
trac run
  -> cmd_run -> Executor.run_loop（active run = hotfix run）
    -> M-DESIGN: 既有 Archer 三文档派发；hotfix run 的 assignment 增补物化字段
       {anchor_acs, target_version, baseline_doc_paths}（目标版本三件套 + 设计三文档只读路径）
       产物落 .tracks/projects/{target}-hotfix-{issue}/（version_dir 自然解析）
       PRISM_REVIEW verdict 可携 anchor_verdict=overturned -> stage.rolled_back -> M-HOTFIX-TRIAGE/SAGE_TRIAGE
    -> M-TEST: 既有 Shield/collect/RED_CHECK/trace/commit 管线
       delta §8 有 integration/e2e 行 -> Shield WRITE 照常（回归用例绑定跨版本 AC）
       delta §8 全 unit 行 -> 空 Shield 增量放行（increment.declared + 计划级 trace 闭合，§3.6）
    -> M-IMPL: 既有 21 态子状态机；BASELINE digest 增补场景 B 活跃分支 HEAD（stale -> NEEDS_ATTENTION）
       task graph 归 hotfix 项目目录；issue_number = hotfix issue；实现提交落 fix/{issue} 分支
    -> ISLAND_GATE_2 -> stage.exited(M-IMPL) -> run.completed(boundary)
       + 分支恢复 checkout（挂起 feature run 恢复 active）
```

**并发拒绝路径（FR-0242-04，零增量）**：`writer_lock` 被持有时 `LockHeld` → main 捕获 → stderr `runtime lock held by pid <N>` + exit 1，不 append 事件、不推进派发。

**ac_gap/spec_gap 退出路径（FR-0248）**：M-IMPL DIAGNOSE `verdict.failed(check=ac_gap|spec_gap)` → awaiting_human（既有）；`cmd_approve` 扩展接受该 awaiting 形态 → `human.approval` 事件（AC 要求的 Human 决定证据）→ `backlog.recorded {decision: ac_gap|spec_gap}` + `run.completed(terminal_state=ac_gap|spec_gap)`。

**设计缺口回退路径（FR-0247）**：M-TEST stub_gap / M-IMPL stub_gap → `stage.rolled_back(to_stage=M-DESIGN)`（既有路由）；hotfix run 的 M-DESIGN 即 hotfix 自己的 delta 设计闭环（Archer+Prism，无 Human 技术门），闭环后 `stage.entered(M-TEST)` 重新承接。

### 1.2 Required AC closure (ISLAND_GATE_1)

每行对应 Acceptance 中同 requirement 下按文档顺序的一条 required AC；owner 值中的 AC ID 消除同 requirement 多行的歧义。

- **FR-0240** owner=cli/main.py:AC-FR0240-01 surface=trac hotfix <issue> --scenario ... + `trac status` composition=cmd_hotfix→writer_lock→hotfix.requested→run_loop wiring=参数校验→run 建立→PRECHECK→SAGE_TRIAGE→triage.prechecked→status 投影 test=integration:test_hotfix_entry_happy_requested_and_status_substates+e2e:test_hotfix_journey_happy_post_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_cli_entry.py tests/e2e/test_hotfix_journey.py`输出`passed`且events首事件为`hotfix.requested`(含issue/scenario) IF-HOTFIX-001 IF-HOTFIX-002
- **FR-0240** owner=executor/hotfix.py:AC-FR0240-02 surface=trac hotfix + `trac status` composition=PRECHECK→_do_precheck_hotfix wiring=fetch_issue+scenario/活跃分支校验→triage.prechecked(pass)→SAGE_TRIAGE test=integration:test_precheck_pass_reaches_sage_triage_without_agent_dispatch evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_precheck.py`输出`passed`且events含`triage.prechecked`且PRECHECK步无dispatch_agent事件 IF-HOTFIX-003
- **FR-0240** owner=executor/hotfix.py:AC-FR0240-03 surface=trac hotfix composition=PRECHECK→rejected-terminal wiring=not_bug|not_found|scenario_invalid|no_branch|baseline_missing→triage.prechecked(rejected)→run.completed(rejected) test=integration:test_precheck_rejection_matrix_no_branch_nonzero_exit evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_precheck.py`输出`passed`且`git branch --list fix/N`为空且CLI exit非零 IF-HOTFIX-003
- **FR-0240** owner=executor/hotfix.py:AC-FR0240-04 surface=trac hotfix + `trac status` composition=SAGE_TRIAGE→dispatch sage→validate_anchor wiring=anchor 语料派发→锚定 outcome→逐条跨版本校验→anchor.validated→ANCHORED test=integration:test_sage_anchor_validated_reports_anchored_set evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_anchor.py`输出`passed`且`anchor.validated`payload含AC-FRXXXX-YY@ver集合 IF-HOTFIX-004
- **FR-0240** owner=kernel/hotfix.py:AC-FR0240-05 surface=trac hotfix + `trac status` composition=validate_anchor→retry-budget wiring=anchor_invalid→重派≤3→NO_ANCHOR/超限→AWAIT_HUMAN test=integration:test_anchor_invalid_redispatch_and_await_human_no_auto_feature evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_anchor.py`输出`passed`且status含`awaiting=awaiting_human`且无backlog.recorded IF-HOTFIX-002 IF-HOTFIX-004
- **FR-0240** owner=cli/main.py:AC-FR0240-06 surface=`trac status` + trac hotfix anchor + trac hotfix feature-route composition=AWAIT_HUMAN→submit_human_result wiring=human.anchor(manual)→校验→ANCHORED；human.anchor(feature_route)→FEATURE_ROUTE test=integration:test_human_anchor_manual_and_feature_route+e2e:test_hotfix_await_journey_manual_anchor evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_await_human.py tests/e2e/test_hotfix_await_journey.py`输出`passed`且manual路径进入M-DESIGN、feature_route无分支 IF-HOTFIX-001 IF-HOTFIX-002
- **FR-0240** owner=store/store.py:AC-FR0240-07 surface=`trac replay`+`trac report` composition=hotfix事件→append→replay/report投影 wiring=入口事件append-only→replay按seq→report md test=integration:test_hotfix_events_append_only_replay_and_report evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_events.py`输出`passed`且事件表无既有行改写 IF-HOTFIX-002 IF-HOTFIX-006
- **FR-0241** owner=executor/hotfix.py:AC-FR0241-01 surface=trac hotfix + git composition=complete_hotfix_entry→create_branch wiring=场景A base=main HEAD/场景B base=活跃分支 HEAD→branch.created(fix/N) test=integration:test_anchored_creates_isolated_fix_branch_per_scenario evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_branch_baseline.py`输出`passed`且`git log --oneline fix/N ^main`为空（场景A） IF-HOTFIX-005
- **FR-0241** owner=executor/hotfix.py:AC-FR0241-02 surface=`trac status`+`trac replay` composition=complete_hotfix_entry→基线继承 wiring=source approval 记录→跳过需求阶段→AC 集合为跨版本引用 test=integration:test_baseline_inheritance_no_requirement_stage_artifacts evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_branch_baseline.py`输出`passed`且hotfix目录无story/spec/acceptance且无approval.recorded IF-HOTFIX-005
- **FR-0241** owner=kernel/machine.py:AC-FR0241-03 surface=`trac run`+`trac status` composition=ANCHORED→stage.entered(M-DESIGN) wiring=锚定→M-DESIGN进入→trac run续跑（FR-0242接续） test=integration:test_anchored_enters_mdesign_and_run_continues+e2e:test_hotfix_journey_happy_post_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_branch_baseline.py tests/e2e/test_hotfix_journey.py`输出`passed`且events含`stage.entered`(M-DESIGN) IF-HOTFIX-005 IF-HOTFIX-006
- **FR-0241** owner=kernel/hotfix.py:AC-FR0241-04 surface=trac hotfix feature-route + `trac status` + git composition=FEATURE_ROUTE→退出 wiring=human.anchor(feature_route)→backlog.recorded→run.completed(feature_route) test=integration:test_feature_route_exits_without_branch_or_dangling_run evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_feature_route.py`输出`passed`且`git branch --list fix/N`为空且无活跃hotfix run IF-HOTFIX-002 IF-HOTFIX-009
- **FR-0242** owner=cli/main.py:AC-FR0242-01 surface=`trac run`+`trac status` composition=cmd_run→active_run→hotfix续跑 wiring=hotfix建立即active→status含run_id+stage+branch+scenario test=integration:test_active_run_selection_and_status_discriminates_runs+e2e:test_hotfix_journey_happy_post_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_coexist.py tests/e2e/test_hotfix_journey.py`输出`passed`且status输出含branch=fix/N scenario=post-release IF-HOTFIX-006
- **FR-0242** owner=executor/executor.py:AC-FR0242-02 surface=`trac status`+`trac run` composition=hotfix boundary→active切换 wiring=run.completed(boundary)→挂起feature run恢复active→续跑事件 test=integration:test_boundary_restores_suspended_feature_run_as_active evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_coexist.py`输出`passed`且boundary后status的run_id切回feature run IF-HOTFIX-006 IF-HOTFIX-008
- **FR-0242** owner=cli/main.py:AC-FR0242-03 surface=`trac status`+`trac replay`+`trac report` composition=runs投影→suspended清单 wiring=挂起run状态持久化→suspended:行→replay/report按run_id test=integration:test_suspended_run_observable_and_gates_recoverable evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_coexist.py`输出`passed`且status含`suspended:`行且replay可读挂起run IF-HOTFIX-006
- **FR-0242** owner=cli/main.py:AC-FR0242-04 surface=`trac run`（并发第二命令） composition=writer_lock→LockHeld wiring=锁被持→stderr持有者PID→exit 1→无事件推进 test=integration:test_second_concurrent_trac_command_rejected_with_holder_pid evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_coexist.py`输出`passed`且stderr含`runtime lock held by pid`且事件计数不变 IF-HOTFIX-006
- **FR-0243** owner=executor/executor.py:AC-FR0243-01 surface=`trac run`（M-DESIGN）+`trac status` composition=M-DESIGN→dispatch Archer（hotfix物化字段） wiring=anchor_acs+baseline_doc_paths→delta三文档→hotfix项目目录 test=integration:test_mdesign_delta_docs_in_hotfix_dir_with_inherited_contracts evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_design_delta.py`输出`passed`且delta三文档存在于hotfix目录 IF-HOTFIX-005 IF-HOTFIX-006
- **FR-0243** owner=kernel/machine.py:AC-FR0243-02 surface=`trac status`+`trac replay` composition=PRISM_REVIEW→verdict投影 wiring=delta承接锚定AC逐条引用→Prism复核结果可见 test=integration:test_delta_design_carries_anchor_set_and_prism_review evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_design_delta.py`输出`passed`且delta文档含`AC-FRXXXX-YY@<version>`引用 IF-HOTFIX-002 IF-HOTFIX-009
- **FR-0243** owner=kernel/machine.py:AC-FR0243-03 surface=`trac run`+`trac replay` composition=anchor_verdict=overturned→stage.rolled_back wiring=M-DESIGN→M-HOTFIX-TRIAGE/SAGE_TRIAGE重派→新锚定→重新承接 test=integration:test_prism_anchor_overturn_routes_back_to_sage_triage evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_design_delta.py`输出`passed`且`stage.rolled_back`reason=anchor_overturned且M-DESIGN预算未消费 IF-HOTFIX-002 IF-HOTFIX-009
- **FR-0244** owner=kernel/m_test.py:AC-FR0244-01 surface=`trac run`（M-TEST）+events composition=Shield回归→RED_CHECK wiring=回归用例带缺陷基线先RED（unexpected_pass非法）→修复后GREEN test=integration:test_mtest_regression_red_first_then_green evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mtest.py`输出`passed`且RED在GREEN前且无unexpected_pass放行 IF-HOTFIX-007 IF-MTEST-002
- **FR-0244** owner=executor/validate.py:AC-FR0244-02 surface=`trac validate --file test-plan.md` composition=delta §8→层归属校验 wiring=unit行仅hotfix合法→归属声明可校验 test=integration:test_delta_testplan_layer_ownership_validated evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mtest.py`输出`passed`且`trac validate`对层归属错误exit非零 IF-HOTFIX-007 IF-VALIDATE-001
- **FR-0244** owner=checks/trace.py:AC-FR0244-03 surface=`trac check trace` composition=trace扫描→跨版本解析 wiring=`AC-FRXXXX-YY@<version>`标记→所指版本acceptance存在性→闭合校验 test=integration:test_trace_binds_cross_version_ac_without_new_ac evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mtest.py`输出`passed`且`trac check trace`exit 0且锚定AC闭合 IF-HOTFIX-007 IF-TRACE-002
- **FR-0244** owner=kernel/m_test.py:AC-FR0244-04 surface=`trac run`（M-TEST EXIT）+`trac status` composition=空Shield增量→increment.declared→放行 wiring=delta§8全unit→无Shield WRITE事件→计划级trace闭合→EXIT test=integration:test_empty_shield_increment_release_with_unit_closure evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mtest.py`输出`passed`且events含`increment.declared`且无Shield WRITE活动事件 IF-HOTFIX-007 IF-TRACE-002
- **FR-0245** owner=executor/m_impl_runtime.py:AC-FR0245-01 surface=`trac run`（M-IMPL）+git composition=fix分支→RGR handler wiring=task graph按影响切片→R/G提交落fix/{issue}→活跃分支无这些提交 test=integration:test_mimpl_commits_isolated_on_fix_branch evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mimpl.py`输出`passed`且`git log fix/N`含修复提交且活跃分支不含 IF-HOTFIX-008 IF-IMPL-002
- **FR-0245** owner=executor/m_impl_runtime.py:AC-FR0245-02 surface=`trac run`+`trac status` composition=BASELINE→digest（场景B） wiring=活跃分支HEAD进入digest→推进即stale→NEEDS_ATTENTION test=integration:test_scenario_b_baseline_stale_reconcile_needs_attention evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mimpl.py`输出`passed`且活跃分支推进后`baseline.frozen(status=stale)`且status含NEEDS_ATTENTION IF-HOTFIX-008 IF-IMPL-001
- **FR-0246** owner=executor/executor.py:AC-FR0246-01 surface=`trac status`+git composition=ISLAND_GATE_2→boundary wiring=stage.exited(M-IMPL)→run.completed(boundary)→fix分支保留 test=integration:test_boundary_terminal_state_keeps_fix_branch+e2e:test_hotfix_journey_happy_post_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mimpl.py tests/e2e/test_hotfix_journey.py`输出`passed`且status含`terminal=boundary`且分支未删除 IF-HOTFIX-008
- **FR-0246** owner=executor/executor.py:AC-FR0246-02 surface=`trac status`+`trac replay` composition=boundary→无发布副作用 wiring=无merge/patch/tag事件→boundary状态呈现 test=integration:test_no_release_events_after_boundary evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_mimpl.py`输出`passed`且事件流无merge/tag/publish事件且status不报已发布 IF-HOTFIX-008
- **FR-0246** owner=report.py:AC-FR0246-03 surface=`trac replay`+`trac report --format md` composition=事件流→审计渲染 wiring=hotfix.requested→…→run.completed(boundary)全序列→report test=integration:test_replay_and_report_show_full_hotfix_journey+e2e:test_hotfix_journey_happy_post_release evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_events.py tests/e2e/test_hotfix_journey.py`输出`passed`且replay序列含triage→M-DESIGN→M-TEST→M-IMPL→boundary IF-HOTFIX-006 IF-HOTFIX-008
- **FR-0247** owner=kernel/machine.py:AC-FR0247-01 surface=`trac run`+`trac status`+`trac replay` composition=stub_gap→rollback_stage wiring=设计缺口→stage.rolled_back(M-DESIGN)→无human前置门 test=integration:test_design_gap_returns_to_hotfix_mdesign_without_human_gate evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_routes.py`输出`passed`且回退前无`human.review`/`human.approval`事件 IF-HOTFIX-009
- **FR-0247** owner=kernel/machine.py:AC-FR0247-02 surface=`trac replay` composition=M-DESIGN闭环→重新进入 wiring=delta闭环→stage.entered(M-TEST)重新承接→replay可复核 test=integration:test_design_gap_loop_closes_and_reenters_journey evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_routes.py`输出`passed`且闭环后events重现M-TEST派发序列 IF-HOTFIX-009
- **FR-0248** owner=cli/main.py:AC-FR0248-01 surface=`trac approve`+`trac status`+`trac replay` composition=ac_gap/spec_gap→awaiting_human→approve wiring=verdict.failed(ac_gap|spec_gap)→human.approval前置→backlog.recorded→run.completed test=integration:test_ac_gap_spec_gap_exit_with_human_approval_prerequisite evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_routes.py`输出`passed`且退出路由前有`human.approval`且terminal=ac_gap|spec_gap IF-HOTFIX-009
- **NFR-0100** owner=executor/hotfix.py:AC-NFR0100-01 surface=events+dispatch审计 composition=PRECHECK→纯程序判断 wiring=同输入复跑结果一致→无agent派发对应此步 test=integration:test_precheck_deterministic_no_llm_dispatch_records evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_precheck.py`输出`passed`且两次复跑事件同构且无Sage派发 IF-HOTFIX-003
- **NFR-0100** owner=executor/hotfix.py:AC-NFR0100-02 surface=`trac validate`+`trac replay` composition=validate_anchor→校验审计 wiring=不实引用→validate fail非零→重派计数可回溯 test=integration:test_anchor_validation_traceable_with_retry_count evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_anchor.py`输出`passed`且`trac validate`对不实引用exit非零且replay可见计数 IF-HOTFIX-004 IF-VALIDATE-001
- **NFR-0100** owner=kernel/hotfix.py:AC-NFR0100-03 surface=git+`trac status` composition=REJECTED/FEATURE_ROUTE→fail-closed wiring=无分支副作用→锚定不成立交Human→无自动feature路由 test=integration:test_fail_closed_no_branch_and_no_auto_feature_route evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_feature_route.py`输出`passed`且两路径后`git branch --list fix/N`均空 IF-HOTFIX-002 IF-HOTFIX-005
- **NFR-0100** owner=store/store.py:AC-NFR0100-04 surface=events表+`trac status` composition=入口事件→append-only→投影重建 wiring=drop投影表→rebuild→入口状态与原一致 test=integration:test_hotfix_events_append_only_and_projection_rebuild evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_events.py`输出`passed`且rebuild前后status输出一致 IF-HOTFIX-002 IF-HOTFIX-006
- **NFR-0110** owner=cli/main.py:AC-NFR0110-01 surface=`trac status`+`trac replay`+`trac report` composition=runs投影→active+suspended wiring=run_id+stage+branch+scenario→挂起run持久化可观察 test=integration:test_suspended_run_observable_and_gates_recoverable+e2e:test_hotfix_await_journey_manual_anchor evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_coexist.py tests/e2e/test_hotfix_await_journey.py`输出`passed`且status含scenario字段且挂起run可replay IF-HOTFIX-006
- **NFR-0110** owner=executor/executor.py:AC-NFR0110-02 surface=`trac status`+`trac check` composition=boundary→恢复active→门禁可读 wiring=hotfix完成→feature run恢复→check/status读出门禁状态 test=integration:test_boundary_restores_suspended_feature_run_as_active evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_coexist.py`输出`passed`且恢复后feature run门禁状态可由status读出 IF-HOTFIX-006
- **NFR-0110** owner=executor/executor.py:AC-NFR0110-03 surface=`trac status` composition=_recover→事件重放 wiring=重启→pending子状态恢复→不重跑已完成步骤 test=integration:test_crash_recovery_resumes_precise_hotfix_state evidence=`.venv/bin/python -m pytest -q tests/integration/test_hotfix_recovery.py`输出`passed`且重启前后status一致且已完成事件计数不变 IF-HOTFIX-002 IF-HOTFIX-006

## 2. Scaffold 宣言

- `tracks/kernel/hotfix.py` — HOTFIX-TRIAGE 子状态机接口桩：`_decide_hotfix_triage` / 事件 reducer / `parse_anchor_refs` 等纯函数完整签名，行为体仅 `raise NotImplementedError("IF-HOTFIX-002")`（kind: stub）
- `tracks/executor/hotfix.py` — PRECHECK / 锚定校验 / 入口完成 / 目标版本定位接口桩：`precheck_hotfix` / `validate_anchor_refs` / `locate_target_version` / `active_release_branch` / `parse_issue_hints` / `complete_hotfix_entry` 完整签名，行为体仅 `raise NotImplementedError("IF-HOTFIX-003/004/005")`（kind: stub）

> **Prism:** PRISM-ARCH006-B01 [severity=blocker][artifact=architecture.md §2（anchor lines 201-202）+ §0.2（line 29）][关联=IF-HOTFIX-002/003/004，FR-0240-04 锚定校验实现面] Scaffold 宣言成员清单与实际接口桩、interfaces §1d 三方不一致：(a) §2 kernel/hotfix.py 条目声明含 parse_anchor_refs（§0.2 亦把「锚定引用解析纯函数」归入 kernel 新模块），但物理桩 tracks/kernel/hotfix.py 无此函数——interfaces §1d 与物理桩 tracks/executor/hotfix.py 均把 parse_anchor_refs 冻结在 executor 侧（IF-HOTFIX-004，modules 列实现者为 executor/hotfix.py）；(b) §2 executor/hotfix.py 条目声明含 active_release_branch，但物理桩与 interfaces §1d 均无该独立函数（P-3 活跃分支定位已作为参数语义消化在 precheck_hotfix 签名内）。影响：Scaffold 宣言是 Devon「不改冻结合同填桩」的清单与 ISLAND/scaffold checkpoint 的核对基线——两份文档对 parse_anchor_refs 归属矛盾，Devon 补 kernel 桩时被迫在 kernel/executor 间做模块归属选择（M-IMPL 不得再做设计选择），Shield 契约测试绑定 IF token 时同样面对矛盾文档。预期修订：Archer 修订 architecture §0.2 与 §2 两个条目的成员清单，与 interfaces §1d 及物理桩三方逐项一致——将 parse_anchor_refs 从 kernel 条目移除并更正 §0.2 的「锚定引用解析纯函数」归述（或如确需 kernel 归属，须同步修订 interfaces §1d 实现模块列与物理桩，二选一保持一致）；active_release_branch 同理（从宣言删除，或补入 interfaces §1d 与桩）。其余 Scaffold 维度已核验通过：文件级宣言与 cd3a26f 实际交付逐项一致（恰两桩+三文档），桩体纯签名+NotImplementedError 无业务行为。

本节只声明 M-DESIGN 物理脚手架。既有配置（`pyproject.toml`、`.flake8`、`.githooks/pre-commit`、`.github/workflows/ci.yml`、`.tracks/projects/project.toml`）为 v0.4/v0.5 已交付的真实合同，v0.6 不修改不重建，故不列入。`machine.py` / `executor.py` / `events.py` / `capabilities.py` / `trace.py` / `github.py` / `fake.py` 等既有文件由 Devon 在实现 task 中按合同增量扩展，不是 scaffold。Shield 的测试交付物（`tests/integration/test_hotfix_*.py`、`tests/e2e/test_hotfix_*.py`、`tests/e2e_live/test_hotfix_live.py`）不在宣言中。`tests/ground_truth/` 不创建（test-plan §3 判定不适用）。两个接口桩在被 M-IMPL 接线任务 import 之前是 reach 孤岛——预期中间态，语义与闭合保证见 §5.2「M-DESIGN 接口桩的 reach 孤岛窗口」。

## 3. 技术选型

### 3.1 issue 读取通道（effects/github.py，IF-HOTFIX-003）

- **新增 `fetch_issue(issue_number)`**：读通道与既有写通道（create_issue/add_to_project）并列。`GithubBackend.fetch_issue` 走 `GET /repos/{repo}/issues/{n}`（urllib，沿用既有 `_request` 风格但 method=GET，不新建 HTTP 依赖）；`FakeIssueBackend.fetch_issue` 读 `.tracks/runtime/host-issues.json` 种子文件（interfaces §3b schema）。
- **type=bug 判定**：REST issue 的 `labels` 含 `bug`（宿主 repo 的 bug issue template 惯例配 label；GitHub REST 无独立 type 字段）。`404` → `issue_not_found`；网络/鉴权失败 → `issue_fetch_failed`（fail-closed，REJECTED 可重试路径，见 §3.2）。
- **可选字段**：issue body 中 bug template 的「版本 / 对应 FR/NFR」字段由确定性正则解析（`parse_issue_hints`），仅作 Sage 辅助语料，PRECHECK 不因缺失而 fail（SPEC 范围排除明确）。
- **取舍**：不引入 GraphQL issue type（增加 token 面与脆弱性）；不引入 PyGithub（保持零运行时依赖）。代价：依赖宿主 repo 约定 `bug` label——写入 interfaces 合同并在 live 探针（§4.4）中校验该约定。

### 3.2 PRECHECK 确定性规则（executor/hotfix.py，IF-HOTFIX-003）

PRECHECK 是纯程序判断（无 LLM，AC-NFR0100-01），规则封闭（失败原因封闭集见 interfaces §1c）：

1. **P-1 issue 定位**：`fetch_issue` 成功且 labels 含 `bug`；失败/非 bug → REJECTED(`issue_not_found` | `issue_fetch_failed` | `not_bug`)。
2. **P-2 scenario 合法性**：`--scenario` 必填且取值 ∈ {`post-release`, `dev`}；缺省由 CLI 层先拦截（stderr 询问提示 + exit 2，不建 run、不推断——AC-FR0240-01）；值非法 → REJECTED(`scenario_invalid`)。
3. **P-3 活跃 release 分支**（仅 dev 场景）：活跃 release 分支 = 活跃 run 的 branch（若为 `releases/*`），否则当前 HEAD 分支（若为 `releases/*`）；两者皆不可定位 → REJECTED(`no_active_release_branch`)。
4. **P-4 目标版本定位**：dev 场景 target = 活跃 release 分支名解析的版本（`releases/v0.6` → `v0.6`）；post-release 场景 target = `.tracks/projects/` 下已批准基线的最高版本（数值组比较；「已批准」= events 表存在该 version 的 `approval.recorded` 且 spec.md/acceptance.md 文件在位）。不可定位 → REJECTED(`baseline_not_locatable`)，payload 标注「补全后重试」。
5. **P-5 分支占用**：`fix/{issue}` 已存在 → REJECTED(`fix_branch_exists`)（防半建 run 串写）。

取舍：post-release 的目标版本取「最高已批准版本」而非 issue 可选字段直采——字段「不完全采信」（SPEC/flow.md §16.4），且确定性规则必须无歧义；issue 字段仅作为 Sage 语料辅助。代价：多版本并存发布线（非本版场景）时需要后续引入显式声明——记录为 §5 风险。

### 3.3 Sage 锚定与程序校验（IF-HOTFIX-004）

- **语料物化**：SAGE_TRIAGE 派发 assignment 携带 `issue`（title/body/labels）、`scenario`、`target_version`、`anchor_hints`（P-1 解析的可选字段）、`corpus`（`.tracks/projects/*/spec.md` + `acceptance.md` 绝对路径清单，目标版本及全部历史版本）。物化字段集扩展进 FR-0190 dispatch 完整性合同（interfaces §3a）。
- **锚定 outcome 合同**：`acs: ["AC-FRXXXX-YY@<version>", ...]` + 逐条 rationale/source，或 `no_anchor: true` + `searched_versions` + `corpus_digests`。引用语法封闭（interfaces §1e）。
- **程序校验**：`validate_anchor_refs` 逐条把 `@<version>` 解析到 `.tracks/projects/<version>/acceptance.md`，AC id 必须命中该文件 `### AC-FRXXXX-YY` 标题（确定性文本扫描，复用 trace 扫描器的 AC 标题正则）。任何一条不实 → `verdict.failed(check="anchor_invalid")` → 重派（≤3）；NO_ANCHOR / 超限 → AWAIT_HUMAN。
- **fake 通道**：`TRAC_FAKE_SIMULATE` key `sage:SAGE_TRIAGE`，token ∈ {`anchor`（默认，取目标 acceptance 首个 AC 标题作确定性锚定）, `no_anchor`, `bad_anchor`（引用不实）}；`|` 序列语义不变。
- **取舍**：锚定校验只做「存在性」不做「语义正确性」——语义归 Sage、复核归 Prism（anchor_verdict），程序不越权。代价：Sage 可锚定存在但不相关的 AC；由 Prism M-DESIGN 评审推翻路径收口（FR-0243-03）。

### 3.4 fix/{issue} 分支、基线继承与 hotfix 项目目录（IF-HOTFIX-005）

- **分支**：`create_branch` 复用（既有命令 + reconcile），params `{branch_name: "fix/{issue}", base: "main" | "<active release branch>"}`，随后 checkout fix/{issue}（Runtime 唯一 branch authority；分支创建/HEAD 推进均有 branch.created 等事件审计，AC-FR0241-01）。
- **基线继承（source approval）**：`complete_hotfix_entry` 在 events 记录 `baseline.inherited` payload（target_version、baseline digest 指纹 = 目标版本三件套 + 设计三文档内容身份、anchor_acs）——不复制文件、不重新批准（AC-FR0241-02 的 observable：hotfix 目录无 story/spec/acceptance、无本 run 自有 `approval.recorded`）。M-DESIGN 派发的只读基线路径来自该记录。
- **hotfix 项目目录**：`.tracks/projects/{target}-hotfix-{issue}/`（version_dir 自然解析，无新路径函数）。delta 三文档、tasks.json/tasks.md 落此目录。
- **取舍**：不复制基线文件到 hotfix 目录（快照会随目标版本演进失真且产生双真相）；基线身份以 digest 记录 + 原路径只读引用。代价：目标版本目录被删时 hotfix 历史不可复核——接受（.tracks/projects 是 git-tracked，删除即仓库历史事件）。

### 3.5 run 并存与单一 active（IF-HOTFIX-006，零 schema 变更）

- `active_run()` 语义不变（最新非 completed），hotfix 建立即最新即 active；boundary 后自然回退到挂起 feature run（§1.0.4）。不加 `is_active` 列——排序语义已封闭，schema 变更会破坏既有投影重建。
- `run.completed` 的 hotfix 处理器附带分支恢复 checkout（restored_active_run/restored_branch 进 payload）。取舍：Runtime 主动切回而非留给操作者——否则场景 A 后操作者手工切分支的失误会破坏 feature run 工作区；Runtime 是唯一 branch authority，切回是其职责。

### 3.6 M-TEST 空 Shield 增量放行（IF-HOTFIX-007，关键设计决策）

> **Archer [RESOLVED]:** ARCH-DEC-001 [advisory，请 Prism 在 M-DESIGN 评审重点复核] 空 Shield 增量放行的闭合语义（AC-FR0244-04 / interfaces §1f / 本节）。时序事实：Human 裁定 T-003 把 unit 层回归用例的补写放在 M-IMPL RED 阶段（Devon），而 AC-FR0244-04 要求 M-TEST 放行凭据是『trac check trace 退出码 0 且 unit 层回归用例闭合所有 required AC trace』——M-TEST EXIT 时 unit 回归文件尚不存在，文件级闭合在该时点不可达（被锚定的 AC 恰恰可能是『有 AC 而此前没有检验』，既有测试无法兜底）。本设计的机械实现：M-TEST EXIT 的 trace 闭合在 hotfix 空 Shield 分支下以 delta §8 已声明的 unit 行为单位（计划级闭合，事件流含 increment.declared 才启用；feature 版本 trace 语义逐字节不变）；文件级闭环后移到 M-IMPL 的 TASK_REVIEW（声明-文件一致性，fail-closed check=trace_mismatch）与 ISLAND_GATE_2（全量文件级 trace）双门禁补偿。请 Prism 复核：(1) 计划级闭合窗口（声明了但 M-IMPL 未物化）由双门禁补偿是否充分；(2) 『unit 层回归用例闭合 required AC trace』在 M-TEST 时点解释为『delta §8 声明的 unit 行闭合』是否符合 AC 意图；(3) 若认为需要 spec 澄清，请给出可定位的 AC 修订建议（这会走 Spec 修订阻塞而非设计吸收）。
>> **Archer:** ARCH-DEC-001 收敛说明：(1) 本线程的三个复核问题已全部物化进文档正文——计划级闭合的机械实现与『请 Prism 重点复核』标注在 §3.6，合同在 interfaces §1f / IF-HOTFIX-007（increment.declared 仅 hotfix run+事件在流才启用；feature 版本 trace 语义逐字节不变；M-IMPL TASK_REVIEW check=trace_mismatch + ISLAND_GATE_2 全量文件级 trace 双门禁补偿），取舍与风险在 §5.1/§5.2。resolve 本线程不撤销复核请求：PRISM_REVIEW 按 assignment 读文档，§3.6 正文即复核焦点；若 Prism 有异议，按协议以 Prism 名义 start 新锚定线程（revise 无锚定线程会被判 revise_without_findings），需要 spec 澄清则给出可定位 AC 修订建议（走 Spec 修订阻塞）。(2) 按 DRAFT 阶段门禁纪律（作者发起线程须 resolved 派发方可结束），作为发起人现在 resolve 本 advisory 线程；设计决策维持 §3.6 所述不变。

Human 裁定 T-003 允许 Archer 把回归用例全部划归 unit 层（Devon 在 M-IMPL RED 阶段补写）。因此空 Shield 增量分支下，M-TEST EXIT 时 unit 层回归**文件**尚不存在，而 AC-FR0244-04 要求放行凭据是「`trac check trace` 退出码 0 且 unit 层回归用例闭合所有 required AC trace」。机械实现（本设计决策，请 Prism 重点复核）：

- **delta test-plan §8 扩展（仅 hotfix delta plan 合法）**：§8 行的 layer 词表扩展 `unit`（feature 版本的 §8 仍只允许 integration/e2e——本 v0.6 计划自身即如此）；每行仍是机器可读的回归用例声明（AC@version + layer + IF 归属 + 建议 test 名）。
- **放行判定（hotfix run 的 M-TEST EXIT）**：§8 无任何 integration/e2e 行且 ≥1 unit 行 → Shield 增量为空（不派发 Shield WRITE，无 test.committed）；executor 产出 `increment.declared {shield: "empty", unit_rows, trace_status}` 事件（放行依据入事件流）；`trac check trace` 在 hotfix scope 下（锚定 AC 集合）以「§8 已声明的 unit 行 + 既有文件标记」闭合校验，exit 0 才放行 EXIT。
- **文件级闭环后移到 M-IMPL 门禁**：Devon RED 阶段必须物化 delta §8 声明的 unit 回归用例（task graph 携带声明行）；TASK_REVIEW 的 AC trace 校验与 ISLAND_GATE_2 的全量 `trac check trace`（文件级）验证声明与文件一致——声明了不写、或写了不在声明内，均 fail closed。
- **防退化**：`trac check trace` 的 hotfix 计划级闭合只在「run 为 hotfix 且 increment.declared 事件在流」时启用；feature 版本的 trace 语义逐字节不变。

取舍：计划级闭合弱于文件级（存在「声明了但 M-IMPL 未物化」的窗口），用 M-IMPL 双门禁补偿；这是把「RED 用例在 M-IMPL 才存在」的裁定顺序如实机械化的最小代价。

### 3.7 M-IMPL 场景 B stale reconcile（IF-HOTFIX-008）

- hotfix M-IMPL BASELINE 的 digest 输入在既有集合（三件套 + 设计三文档 + 冻结测试资产 + contracts + branch + approval）之上追加：场景 B 的活跃 release 分支 HEAD SHA。活跃分支推进 → digest 不匹配 → `baseline.frozen(status="stale")` → NEEDS_ATTENTION（既有路由，`trac status` 可观察 `needs_attention`）——这就是 flow.md §16.3.3 要求保留的 reconcile 与冲突路径（merge 本身不执行，FR-0246）。
- 隔离性（AC-FR0245-01）：hotfix run 的 branch 即 fix/{issue}，RGR 的 R/G/refactor 提交天然落在该分支；断言面是 git（fix/N 有修复提交、活跃分支与 feature 工作区无）。task graph 的 scope 白名单沿用目标版本 layout（project.toml [layout] 不变）。
- 取舍：不为场景 B 引入独立 merge-base 计算（本版不执行 merge）；stale 检测用分支 HEAD 等值比较。代价：活跃分支 rebase 历史改写时 HEAD 等值仍可能误报 stale——fail-closed 方向可接受。

### 3.8 缺口与退出路由（IF-HOTFIX-009）

- **anchor 推翻**（FR-0243-03）：`prism.verdict` payload 增 `anchor_verdict: "upheld"|"overturned"`（缺省 upheld，向后兼容）；overturned → `stage.rolled_back(M-DESIGN → M-HOTFIX-TRIAGE)` + SAGE_TRIAGE 重派（SM-01.6 预算）。fake token：`prism:PRISM_REVIEW=anchor_overturned`。
- **设计缺口**（FR-0247）：M-TEST/M-IMPL `stub_gap` → 既有 `rollback_stage(M-DESIGN)`；hotfix run 的 M-DESIGN 即 delta 闭环，全程无 `human.review`/`human.approval` 前置（与 FR-0248 的区分断言点）。
- **ac_gap/spec_gap**（FR-0248）：DIAGNOSE 路由 → awaiting_human；`cmd_approve` 扩展接受该 awaiting 形态（`trac approve --actor NAME`），落 `human.approval`（AC 要求的决定证据）→ `backlog.recorded {decision: ac_gap|spec_gap, issue}` + `run.completed(terminal_state=ac_gap|spec_gap)`，不建后续提交。
- 取舍：复用 `trac approve` 而非新增 trac hotfix exit 子命令——SPEC 范围排除限定「新增顶层命令仅 trac hotfix」，approve 是既有命令的 awaiting 形态扩展，且 AC 指名 human.approval 为合法证据。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

路径与内容继承 v0.5（v0.6 无测试目录变更）：integration/e2e 均为 pytest，paths 分别为 `tests/integration/` 与 `tests/e2e/`，collect/run 命令使用宿主 `.venv/bin/python -m pytest`，cwd 为仓库根。文件已在库中且被当前 Runtime 消费，本轮不修改（内容与 ARCH-005 §4.1 逐字一致）。`tests/e2e_live/` 继续为独立 opt-in live 通道，不入默认套件。

### 4.2 质量守卫栈（八类，全部继承既有实现）

| # | 守卫 | 工具（pinned） | 配置位置 | 阈值 | 执行点 |
|:---|:---|:---|:---|:---|:---|
| 1 | lint + format | ruff==0.16.0 | `pyproject.toml` `[tool.ruff]`/`[tool.ruff.lint]` | line-length=100；select E,F,W,I,B,UP,SIM,C4 | pre-commit + CI `lint` |
| 2 | 认知复杂度 | flake8==7.3.0 + flake8-cognitive-complexity==0.1.0 | `.flake8` | CCR001 ≤ 15（tracks；tests 豁免） | pre-commit + CI `lint` |
| 3 | 重复度 | pylint==4.0.6 | `pyproject.toml` `[tool.pylint.similarities]` | R0801 min-similarity-lines=5（tracks only） | pre-commit（`--exit-zero`）+ CI `lint` |
| 4 | 文件长度 | pylint | `[tool.pylint.format]` | C0302 max-module-lines=1200 | pre-commit + CI `lint` |
| 5 | 方法长度/局部变量 | pylint | `[tool.pylint.design]` | R0915 max-statements=50；R0914 max-locals=15（tracks；tests 豁免） | pre-commit + CI `lint` |
| 6 | 覆盖率门槛 | coverage==7.15.2 + pytest==9.1.1 | `pyproject.toml` `[tool.coverage.*]` | CI `coverage report --fail-under=95` | CI `coverage` |
| 7 | 钩子运行器 | git hooks（hooksPath=.githooks） | `.githooks/pre-commit` | 按序执行 ruff/flake8/pylint（分层政策不变） | 本地提交 |
| 8 | CI required checks | GitHub Actions | `.github/workflows/ci.yml` | `lint/coverage/test/deliverables/trace/reach` 全 required；`release-evidence` 为 milestone 硬门禁 | merge 门禁 |

安装命令（不变；执行属 Runtime 生效副作用）：`python -m venv .venv && .venv/bin/pip install -e '.[dev]' && git config core.hooksPath .githooks`。新模块 `kernel/hotfix.py`、`executor/hotfix.py` 遵守同一栈（目标 ≤500 行/模块，同 doc_comment.py 先例）。

### 4.3 CI 合同（增量；workflow 变更为待实现 Devon foundation task）

stable required checks 不变：`lint + coverage + test + deliverables + trace + reach`（routine DAG），milestone `release-evidence`（tag/release，needs routine 全部）。

v0.6 增量（**待实现**，Devon foundation task 修改 `.github/workflows/ci.yml`，Runtime 负责绑定生效）：

1. `live-opencode`（weekly/manual，非 required）增补步骤：`pytest -q -rs tests/e2e_live/test_hotfix_live.py`——真实 GitHub 读取探针（防腐烂）；缺凭据输出 `LIVE_SKIPPED: missing <NAME>`。
2. `release-evidence`（milestone 硬门禁）增补同一探针步骤：缺凭据 fail（不 skip），保证发布候选上 hotfix 读通道与 `bug` label 约定被真实验证。

### 4.4 integration/e2e 测试基础设施

- **fake 通道**（deterministic，CI 必跑）：`host_repo` fixture 之上新增 hotfix 语料准备——测试把宿主 issue 种子写入 `.tracks/runtime/host-issues.json`（fake fetch_issue 数据源）；目标版本基线由 fixture 走最小 feature journey 或直接投影事件构造（`approval.recorded` + 三件套文件）；`TRAC_FAKE_SIMULATE` 注入 `sage:SAGE_TRIAGE` / `prism:PRISM_REVIEW` 分支。
- **L2 contract sim**：`fake_opencode` 既有 fixture 覆盖 Sage/Devon/Prism 派发物化与审计（hotfix 物化字段增量并入）。
- **live 通道**（真实 GitHub 读取，唯一新 live 面）：`tests/e2e_live/test_hotfix_live.py`——probe `GITHUB_TOKEN` + `TRAC_GITHUB_REPO` + `TRAC_LIVE_ISSUE`（真实 bug issue 号）；缺失 → `LIVE_SKIPPED: missing <NAME>` + exit 0 skip；齐备 → 真实 fetch + PRECHECK 通过断言（≤1 次 API 读，smoke 语义）。fake 与 live 的 AC 不重叠（fake 覆盖事件流/路由矩阵；live 只覆盖真实 API 读取与 label 约定）。
- **e2e 范围**：仅 happy path——场景 A 全旅程（`test_hotfix_journey.py`）与 awaiting→manual anchor→继续→boundary 旅程（`test_hotfix_await_journey.py`）；全部错误/边界矩阵归 integration。

### 4.5 release version / build / artifact

继承 ARCH-005 §4.4/§4.5：wheel 构建、package-data（agents/skills）不变；v0.6 无新增 package data（两个新 .py 模块随包分发）。发布语义本版不执行（FR-0246）。

### 4.6 发布恢复

继承 v0.1–v0.5：append-only 事件 + 投影可重建；hotfix 全程事件 append-only；三个新 command kind 各有幂等 reconcile（§1.0.4）。R ref 不可变与 G trailers 语义在 fix/{issue} 分支上不变。

## 5. 有意识简化与风险

### 5.1 有意识简化

- **REJECTED run 行保留作审计**：入口即建 run（SM-01），REJECTED 后立即 `run.completed(rejected)`。「不建立 hotfix run」的合同解释为无活跃 run + 无分支副作用（AC observable 奇点），事件与 run 行是审计必需（E-01 输出即含 run id）。代价：runs 表多一条 completed 行；重试是新 run，无状态串写。
- **trac hotfix 子动作而非新顶层命令**：AWAIT_HUMAN 的 Human 动作是 trac hotfix anchor / trac hotfix feature-route（同一命令的子动作形态，待实现），严格满足「新增顶层命令仅 trac hotfix」的范围排除。ac_gap/spec_gap 退出复用 `trac approve` 扩展（§3.8）。
- **目标版本定位规则确定性优先**：post-release = 最高已批准版本；dev = 活跃 release 分支版本；issue 可选字段仅作 Sage 辅助。多活跃发布线的显式声明推迟到真实需要时（spec 变更），不在本版猜补。
- **空 Shield 增量的计划级闭合**（§3.6，关键决策）：M-TEST EXIT 的 trace 闭合在 hotfix 空 Shield 分支下以 delta §8 声明为单位，文件级闭环由 M-IMPL TASK_REVIEW + ISLAND_GATE_2 补偿。这是 Human 裁定「unit 层归 Devon 在 M-IMPL RED 阶段补写」与 AC-FR0244-04「M-TEST 凭 trace 闭合放行」两个时序事实的唯一定序方式。
- **场景 B stale 用分支 HEAD 等值**：不做 merge-base/补丁级冲突计算（merge 本版不执行）；stale → NEEDS_ATTENTION 即保留的冲突路径。
- **ground truth 不适用**：v0.6 是状态机/路由/事件行为，无算法正确性需独立重算（目标版本定位/锚定存在性是简单规则，测试数据即真值来源，test-plan §3.1 第 3 行）。不创建 `tests/ground_truth/`。

### 5.2 风险

- **`bug` label 约定依赖宿主 repo**：PRECHECK 的 type=bug 判定依赖宿主 bug template 配 label。缓解：约定写入 interfaces 合同 + live 探针在 weekly/milestone 真实验证（§4.3）；PRECHECK 失败原因封闭可诊断。
- **Sage 锚定语义漂移**（锚定存在但不相关）：程序校验只做存在性。缓解：Prism M-DESIGN 评审 anchor_verdict 推翻路径 + 回 SAGE_TRIAGE 重派；重派超限 AWAIT_HUMAN 人工兜底。
- **并存 run 的分支切换摩擦**：hotfix 期间工作树在 fix/{issue}，feature run 挂起；boundary 后 Runtime 自动切回（§3.5）。风险在 operator 手工 checkout 绕过——缓解：branch 操作全部经 Runtime 事件审计，绕过操作在下一门禁 stale 检测中暴露。
- **空 Shield 增量窗口**（§3.6 的「声明-物化」窗口）：M-TEST 放行后 M-IMPL 未物化声明用例。缓解：TASK_REVIEW 声明-文件一致性校验 fail closed；ISLAND_GATE_2 全量文件级 trace 兜底。
- **hotfix run 与既有 e2e 前缀稳定性**：`trac start`/`trac run` 既有事件前缀不受 hotfix 影响（新命令新 run）；既有测试更新面小（test-plan §10）。
- **M-DESIGN 接口桩的 reach 孤岛窗口**：Scaffold 宣言的两个接口桩（`kernel/hotfix.py`、`executor/hotfix.py`）在被 `machine.py`/`executor.py` 接线（Devon M-IMPL task）之前是 import 图孤岛——`trac check reach` 在 M-DESIGN scaffold checkpoint 到 M-IMPL 接线完成之间对这两个模块报 island（exit 1）。这是接口桩模式的预期中间态而非设计缺陷（v0.5 先例：`live_evidence.py`/`release_evidence.py`/`doc_comment.py` 桩在 e5ed0da 入库时同样零引用，经历了同一窗口）。缓解与闭合：(a) Runtime 不执行 git push，pre-commit hook 只跑 ruff/flake8/pylint 不跑 reach——scaffold checkpoint 提交与阶段推进不被阻塞；(b) M-IMPL task graph 把接线任务排在首批 scope，ISLAND_GATE_2 全量 reach fail-closed 兜底（接线不完整则 M-IMPL 不退出）；(c) 窗口期内操作者手动 push `releases/v0.6` 会看到 CI `reach` 红——发布候选以 milestone `release-evidence`（needs routine 全部含 reach）为硬门禁，中间红不构成发布证据（test-plan §7「中间态不是通过证据」同型惯例）。显式不用 FR-0100 legacy baseline 豁免：该合同只冻结采纳时刻存量，豁免新桩会永久掩盖真实孤岛腐烂。
- **live GitHub 读取的 rate limit/网络抖动**：fake 通道为主验证面；live 仅 smoke（≤1 读）且独立通道隔离（§4.4）。
