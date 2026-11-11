---
envelope: tracks-envelope:v2
spec_id: SPEC-012
created: 2026-10-27
status: draft
sha:
---

# v0.12.1 发布后修复（hotfix-262） — Test Plan

- **Related acceptance**: `.tracks/projects/v0.12/acceptance.md`（本 delta 锚定跨版本 AC，逐字引用见 §8）
- **Related interfaces**: `.tracks/projects/v0.12-hotfix-262/interfaces.md` (assertion basis — see §6.5)
- **Related design delta**: `.tracks/projects/v0.12.1/design-delta.md`（已批准，本文为其测试层落地）

## 1. Stance and Boundaries

### 1.1. Black-box Statement

This test plan only declares test methods that are **observable from outside the system**. Observable objects are limited to:

- CLI endpoints（`trac run`、`trac release`、`trac hotfix/triage`、`trac status/replay/report` 及退出码）
- 事件流（`full.executed`/`evidence.reused`/`release.previewed`/`release.decided`/`prism.verdict`/`verdict.failed`/`attention.required`/`stage.exited/entered`/`refs.cleaned`）
- HTTP 投影（`/api/runs/{id}/ac-chain`、`/api/runs/{id}/timeline?slim=1`）与响应时间/payload 大小
- 子进程 env（探针打印的子进程环境变量集）
- Git 引用（`git for-each-ref refs/trac/tmp refs/trac/rgr`）
- UI entry points（Playwright + `data-testid`：stage 节点选中环、backlog 标题、Todos inbox 卡片、legacy run 时间线）

### 1.2. Non-observable Objects (tests do not directly depend on)

- 谓词内部实现（`_evidence_key` 的内部映射表、visit 守卫的内部状态）
- reducer/投影的中间数据结构
- 安装/staging 内部布局

**Observable contract**: AC 需要观察的内部状态必须由 interfaces.md §4 的出口承载（§6.5 闭环）；本 delta 对应出口：replay 事件判定（§4a）、子进程 env 探针（§4b）、HTTP/UI 性能与 DOM（§4c）。

### 1.3. Cheating Patterns (CI enforced interception)

| # | Cheating Pattern | Typical Symptom |
|:--|:--|:--|
| 1 | Change assertions to fit impl | 把「pass+blocking 被拒」改成断言「verdict 被降级为 revise」 |
| 2 | Use skip to evade validation | 用 skip 跳过跨语言探针或 retry_tail 重入，声称手工验证 |
| 3 | Assertion degradation | 只断言 `returncode != 0`，不检查封闭 reason/固定文案/事件缺席 |
| 4 | try/except: pass | 吞掉子进程/事件断言失败 |
| 5 | Over-mocking | mock kernel/executor/谓词内部，测 mock 行为而非真实事件流 |
| 6 | Ground truth uses impl | 用被测 `is_authoritative_full_f` 输出反推期望 |
| 7 | Hardcoded expected values | 只因当前 payload 形状写死，不校验 INV-1 各独立拒绝理由 |
| 8 | Trivial pass | `assert True` / 只断言事件存在不统计次数 |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**：每个测试函数 R-1 行带 `# AC-FRXXXX-YY@<ver> TRACKS-TRACE ...`；hotfix 锚定引用必须逐字使用所指版本的 acceptance AC id + `@version` 后缀（跨版本引用语法 IF-HOTFIX-004）；CI 扫描每个测试 ≥1 AC、每条锚定 AC ≥1 测试。
2. **Assertion taboos**：同模板禁令（无唯一 `assert True`、无 `try/except: pass`、无无 issue 链接的 skip）。
3. **Test change classification**：PR 必填类别；禁止「impl 与 spec 不一致 → 改测试」。
4. **Testability fallback**：不 mock 内部强行通过；登记 testability gap。

### 1.5. Test Division of Labor

