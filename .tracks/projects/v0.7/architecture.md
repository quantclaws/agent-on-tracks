---
architecture_id: ARCH-007
spec_ref: SPEC-007
created: 2026-08-24
status: draft
sha:
---

# v0.7-A — 架构

本文是 ARCH-006 的增量延伸。v0.1～v0.6 的事件溯源、单写者、canonical 阶段、M-TEST D-41 选择语义、M-IMPL RGR/FULL 链、hotfix 与既有 CLI 均保持不变；v0.7-A 在其上增加 Phase 0 可信基线、canonical quality guard registry、Test Authenticity Gate、语言中立 mutation evidence、host adapter seam、candidate-bound trace 与双宿主演证。本版不注册 M-VERIFY，不做 CI SHA 回读、制品构建或发布自动化。

## 0. 延续性声明（什么不变）

### 0.1 继承 ARCH-006

- 唯一生产路径 `cli -> kernel.machine.decide -> executor.execute -> store.append`、SQLite append-only events、投影可重建、单写者锁与 per-kind reconcile 不变。
- `kernel/` 纯控制、`executor/` 副作用、`checks/` 静态闭合、`effects/` 外部通道与 `cli/` 交付面的分层不变；v0.7-A 新增 `adapters/` 作为唯一宿主测试框架语义边界。
- v0.6 的 R1/R2/REMOVED 分类、SELECT_R2/SELECT_TASK/SELECT_DIFF/FULL、selection/evidence identity、失败台账与 FULL 链不变。FR-0260 只替换“Red 是否真实”的资格判定，不改变选择集合。
- `.tracks/projects/project.toml` 的三层 `collect`/`run`/`run_selected`、`{nodes}`/`{result}` 所有权与 `[nightly]` 继续有效；本版追加 `[adapter]`；`[layout.shield]` 为精确集（tests/{integration,e2e,e2e_live,counterexamples,assets,_support}/ ），Shield 永不获得 tests/ 根前缀授权；Devon 的 `red_test_paths`（tests/unit）豁免 forbidden 判定，RED 单测资产冻结由 layout 域派生继续保证。
- 既有 `trac run/status/replay/report/check/validate` 是全部交付面；无新增顶层 CLI 命令或 canonical 阶段。
- v0.6 的 37 个 IF 标识不可重定义或复用；IF-007 只追加新标识。
- wheel 安装、隔离 venv、源码树外 cwd、GitHub Actions stable required checks 与 live channel 三层机制不变。

### 0.2 v0.7-A 变更

- 新增 `kernel/phase0.py` 与 `executor/phase0.py`：M-DESIGN EXIT 后、首次 M-TEST 派发前运行 SM-01；扫描并真实绑定 v0.6 trace 缺口，执行覆盖率/守卫/环境校验，封存 v0.6。
- 在 architecture.md §4.2 内嵌唯一 machine-readable TOML registry，并新增 `executor/guard_registry.py`：Runtime、pre-commit、CI 三处语义 parity 由程序 fail-closed 校验；不创建额外 `.tracks/projects/guards.toml` 真相源。
- 新增 `executor/authenticity.py`：按冻结 baseline 判定 `new|existing`；新行为要求 AC/IF-specific legal Red，既有行为允许绿但必须有 AC-specific counterexample kill。
- 新增 `executor/mutation.py`：最小 manifest、隔离 worktree 实验、target/control、rollback 与 crash recovery。
- 新增 `adapters/base.py` 与 `adapters/reference_pytest.py`：kernel/executor/cli 仅消费 `tracks-test-result` v1；pytest/JUnit 语义全部下沉到 reference adapter。
- `checks/trace.py` 与 ISLAND_GATE_2 增强为 candidate-bound closure；`executor/demo_host.py` 在真实安装路径创建最小宿主并完成双宿主 9 类 fail-closed 演证。
- EVENT_TYPES 追加 `phase0.*`、`guard.parity`、`authenticity.judged`、`mutation.*`、`demo.equivalence`、`failclosed.*`；COMMAND_KINDS 追加 `phase0_validate`、`check_guard_parity`、`mutation_verify`、`demonstrate_failclosed`。
- registry 冻结 `.githooks/pre-commit` 去除 `--exit-zero` 并与 CI scope 对齐的目标形态；当前软 hook 是 Phase 0 的已知 mismatch 输入，违规清零后由 Runtime `deploy_guard_configs` 更新/激活。`pyproject.toml` 注册 `integration`/`e2e` marks。
- 【R 修订（lineage 回滚重入）】冻结 task graph schema v2 层分家合同（interfaces §5 IF-IMPL-007）：tasks.json 根 `"schema": 2`、每 task 显式 `unit_refs`/`acceptance_refs` 分家（acceptance 仅 integration 层）、§8 e2e 目标为 ISLAND_GATE_2/FULL 兜底的终态锚点不进 task 验收声明、commit 期 integration 锚点闭合 fail-closed。该合同由本 run M-IMPL PLANNING 轮 Prism findings（B50/#65、B52/#68）裁定并已由 Runtime 三层机器强制（parse/commit/GREEN 门）；本修订将其从实现事实提升为设计合同。触发本次重入的 TASK_REVIEW lineage 失败（G trailers 与不可变 R lineage 不匹配）按 B57 语义路由 awaiting=rollback → Human 批准丢弃 M-IMPL 进度 → `trac recover` 重回 M-DESIGN；R refs/G commits 不可变使重试必然确定性复败，该恢复边界是既有 RGR machine contract 的运行时修正，不改变 v0.7-A 模块边界、事件封闭集与 IF 集合。

## 1. 模块边界

### 1.0.1 增长轴归属

| # | 包/文件 | v0.7-A 增长 | 触发 | IF |
|:--|:--|:--|:--|:--|
| 1 | `cli/main.py` | `status`、`replay/report` 展示 Phase 0、parity、authenticity、演证；既有 `check trace`/`validate` 参数不变 | FR-0256～0266 | IF-PHASE-001, IF-CLOSURE-001 |
| 2 | `kernel/phase0.py`、`kernel/m_test.py`、`kernel/machine.py`、`kernel/events.py` | Phase 0 reducer/路由、Authenticity Gate 接线、新事件/命令封闭集、演证出口门禁 | FR-0256/0257/0260/0266 | IF-PHASE-001, IF-AUTH-001 |
| 3 | `executor/phase0.py` | gap scan、coverage 判定、seal manifest | FR-0256/0257 | IF-PHASE-001/002/003 |
| 4 | `executor/guard_registry.py` | registry 读取、八类校验、三处 parity、宿主配置部署 | FR-0258/0259 | IF-GUARD-001/002 |
| 5 | `executor/authenticity.py` | `new|existing` 分类、legal Red 与 unrelated failure 判定、existing-green kill 前置 | FR-0260/0261 | IF-AUTH-001/002 |
| 6 | `executor/mutation.py` | manifest 校验、isolated worktree target/control 实验、WAL/replay | FR-0262/0263 | IF-MUTATION-001/002 |
| 7 | `adapters/base.py`、`adapters/reference_pytest.py` | language-neutral protocol 与首个 reference adapter；新增长轴只承载宿主语言/框架差异 | FR-0264 | IF-ADAPTER-001/002/003 |
| 8 | `checks/trace.py`、`executor/m_impl_runtime.py` | candidate-bound closure 与 ISLAND_GATE_2 双闭合 | FR-0265 | IF-CLOSURE-001 |
| 9 | `executor/demo_host.py`、`tracks/assets/demo_host/` | wheel 安装的动态最小宿主、路径等价与 9 场景演证 | FR-0266/NFR-0142 | IF-DEMO-001, IF-FAILCLOSED-001 |

### 1.0.2 Phase 0 与封存

Phase 0 不是 canonical stage。版本能力 `version >= v0.7` 且 `phase0_status != SEALED` 时，M-DESIGN EXIT 到 M-TEST 入口的 Runtime 前置门先执行 `phase0_validate`。SM-01 投影封闭集为 `UNSEALED | PHASE0_VALIDATING | SEALED | BLOCKED`；未列出的转移非法。

1. 扫描 v0.6 全部 approved AC 的 test-plan outlet、实际 collect inventory 与执行证据；已知缺口 AC-FR0250-03、AC-NFR0130-01、AC-NFR0130-02@v0.6 必须经 adapter `run_selected` 取得真实 node id、node digest、selection/evidence identity。
2. marker-only 不产生 binding；不可 collect、节点缺失或 identity 不可恢复发出 `phase0.blocked`，后续 M-TEST 不派发。
3. Runtime 真跑 registry 的 coverage/guard 命令，要求 coverage ≥95%、source omit 为空、守卫零违规且 parity 通过；D-18 live 目录隔离不是 source coverage omit。
4. `phase0.sealed` 引用 seal blob：v0.6 六文档 digest、全部冻结 test node digest、marks/env/adapter 验证结果。后续角色 scope 排除 v0.6 文档与冻结 integration/e2e；任何漂移 fail-closed。

### 1.0.3 Canonical quality guard registry

每个被验宿主的 architecture.md §4.2 中唯一 `[quality_registry]` TOML block 是该宿主的 canonical registry：tracks 使用本文 §4.2，demo-pytest 使用 wheel asset `tracks/assets/demo_host/architecture.md`，部署到 demo repo `.tracks/projects/v0.1/architecture.md`。两者使用 IF-GUARD-001 同一 schema、`load_guard_registry`、八类校验与 digest 算法；一个 repo 不得同时选择两个 registry source。每项包含 category、pinned tool、canonical command、config path/section、config digest、scope、threshold、timeout、failure policy、execution points、required check。`check_guard_parity` 对 Runtime gate、`.githooks/pre-commit`、`.github/workflows/ci.yml` 做语义归一化比较：argv0 用 IF-RUNCONTRACT-001 同一解析，允许 CI 的 PATH argv0 与 `.venv/bin/*` 等价；tool/rule/scope/threshold/命令不一致、missing、config digest 漂移、`--exit-zero` 均阻断。

