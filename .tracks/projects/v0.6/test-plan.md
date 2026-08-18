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
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表（v0.6 新增 IF-HOTFIX-001～009，并继承 v0.4/v0.5 注册表）。

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

> **Prism:** [PRISM-V06-09][blocker][defect_classification=test_defect] test-plan 承诺的 tests/e2e_live/test_hotfix_live.py 未交付：§2.1 布局（本行）、§2.3 Live selection 命令（.venv/bin/python -m pytest -q -rs tests/e2e_live/test_hotfix_live.py）、§2.5「v0.6 的 live 增量（test_hotfix_live.py）在同一隔离纪律下运行」、§6.3 L3、§1.4.2 taboo 例外（v0.5 既有 + v0.6 hotfix 探针）五处规范性引用该文件，当前树不存在（ls tests/e2e_live/ 无 hotfix 探针）——§2.3 命令将 file-not-found。§7 待实现项只把 CI job 步骤列为 Devon foundation task，探针文件本身按 §1.5 归 Shield 编写，属本轮 M-TEST 交付缺口。修订预期：Shield 补写探针（§6.3 L3 合同：probe GITHUB_TOKEN + TRAC_GITHUB_REPO + TRAC_LIVE_ISSUE；缺失 → stdout 精确含 LIVE_SKIPPED: missing <NAME> 且 exit 0 skip、不产 evidence；齐备 → GithubBackend.fetch_issue 真实读取 + precheck_hotfix 对真实 issue 判定，不建 run、不写库；§2.5 隔离纪律：断言 import path 不在源码树、不设 TRAC_FAKE_SIMULATE、不传 --assignment-overlay）。

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
| AC-FR0243-01 | integration | tests/integration/test_hotfix_design_delta.py::test_mdesign_delta_docs_in_hotfix_dir_with_inherited_contracts | IF-HOTFIX-005, IF-HOTFIX-006 |
| AC-FR0243-02 | integration | tests/integration/test_hotfix_design_delta.py::test_delta_design_carries_anchor_set_and_prism_review | IF-HOTFIX-002, IF-HOTFIX-009 |
| AC-FR0243-03 | integration | tests/integration/test_hotfix_design_delta.py::test_prism_anchor_overturn_routes_back_to_sage_triage | IF-HOTFIX-002, IF-HOTFIX-009 |
| AC-FR0244-01 | integration | tests/integration/test_hotfix_mtest.py::test_mtest_regression_red_first_then_green | IF-HOTFIX-007, IF-MTEST-002 |
| AC-FR0244-02 | integration | tests/integration/test_hotfix_mtest.py::test_delta_testplan_layer_ownership_validated | IF-HOTFIX-007, IF-VALIDATE-001 |
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

> **Prism:** [PRISM-V06-08][blocker][defect_classification=test_defect] AC-FR0248-01 的 ac_gap 触发 token 惰性：tests/integration/test_hotfix_routes.py:111 使用 simulate=devon:DIAGNOSE=ac_gap，但 DIAGNOSE 是 Prism 派发子状态（tracks/kernel/m_impl.py:465 dispatch Prism；tracks/effects/fake.py:50-56 _PRISM_REVIEW_SUBSTATES 含 DIAGNOSE，token 键按 role:substate 构造），任何合规实现（IF-IMPL-001 复用、M-IMPL 21 子状态封闭集不变）都不会产生 role=devon+substate=DIAGNOSE 的派发 → token 永不命中；且 happy path 无 gate 失败，旅程根本不进入 DIAGNOSE → awaiting=escalation(check=ac_gap) 不可达 → trac approve 拒绝（run not awaiting approval or escalation，cli/main.py:684）→ :117-118 assert approve.returncode == 0 永久 Red。既有机制的合法注入是「失败触发 + diagnose:classification=ac_gap」（v0.4 tests/integration/test_diagnose.py:69 同款：shield:WRITE=illegit_red,diagnose:classification=ac_gap）。另 :128-129 断言 test.committed/green.committed 全流缺席与 :109 注释「Drive M-IMPL until Devon surfaces」矛盾——M-IMPL 段触发前 RGR green.committed 必已存在；应改为 M-TEST 段触发（v0.4 模式，全流缺席自然成立）或仅断言退出路由之后无后续 M-TEST/M-IMPL 活动事件。修订预期：simulate 改为失败触发 + diagnose:classification=ac_gap（具体失败点按 v0.6 fake 合同选择，M-TEST 段最贴近既有模式），保持 human.approval → backlog.recorded → run.completed(ac_gap) 与「不自动继续」断言。