- **Unit tests**：Devon 在 R-G-R 内随实现提交；谓词矩阵（`is_authoritative_full_f`、`has_blocking_findings`、`host_process_env`、`_finalize_full_round`、`_evidence_key`）的细粒度组合由 Devon unit 义务与覆盖率门禁承载；本计划 §8 不处方 unit 行（本 hotfix 全部锚定 AC 均有 integration/e2e 出口，不走 IF-HOTFIX-007 的 unit-only 放行）。
- **Integration tests**：Shield——谓词接缝、replay 回归、ingest 拒绝、release 门禁、attention/凭据、阶段事件、cleanup、UI 投影性能。
- **E2E tests**：Shield——缺 token 的 run_loop 停顿旅程（非 `ui`）与 UI 旅程（`ui` marker：legacy 时间线、选中环、backlog、todos）。
- **Ground Truth**：不适用（§3）。
- **Review ownership**：全部测试变更由 test lead 评审；replay 切片夹具重点核对与 v0.12.0 真实事件的一致性。

---

## 2. Test Environment

### 2.1. Directory Layout

```text
tests/
├── unit/          # Devon R-G-R unit tests（谓词矩阵等）
├── integration/   # Shield: test_hotfix262_*.py
├── e2e/           # Shield: test_hotfix262_attention_journey.py（非 ui）、test_hotfix262_release_ui.py（ui）
├── assets/v0.12-hotfix-262/  # Shield-owned: v0.12.0 事件切片、legacy preview blob、注入 payload
├── counterexamples/v0.12-hotfix-262/  # Shield-owned: per-node minimal patches + kill-manifest.json
└── ground_truth/  # Not applicable (§3)
```

### 2.2. Naming Conventions

- File: `test_hotfix262_<scenario>.py`
- Function: `test_<scenario>__<subscenario>` 或既有 `test_ac_` 前缀风格；每 AC 一行 TRACKS-TRACE marker（带 `@version` 后缀）。

### 2.3. Execution

- **Offline**：replay 切片/注入样本全部离线；live GitHub 通道不属本 delta（继承既有 live 分层，见 §6）。
- **Execution order**：unit → integration → e2e。
- **CI**：FULL 每次 push；`ui` marker 由 `ui-e2e` job 承载；电池墙钟继承。

### 2.3.1. Test Execution Contract (`.tracks/projects/project.toml`)

逐字继承 v0.12（零 bytes 变更）：

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

### 2.4. Test Data

- **Source**：`tests/assets/v0.12-hotfix-262/`（Shield 写域）：v0.12.0 事件切片（含 seq 7342/7362/5896/7473 形状的重放样本，以合成方式复刻其 payload 特征：7362-shape=`serves_as_full_f=false, full_f_eligible=true, round=FULL_F, execution_commit=44c14de…`；5896-shape=`serves_as_full_f=true, full_f_eligible=false` 外 commit）、legacy `release.previewed` blob（单 `"prism"` 键）、pass+blocker 注入 payload（四个 ingest scope）、缺 token 环境样本、legacy run 事件种子（无显式阶段事件）。
- **Reproducible**：重放断言以固定 payload 形状与固定 SHA 为准；性能断言以有界阈值（<5ms / <50KB / <500ms）为准。
- **Sensitive data**：canary 密钥纪律继承；凭据测试只用 env 注入，不提交真实 token。
- **Counterexample data**：`tests/counterexamples/v0.12-hotfix-262/kill-manifest.json` 是 Shield 交付的 required-node 绑定清单；每个 binding 指向一个最小 git patch，并记录该 patch 在当前 head 上经 apply → 目标节点失败 → restore 的 kill 证据。该清单不是产品接口，也不是 Archer scaffold。

### 2.5. Installation & Isolation

继承 v0.12 §2.5（安装方法不变；本 delta 不改变安装面）。凭据/attention e2e 在隔离临时项目与 env 快照（保存/恢复 `os.environ`）内执行，不污染系统环境。

---

## 3. Ground Truth Method

本 delta 判定**不适用**独立 ground-truth 脚本。验证对象是 fail-closed 谓词判定、事件发射次数/顺序、进程 env、HTTP 性能与 UI DOM——预期值来自 INV-1..INV-10 的合同文本、固定事件切片与封闭枚举，无算法/数值计算对象。谓词矩阵的期望值在测试体内按 INV 文本显式枚举（每个独立拒绝理由单独一行用例），不 import 被测模块反推；不创建 `tests/ground_truth/`。

