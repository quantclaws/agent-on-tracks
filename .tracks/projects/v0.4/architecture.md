---
architecture_id: ARCH-004
spec_ref: SPEC-004
created: 2026-08-05
status: draft
sha:
---

# v0.4 - 架构

本文是 ARCH-003（v0.2）的增量延伸，并纳入 v0.3 M-DESIGN 的既有实现事实。v0.1 的内核机制（事件溯源、单一生产路径、四增长轴分包、单写者锁、per-kind reconcile）与 v0.2/v0.3 的全部扩展（inline-discussion、模板校验、M-ACC/M-REQ-APPROVAL、M-DESIGN Archer/Prism）保持不变；v0.4 在既有包内新增 M-TEST 控制流与需求追踪工具，不推翻布局。凡未提及者，一律继承 ARCH-003 + v0.3 既有实现。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-003（v0.2）不变项

- 唯一工作流生产路径 `cli -> kernel.runtime.run_loop -> effects.executor.execute`：不变。M-TEST 的状态变更命令仍只此一路。
- 四增长轴分包 kernel/workflows/effects/checks：不变。v0.4 在 `effects/`（Shield 接入）、`kernel/`（M-TEST StageDef + 控制流）、`checks/`（新增 trace/reach 工具）上生长。
- 事件溯源 SQLite append-only + 投影可重建：机制不变。M-TEST 全程事件追加同一 events 表（NFR-0040）。
- 单写者锁 / 取消协议 / per-kind reconcile：不变。Shield dispatch 复用 dispatch_agent reconcile + opencode 物化清理。
- 纯核心 `project`/`decide` 零 I/O：不变。M-TEST 控制流分支在 decide()/project() 内仍为纯函数（NFR-0030）；collection 复跑、trace 调用、判据包加载归 executor。
- FakeBackend 确定性函数 + OpencodeBackend：不变。Shield 作为第六个 opencode agent 接入（同构模式）。
- inline-discussion 旁路（discuss/）：不变。M-TEST 的 Prism revise 仍经 `trac discuss` 锚定线程。
- 模板 + 校验（templating.py + executor/validate.py）：不变既有行为；v0.4 扩展 check_design_trace（IF- 归属）与新增 ID 文法校验。

### 0.2 继承 v0.3 M-DESIGN 既有实现事实

v0.3 未产出独立 architecture.md，其 M-DESIGN 实现已落入代码（machine.py StageDef / executor.py _NEXT_STAGE / Archer.md / Prism.md / design.committed + prism.verdict 事件）。v0.4 继承这些事实：

- `machine.py` `_STAGES` 中 M-DESIGN StageDef（initial_substate="DRAFT", drafting_role="archer", docs=DESIGN_DOCS, reviewer="prism"）：不变。
- `executor.py` `_NEXT_STAGE["M-REQ-APPROVAL"] = "M-DESIGN"`：不变。
- `design.committed` / `prism.verdict` 事件与 reducer：不变。v0.4 扩展 `_on_prism_verdict` 增加 M-TEST 分支（pass -> RED_CHECK, revise -> WRITE），不影响 M-DESIGN 分支。
- Archer/Prism agent 接入（AGENT_NAME, deliverables 一致性集合）：不变。v0.4 增补 Shield（第六个 agent）。

### 0.3 v0.4 变更项

