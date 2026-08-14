---
architecture_id: ARCH-005
spec_ref: SPEC-005
created: 2026-08-09
status: draft
sha:
---

# v0.5 — 架构

本文是 ARCH-004（v0.4）的增量延伸。v0.1 的内核机制（事件溯源、单一生产路径、四增长轴分包、单写者锁、per-kind reconcile）、v0.2 的物化合同与 audit、v0.3 的 M-DESIGN Archer/Prism、v0.4 的 M-TEST 与 checks/ 增长轴全部保持不变；v0.5 在既有包内新增 M-IMPL 控制流与 Devon agent 接入，为 FR-0230～FR-0233/NFR-0080 加入真实 live evidence 与只读发布检查，并为 FR-0234～FR-0237/NFR-0090 在 outcome 验收边界加入 doc-comment-first、裁定与隔离恢复，不新增产品顶层 CLI。凡未提及者，一律继承 ARCH-004。

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
- 质量守卫栈 + pre-commit + CI required checks：总体机制不变。pre-commit hook 通过分命令实现 BS-11 测试代码分层（pylint R0801/C0302 改为 tracks only、tests exempt；pylint 全部加 `--exit-zero` 使警告可见但不阻塞提交）；v0.5 在 `executor/quality_gate.py` 程序执行中继承同一分层政策。
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
- `tracks/project.py` 待 foundation task 强化 canonical `.tracks/project/project.toml` 路径约束（唯一允许的 `.tracks/**` project contract 路径）；当前 Runtime bootstrap 仍读取 `.tracks/projects/project.toml`。
- 质量守卫配置不变（pre-commit hook 已在 v0.4 通过分别执行命令实现 BS-11 分层）；v0.5 在 `executor/quality_gate.py` 程序执行中继承同一分层政策（按 changed_paths 分类后分别执行检查集）。
- 新增 `executor/live_evidence.py`：只在真实 OpencodeBackend、无 `TRAC_FAKE_SIMULATE`、无 assignment overlay 的 M-IMPL run 成功到达 boundary 后，绑定并原子写入 canonical evidence bundle；失败/取消路径不写 success bundle。
- 新增 `checks/release_evidence.py` 与 `trac check release-evidence [--json]`：只读校验 evidence bundle、当前 Git HEAD、agent I/O digest、事件序列、RGR lineage、独立门禁和 boundary；不推进 M-VERIFY/M-RELEASE，不发布 artifact。
- `tests/e2e_live` 由原单次 agent smoke 提升为一个 opt-in 的真实 M-IMPL 最小纵向旅程；例行无凭据允许显式 skip，tag/release 验证不得 skip。
- 既有文件的精确增量：`tracks/cli/main.py` 接线新 check；`tracks/executor/executor.py` 在成功 boundary 调用 binder；`tests/e2e_live/conftest.py`/`harness.py` 增加 credential 与 current-wheel provenance；`.github/workflows/ci.yml` 由 Devon foundation task 增加 routine opt-in、weekly 与 tag/release evidence jobs。`tracks/kernel/machine.py`、M-IMPL 状态集合及 M-VERIFY/M-RELEASE 均不因本增量修改。
- 新增 `executor/doc_comment.py` 接口轴内模块：比较 pre-dispatch 文档身份、分类合法 discussion/非法正文、建立 SM-02 记录、描述 quarantine 与决定 restore/discard；副作用仍由 executor/effects/store 执行。
- `executor/executor.py` 的 outcome 接收顺序改为：原子非法正文审计 → 合法新增讨论截获与授权非文档变化隔离 → 既有 ResultCheckpoint/M-IMPL artifact/manifest/collection/gate/checkpoint/DIAGNOSE。合法讨论不再先进入普通验证。
- `kernel/machine.py`/events/report/CLI 投影扩展 SM-02；`trac status`、`trac replay`、`trac report` 暴露等待、裁定、quarantine、restore/discard、resumed。`trac discuss` 协议和固定可评论文档集合不变。
- 非 discussion 正文编辑继续继承当前 effects audit 的整回合原子回滚；本版仅补齐显式 `outcome.rejected` 审计与原 role/task/phase 新 attempt 路由，不允许借合法 discussion 绕过。

## 1. 模块边界

### 1.0.1 增长轴归属

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
| 9 | `executor/` | live evidence provenance 捕获、bundle 绑定与原子持久化 | FR-0230/FR-0231 | IF-LIVE-001 |
| 10 | `checks/` + `cli/` | release-evidence 纯判定、文件读取 wrapper 与 CLI 接线 | FR-0232/FR-0233/NFR-0080 | IF-RELEASE-001 |
| 11 | `executor/` + `effects/` | outcome 文档差异前置分类、Prism 裁定路由与非法正文原子拒绝 | FR-0234/FR-0235/FR-0237 | IF-DOCGAP-001 |
| 12 | `executor/` + `store/` + `kernel/` | 授权非文档变化 content-addressed 隔离、恢复/丢弃与重放投影 | FR-0236/NFR-0090 | IF-QUARANTINE-001 |

新增四个 executor 子模块（taskgraph.py / rgr.py / worktree.py / quality_gate.py）不是新增长轴——它们是 executor 增长轴内的功能模块，被 executor.py 的 M-IMPL handler 调用，不直接被 cli 或 kernel 引用。分层约束：这四个模块可执行 git/subprocess/文件系统操作（副作用），属于 executor 副作用边界，不被 kernel 的 decide()/project() 引用。

### 1.0.2 M-IMPL 控制流（kernel/machine.py）

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

### 1.1 Composition Root

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

**真实 live evidence 入口路径**（FR-0230/FR-0231）：

```
current candidate HEAD -> .venv/bin/python -m pip wheel --no-deps --wheel-dir dist . -> dist/*.whl (sha256 recorded)
  -> isolated demo host/.venv/bin/pip install <current-candidate-wheel>
  -> demo host 中 TRAC_AGENT_BACKEND=opencode
     TRAC_LIVE_CANDIDATE_SHA=<full-head>
     TRAC_LIVE_ARTIFACT_SHA256=<wheel-sha256> trac run
     （不得设置 TRAC_FAKE_SIMULATE；不得使用 --assignment-overlay）
    -> effects.select_backend -> OpencodeBackend
    -> Executor M-IMPL BASELINE -> RED -> RED_GATE -> PRISM_RED
       -> GREEN -> GREEN_GATE -> GREEN_COMMIT -> REFACTOR -> REFACTOR_GATE
       -> TASK_REVIEW -> PRISM_FINAL -> TASK_DONE -> ISLAND_GATE_2
    -> stage.exited(M-IMPL) -> run.completed(terminal_state="boundary")
    -> executor.live_evidence.bind_live_evidence
       -> 复核真实 backend/provenance + Devon RED/GREEN/REFACTOR dispatch receipts
       -> 复核 agent I/O blobs/digests + ordered events + Git R/G lineage + 独立 gate observations
       -> 原子写 canonical evidence bundle
```

live harness 只负责从隔离 demo host 将 Runtime 已生成的 bundle **逐字节传输**到待发布 checkout 的同一 canonical 相对路径；不得合成、补写或改写 evidence。候选 wheel 的完整 SHA-256 与 source candidate HEAD 同时进入 provenance，因而“装了别的 wheel 再声称当前 HEAD”会 fail closed。

**release-evidence 检查入口路径**（FR-0232/NFR-0080）：

```
trac check release-evidence [--json]
  -> tracks.cli.main:cmd_check
  -> tracks.checks.release_evidence:check_release_evidence_file
  -> 读取 current Git HEAD + canonical evidence bundles + bundle 内 content-addressed blobs
  -> check_release_evidence（纯判定）
  -> ReleaseEvidenceReport -> 确定性 stdout/JSON + exit 0|1
```

**doc-comment-first outcome 入口路径**（FR-0234～FR-0237/NFR-0090）：

```
trac run -> Executor._do_dispatch_agent -> backend.act(Devon|Shield)
  -> effects audit 保存 pre-dispatch 文档/Human-pre-dirty 内容身份与 Agent 可归因 diff
  -> executor.doc_comment.classify_design_document_deltas
     -> illegal_body_edit
        -> Auditor.rollback_agent_changes(force=True)
        -> outcome.rejected(over_reach, rollback=atomic, rejected_paths)
        -> 原 logical role/task/phase 的新 dispatch/attempt
     -> legal_discussion
        -> quarantine_authorized_changes（manifest 内非文档 diff；不用 shared index）
        -> doc_comment.detected + outcome.quarantined(empty|held)
        -> AWAITING_ADJUDICATION；普通 ResultCheckpoint/M-IMPL 验证不运行
        -> dispatch Prism 在原 thread 裁定 design_gap|agent_correction
        -> doc_comment.adjudicated；Archer 或原 Agent 经同一 thread 闭环
        -> decide_quarantine_resume(current design/run/path identities)
        -> outcome.restored|outcome.discarded
        -> outcome.resumed + 相同 logical role/task/phase 的新 dispatch/attempt
        -> 新 outcome 重新从 doc-comment-first 与原 phase 全部验证开始
     -> none
        -> 既有 artifact/manifest/collection/gate/checkpoint/DIAGNOSE
  -> store.append(event) -> kernel.project(state) -> status/replay/report projection
```

SM-02 是附着于 origin dispatch 的持久化记录，不是 M-IMPL 第 22 个 substate；因此不改变 AC-FR0010-02 的 21 子状态封闭集。composition root 仍是既有 `trac run`，`trac status/discuss/replay/report` 只是观察与闭环 surface。只有 Prism 在原线程完成裁定；设计缺口路由 Archer、不增加 Human 技术门。隔离内容写既有 Runtime blob store，descriptor/digest 进入事件；共享 Git index 永远不参与。

### 1.2 Required AC closure (ISLAND_GATE_1)

