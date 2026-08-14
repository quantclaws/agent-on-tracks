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

- CLI endpoints (`trac run`, `trac status`, `trac validate`, `trac retry`, `trac check reach`, `trac check deliverables`, `trac check release-evidence [--json]`, `trac report`) stdout/stderr/exit code.
- Persisted data: `.tracks/runtime/tracks.db` `events` table (assertion primary source), projection tables.
- Document files: `.tracks/projects/v0.5/{story,spec,acceptance}.md`, `.tracks/projects/v0.5/tasks.json`（task graph 机器真相）, `.tracks/projects/v0.5/tasks.md`（人类可读投影） (inline-discussion blockquotes, task graph content, phase progress).
- Git state: refs (`refs/trac/rgr/{run}/{task}/{attempt}/red`), commits (G commit parent=B + trailers), worktree state, working tree diff.
- File schema: 当前 DRAFT bootstrap 的 `.tracks/projects/project.toml` 与 FR-0190 迁移后的 canonical `.tracks/project/project.toml`（TOML contract）、`tracks/agents/Devon.md`（frontmatter + body, no permission block）、`tracks/skills/tracks-prism-impl/SKILL.md`。
- `trac check reach --json` / `trac check deliverables` structured output.
- `command.issued` event payload (dispatch assignment materialization fields).
- Canonical live evidence: `.tracks/runtime/release-evidence/v1/{candidate_sha}/{run_id}/evidence.json` 与同目录 content-addressed blobs；只按 interfaces.md §1j/§3h 断言。

### 1.2. Non-observable Objects (tests do not directly depend on)

- `kernel/machine.py` internal State field values (observed via `trac status` / events).
- `executor/executor.py` internal subprocess management (observed via events).
- `executor/taskgraph.py` / `executor/rgr.py` / `executor/worktree.py` / `executor/quality_gate.py` internal data structures (observed via CLI output / events / git state).
- `effects/opencode.py` internal prompt construction (observed via outcome events + audit evidence).
- `effects/audit.py` internal manifest representation (observed via `outcome.received.audit_evidence`).
- `executor/live_evidence.py` / `checks/release_evidence.py` 内部解析和选择步骤（经 canonical bundle 与 CLI text/JSON/exit 观察）。
- `executor/doc_comment.py` 的 delta 分类、quarantine descriptor 与恢复判断内部表示（经 §1m 事件、CLI 状态、discussion、Git/worktree/blob identity 观察）。

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
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表（v0.5 新增 IF-IMPL-001~007、IF-DEVON-001、IF-LIVE-001、IF-RELEASE-001、IF-DOCGAP-001、IF-QUARANTINE-001，并继承 v0.4 注册表）。

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion.
   - No `try: ... except: pass` wrapping the code under test.
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`, jest `it.skip`, Go `t.Skip`) without a GitHub issue link, except the spec-required `tests/e2e_live` credential probe: it must emit `LIVE_SKIPPED: missing <NAME>`, create no evidence, and remains forbidden in the milestone hard gate.

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

- **Unit tests**: Written by the **implementer** (Devon, committed alongside impl in R-G-R) for every implemented FR/NFR; coverage ≥95% is the universal gate. Unit tests are not planned in §8.
- **Integration tests**: Written by the **test lead** (Shield) - covers module interface contracts defined in interfaces.md.
- **E2E tests**: Written by the **test lead** (Shield) - covers user-facing happy paths only.
- **Ground Truth (§3)**: Provided by an **independent developer** not involved in the implementation under test, or a **third-party library**.
- **Review ownership**: All test changes are reviewed by the test lead; Ground Truth script changes require focused review of semantic consistency with the corresponding AC.

---

## 2. Test Environment

### 2.1. Directory Layout

注：下表为 v0.5 测试树实际落盘文件与本 revision 待 Shield 创建文件。原计划的 per-AC 整合文件多数未单独创建；既有跨模块断言整合在少数文件，unit 文件由 Devon 在 RGR 中编写（§1.5）。

```
tests/
├── unit/
│   ├── test_deliverables.py       # [既有] deliverables gate (FR-0170)；test_real_deliverables_consistent 真值驱动
│   ├── test_machine_m_test.py     # [既有] M-TEST kernel (v0.4 marker 可作 M-IMPL unit 接续点)
│   ├── test_machine_m_impl.py     # M-IMPL reducer/decide branches (Devon RGR)
│   ├── test_taskgraph.py          # validate_dag/scope/ac_coverage/issue_numbers (Devon RGR)
│   ├── test_rgr.py                # create_red_ref/create_green_commit/classify_red/verify_lineage (Devon RGR)
│   ├── test_quality_gate.py       # run_gates/layering (Devon RGR)
│   └── ...                        # 既有 unit tests 不变
├── integration/
│   ├── v05_contract_helpers.py            # [既有 helper] run_m_impl_journey/events_of/command_dispatches
│   ├── test_m_impl_cycle.py               # M-IMPL event lifecycle + append-only + rebuild (FR-0010/0020, NFR-0010/0020)
│   ├── test_baseline_recalc.py            # BASELINE recalc + frozen test paths (FR-0020)
│   ├── test_taskgraph_validate.py         # tasks.json DAG/scope/AC coverage/IF-/issue number (FR-0030/0180)
│   ├── test_planning_dispatch.py          # PLANNING/ISLAND_GATE_1/PRISM_PLAN/TASK_DISPATCH contracts (FR-0040/0050/0060)
│   ├── test_execution_gates.py            # RGR phase events + diagnose/shield_fix routes (FR-0070~0160)
│   ├── test_rgr_contract.py               # R/G ref + lineage + red classification (FR-0070/0080/0120/0200/0220)
│   ├── test_worktree_contract.py          # three worktree composition + cleanup (FR-0070/0100/0110)
│   ├── test_quality_gate_contract.py      # GREEN_GATE/REFACTOR_GATE layering + feedback (FR-0110/0130)
│   ├── test_island_gate_2.py              # ISLAND_GATE_2 reach + full int+e2e (FR-0160)
│   ├── test_devon_dispatch.py             # Devon agent dispatch + manifest audit (FR-0170)
│   ├── test_tasksjson_validate.py         # trac validate --file tasks.json (FR-0180)
│   ├── test_dispatch_materialization.py   # dispatch materialization 10 fields (FR-0190)
│   ├── test_crash_recovery.py             # crash recovery (FR-0200)
│   ├── test_trac_retry.py                 # trac retry command (NFR-0030)
│   ├── test_tasklog_report.py             # tasks.md/report rebuild from events (FR-0030/0180)
│   ├── test_testplan_ownership.py         # Shield test-plan ownership boundary (FR-0210)
│   ├── test_issue_consumption.py          # Issues consumption semantics (FR-0220)
│   ├── test_release_evidence.py           # [Shield 待创建] deterministic provenance/stale/schema/CLI negatives (FR-0230~0233/NFR-0080)
│   ├── test_doc_comment_first.py          # [Shield 待创建] comment-first、Prism 两路、非法正文原子拒绝
│   ├── test_doc_comment_quarantine.py     # [Shield 待创建] empty/held、隔离、重启、restore/discard
│   └── ...                                # 既有 integration tests 不变
├── e2e/
│   ├── test_m_impl_journey.py     # M-IMPL happy path: M-TEST exit -> M-IMPL -> boundary
│   ├── test_full_journey_v05.py   # [既有] 全流程: M-STORY -> ... -> M-IMPL -> boundary
│   ├── test_doc_comment_journey.py # [Shield 待创建] E-04 操作者 happy path
│   └── ...                        # 既有 e2e tests 不变
├── e2e_live/                      # live opencode channel (缺凭据 skip)
│   ├── test_m_impl_release_evidence.py # [Shield 待创建] current-wheel real Devon RGR happy path
│   ├── conftest.py                # [既有，待修改] routine LIVE_SKIPPED 探测
│   └── harness.py                 # [既有，待修改] isolated install + byte-preserving evidence transfer
├── assets/
│   ├── trace_fixtures/            # [既有]
│   ├── reach_fixtures/            # [既有]
│   └── taskgraph_fixtures/        # tasks.json fixtures for DAG/scope/AC coverage/IF-/issue number
└── conftest.py                    # 既有 + fake 通道强制 + M-IMPL fixtures
```

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_0070_03_r_ref_immutable`
- Marker: 测试函数定义紧邻上方一行标记注释 `# AC-FRXXXX-YY@v0.5 TRACKS-TRACE 说明`（R-1 约定，长格式 + 特征词强制，FR-0080/FR-0130）

### 2.3. Execution

- **Offline**: unit/integration/e2e fake 通道不依赖网络（data pinned）；只有显式 `tests/e2e_live` 通道访问真实 Opencode/provider。
- **Execution order**: unit (fast) -> integration -> e2e (slow).
- **CI**: Every push runs `tests/unit tests/integration tests/e2e`; `tests/e2e_live` 只走 §6/§7 独立通道。
- **Isolation**: Integration and e2e use registered pytest markers (`@pytest.mark.integration` / `@pytest.mark.e2e`) and distinct paths; routine commands list the three offline paths explicitly so live tests are never selected by default.
- **M-IMPL GREEN_GATE int 子集执行**: executor 使用 project.toml 的 run 命令在 gate worktree 执行 int 子集（按 test-plan §8 IF- 归属筛选命中本 task IF- 集合的 integration 测试）；conftest 的 `_UNDER_COVERAGE` 机制继承（subprocess coverage merge）。
- **Git 操作测试**: R ref / G commit / worktree 操作使用 temp git repo（`tmp_path` fixture），不依赖宿主项目仓库状态。
- **Live selection**: `.venv/bin/python -m pytest -q -rs tests/e2e_live/test_m_impl_release_evidence.py` 是唯一 FR-0230 happy path；不得设置 `TRAC_FAKE_SIMULATE`，不得传 `--assignment-overlay`。routine 无凭据时 skip reason 必须精确含 `LIVE_SKIPPED: missing <NAME>`；tag/release job 在运行 pytest 前自行探测所有凭据，缺失直接 exit 1，故 milestone 路径不存在 skip。

### 2.3.1. Test Execution Contract（DRAFT bootstrap：`.tracks/projects/project.toml`）