- `_NEXT_STAGE` 增补 `"M-DESIGN": "M-TEST"`：M-DESIGN EXIT 后进入 M-TEST（而非 run.completed）。boundary 从 M-DESIGN->M-IMPL 移至 M-TEST->M-IMPL。
- `machine.py` 新增 M-TEST StageDef + `_decide_m_test` 显式控制流（类似 `_decide_approval`）。
- `kernel/events.py` EVENT_TYPES 追加 `test.collected` / `red.validated` / `test.committed`；COMMAND_KINDS 追加 `collect_tests` / `run_tests` / `check_trace` / `commit_tests`。
- 新增 `tracks/checks/` 包（trace.py + reach.py）：第六条增长轴--只读报告工具（非事件溯源）。
- `tracks/effects/opencode.py` AGENT_NAME 增补 `"shield": "Shield"`；Shield 写范围审计（tests/integration/、tests/e2e/、tests/assets/、tests/counterexamples/）。
- `tracks/deliverables.py` AGENT_DELIVERABLES 增补 Shield.md；DELIVERABLES 增补判据包 skill。
- `tracks/cli/main.py` `cmd_check` 扩展为 `trac check <deliverables|trace|reach>`。
- `tracks/executor/validate.py` `check_design_trace` 扩展 IF- 归属校验；新增 `check_story_items`（BS-XX 文法）与跨版本引用/tombstone 规则。
- `tracks/templates/` story/spec/acceptance 模板增补 ID 文法与跨版本引用；test-plan 模板增补变绿条件字段。
- 新增 `tracks/skills/test-asset-criteria/SKILL.md`（D-29 判据包，Prism 在 M-TEST PRISM_REVIEW 消费）。
- interfaces.md §5 新增 IF- 标识注册表（FR-0140 变绿条件基础）：统一定义 IF-MTEST-001/002、IF-SHIELD-001、IF-TRACE-001/002、IF-REACH-001/002、IF-VALIDATE-001 八个标识，供 test-plan §8 IF- 归属引用与 design-trace validator 有效性校验。

## 1. 模块边界

### 1.1 增长轴归属

v0.4 新代码落在既有增长轴上，不引入结构性返工：

| 包 | v0.4 增长 | 触发 |
|:---|:---|:---|
| `kernel/` | M-TEST StageDef + `_decide_m_test` + 新 reducer + State 字段 | M-TEST 子状态机（FR-0010） |
| `effects/` | Shield 接入（AGENT_NAME + 写范围审计）+ 判据包物化 + executor 新 handler | Shield agent（FR-0120）+ M-TEST 副作用（FR-0030/0050/0070） |
| `checks/` | 新包：trace.py + reach.py（只读报告工具，非事件溯源） | 需求追踪工具（FR-0080/0090） |
| `executor/` | validate.py 扩展（IF- 校验 + ID 文法）+ executor.py 新 handler | 模板/校验扩展（FR-0130/0140）+ M-TEST 命令（FR-0030/0050/0070） |
| `cli/` | cmd_check 扩展（trace/reach 子命令） | CLI 新交付入口（FR-0080/0090） |
| `templates/` | story/spec/acceptance/test-plan 模板增补 | ID 文法 + 变绿条件（FR-0130/0140） |
| `skills/` | 新增 test-asset-criteria skill | D-29 判据包（FR-0040） |
| `agents/` | Shield.md 加入 deliverables 一致性集合 | Shield agent（FR-0120） |

`tracks/checks/` 是 v0.4 引入的第六条增长轴（只读报告工具），与事件溯源的四轴及 discuss 旁路正交：它不导入 kernel/effects，不被 project/decide 引用，仅被 cli（trac check 子命令）与 executor（M-TEST EXIT 门禁调用 trace）调用。分层约束：`checks/` 可读文件系统（版本目录 + tests/ 目录 + pyproject.toml），是其报告职责，不属于"effects 唯一副作用边界"。

### 1.2 M-TEST 控制流（kernel/machine.py）

M-TEST 子状态机（SM-01）与既有 DRAFT/REVIEW/EXIT 模式差异较大，`decide()` 为 M-TEST 增加显式控制流分支 `_decide_m_test(s, sub)`，不能仅靠 StageDef 注册驱动（类似 v0.3 的 `_decide_approval`）。该控制流仍维持 kernel 纯函数边界（NFR-0030）。

`_decide_m_test` 按 substate 分发：