每一行对应 Acceptance 中同 requirement 下按文档顺序的一条 required AC；owner 值中的 AC ID 消除同 requirement 多行的歧义。

- **FR-0010** owner=kernel/m_impl.py:AC-FR0010-01 surface=`trac run` composition=cmd_run→Executor.run_loop→decide wiring=M-TEST-exit→stage.entered(M-IMPL)→BASELINE test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且events含`stage.entered/M-IMPL` IF-IMPL-001
- **FR-0010** owner=kernel/m_impl.py:AC-FR0010-02 surface=`trac run`+`trac status` composition=cmd_run/status→Executor→project wiring=M-IMPL-substate→status→boundary test=e2e:test_boundary_after_m_impl evidence=`.venv/bin/python -m pytest -q tests/e2e/test_m_impl_journey.py`输出`passed`且status含`terminal=boundary` IF-IMPL-001
- **FR-0010** owner=kernel/m_impl.py:AC-FR0010-03 surface=`trac run` composition=Executor.run_loop→_decide_m_impl wiring=event→legal-transition-or-no-command test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且非法转移无`stage.exited` IF-IMPL-001
- **FR-0010** owner=kernel/machine.py:AC-FR0010-04 surface=`trac run` composition=workflow-registry→Executor.run_loop wiring=existing-stages→M-TEST→M-IMPL test=e2e:test_full_journey_to_boundary_includes_m_impl evidence=`.venv/bin/python -m pytest -q tests/e2e/test_full_journey_v05.py`输出`passed`且M-TEST前事件前缀稳定 IF-IMPL-001 IF-MTEST-001
- **FR-0010** owner=kernel/m_impl.py:AC-FR0010-05 surface=`trac status` composition=machine.decide→_decide_m_impl wiring=explicit-substate-branch→Command→status-projection test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且观察到21态专属序列 IF-IMPL-001
- **FR-0020** owner=executor/m_impl_runtime.py:AC-FR0020-01 surface=`trac run` composition=BASELINE→freeze_baseline-handler wiring=baseline-inputs→digest→baseline.frozen→PLANNING test=integration:test_baseline_events_expose_digest_paths_and_error_semantics evidence=`.venv/bin/python -m pytest -q tests/integration/test_baseline_recalc.py`输出`passed`且payload含digest IF-IMPL-001 IF-IMPL-002
- **FR-0020** owner=kernel/m_impl.py:AC-FR0020-02 surface=`trac run`+`trac status` composition=BASELINE-reducer→NEEDS_ATTENTION wiring=stale|conflict→rollback-or-reconcile→BASELINE test=integration:test_baseline_events_expose_digest_paths_and_error_semantics evidence=`.venv/bin/python -m pytest -q tests/integration/test_baseline_recalc.py`输出`passed`且status含`NEEDS_ATTENTION` IF-IMPL-001 IF-IMPL-002
- **FR-0020** owner=executor/m_impl_runtime.py:AC-FR0020-03 surface=`trac validate --file test-plan.md` composition=validate→freeze_baseline wiring=AC-layer-table→frozen_test_paths-or-error test=integration:test_baseline_events_expose_digest_paths_and_error_semantics evidence=`.venv/bin/python -m pytest -q tests/integration/test_baseline_recalc.py`输出`passed`且缺层归属exit非零 IF-IMPL-002 IF-VALIDATE-001
- **FR-0020** owner=executor/worktree.py:AC-FR0020-04 surface=`trac run` composition=freeze_baseline→worktree-composition wiring=frozen-path-set→test-authority→gate-worktree test=integration:test_three_worktree_composition_and_cleanup evidence=`.venv/bin/python -m pytest -q tests/integration/test_worktree_contract.py`输出`passed`且frozen bundle仅在gate IF-IMPL-002 IF-IMPL-006
- **FR-0030** owner=executor/taskgraph.py:AC-FR0030-01 surface=`trac run` composition=PLANNING→dispatch-Archer→validate-taskgraph wiring=tasks.json→taskgraph.committed test=integration:test_taskgraph_happy_path_reaches_public_file_and_event evidence=`.venv/bin/python -m pytest -q tests/integration/test_taskgraph_validate.py`输出`passed`且事件含task IDs IF-IMPL-001 IF-IMPL-002 IF-IMPL-003
- **FR-0030** owner=executor/taskgraph.py:AC-FR0030-02 surface=`trac run`+`trac validate --file tasks.json` composition=validate_taskgraph→retry-budget wiring=DAG|scope|coverage|issue-fail→redispatch-or-escalation test=integration:test_taskgraph_key_error_paths_fail_at_validate_cli evidence=`.venv/bin/python -m pytest -q tests/integration/test_taskgraph_validate.py`输出`passed`且第三次为escalation IF-IMPL-001 IF-IMPL-003
- **FR-0030** owner=executor/m_impl_runtime.py:AC-FR0030-03 surface=`trac report`+tasks.md composition=taskgraph.committed→projection-writer wiring=tasks.json→tasks.md+events→report test=integration:test_tasksmd_projected+test_report_progress evidence=`.venv/bin/python -m pytest -q tests/integration/test_tasklog_report.py`输出`passed`且report含per-task进展 IF-IMPL-002 IF-IMPL-007
- **FR-0040** owner=executor/m_impl_runtime.py:AC-FR0040-01 surface=`trac run`+`trac status` composition=ISLAND_GATE_1→check_island wiring=design-six-tuples→verdict.passed→PRISM_PLAN test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且status为PRISM_PLAN IF-IMPL-001 IF-IMPL-002
- **FR-0040** owner=executor/m_impl_runtime.py:AC-FR0040-02 surface=`trac run` composition=check_island→M-IMPL-reducer wiring=gap→verdict.failed(island)→PLANNING test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且事件含failed-island IF-IMPL-001 IF-IMPL-002
- **FR-0050** owner=executor/executor.py:AC-FR0050-01 surface=`trac run` composition=PRISM_PLAN→dispatch_agent wiring=assigned-criteria-pack→Prism-outcome→identity-check test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且verdict payload含pack identity IF-IMPL-001 IF-IMPL-002
- **FR-0050** owner=kernel/m_impl.py:AC-FR0050-02 surface=`trac run` composition=prism.verdict→_route_prism_plan_revise wiring=pass|revise|design-gap|requirement-gap→declared-target test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且四路事件目标正确 IF-IMPL-001 IF-IMPL-002
- **FR-0050** owner=effects/opencode.py:AC-FR0050-03 surface=`trac discuss query` composition=Prism-dispatch→discussion-audit wiring=revise→open-thread-required→verdict test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且无线程为`revise_without_findings` IF-IMPL-001
- **FR-0050** owner=executor/executor.py:AC-FR0050-04 surface=`trac run` composition=all-Prism-dispatches→criteria-pack-check wiring=plan|red|final|diagnose→assignment+outcome identity test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且四类dispatch均有pack IF-IMPL-002
- **FR-0060** owner=executor/m_impl_runtime.py:AC-FR0060-01 surface=`trac run` composition=TASK_DISPATCH→start_task wiring=ready-DAG-node→lease+manifest→task.started test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且事件含writelock与task IF-IMPL-001 IF-IMPL-002
- **FR-0060** owner=executor/m_impl_runtime.py:AC-FR0060-02 surface=`trac run` composition=start_task→IF-test-selection wiring=task.if_ids→integration-subset→next-task-or-island2 test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且选择子集可观察 IF-IMPL-001 IF-IMPL-002
- **FR-0060** owner=executor/m_impl_runtime.py:AC-FR0060-03 surface=`trac replay` composition=DAG-scheduler→serial-dispatch wiring=parallel-marker→record-only→nonoverlap-task.started test=integration:test_planning_and_dispatch_contracts_are_persisted evidence=`.venv/bin/python -m pytest -q tests/integration/test_planning_dispatch.py`输出`passed`且task.started无交错 IF-IMPL-002
- **FR-0070** owner=executor/worktree.py:AC-FR0070-01 surface=`trac run` composition=TASK_DISPATCH→create_devon_worktree→Devon-RED wiring=C_design→candidate-worktree→dispatch test=integration:test_three_worktree_composition_and_cleanup evidence=`.venv/bin/python -m pytest -q tests/integration/test_worktree_contract.py`输出`passed`且candidate无Shield tests IF-IMPL-001 IF-IMPL-002 IF-IMPL-006
- **FR-0070** owner=effects/opencode.py:AC-FR0070-02 surface=`trac run` composition=Devon-RED→Auditor wiring=manifest+isolated-root→diff-audit→done|over_reach test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且越权被拒 IF-IMPL-002 IF-DEVON-001
- **FR-0070** owner=executor/rgr.py:AC-FR0070-03 surface=events+Git-ref composition=RED_CHECKPOINT→create_red_ref wiring=test-only-diff→commit-R→refs/trac/rgr→event test=integration:test_rgr_git_contract_happy_and_immutable evidence=`.venv/bin/python -m pytest -q tests/integration/test_rgr_contract.py`输出`passed`且`git rev-parse`成功 IF-IMPL-002 IF-IMPL-004
- **FR-0070** owner=executor/rgr.py:AC-FR0070-04 surface=Git-ref composition=create_red_ref→compare-and-set wiring=existing-ref→new-attempt→old-SHA-stable test=integration:test_rgr_git_contract_happy_and_immutable evidence=`.venv/bin/python -m pytest -q tests/integration/test_rgr_contract.py`输出`passed`且前后SHA相同 IF-IMPL-004
- **FR-0070** owner=executor/worktree.py:AC-FR0070-05 surface=Git-worktrees composition=create_gate_worktree wiring=C_design+frozen-bundle+Devon-diff→gate-only test=integration:test_three_worktree_composition_and_cleanup evidence=`.venv/bin/python -m pytest -q tests/integration/test_worktree_contract.py`输出`passed`且candidate log无frozen commit IF-IMPL-002 IF-IMPL-006
- **FR-0080** owner=executor/rgr.py:AC-FR0080-01 surface=`trac run` composition=RED_GATE→classify_red wiring=pytest-result→assertion|symbol→RED_CHECKPOINT test=integration:test_m_impl_red_classification_excludes_stub_tokens evidence=`.venv/bin/python -m pytest -q tests/integration/test_rgr_contract.py`输出`passed`且stub-token不合法 IF-IMPL-002 IF-IMPL-004
- **FR-0080** owner=kernel/m_impl.py:AC-FR0080-02 surface=events+status composition=classify_red→reducer wiring=collection|syntax|fixture|import|pass→failed(red_invalid)→RED test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且事件含red_invalid IF-IMPL-001 IF-IMPL-002
- **FR-0090** owner=executor/executor.py:AC-FR0090-01 surface=`trac run`+`trac status` composition=PRISM_RED→dispatch-Prism wiring=B..R+criteria-pack→verdict test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且status为PRISM_RED IF-IMPL-001 IF-IMPL-002
- **FR-0090** owner=kernel/m_impl.py:AC-FR0090-02 surface=`trac run`+`trac discuss query` composition=prism.verdict→M-IMPL-reducer wiring=pass→GREEN|revise+thread→RED-new-attempt test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且路由及thread可见 IF-IMPL-001 IF-IMPL-002
- **FR-0100** owner=executor/m_impl_runtime.py:AC-FR0100-01 surface=`trac run` composition=GREEN→Devon-from-R wiring=r_tree_identity→dispatch→R-test-immutability-audit test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且改R测试被拒 IF-IMPL-001 IF-IMPL-002
- **FR-0100** owner=executor/worktree.py:AC-FR0100-02 surface=Git-worktree composition=Devon-outcome→controlled-rebase wiring=manifest-filter→main-worktree→cleanup-reconcile test=integration:test_three_worktree_composition_and_cleanup evidence=`.venv/bin/python -m pytest -q tests/integration/test_worktree_contract.py`输出`passed`且白名单外未回灌 IF-IMPL-002 IF-IMPL-006
- **FR-0110** owner=executor/quality_gate.py:AC-FR0110-01 surface=`trac run`+gate-events composition=GREEN_GATE→run_gates wiring=unit+history+IF-int+quality→GREEN_COMMIT|GREEN test=integration:test_quality_gate_layers_reach_public_commands_and_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_quality_gate_contract.py`输出`passed`且首轮无e2e IF-IMPL-001 IF-IMPL-002 IF-IMPL-005
- **FR-0110** owner=executor/worktree.py:AC-FR0110-02 surface=gate-worktree-events composition=run_gates→create_gate_worktree wiring=C_design+tests+candidate→pytest→attribution test=integration:test_three_worktree_composition_and_cleanup evidence=`.venv/bin/python -m pytest -q tests/integration/test_worktree_contract.py`输出`passed`且运行目录是gate IF-IMPL-002 IF-IMPL-006
- **FR-0110** owner=executor/quality_gate.py:AC-FR0110-03 surface=dispatch-assignment-feedback composition=gate-result→feedback-sanitizer wiring=unit-full|integration-classification→Devon payload test=integration:test_quality_gate_layers_reach_public_commands_and_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_quality_gate_contract.py`输出`passed`且payload无断言原文 IF-IMPL-002 IF-IMPL-005
- **FR-0110** owner=kernel/m_impl.py:AC-FR0110-04 surface=`trac status` composition=gate-failure→M-IMPL-reducer wiring=unattributed-integration-failure→DIAGNOSE test=integration:test_quality_gate_layers_reach_public_commands_and_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_quality_gate_contract.py`输出`passed`且status为DIAGNOSE IF-IMPL-001 IF-IMPL-002
- **FR-0120** owner=executor/rgr.py:AC-FR0120-01 surface=Git-commit+events composition=GREEN_COMMIT→create_green_commit wiring=impl-diff+B+R+provenance→G+trailers→event test=integration:test_rgr_git_contract_happy_and_immutable evidence=`.venv/bin/python -m pytest -q tests/integration/test_rgr_contract.py`输出`passed`且五trailer存在 IF-IMPL-002 IF-IMPL-004
- **FR-0120** owner=executor/rgr.py:AC-FR0120-02 surface=Git-ref+commit+events composition=verify_lineage wiring=R-ref+G-trailer+seq-order→proof test=integration:test_rgr_git_contract_happy_and_immutable evidence=`.venv/bin/python -m pytest -q tests/integration/test_rgr_contract.py`输出`passed`且R-seq小于G-seq IF-IMPL-002 IF-IMPL-004
- **FR-0130** owner=kernel/m_impl.py:AC-FR0130-01 surface=`trac run`+events composition=REFACTOR→Devon→REFACTOR_GATE wiring=change|no_change+reason→TASK_REVIEW test=integration:test_quality_gate_layers_reach_public_commands_and_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_quality_gate_contract.py`输出`passed`且no_change事件有reason IF-IMPL-001 IF-IMPL-002
- **FR-0130** owner=executor/quality_gate.py:AC-FR0130-02 surface=gate-events composition=REFACTOR_GATE→run_gates wiring=production-full|tests-dup-length→verdict test=integration:test_quality_gate_layers_reach_public_commands_and_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_quality_gate_contract.py`输出`passed`且checks_run符合分层 IF-IMPL-002 IF-IMPL-005
- **FR-0130** owner=kernel/m_impl.py:AC-FR0130-03 surface=events+status composition=refactor-verdict→M-IMPL-reducer wiring=public-interface→rollback|pass→review|fail→refactor test=integration:test_quality_gate_layers_reach_public_commands_and_events evidence=`.venv/bin/python -m pytest -q tests/integration/test_quality_gate_contract.py`输出`passed`且三路结果可见 IF-IMPL-001 IF-IMPL-002
- **FR-0140** owner=executor/m_impl_runtime.py:AC-FR0140-01 surface=`trac run`+status composition=TASK_REVIEW→review_task wiring=scope+secret+trace+lineage+budget→PRISM_FINAL|GREEN test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且失败check可见 IF-IMPL-001 IF-IMPL-002
- **FR-0140** owner=executor/executor.py:AC-FR0140-02 surface=`trac run`+status composition=PRISM_FINAL→dispatch-Prism wiring=full-range+lineage+pack→pass|revise-route test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且三路verdict正确 IF-IMPL-001 IF-IMPL-002
- **FR-0140** owner=executor/m_impl_runtime.py:AC-FR0140-03 surface=`trac discuss query`+`trac report` composition=Prism-final→thread-gate→task.completed wiring=pass-after-review→event→lineage-report test=integration:test_rgr_phase_events_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且report含完整lineage IF-IMPL-001 IF-IMPL-002 IF-IMPL-007
- **FR-0150** owner=kernel/m_impl.py:AC-FR0150-01 surface=`trac run`+status composition=DIAGNOSE→dispatch-Prism→route wiring=classification→GREEN|SHIELD_FIX|M-DESIGN|requirements test=integration:test_diagnose_and_shield_fix_public_routes evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且无Human测试裁定 IF-IMPL-001 IF-IMPL-002
- **FR-0150** owner=executor/executor.py:AC-FR0150-02 surface=`trac report` composition=diagnostic-verdict→event-store wiring=closed-reason→target-stage→report test=integration:test_diagnose_and_shield_fix_public_routes evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且reason属于封闭集 IF-IMPL-002 IF-IMPL-007
- **FR-0150** owner=executor/m_impl_runtime.py:AC-FR0150-03 surface=`trac run`+events composition=SHIELD_FIX→dispatch-Shield→test-commit wiring=frozen-test-paths→controlled-commit→GREEN_GATE test=integration:test_diagnose_and_shield_fix_public_routes evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且事件含test.committed IF-IMPL-002 IF-SHIELD-001
- **FR-0150** owner=executor/m_impl_runtime.py:AC-FR0150-04 surface=`trac replay` composition=rollback-stage→stale-marking wiring=upstream-return→invalidate-downstream→fresh-reentry test=integration:test_diagnose_and_shield_fix_public_routes evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且旧green未复用 IF-IMPL-002
- **FR-0160** owner=executor/m_impl_runtime.py:AC-FR0160-01 surface=`trac run`+`trac check reach` composition=ISLAND_GATE_2→check_island_2 wiring=all-tasks→reach+full-int+e2e→exit-or-hold test=integration:test_island_gate_two_requires_reach_and_full_suites evidence=`.venv/bin/python -m pytest -q tests/integration/test_island_gate_2.py`输出`passed`且失败不退出 IF-IMPL-002 IF-REACH-002
- **FR-0160** owner=kernel/m_impl.py:AC-FR0160-02 surface=`trac status` composition=island2-result→M-IMPL-reducer wiring=test-fail→DIAGNOSE|island-fail→PLANNING test=integration:test_diagnose_and_shield_fix_public_routes evidence=`.venv/bin/python -m pytest -q tests/integration/test_execution_gates.py`输出`passed`且两路状态正确 IF-IMPL-001 IF-IMPL-002
- **FR-0160** owner=kernel/machine.py:AC-FR0160-03 surface=`trac run`+`trac status` composition=ISLAND_GATE_2-pass→stage-exit wiring=stage.exited(M-IMPL)→run.completed(boundary) test=e2e:test_boundary_after_m_impl evidence=`.venv/bin/python -m pytest -q tests/e2e/test_m_impl_journey.py`输出`passed`且无M-VERIFY-entered IF-IMPL-001 IF-IMPL-002
- **FR-0160** owner=kernel/m_impl.py:AC-FR0160-04 surface=`trac replay` composition=M-IMPL-exit→program-gates wiring=gate-events→stage-exit-without-human-exit-prerequisite test=e2e:test_boundary_after_m_impl evidence=`.venv/bin/python -m pytest -q tests/e2e/test_m_impl_journey.py`输出`passed`且退出前无human.review/approval IF-IMPL-001
- **FR-0170** owner=effects/opencode.py:AC-FR0170-01 surface=`trac run` composition=dispatch-agent→AGENT_NAME→OpencodeBackend wiring=devon→materialize→act→cleanup test=integration:test_devon_dispatch_manifest_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_devon_dispatch.py`输出`passed`且role=Devon IF-DEVON-001
- **FR-0170** owner=deliverables.py:AC-FR0170-02 surface=`trac check deliverables` composition=cmd_check→check_deliverables wiring=Devon.md→frontmatter+no-permission→exit test=integration:test_real_deliverables_consistent evidence=`.venv/bin/python -m pytest -q tests/unit/test_deliverables.py`输出`passed`且Devon deliverable合法 IF-DEVON-001
- **FR-0170** owner=effects/opencode.py:AC-FR0170-03 surface=`trac run`+events composition=Devon-outcome→Auditor wiring=manifest-diff→over_reach→attributable-rollback test=integration:test_devon_dispatch_manifest_and_audit_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_devon_dispatch.py`输出`passed`且Human bytes不变 IF-DEVON-001
- **FR-0180** owner=executor/taskgraph.py:AC-FR0180-01 surface=tasks.json+tasks.md+`trac report` composition=parse-taskgraph→projection wiring=JSON→DAG-schedule+MD+events test=integration:test_tasksjson_parsed_by_runtime+test_report_rebuild evidence=`.venv/bin/python -m pytest -q tests/integration/test_tasksjson_validate.py tests/integration/test_tasklog_report.py`输出`passed`且report可重建 IF-IMPL-002 IF-IMPL-003 IF-IMPL-007
- **FR-0180** owner=executor/taskgraph.py:AC-FR0180-02 surface=`trac validate --file tasks.json` composition=validate→parse+five-checks wiring=required-fields→TaskNode-or-error test=integration:test_validate_dag+test_validate_scope+test_validate_ac_coverage+test_validate_if_validity+test_validate_issue_numbers evidence=`.venv/bin/python -m pytest -q tests/integration/test_tasksjson_validate.py`输出`passed`且缺字段exit非零 IF-IMPL-003 IF-VALIDATE-001
- **FR-0180** owner=executor/validate.py:AC-FR0180-04 surface=`trac validate --file tasks.json` composition=CLI→taskgraph-validators wiring=invalid-fixture→located-stderr→exit1 test=integration:test_five_checks_individual_failures evidence=`.venv/bin/python -m pytest -q tests/integration/test_tasksjson_validate.py`输出`passed`且五类错误含task或AC IF-IMPL-003 IF-VALIDATE-001
- **FR-0180** owner=executor/validate.py:AC-FR0180-05 surface=`trac validate`+Prism-verdict composition=Runtime-structural-check→Prism-semantic-review wiring=schema-DAG-only→no-semantic-auto-pass test=integration:test_runtime_validation_is_structural_only evidence=`.venv/bin/python -m pytest -q tests/integration/test_tasksjson_validate.py`输出`passed`且语义变化不由Runtime裁决 IF-VALIDATE-001
- **FR-0190** owner=executor/executor.py:AC-FR0190-01 surface=command.issued composition=issue-command→assignment-materializer wiring=state+docs+identity→complete-payload→backend test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且字段集完整 IF-IMPL-002
- **FR-0190** owner=kernel/m_impl.py:AC-FR0190-02 surface=`trac run` composition=escalation-router→rollback-stage wiring=M-TEST|M-IMPL-design-gap→M-DESIGN test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且target=M-DESIGN IF-IMPL-001 IF-IMPL-002
- **FR-0190** owner=project.py:AC-FR0190-03 surface=`trac run` composition=contract-path→design-checkpoint wiring=`.tracks/project/project.toml`→dedup-stage→load test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且其它.tracks合同拒绝 IF-IMPL-002
- **FR-0190** owner=executor/validate.py:AC-FR0190-04 surface=`trac validate --file test-plan.md` composition=design-trace-parser→IF-registry wiring=canonical-headings+rows→test_tasks-or-fail test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且坏header/duplicate/IF均失败 IF-IMPL-002 IF-VALIDATE-001
- **FR-0190** owner=executor/executor.py:AC-FR0190-05 surface=command.issued composition=Shield-WRITE→test_tasks-enrichment wiring=§8-rows→nonempty-assignment-or-stub_gap test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且无效时backend未调用 IF-IMPL-002
- **FR-0190** owner=executor/result_checkpoint.py:AC-FR0190-06 surface=events+test-diff composition=outcome→ResultCheckpoint wiring=pre-dirty-snapshot+requires-diff→checkpoint-or-retry test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且unchanged-Human未混入 IF-IMPL-002
- **FR-0190** owner=executor/executor.py:AC-FR0190-07 surface=test.collected-event composition=collect_tests→module-filter wiring=all-py-checkpoint→test-module-collect→fail-if-helper-only test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且conftest不单独no-tests IF-IMPL-002
- **FR-0190** owner=project.py:AC-FR0190-08 surface=test.collected-event composition=host-command-resolver→subprocess wiring=project-venv-or-runtime-venv→shell-false test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且不调用系统Python IF-IMPL-002
- **FR-0190** owner=executor/executor.py:AC-FR0190-09 surface=`trac replay` composition=rollback-handler→failure-evidence-policy wiring=M-TEST-stub-gap-clear|other-preserve→event test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且scope证据保留 IF-IMPL-002
- **FR-0190** owner=executor/executor.py:AC-FR0190-10 surface=command.issued+events composition=all-agent-dispatches→shared-materializer wiring=role/substate→complete-payload→Runtime-actions test=integration:test_dispatch_assignments_have_complete_public_payload evidence=`.venv/bin/python -m pytest -q tests/integration/test_dispatch_materialization.py`输出`passed`且声明角色/动作全覆盖 IF-IMPL-002
- **FR-0200** owner=executor/executor.py:AC-FR0200-01 surface=`trac run`+replay composition=_recover→event-project→reconcile wiring=restart→pending-phase→resume-without-duplicate test=integration:test_replay_does_not_duplicate_completed_task_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_crash_recovery.py`输出`passed`且已完成事件计数不变 IF-IMPL-002
- **FR-0200** owner=executor/rgr.py:AC-FR0200-02 surface=Git-ref+commit composition=recover→verify_lineage wiring=persisted-R-ref+G-trailers→rebuild-binding test=integration:test_rgr_git_contract_happy_and_immutable evidence=`.venv/bin/python -m pytest -q tests/integration/test_rgr_contract.py`输出`passed`且重启前后R SHA相同 IF-IMPL-002 IF-IMPL-004
- **FR-0210** owner=executor/validate.py:AC-FR0210-01 surface=test-plan.md+tasks.json composition=design-trace→ownership-check wiring=layers→Shield-scope|unit-Devon-scope test=integration:test_testplan_not_copied_to_tasksjson evidence=`.venv/bin/python -m pytest -q tests/integration/test_testplan_ownership.py`输出`passed`且tasks无环境复制 IF-IMPL-007 IF-VALIDATE-001
- **FR-0210** owner=executor/validate.py:AC-FR0210-02 surface=CLI+events+files+Git composition=public-outlets→design-trace-validator wiring=AC→IF→observable-exit test=integration:test_test_refs_traceable_to_section8 evidence=`.venv/bin/python -m pytest -q tests/integration/test_testplan_ownership.py`输出`passed`且无私有观察引用 IF-IMPL-003 IF-IMPL-007
- **FR-0210** owner=executor/validate.py:AC-FR0210-03 surface=test-plan.md-§8 composition=IF-registry→test-selector wiring=AC-row.if_ids→task.if_ids→integration-subset test=integration:test_ac_coverage_cross_consistent evidence=`.venv/bin/python -m pytest -q tests/integration/test_testplan_ownership.py`输出`passed`且归属非空有效 IF-IMPL-003 IF-IMPL-007
- **FR-0210** owner=executor/m_impl_runtime.py:AC-FR0210-04 surface=`trac run`+test.committed composition=DIAGNOSE→SHIELD_FIX wiring=Shield-manifest→allowed-test-diff→controlled-commit test=integration:test_if_attribution_cross_consistent evidence=`.venv/bin/python -m pytest -q tests/integration/test_testplan_ownership.py`输出`passed`且越权写失败 IF-IMPL-002 IF-SHIELD-001
- **FR-0210** owner=executor/executor.py:AC-FR0210-05 surface=command.issued composition=Shield-WRITE→test_tasks-injection wiring=§8-machine-rows→assignment→Shield test=integration:test_ground_truth_not_in_tasksjson evidence=`.venv/bin/python -m pytest -q tests/integration/test_testplan_ownership.py`输出`passed`且Shield不自衍归属 IF-IMPL-002 IF-IMPL-007
- **FR-0210** owner=executor/validate.py:AC-FR0210-06 surface=test-plan.md+tasks.json composition=design-baseline→taskgraph-reference-validator wiring=environment+fixtures-in-plan→test_refs-only-in-taskgraph test=integration:test_frozen_test_refs_immutable_in_m_impl evidence=`.venv/bin/python -m pytest -q tests/integration/test_testplan_ownership.py`输出`passed`且职责不重叠 IF-IMPL-002 IF-IMPL-007
- **FR-0220** owner=baseline.py:AC-FR0220-01 surface=`trac run` composition=freeze_baseline→issue-digest wiring=issue-number+spec-ref→freshness→BASELINE-status test=integration:test_issue_number_in_tasksjson evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_consumption.py`输出`passed`且issue元数据变化不消费 IF-IMPL-003 IF-VALIDATE-001
- **FR-0220** owner=executor/taskgraph.py:AC-FR0220-02 surface=tasks.json composition=PLANNING→taskgraph-scheduler wiring=issues-readonly-map→independent-task-DAG test=integration:test_dispatch_payload_carries_provenance evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_consumption.py`输出`passed`且Issues不是DAG节点 IF-IMPL-002 IF-IMPL-003
- **FR-0220** owner=executor/rgr.py:AC-FR0220-03 surface=command.issued+Git-commit composition=taskgraph→Devon-assignment→green-commit wiring=issue+AC provenance→payload→trailers test=integration:test_g_commit_trailers_contain_provenance evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_consumption.py`输出`passed`且Git message含Issue和AC IF-IMPL-002 IF-IMPL-004 IF-DEVON-001
- **FR-0220** owner=executor/taskgraph.py:AC-FR0220-04 surface=`trac validate --file tasks.json` composition=validate_issue_numbers→read-only-consumption wiring=issue-ref→validate-only→no-GitHub-write test=integration:test_missing_issue_fails evidence=`.venv/bin/python -m pytest -q tests/integration/test_issue_consumption.py`输出`passed`且无Issue创建/更新事件 IF-IMPL-002 IF-IMPL-003
- **FR-0230** owner=executor/live_evidence.py:AC-FR0230-01 surface=`TRAC_AGENT_BACKEND=opencode trac run` composition=current-wheel→Executor→bind_live_evidence wiring=BASELINE→real-Devon-RGR→review→ISLAND_GATE_2→boundary test=e2e:test_real_opencode_m_impl_rgr_release_evidence evidence=`.venv/bin/python -m pytest -q -rs tests/e2e_live/test_m_impl_release_evidence.py`输出`1 passed`且required phases齐全 IF-LIVE-001 IF-RELEASE-001
- **FR-0230** owner=report.py:AC-FR0230-02 surface=`trac report`+Git+bundle composition=event-store→report+evidence wiring=run→events→R/G→gates→boundary test=e2e:test_real_journey_exposes_events_lineage_report_and_boundary evidence=`.venv/bin/python -m pytest -q -rs tests/e2e_live/test_m_impl_release_evidence.py`输出`passed`且report含完整序列 IF-LIVE-001
- **FR-0230** owner=executor/live_evidence.py:AC-FR0230-03 surface=`trac run`+canonical-evidence composition=failure-path→no-success-write wiring=failed|cancelled|incomplete|fake→reject→no-bundle test=integration:test_incomplete_failed_cancelled_and_fake_never_write_success evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且无satisfied bundle IF-LIVE-001 IF-RELEASE-001
- **FR-0231** owner=executor/live_evidence.py:AC-FR0231-01 surface=canonical-evidence-bundle composition=boundary→bind→atomic-write wiring=run+backend+I/O+events+lineage+candidate→JSON+blobs test=e2e:test_real_journey_writes_complete_auditable_bundle evidence=`.venv/bin/python -m pytest -q -rs tests/e2e_live/test_m_impl_release_evidence.py`输出`passed`且schema字段完整 IF-LIVE-001 IF-RELEASE-001
- **FR-0231** owner=executor/live_evidence.py:AC-FR0231-02 surface=canonical-evidence-bundle composition=provenance-gate→write-live-evidence wiring=fake|simulation|overlay|manual|incomplete→reject test=integration:test_provenance_rejects_fake_simulation_overlay_manual_events_and_incomplete_io evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且非真实均无标记 IF-LIVE-001 IF-RELEASE-001
- **FR-0232** owner=checks/release_evidence.py:AC-FR0232-01 surface=`trac check release-evidence` composition=cmd_check→file-wrapper→pure-check wiring=current-HEAD→latest-bundle+blobs+Git→report test=integration:test_check_accepts_latest_current_real_auditable_bundle evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且exit0 IF-RELEASE-001
- **FR-0232** owner=checks/release_evidence.py:AC-FR0232-02 surface=`trac check release-evidence` composition=file-wrapper→current-head-compare wiring=live-candidate-SHA→HEAD-equality→stale test=integration:test_check_rejects_stale_and_candidate_sha_mismatch evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且新commit后reason=stale IF-RELEASE-001
- **FR-0232** owner=cli/main.py:AC-FR0232-03 surface=`trac check release-evidence [--json]` composition=ReleaseEvidenceReport→renderer wiring=ok→exact-text|canonical-JSON→exit0 test=integration:test_check_success_text_json_and_exit_contract evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且stdout逐字节匹配 IF-RELEASE-001
- **FR-0232** owner=cli/main.py:AC-FR0232-04 surface=`trac check release-evidence [--json]` composition=fail-report→renderer wiring=closed-reason→next-action→exit1 test=integration:test_check_fail_closed_reason_precedence_text_json_and_exit evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且全部reason fail-closed IF-RELEASE-001
- **FR-0232** owner=checks/release_evidence.py:AC-FR0232-05 surface=`trac check release-evidence` composition=read-only-wrapper wiring=read-evidence+Git→report-only test=integration:test_check_is_read_only_and_emits_no_verify_release_publish_effect evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且events/refs/worktree字节不变 IF-RELEASE-001
- **FR-0233** owner=tests/e2e_live/conftest.py:AC-FR0233-01 surface=CI-routine-live-job composition=pytest-credential-probe wiring=missing-secret→`LIVE_SKIPPED`→other-jobs-continue test=e2e:test_routine_missing_credentials_reports_live_skipped_without_evidence evidence=`.venv/bin/python -m pytest -q -rs tests/e2e_live/test_m_impl_release_evidence.py`无凭据输出`LIVE_SKIPPED: missing`且exit0 IF-LIVE-001
- **FR-0233** owner=.github/workflows/ci.yml:AC-FR0233-02 surface=CI-live-opencode-job composition=build-wheel→e2e-live→release-check wiring=credentials+opt-in→real-journey→bundle→satisfied test=e2e:test_real_opencode_m_impl_rgr_release_evidence evidence=CI `live-opencode`输出`1 passed`与`status=satisfied` IF-LIVE-001 IF-RELEASE-001
- **FR-0233** owner=.github/workflows/ci.yml:AC-FR0233-03 surface=CI-release-evidence-job composition=milestone-job→trac-check wiring=current-candidate→fail-closed-check→gate test=integration:test_routine_skip_and_fake_never_satisfy_release_prerequisite evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且skip/fake exit1 IF-RELEASE-001
- **FR-0234** owner=executor/doc_comment.py:AC-FR0234-01 surface=`trac run`+events composition=dispatch-outcome→doc-comment-first wiring=pre-dispatch-docs→classify→pause-before-validation test=integration:test_legal_discussion_pauses_before_all_ordinary_validation evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且detected后无普通成功事件 IF-DOCGAP-001 IF-QUARANTINE-001
- **FR-0234** owner=executor/doc_comment.py:AC-FR0234-02 surface=`trac discuss query` composition=COMMENTABLE_DOCS→delta-classifier wiring=role-fixed-docs+baseline-identity→new-thread-set test=integration:test_role_comment_scope_and_predispatch_threads_do_not_retrigger evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且既有thread不触发 IF-DOCGAP-001
- **FR-0234** owner=cli/main.py:AC-FR0234-03 surface=`trac status`+`trac discuss query`+`trac replay`+`trac report` composition=event-projection→CLI-renderers wiring=record+quarantine→visible-state+thread+audit test=e2e:test_design_discussion_adjudication_resume_happy_path evidence=`.venv/bin/python -m pytest -q tests/e2e/test_doc_comment_journey.py`输出`passed`且status含origin/quarantine IF-DOCGAP-001 IF-QUARANTINE-001
- **FR-0234** owner=executor/executor.py:AC-FR0234-04 surface=`trac run`+`trac status` composition=outcome-precheck→ordinary-validation wiring=no-doc-delta→existing-pipeline test=integration:test_outcome_without_new_discussion_uses_original_validation_path evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且status无doc-gap IF-DOCGAP-001
- **FR-0235** owner=executor/doc_comment.py:AC-FR0235-01 surface=`trac discuss query`+`trac status`+`trac report` composition=AWAITING_ADJUDICATION→Prism-dispatch wiring=original-thread→design_gap→Archer-route test=e2e:test_design_discussion_adjudication_resume_happy_path evidence=`.venv/bin/python -m pytest -q tests/e2e/test_doc_comment_journey.py`输出`passed`且无human审批事件 IF-DOCGAP-001
- **FR-0235** owner=executor/doc_comment.py:AC-FR0235-02 surface=`trac discuss query`+`trac status` composition=Prism-verdict→agent-correction wiring=original-thread→instruction→remain-paused-until-closed test=integration:test_agent_correction_stays_paused_until_original_thread_closes evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且未闭环无普通验证 IF-DOCGAP-001
- **FR-0235** owner=executor/executor.py:AC-FR0235-03 surface=`trac run`+command.issued composition=READY_TO_RESUME→resume-dispatch wiring=closed-thread→restore|discard→new-dispatch-attempt→full-revalidation test=e2e:test_design_discussion_adjudication_resume_happy_path evidence=`.venv/bin/python -m pytest -q tests/e2e/test_doc_comment_journey.py`输出`passed`且dispatch_id变化/attempt递增 IF-DOCGAP-001 IF-QUARANTINE-001
- **FR-0236** owner=executor/doc_comment.py:AC-FR0236-01 surface=`trac replay`+Git/worktree composition=legal-discussion→quarantine_authorized_changes wiring=agent-attributable-manifest-diff→blob-descriptor→held test=integration:test_quarantine_excludes_human_predirty_and_shared_index evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且Human bytes/index不变 IF-QUARANTINE-001 IF-DOCGAP-001
- **FR-0236** owner=executor/doc_comment.py:AC-FR0236-02 surface=`trac status`+events composition=quarantine-empty-or-held→pause wiring=no-authorized-change→empty|change→held→no-success test=integration:test_empty_and_held_quarantine_never_checkpoint_before_resume evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且无commit/gate/task完成 IF-QUARANTINE-001
- **FR-0236** owner=kernel/machine.py:AC-FR0236-03 surface=`trac status`+`trac replay`+`trac report` composition=event-replay→doc-gap-projection wiring=restart→events+blobs→same-held/restored/discarded-state test=integration:test_restart_rebuilds_doc_gap_and_quarantine_state evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且重启前后投影相等 IF-QUARANTINE-001 IF-DOCGAP-001
- **FR-0236** owner=executor/doc_comment.py:AC-FR0236-04 surface=`trac run`+`trac replay` composition=decide_quarantine_resume→new-dispatch wiring=current-identities→restore|stale/conflict→discard→revalidate test=integration:test_resume_restores_current_and_discards_stale_without_touching_predirty evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且reason与新attempt可见 IF-QUARANTINE-001 IF-DOCGAP-001
- **FR-0237** owner=effects/opencode.py:AC-FR0237-01 surface=`trac run`+Git/worktree composition=post-write-audit→atomic-rollback wiring=illegal-body-even-with-thread→rollback-all-agent-changes→no-Prism test=integration:test_illegal_body_edit_atomically_rolls_back_entire_outcome evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且Human/pre-dirty逐字节不变 IF-DOCGAP-001 IF-QUARANTINE-001
- **FR-0237** owner=executor/executor.py:AC-FR0237-02 surface=`trac status`+`trac replay`+`trac report` composition=outcome.rejected→failure-retry wiring=over_reach+paths+atomic→new-same-phase-attempt test=integration:test_illegal_edit_reports_paths_and_redispatches_new_attempt evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且旧outcome无success IF-DOCGAP-001
- **FR-0237** owner=report.py:AC-FR0237-03 surface=`trac replay`+`trac report` composition=rejection-event→audit-renderer wiring=failure+rollback-evidence→persistent-report test=integration:test_illegal_edit_failure_evidence_survives_replay evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_first.py`输出`passed`且失败路径可重放 IF-DOCGAP-001
- **NFR-0010** owner=kernel/m_impl.py:AC-NFR0010-01 surface=`trac run`+events composition=decide/project→pure-command-state wiring=input-state+event→output-without-I/O test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且外部事实仅经事件进入 IF-IMPL-001
- **NFR-0010** owner=kernel/machine.py:AC-NFR0010-02 surface=`trac status`+events composition=executor-side-effects→store.append→project wiring=I/O-result→event→rebuild test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且drop/rebuild一致 IF-IMPL-001 IF-IMPL-002
- **NFR-0020** owner=store/store.py:AC-NFR0020-01 surface=events-table composition=executor→EventStore.append wiring=M-IMPL-fact→new-seq-row→projection test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且既有行hash不变 IF-IMPL-001 IF-IMPL-002
- **NFR-0020** owner=kernel/machine.py:AC-NFR0020-02 surface=`trac status`+`trac report` composition=events→projector→read-model wiring=drop-projection→replay→same-output test=integration:test_m_impl_public_event_lifecycle evidence=`.venv/bin/python -m pytest -q tests/integration/test_m_impl_cycle.py`输出`passed`且输出相同 IF-IMPL-001 IF-IMPL-002
- **NFR-0030** owner=executor/executor.py:AC-NFR0030-01 surface=`trac run`-stdout composition=dispatch-agent→activity-logger wiring=start→flushed-line→completion+duration test=integration:test_retry_activity_and_failure_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_trac_retry.py`输出`passed`且无海量agent stdout IF-IMPL-002
- **NFR-0030** owner=cli/main.py:AC-NFR0030-02 surface=`trac run`+`trac status` composition=attempt-events→status-renderer wiring=third-failure→attempt+class+reason test=integration:test_retry_activity_and_failure_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_trac_retry.py`输出`passed`且status含attempt/failure IF-IMPL-001
- **NFR-0030** owner=cli/main.py:AC-NFR0030-03 surface=`trac retry` composition=cmd_retry→store.append(human.retry) wiring=escalation→reset-budget-preserve-evidence→no-auto-dispatch test=integration:test_retry_activity_and_failure_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_trac_retry.py`输出`passed`且非escalation exit非零 IF-IMPL-001
- **NFR-0080** owner=checks/release_evidence.py:AC-NFR0080-01 surface=`trac check release-evidence --json` composition=pure-validator+read-only-wrapper wiring=canonical-bytes+Git→deterministic-report test=integration:test_check_is_deterministic_auditable_and_rejects_event_only_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且重复输出逐字节相同 IF-LIVE-001 IF-RELEASE-001
- **NFR-0080** owner=checks/release_evidence.py:AC-NFR0080-02 surface=`trac check release-evidence` composition=provenance-validator→fail-closed-renderer wiring=skip|fake|simulate→missing|not_real→exit1 test=integration:test_non_real_sources_neither_produce_nor_satisfy_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_release_evidence.py`输出`passed`且无`satisfied` IF-LIVE-001 IF-RELEASE-001
- **NFR-0090** owner=store/store.py:AC-NFR0090-01 surface=events-table+`trac replay`+`trac report` composition=doc-gap-actions→append-events→project wiring=origin+thread+quarantine+next-attempt→ordered-audit test=integration:test_restart_rebuilds_doc_gap_and_quarantine_state evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且drop/rebuild状态一致 IF-DOCGAP-001 IF-QUARANTINE-001
- **NFR-0090** owner=executor/executor.py:AC-NFR0090-02 surface=events+Git/worktree composition=pause/recover→fail-closed-guards wiring=unclosed-or-unvalidated→no-gate/commit/success+no-index-pollution test=integration:test_interruption_never_leaks_quarantine_past_gates evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且无关task/Human bytes不变 IF-DOCGAP-001 IF-QUARANTINE-001
- **NFR-0090** owner=kernel/machine.py:AC-NFR0090-03 surface=`trac replay`+command.issued composition=event-replay→resume-state→new-dispatch wiring=old-outcome→never-success→new-attempt-full-validation test=integration:test_replay_never_promotes_old_outcome_or_duplicates_success evidence=`.venv/bin/python -m pytest -q tests/integration/test_doc_comment_quarantine.py`输出`passed`且task.completed不重复 IF-DOCGAP-001 IF-QUARANTINE-001