> **Prism:** [PRISM-V06-07][blocker][defect_classification=test_defect] AC-FR0245-02 stale 触发时序错位：tests/integration/test_hotfix_mimpl.py:80-82 先以 4 次无上限 trac run 推进旅程——按既有 run_loop 语义（tracks/executor/executor.py:271-294，_is_phase_boundary 仅 stub_gap 回滚；v0.5 tests/e2e/helpers.py walk_to_m_test_complete 实证单次 run 即达 run.completed(boundary)），首次调用已把 M-DESIGN→M-TEST→M-IMPL 推到 boundary；:91-99 随后才用 plumbing 推进 releases/v0.6，此刻已无活跃 M-IMPL 检查点，baseline.frozen(status=stale) 与 needs_attention 永不出现 → :110 assert stale 与 :114 assert needs_attention 在合规 runtime 下永久 Red（永续性判据；PRISM-V06-02 修复了推进对象但未修复时序）。修订预期：让 run 停在 M-IMPL 中段再推进分支——用 trac run --max-dispatches K（cli/main.py:284 既有支持）计数停在 M-IMPL 内（活跃非终态），或注入失败/hang token 使 run 驻留；随后 plumbing 推进 releases/v0.6，再次 trac run 恢复时触发 IF-HOTFIX-008 stale reconcile（FR-0245-02 要求该路径可达），stale/needs_attention 断言语义即成立。

> **Prism [RESOLVED]:** [PRISM-V06-06][blocker][defect_classification=test_defect] 重建/恢复测试以「hotfix」字面量断言 status，但该字面量不在任何合同出口中，合规 runtime 下永久 Red。tests/integration/test_hotfix_events.py:71 与 :85（test_hotfix_events_append_only_and_projection_rebuild，AC-NFR0100-04）、tests/integration/test_hotfix_recovery.py:34 与 :59（test_crash_recovery_resumes_precise_hotfix_state，AC-NFR0110-03）断言 assert "M-HOTFIX-TRIAGE" in status.stdout or "hotfix" in status.stdout.lower()。这两个测试用 issue 42 + 默认 simulate（fake Sage 锚定通过），断言时 run 已同步完成 triage 到 stage=M-DESIGN；合同下此刻 status 的 hotfix 扩展行只追加 branch=fix/{N} scenario={s} issue={N}（interfaces §2b）——「stage=M-HOTFIX-TRIAGE」仅入口子状态机活跃期间、「hotfix」字面量仅 awaiting 行 origin=hotfix-triage（§2a #1），M-DESIGN 行两者皆无；run_id 沿用既有 ULID 机制（cli/main.py:184 new_ulid()，IF-HOTFIX-006 §0「active_run 语义不变」），id 不含 hotfix 字样。最小合规实现下四处断言均永久 Red（永续性判据：不要求 runtime 输出合同未约定的字面量才能绿）。修订预期：以 §2b 合同字段断言 hotfix 身份——如 assert "scenario=post-release" in status.stdout and "branch=fix/42"；对 NFR-0100-04/NFR-0110-03 的「投影重建/恢复后状态一致」语义更强且更贴 AC 的写法是比较重建前后两次 status 输出的一致性（对 run 行做相等断言）；或改用 issue 77 + no_anchor simulate 驻留 AWAIT_HUMAN 后断言合同约定的 awaiting=awaiting_human origin=hotfix-triage 行。
>> **Shield:** 已修订（PRISM-V06-06）：test_hotfix_events.py::test_hotfix_events_append_only_and_projection_rebuild 与 test_hotfix_recovery.py::test_crash_recovery_resumes_precise_hotfix_state 移除「hotfix」/「M-HOTFIX-TRIAGE」字面量断言，改为 interfaces §2b 合同字段断言（branch=fix/42 + scenario=post-release），并对两次 status 的 fix/42 运行行做前后相等断言（NFR-0100-04 投影重建 / NFR-0110-03 崩溃恢复的「状态一致」语义）。已用 cli_hotfix_no_events.patch 重验 killed（assert 'branch=fix/42' in 'no runs yet'），反向恢复后仍为合法 Red（USAGE / exit 1）。kill-manifest.json 对应条目 verification 已更新。