- `DISPATCH`：产出 `dispatch_agent(role=shield, substate=WRITE)` command，携带 test-plan 层归属与变绿条件。进入 WRITE。
- `WRITE`：产出 `dispatch_agent(role=shield, substate=WRITE)` command（重派时携带 failure evidence）。Shield outcome 后由 executor 产出 `outcome.received`，decide 根据 status 路由：done -> COLLECT；failed -> 重派（<=3 升级）。
- `COLLECT`：产出 `collect_tests` command。executor 独立执行 collection，产出 `test.collected(passed|failed)`。decide 路由：passed -> PRISM_REVIEW；failed -> WRITE 重派。
- `PRISM_REVIEW`：产出 `dispatch_agent(role=prism, substate=PRISM_REVIEW)` command，携带判据包名称+版本。executor dispatch Prism，校验 outcome 携带的判据包 identity（反自述回读），产出 `prism.verdict(pass|revise)`。decide 路由：pass -> RED_CHECK；revise -> WRITE 重派。
- `RED_CHECK`：产出 `run_tests` command。executor 独立复跑 integration/e2e，分类失败，产出 `red.validated(valid|invalid)`。decide 路由：valid -> EXIT；invalid -> DIAGNOSE。
- `EXIT`：产出 `check_trace` command。executor 调用 `trac check trace`，产出 `verdict.passed(trace)` 或 `verdict.failed(trace)`。decide 路由：passed -> 产出 `commit_tests` command（冻结测试资产）；failed -> WRITE 重派（SM-01.15，<=3 升级）。`commit_tests` 完成后产出 `test.committed` + `stage.exited(M-TEST)` + `run.completed(boundary)`。
- `DIAGNOSE`：由 executor 的 DIAGNOSE handler 产出 `verdict.failed(classification)` 事件，decide 根据 classification 路由：test_defect -> WRITE；stub_gap -> rollback_stage(M-DESIGN)；ac_gap -> awaiting_human（Human 批准后 rollback_stage(M-ACC)）；spec_gap -> awaiting_human（Human 批准后 rollback_stage(M-SPEC)）。

M-TEST 的 attempt 预算：WRITE validate 失败、PRISM_REVIEW revise、EXIT trace 复跑失败均消耗同一 <=3 预算（第 3 次升级 `awaiting_human`/escalation）。DIAGNOSE 的 test_defect 路由回 WRITE 也消耗此预算。stub_gap/ac_gap/spec_gap 不消耗预算（它们路由到其他阶段）。

### 1.3 Composition Root

M-TEST 的装配入口是 `trac run`（与既有阶段同构）。每条 required AC 的六元组 composition 列在此节可追溯：

**M-TEST 阶段入口路径**：

```
trac run
  -> tracks.cli.main:cmd_run
    -> Executor(store, repo, run_id).run_loop()
      -> kernel.machine.decide(state)
        -> _decide_m_test(s, sub)  # M-TEST 显式控制流
          -> Command(kind="dispatch_agent", params={role, substate, assignment})
          -> Command(kind="collect_tests")
          -> Command(kind="run_tests")
          -> Command(kind="check_trace")
          -> Command(kind="commit_tests")
      -> Executor._execute(cmd, state)
        -> _do_dispatch_agent  # Shield/Prism dispatch via effects backend
        -> _do_collect_tests    # pytest --collect-only subprocess
        -> _do_run_tests        # pytest subprocess + Red classification
        -> _do_check_trace      # tracks.checks.trace.check_trace_full_file()
        -> _do_commit_tests     # git add tests/ + commit
      -> store.append(event)    # test.collected / red.validated / etc.
```

**trac check trace/reach 入口路径**（独立 CLI，非事件溯源）：

```
trac check trace
  -> tracks.cli.main:cmd_check
    -> tracks.checks.trace.check_trace_full_file(version_dir, tests_dir)
      -> 纯函数：解析 story/spec/acceptance + 测试 marker -> TraceReport

trac check reach
  -> tracks.cli.main:cmd_check
    -> tracks.checks.reach.check_reach_file(repo)
      -> 纯函数：解析 pyproject [project.scripts] + ast import 图 -> ReachReport
```

六元组闭合（每条 required AC 的 owner/surface/composition/wiring/test/evidence，附 IF- 标识对齐 interfaces.md §5）：

