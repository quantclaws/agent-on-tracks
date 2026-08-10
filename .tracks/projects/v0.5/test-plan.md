---
spec_id: SPEC-005
created: 2026-08-09
status: draft
sha:
---

# M-IMPL 阶段推进（Devon 逐 task RGR） - Test Plan

- **Related acceptance**: `.tracks/projects/v0.5/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.5/interfaces.md` (assertion basis - see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- CLI endpoints (`trac run`, `trac status`, `trac validate`, `trac retry`, `trac check reach`, `trac check deliverables`, `trac report`) stdout/stderr/exit code.
- Persisted data: `.tracks/runtime/tracks.db` `events` table (assertion primary source), projection tables.
- Document files: `.tracks/projects/v0.5/{story,spec,acceptance}.md`, `.tracks/projects/v0.5/tasks.json`（task graph 机器真相）, `.tracks/projects/v0.5/tasks.md`（人类可读投影） (inline-discussion blockquotes, task graph content, phase progress).
- Git state: refs (`refs/trac/rgr/{run}/{task}/{attempt}/red`), commits (G commit parent=B + trailers), worktree state, working tree diff.
- File schema: `.tracks/project/project.toml` (TOML canonical contract), `tracks/agents/Devon.md` (frontmatter + body, no permission block), `tracks/skills/tracks-prism-impl/SKILL.md`.
- `trac check reach --json` / `trac check deliverables` structured output.
- `command.issued` event payload (dispatch assignment materialization fields).

### 1.2. Non-observable Objects (tests do not directly depend on)

- `kernel/machine.py` internal State field values (observed via `trac status` / events).
- `executor/executor.py` internal subprocess management (observed via events).
- `executor/taskgraph.py` / `executor/rgr.py` / `executor/worktree.py` / `executor/quality_gate.py` internal data structures (observed via CLI output / events / git state).
- `effects/opencode.py` internal prompt construction (observed via outcome events + audit evidence).
- `effects/audit.py` internal manifest representation (observed via `outcome.received.audit_evidence`).

**Observable contract**: Any internal state that acceptance validation needs must be provided by the implementation layer via events/CLI/file/git observation points. This is the responsibility of **interfaces.md** - if an AC needs to observe internal state, interfaces.md must have a corresponding outlet (see §6.5).

### 1.3. Cheating Patterns (CI enforced interception)

| #   | Cheating Pattern                | Typical Symptom                                |
| --- | -------------------------------- | ---------------------------------------------- |
| 1   | Change assertions to fit impl    | spec says "throw exception", test changes to "return False" |
| 2   | Use skip to evade validation     | skip/ignore (e.g. `pytest.skip`, `it.skip`) with "see e2e" but e2e is never written |
| 3   | Assertion degradation            | `assert issubclass(X, Exception)` instead of actually submitting and catching |
| 4   | try/except: pass                 | Exception path is swallowed                     |
| 5   | Over-mocking                     | Mock the framework core, testing mock behavior instead |
| 6   | Ground truth uses impl           | Expected value = impl output                   |
| 7   | Hardcoded expected values        | `assert result == 0.15` only because current impl outputs 0.15 |
| 8   | Trivial pass                     | `assert True` / `assert 1 == 1`                |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.5 TRACKS-TRACE <optional description>` (long-format marker with `TRACKS-TRACE` token, FR-0080/FR-0130). The `TRACKS-TRACE` token is mandatory-without it the marker is invisible to the trace scanner. Multiple ACs bound to the same function get one marker line per AC.
   - CI scans `tests/`, verifying: each test references at least one AC; each AC is referenced by at least one test.
   - Any check failure blocks merge.
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表（v0.5 新增 IF-IMPL-001~007、IF-DEVON-001 + v0.4 既有 IF-MTEST-001/002、IF-SHIELD-001、IF-TRACE-001/002、IF-REACH-001/002、IF-VALIDATE-001）。

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion.
   - No `try: ... except: pass` wrapping the code under test.
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`, jest `it.skip`, Go `t.Skip`) without a GitHub issue link.

3. **Test change classification** (required in PR description)
   - [ ] New AC (link to acceptance.md commit)
   - [ ] Spec change (link to spec commit)
   - [ ] Fix flake / environment issue (link to issue)

   **Prohibited category**: "impl behavior inconsistent with spec -> change test". Reject directly during review.

4. **Testability fallback** (if an AC cannot be tested)
   - Do not mock internals to force it through.
   - Register it as a framework-side testability requirement, requesting the implementation layer to add a public assembly point.
   - Until resolved, mark the AC as "blocked by testability gap".

### 1.5. Test Division of Labor

- **Unit tests**: Written by the **implementer** (Devon, committed alongside impl in R-G-R).
- **Integration tests**: Written by the **test lead** (Shield) - covers module interface contracts defined in interfaces.md.
- **E2E tests**: Written by the **test lead** (Shield) - covers user-facing happy paths only.
- **Ground Truth (§3)**: Provided by an **independent developer** not involved in the implementation under test, or a **third-party library**.
- **Review ownership**: All test changes are reviewed by the test lead; Ground Truth script changes require focused review of semantic consistency with the corresponding AC.

---

## 2. Test Environment

### 2.1. Directory Layout

```
tests/
├── unit/
│   ├── test_machine_m_impl.py     # M-IMPL reducer/decide branches (SM-01)
│   ├── test_taskgraph.py          # parse_tasks_json/validate_dag/validate_scope/validate_ac_coverage/validate_issue_numbers (FR-0030/0180/0220)
│   ├── test_rgr.py                # create_red_ref/create_green_commit/classify_red/verify_lineage (FR-0070/0080/0120)
│   ├── test_quality_gate.py       # run_gates/run_production_checks/run_test_checks layering (FR-0110/0130)
│   ├── test_worktree.py           # three worktree scheme (FR-0070)
│   ├── test_deliverables.py       # [既有，扩展] Devon.md deliverables gate (FR-0170)
│   └── ...                        # 既有 unit tests 不变
├── integration/
│   ├── test_m_impl_cycle.py       # M-IMPL full cycle (FR-0010~0160)
│   ├── test_baseline_recalc.py    # BASELINE recalc + frozen test paths (FR-0020)
│   ├── test_taskgraph_validate.py # tasks.json DAG/scope/AC coverage/IF- 有效性/issue number (FR-0030/0180)
│   ├── test_island_gate_1.py      # ISLAND_GATE_1 six-tuple check (FR-0040)
│   ├── test_prism_plan.py         # PRISM_PLAN criteria pack binding (FR-0050)
│   ├── test_task_dispatch.py      # DAG scheduling + writelock + manifest (FR-0060)
│   ├── test_red_phase.py          # RED Devon dispatch + isolation (FR-0070)
│   ├── test_red_gate_m_impl.py    # RED_GATE classification (FR-0080)
│   ├── test_prism_red.py          # PRISM_RED B..R review (FR-0090)
│   ├── test_green_phase.py        # GREEN Devon dispatch + diff rebase (FR-0100)
│   ├── test_green_gate.py         # GREEN_GATE gates + feedback desensitization (FR-0110)
│   ├── test_green_commit.py       # GREEN_COMMIT G commit + lineage (FR-0120)
│   ├── test_refactor.py           # REFACTOR + quality gate layering (FR-0130)
│   ├── test_task_review.py        # TASK_REVIEW scope/lineage/budget (FR-0140)
│   ├── test_prism_final.py        # PRISM_FINAL (FR-0140)
│   ├── test_diagnose_four_way.py  # DIAGNOSE 4-way routing (FR-0150)
│   ├── test_shield_fix.py         # SHIELD_FIX (FR-0150)
│   ├── test_island_gate_2.py      # ISLAND_GATE_2 reach + full int+e2e (FR-0160)
│   ├── test_devon_dispatch.py     # Devon agent dispatch + manifest audit (FR-0170)
│   ├── test_tasksjson_validate.py # trac validate --file tasks.json (FR-0180)
│   ├── test_dispatch_materialization.py  # dispatch materialization (FR-0190)
│   ├── test_crash_recovery.py     # crash recovery (FR-0200)
│   ├── test_trac_retry.py         # trac retry command (NFR-0030)
│   ├── test_kernel_purity_m_impl.py  # kernel purity (NFR-0010)
│   ├── test_events_append_only_m_impl.py  # append-only (NFR-0020)
│   └── ...                        # 既有 integration tests 不变
├── e2e/
│   ├── test_m_impl_journey.py     # M-IMPL happy path: M-TEST exit -> M-IMPL -> boundary
│   ├── test_full_journey.py       # [既有，更新] 全流程: M-STORY -> ... -> M-IMPL -> boundary
│   └── ...                        # 既有 e2e tests 不变
├── e2e_live/                      # live opencode channel (缺凭据 skip)
│   └── ...
├── assets/
│   ├── trace_fixtures/            # [既有]
│   ├── reach_fixtures/            # [既有]
│   └── taskgraph_fixtures/        # tasks.json fixtures for DAG/scope/AC coverage/IF- 有效性/issue number
└── conftest.py                    # 既有 + fake 通道强制 + M-IMPL fixtures
```

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_0070_03_r_ref_immutable`
- Marker: 测试函数定义紧邻上方一行标记注释 `# AC-FRXXXX-YY@v0.5 TRACKS-TRACE 说明`（R-1 约定，长格式 + 特征词强制，FR-0080/FR-0130）

### 2.3. Execution