---

## 4. Test Scope

本计划覆盖 assignment 锚定的 26 条跨版本 AC（anchor_acs，逐字见 §8）；它们分别登记于 v0.4/v0.5/v0.8/v0.9/v0.10/v0.11 的 acceptance.md，本 hotfix 不分配新 AC 编号（IF-HOTFIX-004）。Validity/Testability/Decision 均为绿（所引 AC 在原版本已锁定）。

| Valid | Testable | Decided |
|:------|:---------|:--------|
| ✅    | ✅       | ✅      |

范围外（design-delta §6 显式 OUT）：`release_evidence._REQUIRED_PHASES` 阻塞门禁（留 v0.13）、跨语言 test/gate adapter 全面化（留 v0.13）、agent→model 映射、全量 observability、测试提速/mutation/phase0 重设计、自动凭据回退（用户决定 1 否决）。

---

## 5. Acceptance Criteria

1. Unit test coverage：继承 registry 门禁（`--fail-under=89`）；新模块（full_f_authority/review_findings/host_env）由 Devon unit 义务覆盖。
2. interfaces.md 每个 `modules` 2+ 的新合同（IF-FULLF-001 / IF-RELEV-001 / IF-BLOCKREV-001 / IF-ATTN-001 / IF-HOSTENV-001 / IF-STAGE-001 / IF-UIPERF-001 / IF-CLEANUP-001 全部）至少一条 integration happy + 关键 error/edge。
3. e2e happy path：缺 token attention 停顿旅程（非 ui）与 UI 旅程（legacy 时间线/选中环/backlog/todos，`ui` marker）全绿。
4. §8 的 26 条锚定 AC 全部有 integration/e2e layer 与已注册 IF- 归属（上列 8 个本 delta 标识）。
5. §6 外部依赖分层：L1/L2 默认 CI 全绿；L3（真实 POSIX detached 子进程/跨语言探针在本机 OS）在 CI 本地面默认跑；GitHub live 通道继承且不新增 AC 重叠。
6. 性能门槛可观察：ac-chain 短路 <5ms、slim payload <50KB / p95 <500ms（在 CI 固定负载夹具下断言）。

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描：本 delta 涉及两类外部面——(a) GitHub API 凭据（attention/issue 创建），(b) 宿主子进程工具链（跨语言探针）。(a) 的 live 通道继承既有 `tests/e2e_live/` 分层，本 delta 不新增 live AC；(b) 是本机确定性面（真实子进程，无网络）。

### 6.1. Three Unavoidable Constraints

| # | Constraint | Consequence |
|:--|:--|:--|
| C1 | Test environment cannot connect to production dependencies | GitHub 面用 env 注入（缺 token）构造失败路径；不做真实 issue 创建 |
| C2 | Cannot wait for real time | run_loop 停顿用有界迭代计数断言；性能阈值有界 |
| C3 | Cannot mock framework internals | 谓词/ingest/投影/ kernel reducer 均为被测对象；只替换外部输入（事件切片、env、payload 文件） |

### 6.2. Stance: Controllable vs Mock

- **可替换**：事件切片文件、legacy preview blob、env 变量、payload JSON、端口号、临时 git 仓。
- **不可 mock**：`is_authoritative_full_f`/`has_blocking_findings`/`host_process_env` 本体、ingest handler、release preview/authorization、run_loop preflight、publish/milestone 发射、投影与 timeline 合成、UI DOM。

### 6.3. Three-Layer Test Pyramid

| Layer | Name | Time | Speed | Coverage | Default Run |
|:------|:-----|:-----|:------|:---------|:------------|
| L1 | Deterministic sim | 注入事件/env | Seconds | 谓词矩阵经事件切片回放；ingest 注入；release 门禁；投影性能 | ✅ CI default |
| L2 | Contract sim | 注入事件/env | Seconds | 跨语言探针经 10 站点（真实子进程，本机工具链在场断言，缺席则按工具链在场前提断言缺席原因）；legacy preview blob | ✅ CI default |
| L3 | Real env smoke | Real OS | Real | POSIX detached 后台语义不属本 delta；GitHub live 继承既有通道（`LIVE_SKIPPED` 语义不变） | ❌ 继承既有 weekly/manual，不新增 |