`deploy_guard_configs` 是两个宿主共用的部署入口：从传入 registry 生成/验证 pre-commit 与 CI guard steps。tracks 自身现有文件须与 tracks registry 生成语义等价；demo provisioner 先把 asset architecture 放到上述 canonical 落点，再调用同一 loader/deployer，禁止专用 parser、专用 guard patch 或预制 hook/CI。`same registry-digest` 指一个宿主内 Runtime/pre-commit/CI 与 deployment record 都引用该次 `GuardRegistry.digest`；它不要求 tracks 与 demo-pytest 的 digest 相等。两宿主因产品 scope、file-length 1200/500、required checks 六项/三项而有意使用不同 digest，差异冻结于各自 registry。Prism 对 registry 缺类、软放行、parity 不一致或无 CI 真实执行证据判 REVISE。

### 1.0.4 Test Authenticity Gate

M-TEST 的 SELECT_R2 与节点合法失败明细继续由 D-41 产生；资格判定新增 `authenticity.judged`，以 AC 为单位覆盖其全部绑定节点：

- `new`：AC/IF 不在 `phase0.sealed` baseline；绑定节点必须在 pre-implementation tree 上产生行为断言失败、桩 token 失败或 symbol 缺失。collection/import/fixture/语法失败非法。
- `existing`：AC/IF 在冻结 baseline；未变异 candidate 可绿，但必须引用同 AC 的 `mutation.experiment(passed)` counterexample kill，且 Prism 已审查反例语义与最小性。
- 与 AC 无绑定的失败记入 `unrelated[]`，不作 Red；target 未 collect、adapter result 畸形、control 失败、kill 缺失均阻断且不产 `prism.verdict`。
- R2 空集 fail-closed、hotfix unit-only 旁路、`test.baseline_captured`/`test.selected`/`red.validated` 与 selection identity 不变。

### 1.0.5 Mutation evidence protocol

每个 manifest 只绑定一个 AC/IF 与一个 candidate/patch；字段封闭于 IF-MUTATION-001。node ID 对 kernel 是 opaque string，框架解释只归 adapter。Runtime 在隔离 worktree 按序执行 baseline identity、`git apply`、实际 diff digest、target kill、controls green、rollback clean；每步先落 WAL 或持久化结果 blob，再推进。测试路径不在 allowed scope；空/no-op/broad patch 无 selectable diff identity，不能批量背书。

实验身份为 manifest digest + baseline/candidate/patch digest + runner/env；重启发现已持久化完整结果则复用，缺结果即重跑，绝不补造 passed。`executor/worktree.py` 继续是唯一 worktree 副作用入口。

### 1.0.6 Adapter seam 与语言中立

宿主通过 `.tracks/projects/project.toml [adapter]` 声明 `id="reference-pytest"`、`protocol="tracks-test-result"`、`version=1`。所有 collect/run 路径先 `resolve_adapter`；kernel/executor 只见 `TestNode`/`TestRunResult`，不解析 exit code、JUnit XML 或框架节点语法。

reference adapter 承接 v0.6 的 collect、`{nodes}`/`{result}` 展开、JUnit normalize 与 exact selected-node coverage。v0.7 execution extension 在既有 `test.selected` audit payload 上写入实际 resolver 返回的 `adapter="reference-pytest"`、`protocol="tracks-test-result"`、`protocol_version=1`；这些字段来自运行中的 Adapter 对象，不从 project.toml 字符串原样回显。`trac validate` 扫描 `tracks/kernel/`、`tracks/executor/`、`tracks/cli/` 的运行时代码，禁止 `pytest|junit|java` 语言 token；`tracks/adapters/` 与声明性 contract/data 是唯一允许区。未知 adapter、协议版本错或结果畸形 fail-closed。

### 1.0.7 Candidate-bound trace

`trac check trace --version v0.7` 对每条 approved AC 连接：acceptance → test-plan/public outlet → collected node → frozen-baseline authenticity evidence → manifest/experiment → 同 candidate FULL pass。candidate/patch digest、selection/evidence identity、node result 必须一致；missing node、skip/xfail、identity drift、control failure 形成 hard_errors。ISLAND_GATE_2 调用同一检查，二者都通过才可 `stage.exited(M-IMPL)`。

### 1.0.8 双宿主演证

ISLAND_GATE_2 后、boundary 前执行 `demonstrate_failclosed`，不是新阶段。tracks 与动态 `demo-pytest` 分别演证闭合集：`broad_mutation | stale_patch | wrong_candidate | uncollected_node | unrelated_red | target_survived | control_hit | malformed_adapter_result | guard_parity_mismatch`。每场景在隔离副本中注入且必须被对应真实 gate 阻断，落 `failclosed.demonstrated`；每宿主 9 条齐全才落 `failclosed.summary(all_fail_closed=true)`。

demo 模板随 wheel 位于 `tracks/assets/demo_host/`；Runtime 从安装 wheel 复制模板到 fresh venv 外的临时 repo，执行真实 `trac init`、contract/registry 部署、`git config core.hooksPath .githooks` 与相同 adapter/Runtime 路径。`demo.equivalence` 记录 wheel SHA、import path、venv、canonical architecture path、registry digest、hooks、CI binding、adapter；不得 import tracks 私有测试 helper。中断演练按事件回放续跑缺失场景。

### 1.0.9 Version capability composition seam

既有 `cli.main`、`Executor`、`kernel.machine`、`m_impl_runtime` 只拥有一个语言无关、版本无关的 capability seam：按目标 version 解析可用 extension，并调用其 `before_mtest`、event reducer/status-report renderer、trace checker 与 `island_gate_2` callback。该 seam 只负责注册、调用顺序和“未知/缺 callback 即阻断”，不得内置 Phase 0、mutation、宿主语言或 candidate-bound 的成功结论。

v0.7 extension 在通用 seam 已存在后才装配 `kernel.phase0`、`executor.phase0`、guard/authenticity/mutation 与 demo handlers。`executor.phase0` 保持可独立测试的领域函数，不反向 import composition root；Phase 0 入口、BLOCKED 路由和 reducer 由 v0.7 extension 注册。`checks.trace` 只提供一个 candidate-bound 纯检查器：CLI 的 v0.7 trace callback 与 M-IMPL `ISLAND_GATE_2` callback 都调用该函数，禁止各自重算或自报 pass。v0.6 及更早版本不选择 v0.7 extension，因此其 classic trace JSON、stage 路由和 journey event prefix 保持不变。

该 seam 是内部装配决策，不新增 public IF 或顶层 CLI；其外部可观察结果仍完全落在 IF-PHASE/IF-CLOSURE/IF-ADAPTER 的事件、CLI 与 file outlets。此分层让实现任务按“通用 composition seam → 领域模块 → v0.7 registration”依赖，不要求领域任务越界修改 `machine.py`/`m_impl_runtime.py`。

### 1.0.10 角色所有权

| # | 职责 | Archer | Shield | Devon | Prism | Runtime |
|:--|:--|:--|:--|:--|:--|:--|
| 1 | registry、adapter/test commands、接口桩与 demo 数据 | ✅ 独家设计/脚手架 | ❌ | ❌ | 评审 | 激活/执行 |
| 2 | integration/e2e 与 counterexample patch | ❌ | ✅ | ❌ | 独立审查 | 冻结/执行 |
| 3 | candidate/业务实现 | ❌ | ❌ | ✅ | 评审 | 门禁/提交 |
| 4 | new/existing、Red、parity、mutation、closure 判定 | ❌ | ❌ | ❌ | 消费证据 | ✅ 程序独家 |
| 5 | 修改 Phase 0 冻结测试 | ❌ | ❌ | ❌ | ❌ | ❌（只验证 digest） |
| 6 | 安装 venv/hooks、绑定 CI required checks | 只声明 | ❌ | ❌ | ❌ | ✅ 副作用独家 |

### 1.1 Composition Root

**Phase 0 路径**：

```text
trac run -> cli.main.cmd_run -> Executor.run_loop -> kernel.machine.decide
  -> version capability seam resolves v0.7.before_mtest
  -> v0.7 registration invokes kernel.phase0; entry guard sees phase0_status != SEALED
  -> Command(phase0_validate)
  -> Executor._do_phase0_validate
  -> executor.phase0.scan_trace_gaps
  -> adapters.base.resolve_adapter(project.adapter).run_selected
  -> executor.guard_registry.check_parity + real guard/coverage commands
  -> phase0.baseline_repaired/coverage/guard_hardened/sealed|blocked
  -> kernel.phase0 reducers -> SEALED resumes M-TEST; BLOCKED parks run
```

**Parity 路径**：`stage gate -> Command(check_guard_parity(宿主 canonical architecture_path)) -> load_guard_registry -> guard_registry.check_parity(registry, pre-commit, ci.yml) -> guard.parity -> pass|fail-closed`。

**Authenticity/mutation 路径**：

```text
M-TEST RED_CHECK -> resolve_adapter -> adapter.run_selected(R2 nodes) -> normalized TestRunResult
  -> test.selected audit(adapter/protocol/protocol_version from resolved Adapter)
  -> authenticity.classify_behaviour + judge_authenticity
  -> new: authenticity.judged(legal|illegal)
  -> existing: Command(mutation_verify)
      -> mutation.validate_manifest -> worktree isolated experiment
      -> adapter.run_selected(target/control)
      -> mutation.manifest + mutation.experiment
      -> authenticity.judged(counterexample_kill=verified|missing)
```

**Trace/演证路径**：

```text
trac check trace --version v0.7 OR ISLAND_GATE_2
  -> generic CLI callback OR generic m_impl_runtime ISLAND_GATE_2 callback
  -> v0.7 registration -> the same checks.trace candidate-bound join
  -> closure=pass|hard_errors
  -> demonstrate_failclosed
  -> demo_host.create_demo_host（copy demo architecture→load_guard_registry→deploy_guard_configs）+ verify_path_equivalence
  -> real authenticity/mutation/parity gates x 9 x 2 hosts
  -> demo.equivalence + failclosed.demonstrated/summary
  -> run.completed(boundary) only when all pass
```