> **Prism [RESOLVED]:** [PRISM-V06-05][blocker][defect_classification=test_defect] AC-FR0240-06 测试的第二次 hotfix 入口在合规 runtime 下必被 REJECTED。tests/integration/test_hotfix_await_human.py:43-45（test_human_anchor_manual_and_feature_route）先执行子动作 1：trac hotfix anchor AC-FR0030-01@v0.5——按 interfaces §2a #4「通过则 complete_hotfix_entry」，该子动作内即创建 fix/77 分支 + checkout + stage.entered(M-DESIGN)（IF-HOTFIX-005）；随后 :48-50 子动作 2 以同 issue 重新入口（r2 = trac("hotfix","77","--scenario","post-release", simulate=no_anchor)）并 assert r2.returncode == 0 + awaiting=awaiting_human。但此时 fix/77 已存在：PRECHECK P-5/§1c 封闭原因集 fix_branch_exists（防半建 run 串写）→ REJECTED、exit 1，assert r2.returncode == 0 永久 Red（永续性判据，同 PRISM-V05-R2 类）。修订预期（二选一）：(a) 调换顺序——先在 run #1 的 AWAIT_HUMAN 上执行 feature-route 子动作（该路径不建分支、run 终止 feature_route），再对 issue 77 发起第二次入口驱动 manual anchor 路径（fix/77 从未创建，P-5 不触发）；或 (b) 为 manual anchor 与 feature-route 两个子动作使用两个不同的 no-anchor bug issue（如 77 与另一种子 issue）。两路径均保持对 §2a #4/#5 两个子动作与「不自动转 feature」语义的断言。
>> **Shield:** 已修订（PRISM-V06-05）：test_hotfix_await_human.py::test_human_anchor_manual_and_feature_route 调换子动作顺序——先执行 feature-route（不建 fix/77 分支，run 终止 feature_route），再以同 issue 77 发起第二次入口驱动 manual anchor 路径（fix/77 从未创建，P-5 不触发）。两路径均保持对 §2a #4/#5 两个子动作与「不自动转 feature」语义的断言。已用 cli_hotfix_no_events.patch 重验 killed（assert 'awaiting=awaiting_human' in 'hotfix: ok (no events)'），反向恢复后仍为合法 Red。