### 6.4. Responsibility Contract of Test Infrastructure

| # | Component | Responsibility (external) | Boundary (what it does not implement) |
|:--|:--|:--|:--|
| 1 | Event slice fixture | 构造/加载 v0.12.0 形状的事件切片（7362/5896/7342/7473） | 不 import 被测谓词反推期望 |
| 2 | Env probe fixture | 经指定站点 spawn 子进程并 dump 子进程 env | 不实现 allowlist 逻辑 |
| 3 | Credential fixture | 保存/清除/恢复 `GITHUB_TOKEN`/`GITHUB_ACCESS_TOKEN`/`GH_TOKEN` | 不触网 |
| 4 | Legacy blob fixture | 提供单 `"prism"` 键的 release.previewed blob | 不做 DB 迁移 |
| 5 | Perf fixture | 固定规模事件种子 + 计时/字节度量 | 不放宽阈值 |
| 6 | Browser fixture | Chromium + `data-testid` | 不替代 UI 操作 |

### 6.5. Assertion Basis — Closure with interfaces.md

Test assertions **may only** land on interfaces.md §4 的出口：事件流判定（§4a）、子进程 env（§4b）、HTTP/UI（§4c）。隔离 fixture 可以通过受控的仓库/事件/进程装配建立前置状态，但 fixture 装配本身不是断言；不得断言内部对象、私有方法、内部返回值或 mock 内核。AC 需要的状态若无出口，回修 interfaces/acceptance。

### 6.6 Required-node counterexample contract（TEST-3）

> **Prism [RESOLVED]:** [BLOCKER] TEST-3 contract mismatch: test-plan §6.6 (around lines 191-213) declares every counterexample binding must contain minimum fields ac, patch, test, kill, and restore, but the delivered tests/counterexamples/v0.12-hotfix-262/kill-manifest.json bindings contain ac, patch, test, and result only; no per-binding kill or restore fields. Either update the manifest to satisfy this declared schema with current-head evidence, or revise §6.6 to match the actual machine-readable artifact without weakening the required apply/run/restore proof.
>> **Archer:** 已按 TEST-3 finding 修订 §6.6：binding 最小字段与实际 kill-manifest 对齐为 ac/patch/test/result（result 必须为 killed）；if_ref/mutation 保留为附加 provenance。apply→运行绑定节点→行为断言失败→restore 的证据要求未削弱，改由 manifest 顶层 verification.session、verification.apply_check、verification.per_node_coverage 与当前 head 运行记录承载。当前 manifest 已有 33 个 binding，覆盖全部 26 条 anchor AC，且 result=killed。
>> **Prism:** 已核对修订内容与当前 artifact：binding 字段已与 kill-manifest 对齐，apply/run/restore 证据要求仍明确且当前 head 验证通过。该 blocker 已实质解决。

每个 §8 integration/e2e required node 必须在 Shield 交付中拥有至少一个 `kill-manifest.json` binding；共享一个 patch 的多个节点逐节点登记，不把“一个 patch 覆盖多个节点”当作缺失豁免。当前机器可读 binding 的最小字段与实际 artifact schema 对齐：`ac`（完整 `AC-…@vX.Y`）、`patch`（相对 `tests/counterexamples/v0.12-hotfix-262/` 的 patch 路径）、`test`（§8 节点）、`result`（必须为 `killed`）；`if_ref` 与 `mutation` 可作为附加 provenance 字段。`kill` 与 `restore` 不是 binding 字段：apply/run/restore 证据由 manifest 顶层 `verification.session`、`verification.apply_check`、`verification.per_node_coverage` 及当前-head运行记录共同承载；其中 session 的规范循环是 apply → 运行绑定节点 → 断言行为失败 → restore，`result=killed` 是逐 binding 的结果。Runtime/Prism 以当前 head 实际 apply 与单点运行核验，过时、`result` 非 `killed` 或 `n/a` binding fail-closed。

