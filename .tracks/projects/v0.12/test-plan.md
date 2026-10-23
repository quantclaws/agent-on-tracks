---
envelope: tracks-envelope:v2
spec_id: SPEC-012
created: 2026-10-19
status: draft
sha:
---

# 安装包、全局 trac、项目版本锁定与 OOB 工作台 — Test Plan

- **Related acceptance**: `.tracks/projects/v0.12/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.12/interfaces.md` (assertion basis — see §6.5)

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- CLI endpoints（`trac install`、`trac version`、`trac serve`、既有子命令与退出码）
- Web service / HTTP endpoints（`/healthz`、rounds/round-detail、review 写端点、静态资源响应头）
- UI entry points（Playwright + `data-testid`：阶段轨道、节点详情、阶段工作流、按轮手风琴、运行卡片、评审决策面）
- Persisted data files（`.tracks/install.json`、`.tracks/env/**`、`.tracks/bin/trac.py`、`.opencode/**`、pidfile/log、版本文档讨论块）
- Database tables / events（`human.review` payload、既有事件序；`service.started` payload version 为继承观察点，不纳入 v0.12 断言基线）
- Build artifacts（官方 wheel 的 zip 名单与 `METADATA`）

### 1.2. Non-observable Objects (tests do not directly depend on)

- Internal class hierarchies / installer staging implementation
- Intermediate data structures（安装 staging 内部布局、worker 内部调用栈）
- Implementation details（具体复制方式、内部缓存、私有函数）

**Observable contract**: Any internal state that acceptance validation needs must be provided by the implementation layer via dump/log/DB observation points. This is the responsibility of **interfaces.md** — if an AC needs to observe internal state, interfaces.md must have a corresponding outlet (see §6.5). v0.12 的对应出口：receipt/site-packages/launcher（§1b）、`trac version`/healthz（§1d/§1e；`service.started` 为继承观察点，不在 v0.12 断言基线）、rounds/round-detail（§1f）、UI testid（§4c）、`human.review` payload 与文档线程（§1h/§1j）、静态响应头（§1i）。

### 1.3. Cheating Patterns (CI enforced interception)

| # | Cheating Pattern | Typical Symptom |
|:--|:--|:--|
| 1 | Change assertions to fit impl | spec 要求退回先写文档线程，测试只断言 `human.review.comments`（迁就 HEAD c6dc3f8） |
| 2 | Use skip to evade validation | 用 skip 跳过跨版本/后台服务，声称“手工验证过” |
| 3 | Assertion degradation | 只断言 `returncode != 0`，不检查 stderr 的封闭 reason；只断言“有元素”，不检查 data-state/文本 |
| 4 | try/except: pass | 吞掉 install/healthz/PID 错误；失败仍报成功 |
| 5 | Over-mocking | mock 安装器内部/Worker/uvicorn，测试 mock 行为而非真实 CLI/HTTP/进程 |
| 6 | Ground truth uses impl | 用被测 receipt/healthz 反向生成期望版本；用 UI 输出反向断言 |
| 7 | Hardcoded expected values | 只因为当前版本是 0.12.0 就写死，不校验 wheel METADATA 与 site-packages 字节 |
| 8 | Trivial pass | `assert True` / `assert 1 == 1` / 只断言元素存在 |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.12 TRACKS-TRACE <optional description>`；`TRACKS-TRACE` token 必填；一个函数覆盖多个 AC 时每个 AC 一行。
   - CI scans `tests/`：每个测试至少引用一个 AC；每个 AC 至少被一个测试引用；失败阻断 merge。
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 在 §8 声明 IF- 归属；IF- 取值来自 interfaces.md §5。
2. **Assertion taboos**（CI static checks，违反阻断 merge）
   - 禁止把 `assert True` / `assert 1` / `assert <obj> is not None` 作为唯一断言。
   - 禁止 `try: ... except: pass` 包住被测代码。
   - 禁止无 GitHub issue 链接的 skip/ignore。
3. **Test change classification**（PR 描述必填）
   - [ ] New AC（链接 acceptance.md commit）
   - [ ] Spec change（链接 spec commit）
   - [ ] Fix flake / environment issue（链接 issue）
   - **Prohibited category**: “impl behavior inconsistent with spec → change test”。评审直接拒绝。
4. **Testability fallback**
   - 不 mock 内部强行通过；登记为实现层可测试性需求；未解决前标记 “blocked by testability gap”。

### 1.5. Test Division of Labor

- **Unit tests**: Devon 在 R-G-R 内随实现提交；覆盖每个已实现 FR/NFR，由覆盖率门禁（§5.1）保证；Archer 不处方 unit 文件/函数。
- **Integration tests**: Shield 编写，覆盖 interfaces.md 的跨模块合同、CLI/HTTP/文件/进程面。
- **E2E tests**: Shield 编写，覆盖用户 happy path；UI 层用 `ui` marker。
- **Ground Truth**: 本版不适用（§3）。
- **Review ownership**: 全部测试变更由 test lead 评审；跨版本/安装 fixture 变更重点核对语义一致性。

---

## 2. Test Environment

### 2.1. Directory Layout

```text
tests/
├── unit/          # Devon R-G-R unit tests
├── integration/   # Shield: install/version/serve/rounds/review/static contracts
├── e2e/           # Shield: CLI install journey + Playwright UI workflows
├── assets/v0.12/  # Shield-owned seeds: events, round blobs/logs, bad-wheel recipes
└── ground_truth/  # Not applicable this version (§3)
```

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`（如 `test_ac_0351_01_explicit_version_split`）；同一函数可覆盖多条 AC，每 AC 一行 TRACKS-TRACE marker。