> **Prism [RESOLVED]:** [PRISM-V06-04][blocker][defect_classification=test_defect] FR-0242/NFR-0110 并存测试的挂起 run fixture 投影了一个 completed run，合同下 suspended/restore 断言永久 Red。tests/hotfix_support.py:142-168（seed_completed_feature_run）为 feature run 写入 stage.entered(M-DESIGN) + run.completed(terminal_state=boundary)——终态 run。受影响断言：tests/integration/test_hotfix_coexist.py:49（test_active_run_selection_and_status_discriminates_runs，AC-FR0242-01：assert "suspended:" in status.stdout）、:79（test_boundary_restores_suspended_feature_run_as_active，AC-FR0242-02/AC-NFR0110-02：assert "feature-run-1" in status.stdout）、:104（test_suspended_run_observable_and_gates_recoverable，AC-FR0242-03/AC-NFR0110-01：assert "suspended:"）。合同依据：interfaces.md §2b 明确「存在其余**非 completed run** 时追加行 suspended:」——completed run 不产生 suspended 行；§1a run.completed(hotfix boundary) 的 restored_active_run: str|None 只承接挂起 run，boundary 恢复的对象是挂起（非终态）feature run（AC-FR0242-02「挂起的 feature run 恢复为 active」），终态 run 无从挂起也无从恢复。合规 runtime 下这三处断言永久 Red（判据：断言所依赖的仓库安排必须与合规 runtime 真实产物位置一致）。修订预期：fixture 改为投影一个**进行中**的 feature run（如仅 stage.entered(M-DESIGN)、不写 run.completed，使其成为可被 hotfix 挂起、boundary 后可恢复的合同有效对象），三处断言语义即可成立；无需改动各测试的驱动与断言结构。
>> **Shield:** 已修订（PRISM-V06-04）：hotfix_support.py::seed_completed_feature_run 重命名为 seed_inprogress_feature_run，移除 run.completed 事件，仅保留 stage.entered(M-DESIGN)——使该 feature run 在合同下成为可挂起对象（非终态 run 产生 suspended 行、boundary 后可恢复）。test_hotfix_coexist.py 三处断言（suspended: / feature-run-1 / terminal=boundary）语义即成立，无需改动断言结构。已用 cli_hotfix_no_events.patch 重验 killed（assert 'branch=fix/42' in 'run=feature-run-1: stage=M-DESIGN...' / assert 'terminal=boundary' / assert 'suspended:'），反向恢复后仍为合法 Red。

> **Prism [RESOLVED]:** [PRISM-V06-03][blocker][defect_classification=test_defect] AC-FR0245-01 隔离断言降级：tests/integration/test_hotfix_mimpl.py:40-43（test_mimpl_commits_isolated_on_fix_branch）把「活跃分支不含修复提交」断言成消息子串检查——assert "fix" not in main_log.lower() or "fix/42" not in main_log 析取后实际只检查 main 的 oneline 文本不含 fix/42 字符串，而非提交拓扑（SHA 可达性）。任何把修复提交落到 main 且提交消息不含 fix/42 字样的偏差实现都能存活；interfaces §4c 出口语义是 git history 拓扑（git log fix/{N} 含修复提交、活跃分支不含这些提交）。同函数 :40 的 trailer 断言 assert "Tracks-Issue=42" in body or "Tracks-Issue" in body 第二析取项吞没第一项，实际只检查 Tracks-Issue 存在、不绑定 =42（§4a：trailers Tracks-Issue=hotfix issue）。修订预期：以 SHA 集合断言隔离——例如 git rev-list --format=%H fix/42 ^main 得到的修复提交集与 git rev-list main 得到的提交集不相交（或断言 git cherry main fix/42 输出全为 +，且 fix_log 非空）；trailer 断言收紧为 Tracks-Issue=42 精确匹配。
>> **Shield:** 已修订 test_hotfix_mimpl.py::test_mimpl_commits_isolated_on_fix_branch（PRISM-V06-03）：trailer 断言收紧为精确匹配 Tracks-Issue=42；隔离断言改为拓扑断言 git cherry main fix/42 全为 + 且非空。已绑定 counterexample hotfix_fix_commit_leaks_to_main.patch（相同 patch 落到 main，不同 parent 同 patch-id）：验证 killed（cherry 显示 '-'），反向恢复后仍为合法 Red（USAGE）。

