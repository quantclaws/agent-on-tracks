---
envelope: tracks-envelope:v2
spec_id: SPEC-009
created: 2026-09-18
status: draft
sha:
---

# Web 后台持续驱动与最小工作台（v0.9）— Test Plan

- **Related acceptance**: `.tracks/projects/v0.9/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.9/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

本计划只断言 interfaces.md §4 的外部出口：

- HTTP API 的 JSON 响应与状态码（interfaces §2b 全部端点）、页面 HTML 形态、SSE 帧序列、serve 子命令的 stdout/stderr 与退出码。
- `<service_home>/service.db` 的服务面表（service_events/commands/waits/leases/schedule/projects）与 tracks.db 既有事件、blobs、文档 revision。
- git 本地与 bare/stand-in 远端事实（发布链继承 v0.8 出口）。
- 服务进程的 stdout 结构化 JSON 日志行。
- 系统级观测：服务进程 RSS（经 `ps`）、请求频率（计数桩）。

测试不因内部类、私有 state、函数调用次数或 mock 返回值通过；需要的内部判定必须先落 interfaces.md 的出口（§6.5）。

### 1.2. Non-observable Objects (tests do not directly depend on)

- supervisor/scheduler/lease 的内部实现细节；经 service.db 表、事件与 API 投影观察。
- CommandService 的内部队列组织；经 command 状态机事件与 `/api/commands/{id}` 观察。
- 认证/脱敏的内部数据结构；经 401/403 行为、审计事件与全出口扫描观察。
- 浏览器渲染；UI 断言只到 HTML/资产引用/响应 schema（本版不做浏览器端 e2e 驱动，见 §5.1 注记）。

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
| 9   | 模拟服务代替真实服务             | 用 TestClient/进程内 app 代替真实 uvicorn 子进程（v0.9 特化） |
| 10  | 等待时间造假                     | 注入 retry_at 后断言"过了 1 秒就恢复"，绕过持久化与真实唤醒（v0.9 特化） |
| 11  | 脱敏断言只查一个出口             | 只扫描某个 API 而漏掉页面/日志/SSE（v0.9 特化） |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.9 TRACKS-TRACE <optional description>` (long-format marker with `TRACKS-TRACE` token, FR-0080/FR-0130). Multiple ACs bound to the same function get one marker line per AC.
   - CI scans `tests/`, verifying: each test references at least one AC; each AC is referenced by at least one test
   - Any check failure blocks merge
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表。

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion
   - No `try: ... except: pass` wrapping the code under test
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`, jest `it.skip`, Go `t.Skip`) without a GitHub issue link

3. **v0.9 特化断言纪律**
   - 服务一律以真实子进程启动（serve 命令 + `--port 0` + 临时 `--home`）；禁止进程内 app/TestClient 替身（§1.3 #9）。
   - 持久化断言必须跨进程边界（重启后重新查询），禁止只查内存态（§1.3 #10 的反面）。
   - 幂等/防旧断言必须同时断言"无第二次业务效果"（事件计数 + 远端/文件事实），不能只断言"返回了同一 command_id"。
   - 脱敏断言覆盖全部出口类别：页面 HTML、API JSON、SSE 帧、stdout 日志行（§1.3 #11）。
   - pause/wait 断言基于事件序（run.pause_requested 先于 run.paused），禁止 sleep-race；竞态测试用确定性栅栏（事件等待器）驱动。

4. **Test change classification** (required in PR description)
   - [ ] New AC (link to acceptance.md commit)
   - [ ] Spec change (link to spec commit)
   - [ ] Fix flake / environment issue (link to issue)

   **Prohibited category**: "impl behavior inconsistent with spec → change test". Reject directly during review.

5. **Testability fallback** (if an AC cannot be tested)
   - Do not mock internals to force it through
   - Register it as a framework-side testability requirement, requesting the implementation layer to add a public assembly point
   - Until resolved, mark the AC as "blocked by testability gap"

### 1.5. Test Division of Labor

- **Unit tests**: Written by the **implementer** (Devon, committed alongside impl in R-G-R). Unit tests are Devon's universal obligation for every implemented FR/NFR, enforced by the coverage gate (§5.1). They are **not** planned in §8 AC Coverage — Archer does not prescribe unit test functions or files.
- **Integration tests**: Written by the **test lead** (Shield) - covers module interface contracts defined in interfaces.md（含命令服务/租约/调度/等待/投影/SSE/认证/防护/脱敏的全部错误与边界矩阵）
- **E2E tests**: Written by the **test lead** (Shield) - 仅两条 happy path 组：Web 纵向闭环主链与两类 hotfix 的 Web 入口旅程
- **Ground Truth (§3)**: 本版不适用（见 §3）
- **Review ownership**: All test changes are reviewed by the test lead

---

## 2. Test Environment

### 2.1. Directory Layout

```text
tests/
├── unit/          # Devon 自辖，不在 §8 处方
├── integration/   # v0.9 新增 31 个文件（§8 表 test 列）
├── e2e/           # 新增 test_web_journey.py、test_web_hotfix_journeys.py
├── assets/        # v0.9 新增 assets/v0.9/（quota 信号样本、canary 密钥、陈旧 revision 夹具说明）
├── _support/      # 新增 serve fixture（真实子进程启动/停止）、SSE 读取器、事件等待栅栏、canary 注入器
└── ground_truth/  # 本版不新增（§3 不适用）
```

既有目录结构与 layout 合同不变；新资产全部落在既有 Shield 写域。

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_0295_02_duplicate_submit`
- 可靠性八场景测试另带注释标记 `# RELIABILITY-SCENARIO: <服务重启|断网|模型额度|重复并发请求|并发worker|UI断线重连|旧worker迟到结果|人工pause竞态>` 之一（AC-NFR0151-01 的扫描依据）