## 2. Scaffold 宣言

- `.tracks/projects/project.toml` — 当前 DRAFT Runtime 可提交/读取的宿主测试执行合同；FR-0190 目标路径迁移到 `.tracks/project/project.toml` 属待实现 foundation 工作（kind: config）
- `pyproject.toml` — 已有 build/pytest/coverage/ruff/pylint 配置（kind: config）
- `.flake8` — 已有 CCR001 认知复杂度阈值配置；本 revision 继承并核对（kind: config）
- `.githooks/pre-commit` — 已有八类守卫本地执行骨架；Runtime 负责 hooksPath 生效（kind: config）
- `.github/workflows/ci.yml` — 已有 CI 骨架；声明 routine required checks 及待 Devon 补全的 live/release jobs（kind: ci-skeleton）
- `tracks/executor/taskgraph.py` — 已存在的 IF-IMPL-003 interface scaffold；本 revision 不改写其业务行为（kind: stub）
- `tracks/executor/rgr.py` — 已存在的 IF-IMPL-004 interface scaffold；本 revision 不改写其业务行为（kind: stub）
- `tracks/executor/worktree.py` — 已存在的 IF-IMPL-006 interface scaffold；本 revision 不改写其业务行为（kind: stub）
- `tracks/executor/quality_gate.py` — 已存在的 IF-IMPL-005 interface scaffold；本 revision 不改写其业务行为（kind: stub）
- `tracks/executor/live_evidence.py` — 已存在的 live evidence schema 与 `bind_live_evidence`/`write_live_evidence` 接口桩（kind: stub）
- `tracks/checks/release_evidence.py` — 已存在的 `ReleaseEvidenceReport` 与 release checker 接口桩（kind: stub）
- `tracks/executor/doc_comment.py` — DocCommentOrigin/DocumentDelta/DocGapRecord/QuarantineDescriptor/ResumeDecision 声明与分类、裁定、隔离、恢复决策接口桩；行为体只 raise 对应 IF token（kind: stub）