当前 DRAFT Runtime 从 `.tracks/projects/project.toml` 读取 Archer 交付的宿主测试执行合同；M-TEST 用它独立 collect/run，M-IMPL GREEN_GATE / ISLAND_GATE_2 复用同一合同执行 int 子集与全量 int+e2e。FR-0190 产品终态迁移到 `.tracks/project/project.toml`，由待实现 foundation task 与 §8s 对应测试闭合；迁移后旧路径必须拒绝。

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
- **Live credentials**: `TRAC_LIVE_PROVIDER`、`TRAC_LIVE_MODEL`、`TRAC_LIVE_BASE_URL`、`TRAC_LIVE_API_KEY` 仅从 CI secret/env 注入，不写 fixture、report、event 或 evidence；evidence 仅使用既有脱敏 agent I/O blob。
- **Version snapshot**: Fixtures are version-controlled in git; no external data versioning needed.

### 2.5. Installation & Isolation

继承 v0.2 §13（可安装发行物与 live E2E 安装边界）与 v0.4 §2.5。v0.5 新增 package data（`tracks/agents/Devon.md` 已在 v0.4 创建但未加入 deliverables；`tracks/skills/tracks-prism-impl/SKILL.md` 已在 v0.4 创建）。`[tool.setuptools.package-data]` 已含 `agents/*.md` 与 `skills/*/*.md`，无需修改。安装合同不变并收紧 live evidence：从 current candidate HEAD 执行 `.venv/bin/python -m pip wheel --no-deps --wheel-dir dist .`，记录 wheel SHA-256，`pip install <wheel>` 到隔离 demo host 的 fresh venv，从源码树外 cwd 执行 `trac init`/`trac run`；安装探针断言 import path 位于该 venv 且 `source_tree_import=false`。fake 通道继承 conftest 的 `trac` fixture。demo host 产出的 evidence bundle 只能逐字节传回 current candidate checkout canonical path，传输前后每个文件 SHA-256 相同。

---

## 3. Ground Truth Method

v0.5 是行为正确性（状态机转移、事件序列、git 操作、dispatch 物化），无算法正确性 / 规则正确性 / 计算结果正确性需要独立验证。

task graph DAG 无环校验（Kahn's algorithm 拓扑排序）属简单规则正确性：测试 fixture 本身声明已知有环/无环结构，fixture 数据即 ground truth（§3.1 表第 3 行「简单规则 -> 测试数据本身」）。scope 不重叠与 AC 覆盖闭合同理--fixture 声明已知重叠/无重叠、覆盖/缺口结构，测试数据即真相源。RGR git 操作（R ref 创建、G commit 创建、lineage 证明）属行为正确性，通过 git 命令（`git rev-parse` / `git log --format='%B'`）直接观察，不需要独立参考实现。

§3 判定不适用，不为 FR-0230～FR-0233/NFR-0080 创建 ground-truth 脚本。真实性不是重算业务算法：预期值由三个独立既有事实源交叉给出（SQLite append-only event slice、真实 Git refs/commit trailers、content-addressed agent I/O bytes），测试 fixture 对负例逐一破坏其中一个来源。既有 v0.4 的 `tests/ground_truth/trace_reference.py` 与 `tests/ground_truth/reach_reference.py` 保持不变。

FR-0234～FR-0237/NFR-0090 同样不适用 ground-truth 脚本：它们验证顺序、原子回滚、持久化与身份相等，不是算法输出重算。预期由测试自己创建的 pre-dispatch bytes、Git index tree、path SHA-256、dispatch/attempt IDs 与 append-only event seq 组成；测试在运行前独立快照这些事实，运行后逐字节/逐 identity 比较，不调用 IF-DOCGAP/IF-QUARANTINE 生成 expected。

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
| L3 | Real env journey | Real | Real | current candidate wheel 上真实 OpencodeBackend 的一个完整 Devon RED/GREEN/REFACTOR + Prism review + gates + task completion + ISLAND_GATE_2 + boundary + evidence | ❌ routine；✅ weekly/tag/manual |

- **L1 Deterministic sim**: FakeBackend controls Devon/Archer/Prism/Shield outcome via `simulate`; RGR git ops tested with temp git repo (`tmp_path`); pure functions tested directly with fixtures.
- **L2 Contract sim**: fake opencode stand-in (implements `opencode run --format json` protocol); OpencodeBackend interacts with stand-in, covering Devon dispatch materialization/JSON parsing/manifest audit/failure matrix/criteria pack identity/feedback desensitization.
- **L1/L2 doc-comment-first**：使用真实临时 Git repo、真实 EventStore 与 canonical `trac discuss` 文本；FakeBackend/stand-in 只替代 Agent 进程并产生授权 diff，不替代 Runtime 的 delta 分类、Auditor rollback、blob/event 持久化、状态投影或恢复判断。中断在 blob/event、adjudication、restore/discard 前后注入。
- **L3 Real env journey**: `tests/e2e_live/test_m_impl_release_evidence.py` 在隔离 demo host 从 current wheel 运行；不使用 FakeBackend、`TRAC_FAKE_SIMULATE`、assignment overlay 或人工 event。routine 缺凭据显式 skip；weekly/manual 有凭据运行；tag/release 缺凭据或 skip 都失败。L3 只覆盖真实成功组合，fake/stale/伪造等 negative AC 分配给 L1/L2，不重复。

### 6.4. Responsibility Contract of Test Infrastructure

| Component | Responsibility (external) | Boundary (what it does not implement) |
| --------- | ------------------------- | ------------------------------------- |
| FakeBackend | Control Devon/Archer/Prism/Shield outcome via `simulate` | Does not implement real M-IMPL logic |
| fake opencode stand-in | Implement `opencode run --format json` protocol; write target files per manifest | Does not implement tracks M-IMPL state machine or task graph validation |
| temp git repo (`tmp_path`) | Real git operations on throwaway repo | Does not implement RGR logic (rgr.py does) |
| pytest subprocess (GREEN_GATE / ISLAND_GATE_2) | Execute real pytest on host project tests/ | Does not implement quality gate layering (quality_gate.py does) |
| taskgraph fixture data | Pinned tasks.json with known DAG/scope/AC coverage/IF- validity/issue number properties | Does not implement task graph validation algorithm |
| canonical evidence fixture builder | 生成 schema 合法但可逐字段破坏的 bytes、临时 Git refs/commits/events | 不调用 `bind_live_evidence` 生成 expected，不伪装真实 live success |
| live demo host + current wheel | 用户同构安装并运行真实 OpencodeBackend | 不注入 outcome、不补写 events、不修改 evidence bytes |
| doc-comment outcome fixture | 在真实临时 repo 写 canonical thread、授权 Agent diff、Human pre-dirty/staged bytes，并记录独立 identity | 不调用被测分类/隔离函数生成 expected；不代替 Runtime 普通验证 |

### 6.5. Assertion Basis - Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md §4**:

- Events table (`baseline.frozen`, `taskgraph.committed`, `task.started`, `writelock.granted`, `red.checkpointed`, `green.committed`, `refactor.committed`, `refactor.no_change`, `task.completed`, `prism.verdict`, `verdict.failed`, `test.committed`, `stage.exited`, `stage.rolled_back`, `outcome.received`, `command.issued`, `human.retry`).
- CLI output (`trac status`, `trac validate --file tasks.json`, `trac retry`, `trac check reach`, `trac check deliverables`, `trac report`).
- File schema（`tasks.json`、`tasks.md`、DRAFT bootstrap `.tracks/projects/project.toml`、FR-0190 终态 `.tracks/project/project.toml`、`tracks/agents/Devon.md`、`tracks/skills/tracks-prism-impl/SKILL.md`）。
- Git state (refs `refs/trac/rgr/.../red`, commit G parent=B + trailers, commit R test-only diff, worktree state).
- Canonical live evidence bundle/blob（interfaces.md §1j/§3h）与 `trac check release-evidence [--json]` 的确定性 text/JSON/exit（§2d/§4d）。
- Doc-comment-first 出口（interfaces.md §1m/§2e/§4e）：append-only events、`trac status/discuss/replay/report`、command.issued 新 dispatch identity、Git/worktree/index/pre-dirty bytes 与 Runtime content-addressed blob identity。

If a state needed by an AC has **no** corresponding observable outlet in interfaces.md, this is an observability gap; revise interfaces/acceptance to add the outlet, rather than snooping internal state in the test.

---

## 7. CI Gate

- **Required checks** (architecture.md §4.3 CI 合同):
  1. `lint`：ruff check（tracks/tests）+ flake8 tracks（含 CCR001，tests/ 豁免）+ pylint R0801/C0302（tracks only，tests exempt）+ pylint R0915/R0914（tracks only）；与 pre-commit 同序执行
  2. `coverage`：`coverage run -m pytest tests/unit tests/integration tests/e2e -q -m 'not performance' && coverage combine && coverage report --fail-under=95`
  3. `test`：`pytest tests/unit tests/integration tests/e2e -q -m 'not performance'`（unit + integration + e2e fake 通道；明确排除 live）
  4. `deliverables`：`trac check deliverables`（含 Devon.md，存在性 + version + IQ）
  5. `trace`：`trac check trace --json`（既有子命令；需求追踪闭合）
  6. `reach`：`trac check reach --json`（既有子命令；模块可达性）
  7. `release-evidence`：tag/release candidate milestone required job；`.venv/bin/python -m pytest -q -rs tests/e2e_live/test_m_impl_release_evidence.py` 必须真实 pass，随后当前候选执行 `trac check release-evidence --json` 必须 exit 0/status=satisfied。该 job 待 Devon 补全现有 CI skeleton；不实现 publish。
- **Validation items**:
  - AC reference closure (each required AC ≥1 test with long-format marker, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3)
  - Coverage ≥95%
  - Deliverable existence + version + IQ (Devon.md + tracks-prism-impl skill)
  - trace 闭合（`trac check trace` 无硬错误）
  - reach 闭合（`trac check reach` 无孤岛）
- **Failure semantics**: routine merge required checks 任一失败阻塞 merge；routine live opt-in job 缺凭据输出 `LIVE_SKIPPED: missing <NAME>`，skip 不产生证据。tag/release candidate 的 `release-evidence` job 是 milestone hard gate：缺凭据、pytest skip/fail/cancel、check 非零或 stale 都失败，Human approval 不可绕过。weekly job 同真实命令防通道腐烂，但不发布。
- **待实现 CI skeleton**：`.github/workflows/ci.yml` 当前仍绕过既有 `trace`/`reach` 子命令的失败且尚无 live/release jobs；Devon foundation task 必须按 architecture.md §4.3 补全后，Runtime 才绑定上述稳定 required checks。本文不把 skeleton 当前行为表述为已生效。

---

## 8. AC Coverage