### 2.3. Execution

- **Offline**：安装只用本地 wheel 或运行中 distribution；serve 只 loopback；UI 只本地 Chromium；不访问包索引/外部服务。
- **Execution order**：unit（快）→ integration → e2e（慢）。
- **CI**：每次 push 跑 FULL；browserless job 排除 `ui`；`ui-e2e` job 选择 `ui`。
- **Isolation**：pytest marker `integration/e2e/ui/performance` 继承；安装测试在隔离 prefix/临时项目内完成，不污染源码树与系统环境。

### 2.3.1. Test Execution Contract (`.tracks/projects/project.toml`)

The host project test execution contract is declared in `.tracks/projects/project.toml`（本设计的原始变更集不修改该文件；现行 bytes 已包含 v0.12 OOB 修复的 e2e `collect` marker 过滤变更与 4-worker 单元/集成并发修复，`hooks-runner-ci-required` 的 `config_digest` 已重锚为 `94d9774e…`，见 architecture §4.2；`.flake8` 未变化）。M-TEST uses this contract to collect and run tests independently.

- **Integration**:
  - framework: pytest
  - paths: ["tests/integration/"]
  - collect: `.venv/bin/python -m pytest --collect-only tests/integration/`
  - run: `.venv/bin/python -m pytest tests/integration/ --tb=short -q -n 4 --dist loadscope --junitxml={result}`
  - run_selected: `.venv/bin/python -m pytest {nodes} --tb=short -q -n 4 --dist loadscope --junitxml={result}`
  - cwd: "."
- **E2e**:
  - framework: pytest
  - paths: ["tests/e2e/"]
  - collect: `.venv/bin/python -m pytest --collect-only tests/e2e/ -m 'not performance'`
  - run: `.venv/bin/python -m pytest tests/e2e/ --tb=short -q -n 8 --dist loadscope --junitxml={result}`
  - run_selected: `.venv/bin/python -m pytest {nodes} --tb=short -q -n 8 --dist loadscope -m 'not performance' --junitxml={result}`
  - cwd: "."

`ui` marker 的默认排除由 pyproject `addopts` 承载；e2e `collect` / `run_selected` 显式以 `-m 'not performance'` 覆盖 marker 过滤，以收集和选中 UI 节点。电池墙钟有界（900s 基线 + 60s/节点，`TRAC_BATTERY_TIMEOUT_BASE/PER_NODE`）继承。

### 2.4. Test Data

- **Source**：全部合成/夹具内置。`tests/assets/v0.12/`（Shield 写域）固化：多阶段事件序（含重复派发、human 动作、verdict、awaiting 门）、round 输入/输出 blob 与 `.opencode/logs` 会话样本、坏 wheel 构建配方（缺 `tracks/assets/opencode.json` 或 METADATA 版本不符）、多版本 wheel 构建配方（临时副本改版本后构建）。
- **Reproducible**：wheel/安装断言以固定字节与 METADATA 为准；事件/轮次断言以夹具事件序为准；静态缓存断言以固定 ETag 与内容替换为准。
- **Small data in-repo**：`tests/assets/v0.12/`；不提交构建出的 wheel（运行时在 tmp 构建）。
- **Sensitive data**：canary 密钥纪律继承；安装/委派测试不使用真实凭据；模型/provider 不在测试范围。
- **Version snapshot**：wheel 的 `METADATA` 与 `tracks/__init__.py` 版本作为快照；Vditor/vendor manifest 对账继承。

### 2.5. Installation & Isolation