### 2.3. Execution

- **Offline**: 默认套件不依赖网络（serve/worker/SSE 全部 loopback；quota/CI/网络等待经注入信号与 stand-in 构造；Vditor 资产自源站提供）
- **Execution order**: unit (fast) → integration → e2e (slow)
- **CI**: Run the full suite on every push
- **Isolation**: Integration and e2e use the project framework's marker/tag/select mechanism (`@pytest.mark.integration` / `@pytest.mark.e2e`) to avoid mixing with unit tests
- 每个 serve fixture 使用独立临时 `--home` 与临时项目仓库；端口取 `--port 0` 随机空闲端口；测试结束显式停止子进程

### 2.3.1. Test Execution Contract (`.tracks/project/project.toml`)

The host project test execution contract is declared in `.tracks/projects/project.toml`（v0.9 逐字不变，architecture §4.1）。M-TEST uses this contract to collect and run tests independently.

- **Integration**:
  - framework: pytest
  - paths: ["tests/integration/"]
  - collect: `.venv/bin/python -m pytest --collect-only tests/integration/`
  - run: `.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 4 --dist loadscope --junitxml={result}`
  - cwd: "."
- **E2e** (if applicable):
  - framework: pytest
  - paths: ["tests/e2e/"]
  - collect: `.venv/bin/python -m pytest --collect-only tests/e2e/`
  - run: `.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 4 --dist loadscope --junitxml={result}`
  - cwd: "."

### 2.4. Test Data

- **Source**: 全部合成/夹具内置：`tests/assets/v0.9/` 固化 quota 信号样本（已知 reset 时间 / 未知 reset 时间两种 harness 响应形态）、canary 密钥值（脱敏扫描用）、陈旧 revision 场景的种子文档；stand-in GitHub 与 bare 远程沿用 v0.8 资产
- **Reproducible**: 等待/退避断言以注入的固定时间点与 WaitPolicy 参数为准（initial 60s/cap 900s 在测试中以缩小配置注入，断言相对序列与上限语义，不依赖墙钟长时间等待）；digest 类预期由标准库 sha256 对公开 canonical 输入局部重算
- **Small data in-repo**: `tests/assets/v0.9/`
- **Sensitive data**: canary 密钥是显式合成的假密钥（形态逼真、值唯一可 grep），绝不使用真实凭据
- **Version snapshot**: Vditor vendored 资产以 `tracks/server/static/vendor/vditor/manifest.json` 的逐文件 sha256 为快照，测试对账

### 2.5. Installation & Isolation

继承首版安装合同（E2E 从 candidate wheel 安装到 fresh venv、源码树外 cwd、禁 editable），v0.9 追加：

- e2e 的服务进程从**安装后的**入口启动（隔离 venv 内的 trac），不从源码树 import server 包；serve 的初始化步（口令供给、home 建库）在隔离 home 中完成，正如新用户首次启动
- 安装后断言服务可发现：serve 启动 → `/healthz` 返回可用，再进入功能断言
- 每条 e2e 使用独立临时 home + 临时项目仓库 + 随机端口；结束后全部清理