每个 AC ≥1 测试、每个测试 ≥1 AC（CI 闭合）。跨模块合同（interfaces.md `modules` 列 ≥2）至少一个 integration 测试。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。



「IF- 归属」列的取值只能来自 interfaces.md §5 IF- 标识注册表（含 v0.5 新增 IF-DOCGAP-001 与 IF-QUARANTINE-001）。design-trace validator（`check_design_trace`，FR-0140 扩展）校验：每条 integration/e2e AC 的 IF- 归属非空、且每个 IF- 标识在 interfaces.md §5 已定义。粒度对齐 architecture.md §1 增长轴（Devon 实现 task 边界）。

### 8a. FR-0010 M-IMPL 阶段注册与子状态机驱动

注：M-IMPL control-flow 单测属 Devon unit 义务；Shield 在 integration/e2e 经公开事件出口覆盖同一 AC 的可观察行为。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0010-01（M-TESTEXIT->stage.entered(M-IMPL),substate=BASELINE；无human插队）|integration| test_m_impl_cycle.py::test_m_impl_public_event_lifecycle |IF-IMPL-001|
|AC-FR0010-02（tracstatus报告stage=M-IMPL+substate；_NEXT_STAGE接续+boundary）|e2e| test_m_impl_journey.py::test_boundary_after_m_impl |IF-IMPL-001|
|AC-FR0010-03（SM-01转移严格遵循清单；非法转移不产出）|integration| test_m_impl_cycle.py::test_m_impl_public_event_lifecycle |IF-IMPL-001|
| AC-FR0010-04（既有阶段行为不变；M-TEST 之前事件前缀稳定） | e2e | test_full_journey_v05.py::test_full_journey_to_boundary_includes_m_impl | IF-IMPL-001, IF-MTEST-001 |
|AC-FR0010-05（显式控制流驱动；kernel纯函数边界）|integration| test_m_impl_cycle.py::test_m_impl_public_event_lifecycle |IF-IMPL-001|

### 8b. FR-0020 BASELINE 重算与测试资产冻结

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0020-01（BASELINE重算->baseline.frozen；current->PLANNING）|integration| test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics |IF-IMPL-001,IF-IMPL-002|
|AC-FR0020-02（baseline缺失/stale/冲突->NEEDS_ATTENTION；reconcile->BASELINE；rolled_back）|integration| test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics |IF-IMPL-001,IF-IMPL-002|
| AC-FR0020-03（冻结测试路径集；缺层归属 -> 硬错误；trac validate --file test-plan.md 判失败） | integration | test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics | IF-IMPL-002, IF-VALIDATE-001 |
| AC-FR0020-04（冻结路径集供 test-authority/gate worktree；语言无关不硬编码） | integration | test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics | IF-IMPL-002, IF-IMPL-006 |

### 8c. FR-0030 PLANNING：Archer 拆 task graph

注：task graph 的跨模块行为由 `test_taskgraph_validate.py` 从公开 CLI/event 出口覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0030-01（dispatchArcher拆taskgraph；taskgraph.committed；每task纵向切片+scope+IF-+budget）|integration| test_taskgraph_validate.py::test_taskgraph_happy_path_reaches_public_file_and_event |IF-IMPL-001,IF-IMPL-002|
|AC-FR0030-02（validateDAG无环/scope不重叠/AC覆盖闭合；fail重派<=3->escalation）|integration| test_taskgraph_validate.py::test_taskgraph_key_error_paths_fail_at_validate_cli |IF-IMPL-003,IF-IMPL-001|
| AC-FR0030-03（tasks.md Runtime 确定性生成；进展投影入 events/db；trac report 重建） | integration | test_taskgraph_validate.py::test_tasksmd_projected + test_report_progress | IF-IMPL-002, IF-IMPL-007 |

### 8d. FR-0040 ISLAND_GATE_1：程序复核

注：ISLAND_GATE_1 由 `test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted` 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0040-01（六元组复核通过->PRISM_PLAN）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |IF-IMPL-001,IF-IMPL-002|
|AC-FR0040-02（不闭合->verdict.failed(island)->PLANNING重派Archer）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |IF-IMPL-001,IF-IMPL-002|

### 8e. FR-0050 PRISM_PLAN：判据包绑定与切片评审

注：PRISM_PLAN 由 `test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted` 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0050-01（dispatchPrism评taskgraph切片；反自述三件套：assignment含判据包、verdict携带identity、Runtime回读不匹配判失败）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |IF-IMPL-001,IF-IMPL-002|
|AC-FR0050-02（pass->TASK_DISPATCH；revise->PLANNING；设计缺口->M-DESIGN；需求缺口->M-ACC/M-SPEC）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |IF-IMPL-001,IF-IMPL-002|
| AC-FR0050-03（revise 必须经 trac discuss 锚定线程；无锚定 -> revise_without_findings） | integration | test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted | IF-IMPL-001 |
| AC-FR0050-04（反自述三件套适用于 plan/red/final/diagnostic 全部四种 Prism 派发） | integration | test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted | IF-IMPL-002 |

### 8f. FR-0060 TASK_DISPATCH：DAG 调度与单写者 manifest

注：TASK_DISPATCH 由 `test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted` 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0060-01（DAGreadytask选+单写者lease+manifest创建->writelock.granted+task.started->RED）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |IF-IMPL-001,IF-IMPL-002|
|AC-FR0060-02（task变绿子集=单测+IF-命中int子集；全部完成->ISLAND_GATE_2；还有->TASK_DISPATCH）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |IF-IMPL-001,IF-IMPL-002|
| AC-FR0060-03（[P] 并行标记只记录不并发；串行顺序执行） | integration | test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted | IF-IMPL-002 |

### 8g. FR-0070 RED：Devon 隔离与私有 R ref

注：RED 跨模块合同由 worktree、execution-gates 与 rgr contract 三个 integration 文件覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0070-01（dispatchDevonphase=red在Devoncandidateworktree；worktree在ShieldWRITE前创建）|integration| test_worktree_contract.py::test_three_worktree_composition_and_cleanup |IF-IMPL-001,IF-IMPL-002,IF-IMPL-006|
| AC-FR0070-02（Devon 只添加 unit test；outcome 含产品代码或 Shield 测试 -> 判失败；文件访问被 manifest 限定） | integration | test_execution_gates.py::test_rgr_phase_events_and_audit_evidence | IF-IMPL-002, IF-DEVON-001 |
|AC-FR0070-03（RED_CHECKPOINT创建私有commitR+gitref；red.checkpointed；gitshow存在）|integration| test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable |IF-IMPL-002,IF-IMPL-004|
|AC-FR0070-04（R不可变：重试改写Rrefcompare-and-set失败；rev-parse前后同一SHA）|integration| test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable |IF-IMPL-004|
| AC-FR0070-05（三 worktree 方案：gate worktree 组合 C_design + frozen bundle + Devon candidate；frozen bundle 永不合入 Devon candidate） | integration | test_worktree_contract.py::test_three_worktree_composition_and_cleanup | IF-IMPL-002, IF-IMPL-006 |

### 8h. FR-0080 RED_GATE：合法 Red 分类

注：RED_GATE 由 RGR contract 与 execution-gates integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0080-01（合法红=行为断言失败/symbol缺失->RED_CHECKPOINT；stub_token_failure不在M-IMPL合法红之列）|integration| test_rgr_contract.py::test_m_impl_red_classification_excludes_stub_tokens |IF-IMPL-002,IF-IMPL-004|
|AC-FR0080-02（非法红=collection/语法/fixture/import错误、意外通过->重派Devon；verdict.failed(red_invalid)）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |IF-IMPL-001,IF-IMPL-002|

### 8i. FR-0090 PRISM_RED：Red checkpoint 范围评审

注：PRISM_RED 由 `test_execution_gates.py::test_rgr_phase_events_and_audit_evidence` 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0090-01（dispatchPrism评B..R范围；反自述三件套）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |IF-IMPL-001,IF-IMPL-002|
|AC-FR0090-02（pass绑定R->GREEN；revise->RED新attempt；revise经tracdiscuss锚定）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |IF-IMPL-001,IF-IMPL-002|

### 8j. FR-0100 GREEN：最小实现与受控 diff 回灌

注：GREEN 由 execution-gates 与 worktree integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0100-01（从Rtree恢复worktree；dispatchDevonphase=green；R测试不可改；outcome含R测试改动->判失败）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |IF-IMPL-001,IF-IMPL-002|
| AC-FR0100-02（受控 diff 按 manifest 回灌主仓；白名单外不回灌；视图终态清理） | integration | test_worktree_contract.py::test_three_worktree_composition_and_cleanup | IF-IMPL-002, IF-IMPL-006 |

### 8k. FR-0110 GREEN_GATE：粒度门禁与反馈脱敏

注：GREEN_GATE 由 quality-gate 与 worktree integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0110-01（targeted单测+历史单测+int子集+lint/format/type/static+合同；第一轮不跑e2e；全过->GREEN_COMMIT；缺陷->GREEN）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |IF-IMPL-001,IF-IMPL-002,IF-IMPL-005|
| AC-FR0110-02（int/e2e 由 Runtime 在独立 gate worktree 运行并归因） | integration | test_worktree_contract.py::test_three_worktree_composition_and_cleanup | IF-IMPL-002, IF-IMPL-006 |
| AC-FR0110-03（反馈脱敏：单测失败回完整输出；int/e2e 失败只回分类诊断不回断言原文） | integration | test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events | IF-IMPL-002, IF-IMPL-005 |
|AC-FR0110-04（int失败归因不明->DIAGNOSE）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |IF-IMPL-001,IF-IMPL-002|

### 8l. FR-0120 GREEN_COMMIT：正式 commit G 与 lineage 证明

注：GREEN_COMMIT 由 `test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable` 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0120-01（创建正式commitG；green.committed；Gparent=B；trailersTracks-Task/Tracks-Attempt/Tracks-R/Tracks-Issue/Tracks-AC）|integration| test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable |IF-IMPL-002,IF-IMPL-004|
|AC-FR0120-02（R先于Glineage证明：ref+trailer+事件序列三件联合；不作Gitancestry断言）|integration| test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable |IF-IMPL-002,IF-IMPL-004|

### 8m. FR-0130 REFACTOR 与质量门禁分层