> **Prism:** PRISM-DESIGN-001 [severity=blocker] [artifact=architecture.md §2 Scaffold 宣言 lines 352-355] [criterion=8+9+7] 合同真实性与 Scaffold 宣言不一致：architecture.md §2 将以下 4 个模块标为 interface scaffold（kind: stub），但它们的实际文件包含来自先前 M-IMPL cycle 的完整业务实现（fa19f11 commit M-IMPL implement T-01~T-05），且这些实现能通过现有 integration 测试：tracks/executor/taskgraph.py（23 个 def，0 NotImplementedError）、rgr.py（21 def，0 NIE）、worktree.py（11 def，0 NIE）、quality_gate.py（15 def，0 NIE）。仅 live_evidence.py/release_evidence.py/doc_comment.py 是真正 stub（raise NotImplementedError）。其后果：M-IMPL 对 IF-IMPL-003/004/005/006 的 RED 阶段无法建立合法红——实现已存在且测试通过，Devon 写的 unit tests 会直接 pass（unexpected_pass → illegal red 循环），任务无法推进。预期修订：（a）将 taskgraph/rgr/worktree/quality_gate 还原为正确 stub（raise NotImplementedError），或（b）在 Scaffold 宣言中诚实声明它们包含先前 cycle 的完整实现，并在 M-IMPL baseline 冻结/任务规划中提供处理已有实现的机制（如 baseline 冻结后仅验证性测试，不通过 RED 实现）。

