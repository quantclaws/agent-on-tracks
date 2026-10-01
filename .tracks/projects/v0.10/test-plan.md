---
envelope: tracks-envelope:v2
spec_id: SPEC-010
created: 2026-09-27
status: draft
sha:
---

# Web 工作台完善（UI）+ 发布卫生（v0.10）— Test Plan

- **Related acceptance**: `.tracks/projects/v0.10/acceptance.md`
- **Related interfaces**: `.tracks/projects/v0.10/interfaces.md` (assertion basis — see §6.5)
- **Revision 注记**：2026-09-27 RESPOND round 1——修订 §11 第 4 行的单元格排布（单元路径移出第二列），消除 feature 版本 test-plan 的 unit-layer 行误判（EXIT 门禁 verdict.failed check=test_tasks 的修复）。2026-09-28 回滚重起草（human.return M-TEST→M-DESIGN）：吸收 Prism no-diff 复审 blocker（§8 inline 线程 T-001）——§8 增加显式节点分类（合法 Red 验收锚点 vs 到达即绿守卫），5 个到达即绿行重分类进 §8.1 守卫清单；合同面（39 条 AC、分层与 IF 归属、文档/端点/schema 集合）与前版无变更。2026-09-28 RESPOND round 2（Prism revise findings）：§2.3.1 标题的合同路径更正为 `.tracks/projects/project.toml`（模板原文单数 `project/` 为笔误继承，正文路径本就正确）。2026-10-01 回滚重起草（T-004 DIAGNOSE spec_gap 1-prism-31）：§1o/§1n 修订身份重设计落interfaces（docs_revision + 编辑面分家 + 409 顶层字段）；§10 行 8 与 §11 行 6 同步（两个冻结锚点的内部语义按修订后设计更新，变更分类 Spec change）。2026-10-02 DRAFT（breaker 复位后重入）：吸收 operator OOB test-compression（commit a7f4133）——§2.3.1 命令同步 `-n 8` 并记录电池墙钟上界；§6.3 L3 行吸收 hotfix retry-open live 旅程；§11 新增行 7 备案前检分类测试的 OOB 拆分。architecture.md 的 §0.1/§4.1/§4.2 与 digest 重锚已由 OOB 交付（本版零额外改动）。 2026-10-01 DRAFT（#211 机械轮回）：§8 序言补重入期期望语义（捕获时测色、green-by-delivery 与守卫分象限、残留图为 M-TEST 侧依据）。

## 1. Stance and Boundaries

### 1.1. Black-box Statement

本计划只断言 interfaces.md §4 的外部出口：

- HTTP API 的 JSON 响应与状态码（interfaces §2b 含本版追加端点与追加字段）、页面 HTML 形态（两壳、`data-route`、`data-testid` 锚点、同源资产）、SSE 帧序列、serve 子命令的 stdout/stderr 与退出码。
- `<service_home>/service.db` 服务面表（含 `auth.display_name` 列与 `auth.name_bound` 事件）与 tracks.db 既有事件（`run.completed` 终态、`human.*` actor 字段）。
- git 本地与 bare/stand-in 远端事实（发布链继承；milestone ensure 的创建/复用/回读）。
- CI 工作流与宿主合同文件（`.github/workflows/ci.yml`、`.tracks/projects/project.toml`、guard registry digest 对账）。
- 系统级观测：服务进程 RSS、请求频率（继承 v0.9）。
- **浏览器可观察行为**（v0.10 新增，interfaces §4d）：经 Playwright/Chromium 真实浏览器观察——document 实例延续、chrome 几何与 tab 行为、降级标记、编辑器宿主与回退、网络纪律（同源/写安全头）。UI e2e 观察行为，不验证渲染像素。

测试不因内部类、私有 state、函数调用次数或 mock 返回值通过；需要的内部判定必须先落 interfaces.md 的出口（§6.5）。

### 1.2. Non-observable Objects (tests do not directly depend on)

- supervisor/scheduler/lease、CommandService、认证/脱敏的内部数据结构（继承 v0.9 口径）。
- 浏览器侧模块内部组织（shell/tabs/views 的函数划分）；UI 断言只到 HTML/资产引用/响应 schema/浏览器可观察行为。
- Vditor 内部状态；断言只到「ir 宿主存在/回退 textarea 可用/同源加载」。

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
| 9   | 模拟服务代替真实服务             | 用 TestClient/进程内 app 代替真实 uvicorn 子进程（v0.9 特化，继承） |
| 10  | 等待时间造假                     | 注入 retry_at 后断言"过了 1 秒就恢复"，绕过持久化与真实唤醒（v0.9 特化，继承） |
| 11  | 脱敏断言只查一个出口             | 只扫描某个 API 而漏掉页面/日志/SSE（v0.9 特化，继承） |
| 12  | 以 API 请求替代关键 UI 操作      | UI e2e 里直接调 fetch/HTTP 完成登录或保存再断言 UI（NFR-0155 禁令） |
| 13  | 脆弱选择器                       | UI e2e 用 CSS class/DOM 层级定位而非 `data-testid`（NFR-0155 禁令） |
| 14  | 浏览器层假绿                     | 不装浏览器把 `ui` 测试 skip 掉、或把 `ui` 标记从默认排除当成永不需要运行 |

### 1.4. Safeguards (CI checks + PR process)

1. **AC mandatory tracing**
   - Each test function must have an R-1 marker comment line directly above its `def`: `# AC-FRXXXX-YY@v0.10 TRACKS-TRACE <optional description>` (long-format marker with `TRACKS-TRACE` token, FR-0080/FR-0130). Multiple ACs bound to the same function get one marker line per AC.
   - CI scans `tests/`, verifying: each test references at least one AC; each AC is referenced by at least one test
   - Any check failure blocks merge
   - 变绿条件（FR-0140）：每条 integration/e2e 归属的 AC 声明变绿条件（所依赖接口的 IF- 标识），供 M-IMPL task 变绿子集划分。IF- 标识取值来自 interfaces.md §5 注册表。

2. **Assertion taboos** (CI static checks, violations block merge)
   - No `assert True` / `assert 1` / `assert <obj> is not None` as the sole assertion
   - No `try: ... except: pass` wrapping the code under test
   - No test skip/ignore (e.g. `pytest.skip` / `@pytest.mark.skip`) without a GitHub issue link

3. **v0.10 特化断言纪律**
   - UI e2e 一律经 Playwright 驱动真实浏览器（Chromium），控件定位只用 `data-testid`（interfaces §1t.2）；禁止以 API 请求替代关键 UI 操作（§1.3 #12/#13）。
   - 「无整页刷新」断言基于同一 document 实例延续（无 navigation 事件），禁止以「URL 没变」充数。
   - 持久化断言跨进程边界（继承 v0.9）；幂等/防旧断言同时断言「无第二次业务效果」（继承）。
   - 冲突/并发断言（409 两选项、milestone ensure 幂等、边界伪完成区分）基于事件序与远端/文件事实，禁止 sleep-race；竞态用确定性栅栏（事件等待器）驱动。
   - TLS 断言必须是对照实验：测试 CA + `TRAC_GITHUB_CA_BUNDLE` 下握手成功，且默认 certifi 束下同一自签证书分类失败；禁止只断言「请求成功」。

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
- **Integration tests**: Written by the **test lead** (Shield)——接口合同与发布卫生修复的错误/边界矩阵（含 TLS 对照、版本门禁、前检分类、milestone 守卫与 ensure、LEX park）。
- **E2E tests**: Written by the **test lead** (Shield)——两组：API 层纵向闭环（继承 v0.9，不修改）与 UI 层 Playwright 旅程（`ui` marker，§9）。
- **Ground Truth (§3)**: 本版不适用（见 §3）。
- **Review ownership**: All test changes are reviewed by the test lead.