注：REFACTOR 由 quality-gate integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0130-01（dispatchDevonphase=refactor；可返回no_change+理由；no_change+全绿->TASK_REVIEW）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |IF-IMPL-001,IF-IMPL-002|
|AC-FR0130-02（REFACTOR_GATE重跑GREEN_GATE；质量门禁分层：生产全检查、测试仅R0801+C0302）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |IF-IMPL-002,IF-IMPL-005|
|AC-FR0130-03（动publicinterface->stage.rolled_backupstream；通过->TASK_REVIEW；失败->REFACTOR）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |IF-IMPL-001,IF-IMPL-002|

### 8n. FR-0140 TASK_REVIEW 与 PRISM_FINAL

注：TASK_REVIEW/PRISM_FINAL 由 execution-gates integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0140-01（TASK_REVIEW校验scope/secret/ACtrace/lineage/budget；通过->PRISM_FINAL；budget/scopefail->GREEN）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |IF-IMPL-001,IF-IMPL-002|
|AC-FR0140-02（PRISM_FINALdispatchPrism评完整range+lineage；反自述三件套；pass->TASK_DONE；revise(实现)->GREEN；revise(Red)->RED）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |IF-IMPL-001,IF-IMPL-002|
| AC-FR0140-03（revise 经 trac discuss 锚定；task.completed 在 pass 后出现；trac report 展示 RGR lineage） | integration | test_execution_gates.py::test_rgr_phase_events_and_audit_evidence | IF-IMPL-001, IF-IMPL-002, IF-IMPL-007 |

### 8o. FR-0150 DIAGNOSE 四路诊断与 SHIELD_FIX

注：DIAGNOSE/SHIELD_FIX 由 execution-gates integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0150-01（DIAGNOSE四路诊断；分流永不交给Human；impl_defect->GREEN；test_defect->SHIELD_FIX；stub_gap->M-DESIGN；ac/spec_gap->M-ACC/M-SPEC）|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |IF-IMPL-001,IF-IMPL-002|
| AC-FR0150-02（分流结论落 verdict.failed(reason)，reason 限于封闭集；trac report 展示分类与路由） | integration | test_execution_gates.py::test_diagnose_and_shield_fix_public_routes | IF-IMPL-002, IF-IMPL-007 |
| AC-FR0150-03（SHIELD_FIX dispatch Shield 修测试；test.committed；重跑 GREEN_GATE；Shield 修复落在冻结路径集内） | integration | test_execution_gates.py::test_diagnose_and_shield_fix_public_routes | IF-IMPL-002, IF-SHIELD-001 |
| AC-FR0150-04（return upstream 后目标之后的 task graph/baselines/lineage/commits 标记 stale/superseded；不复用旧绿色证据） | integration | test_execution_gates.py::test_diagnose_and_shield_fix_public_routes | IF-IMPL-002 |

### 8p. FR-0160 ISLAND_GATE_2：出口门禁与边界退出

注：ISLAND_GATE_2 由 island-gate integration、execution-gates 与 e2e journey 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0160-01（全部 task 完成后进入；trac check reach 无孤岛 + 全量 int+e2e 变绿 -> 退出；失败不退出） | integration | test_island_gate_2.py::test_island_gate_two_requires_reach_and_full_suites | IF-IMPL-002, IF-REACH-002 |
|AC-FR0160-02（全量执行有失败->DIAGNOSE；verdict.failed(island)->PLANNING）|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |IF-IMPL-001,IF-IMPL-002|
| AC-FR0160-03（通过 -> stage.exited(M-IMPL) -> run.completed(boundary)；不进入 M-VERIFY） | e2e | test_m_impl_journey.py::test_boundary_after_m_impl | IF-IMPL-001 |
| AC-FR0160-04（M-IMPL 无 Human 门禁：退出依据全程序证据，无 human.review/approval 作为退出前置） | e2e | test_m_impl_journey.py::test_boundary_after_m_impl | IF-IMPL-001 |

### 8q. FR-0170 Devon opencode agent 接入与 manifest 越界审计

注：Devon dispatch 由 integration 覆盖，deliverable schema 经既有 `test_real_deliverables_consistent` 真值驱动覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-FR0170-01（AGENT_NAME增补devon->Devon；物化与回收同构）|integration| test_devon_dispatch.py::test_devon_dispatch_manifest_and_audit_evidence |IF-DEVON-001|
|AC-FR0170-02（Devon.md加入deliverables；frontmatterversion+IQ；无permission块）|integration| test_deliverables.py::test_real_deliverables_consistent |IF-DEVON-001|
| AC-FR0170-03（manifest 越界审计：越权写 -> over_reach failure_class -> git 回滚；回滚仅移除 Devon 改动） | integration | test_devon_dispatch.py::test_devon_dispatch_manifest_and_audit_evidence | IF-DEVON-001 |

### 8r. FR-0180 tasks.json / tasks.md 真相源与 Runtime 解析

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0180-01（tasks.json 为 task graph 唯一机器真相；Runtime 解析驱动 DAG 调度；JSON schema 含 9 项必填 task info：task_id/issue_number/description/ac_refs/fr_refs/if_ids/test_refs/scope_boundary/depends_on + batch/parallel/budget；tasks.md 为人类可读投影由 Runtime 从 tasks.json 确定性生成；进展投影入 events/db；trac report 重建） | integration | test_tasksjson_validate.py::test_tasksjson_parsed_by_runtime + test_tasksmd_projected + test_report_rebuild | IF-IMPL-003, IF-IMPL-007, IF-IMPL-002 |
| AC-FR0180-02（trac validate --file tasks.json 校验 DAG 无环 / scope 边界不重叠 / required AC 覆盖闭合 / IF- 有效性 / issue number 有效性；5 项 check） | integration | test_tasksjson_validate.py::test_validate_dag + test_validate_scope + test_validate_ac_coverage + test_validate_if_validity + test_validate_issue_numbers | IF-IMPL-003, IF-VALIDATE-001 |
| AC-FR0180-04（trac validate --file tasks.json 5 项 check 逐项校验：DAG acyclic / scope non-overlap / required AC coverage closure / IF- validity / issue number validity；任一 fail 非零退出并指出位置） | integration | test_tasksjson_validate.py::test_five_checks_individual_failures | IF-IMPL-003, IF-VALIDATE-001 |
| AC-FR0180-05（Runtime 与 Prism 职责分工：Runtime 只做确定性结构校验，不做语义评审；tasks.md 语义保真度不由 Runtime 校验） | integration | test_tasksjson_validate.py::test_runtime_validation_is_structural_only | IF-VALIDATE-001 |

### 8s. FR-0190 dispatch 物化完整性合同

注：dispatch 物化合同由单一 integration 组合函数覆盖全部公开 payload 不变量。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0190-01（command.issued 前 assignment 物化完整上下文；含 10 项物化字段） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
|AC-FR0190-02（M-TEST/M-IMPLescalation允许to_stage=M-DESIGN；M-TEST回归基线+M-IMPL新增路径）|integration| test_dispatch_materialization.py::test_escalation_to_design_m_impl+test_m_test_escalation_regression,test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload |IF-IMPL-001,IF-IMPL-002|
| AC-FR0190-03（canonical .tracks/project/project.toml 唯一允许；其他 .tracks/** fail closed） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
| AC-FR0190-04（M-DESIGN 输出合同：test-plan §8 canonical header + interfaces §5 IF Registry；解析 {ac_id, layers, if_ids}；missing/empty/duplicate/unregistered fail closed） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002, IF-VALIDATE-001 |
| AC-FR0190-05（test_tasks 注入：Shield WRITE assignment 含非空 test_tasks；无效输入 -> stub_gap） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
| AC-FR0190-06（ResultCheckpoint：invalid retry 清 dispatch flags；Shield WRITE requires_diff=true；pre_dirty_snapshot 持久化与 post 比较） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
| AC-FR0190-07（collection 口径：tests/**/*.py 可 checkpoint；collect-only 只对 test modules；只有 helper fail closed；conftest 不单独判 no-tests） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
| AC-FR0190-08（host project command：.venv/bin/python 不存在回退 sys.executable；项目自有 venv 优先） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
| AC-FR0190-09（rollback evidence：仅 M-TEST stub_gap->M-DESIGN 清 stale failure；其他语义回退保留 evidence） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |
| AC-FR0190-10（覆盖范围：Scribe/Sage/Lex/Archer/Prism/Shield dispatch + Runtime validate/checkpoint/publish/collect/run/red/commit/seal） | integration | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | IF-IMPL-002 |

### 8t. FR-0200 可休眠与崩溃恢复

注：崩溃恢复由 crash-recovery 与 RGR contract integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0200-01（phase 边界事件；重启从 lineage + 事件回放恢复；不重跑已完成 task） | integration | test_crash_recovery.py::test_replay_does_not_duplicate_completed_task_evidence | IF-IMPL-002 |
| AC-FR0200-02（R ref 不可变保证崩溃后 lineage 不丢失；G trailers 可重建 R-G 绑定；崩溃 reconcile 视图终态清理） | integration | test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable | IF-IMPL-002, IF-IMPL-004 |

### 8u. NFR-0010 M-IMPL 控制流维持 kernel 纯函数边界

注：kernel 纯函数的外部效果经 lifecycle/drop-rebuild integration 观察；Devon 另承担 unit 义务。

| AC id | layer | test | IF |
|---|---|---|---|
|AC-NFR0010-01（decide()/project()不碰IO/clock/env/文件系统）|integration| test_m_impl_cycle.py::test_m_impl_public_event_lifecycle |IF-IMPL-001|
|AC-NFR0010-02（副作用归executor；drop投影表后重建一致）|integration| test_m_impl_cycle.py::test_m_impl_public_event_lifecycle |IF-IMPL-001,IF-IMPL-002|

### 8v. NFR-0020 M-IMPL 事件维持 append-only 事件溯源

注：append-only 与投影重建由 M-IMPL lifecycle integration 覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-NFR0020-01（M-IMPL 全程事件 append-only，不改写既有行） | integration | test_m_impl_cycle.py::test_m_impl_public_event_lifecycle | IF-IMPL-001, IF-IMPL-002 |
| AC-NFR0020-02（投影表可从事件完整重建） | integration | test_m_impl_cycle.py::test_m_impl_public_event_lifecycle | IF-IMPL-001, IF-IMPL-002 |

### 8w. NFR-0030 dispatch 活动性可观测