本节只声明 M-DESIGN 物理脚手架。`tests/integration/test_release_evidence.py`、`tests/integration/test_doc_comment_first.py`、`tests/integration/test_doc_comment_quarantine.py`、`tests/e2e/test_doc_comment_journey.py` 与 `tests/e2e_live/test_m_impl_release_evidence.py` 不在宣言中，因为它们是 Shield 的测试交付物，不是 Archer scaffold。

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
- **分层机制**：quality_gate.py 按 changed_paths 分类文件为生产代码（tracks/ 下非 tests/）或测试代码（tests/ 下），对生产代码执行完整检查集、对测试代码执行受限检查集。pre-commit hook 的分层：ruff check 跑 tracks+tests、flake8 只跑 tracks、pylint R0801/C0302 只跑 tracks（tests exempt）、pylint R0915/R0914 只跑 tracks；pylint 全部加 `--exit-zero`（advisory only）。quality_gate.py 继承 BS-11 分层政策但对 changed delta 可以更严格（不使用 `--exit-zero`）。
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
2. **canonical 路径**：foundation task 将 project.py 与 checkpoint 白名单从 bootstrap `.tracks/projects/project.toml` 迁移并强化为只接受 `.tracks/project/project.toml`；其他 `.tracks/**` 路径仍 fail closed。
3. **M-DESIGN 输出合同**：validate.py 的 `check_design_trace` 扩展为解析 test-plan `## 8. AC Coverage` 与 interfaces `## 5. IF Registry` canonical headers。v0.4 已建立 IF- 校验，v0.5 强化 header 解析与 fail-closed 规则。
4. **test_tasks 注入**：executor.py 的 `_do_dispatch_agent` 扩展，向 Shield assignment 注入 `test_tasks`（v0.4 已部分实现，v0.5 强化无效输入 fail closed → `stub_gap`）。
5. **ResultCheckpoint**：result_checkpoint.py 强化 `pre_dirty_snapshot` 持久化与 post 内容身份比较（v0.4 已实现 ResultCheckpoint，v0.5 扩展 dispatch flag 清理与 unchanged Human dirty files 排除）。
6. **collection 口径**：executor.py 的 `_do_collect_tests` 扩展 collection 规则（conftest/helper 可 checkpoint 但 collect-only 只对 test modules；只有 helper 时 fail closed；conftest 不得被单独判 no-tests）。
7. **host project command**：project.py 的 `load_contract` 强化 `.venv/bin/python` 回退逻辑（不存在时回退当前 Runtime venv `sys.executable`，不得系统 Python）。
8. **rollback evidence**：executor.py 的 rollback handler 增补规则（仅 M-TEST `stub_gap`→M-DESIGN 清 stale failure，其他语义回退保留 evidence）。
9. **dispatch materialization 完整性**：executor.py 的 `_do_dispatch_agent` 扩展 assignment payload 物化字段（绝对 target doc/doc-set、role/substate/attempt/review_round、docs/templates/skills、criteria-pack identity、test_tasks、pre_dirty_snapshot、result/checkpoint identity）。
10. **覆盖范围**：横切覆盖所有 agent dispatch + Runtime validate/checkpoint/publish/collect/run/red/commit/seal。