- **Installation method**：E2E 从候选 wheel 以 `pip install --no-deps --target <prefix>/lib/python<X.Y>/site-packages <wheel>` 安装到源码树外的隔离 prefix（不创建 venv）；夹具在 `<prefix>/bin/trac` 生成 console 入口；全局 `trac` 以 `PYTHONPATH=<prefix>/lib/python<X.Y>/site-packages <prefix>/bin/trac` 调用当前测试解释器，starlette/uvicorn/certifi 由该测试解释器提供（与生产的共享解释器合同一致，不访问包索引）；项目安装用该 prefix 的 `trac install`，与最终用户一致；不使用 editable/源码树 import。
- **Install target**：每次测试一个 fresh prefix（`tmp_path`，布局为 `lib/python<X.Y>/site-packages` + `bin/trac`），不污染系统环境或源码树。
- **Working directory**：在源码树外的临时目录（`$TMPDIR/<run-id>/workdir`）运行全局 `trac`；项目目标也是临时 Git 仓库/空目录。
- **Initialization**：空目录由 `trac install` 先 `git init -b main` 并 scaffold；已有项目预置 Git 历史与用户文件；服务用 `trac serve --background` 启动并轮询 `/healthz`。
- **Verification**：安装后先断言 `PYTHONPATH=<prefix>/lib/python<X.Y>/site-packages <prefix>/bin/trac --help` / `version` 可用，再断言功能；后台服务先 healthz 再断言版本/端口。

---

## 3. Ground Truth Method

本版判定**不适用**独立 ground-truth 脚本。v0.12 的验证对象是包装/安装/文件布局/CLI 委派/进程生命周期/HTTP schema/UI 可观察行为，预期值来自 wheel 字节、METADATA、receipt、接口合同与夹具事件序，不存在“算法正确性/数值计算”对象。digest/版本/字节断言在测试体内用标准库 `hashlib`/`zipfile`/`json` 对公开输入局部重算，不 import 被测安装器内部；不创建 `tests/ground_truth/`，既有资产继承且不修改。

---

## 4. Test Scope

本计划覆盖 SPEC-012 与 ACC-012 全部 24 条 required AC（FR-0348～FR-0360 共 22 条 + NFR-0159～NFR-0160 共 2 条）。Validity/Testability/Decision 均为绿；规格期锁定决策（默认版本=全局版本、显式版本+官方 wheel、项目 site-packages 锁定、全局不兜底、POSIX 后台/Windows 前台、review 退回先写线程、OOB 防回退）已全部入 spec/acceptance。

| Valid | Testable | Decided |
|:------|:---------|:--------|
| ✅    | ✅       | ✅      |

范围外（spec 范围排除）：进行中项目一键升级/迁移工具、Windows 后台服务、OOB 纯展示调参数值、包索引下载器、自动重启/开机自启、用户可编辑 tracks 自有文件。

---

## 5. Acceptance Criteria

1. Unit test coverage：继承 registry 门禁（§7 / ARCH-012 §4.2 coverage-threshold，`--fail-under=89`）；新增 Python 面（install/project_runtime/install_cmd、serve/review 修订）由 Devon 的 unit 义务覆盖。
2. interfaces.md 每个 `modules` 2+ 的新合同至少一条 integration happy+关键 error/edge（安装包/安装布局/委派/服务生命周期/rounds/review/static 全覆盖）。
3. e2e happy path 全绿：安装旅程（全局 wheel→install→version→后台 serve→全局升级旧项目仍旧）与 OOB 工作台旅程（阶段路径→工作流→按轮→卡片→评审）两组（§9）；错误/边界矩阵只在 integration。
4. §8 的 24 条 AC 全部有 integration/e2e layer 与已注册 IF；§8.1 的到达即绿守卫节点保持绿。
5. §6 外部依赖分层：L1/L2 默认 CI 全绿；L3 既有 live 通道继承且不新增必需 job；本版安装/服务为本地确定性面。
6. UI e2e 纪律：关键旅程经真实 Chromium、控件 `data-testid` 绑定、不以 API 替代 UI 操作（IF-WEBUI-001 继承）。
7. 安装/委派/恢复断言覆盖“无半成品、无重复副作用、无伪成功”：失败无 receipt、重试收敛、后台 stop 后不可连接、review 退回线程恰一条（幂等/awaiting 前置）。

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描结果：v0.12 **不新增**宿主技术栈之外的远程服务依赖。安装源是本地候选 wheel 或运行中 distribution；项目服务是 loopback 本地进程；OOB 读取本地事件/blob/log；GitHub/opencode/模型 provider 的既有 external 面继承 v0.9～v0.11 的 live 通道，v0.12 不新增 AC 重叠的 live 测试。POSIX 后台服务与 Windows 前台是操作系统语义，分别由真实 POSIX 子进程与平台注入分支覆盖。