注：dispatch 活动性与 retry 由 `test_retry_activity_and_failure_evidence` 组合覆盖。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-NFR0030-01（trac run 为长时间 Agent 派发发出简洁已 flush 控制台活动；不流式输出海量 stdout；不设 elapsed-time 超时） | integration | test_trac_retry.py::test_retry_activity_and_failure_evidence | IF-IMPL-002 |
|AC-NFR0030-02（<=3次失败后tracrun与tracstatus暴露attempt计数+失败类+原因）|integration| test_trac_retry.py::test_retry_activity_and_failure_evidence |IF-IMPL-001|
|AC-NFR0030-03（tracretry追加human.retry事件、清escalationgate、重置attempt预算、保留失败证据、不自动重派；非escalation拒绝；--clear-evidence语义）|integration| test_trac_retry.py::test_retry_activity_and_failure_evidence |IF-IMPL-001|

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

### 8z. FR-0230～FR-0233 / NFR-0080 live release evidence 增量

以下 node ID 是 Shield 的精确交付合同：`tests/integration/test_release_evidence.py` 与 `tests/e2e_live/test_m_impl_release_evidence.py` 当前待 Shield 创建；Devon 不写这些测试。integration 只跑确定性 schema/provenance/CLI negatives；e2e_live 只跑真实成功 journey 或 credential probe。fake 与 live 不共享同一 AC。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0230-01（真实 OpencodeBackend 至少一个 task 完整 RGR 到 boundary） | e2e | tests/e2e_live/test_m_impl_release_evidence.py::test_real_opencode_m_impl_rgr_release_evidence | IF-LIVE-001, IF-RELEASE-001 |
| AC-FR0230-02（公开 events/Git/report 可验证完整阶段序列） | e2e | tests/e2e_live/test_m_impl_release_evidence.py::test_real_journey_exposes_events_lineage_report_and_boundary | IF-LIVE-001 |
| AC-FR0230-03（失败/取消/不完整不产成功证据；fake 不成为 release evidence） | integration | tests/integration/test_release_evidence.py::test_incomplete_failed_cancelled_and_fake_never_write_success | IF-LIVE-001, IF-RELEASE-001 |
| AC-FR0231-01（成功 bundle 绑定 run/backend/I-O/event range/lineage/boundary/candidate） | e2e | tests/e2e_live/test_m_impl_release_evidence.py::test_real_journey_writes_complete_auditable_bundle | IF-LIVE-001 |
| AC-FR0231-02（真实性标记拒绝 fake/simulated/manual/incomplete audit） | integration | tests/integration/test_release_evidence.py::test_provenance_rejects_fake_simulation_overlay_manual_events_and_incomplete_io | IF-LIVE-001, IF-RELEASE-001 |
| AC-FR0232-01（check 读取最近真实成功 journey 及审计记录） | integration | tests/integration/test_release_evidence.py::test_check_accepts_latest_current_real_auditable_bundle | IF-RELEASE-001 |
| AC-FR0232-02（candidate SHA 必须等于 current HEAD；新 commit 使 stale） | integration | tests/integration/test_release_evidence.py::test_check_rejects_stale_and_candidate_sha_mismatch | IF-RELEASE-001 |
| AC-FR0232-03（satisfied text/JSON 与 exit 0） | integration | tests/integration/test_release_evidence.py::test_check_success_text_json_and_exit_contract | IF-RELEASE-001 |
| AC-FR0232-04（missing/failed/stale/not-real fail closed + next action） | integration | tests/integration/test_release_evidence.py::test_check_fail_closed_reason_precedence_text_json_and_exit | IF-RELEASE-001 |
| AC-FR0232-05（只证明 evidence，不实现后续阶段/副作用） | integration | tests/integration/test_release_evidence.py::test_check_is_read_only_and_emits_no_verify_release_publish_effect | IF-RELEASE-001 |
| AC-FR0233-01（routine 无凭据显式 skipped 且无 success evidence） | e2e | tests/e2e_live/test_m_impl_release_evidence.py::test_routine_missing_credentials_reports_live_skipped_without_evidence | IF-LIVE-001 |
| AC-FR0233-02（有凭据+opt-in 执行并报告，成功可供 check） | e2e | tests/e2e_live/test_m_impl_release_evidence.py::test_real_opencode_m_impl_rgr_release_evidence | IF-LIVE-001, IF-RELEASE-001 |
| AC-FR0233-03（发布验证显式 check；fake/skip 不可替代） | integration | tests/integration/test_release_evidence.py::test_routine_skip_and_fake_never_satisfy_release_prerequisite | IF-RELEASE-001 |
| AC-NFR0080-01（确定性、可审计、拒绝人工插入） | integration | tests/integration/test_release_evidence.py::test_check_is_deterministic_auditable_and_rejects_event_only_evidence | IF-LIVE-001, IF-RELEASE-001 |
| AC-NFR0080-02（credential-less skip/fake/simulated 不产生也不满足） | integration | tests/integration/test_release_evidence.py::test_non_real_sources_neither_produce_nor_satisfy_evidence | IF-LIVE-001, IF-RELEASE-001 |

### 8aa. FR-0234～FR-0237 / NFR-0090 doc-comment-first 增量

Shield 新建以下测试。integration 覆盖规则、错误、原子性与恢复；e2e 只覆盖 E-04 面向操作者 happy path。所有断言只落 interfaces.md §1m/§2e/§4e 出口。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0234-01（合法新讨论在所有普通验证前暂停；正文编辑优先拒绝） | integration | tests/integration/test_doc_comment_first.py::test_legal_discussion_pauses_before_all_ordinary_validation | IF-DOCGAP-001, IF-QUARANTINE-001 |
| AC-FR0234-02（Devon/Shield 固定可评论文档；只认本 dispatch 新线程） | integration | tests/integration/test_doc_comment_first.py::test_role_comment_scope_and_predispatch_threads_do_not_retrigger | IF-DOCGAP-001 |
| AC-FR0234-03（status/discuss/replay/report 可见等待、origin、quarantine 与审计） | e2e | tests/e2e/test_doc_comment_journey.py::test_design_discussion_adjudication_resume_happy_path | IF-DOCGAP-001, IF-QUARANTINE-001 |
| AC-FR0234-04（无新讨论/非法正文时普通验证路径不变） | integration | tests/integration/test_doc_comment_first.py::test_outcome_without_new_discussion_uses_original_validation_path | IF-DOCGAP-001 |
| AC-FR0235-01（Prism 原线程确认 design gap 后路由 Archer；无 Human 技术门） | integration + e2e | tests/integration/test_doc_comment_first.py::test_design_gap_routes_archer_without_human_gate + tests/e2e/test_doc_comment_journey.py::test_design_discussion_adjudication_resume_happy_path | IF-DOCGAP-001 |
| AC-FR0235-02（非设计缺口给原 Agent 指引；线程关闭前保持暂停） | integration | tests/integration/test_doc_comment_first.py::test_agent_correction_stays_paused_until_original_thread_closes | IF-DOCGAP-001 |
| AC-FR0235-03（闭环后相同 logical role/task/phase 的新 dispatch/attempt 重新验证） | integration + e2e | tests/integration/test_doc_comment_first.py::test_closed_thread_creates_new_attempt_not_old_outcome_success + tests/e2e/test_doc_comment_journey.py::test_design_discussion_adjudication_resume_happy_path | IF-DOCGAP-001, IF-QUARANTINE-001 |
| AC-FR0236-01（只隔离授权 Agent 非文档变化；排除 Human/pre-dirty；不用 shared index） | integration | tests/integration/test_doc_comment_quarantine.py::test_quarantine_excludes_human_predirty_and_shared_index | IF-QUARANTINE-001, IF-DOCGAP-001 |
| AC-FR0236-02（empty 可裁定；empty/held 在重验前均无 commit/checkpoint/gate/success） | integration | tests/integration/test_doc_comment_quarantine.py::test_empty_and_held_quarantine_never_checkpoint_before_resume | IF-QUARANTINE-001 |
| AC-FR0236-03（中断重启恢复裁定与 quarantine identity/status） | integration | tests/integration/test_doc_comment_quarantine.py::test_restart_rebuilds_doc_gap_and_quarantine_state | IF-QUARANTINE-001, IF-DOCGAP-001 |
| AC-FR0236-04（current restore；stale/conflict discard 或完整重验；不触及 pre-dirty） | integration | tests/integration/test_doc_comment_quarantine.py::test_resume_restores_current_and_discards_stale_without_touching_predirty | IF-QUARANTINE-001, IF-DOCGAP-001 |
| AC-FR0237-01（非 discussion 正文整回合原子回滚；合法线程不能掩盖；不进 Prism） | integration | tests/integration/test_doc_comment_first.py::test_illegal_body_edit_atomically_rolls_back_entire_outcome | IF-DOCGAP-001, IF-QUARANTINE-001 |
| AC-FR0237-02（status/replay/report 显示 over_reach、路径、atomic rollback；新 attempt） | integration | tests/integration/test_doc_comment_first.py::test_illegal_edit_reports_paths_and_redispatches_new_attempt | IF-DOCGAP-001 |
| AC-FR0237-03（失败/回滚证据可重放且不扩大正文权限） | integration | tests/integration/test_doc_comment_first.py::test_illegal_edit_failure_evidence_survives_replay | IF-DOCGAP-001 |
| AC-NFR0090-01（所有 doc-gap/quarantine/resume 事实 append-only 且可重建） | integration | tests/integration/test_doc_comment_quarantine.py::test_restart_rebuilds_doc_gap_and_quarantine_state | IF-DOCGAP-001, IF-QUARANTINE-001 |
| AC-NFR0090-02（中断不越过门禁；隔离/回滚原子且不污染 index/无关 task） | integration | tests/integration/test_doc_comment_quarantine.py::test_interruption_never_leaks_quarantine_past_gates | IF-DOCGAP-001, IF-QUARANTINE-001 |
| AC-NFR0090-03（重放不提升旧 outcome、不重复成功；新 attempt 全量重验） | integration | tests/integration/test_doc_comment_quarantine.py::test_replay_never_promotes_old_outcome_or_duplicates_success | IF-DOCGAP-001, IF-QUARANTINE-001 |