### 3.7 live evidence 与 release-evidence（FR-0230～FR-0233/NFR-0080）

- **生产/消费分离**：`executor/live_evidence.py` 在真实 journey boundary 生产 immutable bundle；`checks/release_evidence.py` 只读消费。放弃“从 `run.completed` 单事件推断成功”，因为人工插入 event 无法证明真实 dispatch、agent I/O 和 Git lineage；代价是 bundle schema 较严格。
- **canonical 存储**：`.tracks/runtime/release-evidence/v1/{candidate_sha}/{run_id}/evidence.json`；同目录 `blobs/{sha256}` 保存 evidence 引用的脱敏 agent input/output 与 canonical event slice。文件先写同目录临时文件、`fsync` 后 `os.replace`，失败不留下可被选择的 `evidence.json`。bundle 可从隔离 demo host逐字节传输到候选 checkout 的同一相对路径；传输不改变 digest。
- **候选绑定**：`candidate_sha` 是 wheel build 前 current tracks checkout 的完整 `git rev-parse HEAD`；候选构建命令为 `.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .`，不新增 build frontend 依赖；`candidate_artifact.sha256` 是安装到 demo host 的 wheel SHA-256；安装探针记录 distribution version 与 import path 不在源码树。二者缺失或不匹配即不可发布。放弃 short SHA（碰撞且不能等值核验）。
- **launch provenance**：live harness 只在 wheel hash 校验与隔离安装探针通过后，向安装版 `trac run` 注入 `TRAC_LIVE_CANDIDATE_SHA`/`TRAC_LIVE_ARTIFACT_SHA256`；Runtime 将二者与安装环境的 `direct_url.json` archive hash、distribution/version/import path 交叉核验后才允许 binder。普通 real run 未提供这两个字段时照常运行，但不产生 release evidence。checker 最终再把 candidate SHA 与待发布 checkout current HEAD 比较；这些字段不是 agent assignment，Agent 不能修改。
- **真实性**：producer 从实际 backend instance 记录 `backend="opencode"`，并冻结 `TRAC_FAKE_SIMULATE` 是否存在、`assignment_overlay` 是否启用。任一 fake/simulate/overlay 为真时 producer 不写 satisfied bundle。validator 还要求每个 RED/GREEN/REFACTOR 都存在 `command.issued` dispatch receipt、匹配的 `outcome.received`、完整 agent I/O blob/digest；仅有阶段 event 的手工记录必然缺 receipt/blob 而失败。
- **独立门禁**：bundle 的 `gate_observations` 必须分别记录 RED_GATE、GREEN_GATE、REFACTOR_GATE、TASK_REVIEW、PRISM_FINAL、ISLAND_GATE_2 的 Runtime 观察值与 seq；不得用 Devon self-report 代替。review 还要求真实 Prism dispatch/outcome。完整 schema 与 reason precedence 见 interfaces.md §1j/§2d/§3h。
- **技术栈**：仅 Python 标准库 `dataclasses/json/hashlib/pathlib/os` 与既有 SQLite/git wrapper；不引入签名库或远端 attestation。取舍：本版防止 fake/simulation/event-only 伪证据，但不声称抵抗拥有仓库与凭据写权限的恶意管理员；密码学供应链证明属于未来发布系统且不在本 spec。