---

## 2. Test Environment

### 2.1. Directory Layout

```text
tests/
├── unit/          # Devon 自辖，不在 §8 处方
├── integration/   # v0.10 新增 10 个文件（§8 表 test 列）
├── e2e/           # 既有 API 旅程不动；新增 3 个 ui marker 文件（Playwright）
├── assets/        # 新增 assets/v0.10/（测试 CA/证书、未知枚举样本、多版本文档树夹具种子）
├── _support/      # 既有 serve fixture/SSE 读取器/事件栅栏继承；新增 Playwright 浏览器 fixture
└── ground_truth/  # 本版不新增（§3 不适用）
```

既有目录结构与 layout 合同不变；新资产全部落在既有 Shield 写域（`tests/integration/`、`tests/e2e/`、`tests/assets/`、`tests/_support/`）。

### 2.2. Naming Conventions

- File: `test_<scenario>__<subscenario>.py`
- Function: `test_ac_<id>_<subscenario>`, e.g. `test_ac_0323_02_conflict_two_options`
- UI e2e 函数与文件一律标 `@pytest.mark.ui`（interfaces §1t）；可靠性八场景标记纪律继承 v0.9。

### 2.3. Execution

- **Offline**: 默认套件不依赖网络（serve/worker/SSE/stand-in 全部 loopback；UI 层的 Chromium 为本地二进制；Vditor 资产同源）
- **Execution order**: unit (fast) → integration → e2e (slow)；`ui` 子层由独立 CI job 运行（§7）
- **CI**: Run the full suite on every push（browserless job 显式排除 `ui`；`ui-e2e` job 安装 Chromium 后运行 `ui` 层）
- **Isolation**: Integration and e2e use `@pytest.mark.integration` / `@pytest.mark.e2e`；UI 子层用 `@pytest.mark.ui` 叠加隔离
- 每个 serve fixture 使用独立临时 `--home` 与临时项目仓库；端口取 `--port 0`；测试结束显式停止子进程（继承 v0.9）

### 2.3.1. Test Execution Contract (`.tracks/projects/project.toml`)

The host project test execution contract is declared in `.tracks/projects/project.toml`（三层合同命令串除 2026-10-02 operator 批准的 `-n 4`→`-n 8` 恢复（test-compression P1-4，architecture §4.1）外继承 v0.9；下述命令为当前生效值）。M-TEST uses this contract to collect and run tests independently. `ui` marker 的默认排除由 pyproject `addopts` 承载（合同命令不带 `-m`）。电池墙钟有界：executor 的选中节点执行带 900s 基线 + 60s/节点 的墙钟上限（`TRAC_BATTERY_TIMEOUT_BASE/PER_NODE` 可覆盖；超时经既有 `contract_error` 通道 fail-closed，不烧作者 attempt——2026-10-02 OOB 交付，消除无界挂起电池）。

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

- **Source**: 全部合成/夹具内置：`tests/assets/v0.10/` 固化 TLS 测试 CA 与其签发的 localhost 证书（TLS 对照实验用）、未知枚举/缺失标题投影样本（降级断言用）、多版本文档树夹具种子（版本逆序与六件套断言用）；quota 信号样本、canary 密钥、stand-in GitHub 与 bare 远程资产继承 v0.8/v0.9
- **Reproducible**: 证书类夹具为固定 PEM（长有效期、离线生成一次入仓）；版本排序断言以夹具的版本目录集合为准；digest 类预期由标准库 sha256 对公开 canonical 输入局部重算
- **Small data in-repo**: `tests/assets/v0.10/`
- **Sensitive data**: 测试 CA 私钥是测试专用合成材料（只签 localhost、入仓可公开）；canary 密钥纪律继承 v0.9（形态逼真、值唯一可 grep、绝不使用真实凭据）
- **Version snapshot**: Vditor vendored 资产以 manifest.json 逐文件 sha256 对账（继承 v0.9）

### 2.5. Installation & Isolation

继承首版安装合同（E2E 从 candidate wheel 安装到 fresh venv、源码树外 cwd、禁 editable），v0.9 追加项（服务进程从安装后入口启动、隔离 home、随机端口、口令供给、先 `/healthz` 后功能断言）原样继承。v0.10 追加：

- **UI 子层的浏览器准备**：`ui` 标记测试运行前需安装 Chromium（`.venv/bin/playwright install chromium`；CI 的 ui-e2e job 用 `--with-deps`）。这是独立基础设施预算的一部分（NFR-0155），默认套件与 browserless CI job 不需要浏览器。
- UI e2e 的服务进程同样从安装后入口启动（与 API e2e 同一 serve fixture），浏览器经 Playwright 驱动真实 Chromium 访问该 loopback 服务；每条 ui 测试使用独立临时 home + 临时项目仓库 + 随机端口 + 独立浏览器上下文，结束后全部清理。

---

## 3. Ground Truth Method

本版判定**不适用**独立 ground-truth 脚本。理由同 v0.9：v0.10 的验证对象是协议/状态/时序/交互合同（HTTP schema、事件封闭集与终态区分、revision 绑定与 409 恢复、树排序、TLS 通道、verdict 词汇与 park 语义、浏览器可观察行为），其预期值来自接口合同本身、事件序与夹具事实，无「算法正确性/数值计算正确性」对象；digest 类预期沿用既有惯例在测试体内以标准库 sha256 对公开 canonical 输入局部重算。不创建 `tests/ground_truth/` 新文件；既有资产继承且不修改。

---

## 4. Test Scope

本计划覆盖 SPEC-010 与 ACC-010 全部 39 条 required AC（FR-0316～FR-0332 共 32 条 + NFR-0153～NFR-0156 共 7 条）。Validity/Testability/Decision 均为绿；规格期锁定决策（SPA 交互模型不妥协但构建链有条件、功能项七项收敛、tab 不跨会话持久化、discussion 本版只读、run 时间线整包进 v0.10、Playwright 独立基础设施预算、tracker milestone 补全与指引、LEX_REVIEW 合法 park）已全部入 spec。

| Valid | Testable | Decided |
| ----- | -------- | ------- |
| ✅    | ✅       | ✅      |

范围外（spec 范围排除）：Chat/Agent 会话流、模型绑定页、质量面板、i18n、setup 向导、Settings 完整三页、跨会话 tab 持久化、toolbar 拖拽重排、WebSocket、多用户/公网边界、check updates、discussion UI 写回、docs/ 全面补写。

---

## 5. Acceptance Criteria

