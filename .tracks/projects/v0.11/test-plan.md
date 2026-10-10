---
envelope: tracks-envelope:v2
spec_id: SPEC-011
created: 2026-10-11
status: draft
sha:
---

# 自驱动工作台（Releases 整顿 + run 时间线 + release/hotfix 创建 + 人工门 UI 化） — Test Plan

- **Related acceptance**: `.tracks/projects/v0.11/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.11/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- Exposed external API / SDK interfaces（§2b 端点：新 #36-44 与修订的 #17/#18、既有门端点）
- Web 服务 / CLI endpoints（serve 子进程 HTTP 面；页面 HTML/URL/tab 集）
- UI entry points (test **observed** behavior, not verify rendering)（Playwright + data-testid；五态标签/固定详情栏/动作面板/建议面板）
- Structured log entries（service_events / serve stderr JSON 行）
- Persisted data files（tracks.db 事件、service.db 表、story capture、git 分支事实）
- Database tables（intakes 表、service_events、backlog 投影源事件）

### 1.2. Non-observable Objects (tests do not directly depend on)

- Internal class hierarchies, scheduling state machines（kernel reducer、scheduler 内部态）
- Intermediate data structures（intake worker 内部会话态、gate_exec 管道中间量）
- Implementation details (internal queues, registries, state variables)

**Observable contract**: Any internal state that acceptance validation needs must be provided by the implementation layer via dump/log/DB observation points. This is the responsibility of **interfaces.md** — if an AC needs to observe internal state, interfaces.md must have a corresponding outlet (see §6.5)。v0.11 的对应出口：awaiting_gate/return_options（§1k/§1m）、intakes 表与 report schema（§1f）、backlog.dispositioned（§1d）、门命令 completed 与域事件（§1j）。

### 1.3. Cheating Patterns (CI enforced interception)

| #   | Cheating Pattern                | Typical Symptom                                |
| --- | -------------------------------- | ---------------------------------------------- |
| 1   | Change assertions to fit impl    | spec says "refuse on dirty tree", test changes to "queued silently" |
| 2   | Use skip to evade validation     | skip/ignore with "see e2e" but e2e is never written |
| 3   | Assertion degradation            | `assert status in (200, 202)` instead of the contracted per-outcome status |
| 4   | try/except: pass                 | Exception path is swallowed                     |
| 5   | Over-mocking                     | Mock the supervisor/kernel to fake gate advance; testing mock behavior instead |
| 6   | Ground truth uses impl           | Expected value = impl output                   |
| 7   | Hardcoded expected values        | assert branch == "releases/v0.12" only because impl derives it |
| 8   | Trivial pass                     | `assert True` / `assert 1 == 1`                |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.11 TRACKS-TRACE <optional description>` (long-format marker with `TRACKS-TRACE` token, FR-0080/FR-0130). The `TRACKS-TRACE` token is mandatory—without it the marker is invisible to the trace scanner. Multiple ACs bound to the same function get one marker line per AC.
   - CI scans `tests/`, verifying: each test references at least one AC; each AC is referenced by at least one test
   - Any check failure blocks merge
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表。

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion
   - No `try: ... except: pass` wrapping the code under test
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`) without a GitHub issue link

3. **Test change classification** (required in PR description)
   - [ ] New AC (link to acceptance.md commit)
   - [ ] Spec change (link to spec commit)
   - [ ] Fix flake / environment issue (link to issue)

   **Prohibited category**: "impl behavior inconsistent with spec → change test". Reject directly during review.

4. **Testability fallback** (if an AC cannot be tested)
   - Do not mock internals to force it through
   - Register it as a framework-side testability requirement, requesting the implementation layer to add a public assembly point
   - Until resolved, mark the AC as "blocked by testability gap"

### 1.5. Test Division of Labor

- **Unit tests**: Written by the **implementer** (Devon, committed alongside impl in R-G-R). Unit tests are Devon's universal obligation for every implemented FR/NFR, enforced by the coverage gate (§5.1). They are **not** planned in §8 AC Coverage — Archer does not prescribe unit test functions or files.
- **Integration tests**: Written by the **test lead** (Shield) - covers module interface contracts defined in interfaces.md
- **E2E tests**: Written by the **test lead** (Shield) - covers user-facing happy paths only
- **Ground Truth (§3)**: Provided by an **independent developer** not involved in the implementation under test, or a **third-party library**
- **Review ownership**: All test changes are reviewed by the test lead; Ground Truth script changes require focused review of semantic consistency with the corresponding AC

---

## 2. Test Environment

### 2.1. Directory Layout (recommended)

```
tests/
├── unit/          # Unit tests; mirror source file directory structure
├── integration/   # Integration tests; verify module interface contracts
├── e2e/           # End-to-end scenario tests (happy path only; ui marker per IF-WEBUI-001)
├── assets/        # Offline, reproducible test data (v0.11 seeds)
└── ground_truth/  # Not applicable this version (§3)
```

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_0339_02_dirty_tree_refusal`