本版 26 条 anchor AC 的 binding 归属如下（同一 AC 可有多个独立 failure mode；每个列出的 patch 都必须保留在 manifest 中）：

| AC | counterexample patch(es) |
|---|---|
| AC-FR0267-01@v0.8 | `ce_ac-fr0267-01_candidate_binding.patch` |
| AC-FR0268-01@v0.8 | `ce_ac-fr0268-01_full_f_authority.patch` |
| AC-FR0268-02@v0.8 | `ce_ac-fr0268-02_drift_reuse.patch` |
| AC-FR0268-03@v0.8 | `ce_ac-fr0268-02_stale_reuse.patch` |
| AC-NFR0143-02@v0.8 | `ce_ac-fr0315-01_host_env.patch` |
| AC-FR0270-02@v0.8 | `ce_ac-fr0270-02_full_f_credit.patch` |
| AC-FR0273-01@v0.8 | `ce_ac-fr0273-01_evidence_key.patch` |
| AC-FR0273-02@v0.8 | `ce_ac-fr0273-02_legacy_readback.patch` |
| AC-FR0271-01@v0.8; AC-FR0272-01@v0.8; AC-FR0040-03@v0.4; AC-FR0140-02@v0.5 | `ce_ac-fr0271-01_blocking_findings.patch` |
| AC-FR0271-02@v0.8; AC-FR0272-02@v0.8 | `ce_ac-fr0271-02_blocking_status.patch` |
| AC-FR0274-02@v0.8; AC-FR0272-02@v0.8 | `ce_ac-fr0272-02_security_gate.patch` |
| AC-FR0283-02@v0.8 | `ce_ac-fr0283-02_attention_dedup.patch` |
| AC-FR0298-02@v0.9 | `ce_ac-fr0298-02_loop_preflight.patch` |
| AC-FR0329-02@v0.10 | `ce_ac-fr0329-02_fetch_classification.patch` |
| AC-FR0276-01@v0.8 | `ce_ac-fr0276-01_stage_events.patch`; `ce_ac-fr0276-01_cleanup.patch` |
| AC-FR0335-01@v0.11 | `ce_ac-fr0335-01_legacy_synthesis.patch`; `ce_ac-fr0335-01_legacy_window.patch` |
| AC-FR0335-02@v0.11 | `ce_ac-fr0335-02_slim_evidence.patch` |
| AC-NFR0150-02@v0.9 | `ce_ac-nfr0150-02_ac_chain_shortcircuit.patch` |
| AC-FR0333-01@v0.11 | `ce_ac-fr0333-01_todo_attention.patch` |
| AC-FR0340-01@v0.11 | `ce_ac-fr0340-01_backlog_ulid.patch`; `ce_ac-fr0340-01_backlog_label.patch` |
| AC-FR0307-01@v0.9 | `ce_ac-fr0307-01_todo_fields.patch`; `ce_ac-fr0307-01_todo_card_text.patch` |
| AC-FR0315-01@v0.9 | `ce_ac-fr0315-01_host_env.patch`; `ce_ac-fr0315-01_host_env_sites.patch` |

---

## 7. CI Gate