---

## 3. Ground Truth Method

本版判定**不适用**独立 ground-truth 脚本。理由：v0.9 的验证对象是协议/状态/时序合同（HTTP schema、事件封闭集、幂等去重、租约防旧、等待持久化、退避序列、脱敏扫描），其预期值来自接口合同本身、事件序与夹具事实，无"算法正确性/数值计算正确性"对象；digest 类（params_digest、preview_digest 绑定）沿用 v0.8 惯例在测试体内以标准库 sha256 对公开 canonical 输入局部重算。不创建 `tests/ground_truth/` 新文件；既有资产继承且不修改。

---

## 4. Test Scope

本计划覆盖 SPEC-009 与 ACC-009 全部 74 条 required AC（FR-0288～FR-0315 共 67 条 + NFR-0150～NFR-0152 共 7 条）。Validity/Testability/Decision 均为绿；规格期锁定决策（单活动 run 串行+hotfix 换队、手动启动、等待/轮询口径、待办中心即发布提醒、harness 配额信号）已全部入 spec。

| Valid | Testable | Decided |
| ----- | -------- | ------- |
| ✅    | ✅       | ✅      |

范围外（spec 范围排除）：Agent 会话内容/会话视图、多 run 真并发、公网多租户、known-issue 撤销通道与 project_close 尾巴、通用包管理 GUI、图形化流程编辑器等（SPEC-009 范围排除章）。

---

## 5. Acceptance Criteria

1. Unit test coverage ≥95%（继承 v0.8 门槛，§4.2 registry coverage-threshold 不变）
2. interfaces.md 每个 `modules` 2+ 的新合同至少一条 integration happy+关键 error/edge（命令服务、租约、调度、等待、投影、SSE、认证、防护、脱敏、文档审阅全覆盖）
3. e2e happy path 两条组（Web 纵向闭环主链、hotfix 两旅程 Web 入口）全绿；错误/边界矩阵只在 integration
4. §8 的 74 条 AC 全部有 integration/e2e layer 与已注册 IF
5. §6 外部依赖分层：L1/L2 默认 CI 全绿；本版无新增 L3 通道（§6.3）
6. 可靠性八场景各有至少一条自动化测试并纳入质量门（AC-NFR0151-01）
7. 幂等/防旧/恢复断言同时覆盖"无重复副作用"（§1.4-3）；脱敏扫描覆盖全部出口类别（§1.4-4）

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描结果：v0.9 存在宿主技术栈之外的外部依赖——模型 provider（harness 的配额/限流信号）、GitHub API 与 git 远端（继承 v0.8 发布链）、网络。Web 服务自身是 loopback 本地面，不构成新的外部依赖。

### 6.1. Three Unavoidable Constraints

| #   | Constraint                                | Consequence                                                |
| --- | ----------------------------------------- | ----------------------------------------------------------- |
| C1  | Test environment cannot connect to production dependencies | CI / cross-platform dev machines cannot run production paths |
| C2  | Cannot wait for real time                 | quota 已知 reset 的"到达后自动续跑"以注入的时间点与受控时钟断言，不等真实墙钟 |
| C3  | Cannot mock framework internals           | server/supervisor/kernel/executor 均为被测对象；只替换 harness 响应、GitHub API、git 远端等真外部依赖 |

### 6.2. Stance: Controllable vs Mock

- **可替换（外部依赖）**：模型 provider 响应（fake backend / 注入 quota 信号样本）、GitHub REST（v0.8 stand-in，TRAC_GITHUB_API_BASE 显式指向）、git 远端（本地 bare 仓）、墙钟（不参与 identity；等待恢复以注入时间点驱动）
- **不可 mock**：命令服务、租约/代次、调度、等待持久化、drive_once、投影、SSE、认证/防护/脱敏——这些是被测对象

### 6.3. Three-Layer Test Pyramid

| Layer | Name                | Time          | Speed  | Coverage               | Default Run       |
| ----- | ------------------- | ------------- | ------ | ---------------------- | ----------------- |
| L1    | Deterministic sim   | 注入时间点     | Seconds | 绝大多数 v0.9 AC（命令/租约/调度/等待/投影/SSE/认证/脱敏） | ✅ CI default     |
| L2    | Contract sim        | 注入时间点     | Seconds | 发布链经 stand-in GitHub + bare 远端的 Web 入口旅程（含两 hotfix） | ✅ CI default |
| L3    | Real env smoke      | Real calendar | Real   | 继承 v0.8 既有 live 通道（双宿主六旅程/live agent）；**v0.9 不新增 L3 通道**——Web 面无可仅在真实环境验证的行为（loopback 服务 + 既有 live 通道已覆盖真实 provider/GitHub） | ❌ nightly/manual |