- **Offline**: Tests do not depend on network (data is pinned).
- **Execution order**: unit (fast) -> integration -> e2e (slow).
- **CI**: Run the full suite on every push.
- **Isolation**: Integration and e2e use pytest markers (`@pytest.mark.integration` / `@pytest.mark.e2e`); default suite runs all, marker-based selection available.
- **M-IMPL GREEN_GATE int 子集执行**: executor 使用 project.toml 的 run 命令在 gate worktree 执行 int 子集（按 test-plan §8 IF- 归属筛选命中本 task IF- 集合的 integration 测试）；conftest 的 `_UNDER_COVERAGE` 机制继承（subprocess coverage merge）。
- **Git 操作测试**: R ref / G commit / worktree 操作使用 temp git repo（`tmp_path` fixture），不依赖宿主项目仓库状态。

### 2.3.1. Test Execution Contract (`.tracks/project/project.toml`)

The host project test execution contract is declared in `.tracks/project/project.toml` (produced by Archer in M-DESIGN). M-TEST uses this contract to collect and run tests independently; M-IMPL GREEN_GATE / ISLAND_GATE_2 复用同一合同执行 int 子集与全量 int+e2e。

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

- **Source**: Built-in fixtures in `tests/assets/taskgraph_fixtures/` (synthetic tasks.json with known DAG/scope/AC coverage/IF- validity/issue number properties).
- **Reproducible**: Each CI run produces consistent results (deterministic fixtures, no network).
- **Small data in-repo**: `tests/assets/taskgraph_fixtures/` fixtures are small JSON files with known cycle/no-cycle, overlap/no-overlap, coverage/gap, invalid-IF, invalid-issue-number structure.
- **Sensitive data**: None (no credentials in test data).
- **Version snapshot**: Fixtures are version-controlled in git; no external data versioning needed.

### 2.5. Installation & Isolation

继承 v0.2 §13（可安装发行物与 live E2E 安装边界）与 v0.4 §2.5。v0.5 新增 package data（`tracks/agents/Devon.md` 已在 v0.4 创建但未加入 deliverables；`tracks/skills/tracks-prism-impl/SKILL.md` 已在 v0.4 创建）。`[tool.setuptools.package-data]` 已含 `agents/*.md` 与 `skills/*/*.md`，无需修改。安装合同不变：live E2E 使用 wheel 安装到隔离 venv，不从源码树 import；fake 通道继承 conftest 的 `trac` fixture（subprocess 调用 `python -m tracks.cli.main`）。

---

## 3. Ground Truth Method

v0.5 是行为正确性（状态机转移、事件序列、git 操作、dispatch 物化），无算法正确性 / 规则正确性 / 计算结果正确性需要独立验证。

task graph DAG 无环校验（Kahn's algorithm 拓扑排序）属简单规则正确性：测试 fixture 本身声明已知有环/无环结构，fixture 数据即 ground truth（§3.1 表第 3 行「简单规则 -> 测试数据本身」）。scope 不重叠与 AC 覆盖闭合同理--fixture 声明已知重叠/无重叠、覆盖/缺口结构，测试数据即真相源。RGR git 操作（R ref 创建、G commit 创建、lineage 证明）属行为正确性，通过 git 命令（`git rev-parse` / `git log --format='%B'`）直接观察，不需要独立参考实现。

§3 判定不适用，不创建 `tests/ground_truth/` 目录。既有 v0.4 的 `tests/ground_truth/trace_reference.py`、`tests/ground_truth/reach_reference.py`、`tests/ground_truth/discuss_reference.py` 保持不变（服务于 v0.4 AC，非 v0.5 新增）。

---

## 4. Test Scope

This test plan covers all requirements in spec.md in the same directory where Valid / Testable / Decided are all green.

| Valid | Testable | Decided |
| ----- | -------- | ------- |
| ✅    | ✅       | ✅      |

---

## 5. Acceptance Criteria

