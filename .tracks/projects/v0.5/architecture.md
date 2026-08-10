---
architecture_id: ARCH-005
spec_ref: SPEC-005
created: 2026-08-09
status: draft
sha:
---

# v0.5 — 架构

本文是 ARCH-004（v0.4）的增量延伸。v0.1 的内核机制（事件溯源、单一生产路径、四增长轴分包、单写者锁、per-kind reconcile）、v0.2 的物化合同与 audit、v0.3 的 M-DESIGN Archer/Prism、v0.4 的 M-TEST 与 checks/ 增长轴全部保持不变；v0.5 在既有包内新增 M-IMPL 控制流与 Devon agent 接入，不推翻布局。凡未提及者，一律继承 ARCH-004。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-004 不变项

- 唯一工作流生产路径 `cli -> kernel.runtime.run_loop -> effects.executor.execute`：不变。M-IMPL 的状态变更命令仍只此一路。
- 四增长轴分包 kernel/workflows/effects/checks + discuss 旁路：不变。v0.5 在 `kernel/`（M-IMPL StageDef + 控制流）、`effects/`（Devon 接入）、`executor/`（新 handler + 新模块）、`cli/`（trac retry）上生长。
- 事件溯源 SQLite append-only + 投影可重建：机制不变。M-IMPL 全程事件追加同一 events 表（NFR-0020）。
- 单写者锁 / 取消协议 / per-kind reconcile：不变。Devon/Prism/Shield dispatch 复用 dispatch_agent reconcile + opencode 物化清理。
- 纯核心 `project`/`decide` 零 I/O：不变。M-IMPL 控制流分支在 decide()/project() 内仍为纯函数（NFR-0010）；collection 复跑、task graph 解析、worktree 操作、git commit/ref 创建、quality gate 执行归 executor。
- FakeBackend 确定性函数 + OpencodeBackend：不变。Devon 作为第七个 opencode agent 接入（同构模式）。
- inline-discussion 旁路（discuss/）：不变。M-IMPL 的 Prism revise 仍经 `trac discuss` 锚定线程。
- 模板 + 校验（templating.py + executor/validate.py）：不变既有行为；v0.5 扩展 tasks.json 校验（DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性）与 dispatch 物化校验。
- checks/ 增长轴（trace.py + reach.py）：不变。M-IMPL ISLAND_GATE_2 消费 `trac check reach`（工具本体由 v0.4 交付）。
- 质量守卫栈八类 + pre-commit + CI required checks：不变。pre-commit hook 已在 v0.4 通过分别执行命令实现 BS-11 测试代码分层；v0.5 在 `executor/quality_gate.py` 程序执行中继承同一分层政策。
- IF- 标识注册表（interfaces.md §5）：v0.4 已建立的 8 个 IF- 标识（IF-MTEST-001/002、IF-SHIELD-001、IF-TRACE-001/002、IF-REACH-001/002、IF-VALIDATE-001）不可变、不可复用；v0.5 §5 列出 cross-reference 条目供 validator 解析（定义仍以 IF-004 §5 为准），并增补 IF-IMPL-001~007 与 IF-DEVON-001 共 8 个新标识。

### 0.2 v0.5 变更项

- `_NEXT_STAGE` 增补 `"M-TEST": "M-IMPL"` 与 `"M-IMPL": "M-VERIFY"`：M-TEST EXIT 后进入 M-IMPL；M-IMPL EXIT 后因 M-VERIFY 未注册落在 boundary。boundary 从 M-TEST→M-IMPL 移至 M-IMPL→M-VERIFY。
- `machine.py` 新增 M-IMPL StageDef + `_decide_m_impl` 显式控制流（21 个子状态，类似 `_decide_m_test` 但更复杂）。
- `kernel/events.py` EVENT_TYPES 追加 M-IMPL 事件（`baseline.frozen` / `taskgraph.committed` / `task.started` / `writelock.granted` / `writelock.released` / `red.checkpointed` / `green.committed` / `refactor.committed` / `refactor.no_change` / `task.completed`）；COMMAND_KINDS 追加 M-IMPL 命令。
- 新增 `executor/taskgraph.py`：tasks.json 解析 + DAG 无环 / scope 边界不重叠 / required AC 覆盖闭合 / IF- 有效性 / issue number 有效性校验（纯函数）。
- 新增 `executor/rgr.py`：RGR git 操作——R ref 创建（compare-and-set）、G commit 创建（parent=B + trailers）、lineage 证明。
- 新增 `executor/worktree.py`：三 worktree 方案——Devon candidate worktree / gate worktree / test-authority worktree 管理。
- 新增 `executor/quality_gate.py`：质量门禁分层执行——生产代码完整四段、测试代码仅 R0801+C0302。
- `executor/executor.py` 增补 M-IMPL handler（baseline / taskgraph / island / dispatch / red checkpoint / green commit / refactor gate / task review / island gate 2 / shield fix / diagnose）。
- `executor/validate.py` 扩展 `trac validate --file tasks.json` 校验 DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性。
- `effects/opencode.py` AGENT_NAME 增补 `"devon": "Devon"`；Devon 写范围审计（manifest 白名单越界检测 + git 回滚）。
- `tracks/deliverables.py` AGENT_DELIVERABLES 增补 Devon.md（version + IQ frontmatter 校验）；DELIVERABLES 增补 tracks-prism-impl skill。
- `tracks/agents/Devon.md` 移除 permission 块（D-26：agent 定义 harness 无关、隔离由时态 worktree + manifest 承担）。
- `tracks/cli/main.py` 增补 `trac retry` 命令（human.retry 事件 + escalation gate 清除 + 新 attempt 预算）。
- `tracks/project.py` 强化 canonical `.tracks/project/project.toml` 路径约束（唯一允许的 `.tracks/**` project contract 路径）。
- 质量守卫配置不变（pre-commit hook 已在 v0.4 通过分别执行命令实现 BS-11 分层）；v0.5 在 `executor/quality_gate.py` 程序执行中继承同一分层政策（按 changed_paths 分类后分别执行检查集）。

## 1. 模块边界

### 1.1 增长轴归属

v0.5 新代码落在既有增长轴上，不引入结构性返工：