### 1.2 Required AC closure (ISLAND_GATE_1)

- **FR-0256** owner=executor/phase0.py:AC-FR0256-01 surface=trac-run-Phase0+trac-replay/report composition=phase0_validate→scan_trace_gaps→adapter-run_selected-binding wiring=M-DESIGN-EXIT→M-TEST-entry-sees-unsealed→gap-scan→three-v0.6-ACs-run_selected→phase0.baseline_repaired test=integration:tests/integration/test_phase0_binding.py::test_gap_acs_bound_to_real_collected_nodes evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_binding.py`输出`passed`且events含`phase0.baseline_repaired(ac=AC-FR0250-03@v0.6,bound_node.node_id=tests/integration/test_evidence_reuse.py::test_prism_consumes_runtime_evidence_without_suite_rerun)` IF-PHASE-001
- **FR-0256** owner=executor/phase0.py:AC-FR0256-02 surface=trac-check-trace-v0.6+trac-status composition=marker-only-counterexample→trace-fail wiring=TRACKS-TRACE-marker-without-binding-event→hard_errors→no-phase0.sealed→PHASE0_VALIDATING-or-BLOCKED test=integration:tests/integration/test_phase0_binding.py::test_marker_only_closure_stays_fail evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_binding.py`输出`passed`且`trac check trace --version v0.6`非零并且无`phase0.sealed` IF-PHASE-001 IF-TRACE-002
- **FR-0256** owner=executor/phase0.py:AC-FR0256-03 surface=trac-status composition=gap-scan→repair-or-block wiring=extra-gap-same-binding-path；missing-or-uncollectable-or-identity-unrecoverable→phase0.blocked→no-later-dispatch test=integration:tests/integration/test_phase0_binding.py::test_field_gap_recovery_and_blocked_routing evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_binding.py`输出`passed`且status含`phase0=blocked`及阻塞原因 IF-PHASE-001
- **FR-0257** owner=executor/phase0.py:AC-FR0257-01 surface=trac-status+trac-report composition=phase0_validate→coverage-guard-command→phase0.coverage wiring=coverage-run+report-fail-under-95→ratio≥0.95/by=collected/exclude=none；source-omit→blocked test=integration:tests/integration/test_phase0_quality_seal.py::test_coverage_ratio_by_collected_exclude_none evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_quality_seal.py`输出`passed`且`phase0.coverage`含`ratio>=0.95,by=collected,exclude=none` IF-PHASE-002 IF-GUARD-001
- **FR-0257** owner=executor/guard_registry.py:AC-FR0257-02 surface=trac-status+trac-replay composition=hard-guard-execution→phase0.guard_hardened wiring=eight-fail-closed-guards+no-exit-zero+parity-pass→violations=0/revised=n；violation→blocked test=integration:tests/integration/test_phase0_quality_seal.py::test_guard_hardened_violations_zero_revisions_audited+tests/integration/test_commit_hook_hard_gate.py::test_phase0_hard_gate_rejects_violation_no_exit_zero evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_quality_seal.py tests/integration/test_commit_hook_hard_gate.py`输出`passed`且`phase0.guard_hardened(violations=0)`在流并且生成hook拒绝soft-bypass IF-GUARD-001 IF-GUARD-002
- **FR-0257** owner=executor/validate.py:AC-FR0257-03 surface=trac-validate+trac-status composition=marks-and-environment-contract-validation wiring=pyproject-integration/e2e/performance-marks+[adapter]+command-contract→validate-pass→status-marks=registered test=integration:tests/integration/test_phase0_quality_seal.py::test_marks_registered_and_env_contract_valid evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_quality_seal.py`输出`passed`且`trac validate --file .tracks/projects/v0.7/test-plan.md`退出0 IF-PHASE-002 IF-ADAPTER-001
- **FR-0257** owner=executor/phase0.py:AC-FR0257-04 surface=trac-status+git-audit composition=all-phase0-checks→seal-manifest→phase0.sealed wiring=v0.6-doc+frozen-test-digests→SEALED-nonreturn→layout-blocks-rewrite test=integration+e2e:tests/integration/test_phase0_quality_seal.py::test_seal_readonly_blocks_rewrite+tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_quality_seal.py tests/e2e/test_v07_journey.py`输出`passed`且status含`phase0=sealed baseline=v0.6-readonly` IF-PHASE-003
- **FR-0257** owner=kernel/phase0.py:AC-FR0257-05 surface=trac-status composition=unrecoverable-check→BLOCKED wiring=coverage-or-guard-unrecoverable→phase0.blocked→repair-revalidate-loop-or-park→no-sealed test=integration:tests/integration/test_phase0_quality_seal.py::test_unrecoverable_coverage_guard_blocked evidence=`.venv/bin/python -m pytest -q tests/integration/test_phase0_quality_seal.py`输出`passed`且status含`phase0=blocked reason=coverage|guards` IF-PHASE-003
- **FR-0258** owner=executor/guard_registry.py:AC-FR0258-01 surface=trac-validate composition=architecture-§4.2-TOML-block→load+verify-eight-categories wiring=category+pinned-tool+config-digest+scope+threshold+timeout/failure+execution-points+required-check→missing-category-nonzero test=integration:tests/integration/test_guard_registry.py::test_registry_single_source_eight_categories+tests/integration/test_commit_hook_hard_gate.py::test_phase0_hard_gate_rejects_violation_no_exit_zero evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_registry.py tests/integration/test_commit_hook_hard_gate.py`输出`passed`且缺类registry副本使validate非零、生成hook无`--exit-zero` IF-GUARD-001
- **FR-0258** owner=executor/guard_registry.py:AC-FR0258-02 surface=trac-status+trac-report composition=check_guard_parity→guard.parity wiring=registry-vs-runtime-vs-precommit-vs-ci-normalized-comparison→missing/exit-zero/scope-threshold-command-mismatch→blocked test=integration:tests/integration/test_guard_parity.py::test_parity_mismatch_blocks_fail_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_parity.py`输出`passed`且`guard.parity(status=blocked)`含不一致项 IF-GUARD-002
- **FR-0258** owner=executor/guard_registry.py:AC-FR0258-03 surface=host-.githooks+CI-files+trac-report composition=load_guard_registry→deploy_guard_configs wiring=host-canonical-architecture-path→validated-project-contract-config-digest→one-GuardRegistry.digest→runtime/precommit/ci-generation→deployment-record；generated-artifact-bytes-audited-not-fed-back；demo-copy-asset-to-.tracks/projects/v0.1/architecture.md-then-same-loader/deployer test=integration:tests/integration/test_guard_parity.py::test_deploy_mechanism_generates_host_configs evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_parity.py`输出`passed`且tracks/demo第8项分别匹配真实project.toml bytes、各宿主deployment/runtime/pre-commit/CI四者引用本宿主registry digest IF-GUARD-002
- **FR-0258** owner=executor/guard_registry.py:AC-FR0258-04 surface=trac-validate composition=v0.6-guard-migration-check wiring=ARCH-006-§4.2-eight-rows→registry-category-entries→no-silent-gap test=integration:tests/integration/test_guard_registry.py::test_registry_migration_no_silent_gap evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_registry.py`输出`passed`且v0.6八项均有registry对应项 IF-GUARD-001
- **FR-0259** owner=kernel/machine.py:AC-FR0259-01 surface=trac-run-PRISM_REVIEW+trac-status composition=Prism-registry-review→prism.verdict(revise) wiring=missing-guard-or-exit-zero-or-parity-mismatch→REVISE→Archer→no-stage-exit-evidence test=integration:tests/integration/test_guard_registry.py::test_prism_revise_routes_back_to_archer evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_registry.py`输出`passed`且status回流`Archer`并无阶段出口 IF-GUARD-001
- **FR-0259** owner=executor/guard_registry.py:AC-FR0259-02 surface=trac-report composition=registry-required-check→real-CI-evidence wiring=each-guard-required-check-name→CI-pass/fail-output；declaration-only→missing→REVISE test=integration:tests/integration/test_guard_parity.py::test_registry_real_execution_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_parity.py`输出`passed`且report引用`lint/coverage`等真实check名 IF-GUARD-001 IF-GUARD-002
- **FR-0260** owner=executor/authenticity.py:AC-FR0260-01 surface=trac-run-MTEST-RED_CHECK+trac-replay composition=classify_behaviour→judge-authenticity wiring=R2∩AC-nodes→adapter-run_selected→new-behaviour-specific-assertion-failure→authenticity.judged(new,legal,frozen-baseline) test=integration+e2e:tests/integration/test_authenticity_gate.py::test_new_behaviour_legal_red_on_frozen_baseline+tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure evidence=`.venv/bin/python -m pytest -q tests/integration/test_authenticity_gate.py tests/e2e/test_v07_journey.py`输出`passed`且`authenticity.judged(category=new,red=legal)`引用冻结baseline IF-AUTH-001
- **FR-0260** owner=executor/authenticity.py:AC-FR0260-02 surface=trac-replay composition=unrelated-failure-isolation wiring=unbound-failure→unrelated=true/excluded；uncollected-or-malformed-or-control-hit→blocked→no-prism-verdict test=integration:tests/integration/test_authenticity_gate.py::test_unrelated_downstream_failure_isolated evidence=`.venv/bin/python -m pytest -q tests/integration/test_authenticity_gate.py`输出`passed`且payload含`unrelated=true`并且blocked路径无`prism.verdict` IF-AUTH-001
- **FR-0260** owner=kernel/m_test.py:AC-FR0260-03 surface=trac-run+trac-status composition=phase0-seal-precondition wiring=no-phase0.sealed→no-authenticity-dispatch→status-phase0-incomplete test=integration:tests/integration/test_authenticity_gate.py::test_phase0_gate_blocks_mtest_before_seal evidence=`.venv/bin/python -m pytest -q tests/integration/test_authenticity_gate.py`输出`passed`且未封存时无`authenticity.judged` IF-PHASE-003 IF-AUTH-001
- **FR-0260** owner=kernel/m_test.py:AC-FR0260-04 surface=trac-replay composition=D41-selection-plus-authenticity wiring=baseline-captured/test.selected/red.validated-unchanged+authenticity.judged-coexists+empty-R2-fail-closed test=integration:tests/integration/test_authenticity_gate.py::test_d41_selection_semantics_coexist evidence=`.venv/bin/python -m pytest -q tests/integration/test_authenticity_gate.py`输出`passed`且两类事件依据可独立追溯 IF-AUTH-001
- **FR-0260** owner=executor/authenticity.py:AC-FR0260-05 surface=trac-status composition=illegal-red-rejection wiring=unrelated-red-or-broad-mutation-without-AC-diff→red=illegal→blocked→no-prism-verdict test=integration:tests/integration/test_authenticity_gate.py::test_broad_mutation_irrelevant_red_rejected evidence=`.venv/bin/python -m pytest -q tests/integration/test_authenticity_gate.py`输出`passed`且status阻断原因=`illegal_red` IF-AUTH-001 IF-MUTATION-001
- **FR-0261** owner=executor/authenticity.py:AC-FR0261-01 surface=trac-run-MTEST-PRISM_REVIEW+trac-replay composition=existing-green→mutation-kill-proof wiring=category=existing+green-allowed+same-AC-mutation.experiment-passed→counterexample_kill=verified；missing→blocked test=integration+e2e:tests/integration/test_counterexample_kill.py::test_existing_green_requires_kill_verified+tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure evidence=`.venv/bin/python -m pytest -q tests/integration/test_counterexample_kill.py tests/e2e/test_v07_journey.py`输出`passed`且`category=existing,green=allowed,counterexample_kill=verified` IF-AUTH-002 IF-MUTATION-002
- **FR-0261** owner=executor/mutation.py:AC-FR0261-02 surface=trac-replay/report composition=AC-specific-counterexample-binding wiring=kill-evidence-binds-single-AC/IF+counterexample-node→Prism-semantic/minimality-review；cross-AC-or-broad→blocked test=integration:tests/integration/test_counterexample_kill.py::test_ac_specific_binding_and_minimality_reviewed evidence=`.venv/bin/python -m pytest -q tests/integration/test_counterexample_kill.py`输出`passed`且反例身份及Prism审查记录可replay IF-AUTH-002 IF-MUTATION-002
- **FR-0261** owner=executor/phase0.py:AC-FR0261-03 surface=git-audit+trac-status composition=seal-manifest+layout-freeze wiring=frozen-test-digests+Devon-scope-excludes-integration/e2e→edit-invalid-and-blocked test=integration:tests/integration/test_counterexample_kill.py::test_frozen_tests_untouchable_role_separation evidence=`.venv/bin/python -m pytest -q tests/integration/test_counterexample_kill.py`输出`passed`且无Devon冻结测试修改记录 IF-AUTH-002 IF-PHASE-003
- **FR-0262** owner=executor/mutation.py:AC-FR0262-01 surface=trac-replay/report composition=build_manifest→validate_manifest wiring=closed-minimal-fields-only+opaque-node-ids+no-language/framework/test-body test=integration:tests/integration/test_mutation_manifest.py::test_manifest_minimal_field_set_language_neutral evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_manifest.py`输出`passed`且manifest字段集恰为IF-MUTATION-001封闭集 IF-MUTATION-001
- **FR-0262** owner=executor/mutation.py:AC-FR0262-02 surface=trac-replay composition=single-AC-endorsement-check wiring=noop-or-no-selectable-diff-or-multi-AC→mutation.manifest(blocked,no_selectable_diff_identity) test=integration:tests/integration/test_mutation_manifest.py::test_no_batch_endorsement_noop_patch evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_manifest.py`输出`passed`且blocked原因=`no_selectable_diff_identity` IF-MUTATION-001
- **FR-0262** owner=executor/mutation.py:AC-FR0262-03 surface=mutation.manifest-event composition=allowed-scope-validation wiring=tests-path-in-allowed-change-scope→blocked-tests_in_scope test=integration:tests/integration/test_mutation_manifest.py::test_tests_scope_mutation_blocked evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_manifest.py`输出`passed`且测试路径mutation被阻断 IF-MUTATION-001
- **FR-0263** owner=executor/mutation.py:AC-FR0263-01 surface=trac-replay/report composition=run_mutation_experiment wiring=isolated-worktree→baseline-verified→git-apply→diff-identity→target-kill→controls-green→rollback-clean test=integration+e2e:tests/integration/test_mutation_experiment.py::test_experiment_chain_target_kill_controls_green+tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_experiment.py tests/e2e/test_v07_journey.py`输出`passed`且五步payload均为成功值 IF-MUTATION-002
- **FR-0263** owner=executor/mutation.py:AC-FR0263-02 surface=trac-replay/report composition=append-only-experiment-events wiring=command/env/node/result/failure-signature/digests→events-without-update test=integration:tests/integration/test_mutation_experiment.py::test_events_append_only_replayable evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_experiment.py`输出`passed`且events表无既有行改写 IF-MUTATION-002
- **FR-0263** owner=executor/mutation.py:AC-FR0263-03 surface=trac-replay composition=WAL-crash-recovery wiring=interrupt→rebuild→missing-result-rerun→never-phantom-pass test=integration:tests/integration/test_mutation_experiment.py::test_crash_recovery_reruns_no_phantom_pass evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_experiment.py`输出`passed`且不存在`passed`但result缺失 IF-MUTATION-002
- **FR-0263** owner=executor/mutation.py:AC-FR0263-04 surface=trac-status composition=failure-matrix wiring=target-survived|control-hit|malformed-result|stale-patch→mutation.experiment(blocked) test=integration:tests/integration/test_mutation_experiment.py::test_failure_matrix_fail_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_mutation_experiment.py`输出`passed`且四类注入全部blocked IF-MUTATION-002
- **FR-0264** owner=adapters/base.py:AC-FR0264-01 surface=trac-run/check-execution-audit composition=resolve_adapter→collect/run_selected/normalize_result wiring=project.adapter-id→tracks-test-result-v1→kernel/executor-consume-TestNode/TestRunResult test=integration:tests/integration/test_adapter_contract.py::test_three_interface_seam_protocol_version evidence=`.venv/bin/python -m pytest -q tests/integration/test_adapter_contract.py`输出`passed`且审计含`adapter=reference-pytest,protocol=tracks-test-result,version=1` IF-ADAPTER-001
- **FR-0264** owner=adapters/reference_pytest.py:AC-FR0264-02 surface=trac-replay+test.selected-adapter-audit+trac-check-trace+early-version-trac-run composition=reference-adapter-migration+v0.7-execution-registration+version-capability-seam wiring=v0.7-resolve_adapter→collect/run_selected/normalize→test.selected(adapter/protocol/version)；v0.6-collect/run_selected/result-normalize-semantics→same-input-same-output；v0.4/v0.6-no-v0.7-extension→classic-trace/event-prefix-unchanged test=integration+e2e:tests/integration/test_adapter_contract.py::test_reference_adapter_equivalent_to_v06+tests/e2e/test_v07_journey.py::test_v07a_journey_uses_reference_adapter+tests/integration/test_check_trace.py::test_v06_trace_output_version_isolated_no_closure_leak+tests/e2e/test_full_journey_v05.py::test_v05_journey_does_not_trigger_v07_phase0_or_dualhost evidence=`.venv/bin/python -m pytest -q tests/integration/test_adapter_contract.py tests/integration/test_check_trace.py tests/e2e/test_v07_journey.py tests/e2e/test_full_journey_v05.py`输出`passed`且v0.7`test.selected`含运行中adapter身份、reference-adapter逐节点等价、早期trace无`closure`、v0.5事件无`phase0.*|failclosed.*` IF-ADAPTER-002
- **FR-0264** owner=adapters/base.py:AC-FR0264-03 surface=trac-status/report composition=resolve-or-normalize-fail-closed wiring=unknown-id-or-protocol-or-malformed/coverage-mismatch→blocked(unknown_adapter|malformed_result)→no-gate-pass test=integration:tests/integration/test_adapter_contract.py::test_unknown_adapter_malformed_result_blocked evidence=`.venv/bin/python -m pytest -q tests/integration/test_adapter_contract.py`输出`passed`且拒绝原因可审计 IF-ADAPTER-001
- **FR-0264** owner=executor/validate.py:AC-FR0264-04 surface=trac-validate composition=kernel-language-token-scan wiring=scan-kernel/executor/cli-runtime-source-for-pytest|junit|java→match-nonzero；runtime-path-adapter-only test=integration:tests/integration/test_kernel_language_neutrality.py::test_no_language_tokens_kernel_executor_cli evidence=`.venv/bin/python -m pytest -q tests/integration/test_kernel_language_neutrality.py`输出`passed`且注入token副本使validate非零 IF-ADAPTER-003
- **FR-0265** owner=checks/trace.py:AC-FR0265-01 surface=trac-check-trace-v0.7 composition=candidate-bound-closure-join wiring=approved-AC→plan/outlet→collected-node→baseline-authenticity→mutation→same-candidate-FULL-pass→ISLAND_GATE_2 test=integration+e2e:tests/integration/test_trace_closure.py::test_closure_candidate_bound_pass+tests/e2e/test_v07_journey.py::test_v07a_journey_phase0_to_closure evidence=`.venv/bin/python -m pytest -q tests/integration/test_trace_closure.py tests/e2e/test_v07_journey.py`输出`passed`且trace输出`status=pass closure=candidate-bound` IF-CLOSURE-001
- **FR-0265** owner=checks/trace.py:AC-FR0265-02 surface=trac-check-trace-v0.7+trac-status composition=closure-hard-errors wiring=node-missing|skip-xfail|identity-drift|control-failure→status=fail→no-stage.exited(M-IMPL) test=integration:tests/integration/test_trace_closure.py::test_blocking_conditions_hard_errors evidence=`.venv/bin/python -m pytest -q tests/integration/test_trace_closure.py`输出`passed`且四类hard_errors逐一出现 IF-CLOSURE-001
- **FR-0265** owner=checks/trace.py:AC-FR0265-03 surface=trac-replay composition=candidate-digest-consistency wiring=baseline/mutation/FULL-candidate-digests-equal；foreign-candidate-evidence→fail-closed test=integration:tests/integration/test_trace_closure.py::test_candidate_digest_consistency evidence=`.venv/bin/python -m pytest -q tests/integration/test_trace_closure.py`输出`passed`且他candidate证据被拒 IF-CLOSURE-001 IF-MUTATION-001
- **FR-0265** owner=executor/m_impl_runtime.py:AC-FR0265-01 surface=trac-run-ISLAND_GATE_2 composition=stale-ledger-settlement-收敛边界 wiring=island_2-OPEN-branch→真实FULL轮→非PROVEN身份且(node,signature)∉本轮失败集且node∈本轮执行集→OPEN→CLASSIFIED→FIXED（显式settlement理由；仅合法转移；无STALE发射；clean判据不变全PROVEN）→既有fallback证明一次全量PROVEN；本轮失败身份保留逐条诊断环不被吞没 test=integration:tests/integration/test_trace_closure.py::test_closure_candidate_bound_pass evidence=`.venv/bin/python -m pytest -q tests/integration/test_trace_closure.py`输出`passed`且结算后`trac replay`逐身份可见OPEN→CLASSIFIED→FIXED→PROVEN迁移链与settlement理由、M-IMPL出口可达 IF-FULLCHAIN-001 IF-LEDGER-001 IF-FAILCLOSED-001
- **FR-0266** owner=executor/demo_host.py:AC-FR0266-01 surface=trac-run-acceptance+trac-replay composition=demonstrate_failclosed wiring=nine-scenarios×tracks/demo→real-gates-block→18-detail-events→two-summaries-all_fail_closed test=integration+e2e:tests/integration/test_failclosed_scenarios.py::test_tracks_host_nine_scenarios_blocked+tests/e2e/test_dualhost_acceptance.py::test_dualhost_nine_scenarios_all_fail_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_failclosed_scenarios.py tests/e2e/test_dualhost_acceptance.py`输出`passed`且两宿主summary含`all_fail_closed=true` IF-FAILCLOSED-001 IF-FULLCHAIN-001 IF-LEDGER-001
- **FR-0266** owner=executor/demo_host.py:AC-FR0266-02 surface=trac-run+trac-replay composition=create_demo_host→load_guard_registry→deploy_guard_configs→verify_path_equivalence wiring=wheel→fresh-venv→trac-init→demo-architecture-fixed-landing→contract/registry-deploy→hooksPath+CI-binding+adapter→demo.equivalence test=integration:tests/integration/test_demo_host.py::test_demo_created_via_real_install_path evidence=`.venv/bin/python -m pytest -q tests/integration/test_demo_host.py`输出`passed`且事件含wheel-sha/architecture-path/registry-digest/hooks/adapter且import-path不在源码树 IF-DEMO-001
- **FR-0266** owner=executor/demo_host.py:AC-FR0266-03 surface=trac-replay composition=acceptance-crash-drill wiring=interrupt-valid-experiment→restart→event-replay→crash_recovery=replay_ok test=integration+e2e:tests/integration/test_failclosed_scenarios.py::test_crash_recovery_replay_ok+tests/e2e/test_dualhost_acceptance.py::test_dualhost_nine_scenarios_all_fail_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_failclosed_scenarios.py tests/e2e/test_dualhost_acceptance.py`输出`passed`且summary含`crash_recovery=replay_ok` IF-FAILCLOSED-001
- **FR-0266** owner=executor/demo_host.py:AC-FR0266-04 surface=trac-status composition=acceptance-boundary wiring=any-leaked-scenario-or-demo-inequivalence→blocked→no-v0.7-B→status-host+scenario-reason test=integration:tests/integration/test_failclosed_scenarios.py::test_any_leak_or_inequivalence_blocks evidence=`.venv/bin/python -m pytest -q tests/integration/test_failclosed_scenarios.py`输出`passed`且leak反例阻断验收 IF-FAILCLOSED-001 IF-DEMO-001
- **NFR-0140** owner=store/store.py:AC-NFR0140-01 surface=trac-replay/report+trac-status composition=append-only-events→projection-rebuild wiring=all-v0.7-evidence-events-append→drop-projection→rebuild-identical test=integration:tests/integration/test_v07_events.py::test_append_only_and_projection_rebuild evidence=`.venv/bin/python -m pytest -q tests/integration/test_v07_events.py`输出`passed`且重建前后status/report一致 IF-AUTH-001 IF-MUTATION-002
- **NFR-0140** owner=executor/mutation.py:AC-NFR0140-02 surface=trac-replay composition=evidence-identity-binding wiring=AC/IF+candidate/patch-digest+selection/evidence-identity+attempt+actor→drift-not-pass test=integration:tests/integration/test_v07_events.py::test_identity_five_part_binding evidence=`.venv/bin/python -m pytest -q tests/integration/test_v07_events.py`输出`passed`且replay身份字段完整 IF-MUTATION-001
- **NFR-0140** owner=executor/mutation.py:AC-NFR0140-03 surface=trac-replay composition=WAL-authenticity/mutation-rebuild wiring=interrupt/restart→state-identical→missing-result-rerun test=integration:tests/integration/test_v07_events.py::test_wal_replay_reruns_missing_never_pass evidence=`.venv/bin/python -m pytest -q tests/integration/test_v07_events.py`输出`passed`且无phantom-pass IF-MUTATION-002
- **NFR-0140** owner=kernel/machine.py:AC-NFR0140-04 surface=trac-status composition=fail-closed-aggregation wiring=unknown-state|missing-entry|identity-drift|control-failure→blocked→reason-event→no-closure-bypass test=integration:tests/integration/test_v07_events.py::test_unknown_missing_drift_control_fail_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_v07_events.py`输出`passed`且四类原因可审计 IF-AUTH-001 IF-MUTATION-002
- **NFR-0141** owner=executor/guard_registry.py:AC-NFR0141-01 surface=trac-replay/report composition=programmatic-parity wiring=guard.parity-carries-registry-digest+three-results→Runtime-only→agent-attestation-ignored test=integration:tests/integration/test_guard_parity.py::test_parity_event_programmatic_no_agent_selfreport evidence=`.venv/bin/python -m pytest -q tests/integration/test_guard_parity.py`输出`passed`且自述不能产生`guard.parity(passed)` IF-GUARD-002
- **NFR-0141** owner=executor/validate.py:AC-NFR0141-02 surface=trac-validate composition=language-neutrality-double-check wiring=static-token-scan+runtime-adapter-only-path→unknown/malformed-blocked test=integration:tests/integration/test_kernel_language_neutrality.py::test_language_invariant_dual_check evidence=`.venv/bin/python -m pytest -q tests/integration/test_kernel_language_neutrality.py`输出`passed`且静态与执行双检查均通过 IF-ADAPTER-003
- **NFR-0142** owner=executor/demo_host.py:AC-NFR0142-01 surface=trac-replay/report composition=path-equivalence-evidence wiring=wheel/venv/pins/canonical-architecture+registry-digest/hooks/CI/adapter-same-path→demo.equivalence(equivalent=true) test=integration:tests/integration/test_demo_host.py::test_path_equivalence_evidence evidence=`.venv/bin/python -m pytest -q tests/integration/test_demo_host.py`输出`passed`且registry/deployment/hook/CI digest一致及其余路径等价证据可replay IF-DEMO-001 IF-FULLCHAIN-001 IF-LEDGER-001
- **NFR-0142** owner=executor/demo_host.py:AC-NFR0142-02 surface=trac-replay composition=demo-crash-recovery wiring=demo-interrupt→event-replay→same-summary-without-loss test=integration+e2e:tests/integration/test_failclosed_scenarios.py::test_demo_host_crash_recovery_rebuild+tests/e2e/test_dualhost_acceptance.py::test_dualhost_nine_scenarios_all_fail_closed evidence=`.venv/bin/python -m pytest -q tests/integration/test_failclosed_scenarios.py tests/e2e/test_dualhost_acceptance.py`输出`passed`且重建结果与中断前一致 IF-FAILCLOSED-001

## 2. Scaffold 宣言

- `tracks/adapters/__init__.py` — adapter 增长轴 package marker（kind: stub）
- `tracks/adapters/base.py` — `tracks-test-result` v1 类型、Adapter protocol、resolver/error 完整签名，行为体仅 raise `IF-ADAPTER-001`（kind: stub）
- `tracks/adapters/reference_pytest.py` — reference adapter 的 collect/run_selected/normalize 完整签名，行为体仅 raise `IF-ADAPTER-002`（kind: stub）
- `tracks/kernel/phase0.py` — Phase 0 封闭状态、路由与 reducer 完整签名，行为体仅 raise `IF-PHASE-001`（kind: stub）
- `tracks/executor/phase0.py` — gap/coverage/seal 类型与纯函数签名，行为体仅 raise `IF-PHASE-001/002/003`（kind: stub）
- `tracks/executor/guard_registry.py` — registry/parity/deploy 类型与函数签名，行为体仅 raise `IF-GUARD-001/002`（kind: stub）
- `tracks/executor/authenticity.py` — new/existing 与 authenticity 判定签名，行为体仅 raise `IF-AUTH-001/002`（kind: stub）
- `tracks/executor/mutation.py` — manifest 与 isolated experiment 签名，行为体仅 raise `IF-MUTATION-001/002`（kind: stub）
- `tracks/executor/demo_host.py` — demo provisioning、路径等价与场景生成签名，行为体仅 raise `IF-DEMO-001/IF-FAILCLOSED-001`（kind: stub）
- `.tracks/projects/project.toml` — 追加 `[adapter]` 声明；既有测试命令/layout/lint 不改（kind: config）
- `pyproject.toml` — 注册 integration/e2e marks，并以显式 allowlist 打包 demo architecture/config/source/tests、排除 inherited legacy `guards.toml`（kind: config）
- `tracks/assets/demo_host/pyproject.toml` — 最小宿主 pinned Python 工具与配置模板（kind: data）
- `tracks/assets/demo_host/demo_calc.py` — demo candidate 的最小确定性源码语料（kind: data）
- `tracks/assets/demo_host/tests/unit/test_demo_calc.py` — demo target/control unit node 语料（kind: data）
- `tracks/assets/demo_host/tests/integration/test_demo_contract.py` — demo integration node 语料（kind: data）
- `tracks/assets/demo_host/tests/e2e/test_demo_journey.py` — demo e2e happy node 语料（kind: data）
- `tracks/assets/demo_host/tracks-project.toml` — demo 三层 test contract、nightly 与 adapter 模板（kind: data）
- `tracks/assets/demo_host/flake8.ini` — demo cognitive-complexity 配置（kind: config）
- `tracks/assets/demo_host/architecture.md` — 部署到 demo `.tracks/projects/v0.1/architecture.md` 的正式 eight-category canonical registry（kind: data）

> **Prism [RESOLVED]:** PRISM-ARCH007-R2-02 [blocker|判据2 六元组闭合 + 判据7 可实现性]：scaffold 已交付 tracks/assets/demo_host/guards.toml，但该文件 schema 在设计三件套中无任何合同：interfaces §1k 只承认 architecture.md §4.2 下 [quality_registry]/[[quality_guard]] 一种格式，且明文'其它 table 名、缺 config_digest 均 fail-closed'；唯一 loader 签名是 load_guard_registry(architecture_path)，而 demo host 没有 architecture.md。实测该模板使用 [registry]/[[guard]] 表名、八条目全部无 config_digest、cognitive-complexity 条目引用不存在于 guards.toml 的伪 section 'guard:cognitive-complexity'。后果：demo 宿主上 scenario 9（guard_parity_mismatch）没有已定义的 registry 真相源，check_guard_parity(architecture_path, repo) 无法在 demo 落地；§1.0.3 要求'demo 宿主经同一入口生成，禁止专用补丁'、AC-FR0258-03 evidence 要求'demo 生成的 pre-commit/CI 关联同一 registry-digest'——手写模板与这两条的关系（deploy 输入？独立真相源？由谁按什么规则校验？）无定义，Devon/Shield 必须临场选择实现路径。请修复（任选其一并补齐合同）：(a) 在 interfaces.md 为 demo registry 定义正式合同：schema、校验规则、部署落点、消费 loader、与 deploy_guard_configs 及 AC-FR0258-03'同一 registry-digest'语义的精确关系，并修正伪 section 与缺 digest 字段；(b) 改为 demo 侧 guard 配置全部由 canonical registry 经 deploy_guard_configs 生成，从本宣言删除该手写模板文件，demo 与 tracks 的阈值/required-check 差异（如 file-length 500 vs 1200、required 三项 vs 六项）在设计中显式文档化。
>> **Archer:** 按方案 (a) 修复：删除无合同的 guards.toml，新增 wheel asset tracks/assets/demo_host/architecture.md（正式 [quality_registry]/[[quality_guard]] 八类 schema、全量真实 config_digest）与 flake8.ini，并冻结部署落点 .tracks/projects/v0.1/architecture.md。ARCH §1.0.3/§3.1/§4.2.1 与 IF §1e/§1j/§1k 现明确同一 load_guard_registry→validate→deploy_guard_configs→check_parity 链、GuardDeployment、hook/CI digest 引用和 demo scenario 9。same registry-digest 被精确定义为单宿主内 deployment/Runtime/hook/CI 四向一致；tracks/demo 因 scope、file-length 1200/500、required checks 六/三而预期 digest 不同。scaffold 已替换，demo 八项/配置 bytes 摘要已程序核对，三份 trac validate 均 valid。
>> **Archer:** Runtime 对上一轮删除 tracks/assets/demo_host/guards.toml 判 undeclared_scaffold 并已回滚；本轮遵循事实，不重建、不删除、不修改、也不在 Scaffold 宣言列该 inherited legacy 文件。为保持单一真相，pyproject package-data 已从 broad glob 改成正式 demo assets 的显式 allowlist，因此 legacy guards.toml 不进入 wheel/fresh repo，Runtime loader/deployer 永不读取；ARCH §4.2.1/§4.5、IF §1k/validate 与 test-plan 均改为验证 allowlist、wheel、fresh repo 无该文件。正式 demo architecture/flake8 scaffold 与同 loader→validate→deploy→parity 链保持。所有当前写盘路径均在 Scaffold 宣言或 assignment target 中；三文档 validate valid。
>> **Prism:** 复核通过：demo registry 合同已闭合——(1) 宣言改为 19 项、demo architecture.md 与 flake8.ini 实际存在，flake8.ini 真 [flake8] section 修复伪 section；(2) demo registry host=demo-pytest、八类各一、八条 config_digest 实测全部匹配真实 bytes（pyproject=0e05b693…×6、flake8.ini=3c05476e…、tracks-project.toml=103a0041…）；(3) §1.0.3/§3.1/§4.2.1+IF §1e/§1j/§1k 定义了 load→validate→deploy→parity 链、GuardDeployment 与四向 digest 关联，same registry-digest 精确为单宿主语义、tracks≠demo 差异冻结于各自 registry；(4) guards.toml 按 Runtime rollback 事实处置为 inherited legacy：不在宣言、package-data 显式 allowlist 排除、validate #6 与 test-plan §6.5/§10.5 验证 wheel/fresh repo 无该文件，实测 b06a3ca..088f8cc 该文件零改动、内容未被消费。Devon/Shield 无需临场选择实现路径。本线程 resolved。

本节以外不创建 scaffold。上述 stub 的行为体/接线，以及 `project.py` adapter loader、EVENT/COMMAND 封闭集、validate/trace 扩展、registry deploy generator，均是**待实现 Devon foundation tasks**；本文不得把它们当作既有可执行能力。现有 `.githooks/pre-commit` 是 Phase 0 输入而非本次 scaffold：registry 已冻结硬化目标，Runtime 在违规清零后执行生成、更新、激活与 readback。`tests/ground_truth/` 不新增文件：test-plan §3 判定不适用。既有 `tests/ground_truth` 资产继承且不修改。新 stub 在 Devon 接入 composition root 前会短暂是 reach island；ISLAND_GATE_2 的 reach hard gate 保证出口前全部接线，不使用永久豁免。

## 3. 技术选型

### 3.1 Registry 使用 TOML

选择每个宿主 architecture.md §4.2 中一个语法冻结的 TOML fenced block：Acceptance 明确要求 registry 位于 architecture machine contracts，且 Runtime scaffold allowlist 不允许额外 `.tracks/projects/guards.toml`。loader 只解析调用方给定 architecture_path 的 §4.2 heading 下、下一个 heading 前、以 `[quality_registry]` 开头的唯一 `toml` block；零个或多个都 fail-closed。tracks 调用本文，动态 demo 调用部署后的 `.tracks/projects/v0.1/architecture.md`，因此每个 repo 都只有一个真相且共用 parser，不把 Tracks 自身 scope 强套到不同产品目录。

### 3.2 Python 静态检查映射

不新增 mypy。本宿主现有 annotations 尚非全量 strict typing，Phase 0 同时引入 mypy 会把产品切片变成类型迁移。静态检查类由同一 `ruff==0.16.0` 执行 F/B 语义规则，lint/format 类记录其余 rule families；同一命令、不同类别视图避免双重风格裁决。认知复杂度仍由 flake8 plugin 单点负责，pylint 只启用 R0801/C0302/R0915/R0914。未来引入 type checker 必须经 registry/design revision，不能静默追加。

### 3.3 Adapter 为显式新增长轴

选择 `tracks/adapters/` 而非继续放在 executor，是为了让 NFR-0141 的禁止区可机器扫描：kernel/executor/cli 完全无宿主语言语义，差异只进入 adapter。首版 adapter 复用现有 pytest/JUnit 行为，不新增第二语言。风险是迁移面较大；用同输入同输出 integration 回归与 unknown-adapter 反例收口。

### 3.4 Mutation 复用 git worktree

选择现有 `executor/worktree.py`，不引入容器依赖。每次实验独立 detached worktree、显式 diff digest、finally rollback；优点是离线、与宿主 git 事实一致，代价是文件系统成本。任一步不确定均 fail-closed，不用 mock git apply。

### 3.5 Demo 模板随 wheel

模板位于 package data 而非 `tests/` 私有 helper，使安装后的 `trac` 可从 wheel 自给语料并证明源码树外运行；repo 在 Runtime temp 中动态创建而非提交成“已配置宿主”。代价是 wheel 增少量文本资产，build contract 增 package-data 回归。

## 4. 交付与运行合同（machine contracts）

### 4.1 测试执行合同（`.tracks/projects/project.toml`）

既有三层合同不变：framework=`pytest`；paths=`tests/unit/`、`tests/integration/`、`tests/e2e/`；collect/run/run_selected 使用宿主 `.venv/bin/python -m pytest`；cwd=`.`；run/run_selected 的 `{result}` 与 run_selected 的 `{nodes}` 恰好一次；worker/JUnit flags 由命令内嵌，Runtime 不注入。

新增：

```toml
[adapter]
id = "reference-pytest"
protocol = "tracks-test-result"
version = 1
```

`project.py` loader 与 `trac validate` 必须验证该段；未知 id/version fail-closed。`framework="pytest"` 仅是 adapter-owned contract data，kernel/executor 不解释。integration/e2e collect/run/cwd 仍由 `.tracks/projects/project.toml` 唯一拥有。

**任务图 schema v2（`.tracks/projects/{ver}/tasks.json`，IF-IMPL-007）**：根标记 `"schema": 2`（真整数，否则 parse fail-closed）；每 task 声明 `unit_refs`（tests/unit/ RED 义务，可空）与 `acceptance_refs`（tests/integration/ 验收锚点，非空），遗留 `test_refs` 拒收，错层路径 parse 拒收。commit 期校验全体 `acceptance_refs` 并集覆盖 §8 全部 integration 行目标（缺口 fail-closed，PRISM_PLAN 领域）；GREEN 门按声明锚点解析（integration inventory 缺失 fail-closed + §8 cross-check）。§8 e2e 行目标是 ISLAND_GATE_2/FULL 兜底的终态覆盖锚点，不进入任何 task 的 acceptance_refs——混合行的验收归属只看 integration 项。tasks.md 是 Runtime 确定性投影，非校验来源。

### 4.2 Canonical quality guard registry

以下 TOML block 是 tracks 宿主的机器真相；字段名/顺序语义冻结，`trac validate` 与 Runtime 只读此 block。单文件 `config_digest` 是 `sha256(raw file bytes)`；多文件先按 repo-relative POSIX path 排序构造无空白 UTF-8 JSON object `{path: sha256(raw file bytes) lowercase hex}`，再对 JSON bytes 做 sha256。字段统一加 `sha256:` 前缀；`config_sections` 只用于 section 存在性/语义校验，不进入 digest。第 8 项与 demo 一样把 digest 绑定到已存在的宿主 test/deployment input `.tracks/projects/project.toml`；生成的 hook/CI 输出不作为自己的 digest 输入，避免 registry-digest→生成文件→config-digest 的循环。当前软 hook 仍因 `--exit-zero`、scope/command 不一致被语义 parity 阻断，而不是因未来输出 bytes 的占位 digest 阻断。

```toml
[quality_registry]
version = 1
host = "tracks"