### 6.1. Three Unavoidable Constraints

| # | Constraint | Consequence |
|:--|:--|:--|
| C1 | Test environment cannot connect to production dependencies | 安装测试不访问包索引；只用本地 wheel/运行 distribution |
| C2 | Cannot wait for real time | 服务等待/轮询用有界 timeout 与 healthz 栅栏；后台持续性用短时 shell 退出验证，不等待真实日历 |
| C3 | Cannot mock framework internals | 安装器、CLI 委派、serve 生命周期、supervisor/worker、投影均为被测对象；只替换外部输入（wheel 文件、静态文件、事件夹具） |

### 6.2. Stance: Controllable vs Mock

- **可替换（外部输入）**：本地 wheel 文件、静态文件内容、事件/blob/log 夹具、端口号、临时 Git 仓库。
- **不可 mock**：`trac install` 的解析/校验/复制/receipt、CLI 委派、项目 launcher、uvicorn/serve、supervisor 门执行、投影/rounds/review 写入、静态中间件、UI DOM 行为。若测试必须绕过这些才能通过，说明观察出口设计错误，应回修 interfaces/acceptance。

### 6.3. Three-Layer Test Pyramid

| Layer | Name | Time | Speed | Coverage | Default Run |
|:------|:-----|:-----|:------|:---------|:------------|
| L1 | Deterministic sim | 注入事件/端口 | Seconds | 安装复制/布局/委派/rounds/UI 种子数据/静态头 | ✅ CI default |
| L2 | Contract sim | 注入事件/端口 | Seconds | 本地 wheel 作为官方 artifact 替身；loopback serve；本地 Chromium | ✅ CI default |
| L3 | Real env smoke | Real OS | Real | POSIX 真实 detached 后台服务（shell 退出后仍存活/stop 不可连接）；候选 wheel 安装到 fresh prefix | ✅ CI default（ubuntu/macOS 本地面），不新增 live job |

Windows 前台分支由平台注入的 integration 分支覆盖（`sys.platform`/平台判定函数），不在 CI 启动真实 Windows；发布前手工 smoke 记录在验收证据中。既有 GitHub/opencode live 通道（`tests/e2e_live/`、weekly/manual）继承，不覆盖 v0.12 的本地 AC。

### 6.4. Responsibility Contract of Test Infrastructure

| # | Component | Responsibility (external) | Boundary (what it does not implement) |
|:--|:--|:--|:--|
| 1 | Wheel builder fixture | 从当前源码构建候选 wheel；在临时副本改版本后构建第二版本 wheel | 不修改仓库源码；不替代安装器校验 |
| 2 | Isolated prefix fixture | 创建 fresh prefix（`lib/python<X.Y>/site-packages` + `bin/trac`）、`pip install --no-deps --target` wheel、注入 `PYTHONPATH` | 不 import 源码树；不实现 tracks 业务；不建 venv |
| 3 | Project fixture | 创建临时 Git 仓库/空目录、用户文件、receipt 目标 | 不伪造 receipt 内容（除损坏注入用例） |
| 4 | Serve/daemon fixture | 启动/停止前台或后台 serve、读 healthz/端口/pidfile | 不替代 supervisor/kernel/HTTP 语义 |
| 5 | Event/round seed | 用 Store/blob/log 写入夹具事件与轮次 | 不合成第二数据源；不伪造 UI 渲染 |
| 6 | Static file fixture | 用临时 static 目录替换内容并请求 | 不改变 API 路由语义 |
| 7 | Browser fixture | 启动/回收 Chromium、隔离上下文 | 不实现业务断言、不替代 UI 操作 |

### 6.5. Assertion Basis — Closure with interfaces.md

Test assertions **may only** land on the external observable outlets defined in **interfaces.md** §4：CLI stdout/stderr/exit（§4a）、receipt/site-packages/managed files/pidfile（§4a）、HTTP schema/headers（§4b）、events payload（§4b）、浏览器 testid/文本（§4c）。AC 需要的内部状态若无对应出口，回修 interfaces/acceptance，不在测试侧窥探内部。

---

## 7. CI Gate