- **Required check**：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`、`ui-e2e`（七名继承，零 workflow 变更）；milestone `release-evidence` 硬门禁继承。
- **Validation items**：
  - AC reference closure（每条锚定 AC ≥1 测试、每个测试 ≥1 AC；跨版本引用存在性校验 IF-HOTFIX-004）
  - Anti-pattern static scan（§1.3）
  - Coverage 门槛（`--fail-under=89`）
  - UI 纪律静态面（`ui` 标记测试 `data-testid` 绑定扫描，继承）
- **failure semantics**：任一 required check 失败即阻断；Agent 自述不构成证据；live 通道缺凭据 `LIVE_SKIPPED` 语义继承；ui-e2e 缺浏览器即红。

---

## 8. AC Coverage

锚定集合解析合同（本 hotfix 的显式设计决定，M-DESIGN EXIT 判据）：

1. **真相来源**：26 条锚定 AC 分别登记于 `v0.4/v0.5/v0.8/v0.9/v0.10/v0.11` 的 `acceptance.md`；delta §8 行的每条 `AC-…@vX.Y` 引用以**其所指版本**的 `acceptance.md` 为存在性真相（IF-HOTFIX-004，与 `tracks/executor/hotfix.validate_anchor_refs`、`tracks/checks/trace.check_hotfix_plan_closure` 的既有 per-version 解析同语义）。baseline（v0.12）acceptance **不是**锚定集合的存在性真相。
2. **覆盖要求**：`test_tasks` 合同对本 delta plan 的 required 集合 = §8 行内出现的全部 `AC-…@vX.Y` 引用；每行必须有 `layer ∈ {integration,e2e}` 与已注册 `IF-` 归属（本表全部满足）。
3. **校验器行为（machine contract，已实现）**：hotfix delta plan 的 `unknown-AC` 判定先把每行的 `@vX.Y` 解析到 `projects_dir/<vX.Y>/acceptance.md` 再判定存在性（`hotfix_test_sources.resolve_ac_sources`；`test_tasks._plan_acceptance` 供 EXIT 校验与 `parse_test_tasks` 共用，OOB commit `90e4879`）。只读 baseline acceptance 会把全部锚定行误判为 unknown，属 fail-closed 方向错误（拒绝合法输入）。判定序列：extract refs → per-version 存在性 → 行内 AC ∈ 并集 → 继续 layer/IF 校验。
4. **失败语义**：仅当引用在**其所指版本**的 acceptance 中不存在（引用不实）或语法不合 IF-HOTFIX-004 时，该行才判无效；不得因引用版本 ≠ 目标版本而拒绝。

上表每行的 AC id 逐字使用跨版本引用（IF-HOTFIX-004 语法）；`ui` 标记的 e2e 由 ui-e2e required check 承载；e2e 项为终态锚点。

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0267-01@v0.8 | integration | tests/integration/test_hotfix262_full_f_authority.py::test_candidate_binding_from_frozen_not_manifest | IF-FULLF-001 |
| AC-FR0268-01@v0.8 | integration | tests/integration/test_hotfix262_full_f_authority.py::test_full_f_reuse_authoritative_matrix | IF-FULLF-001 |
| AC-FR0268-02@v0.8 | integration | tests/integration/test_hotfix262_full_f_authority.py::test_drift_or_stale_forces_full_rerun | IF-FULLF-001 |
| AC-FR0268-03@v0.8 | integration | tests/integration/test_hotfix262_full_f_authority.py::test_stale_full_f_not_reused | IF-FULLF-001 |
| AC-NFR0143-02@v0.8 | integration | tests/integration/test_hotfix262_host_env.py::test_cross_language_toolchain_env_allowlist | IF-HOSTENV-001 |
| AC-FR0270-02@v0.8 | integration | tests/integration/test_hotfix262_release_evidence.py::test_missing_required_keys_block_release | IF-RELEV-001 |
| AC-FR0273-01@v0.8 | integration | tests/integration/test_hotfix262_release_evidence.py::test_preview_requires_full_key_set | IF-RELEV-001 |
| AC-FR0273-02@v0.8 | integration | tests/integration/test_hotfix262_release_evidence.py::test_legacy_single_prism_key_preview_readable | IF-RELEV-001 |
| AC-FR0271-01@v0.8 | integration | tests/integration/test_hotfix262_review_ingest.py::test_verify_final_pass_with_blocker_rejected | IF-BLOCKREV-001 |
| AC-FR0271-02@v0.8 | integration | tests/integration/test_hotfix262_review_ingest.py::test_downstream_gates_treat_blocking_as_failed | IF-BLOCKREV-001 |
| AC-FR0272-01@v0.8 | integration | tests/integration/test_hotfix262_review_ingest.py::test_security_pass_with_blocker_rejected_and_replay_7342 | IF-BLOCKREV-001 |
| AC-FR0272-02@v0.8 | integration | tests/integration/test_hotfix262_review_ingest.py::test_malformed_or_blocking_security_blocks_release | IF-BLOCKREV-001 |
| AC-FR0274-02@v0.8 | integration | tests/integration/test_hotfix262_release_evidence.py::test_release_rejected_on_gate_failed_or_stale | IF-RELEV-001 |
| AC-FR0040-03@v0.4 | integration | tests/integration/test_hotfix262_review_ingest.py::test_m_test_ingest_pass_with_blocker_rejected | IF-BLOCKREV-001 |
| AC-FR0140-02@v0.5 | integration | tests/integration/test_hotfix262_review_ingest.py::test_m_impl_ingest_pass_with_blocker_rejected | IF-BLOCKREV-001 |
| AC-FR0283-02@v0.8 | integration + e2e | tests/integration/test_hotfix262_attention.py::test_missing_token_single_attention_and_hint + tests/e2e/test_hotfix262_attention_journey.py::test_missing_token_halts_and_recovers | IF-ATTN-001 |
| AC-FR0298-02@v0.9 | integration + e2e | tests/integration/test_hotfix262_attention.py::test_loop_halts_on_unresolved_attention + tests/e2e/test_hotfix262_attention_journey.py::test_missing_token_halts_and_recovers | IF-ATTN-001 |
| AC-FR0329-02@v0.10 | integration | tests/integration/test_hotfix262_attention.py::test_issue_fetch_failed_classification_preserved | IF-ATTN-001 |
| AC-FR0315-01@v0.9 | integration | tests/integration/test_hotfix262_host_env.py::test_all_ten_sites_strip_operator_secrets | IF-HOSTENV-001 |
| AC-FR0276-01@v0.8 | integration | tests/integration/test_hotfix262_stage_events.py::test_exactly_once_stage_events_under_retry_tail + tests/integration/test_hotfix262_stage_events.py::test_clean_temp_refs_remaining_zero | IF-STAGE-001, IF-CLEANUP-001 |
| AC-FR0335-01@v0.11 | integration + e2e | tests/integration/test_hotfix262_stage_events.py::test_legacy_run_timeline_synthesizes_transitions + tests/e2e/test_hotfix262_release_ui.py::test_legacy_run_stage_window_nonempty | IF-STAGE-001 |
| AC-FR0335-02@v0.11 | integration | tests/integration/test_hotfix262_ui_perf.py::test_slim_timeline_payload_bounded | IF-UIPERF-001, IF-STAGE-001 |
| AC-NFR0150-02@v0.9 | unit + integration | tests/unit/test_hotfix262_ac_chain_red.py::test_empty_registry_never_reads_event_stream + tests/integration/test_hotfix262_ui_perf.py::test_ac_chain_short_circuits_empty_registry | IF-UIPERF-001 |
| AC-FR0333-01@v0.11 | integration + e2e | tests/integration/test_hotfix262_ui_perf.py::test_unified_todos_inbox_coverage + tests/e2e/test_hotfix262_release_ui.py::test_todos_cards_clickable_readable | IF-UIPERF-001 |
| AC-FR0340-01@v0.11 | integration + e2e | tests/integration/test_hotfix262_ui_perf.py::test_backlog_title_never_ulid + tests/e2e/test_hotfix262_release_ui.py::test_backlog_titles_readable | IF-UIPERF-001 |
| AC-FR0307-01@v0.9 | integration + e2e | tests/integration/test_hotfix262_ui_perf.py::test_todos_items_carry_context_and_action_ref + tests/e2e/test_hotfix262_release_ui.py::test_todos_cards_clickable_readable | IF-UIPERF-001 |

### 8.1 Arrival-green guard nodes（到达即绿守卫清单）

本 delta 无到达即绿守卫：全部接缝（谓词模块、ingest 拒绝、阶段事件、UI 修订）在基线 `44c14de` 上均不存在或行为不符，所有 §8 节点都是合法 Red（重入期无 green-by-delivery 象限）。既有 v0.12 守卫清单（v0.12 test-plan §8.1）继续以原样保持绿，不属本 delta 的守卫声明。

---

## 9. E2E Happy Paths

### 9.1 缺 token attention 停顿旅程（CLI，非 `ui`）

`tests/e2e/test_hotfix262_attention_journey.py`：清除 `GITHUB_TOKEN`、预置 `GITHUB_ACCESS_TOKEN` → 触发 issue 创建路径 → 恰一条 `attention.required(area="issue_creation", reason="missing_token")` 且 hint 含 `export GITHUB_TOKEN` → run_loop 停在 `needs_attention`（无紧循环、无 `CommandStallError`）→ 恢复 `GITHUB_TOKEN` → `trac run --resume` 成功创建 issue（`issue.created` + `issue.mapped api_verified=true`）。

### 9.2 UI 旅程（Playwright，`ui` marker）

`tests/e2e/test_hotfix262_release_ui.py`：legacy run（无显式阶段事件种子）打开时间线 → 合成转移呈现、M-MILESTONE closed=done、`stageWorkflowWindow` 非空 → 选中 stage 节点只有 outline ring（`state-*` 色保留、aria 属性在场）→ backlog 条目显示可读标题（无 ULID）→ Todos inbox 卡片覆盖 awaiting/attention/evaluated 三来源、可点击导航、`data-testid="todo-*"` 保留。

---

## 10. Fail-closed / 注入矩阵（integration 为主）

| # | 注入 | 必须观察的阻断/行为 |
|:--|:--|:--|
| 1 | 5896-shape `full.executed`（eligible=false） | 不信用为 FULL_F；closure 不绑定 |
| 2 | 7362-shape（serves_as_full_f=false ∧ eligible=true） | 信用为权威 FULL_F |
| 3 | serves_as_full_f=true ∧ full_f_eligible=false | 拒绝 |
| 4 | STALE 标记证据、candidate_sha 相同 | 复用 blocked/重跑；replay 标 stale |
| 5 | preview 缺 full_f/ci/prism.verify_final/prism.security/security 任一键 | preview invalid；release 拒绝 |
| 6 | legacy 单 `"prism"` 键 preview blob | 读回成功；无 operation_plan_changed |
| 7 | verify_final ingest pass+blocker | 拒绝；无 passing verdict；attempt 消费 |
| 8 | security ingest pass+blocker + replay seq 7342 | security 门 failed |
| 9 | M-TEST / M-IMPL ingest pass+blocker | 拒绝；路由不前进；无 task.completed |
| 10 | 缺 `GITHUB_TOKEN` + 有 `GITHUB_ACCESS_TOKEN` | ≤1 attention + hint；loop 停；无静默回退 |
| 11 | 重复 `_ensure_issue_backend` | 不重发 attention |
| 12 | 10 站点 spawn 子进程（env 注入密钥） | 子进程 env 无 strip 集成员；工具链正常 |
| 13 | retry_tail 重入 publish/milestone | 阶段事件恰一次、INV-9 顺序 |
| 14 | legacy run 无显式阶段事件 | 投影/UI 合成转移；显式事件优先 |
| 15 | `clean_temp_refs` 后 `refs/trac/rgr/*` 残留 | `remaining == 0` 断言；残留即失败 |
| 16 | 0-AC run 的 ac-chain | <5ms；无事件重放 |
| 17 | backlog 条目无 intake title | fallback 首行/本地化占位；永不 ULID |
| 18 | hotfix/verification-only run 经 release authorization | 不被 `_REQUIRED_PHASES` 阻断（该门禁未接入）；cleanup/必需键检查照常 |

---

## 11. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | 涉及 `serves_as_full_f` 消费断言的既有测试（若有） | 改为经 `is_authoritative_full_f` 语义断言权威判定 | B02 唯一权威 |
| 2 | 涉及 prism verdict 注入的既有集成测试 | pass 样本不得携带 blocker/high/critical findings（否则按新合同被拒） | B03 ingest 拒绝 |
| 3 | `tests/integration/test_milestone*.py` / publish 尾部测试 | 事件序断言追加三点阶段事件（恰一次） | M03 发射 |
| 4 | `tests/e2e/test_run_timeline_ui.py` 等选中节点断言 | 背景色覆写断言改为 outline ring + aria 断言 | 选中样式修订 |

Devon 的实现层测试更新由 RGR/coverage 自辖；本表只列 Shield 可见的既有资产更新。
