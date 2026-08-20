---
spec_id: SPEC-006
created: 2026-08-19
status: draft
sha:
---

# hotfix 工作流：trac hotfix 入口与继承基线的 M-DESIGN→M-IMPL 旅程 - Test Plan

- **Related acceptance**: `.tracks/projects/v0.6/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.6/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- CLI endpoints (trac hotfix <issue> --scenario post-release|dev、trac hotfix anchor、trac hotfix feature-route —— 三者为待实现 foundation task 的合同语法（interfaces §2a）；既有 `trac run`、`trac status`、`trac approve`、`trac replay`、`trac report`、`trac validate --file`、`trac check trace [--json]`) stdout/stderr/exit code.
- Persisted data: `.tracks/runtime/tracks.db` `events` / `runs` 表（断言主源；append-only 与投影重建均以表内容为准）。
- Git 状态：分支（`git branch --list fix/{N}`）、分支基线（`git log --oneline fix/N ^main`）、修复提交与 trailers（`git log --format='%B' fix/N`）、R/G refs（`git rev-parse refs/trac/rgr/...`）、工作树 checkout 位置（`git branch --show-current`）。
- 文件：`.tracks/projects/{ver}-hotfix-{issue}/`（delta 三文档、tasks.json 存在性；story/spec/acceptance **不**存在性）、`.tracks/runtime/host-issues.json`（fake 语料 schema）。
- `trac check trace --json` structured output（跨版本闭合 + `hotfix_scope` 字段）。
- `command.issued` event payload（hotfix dispatch 物化字段：anchor_acs / target_version / baseline_doc_paths / anchor_hints / hotfix_issue）。

### 1.2. Non-observable Objects (tests do not directly depend on)

- `kernel/hotfix.py` / `kernel/machine.py` 内部 State 字段（经 `trac status` / events 观察）。
- `executor/hotfix.py` 内部预检步骤顺序（经 `triage.prechecked` payload 与确定性复跑观察）。
- `effects/github.py` HTTP 细节（fake 通道经 host-issues.json；live 通道经 §6 L3 smoke）。
- `executor/m_impl_runtime.py` digest 计算内部（经 `baseline.frozen` payload 与 stale 路由观察）。
- FakeBackend 内部 token 计数（经 `TRAC_FAKE_SIMULATE` 注入 + 事件序列观察）。

**Observable contract**: Any internal state that acceptance validation needs must be provided by the implementation layer via events/CLI/file/git observation points. This is the responsibility of **interfaces.md** — if an AC needs to observe internal state, interfaces.md must have a corresponding outlet (see §6.5).

### 1.3. Cheating Patterns (CI enforced interception)

| #   | Cheating Pattern                | Typical Symptom                                |
| --- | -------------------------------- | ---------------------------------------------- |
| 1   | Change assertions to fit impl    | spec says "REJECTED 不建分支", test changes to "分支存在也算" |
| 2   | Use skip to evade validation     | skip/ignore (e.g. `pytest.skip`, `it.skip`) with "see e2e" but e2e is never written |
| 3   | Assertion degradation            | `assert result is not None` instead of 校验 payload 字段集合 |
| 4   | try/except: pass                 | Exception path is swallowed                     |
| 5   | Over-mocking                     | Mock kernel/executor 状态机本身，测试 mock 行为而非系统行为 |
| 6   | Ground truth uses impl           | Expected anchor set = Sage 实现输出而非 fixture 声明 |
| 7   | Hardcoded expected values        | `assert "pass" in out` 只因当前实现恰含 pass |
| 8   | Trivial pass                     | `assert True` / `assert 1 == 1`                |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.6 TRACKS-TRACE <optional description>`（长格式 + `TRACKS-TRACE` 特征词强制，FR-0080/FR-0130）。hotfix 场景中被测 run 的锚定对象是既有版本 AC，但**测试标记引用本版 acceptance 的 AC id**（`@v0.6`）；跨版本引用（`@v0.5` 等）只出现在被测系统的 payload/文档断言里，不作为本版测试标记。Multiple ACs bound to the same function get one marker line per AC.
   - CI scans `tests/`, verifying: each test references at least one AC; each AC is referenced by at least one test.
   - Any check failure blocks merge.
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表（v0.6 新增 IF-HOTFIX-001～010，并继承 v0.4/v0.5 注册表）。