1. Unit test coverage ≥95% (coverage + pytest, `coverage report --fail-under=95`).
2. Every cross-module interface contract defined in interfaces.md has at least one integration test (happy + key error/edge paths).
   A **cross-module interface** = an interfaces.md entry whose `modules` column lists 2+ modules (Archer marks this from architecture.md's module boundaries). Shield reads this column as a checklist.
3. User scenarios in Stories and Spec are fully covered by e2e happy paths and pass.
4. All FRs have corresponding test coverage (AC reference closure).
5. §6 external dependency layered testing: L1/L2 pass by default in CI; L3 is runnable in the corresponding environment.

---

## 6. External Dependency Layered Testing (project optional)

### 6.1. Three Unavoidable Constraints

| #   | Constraint | Consequence |
| --- | --- | --- |
| C1  | Test environment cannot connect to production opencode/LLM | CI / cross-platform dev machines cannot run real agent paths (Devon/Prism/Shield dispatch) |
| C2  | Cannot wait for real time | Long agent dispatch cycles infeasible in CI |
| C3  | Cannot mock framework internals | Replacing/patching M-IMPL control flow bypasses the behavior under test |

### 6.2. Stance: Controllable vs Mock

- **Replace external dependencies** (controllable): opencode subprocess, LLM provider, real time - these are **external dependencies** of the framework under test and can be replaced with deterministic stand-ins (FakeBackend / fake opencode stand-in).
- **Cannot mock internal implementation**: The framework's own M-IMPL state machine, task graph validation, RGR git operations, quality gate layering, DIAGNOSE routing - these are **the object under test** and must not be mocked.

**Boundary iron rule**: Under no circumstances may you replace or bypass the framework's own critical implementation to "make the test pass". If a test finds it must bypass to pass, it means the AC's observability design is wrong; revise interfaces/acceptance instead of patching the test side.

### 6.3. Three-Layer Test Pyramid

| Layer | Name | Time | Speed | Coverage | Default Run |
| ----- | ---- | ---- | ----- | -------- | ----------- |
| L1 | Deterministic sim | Virtual | Seconds | M-IMPL state machine, task graph validation, RGR git ops (temp repo), quality gate layering, DIAGNOSE routing, crash recovery | ✅ CI default |
| L2 | Contract sim | Virtual | Seconds | Devon/Prism/Shield dispatch protocol (fake opencode stand-in), manifest audit, criteria pack materialization, feedback desensitization | ✅ CI default |
| L3 | Real env smoke | Real | Real | Real opencode Devon single dispatch smoke (write a unit test) + Prism single dispatch smoke | ❌ nightly/manual |

- **L1 Deterministic sim**: FakeBackend controls Devon/Archer/Prism/Shield outcome via `simulate`; RGR git ops tested with temp git repo (`tmp_path`); pure functions tested directly with fixtures.
- **L2 Contract sim**: fake opencode stand-in (implements `opencode run --format json` protocol); OpencodeBackend interacts with stand-in, covering Devon dispatch materialization/JSON parsing/manifest audit/failure matrix/criteria pack identity/feedback desensitization.
- **L3 Real env smoke**: Real opencode + provider, single Devon dispatch (RED phase: write a unit test) + single Prism dispatch (PRISM_PLAN: criteria pack review); deselected by default, only runs with real credentials.

### 6.4. Responsibility Contract of Test Infrastructure

| Component | Responsibility (external) | Boundary (what it does not implement) |
| --------- | ------------------------- | ------------------------------------- |
| FakeBackend | Control Devon/Archer/Prism/Shield outcome via `simulate` | Does not implement real M-IMPL logic |
| fake opencode stand-in | Implement `opencode run --format json` protocol; write target files per manifest | Does not implement tracks M-IMPL state machine or task graph validation |
| temp git repo (`tmp_path`) | Real git operations on throwaway repo | Does not implement RGR logic (rgr.py does) |
| pytest subprocess (GREEN_GATE / ISLAND_GATE_2) | Execute real pytest on host project tests/ | Does not implement quality gate layering (quality_gate.py does) |
| taskgraph fixture data | Pinned tasks.json with known DAG/scope/AC coverage/IF- validity/issue number properties | Does not implement task graph validation algorithm |

### 6.5. Assertion Basis - Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md §4**:

- Events table (`baseline.frozen`, `taskgraph.committed`, `task.started`, `writelock.granted`, `red.checkpointed`, `green.committed`, `refactor.committed`, `refactor.no_change`, `task.completed`, `prism.verdict`, `verdict.failed`, `test.committed`, `stage.exited`, `stage.rolled_back`, `outcome.received`, `command.issued`, `human.retry`).
- CLI output (`trac status`, `trac validate --file tasks.json`, `trac retry`, `trac check reach`, `trac check deliverables`, `trac report`).
- File schema (`tasks.json`, `tasks.md`, `.tracks/project/project.toml`, `tracks/agents/Devon.md`, `tracks/skills/tracks-prism-impl/SKILL.md`).
- Git state (refs `refs/trac/rgr/.../red`, commit G parent=B + trailers, commit R test-only diff, worktree state).

If a state needed by an AC has **no** corresponding observable outlet in interfaces.md, this is an observability gap; revise interfaces/acceptance to add the outlet, rather than snooping internal state in the test.

---

## 7. CI Gate

- **Required checks** (architecture.md §4.3 CI 合同):
  1. `lint`：ruff check tracks tests + flake8 tracks（含 CCR001，tests/ 豁免--pre-commit 只跑 tracks）+ pylint R0801/C0302/R0915/R0914（tests/ 豁免 R0915/R0914--pre-commit 分命令执行）
  2. `coverage`：`coverage run -m pytest && coverage combine && coverage report --fail-under=95`
  3. `test`：`pytest -q -m 'not performance'`（unit + integration + e2e fake 通道）
  4. `deliverables`：`trac check deliverables`（含 Devon.md，存在性 + version + IQ）
  5. `trace`：`trac check trace --json`（需求追踪闭合；待 Devon 实现子命令后激活，foundation task）
  6. `reach`：`trac check reach --json`（模块可达性；待 Devon 实现子命令后激活，foundation task）
- **Validation items**:
  - AC reference closure (each required AC ≥1 test with long-format marker, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3)
  - Coverage ≥95%
  - Deliverable existence + version + IQ (Devon.md + tracks-prism-impl skill)
  - trace 闭合（`trac check trace` 无硬错误）
  - reach 闭合（`trac check reach` 无孤岛）
- **Failure semantics**: Any required check failure blocks merge. live 通道（`tests/e2e_live/`）缺凭据 skip，非 required check。

---

## 8. AC Coverage

每个 AC ≥1 测试、每个测试 ≥1 AC（CI 闭合）。跨模块合同（interfaces.md `modules` 列 ≥2）至少一个 integration 测试。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。

> **Prism:** PRISM-MTEST-BLOCKER-3 [severity=blocker] [artifact=test-plan.md §8 + §9] [AC=all §8 ACs with test_refs] criterion=1 (忠于AC) + criterion=3 (counterexample 绑定 traceability)
> 
> §8 AC Coverage table and §9 SM-01 transition table reference 157 unique test function names (file::case), but 107 of those function names have no matching def in any test file on disk. This breaks the §8 canonical header contract (AC-FR0210-02: test_refs traceable to test-plan §8) and makes the AC->test traceability non-verifiable.
> 
> Examples of mismatches (§8 reference -> actual file function):
> - §8 AC-FR0190-01: test_dispatch_materialization.py::test_assignment_payload_complete -> actual def is test_dispatch_assignments_have_complete_public_payload (tests/integration/test_dispatch_materialization.py:13)
> - §8 AC-FR0190-10: test_dispatch_materialization.py::test_coverage_all_agents_and_runtime -> no such function in file
> - §8 AC-FR0170-02: test_deliverables.py::test_devon_in_deliverables + test_no_permission_block -> neither function exists in tests/unit/test_deliverables.py
> - §8 AC-FR0010-05: test_machine_m_impl.py::test_explicit_control_flow -> file does not exist; only tests/unit/test_machine_m_test.py has test_explicit_control_flow with @v0.4 marker
> - §9 SM-01 table: every row references test_machine_m_impl.py::test_* functions - none of these functions exist (file does not exist)
> 
> criterion 1 requires every test to faithfully cover its referenced AC. When §8 names a test function that doesn't exist, the AC has no verifiable coverage - the traceability link is broken. This also blocks Runtime's RED_CHECK trace validation (FR-0080/FR-0130): test_refs in tasks.json must point to real collectible tests.
> 
> Expected revision: reconcile §8 and §9 test function references with actual on-disk test functions. Either (a) rename the actual test functions to match §8, or (b) update §8/§9 to reference the actual function names. For ACs whose tests are genuinely missing (not just misnamed), write the missing test functions. After reconciliation, every §8 test_ref must resolve to a real, collectible pytest function.
>> **Prism:** PRISM-MTEST-ATTEMPT2-VERIFY [severity=blocker] [criterion=1+3] Attempt 2 re-verification: Shield did not reply to this thread (reply_count=0). Re-ran traceability check: 156 unique test_refs in §8/§9 tables, only 17 resolve to real pytest functions (11%), 94 refs point to 20 non-existent files, 45 more point to missing functions in existing files. The 3 ACs explicitly named in BLOCKER-1 (AC-FR0010-05, AC-FR0170-02, AC-NFR0010-01) still have zero real test coverage. No test files were modified in attempt 2 (git status: only test-plan.md changed, only to restore Prism blockquotes). Verdict: REVISE — blocker unresolved. Shield must either (a) write the missing 20 test files and 45 missing test functions with @v0.5 markers asserting via interfaces.md §4 outlets, or (b) update §8/§9 test_refs to point only at existing tests and explicitly mark the gap as foundation tasks for this M-TEST revision.
>> **Prism:** PRISM-MTEST-ATTEMPT3-VERIFY [severity=blocker] [criterion=1+3] Attempt 3 re-verification: Shield did not reply to this thread (reply_count=1, only Prism ATTEMPT2-VERIFY). Re-ran traceability check: 167 unique test_refs in §8/§9 tables, only 20 resolve to real pytest functions (12%), 114 refs point to 20 non-existent files (test_machine_m_impl.py, test_kernel_purity_m_impl.py, test_taskgraph.py, test_rgr.py, test_quality_gate.py, test_island_gate_1.py, test_red_phase.py, test_red_gate_m_impl.py, test_prism_plan.py, test_task_dispatch.py, test_prism_red.py, test_green_phase.py, test_green_gate.py, test_green_commit.py, test_refactor.py, test_task_review.py, test_prism_final.py, test_diagnose_four_way.py, test_shield_fix.py, test_events_append_only_m_impl.py), 33 more point to missing functions in existing files (e.g. test_dispatch_materialization.py::test_assignment_payload_complete -> actual def is test_dispatch_assignments_have_complete_public_payload; test_baseline_recalc.py::test_baseline_frozen_event absent). The 3 ACs named in BLOCKER-1 (AC-FR0010-05, AC-FR0170-02, AC-NFR0010-01) still have zero @v0.5 marker coverage (grep finds no AC-FR0010-05@v0.5 / AC-FR0170-02@v0.5 / AC-NFR0010-01@v0.5 in any test file). No test files modified in attempt 3 (git status: only test-plan.md changed; git diff tests/ empty). Verdict: REVISE - blocker unresolved across 3 attempts. Shield has not engaged this thread. §8/§9 test_refs still do not resolve to real collectible pytest functions, breaking AC-FR0210-02 (test_refs traceable to test-plan §8) and criterion 1.

> **Prism:** PRISM-MTEST-BLOCKER-2 [severity=blocker] [artifact=tests/counterexamples/v0.5/kill-manifest.json] [AC=all required integration/e2e ACs in §8] criterion=3 (counterexample 绑定)
> 
> criterion 3 requires each required integration/e2e test to bind a counterexample that the test can kill (verified). The kill-manifest.json declares 19 bindings, ALL with result=pending-implementation, and 0 with result=killed. The manifest's own contract field states: 'integration/e2e tests enter via the public CLI and fail at the CLI-routing / event-observation layer before reaching any mutable stub. A mutation applied to a stub the test never reaches cannot change the test outcome, so no counterexample can be killed (verified) until Devon implements the routing that makes the stub reachable.'
> 
> criterion 3 is explicit: '缺失 counterexample 或 counterexample 不能被测试杀死的判 revise.' All 19 counterexamples cannot be killed - this is a blanket criterion 3 failure. The pending-implementation status means the test assets cannot demonstrate counterexample binding (non-vacuousness): there is no evidence that any test would distinguish a correct implementation from a contract-violating mutation.
> 
> Several bindings explicitly record verification attempts that failed: e.g. baseline_events.patch ('test still failed identically (assert 0 == 1) because run_m_impl_journey never reaches create_test_authority_worktree'), rgr_git.patch ('test still failed identically (assert []) because the test observes red/green events that are never emitted'), red_classification.patch, taskgraph_happy.patch, taskgraph_errors.patch, quality_gate.patch, worktree_isolation.patch - all report the same pattern: mutation applied, test outcome unchanged, counterexample not killed.
> 
> Expected revision: this is a known M-TEST Red-phase limitation (stubs raise before reaching mutable code), but criterion 3 still requires killability. Shield must either (a) restructure the tests so the counterexample mutations are reachable in the Red phase (e.g. assert on the stub-routing layer's observable output rather than downstream events), or (b) provide an explicit M-TEST-phase killability plan with a per-binding analysis of how the kill will be demonstrated once Devon implements the routing, anchored in test-plan.md §3 or a dedicated counterexample-killability section.
>> **Prism:** PRISM-MTEST-ATTEMPT2-VERIFY [severity=blocker] [criterion=3] Attempt 2 re-verification: Shield did not reply to this thread (reply_count=0). Re-checked kill-manifest.json: 19 bindings total, 0 killed, 19 pending-implementation (unchanged from attempt 1). No test files modified in attempt 2 (git status: only test-plan.md changed). The manifest's own contract field still states mutations applied to stubs the test never reaches cannot change test outcome — so 0/19 counterexamples are killable. criterion 3 explicit: 'counterexample 不能被测试杀死的判 revise'. Verdict: REVISE — blocker unresolved. Shield must either (a) restructure the affected tests so counterexample mutations are reachable in the Red phase (assert on stub-routing layer's observable output rather than downstream events), or (b) provide an explicit M-TEST-phase killability plan with per-binding analysis of how the kill will be demonstrated once Devon implements routing, anchored as a dedicated counterexample-killability section in test-plan.md.
>> **Prism:** PRISM-MTEST-ATTEMPT3-VERIFY [severity=blocker] [criterion=3] Attempt 3 re-verification: Shield did not reply to this thread (reply_count=1, only Prism ATTEMPT2-VERIFY). Re-checked kill-manifest.json: 19 bindings total, 0 killed, 19 pending-implementation (unchanged across all 3 attempts). No test files modified in attempt 3 (git status: only test-plan.md changed; git diff tests/ empty). The manifest's own contract field still states mutations applied to stubs the test never reaches cannot change test outcome, so 0/19 counterexamples are killable. criterion 3 explicit: 'counterexample 不能被测试杀死的判 revise'. Verdict: REVISE - blocker unresolved across 3 attempts. Shield has not engaged this thread.

> **Prism:** PRISM-MTEST-BLOCKER-1 [severity=blocker] [artifact=test-plan.md §8 + §2.1] [AC=AC-FR0010-05, AC-FR0170-02, AC-NFR0010-01] criterion=1 (忠于AC)
> 
> §8 AC Coverage table and §2.1 directory layout reference ~21 test files that do not exist on disk. Verified missing: tests/unit/test_machine_m_impl.py, tests/unit/test_kernel_purity_m_impl.py, tests/unit/test_taskgraph.py, tests/unit/test_rgr.py, tests/unit/test_quality_gate.py, tests/unit/test_worktree.py, tests/integration/test_island_gate_1.py, tests/integration/test_prism_plan.py, tests/integration/test_task_dispatch.py, tests/integration/test_red_phase.py, tests/integration/test_red_gate_m_impl.py, tests/integration/test_prism_red.py, tests/integration/test_green_phase.py, tests/integration/test_green_gate.py, tests/integration/test_green_commit.py, tests/integration/test_refactor.py, tests/integration/test_task_review.py, tests/integration/test_prism_final.py, tests/integration/test_diagnose_four_way.py, tests/integration/test_shield_fix.py, tests/integration/test_events_append_only_m_impl.py.
> 
> Of 157 unique test function references in §8, 107 have no corresponding def in any test file.
> 
> Three required ACs are left with zero real test coverage:
> - AC-FR0010-05 (显式控制流驱动; kernel 纯函数边界): §8 row references test_machine_m_impl.py::test_explicit_control_flow + test_kernel_purity_m_impl - neither file exists. The only test_explicit_control_flow in the repo is tests/unit/test_machine_m_test.py and carries @v0.4 marker, not @v0.5.
> - AC-FR0170-02 (Devon.md deliverables; no permission block): §8 row references test_deliverables.py::test_devon_in_deliverables + test_no_permission_block - neither function exists in tests/unit/test_deliverables.py (verified: file has no such functions).
> - AC-NFR0010-01 (decide()/project() no IO/clock/env): §8 row references test_kernel_purity_m_impl.py::test_no_io_no_clock_no_env - file does not exist.
> 
> Criterion 1 (忠于AC) requires every test to faithfully cover its referenced AC; here the referenced tests do not exist, so the ACs have no coverage. Expected revision: write the missing test files/functions with @v0.5 TRACKS-TRACE markers asserting the ACs' observable effects via the outlets in interfaces.md §4, or revise §8 to point only at existing tests and explicitly mark the missing ones as foundation tasks to be written by Shield in this M-TEST revision.
>> **Prism:** PRISM-MTEST-ATTEMPT2-VERIFY [severity=blocker] [criterion=1] Attempt 2 re-verification: Shield did not reply to this thread (reply_count=0). Re-checked: 20 test files still missing from disk (test_machine_m_impl.py, test_kernel_purity_m_impl.py, test_taskgraph.py, test_rgr.py, test_quality_gate.py, test_island_gate_1.py, test_red_phase.py, test_red_gate_m_impl.py, test_green_phase.py, test_green_gate.py, test_green_commit.py, test_refactor.py, test_task_review.py, test_prism_final.py, test_diagnose_four_way.py, test_shield_fix.py, test_events_append_only_m_impl.py, test_prism_plan.py, test_prism_red.py, test_task_dispatch.py). The 3 required ACs still have zero real coverage: AC-FR0010-05 (test_machine_m_impl.py::test_explicit_control_flow - file does not exist), AC-FR0170-02 (test_deliverables.py::test_devon_in_deliverables + test_no_permission_block - neither function exists), AC-NFR0010-01 (test_kernel_purity_m_impl.py::test_no_io_no_clock_no_env - file does not exist). No test files were modified in attempt 2 (git status: only test-plan.md changed). Verdict: REVISE - blocker unresolved.
>> **Prism:** PRISM-MTEST-ATTEMPT3-VERIFY [severity=blocker] [criterion=1] Attempt 3 re-verification: Shield did not reply to this thread (reply_count=1, only Prism ATTEMPT2-VERIFY). Re-checked: 20 test files still missing from disk (test_machine_m_impl.py, test_kernel_purity_m_impl.py, test_taskgraph.py, test_rgr.py, test_quality_gate.py, test_island_gate_1.py, test_red_phase.py, test_red_gate_m_impl.py, test_green_phase.py, test_green_gate.py, test_green_commit.py, test_refactor.py, test_task_review.py, test_prism_final.py, test_diagnose_four_way.py, test_shield_fix.py, test_events_append_only_m_impl.py, test_prism_plan.py, test_prism_red.py, test_task_dispatch.py). The 3 required ACs still have zero real coverage: AC-FR0010-05 (test_machine_m_impl.py::test_explicit_control_flow - file does not exist), AC-FR0170-02 (test_deliverables.py::test_devon_in_deliverables + test_no_permission_block - neither function exists in tests/unit/test_deliverables.py, verified: file has test_real_deliverables_consistent / test_missing_deliverable / test_missing_version etc. but NOT the two named functions), AC-NFR0010-01 (test_kernel_purity_m_impl.py::test_no_io_no_clock_no_env - file does not exist). No test files modified in attempt 3 (git status: only test-plan.md changed; git diff tests/ empty). Verdict: REVISE - blocker unresolved across 3 attempts. Shield has not engaged this thread.

「IF- 归属」列的取值只能来自 interfaces.md §5 IF- 标识注册表（v0.5 新增 IF-IMPL-001~007、IF-DEVON-001 + v0.4 既有 IF-MTEST-001/002、IF-SHIELD-001、IF-TRACE-001/002、IF-REACH-001/002、IF-VALIDATE-001）。design-trace validator（`check_design_trace`，FR-0140 扩展）校验：每条 integration/e2e AC 的 IF- 归属非空、且每个 IF- 标识在 interfaces.md §5（v0.4 或 v0.5）已定义（有效性校验，非仅存在性）。粒度对齐 architecture.md §1.1 增长轴（Devon 实现 task 边界）。

### 8a. FR-0010 M-IMPL 阶段注册与子状态机驱动

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0010-01（M-TEST EXIT -> stage.entered(M-IMPL), substate=BASELINE；无 human 插队） | unit + integration | test_machine_m_impl.py::test_enter_m_impl_from_m_test_exit, test_m_impl_cycle.py::test_enter_baseline | IF-IMPL-001 |
| AC-FR0010-02（trac status 报告 stage=M-IMPL + substate；_NEXT_STAGE 接续 + boundary） | unit + e2e | test_machine_m_impl.py::test_next_stage_m_test_to_m_impl, test_m_impl_journey.py::test_boundary_after_m_impl | IF-IMPL-001 |
| AC-FR0010-03（SM-01 转移严格遵循清单；非法转移不产出） | unit + integration | test_machine_m_impl.py::test_sm01_transitions_enforced, test_m_impl_cycle.py::test_illegal_transition_rejected | IF-IMPL-001 |
| AC-FR0010-04（既有阶段行为不变；M-TEST 之前事件前缀稳定） | e2e | test_full_journey.py::test_full_journey_to_boundary（更新：M-TEST 前缀稳定 + M-IMPL 接续 + boundary 移至 M-IMPL 后） | IF-IMPL-001, IF-MTEST-001 |
| AC-FR0010-05（显式控制流驱动；kernel 纯函数边界） | unit | test_machine_m_impl.py::test_explicit_control_flow + test_kernel_purity_m_impl | IF-IMPL-001 |

### 8b. FR-0020 BASELINE 重算与测试资产冻结

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0020-01（BASELINE 重算 -> baseline.frozen；current -> PLANNING） | unit + integration | test_machine_m_impl.py::test_baseline_current_to_planning, test_baseline_recalc.py::test_baseline_frozen_event | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0020-02（baseline 缺失/stale/冲突 -> NEEDS_ATTENTION；reconcile -> BASELINE；rolled_back） | unit + integration | test_machine_m_impl.py::test_baseline_stale_to_needs_attention + test_baseline_reconcile, test_baseline_recalc.py::test_needs_attention_and_rollback | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0020-03（冻结测试路径集；缺层归属 -> 硬错误；trac validate --file test-plan.md 判失败） | integration | test_baseline_recalc.py::test_frozen_test_paths + test_missing_layer_attribution_fails | IF-IMPL-002, IF-VALIDATE-001 |
| AC-FR0020-04（冻结路径集供 test-authority/gate worktree；语言无关不硬编码） | integration | test_baseline_recalc.py::test_frozen_paths_consumed_by_worktrees | IF-IMPL-002, IF-IMPL-006 |

### 8c. FR-0030 PLANNING：Archer 拆 task graph

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0030-01（dispatch Archer 拆 task graph；taskgraph.committed；每 task 纵向切片 + scope + IF- + budget） | unit + integration | test_machine_m_impl.py::test_planning_dispatch_archer, test_taskgraph_validate.py::test_taskgraph_committed | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0030-02（validate DAG 无环 / scope 不重叠 / AC 覆盖闭合；fail 重派 <=3 -> escalation） | unit + integration | test_taskgraph.py::test_validate_dag + test_validate_scope + test_validate_ac_coverage, test_taskgraph_validate.py::test_validate_fail_redispatch | IF-IMPL-003, IF-IMPL-001 |
| AC-FR0030-03（tasks.md Runtime 确定性生成；进展投影入 events/db；trac report 重建） | integration | test_taskgraph_validate.py::test_tasksmd_projected + test_report_progress | IF-IMPL-002, IF-IMPL-007 |

### 8d. FR-0040 ISLAND_GATE_1：程序复核

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0040-01（六元组复核通过 -> PRISM_PLAN） | unit + integration | test_machine_m_impl.py::test_island_gate_1_pass, test_island_gate_1.py::test_six_tuple_check | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0040-02（不闭合 -> verdict.failed(island) -> PLANNING 重派 Archer） | unit + integration | test_machine_m_impl.py::test_island_gate_1_fail, test_island_gate_1.py::test_not_closed_redispatch | IF-IMPL-001, IF-IMPL-002 |

### 8e. FR-0050 PRISM_PLAN：判据包绑定与切片评审

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0050-01（dispatch Prism 评 task graph 切片；反自述三件套：assignment 含判据包、verdict 携带 identity、Runtime 回读不匹配判失败） | unit + integration | test_machine_m_impl.py::test_prism_plan_dispatch, test_prism_plan.py::test_anti_self_report_triple + test_criteria_pack_mismatch | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0050-02（pass -> TASK_DISPATCH；revise -> PLANNING；设计缺口 -> M-DESIGN；需求缺口 -> M-ACC/M-SPEC） | unit + integration | test_machine_m_impl.py::test_prism_plan_pass + test_prism_plan_revise + test_design_gap_rollback + test_requirement_gap_rollback, test_prism_plan.py::test_verdict_routing | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0050-03（revise 必须经 trac discuss 锚定线程；无锚定 -> revise_without_findings） | integration | test_prism_plan.py::test_revise_without_findings_rejected | IF-IMPL-001 |
| AC-FR0050-04（反自述三件套适用于 plan/red/final/diagnostic 全部四种 Prism 派发） | integration | test_prism_plan.py::test_anti_self_report_all_four_dispatches | IF-IMPL-002 |

### 8f. FR-0060 TASK_DISPATCH：DAG 调度与单写者 manifest

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0060-01（DAG ready task 选 + 单写者 lease + manifest 创建 -> writelock.granted + task.started -> RED） | unit + integration | test_machine_m_impl.py::test_task_dispatch_to_red, test_task_dispatch.py::test_writelock_and_manifest | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0060-02（task 变绿子集 = 单测 + IF- 命中 int 子集；全部完成 -> ISLAND_GATE_2；还有 -> TASK_DISPATCH） | unit + integration | test_machine_m_impl.py::test_all_tasks_done_to_island_gate_2 + test_more_ready_tasks_to_dispatch, test_task_dispatch.py::test_green_subset_selection | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0060-03（[P] 并行标记只记录不并发；串行顺序执行） | integration | test_task_dispatch.py::test_parallel_marker_serial_execution | IF-IMPL-002 |

### 8g. FR-0070 RED：Devon 隔离与私有 R ref

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0070-01（dispatch Devon phase=red 在 Devon candidate worktree；worktree 在 Shield WRITE 前创建） | unit + integration | test_machine_m_impl.py::test_red_dispatch_devon, test_red_phase.py::test_devon_candidate_worktree_created | IF-IMPL-001, IF-IMPL-002, IF-IMPL-006 |
| AC-FR0070-02（Devon 只添加 unit test；outcome 含产品代码或 Shield 测试 -> 判失败；文件访问被 manifest 限定） | integration | test_red_phase.py::test_test_only_diff + test_over_reach_rolled_back | IF-IMPL-002, IF-DEVON-001 |
| AC-FR0070-03（RED_CHECKPOINT 创建私有 commit R + git ref；red.checkpointed；git show 存在） | unit + integration | test_rgr.py::test_create_red_ref, test_red_phase.py::test_red_checkpoint_event | IF-IMPL-002, IF-IMPL-004 |
| AC-FR0070-04（R 不可变：重试改写 R ref compare-and-set 失败；rev-parse 前后同一 SHA） | unit + integration | test_rgr.py::test_r_ref_immutable, test_red_phase.py::test_r_ref_survives_retry | IF-IMPL-004 |
| AC-FR0070-05（三 worktree 方案：gate worktree 组合 C_design + frozen bundle + Devon candidate；frozen bundle 永不合入 Devon candidate） | integration | test_red_phase.py::test_three_worktree_scheme + test_frozen_bundle_not_merged | IF-IMPL-002, IF-IMPL-006 |

### 8h. FR-0080 RED_GATE：合法 Red 分类

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0080-01（合法红 = 行为断言失败 / symbol 缺失 -> RED_CHECKPOINT；stub_token_failure 不在 M-IMPL 合法红之列） | unit + integration | test_rgr.py::test_classify_red_legit + test_stub_token_not_legit, test_red_gate_m_impl.py::test_legit_red_to_checkpoint | IF-IMPL-002, IF-IMPL-004 |
| AC-FR0080-02（非法红 = collection/语法/fixture/import 错误、意外通过 -> 重派 Devon；verdict.failed(red_invalid)） | unit + integration | test_rgr.py::test_classify_red_illegit + test_unexpected_pass, test_red_gate_m_impl.py::test_illegit_red_to_red | IF-IMPL-001, IF-IMPL-002 |

### 8i. FR-0090 PRISM_RED：Red checkpoint 范围评审

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0090-01（dispatch Prism 评 B..R 范围；反自述三件套） | unit + integration | test_machine_m_impl.py::test_prism_red_dispatch, test_prism_red.py::test_br_range_review + test_anti_self_report | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0090-02（pass 绑定 R -> GREEN；revise -> RED 新 attempt；revise 经 trac discuss 锚定） | unit + integration | test_machine_m_impl.py::test_prism_red_pass + test_prism_red_revise, test_prism_red.py::test_revise_anchored | IF-IMPL-001, IF-IMPL-002 |

### 8j. FR-0100 GREEN：最小实现与受控 diff 回灌

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0100-01（从 R tree 恢复 worktree；dispatch Devon phase=green；R 测试不可改；outcome 含 R 测试改动 -> 判失败） | unit + integration | test_machine_m_impl.py::test_green_dispatch_devon, test_green_phase.py::test_r_test_immutable | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0100-02（受控 diff 按 manifest 回灌主仓；白名单外不回灌；视图终态清理） | integration | test_green_phase.py::test_diff_rebase_manifest + test_outside_manifest_not_rebased | IF-IMPL-002, IF-IMPL-006 |

### 8k. FR-0110 GREEN_GATE：粒度门禁与反馈脱敏

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0110-01（targeted 单测 + 历史单测 + int 子集 + lint/format/type/static + 合同；第一轮不跑 e2e；全过 -> GREEN_COMMIT；缺陷 -> GREEN） | unit + integration | test_machine_m_impl.py::test_green_gate_pass + test_green_gate_fail, test_green_gate.py::test_gate_checks + test_no_e2e_first_round | IF-IMPL-001, IF-IMPL-002, IF-IMPL-005 |
| AC-FR0110-02（int/e2e 由 Runtime 在独立 gate worktree 运行并归因） | integration | test_green_gate.py::test_gate_worktree_runs_int | IF-IMPL-002, IF-IMPL-006 |
| AC-FR0110-03（反馈脱敏：单测失败回完整输出；int/e2e 失败只回分类诊断不回断言原文） | integration | test_green_gate.py::test_feedback_desensitization | IF-IMPL-002, IF-IMPL-005 |
| AC-FR0110-04（int 失败归因不明 -> DIAGNOSE） | unit + integration | test_machine_m_impl.py::test_green_gate_to_diagnose, test_green_gate.py::test_int_attribution_unknown | IF-IMPL-001, IF-IMPL-002 |

### 8l. FR-0120 GREEN_COMMIT：正式 commit G 与 lineage 证明

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0120-01（创建正式 commit G；green.committed；G parent=B；trailers Tracks-Task/Tracks-Attempt/Tracks-R/Tracks-Issue/Tracks-AC） | unit + integration | test_rgr.py::test_create_green_commit, test_green_commit.py::test_g_commit_parent_and_trailers | IF-IMPL-002, IF-IMPL-004 |
| AC-FR0120-02（R 先于 G lineage 证明：ref + trailer + 事件序列三件联合；不作 Git ancestry 断言） | unit + integration | test_rgr.py::test_verify_lineage, test_green_commit.py::test_lineage_proof_not_ancestry | IF-IMPL-002, IF-IMPL-004 |

### 8m. FR-0130 REFACTOR 与质量门禁分层

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0130-01（dispatch Devon phase=refactor；可返回 no_change + 理由；no_change + 全绿 -> TASK_REVIEW） | unit + integration | test_machine_m_impl.py::test_refactor_dispatch + test_refactor_no_change, test_refactor.py::test_no_change_accepted | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0130-02（REFACTOR_GATE 重跑 GREEN_GATE；质量门禁分层：生产全检查、测试仅 R0801+C0302） | unit + integration | test_quality_gate.py::test_run_production_checks + test_run_test_checks, test_refactor.py::test_gate_layering | IF-IMPL-002, IF-IMPL-005 |
| AC-FR0130-03（动 public interface -> stage.rolled_back upstream；通过 -> TASK_REVIEW；失败 -> REFACTOR） | unit + integration | test_machine_m_impl.py::test_public_interface_rollback + test_refactor_pass + test_refactor_fail, test_refactor.py::test_public_interface_changed | IF-IMPL-001, IF-IMPL-002 |

### 8n. FR-0140 TASK_REVIEW 与 PRISM_FINAL

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0140-01（TASK_REVIEW 校验 scope/secret/AC trace/lineage/budget；通过 -> PRISM_FINAL；budget/scope fail -> GREEN） | unit + integration | test_machine_m_impl.py::test_task_review_pass + test_task_review_fail, test_task_review.py::test_scope_lineage_budget | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0140-02（PRISM_FINAL dispatch Prism 评完整 range + lineage；反自述三件套；pass -> TASK_DONE；revise(实现) -> GREEN；revise(Red) -> RED） | unit + integration | test_machine_m_impl.py::test_prism_final_pass + test_prism_final_revise_impl + test_prism_final_revise_red, test_prism_final.py::test_full_range_review | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0140-03（revise 经 trac discuss 锚定；task.completed 在 pass 后出现；trac report 展示 RGR lineage） | integration | test_prism_final.py::test_revise_anchored + test_task_completed_event + test_report_lineage | IF-IMPL-001, IF-IMPL-002, IF-IMPL-007 |

### 8o. FR-0150 DIAGNOSE 四路诊断与 SHIELD_FIX

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0150-01（DIAGNOSE 四路诊断；分流永不交给 Human；impl_defect -> GREEN；test_defect -> SHIELD_FIX；stub_gap -> M-DESIGN；ac/spec_gap -> M-ACC/M-SPEC） | unit + integration | test_machine_m_impl.py::test_diagnose_impl_defect + test_diagnose_test_defect + test_diagnose_stub_gap + test_diagnose_ac_spec_gap, test_diagnose_four_way.py::test_no_human_routing | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0150-02（分流结论落 verdict.failed(reason)，reason 限于封闭集；trac report 展示分类与路由） | integration | test_diagnose_four_way.py::test_verdict_failed_reason_closed_set + test_report_classification | IF-IMPL-002, IF-IMPL-007 |
| AC-FR0150-03（SHIELD_FIX dispatch Shield 修测试；test.committed；重跑 GREEN_GATE；Shield 修复落在冻结路径集内） | integration | test_shield_fix.py::test_shield_fix_dispatch + test_test_committed + test_rerun_green_gate | IF-IMPL-002, IF-SHIELD-001 |
| AC-FR0150-04（return upstream 后目标之后的 task graph/baselines/lineage/commits 标记 stale/superseded；不复用旧绿色证据） | integration | test_diagnose_four_way.py::test_stale_evidence_after_rollback | IF-IMPL-002 |

### 8p. FR-0160 ISLAND_GATE_2：出口门禁与边界退出

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0160-01（全部 task 完成后进入；trac check reach 无孤岛 + 全量 int+e2e 变绿 -> 退出；失败不退出） | integration | test_island_gate_2.py::test_reach_pass + test_full_int_e2e_pass + test_fail_no_exit | IF-IMPL-002, IF-REACH-002 |
| AC-FR0160-02（全量执行有失败 -> DIAGNOSE；verdict.failed(island) -> PLANNING） | unit + integration | test_machine_m_impl.py::test_island_gate_2_to_diagnose + test_island_gate_2_to_planning, test_island_gate_2.py::test_fail_routing | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0160-03（通过 -> stage.exited(M-IMPL) -> run.completed(boundary)；不进入 M-VERIFY） | e2e | test_m_impl_journey.py::test_boundary_after_m_impl | IF-IMPL-001 |
| AC-FR0160-04（M-IMPL 无 Human 门禁：退出依据全程序证据，无 human.review/approval 作为退出前置） | e2e | test_m_impl_journey.py::test_no_human_gate | IF-IMPL-001 |

### 8q. FR-0170 Devon opencode agent 接入与 manifest 越界审计

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0170-01（AGENT_NAME 增补 devon->Devon；物化与回收同构） | unit + integration | test_deliverables.py::test_devon_agent_name, test_devon_dispatch.py::test_materialization_lifecycle | IF-DEVON-001 |
| AC-FR0170-02（Devon.md 加入 deliverables；frontmatter version + IQ；无 permission 块） | unit | test_deliverables.py::test_devon_in_deliverables + test_no_permission_block | IF-DEVON-001 |
| AC-FR0170-03（manifest 越界审计：越权写 -> over_reach failure_class -> git 回滚；回滚仅移除 Devon 改动） | integration | test_devon_dispatch.py::test_over_reach_rolled_back | IF-DEVON-001 |

### 8r. FR-0180 tasks.json / tasks.md 真相源与 Runtime 解析

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0180-01（tasks.json 为 task graph 唯一机器真相；Runtime 解析驱动 DAG 调度；JSON schema 含 9 项必填 task info：task_id/issue_number/description/ac_refs/fr_refs/if_ids/test_refs/scope_boundary/depends_on + batch/parallel/budget；tasks.md 为人类可读投影由 Runtime 从 tasks.json 确定性生成；进展投影入 events/db；trac report 重建） | integration | test_tasksjson_validate.py::test_tasksjson_parsed_by_runtime + test_tasksmd_projected + test_report_rebuild | IF-IMPL-003, IF-IMPL-007, IF-IMPL-002 |
| AC-FR0180-02（trac validate --file tasks.json 校验 DAG 无环 / scope 边界不重叠 / required AC 覆盖闭合 / IF- 有效性 / issue number 有效性；5 项 check） | integration | test_tasksjson_validate.py::test_validate_dag + test_validate_scope + test_validate_ac_coverage + test_validate_if_validity + test_validate_issue_numbers | IF-IMPL-003, IF-VALIDATE-001 |
| AC-FR0180-04（trac validate --file tasks.json 5 项 check 逐项校验：DAG acyclic / scope non-overlap / required AC coverage closure / IF- validity / issue number validity；任一 fail 非零退出并指出位置） | integration | test_tasksjson_validate.py::test_five_checks_individual_failures | IF-IMPL-003, IF-VALIDATE-001 |
| AC-FR0180-05（Runtime 与 Prism 职责分工：Runtime 只做确定性结构校验，不做语义评审；tasks.md 语义保真度不由 Runtime 校验） | unit | test_tasksjson_validate.py::test_runtime_validation_is_structural_only | IF-VALIDATE-001 |

### 8s. FR-0190 dispatch 物化完整性合同

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0190-01（command.issued 前 assignment 物化完整上下文；含 10 项物化字段） | integration | test_dispatch_materialization.py::test_assignment_payload_complete | IF-IMPL-002 |
| AC-FR0190-02（M-TEST/M-IMPL escalation 允许 to_stage=M-DESIGN；M-TEST 回归基线 + M-IMPL 新增路径） | unit + integration | test_dispatch_materialization.py::test_escalation_to_design_m_impl + test_m_test_escalation_regression | IF-IMPL-001, IF-IMPL-002 |
| AC-FR0190-03（canonical .tracks/project/project.toml 唯一允许；其他 .tracks/** fail closed） | integration | test_dispatch_materialization.py::test_canonical_project_toml | IF-IMPL-002 |
| AC-FR0190-04（M-DESIGN 输出合同：test-plan §8 canonical header + interfaces §5 IF Registry；解析 {ac_id, layers, if_ids}；missing/empty/duplicate/unregistered fail closed） | integration | test_dispatch_materialization.py::test_design_output_contract | IF-IMPL-002, IF-VALIDATE-001 |
| AC-FR0190-05（test_tasks 注入：Shield WRITE assignment 含非空 test_tasks；无效输入 -> stub_gap） | integration | test_dispatch_materialization.py::test_test_tasks_injection + test_invalid_input_stub_gap | IF-IMPL-002 |
| AC-FR0190-06（ResultCheckpoint：invalid retry 清 dispatch flags；Shield WRITE requires_diff=true；pre_dirty_snapshot 持久化与 post 比较） | integration | test_dispatch_materialization.py::test_result_checkpoint_flags + test_requires_diff + test_pre_dirty_snapshot | IF-IMPL-002 |
| AC-FR0190-07（collection 口径：tests/**/*.py 可 checkpoint；collect-only 只对 test modules；只有 helper fail closed；conftest 不单独判 no-tests） | integration | test_dispatch_materialization.py::test_collection口径 | IF-IMPL-002 |
| AC-FR0190-08（host project command：.venv/bin/python 不存在回退 sys.executable；项目自有 venv 优先） | integration | test_dispatch_materialization.py::test_host_python_fallback | IF-IMPL-002 |
| AC-FR0190-09（rollback evidence：仅 M-TEST stub_gap->M-DESIGN 清 stale failure；其他语义回退保留 evidence） | integration | test_dispatch_materialization.py::test_rollback_evidence_semantics | IF-IMPL-002 |
| AC-FR0190-10（覆盖范围：Scribe/Sage/Lex/Archer/Prism/Shield dispatch + Runtime validate/checkpoint/publish/collect/run/red/commit/seal） | integration | test_dispatch_materialization.py::test_coverage_all_agents_and_runtime | IF-IMPL-002 |

### 8t. FR-0200 可休眠与崩溃恢复

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0200-01（phase 边界事件；重启从 lineage + 事件回放恢复；不重跑已完成 task） | integration | test_crash_recovery.py::test_replay_no_rerun | IF-IMPL-002 |
| AC-FR0200-02（R ref 不可变保证崩溃后 lineage 不丢失；G trailers 可重建 R-G 绑定；崩溃 reconcile 视图终态清理） | integration | test_crash_recovery.py::test_r_ref_survives_crash + test_g_trailers_rebuild + test_reconcile_cleanup | IF-IMPL-002, IF-IMPL-004 |

### 8u. NFR-0010 M-IMPL 控制流维持 kernel 纯函数边界

| AC id | layer | test | IF |
|---|---|---|---|
| AC-NFR0010-01（decide()/project() 不碰 IO/clock/env/文件系统） | unit | test_kernel_purity_m_impl.py::test_no_io_no_clock_no_env | IF-IMPL-001 |
| AC-NFR0010-02（副作用归 executor；drop 投影表后重建一致） | unit + integration | test_kernel_purity_m_impl.py::test_rebuild_from_events, test_m_impl_cycle.py::test_drop_rebuild | IF-IMPL-001, IF-IMPL-002 |

### 8v. NFR-0020 M-IMPL 事件维持 append-only 事件溯源

| AC id | layer | test | IF |
|---|---|---|---|
| AC-NFR0020-01（M-IMPL 全程事件 append-only，不改写既有行） | integration | test_events_append_only_m_impl.py::test_events_append_only | IF-IMPL-001, IF-IMPL-002 |
| AC-NFR0020-02（投影表可从事件完整重建） | integration | test_events_append_only_m_impl.py::test_rebuild_projections | IF-IMPL-001, IF-IMPL-002 |

### 8w. NFR-0030 dispatch 活动性可观测

| AC id | layer | test | IF |
|---|---|---|---|
| AC-NFR0030-01（trac run 为长时间 Agent 派发发出简洁已 flush 控制台活动；不流式输出海量 stdout；不设 elapsed-time 超时） | integration | test_trac_retry.py::test_dispatch_activity_output | IF-IMPL-002 |
| AC-NFR0030-02（<=3 次失败后 trac run 与 trac status 暴露 attempt 计数 + 失败类 + 原因） | unit + integration | test_machine_m_impl.py::test_attempt_count_exposed, test_trac_retry.py::test_status_shows_attempt_and_failure | IF-IMPL-001 |
| AC-NFR0030-03（trac retry 追加 human.retry 事件、清 escalation gate、重置 attempt 预算、保留失败证据、不自动重派；非 escalation 拒绝；--clear-evidence 语义） | unit + integration | test_trac_retry.py::test_retry_appends_event + test_clears_gate + test_resets_budget + test_retains_evidence + test_no_auto_redispatch + test_non_escalation_rejected + test_clear_evidence | IF-IMPL-001 |

### 8x. FR-0210 Shield test-plan 测试归属边界

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0210-01（test-plan.md 继续指导 Shield 的 M-TEST 环境/fixture/ground-truth/分层与冻结；不复制到 tasks.json；trac validate --file test-plan.md 校验 §8 canonical header 存在） | integration | test_testplan_ownership.py::test_testplan_not_copied_to_tasksjson + test_canonical_header_present | IF-IMPL-007, IF-VALIDATE-001 |
| AC-FR0210-02（每个 task 的 test_refs 可追溯到 test-plan §8 行；无悬空引用） | integration | test_testplan_ownership.py::test_test_refs_traceable_to_section8 | IF-IMPL-003, IF-IMPL-007 |
| AC-FR0210-03（test-plan §8 AC Coverage 表与 tasks.json 的 ac_refs 交叉一致；缺失/多余判失败） | integration | test_testplan_ownership.py::test_ac_coverage_cross_consistent | IF-IMPL-003, IF-IMPL-007 |
| AC-FR0210-04（test-plan §8 的 IF- 归属与 tasks.json 的 if_ids 交叉一致；IF- 标识在 interfaces.md §5 已定义） | integration | test_testplan_ownership.py::test_if_attribution_cross_consistent | IF-IMPL-003, IF-VALIDATE-001 |
| AC-FR0210-05（test-plan §3 Ground Truth 方法不复制到 tasks.json；tasks.json 只携带 test_refs 引用） | integration | test_testplan_ownership.py::test_ground_truth_not_in_tasksjson | IF-IMPL-007 |
| AC-FR0210-06（test-plan 冻结后 Shield 在 M-TEST 写入测试；M-IMPL 期间 tasks.json 的 test_refs 不变） | integration | test_testplan_ownership.py::test_frozen_test_refs_immutable_in_m_impl | IF-IMPL-002, IF-IMPL-007 |

### 8y. FR-0220 Issues 消费语义

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0220-01（每个 Devon task 在 tasks.json 中携带 GitHub issue number（正整数）；trac validate --file tasks.json 校验 issue number 有效性） | integration | test_issue_consumption.py::test_issue_number_in_tasksjson + test_validate_issue_numbers | IF-IMPL-003, IF-VALIDATE-001 |
| AC-FR0220-02（Devon dispatch assignment payload 携带 issue_number + ac_refs（FR/NFR/ACC provenance）；物化字段完整） | integration | test_issue_consumption.py::test_dispatch_payload_carries_provenance | IF-IMPL-002, IF-DEVON-001 |
| AC-FR0220-03（Devon G commit trailers 含 Tracks-Issue: {issue_number} + Tracks-AC: {ac_refs}；git log --format='%B' -1 <G> 含五个 trailer） | integration | test_issue_consumption.py::test_g_commit_trailers_contain_provenance | IF-IMPL-002, IF-IMPL-004 |
| AC-FR0220-04（issue number 缺失/非正整数 -> trac validate 判失败并指出位置；Devon 提交缺失 trailer -> TASK_REVIEW 判失败） | integration | test_issue_consumption.py::test_missing_issue_fails + test_missing_trailer_fails_review | IF-IMPL-003, IF-IMPL-002 |

---

## 9. SM-01 转移覆盖清单（NFR-0020，normative 依据 SPEC-005「状态与生命周期」）

每条转移 ≥1 测试走到一次；清单内测试须存在且通过。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。

| 转移 | 内容摘要 | 层 | 测试 |
|:---|:---|:---|:---|
| SM-01.1 | M-TEST EXIT -> stage.entered(M-IMPL) -> BASELINE | unit + e2e | test_machine_m_impl.py::test_enter_m_impl, test_m_impl_journey.py |
| SM-01.2 | BASELINE -> PLANNING：baseline current | unit + integration | test_machine_m_impl.py::test_baseline_to_planning, test_baseline_recalc.py |
| SM-01.3 | BASELINE -> NEEDS_ATTENTION：缺失/stale/冲突 | unit + integration | test_machine_m_impl.py::test_baseline_to_needs_attention, test_baseline_recalc.py |
| SM-01.4 | NEEDS_ATTENTION -> BASELINE：已 reconcile | unit + integration | test_machine_m_impl.py::test_needs_attention_to_baseline, test_baseline_recalc.py |
| SM-01.5 | NEEDS_ATTENTION -> stage.rolled_back：rolled_back | unit + integration | test_machine_m_impl.py::test_needs_attention_to_rolled_back, test_baseline_recalc.py |
| SM-01.6 | PLANNING -> ISLAND_GATE_1：validate pass | unit + integration | test_machine_m_impl.py::test_planning_to_island_gate_1, test_taskgraph_validate.py |
| SM-01.7 | PLANNING -> PLANNING：validate fail，重派 Archer（<=3） | unit + integration | test_machine_m_impl.py::test_planning_validate_fail_redispatch, test_taskgraph_validate.py |
| SM-01.8 | ISLAND_GATE_1 -> PRISM_PLAN：闭合 | unit + integration | test_machine_m_impl.py::test_island_gate_1_to_prism_plan, test_island_gate_1.py |
| SM-01.9 | ISLAND_GATE_1 -> PLANNING：verdict.failed(island) | unit + integration | test_machine_m_impl.py::test_island_gate_1_to_planning, test_island_gate_1.py |
| SM-01.10 | PRISM_PLAN -> TASK_DISPATCH：prism.verdict(pass) | unit + integration | test_machine_m_impl.py::test_prism_plan_to_task_dispatch, test_prism_plan.py |
| SM-01.11 | PRISM_PLAN -> PLANNING：revise -> Archer | unit + integration | test_machine_m_impl.py::test_prism_plan_revise_to_planning, test_prism_plan.py |
| SM-01.12 | PRISM_PLAN -> stage.rolled_back：设计缺口 -> M-DESIGN | unit + integration | test_machine_m_impl.py::test_prism_plan_design_gap, test_prism_plan.py |
| SM-01.13 | PRISM_PLAN -> stage.rolled_back：需求缺口 -> M-SPEC/M-ACC（Human 确认） | unit + integration | test_machine_m_impl.py::test_prism_plan_requirement_gap, test_prism_plan.py |
| SM-01.14 | TASK_DISPATCH -> RED：task.started（DAG ready task + writelock + manifest） | unit + integration | test_machine_m_impl.py::test_task_dispatch_to_red, test_task_dispatch.py |
| SM-01.15 | RED -> RED_GATE：Devon outcome（test-only diff） | unit + integration | test_machine_m_impl.py::test_red_to_red_gate, test_red_phase.py |
| SM-01.16 | RED_GATE -> RED_CHECKPOINT：合法 Red | unit + integration | test_machine_m_impl.py::test_red_gate_to_checkpoint, test_red_gate_m_impl.py |
| SM-01.17 | RED_GATE -> RED：非法 Red，重派 Devon | unit + integration | test_machine_m_impl.py::test_red_gate_to_red, test_red_gate_m_impl.py |
| SM-01.18 | RED_CHECKPOINT -> PRISM_RED：red.checkpointed | unit + integration | test_machine_m_impl.py::test_red_checkpoint_to_prism_red, test_red_phase.py |
| SM-01.19 | PRISM_RED -> GREEN：prism.verdict(pass) 绑定 R | unit + integration | test_machine_m_impl.py::test_prism_red_to_green, test_prism_red.py |
| SM-01.20 | PRISM_RED -> RED：revise -> 新 attempt | unit + integration | test_machine_m_impl.py::test_prism_red_revise_to_red, test_prism_red.py |
| SM-01.21 | GREEN -> GREEN_GATE：Devon outcome（从 R tree 恢复，最小实现） | unit + integration | test_machine_m_impl.py::test_green_to_green_gate, test_green_phase.py |
| SM-01.22 | GREEN_GATE -> GREEN_COMMIT：全过 | unit + integration | test_machine_m_impl.py::test_green_gate_to_commit, test_green_gate.py |
| SM-01.23 | GREEN_GATE -> GREEN：实现缺陷，重派 Devon | unit + integration | test_machine_m_impl.py::test_green_gate_to_green, test_green_gate.py |
| SM-01.24 | GREEN_GATE -> DIAGNOSE：int 失败归因不明 | unit + integration | test_machine_m_impl.py::test_green_gate_to_diagnose, test_green_gate.py |
| SM-01.25 | DIAGNOSE -> GREEN：实现缺陷 -> Devon | unit + integration | test_machine_m_impl.py::test_diagnose_to_green, test_diagnose_four_way.py |
| SM-01.26 | DIAGNOSE -> SHIELD_FIX：测试缺陷 -> Shield | unit + integration | test_machine_m_impl.py::test_diagnose_to_shield_fix, test_diagnose_four_way.py |
| SM-01.27 | DIAGNOSE -> stage.rolled_back：接口/架构不足 -> M-DESIGN | unit + integration | test_machine_m_impl.py::test_diagnose_stub_gap, test_diagnose_four_way.py |
| SM-01.28 | DIAGNOSE -> stage.rolled_back：AC/Spec 缺口 -> M-ACC/M-SPEC（Human 确认） | unit + integration | test_machine_m_impl.py::test_diagnose_ac_spec_gap, test_diagnose_four_way.py |
| SM-01.29 | SHIELD_FIX -> GREEN_GATE：重跑受影响测试 | unit + integration | test_machine_m_impl.py::test_shield_fix_to_green_gate, test_shield_fix.py |
| SM-01.30 | GREEN_COMMIT -> REFACTOR：green.committed（G commit, parent=B, trailers） | unit + integration | test_machine_m_impl.py::test_green_commit_to_refactor, test_green_commit.py |
| SM-01.31 | REFACTOR -> REFACTOR_GATE：Devon outcome（可 no_change） | unit + integration | test_machine_m_impl.py::test_refactor_to_gate, test_refactor.py |
| SM-01.32 | REFACTOR_GATE -> TASK_REVIEW：通过（committed \| no_change） | unit + integration | test_machine_m_impl.py::test_refactor_gate_to_review, test_refactor.py |
| SM-01.33 | REFACTOR_GATE -> REFACTOR：失败，重派 | unit + integration | test_machine_m_impl.py::test_refactor_gate_to_refactor, test_refactor.py |
| SM-01.34 | REFACTOR_GATE -> stage.rolled_back：动 public interface -> upstream | unit + integration | test_machine_m_impl.py::test_refactor_gate_rollback, test_refactor.py |
| SM-01.35 | TASK_REVIEW -> PRISM_FINAL：校验通过 | unit + integration | test_machine_m_impl.py::test_task_review_to_prism_final, test_task_review.py |
| SM-01.36 | TASK_REVIEW -> GREEN：budget/scope fail -> Devon | unit + integration | test_machine_m_impl.py::test_task_review_to_green, test_task_review.py |
| SM-01.37 | PRISM_FINAL -> TASK_DONE：prism.verdict(pass) | unit + integration | test_machine_m_impl.py::test_prism_final_to_task_done, test_prism_final.py |
| SM-01.38 | PRISM_FINAL -> GREEN：revise（实现）-> Devon | unit + integration | test_machine_m_impl.py::test_prism_final_revise_impl, test_prism_final.py |
| SM-01.39 | PRISM_FINAL -> RED：revise（Red 测试）-> 新 lineage | unit + integration | test_machine_m_impl.py::test_prism_final_revise_red, test_prism_final.py |
| SM-01.40 | TASK_DONE -> TASK_DISPATCH：task.completed，还有 ready task | unit + integration | test_machine_m_impl.py::test_task_done_to_dispatch, test_task_dispatch.py |
| SM-01.41 | TASK_DONE -> ISLAND_GATE_2：全部 task 完成 | unit + integration | test_machine_m_impl.py::test_task_done_to_island_gate_2, test_island_gate_2.py |
| SM-01.42 | ISLAND_GATE_2 -> stage.exited(M-IMPL) -> run.completed(boundary)：通过 | integration + e2e | test_island_gate_2.py::test_exit_to_boundary, test_m_impl_journey.py |
| SM-01.43 | ISLAND_GATE_2 -> DIAGNOSE：全量执行有失败 | unit + integration | test_machine_m_impl.py::test_island_gate_2_to_diagnose, test_island_gate_2.py |
| SM-01.44 | ISLAND_GATE_2 -> PLANNING：verdict.failed(island) | unit + integration | test_machine_m_impl.py::test_island_gate_2_to_planning, test_island_gate_2.py |
| 休眠回放 | M-IMPL 各子状态休眠后事件回放恢复 | integration | test_crash_recovery.py::test_replay_recovery |

---

## 10. 既有测试更新（planned changes，AC-FR0010-04）

v0.5 的 boundary 从 M-TEST->M-IMPL 移至 M-IMPL->M-VERIFY，影响既有 e2e 测试：

| 既有测试 | 变更 | 理由 |
|:---|:---|:---|
| `tests/e2e/test_full_journey.py::test_full_journey_to_boundary` | 更新：M-TEST EXIT 后断言 `stage.entered(M-IMPL)`（而非 `run.completed`）；最终 boundary 终态移至 M-IMPL 后；M-TEST 之前事件前缀逐字节稳定 | SM-01.1：M-TEST EXIT -> M-IMPL（不再 run.completed） |
| `tests/e2e/test_full_journey.py::test_prism_revise_drives_a_second_review_round` | 更新：M-DESIGN Prism revise 后仍进入 M-TEST -> M-IMPL（而非直接 run.completed） | boundary 移动 |
| `tests/e2e/test_full_journey.py::test_walk_to_design_complete` | 更新：design complete 后继续 M-TEST -> M-IMPL | boundary 移动 |
| `tests/e2e/test_full_journey.py::test_bounded_walk_matches_harness_step_boundaries` | 更新：增加 M-IMPL 步骤（BASELINE/PLANNING/.../ISLAND_GATE_2/EXIT） | M-IMPL 接入 bounded walk |
| `tests/unit/test_machine_design.py` | 更新：M-DESIGN EXIT 后断言 `stage.entered(M-TEST)`（不变）；M-TEST EXIT 后断言 `stage.entered(M-IMPL)` | _NEXT_STAGE 增补 M-TEST->M-IMPL |
| `tests/unit/test_machine_m_test.py` | 更新：M-TEST EXIT 后断言 `stage.entered(M-IMPL)`（而非 `run.completed`） | _NEXT_STAGE 增补 M-TEST->M-IMPL |

AC-FR0010-04 要求"M-TEST 之前事件前缀逐字节稳定"：上述更新的断言只触及 M-TEST EXIT 及之后的 Events，M-TEST 之前的事件序列不变。

---

## 11. e2e Happy Path 范围（test_m_impl_journey.py）

e2e 仅覆盖 happy path（主成功旅程），边界/错误情形归入 integration：

**M-IMPL happy path（fake 通道）**：

```
[M-TEST EXIT 已完成，测试资产冻结]
-> stage.entered(M-IMPL) -> BASELINE
-> baseline.frozen(current) -> PLANNING
-> dispatch Archer 拆 task graph -> taskgraph.committed(pass) -> ISLAND_GATE_1
-> 六元组复核通过 -> PRISM_PLAN
-> dispatch Prism 评 task graph 切片 -> prism.verdict(pass) -> TASK_DISPATCH
-> writelock.granted + task.started -> RED
-> dispatch Devon(phase=red) -> Devon outcome(test-only diff) -> RED_GATE
-> 合法红 -> RED_CHECKPOINT -> red.checkpointed -> PRISM_RED
-> dispatch Prism 评 B..R -> prism.verdict(pass) -> GREEN
-> dispatch Devon(phase=green) -> Devon outcome(产品代码) -> GREEN_GATE
-> targeted 单测 + int 子集 + lint 全过 -> GREEN_COMMIT -> green.committed
-> dispatch Devon(phase=refactor) -> Devon outcome(no_change) -> REFACTOR_GATE
-> 重跑全检查通过 -> TASK_REVIEW
-> scope/lineage/budget 通过 -> PRISM_FINAL
-> dispatch Prism 评完整 range -> prism.verdict(pass) -> TASK_DONE -> task.completed
-> (单 task 场景) 全部完成 -> ISLAND_GATE_2
-> trac check reach 闭合 + 全量 int+e2e 变绿 -> stage.exited(M-IMPL) -> run.completed(boundary)
```

**断言**：
- 事件流按 SM-01 序列依次出现各子状态。
- 无 `human.review`/`human.approval` 在 M-IMPL 期间作为退出前置插队（BS-14）。
- `trac status` 报告 `stage=M-IMPL` + 当前子状态。
- 最终 `run.completed(terminal_state="boundary")`。
- `git log` 含 G commit（parent=B + trailers Tracks-Task/Tracks-Attempt/Tracks-R/Tracks-Issue/Tracks-AC）。
- `refs/trac/rgr/{run}/{task}/{attempt}/red` 存在且指向 test-only diff commit。
- `tasks.json` 存在且可被 `trac validate --file tasks.json` 校验通过（5 项 check 全过）。

**非 happy path（归入 integration）**：
- BASELINE stale/冲突 -> NEEDS_ATTENTION -> test_baseline_recalc.py
- PLANNING validate fail 重派 -> test_taskgraph_validate.py
- ISLAND_GATE_1 不闭合 -> test_island_gate_1.py
- PRISM_PLAN revise / 设计缺口 / 需求缺口 -> test_prism_plan.py
- RED_GATE 非法红 -> test_red_gate_m_impl.py
- PRISM_RED revise -> test_prism_red.py
- GREEN_GATE 失败 / int 归因不明 -> test_green_gate.py
- REFACTOR_GATE 失败 / 动 public interface -> test_refactor.py
- TASK_REVIEW budget/scope fail -> test_task_review.py
- PRISM_FINAL revise -> test_prism_final.py
- DIAGNOSE 四路路由 -> test_diagnose_four_way.py
- SHIELD_FIX -> test_shield_fix.py
- ISLAND_GATE_2 失败 -> test_island_gate_2.py
- 崩溃恢复 -> test_crash_recovery.py
- trac retry -> test_trac_retry.py