- **Required check**（名称即合同）：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`、`ui-e2e`（七名继承，零 workflow 变更）；merge 全 required；milestone `release-evidence` 硬门禁继承。
- **Validation items**：
  - AC reference closure（每个 AC ≥1 测试、每个测试 ≥1 AC）
  - Anti-pattern static scan（§1.3）
  - Coverage 门槛（registry coverage-threshold，`--fail-under=89`）
  - Vditor/vendor manifest 一致性（继承）
  - 可靠性八场景标记扫描（继承）
  - UI 纪律静态面：`ui` 标记测试的控件定位 `data-testid` 绑定扫描（继承）
- **failure semantics**：任一 required check 失败即阻断；Agent 自述不构成证据；live 通道缺凭据语义继承（weekly/manual `LIVE_SKIPPED`，milestone 通道 fail-closed）；ui-e2e 缺浏览器即红。

---

## 8. AC Coverage

锚点归属（task graph schema v2，两类纪律继承 interfaces IF-GREENGUARD-001）：§8 行的 integration 节点中，除 §8.1 列出的到达即绿守卫外均为可由 task 声明转绿的合法 Red 验收锚点；e2e 项为 ISLAND_GATE_2/FULL 兜底的终态锚点，不写入 task 验收声明；`ui` 标记的 e2e 锚点由 ui-e2e required check 承载。重入期期望语义（green-by-delivery 象限）继承 v0.10/v0.11：OOB 已交付面的测试是守卫，不是 Red。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0348-01 | integration + e2e | tests/integration/test_install_package.py::test_wheel_carries_capability_resources + tests/e2e/test_install_journey.py::test_install_from_official_wheel | IF-INSTALLPKG-001, IF-INSTALL-001 |
| AC-FR0348-02 | integration | tests/integration/test_install_package.py::test_incomplete_wheel_rejected | IF-INSTALLPKG-001, IF-INSTALL-001 |
| AC-FR0349-01 | integration + e2e | tests/integration/test_install_package.py::test_global_trac_help_zero_repo + tests/e2e/test_install_journey.py::test_global_trac_available_anywhere | IF-INSTALLPKG-001, IF-GLOBALCLI-001 |
| AC-FR0349-02 | integration | tests/integration/test_server_lifecycle.py::test_serve_outside_project_refused | IF-GLOBALCLI-001, IF-SERVERLIFE-001 |
| AC-FR0350-01 | integration + e2e | tests/integration/test_server_lifecycle.py::test_background_survives_shell_and_reports_locked_version + tests/e2e/test_install_journey.py::test_project_server_background_roundtrip | IF-SERVERLIFE-001, IF-PROJECTCLI-001, IF-VERSIONVIS-001 |
| AC-FR0350-01 | integration | tests/integration/test_server_lifecycle.py::test_background_on_taken_port_fails_without_pidfile | IF-SERVERLIFE-001, IF-PROJECTCLI-001 |
| AC-FR0350-02 | integration | tests/integration/test_server_lifecycle.py::test_serve_outside_project_refused | IF-SERVERLIFE-001, IF-GLOBALCLI-001 |
| AC-FR0351-01 | integration + e2e | tests/integration/test_install_versions.py::test_explicit_version_and_global_project_split + tests/e2e/test_install_journey.py::test_install_old_version_while_global_new | IF-INSTALL-001, IF-PROJECTCLI-001, IF-VERSIONVIS-001 |
| AC-FR0351-02 | integration | tests/integration/test_install_targets.py::test_empty_dir_and_existing_project | IF-INSTALL-001, IF-PROJLAYOUT-001 |
| AC-FR0352-01 | integration | tests/integration/test_install_overwrite.py::test_managed_files_replaced_user_files_byte_identical | IF-INSTALL-001, IF-PROJLAYOUT-001 |
| AC-FR0352-02 | integration | tests/integration/test_install_overwrite.py::test_user_modified_managed_file_overwritten | IF-INSTALL-001, IF-PROJLAYOUT-001 |
| AC-FR0353-01 | integration + e2e | tests/integration/test_install_versions.py::test_global_upgrade_keeps_locked_project + tests/e2e/test_install_journey.py::test_upgrade_global_project_stays_old | IF-PROJECTCLI-001, IF-VERSIONVIS-001, IF-PROJLAYOUT-001 |
| AC-FR0353-02 | integration | tests/integration/test_install_versions.py::test_no_auto_upgrade_or_lock_change | IF-PROJECTCLI-001, IF-PROJLAYOUT-001 |
| AC-FR0354-01 | integration | tests/integration/test_install_versions.py::test_failure_semantics_and_retry + tests/integration/test_install_overwrite.py::test_reinstall_idempotent | IF-INSTALL-001, IF-INSTALLPKG-001, IF-PROJLAYOUT-001 |
| AC-FR0355-01 | e2e | tests/e2e/test_run_workflow_ui.py::test_stage_path_states_times_and_gate_wait | IF-STAGEPATH-001, IF-WEBUI-001 |
| AC-FR0355-02 | e2e | tests/e2e/test_run_workflow_ui.py::test_stage_path_degradation_readable | IF-STAGEPATH-001, IF-WEBUI-001 |
| AC-FR0356-01 | e2e | tests/e2e/test_run_workflow_ui.py::test_stage_workflow_aggregated_folds | IF-STAGEWORKFLOW-001, IF-WEBUI-001 |
| AC-FR0357-01 | integration + e2e | tests/integration/test_run_rounds.py::test_rounds_list_and_detail + tests/e2e/test_run_workflow_ui.py::test_per_round_detail_lazy_accordion | IF-ROUNDS-001, IF-QUERY-001, IF-WEBUI-001 |
| AC-FR0357-02 | integration + e2e | tests/integration/test_run_rounds.py::test_missing_thinking_degrades + tests/e2e/test_run_workflow_ui.py::test_round_thinking_missing_message | IF-ROUNDS-001, IF-WEBUI-001 |
| AC-FR0358-01 | e2e | tests/e2e/test_run_workflow_ui.py::test_run_card_local_time_and_heartbeat_not_reset | IF-RUNCARD-001, IF-WEBUI-001 |
| AC-FR0359-01 | integration + e2e | tests/integration/test_review_decision_contract.py::test_return_writes_thread_then_review_event + tests/e2e/test_run_workflow_ui.py::test_review_two_decisions_thread_and_continue | IF-REVIEWDECISION-001, IF-GATEUI-001, IF-DOCSWRITE-001 |
| AC-FR0359-02 | e2e | tests/e2e/test_run_workflow_ui.py::test_normal_gate_terse_exception_keeps_reason_impact_and_inline_failure | IF-REVIEWDECISION-001, IF-AWAIT-001, IF-WEBUI-001 |
| AC-FR0360-01 | integration | tests/integration/test_static_cache.py::test_text_assets_no_cache_and_304 + tests/integration/test_static_cache.py::test_api_semantics_unchanged | IF-STATICCACHE-001, IF-QUERY-001 |
| AC-NFR0159-01 | integration | tests/integration/test_server_lifecycle.py::test_background_survives_shell_exit_and_stop_unreachable + tests/integration/test_server_lifecycle.py::test_windows_foreground_branch | IF-SERVERLIFE-001, IF-PROJECTCLI-001 |
| AC-NFR0160-01 | integration + e2e | tests/integration/test_static_cache.py::test_text_assets_no_cache_and_304 + tests/e2e/test_run_workflow_ui.py::test_rounds_lazy_first_paint | IF-ROUNDS-001, IF-STATICCACHE-001 |

### 8.1 Arrival-green guard nodes（到达即绿守卫清单）

以下节点是到达即绿守卫：它们验证设计期已交付的包/版本/token 事实，或 v0.12 OOB 已落地的读取面/UI 行为。它们从红窗必填集排除、不作为 task 验收锚点声明，并在每次执行中必须保持绿（失败即真回归，fail-closed）。本清单是守卫集合的唯一权威。

- `tests/integration/test_install_package.py::test_scaffold_package_resources_declared` — 设计期 package-data/asset 存在性（AC-FR0348-01 的声明半边）— IF-INSTALLPKG-001
- `tests/integration/test_install_package.py::test_version_bump_matches` — pyproject/`tracks/__init__.py` 的 0.12.0 一致（version gate 前置）— IF-VERSION-001
- `tests/integration/test_install_package.py::test_subcommand_tokens_registered` — USAGE 与 `TRAC_SUBCOMMANDS` 的 install/version parity — IF-GLOBALCLI-001
- `tests/integration/test_install_package.py::test_wheel_carries_capability_resources` — 官方 wheel 的资源清单（AC-FR0348-01 的包内容半边）— IF-INSTALLPKG-001
- `tests/integration/test_install_package.py::test_global_trac_help_zero_repo` — 既有 console script 的零仓库 `--help`（AC-FR0349-01 半边）— IF-GLOBALCLI-001
- `tests/e2e/test_install_journey.py::test_global_trac_available_anywhere` — 隔离 prefix 安装后的全局可用（AC-FR0349-01 半边）— IF-GLOBALCLI-001
- `tests/e2e/test_run_workflow_ui.py::test_stage_path_states_times_and_gate_wait` — 已交付阶段轨道/节点详情（AC-FR0355-01）— IF-STAGEPATH-001, IF-WEBUI-001
- `tests/e2e/test_run_workflow_ui.py::test_stage_path_degradation_readable` — 已交付降级文本（AC-FR0355-02）— IF-STAGEPATH-001, IF-WEBUI-001
- `tests/e2e/test_run_workflow_ui.py::test_stage_workflow_aggregated_folds` — 已交付阶段工作流聚合（AC-FR0356-01）— IF-STAGEWORKFLOW-001, IF-WEBUI-001
- `tests/integration/test_run_rounds.py::test_rounds_list_and_detail` — 已交付 rounds API（AC-FR0357-01 半边）— IF-ROUNDS-001, IF-QUERY-001
- `tests/e2e/test_run_workflow_ui.py::test_per_round_detail_lazy_accordion` — 已交付按轮手风琴（AC-FR0357-01 半边）— IF-ROUNDS-001, IF-WEBUI-001
- `tests/integration/test_run_rounds.py::test_missing_thinking_degrades` — 已交付 thinking 降级（AC-FR0357-02 半边）— IF-ROUNDS-001
- `tests/e2e/test_run_workflow_ui.py::test_round_thinking_missing_message` — 已交付缺失说明（AC-FR0357-02 半边）— IF-ROUNDS-001, IF-WEBUI-001
- `tests/e2e/test_run_workflow_ui.py::test_run_card_local_time_and_heartbeat_not_reset` — 已交付卡片/本地时间/心跳（AC-FR0358-01）— IF-RUNCARD-001, IF-WEBUI-001
- `tests/e2e/test_run_workflow_ui.py::test_normal_gate_terse_exception_keeps_reason_impact_and_inline_failure` — 已交付正常门简明/异常门保留/内联失败（AC-FR0359-02）— IF-REVIEWDECISION-001, IF-AWAIT-001, IF-WEBUI-001
- `tests/integration/test_static_cache.py::test_text_assets_no_cache_and_304` — 已交付 no-cache/304（AC-FR0360-01 半边）— IF-STATICCACHE-001
- `tests/integration/test_static_cache.py::test_api_semantics_unchanged` — API 无 no-cache/语义不变（AC-FR0360-01 半边）— IF-STATICCACHE-001, IF-QUERY-001
- `tests/e2e/test_run_workflow_ui.py::test_rounds_lazy_first_paint` — 已交付懒加载（AC-NFR0160-01 半边）— IF-ROUNDS-001, IF-WEBUI-001

说明：AC-FR0359-01 的退回线程写入不在守卫清单——当前 HEAD 只把 comments 放进 `human.review`，没有文档线程；该节点是合法 Red，直到 Devon 按 §1h 恢复线程写入。安装/委派/服务生命周期的其余节点均为合法 Red。v0.11 既有回归测试（Releases/时间线/awaiting）保持既有权重，不列入本清单；其需要按 OOB 现状更新的断言见 §11。

---

## 9. E2E Happy Paths

### 9.1 安装与多版本旅程（CLI，非 `ui`）

`tests/e2e/test_install_journey.py`：在隔离 prefix 安装候选 wheel → 任意非项目目录 `trac --help`（exit 0）→ 空目录 `trac install` → `trac version` 报项目版本 → `trac status` 可用 → 已有项目加装且用户文件不变 → 第二版本 wheel `trac install --version <old> --wheel` → 项目 `trac version`/`trac serve --status`/`/healthz` 报旧版本 → 升级全局 prefix 到新版本 → 旧项目版本/行为不变。

### 9.2 项目后台服务旅程（CLI，非 `ui`）

`tests/e2e/test_install_journey.py::test_project_server_background_roundtrip`：已安装项目 `trac serve --repo <root> --background` → 打印 url/pid/version → `/healthz` version=项目版本 → 发起 shell 退出 → 仍可连接 → `trac serve --status` running → `trac serve --stop` → 连接失败。

### 9.3 OOB 工作台旅程（Playwright，`ui` marker）

`tests/e2e/test_run_workflow_ui.py`：登录+名字采集 → 打开 seeded run → 阶段轨道 13 阶段/五态/当前节点 → 选中节点 → 详情 start/end + owning surface → 阶段工作流聚合图（摘要/折叠/人工门）→ 展开执行明细 → 按轮四区与思考缺失说明 → 运行卡片中文/耗时/本地时间/门控等待 → review 门两决策 → 退回意见写入文档线程并推进 → 正常门无 reason/impact、异常门保留、失败内联。全程 data-testid、同一 document、不以 API 替代 UI 操作。

---

## 10. Fail-closed / 注入矩阵（integration 为主，浏览器层注入归 ui e2e）

| # | 注入 | 必须观察的阻断/行为 |
|:--|:--|:--|
| 1 | `trac install` 参数缺失/未知 flag | `usage`；stderr 指引；无写盘 |
| 2 | `<path>` 是文件 | `target_not_directory`；无写盘 |
| 3 | 非空非 Git 目录 | `not_a_git_repo`；不 `git init`、不写 tracks 文件 |
| 4 | `--version` 非法格式 | `version_invalid`；无写盘 |
| 5 | 显式版本≠运行版本且无 `--wheel` | `version_unavailable`；stderr 指引官方 wheel |
| 6 | wheel METADATA 版本≠`--version` | `version_mismatch`；无 receipt |
| 7 | wheel 缺 agents/skills/templates/assets/opencode.json | `package_incomplete`；无 receipt |
| 8 | wheel 依赖不在解释器环境 | `dependency_missing`；无 receipt |
| 9 | 安装中目录替换/复制失败 | `install_failed`；staging 清理；旧 receipt/托管目录不变 |
| 10 | 非 Git 身份导致 commit 失败 | `git_commit_failed`；文件完整但报失败、可重试 |
| 11 | post-install health check 失败 | `health_check_failed`；不输出成功行 |
| 12 | 同版本重复 install | 成功；无重复文件/空提交；receipt 一致 |
| 13 | 用户改动托管文件后重装 | 托管文件恢复为所装版本；用户文件不变 |
| 14 | receipt 被篡改/删除 | `install_corrupt`；不退化全局版本；指引重装 |
| 15 | 项目外 `trac serve` | `not_installed`；无 `service.started`、端口不监听 |
| 16 | `--background` 在 Windows 判定 | `unsupported_platform`；不 spawn |
| 17 | 后台启动端口被占用 | `background_failed` + 日志尾；无 pidfile |
| 18 | `--stop` 遇陈旧 pidfile/PID 复用 | 清理或 `stop_refused`；不误杀无关进程 |
| 19 | 后台服务存活时 shell 退出 | `/healthz` 仍通；stop 后连接失败 |
| 20 | 全局升级后旧项目 | receipt/sha/version 不变；项目命令/healthz 仍旧版本 |
| 21 | review 退回缺 comments | `validation_failed`；无线程、无 `human.review` |
| 22 | review 退回线程写失败 | `review_thread_write_failed`；门不推进；UI `review-feedback` data-failed=true |
| 23 | review 重复提交 | 幂等键去重/awaiting 前置；恰一条事件、恰一个线程 |
| 24 | rounds 输入/输出 blob 缺失 | input null / output `{"unavailable":true}`；不 500 |
| 25 | 思考日志轮转 | `thinking_available=false`；可读说明；输入输出仍可读 |
| 26 | 静态文件内容变更后刷新 | 返回新字节；未变更文件 304；API 无 `no-cache` |
| 27 | 阶段数据缺失/请求失败 | 可读降级，不空白、不伪造时间 |
| 28 | UI 写失败（route abort） | 内联失败反馈；不伪报成功 |

---

## 11. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | `tests/e2e/test_awaiting_ui.py::test_awaiting_panel_reason_impact_action` | 正常 triage 门改为断言**无** `gate-reason`/`gate-impact`；异常门（escalation/rollback）的 reason/impact 断言移到新用例/夹具 | FR-0359-02 正常流门不堆模板文案（OOB ffaeed4 行为） |
| 2 | `tests/e2e/test_awaiting_ui.py::test_stage_review_completed_in_panel` | 把 `gate-action-review` 改为 `review-approve`（review 面已换成两决策按钮） | OOB 评审两决策面 |
| 3 | `tests/e2e/test_awaiting_ui.py::test_review_round_doc_write` | 移除已不存在的 `review-doc-editor`/`review-doc-save`/`review-comment-*` 用法；评审轮编辑走 Docs 窗口，inline comment 走 `discussion-anchor-comment` | OOB 移除内联文档编辑器（编辑留在 Docs 窗口） |
| 4 | `tests/e2e/test_awaiting_ui.py::test_ui_writes_carry_headers` | triage 后的 review 门用 `review-approve` 触发写请求；仍断言 CSRF/Idempotency-Key | OOB 评审两决策面 |
| 5 | `tests/integration/test_distribution.py` | wheel 资源断言加入 `tracks/assets/opencode.json`；增加非项目 cwd 的 `trac --help` 断言 | FR-0348/FR-0349 官方包内容与全局可用 |
| 6 | `tests/e2e/test_run_timeline_ui.py` | 保持既有阶段轨道/固定详情栏断言；如新增 `stage-path`/`stage-workflow-*` 节点，仅追加断言，不改旧 testid | OOB 面追加、既有面不回退 |
| 7 | `tests/integration/test_timeline_full.py`、`tests/e2e/test_releases_ui.py`、`tests/e2e/test_workbench_ui.py` | 零行为变更，回归守卫保持 | API/UI 追加式，v0.12 不改这些面 |

Devon 的实现层测试更新由 RGR/coverage 自辖；本表只列 Shield 可见的既有资产更新，不处方实现层测试文件/函数。