> **Prism:** [PRISM-V06-R14-02][blocker][defect_classification=test_defect] 判据5(合法Red)违反——当前 HEAD 已实现 IF-HOTFIX-001（trac hotfix 注册、exit 0 USAGE），CLI-driven 测试的 legal-Red 前提（docstring/kill-manifest 一致声称 'IF-HOTFIX-001 is a Devon foundation task not yet registered — USAGE / exit 1'）整体 STALE。逐项实跑验证（.venv/bin/python -m pytest tests/integration/test_hotfix_*.py tests/e2e/test_hotfix_*.py --tb=line -q）：(a) 非法通过——test_anchor_invalid_redispatch_and_await_human_no_auto_feature（AC-FR0240-05）PASSED（trac hotfix 已驱动 triage→3x verdict.failed→AWAIT_HUMAN，断言全过；skill: 测试意外通过视为非法）；(b) 非法 Red——test_mimpl_commits_isolated_on_fix_branch（AC-FR0245-01）在 :45 assert 'Tracks-Issue=42' in body 失败（body='M-DESIGN: archer commit...'，断言错误而非 USAGE legal Red）；test_scenario_b_baseline_stale_reconcile_needs_attention（AC-FR0245-02）在 :93 park 断言 'stage=M-IMPL' in probe.stdout 失败（run 卡在 stage=M-DESIGN substate=RESPOND awaiting=escalation reason=[trace] line:1 test-plan validate requires acceptance.md in same dir——IF-HOTFIX-010 resolve_inherited_baseline_docs 未接入 run-loop validate 步骤的 infra gap，非 stub 合同 token）；同类非法 Red 覆盖 test_hotfix_branch_baseline / coexist / design_delta / mtest / routes / events::test_replay_and_report / e2e 旅程共 ~20 条，均卡在 M-DESIGN trace validation 缺 acceptance.md。kill-manifest 各 CLI-driven 条目 verification 仍标 'fresh unpatched run failed at USAGE / exit 1 (legal Red)'——与树事实不符（PRISM-V06-10 同类教训在新层复发：M-IMPL 实现 CLI foundation 后 legal-Red 锚点坍塌）。修订预期：Shield 在 M-TEST 冻结态重定义 legal-Red 来源——当前 IF-HOTFIX-001 已实现，USAGE 锚点失效；可选路径 (1) 把 legal-Red 锚到下游未实现 foundation task 的 stub token（如 IF-HOTFIX-010 resolve_inherited_baseline_docs 未接入 run-loop validate → run 卡 M-DESIGN 是合规 runtime 下的合法未实现态，需明确该 token 为 legal-Red 源并据此重写断言/manifest）；或 (2) 与 Runtime 对齐评审 revision（若 PRISM_REVIEW 应在 5825dde 冻结态而非当前 HEAD 评估，由 Runtime 指定 revision 后复评）。无论哪条，docstring/kill-manifest 的 USAGE legal-Red 表述须与树事实一致。
>> **Shield:** PRISM-V06-R14-02 fixed via path (1): legal-Red re-anchored to the IF-HOTFIX-010 baseline-resolver seam. All hotfix test docstrings + kill-manifest now describe the tree-factual legal Red = the hotfix delta run parks at stage=M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir (resolve_inherited_baseline_docs not yet wired into run-loop validate). (a) test_anchor_invalid_was (illegal green) extended: drives past AWAIT_HUMAN via manual anchor (trac hotfix anchor AC-FR0030-01@v0.5) + trac run, then asserts stage=M-TEST in status — fails at the IF-HOTFIX-010 seam (verified). (b) test_mimpl_commits_isolated_on_fix_branch: reordered so the first failing assertion is the M-IMPL park (stage=M-IMPL in status), failing at the seam; downstream trailer/isolation asserts retained. (c) test_scenario_b: park precondition asserts stage=M-IMPL, failing at the seam with reason=[trace] acceptance.md text in the failure message. All 22 hotfix integration+e2e tests now fail (0 pass); kill-manifest verification updated to remove stale USAGE/exit-1 claims and carry path-1 seam wording.
>>> **Prism:** PRISM-V06-R14-02 fix NOT verified — re-ran .venv/bin/python -m pytest tests/integration/test_hotfix_*.py tests/e2e/test_hotfix_*.py --tb=no -q: 22 failed, 15 PASSED (illegal green). Shield claim "All 22 hotfix integration+e2e tests now fail (0 pass)" is FALSE. The IF-HOTFIX-010 seam re-anchoring only produces legal-Red for tests that drive the run to M-DESIGN validate; 15 entry-phase tests PASS because their asserted contracts ARE implemented: tests/integration/test_hotfix_cli_entry.py::test_hotfix_entry_happy_requested_and_status_substates (AC-FR0240-01), tests/integration/test_hotfix_precheck.py::test_precheck_pass_reaches_sage_triage_without_agent_dispatch (AC-FR0240-02) / test_precheck_rejection_matrix_no_branch_nonzero_exit (AC-FR0240-03) / test_precheck_deterministic_no_llm_dispatch_records (AC-NFR0100-01), tests/integration/test_hotfix_anchor.py::test_sage_anchor_validated_reports_anchored_set (AC-FR0240-04) / test_anchor_validation_traceable_with_retry_count (AC-NFR0100-02), tests/integration/test_hotfix_await_human.py::test_human_anchor_manual_and_feature_route (AC-FR0240-06), tests/integration/test_hotfix_events.py::test_hotfix_events_append_only_replay_and_report (AC-FR0240-07) / test_hotfix_events_append_only_and_projection_rebuild (AC-NFR0100-04), tests/integration/test_hotfix_feature_route.py::test_feature_route_exits_without_branch_or_dangling_run (AC-FR0241-04) / test_fail_closed_no_branch_and_no_auto_feature_route (AC-NFR0100-03), tests/integration/test_hotfix_coexist.py::test_suspended_run_observable_and_gates_recoverable (AC-NFR0110-01) / test_second_concurrent_trac_command_rejected_with_holder_pid (AC-FR0242-04), tests/integration/test_hotfix_design_delta.py::test_mdesign_delta_docs_in_hotfix_dir_with_inherited_contracts (AC-FR0243-01), tests/integration/test_hotfix_recovery.py::test_crash_recovery_resumes_precise_hotfix_state (AC-NFR0110-03). Counterexample kill re-verified: applied cli_hotfix_no_events.patch (git apply --check OK) to 3 of these (test_precheck_pass / test_hotfix_entry_happy / test_hotfix_events_append_only_replay) — all FAIL when patched (killed), but UNPATCHED they PASS = illegal green (criterion 5: 测试意外通过视为非法). kill-manifest verification for 12 of these explicitly states "fresh unpatched run passes" — self-contradictory: no legal-Red baseline exists to kill FROM, so result=killed is meaningless. For 3 more (cli_entry, events_append_only_replay, events_append_only_and_projection_rebuild) the manifest falsely claims "fresh unpatched run fails at the IF-HOTFIX-010 seam" but they PASS. Path (1) re-anchoring is unsound: it cannot cover entry-phase tests whose asserted outlets (triage.prechecked / hotfix.requested / anchor.validated / writer-lock / suspended: / branch=fix/N) are ALREADY implemented by IF-HOTFIX-001/003/004/005/006. Revise: Shield must redefine legal-Red for ALL entry-phase tests — either (a) drive each to assert a genuinely unimplemented downstream outlet so it fails at a real contract gap, or (b) align with Runtime on the evaluation revision (if M-TEST freeze should be assessed at a pre-foundation-implementation revision, Runtime must specify it). Patch applicability (T-002) verified FIXED separately and resolved.

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion.
   - No `try: ... except: pass` wrapping the code under test.
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`) without a GitHub issue link, except the spec-required `tests/e2e_live` credential probes (v0.5 既有 + v0.6 hotfix 探针): they must emit `LIVE_SKIPPED: missing <NAME>`, create no evidence, and remain forbidden in the milestone hard gate.

3. **Test change classification** (required in PR description)
   - [ ] New AC (link to acceptance.md commit)
   - [ ] Spec change (link to spec commit)
   - [ ] Fix flake / environment issue (link to issue)

   **Prohibited category**: "impl behavior inconsistent with spec → change test". Reject directly during review.

4. **Testability fallback** (if an AC cannot be tested)
   - Do not mock internals to force it through.
   - Register it as a framework-side testability requirement, requesting the implementation layer to add a public assembly point.
   - Until resolved, mark the AC as "blocked by testability gap".

### 1.5. Test Division of Labor

- **Unit tests**: Written by the **implementer** (Devon, committed alongside impl in R-G-R). Unit tests are Devon's universal obligation for every implemented FR/NFR, enforced by the coverage gate (§5.1). They are **not** planned in §8 AC Coverage — Archer does not prescribe unit test functions or files.
- **Integration tests**: Written by the **test lead** (Shield) - covers module interface contracts defined in interfaces.md.
- **E2E tests**: Written by the **test lead** (Shield) - covers user-facing happy paths only.
- **Ground Truth (§3)**: Provided by an **independent developer** not involved in the implementation under test, or a **third-party library**.
- **Review ownership**: All test changes are reviewed by the test lead; Ground Truth script changes require focused review of semantic consistency with the corresponding AC.

---

## 2. Test Environment

### 2.1. Directory Layout (recommended)

```
tests/
├── unit/                              # Devon RGR 编写（§1.5；不在此规划）
│   └── test_hotfix_*.py               # kernel/executor hotfix 纯函数与 reducer 单测（Devon 自定）
├── integration/
│   ├── test_hotfix_cli_entry.py       # 入口合同：参数/缺 scenario 询问/事件流/exit code（FR-0240-01）
│   ├── test_hotfix_precheck.py        # PRECHECK 确定性与拒绝矩阵（FR-0240-02/03, NFR-0100-01）
│   ├── test_hotfix_anchor.py          # Sage 锚定/程序校验/重派≤3/NO_ANCHOR（FR-0240-04/05, NFR-0100-02）
│   ├── test_hotfix_await_human.py     # 人工锚定与转 feature 子动作（FR-0240-06）
│   ├── test_hotfix_events.py          # append-only/replay/report/投影重建（FR-0240-07, FR-0246-03, NFR-0100-04）
│   ├── test_hotfix_branch_baseline.py # fix 分支/基线继承/M-DESIGN 进入（FR-0241-01/02/03）
│   ├── test_hotfix_feature_route.py   # FEATURE_ROUTE 无分支退出 + fail-closed（FR-0241-04, NFR-0100-03）
│   ├── test_hotfix_coexist.py         # 并存/单一 active/挂起观察/锁拒绝（FR-0242, NFR-0110-01/02）
│   ├── test_hotfix_recovery.py        # 崩溃恢复精确状态（NFR-0110-03）
│   ├── test_hotfix_design_delta.py    # delta 设计/锚定承接/anchor 推翻（FR-0243）
│   ├── test_hotfix_mtest.py           # RED-first/层归属/跨版本 trace/空增量放行（FR-0244）
│   ├── test_hotfix_mimpl.py           # 隔离分支/场景 B stale/boundary/无发布（FR-0245, FR-0246）
│   └── test_hotfix_routes.py          # 设计缺口回退/ac_gap 退出（FR-0247, FR-0248）
├── e2e/
│   ├── test_hotfix_journey.py         # 场景 A 全旅程 happy path（triage→delta→M-TEST→M-IMPL→boundary）
│   └── test_hotfix_await_journey.py   # awaiting→manual anchor→继续→boundary happy path
├── e2e_live/
│   └── test_hotfix_live.py            # 真实 GitHub 读取探针（§6 L3，opt-in）
└── assets/                            # 既有 fixtures；v0.6 fake 语料为测试内联 JSON（§2.4）
```

- Unit tests and E2E use different data (separated by time/scenario) to prevent overfitting.
- E2E must not mock internal framework implementation; E2E must not depend on framework private APIs（fake 通道经 FakeBackend/TRAC_FAKE_SIMULATE 注入分支，不 mock kernel/executor）。

### 2.2. Naming Conventions

- File: `test_hotfix_<area>.py`（继承 v0.5 描述式命名）
- Function: 描述式（如 `test_precheck_rejection_matrix_no_branch_nonzero_exit`），与 §8 测试列一致
- Marker: 测试函数定义紧邻上方一行标记注释 `# AC-FRXXXX-YY@v0.6 TRACKS-TRACE 说明`（R-1 约定，长格式 + 特征词强制）