[[quality_guard]]
id = "lint-format"
category = "lint_format"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff", "tool.ruff.lint"]
config_digest = "sha256:76e156ca4de243d3a176bf487096081dab181b3feaa09e44b5dc471337d711ba"
scope = ["tracks", "tests"]
threshold = "line-length=100; select=E,F,W,I,B,UP,SIM,C4; ignore=SIM108; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "static-semantic"
category = "static_analysis"
tool = "ruff"
tool_version = "0.16.0"
command = ".venv/bin/ruff check tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.ruff.lint"]
config_digest = "sha256:76e156ca4de243d3a176bf487096081dab181b3feaa09e44b5dc471337d711ba"
scope = ["tracks", "tests"]
threshold = "F and B semantic rule families; violations=0"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "cognitive-complexity"
category = "cognitive_complexity"
tool = "flake8+flake8-cognitive-complexity"
tool_version = "7.3.0+0.1.0"
command = ".venv/bin/flake8 tracks"
config_paths = [".flake8"]
config_sections = ["flake8"]
config_digest = "sha256:f899995c15557184f3a2d082469fcd7f15ae8f22928823249e6479a611c46382"
scope = ["tracks"]
threshold = "CCR001 max-cognitive-complexity=15; tests exempt"
timeout_seconds = 300
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "file-length"
category = "file_length"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=C0302 tracks tests"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.format"]
config_digest = "sha256:76e156ca4de243d3a176bf487096081dab181b3feaa09e44b5dc471337d711ba"
scope = ["tracks", "tests"]
threshold = "C0302 max-module-lines=1200"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "method-length-locals"
category = "method_length_locals"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0915,R0914 tracks"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.design"]
config_digest = "sha256:76e156ca4de243d3a176bf487096081dab181b3feaa09e44b5dc471337d711ba"
scope = ["tracks"]
threshold = "R0915 max-statements=50; R0914 max-locals=15; tests exempt"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "duplication"
category = "duplication"
tool = "pylint"
tool_version = "4.0.6"
command = ".venv/bin/pylint --disable=all --enable=R0801 tracks"
config_paths = ["pyproject.toml"]
config_sections = ["tool.pylint.similarities"]
config_digest = "sha256:76e156ca4de243d3a176bf487096081dab181b3feaa09e44b5dc471337d711ba"
scope = ["tracks", "tests"]
threshold = "R0801 min-similarity-lines=4 (product scope; test-file similarity is covered by review, not this release gate)"
timeout_seconds = 600
failure_policy = "fail_closed"
execution_points = ["runtime", "pre_commit", "ci"]
required_check = "lint"