### 2.3. Execution

- **Offline**: Tests do not depend on network（Scribe 评估走 fake backend；issue 取文走 stand-in GitHub；浏览器为本地 Chromium）
- **Execution order**: unit (fast) → integration → e2e (slow)
- **CI**: Run the full suite on every push（browserless job 排除 ui；ui-e2e job 选择 ui——通道隔离继承 v0.10）
- **Isolation**: pytest marker 体系（integration/e2e/ui/performance）继承；新测试全部落既有 marker 语义

### 2.3.1. Test Execution Contract (`.tracks/projects/project.toml`)

The host project test execution contract is declared in `.tracks/projects/project.toml`（本版零变更，逐字继承 v0.10 现行值——ARCH-011 §4.1）。M-TEST uses this contract to collect and run tests independently. `ui` marker 的默认排除由 pyproject `addopts` 承载（合同命令不带 `-m`）。电池墙钟有界（900s 基线 + 60s/节点，TRAC_BATTERY_TIMEOUT_BASE/PER_NODE）继承。

- **Integration**:
  - framework: pytest
  - paths: ["tests/integration/"]
  - collect: `.venv/bin/python -m pytest --collect-only tests/integration/`
  - run: `.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`
  - cwd: "."
- **E2e**:
  - framework: pytest
  - paths: ["tests/e2e/"]
  - collect: `.venv/bin/python -m pytest --collect-only tests/e2e/`
  - run: `.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`
  - cwd: "."

### 2.4. Test Data

- **Source**: 全部合成/夹具内置：`tests/assets/v0.11/`（Shield 写域）固化多版本 run 注册表种子（版本分组/backlog/归档断言用）、backlog.recorded/dispositioned 事件种子（去向断言用）、含/不含 AC 锚定的 hotfix 预检夹具、raw seed 与 complete story 样本各一（分类断言用）；TLS 测试 CA、canary 密钥、stand-in GitHub、bare 远程、quota 信号样本继承 v0.8/v0.9/v0.10
- **Reproducible**: 版本分组/去向断言以夹具事件集为准；五态派生断言以夹具事件序为准；digest 类预期由标准库 sha256 对公开 canonical 输入局部重算
- **Small data in-repo**: `tests/assets/v0.11/`
- **Sensitive data**: canary 密钥纪律继承（形态逼真、值唯一可 grep、绝不使用真实凭据）
- **Version snapshot**: Vditor vendored 资产 manifest 对账继承

### 2.5. Installation & Isolation

继承首版安装合同（E2E 从 candidate wheel 安装到 fresh venv、源码树外 cwd、禁 editable）与 v0.9/v0.10 追加项（serve 子进程从安装后入口启动、隔离 home、随机端口、口令供给、先 /healthz 后功能断言、UI 子层 Chromium 准备与独立浏览器上下文）——本版安装方式零变更，按生命周期条款不再重述；CI 每次照常执行安装步骤。

---

## 3. Ground Truth Method

本版判定**不适用**独立 ground-truth 脚本。理由同 v0.9/v0.10：v0.11 的验证对象是协议/状态/时序/交互合同（HTTP schema、事件封闭集与去向、门命令执行与自驱动闭环、投影聚合、版本推导、分类采纳、浏览器可观察行为），其预期值来自接口合同本身、事件序与夹具事实，无「算法正确性/数值计算正确性」对象；digest 类预期沿用既有惯例在测试体内以标准库 sha256 对公开 canonical 输入局部重算。不创建 `tests/ground_truth/` 新文件；既有资产继承且不修改。