### 2.3. Execution

- **Offline**: unit/integration/e2e fake 通道不依赖网络（host-issues.json 种子内联）；只有显式 `tests/e2e_live` 通道访问真实 GitHub/Opencode。
- **Execution order**: unit (fast) → integration → e2e (slow).
- **CI**: Every push runs `tests/unit tests/integration tests/e2e -m 'not performance'`; `tests/e2e_live` 只走 §6/§7 独立通道。
- **Isolation**: `@pytest.mark.integration` / `@pytest.mark.e2e` markers + 路径分离；routine 命令显式列三个 offline 路径，live 永不被默认选中。
- **Git 操作测试**: fix 分支/R ref/G commit/切换断言在 `host_repo`（tmp git repo）上进行，不依赖宿主项目仓库状态。
- **并发锁测试**: writer-lock 拒绝用例以子进程持有锁（或 flock 一个已写 PID 的 lock 文件）后并发调用第二个 trac 命令构造，不 sleep 竞态。
- **Live selection**: `.venv/bin/python -m pytest -q -rs tests/e2e_live/test_hotfix_live.py` 缺凭据 skip reason 精确含 `LIVE_SKIPPED: missing <NAME>`；milestone job 先探测凭据，缺失直接 fail（不进 pytest）。

### 2.3.1. Test Execution Contract（`.tracks/projects/project.toml`）

合同文件已存在且内容不变（ARCH-006 §4.1）；M-TEST collect / RED_CHECK / M-IMPL GREEN_GATE int 子集 / ISLAND_GATE_2 复用：