> **Prism [RESOLVED]:** [PRISM-V06-02][blocker][defect_classification=test_defect] AC-FR0245-02 永续性缺陷：tests/integration/test_hotfix_mimpl.py:73-76（test_scenario_b_baseline_stale_reconcile_needs_attention）在 host_repo 直接 git add/commit marker.txt 以「推进活跃 release 分支 HEAD」，但此时工作树已按 IF-HOTFIX-005 冻结合同 checkout 到 fix/50（complete_hotfix_entry = create_branch + checkout，§1d/§1a），该提交落在 fix/50 而非 releases/v0.6。场景 B 的 stale 检测输入是活跃 release 分支 HEAD SHA（interfaces §1g：git rev-parse {active_branch}），此输入不变 → baseline.frozen(status=stale) 永不触发 → :85 assert stale 在合规实现下永久 Red（判据：断言所依赖的仓库安排必须与合规 runtime 真实产物位置一致）。修订预期：真正推进 releases/v0.6 HEAD——先 git checkout releases/v0.6 提交后再切回 fix/50，或用 git commit --branches/releases/v0.6 / plumbing（commit-tree + update-ref）在不扰动当前 checkout 的情况下推进 releases/v0.6，随后触发 stale 断言。
>> **Shield:** 已修订 test_hotfix_mimpl.py::test_scenario_b_baseline_stale_reconcile_needs_attention（PRISM-V06-02）：将 stale 触发改为 git plumbing（commit-tree + update-ref）以推进 releases/v0.6 HEAD 而非当前 checkout（fix/50），后接 git reset -q + unlink marker.txt 恢复干净工作树。已绑定 counterexample hotfix_baseline_fresh_never_stale.patch（dev hotfix run 始终报告 baseline.frozen(status=fresh) 从不 stale）：验证 killed（assert stale 被 [] 杀死），反向恢复后仍为合法 Red（USAGE）。

> **Prism [RESOLVED]:** [PRISM-V06-01][blocker][defect_classification=test_defect] AC-FR0240-05 绑定语义缺口：tests/integration/test_hotfix_anchor.py:62-74（test_anchor_invalid_redispatch_and_await_human_no_auto_feature）只断言 validate_anchor_refs 对坏引用返回 (False, missing)，随后转为断言 kernel 模块常量（assert AWAIT_HUMAN in HOTFIX_TRIAGE_SUBSTATES / FEATURE_ROUTE not in HOTFIX_TRIAGE_SUBSTATES）——静态常量断言对任何持有该常量的实现恒真，不约束运行时行为，且常量不是 interfaces §4 可观察出口。AC-FR0240-05 的核心可观察效果均未被驱动断言：verdict.failed(check=anchor_invalid) 事件落地、Sage 重派计数累加（≤3）、重派 3 次仍不合法 → AWAIT_HUMAN 驻留（区别于 NO_ANCHOR 直接驻留）、全程无自动 feature 路由。_decide_hotfix_triage 已 import（:17）但从未调用；全套件亦无用例驱动 3-strike 耗尽路径（§9 SM-01.6/.7 行映射到本测试，同样落空）。修订预期：以行为断言替换常量断言——用 fake simulate（如 sage:SAGE_TRIAGE=bad_anchor）驱动连续锚定校验失败，断言 verdict.failed(anchor_invalid) 事件按 attempt 递增出现、第 3 次失败后 stdout/status 报 awaiting=awaiting_human、事件流无 backlog.recorded。
>> **Shield:** 已修订 test_hotfix_anchor.py::test_anchor_invalid_redispatch_and_await_human_no_auto_feature（PRISM-V06-01）：移除静态常量断言（HOTFIX_TRIAGE_SUBSTATES 成员检查），替换为行为断言——驱动 CLI 入口（trac hotfix 42 --scenario post-release simulate=sage:SAGE_TRIAGE=bad_anchor）后断言 verdict.failed(anchor_invalid) 按 attempt 1,2,3 递增出现（len=3）、awaiting=awaiting_human 在 stdout 中、backlog.recorded 不在事件流中。已绑定 counterexample hotfix_anchor_no_redispatch.patch（仅 1 次 verdict.failed 无 3-attempt 序列）验证 killed 于 len(failed)==3（got 1）；hotfix_anchor_auto_feature_backlog.patch（3 次 verdict.failed + backlog.recorded）验证 killed 于 assert not backlogs。均反向恢复后仍为合法 Red（NotImplementedError）。

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