| # | 包 | v0.5 增长 | 触发 | IF- 标识 |
|:---|:---|:---|:---|:---|
| 1 | `kernel/` | M-IMPL StageDef + `_decide_m_impl` + 新 reducer + State 字段 + 事件/命令 | M-IMPL 子状态机（FR-0010） | IF-IMPL-001 |
| 2 | `executor/` | executor.py M-IMPL handler + 新模块 taskgraph.py / rgr.py / worktree.py / quality_gate.py | M-IMPL 副作用（FR-0020~0160） | IF-IMPL-002~007 |
| 3 | `effects/` | Devon 接入（AGENT_NAME + deliverables + Auditor manifest 越界审计） | Devon agent（FR-0170） | IF-DEVON-001 |
| 4 | `cli/` | cmd_retry 扩展 | NFR-0030 trac retry | IF-IMPL-001 |
| 5 | `executor/validate.py` | tasks.json DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性校验 | FR-0180 | IF-IMPL-003 |
| 6 | `agents/` | Devon.md 移除 permission 块 + 加入 deliverables | FR-0170 | IF-DEVON-001 |
| 7 | `skills/` | tracks-prism-impl 已存在（v0.4 创建） | D-29 M-IMPL 判据包 | — |
| 8 | `project.py` | canonical 路径约束强化 | FR-0190 第 2 项 | IF-IMPL-002 |

新增四个 executor 子模块（taskgraph.py / rgr.py / worktree.py / quality_gate.py）不是新增长轴——它们是 executor 增长轴内的功能模块，被 executor.py 的 M-IMPL handler 调用，不直接被 cli 或 kernel 引用。分层约束：这四个模块可执行 git/subprocess/文件系统操作（副作用），属于 executor 副作用边界，不被 kernel 的 decide()/project() 引用。

### 1.2 M-IMPL 控制流（kernel/machine.py）

M-IMPL 子状态机（SM-01，21 个子状态）与既有 DRAFT/REVIEW/EXIT 模式差异极大，`decide()` 为 M-IMPL 增加显式控制流分支 `_decide_m_impl(s, sub)`。该控制流仍维持 kernel 纯函数边界（NFR-0010）。

`_decide_m_impl` 按 substate 分发：

- `BASELINE`：产出 `recompute_baseline` command。executor 重算 baseline digest + 冻结测试路径集，产出 `baseline.frozen`。decide 路由：current → PLANNING；缺失/stale/冲突 → NEEDS_ATTENTION。
- `NEEDS_ATTENTION`：产出 `rollback_stage` command（return upstream）或等待 reconcile（`baseline.reconciled` 事件 → BASELINE）。
- `PLANNING`：产出 `dispatch_agent(role=archer, substate=PLANNING)` command，携带 tasks.json 模板 + 设计文档路径。Archer outcome 后由 executor 产出 `outcome.received` + `taskgraph.committed`。decide 路由：validate pass → ISLAND_GATE_1；validate fail → 重派 Archer（≤3 升级）。
- `ISLAND_GATE_1`：产出 `check_island` command。executor 执行六元组复核，产出 `verdict.passed(island)` 或 `verdict.failed(island)`。decide 路由：passed → PRISM_PLAN；failed → PLANNING。
- `PRISM_PLAN`：产出 `dispatch_agent(role=prism, substate=PRISM_PLAN)` command，携带判据包名称+版本 + task graph 切片。executor dispatch Prism，校验判据包 identity（反自述回读），产出 `prism.verdict(pass|revise)`。decide 路由：pass → TASK_DISPATCH；revise → PLANNING；设计缺口 → rollback_stage(M-DESIGN)；需求缺口 → awaiting_human。
- `TASK_DISPATCH`：产出 `start_task` command（DAG 依赖选 ready task + 单写者 lease + manifest 创建）。executor 产出 `writelock.granted` + `task.started`。decide 路由：→ RED。全部 task 完成 → ISLAND_GATE_2。
- `RED`：产出 `dispatch_agent(role=devon, substate=RED)` command，携带 task manifest + phase=red。Devon outcome 后由 executor 校验 test-only diff，产出 `outcome.received`。decide 路由：test-only → RED_GATE；非 test-only → 重派 Devon。
- `RED_GATE`：产出 `classify_red` command。executor 分类预期失败（合法红 = assertion_failure / symbol_missing；非法红 = collection_error / unexpected_pass）。decide 路由：全部合法 → RED_CHECKPOINT；非法 → RED 重派。
- `RED_CHECKPOINT`：产出 `create_red_checkpoint` command。executor 创建私有 commit R + git ref `refs/trac/rgr/{run}/{task}/{attempt}/red`（compare-and-set），产出 `red.checkpointed`。decide 路由：→ PRISM_RED。
- `PRISM_RED`：产出 `dispatch_agent(role=prism, substate=PRISM_RED)` command，携带 B..R diff + 判据包。decide 路由：pass → GREEN；revise → RED 新 attempt。
- `GREEN`：产出 `dispatch_agent(role=devon, substate=GREEN)` command，携带 task manifest + phase=green + R tree identity。Devon outcome 后由 executor 校验 R 测试不可改 + manifest 白名单 diff 回灌。decide 路由：→ GREEN_GATE。
- `GREEN_GATE`：产出 `run_task_gates` command。executor 在 gate worktree 执行 targeted 单测 + 历史单测 + int 子集 + lint/format/type/static + 合同。decide 路由：全过 → GREEN_COMMIT；实现缺陷 → GREEN；int 归因不明 → DIAGNOSE。
- `GREEN_COMMIT`：产出 `create_green_commit` command。executor 创建正式 commit G（parent=B + trailers Tracks-Task/Tracks-Attempt/Tracks-R/Tracks-Issue/Tracks-AC），产出 `green.committed`。decide 路由：→ REFACTOR。
- `REFACTOR`：产出 `dispatch_agent(role=devon, substate=REFACTOR)` command，携带 phase=refactor。Devon outcome 可含 no_change + 理由。decide 路由：→ REFACTOR_GATE。
- `REFACTOR_GATE`：产出 `run_refactor_gate` command。executor 重跑 GREEN_GATE 全部检查 + 质量门禁分层。decide 路由：通过 → TASK_REVIEW；失败 → REFACTOR；动 public interface → rollback_stage(upstream)。
- `TASK_REVIEW`：产出 `review_task` command。executor 校验 write scope / secret / AC trace / B-R-G(-Refactor) lineage / budget。decide 路由：通过 → PRISM_FINAL；budget/scope fail → GREEN。
- `PRISM_FINAL`：产出 `dispatch_agent(role=prism, substate=PRISM_FINAL)` command，携带完整 range + lineage + 判据包。decide 路由：pass → TASK_DONE；revise(实现) → GREEN；revise(Red 测试) → RED 新 lineage。
- `TASK_DONE`：产出 `task.completed` 事件。decide 路由：还有 ready task → TASK_DISPATCH；全部完成 → ISLAND_GATE_2。
- `ISLAND_GATE_2`：产出 `check_island_2` command。executor 执行 `trac check reach` + 全量 integration + e2e。decide 路由：通过 → stage.exited(M-IMPL) + run.completed(boundary)；失败 → DIAGNOSE；verdict.failed(island) → PLANNING。
- `DIAGNOSE`：产出 `dispatch_agent(role=prism, substate=DIAGNOSE)` command（四路诊断）。executor 产出 `verdict.failed(classification)` 事件。decide 路由：impl_defect → GREEN；test_defect → SHIELD_FIX；stub_gap → rollback_stage(M-DESIGN)；ac_gap/spec_gap → awaiting_human。
- `SHIELD_FIX`：产出 `dispatch_agent(role=shield, substate=SHIELD_FIX)` command。executor 创建受控测试 commit（`test.committed`）。decide 路由：→ GREEN_GATE（重跑）。