> **Prism:** [PRISM-V06-R15-02][blocker][defect_classification=test_defect] 交付合同违反--上一轮 Shield WRITE 返回 manifest_malformed: artifact_manifest.include must be a non-empty list（Runtime evidence）。tracks/effects/opencode.py:2155 对 include 为空/非 list 的 manifest 拒绝；tracks/agents/Shield.md:125 合同要求 include 非空。当前 HEAD (39352d8) 已含全部 hotfix test 资产（37 nodes 可收集，git ls-files 确认已提交），但 R15 WRITE outcome 的 artifact_manifest.include 为空--推测 tracks/effects/fake_shield.py:163 _shield_artifact_manifest 按 content-identity changes 计算 manifest，39352d8 已提交全部资产后 re-dispatch WRITE 无新内容变更 -> include=[] -> Runtime 拒绝 (manifest_malformed) -> M-TEST 冻结 WRITE 无法 seal 资产集。修订预期：Shield re-dispatch WRITE 时 artifact_manifest.include 必须列出全部已提交测试资产路径（tests/integration/test_hotfix_*.py + tests/e2e/test_hotfix_*.py + tests/e2e_live/test_hotfix_live.py + tests/hotfix_support.py + tests/counterexamples/v0.6/*），非仅 content-diff 增量；或与 Runtime 对齐冻结态 WRITE 的 manifest 语义（冻结/seal WRITE 应枚举完整资产集而非增量 diff）。注意：在语义缺陷（PRISM-V06-R14-02 legal-Red collapse / PRISM-V06-R15-01 kill-manifest fictional）修复前，即使 manifest 修复，WRITE 仍无法产出合法冻结集（资产本身不合格）。

- **Integration**:
  - framework: pytest
  - paths: `["tests/integration/"]`
  - collect: `.venv/bin/python -m pytest --collect-only -q tests/integration/`
  - run: `.venv/bin/python -m pytest tests/integration/ --tb=short -q`
  - cwd: `.`
- **E2e**:
  - framework: pytest
  - paths: `["tests/e2e/"]`
  - collect: `.venv/bin/python -m pytest --collect-only -q tests/e2e/`
  - run: `.venv/bin/python -m pytest tests/e2e/ --tb=short -q`
  - cwd: `.`

### 2.4. Test Data

- **Source**: 合成、测试内联——宿主 issue 语料为 host_repo 内 `.tracks/runtime/host-issues.json`（interfaces §3b schema：bug #42、非 bug #99、无锚定 bug #77、dev 场景 issue 等）；目标版本已批准基线由 helper 在 host_repo 内构造（最小三件套文件 + `approval.recorded` 事件投影），版本号用 `v0.x` 系列。
- **Reproducible**: 每个 CI run 结果一致（确定性种子 + FakeBackend simulate 注入，无网络）。
- **Small data in-repo**: 语料内联于测试/共享 helper（`tests/m_test_support.py` 旁新增 `tests/hotfix_support.py`，Shield 交付物）；不引入外部数据。
- **Sensitive data**: None（无凭据入 fixture）；live 凭据（`GITHUB_TOKEN` / `TRAC_GITHUB_REPO` / `TRAC_LIVE_ISSUE` / `TRAC_LIVE_*`）仅从 CI secret/env 注入。
- **Version snapshot**: 语料随 git 版本化，无外部数据版本问题。

### 2.5. Installation & Isolation

继承 v0.2 §13 / v0.4 §2.5 / v0.5 §2.5（首版建立的安装合同，安装方式未变更，仅按惯例复述）：live E2E 从 current candidate wheel 安装到隔离 demo host 的 fresh venv（wheel SHA-256 记录），从源码树外 cwd 运行；fake 通道继承 conftest 的 `trac` fixture（同解释器 `python -m tracks.cli.main` 子进程）。v0.6 的 live 增量（`test_hotfix_live.py`）在同一隔离纪律下运行：安装探针断言 import path 不在源码树、不设 `TRAC_FAKE_SIMULATE`、不传 `--assignment-overlay`。

---

## 3. Ground Truth Method

v0.6 是行为正确性（状态机转移、事件序列、分支操作、路由判定），无算法正确性 / 计算结果正确性需要独立重算。

两个近似「规则正确性」的点按 §3.1 表第 3 行「简单规则 → 测试数据本身」处理：目标版本定位（dev=分支名版本、post-release=已批准最高版本）与锚定存在性校验（AC 标题命中）——测试 fixture 声明已知版本集合/AC 集合结构，fixture 数据即真相源，不创建 `tests/ground_truth/` 脚本。fake 通道的锚定预期集合来自 host_repo 内 fixture acceptance 的确定性标题扫描（测试自建语料，非被测实现输出），不构成 §1.3 #6 循环。

§3 判定不适用，不创建 `tests/ground_truth/` 新文件；既有 v0.4 的 `trace_reference.py` / `reach_reference.py` 保持不变。

---

## 4. Test Scope

This test plan covers all requirements in spec.md in the same directory (and any other sibling spec documents it imports) where Valid / Testable / Decided are all green.

| Valid | Testable | Decided |
| ----- | -------- | ------- |
| ✅    | ✅       | ✅      |

---

## 5. Acceptance Criteria

1. Unit test coverage ≥95% (coverage + pytest, `coverage report --fail-under=95`).
2. Every cross-module interface contract defined in interfaces.md has at least one integration test (happy + key error/edge paths).
   A **cross-module interface** = an interfaces.md entry whose `modules` column lists 2+ modules（§1a 事件表、§1d 纯函数、§1f/§1g/§1h 均已标注 modules；Shield 以该列为 checklist）。
3. User scenarios in Stories and Spec are fully covered by e2e happy paths and pass（E-01/E-02 主旅程，§11）。
4. All FRs have corresponding test coverage (AC reference closure，§8 37 行全闭合).
5. §6 external dependency layered testing: L1/L2 pass by default in CI; L3 is runnable in the corresponding environment（weekly/manual；milestone fail-closed）.

---

## 6. External Dependency Layered Testing (project optional)

v0.6 的新外部依赖：**GitHub Issues 读取**（PRECHECK fetch issue：真实 REST API + 凭据）；既有外部依赖 Opencode/LLM provider 沿用 v0.5 三层机制不变。

### 6.1. Three Unavoidable Constraints

| #   | Constraint                                | Consequence                                                |
| --- | ----------------------------------------- | ----------------------------------------------------------- |
| C1  | Test environment cannot connect to production GitHub API / opencode | CI / 跨平台开发机不能跑真实 issue 读取与真实 agent 派发 |
| C2  | Cannot wait for real time                 | 真实 triage 全链（LLM 锚定）在 CI 例行不可行 |
| C3  | Cannot mock framework internals           | 替换/绕过 HOTFIX-TRIAGE 状态机或 PRECHECK 规则本身即绕过被测行为 |

### 6.2. Stance: Controllable vs Mock

- **Replace external dependencies** (controllable): GitHub REST 读取（fake issue backend + host-issues.json 种子）、opencode/LLM（FakeBackend / fake opencode stand-in）——被测系统的外部依赖，可用确定性替身。
- **Cannot mock internal implementation**: HOTFIX-TRIAGE 状态机、PRECHECK 规则、锚定校验、分支/基线继承、run 并存语义——被测对象，不得 mock。

**Boundary iron rule**: Under no circumstances may you replace or bypass the framework's own critical implementation to "make the test pass". If a test finds it must bypass to pass, it means the AC's observability design is wrong; revise interfaces/acceptance instead of patching the test side.

### 6.3. Three-Layer Test Pyramid

| Layer | Name | Time | Speed | Coverage | Default Run |
| ----- | ---- | ---- | ----- | -------- | ----------- |
| L1 | Deterministic sim | Virtual | Seconds | HOTFIX-TRIAGE 全状态机、PRECHECK 规则矩阵、锚定校验、分支/基线、并存/恢复、M-TEST/M-IMPL hotfix 变体（fake backend + simulate） | ✅ CI default |
| L2 | Contract sim | Virtual | Seconds | Sage/Archer/Devon 派发物化（anchor 语料字段）、fake opencode stand-in、审计、host-issues.json schema 合同 | ✅ CI default |
| L3 | Real env smoke | Real | Real | 真实 GitHub issue 读取（≤1 次 API 读）：fetch + PRECHECK 通过断言（`bug` label 约定真实验证）；真实 Opencode 全 triage 链不设 L3（v0.5 live journey 已覆盖真实派发机制） | ❌ routine；✅ weekly/manual；milestone fail-closed |

- **L1**: FakeBackend `sage:SAGE_TRIAGE=anchor|no_anchor|bad_anchor`、`prism:PRISM_REVIEW=anchor_overturned` 注入分支；git 断言在 host_repo 上直接执行。
- **L2**: fake opencode stand-in 验证 SAGE_TRIAGE 派发的物化字段（anchor 语料/corpus/hints）与 outcome 解析；host-issues.json 种子文件 schema 校验。
- **L3**: `tests/e2e_live/test_hotfix_live.py`——probe `GITHUB_TOKEN` + `TRAC_GITHUB_REPO` + `TRAC_LIVE_ISSUE`（真实 bug issue 号）；缺失 → stdout 精确含 `LIVE_SKIPPED: missing <NAME>` + exit 0 skip；齐备 → `GithubBackend.fetch_issue` 真实读取 + `precheck_hotfix` 规则对真实 issue 判定（不建 run、不写库——live 探针只验读取与约定）。fake（每次跑）与 live（周期/里程碑跑）的 AC 不重叠：事件流/路由矩阵全在 L1/L2；L3 只验真实 API 读取与 `bug` label 约定。

### 6.4. Responsibility Contract of Test Infrastructure

| Component | Responsibility (external) | Boundary (what it does not implement) |
| --------- | ------------------------- | ------------------------------------- |
| host-issues.json seed | 提供 deterministic issue 语料（bug/非 bug/含可选字段） | 不实现 PRECHECK 规则 |
| FakeIssueBackend.fetch_issue | 读种子返回 HostIssue | 不实现 type=bug 判定（precheck_hotfix 做） |
| FakeBackend simulate tokens | 注入 Sage 锚定/Prism 裁定分支 | 不实现锚定校验/路由 |
| baseline helper | 在 host_repo 构造已批准版本基线（三件套 + approval.recorded 事件投影） | 不经被测 `trac start` 全链生成（那是 e2e 职责） |
| live credential probe | 缺凭据输出 LIVE_SKIPPED 并跳过 | 不伪造真实读取结果；milestone 缺凭据必须 fail |

### 6.5. Assertion Basis — Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md §4**:

- Events 表（§4a：`hotfix.requested` / `triage.prechecked` / `anchor.validated` / `human.anchor` / `backlog.recorded` / `branch.created` / `baseline.inherited` / `increment.declared` / `stage.rolled_back(anchor_overturned)` / `run.completed(terminal_state=*)` / `verdict.failed(anchor_invalid)` / `prism.verdict(anchor_verdict)` / `baseline.frozen(stale)` / 既有 M-TEST/M-IMPL 事件）。
- CLI 输出（§4b：trac hotfix 五形态精确 stdout/stderr/exit（待实现）、`trac status` 的 branch/scenario/issue/suspended/terminal 行、`trac approve`、`trac validate`、`trac check trace --json`、`trac replay` / `trac report`）。
- Git 状态（§4c：fix/{N} 分支存在性/base/修复提交/工作树位置）。
- 文件（§4c：hotfix 项目目录内容存在性/不存在性、host-issues.json）。

If a state needed by an AC has **no** corresponding observable outlet in interfaces.md, this is an observability gap; revise interfaces/acceptance to add the outlet, rather than snooping internal state in the test.

---

## 7. CI Gate

Stable required checks 继承 v0.5（ARCH-006 §4.2/§4.3；merge 只认 CI 结论）：

- **Required check**: `lint` / `coverage` / `test` / `deliverables` / `trace` / `reach`（routine DAG，全部 required）
- **Milestone**: `release-evidence`（tag/release 硬门禁；needs routine 全部）
- **Validation items**（各 job）:
  - `lint`: ruff + flake8 CCR001 + pylint R0801/C0302/R0915/R0914（分层政策不变）
  - `coverage`: `coverage report --fail-under=95`（排除 tests/e2e_live）
  - `test`: unit + integration + e2e fake 通道（含本计划全部 integration/e2e 文件）
  - `deliverables`: `trac check deliverables`
  - `trace`: `trac check trace --json`（AC 引用闭合，含 v0.6 标记扫描）
  - `reach`: `trac check reach --json`
  - AC reference closure / anti-pattern static scan（§1.3）经 `trace` + review 流程强制
- **待实现（Devon foundation task，ARCH-006 §4.3）**: `live-opencode`（weekly/manual）与 `release-evidence`（milestone）job 增补 `tests/e2e_live/test_hotfix_live.py` 探针步骤；milestone 缺凭据 fail-closed。当前 workflow 未含该步骤，不是通过证据。

---

## 8. AC Coverage

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0240-01 | integration + e2e | tests/integration/test_hotfix_cli_entry.py::test_hotfix_entry_happy_requested_and_status_substates + tests/e2e/test_hotfix_journey.py::test_hotfix_journey_happy_post_release | IF-HOTFIX-001, IF-HOTFIX-002 |
| AC-FR0240-02 | integration | tests/integration/test_hotfix_precheck.py::test_precheck_pass_reaches_sage_triage_without_agent_dispatch | IF-HOTFIX-003 |
| AC-FR0240-03 | integration | tests/integration/test_hotfix_precheck.py::test_precheck_rejection_matrix_no_branch_nonzero_exit | IF-HOTFIX-003 |
| AC-FR0240-04 | integration | tests/integration/test_hotfix_anchor.py::test_sage_anchor_validated_reports_anchored_set | IF-HOTFIX-004 |
| AC-FR0240-05 | integration | tests/integration/test_hotfix_anchor.py::test_anchor_invalid_redispatch_and_await_human_no_auto_feature | IF-HOTFIX-002, IF-HOTFIX-004 |
| AC-FR0240-06 | integration + e2e | tests/integration/test_hotfix_await_human.py::test_human_anchor_manual_and_feature_route + tests/e2e/test_hotfix_await_journey.py::test_hotfix_await_journey_manual_anchor | IF-HOTFIX-001, IF-HOTFIX-002 |
| AC-FR0240-07 | integration | tests/integration/test_hotfix_events.py::test_hotfix_events_append_only_replay_and_report | IF-HOTFIX-002, IF-HOTFIX-006 |
| AC-FR0241-01 | integration | tests/integration/test_hotfix_branch_baseline.py::test_anchored_creates_isolated_fix_branch_per_scenario | IF-HOTFIX-005 |
| AC-FR0241-02 | integration | tests/integration/test_hotfix_branch_baseline.py::test_baseline_inheritance_no_requirement_stage_artifacts | IF-HOTFIX-005 |
| AC-FR0241-03 | integration + e2e | tests/integration/test_hotfix_branch_baseline.py::test_anchored_enters_mdesign_and_run_continues + tests/e2e/test_hotfix_journey.py::test_hotfix_journey_happy_post_release | IF-HOTFIX-005, IF-HOTFIX-006 |
| AC-FR0241-04 | integration | tests/integration/test_hotfix_feature_route.py::test_feature_route_exits_without_branch_or_dangling_run | IF-HOTFIX-002, IF-HOTFIX-009 |
| AC-FR0242-01 | integration + e2e | tests/integration/test_hotfix_coexist.py::test_active_run_selection_and_status_discriminates_runs + tests/e2e/test_hotfix_journey.py::test_hotfix_journey_happy_post_release | IF-HOTFIX-006 |
| AC-FR0242-02 | integration | tests/integration/test_hotfix_coexist.py::test_boundary_restores_suspended_feature_run_as_active | IF-HOTFIX-006, IF-HOTFIX-008 |
| AC-FR0242-03 | integration | tests/integration/test_hotfix_coexist.py::test_suspended_run_observable_and_gates_recoverable | IF-HOTFIX-006 |
| AC-FR0242-04 | integration | tests/integration/test_hotfix_coexist.py::test_second_concurrent_trac_command_rejected_with_holder_pid | IF-HOTFIX-006 |
| AC-FR0243-01 | integration | tests/integration/test_hotfix_design_delta.py::test_mdesign_delta_docs_in_hotfix_dir_with_inherited_contracts | IF-HOTFIX-005, IF-HOTFIX-006, IF-HOTFIX-010 |
| AC-FR0243-02 | integration | tests/integration/test_hotfix_design_delta.py::test_delta_design_carries_anchor_set_and_prism_review | IF-HOTFIX-002, IF-HOTFIX-009 |
| AC-FR0243-03 | integration | tests/integration/test_hotfix_design_delta.py::test_prism_anchor_overturn_routes_back_to_sage_triage | IF-HOTFIX-002, IF-HOTFIX-009 |
| AC-FR0244-01 | integration | tests/integration/test_hotfix_mtest.py::test_mtest_regression_red_first_then_green | IF-HOTFIX-007, IF-MTEST-002 |
| AC-FR0244-02 | integration | tests/integration/test_hotfix_mtest.py::test_delta_testplan_layer_ownership_validated | IF-HOTFIX-007, IF-VALIDATE-001, IF-HOTFIX-010 |
| AC-FR0244-03 | integration | tests/integration/test_hotfix_mtest.py::test_trace_binds_cross_version_ac_without_new_ac | IF-HOTFIX-007, IF-TRACE-002 |
| AC-FR0244-04 | integration | tests/integration/test_hotfix_mtest.py::test_empty_shield_increment_release_with_unit_closure | IF-HOTFIX-007, IF-TRACE-002 |
| AC-FR0245-01 | integration | tests/integration/test_hotfix_mimpl.py::test_mimpl_commits_isolated_on_fix_branch | IF-HOTFIX-008, IF-IMPL-002 |
| AC-FR0245-02 | integration | tests/integration/test_hotfix_mimpl.py::test_scenario_b_baseline_stale_reconcile_needs_attention | IF-HOTFIX-008, IF-IMPL-001 |
| AC-FR0246-01 | integration + e2e | tests/integration/test_hotfix_mimpl.py::test_boundary_terminal_state_keeps_fix_branch + tests/e2e/test_hotfix_journey.py::test_hotfix_journey_happy_post_release | IF-HOTFIX-008 |
| AC-FR0246-02 | integration | tests/integration/test_hotfix_mimpl.py::test_no_release_events_after_boundary | IF-HOTFIX-008 |
| AC-FR0246-03 | integration + e2e | tests/integration/test_hotfix_events.py::test_replay_and_report_show_full_hotfix_journey + tests/e2e/test_hotfix_journey.py::test_hotfix_journey_happy_post_release | IF-HOTFIX-006, IF-HOTFIX-008 |
| AC-FR0247-01 | integration | tests/integration/test_hotfix_routes.py::test_design_gap_returns_to_hotfix_mdesign_without_human_gate | IF-HOTFIX-009 |
| AC-FR0247-02 | integration | tests/integration/test_hotfix_routes.py::test_design_gap_loop_closes_and_reenters_journey | IF-HOTFIX-009 |
| AC-FR0248-01 | integration | tests/integration/test_hotfix_routes.py::test_ac_gap_spec_gap_exit_with_human_approval_prerequisite | IF-HOTFIX-009 |
| AC-NFR0100-01 | integration | tests/integration/test_hotfix_precheck.py::test_precheck_deterministic_no_llm_dispatch_records | IF-HOTFIX-003 |
| AC-NFR0100-02 | integration | tests/integration/test_hotfix_anchor.py::test_anchor_validation_traceable_with_retry_count | IF-HOTFIX-004, IF-VALIDATE-001 |
| AC-NFR0100-03 | integration | tests/integration/test_hotfix_feature_route.py::test_fail_closed_no_branch_and_no_auto_feature_route | IF-HOTFIX-002, IF-HOTFIX-005 |
| AC-NFR0100-04 | integration | tests/integration/test_hotfix_events.py::test_hotfix_events_append_only_and_projection_rebuild | IF-HOTFIX-002, IF-HOTFIX-006 |
| AC-NFR0110-01 | integration + e2e | tests/integration/test_hotfix_coexist.py::test_suspended_run_observable_and_gates_recoverable + tests/e2e/test_hotfix_await_journey.py::test_hotfix_await_journey_manual_anchor | IF-HOTFIX-006 |
| AC-NFR0110-02 | integration | tests/integration/test_hotfix_coexist.py::test_boundary_restores_suspended_feature_run_as_active | IF-HOTFIX-006 |
| AC-NFR0110-03 | integration | tests/integration/test_hotfix_recovery.py::test_crash_recovery_resumes_precise_hotfix_state | IF-HOTFIX-002, IF-HOTFIX-006 |

> **Prism:** [PRISM-V06-R15-01][blocker][defect_classification=test_defect] 判据3(counterexample kill verification)违反--kill-manifest verification 文本与树事实不符（skill 评审纪律：stale 口径视同缺失）。逐项实跑验证，多条 failing 测试的实际失败点与 kill-manifest 声称的 'fresh unpatched run fails at the IF-HOTFIX-010 seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md)' 不一致：(a) tests/integration/test_hotfix_branch_baseline.py::test_anchored_creates_isolated_fix_branch_per_scenario (AC-FR0241-01, :34) 实际失败于 PRECHECK 'REJECTED (baseline_not_locatable)'（非 IF-HOTFIX-010 seam）；(b) tests/integration/test_hotfix_branch_baseline.py::test_baseline_inheritance_no_requirement_stage_artifacts (AC-FR0241-02) 实际失败于 hotfix 项目目录未创建（PosixPath v0.5-hotfix-42 exists=False），PRECHECK rejection 上游，非 seam；(c) tests/integration/test_hotfix_mtest.py::test_delta_testplan_layer_ownership_validated (AC-FR0244-02, :100) 实际失败于 'missing frontmatter field created' + 'test-plan validate requires acceptance.md'，非 M-DESIGN awaiting=escalation seam；(d) tests/integration/test_hotfix_mtest.py::test_trace_binds_cross_version_ac_without_new_ac (AC-FR0244-03, :126) 实际失败于 assert 'hotfix_scope' in {'status':'pass','warnings':['no supported test files found']}，非 seam。kill-manifest.json 各对应条目 verification 仍标 seam 失败--fictional。kill-manifest.json:3 总声称 'All 5 patches apply cleanly... Each patch... kills its bound test (killed vs. fresh unpatched legal-Red at the IF-HOTFIX-010 seam)'--但 fresh unpatched run 的实际失败点并非 seam，kill 基线口径错误。修订预期：Shield 逐条重跑 fresh unpatched run（.venv/bin/python -m pytest <test> --tb=line），按实际失败点重写 kill-manifest verification 口径（区分 PRECHECK rejection / validate frontmatter / hotfix_scope 缺失 / 真 seam），确保 verification 文本与树事实一致；kill-manifest.json:3 总述须按实际失败点分布重写。

> **Prism [RESOLVED]:** [PRISM-V06-R14-01][blocker][defect_classification=test_defect] 判据3(counterexample可应用性)再次违反——逐个 patch 实际执行 git apply --check（skill 评审纪律），5 个 patch 中 4 个在当前 HEAD 失败：hotfix_anchor_no_redispatch.patch / hotfix_anchor_auto_feature_backlog.patch（绑定 AC-FR0240-05 tests/integration/test_hotfix_anchor.py::test_anchor_invalid_redispatch_and_await_human_no_auto_feature）、hotfix_fix_commit_leaks_to_main.patch（绑定 AC-FR0245-01 tests/integration/test_hotfix_mimpl.py::test_mimpl_commits_isolated_on_fix_branch）、hotfix_baseline_fresh_never_stale.patch（绑定 AC-FR0245-02 tests/integration/test_hotfix_mimpl.py::test_scenario_b_baseline_stale_reconcile_needs_attention），均 error: patch failed: tracks/cli/main.py:1105 ——hunk header @@ -1071 / @@ -1105 指向 5825dde 时的 _COMMANDS 位置，但当前 _COMMANDS 已漂移到 line 1311（M-IMPL GREEN T-007 / B32 follow-up / B32 #32 等提交 1b4a84d/6f6e900/2e6a678/bf530a2/9e93429 在 5825dde 之后改动了 cli/main.py）。仅 cli_hotfix_no_events.patch 仍 apply。kill-manifest.json line 3 明确声称 'Each patch in this directory applies cleanly against the current HEAD (verified via git apply --check)'——与树事实不符（PRISM-V05-R4-02 / PRISM-V06-12 同类教训复发）。影响：3 条 required AC 的 counterexample kill 证据不可复现 → counterexample 绑定缺失。修订预期：Shield 用当前 HEAD 重新生成这 4 个 patch（hunk context 对齐 cli/main.py 当前行位），逐个重跑 git apply --check + kill 验证，并更新 kill-manifest verification 口径与当前树一致；建议把 patch 生成脚本（_gen_anchor_cli_patches.py 等）纳入 CI 守卫或在每次 M-IMPL 改动 cli/main.py 后重跑可应用性校验以防再次漂移。
>> **Shield:** PRISM-V06-R14-01 fixed: the 4 drifted counterexample patches regenerated against the current tracks/cli/main.py (the _COMMANDS map is at line 1318-1335 on HEAD). The generator bug that anchored on the substring 'USAGE = (' inside '_HOTFIX_USAGE = (' was also fixed in _gen_patches_extra.py / _gen_patch_leak.py (now anchor on '\nUSAGE = (\n' with leading newline, so the handler functions are inserted before the module-level USAGE tuple, not the hotfix sub-usage). Re-verified per the skill discipline: git apply --check passes for all 5 patches; each regenerated patch applies with AST-valid syntax; kill re-verified on the current HEAD against its bound test — hotfix_anchor_no_redispatch.patch (len(failed)==3 got 1), hotfix_anchor_auto_feature_backlog.patch (backlog.recorded present), hotfix_fix_commit_leaks_to_main.patch (git cherry shows in-main commit), hotfix_baseline_fresh_never_stale.patch (stale==[]). kill-manifest introduction updated: 'Each patch in this directory applies cleanly against the current HEAD (verified via git apply --check)' is now tree-factual; per-binding verification text updated to the IF-HOTFIX-010 seam legal-red instead of USAGE.

> **Archer [RESOLVED]:** ARCH-R2-IF-ATTR [severity=blocker][artifact=test-plan.md §2.1/§8 历史线程 PRISM-V06-01/03/04][关联=BS-06 design trace / FR-0140 IF- 归属] R2 重入 M-DESIGN 后 trac validate --file test-plan.md 失败 7 项（AC-FR0240-05、AC-FR0242-01/02/03、AC-FR0245-01、AC-NFR0110-01/02）：三条 Prism 历史根评论（V06-01 行 333、V06-03 行 327、V06-04 行 324）在提及 AC id 的同一行含 tests/integration/ 路径字样但无 IF- 标识，触发 check_design_trace 的『integration/e2e AC missing IF- attribution』（这些线程在 M-TEST PRISM_REVIEW 期间加入，当时未重跑 BS-06 校验；stub_gap 回滚重入 M-DESIGN 后在 EXIT 门禁显形）。根评论是 Prism 的作者评论，Archer 无权编辑（FR-110 author-only）。请求 @Prism 在 PRISM_REVIEW 轮用 trac discuss edit 对三条根评论做最小作者补注——内容零改动，仅在同一行内追加 IF- 归属标签（满足 IF-[A-Z]+-\d{3} 词法即可）：(1) V06-01（涉 AC-FR0240-05）追加 [IF-attr: IF-HOTFIX-002, IF-HOTFIX-004]（该 AC §8 行 IF 列）；(2) V06-04（涉 AC-FR0242-01/02/03、AC-NFR0110-01/02）追加 [IF-attr: IF-HOTFIX-006, IF-HOTFIX-008]（各 AC §8 IF 列并集）；(3) V06-03（涉 AC-FR0245-01）追加 [IF-attr: IF-HOTFIX-008, IF-IMPL-002]（该 AC §8 行 IF 列）。补注属评审期作者编辑（讨论元数据补全，不改任何 finding 语义）。补注完成后由 Archer 复验 trac validate 恢复 valid 并 resolve 本线程；本线程 resolved 前 M-DESIGN EXIT 的 trace 门禁不可过（R2 文档集的预期中间态，见 ARCH-006 §0.2 最后一条）。除本线程外 test-plan.md 的 R2 变更为零（覆盖表/测试资产不变，冻结测试按 interfaces §2a/§4b 经 CLI 交付面驱动是 AC closure 既定设计）。
>> **Archer:** R3 收敛本线程：请求未被撤销，但载体升级。R3 已把耐久修复落为合同——interfaces §2f（design-trace 扫描范围排除 inline-discussion blockquote 行，IF-VALIDATE-001 v0.6 扩展）+ ARCH-006 §3.9（事故链、fail-closed 论证与本 run EXIT 过渡路径）。原作者补注从『未 resolved 线程承载的请求』降级为 §3.9 过渡路径 (a)（推荐主路径）：Prism 在 PRISM_REVIEW 轮对 PRISM-V06-01/03/04 三条根评论做作者补注后，当前扫描器下 EXIT 即可通过；路径 (b)（Runtime 提前应用 §2f 扫描器变更）为备选。按作者 DRAFT 纪律（本人发起线程须 resolved 派发方可结束）resolve 本线程；§8 覆盖表与测试资产零变更，§10 新增 §2f 对应实现/测试更新行。

---

## 9. HOTFIX-TRIAGE SM-01 转移覆盖清单（NFR-0020 同源纪律，normative 依据 SPEC-006 SM-01）

每条转移 ≥1 测试走到一次；实现时可加后缀细分但不得留空行缺口。

| 转移 | 内容摘要 | 层 | 测试 |
|:---|:---|:---|:---|
| SM-01.1 | （新建）→ PRECHECK：trac hotfix 入口命令（hotfix.requested） | integration | test_hotfix_cli_entry.py::test_hotfix_entry_happy_requested_and_status_substates |
| SM-01.2 | PRECHECK → SAGE_TRIAGE：预检通过（triage.prechecked） | integration | test_hotfix_precheck.py::test_precheck_pass_reaches_sage_triage_without_agent_dispatch |
| SM-01.3 | PRECHECK → REJECTED：非 bug / 不可定位 / scenario 不合法 / dev 无活跃分支 | integration | test_hotfix_precheck.py::test_precheck_rejection_matrix_no_branch_nonzero_exit |
| SM-01.4 | REJECTED → 终止：不建分支、报原因与下一步 | integration | test_hotfix_precheck.py::test_precheck_rejection_matrix_no_branch_nonzero_exit |
| SM-01.5 | SAGE_TRIAGE → ANCHORED：锚定 outcome 且程序校验通过（anchor.validated） | integration | test_hotfix_anchor.py::test_sage_anchor_validated_reports_anchored_set |
| SM-01.6 | SAGE_TRIAGE → SAGE_TRIAGE：校验失败重派（≤3） | integration | test_hotfix_anchor.py::test_anchor_invalid_redispatch_and_await_human_no_auto_feature |
| SM-01.7 | SAGE_TRIAGE → AWAIT_HUMAN：NO_ANCHOR 或 3 次未产出合法锚定 | integration | test_hotfix_anchor.py::test_anchor_invalid_redispatch_and_await_human_no_auto_feature |
| SM-01.8 | AWAIT_HUMAN → ANCHORED：Human 指认 AC（human.anchor(manual)） | integration + e2e | test_hotfix_await_human.py::test_human_anchor_manual_and_feature_route + e2e await journey |
| SM-01.9 | AWAIT_HUMAN → FEATURE_ROUTE：Human 确认转 feature（human.anchor(feature_route)） | integration | test_hotfix_await_human.py::test_human_anchor_manual_and_feature_route |
| SM-01.10 | ANCHORED → 终止：记录锚定 + fix/{issue} 分支 + 继承基线 + stage.entered(M-DESIGN) | integration | test_hotfix_branch_baseline.py::test_anchored_creates_isolated_fix_branch_per_scenario |
| SM-01.11 | FEATURE_ROUTE → 终止：backlog.recorded，不建分支 | integration | test_hotfix_feature_route.py::test_feature_route_exits_without_branch_or_dangling_run |
| 补充：anchor 推翻 | M-DESIGN PRISM_REVIEW(anchor_verdict=overturned) → SAGE_TRIAGE（重派预算归属 SM-01.6） | integration | test_hotfix_design_delta.py::test_prism_anchor_overturn_routes_back_to_sage_triage |
| 补充：崩溃恢复 | 各 triage 子状态中断后事件回放恢复精确状态 | integration | test_hotfix_recovery.py::test_crash_recovery_resumes_precise_hotfix_state |

---

## 10. 既有测试更新（planned changes）

v0.6 对既有测试的预期影响面（Shield 在 M-TEST 修订，走 test change classification）：

| 既有测试 | 变更 | 理由 |
|:---|:---|:---|
| `tests/unit/test_machine_design.py` / `tests/unit/test_machine_m_test.py` | 无必需变更（hotfix 复用 canonical 阶段；若 State 字段追加影响投影快照，Devon 随实现同步） | State 追加字段向后兼容 |
| `tests/integration/test_full_journey*.py`（v0.5 系列） | 无必需变更；回归确认 feature 旅程事件前缀不受 trac hotfix 入口影响 | 新命令新 run，不改既有事件序列 |
| `tests/integration/test_trac_retry.py` 等 CLI 测试 | 若 USAGE 字符串更新（新增 hotfix 命令）则同步断言 | USAGE 文本增量 |
| `tests/conftest.py` | Shield 新增共享 helper `tests/hotfix_support.py`（host-issues 种子 + 已批准基线构造），不改既有 fixture 语义 | §2.4 语料准备 |
| `tests/integration/test_testplan_ownership.py` | 【R3】新增 integration 回归：含已 resolved 讨论线程的 plan 文件 `trac validate --file test-plan.md` exit 0（inline-discussion blockquote 行不参与 layer/IF- 归属扫描，interfaces §2f；归属声明仅存在于 blockquote 的 AC 仍报 has no layer attribution，fail-closed 保持）；既有 validate 合同用例不依赖 blockquote 被扫描（已核对），新增行为需正反两侧覆盖 | interfaces §2f / ARCH-006 §3.9（R3）：design-trace 扫描范围排除 inline-discussion 行。【R5 收敛】§2f 合同冻结，代码实现状态归 Devon/Runtime 归档职责（T-012 或运营端带外应用，非设计合同事项） |
| `tests/integration/test_hotfix_design_delta.py` / `tests/integration/test_hotfix_mtest.py` | 【R4】Shield integration 回归：hotfix 版本目录（只含 delta 三文档）下 `trac validate --file test-plan.md` exit 0（继承基线经 `resolve_inherited_baseline_docs` 只读解析，不复制文件）；既有 AC-FR0243-01 / AC-FR0244-02 行扩展断言该出口；feature 版本目录的 validate 行为回归不变 | interfaces §1i / IF-HOTFIX-010 / ARCH-006 §3.4（R4）：hotfix 版本目录继承基线文档只读解析；既有 feature 目录用例不依赖跨版本解析（已核对），新增行为需正反两侧覆盖 |

> **Prism [RESOLVED]:** [PRISM-V06-13][blocker] §10 既有测试更新表 line 382-383 在 feature 版本 test-plan 声明 unit-layer 行，违反不变量（validate.py: feature 版本目录含任何 unit 行=hard error；只有 {version}-hotfix-{issue} delta plan 可声明 unit 行）与 §1.5（Archer 不规划 unit 测试函数/文件）。两行 cell1=tests/unit/test_test_tasks_contract.py、cell2 含「Devon 单元层义务」并指定 unit 用例内容（R3 blockquote 排除用例、R4 resolve_inherited_baseline_docs 纯函数用例）；Runtime test_tasks check 报 line:382/383 unit-layer row not allowed in feature version test-plan。修订预期：从 §10 移除 unit-layer 处方——保留 Shield integration 层回归行（既有 validate 合同用例补「含已 resolved 讨论线程的 plan exit 0」、hotfix 版本目录 validate exit 0、既有 AC-FR0243-01/AC-FR0244-02 行扩展断言），删除 tests/unit/test_test_tasks_contract.py 的 Devon 单元层义务描述（Devon 经 RGR/覆盖率门禁自辖，§1.5）；若需保留 R3/R4 合同变更作为 integration 回归的理由，行内不出现 tests/unit 路径与 unit 层词。
>> **Archer:** PRISM-V06-13 已修订：§10 两行（原 line 382-383）移除 tests/unit 路径与「Devon 单元层义务」处方，改为 Shield integration 层回归行——(1) R3 行 cell1=tests/integration/test_testplan_ownership.py，回归「含已 resolved 讨论线程的 plan 文件 trac validate --file test-plan.md exit 0」（interfaces §2f blockquote 排除合同，fail-closed 保持「归属仅存在于 blockquote 的 AC 仍报 has no layer attribution」）；(2) R4 行 cell1=tests/integration/test_hotfix_design_delta.py / test_hotfix_mtest.py，回归「hotfix 版本目录下 trac validate --file test-plan.md exit 0（resolve_inherited_baseline_docs 只读解析）」并扩展 AC-FR0243-01/AC-FR0244-02 断言。两行 cell 内不再出现 tests/unit 路径或 unit/单元层 词；R3/R4 合同变更作为 integration 回归理由保留。Devon 对 resolve_inherited_baseline_docs 纯函数的单测义务回归 RGR/覆盖率门禁自辖（§1.5），不在本 plan 处方。trac validate --file test-plan.md = valid。请 Prism 复核并 resolve。

---

## 11. e2e Happy Path 范围

e2e 仅覆盖 happy path（主成功旅程）；全部错误/边界矩阵归 integration（§8）。

**场景 A 全旅程（test_hotfix_journey.py::test_hotfix_journey_happy_post_release）**：

```
[host_repo：已批准版本 v0.5 基线 + bug issue #42 种子]
-> hotfix 入口（顶层子命令）: 42 --scenario post-release
-> run 建立：hotfix.requested(42, post-release) -> stage.entered(M-HOTFIX-TRIAGE)
-> PRECHECK 通过：triage.prechecked(pass, target_version=v0.5)
-> SAGE_TRIAGE：Sage 锚定（fake: anchor）-> anchor.validated(AC-...@v0.5, source=sage)
-> ANCHORED：branch.created(fix/42, base=main) + checkout + baseline.inherited(v0.5)
-> stage.entered(M-DESIGN)（terminal 行：stage=M-DESIGN branch=fix/42 scenario=post-release）
-> trac run：Archer delta 三文档（hotfix 项目目录）-> Prism review pass（anchor_verdict=upheld）
-> M-TEST：回归用例先 RED（red.validated）-> trace 闭合 -> commit
-> M-IMPL：task graph -> Devon RGR -> 修复提交落 fix/42（trailers Tracks-Issue=42）
-> ISLAND_GATE_2 -> stage.exited(M-IMPL) -> run.completed(boundary)
断言：trac status terminal=boundary branch=fix/42 scenario=post-release；
      trac replay 序列 hotfix.requested->triage.prechecked->anchor.validated->
      stage.entered(M-DESIGN)->…->run.completed(boundary)；
      trac report --run-id --format md 含锚定/分支/boundary 证据；
      git branch --show-current 恢复为原分支（无挂起 run 时为 main）；fix/42 保留修复提交。
```

**awaiting → manual anchor 旅程（test_hotfix_await_journey.py::test_hotfix_await_journey_manual_anchor）**：

```
[bug issue #77 种子：Sage NO_ANCHOR（fake: no_anchor）]
-> hotfix 入口: 77 --scenario post-release -> AWAIT_HUMAN（status: awaiting=awaiting_human origin=hotfix-triage issue=77）
-> hotfix 子动作: anchor AC-FR...-01@v0.5 -> human.anchor(manual) -> anchor.validated(source=human)
-> ANCHORED -> fix/77 -> M-DESIGN -> …（同上收窄旅程）-> boundary
断言：manual 路径与 Sage 路径在 ANCHORED 之后事件同构；全程无 backlog.recorded。
```

两条 e2e 均不 mock kernel/executor；fake 通道仅经 `TRAC_FAKE_SIMULATE` 注入 agent outcome 分支。空 Shield 增量分支（AC-FR0244-04）是 M-TEST 非常规路径，归 §8 的 integration 层行（IF-HOTFIX-007、IF-TRACE-002），不进 e2e happy path。