L3 的既有语义（凭据探针、fail-never-skip 于 milestone 通道）继承 v0.8 不变。

### 6.4. Responsibility Contract of Test Infrastructure

| # | Component        | Responsibility (external)              | Boundary (what it does not implement) |
| --- | ---------------- | --------------------------------------- | -------------------------------------- |
| 1 | serve fixture（tests/_support） | 以真实子进程启动/停止服务（临时 home/项目仓/随机端口/口令供给），暴露 base_url 与进程句柄 | 不 import server 内部、不替代 HTTP 语义 |
| 2 | SSE 读取器 | 读 text/event-stream 帧序列（id/event/data），支持断连重连注入 | 不解析业务语义 |
| 3 | 事件等待栅栏 | 等待指定事件/状态出现（确定性同步，消除 sleep-race） | 不产生事件 |
| 4 | quota 信号注入器 | 以样本响应使 harness 返回已知/未知 reset 的配额信号 | 不修改 waiting 策略 |
| 5 | canary 注入器 | 把合成密钥值植入 env 与 run 数据 | 不使用真实凭据 |
| 6 | stand-in GitHub + bare 远程（继承 v0.8） | 发布链协议替身 | 不实现 tracks 业务 |

### 6.5. Assertion Basis — Closure with interfaces.md

断言只落 interfaces.md §4：§2b 全部端点响应/状态码、页面形态（Vditor 资产同源引用、无密钥、无会话内容）、SSE 帧序、serve 子命令输出与退出码、service.db 各表与 service_events 封闭集、tracks.db 既有事件、结构化日志行、远端/git 事实、进程 RSS 与请求频率观测。AC 需要的内部状态若无对应出口，回修 interfaces/acceptance，不在测试侧窥探内部。

---

## 7. CI Gate