[[quality_guard]]
id = "coverage-threshold"
category = "coverage_threshold"
tool = "coverage+pytest"
tool_version = "7.15.2+9.1.1"
command = ".venv/bin/coverage report --fail-under=95"
config_paths = ["pyproject.toml"]
config_sections = ["tool.coverage.run", "tool.coverage.report"]
config_digest = "sha256:76e156ca4de243d3a176bf487096081dab181b3feaa09e44b5dc471337d711ba"
scope = ["tracks"]
threshold = "line coverage >=95; by=collected; source omit=none"
timeout_seconds = 1800
failure_policy = "fail_closed"
execution_points = ["runtime", "ci"]
required_check = "coverage"

[[quality_guard]]
id = "hooks-runner-ci-required"
category = "hooks_runner_ci_required_checks"
tool = "git-hooks+github-actions"
tool_version = "git-env-fingerprinted+checkout@v4+setup-python@v5"
command = "sh .githooks/pre-commit"
config_paths = [".tracks/projects/project.toml"]
config_sections = ["unit", "integration", "e2e", "adapter"]
config_digest = "sha256:eb4be4e2d380b6bb29377f1f659fec15824af8a2fa72d04d8b48318379214144"
scope = ["local-commit", "pull-request", "main", "releases"]
threshold = "no --exit-zero; required=lint,coverage,test,deliverables,trace,reach; milestone=release-evidence"
timeout_seconds = 3600
failure_policy = "fail_closed"
execution_points = ["pre_commit", "ci"]
required_check = "lint,coverage,test,deliverables,trace,reach"
```

> **Prism [RESOLVED]:** PRISM-ARCH007-R3-01 [blocker|判据7 可实现性 + 判据8 合同真实性]：§4.2 第 8 条 hooks-runner-ci-required 的 config_digest=e25583b8… 无可推导来源且按本设计流程不可实现。证据：(1) 按冻结公式对现行 config_paths（.githooks/pre-commit + .github/workflows/ci.yml）计算=1b61a873…≠声明值；(2) 穷举全部 git tracked 文件代入 hook 分量及各公式变体（array序/prefix/默认分隔符/字节拼接/单文件）均不匹配；(3) 该值自称'目标 digest'（前言：当前软 hook 与第 8 项目标 digest 不同），而目标 hook 由 deploy_guard_configs 生成——该生成器是待实现 Devon foundation task，其输出字节在设计三件套中零规定，sha256 下 Devon 无法实现出生成物恰好命中冻结值。后果：§4.3 要求 hook 更新/激活/readback 后 guard.parity 通过才可 phase0.sealed，digest 漂移是法定 GuardMismatch 阻断项——按现值 Phase 0 永久 BLOCKED，v0.7-A 无法成立；除非无据改写已冻结 registry（设计未提供该机制）。修复（任选，须保持 §1k 公式、§4.2 前言与八条值一致）：(a) 参照 demo 第 8 条做法，把 config_paths 改绑已存在的可验证配置（如 .github/workflows/ci.yml 单文件或 .tracks/projects/project.toml），hook 硬化 parity 交由 check_parity 既有的命令/scope/threshold/--exit-zero 语义比对承载；(b) 在设计中字节级规定生成 hook 的模板并据此重算声明值；(c) 显式定义'部署时由 Runtime 重算并经已审计 design-revision 更新第 8 条 digest'的机制，并把现值标注为 pre-deployment 占位而非冻结真源。
>> **Archer:** 采用 (a) 修复。tracks 第 8 项现与 demo 同构：config_paths=[.tracks/projects/project.toml]、sections=unit/integration/e2e/adapter、config_digest=sha256:3ec206b42c0e8fb4695d0ec34f484591f7ca1c7d25b95e3120988c71c99f5c24；已现场按 raw bytes 重算一致。ARCH §4.2 前言/镜像/§4.2 后置规则、IF §1e/§1k/IF-GUARD-002 与 test-plan §6.5 已冻结 input/output 边界：config_digest 只验证部署前 project contract；生成 hook/CI raw bytes 只进入 GuardDeployment.artifact_digests 审计且不回填 registry，避免内嵌 registry digest 的自引用；check_parity 继续对 registry-digest header、command、scope、threshold、required checks、--exit-zero 做语义 fail-closed。现有软 hook 因语义 mismatch blocked，不再依赖不可推导占位值。程序核对 tracks 八项 config_digest 全部匹配真实文件，三文档 validate valid。
>> **Prism:** 复核通过：方案 (a) 已落地且实测一致——(1) 第 8 项 config_paths 改绑部署前已存在的 .tracks/projects/project.toml，实测 sha256=3ec206b4… 与声明值精确一致，sections unit/integration/e2e/adapter 实际在文件中；demo 第 8 项同构绑定 103a0041…（其 tracks-project.toml bytes）。(2) 程序复核 tracks 8+demo 8 共 16 条 config_digest 全部匹配真实文件字节，无可推导性缺口。(3) 自引用循环已消除并显式冻结：IF §1k/§1e 规定生成 hook/CI 的 artifact_digests 仅为审计值、不得回填 registry；§4.2 后置规则把第 8 项 digest 漂移限定于声明 input，生成物语义漂移由 check_parity 的 registry-digest header/command/scope/threshold/required-check/--exit-zero 比对阻断——hook 硬化门禁不再依赖未来输出 bytes 的占位摘要。(4) 现软 hook 因真实语义 mismatch（--exit-zero、scope/命令）blocked，与 §4.3 流程自洽，Phase 0 可经部署后语义 parity 达成 sealed。占位值 e25583b8… 已全文清除。本线程 resolved。

下表是审查镜像，不是第二数据源。

| # | 类别 | 工具（pinned） | 配置/范围 | 阈值 | 执行点与 required check |
|:--|:--|:--|:--|:--|:--|
| 1 | lint + format | ruff==0.16.0 | `pyproject.toml [tool.ruff*]`; `tracks tests` | line-length=100；E/F/W/I/B/UP/SIM/C4；0 violations | Runtime + pre-commit + CI `lint` |
| 2 | 静态检查 | ruff==0.16.0 | 同配置；F/B semantic subset；`tracks tests` | undefined/unused/likely-bug=0 | Runtime + pre-commit + CI `lint` |
| 3 | 认知复杂度 | flake8==7.3.0 + flake8-cognitive-complexity==0.1.0 | `.flake8`; `tracks` | CCR001 ≤15；tests 豁免 | Runtime + pre-commit + CI `lint` |
| 4 | 文件长度 | pylint==4.0.6 | `pyproject.toml [tool.pylint.format]`; `tracks tests` | C0302 max-module-lines=1200 | Runtime + pre-commit + CI `lint` |
| 5 | 方法长度/局部变量 | pylint==4.0.6 | `[tool.pylint.design]`; `tracks` | R0915≤50；R0914≤15；tests 豁免 | Runtime + pre-commit + CI `lint` |
| 6 | 重复度 | pylint==4.0.6 | `[tool.pylint.similarities]`; `tracks tests` | R0801 min-similarity-lines=5 | Runtime + pre-commit + CI `lint` |
| 7 | 覆盖率 | coverage==7.15.2 + pytest==9.1.1 | `[tool.coverage.*]`; source_pkgs=`tracks` | report ≥95%；by=collected；source omit=none | Runtime Phase0 + CI `coverage` |
| 8 | hooks runner + CI required checks | git hooks + GitHub Actions | digest input=`.tracks/projects/project.toml [unit/integration/e2e/adapter]`；generated/parity outputs=`.githooks/pre-commit`、`.github/workflows/ci.yml` | guards 1–7 hard；禁止 `--exit-zero`；required=`lint/coverage/test/deliverables/trace/reach` | local commit + merge；milestone `release-evidence` |

安装命令（只由 Runtime 执行副作用）：

```text
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
git config core.hooksPath .githooks
```

每项 timeout/failure policy 在 registry 中具体声明；timeout、missing config、digest 漂移均为失败。第 8 项的 digest 漂移只指其声明的 project contract input；生成 hook/CI 的任何 bytes 或语义漂移由 `GuardDeployment.artifact_digests` 审计并由 `check_parity` 的 registry-digest、command、scope、threshold、required-check、`--exit-zero` 比对阻断，不拿生成物 bytes 回填 registry。阈值/scope 改动必须修订 design+registry，经 Prism 评审；`phase0.guard_hardened.revised` 只统计该类已审变更。

`[layout.shield]` 精确集（layer 目录 + `_support/` + 精确文件）是 SHIELD_FIX 的测试域声明，不是产品代码授权：Shield 可修改 integration/e2e/e2e_live/assets/counterexamples/_support ，但永不获得 tests/ 根前缀；Devon 的 `red_test_dirs` 命中路径豁免 forbidden veto，其余路径 forbidden 先于 allow 判定，unit RED 资产仍由 layout 派生冻结。该 project contract raw bytes 属第 8 项 config input，任何 layout 改动均须同步本 registry digest，不得以未审工作树漂移绕过 guard。

#### 4.2.1 Demo registry 与部署闭合

- **正式 source/落点**：wheel 只携带 `tracks/assets/demo_host/architecture.md` 这一份 demo registry source；`create_demo_host` 在 `trac init` 后把它逐字节写到 fresh repo `.tracks/projects/v0.1/architecture.md`，把 `tracks-project.toml` 写到 `.tracks/projects/project.toml`，其余 `pyproject.toml`、`flake8.ini`、源码/测试写到 repo 根。仓库 inherited legacy `tracks/assets/demo_host/guards.toml` 不属于本次 Scaffold、不得修改，且被 `pyproject.toml [tool.setuptools.package-data]` 显式 allowlist 排除，因此 wheel/fresh repo 中不存在该文件；Runtime 不读取它。无第二 parser 或 pre-generated hook/CI。
- **消费链**：`load_guard_registry(repo/.tracks/projects/v0.1/architecture.md)` → `validate_guard_registry(registry, repo)` → `deploy_guard_configs(registry, repo)` → `.githooks/pre-commit` + `.github/workflows/ci.yml` → `check_parity`。任一步缺文件、digest/section 不符或 host≠`demo-pytest` 均使 equivalence=false；scenario `guard_parity_mismatch` 必须在已成功部署的副本上只改变一处后由同一 `check_parity` 阻断。
- **digest 关联**：deployer 产生的 `GuardDeployment.registry_digest`、hook 声明 `TRACKS_GUARD_REGISTRY=<digest>`、CI 顶层 `env.TRACKS_GUARD_REGISTRY=<digest>` 与随后 `guard.parity.registry` 必须四者相等；digest 是 canonical TOML data（排除 Markdown/frontmatter、保持 array 顺序、JSON sort_keys/separators）之 sha256。tracks 与 demo 的 digest 不比较相等。
- **有意差异**：demo scope 是 `demo_calc.py/tests` 而非 `tracks/tests`；file-length=500 而非 1200；timeout=120/600/900 而非 300/600/1800/3600；CI required checks 仅 `lint,coverage,test`，不声称 demo 具有 Tracks 产品专属 `deliverables,trace,reach`。其余 pinned versions、line-length=100、CCR001=15、method=50/locals=15、similarity=5、coverage=95、fail_closed 与禁 `--exit-zero` 相同。所有差异都位于 demo architecture registry，不由 deployer 硬编码。

### 4.3 CI / pre-commit / release

- Stable required checks 继承：`lint`、`coverage`、`test`、`deliverables`、`trace`、`reach`；job 名不得变。`release-evidence` 仍是 tag milestone hard gate，needs routine 全部。
- Phase 0 先以现有软 hook 作为 mismatch 证据，消除真实违规后 Runtime 用 registry 生成并更新目标 hook：ruff `tracks tests`、flake8 `tracks`、pylint R0801/C0302 `tracks tests`、R0915/R0914 `tracks`；全部 hard。更新前 `guard.parity` 必须 blocked，更新/激活/readback 后才可 sealed。
- `guard.parity` 是 Runtime gate，不新增 CI job；CI 的真实执行结论由 registry 的 required check 名引用。仅有文件声明无成功/失败输出不构成证据。
- live-opencode 与 release-evidence 的凭据探针、`LIVE_SKIPPED`、weekly/milestone 语义不变；v0.7-A 无新外部依赖或 live AC。
- release version/build/publish 语义不改；v0.7-B 的 CI SHA 回读与发布自动化明确不实现。

### 4.4 Integration/e2e 基础设施

- Shield integration 资产落 `tests/integration/test_{phase0,guard,authenticity,mutation,adapter,trace,demo}_*.py`；e2e 仅 `test_v07_journey.py` 与 `test_dualhost_acceptance.py` 两条 happy path。
- fault 场景通过 public Runtime gates 与隔离 worktree 注入，不 mock kernel/executor；demo 通过 wheel/CLI，不 import tests helper。
- counterexample patch 归 `tests/counterexamples/v0.7/`，每 patch 单 AC/IF、不得改 tests、必须 `git apply --check` 且由 Runtime 真跑 target/control。
- deterministic suite 默认离线。没有新增生产网络依赖；现有 live channel 继续独立。

### 4.5 Build / artifact

build backend 与 wheel 名不变。`pyproject.toml [tool.setuptools.package-data]` 显式列出 `architecture.md`、`demo_calc.py`、`flake8.ini`、`pyproject.toml`、`tracks-project.toml` 与三层 tests；不使用 `assets/demo_host/*` broad glob，故 inherited legacy `guards.toml` 不进入 wheel。CI build/package-data 回归必须证明 wheel 安装后可定位声明资产、无 `guards.toml`、source import path 不在仓库。v0.7-A 不执行正式制品发布。

### 4.6 发布恢复

所有新事件 append-only。Phase 0 seal、mutation experiment、demo scenario 以 event+blob 为 WAL；reconcile 以 seal/manifest/experiment/scenario digest 去重。中断后无完整持久化结果即重跑，完整且 identity 相同才复用；dirty worktree/rollback 未 clean 一律停车。M-VERIFY、发布回滚不在本版。

## 5. 有意识简化与风险

### 5.1 有意识简化

- 只交付一个 reference adapter；语言中立由 opaque protocol、禁止区静态检查与 unknown-adapter fail-closed 证明，不用第二语言扩大范围。
- Python 静态检查复用 ruff F/B，不在 Phase 0 引入 mypy 全仓迁移；这是显式设计决定，不等于空缺守卫。
- 9 场景由 Runtime 在隔离副本合成，不提交九套宿主 repo；每场景仍经过真实 gate 并记录实际结果。
- Ground Truth 不适用：需求是状态/证据/身份/路由正确性，预期来自事件 schema、git diff、fixture node 集本身；不独立重写被测协议算法。
- Phase 0 触发位于 M-DESIGN EXIT→M-TEST entry，因为本 run 的需求阶段已完成，而冻结 baseline 的首个消费者是 M-TEST；此前阶段不消费真实性证据。

### 5.2 风险

- **Phase 0 真实绿基线不可恢复**：三缺口节点不存在、coverage 无法达到或硬守卫无法修复时按 spec 进入 BLOCKED，不能由设计放宽。
- **adapter 迁移遗漏语言 token**：strict scanner 可能先使现有树红；这是预期 legal Red，Devon 必须把解析/exit-code 语义移入 adapter，不可 allowlist executor。
- **registry parser 对 CI shell 归一化过宽**：只允许 argv0 路径等价与组合命令拆项；不能忽略 scope、rule code、threshold 或 `--exit-zero`。
- **counterexample Goodhart**：Prism 审语义/最小性，Runtime 审可应用性与 kill/control；两者缺一阻断。
- **demo 假等价**：若使用源码 editable install、tests helper、预建 repo 或跳过 hooks/CI/adapter 任一项，`demo.equivalence` 必须 false，验收失败。
- **scaffold reach 窗口**：九个新模块在 M-IMPL 接线前可能是 island；不得永久豁免，task graph 必须 composition-root-first，ISLAND_GATE_2 reach 兜底。