- **FR-0010（M-TEST 注册）**：owner=kernel/machine.py, surface=`trac run`/`trac status`, composition=Executor.run_loop->_decide_m_test, wiring=cmd_run->decide->_decide_m_test->dispatch_agent, test=integration test_m_test_cycle + e2e test_m_test_journey, evidence=`trac status` 报告 `stage=M-TEST substate=DISPATCH` + 事件流 `stage.entered(M-TEST)`, IF-=IF-MTEST-001。
- **FR-0020（WRITE）**：owner=kernel+effects, surface=`trac run`, composition=_decide_m_test->dispatch_agent(shield), wiring=decide->dispatch_agent->OpencodeBackend.act(shield)->outcome.received, test=integration test_shield_dispatch + e2e test_m_test_journey, evidence=宿主 tests/integration/ 与 tests/e2e/ 下存在测试文件 + `outcome.received` 事件, IF-=IF-MTEST-001 / IF-SHIELD-001。
- **FR-0030（COLLECT）**：owner=executor, surface=`trac run`, composition=_decide_m_test->collect_tests, wiring=decide->collect_tests->_do_collect_tests->test.collected, test=integration test_m_test_cycle, evidence=`test.collected(passed)` 事件, IF-=IF-MTEST-001 / IF-MTEST-002。
- **FR-0040（PRISM_REVIEW）**：owner=kernel+effects, surface=`trac run`, composition=_decide_m_test->dispatch_agent(prism)+判据包, wiring=decide->dispatch_agent->OpencodeBackend.act(prism)->prism.verdict, test=integration test_criteria_pack, evidence=`prism.verdict` 事件 payload 含判据包 identity, IF-=IF-MTEST-001 / IF-MTEST-002。
- **FR-0050（RED_CHECK）**：owner=executor, surface=`trac run`, composition=_decide_m_test->run_tests, wiring=decide->run_tests->_do_run_tests->red.validated, test=integration test_red_check, evidence=`red.validated(valid|invalid)` 事件, IF-=IF-MTEST-001 / IF-MTEST-002。
- **FR-0060（DIAGNOSE）**：owner=kernel+executor, surface=`trac run`, composition=_decide_m_test(DIAGNOSE)->verdict.failed, wiring=decide->verdict.failed->_on_verdict_failed(M-TEST branch), test=integration test_diagnose, evidence=`verdict.failed(classification)` 事件 + `stage.rolled_back` 事件, IF-=IF-MTEST-001 / IF-MTEST-002。
- **FR-0070（EXIT）**：owner=executor, surface=`trac run`, composition=_decide_m_test(EXIT)->check_trace->commit_tests, wiring=decide->check_trace->_do_check_trace->verdict->commit_tests->test.committed->stage.exited, test=integration test_m_test_exit + e2e test_m_test_journey, evidence=`test.committed` + `stage.exited(M-TEST)` + `run.completed(boundary)` 事件 + git log 含受控测试 commit, IF-=IF-MTEST-001 / IF-MTEST-002 / IF-TRACE-002。
- **FR-0080（trac check trace）**：owner=checks/trace.py, surface=`trac check trace` CLI, composition=cmd_check->check_trace_full_file, wiring=cli->checks.trace->TraceReport, test=integration test_check_trace + ground_truth trace_reference, evidence=CLI stdout/stderr/exit code + `--json` 输出, IF-=IF-TRACE-001 / IF-TRACE-002。
- **FR-0090（trac check reach）**：owner=checks/reach.py, surface=`trac check reach` CLI, composition=cmd_check->check_reach_file, wiring=cli->checks.reach->ReachReport, test=integration test_check_reach + ground_truth reach_reference, evidence=CLI stdout/stderr/exit code + `--json` 输出, IF-=IF-REACH-001 / IF-REACH-002。
- **FR-0100（存量基线）**：owner=checks/trace.py+reach.py, surface=`trac check trace`/`trac check reach` CLI, composition=check_trace_full_file+check_reach_file 读取 baseline, wiring=cli->checks->baseline.json->TraceReport/ReachReport, test=integration test_baseline_exemption, evidence=CLI 输出不含基线豁免项, IF-=IF-TRACE-002 / IF-REACH-002。
- **FR-0110（双格式输出）**：owner=checks/trace.py+reach.py, surface=CLI, composition=check_trace_full_file+check_reach_file, wiring=cli->checks->Report, test=integration test_check_trace + test_check_reach, evidence=默认人类可读 + `--json` 输出 + 退出码, IF-=IF-TRACE-002 / IF-REACH-002。
- **FR-0120（Shield 接入）**：owner=effects/opencode.py+deliverables.py, surface=`trac run`+`trac check deliverables`, composition=_do_dispatch_agent(shield)+check_deliverables, wiring=decide->dispatch_agent->OpencodeBackend.act(shield)+audit, test=integration test_shield_dispatch + unit test_deliverables, evidence=Shield.md 存在 + version/IQ frontmatter + 写范围审计回滚, IF-=IF-SHIELD-001。
- **FR-0130（ID 文法）**：owner=executor/validate.py+templates, surface=`trac validate`, composition=validate_document->check_story_items+template, wiring=cli->validate->check_template, test=integration test_id_grammar, evidence=`trac validate --file story.md` 校验 BS-XX 文法, IF-=IF-VALIDATE-001。
- **FR-0140（变绿条件）**：owner=executor/validate.py+templates, surface=`trac validate --file test-plan.md`, composition=validate_document->check_design_trace(IF-), wiring=cli->validate->check_design_trace, test=integration test_green_condition, evidence=`trac validate --file test-plan.md` 校验 IF- 归属, IF-=IF-VALIDATE-001。