---

## 4. Test Scope

本计划覆盖 SPEC-011 与 ACC-011 全部 34 条 required AC（FR-0333～FR-0347 共 31 条 + NFR-0157～NFR-0158 共 3 条）。Validity/Testability/Decision 均为绿；规格期锁定决策（五 tab 收敛与 Review 承接、固定详情栏不可协商、时间线单数据源、统一入口四动作与 SM-01、Scribe 评估先于 CREATE_BRANCH、backlog/归档分储、门端点与自驱动、TRIAGE 栏目与评审人合同只落文本不拦运行时）已全部入 spec。

| Valid | Testable | Decided |
| ----- | -------- | ------- |
| ✅    | ✅       | ✅      |

范围外（spec 范围排除）：Docs/Todos/Settings/Account 现有行为变更、时间线数据源新增、Scribe 分流检测的运行时拦截或自动 NO-GO、in-run hotfix_triage 锚定门 UI 化（architecture §5.1 显式简化）、WebSocket、多用户/公网边界。

---

## 5. Acceptance Criteria

1. Unit test coverage：继承 registry 门禁（§7 / ARCH-011 §4.2 coverage-threshold，fail-under=89）；本版新增 Python 面（intake/gate_exec/service 扩展/projections/templating/opencode_review 修订）由 Devon 的 unit 义务覆盖
2. interfaces.md 每个 `modules` 2+ 的新合同至少一条 integration happy+关键 error/edge（Releases 投影、intake 面、M-START 门、backlog 去向、hotfix 入口、门端点与执行、awaiting 面板、时间线数据面、回拨、写入通道、报告栏目、评审规则全覆盖）
3. e2e happy path 全绿：Releases 窗口旅程（注册表→run 详情→时间线→固定详情栏）、统一入口 develop 旅程（建议面板→门→分支→run）、自驱动门旅程（面板动作→下一道门呈现）三组（§9）；错误/边界矩阵只在 integration
4. §8 的 34 条 AC 全部有 integration/e2e layer 与已注册 IF
5. §6 外部依赖分层：L1/L2 默认 CI 全绿；L3 live 通道继承（weekly/manual skip 语义 + release-evidence fail-closed）
6. UI e2e 纪律：关键旅程经真实浏览器完成、控件 `data-testid` 绑定、不以 API 替代 UI 操作（§1.4-3，IF-WEBUI-001 继承）
7. 幂等/防旧/恢复断言覆盖「无重复副作用」（门命令幂等去重 + awaiting 前置；intake 重派无重复）；脱敏扫描覆盖全部出口类别（继承）

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描结果：v0.11 存在宿主技术栈之外的外部依赖——GitHub API（issue 取文：intake source=issue 与 hotfix 预检，继承发布链通道）、模型 provider（Scribe intake 评估与 run 派发，继承 harness 配额/限流信号）、git 远端（继承）、网络/TLS 栈（继承）、Playwright 浏览器二进制（预装基础设施，继承 NFR-0155）。Web 服务自身是 loopback 本地面。

### 6.1. Three Unavoidable Constraints

| #   | Constraint                                | Consequence                                                |
| --- | ----------------------------------------- | ----------------------------------------------------------- |
| C1  | Test environment cannot connect to production dependencies | CI / dev machines cannot run real GitHub/provider paths |
| C2  | Cannot wait for real time                 | 等待/退避断言以注入时间点与受控时钟为准（继承 v0.9） |
| C3  | Cannot mock framework internals           | supervisor/service/kernel/executor/intake worker 均为被测对象；只替换 GitHub API、git 远端、模型 provider 响应 |

### 6.2. Stance: Controllable vs Mock

- **可替换（外部依赖）**：GitHub REST（stand-in，`TRAC_GITHUB_API_BASE`——intake issue 取文与 hotfix 预检共用）、git 远端（本地 bare 仓）、模型 provider 响应（fake backend：Scribe intake 评估与 run 派发同一 TRAC_AGENT_BACKEND 开关）
- **不可 mock**：命令服务、intake 状态机、门命令认领与执行、supervisor drive、投影、SSE、认证/防护/脱敏、分类与版本推导、评审出口计算——这些是被测对象