- **Required check**（继承，名称不变）：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`；merge 全 required；milestone `release-evidence` 硬门禁继承不变
- **Validation items**:
  - AC reference closure (each AC ≥1 test, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3，含 v0.9 特化三条)
  - Coverage ≥95%（§4.2 registry）
  - Vditor manifest 一致性（manifest.json 逐文件 sha256 对账，经 AC-FR0294-02 同源断言承载）
  - 可靠性八场景标记扫描（AC-NFR0151-01）
- **failure semantics**：任何 required check 失败即阻断；Agent 自述不构成证据；live 通道缺凭据的语义继承 v0.8（weekly/manual `LIVE_SKIPPED`，milestone 通道 fail-closed）

---

## 8. AC Coverage

锚点归属（task graph schema v2）：每行 `test` 列的 integration 项是可由 task 声明转绿的验收锚点；e2e 项是 ISLAND_GATE_2/FULL 兜底的终态锚点，不写入 task 验收声明。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0288-01 | integration + e2e | tests/integration/test_serve_lifecycle.py::test_health_and_browserless_progress + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-SERVE-001 |
| AC-FR0288-02 | integration | tests/integration/test_serve_lifecycle.py::test_restart_recovers_registry_commands_waits | IF-SERVE-001, IF-RECOVER-001 |
| AC-FR0288-03 | integration | tests/integration/test_serve_lifecycle.py::test_unhealthy_no_fake_available | IF-SERVE-001 |
| AC-FR0289-01 | integration + e2e | tests/integration/test_project_registry.py::test_register_shows_attribution_and_survives_restart + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-PROJ-001 |
| AC-FR0289-02 | integration | tests/integration/test_project_registry.py::test_out_of_scope_rejected | IF-PROJ-001, IF-SECRECY-001 |
| AC-FR0290-01 | integration + e2e | tests/integration/test_readiness.py::test_readiness_items_displayed + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-PROJ-001 |
| AC-FR0290-02 | integration | tests/integration/test_readiness.py::test_not_ready_blocks_run_creation | IF-PROJ-001 |
| AC-FR0291-01 | integration + e2e | tests/integration/test_run_create_web.py::test_create_feature_run_returns_ids + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-CMDSVC-001 |
| AC-FR0291-02 | integration | tests/integration/test_run_create_web.py::test_failed_and_duplicate_submit_no_fake_run | IF-CMDSVC-001 |
| AC-FR0292-01 | e2e | tests/e2e/test_web_hotfix_journeys.py::test_post_release_hotfix_web_journey + tests/e2e/test_web_hotfix_journeys.py::test_dev_hotfix_web_journey | IF-CMDSVC-001, IF-JOURNEY-001 |
| AC-FR0292-02 | integration | tests/integration/test_run_create_web.py::test_hotfix_precheck_rejected | IF-CMDSVC-001, IF-HOTFIX-001 |
| AC-FR0293-01 | integration + e2e | tests/integration/test_clarify_web.py::test_reply_continues_same_run + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-CMDSVC-001, IF-DOCGAP-001 |
| AC-FR0293-02 | integration | tests/integration/test_clarify_web.py::test_discussion_history_traceable | IF-DOCGAP-001 |
| AC-FR0294-01 | integration | tests/integration/test_doc_review_web.py::test_revision_visible_and_pending_bound | IF-DOCREV-001 |
| AC-FR0294-02 | integration | tests/integration/test_doc_review_web.py::test_diff_between_revisions_visible | IF-DOCREV-001 |
| AC-FR0294-03 | integration | tests/integration/test_doc_review_web.py::test_edit_produces_new_revision_stale_approval_rejected | IF-DOCREV-001, IF-WEBGATE-001 |
| AC-FR0295-01 | integration | tests/integration/test_command_service.py::test_command_id_survives_restart | IF-CMDSVC-001 |
| AC-FR0295-02 | integration | tests/integration/test_command_service.py::test_idempotency_dedup_and_conflict | IF-CMDSVC-001 |
| AC-FR0295-03 | integration | tests/integration/test_command_service.py::test_cli_http_equivalent_events | IF-CMDSVC-001 |
| AC-FR0295-04 | integration | tests/integration/test_command_service.py::test_queries_are_readonly | IF-CMDSVC-001, IF-QUERY-001 |
| AC-FR0296-01 | integration | tests/integration/test_supervisor_lease.py::test_concurrent_drive_single_dispatch | IF-LEASE-001 |
| AC-FR0296-02 | integration | tests/integration/test_supervisor_lease.py::test_stale_generation_late_result_quarantined | IF-LEASE-001 |
| AC-FR0296-03 | integration | tests/integration/test_hotfix_swap.py::test_second_run_queued | IF-SCHED-001 |
| AC-FR0296-04 | integration | tests/integration/test_hotfix_swap.py::test_hotfix_preemption_audited | IF-SCHED-001, IF-PAUSE-001 |
| AC-FR0297-01 | integration + e2e | tests/integration/test_auto_drive.py::test_auto_advance_stages + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-DRIVE-001 |
| AC-FR0297-02 | integration | tests/integration/test_auto_drive.py::test_human_gate_blocks_advance | IF-DRIVE-001 |
| AC-FR0298-01 | integration + e2e | tests/integration/test_wait_persistence.py::test_wait_survives_restart_and_auto_resumes + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-WAIT-001, IF-RECOVER-001 |
| AC-FR0298-02 | integration | tests/integration/test_wait_persistence.py::test_no_hot_loop_bounded_probing | IF-WAIT-001 |
| AC-FR0299-01 | integration | tests/integration/test_quota_wait.py::test_known_reset_auto_resume | IF-WAIT-001 |
| AC-FR0299-02 | integration | tests/integration/test_quota_wait.py::test_unknown_reset_probe_plan_no_countdown | IF-WAIT-001 |
| AC-FR0300-01 | integration + e2e | tests/integration/test_restart_recovery.py::test_worker_kill_reclaim_no_duplicate_effects + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-RECOVER-001, IF-PUBLISH-002 |
| AC-FR0300-02 | integration | tests/integration/test_restart_recovery.py::test_server_restart_resumes_without_loss | IF-RECOVER-001 |
| AC-FR0301-01 | integration | tests/integration/test_overview_query.py::test_overview_consistent_with_state | IF-QUERY-001 |
| AC-FR0301-02 | integration | tests/integration/test_overview_query.py::test_query_during_long_task_nonblocking | IF-QUERY-001 |
| AC-FR0302-01 | integration | tests/integration/test_progress_projection.py::test_dual_counts_presented | IF-QUERY-001 |
| AC-FR0302-02 | integration | tests/integration/test_progress_projection.py::test_progress_matches_real_counts | IF-QUERY-001 |
| AC-FR0303-01 | integration | tests/integration/test_execution_state.py::test_role_task_states_with_log_refs | IF-QUERY-001 |
| AC-FR0303-02 | integration | tests/integration/test_execution_state.py::test_no_agent_session_content_anywhere | IF-QUERY-001 |
| AC-FR0304-01 | integration | tests/integration/test_ac_evidence_view.py::test_ac_chain_complete | IF-QUERY-001 |
| AC-FR0304-02 | integration | tests/integration/test_ac_evidence_view.py::test_missing_stale_unreviewed_marked | IF-QUERY-001 |
| AC-FR0305-01 | integration | tests/integration/test_timeline_diagnosis.py::test_timeline_shows_failures_retries_next | IF-QUERY-001 |
| AC-FR0305-02 | integration | tests/integration/test_timeline_diagnosis.py::test_logs_locatable_by_dimensions | IF-QUERY-001 |
| AC-FR0306-01 | integration | tests/integration/test_event_stream.py::test_live_push_without_refresh | IF-STREAM-001 |
| AC-FR0306-02 | integration | tests/integration/test_event_stream.py::test_reconnect_backfill_no_regression | IF-STREAM-001 |
| AC-FR0307-01 | integration | tests/integration/test_todo_center.py::test_legit_decisions_listed_with_context | IF-QUERY-001 |
| AC-FR0307-02 | integration | tests/integration/test_todo_center.py::test_waits_never_in_todos | IF-QUERY-001 |
| AC-FR0308-01 | integration + e2e | tests/integration/test_web_approval.py::test_approval_bound_and_advances + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-WEBGATE-001 |
| AC-FR0308-02 | integration | tests/integration/test_web_approval.py::test_stale_revision_rejected | IF-WEBGATE-001 |
| AC-FR0309-01 | integration + e2e | tests/integration/test_web_release.py::test_release_via_web_happy + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-WEBGATE-001, IF-RELEASE-003 |
| AC-FR0309-02 | integration | tests/integration/test_web_release.py::test_stale_preview_rejected | IF-WEBGATE-001, IF-RELEASE-002 |
| AC-FR0309-03 | integration | tests/integration/test_web_release.py::test_delay_and_return_bound_to_digest | IF-WEBGATE-001, IF-RELEASE-003 |
| AC-FR0310-01 | integration + e2e | tests/integration/test_pause_resume.py::test_pause_two_phase_and_priority + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-PAUSE-001, IF-CMDSVC-001 |
| AC-FR0310-02 | integration | tests/integration/test_pause_resume.py::test_resume_from_legal_position | IF-PAUSE-001, IF-CMDSVC-001 |
| AC-FR0311-01 | integration | tests/integration/test_controlled_retry.py::test_retry_from_legal_position_no_duplicates | IF-WEBGATE-001, IF-ESCAPE-001 |
| AC-FR0311-02 | integration | tests/integration/test_controlled_retry.py::test_stale_evidence_retry_rejected | IF-WEBGATE-001 |
| AC-FR0312-01 | integration + e2e | tests/integration/test_web_rollback.py::test_return_with_impact_preview + tests/e2e/test_web_journey.py::test_feature_web_vertical_journey | IF-WEBGATE-001, IF-ESCAPE-001 |
| AC-FR0312-02 | integration | tests/integration/test_web_rollback.py::test_late_results_isolated_after_rollback | IF-ESCAPE-001, IF-LEASE-001 |
| AC-FR0312-03 | integration | tests/integration/test_web_rollback.py::test_pushed_release_survives_rollback | IF-ESCAPE-001 |
| AC-FR0313-01 | integration | tests/integration/test_abandon_web.py::test_abandon_terminal_auditable | IF-WEBGATE-001, IF-ESCAPE-002 |
| AC-FR0313-02 | integration | tests/integration/test_abandon_web.py::test_abandon_no_fake_success | IF-ESCAPE-002 |
| AC-FR0313-03 | integration | tests/integration/test_abandon_web.py::test_unrecoverable_failure_terminal | IF-DRIVE-001 |
| AC-FR0314-01 | integration | tests/integration/test_web_auth.py::test_unauthenticated_rejected_then_ok | IF-WEBAUTH-001 |
| AC-FR0314-02 | integration | tests/integration/test_web_auth.py::test_non_human_decision_rejected_audited | IF-CMDGUARD-001 |
| AC-FR0314-03 | integration | tests/integration/test_web_auth.py::test_shell_payload_blocked | IF-CMDGUARD-001 |
| AC-FR0314-04 | integration | tests/integration/test_web_auth.py::test_adjudication_ownership_enforced | IF-WEBAUTH-001 |
| AC-FR0315-01 | integration | tests/integration/test_secrecy.py::test_no_plaintext_secrets_any_surface | IF-SECRECY-001 |
| AC-FR0315-02 | integration | tests/integration/test_secrecy.py::test_out_of_scope_read_denied | IF-SECRECY-001 |
| AC-NFR0150-01 | integration | tests/integration/test_perf_budget.py::test_accept_returns_after_persist | IF-CMDSVC-001 |
| AC-NFR0150-02 | integration | tests/integration/test_perf_budget.py::test_query_p95_under_1s | IF-QUERY-001 |
| AC-NFR0150-03 | integration | tests/integration/test_perf_budget.py::test_soak_memory_bounded | IF-SERVE-001 |
| AC-NFR0151-01 | integration | tests/integration/test_reliability_matrix.py::test_eight_reliability_scenarios_covered | IF-MTEST-001, IF-MTEST-002 |
| AC-NFR0151-02 | integration | tests/integration/test_reliability_matrix.py::test_coverage_threshold_inherited | IF-GUARD-001, IF-GUARD-002 |
| AC-NFR0152-01 | integration | tests/integration/test_backoff_policy.py::test_backoff_growth_bounded_and_config_readable | IF-WAIT-001 |
| AC-NFR0152-02 | integration | tests/integration/test_backoff_policy.py::test_poll_fallback_caps_and_idle_silence | IF-STREAM-001 |

> **Prism [RESOLVED]:** [severity=blocker] [defect_classification=test_defect] [artifact=tests/integration/test_serve_lifecycle.py + tests/integration/test_reliability_matrix.py + tests/e2e/test_web_journey.py + tests/e2e/test_web_hotfix_journeys.py + tests/_support/v09_web.py (WRITE manifest)] [AC=AC-FR0288-01/02/03, AC-NFR0151-01] NO_DIFF_REVIEW verdict=revise: Shield no-diff explanation unjustified. Explanation text is vacuous ('Shield completed NO_DIFF_EXPLAIN', result_id 01M2SR5YKBC0W37ZBVB6NP99C4) with zero reason or evidence. Forensic record: manifest include lists 5 already-committed paths while observed=[] (manifest_error mismatch), i.e. zero worktree edits this dispatch; HEAD copies of those 5 files still carry the known test_defect (8 serve/reliability nodes with inverted try/except-stub assertions that PASS on stubs and can never pass post-implementation, per verdict.failed check=test_defect attempt=3, blob 3e4d99b928e30bb1ffa236d4589bc1f9ec6667f71cc4fc4c4f9dbc4de175b3b0; base_sha 202b2002aceca2e9e4242d84947ec38561ee8c60). The required positive-assertion rewrite was proved producible (commit 202b200) and is outstanding again after revert 3e19347, so a diff is both possible and required. Injected failure evidence 1-archer-15 (M-DESIGN Archer non_zero_exit) is unrelated and supports nothing here. Expected revision: re-dispatch Shield WRITE to rewrite the 8 defect nodes as positive contract-token assertions with an attributable diff and matching manifest; do not waive requires_diff while test_defect stands.
>> **Prism:** 收束验证：修复提交 1acc1f1（SHIELD_FIX，command_id 01M2SYDC3XWBW6YA80SMMKTAM7）已改写本线程点名的全部 5 个文件；8 节点现为面向真实 serve 进程的 positive 断言，当前失败均为 stub 未实现触发的合法 Red（IF-SERVE-001 等 contract token），inverted try/except-stub 缺陷形态已消除。

---

## 9. E2E Happy Paths

### 9.1 Web 纵向闭环主链（test_feature_web_vertical_journey）

对应 acceptance 文首脚本主链：serve 启动（隔离 home/随机端口/口令供给）→ 登记项目 → 就绪检查 → 经 Web 创建 feature run（返回 command_id/run_id）→ UI 澄清回复 → 绑定 revision 的批准 → 此后零人工命令：supervisor 沿设计→测试→实施/RGR→验证自动推进（fake backend + stand-in GitHub + bare 远程）→ 注入一次可恢复外部等待并恢复 → kill 一次 worker 进程并观察重认领 → 服务整体重启一次并观察等待/命令/项目恢复 → pause（观察"已接收→已暂停"，注入 retry_at 到期不越过）→ resume → 合法阶段回拨（影响预览 + 确认，迟到结果隔离）→ 全门通过后 Web 审阅 preview 并 release → 终态 released，远端 tag 与 AC 证据链可查。全程经 stdlib HTTP 客户端驱动，无任何 CLI 逐条推进。

### 9.2 hotfix 两旅程 Web 入口（test_post_release_hotfix_web_journey / test_dev_hotfix_web_journey）

在已发布基线上经 Web 入口分别运行 post-release 与 dev hotfix 至终态：post-release 产生 patch tag/release；dev 仅 prerelease（无公开 tag/release 产物）。前检失败路径（无活跃 release 分支起 dev）归 integration 层（AC-FR0292-02，IF-CMDSVC-001/IF-HOTFIX-001）。

---

## 10. Fail-closed / 注入矩阵（integration）

| # | 注入 | 必须观察的阻断/行为 |
|:--|:--|:--|
| 1 | 越范围项目登记 | project.registration_rejected(outside_permitted_scope)，403，无 run |
| 2 | 就绪缺失工具 | tools.ok=false+reason；create_run 拒绝且不建 run |
| 3 | 同幂等键重复提交 | command.deduplicated，业务效果一次；同键异 payload → 409 idempotency_conflict |
| 4 | 并发推进同一 run（两实例+并发请求） | 单代次胜出；无重复 dispatch/publish |
| 5 | 旧代次迟到结果 | worker.late_result(quarantined)；run 状态不变 |
| 6 | hotfix preempt=false 且有活动 run | command.rejected(active_run_exists) |
| 7 | quota 已知 reset | retry_at 精确唤醒；known_reset=true；无人工干预续跑 |
| 8 | quota 未知 reset | 有界退避探测；无倒计时字段；不进入待办中心 |
| 9 | 等待期间服务重启 | waits 行保留；retry_at 不变；恢复推进 |
| 10 | worker 执行中被杀 | command.requeued(lease_expired)；已完成副作用不重复 |
| 11 | 长任务期间查询风暴 | 查询即时返回；事件计数不变；P95<1s |
| 12 | SSE 断连后重连+重复/乱序事件 | 按游标补齐；不回退 |
| 13 | 过期 revision 批准 / 过期 preview 发布 | rejected(stale_revision/stale_preview)；状态不推进 |
| 14 | pause 后注入 retry_at 到期/额度恢复 | run 保持暂停（pause 优先） |
| 15 | 证据过期的受控重试 | rejected+原因；不复活过期证据；不可恢复错误无重试入口 |
| 16 | 回拨跨越已推送 tag/release | 先 already_executed 清单 + confirm 门；远端对象保留 |
| 17 | 未认证访问任意页面/API | 401/302 登录页 |
| 18 | 非人 actor_class 提交批准/发布 | 403 + forbidden_actor + access.denied；无状态推进 |
| 19 | 任意 shell 载荷 | 400 guard_blocked；无执行效果 |
| 20 | 非被请求方 resolved 裁决线程 | 状态不变 + access.denied；被请求方经认证入口生效 |
| 21 | canary 密钥扫全部出口 | 页面/API/SSE/日志零明文 |
| 22 | 授权范围外文件读取 | 403 scope_violation + 审计 |
| 23 | 服务不可用依赖启动 | healthz 503+reasons；无"可用"假象 |
| 24 | 未知 reset 探测间隔观测 | 60s 起退避至 ≤900s；config 端点读出默认值 |
| 25 | 服务空闲 | 无持续轮询负载 |

---

## 11. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | 既有 e2e/integration 全套 | 不修改：v0.8 及以前合同继承（回归守护） | v0.9 不动既有可观察行为 |
| 2 | `tests/integration/test_kernel_language_neutrality.py` | 追加断言：新包 tracks/server、tracks/supervisor 同样零语言 token（扫描范围 tracks/** 已覆盖，追加显式用例锁定） | NFR-0147 扫描语义延续 |

Devon 的 unit 更新由 RGR/coverage 自辖；本表不处方 unit 文件/函数。