## 2. Scaffold 宣言

- tracks/checks/__init__.py — checks 包初始化（空文件，声明包边界）（kind: stub）
- tracks/checks/trace.py — trac check trace 工具桩：check_trace_full/check_trace_full_file/TraceReport 签名，行为体 raise NotImplementedError("IF-TRACE-*")（kind: stub）
- tracks/checks/reach.py — trac check reach 工具桩：check_reach/check_reach_file/ReachReport 签名，行为体 raise NotImplementedError("IF-REACH-*")（kind: stub）
- tracks/skills/test-asset-criteria/SKILL.md — D-29 测试资产判据包 skill（语义判据：忠于 AC + 断言落公开出口 + counterexample 绑定 + 无伪测试；不含形式校验规则）（kind: config）
- tests/ground_truth/trace_reference.py — trace 工具独立参考实现（不 import tracks，标准库 re/pathlib；扫描 story/spec/acceptance + 测试 marker，重算孤儿清单）（kind: ground-truth）
- tests/ground_truth/reach_reference.py — reach 工具独立参考实现（不 import tracks，标准库 ast/pathlib/re；构建模块级 import 图，重算孤岛集合）（kind: ground-truth）
- .github/workflows/ci.yml — CI workflow 骨架（lint/test/coverage/deliverables/trace/reach jobs；trace/reach job 标注待 Devon 实现子命令后激活）（kind: ci-skeleton）

## 3. 技术选型

### 3.1 需求追踪工具（tracks/checks/）

- **trace 工具**：纯 Python 标准库（re/pathlib/json）。解析 story.md（BS-XX）、spec.md（FR-XXXX/NFR-XXXX）、acceptance.md（AC-FRXXXX-YY）、tests/ 下 `.py` 文件中的长格式 marker `AC-FRXXXX-YY@<version>`。复用 `executor/validate.py` 既有扫描助手（`_spec_items`/`_acc_scan`），避免重复实现 AC<->FR 逻辑。新增 BS 扫描（story `### BS-XX`）与 test marker 扫描（regex `AC-(N?FR)\d{4}-\d{2}@v\d+\.\d+`）。
- **reach 工具**：纯 Python 标准库（ast/pathlib/re/json）。`ast.parse` 静态分析 `.py` 文件 import 语句，构建模块级 import 图。入口点发现：解析 `pyproject.toml` `[project.scripts]` 段（targeted regex parser，避免引入 tomli 依赖）、扫描包目录下 `__main__.py`、读取显式白名单（`.tracks/reach-entries.txt`）。
- **取舍**：不引入 `tomli`/`tomllib` 依赖（项目当前零运行时依赖，`dependencies = []`）。`[project.scripts]` 段格式简单（`key = "module:func"` 行），targeted regex parser 对此足够可靠。Python 3.11+ 的 `tomllib` 可作为未来增强，但 v0.4 不依赖。
- **风险**：regex parser 对非标准 TOML（多行字符串、嵌套表）不健壮；但 `[project.scripts]` 段是 PEP 621 标准的平坦 key-value 段，不涉及这些情况。

### 3.2 M-TEST 测试执行基础设施