### 6.3. Three-Layer Test Pyramid

| Layer | Name                | Time          | Speed  | Coverage               | Default Run       |
| ----- | ------------------- | ------------- | ------ | ---------------------- | ----------------- |
| L1    | Deterministic sim   | 注入时间点     | Seconds | 绝大多数 v0.11 AC（Releases 投影/intake 状态机与分类/M-START 门/门端点与 worker 执行/自驱动推进/awaiting 映射/写入通道/评审规则） | ✅ CI default     |
| L2    | Contract sim        | 注入时间点     | Seconds | issue 取文与 hotfix 预检经 stand-in GitHub；Scribe 评估经 fake backend | ✅ CI default |
| L3    | Real env smoke      | Real calendar | Real   | 继承既有 live 通道（双宿主旅程/hotfix live/release-evidence + hotfix retry-open live）；intake issue 取文与 hotfix 预检共用 effects/github fetch 层，由既有 live 旅程在真实环境兜验，不新增 live job | ❌ nightly/manual |

UI e2e（`ui` marker）是与 L1/L2 正交的交付面层（对 loopback serve 驱动真实 Chromium），属默认 CI 的独立 required check（ui-e2e），不属于 L3——继承 v0.10 语义。fake（每次跑）与 live（周期/里程碑跑）的 AC 不重叠；纯本地确定性逻辑（门执行/投影/状态机）不设 live 通道。

### 6.4. Responsibility Contract of Test Infrastructure

| # | Component        | Responsibility (external)              | Boundary (what it does not implement) |
| --- | --------------------------------------- | --------------------------------------- | -------------------------------------- |
| 1 | serve fixture（tests/_support，继承） | 真实子进程启动/停止服务（隔离 home/项目仓/随机端口/口令供给） | 不 import server 内部、不替代 HTTP 语义 |
| 2 | Playwright 浏览器 fixture（继承） | 启动/回收 Chromium、隔离上下文、storage_state、page 对象 | 不实现业务断言、不替代 UI 操作 |
| 3 | SSE 读取器/事件等待栅栏（继承） | 帧序列读取、确定性同步 | 不产生事件 |
| 4 | stand-in GitHub + bare 远程（继承） | issue 取文/milestone/hotfix 预检协议替身 | 不实现 tracks 业务 |
| 5 | intake 评估栅栏（Shield 新增，tests/_support） | 以 fake backend 驱动 Scribe 评估至 evaluated/failed 终态（轮询 intake 状态） | 不伪造 report 内容（fake act 的回复体即被测输入） |
| 6 | canary/quota 注入器（继承） | 合成密钥与配额信号注入 | 不使用真实凭据 |

### 6.5. Assertion Basis — Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md** §4：§2b 新端点响应/状态码（#36-44 与 #17/#18 修订形态、错误 reason 封闭集）、run detail 追加字段（awaiting_gate/return_options）、页面形态（PAGES 6 名/五 tab/302 重定/data-testid）、service_events 新成员（#26-28）与 intakes 表、tracks.db 事件（human.*/release.decided/backlog.*/story capture）、git 分支事实、CI/合同文件事实、浏览器可观察出口（§4d）。AC 需要的内部状态若无对应出口，回修 interfaces/acceptance，不在测试侧窥探内部。

---

## 7. CI Gate