### 3.8 doc-comment-first 与 quarantine（FR-0234～FR-0237/NFR-0090）

- **前置分类**：新增 `executor/doc_comment.py`，复用 `discuss/parser.py` 的 canonical parser 与 `executor/file_identity.py` 的内容身份；不自行写第二套 markdown parser。纯函数只产出 interfaces.md §1k/§1l 类型，文件回滚、blob 持久化和 dispatch 均留在既有 effects/executor/store 边界。
- **为什么独立小模块**：当前 `executor.py`、`m_impl_runtime.py` 与 `effects/opencode.py` 已接近或超过文件长度守卫，且该能力横切 M-DESIGN、M-TEST、M-IMPL outcome。把可测试规则放入 `doc_comment.py`，而在既有 composition root 只做适配，避免继续扩张单体 handler；放弃在 `ResultCheckpointMixin` 内实现，因为 M-IMPL outcome 不全走该 pipeline，无法满足“所有普通验证之前”。
- **隔离载体**：复用 `EventStore.write_audit_blob` 的 content-addressed、blob-first/event-second 原子语义。quarantine descriptor 只引用 manifest/content blob digest，不创建 Git commit/ref，也不暂存到共享 index。放弃 `git stash`（依赖共享 index/worktree且可能混入 Human/pre-dirty）；放弃长期临时 worktree（恢复与清理复杂、仍需独立内容身份）。
- **归因与原子性**：复用 Auditor 的 pre-dispatch byte snapshot、agent_changed_paths 与 rollback_agent_changes。非法正文先于合法评论判定，整回合 force rollback；合法评论只把 manifest 授权的非文档 Agent diff 序列化，Human/pre-dirty 仅作为排除与冲突身份，不复制进 blob。
- **恢复**：design identity 取 architecture/interfaces/test-plan 当前 body identity，run identity 取 origin run/baseline/result identity，path identity 取恢复前工作树。任一 stale/conflict 默认 discard；current 才可 restore，但 restore 后也只是新 dispatch/attempt 的输入，旧 outcome 不发布成功事件。放弃自动三方 merge，因 Spec 要求 stale 时安全丢弃或完整重验，自动 merge 会改变数据后果。
- **裁定**：Prism assignment 含 record_id/origin/original thread IDs；其 outcome 只允许 `design_gap|agent_correction`。design_gap 路由 Archer RESPOND；agent_correction 路由原 Devon/Shield。两路都以原线程 resolved 为 READY_TO_RESUME 条件，不增加 Human gate。
- **风险**：崩溃可能落在 blob 与事件之间。沿用 store 的 blob-first/event-second，孤立 blob 不构成状态；事件一旦提交其 blob 必存在。恢复/丢弃副作用必须 command-id 幂等，重复 replay 不重复创建 dispatch 或 `task.completed`。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（DRAFT bootstrap：`.tracks/projects/project.toml`）

当前执行本 assignment 的 Runtime 事实是 `paths.project_toml_path()`、设计 checkpoint 白名单与既有 tracked contract 均指向 `.tracks/projects/project.toml`；它会拒绝把 `.tracks/project/project.toml` 作为 scaffold。故 M-DESIGN DRAFT 物理交付并由当前 Runtime 消费前一路径，避免把无法提交的文件伪称既有合同。FR-0190/AC-FR0190-03 的产品终态仍是 `.tracks/project/project.toml`：Devon foundation task 必须在同一切片中迁移 `paths.project_toml_path`、checkpoint 白名单、loader 与 contract 文件，删除旧路径，并由 `test_dispatch_materialization.py` 证明只有新路径被接受。迁移完成前，当前 bootstrap 路径不是 AC-FR0190-03 已通过的证据。

合同声明 integration/e2e 的 framework、paths、collect/run 命令和 cwd。collect 命令用于 M-TEST 测试收集阶段（SM-01.5），run 命令用于 RED_CHECK（SM-01.9）与 M-IMPL GREEN_GATE int 子集（SM-01.22）。命令通过 shlex.split + subprocess(shell=False) 执行，cwd 相对于宿主项目根目录。

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