- **collection**：executor 通过 `subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/integration/", "tests/e2e/"], cwd=repo)` 执行 collection。退出码 0 = collection 成功；非 0 = 失败。
- **RED_CHECK 复跑**：executor 通过 `subprocess.run([sys.executable, "-m", "pytest", "tests/integration/", "tests/e2e/", "--tb=short", "-q"], cwd=repo)` 执行测试。解析 stdout/stderr 分类失败：`NotImplementedError("IF-` = 桩 token 失败；`AssertionError` = 行为断言失败；`ImportError`/`ModuleNotFoundError`/`SyntaxError`/`FixtureLookupError`/collection error = 非法 Red；测试通过 = 非法（意外通过）。
- **取舍**：复用项目既有 pytest 基础设施（pyproject.toml `[tool.pytest.ini_options]`），不引入新测试框架。collection 与复跑使用与最终用户一致的命令（`pytest`），保证一致性。
- **风险**：pytest 输出格式跨版本可能变化（断言失败 traceback 格式）。缓解：分类基于退出码 + 关键字匹配（`NotImplementedError`/`AssertionError`/`ImportError` 等），不依赖精确 traceback 格式；pytest 版本已 pinned（9.1.1）。

### 3.3 Shield agent 接入

Shield 作为第六个 opencode agent 接入，与 Scribe/Sage/Lex/Archer/Prism 同构：

- `AGENT_NAME["shield"] = "Shield"`。
- Shield.md 加入 `AGENT_DELIVERABLES`（version + IQ frontmatter 校验）。
- Shield 写范围审计：`Auditor` 的 allowed 列表对 Shield dispatch 增补 `tests/integration/`、`tests/e2e/`、`tests/assets/`、`tests/counterexamples/` 四目录。越权写（产品代码、接口桩、tests/ground_truth/、设计文档）-> `over_reach` failure_class -> git 回滚。
- Shield assignment 携带 `skills: ["tracks-discuz"]`（与 Sage/Archer 同构的讨论 skill，用于 Prism revise 时锚定线程）。

### 3.4 判据包 skill（D-29）

`tracks/skills/test-asset-criteria/SKILL.md` 是 D-29 测试资产判据包，物化为 skill。Prism 在 M-TEST PRISM_REVIEW 按 assignment 指定名称+版本加载。反自述三件套：

1. assignment payload 含 `criteria_pack: {"name": "test-asset-criteria", "version": "0.1"}`（Runtime 决定，Prism 不自选）。
2. Prism verdict outcome 含 `criteria_pack: {"name": "test-asset-criteria", "version": "0.1"}`（实际加载 identity）。
3. executor `_do_dispatch_agent` 在 M-TEST PRISM_REVIEW 回读：outcome.criteria_pack 与 assignment.criteria_pack 不匹配 -> `verdict.failed(check="criteria_pack_mismatch")` -> 重派 Prism。

判据包内容为语义判据（忠于 AC、断言落公开出口、counterexample 绑定、无伪测试），不含形式校验规则（D-14 边界：marker 长格式/绑定完整性/ID 文法归 Runtime，FR-0080/0130）。

## 4. 交付与运行合同（machine contracts）

### 4.1 质量守卫栈（八类，继承 tracks 自身 canonical 配置 + v0.4 增补）

| 守卫 | 工具（pinned） | 配置位置 | 阈值 | 执行点 |
|:---|:---|:---|:---|:---|
| lint + format | ruff==0.16.0 | `pyproject.toml` `[tool.ruff]`/`[tool.ruff.lint]` | line-length=100；select E,F,W,I,B,UP,SIM,C4 | pre-commit + CI `lint` |
| 认知复杂度 | flake8==7.3.0 + flake8-cognitive-complexity==0.1.0 | `.flake8` | CCR001 ≤ 15 | pre-commit + CI `lint` |
| 重复度 | pylint==4.0.6 | `pyproject.toml` `[tool.pylint.similarities]` | R0801，min-similarity-lines=5 | pre-commit + CI `lint` |
| 文件长度 | pylint | `[tool.pylint.format]` | C0302，max-module-lines=1000 | pre-commit + CI `lint` |
| 方法长度/局部变量 | pylint | `[tool.pylint.design]` | R0915 max-statements=50；R0914 max-locals=15 | pre-commit + CI `lint`（tests 豁免） |
| 覆盖率门槛 | coverage==7.15.2 + pytest==9.1.1 | `pyproject.toml` `[tool.coverage.*]` | CI `coverage report --fail-under=95` | CI `coverage` |
| 钩子运行器 | git hooks | `.githooks/pre-commit` | 按序执行 ruff+flake8+pylint | 本地提交 + CI |
| CI required checks | GitHub Actions | `.github/workflows/ci.yml`（待实现） | lint/coverage/test/deliverables/trace/reach 全部 required | merge 门禁 |