- **Required check**（名称即合同）：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`、`ui-e2e`（七名继承，零 workflow 变更）；merge 全 required；milestone `release-evidence` 硬门禁继承
- **Validation items**:
  - AC reference closure (each AC ≥1 test, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3)
  - Coverage 门槛（registry coverage-threshold，fail-under=89）
  - Vditor manifest 一致性（继承）
  - 可靠性八场景标记扫描（继承）
  - UI 纪律静态面：`ui` 标记测试的控件定位 `data-testid` 绑定扫描（§1.4-3，继承）
- **failure semantics**：任何 required check 失败即阻断；Agent 自述不构成证据；live 通道缺凭据语义继承（weekly/manual `LIVE_SKIPPED`，milestone 通道 fail-closed）；ui-e2e 缺浏览器即红

---

## 8. AC Coverage

锚点归属（task graph schema v2，两类纪律继承 interfaces IF-010 §1u / IF-GREENGUARD-001）：§8 行的 integration 节点中，除标注「守卫，§8.1」者外均为可由 task 声明转绿的**合法 Red 验收锚点**（先红后绿）；e2e 项为 ISLAND_GATE_2/FULL 兜底的终态锚点，不写入 task 验收声明；`ui` 标记的 e2e 锚点由 ui-e2e required check 承载。守卫节点验证设计期已落地事实，从红窗必填集排除、不作 task 验收锚点（§8.1 为唯一权威）。重入期期望语义（green-by-delivery 象限）继承 v0.10。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0333-01 | integration + e2e | tests/integration/test_releases_projection.py::test_registry_backlog_groups + tests/e2e/test_releases_ui.py::test_releases_sidebar_and_selection | IF-RELEASES-001, IF-BACKLOG-001 |
| AC-FR0333-02 | integration | tests/integration/test_releases_projection.py::test_archive_group_distinct_queryable | IF-RELEASES-001, IF-BACKLOG-001 |
| AC-FR0334-01 | integration + e2e | tests/integration/test_workbench_tabs.py::test_five_tab_closed_set + tests/e2e/test_releases_ui.py::test_five_tabs_no_review | IF-RELEASES-001, IF-WORKBENCH-001 |
| AC-FR0334-02 | integration + e2e | tests/integration/test_gate_endpoints.py::test_awaiting_gate_mapping + tests/e2e/test_awaiting_ui.py::test_stage_review_completed_in_panel | IF-AWAIT-001, IF-GATEUI-001 |
| AC-FR0335-01 | e2e | tests/e2e/test_releases_ui.py::test_timeline_full_rendering | IF-TIMELINE-002, IF-WEBUI-001 |
| AC-FR0335-02 | integration | tests/integration/test_timeline_full.py::test_timeline_data_parity_no_mock | IF-TIMELINE-002, IF-QUERY-001 |
| AC-FR0336-01 | e2e | tests/e2e/test_releases_ui.py::test_node_detail_fixed_pane | IF-TIMELINE-002, IF-WEBUI-001 |
| AC-FR0336-02 | integration + e2e | tests/integration/test_timeline_full.py::test_detail_degradation_no_floating + tests/e2e/test_releases_ui.py::test_fixed_pane_position_no_overlay | IF-TIMELINE-002 |
| AC-FR0337-01 | integration | tests/integration/test_timeline_full.py::test_return_options_gate_pointer | IF-RETURN-001, IF-TIMELINE-002 |
| AC-FR0337-02 | integration + e2e | tests/integration/test_timeline_full.py::test_return_confirm_revalidation + tests/e2e/test_return_ui.py::test_return_confirm_face | IF-RETURN-001 |
| AC-FR0338-01 | integration + e2e | tests/integration/test_intake.py::test_intake_sources_and_actions + tests/e2e/test_intake_ui.py::test_scribe_panel_four_actions | IF-INTAKE-001 |
| AC-FR0338-02 | integration | tests/integration/test_intake.py::test_raw_seed_verbatim_complete_story_adopted_no_hotfix_routing | IF-INTAKE-001, IF-MSTART-001 |
| AC-FR0339-01 | integration + e2e | tests/integration/test_mstart_gate.py::test_develop_creates_branch_run_title + tests/e2e/test_intake_ui.py::test_develop_happy_creates_release | IF-MSTART-001 |
| AC-FR0339-02 | integration | tests/integration/test_mstart_gate.py::test_active_run_backlog_dirty_refusal_unmerged_confirm | IF-MSTART-001, IF-BACKLOG-001 |
| AC-FR0339-03 | integration | tests/integration/test_flow_contracts.py::test_flowmd_scribe_before_create_branch + tests/integration/test_mstart_gate.py::test_nondevelop_actions_no_run_branch | IF-MSTART-001 |
| AC-FR0340-01 | integration | tests/integration/test_releases_projection.py::test_backlog_storage_and_archive_distinct | IF-BACKLOG-001 |
| AC-FR0340-02 | integration | tests/integration/test_releases_projection.py::test_disposition_recorded_and_promote_flow | IF-BACKLOG-001, IF-INTAKE-001 |
| AC-FR0341-01 | integration + e2e | tests/integration/test_hotfix_entry.py::test_hotfix_preview_and_anchor_display + tests/e2e/test_intake_ui.py::test_hotfix_entry_anchored_proceeds | IF-HOTFIXUI-001, IF-HOTFIX-011 |
| AC-FR0341-02 | integration | tests/integration/test_hotfix_entry.py::test_anchor_fail_exits_no_half_branch | IF-HOTFIXUI-001 |
| AC-FR0342-01 | integration | tests/integration/test_gate_endpoints.py::test_triage_review_events_csrf_idempotency | IF-GATEUI-001, IF-CMDSVC-001 |
| AC-FR0342-02 | integration + e2e | tests/integration/test_gate_endpoints.py::test_approve_release_executed_via_worker + tests/e2e/test_awaiting_ui.py::test_approve_release_in_panel | IF-GATEUI-001, IF-WEBGATE-001 |
| AC-FR0343-01 | e2e | tests/e2e/test_awaiting_ui.py::test_awaiting_panel_reason_impact_action | IF-AWAIT-001, IF-WEBUI-001 |
| AC-FR0343-02 | integration + e2e | tests/integration/test_gate_endpoints.py::test_non_awaiting_no_gate_entry + tests/e2e/test_awaiting_ui.py::test_no_dangling_stage_review | IF-AWAIT-001 |
| AC-FR0344-01 | integration + e2e | tests/integration/test_review_write_channel.py::test_review_round_edit_and_comments + tests/e2e/test_awaiting_ui.py::test_review_round_doc_write | IF-DOCSWRITE-001, IF-DOCSAVE-001 |
| AC-FR0344-02 | integration | tests/integration/test_review_write_channel.py::test_pre_approval_empty_baseline_not_blocked | IF-DOCSWRITE-001 |
| AC-FR0345-01 | integration + e2e | tests/integration/test_gate_endpoints.py::test_gate_action_auto_advances + tests/e2e/test_awaiting_ui.py::test_self_drive_next_gate_appears | IF-GATEUI-001, IF-DRIVE-001 |
| AC-FR0345-02 | integration | tests/integration/test_gate_endpoints.py::test_no_manual_dispatch_needed | IF-GATEUI-001 |
| AC-FR0346-01 | integration | tests/integration/test_intake.py::test_report_dup_conflict_columns | IF-SPLIT-001, IF-INTAKE-001 |
| AC-FR0346-02 | integration | tests/integration/test_flow_contracts.py::test_triage_contract_columns_no_runtime_interception | IF-SPLIT-001 |
| AC-FR0347-01 | integration | tests/integration/test_flow_contracts.py::test_reviewer_contract_clause | IF-REVIEW-002 |
| AC-FR0347-02 | integration | tests/integration/test_review_exit_rule.py::test_pass_without_self_findings_revise_anchored | IF-REVIEW-002 |
| AC-NFR0157-01 | integration + e2e | tests/integration/test_gate_endpoints.py::test_all_new_writes_csrf_idempotency_single_path + tests/e2e/test_awaiting_ui.py::test_ui_writes_carry_headers | IF-CMDSVC-001, IF-GATEUI-001 |
| AC-NFR0158-01 | integration + e2e | tests/integration/test_timeline_full.py::test_no_second_source_failure_visible + tests/e2e/test_releases_ui.py::test_api_failure_feedback_new_surfaces | IF-QUERY-001, IF-STREAM-001, IF-TIMELINE-002 |
| AC-NFR0158-02 | integration + e2e | tests/integration/test_flow_contracts.py::test_v010_rulings_anchored（守卫，§8.1） + tests/e2e/test_workbench_ui.py::test_v010_rulings_regression | IF-WORKBENCH-001, IF-GREENGUARD-001 |

### 8.1 Arrival-green guard nodes（到达即绿守卫清单）

以下 1 个节点是到达即绿守卫（interfaces IF-010 §1u / IF-GREENGUARD-001 继承）：它验证设计期已落地事实，从红窗必填集排除、不作为 task 验收锚点声明，且在每次执行中必须保持绿（失败即真回归，fail-closed）。本清单是守卫集合的唯一权威；不在清单内的节点到达即绿仍按既有 unexpected_pass 处理。

- `tests/integration/test_flow_contracts.py::test_v010_rulings_anchored` — AC-NFR0158-02 的声明半边（设计期裁定存在性：v0.10 工作台裁定在 ARCH-011 §0.1 显式继承；同 AC 的 e2e test_v010_rulings_regression 保持合法 Red 锚点）— IF-WORKBENCH-001, IF-GREENGUARD-001

变更注记（初版修订，2026-10-11）：flow.md §3/§4/§5 合同文本不在 Archer scaffold 域内（保留域拒收 `.tracks/**`，两次 checkpoint 拒绝实证）；初版将 test_flowmd_scribe_before_create_branch / test_triage_contract_columns_no_runtime_interception / test_reviewer_contract_clause 三个节点列为守卫的前提（设计期已交付文本）不成立。修订后三节点为**合法 Red 验收锚点**：ARCH-011 §1.0.12 钉定修订全文，Devon 任务经 `[layout.devon].writable` 的 `.tracks/wiki/flow.md` 单文件授权（ARCH-011 §2 已交付 project.toml）落地，锚点随任务转绿。

---

## 9. E2E Happy Paths

### 9.1 API 层纵向闭环（继承 v0.9/v0.10，不修改）

`tests/e2e/test_web_journey.py` 与 `test_web_hotfix_journeys.py` 原样继承（API 面零破坏性变更——新端点追加式、既有端点语义不变）。

### 9.2 UI 层：Releases 窗口旅程（Playwright，`ui` marker）

`tests/e2e/test_releases_ui.py`：登录+名字采集（继承流程）→ tab 条五项（无 Review）→ Releases 窗口 sidebar 三组（版本分组注册表/backlog 折叠/归档查询）+ 摘要行 → 选中 release(run) → 时间线完整绘制（attempt 独立节点/回边/五态标签/聚焦活跃）→ 鼠标选中节点 → 固定详情栏加载真数据详情（选中/活跃双标记、owning surface 跳转返回）→ API 失败注入呈现可观察反馈 → 全程 data-testid、同一 document。

### 9.3 UI 层：统一入口 develop 旅程（Playwright，`ui` marker）

`tests/e2e/test_intake_ui.py`：新建 release 输入一段话（raw seed）→ 建议面板呈现（可行性/重复/冲突栏目 + 四动作）→ 选「进入开发」→ 过 M-START 门 → `releases/v{N+1}` 分支与 run 建立 → 时间线呈现 M-STORY 活跃；附 hotfix 入口半程（选择 issue+场景 → 预检与锚定结果呈现 → 锚定成立继续）。

### 9.4 UI 层：自驱动门旅程（Playwright，`ui` marker）

`tests/e2e/test_awaiting_ui.py`：run 到达 triage 门 → 下栏动作面板（原因/影响/动作）→ UI 完成 triage(go) → 无手工推进 → 下一道门在时间线呈现（活跃指针前移）→ 文档阶段评审轮（直接修改 + inline-comment 发表）→ review(no_comment) → 连续处理至阶段完成；全程写请求携带 CSRF/Idempotency-Key。

---

## 10. Fail-closed / 注入矩阵（integration 为主，浏览器层注入归 ui e2e）

| # | 注入 | 必须观察的阻断/行为 |
|:--|:--|:--|
| 1 | submit_intake issue 来源、issue 不存在/取文失败 | rejected(issue_fetch_failed) 携分类 next；无 intake 行残留 |
| 2 | decide_intake 于 status≠evaluated | rejected(intake_not_ready)；无状态迁移 |
| 3 | decide_intake(remove_backlog) 于 source=text | rejected(validation_failed)（SM-01.5 仅 backlog 来源） |
| 4 | decide_intake(develop) 工作区不干净 | rejected(dirty_tree) 携原因；无分支无 run |
| 5 | decide_intake(develop) 未合并分支、无 confirm | 409 unmerged_release_branches {branches}；不建分支 |
| 6 | 同上、confirm=false | 取消退出；无分支无 run |
| 7 | decide_intake(develop) 活跃 run 存在 | backlog.recorded(reason=active_run)；不开新 run 不建分支 |
| 8 | 门端点在 awaiting 不匹配时提交 | rejected(awaiting_mismatch)；无事件落盘 |
| 9 | 门端点重复提交（同幂等键） | command.deduplicated；human.* 事件恰一条 |
| 10 | 门命令执行中崩溃（杀 worker） | requeue → 重放安全（awaiting 前置或事件恰一条） |
| 11 | Scribe 评估 fake backend 连续失败 | intake failed 态可读呈现；重派 ≤3；无重复 intake |
| 12 | #35 保存于六件套不全目录（pre-approval） | docs_revision（missing 标签）可得；保存成功产生新 revision |
| 13 | #17 保存 base=null、trio 可得 | 409 stale_revision（必须绑定） |
| 14 | #18 anchor_line 与 thread_token 同给/同缺 | 422；无讨论写盘 |
| 15 | 保存通道网络/5xx 失败（ui） | 编辑内容保留可重试；不伪报成功 |
| 16 | hotfix-preview 锚定不成立 | unanchored 呈现；create_run rejected；无 fix 分支 |
| 17 | 旧 URL 访问（/projects、/runs/{id}/review） | 302 至 / |
| 18 | 时间线数据缺失节点（夹具空事件 run） | 可读降级标记；不伪造节点（ui 半） |
| 19 | return_options 于不可回拨形态 | targets 空；UI 无回拨控件 |
| 20 | return 无 confirm 且存在 already_executed | 409 回显清单；确认后执行；不伪装撤销 |
| 21 | API 失败注入于新数据面（releases/intakes） | 可观察失败反馈；不伪报成功（ui） |
| 22 | 非本人未决线程的评审出口计算 | verdict=pass；不触发无发现修订（§1p.2 收窄） |
| 23 | 评审人自有未决线程 | revise 且线程锚定可追溯 |
| 24 | intake 评估崩溃重启（evaluating 悬置） | 超时回收重派；报告以最后成功 envelope 为准 |

---

## 11. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | 既有 e2e/integration 全套（v0.10 及以前） | API 面零破坏性变更的回归守护不变；新端点为追加式 | v0.11 变更追加式 |
| 2 | `tests/e2e/test_workbench_ui.py` | tab 集断言七项→五项（移除 projects/runs/review 条目）；overview/projects 导航断言改 Releases 窗口；新增 test_v010_rulings_regression 承载五项裁定回归（钉视口/令牌/紧凑行/pane bar/自动保存断言保留） | FR-0333/FR-0334 破坏性变更（spec 变更驱动） |
| 3 | `tests/e2e/test_run_timeline_ui.py` | 浮层断言改为固定详情栏断言（data-testid=detail-pane、无 overlay）；五态标签断言按 data-state | FR-0335/FR-0336 固定下栏替换浮层（spec 变更驱动） |
| 4 | `tests/e2e/test_docs_ui.py` | `test_no_discussion_write_controls` 修订：评审轮上下文（awaiting review/triage）呈现写控、非评审轮维持无写控断言（#18 anchor_line 通道不触碰 /discussions 路径——v0.10 §1p.3 边界保持） | FR-0344 评审轮写入通道（spec 变更驱动） |
| 5 | `tests/integration/test_web_auth.py` 等 URL 相关断言 | /projects、/projects/{pid}/runs/new、/runs/{id}/review 的页面断言改 302 重定 | PAGES 收敛（spec 变更驱动） |
| 6 | `tests/integration/test_lex_review_park.py` | revise 分支形态断言按 §1p.2 收窄复核：非本人非 Human 未决线程 → pass（原 revise 形态改构造自有线程）；park 分支断言不变 | FR-0347 评审出口规则修订（spec 变更驱动） |
| 7 | `tests/integration/test_docs_center.py` | 六件套不全目录的 docs_revision 断言从「不可得/拒绝」改为「missing 标签可得」（齐全目录断言值不变） | IF-DOCSWRITE-001 全域化（设计修订驱动） |
| 8 | Devon 侧单测（Devon-owned，RGR 内做合同适配） | pages/timeline/intake/gate 相关 unit 由 Devon 按 ARCH-011 §1.0.1 增长轴自辖 | 既有 RGR 纪律 |

Devon 的 unit 更新由 RGR/coverage 自辖；本表不处方 unit 文件/函数。