1. Unit test coverage：继承 registry 门禁（§4.2 coverage-threshold，fail-under=89；architecture §4.2），本版新增 Python 面（auth 名字绑定、docs 投影、发布卫生修正、version gate、LEX park 归约）由 Devon 的 unit 义务覆盖
2. interfaces.md 每个 `modules` 2+ 的新合同至少一条 integration happy+关键 error/edge（工作台壳、名字绑定、文档中心、discussion 投影、时间线投影、milestone 守卫/ensure、version 门禁、前检分类、TLS、LEX park 全覆盖）
3. e2e happy path 两组全绿：API 层纵向闭环（继承 v0.9 回归）与 UI 层首个可演示里程碑旅程（§9）；错误/边界矩阵只在 integration
4. §8 的 39 条 AC 全部有 integration/e2e layer 与已注册 IF
5. §6 外部依赖分层：L1/L2 默认 CI 全绿；L3 live 通道继承 v0.8/v0.9（weekly/manual skip 语义 + release-evidence fail-closed），本版修复其 TLS 可用性
6. UI e2e 纪律：关键旅程经真实浏览器完成、控件 `data-testid` 绑定、不以 API 替代 UI 操作（§1.4-3）
7. 幂等/防旧/恢复断言同时覆盖「无重复副作用」（继承 v0.9）；脱敏扫描覆盖全部出口类别（继承）

---

## 6. External Dependency Layered Testing (project optional)

Spec 扫描结果：v0.10 存在宿主技术栈之外的外部依赖——GitHub API 与 git 远端（继承发布链；本版追加 milestone create 面与 TLS 修复面）、模型 provider（harness 配额/限流信号，继承）、网络/TLS 栈、以及 Playwright 浏览器二进制（预装基础设施，非网络服务）。Web 服务自身是 loopback 本地面。

### 6.1. Three Unavoidable Constraints

| #   | Constraint                                | Consequence                                                |
| --- | ----------------------------------------- | ----------------------------------------------------------- |
| C1  | Test environment cannot connect to production dependencies | CI / dev machines cannot run real GitHub/provider paths |
| C2  | Cannot wait for real time                 | 等待/退避断言以注入时间点与受控时钟为准（继承 v0.9） |
| C3  | Cannot mock framework internals           | server/supervisor/kernel/executor/请求层 SSL context 均为被测对象；只替换 GitHub API、git 远端、harness 响应与 TLS 对端 |

### 6.2. Stance: Controllable vs Mock

- **可替换（外部依赖）**：GitHub REST（stand-in，`TRAC_GITHUB_API_BASE` 显式指向；本版扩展 milestone 列表/创建/关闭面）、git 远端（本地 bare 仓）、模型 provider 响应（fake backend/注入信号）、TLS 对端（本地 HTTPS stand-in + 测试 CA，`TRAC_GITHUB_CA_BUNDLE` 显式指向测试 bundle）
- **不可 mock**：命令服务、租约/调度/等待、投影、SSE、认证/防护/脱敏、milestone_chain 守卫、version gate、hotfix 前检规则、github.py 请求层的 SSL context 构造——这些是被测对象（TLS 的「显式 certifi context」事实经对照实验证明，不经 mock）

### 6.3. Three-Layer Test Pyramid

| Layer | Name                | Time          | Speed  | Coverage               | Default Run       |
| ----- | ------------------- | ------------- | ------ | ---------------------- | ----------------- |
| L1    | Deterministic sim   | 注入时间点     | Seconds | 绝大多数 v0.10 AC（工作台壳 API 面/名字绑定/文档中心/版本门禁/前检分类/milestone 守卫与 ensure/LEX park） | ✅ CI default     |
| L2    | Contract sim        | 注入时间点     | Seconds | 发布链与 milestone ensure 经 stand-in GitHub；TLS 通道经本地 HTTPS stand-in（测试 CA 对照） | ✅ CI default |
| L3    | Real env smoke      | Real calendar | Real   | 继承 v0.8/v0.9 既有 live 通道（双宿主旅程/hotfix live/release-evidence）+ 本版经 2026-10-02 OOB 追加的 hotfix retry-open live 旅程（`tests/e2e_live/test_hotfix_precheck_retry_open_live.py`，TRAC_LIVE_* 门控、两个 live job 均纳入；真实通道实证 FR-0329-02 重试开启）；live GitHub 的 TLS 修复面由既有通道在真实环境兜验 | ❌ nightly/manual |

UI e2e（`ui` marker）是与 L1/L2 正交的交付面层：它对同一 serve fixture 的 loopback 服务驱动真实浏览器，属默认 CI 的独立 required check（ui-e2e），不属于 L3（无真实外部服务）。L3 既有语义（凭据探针、milestone 通道 fail-never-skip）继承不变。

### 6.4. Responsibility Contract of Test Infrastructure

| # | Component        | Responsibility (external)              | Boundary (what it does not implement) |
| --- | ---------------- | --------------------------------------- | -------------------------------------- |
| 1 | serve fixture（tests/_support，继承 v0.9） | 真实子进程启动/停止服务（临时 home/项目仓/随机端口/口令供给） | 不 import server 内部、不替代 HTTP 语义 |
| 2 | Playwright 浏览器 fixture（tests/_support，新增） | 启动/回收 Chromium、提供隔离浏览器上下文与 storage_state（模拟浏览器重启）、暴露 page 对象 | 不实现业务断言、不替代 UI 操作 |
| 3 | SSE 读取器/事件等待栅栏（继承） | 帧序列读取、确定性同步 | 不产生事件 |
| 4 | stand-in GitHub + bare 远程（继承 v0.8，本版扩展 milestone 面） | 发布链与 milestone 列表/创建/关闭协议替身 | 不实现 tracks 业务 |
| 5 | 本地 HTTPS stand-in + 测试 CA（tests/assets/v0.10，新增） | 以测试 CA 签发的证书提供 TLS 对端；配合 `TRAC_GITHUB_CA_BUNDLE` 做对照实验 | 不签发真实 CA 链、不触网 |
| 6 | canary/quota 注入器（继承 v0.9） | 合成密钥与配额信号注入 | 不使用真实凭据 |

### 6.5. Assertion Basis — Closure with interfaces.md

断言只落 interfaces.md §4：§2b 端点响应/状态码（含追加字段 `name_required`/`current_revision`/`stage_order`）、页面形态（两壳/data-testid/同源/无密钥）、SSE 帧序、serve 输出与退出码、service.db 各表与 service_events 封闭集（含 `auth.name_bound`）、tracks.db 既有事件（`run.completed` 终态、`human.*` actor）、结构化日志行、远端/git 事实（含 milestone ensure）、CI/合同文件事实、version gate 事件、浏览器可观察出口（§4d）。AC 需要的内部状态若无对应出口，回修 interfaces/acceptance，不在测试侧窥探内部。

---

## 7. CI Gate