<!-- preserved inline-discussion threads; validator ignores HTML comments
> **Prism [RESOLVED]:** PRISM-Q04-003 [severity=blocker] [defect_classification=test_defect] [artifact=tests/integration/test_release_evidence.py:187-227,359-406; tests/e2e_live/test_m_impl_release_evidence.py:35-75; tests/counterexamples/v0.5/kill-manifest.json] [AC=AC-FR0230-03,AC-FR0231-02,AC-FR0233-01,AC-NFR0080-01,AC-NFR0080-02] criterion=1+3+4. Provenance and routine-skip negatives are incomplete/non-observable: the provenance loop checks only three flags, not manual event_origin or incomplete agent I/O; the NFR test checks only fake binding; and _require_release_credentials calls pytest.skip before _no_success_evidence, so the credential-less branch never asserts absence of success evidence. In addition, none of the Q-04 tests has a binding in tests/counterexamples/v0.5/kill-manifest.json or a Q-04 counterexample patch/kill record, violating criterion 3. Expected revision: add distinct public-surface negatives for FakeBackend, TRAC_FAKE_SIMULATE, outcome simulation, assignment overlay, manual events and incomplete audit; make routine missing-credential behavior observably assert LIVE_SKIPPED plus no bundle; bind every required Q-04 integration/e2e test to a minimal counterexample with kill evidence.

> **Prism [RESOLVED]:** PRISM-Q04-002 [severity=blocker] [defect_classification=test_defect] [artifact=tests/integration/test_release_evidence.py:284-318,321-340] [AC=AC-FR0232-03,AC-FR0232-04,AC-FR0232-05] criterion=1+2. The tests bypass the public CLI contract by importing check_release_evidence and asserting internal ReleaseEvidenceReport fields; they never execute trac check release-evidence or --json, and do not assert the exact text/JSON/stderr/exit 0/1 contract or recovery next-action required by interfaces.md §2d. The read-only test snapshots only the evidence root, so it also cannot detect forbidden event/ref/commit/worktree effects. Expected revision: invoke the installed public `trac check release-evidence` command entrypoint and assert exact deterministic stdout/stderr/exit for satisfied and each fail-closed class, plus snapshot all contractually protected observable state for AC-FR0232-05.
>> **Shield:** PRISM-Q04-002 addressed. Exact changed nodes in tests/integration/test_release_evidence.py (node IDs + TRACKS-TRACE markers preserved verbatim): test_check_success_text_json_and_exit_contract (AC-FR0232-03), test_check_fail_closed_reason_precedence_text_json_and_exit (AC-FR0232-04), test_check_is_read_only_and_emits_no_verify_release_publish_effect (AC-FR0232-05). All three now invoke the installed public command surface via subprocess [sys.executable -m tracks.cli.main check release-evidence [--json]] instead of importing check_release_evidence internals; assertions land on public stdout/stderr/exit. AC-FR0232-03 asserts exact text line (release-evidence: satisfied (backend=opencode, run <run_id>, candidate HEAD <full_sha>, branch <branch>)), empty stderr, exit 0, plus canonical compact sort-key --json with status/reason_code/backend/candidate_sha/run_id and trailing-LF single line. AC-FR0232-04 asserts every closed-set fail-closed reason code in precedence order (missing, stale, malformed, not_real, audit_incomplete, journey_incomplete), each yielding exit 1, empty stderr, exact two-line text (release-evidence: NOT satisfied — <reason> + '  next: rerun the opt-in live journey at current HEAD, then re-check' per IF-RELEASE-001), and canonical --json with status=not_satisfied. AC-FR0232-05 snapshots and byte-compares canonical evidence bytes, runtime events DB bytes, refs, HEAD and commit-graph identity, index, and worktree before and after BOTH text and JSON checks, asserting no verify/release/publish effect. Collection: pytest -n 4 --collect-only = 10 collected, exit 0. Legal Red: all 3 revised nodes fail ONLY with 'usage: trac check <deliverables|trace|reach>' stderr / exit-1 (public release-evidence subcommand not yet wired — missing IF-RELEASE-001/public CLI wiring); no test-side assembly errors. ruff check on the file: All checks passed (removed unused write_live_evidence F401, wrapped overlong signature E501). Leaving the Prism-owned thread open for Prism to resolve.
>> **Shield:** PRISM-Q04-002 acceptance strengthening (follow-up). Three changes in tests/integration/test_release_evidence.py; the three Q-04 node IDs and TRACKS-TRACE markers preserved verbatim; T-004/PRISM-Q04-003 untouched. (1) _run_release_check now executes the real installed public console script adjacent to the interpreter, Path(sys.executable).with_name('trac'), and fails clearly if that executable is absent; python -m tracks.cli.main is no longer used. (2) For satisfied and every not-satisfied JSON case the test constructs the complete section-2d seven-field object (backend, candidate_sha, evidence_path, event_bounds, reason_code, run_id, status) from independent fixture facts and asserts stdout byte-equals json.dumps(expected, sort_keys=True, separators=(',',':')) plus one LF; expected output is never derived by parsing/reserializing actual stdout, and the exact key set rejects extra/missing fields. Reason precedence is strengthened with compound-invalid inputs: stale coexists with malformed + not_real + audit_incomplete + journey_incomplete bundles on non-current SHAs; malformed (byte-max run_id, unreadable) coexists with a lower run_id bundle carrying not_real + audit_incomplete + journey_incomplete defects; not_real coexists with audit_incomplete + journey_incomplete; audit_incomplete coexists with journey_incomplete — each expected reason must still be selected. Exact deterministic text stdout, empty stderr, exit 0/1, every closed-set fail-closed reason, and the exact IF-RELEASE-001 recovery next-action preserved. (3) The read-only check runs both text and JSON variants via the installed console script and byte-compares canonical evidence bytes, runtime events database bytes, refs, HEAD and commit-graph identity, index, and worktree before and after, asserting no verify/release/publish effect. F401/E501: ruff clean at HEAD and after revision (verified with --select F401,E501). Evidence: .venv/bin/python -m pytest -n 4 -p no:cacheprovider --collect-only tests/integration/test_release_evidence.py = 10 collected, exit 0; same flags on exactly the three revised nodes = 3 failed, each failure caused only by the public release-evidence subcommand being unwired (stderr 'usage: trac check <deliverables|trace|reach>' / exit 1 against the section-2d empty-stderr/exit contract) — legal Red limited to missing IF-RELEASE-001/public CLI wiring, no test-side assembly errors; .venv/bin/python -m ruff check tests/integration/test_release_evidence.py = All checks passed. Leaving the Prism-owned thread open.

> **Prism [RESOLVED]:** PRISM-Q04-001 [severity=blocker] [defect_classification=test_defect] [artifact=tests/e2e_live/test_m_impl_release_evidence.py:80-109,112-185; tests/e2e_live/test_full_journey.py:545-605] [AC=AC-FR0230-01,AC-FR0230-02,AC-FR0231-01,AC-FR0233-02] criterion=1+2+5. The named live happy-path tests call _phase_m_test_to_boundary, whose contract and assertions stop at stage.exited(M-TEST) -> run.completed(boundary), not an M-IMPL BASELINE/task RED-GREEN-REFACTOR/review/ISLAND_GATE_2 journey. Thus the tests cannot observe the required M-IMPL public sequence and any current IF-LIVE-001 Red does not establish faithful coverage. Expected revision: drive the installed current wheel from M-TEST EXIT into M-IMPL through at least one real OpencodeBackend task, and assert the required events, real Devon/Prism dispatch receipts, R/G lineage, report, gates, task completion, M-IMPL exit and boundary without fake/simulation/overlay/manual insertion.
>> **Shield:** PRISM-Q04-001 (T-006) addressed. Exact changed files: tests/e2e_live/harness.py (adds opt-in no_overlay=True run mode; legacy overlay behavior unchanged) and tests/e2e_live/test_m_impl_release_evidence.py (no-overlay installed-wheel release journey). Five collected nodes: test_routine_missing_credentials_reports_live_skipped_without_evidence, test_real_opencode_m_impl_rgr_release_evidence, test_real_journey_exposes_events_lineage_report_and_boundary, test_real_journey_writes_complete_auditable_bundle, test_no_overlay_run_args_have_no_assignment_overlay_and_no_console. No-overlay contract pass: _command_args builds ['run','--max-dispatches',N] with no --assignment-overlay and empty console; overlay mode fails closed when scenario absent. Precise LIVE_SKIPPED evidence: exact reason 'LIVE_SKIPPED: missing <NAME>; set TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY to enable' plus _no_success_evidence asserting no satisfied bundle. Ruff passes on both files. The credentialed journey is a faithful forward-looking Red: it fails closed with an explicit AssertionError if the runtime jumps M-TEST EXIT straight to boundary without entering M-IMPL (stage.entered(M-IMPL) -> freeze_baseline(current) -> real Devon RED/GREEN/REFACTOR and Prism PRISM_RED/PRISM_FINAL dispatch I-O correlation -> R/G lineage -> RED_GATE/GREEN_GATE/run_refactor_gate/TASK_REVIEW/check_island_2 receipts -> ISLAND_GATE_2 -> stage.exited(M-IMPL) -> run.completed(boundary)), requiring M-TEST EXIT -> M-IMPL routing before this live path satisfies. Leaving the Prism-owned thread open for Prism to resolve.
-->

## 9. SM-01 转移覆盖清单（NFR-0020，normative 依据 SPEC-005「状态与生命周期」）

每条转移 ≥1 测试走到一次；清单内测试须存在且通过。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。

注：SM-01 跨模块断言整合在 planning-dispatch、execution-gates、RGR、quality-gate 与 worktree integration 函数；下表引用这些真实组合出口。