### 4.2 质量守卫栈（完整类别；BS-11 分层由 pre-commit 分命令执行 + quality_gate.py 程序执行继承）

| # | 守卫 | 工具（pinned） | 配置位置 | 阈值 | 执行点 |
|:---|:---|:---|:---|:---|:---|
| 1 | lint | ruff==0.16.0 | `pyproject.toml` `[tool.ruff]`/`[tool.ruff.lint]` | `ruff check` 零 error；line-length=100；select E,F,W,I,B,UP,SIM,C4 | pre-commit + CI `lint` |
| 2 | 认知复杂度 | flake8==7.3.0 + flake8-cognitive-complexity==0.1.0 | `.flake8` | CCR001 ≤ 15（生产代码）；tests/ 豁免（pre-commit 只跑 tracks） | pre-commit + CI `lint` |
| 3 | 重复度 | pylint==4.0.6 | `pyproject.toml` `[tool.pylint.similarities]` | R0801，min-similarity-lines=5（tracks only；tests exempt） | pre-commit（`--exit-zero`）+ CI `lint` |
| 4 | 文件长度 | pylint | `[tool.pylint.format]` | C0302，max-module-lines=1200（tracks only；tests exempt；`--exit-zero`）；新 `doc_comment.py` 目标 ≤500 | pre-commit + CI `lint` |
| 5 | 方法长度/局部变量 | pylint | `[tool.pylint.design]` | R0915 max-statements=50；R0914 max-locals=15（生产代码）；tests/ 豁免 | pre-commit（`--exit-zero`）+ CI `lint`（tests 豁免 R0915/R0914） |
| 6 | 覆盖率门槛 | coverage==7.15.2 + pytest==9.1.1 | `pyproject.toml` `[tool.coverage.*]` | CI `coverage report --fail-under=95` | CI `coverage` |
| 7 | 钩子运行器 + CI required checks | git hooks + GitHub Actions | `.githooks/pre-commit` + `.github/workflows/ci.yml`（workflow 待补全） | hook 按序执行 ruff/flake8/pylint（pylint `--exit-zero`）；CI `lint/coverage/test/deliverables/trace/reach` 全部 required | 本地提交 + merge 门禁 |

v0.5 增补的分层执行（BS-11）：
- pre-commit hook 在 v0.4 基础上调整：pylint R0801/C0302 改为 tracks only（tests exempt），与 R0915/R0914 一致；pylint 全部加 `--exit-zero` 使警告可见但不阻塞提交（pylint 对任何 R/C 类消息返回非零退出码，`set -e` 会拦截）。ruff check 和 flake8 保持严格。
- GREEN_GATE/REFACTOR_GATE 中 `quality_gate.py` 按 changed_paths 分类文件后分别执行检查集（程序执行，与 pre-commit hook 继承同一 BS-11 分层政策）。

安装命令（Archer 写入合同；执行属 Runtime 生效副作用）：

```
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
git config core.hooksPath .githooks
```

### 4.3 CI 合同（`.github/workflows/ci.yml`，ci-skeleton 待 Devon 补全为真实 workflow）

stable required checks（名称稳定，merge 只认 CI 结论）：

1. `lint`：ruff check（tracks/tests）+ flake8 tracks（含 CCR001，tests/ 豁免）+ pylint R0801/C0302（tracks only，tests exempt）+ pylint R0915/R0914（tracks only）；与 pre-commit 同序执行。
2. `coverage`：`coverage run -m pytest tests/unit tests/integration tests/e2e -q -m 'not performance' && coverage combine && coverage report --fail-under=95`；明确排除 `tests/e2e_live`。
3. `test`：`pytest tests/unit tests/integration tests/e2e -q -m 'not performance'`（unit + integration + e2e fake 通道；明确排除 live）。
4. `deliverables`：`trac check deliverables`（存在性 + version + IQ，含 Devon.md）。
5. `trace`：`trac check trace --json`（既有子命令；需求追踪闭合）。
6. `reach`：`trac check reach --json`（既有子命令；模块可达性）。
7. `release-evidence`：仅 tag/release candidate 的硬门禁，先运行真实 `tests/e2e_live/test_m_impl_release_evidence.py`，再对当前 HEAD 执行 `trac check release-evidence --json`；缺凭据、skip、fake 或 stale 均失败，不能以 Human approval 绕过。该 job 只验证证据，不发布。

live 通道（`tests/e2e_live/`）：routine pull request/push 中为独立 opt-in、非 required job；缺凭据时必须输出 `LIVE_SKIPPED: missing <NAME>` 且不生成 bundle。`workflow_dispatch` 可显式运行。tag/release candidate 的 `release-evidence` job 是 milestone hard gate，缺凭据必须 fail 而非 skip；另设每周 schedule 运行真实通道防腐烂。secrets 只注入该 job，权限 `contents: read`，不得写 release/publish。

现有 `.github/workflows/ci.yml` 是 Archer 已物化的 ci-skeleton；`trac check trace`/`reach` 子命令已经存在，但 skeleton 仍以 `continue-on-error`/`|| true` 绕过它们，且 release/live jobs 尚缺。**待 Devon foundation task**：补全 `setup-python`、安装，移除绕过，加入稳定 `release-evidence` milestone job 与 `live-opencode` weekly/manual job。Runtime 负责绑定 required check、secrets 和触发，不由 Devon 执行生效。routine required DAG 为 `lint + coverage + test + deliverables + trace + reach`；milestone DAG 在这些成功后运行 `release-evidence`。

激活 `lint` 前的 Devon foundation task 还包括：把当前 1340 行的 `tracks/kernel/machine.py` 沿既有 `kernel/` 增长轴拆分到 `max-module-lines=1200` 以下；不得以调高阈值替代。上述为待实现，当前 skeleton 不是通过证据。

### 4.4 integration/e2e 测试基础设施

- **fake 通道**（deterministic，CI 必跑）：conftest 强制 `TRAC_AGENT_BACKEND=fake`。FakeBackend 经 `simulate` 控制 Devon/Archer/Prism/Shield 分支。继承 v0.2 conftest 的 `host_repo`/`trac`/`event_log` fixtures。
- **L2 contract sim**（opencode stand-in）：继承 v0.2 `fake_opencode` fixture，验证 Devon dispatch 的物化/JSON 解析/manifest 越界审计/失败矩阵。
- **live 通道**（真 opencode）：唯一新 happy path 节点为 `tests/e2e_live/test_m_impl_release_evidence.py::test_real_opencode_m_impl_rgr_release_evidence`；从 current candidate wheel 安装到隔离 demo host，不从源码树 import，不使用 `--assignment-overlay`，执行至少一个 task 的真实 Devon RED/GREEN/REFACTOR 和 Prism review，断言独立 gates、TASK_DONE、ISLAND_GATE_2、boundary 与 bundle。routine 缺凭据显式 skip；tag/release 不允许 skip。
- **确定性 negative 通道**：`tests/integration/test_release_evidence.py` 覆盖 missing/malformed/stale/SHA mismatch、FakeBackend、`TRAC_FAKE_SIMULATE`、assignment overlay、manual event-only、agent I/O 缺失、lineage/gate/boundary 缺失以及 text/JSON/exit 稳定性；这些 AC 不与真实 live happy path 重复。
- **doc-comment-first 通道**：`tests/integration/test_doc_comment_first.py` 覆盖 role 文档范围、pre-dispatch 基线、优先级、Prism 两路裁定、非法正文原子拒绝与普通路径回归；`tests/integration/test_doc_comment_quarantine.py` 覆盖 empty/held、Human/pre-dirty/index 隔离、重启重放、current restore/stale discard 与旧 outcome 不成功；`tests/e2e/test_doc_comment_journey.py` 只覆盖面向操作者的 E-04 happy path。
- **精确测试文件增量**：Shield 新建 `tests/integration/test_release_evidence.py` 与 `tests/e2e_live/test_m_impl_release_evidence.py`，修改 `tests/e2e_live/conftest.py`（显式 skip 文本）和 `tests/e2e_live/harness.py`（current wheel 安装证明及 bundle byte-for-byte 传输）；不新建模拟 scenario JSON，因为该 journey 禁止 assignment overlay/simulation。
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
- **outcome 横切顺序回归**：doc-comment-first 必须早于 M-DESIGN/M-TEST ResultCheckpoint 与 M-IMPL 普通验收，而非法正文又必须早于合法讨论。缓解：composition root 只在 `_dispatch_agent_backend` 的单一 outcome 接收点接线；integration 用可观察事件证明优先级，并对无文档变化路径做回归。
- **隔离数据污染**：错误归因可能带入 Human/pre-dirty 或共享 index。缓解：Auditor pre-dispatch bytes 是排除 authority；descriptor 逐路径记录 baseline/content identity；测试在有 staged Human 文件与 pre-dirty 内容时比较 byte/index identity。
- **live 成本与不稳定外部 provider**：真实完整 RGR 显著慢于 fake suite。缓解：只覆盖一个最小纵向 task；routine 独立 opt-in，weekly/tag 执行；release candidate 不接受 skip。
- **本地 bundle 可被高权限操作者篡改**：本版通过 content digest、backend provenance、dispatch receipt、event slice、Git lineage 和独立 gate 多源交叉核验，拒绝 fake/simulation/manual-event-only；不提供远端签名 attestation。若威胁模型升级为恶意管理员，需独立供应链签名需求，不能在本版架构中猜补。
- **stale 选择歧义**：validator 只在完整校验后从 current SHA 目录按 `run_id` 字节序选择最大成功 run；current SHA 无 bundle但存在旧 SHA bundle时固定返回 `stale`，完全无 bundle返回 `missing`，避免依赖文件 mtime/系统时钟。