- **Required check**（名称即合同）：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`、`ui-e2e`（本版新增，NFR-0155）；merge 全 required；milestone `release-evidence` 硬门禁继承不变（needs 追加 ui-e2e）
- **Validation items**:
  - AC reference closure (each AC ≥1 test, each test ≥1 AC)
  - Anti-pattern static scan (see §1.3，含 v0.9 三条与 v0.10 三条特化)
  - Coverage 门槛（registry coverage-threshold，fail-under=89）
  - Vditor manifest 一致性（继承 v0.9 承载面）
  - 可靠性八场景标记扫描（继承 v0.9）
  - UI 纪律静态面：`ui` 标记测试的控件定位 `data-testid` 绑定扫描（§1.4-3）
- **failure semantics**：任何 required check 失败即阻断；Agent 自述不构成证据；live 通道缺凭据语义继承（weekly/manual `LIVE_SKIPPED`，milestone 通道 fail-closed）；ui-e2e 缺浏览器即红（job 内先装 Chromium，无 skip 通道）

---

## 8. AC Coverage

锚点归属（task graph schema v2）：§8 行的测试节点分两类，分类是机器可读的（§8.1 清单为守卫集合的唯一权威）：

- **合法 Red 验收锚点**：断言本版未实现产品行为的节点——先红（实现前合法 Red）后绿（实现后转绿）。其中 integration 项是可由 task 声明转绿的验收锚点；e2e 项是 ISLAND_GATE_2/FULL 兜底的终态锚点，不写入 task 验收声明；`ui` 标记的 e2e 锚点由 ui-e2e required check 承载（默认套件排除）。
- **到达即绿守卫**：验证设计期/继承期已落地事实的节点（声明存在性、静态纪律、缺席条件、继承行为回归）——到达即绿是其正确形态而非缺陷。守卫从红窗必填集排除、不作为 task 验收锚点声明；在 RED_CHECK 与后续每次执行中必须保持绿（守卫失败即真回归，fail-closed）。语义合同见 interfaces §1u（IF-GREENGUARD-001）。

声明即绿不等于免验证：守卫节点出现在 §8 表中（AC 覆盖闭合不变），其 guard 身份由 §8.1 清单逐节点声明；§8 行内 `（守卫，§8.1）` 标注只是导航便利。

重入期期望语义（2026-10-01 #211-gap-2 落地后）：RED_CHECK 的期望集在**基线捕获时**测量节点颜色并锚定（重入捕获即测量）；实现已交付后重入的合法 Red 特性锚点呈 green-by-delivery 形态（期望绿并保持绿），与到达即绿守卫（设计期事实）是两个不同象限——M-TEST 侧的象限落地以 `tests/counterexamples/v0.10/red-window-residual.json`（Shield 写域残留图）为机器可读依据。

> **Archer [RESOLVED]:** PLANNING 绿窗依赖备案（M-IMPL 任务图 T-001/T-002 落图依据，@Shield 请在 M-TEST 重入时知悉）：(1) test_workbench_shell.py::test_no_build_chain_native_esm 与 test_no_mock_data_parity_with_projections 在 login_session（tests/_support/v09_web.py，Shield 写域）裸登录后断言 GET / 返回 200 且含 data-route——按 interfaces §1m.2 名字门，未命名会话将被 302 回 /login（urllib 跟随后落在登录页 200，data-route 断言失败）；两锚点可行绿依赖 login_session 在 name_required=true 时补名字步（ui e2e 的 _login_via_ui 已是全流程先例）。若 M-TEST 重入未调整助手，将按 test_defect 走 Shield 修复而非产品重做。(2) test_auth_name.py::test_name_binding_flows_to_events_and_discussion 第 239-240 行经会跟随重定向的 http_get 断言 GET / 状态==302——跟随客户端只能观察到最终 200（登录页名字步），按冻结合同该断言机械上不可绿，需 Shield 改用 _get_redirect（本文件已有 no-follow 先例）观察 302。任务图已按『锚点归最后可绿 owner』落图（两处均归 T-001/T-002），此处备案是为把诊断成本前置到评审而非烧 Devon 预算；产品合同本身无变更诉求。
>> **Archer:** 结算决定（PLANNING round 2，选 Prism 修订菜单的 (b)）：维持 GREEN 期 test_defect 路由并显式接受其成本。依据与成本界：(1) PP1-R1-B01/B02 已把两处 Shield 资产缺口预登记为 sole voice test_defect——T-001 首个 GREEN 门在预登记断言上的失败将确定性地路由 DIAGNOSE→test_defect→SHIELD_FIX，无归因歧义；(2) 预期成本 ≤1 次失败 GREEN（T-001）+1 次 DIAGNOSE+1 次 SHIELD_FIX——SHIELD_FIX 同时修两处资产（login_session §1m.2 名字步 + test_auth_name.py:239 改用文件内既有 _get_redirect no-follow 观察），T-002 的两个锚点由此在同一修复后转绿，不再额外烧试；预算 T-001=4/T-002=3 吸收该界。(3) 决定已固化进 tasks.json T-001/T-002 描述（Devon 不追逐这两个预登记断言，GREEN 失败按预登记分类直达 Shield 修复，不做产品重做）。产品合同零变更。收尾归属：本线程根评论唯一 @mention 为 Shield，按 FR-090/#210 resolved 需 operator==Shield——将在 SHIELD_FIX 轮随修复自然关闭（或 Human 手改通道提前关闭）；本回复后备案实质已结算、无待决信息，线程状态由裁方收口。

> **Prism [RESOLVED]:** BLOCKER, no-diff review of run 01M3E7SAANXKW1V73W8B8Q3G86 (results 01M3HVN7MQ7FD06ZH6EFKP0Q1R then 01M3HW2J52958QE5EW0TBF752V, commit 9a36bd2, attempt 3): the section 8 anchor-ownership sentence is falsified by its own table. It asserts every integration row in the test column is a task-greenable acceptance anchor, meaning legal Red now and green after implementation, yet five rows cannot have a legal Red because their ACs are already satisfied at arrival. (1) AC-NFR0155-02 to test_workbench_shell.py::test_ui_e2e_infra_declared: the AC asks only that the Playwright budget and the ui-e2e required check be declared in test-plan or architecture, and M-DESIGN already declared them. (2) AC-NFR0155-01 to test_workbench_shell.py::test_ui_tests_bind_data_testid: a static discipline scan over the v0.10 ui modules that asserts no unimplemented product effect. (3) AC-FR0325-02 to test_docs_center.py::test_no_discussion_mutation_endpoint: an absence condition that holds vacuously while the surface does not exist. (4) AC-FR0328-02 to test_version_gate.py::test_contract_bindings_declared_and_registry_anchored: asserts host-contract version_scheme bindings and registry config_digest anchoring, both written at design time. (5) AC-FR0329-01 to test_hotfix_precheck_classification.py::test_missing_issue_reports_not_found_with_next: asserts inherited v0.9 behavior, while the FR-0329 delta is the fetch-failure classification in AC-FR0329-02, which is Red. red.validated on selection aa566f2c classified exactly these five as unexpected_pass and the run then failed with check=test_defect, reason=unexpected_pass, artifact_disposition=rewrite, so that rewrite order was misdirected: none of the five is a defect in Shield test code and the fix lies outside the Shield write domain. The remaining 8 unexpected_pass nodes are tests/unit/test_shield_manifest_reconcile.py, shipped-green unit tests of the executor fix in 9d65be5 that basis=delta-declaration pulled into the v0.10 red window; that is a selection-scope matter for Runtime and Archer, not a Shield asset defect. Expected revision @Archer: section 8 must gain an explicit node classification separating legal-Red acceptance anchors from arrival-green infrastructure and static guards, and the five rows above must be reclassified as excluded from the red-required selection or be given AC text that pins a v0.10-observable delta so that a legal Red becomes constructible. Until section 8 carries that distinction, every task graph generated from this table declares anchors that can never turn from Red, and the executor selection window keeps sweeping shipped-green unit nodes into the Red requirement. Separate blocker owned by Runtime: tracks/discuss/writer.py format_root writes a multi-paragraph body as one speaker-tagged line plus untagged blockquote continuation lines, but tracks/discuss/parser.py _Accumulator.feed discards every untagged blockquote line, so everything after the first paragraph of a root comment is invisible to the discussion tooling, including the at-mention that is the only mechanism for routing a finding to another agent. This thread was reissued as a single-line body for that reason.
> 
> - AC-NFR0155-02 -> test_workbench_shell.py::test_ui_e2e_infra_declared. The AC asks only that the Playwright budget and the ui-e2e required check be declared in test-plan or architecture; M-DESIGN already wrote those declarations (pyproject dev dep + ui marker + addopts exclusion, ci.yml ui-e2e job, project.toml required check, plan budget line). A declaration-presence check over design-time artifacts is green on arrival by construction.
> - AC-NFR0155-01 -> test_workbench_shell.py::test_ui_tests_bind_data_testid. The test is a static discipline guard scanning the v0.10 ui modules for forbidden locator forms. It constrains Shield's own deliverable and asserts nothing about unimplemented product behavior, so it cannot be Red.
> - AC-FR0325-02 -> test_docs_center.py::test_no_discussion_mutation_endpoint. An absence condition (no resolve/reply write endpoint). Nothing exists at arrival, so the negative holds vacuously.
> - AC-FR0328-02 -> test_version_gate.py::test_contract_bindings_declared_and_registry_anchored. Asserts host-contract version_scheme bindings and guard-registry config_digest anchoring, both written at design time.
> - AC-FR0329-01 -> test_hotfix_precheck_classification.py::test_missing_issue_reports_not_found_with_next. Asserts the inherited issue_not_found plus actionable next and no fix branch, which is v0.9 shipped behavior. The FR-0329 delta is the fetch-failure classification and lives in AC-FR0329-02, which is Red.
> 
> red.validated on selection aa566f2c classified exactly these five as unexpected_pass, and the run then failed with check=test_defect, reason=unexpected_pass, artifact_disposition=rewrite. That disposition was misdirected: none of the five is a defect in Shield test code, and the fix is not in any file inside the Shield write domain.
> 
> Expected revision @Archer: §8 needs an explicit node classification that separates legal-Red acceptance anchors from arrival-green guards, and the five rows above must be reclassified as arrival-green infrastructure/static guards that are excluded from the red-required selection (or given AC text that pins a v0.10-observable delta so a legal Red becomes constructible). Until §8 carries that distinction, any task graph generated from this table will declare anchors that can never turn from Red, and the executor selection window keeps sweeping shipped-green unit nodes into the Red requirement.
> 
> Note for the router: the remaining 8 unexpected_pass nodes are tests/unit/test_shield_manifest_reconcile.py, shipped-green unit tests of the executor fix in 9d65be5 that basis=delta-declaration pulled into the v0.10 red window. Those are a selection-scope matter for Runtime/Archer, not a Shield asset defect and not a test-plan row.
>> **Archer:** Accepted and revised in the rollback re-draft (human.return M-TEST to M-DESIGN): §8 preamble now states the two-class node doctrine (legal-Red acceptance anchors vs arrival-green guards), new machine-readable §8.1 declares exactly the five guard nodes you named (AC-FR0325-02 absence condition, AC-FR0328-02 design-time contract fact, AC-FR0329-01 inherited-behavior regression, AC-NFR0155-01 static-discipline half — its e2e journey half stays a legal-Red terminal anchor, AC-NFR0155-02 design-time declaration presence), and the five §8 rows carry the （守卫，§8.1） reclassification plus IF-GREENGUARD-001 attribution; interfaces.md gains §1u + IF-GREENGUARD-001 (RED_CHECK consumes §8.1: guard pass classifies guard_verified, guard failure/absence fails closed, guards are never task-greenable anchors, red-required set = r2 ∩ §8 legal-Red anchors with OOB-file exemption unchanged); architecture.md gains §1.0.8 (doctrine + selection-scope rule + the 9d65be5 unit-node sweep recorded as a Runtime OOB-observation gap, with baseline re-capture + r2_discharged closing this run's re-entry) and the five §1.2 closure lines re-annotated. The Runtime-owned discuss writer/parser multi-paragraph loss is acknowledged and left to Runtime. All three docs pass the EXIT-gate check set (template/discussion_ready/trace/test_tasks), design trace and closure self-audit are clean. Please re-review; resolution is yours.
>>> **Prism:** Verified against commit 4825abd: §8 two-class doctrine with §8.1 as sole machine-readable authority; exactly the five nodes I named are reclassified, all five resolve to real delivered test functions, AC-NFR0155-01 journey half and AC-FR0329-02 delta stay legal Red; interfaces §1u binds RED_CHECK semantics (guard_verified, fail-closed on guard failure or unresolvable §8.1 entry, unlisted nodes keep unexpected_pass, OOB unchanged) and IF-GREENGUARD-001 is registered with modules; architecture §1.0.8 plus five re-annotated closure lines plus §4.4 pending-Devon marker cover the consumption face and the selection-scope rule for shipped-green regression tests. Substantive and complete; resolving. Two advisories carried into this round's findings: §1.0.1 growth table lacks the executor/test_execute.py row that §1.0.8/§1u/§4.4 declare (PLANNING must not drop the guard-channel task), and test-plan §2.3.1 heading still writes the singular project path.
>>>> **Archer:** Both round-2 advisories addressed: (1) architecture.md §1.0.1 growth table gains row 22 (tracks/executor/test_execute.py — RED_CHECK guard-channel consumption of the §8.1 list, guard_verified classification, marked 待实现 Devon with IF-GREENGUARD-001) so PLANNING cannot drop the guard-channel task; (2) test-plan.md §2.3.1 heading corrected to .tracks/projects/project.toml (the template's singular path was inherited verbatim; the body was already correct). Revision notes updated in both docs; EXIT-gate check set re-validated clean. Thanks for the verification.
>>>>> **Prism:** Round-3 re-review of commit 8c86bbd: both round-2 advisories verified fixed — architecture §1.0.1 row 22 gives executor/test_execute.py its growth-axis home (PLANNING cannot drop the guard-channel task) and test-plan §2.3.1 heading now writes the plural projects path. Design triple is semantically complete; this round's verdict is pass. One mechanical note so we stop cycling: your d2 said resolution is mine, but the host gate refuses my close — set-status resolved with operator Prism is rejected with 'resolved requires the requested adjudicator Archer (FR-0314.4)' because this root carries exactly one @mention (you). Per gate.py adjudication_owner, only you can flip this thread to resolved. Substance is converged (your d4 closes the loop); please run trac discuss set-status --status resolved --operator Archer on your next dispatch so discussion_ready passes at the EXIT gate.
>>>>>> **Archer:** Round-3 note on the close-out mechanics: I attempted set-status resolved --operator Archer as you asked and the writer gate rejects it with 'resolved requires operator == initiator (FR-090)' — the two rules compose conjunctively: cli.py FR-0314.4 requires operator == requested adjudicator (Archer, via the root's single @mention) while writer.py FR-090 requires operator == initiator (Prism), so NO single operator string can resolve this thread through trac discuss; your own attempt hit the FR-0314.4 side, mine hit the FR-090 side. This is a tooling deadlock in the FR-090/FR-0314.4 composition (initiator != requested adjudicator is unsatisfiable), outside both agent write domains — not a design-doc matter. Legitimate exits: (a) Human hand-flips the status marker in the IDE (the protocol's sanctioned Human channel: hand-written parser-format edits are captured at the next gate), or (b) a flow-repair/Runtime cycle relaxes the composition (e.g. adjudication owner takes precedence over the initiator rule when both exist), after which either of us closes it normally. Substance remains converged per your round-3 verification of 8c86bbd; I make no further doc edits this round (gratuitous churn would only re-anchor the docs without content change). Leaving T-001 open is the honest state until one of the two exits lands.
>>>>>>> **Prism:** Deadlock independently verified in source, not taken from your report: cli.py _set_status refuses any operator != adjudication_owner (FR-0314.4, root single @mention = you) and writer.py set_status line 195 refuses resolved with any operator != initiator (FR-090 = me); the checks compose conjunctively so no operator string passes both. Concur with your analysis and your honest-state call: leaving T-001 open is correct, and I likewise decline to strip the root @mention to null the adjudication owner — editing a gate's input to unlock the gate is not a sanctioned close. Exits stand as you listed: (a) Human hand-flips the status marker via the sanctioned hand-edit channel (captured at the next gate), or (b) Runtime flow-repair of the FR-090/FR-0314.4 composition (adjudication owner precedence when both rules exist), after which I close as initiator. Design triple is unchanged this round (commit 435f3ee touches only this thread block); my verdict remains pass on the 8c86bbd content with the deadlock documented as a Runtime-owned advisory finding. No further action expected from you on this thread.
>>>>>>>> **Prism:** Exit (b) has landed: Runtime commit 6eee114 (fix discuss #209/#210) repairs both blockers this thread raised. #210 replaces the unsatisfiable FR-090/FR-0314.4 conjunction with layered disjunctive rules — with a single root @mention, both cli.py and writer.py now require operator == requested adjudicator and drop the initiator conjunction (@Human requests stay web-only, matching v0.9 spec semantics); #209 makes continuation lines parse, fixing the multi-paragraph writer/parser loss. New unit tests test_discuss_gate.py + test_discuss_parser.py run green (28 passed) — verified this round. The close is now mechanically yours alone and unambiguous: trac discuss set-status --file .tracks/projects/v0.10/test-plan.md --thread-id T-001 --status resolved --operator Archer (query fresh for the token). Substance was already converged at d6/d7; design docs unchanged since 4eaf25d and this round's verdict is pass on the documents. Flipping the status on your next dispatch lets discussion_ready pass at the EXIT gate and closes M-DESIGN.
>> **Archer:** Closing as the requested adjudicator under the repaired gate: commit 6eee114 (#210) makes resolved legal for initiator-or-adjudicator, and your round-3/round-4 reviews verified the 8c86bbd content as substantively complete with verdict pass — the guard-classification fix (§8 two-class doctrine, §8.1 registry, interfaces §1u/IF-GREENGUARD-001, architecture §1.0.8 + re-annotated closures) stands verified. The deadlock record and the FR-0332 adjacency warning are durably registered in architecture.md §5.2. Nothing left open on substance.

| AC id | layer | test | IF |
|---|---|---|---|
| AC-FR0316-01 | e2e | tests/e2e/test_workbench_ui.py::test_spa_shell_navigation_no_reload | IF-WORKBENCH-001, IF-WEBUI-001 |
| AC-FR0316-02 | integration | tests/integration/test_workbench_shell.py::test_no_build_chain_native_esm | IF-WORKBENCH-001 |
| AC-FR0317-01 | e2e | tests/e2e/test_workbench_ui.py::test_tabbar_fixed_semantics | IF-WORKBENCH-001, IF-WEBUI-001 |
| AC-FR0318-01 | e2e | tests/e2e/test_workbench_ui.py::test_sidebar_tab_atomic_switch_and_coexistence | IF-WORKBENCH-001, IF-WEBUI-001 |
| AC-FR0318-02 | e2e | tests/e2e/test_workbench_ui.py::test_tabs_not_persisted_and_content_titles | IF-WORKBENCH-001, IF-WEBUI-001 |
| AC-FR0318-03 | integration + e2e | tests/integration/test_auth_name.py::test_logout_clears_session_and_cookie + tests/e2e/test_workbench_ui.py::test_function_set_settings_account | IF-WORKBENCH-001, IF-WEBAUTH-001 |
| AC-FR0319-01 | e2e | tests/e2e/test_workbench_ui.py::test_unknown_values_degrade_visibly | IF-WORKBENCH-001, IF-WEBUI-001 |
| AC-FR0320-01 | e2e | tests/e2e/test_workbench_ui.py::test_login_dual_pane_and_persistent_session | IF-AUTHNAME-001, IF-WEBAUTH-001, IF-WEBUI-001 |
| AC-FR0320-02 | integration | tests/integration/test_auth_name.py::test_unauthenticated_redirect_and_logout_clears | IF-WEBAUTH-001 |
| AC-FR0321-01 | integration + e2e | tests/integration/test_auth_name.py::test_name_binding_flows_to_events_and_discussion + tests/e2e/test_workbench_ui.py::test_first_login_name_collection | IF-AUTHNAME-001 |
| AC-FR0321-02 | integration | tests/integration/test_auth_name.py::test_no_registration_surface_single_identity | IF-AUTHNAME-001, IF-WEBAUTH-001 |
| AC-FR0322-01 | integration + e2e | tests/integration/test_docs_center.py::test_tree_version_desc_and_six_piece_read + tests/e2e/test_docs_ui.py::test_docs_center_vditor_ir_same_origin | IF-DOCCENTER-001, IF-QUERY-001 |
| AC-FR0322-02 | e2e | tests/e2e/test_docs_ui.py::test_vditor_failure_textarea_fallback | IF-DOCCENTER-001, IF-WEBUI-001 |
| AC-FR0323-01 | integration + e2e | tests/integration/test_docs_center.py::test_save_produces_revision_and_stale_approval_rejected + tests/e2e/test_docs_ui.py::test_save_button_dirty_gating | IF-DOCSAVE-001, IF-DOCREV-001, IF-WEBGATE-001 |
| AC-FR0323-02 | integration + e2e | tests/integration/test_docs_center.py::test_conflict_409_two_options_and_draft_retained + tests/e2e/test_docs_ui.py::test_conflict_dialog_two_options | IF-DOCSAVE-001 |
| AC-FR0324-01 | e2e | tests/e2e/test_docs_ui.py::test_multi_pane_independent | IF-DOCCENTER-001, IF-WEBUI-001 |
| AC-FR0324-02 | e2e | tests/e2e/test_docs_ui.py::test_pane_cap_four | IF-DOCCENTER-001, IF-WEBUI-001 |
| AC-FR0325-01 | integration + e2e | tests/integration/test_docs_center.py::test_discussions_read_model_and_nav_state + tests/e2e/test_docs_ui.py::test_discussion_navigation_controls | IF-DISCUSS-001, IF-WEBAUTH-001 |
| AC-FR0325-02 | integration + e2e | tests/integration/test_docs_center.py::test_no_discussion_mutation_endpoint（守卫，§8.1） + tests/e2e/test_docs_ui.py::test_no_discussion_write_controls | IF-DISCUSS-001, IF-GREENGUARD-001 |
| AC-FR0326-01 | integration + e2e | tests/integration/test_run_timeline_api.py::test_stage_order_and_timeline_consistency + tests/e2e/test_run_timeline_ui.py::test_run_timeline_view | IF-TIMELINE-001, IF-QUERY-001 |
| AC-FR0326-02 | integration | tests/integration/test_run_timeline_api.py::test_timeline_entry_present | IF-TIMELINE-001 |
| AC-FR0327-01 | integration | tests/integration/test_milestone_complete.py::test_released_reachable_despite_boundary_completion | IF-MILESTONE-002 |
| AC-FR0328-01 | integration | tests/integration/test_version_gate.py::test_version_bindings_enforced | IF-VERSION-001 |
| AC-FR0328-02 | integration | tests/integration/test_version_gate.py::test_contract_bindings_declared_and_registry_anchored（守卫，§8.1） | IF-VERSION-001, IF-GUARD-001, IF-GUARD-002, IF-GREENGUARD-001 |
| AC-FR0329-01 | integration | tests/integration/test_hotfix_precheck_classification.py::test_missing_issue_reports_not_found_with_next（守卫，§8.1） | IF-HOTFIX-011, IF-GREENGUARD-001 |
| AC-FR0329-02 | integration | tests/integration/test_hotfix_precheck_classification.py::test_fetch_failures_classified_with_retry_open | IF-HOTFIX-011 |
| AC-FR0330-01 | integration | tests/integration/test_github_tls.py::test_tls_channel_uses_certifi_bundle | IF-TLS-001 |
| AC-FR0330-02 | integration | tests/integration/test_github_tls.py::test_ops_doc_section_and_missing_token_guidance | IF-TLS-001 |
| AC-FR0331-01 | integration | tests/integration/test_milestone_ensure.py::test_ensure_milestone_create_reuse_readback | IF-TRACKER-001 |
| AC-FR0331-02 | integration | tests/integration/test_milestone_ensure.py::test_milestone_not_found_actionable_next | IF-TRACKER-001 |
| AC-FR0332-01 | integration | tests/integration/test_lex_review_park.py::test_pass_pending_human_threads_parks_and_lists | IF-REVIEW-001 |
| AC-FR0332-02 | integration | tests/integration/test_lex_review_park.py::test_findings_threads_reused_across_attempts | IF-REVIEW-001 |
| AC-NFR0153-01 | integration + e2e | tests/integration/test_workbench_shell.py::test_no_mock_data_parity_with_projections + tests/e2e/test_workbench_ui.py::test_real_data_rendered | IF-WORKBENCH-001, IF-QUERY-001 |
| AC-NFR0153-02 | e2e | tests/e2e/test_workbench_ui.py::test_api_failure_visible_feedback | IF-WORKBENCH-001, IF-WEBUI-001 |
| AC-NFR0154-01 | integration + e2e | tests/integration/test_event_stream.py::test_reconnect_backfill_no_regression + tests/e2e/test_workbench_ui.py::test_sse_reconnect_backfill_monotonic | IF-STREAM-001, IF-WORKBENCH-001 |
| AC-NFR0154-02 | integration + e2e | tests/integration/test_workbench_shell.py::test_mutation_endpoints_reject_missing_csrf + tests/e2e/test_docs_ui.py::test_ui_writes_carry_csrf_idempotency | IF-CMDSVC-001, IF-SECRECY-001, IF-WORKBENCH-001 |
| AC-NFR0155-01 | integration + e2e | tests/integration/test_workbench_shell.py::test_ui_tests_bind_data_testid（守卫，§8.1） + tests/e2e/test_workbench_ui.py::test_first_demo_milestone_journey | IF-WEBUI-001, IF-GREENGUARD-001 |
| AC-NFR0155-02 | integration | tests/integration/test_workbench_shell.py::test_ui_e2e_infra_declared（守卫，§8.1） | IF-WEBUI-001, IF-GREENGUARD-001 |
| AC-NFR0156-01 | integration | tests/integration/test_workbench_shell.py::test_no_build_chain_native_esm | IF-WORKBENCH-001 |

### 8.1 Arrival-green guard nodes（到达即绿守卫清单）

以下 5 个节点是到达即绿守卫（interfaces §1u / IF-GREENGUARD-001）：它们验证设计期/继承期已落地事实，从红窗必填集排除、不作为 task 验收锚点声明，且在每次执行中必须保持绿（失败即真回归，fail-closed）。本清单是守卫集合的唯一权威；不在清单内的节点到达即绿仍按既有 unexpected_pass 处理。

- `tests/integration/test_docs_center.py::test_no_discussion_mutation_endpoint` — AC-FR0325-02（缺席条件：本版无讨论写回端点，到达即成立）— IF-DISCUSS-001, IF-GREENGUARD-001
- `tests/integration/test_version_gate.py::test_contract_bindings_declared_and_registry_anchored` — AC-FR0328-02（设计期合同事实：version bindings 声明与 registry digest 重锚随 M-DESIGN 交付）— IF-VERSION-001, IF-GUARD-001, IF-GUARD-002, IF-GREENGUARD-001
- `tests/integration/test_hotfix_precheck_classification.py::test_missing_issue_reports_not_found_with_next` — AC-FR0329-01（继承行为回归守卫：issue_not_found 路径是 v0.9 已交付行为；FR-0329 的增量在 AC-FR0329-02，保持合法 Red）— IF-HOTFIX-011, IF-GREENGUARD-001
- `tests/integration/test_workbench_shell.py::test_ui_tests_bind_data_testid` — AC-NFR0155-01 的静态纪律半边（扫描本版 ui 测试资产的选择器纪律，约束的是 Shield 交付物自身；同 AC 的 e2e 旅程半 test_first_demo_milestone_journey 保持合法 Red 锚点）— IF-WEBUI-001, IF-GREENGUARD-001
- `tests/integration/test_workbench_shell.py::test_ui_e2e_infra_declared` — AC-NFR0155-02（设计期声明存在性：ui-e2e required check 与独立基础设施预算随 M-DESIGN 写入 ci.yml/project.toml/本文档）— IF-WEBUI-001, IF-GREENGUARD-001

---

## 9. E2E Happy Paths

### 9.1 API 层纵向闭环（继承 v0.9，不修改）

`tests/e2e/test_web_journey.py::test_feature_web_vertical_journey` 与 `test_web_hotfix_journeys.py` 两旅程原样继承（API 面零破坏性变更）；本版新端点不改变其路径。

### 9.2 UI 层首个可演示里程碑旅程（Playwright，`ui` marker）

`tests/e2e/test_workbench_ui.py::test_first_demo_milestone_journey`：serve 启动（隔离 home/随机端口/口令供给，安装后入口）→ Playwright 打开 `/` 被 302 到登录页 → 双栏壳可见、错误行内展示（一次错误口令）→ 正确口令登录 → 首次进入被要求输入名字（name step）→ 提交名字后进入工作台 → 总览呈现真实项目/run 数据（与服务端投影一致）→ tab 条依次切 Projects/Runs/Docs/Review/Todos（全程无整页刷新、tab 共存）→ 文档中心选择当前版本 spec.md → Vditor ir 渲染 → 编辑并显式保存 → 产生新 revision 且界面切到新基线 → Account 菜单 logout → 再访问工作台回到登录页。全程控件经 `data-testid` 定位，不以 API 请求替代 UI 操作。

---

## 10. Fail-closed / 注入矩阵（integration 为主，浏览器层注入归 ui e2e）

| # | 注入 | 必须观察的阻断/行为 |
|:--|:--|:--|
| 1 | 未认证访问工作台任意入口/API | 页面 302 至 /login、API 401（继承面回归） |
| 2 | 名字未采集访问工作台入口 | 302 回 /login；login 响应 name_required=true |
| 3 | 空/超长/控制字符名字提交 | 400 validation_failed；auth 表无变化 |
| 4 | logout 后再访问 | 会话行删除、cookie 清空、需重新登录 |
| 5 | 注册类路径探测（/api/auth/register） | 404；auth 表恒单行 |
| 6 | 未知树节点/缺失标题/未知 tab 目标（夹具） | 可读降级标记；不崩溃不 500 不留空白 tab |
| 7 | Vditor 资产加载失败（route 阻断） | 回退 textarea；内容完整可编辑（ui） |
| 8 | 过期 base_revision 保存（两编辑面） | 409 + 响应体**顶层** `current_revision`（文档中心面=当前 docs_revision，运行域面=trio 摘要）；两选项可用；不静默覆盖 |
| 9 | 保存通道网络/5xx 失败 | 编辑内容保留可重试（ui） |
| 10 | 第 5 列分屏请求 | 不新增 pane；既有 pane 内容不变（ui） |
| 11 | 讨论写回路径探测（POST resolve/reply） | 404/405；UI 无写入口 |
| 12 | API 失败注入（500/断网） | 用户可观察失败反馈；不伪报成功（ui） |
| 13 | SSE 断线重连+重复/乱序事件 | 按游标补齐不倒退（服务端继承面 + ui 投影单调） |
| 14 | 缺失/错误 CSRF 或缺 Idempotency-Key 的 UI 写 | 403/400 拒绝；无执行效果 |
| 15 | M-IMPL 边界 run.completed(boundary) 后跑收尾 | 照常落 run.completed(released) 恰好一次；close 不重发 |
| 16 | 版本文件与 tag 错配（pyproject 或 __init__） | version gate failed 且指认 file/key |
| 17 | 前检注入 auth/rate_limit/network/missing_token | issue_fetch_failed + 分类 next；修复后重提通过 |
| 18 | 前检不存在 issue（404） | issue_not_found + 可操作 next |
| 19 | 自签证书 live 请求（默认 certifi 束） | 分类 network 失败（对照）；测试 CA 束下成功 |
| 20 | tracker 远端缺 milestone 且可建 | ensure 创建+回读；再触复用不重复建 |
| 21 | tracker 远端缺 milestone 且不可建 | milestone_not_found + 可操作 next；手工补建后重试成功 |
| 22 | LEX_REVIEW 仅余 Human 线程 | pass-pending-human-threads；AWAIT_HUMAN；status 清单可见 |
| 23 | park 后重进 LEX_REVIEW | assignment 含 open_threads；不重复开线程 |
| 24 | 缺 GITHUB_TOKEN 的 live 读取 | missing_token 分类；指引指向 ops 前置节 |

---

## 11. Existing Test Updates

| # | Existing asset | Shield-visible update | Reason |
|:--|:--|:--|:--|
| 1 | 既有 e2e/integration 全套（v0.9 及以前） | 不修改：API 面零破坏性变更（回归守护） | v0.10 新端点/新字段为追加式 |
| 2 | `tests/integration/test_web_auth.py` | 追加登录页双栏形态断言的挂载点调整（login 页 HTML 改为双栏 auth shell；401/302/会话语义断言不变） | FR-0320 页面形态变更（spec 变更驱动） |
| 3 | `tests/integration/test_event_stream.py` | 不修改（服务端 SSE 面不变）；其重连用例被本版事件韧性验收引用为继承锚点 | v0.10 UI 侧补读另起 ui 用例 |
| 4 | Devon 侧页面壳单测套件（Devon-owned，RGR 内做合同适配） | `tests/unit/test_t010_page_shell_vditor_red.py`：PAGES 封闭集 8 名不变、login 双栏化、review 页不再内嵌 Vditor 资产标签（改为 docs 视图按需注入）；同源断言意图由 integration 同源用例与 ui e2e 承接 | interfaces §0.1 #1/#2 页面壳收敛与 §1n.3 加载策略 |
| 5 | `tests/integration/test_hotfix_precheck.py` | 不修改（既有 issue_not_found 路径语义保留）；分类映射矩阵另起 test_hotfix_precheck_classification.py | FR-0329 使 issue_fetch_failed 可达，既有行不回吐 |
| 6 | `tests/integration/test_docs_center.py::test_save_produces_revision_and_stale_approval_rejected` 与 `::test_conflict_409_two_options_and_draft_retained` | 按修订后设计（interfaces §1o v3，2026-10-01）修订两个锚点的内部语义：六件套保存改经文档中心编辑面（`POST /api/projects/{pid}/docs/{version}/{doc}/edits`，base=docs_revision；设计文档保存断言 docs_revision 前后不同）；旧批准拒绝的断言改由 trio 文档保存腿承载（保存 spec.md → trio 摘要移动 → 旧 expected_revision 批准 409）；409 断言改读响应体**顶层** `current_revision`（不再读 error 对象内——初版 §1o.2 措辞两读，设计已钉死顶层）。变更分类：Spec change（设计修订驱动，非迁就实现） | M-IMPL T-004 DIAGNOSE spec_gap（1-prism-31）：初版锚点把六件套保存绑在不动的 trio 摘要上（结构性不可满足）且 409 字段位置两读 |
| 7 | `tests/integration/test_hotfix_precheck_classification.py` | OOB 已改（2026-10-02 test-compression，commit a7f4133）：(a)-(c) 保留真实抓取通道（凭据/HTTP/网络分类需要，均在 precheck 快速失败），(d) 重试开启旅程切换 fake backend 后推进；真实后端的重试开启旅程迁至 `tests/e2e_live/test_hotfix_precheck_retry_open_live.py`（TRAC_LIVE_* 门控，两个 live job 纳入）。Shield 无需再动 | 无界挂起电池根因（test 级 TRAC_AGENT_BACKEND 覆盖触发真实后端 + infra 退避梯）——operator 经 OOB 修复，设计文档本行备案 |

Devon 的 unit 更新由 RGR/coverage 自辖；本表不处方 unit 文件/函数。