| 转移 | 内容摘要 | 层 | 测试 |
|:---|:---|:---|:---|
|SM-01.1|M-TESTEXIT->stage.entered(M-IMPL)->BASELINE|e2e| test_full_journey_v05.py::test_full_journey_to_boundary_includes_m_impl |
|SM-01.2|BASELINE->PLANNING：baselinecurrent|integration| test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics |
|SM-01.3|BASELINE->NEEDS_ATTENTION：缺失/stale/冲突|integration| test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics |
|SM-01.4|NEEDS_ATTENTION->BASELINE：已reconcile|integration| test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics |
|SM-01.5|NEEDS_ATTENTION->stage.rolled_back：rolled_back|integration| test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics |
|SM-01.6|PLANNING->ISLAND_GATE_1：validatepass|integration\| test_taskgraph_validate.py::test_taskgraph_happy_path_reaches_public_file_and_event |
|SM-01.7|PLANNING->PLANNING：validatefail，重派Archer（<=3）|integration| test_taskgraph_validate.py::test_taskgraph_key_error_paths_fail_at_validate_cli |
|SM-01.8|ISLAND_GATE_1->PRISM_PLAN：闭合|integration\| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.9|ISLAND_GATE_1->PLANNING：verdict.failed(island)|integration\| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.10|PRISM_PLAN->TASK_DISPATCH：prism.verdict(pass)|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.11|PRISM_PLAN->PLANNING：revise->Archer|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.12|PRISM_PLAN->stage.rolled_back：设计缺口->M-DESIGN|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.13|PRISM_PLAN->stage.rolled_back：需求缺口->M-SPEC/M-ACC（Human确认）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.14|TASK_DISPATCH->RED：task.started（DAGreadytask+writelock+manifest）|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.15|RED->RED_GATE：Devonoutcome（test-onlydiff）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.16|RED_GATE->RED_CHECKPOINT：合法Red|integration| test_rgr_contract.py::test_m_impl_red_classification_excludes_stub_tokens |
|SM-01.17|RED_GATE->RED：非法Red，重派Devon|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.18|RED_CHECKPOINT->PRISM_RED：red.checkpointed|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.19|PRISM_RED->GREEN：prism.verdict(pass)绑定R|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.20|PRISM_RED->RED：revise->新attempt|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.21|GREEN->GREEN_GATE：Devonoutcome（从Rtree恢复，最小实现）|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.22|GREEN_GATE->GREEN_COMMIT：全过|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.23|GREEN_GATE->GREEN：实现缺陷，重派Devon|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.24|GREEN_GATE->DIAGNOSE：int失败归因不明|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.25|DIAGNOSE->GREEN：实现缺陷->Devon|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
|SM-01.26|DIAGNOSE->SHIELD_FIX：测试缺陷->Shield|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
|SM-01.27|DIAGNOSE->stage.rolled_back：接口/架构不足->M-DESIGN|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
|SM-01.28|DIAGNOSE->stage.rolled_back：AC/Spec缺口->M-ACC/M-SPEC（Human确认）|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
|SM-01.29|SHIELD_FIX->GREEN_GATE：重跑受影响测试|integration| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
|SM-01.30|GREEN_COMMIT->REFACTOR：green.committed（Gcommit,parent=B,trailers）|integration| test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable |
|SM-01.31|REFACTOR->REFACTOR_GATE：Devonoutcome（可no_change）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.32|REFACTOR_GATE->TASK_REVIEW：通过（committed\|no_change）|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.33|REFACTOR_GATE->REFACTOR：失败，重派|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.34|REFACTOR_GATE->stage.rolled_back：动publicinterface->upstream|integration| test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events |
|SM-01.35|TASK_REVIEW->PRISM_FINAL：校验通过|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.36|TASK_REVIEW->GREEN：budget/scopefail->Devon|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.37|PRISM_FINAL->TASK_DONE：prism.verdict(pass)|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.38|PRISM_FINAL->GREEN：revise（实现）->Devon|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.39|PRISM_FINAL->RED：revise（Red测试）->新lineage|integration| test_execution_gates.py::test_rgr_phase_events_and_audit_evidence |
|SM-01.40|TASK_DONE->TASK_DISPATCH：task.completed，还有readytask|integration| test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted |
|SM-01.41|TASK_DONE->ISLAND_GATE_2：全部task完成|integration\| test_island_gate_2.py::test_island_gate_two_requires_reach_and_full_suites |
| SM-01.42 | ISLAND_GATE_2 -> stage.exited(M-IMPL) -> run.completed(boundary)：通过 | integration + e2e | test_island_gate_2.py::test_island_gate_two_requires_reach_and_full_suites, test_m_impl_journey.py::test_boundary_after_m_impl |
|SM-01.43|ISLAND_GATE_2->DIAGNOSE：全量执行有失败|integration\| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
|SM-01.44|ISLAND_GATE_2->PLANNING：verdict.failed(island)|integration\| test_execution_gates.py::test_diagnose_and_shield_fix_public_routes |
| 休眠回放 | M-IMPL 各子状态休眠后事件回放恢复 | integration | test_crash_recovery.py::test_replay_does_not_duplicate_completed_task_evidence |

---

## 10. 既有测试更新（planned changes，AC-FR0010-04）

v0.5 的 boundary 从 M-TEST->M-IMPL 移至 M-IMPL->M-VERIFY，影响既有 e2e 测试：

| 既有测试 | 变更 | 理由 |
|:---|:---|:---|
| `tests/e2e/test_full_journey_v05.py::test_full_journey_to_boundary_includes_m_impl` | 更新：M-TEST EXIT 后断言 `stage.entered(M-IMPL)`（而非 `run.completed`）；最终 boundary 终态移至 M-IMPL 后；M-TEST 之前事件前缀逐字节稳定 | SM-01.1：M-TEST EXIT -> M-IMPL（不再 run.completed） |
| `tests/e2e/test_full_journey.py::test_prism_revise_drives_a_second_review_round` | 更新：M-DESIGN Prism revise 后仍进入 M-TEST -> M-IMPL（而非直接 run.completed） | boundary 移动 |
| `tests/e2e/test_full_journey.py::test_walk_to_design_complete` | 更新：design complete 后继续 M-TEST -> M-IMPL | boundary 移动 |
| `tests/e2e/test_full_journey.py::test_bounded_walk_matches_harness_step_boundaries` | 更新：增加 M-IMPL 步骤（BASELINE/PLANNING/.../ISLAND_GATE_2/EXIT） | M-IMPL 接入 bounded walk |
| `tests/unit/test_machine_design.py` | 更新：M-DESIGN EXIT 后断言 `stage.entered(M-TEST)`（不变）；M-TEST EXIT 后断言 `stage.entered(M-IMPL)` | _NEXT_STAGE 增补 M-TEST->M-IMPL |
| `tests/unit/test_machine_m_test.py` | 更新：M-TEST EXIT 后断言 `stage.entered(M-IMPL)`（而非 `run.completed`） | _NEXT_STAGE 增补 M-TEST->M-IMPL |

AC-FR0010-04 要求"M-TEST 之前事件前缀逐字节稳定"：上述更新的断言只触及 M-TEST EXIT 及之后的 Events，M-TEST 之前的事件序列不变。

---

## 11. e2e Happy Path 范围（test_m_impl_journey.py）

### 11.0 真实 live happy path（FR-0230 增量）

`tests/e2e_live/test_m_impl_release_evidence.py` 是独立于 fake `test_m_impl_journey.py` 的唯一真实路径。它从 current candidate wheel 安装后的 `trac run` 进入，顺序观察：M-IMPL BASELINE → 至少一个 task 的真实 Devon RED dispatch/outcome → RED_GATE → PRISM_RED → GREEN dispatch/outcome → GREEN_GATE → GREEN_COMMIT → REFACTOR dispatch/outcome → REFACTOR_GATE → TASK_REVIEW → PRISM_FINAL → `task.completed` → ISLAND_GATE_2 → `stage.exited(M-IMPL)` → `run.completed(boundary)` → canonical evidence → `trac check release-evidence` satisfied。测试不得注入 assignment overlay、fake token、模拟 outcome 或事件。

本路径不覆盖错误矩阵；错误/边界全部在 `tests/integration/test_release_evidence.py`。routine credential probe 是同一 live 文件的环境合同，不把 skip 视为 happy path。发布 job 检测到 pytest skip count 非零即失败。

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
- BASELINE stale/冲突 -> NEEDS_ATTENTION -> test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics
- PLANNING validate fail 重派 -> test_taskgraph_validate.py::test_taskgraph_key_error_paths_fail_at_validate_cli
- ISLAND_GATE_1 不闭合 -> test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted
- PRISM_PLAN revise / 设计缺口 / 需求缺口 -> test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted
- RED_GATE 非法红 -> test_execution_gates.py::test_rgr_phase_events_and_audit_evidence
- RED_GATE 合法红排除 stub_token -> test_rgr_contract.py::test_m_impl_red_classification_excludes_stub_tokens
- PRISM_RED revise -> test_execution_gates.py::test_rgr_phase_events_and_audit_evidence
- GREEN_PHASE 受控 diff 回灌 -> test_worktree_contract.py::test_three_worktree_composition_and_cleanup
- GREEN_GATE 失败 / int 归因不明 -> test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events
- REFACTOR_GATE 失败 / 动 public interface -> test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events
- TASK_REVIEW / PRISM_FINAL revise -> test_execution_gates.py::test_rgr_phase_events_and_audit_evidence
- DIAGNOSE 四路路由 / SHIELD_FIX -> test_execution_gates.py::test_diagnose_and_shield_fix_public_routes
- ISLAND_GATE_2 失败 -> test_island_gate_2.py::test_island_gate_two_requires_reach_and_full_suites
- 崩溃恢复 -> test_crash_recovery.py::test_replay_does_not_duplicate_completed_task_evidence
- trac retry -> test_trac_retry.py::test_retry_activity_and_failure_evidence

---

## 12. Counterexample Killability Plan（counterexample 区分力验证计划）

### 12.1. 现状与术语

Shield 在 M-TEST 阶段已为每条 required integration/e2e 测试绑定一个最小合同偏离补丁（counterexample patch），存放在 `tests/counterexamples/v0.5/`，并在 `kill-manifest.json` 中登记 19 条 binding。

**现状**：19 条 binding 中的一部分因前一轮 M-IMPL cycle（fa19f11）已实现对应生产代码（architecture.md §1.0.3 预实现 IF- 合同），其 killability 状态从 `pending-implementation` 转为**可立即 kill 验证**——路由已存在、生产代码可达，应用 counterexample 后测试失败方式应改变。其余指向 `tests/e2e_live`/doc-comment 等新建桩的 binding 保持 `pending-implementation`（见 §12.3）。`kill-manifest.json` 的 `contract` 字段固化旧口径，本条修订后以本计划为准，Shield 按 §12.5 协议重跑未过期的 binding。

**术语**：
- **killed**：应用 patch 后测试按预期失败方式改变（失败栈/断言信息变化），证明断言有区分力。
- **survived**：应用 patch 后测试仍以原方式失败，证明断言未命中被变异的合同条款（区分力缺失）或测试未到达被变异点（结构性 pending）。
- **pending-implementation**：survived 的一个子类——失败不变因为被变异生产代码在 M-TEST 阶段尚未实现/不可达；待对应实现路由存在后变为可 kill。对预实现 IF- 合同（architecture.md §1.0.3），该子类不再适用。

### 12.2. 为何部分 binding 在 M-TEST 阶段无法 kill