安装命令（Archer 写入合同；执行属 Runtime 生效副作用）：

```
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
git config core.hooksPath .githooks
```

### 4.2 CI 合同（`.github/workflows/ci.yml`，ci-skeleton 待 Devon 补全为真实 workflow）

stable required checks（名称稳定，merge 只认 CI 结论）：

1. `lint`：ruff check tracks tests + flake8 tracks + pylint R0801/C0302/R0915/R0914（pre-commit 等价）。
2. `coverage`：`coverage run -m pytest && coverage combine && coverage report --fail-under=95`。
3. `test`：`pytest -q -m 'not performance'`（含 unit + integration + e2e fake 通道）。
4. `deliverables`：`trac check deliverables`（存在性 + version + IQ）。
5. `trace`：`trac check trace --json`（需求追踪闭合；待 Devon 实现子命令后激活，标注 foundation task）。
6. `reach`：`trac check reach --json`（模块可达性；待 Devon 实现子命令后激活，标注 foundation task）。

live 通道（`tests/e2e_live/`）：缺凭据 skip（独立 opt-in job），非 required check。

### 4.3 integration/e2e 测试基础设施

- **fake 通道**（deterministic，CI 必跑）：conftest 强制 `TRAC_AGENT_BACKEND=fake`。FakeBackend 经 `simulate` 控制 Shield/Prism 分支。继承 v0.2 conftest 的 `host_repo`/`trac`/`event_log` fixtures。
- **L2 contract sim**（opencode stand-in）：继承 v0.2 `fake_opencode` fixture（实现 `opencode run --format json` 协议的脚本），验证 Shield dispatch 的物化/JSON 解析/写范围审计/失败矩阵。
- **live 通道**（真 opencode）：缺凭据 skip，独立 opt-in job。
- **collection/RED_CHECK 复跑**：executor 使用 `sys.executable -m pytest` 子进程；conftest 的 `_UNDER_COVERAGE` 机制继承（subprocess coverage 合并）。

### 4.4 release version / build / artifact

继承 v0.2 §13（可安装发行物与 live E2E 安装边界）：wheel 构建、隔离 venv 安装、package data 校验。v0.4 新增 package data：`tracks/agents/Shield.md`、`tracks/skills/test-asset-criteria/SKILL.md`。`[tool.setuptools.package-data]` 增补 `agents/*.md`（已含）与 `skills/*/*.md`（已含），无需修改。

### 4.5 发布恢复

继承 v0.1-v0.3：事件溯源 append-only + 投影可重建。M-TEST 全程事件 append-only（NFR-0040）；drop 投影表后 `trac status`/`trac report` 重建的 M-TEST 状态与原一致。崩溃恢复：`_recover()` 复用 per-kind reconcile（D-13）；M-TEST 的 collect_tests/run_tests/check_trace/commit_tests 各有 reconcile 语义（幂等：已成功则跳过）。

## 5. 有意识简化与风险

### 5.1 有意识简化