M-IMPL attempt 预算：PLANNING validate 失败、PRISM_PLAN/PRISM_RED/PRISM_FINAL revise、RED_GATE 非法红、GREEN_GATE/REFACTOR_GATE 失败均消耗各自子状态的 ≤3 预算（第 3 次升级 `awaiting_human`/escalation）。DIAGNOSE 路由（impl_defect/test_defect）回 GREEN/SHIELD_FIX 消耗 GREEN 预算；stub_gap/ac_gap/spec_gap 不消耗预算（路由到其他阶段）。`trac retry` 清除 escalation gate 并重置一份新的 ≤3 预算（NFR-0030-03）。

### 1.3 Composition Root

M-IMPL 的装配入口是 `trac run`（与既有阶段同构）。每条 required AC 的六元组 composition 列在此节可追溯：

**M-IMPL 阶段入口路径**：

```
trac run
  -> tracks.cli.main:cmd_run
    -> Executor(store, repo, run_id).run_loop()
      -> kernel.machine.decide(state)
        -> _decide_m_impl(s, sub)  # M-IMPL 显式控制流
          -> Command(kind="dispatch_agent", params={role, substate, assignment})
          -> Command(kind="recompute_baseline")
          -> Command(kind="validate_taskgraph")
          -> Command(kind="check_island")
          -> Command(kind="start_task")
          -> Command(kind="classify_red")
          -> Command(kind="create_red_checkpoint")
          -> Command(kind="run_task_gates")
          -> Command(kind="create_green_commit")
          -> Command(kind="run_refactor_gate")
          -> Command(kind="review_task")
          -> Command(kind="check_island_2")
          -> Command(kind="create_test_commit")  # SHIELD_FIX
      -> Executor._execute(cmd, state)
        -> _do_dispatch_agent      # Devon/Archer/Prism/Shield dispatch via effects backend
        -> _do_recompute_baseline  # baseline digest + frozen test path set
        -> _do_validate_taskgraph  # taskgraph.parse_tasks_json + validate_dag/scope/ac_coverage/issue_numbers
        -> _do_check_island        # six-tuple check (reads architecture.md + interfaces.md + test-plan.md)
        -> _do_start_task          # DAG ready task select + writelock lease + manifest creation
        -> _do_classify_red        # rgr.classify_red (reuse v0.4 RedClass, minus stub_token_failure)
        -> _do_create_red_checkpoint  # rgr.create_red_ref (compare-and-set)
        -> _do_run_task_gates      # quality_gate.run_gates (targeted unit + int subset + lint)
        -> _do_create_green_commit # rgr.create_green_commit (parent=B + trailers Tracks-Task/Attempt/R/Issue/AC)
        -> _do_run_refactor_gate   # quality_gate.run_gates + layering enforcement
        -> _do_review_task         # scope/secret/AC trace/lineage/budget check
        -> _do_check_island_2      # trac check reach + full int+e2e run
        -> _do_create_test_commit  # git add tests/ + commit (SHIELD_FIX)
      -> store.append(event)    # baseline.frozen / taskgraph.committed / red.checkpointed / etc.
```

**trac retry 入口路径**（独立 CLI，NFR-0030-03）：

```
trac retry [--clear-evidence]
  -> tracks.cli.main:cmd_retry
    -> store.append(Event(kind="human.retry"))
    -> kernel: clear escalation gate, reset attempt budget (≤3)
    -> 不自动重新派发（Human 须再次 trac run）
```

六元组闭合（每条 required AC 的 owner/surface/composition/wiring/test/evidence，附 IF- 标识对齐 interfaces.md §5）：

