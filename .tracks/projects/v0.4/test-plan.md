---
spec_id: SPEC-004
created: 2026-08-05
status: draft
sha:
---

# M-TEST 阶段与需求追踪工具 - Test Plan

- **Related acceptance**: `.tracks/projects/v0.4/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.4/interfaces.md` (assertion basis - see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- CLI endpoints (`trac check trace`, `trac check reach`, `trac check deliverables`, `trac status`, `trac validate`, `trac run`) stdout/stderr/exit code.
- Persisted data: `.tracks/runtime/tracks.db` `events` table (assertion primary source), projection tables.
- Document files: `.tracks/projects/v0.4/{story,spec,acceptance}.md` (inline-discussion blockquotes).
- Test files: `tests/integration/*.py`, `tests/e2e/*.py` (existence, collectability, marker format).
- Git state (branch, commits, working tree diff).
- `trac check trace --json` / `trac check reach --json` structured output.
- Deliverable files: `tracks/agents/Shield.md`, `tracks/skills/test-asset-criteria/SKILL.md` (frontmatter existence + version + IQ).

### 1.2. Non-observable Objects (tests do not directly depend on)

- `kernel/machine.py` internal State field values (observed via `trac status` / events).
- `checks/trace.py` / `checks/reach.py` internal parsing data structures (observed via CLI output / `--json`).
- `executor/executor.py` internal subprocess management (observed via events).
- `effects/opencode.py` internal prompt construction (observed via outcome events + audit evidence).
- `effects/audit.py` internal baseline representation (observed via `outcome.received.audit_evidence`).

**Observable contract**: Any internal state that acceptance validation needs must be provided by the implementation layer via events/CLI/file observation points. This is the responsibility of **interfaces.md** - if an AC needs to observe internal state, interfaces.md must have a corresponding outlet (see §6.5).

### 1.3. Cheating Patterns (CI enforced interception)

| #   | Cheating Pattern                | Typical Symptom                                |
| --- | -------------------------------- | ---------------------------------------------- |
| 1   | Change assertions to fit impl    | spec says "throw exception", test changes to "return False" |
| 2   | Use skip to evade validation     | skip/ignore with "see e2e" but e2e is never written |
| 3   | Assertion degradation            | `assert issubclass(X, Exception)` instead of actually submitting and catching |
| 4   | try/except: pass                 | Exception path is swallowed                     |
| 5   | Over-mocking                     | Mock the framework core, testing mock behavior instead |
| 6   | Ground truth uses impl           | Expected value = impl output                   |
| 7   | Hardcoded expected values        | `assert result == 0.15` only because current impl outputs 0.15 |
| 8   | Trivial pass                     | `assert True` / `assert 1 == 1`                |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - The first line of each test function's docstring/comment must contain `AC-FRXXXX-YY@v0.4` (long-format marker, FR-0080/FR-0130).
   - CI scans `tests/`, verifying: each test references at least one AC; each required AC (integration|e2e layer) is referenced by at least one test.
   - Any check failure blocks merge.

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion.
   - No `try: ... except: pass` wrapping the code under test.
   - No test skip/ignore without a GitHub issue link.

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
│   ├── test_machine_m_test.py     # M-TEST reducer/decide branches (SM-01)
│   ├── test_check_trace.py        # check_trace_full pure function (FR-0080)
│   ├── test_check_reach.py        # check_reach pure function (FR-0090)
│   ├── test_red_classifier.py     # classify_red pure function (FR-0050)
│   ├── test_baseline.py           # legacy baseline parsing (FR-0100)
│   ├── test_id_grammar.py         # BS-XX/@version/tombstone validation (FR-0130)
│   ├── test_green_condition.py    # check_design_trace IF- attribution (FR-0140)
│   ├── test_deliverables.py       # Shield.md + criteria pack deliverable gate (FR-0120) [既有，扩展]
│   └── ...                        # 既有 unit tests 不变
├── integration/
│   ├── test_check_trace.py        # trac check trace CLI (FR-0080/0110)
│   ├── test_check_reach.py        # trac check reach CLI (FR-0090/0110)
│   ├── test_m_test_cycle.py       # M-TEST full cycle (FR-0010~0070)
│   ├── test_shield_dispatch.py    # Shield dispatch + write scope audit (FR-0020/0120)
│   ├── test_red_check.py          # Red classification via events (FR-0050)
│   ├── test_criteria_pack.py      # Criteria pack binding + anti-self-report (FR-0040)
│   ├── test_diagnose.py           # DIAGNOSE 4-way routing (FR-0060)
│   ├── test_m_test_exit.py        # EXIT trace gate + test commit + boundary (FR-0070)
│   ├── test_baseline_exemption.py # Legacy baseline exemption (FR-0100)
│   ├── test_id_grammar.py         # ID grammar validation via trac validate (FR-0130)
│   ├── test_green_condition.py    # test-plan IF- validation via trac validate (FR-0140)
│   └── ...                        # 既有 integration tests 不变
├── e2e/
│   ├── test_m_test_journey.py     # M-TEST happy path: M-DESIGN exit -> M-TEST -> boundary (FR-0010~0070)
│   ├── test_full_journey.py       # [既有，更新] 全流程: M-STORY -> ... -> M-TEST -> boundary
│   └── ...                        # 既有 e2e tests 不变
├── e2e_live/                      # live opencode channel (缺凭据 skip)
│   └── ...
├── assets/
│   ├── trace_fixtures/            # story/spec/acceptance + test marker fixtures for trace tool
│   └── reach_fixtures/            # Python package fixtures with known import graphs
├── ground_truth/
│   ├── trace_reference.py         # trace tool independent reference (FR-0080)
│   ├── reach_reference.py         # reach tool independent reference (FR-0090)
│   └── discuss_reference.py       # [既有] discuss parser reference
└── conftest.py                    # 既有 + fake 通道强制 + M-TEST fixtures
```

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_0080_06_short_format_marker_rejected`
- Marker: docstring 首行含 `AC-FRXXXX-YY@v0.4`（长格式强制，FR-0080/FR-0130）

### 2.3. Execution

- **Offline**: Tests do not depend on network (data is pinned).
- **Execution order**: unit (fast) -> integration -> e2e (slow).
- **CI**: Run the full suite on every push.
- **Isolation**: Integration and e2e use pytest markers (`@pytest.mark.integration` / `@pytest.mark.e2e`); default suite runs all, marker-based selection available.
- **M-TEST collection/RED_CHECK**: executor uses `sys.executable -m pytest --collect-only` / `sys.executable -m pytest` subprocess; conftest's `_UNDER_COVERAGE` mechanism inherited (subprocess coverage merge).

### 2.4. Test Data

- **Source**: Built-in fixtures in `tests/assets/trace_fixtures/` and `tests/assets/reach_fixtures/` (synthetic, pinned).
- **Reproducible**: Each CI run produces consistent results (deterministic fixtures, no network).
- **Small data in-repo**: `tests/assets/` fixtures are small Markdown/Python files with known orphan/island structure.
- **Sensitive data**: None (no credentials in test data).
- **Version snapshot**: Fixtures are version-controlled in git; no external data versioning needed.

### 2.5. Installation & Isolation

继承 v0.2 §13（可安装发行物与 live E2E 安装边界）。v0.4 新增 package data（`tracks/agents/Shield.md`、`tracks/skills/test-asset-criteria/SKILL.md`）已在 `[tool.setuptools.package-data]` 覆盖（`agents/*.md`、`skills/*/*.md` 已含）。安装合同不变：live E2E 使用 wheel 安装到隔离 venv，不从源码树 import；fake 通道继承 conftest 的 `trac` fixture（subprocess 调用 `python -m tracks.cli.main`）。

---

## 3. Ground Truth Method

v0.4 有两项"规则/算法正确性"需独立参考验证：trace 工具的 BS->FR->AC->test 孤儿检测（FR-0080）与 reach 工具的模块级 import 图孤岛检测（FR-0090）。Red 分类（FR-0050）属"简单规则"，以测试数据本身为 ground truth（§3.1 表第 3 行）。

### 3.1. General Principle

**Do not hardcode expected values**. All ground truth is computed by **independent sources** at test runtime:

| Type | Independent Source |
|:---|:---|
| trace 孤儿检测（BS->FR->AC->test 全链双向） | **Manual calculation**: `tests/ground_truth/trace_reference.py` 独立参考实现 |
| reach 孤岛检测（模块级 import 图 BFS） | **Manual calculation**: `tests/ground_truth/reach_reference.py` 独立参考实现 |
| Red 分类（合法/非法 Red） | **The data itself**: 测试 fixture 的失败输出（stderr/returncode）作为单一真相源 |

Key design: ground truth is a **recomputable script**, not a documented fixed value. At test runtime, the same data + ground truth script is called and compared with the framework output.

### 3.2. Ground Truth Isolation (mandatory rule)

1. **Code location**: `tests/ground_truth/trace_reference.py` 与 `tests/ground_truth/reach_reference.py`（shared by unit/integration/e2e）。
2. **Import taboo**: `tests/ground_truth/**/*.py` **must not** `import tracks.*`（CI static check blocks merge on violation）。既有 `discuss_reference.py` 同守此规。
3. **Allowed dependencies**: Only standard library（re/pathlib/ast/json）+ test data files（`tests/assets/`）。
4. **Data access**: Read fixture files directly from `tests/assets/trace_fixtures/` / `tests/assets/reach_fixtures/`，不通过 tracks SDK。
5. **Review ownership**: Ground truth script changes require focused review of semantic consistency with the corresponding AC.

### 3.3. trace_reference.py 验证范围

独立参考实现扫描 fixture story/spec/acceptance + 测试 marker，重算孤儿清单，与 `tracks.checks.trace.check_trace_full` 输出比对：

- 全覆盖 pass（每 BS->FR->AC->test 链完整）-> `status=pass, hard_errors=[]`。
- 正向孤儿：spec FR 无 acceptance AC -> 硬错误（FR ID + spec line:N）。
- 反向孤儿：acceptance AC 回指不存在 FR -> 硬错误（AC ID + acceptance line:N）。
- AC 无 test marker -> 硬错误（AC ID + acceptance line:N）。
- marker 指向不存在 AC -> 硬错误（marker + test file line:N）。
- 短格式 marker（缺 @version）-> 硬错误（test file line:N）。
- BS 无 FR 承接 -> warning（BS ID + story line:N），不改变退出码。
- 重复 FR/AC ID -> 硬错误（冲突双方 line:N）。
- tombstone ID 不计孤儿。
- 讨论块（`>` 行）与 fenced code 内的假 ID 忽略。
- 完整清单不短路，顺序稳定。

### 3.4. reach_reference.py 验证范围

独立参考实现用 `ast` 解析 fixture Python 包，构建模块级 import 图，BFS 计算孤岛，与 `tracks.checks.reach.check_reach` 输出比对：

- 全可达 pass -> `status=pass, islands=[]`。
- 孤岛：从入口不可达的生产模块 -> `islands` 含模块名。
- 纯测试模块（tests/ 下）不计孤岛。
- 无入口声明 -> `status=fail, errors` 非空。
- `__main__.py` 作为入口。
- `[project.scripts]` 入口解析（`name = "module:func"` -> 入口模块）。
- 显式白名单入口（`--entry`）。
- baseline 豁免模块不计孤岛。

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
| C1  | Test environment cannot connect to production opencode/LLM | CI / cross-platform dev machines cannot run real agent paths |
| C2  | Cannot wait for real time | Long agent dispatch cycles infeasible in CI |
| C3  | Cannot mock framework internals | Replacing/patching M-TEST control flow bypasses the behavior under test |

### 6.2. Stance: Controllable vs Mock

- **Replace external dependencies** (controllable): opencode subprocess, LLM provider, real time - these are **external dependencies** of the framework under test and can be replaced with deterministic stand-ins (FakeBackend / fake opencode stand-in).
- **Cannot mock internal implementation**: The framework's own M-TEST state machine, Red classification, trace/reach logic - these are **the object under test** and must not be mocked.

**Boundary iron rule**: Under no circumstances may you replace or bypass the framework's own critical implementation to "make the test pass". If a test finds it must bypass to pass, it means the AC's observability design is wrong; revise interfaces/acceptance instead of patching the test side.

### 6.3. Three-Layer Test Pyramid

| Layer | Name | Time | Speed | Coverage | Default Run |
| ----- | ---- | ---- | ----- | -------- | ----------- |
| L1 | Deterministic sim | Virtual | Seconds | M-TEST state machine, trace/reach pure functions, Red classification, ID grammar, IF- validation | ✅ CI default |
| L2 | Contract sim | Virtual | Seconds | Shield/Prism dispatch protocol (fake opencode stand-in), write scope audit, criteria pack materialization | ✅ CI default |
| L3 | Real env smoke | Real | Real | Real opencode Shield/Prism single dispatch smoke | ❌ nightly/manual |

- **L1 Deterministic sim**: FakeBackend controls Shield/Prism branches via `simulate`; pure functions tested directly with fixtures.
- **L2 Contract sim**: fake opencode stand-in (implements `opencode run --format json` protocol); OpencodeBackend interacts with stand-in, covering materialization/JSON parsing/write audit/failure matrix/criteria pack identity.
- **L3 Real env smoke**: Real opencode + provider, single Shield dispatch (write a test file) + single Prism dispatch (criteria pack review); deselected by default, only runs with real credentials.

### 6.4. Responsibility Contract of Test Infrastructure

| Component | Responsibility (external) | Boundary (what it does not implement) |
| --------- | ------------------------- | ------------------------------------- |
| FakeBackend | Control Shield/Prism outcome via `simulate` | Does not implement real M-TEST logic |
| fake opencode stand-in | Implement `opencode run --format json` protocol; write target files per permission | Does not implement tracks M-TEST state machine or trace/reach logic |
| pytest subprocess (collection/RED_CHECK) | Execute real pytest on host project tests/ | Does not implement Red classification (executor does) |
| trace/reach fixture data | Pinned story/spec/acceptance + test markers / Python packages | Does not implement trace/reach algorithm |

### 6.5. Assertion Basis - Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md §4**:

- Events table (`test.collected`, `red.validated`, `prism.verdict`, `verdict.failed`, `test.committed`, `stage.exited`, `stage.rolled_back`, `outcome.received`).
- CLI output (`trac check trace` / `trac check reach` stdout/stderr/exit code/`--json`).
- File schema (`tests/integration/*.py`, `tests/e2e/*.py`, `.tracks/legacy-baseline.json`, `tracks/agents/Shield.md`, `tracks/skills/test-asset-criteria/SKILL.md`).
- Git state (commits, working tree diff).

If a state needed by an AC has **no** corresponding observable outlet in interfaces.md, this is an observability gap; revise interfaces/acceptance to add the outlet, rather than snooping internal state in the test.

---

## 7. CI Gate

- **Required checks** (architecture.md §4.2 CI 合同):
  1. `lint`：ruff + flake8 + pylint（pre-commit 等价）
  2. `coverage`：`coverage report --fail-under=95`
  3. `test`：`pytest -q -m 'not performance'`（unit + integration + e2e fake 通道）
  4. `deliverables`：`trac check deliverables`（含 Shield.md + criteria pack skill）
  5. `trace`：`trac check trace --json`（待 Devon 实现子命令后激活，foundation task）
  6. `reach`：`trac check reach --json`（待 Devon 实现子命令后激活，foundation task）
- **Validation items**:
  - AC reference closure (each required AC ≥1 test with long-format marker, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3)
  - Coverage ≥95%
  - §3.2 ground truth isolation (`tests/ground_truth/**/*.py` must not `import tracks.*`)
  - Deliverable existence + version + IQ (Shield.md + criteria pack skill)
  - trace 闭合（`trac check trace` 无硬错误）
  - reach 闭合（`trac check reach` 无孤岛）
- **Failure semantics**: Any required check failure blocks merge. live 通道（`tests/e2e_live/`）缺凭据 skip，非 required check。

---

## 8. AC -> 测试层映射

> **Prism [RESOLVED]:** BLOCKER-002 [severity=blocker, artifact=test-plan.md, anchor=§8 行305-487, 关联=FR-0140/AC-IF-closure/BS-12]：test-plan §8「IF- 归属」列将全部 M-TEST 状态机 AC（FR-0010~0070 共 40 条）、Shield 接入 AC（FR-0120 共 6 条）及 M-TEST NFR AC（NFR-0030/0040 共 4 条）——合计 50/90 条 AC——统一归属为 IF-TRACE-001。但 IF-TRACE-001 是 tracks/checks/trace.py:42 定义的桩合同 token（NotImplementedError("IF-TRACE-001 check_trace_full")），专指 check_trace_full 孤儿检测纯函数。M-TEST 状态机 AC 依赖的是 kernel/machine.py _decide_m_test 与 executor/executor.py 的 collect_tests/run_tests/check_trace/commit_tests handler（architecture.md §1.2/§1.3），与 check_trace_full 无关。这导致三个闭合问题：(1) AC->IF->ARC 路径断裂——AC-FR0010-01（M-TEST 进入）经 IF-TRACE-001 指向 checks/trace.py，而非 kernel/machine.py，路径、命令与失败语义不一致；(2) 变绿条件错误——FR-0140 要求「所依赖接口的 IF- 标识，供 M-IMPL task 变绿子集划分」，Devon 实现 check_trace_full 不会使 M-TEST 状态机测试变绿，green condition 语义无效；(3) IF- 标识未在 interfaces.md 定义——IF-TRACE-001/002、IF-REACH-001/002 仅作为 NotImplementedError token 存在于桩代码中，interfaces.md §1a-§1h 未定义这些标识，design-trace validator（check_design_trace）无法对 IF- 标识做有效性校验（只能做存在性检查）。根因：M-TEST 状态机接口（§1a 事件/§1b 命令/§1c 状态）无 IF- 标识定义，test-plan 机械地用 IF-TRACE-001 填充。预期修订：Archer 在 interfaces.md 为 M-TEST 状态机接口定义 IF- 标识（如 IF-MTEST-001 对应 §1a/§1b/§1c 的事件/命令/状态合同，或在 Scaffold 为 machine.py/executor.py M-TEST 新增声明桩并赋 IF- token），并将 test-plan §8 中 50 条 M-TEST 状态机/Shield/NFR AC 的 IF- 归属更新为正确标识；同时确认 design-trace validator 能校验 IF- 标识有效性（非仅存在性）。

> 每个 AC ≥1 测试、每个测试 ≥1 AC（CI 闭合）。跨模块合同（interfaces.md `modules` 列 ≥2）至少一个 integration 测试。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。
>> **Archer:** 已修订，三处闭合问题全部解决：
>> 
>> (1) AC->IF->ARC 路径修复：interfaces.md §5 新增 IF- 标识注册表，定义 8 个标识--IF-MTEST-001（kernel 状态机：§1a reducer + §1b Command 产出 + §1c State + _decide_m_test，实现模块 kernel/machine.py + kernel/events.py）、IF-MTEST-002（executor handler：_do_collect_tests/_do_run_tests/_do_check_trace/_do_commit_tests + classify_red + 判据包物化，实现模块 executor/executor.py）、IF-SHIELD-001（Shield 接入：AGENT_NAME + deliverables + Auditor 写范围，实现模块 effects/opencode.py + deliverables.py）、IF-TRACE-001/002（checks/trace.py，原桩 token 现正式定义）、IF-REACH-001/002（checks/reach.py，原桩 token 现正式定义）、IF-VALIDATE-001（validate.py M-TEST 扩展：check_design_trace IF- 校验 + BS-XX 文法 + @version，实现模块 executor/validate.py + templates/）。architecture.md §1.3 六元组每条 FR 增补 IF-= 字段，AC->IF->ARC 路径连续。
>> 
>> (2) 变绿条件修复：test-plan §8 全部 50 条 M-TEST/Shield/NFR AC 的 IF- 归属从 IF-TRACE-001 更新为正确标识--FR-0010 全部 IF-MTEST-001；FR-0020 按 kernel 路由/Shield 写范围拆分 IF-MTEST-001 + IF-SHIELD-001；FR-0030/0040/0050/0060/0070 按 kernel 路由/executor 执行拆分 IF-MTEST-001 + IF-MTEST-002（FR-0070-02/03/04 trace 相关 AC 另加 IF-TRACE-002）；FR-0120 全部 IF-SHIELD-001；NFR-0030 IF-MTEST-001；NFR-0040 IF-MTEST-001 + IF-MTEST-002。另发现 FR-0130/FR-0140 原 IF-TRACE-002 同类错误（validate.py 扩展不等于 checks/trace.py），已修正为 IF-VALIDATE-001。Devon 实现对应 IF- 标识可使依赖该标识的测试变绿，green condition 语义有效。
>> 
>> (3) IF- 标识有效性校验：interfaces.md §5 定义 design-trace validator 扩展机制--validator 解析 §5 注册表表格第 2 列构建已定义 IF- 标识集合，解析 test-plan §8 每条 integration/e2e AC 的 IF- 归属列，未注册标识判硬错误（line:N IF-id not defined in interfaces.md §5）；缺 IF- 归属判硬错误。unit AC 的 IF- 归属为 informational。architecture.md §5.1 记取舍：IF- 标识粒度对齐增长轴（Devon task 边界）而非函数签名，因 kernel/effects/executor 是既有文件无桩；IF- 标识注册表在 interfaces.md §5 而非桩代码。
>> 
>> 三文档已 trac validate 通过。

「IF- 归属」列的取值只能来自 interfaces.md §5 IF- 标识注册表（IF-MTEST-001 / IF-MTEST-002 / IF-SHIELD-001 / IF-TRACE-001 / IF-TRACE-002 / IF-REACH-001 / IF-REACH-002 / IF-VALIDATE-001）。design-trace validator（`check_design_trace`，FR-0140 扩展）校验：每条 integration/e2e AC 的 IF- 归属非空、且每个 IF- 标识在 interfaces.md §5 已定义（有效性校验，非仅存在性）。粒度对齐 architecture.md §1.1 增长轴（Devon 实现 task 边界）：kernel 状态机 -> IF-MTEST-001；executor handler -> IF-MTEST-002；Shield 接入 -> IF-SHIELD-001；validate.py 扩展 -> IF-VALIDATE-001；trace/reach 工具 -> IF-TRACE-001/002、IF-REACH-001/002。

### 8a. FR-0010 M-TEST 阶段注册与子状态机驱动

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0010-01（M-DESIGN EXIT -> stage.entered(M-TEST), substate=DISPATCH） | unit + integration | test_machine_m_test.py::test_enter_m_test_from_design_exit, test_m_test_cycle.py::test_enter_dispatch | IF-MTEST-001 |
| AC-FR0010-02（trac status 报告 stage=M-TEST + substate；_NEXT_STAGE 接续 + boundary） | unit + e2e | test_machine_m_test.py::test_next_stage_design_to_m_test, test_m_test_journey.py::test_boundary_after_m_test | IF-MTEST-001 |
| AC-FR0010-03（SM-01 转移严格遵循清单；非法转移不产出） | unit + integration | test_machine_m_test.py::test_sm01_transitions_enforced, test_m_test_cycle.py::test_illegal_transition_rejected | IF-MTEST-001 |
| AC-FR0010-04（既有阶段行为不变；M-DESIGN 之前事件前缀稳定） | e2e | test_full_journey.py::test_full_journey_to_boundary（更新：M-DESIGN 前缀稳定 + M-TEST 接续 + boundary 移至 M-TEST 后） | IF-MTEST-001 |
| AC-FR0010-05（显式控制流驱动；kernel 纯函数边界） | unit | test_machine_m_test.py::test_explicit_control_flow + test_kernel_purity_m_test | IF-MTEST-001 |

### 8b. FR-0020 M-TEST WRITE

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0020-01（DISPATCH 按 test-plan 层归属创建 Shield tasks -> WRITE） | unit + integration | test_machine_m_test.py::test_dispatch_creates_shield_tasks, test_shield_dispatch.py::test_dispatch_uses_test_plan_layers | IF-MTEST-001, IF-SHIELD-001 |
| AC-FR0020-02（Shield 写入 tests/integration/、tests/e2e/、tests/assets/、tests/counterexamples/） | integration + e2e | test_shield_dispatch.py::test_shield_writes_test_files, test_m_test_journey.py | IF-SHIELD-001 |
| AC-FR0020-03（可 collect 且全部合法失败的测试文件） | integration | test_shield_dispatch.py::test_collectable_legit_red_tests | IF-SHIELD-001 |
| AC-FR0020-04（validate 失败重派 Shield <=3 -> escalation） | unit + integration | test_machine_m_test.py::test_write_retry_escalation, test_shield_dispatch.py::test_validate_fail_redispatch | IF-MTEST-001 |
| AC-FR0020-05（Shield 不 commit/push/不改产品代码/接口桩；写范围审计回滚） | integration | test_shield_dispatch.py::test_shield_no_commit + test_over_reach_rolled_back | IF-SHIELD-001 |

### 8c. FR-0030 M-TEST COLLECT

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0030-01（Runtime 独立 collection，不读 Shield 自述） | integration | test_m_test_cycle.py::test_collect_independent | IF-MTEST-002 |
| AC-FR0030-02（collection 成功 -> test.collected(passed) -> PRISM_REVIEW；失败 -> WRITE） | unit + integration | test_machine_m_test.py::test_collect_passed_to_prism + test_collect_failed_to_write, test_m_test_cycle.py | IF-MTEST-001 |
| AC-FR0030-03（复跑证据落事件；Shield 自述不构成通过证据） | integration | test_m_test_cycle.py::test_collected_event_evidence | IF-MTEST-002 |

### 8d. FR-0040 M-TEST PRISM_REVIEW

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0040-01（dispatch Prism 按判据包审测试合约 -> PRISM_REVIEW） | unit + integration | test_machine_m_test.py::test_prism_dispatch, test_criteria_pack.py::test_prism_reviews_with_criteria_pack | IF-MTEST-001, IF-MTEST-002 |
| AC-FR0040-02（反自述三件套：assignment 含判据包、verdict 携带 identity、Runtime 回读不匹配判失败） | unit + integration | test_machine_m_test.py::test_criteria_pack_mismatch, test_criteria_pack.py::test_anti_self_report_triple | IF-MTEST-001, IF-MTEST-002 |
| AC-FR0040-03（prism.verdict(pass) -> RED_CHECK；revise -> WRITE） | unit | test_machine_m_test.py::test_prism_pass_to_red + test_prism_revise_to_write | IF-MTEST-001 |
| AC-FR0040-04（revise 必须经 trac discuss 锚定线程；无锚定 -> revise_without_findings） | integration | test_criteria_pack.py::test_revise_without_findings_rejected | IF-MTEST-001 |
| AC-FR0040-05（判据包 skill 内容为语义判据，不含形式校验规则） | unit | test_criteria_pack.py::test_criteria_pack_no_formal_rules（读取 SKILL.md 文本，断言无 marker/ID 文法规则） | IF-MTEST-002 |
| AC-FR0040-06（判据包 skill 物化与回收与 tracks-discuz 同构） | integration | test_criteria_pack.py::test_criteria_pack_materialization_lifecycle | IF-MTEST-002 |

### 8e. FR-0050 M-TEST RED_CHECK

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0050-01（Runtime 独立复跑 integration/e2e；复跑证据落事件） | integration | test_red_check.py::test_red_check_independent_rerun | IF-MTEST-002 |
| AC-FR0050-02（合法 Red 分类：行为断言失败 / 桩 token 失败 / symbol 缺失） | unit + integration | test_red_classifier.py::test_legit_red_classes, test_red_check.py::test_legit_red_validated | IF-MTEST-002 |
| AC-FR0050-03（非法 Red 分类：collection/语法/fixture/import 错误） | unit + integration | test_red_classifier.py::test_illegit_red_classes, test_red_check.py::test_illegit_red_to_diagnose | IF-MTEST-002 |
| AC-FR0050-04（测试意外通过视为非法） | unit + integration | test_red_classifier.py::test_unexpected_pass, test_red_check.py::test_unexpected_pass_to_diagnose | IF-MTEST-002 |
| AC-FR0050-05（全部合法 -> red.validated(valid) -> EXIT；非法/意外通过 -> DIAGNOSE） | unit | test_machine_m_test.py::test_red_valid_to_exit + test_red_invalid_to_diagnose | IF-MTEST-001 |
| AC-FR0050-06（非常规要求：全部意外通过时不退出 M-TEST） | integration | test_red_check.py::test_all_pass_does_not_exit | IF-MTEST-002 |

### 8f. FR-0060 M-TEST DIAGNOSE

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0060-01（DIAGNOSE 在非法 Red/意外通过时进入；不交给 Human） | unit + integration | test_machine_m_test.py::test_diagnose_entered, test_diagnose.py::test_diagnose_no_human | IF-MTEST-001 |
| AC-FR0060-02（test_defect -> WRITE 重派 Shield） | unit + integration | test_machine_m_test.py::test_diagnose_test_defect_to_write, test_diagnose.py::test_test_defect_redispatch | IF-MTEST-001 |
| AC-FR0060-03（stub_gap -> rollback M-DESIGN，不经 Human） | unit + integration | test_machine_m_test.py::test_diagnose_stub_gap_to_design, test_diagnose.py::test_stub_gap_no_human | IF-MTEST-001 |
| AC-FR0060-04（ac_gap -> rollback M-ACC，需 Human 批准） | unit + integration | test_machine_m_test.py::test_diagnose_ac_gap_to_acc, test_diagnose.py::test_ac_gap_needs_human | IF-MTEST-001 |
| AC-FR0060-05（spec_gap -> rollback M-SPEC，需 Human 批准） | unit + integration | test_machine_m_test.py::test_diagnose_spec_gap_to_spec, test_diagnose.py::test_spec_gap_needs_human | IF-MTEST-001 |
| AC-FR0060-06（verdict.failed 携带 classification/target_stage/artifact_disposition） | integration | test_diagnose.py::test_verdict_failed_payload | IF-MTEST-002 |
| AC-FR0060-07（M-IMPL 侧 SHIELD_FIX/DIAGNOSE 不实现，不产出 M-IMPL 事件） | unit | test_machine_m_test.py::test_no_m_impl_events | IF-MTEST-001 |

### 8g. FR-0070 M-TEST EXIT

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0070-01（EXIT 门禁 = collection + 合法 Red + Prism pass + trace 闭合） | integration | test_m_test_exit.py::test_exit_gate_all_conditions | IF-MTEST-002 |
| AC-FR0070-02（trace 闭合：每条 required AC ≥1 长格式 marker、无无主 marker） | integration | test_m_test_exit.py::test_trace_closure_required_acs | IF-MTEST-002, IF-TRACE-002 |
| AC-FR0070-03（trace 统一检查所有 AC；M-TEST 门禁过滤 required AC；non-required 不阻塞） | integration | test_m_test_exit.py::test_trace_filters_required_only | IF-MTEST-002, IF-TRACE-002 |
| AC-FR0070-04（trace 不闭合不退出：无 stage.exited） | integration | test_m_test_exit.py::test_trace_fail_no_exit | IF-MTEST-002, IF-TRACE-002 |
| AC-FR0070-05（trace 复跑失败 -> EXIT->WRITE 重派 Shield <=3 -> escalation） | unit + integration | test_machine_m_test.py::test_trace_fail_to_write, test_m_test_exit.py::test_trace_fail_redispatch | IF-MTEST-001 |
| AC-FR0070-06（M-TEST 无 Human 门禁：退出依据全程序证据，无 human.review/approval） | e2e | test_m_test_journey.py::test_no_human_gate | IF-MTEST-001 |
| AC-FR0070-07（受控测试 commit 冻结测试资产 -> test.committed + stage.exited） | integration + e2e | test_m_test_exit.py::test_test_committed, test_m_test_journey.py | IF-MTEST-002 |
| AC-FR0070-08（stage.exited(M-TEST) -> run.completed(boundary)） | e2e | test_m_test_journey.py::test_boundary_after_m_test | IF-MTEST-001 |

### 8h. FR-0080 trac check trace

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0080-01（解析三文档 + 测试 marker，全链双向孤儿检测；可独立 CLI） | unit + integration + ground_truth | test_check_trace.py::test_full_chain, test_check_trace_cli.py::test_independent_cli, ground_truth trace_reference.py | IF-TRACE-002 |
| AC-FR0080-02（FR<->AC 硬错误：无 AC 的 FR、回指不存在 FR 的 AC） | unit + ground_truth | test_check_trace.py::test_fr_ac_hard_errors, trace_reference.py | IF-TRACE-002 |
| AC-FR0080-03（AC<->test 硬错误：无 marker 的 AC、marker 指向不存在 AC） | unit + ground_truth | test_check_trace.py::test_ac_test_hard_errors, trace_reference.py | IF-TRACE-002 |
| AC-FR0080-04（BS->FR warning：不改变退出码） | unit | test_check_trace.py::test_bs_fr_warning_only | IF-TRACE-002 |
| AC-FR0080-05（孤儿清单完整列出，不短路） | unit + ground_truth | test_check_trace.py::test_no_short_circuit, trace_reference.py | IF-TRACE-002 |
| AC-FR0080-06（短格式 marker 触发失败并指出位置） | unit + integration | test_check_trace.py::test_short_format_rejected, test_check_trace_cli.py::test_short_format_in_cli | IF-TRACE-002 |
| AC-FR0080-07（长格式 marker 引用不存在 AC -> NOT_FOUND，不静默回退） | unit | test_check_trace.py::test_not_found_no_silent_fallback | IF-TRACE-002 |
| AC-FR0080-08（重复 ID -> 失败并指出冲突双方 line:N） | unit | test_check_trace.py::test_duplicate_ids | IF-TRACE-002 |
| AC-FR0080-09（tombstone ID 不计孤儿） | unit | test_check_trace.py::test_tombstone_not_orphan | IF-TRACE-002 |
| AC-FR0080-10（统一检查所有 AC；M-TEST 门禁自行过滤 required AC） | integration | test_m_test_exit.py::test_trace_filters_required_only（与 AC-FR0070-03 共用） | IF-TRACE-002 |
| AC-FR0080-11（引擎可当 verdict 来源调用：读取退出码 + --json） | integration | test_m_test_exit.py::test_trace_as_verdict_source | IF-TRACE-002 |

### 8i. FR-0090 trac check reach

> **Prism [RESOLVED]:** BLOCKER-003 [severity=blocker, artifact=test-plan.md, anchor=§8i 行413（FR-0090），波及 §8j/§8k/§8o/§8p，关联=FR-0140/BS-12/FR-0090/FR-0100/FR-0110/NFR-0010/NFR-0020]：test-plan §8 全部 reach 相关 integration AC 的 IF- 归属为 IF-REACH-001（check_reach 纯函数），但 integration 测试经 CLI（test_check_reach_cli.py）调用 check_reach_file（IF-REACH-002），Devon 仅实现 IF-REACH-001 不会使 CLI integration 测试变绿--check_reach_file 仍 raise NotImplementedError("IF-REACH-002")。具体：(1) FR-0090 integration AC（AC-FR0090-01/04/06，layer 含 integration，测试 test_check_reach_cli.py::*）IF-=IF-REACH-001，应为 IF-REACH-002（或 IF-REACH-001+IF-REACH-002）；(2) FR-0100/0110、NFR-0010/0020 的 reach integration AC 同样标 IF-REACH-001，应含 IF-REACH-002。对照 trace 侧（FR-0080）integration AC 正确使用 IF-TRACE-002（file wrapper）--因 check_trace_full_file 调用 check_trace_full，实现 IF-TRACE-002 隐含实现 IF-TRACE-001；reach 侧反向：check_reach_file 调用 check_reach，实现 IF-REACH-001 不隐含 IF-REACH-002。三文档不一致：architecture.md §1.3 六元组 FR-0090 写 IF-=IF-REACH-001/IF-REACH-002、FR-0100/0110 写 IF-=IF-TRACE-002/IF-REACH-002；interfaces.md §5 IF-REACH-002 关联 FR 列含 FR-0090-01/FR-0100/FR-0110/NFR-0010/NFR-0020；但 test-plan §8 仅写 IF-REACH-001。预期修订：将 test-plan §8i/§8j/§8k/§8o/§8p 中 layer 含 integration 的 reach AC 的 IF- 归属更新为 IF-REACH-002（或 IF-REACH-001+IF-REACH-002），与 trace 侧 IF-TRACE-002 模式对称，使变绿条件（BS-12/FR-0140）语义有效。
>> **Archer:** 已修订：将 test-plan §8i/§8j/§8k/§8o/§8p 中全部 layer 含 integration 的 reach AC 的 IF- 归属从 IF-REACH-001 更新为 IF-REACH-002，与 trace 侧 IF-TRACE-002 模式对称。具体变更（14 条 integration AC）：§8i AC-FR0090-01/04/06；§8j AC-FR0100-01/02/04/05；§8k AC-FR0110-01/02/03；§8o AC-NFR0010-01/02；§8p AC-NFR0020-01/02。另将 §8j 两条 baseline unit AC（AC-FR0100-03/06）从 IF-REACH-001 更新为 IF-REACH-002：LegacyBaseline schema 按 interfaces.md §5 属于 IF-REACH-002（file wrapper 读取基线），非 IF-REACH-001（纯函数接收 baseline dict 参数）；trace 侧同 AC 已用 IF-TRACE-002，reach 侧对称修正。保留 IF-REACH-001 的 3 条 AC（AC-FR0090-02/03/05）均为 unit-only、直接测试 check_reach 纯函数，归属正确。变绿条件（BS-12/FR-0140）语义现在有效：Devon 实现 IF-REACH-002 使 CLI integration 测试变绿（check_reach_file 不再 raise NotImplementedError），实现 IF-REACH-001 使纯函数 unit 测试变绿。三文档 trac validate 通过。

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0090-01（从声明入口点构建模块级 import 图；可独立 CLI） | unit + integration + ground_truth | test_check_reach.py::test_build_import_graph, test_check_reach_cli.py::test_independent_cli, ground_truth reach_reference.py | IF-REACH-002 |
| AC-FR0090-02（报告不可达的生产模块孤岛） | unit + ground_truth | test_check_reach.py::test_islands_reported, reach_reference.py | IF-REACH-001 |
| AC-FR0090-03（纯测试模块不计入，不产生误报） | unit | test_check_reach.py::test_test_modules_excluded | IF-REACH-001 |
| AC-FR0090-04（无入口声明时报错，非零退出） | unit + integration | test_check_reach.py::test_no_entrypoints_error, test_check_reach_cli.py::test_no_entries_fail | IF-REACH-002 |
| AC-FR0090-05（只做模块级 import 图，不做函数级调用图） | unit | test_check_reach.py::test_no_function_level_graph | IF-REACH-001 |
| AC-FR0090-06（可被引擎当 verdict 来源调用） | integration | test_check_reach_cli.py::test_verdict_source | IF-REACH-002 |

### 8j. FR-0100 存量基线豁免

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0100-01（trace 与 reach 提供同一份存量基线豁免清单；.tracks/ 下声明文件） | integration | test_baseline_exemption.py::test_baseline_file_schema | IF-TRACE-002, IF-REACH-002 |
| AC-FR0100-02（基线内文档/编号 trace 不计孤儿；基线内模块 reach 不计孤岛） | integration + ground_truth | test_baseline_exemption.py::test_trace_baseline_exemption + test_reach_baseline_exemption, trace_reference.py, reach_reference.py | IF-TRACE-002, IF-REACH-002 |
| AC-FR0100-03（基线只冻结采纳时刻存量，不回填历史） | unit | test_baseline.py::test_baseline_freezes_adoption_only | IF-TRACE-002, IF-REACH-002 |
| AC-FR0100-04（基线后新增同形内容仍正常报错） | integration | test_baseline_exemption.py::test_new_content_still_reported | IF-TRACE-002, IF-REACH-002 |
| AC-FR0100-05（不强制重编号/补链路；工具只报告不改写） | integration | test_baseline_exemption.py::test_no_auto_fix（git 工作区无变化，NFR-0010） | IF-TRACE-002, IF-REACH-002 |
| AC-FR0100-06（schema 由 Archer 裁定，FR 锁定语义与范围） | unit | test_baseline.py::test_schema_fields | IF-TRACE-002, IF-REACH-002 |

### 8k. FR-0110 双格式输出与稳定退出码

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0110-01（trace 与 reach 均支持人类可读 + --json） | integration | test_check_trace_cli.py::test_json_output + test_human_readable_output, test_check_reach_cli.py::test_json_output + test_human_readable_output | IF-TRACE-002, IF-REACH-002 |
| AC-FR0110-02（退出码稳定：0=通过、非0=有硬错误；多次运行一致） | integration | test_check_trace_cli.py::test_exit_code_stable, test_check_reach_cli.py::test_exit_code_stable | IF-TRACE-002, IF-REACH-002 |
| AC-FR0110-03（CLI 与引擎双消费者：人类读默认格式、引擎读 --json + 退出码） | integration | test_check_trace_cli.py::test_engine_consumer, test_check_reach_cli.py::test_engine_consumer | IF-TRACE-002, IF-REACH-002 |

### 8l. FR-0120 Shield opencode agent 接入与写范围审计

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0120-01（AGENT_NAME 增补 shield->Shield；物化与回收同构） | unit + integration | test_deliverables.py::test_shield_agent_name, test_shield_dispatch.py::test_materialization_lifecycle | IF-SHIELD-001 |
| AC-FR0120-02（Shield.md 加入 deliverables 一致性集合） | unit | test_deliverables.py::test_shield_in_deliverables（既有 test 扩展） | IF-SHIELD-001 |
| AC-FR0120-03（Shield 写范围仅限四目录） | integration | test_shield_dispatch.py::test_write_scope_four_dirs | IF-SHIELD-001 |
| AC-FR0120-04（越权写被审计检出并 git 回滚：over_reach failure_class） | integration | test_shield_dispatch.py::test_over_reach_rolled_back | IF-SHIELD-001 |
| AC-FR0120-05（Shield 不写产品代码/接口桩/ground_truth/设计文档） | integration | test_shield_dispatch.py::test_no_product_code_writes | IF-SHIELD-001 |
| AC-FR0120-06（Shield 读 test-plan/interfaces/acceptance + 桩 -> 写测试 -> 自检 -> outcome） | integration + e2e | test_shield_dispatch.py::test_shield_workflow, test_m_test_journey.py | IF-SHIELD-001 |

### 8m. FR-0130 ID 文法与跨版本引用进模板

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0130-01（三模板增补 ID 文法：BS-XX / FR-XXXX / NFR-XXXX / AC-FRXXXX-YY） | unit + integration | test_id_grammar.py::test_bs_grammar + test_fr_grammar + test_ac_grammar, test_id_grammar_cli.py::test_validate_story_bs | IF-VALIDATE-001 |
| AC-FR0130-02（ID 不可变/不可复用；tombstone 不计孤儿） | unit | test_id_grammar.py::test_id_immutable + test_tombstone | IF-VALIDATE-001 |
| AC-FR0130-03（跨版本限定引用 @version opt-in；解析失败 NOT_FOUND） | unit | test_id_grammar.py::test_version_qualified_reference | IF-VALIDATE-001 |
| AC-FR0130-04（文档短/长格式均允许；测试 marker 必须长格式） | unit + integration | test_id_grammar.py::test_doc_short_long_allowed, test_check_trace.py::test_test_marker_must_be_long | IF-VALIDATE-001 |
| AC-FR0130-05（trac validate 校验编号文法与跨版本引用） | integration | test_id_grammar_cli.py::test_validate_rejects_bad_grammar | IF-VALIDATE-001 |
| AC-FR0130-06（既有 check_spec_items 行为不回归） | unit | test_id_grammar.py::test_existing_spec_validation_unchanged（既有 test_trace.py 子集） | IF-VALIDATE-001 |

### 8n. FR-0140 test-plan 变绿条件字段与 design-trace IF- 校验

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-FR0140-01（test-plan 模板增补变绿条件字段） | unit | test_green_condition.py::test_template_has_green_condition_field | IF-VALIDATE-001 |
| AC-FR0140-02（check_design_trace 扩展：校验每条 integration/e2e 的 IF- 归属） | unit | test_green_condition.py::test_check_design_trace_if_attribution | IF-VALIDATE-001 |
| AC-FR0140-03（trac validate --file test-plan.md 校验变绿条件与 IF- 归属） | integration | test_green_condition_cli.py::test_validate_test_plan_if | IF-VALIDATE-001 |
| AC-FR0140-04（绿的粒度约束 M-IMPL 设计，本 release 只携字段不实现变绿执行） | unit | test_green_condition.py::test_field_carries_if_not_execution | IF-VALIDATE-001 |

### 8o. NFR-0010 trace/reach 工具只报告不改写

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-NFR0010-01（运行前后文件内容无变化） | integration | test_check_trace_cli.py::test_no_file_changes + test_check_reach_cli.py::test_no_file_changes | IF-TRACE-002, IF-REACH-002 |
| AC-NFR0010-02（工具只报告，不做自动修复/重编号） | integration | test_check_trace_cli.py::test_no_auto_fix + test_check_reach_cli.py::test_no_auto_fix | IF-TRACE-002, IF-REACH-002 |

### 8p. NFR-0020 trace/reach 输出确定性与幂等

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-NFR0020-01（同一输入多次运行输出完全一致，字节级） | integration | test_check_trace_cli.py::test_output_deterministic + test_check_reach_cli.py::test_output_deterministic | IF-TRACE-002, IF-REACH-002 |
| AC-NFR0020-02（无随机顺序/时间戳/环境漂移；清单顺序稳定） | unit + integration | test_check_trace.py::test_stable_order, test_check_reach_cli.py::test_stable_order | IF-TRACE-002, IF-REACH-002 |

### 8q. NFR-0030 M-TEST 控制流维持 kernel 纯函数边界

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-NFR0030-01（decide()/project() 不碰 IO/clock/env/文件系统） | unit | test_machine_m_test.py::test_kernel_purity_no_io | IF-MTEST-001 |
| AC-NFR0030-02（副作用归 executor；drop 投影表后重建一致） | unit + integration | test_machine_m_test.py::test_rebuild_from_events, test_m_test_cycle.py::test_drop_rebuild | IF-MTEST-001, IF-MTEST-002 |

### 8r. NFR-0040 M-TEST 事件维持 append-only 事件溯源

| AC | 层 | 测试 | IF- 归属 |
|:---|:---|:---|:---|
| AC-NFR0040-01（M-TEST 全程事件 append-only，不改写既有行） | integration | test_m_test_cycle.py::test_events_append_only | IF-MTEST-001, IF-MTEST-002 |
| AC-NFR0040-02（投影表可从事件完整重建） | integration | test_m_test_cycle.py::test_rebuild_projections | IF-MTEST-001, IF-MTEST-002 |

---

## 9. SM-01 转移覆盖清单（NFR-0040，normative 依据 SPEC-004「状态与生命周期」）

> 每条转移 ≥1 测试走到一次；清单内测试须存在且通过（机器化 trace 工具属本 release 交付，合入前 trac check trace 核对）。测试列为**计划落点**（file::case 前缀），实现时可加后缀细分但不得留空行缺口。

| 转移 | 内容摘要 | 层 | 测试 |
|:---|:---|:---|:---|
| SM-01.1 | M-DESIGN EXIT -> stage.entered(M-TEST) -> DISPATCH | unit + e2e | test_machine_m_test.py::test_enter_m_test, test_m_test_journey.py |
| SM-01.2 | DISPATCH -> WRITE：tasks 就绪 | unit + integration | test_machine_m_test.py::test_dispatch_to_write, test_shield_dispatch.py |
| SM-01.3 | WRITE -> COLLECT：Shield outcome（测试文件写入） | integration | test_shield_dispatch.py::test_write_to_collect |
| SM-01.4 | WRITE -> WRITE：validate 失败重派 Shield（<=3 升级） | unit + integration | test_machine_m_test.py::test_write_retry, test_shield_dispatch.py::test_validate_fail_redispatch |
| SM-01.5 | COLLECT -> PRISM_REVIEW：collection 成功 | unit + integration | test_machine_m_test.py::test_collect_to_prism, test_m_test_cycle.py |
| SM-01.6 | COLLECT -> WRITE：collection 失败回 WRITE | unit + integration | test_machine_m_test.py::test_collect_fail_to_write |
| SM-01.7 | PRISM_REVIEW -> RED_CHECK：prism.verdict(pass)（判据包 identity 匹配） | unit + integration | test_machine_m_test.py::test_prism_pass_to_red, test_criteria_pack.py |
| SM-01.8 | PRISM_REVIEW -> WRITE：prism.verdict(revise)（经 trac discuss 锚定） | unit + integration | test_machine_m_test.py::test_prism_revise_to_write, test_criteria_pack.py::test_revise_without_findings_rejected |
| SM-01.9 | RED_CHECK -> EXIT：全部合法 Red | unit + integration | test_machine_m_test.py::test_red_valid_to_exit, test_red_check.py |
| SM-01.10 | RED_CHECK -> DIAGNOSE：非法 Red 或意外通过 | unit + integration | test_machine_m_test.py::test_red_invalid_to_diagnose, test_red_check.py |
| SM-01.11 | DIAGNOSE -> WRITE：测试缺陷 -> Shield 重派 | unit + integration | test_machine_m_test.py::test_diagnose_test_defect, test_diagnose.py |
| SM-01.12 | DIAGNOSE -> stage.rolled_back(M-DESIGN)：桩/接口缺口（不经 Human） | unit + integration | test_machine_m_test.py::test_diagnose_stub_gap, test_diagnose.py::test_stub_gap_no_human |
| SM-01.13 | DIAGNOSE -> stage.rolled_back(M-ACC/M-SPEC)：AC/Spec 缺口（Human 批准后） | unit + integration | test_machine_m_test.py::test_diagnose_ac_gap + test_diagnose_spec_gap, test_diagnose.py::test_ac_gap_needs_human + test_spec_gap_needs_human |
| SM-01.14 | EXIT -> stage.exited(M-TEST) -> run.completed(boundary)：trace 闭合 + 测试资产冻结 | integration + e2e | test_m_test_exit.py::test_exit_to_boundary, test_m_test_journey.py |
| SM-01.15 | EXIT -> WRITE：trace 闭合复跑失败，重派 Shield 修 marker/绑定（<=3 升级） | unit + integration | test_machine_m_test.py::test_trace_fail_to_write, test_m_test_exit.py::test_trace_fail_redispatch |
| 休眠回放 | M-TEST 各子状态休眠后事件回放恢复 | integration | test_m_test_cycle.py::test_replay_recovery |

---

## 10. 既有测试更新（planned changes，AC-FR0010-04）

v0.4 的 boundary 从 M-DESIGN->M-IMPL 移至 M-TEST->M-IMPL，影响既有 e2e 测试：

| 既有测试 | 变更 | 理由 |
|:---|:---|:---|
| `tests/e2e/test_full_journey.py::test_full_journey_to_boundary` | 更新：M-DESIGN EXIT 后断言 `stage.entered(M-TEST)`（而非 `run.completed`）；最终 boundary 终态移至 M-TEST 后；M-DESIGN 之前事件前缀逐字节稳定 | SM-01.1：M-DESIGN EXIT -> M-TEST（不再 run.completed） |
| `tests/e2e/test_full_journey.py::test_prism_revise_drives_a_second_review_round` | 更新：M-DESIGN Prism revise 后仍进入 M-TEST（而非直接 run.completed） | M-DESIGN EXIT 后接 M-TEST |
| `tests/e2e/test_full_journey.py::test_walk_to_design_complete` | 更新：helper 名称/行为调整（design complete 后不再 terminal，需继续 M-TEST） | boundary 移动 |
| `tests/e2e/test_full_journey.py::test_bounded_walk_matches_harness_step_boundaries` | 更新：增加 M-TEST 步骤（DISPATCH/WRITE/COLLECT/PRISM_REVIEW/RED_CHECK/EXIT） | M-TEST 接入 bounded walk |
| `tests/unit/test_machine_design.py` | 更新：M-DESIGN EXIT 后断言 `stage.entered(M-TEST)`（而非 `run.completed`） | _NEXT_STAGE 增补 M-DESIGN->M-TEST |

> AC-FR0010-04 要求"M-DESIGN 之前事件前缀逐字节稳定"：上述更新的断言只触及 M-DESIGN EXIT 及之后的Events，M-DESIGN 之前的事件序列不变。

---

## 11. e2e Happy Path 范围（test_m_test_journey.py）

e2e 仅覆盖 happy path（主成功旅程），边界/错误情形归入 integration：

**M-TEST happy path（fake 通道）**：

```
[M-DESIGN EXIT 已完成]
-> stage.entered(M-TEST) -> DISPATCH
-> dispatch Shield 写 integration/e2e 测试（fake: 测试文件落盘）
-> WRITE -> COLLECT：collection 成功
-> PRISM_REVIEW：Prism verdict(pass)（判据包 identity 匹配）
-> RED_CHECK：全部合法 Red（fake: 测试因桩 NotImplementedError 失败）
-> EXIT：trac check trace 闭合
-> commit_tests：受控测试 commit
-> stage.exited(M-TEST) -> run.completed(boundary)
```

**断言**：
- 事件流按 SM-01 序列依次出现各子状态。
- 无 `human.review`/`human.approval` 在 M-TEST 期间插队（BS-05）。
- `trac status` 报告 `stage=M-TEST` + 当前子状态。
- 最终 `run.completed(terminal_state="boundary")`。
- git log 含受控测试 commit。
- 测试文件存在于 `tests/integration/` 与 `tests/e2e/`。

**非 happy path（归入 integration）**：
- WRITE validate 失败重派 -> test_shield_dispatch.py
- COLLECT 失败回 WRITE -> test_m_test_cycle.py
- PRISM revise -> test_criteria_pack.py
- 非法 Red / 意外通过 -> test_red_check.py / test_diagnose.py
- trace 不闭合 -> test_m_test_exit.py
- DIAGNOSE 四路路由 -> test_diagnose.py