M-TEST 阶段的合法 Red 模式（仅对尚未实现的路由适用）：
1. 测试经 `trac` CLI 或 event log 进入被测系统；
2. 路由层（`trac` 命令分发、event 追加）本身由既有 v0.4 实现承载，可达；
3. 一旦路由需要进入未实现的生产代码（如 `create_red_ref`、`run_gates`、`create_gate_worktree`、`parse_tasks_json` 等），桩立即 `raise NotImplementedError("IF-...")`；
4. 测试在断言"事件序列含某 event kind"或"CLI 输出含某 token"处失败，**断言失败发生在桩 raise 之前的事件观察层**；
5. 应用 counterexample（修改桩的返回值/行为）不会改变事件序列或 CLI 输出，因为桩根本没被调用到。

这曾是 M-TEST 阶段的**结构性必然**。但 v0.5 M-DESIGN 修订（architecture.md §1.0.3/§2）确认前一轮 M-IMPL cycle 已实现 IF-IMPL-003/004/005/006 及 M-IMPL 控制流模块（kernel/m_impl.py、executor/m_impl_runtime.py 等），这些模块的公开出口可达、既有 integration 测试通过——因此针对这些模块的 binding 不再因"桩不可达"而 pending，应在 M-TEST 立即重跑 kill 验证确认区分力。仅针对确实新建的桩（`live_evidence.py`、`release_evidence.py`、`doc_comment.py`）的 binding 维持旧语义。

### 12.3. Killability 解锁条件

每条 binding 的 pending 状态将在以下条件之一满足后解锁，转为可执行 kill 验证：

| 解锁触发 | 责任方 | 时机 | kill 验证动作 |
|:---|:---|:---|:---|
| 生产代码已实现（前一轮 M-IMPL cycle，architecture.md §1.0.3 预实现 IF- 合同），路由可达 | —（已存在） | M-TEST 阶段立即（本计划修订后） | Shield 重新应用 patch，确认测试失败方式改变 -> killed |
| Devon 在 M-IMPL RGR 中实现对应新建桩（IF-LIVE-001/IF-RELEASE-001/IF-DOCGAP-001/IF-QUARANTINE-001）的真实路由，使被变异桩可达 | Devon | M-IMPL GREEN 阶段 | Shield 在 SHIELD_FIX（如测试被诊断为缺陷）或 ISLAND_GATE_2 全量执行时重新应用 patch，确认测试失败方式改变 -> killed |
| Prism 在 M-IMPL 评审中指出某 binding 区分力可疑（survived 但非 pending-implementation 语义） | Prism | M-IMPL review | Shield 修订测试断言使其命中合同条款，重做 kill 验证 |

### 12.4. 19 条 binding 的 killability 预期

下表给出每条 binding 在 Devon 实现路由后的预期 kill 行为。每条 binding 的 patch 文件已在 `tests/counterexamples/v0.5/` 落盘，patch 内容为对该 binding `mutation` 字段描述的最小合同偏离。预期 kill = 应用 patch 后测试的失败方式从"桩不可达导致事件缺失"转为"被变异合同条款被实际执行后断言命中变异"。

| # | test | patch | mutation（合同偏离） | 预期 kill 行为（Devon 实现路由后） |
|:--|:---|:---|:---|:---|
| 1 | test_baseline_recalc.py::test_baseline_events_expose_digest_paths_and_error_semantics | baseline_events.patch | test-authority worktree 被误标为 Devon candidate | worktree 创建事件中 `kind` 字段变为 Devon candidate；断言 `kind == "test_authority"` 失败 |
| 2 | test_crash_recovery.py::test_replay_does_not_duplicate_completed_task_evidence | crash_replay.patch | replay 重派已完成 task | replay 事件序列中出现重复 `task.started`；断言"无重复 task 证据"失败 |
| 3 | test_devon_dispatch.py::test_devon_dispatch_manifest_and_audit_evidence | devon_audit.patch | manifest 越界被接受不回滚 | 越界写后无 `over_reach` failure_class 事件 + 无 git 回滚；断言"over_reach -> rollback"失败 |
| 4 | test_dispatch_materialization.py::test_dispatch_assignments_have_complete_public_payload | dispatch_payload.patch | pre_dirty_snapshot 从 command.issued 中缺失 | command.issued event payload 缺 `pre_dirty_snapshot` 字段；断言"10 项物化字段完整"失败 |
| 5 | test_execution_gates.py::test_rgr_phase_events_and_audit_evidence | rgr_phase_events.patch | R/G 事件顺序倒置 + phase evidence 缺失 | 事件序列中 green.committed 先于 red.checkpointed 或缺 phase evidence；断言"RGR 相位事件有序"失败 |
| 6 | test_execution_gates.py::test_diagnose_and_shield_fix_public_routes | diagnose_routes.patch | test_defect 路由至 Human 而非 SHIELD_FIX | DIAGNOSE verdict 中 test_defect 的 reason 路由至 `human.review`；断言"永不交给 Human"失败 |
| 7 | test_island_gate_2.py::test_island_gate_two_requires_reach_and_full_suites | island_gate_2.patch | reach 失败仍允许 stage 退出 | reach 失败后仍出现 `stage.exited(M-IMPL)`；断言"失败不退出"失败 |
| 8 | test_m_impl_cycle.py::test_m_impl_public_event_lifecycle | m_impl_lifecycle.patch | M-IMPL 进入错误子状态 + 改写事件历史 | 事件序列含错误 substate 或既有行被改写；断言"substate 序列 + append-only"失败 |
| 9 | test_planning_dispatch.py::test_planning_and_dispatch_contracts_are_persisted | planning_dispatch.patch | 并行 task dispatch 绕过串行 writelock | [P] 标记 task 被并发 dispatch；断言"串行顺序执行"失败 |
| 10 | test_quality_gate_contract.py::test_quality_gate_layers_reach_public_commands_and_events | quality_gate.patch | 所有质量门禁 report pass 不跑检查 | 事件中无 `run_task_gates`/`run_production_checks` event kind，仅 pass；断言"gate 检查事件存在"失败 |
| 11 | test_rgr_contract.py::test_rgr_git_contract_happy_and_immutable | rgr_git.patch | R ref 用错误名 + 报告 not-created | `refs/trac/rgr/.../red` 不存在或名称错误；断言"R ref 存在 + 不可变"失败 |
| 12 | test_rgr_contract.py::test_m_impl_red_classification_excludes_stub_tokens | red_classification.patch | stub-token failure 被误分类为合法断言失败 | red.classification event 中 stub_token_failure 的 verdict 为 legit；断言"stub_token 不在合法红之列"失败 |
| 13 | test_taskgraph_validate.py::test_taskgraph_happy_path_reaches_public_file_and_event | taskgraph_happy.patch | 非空有效 task plan 被解析为空图 | taskgraph.committed event 中 task 数为 0；断言"非空 task graph"失败 |
| 14 | test_taskgraph_validate.py::test_taskgraph_key_error_paths_fail_at_validate_cli | taskgraph_errors.patch | 环形 task graph 总被接受 | `trac validate --file tasks.json` 对环形 DAG 返回 exit 0；断言"环形 DAG fail"失败 |
| 15 | test_tasklog_report.py::test_tasklog_and_report_are_rebuilt_from_events | tasklog_report.patch | phase-boundary task-log 投影缺失 | `trac report` 重建结果缺 phase-boundary task 条目；断言"投影完整"失败 |
| 16 | test_trac_retry.py::test_retry_activity_and_failure_evidence | retry_evidence.patch | retry 清证据 + 自动重派 | retry 后 failure evidence 消失 + 出现新 dispatch event；断言"保留证据 + 不自动重派"失败 |
| 17 | test_worktree_contract.py::test_three_worktree_composition_and_cleanup | worktree_isolation.patch | gate worktree 暴露为 Devon candidate | worktree 创建事件中 gate worktree 的 kind 为 devon_candidate；断言"三 worktree 类型区分"失败 |
| 18 | test_full_journey_v05.py::test_full_journey_to_boundary_includes_m_impl | full_journey.patch | journey 终止于旧 M-TEST boundary | 事件流缺 `stage.entered(M-IMPL)` 与 `run.completed(boundary)`；断言"M-IMPL 接入 + boundary 移动"失败 |
| 19 | test_m_impl_journey.py::test_boundary_after_m_impl | m_impl_boundary.patch | M-IMPL 退出需 Human + 进入 M-VERIFY | 退出前出现 `human.review` 事件或后续 `stage.entered(M-VERIFY)`；断言"无 Human 门禁 + 不进 M-VERIFY"失败 |

### 12.5. 验证协议（Devon 实现路由后执行）

当 Devon 在 M-IMPL GREEN 阶段实现某 IF- 的真实路由后，Shield 依以下协议对该 IF- 关联的 binding 重新执行 kill 验证：

1. `git stash`（或记录当前工作区状态）；
2. `git apply tests/counterexamples/v0.5/<patch>.patch`；
3. 运行该 binding 指向的测试函数（精确 node id）；
4. 判定：
   - 测试失败方式改变（失败栈/断言信息与未应用 patch 时不同）→ **killed**，更新 `kill-manifest.json` 中该 binding 的 `result` 为 `killed`，记录 `verification` 字段；
   - 测试失败方式不变 → **survived**，说明断言未命中合同条款或测试未到达变异点，Shield 修订测试断言后重验；
5. `git apply -R tests/counterexamples/v0.5/<patch>.patch`（恢复工作区）；
6. 若步骤 4 判定为 survived 且经修订仍 survived，返回 advisory（observability gap 或合同条款不清）。

### 12.6. 合规声明

- 19 条 binding 的 patch 文件均已在 `tests/counterexamples/v0.5/` 落盘，内容为最小合同偏离（只改一个合同条款，不夹带其它变更）。
- 前一轮 M-IMPL cycle（fa19f11）已实现 IF-IMPL-003/004/005/006 及 M-IMPL 控制流模块，使其对应 binding 不再因"桩不可达"而 pending；Shield 应在 M-TEST 立即重跑这些 binding 的 kill 验证确认区分力（§12.5 协议）。
- 指向新建桩（`live_evidence.py`/`release_evidence.py`/`doc_comment.py` 及 doc-gap/quarantine）的 binding 维持 `pending-implementation` 状态，待 Devon 在 M-IMPL 实现路由后依 §12.5 协议执行 kill 验证。
- Shield 不在 M-TEST 阶段强行 kill（不通过 mock 桩或窥探内部状态换取虚假 killed）。
- 若 Prism 在 M-IMPL review 中指出某 binding 的 patch 未真正偏离目标合同（即 patch 本身有问题），Shield 修订 patch 后重登记。