- **FR-0010（M-IMPL 注册）**：owner=kernel/machine.py, surface=`trac run`/`trac status`, composition=Executor.run_loop→_decide_m_impl, wiring=cmd_run→decide→_decide_m_impl→dispatch_agent/recompute_baseline, test=integration test_m_impl_cycle + e2e test_m_impl_journey, evidence=`trac status` 报告 `stage=M-IMPL substate=BASELINE` + 事件流 `stage.entered(M-IMPL)`, IF-=IF-IMPL-001。
- **FR-0020（BASELINE）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(BASELINE)→recompute_baseline, wiring=decide→recompute_baseline→_do_recompute_baseline→baseline.frozen, test=integration test_baseline_recalc + test_frozen_test_paths, evidence=`baseline.frozen` 事件 payload 含 digest + frozen test path set, IF-=IF-IMPL-001, IF-IMPL-002。
- **FR-0030（PLANNING）**：owner=kernel+executor, surface=`trac run`, composition=_decide_m_impl(PLANNING)→dispatch_agent(archer)+validate_taskgraph, wiring=decide→dispatch_agent→Archer outcome→taskgraph.committed→validate→ISLAND_GATE_1, test=integration test_taskgraph_validate + test_planning_cycle, evidence=`taskgraph.committed` 事件 + `trac validate --file tasks.json` 校验 DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性, IF-=IF-IMPL-001, IF-IMPL-002, IF-IMPL-003。
- **FR-0040（ISLAND_GATE_1）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(ISLAND_GATE_1)→check_island, wiring=decide→check_island→_do_check_island→verdict.passed(island), test=integration test_island_gate_1, evidence=六元组闭合检查通过 + `trac status` 报告 `substate=PRISM_PLAN`, IF-=IF-IMPL-002。
- **FR-0050（PRISM_PLAN）**：owner=kernel+effects, surface=`trac run`, composition=_decide_m_impl(PRISM_PLAN)→dispatch_agent(prism)+判据包, wiring=decide→dispatch_agent→OpencodeBackend.act(prism)→prism.verdict, test=integration test_prism_plan_criteria_pack, evidence=`prism.verdict` 事件 payload 含判据包 identity, IF-=IF-IMPL-001, IF-IMPL-002。
- **FR-0060（TASK_DISPATCH）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(TASK_DISPATCH)→start_task, wiring=decide→start_task→_do_start_task→writelock.granted+task.started, test=integration test_task_dispatch + test_dag_scheduling, evidence=`writelock.granted` + `task.started` 事件 + manifest 创建, IF-=IF-IMPL-002。
- **FR-0070（RED）**：owner=kernel+effects+executor, surface=`trac run`, composition=_decide_m_impl(RED)→dispatch_agent(devon)+create_red_checkpoint, wiring=decide→dispatch_agent→Devon outcome→red.checkpointed, test=integration test_red_phase + test_devon_isolation + test_r_ref, evidence=`red.checkpointed` 事件 + `git rev-parse refs/trac/rgr/{run}/{task}/{attempt}/red` 存在, IF-=IF-IMPL-001, IF-IMPL-002, IF-IMPL-004, IF-IMPL-006, IF-DEVON-001。
- **FR-0080（RED_GATE）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(RED_GATE)→classify_red, wiring=decide→classify_red→rgr.classify_red→verdict, test=integration test_red_gate_m_impl, evidence=合法红分类事件 + `trac status` 报告 `substate=RED_CHECKPOINT`, IF-=IF-IMPL-002, IF-IMPL-004。
- **FR-0090（PRISM_RED）**：owner=kernel+effects, surface=`trac run`, composition=_decide_m_impl(PRISM_RED)→dispatch_agent(prism), wiring=decide→dispatch_agent→prism.verdict, test=integration test_prism_red, evidence=`prism.verdict` 事件 + B..R range 评审, IF-=IF-IMPL-001, IF-IMPL-002。
- **FR-0100（GREEN）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(GREEN)→dispatch_agent(devon)+diff回灌, wiring=decide→dispatch_agent→Devon outcome→manifest diff回灌, test=integration test_green_phase + test_diff_rebase, evidence=Devon outcome 含产品代码 + R 测试不可改校验, IF-=IF-IMPL-002, IF-IMPL-006。
- **FR-0110（GREEN_GATE）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(GREEN_GATE)→run_task_gates, wiring=decide→run_task_gates→quality_gate.run_gates→verdict, test=integration test_green_gate + test_feedback_desensitization, evidence=gate 通过 + int/e2e 失败反馈脱敏（无断言原文）, IF-=IF-IMPL-002, IF-IMPL-005, IF-IMPL-006。
- **FR-0120（GREEN_COMMIT）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(GREEN_COMMIT)→create_green_commit, wiring=decide→create_green_commit→rgr.create_green_commit→green.committed, test=integration test_green_commit + test_lineage_proof, evidence=`green.committed` 事件 + `git log --format='%B' -1 <G>` 含五个 trailer（Tracks-Task/Tracks-Attempt/Tracks-R/Tracks-Issue/Tracks-AC）, IF-=IF-IMPL-002, IF-IMPL-004。
- **FR-0130（REFACTOR）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(REFACTOR)→dispatch_agent(devon)+run_refactor_gate, wiring=decide→dispatch_agent→Devon outcome→run_refactor_gate→verdict, test=integration test_refactor + test_quality_gate_layering, evidence=`refactor.committed` 或 `refactor.no_change` 事件 + 质量门禁分层（生产全检查、测试仅 R0801+C0302）, IF-=IF-IMPL-002, IF-IMPL-005。
- **FR-0140（TASK_REVIEW+PRISM_FINAL）**：owner=executor+kernel, surface=`trac run`, composition=_decide_m_impl(TASK_REVIEW)→review_task + _decide_m_impl(PRISM_FINAL)→dispatch_agent(prism), wiring=decide→review_task→prism.verdict→task.completed, test=integration test_task_review + test_prism_final, evidence=`task.completed` 事件 + `trac report` 展示 per-task RGR lineage, IF-=IF-IMPL-001, IF-IMPL-002, IF-IMPL-007。
- **FR-0150（DIAGNOSE+SHIELD_FIX）**：owner=kernel+executor, surface=`trac run`, composition=_decide_m_impl(DIAGNOSE)→dispatch_agent(prism)+_decide_m_impl(SHIELD_FIX)→dispatch_agent(shield), wiring=decide→dispatch_agent→verdict.failed(classification)→route, test=integration test_diagnose_four_way + test_shield_fix, evidence=`verdict.failed(reason)` 事件 + `test.committed` 事件（SHIELD_FIX）, IF-=IF-IMPL-001, IF-IMPL-002。
- **FR-0160（ISLAND_GATE_2）**：owner=executor, surface=`trac run`, composition=_decide_m_impl(ISLAND_GATE_2)→check_island_2, wiring=decide→check_island_2→trac check reach + full int+e2e→stage.exited, test=integration test_island_gate_2 + e2e test_m_impl_journey, evidence=`stage.exited(M-IMPL)` + `run.completed(terminal_state="boundary")` 事件, IF-=IF-IMPL-002。
- **FR-0170（Devon 接入）**：owner=effects/opencode.py+deliverables.py, surface=`trac run`+`trac check deliverables`, composition=_do_dispatch_agent(devon)+check_deliverables, wiring=decide→dispatch_agent→OpencodeBackend.act(devon)+audit, test=integration test_devon_dispatch + unit test_deliverables, evidence=Devon.md 存在 + version/IQ frontmatter + manifest 越界审计回滚, IF-=IF-DEVON-001。
- **FR-0180（tasks.json/tasks.md）**：owner=executor/validate.py+taskgraph.py, surface=`trac run`+`trac validate --file tasks.json`, composition=_do_validate_taskgraph+validate_document, wiring=cli→validate→taskgraph.parse_tasks_json+taskgraph.validate_dag/scope/ac_coverage/issue_numbers, test=integration test_tasksjson_validate + test_tasksmd_projection, evidence=`trac validate --file tasks.json` 校验 DAG 无环 + scope 边界不重叠 + required AC 覆盖闭合 + IF- 有效性 + issue number 有效性（5 项 check） + tasks.md 确定性投影写入, IF-=IF-IMPL-002, IF-IMPL-003, IF-IMPL-007。
- **FR-0190（dispatch 物化）**：owner=executor+project.py, surface=`trac run`, composition=_do_dispatch_agent assignment 物化, wiring=decide→dispatch_agent→assignment payload 物化→backend, test=integration test_dispatch_materialization, evidence=assignment payload 含完整上下文（10 项物化字段）, IF-=IF-IMPL-002。
- **FR-0200（崩溃恢复）**：owner=executor, surface=`trac run`, composition=_recover()+per-kind reconcile, wiring=trac run→_recover→event replay→resume phase, test=integration test_crash_recovery + test_r_ref_survives_crash, evidence=崩溃重启后 R ref 不变 + G trailers 可重建 + 不重跑已完成 task, IF-=IF-IMPL-002, IF-IMPL-004。
- **FR-0210（Shield test-plan 测试归属边界）**：owner=executor/validate.py, surface=`trac run`+`trac validate --file test-plan.md`, composition=_do_validate_taskgraph+validate_document test-plan §8 归属校验, wiring=cli→validate→test-plan §8 AC Coverage 表解析→task.ac_refs/test_refs 交叉校验, test=integration test_testplan_ownership_boundary, evidence=`trac validate --file test-plan.md` 校验 test-plan §8 不复制到 tasks.json + 每个 task 的 test_refs 可追溯到 §8 行, IF-=IF-IMPL-002, IF-IMPL-003, IF-IMPL-007。
- **FR-0220（Issues 消费语义）**：owner=executor/taskgraph.py+effects/opencode.py, surface=`trac run`+`trac validate --file tasks.json`, composition=_do_validate_taskgraph(validate_issue_numbers)+_do_dispatch_agent(issue# + FR/NFR/ACC provenance payload), wiring=cli→validate→taskgraph.validate_issue_numbers + decide→dispatch_agent→assignment payload 携带 issue# + FR/NFR/ACC provenance→Devon commit trailers, test=integration test_issue_consumption + test_commit_trailers_provenance, evidence=`trac validate --file tasks.json` issue number 有效性校验 + Devon G commit trailers 含 `Tracks-Issue: {issue_number}` + `Tracks-AC: {ac_refs}`, IF-=IF-IMPL-002, IF-IMPL-003, IF-IMPL-004, IF-DEVON-001。
- **NFR-0010（kernel 纯函数）**：owner=kernel/machine.py, surface=`trac run`, composition=_decide_m_impl 纯函数, wiring=decide→纯函数, test=unit test_kernel_purity_m_impl, evidence=decide()/project() 不碰 IO/clock/env/文件系统, IF-=IF-IMPL-001。
- **NFR-0020（append-only）**：owner=kernel+executor, surface=`trac run`, composition=store.append 事件, wiring=executor→store.append, test=integration test_events_append_only_m_impl, evidence=M-IMPL 事件 append-only + 投影可重建, IF-=IF-IMPL-001, IF-IMPL-002。
- **NFR-0030（dispatch 活动性）**：owner=cli+kernel, surface=`trac run`/`trac status`/`trac retry`, composition=cmd_run 控制台输出+cmd_retry, wiring=cli→控制台 flush+store.append(human.retry), test=integration test_dispatch_activity + test_trac_retry, evidence=控制台活动输出 + `trac status` 含 attempt 计数 + `trac retry` 产出 `human.retry` 事件, IF-=IF-IMPL-001。

## 2. Scaffold 宣言

- `.tracks/project/project.toml` — canonical 宿主项目测试执行合同（integration/e2e 的 framework/paths/collect/run/cwd）（kind: config）
- `tracks/executor/taskgraph.py` — tasks.json 解析 + DAG/scope/AC 覆盖/IF- 有效性/issue number 有效性校验桩：`parse_tasks_json` / `validate_dag` / `validate_scope` / `validate_ac_coverage` / `validate_issue_numbers` 签名，行为体 raise NotImplementedError("IF-IMPL-003")（kind: stub）
- `tracks/executor/rgr.py` — RGR git 操作桩：`create_red_ref` / `create_green_commit` / `classify_red` / `verify_lineage` 签名，行为体 raise NotImplementedError("IF-IMPL-004")（kind: stub）
- `tracks/executor/worktree.py` — 三 worktree 方案桩：`create_devon_worktree` / `create_gate_worktree` / `create_test_authority_worktree` / `cleanup_worktree` 签名，行为体 raise NotImplementedError("IF-IMPL-006")（kind: stub）
- `tracks/executor/quality_gate.py` — 质量门禁分层执行桩：`run_gates` / `run_production_checks` / `run_test_checks` 签名，行为体 raise NotImplementedError("IF-IMPL-005")（kind: stub）

## 3. 技术选型

### 3.1 tasks.json 解析与校验（executor/taskgraph.py）

- **解析**：纯 Python 标准库（json/dataclasses/pathlib）。解析 tasks.json（task graph 唯一机器真相）——JSON 数组，每个 task 对象字段：`task_id`（T-xxx）、`issue_number`（正整数 GitHub issue 编号）、`description`（切片描述 + 实现意图）、`ac_refs`（AC ID 元组）、`fr_refs`（FR/NFR ID 元组）、`if_ids`（IF- 集合）、`test_refs`（test-plan §8 测试引用）、`scope_boundary`（manifest 授权 scope，非预声明输出文件集）、`depends_on`（依赖 task ID 元组，`[]` 或 `["-"]` = 无依赖）、`batch`（批次标记）、`parallel`（`[P]` 标记，v0.5 串行仅记录）、`budget`（attempt 预算 ≤3）。tasks.md 仅为人类可读投影，由 Runtime 从 tasks.json 确定性生成（不作为解析来源）。
- **DAG 校验**：拓扑排序检测环（Kahn's algorithm）。scope 边界不重叠检查（manifest 白名单集合交集为空——scope_boundary 是授权范围而非预声明输出文件集，observed diff 为权威）。
- **AC 覆盖闭合检查**：每个 required AC 至少被一个 task 的 ac_refs + if_ids 覆盖（读取 acceptance.md required AC 列表 + tasks.json 中每个 task 声明的 ac_refs/if_ids）。
- **IF- 有效性检查**：tasks.json 中每个 task 的 if_ids 必须存在于 interfaces.md §5 IF Registry（读取 interfaces.md §5 canonical headers）。
- **issue number 有效性检查**：每个 task 的 issue_number 必须是正整数（>=1）。
- **取舍**：不引入 networkx 等图库（项目零运行时依赖）。Kahn's algorithm 用标准库 collections.deque 即可实现。不引入 jsonschema（tasks.json schema 用纯 Python dataclass + 字段校验实现，避免运行时依赖）。
- **风险**：tasks.json 字段缺失或类型错误。缓解：解析器容错（缺失必填字段产出明确校验失败消息，未知字段跳过不报错）。

### 3.2 RGR git 操作（executor/rgr.py）

- **R ref 创建**：`git update-ref refs/trac/rgr/{run}/{task}/{attempt}/red <sha>`（compare-and-set：先 `git rev-parse` 检查 ref 是否存在，已存在则失败开新 attempt）。
- **G commit 创建**：`git commit-tree` 或 `git commit` with `--trailer` 选项（Git ≥ 2.32 支持 `--trailer`；项目 .gitignore 不影响 refs/trac/）。trailers：`Tracks-Task: {task_id}` / `Tracks-Attempt: {attempt}` / `Tracks-R: {r_sha}` / `Tracks-Issue: {issue_number}` / `Tracks-AC: {ac_refs}`（R-3/R-4：Devon 提交时在 trailers 中包含 issue# + FR/NFR/ACC provenance，使后续 bug fix 保留 provenance）。
- **lineage 证明**：检查 R ref 存在 + G commit trailers 存在 + 事件序列（`red.checkpointed` seq < `green.committed` seq）。不作 Git ancestry 拓扑断言（R-1）。
- **取舍**：不使用 `git notes`（trailers 在 commit message 中更持久）。不依赖 Git ancestry（R-1 明确：G parent=B，R 不是 G 的祖先）。
- **风险**：Git 版本 < 2.32 不支持 `--trailer`。缓解：检测 Git 版本，回退到手动构造 commit message 含 trailer 行。

### 3.3 三 worktree 方案（executor/worktree.py）

- **Devon candidate worktree**：从 `C_design`（M-DESIGN pass 后的 commit）`git worktree add` 创建。Devon 在此 worktree 运行，天然不包含 Shield WRITE 之后生成的测试。
- **Gate worktree**：组合 `C_design` + frozen test bundle + Devon candidate diff。先 `git worktree add` 从 `C_design`，然后 `git checkout` Devon candidate 的产品代码 diff，最后 `git cherry-pick` 或 `git merge` frozen test commit。
- **Test-authority worktree**：Shield 在此 worktree 冻结测试（frozen bundle），独立 commit。
- **清理**：phase 完成后 `git worktree remove`。
- **取舍**：三 worktree 方案是 v0.5 最复杂的 executor 子模块。不使用 `git submodule`（过于重量级）。frozen bundle 永不合入 Devon candidate（BS-04 时间隔离）。
- **风险**：worktree 操作可能失败（磁盘空间、路径冲突）。缓解：失败时产出明确 error 事件，不破坏既有 worktree。

### 3.4 质量门禁分层（executor/quality_gate.py）

- **生产代码**：执行 ruff check + flake8（含 CCR001）+ pylint（R0801 + C0302 + R0915 + R0914）。任一判失败重派 Devon。
- **测试代码**：仅执行 pylint R0801 + C0302。不套用 CCR001 / R0915 / R0914（BS-11）。
- **分层机制**：quality_gate.py 按 changed_paths 分类文件为生产代码（tracks/ 下非 tests/）或测试代码（tests/ 下），对生产代码执行完整检查集、对测试代码执行受限检查集（与 pre-commit hook 的分层方式一致：pre-commit 通过分别执行命令实现--flake8 CCR001 只跑 tracks、pylint R0915/R0914 只跑 tracks；quality_gate.py 继承同一分层政策，按 changed_paths 分类后分别执行）。
- **取舍**：pre-commit 与 quality_gate.py 均分层（BS-11 一致政策），分层方式为分别执行命令而非 per-file-ignores 配置（pylint pyproject.toml 不支持 per-file disable，flake8 per-file-ignores 非必需）。代价：quality_gate.py 需自行按 changed_paths 分类并分别调用工具。
- **风险**：生产代码与测试代码的路径分类需要准确。缓解：基于 manifest 白名单 + 文件路径前缀（`tests/` 前缀 = 测试代码）。

### 3.5 Devon agent 接入（effects/opencode.py）

Devon 作为第七个 opencode agent 接入，与 Scribe/Sage/Lex/Archer/Prism/Shield 同构：

- `AGENT_NAME["devon"] = "Devon"`。
- Devon.md 加入 `AGENT_DELIVERABLES`（version + IQ frontmatter 校验）。
- Devon.md 移除 permission 块（D-26：隔离由时态 worktree + manifest 承担，不由 agent 定义中的 permission 块承担）。现有 Devon.md 的 permission 块在 v0.5 实现阶段移除。
- Devon manifest 越界审计：`Auditor` 的 allowed 列表对 Devon dispatch 使用 per-task manifest 白名单（而非固定目录列表）。越权写（manifest 白名单之外的文件）→ `over_reach` failure_class → git 回滚。
- Devon assignment 携带 `skills: ["tracks-prism-impl"]`（M-IMPL 评审判据包，Prism 消费；Devon 自身不消费判据包 skill，但 assignment 需要物化判据包 identity 供 Prism 反自述回读）。

### 3.6 dispatch 物化合同（FR-0190）

FR-0190 的 10 项物化增量大部分是对 v0.4 既有机制的扩展/强化，不引入新模块：

1. **state-specific Human return**：machine.py 的 `awaiting_human` 路由增补 M-IMPL escalation → `to_stage=M-DESIGN`（SM-01.12/27）。扩展既有 human return 逻辑。
2. **canonical 路径**：project.py 强化 `.tracks/project/project.toml` 为唯一允许的 `.tracks/**` project contract 路径。其他 `.tracks/**` 路径仍 fail closed。
3. **M-DESIGN 输出合同**：validate.py 的 `check_design_trace` 扩展为解析 test-plan `## 8. AC Coverage` 与 interfaces `## 5. IF Registry` canonical headers。v0.4 已建立 IF- 校验，v0.5 强化 header 解析与 fail-closed 规则。
4. **test_tasks 注入**：executor.py 的 `_do_dispatch_agent` 扩展，向 Shield assignment 注入 `test_tasks`（v0.4 已部分实现，v0.5 强化无效输入 fail closed → `stub_gap`）。
5. **ResultCheckpoint**：result_checkpoint.py 强化 `pre_dirty_snapshot` 持久化与 post 内容身份比较（v0.4 已实现 ResultCheckpoint，v0.5 扩展 dispatch flag 清理与 unchanged Human dirty files 排除）。
6. **collection 口径**：executor.py 的 `_do_collect_tests` 扩展 collection 规则（conftest/helper 可 checkpoint 但 collect-only 只对 test modules；只有 helper 时 fail closed；conftest 不得被单独判 no-tests）。
7. **host project command**：project.py 的 `load_contract` 强化 `.venv/bin/python` 回退逻辑（不存在时回退当前 Runtime venv `sys.executable`，不得系统 Python）。
8. **rollback evidence**：executor.py 的 rollback handler 增补规则（仅 M-TEST `stub_gap`→M-DESIGN 清 stale failure，其他语义回退保留 evidence）。
9. **dispatch materialization 完整性**：executor.py 的 `_do_dispatch_agent` 扩展 assignment payload 物化字段（绝对 target doc/doc-set、role/substate/attempt/review_round、docs/templates/skills、criteria-pack identity、test_tasks、pre_dirty_snapshot、result/checkpoint identity）。
10. **覆盖范围**：横切覆盖所有 agent dispatch + Runtime validate/checkpoint/publish/collect/run/red/commit/seal。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/project/project.toml`）

声明 integration/e2e 的 framework、paths、collect/run 命令和 cwd。collect 命令用于 M-TEST 测试收集阶段（SM-01.5），run 命令用于 RED_CHECK（SM-01.9）与 M-IMPL GREEN_GATE int 子集（SM-01.22）。命令通过 shlex.split + subprocess(shell=False) 执行，cwd 相对于宿主项目根目录。

```toml
[integration]
framework = "pytest"
paths = ["tests/integration/"]
collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"
run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q"
cwd = "."

[e2e]
framework = "pytest"
paths = ["tests/e2e/"]
collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"
run = ".venv/bin/python -m pytest tests/e2e/ --tb=short -q"
cwd = "."
```

### 4.2 质量守卫栈（八类，继承 ARCH-004 canonical 配置；BS-11 分层由 pre-commit 分命令执行 + quality_gate.py 程序执行继承）

| # | 守卫 | 工具（pinned） | 配置位置 | 阈值 | 执行点 |
|:---|:---|:---|:---|:---|:---|
| 1 | lint + format | ruff==0.16.0 | `pyproject.toml` `[tool.ruff]`/`[tool.ruff.lint]` | line-length=100；select E,F,W,I,B,UP,SIM,C4 | pre-commit + CI `lint` |
| 2 | 认知复杂度 | flake8==7.3.0 + flake8-cognitive-complexity==0.1.0 | `.flake8` | CCR001 ≤ 15（生产代码）；tests/ 豁免（pre-commit 只跑 tracks） | pre-commit + CI `lint` |
| 3 | 重复度 | pylint==4.0.6 | `pyproject.toml` `[tool.pylint.similarities]` | R0801，min-similarity-lines=5 | pre-commit + CI `lint` |
| 4 | 文件长度 | pylint | `[tool.pylint.format]` | C0302，max-module-lines=1000 | pre-commit + CI `lint` |
| 5 | 方法长度/局部变量 | pylint | `[tool.pylint.design]` | R0915 max-statements=50；R0914 max-locals=15（生产代码）；tests/ 豁免 | pre-commit + CI `lint`（tests 豁免 R0915/R0914） |
| 6 | 覆盖率门槛 | coverage==7.15.2 + pytest==9.1.1 | `pyproject.toml` `[tool.coverage.*]` | CI `coverage report --fail-under=95` | CI `coverage` |
| 7 | 钩子运行器 | git hooks | `.githooks/pre-commit` | 按序执行 ruff+flake8+pylint | 本地提交 + CI |
| 8 | CI required checks | GitHub Actions | `.github/workflows/ci.yml`（待实现） | lint/coverage/test/deliverables/trace/reach 全部 required | merge 门禁 |

v0.5 增补的分层执行（BS-11）：
- pre-commit hook 已在 v0.4 通过分别执行命令实现分层（flake8 CCR001 只跑 tracks、pylint R0915/R0914 只跑 tracks）；v0.5 不修改 pre-commit hook 与配置文件。
- GREEN_GATE/REFACTOR_GATE 中 `quality_gate.py` 按 changed_paths 分类文件后分别执行检查集（程序执行，与 pre-commit hook 继承同一 BS-11 分层政策）。

安装命令（Archer 写入合同；执行属 Runtime 生效副作用）：

```
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
git config core.hooksPath .githooks
```

### 4.3 CI 合同（`.github/workflows/ci.yml`，ci-skeleton 待 Devon 补全为真实 workflow）

stable required checks（名称稳定，merge 只认 CI 结论）：

1. `lint`：ruff check tracks tests + flake8 tracks（含 CCR001，tests/ 豁免--pre-commit 只跑 tracks）+ pylint R0801/C0302/R0915/R0914（tests/ 豁免 R0915/R0914--pre-commit 分命令执行）。
2. `coverage`：`coverage run -m pytest && coverage combine && coverage report --fail-under=95`。
3. `test`：`pytest -q -m 'not performance'`（含 unit + integration + e2e fake 通道）。
4. `deliverables`：`trac check deliverables`（存在性 + version + IQ，含 Devon.md）。
5. `trace`：`trac check trace --json`（需求追踪闭合；待 Devon 实现子命令后激活，标注 foundation task）。
6. `reach`：`trac check reach --json`（模块可达性；待 Devon 实现子命令后激活，标注 foundation task）。

live 通道（`tests/e2e_live/`）：缺凭据 skip（独立 opt-in job），非 required check。

### 4.4 integration/e2e 测试基础设施

- **fake 通道**（deterministic，CI 必跑）：conftest 强制 `TRAC_AGENT_BACKEND=fake`。FakeBackend 经 `simulate` 控制 Devon/Archer/Prism/Shield 分支。继承 v0.2 conftest 的 `host_repo`/`trac`/`event_log` fixtures。
- **L2 contract sim**（opencode stand-in）：继承 v0.2 `fake_opencode` fixture，验证 Devon dispatch 的物化/JSON 解析/manifest 越界审计/失败矩阵。
- **live 通道**（真 opencode）：缺凭据 skip，独立 opt-in job。
- **GREEN_GATE int 子集执行**：executor 使用 project.toml 的 run 命令在 gate worktree 执行 int 子集（按 test-plan §8 IF- 归属筛选）。

### 4.5 release version / build / artifact

继承 ARCH-004 §4.4。v0.5 新增 package data：`tracks/agents/Devon.md` 已在 v0.4 创建（但未加入 deliverables）；`tracks/skills/tracks-prism-impl/SKILL.md` 已在 v0.4 创建。`[tool.setuptools.package-data]` 已含 `agents/*.md` 与 `skills/*/*.md`，无需修改。

### 4.6 发布恢复

继承 v0.1-v0.4：事件溯源 append-only + 投影可重建。M-IMPL 全程事件 append-only（NFR-0020）；drop 投影表后 `trac status`/`trac report` 重建的 M-IMPL 状态与原一致。崩溃恢复（FR-0200）：`_recover()` 复用 per-kind reconcile（D-13）；M-IMPL 的 recompute_baseline/validate_taskgraph(check tasks.json)/check_island/start_task/create_red_checkpoint/run_task_gates/create_green_commit/run_refactor_gate/review_task/check_island_2 各有 reconcile 语义（幂等：已成功则跳过）。R ref 不可变（FR-0070）保证崩溃后 lineage 证据不丢失；G commit trailers 保证崩溃后 R-G 绑定可重建。

## 5. 有意识简化与风险

### 5.1 有意识简化

- **串行调度，不并发 `[P]`**：v0.5 串行调度所有 task，`[P]` 标记只记录不并发执行（Aaron 裁定）。代价：DAG 中的并行机会未被利用。理由：并发调度引入复杂的 worktree/manifest/git ref 管理，v0.5 范围排除。
- **GREEN_GATE 不跑 e2e**：第一轮 GREEN_GATE 只跑 targeted 单测 + 历史单测 + int 子集 + lint/format/type/static（BS-08）。代价：e2e 失败延迟到 ISLAND_GATE_2 发现。理由：e2e 慢且 Devon 隔离不看 e2e 结果。
- **lineage 不作 Git ancestry 拓扑断言**：R 先于 G 由 ref + trailer + 事件序列联合证明，不用 `git merge-base --is-ancestor`（R-1）。代价：lineage 证明依赖三个独立来源。理由：G parent=B，R 不是 G 的祖先，Git ancestry 无法证明 R 先于 G。
- **quality_gate 分层与 pre-commit 一致**：pre-commit hook 已在 v0.4 通过分别执行命令实现 BS-11 分层（flake8 CCR001 只跑 tracks、pylint R0915/R0914 只跑 tracks）；quality_gate.py 继承同一分层政策，按 changed_paths 分类后分别执行。代价：quality_gate.py 需自行分类文件并分别调用工具。理由：BS-11 是 spec 明确要求，pre-commit 与 GREEN_GATE 行为一致避免 Devon 困惑。
- **Devon.md 移除 permission 块**：隔离由时态 worktree + manifest 承担（D-26）。代价：Devon.md 的 frontmatter permission 块移除后，opencode 的 per-agent harness 不提供文件级 deny。理由：时态 worktree 隔离（Devon candidate 不含 Shield tests）比 permission 块更可靠；manifest 白名单审计是程序级强制。
- **IF- 标识粒度对齐增长轴**：IF-IMPL-001（kernel 状态机）与 IF-IMPL-002（executor handler）是粗粒度标识，覆盖多个函数/事件/状态字段。理由：kernel/machine.py 与 executor/executor.py 是既有文件，M-IMPL 是扩展而非新桩；IF- 标识服务于 M-IMPL task 变绿子集划分。
- **task graph 解析用纯标准库**：不引入 networkx。代价：DAG 拓扑排序需自行实现。理由：Kahn's algorithm 简单（< 30 行），项目零运行时依赖。
- **ground truth 不适用**：v0.5 是行为正确性（状态机转移、事件序列、git 操作），无算法正确性/规则正确性/计算结果正确性需要独立验证。test-plan §3 判定不适用，不创建 tests/ground_truth/。

### 5.2 风险

- **M-IMPL 子状态机复杂度**：21 个子状态 + 44 条转移 + 4 路 DIAGNOSE 路由，是 tracks 迄今最复杂的状态机。缓解：flow.md §9.1 已完整定义；FakeAgent 端到端测试覆盖完整循环与负例；SM-01 转移覆盖清单逐条核对（NFR-0020）。
- **三 worktree 方案复杂度**：Devon candidate / gate / test-authority 三个 worktree 的创建、组合、清理是 v0.5 最复杂的 executor 操作。缓解：worktree.py 桩先定义接口合同，Devon 逐步实现；integration 测试覆盖 worktree 生命周期。
- **R ref compare-and-set 竞态**：多 attempt 同时写 R ref 可能竞态。缓解：compare-and-set 先 `git rev-parse` 检查 ref 是否存在，已存在则失败；R ref 不可变（BS-06）。
- **Devon manifest 越界审计**：Devon 是第一个按 per-task manifest 白名单审计的 agent（既有 agent 用固定目录列表）。缓解：复用 v0.2 audit 机制（baseline + post-run diff），allowed 列表改为 per-task manifest 白名单。
- **dispatch 物化合同回归覆盖**：FR-0190 的 10 项物化增量大部分是 v0.4 既有机制的扩展，需确保不回归。缓解：M-TEST 既有回归基线（run 01KZ5QCRPMBVC1A6HYEHMKKGVH）作为确定性回归基线；v0.5 新增 M-IMPL escalation 路径在同一测试中覆盖。