- **trace 工具不读 test-plan.md**：trace 工具只读 story/spec/acceptance 三文档 + 测试 marker（FR-0080 明确"三文档"）。AC 的 required/non-required（integration|e2e vs unit）区分由 M-TEST EXIT 门禁自行过滤（AC-FR0070-03/AC-FR0080-10），trace 工具统一检查所有 AC。代价：trace 工具的退出码对任何 AC 缺口都是非零（包括 unit 层 AC 无 marker），M-TEST 门禁需解析 `--json` 报告按 required 过滤，不能仅看退出码。
- **reach 只做模块级 import 图**：不做函数级调用图（Out-of-Scope）。`ast` 解析只收集 top-level `import`/`from import`，不跟踪函数内动态 import 或 `__import__` 调用。代价：动态 import 的模块可能被误报为孤岛。
- **pyproject.toml 解析用 targeted regex**：不引入 tomli/tomllib 依赖。代价：非标准 TOML 格式可能解析失败；但 `[project.scripts]` 段是平坦 key-value，风险可控。
- **Red 分类基于关键字匹配**：不解析完整 traceback 结构，基于退出码 + 关键字（`NotImplementedError`/`AssertionError`/`ImportError` 等）分类。代价：非标准失败模式可能误分类。缓解：关键字集合是封闭的（IF-004 §1a RedClass），DIAGNOSE 路由处理未分类失败。
- **M-TEST attempt 预算共享**：WRITE/PRISM_REVIEW revise/EXIT trace 复跑失败共享同一 <=3 预算。代价：多次不同子状态失败累积快。理由：与 M-DESIGN RESPOND 同构（v0.3 既有模式），且 escalation 是处置而非退出门禁（BS-05）。
- **判据包 skill 不含形式校验**：D-14 边界。形式校验（marker 长格式、绑定完整性、ID 文法）归 Runtime 程序校验，判据包只含语义判据。代价：Prism 评审通过但 marker 不合规的情况由 EXIT trace 复跑捕获（SM-01.15 恢复路径）。
- **IF- 标识粒度对齐增长轴而非函数签名**：IF-MTEST-001（kernel 状态机）与 IF-MTEST-002（executor handler）是粗粒度标识，覆盖多个函数/事件/状态字段，而非像 IF-TRACE-001/002 那样一一对应桩函数。理由：kernel/machine.py 与 executor/executor.py 是既有文件（v0.1-v0.3），M-TEST 是扩展而非新桩；IF- 标识服务于 M-IMPL task 变绿子集划分（FR-0140），粒度应匹配 Devon 的实现 task 边界（architecture.md §1.1 增长轴），而非单个函数签名。代价：同一 IF- 标识覆盖的 AC 在 Devon 部分实现时可能处于半绿状态；缓解：M-IMPL task graph 进一步细分（ISLAND_GATE_1 输入），IF- 标识是变绿条件的外层裁剪维度。
- **IF- 标识注册表在 interfaces.md §5 而非桩代码**：IF-TRACE-001/002 与 IF-REACH-001/002 原仅作为 NotImplementedError token 存在于 checks/trace.py、checks/reach.py 桩代码中；IF-MTEST-001/002、IF-SHIELD-001、IF-VALIDATE-001 无对应桩（kernel/effects/executor 是既有文件）。v0.4 在 interfaces.md §5 统一定义为接口合同标识，design-trace validator（`check_design_trace`，FR-0140 扩展）解析 §5 注册表校验 IF- 标识有效性（非仅存在性）：test-plan §8 中出现的每个 IF- 标识必须在 §5 注册，未注册 -> 硬错误。代价：validator 需解析 interfaces.md §5 表格提取已定义 IF- 标识集合（regex 匹配 `IF-[A-Z]+-\d{3}` 模式）；IF- 标识集合是封闭的，新增须更新 §5 注册表。

### 5.2 风险

- **M-TEST 子状态机复杂度**：7 个子状态 + 15 条转移 + 4 路 DIAGNOSE 路由，是 tracks 迄今最复杂的状态机。缓解：flow.md §9.1 已完整定义；FakeAgent 端到端测试覆盖完整循环与负例（非法 Red / 意外通过 / trace 孤儿 / 短格式 marker）；SM-01 转移覆盖清单逐条核对（NFR-0040）。
- **Shield 写范围审计**：Shield 是第一个写 tests/ 目录的 agent（既有 agent 写文档或产品代码）。Auditor 需正确处理 tests/ 子目录的文件级审计。缓解：复用 v0.2 audit 机制（baseline + post-run diff），allowed 列表增补四目录；越权写回滚已有测试覆盖（test_opencode_backend.py::test_over_reach_detected_and_rolled_back）。
- **trace 工具与既有 check_trace 的关系**：既有 `executor/validate.py::check_trace`（FR-0170，acceptance AC<->FR）与新增 `checks/trace.py::check_trace_full`（FR-0080，BS->FR->AC->test 全链）共享扫描逻辑但范围不同。缓解：checks/trace.py 复用 validate.py 的 `_spec_items`/`_acc_scan` 助手，不重复实现；既有 `validate_document` 的 acceptance trace 行为不变（不回归）。
- **boundary 移动影响既有 e2e**：M-DESIGN EXIT 不再产生 `run.completed`，而是 `stage.entered(M-TEST)`。既有 `test_full_journey.py::test_full_journey_to_boundary` 需更新（AC-FR0010-04：M-DESIGN 之前事件前缀稳定，boundary 终态仍为 "boundary" 但移至 M-TEST 后）。缓解：AC-FR0010-04 明确前缀稳定；测试更新是 planned change（test-plan 标注）。
